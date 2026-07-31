import enum
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship

from pkg.common.utils import utcnow


class ChannelType(str, enum.Enum):
    WECOM = "wecom"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"


class EngineType(str, enum.Enum):
    HERMES = "HERMES"
    OPENCLAW = "OPENCLAW"
    DIFY = "DIFY"


class AgentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    OFFLINE = "OFFLINE"


class DeploymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ProfileType(str, enum.Enum):
    INDEPENDENT = "INDEPENDENT"
    SHARED = "SHARED"


class Base(DeclarativeBase):
    pass

# Association tables
user_roles = Table(
    "user_roles", Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)

user_group_members = Table(
    "user_group_members", Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), primary_key=True),
)
class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index(
            "ux_users_email_verified",
            "email",
            unique=True,
            postgresql_where=text("email_verified = TRUE AND email IS NOT NULL"),
        ),
        Index(
            "ux_users_phone_verified",
            "phone",
            unique=True,
            postgresql_where=text("phone_verified = TRUE AND phone IS NOT NULL"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(128), unique=True, nullable=False, index=True)
    real_name = Column(String(128), nullable=True)
    email = Column(String(256), nullable=True)
    phone = Column(String(32), nullable=True)
    email_verified = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))
    phone_verified = Column(Boolean, nullable=False, default=False, server_default=text("FALSE"))
    hashed_password = Column(String(256), nullable=False)
    avatar_url = Column(String(512), nullable=True)
    is_active = Column(Boolean, default=True)
    # 0.8.103 登录安全加固
    failed_login_count = Column(Integer, nullable=False, default=0, server_default="0")
    locked_until = Column(DateTime(timezone=True), nullable=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    last_login_ip = Column(String(64), nullable=True)
    last_login_user_agent = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    roles = relationship("Role", secondary=user_roles, back_populates="users")
    groups = relationship("UserGroup", secondary=user_group_members, back_populates="members")
class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), unique=True, nullable=False)
    description = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    users = relationship("User", secondary=user_roles, back_populates="roles")
    permissions = relationship("Permission", secondary=role_permissions, back_populates="roles")
class Permission(Base):
    __tablename__ = "permissions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), unique=True, nullable=False)
    code = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    resource_type = Column(String(64), nullable=False)  # menu, api, button
    created_at = Column(DateTime(timezone=True), default=utcnow)

    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")
# Agent access association tables

class UserGroup(Base):
    """用户组 — 平台最小隔离单元（等价于租户）。

    所有租户化资源（定义/实例/运行时/私有资源池）按 group_id 归属，
    跨组不可见。code 为机器码（中文转拼音/英文直用，自动生成，全局唯一），
    用于 MinIO 前缀与 Pod label。litellm_team_id = str(id)，创建时持久化。
    """

    __tablename__ = "user_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), unique=True, nullable=False, index=True)
    code = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    litellm_team_id = Column(String(128), nullable=True)  # = str(id)，创建时 ensure_team 持久化
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    members = relationship("User", secondary=user_group_members, back_populates="groups")



class AgentDeployment(Base):
    """智能体引擎部署状态"""
    __tablename__ = "agent_deployments"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "scope_type", "scope_target_id",
            name="uq_agent_deployment_scope",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("agent_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_pool_id = Column(UUID(as_uuid=True), ForeignKey("resource_pools.id"), nullable=True)
    status = Column(Enum(DeploymentStatus), default=DeploymentStatus.PENDING, nullable=False)

    # 部署范围
    scope_type = Column(String(16), nullable=False, default="ALL")
    scope_target_id = Column(UUID(as_uuid=True), nullable=True)

    pod_name = Column(String(256), nullable=True)
    namespace = Column(String(128), default="unionagents")
    engine_url = Column(String(512), nullable=True)

    # Pod 内 Profile → 端口映射
    internal_port_map = Column(JSON, default=dict)

    deployed_at = Column(DateTime(timezone=True), nullable=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    backup_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archive_path = Column(String(1024), nullable=True)
    node_name = Column(String(256), nullable=True)
    error_message = Column(Text, nullable=True)

    instance = relationship("AgentInstance", back_populates="deployments")

class AgentProfile(Base):
    """Hermes Profile 映射"""
    __tablename__ = "agent_profiles"
    __table_args__ = (
        UniqueConstraint("deployment_id", "profile_name", name="uq_profile_per_deployment"),
        UniqueConstraint("instance_id", "resource_pool_id", "user_id", name="uq_user_profile_per_instance"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("agent_instances.id", ondelete="CASCADE"), nullable=False)
    resource_pool_id = Column(UUID(as_uuid=True), ForeignKey("resource_pools.id"), nullable=False)
    deployment_id = Column(UUID(as_uuid=True), ForeignKey("agent_deployments.id", ondelete="CASCADE"), nullable=False)

    profile_name = Column(String(256), nullable=False)
    profile_type = Column(String(16), nullable=False, default="INDEPENDENT")

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False)

    hermes_home = Column(String(512), nullable=False)
    internal_port = Column(Integer, nullable=True)

    is_active = Column(Boolean, default=True)
    config_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ImUserBinding(Base):
    """IM 平台用户 ID ↔ 平台用户 UUID 映射

    用于将企业微信/飞书/钉钉等 IM 平台的用户 ID（如 FromUserName）
    映射到 UnionAgents 的内部用户 UUID，使 profile_resolver 可以
    正确识别和处理来自 IM 渠道的用户。
    """
    __tablename__ = "im_user_bindings"
    __table_args__ = (
        UniqueConstraint("channel_type", "im_user_id", name="uq_im_channel_user"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    channel_type = Column(String(32), nullable=False)
    im_user_id = Column(String(256), nullable=False)
    im_user_name = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class BusinessUserBinding(Base):
    """业务系统用户身份绑定（1:1）：UA user ↔ 业务系统用户。

    与 im_user_bindings（1:N，IM 渠道身份）并列，共同构成「平台用户 + IM 用户 +
    业务用户」三方身份。业务绑定信息经 current-user-info 预置 skill 实时 pull
    /user-context 端点返回给智能体（见 user_info_renderer.serialize_user_context）。

    user_id ondelete=CASCADE：删 UA user 时自动删业务绑定（delete_user 无需显式删）。
    """
    __tablename__ = "business_user_bindings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_business_binding_per_user"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    business_username = Column(String(128), nullable=False)
    business_phone = Column(String(64), nullable=True)
    business_email = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class ResourceMetricSample(Base):
    """引擎 Pod 资源用量时序采样 — 由 controller metric_sampler 每 1 分钟写入。

    用于 instance 详情 / 资源池页的 CPU/内存趋势图。按 instance_id（实例详情）
    或 resource_pool_id（资源池页）聚合。保留 7 天，超期由采样任务清理。
    """
    __tablename__ = "resource_metric_samples"
    __table_args__ = (
        Index("ix_rms_instance_ts", "resource_pool_id", "ts"),
        Index("ix_rms_agent_ts", "instance_id", "ts"),
        Index("ix_rms_pod_ts", "pod_name", "ts"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resource_pool_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    pod_name = Column(String(256), nullable=False, index=True)
    instance_id = Column(UUID(as_uuid=True), nullable=True, index=True)  # Pod 所属 instance（创建者）
    ts = Column(DateTime(timezone=True), nullable=False, index=True)
    cpu_m = Column(Integer, nullable=False, default=0)       # millicores
    memory_mi = Column(Integer, nullable=False, default=0)   # Mi


# =========================================
# V3 三层模型：定义 / 版本 / 资源池 / 实例
# 与现有 Agent/EngineInstance 并存；service 切换后迁移数据并下线老表。
# 见 memory: v3-three-layer-refactor
# =========================================


class DefinitionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class MarketplaceStatus(str, enum.Enum):
    PRIVATE = "PRIVATE"   # 仅本租户可用
    LISTED = "LISTED"     # 已发布到智能体市场（预留，本期恒 PRIVATE）


class ResourcePool(Base):
    """运行资源池 — K8s Pod 资源规格 + 回收策略，与引擎类型解耦，可跨实例共享。"""

    __tablename__ = "resource_pools"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, default="")

    # K8s 资源规格（每个 Pod）
    min_cpu = Column(String(16), default="100m")
    max_cpu = Column(String(16), default="2")
    min_memory = Column(String(16), default="256Mi")
    max_memory = Column(String(16), default="2Gi")

    # Pod 副本数
    min_replicas = Column(Integer, default=1)
    max_replicas = Column(Integer, default=5)

    # 单 Pod 最大会话数（原 max_profiles_per_pod）
    max_sessions_per_pod = Column(Integer, default=20)

    # 自动回收策略
    auto_recycle = Column(Boolean, default=True)
    idle_suspend_minutes = Column(Integer, default=30)
    idle_destroy_hours = Column(Integer, default=24)

    # 归属用户组：NULL=平台共享默认池（各组可用），非空=组私有池（仅该组可用）
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=True, index=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    creator = relationship("User")
    group = relationship("UserGroup", foreign_keys=[group_id])


class AgentDefinition(Base):
    """智能体定义 — 元数据层。定义「能干什么」，支持版本快照与发布。"""

    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("group_id", "name", name="uq_definition_group_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, default="")
    avatar_color = Column(String(7), default="#6366f1")

    # 引擎类型（枚举 + 系统配置，不建外键表）。见 pkg.common.config.ENGINE_RUNTIMES
    engine_type = Column(Enum(EngineType), nullable=False, default=EngineType.HERMES)

    status = Column(Enum(DefinitionStatus), default=DefinitionStatus.DRAFT, nullable=False)
    # use_alter 打破与 agent_versions.definition_id 的循环 FK
    current_version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_versions.id", use_alter=True, name="fk_definition_current_version"),
        nullable=True,
    )
    marketplace_status = Column(Enum(MarketplaceStatus), default=MarketplaceStatus.PRIVATE, nullable=False)

    # 当前草稿配置（发布时拷贝为 AgentVersion 快照）
    persona_config = Column(JSON, default=dict)   # 人设 SOUL.md / system_prompt
    model_config = Column(JSON, default=dict)     # 模型 / LiteLLM 配置
    skill_config = Column(JSON, default=dict)
    memory_config = Column(JSON, default=dict)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    published_at = Column(DateTime(timezone=True), nullable=True)

    group = relationship("UserGroup", foreign_keys=[group_id])
    creator = relationship("User")
    current_version = relationship("AgentVersion", foreign_keys=[current_version_id])
    versions = relationship(
        "AgentVersion",
        back_populates="definition",
        foreign_keys="AgentVersion.definition_id",
        passive_deletes=True,
    )


class AgentVersion(Base):
    """智能体版本快照 — 不可变。发布定义时生成，实例绑定特定版本支持回滚。"""

    __tablename__ = "agent_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    definition_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    version_no = Column(String(32), nullable=False)  # 语义化版本，如 1.0.0

    # 配置快照（发布时从定义草稿拷贝）
    persona_config = Column(JSON, default=dict)   # 人设 SOUL.md / system_prompt
    model_config = Column(JSON, default=dict)     # 模型 / LiteLLM 配置
    skill_config = Column(JSON, default=dict)
    memory_config = Column(JSON, default=dict)
    engine_type = Column(Enum(EngineType), nullable=False, default=EngineType.HERMES)  # 快照时刻

    change_log = Column(Text, default="")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    definition = relationship("AgentDefinition", back_populates="versions", foreign_keys=[definition_id])
    group = relationship("UserGroup", foreign_keys=[group_id])


# AgentInstance 访问范围：组隔离后实例默认对所属组全员可见，不再需要 access 关联表
# （原 agent_instance_user_access / agent_instance_group_access 已随 group 隔离改造删除）


class AgentInstance(Base):
    """智能体实例 — 定义×版本×资源池×访问范围的关联。运行时与终端访问均挂此层。"""

    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint("group_id", "name", name="uq_instance_group_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(Text, default="")

    definition_id = Column(UUID(as_uuid=True), ForeignKey("agent_definitions.id"), nullable=False, index=True)
    version_id = Column(UUID(as_uuid=True), ForeignKey("agent_versions.id"), nullable=True)
    resource_pool_id = Column(UUID(as_uuid=True), ForeignKey("resource_pools.id"), nullable=True)  # Dify 外接模式不需要资源池

    status = Column(Enum(AgentStatus), default=AgentStatus.DRAFT, nullable=False)  # DRAFT/PUBLISHED/OFFLINE

    # LiteLLM per-instance key 信息 {team_id, key_id, key, model_group}（每实例一 key，归属 UserGroup 对应 Team）
    litellm_config = Column(JSON, default=dict)

    # Dify 应用对接配置 per-instance：{base_url, app_api_key, app_type, app_id, app_name, source}
    # 每实例独立绑定一个 Dify 应用（dev/staging/prod 可指向不同 app）
    dify_config = Column(JSON, default=dict)

    # 运行时开关 per-instance，承载可选运行时特性配置，如：
    # {"browser_sandbox": {"enabled": true}} —— 启用浏览器沙箱（VNC 接管）能力
    runtime_config = Column(JSON, default=dict)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    published_at = Column(DateTime(timezone=True), nullable=True)

    creator = relationship("User")
    definition = relationship("AgentDefinition", foreign_keys=[definition_id])
    version = relationship("AgentVersion", foreign_keys=[version_id])
    resource_pool = relationship("ResourcePool", foreign_keys=[resource_pool_id])
    group = relationship("UserGroup", foreign_keys=[group_id])
    # 删实例时交由 DB ondelete=CASCADE 清理渠道（passive_deletes 避免 ORM 懒加载子集合，
    # async 下不触发 greenlet，也避免默认置 NULL 撞 instance_id NOT NULL）
    channels = relationship(
        "AgentInstanceChannel", back_populates="instance", passive_deletes=True
    )
    # deployments 同理：删实例时 DB ondelete=CASCADE 清理 agent_deployments（+ 级联 agent_profiles）
    deployments = relationship(
        "AgentDeployment", back_populates="instance", passive_deletes=True
    )
    # api_keys 同理：删实例时 DB ondelete=CASCADE 清理 agent_instance_api_keys
    api_keys = relationship(
        "AgentApiKey", back_populates="instance", passive_deletes=True
    )


class AgentInstanceChannel(Base):
    """智能体实例的 IM 渠道绑定（重命名自 AgentChannel，agent_id→instance_id）。"""

    __tablename__ = "agent_instance_channels"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "channel_type", "scope_type", "scope_target_id",
            name="uq_instance_channel_scope",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("agent_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_type = Column(String(32), nullable=False)

    scope_type = Column(String(16), nullable=False, default="ALL")
    scope_target_id = Column(UUID(as_uuid=True), nullable=True)
    profile_type = Column(String(16), nullable=False, default="INDEPENDENT")

    config = Column(JSON, nullable=False, default={})
    enabled = Column(Boolean, default=True)
    callback_url = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    instance = relationship("AgentInstance", back_populates="channels")


class AgentApiKey(Base):
    """智能体实例的 OpenAI 兼容 API Key。

    明文仅创建时返回一次；DB 只存 HMAC-SHA256 hash（不可逆）。key_prefix 取前 14 字符
    用于列表展示和 Gateway 前缀索引查询。每实例最多 10 个（service 层 enforce）。
    """

    __tablename__ = "agent_instance_api_keys"
    __table_args__ = (
        UniqueConstraint("instance_id", "name", name="uq_instance_apikey_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), ForeignKey("agent_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    key_hash = Column(String(128), nullable=False)  # HMAC-SHA256 hex
    key_prefix = Column(String(20), nullable=False, index=True)  # first 14 chars for display + lookup
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_used_ip = Column(String(64), nullable=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    instance = relationship("AgentInstance", back_populates="api_keys")


class ArticleStatus(str, enum.Enum):
    """社区文章状态机：DRAFT → PENDING（提交审核）→ PUBLISHED / REJECTED。"""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    REJECTED = "REJECTED"


class Article(Base):
    """社区技术文章（全局平台级，无 group 隔离，文章池共享）。

    markdown 存 content，前端 md-editor-v3 MdPreview 渲染。slug 全局唯一用于 URL。
    审核流：作者 create(DRAFT) → submit(PENDING) → admin audit(PUBLISHED/REJECTED)。
    公开 list/get 只返回 PUBLISHED；view_count 在 get 时自增（UPDATE 不 fetch）。
    """

    __tablename__ = "community_articles"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_article_slug"),
        Index("ix_articles_status_published_at", "status", "published_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    slug = Column(String(200), nullable=False, index=True)
    excerpt = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    status = Column(String(20), default=ArticleStatus.DRAFT, nullable=False, index=True)  # DRAFT/PENDING/PUBLISHED/REJECTED
    reject_reason = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    author = relationship("User")


class SkillCredential(Base):
    """skill 外部 API 凭证（per-skill + scope 预留）。

    凭证加密存储（credentials_encrypted 列，Fernet token）。运行时由 controller 把密文 fan-out
    落 Pod skills/{name}/secrets.enc，Pod 内 sidecar 容器持 credential_encryption_key 解密，
    skill 用 execute_code 调 localhost:8004/secret 拿明文直接调外部 API（不经出口代理）。
    scope 维度当前固定 per-skill（scope_type='ALL'）；预留 USER / USER_GROUP 扩展
    （复用 AgentInstanceChannel.scope 模式）。
    """

    __tablename__ = "skill_credentials"
    __table_args__ = (
        UniqueConstraint(
            "definition_id",
            "skill_name",
            "scope_type",
            "scope_target_id",
            name="uq_skill_credential_scope",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # skill 绑定 definition 层（与 skill_config JSON 列同维度，非 instance）
    definition_id = Column(
        UUID(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_name = Column(String(128), nullable=False, index=True)  # 对应 skill_config.skills[].name

    # scope 预留：当前 per-skill 即 scope_type='ALL'，scope_target_id=NULL
    scope_type = Column(String(16), nullable=False, default="ALL")
    scope_target_id = Column(UUID(as_uuid=True), nullable=True)

    # 加密的凭证 JSON（Fernet token，base64）。明文结构由 manifest config_params 决定，
    # 如 {"api_key": "sk-xxx"}。绝不存明文。
    credentials_encrypted = Column(Text, nullable=False)

    # 外部 API base URL（可选，便于审计 + 默认值；skill 请求时也可在 body 传 target_url 覆盖）
    target_base_url = Column(String(512), nullable=True)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    definition = relationship("AgentDefinition")


class DifyEngineMode(str, enum.Enum):
    """Dify 引擎对接模式。MANAGED=平台托管 Pod；EXTERNAL=外接 Dify 平台。"""
    MANAGED = "MANAGED"
    EXTERNAL = "EXTERNAL"


class EngineConfig(Base):
    """引擎级系统配置 — 全局或按 UserGroup 隔离。

    v1 只实现 DIFY 引擎 + 全局配置（group_id NULL）。外接模式下 admin_email/admin_password
    可选：配了走 Console API（下拉选应用），没配走 Service API（手填 base_url+api_key）。

    admin_password / cached_access_token 用 Fernet 加密存（app/core/crypto.py）。
    """

    __tablename__ = "engine_configs"
    __table_args__ = (
        # 全局唯一约束：每个 engine_type 只允许一个 group_id IS NULL 的全局配置
        Index(
            "uq_engine_config_global_engine_type",
            "engine_type",
            unique=True,
            postgresql_where="group_id IS NULL",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL = 全局配置；非空 = 按用户组隔离（v2 扩展用）
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    engine_type = Column(Enum(EngineType), nullable=False)  # v1 只用 DIFY
    mode = Column(Enum(DifyEngineMode), nullable=False, default=DifyEngineMode.EXTERNAL)

    # EXTERNAL 模式必填；MANAGED 模式为空
    base_url = Column(String(512), nullable=True)
    admin_email = Column(String(255), nullable=True)
    admin_password_encrypted = Column(Text, nullable=True)  # Fernet token

    # access_token 缓存（避免每次操作都登录 Dify）
    cached_access_token_encrypted = Column(Text, nullable=True)
    cached_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Langfuse 集成配置（Dify 外接模式 per-EngineConfig）
    # 用户在 Dify workspace 后台配 Langfuse 集成后，把同一组 host + public_key + secret_key 填到这里，
    # manager 调 Langfuse API 按 metadata[app_id] 反查 per-app 用量。
    # secret_key 用 Fernet 加密存（app/core/crypto.py）。
    langfuse_host = Column(String(512), nullable=True)
    langfuse_public_key = Column(String(255), nullable=True)
    langfuse_secret_key_encrypted = Column(Text, nullable=True)  # Fernet token

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    group = relationship("UserGroup", foreign_keys=[group_id])
    creator = relationship("User")


class SmsConfig(Base):
    """短信服务商配置 — multi-config，每行一个 provider，全局一行 is_active=true。

    v1 支持 aliyun/tencent/huawei 3 个 provider；aliyun/tencent 用对应 SDK 调只读
    list 模板 API 探活，huawei 用 httpx + stdlib HMAC-SHA256 签名直接调 RESTful API
    （app/services/sms_providers/）。AK/SK 用 Fernet 加密存（app/core/crypto.py）。
    一个 provider 只能建一条记录（ux_sms_configs_provider unique index）。
    """

    __tablename__ = "sms_configs"
    __table_args__ = (
        # 全局仅一行 is_active=true（partial unique index，PG 14+）
        Index("ix_sms_configs_active", "is_active", unique=True, postgresql_where=text("is_active = TRUE")),
        # 一个 provider 只能建一条记录
        Index("ux_sms_configs_provider", "provider", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String(16), nullable=False)  # aliyun / tencent / huawei
    is_active = Column(Boolean, nullable=False, default=False)

    # 共享字段（所有 provider 必填，schema 层校验；DB 层 nullable 便于 multi-config 切换）
    sign_name = Column(String(64), nullable=True)
    template_code = Column(String(64), nullable=True)

    # 云厂商通用 AK/SK（所有 provider 必填，DB 层 nullable，schema 层校验）
    access_key_id_encrypted = Column(Text, nullable=True)
    access_key_secret_encrypted = Column(Text, nullable=True)

    # provider 特定字段（按 provider 切换使用）
    sdk_app_id = Column(String(128), nullable=True)  # 腾讯云 SmsSdkAppId
    region = Column(String(64), nullable=True)  # 阿里云 region_id / 华为云 region

    # 风控参数
    daily_limit = Column(Integer, nullable=False, default=1000)
    interval_seconds = Column(Integer, nullable=False, default=60)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    creator = relationship("User")


class EmailConfig(Base):
    """邮件服务商配置 — multi-config，每行一个 provider，全局一行 is_active=true。

    v1 支持 smtp + aliyun/tencent/huawei 4 个 provider；smtp 用 stdlib smtplib 探活，
    云厂商用对应 SDK 调只读 list API 探活（app/services/email_providers/）。
    AK/SK + SMTP 密码均用 Fernet 加密存（app/core/crypto.py）。
    一个 provider 只能建一条记录（ux_email_configs_provider unique index）。
    """

    __tablename__ = "email_configs"
    __table_args__ = (
        # 全局仅一行 is_active=true（partial unique index，PG 14+）
        Index("ix_email_configs_active", "is_active", unique=True, postgresql_where=text("is_active = TRUE")),
        # 一个 provider 只能建一条记录
        Index("ux_email_configs_provider", "provider", unique=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider = Column(String(16), nullable=False)  # smtp / aliyun / tencent / huawei
    is_active = Column(Boolean, nullable=False, default=False)

    # SMTP 特定字段（provider='smtp' 时必填，其他 provider 为 NULL）
    smtp_host = Column(String(255), nullable=True)
    smtp_port = Column(Integer, nullable=True, default=465)
    encryption = Column(String(16), nullable=True, default="ssl")  # none / ssl / starttls
    username = Column(String(255), nullable=True)  # SMTP login username = 发件邮箱
    password_encrypted = Column(Text, nullable=True)  # SMTP 密码 / 授权码

    # 云厂商通用字段（provider IN aliyun/tencent/huawei 时必填）
    access_key_id_encrypted = Column(Text, nullable=True)
    access_key_secret_encrypted = Column(Text, nullable=True)
    region = Column(String(64), nullable=True)  # aliyun: cn-hangzhou; tencent: ap-hongkong; huawei: cn-north-4
    from_email = Column(String(255), nullable=True)  # 云厂商发信地址

    # 共享字段
    from_name = Column(String(128), nullable=True)  # 发件人显示名，如「知行平台」
    daily_limit = Column(Integer, nullable=False, default=200)
    interval_seconds = Column(Integer, nullable=False, default=60)

    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    creator = relationship("User")


class OperationLog(Base):
    """admin 后台写操作审计日志。手动在 service 层 commit 前埋点
    （log_operation helper），与业务变更同事务 commit 保证一致性。
    平台级操作（如登录）group_id 为 null。"""
    __tablename__ = "operation_logs"
    __table_args__ = (
        Index("ix_oplog_group_created", "group_id", "created_at"),
        Index("ix_oplog_actor_created", "actor_id", "created_at"),
        Index("ix_oplog_target", "target_type", "target_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    # actor_id nullable + ON DELETE SET NULL：用户被删时审计日志保留（actor_id 置 NULL）。
    # 审计场景要求"用户已删除但日志仍在"，NOT NULL 会导致 SET NULL FK 约束冲突。
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String(128), nullable=False)
    target_type = Column(String(64), nullable=False)
    target_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(String(16), nullable=False, default="success")
    detail = Column(JSON, nullable=True, default=dict)
    # 操作者 IP（从请求头 X-Forwarded-For/X-Real-For 或 request.client.host 提取，
    # 由 middleware set 到 contextvar，log_operation 自动读取）。
    operator_ip = Column(String(64), nullable=True)
    # 操作者 User-Agent（从请求头 User-Agent 提取，由 middleware set 到 contextvar）。
    # 用于识别客户端类型（浏览器/curl/SDK）+ 版本，便于安全审计追溯异常来源。
    operator_user_agent = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)


# 渠道↔规则 关联表（沿用 user_roles / role_permissions / user_group_members 的 Table() 约定）。
# 必须在 AlertRule / AlertChannel 之前定义，二者 secondary= 直接引用 Table 对象。
channel_rule_subscriptions = Table(
    "channel_rule_subscriptions", Base.metadata,
    Column("channel_id", UUID(as_uuid=True), ForeignKey("alert_channels.id", ondelete="CASCADE"), primary_key=True),
    Column("rule_id", UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="CASCADE"), primary_key=True),
)


class AlertRule(Base):
    """异常告警规则配置。/alerts 端点读 DB 替代硬编码常量，admin 后台可编辑阈值/启停。

    rule_type 取值（按 category 分组，5 大类 16 子规则）：
      - tracing: error_trace / high_latency / high_tokens
      - resource: high_cpu / high_memory / high_disk / pod_restart
      - service_health: service_down / high_p95_latency / low_uptime
      - usage: high_daily_tokens / high_monthly_cost / high_agent_tokens
      - call_analysis: low_success_rate / high_p95_call_latency / high_avg_tokens_per_request

    threshold 语义随 rule_type 不同（ms / tokens / % / USD / 次数）；error_trace 与
    service_down 无阈值。低类规则（low_uptime / low_success_rate）阈值比较方向反向——
    值低于阈值才触发。

    通知渠道不挂在规则上，独立成 alert_channels 实体（渠道订阅规则，见 AlertChannel）。
    """
    __tablename__ = "alert_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(64), nullable=False)
    category = Column(String(32), nullable=False, default="tracing", index=True)
    rule_type = Column(String(32), nullable=False, index=True)
    threshold = Column(Integer, nullable=True)  # error_trace / service_down 无需阈值
    enabled = Column(Boolean, default=True, nullable=False)
    severity = Column(String(16), default="warning", nullable=False)  # critical / warning
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, onupdate=utcnow)

    # 反向关联：订阅了本规则的渠道（check_and_notify 查询用，不懒加载子集合，async 下不触发 greenlet）。
    channels = relationship(
        "AlertChannel", secondary=channel_rule_subscriptions, back_populates="rules"
    )


class AlertChannel(Base):
    """告警通知渠道（独立实体，订阅规则）。

    一个渠道 = 一个飞书群 / 钉钉群 / 企微群 / 邮箱组，可订阅多条规则或 subscribed_all=true 收所有。
    config 按 channel_type 分化：
      - feishu/dingtalk/wecom: {"webhook_url": "https://..."}
      - email: {"to": ["a@b.com", "c@d.com"]}
    webhook URL/邮箱地址敏感，仅平台管理员可见。
    """
    __tablename__ = "alert_channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    channel_type = Column(String(20), nullable=False)  # feishu/dingtalk/wecom/email
    config = Column(JSON, default=dict, nullable=False)
    subscribed_all = Column(Boolean, default=False, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, onupdate=utcnow)

    # 订阅的规则列表（subscribed_all=true 时此关联不写入，运行时短路）
    rules = relationship(
        "AlertRule", secondary=channel_rule_subscriptions, back_populates="channels"
    )


class AlertEvent(Base):
    """告警事件记录（带状态机）。

    状态流转：
      firing（默认）→ resolved（A 类规则指标恢复后后台轮询自动标记）
      firing → acknowledged（人工确认，正交状态，不发重复通知但保留 firing/resolved 语义）
      acknowledged → resolved（A 类恢复时仍可被自动标记）

    A 类规则（resource/service_health/call_analysis 共 10 条）指标可降即恢复；
    B 类（tracing 3 条）单次 trace 异常不可恢复；C 类（usage 3 条）累积值周期结束自然清零。

    rule_id ondelete=SET NULL：删规则时事件保留为历史，rule_name 冗余存仍可读。
    notified_channels 不存 webhook URL/邮箱地址，只存 [{type, name?, ok, error?}]。
    """
    __tablename__ = "alert_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True, index=True)
    rule_name = Column(String(64), nullable=False)
    rule_type = Column(String(32), nullable=False)
    trace_id = Column(String(64), nullable=True, index=True)
    agent_id = Column(String(64), nullable=True)
    severity = Column(String(16), nullable=False)
    message = Column(Text, nullable=False)
    notified_channels = Column(JSON, default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    # 状态机字段（0.8.66）
    status = Column(String(16), default="firing", nullable=False, index=True)  # firing/resolved/acknowledged
    acknowledged_by = Column(String(64), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)  # 仍触发时每次轮询更新
    resolved_at = Column(DateTime(timezone=True), nullable=True)


class RolloutStatus(str, enum.Enum):
    """引擎镜像滚动发布状态。"""
    RUNNING = "RUNNING"        # 后台分批执行中
    FINISHED = "FINISHED"      # 全部处理完（允许部分 item failed）
    FAILED = "FAILED"          # 整体失败（如目标镜像无效、K8s 不可达）


class RolloutItemStatus(str, enum.Enum):
    """单个引擎在一次 rollout 中的处理状态。"""
    PENDING = "PENDING"        # 已入队待处理
    PATCHED = "PATCHED"        # 已 patch image（SUSPENDED 实例到此结束，不等 ready）
    READY = "READY"            # RUNNING 实例 patch 后 pod 已 ready 且用上新镜像
    FAILED = "FAILED"          # patch 或等 ready 超时/出错
    SKIPPED = "SKIPPED"        # 跳过（ARCHIVED 无资源 / DIFY 外部 / 镜像已是目标值）


class EngineRollout(Base):
    """引擎镜像滚动发布记录。

    发版后存量引擎 Deployment 的 image 是创建时烘入的旧值，不随 manager 的 UA_ENGINE_IMAGE
    更新。本表记录一次「把所有引擎 Deployment 的 image 批量滚到目标镜像」的操作，后台分批
    执行，避免同步 HTTP 超时。状态分类（对接 C2 状态机）：RUNNING→patch+等ready；
    SUSPENDED→只 patch 不等 ready（下次 resume 拉新镜像）；ARCHIVED/DIFY→跳过。
    """
    __tablename__ = "engine_rollouts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engine_type = Column(String(16), nullable=True)  # HERMES/OPENCLAW；None=全部
    target_image = Column(String(512), nullable=False)
    status = Column(String(16), default=RolloutStatus.RUNNING.value, nullable=False, index=True)
    batch_size = Column(Integer, nullable=False, default=5)
    force_repull = Column(Boolean, nullable=False, default=False)
    dry_run = Column(Boolean, nullable=False, default=False)
    summary = Column(JSON, default=dict, nullable=False)  # {total, ready, patched, failed, skipped}
    triggered_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    items = relationship("EngineRolloutItem", back_populates="rollout", cascade="all, delete-orphan")


class EngineRolloutItem(Base):
    """单引擎 rollout 处理明细。"""
    __tablename__ = "engine_rollout_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rollout_id = Column(UUID(as_uuid=True), ForeignKey("engine_rollouts.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(64), nullable=False)  # = str(instance_id)，用于 _engine_name
    deployment_name = Column(String(128), nullable=False)
    prev_image = Column(String(512), nullable=True)
    engine_status = Column(String(16), nullable=True)  # 处理时该引擎的 DB 状态（RUNNING/SUSPENDED/...）
    status = Column(String(16), default=RolloutItemStatus.PENDING.value, nullable=False, index=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    rollout = relationship("EngineRollout", back_populates="items")


class VerificationCode(Base):
    """验证码 — 6 位数字 OTP，bcrypt hash 存。10min 有效，5 次错误失效。

    Phase 1（0.8.104+）：用于忘记密码 / 改绑手机邮箱 / 账号锁定邮件解锁场景。
    code_hash 用 app.core.auth.hash_password 落库（bcrypt cost 12），不存明文防 DB dump 泄露。
    """

    __tablename__ = "verification_codes"
    __table_args__ = (
        Index("ix_verification_codes_lookup", "target", "purpose", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel = Column(String(8), nullable=False)  # sms / email
    target = Column(String(256), nullable=False)  # phone 或 email
    purpose = Column(String(32), nullable=False)  # reset_password / change_phone / change_email / account_unlock
    code_hash = Column(String(256), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class VerificationTicket(Base):
    """验证码校验通过后的临时凭证 — 单次使用，10min 有效。

    reset-password / unlock-account endpoint 用 ticket 反查 user（不信任前端传 target）。
    purpose + target 校验防 ticket 跨场景重放（reset_password ticket 不能调 unlock-account）。
    """

    __tablename__ = "verification_tickets"
    __table_args__ = (
        Index("ix_verification_tickets_lookup", "target", "purpose", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_id = Column(UUID(as_uuid=True), ForeignKey("verification_codes.id", ondelete="CASCADE"), nullable=False)
    purpose = Column(String(32), nullable=False)
    target = Column(String(256), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)


class AppReleaseStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"


class AppRelease(Base):
    """APP 发布记录——base 安装包（带占位符）+ admin 编辑元数据 + publish 后产出下载包。

    platform 区分 android（.apk）/ harmony（.hap）：
    - android：base APK 捆绑在 manager 镜像（/app/base-apks/*.apk）bootstrap 注册；
      publish 时 ApkPatcher 替换 assets/server_config.json 占位符 + zipalign + 重签名。
    - harmony：admin 手动上传已签名的 .hap（DevEco/hvigor 产出，server_config 在构建期
      已写入真实地址）；publish 不做 patch，原样转存为下载包。
    landing 下载页通过 /api/manager/public/app-releases/latest?platform= 拉取展示。
    (platform, version) 复合唯一约束保证同平台相同 versionName 不重复注册。
    """

    __tablename__ = "app_releases"
    __table_args__ = (
        UniqueConstraint("platform", "version", name="uq_app_releases_platform_version"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    platform = Column(String(16), nullable=False, default="android", index=True)  # android / harmony
    version = Column(String(64), nullable=False)  # 来自安装包 versionName，如 "0.8.123"
    base_apk_object_key = Column(String(512), nullable=False)  # MinIO 私有桶 object key
    patched_apk_object_key = Column(String(512), nullable=True)  # publish 后产出的下载包 object key
    display_name = Column(String(128), nullable=False, default="知行")  # UI 展示名
    description = Column(Text, nullable=False, default="")
    icon_object_key = Column(String(512), nullable=True)  # 公开桶 icon key
    status = Column(String(16), default=AppReleaseStatus.DRAFT.value, nullable=False, index=True)
    manager_url = Column(String(512), nullable=True)  # publish 时写入的目标 URL
    gateway_url = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)


class MessageFeedbackValue(str, enum.Enum):
    UP = "up"
    DOWN = "down"


class MessageFeedbackReason(str, enum.Enum):
    """点踩分类（客户端表单必选其一）。"""
    INACCURATE = "inaccurate"    # 不准确
    HARMFUL = "harmful"          # 有害或不当
    OFF_TOPIC = "off_topic"      # 跑题未解决
    OTHER = "other"              # 其他


class MessageFeedback(Base):
    """消息级用户反馈（赞/踩）— 针对引擎某次回复，业务库为 source of truth。

    消息由引擎管理（不入 manager DB），锚点用 message_ref：
      "mid:{引擎消息id}" 优先（历史消息响应带稳定自增 id），
      "hash:{sha256(content)[:16]}" 兜底（引擎无 id 时）。
    run_id 仅作 Langfuse 镜像用元数据（历史消息无 run_id，反馈仍能落库）。
    content_snapshot 存消息内容快照：列表/分析不依赖引擎会话仍在。
    同一用户对同一会话同一消息只有一条反馈（重复提交即更新 value/reason）。
    Langfuse 镜像：写库成功后异步把 value 作为 score 挂到 run 对应的
    gateway 外层 trace（metadata.run_id 匹配），未配置/无 run_id 时静默跳过。
    """

    __tablename__ = "message_feedbacks"
    __table_args__ = (
        UniqueConstraint("user_id", "session_id", "message_ref", name="uq_message_feedbacks_user_msg"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_id = Column(String(64), nullable=False)  # 引擎实例 id
    session_id = Column(String(128), nullable=False, index=True)
    message_ref = Column(String(128), nullable=False)
    run_id = Column(String(64), nullable=True)
    value = Column(String(8), nullable=False)  # up / down
    reason = Column(String(32), nullable=True)  # down 时必选：inaccurate/harmful/off_topic/other
    comment = Column(Text, nullable=True)
    content_snapshot = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, onupdate=utcnow)


class MessageFavorite(Base):
    """消息级收藏 — 用户书签，「我的收藏」列表数据源。

    锚点与快照策略同 MessageFeedback。收藏不镜像 Langfuse（书签非质量信号）。
    """

    __tablename__ = "message_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "session_id", "message_ref", name="uq_message_favorites_user_msg"),
        Index("ix_message_favorites_user_created", "user_id", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    agent_id = Column(String(64), nullable=False)
    session_id = Column(String(128), nullable=False)
    message_ref = Column(String(128), nullable=False)
    run_id = Column(String(64), nullable=True)
    content_snapshot = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
