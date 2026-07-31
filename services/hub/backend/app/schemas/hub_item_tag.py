import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class HubItemTagCreate(BaseModel):
    hub_item_id: uuid.UUID
    tag_id: uuid.UUID


class HubItemTagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hub_item_id: uuid.UUID
    tag_id: uuid.UUID
    created_at: datetime
