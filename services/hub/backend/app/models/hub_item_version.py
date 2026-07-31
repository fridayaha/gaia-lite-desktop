import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import HubItemVersionStatus, RiskLevel
from app.db.base import Base


class HubItemVersion(Base):
    __tablename__ = "hub_item_versions"
    __table_args__ = (
        Index("ix_hub_item_versions_item_version", "hub_item_id", "version", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hub_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_items.id"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    manifest_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_schema: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    permission_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    runtime_compatibility: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[HubItemVersionStatus] = mapped_column(
        nullable=False, default=HubItemVersionStatus.draft
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        nullable=False, default=RiskLevel.low
    )
    package_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    change_log: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    hub_item: Mapped["HubItem"] = relationship(
        "HubItem", back_populates="versions"
    )
