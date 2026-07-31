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


def _h(actor="user-1", roles="asset_owner", org="org-a", ws="ws-a"):
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


def _approver_h(ws="ws-a"):
    return {
        "X-Actor-ID": "approver-1",
        "X-Roles": "business_approver",
        "X-Organization-ID": "org-a",
        "X-Workspace-ID": ws,
    }


def _create(client, h, name="A"):
    resp = client.post("/api/hub/items", json={"name": name, "type": "tool"}, headers=h)
    assert resp.status_code == 201
    return resp.json()


def _create_ver(client, h, item_id, ver="1.0.0"):
    resp = client.post(f"/api/hub/items/{item_id}/versions",
                       json={"hub_item_id": item_id, "version": ver}, headers=h)
    assert resp.status_code == 201
    return resp.json()


def _prepare_for_approve(db, item_id, version_id):
    import uuid
    version = db.get(HubItemVersion, uuid.UUID(version_id))
    version.status = HubItemVersionStatus.pending_review
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
    db.commit()


class TestApproveTenantGuard:
    def test_same_tenant_approve_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="ApproveMe")
        v = _create_ver(client, h, item["id"])
        _prepare_for_approve(db, item["id"], v["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/approve",
                           json={"operator": "ap"}, headers=_approver_h(ws="ws-a"))
        assert resp.status_code == 200
        db.close()

    def test_different_tenant_approve_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="NoApprove")
        v = _create_ver(client, h, item["id"])
        _prepare_for_approve(db, item["id"], v["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/approve",
                           json={"operator": "ap"}, headers=_approver_h(ws="ws-b"))
        assert resp.status_code == 403
        db.close()

    def test_admin_approve_other_tenant_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="AdminApprove")
        v = _create_ver(client, h, item["id"])
        _prepare_for_approve(db, item["id"], v["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/approve",
                           json={"operator": "adm"}, headers=_admin_headers())
        assert resp.status_code == 200
        db.close()


class TestRejectTenantGuard:
    def test_different_tenant_reject_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="NoReject")
        v = _create_ver(client, h, item["id"])
        _prepare_for_approve(db, item["id"], v["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/reject",
                           json={"operator": "rj"}, headers=_approver_h(ws="ws-b"))
        assert resp.status_code == 403
        db.close()


class TestRequestChangeTenantGuard:
    def test_different_tenant_request_change_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="NoRC")
        v = _create_ver(client, h, item["id"])
        _prepare_for_approve(db, item["id"], v["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/request-change",
                           json={"operator": "rc"}, headers=_approver_h(ws="ws-b"))
        assert resp.status_code == 403
        db.close()


class TestScanTenantGuard:
    def test_different_tenant_scan_403(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        b = _h(ws="ws-b")
        item = _create(client, h, name="NoScan")
        v = _create_ver(client, h, item["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/scan",
                           json={"operator": "sc"}, headers=b)
        assert resp.status_code == 403
        db.close()

    def test_same_tenant_scan_report_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="ScanMe")
        v = _create_ver(client, h, item["id"])

        client.post(f"/api/hub/versions/{v['id']}/scan",
                    json={"operator": "sc"}, headers=h)

        resp = client.get(f"/api/hub/versions/{v['id']}/scan-report", headers=h)
        assert resp.status_code == 200
        db.close()

    def test_different_tenant_scan_report_404(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        b = _h(ws="ws-b")
        item = _create(client, h, name="HiddenScan")
        v = _create_ver(client, h, item["id"])

        client.post(f"/api/hub/versions/{v['id']}/scan",
                    json={"operator": "sc"}, headers=h)

        resp = client.get(f"/api/hub/versions/{v['id']}/scan-report", headers=b)
        assert resp.status_code == 404
        db.close()


class TestScanTenantGuardOwnWorkspace:
    def test_same_tenant_scan_ok(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        h = _h(ws="ws-a")
        item = _create(client, h, name="ScanOk")
        v = _create_ver(client, h, item["id"])

        resp = client.post(f"/api/hub/versions/{v['id']}/scan",
                           json={"operator": "sc"}, headers=h)
        assert resp.status_code == 200
        db.close()
