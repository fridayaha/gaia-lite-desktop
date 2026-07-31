"""Unit tests for IngestionFilter — feedback loop prevention.

Tests validate:
1. Incremental query rewriting (WHERE clause injection)
2. Batch query rewriting (NOT IN exclusion)
3. Row-level skipping decisions
4. Metadata extraction for watermark tracking
"""

import pytest

from ontology.services.ingestion_filter import IngestionFilter


@pytest.fixture
def filt() -> IngestionFilter:
    return IngestionFilter()


class TestRewriteIncrementalQuery:
    """Incremental query rewriting with feedback loop prevention."""

    def test_no_filter_when_no_sync_tx(self, filt):
        """When last_sync_tx_id is None, original SQL is returned unchanged."""
        original = "SELECT * FROM orders WHERE updated_at > :watermark"
        result = filt.rewrite_incremental_query(original, last_sync_tx_id=None)
        assert result == original

    def test_adds_filter_clause_with_where(self, filt):
        """Adds gaia_sync_tx filter after existing WHERE clause."""
        original = "SELECT * FROM orders WHERE updated_at > :watermark"
        result = filt.rewrite_incremental_query(original, last_sync_tx_id="tx-123")

        assert "WHERE updated_at > :watermark" in result
        assert "gaia_sync_tx IS NULL" in result
        assert "gaia_sync_tx != 'tx-123'" in result

    def test_adds_filter_clause_without_where(self, filt):
        """Adds WHERE clause when original has none."""
        original = "SELECT * FROM orders"
        result = filt.rewrite_incremental_query(original, last_sync_tx_id="tx-456")

        assert "WHERE" in result
        assert "gaia_sync_tx IS NULL" in result
        assert "gaia_sync_tx != 'tx-456'" in result

    def test_case_insensitive_where_detection(self, filt):
        """WHERE detection is case-insensitive."""
        original = "SELECT * FROM orders where updated_at > :watermark"
        result = filt.rewrite_incremental_query(original, last_sync_tx_id="tx-case")

        assert "gaia_sync_tx" in result
        assert "where updated_at" in result.lower()

    def test_complex_query_with_joins(self, filt):
        """Complex query with JOINs and subqueries is handled."""
        original = (
            "SELECT o.*, c.name FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.updated_at > :watermark"
        )
        result = filt.rewrite_incremental_query(original, last_sync_tx_id="tx-complex")

        assert "gaia_sync_tx IS NULL" in result
        assert "JOIN customers" in result


class TestRewriteBatchQuery:
    """Batch query rewriting with multi-value exclusion."""

    def test_no_filter_when_empty_list(self, filt):
        """Empty known_sync_tx_ids returns original SQL."""
        original = "SELECT * FROM inventory WHERE updated_at > :watermark"
        result = filt.rewrite_batch_query(original, known_sync_tx_ids=[])
        assert result == original

    def test_no_filter_when_none(self, filt):
        """None known_sync_tx_ids returns original SQL."""
        original = "SELECT * FROM inventory"
        result = filt.rewrite_batch_query(original, known_sync_tx_ids=None)
        assert result == original

    def test_not_in_exclusion(self, filt):
        """Multiple tx IDs use NOT IN clause."""
        original = "SELECT * FROM inventory WHERE updated_at > :watermark"
        result = filt.rewrite_batch_query(original, known_sync_tx_ids=["tx-1", "tx-2", "tx-3"])

        assert "NOT IN" in result
        assert "'tx-1'" in result
        assert "'tx-2'" in result
        assert "'tx-3'" in result

    def test_adds_where_when_none_exists(self, filt):
        """Adds WHERE clause for batch exclusion."""
        original = "SELECT * FROM inventory"
        result = filt.rewrite_batch_query(original, known_sync_tx_ids=["tx-single"])

        assert result.startswith("SELECT * FROM inventory")
        assert "WHERE" in result
        assert "NOT IN" in result


class TestShouldSkipRow:
    """Row-level filtering decisions."""

    def test_skip_gaia_written_row(self, filt):
        """Row with matching gaia_sync_tx is skipped."""
        row = {"id": "1", "name": "test", "gaia_sync_tx": "tx-match"}
        assert filt.should_skip_row(row, last_sync_tx_id="tx-match") is True

    def test_keep_row_with_no_sync_tx(self, filt):
        """Row without gaia_sync_tx is kept."""
        row = {"id": "2", "name": "external"}
        assert filt.should_skip_row(row, last_sync_tx_id="tx-any") is False

    def test_keep_row_with_different_sync_tx(self, filt):
        """Row with different gaia_sync_tx is kept."""
        row = {"id": "3", "name": "old-write", "gaia_sync_tx": "tx-old"}
        assert filt.should_skip_row(row, last_sync_tx_id="tx-new") is False

    def test_keep_all_when_no_last_sync_tx(self, filt):
        """When last_sync_tx_id is None, all rows are kept."""
        row = {"id": "4", "gaia_sync_tx": "tx-something"}
        assert filt.should_skip_row(row, last_sync_tx_id=None) is False


class TestExtractWatermarkMetadata:
    """Metadata extraction for watermark tracking."""

    def test_extract_from_gaia_row(self, filt):
        """Row with Gaia sync metadata is detected."""
        row = {"id": "1", "gaia_sync_tx": "tx-watermark", "gaia_sync_user": "gaia-system"}
        meta = filt.extract_watermark_metadata(row)

        assert meta["has_sync_tx"] is True
        assert meta["sync_tx"] == "tx-watermark"
        assert meta["sync_user"] == "gaia-system"

    def test_extract_from_clean_row(self, filt):
        """Row without Gaia metadata returns nulls."""
        row = {"id": "2", "name": "clean"}
        meta = filt.extract_watermark_metadata(row)

        assert meta["has_sync_tx"] is False
        assert meta["sync_tx"] is None
        assert meta["sync_user"] is None

    def test_extract_from_row_with_none_sync_tx(self, filt):
        """Row with gaia_sync_tx=None is treated as clean."""
        row = {"id": "3", "gaia_sync_tx": None}
        meta = filt.extract_watermark_metadata(row)

        assert meta["has_sync_tx"] is False
        assert meta["sync_tx"] is None
