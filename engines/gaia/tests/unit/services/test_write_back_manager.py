"""Unit tests for WriteBackManager — write-back to external source systems.

Tests validate:
1. Sync metadata injection (gaia_sync_tx, gaia_sync_user)
2. UPSERT SQL generation (INSERT ON CONFLICT)
3. MERGE SQL generation (alternative syntax)
4. Metadata extraction from source rows
"""

import pytest

from ontology.services.write_back_manager import WriteBackManager


@pytest.fixture
def manager() -> WriteBackManager:
    return WriteBackManager(gaia_sync_user="test-user")


class TestBuildWriteBackPayload:
    """Sync metadata injection into write-back payloads."""

    def test_injects_sync_metadata(self, manager):
        """Payload is augmented with gaia_sync_tx and gaia_sync_user."""
        changes = {"status": "shipped", "tracking_number": "TRK-001"}
        result = manager.build_write_back_payload(changes, sync_tx_id="tx-001")

        assert result["status"] == "shipped"
        assert result["tracking_number"] == "TRK-001"
        assert result[WriteBackManager.SYNC_TX_FIELD] == "tx-001"
        assert result[WriteBackManager.SYNC_USER_FIELD] == "test-user"

    def test_does_not_overwrite_existing_values(self, manager):
        """Original values are preserved alongside sync metadata."""
        changes = {"gaia_sync_tx": "original-value", "name": "test"}
        result = manager.build_write_back_payload(changes, sync_tx_id="tx-002")

        assert result["gaia_sync_tx"] == "tx-002"  # Overwritten
        assert result[WriteBackManager.SYNC_USER_FIELD] == "test-user"


class TestBuildUpsertSql:
    """UPSERT SQL generation (INSERT ON CONFLICT DO UPDATE)."""

    def test_builds_valid_upsert_sql(self, manager):
        """Generated SQL is valid INSERT ON CONFLICT (postgres dialect: $N)."""
        sql, params = manager.build_upsert_sql(
            table="orders",
            primary_key="order_id",
            changes={"order_id": "ORD-1", "status": "shipped"},
            sync_tx_id="tx-upsert-001",
        )
        assert "INSERT INTO orders" in sql
        assert "ON CONFLICT (order_id)" in sql
        assert "DO UPDATE SET" in sql
        # postgres positional placeholders $1, $2, ...
        assert "$1" in sql and "$2" in sql
        # params ordered: VALUES (4 cols incl sync metadata) then SET (4 cols)
        assert params[:4] == ["ORD-1", "shipped", "tx-upsert-001", manager._sync_user]
        assert params[4:] == ["ORD-1", "shipped", "tx-upsert-001", manager._sync_user]

    def test_upsert_includes_all_columns_in_set(self, manager):
        """All columns appear in both INSERT values and UPDATE SET clauses."""
        sql, _ = manager.build_upsert_sql(
            table="customers",
            primary_key="id",
            changes={"id": "C-1", "name": "Acme", "tier": "gold"},
            sync_tx_id="tx-batch",
        )
        # SET clause references each column (postgres $N placeholders)
        assert "name = $" in sql
        assert "tier = $" in sql
        assert f"{WriteBackManager.SYNC_TX_FIELD} = $" in sql

    def test_upsert_with_single_column(self, manager):
        """UPSERT with minimal change set still works."""
        sql, _ = manager.build_upsert_sql(
            table="inventory",
            primary_key="sku",
            changes={"sku": "SKU-1", "quantity": "50"},
            sync_tx_id="tx-single",
        )
        assert "INSERT INTO inventory" in sql
        assert "ON CONFLICT (sku)" in sql

    def test_upsert_mysql_dialect_uses_percent_placeholders(self, manager):
        """mysql dialect emits %s placeholders (aiomysql native).

        D4 fix: MySQL upsert uses UPDATE semantics (Action mutations carry
        only changed columns; INSERT...ON DUPLICATE KEY UPDATE would fail on
        NOT NULL columns absent from changes). The generated SQL is
        ``UPDATE table SET col=%s, ... WHERE pk=%s`` with SET values (excl.
        pk) followed by the pk for the WHERE clause.
        """
        sql, params = manager.build_upsert_sql(
            table="orders",
            primary_key="order_id",
            changes={"order_id": "ORD-1", "status": "shipped"},
            sync_tx_id="tx-mysql",
            dialect="mysql",
        )
        assert sql.startswith("UPDATE orders SET")
        assert "WHERE order_id = %s" in sql
        assert "%s" in sql
        assert "$" not in sql  # no postgres placeholders leak
        # params: SET values (status, gaia_sync_tx, gaia_sync_user — excl pk) + pk
        assert "shipped" in params
        assert "tx-mysql" in params
        assert manager._sync_user in params
        assert params[-1] == "ORD-1"  # pk for WHERE


class TestBuildMergeSql:
    """MERGE SQL generation for databases that prefer MERGE syntax."""

    def test_builds_valid_merge_sql(self, manager):
        """Generated SQL is valid MERGE statement."""
        sql = manager.build_merge_sql(
            table="employees",
            primary_key="emp_id",
            changes={"emp_id": "E-1", "department": "Engineering", "title": "Senior"},
            sync_tx_id="tx-merge-001",
        )
        assert "MERGE INTO employees" in sql
        assert "USING" in sql
        assert "ON tgt.emp_id = src.emp_id" in sql
        assert "WHEN MATCHED THEN UPDATE SET" in sql
        assert "WHEN NOT MATCHED THEN INSERT" in sql
        # Primary key should NOT be in the UPDATE SET
        assert "emp_id = src.emp_id" not in sql.split("WHEN MATCHED")[1]

    def test_merge_excludes_pk_from_update(self, manager):
        """Primary key column is excluded from UPDATE SET in MERGE."""
        sql = manager.build_merge_sql(
            table="products",
            primary_key="product_id",
            changes={"product_id": "P-1", "price": "99.99", "name": "Widget"},
            sync_tx_id="tx-merge-pk",
        )
        update_part = sql.split("WHEN MATCHED THEN UPDATE SET ")[1].split("WHEN NOT")[0]
        assert "product_id" not in update_part
        assert "price = src.price" in update_part


class TestExtractSyncMetadata:
    """Metadata extraction from source rows."""

    def test_extract_from_gaia_written_row(self, manager):
        """Row with gaia_sync_tx triggers detection."""
        row = {
            "id": "1",
            "name": "test",
            WriteBackManager.SYNC_TX_FIELD: "tx-sync-001",
            WriteBackManager.SYNC_USER_FIELD: "gaia-system",
        }
        metadata = manager.extract_sync_metadata_from_row(row)
        assert metadata["sync_tx"] == "tx-sync-001"
        assert metadata["sync_user"] == "gaia-system"

    def test_extract_from_clean_row(self, manager):
        """Row never touched by Gaia has no sync metadata."""
        row = {"id": "2", "name": "external-data"}
        metadata = manager.extract_sync_metadata_from_row(row)
        assert metadata["sync_tx"] is None
        assert metadata["sync_user"] is None

    def test_extract_from_row_with_missing_fields(self, manager):
        """Missing sync fields return None."""
        row = {"id": "3"}
        metadata = manager.extract_sync_metadata_from_row(row)
        assert metadata["sync_tx"] is None
