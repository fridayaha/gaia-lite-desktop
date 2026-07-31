"""Unit tests for ConflictDetector.

2026-07-08: audit target switched from Iceberg (version equality) to Doris
(presence check — object_state has it, does Doris?). Detects outbox INDEX
effect consumption failures (missed writes). See conflict_detector.py docstring.
"""

from unittest.mock import AsyncMock

import pytest

from ontology.services.conflict_detector import ConflictDetector


@pytest.fixture
def mock_index() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def detector(mock_index) -> ConflictDetector:
    return ConflictDetector(index=mock_index)


class TestVerifyObjectStateConsistency:
    """Cross-check PG object_state presence against Doris idx table."""

    @pytest.mark.asyncio
    async def test_all_present_no_mismatches(self, detector, mock_index):
        """When Doris has all PKs, return empty mismatch list."""
        mock_index.load_by_ids.return_value = [
            {"flight_id": "CA123"},
            {"flight_id": "CA456"},
        ]

        mismatches = await detector.verify_object_state_consistency(
            ontology_api_name="Airline",
            object_type_api_name="Flight",
            pk_column="flight_id",
            object_pks=["CA123", "CA456"],
        )
        assert mismatches == []

    @pytest.mark.asyncio
    async def test_missing_in_doris_reported(self, detector, mock_index):
        """When Doris is missing a PK that object_state has, report it."""
        mock_index.load_by_ids.return_value = [
            {"flight_id": "CA456"},  # only CA456 present in Doris
        ]

        mismatches = await detector.verify_object_state_consistency(
            ontology_api_name="Airline",
            object_type_api_name="Flight",
            pk_column="flight_id",
            object_pks=["CA123", "CA456"],  # object_state has both
        )
        assert "CA123" in mismatches
        assert "CA456" not in mismatches

    @pytest.mark.asyncio
    async def test_doris_unavailable_propagates(self, detector, mock_index):
        """When Doris is unavailable, the error propagates (caller decides).

        run_audit_once catches this and marks index_unavailable; standalone
        callers may catch and treat as empty.
        """
        mock_index.load_by_ids.side_effect = RuntimeError("Doris down")

        with pytest.raises(RuntimeError, match="Doris down"):
            await detector.verify_object_state_consistency(
                ontology_api_name="Airline",
                object_type_api_name="Flight",
                pk_column="flight_id",
                object_pks=["CA123", "CA456"],
            )

    @pytest.mark.asyncio
    async def test_empty_pks_returns_empty(self, detector, mock_index):
        """No PKs to check → empty result, no Doris call."""
        mismatches = await detector.verify_object_state_consistency(
            ontology_api_name="Airline",
            object_type_api_name="Flight",
            pk_column="flight_id",
            object_pks=[],
        )
        assert mismatches == []
        mock_index.load_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_missing_in_doris(self, detector, mock_index):
        """When Doris has none of the PKs, report all as mismatches."""
        mock_index.load_by_ids.return_value = []

        mismatches = await detector.verify_object_state_consistency(
            ontology_api_name="Airline",
            object_type_api_name="Flight",
            pk_column="flight_id",
            object_pks=["CA123", "CA456"],
        )
        assert set(mismatches) == {"CA123", "CA456"}


class TestRunAuditOnce:
    """ConflictDetector.run_audit_once — PG object_state vs Doris reconciliation."""

    @pytest.mark.asyncio
    async def test_no_object_state_returns_zero_audited(self, mock_index):
        meta = AsyncMock()
        meta.get_object_states_by_type = AsyncMock(return_value=[])
        det = ConflictDetector(index=mock_index, metadata=meta)
        summary = await det.run_audit_once("Airline", "Flight", "flightId")
        assert summary["audited"] == 0
        assert summary["mismatches"] == []

    @pytest.mark.asyncio
    async def test_reports_mismatches(self, mock_index):
        """object_state has 2 objects, Doris missing 1 → report it."""
        meta = AsyncMock()
        meta.get_object_states_by_type = AsyncMock(
            return_value=[
                {"object_id": "o1", "properties": {"flight_id": "CA123"}},
                {"object_id": "o2", "properties": {"flight_id": "CA456"}},
            ]
        )
        # ObjectType with primary_key "flightId" → backing_column "flight_id"
        prop = AsyncMock()
        prop.api_name = "flightId"
        prop.backing_mapping = AsyncMock(backing_column="flight_id")
        ot = AsyncMock()
        ot.primary_key = "flightId"
        ot.properties = [prop]
        meta.get_object_type = AsyncMock(return_value=ot)
        # Doris missing CA123
        mock_index.load_by_ids.return_value = [{"flight_id": "CA456"}]

        det = ConflictDetector(index=mock_index, metadata=meta)
        summary = await det.run_audit_once("Airline", "Flight", "flightId")
        assert summary["audited"] == 2
        assert "CA123" in summary["mismatches"]

    @pytest.mark.asyncio
    async def test_doris_unavailable_is_best_effort(self, mock_index):
        meta = AsyncMock()
        meta.get_object_states_by_type = AsyncMock(
            return_value=[{"object_id": "o1", "properties": {"flight_id": "CA123"}}]
        )
        prop = AsyncMock()
        prop.api_name = "flightId"
        prop.backing_mapping = AsyncMock(backing_column="flight_id")
        ot = AsyncMock()
        ot.primary_key = "flightId"
        ot.properties = [prop]
        meta.get_object_type = AsyncMock(return_value=ot)
        mock_index.load_by_ids.side_effect = RuntimeError("boom")

        det = ConflictDetector(index=mock_index, metadata=meta)
        summary = await det.run_audit_once("Airline", "Flight", "flightId")
        assert summary["audited"] == 1
        assert summary["mismatches"] == []
        assert summary.get("index_unavailable") is True

    @pytest.mark.asyncio
    async def test_no_pk_backing_column_skips(self, mock_index):
        """ObjectType with no resolvable PK backing_column → skipped."""
        meta = AsyncMock()
        meta.get_object_states_by_type = AsyncMock(
            return_value=[{"object_id": "o1", "properties": {}}]
        )
        ot = AsyncMock()
        ot.primary_key = "flightId"
        ot.properties = []  # no properties → can't resolve backing_column
        meta.get_object_type = AsyncMock(return_value=ot)

        det = ConflictDetector(index=mock_index, metadata=meta)
        summary = await det.run_audit_once("Airline", "Flight", "flightId")
        assert summary["mismatches"] == []
        assert summary.get("skipped") == "no primary_key backing_column"


class TestAuditIterationExcludesVirtual:
    """ADR-021 §2.7：_audit_iteration 排除 VIRTUAL ObjectType。

    VIRTUAL 节点不可对账（外部源无 data_version），审计无基准。查询语句
    必须带 ``storage_type == 'MANAGED'`` 条件，让 VIRTUAL OT 不进审计循环。
    """

    @pytest.mark.asyncio
    async def test_query_filters_virtual_storage_type(self, detector, mock_index):
        """验证 _audit_iteration 的查询语句包含 storage_type == 'MANAGED'。"""
        from unittest.mock import MagicMock

        metadata = MagicMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        metadata.session.execute = AsyncMock(return_value=result_mock)

        await detector._audit_iteration(metadata)

        # 查询语句被传给 session.execute，校验编译后的 SQL 含 MANAGED 过滤
        call_args = metadata.session.execute.call_args
        compiled = str(call_args.args[0].compile(compile_kwargs={"literal_binds": True}))
        assert "MANAGED" in compiled
        assert "storage_type" in compiled

    @pytest.mark.asyncio
    async def test_empty_rows_no_audit_calls(self, detector, mock_index):
        """无 OT 时不调 run_audit_once。"""
        from unittest.mock import MagicMock

        metadata = MagicMock()
        result_mock = MagicMock()
        result_mock.all.return_value = []
        metadata.session.execute = AsyncMock(return_value=result_mock)

        detector.run_audit_once = AsyncMock()

        await detector._audit_iteration(metadata)

        detector.run_audit_once.assert_not_awaited()
