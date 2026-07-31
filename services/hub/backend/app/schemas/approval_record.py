import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ApprovalAction


class ApprovalRecordCreate(BaseModel):
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID
    action: ApprovalAction
    from_status: str | None = Field(default=None, max_length=50)
    to_status: str | None = Field(default=None, max_length=50)
    operator: str = Field(max_length=100)
    comment: str | None = None


class ApprovalRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID
    action: ApprovalAction
    from_status: str | None
    to_status: str | None
    operator: str
    comment: str | None
    created_at: datetime
