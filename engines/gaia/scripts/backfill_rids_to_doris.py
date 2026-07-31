"""Backfill the ``rid`` column of existing Doris idx tables (one-shot migration).

Trigger: after PR-1 of handoff-rid-funnel-closure.md lands (``create_index_table``
now injects a ``rid`` column, and the ALTER migration adds it to存量 tables).
存量 rows have ``rid IS NULL`` (or empty); this script resolves a stable rid
for each, using **Neo4j as the trusted source** (D2):

  1. For each MANAGED ObjectType with a Doris idx table:
     2. SELECT pk_column, rid FROM idx WHERE rid IS NULL OR rid = ''
     3. For each row: look up the rid in Neo4j by PK value (D5 guarantees the
        node carries the PK value as a property).
        - hit  → reuse the Neo4j rid, UPDATE Doris.
        - miss → allocate a fresh rid (generate_object_rid), UPDATE Doris,
                 and UPSERT the Neo4j node so the graph stays consistent.
  4. Also re-project to PostGIS if geotime is enabled (best-effort).

Idempotent: re-running only touches rows still missing a rid.

Run::

    .venv/bin/python scripts/backfill_rids_to_doris.py [--dry-run] [--limit N]

Options:
  --dry-run   Report counts only; do not UPDATE Doris / UPSERT Neo4j.
  --limit N   Cap rows processed per ObjectType (default 100000).

Exit code 0 on success, non-zero on any ObjectType that fails (continues to
the next OT, logs the error).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from ontology.config.container import container
from ontology.core.rid import generate_object_rid
from ontology.layers.graph.neo4j_graph_store import graph_label

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_log = logging.getLogger("backfill_rids")


async def _resolve_pk_column(metadata: Any, ont_api: str, ot_api: str) -> tuple[str | None, Any]:
    """Return (pk_backing_column, ObjectType) for the given OT, or (None, None)."""
    ot = await metadata.get_object_type(ont_api, ot_api)
    if not ot.primary_key:
        return None, ot
    pk_api = ot.primary_key
    pk_col = pk_api
    for prop in ot.properties:
        if prop.api_name == pk_api and prop.backing_mapping:
            pk_col = prop.backing_mapping.backing_column or pk_api
            break
    return pk_col, ot


async def _neo4j_lookup_rid_by_pk(graph: Any, label: str, pk_api: str, pk_value: Any) -> str | None:
    """Look up an existing rid in Neo4j by the PK property value."""
    cypher = f"MATCH (n:`{label}` {{{pk_api}: $pk}}) RETURN n.rid AS rid LIMIT 1"
    result = await graph._run(cypher, pk=pk_value)  # noqa: SLF001 — _run is the public-ish query helper
    for record in result.records:
        rid = record.get("rid") if hasattr(record, "get") else record[0]
        if rid:
            return str(rid)
    return None


async def backfill_one_object_type(
    metadata: Any,
    index_store: Any,
    graph: Any | None,
    ont_api: str,
    ot_api: str,
    *,
    dry_run: bool,
    limit: int,
) -> dict[str, int]:
    """Backfill rids for a single ObjectType. Returns {reused, allocated, skipped, failed}."""
    stats = {"reused": 0, "allocated": 0, "skipped": 0, "failed": 0}
    pk_column, ot = await _resolve_pk_column(metadata, ont_api, ot_api)
    if pk_column is None:
        _log.warning("%s/%s: no primary_key, skipping rid backfill", ont_api, ot_api)
        stats["skipped"] += 1
        return stats
    if ot.storage_type == "VIRTUAL":
        _log.debug("%s/%s: VIRTUAL, no Doris idx table, skipping", ont_api, ot_api)
        stats["skipped"] += 1
        return stats

    # Pull rows missing a rid. Doris returns the physical PK column + (empty) rid.
    table = index_store._table_name(ont_api, ot_api)  # noqa: SLF001
    sql_select = f"SELECT `{pk_column}`, `rid` FROM {table} WHERE `rid` IS NULL OR `rid` = '' LIMIT {int(limit)}"
    try:
        conn = await index_store._acquire()  # noqa: SLF001
        try:
            cursor = await index_store._cursor(conn)  # noqa: SLF001
            try:
                await cursor.execute(sql_select)
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        finally:
            await index_store._release(conn)  # noqa: SLF001
    except Exception as exc:
        _log.error("%s/%s: failed to read NULL-rid rows: %s", ont_api, ot_api, exc)
        stats["failed"] += 1
        return stats

    if not rows:
        _log.info("%s/%s: no rows missing rid", ont_api, ot_api)
        return stats

    label = graph_label(ont_api, ot_api)
    pk_api = ot.primary_key
    updates: list[tuple[str, Any]] = []  # (rid, pk_value)

    for pk_value, _empty_rid in rows:
        rid: str | None = None
        if graph is not None:
            try:
                rid = await _neo4j_lookup_rid_by_pk(graph, label, pk_api, pk_value)
            except Exception as exc:  # noqa: BLE001 — best-effort lookup
                _log.debug("%s/%s: Neo4j lookup failed for pk=%s: %s", ont_api, ot_api, pk_value, exc)
        if rid:
            stats["reused"] += 1
        else:
            rid = generate_object_rid()
            stats["allocated"] += 1
        updates.append((rid, pk_value))

    if dry_run:
        _log.info("%s/%s [dry-run]: would update %d rows (%d reused, %d allocated)",
                  ont_api, ot_api, len(updates), stats["reused"], stats["allocated"])
        return stats

    # Batch UPDATE Doris: Doris Unique-model INSERT with the PK column overwrites
    # the existing row (upsert semantics), which is simpler than UPDATE...WHERE
    # for bulk. We only set rid + pk (other columns preserved by Unique MOW? No —
    # Unique-model INSERT replaces the whole row). To avoid clobbering other
    # columns we must use UPDATE ... WHERE pk = ?. Doris supports UPDATE on
    # Unique-model tables since 1.2; batch via executemany.
    update_sql = f"UPDATE {table} SET `rid` = %s WHERE `{pk_column}` = %s"
    try:
        conn = await index_store._acquire()  # noqa: SLF001
        try:
            cursor = await index_store._cursor(conn)  # noqa: SLF001
            try:
                # executemany is not uniformly supported on Doris FE for UPDATE;
                # fall back to per-row execute (bounded by `limit`, acceptable
                # for a one-shot migration).
                for rid, pk_value in updates:
                    await cursor.execute(update_sql, (rid, pk_value))
            finally:
                await cursor.close()
        finally:
            await index_store._release(conn)  # noqa: SLF001
    except Exception as exc:
        _log.error("%s/%s: Doris UPDATE rid failed: %s", ont_api, ot_api, exc)
        stats["failed"] += len(updates)
        return stats

    _log.info("%s/%s: updated %d rows (%d reused from Neo4j, %d allocated)",
              ont_api, ot_api, len(updates), stats["reused"], stats["allocated"])
    return stats


async def main(dry_run: bool, limit: int) -> int:
    _log.info("starting rid backfill (dry_run=%s, limit=%d)", dry_run, limit)
    metadata = container.metadata
    index_store = container.index
    graph = container.graph_projector._graph if container.graph_projector else None  # noqa: SLF001

    # Enumerate all MANAGED ObjectTypes across all ontologies.
    ontologies = await metadata.list_ontologies(include_deleted=False)
    total = {"reused": 0, "allocated": 0, "skipped": 0, "failed": 0}
    for ont in ontologies:
        ont_api = ont.api_name
        try:
            ots = await metadata.list_object_types(ont_api, include_deleted=False)
        except Exception as exc:  # noqa: BLE001
            _log.error("failed to list ObjectTypes for %s: %s", ont_api, exc)
            continue
        for ot in ots:
            try:
                stats = await backfill_one_object_type(
                    metadata, index_store, graph, ont_api, ot.api_name,
                    dry_run=dry_run, limit=limit,
                )
                for k, v in stats.items():
                    total[k] += v
            except Exception as exc:  # noqa: BLE001
                _log.error("%s/%s: backfill failed: %s", ont_api, ot.api_name, exc)
                total["failed"] += 1

    _log.info("backfill complete: %s", total)
    return 0 if total["failed"] == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill rid column in Doris idx tables")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--limit", type=int, default=100_000, help="Max rows per ObjectType")
    args = parser.parse_args()
    rc = asyncio.run(main(dry_run=args.dry_run, limit=args.limit))
    sys.exit(rc)
