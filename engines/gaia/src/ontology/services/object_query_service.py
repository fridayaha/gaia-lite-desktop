"""ObjectQueryService — core query routing engine.

Routes based on ObjectType.storage_type:
  MANAGED → Doris index filter + Iceberg point lookup (Trino fallback)
  VIRTUAL → Trino View query
"""

import logging
from typing import TYPE_CHECKING, Any, cast

from ontology.core.exceptions import (
    DorisUnavailableError,
    ForbiddenError,
    NotFoundError,
    OntologyError,
)
from ontology.core.permission_roles import OP_OBJECT_VIEW
from ontology.core.schemas.ontology import ObjectType
from ontology.core.schemas.query import (
    AggregationRequest,
    QueryFilter,
)
from ontology.config.settings import settings
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.observability.metrics import (
    object_query_fallback_total,
    object_query_index_hit_total,
)
from ontology.services._metadata_owner import MetadataOwnerMixin
from ontology.services.textql.sql_compiler import OntologySqlCompiler

if TYPE_CHECKING:
    # IcebergStore 仅类型注解；顶层 import 会传递性拉 pyiceberg 重依赖，lite 版装
    # 不上。移入 TYPE_CHECKING 使本模块在 lite 下可 import（A3）。engine 按
    # QueryEngine 契约注解（Trino/DuckDB 共实现，B2）。
    from ontology.core.schemas.permission import Principal
    from ontology.layers.dataset.iceberg_store import IcebergStore
    from ontology.layers.engine.base import QueryEngine
    from ontology.services.authorization_service import AuthorizationService

logger = logging.getLogger(__name__)


class _UnsupportedFilterError(Exception):
    """Raised when a QueryFilter construct can't be expressed in the target SQL.

    Used internally to signal that the physical load path should fall back
    to Trino (or / search_around). Carries the unsupported node type for
    diagnostics.
    """

    def __init__(self, node_type: str) -> None:
        super().__init__(f"unsupported filter node for Doris: {node_type}")
        self.node_type = node_type


class ObjectQueryService(MetadataOwnerMixin):
    """Core query orchestration with multi-path routing and fallback."""

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry | None,
        index: DorisIndexStore | None,
        dataset: "IcebergStore | None",
        engine: "QueryEngine",
        authorization_service: "AuthorizationService | None" = None,
    ) -> None:
        self._metadata = metadata
        # catalog/index/dataset lite 装配传 None（lite VIRTUAL 走 DuckDB 不触达；
        # MANAGED 路径 _compile_and_run 已 guard 抛 EDITION_UNAVAILABLE）。存到 _val，
        # 下方 property 窄化回非 None 类型让 full 路径调用点 mypy 不报 union-attr；
        # lite 下访问这些 property 即走错路径，assert 兜底。
        self._catalog_val = catalog
        self._index_val = index
        self._dataset_val = dataset
        self._engine = engine
        self._authz = authorization_service

    @property
    def _catalog(self) -> GravitinoRegistry:
        assert self._catalog_val is not None, "catalog 未装配（lite 版不应触达 Gravitino 路径）"
        return self._catalog_val

    @property
    def _index(self) -> DorisIndexStore:
        assert self._index_val is not None, "index 未装配（lite 版不应触达 Doris 路径）"
        return self._index_val

    @property
    def _dataset(self) -> "IcebergStore":
        assert self._dataset_val is not None, "dataset 未装配（lite 版不应触达 Iceberg 路径）"
        return self._dataset_val

    async def aclose(self) -> None:
        """Close the underlying metadata session (return connection to pool).

        ObjectQueryService itself holds no dedicated session, but the
        PostgresMetaStore it is constructed with does (container.metadata
        creates a fresh AsyncSession per access). Callers that obtain this
        service via ``container.object_query_service`` (e.g. AG-UI toolsets)
        must close it to avoid leaking the session (D7).
        """
        try:
            await self._metadata.close()
        except Exception:
            pass

    async def aggregate_by_request(
        self, request: AggregationRequest, *, principal: "Principal | None" = None
    ) -> list[dict[str, object]]:
        """Aggregate via the TextQL compiler path (ADR-012 Step 4 path B).

        Entry point for POST /objects/aggregate. Constructs an aggregate
        logical SQL from AggregationRequest (property api_names in
        metrics/group_by/filter, all in api_name form), compiles via
        OntologySqlCompiler (column mapping + parameterized binding +
        dialect fork: MANAGED→Doris / VIRTUAL→Trino federation), executes.
        Aggregate aliases (count / sum_<prop> / ...) are kept as-is (no
        _map_backing_to_api — they are not OT properties).
        """
        object_type_api_name = request.object_set.object_type_api_name
        ot, _ = await self._resolve_query_target(object_type_api_name, principal=principal)
        ontology_api, type_api = object_type_api_name.split(".", 1)

        # Build aggregate logical SQL in api_name form.
        select_parts: list[str] = []
        if request.group_by:
            select_parts.extend(request.group_by)
        for m in request.metrics:
            func = m.func.lower()
            if func == "count":
                select_parts.append("COUNT(*) AS count")
            else:
                select_parts.append(f"{func.upper()}({m.field}) AS {func}_{m.field}")
        logical_sql = f"SELECT {', '.join(select_parts)} FROM {type_api}"
        if request.object_set.filter:
            where = self._filter_to_logical_sql(request.object_set.filter)
            if where:
                logical_sql += f" WHERE {where}"
        if request.group_by:
            logical_sql += " GROUP BY " + ", ".join(request.group_by)

        # ── Row-level pushdown (single-OT aggregate, design §4.1) ──
        residuals: list[str] = []
        masked: set[str] = set()
        if principal is not None and self._authz is not None:
            scope = await self._authz.evaluate_query_scope(principal, ontology_api, type_api)
            if scope.forbidden:
                return []  # 不可见即安全
            if scope.residual:
                residuals.append(scope.residual)
            masked.update(scope.masked_properties)
        rows = await self._compile_and_run(
            ot,
            ontology_api,
            logical_sql,
            residuals=residuals or None,
        )
        # Aggregate output uses aliases (count / sum_<prop>), not OT property
        # api_names — masking doesn't apply to aggregate columns.
        return rows

    @staticmethod
    def _filter_to_logical_sql(node: QueryFilter) -> str:
        """Render a QueryFilter tree to a logical-SQL WHERE fragment (api_name form).

        Uses api_names directly (the compiler maps them to physical columns
        downstream). This bypasses the legacy ``_filter_dict_to_sql`` (which
        had the D3 and/or format bug) — combinator semantics come from SQL
        syntax itself, and literals are inlined (the compiler parameterizes
        them). Supports eq/range/and/or; search_around returns "" (caller
        skips — aggregate doesn't support traversal).
        """
        t = node.type
        if t == "and":
            kids = [ObjectQueryService._filter_to_logical_sql(c) for c in (node.filters or [])]
            kids = [k for k in kids if k]
            return "(" + " AND ".join(kids) + ")" if kids else ""
        if t == "or":
            kids = [ObjectQueryService._filter_to_logical_sql(c) for c in (node.filters or [])]
            kids = [k for k in kids if k]
            return "(" + " OR ".join(kids) + ")" if kids else ""
        if t == "eq" and node.field:
            return f"{node.field} = '{node.value}'"
        if t == "range" and node.field:
            parts = []
            if node.min is not None:
                parts.append(f"{node.field} >= '{node.min}'")
            if node.max is not None:
                parts.append(f"{node.field} <= '{node.max}'")
            return " AND ".join(parts) if parts else ""
        # search_around / unknown — aggregate doesn't support traversal.
        return ""

    async def execute_compiled_sql(
        self,
        ontology_api_name: str,
        logical_sql: str,
        compiler: OntologySqlCompiler | None = None,
        *,
        principal: "Principal | None" = None,
    ) -> list[dict[str, object]]:
        """Execute a text2sql-compiled query (ADR-012 Step 4 path B).

        Compiles ``logical_sql`` (ObjectType api_name as table, property
        api_name as column) to physical Doris SQL via ``compiler`` (with
        ontology guardrails), runs it on Doris, and falls back to Trino
        (recompiled to Trino dialect) when Doris is unavailable.

        **No ``object_type`` anchor parameter** (design decision C): every
        ObjectType referenced in the SQL is inferred via
        ``compiler.involved_object_types`` and treated uniformly —
        access-checked, storage-routed, and column-remapped. This closes
        the single-anchor gap where ``SELECT a.p1, b.p2 FROM A JOIN B``
        only checked/mapped one OT, leaking columns as physical names and
        skipping access checks on joined tables.

        Storage routing across all involved OTs:
          - all MANAGED → Doris primary (Trino fallback on unavailable/not-built)
          - any VIRTUAL → Trino federation. Trino natively cross-catalog JOINs
            MANAGED tables (visible as ``iceberg.ontology.<snake>`` via the
            ``iceberg`` catalog) with VIRTUAL tables (external
            ``<catalog>.<schema>.<table>`` locators), so a single Trino query
            spans both. The compiler emits dialect-aware physical names so the
            Trino SQL resolves every table correctly.

        The compiler is injected (not constructed here) so callers can
        reuse a schema-preloaded instance per request. If None, a fresh
        MetaStoreSchemaProvider-backed compiler is built (slower — loads
        the full ontology schema each call).

        Args:
            ontology_api_name: Owning ontology.
            logical_sql: Logical SQL (ObjectType api_name as table,
                property api_name as column).
            compiler: Pre-built compiler with loaded schema, or None.

        Returns:
            Rows with physical column names mapped back to property
            api_names via ``_map_backing_to_api_multi`` (merged across
            all involved OTs).
        """
        if compiler is None:
            from ontology.services.textql.schema_provider import MetaStoreSchemaProvider

            provider = MetaStoreSchemaProvider(self._metadata)
            await provider.load(ontology_api_name)
            compiler = OntologySqlCompiler(provider)

        # Infer every ObjectType the SQL references — the single source of
        # truth for access check / routing / remap (no caller-supplied anchor).
        involved_apis = compiler.involved_object_types(logical_sql)
        if not involved_apis:
            raise OntologyError("SQL 未引用任何 ObjectType，无法执行", code="INVALID_TABLE")

        # Resolve each OT (existence + access check) and collect storage types.
        ots: list[ObjectType] = []
        for ot_api in involved_apis:
            ot, _ = await self._resolve_query_target(f"{ontology_api_name}.{ot_api}", principal=principal)
            ots.append(ot)
        storage_types = {ot.storage_type for ot in ots}
        # Routing: all-MANAGED → Doris primary (Trino fallback); any VIRTUAL
        # → Trino federation (Trino natively cross-catalog JOINs MANAGED
        # iceberg.ontology.<t> tables with VIRTUAL external <catalog> tables).
        force_trino = "VIRTUAL" in storage_types

        # ── Row-level pushdown (design §4.1 QueryScope, §4.2 SqlGlot inject) ──
        # Evaluate the row-security scope for each involved OT (Cedar TPE →
        # residual predicate + masked properties). Any OT hard-forbidden →
        # return empty (不可见即安全). Collected residuals are ANDed and
        # injected into the compiled SQL's WHERE (conservative: the principal
        # must satisfy every involved OT's row policy).
        residual_predicates: list[str] = []
        all_masked: set[str] = set()
        if principal is not None and self._authz is not None:
            for ot in ots:
                scope = await self._authz.evaluate_query_scope(principal, ontology_api_name, ot.api_name)
                if scope.forbidden:
                    return []  # 不可见即安全
                if scope.residual:
                    residual_predicates.append(scope.residual)
                all_masked.update(scope.masked_properties)

        rows = await self._compile_and_run(
            ots[0],
            ontology_api_name,
            logical_sql,
            compiler,
            force_trino=force_trino,
            residuals=residual_predicates or None,
        )
        # 语义层出口: 物理列名 → 属性 api_name（合并所有参与 OT 的映射表）。
        mapped = [self._map_backing_to_api_multi(ots, dict(r)) for r in rows]
        # 列脱敏: masked 属性置 null（存储层脱敏的补充，防止已水合的行泄露）。
        if all_masked:
            for row in mapped:
                for prop in all_masked:
                    if prop in row:
                        row[prop] = None
        return mapped

    async def _compile_and_run(
        self,
        ot: ObjectType,
        ontology_api_name: str,
        logical_sql: str,
        compiler: OntologySqlCompiler | None = None,
        *,
        force_trino: bool = False,
        residuals: list[str] | None = None,
    ) -> list[dict[str, object]]:
        """Compile logical SQL to physical dialect SQL and execute (no column mapping).

        Shared kernel for ``execute_compiled_sql`` (object loads, maps columns
        back to api_names) and aggregate compilation (keeps aggregate aliases
        like ``count`` / ``sum_amount`` as-is). Returns rows with **physical**
        column names; the caller decides whether to map back to api_names.

        Routing:
          - ``force_trino`` (any VIRTUAL OT in the SQL) → Trino federation
            directly. Trino cross-catalog JOINs MANAGED ``iceberg.ontology.<t>``
            tables with VIRTUAL external ``<catalog>.<schema>.<table>`` tables.
          - all MANAGED → Doris primary (Trino fallback on unavailable/not-built);
          - single VIRTUAL → Trino federation directly (no Doris table).
        """
        # Build a compiler if not provided.
        if compiler is None:
            from ontology.services.textql.schema_provider import MetaStoreSchemaProvider

            provider = MetaStoreSchemaProvider(self._metadata)
            await provider.load(ontology_api_name)
            compiler = OntologySqlCompiler(provider)

        # Compile to Doris (primary path) and Trino (fallback) up front
        # so the fallback is a cheap recompile, not a re-parse.
        doris_sql, params = compiler.compile(logical_sql, "doris")
        trino_sql, _ = compiler.compile(logical_sql, "trino")

        import logging as _lg

        _lg.getLogger(__name__).debug(
            "[TEXTSQL] trino=%s | doris=%s | ot=%s",
            trino_sql,
            doris_sql,
            getattr(ot, "api_name", None),
        )

        # B3: lite 桌面版无 Doris/Iceberg，查询走 DuckDB 联邦引擎。
        # lite 红线下只支持 VIRTUAL 本体（外部源联邦）；MANAGED 托管表不做。
        if settings.edition == "lite":
            if force_trino or ot.storage_type == "VIRTUAL":
                duckdb_sql, _ = compiler.compile(logical_sql, "duckdb")
                if residuals:
                    from ontology.services.sql_injector import inject_permission

                    combined = " AND ".join(f"({r})" for r in residuals)
                    duckdb_sql = inject_permission(duckdb_sql, combined, dialect="duckdb")
                return cast(list[dict[str, object]], await self._engine.query(duckdb_sql, params=params))
            # lite 版 MANAGED 查询无 Doris/Iceberg 落地，guard 拦截。
            raise OntologyError(
                f"桌面版不支持托管表（MANAGED）查询：{ot.api_name}",
                code="EDITION_UNAVAILABLE",
            )

        # Row-level pushdown: AND all residual predicates into every WHERE
        # clause of the compiled SQL (design §4.2, SqlGlot AST injection).
        # Conservative: the principal must satisfy every involved OT's row
        # policy. Applied to both Doris and Trino SQL (same residuals, both
        # dialects).
        if residuals:
            from ontology.services.sql_injector import inject_permission

            combined = " AND ".join(f"({r})" for r in residuals)
            doris_sql = inject_permission(doris_sql, combined, dialect="doris")
            trino_sql = inject_permission(trino_sql, combined, dialect="trino")

        # Any VIRTUAL OT in the SQL → Trino federation (cross-catalog JOIN).
        # Skip Doris entirely: it has no VIRTUAL physical tables.
        if force_trino or ot.storage_type == "VIRTUAL":
            return cast(list[dict[str, object]], await self._engine.query(trino_sql, params=params))

        # MANAGED → Doris primary, Trino fallback on Doris unavailable.
        try:
            if not await self._index.table_exists(ontology_api_name, ot.api_name):
                logger.info("Doris table not built for %s, text2sql via Trino", ot.api_name)
                object_query_fallback_total.labels(object_type=ot.api_name, reason="not_built").inc()
                return cast(list[dict[str, object]], await self._engine.query(trino_sql, params=params))
            rows = await self._index.execute_sql(ontology_api_name, ot.api_name, doris_sql, params)
            object_query_index_hit_total.labels(object_type=ot.api_name).inc()
            return cast(list[dict[str, object]], rows)
        except DorisUnavailableError:
            logger.warning("Doris text2sql failed for %s, falling back to Trino", ot.api_name)
            object_query_fallback_total.labels(object_type=ot.api_name, reason="doris_down").inc()
            return cast(list[dict[str, object]], await self._engine.query(trino_sql, params=params))

        # MANAGED → Doris primary, Trino fallback on Doris unavailable.
        try:
            if not await self._index.table_exists(ontology_api_name, ot.api_name):
                logger.info("Doris table not built for %s, text2sql via Trino", ot.api_name)
                object_query_fallback_total.labels(object_type=ot.api_name, reason="not_built").inc()
                rows = await self._engine.query(trino_sql, params=params)
                return [self._map_backing_to_api(ot, dict(r)) for r in rows]
            rows = await self._index.execute_sql(ontology_api_name, ot.api_name, doris_sql, params)
            object_query_index_hit_total.labels(object_type=ot.api_name).inc()
            return [self._map_backing_to_api(ot, dict(r)) for r in rows]
        except DorisUnavailableError:
            logger.warning("Doris text2sql failed for %s, falling back to Trino", ot.api_name)
            object_query_fallback_total.labels(object_type=ot.api_name, reason="doris_down").inc()
            rows = await self._engine.query(trino_sql, params=params)
            return [self._map_backing_to_api(ot, dict(r)) for r in rows]

        # MANAGED → Doris primary, Trino fallback on Doris unavailable.
        try:
            if not await self._index.table_exists(ontology_api_name, ot.api_name):
                logger.info("Doris table not built for %s, text2sql via Trino", ot.api_name)
                object_query_fallback_total.labels(object_type=ot.api_name, reason="not_built").inc()
                rows = await self._engine.query(trino_sql, params=params)
                return [self._map_backing_to_api(ot, dict(r)) for r in rows]
            rows = await self._index.execute_sql(ontology_api_name, ot.api_name, doris_sql, params)
            object_query_index_hit_total.labels(object_type=ot.api_name).inc()
            return [self._map_backing_to_api(ot, dict(r)) for r in rows]
        except DorisUnavailableError:
            logger.warning("Doris text2sql failed for %s, falling back to Trino", ot.api_name)
            object_query_fallback_total.labels(object_type=ot.api_name, reason="doris_down").inc()
            rows = await self._engine.query(trino_sql, params=params)
            return [self._map_backing_to_api(ot, dict(r)) for r in rows]

    async def hydrate_by_pk(
        self,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        """ADR Action Mutation Mapping 决策 C: hydrate 单个对象全量当前值。

        专为 ActionService._build_modify_mutation 设计的点查。走
        ``execute_compiled_sql``（TextQL/SqlGlot 编译路径，ADR-012 Step 4
        path B）：拼点查 logical SQL
        ``SELECT * FROM <OT> WHERE <pk_api> = '<id>'``，编译器做列名映射、
        参数化绑定、方言分叉（MANAGED→Doris 主 / Trino-Iceberg 降级；
        VIRTUAL→Trino 联邦查源表，PR 0 已支持）。

        字面量由 OntologySqlCompiler 自动提取为 ``?`` 占位符 + params
        （参数化绑定，注入安全）。

        降级：编译路径返回空时，用 Trino 联邦查 ObjectType.backing_mapping
        指向的源表（benchmark 数据可能在源系统而 Doris 未同步）。

        返回属性 dict（camelCase api_name → 值）或 None（所有源都查不到）。
        """
        parts = object_type_api_name.split(".")
        if len(parts) != 2:
            return None
        ontology_api_name, type_api_name = parts
        ot = await self._metadata.get_object_type(ontology_api_name, type_api_name)
        # 1. 主路径: execute_compiled_sql（编译路径，含 Doris/Trino 分叉 + 列名映射）
        pk_api = getattr(ot, "primary_key", None) or "id"
        # logical SQL 用 api_name 当表名/列名；编译器自动参数化字面量。
        logical_sql = f"SELECT * FROM {type_api_name} WHERE {pk_api} = '{object_id}'"
        try:
            rows = await self.execute_compiled_sql(ontology_api_name, logical_sql)
        except Exception:
            rows = []
        if rows:
            row = rows[0]
            data = dict(row) if isinstance(row, dict) else {"_value": row}
            return self._coerce_property_types(ot, data)
        # 2. 降级: Trino 联邦查 backing_mapping 指向的源表(MySQL 等)。
        return await self._hydrate_via_source_table(ot, object_id)

    async def hydrate_by_rids(
        self,
        ontology_api_name: str,
        rids: list[str],
        ot: ObjectType,
    ) -> list[dict[str, Any]]:
        """按 rid 批量水合全量属性（推理线主水合路径, handoff-rid-funnel-closure.md T1.6）。

        走 Doris idx 表 ``SELECT stored_columns FROM idx WHERE rid IN (...)``
        分批 1000/批, 返回 backing_column→api_name 映射后的属性 dict 列表。
        比 hydrate_by_pk 逐个查（N+1）高效一个数量级。

        - MANAGED: Doris idx 主路径, Doris 不可用时不降级（推理线无容灾需求,
          失败即抛 DorisUnavailableError 让上层处理）
        - VIRTUAL: rid 解析 → (ont, ot, pk) → 查外部源表, 委托 hydrate_by_pks
          批量 WHERE pk IN (...)（graph-reasoning-design.md §7.7，PR 5a）

        Args:
            ontology_api_name: 本体 api_name。
            rids: 待水合的 rid 列表（= object_state.id）。
            ot: 目标 ObjectType（调用方已加载, 避免重复查元数据）。

        Returns:
            属性 dict 列表（camelCase api_name → 值, 含类型强转）。rid 不在
            Doris 里的行会被跳过（返回数 ≤ len(rids)）。顺序不保证与输入一致。

        Raises:
            DorisUnavailableError: Doris 不可用（MANAGED 路径）。
        """
        if not rids:
            return []
        if ot.storage_type == "VIRTUAL":
            # VIRTUAL 批量水合：rid → PK → 外部源表 WHERE pk IN (...) 批量查
            # （graph-reasoning-design.md §7.7，PR 5a）。委托 hydrate_by_pks。
            parsed: list[str] = []
            from ontology.core.rid import parse_virtual_rid_pk

            for rid in rids:
                try:
                    _, _, pk = parse_virtual_rid_pk(rid)
                    parsed.append(pk)
                except ValueError:
                    continue
            return await self.hydrate_by_pks(ontology_api_name, ot, parsed)

        # 解析 stored_columns（全量物理列）: 复用 IndexFieldExtractor 拿到
        # 所有 backing_column, 作为 SELECT 列。
        from ontology.services.index_field_extractor import IndexFieldExtractor

        extractor = IndexFieldExtractor()
        extraction = extractor.extract(ot.properties, primary_key=ot.primary_key)
        columns = extraction.stored_columns
        if not columns:
            return []

        # 分批查 Doris idx（rid IN (...) 分批 1000, 对齐 load_by_ids 的批量级配）。
        batch_size = 1000
        raw_rows: list[dict[str, Any]] = []
        for i in range(0, len(rids), batch_size):
            chunk = rids[i : i + batch_size]
            rows = await self._index.load_by_ids(
                ontology_api_name,
                ot.api_name,
                rids=chunk,
                columns=columns,
                pk_column="rid",
            )
            raw_rows.extend(rows)

        # 映射 backing_column → api_name + 类型强转。
        return [self._coerce_property_types(ot, self._map_backing_to_api(ot, row)) for row in raw_rows]

    async def hydrate_by_pks(
        self,
        ontology_api_name: str,
        ot: ObjectType,
        pks: list[str],
        select_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """VIRTUAL 批量水合：WHERE pk IN (...) 分批查外部源表（§7.7，PR 5a）。

        替代 ``_hydrate_virtual`` 逐个调 ``hydrate_by_pk`` 的 N+1 反模式。
        同一批 PK 构造单条 ``SELECT cols FROM <virtual_table_ref> WHERE pk IN
        (?, ?, ...)``，参数化绑定，分批 1000（对齐 MANAGED 的 hydrate_by_rids
        批量级配）。

        Args:
            ontology_api_name: 本体 api_name（用于日志，查询本身不依赖）。
            ot: 目标 VIRTUAL ObjectType（调用方已加载）。
            pks: 待水合的主键值列表。
            select_fields: 可选属性 api_name 投影（下推到 SELECT 列表减少
                响应大小）；None 时取全量属性。PK 列始终被选（用于 rid 回填）。

        Returns:
            属性 dict 列表（camelCase api_name → 值, 含类型强转 + datetime/
            decimal 规范化）。顺序不保证与输入一致（调用方按 pk 回填 rid）。

        Raises:
            OntologyError: Trino 查询失败（调用方 catch 后标 _partial）。
        """
        if not pks:
            return []
        if ot.storage_type != "VIRTUAL":
            # MANAGED 应走 hydrate_by_rids（Doris 批量），此处仅 VIRTUAL。
            raise ValueError(f"hydrate_by_pks only supports VIRTUAL ObjectType, got {ot.storage_type}")

        table_ref = await self._virtual_table_ref(ot)
        pk_col = ObjectQueryService._pk_backing_column(ot)
        self._validate_identifier(pk_col)

        # 构建 SELECT 列表 + 物理列 → api_name 映射。
        col_to_api: dict[str, str] = {}
        for p in ot.properties or []:
            pm = getattr(p, "backing_mapping", None)
            col = str(pm.backing_column) if pm and getattr(pm, "backing_column", None) else str(p.api_name)
            self._validate_identifier(col)
            col_to_api[col] = str(p.api_name)
        if not col_to_api:
            return []

        # select_fields 下推：只选指定 api_name 对应的物理列；PK 列必选。
        if select_fields:
            api_to_col = {v: k for k, v in col_to_api.items()}
            wanted_cols = {api_to_col[f] for f in select_fields if f in api_to_col}
            wanted_cols.add(pk_col)  # PK 必选
            select_cols = list(wanted_cols)
        else:
            select_cols = list(col_to_api.keys())
        col_list = ", ".join(f'"{c}"' for c in select_cols)

        # 分批 1000 查询（IN 列表上限，对齐 hydrate_by_rids）。
        batch_size = 1000
        raw_rows: list[dict[str, Any]] = []
        for i in range(0, len(pks), batch_size):
            chunk = pks[i : i + batch_size]
            placeholders = ", ".join(["?"] * len(chunk))
            sql = f'SELECT {col_list} FROM {table_ref} WHERE "{pk_col}" IN ({placeholders})'
            rows = await self._engine.query(sql, chunk)
            raw_rows.extend(rows)

        # 规范化值 + 映射物理列 → api_name + 类型强转。
        from datetime import date, datetime
        from decimal import Decimal

        def _norm(v: Any) -> Any:
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        results: list[dict[str, Any]] = []
        for row in raw_rows:
            if not isinstance(row, dict):
                continue
            mapped = {col_to_api.get(k, k): _norm(v) for k, v in row.items()}
            results.append(self._coerce_property_types(ot, mapped))
        return results

    @staticmethod
    def _coerce_property_types(ot: ObjectType, data: dict[str, Any]) -> dict[str, Any]:
        """Coerce hydrate values to each property's declared data_type.

        Doris/Iceberg/Trino storage layers may return numeric values as
        strings (e.g. BIGINT → "100"), which breaks Action rule expressions
        like ``newAircraft.aircraftId > 0`` (D3: str > int TypeError). This
        restores the declared type so downstream rule evaluation and
        mutations see native ints/floats/bools.
        """
        if not isinstance(data, dict):
            return data
        by_api = {p.api_name: p for p in (ot.properties or [])}
        for api_name, value in list(data.items()):
            p = by_api.get(api_name)
            if p is None or value is None:
                continue
            dt = str(getattr(p, "data_type", "")).upper()
            try:
                if dt in ("INTEGER", "LONG", "BIGINT", "INT") and isinstance(value, str):
                    data[api_name] = int(value)
                elif dt in ("DOUBLE", "FLOAT", "DECIMAL") and isinstance(value, str):
                    data[api_name] = float(value)
                elif dt in ("BOOLEAN", "BOOL") and isinstance(value, str):
                    data[api_name] = value.strip().lower() in ("true", "1", "t", "yes")
            except (ValueError, TypeError):
                pass  # leave original value if coercion fails
        return data

    def _map_backing_to_api(self, ot: ObjectType, data: dict[str, Any]) -> dict[str, Any]:
        """snake_case 物理列名 → camelCase 属性 api_name (load_objects 主路径出口)。"""
        return self._map_backing_to_api_multi([ot], data)

    def _map_backing_to_api_multi(self, ots: list[ObjectType], data: dict[str, Any]) -> dict[str, Any]:
        """Map physical column names → property api_names across multiple OTs.

        Used by ``execute_compiled_sql`` for multi-table JOIN queries where
        the result rows contain columns from several ObjectTypes. Merges the
        ``backing_column → api_name`` maps of every involved OT.

        Conflict policy: when two OTs map the SAME physical column to
        DIFFERENT api_names, the column is ambiguous (cannot attribute it to
        one OT) — keep the physical name as-is rather than silently picking
        one OT's mapping (which would mis-attribute the column). Columns not
        in any OT's map (aggregation aliases like ``count``/``sum_amount``,
        or ``SELECT *`` extras) pass through unchanged.
        """
        col_to_api: dict[str, str] = {}
        ambiguous: set[str] = set()
        for ot in ots:
            for p in ot.properties or []:
                pm = getattr(p, "backing_mapping", None)
                col = str(pm.backing_column) if pm and getattr(pm, "backing_column", None) else str(p.api_name)
                if col in col_to_api and col_to_api[col] != str(p.api_name):
                    # Two OTs claim this physical col with different api_names → ambiguous.
                    ambiguous.add(col)
                else:
                    col_to_api[col] = str(p.api_name)
        # Remove ambiguous mappings so they fall through to the physical name.
        for col in ambiguous:
            col_to_api.pop(col, None)
        # 规范化值(datetime/Decimal → JSON 可序列化)。
        from datetime import date, datetime
        from decimal import Decimal

        def _norm(v: Any) -> Any:
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        return {col_to_api.get(k, k): _norm(v) for k, v in data.items()}

    async def _hydrate_via_source_table(self, ot: ObjectType, object_id: str) -> dict[str, Any] | None:
        """降级 hydrate: 用 Trino 查 ObjectType backing_mapping 指向的源表。

        构造 SELECT <全量物理列> FROM <catalog>.<schema>.<table>
        WHERE <主键物理列> = '<id>',把结果行(snake_case 列)映射回 camelCase 属性。
        """
        catalog = schema = table = None
        for p in ot.properties or []:
            pm = getattr(p, "backing_mapping", None)
            if pm and getattr(pm, "backing_table", None):
                catalog = pm.backing_catalog
                schema = pm.backing_schema
                table = pm.backing_table
                break
        if not (catalog and schema and table):
            return None
        # B3: lite 版 catalog 用 DuckDB ATTACH 别名 src_<ds>（DataSource api_name）。
        if settings.edition == "lite":
            catalog = f"src_{str(catalog).lower()}"
        pk_col = self._pk_backing_column(ot)
        col_to_api: dict[str, str] = {}
        cols: list[str] = []
        for p in ot.properties or []:
            pm = getattr(p, "backing_mapping", None)
            col = str(pm.backing_column) if pm and getattr(pm, "backing_column", None) else str(p.api_name)
            self._validate_identifier(col)
            cols.append(col)
            col_to_api[col] = str(p.api_name)
        if not cols:
            return None
        col_list = ", ".join(cols)
        self._validate_identifier(pk_col)
        # 主键值字面量: 纯数字不加引号(bigtint/int),其它加单引号(字符串)。
        # 标识符已白名单校验;值转义防注入。
        if isinstance(object_id, str) and object_id.lstrip("-").isdigit():
            lit = object_id
        elif isinstance(object_id, int | float):
            lit = str(object_id)
        else:
            lit = "'" + str(object_id).replace("'", "''") + "'"
        sql = f'SELECT {col_list} FROM "{catalog}"."{schema}"."{table}" WHERE "{pk_col}" = {lit}'
        try:
            rows = await self._engine.query(sql)
        except Exception:
            return None
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, dict):
            return None
        # 规范化值: datetime/decimal 等 → JSON 可序列化(isoformat/str)。
        from datetime import date, datetime
        from decimal import Decimal

        def _norm(v: Any) -> Any:
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        return self._coerce_property_types(ot, {col_to_api.get(k, k): _norm(v) for k, v in row.items()})

    async def _resolve_query_target(
        self, object_type_api_name: str, *, principal: "Principal | None" = None
    ) -> tuple[ObjectType, str]:
        """Resolve an "ontology.type" string to (ObjectType, Trino table ref).

        Applies the access check (fail-closed via PDP when a principal is
        given) + storage_type routing. For MANAGED objects the table ref
        points at the Iceberg table (via Gravitino catalog); for VIRTUAL it
        points at the Virtual Table view.
        """
        parts = object_type_api_name.split(".")
        if len(parts) != 2:
            raise NotFoundError("ObjectType", object_type_api_name)
        ontology_api_name, type_api_name = parts
        ot = await self._metadata.get_object_type(ontology_api_name, type_api_name)
        # Permission check (fail-closed via PDP when principal is given).
        if principal is not None and self._authz is not None:
            result = await self._authz.check_access(principal, "OBJECT_TYPE", ot.api_name, OP_OBJECT_VIEW)
            if not result.allowed:
                raise ForbiddenError(f"Access denied to {ot.api_name}: {result.reason}")
        if ot.storage_type == "VIRTUAL":
            return ot, await self._virtual_table_ref(ot)
        table_info = await self._catalog.resolve_backing_table(ot.api_name)
        return ot, f"{table_info['catalog']}.{table_info['schema']}.{table_info['table']}"

    @staticmethod
    def _pk_backing_column(ot: ObjectType) -> str:
        """Resolve the physical Iceberg column for an ObjectType's primary key.

        ``ObjectType.primary_key`` is the property *api_name* (camelCase, e.g.
        "flightId"), but the Iceberg table stores rows under the *physical*
        column name (snake_case, e.g. "flight_id", from PropertyDef.backing_mapping.backing_column).
        Falls back to the api_name when no physical mapping is present.
        """
        pk_api = getattr(ot, "primary_key", None)
        if not pk_api:
            return "id"
        for p in getattr(ot, "properties", []) or []:
            if getattr(p, "api_name", None) == pk_api:
                pm = getattr(p, "backing_mapping", None)
                if pm and getattr(pm, "backing_column", None):
                    return str(pm.backing_column)
                return str(pk_api)
        return str(pk_api)

    async def _virtual_table_ref(self, object_type: ObjectType) -> str:
        """Build a ``catalog.schema.table`` ref for a VIRTUAL object.

        - full 版（Trino）：catalog = DataSource api_name（Trino 注册时 lower-case）。
        - lite 版（DuckDB, B3）：catalog = ``src_<ds api_name lower>``（DuckDB
          ATTACH 别名，见 B4 连接器 ``to_duckdb_attach``）。

        Reads the first property's backing_mapping (catalog_name/schema_name/
        table_name). Resolves the catalog name from the registered DataSource
        (datasource.api_name) rather than blindly trusting property.backing_catalog,
        so a mismatch like ``airline_mysql`` (snake) vs registered ``airlineMysql``
        does not yield ``Catalog not found`` (D2). Falls back to
        ``gravitino.ontology.{api}_view`` (full) / ``src_gravitino.ontology.{api}_view``
        (lite) when no mapping present.
        """
        for p in object_type.properties or []:
            pm = getattr(p, "backing_mapping", None)
            if pm and getattr(pm, "backing_table", None):
                catalog = getattr(pm, "backing_catalog", None) or "gravitino"
                # Resolve the real catalog name from the DataSource that backs
                # this object type (D2): backing_catalog may be stale /
                # differently-cased; the DataSource api_name is authoritative.
                catalog = await self._resolve_trino_catalog(catalog)
                if settings.edition == "lite":
                    # B3: DuckDB ATTACH 别名约定 src_<ds>。
                    catalog = f"src_{catalog.lower()}"
                schema = getattr(pm, "backing_schema", None) or "ontology"
                table = str(pm.backing_table)
                ObjectQueryService._validate_identifier(catalog)
                ObjectQueryService._validate_identifier(schema)
                ObjectQueryService._validate_identifier(table)
                return f"{catalog}.{schema}.{table}"
        suffix = f"{object_type.api_name}_view"
        if settings.edition == "lite":
            return f"src_gravitino.ontology.{suffix}"
        return f"gravitino.ontology.{suffix}"

    async def _resolve_trino_catalog(self, backing_catalog: str) -> str:
        """Resolve the Trino catalog name for a backing_catalog value.

        backing_catalog nominally equals a DataSource api_name, but historical
        data may carry a differently-cased / snake_case variant. We match
        case-insensitively against registered DataSources and return the
        DataSource's api_name (the authoritative catalog name). Falls back
        to the raw backing_catalog when no DataSource matches (let Trino
        surface the error).
        """
        try:
            datasources = await self._metadata.list_datasources()
            if not isinstance(datasources, list):
                return backing_catalog
        except Exception:
            return backing_catalog
        bc_lower = backing_catalog.lower()
        for ds in datasources:
            if ds.api_name.lower() == bc_lower:
                return ds.api_name
        return backing_catalog

    @staticmethod
    def _validate_identifier(name: str) -> None:
        """Validate a SQL identifier against a whitelist pattern.

        Identifiers (catalog/schema/table/column names from the backing
        layer) — they MUST be validated before interpolation into SQL to
        prevent injection. Allow letters, digits, underscore; must start
        with a letter or underscore.

        This is the injection-safety floor. The friendlier whitelist check
        (``_validate_filter_properties``) runs BEFORE this in callers that
        have the ObjectType context, rejecting unknown property names with
        INVALID_FILTER rather than letting them reach SQL and fail opaquely.
        """
        import re

        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise OntologyError(f"Invalid SQL identifier: {name!r}", code="INVALID_FILTER")
