"""OpenAI 兼容 API Key 鉴权（sk- 前缀）— Gateway 端验证模块。

缓存策略：
  - _api_key_cache: key_prefix → (instance_id, key_hash, key_id, last_db_update_ts, engine_type)
  - 60s TTL（避免每请求打 DB；Manager 删除后最长 60s 内仍可用，UI 文案提示「删除后短期内可能仍生效」）
  - 每次请求仍 HMAC verify（prefix 是 UI 可见的不可信，不能跳过）

last_used_at 节流：
  - 每 60s 最多一次 DB 写（asyncio.create_task fire-and-forget）
  - 避免高 QPS 下每请求都写 DB

Gateway 反向依赖约束：本模块只查共享 PG（agent_instance_api_keys JOIN agent_instances
JOIN agent_definitions 拿 engine_type），不调 manager API。
"""
import asyncio
import hashlib
import hmac
import logging
import time
from uuid import UUID

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import text

from app.settings import settings
from pkg.common.database import async_session

logger = logging.getLogger(__name__)

# dev 环境留空时的固定默认值（与 manager api_key_service 保持一致）
_DEV_DEFAULT_SECRET = "ua-api-key-hmac-dev-secret-change-in-production-min-32-chars"

_api_key_cache: dict[str, tuple[UUID, str, UUID, float, str]] = {}
_API_KEY_CACHE_TTL: float = 60.0  # 60s TTL
_LAST_USED_THROTTLE: float = 60.0  # 节流：每 60s 最多一次 DB 写 last_used_at


def _get_hmac_secret() -> str:
    """获取 HMAC 密钥。dev 留空回退默认值；prod 由 assert_api_key_hmac_secret 强制。"""
    return settings.api_key_hmac_secret or _DEV_DEFAULT_SECRET


def _hmac_hash(full_key: str) -> str:
    return hmac.new(
        _get_hmac_secret().encode(),
        full_key.encode(),
        hashlib.sha256,
    ).hexdigest()


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[UUID, UUID, str, str]:
    """验证 sk- 风格 API Key。返回 (instance_id, key_id, key_prefix, engine_type)。

    engine_type 从 agent_definitions JOIN 拿到，用于 sk- 路径下覆盖 gateway
    默认的 X-Engine-Type=HERMES（OpenAI SDK 不传此头，Dify/其他引擎实例会路由错）。

    Raises:
        HTTPException 401: credentials 缺失 / 前缀不匹配 / DB 无此 prefix / HMAC 不匹配
    """
    if not credentials or not credentials.credentials.startswith("sk-"):
        raise HTTPException(status_code=401, detail="Invalid API key")
    full_key = credentials.credentials
    prefix = full_key[:14]  # sk- + 11 chars

    now = time.time()
    cached = _api_key_cache.get(prefix)
    if cached and now - cached[3] <= _API_KEY_CACHE_TTL:
        instance_id, key_hash, key_id, _, engine_type = cached
    else:
        # 缓存过期或 miss → DB 查询（JOIN agent_instances + agent_definitions 拿 engine_type）
        try:
            async with async_session() as db:
                row = (
                    await db.execute(
                        text(
                            "SELECT k.id, k.instance_id, k.key_hash, d.engine_type "
                            "FROM agent_instance_api_keys k "
                            "JOIN agent_instances i ON k.instance_id = i.id "
                            "JOIN agent_definitions d ON i.definition_id = d.id "
                            "WHERE k.key_prefix = :p LIMIT 1"
                        ),
                        {"p": prefix},
                    )
                ).mappings().first()
        except Exception as e:
            logger.warning("API key DB lookup error for %s: %s", prefix, e)
            raise HTTPException(status_code=401, detail="Invalid API key")
        if not row:
            # 删除已过期的缓存项（避免 deleted key 的 prefix 一直占位）
            if cached:
                _api_key_cache.pop(prefix, None)
            raise HTTPException(status_code=401, detail="Invalid API key")
        instance_id = row["instance_id"]
        key_hash = row["key_hash"]
        key_id = row["id"]
        engine_type = row["engine_type"]
        _api_key_cache[prefix] = (instance_id, key_hash, key_id, now, engine_type)

    # 始终 HMAC verify（prefix 是 UI 可见的不可信；不能仅凭缓存命中就放行）
    if not hmac.compare_digest(_hmac_hash(full_key), key_hash):
        raise HTTPException(status_code=401, detail="Invalid API key")

    # 节流更新 last_used_at（fire-and-forget，不阻塞请求）
    cached = _api_key_cache.get(prefix)
    if cached and now - cached[3] > _LAST_USED_THROTTLE:
        asyncio.create_task(_update_last_used(key_id, prefix))

    return instance_id, key_id, prefix, engine_type


async def _update_last_used(key_id: UUID, prefix: str) -> None:
    """fire-and-forget 更新 last_used_at。失败仅 warning 不抛异常。"""
    try:
        async with async_session() as db:
            await db.execute(
                text(
                    "UPDATE agent_instance_api_keys SET last_used_at = NOW() "
                    "WHERE id = :kid"
                ),
                {"kid": key_id},
            )
            await db.commit()
        cached = _api_key_cache.get(prefix)
        if cached:
            # 只更新时间戳，engine_type 保持不变（实例定义改引擎类型需重建 Key）
            _api_key_cache[prefix] = (cached[0], cached[1], cached[2], time.time(), cached[4])
    except Exception as e:
        logger.warning("Failed to update last_used_at for %s: %s", prefix, e)


def invalidate_cache(prefix: str) -> None:
    """同进程内失效缓存（跨进程需 Redis pub/sub，v1 走 TTL 兜底）。"""
    _api_key_cache.pop(prefix, None)
