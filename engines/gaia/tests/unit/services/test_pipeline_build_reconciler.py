"""Unit tests for PipelineBuildReconciler — stuck execution reconciliation.

Verifies the background loop's per-iteration logic: finds stuck PENDING/
RUNNING executions, queries Kestra, and aligns DB state + state_history.
Does NOT test the loop itself (asyncio.sleep-based) — only _reconcile_one.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.models.pipeline import PipelineExecutionModel
from ontology.services.pipeline_build_reconciler import PipelineBuildReconciler


def _make_execution(
    *,
    state: str = "RUNNING",
    kestra_id: str | None = "kestra-exec-123",
    age_minutes: int = 10,
) -> PipelineExecutionModel:
    from ontology.core.models.defaults import utcnow

    now = utcnow()
    return PipelineExecutionModel(
        id="exec-1",
        pipeline_id="pipe-1",
        version_id="ver-1",
        trigger_type="MANUAL",
        current_state=state,
        kestra_execution_id=kestra_id,
        state_started_at=now - timedelta(minutes=age_minutes),
        created_at=now - timedelta(minutes=age_minutes),
    )


class TestReconcileOne:
    """_reconcile_one aligns DB state with Kestra's real state."""

    @pytest.fixture
    def reconciler(self) -> PipelineBuildReconciler:
        container = MagicMock()
        return PipelineBuildReconciler(container)

    async def test_kestra_success_marks_db_success(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """DB RUNNING + Kestra SUCCESS → DB updated to SUCCESS + state_history."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        kestra = AsyncMock()
        kestra.get_build_status = AsyncMock(return_value={"state": "SUCCESS"})

        execution = _make_execution(state="RUNNING")
        await reconciler._reconcile_one(session, kestra, execution)

        assert execution.current_state == "SUCCESS"
        assert execution.finished_at is not None
        # state_history record added
        session.add.assert_called_once()

    async def test_kestra_failed_marks_db_failed(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """DB RUNNING + Kestra FAILED → DB updated to FAILED + error_message."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        kestra = AsyncMock()
        kestra.get_build_status = AsyncMock(
            return_value={"state": "FAILED", "error": "OOM"}
        )

        execution = _make_execution(state="RUNNING")
        await reconciler._reconcile_one(session, kestra, execution)

        assert execution.current_state == "FAILED"
        assert execution.error_message == "OOM"

    async def test_kestra_killed_marks_cancelled(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """DB RUNNING + Kestra KILLED → DB updated to CANCELLED."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        kestra = AsyncMock()
        kestra.get_build_status = AsyncMock(return_value={"state": "KILLED"})

        execution = _make_execution(state="RUNNING")
        await reconciler._reconcile_one(session, kestra, execution)

        assert execution.current_state == "CANCELLED"

    async def test_no_divergence_no_update(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """DB RUNNING + Kestra RUNNING → no update."""
        session = AsyncMock()
        kestra = AsyncMock()
        kestra.get_build_status = AsyncMock(return_value={"state": "RUNNING"})

        execution = _make_execution(state="RUNNING")
        await reconciler._reconcile_one(session, kestra, execution)

        assert execution.current_state == "RUNNING"
        session.add.assert_not_called()

    async def test_pending_no_kestra_id_marks_failed(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """PENDING with no kestra_execution_id (trigger crashed before Kestra call)."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        kestra = AsyncMock()

        execution = _make_execution(state="PENDING", kestra_id=None)
        await reconciler._reconcile_one(session, kestra, execution)

        assert execution.current_state == "FAILED"
        # state_history record added with the reason
        session.add.assert_called_once()

    async def test_kestra_unreachable_skips(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """Kestra query failure → skip this execution (retry next round)."""
        session = AsyncMock()
        kestra = AsyncMock()
        kestra.get_build_status = AsyncMock(side_effect=RuntimeError("conn refused"))

        execution = _make_execution(state="RUNNING")
        await reconciler._reconcile_one(session, kestra, execution)

        # State unchanged (will retry next round)
        assert execution.current_state == "RUNNING"
        session.add.assert_not_called()

    async def test_unknown_kestra_state_skips(
        self, reconciler: PipelineBuildReconciler
    ) -> None:
        """Unknown Kestra state (not in map) → skip (retry next round)."""
        session = AsyncMock()
        kestra = AsyncMock()
        kestra.get_build_status = AsyncMock(return_value={"state": "WEIRD_STATE"})

        execution = _make_execution(state="RUNNING")
        await reconciler._reconcile_one(session, kestra, execution)

        assert execution.current_state == "RUNNING"
        session.add.assert_not_called()
