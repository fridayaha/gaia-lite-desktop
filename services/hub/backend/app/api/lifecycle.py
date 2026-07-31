import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_and_log_operator
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.policies.ownership_policy import (
    require_asset_ownership,
    require_asset_ownership_from_version,
)
from app.policies.tenant_policy import (
    require_same_tenant_from_item,
    require_same_tenant_from_version,
)
from app.schemas.lifecycle import LifecycleActionRequest, RollbackRequest
from app.services.exceptions import (
    ApprovalPolicyDeniedError,
    BlockingRiskSubmitError,
    HubItemNotFoundError,
    HubItemVersionNotFoundError,
    InvalidStateTransitionError,
    RollbackTargetInvalidError,
    VersionNotScannedError,
)
from app.services.lifecycle_service import LifecycleService

router = APIRouter(tags=["lifecycle"])


def get_service(db: Session = Depends(get_db)) -> LifecycleService:
    return LifecycleService(db)


@router.post("/hub/items/{item_id}/submit")
def submit_item(
    item_id: uuid.UUID,
    body: LifecycleActionRequest,
    svc: LifecycleService = Depends(get_service),
    _perm=require_permission(Permission.review__submit),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "submit_item", item_id=str(item_id),
    )
    try:
        svc.submit_item(item_id, effective, body.reason, ctx=ctx)
        return {"detail": "ok"}
    except HubItemNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except ApprovalPolicyDeniedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except InvalidStateTransitionError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post(
    "/hub/versions/{version_id}/submit-review",
    status_code=status.HTTP_200_OK,
)
def submit_version(
    version_id: uuid.UUID,
    body: LifecycleActionRequest,
    svc: LifecycleService = Depends(get_service),
    _perm=require_permission(Permission.review__submit),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=False),
    _own=require_asset_ownership_from_version("version_id"),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "submit_review", version_id=str(version_id),
    )
    try:
        svc.submit_version(version_id, effective, body.reason, ctx=ctx)
        return {"detail": "ok"}
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except ApprovalPolicyDeniedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except InvalidStateTransitionError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except BlockingRiskSubmitError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/hub/versions/{version_id}/publish")
def publish_version(
    version_id: uuid.UUID,
    body: LifecycleActionRequest,
    svc: LifecycleService = Depends(get_service),
    _perm=require_permission(Permission.lifecycle__publish),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=False),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "publish", version_id=str(version_id),
    )
    try:
        svc.publish_version(version_id, effective, body.reason, ctx=ctx)
        return {"detail": "ok"}
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except HubItemNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except ApprovalPolicyDeniedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except InvalidStateTransitionError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except (VersionNotScannedError, BlockingRiskSubmitError) as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/hub/items/{item_id}/disable")
def disable_item(
    item_id: uuid.UUID,
    body: LifecycleActionRequest,
    svc: LifecycleService = Depends(get_service),
    _perm=require_permission(Permission.lifecycle__disable),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "disable", item_id=str(item_id),
    )
    try:
        svc.disable_item(item_id, effective, body.reason)
        return {"detail": "ok"}
    except HubItemNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except InvalidStateTransitionError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/hub/items/{item_id}/archive")
def archive_item(
    item_id: uuid.UUID,
    body: LifecycleActionRequest,
    svc: LifecycleService = Depends(get_service),
    _perm=require_permission(Permission.lifecycle__archive),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "archive", item_id=str(item_id),
    )
    try:
        svc.archive_item(item_id, effective, body.reason)
        return {"detail": "ok"}
    except HubItemNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except InvalidStateTransitionError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/hub/items/{item_id}/rollback")
def rollback_item(
    item_id: uuid.UUID,
    body: RollbackRequest,
    svc: LifecycleService = Depends(get_service),
    _perm=require_permission(Permission.lifecycle__rollback),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "rollback", item_id=str(item_id),
    )
    try:
        svc.rollback_item(
            item_id, body.target_version_id, effective, body.reason
        )
        return {"detail": "ok"}
    except HubItemNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except (InvalidStateTransitionError, RollbackTargetInvalidError) as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
