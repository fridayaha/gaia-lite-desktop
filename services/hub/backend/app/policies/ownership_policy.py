import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.status import HTTP_403_FORBIDDEN

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.event_log import log_event
from app.db.session import get_db
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion


def _is_admin(ctx: AuthContext) -> bool:
    return "platform_admin" in ctx.roles


def _is_missing_owner(item: HubItem) -> bool:
    return not item.created_by or not item.created_by.strip() or item.created_by == "unknown"


def _is_creator(ctx: AuthContext, item: HubItem) -> bool:
    return item.created_by is not None and item.created_by == ctx.actor_id


def require_asset_ownership(path_param: str = "item_id"):
    def checker(
        request: Request,
        db: Session = Depends(get_db),
        ctx: AuthContext = Depends(get_auth_context),
    ):
        if _is_admin(ctx):
            return
        item_id_str = request.path_params.get(path_param)
        if not item_id_str:
            return
        item = db.get(HubItem, uuid.UUID(item_id_str))
        if item is None:
            return
        if _is_missing_owner(item):
            log_event(
                "ownership.missing_owner",
                item_id=str(item.id),
                result="allowed_legacy",
            )
            return
        if _is_creator(ctx, item):
            return
        log_event(
            "ownership.policy_denied",
            item_id=str(item.id),
            result="denied",
        )
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="ownership policy denied",
        )
    return Depends(checker)


def require_asset_ownership_from_version(version_param: str = "version_id"):
    def checker(
        request: Request,
        db: Session = Depends(get_db),
        ctx: AuthContext = Depends(get_auth_context),
    ):
        if _is_admin(ctx):
            return
        version_id_str = request.path_params.get(version_param)
        if not version_id_str:
            return
        version = db.get(HubItemVersion, uuid.UUID(version_id_str))
        if version is None:
            return
        item = db.get(HubItem, version.hub_item_id)
        if item is None:
            return
        if _is_missing_owner(item):
            log_event(
                "ownership.missing_owner",
                item_id=str(item.id),
                result="allowed_legacy",
            )
            return
        if _is_creator(ctx, item):
            return
        log_event(
            "ownership.policy_denied",
            item_id=str(item.id),
            result="denied",
        )
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="ownership policy denied",
        )
    return Depends(checker)


def check_relation_source_ownership(
    ctx: AuthContext,
    db: Session,
    source_item_id: uuid.UUID,
) -> None:
    if _is_admin(ctx):
        return
    item = db.get(HubItem, source_item_id)
    if item is None:
        return
    if _is_missing_owner(item):
        log_event(
            "ownership.missing_owner",
            item_id=str(item.id),
            result="allowed_legacy",
        )
        return
    if _is_creator(ctx, item):
        return
    log_event(
        "ownership.policy_denied",
        item_id=str(item.id),
        result="denied",
    )
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN,
        detail="ownership policy denied",
    )


def check_relation_delete_ownership(
    ctx: AuthContext,
    db: Session,
    relation_id: uuid.UUID,
) -> None:
    from app.models.hub_item_relation import HubItemRelation
    if _is_admin(ctx):
        return
    relation = db.get(HubItemRelation, relation_id)
    if relation is None:
        return
    item = db.get(HubItem, relation.source_item_id)
    if item is None:
        return
    if _is_missing_owner(item):
        log_event(
            "ownership.missing_owner",
            item_id=str(item.id),
            result="allowed_legacy",
        )
        return
    if _is_creator(ctx, item):
        return
    log_event(
        "ownership.policy_denied",
        item_id=str(item.id),
        result="denied",
    )
    raise HTTPException(
        status_code=HTTP_403_FORBIDDEN,
        detail="ownership policy denied",
    )
