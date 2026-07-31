import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.approval_record import ApprovalRecord
from app.models.lifecycle_event import LifecycleEvent
from app.models.scan_report import ScanReport


def _create_item(client: TestClient, name: str, item_type: str) -> str:
    resp = client.post(
        "/api/hub/items",
        json={"name": name, "type": item_type},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_version(
    client: TestClient, item_id: str, version: str = "1.0.0", **extra
) -> str:
    payload = {"hub_item_id": item_id, "version": version, **extra}
    resp = client.post(
        f"/api/hub/items/{item_id}/versions", json=payload
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _scan(client: TestClient, version_id: str):
    return client.post(
        f"/api/hub/versions/{version_id}/scan",
        json={"operator": "scanner"},
    )


def _submit(client: TestClient, version_id: str):
    return client.post(
        f"/api/hub/versions/{version_id}/submit-review",
        json={"operator": "dev"},
    )


def _approve(client: TestClient, version_id: str):
    return client.post(
        f"/api/hub/versions/{version_id}/approve",
        json={"operator": "approver", "comment": "approved"},
    )


def _publish(client: TestClient, version_id: str):
    return client.post(
        f"/api/hub/versions/{version_id}/publish",
        json={"operator": "admin"},
    )


def _publish_flow(client: TestClient, item_id: str, version: str) -> str:
    """create version → scan → submit → approve → publish"""
    version_id = _create_version(client, item_id, version)
    _scan(client, version_id)
    resp = _submit(client, version_id)
    assert resp.status_code == 200
    resp = _approve(client, version_id)
    assert resp.status_code == 200
    resp = _publish(client, version_id)
    assert resp.status_code == 200
    return version_id


class TestE2ELowRiskPublish:
    def test_low_risk_tool_publish_success(self, client: TestClient, db_session):
        item_id = _create_item(client, "Low Risk Tool", "tool")
        version_id = _create_version(
            client, item_id, "1.0.0",
            manifest_json={"name": "safe tool"},
            config_json={"timeout": 30},
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission_json={"scope": ["internal"]},
            runtime_compatibility={"platform": "linux"},
        )
        scan_resp = _scan(client, version_id)
        assert scan_resp.status_code == 200
        assert scan_resp.json()["risk_level"] == "low"

        submit_resp = _submit(client, version_id)
        assert submit_resp.status_code == 200

        approve_resp = _approve(client, version_id)
        assert approve_resp.status_code == 200

        publish_resp = _publish(client, version_id)
        assert publish_resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "published"
        assert item["current_version_id"] == version_id

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "published"

        scan_count = db_session.query(ScanReport).filter(
            ScanReport.hub_item_version_id == uuid.UUID(version_id)
        ).count()
        assert scan_count >= 1

        approval_count = db_session.query(ApprovalRecord).filter(
            ApprovalRecord.hub_item_version_id == uuid.UUID(version_id)
        ).count()
        assert approval_count >= 1

        event_count = (
            db_session.query(LifecycleEvent)
            .filter(LifecycleEvent.hub_item_id == uuid.UUID(item_id))
            .count()
        )
        assert event_count >= 2


class TestE2EBlockingReject:
    def test_blocking_mcp_cannot_submit(self, client: TestClient, db_session):
        item_id = _create_item(client, "Blocking MCP", "mcp")
        version_id = _create_version(
            client, item_id, "1.0.0",
            config_json={"setup": "init: rm -rf /tmp/cache"},
        )

        submit_resp = _submit(client, version_id)
        assert submit_resp.status_code == 400

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] != "pending_review"
        assert v["status"] != "approved"

        report_resp = client.get(
            f"/api/hub/versions/{version_id}/scan-report"
        )
        assert report_resp.status_code == 200
        findings = report_resp.json()["findings"]
        assert len(findings) >= 1


class TestE2ERollback:
    def test_publish_two_versions_then_rollback(
        self, client: TestClient, db_session
    ):
        item_id = _create_item(client, "Rollback Skill", "skill")
        v1_id = _publish_flow(client, item_id, "1.0.0")
        v2_id = _publish_flow(client, item_id, "2.0.0")

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v1 = next(v for v in versions if v["id"] == v1_id)
        v2 = next(v for v in versions if v["id"] == v2_id)
        assert v1["status"] == "deprecated"
        assert v2["status"] == "published"

        rollback_resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": v1_id,
                "operator": "admin",
                "reason": "v2 bug",
            },
        )
        assert rollback_resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["current_version_id"] == v1_id

        versions_after = client.get(
            f"/api/hub/items/{item_id}/versions"
        ).json()
        v1_after = next(v for v in versions_after if v["id"] == v1_id)
        v2_after = next(v for v in versions_after if v["id"] == v2_id)
        assert v1_after["status"] == "published"
        assert v2_after["status"] == "deprecated"


class TestE2EDisable:
    def test_disable_allows_existing_reference(self, client: TestClient):
        item_id = _create_item(client, "Disable Agent", "agent")
        _publish_flow(client, item_id, "1.0.0")

        disable_resp = client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )
        assert disable_resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "disabled"
        assert item["discoverable"] is False
        assert item["allow_existing_references"] is True

        detail_resp = client.get(f"/api/hub/items/{item_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["status"] == "disabled"
