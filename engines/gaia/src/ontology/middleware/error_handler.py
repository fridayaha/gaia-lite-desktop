"""Global error handling middleware.

Maps domain exceptions to HTTP responses with consistent format.
"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from ontology.core.exceptions import (
    CatalogNotRegisteredError,
    ConflictError,
    DataSourceUnreachableError,
    ForbiddenError,
    NotFoundError,
    OntologyError,
    TrinoUnavailableError,
    ValidationError,
)

logger = logging.getLogger(__name__)


async def ontology_error_handler(request: Request, exc: OntologyError) -> JSONResponse:
    """Global handler for all OntologyError exceptions.

    Maps domain exception types to appropriate HTTP status codes with a
    consistent JSON error response format. Preserves the stable ``code``
    field when the exception carries one (D3: align REST error format with
    the MCP/AG-UI tool-layer envelope ``{"error": {"code","message"}}``).
    """
    status_code: int
    detail: str = str(exc)

    if isinstance(exc, NotFoundError):
        status_code = 404
        detail = str(exc)
    elif isinstance(exc, ForbiddenError):
        status_code = 403
        detail = str(exc)
    elif isinstance(exc, ConflictError):
        status_code = 409
        detail = str(exc)
    elif isinstance(exc, ValidationError):
        status_code = 422
        detail = str(exc)
    elif isinstance(exc, DataSourceUnreachableError):
        # Trino is up but the federated catalog's backing data source is
        # unreachable (DNS/refused/timeout). 502 = bad gateway: an upstream
        # the server depends on is down. detail is user-facing & actionable.
        status_code = 502
        detail = str(exc)
    elif isinstance(exc, CatalogNotRegisteredError):
        # Trino is up and the source DB may be reachable, but the Gravitino
        # catalog registration is gone (stale bookkeeping after a Gravitino
        # rebuild). 502 = bad gateway: an upstream registration is missing.
        # Recoverable by re-registering — the caller (or the reconcile loop)
        # can rebuild the catalog without losing any source data.
        status_code = 502
        detail = str(exc)
    elif isinstance(exc, TrinoUnavailableError):
        # The query engine service itself is down. 503 = service unavailable.
        status_code = 503
        detail = str(exc)
    else:
        status_code = 500
        detail = "Internal server error"
        logger.exception("Unhandled OntologyError: %s", exc)

    # code: stable contract code from OntologyError.code (e.g.
    # INVALID_AGGREGATION), or a default per type. Aligns REST responses
    # with the tool-layer error envelope so all three entry points
    # (MCP / AG-UI / REST) surface the same code.
    code: str = getattr(exc, "code", None) or {
        NotFoundError: "OBJECT_NOT_FOUND",
        ForbiddenError: "PERMISSION_DENIED",
        ConflictError: "CONFLICT",
        ValidationError: "INVALID_PARAMETER",
        DataSourceUnreachableError: "DATASOURCE_UNREACHABLE",
        CatalogNotRegisteredError: "CATALOG_NOT_REGISTERED",
        TrinoUnavailableError: "TRINO_UNAVAILABLE",
    }.get(type(exc), "ONTOLOGY_ERROR")

    return JSONResponse(
        status_code=status_code,
        content={"detail": detail, "error_type": type(exc).__name__, "code": code},
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all handler for unhandled exceptions."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error_type": "InternalError"},
    )
