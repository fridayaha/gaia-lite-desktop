import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import RelationScope, RelationType
from app.db.base import Base


class HubItemRelation(Base):
    __tablename__ = "hub_item_relations"

    __table_args__ = (
        UniqueConstraint(
            "source_item_id",
            "target_item_id",
            "relation_type",
            "relation_scope",
            name="uq_relation_source_target_type_scope",
        ),
        Index("ix_relation_source_item_id", "source_item_id"),
        Index("ix_relation_target_item_id", "target_item_id"),
        Index("ix_relation_source_scope", "source_item_id", "relation_scope"),
        Index("ix_relation_target_scope", "target_item_id", "relation_scope"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_items.id"), nullable=False
    )
    target_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_items.id"), nullable=False
    )
    relation_type: Mapped[RelationType] = mapped_column(nullable=False)
    relation_scope: Mapped[RelationScope] = mapped_column(nullable=False)
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source_item: Mapped["HubItem"] = relationship(
        "HubItem", foreign_keys=[source_item_id], back_populates="outgoing_relations"
    )
    target_item: Mapped["HubItem"] = relationship(
        "HubItem", foreign_keys=[target_item_id], back_populates="incoming_relations"
    )
