import uuid

from pydantic import BaseModel, Field


class LifecycleActionRequest(BaseModel):
    operator: str = Field(
        min_length=1,
        max_length=100,
        description=(
            "Compatibility field for operator display name. "
            "This field is NOT used for authorization. "
            "The authenticated identity comes from the X-Actor-ID header. "
            "This field is preserved for audit trail compatibility "
            "and may be deprecated in a future version."
        ),
    )
    reason: str | None = None


class RollbackRequest(BaseModel):
    target_version_id: uuid.UUID
    operator: str = Field(
        min_length=1,
        max_length=100,
        description=(
            "Compatibility field for operator display name. "
            "This field is NOT used for authorization. "
            "The authenticated identity comes from the X-Actor-ID header. "
            "This field is preserved for audit trail compatibility "
            "and may be deprecated in a future version."
        ),
    )
    reason: str | None = None
