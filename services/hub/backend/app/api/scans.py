import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_and_log_operator
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.policies.ownership_policy import require_asset_ownership_from_version
from app.policies.tenant_policy import require_same_tenant_from_version
from app.schemas.scan import ScanReportRead, ScanRequest
from app.services.exceptions import HubItemVersionNotFoundError
from app.services.scan_service import ScanService

router = APIRouter(tags=["scans"])


def get_service(db: Session = Depends(get_db)) -> ScanService:
    return ScanService(db)


@router.post(
    "/hub/versions/{version_id}/scan",
    response_model=ScanReportRead,
)
def scan_version(
    version_id: uuid.UUID,
    body: ScanRequest = ScanRequest(),
    svc: ScanService = Depends(get_service),
    _perm=require_permission(Permission.scan__run),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=False),
    _own=require_asset_ownership_from_version("version_id"),
    ctx: AuthContext = Depends(get_auth_context),
):
    effective = resolve_and_log_operator(
        ctx, body.operator, "scan", version_id=str(version_id),
    )
    try:
        return svc.scan_version(version_id, effective)
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@router.get(
    "/hub/versions/{version_id}/scan-report",
    response_model=ScanReportRead,
)
def get_scan_report(
    version_id: uuid.UUID,
    svc: ScanService = Depends(get_service),
    _perm=require_permission(Permission.scan__read),
    _tenant=require_same_tenant_from_version("version_id", not_found_on_deny=True),
):
    try:
        report = svc.get_latest_report(version_id)
        if report is None:
            return JSONResponse(
                status_code=404,
                content={"detail": "No scan report found for this version"},
            )
        return report
    except HubItemVersionNotFoundError as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
