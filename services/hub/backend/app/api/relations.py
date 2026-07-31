import uuid

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.rbac import Permission, require_permission
from app.db.session import get_db
from app.policies.ownership_policy import (
    check_relation_delete_ownership,
    check_relation_source_ownership,
)
from app.policies.tenant_policy import (
    check_relation_source_tenant,
    check_relation_tenant_from_relation,
)
from app.schemas.hub_item_relation import (
    RelationCreate,
    RelationRead,
)
from app.services.exceptions import (
    DuplicateRelationError,
    HubItemNotFoundError,
    InvalidRelationTypeCombinationError,
    RelationNotFoundError,
    SelfRelationError,
)
from app.services.relation_service import RelationService

router = APIRouter(prefix="/hub/relations", tags=["relations"])


def get_service(db: Session = Depends(get_db)) -> RelationService:
    return RelationService(db)


def _item_not_found(item_id: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=404,
        content={"detail": f"HubItem not found: {item_id}"},
    )


def _relation_not_found(relation_id: str):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=404,
        content={"detail": f"HubItemRelation not found: {relation_id}"},
    )


@router.post("", response_model=RelationRead, status_code=status.HTTP_201_CREATED)
def create_relation(
    data: RelationCreate,
    svc: RelationService = Depends(get_service),
    _perm=require_permission(Permission.relation__create),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> RelationRead:
    check_relation_source_ownership(ctx, db, data.source_item_id)
    check_relation_source_tenant(ctx, db, data.source_item_id)
    try:
        return svc.create(data)
    except HubItemNotFoundError as e:
        return _item_not_found(e.item_id)
    except SelfRelationError as e:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(e)})
    except InvalidRelationTypeCombinationError as e:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=400, content={"detail": str(e)})
    except DuplicateRelationError as e:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(e)})


@router.get("/{relation_id}", response_model=RelationRead)
def get_relation(
    relation_id: uuid.UUID,
    svc: RelationService = Depends(get_service),
    _perm=require_permission(Permission.asset__read),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
) -> RelationRead:
    check_relation_tenant_from_relation(ctx, db, relation_id, not_found_on_deny=True)
    try:
        return svc.get_by_id(relation_id)
    except RelationNotFoundError:
        return _relation_not_found(str(relation_id))


@router.delete("/{relation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_relation(
    relation_id: uuid.UUID,
    svc: RelationService = Depends(get_service),
    _perm=require_permission(Permission.relation__delete),
    db: Session = Depends(get_db),
    ctx: AuthContext = Depends(get_auth_context),
):
    check_relation_delete_ownership(ctx, db, relation_id)
    check_relation_tenant_from_relation(ctx, db, relation_id)
    try:
        svc.delete(relation_id)
    except RelationNotFoundError:
        return _relation_not_found(str(relation_id))
