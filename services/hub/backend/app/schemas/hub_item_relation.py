import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import HubItemType, RelationScope, RelationType


class HubItemBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    type: HubItemType


class RelationCreate(BaseModel):
    source_item_id: uuid.UUID
    target_item_id: uuid.UUID
    relation_type: RelationType
    relation_scope: RelationScope = RelationScope.management
    required: bool = False
    description: str | None = None
    created_by: str | None = Field(default=None, max_length=100)


class RelationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_item_id: uuid.UUID
    target_item_id: uuid.UUID
    relation_type: RelationType
    relation_scope: RelationScope
    required: bool
    description: str | None
    source_item: HubItemBrief
    target_item: HubItemBrief
    created_by: str | None
    created_at: datetime


class RelationListResponse(BaseModel):
    items: list[RelationRead]
    total: int


class ItemRelationsResponse(BaseModel):
    outgoing: list[RelationRead]
    incoming: list[RelationRead]
