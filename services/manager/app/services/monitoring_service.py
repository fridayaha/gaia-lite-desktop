"""监测中心数据源 summary helper — 供 alert_service 5 个 evaluator 复用。

每个 helper 返回评估需要的最小标量字段集（不复刻 monitoring 端点全量响应）。
数据源未配置或不可达时返回 None，evaluator 跳过该类规则。

PromQL 常量与 observability.py 同步（独立定义避免 service→api 反向依赖循环）。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import langfuse_client, litellm_client, prometheus_client
from pkg.common.config import settings

logger = logging.getLogger(__name__)

# ── PromQL 常量（与 observability.py 同步） ───────────────────
_PROM_CLUSTER_CPU_PCT = "1 - avg(rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))"
_PROM_CLUSTER_MEM_PCT = "1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)"
_PROM_TOP_NODES_DISK = 'topk(5, (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100)'
_PROM_POD_RESTARTS = 'max by (pod) (kube_pod_container_status_restarts_total{namespace="unionagents"})'

# 服务清单（与 observability.py _SERVICES 同步）
_SERVICES: list[dict[str, str]] = [
    {"name": "Manager", "kind": "both", "job": "union-manager",
     "instance": "http://manager.unionagents:8002/health"},
    {"name": "Gateway", "kind": "probe", "instance": "http://gateway.unionagents:8010/health"},
    {"name": "PostgreSQL", "kind": "probe", "instance": "postgres.unionagents:5432", "is_tcp": "1"},
    {"name": "MinIO", "kind": "probe", "instance": "minio.unionagents:9000", "is_tcp": "1"},
    {"name": "LiteLLM", "kind": "probe", "instance": "http://litellm.unionagents:4000/health/liveliness"},
    {"name": "Langfuse", "kind": "both", "job": "langfuse",
     "instance": "http://langfuse.monitoring:3000/api/public/health"},
]


# ── Prometheus 标量提取（与 observability.py 同步） ──────────
def _scalar_value(result: list[dict[str, Any]] | None) -> float | None:
    if not result:
        return None
    item = result[0]
    val = item.get("value")
    if isinstance(val, list) and len(val) >= 2:
        try:
            return float(val[1])
        except (ValueError, TypeError):
            return None
    return None


def _topk_list(result: list[dict[str, Any]] | None) -> list[tuple[dict[str, str], float]]:
    out: list[tuple[dict[str, str], float]] = []
    if not result:
        return out
    for item in result:
        labels = item.get("metric") or {}
        val = item.get("value")
        if isinstance(val, list) and len(val) >= 2:
            try:
                out.append((labels, float(val[1])))
            except (ValueError, TypeError):
                continue
    out.sort(key=lambda x: x[1], reverse=True)
    return out


# ── 4 个 summary helper ─────────────────────────────────────

async def get_resource_summary() -> dict[str, Any] | None:
    """资源监控摘要（4 个标量字段）。

    返回 {cluster_cpu_pct, cluster_memory_pct, max_disk_pct, max_pod_restarts}。
    Prometheus 未配置或不可达返回 None。
    """
    if not prometheus_client.is_configured():
        return None

    cpu_res, mem_res, disk_res, restarts_res = await asyncio.gather(
        prometheus_client.query(_PROM_CLUSTER_CPU_PCT),
        prometheus_client.query(_PROM_CLUSTER_MEM_PCT),
        prometheus_client.query(_PROM_TOP_NODES_DISK),
        prometheus_client.query(_PROM_POD_RESTARTS),
    )

    # 任一核心查询失败即视为不可达
    if cpu_res is None and mem_res is None and disk_res is None and restarts_res is None:
        return None

    cluster_cpu = _scalar_value(cpu_res)
    cluster_mem = _scalar_value(mem_res)
    disk_max = max((v for _, v in _topk_list(disk_res)), default=0.0)
    restart_max = 0
    if restarts_res:
        for item in restarts_res:
            val = item.get("value")
            if isinstance(val, list) and len(val) >= 2:
                try:
                    restart_max = max(restart_max, int(float(val[1])))
                except (ValueError, TypeError):
                    continue

    return {
        "cluster_cpu_pct": round(cluster_cpu * 100, 2) if cluster_cpu is not None else 0.0,
        "cluster_memory_pct": round(cluster_mem * 100, 2) if cluster_mem is not None else 0.0,
        "max_disk_pct": round(disk_max, 2),
        "max_pod_restarts": restart_max,
    }


async def get_service_health_summary() -> dict[str, Any] | None:
    """服务健康摘要（6 个核心服务状态）。

    返回 {services: [{name, status, p95_ms, uptime_pct}, ...]}。
    Prometheus 未配置或不可达返回 None。
    """
    if not prometheus_client.is_configured():
        return None

    avg_range = "1h"
    queries: list[tuple[str, str, str]] = []  # (service_name, query_type, promql)
    for svc in _SERVICES:
        name = svc["name"]
        if svc["kind"] == "probe" or svc["kind"] == "both":
            queries.append((name, "status",
                            f'probe_success{{instance="{svc["instance"]}"}}'))
            queries.append((name, "p95",
                            f'quantile_over_time(0.95, probe_duration_seconds{{instance="{svc["instance"]}"}}[{avg_range}]) * 1000'))
            queries.append((name, "uptime",
                            f'avg_over_time(probe_success{{instance="{svc["instance"]}"}}[{avg_range}]) * 100'))
        elif svc["kind"] == "up":
            queries.append((name, "status", f'up{{job="{svc["job"]}"}}'))
            queries.append((name, "uptime", f'avg_over_time(up{{job="{svc["job"]}"}}[{avg_range}]) * 100'))

    results = await asyncio.gather(*[prometheus_client.query(q) for _, _, q in queries])

    by_service: dict[str, dict[str, float | None]] = {svc["name"]: {} for svc in _SERVICES}
    for (name, qtype, _q), res in zip(queries, results):
        by_service[name][qtype] = _scalar_value(res)

    services: list[dict[str, Any]] = []
    for svc in _SERVICES:
        name = svc["name"]
        d = by_service[name]
        status_val = d.get("status")
        status = "ok" if (status_val is not None and status_val >= 1.0) else "down"
        p95 = d.get("p95")
        uptime = d.get("uptime")
        services.append({
            "name": name,
            "status": status,
            "p95_ms": round(p95, 2) if p95 is not None else None,
            "uptime_pct": round(uptime, 2) if uptime is not None else 0.0,
        })

    return {"services": services}


async def get_usage_summary() -> dict[str, Any] | None:
    """用量分析摘要（LiteLLM spend_logs 当日/当月/按 agent 聚合）。

    返回 {today_tokens, monthly_cost, by_agent: [{agent_id, total_tokens}, ...]}。
    LiteLLM 未配置或不可达返回 None。
    """
    if not litellm_client._base():
        return None

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    range_start = month_start - timedelta(days=2)  # 拉当月+前 2 天兜底

    try:
        resp = await litellm_client.spend_logs(
            start_date=range_start.strftime("%Y-%m-%d"),
            limit=1000,
        )
    except litellm_client.LitellmError as e:
        logger.warning(f"get_usage_summary: litellm spend_logs failed: {e}")
        return None

    logs = _extract_logs(resp)
    # 过滤失败请求（status=failure，0 token 污染聚合）
    logs = [lg for lg in logs if lg.get("status") != "failure"]

    today_tokens = 0
    monthly_tokens = 0
    monthly_cost = 0.0
    by_agent_map: dict[str, int] = {}

    for lg in logs:
        t = _parse_log_time(lg.get("startTime"))
        if t is None:
            continue
        tok = int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0)
        cost = float(lg.get("spend") or 0)
        if today_start <= t <= now:
            today_tokens += tok
        if month_start <= t <= now:
            monthly_tokens += tok
            monthly_cost += cost
        # by_agent: 直接取 log.agent_id（若 engine 透传）；否则按 api_key 前缀映射（此处简化，跳过无 agent_id 的 log）
        aid = lg.get("agent_id")
        if aid:
            by_agent_map[str(aid)] = by_agent_map.get(str(aid), 0) + tok

    by_agent = sorted(
        [{"agent_id": aid, "total_tokens": tok} for aid, tok in by_agent_map.items()],
        key=lambda x: x["total_tokens"],
        reverse=True,
    )

    return {
        "today_tokens": today_tokens,
        "monthly_tokens": monthly_tokens,
        "monthly_cost": round(monthly_cost, 2),
        "by_agent": by_agent,
    }


async def get_call_quality_summary() -> dict[str, Any] | None:
    """调用分析摘要（Langfuse traces 全局聚合，limit=100）。

    返回 {overall: {success_rate, p95_latency_ms, avg_tokens_per_request}, by_agent: [...]}。
    Langfuse 未配置或不可达返回 None。
    """
    if not langfuse_client.is_configured():
        return None

    resp = await langfuse_client.list_traces(limit=100)
    if resp is None:
        return None
    traces = resp.get("data") or []
    if not traces:
        return {"overall": _empty_overall(), "by_agent": []}

    # 并发拉 observations
    observations_list = await asyncio.gather(
        *[langfuse_client.list_observations(t.get("id")) for t in traces],
        return_exceptions=True,
    )

    # late import: observability 的 helper 是纯函数，避免顶层 circular
    from app.api.observability import _trace_latency_ms, _trace_status, _trace_token_total

    all_latencies: list[int] = []
    all_tokens: list[int] = []
    success_count = 0
    total_count = 0

    for t, obs in zip(traces, observations_list):
        if isinstance(obs, Exception):
            obs = []
        aid = t.get("userId")
        if not aid:
            continue
        latency = _trace_latency_ms(t, obs)
        tokens = _trace_token_total(t, obs)
        status = _trace_status(t, obs)
        if latency is not None:
            all_latencies.append(latency)
        all_tokens.append(tokens)
        if status == "ok":
            success_count += 1
        total_count += 1

    if total_count == 0:
        return {"overall": _empty_overall(), "by_agent": []}

    all_latencies_sorted = sorted(all_latencies)
    return {
        "overall": {
            "request_count": total_count,
            "success_rate": round(success_count / total_count, 4),
            "p50_latency_ms": _percentile(all_latencies_sorted, 50),
            "p95_latency_ms": _percentile(all_latencies_sorted, 95),
            "avg_tokens_per_request": int(sum(all_tokens) / total_count) if total_count else 0,
        },
        "by_agent": [],
    }


# ── 内部 helper（与 observability.py 同步，独立定义避免循环） ──

def _extract_logs(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("logs") or []
    return []


def _parse_log_time(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _percentile(sorted_vals: list[int], p: int) -> int:
    if not sorted_vals:
        return 0
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
    return sorted_vals[idx]


def _empty_overall() -> dict[str, Any]:
    return {
        "request_count": 0,
        "success_rate": 0,
        "p50_latency_ms": 0,
        "p95_latency_ms": 0,
        "avg_tokens_per_request": 0,
    }
