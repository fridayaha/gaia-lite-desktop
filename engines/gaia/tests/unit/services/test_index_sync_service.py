"""Unit tests for IndexSyncService (schema-only, post T1.10).

Validates Doris index-table **schema** provisioning (DDL only). The legacy
SeaTunnel INDEX pipeline + backfill + sync_now were removed
(handoff-rid-funnel-closure.md T1.10): data sync now lives in
OutboxExecutor (Action path) and ObjectIndexFunnel (external ingestion).
Both layers are mocked.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ontology.core.exceptions import IndexProvisionError, OntologyError
from ontology.services.index_sync_service import IndexSyncService

_ONT = "shop"
_TYPE = "order"


@pytest.fixture
def mock_index() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(mock_index) -> IndexSyncService:
    return IndexSyncService(index=mock_index)


def _props() -> list[SimpleNamespace]:
    """PK + one indexed string column."""
    return [
        SimpleNamespace(
            api_name="id",
            data_type="STRING",
            is_primary_key=True,
            indexed=False,
            backing_column="id",
        ),
        SimpleNamespace(
            api_name="status",
            data_type="STRING",
            is_primary_key=False,
            indexed=True,
            backing_column="status",
        ),
    ]


class TestProvision:
    @pytest.mark.asyncio
    async def test_provision_creates_table_only(self, service, mock_index):
        """T1.10: provision does DDL only — no SeaTunnel pipeline to start."""
        fields = await service.provision(_ONT, _TYPE, _props())

        assert len(fields) == 2
        mock_index.drop_index_table.assert_awaited_once_with(_ONT, _TYPE)
        mock_index.create_index_table.assert_awaited_once()
        kwargs = mock_index.create_index_table.call_args.kwargs
        assert kwargs["ontology_api_name"] == _ONT
        assert kwargs["object_type_api_name"] == _TYPE
        names = [f["name"] for f in kwargs["fields"]]
        assert "id" in names and "status" in names

    @pytest.mark.asyncio
    async def test_provision_doris_failure_wraps_as_provision_error(self, service, mock_index):
        """Doris DDL failure → IndexProvisionError (non-fatal to caller)."""
        mock_index.create_index_table.side_effect = OntologyError("boom")
        with pytest.raises(IndexProvisionError):
            await service.provision(_ONT, _TYPE, _props())


class TestRebuild:
    @pytest.mark.asyncio
    async def test_rebuild_recreates_table_only(self, service, mock_index):
        """T1.10: rebuild does DDL only — no pipeline update."""
        await service.rebuild(_ONT, _TYPE, _props())
        mock_index.drop_index_table.assert_awaited_once_with(_ONT, _TYPE)
        mock_index.create_index_table.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rebuild_doris_failure_wraps_as_provision_error(self, service, mock_index):
        mock_index.create_index_table.side_effect = OntologyError("boom")
        with pytest.raises(IndexProvisionError):
            await service.rebuild(_ONT, _TYPE, _props())


class TestDeprovision:
    @pytest.mark.asyncio
    async def test_deprovision_drops_table_only(self, service, mock_index):
        """T1.10: deprovision drops the table — no SeaTunnel pipeline stop."""
        await service.deprovision(_ONT, _TYPE)
        mock_index.drop_index_table.assert_awaited_once_with(_ONT, _TYPE)

    @pytest.mark.asyncio
    async def test_deprovision_best_effort_on_drop_failure(self, service, mock_index):
        """Drop failure is logged, not raised — delete must not be blocked."""
        mock_index.drop_index_table.side_effect = OntologyError("drop failed")
        # Should not raise
        await service.deprovision(_ONT, _TYPE)
