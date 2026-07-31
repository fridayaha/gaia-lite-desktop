"""PrincipalService — resolves the request Principal (ADR-016/017).

The entry point of every request: turns an HTTP request into a Principal
object. Two modes (design §2.3, §3.1, §5.5):

  Production mode (Phase 5): Better Auth issues an OIDC JWT; ``fastapi-betterauth``
    verifies signature + expiry + iss/aud via PyJWKClient (built-in JWKS caching
    + key rotation); PrincipalService maps claims → Gaia Principal.
    The JWT carries identity + attributes (from OIDC claims) + roles/markings
    (encoded by Better Auth's ``definePayload`` callback).

  Dev mode (Phase 0): ``X-User-Id`` / ``X-User-Roles`` request headers.
    No Better Auth deployment needed — supports local development and testing.

The Principal object (identity + attributes + groups + roles + markings) is
the complete input to the AuthorizationService five-layer check. This service
is intentionally thin: it does NOT do authorization (that's
AuthorizationService's job). It only resolves *who* is asking.

JWT verification delegates to ``fastapi-betterauth`` (a mature community
library) which wraps PyJWKClient with built-in JWKS caching, key rotation,
EdDSA support, and iss/aud validation. This replaces the hand-rolled
Authlib JWKS fetch + KeySet cache (which had edge cases around key
rotation and sync I/O).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontology.config.settings import settings
from ontology.core.schemas.permission import Principal

if TYPE_CHECKING:
    from starlette.requests import Request

_log = logging.getLogger(__name__)

# Request header names for dev-mode principal resolution.
_USER_ID_HEADER = "X-User-Id"
_USER_ROLES_HEADER = "X-User-Roles"
_USER_ATTRIBUTES_HEADER = "X-User-Attributes"
_USER_DISPLAY_HEADER = "X-User-Display"
_HOME_ORG_HEADER = "X-User-Home-Org"

# JWT claim names (Better Auth + standard OIDC).
_CLAIM_SUB = "sub"
_CLAIM_EMAIL = "email"
_CLAIM_ROLES = "roles"
_CLAIM_GROUPS = "groups"
_CLAIM_MARKINGS = "markings"
_CLAIM_ATTRIBUTES = "attributes"
_CLAIM_HOME_ORG = "home_organization"


def _parse_roles(raw: str) -> list[str]:
    """Parse comma-separated role names into a list (dev-mode roles)."""
    return [r.strip() for r in raw.split(",") if r.strip()] if raw else []


def _parse_attributes(raw: str) -> dict[str, Any]:
    """Parse dev-mode attributes header (key=value;key=value format).

    Accepts semicolon-separated ``key=value`` pairs (the dev-mode
    X-User-Attributes header format). Malformed pairs (no ``=``) are
    skipped. Empty string → empty dict.
    """
    if not raw:
        return {}
    result: dict[str, Any] = {}
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        result[key.strip()] = value.strip()
    return result


class PrincipalService:
    """Resolves the Principal for an incoming request.

    ``dev_mode=True`` (default, controlled by ``settings.authz_dev_mode``):
    reads X-User-Id / X-User-Roles / X-User-Attributes headers. When False
    (Better Auth deployed), verifies the Bearer JWT via ``fastapi-betterauth``
    and maps claims → Principal.
    """

    def __init__(
        self,
        *,
        dev_mode: bool = True,
        better_auth_url: str | None = None,
        jwt_issuer: str | None = None,
        jwt_audience: str | None = None,
        metadata: Any | None = None,
    ) -> None:
        self._dev_mode = dev_mode
        self._better_auth_url = better_auth_url or ""
        self._jwt_issuer = jwt_issuer or ""
        self._jwt_audience = jwt_audience or ""
        self._metadata = metadata
        # The fastapi-betterauth verifier (lazy-initialized — only created
        # in production mode so dev mode doesn't require a Better Auth URL).
        self._verifier: Any | None = None

    async def resolve_principal(self, request: Request) -> Principal:
        """Resolve the Principal from the request.

        Dev mode: X-User-Id headers. Production: Bearer JWT via
        fastapi-betterauth. Returns an anonymous principal when no
        credential is present (fail-closed: AuthorizationService Layer 1
        denies non-public resources for anonymous, design §0.1 principle 4).
        """
        if self._dev_mode:
            return await self._resolve_dev(request)
        return await self._resolve_jwt(request)

    async def _resolve_dev(self, request: Request) -> Principal:
        user_id = request.headers.get(_USER_ID_HEADER, "").strip()
        if not user_id:
            return Principal.anonymous_principal()
        roles = _parse_roles(request.headers.get(_USER_ROLES_HEADER, ""))
        attributes = _parse_attributes(request.headers.get(_USER_ATTRIBUTES_HEADER, ""))
        display = request.headers.get(_USER_DISPLAY_HEADER, "").strip() or user_id
        home_org = request.headers.get(_HOME_ORG_HEADER, "").strip() or None
        # In dev mode, if X-User-Roles is NOT provided, try to load the user's
        # groups + roles from the DB (so DB-driven RBAC works in dev tests).
        # When X-User-Roles IS provided, it overrides (quick impersonation).
        groups: list[str] = []
        if not roles and self._metadata is not None:
            try:
                # Look up the user by subject (dev-mode X-User-Id = subject).
                user = await self._metadata.get_user_by_subject(user_id)
                if user is not None:
                    user_groups = await self._metadata.list_user_groups(user.id)
                    groups = [g.id for g in user_groups]
                    # Load attributes from the user record.
                    if user.attributes:
                        attributes = dict(user.attributes)
            except Exception:
                pass  # DB not ready / user not found — proceed with empty groups
        return Principal(
            id=user_id,
            principal_type="USER",
            display_name=display,
            attributes=attributes,
            roles=roles,
            home_organization=home_org,
            groups=groups,
            is_anonymous=False,
        )

    async def _resolve_jwt(self, request: Request) -> Principal:
        """Verify the Bearer JWT (Better Auth) via fastapi-betterauth → Principal.

        Extracts the ``Authorization: Bearer <jwt>`` header, verifies the
        signature + expiry + iss/aud via ``fastapi-betterauth`` (PyJWKClient
        with built-in JWKS caching + key rotation), and maps the claims
        (sub, email, roles, groups, markings, attributes) to a Gaia
        Principal. Returns anonymous on missing/invalid token (fail-closed).
        """
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return Principal.anonymous_principal()
        token = auth_header[len("Bearer ") :].strip()
        if not token:
            return Principal.anonymous_principal()
        try:
            claims = self._verify_token(token)
        except Exception as exc:  # noqa: BLE001 — any verification failure → anonymous
            _log.warning("JWT verification failed: %s", exc)
            return Principal.anonymous_principal()
        if claims is None:
            return Principal.anonymous_principal()
        principal = self._claims_to_principal(claims)
        # Enrich the principal with groups + attributes from the Gaia DB.
        # Better Auth's JWT only carries identity (sub/email/roles) — the
        # group memberships and row-level attributes live in Gaia's users
        # table (linked by subject = JWT sub). Without this step,
        # resolve_effective_role_scopes finds no role assignments (groups
        # empty) and every non-PLATFORM_ADMIN user is denied everything.
        if self._metadata is not None and not principal.is_anonymous:
            try:
                user = await self._metadata.get_user_by_subject(principal.id)
                if user is not None:
                    user_groups = await self._metadata.list_user_groups(user.id)
                    group_ids = [g.id for g in user_groups]
                    _log.debug("Loaded Gaia user %s: %d groups", principal.id, len(group_ids))
                    # Principal is frozen (pydantic v2 model_config=frozen) —
                    # use model_copy(update=...) to return an enriched copy.
                    updates: dict[str, Any] = {"groups": group_ids}
                    if user.attributes:
                        updates["attributes"] = dict(user.attributes)
                    if user.home_organization:
                        updates["home_organization"] = user.home_organization
                    principal = principal.model_copy(update=updates)
                else:
                    _log.warning("No Gaia user found for subject=%s (create via POST /identity/users)", principal.id)
            except Exception as exc:  # noqa: BLE001
                _log.warning("Failed to load Gaia user profile for sub=%s: %s", principal.id, exc)
        return principal

    def _get_verifier(self) -> Any:
        """Lazily create the fastapi-betterauth verifier (cached singleton).

        The verifier (``BetterAuth`` instance) wraps a ``PyJWKClient`` with
        built-in JWKS caching (default 300s lifespan) + key rotation support.
        ``auto_error=False`` so missing/invalid tokens return None instead
        of raising (we handle that ourselves → anonymous principal).
        """
        if self._verifier is not None:
            return self._verifier
        from fastapi_betterauth import BetterAuth

        self._verifier = BetterAuth(
            base_url=self._better_auth_url,
            audience=self._jwt_audience or None,
            issuer=self._jwt_issuer or None,
            # Don't raise on missing token — we return anonymous ourselves.
            # Invalid tokens still raise TokenValidationError (caught above).
            auto_error=False,
        )
        return self._verifier

    def _verify_token(self, token: str) -> dict[str, Any] | None:
        """Verify a Better Auth JWT via fastapi-betterauth and return its claims.

        ``BetterAuth.fetch_token(token)`` verifies the signature (EdDSA by
        default), expiry, and iss/aud (when configured), returning the
        decoded claims as a ``User`` TypedDict. Raises
        ``TokenValidationError`` on any failure.
        """
        verifier = self._get_verifier()
        # fetch_token is sync (PyJWKClient uses sync urllib under the hood).
        # It's fast (JWKS cached) and runs in the async event loop thread —
        # acceptable for the expected request volume. If this becomes a
        # bottleneck, wrap in asyncio.to_thread.
        user = verifier.fetch_token(token)
        # User is a TypedDict — convert to plain dict for claims mapping.
        return dict(user) if user else None

    def _claims_to_principal(self, claims: dict[str, Any]) -> Principal:
        """Map a verified JWT's claims to a Gaia Principal.

        Better Auth's JWT carries: ``sub`` (user id), ``email``, and
        custom claims populated by the ``definePayload`` callback
        (``roles``, ``groups``, ``markings``, ``attributes``,
        ``home_organization``). These are the five-layer check inputs.
        """
        sub = str(claims.get(_CLAIM_SUB, ""))
        if not sub:
            return Principal.anonymous_principal()
        email = str(claims.get(_CLAIM_EMAIL, sub))
        roles_raw = claims.get(_CLAIM_ROLES, [])
        roles = [str(r) for r in roles_raw] if isinstance(roles_raw, list) else []
        # Map Better Auth roles to Gaia RBAC roles:
        #   Better Auth "admin" → Gaia PLATFORM_ADMIN (full platform access)
        #   Better Auth "user" → no Gaia role (must be granted VIEWER/EDITOR/etc
        #     via role-assignment API, just like a real enterprise user)
        if "admin" in roles and "PLATFORM_ADMIN" not in roles:
            roles.append("PLATFORM_ADMIN")
        groups_raw = claims.get(_CLAIM_GROUPS, [])
        groups = [str(g) for g in groups_raw] if isinstance(groups_raw, list) else []
        markings_raw = claims.get(_CLAIM_MARKINGS, [])
        markings = [str(m) for m in markings_raw] if isinstance(markings_raw, list) else []
        attributes_raw = claims.get(_CLAIM_ATTRIBUTES, {})
        attributes = dict(attributes_raw) if isinstance(attributes_raw, dict) else {}
        home_org = claims.get(_CLAIM_HOME_ORG)
        return Principal(
            id=sub,
            principal_type="USER",
            display_name=email,
            attributes=attributes,
            groups=groups,
            roles=roles,
            markings=markings,
            home_organization=str(home_org) if home_org else None,
            is_anonymous=False,
        )


def build_principal_service(metadata: Any | None = None) -> PrincipalService:
    """Factory: build a PrincipalService from settings.

    Dev mode when ``settings.authz_dev_mode`` is True OR no Better Auth URL
    is configured. Production (JWT/JWKS) mode when ``authz_dev_mode=False``
    and a Better Auth URL is set.
    """
    # Production mode requires a Better Auth URL (for JWKS discovery).
    prod_ready = not settings.authz_dev_mode and bool(settings.better_auth_url)
    if not prod_ready:
        return PrincipalService(dev_mode=True, metadata=metadata)
    # Issuer/audience default to Better Auth's baseURL when not overridden.
    issuer = settings.better_auth_jwt_issuer or settings.better_auth_url
    audience = settings.better_auth_jwt_audience or settings.better_auth_url
    return PrincipalService(
        dev_mode=False,
        better_auth_url=settings.better_auth_url,
        jwt_issuer=issuer,
        jwt_audience=audience,
        metadata=metadata,
    )
