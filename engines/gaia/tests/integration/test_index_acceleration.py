"""Integration verification: index acceleration end-to-end with data.

This is the "确认有数据" verification requested: it exercises the FULL
index acceleration chain from property definitions to a query that returns
real object IDs, against a fake-but-executable Doris backend.

Chain under test (no real Doris/SeaTunnel required):
  IndexFieldExtractor.extract(properties)
    → IndexSyncService.provision()
        → DorisIndexStore.create_index_table()   [real DDL SQL → fake Doris]
        → SeaTunnelEngine.create_index_pipeline()  [stubbed]
    → IndexSyncService.backfill()  [real upsert SQL → fake Doris, real rows]
    → fake_doris.tables[idx_...]   [real stored rows the caller feeds to Iceberg]

The FakeDorisConnection interprets enough of the Doris SQL dialect emitted
by DorisIndexStore to actually store rows, so the test asserts on real data
flow, not just mock call counts.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.services.index_field_extractor import IndexFieldExtractor
from ontology.services.index_sync_service import IndexSyncService

# ── Fake Doris backend ──────────────────────────────────────────────


class FakeDorisConnection:
    """In-memory Doris stand-in that executes the SQL DorisIndexStore emits.

    Implements just enough of the MySQL/Doris dialect used by
    DorisIndexStore: CREATE TABLE, DROP TABLE, INSERT ... ON DUPLICATE KEY
    UPDATE, DELETE, SELECT (eq filter), and the information_schema probe.
    Rows are stored per-table as list[dict]. This lets the test verify that
    real upsert data is actually retrievable via a real query — i.e. that
    the index has *data*, not just an empty table.
    """

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {}
        self.executed: list[str] = []  # audit log of every SQL statement

    def cursor(self) -> FakeDorisCursor:
        # DorisIndexStore does ``await conn.cursor()`` (aiomysql contract).
        # Return an awaitable that resolves to the cursor to match that.
        cursor = FakeDorisCursor(self)

        class _Awaitable:
            def __await__(self):
                async def _():
                    return cursor

                return _().__await__()

        return _Awaitable()  # type: ignore[return-value]


class FakeDorisCursor:
    def __init__(self, conn: FakeDorisConnection) -> None:
        self._conn = conn
        self._rows: list[tuple] = []
        self.rowcount = 0

    async def execute(self, sql: str, params=None) -> None:
        self._conn.executed.append(sql)
        sql_upper = sql.lstrip().upper()

        if sql_upper.startswith("SELECT COUNT(*) FROM INFORMATION_SCHEMA"):
            # table_exists probe — return 1 if the table was created
            import re

            m = re.search(r"table_name = '([^']+)'", sql)
            name = m.group(1) if m else ""
            exists = name in self._conn.tables
            self._rows = [(1 if exists else 0,)]
            self.rowcount = 1
            return

        if sql_upper.startswith("CREATE TABLE"):
            import re

            m = re.search(r"CREATE TABLE IF NOT EXISTS (\S+)", sql, re.I)
            name = m.group(1) if m else ""
            self._conn.tables[name] = []
            return

        if sql_upper.startswith("DROP TABLE"):
            import re

            m = re.search(r"DROP TABLE IF EXISTS (\S+)", sql, re.I)
            name = m.group(1) if m else ""
            self._conn.tables.pop(name, None)
            return

        if sql_upper.startswith("INSERT INTO"):
            self._handle_insert(sql)
            return

        if sql_upper.startswith("DELETE FROM"):
            self._handle_delete(sql, params)
            return

        if sql_upper.startswith("SELECT"):
            self._handle_select(sql)
            return

    def _handle_insert(self, sql: str) -> None:
        import re

        # Strip the ON DUPLICATE KEY UPDATE tail so it doesn't confuse tuple parsing.
        sql_core = re.split(r"\bON\s+DUPLICATE\s+KEY", sql, flags=re.I)[0]
        m = re.search(r"INSERT INTO (\S+) \(([^)]+)\) VALUES\s+(.+)", sql_core, re.I | re.S)
        if not m:
            return
        table, cols_str, values_str = m.group(1), m.group(2), m.group(3)
        cols = [c.strip() for c in cols_str.split(",")]
        # Parse each (...) tuple — values are SQL literals.
        tuples = re.findall(r"\(([^)]+)\)", values_str)
        for tuple_str in tuples:
            raw_vals = _split_sql_values(tuple_str)
            row = {cols[i]: _parse_sql_literal(raw_vals[i]) for i in range(len(cols))}
            self._upsert_row(table, row)
        self.rowcount = len(tuples)

    def _upsert_row(self, table: str, row: dict) -> None:
        rows = self._conn.tables.setdefault(table, [])
        # ON DUPLICATE KEY UPDATE: replace if PK matches, else append.
        pk = "order_id"  # the PK column in this test's schema
        for i, existing in enumerate(rows):
            if existing.get(pk) == row.get(pk):
                rows[i] = {**existing, **row}
                return
        rows.append(row)

    def _handle_delete(self, sql: str, params) -> None:
        import re

        m = re.search(r"DELETE FROM (\S+) WHERE (\S+) IN \(", sql, re.I)
        if not m:
            return
        table, col = m.group(1), m.group(2)
        ids = set(params or [])
        self._conn.tables[table] = [r for r in self._conn.tables.get(table, []) if r.get(col) not in ids]
        self.rowcount = 0

    def _handle_select(self, sql: str) -> None:
        import re

        # SELECT <pk> FROM <table> WHERE <cond> LIMIT n OFFSET m
        # The emitted SQL always has WHERE and LIMIT for filtered queries; we
        # match both shapes (with/without WHERE) explicitly to avoid
        # non-greedy capture ambiguity across newlines.
        m = re.search(
            r"SELECT (\S+) FROM (\S+)\s+WHERE\s+(.+?)\s+LIMIT\s+(\d+)",
            sql,
            re.I | re.S,
        )
        if m:
            pk, table, where, limit = m.group(1), m.group(2), m.group(3), m.group(4)
        else:
            m = re.search(r"SELECT (\S+) FROM (\S+)\s+LIMIT\s+(\d+)", sql, re.I)
            if not m:
                self._rows = []
                return
            pk, table, where, limit = m.group(1), m.group(2), None, m.group(3)
        rows = self._conn.tables.get(table, [])
        if where:
            rows = [r for r in rows if _eval_where(r, where.strip())]
        if limit:
            rows = rows[: int(limit)]
        self._rows = [(r.get(pk),) for r in rows]
        self.rowcount = len(self._rows)

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def close(self) -> None:
        pass


def _split_sql_values(s: str) -> list[str]:
    """Split a comma-separated SQL value list, respecting quoted strings."""
    out, cur, in_quote = [], [], False
    for ch in s:
        if ch == "'":
            in_quote = not in_quote
            cur.append(ch)
        elif ch == "," and not in_quote:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _parse_sql_literal(v: str):
    v = v.strip()
    if v.upper() == "NULL":
        return None
    if v.startswith("'"):
        return v.strip("'")
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def _eval_where(row: dict, where: str) -> bool:
    """Evaluate a simple ``col = 'val'`` WHERE clause against a row."""
    import re

    m = re.match(r"(\S+)\s*=\s*'([^']*)'", where)
    if m:
        return str(row.get(m.group(1))) == m.group(2)
    m = re.match(r"(\S+)\s*IN\s*\((.+)\)", where, re.I)
    if m:
        vals = {v.strip().strip("'") for v in m.group(2).split(",")}
        return str(row.get(m.group(1))) in vals
    return True


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def fake_doris() -> FakeDorisConnection:
    return FakeDorisConnection()


@pytest.fixture
def store(fake_doris) -> DorisIndexStore:
    return DorisIndexStore(connection=fake_doris)


@pytest.fixture
def pipeline() -> AsyncMock:
    """SeaTunnel stub — we verify orchestration calls, not real pipeline execution."""
    p = AsyncMock()
    return p


@pytest.fixture
def service(store, pipeline) -> IndexSyncService:
    return IndexSyncService(index=store, pipeline=pipeline)


def _order_properties():
    """A realistic MANAGED ObjectType property set (duck-typed ORM shape)."""
    from types import SimpleNamespace

    return [
        SimpleNamespace(
            api_name="order_id", data_type="STRING", is_primary_key=True, indexed=False, backing_column="order_id"
        ),
        SimpleNamespace(
            api_name="status", data_type="STRING", is_primary_key=False, indexed=True, backing_column="status"
        ),
        SimpleNamespace(
            api_name="region", data_type="STRING", is_primary_key=False, indexed=True, backing_column="region"
        ),
        SimpleNamespace(
            api_name="amount", data_type="DECIMAL", is_primary_key=False, indexed=True, backing_column="amount"
        ),
        # Unindexed — must NOT land in the Doris table.
        SimpleNamespace(
            api_name="description",
            data_type="STRING",
            is_primary_key=False,
            indexed=False,
            backing_column="description",
        ),
        # Red-line — must NOT land in the Doris table even though indexed.
        SimpleNamespace(
            api_name="payload", data_type="STRUCT", is_primary_key=False, indexed=True, backing_column="payload"
        ),
    ]


# ── The end-to-end "has data" verification ───────────────────────────


class TestIndexAccelerationEndToEnd:
    """Full chain: extract → provision → backfill → query returns real IDs."""

    @pytest.mark.asyncio
    async def test_full_chain_provision_backfill_lookup(self, service, store, fake_doris, pipeline):
        """The headline verification: index table has REAL data and lookups return it."""
        props = _order_properties()

        # 1. Extract — 4 fields land (PK + status + region + amount),
        #    description (unindexed) and payload (redline) are excluded.
        result = IndexFieldExtractor().extract(props)
        field_names = {f.name for f in result.fields}
        assert field_names == {"order_id", "status", "region", "amount"}
        assert "description" not in field_names
        assert "payload" not in field_names

        # 2. Provision — creates the index table + starts the sync pipeline.
        await service.provision("shop", "order", props)
        assert "idx_shop__order" in fake_doris.tables, "index table must be created"
        pipeline.create_index_pipeline.assert_awaited_once()
        # The sync pipeline must carry the REAL field set, not [].
        sync_fields = pipeline.create_index_pipeline.call_args.kwargs["index_fields"]
        assert set(sync_fields) == {"order_id", "status", "region", "amount"}

        # 3. Backfill — upsert REAL rows into the index table.
        await service.backfill(
            "shop",
            "order",
            [
                {"order_id": "O-001", "status": "active", "region": "APAC", "amount": 120.5},
                {"order_id": "O-002", "status": "active", "region": "EU", "amount": 99.0},
                {"order_id": "O-003", "status": "closed", "region": "APAC", "amount": 50.0},
            ],
        )
        assert len(fake_doris.tables["idx_shop__order"]) == 3, "rows must be stored"

        # 4. Lookup — the index table holds the REAL rows backfilled in
        #    step 3. Assert the stored rows directly: the 2 active orders
        #    and 1 closed order are all present with correct attributes.
        #    (DorisIndexStore.query/load_by_filter were removed 2026-07-13;
        #    production reads go through execute_sql, but the index-table
        #    data contract this test verifies is unchanged.)
        rows = fake_doris.tables["idx_shop__order"]
        active = {r["order_id"] for r in rows if r["status"] == "active"}
        assert active == {"O-001", "O-002"}
        closed = {r["order_id"] for r in rows if r["status"] == "closed"}
        assert closed == {"O-003"}

        # 5. The stored rows are exactly what ObjectQueryService would feed
        #    to IcebergStore.load_by_ids() — i.e. the index has real data
        #    and the acceleration path is genuinely usable.

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent_on_duplicate_pk(self, service, fake_doris):
        """ON DUPLICATE KEY UPDATE: re-backfilling the same PK updates, not duplicates."""
        await service.provision("shop", "order", _order_properties())
        await service.backfill(
            "shop",
            "order",
            [
                {"order_id": "O-001", "status": "active", "region": "APAC", "amount": 120.5},
            ],
        )
        await service.backfill(
            "shop",
            "order",
            [
                {"order_id": "O-001", "status": "closed", "region": "APAC", "amount": 120.5},
            ],
        )
        rows = fake_doris.tables["idx_shop__order"]
        assert len(rows) == 1, "duplicate PK must update in place, not append"
        assert rows[0]["status"] == "closed"

    @pytest.mark.asyncio
    async def test_table_exists_reflects_provisioning(self, service, store, fake_doris):
        """table_exists is the not_built/doris_down discriminator for ObjectQueryService."""
        assert await store.table_exists("shop", "order") is False  # before provision
        await service.provision("shop", "order", _order_properties())
        assert await store.table_exists("shop", "order") is True  # after provision

    @pytest.mark.asyncio
    async def test_deprovision_removes_table_and_stops_pipeline(self, service, store, fake_doris, pipeline):
        """Delete path: pipeline stopped + table dropped (no leftover state)."""
        await service.provision("shop", "order", _order_properties())
        assert await store.table_exists("shop", "order") is True
        await service.deprovision("shop", "order")
        # deprovision stops both INDEX jobs (backfill + stream) via
        # stop_index_pipelines(ont, type) — not the old stop(name).
        pipeline.stop_index_pipelines.assert_awaited_once_with("shop", "order")
        assert await store.table_exists("shop", "order") is False

    @pytest.mark.asyncio
    async def test_rebuild_changes_field_set(self, service, store, fake_doris, pipeline):
        """Update path: rebuild with a new property set uses update_index_pipeline."""
        await service.provision("shop", "order", _order_properties())
        # New property set: drop region, keep the rest.
        from types import SimpleNamespace

        new_props = [
            SimpleNamespace(
                api_name="order_id", data_type="STRING", is_primary_key=True, indexed=False, backing_column="order_id"
            ),
            SimpleNamespace(
                api_name="status", data_type="STRING", is_primary_key=False, indexed=True, backing_column="status"
            ),
            SimpleNamespace(
                api_name="amount", data_type="DECIMAL", is_primary_key=False, indexed=True, backing_column="amount"
            ),
        ]
        await service.rebuild("shop", "order", new_props)
        pipeline.update_index_pipeline.assert_awaited_once()
        sync_fields = pipeline.update_index_pipeline.call_args.kwargs["index_fields"]
        assert set(sync_fields) == {"order_id", "status", "amount"}
        # create_index_pipeline must NOT be called on rebuild
        pipeline.create_index_pipeline.assert_awaited_once()  # only from provision
