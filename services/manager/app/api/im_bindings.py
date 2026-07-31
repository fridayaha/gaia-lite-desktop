"""IM 用户绑定管理 API"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from pkg.common.database import get_db
from app.models import User, user_group_members
from app.schemas import ImBindingCreate, ImBindingResponse, ImBindingListResponse
from app.services.im_binding_service import (
    list_bindings_by_user,
    create_binding,
    delete_binding,
)
from app.core.auth import get_current_user, is_platform_admin
from app.core.group_scope import get_current_group_ids

router = APIRouter(
    prefix="/api/manager/users/{user_id}/im-bindings",
    tags=["im-bindings"],
)


async def _check_user_scope(
    db: AsyncSession,
    user: User,
    target_user_id: UUID,
    group_ids: list[UUID] | None,
) -> None:
    """校验当前用户对目标 user_id 的 IM 绑定访问权限。

    - 平台管理员（is_platform_admin）：可管任意 user_id（group_ids 为 None 即旁路）
    - 组用户：只能管自己，或与目标用户有共同组（目标 user_id ∈ 任意当前用户所属组成员）
    """
    if group_ids is None:
        return  # 平台管理员旁路
    if target_user_id == user.id:
        return
    # 查目标用户所属组，与当前用户所属组求交集
    res = await db.execute(
        select(user_group_members.c.group_id).where(
            user_group_members.c.user_id == target_user_id
        )
    )
    target_groups = {r[0] for r in res.all()}
    if target_groups.isdisjoint(group_ids):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权操作该用户的 IM 绑定",
        )


@router.get("", response_model=ImBindingListResponse)
async def get_user_im_bindings(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _check_user_scope(db, user, user_id, group_ids)
    bindings, total = await list_bindings_by_user(db, user_id)
    return ImBindingListResponse(
        items=[ImBindingResponse.model_validate(b) for b in bindings],
        total=total,
    )


@router.post("", response_model=ImBindingResponse, status_code=status.HTTP_201_CREATED)
async def add_user_im_binding(
    user_id: UUID,
    data: ImBindingCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _check_user_scope(db, user, user_id, group_ids)
    try:
        binding = await create_binding(db, user_id, data)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 IM 平台用户 ID 已绑定",
        )
    return ImBindingResponse.model_validate(binding)


@router.delete("/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_im_binding(
    user_id: UUID,
    binding_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _check_user_scope(db, user, user_id, group_ids)
    deleted = await delete_binding(db, binding_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="IM 绑定记录未找到",
        )
