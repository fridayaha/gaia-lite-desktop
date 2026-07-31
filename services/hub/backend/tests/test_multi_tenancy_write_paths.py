import json
import uuid as _uuid

from app.core.enums import (
    HubItemStatus,
    HubItemType,
    HubItemVersionStatus,
    RiskLevel,
)
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.schemas.hub_item import HubItemCreate
from app.schemas.hub_item_version import HubItemVersionCreate


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


class TestCreateItemWritePaths:
    def test_create_item_uses_fallback_tenant(self, db_session):
        from app.services.hub_item_service import HubItemService
        svc = HubItemService(db_session)
        data = HubItemCreate(
            name="FallbackItem",
            type=HubItemType.tool,
            source_type="manual",
            risk_level=RiskLevel.low,
        )
        item = svc.create(data)
        db_session.refresh(item)
        assert item.workspace_id == "default"
        assert item.organization_id == "default"
        assert item.visibility_scope == "workspace"

    def test_create_item_explicit_tenant(self, db_session):
        from app.services.hub_item_service import HubItemService
        svc = HubItemService(db_session)
        data = HubItemCreate(
            name="ExplicitTenant",
            type=HubItemType.tool,
            source_type="manual",
            risk_level=RiskLevel.low,
        )
        item = svc.create(data, organization_id="org-a", workspace_id="ws-a")
        db_session.refresh(item)
        assert item.organization_id == "org-a"
        assert item.workspace_id == "ws-a"

    def test_create_item_visibility_scope_from_body(self, db_session):
        from app.services.hub_item_service import HubItemService
        svc = HubItemService(db_session)
        data = HubItemCreate(
            name="ScopeOrg",
            type=HubItemType.tool,
            source_type="manual",
            risk_level=RiskLevel.low,
            visibility_scope="organization",
        )
        item = svc.create(data)
        db_session.refresh(item)
        assert item.visibility_scope == "organization"

    def test_create_item_invalid_visibility_falls_back(self, db_session):
        from app.services.hub_item_service import HubItemService
        svc = HubItemService(db_session)
        data = HubItemCreate(
            name="InvalidScope",
            type=HubItemType.tool,
            source_type="manual",
            risk_level=RiskLevel.low,
            visibility_scope="invalid123",
        )
        item = svc.create(data)
        db_session.refresh(item)
        assert item.visibility_scope == "workspace"

    def test_create_item_via_api_with_org_ws_headers(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        headers = {
            "X-Actor-ID": "user-mt1",
            "X-Organization-ID": "org-a",
            "X-Workspace-ID": "ws-a",
            "X-Roles": "contributor",
        }
        resp = client.post("/api/hub/items", json={
            "name": "HeaderTenantItem",
            "type": "tool",
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["organization_id"] == "org-a"
        assert data["workspace_id"] == "ws-a"
        db.close()

    def test_create_item_via_api_missing_headers_falls_back(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        headers = {
            "X-Actor-ID": "user-x",
            "X-Roles": "contributor",
        }
        resp = client.post("/api/hub/items", json={
            "name": "NoHeadersItem",
            "type": "tool",
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["organization_id"] == "default"
        assert data["workspace_id"] == "default"
        db.close()


class TestCreateVersionWritePaths:
    def test_version_inherits_item_tenant(self, db_session):
        item = HubItem(
            name="ParentItem",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id="parent-org",
            workspace_id="parent-ws",
        )
        db_session.add(item)
        db_session.flush()

        from app.services.version_service import VersionService
        svc = VersionService(db_session)
        data = HubItemVersionCreate(hub_item_id=item.id, version="1.0.0", risk_level=RiskLevel.low)
        version = svc.create(item.id, data)
        assert version.organization_id == "parent-org"
        assert version.workspace_id == "parent-ws"

    def test_create_version_via_api_inherits_tenant(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        headers = {
            "X-Actor-ID": "user-v",
            "X-Organization-ID": "org-v",
            "X-Workspace-ID": "ws-v",
            "X-Roles": "contributor",
        }
        resp = client.post("/api/hub/items", json={"name": "VersionParent", "type": "tool"}, headers=headers)
        assert resp.status_code == 201
        item_id = resp.json()["id"]

        resp = client.post(f"/api/hub/items/{item_id}/versions", json={
            "hub_item_id": item_id,
            "version": "1.0.0",
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["organization_id"] == "org-v"
        assert data["workspace_id"] == "ws-v"
        db.close()


class TestImportWritePaths:
    def test_package_import_writes_tenant(self, db_session):
        manifest = {
            "name": "import-test-tool",
            "type": "tool",
            "version": "1.0.0",
            "description": "import test",
        }
        from fastapi import UploadFile
        from io import BytesIO
        from app.services.import_service import ImportService

        svc = ImportService(db_session)
        content = json.dumps(manifest).encode()
        file = UploadFile(
            filename="test.json",
            file=BytesIO(content),
        )
        result = svc.import_package(file, created_by="test")
        item_id = _uuid.UUID(result["item_id"])

        item = db_session.get(HubItem, item_id)
        assert item.organization_id == "default"
        assert item.workspace_id == "default"
        assert item.visibility_scope == "workspace"

        versions = (
            db_session.query(HubItemVersion)
            .filter(HubItemVersion.hub_item_id == item_id)
            .all()
        )
        assert len(versions) >= 1
        for v in versions:
            assert v.organization_id == "default"
            assert v.workspace_id == "default"

    def test_package_import_explicit_tenant(self, db_session):
        manifest = {
            "name": "import-explicit-tenant",
            "type": "tool",
            "version": "1.0.0",
            "description": "import with explicit tenant",
        }
        from fastapi import UploadFile
        from io import BytesIO
        from app.services.import_service import ImportService

        svc = ImportService(db_session)
        content = json.dumps(manifest).encode()
        file = UploadFile(filename="test.json", file=BytesIO(content))
        result = svc.import_package(
            file, created_by="test",
            organization_id="org-b", workspace_id="ws-b",
        )
        item_id = _uuid.UUID(result["item_id"])

        item = db_session.get(HubItem, item_id)
        assert item.organization_id == "org-b"
        assert item.workspace_id == "ws-b"

        versions = (
            db_session.query(HubItemVersion)
            .filter(HubItemVersion.hub_item_id == item_id)
            .all()
        )
        for v in versions:
            assert v.organization_id == "org-b"
            assert v.workspace_id == "ws-b"

    def test_package_import_via_api_with_org_ws_headers(self, monkeypatch):
        client, db = _header_client(monkeypatch)
        headers = {
            "X-Actor-ID": "user-import",
            "X-Organization-ID": "org-b",
            "X-Workspace-ID": "ws-b",
            "X-Roles": "contributor",
        }
        manifest = {"name": "api-import-tenant", "type": "tool", "version": "1.0.0"}
        content = json.dumps(manifest).encode()
        resp = client.post("/api/hub/imports/package", files={"file": ("test.json", content, "application/json")}, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        item_id = _uuid.UUID(data["item_id"])
        item = db.get(HubItem, item_id)
        assert item.organization_id == "org-b"
        assert item.workspace_id == "ws-b"
        db.close()


class TestOpenAPIImportWritePaths:
    def test_openapi_import_explicit_tenant(self, db_session):
        from app.services.openapi_import_service import OpenAPIImportService

        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        svc = OpenAPIImportService(db_session)
        result = svc.import_from_spec(
            json.dumps(spec).encode(), "openapi.json",
            created_by="test",
            organization_id="org-c", workspace_id="ws-c",
        )
        assert result["tools_created"] >= 1
        for tool_info in result["items"]:
            item_id = _uuid.UUID(tool_info["item_id"])
            item = db_session.get(HubItem, item_id)
            assert item.organization_id == "org-c"
            assert item.workspace_id == "ws-c"

            versions = (
                db_session.query(HubItemVersion)
                .filter(HubItemVersion.hub_item_id == item_id)
                .all()
            )
            for v in versions:
                assert v.organization_id == "org-c"
                assert v.workspace_id == "ws-c"


class TestRelationWritePaths:
    def test_relation_inherits_source_tenant(self, db_session):
        from app.services.relation_service import RelationService
        from app.schemas.hub_item_relation import RelationCreate
        from app.core.enums import RelationType, RelationScope

        source = HubItem(
            name="SourceItem", type=HubItemType.skill,
            status=HubItemStatus.draft, risk_level=RiskLevel.low,
            organization_id="org-src", workspace_id="ws-src",
        )
        target = HubItem(
            name="TargetItem", type=HubItemType.mcp,
            status=HubItemStatus.draft, risk_level=RiskLevel.low,
            organization_id="org-tgt", workspace_id="ws-tgt",
        )
        db_session.add_all([source, target])
        db_session.flush()

        svc = RelationService(db_session)
        data = RelationCreate(
            source_item_id=source.id, target_item_id=target.id,
            relation_type=RelationType.depends_on,
            relation_scope=RelationScope.runtime,
            required=True,
        )
        rel = svc.create(data)
        assert rel.organization_id == "org-src"
        assert rel.workspace_id == "ws-src"


class TestLifecycleAndApprovalWritePaths:
    def test_submit_item_produces_lifecycle_event_with_tenant(self, db_session):
        item = HubItem(
            name="SubmitItemTenant",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id="org-sub",
            workspace_id="ws-sub",
        )
        db_session.add(item)
        db_session.flush()

        from app.services.lifecycle_service import LifecycleService
        svc = LifecycleService(db_session)
        svc.submit_item(item.id, "operator-test")

        from app.models.lifecycle_event import LifecycleEvent
        events = (
            db_session.query(LifecycleEvent)
            .filter(LifecycleEvent.hub_item_id == item.id)
            .all()
        )
        assert len(events) >= 1
        assert events[0].organization_id == "org-sub"
        assert events[0].workspace_id == "ws-sub"

    def test_scan_report_has_tenant(self, db_session):
        item = HubItem(
            name="ScanTenant",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id="org-scan",
            workspace_id="ws-scan",
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            status=HubItemVersionStatus.draft,
            risk_level=RiskLevel.low,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        db_session.add(version)
        db_session.flush()

        from app.services.scan_service import ScanService
        svc = ScanService(db_session)
        report = svc.scan_version(version.id, operator="test")

        assert report.organization_id == "org-scan"
        assert report.workspace_id == "ws-scan"

    def test_scan_produces_lifecycle_event_with_tenant(self, db_session):
        item = HubItem(
            name="ScanEventTenant",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id="org-scan-ev",
            workspace_id="ws-scan-ev",
        )
        db_session.add(item)
        db_session.flush()
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            status=HubItemVersionStatus.draft,
            risk_level=RiskLevel.low,
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
        )
        db_session.add(version)
        db_session.flush()

        from app.services.scan_service import ScanService
        from app.models.lifecycle_event import LifecycleEvent
        svc = ScanService(db_session)
        svc.scan_version(version.id, operator="test")

        events = (
            db_session.query(LifecycleEvent)
            .filter(
                LifecycleEvent.hub_item_version_id == version.id,
                LifecycleEvent.event_type == "scanned",
            )
            .all()
        )
        assert len(events) >= 1
        assert events[0].organization_id == "org-scan-ev"
        assert events[0].workspace_id == "ws-scan-ev"


class TestListingBehaviorUnchanged:
    def test_list_does_not_filter_by_workspace(self, db_session):
        from app.schemas.hub_item_list import HubItemListFilters
        from app.services.hub_item_service import HubItemService

        item1 = HubItem(
            name="ws-item1", type=HubItemType.tool,
            status=HubItemStatus.draft, risk_level=RiskLevel.low,
            organization_id="org-1", workspace_id="ws-1",
        )
        item2 = HubItem(
            name="ws-item2", type=HubItemType.tool,
            status=HubItemStatus.draft, risk_level=RiskLevel.low,
            organization_id="org-2", workspace_id="ws-2",
        )
        db_session.add_all([item1, item2])
        db_session.flush()

        svc = HubItemService(db_session)
        filters = HubItemListFilters()
        items, total = svc.list_with_total(filters, limit=100)
        item_ids = {item.id for item in items}
        assert item1.id in item_ids
        assert item2.id in item_ids
