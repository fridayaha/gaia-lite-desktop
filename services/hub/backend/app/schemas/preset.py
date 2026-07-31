from pydantic import BaseModel


class PresetItemSummary(BaseModel):
    id: str
    name: str
    type: str
    source_type: str
    status: str


class PresetInitResponse(BaseModel):
    created: int
    skipped: int
    items: list[PresetItemSummary]
