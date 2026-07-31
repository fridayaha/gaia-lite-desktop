"""GeoTimeStore — 时空层 (graph-reasoning-design.md §5).

PostGIS（静态空间属性）+ TimescaleDB（动态 GTS 时空序列）合并封装，
同一 PG 实例（C2 一体镜像 ngosang/timescaledb-postgis）。

设计要点：
- **静态/动态二分**（C2）：GEOPOINT/GEOSHAPE → PostGIS 空间表（GiST）；
  GEOTEMPORAL_SERIES/TIME_SERIES → TimescaleDB 超表。
- **精简双存**：PostGIS 只存 rid + 主键 + 几何 + 剪枝字段，不存全量（全量在 Doris）。
- **流式独立链路**（C3）：时序数据走 SeaTunnel Kafka→TimescaleDB，不经 Iceberg/Action。
- **空间查询毫秒级**：GiST 索引 + ID IN 过滤（候选 rid 集先算，再空间过滤）。
- **不碰 Doris**（C5/C12）：推理线空间/时序走 PG，水合全量属性借 ObjectQueryService。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from ontology.config.database import engine as _default_engine
from ontology.core.naming import geo_table, timeseries_hypertable
from ontology.core.schemas.geotime import AggSpec, SpatialFilter

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


class GeoTimeStore:
    """时空层。PostGIS（静态空间）+ TimescaleDB（动态序列）同 PG 实例合并封装。

    复用现有 PG async engine（与 metadata 同库，PostGIS/TimescaleDB 已激活）。
    用 SQLAlchemy text() 执行原生 SQL —— PostGIS/TimescaleDB 函数是 PG 扩展，
    ORM 不直接支持，原生 SQL 是最稳妥通道（R1：不依赖 Ibis geospatial API）。

    ``engine`` 可注入以便测试用独立 engine 避免 event-loop 跨越问题；生产
    用模块级默认 engine（ontology.config.database.engine）。
    """

    def __init__(self, engine: Any | None = None) -> None:
        self._engine = engine if engine is not None else _default_engine

    # ── Schema（define_object_type 触发） ──

    async def create_geo_table(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        geo_type: str,  # "GEOPOINT" | "GEOSHAPE"
        indexed_fields: list[str] | None = None,
    ) -> str:
        """创建 PostGIS 静态空间表（GiST 索引）。

        精简双存：rid + 主键 + 几何 + 剪枝字段。命名走 core/naming.geo_table。
        """
        table = geo_table(ontology_api_name, object_type_api_name)
        indexed_fields = indexed_fields or []
        # indexed 剪枝字段动态加列（VARCHAR(255)，类型容忍）。
        index_cols = "".join(f", {f} VARCHAR(255)" for f in indexed_fields)
        geom_col = "location" if geo_type == "GEOPOINT" else "geometry"
        geom_type = "GEOGRAPHY(POINT, 4326)" if geo_type == "GEOPOINT" else "GEOGRAPHY(POLYGON, 4326)"

        ddl = f"""CREATE TABLE IF NOT EXISTS {table} (
            rid VARCHAR(128) PRIMARY KEY,
            api_name VARCHAR(255) NOT NULL,
            pk_value VARCHAR(255) NOT NULL,
            {geom_col} {geom_type}{index_cols},
            update_time TIMESTAMPTZ DEFAULT NOW(),
            data_version BIGINT DEFAULT 0
        )"""
        async with self._engine.begin() as conn:
            await conn.execute(text(ddl))
            # GiST 空间索引（毫秒级空间过滤基础）。
            await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {table}_gist ON {table} USING GIST ({geom_col})"))
        _log.info("Created PostGIS table: %s (geom=%s)", table, geom_col)
        return table

    async def create_timeseries_hypertable(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        series_property_api_name: str,
        has_position: bool,  # GEOTEMPORAL_SERIES=True, TIME_SERIES=False
        metric_fields: list[str] | None = None,
    ) -> str:
        """创建 TimescaleDB 超表（动态 GTS 时空序列）。

        series_id + timestamp 强制列；location 仅 GEOTEMPORAL_SERIES 有。
        metric_fields 业务指标列（DOUBLE PRECISION）。chunk_time_interval=1day。
        """
        table = timeseries_hypertable(ontology_api_name, object_type_api_name, series_property_api_name)
        metric_fields = metric_fields or []
        loc_col = "location GEOGRAPHY(POINT, 4326), " if has_position else ""
        metric_cols = "".join(f", {f} DOUBLE PRECISION" for f in metric_fields)

        ddl = f"""CREATE TABLE IF NOT EXISTS {table} (
            series_id VARCHAR(64) NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            {loc_col}status VARCHAR(32),
            payload JSONB{metric_cols}
        )"""
        async with self._engine.begin() as conn:
            await conn.execute(text(ddl))
            # 转为超表（按 timestamp 分片，1 day/chunk）。
            await conn.execute(
                text(
                    f"SELECT create_hypertable('{table}', 'timestamp', "
                    f"chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
                )
            )
            if has_position:
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS {table}_gist ON {table} USING GIST (location)"))
            # series_id + timestamp 复合索引（按序列查最新值）。
            await conn.execute(
                text(f"CREATE INDEX IF NOT EXISTS {table}_series_time ON {table} (series_id, timestamp DESC)")
            )
        _log.info("Created TimescaleDB hypertable: %s (has_position=%s)", table, has_position)
        return table

    # ── 静态空间写入（GeoTimeProjector 调用） ──

    async def upsert_geo(
        self,
        table: str,
        rid: str,
        api_name: str,
        pk_value: str,
        geometry_wkt: str,  # WKT 格式几何
        props: dict[str, Any] | None = None,
        geom_column: str = "location",
    ) -> None:
        """Upsert 静态空间记录（ON CONFLICT rid 更新）。

        geometry_wkt 是 WKT 字符串，用 ST_GeogFromText 转 geography。
        props 是剪枝字段（indexed 属性），动态加到 SET 子句。
        """
        props = props or {}
        # 构建列与占位符：rid, api_name, pk_value, geom + props。
        cols = ["rid", "api_name", "pk_value", geom_column]
        placeholders = [":rid", ":api_name", ":pk_value", "ST_GeogFromText(:geom)"]
        params: dict[str, Any] = {
            "rid": rid,
            "api_name": api_name,
            "pk_value": pk_value,
            "geom": geometry_wkt,
        }
        for k, v in props.items():
            cols.append(k)
            placeholders.append(f":{k}")
            params[k] = v
        # ON CONFLICT (rid) DO UPDATE：更新所有非主键列。
        update_cols = [c for c in cols if c != "rid"]
        set_clause = ", ".join(
            f"{c} = ST_GeogFromText(:geom)" if c == geom_column else f"{c} = EXCLUDED.{c}" for c in update_cols
        )
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(placeholders)}) "
            f"ON CONFLICT (rid) DO UPDATE SET {set_clause}, data_version = {table}.data_version + 1"
        )
        async with self._engine.begin() as conn:
            await conn.execute(text(sql), params)

    # ── 动态序列写入（SeaTunnel sink / 流式链路） ──

    async def append_series(self, table: str, rows: list[dict[str, Any]]) -> int:
        """批量写入时序行到超表（流式 sink 用）。

        rows 每行含 series_id + timestamp + 可选 location([lon,lat]) + metrics。
        location 列用 ST_GeogFromText 转换；其余列直接绑定。
        返回写入行数。
        """
        if not rows:
            return 0
        # 预处理：location [lon,lat] → location_wkt（参数名），但插入列名仍是 location。
        cleaned = []
        for r in rows:
            row = dict(r)
            if "location" in row and row["location"] is not None:
                loc = row["location"]
                if isinstance(loc, (list, tuple)) and len(loc) == 2:
                    row["location_wkt"] = f"POINT({loc[0]} {loc[1]})"
                    del row["location"]
            cleaned.append(row)
        # 构建列名与占位符。location_wkt 占位符用 ST_GeogFromText，列名映射为 location。
        cols: list[str] = []
        placeholders: list[str] = []
        for k in cleaned[0].keys():
            if k == "location_wkt":
                cols.append("location")
                placeholders.append("ST_GeogFromText(:location_wkt)")
            else:
                cols.append(k)
                placeholders.append(f":{k}")
        col_str = ", ".join(cols)
        val_str = ", ".join(placeholders)
        sql = f"INSERT INTO {table} ({col_str}) VALUES ({val_str})"
        async with self._engine.begin() as conn:
            await conn.execute(text(sql), cleaned)
        return len(cleaned)

    # ── 空间查询（DataFrameQueryService 的空间 filter 步骤） ──

    async def spatial_filter(
        self,
        table: str,
        candidate_rids: list[str],
        spatial: SpatialFilter,
        geom_column: str = "location",
    ) -> list[str]:
        """空间过滤：从候选 rid 集中返回命中空间条件的 rid。

        GiST 索引 + rid IN 过滤，毫秒级。candidate_rids 分批避免超大 IN（R2）。
        """
        if not candidate_rids:
            return []

        # 构建空间谓词 SQL 片段。
        if spatial.op == "withinDistance" and spatial.center and spatial.max_distance is not None:
            geom_pred = f"ST_DWithin({geom_column}, ST_MakePoint(:lon, :lat)::geography, :dist)"
            params: dict[str, Any] = {
                "lon": spatial.center[0],
                "lat": spatial.center[1],
                "dist": spatial.max_distance,
            }
        elif spatial.op == "withinPolygon" and spatial.coords:
            # 构建多边形 WKT。
            pts = ", ".join(f"{c[0]} {c[1]}" for c in spatial.coords)
            polygon_wkt = f"POLYGON(({pts}, {spatial.coords[0][0]} {spatial.coords[0][1]}))"
            # geography 类型不支持 ST_Within，用 ST_Covers（geography 兼容）。
            geom_pred = f"ST_Covers(ST_GeogFromText(:wkt), {geom_column})"
            params = {"wkt": polygon_wkt}
        elif spatial.op == "withinBoundingBox" and spatial.bbox:
            minlon, minlat = spatial.bbox[0]
            maxlon, maxlat = spatial.bbox[1]
            bbox_wkt = (
                f"POLYGON(({minlon} {minlat}, {maxlon} {minlat}, "
                f"{maxlon} {maxlat}, {minlon} {maxlat}, {minlon} {minlat}))"
            )
            geom_pred = f"ST_Covers(ST_GeogFromText(:wkt), {geom_column})"
            params = {"wkt": bbox_wkt}
        else:
            return list(candidate_rids)  # 无有效空间条件，原样返回

        results: list[str] = []
        batch_size = 5000  # R2: PG IN 子句分批。
        async with self._engine.connect() as conn:
            for i in range(0, len(candidate_rids), batch_size):
                batch = candidate_rids[i : i + batch_size]
                sql = f"SELECT rid FROM {table} WHERE rid = ANY(:rids) AND {geom_pred}"
                params["rids"] = batch
                res = await conn.execute(text(sql), params)
                for row in res:
                    results.append(str(row[0]))
        return results

    # ── 时序查询（DataFrameQueryService 的时序 filter 步骤） ──

    async def series_query(
        self,
        table: str,
        series_ids: list[str],
        time_range: tuple[Any, Any] | None = None,
        spatial: SpatialFilter | None = None,
        aggregations: list[AggSpec] | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """时序查询：按 series_id + 时间窗口 + 可选空间 + 可选聚合。"""
        if not series_ids:
            return []
        params: dict[str, Any] = {"series_ids": series_ids, "limit": limit}
        where = ["series_id = ANY(:series_ids)"]
        if time_range is not None:
            where.append("timestamp >= :ts_start")
            where.append("timestamp <= :ts_end")
            params["ts_start"] = time_range[0]
            params["ts_end"] = time_range[1]

        select_cols = "series_id, timestamp"
        group_by = ""
        if aggregations:
            agg_parts = []
            for a in aggregations:
                alias = a.alias or f"{a.func}_{a.field}"
                agg_parts.append(f"{a.func.upper()}({a.field}) AS {alias}")
            select_cols = "series_id" + (", " + ", ".join(agg_parts) if agg_parts else "")
            group_by = " GROUP BY series_id"
            # 聚合时去掉 timestamp（与 GROUP BY 不兼容）。
            select_cols = select_cols.replace(", timestamp", "") if "timestamp" in select_cols else select_cols
            if not aggregations:
                select_cols = "series_id, timestamp"

        sql = (
            f"SELECT {select_cols} FROM {table} "
            f"WHERE {' AND '.join(where)}{group_by} "
            f"ORDER BY timestamp DESC LIMIT :limit"
        )

        async with self._engine.connect() as conn:
            res = await conn.execute(text(sql), params)
            return [dict(row._mapping) for row in res]

    async def table_exists(self, table_name: str) -> bool:
        """检查表是否存在（rebuild/对账用）。"""
        async with self._engine.connect() as conn:
            res = await conn.execute(
                text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :t)"),
                {"t": table_name},
            )
            return bool(res.scalar())

    async def drop_table(self, table_name: str) -> None:
        """删除表（deprovision 用）。"""
        async with self._engine.begin() as conn:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))
