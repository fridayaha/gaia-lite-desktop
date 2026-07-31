from uuid import UUID

from app.core.auth import get_current_user, is_platform_admin
from app.core.group_scope import get_current_group_ids
from app.models import User
from app.schemas import (
    UserGroupCreate,
    UserGroupDetailResponse,
    UserGroupResponse,
    UserGroupUpdate,
)
from app.services.user_group_service import (
    create_group,
    delete_group,
    get_group,
    list_groups,
    update_group,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/user-groups", tags=["user-groups"])


def _check_group_scope(group_id: UUID, group_ids: list[UUID] | None) -> None:
    """组用户只能访问所属组；平台管理员(group_ids=None)旁路。跨组返回 404。"""
    if group_ids is not None and group_id not in group_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UserGroup not found")


@router.get("", response_model=list[UserGroupResponse])
async def get_user_groups(
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    groups = await list_groups(db, group_ids)
    return [
        UserGroupResponse(
            id=g.id,
            name=g.name,
            code=g.code,
            description=g.description,
            member_count=len(g.members) if hasattr(g, "members") else 0,
            created_at=g.created_at,
        )
        for g in groups
    ]


@router.post("", response_model=UserGroupResponse, status_code=status.HTTP_201_CREATED)
async def add_user_group(
    data: UserGroupCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可创建用户组")
    group = await create_group(db, data, actor_id=user.id)
    return UserGroupResponse(
        id=group.id,
        name=group.name,
        code=group.code,
        description=group.description,
        member_count=len(group.members) if hasattr(group, "members") else 0,
        created_at=group.created_at,
    )


@router.get("/{group_id}", response_model=UserGroupDetailResponse)
async def get_user_group_detail(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    _check_group_scope(group_id, group_ids)
    group = await get_group(db, group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="UserGroup not found"
        )
    return UserGroupDetailResponse(
        id=group.id,
        name=group.name,
        code=group.code,
        description=group.description,
        member_count=len(group.members) if hasattr(group, "members") else 0,
        created_at=group.created_at,
        members=[
            {"id": str(u.id), "username": u.username, "email": u.email}
            for u in group.members
        ],
    )


@router.put("/{group_id}", response_model=UserGroupResponse)
async def edit_user_group(
    group_id: UUID,
    data: UserGroupUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    _check_group_scope(group_id, group_ids)
    group = await update_group(db, group_id, data, actor_id=user.id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="UserGroup not found"
        )
    return UserGroupResponse(
        id=group.id,
        name=group.name,
        code=group.code,
        description=group.description,
        member_count=len(group.members) if hasattr(group, "members") else 0,
        created_at=group.created_at,
    )


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_group(
    group_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    if not is_platform_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可删除用户组")
    _check_group_scope(group_id, group_ids)
    deleted = await delete_group(db, group_id, actor_id=user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="UserGroup not found"
        )
