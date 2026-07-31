"""pydantic v2 schemas for Action domain — validation/serialization.

These schemas define the API contract for creating ActionTypes and executing actions.
They are strictly separated from SQLAlchemy ORM models per Gaia coding standards.

Palantir alignment:
    - ActionTypeCreate ↔ Foundry Action Type definition
    - ActionExecutionRequest ↔ Foundry Action submission
    - ActionExecutionResult ↔ Foundry Action response (applied/conflict/failed)
    - Mutation ↔ Foundry OntologyEdit (P1, ADR-011)
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ontology.core.naming import PROPERTY_API_NAME_PATTERN
from ontology.core.schemas.ontology import DataType
from ontology.core.schemas.permission import Principal


class ActionTypeParameter(BaseModel):
    """Action parameter definition (Palantir ActionType.parameters equivalent).

    Palantir alignment: parameter definition with type, required, default,
    dynamic default sources, readonly/hidden flags, pattern constraints,
    enum values, and object references (Single Object / Object Set).
    """

    api_name: str = Field(..., pattern=PROPERTY_API_NAME_PATTERN)
    display_name: str
    data_type: DataType
    required: bool = True
    default: Any | None = None
    description: str = ""
    # Dynamic default value source (P1, ADR-011). When set to anything other
    # than "static", `default` is ignored and the value is resolved from the
    # ActionContext at execution time.
    default_source: Literal[
        "static",
        "current_user",
        "current_timestamp",
        "workspace_id",
        "selected_object_field",
    ] = "static"
    # When default_source="selected_object_field", the field on the
    # context.selected_object to read (e.g. "owner", "status").
    default_source_field: str | None = None
    # Read-only / hidden flags for form rendering and enforcement.
    readonly: bool = False
    hidden: bool = False
    # Regex constraint (validated client + server side).
    pattern: str | None = None
    # Custom error message override for validation failures.
    error_message: str | None = None
    # Static enum values (data_type=STRING). When non-empty the frontend
    # renders a <select> and the validator restricts input to these values.
    enum_values: list[str] | None = None
    # Object reference: when data_type=STRING and object_type_ref is set, the
    # parameter holds an object id of the referenced ObjectType.
    # is_object_set=True makes it an Object Set (list of object ids).
    object_type_ref: str | None = None
    is_object_set: bool = False


class ActionRule(BaseModel):
    """Declarative rule for validation or derivation.

    Types:
        - derivation: Compute new parameter values from existing ones
        - constraint: Validate parameter combinations
        - validation: Check business rules
    """

    type: Literal["constraint", "derivation", "validation"]
    target: str  # Target parameter name or property name
    expression: str  # Safe expression, e.g. "value > 0", "unit_price * quantity"
    description: str = ""


class ValueSource(BaseModel):
    """属性值来源(对标 Foundry 4 类 + EXPRESSION 扩展,ADR Action Mutation Mapping)。

    Sources:
        - PARAMETER:        value=参数api_name,如 "delay_minutes"
        - OBJECT_PROPERTY:  value="参数名.属性名",如 "newAircraft.status"
                            读 ObjectReference 参数引用对象的属性(决策 7)
        - STATIC_VALUE:     value=字面量,如 "Delayed"
        - SYSTEM_CONTEXT:   value ∈ {CURRENT_USER_ID, CURRENT_TIMESTAMP}
        - SYSTEM_GENERATED: value="uuid" → 生成主键
        - EXPRESSION:       value=simpleeval 表达式,命名空间=所有参数 + 引用对象属性
    """

    source: Literal[
        "PARAMETER",
        "OBJECT_PROPERTY",
        "STATIC_VALUE",
        "SYSTEM_CONTEXT",
        "SYSTEM_GENERATED",
        "EXPRESSION",
    ]
    value: str | None = None


class OntologyRule(BaseModel):
    """声明式 Ontology Rule(对标 Foundry Ontology Rules,ADR Action Mutation Mapping)。

    一类声明式变更:对哪个对象、按什么主键匹配、做 CREATE/UPDATE/Upsert/DELETE、
    属性值从哪来。由 ActionService._build_mutations_from_rules 解析为 Mutation。
    """

    type: Literal[
        "CreateObject",
        "ModifyObject",
        "UpsertObject",
        "DeleteObject",
        "CreateLink",
        "DeleteLink",
    ]
    # 目标对象定位:
    # - Modify/Upsert/Delete: target_parameter=ObjectReference 参数名,
    #   执行时取其值作主键,匹配 ObjectType.primary_key
    # - Create: target_object_type=显式对象类型
    target_parameter: str | None = None
    target_object_type: str | None = None
    # 本期不支持 target_path 跨对象路径(决策 7);字段保留但执行期忽略
    target_path: str | None = None
    # 属性赋值:{属性api_name: ValueSource};主键不可出现在 Modify 的 properties
    properties: dict[str, ValueSource] = Field(default_factory=dict)
    # 链接规则专用
    link_type: str | None = None
    source_parameter: str | None = None
    target_link_parameter: str | None = None
    # 条件执行(simpleeval,如 "$isUrgent = true");None=无条件
    condition: str | None = None
    # Upsert 命中 0 行时:raise_not_found(默认,与 Modify 一致) | create
    on_missing: Literal["raise_not_found", "create"] = "raise_not_found"
    description: str = ""


class WriteBackEffectConfig(BaseModel):
    """回写源表的 effect 配置(ADR §3.9)。

    用 ObjectType.backing_mapping 自动推导 table/columns 生成参数化 SQL;
    无需手写 table/columns。op=upsert 对应 ModifyObject 回写,
    op=insert 对应 CreateObject 回写。
    """

    target_object_type: str
    op: Literal["upsert", "insert"]


class ActionEffectConfig(BaseModel):
    """Side effect configuration for an Action.

    P1 expanded types (ADR-011):
        - webhook: HTTP POST to external API
        - write_back: JDBC UPSERT/MERGE to source system
        - sub_action: trigger another ActionType after commit (chain orchestration)
        - kafka_topic: publish change event to a Kafka topic
    ADR Action Mutation Mapping adds:
        - notification: in-app/email notification (对标 Foundry Side Effects)
    """

    type: Literal["webhook", "write_back", "sub_action", "kafka_topic", "notification"]
    config: dict[str, Any] = Field(default_factory=dict)
    # 触发时机:BEFORE/AFTER 本体变更(对标 Foundry);默认 AFTER
    trigger: Literal["BEFORE_ONTOLOGY_CHANGE", "AFTER_ONTOLOGY_CHANGE"] = "AFTER_ONTOLOGY_CHANGE"
    # 条件触发(simpleeval 表达式,作用于参数命名空间);None=无条件
    condition: str | None = None


class SubmissionCriterion(BaseModel):
    """A single global submission criterion (P1, ADR-011).

    Global validation that runs after parameter validation and rule
    evaluation, before mutations are applied. Replaces the unstructured
    ``submission_criteria: dict[str, Any]`` field — the engine now evaluates
    each criterion's expression via the rule engine.
    """

    expression: str  # simpleeval expression, e.g. "quantity > 0 and status == 'open'"
    error_message: str = Field(..., min_length=1)
    description: str = ""


class ActionTypeCreate(BaseModel):
    """Create a new ActionType — business users define the action contract.

    Includes parameter definitions, validation rules, and side effect configurations.

    P1 extensions (ADR-011):
        - operation_kind: classify the action for UI/permission routing
        - batch_enabled: gate Batch Action UI (full Batch runtime is P2)
        - submission_criteria: now structured (list[SubmissionCriterion]);
          a bare dict is accepted for backward compatibility and treated as
          a single criterion with key=expression, value=error_message.
    """

    api_name: str = Field(..., pattern=PROPERTY_API_NAME_PATTERN)
    display_name: str
    description: str = ""
    affected_object_type_api_name: str  # Uses api_name, not internal UUID
    parameters: list[ActionTypeParameter] = Field(default_factory=list)
    rules: list[ActionRule] = Field(default_factory=list)
    # Accept list (structured) or dict (legacy single criterion) for backward
    # compatibility. ActionService normalizes to list[SubmissionCriterion].
    submission_criteria: list[SubmissionCriterion] | dict[str, Any] = Field(default_factory=lambda: [])
    effects: list[ActionEffectConfig] = Field(default_factory=list)
    # ADR Action Mutation Mapping: 声明式 Ontology Rules(参数→对象变更映射)。
    # 为空时回退到旧 _build_mutations 硬编码行为(向后兼容)。
    ontology_rules: list[OntologyRule] = Field(default_factory=list)
    # HITL gating (ADR-010): low (default, no approval) / medium (list impact
    # + confirm) / high (type-name confirm on AG-UI, yes-no on MCP).
    risk_level: Literal["low", "medium", "high"] = "low"
    # P1: operation classification + batch gate.
    operation_kind: Literal["create", "update", "delete", "mixed"] = "mixed"
    batch_enabled: bool = False


class ActionExecutionRequest(BaseModel):
    """Request payload for executing an action.

    The idempotency_key ensures exactly-once semantics.
    """

    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None  # Client-provided idempotency key


class Mutation(BaseModel):
    """A single resolved change intent (Palantir OntologyEdit equivalent).

    P1 (ADR-011): unified mutation schema covering object CRUD and Link
    operations. Produced by MutationBuilder, consumed by ActionService.

    Mutation types:
        - CREATE_OBJECT: insert a new object into object_state
        - UPDATE_PROPERTY / UPDATE_OBJECT: OCC update of an existing object
        - DELETE_OBJECT: remove from object_state
        - RELATE: add a Link between source and target object
        - UNRELATE: remove a single Link
        - CLEAR_LINKS: remove all Links of a given link_type from a source

    Link-specific fields (link_type_api_name, target_object_id,
    target_object_ids) are only set for RELATE/UNRELATE/CLEAR_LINKS.
    ``condition`` (simpleeval expression) gates conditional assignment — when
    set and evaluates falsy, the mutation is skipped.
    """

    type: Literal[
        "CREATE_OBJECT",
        "UPDATE_PROPERTY",
        "UPDATE_OBJECT",
        "DELETE_OBJECT",
        "RELATE",
        "UNRELATE",
        "CLEAR_LINKS",
    ]
    rid: str
    expected_version: int = 0
    properties: dict[str, Any] = Field(default_factory=dict)
    # Link operation fields.
    link_type_api_name: str | None = None
    target_rid: str | None = None
    target_rids: list[str] | None = None  # CLEAR_LINKS batch target list
    # Conditional assignment: skip mutation when expression evaluates falsy.
    condition: str | None = None


class ActionExecutionResult(BaseModel):
    """Result of an action execution.

    Possible statuses:
        - "applied": Mutations committed to object_state (read-your-writes)
        - "accepted": Duplicate request with matching idempotency_key
        - "conflict": Row-level version OCC failed (affected_rows=0)
        - "validation_failed": Parameter or rule validation errors
    """

    status: Literal["applied", "accepted", "conflict", "validation_failed"]
    action_id: str
    affected_objects: dict[str, int] = Field(default_factory=dict)  # rid → new_version
    mutations: list[dict[str, Any]] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    conflict_details: dict[str, Any] | None = None
    # P1: objects the caller lacked row-level write permission for (ADR-011).
    forbidden_objects: list[str] = Field(default_factory=list)


class ActionContext(BaseModel):
    """Execution context injected into rule evaluation and permission checks.

    P1 (ADR-011): carries the built-in global variables Palantir Foundry
    injects into Action logic (currentUser, currentTimestamp, workspaceId).

    ADR-016 Phase 1: ``principal`` carries the resolved Principal (from
    AuthMiddleware) for the AuthorizationService five-layer check. The legacy
    ``current_user`` string is retained for backward compatibility — when
    set directly (without a principal), a minimal Principal is synthesized
    so both old and new code paths work.
    """

    principal: Principal = Field(default_factory=Principal.anonymous_principal)
    current_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workspace_id: str = ""
    ontology_snapshot_version: int | None = None
    selected_object: dict[str, Any] | None = None
    # P1: caller roles. Derived from ``principal.roles`` when a principal is
    # set, but also settable directly for backward compat (legacy callers
    # passing user_roles without a principal).
    user_roles: list[str] = Field(default_factory=list)
    # Legacy: the current user's display name. When set directly (old code),
    # a minimal Principal is synthesized. When a principal is set, this
    # mirrors ``principal.display_name``.
    current_user: str = "anonymous"

    @model_validator(mode="after")
    def _sync_principal_and_user(self) -> "ActionContext":
        """Keep current_user and principal.display_name consistent.

        - If current_user was set but principal is still anonymous, synthesize
          a non-anonymous Principal with that display name (legacy compat).
        - If principal was set (non-anonymous), mirror its display_name into
          current_user so legacy readers see the principal's name.
        """
        if self.current_user != "anonymous" and self.principal.is_anonymous:
            # Legacy construction: synthesize a Principal from current_user.
            object.__setattr__(
                self,
                "principal",
                Principal(id=self.current_user, display_name=self.current_user, is_anonymous=False),
            )
        elif not self.principal.is_anonymous:
            object.__setattr__(self, "current_user", self.principal.display_name)
        # Also keep user_roles in sync with principal.roles when user_roles
        # wasn't explicitly set but principal has roles.
        if not self.user_roles and self.principal.roles:
            object.__setattr__(self, "user_roles", list(self.principal.roles))
        return self


class ActionPreviewResult(BaseModel):
    """Dry-run preview result (P1, ADR-011 — OMA preview/debug panel).

    Runs the full pipeline up to mutation building and before_snapshot
    collection, without persisting to object_state/outbox. Lets the OMA
    debug panel show what *would* happen.
    """

    valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    mutations: list[dict[str, Any]] = Field(default_factory=list)
    before_snapshots: dict[str, Any] = Field(default_factory=dict)
    derived_parameters: dict[str, Any] = Field(default_factory=dict)


class ActionTypeVersion(BaseModel):
    """A historical version snapshot of an ActionType (P1, ADR-011)."""

    id: str
    action_type_id: str
    version: int
    snapshot: dict[str, Any]
    published_by: str
    created_at: datetime


# ── P2: Batch Action (ADR-011 follow-up) ──
#
# Batch Action applies the SAME ActionType to a large set of target objects,
# sharded into independent transactions so that:
#   - a single object's OCC conflict / validation error doesn't abort the
#     whole batch (partial success is reported);
#   - each shard commits in its own PG transaction (keeps locks short,
#     WAL segment pressure bounded);
#   - the batch is resumable via per-item idempotency keys.
#
# Alignment with Palantir Foundry: this is the Gaia equivalent of Foundry's
# Action Set / bulk Action submission — one ActionType applied to an Object
# Set, with per-item success/failure reporting.

# Default shard size: each shard is one PG transaction applying this many
# objects. Tuned for short lock duration + bounded WAL per commit. The
# caller may override; values <1 are clamped to 1.
BATCH_DEFAULT_SHARD_SIZE = 100
BATCH_MAX_SHARD_SIZE = 1000
BATCH_MAX_ITEMS = 10_000


class BatchActionItem(BaseModel):
    """A single target within a Batch Action.

    ``parameters`` are per-object inputs. They are merged with the batch's
    shared ``default_parameters`` (item values win on conflict) before being
    passed to the ActionType. ``idempotency_key`` (when omitted) is derived
    from the batch's idempotency key + item index, making the whole batch
    safely re-runnable.
    """

    rid: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    expected_version: int = 0


class BatchActionRequest(BaseModel):
    """Request payload for a Batch Action.

    Applies ``action_api_name`` to every item in ``items``. The ActionType
    must have ``batch_enabled = True`` (definition-time gate, ADR-011).
    """

    items: list[BatchActionItem] = Field(..., min_length=1)
    default_parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    # Shard size override; None → BATCH_DEFAULT_SHARD_SIZE. Clamped to
    # [1, BATCH_MAX_SHARD_SIZE] at execution time.
    shard_size: int | None = None
    # When True, the whole batch is rejected if ANY item fails (all-or-
    # nothing, single transaction per shard but cross-shard rollback is
    # NOT supported — items already committed in earlier shards stay).
    # When False (default), each item is independent; failures are reported
    # in the result and the batch returns status="partial".
    fail_fast: bool = False


class BatchItemResult(BaseModel):
    """Per-item outcome within a BatchActionResult."""

    rid: str
    status: Literal["applied", "accepted", "conflict", "validation_failed", "not_found", "forbidden", "error"]
    action_id: str | None = None
    new_version: int | None = None
    error: str | None = None


class BatchActionResult(BaseModel):
    """Aggregate result of a Batch Action.

    status:
        - "applied":  every item applied (or accepted via idempotency)
        - "partial":  some items applied, some failed (see item_results)
        - "failed":   no items applied (e.g. ActionType not batch_enabled,
                     or fail_fast aborted before any commit)
        - "rejected": request rejected before execution (too many items,
                     ActionType missing, etc.)
    """

    status: Literal["applied", "partial", "failed", "rejected"]
    total: int
    applied: int
    failed: int
    accepted: int = 0
    item_results: list[BatchItemResult] = Field(default_factory=list)
    # Shards actually committed (for observability / resume).
    shards_committed: int = 0
    shards_total: int = 0
    # The first error encountered (for quick diagnosis); full per-item
    # detail is in item_results.
    first_error: str | None = None
