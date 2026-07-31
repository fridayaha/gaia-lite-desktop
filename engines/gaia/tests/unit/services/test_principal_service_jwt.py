"""Tests for PrincipalService JWT mode (ADR-016/017 Phase 5).

Verifies the ``fastapi-betterauth``-based JWT verification path: the
``_claims_to_principal`` mapping (sub/email/roles/groups/markings/attributes
→ Gaia Principal) and the resolve_principal flow (Bearer header extraction,
fail-closed on missing/invalid token, admin role mapping).

The actual JWT signature verification is delegated to ``fastapi-betterauth``
(PyJWKClient + JWKS caching) — tested by that library. Here we mock
``_verify_token`` to return claims directly, focusing on the Principal
mapping logic (Gaia's responsibility).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ontology.services.principal_service import PrincipalService, build_principal_service


def _claims(**overrides):
    """A minimal Better Auth JWT claims dict (verified)."""
    base = {
        "sub": "user-123",
        "email": "alice@example.com",
        "roles": ["user"],
        "exp": 9999999999,
        "iss": "http://localhost:3000",
        "aud": "http://localhost:3000",
    }
    base.update(overrides)
    return base


class TestDevMode:
    """Dev mode: X-User-Id headers."""

    @pytest.mark.asyncio
    async def test_dev_mode_resolves_from_headers(self):
        svc = PrincipalService(dev_mode=True)
        request = MagicMock()
        request.headers = {
            "X-User-Id": "alice",
            "X-User-Roles": "VIEWER,EDITOR",
            "X-User-Attributes": "region=east",
        }
        principal = await svc.resolve_principal(request)
        assert principal.id == "alice"
        assert not principal.is_anonymous
        assert "VIEWER" in principal.roles
        assert "EDITOR" in principal.roles
        assert principal.attributes == {"region": "east"}

    @pytest.mark.asyncio
    async def test_dev_mode_anonymous_when_no_header(self):
        svc = PrincipalService(dev_mode=True)
        request = MagicMock()
        request.headers = {}
        principal = await svc.resolve_principal(request)
        assert principal.is_anonymous


class TestJWTMode:
    """Production mode: Bearer JWT via fastapi-betterauth."""

    def _make_request(self, auth_header: str | None):
        request = MagicMock()
        request.headers = {}
        if auth_header:
            request.headers["Authorization"] = auth_header
        return request

    @pytest.mark.asyncio
    async def test_valid_jwt_maps_to_principal(self):
        svc = PrincipalService(
            dev_mode=False,
            better_auth_url="http://localhost:3000",
            jwt_issuer="http://localhost:3000",
            jwt_audience="http://localhost:3000",
        )
        with patch.object(svc, "_verify_token", return_value=_claims()):
            request = self._make_request("Bearer valid.jwt.token")
            principal = await svc.resolve_principal(request)
        assert principal.id == "user-123"
        assert principal.display_name == "alice@example.com"
        assert not principal.is_anonymous

    @pytest.mark.asyncio
    async def test_admin_role_maps_to_platform_admin(self):
        """Better Auth 'admin' role → Gaia PLATFORM_ADMIN."""
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        with patch.object(svc, "_verify_token", return_value=_claims(roles=["admin"])):
            request = self._make_request("Bearer valid.jwt.token")
            principal = await svc.resolve_principal(request)
        assert "PLATFORM_ADMIN" in principal.roles
        assert "admin" in principal.roles  # original role preserved

    @pytest.mark.asyncio
    async def test_user_role_does_not_get_platform_admin(self):
        """Better Auth 'user' role → no Gaia role (must be granted via API)."""
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        with patch.object(svc, "_verify_token", return_value=_claims(roles=["user"])):
            request = self._make_request("Bearer valid.jwt.token")
            principal = await svc.resolve_principal(request)
        assert "PLATFORM_ADMIN" not in principal.roles
        assert principal.roles == ["user"]

    @pytest.mark.asyncio
    async def test_groups_markings_attributes_mapped(self):
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        with patch.object(
            svc,
            "_verify_token",
            return_value=_claims(
                groups=["grp-1"],
                markings=["PII"],
                attributes={"region": "east"},
                home_organization="org-default",
            ),
        ):
            request = self._make_request("Bearer valid.jwt.token")
            principal = await svc.resolve_principal(request)
        assert principal.groups == ["grp-1"]
        assert principal.markings == ["PII"]
        assert principal.attributes == {"region": "east"}
        assert principal.home_organization == "org-default"

    @pytest.mark.asyncio
    async def test_missing_bearer_returns_anonymous(self):
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        request = self._make_request(None)
        principal = await svc.resolve_principal(request)
        assert principal.is_anonymous

    @pytest.mark.asyncio
    async def test_invalid_token_returns_anonymous(self):
        """Token verification failure → anonymous (fail-closed)."""
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        with patch.object(svc, "_verify_token", side_effect=Exception("bad signature")):
            request = self._make_request("Bearer invalid.token.here")
            principal = await svc.resolve_principal(request)
        assert principal.is_anonymous

    @pytest.mark.asyncio
    async def test_missing_sub_returns_anonymous(self):
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        with patch.object(svc, "_verify_token", return_value={"email": "no-sub@example.com"}):
            request = self._make_request("Bearer valid.jwt.token")
            principal = await svc.resolve_principal(request)
        assert principal.is_anonymous

    @pytest.mark.asyncio
    async def test_empty_token_returns_anonymous(self):
        svc = PrincipalService(dev_mode=False, better_auth_url="http://localhost:3000")
        request = self._make_request("Bearer ")
        principal = await svc.resolve_principal(request)
        assert principal.is_anonymous


class TestBuildPrincipalService:
    """Factory: build_principal_service from settings."""

    def test_dev_mode_when_authz_dev_mode_true(self):
        with patch("ontology.services.principal_service.settings") as mock_settings:
            mock_settings.authz_dev_mode = True
            mock_settings.better_auth_url = ""
            mock_settings.better_auth_jwt_issuer = ""
            mock_settings.better_auth_jwt_audience = ""
            svc = build_principal_service()
        assert svc._dev_mode is True

    def test_production_mode_when_authz_dev_mode_false(self):
        with patch("ontology.services.principal_service.settings") as mock_settings:
            mock_settings.authz_dev_mode = False
            mock_settings.better_auth_url = "http://localhost:3000"
            mock_settings.better_auth_jwt_issuer = ""
            mock_settings.better_auth_jwt_audience = ""
            svc = build_principal_service()
        assert svc._dev_mode is False
        assert svc._better_auth_url == "http://localhost:3000"

    def test_production_mode_requires_better_auth_url(self):
        with patch("ontology.services.principal_service.settings") as mock_settings:
            mock_settings.authz_dev_mode = False
            mock_settings.better_auth_url = ""
            mock_settings.better_auth_jwt_issuer = ""
            mock_settings.better_auth_jwt_audience = ""
            svc = build_principal_service()
        # Falls back to dev mode when no URL
        assert svc._dev_mode is True

