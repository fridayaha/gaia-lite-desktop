import json
import uuid as _uuid

from app.core.enums import HubItemStatus, HubItemType, HubItemVersionStatus, RiskLevel
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion


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


def _headers(actor="user-1", roles="contributor", org="org-a", ws="ws-a"):
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


def _create_item(client, headers, name="TestItem", item_type="tool"):
    resp = client.post("/api/hub/items", json={
        "name": name, "type": item_type,
    }, headers=headers)
    assert resp.status_code == 201
    return resp.json()


def _create_version(client, headers, item_id, ver="1.0.0"):
    resp = client.post(f"/api/hub/items/{item_id}/versions", json={
        "hub_item_id": item_id, "version": ver,
    }, headers=headers)
    assert resp.status_code == 201
    return resp.json()


class TestListItemsTenantFiltering:
    def test_user_sees_own_workspace_items(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        _create_item(client, ws_a, name="ItemA1")
        _create_item(client, ws_a, name="ItemA2")

        resp = client.get("/api/hub/items", headers=ws_a)
        assert resp.status_code == 200
        data = resp.json()
        names = {i["name"] for i in data["items"]}
        assert "ItemA1" in names
        assert "ItemA2" in names
        assert data["total"] >= 2
        db.close()

    def test_user_does_not_see_other_workspace_items(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        _create_item(client, ws_a, name="ItemA")
        _create_item(client, ws_b, name="ItemB")

        resp = client.get("/api/hub/items", headers=ws_a)
        assert resp.status_code == 200
        data = resp.json()
        names = {i["name"] for i in data["items"]}
        assert "ItemA" in names
        assert "ItemB" not in names
        db.close()

    def test_platform_admin_sees_all(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        _create_item(client, ws_a, name="AdminItemA")
        _create_item(client, ws_b, name="AdminItemB")

        resp = client.get("/api/hub/items", headers=_admin_headers())
        assert resp.status_code == 200
        data = resp.json()
        names = {i["name"] for i in data["items"]}
        assert "AdminItemA" in names
        assert "AdminItemB" in names
        db.close()

    def test_filter_coexists_with_workspace_filter(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        _create_item(client, ws_a, name="SkillA", item_type="skill")
        _create_item(client, ws_a, name="ToolA", item_type="tool")

        resp = client.get("/api/hub/items?type=skill", headers=ws_a)
        assert resp.status_code == 200
        data = resp.json()
        names = {i["name"] for i in data["items"]}
        assert "SkillA" in names
        assert "ToolA" not in names
        assert data["total"] == 1
        db.close()

    def test_pagination_total_is_filtered(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        _create_item(client, ws_a, name="A1")
        _create_item(client, ws_a, name="A2")
        _create_item(client, ws_b, name="B1")

        resp = client.get("/api/hub/items", headers=ws_a)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        db.close()


class TestGetItemTenantGuard:
    def test_same_tenant_get_item_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        item = _create_item(client, ws_a, name="VisibleItem")

        resp = client.get(f"/api/hub/items/{item['id']}", headers=ws_a)
        assert resp.status_code == 200
        assert resp.json()["name"] == "VisibleItem"
        db.close()

    def test_different_tenant_get_item_404(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="HiddenItem")

        resp = client.get(f"/api/hub/items/{item['id']}", headers=ws_b)
        assert resp.status_code == 404
        db.close()

    def test_platform_admin_get_other_tenant_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a")
        item = _create_item(client, ws_a, name="AdminSee")

        resp = client.get(f"/api/hub/items/{item['id']}", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["name"] == "AdminSee"
        db.close()

    def test_dev_mode_get_ok(self, monkeypatch):
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
        resp = client.get(f"/api/hub/items/{item['id']}")
        assert resp.status_code == 200
        dev_db.close()


class TestUpdateItemTenantGuard:
    def test_same_tenant_owner_update_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        item = _create_item(client, ws_a, name="UpdatableItem")

        resp = client.put(f"/api/hub/items/{item['id']}", json={
            "name": "UpdatedName",
        }, headers=ws_a)
        assert resp.status_code == 200
        assert resp.json()["name"] == "UpdatedName"
        db.close()

    def test_different_tenant_update_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        ws_b = _headers(ws="ws-b", actor="owner-b", roles="asset_owner")
        item = _create_item(client, ws_a, name="ProtectedItem")

        resp = client.put(f"/api/hub/items/{item['id']}", json={
            "name": "Hijack",
        }, headers=ws_b)
        assert resp.status_code == 403
        db.close()

    def test_same_tenant_not_owner_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a_owner = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        ws_a_other = _headers(ws="ws-a", actor="other-a", roles="contributor")
        item = _create_item(client, ws_a_owner, name="OwnedItem")

        resp = client.put(f"/api/hub/items/{item['id']}", json={
            "name": "Stolen",
        }, headers=ws_a_other)
        assert resp.status_code == 403
        db.close()

    def test_platform_admin_update_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        item = _create_item(client, ws_a, name="AdminUpdate")

        resp = client.put(f"/api/hub/items/{item['id']}", json={
            "name": "AdminChanged",
        }, headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["name"] == "AdminChanged"
        db.close()


class TestVersionsTenantGuard:
    def test_same_tenant_list_versions_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        item = _create_item(client, ws_a, name="VersionedItem")
        _create_version(client, ws_a, item["id"], "1.0.0")

        resp = client.get(f"/api/hub/items/{item['id']}/versions", headers=ws_a)
        assert resp.status_code == 200
        versions = resp.json()
        assert len(versions) >= 1
        db.close()

    def test_different_tenant_list_versions_404(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="PrivateVersioned")
        _create_version(client, ws_a, item["id"], "1.0.0")

        resp = client.get(f"/api/hub/items/{item['id']}/versions", headers=ws_b)
        assert resp.status_code == 404
        db.close()

    def test_same_tenant_get_version_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        item = _create_item(client, ws_a, name="GetVersionItem")
        v = _create_version(client, ws_a, item["id"], "1.0.0")

        resp = client.get(
            f"/api/hub/items/{item['id']}/versions/{v['id']}", headers=ws_a,
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == "1.0.0"
        db.close()

    def test_different_tenant_get_version_404(self, monkeypatch):
        client, db = _header_client(monkeypatch)

        ws_a = _headers(ws="ws-a", actor="owner-a", roles="asset_owner")
        ws_b = _headers(ws="ws-b")
        item = _create_item(client, ws_a, name="PrivateGetVersion")
        v = _create_version(client, ws_a, item["id"], "1.0.0")

        resp = client.get(
            f"/api/hub/items/{item['id']}/versions/{v['id']}", headers=ws_b,
        )
        assert resp.status_code == 404
        db.close()
