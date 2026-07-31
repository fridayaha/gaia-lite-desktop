import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _create_item(
    client: TestClient, name: str = "Test", item_type: str = "tool", headers: dict | None = None
) -> str:
    kwargs: dict = {"json": {"name": name, "type": item_type}}
    if headers:
        kwargs["headers"] = headers
    resp = client.post("/api/hub/items", **kwargs)
    return resp


def _create_version(client: TestClient, item_id: str, version: str = "1.0.0") -> str:
    resp = client.post(
        f"/api/hub/items/{item_id}/versions",
        json={"hub_item_id": item_id, "version": version},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _admin_headers() -> dict:
    return {"X-Actor-ID": "admin", "X-Roles": "platform_admin"}


def _contributor_headers() -> dict:
    return {"X-Actor-ID": "contrib1", "X-Roles": "contributor"}


def _reviewer_headers() -> dict:
    return {"X-Actor-ID": "sec1", "X-Roles": "security_reviewer"}


def _approver_headers() -> dict:
    return {"X-Actor-ID": "biz1", "X-Roles": "business_approver"}


def _publisher_headers() -> dict:
    return {"X-Actor-ID": "pub1", "X-Roles": "publisher"}


def _auditor_headers() -> dict:
    return {"X-Actor-ID": "aud1", "X-Roles": "auditor"}


def _consumer_headers() -> dict:
    return {"X-Actor-ID": "svc1", "X-Actor-Type": "service", "X-Roles": "runtime_consumer"}


def _owner_headers() -> dict:
    return {"X-Actor-ID": "owner1", "X-Roles": "asset_owner"}


def _set_version_status(db_session: Session, version_id: str, status: str):
    import uuid as _uuid
    from app.core.enums import RiskLevel
    from app.models.hub_item_version import HubItemVersion
    from app.models.scan_report import ScanReport

    version = db_session.get(HubItemVersion, _uuid.UUID(version_id))
    if version is not None:
        version.status = status
        if status == "approved":
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
                    scanner_version="test",
                )
                db_session.add(report)
        db_session.commit()


# ─── dev mode (no header) tests ───


class TestDevModeNoHeader:
    """dev mode: no header → dev-admin → all APIs work"""

    def test_create_item(self, client: TestClient):
        resp = _create_item(client)
        assert resp.status_code == 201

    def test_list_items(self, client: TestClient):
        resp = client.get("/api/hub/items")
        assert resp.status_code == 200

    def test_submit_review(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        _create_version(client, item_id)
        resp = client.post(
            f"/api/hub/items/{item_id}/submit",
            json={"operator": "dev"},
        )
        assert resp.status_code == 200

    def test_approve(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "dev"},
        )
        assert resp.status_code == 200

    def test_publish(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "dev"},
        )
        assert resp.status_code == 200

    def test_export(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "dev"},
        )
        resp = client.get(f"/api/hub/exports/items/{item_id}/versions/{v_id}/package")
        assert resp.status_code == 200


# ─── header mode: no header → 403 ───


@pytest.fixture(autouse=False)
def header_mode_client(db_session: Session):
    """Override TestClient with HUB_AUTH_MODE=header for header-mode tests."""
    old = os.environ.get("HUB_AUTH_MODE")
    os.environ["HUB_AUTH_MODE"] = "header"
    try:
        # Force reimport of auth_middleware to pick up new env
        import importlib
        from app.core import auth_middleware
        importlib.reload(auth_middleware)

        from app.main import app as _app
        from app.db.session import get_db
        from fastapi.testclient import TestClient as TC

        def override_get_db():
            try:
                yield db_session
            finally:
                pass

        _app.dependency_overrides[get_db] = override_get_db
        with TC(_app) as c:
            yield c
        _app.dependency_overrides.clear()
    finally:
        if old is not None:
            os.environ["HUB_AUTH_MODE"] = old
        else:
            os.environ.pop("HUB_AUTH_MODE", None)
        import importlib
        from app.core import auth_middleware
        importlib.reload(auth_middleware)


class TestHeaderModeNoHeader:
    """header mode: no auth header → 403 on write APIs"""

    def test_create_item_forbidden(self, header_mode_client):
        resp = header_mode_client.post(
            "/api/hub/items", json={"name": "X", "type": "tool"}
        )
        assert resp.status_code == 403

    def test_list_items_forbidden(self, header_mode_client):
        resp = header_mode_client.get("/api/hub/items")
        assert resp.status_code == 403

    def test_health_unaffected(self, header_mode_client):
        resp = header_mode_client.get("/api/health")
        assert resp.status_code == 200


# ─── admin role ───


class TestAdmin:
    def test_admin_can_create(self, client: TestClient):
        resp = _create_item(client, headers=_admin_headers())
        assert resp.status_code == 201

    def test_admin_can_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "admin"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_admin_can_publish(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "admin"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_admin_can_disable(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "admin"},
        )
        resp = client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "admin"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_admin_can_archive(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "admin"},
        )
        resp = client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "admin"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_admin_can_rollback(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")
        _set_version_status(db_session, v1_id, "approved")
        _set_version_status(db_session, v2_id, "approved")
        client.post(
            f"/api/hub/versions/{v1_id}/publish",
            json={"operator": "admin"},
        )
        client.post(
            f"/api/hub/versions/{v2_id}/publish",
            json={"operator": "admin"},
        )
        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={"target_version_id": v1_id, "operator": "admin"},
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_admin_can_configure_presets(self, client: TestClient):
        resp = client.post(
            "/api/hub/presets/init",
            headers=_admin_headers(),
        )
        assert resp.status_code == 200

    def test_admin_can_export(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "admin"},
        )
        resp = client.get(
            f"/api/hub/exports/items/{item_id}/versions/{v_id}/package",
            headers=_admin_headers(),
        )
        assert resp.status_code == 200


# ─── contributor ───


class TestContributor:
    def test_can_create(self, client: TestClient):
        resp = _create_item(client, headers=_contributor_headers())
        assert resp.status_code == 201

    def test_cannot_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "contributor"},
            headers=_contributor_headers(),
        )
        assert resp.status_code == 403

    def test_cannot_publish(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "contributor"},
            headers=_contributor_headers(),
        )
        assert resp.status_code == 403

    def test_can_create_relation(self, client: TestClient):
        item1_id = _create_item(client, "AgentItem", "agent", headers=_contributor_headers()).json()["id"]
        item2_id = _create_item(client, "ToolItem", "tool", headers=_admin_headers()).json()["id"]
        resp = client.post(
            "/api/hub/relations",
            json={
                "source_item_id": item1_id,
                "target_item_id": item2_id,
                "relation_type": "invokes",
            },
            headers=_contributor_headers(),
        )
        assert resp.status_code == 201

    def test_cannot_delete_relation(self, client: TestClient):
        item1_id = _create_item(client, "AgentR1", "agent", headers=_admin_headers()).json()["id"]
        item2_id = _create_item(client, "ToolR1", "tool", headers=_admin_headers()).json()["id"]
        create_resp = client.post(
            "/api/hub/relations",
            json={
                "source_item_id": item1_id,
                "target_item_id": item2_id,
                "relation_type": "invokes",
            },
            headers=_admin_headers(),
        )
        assert create_resp.status_code == 201, f"create relation failed: {create_resp.json()}"
        rel_id = create_resp.json()["id"]
        resp = client.delete(
            f"/api/hub/relations/{rel_id}",
            headers=_contributor_headers(),
        )
        assert resp.status_code == 403


# ─── asset_owner ───


class TestAssetOwner:
    def test_cannot_publish(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "owner"},
            headers=_owner_headers(),
        )
        assert resp.status_code == 403

    def test_cannot_disable(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        client.post(f"/api/hub/versions/{v_id}/publish", json={"operator": "admin"})
        resp = client.post(
            f"/api/hub/items/{item_id}/disable",
            json={"operator": "owner"},
            headers=_owner_headers(),
        )
        assert resp.status_code == 403

    def test_cannot_archive(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        client.post(f"/api/hub/versions/{v_id}/publish", json={"operator": "admin"})
        resp = client.post(
            f"/api/hub/items/{item_id}/archive",
            json={"operator": "owner"},
            headers=_owner_headers(),
        )
        assert resp.status_code == 403

    def test_cannot_rollback(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v1_id = _create_version(client, item_id, "1.0.0")
        v2_id = _create_version(client, item_id, "2.0.0")
        _set_version_status(db_session, v1_id, "approved")
        _set_version_status(db_session, v2_id, "approved")
        client.post(f"/api/hub/versions/{v1_id}/publish", json={"operator": "admin"})
        client.post(f"/api/hub/versions/{v2_id}/publish", json={"operator": "admin"})
        resp = client.post(
            f"/api/hub/items/{item_id}/rollback",
            json={"target_version_id": v1_id, "operator": "owner"},
            headers=_owner_headers(),
        )
        assert resp.status_code == 403

    def test_can_read(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        resp = client.get(f"/api/hub/items/{item_id}", headers=_owner_headers())
        assert resp.status_code == 200

    def test_cannot_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "owner"},
            headers=_owner_headers(),
        )
        assert resp.status_code == 403


# ─── publisher ───


class TestPublisher:
    def test_can_publish(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "publisher"},
            headers=_publisher_headers(),
        )
        assert resp.status_code == 200

    def test_cannot_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "publisher"},
            headers=_publisher_headers(),
        )
        assert resp.status_code == 403


# ─── security_reviewer ───


class TestSecurityReviewer:
    def test_can_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "reviewer"},
            headers=_reviewer_headers(),
        )
        assert resp.status_code == 200

    def test_can_reject(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/reject",
            json={"operator": "reviewer"},
            headers=_reviewer_headers(),
        )
        assert resp.status_code == 200

    def test_can_request_change(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/request-change",
            json={"operator": "reviewer"},
            headers=_reviewer_headers(),
        )
        assert resp.status_code == 200

    def test_cannot_create(self, client: TestClient):
        resp = _create_item(client, headers=_reviewer_headers())
        assert resp.status_code == 403


# ─── business_approver ───


class TestBusinessApprover:
    def test_can_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "approver"},
            headers=_approver_headers(),
        )
        assert resp.status_code == 200

    def test_cannot_create(self, client: TestClient):
        resp = _create_item(client, headers=_approver_headers())
        assert resp.status_code == 403

    def test_cannot_publish(self, client: TestClient, db_session):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        _set_version_status(db_session, v_id, "approved")
        resp = client.post(
            f"/api/hub/versions/{v_id}/publish",
            json={"operator": "approver"},
            headers=_approver_headers(),
        )
        assert resp.status_code == 403


# ─── auditor ───


class TestAuditor:
    def test_can_read(self, client: TestClient):
        resp = client.get("/api/hub/items", headers=_auditor_headers())
        assert resp.status_code == 200

    def test_cannot_create(self, client: TestClient):
        resp = _create_item(client, headers=_auditor_headers())
        assert resp.status_code == 403

    def test_cannot_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "auditor"},
            headers=_auditor_headers(),
        )
        assert resp.status_code == 403


# ─── runtime_consumer ───


class TestRuntimeConsumer:
    def test_cannot_create(self, client: TestClient):
        resp = _create_item(client, headers=_consumer_headers())
        assert resp.status_code == 403

    def test_cannot_read(self, client: TestClient):
        resp = client.get("/api/hub/items", headers=_consumer_headers())
        assert resp.status_code == 403

    def test_cannot_approve(self, client: TestClient):
        item_id = _create_item(client).json()["id"]
        v_id = _create_version(client, item_id)
        client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "dev"},
        )
        resp = client.post(
            f"/api/hub/versions/{v_id}/approve",
            json={"operator": "consumer"},
            headers=_consumer_headers(),
        )
        assert resp.status_code == 403


# ─── operator / actor_id compatibility ───


class TestOperatorActorCompatibility:
    def test_body_operator_differs_from_header_actor(self, client: TestClient):
        """body.operator differs from header actor_id: auth uses header, not body."""
        resp = client.post(
            "/api/hub/items",
            json={"name": "Compat", "type": "tool"},
            headers=_contributor_headers(),
        )
        assert resp.status_code == 201
        item_id = resp.json()["id"]
        v_id = _create_version(client, item_id)

        resp = client.post(
            f"/api/hub/versions/{v_id}/submit-review",
            json={"operator": "someone-else"},
            headers=_contributor_headers(),
        )
        assert resp.status_code == 200


# ─── unknown role ───


class TestUnknownRole:
    def test_no_permission(self, client: TestClient):
        resp = _create_item(
            client,
            headers={"X-Actor-ID": "u1", "X-Roles": "nonexistent_role"},
        )
        assert resp.status_code == 403


# ─── role normalize ───


class TestRoleNormalize:
    def test_spaces_and_case(self, client: TestClient):
        resp = client.get(
            "/api/hub/items",
            headers={"X-Actor-ID": "u1", "X-Roles": "  AuDiToR  "},
        )
        assert resp.status_code == 200

    def test_hyphens(self, client: TestClient):
        resp = client.get(
            "/api/hub/items",
            headers={"X-Actor-ID": "u1", "X-Roles": "platform-admin"},
        )
        assert resp.status_code == 200

    def test_mixed(self, client: TestClient):
        resp = client.get(
            "/api/hub/items",
            headers={
                "X-Actor-ID": "u1",
                "X-Roles": " security-reviewer ,  Publisher ",
            },
        )
        assert resp.status_code == 200


# ─── Runtime API unaffected ───


class TestRuntimeUnaffected:
    def test_discover_no_auth_header(self, client: TestClient):
        resp = client.get("/api/runtime/capabilities/discover")
        assert resp.status_code == 200

    def test_resolve_not_found(self, client: TestClient):
        import uuid
        resp = client.get(f"/api/runtime/capabilities/{uuid.uuid4()}/resolve")
        assert resp.status_code == 404

    def test_tool_definition_not_found(self, client: TestClient):
        import uuid
        resp = client.get(f"/api/runtime/capabilities/{uuid.uuid4()}/tool-definition")
        assert resp.status_code == 404
