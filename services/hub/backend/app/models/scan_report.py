import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, JSON, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import RiskLevel
from app.db.base import Base


class ScanReport(Base):
    __tablename__ = "scan_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    hub_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("hub_items.id"), nullable=False
    )
    hub_item_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("hub_item_versions.id"),
        nullable=False,
    )
    risk_level: Mapped[RiskLevel] = mapped_column(nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scanner_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    findings: Mapped[list["ScanFinding"]] = relationship(
        "ScanFinding", back_populates="scan_report"
    )
