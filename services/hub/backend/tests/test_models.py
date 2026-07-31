def test_import_models():
    from app.models import (
        ApprovalRecord,
        Category,
        HubItem,
        HubItemTag,
        HubItemVersion,
        LifecycleEvent,
        ScanFinding,
        ScanReport,
        Tag,
    )

    assert HubItem.__tablename__ == "hub_items"
    assert HubItemVersion.__tablename__ == "hub_item_versions"
    assert ApprovalRecord.__tablename__ == "approval_records"
    assert ScanReport.__tablename__ == "scan_reports"
    assert ScanFinding.__tablename__ == "scan_findings"
    assert LifecycleEvent.__tablename__ == "lifecycle_events"
    assert Category.__tablename__ == "categories"
    assert Tag.__tablename__ == "tags"
    assert HubItemTag.__tablename__ == "hub_item_tags"


def test_enums():
    from app.core.enums import (
        ApprovalAction,
        EventType,
        FindingSeverity,
        HubItemStatus,
        HubItemType,
        HubItemVersionStatus,
        RiskLevel,
        SourceType,
    )

    assert HubItemType.agent.value == "agent"
    assert HubItemType.mcp.value == "mcp"
    assert HubItemType.skill.value == "skill"
    assert HubItemType.tool.value == "tool"

    assert SourceType.preset.value == "preset"
    assert SourceType.manual.value == "manual"
    assert SourceType.upload.value == "upload"

    assert HubItemStatus.draft.value == "draft"
    assert HubItemStatus.pending_review.value == "pending_review"
    assert HubItemStatus.published.value == "published"
    assert HubItemStatus.rejected.value == "rejected"
    assert HubItemStatus.disabled.value == "disabled"
    assert HubItemStatus.archived.value == "archived"

    assert HubItemVersionStatus.draft.value == "draft"
    assert HubItemVersionStatus.pending_review.value == "pending_review"
    assert HubItemVersionStatus.approved.value == "approved"
    assert HubItemVersionStatus.rejected.value == "rejected"
    assert HubItemVersionStatus.change_required.value == "change_required"
    assert HubItemVersionStatus.published.value == "published"
    assert HubItemVersionStatus.deprecated.value == "deprecated"
    assert HubItemVersionStatus.archived.value == "archived"

    assert RiskLevel.low.value == "low"
    assert RiskLevel.medium.value == "medium"
    assert RiskLevel.high.value == "high"
    assert RiskLevel.blocking.value == "blocking"

    assert ApprovalAction.submit.value == "submit"
    assert ApprovalAction.approve.value == "approve"
    assert ApprovalAction.reject.value == "reject"
    assert ApprovalAction.request_change.value == "request_change"

    assert EventType.created.value == "created"
    assert EventType.submitted.value == "submitted"
    assert EventType.approved.value == "approved"
    assert EventType.rejected.value == "rejected"
    assert EventType.change_requested.value == "change_requested"
    assert EventType.published.value == "published"
    assert EventType.deprecated.value == "deprecated"
    assert EventType.rolled_back.value == "rolled_back"
    assert EventType.disabled.value == "disabled"
    assert EventType.archived.value == "archived"
    assert EventType.scanned.value == "scanned"

    assert FindingSeverity.low.value == "low"
    assert FindingSeverity.medium.value == "medium"
    assert FindingSeverity.high.value == "high"
    assert FindingSeverity.critical.value == "critical"


def test_schema_config():
    from app.schemas.hub_item import HubItemRead
    from app.schemas.hub_item_version import HubItemVersionRead

    assert HubItemRead.model_config.get("from_attributes") is True
    assert HubItemVersionRead.model_config.get("from_attributes") is True


def test_unique_constraints():
    from app.models import HubItemTag, HubItemVersion

    version_args = HubItemVersion.__table_args__
    assert version_args is not None
    has_unique = any(
        getattr(idx, "unique", False) for idx in version_args
    )
    assert has_unique

    tag_args = HubItemTag.__table_args__
    assert tag_args is not None
    has_unique = any(
        getattr(idx, "unique", False) for idx in tag_args
    )
    assert has_unique
