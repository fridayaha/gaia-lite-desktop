import uuid

from pydantic import BaseModel, Field


class OpenAPIImportResult(BaseModel):
    item_id: str
    name: str
    type: str = "tool"
    version: str


class OpenAPIImportResponse(BaseModel):
    tools_created: int
    items: list[OpenAPIImportResult]
    warnings: list[dict] = Field(default_factory=list)
