from sqlalchemy.orm import Session

from app.core.enums import (
    EventType,
    HubItemStatus,
    HubItemType,
    HubItemVersionStatus,
    RiskLevel,
)
from app.models.approval_record import ApprovalRecord
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent
from app.models.scan_report import ScanReport


def _make_item(
    db: Session,
    name: str = "TenantItem",
    org_id: str = "org-a",
    ws_id: str = "ws-a",
) -> HubItem:
    item = HubItem(
        name=name,
        type=HubItemType.tool,
        status=HubItemStatus.draft,
        risk_level=RiskLevel.low,
        organization_id=org_id,
        workspace_id=ws_id,
    )
    db.add(item)
    db.flush()
    return item


def _make_version(
    db: Session,
    item: HubItem,
    ver: str = "1.0.0",
    status: HubItemVersionStatus = HubItemVersionStatus.draft,
) -> HubItemVersion:
    version = HubItemVersion(
        hub_item_id=item.id,
        version=ver,
        status=status,
        risk_level=RiskLevel.low,
        organization_id=item.organization_id,
        workspace_id=item.workspace_id,
    )
    db.add(version)
    db.flush()
    return version


def _add_scan_report(db: Session, version: HubItemVersion) -> ScanReport:
    report = ScanReport(
        hub_item_id=version.hub_item_id,
        hub_item_version_id=version.id,
        risk_level=RiskLevel.low,
        summary={},
        scanner_version="test",
        organization_id=version.organization_id,
        workspace_id=version.workspace_id,
    )
    db.add(report)
    db.flush()
    return report


class TestSubmitLifecycleEvents:
    def test_submit_item_writes_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-sub", ws_id="ws-sub")

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.submit_item(item.id, "operator-test")

        events = (
            db_session.query(LifecycleEvent)
            .filter(LifecycleEvent.hub_item_id == item.id)
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id == "org-sub"
            assert e.workspace_id == "ws-sub"

    def test_submit_version_writes_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-v", ws_id="ws-v")
        version = _make_version(db_session, item)
        _add_scan_report(db_session, version)

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.submit_version(version.id, "operator-test")

        events = (
            db_session.query(LifecycleEvent)
            .filter(LifecycleEvent.hub_item_version_id == version.id)
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id == "org-v"
            assert e.workspace_id == "ws-v"


class TestApproveLifecycleAndAudit:
    def test_approve_writes_approval_record_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-approve", ws_id="ws-approve")
        version = _make_version(db_session, item, status=HubItemVersionStatus.pending_review)
        _add_scan_report(db_session, version)

        from app.services.approval_service import ApprovalService
        svc = ApprovalService(db_session)
        svc.approve_version(version.id, "approver")

        records = (
            db_session.query(ApprovalRecord)
            .filter(ApprovalRecord.hub_item_version_id == version.id)
            .all()
        )
        assert len(records) >= 1
        for r in records:
            assert r.organization_id == "org-approve"
            assert r.workspace_id == "ws-approve"

    def test_approve_writes_lifecycle_event_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-ae", ws_id="ws-ae")
        version = _make_version(db_session, item, status=HubItemVersionStatus.pending_review)
        _add_scan_report(db_session, version)

        from app.services.approval_service import ApprovalService
        svc = ApprovalService(db_session)
        svc.approve_version(version.id, "approver")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_version_id == version.id,
                LifecycleEvent.event_type == EventType.approved,
            )
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id == "org-ae"
            assert e.workspace_id == "ws-ae"


class TestRejectLifecycleAndAudit:
    def test_reject_writes_approval_record_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-rej", ws_id="ws-rej")
        version = _make_version(db_session, item, status=HubItemVersionStatus.pending_review)
        _add_scan_report(db_session, version)

        from app.services.approval_service import ApprovalService
        svc = ApprovalService(db_session)
        svc.reject_version(version.id, "approver")

        records = (
            db_session.query(ApprovalRecord)
            .filter(ApprovalRecord.hub_item_version_id == version.id)
            .all()
        )
        assert len(records) >= 1
        for r in records:
            assert r.organization_id == "org-rej"
            assert r.workspace_id == "ws-rej"

    def test_reject_writes_lifecycle_event_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-rej-ev", ws_id="ws-rej-ev")
        version = _make_version(db_session, item, status=HubItemVersionStatus.pending_review)
        _add_scan_report(db_session, version)

        from app.services.approval_service import ApprovalService
        svc = ApprovalService(db_session)
        svc.reject_version(version.id, "approver")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_version_id == version.id,
                LifecycleEvent.event_type == EventType.rejected,
            )
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id == "org-rej-ev"
            assert e.workspace_id == "ws-rej-ev"


class TestRequestChangeLifecycleAndAudit:
    def test_request_change_writes_approval_record_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-rc", ws_id="ws-rc")
        version = _make_version(db_session, item, status=HubItemVersionStatus.pending_review)
        _add_scan_report(db_session, version)

        from app.services.approval_service import ApprovalService
        svc = ApprovalService(db_session)
        svc.request_change(version.id, "approver")

        records = (
            db_session.query(ApprovalRecord)
            .filter(ApprovalRecord.hub_item_version_id == version.id)
            .all()
        )
        assert len(records) >= 1
        for r in records:
            assert r.organization_id == "org-rc"
            assert r.workspace_id == "ws-rc"

    def test_request_change_writes_lifecycle_event_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-rc-ev", ws_id="ws-rc-ev")
        version = _make_version(db_session, item, status=HubItemVersionStatus.pending_review)
        _add_scan_report(db_session, version)

        from app.services.approval_service import ApprovalService
        svc = ApprovalService(db_session)
        svc.request_change(version.id, "approver")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_version_id == version.id,
                LifecycleEvent.event_type == EventType.change_requested,
            )
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id == "org-rc-ev"
            assert e.workspace_id == "ws-rc-ev"


class TestPublishLifecycleEvents:
    def test_publish_writes_lifecycle_events_with_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-pub", ws_id="ws-pub")
        version = _make_version(db_session, item, status=HubItemVersionStatus.approved)
        _add_scan_report(db_session, version)

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.publish_version(version.id, "publisher")

        events = (
            db_session.query(LifecycleEvent)
            .filter(LifecycleEvent.hub_item_id == item.id)
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id == "org-pub"
            assert e.workspace_id == "ws-pub"


class TestDisableLifecycleEvents:
    def test_disable_writes_lifecycle_event_with_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-disable", ws_id="ws-disable")
        db_session.refresh(item)
        from app.core.enums import HubItemStatus
        item.status = HubItemStatus.published
        item.current_version_id = None
        db_session.flush()

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.disable_item(item.id, "admin")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_id == item.id,
                LifecycleEvent.event_type == EventType.disabled,
            )
            .all()
        )
        assert len(events) >= 1
        assert events[0].organization_id == "org-disable"
        assert events[0].workspace_id == "ws-disable"


class TestArchiveLifecycleEvents:
    def test_archive_writes_lifecycle_event_with_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-archive", ws_id="ws-archive")
        db_session.refresh(item)
        item.status = HubItemStatus.disabled
        item.current_version_id = None
        db_session.flush()

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.archive_item(item.id, "admin")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_id == item.id,
                LifecycleEvent.event_type == EventType.archived,
            )
            .all()
        )
        assert len(events) >= 1
        assert events[0].organization_id == "org-archive"
        assert events[0].workspace_id == "ws-archive"


class TestRollbackLifecycleEvents:
    def test_rollback_writes_lifecycle_events_with_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-rb", ws_id="ws-rb")
        v1 = _make_version(db_session, item, ver="1.0.0", status=HubItemVersionStatus.published)
        v2 = _make_version(db_session, item, ver="2.0.0", status=HubItemVersionStatus.published)
        _add_scan_report(db_session, v1)
        _add_scan_report(db_session, v2)

        item.current_version_id = v2.id
        item.status = HubItemStatus.published
        db_session.flush()

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.rollback_item(item.id, v1.id, "admin", reason="bug in v2")

        events = (
            db_session.query(LifecycleEvent)
            .filter(LifecycleEvent.hub_item_id == item.id)
            .order_by(LifecycleEvent.created_at)
            .all()
        )
        assert len(events) >= 1
        for e in events:
            assert e.organization_id is not None, f"rollback lifecycle event {e.event_type} missing org_id"
            assert e.workspace_id is not None, f"rollback lifecycle event {e.event_type} missing ws_id"
            assert e.organization_id == "org-rb"
            assert e.workspace_id == "ws-rb"


class TestScanLifecycleEvents:
    def test_scan_produces_lifecycle_event_with_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-scan-lc", ws_id="ws-scan-lc")
        version = _make_version(db_session, item)

        from app.services.scan_service import ScanService
        svc = ScanService(db_session)
        svc.scan_version(version.id, operator="test")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_version_id == version.id,
                LifecycleEvent.event_type == EventType.scanned,
            )
            .all()
        )
        assert len(events) >= 1
        assert events[0].organization_id == "org-scan-lc"
        assert events[0].workspace_id == "ws-scan-lc"


class TestAutoScanLifecycleEvents:
    def test_auto_scan_produces_scan_report_with_tenant(self, db_session):
        item = _make_item(db_session, org_id="org-auto", ws_id="ws-auto")
        version = _make_version(db_session, item)

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        try:
            svc.submit_version(version.id, "operator-test")
        except Exception:
            pass

        reports = (
            db_session.query(ScanReport)
            .filter(ScanReport.hub_item_version_id == version.id)
            .all()
        )
        assert len(reports) >= 1
        for r in reports:
            assert r.organization_id == "org-auto"
            assert r.workspace_id == "ws-auto"
