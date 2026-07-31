import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import HubItemStatus, HubItemType, RiskLevel, SourceType
from app.db.base import Base


class HubItem(Base):
    __tablename__ = "hub_items"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[HubItemType] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("categories.id"), nullable=True
    )
    source_type: Mapped[SourceType] = mapped_column(
        nullable=False, default=SourceType.manual
    )
    status: Mapped[HubItemStatus] = mapped_column(
        nullable=False, default=HubItemStatus.draft
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        nullable=False, default=RiskLevel.low
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    discoverable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    allow_existing_references: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    force_disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    featured: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    visibility_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    category: Mapped["Category | None"] = relationship(
        "Category", back_populates="hub_items"
    )
    versions: Mapped[list["HubItemVersion"]] = relationship(
        "HubItemVersion", back_populates="hub_item"
    )
    tags: Mapped[list["Tag"]] = relationship(
        "Tag", secondary="hub_item_tags", back_populates="hub_items"
    )
    outgoing_relations: Mapped[list["HubItemRelation"]] = relationship(
        "HubItemRelation",
        foreign_keys="HubItemRelation.source_item_id",
        back_populates="source_item",
    )
    incoming_relations: Mapped[list["HubItemRelation"]] = relationship(
        "HubItemRelation",
        foreign_keys="HubItemRelation.target_item_id",
        back_populates="target_item",
    )
