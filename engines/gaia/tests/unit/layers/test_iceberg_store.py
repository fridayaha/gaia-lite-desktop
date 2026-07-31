"""Unit tests for IcebergStore.

The pyiceberg ``RestCatalog``/``Table`` and the ``TrinoQueryEngine`` are
mocked. Tests validate:
1. Metadata reads (schema, snapshots) delegate correctly to pyiceberg
2. Data reads/writes go through Trino with the right SQL
3. Time-travel queries use ``FOR VERSION AS OF``
4. Schema evolution delegates to pyiceberg's update_schema
5. ``NotFoundError`` is raised for missing tables
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import IcebergUnavailableError, NotFoundError
from ontology.core.schemas.dataset import ColumnDef, DatasetSnapshot, WriteResult
from ontology.layers.dataset.iceberg_store import IcebergStore

# ── Fixtures ──


@pytest.fixture
def mock_catalog() -> MagicMock:
    """Mock pyiceberg RestCatalog (sync API — calls via to_thread)."""
    return MagicMock()


@pytest.fixture
def mock_engine() -> AsyncMock:
    """Mock TrinoQueryEngine (async query)."""
    engine = AsyncMock()
    engine.query = AsyncMock(return_value=[])
    return engine


@pytest.fixture
def store(mock_catalog, mock_engine) -> IcebergStore:
    """Create IcebergStore with mocked catalog + engine."""
    return IcebergStore(engine=mock_engine, catalog=mock_catalog)


def _make_field(name: str, type_str: str, required: bool) -> MagicMock:
    """Build a fake pyiceberg schema field."""
    field = MagicMock()
    field.name = name
    field.field_type = type_str
    field.required = required
    return field


def _make_snapshot(sid: int, ts: int, operation: str = "append") -> MagicMock:
    """Build a fake pyiceberg Snapshot."""
    snap = MagicMock()
    snap.snapshot_id = sid
    snap.timestamp_ms = ts
    snap.summary = {"operation": operation}
    return snap


def _make_table(fields=None, snapshots=None, current=None) -> MagicMock:
    """Build a fake pyiceberg Table."""
    table = MagicMock()
    schema = MagicMock()
    schema.fields = fields or []
    table.schema.return_value = schema
    table.snapshots.return_value = iter(snapshots or [])
    table.current_snapshot.return_value = current
    # update_schema context manager
    updater = MagicMock()
    table.update_schema.return_value.__enter__.return_value = updater
    table.update_schema.return_value.__exit__.return_value = None
    return table


# ── Schema ──


class TestSchema:
    @pytest.mark.asyncio
    async def test_get_schema(self, store, mock_catalog):
        mock_catalog.load_table.return_value = _make_table(
            fields=[
                _make_field("id", "string", True),
                _make_field("name", "string", False),
            ]
        )
        result = await store.get_schema("ontology.employees")
        assert len(result.columns) == 2
        assert result.columns[0].name == "id"
        assert result.columns[0].type == "string"
        assert result.columns[0].nullable is False
        assert result.columns[1].nullable is True
        mock_catalog.load_table.assert_called_once_with("ontology.employees")

    @pytest.mark.asyncio
    async def test_get_schema_table_not_found(self, store, mock_catalog):
        mock_catalog.load_table.side_effect = Exception("no such table")
        with pytest.raises(NotFoundError, match="Dataset not found"):
            await store.get_schema("ontology.nonexistent")

    @pytest.mark.asyncio
    async def test_get_schema_qualifies_bare_api_name(self, store, mock_catalog, monkeypatch):
        # Dataset api_names are stored without a namespace prefix.
        from ontology.layers.dataset import iceberg_store as istore

        monkeypatch.setattr(istore.settings, "iceberg_namespace", "ontology")
        mock_catalog.load_table.return_value = _make_table(fields=[_make_field("id", "string", True)])
        await store.get_schema("object_types_raw")
        mock_catalog.load_table.assert_called_once_with("ontology.object_types_raw")

    @pytest.mark.asyncio
    async def test_evolve_schema(self, store, mock_catalog):
        table = _make_table()
        mock_catalog.load_table.return_value = table
        additions = [
            ColumnDef(name="email", type="string", nullable=True),
            ColumnDef(name="age", type="int", nullable=False),
        ]
        await store.evolve_schema("ontology.employees", additions)
        updater = table.update_schema.return_value.__enter__.return_value
        assert updater.add_column.call_count == 2
        # First addition passed correctly (path is positional arg)
        first_call = updater.add_column.call_args_list[0]
        assert first_call.args[0] == "email"
        assert first_call.kwargs["required"] is False

    @pytest.mark.asyncio
    async def test_evolve_schema_empty(self, store, mock_catalog):
        await store.evolve_schema("ontology.employees", [])
        mock_catalog.load_table.assert_not_called()

    @pytest.mark.asyncio
    async def test_evolve_schema_failure_raises(self, store, mock_catalog):
        table = _make_table()
        updater = table.update_schema.return_value.__enter__.return_value
        updater.add_column.side_effect = RuntimeError("boom")
        mock_catalog.load_table.return_value = table
        with pytest.raises(IcebergUnavailableError):
            await store.evolve_schema("ontology.employees", [ColumnDef(name="x", type="string")])

    # ── Catalog First: managed table creation ──

    @pytest.mark.asyncio
    async def test_create_managed_table_new(self, store, mock_catalog, monkeypatch):
        """New table → create_table with PK identifier, comments, required, props."""
        from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

        # table does not exist yet
        mock_catalog.load_table.side_effect = RuntimeError("404")
        monkeypatch.setattr(store, "ensure_namespace", AsyncMock())
        monkeypatch.setattr(store, "_table_exists", AsyncMock(return_value=False))

        schema = ManagedTableSchema(
            columns=[
                ManagedColumnDef(name="id", type="long", nullable=False, comment="主键", is_primary_key=True),
                ManagedColumnDef(name="name", type="string", nullable=True, comment="名称"),
            ],
            table_comment="订单表",
        )
        await store.create_managed_table("orders", schema, properties={"gaia.source-datasource": "erp"})

        mock_catalog.create_table.assert_called_once()
        call = mock_catalog.create_table.call_args
        assert call.kwargs["identifier"] == "ontology.orders"
        # schema carries identifier + field docs
        ic_schema = call.kwargs["schema"]
        assert ic_schema.identifier_field_ids == [1]
        props = call.kwargs["properties"]
        assert props["comment"] == "订单表"
        assert props["gaia.source-datasource"] == "erp"
        assert props["format-version"] == "2"

    @pytest.mark.asyncio
    async def test_create_managed_table_no_primary_key(self, store, mock_catalog, monkeypatch):
        """Table with no PK column must not pass identifier_field_ids=None
        (pyiceberg rejects None — regression: previously ``pk_ids or None``
        turned an empty list into None → validation error)."""
        from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

        monkeypatch.setattr(store, "ensure_namespace", AsyncMock())
        monkeypatch.setattr(store, "_table_exists", AsyncMock(return_value=False))

        schema = ManagedTableSchema(
            columns=[ManagedColumnDef(name="name", type="string", nullable=True, comment="名称")],
            table_comment="无主键表",
        )
        await store.create_managed_table("no_pk", schema)

        call = mock_catalog.create_table.call_args
        ic_schema = call.kwargs["schema"]
        # No PK → identifier_field_ids stays at pyiceberg default (empty list),
        # never None. ``identifier_field_ids`` kwarg must be ABSENT so the
        # pydantic default_factory=list kicks in.
        assert "identifier_field_ids" not in call.kwargs
        assert ic_schema.identifier_field_ids == []

    @pytest.mark.asyncio
    async def test_create_managed_table_existing_reconciles(self, store, mock_catalog, monkeypatch):
        """Existing table → ensure_schema (never drop, history preserved)."""
        from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

        monkeypatch.setattr(store, "ensure_namespace", AsyncMock())
        monkeypatch.setattr(store, "_table_exists", AsyncMock(return_value=True))
        ensure_spy = AsyncMock()
        monkeypatch.setattr(store, "ensure_schema", ensure_spy)

        schema = ManagedTableSchema(columns=[ManagedColumnDef(name="id", type="long", is_primary_key=True)])
        await store.create_managed_table("orders", schema)
        mock_catalog.create_table.assert_not_called()
        ensure_spy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ensure_schema_adds_only_missing(self, store, mock_catalog):
        """ensure_schema adds columns absent from the table, skips existing."""
        from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

        table = _make_table([_make_field("id", "long", True), _make_field("name", "string", False)])
        mock_catalog.load_table.return_value = table
        schema = ManagedTableSchema(
            columns=[
                ManagedColumnDef(name="id", type="long", is_primary_key=True),  # exists, skip
                ManagedColumnDef(name="email", type="string", nullable=True, comment="邮箱"),  # new
            ]
        )
        await store.ensure_schema("orders", schema)
        updater = table.update_schema.return_value.__enter__.return_value
        updater.add_column.assert_called_once()
        call = updater.add_column.call_args
        assert call.args[0] == "email"
        assert call.kwargs["doc"] == "邮箱"


# ── Snapshots ──


class TestSnapshots:
    @pytest.mark.asyncio
    async def test_get_snapshots(self, store, mock_catalog):
        mock_catalog.load_table.return_value = _make_table(
            snapshots=[_make_snapshot(1, 1000), _make_snapshot(2, 2000, "overwrite")]
        )
        result = await store.get_snapshots("ontology.employees")
        assert len(result) == 2
        assert all(isinstance(s, DatasetSnapshot) for s in result)
        assert result[0].snapshot_id == 1
        assert result[1].operation == "overwrite"

    @pytest.mark.asyncio
    async def test_get_latest_snapshot(self, store, mock_catalog):
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(42, 3000))
        result = await store.get_latest_snapshot("ontology.employees")
        assert isinstance(result, DatasetSnapshot)
        assert result.snapshot_id == 42

    @pytest.mark.asyncio
    async def test_get_latest_snapshot_no_data(self, store, mock_catalog):
        mock_catalog.load_table.return_value = _make_table(current=None)
        result = await store.get_latest_snapshot("ontology.empty")
        assert result is None


# ── Namespace / table lifecycle ──


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_ensure_namespace(self, store, mock_catalog):
        await store.ensure_namespace("ontology")
        mock_catalog.create_namespace_if_not_exists.assert_called_once_with("ontology")

    @pytest.mark.asyncio
    async def test_ensure_namespace_swallows_errors(self, store, mock_catalog):
        mock_catalog.create_namespace_if_not_exists.side_effect = RuntimeError("oops")
        # Should not raise
        await store.ensure_namespace("ontology")

    @pytest.mark.asyncio
    async def test_drop_table_if_exists(self, store, mock_catalog):
        result = await store.drop_table_if_exists("ontology", "employees")
        assert result is True
        mock_catalog.drop_table.assert_called_once_with("ontology.employees")

    @pytest.mark.asyncio
    async def test_drop_table_if_exists_failure(self, store, mock_catalog):
        mock_catalog.drop_table.side_effect = RuntimeError("oops")
        result = await store.drop_table_if_exists("ontology", "employees")
        assert result is False


# ── Data reads via Trino ──


class TestTrinoReads:
    @pytest.mark.asyncio
    async def test_load_by_ids(self, store, mock_engine):
        mock_engine.query.return_value = [{"id": "42", "name": "Alice"}]
        result = await store.load_by_ids("ontology.employees", ids=["42", "99"], columns=["id", "name"])
        assert result == [{"id": "42", "name": "Alice"}]
        sql = mock_engine.query.call_args.args[0]
        assert "SELECT id, name FROM iceberg.ontology.employees" in sql
        # Pure-integer ids are rendered as UNQUOTED numeric literals so a
        # BIGINT PK column doesn't fail with TYPE_MISMATCH
        # ("Cannot find common type between bigint and varchar").
        assert "WHERE id IN (42, 99)" in sql
        assert "'42'" not in sql

    @pytest.mark.asyncio
    async def test_load_by_ids_quotes_non_numeric_ids(self, store, mock_engine):
        """Non-numeric ids stay quoted string literals (and are escaped)."""
        mock_engine.query.return_value = []
        await store.load_by_ids(
            "ontology.employees",
            ids=["abc", "it's a quote"],
            columns=["id"],
        )
        sql = mock_engine.query.call_args.args[0]
        # Non-numeric ids are quoted; embedded single quotes are doubled.
        assert "WHERE id IN ('abc', 'it''s a quote')" in sql

    @pytest.mark.asyncio
    async def test_load_by_ids_mixed_numeric_and_string(self, store, mock_engine):
        """Mixed id types render each per its own literal kind."""
        mock_engine.query.return_value = []
        await store.load_by_ids("ontology.employees", ids=["42", "abc"], columns=["id"])
        sql = mock_engine.query.call_args.args[0]
        assert "WHERE id IN (42, 'abc')" in sql

    @pytest.mark.asyncio
    async def test_load_by_ids_empty(self, store, mock_engine):
        result = await store.load_by_ids("ontology.employees", ids=[], columns=["id"])
        assert result == []
        mock_engine.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_by_ids_as_of_uses_time_travel(self, store, mock_engine):
        mock_engine.query.return_value = [{"id": "1", "name": "Alice (v1)"}]
        await store.load_by_ids_as_of("ontology.employees", ids=["1"], columns=["id", "name"], snapshot_id=12345)
        sql = mock_engine.query.call_args.args[0]
        assert "FOR VERSION AS OF 12345" in sql

    @pytest.mark.asyncio
    async def test_scan_as_of(self, store, mock_engine):
        mock_engine.query.return_value = [{"id": "1"}]
        await store.scan_as_of("ontology.employees", columns=["id"], snapshot_id=999, limit=50)
        sql = mock_engine.query.call_args.args[0]
        assert "SELECT id FROM iceberg.ontology.employees FOR VERSION AS OF 999 LIMIT 50" == sql


# ── Data writes via Trino ──


class TestTrinoWrites:
    @pytest.mark.asyncio
    async def test_append(self, store, mock_engine, mock_catalog):
        # get_latest_snapshot called after insert
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(7, 7000))
        rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        result = await store.append("ontology.employees", rows)
        assert isinstance(result, WriteResult)
        assert result.rows_written == 2
        sql = mock_engine.query.call_args.args[0]
        assert sql.startswith("INSERT INTO iceberg.ontology.employees")
        assert "(1, 'Alice')" in sql
        assert "(2, 'Bob')" in sql

    @pytest.mark.asyncio
    async def test_append_empty(self, store, mock_engine):
        result = await store.append("ontology.employees", [])
        assert result.rows_written == 0
        mock_engine.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_overwrite(self, store, mock_engine, mock_catalog):
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(8, 8000))
        rows = [{"id": 1, "name": "Updated"}]
        result = await store.overwrite("ontology.employees", rows)
        assert result.rows_written == 1
        # First call is DELETE, second is INSERT
        calls = [c.args[0] for c in mock_engine.query.call_args_list]
        assert any(c.startswith("DELETE FROM iceberg.ontology.employees") for c in calls)
        assert any(c.startswith("INSERT INTO iceberg.ontology.employees") for c in calls)

    @pytest.mark.asyncio
    async def test_overwrite_empty_deletes_only(self, store, mock_engine, mock_catalog):
        mock_catalog.load_table.return_value = _make_table(current=None)
        result = await store.overwrite("ontology.employees", [])
        assert result.rows_written == 0
        assert mock_engine.query.call_count == 1
        assert mock_engine.query.call_args.args[0].startswith("DELETE FROM")


# ── merge: MERGE INTO upsert/delete (action-sync-outbox-design.md §8.4) ──


class TestMerge:
    """IcebergStore.merge builds a Trino MERGE INTO with business PK ON clause.

    Trino INSERT doesn't dedupe on v2 upsert tables, so MERGE INTO is the only
    way to overwrite by PK. PK is the business primary_key backing_column,
    NOT object_id (design §3.3).
    """

    @pytest.mark.asyncio
    async def test_merge_upsert(self, store, mock_engine, mock_catalog):
        """CREATE/UPDATE → WHEN MATCHED THEN UPDATE + WHEN NOT MATCHED THEN INSERT."""
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(9, 9000, "overwrite"))
        rows = [{"flight_id": "CA123", "status": "delayed"}]
        result = await store.merge("ontology.flight", rows, ["flight_id"], delete=False)
        assert isinstance(result, WriteResult)
        assert result.rows_written == 1
        sql = mock_engine.query.call_args.args[0]
        assert sql.startswith("MERGE INTO iceberg.ontology.flight AS target")
        assert "USING (VALUES ('CA123', 'delayed')) AS source (flight_id, status)" in sql
        assert "ON target.flight_id = source.flight_id" in sql
        assert "WHEN MATCHED THEN UPDATE SET flight_id = source.flight_id, status = source.status" in sql
        assert "WHEN NOT MATCHED THEN INSERT (flight_id, status) VALUES (source.flight_id, source.status)" in sql

    @pytest.mark.asyncio
    async def test_merge_delete(self, store, mock_engine, mock_catalog):
        """DELETE → WHEN MATCHED THEN DELETE (source 只需 PK 列)."""
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(10, 10000, "delete"))
        rows = [{"flight_id": "CA123"}]
        result = await store.merge("ontology.flight", rows, ["flight_id"], delete=True)
        assert result.rows_written == 1
        sql = mock_engine.query.call_args.args[0]
        assert sql.startswith("MERGE INTO iceberg.ontology.flight AS target")
        assert "USING (VALUES ('CA123')) AS source (flight_id)" in sql
        assert "ON target.flight_id = source.flight_id" in sql
        assert "WHEN MATCHED THEN DELETE" in sql
        assert "UPDATE" not in sql  # delete 模式不含 UPDATE 子句
        assert "INSERT" not in sql

    @pytest.mark.asyncio
    async def test_merge_empty_rows(self, store, mock_engine):
        """空 rows → 不发任何 SQL (幂等 no-op)."""
        result = await store.merge("ontology.flight", [], ["flight_id"], delete=False)
        assert result.rows_written == 0
        mock_engine.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_merge_requires_pk_columns(self, store):
        """无 pk_columns → OntologyError (design §10: MERGE 必须按业务 PK)."""
        from ontology.core.exceptions import OntologyError

        with pytest.raises(OntologyError, match="at least one pk_column"):
            await store.merge("ontology.flight", [{"flight_id": "CA123"}], [], delete=False)

    @pytest.mark.asyncio
    async def test_merge_multi_row_upsert(self, store, mock_engine, mock_catalog):
        """多行 upsert: VALUES 多组, ON 单列 PK."""
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(11, 11000))
        rows = [
            {"flight_id": "CA123", "status": "delayed"},
            {"flight_id": "CA456", "status": "on_time"},
        ]
        await store.merge("ontology.flight", rows, ["flight_id"], delete=False)
        sql = mock_engine.query.call_args.args[0]
        assert "('CA123', 'delayed')" in sql
        assert "('CA456', 'on_time')" in sql

    @pytest.mark.asyncio
    async def test_merge_rejects_invalid_pk_identifier(self, store):
        """pk_column 走白名单校验, 拒绝注入."""
        from ontology.core.exceptions import OntologyError

        with pytest.raises(OntologyError, match="Invalid SQL identifier"):
            await store.merge("ontology.flight", [{"a; DROP": 1}], ["a; DROP"], delete=False)

    @pytest.mark.asyncio
    async def test_merge_rejects_invalid_column_identifier(self, store):
        """row 列名也走白名单校验."""
        from ontology.core.exceptions import OntologyError

        with pytest.raises(OntologyError, match="Invalid SQL identifier"):
            await store.merge("ontology.flight", [{"bad col!": 1}], ["good_id"], delete=False)

    @pytest.mark.asyncio
    async def test_merge_composite_pk(self, store, mock_engine, mock_catalog):
        """联合 PK: ON 条件用 AND 连接多列."""
        mock_catalog.load_table.return_value = _make_table(current=_make_snapshot(12, 12000))
        rows = [{"tenant": "t1", "seq": 1, "v": "x"}]
        await store.merge("ontology.event", rows, ["tenant", "seq"], delete=False)
        sql = mock_engine.query.call_args.args[0]
        assert "ON target.tenant = source.tenant AND target.seq = source.seq" in sql


# ── _id_literal: SQL literal rendering for object ids ──


class TestIdLiteral:
    """_id_literal renders ids as safe SQL literals.

    Pure-integer ids become unquoted numeric literals (avoids the BIGINT↔varchar
    TYPE_MISMATCH on integer PK columns); everything else is a quoted, escaped
    string literal. This is the injection boundary for the IN-list, so it is
    tested directly.
    """

    @pytest.mark.parametrize(
        "v, expected",
        [
            ("42", "42"),
            ("99", "99"),
            ("0", "0"),
            ("-7", "-7"),
            (42, "42"),  # int input
            (0, "0"),
        ],
    )
    def test_numeric_is_unquoted(self, v, expected):
        assert IcebergStore._id_literal(v) == expected

    @pytest.mark.parametrize(
        "v, expected",
        [
            ("abc", "'abc'"),
            ("user_1", "'user_1'"),
            ("it's a quote", "'it''s a quote'"),
            ("a'b", "'a''b'"),
            ("42abc", "'42abc'"),  # not pure-numeric → quoted
            ("12.5", "'12.5'"),  # float-looking → quoted (isdigit() is False)
            ("", "''"),
        ],
    )
    def test_non_numeric_is_quoted_and_escaped(self, v, expected):
        assert IcebergStore._id_literal(v) == expected

    def test_none_is_null(self):
        assert IcebergStore._id_literal(None) == "NULL"

    @pytest.mark.parametrize(
        "injection",
        [
            "1; DROP TABLE x",
            "1) OR 1=1 --",
            "' OR '1'='1",
            "x'; DROP TABLE y; --",
        ],
    )
    def test_sql_injection_is_neutralized(self, injection):
        """Any non-pure-numeric payload is wrapped in a single quoted, escaped
        literal — it cannot break out of the string context.

        The safety property: the output is exactly one SQL string literal,
        i.e. starts and ends with a single quote and every interior single
        quote is doubled, so there is no unescaped delimiter that would let
        the payload terminate the literal early.
        """
        lit = IcebergStore._id_literal(injection)
        assert lit.startswith("'") and lit.endswith("'")
        assert len(lit) >= 2
        inner = lit[1:-1]
        # Every single quote inside must be doubled (no lone, unescaped quote
        # that could close the literal prematurely).
        assert "'" not in inner.replace("''", "")
        # The payload is fully contained between the delimiters — nothing
        # leaks as a bare SQL token outside the literal.
        assert lit == "'" + inner + "'"


class TestEnsureWarehouseBucket:
    """ensure_warehouse_bucket creates the S3 bucket if missing (idempotent).

    Iceberg/Gravitino don't create the backing S3 bucket; SeaTunnel's Iceberg
    sink fails with NoSuchBucketException when it's missing. Standard S3FileIO
    doesn't auto-create either, so ensure_warehouse_bucket must call the S3
    CreateBucket API itself.
    """

    @pytest.mark.asyncio
    async def test_bucket_exists_skips_create(self, store, monkeypatch):
        """head_bucket succeeds → create_bucket NOT called."""
        s3_client = AsyncMock()
        s3_client.head_bucket = AsyncMock(return_value={})  # exists
        s3_client.create_bucket = AsyncMock()
        session = MagicMock()
        session.create_client.return_value.__aenter__ = AsyncMock(return_value=s3_client)
        session.create_client.return_value.__aexit__ = AsyncMock(return_value=None)
        import aiobotocore.session as aibs

        monkeypatch.setattr(aibs, "get_session", lambda: session)

        await store.ensure_warehouse_bucket()
        s3_client.head_bucket.assert_awaited_once()
        s3_client.create_bucket.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_bucket_missing_creates_it(self, store, monkeypatch):
        """head_bucket fails → create_bucket called with parsed bucket name."""
        s3_client = AsyncMock()
        s3_client.head_bucket = AsyncMock(side_effect=Exception("404 NoSuchBucket"))
        s3_client.create_bucket = AsyncMock()
        session = MagicMock()
        session.create_client.return_value.__aenter__ = AsyncMock(return_value=s3_client)
        session.create_client.return_value.__aexit__ = AsyncMock(return_value=None)
        import aiobotocore.session as aibs

        monkeypatch.setattr(aibs, "get_session", lambda: session)

        await store.ensure_warehouse_bucket()
        s3_client.head_bucket.assert_awaited_once()
        s3_client.create_bucket.assert_awaited_once()
        # bucket name parsed from default warehouse s3://ontology-warehouse/
        call = s3_client.create_bucket.await_args
        assert call.kwargs["Bucket"] == "ontology-warehouse"
        # us-east-1 → no LocationConstraint
        assert "CreateBucketConfiguration" not in call.kwargs

    @pytest.mark.asyncio
    async def test_ensure_namespace_calls_ensure_bucket(self, store, monkeypatch):
        """ensure_namespace ensures the bucket BEFORE creating the namespace."""
        called = []

        async def fake_bucket():
            called.append("bucket")

        monkeypatch.setattr(store, "ensure_warehouse_bucket", fake_bucket)
        store._catalog = MagicMock()
        store._catalog.create_namespace_if_not_exists = MagicMock()
        # _run wraps sync calls via to_thread; mock it to call directly.
        monkeypatch.setattr(store, "_run", lambda fn, *a, **k: fn(*a, **k))

        await store.ensure_namespace("ontology")
        assert called == ["bucket"]
        store._catalog.create_namespace_if_not_exists.assert_called_once_with("ontology")
