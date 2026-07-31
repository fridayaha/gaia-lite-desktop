"""Unit tests for PrincipalService + AuthMiddleware (ADR-016 Phase 0).

Validates the request→Principal resolution in dev mode (X-User-Id /
X-User-Roles / X-User-Attributes headers) and the anonymous fallback.
Phase 5 will add JWT verification tests; Phase 0 covers the dev-mode
contract that downstream code depends on.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.permission import Principal
from ontology.middleware.auth import AuthMiddleware
from ontology.services.principal_service import (
    PrincipalService,
    _parse_attributes,
    _parse_roles,
)


class TestParseHelpers:
    def test_parse_roles_empty(self):
        assert _parse_roles("") == []

    def test_parse_roles_single(self):
        assert _parse_roles("PLATFORM_ADMIN") == ["PLATFORM_ADMIN"]

    def test_parse_roles_multiple_with_spaces(self):
        assert _parse_roles("PLATFORM_ADMIN, SPACE_OWNER, Viewer") == [
            "PLATFORM_ADMIN", "SPACE_OWNER", "Viewer",
        ]

    def test_parse_attributes_empty(self):
        assert _parse_attributes("") == {}

    def test_parse_attributes_single(self):
        assert _parse_attributes("region=east") == {"region": "east"}

    def test_parse_attributes_multiple(self):
        attrs = _parse_attributes("region=east;department=sales;level=5")
        assert attrs == {"region": "east", "department": "sales", "level": "5"}

    def test_parse_attributes_malformed_skipped(self):
        """Malformed pairs (no '=') are skipped, not crashed."""
        assert _parse_attributes("region=east;garbage;level=5") == {
            "region": "east", "level": "5",
        }

    def test_parse_attributes_empty_value(self):
        assert _parse_attributes("region=;dept=sales") == {"region": "", "dept": "sales"}


class TestPrincipalSchema:
    def test_anonymous_principal_defaults(self):
        p = Principal.anonymous_principal()
        assert p.id == "anonymous"
        assert p.is_anonymous is True
        assert p.principal_type == "USER"
        assert p.attributes == {}
        assert p.groups == []
        assert p.roles == []
        assert p.markings == []
        assert p.home_organization is None

    def test_principal_is_frozen(self):
        """Principal is frozen — request.state consumers can't mutate it."""
        p = Principal.anonymous_principal()
        with pytest.raises(Exception):
            p.id = "mutated"  # type: ignore[misc]

    def test_from_user_builds_principal(self):
        from datetime import UTC, datetime

        from ontology.core.schemas.permission import User

        now = datetime.now(UTC)
        user = User(
            id="user-123",
            email="alice@example.com",
            subject="oidc-sub-alice",
            attributes={"region": "east", "department": "sales"},
            home_organization="org-1",
            created_at=now,
            updated_at=now,
        )
        p = Principal.from_user(user, groups=["g1", "g2"], roles=["VIEWER"])
        assert p.id == "user-123"
        assert p.is_anonymous is False
        assert p.display_name == "alice@example.com"
        assert p.attributes == {"region": "east", "department": "sales"}
        assert p.groups == ["g1", "g2"]
        assert p.roles == ["VIEWER"]
        assert p.home_organization == "org-1"


def _make_request(headers: dict[str, str] | None = None) -> MagicMock:
    """Build a mock starlette Request with the given headers."""
    request = MagicMock()
    request.headers = headers or {}
    return request


class TestPrincipalServiceDevMode:
    """Dev-mode header parsing (Phase 0)."""

    @pytest.mark.asyncio
    async def test_no_headers_returns_anonymous(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({}))
        assert principal.is_anonymous is True
        assert principal.id == "anonymous"

    @pytest.mark.asyncio
    async def test_user_id_header_resolves(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({"X-User-Id": "user-42"}))
        assert principal.is_anonymous is False
        assert principal.id == "user-42"
        assert principal.display_name == "user-42"  # falls back to id

    @pytest.mark.asyncio
    async def test_roles_header_parsed(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({
            "X-User-Id": "user-42",
            "X-User-Roles": "PLATFORM_ADMIN,SPACE_OWNER",
        }))
        assert principal.roles == ["PLATFORM_ADMIN", "SPACE_OWNER"]

    @pytest.mark.asyncio
    async def test_attributes_header_parsed(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({
            "X-User-Id": "user-42",
            "X-User-Attributes": "region=east;department=sales",
        }))
        assert principal.attributes == {"region": "east", "department": "sales"}

    @pytest.mark.asyncio
    async def test_display_name_override(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({
            "X-User-Id": "user-42",
            "X-User-Display": "Alice Doe",
        }))
        assert principal.display_name == "Alice Doe"

    @pytest.mark.asyncio
    async def test_home_org_header(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({
            "X-User-Id": "user-42",
            "X-User-Home-Org": "org-9",
        }))
        assert principal.home_organization == "org-9"

    @pytest.mark.asyncio
    async def test_empty_user_id_returns_anonymous(self):
        """X-User-Id with empty/whitespace value → anonymous (fail-closed)."""
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({"X-User-Id": "  "}))
        assert principal.is_anonymous is True

    @pytest.mark.asyncio
    async def test_full_header_combination(self):
        svc = PrincipalService(dev_mode=True)
        principal = await svc.resolve_principal(_make_request({
            "X-User-Id": "alice",
            "X-User-Display": "Alice",
            "X-User-Roles": "EDITOR,VIEWER",
            "X-User-Attributes": "region=west;level=3",
            "X-User-Home-Org": "org-default",
        }))
        assert principal.id == "alice"
        assert principal.display_name == "Alice"
        assert principal.roles == ["EDITOR", "VIEWER"]
        assert principal.attributes == {"region": "west", "level": "3"}
        assert principal.home_organization == "org-default"
        assert principal.is_anonymous is False


class TestAuthMiddleware:
    """AuthMiddleware injects the Principal onto request.state."""

    @pytest.mark.asyncio
    async def test_middleware_injects_principal(self):
        svc = PrincipalService(dev_mode=True)
        mw = AuthMiddleware(app=MagicMock(), principal_service=svc)
        request = _make_request({"X-User-Id": "bob"})
        # call_next returns a dummy response
        call_next = AsyncMock(return_value=MagicMock())
        await mw.dispatch(request, call_next)
        assert hasattr(request.state, "principal")
        assert request.state.principal.id == "bob"
        assert request.state.principal.is_anonymous is False

    @pytest.mark.asyncio
    async def test_middleware_anonymous_when_no_header(self):
        svc = PrincipalService(dev_mode=True)
        mw = AuthMiddleware(app=MagicMock(), principal_service=svc)
        request = _make_request({})
        call_next = AsyncMock(return_value=MagicMock())
        await mw.dispatch(request, call_next)
        assert request.state.principal.is_anonymous is True

    @pytest.mark.asyncio
    async def test_middleware_calls_call_next(self):
        """The middleware must invoke the next handler (doesn't swallow requests)."""
        svc = PrincipalService(dev_mode=True)
        mw = AuthMiddleware(app=MagicMock(), principal_service=svc)
        request = _make_request({})
        call_next = AsyncMock(return_value=MagicMock())
        await mw.dispatch(request, call_next)
        call_next.assert_awaited_once()
