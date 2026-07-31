"""Profile ↔ 用户映射 helper。

资源池/实例详情页的日志来源接口返回的 `profiles` 是 `profile_name` 字符串列表
（由 controller 从 Pod 内 `/tmp/gateway-{profile}.log` 文件名解析得到），对管理员
毫无可读性。本 helper 把 `profile_name` 经 `AgentProfile.user_id` join 到 `users`，
补出 `username` / `real_name`，供前端展示「真实姓名(用户名)」。
"""

from uuid import UUID

from app.models import AgentProfile, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def map_profiles_to_users(
    db: AsyncSession,
    profile_names: list[str],
    *,
    pool_id: UUID | None = None,
    instance_id: UUID | None = None,
) -> dict[str, dict]:
    """profile_name -> {"username": str|None, "real_name": str|None}。

    按 pool_id 或 instance_id 维度过滤（二选一），避免跨池/跨实例同名 profile 串扰。
    `user_id` 为空（共享/平台 profile）时 username/real_name 为 None，前端回退显示 profile_name。
    """
    if not profile_names:
        return {}

    stmt = (
        select(AgentProfile.profile_name, User.username, User.real_name)
        .join(User, User.id == AgentProfile.user_id, isouter=True)
        .where(AgentProfile.profile_name.in_(profile_names))
    )
    if pool_id is not None:
        stmt = stmt.where(AgentProfile.resource_pool_id == pool_id)
    if instance_id is not None:
        stmt = stmt.where(AgentProfile.instance_id == instance_id)

    rows = await db.execute(stmt)
    return {
        pn: {"username": uname, "real_name": rname}
        for pn, uname, rname in rows.all()
    }


def enrich_profiles(
    profile_names: list[str], user_map: dict[str, dict]
) -> list[dict]:
    """把 controller 返回的 profile_name 列表富化为对象数组。"""
    return [
        {
            "profile_name": pn,
            "username": user_map.get(pn, {}).get("username"),
            "real_name": user_map.get(pn, {}).get("real_name"),
        }
        for pn in profile_names
    ]
