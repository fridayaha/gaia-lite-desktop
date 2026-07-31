"""DorisIndexStore — online read source over Apache Doris (MySQL protocol).

Architecture (post ADR-001 revision, 2026-06-25):
  Doris is the **online read primary source**, storing full structured
  attributes for each ObjectType (single table per OT: ``idx_{ont}__{type}``).
  Point lookups and filter queries return full rows directly from Doris;
  Iceberg/Trino fall back to history/time-travel/batch-analysis/disaster-
  recovery only. POC measured Doris full point-lookup ~300x faster than
  Trino-Iceberg (qps 1.8 → 552, p95 1900ms → 65ms at concurrency 10).

  - Connection management: a module-level aiomysql Pool (lazy-init) is shared
    across all requests. Per-request new connections capped throughput at
    qps 35 in the POC; a persistent pool reached qps 552. The pool is closed
    on application shutdown via ``close()``.
  - Table model: Doris Unique Key (merge-on-read) so plain INSERT has upsert
    semantics — IndexSync backfill is idempotent without ON DUPLICATE KEY.
  - Column types: PRIMARY_KEY columns use the source datatype mapping
    (LONG→BIGINT, INTEGER→INT) per Doris guidance for numeric PKs (preserves
    ORDER BY / range semantics); INVERTED/RANGE columns stay VARCHAR(255)
    (type-tolerant hot mirror); STORED_ONLY full-detail columns use the same
    datatype-aware mapping. All non-indexed attributes land as STORED_ONLY.
  - Doris unavailable → caller (ObjectQueryService) falls back to Trino scan.
"""

from __future__ import annotations

import logging
from typing import Any

from ontology.config.settings import settings
from ontology.core import naming
from ontology.core.exceptions import DorisUnavailableError, OntologyError

# NOTE (2026-07-13): legacy filter-DSL methods query()/load_by_filter()/aggregate()
# and their IndexFilter/IndexQuery/IndexResult schemas were removed — they had
# no production callers and used non-parameterized SQL assembly. Doris reads
# now go solely through execute_sql (parameterized) and load_by_ids.

_log = logging.getLogger(__name__)

# ── Doris connection pool (module-level singleton) ──
# A single pool is shared by all DorisIndexStore instances. Created lazily on
# first use; closed via close() at application shutdown (main.py lifespan).
_pool: Any = None  # aiomysql.Pool | None


async def _get_pool() -> Any:
    """Lazy-init the shared aiomysql Pool.

    Ensures the ``ontology`` database exists before binding the pool to it —
    Doris ships with only system schemas (``__internal_schema``/``mysql``),
    and the index tables live in a dedicated ``ontology`` database that must
    be created up-front. Creating it here (idempotent) keeps ``provision``
    self-contained instead of relying on an external init step.
    """
    global _pool
    if _pool is None:
        import aiomysql

        # Ensure the ontology database exists. Doris's default catalogs don't
        # include it, and create_index_table assumes it does. A no-op connect
        # (no db) + CREATE DATABASE IF NOT EXISTS makes provision self-bootstrapping.
        try:
            async with aiomysql.connect(
                host=settings.doris_host,
                port=settings.doris_port,
                user=settings.doris_user,
                password=settings.doris_password,
                autocommit=True,
            ) as boot_conn:
                async with boot_conn.cursor() as cur:
                    await cur.execute("CREATE DATABASE IF NOT EXISTS ontology")
        except Exception as exc:
            # Best-effort: if we can't pre-create the database (e.g. Doris not
            # yet reachable on cold start), the pool will still be created and
            # the first query will surface a clear error. Don't hard-fail here.
            _log.warning("Could not pre-create Doris 'ontology' database: %s", exc)

        _pool = await aiomysql.create_pool(
            host=settings.doris_host,
            port=settings.doris_port,
            user=settings.doris_user,
            password=settings.doris_password,
            db="ontology",
            autocommit=True,
            minsize=5,
            maxsize=50,
            pool_recycle=3600,
        )
        _log.info("Doris connection pool created (minsize=5 maxsize=50)")
    return _pool


async def close_pool() -> None:
    """Close the shared Doris pool. Called from main.py lifespan shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        _log.info("Doris connection pool closed")


# ── Datatype → Doris column type mapping (for STORED_ONLY full-detail cols) ──
# PRIMARY_KEY columns use the source datatype mapping (numeric PKs keep
# numeric semantics); INVERTED/RANGE stay VARCHAR(255) (text/range hot
# mirror); STORED_ONLY columns preserve the source datatype so range/order
# semantics work for non-indexed queries too.
_DORIS_TYPE_MAP: dict[str, str] = {
    "STRING": "VARCHAR(255)",
    "INTEGER": "INT",
    "SHORT": "SMALLINT",
    "LONG": "BIGINT",
    "BOOLEAN": "BOOLEAN",
    "BYTE": "TINYINT",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "DECIMAL": "DECIMAL(18,4)",
    "DATE": "DATE",
    "TIMESTAMP": "DATETIME",
    "ARRAY": "STRING",  # serialized JSON; Doris ARRAY support varies, keep simple
    "STRUCT": "STRING",  # serialized JSON
    "VECTOR": "ARRAY<FLOAT>",
    "GEOPOINT": "STRING",
    "GEOSHAPE": "STRING",
    "MEDIA_REFERENCE": "STRING",  # reference URL/id only, not the binary
    "ATTACHMENT": "STRING",  # reference URL/id only, not the binary
}


class DorisIndexStore:
    """Online read source over Apache Doris (MySQL protocol).

    Args:
        connection: Optional pre-configured aiomysql Connection or Pool. Used
                    by tests to inject a mock; production code leaves it None
                    and the shared module-level pool is used.
    """

    def __init__(self, connection: Any | None = None) -> None:
        # Tests inject a mock connection/pool directly. Production leaves this
        # None and _acquire() uses the shared module-level pool.
        self._connection = connection

    async def _acquire(self) -> Any:
        """Get a connection (from injected mock or the shared pool)."""
        if self._connection is not None:
            # Test mock: return it directly. Pools expose .acquire() as a
            # context manager; a bare connection does not — normalize by
            # returning the connection itself (tests mock cursor() on it).
            return self._connection
        pool = await _get_pool()
        return await pool.acquire()

    async def _release(self, conn: Any) -> None:
        """Release a connection back to the pool (no-op for injected mocks)."""
        if self._connection is not None:
            return
        pool = await _get_pool()
        pool.release(conn)

    async def _cursor(self, conn: Any) -> Any:
        """Get a cursor from a connection."""
        return await conn.cursor()

    def _table_name(self, ontology_api_name: str, object_type_api_name: str) -> str:
        """Generate the namespaced Doris table name.

        Uses ``naming.doris_index_table`` so two ontologies that both define
        an ObjectType named ``asset`` get separate tables
        (``idx_ont1__asset`` vs ``idx_ont2__asset``). The naming module
        validates both identifiers.
        """
        return naming.doris_index_table(ontology_api_name, object_type_api_name)

    # ── DDL ──

    async def create_index_table(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        fields: list[dict[str, Any]],
        partition_by: list[str] | None = None,
    ) -> None:
        """Create a Doris object-store table for an ObjectType.

        The table holds **full structured attributes** (online read source).
        Indexed columns (PRIMARY_KEY/INVERTED/RANGE/VECTOR) get the appropriate
        Doris index; STORED_ONLY columns are stored without an index.

        Args:
            ontology_api_name: Owning ontology (drives the table-name prefix).
            object_type_api_name: Object type identifier.
            fields: List of field definitions with ``name``, ``index_type``
                    (PRIMARY_KEY/INVERTED/RANGE/VECTOR/STORED_ONLY), and
                    optional ``data_type`` (for STORED_ONLY type mapping).
            partition_by: Optional partition column(s)

        Raises:
            DorisUnavailableError: If the operation fails.
        """
        table = self._table_name(ontology_api_name, object_type_api_name)
        col_defs: list[str] = []
        index_defs: list[str] = []
        pk_cols: list[str] = []

        for f in fields:
            index_type = f["index_type"]
            # Backtick column names — Doris reserves words like `role`,
            # `status`, `user` that collide with common property names.
            col_name = f"`{f['name']}`"

            if index_type == "PRIMARY_KEY":
                pk_cols.append(col_name)
                # PK columns use the source datatype mapping (LONG→BIGINT,
                # INTEGER→INT, STRING→VARCHAR) so ORDER BY / range queries on
                # numeric PKs keep numeric semantics (Doris official guidance:
                # pure-numeric single-source PKs should use INT/BIGINT, not
                # VARCHAR — VARCHAR forces string dictionary order, breaks
                # Zone Map range filtering, and bloats storage). Gaia's PKs
                # are single-source numeric (MySQL BIGINT → Iceberg), so the
                # heterogeneous-PK VARCHAR fallback does not apply here.
                data_type = str(f.get("data_type", "STRING")).upper()
                doris_type = _DORIS_TYPE_MAP.get(data_type, "VARCHAR(255)")
                col_defs.append(f"    {col_name} {doris_type}")
            elif index_type == "INVERTED":
                col_defs.append(f"    {col_name} VARCHAR(255)")
                index_defs.append(f"    INDEX idx_{f['name']} ({col_name}) USING INVERTED")
            elif index_type == "RANGE":
                # RANGE columns stay VARCHAR(255): RANGE covers DATE/TIMESTAMP
                # whose values ("2026-04-19") can't cast to DECIMAL, and numeric
                # ranges are served by the column's own minmax anyway.
                col_defs.append(f"    {col_name} VARCHAR(255)")
            elif index_type == "VECTOR":
                col_defs.append(f"    `{f['name']}_embedding` ARRAY<FLOAT>")
                index_defs.append(f"    INDEX idx_{f['name']}_vector (`{f['name']}_embedding`) USING VECTOR")
            elif index_type == "STORED_ONLY":
                # Full-detail column: map source datatype to a Doris column type
                # so non-indexed queries keep proper semantics.
                data_type = str(f.get("data_type", "STRING")).upper()
                doris_type = _DORIS_TYPE_MAP.get(data_type, "VARCHAR(255)")
                col_defs.append(f"    {col_name} {doris_type}")
            else:
                # Unknown index_type — store as STRING to be safe.
                col_defs.append(f"    {col_name} VARCHAR(255)")

        # System column: ``rid`` (Palantir Resource Identifier, see core/rid.py
        # + handoff-rid-funnel-closure.md §T1.1). The rid is the cross-engine
        # identity key (Neo4j node id / PostGIS row anchor / TimescaleDB tag)
        # and Doris idx is its authoritative source (T1.4/T1.5 fill it). It is
        # injected as a system column — NOT part of IndexFieldExtractor output
        # (rid is not an ObjectType property). It carries an INVERTED index for
        # hydrate_by_rids point lookups but is NOT part of UNIQUE KEY: rid
        # uniqueness is guaranteed by the business PK UNIQUE KEY + the rid
        # allocation contract (reuse-or-generate), so adding a redundant UNIQUE
        # constraint would only bloat storage. Empty for存量 rows until T1.5
        # backfill (rid IS NULL). See graph-reasoning-design.md §4.4.
        col_defs.append("    `rid` VARCHAR(128)")
        index_defs.append("    INDEX idx_rid (`rid`) USING INVERTED")

        # UNIQUE KEY (not DUPLICATE KEY) so that plain INSERT INTO ... VALUES
        # has upsert semantics: a new row with an existing PK overwrites the
        # old one (Doris Unique model, merge-on-read). This is what makes
        # upsert idempotent without MySQL-specific ON DUPLICATE KEY UPDATE
        # syntax (which Doris does not support).
        pk = f"UNIQUE KEY ({', '.join(pk_cols)})" if pk_cols else ""
        partition = ""
        if partition_by:
            partition = f"PARTITION BY RANGE ({', '.join(partition_by)}) ()"
        # Doris 4.x requires Unique/Duplicate tables to declare an explicit
        # distribution (bucketing). Hash on the PK columns; 1 bucket is enough
        # for an index table (small, filtered by ID, not range-scanned).
        distribution = (
            f"DISTRIBUTED BY HASH ({', '.join(pk_cols)}) BUCKETS 1" if pk_cols else "DISTRIBUTED BY RANDOM BUCKETS 1"
        )

        sql = f"CREATE TABLE IF NOT EXISTS {table} (\n" + ",\n".join(col_defs)
        if index_defs:
            sql += ",\n" + ",\n".join(index_defs)
        sql += "\n)\n"
        if pk:
            sql += pk + "\n"
        if partition:
            sql += partition + "\n"
        sql += distribution + "\n"
        # replication_num: single-BE dev clusters (the default docker-compose
        # setup) cannot satisfy Doris's default replication_num=3. Read from
        # settings so production clusters can override to 3+ without code change.
        sql += f'PROPERTIES (\n  "replication_num"="{settings.doris_replication_num}"\n)'

        await self._execute(sql)

    async def drop_index_table(self, ontology_api_name: str, object_type_api_name: str) -> None:
        """Drop an object-store table."""
        table = self._table_name(ontology_api_name, object_type_api_name)
        await self._execute(f"DROP TABLE IF EXISTS {table}")

    async def table_exists(self, ontology_api_name: str, object_type_api_name: str) -> bool:
        """Check whether the object-store table for an ObjectType exists.

        Used by ObjectQueryService to distinguish "table not provisioned"
        (normal Trino fallback) from "Doris down" (fault fallback). A missing
        table is not an error condition — it simply means provisioning has
        not run or was deferred.

        Raises:
            DorisUnavailableError: if Doris itself is unreachable (distinct
                from a missing table — the caller treats this as a fault).
        """
        table = self._table_name(ontology_api_name, object_type_api_name)
        # INFORMATION_SCHEMA is portable across Doris/MySQL. COUNT(*) returns 1
        # when the table exists in the ontology schema, 0 otherwise.
        sql = (
            "SELECT COUNT(*) FROM information_schema.tables "
            f"WHERE table_schema = 'ontology' AND table_name = '{_escape(table)}'"
        )
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql)
                    row = await cursor.fetchone()
                    return bool(row and row[0])
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except Exception as exc:
            raise DorisUnavailableError(f"Doris unavailable: {exc}") from exc

    # ── DML ──

    async def upsert(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert or update records (full structured attributes) in the table.

        Uses Doris Unique-model INSERT (idempotent on PK — new row overwrites old).

        Args:
            ontology_api_name: Owning ontology (drives the table-name prefix).
            object_type_api_name: Object type identifier
            records: List of row dicts (full attribute set)
        """
        if not records:
            return

        table = self._table_name(ontology_api_name, object_type_api_name)
        columns = list(records[0].keys())
        for col in columns:
            _validate_identifier(col)

        # Build bulk INSERT. Doris Unique-model tables make plain INSERT
        # idempotent on the PK (new row overwrites old), so no ON DUPLICATE
        # KEY UPDATE clause (which Doris does not support anyway).
        rows_sql: list[str] = []
        for record in records:
            values = ", ".join([_escape_val(v) for v in record.values()])
            rows_sql.append(f"({values})")

        # Backtick column names to avoid Doris reserved-word collisions
        # (role/status/user are common property names).
        cols_quoted = ", ".join(f"`{c}`" for c in columns)
        # Batch the INSERT — a single multi-row INSERT for tens of thousands
        # of rows exhausts Doris FE memory (MEM_ALLOC_FAILED) / drops the
        # connection. 1000 rows/batch stays well under the limit.
        batch_size = 1000
        for i in range(0, len(rows_sql), batch_size):
            chunk = rows_sql[i : i + batch_size]
            sql = f"INSERT INTO {table} ({cols_quoted}) VALUES\n" + ",\n".join(chunk)
            await self._execute(sql)

    async def delete_by_ids(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        ids: list[str],
        pk_column: str,
    ) -> None:
        """Delete records by primary key.

        Args:
            ontology_api_name: Owning ontology (drives the table-name prefix).
            object_type_api_name: Object type identifier
            ids: Primary key values to delete
            pk_column: The physical PK column name in the Doris table
                (from the property's backing_mapping.backing_column).
        """
        if not ids:
            return

        table = self._table_name(ontology_api_name, object_type_api_name)
        _validate_identifier(pk_column)
        # Parameterize values to avoid SQL injection via id contents.
        placeholders = ", ".join(["%s" for _ in ids])
        sql = f"DELETE FROM {table} WHERE {pk_column} IN ({placeholders})"
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql, ids)
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except Exception as exc:
            raise DorisUnavailableError(f"Doris unavailable: {exc}") from exc

    async def upsert_embedding(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        pk_column: str,
        pk_value: str,
        embedding_column: str,
        embedding: list[float],
    ) -> None:
        """Update a single object's embedding column (§14.4 语义检索).

        Uses Doris Unique-model ``UPDATE ... SET col = [...] WHERE pk = ?``
        (partial column update, docs/4.x/data-operate/update/unique-update).
        Only the embedding column is touched — other columns retain their
        values (no read-modify-write of the full row).

        Called by OutboxExecutor EMBEDDING effect after OnnxEmbeddingProvider
        computes the vector from the object's source_expression properties.

        Args:
            ontology_api_name: Owning ontology (table-name prefix).
            object_type_api_name: Object type identifier.
            pk_column: Business PK column name (backing_column of primary_key).
            pk_value: Business PK value locating the row.
            embedding_column: The ``{prop}_embedding`` ARRAY<FLOAT> column.
            embedding: L2-normalized float vector (from EmbeddingProvider).
        """
        table = self._table_name(ontology_api_name, object_type_api_name)
        _validate_identifier(pk_column)
        _validate_identifier(embedding_column)
        # ARRAY<FLOAT> literal: [1.0, 2.0, ...]. embedding values are
        # L2-normalized model output (not user input) → safe to inline.
        arr_literal = "[" + ", ".join(repr(float(x)) for x in embedding) + "]"
        sql = f"UPDATE {table} SET `{embedding_column}` = ARRAY<float>{arr_literal} WHERE `{pk_column}` = %s"
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql, [str(pk_value)])
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris upsert_embedding failed: {exc}") from exc

    async def load_by_ids(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        rids: list[str],
        columns: list[str],
        pk_column: str,
    ) -> list[dict[str, Any]]:
        """Load full attributes for specific object RIDs (point lookup).

        The primary online read path for point queries. Returns full rows
        directly from Doris; the caller falls back to Trino-Iceberg only if
        this raises DorisUnavailableError.

        Args:
            ontology_api_name: Owning ontology.
            object_type_api_name: Object type identifier.
            rids: Primary key values (or RIDs, post-migration) to look up.
            columns: Physical column names to select (validated identifiers).
            pk_column: Physical PK column name.

        Returns:
            List of row dicts (column name → value). Empty if no matches.

        Raises:
            DorisUnavailableError: if Doris is unreachable (triggers Trino fallback).
        """
        if not rids or not columns:
            return []
        table = self._table_name(ontology_api_name, object_type_api_name)
        _validate_identifier(pk_column)
        for c in columns:
            _validate_identifier(c)
        cols = ", ".join(f"`{c}`" for c in columns)
        placeholders = ", ".join(["%s" for _ in rids])
        sql = f"SELECT {cols} FROM {table} WHERE {pk_column} IN ({placeholders})"
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql, rids)
                    rows = await cursor.fetchall()
                    col_names = [d[0] for d in cursor.description] if cursor.description else columns
                    return [dict(zip(col_names, row)) for row in rows]
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except Exception as exc:
            raise DorisUnavailableError(f"Doris load_by_ids failed: {exc}") from exc

    async def get_rid_by_pk(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        pk_column: str,
        pk_value: Any,
    ) -> str | None:
        """Look up the rid for a single business-PK value (reuse-or-generate).

        Used by ObjectIndexFunnel (external-ingestion path) to **reuse** an
        existing rid on re-sync instead of allocating a new one — keeping
        rid identity stable across re-imports. Returns ``None`` when the row
        is absent (caller allocates a fresh rid via ``generate_object_rid``).

        Doris idx is the rid authoritative source (graph-reasoning-design.md
        §4.4); the rid column is populated by the Action outbox path (T1.3)
        and the external-ingestion path (T1.4), and backfilled for存量 rows
        by T1.5.

        Args:
            ontology_api_name: Owning ontology (table-name prefix).
            object_type_api_name: Target ObjectType.
            pk_column: Physical PK column name (backing_column of primary_key).
            pk_value: Business primary key value.

        Returns:
            The rid string, or ``None`` if no row matches this PK.

        Raises:
            DorisUnavailableError: if Doris is unreachable. Callers that need
                fault-tolerance (ingestion) treat this as "allocate fresh rid"
                rather than failing the import.
        """
        table = self._table_name(ontology_api_name, object_type_api_name)
        _validate_identifier(pk_column)
        sql = f"SELECT `rid` FROM {table} WHERE `{pk_column}` = %s LIMIT 1"
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql, [pk_value])
                    row = await cursor.fetchone()
                    if row is None:
                        return None
                    rid = row[0]
                    # rid may be empty string for存量 rows pre-backfill (T1.5
                    # fills them). Treat empty as "not yet assigned" so the
                    # caller allocates a fresh rid instead of propagating "".
                    return str(rid) if rid else None
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris get_rid_by_pk failed: {exc}") from exc

    async def get_rids_by_pks(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        pk_column: str,
        pk_values: list[Any],
    ) -> dict[str, str | None]:
        """Batch version of :meth:`get_rid_by_pk` — reuse-or-generate for imports.

        Returns a ``{pk_value: rid_or_None}`` map. PKs absent from Doris map
        to ``None`` (caller allocates). The map is keyed by the **string**
        form of each pk_value so callers can look up by the original value
        via ``str(pk_value)``.

        Used by ObjectIndexFunnel to resolve rids for a whole Iceberg batch
        in one round-trip (avoiding N single queries).

        Args:
            ontology_api_name: Owning ontology (table-name prefix).
            object_type_api_name: Target ObjectType.
            pk_column: Physical PK column name (backing_column of primary_key).
            pk_values: Business primary key values to resolve.

        Returns:
            Dict mapping ``str(pk_value)`` → rid (or ``None`` if the row is
            absent or its rid column is empty/unbackfilled).

        Raises:
            DorisUnavailableError: if Doris is unreachable.
        """
        if not pk_values:
            return {}
        table = self._table_name(ontology_api_name, object_type_api_name)
        _validate_identifier(pk_column)
        placeholders = ", ".join(["%s" for _ in pk_values])
        sql = f"SELECT `{pk_column}`, `rid` FROM {table} WHERE `{pk_column}` IN ({placeholders})"
        result: dict[str, str | None] = {str(v): None for v in pk_values}
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql, list(pk_values))
                    rows = await cursor.fetchall()
                    for pk_val, rid in rows:
                        result[str(pk_val)] = str(rid) if rid else None
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris get_rids_by_pks failed: {exc}") from exc
        return result

    async def execute_sql(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        physical_sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a compiler-produced physical SQL string with params.

        Used by ObjectQueryService.execute_compiled_sql (text2sql path B,
        ADR-012 Step 4). The SQL is already dialect-correct and
        parameterized (literals → ``?`` placeholders); this method just
        runs it against the Doris index table and returns rows.

        Args:
            ontology_api_name: Owning ontology (for logging/metrics).
            object_type_api_name: Primary object type (logging/metrics).
            physical_sql: Physical Doris SQL with ``?`` placeholders.
            params: Positional parameter values for the placeholders.

        Returns:
            List of row dicts (physical column name → value).

        Raises:
            DorisUnavailableError: if Doris is unreachable (caller falls
                back to Trino recompile).
        """
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    # aiomysql (Doris) uses %s placeholders, not ?. The
                    # compiler emits ? (standard); convert here so callers
                    # stay dialect-agnostic at the IR level.
                    mysql_sql = physical_sql.replace("?", "%s")
                    await cursor.execute(mysql_sql, params or [])
                    rows = await cursor.fetchall()
                    col_names = [d[0] for d in cursor.description] if cursor.description else []
                    return [dict(zip(col_names, row)) for row in rows]
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris execute_sql failed: {exc}") from exc

    # ── TextQL semantic-recall table (ADR-012 §Step 2 引擎B) ──────────────

    _SEMANTIC_TABLE = "idx_ontology_semantic"

    async def semantic_table_exists(self) -> bool:
        """Check if the global ontology-semantic vector table exists."""
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(f"SHOW TABLES LIKE '{self._SEMANTIC_TABLE}'")
                    rows = await cursor.fetchall()
                    return len(rows) > 0
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris semantic_table_exists failed: {exc}") from exc

    async def create_semantic_table(self, dim: int = 384) -> None:
        """Create the global ontology-semantic vector table if absent.

        Schema (ADR-012 §2.3): one row per ontology element (ObjectType /
        Property / LinkType), with a 384-dim embedding column indexed by
        Doris ANN (HNSW + inner_product, since vectors are L2-normalized →
        inner_product == cosine).
        """
        sql = (
            f"CREATE TABLE IF NOT EXISTS {self._SEMANTIC_TABLE} (\n"
            "  ontology_api_name VARCHAR(255) NOT NULL,\n"
            "  element_type VARCHAR(20) NOT NULL,\n"
            "  element_api_name VARCHAR(255) NOT NULL,\n"
            "  display_name VARCHAR(255) NOT NULL,\n"
            "  description VARCHAR(65533),\n"
            f"  embedding ARRAY<FLOAT> NOT NULL COMMENT 'dim={dim}'\n"
            ")\n"
            "DUPLICATE KEY(ontology_api_name, element_type, element_api_name)\n"
            "DISTRIBUTED BY HASH(ontology_api_name) BUCKETS 3\n"
            f'PROPERTIES (\n  "replication_num"="{settings.doris_replication_num}"\n)'
        )
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql)
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
            _log.info("Created semantic table %s", self._SEMANTIC_TABLE)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris create_semantic_table failed: {exc}") from exc

    async def build_semantic_index(self, dim: int = 384) -> None:
        """Build the ANN index on the semantic table via ALTER ADD INDEX.

        Doris 4.x ANN index builds pre-allocate ~2GB for memtable load when
        the index is declared inline in CREATE TABLE — exceeding the default
        1GB container. Building the index post-insert via ALTER ADD INDEX
        uses a different (lower-memory) code path. Call this AFTER the first
        batch of rows is upserted; idempotent (re-running is a no-op once
        the index exists). Uses IVF (lower memory than HNSW) + inner_product
        (cosine on L2-normalized vectors).
        """
        sql = (
            f"ALTER TABLE {self._SEMANTIC_TABLE} ADD INDEX IF NOT EXISTS "
            "idx_semantic_vec (embedding) USING ANN PROPERTIES("
            "'index_type'='ivf',"
            "'metric_type'='inner_product',"
            f"'dim'='{dim}',"
            "'nlist'='128',"
            "'quantizer'='flat'"
            ")"
        )
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql)
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
            _log.info("Built ANN index on %s", self._SEMANTIC_TABLE)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            # Idempotent: if index already exists the ALTER is a no-op;
            # surface other errors but don't block the pipeline.
            _log.warning("build_semantic_index (may be idempotent no-op): %s", exc)

    async def upsert_semantic_rows(self, rows: list[dict[str, Any]]) -> None:
        """Upsert ontology-element rows into the semantic table.

        Each row: {ontology_api_name, element_type, element_api_name,
        display_name, description, embedding (list[float])}.

        The semantic table is DUPLICATE KEY (Doris ANN requires DUP_KEYS),
        so upsert = delete-then-insert per (ontology, element_type,
        element_api_name) key. Idempotent: re-indexing overwrites cleanly.
        """
        if not rows:
            return
        import json

        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    for r in rows:
                        # Delete existing row(s) for this key (DUP table).
                        await cursor.execute(
                            f"DELETE FROM {self._SEMANTIC_TABLE} "
                            "WHERE ontology_api_name = %s "
                            "AND element_type = %s "
                            "AND element_api_name = %s",
                            [r["ontology_api_name"], r["element_type"], r["element_api_name"]],
                        )
                        # Insert new row.
                        emb = json.dumps(list(r["embedding"]))
                        await cursor.execute(
                            f"INSERT INTO {self._SEMANTIC_TABLE} "
                            "(ontology_api_name, element_type, element_api_name, "
                            "display_name, description, embedding) VALUES "
                            f"(%s, %s, %s, %s, %s, %s)",
                            [
                                r["ontology_api_name"],
                                r["element_type"],
                                r["element_api_name"],
                                r["display_name"],
                                r.get("description", ""),
                                emb,
                            ],
                        )
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris upsert_semantic_rows failed: {exc}") from exc

    async def vector_search(
        self,
        query_embedding: list[float],
        ontology_api_name: str,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """ANN vector search: top-K ontology elements by cosine similarity.

        Uses inner_product_approximate (vectors are L2-normalized → inner
        product == cosine). Returns rows with element_type, element_api_name,
        display_name, description, similarity.

        The query embedding is inlined as an ARRAY literal (not parameterized)
        because Doris's MySQL-protocol binding doesn't support ARRAY<FLOAT>
        params — it would pass the JSON string as VARCHAR. The embedding is
        L2-normalized numeric output from EmbeddingProvider (not user input),
        so inlining is injection-safe.
        """
        # Build a Doris ARRAY literal: [1.0, 2.0, ...] (all floats,
        # no user input — embedding is L2-normalized model output).
        emb_items = ",".join(repr(float(x)) for x in query_embedding)
        sql = (
            f"SELECT element_type, element_api_name, display_name, description, "
            f"inner_product_approximate(embedding, [{emb_items}]) AS similarity "
            f"FROM {self._SEMANTIC_TABLE} "
            "WHERE ontology_api_name = %s "
            "ORDER BY similarity DESC "
            f"LIMIT {int(top_k)}"
        )
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql, [ontology_api_name])
                    rows = await cursor.fetchall()
                    col_names = [d[0] for d in cursor.description] if cursor.description else []
                    return [dict(zip(col_names, row)) for row in rows]
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except DorisUnavailableError:
            raise
        except Exception as exc:
            raise DorisUnavailableError(f"Doris vector_search failed: {exc}") from exc

    # ── Helpers ──

    @staticmethod
    def _build_order_by_clause(sort: list[tuple[str, str]] | None) -> str:
        """Build an ORDER BY clause from (column, direction) tuples.

        Each column is validated as an identifier; direction is coerced to
        ASC/DESC. Returns "" (empty) when sort is None/empty so no ORDER BY
        is emitted (Doris returns rows in unspecified order).
        """
        if not sort:
            return ""
        parts: list[str] = []
        for col, direction in sort:
            _validate_identifier(col)
            direction = str(direction).upper()
            if direction not in ("ASC", "DESC"):
                direction = "ASC"
            parts.append(f"`{col}` {direction}")
        return ("ORDER BY " + ", ".join(parts)) if parts else ""

    async def _execute(self, sql: str) -> None:
        """Execute a SQL statement (DDL or DML)."""
        try:
            conn = await self._acquire()
            try:
                cursor = await self._cursor(conn)
                try:
                    await cursor.execute(sql)
                finally:
                    await cursor.close()
            finally:
                await self._release(conn)
        except Exception as exc:
            raise DorisUnavailableError(f"Doris unavailable: {exc}") from exc


def _escape(val: Any) -> str:
    """Escape a string value for SQL (single quotes + backslashes)."""
    # Backslash must be escaped first, then single quotes. Doris (MySQL protocol)
    # honours backslash escapes inside string literals, so failing to escape
    # backslashes enables injection (e.g. '\' turning into an escaped quote).
    return str(val).replace("\\", "\\\\").replace("'", "''")


def _validate_identifier(name: str) -> None:
    """Validate a SQL identifier (table/column name) is safe to interpolate.

    Identifiers come from ObjectType api_names / field names, which are
    user-influenced. Restrict to ASCII alphanumerics + underscore to close the
    injection vector; the value is still interpolated (identifiers cannot be
    parameterized in SQL), but only after this allowlist check.
    """
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise OntologyError(f"Invalid SQL identifier: {name!r}")


def _escape_val(val: Any) -> str:
    """Format a value for SQL insertion."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return str(val)
    # §14.4: ARRAY<FLOAT> embedding 列 — Doris ARRAY 字面量 [1.0, 2.0, ...]。
    # 嵌套元素走 _escape_val 递归 (float → str, None → NULL)。
    if isinstance(val, (list, tuple)):
        return "[" + ", ".join(_escape_val(v) for v in val) + "]"
    return f"'{_escape(str(val))}'"
