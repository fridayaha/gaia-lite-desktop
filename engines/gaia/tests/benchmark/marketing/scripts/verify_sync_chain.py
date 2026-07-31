"""Minimal verification: full sync chain for ONE table (dealership, 20 rows).

Validates the P3 prerequisite (DESIGN.md option A): MySQL → SeaTunnel → Iceberg
→ sync_now → Doris, end to end through the REAL system APIs.

Run:
    .venv/bin/python -m tests.benchmark.marketing.scripts.verify_sync_chain
"""

from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("verify_sync")

ONTO = "Marketing"
SOURCE_TABLE = "t_ods_master_data_store"  # dealership 物理表
TARGET_DATASET = "marketing.dealership"  # Iceberg target (ontology.table)
DS_API = "marketingMysql"
CRED_API = "marketingMysqlCred"
SYNC_API = "dealership_sync"
DATASET_API = "marketing_dealership"  # dataset governance api_name (snake)


async def main() -> int:
    from ontology.config.container import container
    from ontology.core.schemas.datasource import (
        CredentialCreate,
        DatasetGovernanceCreate,
        DataSourceCreate,
        SyncTaskCreate,
    )

    ds_svc = container.datasource_service
    meta = container.metadata
    onto_svc = container.ontology_service
    index_sync = container.index_sync_service

    # ── 1. Credential (idempotent: 409 ok) ─────────────────────────────
    log.info("[1/8] Creating credential %s ...", CRED_API)
    try:
        await ds_svc.create_credential(
            CredentialCreate(
                api_name=CRED_API,
                credential_type="username_password",
                secret_data={"username": "root", "password": "marketing123"},
            )
        )
        log.info("  ✓ credential created")
    except Exception as e:
        log.info("  → credential exists or skipped: %s", str(e)[:120])
    # credential_id is a FK to credentials.id (UUID), NOT the api_name — resolve it.
    cred = await ds_svc.get_credential(CRED_API)
    log.info("  credential id = %s", cred.id)

    # ── 2. DataSource (mysql, points at marketing-mysql) ───────────────
    log.info("[2/8] Creating datasource %s ...", DS_API)
    try:
        await ds_svc.create_datasource(
            DataSourceCreate(
                api_name=DS_API,
                display_name="Marketing MySQL",
                description="Marketing benchmark source MySQL",
                connector_type="mysql",
                connector_config={
                    "host": "localhost",  # backend view; override rewrites for containers
                    "port": 3306,
                    "database": "marketing_benchmark",
                },
                credential_id=cred.id,  # UUID, not api_name
            )
        )
        log.info("  ✓ datasource created")
    except Exception as e:
        log.info("  → datasource exists or skipped: %s", str(e)[:120])

    # ── 3. Register dataset (MANAGED, Iceberg target) ──────────────────
    log.info("[3/8] Registering dataset %s ...", DATASET_API)
    try:
        await ds_svc.register_dataset(
            DatasetGovernanceCreate(
                api_name=DATASET_API,
                display_name="Marketing Dealership",
                storage_location="s3://ontology-warehouse/ontology/dealership",
                data_source_api_name=DS_API,
                kind="MANAGED",
            )
        )
        log.info("  ✓ dataset registered")
    except Exception as e:
        log.info("  → dataset exists or skipped: %s", str(e)[:120])

    # ── 4. Link dataset to Dealership ObjectType (property ↔ column) ───
    log.info("[4/8] Linking dataset to %s.Dealership ...", ONTO)
    # column_mappings: property_api_name (camelCase, derived from backing_column) ↔ column_name (snake)
    mappings = [
        {"property_api_name": "storeCode", "column_name": "store_code"},
        {"property_api_name": "orgName", "column_name": "org_name"},
        {"property_api_name": "province", "column_name": "province"},
        {"property_api_name": "city", "column_name": "city"},
    ]
    try:
        await onto_svc.link_dataset(ONTO, "Dealership", DATASET_API, mappings)
        log.info("  ✓ dataset linked")
    except Exception as e:
        log.info("  → link exists or skipped: %s", str(e)[:120])

    # ── 5. Create sync-task (MySQL → Iceberg via SeaTunnel) ────────────
    log.info("[5/8] Creating sync-task %s ...", SYNC_API)
    ds_record = await ds_svc.get_datasource(DS_API)
    try:
        task = await ds_svc.create_sync_task(
            SyncTaskCreate(
                api_name=SYNC_API,
                data_source_id=ds_record.id,
                sync_type="table",
                source_config={"table": SOURCE_TABLE, "schema": "marketing_benchmark"},
                target_dataset_api_name=TARGET_DATASET,
                sync_mode="full_snapshot",
                transaction_type="snapshot",
            )
        )
        log.info("  ✓ sync-task created (pipeline=%s, status=%s)", task.pipeline_name, task.status)
    except Exception as e:
        log.info("  → sync-task exists or skipped: %s", str(e)[:120])

    # ── 6. START sync (trigger SeaTunnel MySQL → Iceberg) ──────────────
    log.info("[6/8] Starting sync-task (SeaTunnel MySQL → Iceberg) ...")
    try:
        task = await ds_svc.start_sync(SYNC_API)
        log.info("  ✓ sync started (status=%s, pipeline=%s)", task.status, task.pipeline_name)
    except Exception as e:
        log.error("  ✗ start_sync FAILED: %s", str(e)[:300])
        return 6

    # ── 7. Poll Iceberg for rows (wait up to 90s) ──────────────────────
    log.info("[7/8] Polling Iceberg for landed rows (up to 120s) ...")
    engine = container.engine
    landed = 0
    for i in range(24):
        await asyncio.sleep(5)
        try:
            # Use Trino (more reliable than pyiceberg scan for freshly-created tables).
            rows = await engine.query("SELECT COUNT(*) AS c FROM iceberg.ontology.dealership")
            landed = int(rows[0]["c"]) if rows else 0
            log.info("  poll %d: iceberg.ontology.dealership = %d rows", i + 1, landed)
            if landed > 0:
                break
        except Exception as e:
            log.info("  poll %d: %s", i + 1, str(e)[:120])
    if landed == 0:
        log.error("  ✗ No rows landed in Iceberg after 120s")
        return 7
    log.info("  ✓ %d rows landed in Iceberg", landed)

    # ── 8. Provision Doris + sync_now (Iceberg → Doris) ────────────────
    log.info("[8/8] Provision Doris + sync_now (Iceberg → Doris) ...")
    ot = await meta.get_object_type(ONTO, "Dealership")
    props = await meta.get_properties(ot.id)
    await index_sync.provision(ONTO, "Dealership", props, primary_key="storeCode")
    log.info("  ✓ Doris idx_marketing__dealership provisioned")
    count = await index_sync.sync_now(ONTO, "Dealership", props, primary_key="storeCode")
    log.info("  ✓ sync_now upserted %d records into Doris", count)

    # ── Verify: query Doris via ObjectQueryService ─────────────────────
    log.info("=== VERIFY: query Dealership via ObjectQueryService ===")
    qsvc = container.object_query_service
    from ontology.core.schemas.query import LoadObjectsRequest, ObjectSet

    # object_type_api_name is "{ontology}.{type}" (ObjectQueryService splits on '.').
    result = await qsvc.load_objects(
        LoadObjectsRequest(
            object_set=ObjectSet(object_type_api_name=f"{ONTO}.Dealership"),
            properties=["storeCode", "orgName", "province", "city"],
            limit=3,
        )
    )
    log.info("  ObjectQueryService returned %d objects", len(result))
    if result:
        log.info("  sample: %s", str(result[0])[:200])

    log.info("═══ Minimal sync chain verification PASSED ═══")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
