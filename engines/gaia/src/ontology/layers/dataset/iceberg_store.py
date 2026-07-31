# mypy: ignore-errors
"""IcebergStore — full-detail persistence via Apache Iceberg.

Two complementary channels are used to talk to the Gravitino-managed
Iceberg REST Catalog, each on the path where it is reliable under the
Gravitino ``memory`` backend:

* **Metadata** (schema, snapshots, namespace, drop, schema evolution) is
  read/written via **pyiceberg's ``RestCatalog``**. This gives us the
  library's mature handling of the Iceberg REST spec: typed
  schema/snapshot models, retry/backoff, and proper exception mapping
  (404 → ``NoSuchTableError`` etc.).

* **Data** (point lookups, scans, time-travel reads, writes) goes
  through **Trino**'s Iceberg connector (``iceberg.<ns>.<table>``).
  Trino reads the Parquet data files directly and is the architectural
  query engine. The REST ``/scan`` endpoint and pyiceberg's own
  file-based scan both fail under the Gravitino memory backend because
  the manifest/metadata files are not actually persisted to S3
  ("No in-memory file found").

Why ``GravitinoRestCatalog`` instead of plain ``load_catalog("rest")``:
pyiceberg's ``RestCatalog.__init__`` forces a ``GET /v1/config`` call
(with a ``warehouse`` query param) which the Gravitino memory backend's
``credential-providers=s3-token`` rejects with HTTP 401 ("The provided
credentials did not support"). Its default session also sends an
``X-Iceberg-Access-Delegation: vended-credentials`` header which
Gravitino rejects with HTTP 400, and an ``AuthManager`` that injects a
Bearer token rejected with 401. The subclass below skips the config
fetch (the defaults are known and empty for this server) and strips
the offending header/auth from the session — everything else in
``RestCatalog`` is reused verbatim.

Per architecture: Iceberg is the single write entry point for all
master data. Doris index sync and Trino queries read from Iceberg.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pyiceberg.catalog.rest import (
    DEFAULT_ENDPOINTS,
    DEFAULT_NAMESPACE_SEPARATOR,
    RestCatalog,
)

from ontology.config.settings import settings
from ontology.core.exceptions import IcebergUnavailableError, NotFoundError, OntologyError
from ontology.core.schemas.dataset import (
    ColumnDef,
    DatasetSchema,
    DatasetSnapshot,
    ManagedTableSchema,
    WriteResult,
)

if TYPE_CHECKING:
    from pyiceberg.table import Snapshot, Table

    from ontology.layers.engine.trino_query_engine import TrinoQueryEngine


# Trino catalog that exposes the Iceberg tables. The Gravitino catalog
# ("gravitino") does not register the Iceberg tables in this deployment,
# but the dedicated ``iceberg`` connector does.
_TRINO_ICEBERG_CATALOG = "iceberg"


class GravitinoRestCatalog(RestCatalog):
    """``RestCatalog`` adapted for the Gravitino memory backend.

    Overrides two hooks that ``RestCatalog.__init__`` invokes:

    * ``_fetch_config`` — bypasses ``GET /v1/config``, which 401s under
      Gravitino's ``s3-token`` credential provider when called with the
      ``warehouse`` query param. The server's config response is empty
      (``defaults:{}, overrides:{}``) and the endpoint set is the
      standard one, so we set the equivalent state directly.
    * ``_create_session`` — removes the ``X-Iceberg-Access-Delegation``
      header (Gravitino 400s on ``vended-credentials``) and the
      ``AuthManager``-backed ``session.auth`` (Gravitino 401s on any
      Bearer token). Plain unauthenticated JSON calls are accepted.

    All other ``RestCatalog`` behavior (typed responses, retry, endpoint
    routing, exception mapping) is inherited unchanged.
    """

    def _fetch_config(self) -> None:  # type: ignore[override]
        self._supported_endpoints = set(DEFAULT_ENDPOINTS)
        self._namespace_separator = DEFAULT_NAMESPACE_SEPARATOR

    def _create_session(self):  # type: ignore[override]
        session = super()._create_session()
        session.headers.pop("X-Iceberg-Access-Delegation", None)
        session.auth = None
        return session


def _build_rest_catalog() -> GravitinoRestCatalog:
    """Instantiate a ``GravitinoRestCatalog`` from application settings."""
    return GravitinoRestCatalog(
        "rest",
        uri=settings.iceberg_rest_uri,
        warehouse=settings.iceberg_warehouse,
        **{
            "s3.endpoint": settings.s3_endpoint,
            "s3.access-key-id": settings.s3_access_key_id,
            "s3.secret-access-key": settings.s3_secret_access_key,
            "s3.path-style-access": str(settings.s3_path_style_access),
            "s3.region": settings.s3_region,
        },
    )


class IcebergStore:
    """Dataset persistence layer over Apache Iceberg.

    Args:
        engine: Optional pre-configured ``TrinoQueryEngine`` used for
            data reads/writes. If ``None``, one is resolved lazily via
            the DI container on first use.
        catalog: Optional pre-configured ``GravitinoRestCatalog`` used
            for metadata operations. Primarily for tests.
    """

    def __init__(
        self,
        engine: TrinoQueryEngine | None = None,
        catalog: GravitinoRestCatalog | None = None,
    ) -> None:
        self._engine: TrinoQueryEngine | None = engine
        self._catalog: GravitinoRestCatalog | None = catalog

    # ── Dependency access (lazy) ──

    @property
    def engine(self) -> TrinoQueryEngine:
        """Lazy-initialized Trino engine for data operations."""
        if self._engine is None:
            from ontology.config.container import Container

            self._engine = Container().engine
        return self._engine

    @property
    def catalog(self) -> GravitinoRestCatalog:
        """Lazy-initialized pyiceberg REST catalog for metadata operations."""
        if self._catalog is None:
            self._catalog = _build_rest_catalog()
        return self._catalog

    # ═════════════════════════════════════════════════════════════════
    # Namespace / table lifecycle (pyiceberg RestCatalog)
    # ═════════════════════════════════════════════════════════════════

    async def ensure_warehouse_bucket(self) -> None:
        """Ensure the S3 bucket backing ``iceberg_warehouse`` exists.

        Iceberg/Gravitino manage namespace/table metadata, but the **S3
        bucket** is object-store infrastructure that neither creates
        automatically — SeaTunnel's Iceberg sink fails with
        ``NoSuchBucketException`` when the bucket is missing. Standard
        ``S3FileIO`` does not auto-create buckets either, so we must call
        the S3 ``CreateBucket`` API ourselves. Idempotent (HEAD then
        CREATE). Best-effort: failures are logged, not raised, so a
        transient S3 outage doesn't block app startup — the first sync
        will surface a clear ``NoSuchBucketException`` if creation truly
        failed.
        """
        import logging
        import re

        import aiobotocore.session

        from ontology.config.settings import settings

        _log = logging.getLogger(__name__)
        # Parse bucket name from "s3://ontology-warehouse/" → "ontology-warehouse".
        m = re.match(r"s3://([^/]+)", settings.iceberg_warehouse)
        if not m:
            _log.warning("Cannot parse bucket from warehouse %r", settings.iceberg_warehouse)
            return
        bucket = m.group(1)

        session = aiobotocore.session.get_session()
        async with session.create_client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        ) as s3:
            try:
                await s3.head_bucket(Bucket=bucket)
                return  # exists
            except Exception:
                pass  # not found (or 403 for no-list perms) → try create
            try:
                # us-east-1 does not accept a LocationConstraint; others require it.
                cfg = (
                    {}
                    if settings.s3_region in ("us-east-1", "", None)
                    else {"CreateBucketConfiguration": {"LocationConstraint": settings.s3_region}}
                )
                await s3.create_bucket(Bucket=bucket, **cfg)
                _log.info("Created S3 bucket '%s' (warehouse root)", bucket)
            except Exception as exc:
                # Likely already exists (race) or perm error — either way
                # best-effort; downstream will surface a real failure.
                _log.warning("Could not create S3 bucket '%s': %s", bucket, exc)

    async def ensure_namespace(self, namespace: str) -> None:
        """Create an Iceberg namespace if it doesn't already exist.

        SeaTunnel's Iceberg sink with ``schema_save_mode =
        CREATE_SCHEMA_WHEN_NOT_EXIST`` is *supposed* to create the
        namespace on the fly, but against the Gravitino Iceberg REST
        server (memory backend) that path has been observed to fail with
        ``NoSuchNamespaceException`` before the table is created. Creating
        the namespace up-front from the service layer makes the sync path
        deterministic regardless of SeaTunnel's save-mode quirks.

        Idempotent: a no-op when the namespace already exists.

        Best-effort: on REST failure the exception is swallowed so the
        caller (sync flow) can let SeaTunnel's own save-mode logic try.
        """
        # Ensure the backing S3 bucket exists BEFORE creating a namespace
        # in it — SeaTunnel's Iceberg sink writes data files into this
        # bucket and fails with NoSuchBucketException if it is missing.
        await self.ensure_warehouse_bucket()
        try:
            await self._run(self.catalog.create_namespace_if_not_exists, namespace)
        except Exception:
            # Best-effort: if we can't pre-create the namespace, let
            # SeaTunnel's own save-mode logic try (and surface its error
            # if it also fails). Don't block the whole sync on this.
            pass

    async def drop_table_if_exists(self, namespace: str, table: str) -> bool:
        """Drop an Iceberg table if it exists. Returns whether a drop happened.

        Used by full_snapshot sync to recreate the target table with the
        current source schema. Best-effort: returns False if the table is
        absent or the catalog is unreachable — the caller proceeds and
        lets SeaTunnel's CREATE_SCHEMA_WHEN_NOT_EXIST handle creation.
        """
        try:
            await self._run(self.catalog.drop_table, f"{namespace}.{table}")
            return True
        except Exception:
            return False

    # ═════════════════════════════════════════════════════════════════
    # Schema management (pyiceberg RestCatalog)
    # ═════════════════════════════════════════════════════════════════

    async def get_schema(self, dataset: str) -> DatasetSchema:
        """Get the schema of a dataset from the Iceberg table metadata."""
        table = await self._load_table(dataset)
        return DatasetSchema(
            columns=[
                ColumnDef(
                    name=field.name,
                    type=str(field.field_type),
                    nullable=not field.required,
                )
                for field in table.schema().fields
            ]
        )

    async def evolve_schema(self, dataset: str, additions: list[ColumnDef]) -> None:
        """Add new columns to a dataset schema via pyiceberg's update API.

        Args:
            dataset: Fully qualified table name
            additions: Columns to add

        Raises:
            IcebergUnavailableError: If the schema evolution fails.
        """
        if not additions:
            return
        table = await self._load_table(dataset)
        try:
            with table.update_schema() as updater:
                for col in additions:
                    updater.add_column(
                        col.name,
                        field_type=_iceberg_type_from_str(col.type),
                        required=not col.nullable,
                    )
        except Exception as exc:
            raise IcebergUnavailableError(f"Schema evolution failed for {dataset}: {exc}") from exc

    # ═════════════════════════════════════════════════════════════════
    # Managed table creation (Catalog First)
    # ═════════════════════════════════════════════════════════════════

    async def create_managed_table(
        self,
        dataset_api_name: str,
        schema: ManagedTableSchema,
        *,
        properties: dict[str, str] | None = None,
    ) -> None:
        """Create a Gaia-managed Iceberg table with full physical metadata.

        Per the Gravitino Catalog First principle: Gaia creates managed
        tables via the Iceberg REST catalog (pyiceberg), registering the
        complete physical schema — column comments, NOT-NULL constraints,
        primary-key identifier fields, and table-level properties (comment
        + provenance). SeaTunnel / pipeline writers only append data.

        Idempotent on an existing table: if the table already exists, it
        is **not** dropped (which would discard snapshots/history); the
        schema is reconciled via :meth:`ensure_schema` instead, and table
        properties are left untouched (their provenance is authoritative).

        Args:
            dataset_api_name: Iceberg table name (snake_case, == dataset
                api_name per ``core/naming.py``). The namespace is
                ``settings.iceberg_namespace``.
            schema: Column definitions + table comment.
            properties: Optional Iceberg table properties. ``comment`` is
                always set from ``schema.table_comment`` unless overridden
                here; ``format-version=2`` is added by default.
        """
        import logging

        from pyiceberg.schema import Schema
        from pyiceberg.types import NestedField

        _log = logging.getLogger(__name__)
        qualified = self._qualified(dataset_api_name)
        ns = settings.iceberg_namespace

        # 1. ensure namespace (idempotent)
        await self.ensure_namespace(ns)

        # 2. if table exists → reconcile schema (never drop)
        existing = await self._table_exists(dataset_api_name)
        if existing:
            _log.info(
                "create_managed_table: '%s' already exists — reconciling schema (history preserved)",
                qualified,
            )
            await self.ensure_schema(dataset_api_name, schema)
            return

        # 3. build pyiceberg Schema with comments, required flags, PK identifiers
        fields: list[NestedField] = []
        pk_ids: list[int] = []
        for idx, col in enumerate(schema.columns):
            field_id = idx + 1
            if col.is_primary_key:
                pk_ids.append(field_id)
            fields.append(
                NestedField(
                    field_id=field_id,
                    name=col.name,
                    field_type=_iceberg_type_from_str(col.type),
                    required=not col.nullable,
                    doc=col.comment or None,
                )
            )
        # identifier_field_ids must be a list (pyiceberg rejects None). Only
        # pass it when there are PK columns; otherwise let pyiceberg use its
        # default empty list.
        schema_kwargs: dict[str, Any] = {}
        if pk_ids:
            schema_kwargs["identifier_field_ids"] = pk_ids
        iceberg_schema = Schema(*fields, **schema_kwargs)

        # 4. table properties: comment from schema, format-version=2, caller overrides
        props: dict[str, str] = {"format-version": "2"}
        if schema.table_comment:
            props["comment"] = schema.table_comment
        if properties:
            props.update(properties)

        location = f"{settings.iceberg_warehouse.rstrip('/')}/{ns}/{dataset_api_name}"
        try:
            await self._run(
                self.catalog.create_table,
                identifier=qualified,
                schema=iceberg_schema,
                location=location,
                properties=props,
            )
            _log.info(
                "create_managed_table: created '%s' (%d cols, %d PK) props=%s",
                qualified,
                len(fields),
                len(pk_ids),
                {k: v for k, v in props.items() if k != "comment"},
            )
        except Exception as exc:
            # CREATE_SCHEMA_WHEN_NOT_EXIST race with SeaTunnel / concurrent define:
            # a parallel writer may have created the table between our existence
            # check and create_table. Reconcile instead of failing.
            if await self._table_exists(dataset_api_name):
                _log.warning(
                    "create_managed_table: '%s' appeared concurrently — reconciling schema",
                    qualified,
                )
                await self.ensure_schema(dataset_api_name, schema)
                return
            raise IcebergUnavailableError(
                f"Failed to create managed table '{qualified}': {exc}"
            ) from exc

    async def ensure_schema(self, dataset_api_name: str, schema: ManagedTableSchema) -> None:
        """Reconcile an existing managed table's schema without dropping it.

        Adds columns present in ``schema`` but absent from the Iceberg
        table (with their comment + required flag), preserving existing
        snapshots/history. Existing columns are **not** renamed or retyped
        (Iceberg schema evolution does not support in-place rename/type
        change via the additive ``update_schema`` API; that requires an
        explicit rewrite, out of scope here).

        No-op when the table's current schema already covers every column
        in ``schema``.
        """
        import logging

        _log = logging.getLogger(__name__)
        table = await self._load_table(dataset_api_name)
        existing_names = {f.name for f in table.schema().fields}
        additions = [c for c in schema.columns if c.name not in existing_names]
        if not additions:
            return
        try:
            with table.update_schema() as updater:
                for col in additions:
                    updater.add_column(
                        col.name,
                        field_type=_iceberg_type_from_str(col.type),
                        required=not col.nullable,
                        doc=col.comment or None,
                    )
            _log.info(
                "ensure_schema: added %d column(s) to '%s' (history preserved)",
                len(additions),
                self._qualified(dataset_api_name),
            )
        except Exception as exc:
            raise IcebergUnavailableError(
                f"Schema reconciliation failed for '{dataset_api_name}': {exc}"
            ) from exc

    async def _table_exists(self, dataset_api_name: str) -> bool:
        """Cheap existence check via pyiceberg ``load_table`` (404 → False)."""
        qualified = self._qualified(dataset_api_name)
        try:
            await self._run(self.catalog.load_table, qualified)
            return True
        except Exception:
            return False


    # ═════════════════════════════════════════════════════════════════
    # Snapshot management (pyiceberg RestCatalog)
    # ═════════════════════════════════════════════════════════════════

    async def get_snapshots(self, dataset: str) -> list[DatasetSnapshot]:
        """Get all snapshots for a dataset."""
        table = await self._load_table(dataset)
        return [_snapshot_to_model(s) for s in table.snapshots()]

    async def get_latest_snapshot(self, dataset: str) -> DatasetSnapshot | None:
        """Get the current (latest) snapshot. Returns None if no data yet."""
        table = await self._load_table(dataset)
        current = table.current_snapshot()
        if current is None:
            return None
        return _snapshot_to_model(current)

    # ═════════════════════════════════════════════════════════════════
    # Data reads (Trino Iceberg connector)
    # ═════════════════════════════════════════════════════════════════

    async def load_by_ids(
        self,
        dataset: str,
        ids: list[str],
        columns: list[str],
        pk_column: str = "id",
    ) -> list[dict[str, Any]]:
        """Load full attributes for given IDs via Trino.

        Uses a ``WHERE <pk_column> IN (...)`` query against the Iceberg table
        through Trino.

        Args:
            dataset: Fully qualified table name
            ids: List of primary key values
            columns: Columns to return
            pk_column: Physical Iceberg column name for the primary key
                (default "id"; pass the ObjectType's primary_key-derived
                physical column for tables whose PK isn't literally "id").

        Returns:
            List of row dicts with full attributes.
        """
        if not ids:
            return []
        return await self._query_by_ids(dataset, columns, ids, snapshot_id=None, pk_column=pk_column)

    async def load_by_ids_as_of(
        self,
        dataset: str,
        ids: list[str],
        columns: list[str],
        snapshot_id: int,
    ) -> list[dict[str, Any]]:
        """Load historical attributes from a specific snapshot.

        Uses Trino's ``FOR VERSION AS OF <snapshot_id>`` time-travel
        syntax on the Iceberg table.

        Args:
            dataset: Fully qualified table name
            ids: List of primary key values
            columns: Columns to return
            snapshot_id: Iceberg snapshot ID for time travel

        Returns:
            List of row dicts as of the given snapshot.
        """
        if not ids:
            return []
        return await self._query_by_ids(dataset, columns, ids, snapshot_id=snapshot_id)

    async def scan_latest(
        self,
        dataset: str,
        columns: list[str],
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """Scan the latest snapshot of a dataset via Trino.

        Used by IndexSyncService.sync_now to read indexed columns out of
        Iceberg and upsert them into the Doris index table — the available
        sync path while SeaTunnel 2.3.13's Iceberg source lacks REST-catalog
        support (see ADR-008).

        Args:
            dataset: Fully qualified table name.
            columns: Columns to return.
            limit: Maximum rows.
        """
        table_ref = self._trino_table_ref(dataset, snapshot_id=None)
        cols = ", ".join(columns) if columns else "*"
        sql = f"SELECT {cols} FROM {table_ref} LIMIT {int(limit)}"
        return await self.engine.query(sql)

    async def scan_as_of(
        self,
        dataset: str,
        columns: list[str],
        snapshot_id: int,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Scan dataset as of a historical snapshot.

        Args:
            dataset: Fully qualified table name
            columns: Columns to return
            snapshot_id: Iceberg snapshot ID
            limit: Maximum rows

        Returns:
            List of row dicts as of the given snapshot.
        """
        table_ref = self._trino_table_ref(dataset, snapshot_id=snapshot_id)
        cols = ", ".join(columns) if columns else "*"
        sql = f"SELECT {cols} FROM {table_ref} LIMIT {int(limit)}"
        return await self.engine.query(sql)

    # ═════════════════════════════════════════════════════════════════
    # Data writes (Trino Iceberg connector)
    # ═════════════════════════════════════════════════════════════════

    async def append(self, dataset: str, rows: list[dict[str, Any]]) -> WriteResult:
        """Append rows to a dataset via a Trino ``INSERT``.

        Args:
            dataset: Fully qualified table name (e.g. 'ontology.employees')
            rows: List of row dicts

        Returns:
            WriteResult with snapshot info and row count.
        """
        if not rows:
            return WriteResult(
                snapshot=DatasetSnapshot(snapshot_id=0, timestamp=0, operation="append"),
                rows_written=0,
            )
        table_ref = self._trino_table_ref(dataset)
        columns = list(rows[0].keys())
        col_list = ", ".join(columns)
        values_sql = ", ".join("(" + ", ".join(_sql_literal(row[c]) for c in columns) + ")" for row in rows)
        await self.engine.query(f"INSERT INTO {table_ref} ({col_list}) VALUES {values_sql}")
        latest = await self.get_latest_snapshot(dataset)
        return WriteResult(
            snapshot=latest if latest is not None else DatasetSnapshot(snapshot_id=0, timestamp=0, operation="append"),
            rows_written=len(rows),
        )

    async def merge(
        self,
        dataset: str,
        rows: list[dict[str, Any]],
        pk_columns: list[str],
        *,
        delete: bool = False,
    ) -> WriteResult:
        """MERGE INTO — 按业务 PK 覆盖旧记录 (upsert) 或删除 (delete=True).

        action-sync-outbox-design.md §3.3/§8.4: Trino 的普通 INSERT 对 Iceberg v2
        upsert 表**不会自动去重** (即使配了 primary-keys + write.upsert.enabled),
        INSERT 仍追加新行产生重复 PK。upsert 语义只在 Flink 写入时生效。Trino 要
        实现"按 PK 覆盖"必须用 MERGE INTO。

        ⚠️ PK 是业务主键的 backing_column (如 flight_id), 不是 object_id
        (design §3.3)。ON 条件、DELETE 都用 pk_columns 指定的业务 PK 列。

        Args:
            dataset: 业务表名 (如 'ontology.flight')。
            rows: 行数据 (key = backing_column = 列名)。
                delete=True 时只需 PK 列; delete=False 时需全量属性列。
            pk_columns: 业务主键列名列表 (backing_column)。用于 ON 匹配。
                多列联合 PK 时传多个。列名走 _validate_identifier 白名单校验。
            delete: True=WHEN MATCHED THEN DELETE; False=UPDATE+INSERT (upsert)。
        """
        if not rows:
            return WriteResult(
                snapshot=DatasetSnapshot(snapshot_id=0, timestamp=0, operation="delete" if delete else "append"),
                rows_written=0,
            )
        if not pk_columns:
            raise OntologyError("IcebergStore.merge requires at least one pk_column (business PK)")

        table_ref = self._trino_table_ref(dataset)
        for pk in pk_columns:
            _validate_identifier(pk)

        # source 行的所有列 (并集); 按 row 顺序保留首次出现。
        columns: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for c in row.keys():
                if c not in seen:
                    seen.add(c)
                    columns.append(c)
        for c in columns:
            _validate_identifier(c)
        col_list = ", ".join(columns)
        # VALUES 子句: 每行一组括号, 列顺序与 columns 对齐 (缺失列填 NULL)。
        values_sql = ", ".join(
            "(" + ", ".join(_sql_literal(row.get(c)) for c in columns) + ")" for row in rows
        )
        source_cols = ", ".join(columns)
        # ON 匹配条件: 所有 pk_columns 等值连接 (联合 PK)。
        on_clause = " AND ".join(f"target.{pk} = source.{pk}" for pk in pk_columns)

        if delete:
            # WHEN MATCHED THEN DELETE (source 只需 PK 列)。
            sql = (
                f"MERGE INTO {table_ref} AS target "
                f"USING (VALUES {values_sql}) AS source ({source_cols}) "
                f"ON {on_clause} "
                f"WHEN MATCHED THEN DELETE"
            )
        else:
            # WHEN MATCHED THEN UPDATE + WHEN NOT MATCHED THEN INSERT (upsert)。
            set_clause = ", ".join(f"{c} = source.{c}" for c in columns)
            insert_cols = col_list
            insert_vals = ", ".join(f"source.{c}" for c in columns)
            sql = (
                f"MERGE INTO {table_ref} AS target "
                f"USING (VALUES {values_sql}) AS source ({source_cols}) "
                f"ON {on_clause} "
                f"WHEN MATCHED THEN UPDATE SET {set_clause} "
                f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals})"
            )
        await self.engine.query(sql)
        latest = await self.get_latest_snapshot(dataset)
        op = "delete" if delete else "overwrite"
        return WriteResult(
            snapshot=latest
            if latest is not None
            else DatasetSnapshot(snapshot_id=0, timestamp=0, operation=op),  # type: ignore[arg-type]
            rows_written=len(rows),
        )

    async def overwrite(self, dataset: str, rows: list[dict[str, Any]]) -> WriteResult:
        """Overwrite all data in a dataset via ``DELETE`` + ``INSERT``.

        The Trino Iceberg connector does not support a single atomic
        overwrite for arbitrary row sets, so this is implemented as a
        full delete followed by an insert.

        Args:
            dataset: Fully qualified table name
            rows: List of row dicts

        Returns:
            WriteResult with snapshot info and row count.
        """
        table_ref = self._trino_table_ref(dataset)
        await self.engine.query(f"DELETE FROM {table_ref}")
        if not rows:
            latest = await self.get_latest_snapshot(dataset)
            return WriteResult(
                snapshot=latest
                if latest is not None
                else DatasetSnapshot(snapshot_id=0, timestamp=0, operation="delete"),
                rows_written=0,
            )
        columns = list(rows[0].keys())
        col_list = ", ".join(columns)
        values_sql = ", ".join("(" + ", ".join(_sql_literal(row[c]) for c in columns) + ")" for row in rows)
        await self.engine.query(f"INSERT INTO {table_ref} ({col_list}) VALUES {values_sql}")
        latest = await self.get_latest_snapshot(dataset)
        return WriteResult(
            snapshot=latest
            if latest is not None
            else DatasetSnapshot(snapshot_id=0, timestamp=0, operation="overwrite"),
            rows_written=len(rows),
        )

    # ═════════════════════════════════════════════════════════════════
    # Helpers
    # ═════════════════════════════════════════════════════════════════

    async def _load_table(self, dataset: str) -> Table:
        """Load a pyiceberg Table by qualified name.

        Raises:
            NotFoundError: If the dataset/table doesn't exist.
        """
        try:
            return await self._run(self.catalog.load_table, self._qualified(dataset))
        except Exception as exc:
            # pyiceberg raises NoSuchTableError / NamespaceNotFoundError etc.
            raise NotFoundError("Dataset", dataset) from exc

    @staticmethod
    async def _run(func: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a sync pyiceberg call in a thread.

        pyiceberg's ``RestCatalog`` uses a ``requests.Session`` (sync),
        so we offload each call to a worker thread to stay non-blocking.
        """
        import asyncio

        return await asyncio.to_thread(func, *args, **kwargs)

    @staticmethod
    def _qualified(dataset: str) -> str:
        """Return a namespace-qualified dataset identifier.

        Stored dataset api_names (e.g. ``"object_types_raw"``) carry no
        namespace; the Iceberg REST catalog and Trino both require one.
        If ``dataset`` already contains a dot it is returned unchanged.
        """
        if "." in dataset:
            return dataset
        return f"{settings.iceberg_namespace}.{dataset}"

    def _trino_table_ref(self, dataset: str, snapshot_id: int | None = None) -> str:
        """Build a Trino table reference with optional time-travel suffix.

        ``dataset`` may arrive as either ``schema.table`` (2 parts, the
        historical form) or ``catalog.schema.table`` (3 parts, as returned
        by GravitinoRegistry.resolve_backing_table). In the 3-part case the
        leading catalog is replaced by the Trino iceberg catalog name to
        avoid a 4-part ``iceberg.iceberg.ontology.flight`` reference
        ("Too many dots in table name").
        """
        qualified = self._qualified(dataset)
        parts = qualified.split(".")
        if len(parts) >= 3:
            # catalog.schema.table[.more] → drop the leading catalog, re-prefix
            # with the Trino iceberg catalog so the final ref is
            # iceberg.<schema>.<table>.
            ns_table = ".".join(parts[1:])
            ref = f"{_TRINO_ICEBERG_CATALOG}.{ns_table}"
        else:
            ns, table = qualified.split(".", 1)
            ref = f"{_TRINO_ICEBERG_CATALOG}.{ns}.{table}"
        if snapshot_id is not None:
            ref += f" FOR VERSION AS OF {int(snapshot_id)}"
        return ref

    async def _query_by_ids(
        self,
        dataset: str,
        columns: list[str],
        ids: list[str],
        snapshot_id: int | None,
        pk_column: str = "id",
    ) -> list[dict[str, Any]]:
        """Run a ``SELECT ... WHERE <pk_column> IN (...)`` via Trino.

        IDs that look like integers are rendered as unquoted numeric
        literals so a BIGINT PK column doesn't fail with TYPE_MISMATCH
        ("Cannot find common type between bigint and varchar"); non-numeric
        IDs stay quoted string literals.
        """
        table_ref = self._trino_table_ref(dataset, snapshot_id=snapshot_id)
        cols = ", ".join(columns) if columns else "*"
        id_list = ", ".join(self._id_literal(i) for i in ids)
        sql = f"SELECT {cols} FROM {table_ref} WHERE {pk_column} IN ({id_list})"
        return await self.engine.query(sql)

    @staticmethod
    def _id_literal(v: Any) -> str:
        """Render an object id as a SQL literal, unquoting pure integers."""
        if v is None:
            return "NULL"
        s = str(v)
        # Pure integer id → numeric literal (avoids BIGINT↔varchar cast error).
        if s.lstrip("-").isdigit():
            return s
        escaped = s.replace("'", "''")
        return f"'{escaped}'"


# ═════════════════════════════════════════════════════════════════════
# Pure helpers (module-level for testability)
# ═════════════════════════════════════════════════════════════════════


def _snapshot_to_model(snap: Snapshot) -> DatasetSnapshot:
    """Convert a pyiceberg ``Snapshot`` to a ``DatasetSnapshot``.

    pyiceberg's ``Snapshot.summary`` is a pydantic ``Summary`` model;
    ``model_dump()`` yields a plain dict with string values (e.g.
    ``{"operation": "append", ...}``).
    """
    summary_obj = snap.summary
    if hasattr(summary_obj, "model_dump"):
        summary = dict(summary_obj.model_dump())
    elif summary_obj:
        summary = dict(summary_obj)
    else:
        summary = {}
    operation = str(summary.get("operation", "append"))
    # pyiceberg may render the operation as ``"Operation.APPEND"``;
    # normalize to the lowercase token Iceberg uses.
    if operation.startswith("Operation."):
        operation = operation.split(".", 1)[1].lower()
    if operation not in ("append", "overwrite", "delete"):
        operation = "append"
    return DatasetSnapshot(
        snapshot_id=int(snap.snapshot_id),
        timestamp=int(snap.timestamp_ms),
        operation=operation,  # type: ignore[arg-type]
        summary=summary,
    )


def _iceberg_type_from_str(type_str: str) -> Any:
    """Map a stored type string back to a pyiceberg type.

    ``ColumnDef.type`` holds the string rendered by ``str(field_type)``
    (e.g. ``"string"``, ``"long"``, ``"int"``, ``"double"``,
    ``"timestamp"``, ``"list<string>"``). For primitive types we map to
    the pyiceberg singleton; for parameterized types we fall back to
    ``StringType`` to keep schema-evolution commits valid (nested-type
    evolution is out of scope).
    """
    from pyiceberg.types import (
        BinaryType,
        BooleanType,
        DateType,
        DoubleType,
        IntegerType,
        LongType,
        StringType,
        TimestampType,
        TimestamptzType,
        TimeType,
        UUIDType,
    )

    mapping: dict[str, Any] = {
        "string": StringType(),
        "boolean": BooleanType(),
        "int": IntegerType(),
        "integer": IntegerType(),
        "long": LongType(),
        "float": DoubleType(),
        "double": DoubleType(),
        "date": DateType(),
        "time": TimeType(),
        "timestamp": TimestampType(),
        "timestamptz": TimestamptzType(),
        "binary": BinaryType(),
        "uuid": UUIDType(),
    }
    if isinstance(type_str, str) and type_str in mapping:
        return mapping[type_str]
    # Parameterized / unknown types — fall back to string.
    return StringType()


def _sql_literal(value: Any) -> str:
    """Render a Python value as a Trino SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    # str and everything else → quoted string literal (single quotes doubled)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _validate_identifier(name: str) -> None:
    """Validate a SQL identifier (table/column name) is safe to interpolate.

    Identifiers come from backing_column / pk_columns (user-influenced via
    ObjectType config). Restrict to ASCII alphanumerics + underscore to close
    the injection vector; the value is still interpolated (identifiers cannot
    be parameterized in SQL), but only after this allowlist check.
    """
    import re

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise OntologyError(f"Invalid SQL identifier: {name!r}")


__all__ = ["IcebergStore", "GravitinoRestCatalog"]
