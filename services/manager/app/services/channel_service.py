"""V3 IM 渠道配置 CRUD 服务 — 渠道绑定挂在 AgentInstance 层。

渠道恒为 USER 级 INDEPENDENT Profile（组共享 SHARED 已下线）。实例归属单一用户组
仅决定访问范围（USER_GROUP），不再决定 Profile 共享维度。
"""
from datetime import UTC, datetime
from uuid import UUID

from app.models import AgentInstance, AgentInstanceChannel
from app.schemas import AgentInstanceChannelCreate, AgentInstanceChannelUpdate, SENSITIVE_MASK
from app.services.audit_service import log_operation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings


def _generate_callback_url(instance_id: UUID, channel_type: str) -> str:
    """生成 IM 平台回调 URL。

    完整 URL = callback_base_url + 路径（UA_CALLBACK_BASE_URL 配置）。
    未配置时返回相对路径（兼容旧版，但飞书等平台需完整 URL）。
    """
    path = f"/api/gateway/channel/{channel_type}/{instance_id}/callback"
    base = settings.callback_base_url
    if base:
        return base.rstrip("/") + path
    return path


async def list_channels(
    db: AsyncSession,
    instance_id: UUID,
) -> tuple[list[AgentInstanceChannel], int]:
    """列出指定实例的所有渠道"""
    query = select(AgentInstanceChannel).where(AgentInstanceChannel.instance_id == instance_id)
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    result = await db.execute(query.order_by(AgentInstanceChannel.created_at.desc()))
    channels = list(result.scalars().all())
    return channels, total


async def create_channel(
    db: AsyncSession,
    instance_id: UUID,
    data: AgentInstanceChannelCreate,
    *,
    actor_id: UUID,
) -> AgentInstanceChannel:
    """创建渠道绑定（恒为 INDEPENDENT 用户级独占 profile；SHARED 已下线，传入值被忽略）"""
    instance = await db.get(AgentInstance, instance_id)
    if not instance:
        raise ValueError(f"AgentInstance {instance_id} not found")

    # SHARED 已下线：统一 INDEPENDENT/USER（gateway 用 user hash 生成 profile_name）
    profile_type = "INDEPENDENT"
    scope_type, scope_target_id = "USER", None

    channel = AgentInstanceChannel(
        instance_id=instance_id,
        group_id=instance.group_id,
        channel_type=data.channel_type,
        scope_type=scope_type,
        scope_target_id=scope_target_id,
        profile_type=profile_type,
        config=data.config,
        enabled=True,
        callback_url=_generate_callback_url(instance_id, data.channel_type),
    )
    db.add(channel)
    await db.flush()
    await log_operation(
        db, actor_id=actor_id, action="agent_channel.create",
        target_type="agent_channel", target_id=channel.id,
        group_id=instance.group_id,
        detail={"instance_id": str(instance_id), "channel_type": data.channel_type, "profile_type": profile_type},
    )
    await db.commit()
    await db.refresh(channel)
    return channel


async def get_channel(
    db: AsyncSession,
    channel_id: UUID,
) -> AgentInstanceChannel | None:
    result = await db.execute(
        select(AgentInstanceChannel).where(AgentInstanceChannel.id == channel_id)
    )
    return result.scalar_one_or_none()


async def ensure_http_channel(db: AsyncSession, instance: AgentInstance) -> AgentInstanceChannel | None:
    """确保实例存在 http 渠道绑定（web 聊天必需）。

    上线时自动调用：若无 http 渠道则创建一条 INDEPENDENT 用户级独占 profile
    （与 create_channel 默认值一致，避免 web/IM 跨渠道产生两个 profile）。
    幂等：已存在则直接返回。
    """
    result = await db.execute(
        select(AgentInstanceChannel).where(
            AgentInstanceChannel.instance_id == instance.id,
            AgentInstanceChannel.channel_type == "http",
            AgentInstanceChannel.enabled.is_(True),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing

    channel = AgentInstanceChannel(
        instance_id=instance.id,
        group_id=instance.group_id,
        channel_type="http",
        scope_type="USER",
        scope_target_id=None,
        profile_type="INDEPENDENT",
        config={},
        enabled=True,
        callback_url=None,
    )
    db.add(channel)
    await db.flush()
    return channel


async def ensure_http_channel_by_id(db: AsyncSession, instance_id: UUID) -> AgentInstanceChannel | None:
    """按 instance_id 加载实例后确保 http 渠道（web 聊天必需，幂等）。"""
    instance = await db.get(AgentInstance, instance_id)
    if not instance:
        return None
    return await ensure_http_channel(db, instance)


async def update_channel(
    db: AsyncSession,
    channel_id: UUID,
    data: AgentInstanceChannelUpdate,
    *,
    actor_id: UUID,
) -> AgentInstanceChannel | None:
    channel = await get_channel(db, channel_id)
    if not channel:
        return None
    if data.config is not None:
        # 合并而非全量替换：敏感字段（secret/key/token）传空值或掩码表示保持不变，
        # 避免编辑单个字段时清空其他凭据。
        sensitive = ("secret", "key", "token")
        merged = dict(channel.config or {})
        for k, v in (data.config or {}).items():
            if v in ("", SENSITIVE_MASK) and any(s in k.lower() for s in sensitive):
                continue  # 空值或掩码 → 保留原值
            merged[k] = v
        channel.config = merged
    if data.enabled is not None:
        channel.enabled = data.enabled
    # profile_type 不可变（SHARED 已下线，恒为 INDEPENDENT）；忽略客户端传入值。
    channel.updated_at = datetime.now(UTC)
    await log_operation(
        db, actor_id=actor_id, action="agent_channel.update",
        target_type="agent_channel", target_id=channel_id,
        group_id=channel.group_id,
        detail={"instance_id": str(channel.instance_id), "fields": [k for k in data.model_fields_set if getattr(data, k) is not None]},
    )
    await db.commit()
    await db.refresh(channel)
    return channel


async def delete_channel(
    db: AsyncSession,
    channel_id: UUID,
    *,
    actor_id: UUID,
) -> bool:
    channel = await get_channel(db, channel_id)
    if not channel:
        return False
    instance_id = channel.instance_id
    channel_type = channel.channel_type
    await log_operation(
        db, actor_id=actor_id, action="agent_channel.delete",
        target_type="agent_channel", target_id=channel_id,
        group_id=channel.group_id,
        detail={"instance_id": str(instance_id), "channel_type": channel_type},
    )
    await db.delete(channel)
    await db.commit()
    return True


async def get_channel_by_instance_type(
    db: AsyncSession,
    instance_id: UUID,
    channel_type: str,
) -> AgentInstanceChannel | None:
    """Gateway 用：按 instance_id + channel_type 查询启用中的渠道配置"""
    result = await db.execute(
        select(AgentInstanceChannel).where(
            AgentInstanceChannel.instance_id == instance_id,
            AgentInstanceChannel.channel_type == channel_type,
            AgentInstanceChannel.enabled == True,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()
