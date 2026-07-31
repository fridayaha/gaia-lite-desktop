from fastapi import APIRouter, Depends, File, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_effective_created_by
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.manifests.errors import ManifestValidationError
from app.schemas.imports import ImportResponse
from app.services.exceptions import (
    DuplicateVersionError,
    InvalidManifestError,
    UnsupportedFormatError,
    ZipSlipError,
)
from app.services.import_service import ImportService

router = APIRouter(tags=["imports"])


@router.post(
    "/hub/imports/package",
    response_model=ImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_package(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _perm=require_permission(Permission.asset__import),
    ctx: AuthContext = Depends(get_auth_context),
):
    svc = ImportService(db)
    created_by = resolve_effective_created_by(ctx, None)
    try:
        return svc.import_package(
            file, created_by=created_by,
            organization_id=ctx.organization_id,
            workspace_id=ctx.workspace_id,
        )
    except (InvalidManifestError, UnsupportedFormatError, ZipSlipError) as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
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
    except DuplicateVersionError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
