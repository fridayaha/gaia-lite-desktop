from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.schemas.preset import PresetInitResponse
from app.services.preset_service import PresetService

router = APIRouter(tags=["presets"])


@router.post("/hub/presets/init", response_model=PresetInitResponse)
def init_presets(
    db: Session = Depends(get_db),
    _perm=require_permission(Permission.admin__configure),
):
    svc = PresetService(db)
    return svc.init_presets()
