"""Langfuse public API 客户端封装（httpx + Basic Auth，不引入 langfuse SDK）。

manager 用此 client 查询 trace/observation 数据，供监控中心使用。

认证：HTTP Basic Auth，public_key:secret_key base64 编码。
未部署（key 为空）时所有方法返回 None，调用方降级处理。

支持两种配置来源：
- 全局：settings.langfuse_base_url/public_key/secret_key（Hermes trace 查询用，向后兼容）
- per-EngineConfig：传入 LangfuseConfig 实例（Dify 外接模式 per-app 用量查询用，
  每个 Dify EngineConfig 可对接不同 Langfuse 实例）
"""
from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from pkg.common.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# 全局并发信号量：monitoring_service.get_call_quality_summary / alert_service 会
# asyncio.gather 100 个 list_observations(trace_id) 同时打 langfuse，单实例 langfuse
# 扛不住 100 并发 → 大量 ReadTimeout（_TIMEOUT 内拿不到响应）。限制全局最多 10 个
# 并发查询，100 个排队依次跑，避免雪崩。（asyncio.Semaphore 在 3.10+ 不绑定 loop，
# 模块级创建安全。）
_LANGFUSE_CONCURRENCY = asyncio.Semaphore(10)


@dataclass(frozen=True)
class LangfuseConfig:
    """per-EngineConfig Langfuse 配置（Dify 外接模式用量查询用）。

    base_url 不带尾斜杠；public_key/secret_key 是 Langfuse project API key。
    """

    base_url: str
    public_key: str
    secret_key: str


class LangfuseError(Exception):
    """Langfuse API 调用异常。"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def is_configured() -> bool:
    """Langfuse 是否配置了全局凭据（Hermes trace 查询用）。

    per-EngineConfig 模式（Dify 外接）不查此函数，直接传 LangfuseConfig 给方法。
    """
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key)


def _resolve(config: LangfuseConfig | None) -> tuple[str, dict[str, str]] | None:
    """解析 base_url + headers。config=None 时用全局 settings；都没配返回 None。"""
    if config is not None:
        if not (config.base_url and config.public_key and config.secret_key):
            return None
        base = config.base_url.rstrip("/")
        token = base64.b64encode(
            f"{config.public_key}:{config.secret_key}".encode()
        ).decode()
        return base, {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }
    # 全局 fallback
    if not is_configured():
        return None
    base = settings.langfuse_base_url.rstrip("/")
    token = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
    ).decode()
    return base, {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _base() -> str:
    """向后兼容：旧调用方（observability.py 全局 trace 查询）用 _base()。

    新代码应通过 _resolve(None) 拿 base_url，避免在未配置时抛异常。
    """
    return settings.langfuse_base_url.rstrip("/")


def _headers() -> dict[str, str]:
    """向后兼容：旧调用方用 _headers()。"""
    token = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


async def list_traces(
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    name: str | None = None,
    from_ts: str | None = None,  # ISO 8601
    to_ts: str | None = None,
    limit: int = 50,
    offset: int = 0,
    order_field: str = "createdAt",
    order_dir: str = "DESC",
    metadata: dict[str, str] | None = None,
    config: LangfuseConfig | None = None,
) -> dict[str, Any] | None:
    """GET /api/public/traces — 列 trace。

    返回 {data: [...], meta: {totalItems, totalPages, ...}}。
    未配置 Langfuse 时返回 None。

    注：v3 的 orderBy 参数要求 JSON 对象，但服务端解析对 URL-encoding 敏感，
    实测去掉该参数时默认按 createdAt DESC 返回，满足需求，故不传 orderBy。

    - session_id: 按会话 ID 过滤（Langfuse v3 支持 sessionId 查询参数，实测生效）
    - metadata: 按 trace metadata 过滤（v3 支持 metadata[<key>]=<value> 结构化过滤，
      用于 Dify 外接模式按 metadata[app_id]=xxx 反查 per-app trace）
    - config: per-EngineConfig Langfuse 配置（Dify 外接模式用）；None 时用全局 settings
    """
    resolved = _resolve(config)
    if resolved is None:
        return None
    base, headers = resolved
    params: dict[str, Any] = {
        "limit": min(limit, 100),
        "offset": offset,
    }
    if user_id:
        params["userId"] = user_id
    if session_id:
        params["sessionId"] = session_id
    if name:
        params["name"] = name
    if from_ts:
        params["fromTimestamp"] = from_ts
    if to_ts:
        params["toTimestamp"] = to_ts
    if metadata:
        # Langfuse v3 用 metadata[<key>]=<value> 形式；httpx 的 params 会 URL-encode 方括号
        # 实测 v3 服务端能正确解析 %5B/%5D，无需特殊处理
        for k, v in metadata.items():
            params[f"metadata[{k}]"] = v
    try:
        async with _LANGFUSE_CONCURRENCY, httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/api/public/traces",
                params=params,
                headers=headers,
            )
        if resp.status_code >= 400:
            raise LangfuseError(f"list_traces failed: {resp.status_code} {resp.text[:200]}")
        return resp.json()
    except LangfuseError:
        raise
    except Exception as e:
        logger.warning(f"Langfuse list_traces error: {e}")
        return None


async def get_trace(
    trace_id: str,
    *,
    config: LangfuseConfig | None = None,
) -> dict[str, Any] | None:
    """GET /api/public/traces/{id} — 单个 trace 详情。"""
    resolved = _resolve(config)
    if resolved is None:
        return None
    base, headers = resolved
    try:
        async with _LANGFUSE_CONCURRENCY, httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/api/public/traces/{trace_id}",
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(f"Langfuse get_trace {trace_id} failed: {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        logger.warning(f"Langfuse get_trace error: {e}")
        return None


async def list_observations(
    trace_id: str | None = None,
    *,
    type: str | None = None,  # "GENERATION" / "SPAN" / "EVENT"
    from_ts: str | None = None,  # ISO 8601
    to_ts: str | None = None,
    limit: int = 100,
    offset: int = 0,
    config: LangfuseConfig | None = None,
) -> list[dict[str, Any]] | None:
    """GET /api/public/observations — 列 observation。

    按 traceId / type / 时间窗过滤，分页拉取。
    返回 list[observation]（None 表示未配置或调用失败）。
    observation 含 startTime/endTime/model/usage/calculatedTotalCost/level/traceId。
    """
    resolved = _resolve(config)
    if resolved is None:
        return None
    base, headers = resolved
    params: dict[str, Any] = {"limit": min(limit, 100), "offset": offset}
    if trace_id:
        params["traceId"] = trace_id
    if type:
        params["type"] = type
    if from_ts:
        params["fromTimestamp"] = from_ts
    if to_ts:
        params["toTimestamp"] = to_ts
    try:
        async with _LANGFUSE_CONCURRENCY, httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{base}/api/public/observations",
                params=params,
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(f"Langfuse list_observations failed: {resp.status_code}")
            return None
        data = resp.json()
        # v3 返回 {data: [...]} 或直接 list
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.warning(f"Langfuse list_observations error: {e}")
        return None


async def create_score(
    *,
    trace_id: str,
    name: str,
    value: float,
    comment: str | None = None,
    score_id: str | None = None,  # 传业务侧 id 实现幂等（重复提交覆盖同名 score）
    config: LangfuseConfig | None = None,
) -> bool:
    """POST /api/public/scores — 给 trace 打 NUMERIC score（用户反馈镜像用）。

    未配置 Langfuse 或调用失败返回 False（镜像是 fire-and-forget，调用方不重试）。
    """
    resolved = _resolve(config)
    if resolved is None:
        return False
    base, headers = resolved
    body: dict[str, Any] = {
        "traceId": trace_id,
        "name": name,
        "value": value,
        "dataType": "NUMERIC",
    }
    if comment:
        body["comment"] = comment
    if score_id:
        body["id"] = score_id
    try:
        async with _LANGFUSE_CONCURRENCY, httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{base}/api/public/scores",
                json=body,
                headers=headers,
            )
        if resp.status_code >= 400:
            logger.warning(f"Langfuse create_score failed: {resp.status_code} {resp.text[:200]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"Langfuse create_score error: {e}")
        return False
