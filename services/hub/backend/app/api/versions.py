import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_effective_created_by
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.manifests.errors import ManifestValidationError
from app.policies.ownership_policy import require_asset_ownership
from app.policies.tenant_policy import require_same_tenant_from_item
from app.schemas.hub_item_version import HubItemVersionCreate, HubItemVersionRead
from app.services.exceptions import (
    DuplicateVersionError,
    HubItemNotFoundError,
    HubItemVersionNotFoundError,
)
from app.services.version_service import VersionService

router = APIRouter(tags=["versions"])


def get_service(db: Session = Depends(get_db)) -> VersionService:
    return VersionService(db)


@router.post(
    "/hub/items/{item_id}/versions",
    response_model=HubItemVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    item_id: uuid.UUID,
    data: HubItemVersionCreate,
    svc: VersionService = Depends(get_service),
    _perm=require_permission(Permission.version__create),
    _own=require_asset_ownership("item_id"),
    ctx: AuthContext = Depends(get_auth_context),
) -> HubItemVersionRead:
    data.created_by = resolve_effective_created_by(ctx, data.created_by)
    try:
        return svc.create(item_id, data)
    except HubItemNotFoundError as exc:
        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )
    except DuplicateVersionError as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc)},
        )
    except ManifestValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "detail": str(exc),
                "errors": [
                    {"field": e.field, "message": e.message, "level": e.level}
                    for e in exc.errors
                ],
            },
        )


@router.get(
    "/hub/items/{item_id}/versions",
    response_model=list[HubItemVersionRead],
)
def list_versions(
    item_id: uuid.UUID,
    svc: VersionService = Depends(get_service),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=True),
    _perm=require_permission(Permission.asset__read),
) -> list[HubItemVersionRead]:
    try:
        return svc.list_by_item(item_id)
    except HubItemNotFoundError as exc:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )


@router.get(
    "/hub/items/{item_id}/versions/{version_id}",
    response_model=HubItemVersionRead,
)
def get_version(
    item_id: uuid.UUID,
    version_id: uuid.UUID,
    svc: VersionService = Depends(get_service),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=True),
    _perm=require_permission(Permission.asset__read),
) -> HubItemVersionRead:
    try:
        return svc.get_by_id(item_id, version_id)
    except HubItemNotFoundError as exc:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )
    except HubItemVersionNotFoundError as exc:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=404,
            content={"detail": str(exc)},
        )
