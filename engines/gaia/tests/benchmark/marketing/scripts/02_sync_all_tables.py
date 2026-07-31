"""Marketing benchmark — batch serial sync of all 14 physical tables (DESIGN.md P3 prereq).

Syncs every source-backed MANAGED ObjectType from MySQL → Iceberg (via SeaTunnel)
→ Doris (provision + sync_now), **serially** to avoid resource exhaustion
(DESIGN.md §八-3 "必须串行"). Records per-table progress to a JSON state file
so an interrupted run can resume (DESIGN.md §2.5 determinism / resumability).

Principle 3.5 compliance: every write goes through the system service layer
(container.datasource_service / index_sync_service) — no direct Doris/Iceberg
writes from this script. ``recording`` is a synthetic table seeded directly
into MySQL by the fixture (DESIGN.md §3.2 修正3), but it still needs a SeaTunnel
sync into Iceberg/Doris like any other source table (it is a real MySQL row
set the harness reads via the ontology API in L5).

Flow per table (mirrors verify_sync_chain.py, generalized):
  1. ensure credential + datasource + dataset registered (idempotent, 409 ok)
  2. link_dataset (idempotent — backend dedups by (ot, dataset, column))
  3. create_sync_task (idempotent via 409 catch)
  4. start_sync → poll Iceberg row count via Trino (up to 120s)
  5. provision Doris index table (drops pre-existing)
  6. sync_now (Iceberg → Doris, backfills row_count_estimate)

Usage:
    .venv/bin/python -m tests.benchmark.marketing.scripts.02_sync_all_tables [--reset]
    .venv/bin/python -m tests.benchmark.marketing.scripts.02_sync_all_tables --only Lead,User
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("sync_all")

# ── Constants ────────────────────────────────────────────────────────────────
ONTO = "Marketing"
DS_API = "marketingMysql"
CRED_API = "marketingMysqlCred"
SOURCE_SCHEMA = "marketing_benchmark"
# backend connects to localhost:3306 (env override rewrites to container name)
CONNECTOR_CONFIG = {"host": "localhost", "port": 3306, "database": SOURCE_SCHEMA}

STATE_FILE = Path(__file__).resolve().parent / ".sync_state.json"


# ── Per-entity sync spec ─────────────────────────────────────────────────────
# (api_name, pk_api_name, source_physical_table, dataset_api_name)
# dataset_api_name = managed_dataset_api_name(api_name) = snake_case(api_name)
@dataclass
class SyncSpec:
    api_name: str
    pk_api: str
    source_table: str
    dataset_api: str
    skip_sync_task: bool = False  # synthetic tables that bypass SeaTunnel? (no — all go via SeaTunnel)


# All 14 source-backed tables (DESIGN.md §12 "需同步的 14 张物理表").
# recording is synthetic (seed-built) but still has a real MySQL table → sync normally.
SPECS: list[SyncSpec] = [
    SyncSpec("Dealership", "storeCode", "t_ods_master_data_store", "dealership"),
    SyncSpec("SalesConsultant", "userId", "t_ods_master_data_staff", "sales_consultant"),
    SyncSpec("LeadSource", "sourceId", "t_ods_leads_server_leads_source", "lead_source"),
    SyncSpec("User", "userId", "t_ods_leads_server_leads_user_rt", "user"),
    SyncSpec("Lead", "id", "t_ods_leads_server_leads_info_rt", "lead"),
    SyncSpec("LeadAllocateRecord", "oid", "t_ods_source_data_leads_operation_record", "lead_allocate_record"),
    SyncSpec("LeadDistributeRecord", "oid", "t_ods_source_data_leads_operation_record", "lead_distribute_record"),
    SyncSpec("LeadFollowRecord", "oid", "t_ods_source_data_leads_follow_record", "lead_follow_record"),
    SyncSpec("ManualOutboundCall", "id", "t_ods_leads_server_sale_call_record_rt", "manual_outbound_call"),
    SyncSpec("AiOutboundCall", "id", "t_ods_leads_server_ai_call_out_result_rt", "ai_outbound_call"),
    SyncSpec("TestDrive", "id", "t_ods_test_drive_test_drive_rt", "test_drive"),
    SyncSpec("TestDriveCar", "id", "t_ods_test_drive_car_model", "test_drive_car"),
    SyncSpec("TestDriveRoute", "id", "t_ods_test_drive_route", "test_drive_route"),
    SyncSpec("ChatRecord", "id", "t_ods_inspection_weixin_log", "chat_record"),
    # recording is seeded into MySQL as a real table — sync it like the rest.
    SyncSpec("Recording", "recordingId", "recording", "recording"),
]


@dataclass
class TableState:
    api_name: str
    status: str = "pending"  # pending | syncing | synced | failed
    iceberg_rows: int = 0
    doris_rows: int = 0
    error: str = ""
    elapsed_s: float = 0.0
    started_at: str = ""
    finished_at: str = ""


@dataclass
class SyncState:
    tables: dict[str, TableState] = field(default_factory=dict)
    started_at: str = ""

    def save(self) -> None:
        STATE_FILE.write_text(
            json.dumps(
                {"started_at": self.started_at, "tables": {k: asdict(v) for k, v in self.tables.items()}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls) -> SyncState:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            st = cls(started_at=data.get("started_at", ""))
            for k, v in data.get("tables", {}).items():
                st.tables[k] = TableState(**v)
            return st
        return cls()


# ── Helpers ──────────────────────────────────────────────────────────────────
async def _ensure_credential(ds_svc) -> str:
    from ontology.core.schemas.datasource import CredentialCreate

    try:
        await ds_svc.create_credential(
            CredentialCreate(
                api_name=CRED_API,
                credential_type="username_password",
                secret_data={"username": "root", "password": "marketing123"},
            )
        )
        log.info("  ✓ credential %s created", CRED_API)
    except Exception as e:
        log.info("  → credential exists: %s", str(e)[:80])
    cred = await ds_svc.get_credential(CRED_API)
    return cred.id


async def _ensure_datasource(ds_svc, cred_id: str) -> None:
    from ontology.core.schemas.datasource import DataSourceCreate

    try:
        await ds_svc.create_datasource(
            DataSourceCreate(
                api_name=DS_API,
                display_name="Marketing MySQL",
                description="Marketing benchmark source MySQL",
                connector_type="mysql",
                connector_config=CONNECTOR_CONFIG,
                credential_id=cred_id,
            )
        )
        log.info("  ✓ datasource %s created", DS_API)
    except Exception as e:
        log.info("  → datasource exists: %s", str(e)[:80])


async def _ensure_dataset(ds_svc, spec: SyncSpec) -> None:
    from ontology.core.schemas.datasource import DatasetGovernanceCreate

    try:
        await ds_svc.register_dataset(
            DatasetGovernanceCreate(
                api_name=spec.dataset_api,
                display_name=f"Marketing {spec.api_name}",
                storage_location=f"s3://ontology-warehouse/ontology/{spec.dataset_api}",
                data_source_api_name=DS_API,
                kind="MANAGED",
            )
        )
        log.info("  ✓ dataset %s registered", spec.dataset_api)
    except Exception as e:
        log.info("  → dataset %s exists: %s", spec.dataset_api, str(e)[:80])


async def _link_dataset(onto_svc, spec: SyncSpec, props) -> None:
    """Link ObjectType ↔ dataset via column mappings (idempotent)."""
    mappings = []
    for p in props:
        bm = getattr(p, "backing_mapping", None)
        if bm and getattr(bm, "backing_column", None) and getattr(bm, "dataset_api_name", None) == spec.dataset_api:
            mappings.append({"property_api_name": p.api_name, "column_name": bm.backing_column})
    if not mappings:
        log.warning("  ⚠ no properties with dataset_api_name=%s to link", spec.dataset_api)
        return
    try:
        await onto_svc.link_dataset(ONTO, spec.api_name, spec.dataset_api, mappings)
        log.info("  ✓ linked %s ↔ %s (%d cols)", spec.api_name, spec.dataset_api, len(mappings))
    except Exception as e:
        # Idempotent: backend dedups existing mappings. Only treat as error
        # if the message indicates a real failure.
        msg = str(e)
        if "already" in msg.lower() or "exists" in msg.lower() or "conflict" in msg.lower():
            log.info("  → link %s already exists", spec.api_name)
        else:
            log.info("  → link %s: %s", spec.api_name, msg[:120])


async def _ensure_sync_task(ds_svc, spec: SyncSpec):
    from ontology.core.schemas.datasource import SyncTaskCreate

    ds_record = await ds_svc.get_datasource(DS_API)
    # Sync-task api_name is an ops resource identifier (snake_case, see
    # naming.DATASET_API_NAME_PATTERN). Derive from the ObjectType api_name
    # via _to_snake (e.g. LeadSource → lead_source_sync). Mirrors
    # verify_sync_chain's 'dealership_sync'.
    from ontology.core.naming import _to_snake
    sync_api = _to_snake(spec.api_name) + "_sync"
    target_dataset = f"marketing.{spec.dataset_api}"
    try:
        task = await ds_svc.create_sync_task(
            SyncTaskCreate(
                api_name=sync_api,
                data_source_id=ds_record.id,
                sync_type="table",
                source_config={"table": spec.source_table, "schema": SOURCE_SCHEMA},
                target_dataset_api_name=target_dataset,
                sync_mode="full_snapshot",
                transaction_type="snapshot",
            )
        )
        log.info("  ✓ sync-task %s created", sync_api)
        return task
    except Exception as e:
        msg = str(e)
        if "exists" in msg.lower() or "conflict" in msg.lower() or "unique" in msg.lower():
            log.info("  → sync-task %s exists", sync_api)
            return await ds_svc.get_sync_task(sync_api)
        raise


async def _start_sync(ds_svc, spec: SyncSpec):
    sync_api = spec.api_name[0].lower() + spec.api_name[1:] + "Sync"
    try:
        task = await ds_svc.start_sync(sync_api)
        log.info("  ✓ sync started (status=%s)", task.status)
        return task
    except Exception as e:
        # If already RUNNING, that's fine — poll anyway.
        msg = str(e)
        if "running" in msg.lower() or "already" in msg.lower():
            log.info("  → sync already running")
            return await ds_svc.get_sync_task(sync_api)
        raise


async def _poll_iceberg(engine, spec: SyncSpec, timeout_s: int = 180) -> int:
    """Poll Trino for Iceberg row count. Returns row count (0 on timeout)."""
    iceberg_table = f"iceberg.ontology.{spec.dataset_api}"
    deadline = time.time() + timeout_s
    last = -1
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            rows = await engine.query(f"SELECT COUNT(*) AS c FROM {iceberg_table}")
            last = int(rows[0]["c"]) if rows else 0
            if attempt <= 3 or attempt % 3 == 0:
                log.info("    poll %d: %s = %d rows", attempt, iceberg_table, last)
            if last > 0:
                return last
        except Exception as e:
            if attempt <= 2:
                log.info("    poll %d: %s", attempt, str(e)[:100])
        await asyncio.sleep(6)
    log.warning("    poll timed out after %ds; last count=%d", timeout_s, last)
    return max(last, 0)


async def _provision_and_sync(index_sync, meta, spec: SyncSpec) -> tuple[int, int]:
    ot = await meta.get_object_type(ONTO, spec.api_name)
    props = await meta.get_properties(ot.id)
    await index_sync.provision(ONTO, spec.api_name, props, primary_key=spec.pk_api)
    log.info("  ✓ Doris idx_%s__%s provisioned", ONTO.lower(), spec.dataset_api)
    # sync_now default limit is 10k; for large tables (lead_allocate_record=90k)
    # pass a high limit so Doris gets the full row set. DESIGN.md §2.3 max
    # single-table is 100k, so 200k limit covers it with margin.
    count = await index_sync.sync_now(ONTO, spec.api_name, props, primary_key=spec.pk_api, limit=200_000)
    log.info("  ✓ sync_now upserted %d records into Doris", count)
    return count, count


async def _sync_one(spec: SyncSpec, st: TableState) -> None:
    from datetime import UTC, datetime

    from ontology.config.container import container

    ds_svc = container.datasource_service
    onto_svc = container.ontology_service
    index_sync = container.index_sync_service
    engine = container.engine

    st.status = "syncing"
    st.started_at = datetime.now(UTC).isoformat()
    t0 = time.time()
    log.info("═══ Syncing %s (pk=%s, src=%s) ═══", spec.api_name, spec.pk_api, spec.source_table)
    try:
        # 1-2. credential + datasource (idempotent)
        cred_id = await _ensure_credential(ds_svc)
        await _ensure_datasource(ds_svc, cred_id)
        # 3. dataset
        await _ensure_dataset(ds_svc, spec)
        # 4. link dataset (metadata_session for proper AsyncSession cleanup)
        async with container.metadata_session() as meta:
            ot = await meta.get_object_type(ONTO, spec.api_name)
            props = await meta.get_properties(ot.id)
            await _link_dataset(onto_svc, spec, props)
        # 5. sync task + start
        await _ensure_sync_task(ds_svc, spec)
        await _start_sync(ds_svc, spec)
        # 6. poll Iceberg
        iceberg_rows = await _poll_iceberg(engine, spec, timeout_s=240)
        st.iceberg_rows = iceberg_rows
        if iceberg_rows == 0:
            raise RuntimeError(f"no rows landed in Iceberg for {spec.api_name} after polling")
        # 7. provision + sync_now (metadata_session for proper cleanup)
        async with container.metadata_session() as meta:
            doris_rows, _ = await _provision_and_sync(index_sync, meta, spec)
        st.doris_rows = doris_rows
        st.status = "synced"
        st.elapsed_s = round(time.time() - t0, 1)
        st.finished_at = datetime.now(UTC).isoformat()
        log.info("✓ %s synced: iceberg=%d doris=%d (%.1fs)", spec.api_name, iceberg_rows, doris_rows, st.elapsed_s)
    except Exception as e:
        st.status = "failed"
        st.error = str(e)[:300]
        st.elapsed_s = round(time.time() - t0, 1)
        st.finished_at = datetime.now(UTC).isoformat()
        log.error("✗ %s FAILED: %s", spec.api_name, st.error)


async def _resync_doris_only(spec: SyncSpec, st: TableState) -> None:
    """Re-provision + sync_now only (skip SeaTunnel — Iceberg already has rows).

    Used when raising the sync_now limit so previously-truncated Doris tables
    get the full row set. Idempotent: provision drops the index table first.
    """
    from datetime import UTC, datetime

    from ontology.config.container import container

    index_sync = container.index_sync_service
    t0 = time.time()
    log.info("═══ Re-sync Doris %s (iceberg=%d) ═══", spec.api_name, st.iceberg_rows)
    try:
        async with container.metadata_session() as meta:
            doris_rows, _ = await _provision_and_sync(index_sync, meta, spec)
        st.doris_rows = doris_rows
        st.status = "synced"
        st.error = ""
        st.elapsed_s = round(time.time() - t0, 1)
        st.finished_at = datetime.now(UTC).isoformat()
        log.info("✓ %s re-synced: doris=%d (%.1fs)", spec.api_name, doris_rows, st.elapsed_s)
    except Exception as e:
        st.status = "failed"
        st.error = str(e)[:300]
        st.elapsed_s = round(time.time() - t0, 1)
        log.error("✗ %s re-sync FAILED: %s", spec.api_name, st.error)


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated api_names to sync (default: all)")
    ap.add_argument("--reset", action="store_true", help="clear progress state and re-sync all")
    ap.add_argument("--retry-failed", action="store_true", help="only retry tables in 'failed' state")
    ap.add_argument(
        "--resync-doris",
        action="store_true",
        help="re-provision + sync_now for every already-synced table (e.g. after raising the sync_now limit)",
    )
    args = ap.parse_args(argv)

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        log.info("Cleared progress state.")

    state = SyncState.load()
    if not state.started_at:
        from datetime import UTC, datetime

        state.started_at = datetime.now(UTC).isoformat()

    selected = SPECS
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        selected = [s for s in SPECS if s.api_name in wanted]
    elif args.retry_failed:
        selected = [s for s in SPECS if state.tables.get(s.api_name) and state.tables[s.api_name].status == "failed"]
    elif args.resync_doris:
        # Re-provision + sync_now for every table that already has Iceberg data.
        selected = [s for s in SPECS if state.tables.get(s.api_name) and state.tables[s.api_name].iceberg_rows > 0]
    # --resync-doris combines with --only (only re-sync the selected subset).

    log.info("Will sync %d tables (serially): %s", len(selected), [s.api_name for s in selected])

    overall_t0 = time.time()
    for spec in selected:
        ts = state.tables.get(spec.api_name)
        if args.resync_doris:
            # Skip the full pipeline; only re-provision + sync_now.
            if not ts:
                ts = TableState(api_name=spec.api_name)
                state.tables[spec.api_name] = ts
            await _resync_doris_only(spec, ts)
            state.save()
            continue
        if ts and ts.status == "synced" and not args.retry_failed:
            log.info("→ %s already synced (iceberg=%d doris=%d), skip", spec.api_name, ts.iceberg_rows, ts.doris_rows)
            continue
        if not ts:
            ts = TableState(api_name=spec.api_name)
            state.tables[spec.api_name] = ts
        await _sync_one(spec, ts)
        state.save()  # persist progress after each table (resumable)

    # Summary
    log.info("═══════ SYNC SUMMARY ═══════")
    ok = sum(1 for t in state.tables.values() if t.status == "synced")
    fail = sum(1 for t in state.tables.values() if t.status == "failed")
    pend = sum(1 for t in state.tables.values() if t.status in ("pending", "syncing"))
    for t in state.tables.values():
        marker = "✓" if t.status == "synced" else "✗"
        log.info(
            "  %s %-22s iceberg=%-7d doris=%-7d %s (%.1fs)",
            marker,
            t.api_name,
            t.iceberg_rows,
            t.doris_rows,
            t.status,
            t.elapsed_s,
        )
    log.info("Total: %d synced, %d failed, %d pending (wall %.1fs)", ok, fail, pend, time.time() - overall_t0)
    state.save()
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
