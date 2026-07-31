"""Conflict detection — audit-layer OCC + outbox sync consistency.

Primary OCC is handled inline in the PG transaction (see ActionService.execute_action
step 8: row-level ``WHERE version = :expected`` OCC). This module provides a secondary
audit layer for post-commit consistency checks and cross-store reconciliation.

Use cases:
    - Detecting anomalies that bypassed L1 (e.g., direct PG writes outside ActionService)
    - Cross-store consistency audits (object_state vs Doris — the outbox INDEX effect
      sync target; detects outbox consumption failures / missed writes)
    - Forensic replay of conflicting write sequences

2026-07-08 (outbox 驱动重构): the audit target switched from Iceberg (ARCHIVE,
≤5min lag, would systematically false-report the lag window as mismatches) to
Doris (INDEX effect, ≤1s lag — 300x smaller window, and Doris is the online read
primary source per ADR-001 so its consistency is what users actually observe).
Doris idx tables have no ``version`` column (IndexFieldExtractor does not add one),
so the audit checks **presence** (object_state has it, does Doris?) rather than
version equality — this detects outbox INDEX consumption failures (missed writes),
which is the real failure mode of the async sync path. See
docs/design/action-sync-outbox-design.md §七.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ontology.services._metadata_owner import MetadataOwnerMixin

if TYPE_CHECKING:
    from ontology.config.container import Container
    from ontology.core.schemas.ontology import ObjectType
    from ontology.layers.index.doris_index_store import DorisIndexStore
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)


class ConflictDetector(MetadataOwnerMixin):
    """Audit-layer: reconcile PG object_state vs Doris (outbox INDEX target).

    This is NOT called during the Action hot path. It serves as a reconciliation
    and audit tool, answering:
        - "Did any object_state write fail to reach Doris?" (outbox INDEX
          consumption failure / missed write)
    """

    def __init__(
        self,
        index: "DorisIndexStore",
        metadata: "PostgresMetaStore | None" = None,
        *,
        container: "Container | None" = None,
    ) -> None:
        self._index = index
        self._metadata = metadata
        # Prefer a container reference so the audit loop can open a fresh,
        # properly-closed session per iteration (M2) instead of reusing the
        # long-lived session captured at construction time.
        self._container = container

    async def verify_object_state_consistency(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        pk_column: str,
        object_pks: list[str],
    ) -> list[str]:
        """Cross-check PG object_state presence against Doris idx table.

        Returns list of business PK values present in object_state but **missing**
        from Doris — i.e. outbox INDEX effect failed to sync them (consumption
        failure / missed write). These are the objects a user would observe as
        "stale" in queries (Doris is the online read primary source).

        Args:
            ontology_api_name: Owning ontology (drives Doris table ``idx_{ont}__{type}``).
            object_type_api_name: Target ObjectType.
            pk_column: Physical PK column name (backing_column of the ObjectType's
                primary_key) — the Doris UNIQUE KEY column.
            object_pks: Business PK values (from object_state.properties[pk_column])
                to verify against Doris.

        Returns:
            List of PK values missing from Doris. Empty if all present.
        """
        if not object_pks:
            return []
        # NOTE: Doris unavailability propagates as an exception (DorisUnavailableError
        # or RuntimeError) — callers decide how to handle (run_audit_once marks
        # index_unavailable; standalone callers may catch and treat as empty).
        rows = await self._index.load_by_ids(
            ontology_api_name,
            object_type_api_name,
            object_pks,
            [pk_column],
            pk_column,
        )
        found = {str(r.get(pk_column)) for r in rows}
        return [pk for pk in object_pks if str(pk) not in found]

    async def run_audit_once(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        primary_key: str,
        *,
        metadata: "PostgresMetaStore | None" = None,
    ) -> dict[str, Any]:
        """Audit one object type's PG object_state presence against Doris.

        Returns a summary dict with ``mismatches`` (list of PK values missing
        from Doris) and ``audited`` (count). Doris-unavailable is reported as
        ``index_unavailable: True`` rather than raising — the audit loop is
        best-effort and must not crash the background task.

        Args:
            ontology_api_name: Owning ontology.
            object_type_api_name: ObjectType api_name to audit.
            primary_key: ObjectType.primary_key (api_name) — used to resolve the
                backing_column (physical PK column) from the ObjectType's
                properties, and to extract the PK value from each object_state's
                properties JSONB.
            metadata: Optional metadata store bound to a session. When the
                audit loop runs via ``container``, it opens a fresh session per
                iteration and passes the resulting store here (``self._metadata``
                is None in that path). Callers outside the loop may omit this
                and rely on the constructor-injected ``self._metadata``.
        """
        meta = metadata or self._metadata
        if meta is None:
            raise RuntimeError("ConflictDetector.run_audit_once requires metadata")
        rows = await meta.get_object_states_by_type(object_type_api_name, limit=500)
        if not rows:
            return {"object_type": object_type_api_name, "audited": 0, "mismatches": []}

        # Resolve the physical PK column (backing_column) from the ObjectType.
        ot = await meta.get_object_type(ontology_api_name, object_type_api_name)
        pk_column = _resolve_pk_backing_column(ot, primary_key)
        if pk_column is None:
            return {
                "object_type": object_type_api_name,
                "audited": len(rows),
                "mismatches": [],
                "skipped": "no primary_key backing_column",
            }

        # Extract the business PK value from each object_state's properties.
        pks: list[str] = []
        for r in rows:
            props = r.get("properties") or {}
            val = props.get(pk_column)
            if val is not None:
                pks.append(str(val))
        if not pks:
            return {"object_type": object_type_api_name, "audited": len(rows), "mismatches": []}

        try:
            mismatches = await self.verify_object_state_consistency(
                ontology_api_name, object_type_api_name, pk_column, pks
            )
        except Exception as exc:  # noqa: BLE001 — audit must not crash
            _log.warning("ConflictDetector audit for %s failed: %s", object_type_api_name, exc)
            return {
                "object_type": object_type_api_name,
                "audited": len(pks),
                "mismatches": [],
                "index_unavailable": True,
            }
        return {
            "object_type": object_type_api_name,
            "audited": len(pks),
            "mismatches": mismatches,
        }

    async def run_audit_loop(self, interval: float = 300.0) -> None:
        """Background audit loop: periodically reconcile PG vs Doris.

        Iterates all ObjectTypes with a primary_key (have a Doris idx table),
        runs ``run_audit_once`` per type, and logs warnings on mismatches
        (objects in object_state but missing from Doris = outbox INDEX sync
        failure). Best-effort: any per-type error is swallowed so the loop
        keeps running. Runs for the app lifetime; cancelled on shutdown.
        """
        if self._metadata is None and self._container is None:
            _log.warning("ConflictDetector audit loop disabled: no metadata wired")
            return
        _log.info("ConflictDetector audit loop started (interval=%.0fs)", interval)
        while True:
            try:
                if self._container is not None:
                    async with self._container.metadata_session() as meta:
                        await self._audit_iteration(meta)
                else:
                    # Legacy path (no container): reuse the injected store.
                    assert self._metadata is not None
                    await self._audit_iteration(self._metadata)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — loop must survive per-iter errors
                _log.exception("ConflictDetector audit loop error")
            await asyncio.sleep(interval)

    async def _audit_iteration(self, metadata: "PostgresMetaStore") -> None:
        """One audit pass: iterate all ObjectTypes with a primary_key, audit each.

        Uses the provided metadata store (bound to a freshly-opened session in
        the container path) for both the ObjectType lookup and the per-type
        audit, so ``run_audit_once`` does not depend on ``self._metadata``.
        """
        from sqlalchemy import select

        from ontology.core.models.ontology import ObjectTypeModel, OntologyModel

        # ObjectTypes that have a primary_key (→ have a Doris idx table keyed by
        # the PK backing_column). JOIN ontologies for the ontology api_name.
        # ADR-021 §2.7: exclude VIRTUAL — external sources have no data_version
        # baseline, so ConflictDetector audit has nothing to reconcile.
        stmt = (
            select(ObjectTypeModel, OntologyModel.api_name.label("ontology_api_name"))
            .join(OntologyModel, OntologyModel.id == ObjectTypeModel.ontology_id)
            .where(ObjectTypeModel.primary_key.is_not(None))
            .where(ObjectTypeModel.primary_key != "")
            .where(ObjectTypeModel.storage_type == "MANAGED")
        )
        result = await metadata.session.execute(stmt)
        rows = result.all()
        for ot, ontology_api_name in rows:
            try:
                summary = await self.run_audit_once(
                    ontology_api_name,
                    ot.api_name,
                    ot.primary_key,
                    metadata=metadata,
                )
                if summary.get("mismatches"):
                    _log.warning(
                        "Audit mismatch (object_state has, Doris missing): "
                        "type=%s ontology=%s pks=%s",
                        ot.api_name,
                        ontology_api_name,
                        summary["mismatches"],
                    )
            except Exception as exc:  # noqa: BLE001 — best-effort per type
                _log.warning(
                    "Audit for %s.%s failed: %s",
                    ontology_api_name,
                    ot.api_name,
                    exc,
                    exc_info=True,
                )


def _resolve_pk_backing_column(ot: "ObjectType", primary_key: str) -> str | None:
    """Resolve the physical backing_column for an ObjectType's primary_key api_name.

    Returns None if the ObjectType has no matching property or the property
    has no backing_column.
    """
    for prop in ot.properties:
        if prop.api_name == primary_key:
            backing = getattr(prop, "backing_mapping", None)
            if backing is not None:
                col = getattr(backing, "backing_column", None)
                if col:
                    return str(col)
            # ORM PropertyDefModel stores backing_column directly (not nested).
            col = getattr(prop, "backing_column", None)
            if col:
                return str(col)
    return None
