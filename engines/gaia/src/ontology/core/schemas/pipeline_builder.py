"""pydantic v2 schemas for Pipeline Builder (Pipeline IR + API DTOs).

Design follows ADR-018 D9 (Palantir Deploy/Build separation, Schedule
as independent resource, Release Stage).  The IR types defined here are
the engine-agnostic logical representation — they are NOT coupled to
Kestra, Trino, or any execution engine (ADR-018 D2).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ═══════════════════════════════════════════════════════════════════
# Pipeline IR — Node definitions
# ═══════════════════════════════════════════════════════════════════


class NodePort(BaseModel):
    """A node's input or output port descriptor."""

    id: str
    label: str = ""
    schema_field: str | None = None  # Linked field name in the schema


class SchemaField(BaseModel):
    """A single field in a schema."""

    name: str
    data_type: str  # STRING | INTEGER | DECIMAL | BOOLEAN | TIMESTAMP | DATE | JSON | VECTOR | ...
    nullable: bool = True
    description: str = ""
    primary_key: bool = False


class Schema(BaseModel):
    """Input or output schema for an IR node."""

    fields: list[SchemaField] = Field(default_factory=list)


class InputContract(BaseModel):
    """Input constraints for an IR node.

    Min/max ports and field-level requirements.  Evaluated by the
    SchemaInferenceEngine during validation.
    """

    min_inputs: int = 1
    max_inputs: int = 1
    required_fields: list[str] = Field(default_factory=list)
    field_type_requirements: dict[str, list[str]] = Field(default_factory=dict)


class QualityRule(BaseModel):
    """A data quality rule attached to a node.

    Severity: ERROR (blocks build) | WARNING (flags but doesn't block)
    | SPLIT (diverts bad rows to an exception dataset).
    """

    rule_type: Literal["not_null", "unique", "range", "regex", "expression"]
    field: str  # Target field
    config: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["ERROR", "WARNING", "SPLIT"] = "ERROR"
    message: str = ""


class JoinCondition(BaseModel):
    """单个 JOIN 关联条件（等值连接）：左表列 = 右表列。"""

    left_column: str
    right_column: str


class SortKey(BaseModel):
    """排序键：列名 + 方向。"""

    column: str
    direction: Literal["ASC", "DESC"] = "ASC"


class FilterCondition(BaseModel):
    """结构化过滤条件（替代手写 SQL WHERE）。

    用列名 + 操作符 + 值表达常见过滤，避免用户手写 SQL 导致的注入与拼写错误。
    复杂条件仍可用 NodeConfig.expression（高级模式）。"""

    column: str
    operator: Literal[
        "eq", "neq", "gt", "gte", "lt", "lte",
        "in", "not_in", "is_null", "is_not_null",
        "contains", "not_contains", "starts_with", "ends_with",
    ]
    value: Any | None = None
    # 多值操作符 (in/not_in) 的值列表
    values: list[Any] | None = None


class NodeConfig(BaseModel):
    """Configuration for an IR node — type-specific fields.

    Concrete fields vary by node_type (see OperatorSpec.config_schema).
    The config field is kept as a generic dict and validated by the
    SchemaInferenceEngine's per-operator validate_config function.
    """

    # Expression/condition: used by Filter, Expression, QualityCheck
    expression: str | None = None
    # Columns to select / rename / drop: used by Select, Rename, Drop, Deduplicate
    columns: list[str] | None = None
    # Column renames {old: new}: used by Rename
    column_mapping: dict[str, str] | None = None
    # Target data type: used by TypeCast
    target_type: str | None = None
    # Cast columns (TypeCast): list of {column, target_type} for multi-column cast
    cast_columns: list[dict[str, str]] | None = None
    # Join type: used by Join
    join_type: Literal["INNER", "LEFT", "RIGHT", "FULL"] | None = None
    # Join condition: used by Join
    join_condition: str | None = None
    # Structured join conditions (preferred over join_condition string):
    # list of {left_column, right_column} for equi-joins.
    join_conditions: list[JoinCondition] | None = None
    # Group-by fields: used by Aggregate
    group_by: list[str] | None = None
    # Aggregations: used by Aggregate
    aggregations: list[dict[str, str]] | None = None  # [{field, function, alias}, ...]
    # Sort keys (Sort): list of {column, direction} — preferred over `columns`.
    sort_keys: list[SortKey] | None = None
    # Structured filter conditions (Filter): preferred over `expression` string.
    filter_conditions: list[FilterCondition] | None = None
    # Type-specific config for GenericKestraTask (raw config passed to Kestra)
    kestra_task_type: str | None = None
    kestra_task_config: dict[str, Any] | None = None
    # Type-specific config for QualityCheck
    quality_rules: list[QualityRule] | None = None
    # Any other config fields (extensible)
    extra: dict[str, Any] = Field(default_factory=dict)


class IRNode(BaseModel):
    """A single node in the Pipeline IR DAG."""

    id: str
    type: Literal[
        "Source",
        "Transform",
        "Sink",
        "QualityCheck",
        "GenericKestraTask",
    ]
    # Concrete operator type (e.g. "Filter", "Join", "Aggregate", "GenericKestraTask").
    # Used by SchemaInferenceEngine to look up inference rules in the registry.
    operator_type: str = ""
    # Human-readable label (derived from type + config, user-editable)
    label: str = ""
    description: str = ""
    # Input port schemas (0 for Source, 1~N for others)
    input_schemas: list[Schema] = Field(default_factory=list)
    # Output schema (inferred by SchemaInferenceEngine; used as cache)
    output_schema: Schema | None = None
    # Node config
    config: NodeConfig = Field(default_factory=NodeConfig)
    # Visual state (position on canvas — purely frontend hint, not part of IR logic)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


class IREdge(BaseModel):
    """A connection between two IR nodes."""

    id: str
    source_id: str
    target_id: str
    source_port: str = "default"
    target_port: str = "default"


class PipelineIR(BaseModel):
    """Complete Pipeline IR — the engine-agnostic logical DAG.

    This is the single source of truth for pipeline logic.  Everything
    else (Kestra Flow, DuckDB SQL, Trino queries) is derived from it
    via the KestraEngine translator.
    """

    nodes: list[IRNode] = Field(default_factory=list)
    edges: list[IREdge] = Field(default_factory=list)
    # Pipeline-level metadata (execution attributes, governance)
    write_mode: Literal["FULL_REFRESH", "APPEND"] = "FULL_REFRESH"
    trigger_index_sync: bool = False
    tags: list[str] = Field(default_factory=list)
    owner: str | None = None
    business_domain: str | None = None


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Pipeline CRUD
# ═══════════════════════════════════════════════════════════════════


class PipelineCreate(BaseModel):
    """Create pipeline request body."""

    api_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str
    description: str = ""
    write_mode: Literal["FULL_REFRESH", "APPEND"] = "FULL_REFRESH"
    sink_dataset_api_name: str
    graph: PipelineIR = Field(default_factory=PipelineIR)
    change_summary: str = ""


class PipelineUpdate(BaseModel):
    """Update pipeline request body (partial)."""

    display_name: str | None = None
    description: str | None = None
    write_mode: Literal["FULL_REFRESH", "APPEND"] | None = None
    sink_dataset_api_name: str | None = None
    graph: PipelineIR | None = None
    change_summary: str | None = None


class PipelineResponse(BaseModel):
    """Pipeline response (current version metadata)."""

    api_name: str
    display_name: str
    description: str = ""
    status: Literal["DRAFT", "PUBLISHED", "DEPRECATED", "ARCHIVED"] = "DRAFT"
    current_version_id: str | None = None
    current_version_number: int | None = None
    write_mode: Literal["FULL_REFRESH", "APPEND"] = "FULL_REFRESH"
    sink_dataset_api_name: str
    owner_id: str | None = None
    project_id: str | None = None
    deleted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PipelineListResponse(BaseModel):
    """List response with pagination metadata."""

    items: list[PipelineResponse]
    total: int
    offset: int = 0
    limit: int = 20


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Pipeline Versions
# ═══════════════════════════════════════════════════════════════════


class PipelineVersionResponse(BaseModel):
    """Pipeline version response (includes IR + inferred schema)."""

    id: str
    pipeline_id: str
    version_number: int
    graph: PipelineIR
    inferred_schema: dict[str, Any] | None = None
    change_summary: str = ""
    created_by: str | None = None
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Deploy & Build
# ═══════════════════════════════════════════════════════════════════


class DeployRequest(BaseModel):
    """Deploy request — make a pipeline version the active logical definition.

    Translates the IR to Kestra Flow YAML and registers/triggers in Kestra.
    Does NOT trigger a data build.
    """

    version_id: str | None = None  # Defaults to current_version_id
    force: bool = False  # Force re-deploy even if unchanged


class DeployResponse(BaseModel):
    """Deploy response."""

    api_name: str
    status: Literal["DRAFT", "PUBLISHED", "DEPRECATED", "ARCHIVED"] = "PUBLISHED"
    deployed_version_id: str
    deployed_version_number: int
    kestra_flow_id: str | None = None
    kestra_namespace: str | None = None
    deployed_at: datetime


class BuildRequest(BaseModel):
    """Build (execution) request — materialise data.

    Based on Palantir CreateBuildRequest: force_build, retry_count,
    timeout_minutes, abort_on_failure.
    """

    version_id: str | None = None  # Defaults to current_version_id
    force_build: bool = False
    retry_count: int = 3
    retry_backoff_seconds: int = 60
    timeout_minutes: int = 120
    abort_on_failure: bool = True
    idempotency_key: str | None = None


class BuildResponse(BaseModel):
    """Build response (202 Accepted) — also used for list views.

    Includes optional started_at/finished_at/duration_ms/error_message so
    the builds list can render timing + errors without fetching each
    build's detail (avoids N+1 detail fetches).
    """

    build_id: str
    pipeline_api_name: str
    version_id: str
    version_number: int
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "CANCELLED"] = "PENDING"
    trigger_type: Literal["MANUAL", "SCHEDULE", "UPSTREAM_EVENT"] = "MANUAL"
    triggered_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Build monitoring
# ═══════════════════════════════════════════════════════════════════


class NodeRunResponse(BaseModel):
    """Per-node execution details within a build."""

    node_id: str
    node_type: str
    engine: str | None = None
    status: Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "SKIPPED"] = "PENDING"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    attempt: int = 1
    rows_in: int | None = None
    rows_out: int | None = None
    bytes_processed: int | None = None


class StateHistoryResponse(BaseModel):
    """State transition record."""

    from_state: str | None = None
    to_state: str
    reason: str | None = None
    changed_by: str | None = None
    changed_at: datetime


class BuildDetailResponse(BaseModel):
    """Full build detail (with node runs and state history)."""

    build_id: str
    pipeline_api_name: str
    version_id: str
    version_number: int
    status: str
    trigger_type: str
    triggered_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None
    output_snapshot_id: str | None = None
    execution_meta: dict[str, Any] = Field(default_factory=dict)
    node_runs: list[NodeRunResponse] = Field(default_factory=list)
    state_history: list[StateHistoryResponse] = Field(default_factory=list)
    created_at: datetime


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Validation
# ═══════════════════════════════════════════════════════════════════


class ContractViolation(BaseModel):
    """A single contract violation found during validation."""

    node_id: str = ""  # Empty when created inside validate_config; filled by engine at DAG level
    valid: bool = True
    level: Literal["ERROR", "WARNING", "INFO"] = "ERROR"
    message: str = ""


class ValidationResponse(BaseModel):
    """Pipeline IR validation + Schema inference result."""

    valid: bool
    inferred_schema: Schema | None = None
    contracts: list[ContractViolation] = Field(default_factory=list)
    # 每个节点的输出 Schema（node_id → Schema）。
    # 供前端配置面板渲染列下拉（Join 条件、Select 列选择、Aggregate 分组等）。
    # 键为节点 id，值为该节点推导出的输出 Schema；推导失败的节点值为空 Schema。
    node_schemas: dict[str, Schema] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Schedules
# ═══════════════════════════════════════════════════════════════════


class TriggerConfig(BaseModel):
    """Trigger configuration JSON.

    MVP supports single trigger types:
      - time: {"type": "time", "cron": "0 9 * * 1-5", "tz": "Asia/Shanghai"}
      - webhook: {"type": "webhook", "key": "orders-arrived"}
    Phase 2 reserves AND/OR nesting.
    """

    type: Literal["time", "webhook"]
    cron: str | None = None  # For type=time
    tz: str | None = "UTC"
    key: str | None = None  # For type=webhook
    # Phase 2: nested triggers for AND/OR


class ActionConfig(BaseModel):
    """Build action config for a schedule."""

    force_build: bool = False
    retry_count: int = 3
    timeout_minutes: int = 120
    abort_on_failure: bool = True


class ScheduleCreate(BaseModel):
    """Create schedule request."""

    api_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]*$")
    display_name: str = ""
    trigger: TriggerConfig
    action_config: ActionConfig = Field(default_factory=ActionConfig)
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    """Update schedule request (partial)."""

    display_name: str | None = None
    trigger: TriggerConfig | None = None
    action_config: ActionConfig | None = None
    enabled: bool | None = None


class ScheduleResponse(BaseModel):
    """Schedule response."""

    id: str
    pipeline_api_name: str
    api_name: str
    display_name: str = ""
    trigger: TriggerConfig
    action_config: ActionConfig = Field(default_factory=ActionConfig)
    enabled: bool = True
    created_by: str | None = None
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Operators catalog
# ═══════════════════════════════════════════════════════════════════


class OperatorSpecResponse(BaseModel):
    """Operator specification for the catalog."""

    type: str
    category: Literal["source", "transform", "sink", "quality", "kestra"]
    display_name: str
    description: str = ""
    input_ports: int = 1
    output_ports: int = 1
    config_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema_rule: str = ""


class KestraPluginResponse(BaseModel):
    """Kestra plugin descriptor (proxy from Kestra /plugins endpoint)."""

    type: str
    display_name: str = ""
    category: str = "script"
    no_code_schema: dict[str, Any] = Field(default_factory=dict)


class OperatorCatalogResponse(BaseModel):
    """Full operator catalog response."""

    operators: list[OperatorSpecResponse] = Field(default_factory=list)
    kestra_plugins: list[KestraPluginResponse] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# API DTOs — Error response
# ═══════════════════════════════════════════════════════════════════


class PipelineErrorResponse(BaseModel):
    """Standard error response (Gaia convention)."""

    detail: str
    error_type: str
    code: str
