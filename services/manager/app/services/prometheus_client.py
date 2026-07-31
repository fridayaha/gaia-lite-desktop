"""Thin Prometheus HTTP API client.

manager 用此 client 查询集群资源指标（CPU/内存/Pod 数/Top 节点/Top Pod），
供监控中心 /api/manager/observability/resources 使用。

Read-only；不需要 Prometheus SDK，直接调 REST 端点：
- /api/v1/query  实时查询
- /api/v1/query_range  范围查询（趋势图）

未配置（prometheus_url 为空）或调用失败时返回 None，调用方降级。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from pkg.common.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


def is_configured() -> bool:
    """Prometheus 是否配置了地址。"""
    return bool(settings.prometheus_url)


def _base() -> str:
    return settings.prometheus_url.rstrip("/")


async def query(promql: str) -> list[dict[str, Any]] | None:
    """即时查询。返回 data.result 列表；未配置或失败返回 None。"""
    if not is_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_base()}/api/v1/query",
                params={"query": promql},
            )
            r.raise_for_status()
            body = r.json()
            if body.get("status") != "success":
                return None
            return body.get("data", {}).get("result") or []
    except Exception as e:
        logger.warning("Prometheus query 失败 promql=%s err=%s", promql[:80], e)
        return None


async def query_range(
    promql: str, start: float, end: float, step: str
) -> list[dict[str, Any]] | None:
    """范围查询。返回 data.result 列表（每个 series 含 values 数组）；未配置或失败返回 None。"""
    if not is_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(
                f"{_base()}/api/v1/query_range",
                params={"query": promql, "start": start, "end": end, "step": step},
            )
            r.raise_for_status()
            body = r.json()
            if body.get("status") != "success":
                return None
            return body.get("data", {}).get("result") or []
    except Exception as e:
        logger.warning("Prometheus query_range 失败 promql=%s err=%s", promql[:80], e)
        return None
