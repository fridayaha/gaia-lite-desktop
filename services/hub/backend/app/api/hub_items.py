import uuid

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.operator import resolve_effective_created_by
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.policies.ownership_policy import require_asset_ownership
from app.policies.tenant_policy import (
    apply_tenant_filter_to_items,
    is_platform_admin,
    is_dev_mode,
    require_same_tenant_from_item,
)
from app.schemas.hub_item import HubItemCreate, HubItemRead, HubItemUpdate
from app.schemas.hub_item_list import HubItemListFilters, HubItemListResponse
from app.schemas.hub_item_relation import ItemRelationsResponse
from app.services.exceptions import HubItemNotFoundError
from app.services.hub_item_service import HubItemService
from app.services.relation_service import RelationService

router = APIRouter(prefix="/hub/items", tags=["hub_items"])


def get_service(db: Session = Depends(get_db)) -> HubItemService:
    return HubItemService(db)


def get_relation_service(db: Session = Depends(get_db)) -> RelationService:
    return RelationService(db)


@router.post("", response_model=HubItemRead, status_code=status.HTTP_201_CREATED)
def create_item(
    data: HubItemCreate,
    svc: HubItemService = Depends(get_service),
    _perm=require_permission(Permission.asset__create),
    ctx: AuthContext = Depends(get_auth_context),
) -> HubItemRead:
    data.created_by = resolve_effective_created_by(ctx, data.created_by)
    return svc.create(
        data,
        organization_id=ctx.organization_id,
        workspace_id=ctx.workspace_id,
    )


@router.get("", response_model=HubItemListResponse)
def list_items(
    type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    risk_level: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    featured: bool | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    svc: HubItemService = Depends(get_service),
    ctx: AuthContext = Depends(get_auth_context),
    _perm=require_permission(Permission.asset__read),
) -> dict:
    filters = HubItemListFilters(
        type=type,
        status=status,
        risk_level=risk_level,
        source_type=source_type,
        keyword=keyword,
        featured=featured,
    )
    effective_ws = None
    effective_org = None
    if not (is_platform_admin(ctx) or is_dev_mode(ctx)):
        effective_org = ctx.organization_id
        effective_ws = ctx.workspace_id
    items, total = svc.list_with_total(
        filters, skip=skip, limit=limit,
        organization_id=effective_org,
        workspace_id=effective_ws,
    )
    return {"items": items, "total": total}


@router.get("/{item_id}", response_model=HubItemRead)
def get_item(
    item_id: uuid.UUID,
    svc: HubItemService = Depends(get_service),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=True),
    _perm=require_permission(Permission.asset__read),
) -> HubItemRead:
    try:
        return svc.get_by_id(item_id)
    except HubItemNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"HubItem not found: {item_id}"},
        )


@router.put("/{item_id}", response_model=HubItemRead)
def update_item(
    item_id: uuid.UUID,
    data: HubItemUpdate,
    svc: HubItemService = Depends(get_service),
    _perm=require_permission(Permission.asset__update),
    _tenant=require_same_tenant_from_item("item_id", not_found_on_deny=False),
    _own=require_asset_ownership("item_id"),
) -> HubItemRead:
    try:
        return svc.update(item_id, data)
    except HubItemNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"HubItem not found: {item_id}"},
        )


@router.get(
    "/{item_id}/relations",
    response_model=ItemRelationsResponse,
)
def list_item_relations(
    item_id: uuid.UUID,
    svc: RelationService = Depends(get_relation_service),
    _perm=require_permission(Permission.asset__read),
) -> ItemRelationsResponse:
    try:
        outgoing, incoming = svc.list_by_item(item_id)
        return ItemRelationsResponse(outgoing=outgoing, incoming=incoming)
    except HubItemNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": f"HubItem not found: {item_id}"},
        )
