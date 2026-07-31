"""Pipeline build reconciliation — aligns DB execution state with Kestra.

Mirrors ``ConflictDetector`` (object_state ↔ Doris reconciliation): a
best-effort background loop that periodically scans ``pipeline_executions``
stuck in PENDING/RUNNING for too long, queries Kestra for the real state,
and updates the DB + state_history if they diverge.

Failure modes this catches (ADR-018 D8 D-3):
  - ``trigger_build`` inserted PENDING but crashed before calling Kestra
    → execution stays PENDING forever without a reconciler
  - Kestra completed/failed but the webhook/SSE callback was lost
    → execution stays RUNNING forever
  - Kestra killed the execution but the kill callback failed
    → execution stays RUNNING forever

The reconciler is the "eventual consistency" safety net for the cross-
system state machine (PG + Kestra). It does NOT replace the synchronous
state transitions in ``PipelineBuilderService.trigger_build`` — it only
fixes divergences the synchronous path missed.

Interval: 60s (builds are short-lived vs object_state sync; stuck builds
waste Kestra worker capacity so we want fast detection).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ontology.core.models.defaults import new_uuid, utcnow
from ontology.core.models.pipeline import (
    PipelineExecutionModel,
    PipelineStateHistoryModel,
)
from ontology.services._metadata_owner import MetadataOwnerMixin

if TYPE_CHECKING:
    from ontology.config.container import Container
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)

# Stuck threshold: PENDING/RUNNING executions older than this are reconciled.
_STUCK_THRESHOLD = timedelta(minutes=2)

# Kestra state → Gaia execution state mapping
_KESTRA_STATE_MAP: dict[str, str] = {
    "CREATED": "PENDING",
    "RUNNING": "RUNNING",
    "PAUSED": "RUNNING",
    "RESTARTED": "RUNNING",
    "KILLING": "CANCELLED",
    "SUCCESS": "SUCCESS",
    "WARNING": "SUCCESS",
    "FAILED": "FAILED",
    "KILLED": "CANCELLED",
    "CANCELLED": "CANCELLED",
}


class PipelineBuildReconciler(MetadataOwnerMixin):
    """Background reconciler: aligns PG execution state with Kestra.

    Like ``ConflictDetector``, it uses ``container.metadata_session()``
    per iteration (fresh session, properly closed) and swallows per-iter
    errors so the loop survives for the app lifetime.
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        # _metadata is None — we use container.metadata_session() per iter
        self._metadata = None

    async def run_reconcile_loop(self, interval: float = 60.0) -> None:
        """Background loop: periodically reconcile stuck executions.

        Best-effort: any per-iteration error is swallowed so the loop
        keeps running. Runs for the app lifetime; cancelled on shutdown.
        """
        _log.info("PipelineBuildReconciler loop started (interval=%.0fs)", interval)
        while True:
            try:
                async with self._container.metadata_session() as meta:
                    await self._reconcile_iteration(meta)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — loop must survive per-iter errors
                _log.exception("PipelineBuildReconciler loop error")
            await asyncio.sleep(interval)

    async def _reconcile_iteration(self, metadata: PostgresMetaStore) -> None:
        """One reconciliation pass: find stuck executions, query Kestra, update."""
        session = metadata._session  # noqa: SLF001 — pipeline tables not in layer methods
        cutoff = utcnow() - _STUCK_THRESHOLD

        # Find executions stuck in PENDING/RUNNING past the threshold
        stmt = select(PipelineExecutionModel).where(
            PipelineExecutionModel.current_state.in_(("PENDING", "RUNNING")),
            PipelineExecutionModel.created_at < cutoff,
        )
        result = await session.execute(stmt)
        stuck = result.scalars().all()

        if not stuck:
            return

        kestra = self._container.kestra_engine
        for execution in stuck:
            await self._reconcile_one(session, kestra, execution)

    async def _reconcile_one(
        self,
        session: Any,  # AsyncSession
        kestra: Any,  # KestraEngine
        execution: PipelineExecutionModel,
    ) -> None:
        """Reconcile a single stuck execution against Kestra's real state."""
        if not execution.kestra_execution_id:
            # PENDING with no kestra_execution_id for >threshold = trigger failed
            # to even call Kestra. Mark as FAILED (the synchronous path should
            # have done this, but crashes can skip it).
            if execution.current_state == "PENDING":
                _log.warning(
                    "Execution %s stuck PENDING with no Kestra id — marking FAILED",
                    execution.id,
                )
                await self._update_state(
                    session,
                    execution,
                    "FAILED",
                    reason="reconciler: stuck PENDING with no Kestra execution id",
                )
            return

        # Query Kestra for the real state
        try:
            kestra_exec = await kestra.get_build_status(execution.kestra_execution_id)
        except Exception as e:  # noqa: BLE001 — Kestra may be down; skip this round
            _log.warning(
                "Reconciler: Kestra query failed for execution %s: %s",
                execution.id,
                e,
            )
            return

        kestra_state = kestra_exec.get("state", "UNKNOWN") if isinstance(kestra_exec, dict) else "UNKNOWN"
        target_state = _KESTRA_STATE_MAP.get(kestra_state)

        if target_state is None or target_state == execution.current_state:
            return  # No divergence or unknown Kestra state (skip, retry next round)

        _log.info(
            "Reconciling execution %s: DB=%s → Kestra=%s → target=%s",
            execution.id,
            execution.current_state,
            kestra_state,
            target_state,
        )
        await self._update_state(
            session,
            execution,
            target_state,
            reason=f"reconciler: Kestra state={kestra_state}",
            error=kestra_exec.get("error") if isinstance(kestra_exec, dict) else None,
        )

    async def _update_state(
        self,
        session: Any,  # AsyncSession
        execution: PipelineExecutionModel,
        new_state: str,
        *,
        reason: str,
        error: str | None = None,
    ) -> None:
        """Update execution state + append state_history record (atomic)."""
        prev_state = execution.current_state
        now = utcnow()
        execution.current_state = new_state
        if new_state in ("SUCCESS", "FAILED", "CANCELLED"):
            execution.finished_at = now
            if execution.state_started_at:
                execution.duration_ms = int((now - execution.state_started_at).total_seconds() * 1000)
        if error:
            execution.error_message = error
        session.add(
            PipelineStateHistoryModel(
                id=new_uuid(),
                execution_id=execution.id,
                from_state=prev_state,
                to_state=new_state,
                reason=reason,
                changed_at=now,
            )
        )
        await session.commit()
