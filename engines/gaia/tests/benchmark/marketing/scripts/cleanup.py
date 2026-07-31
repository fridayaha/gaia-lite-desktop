"""State cleanup between benchmark runs (DESIGN.md §2.2 exception 2).

Direct-connect cleanup of benchmark state: drops the Marketing ontology (PG,
CASCADE) + Doris index tables + Iceberg tables + MySQL source schema. This is
a DESTRUCTIVE operation — requires --confirm (or --dry-run to preview).

DESIGN.md §2.2: cleanup is an exception to the write-path-via-API rule, but
must have --dry-run / --confirm double-safeguards and does NOT participate in
correctness assertions.

Usage:
    .venv/bin/python -m tests.benchmark.marketing.scripts.cleanup --dry-run
    .venv/bin/python -m tests.benchmark.marketing.scripts.cleanup --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("cleanup")

ONTO = "Marketing"
AI_PRODUCT_OTS = [
    "TdAnalysisDetails",
    "CompetitiveAnalysis",
    "StrategyExecutionAudit",
    "ScriptExecutionAnalysis",
    "FocusResistancePoints",
    "UserProfileBasicNote",
    "UserProfileOverview",
    "CustomerProfileEmotion",
    "CustomerProfileInferredTag",
    "CustomerProfileUsageScenario",
    "CustomerProfilePurchaseMotivation",
    "CustomerProfileProductPreference",
    "CustomerProfileResistance",
]
SOURCE_OTS = [
    "Dealership",
    "SalesConsultant",
    "LeadSource",
    "User",
    "Lead",
    "LeadAllocateRecord",
    "LeadDistributeRecord",
    "LeadFollowRecord",
    "ManualOutboundCall",
    "AiOutboundCall",
    "TestDrive",
    "TestDriveCar",
    "TestDriveRoute",
    "ChatRecord",
    "Recording",
]


async def _drop_pg_ontology(dry_run: bool) -> None:
    from sqlalchemy import text

    from ontology.config.database import async_session_factory

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT api_name FROM ontologies WHERE api_name=:n"), {"n": ONTO})
        exists = r.scalar_one_or_none()
        if not exists:
            log.info("[pg] ontology %s not found, skip", ONTO)
            return
        if dry_run:
            log.info("[pg] DRY-RUN: would DELETE FROM ontologies WHERE api_name='%s' (CASCADE)", ONTO)
            return
        await s.execute(text("DELETE FROM ontologies WHERE api_name=:n"), {"n": ONTO})
        await s.commit()
        log.info("[pg] deleted ontology %s (CASCADE)", ONTO)


async def _drop_doris_tables(dry_run: bool) -> None:
    from ontology.config.container import container

    idx = container.index
    for ot in SOURCE_OTS + AI_PRODUCT_OTS:
        table = f"idx_{ONTO.lower()}__" + ot[0].lower() + ot[1:]
        # The naming uses _to_snake on the OT api_name; recompute properly.
        from ontology.core.naming import _to_snake

        table = f"idx_{ONTO.lower()}__{_to_snake(ot)}"
        if dry_run:
            log.info("[doris] DRY-RUN: would DROP TABLE IF EXISTS %s", table)
            continue
        try:
            await idx.execute_sql(ONTO, ot, f"DROP TABLE IF EXISTS {table}")
            log.info("[doris] dropped %s", table)
        except Exception as e:
            log.info("[doris] %s: %s", table, str(e)[:80])


async def _drop_iceberg_tables(dry_run: bool) -> None:
    from ontology.config.container import container
    from ontology.core.naming import managed_dataset_api_name

    eng = container.engine
    for ot in SOURCE_OTS + AI_PRODUCT_OTS:
        t = managed_dataset_api_name(ot)
        if dry_run:
            log.info("[iceberg] DRY-RUN: would DROP TABLE IF EXISTS iceberg.ontology.%s", t)
            continue
        try:
            await eng.query(f"DROP TABLE IF EXISTS iceberg.ontology.{t}")
            log.info("[iceberg] dropped iceberg.ontology.%s", t)
        except Exception as e:
            log.info("[iceberg] %s: %s", t, str(e)[:80])


async def _drop_mysql_schema(dry_run: bool) -> None:
    import aiomysql

    if dry_run:
        log.info("[mysql] DRY-RUN: would DROP DATABASE IF EXISTS marketing_benchmark")
        return
    conn = await aiomysql.connect(
        host="localhost", port=3306, user="root", password="marketing123", autocommit=True, charset="utf8mb4"
    )
    try:
        async with conn.cursor() as cur:
            await cur.execute("DROP DATABASE IF EXISTS marketing_benchmark")
        log.info("[mysql] dropped database marketing_benchmark")
    finally:
        conn.close()


async def main(dry_run: bool, confirm: bool, skip_mysql: bool) -> int:
    if not dry_run and not confirm:
        log.error("Refusing to run without --dry-run or --confirm (double-safeguard, DESIGN.md §2.2).")
        return 2
    log.info("=== cleanup (dry_run=%s confirm=%s) ===", dry_run, confirm)
    await _drop_pg_ontology(dry_run)
    await _drop_doris_tables(dry_run)
    await _drop_iceberg_tables(dry_run)
    if not skip_mysql:
        await _drop_mysql_schema(dry_run)
    # Clear sync state file.
    from pathlib import Path

    state = Path(__file__).resolve().parent / ".sync_state.json"
    if state.exists():
        if dry_run:
            log.info("[state] DRY-RUN: would remove %s", state.name)
        else:
            state.unlink()
            log.info("[state] removed %s", state.name)
    log.info("=== cleanup complete ===")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="preview only, no changes")
    ap.add_argument("--confirm", action="store_true", help="actually perform destructive cleanup")
    ap.add_argument("--skip-mysql", action="store_true", help="skip dropping the MySQL source schema")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.confirm, args.skip_mysql)))
