"""Shared FastAPI dependencies for permission governance (ADR-016/017).

Centralizes the two dependencies every permission-aware route needs:

  - :func:`get_principal` — reads the Principal injected by AuthMiddleware
    onto ``request.state.principal``.
  - :func:`get_authz_service` — yields the request-scoped
    AuthorizationService (PDP) and closes its metadata session after.

Before this module, ``_principal`` was copy-pasted into ``authz.py``,
``marking.py``, and ``action/__init__.py``. Consolidating them here is part
of the "no scattering" principle: one definition, reused everywhere. Routes
that also need the ship-the-decision envelope import
:mod:`ontology.services.permission_envelope` and call
:func:`envelope` / :meth:`PermissionEnvelope.wrap_list`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request

from ontology.config.container import container
from ontology.core.schemas.permission import Principal
from ontology.services.authorization_service import AuthorizationService


def get_principal(request: Request) -> Principal:
    """The authenticated Principal for the current request.

    AuthMiddleware resolves the Principal (dev: X-User-Id header; prod: Better
    Auth JWT via Authlib) and stores it on ``request.state.principal``. This
    dependency just surfaces it for FastAPI's DI. Anonymous when unauthenticated
    (AuthorizationService Layer 1 denies non-public resources).
    """
    principal: Principal = request.state.principal
    return principal


async def get_authz_service() -> AsyncIterator[AuthorizationService]:
    """Yield a request-scoped AuthorizationService (PDP) and close after.

    The service is bound to a FRESH metadata session per request (see
    ``container.authorization_service``). We close the session after the
    request so the connection returns to the pool.
    """
    svc = container.authorization_service
    try:
        yield svc
    finally:
        await svc._metadata.close()  # noqa: SLF001 — session lifecycle
