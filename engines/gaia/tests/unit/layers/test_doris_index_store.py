"""Unit tests for DorisIndexStore.

aiomysql connection is mocked. Tests validate:
1. Index table management (CREATE, DROP)
2. Data manipulation (UPSERT, DELETE)
3. Point lookup (load_by_ids) and parameterized SQL execution (execute_sql)
4. Error paths and fallback behavior
"""

from unittest.mock import AsyncMock

import pytest

from ontology.core.exceptions import DorisUnavailableError
from ontology.layers.index.doris_index_store import DorisIndexStore


@pytest.fixture
def mock_conn() -> AsyncMock:
    """Mock aiomysql Connection."""
    conn = AsyncMock()
    conn.cursor = AsyncMock()
    return conn


@pytest.fixture
def mock_cursor() -> AsyncMock:
    """Mock aiomysql Cursor."""
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock()
    cursor.fetchall = AsyncMock()
    cursor.execute = AsyncMock()
    return cursor


@pytest.fixture
def store(mock_conn) -> DorisIndexStore:
    """Create DorisIndexStore with mocked connection."""
    return DorisIndexStore(connection=mock_conn)


class TestCreateIndexTable:
    """Index table DDL operations."""

    @pytest.mark.asyncio
    async def test_create_index_table(self, store, mock_conn, mock_cursor):
        """Create an indexed table for an ObjectType."""
        mock_conn.cursor.return_value = mock_cursor

        index_fields = [
            {"name": "order_id", "index_type": "PRIMARY_KEY"},
            {"name": "status", "index_type": "INVERTED"},
            {"name": "region", "index_type": "INVERTED"},
            {"name": "amount", "index_type": "RANGE"},
        ]

        await store.create_index_table(
            ontology_api_name="shop",
            object_type_api_name="order",
            fields=index_fields,
            partition_by=["created_at"],
        )

        # Verify a CREATE TABLE SQL was executed
        mock_cursor.execute.assert_awaited_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "CREATE TABLE" in sql.upper()
        assert "idx_shop__order" in sql

    @pytest.mark.asyncio
    async def test_drop_index_table(self, store, mock_conn, mock_cursor):
        """Drop an index table."""
        mock_conn.cursor.return_value = mock_cursor

        await store.drop_index_table("shop", "order")

        mock_cursor.execute.assert_awaited_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "DROP TABLE" in sql.upper()

    @pytest.mark.asyncio
    async def test_create_index_table_connection_error(self, store, mock_conn):
        """Connection error raises DorisUnavailableError."""
        mock_conn.cursor.side_effect = Exception("Doris connection refused")

        with pytest.raises(DorisUnavailableError, match="Doris unavailable"):
            await store.create_index_table(
                ontology_api_name="shop",
                object_type_api_name="order",
                fields=[],
            )


class TestUpsert:
    """Data upsert operations."""

    @pytest.mark.asyncio
    async def test_upsert_records(self, store, mock_conn, mock_cursor):
        """Upsert records into the index table."""
        mock_conn.cursor.return_value = mock_cursor

        records = [
            {"order_id": "1", "status": "active", "amount": 100.0},
            {"order_id": "2", "status": "pending", "amount": 200.0},
        ]

        await store.upsert("shop", "order", records)

        # Should have executed one INSERT for each record or batch
        assert mock_cursor.execute.awaited

    @pytest.mark.asyncio
    async def test_upsert_empty(self, store, mock_conn):
        """Upserting empty list is a no-op."""
        await store.upsert("shop", "order", [])

        mock_conn.cursor.assert_not_called()


class TestDeleteByIds:
    """Delete operations."""

    @pytest.mark.asyncio
    async def test_delete_by_ids(self, store, mock_conn, mock_cursor):
        """Delete records by primary key."""
        mock_conn.cursor.return_value = mock_cursor

        await store.delete_by_ids("shop", "order", ["1", "2", "3"], pk_column="order_id")

        mock_cursor.execute.assert_awaited_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "DELETE" in sql.upper()
        assert "IN" in sql.upper()

    @pytest.mark.asyncio
    async def test_delete_by_ids_empty(self, store, mock_conn):
        """Deleting with empty ID list is a no-op."""
        await store.delete_by_ids("shop", "order", [], pk_column="order_id")

        mock_conn.cursor.assert_not_called()


class TestLoadByIds:
    """Point-lookup full-row load (online read primary path)."""

    @pytest.mark.asyncio
    async def test_load_by_ids_returns_full_rows(self, store, mock_conn, mock_cursor):
        """load_by_ids returns full attribute rows for the given IDs."""
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [("1", "active"), ("2", "pending")]
        mock_cursor.description = [("order_id",), ("status",)]

        rows = await store.load_by_ids(
            ontology_api_name="shop",
            object_type_api_name="order",
            rids=["1", "2"],
            columns=["order_id", "status"],
            pk_column="order_id",
        )

        assert rows == [{"order_id": "1", "status": "active"}, {"order_id": "2", "status": "pending"}]
        sql = mock_cursor.execute.call_args[0][0]
        assert "SELECT" in sql.upper()
        assert "WHERE" in sql.upper() and "IN" in sql.upper()

    @pytest.mark.asyncio
    async def test_load_by_ids_empty(self, store, mock_conn, mock_cursor):
        """Empty IDs or columns returns [] without hitting Doris."""
        mock_conn.cursor.return_value = mock_cursor
        assert await store.load_by_ids("shop", "order", [], ["order_id"], "order_id") == []
        assert await store.load_by_ids("shop", "order", ["1"], [], "order_id") == []
        mock_cursor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_by_ids_doris_error_raises(self, store, mock_conn):
        """Doris errors raise DorisUnavailableError (triggers Trino fallback)."""
        mock_conn.cursor.side_effect = Exception("Doris down")
        with pytest.raises(DorisUnavailableError, match="load_by_ids failed"):
            await store.load_by_ids("shop", "order", ["1"], ["order_id"], "order_id")


class TestRidColumn:
    """System-injected rid column + reuse-or-generate lookups (T1.1/T1.2)."""

    @pytest.mark.asyncio
    async def test_create_index_table_injects_rid_column(self, store, mock_conn, mock_cursor):
        """create_index_table auto-injects a `rid` system column + INVERTED index."""
        mock_conn.cursor.return_value = mock_cursor
        await store.create_index_table(
            ontology_api_name="shop",
            object_type_api_name="order",
            fields=[{"name": "order_id", "index_type": "PRIMARY_KEY"}],
        )
        sql = mock_cursor.execute.call_args[0][0]
        # rid column present as a system column (not a user field)
        assert "`rid` VARCHAR(128)" in sql
        # INVERTED index on rid for hydrate_by_rids point lookups
        assert "INDEX idx_rid (`rid`) USING INVERTED" in sql
        # rid is NOT part of UNIQUE KEY (uniqueness via business PK + upsert)
        unique_key_clause = sql.split("UNIQUE KEY")[1].split(")")[0] if "UNIQUE KEY" in sql else ""
        assert "rid" not in unique_key_clause

    @pytest.mark.asyncio
    async def test_get_rid_by_pk_returns_rid(self, store, mock_conn, mock_cursor):
        """get_rid_by_pk returns the rid for an existing PK (reuse path)."""
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("ri.ontology.main.object.abc-123",)
        rid = await store.get_rid_by_pk("shop", "order", "order_id", "O1")
        assert rid == "ri.ontology.main.object.abc-123"
        sql, params = mock_cursor.execute.call_args[0][0], mock_cursor.execute.call_args[0][1]
        assert "SELECT `rid`" in sql
        assert "WHERE `order_id` = %s" in sql
        assert params == ["O1"]

    @pytest.mark.asyncio
    async def test_get_rid_by_pk_missing_returns_none(self, store, mock_conn, mock_cursor):
        """Missing PK → None (caller allocates a fresh rid)."""
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None
        assert await store.get_rid_by_pk("shop", "order", "order_id", "O1") is None

    @pytest.mark.asyncio
    async def test_get_rid_by_pk_empty_rid_returns_none(self, store, mock_conn, mock_cursor):
        """Empty-string rid (存量 row pre-backfill) treated as None."""
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("",)
        assert await store.get_rid_by_pk("shop", "order", "order_id", "O1") is None

    @pytest.mark.asyncio
    async def test_get_rid_by_pk_doris_error_raises(self, store, mock_conn):
        """Doris errors raise DorisUnavailableError."""
        mock_conn.cursor.side_effect = Exception("Doris down")
        with pytest.raises(DorisUnavailableError, match="get_rid_by_pk failed"):
            await store.get_rid_by_pk("shop", "order", "order_id", "O1")

    @pytest.mark.asyncio
    async def test_get_rids_by_pks_batch(self, store, mock_conn, mock_cursor):
        """Batch version returns {pk: rid} map; absent PKs map to None."""
        mock_conn.cursor.return_value = mock_cursor
        # Only O1 has a rid; O2 absent; O3 has empty rid (pre-backfill)
        mock_cursor.fetchall.return_value = [("O1", "ri.ontology.main.object.1"), ("O3", "")]
        result = await store.get_rids_by_pks("shop", "order", "order_id", ["O1", "O2", "O3"])
        assert result == {"O1": "ri.ontology.main.object.1", "O2": None, "O3": None}
        sql = mock_cursor.execute.call_args[0][0]
        assert "IN" in sql

    @pytest.mark.asyncio
    async def test_get_rids_by_pks_empty(self, store, mock_conn, mock_cursor):
        """Empty input returns {} without hitting Doris."""
        assert await store.get_rids_by_pks("shop", "order", "order_id", []) == {}
        mock_cursor.execute.assert_not_called()


class TestStoredOnlyColumns:
    """STORED_ONLY full-detail columns in create_index_table (ADR-001 revision)."""

    @pytest.mark.asyncio
    async def test_create_table_with_stored_only_uses_typed_columns(self, store, mock_conn, mock_cursor):
        """STORED_ONLY columns map to Doris types via _DORIS_TYPE_MAP."""
        mock_conn.cursor.return_value = mock_cursor

        fields = [
            {"name": "order_id", "index_type": "PRIMARY_KEY"},
            {"name": "status", "index_type": "INVERTED"},
            {"name": "amount", "index_type": "STORED_ONLY", "data_type": "DECIMAL"},
            {"name": "created_at", "index_type": "STORED_ONLY", "data_type": "TIMESTAMP"},
            {"name": "payload", "index_type": "STORED_ONLY", "data_type": "STRUCT"},
        ]

        await store.create_index_table(
            ontology_api_name="shop",
            object_type_api_name="order",
            fields=fields,
        )

        sql = mock_cursor.execute.call_args[0][0]
        # STORED_ONLY columns get typed definitions, not VARCHAR(255).
        assert "`amount` DECIMAL(18,4)" in sql
        assert "`created_at` DATETIME" in sql
        assert "`payload` STRING" in sql  # STRUCT → STRING (serialized)
        # INVERTED columns keep VARCHAR(255) (text hot mirror).
        assert "`status` VARCHAR(255)" in sql
        # PRIMARY_KEY without data_type falls back to VARCHAR(255).
        assert "`order_id` VARCHAR(255)" in sql
        assert "UNIQUE KEY" in sql.upper()

    async def test_primary_key_numeric_uses_bigint(self, store, mock_conn, mock_cursor):
        """PRIMARY_KEY with a numeric data_type maps to BIGINT/INT, not VARCHAR.

        Doris guidance: pure-numeric single-source PKs should use INT/BIGINT to
        preserve ORDER BY / range semantics (VARCHAR forces string dictionary
        order). This verifies the fix for the task_id/flight_id VARCHAR bug.
        """
        mock_conn.cursor.return_value = mock_cursor
        fields = [
            {"name": "order_id", "index_type": "PRIMARY_KEY", "data_type": "LONG"},
            {"name": "seq", "index_type": "PRIMARY_KEY", "data_type": "INTEGER"},
            {"name": "code", "index_type": "PRIMARY_KEY", "data_type": "STRING"},
        ]
        await store.create_index_table(ontology_api_name="shop", object_type_api_name="order", fields=fields)
        sql = mock_cursor.execute.call_args[0][0]
        assert "`order_id` BIGINT" in sql  # LONG → BIGINT
        assert "`seq` INT" in sql  # INTEGER → INT
        assert "`code` VARCHAR(255)" in sql  # STRING → VARCHAR (unchanged)


class TestConnectionPoolLifecycle:
    """The shared module-level pool is lazy-init and closeable."""

    @pytest.mark.asyncio
    async def test_close_pool_is_idempotent(self):
        """close_pool when no pool exists is a no-op (no error)."""
        from ontology.layers.index.doris_index_store import close_pool

        await close_pool()  # pool was never created — should not raise


class TestEscapeValArray:
    """_escape_val supports ARRAY<FLOAT> literals (§14.4 VECTOR embedding)."""

    def test_escape_list_float(self):
        from ontology.layers.index.doris_index_store import _escape_val

        assert _escape_val([0.1, 0.2, 0.3]) == "[0.1, 0.2, 0.3]"

    def test_escape_empty_list(self):
        from ontology.layers.index.doris_index_store import _escape_val

        assert _escape_val([]) == "[]"

    def test_escape_none_still_null(self):
        from ontology.layers.index.doris_index_store import _escape_val

        assert _escape_val(None) == "NULL"


class TestUpsertEmbedding:
    """upsert_embedding: UPDATE 单对象 embedding 列 (§14.4)."""

    @pytest.mark.asyncio
    async def test_upsert_embedding_calls_update(self, store, mock_conn, mock_cursor):
        mock_conn.cursor.return_value = mock_cursor
        await store.upsert_embedding(
            "shop", "order", "order_id", "O1", "profile_embedding_embedding",
            [0.1, 0.2, 0.3],
        )
        mock_cursor.execute.assert_awaited_once()
        sql = mock_cursor.execute.await_args.args[0]
        assert "UPDATE" in sql
        assert "profile_embedding_embedding" in sql
        assert "order_id" in sql
        # PK value parameterized
        assert mock_cursor.execute.await_args.args[1] == ["O1"]

    @pytest.mark.asyncio
    async def test_upsert_embedding_reserved_column_quoted(self, store, mock_conn, mock_cursor):
        """列名含 Doris 保留词 → 反引号 (status/user 等)."""
        mock_conn.cursor.return_value = mock_cursor
        await store.upsert_embedding(
            "shop", "order", "status", "O1", "user_embedding",
            [0.1],
        )
        sql = mock_cursor.execute.await_args.args[0]
        assert "`status`" in sql
        assert "`user_embedding`" in sql
