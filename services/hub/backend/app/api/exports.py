import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.policies.ownership_policy import require_asset_ownership
from app.policies.tenant_policy import require_same_tenant_from_item
from app.services.export_service import ExportService

router = APIRouter(prefix="/hub/exports", tags=["exports"])


def get_service(db: Session = Depends(get_db)) -> ExportService:
    return ExportService(db)


@router.get(
    "/items/{item_id}/versions/{version_id}/package",
)
def download_version_package(
    item_id: uuid.UUID,
    version_id: uuid.UUID,
    svc: ExportService = Depends(get_service),
    _perm=require_permission(Permission.export__download),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
):
    result = svc.build_version_package(item_id, version_id)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "item or version not found"},
        )
    buf, filename = result
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/items/{item_id}")
def download_item_export(
    item_id: uuid.UUID,
    svc: ExportService = Depends(get_service),
    _perm=require_permission(Permission.export__download),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
):
    result = svc.build_item_export(item_id)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"detail": "item not found"},
        )
    buf, filename = result
    return Response(
        content=buf.read(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
