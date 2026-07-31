"""TrinoQueryEngine — federated query execution.

Wraps the trino-python-client (DBAPI) to provide async query execution.
All queries are dispatched through a Trino connection configured with
the Gravitino catalog for federated access to Iceberg and other sources.

Per architecture: Trino is the primary query engine. All federated queries,
View executions, and time-travel queries go through this layer.
"""

import asyncio
from typing import Any

from trino.dbapi import Connection

from ontology.config.settings import settings
from ontology.core.exceptions import (
    CatalogNotRegisteredError,
    DataSourceUnreachableError,
    OntologyError,
    TrinoUnavailableError,
)

# ── Trino error classification ──────────────────────────────────────────────
# Substrings in a TrinoQueryError message that indicate the *external data
# source* (not Trino itself) is unreachable. Matched case-insensitively.
# Sourced from observed Gravitino Connector + JDBC driver failures:
#   - UnknownHostException (DNS, e.g. stopped container / wrong host)
#   - Communications link failure (MySQL CJ driver: socket couldn't connect)
#   - Cannot create PoolableConnectionFactory (commons-dbcp2 wrapping the above)
#   - Connection refused / Connection timed out (generic JDBC socket errors)
#   - No route to host (network unreachable)
_DATASOURCE_UNREACHABLE_MARKERS = (
    "unknownhostexception",
    "communications link failure",
    "poolableconnectionfactory",
    "connection refused",
    "connection timed out",
    "no route to host",
    "connectexception",
)


# Trino error ``error_name`` values that indicate the *Gravitino catalog*
# backing a data source is missing (not the source DB itself). Trino ran
# the query but the federated catalog wasn't loaded — typically because
# Gravitino was rebuilt and its PG-backed catalog metadata was lost.
# Recoverable by re-registering the catalog (DataSourceService reconcile).
#
# Two variants observed in practice:
#   - CATALOG_NOT_FOUND: Trino's own error when a catalog isn't configured
#     at the Trino level (e.g. Gravitino connector hasn't loaded it).
#   - GRAVITINO_CATALOG_NOT_EXISTS: Gravitino Connector's error when Trino
#     reached the connector but Gravitino reports the catalog is gone
#     (e.g. after a Gravitino rebuild wiped its PG metadata).
#   - SCHEMA_NOT_FOUND: catalog present but schema gone — same root cause.
_CATALOG_MISSING_ERROR_NAMES = frozenset(
    {
        "CATALOG_NOT_FOUND",
        "GRAVITINO_CATALOG_NOT_EXISTS",
        "SCHEMA_NOT_FOUND",  # catalog present but schema gone — same root cause
    }
)


def _classify_trino_error(exc: Exception) -> OntologyError:
    """Map a raw trino-client exception to a domain exception.

    Four buckets:
      1. ``TrinoConnectionError`` — the trino-python-client couldn't reach
         the Trino server at all (socket refused / timeout). The query
         engine service is down → :class:`TrinoUnavailableError` (HTTP 503).
      2. ``TrinoQueryError`` whose message carries a JDBC connection-failure
         marker — Trino is up but a federated catalog couldn't dial its
         backing DB → :class:`DataSourceUnreachableError` (HTTP 502).
      3. ``TrinoQueryError`` whose ``error_name`` is CATALOG_NOT_FOUND /
         GRAVITINO_CATALOG_NOT_EXISTS / SCHEMA_NOT_FOUND — the Gravitino
         catalog registration is gone (Gaia's bookkeeping is stale, e.g.
         after Gravitino rebuild) → :class:`CatalogNotRegisteredError`
         (HTTP 502, code ``CATALOG_NOT_REGISTERED``). Recoverable by
         re-registering.
      4. Anything else (syntax, type, permission, generic internal) — keep
         the legacy :class:`OntologyError` so existing callers behave the
         same.
    """
    # Late import keeps the module importable even if the trino package is
    # stubbed in environments that only touch the type layer.
    from trino.exceptions import TrinoConnectionError, TrinoQueryError

    if isinstance(exc, TrinoConnectionError):
        return TrinoUnavailableError(
            f"Trino server unreachable: {exc}",
            code="TRINO_UNAVAILABLE",
        )

    if isinstance(exc, TrinoQueryError):
        msg = str(exc)
        lowered = msg.lower()
        if any(marker in lowered for marker in _DATASOURCE_UNREACHABLE_MARKERS):
            return DataSourceUnreachableError(msg, code="DATASOURCE_UNREACHABLE")
        # Catalog/schema missing → stale Gravitino registration (not a source
        # connectivity issue). Surfaced so the caller can trigger reconcile.
        error_name = getattr(exc, "error_name", None) or ""
        if error_name in _CATALOG_MISSING_ERROR_NAMES:
            return CatalogNotRegisteredError(
                f"数据源 catalog 未注册（可能因引擎重建丢失）：{exc}",
                code="CATALOG_NOT_REGISTERED",
            )

    return OntologyError(f"Trino query failed: {exc}")


class TrinoQueryEngine:
    """Trino query execution engine.

    Wraps the synchronous Trino DBAPI client in async-compatible calls.
    All results are returned as lists of dicts (row-oriented).

    Args:
        connection: Optional pre-configured Trino Connection. If None,
                    creates one using settings.
    """

    def __init__(self, connection: Connection | None = None) -> None:
        self._connection = connection

    @property
    def connection(self) -> Connection:
        """Lazy-initialized Trino connection."""
        if self._connection is None:
            from trino.dbapi import connect as trino_connect

            self._connection = trino_connect(  # type: ignore[no-untyped-call]
                host=settings.trino_host,
                port=settings.trino_port,
                user=settings.trino_user,
                catalog=settings.trino_catalog,
                schema=settings.trino_schema,
            )
        return self._connection

    async def query(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts.

        Args:
            sql: SQL query string
            params: Optional query parameters (positional)

        Returns:
            List of rows, each row as a dict mapping column name → value

        Raises:
            TrinoUnavailableError: Trino server itself is unreachable.
            DataSourceUnreachableError: Trino is up but an external JDBC
                catalog couldn't connect to its backing data source.
            OntologyError: Any other query failure.
        """

        def _execute() -> list[dict[str, Any]]:
            cursor = self.connection.cursor()
            try:
                if params:
                    cursor.execute(sql, params)
                else:
                    cursor.execute(sql)

                rows = cursor.fetchall()
                if not cursor.description:
                    return []

                columns = [desc[0] for desc in cursor.description]
                return [dict(zip(columns, row)) for row in rows]
            except Exception as exc:
                raise _classify_trino_error(exc) from exc
            finally:
                cursor.close()

        return await asyncio.to_thread(_execute)

    # ═════════════════════════════════════════════════════════════
    # Data Source Exploration Helpers
    # ═════════════════════════════════════════════════════════════

    async def list_tables(self, catalog: str, schema: str = "") -> list[str]:
        """List all tables in a catalog/schema.

        Args:
            catalog: Gravitino catalog name (e.g. "erp_mysql_prod")
            schema: Optional schema/database name filter (use "" for default)

        Returns:
            List of table names
        """
        if schema:
            sql = f'SHOW TABLES FROM "{catalog}"."{schema}"'
            rows = await self.query(sql)
            return [row.get("Table", "") for row in rows if row.get("Table")]

        # No schema specified — list all schemas then all tables
        sql = f'SHOW SCHEMAS FROM "{catalog}"'
        rows = await self.query(sql)
        schemas = [row.get("Schema", "") for row in rows if row.get("Schema")]
        tables: list[str] = []
        for s in schemas[:10]:  # Limit to avoid overwhelming
            try:
                show_tables = f'SHOW TABLES FROM "{catalog}"."{s}"'
                table_rows = await self.query(show_tables)
                for tr in table_rows:
                    if tr.get("Table"):
                        tables.append(f"{s}.{tr['Table']}")
            except OntologyError:
                continue
        return tables

    async def describe_table(self, catalog: str, schema: str, table: str) -> list[dict[str, Any]]:
        """Describe a table's columns.

        Args:
            catalog: Gravitino catalog name
            schema: Schema/database name
            table: Table name

        Returns:
            List of column descriptions [{name, type, nullable, ...}, ...]
        """
        sql = f'DESCRIBE "{catalog}"."{schema}"."{table}"'
        try:
            return await self.query(sql)
        except OntologyError:
            # Try SHOW COLUMNS as fallback for some connectors
            sql = f'SHOW COLUMNS FROM "{catalog}"."{schema}"."{table}"'
            return await self.query(sql)

    async def sample_data(self, catalog: str, schema: str, table: str, limit: int = 10) -> list[dict[str, Any]]:
        """Sample rows from a table.

        Args:
            catalog: Gravitino catalog name
            schema: Schema/database name
            table: Table name
            limit: Number of rows to return

        Returns:
            List of row dicts
        """
        sql = f'SELECT * FROM "{catalog}"."{schema}"."{table}" LIMIT {limit}'
        return await self.query(sql)

    async def sample_data_columns(
        self, catalog: str, schema: str, table: str, columns: list[str], limit: int = 10
    ) -> list[dict[str, Any]]:
        """Sample rows from a table, selecting only the given columns.

        Used as a fallback when ``SELECT *`` fails because the table
        contains a column whose type Gravitino can't resolve (e.g.
        PostgreSQL ``jsonb`` mapped to ``external(jsonb)``). The caller
        is expected to filter such columns out of ``columns``.

        Args:
            catalog: Gravitino catalog name
            schema: Schema/database name
            table: Table name
            columns: Column names to select (already validated / quoted-safe)
            limit: Number of rows to return

        Returns:
            List of row dicts
        """
        # 列名来自后端元数据（非用户输入），但仍用双引号包裹以防保留字。
        col_list = ", ".join(f'"{c}"' for c in columns)
        sql = f'SELECT {col_list} FROM "{catalog}"."{schema}"."{table}" LIMIT {limit}'
        return await self.query(sql)

    async def test_connection(self, catalog: str) -> bool:
        """Test connectivity to a catalog via Trino.

        Args:
            catalog: Gravitino catalog name

        Returns:
            True if connection succeeds, False otherwise
        """
        try:
            await self.query(f'SELECT 1 FROM "{catalog}"."information_schema"."tables" LIMIT 1')
            return True
        except Exception:
            return False
