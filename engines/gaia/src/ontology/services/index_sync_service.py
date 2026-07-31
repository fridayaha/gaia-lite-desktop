"""IndexSyncService — orchestrate Doris index table **schema** provisioning.

Single entry point for wiring an ObjectType's ``indexed`` properties to a
live Doris index table **schema** (DDL only). Closes the "Doris index
link spins empty" gap documented in implementation-status.md (#2).

Responsibilities (all side-effectful orchestration; no business logic):
  - provision:  on ObjectType create    → drop+create index table (DDL)
  - rebuild:    on ObjectType update    → drop+create index table (new field set)
  - deprovision:on ObjectType delete    → drop index table

Data sync (Iceberg→Doris / Action→Doris) is **NOT** this service's job:
  - Action write path: OutboxExecutor INDEX effect → DorisIndexStore.upsert
  - External ingestion path: ObjectIndexFunnel → DorisIndexStore.upsert
  - SeaTunnel INDEX pipeline (backfill BATCH + stream STREAMING) was **removed**
    (handoff-rid-funnel-closure.md T1.10): Doris writes are now unified under
    ObjectIndexFunnel, with rid assignment
    + four-engine fan-out. This service is schema-only.

Failure semantics (architecture: provisioning must NOT block ObjectType CRUD):
  Every Doris error is caught and re-raised as IndexProvisionError.
  Callers (OntologyService) catch IndexProvisionError, log it, and continue —
  the ObjectType is still created/updated in PG. Queries fall back to Trino
  (IndexNotBuiltError path in ObjectQueryService) until a retry succeeds.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from ontology.core.exceptions import IndexProvisionError
from ontology.core.schemas.index import IndexField

if TYPE_CHECKING:
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.services.index_field_extractor import IndexFieldExtractor

_log = logging.getLogger(__name__)


class IndexSyncService:
    """Orchestrate Doris index **schema** provisioning for ObjectTypes (DDL only).

    Data sync (Iceberg→Doris / Action→Doris) is handled by OutboxExecutor
    (Action path) and ObjectIndexFunnel (external ingestion path); the legacy
    SeaTunnel INDEX pipeline was removed (handoff-rid-funnel-closure.md T1.10).

    Args:
        index: DorisIndexStore — DDL + DML on Doris index tables.
        extractor: IndexFieldExtractor — derives IndexField[] from properties.
            Injected to allow test substitution; defaults to the standard one.
        metadata: PostgresMetaStore — optional, reserved for future row-count
            back-fill hooks (currently unused after sync_now removal).
    """

    def __init__(
        self,
        index: DorisIndexStore,
        extractor: IndexFieldExtractor | None = None,
        metadata: PostgresMetaStore | None = None,
    ) -> None:
        self._index = index
        self._extractor = extractor or IndexFieldExtractor()
        # PostgresMetaStore — reserved for future hooks; currently unused
        # after the sync_now / SeaTunnel backfill removal (T1.10).
        self._metadata = metadata

    # ── Lifecycle hooks ──

    async def provision(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        properties: Sequence[object],
        primary_key: str | None = None,
    ) -> list[IndexField]:
        """Provision a fresh index table + sync pipeline for a new ObjectType.

        Called from OntologyService.define_object_type(_batch) for MANAGED types.
        Idempotent: drops any pre-existing index table first (handles retries
        and the rare case of a leftover table from a failed prior attempt).

        Args:
            ontology_api_name: Owning ontology — drives the Doris table name
                prefix (``idx_{ontology}__{type}``) and INDEX pipeline name
                (``index_{ontology}__{type}``) for cross-ontology isolation.
            object_type_api_name: The ObjectType api_name.
            properties: PropertyDefModel/PropertyDef sequence — indexed fields
                are extracted from these.
            primary_key: The ObjectType's primary_key field name, so the PK
                column lands in the index table even when the per-property
                is_primary_key flag is not set.

        Returns:
            The IndexField list actually provisioned (for logging/verification).

        Raises:
            IndexProvisionError: if any Doris/SeaTunnel step fails. Non-fatal
                to the caller; the ObjectType CRUD still succeeds.
        """
        result = self._extractor.extract(properties, primary_key=primary_key)
        fields = result.fields
        field_names = [f.name for f in fields]

        try:
            # Idempotent rebuild: drop first so a half-created table from a
            # prior failed attempt does not leave stale columns.
            await self._index.drop_index_table(ontology_api_name, object_type_api_name)
            await self._index.create_index_table(
                ontology_api_name=ontology_api_name,
                object_type_api_name=object_type_api_name,
                fields=[_field_to_dict(f) for f in fields],
            )
            # Data sync is NOT this service's job (T1.10): Action path goes
            # through OutboxExecutor; external ingestion goes through
            # ObjectIndexFunnel. No SeaTunnel INDEX pipeline to start.
        except IndexProvisionError:
            raise
        except Exception as exc:
            raise IndexProvisionError(
                f"Failed to provision index for {ontology_api_name}.{object_type_api_name}: {exc}"
            ) from exc

        _log.info(
            "IndexSyncService.provision: %s — %d field(s) indexed %s, %d skipped",
            object_type_api_name,
            len(fields),
            field_names,
            len(result.skipped),
        )
        return fields

    async def rebuild(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        properties: Sequence[object],
        primary_key: str | None = None,
    ) -> list[IndexField]:
        """Rebuild the index table schema after a property set change.

        Called from OntologyService.update_object_type_batch when the indexed
        field set may have changed. Drops + recreates the Doris index table
        (DDL only); data is refilled by ObjectIndexFunnel / OutboxExecutor
        (T1.10: SeaTunnel backfill removed).
        """
        result = self._extractor.extract(properties, primary_key=primary_key)
        fields = result.fields
        field_names = [f.name for f in fields]

        try:
            await self._index.drop_index_table(ontology_api_name, object_type_api_name)
            await self._index.create_index_table(
                ontology_api_name=ontology_api_name,
                object_type_api_name=object_type_api_name,
                fields=[_field_to_dict(f) for f in fields],
            )
            # Data sync is NOT this service's job (T1.10). No SeaTunnel INDEX
            # pipeline to update — the Doris index table DDL is recreated
            # above; ObjectIndexFunnel / OutboxExecutor refill it.
        except IndexProvisionError:
            raise
        except Exception as exc:
            raise IndexProvisionError(
                f"Failed to rebuild index for {ontology_api_name}.{object_type_api_name}: {exc}"
            ) from exc

        _log.info(
            "IndexSyncService.rebuild: %s.%s — %d field(s) indexed %s",
            ontology_api_name,
            object_type_api_name,
            len(fields),
            field_names,
        )
        return fields

    async def deprovision(self, ontology_api_name: str, object_type_api_name: str) -> None:
        """Tear down the index table on ObjectType deletion (DDL only).

        Best-effort: a failing drop is logged but does not raise, because
        the ObjectType has already been deleted from PG and we must not block
        the delete response. Leftover Doris tables are reaped on the next
        provision of an ObjectType reusing the same api_name (provision drops
        first). The SeaTunnel INDEX pipeline stop was removed (T1.10).
        """
        try:
            await self._index.drop_index_table(ontology_api_name, object_type_api_name)
        except Exception as exc:
            _log.warning(
                "IndexSyncService.deprovision: drop index table for %s.%s failed: %s",
                ontology_api_name,
                object_type_api_name,
                exc,
            )
            return
        _log.info(
            "IndexSyncService.deprovision: %s.%s torn down",
            ontology_api_name,
            object_type_api_name,
        )


def _field_to_dict(field: IndexField) -> dict[str, object]:
    """IndexField → the dict shape DorisIndexStore.create_index_table expects.

    create_index_table reads ``name``, ``index_type``, and (for STORED_ONLY)
    ``data_type`` off each field dict.
    """
    d: dict[str, object] = {"name": field.name, "index_type": field.index_type}
    if field.data_type is not None:
        d["data_type"] = field.data_type
    return d
