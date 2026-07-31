from dataclasses import dataclass

import pytest

from app.core.auth_context import AuthContext
from app.policies.tenant_policy import (
    can_access_tenant,
    is_dev_mode,
    is_legacy_tenant,
    is_missing_tenant,
    is_platform_admin,
    is_same_tenant,
)


def _ctx(
    actor_id: str = "user-1",
    roles: str = "contributor",
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
class FakeAsset:
    organization_id: str | None = None
    workspace_id: str | None = None


class TestPlatformAdmin:
    def test_is_platform_admin_returns_true(self):
        ctx = _ctx(roles="platform_admin")
        assert is_platform_admin(ctx) is True

    def test_is_platform_admin_returns_false(self):
        ctx = _ctx(roles="contributor")
        assert is_platform_admin(ctx) is False

    def test_can_access_allows_platform_admin(self):
        ctx = _ctx(roles="platform_admin", org_id="other", ws_id="other")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, obj) is True


class TestDevMode:
    def test_is_dev_mode_returns_true(self):
        ctx = _ctx(auth_mode="dev")
        assert is_dev_mode(ctx) is True

    def test_is_dev_mode_returns_false_for_header(self):
        ctx = _ctx(auth_mode="header")
        assert is_dev_mode(ctx) is False

    def test_is_dev_mode_returns_false_for_none(self):
        ctx = _ctx(auth_mode="none")
        assert is_dev_mode(ctx) is False

    def test_can_access_allows_dev_mode(self):
        ctx = _ctx(auth_mode="dev", org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-b", workspace_id="ws-b")
        assert can_access_tenant(ctx, obj) is True


class TestIsMissingTenant:
    def test_none_values(self):
        obj = FakeAsset(organization_id=None, workspace_id=None)
        assert is_missing_tenant(obj) is True

    def test_empty_strings(self):
        obj = FakeAsset(organization_id="", workspace_id="")
        assert is_missing_tenant(obj) is True

    def test_whitespace_strings(self):
        obj = FakeAsset(organization_id="  ", workspace_id="\t")
        assert is_missing_tenant(obj) is True

    def test_one_missing_one_present(self):
        obj = FakeAsset(organization_id="org-a", workspace_id="")
        assert is_missing_tenant(obj) is True

    def test_both_present(self):
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert is_missing_tenant(obj) is False


class TestIsLegacyTenant:
    def test_none_is_legacy(self):
        obj = FakeAsset(organization_id=None, workspace_id=None)
        assert is_legacy_tenant(obj) is True

    def test_default_is_legacy(self):
        obj = FakeAsset(organization_id="default", workspace_id="default")
        assert is_legacy_tenant(obj) is True

    def test_empty_is_legacy(self):
        obj = FakeAsset(organization_id="", workspace_id="")
        assert is_legacy_tenant(obj) is True

    def test_non_default_is_not_legacy(self):
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert is_legacy_tenant(obj) is False

    def test_partial_default_is_not_legacy(self):
        obj = FakeAsset(organization_id="default", workspace_id="ws-a")
        assert is_legacy_tenant(obj) is False


class TestIsSameTenant:
    def test_same_org_and_workspace(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert is_same_tenant(ctx, obj) is True

    def test_different_org_same_workspace(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-b", workspace_id="ws-a")
        assert is_same_tenant(ctx, obj) is False

    def test_same_org_different_workspace(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-b")
        assert is_same_tenant(ctx, obj) is False

    def test_different_org_and_workspace(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-b", workspace_id="ws-b")
        assert is_same_tenant(ctx, obj) is False

    def test_ctx_missing_ws_falls_back_to_default(self):
        ctx = _ctx(org_id=None, ws_id=None)
        obj = FakeAsset(organization_id="default", workspace_id="default")
        assert is_same_tenant(ctx, obj) is True

    def test_ctx_missing_org_only(self):
        ctx = _ctx(org_id=None, ws_id="ws-a")
        obj = FakeAsset(organization_id="default", workspace_id="ws-a")
        assert is_same_tenant(ctx, obj) is True

    def test_obj_missing_ws_normalized_to_default(self):
        ctx = _ctx(org_id="org-a", ws_id="default")
        obj = FakeAsset(organization_id="org-a", workspace_id="")
        assert is_same_tenant(ctx, obj) is True

    def test_obj_empty_string_normalized(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert is_same_tenant(ctx, obj) is True


class TestCanAccessTenantSameWorkspace:
    def test_same_tenant_allow(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, obj) is True

    def test_different_tenant_deny(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-b", workspace_id="ws-b")
        assert can_access_tenant(ctx, obj) is False

    def test_different_org_same_workspace_deny(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-b", workspace_id="ws-a")
        assert can_access_tenant(ctx, obj) is False

    def test_same_org_different_workspace_deny(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-b")
        assert can_access_tenant(ctx, obj) is False


class TestCanAccessTenantLegacy:
    def test_legacy_obj_ctx_default_allow(self):
        ctx = _ctx(org_id="default", ws_id="default")
        obj = FakeAsset(organization_id="default", workspace_id="default")
        assert can_access_tenant(ctx, obj) is True

    def test_legacy_obj_ctx_default_explicit_default_allow(self):
        ctx = _ctx(org_id="default", ws_id="default")
        obj = FakeAsset(organization_id=None, workspace_id=None)
        assert can_access_tenant(ctx, obj) is True

    def test_legacy_obj_ctx_nondefault_legacy_visible_false_deny(self, monkeypatch):
        monkeypatch.setenv("HUB_TENANT_LEGACY_VISIBLE", "false")
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="default", workspace_id="default")
        assert can_access_tenant(ctx, obj) is False

    def test_legacy_obj_ctx_nondefault_legacy_visible_true_allow(self, monkeypatch):
        monkeypatch.setenv("HUB_TENANT_LEGACY_VISIBLE", "true")
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="default", workspace_id="default")
        assert can_access_tenant(ctx, obj) is True

    def test_normal_obj_ctx_default_deny(self):
        ctx = _ctx(org_id="default", ws_id="default")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, obj) is False


class TestActionParam:
    def test_action_read_allows(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, obj, action="read") is True

    def test_action_write_allows(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-a", workspace_id="ws-a")
        assert can_access_tenant(ctx, obj, action="write") is True

    def test_action_does_not_influence_deny(self):
        ctx = _ctx(org_id="org-a", ws_id="ws-a")
        obj = FakeAsset(organization_id="org-b", workspace_id="ws-b")
        assert can_access_tenant(ctx, obj, action="read") is False
        assert can_access_tenant(ctx, obj, action="write") is False


class TestInteractionWithRealModels:
    def test_can_access_with_hubitem_like_object(self):
        real = _ctx(org_id="org-x", ws_id="ws-x")
        obj = FakeAsset(organization_id="org-x", workspace_id="ws-x")
        assert can_access_tenant(real, obj) is True

    def test_can_access_with_partial_object(self):
        real = _ctx(org_id="org-x", ws_id="ws-x")
        obj = FakeAsset(organization_id="org-x")
        assert can_access_tenant(real, obj) is False
