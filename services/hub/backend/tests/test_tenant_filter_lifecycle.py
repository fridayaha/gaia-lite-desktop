import uuid

from app.core.enums import HubItemStatus, HubItemType, HubItemVersionStatus, RiskLevel
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.models.scan_report import ScanReport


def _header_client(monkeypatch):
    monkeypatch.setenv("HUB_AUTH_MODE", "header")
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    from app.main import app
    from app.db.session import get_db
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    app.dependency_overrides[get_db] = lambda: db
    from fastapi.testclient import TestClient
    return TestClient(app), db


def _headers(actor="user-1", roles="asset_owner", org="org-a", ws="ws-a"):
    return {
        "X-Actor-ID": actor,
        "X-Roles": roles,
        "X-Organization-ID": org,
        "X-Workspace-ID": ws,
    }


def _admin_headers():
    return {
        "X-Actor-ID": "admin-1",
        "X-Roles": "platform_admin",
        "X-Organization-ID": "org-adm",
        "X-Workspace-ID": "ws-adm",
    }


def _create_item(client, headers, name="LifecycleItem"):
    resp = client.post("/api/hub/items", json={"name": name, "type": "tool"}, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def _create_version(client, headers, item_id, ver="1.0.0"):
    resp = client.post(f"/api/hub/items/{item_id}/versions", json={
        "hub_item_id": item_id, "version": ver,
    }, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def _set_version_status(db, version_id, status, item=None):
    version = db.get(HubItemVersion, uuid.UUID(version_id))
    if version is not None:
        version.status = HubItemVersionStatus(status)
    if item:
        if status == "approved":
            report = ScanReport(
                hub_item_id=item.id,
                hub_item_version_id=version.id,
                risk_level=RiskLevel.low,
                summary={},
                scanner_version="test",
                organization_id=item.organization_id,
                workspace_id=item.workspace_id,
            )
            db.add(report)
        if status == "published":
            item.status = HubItemStatus.published
            item.current_version_id = version.id
            item.risk_level = RiskLevel.low
    db.commit()


def _set_item_status(db, item_id, status):
    item = db.get(HubItem, uuid.UUID(item_id))
    if item is not None:
        item.status = HubItemStatus(status)
        db.commit()


class TestSubmitItemTenantGuard:
    def test_same_tenant_submit_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _headers(ws="ws-a")
        item = _create_item(client, h, name="SubmitMe")
        resp = client.post(f"/api/hub/items/{item['id']}/submit",
                           json={"operator": "op"}, headers=h)
        assert resp.status_code == 200
        db.close()

    def test_different_tenant_submit_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="NoSubmit")
        resp = client.post(f"/api/hub/items/{item['id']}/submit",
                           json={"operator": "op"}, headers=ws_b)
        assert resp.status_code == 403
        db.close()

    def test_admin_submit_other_tenant_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        item = _create_item(client, ws_a, name="AdminSubmit")
        resp = client.post(f"/api/hub/items/{item['id']}/submit",
                           json={"operator": "op"}, headers=_admin_headers())
        assert resp.status_code == 200
        db.close()


class TestSubmitVersionTenantGuard:
    def test_same_tenant_submit_version_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _headers(ws="ws-a")
        item = _create_item(client, h, name="SubmitVerOk")
        v = _create_version(client, h, item["id"])
        resp = client.post(f"/api/hub/versions/{v['id']}/submit-review",
                           json={"operator": "op"}, headers=h)
        assert resp.status_code == 200
        db.close()

    def test_different_tenant_submit_version_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="SubmitVerNo")
        v = _create_version(client, ws_a, item["id"])
        resp = client.post(f"/api/hub/versions/{v['id']}/submit-review",
                           json={"operator": "op"}, headers=ws_b)
        assert resp.status_code == 403
        db.close()


class TestPublishTenantGuard:
    def test_different_tenant_publish_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b", roles="publisher")
        item = _create_item(client, ws_a, name="PubNo")
        v = _create_version(client, ws_a, item["id"])
        _set_version_status(db, v["id"], "approved", item=db.get(HubItem, uuid.UUID(item["id"])))
        db.commit()

        resp = client.post(f"/api/hub/versions/{v['id']}/publish",
                           json={"operator": "pub"}, headers=ws_b)
        assert resp.status_code == 403
        db.close()

    def test_admin_publish_other_tenant_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        item = _create_item(client, ws_a, name="AdminPub")
        v = _create_version(client, ws_a, item["id"])
        _set_version_status(db, v["id"], "approved", item=db.get(HubItem, uuid.UUID(item["id"])))
        db.commit()

        resp = client.post(f"/api/hub/versions/{v['id']}/publish",
                           json={"operator": "admin"}, headers=_admin_headers())
        assert resp.status_code == 200
        db.close()


class TestDisableTenantGuard:
    def test_different_tenant_disable_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="DisableNo")
        _set_item_status(db, item["id"], "published")

        resp = client.post(f"/api/hub/items/{item['id']}/disable",
                           json={"operator": "op"}, headers=ws_b)
        assert resp.status_code == 403
        db.close()


class TestArchiveTenantGuard:
    def test_different_tenant_archive_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="ArchiveNo")
        _set_item_status(db, item["id"], "disabled")

        resp = client.post(f"/api/hub/items/{item['id']}/archive",
                           json={"operator": "op"}, headers=ws_b)
        assert resp.status_code == 403
        db.close()


class TestRollbackTenantGuard:
    def test_different_tenant_rollback_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="RollNo")
        v1 = _create_version(client, ws_a, item["id"], "1.0.0")
        v2 = _create_version(client, ws_a, item["id"], "2.0.0")
        _set_version_status(db, v1["id"], "published")
        _set_version_status(db, v2["id"], "published")
        item_obj = db.get(HubItem, uuid.UUID(item["id"]))
        item_obj.status = HubItemStatus.published
        item_obj.current_version_id = uuid.UUID(v2["id"])
        db.commit()

        resp = client.post(f"/api/hub/items/{item['id']}/rollback", json={
            "target_version_id": v1["id"],
            "operator": "op",
        }, headers=ws_b)
        assert resp.status_code == 403
        db.close()


class TestDevModeBypass:
    def test_dev_mode_bypass_tenant_guard(self, monkeypatch):
        monkeypatch.setenv("HUB_AUTH_MODE", "dev")
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from sqlalchemy.orm import sessionmaker
        from app.db.base import Base
        from app.main import app
        from app.db.session import get_db
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        dev_db = SessionLocal()
        app.dependency_overrides[get_db] = lambda: dev_db
        from fastapi.testclient import TestClient
        client = TestClient(app)

        item = _create_item(client, {}, name="DevItem")
        resp = client.post(f"/api/hub/items/{item['id']}/submit",
                           json={"operator": "op"})
        assert resp.status_code == 200
        dev_db.close()
