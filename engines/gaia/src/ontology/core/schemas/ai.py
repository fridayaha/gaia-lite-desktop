"""AI suggestion schemas — LLM-generated ontology suggestions for form pre-fill.

These schemas define the structured output that the LLM produces.
They align with the existing ObjectTypeCreate / PropertyDefCreate shapes
so AI suggestions can be fed directly into the batch-create API.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class SuggestionDataType(StrEnum):
    """Data types the LLM is allowed to suggest."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    LONG = "LONG"
    SHORT = "SHORT"
    BOOLEAN = "BOOLEAN"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    ARRAY = "ARRAY"
    STRUCT = "STRUCT"


class AiPropertySuggestion(BaseModel):
    """A single property suggested by the AI."""

    api_name: str = Field(description="snake_case English identifier, e.g. 'order_status'")
    display_name: str = Field(description="Human-readable Chinese name, e.g. '订单状态'")
    description: str = Field(default="", description="What this property represents")
    data_type: SuggestionDataType = Field(
        description="Data type. Use DECIMAL for monetary amounts, TIMESTAMP for time points, STRING for text."
    )
    is_primary_key: bool = Field(
        default=False,
        description="True for the unique identifier property (usually 'id')",
    )
    is_title_property: bool = Field(
        default=False,
        description="True for the property used as the display title in UI",
    )
    indexed: bool = Field(
        default=False,
        description="True for properties commonly used in filtering/searching",
    )


class AiLinkSuggestion(BaseModel):
    """A relationship suggested by the AI."""

    api_name: str = Field(description="snake_case identifier, e.g. 'belongs_to_customer'")
    display_name: str = Field(description="Human-readable name, e.g. '所属客户'")
    target_object_type: str = Field(description="api_name of the related object type, e.g. 'customer'")
    cardinality: Literal["ONE", "MANY"] = Field(default="ONE", description="ONE for 1:1, MANY for N:1")


class AiObjectTypeSuggestion(BaseModel):
    """A complete object type definition suggested by the AI.

    This is the primary output format.  The LLM produces one or more of these
    based on the user's business description.
    """

    api_name: str = Field(description="snake_case English identifier, e.g. 'work_order'")
    display_name: str = Field(description="Human-readable Chinese name, e.g. '工单'")
    description: str = Field(description="Business meaning of this object type, 1-2 sentences")
    storage_type: Literal["MANAGED", "VIRTUAL"] = Field(
        default="MANAGED",
        description="MANAGED for data-backed objects, VIRTUAL for computed/derived views",
    )
    properties: list[AiPropertySuggestion] = Field(
        description="Properties of this object type. Must include at least one primary key.",
        min_length=1,
    )
    links: list[AiLinkSuggestion] = Field(
        default_factory=list,
        description="Relationships to other object types (if applicable)",
    )


class AiGenerateRequest(BaseModel):
    """Request body for the AI generation endpoint."""

    description: str = Field(
        description="Natural language description of the business domain, e.g. '汽车制造领域，需要管理车型配置'",
        min_length=3,
    )


class AiGenerateResponse(BaseModel):
    """Response from the AI generation endpoint."""

    suggestions: list[AiObjectTypeSuggestion] = Field(description="AI-generated object type suggestions")
