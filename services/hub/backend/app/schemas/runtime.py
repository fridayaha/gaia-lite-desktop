import uuid

from pydantic import BaseModel, Field


class RuntimeItemBrief(BaseModel):
    id: uuid.UUID
    name: str
    type: str


class RuntimeDiscoverFilters(BaseModel):
    type: str | None = None
    keyword: str | None = None
    risk_level_max: str = "high"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class RuntimeCapabilitySummary(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    description: str | None
    version: str
    risk_level: str


class RuntimeDiscoverResponse(BaseModel):
    items: list[RuntimeCapabilitySummary]
    total: int


class RuntimeRelationSummary(BaseModel):
    relation_type: str
    target_item: RuntimeItemBrief
    required: bool


class RuntimeDependencyNode(BaseModel):
    item: RuntimeItemBrief
    relation_type: str
    required: bool
    depth: int
    source_item_id: uuid.UUID
    available: bool
    warnings: list[str] = Field(default_factory=list)


class RuntimeDependencyWarning(BaseModel):
    source_item_id: uuid.UUID | None = None
    target_item_id: uuid.UUID | None = None
    relation_type: str | None = None
    required: bool | None = None
    depth: int | None = None
    warning_type: str
    detail: str


class RuntimeCapabilityResolve(BaseModel):
    id: uuid.UUID
    name: str
    type: str
    description: str | None
    version: str
    status: str
    risk_level: str
    manifest_json: dict | None
    config_json: dict | None
    input_schema: dict | None
    output_schema: dict | None
    permission_json: dict | None
    runtime_compatibility: dict | None
    relations: list[RuntimeRelationSummary]
    dependencies: list[RuntimeDependencyNode] = Field(default_factory=list)
    dependency_warnings: list[RuntimeDependencyWarning] = Field(default_factory=list)


class FunctionDefinition(BaseModel):
    name: str
    description: str
    parameters: dict


class FunctionCallingToolDefinition(BaseModel):
    type: str = "function"
    function: FunctionDefinition
