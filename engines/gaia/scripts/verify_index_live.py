"""Live verification against the REAL Doris container (port 9030).

Run: .venv/bin/python scripts/verify_index_live.py

Verifies the index acceleration layer against a live Doris FE, no mocks:
  1. IndexFieldExtractor derives real fields from properties
  2. IndexSyncService.provision creates a REAL Doris index table (real DDL)
  3. backfill upserts REAL rows into Doris (real DML)
  4. DorisIndexStore.query filters and returns REAL object IDs
  5. table_exists reflects the real table state
  6. deprovision drops the real table

SeaTunnelEngine is stubbed — its real pipeline execution needs a full
Iceberg table + Zeta job, tracked separately. This script isolates and
proves the Doris side is genuinely functional.
"""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.services.index_field_extractor import IndexFieldExtractor
from ontology.services.index_sync_service import IndexSyncService

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _order_properties():
    # Mirrors the API batch-create shape: is_primary_key is NOT set per-property;
    # the ObjectType's primary_key ("order_id") is passed separately.
    return [
        SimpleNamespace(
            api_name="order_id", data_type="STRING", is_primary_key=False, indexed=False, physical_column="order_id"
        ),
        SimpleNamespace(
            api_name="status", data_type="STRING", is_primary_key=False, indexed=True, physical_column="status"
        ),
        SimpleNamespace(
            api_name="region", data_type="STRING", is_primary_key=False, indexed=True, physical_column="region"
        ),
        SimpleNamespace(
            api_name="amount", data_type="DECIMAL", is_primary_key=False, indexed=True, physical_column="amount"
        ),
        # redline — must NOT be indexed
        SimpleNamespace(
            api_name="payload", data_type="STRUCT", is_primary_key=False, indexed=True, physical_column="payload"
        ),
    ]


async def main() -> None:
    # Real Doris connection (localhost:9030, root, ontology db)
    import aiomysql

    conn = await aiomysql.connect(host="localhost", port=9030, user="root", password="", db="ontology")
    store = DorisIndexStore(connection=conn)
    # SeaTunnel stubbed — real Zeta job needs Iceberg table (separate verification)
    pipeline = AsyncMock()
    service = IndexSyncService(index=store, pipeline=pipeline)

    api = "live_order"
    # Clean up any leftover table from a prior failed run.
    await store.drop_index_table(api)

    print("\n=== 1. Extract fields (IndexFieldExtractor) ===")
    result = IndexFieldExtractor().extract(_order_properties(), primary_key="order_id")
    print(f"   fields: {[(f.name, f.index_type) for f in result.fields]}")
    print(f"   skipped: {result.skipped}")
    assert {f.name for f in result.fields} == {"order_id", "status", "region", "amount"}
    assert ("payload",) == (result.skipped[0][0],)

    print("\n=== 2. table_exists BEFORE provision (expect False) ===")
    exists = await store.table_exists("live_shop", api)
    print(f"   exists={exists}")
    assert exists is False

    print("\n=== 3. provision (real Doris DDL + stubbed SeaTunnel) ===")
    await service.provision("live_shop", api, _order_properties(), primary_key="order_id")
    pipeline.create_index_pipeline.assert_awaited_once()
    print(f"   sync pipeline fields: {pipeline.create_index_pipeline.call_args.kwargs['index_fields']}")

    print("\n=== 4. table_exists AFTER provision (expect True) ===")
    exists = await store.table_exists("live_shop", api)
    print(f"   exists={exists}")
    assert exists is True

    print("\n=== 5. backfill real rows into Doris ===")
    n = await service.backfill(
        "live_shop",
        api,
        [
            {"order_id": "O-001", "status": "active", "region": "APAC", "amount": 120.5},
            {"order_id": "O-002", "status": "active", "region": "EU", "amount": 99.0},
            {"order_id": "O-003", "status": "closed", "region": "APAC", "amount": 50.0},
        ],
    )
    print(f"   upserted {n} rows")

    print("\n=== 6. query status=active (expect O-001, O-002) ===")
    from ontology.core.schemas.index import IndexFilter, IndexQuery

    r = await store.query(
        IndexQuery(
            ontology_api_name="live_shop",
            object_type_api_name=api,
            filters=[IndexFilter(field="status", op="eq", value="active")],
            limit=100,
            pk_column="order_id",
        )
    )
    print(f"   object_ids={sorted(r.object_ids)} total={r.total}")
    assert set(r.object_ids) == {"O-001", "O-002"}, f"got {r.object_ids}"

    print("\n=== 7. query region=APAC (expect O-001, O-003) ===")
    r2 = await store.query(
        IndexQuery(
            ontology_api_name="live_shop",
            object_type_api_name=api,
            filters=[IndexFilter(field="region", op="eq", value="APAC")],
            limit=100,
            pk_column="order_id",
        )
    )
    print(f"   object_ids={sorted(r2.object_ids)} total={r2.total}")
    assert set(r2.object_ids) == {"O-001", "O-003"}, f"got {r2.object_ids}"

    print("\n=== 8. upsert idempotency (re-backfill O-001 with status=closed) ===")
    await service.backfill(
        "live_shop",
        api,
        [
            {"order_id": "O-001", "status": "closed", "region": "APAC", "amount": 120.5},
        ],
    )
    r3 = await store.query(
        IndexQuery(
            ontology_api_name="live_shop",
            object_type_api_name=api,
            filters=[IndexFilter(field="status", op="eq", value="active")],
            limit=100,
            pk_column="order_id",
        )
    )
    print(f"   active after re-upsert={sorted(r3.object_ids)} (expect O-002 only)")
    assert set(r3.object_ids) == {"O-002"}, f"got {r3.object_ids}"

    print("\n=== 9. deprovision (drop real table) ===")
    await service.deprovision("live_shop", api)
    exists = await store.table_exists("live_shop", api)
    print(f"   exists after deprovision={exists}")
    assert exists is False

    conn.close()
    print("\n✅ ALL LIVE DORIS CHECKS PASSED — index acceleration layer works on real Doris.")


if __name__ == "__main__":
    asyncio.run(main())
