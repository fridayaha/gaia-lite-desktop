"""GravitinoRegistry — physical asset registration center.

Wraps the Apache Gravitino REST API for:
- Iceberg table registration
- View (virtual table) creation and inspection
- RBAC permission checks
- Physical table route resolution

Per architecture constraints: Gravitino stores NO business ontology metadata.
It only manages physical data assets, RBAC, and lineage.
"""

from typing import Any

from httpx import AsyncClient, ConnectError

from ontology.config.settings import settings
from ontology.core.exceptions import GravitinoUnavailableError, NotFoundError
from ontology.core.naming import managed_dataset_api_name


class GravitinoRegistry:
    """Physical asset registry via Apache Gravitino REST API.

    Args:
        client: Optional pre-configured httpx AsyncClient. If None,
                creates one using settings.gravitino_uri.
    """

    def __init__(self, client: AsyncClient | None = None) -> None:
        self.client = client or AsyncClient(
            base_url=settings.gravitino_uri,
            timeout=30.0,
        )

    def _metalake_path(self) -> str:
        """Default metalake is 'ontology'."""
        return "/api/metalakes/ontology"

    # Ontology data_type (uppercase, SQL-ish) → Iceberg REST type name.
    # Iceberg rejects "integer" (wants "int") and bare "decimal" (wants
    # "decimal(p,s)"). VIRTUAL-only complex types (STRUCT/ARRAY) are mapped
    # to string since they never reach Iceberg (VIRTUAL objects skip
    # register_dataset) — the mapping exists purely defensively.
    _ICEBERG_TYPE_MAP: dict[str, str] = {
        "integer": "int",
        "long": "long",
        "bigint": "long",
        "string": "string",
        "varchar": "string",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "timestamp",
        "double": "double",
        "float": "float",
        "decimal": "decimal(38,18)",  # max-precision default; callers needing specific scale pass it through
        "struct": "string",
        "array": "string",
        "json": "string",
    }

    @classmethod
    def _to_iceberg_type(cls, raw: object) -> str:
        """Convert an ontology data_type to an Iceberg REST type string."""
        if not raw:
            return "string"
        key = str(raw).strip().lower()
        # Pass through already-valid Iceberg type literals (e.g. "decimal(10,2)",
        # "int", "long") unchanged so callers can supply precise types.
        if key in cls._ICEBERG_TYPE_MAP:
            return cls._ICEBERG_TYPE_MAP[key]
        if key.startswith("decimal("):
            return key
        if key in ("int", "long", "string", "boolean", "date", "timestamp", "double", "float"):
            return key
        # Unknown → string is a safe fallback that never 400s; the real type
        # mismatch surfaces at query time if it matters.
        return "string"

    # ── Dataset Registration ──

    async def register_dataset(
        self,
        schema: str,
        name: str,
        location: str,
        columns: list[dict[str, object]],
        catalog: str = "",
    ) -> None:
        """Register a physical table in the Iceberg catalog.

        Uses the Iceberg REST API (port 9001 — Gravitino's iceberg-rest
        auxiliary service) directly, which is the same endpoint that
        Trino's Iceberg connector points to. This ensures tables created
        here are immediately visible to Trino queries.

        The ``catalog`` param is accepted for API consistency with callers
        but is unused by the REST bridge — it already targets the single
        Iceberg REST endpoint configured at ``iceberg.rest-catalog.uri``.
        """
        import logging

        _log = logging.getLogger(__name__)
        # Use the backend's own Iceberg REST view (settings.iceberg_rest_uri,
        # typically localhost:9001 when the backend runs on the host). The
        # SeaTunnel/Gravitino container view (gravitino:9001) is a different
        # setting — hardcoding it here made the backend wait on DNS resolution
        # of "gravitino" for ~18s per MANAGED object and never registered the
        # Iceberg table when running outside the container network.
        iceberg_api = f"{settings.iceberg_rest_uri.rstrip('/')}/v1"
        ns_url = f"{iceberg_api}/namespaces"

        # Create the namespace (schema) if it does not exist yet.
        try:
            ns_resp = await self.client.post(ns_url, json={"namespace": [schema]})
            if ns_resp.status_code not in (200, 201, 409):
                ns_resp.raise_for_status()
        except Exception as exc:
            _log.warning("Iceberg namespace creation skipped: %s", exc)

        # Build column definitions in Iceberg REST format, with field IDs.
        # Ontology data_type uses uppercase SQL-ish names (INTEGER/STRING/...);
        # Iceberg REST requires its own lowercase type names. Notably INTEGER→int
        # and DECIMAL→decimal(p,s) (needs precision). Map them here so table
        # creation doesn't 400 on every MANAGED object.
        iceberg_cols = [
            {
                "name": col["name"],
                "type": self._to_iceberg_type(col.get("type", "string")),
                "id": idx + 1,
                "required": False,
            }
            for idx, col in enumerate(columns)
        ]
        table_url = f"{ns_url}/{schema}/tables"
        tbl_payload: dict[str, object] = {
            "name": name,
            "schema": {
                "type": "struct",
                "fields": iceberg_cols,
            },
            "location": location,
        }
        try:
            tbl_resp = await self.client.post(table_url, json=tbl_payload)
            if tbl_resp.status_code == 409:
                return
            tbl_resp.raise_for_status()
        except Exception as exc:
            raise GravitinoUnavailableError(f"Iceberg API error: {exc}") from exc

    async def _ensure_catalog(self, catalog: str) -> None:
        """Lazily ensure a catalog exists in Gravitino.

        Checks ``GET /metalakes/ontology/catalogs`` for the target name.
        If missing, creates it with the ``lakehouse-iceberg`` provider,
        pointing the warehouse to the same S3 endpoint (RustFS) that
        Trino's Iceberg connector and the Iceberg REST bridge use.

        Safe to call on every ``register_dataset`` — the check is a cheap
        list lookup, and POST returns 409 if the catalog already exists.
        """
        import logging

        _log = logging.getLogger(__name__)
        try:
            resp = await self.client.get(f"{self._metalake_path()}/catalogs")
            if resp.status_code == 200:
                body = resp.json()
                existing: list[dict[str, object]] = body if isinstance(body, list) else body.get("identifiers", [])
                names = {e.get("name") if isinstance(e, dict) else str(e) for e in existing}
                if catalog in names:
                    return  # already exists
        except Exception:
            pass  # probe failed — try create anyway

        # Create the catalog with lakehouse-iceberg provider.
        payload: dict[str, object] = {
            "name": catalog,
            "type": "relational",
            "provider": "lakehouse-iceberg",
            "comment": f"Gaia {catalog} catalog (auto-created)",
            "properties": {
                "catalog-backend": "memory",
                "uri": "http://localhost:9001/iceberg",
                "warehouse": "s3://ontology-warehouse/",
            },
        }
        try:
            resp = await self.client.post(f"{self._metalake_path()}/catalogs", json=payload)
            if resp.status_code == 409:
                return
            resp.raise_for_status()
            _log.info("Created Gravitino catalog '%s' (lakehouse-iceberg)", catalog)
        except Exception as exc:
            _log.warning("Failed to create Gravitino catalog '%s': %s", catalog, exc)
            # Non-blocking: Trino/Iceberg connector may already have
            # out-of-band access to the warehouse.
            raise

    async def _ensure_schema(self, catalog: str, schema: str) -> None:
        """Lazily ensure a schema (namespace) exists under a catalog.

        Gravitino requires schemas to exist before tables can be created
        under them. Unlike JDBC catalogs whose schemas are auto-discovered
        from the actual database, ``lakehouse-iceberg`` catalogs start empty
        and must have schemas created explicitly via the REST API.

        Safe to call on every ``register_dataset`` — checks first, POST
        returns 409 if the schema already exists.
        """
        import logging

        _log = logging.getLogger(__name__)
        url = f"{self._metalake_path()}/catalogs/{catalog}/schemas"
        try:
            resp = await self.client.get(url)
            if resp.status_code == 200:
                existing = resp.json().get("identifiers", [])
                names = {e.get("name") if isinstance(e, dict) else str(e) for e in existing}
                if schema in names:
                    return
        except Exception:
            pass
        try:
            resp = await self.client.post(url, json={"name": schema})
            if resp.status_code == 409:
                return
            resp.raise_for_status()
        except Exception as exc:
            _log.warning(
                "Failed to create Gravitino schema '%s' under '%s': %s",
                schema,
                catalog,
                exc,
            )
            raise

    #
    # create_view (Gravitino SQL View) was removed: it collided with the
    # Palantir Virtual Table semantics (see docs/design/dataset-ontology-binding.md
    # §1.2 and §3.4). Virtual Tables (kind=VIRTUAL) are now registered as
    # DatasetGovernance pointers to external tables, not as Gravitino Views.
    # is_view is retained as a low-level runtime probe (it does not imply
    # the DatasetGovernance.is_view semantics narrowed in §1.4).

    async def is_view(
        self,
        catalog: str,
        schema: str,
        name: str,
    ) -> bool:
        """Check if a table is a Gravitino view (runtime probe).

        This is a low-level catalog capability. It is NOT the same as the
        DatasetGovernance.is_view flag, whose semantics were narrowed to a
        Managed Table Foundry-View subtype marker (currently always False).

        Returns:
            True if the table exists and is a view, False otherwise.
        """
        url = f"{self._metalake_path()}/catalogs/{catalog}/schemas/{schema}/tables/{name}"
        try:
            response = await self.client.get(url)
            if response.status_code == 404:
                return False
            response.raise_for_status()
            info: dict[str, object] = response.json()
            return bool(info.get("type") == "view")
        except Exception:
            return False

    # ── RBAC ──

    async def check_access(
        self,
        object_type_api_name: str,
        operation: str,
    ) -> bool:
        """Check if the current principal has access to an object type.

        Per architecture (architecture_plan.md §4.1, implementation-status.md):
        the current phase only supports object-type-level read/write checks;
        attribute-level permissions and visibility-based filtering are deferred.
        Gravitino RBAC is not yet wired to a real authorizer, so this method
        is **permissive by default**: it only denies when an authorizer is
        actually reachable and explicitly returns ``allowed=false``.

        Failure modes mapped to allow (fail-open), consistent with the
        architecture's "degrade rather than crash" principle:
          * Gravitino unreachable (ConnectError)        → allow
          * Gravitino returns 404 (no perm endpoint)    → allow (current state)
          * Gravitino returns 5xx / transport error     → allow + warn
          * Gravitino returns 200 with allowed=false    → deny (the only deny path)

        Args:
            object_type_api_name: Object type identifier
            operation: 'read' or 'write'

        Returns:
            True if access is allowed, False otherwise.
        """
        import logging

        _log = logging.getLogger(__name__)
        url = f"{self._metalake_path()}/permissions/{object_type_api_name}/{operation}"
        try:
            response = await self.client.get(url)
        except ConnectError:
            # Gravitino unreachable → bypass permission check for physical tables
            return True
        except Exception as exc:
            # Transport-level failure (timeout, reset, …) → fail open + warn.
            _log.warning(
                "Gravitino permission check transport error for %s/%s: %s; allowing",
                object_type_api_name,
                operation,
                exc,
            )
            return True

        # No permission endpoint configured (Gravitino 1.2.0/1.3.0 has no
        # /permissions/... route) → no explicit policy → allow.
        if response.status_code == 404:
            return True
        # Server-side failure → cannot determine policy → fail open + warn.
        if response.status_code >= 400:
            _log.warning(
                "Gravitino permission check for %s/%s returned %s; allowing",
                object_type_api_name,
                operation,
                response.status_code,
            )
            return True

        try:
            result: dict[str, object] = response.json()
        except Exception:
            # Non-JSON body → treat as no explicit policy → allow.
            return True
        # The ONLY path that denies: authorizer present and explicit deny.
        return bool(result.get("allowed", True))

    # ═══════════════════════════════════════════════════════════
    # Dynamic Catalog Management (DataSource integration)
    # ═══════════════════════════════════════════════════════════

    async def register_jdbc_catalog(
        self,
        catalog_name: str,
        provider: str,
        jdbc_url: str,
        jdbc_database: str,
        jdbc_user: str,
        jdbc_password: str,
        jdbc_driver: str,
    ) -> None:
        """Dynamically register a JDBC Catalog in Gravitino.

        POST /api/metalakes/ontology/catalogs
        { name, type: "relational", provider, properties: {jdbc-url, ...} }

        Gravitino (1.2.0/1.3.0) supports dynamic catalog creation via REST API.
        The new catalog is immediately available to Trino via Gravitino Connector.

        Prerequisite: JDBC driver jar must be placed in the Gravitino plugin dir:
          ${GRAVITINO_HOME}/catalogs/{provider}/libs/{driver}.jar
        """
        url = f"{self._metalake_path()}/catalogs"
        payload: dict[str, object] = {
            "name": catalog_name,
            "type": "relational",
            "provider": provider,
            "comment": f"Gaia DataSource: {catalog_name}",
            "properties": {
                "jdbc-url": jdbc_url,
                "jdbc-database": jdbc_database,
                "jdbc-user": jdbc_user,
                "jdbc-password": jdbc_password,
                "jdbc-driver": jdbc_driver,
            },
        }
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise GravitinoUnavailableError(f"Failed to register JDBC catalog '{catalog_name}': {exc}") from exc

    # ── Multi-source catalog registration (multi-source-data-fusion-design.md §6) ──

    async def _register_typed_catalog(
        self,
        catalog_name: str,
        catalog_type: str,
        provider: str,
        properties: dict[str, str],
        comment: str = "",
    ) -> None:
        """Dynamically register a catalog in Gravitino via REST API.

        Unified backend for lakehouse / kafka / fileset catalogs. Mirrors
        ``register_jdbc_catalog`` (relational type) but for the other
        Gravitino catalog types.

        POST /api/metalakes/ontology/catalogs
        { name, type, provider, comment, properties }
        """
        url = f"{self._metalake_path()}/catalogs"
        payload: dict[str, object] = {
            "name": catalog_name,
            "type": catalog_type,
            "provider": provider,
            "comment": comment or f"Gaia DataSource: {catalog_name}",
            "properties": properties,
        }
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise GravitinoUnavailableError(
                f"Failed to register {catalog_type}/{provider} catalog '{catalog_name}': {exc}"
            ) from exc

    async def register_lakehouse_catalog(
        self,
        catalog_name: str,
        provider: str,
        properties: dict[str, str],
    ) -> None:
        """Register an external lakehouse catalog as a federation source.

        Lets Gaia query existing Hive/Delta/Hudi/Paimon tables without
        landing them (VIRTUAL federation, §6.2). Gravitino 1.2.0+ Generic
        Lakehouse Catalog (#9647) unifies Delta/Hudi/Paimon under one entry.

        Args:
            catalog_name: Catalog name (== DataSource api_name).
            provider: One of "hive", "lakehouse-delta", "lakehouse-hudi",
                "lakehouse-paimon", "lakehouse-iceberg".
            properties: Provider-specific config, e.g.:
                hive: {"metastore-uri": "thrift://hms:9083"}
                lakehouse-delta: {"catalog-backend": "hive", "warehouse": "s3://..."}
        """
        await self._register_typed_catalog(
            catalog_name=catalog_name,
            catalog_type="relational",
            provider=provider,
            properties=properties,
            comment=f"Gaia lakehouse federation source: {catalog_name}",
        )

    async def register_kafka_catalog(
        self,
        catalog_name: str,
        bootstrap_servers: str,
        properties: dict[str, str] | None = None,
    ) -> None:
        """Register a Kafka catalog to manage topic metadata.

        Gravitino Kafka catalog only manages topic metadata (create/list/
        describe) — it does NOT store message content (§6.4.1). Message
        consumption goes through SeaTunnel/Trino, not Gravitino.

        Args:
            catalog_name: Catalog name (== DataSource api_name).
            bootstrap_servers: Kafka bootstrap servers (e.g. "kafka:9092").
            properties: Extra Kafka properties (sasl/ssl etc.).
        """
        props = {"bootstrap.servers": bootstrap_servers}
        if properties:
            props.update(properties)
        await self._register_typed_catalog(
            catalog_name=catalog_name,
            catalog_type="messaging",
            provider="kafka",
            properties=props,
            comment=f"Gaia Kafka federation source: {catalog_name}",
        )

    async def register_fileset_catalog(
        self,
        catalog_name: str,
        provider: str,
        properties: dict[str, str],
    ) -> None:
        """Register a Fileset catalog to manage file/object storage metadata.

        Gravitino Fileset catalog manages unstructured file metadata +
        storage location + access permissions (GVFS layer), not file content
        (§6.3). File content is read by SeaTunnel, not Gravitino.

        Args:
            catalog_name: Catalog name (== DataSource api_name).
            provider: Gravitino 1.3.0 fileset catalog provider — always
                "fileset" (live-verified). The storage backend (S3/HDFS/etc.)
                is determined by the `location` scheme, not the provider name.
            properties: Provider-specific config. location uses s3a:// for
                S3-compatible stores (MinIO/RustFS/OSS), hdfs:// for HDFS:
                {"location": "s3a://bucket/path", "s3-endpoint": "...",
                 "s3-access-key-id": "...", "s3-secret-access-key": "..."}
        """
        await self._register_typed_catalog(
            catalog_name=catalog_name,
            catalog_type="fileset",
            provider=provider,
            properties=properties,
            comment=f"Gaia file/object storage source: {catalog_name}",
        )

    async def remove_catalog(self, catalog_name: str, force: bool = True) -> None:
        """Remove a Gravitino Catalog (when DataSource is deleted).

        Args:
            catalog_name: Name of the catalog to remove.
            force: If True, attach ?force=true to delete schemas/tables
                   under the catalog. Default True per Gaia's lifecycle
                   semantics (cascading delete).

        Gravitino's default behavior (force=False) requires the catalog to
        have no schemas and be disabled. Since our JDBC catalogs are auto-
        discovered with schemas, we always use force=True.
        """
        params = "?force=true" if force else ""
        url = f"{self._metalake_path()}/catalogs/{catalog_name}{params}"
        try:
            response = await self.client.delete(url)
            if response.status_code == 404:
                return  # Already deleted or never registered
            response.raise_for_status()
        except Exception as exc:
            raise GravitinoUnavailableError(f"Failed to remove catalog '{catalog_name}': {exc}") from exc

    async def list_catalogs(self) -> list[dict[str, Any]]:
        """List all registered catalogs in the ontology metalake."""
        url = f"{self._metalake_path()}/catalogs"
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            data: dict[str, object] = response.json()
            identifiers: list[dict[str, Any]] = data.get("identifiers", [])  # type: ignore[assignment]
            return identifiers
        except Exception as exc:
            raise GravitinoUnavailableError(f"Gravitino API error: {exc}") from exc

    # ── Table Route Resolution ──

    async def get_table_columns(
        self,
        catalog: str,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """Get column metadata for a table via Gravitino REST API.

        Bypasses the Trino connector's type-conversion layer — queries
        Gravitino directly, which can handle PostgreSQL-specific types
        (jsonb, uuid, inet, etc.) as ExternalType rather than failing.

        Returns:
            List of column dicts with keys: name, type, nullable,
            auto_increment, default_value, comment

        Raises:
            NotFoundError: If the table doesn't exist.
        """
        table_data = await self._get_table_payload(catalog, schema, table)
        columns_raw: object = table_data.get("columns", [])
        if not isinstance(columns_raw, list):
            return []
        columns: list[dict[str, Any]] = []
        for item in columns_raw:
            if isinstance(item, dict):
                columns.append(item)
        return columns

    async def get_table_comment(
        self,
        catalog: str,
        schema: str,
        table: str,
    ) -> str:
        """Get the table-level comment via Gravitino REST API.

        The Trino connector's ``DESCRIBE`` only returns column comments;
        table comments require a direct Gravitino REST call.

        Returns:
            The table comment string, or "" if none / unavailable.
        """
        try:
            table_data = await self._get_table_payload(catalog, schema, table)
        except Exception:
            return ""
        comment = table_data.get("comment", "")
        return str(comment) if comment else ""

    async def get_table_metadata(
        self,
        catalog: str,
        schema: str,
        table: str,
    ) -> dict[str, Any]:
        """Fetch full table metadata in one REST call.

        Returns a dict with keys:
          - ``columns``: list of column dicts (same shape as ``get_table_columns``)
          - ``indexes``: list of index dicts (PRIMARY_KEY entries expose PK columns)
          - ``comment``: table-level comment string

        Single round-trip variant of ``get_table_columns`` +
        ``get_table_comment`` + index lookup. Use this when a caller needs
        more than one of those (e.g. ``describe_table`` wants columns +
        PK + comment) to avoid 3 separate REST calls.

        Raises:
            NotFoundError: If the table doesn't exist.
            GravitinoUnavailableError: On transport errors.
        """
        payload = await self._get_table_payload(catalog, schema, table)
        columns_raw: object = payload.get("columns", [])
        columns: list[dict[str, Any]] = (
            [item for item in columns_raw if isinstance(item, dict)] if isinstance(columns_raw, list) else []
        )
        indexes_raw: object = payload.get("indexes", [])
        indexes: list[dict[str, Any]] = (
            [item for item in indexes_raw if isinstance(item, dict)] if isinstance(indexes_raw, list) else []
        )
        comment = payload.get("comment", "")
        return {
            "columns": columns,
            "indexes": indexes,
            "comment": str(comment) if comment else "",
        }

    async def get_table_indexes(
        self,
        catalog: str,
        schema: str,
        table: str,
    ) -> list[dict[str, Any]]:
        """Get index metadata (incl. PRIMARY_KEY) via Gravitino REST API.

        Gravitino exposes table indexes (primary key, unique, etc.) in the
        table payload's ``indexes`` field — the Trino connector's DESCRIBE
        does not surface this. Each entry has ``indexType`` (e.g.
        ``PRIMARY_KEY``), ``name``, and ``fieldNames`` (list of column-name
        paths, e.g. ``[["modelId"]]``).

        Returns:
            List of index dicts; empty list if none / unavailable.
        """
        try:
            payload = await self._get_table_payload(catalog, schema, table)
        except Exception:
            return []
        indexes_raw: object = payload.get("indexes", [])
        if not isinstance(indexes_raw, list):
            return []
        return [item for item in indexes_raw if isinstance(item, dict)]

    async def _get_table_payload(
        self,
        catalog: str,
        schema: str,
        table: str,
    ) -> dict[str, Any]:
        """Fetch the raw ``table`` payload from Gravitino REST.

        Shared by ``get_table_columns`` and ``get_table_comment``. Returns
        the inner ``table`` dict from the Gravitino response.

        Raises:
            NotFoundError: If the table doesn't exist.
            GravitinoUnavailableError: On transport errors.
        """
        url = f"{self._metalake_path()}/catalogs/{catalog}/schemas/{schema}/tables/{table}"
        try:
            response = await self.client.get(url)
            if response.status_code == 404:
                raise NotFoundError("Table", f"{catalog}.{schema}.{table}")
            response.raise_for_status()
            data: dict[str, object] = response.json()
            table_data = data.get("table", {})
            if isinstance(table_data, dict):
                return table_data
            return {}
        except NotFoundError:
            raise
        except Exception as exc:
            raise GravitinoUnavailableError(f"Gravitino API error: {exc}") from exc

    @staticmethod
    def _format_gravitino_column_type(col_type: object) -> str:
        """Convert a Gravitino column type (dict or string) to a display string.

        Gravitino represents types as either:
        - Simple string: "varchar(255)", "integer", "long"
        - Complex dict: {"type": "external", "catalogString": "jsonb"}
        """
        if isinstance(col_type, str):
            return col_type
        if isinstance(col_type, dict):
            type_name = col_type.get("type", "unknown")
            if type_name == "external":
                return str(col_type.get("catalogString", type_name))
            return str(type_name)
        return str(col_type)

    async def resolve_backing_table(
        self,
        object_type_api_name: str,
    ) -> dict[str, str]:
        """Resolve an object type to its physical table location.

        Returns:
            Dict with keys: catalog, schema, table

        For MANAGED object types the physical Iceberg table follows the
        platform convention ``iceberg_catalog.ontology.<api_name>`` (see
        OntologyService.define_object_type_batch — Gravitino registers the
        dataset under that exact catalog/schema/name). We return that
        convention directly instead of querying a Gravitino location
        endpoint, which does not exist in Gravitino (1.2.0/1.3.0). If a future
        authorizer/locator is wired, the Gravitino REST probe below can be
        re-enabled; for now it is bypassed to avoid NoSuchEndpoint errors
        breaking every physical query.

        Raises:
            NotFoundError: Only if an explicit Gravitino lookup is configured
            and returns 404 (kept for future use).
        """
        return {
            "catalog": "iceberg",
            "schema": "ontology",
            # The Iceberg table is named after the dataset api_name
            # (managed_dataset_api_name = snake_case of the ObjectType
            # api_name), NOT the raw PascalCase api_name. Trino lowercases
            # identifiers, so 'SalesConsultant' would become 'salesconsultant'
            # (no underscore) and miss the actual 'sales_consultant' table.
            "table": managed_dataset_api_name(object_type_api_name.split(".", 1)[-1]),
        }
