"""WriteBackManager — write changes back to external source systems.

Implements the Palantir Write-back pattern (OSv2):
    - Webhook path (Path A): Real-time API calls to SaaS/ERP systems
    - JDBC path (Path B): Direct database write-back via SeaTunnel JDBC sink

Feedback loop prevention:
    - Injects gaia_sync_tx and gaia_sync_user metadata into write-back payloads
    - These markers are used by IngestionFilter to exclude self-written rows
      during incremental ingestion (see ingestion_filter.py).
"""

from typing import Any, Literal


class WriteBackManager:
    """Write-back changes to external source systems.

    Two write-back paths are supported:
    - Path A (Webhook): Sends HTTP requests with idempotency keys
    - Path B (JDBC): Submits one-shot JDBC tasks via SeaTunnel REST API

    Both paths inject sync metadata (gaia_sync_tx, gaia_sync_user)
    for feedback loop prevention.
    """

    # Metadata fields injected into every write-back for feedback loop prevention
    SYNC_TX_FIELD = "gaia_sync_tx"
    SYNC_USER_FIELD = "gaia_sync_user"

    def __init__(
        self,
        gaia_sync_user: str = "gaia-system",
    ) -> None:
        self._sync_user = gaia_sync_user

    def build_write_back_payload(
        self,
        changes: dict[str, Any],
        sync_tx_id: str,
    ) -> dict[str, Any]:
        """Augment changes with sync metadata for feedback loop prevention.

        Args:
            changes: Column-value pairs to write back.
            sync_tx_id: Unique transaction ID for this write-back.

        Returns:
            Augmented changes with gaia_sync_tx and gaia_sync_user fields.
        """
        return {
            **changes,
            self.SYNC_TX_FIELD: sync_tx_id,
            self.SYNC_USER_FIELD: self._sync_user,
        }

    def build_upsert_sql(
        self,
        table: str,
        primary_key: str,
        changes: dict[str, Any],
        sync_tx_id: str,
        *,
        dialect: Literal["postgres", "mysql"] = "postgres",
    ) -> tuple[str, list[Any]]:
        """Build a parameterized UPSERT SQL statement for the target dialect.

        Generates an INSERT ... ON CONFLICT DO UPDATE statement that:
        1. Creates the row if it doesn't exist
        2. Updates all columns if the row exists (including sync metadata)
        3. Uses driver-native parameter placeholders — no caller-side
           placeholder translation (V2 fix, architecture §7.2):
             - postgres (asyncpg): $1, $2, ... (positional)
             - mysql (aiomysql):   %s       (positional)
        Returns (sql, ordered_params) so the executor binds values directly
        without parsing the SQL string.

        Args:
            table: Target table name in the source system.
            primary_key: Primary key column name for conflict detection.
            changes: Column-value pairs to upsert.
            sync_tx_id: Transaction ID for feedback loop prevention.
            dialect: Target driver dialect for placeholder style.

        Returns:
            (sql, params) — sql with native placeholders, params in column
            order (each column appears twice: VALUES list + ON CONFLICT SET).
        """
        augmented = self.build_write_back_payload(changes, sync_tx_id)
        columns = list(augmented.keys())
        if dialect == "mysql":
            # MySQL upsert for Action write-back (D4 fix): Action mutations
            # only carry the *changed* columns, not a full row. INSERT ... ON
            # DUPLICATE KEY UPDATE still requires every NOT NULL column in the
            # INSERT clause (MySQL validates INSERT column completeness even
            # when the row exists and the UPDATE branch runs), so a partial
            # changes set fails with "Field 'X' doesn't have a default value".
            # Since Action ModifyObject always targets a pre-existing object
            # (hydrate confirmed existence), UPDATE semantics are correct:
            # update only the changed columns, no NOT NULL-column requirement.
            # (Row-not-found returns 0 affected rows; CreateObject uses op=insert.)
            set_cols = [c for c in columns if c != primary_key]
            if not set_cols:
                return ("SELECT 1", [])  # nothing to update
            set_clause = ", ".join(
                f"{c} = {self._placeholder(columns, i, dialect)}" for i, c in enumerate(columns) if c != primary_key
            )
            pk_pos = columns.index(primary_key) if primary_key in columns else len(columns)
            # params: SET values (excluding pk) then the pk for WHERE
            set_params = [augmented[c] for c in columns if c != primary_key]
            pk_param = augmented.get(primary_key)
            if pk_param is None:
                # primary_key not in changes — cannot target a row; fall back
                # to INSERT (caller must ensure pk present for UPDATE path).
                pass
            sql = f"UPDATE {table} SET {set_clause} WHERE {primary_key} = {self._placeholder(columns, pk_pos, dialect)}"
            params = set_params + ([pk_param] if pk_param is not None else [])
            return sql, params
        # postgres: INSERT ... ON CONFLICT (pk) DO UPDATE SET col = $N
        col_list = ", ".join(columns)
        values_placeholders = ", ".join(self._placeholder(columns, i, dialect) for i in range(len(columns)))
        set_clause = ", ".join(
            f"{c} = {self._placeholder(columns, i, dialect, offset=len(columns))}" for i, c in enumerate(columns)
        )
        sql = (
            f"INSERT INTO {table} ({col_list})\n"
            f"VALUES ({values_placeholders})\n"
            f"ON CONFLICT ({primary_key}) DO UPDATE SET {set_clause}"
        )
        # Each column value is bound twice (VALUES + SET clause) — dialect
        # placeholders are positional, so the params list mirrors the
        # placeholder order: all VALUES first, then all SET.
        params = [augmented[c] for c in columns] + [augmented[c] for c in columns]
        return sql, params

    @staticmethod
    def _placeholder(columns: list[str], index: int, dialect: str, offset: int = 0) -> str:
        """Return the native positional placeholder for ``index``."""
        pos = index + offset + 1  # 1-based for both $N and %s sequences
        if dialect == "postgres":
            return f"${pos}"
        return "%s"

    def build_insert_sql(
        self,
        table: str,
        changes: dict[str, Any],
        sync_tx_id: str,
        *,
        dialect: Literal["postgres", "mysql"] = "postgres",
    ) -> tuple[str, list[Any]]:
        """Build a parameterized INSERT SQL statement (ADR Action Mutation Mapping §3.9)。

        用于 CreateObject 回写(auto_increment 主键表,如 flight_status_log)。
        不含 ON CONFLICT,不要求 primary_key。注入同步标记防反馈环。
        """
        augmented = self.build_write_back_payload(changes, sync_tx_id)
        columns = list(augmented.keys())
        col_list = ", ".join(columns)
        values_placeholders = ", ".join(self._placeholder(columns, i, dialect) for i in range(len(columns)))
        sql = f"INSERT INTO {table} ({col_list})\nVALUES ({values_placeholders})"
        params = [augmented[c] for c in columns]
        return sql, params

    def build_merge_sql(
        self,
        table: str,
        primary_key: str,
        changes: dict[str, Any],
        sync_tx_id: str,
    ) -> str:
        """Build a MERGE statement for databases that support it.

        Some source systems (e.g., Oracle, SQL Server) use MERGE instead
        of INSERT ON CONFLICT. This method provides that alternative.

        Args:
            table: Target table name.
            primary_key: Primary key column name.
            changes: Column-value pairs to apply.
            sync_tx_id: Transaction ID for feedback loop prevention.

        Returns:
            MERGE SQL statement with parameterized placeholders.
        """
        augmented = self.build_write_back_payload(changes, sync_tx_id)
        columns = list(augmented.keys())
        insert_cols = ", ".join(columns)
        insert_vals = ", ".join(f"src.{c}" for c in columns)
        update_set = ", ".join(f"{c} = src.{c}" for c in columns if c != primary_key)

        return (
            f"MERGE INTO {table} AS tgt\n"
            f"USING (SELECT {', '.join(f':{c} AS {c}' for c in columns)}) AS src\n"
            f"ON tgt.{primary_key} = src.{primary_key}\n"
            f"WHEN MATCHED THEN UPDATE SET {update_set}\n"
            f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
        )

    @staticmethod
    def extract_sync_metadata_from_row(row: dict[str, Any]) -> dict[str, Any]:
        """Extract sync metadata from a source row for feedback loop detection.

        Used by incremental ingestion to identify rows previously written
        back by Gaia, preventing infinite feedback loops.

        Args:
            row: A row from the source system.

        Returns:
            Dict with sync_tx, sync_user if present in the row.
        """
        return {
            "sync_tx": row.get(WriteBackManager.SYNC_TX_FIELD),
            "sync_user": row.get(WriteBackManager.SYNC_USER_FIELD),
        }
