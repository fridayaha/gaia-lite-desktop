"""Seed the `flight` dataset with a realistic 6-column schema + sample rows.

Run: .venv/bin/python scripts/seed_flight_dataset.py

Creates the Iceberg table `ontology.flight` (namespace `ontology`) with 6
columns matching a flight-info business object, then appends 3 sample rows.
After this, `GET /api/datasets/flight/schema` returns the real schema, so the
BuildWith scaffold flow in the CreateObjectWizard has real columns to derive
an ObjectType from.

Idempotent: drops + recreates the table if it already exists.
"""

from __future__ import annotations

import asyncio

from ontology.config.container import container
from ontology.core.schemas.dataset import ColumnDef, DatasetSchema

# Realistic flight-info schema (6 columns) for scaffold to chew on.
FLIGHT_COLUMNS = [
    ColumnDef(name="id", type="bigint", nullable=False, comment="航班记录主键"),
    ColumnDef(name="flight_no", type="varchar", nullable=False, comment="航班号"),
    ColumnDef(name="airline", type="varchar", nullable=False, comment="航空公司"),
    ColumnDef(name="status", type="varchar", nullable=True, comment="航班状态"),
    ColumnDef(name="depart_time", type="timestamp(6)", nullable=True, comment="起飞时间"),
    ColumnDef(name="created_at", type="timestamp(6)", nullable=True, comment="记录创建时间"),
]

SAMPLE_ROWS = [
    {
        "id": 1,
        "flight_no": "CA1831",
        "airline": "中国国际航空",
        "status": "准点",
        "depart_time": "2026-07-01 08:30:00",
        "created_at": "2026-06-28 10:00:00",
    },
    {
        "id": 2,
        "flight_no": "MU5102",
        "airline": "中国东方航空",
        "status": "延误",
        "depart_time": "2026-07-01 14:05:00",
        "created_at": "2026-06-28 10:01:00",
    },
    {
        "id": 3,
        "flight_no": "CZ3107",
        "airline": "中国南方航空",
        "status": "取消",
        "depart_time": "2026-07-01 22:15:00",
        "created_at": "2026-06-28 10:02:00",
    },
]

NAMESPACE = "ontology"
TABLE = "flight"


async def main() -> None:
    store = container.dataset
    # Iceberg table identifier is "namespace.table" → "ontology.flight".
    dataset = f"{NAMESPACE}.{TABLE}"

    # 1. Ensure namespace exists.
    await store.ensure_namespace(NAMESPACE)
    print(f"[seed] namespace '{NAMESPACE}' ensured")

    # 2. Drop existing table (idempotent recreate).
    dropped = await store.drop_table_if_exists(NAMESPACE, TABLE)
    print(f"[seed] dropped existing table: {dropped}")

    # 3. Create the table with the explicit schema via the pyiceberg catalog.
    catalog = store.catalog
    # pyiceberg create_table signature: create_table(identifier, schema=Schema(...))
    from pyiceberg.schema import Schema
    from pyiceberg.types import (
        LongType,
        NestedField,
        StringType,
        TimestampType,
    )

    schema = Schema(
        NestedField(field_id=1, name="id", field_type=LongType(), required=True),
        NestedField(field_id=2, name="flight_no", field_type=StringType(), required=True),
        NestedField(field_id=3, name="airline", field_type=StringType(), required=True),
        NestedField(field_id=4, name="status", field_type=StringType(), required=False),
        NestedField(field_id=5, name="depart_time", field_type=TimestampType(), required=False),
        NestedField(field_id=6, name="created_at", field_type=TimestampType(), required=False),
    )
    await store._run(catalog.create_table, f"{NAMESPACE}.{TABLE}", schema=schema)  # noqa: SLF001
    print(f"[seed] created table '{dataset}' with {len(FLIGHT_COLUMNS)} columns")

    # 4. Append sample rows via Trino INSERT with explicit casts (the
    # IcebergStore.append infers literal types too narrowly — varchar(19)
    # for timestamps — so we INSERT directly with CASTs to match the table).
    engine = store.engine
    table_ref = store._trino_table_ref(dataset)  # noqa: SLF001 → iceberg.ontology.flight
    rows_sql = [
        "(BIGINT '1', 'CA1831', '国航', '准点', TIMESTAMP '2026-07-01 08:30:00', TIMESTAMP '2026-06-28 10:00:00')",
        "(BIGINT '2', 'MU5102', '东航', '延误', TIMESTAMP '2026-07-01 14:05:00', TIMESTAMP '2026-06-28 10:01:00')",
        "(BIGINT '3', 'CZ3107', '南航', '取消', TIMESTAMP '2026-07-01 22:15:00', TIMESTAMP '2026-06-28 10:02:00')",
    ]
    await engine.query(
        f"INSERT INTO {table_ref} "
        "(id, flight_no, airline, status, depart_time, created_at) VALUES " + ", ".join(rows_sql)
    )
    print("[seed] appended 3 rows")

    # 5. Verify schema is readable.
    got: DatasetSchema = await store.get_schema(dataset)
    print(f"[seed] get_schema returned {len(got.columns)} columns:")
    for c in got.columns:
        print(f"   - {c.name} | {c.type} | nullable={c.nullable}")


if __name__ == "__main__":
    asyncio.run(main())
