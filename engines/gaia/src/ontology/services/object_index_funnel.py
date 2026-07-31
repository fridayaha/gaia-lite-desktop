"""ObjectIndexFunnel — 统一索引编排漏斗：外部数据 → Doris/Neo4j/PostGIS。

背景:
  Action 写入路径的图/时空投影已在 OutboxExecutor（节点）和 ActionService
  （边）接线——outbox payload 自带 properties，直接调 projector。

  外部数据接入路径（SeaTunnel → Iceberg）不经过 Action，不产生 outbox
  INDEX event，因此索引/投影缺失。本 Funnel 弥补此缺口：从 Iceberg 读取
  外部接入的数据，统一完成 ① rid 分配/复用 ② Doris idx 写入 ③ 按能力
  门控投影到 Neo4j/PostGIS，是外部数据进入在线读主源 + 图/时空索引的
  唯一编排入口。

触发时机:
  - SeaTunnel 同步完成后（外部数据刚入库 Iceberg）
  - 用户手动触发管理 API（POST /admin/project/rebuild）
  - provision 后首轮填充（define_object_type 创建新 OT 后）

Capabilities 四道门（ADR-015):
  Gate 1: storage_type == MANAGED
  Gate 2: data_type 匹配（indexed→Neo4j / GEOPOINT/GEOSHAPE→PostGIS）
  Gate 3: 关系存在（仅图投影，no links = no graph value）
  Gate 4: capabilities.graph_indexing_enabled / geotime_indexing_enabled

  四道门在 projector 内部已部分检查（GeoTimeProjector 跳过非空间对象、
  GraphProjector 只投影 indexed 属性），本 Funnel 在调用前补检查 Gate 1+4，
  避免无谓的 Iceberg 读取。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from ontology.core.naming import _to_snake
from ontology.core.rid import generate_object_rid, generate_virtual_rid

if TYPE_CHECKING:
    from ontology.core.models.ontology import ObjectTypeModel
    from ontology.core.schemas.ontology import LinkTypeDef, ObjectType
    from ontology.layers.dataset.iceberg_store import IcebergStore
    from ontology.layers.engine.base import QueryEngine
    from ontology.layers.index.doris_index_store import DorisIndexStore
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
    from ontology.services.geotime_projector import GeoTimeProjector
    from ontology.services.graph_projector import GraphProjector
    from ontology.services.object_query_service import ObjectQueryService

_log = logging.getLogger(__name__)

# Iceberg scan 分批大小（对齐 IndexSyncService.sync_now 的 limit）。
_SCAN_BATCH_SIZE = 10_000
# Admin 端点 limit 上限（防止恶意调用拉爆内存）。
_MAX_LIMIT = 100_000
# VIRTUAL 联邦投影游标分页批大小（ADR-021 §2.5，难点 6 决策）。
_VIRTUAL_BATCH_SIZE = 1000


class ObjectIndexFunnel:
    """从 Iceberg 读取外部数据，统一编排 rid 分配 + Doris idx 写入 + 图/时空投影。

    依赖注入:
      - metadata:  读 ObjectType 元数据（capabilities + properties）
      - dataset:   IcebergStore，scan_latest 读全量数据
      - index_store: DorisIndexStore，rid 复用/分配 + Doris idx 写入
        (T1.4 handoff-rid-funnel-closure.md：外部接入路径写入 Doris idx
        成为唯一数据同步路径, SeaTunnel backfill 废弃)
      - graph_projector:  GraphProjector（可能为 None，Neo4j 未启动时）
      - geotime_projector: GeoTimeProjector（可能为 None）
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        dataset: IcebergStore,
        graph_projector: GraphProjector | None = None,
        geotime_projector: GeoTimeProjector | None = None,
        index_store: DorisIndexStore | None = None,
        *,
        engine: QueryEngine | None = None,
        object_query: ObjectQueryService | None = None,
    ) -> None:
        self._metadata = metadata
        self._dataset = dataset
        self._graph_projector = graph_projector
        self._geotime_projector = geotime_projector
        self._index_store = index_store
        # ADR-021 VIRTUAL 联邦投影：Trino 是 VIRTUAL 数据源，object_query 提供
        # _virtual_table_ref（Trino table ref 解析）。两者可选，None 时 VIRTUAL
        # 投影不可用（best-effort，不阻塞 MANAGED 路径）。
        self._engine = engine
        self._object_query = object_query

    # ── Public API ──

    async def project_for_object_type(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        *,
        dataset_api_name: str | None = None,
        limit: int = _SCAN_BATCH_SIZE,
    ) -> dict[str, int]:
        """对单个 ObjectType 执行图+时空投影。

        从 Iceberg 读取该 ObjectType 的全量数据（latest snapshot），
        按 capabilities 门控逐条投影到 Neo4j 和/或 PostGIS。

        Args:
            ontology_api_name: 本体 api_name。
            object_type_api_name: 目标 ObjectType。
            dataset_api_name: Iceberg 表名（= dataset api_name，snake_case）。
                None 时用 object_type_api_name 的 snake_case 兜底。
            limit: Iceberg 最大扫描行数（默认 10,000，上限 100,000）。

        Returns:
            {"graph": n, "geotime": m}: 实际投影的节点数。0 表示未启用或空表。
        """
        result: dict[str, int] = {"graph": 0, "geotime": 0}
        limit = max(1, min(limit, _MAX_LIMIT))

        # 查 ObjectType 元数据（capabilities + properties + storage_type）。
        try:
            ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        except Exception as exc:
            _log.warning(
                "project_for_object_type: failed to load OT %s/%s: %s",
                ontology_api_name,
                object_type_api_name,
                exc,
            )
            return result

        # Gate 1: VIRTUAL 类型无数据落地。
        if ot.storage_type == "VIRTUAL":
            _log.debug("project_for_object_type: %s/%s is VIRTUAL, skip", ontology_api_name, object_type_api_name)
            return result

        caps = ot.capabilities

        # Gate 4 快速短路：避免无谓的 Iceberg 读取。
        want_graph = caps.graph_indexing_enabled and self._graph_projector is not None
        want_geotime = caps.geotime_indexing_enabled and self._geotime_projector is not None
        if not want_graph and not want_geotime:
            _log.debug(
                "project_for_object_type: %s/%s has no projection enabled, skip",
                ontology_api_name,
                object_type_api_name,
            )
            return result

        # 从 Iceberg 读取全量数据。
        iceberg_table = dataset_api_name or _to_snake(object_type_api_name)
        try:
            rows = await self._dataset.scan_latest(
                f"ontology.{iceberg_table}",
                columns=["*"],
                limit=limit,
            )
        except Exception as exc:
            _log.warning("project_for_object_type: Iceberg scan failed for %s: %s", iceberg_table, exc)
            return result

        if not rows:
            _log.info("project_for_object_type: Iceberg table %s is empty", iceberg_table)
            return result

        _log.info(
            "project_for_object_type: %s/%s — %d rows, graph=%s geotime=%s",
            ontology_api_name,
            object_type_api_name,
            len(rows),
            want_graph,
            want_geotime,
        )

        # 确定业务主键列名（Iceberg 中不一定是 "object_id"，而是 ObjectType 的 primary_key）。
        pk_api = ot.primary_key if ot.primary_key else "object_id"
        # 解析 primary_key 的 backing_column（Doris idx 表的物理 PK 列名）。
        # 与 OutboxExecutor._resolve_pk_for_delete 同逻辑：找到 PropertyDef
        # 的 backing_mapping.backing_column，没有则退到 api_name 本身。
        pk_column = pk_api
        for prop in ot.properties:
            if prop.api_name == pk_api and prop.backing_mapping:
                pk_column = prop.backing_mapping.backing_column or pk_api
                break

        # 提取所有行的业务 PK 值（跳过无 PK 的行）。
        typed_rows: list[tuple[Any, dict[str, Any]]] = []
        for row in rows:
            obj_id = self._extract_pk(row, pk_column)
            if not obj_id:
                _log.debug(
                    "project_for_object_type: cannot find PK in row for %s/%s, skip",
                    ontology_api_name,
                    object_type_api_name,
                )
                continue
            typed_rows.append((obj_id, row))

        if not typed_rows:
            _log.info("project_for_object_type: %s/%s no rows with valid PK", ontology_api_name, object_type_api_name)
            return result

        # T1.4: 批量复用 rid（rid 权威源是 Doris idx, design §4.4）。
        # 从 Doris 一次性查所有 PK 的 rid，命中则复用，未命中则新分配。
        # Doris 不可用或 index_store 未注入时，全部新分配（fail-soft，
        # 不阻塞投影；后续 re-sync 会重新分配导致 rid 不稳定，记 warning）。
        pk_values = [pk for pk, _ in typed_rows]
        rid_map: dict[str, str | None] = {str(pk): None for pk in pk_values}
        if self._index_store is not None:
            try:
                rid_map = await self._index_store.get_rids_by_pks(
                    ontology_api_name,
                    object_type_api_name,
                    pk_column,
                    pk_values,
                )
            except Exception as exc:  # noqa: BLE001 — fail-soft
                _log.warning(
                    "project_for_object_type: get_rids_by_pks failed for %s/%s, will allocate fresh rids: %s",
                    ontology_api_name,
                    object_type_api_name,
                    exc,
                )

        # 分配/复用 rid + 写 Doris idx（唯一数据同步路径，T1.10 backfill 已废弃）。
        doris_records: list[dict[str, Any]] = []
        rid_assignments: list[tuple[str, str]] = []  # (pk_str, rid)
        for pk, row in typed_rows:
            pk_str = str(pk)
            rid = rid_map.get(pk_str)
            if not rid:
                rid = generate_object_rid()
                rid_map[pk_str] = rid
            rid_assignments.append((pk_str, rid))
            doris_records.append({**row, "rid": rid})

        # 写 Doris idx（外部接入路径写入 Doris 成为唯一路径，backfill 已废弃）。
        if self._index_store is not None and doris_records:
            try:
                await self._index_store.upsert(ontology_api_name, object_type_api_name, doris_records)
            except Exception as exc:  # noqa: BLE001 — fail-soft
                _log.warning(
                    "project_for_object_type: Doris upsert failed for %s/%s: %s",
                    ontology_api_name,
                    object_type_api_name,
                    exc,
                )

        # 逐条投影（fail-tolerant：单条失败不中断整批）。
        # rid_assignments 与 typed_rows 顺序一致，直接 zip 避免 O(n²) 查找。
        for (pk, row), (pk_str, rid) in zip(typed_rows, rid_assignments, strict=True):
            object_state = {"rid": rid, "properties": row}
            if want_graph:
                try:
                    await self._graph_projector.project_object(  # type: ignore[union-attr]
                        ontology_api_name,
                        object_type_api_name,
                        object_state,
                    )
                    result["graph"] += 1
                except Exception as exc:
                    _log.debug(
                        "graph project failed for %s/%s rid=%s: %s", ontology_api_name, object_type_api_name, rid, exc
                    )

            if want_geotime:
                try:
                    await self._geotime_projector.project_object(  # type: ignore[union-attr]
                        ontology_api_name,
                        object_type_api_name,
                        object_state,
                    )
                    result["geotime"] += 1
                except Exception as exc:
                    _log.debug(
                        "geotime project failed for %s/%s rid=%s: %s", ontology_api_name, object_type_api_name, rid, exc
                    )

        _log.info(
            "project_for_object_type: %s/%s done — graph=%d geotime=%d",
            ontology_api_name,
            object_type_api_name,
            result["graph"],
            result["geotime"],
        )
        return result

    async def project_for_dataset(
        self,
        dataset_api_name: str,
        *,
        limit: int = _SCAN_BATCH_SIZE,
    ) -> dict[str, dict[str, int]]:
        """对 dataset 关联的所有 ObjectType 执行投影。

        查询哪些 ObjectType 的 properties 引用了此 dataset（通过
        backing_dataset_api_name），然后逐个调用 project_for_object_type。

        自动通过 ontology_id 反查 ontology_api_name（跨本体 dataset 共享场景）。

        Args:
            dataset_api_name: Dataset api_name（snake_case，= Iceberg 表名）。
            limit: 每个 ObjectType 的 Iceberg 最大扫描行数。

        Returns:
            {ot_api_name: {"graph": n, "geotime": m}, ...}
        """
        results: dict[str, dict[str, int]] = {}

        ots = await self._metadata.get_object_types_for_dataset(dataset_api_name)
        if not ots:
            _log.info("project_for_dataset: no ObjectType references dataset %s", dataset_api_name)
            return results

        # 批量反查 ontology_id → api_name（用 metadata 公开方法，不访问 _session）。
        ontology_map = await self._metadata.get_ontology_api_names_by_ids(list({ot.ontology_id for ot in ots}))

        for ot in ots:
            ont_api = ontology_map.get(ot.ontology_id)
            if not ont_api:
                _log.warning(
                    "project_for_dataset: cannot resolve ontology for OT %s (ontology_id=%s)",
                    ot.api_name,
                    ot.ontology_id,
                )
                continue
            try:
                ot_result = await self.project_for_object_type(
                    ont_api,
                    ot.api_name,
                    dataset_api_name=dataset_api_name,
                    limit=limit,
                )
                results[ot.api_name] = ot_result
            except Exception as exc:
                _log.warning("project_for_dataset: failed for OT %s: %s", ot.api_name, exc)
        return results

    # ── Internal helpers ──

    @staticmethod
    def _extract_pk(row: dict[str, Any], pk_column: str) -> str | None:
        """从 Iceberg 行中提取主键值。

        Iceberg 的列名可能是 snake_case（如 ``supplierid``），而 ObjectType
        的 primary_key 是 camelCase（如 ``supplierId``）。这里做不区分大小写的
        匹配，并优先用 primary_key 列，其次退到 ``object_id``。

        返回 str 或 None（找不到时）。注意 None 值会被正确处理（不会变成
        字符串 "None"），数字 0 不会被当成 falsy 跳过。
        """
        # 1. 精确匹配 primary_key 列。
        raw = row.get(pk_column)
        # 2. 精确匹配 object_id（Action 写入路径的标识列）。
        if raw is None:
            raw = row.get("object_id")
        # 3. 不区分大小写匹配（Iceberg snake_case vs OT camelCase）。
        if raw is None:
            pk_lower = pk_column.lower()
            for key, val in row.items():
                if key.lower() == pk_lower:
                    raw = val
                    break
        # None / 空值视为无主键。
        if raw is None:
            return None
        text = str(raw)
        return text if text else None

    # ── ADR-021 VIRTUAL 联邦投影（PR 1：节点投影 MVP） ──

    async def project_for_virtual_object_type(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        *,
        batch_size: int = _VIRTUAL_BATCH_SIZE,
    ) -> dict[str, Any]:
        """对单个 VIRTUAL ObjectType 执行图投影（身份骨架，ADR-021 §2.1）。

        旁路 Gate 1（Gate 1 仍对 project_for_object_type 生效）。数据源是
        Trino 联邦查外部源，不是 IcebergStore.scan_latest。

        流程：
          1. 查 ObjectType 元数据（PK api + title api + indexed 列表 + backing_column）
          2. 从 ObjectQueryService._virtual_table_ref 拿 Trino table ref
          3. 游标分页从 Trino 拉数据：SELECT pk,title,indexed FROM ref
             WHERE pk > $last ORDER BY pk LIMIT $batch
          4. 逐批合成 object_state（带 _virtual/_source_ref/_sync_tag），调
             GraphProjector.project_object
          5. 孤儿清理（cleanup_stale_virtual，watermark + cleanup）

        PR 1 只做节点投影。FK→边投影归 PR 2（_project_virtual_edges）。

        Returns:
            {"nodes": n, "cleaned": k, "partial": bool, "error": str | None}
        """
        result: dict[str, Any] = {"nodes": 0, "cleaned": 0, "partial": False, "error": None}

        # 前置依赖检查：Trino + object_query 必须注入，graph_projector 必须有。
        if self._engine is None or self._object_query is None:
            result["error"] = "TrinoQueryEngine or ObjectQueryService not injected"
            result["partial"] = True
            _log.warning("project_for_virtual_object_type skipped: %s", result["error"])
            return result
        if self._graph_projector is None:
            result["error"] = "GraphProjector not available (Neo4j disabled)"
            result["partial"] = True
            _log.warning("project_for_virtual_object_type skipped: %s", result["error"])
            return result

        ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        if ot.storage_type != "VIRTUAL":
            result["error"] = f"ObjectType {object_type_api_name} is not VIRTUAL (got {ot.storage_type})"
            result["partial"] = True
            return result
        if not ot.primary_key:
            result["error"] = f"ObjectType {object_type_api_name} has no primary_key"
            result["partial"] = True
            return result

        table_ref = await self._object_query._virtual_table_ref(ot)

        # 物理列名映射：api_name → backing_column（Trino 查物理列名）。
        pk_api = ot.primary_key
        pk_col = self._api_to_backing_column(ot, pk_api) or _to_snake(pk_api)
        title_api = ot.title_property or ""
        title_col = self._api_to_backing_column(ot, title_api) if title_api else None
        indexed_props = [p for p in (ot.properties or []) if p.indexed]
        indexed_cols = {
            p.api_name: self._api_to_backing_column(ot, p.api_name) or _to_snake(p.api_name) for p in indexed_props
        }

        # SELECT 列表（去重保序：pk, title?, indexed...）
        select_cols = [pk_col]
        if title_col and title_col not in select_cols:
            select_cols.append(title_col)
        for col in indexed_cols.values():
            if col not in select_cols:
                select_cols.append(col)
        select_clause = ", ".join(f'"{c}"' for c in select_cols)

        # watermark：本次投影的 sync_tag（int epoch，难点 2/4 决策）。
        sync_tag = int(time.time())

        nodes_total = 0
        last_pk: Any = None
        try:
            while True:
                # 游标分页：WHERE pk > $last ORDER BY pk LIMIT $batch（难点 3 决策，弃 OFFSET）。
                if last_pk is None:
                    sql = f'SELECT {select_clause} FROM {table_ref} ORDER BY "{pk_col}" LIMIT ?'
                    rows = await self._engine.query(sql, [batch_size])
                else:
                    sql = f'SELECT {select_clause} FROM {table_ref} WHERE "{pk_col}" > ? ORDER BY "{pk_col}" LIMIT ?'
                    rows = await self._engine.query(sql, [last_pk, batch_size])
                if not rows:
                    break

                # 逐行合成 object_state，批量投影。
                states: list[dict[str, Any]] = []
                for row in rows:
                    row_pk = row.get(pk_col)
                    if row_pk is None:
                        continue
                    pk_str = str(row_pk)
                    rid = generate_virtual_rid(ontology_api_name, object_type_api_name, pk_str)
                    props: dict[str, Any] = {pk_api: pk_str}
                    if title_api and title_col:
                        props[title_api] = row.get(title_col)
                    for p in indexed_props:
                        col = indexed_cols[p.api_name]
                        props[p.api_name] = row.get(col)
                    states.append(
                        {
                            "rid": rid,
                            "object_type_api_name": object_type_api_name,
                            "properties": props,
                            "_virtual": True,
                            "_source_ref": table_ref,
                            "_sync_tag": sync_tag,
                        }
                    )

                # 批量投影（GraphProjector 内部调 upsert_node）。
                for state in states:
                    await self._graph_projector.project_object(ontology_api_name, object_type_api_name, state)
                nodes_total += len(states)

                # 游标推进。
                last_pk = rows[-1].get(pk_col)
                if len(rows) < batch_size:
                    break
        except Exception as exc:
            _log.warning(
                "VIRTUAL 投影 Trino 查询失败 %s.%s: %s（已投影 %d 节点保留）",
                ontology_api_name,
                object_type_api_name,
                exc,
                nodes_total,
            )
            result["partial"] = True
            result["error"] = str(exc)
            # 投影失败仍尝试 cleanup 已投影的部分（best-effort）

        # 孤儿清理：删除本次未触及的 VIRTUAL 节点（源里已删除的，§2.4）。
        # 仅在成功投影至少一批后才 cleanup，避免空投影误删全量。
        # 边投影（§2.3）：节点投影后、孤儿清理前。先建后删保无窗口期断链。
        if nodes_total > 0:
            try:
                edges_count = await self._project_virtual_edges(ontology_api_name, ot, sync_tag)
                result["edges"] = edges_count
            except Exception as exc:
                _log.warning("VIRTUAL 边投影失败 %s: %s", object_type_api_name, exc)
                result["partial"] = True
                result.setdefault("edges", 0)

        # 孤儿清理：删除本次未触及的 VIRTUAL 节点（源里已删除的，§2.4）。
        # 仅在成功投影至少一批后才 cleanup，避免空投影误删全量。
        if nodes_total > 0:
            try:
                result["cleaned"] = await self._graph_projector.cleanup_stale_virtual(
                    ontology_api_name, object_type_api_name, sync_tag
                )
            except Exception as exc:
                _log.warning("cleanup_stale_virtual 失败 %s: %s", object_type_api_name, exc)
                result["partial"] = True

        result["nodes"] = nodes_total
        return result

    @staticmethod
    def _api_to_backing_column(ot: Any, api_name: str) -> str | None:
        """从 ObjectType.properties 查 api_name 对应的 backing_column。

        Returns: backing_column 字符串，或 None（属性未绑 backing_column）。
        """
        for p in ot.properties or []:
            if p.api_name == api_name:
                bc: str | None = getattr(p, "backing_column", None)
                if bc:
                    return bc
        return None

    # ── ADR-021 FK→边投影（PR 2） ──

    @staticmethod
    def _resolve_fk_backing_column(
        link: LinkTypeDef, src_ot: ObjectTypeModel, tgt_ot: ObjectTypeModel
    ) -> tuple[str, ObjectTypeModel] | None:
        """解析 FK 属性的物理列名 + 归属的 ObjectType（§2.3 难点 1）。

        LinkType.foreign_key_property_api_name 是属性 api_name（非物理列名）。
        按 source 端优先 → target 端兜底查找 backing_column。

        Returns: (backing_column, owning_ot) 或 None（FK 缺失或属性未绑 backing_column）。
        """
        fk_api = link.foreign_key_property_api_name
        if not fk_api:
            return None
        # source 端优先
        for p in src_ot.properties or []:
            if p.api_name == fk_api and p.backing_column:
                return (p.backing_column, src_ot)
        # target 端兜底
        for p in tgt_ot.properties or []:
            if p.api_name == fk_api and p.backing_column:
                return (p.backing_column, tgt_ot)
        return None

    async def _project_virtual_edges(self, ontology_api_name: str, ot: ObjectType, sync_tag: int) -> int:
        """对该 VIRTUAL ObjectType 相关的所有 LinkType 投影边（§2.3）。

        三种形态：
          1. 两端都 MANAGED → 跳过（MANAGED 边由 Action Step 11 投影）
          2. 两端都 VIRTUAL → 内存 join（_project_virtual_virtual_edges）
          3. 一端 VIRTUAL 一端 MANAGED → PG 反查 PK→rid（_project_virtual_managed_edges）

        Returns: 投影的边总数。
        """
        if self._engine is None or self._object_query is None:
            return 0
        assert self._engine is not None and self._object_query is not None  # mypy narrow
        ot_id = getattr(ot, "id", "")
        if not ot_id:
            return 0

        links = await self._metadata.get_link_types(ontology_api_name)
        # 仅处理当前 OT 作为 source 或 target 的 LinkType。
        relevant = [lk for lk in links if lk.source_object_type_id == ot_id or lk.target_object_type_id == ot_id]
        if not relevant:
            return 0

        total_edges = 0
        for link in relevant:
            try:
                src_ot_model = await self._metadata.get_object_type_by_id(link.source_object_type_id)
                tgt_ot_model = await self._metadata.get_object_type_by_id(link.target_object_type_id)
                if src_ot_model is None or tgt_ot_model is None:
                    _log.warning("LinkType %s 端点 OT 不存在，跳过", link.api_name)
                    continue
                src_ot, src_space, src_proj = src_ot_model
                tgt_ot, tgt_space, tgt_proj = tgt_ot_model
                src_virtual = src_ot.storage_type == "VIRTUAL"
                tgt_virtual = tgt_ot.storage_type == "VIRTUAL"

                # 情况 1：两端都 MANAGED → 跳过（不在此投影）
                if not src_virtual and not tgt_virtual:
                    continue

                fk = self._resolve_fk_backing_column(link, src_ot, tgt_ot)
                if fk is None:
                    _log.warning("LinkType %s 缺 FK 元数据，边不投影", link.api_name)
                    continue
                fk_col, _fk_owning_ot = fk

                if src_virtual and tgt_virtual:
                    # 情况 2：两端都 VIRTUAL
                    n = await self._project_virtual_virtual_edges(ontology_api_name, link, src_ot, tgt_ot, fk_col)
                else:
                    # 情况 3：一端 VIRTUAL 一端 MANAGED
                    n = await self._project_virtual_managed_edges(ontology_api_name, link, src_ot, tgt_ot, fk_col)
                total_edges += n
            except Exception as exc:
                _log.warning("LinkType %s 边投影失败: %s（跳过该 link）", link.api_name, exc)
        return total_edges

    async def _project_virtual_managed_edges(
        self,
        ontology_api_name: str,
        link: LinkTypeDef,
        src_ot: ObjectTypeModel,
        tgt_ot: ObjectTypeModel,
        fk_col: str,
    ) -> int:
        """一端 VIRTUAL 一端 MANAGED 的边投影（§2.3 情况 3）。

        FK 在 VIRTUAL 端，指向 MANAGED 端 PK：
          1. 从 Trino 拉 VIRTUAL 表的 (fk, virtual_pk) 对
          2. 收集 MANAGED PK 值，批量反查 rid（get_object_states_by_pks）
          3. 构造 (virtual_rid, managed_rid) 边集，悬空 FK 跳过
          4. 批量 project_links_batch
        """
        assert self._engine is not None and self._object_query is not None  # mypy narrow (caller 已检查)
        # 判定哪端是 VIRTUAL
        if src_ot.storage_type == "VIRTUAL":
            virtual_ot, managed_ot = src_ot, tgt_ot
            virtual_is_source = True
        else:
            virtual_ot, managed_ot = tgt_ot, src_ot
            virtual_is_source = False

        virtual_pk_api = virtual_ot.primary_key
        if not virtual_pk_api:
            return 0
        virtual_pk_col = self._api_to_backing_column(virtual_ot, virtual_pk_api) or _to_snake(virtual_pk_api)
        managed_pk_api = managed_ot.primary_key
        if not managed_pk_api:
            return 0
        managed_pk_backing = self._api_to_backing_column(managed_ot, managed_pk_api) or _to_snake(managed_pk_api)

        virtual_table_ref = await self._object_query._virtual_table_ref(virtual_ot)  # type: ignore[arg-type]

        # 游标分页拉 VIRTUAL 表 (fk_col, virtual_pk_col)
        edges: list[tuple[str, str]] = []
        managed_pks_seen: list[str] = []
        rows_by_pk: list[tuple[str, str]] = []  # (virtual_pk, fk_value)
        last_pk: Any = None
        batch_size = _VIRTUAL_BATCH_SIZE
        while True:
            if last_pk is None:
                sql = (
                    f'SELECT "{virtual_pk_col}", "{fk_col}" FROM {virtual_table_ref} '
                    f'ORDER BY "{virtual_pk_col}" LIMIT ?'
                )
                rows = await self._engine.query(sql, [batch_size])
            else:
                sql = (
                    f'SELECT "{virtual_pk_col}", "{fk_col}" FROM {virtual_table_ref} '
                    f'WHERE "{virtual_pk_col}" > ? ORDER BY "{virtual_pk_col}" LIMIT ?'
                )
                rows = await self._engine.query(sql, [last_pk, batch_size])
            if not rows:
                break
            for row in rows:
                vpk = row.get(virtual_pk_col)
                fkv = row.get(fk_col)
                if vpk is None or fkv is None:
                    continue
                vpk_str = str(vpk)
                fk_str = str(fkv)
                rows_by_pk.append((vpk_str, fk_str))
                managed_pks_seen.append(fk_str)
            last_pk = rows[-1].get(virtual_pk_col)
            if len(rows) < batch_size:
                break

        if not rows_by_pk:
            return 0

        # 批量反查 MANAGED 端 PK→rid
        managed_states = await self._metadata.get_object_states_by_pks(
            ontology_api_name,
            managed_ot.api_name,
            managed_pk_backing,
            managed_pks_seen,
        )
        pk_to_rid: dict[str, str] = {}
        for s in managed_states:
            props = s.get("properties", {})
            pk_val = props.get(managed_pk_api)
            if pk_val is not None:
                pk_to_rid[str(pk_val)] = s["rid"]

        # 构造边（悬空 FK 跳过）
        virtual_rid_base = (ontology_api_name, virtual_ot.api_name)
        for vpk_str, fk_str in rows_by_pk:
            managed_rid = pk_to_rid.get(fk_str)
            if managed_rid is None:
                continue  # 悬空 FK
            virtual_rid = generate_virtual_rid(virtual_rid_base[0], virtual_rid_base[1], vpk_str)
            if virtual_is_source:
                edges.append((virtual_rid, managed_rid))
            else:
                edges.append((managed_rid, virtual_rid))

        if not edges:
            return 0
        return await self._graph_projector.project_links_batch(  # type: ignore[union-attr]
            ontology_api_name,
            link.api_name,
            src_ot.api_name,
            tgt_ot.api_name,
            edges,
        )

    async def _project_virtual_virtual_edges(
        self,
        ontology_api_name: str,
        link: LinkTypeDef,
        src_ot: ObjectTypeModel,
        tgt_ot: ObjectTypeModel,
        fk_col: str,
    ) -> int:
        """两端都 VIRTUAL 的边投影（§2.3 情况 2，难点 5 内存 join）。

        不走 Trino 跨 catalog JOIN，分两步拉取 + 内存 join：
          1. 从 source VIRTUAL 表拉 (source_pk, fk) 对
          2. 从 target VIRTUAL 表拉 (target_pk,) 集
          3. 内存 join：fk == target_pk → (source_rid, target_rid) 边集
          4. 批量 project_links_batch

        比 _project_virtual_managed_edges 更简单（两端 rid 都合成，不需 PG 反查）。
        """
        assert self._engine is not None and self._object_query is not None  # mypy narrow (caller 已检查)
        src_pk_api = src_ot.primary_key
        tgt_pk_api = tgt_ot.primary_key
        if not src_pk_api or not tgt_pk_api:
            return 0
        src_pk_col = self._api_to_backing_column(src_ot, src_pk_api) or _to_snake(src_pk_api)
        tgt_pk_col = self._api_to_backing_column(tgt_ot, tgt_pk_api) or _to_snake(tgt_pk_api)

        src_ref = await self._object_query._virtual_table_ref(src_ot)  # type: ignore[arg-type]
        tgt_ref = await self._object_query._virtual_table_ref(tgt_ot)  # type: ignore[arg-type]

        # 1. 拉 source (source_pk, fk)
        src_pairs: list[tuple[str, str]] = []
        last_pk: Any = None
        batch_size = _VIRTUAL_BATCH_SIZE
        while True:
            if last_pk is None:
                sql = f'SELECT "{src_pk_col}", "{fk_col}" FROM {src_ref} ORDER BY "{src_pk_col}" LIMIT ?'
                rows = await self._engine.query(sql, [batch_size])
            else:
                sql = (
                    f'SELECT "{src_pk_col}", "{fk_col}" FROM {src_ref} '
                    f'WHERE "{src_pk_col}" > ? ORDER BY "{src_pk_col}" LIMIT ?'
                )
                rows = await self._engine.query(sql, [last_pk, batch_size])
            if not rows:
                break
            for row in rows:
                spk = row.get(src_pk_col)
                fkv = row.get(fk_col)
                if spk is not None and fkv is not None:
                    src_pairs.append((str(spk), str(fkv)))
            last_pk = rows[-1].get(src_pk_col)
            if len(rows) < batch_size:
                break

        if not src_pairs:
            return 0

        # 2. 拉 target (target_pk,) 集，建 set 加速 join
        tgt_pk_set: set[str] = set()
        last_tpk: Any = None
        while True:
            if last_tpk is None:
                sql = f'SELECT "{tgt_pk_col}" FROM {tgt_ref} ORDER BY "{tgt_pk_col}" LIMIT ?'
                rows = await self._engine.query(sql, [batch_size])
            else:
                sql = f'SELECT "{tgt_pk_col}" FROM {tgt_ref} WHERE "{tgt_pk_col}" > ? ORDER BY "{tgt_pk_col}" LIMIT ?'
                rows = await self._engine.query(sql, [last_tpk, batch_size])
            if not rows:
                break
            for row in rows:
                tpk = row.get(tgt_pk_col)
                if tpk is not None:
                    tgt_pk_set.add(str(tpk))
            last_tpk = rows[-1].get(tgt_pk_col)
            if len(rows) < batch_size:
                break

        # 3. 内存 join：fk == target_pk
        edges: list[tuple[str, str]] = []
        for spk, fkv in src_pairs:
            if fkv in tgt_pk_set:
                src_rid = generate_virtual_rid(ontology_api_name, src_ot.api_name, spk)
                tgt_rid = generate_virtual_rid(ontology_api_name, tgt_ot.api_name, fkv)
                edges.append((src_rid, tgt_rid))

        if not edges:
            return 0
        return await self._graph_projector.project_links_batch(  # type: ignore[union-attr]
            ontology_api_name,
            link.api_name,
            src_ot.api_name,
            tgt_ot.api_name,
            edges,
        )
