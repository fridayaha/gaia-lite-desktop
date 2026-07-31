import json
import uuid

from app.core.enums import HubItemStatus, HubItemType, HubItemVersionStatus, RiskLevel, RelationType, RelationScope
from app.models.hub_item import HubItem
from app.models.hub_item_relation import HubItemRelation
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


def _h(actor="user-1", roles="asset_owner", org="org-a", ws="ws-a"):
    return {
        "X-Actor-ID": actor,
        "X-Roles": roles,
        "X-Organization-ID": org,
        "X-Workspace-ID": ws,
    }


def _admin():
    return {
        "X-Actor-ID": "admin-1",
        "X-Roles": "platform_admin",
        "X-Organization-ID": "org-adm",
        "X-Workspace-ID": "ws-adm",
    }


def _create(client, h, name="Test", item_type="tool"):
    resp = client.post("/api/hub/items", json={"name": name, "type": item_type}, headers=h)
    assert resp.status_code == 201
    return resp.json()


def _create_ver(client, h, item_id, ver="1.0.0"):
    resp = client.post(f"/api/hub/items/{item_id}/versions", json={
        "hub_item_id": item_id, "version": ver,
    }, headers=h)
    return resp.json()


class TestRelationCreate:
    def test_same_tenant_create_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        src = _create(client, h, name="Src", item_type="skill")
        tgt = _create(client, h, name="Tgt", item_type="mcp")
        resp = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=h)
        assert resp.status_code == 201
        db.close()

    def test_different_tenant_create_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")
        src = _create(client, ha, name="SrcCross", item_type="skill")
        tgt = _create(client, hb, name="TgtCross", item_type="mcp")
        resp = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=hb)
        assert resp.status_code == 403
        db.close()

    def test_target_different_source_same_allow(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")
        src = _create(client, ha, name="SrcSame", item_type="skill")
        tgt = _create(client, hb, name="TgtOther", item_type="mcp")
        resp = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=ha)
        assert resp.status_code == 201
        db.close()

    def test_admin_create_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        src = _create(client, ha, name="SrcAdmin", item_type="skill")
        tgt = _create(client, ha, name="TgtAdmin", item_type="mcp")
        resp = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=_admin())
        assert resp.status_code == 201
        db.close()


class TestRelationDetail:
    def test_same_tenant_detail_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        src = _create(client, h, name="DetailSrc", item_type="skill")
        tgt = _create(client, h, name="DetailTgt", item_type="mcp")
        r = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=h)
        rid = r.json()["id"]
        resp = client.get(f"/api/hub/relations/{rid}", headers=h)
        assert resp.status_code == 200
        db.close()

    def test_different_tenant_detail_404(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")
        src = _create(client, ha, name="HiddenRelSrc", item_type="skill")
        tgt = _create(client, ha, name="HiddenRelTgt", item_type="mcp")
        r = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=ha)
        rid = r.json()["id"]
        resp = client.get(f"/api/hub/relations/{rid}", headers=hb)
        assert resp.status_code == 404
        db.close()


class TestRelationDelete:
    def test_same_tenant_delete_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        src = _create(client, h, name="DelSrc", item_type="skill")
        tgt = _create(client, h, name="DelTgt", item_type="mcp")
        r = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=h)
        rid = r.json()["id"]
        resp = client.delete(f"/api/hub/relations/{rid}", headers=h)
        assert resp.status_code == 204
        db.close()

    def test_different_tenant_delete_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")
        src = _create(client, ha, name="NoDelSrc", item_type="skill")
        tgt = _create(client, ha, name="NoDelTgt", item_type="mcp")
        r = client.post("/api/hub/relations", json={
            "source_item_id": src["id"], "target_item_id": tgt["id"],
            "relation_type": "depends_on", "relation_scope": "runtime",
            "required": True,
        }, headers=ha)
        rid = r.json()["id"]
        resp = client.delete(f"/api/hub/relations/{rid}", headers=hb)
        assert resp.status_code == 403
        db.close()


class TestExportTenantGuard:
    def test_export_item_same_tenant_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="ExportMe")
        _create_ver(client, h, item["id"])
        resp = client.get(f"/api/hub/exports/items/{item['id']}", headers=h)
        assert resp.status_code == 200
        db.close()

    def test_export_item_different_tenant_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")
        item = _create(client, ha, name="NoExport")
        _create_ver(client, ha, item["id"])
        resp = client.get(f"/api/hub/exports/items/{item['id']}", headers=hb)
        assert resp.status_code == 403
        db.close()

    def test_export_package_same_tenant_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="PkgExport")
        v = _create_ver(client, h, item["id"])
        resp = client.get(
            f"/api/hub/exports/items/{item['id']}/versions/{v['id']}/package",
            headers=h,
        )
        assert resp.status_code == 200
        db.close()

    def test_export_package_different_tenant_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")
        item = _create(client, ha, name="NoPkgExport")
        v = _create_ver(client, ha, item["id"])
        resp = client.get(
            f"/api/hub/exports/items/{item['id']}/versions/{v['id']}/package",
            headers=hb,
        )
        assert resp.status_code == 403
        db.close()

    def test_admin_export_other_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        item = _create(client, ha, name="AdminExport")
        _create_ver(client, ha, item["id"])
        resp = client.get(f"/api/hub/exports/items/{item['id']}", headers=_admin())
        assert resp.status_code == 200
        db.close()


class TestImportExistingItemMatch:
    def test_import_isolated_existing_match(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        ha = _h(ws="ws-a")
        hb = _h(ws="ws-b")

        manifest = {"name": "ImportDedup", "type": "tool", "version": "1.0.0"}
        content = json.dumps(manifest).encode()
        client.post("/api/hub/imports/package",
                    files={"file": ("test.json", content, "application/json")},
                    headers=ha)

        client.post("/api/hub/imports/package",
                    files={"file": ("test.json", content, "application/json")},
                    headers=hb)

        items_a = db.query(HubItem).filter(
            HubItem.name == "ImportDedup", HubItem.workspace_id == "ws-a",
        ).all()
        items_b = db.query(HubItem).filter(
            HubItem.name == "ImportDedup", HubItem.workspace_id == "ws-b",
        ).all()
        assert len(items_a) == 1
        assert len(items_b) == 1
        db.close()
