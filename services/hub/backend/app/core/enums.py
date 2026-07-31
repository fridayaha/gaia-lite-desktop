from enum import Enum


class HubItemType(str, Enum):
    agent = "agent"
    mcp = "mcp"
    skill = "skill"
    tool = "tool"


class SourceType(str, Enum):
    preset = "preset"
    manual = "manual"
    upload = "upload"


class HubItemStatus(str, Enum):
    draft = "draft"
    pending_review = "pending_review"
    published = "published"
    rejected = "rejected"
    disabled = "disabled"
    archived = "archived"


class HubItemVersionStatus(str, Enum):
    draft = "draft"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    change_required = "change_required"
    published = "published"
    deprecated = "deprecated"
    archived = "archived"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    blocking = "blocking"


class ApprovalAction(str, Enum):
    submit = "submit"
    approve = "approve"
    reject = "reject"
    request_change = "request_change"


class EventType(str, Enum):
    created = "created"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    change_requested = "change_requested"
    published = "published"
    deprecated = "deprecated"
    rolled_back = "rolled_back"
    disabled = "disabled"
    archived = "archived"
    scanned = "scanned"


class FindingSeverity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RelationType(str, Enum):
    uses = "uses"
    invokes = "invokes"
    depends_on = "depends_on"
    provides = "provides"


class RelationScope(str, Enum):
    management = "management"
    runtime = "runtime"
