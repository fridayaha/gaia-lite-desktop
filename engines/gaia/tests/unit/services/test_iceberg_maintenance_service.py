"""Unit tests for IcebergMaintenanceService — Trino ALTER TABLE EXECUTE maintenance."""

from unittest.mock import AsyncMock

import pytest

from ontology.services.iceberg_maintenance_service import (
    DEFAULT_OPTIMIZE_FILE_SIZE_THRESHOLD,
    DEFAULT_SNAPSHOT_RETENTION,
    IcebergMaintenanceService,
)


@pytest.fixture
def mock_engine() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(mock_engine: AsyncMock) -> IcebergMaintenanceService:
    return IcebergMaintenanceService(engine=mock_engine)


class TestOptimize:
    @pytest.mark.asyncio
    async def test_optimize_emits_alter_table_execute(self, service, mock_engine):
        """optimize runs ALTER TABLE ... EXECUTE optimize with file_size_threshold."""
        mock_engine.query.return_value = [{"added_files": 0, "rewritten_files": 3}]
        await service.optimize("LeadAllocateRecord")
        sql = mock_engine.query.call_args.args[0]
        assert sql == (
            "ALTER TABLE iceberg.ontology.lead_allocate_record "
            f"EXECUTE optimize(file_size_threshold => '{DEFAULT_OPTIMIZE_FILE_SIZE_THRESHOLD}')"
        )
        mock_engine.query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_optimize_custom_threshold(self, service, mock_engine):
        await service.optimize("Order", file_size_threshold="64MB")
        sql = mock_engine.query.call_args.args[0]
        assert "file_size_threshold => '64MB'" in sql

    @pytest.mark.asyncio
    async def test_optimize_snake_cases_type_name(self, service, mock_engine):
        """PascalCase ObjectType api_name → snake_case Iceberg table name."""
        mock_engine.query.return_value = []
        await service.optimize("FlightStatusLog")
        sql = mock_engine.query.call_args.args[0]
        assert "iceberg.ontology.flight_status_log" in sql


class TestExpireSnapshots:
    @pytest.mark.asyncio
    async def test_expire_snapshots_default_retention(self, service, mock_engine):
        mock_engine.query.return_value = []
        await service.expire_snapshots("Order")
        sql = mock_engine.query.call_args.args[0]
        assert "EXECUTE expire_snapshots(retention_threshold =>" in sql
        assert DEFAULT_SNAPSHOT_RETENTION in sql

    @pytest.mark.asyncio
    async def test_expire_snapshots_custom_retention(self, service, mock_engine):
        await service.expire_snapshots("Order", retention_threshold="30d")
        sql = mock_engine.query.call_args.args[0]
        assert "retention_threshold => '30d'" in sql


class TestRemoveOrphanFiles:
    @pytest.mark.asyncio
    async def test_remove_orphan_files(self, service, mock_engine):
        mock_engine.query.return_value = []
        await service.remove_orphan_files("Order")
        sql = mock_engine.query.call_args.args[0]
        assert "EXECUTE remove_orphan_files(retention_threshold =>" in sql


class TestRunMaintenanceOnce:
    @pytest.mark.asyncio
    async def test_runs_all_three_ops(self, service, mock_engine):
        """run_maintenance_once calls optimize + expire_snapshots + remove_orphan_files."""
        mock_engine.query.return_value = []
        summary = await service.run_maintenance_once("Order")
        assert mock_engine.query.await_count == 3
        ops_called = [c.args[0].split("EXECUTE ")[1].split("(")[0] for c in mock_engine.query.call_args_list]
        assert set(ops_called) == {"optimize", "expire_snapshots", "remove_orphan_files"}
        assert summary["object_type"] == "Order"
        assert summary["optimize"] == "ok"
        assert summary["expire_snapshots"] == "ok"
        assert summary["remove_orphan_files"] == "ok"

    @pytest.mark.asyncio
    async def test_one_op_failure_does_not_block_others(self, service, mock_engine):
        """A failure in one op is recorded but the others still run."""
        call_count = 0

        async def _side_effect(sql, params=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # optimize fails
                raise RuntimeError("optimize boom")
            return []

        mock_engine.query.side_effect = _side_effect
        summary = await service.run_maintenance_once("Order")
        assert mock_engine.query.await_count == 3  # all three attempted
        assert "failed" in summary["optimize"]
        assert summary["expire_snapshots"] == "ok"
        assert summary["remove_orphan_files"] == "ok"
