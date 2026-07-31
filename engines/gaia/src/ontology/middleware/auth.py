"""AuthMiddleware — authenticates every request + injects the Principal (ADR-016, Phase 0).

The first gate of every request. Two responsibilities (design §3.1):

1. **Authentication**: resolve the Principal (dev mode: X-User-Id header;
   Phase 5: Better Auth JWT via Authlib) and inject it onto
   ``request.state.principal`` for downstream services.

2. **PG RLS context injection** (Phase 3): set PG session variables
   (``SET LOCAL app.principal_organization`` etc.) so object_state's RLS
   policies can read the current principal. Phase 0 reserves the hook but
   does not enable RLS — RLS lands in Phase 3 together with the policy.

Downstream services read the Principal via ``request.state.principal`` (set
here) or a FastAPI ``Depends`` that wraps it (Phase 1).

Fail-closed: if PrincipalService cannot resolve a principal, an anonymous
Principal is injected. The AuthorizationService Layer 1 (Phase 1) denies
non-public resources for anonymous principals — the middleware never raises
a 401 itself; that's the AuthorizationService's call (a health check or
public endpoint should still succeed without a principal).
"""

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from ontology.services.principal_service import PrincipalService

if TYPE_CHECKING:
    from ontology.config.container import Container


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve + inject the Principal on every request.

    The PrincipalService is constructed lazily from the container (Phase 5
    will inject a JWT-verifying variant). For Phase 0 we default to dev-mode
    header parsing.
    """

    def __init__(self, app: ASGIApp, principal_service: PrincipalService | None = None) -> None:
        super().__init__(app)
        self._principal_service = principal_service

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Each request gets a fresh PrincipalService with a fresh DB session
        # (so JWT-mode group loading doesn't use a stale/closed session).
        # In dev mode (no DB needed) the singleton is fine.
        svc = self._principal_service
        created_svc = False
        if svc is None or (not svc._dev_mode and svc._metadata is None):  # noqa: SLF001
            svc = get_principal_service()
            created_svc = True
        principal = await svc.resolve_principal(request)
        request.state.principal = principal
        # Close the request-scoped DB session (if we created one).
        if created_svc and svc._metadata is not None:  # noqa: SLF001
            await svc._metadata.close()  # noqa: SLF001
        response = await call_next(request)
        return response


def get_principal_service(container: "Container | None" = None) -> PrincipalService:
    """Factory: build a PrincipalService from settings.

    Dev mode when ``settings.authz_dev_mode`` is True (or no Better Auth URL);
    JWT/JWKS production mode when Better Auth is deployed.
    """
    from ontology.config.container import container as default_container
    from ontology.services.principal_service import build_principal_service

    c = container or default_container
    return build_principal_service(metadata=c.metadata)
