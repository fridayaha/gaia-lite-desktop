"""Unit tests for ActionAuthorizer — three-layer permission checks (P1, ADR-011)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import ForbiddenError
from ontology.core.schemas.action import ActionContext
from ontology.core.schemas.ontology import ActionType
from ontology.core.schemas.permission import Principal
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.action_auth import ActionAuthorizer
from ontology.services.action_rule_engine import ActionRuleEngine


def _make_action_type(parameters: dict | None = None) -> ActionType:
    return ActionType(
        id="at1",
        ontology_id="ont1",
        api_name="approve_order",
        display_name="Approve",
        description="",
        affected_object_type_id="ot1",
        parameters=parameters or {},
        rules={},
        submission_criteria={},
        status="ACTIVE",
        risk_level="low",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock(spec=PostgresMetaStore)


@pytest.fixture
def mock_catalog() -> AsyncMock:
    cat = AsyncMock(spec=GravitinoRegistry)
    return cat


@pytest.fixture
def mock_authz() -> AsyncMock:
    """A permissive AuthorizationService mock (PDP allows by default).

    Individual tests override check_access / check_action_permission returns
    to test denial paths. Default-allow lets Layer 1 tests focus on the
    ADR-011 JSON permissions stacking logic.
    """
    az = AsyncMock()
    az.check_access.return_value = MagicMock(allowed=True, reason="")
    az.check_action_permission.return_value = set()
    return az


@pytest.fixture
def authorizer(mock_metadata, mock_catalog, mock_authz) -> ActionAuthorizer:
    return ActionAuthorizer(
        metadata=mock_metadata, catalog=mock_catalog,
        rule_engine=ActionRuleEngine(), authorization_service=mock_authz,
    )


def _ctx(**overrides) -> ActionContext:
    """ActionContext with a non-anonymous principal (passes PDP Layer 1).

    current_user and principal.display_name are kept in sync (the
    ActionContext validator mirrors them); tests that need a specific
    name should pass ``current_user`` only.
    """
    current_user = overrides.pop("current_user", "u1")
    return ActionContext(
        principal=Principal(id="u1", display_name=current_user, is_anonymous=False),
        current_user=current_user,
        **overrides,
    )


class TestLayer1ExecutePermission:
    def test_open_access_when_no_permissions(self, authorizer):
        """PDP allows + no extra permissions config → allowed."""
        at = _make_action_type(parameters={})
        import asyncio

        asyncio.run(authorizer.check_execute_permission(at, _ctx()))

    def test_role_allowlist_pass(self, authorizer):
        """PDP allows + caller holds a required role → allowed."""
        at = _make_action_type(parameters={"permissions": {"roles": ["manager", "approver"]}})
        ctx = _ctx(current_user="alice", user_roles=["manager"])
        import asyncio

        asyncio.run(authorizer.check_execute_permission(at, ctx))

    def test_role_allowlist_fail(self, authorizer):
        """PDP allows but caller lacks required role → forbidden (JSON stacking)."""
        at = _make_action_type(parameters={"permissions": {"roles": ["manager"]}})
        ctx = _ctx(current_user="intern", user_roles=["intern"])
        import asyncio

        with pytest.raises(ForbiddenError, match="roles"):
            asyncio.run(authorizer.check_execute_permission(at, ctx))

    def test_dynamic_condition_pass(self, authorizer):
        """PDP allows + condition referencing selectedObject passes when truthy."""
        at = _make_action_type(parameters={"permissions": {"condition": "selectedObject['owner'] == currentUser"}})
        ctx = _ctx(current_user="alice", selected_object={"owner": "alice"})
        import asyncio

        asyncio.run(authorizer.check_execute_permission(at, ctx))

    def test_dynamic_condition_fail(self, authorizer):
        """PDP allows but condition fails → ForbiddenError (JSON stacking)."""
        at = _make_action_type(parameters={"permissions": {"condition": "selectedObject['owner'] == currentUser"}})
        ctx = _ctx(current_user="bob", selected_object={"owner": "alice"})
        import asyncio

        with pytest.raises(ForbiddenError, match="Permission condition failed"):
            asyncio.run(authorizer.check_execute_permission(at, ctx))

    @pytest.mark.asyncio
    async def test_pdp_deny_short_circuits_json(self, authorizer, mock_authz):
        """PDP denies → ForbiddenError, JSON permissions not even evaluated."""
        from unittest.mock import MagicMock
        mock_authz.check_access.return_value = MagicMock(allowed=False, reason="No PROJECT role")
        at = _make_action_type(parameters={"permissions": {"roles": ["manager"]}})
        ctx = _ctx(user_roles=["manager"])  # would pass JSON, but PDP denies first
        with pytest.raises(ForbiddenError, match="No PROJECT role"):
            await authorizer.check_execute_permission(at, ctx)


class TestLayer2RowWritePermission:
    @pytest.mark.asyncio
    async def test_all_allowed_when_authz_permits(self, authorizer, mock_authz):
        """PDP permits → no forbidden objects."""
        mock_authz.check_action_permission.return_value = set()
        forbidden = await authorizer.check_row_write_permission("order", ["o1", "o2"], _ctx())
        assert forbidden == set()

    @pytest.mark.asyncio
    async def test_some_forbidden_when_authz_denies(self, authorizer, mock_authz):
        """PDP denies specific objects → they're in the forbidden set."""
        mock_authz.check_action_permission.return_value = {"o2"}
        forbidden = await authorizer.check_row_write_permission("order", ["o1", "o2"], _ctx())
        assert forbidden == {"o2"}

    @pytest.mark.asyncio
    async def test_all_forbidden_when_authz_denies_all(self, authorizer, mock_authz):
        """PDP denies the whole type → all objects forbidden."""
        mock_authz.check_action_permission.return_value = {"o1", "o2"}
        forbidden = await authorizer.check_row_write_permission("order", ["o1", "o2"], _ctx())
        assert forbidden == {"o1", "o2"}


class TestLayer3SensitiveParameters:
    def test_strips_sensitive_for_non_admin(self, authorizer):
        """Sensitive params removed for non-admin callers."""
        at = _make_action_type(parameters={"permissions": {"sensitive_params": ["credit_limit"]}})
        params = {"name": "alice", "credit_limit": 5000}
        filtered = authorizer.filter_sensitive_parameters(at, params, ActionContext(user_roles=["user"]))
        assert "credit_limit" not in filtered
        assert filtered["name"] == "alice"

    def test_keeps_sensitive_for_admin(self, authorizer):
        """Admin role sees all parameters."""
        at = _make_action_type(parameters={"permissions": {"sensitive_params": ["credit_limit"]}})
        params = {"name": "alice", "credit_limit": 5000}
        filtered = authorizer.filter_sensitive_parameters(at, params, ActionContext(user_roles=["admin"]))
        assert filtered["credit_limit"] == 5000

    def test_no_sensitive_config_passthrough(self, authorizer):
        """No sensitive_params config → nothing stripped."""
        at = _make_action_type(parameters={})
        params = {"name": "alice", "credit_limit": 5000}
        filtered = authorizer.filter_sensitive_parameters(at, params, ActionContext())
        assert filtered == params
