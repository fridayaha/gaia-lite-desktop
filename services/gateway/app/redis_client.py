"""Redis 共享存储客户端（wecom_bot_callback 流式状态跨副本共享用）。

降级模式：``UA_REDIS_URL`` 未配置时 ``get_redis()`` 返回 ``None``，调用方走内存模式
（单副本/本地冒烟/单测不依赖 Redis）。
"""
import logging

from app.settings import settings

logger = logging.getLogger(__name__)

_redis = None
_initialized = False


async def get_redis():
    """返回 Redis async 单例；未配置 redis_url 时返回 None（降级内存模式）。"""
    global _redis, _initialized
    if not settings.redis_url:
        return None
    if not _initialized:
        try:
            import redis.asyncio as aioredis

            _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            # 触发一次连接验证
            await _redis.ping()
            logger.info("Redis connected for wecom_bot_callback shared store: %s", settings.redis_url)
        except Exception as e:
            logger.warning(
                "Redis connect failed (%s), degrade to in-memory (multi-replica streaming "
                "will break): %s", settings.redis_url, e,
            )
            _redis = None
        _initialized = True
    return _redis
