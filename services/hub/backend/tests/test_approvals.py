import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.approval_record import ApprovalRecord
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent


def _create_item(client: TestClient, name: str = "Test Item") -> str:
    resp = client.post(
        "/api/hub/items", json={"name": name, "type": "tool"}
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_version(
    client: TestClient, item_id: str, version: str = "1.0.0"
) -> str:
    resp = client.post(
        f"/api/hub/items/{item_id}/versions",
        json={"hub_item_id": item_id, "version": version},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _set_version_status(db_session: Session, version_id: str, status: str):
    v = db_session.get(HubItemVersion, uuid.UUID(version_id))
    if v is not None:
        v.status = status
        db_session.commit()


def _set_version_risk(db_session: Session, version_id: str, risk: str):
    v = db_session.get(HubItemVersion, uuid.UUID(version_id))
    if v is not None:
        v.risk_level = risk
        db_session.commit()


def _count_approval_records(db_session: Session, version_id: str) -> int:
    return (
        db_session.query(ApprovalRecord)
        .filter(
            ApprovalRecord.hub_item_version_id == uuid.UUID(version_id)
        )
        .count()
    )


def _count_events(db_session: Session, item_id: str) -> int:
    return (
        db_session.query(LifecycleEvent)
        .filter(LifecycleEvent.hub_item_id == uuid.UUID(item_id))
        .count()
    )


def _to_pending_review(
    client: TestClient, item_id: str, db_session: Session
) -> str:
    version_id = _create_version(client, item_id)
    client.post(
        f"/api/hub/versions/{version_id}/submit-review",
        json={"operator": "tester"},
    )
    return version_id


class TestApprove:
    def test_approve_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver", "comment": "looks good"},
        )
        assert resp.status_code == 200

    def test_approve_sets_status(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "approved"

    def test_approve_writes_approval_record(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        assert _count_approval_records(db_session, version_id) == 0
        client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        assert _count_approval_records(db_session, version_id) == 1

    def test_approve_writes_lifecycle_event(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        before = _count_events(db_session, item_id)
        client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        after = _count_events(db_session, item_id)
        assert after == before + 1

    def test_approve_blocking_risk(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)
        _set_version_risk(db_session, version_id, "blocking")

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        assert resp.status_code == 400

    def test_approve_draft_version(self, client: TestClient):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        assert resp.status_code == 400

    def test_approve_published_version(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        assert resp.status_code == 400

    def test_approve_does_not_publish(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver"},
        )
        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "draft"
        assert item["current_version_id"] is None


class TestReject:
    def test_reject_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/reject",
            json={"operator": "approver", "comment": "needs work"},
        )
        assert resp.status_code == 200

    def test_reject_sets_status(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/reject",
            json={"operator": "approver"},
        )
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "rejected"

    def test_reject_writes_approval_record(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/reject",
            json={"operator": "approver"},
        )
        assert _count_approval_records(db_session, version_id) == 1


class TestRequestChange:
    def test_request_change_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/request-change",
            json={"operator": "approver", "comment": "please fix"},
        )
        assert resp.status_code == 200

    def test_request_change_sets_status(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/request-change",
            json={"operator": "approver"},
        )
        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "change_required"

    def test_request_change_then_resubmit(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/request-change",
            json={"operator": "approver"},
        )
        resp = client.post(
            f"/api/hub/versions/{version_id}/submit-review",
            json={"operator": "dev"},
        )
        assert resp.status_code == 200

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "pending_review"


class TestNotFound:
    def test_version_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        for path in ("approve", "reject", "request-change"):
            resp = client.post(
                f"/api/hub/versions/{fake_id}/{path}",
                json={"operator": "tester"},
            )
            assert resp.status_code == 404


class TestSecurityAdmission:
    def test_approve_not_scanned_returns_400(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "pending_review")

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "tester", "comment": "ok"},
        )
        assert resp.status_code == 400


class TestFourEyesIntegration:
    def test_same_person_approve_allowed_when_four_eyes_disabled(
        self, client: TestClient, db_session,
    ):
        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "tester", "comment": "self approve"},
        )
        assert resp.status_code == 200

    def test_same_person_deny_with_default_policy(
        self, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        from app.policies.approval_policy import DefaultApprovalPolicy
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError
        from app.core.auth_context import AuthContext
        from app.models.scan_report import ScanReport
        from app.models.lifecycle_event import LifecycleEvent
        from app.core.enums import EventType
        import uuid as _uuid

        policy = DefaultApprovalPolicy()
        svc = ApprovalService(db_session, policy=policy)

        from tests.test_helpers import (
            create_item_db, create_version_db, set_version_status,
            set_version_risk, submit_version, add_scan_report,
        )

        item_id = create_item_db(db_session, "four-eyes-item", "tool")
        version_id = create_version_db(db_session, item_id, "1.0.0")
        set_version_status(db_session, version_id, "draft")
        add_scan_report(db_session, version_id)
        set_version_risk(db_session, version_id, "low")
        submit_version(db_session, version_id, item_id, "tester")

        with pytest.raises(ApprovalPolicyDeniedError) as exc:
            svc.approve_version(
                _uuid.UUID(version_id), "tester", "self approve",
                ctx=AuthContext(actor_id="tester", roles=["contributor"]),
            )
        assert "four eyes" in str(exc.value)

    def test_different_person_approve_allowed_when_four_eyes_enabled(
        self, client: TestClient, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")

        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver", "comment": "looks good"},
        )
        assert resp.status_code == 200

    def test_four_eyes_deny_does_not_change_status(
        self, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        from app.policies.approval_policy import DefaultApprovalPolicy
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError
        from app.core.auth_context import AuthContext
        import uuid as _uuid
        from tests.test_helpers import (
            create_item_db, create_version_db, set_version_status,
            set_version_risk, submit_version, add_scan_report,
        )

        policy = DefaultApprovalPolicy()
        svc = ApprovalService(db_session, policy=policy)

        item_id = create_item_db(db_session, "four-eyes-status", "tool")
        version_id = create_version_db(db_session, item_id, "1.0.0")
        set_version_status(db_session, version_id, "draft")
        add_scan_report(db_session, version_id)
        set_version_risk(db_session, version_id, "low")
        submit_version(db_session, version_id, item_id, "tester")

        try:
            svc.approve_version(
                _uuid.UUID(version_id), "tester", "self approve",
                ctx=AuthContext(actor_id="tester", roles=["contributor"]),
            )
        except ApprovalPolicyDeniedError:
            pass

        from app.models.hub_item_version import HubItemVersion
        v = db_session.get(HubItemVersion, _uuid.UUID(version_id))
        assert v.status.value == "pending_review"

    def test_four_eyes_deny_does_not_write_approval_record(
        self, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")
        from app.policies.approval_policy import DefaultApprovalPolicy
        from app.services.approval_service import ApprovalService
        from app.services.exceptions import ApprovalPolicyDeniedError
        from app.core.auth_context import AuthContext
        import uuid as _uuid
        from tests.test_helpers import (
            create_item_db, create_version_db, set_version_status,
            set_version_risk, submit_version, add_scan_report,
        )

        policy = DefaultApprovalPolicy()
        svc = ApprovalService(db_session, policy=policy)

        item_id = create_item_db(db_session, "four-eyes-record", "tool")
        version_id = create_version_db(db_session, item_id, "1.0.0")
        set_version_status(db_session, version_id, "draft")
        add_scan_report(db_session, version_id)
        set_version_risk(db_session, version_id, "low")
        submit_version(db_session, version_id, item_id, "tester")

        before = _count_approval_records(db_session, version_id)
        try:
            svc.approve_version(
                _uuid.UUID(version_id), "tester", "self approve",
                ctx=AuthContext(actor_id="tester", roles=["contributor"]),
            )
        except ApprovalPolicyDeniedError:
            pass
        after = _count_approval_records(db_session, version_id)
        assert after == before

    def test_reject_not_affected_by_four_eyes(
        self, client: TestClient, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")

        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/reject",
            json={"operator": "tester", "comment": "bad"},
        )
        assert resp.status_code == 200

    def test_request_change_not_affected_by_four_eyes(
        self, client: TestClient, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")

        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/request-change",
            json={"operator": "tester", "comment": "fix this"},
        )
        assert resp.status_code == 200

    def test_latest_submitter_used_after_resubmit(
        self, client: TestClient, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")

        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        client.post(
            f"/api/hub/versions/{version_id}/request-change",
            json={"operator": "approver"},
        )
        client.post(
            f"/api/hub/versions/{version_id}/submit-review",
            json={"operator": "new-submitter"},
        )

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "tester", "comment": "ok"},
        )
        assert resp.status_code == 200

    def test_four_eyes_default_off_does_not_break_existing(
        self, client: TestClient, db_session, monkeypatch,
    ):
        monkeypatch.delenv("HUB_FOUR_EYES_REQUIRED", raising=False)

        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "tester", "comment": "ok"},
        )
        assert resp.status_code == 200

    def test_e2e_approve_then_publish_unaffected(
        self, client: TestClient, db_session, monkeypatch,
    ):
        monkeypatch.setenv("HUB_FOUR_EYES_REQUIRED", "true")

        item_id = _create_item(client)
        version_id = _to_pending_review(client, item_id, db_session)

        resp = client.post(
            f"/api/hub/versions/{version_id}/approve",
            json={"operator": "approver", "comment": "ok"},
        )
        assert resp.status_code == 200

        resp = client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "tester", "reason": "release"},
        )
        assert resp.status_code == 200

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "published"
