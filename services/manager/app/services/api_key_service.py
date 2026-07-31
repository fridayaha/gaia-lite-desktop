"""OpenAI 兼容 API Key CRUD 服务 — 挂在 AgentInstance 层。

Key 格式：sk- + secrets.token_urlsafe(32)（~46 字符，256-bit 熵）
存储：HMAC-SHA256 hex（不可逆）；明文仅创建时返回一次。
每实例最多 10 个（service 层 enforce，SELECT FOR UPDATE 串行化避免 race）。
"""
import hashlib
import hmac
import secrets
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentApiKey, AgentInstance
from app.services.audit_service import log_operation
from pkg.common.config import settings

KEY_PREFIX_LEN = 14  # "sk-" + 11 chars base64url
MAX_KEYS_PER_INSTANCE = 10

# dev 环境留空时的固定默认值（仅供本地测试，prod 由 assert_api_key_hmac_secret 强制显式设置）
_DEV_DEFAULT_SECRET = "ua-api-key-hmac-dev-secret-change-in-production-min-32-chars"


def _get_hmac_secret() -> str:
    """获取 HMAC 密钥。dev 留空时回退到固定默认值；prod 由 assert_api_key_hmac_secret 强制。"""
    return settings.api_key_hmac_secret or _DEV_DEFAULT_SECRET


def _hmac_hash(full_key: str) -> str:
    """计算 full_key 的 HMAC-SHA256 hex。"""
    return hmac.new(
        _get_hmac_secret().encode(),
        full_key.encode(),
        hashlib.sha256,
    ).hexdigest()


def _generate_api_key() -> tuple[str, str]:
    """生成 (full_key, key_prefix)。full_key 返回给用户一次；prefix 存 DB。"""
    raw = secrets.token_urlsafe(32)  # ~43 chars base64url, 256-bit entropy
    full_key = f"sk-{raw}"
    return full_key, full_key[:KEY_PREFIX_LEN]


def verify_hmac(full_key: str, stored_hash: str) -> bool:
    """常量时间比较，避免时序侧信道。供 gateway 调用。"""
    return hmac.compare_digest(_hmac_hash(full_key), stored_hash)


async def create_key(
    db: AsyncSession,
    instance_id: UUID,
    name: str,
    *,
    actor_id: UUID,
) -> tuple[AgentApiKey, str]:
    """为实例创建 API Key。返回 (key_record, full_plaintext_key)。

    Raises:
        ValueError: 实例不存在
        HTTPException 400: 超过 MAX_KEYS_PER_INSTANCE 上限
    """
    # SELECT FOR UPDATE on parent 串行化 per-instance key 创建（避免 race 导致超 10）
    inst = (
        await db.execute(
            select(AgentInstance).where(AgentInstance.id == instance_id).with_for_update()
        )
    ).scalar_one_or_none()
    if not inst:
        raise ValueError(f"AgentInstance {instance_id} not found")

    count = (
        await db.execute(
            select(func.count())
            .select_from(AgentApiKey)
            .where(AgentApiKey.instance_id == instance_id)
        )
    ).scalar() or 0
    if count >= MAX_KEYS_PER_INSTANCE:
        raise HTTPException(
            status_code=400,
            detail=f"每个智能体最多 {MAX_KEYS_PER_INSTANCE} 个 API Key",
        )

    full_key, prefix = _generate_api_key()
    key = AgentApiKey(
        instance_id=instance_id,
        group_id=inst.group_id,
        name=name,
        key_hash=_hmac_hash(full_key),
        key_prefix=prefix,
        created_by=actor_id,
    )
    db.add(key)
    await db.flush()
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_apikey.create",
        target_type="agent_apikey",
        target_id=key.id,
        group_id=inst.group_id,
        detail={
            "instance_id": str(instance_id),
            "name": name,
            "key_prefix": prefix,  # 永远不记明文 key
        },
    )
    await db.commit()
    await db.refresh(key)
    return key, full_key


async def list_keys(
    db: AsyncSession,
    instance_id: UUID,
) -> tuple[list[AgentApiKey], int]:
    """列出实例的所有 API Key（不含明文，只有 prefix）。"""
    query = select(AgentApiKey).where(AgentApiKey.instance_id == instance_id)
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    result = await db.execute(query.order_by(AgentApiKey.created_at.desc()))
    keys = list(result.scalars().all())
    return keys, total


async def delete_key(
    db: AsyncSession,
    key_id: UUID,
    *,
    actor_id: UUID,
) -> bool:
    """硬删除 API Key。返回 False 表示 key 不存在。"""
    key = (
        await db.execute(select(AgentApiKey).where(AgentApiKey.id == key_id))
    ).scalar_one_or_none()
    if not key:
        return False

    instance_id = key.instance_id
    group_id = key.group_id
    prefix = key.key_prefix
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_apikey.delete",
        target_type="agent_apikey",
        target_id=key_id,
        group_id=group_id,
        detail={
            "instance_id": str(instance_id),
            "key_prefix": prefix,  # 永远不记明文 key
        },
    )
    await db.delete(key)
    await db.commit()
    return True
