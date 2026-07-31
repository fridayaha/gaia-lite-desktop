"""V3 智能体定义 API — /api/manager/agent-definitions

定义层：元数据 + 草稿配置 + 发布版本快照。技能管理属于定义层（后续迁入）。
Dify 应用对接配置已下沉到实例层（AgentInstance.dify_config），定义层只声明 engine_type=DIFY。
"""

from uuid import UUID

from app.core.auth import get_current_user
from app.core.group_scope import assert_group_writable, get_current_group_ids
from app.models import User
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionListResponse,
    AgentDefinitionResponse,
    AgentDefinitionUpdate,
    AgentVersionResponse,
    PublishVersionRequest,
)
from app.services import definition_service
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/agent-definitions", tags=["agent-definitions"])


def _version_no_of(d) -> str | None:
    """从已载入的 versions 集合中取当前版本号（避免懒加载）。"""
    if not d.current_version_id:
        return None
    for v in d.versions or []:
        if v.id == d.current_version_id:
            return v.version_no
    return None


def _has_unpublished_changes(d) -> bool:
    """草稿配置是否相对于最近发布快照有改动（供前端显示「未发布修改」徽标）。

    - 从未发布（无 current_version）→ True
    - 已发布但草稿 4 项配置任一与快照不一致 → True
    复用已 selectinload 的 versions 集合定位快照，避免 async 懒加载。
    """
    if not d.current_version_id:
        return True
    v = next((x for x in (d.versions or []) if x.id == d.current_version_id), None)
    if v is None:
        return True
    return (
        (d.persona_config or {}) != (v.persona_config or {})
        or (d.model_config or {}) != (v.model_config or {})
        or (d.skill_config or {}) != (v.skill_config or {})
        or (d.memory_config or {}) != (v.memory_config or {})
    )


def _to_response(d, instance_count: int = 0) -> AgentDefinitionResponse:
    return AgentDefinitionResponse(
        id=d.id,
        name=d.name,
        description=d.description,
        avatar_color=d.avatar_color,
        engine_type=d.engine_type,
        status=d.status,
        group_id=d.group_id,
        group_name=d.group.name if d.group else "",
        current_version_id=d.current_version_id,
        current_version_no=_version_no_of(d),
        marketplace_status=d.marketplace_status,
        persona_config=d.persona_config or {},
        model_settings=d.model_config or {},
        skill_config=d.skill_config or {},
        memory_config=d.memory_config or {},
        created_by=d.created_by,
        creator_name=d.creator.username if d.creator else "",
        instance_count=instance_count,
        created_at=d.created_at,
        updated_at=d.updated_at,
        published_at=d.published_at,
        has_unpublished_changes=_has_unpublished_changes(d),
    )


async def _require_definition(db: AsyncSession, definition_id: UUID, group_ids: list[UUID] | None):
    """取定义并校验组隔离：组用户只能访问所属组定义（跨组返回 404）。"""
    d = await definition_service.get_definition(db, definition_id)
    if not d:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定义不存在")
    if group_ids is not None and d.group_id not in group_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定义不存在")
    return d


@router.get("", response_model=AgentDefinitionListResponse)
async def list_definitions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = None,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    items, total = await definition_service.list_definitions(db, page, page_size, search, group_ids=group_ids)
    counts = await definition_service.instance_counts_for(db, [d.id for d in items])
    return AgentDefinitionListResponse(
        items=[_to_response(d, counts.get(d.id, 0)) for d in items],
        total=total, page=page, page_size=page_size,
    )


@router.post("", response_model=AgentDefinitionResponse, status_code=status.HTTP_201_CREATED)
async def create_definition(
    data: AgentDefinitionCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    assert_group_writable(data.group_id, group_ids)
    try:
        d = await definition_service.create_definition(db, data, user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该用户组下已存在同名智能体定义",
        )
    return _to_response(d)


@router.get("/{definition_id}", response_model=AgentDefinitionResponse)
async def get_definition(
    definition_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    d = await _require_definition(db, definition_id, group_ids)
    counts = await definition_service.instance_counts_for(db, [d.id])
    return _to_response(d, counts.get(d.id, 0))


@router.put("/{definition_id}", response_model=AgentDefinitionResponse)
async def update_definition(
    definition_id: UUID,
    data: AgentDefinitionUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    d = await _require_definition(db, definition_id, group_ids)
    updated = await definition_service.update_definition(db, definition_id, data, actor_id=user.id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定义不存在")
    counts = await definition_service.instance_counts_for(db, [d.id])
    return _to_response(updated, counts.get(d.id, 0))


@router.delete("/{definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_definition(
    definition_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_definition(db, definition_id, group_ids)
    try:
        if not await definition_service.delete_definition(db, definition_id, actor_id=user.id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="定义不存在")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


# ── 版本管理 ────────────────────────────────────


@router.get("/{definition_id}/versions", response_model=list[AgentVersionResponse])
async def list_versions(
    definition_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_definition(db, definition_id, group_ids)
    return await definition_service.list_versions(db, definition_id)


@router.post("/{definition_id}/publish", response_model=AgentVersionResponse, status_code=status.HTTP_201_CREATED)
async def publish_definition(
    definition_id: UUID,
    req: PublishVersionRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """发布定义：生成不可变版本快照，置为 current_version。"""
    await _require_definition(db, definition_id, group_ids)
    try:
        _, v = await definition_service.publish_definition(db, definition_id, req, user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return v
