"""Dependency injection container — wires all layers and services.

Single source of truth for component wiring. All Layer implementations
are instantiated here with their runtime dependencies, then injected
into Services.

Usage:
    from ontology.config.container import Container
    container = Container()

    # In route handlers, use metadata as context manager for session cleanup:
    async with container.metadata as meta:
        ...

    # Non-metadata services don't need cleanup:
    engine = container.engine
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cashews import Cache

# 属性过滤用的 PG engine（object_state JSONB 过滤）。延迟导入避免循环。
# database.py 仅依赖 SQLAlchemy + settings，不含重依赖，两版均可顶层 import。
from ontology.config.database import engine as _default_engine_for_attr_filter

# settings 仅依赖 pydantic-settings，无重依赖；edition 字段驱动 Layer 条件装配（A3）。
from ontology.config.settings import settings
from ontology.core.exceptions import EditionUnavailableError

# Layer / Service 类全部走 TYPE_CHECKING + property 内 lazy import，确保
# `import ontology.config.container` 在 lite 版（未装 neo4j/pyiceberg/trino/
# onnxruntime/aiobotocore/asyncpg/aiomysql 等重依赖）下也不 ImportError。
# 实际实例化延迟到对应 property 首次访问时。
if TYPE_CHECKING:
    from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
    from ontology.layers.dataset.iceberg_store import IcebergStore
    from ontology.layers.engine.base import QueryEngine
    from ontology.layers.geotime.geotime_store import GeoTimeStore
    from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore
    from ontology.layers.index.doris_index_store import DorisIndexStore
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
    from ontology.layers.pipeline.kestra_engine import KestraEngine
    from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine
    from ontology.services.access_request_service import AccessRequestService
    from ontology.services.action_auth import ActionAuthorizer
    from ontology.services.action_service import ActionService
    from ontology.services.authorization_service import AuthorizationService
    from ontology.services.conflict_detector import ConflictDetector
    from ontology.services.container_service import ContainerService
    from ontology.services.datasource_service import DataSourceService
    from ontology.services.geotime_projector import GeoTimeProjector
    from ontology.services.graph_projector import GraphProjector
    from ontology.services.iceberg_maintenance_service import IcebergMaintenanceService
    from ontology.services.identity_service import IdentityService
    from ontology.services.index_sync_service import IndexSyncService
    from ontology.services.marking_service import MarkingService
    from ontology.services.object_index_funnel import ObjectIndexFunnel
    from ontology.services.object_query_service import ObjectQueryService
    from ontology.services.object_set_executor import DataFrameQueryService
    from ontology.services.ontology_service import OntologyService
    from ontology.services.outbox_executor import OutboxExecutor
    from ontology.services.pipeline_build_reconciler import PipelineBuildReconciler
    from ontology.services.pipeline_builder_service import PipelineBuilderService
    from ontology.services.schema_inference_engine import SchemaInferenceEngine
    from ontology.services.sync_flush_scheduler import SyncFlushScheduler
    from ontology.services.time_travel_service import TimeTravelService
    from ontology.services.write_back_manager import WriteBackManager


class Container:
    """Application-level dependency injection container.

    Lazily initializes Layer components (connections established on first
    use). Services are NOT cached — each access builds a fresh service bound
    to a fresh AsyncSession, so requests never share a session.
    at construction time (connections are established on first use).
    """

    def __init__(self) -> None:
        self._catalog: GravitinoRegistry | None = None
        self._dataset: IcebergStore | None = None
        self._index: DorisIndexStore | None = None
        self._pipeline: SeaTunnelEngine | None = None
        self._engine: QueryEngine | None = None
        # Graph Layer (graph-reasoning-design.md)。Neo4j 独立服务 profile=graph，
        # 按需启停；store 实例轻量，driver 模块级单例。
        self._graph_store: Neo4jGraphStore | None = None
        # GeoTime Layer (graph-reasoning-design.md)。PostGIS+TimescaleDB 复用
        # 现有 PG 实例（一体镜像），store 实例轻量。
        self._geotime_store: GeoTimeStore | None = None
        # §14.4 语义检索: EmbeddingProvider 惰性单例 (本地 ONNX, 首次使用加载模型)。
        self._embedding_provider: Any = None
        # Optional service overrides — when set, the corresponding property
        # returns the override instead of building a fresh service. Used for
        # testing and advanced DI; production code leaves this empty so each
        # request gets a fresh service bound to a fresh AsyncSession.
        self.service_overrides: dict[str, Any] = {}
        # Cached service singletons (D7 fix): AG-UI toolsets access
        # ``container.<service>`` per tool call; without caching each access
        # built a fresh service bound to a fresh AsyncSession that was never
        # closed, leaking connections under long runs. Cached services are
        # closed in ``aclose()`` (called at request/app shutdown).
        self._service_cache: dict[str, Any] = {}

    async def aclose(self) -> None:
        """Close all cached services, returning their sessions to the pool.

        Call at request end (AG-UI run) or app shutdown to prevent the
        connection-pool exhaustion seen in long-running agent benchmarks (D7).
        """
        for svc in self._service_cache.values():
            aclose = getattr(svc, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass
        self._service_cache.clear()
        # B2: lite 版 DuckDB 引擎是单例 Layer（不在 _service_cache），shutdown 关连接。
        # close() 是 DuckDBEngine 专属（QueryEngine 契约不含），用 getattr 守卫。
        if settings.edition == "lite" and self._engine is not None:
            close = getattr(self._engine, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
            self._engine = None

    # ── Layer Initialization ──

    @property
    def metadata(self) -> PostgresMetaStore:
        """PostgreSQL metadata store.

        DEPRECATED: Use metadata_session() context manager instead to
        ensure the AsyncSession is properly closed after the request.

        Creates a fresh AsyncSession on each access to avoid:
          - Cross-request session contamination
          - Greenlet finalization errors on hot-reload
          - Stale/session-disconnected errors

        Each access returns a NEW session that is never closed by this
        property — callers that hold the returned PostgresMetaStore (e.g.
        Services constructed with ``metadata=self.metadata``) will leak
        the session. Migrate to metadata_session() (M2). Accessing this
        property logs a deprecation warning.
        """
        import warnings

        warnings.warn(
            "container.metadata is deprecated; it leaks an unclosed AsyncSession. "
            "Use `async with container.metadata_session() as meta:` instead (M2).",
            DeprecationWarning,
            stacklevel=2,
        )
        from ontology.config.database import async_session_factory
        from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

        session = async_session_factory()
        return PostgresMetaStore(session)

    @asynccontextmanager
    async def metadata_session(self) -> AsyncIterator[PostgresMetaStore]:
        """Context manager for PostgresMetaStore with session cleanup.

        Usage:
            async with container.metadata_session() as meta:
                result = await meta.list_datasources()

        The AsyncSession is automatically closed when the context exits,
        returning the connection to the pool.
        """
        from ontology.config.database import async_session_factory
        from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

        session = async_session_factory()
        store = PostgresMetaStore(session)
        try:
            yield store
        finally:
            await store.close()

    @property
    def catalog(self) -> GravitinoRegistry:
        # lite 版不用 Gravitino：edition 短路在前，根本不 import 重依赖模块。
        if settings.edition == "lite":
            raise EditionUnavailableError("Gravitino catalog 在 lite 版不可用")
        if self._catalog is None:
            from ontology.layers.catalog.gravitino_registry import GravitinoRegistry

            self._catalog = GravitinoRegistry()
        return self._catalog

    @property
    def dataset(self) -> IcebergStore:
        if settings.edition == "lite":
            raise EditionUnavailableError("Iceberg dataset 层在 lite 版不可用")
        if self._dataset is None:
            from ontology.layers.dataset.iceberg_store import IcebergStore

            self._dataset = IcebergStore()
        return self._dataset

    @property
    def index(self) -> DorisIndexStore:
        if settings.edition == "lite":
            raise EditionUnavailableError("Doris index 层在 lite 版不可用")
        if self._index is None:
            from ontology.layers.index.doris_index_store import DorisIndexStore

            self._index = DorisIndexStore()
        return self._index

    @property
    def pipeline(self) -> SeaTunnelEngine:
        if settings.edition == "lite":
            raise EditionUnavailableError("SeaTunnel pipeline 层在 lite 版不可用")
        if self._pipeline is None:
            from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine

            self._pipeline = SeaTunnelEngine()
        return self._pipeline

    @property
    def engine(self) -> QueryEngine:
        # lite 版用 DuckDB 嵌入式联邦引擎（B2）；full 版用 Trino。两者实现同一
        # QueryEngine 契约（layers/engine/base.py），Service 层按契约依赖。
        if settings.edition == "lite":
            if self._engine is None:
                from ontology.layers.engine.duckdb_engine import DuckDBEngine

                self._engine = DuckDBEngine()
            return self._engine
        if self._engine is None:
            from ontology.layers.engine.trino_query_engine import TrinoQueryEngine

            self._engine = TrinoQueryEngine()
        return self._engine

    # ── Graph Layer (graph-reasoning-design.md §4) ──

    @property
    def graph_store(self) -> Neo4jGraphStore:
        """Neo4jGraphStore 单例。driver 在模块级单例管理（_get_driver），
        store 实例轻量无状态。

        Neo4j 不可用时（profile=graph 未启动）属性访问不报错——报错延迟到
        首次查询（GraphUnavailableError），让 SQL 线（query_with_sql）不受
        影响（两条线独立, C5/C12）。lite 版无图推理，访问即报 EditionUnavailableError。
        """
        if override := self.service_overrides.get("graph_store"):
            return override  # type: ignore[no-any-return]
        if settings.edition == "lite":
            raise EditionUnavailableError("Neo4j graph 层在 lite 版不可用")
        if self._graph_store is None:
            from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore

            self._graph_store = Neo4jGraphStore()
        return self._graph_store

    @property
    def graph_projector(self) -> GraphProjector:
        if override := self.service_overrides.get("graph_projector"):
            return override  # type: ignore[no-any-return]
        from ontology.services.graph_projector import GraphProjector

        return GraphProjector(metadata=self.metadata, graph_store=self.graph_store)

    # ── GeoTime Layer (graph-reasoning-design.md §5) ──

    @property
    def geotime_store(self) -> GeoTimeStore:
        """GeoTimeStore 单例（PostGIS + TimescaleDB 合并封装）。

        复用现有 PG engine（与 metadata 同库，PostGIS/TimescaleDB 已激活）。
        lite 版无空间/时序能力，访问即报 EditionUnavailableError。
        """
        if override := self.service_overrides.get("geotime_store"):
            return override  # type: ignore[no-any-return]
        if settings.edition == "lite":
            raise EditionUnavailableError("GeoTime 时空层在 lite 版不可用")
        if self._geotime_store is None:
            from ontology.layers.geotime.geotime_store import GeoTimeStore

            self._geotime_store = GeoTimeStore()
        return self._geotime_store

    @property
    def geotime_projector(self) -> GeoTimeProjector:
        if override := self.service_overrides.get("geotime_projector"):
            return override  # type: ignore[no-any-return]
        from ontology.services.geotime_projector import GeoTimeProjector

        return GeoTimeProjector(metadata=self.metadata, geotime_store=self.geotime_store)

    # ─§14.4 语义检索: EmbeddingProvider (本地 ONNX) ──

    @property
    def embedding_provider(self) -> Any:
        """EmbeddingProvider 单例 (OnnxEmbeddingProvider, MiniLM-L12-v2 384 维)。

        惰性加载: 首次访问时加载 ONNX 模型 (~15ms/sentence, CPU)。复用
        textql.embedding 的模块级单例 (TextQL 引擎B 向量召回已用同一实例)。
        """
        if override := self.service_overrides.get("embedding_provider"):
            return override
        if self._embedding_provider is None:
            try:
                from ontology.services.textql.embedding import get_embedding_provider

                self._embedding_provider = get_embedding_provider()
            except FileNotFoundError:
                # ONNX 模型缺失（如本地开发未下载）时降级为 None。
                # TextQL 语义召回（引擎 B）会不可用，但后端其余功能（pipeline builder /
                # ontology CRUD / action 等）不受影响。生产环境应预装模型。
                self._embedding_provider = None
        return self._embedding_provider

    # ── 外部数据索引漏斗 (object_index_funnel) ──

    @property
    def object_index_funnel(self) -> ObjectIndexFunnel:
        """ObjectIndexFunnel：从 Iceberg 读取外部接入数据，统一编排 rid 分配 +
        Doris idx 写入 + 按 capabilities 门控投影到 Neo4j 图/PostGIS 空间表。

        与 OutboxExecutor 的区别：OutboxExecutor 消费 outbox INDEX event
        （Action 写入路径），本 Funnel 读 Iceberg（外部数据接入路径）。
        """
        if override := self.service_overrides.get("object_index_funnel"):
            return override  # type: ignore[no-any-return]
        from ontology.services.object_index_funnel import ObjectIndexFunnel

        return ObjectIndexFunnel(
            metadata=self.metadata,
            dataset=self.dataset,
            graph_projector=self.graph_projector,
            geotime_projector=self.geotime_projector,
            index_store=self.index,
            engine=self.engine,
            object_query=self.object_query_service,
        )

    # ── 推理线编排中枢 (graph-reasoning-design.md §7.3) ──

    @property
    def dataframe_query_service(self) -> DataFrameQueryService:
        """DataFrameQueryService：ObjectSet IR → 多引擎执行。

        注入 graph_store + geotime_store + metadata（水合借 object_state 批量取，
        C12；Doris 水合留优化期）。Neo4j/PostGIS 不可用时首次查询报错
        （GraphUnavailableError/GeoTimeUnavailableError），不阻塞构造。
        """
        if override := self.service_overrides.get("dataframe_query_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.object_set_executor import DataFrameQueryService

        return DataFrameQueryService(
            graph_store=self.graph_store,
            geotime_store=self.geotime_store,
            metadata=self.metadata,
            attr_engine=_default_engine_for_attr_filter,
            object_query_service=self.object_query_service,
        )

    # ── Service Initialization ──

    @property
    def index_sync_service(self) -> IndexSyncService:
        if override := self.service_overrides.get("index_sync_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.index_sync_service import IndexSyncService

        return IndexSyncService(
            index=self.index,
            metadata=self.metadata,
        )

    @property
    def ontology_service(self) -> OntologyService:
        if override := self.service_overrides.get("ontology_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.ontology_service import OntologyService

        # lite 版只做 VIRTUAL 本体建模（define_object_type 入口已 guard 拦截
        # MANAGED），运行时不触达 catalog/index/index_sync（这些 Layer lite 不装）。
        # 构造期传 None 避免访问 self.catalog/self.index/self.index_sync_service
        # 抛 EditionUnavailableError。full 版行为不变。
        if settings.edition == "lite":
            return OntologyService(
                metadata=self.metadata,
                catalog=None,
                index=None,
                index_sync=None,
                container=self,
            )
        return OntologyService(
            metadata=self.metadata,
            catalog=self.catalog,
            index=self.index,
            index_sync=self.index_sync_service,
            container=self,
        )

    @property
    def object_query_service(self) -> ObjectQueryService:
        if override := self.service_overrides.get("object_query_service"):
            return override  # type: ignore[no-any-return]
        # D7 fix: cache the service so AG-UI toolsets (which access this
        # property per tool call) reuse one session instead of leaking one
        # per call. Closed via container.aclose().
        cached = self._service_cache.get("object_query_service")
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        from ontology.services.object_query_service import ObjectQueryService

        # lite 版 VIRTUAL 查询走 DuckDB（_virtual_table_ref 用 src_<ds> 前缀，
        # B3），不触达 catalog/index/dataset（MANAGED 路径 _compile_and_run 已
        # guard 抛 EDITION_UNAVAILABLE）。构造期传 None 避免访问 self.catalog
        # /self.index/self.dataset 抛错。engine 走 DuckDB（lite 不抛错）。
        if settings.edition == "lite":
            svc = ObjectQueryService(
                metadata=self.metadata,
                catalog=None,
                index=None,
                dataset=None,
                engine=self.engine,
                authorization_service=self.authorization_service,
            )
            self._service_cache["object_query_service"] = svc
            return svc
        svc = ObjectQueryService(
            metadata=self.metadata,
            catalog=self.catalog,
            index=self.index,
            dataset=self.dataset,
            engine=self.engine,
            authorization_service=self.authorization_service,
        )
        self._service_cache["object_query_service"] = svc
        return svc

    @property
    def time_travel_service(self) -> TimeTravelService:
        if override := self.service_overrides.get("time_travel_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.time_travel_service import TimeTravelService

        return TimeTravelService(
            catalog=self.catalog,
            engine=self.engine,
        )

    @property
    def permission_cache(self) -> Cache:
        """cashews permission cache (ADR-017 D2).

        URL-driven backend: ``mem://`` for dev, ``redis://host:6379/0`` for
        production. Same code, only ``settings.permission_cache_url`` changes.
        Cached as a singleton (the cache backend connection is reused).
        """
        if cached := self._service_cache.get("permission_cache"):
            return cached  # type: ignore[no-any-return]
        from cashews import Cache

        from ontology.config.settings import settings

        c = Cache(name="permission")
        c.setup(
            settings.permission_cache_url,
            client_side=settings.permission_cache_client_side,
        )
        self._service_cache["permission_cache"] = c
        return c

    @property
    def authorization_service(self) -> AuthorizationService:
        """AuthorizationService (PDP, ADR-016 Phase 1).

        Bound to a FRESH metadata session per access (request-scoped). The
        underlying permission_cache is the shared singleton.
        """
        if override := self.service_overrides.get("authorization_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.authorization_service import AuthorizationService

        return AuthorizationService(metadata=self.metadata, cache=self.permission_cache)

    @property
    def access_request_service(self) -> AccessRequestService:
        """AccessRequestService (JIT, ADR-016 Phase 4)."""
        if override := self.service_overrides.get("access_request_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.access_request_service import AccessRequestService

        return AccessRequestService(metadata=self.metadata, authorization_service=self.authorization_service)

    @property
    def marking_service(self) -> MarkingService:
        """MarkingService (MAC, ADR-016 Phase 2).

        Bound to a FRESH metadata session per access. Wires the
        AuthorizationService for separation-of-duties enforcement.
        """
        if override := self.service_overrides.get("marking_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.marking_service import MarkingService

        return MarkingService(metadata=self.metadata, authorization_service=self.authorization_service)

    @property
    def identity_service(self) -> IdentityService:
        """IdentityService (User/Group management, ADR-016 Phase 1).

        Bound to a FRESH metadata session per access. Wires the
        AuthorizationService for the role:manage gate.
        """
        if override := self.service_overrides.get("identity_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.identity_service import IdentityService

        return IdentityService(metadata=self.metadata, authorization_service=self.authorization_service)

    @property
    def container_service(self) -> ContainerService:
        """ContainerService (Org/Space/Project management, ADR-016 Phase 0/1)."""
        if override := self.service_overrides.get("container_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.container_service import ContainerService

        return ContainerService(metadata=self.metadata, authorization_service=self.authorization_service)

    @property
    def action_authorizer(self) -> ActionAuthorizer:
        if override := self.service_overrides.get("action_authorizer"):
            return override  # type: ignore[no-any-return]
        from ontology.services.action_auth import ActionAuthorizer
        from ontology.services.action_rule_engine import ActionRuleEngine

        # lite 版 ActionAuthorizer 的 catalog 是死存储（从不调用其方法，仅用于
        # 未来扩展），传 None 避免访问 self.catalog 抛 EditionUnavailableError。
        if settings.edition == "lite":
            return ActionAuthorizer(
                metadata=self.metadata,
                catalog=None,
                rule_engine=ActionRuleEngine(),
                authorization_service=self.authorization_service,
            )
        return ActionAuthorizer(
            metadata=self.metadata,
            catalog=self.catalog,
            rule_engine=ActionRuleEngine(),
            authorization_service=self.authorization_service,
        )

    @property
    def action_service(self) -> ActionService:
        if override := self.service_overrides.get("action_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.action_rule_engine import ActionRuleEngine
        from ontology.services.action_service import ActionService

        # lite 版 Action 执行写 SQLite object_state（B5），不触达 catalog/dataset
        # （catalog 仅 lazy 构造 ActionAuthorizer 的死存储；outbox effect 已过滤
        # INDEX/ARCHIVE/EMBEDDING）。graph_projector lite 不装（图推理砍）传 None。
        if settings.edition == "lite":
            return ActionService(
                metadata=self.metadata,
                catalog=None,
                dataset=None,
                rule_engine=ActionRuleEngine(),
                authorizer=self.action_authorizer,
                object_query_service=self.object_query_service,
                authorization_service=self.authorization_service,
                graph_projector=None,
            )
        return ActionService(
            metadata=self.metadata,
            catalog=self.catalog,
            dataset=self.dataset,
            rule_engine=ActionRuleEngine(),
            authorizer=self.action_authorizer,
            object_query_service=self.object_query_service,
            authorization_service=self.authorization_service,
            graph_projector=self.graph_projector,
        )

    # ── Pipeline Builder (ADR-018) ──

    @property
    def kestra_engine(self) -> KestraEngine:
        """KestraEngine singleton — stateless, wraps REST client + translator.

        Cached on first access (stateless component, safe to share across
        requests — mirrors schema_inference_engine / graph_projector caching).
        """
        if override := self.service_overrides.get("kestra_engine"):
            return override  # type: ignore[no-any-return]
        from ontology.layers.pipeline.kestra_engine import KestraEngine

        if not hasattr(self, "_kestra_engine_instance"):
            self._kestra_engine_instance = KestraEngine()
        return self._kestra_engine_instance

    @property
    def schema_inference_engine(self) -> SchemaInferenceEngine:
        """SchemaInferenceEngine singleton — stateless, no session needed.

        Pure-logic engine: compiles Pipeline IR to inferred schemas and
        contract violations without touching any database or external service.
        Safe to share across requests.
        """
        if override := self.service_overrides.get("schema_inference_engine"):
            return override  # type: ignore[no-any-return]
        from ontology.services.schema_inference_engine import SchemaInferenceEngine

        return SchemaInferenceEngine()

    @property
    def pipeline_builder_service(self) -> PipelineBuilderService:
        """PipelineBuilderService — bound to a FRESH metadata session per request.

        Usage in route handlers:
            svc = container.pipeline_builder_service
            try:
                result = await svc.create_pipeline(data)
            finally:
                await svc.aclose()
        """
        if override := self.service_overrides.get("pipeline_builder_service"):
            return override  # type: ignore[no-any-return]
        from ontology.config.database import async_session_factory
        from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
        from ontology.services.pipeline_builder_service import PipelineBuilderService

        session = async_session_factory()
        # PostgresMetaStore wraps the session (provides transaction() + close()).
        metadata = PostgresMetaStore(session)
        return PipelineBuilderService(
            metadata=metadata,
            schema_engine=self.schema_inference_engine,
            kestra_engine=self.kestra_engine,
            dataset=self.dataset,
            catalog=self.catalog,
        )

    @property
    def pipeline_build_reconciler(self) -> PipelineBuildReconciler:
        """PipelineBuildReconciler — background loop aligning PG vs Kestra.

        Stateless (uses ``container.metadata_session()`` per iteration), so
        a single instance is cached for the app lifetime.
        """
        if override := self.service_overrides.get("pipeline_build_reconciler"):
            return override  # type: ignore[no-any-return]
        from ontology.services.pipeline_build_reconciler import PipelineBuildReconciler

        if not hasattr(self, "_pipeline_build_reconciler_instance"):
            self._pipeline_build_reconciler_instance = PipelineBuildReconciler(self)
        return self._pipeline_build_reconciler_instance

    @property
    def datasource_service(self) -> DataSourceService:
        if override := self.service_overrides.get("datasource_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.datasource_service import DataSourceService

        # lite 版数据源 CRUD + 探索 + 虚拟表注册走 DuckDB（B4），Service 内部
        # self.catalog.xxx 调用全被 `if edition=="lite"` 分支提前 return 保护，
        # 不触达 catalog/pipeline/dataset。object_index_funnel lite 不装（Index
        # 同步砍）传 None。engine 走 DuckDB（lite 不抛错）。
        if settings.edition == "lite":
            return DataSourceService(
                metadata=self.metadata,
                catalog=None,
                engine=self.engine,
                pipeline=None,
                dataset=None,
                object_index_funnel=None,
            )
        return DataSourceService(
            metadata=self.metadata,
            catalog=self.catalog,
            engine=self.engine,
            pipeline=self.pipeline,
            dataset=self.dataset,
            object_index_funnel=self.object_index_funnel,
        )

    @property
    def write_back_manager(self) -> WriteBackManager:
        if override := self.service_overrides.get("write_back_manager"):
            return override  # type: ignore[no-any-return]
        from ontology.services.write_back_manager import WriteBackManager

        return WriteBackManager()

    @property
    def outbox_executor(self) -> OutboxExecutor:
        """OutboxExecutor bound to a FRESH metadata session.

        The background loop polls outbox records and writes back to external
        systems — it must NOT share a session with request-scoped services.
        Each access builds a fresh PostgresMetaStore so the loop is isolated
        from the request session lifecycle.

        action-sync-outbox-design.md §8.5/§8.7: 注入 index_store (INDEX effect
        同步 Doris) + metadata_factory (DELETE 查 ObjectType 拿 PK 列名)。
        """
        if override := self.service_overrides.get("outbox_executor"):
            return override  # type: ignore[no-any-return]
        from ontology.services.outbox_executor import OutboxExecutor

        return OutboxExecutor(
            metadata=self.metadata,
            write_back_manager=self.write_back_manager,
            action_service=self.action_service,
            index_store=self.index,
            metadata_factory=lambda: self.metadata,
            graph_projector=self.graph_projector,
            geotime_projector=self.geotime_projector,
            embedding_provider=self.embedding_provider,
        )

    @property
    def conflict_detector(self) -> ConflictDetector:
        if override := self.service_overrides.get("conflict_detector"):
            return override  # type: ignore[no-any-return]
        from ontology.services.conflict_detector import ConflictDetector

        return ConflictDetector(index=self.index, metadata=None, container=self)

    @property
    def iceberg_maintenance_service(self) -> IcebergMaintenanceService:
        """Iceberg table maintenance (optimize/expire_snapshots/remove_orphan_files).

        Path A 配套 (ADR-008 2nd revision): Iceberg 批式写累积小文件,
        Trino 原生 Iceberg connector 的 ALTER TABLE EXECUTE 定期治理。
        """
        if override := self.service_overrides.get("iceberg_maintenance_service"):
            return override  # type: ignore[no-any-return]
        from ontology.services.iceberg_maintenance_service import IcebergMaintenanceService

        return IcebergMaintenanceService(engine=self.engine)

    @property
    def sync_flush_scheduler(self) -> SyncFlushScheduler:
        """ARCHIVE outbox 微批归档到 Iceberg + outbox 清理 (action-sync-outbox-design.md)。

        注入 dataset (IcebergStore.merge) + metadata_factory (claim/mark/
        查 ObjectType PK)。metadata_factory 每次返回独立 session 的
        PostgresMetaStore (后台循环不能复用请求级 session)。
        """
        if override := self.service_overrides.get("sync_flush_scheduler"):
            return override  # type: ignore[no-any-return]
        from ontology.services.sync_flush_scheduler import SyncFlushScheduler

        return SyncFlushScheduler(
            dataset=self.dataset,
            metadata_factory=lambda: self.metadata,
        )


# Singleton instance for FastAPI dependency injection
container = Container()
