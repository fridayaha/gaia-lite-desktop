"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from ontology.__init__ import __version__
from ontology.config.settings import settings
from ontology.core.exceptions import OntologyError
from ontology.middleware.auth import AuthMiddleware
from ontology.middleware.error_handler import generic_error_handler, ontology_error_handler
from ontology.middleware.tracing import TraceIDMiddleware
from ontology.observability.metrics import metrics_endpoint
from ontology.routes.action import router as action_router
from ontology.routes.admin import router as admin_router
from ontology.routes.ai import router as ai_router
from ontology.routes.auth import router as auth_router
from ontology.routes.authz import router as authz_router
from ontology.routes.containers import router as containers_router
from ontology.routes.datasource import router as datasource_router
from ontology.routes.identity import router as identity_router
from ontology.routes.marking import router as marking_router
from ontology.routes.ontology import router as ontology_router
from ontology.routes.pipeline_builder import router as pipeline_builder_router
from ontology.routes.query import router as query_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: start background tasks, dispose DB on shutdown.

    lite 版（settings.edition == "lite"）跳过全部云版后台任务与启动自愈——
    这些任务依赖 Doris/Iceberg/Trino/Gravitino/Neo4j 等 lite 不装的 Layer，
    访问对应 property 即抛 EditionUnavailableError。桌面版的简化后台编排
    （如同步 outbox）见 B5。full 版行为与历史一致。
    """
    import asyncio
    import logging
    from collections.abc import Coroutine
    from typing import Any

    from ontology.config.container import container

    _log = logging.getLogger(__name__)
    is_full = settings.edition == "full"

    bg_tasks: list[asyncio.Task[object]] = []

    # A background-task exception must NOT crash the app — log + continue.
    # (Trino may reject maintenance on non-existent tables during dev; the
    # loop retries on its next tick.)
    def _log_bg_failure(t: asyncio.Task[object]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            _log.warning("Background task %s failed (non-fatal): %s", t.get_name(), exc)

    def _start(name: str, coro: Coroutine[Any, Any, object]) -> None:
        task = asyncio.create_task(coro)
        task.add_done_callback(_log_bg_failure)
        bg_tasks.append(task)
        _log.info("%s started", name)

    if is_full:
        # OutboxExecutor: 消费 PENDING outbox（webhooks / write-backs / INDEX→Doris）。
        # action-sync-outbox-design.md: INDEX effect 近实时同步 object_state 到 Doris。
        _start("OutboxExecutor background task", container.outbox_executor.run_forever())
        # SyncFlushScheduler: ARCHIVE outbox 微批归档到 Iceberg (60s) + outbox 7d 清理 (1h)。
        _start("SyncFlushScheduler flush loop", container.sync_flush_scheduler.run_flush_loop())
        _start("SyncFlushScheduler cleanup loop", container.sync_flush_scheduler.run_cleanup_loop())
        # ConflictDetector: PG object_state vs Iceberg snapshot 审计 (best-effort)。
        _start("ConflictDetector audit loop", container.conflict_detector.run_audit_loop())
        # IcebergMaintenanceService: 定期 optimize/expire_snapshots (Trino, path A 配套)。
        _start(
            "IcebergMaintenanceService loop",
            container.iceberg_maintenance_service.run_maintenance_loop(container),
        )
        # PipelineBuildReconciler: PG pipeline_executions vs Kestra 状态对账 (ADR-018 D8)。
        _start("PipelineBuildReconciler loop", container.pipeline_build_reconciler.run_reconcile_loop())
        # DataSource health-check: 探活 ERROR/DISCONNECTED JDBC 源自动恢复 (60s)。
        _start(
            "DataSource health-check loop",
            container.datasource_service.run_health_check_loop(interval=60),
        )
    else:
        _log.info(
            "lite edition: 跳过全部云版后台任务"
            "（outbox/sync_flush/conflict/iceberg_maintenance/pipeline_reconcile/datasource_health）"
        )

    try:
        if not is_full:
            # lite 跳过 Alembic（迁移含 PG-only 构造），改由 ORM 直接建 SQLite
            # 空库。须先 import 全部 model 模块让表注册进 Base.metadata
            # （ontology/datasource/permission/pipeline 共 49 张表）。
            from ontology.config.database import engine as _engine
            from ontology.core.models import Base

            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            _log.info("lite edition: SQLite schema created via Base.metadata.create_all")

            # seed 默认 Organization/Space/Project + 内置角色（与 full 同幂等
            # 逻辑，permission_bootstrap 仅写 PG/SQLite 元数据，无重依赖）。
            # ObjectType.project_id NOT NULL FK→projects，无此 seed 则
            # OntologyService 的 _resolve_default_project_for_space 返回 None
            # → 落 sentinel → FK 违规，本体 CRUD 起不来。
            from ontology.config.database import async_session_factory
            from ontology.services.permission_bootstrap import bootstrap_default_containers

            async with async_session_factory() as boot_session:
                await bootstrap_default_containers(boot_session)
            _log.info("lite edition: default containers bootstrapped")

        if is_full:
            # ADR-016 permission governance (Phase 0): seed the default
            # Space + Project (and adopt orphan Ontologies) on startup. The
            # default Organization is seeded by the Alembic migration; the
            # Space/Project need Service logic (1:1 Ontology pairing) so they
            # live in the lifespan. Idempotent — no-op if already present.
            from ontology.config.database import async_session_factory
            from ontology.services.permission_bootstrap import bootstrap_default_containers

            async with async_session_factory() as boot_session:
                await bootstrap_default_containers(boot_session)
            _log.info("Permission default containers bootstrapped")

            # Catalog 一致性自愈：启动时立即跑一次 reconcile_catalogs，把 Gravitino
            # 重建/升级后丢失的 catalog 补回来（不等首个 60s 健康检查 tick）。
            # catalog 是无状态配置，重注册幂等且不丢源数据。best-effort，失败只 log。
            try:
                healed = await container.datasource_service.reconcile_catalogs()
                if healed:
                    _log.info(
                        "Startup catalog reconcile: re-registered %d missing catalog(s)",
                        healed,
                    )
            except Exception as exc:  # noqa: BLE001 — 启动自愈失败不阻断应用启动
                _log.warning("Startup catalog reconcile failed (non-fatal): %s", exc)

        yield
    finally:
        for task in bg_tasks:
            task.cancel()
        for task in bg_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if is_full:
            await container.outbox_executor.close()
            # Close the shared Doris connection pool (online read source).
            from ontology.layers.index.doris_index_store import close_pool

            await close_pool()
            # Close the shared Neo4j driver (Graph Layer, graph-reasoning-design.md).
            from ontology.layers.graph.neo4j_graph_store import close_driver

            await close_driver()
        # Close cached service sessions (D7 fix: AG-UI toolsets cache services
        # on the container; release their sessions on shutdown).
        await container.aclose()
        from ontology.config.database import engine

        await engine.dispose()


app = FastAPI(
    title="Ontology API",
    description="Palantir-style Ontology API with layered open-source architecture",
    version=__version__,
    lifespan=lifespan,
)


# ── Middleware ──
# Execution order on request entry (last added runs first): CORS → TraceID → Auth.
# CORS outermost (handles preflight before anything else), TraceID next
# (stamps trace_id for all downstream logs), Auth innermost (resolves
# Principal just before routing so request.state.principal is ready).
app.add_middleware(TraceIDMiddleware)
app.add_middleware(AuthMiddleware)

# CORS — the AG-UI /ai/agent endpoint is consumed from the browser via SSE,
# so the web-ui origin must be allow-listed. expose_headers prevents
# proxies (Nginx) from buffering the SSE stream.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Accel-Buffering"],
)

# ── Exception Handlers ──
app.add_exception_handler(OntologyError, ontology_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, generic_error_handler)

# ── Routers ──
app.include_router(ontology_router)
app.include_router(pipeline_builder_router)
app.include_router(query_router)
app.include_router(action_router)
app.include_router(ai_router)
app.include_router(datasource_router)
app.include_router(auth_router)
app.include_router(authz_router)
app.include_router(marking_router)
app.include_router(identity_router)
app.include_router(containers_router)
app.include_router(admin_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    data = metrics_endpoint()
    return Response(content=data, media_type="text/plain; charset=utf-8")
