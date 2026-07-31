"""Shared database models used across services.

AgentSession and AgentDeployment are used by both Manager (CRUD)
and Controller (lifecycle management).
"""

from sqlalchemy import Column, String, Enum, Text, DateTime, Integer, UniqueConstraint, Boolean, JSON, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase
from pkg.common.utils import utcnow
import uuid
import enum


class Base(DeclarativeBase):
    pass


class DeploymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    DEPLOYING = "DEPLOYING"
    RUNNING = "RUNNING"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


# These models reference the SAME TABLES as in services/manager/app/models/__init__.py
# but WITHOUT ForeignKey constraints (Controller doesn't need referential integrity).
# The table names must match exactly.

class AgentDeployment(Base):
    """智能体引擎部署状态 — 无外键约束版本（供 Controller 使用）"""
    __tablename__ = "agent_deployments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    group_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    resource_pool_id = Column(UUID(as_uuid=True), nullable=True)
    status = Column(Enum(DeploymentStatus), default=DeploymentStatus.PENDING, nullable=False)
    scope_type = Column(String(16), nullable=False, default="ALL")
    scope_target_id = Column(UUID(as_uuid=True), nullable=True)
    pod_name = Column(String(256), nullable=True)
    namespace = Column(String(128), default="unionagents")
    engine_url = Column(String(512), nullable=True)
    internal_port_map = Column(JSON, default=dict)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    last_active_at = Column(DateTime(timezone=True), nullable=True)
    backup_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archive_path = Column(String(1024), nullable=True)
    node_name = Column(String(256), nullable=True)
    error_message = Column(Text, nullable=True)


class AgentProfile(Base):
    """Hermes Profile 映射 — 无外键约束版本（供 Controller 使用）"""
    __tablename__ = "agent_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    resource_pool_id = Column(UUID(as_uuid=True), nullable=False)
    deployment_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    profile_name = Column(String(256), nullable=False)
    profile_type = Column(String(16), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=True)
    group_id = Column(UUID(as_uuid=True), nullable=True)
    hermes_home = Column(String(512), nullable=False)
    internal_port = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    config_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow)


class ResourceMetricSample(Base):
    """引擎 Pod 资源用量时序采样 — 无外键约束版本（供 Controller 写入）。

    与 services/manager/app/models/__init__.py 的 ResourceMetricSample 同表。
    Controller 的 metric_sampler 每 1 分钟写入一条/pod。
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
    instance_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    ts = Column(DateTime(timezone=True), nullable=False, index=True)
    cpu_m = Column(Integer, nullable=False, default=0)
    memory_mi = Column(Integer, nullable=False, default=0)


# Re-export for convenience
__all__ = ["Base", "DeploymentStatus", "AgentDeployment", "AgentProfile", "ResourceMetricSample"]
