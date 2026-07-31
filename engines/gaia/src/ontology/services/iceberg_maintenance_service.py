"""IcebergMaintenanceService — periodic Iceberg table health maintenance.

Iceberg tables accumulate small data files, snapshots, and orphan files as
CDC/batch writes commit. Without maintenance this degrades query performance
(scan planning slows, S3 API calls grow, metadata bloats). This service runs
the three standard Iceberg maintenance operations via Trino's native Iceberg
connector (`ALTER TABLE ... EXECUTE`):

  - ``optimize``: compact small data files into larger ones
  - ``expire_snapshots``: drop old snapshots, release their data files
  - ``remove_orphan_files``: delete files no longer referenced by any snapshot

Gravitino's built-in Iceberg REST Catalog does NOT provide maintenance (it is
a catalog proxy, metadata-only). But Gaia's existing Trino native Iceberg
connector (`config/trino/catalog/iceberg.properties`, `connector.name=iceberg`
+ `iceberg.catalog.type=rest` pointing at Gravitino 9001) fully supports these
procedures — verified live (2026-07-06).

This is the maintenance half of path A (Iceberg→Doris low-frequency batch):
since path A no longer runs a per-type常驻 STREAMING job (ADR-008 2nd revision),
the Iceberg tables are written only by periodic BATCH backfill + the
PG→Iceberg CDC. Maintenance keeps those tables healthy.

Lifecycle: started by main.py lifespan as a background task (run_maintenance_loop),
best-effort — any per-table error is logged and skipped so one bad table does
not abort the whole pass.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from ontology.core.naming import _to_snake

if TYPE_CHECKING:
    from ontology.config.container import Container
    from ontology.layers.engine.base import QueryEngine

_log = logging.getLogger("ontology.iceberg_maintenance")

# Default maintenance retention. Iceberg's Trino connector enforces a minimum
# retention (7d default) to avoid expiring snapshots still needed by running
# queries/jobs; stay above it.
DEFAULT_SNAPSHOT_RETENTION = "7d"
DEFAULT_ORPHAN_RETENTION = "7d"
# File size threshold for compaction — Iceberg/Trino recommend targeting the
# Parquet block size (~128MB) so compacted files are efficient for vectorized reads.
DEFAULT_OPTIMIZE_FILE_SIZE_THRESHOLD = "128MB"


class IcebergMaintenanceService:
    """Run Iceberg maintenance (optimize / expire_snapshots / remove_orphan_files).

    Args:
        engine: QueryEngine — executes the ``ALTER TABLE ... EXECUTE`` SQL.
    """

    def __init__(self, engine: QueryEngine | None = None) -> None:
        self._engine = engine

    def _get_engine(self, container: Container | None) -> QueryEngine:
        if self._engine is not None:
            return self._engine
        if container is None:
            raise RuntimeError("IcebergMaintenanceService needs an engine or container")
        return container.engine

    @staticmethod
    def _table_ref(object_type_api_name: str) -> str:
        """Build the Trino Iceberg table reference: iceberg.ontology.<snake_type>."""
        return f"iceberg.ontology.{_to_snake(object_type_api_name)}"

    async def optimize(
        self,
        object_type_api_name: str,
        *,
        file_size_threshold: str = DEFAULT_OPTIMIZE_FILE_SIZE_THRESHOLD,
        container: Container | None = None,
    ) -> list[dict[str, Any]]:
        """Compact small data files into larger ones.

        Runs ``ALTER TABLE iceberg.ontology.<t> EXECUTE optimize(file_size_threshold => '<n>')``.
        Returns the Trino summary rows (files rewritten etc.).
        """
        engine = self._get_engine(container)
        table = self._table_ref(object_type_api_name)
        sql = f"ALTER TABLE {table} EXECUTE optimize(file_size_threshold => '{file_size_threshold}')"
        _log.info("Iceberg optimize: %s (threshold=%s)", table, file_size_threshold)
        return await engine.query(sql)

    async def expire_snapshots(
        self,
        object_type_api_name: str,
        *,
        retention_threshold: str = DEFAULT_SNAPSHOT_RETENTION,
        container: Container | None = None,
    ) -> list[dict[str, Any]]:
        """Expire old snapshots, releasing their data files.

        Runs ``ALTER TABLE ... EXECUTE expire_snapshots(retention_threshold => '<n>')``.
        Trino enforces a minimum retention (default 7d) to protect in-flight
        queries; stay at or above it.
        """
        engine = self._get_engine(container)
        table = self._table_ref(object_type_api_name)
        sql = f"ALTER TABLE {table} EXECUTE expire_snapshots(retention_threshold => '{retention_threshold}')"
        _log.info("Iceberg expire_snapshots: %s (retention=%s)", table, retention_threshold)
        return await engine.query(sql)

    async def remove_orphan_files(
        self,
        object_type_api_name: str,
        *,
        retention_threshold: str = DEFAULT_ORPHAN_RETENTION,
        container: Container | None = None,
    ) -> list[dict[str, Any]]:
        """Delete files no longer referenced by any snapshot or in-progress transaction.

        Runs ``ALTER TABLE ... EXECUTE remove_orphan_files(retention_threshold => '<n>')``.
        """
        engine = self._get_engine(container)
        table = self._table_ref(object_type_api_name)
        sql = f"ALTER TABLE {table} EXECUTE remove_orphan_files(retention_threshold => '{retention_threshold}')"
        _log.info("Iceberg remove_orphan_files: %s (retention=%s)", table, retention_threshold)
        return await engine.query(sql)

    async def run_maintenance_once(
        self,
        object_type_api_name: str,
        *,
        container: Container | None = None,
    ) -> dict[str, Any]:
        """Run all three maintenance ops for one ObjectType (best-effort).

        Each op is independent — a failure in one is logged and does not block
        the others. Returns a summary dict with per-op status.
        """
        summary: dict[str, Any] = {"object_type": object_type_api_name}
        for op in ("optimize", "expire_snapshots", "remove_orphan_files"):
            try:
                method = getattr(self, op)
                await method(object_type_api_name, container=container)
                summary[op] = "ok"
            except Exception as exc:  # noqa: BLE001 — maintenance is best-effort
                _log.warning(
                    "Iceberg maintenance %s for %s failed: %s",
                    op,
                    object_type_api_name,
                    exc,
                    exc_info=True,
                )
                summary[op] = f"failed: {exc}"
        return summary

    async def run_maintenance_loop(
        self,
        container: Container,
        *,
        interval: float = 86400.0,  # 24h default — maintenance is low-frequency
    ) -> None:
        """Background maintenance loop: periodically run maintenance on all Iceberg tables.

        Iterates all ObjectTypes that have a backing Iceberg dataset (i.e. a
        ``backing_mapping`` on a property) and runs the three maintenance ops
        per type. Best-effort: any per-type/per-op error is swallowed so the
        loop keeps running. Runs for the app lifetime; cancelled on shutdown.

        Args:
            container: Container — for engine + metadata_session.
            interval: Seconds between full passes. Default 24h (maintenance is
                not latency-sensitive; path A backfill is the frequent op).
        """
        _log.info("Iceberg maintenance loop started (interval=%.0fs)", interval)
        while True:
            try:
                async with container.metadata_session() as meta:
                    await self._maintenance_iteration(meta, container)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — loop must survive per-iter errors
                _log.exception("Iceberg maintenance loop error")
            await asyncio.sleep(interval)

    async def _maintenance_iteration(
        self,
        metadata: Any,
        container: Container,
    ) -> None:
        """One maintenance pass: discover Iceberg-backed ObjectTypes, maintain each."""
        from sqlalchemy import select

        from ontology.core.models.ontology import ObjectTypeModel, PropertyDefModel

        # ObjectTypes with at least one property carrying a backing_dataset_api_name
        # have an Iceberg dataset to maintain.
        stmt = (
            select(PropertyDefModel.backing_dataset_api_name)
            .where(PropertyDefModel.backing_dataset_api_name.is_not(None))
            .distinct()
        )
        result = await metadata.session.execute(stmt)
        dataset_names = {row[0] for row in result.fetchall() if row[0]}
        if not dataset_names:
            return
        # Resolve the ObjectType api_name for each dataset (the dataset api_name
        # is derived from the ObjectType api_name via naming; for maintenance we
        # need the ObjectType api_name to build the Iceberg table ref). Query
        # the ObjectType that owns these properties.
        ot_stmt = (
            select(ObjectTypeModel.api_name)
            .distinct()
            .join(PropertyDefModel, PropertyDefModel.object_type_id == ObjectTypeModel.id)
            .where(PropertyDefModel.backing_dataset_api_name.in_(dataset_names))
        )
        ot_result = await metadata.session.execute(ot_stmt)
        type_names = [r[0] for r in ot_result.fetchall() if r[0]]
        for type_name in type_names:
            try:
                await self.run_maintenance_once(type_name, container=container)
            except Exception as exc:  # noqa: BLE001 — best-effort per type
                _log.warning("Maintenance for %s skipped: %s", type_name, exc)
