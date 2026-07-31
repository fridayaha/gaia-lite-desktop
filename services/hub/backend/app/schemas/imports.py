from pydantic import BaseModel, Field


class ImportResponse(BaseModel):
    item_id: str
    version_id: str
    name: str
    type: str
    version: str
    status: str
    message: str
    warnings: list[dict] = Field(default_factory=list)
