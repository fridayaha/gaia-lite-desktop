"""Unit tests for edition-conditional lifespan background tasks (A4 + B1).

Verifies that under ``edition == "lite"`` the lifespan starts *no* cloud
background tasks but DOES run the lite startup path — ``Base.metadata.create_all``
on SQLite + ``bootstrap_default_containers`` (B1) — while ``edition == "full"``
starts all seven background loops and the full startup bootstrapping + catalog
reconcile. Cloud services are mocked so no real Layer/Doris/Neo4j/PG connection
is made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.config.container import container
from ontology.config.settings import settings


def _make_service_mock(*run_names: str) -> MagicMock:
    """A fake service whose ``run_*`` coroutines complete immediately."""
    svc = MagicMock()
    for name in run_names:
        setattr(svc, name, AsyncMock(return_value=None))
    svc.close = AsyncMock(return_value=None)
    return svc


@pytest.fixture
def mocked_container(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Override the singleton container's cloud services with mocks and patch
    the startup/shutdown helpers that touch real external systems.

    Snapshots ``service_overrides`` / ``_service_cache`` so the shared
    singleton is restored after the test.
    """
    outbox = _make_service_mock("run_forever")
    sync_flush = _make_service_mock("run_flush_loop", "run_cleanup_loop")
    conflict = _make_service_mock("run_audit_loop")
    iceberg_maint = _make_service_mock("run_maintenance_loop")
    pipeline_recon = _make_service_mock("run_reconcile_loop")
    datasource = _make_service_mock("run_health_check_loop")
    datasource.reconcile_catalogs = AsyncMock(return_value=0)

    overrides = {
        "outbox_executor": outbox,
        "sync_flush_scheduler": sync_flush,
        "conflict_detector": conflict,
        "iceberg_maintenance_service": iceberg_maint,
        "pipeline_build_reconciler": pipeline_recon,
        "datasource_service": datasource,
    }
    saved_overrides = dict(container.service_overrides)
    saved_cache = dict(container._service_cache)
    container.service_overrides.update(overrides)

    # Startup helpers (imported lazily inside lifespan).
    boot_session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=boot_session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("ontology.config.database.async_session_factory", session_factory)
    monkeypatch.setattr(
        "ontology.services.permission_bootstrap.bootstrap_default_containers",
        AsyncMock(return_value=None),
    )
    # Shutdown resource closes (imported lazily inside lifespan finally).
    # engine 也支持 .begin() 异步上下文管理器（B1 lite 用它跑 create_all）。
    engine = MagicMock()
    engine.dispose = AsyncMock(return_value=None)
    begin_conn = MagicMock()
    begin_conn.run_sync = AsyncMock(return_value=None)
    engine.begin.return_value.__aenter__ = AsyncMock(return_value=begin_conn)
    engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr("ontology.config.database.engine", engine)
    monkeypatch.setattr("ontology.layers.index.doris_index_store.close_pool", AsyncMock(return_value=None))
    monkeypatch.setattr("ontology.layers.graph.neo4j_graph_store.close_driver", AsyncMock(return_value=None))

    yield {
        "outbox": outbox,
        "sync_flush": sync_flush,
        "conflict": conflict,
        "iceberg_maint": iceberg_maint,
        "pipeline_recon": pipeline_recon,
        "datasource": datasource,
        "bootstrap": __import__(
            "ontology.services.permission_bootstrap", fromlist=["bootstrap_default_containers"]
        ).bootstrap_default_containers,
        "engine": engine,
        "begin_conn": begin_conn,
    }

    container.service_overrides.clear()
    container.service_overrides.update(saved_overrides)
    container._service_cache.clear()
    container._service_cache.update(saved_cache)


class TestLiteLifespan:
    async def test_lite_skips_all_background_tasks(
        self, mocked_container: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "edition", "lite")
        from ontology.main import lifespan

        async with lifespan(MagicMock()):
            pass

        m = mocked_container
        m["outbox"].run_forever.assert_not_called()
        m["sync_flush"].run_flush_loop.assert_not_called()
        m["sync_flush"].run_cleanup_loop.assert_not_called()
        m["conflict"].run_audit_loop.assert_not_called()
        m["iceberg_maint"].run_maintenance_loop.assert_not_called()
        m["pipeline_recon"].run_reconcile_loop.assert_not_called()
        m["datasource"].run_health_check_loop.assert_not_called()
        # lite 不跑云版启动自愈（catalog reconcile 是 Gravitino 自愈，lite 无 Gravitino）。
        m["datasource"].reconcile_catalogs.assert_not_called()
        # B1: lite 走自己的启动路径——SQLite create_all + bootstrap_default_containers。
        m["begin_conn"].run_sync.assert_awaited_once()  # Base.metadata.create_all
        m["bootstrap"].assert_awaited_once()
        # lite 不访问 outbox_executor（否则会构造真 OutboxExecutor 并抛 EditionUnavailableError）。
        m["outbox"].close.assert_not_called()


class TestFullLifespan:
    async def test_full_starts_all_background_tasks(
        self, mocked_container: dict[str, MagicMock], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "edition", "full")
        from ontology.main import lifespan

        async with lifespan(MagicMock()):
            # 让事件循环跑一轮，使被 create_task 调度的 AsyncMock 协程真正执行
            # （否则它们在 yield 后、cancel 前没机会运行 → await_count=0）。
            import asyncio

            await asyncio.sleep(0)

        m = mocked_container
        m["outbox"].run_forever.assert_awaited_once()
        m["sync_flush"].run_flush_loop.assert_awaited_once()
        m["sync_flush"].run_cleanup_loop.assert_awaited_once()
        m["conflict"].run_audit_loop.assert_awaited_once()
        m["iceberg_maint"].run_maintenance_loop.assert_awaited_once()
        m["pipeline_recon"].run_reconcile_loop.assert_awaited_once()
        m["datasource"].run_health_check_loop.assert_awaited_once()
        # full 跑启动自愈。
        m["datasource"].reconcile_catalogs.assert_awaited_once()
        m["bootstrap"].assert_awaited_once()
        m["outbox"].close.assert_awaited_once()
