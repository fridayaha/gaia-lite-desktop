from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_effective_created_by
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.schemas.openapi_import import OpenAPIImportResponse
from app.services.openapi_import_service import OpenAPIImportService

router = APIRouter(tags=["imports"])


@router.post(
    "/hub/imports/openapi",
    response_model=OpenAPIImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_openapi(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _perm=require_permission(Permission.asset__import),
    ctx: AuthContext = Depends(get_auth_context),
):
    svc = OpenAPIImportService(db)
    created_by = resolve_effective_created_by(ctx, None)
    try:
        content = file.file.read()
        return svc.import_from_spec(content, file.filename or "spec.yaml",
                                    created_by=created_by,
                                    organization_id=ctx.organization_id,
                                    workspace_id=ctx.workspace_id)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
