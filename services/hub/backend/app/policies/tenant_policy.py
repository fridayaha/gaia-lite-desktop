from __future__ import annotations

import os
import uuid

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.status import HTTP_403_FORBIDDEN, HTTP_404_NOT_FOUND

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context
from app.core.tenancy import (
    DEFAULT_ORGANIZATION_ID,
    DEFAULT_WORKSPACE_ID,
    VALID_VISIBILITY_SCOPES,
    normalize_visibility_scope,
    resolve_tenant_ids,
)
from app.db.session import get_db
from app.models.hub_item import HubItem
from app.models.hub_item_relation import HubItemRelation
from app.models.hub_item_version import HubItemVersion


def _get_legacy_visible() -> bool:
    val = os.environ.get("HUB_TENANT_LEGACY_VISIBLE", "false")
    return val.lower() in ("1", "true", "yes", "on")


def is_platform_admin(ctx: AuthContext) -> bool:
    return "platform_admin" in ctx.roles


def is_dev_mode(ctx: AuthContext) -> bool:
    return ctx.auth_mode == "dev"


def is_missing_tenant(obj: object) -> bool:
    org = getattr(obj, "organization_id", None)
    ws = getattr(obj, "workspace_id", None)
    return not org or not ws or str(org).strip() == "" or str(ws).strip() == ""


def is_legacy_tenant(obj: object) -> bool:
    if is_missing_tenant(obj):
        return True
    org = str(getattr(obj, "organization_id", "")).strip()
    ws = str(getattr(obj, "workspace_id", "")).strip()
    return org == DEFAULT_ORGANIZATION_ID and ws == DEFAULT_WORKSPACE_ID


def _normalize_tenant_id(value: str | None) -> str:
    if value is None or str(value).strip() == "":
        return DEFAULT_ORGANIZATION_ID
    return str(value).strip()


def is_same_tenant(ctx: AuthContext, obj: object) -> bool:
    ctx_org, ctx_ws = resolve_tenant_ids(ctx.organization_id, ctx.workspace_id)

    obj_org = _normalize_tenant_id(getattr(obj, "organization_id", None))
    obj_ws = _normalize_tenant_id(getattr(obj, "workspace_id", None))

    return obj_org == ctx_org and obj_ws == ctx_ws


def can_access_tenant(
    ctx: AuthContext,
    obj: object,
    action: str = "read",
) -> bool:
    if is_platform_admin(ctx):
        return True
    if is_dev_mode(ctx):
        return True

    if is_same_tenant(ctx, obj):
        return True

    if is_legacy_tenant(obj):
        ctx_org, ctx_ws = resolve_tenant_ids(ctx.organization_id, ctx.workspace_id)
        if ctx_ws == DEFAULT_WORKSPACE_ID and ctx_org == DEFAULT_ORGANIZATION_ID:
            return True
        if _get_legacy_visible():
            return True
    return False


def can_runtime_access_item(
    ctx: AuthContext,
    item: object,
    action: str = "discover",
) -> bool:
    """Runtime 侧 tenant + visibility_scope 访问判断。

    不检查 runtime_consumer role/scope（由 runtime_auth 负责）。
    不检查 lifecycle/risk/discoverable（由 RuntimeDiscoverService 负责）。
    不调用 CapabilityAccessPolicy（由调用方负责）。

    visibility_scope 语义：
    - private:     仅 owner (created_by == actor_id) / platform_admin / dev 可见
    - workspace:   organization_id + workspace_id 同时匹配
    - organization: organization_id 匹配即可
    - public:      所有 runtime_consumer 可见
    - null/unknown: 按 workspace 处理
    """
    if is_platform_admin(ctx):
        return True
    if is_dev_mode(ctx):
        return True

    vis = getattr(item, "visibility_scope", None)
    if not vis or str(vis).strip() == "":
        vis = "workspace"

    creator = getattr(item, "created_by", None)

    if vis == "private":
        if ctx.actor_id and creator and ctx.actor_id == creator:
            return True
        return False

    ctx_org, ctx_ws = resolve_tenant_ids(ctx.organization_id, ctx.workspace_id)
    item_org = _normalize_tenant_id(getattr(item, "organization_id", None))
    item_ws = _normalize_tenant_id(getattr(item, "workspace_id", None))

    if vis == "public":
        return True

    if vis == "organization":
        return item_org == ctx_org

    if vis == "workspace":
        return _workspace_or_legacy_check(ctx, item)

    # unknown visibility_scope: treat as workspace with legacy fallback
    if vis not in VALID_VISIBILITY_SCOPES:
        return _workspace_or_legacy_check(ctx, item)

    return False


def _workspace_or_legacy_check(ctx: AuthContext, item: object) -> bool:
    """Workspace visibility check with legacy tenant fallback."""
    ctx_org, ctx_ws = resolve_tenant_ids(ctx.organization_id, ctx.workspace_id)
    item_org = _normalize_tenant_id(getattr(item, "organization_id", None))
    item_ws = _normalize_tenant_id(getattr(item, "workspace_id", None))

    if item_org == ctx_org and item_ws == ctx_ws:
        return True

    if is_legacy_tenant(item):
        if ctx_ws == DEFAULT_WORKSPACE_ID and ctx_org == DEFAULT_ORGANIZATION_ID:
            return True
        if _get_legacy_visible():
            return True

    return False


def require_same_tenant_from_item(
    item_param: str = "item_id",
    *,
    not_found_on_deny: bool = True,
):
    def checker(
        request: Request,
        db: Session = Depends(get_db),
        ctx: AuthContext = Depends(get_auth_context),
    ):
        if is_platform_admin(ctx) or is_dev_mode(ctx):
            return
        item_id_str = request.path_params.get(item_param)
        if not item_id_str:
            return
        item = db.get(HubItem, uuid.UUID(item_id_str))
        if item is None:
            return
        if not can_access_tenant(ctx, item):
            status_code = HTTP_404_NOT_FOUND if not_found_on_deny else HTTP_403_FORBIDDEN
            raise HTTPException(status_code=status_code, detail="not found")

    return Depends(checker)


def apply_tenant_filter_to_items(query, ctx: AuthContext):
    if is_platform_admin(ctx) or is_dev_mode(ctx):
        return query
    org_id = ctx.organization_id or DEFAULT_ORGANIZATION_ID
    ws_id = ctx.workspace_id or DEFAULT_WORKSPACE_ID
    return query.filter(
        HubItem.organization_id == org_id,
        HubItem.workspace_id == ws_id,
    )


def require_same_tenant_from_version(
    version_param: str = "version_id",
    *,
    not_found_on_deny: bool = False,
):
    def checker(
        request: Request,
        db: Session = Depends(get_db),
        ctx: AuthContext = Depends(get_auth_context),
    ):
        if is_platform_admin(ctx) or is_dev_mode(ctx):
            return
        version_id_str = request.path_params.get(version_param)
        if not version_id_str:
            return
        version = db.get(HubItemVersion, uuid.UUID(version_id_str))
        if version is None:
            return
        if not can_access_tenant(ctx, version):
            status_code = HTTP_404_NOT_FOUND if not_found_on_deny else HTTP_403_FORBIDDEN
            raise HTTPException(status_code=status_code, detail="not found")

    return Depends(checker)


def check_relation_source_tenant(
    ctx: AuthContext,
    db: Session,
    source_item_id: uuid.UUID,
    *,
    not_found_on_deny: bool = False,
) -> None:
    if is_platform_admin(ctx) or is_dev_mode(ctx):
        return
    item = db.get(HubItem, source_item_id)
    if item is None:
        raise HTTPException(status_code=HTTP_404_NOT_FOUND, detail="source item not found")
    if not can_access_tenant(ctx, item):
        status_code = HTTP_404_NOT_FOUND if not_found_on_deny else HTTP_403_FORBIDDEN
        raise HTTPException(status_code=status_code, detail="not found")


def check_relation_tenant_from_relation(
    ctx: AuthContext,
    db: Session,
    relation_id: uuid.UUID,
    *,
    not_found_on_deny: bool = False,
) -> None:
    if is_platform_admin(ctx) or is_dev_mode(ctx):
        return
    relation = db.get(HubItemRelation, relation_id)
    if relation is None:
        return
    check_relation_source_tenant(
        ctx, db, relation.source_item_id,
        not_found_on_deny=not_found_on_deny,
    )
