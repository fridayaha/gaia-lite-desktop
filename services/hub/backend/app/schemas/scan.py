import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RiskLevel
from app.schemas.scan_finding import ScanFindingRead


class ScanRequest(BaseModel):
    operator: str | None = Field(
        default=None,
        description=(
            "Compatibility field for operator display name. "
            "This field is NOT used for authorization. "
            "The authenticated identity comes from the X-Actor-ID header. "
            "This field is preserved for audit trail compatibility "
            "and may be deprecated in a future version."
        ),
    )


class ScanReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID
    risk_level: RiskLevel
    summary: dict | None
    scanner_version: str | None
    findings: list[ScanFindingRead] = []
    created_at: datetime
