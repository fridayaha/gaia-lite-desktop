"""IngestionFilter — prevent feedback loops during incremental ingestion.

Implements the Palantir Feedback Loop Prevention pattern (OSv2):
    - Rewrites ingestion SQL to exclude rows previously written back by Gaia
    - Uses transaction tagging (gaia_sync_tx, gaia_sync_user) for filtering
    - Three defense layers: transaction marking, incremental filtering, snapshot hash comparison

Defense layers:
    1. Transaction Marking: WriteBackManager injects gaia_sync_tx/gaia_sync_user
    2. Incremental Filtering: This class rewrites ingestion queries to exclude
       rows with known gaia_sync_tx values
    3. Snapshot Hash Comparison: (future) Compare source hash vs applied hash

Feedback loop example:
    1. Source DB row changes → Ingestion pulls into Gaia → Iceberg
    2. Action updates row → WriteBackManager writes back to Source DB
    3. Source DB row changes again (due to write-back) → Ingestion could re-pull
    4. This class prevents step 3 by filtering out the write-back's own changes
"""

from typing import Any


class IngestionFilter:
    """Prevent feedback loops during incremental data ingestion.

    Rewrites ingestion queries to exclude rows written back by Gaia itself.
    Uses transaction tagging (gaia_sync_tx, gaia_sync_user) for filtering.

    Usage:
        filt = IngestionFilter()
        safe_sql = filt.rewrite_incremental_query(
            original_sql="SELECT * FROM source_table WHERE updated_at > :watermark",
            last_sync_tx_id="tx-2026-001",
        )
    """

    # Column names injected by WriteBackManager for tracking
    SYNC_TX_COLUMN = "gaia_sync_tx"
    SYNC_USER_COLUMN = "gaia_sync_user"

    def rewrite_incremental_query(
        self,
        original_sql: str,
        last_sync_tx_id: str | None = None,
    ) -> str:
        """Rewrite an incremental ingestion SQL to filter out self-written rows.

        Transforms:
            SELECT ... FROM table WHERE updated_at > :watermark
        Into:
            SELECT ... FROM table
            WHERE updated_at > :watermark
              AND (gaia_sync_tx IS NULL OR gaia_sync_tx != :last_sync_tx)

        This prevents Gaia from re-ingesting rows it wrote back to the source.

        Args:
            original_sql: The original ingestion query.
            last_sync_tx_id: The last known Gaia sync transaction ID.
                            If None, no filtering is applied.

        Returns:
            Rewritten SQL with feedback loop prevention clause.
        """
        if last_sync_tx_id is None:
            return original_sql

        filter_clause = f"({self.SYNC_TX_COLUMN} IS NULL OR {self.SYNC_TX_COLUMN} != '{last_sync_tx_id}')"

        # Check if WHERE clause exists (case-insensitive)
        sql_upper = original_sql.upper()
        if "WHERE" in sql_upper:
            return f"{original_sql} AND {filter_clause}"
        else:
            return f"{original_sql} WHERE {filter_clause}"

    def rewrite_batch_query(
        self,
        original_sql: str,
        known_sync_tx_ids: list[str] | None = None,
    ) -> str:
        """Rewrite a batch ingestion query to filter out multiple sync transactions.

        Args:
            original_sql: The original batch ingestion query.
            known_sync_tx_ids: List of known Gaia sync transaction IDs to exclude.

        Returns:
            Rewritten SQL with multi-value exclusion clause.
        """
        if not known_sync_tx_ids:
            return original_sql

        tx_list = ", ".join(f"'{tx_id}'" for tx_id in known_sync_tx_ids)
        filter_clause = f"({self.SYNC_TX_COLUMN} IS NULL OR {self.SYNC_TX_COLUMN} NOT IN ({tx_list}))"

        sql_upper = original_sql.upper()
        if "WHERE" in sql_upper:
            return f"{original_sql} AND {filter_clause}"
        else:
            return f"{original_sql} WHERE {filter_clause}"

    def should_skip_row(
        self,
        row: dict[str, Any],
        last_sync_tx_id: str | None = None,
    ) -> bool:
        """Check if a single row should be skipped during ingestion.

        Row-level filter for streaming ingestion paths where SQL rewriting
        is not possible. Returns True if the row was written back by Gaia
        and should be excluded from ingestion.

        Args:
            row: A row dict from the source system.
            last_sync_tx_id: The last known Gaia sync transaction ID.

        Returns:
            True if the row should be skipped (was written back by Gaia).
        """
        if last_sync_tx_id is None:
            return False

        row_tx = row.get(self.SYNC_TX_COLUMN)
        if row_tx is None:
            return False  # Never written back by Gaia

        return bool(row_tx == last_sync_tx_id)

    def extract_watermark_metadata(self, row: dict[str, Any]) -> dict[str, Any]:
        """Extract feedback-loop-relevant metadata from an ingested row.

        Used to track the latest sync state for subsequent filtering.

        Args:
            row: A row from the source system.

        Returns:
            Dict with sync metadata fields relevant for feedback loop prevention.
        """
        return {
            "has_sync_tx": self.SYNC_TX_COLUMN in row and row[self.SYNC_TX_COLUMN] is not None,
            "sync_tx": row.get(self.SYNC_TX_COLUMN),
            "sync_user": row.get(self.SYNC_USER_COLUMN),
        }
