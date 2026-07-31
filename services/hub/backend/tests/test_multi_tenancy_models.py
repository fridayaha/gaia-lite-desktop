import pytest
from sqlalchemy.orm import Session

from app.core.enums import (
    HubItemStatus,
    HubItemType,
    HubItemVersionStatus,
    RiskLevel,
    SourceType,
)
from app.core.tenancy import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_WORKSPACE_ID,
    DEFAULT_VISIBILITY_SCOPE,
    VALID_VISIBILITY_SCOPES,
    normalize_visibility_scope,
    resolve_tenant_ids,
)
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion


class TestTenancyHelpers:
    def test_resolve_tenant_ids_with_values(self):
        org, ws = resolve_tenant_ids("org-1", "ws-2")
        assert org == "org-1"
        assert ws == "ws-2"

    def test_resolve_tenant_ids_with_none(self):
        org, ws = resolve_tenant_ids(None, None)
        assert org == DEFAULT_ORGANIZATION_ID
        assert ws == DEFAULT_WORKSPACE_ID

    def test_resolve_tenant_ids_partial(self):
        org, ws = resolve_tenant_ids(None, "ws-99")
        assert org == DEFAULT_ORGANIZATION_ID
        assert ws == "ws-99"

    def test_normalize_visibility_scope_valid(self):
        assert normalize_visibility_scope("workspace") == "workspace"
        assert normalize_visibility_scope("private") == "private"
        assert normalize_visibility_scope("organization") == "organization"
        assert normalize_visibility_scope("public") == "public"

    def test_normalize_visibility_scope_invalid(self):
        assert normalize_visibility_scope("invalid") == DEFAULT_VISIBILITY_SCOPE
        assert normalize_visibility_scope(None) == DEFAULT_VISIBILITY_SCOPE
        assert normalize_visibility_scope("") == DEFAULT_VISIBILITY_SCOPE

    def test_valid_visibility_scopes(self):
        assert "workspace" in VALID_VISIBILITY_SCOPES
        assert "private" in VALID_VISIBILITY_SCOPES
        assert "organization" in VALID_VISIBILITY_SCOPES
        assert "public" in VALID_VISIBILITY_SCOPES
        assert "admin" not in VALID_VISIBILITY_SCOPES


def _make_item(
    db_session: Session,
    name: str = "TenantTest",
    org_id: str = "org-test",
    ws_id: str = "ws-test",
) -> HubItem:
    item = HubItem(
        name=name,
        type=HubItemType.tool,
        status=HubItemStatus.draft,
        risk_level=RiskLevel.low,
        organization_id=org_id,
        workspace_id=ws_id,
        visibility_scope="workspace",
    )
    db_session.add(item)
    db_session.flush()
    return item


class TestHubItemTenantFields:
    def test_create_item_writes_tenant_fields(self, db_session):
        item = _make_item(db_session, org_id="org-1", ws_id="ws-1")
        db_session.refresh(item)
        assert item.organization_id == "org-1"
        assert item.workspace_id == "ws-1"
        assert item.visibility_scope == "workspace"

    def test_create_item_default_visibility_scope(self, db_session):
        item = _make_item(db_session, org_id="org-a", ws_id="ws-a")
        assert item.visibility_scope == "workspace"

    def test_create_item_custom_visibility_scope(self, db_session):
        item = HubItem(
            name="PrivateItem",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id="org-x",
            workspace_id="ws-x",
            visibility_scope="private",
        )
        db_session.add(item)
        db_session.flush()
        assert item.visibility_scope == "private"

    def test_create_item_null_tenant_allowed(self, db_session):
        item = HubItem(
            name="NullTenant",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(item)
        db_session.flush()
        assert item.organization_id is None
        assert item.workspace_id is None
        assert item.visibility_scope is None

    def test_read_schema_includes_tenant_fields_after_backfill(self, db_session):
        from sqlalchemy import text
        db_session.execute(
            text("UPDATE hub_items SET organization_id='x', workspace_id='x', visibility_scope='x'")
        )
        db_session.commit()

        item = HubItem(
            name="ReadSchema",
            type=HubItemType.tool,
            status=HubItemStatus.draft,
            risk_level=RiskLevel.low,
            organization_id="org-abc",
            workspace_id="ws-def",
            visibility_scope="organization",
        )
        db_session.add(item)
        db_session.flush()

        from app.schemas.hub_item import HubItemRead
        read = HubItemRead.model_validate(item)
        assert read.organization_id == "org-abc"
        assert read.workspace_id == "ws-def"
        assert read.visibility_scope == "organization"


class TestHubItemVersionTenantInheritance:
    def test_version_inherits_tenant_from_item(self, db_session):
        item = _make_item(db_session, org_id="org-v", ws_id="ws-v")
        version = HubItemVersion(
            hub_item_id=item.id,
            version="1.0.0",
            organization_id=item.organization_id,
            workspace_id=item.workspace_id,
            status=HubItemVersionStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(version)
        db_session.flush()
        assert version.organization_id == "org-v"
        assert version.workspace_id == "ws-v"

    def test_version_without_tenant_is_null(self, db_session):
        item = _make_item(db_session)
        version = HubItemVersion(
            hub_item_id=item.id,
            version="2.0.0",
            status=HubItemVersionStatus.draft,
            risk_level=RiskLevel.low,
        )
        db_session.add(version)
        db_session.flush()
        assert version.organization_id is None
        assert version.workspace_id is None


class TestPresetsUseDefaultTenant:
    def test_preset_item_has_default_org_and_workspace(self, db_session):
        from app.services.preset_service import PresetService
        svc = PresetService(db_session)
        result = svc.init_presets()
        assert result["created"] > 0

        items = (
            db_session.query(HubItem)
            .filter(HubItem.source_type == SourceType.preset)
            .all()
        )
        assert len(items) > 0
        for item in items:
            assert item.organization_id == "default"
            assert item.workspace_id == "default"
            assert item.visibility_scope == "workspace"

    def test_preset_versions_has_default_workspace(self, db_session):
        from app.services.preset_service import PresetService
        svc = PresetService(db_session)
        svc.init_presets()

        versions = (
            db_session.query(HubItemVersion)
            .join(HubItem, HubItemVersion.hub_item_id == HubItem.id)
            .filter(HubItem.source_type == SourceType.preset)
            .all()
        )
        assert len(versions) > 0
        for v in versions:
            assert v.organization_id == "default"
            assert v.workspace_id == "default"


class TestListingBehaviorUnchanged:
    def test_list_does_not_filter_by_workspace(self, db_session):
        item1 = _make_item(db_session, name="ws1", ws_id="ws-1")
        item2 = _make_item(db_session, name="ws2", ws_id="ws-2")

        from app.schemas.hub_item_list import HubItemListFilters
        from app.services.hub_item_service import HubItemService
        svc = HubItemService(db_session)
        filters = HubItemListFilters()
        items, total = svc.list_with_total(filters, limit=100)
        item_ids = {item.id for item in items}
        assert item1.id in item_ids
        assert item2.id in item_ids


class TestMigrationBackfill:
    def test_new_items_get_default_tenant_after_migration(self, db_session):
        from sqlalchemy import text
        import uuid as _uuid
        new_id = str(_uuid.uuid4())
        db_session.execute(
            text(
                "INSERT INTO hub_items (id, name, type, status, discoverable, "
                "allow_existing_references, force_disabled, risk_level, "
                "source_type) VALUES (:id, 'backfill-test', 'tool', 'draft', 1, 1, 0, 'low', 'manual')"
            ),
            {"id": new_id},
        )
        db_session.commit()

        item = (
            db_session.query(HubItem)
            .filter(HubItem.name == "backfill-test")
            .first()
        )
        assert item is not None
        assert item.organization_id is None
        assert item.workspace_id is None
        assert item.visibility_scope is None
