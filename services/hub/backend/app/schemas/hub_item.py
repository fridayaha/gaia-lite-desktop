import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import HubItemStatus, HubItemType, RiskLevel, SourceType


class HubItemCreate(BaseModel):
    name: str = Field(max_length=200)
    type: HubItemType
    description: str | None = None
    industry: str | None = Field(default=None, max_length=100)
    scenario: str | None = Field(default=None, max_length=100)
    category_id: uuid.UUID | None = None
    source_type: SourceType = SourceType.manual
    risk_level: RiskLevel = RiskLevel.low
    visibility_scope: str | None = Field(default=None, max_length=50)
    created_by: str | None = Field(default=None, max_length=100)
    featured: bool = False


class HubItemUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    industry: str | None = Field(default=None, max_length=100)
    scenario: str | None = Field(default=None, max_length=100)
    category_id: uuid.UUID | None = None
    risk_level: RiskLevel | None = None
    discoverable: bool | None = None
    allow_existing_references: bool | None = None
    force_disabled: bool | None = None
    featured: bool | None = None


class HubItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: HubItemType
    description: str | None
    industry: str | None
    scenario: str | None
    category_id: uuid.UUID | None
    source_type: SourceType
    status: HubItemStatus
    risk_level: RiskLevel
    current_version_id: uuid.UUID | None
    discoverable: bool
    allow_existing_references: bool
    force_disabled: bool
    featured: bool
    tags: list[str] = Field(default_factory=list)
    created_by: str | None
    organization_id: str | None
    workspace_id: str | None
    visibility_scope: str | None
    created_at: datetime
    updated_at: datetime

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_to_names(cls, v):
        """ORM Tag 对象 → name 字符串列表。"""
        if v is None:
            return []
        return [getattr(t, "name", t) for t in v]
