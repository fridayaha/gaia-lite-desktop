import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import EventType


class LifecycleEventCreate(BaseModel):
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID | None = None
    event_type: EventType
    from_status: str | None = Field(default=None, max_length=50)
    to_status: str | None = Field(default=None, max_length=50)
    operator: str | None = Field(default=None, max_length=100)
    reason: str | None = None


class LifecycleEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_item_id: uuid.UUID
    hub_item_version_id: uuid.UUID | None
    event_type: EventType
    from_status: str | None
    to_status: str | None
    operator: str | None
    reason: str | None
    created_at: datetime
