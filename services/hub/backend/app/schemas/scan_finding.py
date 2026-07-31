import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import FindingSeverity


class ScanFindingCreate(BaseModel):
    scan_report_id: uuid.UUID
    risk_type: str = Field(max_length=100)
    severity: FindingSeverity
    file_path: str | None = Field(default=None, max_length=500)
    evidence: dict | None = None
    recommendation: str | None = None


class ScanFindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scan_report_id: uuid.UUID
    risk_type: str
    severity: FindingSeverity
    file_path: str | None
    evidence: dict | None
    recommendation: str | None
    created_at: datetime
