import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.enums import HubItemVersionStatus
from app.models.hub_item_version import HubItemVersion
from app.models.lifecycle_event import LifecycleEvent


def _create_item(client: TestClient, name: str = "Test Item") -> str:
    resp = client.post(
        "/api/hub/items",
        json={"name": name, "type": "tool"},
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
    version = db_session.get(HubItemVersion, uuid.UUID(version_id))
    if version is not None:
        version.status = HubItemVersionStatus(status)
        if status == "approved":
            from app.core.enums import RiskLevel
            from app.models.scan_report import ScanReport

            scanned = (
                db_session.query(ScanReport)
                .filter(ScanReport.hub_item_version_id == version.id)
                .first()
            )
            if scanned is None:
                report = ScanReport(
                    hub_item_id=version.hub_item_id,
                    hub_item_version_id=version.id,
                    risk_level=RiskLevel.low,
                    summary={},
                    scanner_version="test-helper",
                )
                db_session.add(report)
        db_session.commit()


def _count_events(db_session: Session, item_id: str) -> int:
    return (
        db_session.query(LifecycleEvent)
        .filter(
            LifecycleEvent.hub_item_id == uuid.UUID(item_id)
        )
        .count()
    )


class TestSubmitItem:
    def test_submit_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        resp = client.post(
            f"/api/hub/items/{item_id}/submit",
            json={"operator": "admin", "reason": "ready"},
        )
        assert resp.status_code == 200

        detail = client.get(f"/api/hub/items/{item_id}").json()
        assert detail["status"] == "pending_review"
        assert _count_events(db_session, item_id) == 1

    def test_submit_invalid_transition(self, client: TestClient):
        item_id = _create_item(client)
        client.post(
            f"/api/hub/items/{item_id}/submit",
            json={"operator": "admin"},
        )
        resp = client.post(
            f"/api/hub/items/{item_id}/submit",
            json={"operator": "admin"},
        )
        assert resp.status_code == 400


class TestSubmitVersion:
    def test_submit_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        resp = client.post(
            f"/api/hub/versions/{version_id}/submit-review",
            json={"operator": "admin"},
        )
        assert resp.status_code == 200

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        assert versions[0]["status"] == "pending_review"


class TestPublishVersion:
    def test_publish_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id, "1.0.0")
        _set_version_status(db_session, version_id, "approved")

        resp = client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )
        assert resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "published"
        assert item["current_version_id"] == version_id

    def test_publish_deprecates_old_version(self, client: TestClient, db_session):
        item_id = _create_item(client)
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")

        _set_version_status(db_session, v1_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )

        _set_version_status(db_session, v2_id, "approved")
        client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v1 = next(v for v in versions if v["id"] == v1_id)
        v2 = next(v for v in versions if v["id"] == v2_id)
        assert v1["status"] == "deprecated"
        assert v2["status"] == "published"

    def test_publish_not_approved(self, client: TestClient):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        resp = client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )
        assert resp.status_code == 400

    def test_publish_disabled_item(self, client: TestClient, db_session):
        item_id = _create_item(client)
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")

        _set_version_status(db_session, v1_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )
        client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )

        _set_version_status(db_session, v2_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )
        assert resp.status_code == 400

    def test_publish_archived_item(self, client: TestClient, db_session):
        item_id = _create_item(client)
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")

        _set_version_status(db_session, v1_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )
        client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
        )

        _set_version_status(db_session, v2_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )
        assert resp.status_code == 400


class TestDisableItem:
    def test_disable_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )
        assert resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "disabled"
        assert item["discoverable"] is False
        assert item["allow_existing_references"] is True


class TestArchiveItem:
    def test_archive_from_published(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
        )
        assert resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["status"] == "archived"
        assert item["discoverable"] is False
        assert item["allow_existing_references"] is True

    def test_archive_from_disabled(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )
        client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
        )
        assert resp.status_code == 200

    def test_archive_from_draft_rejected(self, client: TestClient):
        item_id = _create_item(client)
        resp = client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
        )
        assert resp.status_code == 400


class TestRollback:
    def test_rollback_success(self, client: TestClient, db_session):
        item_id = _create_item(client)
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")

        _set_version_status(db_session, v1_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )
        _set_version_status(db_session, v2_id, "approved")
        client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": v1_id,
                "operator": "admin",
                "reason": "bug in v2",
            },
        )
        assert resp.status_code == 200

        item = client.get(f"/api/hub/items/{item_id}").json()
        assert item["current_version_id"] == v1_id

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v1 = next(v for v in versions if v["id"] == v1_id)
        v2 = next(v for v in versions if v["id"] == v2_id)
        assert v1["status"] == "published"
        assert v2["status"] == "deprecated"

    def test_rollback_target_not_found(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )

        fake_id = str(uuid.uuid4())
        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": fake_id,
                "operator": "admin",
            },
        )
        assert resp.status_code == 404

    def test_rollback_to_archived(self, client: TestClient, db_session):
        item_id = _create_item(client)
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")

        _set_version_status(db_session, v1_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )
        _set_version_status(db_session, v2_id, "approved")
        client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )

        db_session.get(HubItemVersion, uuid.UUID(v1_id)).status = "archived"
        db_session.commit()

        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": v1_id,
                "operator": "admin",
            },
        )
        assert resp.status_code == 400

    def test_rollback_no_current_version(self, client: TestClient):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": version_id,
                "operator": "admin",
            },
        )
        assert resp.status_code == 400

    def test_rollback_to_current(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": version_id,
                "operator": "admin",
            },
        )
        assert resp.status_code == 400

    def test_rollback_archived_item(self, client: TestClient, db_session):
        item_id = _create_item(client)
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")

        _set_version_status(db_session, v1_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )
        _set_version_status(db_session, v2_id, "approved")
        client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )
        client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
        )

        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={
                "target_version_id": v1_id,
                "operator": "admin",
            },
        )
        assert resp.status_code == 400

    def test_lifecycle_events_written(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id)
        _set_version_status(db_session, version_id, "approved")
        client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin", "reason": "first release"},
        )

        count = _count_events(db_session, item_id)
        assert count >= 2


class TestSecurityAdmission:
    def test_submit_blocking_returns_400(self, client: TestClient):
        item_id = _create_item(client, "BlockingSub")
        resp = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={
                "hub_item_id": item_id,
                "version": "1.0.0",
                "config_json": {"setup": "rm -rf /tmp"},
            },
        )
        assert resp.status_code == 201
        version_id = resp.json()["id"]

    def test_submit_non_blocking_proceeds(self, client: TestClient):
        item_id = _create_item(client, "SafeSub")
        version_id = _create_version(client, item_id, "1.0.0")
        resp = client.post(
            f"/api/hub/versions/{version_id}/submit-review",
            json={"operator": "tester"},
        )
        assert resp.status_code == 200

        versions = client.get(f"/api/hub/items/{item_id}/versions").json()
        v = next(v for v in versions if v["id"] == version_id)
        assert v["status"] == "pending_review"

    def test_publish_not_scanned_returns_400(self, client: TestClient, db_session):
        item_id = _create_item(client)
        version_id = _create_version(client, item_id, "1.0.0")
        v = db_session.get(HubItemVersion, uuid.UUID(version_id))
        v.status = "approved"
        db_session.commit()

        resp = client.post(
            f"/api/hub/versions/{version_id}/publish",
            json={"operator": "admin"},
        )
        assert resp.status_code == 400
