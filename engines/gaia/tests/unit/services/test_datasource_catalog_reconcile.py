"""Unit tests for ``DataSourceService.reconcile_catalogs`` — catalog self-heal.

Gravitino catalogs are stateless configuration registered at data-source
create time. They can be lost when Gravitino is rebuilt/upgraded (its
PG-backed metadata reset) while Gaia's ``data_sources`` table still records
them as present. ``reconcile_catalogs`` detects the drift and re-registers
the missing catalogs (idempotent, best-effort).

These tests verify:
  - Missing catalog → re-registered, status flipped to CONNECTED.
  - Present catalog → skipped (no re-register call).
  - Non-JDBC sources (no catalog) → skipped.
  - Gravitino unreachable → returns 0, does not crash.
  - Single re-register failure → logged, others continue.
  - explore() self-heals on CatalogNotRegisteredError then retries.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ontology.core.exceptions import CatalogNotRegisteredError
from ontology.core.schemas.datasource import DataSource, ExploreResult
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.engine.trino_query_engine import TrinoQueryEngine
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine
from ontology.services.datasource_service import DataSourceService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock(spec=PostgresMetaStore)


@pytest.fixture
def mock_catalog() -> AsyncMock:
    return AsyncMock(spec=GravitinoRegistry)


@pytest.fixture
def mock_engine() -> AsyncMock:
    return AsyncMock(spec=TrinoQueryEngine)


@pytest.fixture
def mock_pipeline() -> AsyncMock:
    return AsyncMock(spec=SeaTunnelEngine)


@pytest.fixture
def mock_dataset() -> AsyncMock:
    return AsyncMock(spec=IcebergStore)


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_engine, mock_pipeline, mock_dataset) -> DataSourceService:
    return DataSourceService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        engine=mock_engine,
        pipeline=mock_pipeline,
        dataset=mock_dataset,
    )


def _make_ds(
    api_name: str = "xiaoling",
    connector_type: str = "postgresql",
    status: str = "CONNECTED",
    catalog_name: str | None = None,
) -> DataSource:
    return DataSource(
        id="ds1",
        api_name=api_name,
        display_name=api_name,
        description="",
        connector_type=connector_type,
        connector_config={
            "host": "gaia-postgres",
            "port": "5432",
            "database": api_name,
            "username": "ontology",
            "password": "ontology",
        },
        credential_id=None,
        status=status,
        gravitino_catalog_name=catalog_name or api_name,
        capabilities=["explore", "sync", "sample"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestReconcileCatalogs:
    @pytest.mark.asyncio
    async def test_missing_catalog_is_re_registered(self, service, mock_catalog, mock_metadata):
        """A JDBC datasource whose catalog is absent from Gravitino gets re-registered."""
        mock_catalog.list_catalogs.return_value = []  # Gravitino has no catalogs
        mock_metadata.list_datasources.return_value = [_make_ds("xiaoling", status="ERROR")]
        service._register_datasource_catalog = AsyncMock()

        healed = await service.reconcile_catalogs()

        assert healed == 1
        service._register_datasource_catalog.assert_awaited_once()
        # status flipped back to CONNECTED after successful re-register
        mock_metadata.update_datasource.assert_awaited_once_with("xiaoling", {"status": "CONNECTED"})

    @pytest.mark.asyncio
    async def test_present_catalog_is_skipped(self, service, mock_catalog, mock_metadata):
        """A datasource whose catalog already exists in Gravitino is left alone."""
        mock_catalog.list_catalogs.return_value = [{"name": "xiaoling"}]
        mock_metadata.list_datasources.return_value = [_make_ds("xiaoling")]
        service._register_datasource_catalog = AsyncMock()

        healed = await service.reconcile_catalogs()

        assert healed == 0
        service._register_datasource_catalog.assert_not_awaited()
        mock_metadata.update_datasource.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_jdbc_source_is_skipped(self, service, mock_catalog, mock_metadata):
        """File/Kafka/NoSQL sources have no Gravitino catalog — never reconciled."""
        mock_catalog.list_catalogs.return_value = []
        # s3 is a Fileset-catalog source, not JDBC — _JDBC_CONNECTOR_MAP returns None
        mock_metadata.list_datasources.return_value = [_make_ds("my_s3", connector_type="s3")]
        service._register_datasource_catalog = AsyncMock()

        healed = await service.reconcile_catalogs()

        assert healed == 0
        service._register_datasource_catalog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_gravitino_unreachable_returns_zero(self, service, mock_catalog):
        """If Gravitino itself is down, reconcile is a no-op (returns 0), never raises."""
        mock_catalog.list_catalogs.side_effect = RuntimeError("Gravitino 503")

        healed = await service.reconcile_catalogs()

        assert healed == 0

    @pytest.mark.asyncio
    async def test_single_register_failure_does_not_abort_others(self, service, mock_catalog, mock_metadata):
        """One datasource failing to re-register must not block the rest."""
        mock_catalog.list_catalogs.return_value = []
        good = _make_ds("good_pg")
        bad = _make_ds("bad_pg")
        mock_metadata.list_datasources.return_value = [bad, good]

        call_count = 0

        async def _register(ds):
            nonlocal call_count
            call_count += 1
            if ds.api_name == "bad_pg":
                raise RuntimeError("simulated registration failure")

        service._register_datasource_catalog = _register

        healed = await service.reconcile_catalogs()

        # bad failed, good succeeded → only good counted
        assert call_count == 2
        assert healed == 1
        # good's status was CONNECTED already, so no update call for it;
        # bad failed so no update call for it either
        mock_metadata.update_datasource.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_connected_status_not_rewritten_when_already_connected(
        self, service, mock_catalog, mock_metadata
    ):
        """Re-register success on an already-CONNECTED source skips the redundant status write."""
        mock_catalog.list_catalogs.return_value = []
        mock_metadata.list_datasources.return_value = [_make_ds("xiaoling", status="CONNECTED")]
        service._register_datasource_catalog = AsyncMock()

        healed = await service.reconcile_catalogs()

        assert healed == 1
        # status already CONNECTED → no update needed
        mock_metadata.update_datasource.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_datasources_failure_returns_zero(self, service, mock_catalog, mock_metadata):
        """If PG is down (list_datasources fails), reconcile returns 0, never raises."""
        mock_catalog.list_catalogs.return_value = []
        mock_metadata.list_datasources.side_effect = RuntimeError("PG down")

        healed = await service.reconcile_catalogs()

        assert healed == 0


class TestExploreSelfHeal:
    """explore() should self-heal on CatalogNotRegisteredError then retry."""

    @pytest.mark.asyncio
    async def test_explore_self_heals_on_catalog_not_registered(self, service, mock_metadata, mock_engine):
        """When list_tables raises CatalogNotRegisteredError, explore re-registers and retries."""
        ds = _make_ds("xiaoling")
        mock_metadata.get_datasource.return_value = ds
        mock_engine.list_tables.side_effect = [
            CatalogNotRegisteredError("missing", code="CATALOG_NOT_REGISTERED"),
            ["public.users", "public.orders"],  # retry succeeds
        ]
        service._register_datasource_catalog = AsyncMock()

        result = await service.explore("xiaoling")

        assert isinstance(result, ExploreResult)
        assert len(result.tables) == 2
        service._register_datasource_catalog.assert_awaited_once_with(ds)
        assert mock_engine.list_tables.await_count == 2  # initial fail + retry

    @pytest.mark.asyncio
    async def test_explore_raises_when_self_heal_fails(self, service, mock_metadata, mock_engine):
        """If re-registration itself fails, the original CatalogNotRegisteredError propagates."""
        ds = _make_ds("xiaoling")
        mock_metadata.get_datasource.return_value = ds
        catalog_err = CatalogNotRegisteredError("missing", code="CATALOG_NOT_REGISTERED")
        mock_engine.list_tables.side_effect = catalog_err

        async def _register_fails(_ds):
            raise RuntimeError("Gravitino 500")

        service._register_datasource_catalog = _register_fails

        with pytest.raises(CatalogNotRegisteredError):
            await service.explore("xiaoling")

        # list_tables called only once (the failed attempt); no retry
        assert mock_engine.list_tables.await_count == 1
