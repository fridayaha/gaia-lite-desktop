"""pydantic v2 schemas for DataSource, Credential, SyncTask, Dataset management.

These schemas correspond to the data layer domain models defined in
docs/data-layer-design.md §2.

Store in PG (via PostgresMetaStore):
  - DataSource — external system connection configuration
  - Credential — authentication secrets
  - SyncTask — sync job orchestration metadata
  - Dataset — Iceberg table governance metadata (PG mirror)

Exploration models are read-only and not persisted.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from ontology.core.naming import DATASET_API_NAME_PATTERN

# ═══════════════════════════════════════════════════════════════════
# Credential
# ═══════════════════════════════════════════════════════════════════


class Credential(BaseModel):
    """Authentication credential for external data sources.

    TODO(SEC-001): AES-256-GCM encrypt secret_data at rest.
    API responses NEVER return secret_data — serialized as "***".
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    credential_type: str  # "username_password" | "access_key" | "token"
    secret_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    # Note: no updated_at — credentials are replaced (DELETE + CREATE), never modified


class CredentialCreate(BaseModel):
    """Create a new credential — secret_data accepted in request body."""

    api_name: str = Field(..., pattern=DATASET_API_NAME_PATTERN)
    credential_type: str
    secret_data: dict[str, Any]


class CredentialResponse(BaseModel):
    """Credential in API response — secret_data masked."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    credential_type: str
    secret_data: str = "***"  # always masked
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# DataSource
# ═══════════════════════════════════════════════════════════════════


# ── 连接器品类与能力模型 ──
# 参照 Palantir Foundry Data Connection 的 capability 模型（Batch syncs /
# Streaming syncs / CDC / Virtual tables / Exploration）。见
# docs/design/multi-source-data-fusion-design.md §五、§八。
#
# virtual_table 能力表示该品类技术上可走 VIRTUAL 联邦（Gravitino 纳管 +
# Trino 下推），但不强制——用户创建对象时仍可选 MANAGED 落地。
# 品类一刀切策略见 §8.1：NoSQL/时序/SaaS/文件存储一律 MANAGED 落地。

ConnectorCategory = Literal[
    "relational",  # 关系型数据库（含国产库）
    "lakehouse",  # 湖仓格式（Hive/Delta/Hudi/Paimon/Iceberg）
    "file_object",  # 文件与对象存储（S3/MinIO/OSS/HDFS）
    "messaging",  # 消息队列（Kafka）
    "nosql",  # NoSQL（Elasticsearch）
    "cloud_warehouse",  # 中国云数仓（ADB-PG/GaussDB-DWS）
    "generic",  # 通用 JDBC 兜底
]

Capability = Literal[
    "explore",  # 探索 schema
    "batch_sync",  # 批量同步
    "cdc",  # 增量 CDC
    "virtual_table",  # VIRTUAL 联邦不落地
    "streaming_sync",  # 流式同步
    "file_sync",  # 文件同步
]

# capability 映射表（由 connector_type 推断，不存 DB）
CAPABILITY_MAP: dict[str, list[str]] = {
    # ── 关系型数据库（含国产库）──
    "mysql": ["explore", "batch_sync", "cdc", "virtual_table"],
    "mariadb": ["explore", "batch_sync", "cdc", "virtual_table"],
    "postgresql": ["explore", "batch_sync", "cdc", "virtual_table"],
    "postgres": ["explore", "batch_sync", "cdc", "virtual_table"],
    # 国产库（G2 成熟稳定：均有 SeaTunnel 原生 CDC 或厂商通道）
    "opengauss": ["explore", "batch_sync", "cdc", "virtual_table"],
    "gaussdb": ["explore", "batch_sync", "cdc", "virtual_table"],
    "tidb": ["explore", "batch_sync", "cdc", "virtual_table"],
    "oceanbase": ["explore", "batch_sync", "virtual_table"],  # CDC 走 OMS
    "starrocks": ["explore", "batch_sync", "virtual_table"],  # MySQL 协议 OLAP
    "dameng": ["explore", "batch_sync"],  # 无 Gravitino provider，仅落地
    "kingbase": ["explore", "batch_sync", "virtual_table"],
    # 通用 JDBC 兜底（任意 JDBC 兼容库，无 Gravitino catalog，仅落地）
    "generic_jdbc": ["explore", "batch_sync"],
    # ── 湖仓格式（联邦源为主）──
    "iceberg": ["explore", "virtual_table"],
    "hive": ["explore", "batch_sync", "virtual_table"],
    "delta": ["explore", "virtual_table"],
    "hudi": ["explore", "virtual_table"],
    "paimon": ["explore", "virtual_table"],
    # ── 文件与对象存储（只能 MANAGED 落地）──
    "s3": ["explore", "file_sync"],
    "minio": ["explore", "file_sync"],
    "oss": ["explore", "file_sync"],
    "hdfs": ["explore", "file_sync"],
    # ── 消息队列 ──
    "kafka": ["explore", "streaming_sync", "virtual_table"],
    # ── NoSQL（严格一刀切，一律落地）──
    "elasticsearch": ["explore", "batch_sync"],
    # ── 中国云数仓（PG 内核，复用 PG 通道）──
    "analyticdb_pg": ["explore", "batch_sync", "virtual_table"],
    "gaussdb_dws": ["explore", "batch_sync", "virtual_table"],
    "maxcompute": ["explore", "batch_sync"],  # 独立 JDBC，无 VIRTUAL（路标）
}


# lite 桌面版能力映射（B4）：只支持四类源（postgres/mysql/csv/sqlite），能力收窄为
# explore + virtual_table（联邦查询）。无 cdc/batch_sync/streaming/file_sync——桌面版
# 不做托管表落地、CDC、流式同步（红线下砍掉）。lite 版 DataSourceService._compute_capabilities
# 用此表；full 版仍用上方 CAPABILITY_MAP（国产库/湖仓/对象存储全保留）。
LITE_CAPABILITY_MAP: dict[str, list[str]] = {
    "postgresql": ["explore", "virtual_table"],
    "postgres": ["explore", "virtual_table"],
    "mysql": ["explore", "virtual_table"],
    "mariadb": ["explore", "virtual_table"],
    "csv": ["explore", "virtual_table"],
    "csv_file": ["explore", "virtual_table"],
    "sqlite": ["explore", "virtual_table"],
}


# Sensitive keys in connector_config that must never appear in API responses
_SENSITIVE_CONNECTOR_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "access_key",
        "secret_key",
        "token",
        "api_key",
        "private_key",
        "keytab",
        "keystore_password",
        "truststore_password",
    }
)

_MASK_VALUE = "***"


def _sanitize_connector_config(config: dict[str, Any]) -> dict[str, Any]:
    """Replace sensitive values in connector_config with '***'."""
    if not config:
        return config
    return {k: _MASK_VALUE if k.lower() in _SENSITIVE_CONNECTOR_KEYS else v for k, v in config.items()}


class DataSource(BaseModel):
    """External data source instance.

    connector_type maps directly to SeaTunnel Source connector names
    and Gravitino provider names (jdbc-mysql, jdbc-postgresql, etc.).
    capabilities is a derived field computed from connector_type at
    serialize time — not stored in the database.

    Sensitive connector_config values (password, access_key, etc.) are
    automatically masked to '***' on serialization (via field_serializer),
    while remaining unmasked in-memory for internal use.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    display_name: str
    description: str = ""
    connector_type: str  # "mysql" | "postgresql" | "s3" | "kafka" | ...
    connector_config: dict[str, Any] = Field(default_factory=dict)
    credential_id: str | None = None
    status: str = "DISCONNECTED"  # CONNECTED | DISCONNECTED | ERROR
    gravitino_catalog_name: str = ""
    capabilities: list[str] = Field(default_factory=list)  # derived from connector_type
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _compute_capabilities(self) -> "DataSource":
        """自动从 connector_type 推导 capabilities，不依赖调用方手动设置。"""
        if not self.capabilities and self.connector_type:
            type_lower = self.connector_type.lower()
            self.capabilities = CAPABILITY_MAP.get(type_lower, ["explore"])
        return self

    @field_serializer("connector_config")
    def _mask_sensitive_fields(self, value: dict[str, Any]) -> dict[str, Any]:
        """Mask sensitive connector_config values on serialization.

        This only affects JSON serialization (API responses, model_dump).
        The in-memory DataSource object retains unmasked values for
        internal use (JDBC URL construction, SeaTunnel config, etc.).
        """
        return _sanitize_connector_config(value)


class DataSourceCreate(BaseModel):
    """Create a new data source."""

    api_name: str = Field(..., pattern=DATASET_API_NAME_PATTERN)
    display_name: str
    description: str = ""
    connector_type: str
    connector_config: dict[str, Any] = Field(default_factory=dict)
    credential_id: str | None = None


class DataSourceUpdate(BaseModel):
    """Partial update for data source."""

    display_name: str | None = None
    description: str | None = None
    connector_config: dict[str, Any] | None = None
    credential_id: str | None = None


class ConnectionTestResult(BaseModel):
    """Result of a data source connection test."""

    success: bool
    message: str
    details: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════════════════
# SyncTask
# ═══════════════════════════════════════════════════════════════════


class SyncTask(BaseModel):
    """Data sync job — DataSource → Iceberg Dataset.

    One SyncTask maps to one SeaTunnel MAIN pipeline.
    sync_type = "table" uses JDBC Source; "file" uses S3/HDFS Source.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    data_source_id: str
    sync_type: str = "table"  # "table" | "file"
    source_config: dict[str, Any] = Field(default_factory=dict)
    target_dataset_api_name: str
    sync_mode: str = "full_snapshot"  # full_snapshot | incremental
    transaction_type: str = "snapshot"  # snapshot | append
    allow_schema_changes: bool = False
    max_duration_minutes: int | None = None
    file_filters: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None
    status: str = "DRAFT"  # DRAFT | RUNNING | FINISHED | STOPPED | CANCELED | FAILED
    pipeline_name: str | None = None
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SyncTaskCreate(BaseModel):
    """Create a new sync task."""

    # SyncTask api_name is an ops resource identifier (snake_case, like
    # Dataset api_name), not a business camelCase identifier — see naming.py
    # (DATASET_API_NAME_PATTERN). Internal generators also produce snake_case
    # (e.g. start_cdc_sync uses `cdc{table}` / `ts{hypertable}`).
    api_name: str = Field(..., pattern=DATASET_API_NAME_PATTERN)
    data_source_id: str = ""  # Set by the service from URL path
    sync_type: str = "table"  # "table" | "file"
    source_config: dict[str, Any] = Field(default_factory=dict)
    target_dataset_api_name: str
    sync_mode: str = "full_snapshot"
    transaction_type: str = "snapshot"
    allow_schema_changes: bool = False
    max_duration_minutes: int | None = None
    file_filters: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None


class SyncTaskUpdate(BaseModel):
    """Partial update for sync task."""

    source_config: dict[str, Any] | None = None
    sync_mode: str | None = None
    transaction_type: str | None = None
    allow_schema_changes: bool | None = None
    max_duration_minutes: int | None = None
    file_filters: dict[str, Any] | None = None
    schedule: dict[str, Any] | None = None


# ═══════════════════════════════════════════════════════════════════
# Dataset (governance metadata, stored in PG — distinct from
# Iceberg DatasetSchema which describes the physical table)
# ═══════════════════════════════════════════════════════════════════


class DatasetGovernance(BaseModel):
    """Platform-level Dataset metadata stored in PG.

    Complements the Iceberg-level Dataset / DatasetSchema which
    describe the physical table structure. This model tracks:
      - Resource kind (MANAGED table on Iceberg vs VIRTUAL external proxy)
      - Lineage (source_dataset_api_name → this dataset)
      - External origin (data_source_api_name for synced datasets)
      - is_view flag (Managed Table Foundry-View subtype marker; currently
        always False — see dataset-ontology-binding.md §1.4)
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    display_name: str = ""
    storage_location: str = ""
    partition_config: dict[str, Any] | None = None
    source_dataset_api_name: str | None = None
    data_source_api_name: str | None = None
    kind: Literal["MANAGED", "VIRTUAL"] = "MANAGED"
    is_view: bool = False
    row_count_estimate: int | None = None
    created_at: datetime
    updated_at: datetime


class DatasetGovernanceCreate(BaseModel):
    """Register a new Dataset in PG metadata."""

    api_name: str = Field(..., pattern=DATASET_API_NAME_PATTERN)
    display_name: str = ""
    storage_location: str = ""
    partition_config: dict[str, Any] | None = None
    source_dataset_api_name: str | None = None
    data_source_api_name: str | None = None
    kind: Literal["MANAGED", "VIRTUAL"] = "MANAGED"
    is_view: bool = False


class PaginatedDatasets(BaseModel):
    """Paginated dataset list response.

    Wraps the page of items with total count + pagination metadata so the
    client can render a pager without a second round-trip.
    """

    items: list[DatasetGovernance]
    total: int
    page: int
    page_size: int


# ═══════════════════════════════════════════════════════════════════
# Impact Analysis
# ═══════════════════════════════════════════════════════════════════


class ImpactItem(BaseModel):
    """A single affected resource in an impact analysis."""

    resource_type: str  # "sync_task" | "object_type" | "dataset" | "link_type"
    api_name: str
    effect: str  # "CASCADE_DELETE" | "SET_NULL" | "ORPHANED"


class ImpactAnalysis(BaseModel):
    """Result of an impact analysis for a destructive operation."""

    severity: str  # LOW | MEDIUM | HIGH
    action: str  # "delete"
    target_api_name: str
    target_type: str  # "datasource" | "dataset" | "sync_task"
    impacts: list[ImpactItem] = Field(default_factory=list)


class ImpactAnalysisRequest(BaseModel):
    """Request impact analysis for a proposed operation."""

    target_api_name: str
    target_type: str  # "datasource" | "dataset" | "sync_task"
    action: str  # "delete"


# ═══════════════════════════════════════════════════════════════════
# Exploration (read-only, not persisted)
# ═══════════════════════════════════════════════════════════════════


class ExploreRequest(BaseModel):
    """Request to explore a data source's schema."""

    database: str = ""  # empty = use DataSource default database
    table_pattern: str = ""  # optional LIKE pattern


class VirtualTableRegistration(BaseModel):
    """Request body for registering an external table as a Virtual Table.

    The external table (DataSource.database.table) is registered as a
    kind=VIRTUAL DatasetGovernance record pointing at the table via a
    three-part storage_location (catalog.schema.table). No physical
    table is created — Trino federates to the source at query time.
    See dataset-ontology-binding.md §3.2.
    """

    database: str
    table: str
    api_name: str | None = None  # defaults to the table name
    display_name: str = ""


class ColumnInfo(BaseModel):
    """Column metadata from data source exploration."""

    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    comment: str = ""


class TableInfo(BaseModel):
    """Table/collection metadata from data source exploration."""

    name: str
    schema_: str = Field(default="", alias="schema")
    row_count_estimate: int | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    comment: str = ""


class ExploreResult(BaseModel):
    """Full exploration result for a database/schema."""

    database: str
    tables: list[TableInfo] = Field(default_factory=list)
