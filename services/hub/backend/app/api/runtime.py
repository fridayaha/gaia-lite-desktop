import time
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_dependencies import get_runtime_auth_context
from app.core.enums import HubItemType
from app.core.event_log import log_event
from app.core.runtime_auth import require_runtime_permission
from app.db.session import get_db
from app.policies.capability_access import ScopedCapabilityAccessPolicy
from app.schemas.runtime import (
    RuntimeCapabilityResolve,
    RuntimeCapabilitySummary,
    FunctionCallingToolDefinition,
)
from app.services.exceptions import RequiredDependencyUnavailableError
from app.services.runtime_discover_service import (
    CapabilityNotAvailableError,
    RuntimeDiscoverService,
)

router = APIRouter(prefix="/runtime", tags=["runtime"])


def get_service(db: Session = Depends(get_db)) -> RuntimeDiscoverService:
    return RuntimeDiscoverService(db, policy=ScopedCapabilityAccessPolicy())


@router.get("/capabilities/discover")
def discover_capabilities(
    type: HubItemType | None = Query(default=None),
    keyword: str | None = Query(default=None),
    risk_level_max: Literal["low", "medium", "high"] = Query(default="high"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    agent_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    actor_type: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    scopes: str | None = Query(default=None),
    roles: str | None = Query(default=None),
    _perm: None = require_runtime_permission("capability:discover"),
    ctx: AuthContext = Depends(get_runtime_auth_context),
    svc: RuntimeDiscoverService = Depends(get_service),
):
    from app.schemas.runtime import RuntimeDiscoverFilters

    filters = RuntimeDiscoverFilters(
        type=type.value if type else None,
        keyword=keyword,
        risk_level_max=risk_level_max,
        limit=limit,
        offset=offset,
    )

    t0 = time.monotonic()
    items_with_versions, total = svc.discover(filters, ctx)
    duration_ms = round((time.monotonic() - t0) * 1000)
    summaries = [
        RuntimeCapabilitySummary(
            id=item.id,
            name=item.name,
            type=item.type.value,
            description=item.description,
            version=version.version,
            risk_level=item.risk_level.value,
        )
        for item, version in items_with_versions
    ]
    log_event(
        "runtime.discover.completed",
        result_count=len(summaries),
        result_total=total,
        duration_ms=duration_ms,
        status_code=200,
    )
    return {"items": summaries, "total": total}


@router.get(
    "/capabilities/{item_id}/resolve",
    response_model=RuntimeCapabilityResolve,
)
def resolve_capability(
    item_id: uuid.UUID,
    depth: int = Query(default=1, ge=1, le=3),
    agent_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    actor_type: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    scopes: str | None = Query(default=None),
    roles: str | None = Query(default=None),
    _perm: None = require_runtime_permission("capability:resolve"),
    ctx: AuthContext = Depends(get_runtime_auth_context),
    svc: RuntimeDiscoverService = Depends(get_service),
):
    t0 = time.monotonic()
    try:
        result = svc.resolve(item_id, ctx, depth=depth)
        duration_ms = round((time.monotonic() - t0) * 1000)
        log_event(
            "runtime.resolve.completed",
            item_id=str(result["id"]),
            item_type=result["type"],
            depth=depth,
            result_count=len(result.get("dependencies", [])),
            dependency_count=len(result.get("dependencies", [])),
            warning_count=len(result.get("dependency_warnings", [])),
            duration_ms=duration_ms,
            status_code=200,
        )
        return result
    except CapabilityNotAvailableError as e:
        return JSONResponse(
            status_code=404,
            content={"detail": str(e)},
        )
    except RequiredDependencyUnavailableError as e:
        return JSONResponse(
            status_code=409,
            content={"detail": str(e)},
        )
    except ValueError as e:
        return JSONResponse(
            status_code=422,
            content={"detail": str(e)},
        )


@router.get(
    "/capabilities/{item_id}/tool-definition",
    response_model=FunctionCallingToolDefinition,
)
def get_tool_definition(
    item_id: uuid.UUID,
    agent_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    actor_type: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    scopes: str | None = Query(default=None),
    roles: str | None = Query(default=None),
    _perm: None = require_runtime_permission(
        "capability:tool_definition",
        fallback_scopes=["capability:resolve"],
    ),
    ctx: AuthContext = Depends(get_runtime_auth_context),
    svc: RuntimeDiscoverService = Depends(get_service),
):
    t0 = time.monotonic()
    try:
        result = svc.build_tool_definition(item_id, ctx)
        duration_ms = round((time.monotonic() - t0) * 1000)
        log_event(
            "runtime.tool_definition.completed",
            item_id=str(item_id),
            item_type="tool",
            duration_ms=duration_ms,
            status_code=200,
        )
        return result
    except CapabilityNotAvailableError as e:
        return JSONResponse(
            status_code=404,
            content={"detail": str(e)},
        )
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"detail": str(e)},
        )


@router.get("/capabilities/{item_id}/manifest")
def download_runtime_manifest(
    item_id: uuid.UUID,
    agent_id: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    actor_type: str | None = Query(default=None),
    workspace_id: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    scopes: str | None = Query(default=None),
    roles: str | None = Query(default=None),
    _perm: None = require_runtime_permission(
        "capability:manifest",
        fallback_scopes=["capability:resolve"],
    ),
    ctx: AuthContext = Depends(get_runtime_auth_context),
    svc: RuntimeDiscoverService = Depends(get_service),
):
    try:
        data = svc.resolve(item_id, ctx, depth=1)
    except CapabilityNotAvailableError as e:
        return JSONResponse(
            status_code=404,
            content={"detail": str(e)},
        )
    except RequiredDependencyUnavailableError as e:
        return JSONResponse(
            status_code=409,
            content={"detail": str(e)},
        )

    data.pop("status", None)
    data["exported_at"] = datetime.now(timezone.utc).isoformat()
    return data
