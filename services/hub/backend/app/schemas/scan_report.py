import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RiskLevel


class ScanReportCreate(BaseModel):
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID
    risk_level: RiskLevel
    summary: dict | None = None
    scanner_version: str | None = Field(default=None, max_length=50)


class ScanReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID
    risk_level: RiskLevel
    summary: dict | None
    scanner_version: str | None
    created_at: datetime
