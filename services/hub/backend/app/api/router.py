from fastapi import APIRouter

from app.api.health import router as health_router
from app.api.hub_items import router as hub_items_router
from app.api.versions import router as versions_router
from app.api.lifecycle import router as lifecycle_router
from app.api.approvals import router as approvals_router
from app.api.scans import router as scans_router
from app.api.presets import router as presets_router
from app.api.imports import router as imports_router
from app.api.openapi_imports import router as openapi_imports_router
from app.api.relations import router as relations_router
from app.api.runtime import router as runtime_router
from app.api.exports import router as exports_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(hub_items_router)
api_router.include_router(versions_router)
api_router.include_router(lifecycle_router)
api_router.include_router(approvals_router)
api_router.include_router(scans_router)
api_router.include_router(presets_router)
api_router.include_router(imports_router)
api_router.include_router(openapi_imports_router)
api_router.include_router(relations_router)
api_router.include_router(runtime_router)
api_router.include_router(exports_router)
