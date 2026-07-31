import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_and_log_operator
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.schemas.approval import ApprovalActionRequest
from app.services.approval_service import ApprovalService
from app.policies.tenant_policy import require_same_tenant_from_version
from app.services.exceptions import (
    ApprovalPolicyDeniedError,
    ApprovalStateInvalidError,
    BlockingRiskApprovalError,
    HubItemVersionNotFoundError,
    VersionNotScannedError,
)

router = APIRouter(tags=["approvals"])


def get_service(db: Session = Depends(get_db)) -> ApprovalService:
    return ApprovalService(db)


@router.post("/hub/versions/{version_id}/approve")
def approve_version(
    version_id: uuid.UUID,
    body: ApprovalActionRequest,
    svc: ApprovalService = Depends(get_service),
    _perm=require_permission(Permission.review__approve),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=False),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "approve", version_id=str(version_id),
    )
    try:
        svc.approve_version(version_id, effective, body.comment, ctx=ctx)
        return {"detail": "ok"}
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except ApprovalPolicyDeniedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except (ApprovalStateInvalidError, BlockingRiskApprovalError, VersionNotScannedError) as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/hub/versions/{version_id}/reject")
def reject_version(
    version_id: uuid.UUID,
    body: ApprovalActionRequest,
    svc: ApprovalService = Depends(get_service),
    _perm=require_permission(Permission.review__reject),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=False),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "reject", version_id=str(version_id),
    )
    try:
        svc.reject_version(version_id, effective, body.comment, ctx=ctx)
        return {"detail": "ok"}
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except ApprovalPolicyDeniedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except ApprovalStateInvalidError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/hub/versions/{version_id}/request-change")
def request_change(
    version_id: uuid.UUID,
    body: ApprovalActionRequest,
    svc: ApprovalService = Depends(get_service),
    _perm=require_permission(Permission.review__request_change),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=False),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "request_change", version_id=str(version_id),
    )
    try:
        svc.request_change(version_id, effective, body.comment, ctx=ctx)
        return {"detail": "ok"}
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except ApprovalPolicyDeniedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc)})
    except ApprovalStateInvalidError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
