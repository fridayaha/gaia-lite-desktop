import re
from uuid import UUID

from app.models import User, UserGroup
from app.schemas import UserGroupCreate, UserGroupUpdate
from app.services import litellm_client
from app.services.audit_service import log_operation
from pypinyin import Style, lazy_pinyin
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


def _generate_code(name: str) -> str:
    """组机器码：中文名转拼音、英文名直用，仅保留小写字母数字与短横。"""
    raw = "".join(lazy_pinyin(name, style=Style.NORMAL))
    raw = re.sub(r"[^a-zA-Z0-9]+", "-", raw).lower().strip("-")
    return raw or "group"


async def _ensure_unique_code(db: AsyncSession, base: str) -> str:
    """全局唯一 code，撞码加 -2/-3 后缀。"""
    code = base
    n = 2
    while True:
        exists = (
            await db.execute(
                select(func.count()).select_from(
                    select(UserGroup).where(UserGroup.code == code).subquery()
                )
            )
        ).scalar() or 0
        if not exists:
            return code
        code = f"{base}-{n}"
        n += 1


async def _sync_team_members(group: UserGroup, member_ids: list[UUID]) -> None:
    """best-effort 同步：确保 team 存在并把成员加入对应 LiteLLM Team。"""
    team_id = group.litellm_team_id or str(group.id)
    try:
        await litellm_client.ensure_team(team_id, group.name)
        for uid in member_ids:
            await litellm_client.ensure_user(str(uid))
            await litellm_client.team_member_add(team_id, str(uid))
    except litellm_client.LitellmError as e:
        print(f"[litellm] sync team members skipped: {e.message}")


async def list_groups(
    db: AsyncSession, group_ids: list[UUID] | None = None
) -> list[UserGroup]:
    """查询用户组，含成员数量和成员信息。

    group_ids 为 None（平台管理员）时返回全部；否则仅返回用户所属组。
    """
    query = (
        select(UserGroup)
        .options(selectinload(UserGroup.members))
        .order_by(UserGroup.created_at)
    )
    if group_ids is not None:
        query = query.where(UserGroup.id.in_(group_ids))
    return list((await db.execute(query)).scalars().all())


async def create_group(
    db: AsyncSession, data: UserGroupCreate, *, actor_id: UUID
) -> UserGroup:
    """创建用户组，可选关联成员。自动生成全局唯一 code，持久化 litellm_team_id。"""
    code = await _ensure_unique_code(db, _generate_code(data.name))
    group = UserGroup(name=data.name, code=code, description=data.description)
    if data.member_ids:
        result = await db.execute(
            select(User).where(User.id.in_(data.member_ids))
        )
        group.members = list(result.scalars().all())
    db.add(group)
    await db.flush()  # 取 group.id 以派生 litellm_team_id
    group.litellm_team_id = str(group.id)
    await log_operation(
        db,
        actor_id=actor_id,
        action="user_group.create",
        target_type="user_group",
        target_id=group.id,
        detail={"name": group.name, "code": group.code, "member_ids": [str(m) for m in data.member_ids]},
    )
    await db.commit()
    await db.refresh(group, ["members"])

    # 同步到 LiteLLM Team
    if data.member_ids:
        await _sync_team_members(group, data.member_ids)
    else:
        try:
            await litellm_client.ensure_team(group.litellm_team_id, group.name)
        except litellm_client.LitellmError as e:
            print(f"[litellm] ensure team skipped: {e.message}")

    return group


async def get_group(db: AsyncSession, group_id: UUID) -> UserGroup | None:
    """查询单个用户组"""
    result = await db.execute(
        select(UserGroup)
        .options(selectinload(UserGroup.members))
        .where(UserGroup.id == group_id)
    )
    return result.scalar_one_or_none()


async def update_group(
    db: AsyncSession, group_id: UUID, data: UserGroupUpdate, *, actor_id: UUID
) -> UserGroup | None:
    """更新用户组信息和成员"""
    group = await get_group(db, group_id)
    if not group:
        return None

    old_member_ids = {m.id for m in group.members}

    if data.name is not None:
        group.name = data.name
    if data.description is not None:
        group.description = data.description
    if data.member_ids is not None:
        result = await db.execute(
            select(User).where(User.id.in_(data.member_ids))
        )
        group.members = list(result.scalars().all())

    await log_operation(
        db,
        actor_id=actor_id,
        action="user_group.update",
        target_type="user_group",
        target_id=group.id,
        detail={
            "name": group.name,
            "fields": [k for k in data.model_fields_set if getattr(data, k) is not None],
        },
    )
    await db.commit()
    await db.refresh(group, ["members"])

    # 同步成员变更到 LiteLLM Team（best-effort）
    if data.member_ids is not None:
        new_set = set(data.member_ids)
        team_id = group.litellm_team_id or str(group.id)
        try:
            await litellm_client.ensure_team(team_id, group.name)
            for uid in new_set - old_member_ids:
                await litellm_client.ensure_user(str(uid))
                await litellm_client.team_member_add(team_id, str(uid))
            for uid in old_member_ids - new_set:
                await litellm_client.team_member_delete(team_id, str(uid))
        except litellm_client.LitellmError as e:
            print(f"[litellm] sync team members skipped: {e.message}")

    return group


async def delete_group(
    db: AsyncSession, group_id: UUID, *, actor_id: UUID
) -> bool:
    """删除用户组"""
    group = await get_group(db, group_id)
    if not group:
        return False
    name = group.name
    await log_operation(
        db,
        actor_id=actor_id,
        action="user_group.delete",
        target_type="user_group",
        target_id=group_id,
        detail={"name": name},
    )
    await db.delete(group)
    await db.commit()
    return True
