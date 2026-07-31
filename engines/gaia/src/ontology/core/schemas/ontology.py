"""pydantic v2 schemas for Ontology domain — API validation/serialization.

These schemas are strictly separated from SQLAlchemy ORM models.
They define the API contract for creating, reading, and updating
ontology entities.

Conversion between ORM and schema:
    schema_obj = OntologyCreate.model_validate(orm_obj)  # ORM → pydantic
    orm_obj = OntologyModel(**schema_obj.model_dump())    # pydantic → ORM
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontology.core.naming import OBJECT_TYPE_API_NAME_PATTERN, ONTOLOGY_API_NAME_PATTERN


class DataType(StrEnum):
    """Supported property data types (aligned with Palantir OMS, reduced set).

    Graph-reasoning 扩展 (graph-reasoning-design.md §3.3)：激活 GEOPOINT/GEOSHAPE
    （静态空间），新增 GEOTEMPORAL_SERIES / TIME_SERIES（动态时序引用）。
    DataType 驱动存储路由（C4）：GEOPOINT/GEOSHAPE → PostGIS 静态表；
    GEOTEMPORAL_SERIES/TIME_SERIES → TimescaleDB 超表引用（属性值=Series ID）；
    其余走 Doris 全量 / Iceberg 明细。
    """

    STRING = "STRING"
    INTEGER = "INTEGER"
    SHORT = "SHORT"
    LONG = "LONG"
    BOOLEAN = "BOOLEAN"
    BYTE = "BYTE"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    ARRAY = "ARRAY"
    STRUCT = "STRUCT"
    VECTOR = "VECTOR"
    GEOPOINT = "GEOPOINT"
    GEOSHAPE = "GEOSHAPE"
    # 动态时序引用类型（graph-reasoning-design.md §3.3）。属性值 = Series ID，
    # 指向 TimescaleDB 超表；实际点位/指标由流式独立链路写入超表，不经 object_state。
    GEOTEMPORAL_SERIES = "GEOTEMPORAL_SERIES"  # 含 position 的时空序列（轨迹）
    TIME_SERIES = "TIME_SERIES"  # 纯时序指标（无空间）
    MEDIA_REFERENCE = "MEDIA_REFERENCE"
    ATTACHMENT = "ATTACHMENT"


# 时空类 DataType 集合，供投影器/路由判断用（避免散落的字面量比较）。
SPATIAL_DATA_TYPES: frozenset[DataType] = frozenset({DataType.GEOPOINT, DataType.GEOSHAPE})
TIMESERIES_DATA_TYPES: frozenset[DataType] = frozenset(
    {DataType.GEOTEMPORAL_SERIES, DataType.TIME_SERIES}
)


# ── Value Objects ──


class BackingColumnRef(BaseModel):
    """属性级别的 backing dataset 列引用(对标 Palantir mapping.column.backingColumn)。

    backing_column 是底层 Dataset 的物理列名(snake_case),仅元数据 API 可见;
    业务读写 API 只认 apiName,backing_column 对上层透明。
    """

    dataset_api_name: str = ""
    backing_catalog: str
    backing_schema: str
    backing_table: str
    backing_column: str


class VectorPropertyConfig(BaseModel):
    """VECTOR 属性的语义检索配置 (§14.4, 对齐 Palantir Vector base type)。

    当 PropertyDef.data_type == VECTOR 时, constraints 持本配置。
    - dimension: embedding 向量维度, 必须与 EmbeddingProvider.dim 一致
      (默认 OnnxEmbeddingProvider = 384)。
    - similarity_function: 距离度量 (cosine / l2), 默认 cosine。
      L2-normalized 向量下 cosine == inner_product (Doris ANN 用 inner_product)。
    - source_expression: embedding 输入文本由哪些属性拼接 (api_name 列表)。
      系统据此从 object_state.properties / Iceberg 列取值拼接 → embed。
      例: ["name", "description"]。必填——解决了「embedding 输入文本从哪来」
      的核心问题 (见 §14.4 遗留任务 2 设计要点 1)。
    """

    dimension: int = 384
    similarity_function: Literal["cosine", "l2"] = "cosine"
    source_expression: list[str] = Field(default_factory=list, description="拼接成 embedding 输入文本的 api_name 列表")


# ── Property ──


class PropertyDef(BaseModel):
    """Property definition within an ObjectType."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    object_type_id: str
    api_name: str
    display_name: str
    description: str = ""
    data_type: DataType
    is_primary_key: bool = False
    is_title_property: bool = False
    nullable: bool = True
    indexed: bool = False
    backing_mapping: BackingColumnRef | None = None
    # §14.4 语义检索: VECTOR 属性的配置 (从 ORM constraints JSONB 转换)。
    # 非 VECTOR 属性为 None。validator 负责 constraints JSONB → VectorPropertyConfig。
    vector_config: VectorPropertyConfig | None = None
    # v5.2 lifecycle.
    status: Literal["ACTIVE", "DEPRECATED"] = "ACTIVE"
    deleted_at: datetime | None = None
    # ADR-016 option A: the Project this definition belongs to.
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def _coerce_backing_mapping(cls, data: Any) -> Any:
        """Map ORM column names (backing_catalog/...) onto backing_mapping.

        SQLAlchemy PropertyDefModel stores the physical column reference as
        flat columns (backing_catalog, backing_schema, backing_table,
        backing_column, backing_dataset_api_name) — none of which match the
        BackingColumnRef field names (backing_catalog, ...). pydantic's
        from_attributes cannot bridge this gap, so we reconstruct the nested
        object here for every read path (get_object_type, list_properties,
        get_object_types_for_dataset, ...).
        """
        # Fast path for plain dicts (construction from kwargs).
        if isinstance(data, dict):
            if data.get("backing_mapping") is None:
                catalog = data.get("backing_catalog")
                if isinstance(catalog, str):
                    data = {
                        **data,
                        "backing_mapping": BackingColumnRef(
                            dataset_api_name=data.get("backing_dataset_api_name") or "",
                            backing_catalog=catalog,
                            backing_schema=data.get("backing_schema") or "",
                            backing_table=data.get("backing_table") or "",
                            backing_column=data.get("backing_column") or "",
                        ),
                    }
            # §14.4: constraints JSONB → vector_config (VECTOR 属性)。
            if data.get("vector_config") is None:
                constraints = data.get("constraints")
                if isinstance(constraints, dict) and constraints:
                    data = {**data, "vector_config": VectorPropertyConfig(**constraints)}
            return data

        # ORM model instance — rebuild as a dict only when a real mapping exists.
        try:
            catalog = getattr(data, "backing_catalog", None)
        except AttributeError:
            return data
        if not isinstance(catalog, str):
            return data
        # §14.4: ORM constraints JSONB → vector_config (VECTOR 属性)。
        constraints = getattr(data, "constraints", None)
        vec_cfg = VectorPropertyConfig(**constraints) if isinstance(constraints, dict) and constraints else None
        return {
            "id": getattr(data, "id"),
            "object_type_id": getattr(data, "object_type_id"),
            "api_name": getattr(data, "api_name"),
            "display_name": getattr(data, "display_name"),
            "description": getattr(data, "description", "") or "",
            "data_type": getattr(data, "data_type"),
            "is_primary_key": getattr(data, "is_primary_key", False),
            "is_title_property": getattr(data, "is_title_property", False),
            "nullable": getattr(data, "nullable", True),
            "indexed": getattr(data, "indexed", False),
            "backing_mapping": BackingColumnRef(
                dataset_api_name=getattr(data, "backing_dataset_api_name", "") or "",
                backing_catalog=catalog,
                backing_schema=getattr(data, "backing_schema", "") or "",
                backing_table=getattr(data, "backing_table", "") or "",
                backing_column=getattr(data, "backing_column", "") or "",
            ),
            "vector_config": vec_cfg,
            "created_at": getattr(data, "created_at"),
            "updated_at": getattr(data, "updated_at"),
        }


class PropertyDefCreate(BaseModel):
    """Create a new property — system-generated fields excluded.

    ``api_name`` is caller-supplied (camelCase). When omitted, the service
    derives it from ``display_name`` / ``backing_mapping.backing_column``.
    See ``PropertyInput`` for the rationale.
    """

    display_name: str
    api_name: str | None = None
    description: str = ""
    data_type: DataType
    is_primary_key: bool = False
    is_title_property: bool = False
    nullable: bool = True
    indexed: bool = False
    backing_mapping: BackingColumnRef | None = None
    # §14.4 语义检索: VECTOR 属性需提供 vector_config (dimension + source_expression)。
    # 非 VECTOR 属性应留 None。Service 层将其序列化进 ORM constraints JSONB。
    vector_config: VectorPropertyConfig | None = None
    project_id: str | None = None


class LinkTypeDef(BaseModel):
    """Relationship type definition between two ObjectTypes."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    source_object_type_id: str
    target_object_type_id: str
    foreign_key_property_api_name: str | None = None
    cardinality: Literal["ONE", "MANY"]
    direction: Literal["OUTGOING", "INCOMING"]
    # Graph-reasoning 扩展 (graph-reasoning-design.md §3.2)：图遍历必需的边语义。
    # weight_property: 权重属性名（指向边属性），路径推理加权；None=等权。
    # temporal: 是否时态关系（含有效期），查询时由执行器注入时间窗口过滤；
    #   时态边的 start_time/end_time 作为边属性存储，不作为 LinkType 固定列。
    weight_property: str | None = None
    temporal: bool = False
    # v5.2 lifecycle.
    status: Literal["ACTIVE", "DEPRECATED"] = "ACTIVE"
    deleted_at: datetime | None = None
    # ADR-016 option A: Project ownership.
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime


class LinkTypeDefCreate(BaseModel):
    """Create a new link type.

    ``api_name`` is caller-supplied (camelCase). When omitted, the service
    derives it from ``display_name``. See ``LinkInput`` for the rationale.
    """

    display_name: str
    api_name: str | None = None
    description: str = ""
    source_object_type_id: str
    target_object_type_id: str
    foreign_key_property_api_name: str | None = None
    cardinality: Literal["ONE", "MANY"]
    direction: Literal["OUTGOING", "INCOMING"]
    # Graph-reasoning 扩展 (§3.2)：可选边语义。默认等权 + 非时态。
    weight_property: str | None = None
    temporal: bool = False


# ── ObjectType ──


class ObjectTypeCapabilities(BaseModel):
    """ObjectType-level opt-in switches for enhanced indexing (ADR-015 §capabilities).

    Mirrors Palantir Foundry's Ontology Manager Capabilities tab. These are
    explicit user choices — enabling graph/geotime indexing incurs extra
    storage and sync cost, so the user must opt in per ObjectType.

    Four gates must ALL pass before a projection write happens:
      Gate 1: storage_type == MANAGED (VIRTUAL = no data to project)
      Gate 2: data_type match (GEOPOINT/GEOSHAPE for spatial, indexed for graph)
      Gate 3: relationship exists (Neo4j only — no links = no graph value)
      Gate 4: user explicitly enabled the capability here

    Doris base index is NOT gated here — it is always on for MANAGED types
    (online read primary source, red line #4).
    """

    graph_indexing_enabled: bool = False
    """Enable Neo4j graph projection (nodes + edges for searchAround traversal).

    Requires: MANAGED + at least one LinkType connecting this ObjectType
    + indexed properties for pruning. Without links, graph projection has
    no value (nothing to traverse)."""

    geotime_indexing_enabled: bool = False
    """Enable PostGIS/TimescaleDB spatial/temporal projection.

    Requires: MANAGED + GEOPOINT/GEOSHAPE properties (for PostGIS) or
    TIME_SERIES/GEOTEMPORAL_SERIES properties (for TimescaleDB).
    Time-series sync also requires a Kafka→TimescaleDB pipeline to be
    started separately via start_timeseries_sync."""

    @model_validator(mode="before")
    @classmethod
    def _handle_none(cls, data: Any) -> Any:
        """Treat None (null column / pre-migration row) as all-disabled."""
        if data is None:
            return {}
        return data


class ObjectType(BaseModel):
    """Object type definition (Palantir ObjectType equivalent)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    primary_key: str
    title_property: str
    storage_type: Literal["MANAGED", "VIRTUAL"]
    visibility: Literal["NORMAL", "PROMINENT", "HIDDEN"] = "NORMAL"
    status: Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"] = "ACTIVE"
    # ADR-016 option A: the Project this definition belongs to. NULL under
    # option B (fallback to Space's default Project); non-NULL after option A
    # migration (3a backfill + 3c NOT NULL).
    project_id: str | None = None
    # v5.2 lifecycle: soft-delete marker (cascaded from Ontology delete).
    deleted_at: datetime | None = None
    # Primary backing dataset (Palantir "backing datasource"): default/main
    # datasource for this object type. Convenience reference — the
    # authoritative physical binding lives on each property's
    # ``backing_mapping`` (supports column-wise MDO). None when unbound.
    backing_dataset_api_name: str | None = None
    # Capabilities: opt-in switches for enhanced indexing (graph/geotime).
    capabilities: ObjectTypeCapabilities = Field(default_factory=ObjectTypeCapabilities)
    properties: list[PropertyDef] = Field(default_factory=list)
    links: list[LinkTypeDef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ObjectTypeSummary(BaseModel):
    """Lightweight ObjectType for list/table/sidebar/canvas — no property details."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    storage_type: Literal["MANAGED", "VIRTUAL"]
    visibility: Literal["NORMAL", "PROMINENT", "HIDDEN"] = "NORMAL"
    status: Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"] = "ACTIVE"
    # v5.2 lifecycle: soft-delete marker.
    deleted_at: datetime | None = None
    # ADR-016 option A: Project ownership (for the overview tab display).
    project_id: str | None = None
    # Primary backing dataset (convenience ref for list badges; authoritative
    # binding is per-property ``backing_mapping``). None when unbound.
    backing_dataset_api_name: str | None = None
    properties_count: int = 0
    links_count: int = 0
    actions_count: int = 0
    created_at: datetime
    updated_at: datetime


# ── ADR-020: Ontology full metadata aggregate ──
# (Defined after Ontology/InterfaceType at the end of this file to resolve
# forward references cleanly.)


class ObjectTypeCreate(BaseModel):
    """Create a new ObjectType.

    ``api_name`` is PascalCase, caller-supplied (typically derived by the
    frontend from ``display_name`` via LLM translation for Chinese names,
    then confirmed/edited by the user). ``primary_key`` / ``title_property``
    are optional: when omitted the service derives them from the properties'
    ``is_primary_key`` / ``is_title_property`` flags (Q2).

    ``project_id`` (option A): the Project this definition belongs to. When
    omitted, the service resolves the Ontology's owning Space's default
    Project (option B fallback behavior, retained for backward compat).
    """

    api_name: str = Field(..., pattern=OBJECT_TYPE_API_NAME_PATTERN)
    display_name: str
    description: str = ""
    primary_key: str | None = None
    title_property: str | None = None
    storage_type: Literal["MANAGED", "VIRTUAL"]
    visibility: Literal["NORMAL", "PROMINENT", "HIDDEN"] = "NORMAL"
    status: Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"] = "ACTIVE"
    project_id: str | None = None
    # Primary backing dataset (optional). When provided the service anchors it
    # as the OT's default/main datasource; property-level ``backing_mapping``
    # remains authoritative. Typically left unset at creation and populated by
    # the first ``link_dataset`` call.
    backing_dataset_api_name: str | None = None
    capabilities: ObjectTypeCapabilities = Field(default_factory=ObjectTypeCapabilities)


# ── Ontology ──


class Ontology(BaseModel):
    """Top-level ontology container (Palantir Ontology + Space equivalent)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    display_name: str
    description: str = ""
    rid: str = ""
    object_types_count: int = 0
    # v5.2 lifecycle: ACTIVE → DEPRECATED (precondition for delete) → soft-deleted.
    status: Literal["ACTIVE", "DEPRECATED"] = "ACTIVE"
    deleted_at: datetime | None = None
    # ADR-016: Space↔Ontology 1:1 binding. None for pre-Phase-0 ontologies.
    space_id: str | None = None
    created_at: datetime
    updated_at: datetime


class OntologyCreate(BaseModel):
    """Create a new Ontology.

    Ontology api_name is PascalCase, caller-supplied (a namespace is
    user-named, like an S3 bucket — the user picks it explicitly). Same
    pattern as ObjectType (both are top-level ontology entities exposed
    with an uppercase-leading identifier).
    """

    api_name: str = Field(..., pattern=ONTOLOGY_API_NAME_PATTERN)
    display_name: str
    description: str = ""


class OntologyUpdate(BaseModel):
    """Update an existing Ontology (partial update).

    v5.2: ``status`` is mutable here so callers can Deprecate an ontology
    (ACTIVE → DEPRECATED) as the precondition for soft-delete (design §5.5).
    """

    display_name: str | None = None
    description: str | None = None
    status: Literal["ACTIVE", "DEPRECATED"] | None = None


# ── v5.2 Delete governance: impact report (design §六) ──


class ImpactItem(BaseModel):
    """One line of the cascade-impact report shown before deletion."""

    resource_type: str  # "object_type" | "action_type" | "link_type" | ...
    count: int
    label: str


class ImpactReport(BaseModel):
    """Cascade-impact report for ``GET /ontologies/{api_name}/impact``.

    Lists what will be affected by deleting the ontology. ``can_delete`` is
    False while the ontology is ACTIVE (must Deprecate first). Dependencies
    are reported but never block deletion (decision 6: remind, don't block).
    """

    api_name: str
    status: str  # ACTIVE | DEPRECATED
    impacts: list[ImpactItem]
    can_delete: bool
    blocked_reason: str | None = None


# ── SharedProperty ──


class SharedProperty(BaseModel):
    """Globally reusable property (Palantir SharedProperty equivalent)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    display_name: str
    description: str = ""
    data_type: DataType
    # ADR-016 option A: Project ownership.
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime


class SharedPropertyCreate(BaseModel):
    """Create a new shared property.

    ``api_name`` is derived from ``display_name``.
    """

    display_name: str
    description: str = ""
    data_type: DataType


# ── ActionType ──


class ActionType(BaseModel):
    """Action type definition (Palantir ActionType equivalent)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    affected_object_type_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    rules: dict[str, Any] = Field(default_factory=dict)
    # P1 (ADR-011): accept structured list or legacy dict (backward compat).
    submission_criteria: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=lambda: [])
    status: Literal["ACTIVE", "DEPRECATED"] = "ACTIVE"
    # Risk level drives HITL approval gating in the tool layer (ADR-010).
    # low (default) = no approval; medium = list impact + confirm;
    # high = type-name confirm (AG-UI) / yes-no confirm (MCP).
    risk_level: Literal["low", "medium", "high"] = "low"
    # P1 (ADR-011): versioning + operation classification + batch gate.
    version: int = 1
    operation_kind: Literal["create", "update", "delete", "mixed"] = "mixed"
    batch_enabled: bool = False
    # ADR-016 option A: Project ownership.
    project_id: str | None = None
    # ADR Action Mutation Mapping: 声明式 Ontology Rules。
    ontology_rules: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _lift_ontology_rules(self) -> "ActionType":
        """ORM 把 ontology_rules 压在 `parameters` JSONB 里（key=
        `ontology_rules`），但 API schema 把它暴需要在顶层。model_validate
        不会自动提升嵌套 key，这里补一刀：顶层为空时从 parameters 取。
        避免前端读顶层拿到 [] 而以为没有规则（编辑回填属性映射空缺）。
        """
        if not self.ontology_rules:
            rules_in_params = self.parameters.get("ontology_rules") if self.parameters else None
            if rules_in_params:
                # pydantic v2: 直接赋值（model_config 未 freeze）。
                self.ontology_rules = list(rules_in_params)
        return self


# ── Interface ──


class InterfaceProperty(BaseModel):
    """Property within an InterfaceType."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    interface_type_id: str
    api_name: str
    display_name: str
    description: str = ""
    data_type: DataType
    is_shared: bool = False
    created_at: datetime
    updated_at: datetime


class InterfaceType(BaseModel):
    """Interface type (Palantir InterfaceType equivalent, preview feature)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    extends_interface_ids: list[str] = Field(default_factory=list)
    status: Literal["EXPERIMENTAL"] = "EXPERIMENTAL"
    properties: list[InterfaceProperty] = Field(default_factory=list)
    # ADR-016 option A: Project ownership.
    project_id: str | None = None
    created_at: datetime
    updated_at: datetime


# ── ValueType ──


class ValueType(BaseModel):
    """Value type — domain semantics wrapper for base DataType."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    base_type: DataType
    constraints: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ── Struct ──


class StructField(BaseModel):
    """Field within a Struct type."""

    name: str
    data_type: DataType
    nullable: bool = True


class Struct(BaseModel):
    """Structured property type (Palantir Struct equivalent, global)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    api_name: str
    display_name: str
    description: str = ""
    fields: list[StructField] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ── ObjectTypeGroup ──


class ObjectTypeGroup(BaseModel):
    """Grouping of ObjectTypes (Palantir ObjectTypeGroup equivalent)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    api_name: str
    display_name: str
    description: str = ""
    created_at: datetime
    updated_at: datetime


# ── Branch ──


class Branch(BaseModel):
    """Ontology branch (Palantir Branch equivalent)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ontology_id: str
    name: str
    is_main: bool = False
    status: Literal["ACTIVE", "MERGED", "CLOSED"] = "ACTIVE"
    created_at: datetime
    updated_at: datetime


# ── Batch ObjectType creation inputs ──
# Used by both Routes (API validation) and Services (orchestration),
# so they live in core/schemas — not in routes — to avoid the service→route
# dependency inversion. See ADR on layered architecture.


class PropertyInput(BaseModel):
    """Property definition submitted as part of a batch ObjectType create/update.

    ``api_name`` is caller-supplied (camelCase, like ObjectType/Action/Link
    apiName): the frontend derives it from ``display_name`` via AI assist +
    user edit, then submits both together. When omitted, the service derives
    it from ``display_name`` (ASCII) or ``backing_mapping.backing_column``,
    falling back to ``propertyN``. Chinese ``display_name`` with no backing
    column has no ASCII anchor, so callers SHOULD supply ``api_name``
    explicitly for non-ASCII display names.
    ``is_primary_key`` / ``is_title_property`` are optional hints; they are the
    **authoritative** source for the ObjectType's ``primary_key`` /
    ``title_property`` (Q2) — when the ObjectType omits those fields the
    service reads them from the property carrying the flag.
    """

    display_name: str
    api_name: str | None = None
    description: str = ""
    data_type: str
    searchable: bool = True
    is_primary_key: bool | None = None
    is_title_property: bool | None = None
    backing_mapping: BackingColumnRef | None = None
    # §14.4 语义检索: data_type=VECTOR 时需提供 vector_config。
    vector_config: VectorPropertyConfig | None = None


class LinkInput(BaseModel):
    """Link definition submitted as part of a batch ObjectType create.

    ``api_name`` is caller-supplied (camelCase, like Property/Action apiName):
    the frontend derives it from ``display_name`` via AI assist + user edit,
    then submits both together. When omitted, the service derives it from
    ``display_name`` (ASCII) or falls back to ``linkTypeN``. Chinese
    ``display_name`` has no ASCII anchor (links have no backing_column), so
    callers SHOULD supply ``api_name`` explicitly for non-ASCII display names.
    """

    display_name: str
    api_name: str | None = None
    target_object_type_id: str
    cardinality: str = "ONE"
    direction: str = "OUTGOING"
    # Graph-reasoning 扩展 (§3.2)：可选边语义。
    weight_property: str | None = None
    temporal: bool = False


class ObjectTypeBatchCreate(BaseModel):
    """Atomic batch create/update of an ObjectType with its properties and links.

    Processed in a single PostgreSQL transaction by OntologyService.
    ``api_name`` is PascalCase, caller-supplied. ``primary_key`` /
    ``title_property`` are optional (derived from property flags when omitted — Q2).
    """

    api_name: str = Field(..., pattern=OBJECT_TYPE_API_NAME_PATTERN)
    display_name: str
    description: str = ""
    primary_key: str | None = None
    title_property: str | None = None
    storage_type: str = "MANAGED"
    properties: list[PropertyInput] = []
    links: list[LinkInput] = []


# ── ADR-020: Ontology full metadata aggregate ───────────────────────────
#
# Mirrors Palantir Foundry ``/v2/ontologies/{ont}/fullMetadata``: a single
# request returns objects + links + actions + interfaces so an external Agent
# (MCP) or an OSDK-style code generator can bootstrap without N round-trips
# (list → describe → describe → ...). The aggregate is best-effort: a failing
# entity type is recorded in ``omitted`` rather than failing the whole request.
# Defined here (after Ontology/InterfaceType) so all references resolve.


class ActionTypeSummary(BaseModel):
    """Lightweight ActionType for the full-metadata aggregate (ADR-020).

    The full ``parameters`` schema is omitted — it is large and only needed
    when actually invoking/validating an action (call ``describe_action_type`` /
    ``validate_action`` on demand). The summary carries just enough for an Agent
    to know an action exists and what it does.
    """

    model_config = ConfigDict(from_attributes=True)

    api_name: str
    display_name: str
    description: str = ""
    affected_object_type_api_name: str | None = None
    risk_level: Literal["low", "medium", "high"] = "low"
    operation_kind: Literal["create", "update", "delete", "mixed"] = "mixed"


class ObjectTypeFullMetadata(BaseModel):
    """An ObjectType with its relationship/action context (ADR-020).

    Mirrors Palantir ``ObjectTypeFullMetadata``: the object type is self-
    describing — its inbound/outbound links and applicable actions are attached
    so a consumer never needs a second call to resolve "who points at me" or
    "what can I do to this object".
    """

    api_name: str
    display_name: str
    description: str = ""
    primary_key: str
    title_property: str
    storage_type: Literal["MANAGED", "VIRTUAL"]
    visibility: Literal["NORMAL", "PROMINENT", "HIDDEN"] = "NORMAL"
    status: Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"] = "ACTIVE"
    # Primary backing dataset (convenience ref; authoritative binding is
    # per-property ``backing_mapping``). None when unbound.
    backing_dataset_api_name: str | None = None
    properties: list[PropertyDef] = Field(default_factory=list)
    inbound_links: list[str] = Field(default_factory=list)
    outbound_links: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    # Internal UUID kept so consumers can resolve LinkTypeDef source/target
    # UUIDs back to api_names without a second metadata call (the flat
    # link_types map carries UUIDs; this id closes the loop).
    id: str = ""


class OntologyFullMetadata(BaseModel):
    """Full metadata of an ontology in a single payload (ADR-020).

    Returned by ``describe_ontology`` (MCP tool + REST ``/fullMetadata``) and
    consumed by ``build_ontology_summary`` for the AG-UI text injection.

    Maps (``dict[api_name, ...]``) give O(1) lookup by api_name — matching
    Palantir's response shape. ``partial``/``omitted`` carry the best-effort
    semantics: if one entity-type query failed, the rest still load.
    """

    ontology: Ontology
    object_types: dict[str, ObjectTypeFullMetadata] = Field(default_factory=dict)
    link_types: dict[str, LinkTypeDef] = Field(default_factory=dict)
    action_types: dict[str, ActionTypeSummary] = Field(default_factory=dict)
    interfaces: list[InterfaceType] = Field(default_factory=list)
    partial: bool = False
    omitted: list[str] = Field(default_factory=list)
