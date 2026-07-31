from uuid import UUID

from app.core.auth import hash_password
from app.models import AgentProfile, Role, User
from app.schemas import UserCreate, UserUpdate
from app.services import litellm_client
from app.services.audit_service import log_operation
from app.services.preset_avatars import preset_path_for_username
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


async def _sync_litellm_user(user: User) -> None:
    """best-effort 同步 UA user → LiteLLM user（user_id = str(user.id)）。"""
    try:
        await litellm_client.ensure_user(str(user.id), user_alias=user.username)
    except litellm_client.LitellmError as e:
        print(f"[litellm] sync user skipped: {e.message}")


async def list_users(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str = None,
    role_id: UUID = None,
    is_active: bool = None,
) -> tuple[list[User], int]:
    query = select(User).options(selectinload(User.roles))

    if search:
        query = query.where(User.username.ilike(f"%{search}%") | User.email.ilike(f"%{search}%"))
    if role_id:
        query = query.where(User.roles.any(Role.id == role_id))
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = query.order_by(User.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    users = list(result.scalars().all())

    return users, total


async def create_user(db: AsyncSession, data: UserCreate, *, actor_id: UUID) -> User:
    user = User(
        username=data.username,
        real_name=data.real_name,
        email=data.email,
        phone=data.phone,
        email_verified=getattr(data, "email_verified", False) or False,
        phone_verified=getattr(data, "phone_verified", False) or False,
        hashed_password=hash_password(data.password),
        avatar_url=preset_path_for_username(data.username),
    )
    if data.role_ids:
        result = await db.execute(select(Role).where(Role.id.in_(data.role_ids)))
        user.roles = list(result.scalars().all())
    db.add(user)
    await db.flush()
    await log_operation(
        db,
        actor_id=actor_id,
        action="user.create",
        target_type="user",
        target_id=user.id,
        detail={
            "username": user.username,
            "real_name": user.real_name,
            "role_ids": [str(r) for r in data.role_ids],
        },
    )
    await db.commit()
    await db.refresh(user, ["roles"])
    await _sync_litellm_user(user)
    return user


async def get_user(db: AsyncSession, user_id: UUID) -> User | None:
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def update_user(
    db: AsyncSession, user_id: UUID, data: UserUpdate, *, actor_id: UUID
) -> User | None:
    user = await get_user(db, user_id)
    if not user:
        return None

    if data.username is not None:
        user.username = data.username
    if data.real_name is not None:
        user.real_name = data.real_name
    if data.email is not None and data.email != user.email:
        user.email = data.email
        user.email_verified = False  # 改 email 后回退，需重新认证
    if data.phone is not None and data.phone != user.phone:
        user.phone = data.phone
        user.phone_verified = False  # 改 phone 后回退，需重新认证
    if data.password is not None:
        user.hashed_password = hash_password(data.password)
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.role_ids is not None:
        result = await db.execute(select(Role).where(Role.id.in_(data.role_ids)))
        user.roles = list(result.scalars().all())

    await log_operation(
        db,
        actor_id=actor_id,
        action="user.update",
        target_type="user",
        target_id=user.id,
        detail={
            "username": user.username,
            "fields": [k for k in data.model_fields_set if getattr(data, k) is not None],
        },
    )
    await db.commit()
    await db.refresh(user, ["roles"])
    if data.username is not None or data.is_active is not None:
        await _sync_litellm_user(user)
    # 用户基本信息不再写引擎文件：智能体经 current-user-info 预置 skill 调
    # /api/controller/profiles/{profile_name}/user-context 实时 pull 最新值，
    # 无需 fan-out 同步（见重构方案 1-hermes-user-md-2-federated-moon）。
    return user


async def delete_user(db: AsyncSession, user_id: UUID, *, actor_id: UUID) -> bool:
    user = await get_user(db, user_id)
    if not user:
        return False
    username = user.username

    # 1. 清理该用户的所有 per-user profile 全链路资源（DB 行 + Pod 上 port_map/
    #    目录/gateway/nginx）。按 user_id 过滤：凡 user_id 指向本用户的 profile 都是
    #    该用户独占的 INDEPENDENT profile。best-effort：单个 profile 清理失败不阻断
    #    删用户——防漂移优先，DB 行最终由 teardown_profile 自身删除，残留 Pod 资源
    #    由 entrypoint reconcile 兜底。
    profiles = await db.execute(select(AgentProfile).where(AgentProfile.user_id == user_id))
    for profile in profiles.scalars().all():
        try:
            from app.worker.profiles import teardown_profile

            await teardown_profile(db, profile)
        except Exception as e:
            print(f"[delete_user] teardown profile {profile.profile_name[:16]} failed: {e}")

    # 2. 删 IM 绑定：im_user_bindings.user_id 无 ondelete 规则（RESTRICT），
    #    不显式删会致删用户时 IntegrityError。
    #    业务绑定 business_user_bindings.user_id ondelete=CASCADE，删 user 时自动删，无需显式。
    await db.execute(
        text("DELETE FROM im_user_bindings WHERE user_id = :uid"),
        {"uid": str(user_id)},
    )

    await log_operation(
        db,
        actor_id=actor_id,
        action="user.delete",
        target_type="user",
        target_id=user_id,
        detail={"username": username},
    )
    await db.delete(user)
    await db.commit()
    return True


async def list_user_profiles(db: AsyncSession, user_id: UUID) -> tuple[list[dict], int]:
    """查某用户在哪些实例上有 per-user profile（供删用户确认框展示）。

    LEFT JOIN agent_instances 取实例名（profile 行可能指向已删实例，name 为空也保留）。
    按 user_id 过滤：凡 user_id 指向本用户的 INDEPENDENT profile 都算。
    """
    from app.models import AgentInstance

    result = await db.execute(
        select(AgentProfile, AgentInstance.name)
        .outerjoin(AgentInstance, AgentInstance.id == AgentProfile.instance_id)
        .where(AgentProfile.user_id == user_id)
        .order_by(AgentProfile.created_at.desc())
    )
    items = [
        {
            "instance_id": str(profile.instance_id),
            "instance_name": instance_name,
            "profile_name": profile.profile_name,
            "created_at": profile.created_at.isoformat() if profile.created_at else None,
        }
        for profile, instance_name in result.all()
    ]
    return items, len(items)
