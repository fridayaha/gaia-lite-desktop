"""DataFrameQueryService — 推理线编排中枢 (graph-reasoning-design.md §7.3)。

ObjectSet IR → 多引擎执行 + 防线 + 证据链。

求值流程（递归）：
  objectType → 从 PG object_state 取该类型 rid（起始集）
  static → pk 列表解析为 rid（查 object_state）
  filter → 子集 rid + PG 侧过滤（属性/空间/时序）
  searchAround → 子集 rid + Neo4j 图遍历

最后水合全量属性（C12，借 object_state 批量取；Doris 水合留优化期）。

职责隔离红线（C5/C12）：
- filter 不碰 Doris（属性/空间/时序都走 PG）
- searchAround 走 Neo4j
- 水合借 ObjectQueryService（MVP 用 object_state 批量取）

防线（C9 包容式）：超限截断 + truncated + 游标；不拒绝用户。
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import TYPE_CHECKING, Any

from ontology.config.settings import settings
from ontology.core.exceptions import NotFoundError, ValidationError
from ontology.core.naming import graph_label, graph_relationship_type
from ontology.core.rid import is_managed_rid, is_virtual_rid, parse_virtual_rid_pk
from ontology.core.schemas.geotime import SpatialFilter
from ontology.core.schemas.object_set import Filter, ObjectSetIR, ReasoningResult
from ontology.core.schemas.ontology import ObjectType

if TYPE_CHECKING:
    from ontology.layers.geotime.geotime_store import GeoTimeStore
    from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
    from ontology.services.object_query_service import ObjectQueryService

_log = logging.getLogger(__name__)

# searchAround 最大嵌套深度（C7 Palantir 硬限）。
MAX_SEARCH_AROUND_DEPTH = 3

# 属性算子集（非空间/非时序），用于分流。
ATTR_OPS = frozenset(
    {
        "exactMatch",
        "notEqual",
        "in",
        "notIn",
        "range",
        "greaterThan",
        "lessThan",
        "contains",
        "startsWith",
        "endsWith",
        "isNull",
        "isNotNull",
    }
)


class DataFrameQueryService:
    """编排中枢。ObjectSet IR → 多引擎执行 + 防线 + 证据链。"""

    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        geotime_store: GeoTimeStore,
        metadata: PostgresMetaStore,
        *,
        attr_engine: Any | None = None,
        object_query_service: ObjectQueryService | None = None,
    ) -> None:
        self._graph = graph_store
        self._geotime = geotime_store
        self._metadata = metadata
        # 属性过滤用的 PG engine（object_state JSONB 过滤）。None 时回退内存
        # 过滤（单测用，避免真实 PG 依赖）。生产注入模块级 engine。
        self._attr_engine = attr_engine
        # 水合用：VIRTUAL rid 解析出 PK 后走 ObjectQueryService.hydrate_by_pk
        # （Trino 跨 catalog 联邦查外部源表，ADR-014）。MANAGED rid 当前仍走
        # PG object_state（MVP），未来切 Doris 主源点查（ADR-001，handoff §3.4）。
        self._object_query = object_query_service
        # P2: 本体属性白名单（field 名校验，红线 8）。execute 入口一次性加载，
        # _eval_filter / _eval_where 入口校验。空集表示未加载/本体无 OT，跳过校验
        # （兼容边界 + 测试 mock）。request-scoped 实例，无并发问题。
        self._allowed_fields: set[str] = set()
        # per-OT backing_column↔api_name 映射（object_state 存 backing_column，
        # 语义层用 api_name）。随 _load_allowed_fields 一起加载。
        self._ot_backing_to_api: dict[str, dict[str, str]] = {}
        self._ot_api_to_backing: dict[str, dict[str, str]] = {}
        # Flat api_name → backing_column (OT-agnostic, ambiguous keys dropped).
        self._flat_api_to_backing: dict[str, str] = {}

    async def execute(self, ir: ObjectSetIR, ontology_api_name: str, *, cursor: str | None = None) -> ReasoningResult:
        """执行 ObjectSet IR 查询，返回水合对象集 + 证据链。

        Args:
            cursor: 分页游标（上页最后一个 rid），从此 rid 之后开始水合。None 从头开始。
        """
        # 防线一：嵌套深度校验（C7）。
        depth = ir.search_around_depth()
        if depth > MAX_SEARCH_AROUND_DEPTH:
            raise ValueError(f"searchAround nesting depth {depth} exceeds max {MAX_SEARCH_AROUND_DEPTH}")

        # P2: 加载本体属性白名单（一次性，供 _eval_filter/_eval_where 校验 field）。
        # 防止拼错属性名静默返回空结果（「错误必须有可读反馈，禁止静默失败」）。
        # best-effort：加载失败不阻塞查询（跳过白名单校验，回退原行为）。
        await self._load_allowed_fields(ontology_api_name)

        # aggregate 特殊路径：求值子集 → 水合 → 内存聚合 → 返回 aggregates
        if ir.type == "aggregate":
            return await self._execute_aggregate(ir, ontology_api_name)

        # 1. 递归求值 IR 树 → rid 集 + 证据。
        evidence = EvidenceChain()
        rids = await self._eval_object_set(ir, ontology_api_name, evidence)

        # 1.5 可选排序：order_by 保证 cursor 分页稳定性（无 order_by 时顺序不稳定）
        if ir.order_by:
            rids = await self._sort_rids(rids, ir.order_by, ontology_api_name)

        # 2. 防线二：水合上限（C9，Palantir 1 万）。
        hydrate_limit = settings.hydrate_limit
        # cursor 分页：找到 cursor 在 rids 中的位置，从下一位开始
        start_idx = 0
        if cursor and cursor in rids:
            start_idx = rids.index(cursor) + 1
        page_rids = rids[start_idx : start_idx + hydrate_limit]
        truncated = start_idx + hydrate_limit < len(rids)
        rids_to_hydrate = page_rids

        # 3. 水合全量属性（C12，借 object_state 批量取）。select 时只取投影字段。
        objects = await self._hydrate(rids_to_hydrate, ir.select_fields if ir.type == "select" else None)

        # 4. 构建结果 + 证据链摘要。
        stats = {
            "steps": evidence.step_count,
            "engines_used": list(evidence.engines_used),
            "timings": evidence.timings,
            "total_rids": len(rids),
            "hydrated": len(objects),
        }

        # 5. 证据链快照（M6, §10.2）：保存 ObjectSet IR + 各步摘要 + 血缘指针。
        evidence_id = await self._save_evidence(ontology_api_name, ir, stats, rids_to_hydrate, evidence)

        return ReasoningResult(
            objects=objects,
            edges=evidence.edges,
            truncated=truncated,
            next_cursor=rids_to_hydrate[-1] if truncated and rids_to_hydrate else None,
            stats=stats,
            evidence_id=evidence_id,
        )

    async def _save_evidence(
        self,
        ontology_api_name: str,
        ir: ObjectSetIR,
        stats: dict[str, Any],
        rids: list[str],
        evidence: EvidenceChain,
    ) -> str | None:
        """保存证据链快照到 analysis_records（M6）。best-effort，失败不阻塞查询。"""
        import logging

        _log = logging.getLogger(__name__)
        try:
            from ontology.services.analysis_record_store import AnalysisRecordStore

            # 从 api_name 解析 ontology_id。
            onto = await self._metadata.get_ontology(ontology_api_name)
            store = AnalysisRecordStore(self._metadata.session)
            result_summary = {
                **stats,
                "steps_detail": evidence.steps,
                "truncated": evidence.truncated,
            }
            evidence_pointers = {
                "matched_rids": rids,
                "object_count": len(rids),
            }
            return await store.save(
                ontology_id=onto.id,
                object_set_ir=ir.model_dump(),
                result_summary=result_summary,
                evidence_pointers=evidence_pointers,
            )
        except Exception as exc:
            _log.warning("Failed to save analysis record evidence: %s", exc)
            return None

    async def _eval_object_set(self, ir: ObjectSetIR, ontology_api_name: str, evidence: EvidenceChain) -> list[str]:
        """递归求值 ObjectSet IR 树，返回 rid 集。"""
        if ir.type == "objectType":
            return await self._eval_object_type(ir, ontology_api_name, evidence)
        if ir.type == "static":
            return await self._eval_static(ir, ontology_api_name, evidence)
        if ir.type == "filter":
            base_rids = await self._eval_object_set(ir.object_set, ontology_api_name, evidence)  # type: ignore[arg-type]
            # where（嵌套逻辑）优先于 filters（flat AND）
            if ir.where is not None:
                return await self._eval_where(ir.where, base_rids, ontology_api_name, evidence)
            return await self._eval_filter(ir.filters or [], base_rids, ontology_api_name, evidence)
        if ir.type == "searchAround":
            base_rids = await self._eval_object_set(ir.object_set, ontology_api_name, evidence)  # type: ignore[arg-type]
            return await self._eval_search_around(ir, ontology_api_name, base_rids, evidence)
        if ir.type in ("union", "intersect", "subtract"):
            return await self._eval_set_op(ir, ontology_api_name, evidence)
        if ir.type == "select":
            # select 透传子集 rid，投影在 execute 的 _hydrate 处理
            return await self._eval_object_set(ir.object_set, ontology_api_name, evidence)  # type: ignore[arg-type]
        if ir.type in ("withProperties", "reference"):
            raise NotImplementedError(
                f"{ir.type} not yet implemented (requires expression engine / persisted ObjectSet store)"
            )
        if ir.type == "interfaceBase":
            return await self._eval_interface_base(ir, ontology_api_name, evidence)
        if ir.type == "interfaceLinkSearchAround":
            base_rids = await self._eval_object_set(ir.object_set, ontology_api_name, evidence)  # type: ignore[arg-type]
            return await self._eval_interface_link_search_around(ir, ontology_api_name, base_rids, evidence)
        return []

    async def _eval_object_type(self, ir: ObjectSetIR, ontology_api_name: str, evidence: EvidenceChain) -> list[str]:
        """起始对象集：取该 ObjectType 全部 rid（可带内联 filter）。"""
        import time

        t0 = time.monotonic()
        assert ir.object_type is not None  # 校验器保证
        ot_api = ir.object_type
        # 只取 rid，不拉 properties JSONB（大规模友好）
        rids = await self._metadata.get_rids_by_type(ot_api, limit=settings.graph_traversal_result_limit)
        evidence.record("object_type", "postgres", time.monotonic() - t0, len(rids))

        # objectType 内联 filter（起始集就过滤）。where 优先于 filters。
        if ir.where is not None:
            rids = await self._eval_where(ir.where, rids, ontology_api_name, evidence)
        elif ir.filters:
            rids = await self._eval_filter(ir.filters, rids, ontology_api_name, evidence)
        return rids

    async def _eval_static(self, ir: ObjectSetIR, ontology_api_name: str, evidence: EvidenceChain) -> list[str]:
        """static: 业务主键列表 → rid（查 object_state by pk）。

        API 边界翻译层（ADR-019）：Agent 传业务主键，内部解析为 rid。
        ``ir.object_type`` 必填——翻译需 ObjectType 的 primary_key 字段名。
        """
        import time

        t0 = time.monotonic()
        pks = ir.objects or []
        if not pks:
            return []
        if not ir.object_type:
            raise ValueError("static requires object_type to resolve primary keys to rids")
        rids = await self._resolve_rids_by_pk(ontology_api_name, ir.object_type, pks)
        rids = list(dict.fromkeys(rids))  # 去重保序
        evidence.record("static", "postgres", time.monotonic() - t0, len(rids))
        return rids

    async def _load_allowed_fields(self, ontology_api_name: str) -> None:
        """P2: 加载本体所有 ObjectType 的属性 api_name 并集（白名单）。

        同时构建 per-OT 的 backing_column↔api_name 映射表，供 object_state
        读取边界做 key 翻译（object_state 存 backing_column，语义层用 api_name，
        见 core.property_mapping）。best-effort：加载失败清空白名单（跳过校验，
        回退原行为）。
        """
        from ontology.core.property_mapping import api_to_backing_map, backing_to_api_map

        try:
            ots = await self._metadata.list_object_types(ontology_api_name)
            self._allowed_fields = {p.api_name for ot in ots for p in ot.properties}
            self._ot_backing_to_api = {ot.api_name: backing_to_api_map(ot) for ot in ots}
            self._ot_api_to_backing = {ot.api_name: api_to_backing_map(ot) for ot in ots}
            # Flat api_name → backing_column merged across all OTs (for SQL
            # filter predicates that don't carry OT context). Ambiguous keys
            # (same api_name → different backing_column across OTs) are dropped
            # so they fall through to the api_name verbatim — same policy as
            # ObjectQueryService._map_backing_to_api_multi.
            flat: dict[str, str] = {}
            ambiguous: set[str] = set()
            for m in self._ot_api_to_backing.values():
                for api, col in m.items():
                    if api in flat and flat[api] != col:
                        ambiguous.add(api)
                    else:
                        flat[api] = col
            for api in ambiguous:
                flat.pop(api, None)
            self._flat_api_to_backing = flat
        except Exception:
            _log.warning("load allowed_fields failed, skip whitelist", exc_info=True)
            self._allowed_fields = set()
            self._ot_backing_to_api = {}
            self._ot_api_to_backing = {}
            self._flat_api_to_backing = {}

    def _validate_filter_fields(self, filters: list[Filter]) -> None:
        """P2: 校验属性/时序算子的 field 在本体白名单内（红线 8 + 可读错误）。

        空间算子（withinDistance/withinPolygon/withinBoundingBox）的 field 是几何列
        （如 location），由 GeoTime 表结构保证，不走属性白名单。
        空白名单（未加载/本体无 OT）跳过校验，不阻塞查询。
        """
        if not self._allowed_fields:
            return
        for f in filters:
            if f.op in ATTR_OPS or f.op == "timeRange":
                self._check_field(f.field)

    def _validate_where_fields(self, where: Any) -> None:
        """递归校验 where 树（and/or/not）中所有叶子 Filter 的 field。"""
        if not self._allowed_fields:
            return
        if isinstance(where, Filter):
            if where.op in ATTR_OPS or where.op == "timeRange":
                self._check_field(where.field)
        elif hasattr(where, "value"):
            # AndClause / OrClause: value 是 list[WhereClause]
            if isinstance(where.value, list):
                for child in where.value:
                    self._validate_where_fields(child)
            # NotClause: value 是单个 WhereClause
            else:
                self._validate_where_fields(where.value)

    def _check_field(self, field: str) -> None:
        """单字段白名单校验，不在则 raise ValidationError（列出可用属性）。"""
        if field not in self._allowed_fields:
            # 列出可用属性帮助用户/LLM 纠正（限前 20 个避免错误信息过长）
            available = sorted(self._allowed_fields)[:20]
            raise ValidationError(
                f"属性 '{field}' 不在本体中。可用属性：{available}"
                + (f" ...（共 {len(self._allowed_fields)} 个）" if len(self._allowed_fields) > 20 else "")
            )

    def _field_to_backing(self, object_type_api_name: str, field: str) -> str:
        """Translate an api_name filter field → backing_column for JSONB queries.

        object_state stores properties keyed by backing_column; SQL predicates
        like ``(os.properties ->> :field)`` must use the backing_column to match.
        Passthrough when no per-OT mapping is loaded (synthetic OTs / tests).
        """
        mapping = self._ot_api_to_backing.get(object_type_api_name, {})
        return mapping.get(field, field)

    def _field_to_backing_flat(self, field: str) -> str:
        """OT-agnostic api_name → backing_column (for SQL filter predicates).

        Uses the flat cross-OT map; ambiguous / unmapped fields pass through
        verbatim. Passthrough when no ontology loaded (tests).
        """
        return self._flat_api_to_backing.get(field, field)

    def _state_props_to_api(self, state: dict[str, Any]) -> dict[str, Any]:
        """Translate one object_state's properties backing_column → api_name.

        Used at the load boundary so downstream memory matchers / hydration
        output (consumer-facing) speak api_name. Passthrough when the OT has no
        mapping (synthetic OTs / tests) or the type is unknown.
        """
        props = state.get("properties", {}) or {}
        ot_api = state.get("object_type_api_name", "")
        mapping = self._ot_backing_to_api.get(ot_api, {})
        if not mapping:
            return props
        return {mapping.get(k, k): v for k, v in props.items()}

    def _states_props_to_api(self, states: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Translate a batch of object_states' properties backing_column → api_name.

        Returns new dicts (does not mutate input). The non-properties fields
        (rid / version / ...) are copied as-is.
        """
        result: list[dict[str, Any]] = []
        for s in states:
            props = self._state_props_to_api(s)
            if props is s.get("properties", {}):
                result.append(s)
            else:
                result.append({**s, "properties": props})
        return result

    async def _eval_filter(
        self,
        filters: list[Filter],
        base_rids: list[str],
        ontology_api_name: str,
        evidence: EvidenceChain,
    ) -> list[str]:
        """对候选 rid 集应用 filters。属性/空间/时序走 PG，不碰 Doris（C5/C12）。

        有 engine 时走 Ibis 临时表模式（design §7.4）：候选 rids 注册成 PG
        临时表，所有 filter 编译进一条 SQL 下推（PG 优化器自决 join 策略），
        避免分批往返和内存驻留。无 engine 兑底走内存逐个过滤（单测用）。
        """
        if not base_rids:
            return []
        # P2: field 白名单校验（属性/时序算子）。空间算子豁免。
        self._validate_filter_fields(filters)
        if self._attr_engine is not None:
            return await self._eval_filter_sql(filters, base_rids, ontology_api_name, evidence)
        # 无 engine 兑底：逐个 filter 内存过滤
        rids = base_rids
        for f in filters:
            rids = await self._apply_single_filter(f, rids, ontology_api_name, evidence)
            if not rids:
                break
        return rids

    async def _eval_where(
        self,
        where: Any,
        base_rids: list[str],
        ontology_api_name: str,
        evidence: EvidenceChain,
    ) -> list[str]:
        """求值嵌套逻辑组合（and/or/not），对齐 Palantir SearchJsonQueryV2。

        有 engine 时递归编译成一条 SQL（WHERE (.. AND/OR .. AND NOT ..)），
        无 engine 时内存求值。
        """
        if not base_rids:
            return []
        # P2: where 嵌套逻辑组合的 field 白名单校验。
        self._validate_where_fields(where)
        if self._attr_engine is not None:
            return await self._eval_where_sql(where, base_rids, ontology_api_name, evidence)
        # 内存兑底
        states = await self._metadata.get_object_states_by_rids(base_rids)
        state_map = {s["rid"]: self._state_props_to_api(s) for s in states}
        matched = [rid for rid in base_rids if self._match_where_memory(where, state_map.get(rid, {}))]
        return matched

    async def _eval_where_sql(
        self,
        where: Any,
        base_rids: list[str],
        ontology_api_name: str,
        evidence: EvidenceChain,
    ) -> list[str]:
        """SQL 模式求值 where（递归编译 and/or/not）。复用 _eval_filter_sql 的临时表模式。"""
        import time
        import uuid as _uuid

        from sqlalchemy import text

        from ontology.core.naming import geo_table

        t0 = time.monotonic()
        tmp_table = f"_tmp_cand_{_uuid.uuid4().hex[:8]}"
        params: dict[str, Any] = {"rids": base_rids}

        # 收集空间 filter 涉及的 geo 表
        geo_tables: dict[str, str] = {}
        spatial_in_where = self._where_has_spatial(where)
        if spatial_in_where:
            states = await self._metadata.get_object_states_by_rids(base_rids)
            types_seen = {s.get("object_type_api_name", "") for s in states}
            for ot in types_seen:
                if not ot:
                    continue
                tbl = geo_table(ontology_api_name, ot)
                if await self._geotime.table_exists(tbl):
                    geo_tables[ot] = tbl

        # 递归编译 where → SQL 谓词片段
        joins: list[str] = []
        pred, pred_params = self._compile_where(where, "w", joins, geo_tables, 0)
        params.update(pred_params)

        where_clause = pred if pred else "TRUE"
        join_clause = " ".join(joins)
        sql = text(f"""
            WITH {tmp_table} AS (SELECT unnest(CAST(:rids AS text[])) AS rid)
            SELECT DISTINCT os.rid AS rid
            FROM {tmp_table} cand
            INNER JOIN object_state os ON os.rid = cand.rid
            {join_clause}
            WHERE {where_clause}
        """)

        results: list[str] = []
        assert self._attr_engine is not None
        try:
            async with self._attr_engine.connect() as conn:
                res = await conn.execute(sql, params)
                for row in res:
                    results.append(str(row[0]))
        except Exception as exc:
            _log.warning("_eval_where_sql failed, fallback to memory: %s", exc)
            states = await self._metadata.get_object_states_by_rids(base_rids)
            state_map = {s["rid"]: self._state_props_to_api(s) for s in states}
            results = [rid for rid in base_rids if self._match_where_memory(where, state_map.get(rid, {}))]

        engines_used = {"postgres"}
        if spatial_in_where:
            engines_used.add("postgis")
        if self._where_has_temporal(where):
            engines_used.add("timescaledb")
        for eng in engines_used:
            evidence.record(f"filter:{eng}", eng, time.monotonic() - t0, len(results))
        return results

    def _compile_where(
        self,
        where: Any,
        pfx: str,
        joins: list[str],
        geo_tables: dict[str, str],
        depth: int,
    ) -> tuple[str, dict[str, Any]]:
        """递归编译 WhereClause → SQL 谓词。返回 (谓词片段, 参数)。"""
        is_leaf = isinstance(where, Filter)
        wtype = where.op if is_leaf else getattr(where, "type", None)
        # 叶子节点：Filter（type 是 FilterOp）
        if is_leaf:
            return self._compile_filter_node(where, pfx, joins, geo_tables)
        # and/or：递归子节点
        if wtype in ("and", "or"):
            children: list[str] = []
            params: dict[str, Any] = {}
            for i, child in enumerate(where.value):
                cp, cparams = self._compile_where(child, f"{pfx}_{i}", joins, geo_tables, depth + 1)
                children.append(cp)
                params.update(cparams)
            sep = " AND " if wtype == "and" else " OR "
            return f"({sep.join(children)})", params
        # not：递归单个子节点
        if wtype == "not":
            cp, cparams = self._compile_where(where.value, f"{pfx}_n", joins, geo_tables, depth + 1)
            return f"NOT ({cp})", cparams
        return "TRUE", {}

    def _compile_filter_node(
        self, f: Filter, pfx: str, joins: list[str], geo_tables: dict[str, str]
    ) -> tuple[str, dict[str, Any]]:
        """编译单个 Filter 节点（属性/空间/时序），复用 _compile_*_pred。"""
        if f.op in ("withinDistance", "withinPolygon", "withinBoundingBox"):
            spatial = self._build_spatial_filter(f)
            preds: list[str] = []
            params: dict[str, Any] = {}
            for ot, tbl in geo_tables.items():
                alias = f"g_{pfx}_{ot.lower()[:8]}"
                joins.append(f"LEFT JOIN {tbl} {alias} ON os.rid = {alias}.rid")
                pred, fparams = self._compile_spatial_pred(spatial, alias, pfx, ot)
                preds.append(pred)
                params.update(fparams)
            return f"({' OR '.join(preds)})" if preds else "TRUE", params
        if f.op == "timeRange":
            return self._compile_time_pred(f, pfx)
        # 属性算子
        return self._compile_attr_pred(f, pfx)

    def _where_has_spatial(self, where: Any) -> bool:
        """递归检查 where 树是否含空间算子。"""
        is_leaf = isinstance(where, Filter)
        wtype = where.op if is_leaf else getattr(where, "type", None)
        if wtype in ("withinDistance", "withinPolygon", "withinBoundingBox"):
            return True
        if wtype in ("and", "or"):
            return any(self._where_has_spatial(c) for c in where.value)
        if wtype == "not":
            return self._where_has_spatial(where.value)
        return False

    def _where_has_temporal(self, where: Any) -> bool:
        """递归检查 where 树是否含时序算子。"""
        is_leaf = isinstance(where, Filter)
        wtype = where.op if is_leaf else getattr(where, "type", None)
        if wtype == "timeRange":
            return True
        if wtype in ("and", "or"):
            return any(self._where_has_temporal(c) for c in where.value)
        if wtype == "not":
            return self._where_has_temporal(where.value)
        return False

    @staticmethod
    def _match_where_memory(where: Any, props: dict[str, Any]) -> bool:
        """内存求值 where（递归 and/or/not）。"""
        is_leaf = isinstance(where, Filter)
        wtype = where.op if is_leaf else getattr(where, "type", None)
        if wtype in (
            "exactMatch",
            "notEqual",
            "in",
            "notIn",
            "range",
            "greaterThan",
            "lessThan",
            "contains",
            "startsWith",
            "endsWith",
            "isNull",
            "isNotNull",
        ):
            return DataFrameQueryService._match_attr(where, props.get(where.field))
        if wtype == "timeRange":
            v = where.value or {}
            return DataFrameQueryService._match_time(where.field, props, v.get("start"), v.get("end"))
        if wtype in ("withinDistance", "withinPolygon", "withinBoundingBox"):
            return True  # 空间无 PostGIS 无法内存匹配
        if wtype == "and":
            return all(DataFrameQueryService._match_where_memory(c, props) for c in where.value)
        if wtype == "or":
            return any(DataFrameQueryService._match_where_memory(c, props) for c in where.value)
        if wtype == "not":
            return not DataFrameQueryService._match_where_memory(where.value, props)
        return True

    async def _eval_filter_sql(
        self,
        filters: list[Filter],
        base_rids: list[str],
        ontology_api_name: str,
        evidence: EvidenceChain,
    ) -> list[str]:
        """Ibis 临时表模式：候选 rids 建临时表，所有 filter 编译进一条 SQL。

        属性 filter → object_state JSONB 谓词；空间 filter → geo 表 join + PostGIS；
        时序 filter → object_state timestamptz 谓词。混合 filter 一条 SQL 下推。
        """
        import time
        import uuid as _uuid

        from sqlalchemy import text

        from ontology.core.naming import geo_table

        t0 = time.monotonic()
        tmp_table = f"_tmp_cand_{_uuid.uuid4().hex[:8]}"

        # 收集需要的 JOIN（geo 表按 object_type 分组）和 WHERE 片段
        joins: list[str] = []
        wheres: list[str] = []
        params: dict[str, Any] = {"rids": base_rids}

        # 需要知道候选 rid 的 object_type 才能定位 geo 表。
        # 一次性查 object_state 拿类型分组（属性过滤也需 object_state join）。
        # 先收集空间 filter 涉及的 object_type → geo 表映射。
        spatial_filters = [f for f in filters if f.op in ("withinDistance", "withinPolygon", "withinBoundingBox")]
        geo_tables_by_type: dict[str, str] = {}
        if spatial_filters:
            # 查候选 rid 的 object_type 分组
            states = await self._metadata.get_object_states_by_rids(base_rids)
            types_seen = {s.get("object_type_api_name", "") for s in states}
            for ot in types_seen:
                if not ot:
                    continue
                tbl = geo_table(ontology_api_name, ot)
                if await self._geotime.table_exists(tbl):
                    geo_tables_by_type[ot] = tbl

        # 编译每个 filter 为 WHERE 片段 + 必要的 JOIN
        for i, f in enumerate(filters):
            pfx = f"f{i}"
            if f.op in ATTR_OPS:
                # 属性过滤：object_state JSONB（os 已在 JOIN 里）
                pred, fparams = self._compile_attr_pred(f, pfx)
                wheres.append(pred)
                params.update(fparams)
            elif f.op in ("withinDistance", "withinPolygon", "withinBoundingBox"):
                # 空间过滤：每个 geo 表一个 JOIN + OR 谓词
                spatial = self._build_spatial_filter(f)
                for ot, tbl in geo_tables_by_type.items():
                    alias = f"g_{i}_{ot.lower()[:8]}"
                    joins.append(f"LEFT JOIN {tbl} {alias} ON os.rid = {alias}.rid")
                    pred, fparams = self._compile_spatial_pred(spatial, alias, pfx, ot)
                    wheres.append(pred)
                    params.update(fparams)
            elif f.op == "timeRange":
                pred, fparams = self._compile_time_pred(f, pfx)
                wheres.append(pred)
                params.update(fparams)

        # 构建完整 SQL：临时表 + object_state join + geo joins + WHERE
        where_clause = " AND ".join(wheres) if wheres else "TRUE"
        join_clause = " ".join(joins)
        sql = text(f"""
            WITH {tmp_table} AS (SELECT unnest(CAST(:rids AS text[])) AS rid)
            SELECT DISTINCT os.rid AS rid
            FROM {tmp_table} cand
            INNER JOIN object_state os ON os.rid = cand.rid
            {join_clause}
            WHERE {where_clause}
        """)

        results: list[str] = []
        assert self._attr_engine is not None  # _eval_filter 已保证
        try:
            async with self._attr_engine.connect() as conn:
                res = await conn.execute(sql, params)
                for row in res:
                    results.append(str(row[0]))
        except Exception as exc:
            # SQL 执行失败（如 to_timestamp 不兼容 ISO 字符串）→ 纯内存兑底
            _log.warning("_eval_filter_sql failed, fallback to memory: %s", exc)
            states = await self._metadata.get_object_states_by_rids(base_rids)
            matched = list(base_rids)
            for f in filters:
                matched = [
                    s["rid"]
                    for s in states
                    if s["rid"] in matched and self._match_filter_memory(f, self._state_props_to_api(s))
                ]
            return matched

        engines_used = set()
        if any(f.op in ("withinDistance", "withinPolygon", "withinBoundingBox") for f in filters):
            engines_used.add("postgis")
        if any(f.op == "timeRange" for f in filters):
            engines_used.add("timescaledb")
        engines_used.add("postgres")
        for eng in engines_used:
            evidence.record(f"filter:{eng}", eng, time.monotonic() - t0, len(results))
        return results

    def _compile_attr_pred(self, f: Filter, pfx: str) -> tuple[str, dict[str, Any]]:
        """编译属性 filter 为 SQL 谓词片段（参数化）。

        object_state 存 backing_column key，所以 field 需 api_name→backing_column
        翻译后再与 os.properties->> 匹配（core.property_mapping）。
        """
        field_param = f"{pfx}_field"
        # api_name → backing_column（object_state JSONB key）。未加载本体时
        # 透传（合成 OT / 测试）。
        field = self._field_to_backing_flat(f.field)
        if f.op == "isNull":
            return f"(os.properties ->> :{field_param}) IS NULL", {field_param: field}
        if f.op == "isNotNull":
            return f"(os.properties ->> :{field_param}) IS NOT NULL", {field_param: field}
        if f.op == "exactMatch":
            v = f"{pfx}_val"
            return f"(os.properties ->> :{field_param}) = :{v}", {field_param: field, v: str(f.value)}
        if f.op == "notEqual":
            v = f"{pfx}_val"
            not_null = f"(os.properties ->> :{field_param}) IS NULL"
            return (
                f"(os.properties ->> :{field_param}) != :{v} OR {not_null}",
                {field_param: field, v: str(f.value)},
            )
        if f.op == "in":
            v = f"{pfx}_vals"
            return (
                f"(os.properties ->> :{field_param}) = ANY(:{v})",
                {field_param: field, v: [str(x) for x in (f.value or [])]},
            )
        if f.op == "notIn":
            v = f"{pfx}_vals"
            not_null = f"(os.properties ->> :{field_param}) IS NULL"
            return (
                f"((os.properties ->> :{field_param}) != ALL(:{v}) OR {not_null})",
                {field_param: field, v: [str(x) for x in (f.value or [])]},
            )
        if f.op == "greaterThan":
            v = f"{pfx}_val"
            return f"(os.properties ->> :{field_param})::numeric > :{v}", {field_param: field, v: f.value}
        if f.op == "lessThan":
            v = f"{pfx}_val"
            return f"(os.properties ->> :{field_param})::numeric < :{v}", {field_param: field, v: f.value}
        if f.op == "contains":
            v = f"{pfx}_val"
            return f"(os.properties ->> :{field_param}) LIKE :{v}", {field_param: field, v: f"%{f.value}%"}
        if f.op == "startsWith":
            v = f"{pfx}_val"
            return f"(os.properties ->> :{field_param}) LIKE :{v}", {field_param: field, v: f"{f.value}%"}
        if f.op == "endsWith":
            v = f"{pfx}_val"
            return f"(os.properties ->> :{field_param}) LIKE :{v}", {field_param: field, v: f"%{f.value}"}
        if f.op == "range":
            rv = f.value or {}
            mn, mx = rv.get("min"), rv.get("max")
            parts: list[str] = []
            params: dict[str, Any] = {field_param: field}
            if mn is not None:
                mn_p = f"{pfx}_mn"
                parts.append(f"(os.properties ->> :{field_param})::numeric >= :{mn_p}")
                params[mn_p] = mn
            if mx is not None:
                mx_p = f"{pfx}_mx"
                parts.append(f"(os.properties ->> :{field_param})::numeric <= :{mx_p}")
                params[mx_p] = mx
            return " AND ".join(parts) if parts else "TRUE", params
        return "TRUE", {}

    @staticmethod
    def _compile_spatial_pred(spatial: SpatialFilter, alias: str, pfx: str, ot: str) -> tuple[str, dict[str, Any]]:
        """编译空间 filter 为 SQL 谓词（PostGIS，参数化）。geo 表可能无匹配行（LEFT JOIN）。"""
        if spatial.op == "withinDistance" and spatial.center and spatial.max_distance is not None:
            lon_p, lat_p, dist_p = f"{pfx}_lon", f"{pfx}_lat", f"{pfx}_dist"
            return (
                f"({alias}.rid IS NOT NULL AND ST_DWithin({alias}.location, "
                f"ST_MakePoint(:{lon_p}, :{lat_p})::geography, :{dist_p}))",
                {lon_p: spatial.center[0], lat_p: spatial.center[1], dist_p: spatial.max_distance},
            )
        if spatial.op == "withinPolygon" and spatial.coords:
            pts = ", ".join(f"{c[0]} {c[1]}" for c in spatial.coords)
            wkt = f"POLYGON(({pts}, {spatial.coords[0][0]} {spatial.coords[0][1]}))"
            wkt_p = f"{pfx}_wkt"
            return (
                f"({alias}.rid IS NOT NULL AND ST_Covers(ST_GeogFromText(:{wkt_p}), {alias}.location))",
                {wkt_p: wkt},
            )
        if spatial.op == "withinBoundingBox" and spatial.coords:
            minlon, minlat = spatial.coords[0]
            maxlon, maxlat = spatial.coords[1]
            wkt = (
                f"POLYGON(({minlon} {minlat}, {maxlon} {minlat}, "
                f"{maxlon} {maxlat}, {minlon} {maxlat}, {minlon} {minlat}))"
            )
            wkt_p = f"{pfx}_wkt"
            return (
                f"({alias}.rid IS NOT NULL AND ST_Covers(ST_GeogFromText(:{wkt_p}), {alias}.location))",
                {wkt_p: wkt},
            )
        return "TRUE", {}

    def _compile_time_pred(self, f: Filter, pfx: str) -> tuple[str, dict[str, Any]]:
        """编译时序 filter 为 SQL 谓词（timestamptz cast，参数化）。

        object_state 存 backing_column key，field 需 api_name→backing_column
        翻译（core.property_mapping）。
        """
        field_param = f"{pfx}_field"
        field = self._field_to_backing_flat(f.field)
        tv = f.value or {}
        start, end = tv.get("start"), tv.get("end")
        parts: list[str] = []
        params: dict[str, Any] = {field_param: field}
        if start is not None:
            s_p = f"{pfx}_start"
            # createdAt 存的是 ms timestamp，to_timestamp 接受秒，需 /1000
            parts.append(f"to_timestamp((os.properties ->> :{field_param})::numeric / 1000) >= :{s_p}")
            params[s_p] = DataFrameQueryService._to_datetime(start)
        if end is not None:
            e_p = f"{pfx}_end"
            parts.append(f"to_timestamp((os.properties ->> :{field_param})::numeric / 1000) <= :{e_p}")
            params[e_p] = DataFrameQueryService._to_datetime(end)
        return " AND ".join(parts) if parts else "TRUE", params

    @staticmethod
    def _to_datetime(v: Any) -> Any:
        """把时间值转为 datetime（asyncpg timestamptz 参数要求 datetime）。

        兼容数值 timestamp（ms）和 ISO 字符串。已是 datetime 则原样返回。
        """
        from datetime import datetime

        if isinstance(v, datetime):
            return v
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=UTC)
        s = str(v).strip()
        try:
            return datetime.fromtimestamp(float(s) / 1000, tz=UTC)
        except ValueError:
            return datetime.fromisoformat(s)

    async def _apply_single_filter(
        self,
        f: Filter,
        candidate_rids: list[str],
        ontology_api_name: str,
        evidence: EvidenceChain,
    ) -> list[str]:
        """单个 filter：按 op 分流到 PG 属性/空间/时序查询。"""

        # 空间算子 → PostGIS。
        if f.op in ("withinDistance", "withinPolygon", "withinBoundingBox"):
            return await self._spatial_filter(f, candidate_rids, ontology_api_name, evidence)

        # 时序算子 → TimescaleDB。
        if f.op == "timeRange":
            return await self._time_range_filter(f, candidate_rids, ontology_api_name, evidence)

        # 属性算子（exactMatch/range/contains/isNull/isNotNull）→ PG object_state JSONB。
        # 用参数化 SQL 下推过滤（替代内存过滤，利用 GIN 索引，大规模友好）。
        return await self._attr_filter_pg(f, candidate_rids, evidence)

    async def _attr_filter_pg(self, f: Filter, candidate_rids: list[str], evidence: EvidenceChain) -> list[str]:
        """属性过滤走 PG object_state JSONB 参数化 SQL（替代内存过滤）。

        object_state.properties 是 JSONB，用 ->> 取字段值比较。候选 rid 集用
        ANY(:rids) 传递，分批避免超大 IN（R2）。对齐 CLAUDE.md 规范 8（映射表
        查表 + 参数化绑定），不手写转义。``self._attr_engine`` 为 None 时回退
        内存过滤（单测用）。
        """
        import time

        # 无 engine 兄底：内存过滤（单测路径）。
        if self._attr_engine is None:
            t0 = time.monotonic()
            states = await self._metadata.get_object_states_by_rids(candidate_rids)
            matched = [s["rid"] for s in states if self._match_attr(f, self._state_props_to_api(s).get(f.field))]
            evidence.record(f"filter:{f.op}", "postgres", time.monotonic() - t0, len(matched))
            return matched

        from sqlalchemy import text

        t0 = time.monotonic()
        # JSONB 谓词构建（field 名已白名单校验为本体 property api_name，值参数化）。
        # object_state 存 backing_column key，所以 field 需 api_name→backing_column
        # 翻译后再与 properties->> 匹配（core.property_mapping）。
        field = self._field_to_backing_flat(f.field)
        if f.op == "isNull":
            pred = "(properties ->> :field IS NULL)"
            params: dict[str, Any] = {"field": field}
        elif f.op == "isNotNull":
            pred = "(properties ->> :field IS NOT NULL)"
            params = {"field": field}
        elif f.op == "exactMatch":
            pred = "(properties ->> :field = :value)"
            params = {"field": field, "value": str(f.value)}
        elif f.op == "contains":
            pred = "(properties ->> :field LIKE :value)"
            params = {"field": field, "value": f"%{f.value}%"}
        elif f.op == "range":
            v = f.value or {}
            mn, mx = v.get("min"), v.get("max")
            # range 用 ::numeric 强制数值比较（->> 取文本值，字符串比较会让 "9" > "100"）
            parts: list[str] = []
            params = {"field": field}
            if mn is not None:
                parts.append("(properties ->> :field)::numeric >= :mn")
                params["mn"] = mn
            if mx is not None:
                parts.append("(properties ->> :field)::numeric <= :mx")
                params["mx"] = mx
            pred = " AND ".join(parts) if parts else "TRUE"
        else:
            # 未知算子兑底：内存过滤。
            states = await self._metadata.get_object_states_by_rids(candidate_rids)
            matched = [s["rid"] for s in states if self._match_attr(f, self._state_props_to_api(s).get(f.field))]
            evidence.record(f"filter:{f.op}", "postgres", time.monotonic() - t0, len(matched))
            return matched

        # 分批查询（R2: PG IN >3000 性能劣化）。
        results: list[str] = []
        batch_size = settings.rid_batch_size
        async with self._attr_engine.connect() as conn:
            for i in range(0, len(candidate_rids), batch_size):
                batch = candidate_rids[i : i + batch_size]
                sql = f"SELECT rid FROM object_state WHERE rid = ANY(:rids) AND {pred}"
                params["rids"] = batch
                res = await conn.execute(text(sql), params)
                for row in res:
                    results.append(str(row[0]))
        evidence.record(f"filter:{f.op}", "postgres", time.monotonic() - t0, len(results))
        return results

    async def _spatial_filter(
        self, f: Filter, candidate_rids: list[str], ontology_api_name: str, evidence: EvidenceChain
    ) -> list[str]:
        """空间过滤走 PostGIS（GiST 索引）。需定位对象的 geo 表。"""
        import time

        t0 = time.monotonic()
        # 候选 rid 的对象类型未知（图遍历可能跨类型），MVP：遍历所有 geo 表。
        # 优化期：从 IR 上下文拿 object_type，直接定位 geo 表。
        # 这里用候选 rid 查 object_state 拿 object_type，再定位 geo 表。
        states = await self._metadata.get_object_states_by_rids(candidate_rids)
        # 按 object_type 分组，每组查对应 geo 表。
        from ontology.core.naming import geo_table

        result_rids: list[str] = []
        type_to_rids: dict[str, list[str]] = {}
        for s in states:
            ot = s.get("object_type_api_name", "")
            type_to_rids.setdefault(ot, []).append(s["rid"])

        spatial = self._build_spatial_filter(f)
        for ot, rids in type_to_rids.items():
            table = geo_table(ontology_api_name, ot)
            if not await self._geotime.table_exists(table):
                continue  # 非空间对象无 geo 表
            hits = await self._geotime.spatial_filter(table, rids, spatial)
            result_rids.extend(hits)
        evidence.record(f"filter:{f.op}", "postgis", time.monotonic() - t0, len(result_rids))
        return result_rids

    async def _time_range_filter(
        self, f: Filter, candidate_rids: list[str], ontology_api_name: str, evidence: EvidenceChain
    ) -> list[str]:
        """时序过滤：按 properties 里的时间字段过滤。

        timeRange filter 的 field 是时间属性 api_name（如 createdAt/timestamp），
        value={start,end}。把 properties->>field 当时间值比较。
        优先用 timestamptz cast（ISO 字符串），失败回退内存过滤。
        注：完整 TimescaleDB 超表集成需 series_id↔rid 映射，当前用属性时间字段近似
        （与前端 TimeScrubber 按节点 props 时间戳过滤一致）。
        """
        import logging
        import time as time_mod

        _log = logging.getLogger(__name__)
        t0 = time_mod.monotonic()
        v = f.value or {}
        start, end = v.get("start"), v.get("end")
        if start is None and end is None:
            evidence.record("filter:timeRange", "postgres", time_mod.monotonic() - t0, len(candidate_rids))
            return candidate_rids

        # 无 engine 兑底：内存过滤。
        if self._attr_engine is None:
            states = await self._metadata.get_object_states_by_rids(candidate_rids)
            matched = [s["rid"] for s in states if self._match_time(f.field, self._state_props_to_api(s), start, end)]
            evidence.record("filter:timeRange", "postgres", time_mod.monotonic() - t0, len(matched))
            return matched

        from sqlalchemy import text

        # object_state 存 backing_column key；field api_name→backing_column 翻译。
        parts: list[str] = []
        params: dict[str, Any] = {"field": self._field_to_backing_flat(f.field)}
        if start is not None:
            parts.append("(properties ->> :field)::timestamptz >= :ts_start")
            params["ts_start"] = start
        if end is not None:
            parts.append("(properties ->> :field)::timestamptz <= :ts_end")
            params["ts_end"] = end
        pred = " AND ".join(parts)

        results: list[str] = []
        batch_size = settings.rid_batch_size
        try:
            async with self._attr_engine.connect() as conn:
                for i in range(0, len(candidate_rids), batch_size):
                    batch = candidate_rids[i : i + batch_size]
                    sql = f"SELECT rid FROM object_state WHERE rid = ANY(:rids) AND {pred}"
                    params["rids"] = batch
                    res = await conn.execute(text(sql), params)
                    for row in res:
                        results.append(str(row[0]))
            evidence.record("filter:timeRange", "postgres", time_mod.monotonic() - t0, len(results))
            return results
        except Exception as exc:
            # timestamptz cast 失败（非时间格式）回退内存过滤
            _log.warning("timeRange timestamptz cast failed, fallback to memory: %s", exc)
            states = await self._metadata.get_object_states_by_rids(candidate_rids)
            matched = [s["rid"] for s in states if self._match_time(f.field, self._state_props_to_api(s), start, end)]
            evidence.record("filter:timeRange", "postgres", time_mod.monotonic() - t0, len(matched))
            return matched

    async def _sort_rids(self, rids: list[str], order_by: list[dict[str, Any]], ontology_api_name: str) -> list[str]:
        """按 order_by 字段排序 rid 集（保证 cursor 分页稳定性）。

        order_by: [{"field": "createdAt", "desc": false}, ...]
        用 object_state.properties 取字段值，内存排序（rids 已是结果集，规模可控）。
        """
        if not rids:
            return rids
        states = await self._metadata.get_object_states_by_rids(rids)
        # object_state 存 backing_column；转为 api_name 以匹配 order_by.field（语义层）。
        state_map = {s["rid"]: self._state_props_to_api(s) for s in states}

        # 多字段排序：逐字段应用 desc（stable sort，从最后一个字段开始）
        result = list(rids)
        for ob in reversed(order_by):
            field = ob.get("field", "")
            desc = ob.get("desc", False)

            def make_key(f: str) -> Any:
                def key(v: str) -> tuple[bool, Any]:
                    val = state_map.get(v, {}).get(f)
                    return (val is None, val if val is not None else "")

                return key

            result.sort(key=make_key(field), reverse=desc)
        return result

    async def _execute_aggregate(self, ir: ObjectSetIR, ontology_api_name: str) -> ReasoningResult:
        """聚合查询：求值子集 rid → 水合 → 内存 group_by + 聚合。

        aggregate 不返回对象集，返回分组聚合结果（aggregates）。
        支持 func: count/sum/avg/min/max。group_by 可空（全局聚合）。
        """
        import time

        evidence = EvidenceChain()
        t0 = time.monotonic()
        # 求值子集 rid
        rids = await self._eval_object_set(ir.object_set, ontology_api_name, evidence)  # type: ignore[arg-type]
        # 水合全量属性（聚合需要属性值）
        objects = await self._hydrate(rids)

        group_by = ir.group_by or []
        aggs = ir.aggregations or []

        # 分组
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
        for obj in objects:
            props = obj.get("props", {})
            key = tuple(props.get(g) for g in group_by)
            groups.setdefault(key, []).append(props)

        # 聚合
        aggregate_results: list[dict[str, Any]] = []
        for key, group_props in groups.items():
            row: dict[str, Any] = {"group": {g: k for g, k in zip(group_by, key)}}
            agg_vals: dict[str, Any] = {}
            for a in aggs:
                func = a.get("func", "count")
                field = a.get("field", "")
                alias = a.get("alias") or f"{func}_{field}"
                values = [p.get(field) for p in group_props if p.get(field) is not None]
                if func == "count":
                    agg_vals[alias] = len(group_props) if not field else len(values)
                elif func == "sum":
                    agg_vals[alias] = sum(v for v in values if isinstance(v, (int, float)))
                elif func == "avg":
                    nums = [v for v in values if isinstance(v, (int, float))]
                    agg_vals[alias] = sum(nums) / len(nums) if nums else None
                elif func == "min":
                    nums = [v for v in values if isinstance(v, (int, float))]
                    agg_vals[alias] = min(nums) if nums else None
                elif func == "max":
                    nums = [v for v in values if isinstance(v, (int, float))]
                    agg_vals[alias] = max(nums) if nums else None
            row["aggregates"] = agg_vals
            aggregate_results.append(row)

        evidence.record("aggregate", "memory", time.monotonic() - t0, len(aggregate_results))
        stats = {
            "steps": evidence.step_count,
            "engines_used": list(evidence.engines_used),
            "timings": evidence.timings,
            "total_rids": len(rids),
            "groups": len(aggregate_results),
        }
        return ReasoningResult(
            objects=[],  # aggregate 不返回对象集
            aggregates=aggregate_results,
            stats=stats,
        )

    async def _eval_interface_base(self, ir: ObjectSetIR, ontology_api_name: str, evidence: EvidenceChain) -> list[str]:
        """interfaceBase：跨类型起始集，返回所有实现某 Interface 的对象 rid。

        对齐 Palantir ObjectSetInterfaceBaseType。通过 ObjectType.extends_interface_ids
        查找实现该 Interface 的所有 ObjectType，合并它们的对象 rid。
        """
        import time

        t0 = time.monotonic()
        assert ir.interface is not None
        rids = await self._metadata.get_rids_by_interface(ontology_api_name, ir.interface)
        evidence.record("interfaceBase", "postgres", time.monotonic() - t0, len(rids))
        return rids

    async def _eval_interface_link_search_around(
        self,
        ir: ObjectSetIR,
        ontology_api_name: str,
        base_rids: list[str],
        evidence: EvidenceChain,
    ) -> list[str]:
        """interfaceLinkSearchAround：按 Interface 关系跨类型图遍历。

        对齐 Palantir ObjectSetInterfaceLinkSearchAroundType。与 searchAround 类似，
        但目标 label 是所有实现了该 Interface 的 ObjectType 的 graph label 集合。
        Neo4j 遍历时匹配多个 label（用 | 连接或通配）。
        """
        import time

        t0 = time.monotonic()
        assert ir.link is not None and ir.interface is not None
        rel_type = graph_relationship_type(ontology_api_name, ir.link)
        hops = ir.hops or (1, 3)
        direction = ir.direction or "both"

        # 查实现该 Interface 的所有 ObjectType → graph label 集合
        ot_names = await self._metadata.get_object_types_by_interface(ontology_api_name, ir.interface)
        labels = [graph_label(ontology_api_name, ot) for ot in ot_names]
        # Neo4j 多 label 用 | 连接（label1:Label2|Label3）
        target_label = "|".join(labels) if labels else ""

        if not target_label or not base_rids:
            evidence.record("interfaceLinkSearchAround", "neo4j", time.monotonic() - t0, 0)
            return []

        result = await self._graph.search_around(
            label=target_label,
            source_rids=base_rids,
            hops=hops,
            rel_types=[rel_type],
            direction=direction,
            limit=settings.graph_traversal_result_limit,
        )
        evidence.record("interfaceLinkSearchAround", "neo4j", time.monotonic() - t0, result.matched_count)
        if result.truncated:
            evidence.truncated = True
        return result.rids

    async def _eval_set_op(self, ir: ObjectSetIR, ontology_api_name: str, evidence: EvidenceChain) -> list[str]:
        """集合运算：union/intersect/subtract（对齐 Palantir ObjectSet + Ibis）。

        递归求值各子 object_set，按 op 做集合并/交/差。rid 用 set 运算
        （去重），保持顺序（第一个子集的顺序优先）。
        """
        import time

        t0 = time.monotonic()
        child_results: list[list[str]] = []
        for child in ir.object_sets or []:
            rids = await self._eval_object_set(child, ontology_api_name, evidence)
            child_results.append(rids)
        if not child_results:
            return []

        result: list[str] = []
        if ir.type == "union":
            seen: set[str] = set()
            for rids in child_results:
                for v in rids:
                    if v not in seen:
                        seen.add(v)
                        result.append(v)
        elif ir.type == "intersect":
            sets = [set(rids) for rids in child_results]
            common = set.intersection(*sets) if sets else set()
            # 保持第一个子集的顺序
            result = [v for v in child_results[0] if v in common]
        elif ir.type == "subtract":
            base = child_results[0]
            rest: set[str] = set()
            for rids in child_results[1:]:
                rest.update(rids)
            result = [v for v in base if v not in rest]
        evidence.record(f"setOp:{ir.type}", "memory", time.monotonic() - t0, len(result))
        return result

    async def _eval_search_around(
        self,
        ir: ObjectSetIR,
        ontology_api_name: str,
        base_rids: list[str],
        evidence: EvidenceChain,
    ) -> list[str]:
        """图遍历走 Neo4j（C9 原生 Cypher）。"""
        import time

        t0 = time.monotonic()
        # 目标 label：从 link_type 解析两端 ObjectType。MVP：遍历到任意 label。
        # 优化期：从 LinkType 元数据拿目标 ObjectType，定位 label。
        rel_type = graph_relationship_type(ontology_api_name, ir.link)  # type: ignore[arg-type]
        hops = ir.hops or (1, 3)
        direction = ir.direction or "both"

        # base_rids 是源 rid。图遍历返回目标 rid。
        # Neo4j search_around 需要目标 label；MVP 用通配（label 传空则匹配任意）。
        # 但 search_around 接口要求 label 参数。这里从 LinkType 元数据拿目标类型。
        target_label = await self._resolve_target_label(ontology_api_name, ir.link)  # type: ignore[arg-type]

        result = await self._graph.search_around(
            label=target_label,
            source_rids=base_rids,
            hops=hops,
            rel_types=[rel_type],
            direction=direction,
            limit=settings.graph_traversal_result_limit,
        )
        evidence.record("searchAround", "neo4j", time.monotonic() - t0, result.matched_count)
        if result.truncated:
            evidence.truncated = True
        # 累积边三元组（用 IR.link 作为 link_type，覆盖 Neo4j rel_type ——
        # 前端按 LinkType api_name 渲染边样式，rel_type 是物理名）。
        for edge in result.edges:
            evidence.edges.append(
                {
                    "source_rid": edge.source_rid,
                    "target_rid": edge.target_rid,
                    "link_type": ir.link,
                    "direction": direction,
                }
            )
        return result.rids

    async def _resolve_link_endpoint_ot(
        self, ontology_api_name: str, link_api_name: str, endpoint: str
    ) -> ObjectType | None:
        """Resolve the source or target ObjectType of a LinkType.

        Shared by ``_resolve_source_label`` / ``_resolve_target_label`` and by
        the pk→rid translation layer (which needs the endpoint's
        ``primary_key`` to resolve business keys to rids).

        Args:
            endpoint: "source" or "target".
        """
        links = await self._metadata.get_link_types(ontology_api_name)
        link = next((lt for lt in links if lt.api_name == link_api_name), None)
        if link is None:
            return None
        ots = await self._metadata.list_object_types(ontology_api_name)
        endpoint_id = link.source_object_type_id if endpoint == "source" else link.target_object_type_id
        ot = next((ot for ot in ots if ot.id == endpoint_id), None)
        return ot

    async def _resolve_target_label(self, ontology_api_name: str, link_api_name: str) -> str:
        """从 LinkType 元数据解析目标 ObjectType，生成 graph label。"""
        ot = await self._resolve_link_endpoint_ot(ontology_api_name, link_api_name, "target")
        return graph_label(ontology_api_name, ot.api_name) if ot else ""

    async def _resolve_source_label(self, ontology_api_name: str, link_api_name: str) -> str:
        """从 LinkType 元数据解析源 ObjectType，生成 graph label。

        用于 exists_link 的 source 端 label 解析（与 target 对称）。
        """
        ot = await self._resolve_link_endpoint_ot(ontology_api_name, link_api_name, "source")
        return graph_label(ontology_api_name, ot.api_name) if ot else ""

    async def _resolve_rids_by_pk(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        pks: list[str],
    ) -> list[str]:
        """Translate business primary-key values to internal rids.

        This is the API-boundary translation layer that keeps ``rid``
        (``object_state.rid``, a system UUID) from leaking to Agents.
        Agents address objects by ``primary_key`` value (e.g. ``"S001"``);
        this method resolves those to the rids that the graph engine
        (Neo4j) and ``object_links`` table actually key on.

        Resolution uses ``object_state`` with a JSONB equality filter on the
        ObjectType's ``primary_key`` property. Unknown pks raise
        ``NotFoundError`` with the offending values so the Agent gets a
        readable message rather than a silent empty result.

        Args:
            pks: business primary-key values (order preserved in return).

        Returns:
            rids corresponding 1:1 to ``pks`` (same length, same order).

        Raises:
            NotFoundError: if any pk has no object_state row.
        """
        if not pks:
            return []
        ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        pk_field = ot.primary_key
        # JSONB equality: properties->>'<pk_field>' = '<value>'. Reuse the
        # query_object_states filter pattern (text cast covers str/int). Batch
        # via OR to keep this a single round-trip.
        from sqlalchemy import or_
        from sqlalchemy import select as sa_select

        from ontology.core.models.ontology import ObjectStateModel

        stmt = sa_select(ObjectStateModel.rid, ObjectStateModel.properties).where(
            ObjectStateModel.ontology_api_name == ontology_api_name,
            ObjectStateModel.object_type_api_name == object_type_api_name,
            or_(*[ObjectStateModel.properties[pk_field].as_string() == str(pk) for pk in pks]),
        )
        result = await self._metadata._session.execute(stmt)
        rows = result.all()
        # pk → rid map (last writer wins on dup, shouldn't happen with unique pk).
        pk_to_rid: dict[str, str] = {}
        for rid, props in rows:
            pk_val = str(props.get(pk_field)) if props else None
            if pk_val is not None:
                pk_to_rid[pk_val] = str(rid)
        missing = [pk for pk in pks if str(pk) not in pk_to_rid]
        if missing:
            raise NotFoundError(
                f"{object_type_api_name} by {pk_field}",
                ", ".join(missing),
            )
        return [pk_to_rid[str(pk)] for pk in pks]

    async def _resolve_rid_by_pk_any_type(self, ontology_api_name: str, pk: str) -> str:
        """Resolve a pk to rid when the ObjectType is unknown.

        Used by ``find_paths`` when no ``link_types`` are given (so neither
        endpoint ObjectType is known). Scans all ObjectTypes in the ontology
        for an object whose ``primary_key`` property matches. Assumes pks are
        unique across types within an ontology (the common case); if multiple
        types match, the first hit wins.

        Raises ``NotFoundError`` if no object has this pk.
        """
        from sqlalchemy import or_
        from sqlalchemy import select as sa_select

        from ontology.core.models.ontology import ObjectStateModel

        ots = await self._metadata.list_object_types(ontology_api_name)
        clauses = []
        for ot in ots:
            if ot.primary_key:
                clauses.append(
                    (ObjectStateModel.object_type_api_name == ot.api_name)
                    & (ObjectStateModel.properties[ot.primary_key].as_string() == str(pk))
                )
        if not clauses:
            raise NotFoundError(f"object with pk {pk} in ontology {ontology_api_name}", pk)
        stmt = sa_select(ObjectStateModel.rid).where(
            ObjectStateModel.ontology_api_name == ontology_api_name,
            or_(*clauses),
        )
        result = await self._metadata._session.execute(stmt)
        row = result.first()
        if row is None:
            raise NotFoundError(f"object with pk {pk} in ontology {ontology_api_name}", pk)
        return str(row[0])

    async def _hydrate(self, rids: list[str], select_fields: list[str] | None = None) -> list[dict[str, Any]]:
        """水合全量属性（C12）。按 RID 类型分流（handoff §3.4）。

        - MANAGED rid（``ri.ontology.main.object.*``）：走 PG ``object_state``
          批量取（MVP 实现）。未来切 Doris 主源点查（ADR-001，handoff §3.4
          注：需 Doris idx 表加 rid 列，独立架构工作，不在本 PR 范围）。
        - VIRTUAL rid（``ri.ontology.main.virtual-object.*``）：解析 locator
          得 (ont, ot, pk) → ``ObjectQueryService.hydrate_by_pk`` 走 Trino
          跨 catalog 联邦查外部源表（ADR-014）。

        保持 rids 传入顺序（order_by 排序后需保持）。
        select_fields 非空时只投影这些字段（减少响应大小）。

        object_state 存 backing_column key；出口转为 api_name（语义层）。
        """
        if not rids:
            return []
        managed_rids = [r for r in rids if is_managed_rid(r)]
        virtual_rids = [r for r in rids if is_virtual_rid(r)]
        # 未识别为 RID 的（裸 UUID 兼容旧数据）按 MANAGED 处理。
        legacy_rids = [r for r in rids if not is_managed_rid(r) and not is_virtual_rid(r)]
        managed_rids.extend(legacy_rids)

        objects: list[dict[str, Any]] = []

        # MANAGED：PG object_state 批量取（MVP，未来切 Doris）
        if managed_rids:
            objects.extend(await self._hydrate_managed(managed_rids, select_fields))

        # VIRTUAL：解析 PK → ObjectQueryService.hydrate_by_pk（Trino 联邦）
        if virtual_rids:
            objects.extend(await self._hydrate_virtual(virtual_rids, select_fields))

        # 按传入 rids 顺序重排（分流后顺序丢失）
        order = {o["rid"]: i for i, o in enumerate(objects)}
        objects.sort(key=lambda o: order.get(o["rid"], 0))
        return objects

    async def _hydrate_managed(self, rids: list[str], select_fields: list[str] | None) -> list[dict[str, Any]]:
        """MANAGED rid 水合：PG object_state 批量取（MVP，未来切 Doris 主源）。"""
        states = await self._metadata.get_object_states_by_rids(rids)
        state_map = {s["rid"]: s for s in states}
        objects: list[dict[str, Any]] = []
        for rid in rids:
            s = state_map.get(rid)
            if s is None:
                continue
            props = self._state_props_to_api(s)
            if select_fields:
                props = {k: props.get(k) for k in select_fields if k in props}
            objects.append(
                {
                    "rid": s["rid"],
                    "api_name": s.get("object_type_api_name", ""),
                    "props": props,
                }
            )
        return objects

    async def _hydrate_virtual(self, rids: list[str], select_fields: list[str] | None) -> list[dict[str, Any]]:
        """VIRTUAL rid 批量水合：按 (ont, ot) 分组 → hydrate_by_pks（§7.7，PR 5a）。

        locator 格式 ``{ont}.{ot}.{pk}``（``parse_virtual_rid_pk``）。同一 OT
        的 PK 批量查（``WHERE pk IN (...)`` 分批 1000），避免 N+1 反模式。
        多 OT 串行查询（避免外部源并发风暴）。单 OT 组失败标 _partial 不
        阻塞其他组（对齐 ADR-021 §2.8 + C9 包容式防线）。
        """
        if self._object_query is None:
            _log.warning(
                "VIRTUAL rid 水合跳过：ObjectQueryService 未注入（%d rid）",
                len(rids),
            )
            return []
        # 1. 解析 rid → (ont, ot, pk)，按 (ont, ot) 分组。
        groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for rid in rids:
            try:
                ont, ot, pk = parse_virtual_rid_pk(rid)
            except ValueError:
                _log.warning("VIRTUAL rid 解析失败，跳过：%r", rid)
                continue
            groups.setdefault((ont, ot), []).append((pk, rid))
        if not groups:
            return []

        objects: list[dict[str, Any]] = []
        # 2. 按 (ont, ot) 分组串行查（避免外部源并发风暴）。
        for (ont, ot_api), pk_rid_pairs in groups.items():
            pks = [pk for pk, _ in pk_rid_pairs]
            rid_by_pk: dict[str, str] = {pk: rid for pk, rid in pk_rid_pairs}
            try:
                ot_obj = await self._metadata.get_object_type(ont, ot_api)
                rows = await self._object_query.hydrate_by_pks(
                    ont, ot_obj, pks, select_fields,
                )
            except Exception as exc:
                # ADR-021 §2.8：该 OT 组全部 _partial（不静默跳过，不阻塞其他组）。
                _log.warning(
                    "VIRTUAL 批量水合失败（%s.%s，%d pk）：%s",
                    ont, ot_api, len(pks), exc,
                )
                for pk, rid in pk_rid_pairs:
                    objects.append({
                        "rid": rid,
                        "api_name": ot_api,
                        "props": {},
                        "_partial": True,
                        "_error": "source unavailable",
                    })
                continue
            # 3. 按 pk 回填 rid + select_fields 投影。
            pk_api = getattr(ot_obj, "primary_key", "") or "id"
            for row in rows:
                pk_val = str(row.get(pk_api, ""))
                matched_rid: str | None = rid_by_pk.get(pk_val)
                if matched_rid is None:
                    # pk 不在输入集（理论不发生，防御）。
                    continue
                props = dict(row)
                if select_fields:
                    props = {k: props.get(k) for k in select_fields if k in props}
                objects.append({"rid": matched_rid, "api_name": ot_api, "props": props})
            # 4. 查不到的 pk（源表无此行）→ 跳过（对齐原逻辑 data is None: continue）。
        return objects

    @staticmethod
    def _build_spatial_filter(f: Filter) -> SpatialFilter:
        """Filter → SpatialFilter。"""
        return SpatialFilter(
            op=f.op,  # type: ignore[arg-type]
            coords=f.coords,
            center=f.center,
            max_distance=f.max_distance,
        )

    @staticmethod
    def _match_attr(f: Filter, value: Any) -> bool:
        """属性算子内存匹配。"""
        if f.op == "isNull":
            return value is None
        if f.op == "isNotNull":
            return value is not None
        if f.op == "exactMatch":
            return bool(value == f.value)
        if f.op == "notEqual":
            return bool(value != f.value)
        if f.op == "in":
            return bool(value in (f.value or []))
        if f.op == "notIn":
            return bool(value not in (f.value or []))
        if f.op == "greaterThan":
            return bool(value is not None and value > f.value)
        if f.op == "lessThan":
            return bool(value is not None and value < f.value)
        if f.op == "contains":
            return bool(f.value in value) if value is not None else False
        if f.op == "startsWith":
            return bool(str(value).startswith(str(f.value))) if value is not None else False
        if f.op == "endsWith":
            return bool(str(value).endswith(str(f.value))) if value is not None else False
        if f.op == "range":
            v = f.value or {}
            mn, mx = v.get("min"), v.get("max")
            if mn is not None and (value is None or value < mn):
                return False
            if mx is not None and (value is None or value > mx):
                return False
            return True
        return False

    @staticmethod
    def _match_time(field: str, props: dict[str, Any], start: Any, end: Any) -> bool:
        """timeRange 内存匹配：props[field] 在 [start, end] 内。

        兼容数值 timestamp（ms）和 ISO 字符串两种时间格式。
        """
        value = props.get(field)
        if value is None:
            return False

        # 统一转数值比较（ms timestamp）
        def to_ms(v: Any) -> float:
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip()
            try:
                return float(s)  # 纯数字 timestamp
            except ValueError:
                from datetime import datetime

                return float(datetime.fromisoformat(s).timestamp() * 1000)

        try:
            v_ms = to_ms(value)
            if start is not None and v_ms < to_ms(start):
                return False
            if end is not None and v_ms > to_ms(end):
                return False
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _match_filter_memory(f: Filter, props: dict[str, Any]) -> bool:
        """纯内存 filter 匹配（属性 + 时序，SQL 兑底用）。空间无 PostGIS 无法匹配。"""
        if f.op in ATTR_OPS:
            return DataFrameQueryService._match_attr(f, props.get(f.field))
        if f.op == "timeRange":
            v = f.value or {}
            return DataFrameQueryService._match_time(f.field, props, v.get("start"), v.get("end"))
        # 空间算子无 PostGIS 无法内存匹配，保守返回 True（不过滤）
        return True


class EvidenceChain:
    """证据链累积器（M6 证据链快照用）。MVP 记录各步引擎/耗时/命中数。

    同时累积 searchAround 产生的边三元组（ADR-015 探索轨迹），
    execute() 末尾汇总进 ReasoningResult.edges 供画布渲染箭头。"""

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self.engines_used: set[str] = set()
        self.timings: dict[str, float] = {}
        self.truncated: bool = False
        # searchAround 产生的边（source_rid/target_rid/link_type/direction）。
        # 多步 searchAround 串联时累积，形成完整探索轨迹。
        self.edges: list[dict[str, Any]] = []

    def record(self, step: str, engine: str, elapsed: float, count: int) -> None:
        self.steps.append({"step": step, "engine": engine, "elapsed": elapsed, "count": count})
        self.engines_used.add(engine)
        # timings 用 list 累积，避免同名 step（如多个 filter）互相覆盖
        self.timings.setdefault(step, 0.0)
        self.timings[step] = self.timings[step] + elapsed

    @property
    def step_count(self) -> int:
        return len(self.steps)
