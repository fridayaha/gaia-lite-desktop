from app.core.auth_context import AuthContext
from app.core.rbac import (
    ROLE_PERMISSIONS,
    Permission,
    get_permissions_for_roles,
    has_permission,
    normalize_role,
)


class TestNormalizeRole:
    def test_lower(self):
        assert normalize_role("Security_Reviewer") == "security_reviewer"

    def test_strip(self):
        assert normalize_role("  contributor  ") == "contributor"

    def test_hyphen_to_underscore(self):
        assert normalize_role("security-reviewer") == "security_reviewer"

    def test_space_to_underscore(self):
        assert normalize_role("platform admin") == "platform_admin"

    def test_combined(self):
        assert normalize_role(" Business-Approver ") == "business_approver"


class TestRolePermissions:
    def test_admin_all_permissions(self):
        perms = ROLE_PERMISSIONS["platform_admin"]
        assert Permission.asset__create in perms
        assert Permission.asset__read in perms
        assert Permission.admin__configure in perms
        assert Permission.audit__read in perms

    def test_contributor_can_create(self):
        perms = ROLE_PERMISSIONS["contributor"]
        assert Permission.asset__create in perms
        assert Permission.version__create in perms
        assert Permission.review__submit in perms

    def test_contributor_cannot_approve(self):
        perms = ROLE_PERMISSIONS["contributor"]
        assert Permission.review__approve not in perms

    def test_contributor_cannot_publish(self):
        perms = ROLE_PERMISSIONS["contributor"]
        assert Permission.lifecycle__publish not in perms

    def test_contributor_cannot_delete_relation(self):
        perms = ROLE_PERMISSIONS["contributor"]
        assert Permission.relation__delete not in perms

    def test_contributor_can_create_relation(self):
        perms = ROLE_PERMISSIONS["contributor"]
        assert Permission.relation__create in perms

    def test_asset_owner_cannot_publish(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.lifecycle__publish not in perms

    def test_asset_owner_cannot_approve(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.review__approve not in perms

    def test_asset_owner_can_submit_review(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.review__submit in perms

    def test_asset_owner_cannot_disable(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.lifecycle__disable not in perms

    def test_asset_owner_cannot_archive(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.lifecycle__archive not in perms

    def test_asset_owner_cannot_rollback(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.lifecycle__rollback not in perms

    def test_publisher_can_publish(self):
        perms = ROLE_PERMISSIONS["publisher"]
        assert Permission.lifecycle__publish in perms

    def test_publisher_cannot_approve(self):
        perms = ROLE_PERMISSIONS["publisher"]
        assert Permission.review__approve not in perms

    def test_security_reviewer_can_approve(self):
        perms = ROLE_PERMISSIONS["security_reviewer"]
        assert Permission.review__approve in perms
        assert Permission.review__reject in perms
        assert Permission.review__request_change in perms

    def test_security_reviewer_cannot_create(self):
        perms = ROLE_PERMISSIONS["security_reviewer"]
        assert Permission.asset__create not in perms

    def test_business_approver_can_approve(self):
        perms = ROLE_PERMISSIONS["business_approver"]
        assert Permission.review__approve in perms
        assert Permission.review__reject in perms
        assert Permission.review__request_change in perms

    def test_business_approver_cannot_create(self):
        perms = ROLE_PERMISSIONS["business_approver"]
        assert Permission.asset__create not in perms

    def test_runtime_consumer_no_mgmt_perms(self):
        perms = ROLE_PERMISSIONS["runtime_consumer"]
        assert len(perms) == 0

    def test_auditor_can_read(self):
        perms = ROLE_PERMISSIONS["auditor"]
        assert Permission.asset__read in perms
        assert Permission.scan__read in perms
        assert Permission.audit__read in perms

    def test_auditor_cannot_write(self):
        perms = ROLE_PERMISSIONS["auditor"]
        assert Permission.asset__create not in perms
        assert Permission.review__approve not in perms
        assert Permission.lifecycle__publish not in perms

    def test_auditor_can_export(self):
        perms = ROLE_PERMISSIONS["auditor"]
        assert Permission.export__download in perms


class TestGetPermissionsForRoles:
    def test_single_role(self):
        perms = get_permissions_for_roles(["contributor"])
        assert Permission.asset__create in perms
        assert Permission.review__approve not in perms

    def test_multiple_roles_union(self):
        perms = get_permissions_for_roles(["contributor", "publisher"])
        assert Permission.asset__create in perms
        assert Permission.lifecycle__publish in perms
        assert Permission.review__approve not in perms

    def test_normalized_roles(self):
        perms = get_permissions_for_roles([" Platform-Admin "])
        assert Permission.admin__configure in perms

    def test_empty_roles(self):
        perms = get_permissions_for_roles([])
        assert len(perms) == 0

    def test_unknown_role(self):
        perms = get_permissions_for_roles(["nonexistent_role"])
        assert len(perms) == 0


class TestHasPermission:
    def test_has_permission_true(self):
        ctx = AuthContext(actor_id="u1", roles=["contributor"])
        assert has_permission(ctx, Permission.asset__create) is True

    def test_has_permission_false(self):
        ctx = AuthContext(actor_id="u1", roles=["contributor"])
        assert has_permission(ctx, Permission.review__approve) is False

    def test_no_roles(self):
        ctx = AuthContext(actor_id="u1", roles=[])
        assert has_permission(ctx, Permission.asset__read) is False

    def test_unknown_role(self):
        ctx = AuthContext(actor_id="u1", roles=["unknown"])
        assert has_permission(ctx, Permission.asset__read) is False

    def test_admin_has_all(self):
        ctx = AuthContext(actor_id="admin", roles=["platform_admin"])
        for attr in dir(Permission):
            if attr.endswith("__"):
                continue
            perm = getattr(Permission, attr)
            if perm.startswith("runtime"):
                continue
            assert has_permission(ctx, perm), f"admin should have {perm}"


class TestExportPermissions:
    def test_contributor_can_export(self):
        perms = ROLE_PERMISSIONS["contributor"]
        assert Permission.export__download in perms

    def test_publisher_can_export(self):
        perms = ROLE_PERMISSIONS["publisher"]
        assert Permission.export__download in perms

    def test_security_reviewer_can_export(self):
        perms = ROLE_PERMISSIONS["security_reviewer"]
        assert Permission.export__download in perms

    def test_business_approver_can_export(self):
        perms = ROLE_PERMISSIONS["business_approver"]
        assert Permission.export__download in perms

    def test_asset_owner_can_export(self):
        perms = ROLE_PERMISSIONS["asset_owner"]
        assert Permission.export__download in perms

    def test_admin_can_export(self):
        perms = ROLE_PERMISSIONS["platform_admin"]
        assert Permission.export__download in perms
