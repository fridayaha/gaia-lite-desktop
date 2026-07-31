"""用户组隔离 scope 依赖与写校验。

平台最小隔离单元 = UserGroup。所有租户化资源（定义/实例/运行时/私有资源池）
按 group_id 归属，跨组不可见。

- 组用户：仅可见/操作所属组资源（查询条件 group_id IN 所属组）
- 平台管理员：跨组可见可管（scope 旁路，group_ids 返回 None）
- 资源池特殊：平台共享池(group_id IS NULL)对所有组可见

service 层统一接收 group_ids: list[UUID] | None 参数；None 表示平台管理员旁路
（不过滤），非 None 列表则按 IN 过滤（空列表=组用户但无组，看不见任何资源）。
"""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db
from app.core.auth import get_current_user, is_platform_admin
from app.models import User, user_group_members


async def get_current_group_ids(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UUID] | None:
    """当前用户所属组 id 列表。

    平台管理员返回 None（旁路，service 层不过滤）。
    """
    if is_platform_admin(user):
        return None
    rows = await db.execute(
        select(user_group_members.c.group_id).where(
            user_group_members.c.user_id == user.id
        )
    )
    return [r[0] for r in rows.all()]


def assert_group_writable(
    target_group_id: UUID | None,
    group_ids: list[UUID] | None,
) -> None:
    """写操作校验：target_group_id 必须在调用者可操作的组范围内。

    - group_ids 为 None：平台管理员，允许任意 target_group_id（但不可为 None）
    - group_ids 为列表：组用户，target_group_id 必须 ∈ group_ids，否则 403
    """
    if target_group_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="缺少目标用户组"
        )
    if group_ids is None:
        return  # 平台管理员旁路
    if target_group_id not in group_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="无权操作该用户组的资源"
        )


def visible_filter(group_ids: list[UUID] | None):
    """资源池可见性条件：平台共享池(group_id NULL) 或 所属组私有池。

    返回 SQLAlchemy 条件表达式，供 ResourcePool 查询追加。
    group_ids 为 None（平台管理员）时返回 None（调用方不追加条件）。
    """
    from app.models import ResourcePool

    if group_ids is None:
        return None
    return (ResourcePool.group_id.is_(None)) | (ResourcePool.group_id.in_(group_ids))
