from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import HubItemStatus, HubItemType, RiskLevel, SourceType
from app.schemas.hub_item import HubItemRead


class HubItemListFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: HubItemType | None = None
    status: HubItemStatus | None = None
    risk_level: RiskLevel | None = None
    source_type: SourceType | None = None
    keyword: str | None = None
    featured: bool | None = None


class HubItemListResponse(BaseModel):
    items: list[HubItemRead]
    total: int
