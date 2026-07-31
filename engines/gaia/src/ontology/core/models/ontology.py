"""SQLAlchemy 2.0 ORM models for Ontology domain.

All domain entities are mapped here using SQLAlchemy's DeclarativeBase.
Every table uses UUID v4 primary keys, VARCHAR for enums, and JSONB for
flexible fields — per ADR-005.

Key design decisions:
- All FKs use ON DELETE CASCADE (except ActionType.affected_object_type_id = SET NULL)
- (ontology_id, api_name) is unique per ObjectType/LinkType/ActionType/InterfaceType/ValueType;
  (object_type_id, api_name) is unique per PropertyDef; api_name is globally unique for Ontology/SharedProperty/Struct
- Enums stored as VARCHAR, validated at the pydantic layer
- Flexible/config fields use JSONB
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ontology.core.models.defaults import JSONBType, new_uuid, utcnow


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class OntologyModel(Base):
    __tablename__ = "ontologies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    rid: Mapped[str] = mapped_column(String(255), default="")
    # v5.2 lifecycle: ACTIVE → DEPRECATED (precondition for delete) → soft-deleted.
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    # ADR-016 permission governance: Space↔Ontology 1:1 strong binding.
    # Nullable-first (Phase 0 migration backfills existing ontologies to a
    # default Space, then a later revision makes it NOT NULL). ondelete=RESTRICT
    # forces explicit Ontology migration before Space deletion (design §1.1).
    space_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("spaces.id", ondelete="RESTRICT"), nullable=True, unique=True
    )

    object_types: Mapped[list["ObjectTypeModel"]] = relationship(
        back_populates="ontology", cascade="all, delete-orphan"
    )
    link_types: Mapped[list["LinkTypeModel"]] = relationship(back_populates="ontology", cascade="all, delete-orphan")
    action_types: Mapped[list["ActionTypeModel"]] = relationship(
        back_populates="ontology", cascade="all, delete-orphan"
    )
    interface_types: Mapped[list["InterfaceTypeModel"]] = relationship(
        back_populates="ontology", cascade="all, delete-orphan"
    )
    value_types: Mapped[list["ValueTypeModel"]] = relationship(back_populates="ontology", cascade="all, delete-orphan")
    groups: Mapped[list["ObjectTypeGroupModel"]] = relationship(back_populates="ontology", cascade="all, delete-orphan")
    branches: Mapped[list["BranchModel"]] = relationship(back_populates="ontology", cascade="all, delete-orphan")


class ObjectTypeModel(Base):
    __tablename__ = "object_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    primary_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title_property: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_type: Mapped[str] = mapped_column(String(20), nullable=False)  # MANAGED | VIRTUAL
    visibility: Mapped[str] = mapped_column(String(20), default="NORMAL")
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # ADR-016 option B: definition-class resources belong to the Ontology in
    # Phase 0 (project_id nullable). Future option A migration fills this to
    # move definitions into a Project. AuthorizationService Layer 4 falls back
    # to the Ontology's owning Project when project_id is null (design §0.5).
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # v5.2 lifecycle: soft-delete marker (cascaded from Ontology delete).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # Primary backing dataset (Palantir "backing datasource"): the default /
    # main datasource backing this object type. Set on first link_dataset call
    # and retained across subsequent re-binds (acts as the primary source).
    # Property-level ``backing_dataset_api_name`` remains the authoritative
    # physical binding (supports column-wise MDO — one OT, multiple datasets).
    # This OT-level field is a convenience reference for list badges, detail
    # pages, and future permission anchoring — never the sole source of truth.
    # Nullable: None when unbound or for pure MDO types with no clear primary.
    backing_dataset_api_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Capabilities: ObjectType-level opt-in switches for enhanced indexing
    # (graph projection to Neo4j, spatial/temporal projection to PostGIS/
    # TimescaleDB). Mirrors Palantir Foundry's Ontology Manager Capabilities
    # tab. Null/empty = all disabled (only Doris base index, which is always
    # on for MANAGED types as the online read primary source, red line #4).
    # See docs/architecture/adr-015-agent-driven-graph-explore.md §capabilities.
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="object_types")
    properties: Mapped[list["PropertyDefModel"]] = relationship(
        back_populates="object_type", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ontology_id", "api_name", name="uq_object_types_ontology_api_name"),
        {"comment": "本体对象类型定义"},
    )


class PropertyDefModel(Base):
    __tablename__ = "properties"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    object_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("object_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_primary_key: Mapped[bool] = mapped_column(Boolean, default=False)
    is_title_property: Mapped[bool] = mapped_column(Boolean, default=False)
    nullable: Mapped[bool] = mapped_column(Boolean, default=True)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)
    backing_dataset_api_name: Mapped[str | None] = mapped_column(String(255))
    backing_catalog: Mapped[str | None] = mapped_column(String(255))
    backing_schema: Mapped[str | None] = mapped_column(String(255))
    backing_table: Mapped[str | None] = mapped_column(String(255))
    backing_column: Mapped[str | None] = mapped_column(String(255))
    # VECTOR property configuration (§14.4 语义检索): when data_type=VECTOR,
    # constraints holds {dimension, similarity_function, source_expression}.
    # source_expression = list of api_names whose values are concatenated to
    # form the embedding input text. Null/empty for non-VECTOR properties.
    # Aligned with Palantir Foundry Vector base type (dimension + similarity
    # function configured at property level; embedding value from pipeline).
    constraints: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, nullable=False, default=dict, server_default=text("'{}'")
    )
    # ADR-016 option A: Project ownership (NOT NULL after migration).
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # v5.2 lifecycle: DEPRECATED marks a property as removed/disused; soft-delete
    # is cascaded from the parent ObjectType (properties do not soft-delete independently).
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    object_type: Mapped["ObjectTypeModel"] = relationship(back_populates="properties")

    __table_args__ = (UniqueConstraint("object_type_id", "api_name", name="uq_properties_ot_api_name"),)


class LinkTypeModel(Base):
    __tablename__ = "link_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    source_object_type_id: Mapped[str] = mapped_column(String(32), nullable=False)
    target_object_type_id: Mapped[str] = mapped_column(String(32), nullable=False)
    foreign_key_property_api_name: Mapped[str | None] = mapped_column(String(255))
    cardinality: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    # Graph-reasoning 扩展 (graph-reasoning-design.md §3.2)：图遍历必需的边语义。
    # weight_property: 权重属性名（指向边属性），路径推理加权；NULL=等权。
    # temporal: 是否时态关系（含有效期），查询时由执行器注入时间窗口过滤。
    #   时态边的 start_time/end_time 作为边属性存储（Neo4j 关系属性 /
    #   object_links JSONB），不作为 LinkType 固定列，故此处不增列。
    weight_property: Mapped[str | None] = mapped_column(String(255), nullable=True, default=None)
    temporal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # ADR-016 option B: project_id reserved for option A migration (see ObjectType).
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # v5.2 lifecycle: ACTIVE → DEPRECATED → soft-deleted (cascaded from Ontology).
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="link_types")

    __table_args__ = (UniqueConstraint("ontology_id", "api_name", name="uq_link_types_ontology_api_name"),)


class ActionTypeModel(Base):
    __tablename__ = "action_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    affected_object_type_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("object_types.id", ondelete="SET NULL"), nullable=True
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    submission_criteria: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    # ADR-016 option B: project_id reserved for option A migration (see ObjectType).
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # v5.2 lifecycle: soft-delete marker (cascaded from Ontology).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    # Risk level for HITL gating (ADR-010): low (default, no approval) /
    # medium (list impact + confirm) / high (type-name confirm).
    risk_level: Mapped[str] = mapped_column(String(10), default="low")
    # P1 (ADR-011): versioning + operation classification + batch gate.
    version: Mapped[int] = mapped_column(default=1)
    operation_kind: Mapped[str] = mapped_column(String(10), default="mixed")
    batch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="action_types")

    __table_args__ = (UniqueConstraint("ontology_id", "api_name", name="uq_action_types_ontology_api_name"),)


class InterfaceTypeModel(Base):
    __tablename__ = "interface_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    extends_interface_ids: Mapped[list[str]] = mapped_column(JSONBType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="EXPERIMENTAL")
    # ADR-016 option B: project_id reserved for option A migration (see ObjectType).
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="interface_types")

    __table_args__ = (UniqueConstraint("ontology_id", "api_name", name="uq_interface_types_ontology_api_name"),)
    properties: Mapped[list["InterfacePropertyModel"]] = relationship(
        back_populates="interface_type", cascade="all, delete-orphan"
    )


class InterfacePropertyModel(Base):
    __tablename__ = "interface_properties"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    interface_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("interface_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    interface_type: Mapped["InterfaceTypeModel"] = relationship(back_populates="properties")


class ValueTypeModel(Base):
    __tablename__ = "value_types"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    base_type: Mapped[str] = mapped_column(String(50), nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="value_types")

    __table_args__ = (UniqueConstraint("ontology_id", "api_name", name="uq_value_types_ontology_api_name"),)


class SharedPropertyModel(Base):
    __tablename__ = "shared_properties"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    data_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # ADR-016 option B: project_id reserved for option A migration (see ObjectType).
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ObjectTypeSharedPropertyModel(Base):
    __tablename__ = "object_type_shared_properties"

    object_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("object_types.id", ondelete="CASCADE"), primary_key=True
    )
    shared_property_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("shared_properties.id", ondelete="CASCADE"), primary_key=True
    )


class ObjectTypeInterfaceModel(Base):
    """ObjectType implements InterfaceType 关联（Palantir implements）。

    表示某 ObjectType 实现了某 InterfaceType，用于 interfaceBase 跨类型查询。
    """
    __tablename__ = "object_type_interfaces"

    object_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("object_types.id", ondelete="CASCADE"), primary_key=True
    )
    interface_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("interface_types.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class StructModel(Base):
    __tablename__ = "structs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONBType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ObjectTypeGroupModel(Base):
    __tablename__ = "object_type_groups"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="groups")


class BranchModel(Base):
    __tablename__ = "branches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    ontology: Mapped["OntologyModel"] = relationship(back_populates="branches")


class ActionExecutionLogModel(Base):
    """Audit log for action executions.

    Records every action execution attempt with its parameters, mutations,
    and result status. Used for auditing, debugging, and idempotency checks.
    """

    __tablename__ = "action_execution_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    action_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    mutations: Mapped[list[dict[str, Any]]] = mapped_column(JSONBType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[str] = mapped_column(String(255), default="system")
    read_snapshot_id: Mapped[int | None] = mapped_column(default=None)
    # P1 (ADR-011): CDL change-data-log before/after snapshots for full
    # audit traceability ("who changed what from what to what").
    before_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    after_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboxModel(Base):
    """Transactional outbox for side effects (Webhook, Write-back).

    Co-located in PostgreSQL with metadata for atomic commits.
    Each Action execution that has side effects creates one or more
    outbox records within the same database transaction.

    effect_type 复用 (action-sync-outbox-design.md §3.1 + §14.4):
    - 用户配置副作用: WEBHOOK | WRITE_BACK | SUB_ACTION | KAFKA_TOPIC | NOTIFICATION
    - Action 自动同步副作用: INDEX (→Doris 近实时) | ARCHIVE (→Iceberg 微批) | EMBEDDING (→Doris embedding 列, §14.4)
    三类靠 effect_type 过滤互不干扰 (OutboxExecutor 排除 ARCHIVE,
    SyncFlushScheduler 只拉 ARCHIVE, EMBEDDING 与 INDEX 同走 OutboxExecutor)。
    SyncFlushScheduler 只取 ARCHIVE)。
    """

    __tablename__ = "outbox"

    # action-sync-outbox-design.md §8.1: 联合索引支撑两种 claim 查询:
    #   - OutboxExecutor: WHERE effect_type IN (...) AND status='PENDING' ...
    #   - SyncFlushScheduler: WHERE effect_type='ARCHIVE' AND status='PENDING'
    #     AND target_ontology=:ont
    # created_at 加入尾部稳定排序 (FIFO 消费)。
    __table_args__ = (
        Index(
            "ix_outbox_sync_claim",
            "effect_type",
            "status",
            "target_ontology",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    action_execution_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("action_execution_logs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effect_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # WEBHOOK | WRITE_BACK | SUB_ACTION | KAFKA_TOPIC | NOTIFICATION | INDEX | ARCHIVE | EMBEDDING
    # target_ontology: ARCHIVE 分桶键 (ontology api_name)。INDEX 不分桶
    # (逐条近实时),留空。历史 effect_type (WEBHOOK/...) 无本体维度,留空。
    target_ontology: Mapped[str | None] = mapped_column(String(255), nullable=True)
    effect_config: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING | COMPLETED | FAILED | DLQ
    retry_count: Mapped[int] = mapped_column(default=0)
    max_retries: Mapped[int] = mapped_column(default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ObjectStateModel(Base):
    """Operational state for all objects — synchronous write target for Actions.

    This table is the PG-side mirror of the "object" concept. Every Action's
    mutations are applied here within the same PG transaction as the execution
    log, guaranteeing atomicity and read-your-writes consistency.

    Key design:
    - version column enables row-level OCC: Actions include expected_version,
      and UPSERT fails if version has changed (affected_rows = 0 → conflict).
    - object_type_api_name drives CDC routing (PG → Kafka → Doris per-type tables).
    - properties stores the full object as JSONB for schema flexibility.
    """

    __tablename__ = "object_state"

    # Palantir RID (ri.ontology.main.object.{uuid}) — system-assigned identity,
    # stable across the object's lifetime, orthogonal to the business primary
    # key (which lives in ``properties``). See core/rid.py + handoff-rid-migration.md.
    rid: Mapped[str] = mapped_column(String(128), primary_key=True)
    object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(default=1)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False
    )
    # Redundant ontology api_name (denormalized from ontologies via
    # ontology_id). Avoids a JOIN for CDC routing (PG→Kafka transform needs
    # the api_name to build the per-type Kafka topic + Doris idx table name
    # idx_{ont}__{type}; the FK ontology_id is a UUID, not routable). Also
    # speeds up read-your-writes queries that filter by ontology. Populated
    # by ActionService on every upsert_object_state call.
    ontology_api_name: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default="", index=True
    )
    # P1 (ADR-011): who last modified this object — system audit field,
    # mirrors Palantir's modifiedBy. Populated from ActionContext.current_user.
    modified_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ObjectLinkModel(Base):
    """Link relationship instance between two objects (P1, ADR-011).

    The relational companion to ObjectStateModel: object_state stores object
    properties, object_links stores the Links between them. RELATE/UNRELATE/
    CLEAR_LINKS mutations write here within the same PG transaction as the
    object_state mutations, preserving Action atomicity.

    A dedicated table (rather than embedding links inside object_state
    properties JSONB) keeps link queries indexable and lets LinkTraversalService
    (Sprint 3) read a consistent link graph without parsing JSON.
    """

    __tablename__ = "object_links"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False
    )
    link_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Palantir RID refs (see ObjectStateModel.rid). String(128) accommodates both
    # MANAGED (ri.ontology.main.object.{uuid}, ~61 chars) and VIRTUAL合成 rid
    # (ri.ontology.main.virtual-object.{ont}.{ot}.{pk}, ~70+ chars).
    source_rid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_rid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "link_type_api_name",
            "source_rid",
            "target_rid",
            name="uq_object_links",
        ),
    )


class ActionTypeVersionModel(Base):
    """Historical version snapshot of an ActionType (P1, ADR-011).

    Every define/update of an ActionType publishes a snapshot here, enabling
    rollback and audit of configuration changes. The current
    ActionTypeModel row is the live config; this table is the history.
    """

    __tablename__ = "action_type_versions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    action_type_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("action_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONBType, default=dict)
    published_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("action_type_id", "version", name="uq_action_type_versions"),)


class AnalysisRecordModel(Base):
    """图分析查询的证据链快照（graph-reasoning-design.md §3.4, C11）。

    每次推理查询（query_with_dataframe / ObjectSet IR 执行）生成一条记录，含
    ObjectSet IR + 各步引擎结果摘要 + 命中对象的血缘指针。不做全链路数据
    血缘反查（留二期）。合规溯源轻量版：可追溯“谁在何时用何意图查到了哪些对象，
    各步耗时多少，是否被截断”。
    """

    __tablename__ = "analysis_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    principal: Mapped[str] = mapped_column(String(255), default="anonymous", server_default=text("'anonymous'"))
    object_set_ir: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    evidence_pointers: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
