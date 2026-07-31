"""Domain-specific exception hierarchy.

All custom exceptions inherit from OntologyError — never raise raw Exception.
"""


class OntologyError(Exception):
    """Base exception for all domain errors.

    An optional ``code`` may be attached so the tool layer can surface a
    stable, contract-specific error code to the LLM (e.g.
    ``INVALID_AGGREGATION``) instead of the generic ``ONTOLOGY_ERROR``.
    Subclasses pass through ``code``; callers raise
    ``OntologyError(msg, code="INVALID_FILTER")`` for validation-style
    failures that the design doc (ontology-tool-layer.md §5) names
    explicitly. See ``ToolExecutor._classify_error``.
    """

    def __init__(self, *args: object, code: str | None = None) -> None:
        self.code = code
        super().__init__(*args)


class NotFoundError(OntologyError):
    """Resource not found (HTTP 404)."""

    def __init__(self, resource_type: str, identifier: str) -> None:
        self.resource_type = resource_type
        self.identifier = identifier
        super().__init__(f"{resource_type} not found: {identifier}")


class ConflictError(OntologyError):
    """Data conflict, e.g. unique constraint or OCC failure (HTTP 409)."""


class ValidationError(OntologyError):
    """Input validation failure (HTTP 422)."""


class ForbiddenError(OntologyError):
    """Permission denied (HTTP 403)."""


class DorisUnavailableError(OntologyError):
    """Doris is unavailable — triggers fallback to Trino scan."""


class IcebergUnavailableError(OntologyError):
    """Iceberg store is unavailable — triggers fallback to Trino query."""


class GravitinoUnavailableError(OntologyError):
    """Gravitino is unavailable — physical tables bypass permission check."""


class TrinoUnavailableError(OntologyError):
    """Trino query engine itself is unreachable (server down / network refused).

    Distinct from DataSourceUnreachableError: here the *Trino/Gravitino*
    service is down, not the external data source. HTTP 503.
    """


class DataSourceUnreachableError(OntologyError):
    """The external data source backing a catalog is unreachable.

    Trino is up and ran the query, but the JDBC catalog couldn't dial the
    source DB (DNS failure / connection refused / timeout / auth rejected).
    Distinct from TrinoUnavailableError: the problem is the *data source*,
    not the query engine. HTTP 502.
    """


class CatalogNotRegisteredError(OntologyError):
    """The Gravitino catalog backing a data source is missing.

    Trino is up and the external DB is reachable, but the federated catalog
    (registered in Gravitino at data-source create time) no longer exists —
    typically because Gravitino was rebuilt/upgraded and its PG-backed
    catalog metadata was lost, or the catalog was manually removed.

    Distinct from DataSourceUnreachableError: here the *catalog registration*
    is gone (Gaia's bookkeeping is stale), not the source DB itself.
    Recoverable by re-registering the catalog via
    ``DataSourceService._register_datasource_catalog``. HTTP 502 with code
    ``CATALOG_NOT_REGISTERED``.
    """


class IndexNotBuiltError(OntologyError):
    """Doris index table does not exist for an ObjectType.

    Distinct from DorisUnavailableError: the Doris cluster is reachable,
    but no index table has been provisioned yet. Triggers a *normal*
    (info-level) Trino fallback rather than a *fault* (warning-level) one.
    """

    def __init__(self, object_type_api_name: str) -> None:
        self.object_type_api_name = object_type_api_name
        super().__init__(f"Index table not built for ObjectType: {object_type_api_name}")


class IndexProvisionError(OntologyError):
    """Index provisioning (DDL / sync pipeline) failed.

    Non-fatal: ObjectType creation/update must still succeed. The error is
    logged + counted so operators can retry; queries fall back to Trino
    until provisioning is retried successfully.
    """


class GraphUnavailableError(OntologyError):
    """Neo4j graph store is unavailable (server down / network refused).

    Graph-reasoning 特性 (graph-reasoning-design.md)。推理线 (query_with_dataframe)
    依赖图遍历；MVP 容错策略为整体失败 + 保留证据 (C13)，不降级。SQL 线
    (query_with_sql) 走 Doris/Trino，不受影响（两条线独立, C5/C12）。
    """


class GeoTimeUnavailableError(OntologyError):
    """PostGIS/TimescaleDB 时空层不可用。

    Graph-reasoning 特性。推理线空间/时序 filter 步骤依赖 GeoTimeStore；
    MVP 容错策略为整体失败 + 保留证据 (C13)。
    """


class EditionUnavailableError(OntologyError):
    """当前 edition 不支持该 Layer/能力。

    桌面版 lite 砍掉了 catalog/dataset/index/pipeline/engine(trino)/graph/
    geotime 等云版重依赖 Layer。container 对应 property 在 edition=="lite"
    下访问时抛此异常（而非 import 重依赖），让 lite 装配既类型安全又不在
    运行时悄悄拉起未安装的包。full 版永不触发。
    """


class OutboxError(OntologyError):
    """Outbox execution failed after all retries — moved to DLQ."""

    def __init__(self, outbox_id: str, error: str) -> None:
        self.outbox_id = outbox_id
        super().__init__(f"Outbox {outbox_id} permanently failed: {error}")


class ActionAlreadyExecutedError(OntologyError):
    """Duplicate action execution (idempotency key collision).

    Not an error per se — the caller should get back the cached result.
    """

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Action already executed with idempotency key: {idempotency_key}")
