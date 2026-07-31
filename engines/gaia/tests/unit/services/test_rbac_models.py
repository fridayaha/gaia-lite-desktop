"""Tests for Phase 1 RBAC models + builtin roles + ActionAuthorizer internals.

Covers:
  - RoleModel / RoleAssignmentModel constraints (unique name, composite unique)
  - Builtin role seeding (11 roles, correct scope_type/permissions)
  - ActionAuthorizer internals switch (ADR-016 Phase 1): when AuthorizationService
    is wired, Layer 1 delegates to check_access (fail-closed); Layer 2 delegates
    to check_action_permission. Contract unchanged (returns forbidden set /
    raises ForbiddenError).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from ontology.core.models import Base
from ontology.core.models.permission import RoleAssignmentModel, RoleModel
from ontology.core.permission_roles import (
    BUILTIN_ROLES,
    OP_ACTION_TYPE_EXECUTE,
    OP_OBJECT_VIEW,
    OP_OBJECT_WRITE,
    get_builtin_role_names,
    is_builtin_role,
)


def _now() -> datetime:
    return datetime.now(UTC)
def _action_type(api_name: str):
    """Build an ActionType schema with required timestamps."""
    from ontology.core.schemas.ontology import ActionType

    return ActionType(
        id="at1", ontology_id="o1", api_name=api_name, display_name=api_name,
        parameters={}, rules={}, version=1, created_at=_now(), updated_at=_now(),
    )
@pytest.fixture
def in_memory_engine():
    engine = create_engine("sqlite://", echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
class TestRoleModel:
    def test_columns(self, in_memory_engine):
        inspector = inspect(in_memory_engine)
        cols = {c["name"] for c in inspector.get_columns("roles")}
        for expected in ("id", "name", "scope_type", "permissions", "description",
                         "is_builtin", "created_at", "updated_at"):
            assert expected in cols

    def test_name_unique(self, in_memory_engine):
        inspector = inspect(in_memory_engine)
        indexes = inspector.get_indexes("roles")
        name_idx = [i for i in indexes if "name" in i["column_names"] and i.get("unique")]
        assert len(name_idx) == 1

    def test_is_builtin_default_true(self, in_memory_engine):
        inspector = inspect(in_memory_engine)
        cols = {c["name"]: c for c in inspector.get_columns("roles")}
        assert cols["is_builtin"]["default"] is not None
class TestRoleAssignmentModel:
    def test_composite_unique(self, in_memory_engine):
        """A principal gets a role at most once per (role, scope_type, scope_id)."""
        from sqlalchemy.exc import IntegrityError

        with Session(in_memory_engine) as s:
            role = RoleModel(name="VIEWER", scope_type="PROJECT", permissions=[])
            s.add(role)
            s.flush()
            s.add(RoleAssignmentModel(
                principal_id="g1", role_id=role.id, scope_type="PROJECT", scope_id="p1"
            ))
            s.commit()
            s.add(RoleAssignmentModel(
                principal_id="g1", role_id=role.id, scope_type="PROJECT", scope_id="p1"
            ))
            with pytest.raises(IntegrityError):
                s.commit()

    def test_scope_id_nullable_for_global(self, in_memory_engine):
        """GLOBAL scope roles have scope_id = NULL."""
        inspector = inspect(in_memory_engine)
        cols = {c["name"]: c for c in inspector.get_columns("role_assignments")}
        assert cols["scope_id"]["nullable"] is True
class TestBuiltinRoles:
    def test_eleven_builtin_roles(self):
        assert len(BUILTIN_ROLES) == 11

    def test_expected_names(self):
        names = get_builtin_role_names()
        for expected in (
            "PLATFORM_ADMIN", "AUDIT_ADMIN", "MARKING_ADMIN",
            "SPACE_OWNER", "SPACE_EDITOR", "SPACE_VIEWER", "SPACE_DISCOVERER",
            "OWNER", "EDITOR", "VIEWER", "DISCOVERER",
        ):
            assert expected in names, f"Missing builtin role: {expected}"

    def test_scope_types(self):
        by_name = {r["name"]: r for r in BUILTIN_ROLES}
        assert by_name["PLATFORM_ADMIN"]["scope_type"] == "GLOBAL"
        assert by_name["SPACE_OWNER"]["scope_type"] == "SPACE"
        assert by_name["OWNER"]["scope_type"] == "PROJECT"

    def test_separation_of_duties(self):
        """MARKING_ADMIN has no project ops; AUDIT_ADMIN has only audit:read."""
        by_name = {r["name"]: r for r in BUILTIN_ROLES}
        marking_perms = set(by_name["MARKING_ADMIN"]["permissions"])
        audit_perms = set(by_name["AUDIT_ADMIN"]["permissions"])
        # MARKING_ADMIN cannot manage projects
        assert "project:admin" not in marking_perms
        # AUDIT_ADMIN can only read audit
        assert audit_perms == {"audit:read"}

    def test_viewer_cannot_write(self):
        by_name = {r["name"]: r for r in BUILTIN_ROLES}
        viewer_perms = set(by_name["VIEWER"]["permissions"])
        assert OP_OBJECT_VIEW in viewer_perms
        assert OP_OBJECT_WRITE not in viewer_perms

    def test_editor_can_write_and_execute(self):
        by_name = {r["name"]: r for r in BUILTIN_ROLES}
        editor_perms = set(by_name["EDITOR"]["permissions"])
        assert OP_OBJECT_WRITE in editor_perms
        assert OP_ACTION_TYPE_EXECUTE in editor_perms

    def test_is_builtin_role(self):
        assert is_builtin_role("VIEWER")
        assert not is_builtin_role("CUSTOM_ROLE")
class TestActionAuthorizerInternals:
    """ADR-016 Phase 1: ActionAuthorizer delegates to AuthorizationService.

    Contract unchanged: Layer 1 raises ForbiddenError; Layer 2 returns
    forbidden set. Internals switch from JSON/catalog to AuthorizationService.
    """

    @pytest.mark.asyncio
    async def test_layer1_denies_when_authz_says_no(self):
        from ontology.core.exceptions import ForbiddenError
        from ontology.core.schemas.action import ActionContext
        from ontology.core.schemas.permission import Principal
        from ontology.services.action_auth import ActionAuthorizer

        mock_authz = AsyncMock()
        mock_authz.check_access.return_value = MagicMock(
            allowed=False, reason="No PROJECT role"
        )
        authorizer = ActionAuthorizer(
            metadata=AsyncMock(), catalog=AsyncMock(), authorization_service=mock_authz
        )
        action_type = _action_type("Reject")
        ctx = ActionContext(principal=Principal(id="u1", is_anonymous=False))
        with pytest.raises(ForbiddenError):
            await authorizer.check_execute_permission(action_type, ctx)
        # Confirms the new internals: check_access was called (not JSON parsing).
        mock_authz.check_access.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_layer1_allows_when_authz_says_yes(self):
        from ontology.core.schemas.action import ActionContext
        from ontology.core.schemas.permission import Principal
        from ontology.services.action_auth import ActionAuthorizer

        mock_authz = AsyncMock()
        mock_authz.check_access.return_value = MagicMock(allowed=True)
        authorizer = ActionAuthorizer(
            metadata=AsyncMock(), catalog=AsyncMock(), authorization_service=mock_authz
        )
        action_type = _action_type("Allow")
        ctx = ActionContext(principal=Principal(id="u1", is_anonymous=False))
        # Should not raise.
        await authorizer.check_execute_permission(action_type, ctx)

    @pytest.mark.asyncio
    async def test_layer2_returns_forbidden_set_from_authz(self):
        from ontology.core.schemas.action import ActionContext
        from ontology.core.schemas.permission import Principal
        from ontology.services.action_auth import ActionAuthorizer

        mock_authz = AsyncMock()
        mock_authz.check_action_permission.return_value = {"obj-2", "obj-3"}
        authorizer = ActionAuthorizer(
            metadata=AsyncMock(), catalog=AsyncMock(), authorization_service=mock_authz
        )
        ctx = ActionContext(principal=Principal(id="u1", is_anonymous=False))
        forbidden = await authorizer.check_row_write_permission(
            "Invoice", ["obj-1", "obj-2", "obj-3"], ctx
        )
        assert forbidden == {"obj-2", "obj-3"}
        mock_authz.check_action_permission.assert_awaited_once()
