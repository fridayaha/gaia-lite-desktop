"""SQLAlchemy 2.0 ORM models for DataSource, Credential, SyncTask, Dataset.

All models follow the same conventions as the Ontology domain models:
- UUID v4 primary keys
- VARCHAR for enums, validated at pydantic layer
- JSONB for flexible/config fields
- FKs use ON DELETE CASCADE (except where noted)
- api_name is unique within its scope
- utcnow() for all timestamps
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ontology.core.models.defaults import JSONBType, new_uuid, utcnow
from ontology.core.models.ontology import (
    Base,  # 统一 Base：所有 ORM 模型共用一个 metadata，供 Alembic autogenerate 对比
)

# ═══════════════════════════════════════════════════════════════════
# Credential
# ═══════════════════════════════════════════════════════════════════


class CredentialModel(Base):
    """Authentication credential for external data sources.

    TODO(SEC-001): AES-256-GCM encrypt secret_data at rest.
    """

    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    credential_type: Mapped[str] = mapped_column(String(50), nullable=False)
    secret_data: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    # ADR-016: credentials belong to a Project (resource ownership).
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Note: no updated_at — credentials are replaced, never modified


# ═══════════════════════════════════════════════════════════════════
# DataSource
# ═══════════════════════════════════════════════════════════════════


class DataSourceModel(Base):
    """External data source instance."""

    __tablename__ = "data_sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    connector_config: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    credential_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="DISCONNECTED")
    gravitino_catalog_name: Mapped[str] = mapped_column(String(255), default="")
    # ADR-016: data sources belong to a Project (resource ownership).
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ═══════════════════════════════════════════════════════════════════
# SyncTask
# ═══════════════════════════════════════════════════════════════════


class SyncTaskModel(Base):
    """Data sync job — DataSource → Iceberg Dataset."""

    __tablename__ = "sync_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    data_source_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sync_type: Mapped[str] = mapped_column(String(20), default="table")  # "table" | "file"
    source_config: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    target_dataset_api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sync_mode: Mapped[str] = mapped_column(String(20), default="full_snapshot")
    transaction_type: Mapped[str] = mapped_column(String(20), default="snapshot")
    allow_schema_changes: Mapped[bool] = mapped_column(Boolean, default=False)
    max_duration_minutes: Mapped[int | None] = mapped_column(default=None)
    file_filters: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    schedule: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    # DRAFT | RUNNING | FINISHED | STOPPED | CANCELED | FAILED
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    pipeline_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # ADR-016: sync tasks belong to a Project (resource ownership).
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# ═══════════════════════════════════════════════════════════════════
# DatasetGovernance
# ═══════════════════════════════════════════════════════════════════


class DatasetGovernanceModel(Base):
    """Platform-level Dataset metadata stored in PG.

    Complements Iceberg's physical table metadata with lineage,
    origin tracking, and resource kind (MANAGED vs VIRTUAL).

    ADR-018 (D5): Pipeline Builder extends this model with snapshot-aware
    fields for versioned dataset management — current_snapshot_id (for
    atomic version switch / rollback), snapshot_retention (how many
    historical snapshots to keep), write_lock (concurrent build guard).
    """

    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    api_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    storage_location: Mapped[str] = mapped_column(String(1024), default="")
    partition_config: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    source_dataset_api_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    data_source_api_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="MANAGED")
    is_view: Mapped[bool] = mapped_column(Boolean, default=False)
    row_count_estimate: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # ADR-018 D5: Pipeline Builder snapshot-aware fields
    # Current visible Iceberg snapshot ID (atomic switch for version rollback)
    current_snapshot_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Number of historical snapshots to retain (default 10, cleanup via maintenance)
    snapshot_retention: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Write lock: pipeline_id currently writing to this dataset (NULL = writable).
    # Protected by PG advisory lock + this field (crash recovery fallback).
    write_lock: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # ADR-016: datasets belong to a Project (resource ownership).
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
