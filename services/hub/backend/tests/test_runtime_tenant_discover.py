import os
import uuid

import pytest
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.models.hub_item import HubItem
from app.schemas.runtime import RuntimeDiscoverFilters
from app.services.runtime_discover_service import RuntimeDiscoverService


def _make_item(
    db: Session,
    name: str,
    item_type: str = "agent",
    org_id: str = "org-a",
    ws_id: str = "ws-a",
    vis: str = "workspace",
    created_by: str = "user-1",
    discoverable: bool = True,
    risk_level: str = "low",
) -> HubItem:
    from app.core.enums import HubItemStatus, HubItemType, RiskLevel, SourceType

    item = HubItem(
        id=uuid.uuid4(),
        name=name,
        type=HubItemType(item_type),
        status=HubItemStatus.published,
        source_type=SourceType.manual,
        discoverable=discoverable,
        risk_level=RiskLevel(risk_level),
        force_disabled=False,
        organization_id=org_id,
        workspace_id=ws_id,
        visibility_scope=vis,
        created_by=created_by,
    )
    db.add(item)
    db.flush()

    from app.core.enums import HubItemVersionStatus
    from app.models.hub_item_version import HubItemVersion

    version = HubItemVersion(
        id=uuid.uuid4(),
        hub_item_id=item.id,
        version="1.0.0",
        status=HubItemVersionStatus.published,
        risk_level=RiskLevel(risk_level),
        organization_id=org_id,
        workspace_id=ws_id,
        created_by=created_by,
    )
    db.add(version)
    db.flush()

    item.current_version_id = version.id
    db.commit()
    return item


def _ctx(
    actor_id: str = "user-1",
    roles: str = "runtime_consumer",
    scopes: str = "capability:discover",
    org_id: str = "org-a",
    ws_id: str = "ws-a",
    auth_mode: str = "header",
) -> AuthContext:
    return AuthContext(
        actor_id=actor_id,
        roles=[r.strip() for r in roles.split(",") if r.strip()],
        scopes=[s.strip() for s in scopes.split(",") if s.strip()],
        organization_id=org_id,
        workspace_id=ws_id,
        is_authenticated=True,
        auth_mode=auth_mode,
    )


class TestWorkspaceVisibilityDiscover:
    """visibility_scope=workspace: 同 workspace 可见，跨 workspace 不可见。"""

    def test_same_workspace_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "ws-a-item", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "ws-a-item" in names
        assert total >= 1

    def test_different_workspace_not_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "ws-b-item", org_id="org-a", ws_id="ws-b", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "ws-b-item" not in names

    def test_different_org_not_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "org-b-item", org_id="org-b", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "org-b-item" not in names

    def test_only_matching_workspace_in_results(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "ws-a-1", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "ws-a-2", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "ws-b-1", org_id="org-a", ws_id="ws-b", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "ws-a-1" in names
        assert "ws-a-2" in names
        assert "ws-b-1" not in names
        assert total == 2


class TestPublicVisibilityDiscover:
    """visibility_scope=public: 跨 workspace 可见。"""

    def test_public_cross_workspace_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "public-item", org_id="org-b", ws_id="ws-b", vis="public")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "public-item" in names

    def test_public_cross_org_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "public-org", org_id="org-c", ws_id="ws-c", vis="public")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "public-org" in names


class TestOrganizationVisibilityDiscover:
    """visibility_scope=organization: 同 org 不同 ws 可见。"""

    def test_same_org_different_ws_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "org-item", org_id="org-a", ws_id="ws-b", vis="organization")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "org-item" in names

    def test_different_org_not_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "org-b-item", org_id="org-b", ws_id="ws-b", vis="organization")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "org-b-item" not in names


class TestPrivateVisibilityDiscover:
    """visibility_scope=private: owner 可见，non-owner 不可见。"""

    def test_private_owner_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "private-owner", vis="private", created_by="user-1")
        db_session.commit()

        ctx = _ctx(actor_id="user-1")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "private-owner" in names

    def test_private_non_owner_not_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "private-other", vis="private", created_by="user-1")
        db_session.commit()

        ctx = _ctx(actor_id="user-2")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "private-other" not in names


class TestPlatformAdminDiscover:
    """platform_admin 可见全部。"""

    def test_platform_admin_sees_all(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "pa-ws-a", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "pa-ws-b", org_id="org-a", ws_id="ws-b", vis="workspace")
        _make_item(db_session, "pa-private", vis="private", created_by="user-2")
        _make_item(db_session, "pa-public", org_id="org-c", ws_id="ws-c", vis="public")
        db_session.commit()

        ctx = _ctx(actor_id="admin", roles="platform_admin", org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "pa-ws-a" in names
        assert "pa-ws-b" in names
        assert "pa-private" in names
        assert "pa-public" in names
        assert total == 4


class TestDevModeDiscover:
    """dev mode 可见全部。"""

    def test_dev_mode_sees_all(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "dev-ws-a", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "dev-ws-b", org_id="org-a", ws_id="ws-b", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a", auth_mode="dev")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "dev-ws-a" in names
        assert "dev-ws-b" in names
        assert total == 2


class TestNullUnknownVisibilityDiscover:
    """null / unknown visibility_scope 按 workspace 处理。"""

    def test_null_visibility_workspace_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "null-vis", org_id="org-a", ws_id="ws-a", vis=None)
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "null-vis" in names

    def test_null_visibility_cross_ws_not_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "null-vis-b", org_id="org-a", ws_id="ws-b", vis=None)
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "null-vis-b" not in names

    def test_unknown_visibility_treated_as_workspace(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "unk-vis", org_id="org-a", ws_id="ws-a", vis="unknown")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "unk-vis" in names


class TestTotalAndPagination:
    """total 只统计可见资产，pagination 在过滤后作用。"""

    def test_total_only_counts_visible(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "vis-1", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "vis-2", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "vis-cross", org_id="org-a", ws_id="ws-b", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "vis-1" in names
        assert "vis-2" in names
        assert "vis-cross" not in names
        assert total == 2

    def test_pagination_on_visible_only(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "pg-1", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "pg-2", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "pg-3", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(
            RuntimeDiscoverFilters(limit=2, offset=0), ctx
        )
        assert len(results) == 2
        assert total == 3

    def test_pagination_offset(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "pg-off-1", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "pg-off-2", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "pg-off-3", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(
            RuntimeDiscoverFilters(limit=2, offset=1), ctx
        )
        assert total == 3
        assert len(results) <= 2


class TestFilterCoexistence:
    """type / keyword / risk filter 与 tenant filter 共存。"""

    def test_type_filter_with_tenant(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "agent-1", item_type="agent", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "tool-1", item_type="tool", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(
            RuntimeDiscoverFilters(type="agent"), ctx
        )
        names = {item.name for item, _ in results}
        assert "agent-1" in names
        assert "tool-1" not in names

    def test_keyword_filter_with_tenant(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "AlphaSearch", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "BetaTool", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(
            RuntimeDiscoverFilters(keyword="Alpha"), ctx
        )
        names = {item.name for item, _ in results}
        assert "AlphaSearch" in names
        assert "BetaTool" not in names

    def test_keyword_filter_cross_tenant_no_leak(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "GammaWSB", org_id="org-a", ws_id="ws-b", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(
            RuntimeDiscoverFilters(keyword="Gamma"), ctx
        )
        names = {item.name for item, _ in results}
        assert "GammaWSB" not in names


class TestExclusionsStillApply:
    """blocking / not discoverable 仍排除。"""

    def test_blocking_still_excluded(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "blocking-item", risk_level="blocking", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "normal-item", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "blocking-item" not in names
        assert "normal-item" in names

    def test_not_discoverable_still_excluded(self, db_session: Session):
        svc = RuntimeDiscoverService(db_session)
        _make_item(db_session, "hidden-item", discoverable=False, org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "hidden-item" not in names


class TestPolicyDenyStillApplies:
    """CapabilityAccessPolicy deny 仍静默排除。"""

    def test_policy_deny_excludes_after_tenant(self, db_session: Session):
        from app.policies.capability_access import CapabilityAccessPolicy

        class DenyItemAPolicy(CapabilityAccessPolicy):
            version = "deny-a"
            def __init__(self, denied_name: str):
                self.denied_name = denied_name

            def can_discover(self, item, version, context):
                return item.name != self.denied_name

            def can_resolve(self, item, version, context):
                return item.name != self.denied_name

        _make_item(db_session, "policy-deny", org_id="org-a", ws_id="ws-a", vis="workspace")
        _make_item(db_session, "policy-allow", org_id="org-a", ws_id="ws-a", vis="workspace")
        db_session.commit()

        svc = RuntimeDiscoverService(db_session, policy=DenyItemAPolicy(denied_name="policy-deny"))
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "policy-deny" not in names
        assert "policy-allow" in names
