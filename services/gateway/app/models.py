"""Gateway's DB access layer — reads channel config via shared PostgreSQL.

Gateway 没有自己的模型，但需要读取 Manager 创建的 agent_channels 表来
获取 IM 渠道配置。这里封装对共享 PG 的读取访问。

配置缓存：60s TTL，减少重复 DB 查询。
"""
import logging
import time
from textwrap import dedent

from pkg.common.database import async_session

logger = logging.getLogger(__name__)

# ── 配置缓存 ────────────────────────────────────────────────

_config_cache: dict[str, tuple[dict, float]] = {}
_cache_ttl: float = 60.0  # 60 秒 TTL


def _cache_key(agent_id: str, channel_type: str) -> str:
    return f"{agent_id}:{channel_type}"


def _invalidate_channel_config_cache(agent_id: str, channel_type: str):
    """主动失效配置缓存（供 Manager 通知时调用）"""
    key = _cache_key(agent_id, channel_type)
    _config_cache.pop(key, None)


async def ensure_tables():
    """Ensure the Gateway DB connection works (tables managed by Manager)."""
    async with async_session() as session:
        await session.execute(dedent("""\
            SELECT 1 FROM information_schema.tables
            WHERE table_name IN ('agent_instance_channels', 'agent_instance_api_keys')
        """))


async def get_channel_config(agent_id: str, channel_type: str) -> dict | None:
    """读取指定 Instance·渠道配置（仅 enabled 的）— 无缓存

    V3: agent_id 语义 = instance_id，查 agent_instance_channels。
    供 router 等一次性检查场景使用。
    """
    from sqlalchemy import text
    async with async_session() as session:
        result = await session.execute(
            text("SELECT config FROM agent_instance_channels "
                 "WHERE instance_id = :aid AND channel_type = :ct AND enabled = true"),
            {"aid": agent_id, "ct": channel_type},
        )
        row = result.mappings().first()
        return row["config"] if row else None


async def get_channel_config_cached(agent_id: str, channel_type: str) -> dict | None:
    """读取渠道配置，带 60s TTL 缓存

    供 dispatcher 等高频调用场景使用。
    """
    key = _cache_key(agent_id, channel_type)
    now = time.time()

    # 检查缓存命中
    cached = _config_cache.get(key)
    if cached is not None:
        config, expire_at = cached
        if now < expire_at:
            return config
        # 已过期
        del _config_cache[key]

    # 查 DB
    config = await get_channel_config(agent_id, channel_type)
    if config is not None:
        _config_cache[key] = (config, now + _cache_ttl)

    return config


async def get_agent_model_config(agent_id: str) -> dict:
    """读取 Instance 的 LiteLLM 模型配置

    V3: agent_id 语义 = instance_id，从 agent_instances.litellm_config 读取
    per-instance 的 litellm 段，包装成 {"litellm": ...} 供 get_default_model 使用。
    """
    from sqlalchemy import text
    async with async_session() as session:
        result = await session.execute(
            text("SELECT litellm_config FROM agent_instances WHERE id = :aid"),
            {"aid": agent_id},
        )
        row = result.mappings().first()
        if not row:
            return {}
        lc = row["litellm_config"] or {}
        if isinstance(lc, str):
            import json
            lc = json.loads(lc)
        return {"litellm": lc} if lc else {}


def get_default_model(model_config: dict) -> str:
    """从 model_config 中提取默认模型名（LiteLLM 模型组名）。

    引擎只走 LiteLLM：返回 model_config.litellm.model，否则空串。
    """
    litellm = (model_config or {}).get("litellm") or {}
    return litellm.get("model", "")
