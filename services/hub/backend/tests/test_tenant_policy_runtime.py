import os
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from app.core.auth_context import AuthContext
from app.policies.tenant_policy import (
    can_access_tenant,
    can_runtime_access_item,
)


def _ctx(
    actor_id: str = "user-1",
    roles: str = "runtime_consumer",
    org_id: str | None = None,
    ws_id: str | None = None,
    auth_mode: str = "header",
) -> AuthContext:
    return AuthContext(
        actor_id=actor_id,
        roles=[r.strip() for r in roles.split(",")],
        organization_id=org_id,
        workspace_id=ws_id,
        auth_mode=auth_mode,
    )


@dataclass
class FakeSkill:
    organization_id: str | None = "org-a"
    workspace_id: str | None = "ws-a"
    visibility_scope: str | None = "workspace"
    created_by: str | None = "user-1"


class TestCanRuntimeAccessItemBasic:
    """platform_admin / dev mode 可访问任意 visibility。"""

    def test_platform_admin_access_workspace(self):
        ctx = _ctx(roles="platform_admin", org_id="org-b", ws_id="ws-b")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is True

    def test_platform_admin_access_private(self):
        ctx = _ctx(roles="platform_admin", org_id="org-b", ws_id="ws-b")
        item = FakeSkill(visibility_scope="private", created_by="user-2")
        assert can_runtime_access_item(ctx, item) is True

    def test_dev_mode_access_any(self):
        ctx = _ctx(roles="runtime_consumer", org_id="org-b", ws_id="ws-b", auth_mode="dev")
        item = FakeSkill(visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is True

    def test_dev_mode_access_private(self):
        ctx = _ctx(roles="runtime_consumer", org_id="org-b", ws_id="ws-b", auth_mode="dev")
        item = FakeSkill(visibility_scope="private", created_by="user-2")
        assert can_runtime_access_item(ctx, item) is True


class TestWorkspaceVisibility:
    """visibility_scope=workspace: org+ws 同时匹配。"""

    def test_same_org_ws_allow(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is True

    def test_different_ws_deny(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-b")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is False

    def test_different_org_deny(self):
        ctx = _ctx(org_id="org-b", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is False

    def test_different_org_and_ws_deny(self):
        ctx = _ctx(org_id="org-b", ws_id="ws-b")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is False


class TestOrganizationVisibility:
    """visibility_scope=organization: org 匹配即可。"""

    def test_same_org_different_ws_allow(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-b")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="organization")
        assert can_runtime_access_item(ctx, item) is True

    def test_same_org_same_ws_allow(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="organization")
        assert can_runtime_access_item(ctx, item) is True

    def test_different_org_deny(self):
        ctx = _ctx(org_id="org-b", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="organization")
        assert can_runtime_access_item(ctx, item) is False


class TestPublicVisibility:
    """visibility_scope=public: 任意可访问。"""

    def test_different_org_ws_allow(self):
        ctx = _ctx(org_id="org-b", ws_id="ws-b")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="public")
        assert can_runtime_access_item(ctx, item) is True

    def test_same_org_ws_allow(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="public")
        assert can_runtime_access_item(ctx, item) is True


class TestPrivateVisibility:
    """visibility_scope=private: 仅 owner / platform_admin / dev 可见。"""

    def test_owner_allow(self):
        ctx = _ctx(actor_id="user-1", org_id="org-a", ws_id="ws-a")
        item = FakeSkill(visibility_scope="private", created_by="user-1")
        assert can_runtime_access_item(ctx, item) is True

    def test_non_owner_deny(self):
        ctx = _ctx(actor_id="user-2", org_id="org-a", ws_id="ws-a")
        item = FakeSkill(visibility_scope="private", created_by="user-1")
        assert can_runtime_access_item(ctx, item) is False

    def test_platform_admin_allow(self):
        ctx = _ctx(actor_id="user-2", roles="platform_admin")
        item = FakeSkill(visibility_scope="private", created_by="user-1")
        assert can_runtime_access_item(ctx, item) is True

    def test_dev_mode_allow(self):
        ctx = _ctx(actor_id="user-2", auth_mode="dev")
        item = FakeSkill(visibility_scope="private", created_by="user-1")
        assert can_runtime_access_item(ctx, item) is True

    def test_missing_actor_id_deny(self):
        ctx = _ctx(actor_id=None, roles="runtime_consumer")
        item = FakeSkill(visibility_scope="private", created_by="user-1")
        assert can_runtime_access_item(ctx, item) is False

    def test_missing_created_by_allow_if_actor_also_none(self):
        ctx = _ctx(actor_id=None, roles="runtime_consumer")
        item = FakeSkill(visibility_scope="private", created_by=None)
        assert can_runtime_access_item(ctx, item) is False


class TestNullUnknownVisibility:
    """visibility_scope 为 None / '' 处理为 workspace。"""

    def test_null_visibility_treated_as_workspace_allow(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope=None)
        assert can_runtime_access_item(ctx, item) is True

    def test_null_visibility_treated_as_workspace_deny(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-b")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope=None)
        assert can_runtime_access_item(ctx, item) is False

    def test_empty_visibility_treated_as_workspace(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="")
        assert can_runtime_access_item(ctx, item) is True

    def test_unknown_visibility_treated_as_workspace(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill(organization_id="org-a", workspace_id="ws-a", visibility_scope="unknown")
        assert can_runtime_access_item(ctx, item) is True


class TestLegacyDefault:
    """Legacy default 资产兼容。"""

    def test_default_ctx_allow_default_asset(self):
        ctx = _ctx(org_id="default", ws_id="default")
        item = FakeSkill(organization_id="default", workspace_id="default", visibility_scope="workspace")
        assert can_runtime_access_item(ctx, item) is True

    def test_non_default_ctx_deny_default_asset_legacy_visible_false(self):
        with patch.dict(os.environ, {"HUB_TENANT_LEGACY_VISIBLE": "false"}, clear=False):
            ctx = _ctx(org_id="org-a", ws_id="ws-a")
            item = FakeSkill(organization_id="default", workspace_id="default", visibility_scope="workspace")
            assert can_runtime_access_item(ctx, item) is False

    def test_non_default_ctx_allow_default_asset_legacy_visible_true(self):
        with patch.dict(os.environ, {"HUB_TENANT_LEGACY_VISIBLE": "true"}, clear=False):
            ctx = _ctx(org_id="org-a", ws_id="ws-a")
            item = FakeSkill(organization_id="default", workspace_id="default", visibility_scope="workspace")
            assert can_runtime_access_item(ctx, item) is True


class TestActionParameter:
    """action 参数不影响当前结果但接受。"""

    def test_action_discover(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill()
        assert can_runtime_access_item(ctx, item, action="discover") is True

    def test_action_resolve(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        item = FakeSkill()
        assert can_runtime_access_item(ctx, item, action="resolve") is True

    def test_action_does_not_make_private_accessible(self):
        ctx = _ctx(actor_id="user-2", org_id="org-a", ws_id="ws-a")
        item = FakeSkill(visibility_scope="private", created_by="user-1")
        assert can_runtime_access_item(ctx, item, action="discover") is False
        assert can_runtime_access_item(ctx, item, action="resolve") is False


class TestRegressionCanAccessTenant:
    """现有 can_access_tenant tests 仍通过。"""

    def test_same_tenant_same_org_ws(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a", roles="contributor")
        asset = FakeSkill(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, asset) is True

    def test_same_tenant_different_ws(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-b", roles="contributor")
        asset = FakeSkill(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, asset) is False
