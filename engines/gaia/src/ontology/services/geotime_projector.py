"""GeoTimeProjector — object_state → PostGIS 投影 (graph-reasoning-design.md §6.2).

投影器读 ObjectType 元数据决定写什么（C4 本体元数据驱动分发）：
- 仅 GEOPOINT/GEOSHAPE 属性的对象投影到 PostGIS 空间表
- 时序属性（GEOTEMPORAL_SERIES/TIME_SERIES）不经此（走流式独立链路 C3）
- PostGIS 只存 rid + 主键 + 几何 + 剪枝字段（精简双存，全量在 Doris）
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ontology.core.naming import geo_table
from ontology.core.property_mapping import backing_to_api
from ontology.core.schemas.ontology import SPATIAL_DATA_TYPES
from ontology.layers.geotime.geotime_store import GeoTimeStore

if TYPE_CHECKING:
    from ontology.core.schemas.ontology import PropertyDef
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)


class GeoTimeProjector:
    """object_state 变更 → PostGIS 投影（仅含空间属性对象）。

    时序不经此（走流式链路 C3）。
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        geotime_store: GeoTimeStore,
    ) -> None:
        self._metadata = metadata
        self._geotime = geotime_store

    async def project_object(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        object_state: dict[str, Any],
    ) -> None:
        """投影单个对象到 PostGIS（仅空间属性对象）。

        非 GEOPOINT/GEOSHAPE 属性的对象不投影（跳过）。
        """
        ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        spatial_props = [p for p in ot.properties if p.data_type in SPATIAL_DATA_TYPES]
        if not spatial_props:
            return  # 非空间对象不投影

        # 第一个空间属性作为主几何（MVP；多几何留二期）。
        geo_prop = spatial_props[0]
        table = geo_table(ontology_api_name, object_type_api_name)

        rid = str(object_state["rid"])
        # object_state 存 backing_column key（core.property_mapping）；规范化为
        # api_name 以匹配 geo_prop.api_name / ot.primary_key（均按 api_name 读取）。
        # 传 api_name 的调用方幂等（backing_to_api 只重命名已知 backing_column 键）。
        props_dict = backing_to_api(ot, object_state.get("properties", {}) or {})
        if not props_dict:
            props_dict = {k: v for k, v in object_state.items() if k != "properties"} or object_state
        geometry_value = props_dict.get(geo_prop.api_name)
        if not geometry_value:
            return  # 无几何值，跳过

        wkt = self._to_wkt(geometry_value, geo_prop)
        if wkt is None:
            _log.warning(
                "Cannot project geometry for %s.%s (rid=%s): unsupported value %r",
                ontology_api_name,
                object_type_api_name,
                rid,
                geometry_value,
            )
            return

        # 剪枝字段：indexed 属性（非空间）。
        indexed_fields = [p.api_name for p in ot.properties if p.indexed and p.data_type not in SPATIAL_DATA_TYPES]
        prune_props = {f: props_dict.get(f) for f in indexed_fields if props_dict.get(f) is not None}

        pk_value = str(props_dict.get(ot.primary_key, rid))
        geom_column = "location" if geo_prop.data_type == "GEOPOINT" else "geometry"
        await self._geotime.upsert_geo(table, rid, object_type_api_name, pk_value, wkt, prune_props, geom_column)

    @staticmethod
    def _to_wkt(value: Any, prop: PropertyDef) -> str | None:
        """把属性值转为 WKT。支持 [lon, lat] / GeoJSON / {{"lon":...,"lat":...}} / WKT 字符串。"""
        if isinstance(value, str):
            # 可能是 WKT 或 JSON 字符串。先尝试解析 JSON。
            if value.startswith("{"):
                try:
                    loaded = json.loads(value)
                    return GeoTimeProjector._dict_to_wkt(loaded)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
            # WKT 字符串原样返回。
            return value
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return f"POINT({value[0]} {value[1]})"
        if isinstance(value, dict):
            return GeoTimeProjector._dict_to_wkt(value)
        return None

    @staticmethod
    def _dict_to_wkt(data: dict[str, Any]) -> str | None:
        """从 dict 转 WKT。支持 GeoJSON + {{"lon": ..., "lat": ...}} 格式。"""
        # {{"lon": x, "lat": y}} 格式（常见于 SeaTunnel 写入的数据）
        if "lon" in data and "lat" in data:
            return f"POINT({data['lon']} {data['lat']})"
        # {{"longitude": x, "latitude": y}} 变体
        if "longitude" in data and "latitude" in data:
            return f"POINT({data['longitude']} {data['latitude']})"
        # GeoJSON Point
        if data.get("type") == "Point" and "coordinates" in data:
            coords = data["coordinates"]
            return f"POINT({coords[0]} {coords[1]})"
        # GeoJSON Polygon
        if data.get("type") == "Polygon" and "coordinates" in data:
            rings = data["coordinates"]
            if rings:
                ring = rings[0]
                pts = ", ".join(f"{c[0]} {c[1]}" for c in ring)
                # RFC 7946 §3.1.6 要求环已闭合（首==末）；仅在未闭合时补首点，
                # 否则会对已闭合环追加重复点，生成退化零长末段被 PostGIS 拒收。
                if ring[-1] != ring[0]:
                    pts += f", {ring[0][0]} {ring[0][1]}"
                return f"POLYGON(({pts}))"
        return None

    async def delete_object(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        rid: str,
    ) -> None:
        """删除空间记录（对象删除时触发）。"""
        table = geo_table(ontology_api_name, object_type_api_name)
        from sqlalchemy import text

        from ontology.config.database import engine

        async with engine.begin() as conn:
            await conn.execute(text(f"DELETE FROM {table} WHERE rid = :rid"), {"rid": rid})
