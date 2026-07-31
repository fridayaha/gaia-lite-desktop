import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import HubItemVersionStatus, RiskLevel


class HubItemVersionCreate(BaseModel):
    hub_item_id: uuid.UUID
    version: str = Field(max_length=50)
    description: str | None = None
    manifest_json: dict | None = None
    config_json: dict | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    permission_json: dict | None = None
    runtime_compatibility: dict | None = None
    risk_level: RiskLevel = RiskLevel.low
    package_hash: str | None = Field(default=None, max_length=256)
    change_log: dict | None = None
    created_by: str | None = Field(default=None, max_length=100)


class HubItemVersionUpdate(BaseModel):
    description: str | None = None
    manifest_json: dict | None = None
    config_json: dict | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    permission_json: dict | None = None
    runtime_compatibility: dict | None = None
    risk_level: RiskLevel | None = None
    package_hash: str | None = Field(default=None, max_length=256)
    change_log: dict | None = None


class HubItemVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_item_id: uuid.UUID
    version: str
    description: str | None
    manifest_json: dict | None
    config_json: dict | None
    input_schema: dict | None
    output_schema: dict | None
    permission_json: dict | None
    runtime_compatibility: dict | None
    status: HubItemVersionStatus
    risk_level: RiskLevel
    package_hash: str | None
    change_log: dict | None
    created_by: str | None
    organization_id: str | None
    workspace_id: str | None
    created_at: datetime
    updated_at: datetime
