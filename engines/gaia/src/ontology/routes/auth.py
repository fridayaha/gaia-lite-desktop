"""Auth routes — principal introspection (ADR-016 Phase 0).

Phase 0 scope: ``GET /auth/me`` returns the resolved Principal for the
current request, so the frontend (and curl smoke tests) can verify the
AuthMiddleware is wiring the Principal correctly.

``GET /auth/deployment-info`` returns the multi-tenant signal so the frontend
can apply progressive disclosure (design §8.1): single-tenant deployments
hide the three-tier container management (Organization/Space/Project).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from ontology.core.schemas.permission import Principal
from ontology.routes._deps import get_authz_service
from ontology.services.authorization_service import AuthorizationService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def get_current_principal(request: Request) -> Principal:
    """Return the Principal resolved by the AuthMiddleware.

    In dev mode (Phase 0) the Principal comes from X-User-Id / X-User-Roles
    / X-User-Attributes headers. In production (Phase 5+) it comes from a
    Better Auth-issued JWT verified via Authlib. Either way, this endpoint
    reflects exactly what downstream services see on ``request.state.principal``.
    """
    principal: Principal = request.state.principal
    return principal


@router.get("/deployment-info")
async def get_deployment_info(
    authz: AuthorizationService = Depends(get_authz_service),
) -> dict[str, Any]:
    """Return deployment metadata for frontend progressive disclosure.

    - ``is_multi_tenant``: True when more than one Organization exists.
      Single-tenant deployments hide the three-tier container management
      (Organization/Space/Project) from the Settings panel (design §8.1).
    """
    org_count = await authz._metadata.count_organizations()  # noqa: SLF001
    return {"is_multi_tenant": org_count > 1}
