"""监控指标聚合服务。

数据来源：
- requests / tokens：LiteLLM spend_logs（按 agent 的 per-agent key 过滤），按时间桶聚合。
- cpu / memory：resource_metric_samples（controller metric_sampler 每 1min 采样落库），
  按 agent_id（agent 详情，该 agent 自己的 Pod）或 engine_instance_id（引擎实例页，整池）聚合。
  metrics-server 仅提供瞬时值，趋势必须靠采样历史。
- 对话数 / 活跃用户：spend_logs 聚合（Agent 模型无 conversation_count 列）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.models import EngineConfig, EngineType, ResourceMetricSample
from app.services import litellm_client, langfuse_client
from app.services.dify_usage_collector import build_langfuse_config, collect_dify_usage
from app.worker import client as controller_client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# range → (总时长, 桶大小)
_RANGE_CONFIG: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=5)),
    "6h": (timedelta(hours=6), timedelta(minutes=30)),
    "24h": (timedelta(hours=24), timedelta(hours=1)),
    "7d": (timedelta(days=7), timedelta(hours=6)),
}




def _instance_litellm_key(instance) -> str | None:
    """从 AgentInstance.litellm_config 取 per-instance key。"""
    cfg = instance.litellm_config or {}
    return cfg.get("key")


def _parse_start_time(raw: Any) -> datetime | None:
    """解析 LiteLLM spend log 的 startTime（ISO 字符串或 epoch 秒/毫秒）。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        # LiteLLM 用毫秒时间戳
        seconds = raw / 1000 if raw > 1e12 else raw
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            # 兼容末尾 Z
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _extract_logs(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, dict):
        data = resp.get("data")
        if data is None:
            data = resp.get("logs")
        return data or []
    if isinstance(resp, list):
        return resp
    return []




async def _fetch_instance_logs(instance, lookback: timedelta) -> list[dict[str, Any]]:
    """拉取该 AgentInstance 最近 lookback 时长的 spend_logs。无 key 时返回空。"""
    key = _instance_litellm_key(instance)
    return await _fetch_logs_by_key(key, lookback)


async def _fetch_logs_by_key(key: str | None, lookback: timedelta) -> list[dict[str, Any]]:
    """按 LiteLLM api_key 拉取最近 lookback 时长的 spend_logs。"""
    if not key:
        return []
    end = datetime.now(UTC)
    start = end - lookback
    try:
        # 注意：LiteLLM /spend/logs 不接受时间后缀（报 unconverted data remains），且同时传
        # api_key + end_date 会返回 0（该版本 quirk）。故只传 date-only start_date + api_key，
        # 靠下方客户端按 [start, end] 过滤保证时间窗口正确。
        resp = await litellm_client.spend_logs(
            start_date=start.strftime("%Y-%m-%d"),
            api_key=key,
            limit=1000,
        )
    except litellm_client.LitellmError:
        return []
    logs = _extract_logs(resp)
    return [
        lg
        for lg in logs
        if (t := _parse_start_time(lg.get("startTime"))) is not None and start <= t <= end
    ]


def _bucketize(
    logs: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    bucket: timedelta,
) -> list[dict[str, Any]]:
    """将 logs 按时间桶聚合，返回每个桶的 {timestamp, requests, prompt_tokens, completion_tokens}。"""
    points: list[dict[str, Any]] = []
    cur = start
    while cur < end:
        nxt = cur + bucket
        bucket_logs = [
            lg for lg in logs
            if (_parse_start_time(lg.get("startTime")) or start) < nxt
            and (_parse_start_time(lg.get("startTime")) or start) >= cur
        ]
        points.append({
            "timestamp": cur.isoformat(),
            "requests": len(bucket_logs),
            "prompt_tokens": sum(int(lg.get("prompt_tokens") or 0) for lg in bucket_logs),
            "completion_tokens": sum(int(lg.get("completion_tokens") or 0) for lg in bucket_logs),
        })
        cur = nxt
    return points


def _to_metric_points(
    bucketed: list[dict[str, Any]],
    field: str,
) -> list[dict[str, Any]]:
    return [{"timestamp": b["timestamp"], "value": b[field]} for b in bucketed]


def _parse_cpu_m(cpu: str) -> int:
    """CPU 字符串 → millicores（'100m'→100, '1'→1000, ''→0）。"""
    if not cpu:
        return 0
    if cpu.endswith("m"):
        return int(cpu[:-1]) if cpu[:-1].isdigit() else 0
    try:
        return int(float(cpu) * 1000)
    except ValueError:
        return 0


def _parse_mem_mi(mem: str) -> int:
    """内存字符串 → Mi（'256Mi'→256, '1Gi'→1024, ''→0）。"""
    if not mem:
        return 0
    if mem.endswith("Mi"):
        return int(mem[:-2]) if mem[:-2].isdigit() else 0
    if mem.endswith("Gi"):
        try:
            return int(float(mem[:-2]) * 1024)
        except ValueError:
            return 0
    return 0






async def _resource_history(
    db: AsyncSession,
    *,
    instance_id: str | None = None,
    resource_pool_id: str | None = None,
    range_key: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从 resource_metric_samples 取历史时序，返回 (cpu_points, mem_points)。

    instance_id 给定时聚合该实例自己的 Pod（实例详情）；
    resource_pool_id 给定时聚合整池所有 Pod（资源池页）。

    采样为 per-pod 瞬时值（1min）：先按分钟汇总成「该分钟各 Pod 用量之和」，
    再按 range 桶大小对每分钟的总量取均值，得到平滑趋势。
    无样本时返回空数组（前端优雅降级）。
    """
    delta, bucket = _RANGE_CONFIG.get(range_key, _RANGE_CONFIG["24h"])
    end = datetime.now(UTC)
    start = end - delta

    stmt = select(
        ResourceMetricSample.ts,
        ResourceMetricSample.cpu_m,
        ResourceMetricSample.memory_mi,
    ).where(ResourceMetricSample.ts >= start)
    if instance_id:
        stmt = stmt.where(ResourceMetricSample.instance_id == instance_id)
    elif resource_pool_id:
        stmt = stmt.where(ResourceMetricSample.resource_pool_id == resource_pool_id)
    else:
        return [], []
    stmt = stmt.order_by(ResourceMetricSample.ts)

    result = await db.execute(stmt)
    rows = result.all()

    if not rows:
        return [], []

    # 按分钟汇总成「该分钟各 Pod 用量之和」
    minute_totals: dict[datetime, tuple[int, int]] = {}
    for ts, cpu_m, mem_mi in rows:
        minute = ts.replace(second=0, microsecond=0)
        c, m = minute_totals.get(minute, (0, 0))
        minute_totals[minute] = (c + (cpu_m or 0), m + (mem_mi or 0))

    cpu_points: list[dict[str, Any]] = []
    mem_points: list[dict[str, Any]] = []
    cur = start
    while cur < end:
        nxt = cur + bucket
        bucket_vals = [v for mn, v in minute_totals.items() if cur <= mn < nxt]
        if bucket_vals:
            n = len(bucket_vals)
            cpu_mean = sum(v[0] for v in bucket_vals) // n
            mem_mean = sum(v[1] for v in bucket_vals) // n
            cpu_points.append({"timestamp": cur.isoformat(), "value": cpu_mean})
            mem_points.append({"timestamp": cur.isoformat(), "value": mem_mean})
        cur = nxt
    return cpu_points, mem_points




async def _instance_own_pods(instance) -> list[dict[str, Any]]:
    """取该 AgentInstance 自己的 Pod（池内按 agent_id 过滤）。

    Pod 标签 agent.unionagents/agent-id={instance_id}；list_instance_pods 列整池后按 instance.id 筛。
    controller 不可达返回空。
    """
    if not instance.resource_pool_id:
        return []
    try:
        data = await controller_client.list_instance_pods(str(instance.resource_pool_id))
    except controller_client.ControllerError:
        return []
    pods = data.get("items", []) if isinstance(data, dict) else []
    iid = str(instance.id)
    return [p for p in pods if str(p.get("agent_id", "")) == iid]


async def _instance_resource_request(instance) -> dict[str, int]:
    """取该实例自己 Pod 的资源请求合计（millicores / Mi），作为图表参考线。"""
    pods = await _instance_own_pods(instance)
    return {
        "cpu_m": sum(_parse_cpu_m(p.get("cpu", "")) for p in pods),
        "memory_mi": sum(_parse_mem_mi(p.get("memory", "")) for p in pods),
    }


async def _instance_shared_count(db: AsyncSession, instance) -> int:
    """该资源池下绑定的 PUBLISHED 实例数（判断独占/共享）。"""
    if not instance.resource_pool_id:
        return 1
    from app.models import AgentInstance, AgentStatus
    count = await db.scalar(
        select(func.count(AgentInstance.id)).where(
            AgentInstance.resource_pool_id == instance.resource_pool_id,
            AgentInstance.status == AgentStatus.PUBLISHED,
        )
    )
    return int(count or 0)




async def build_pool_metrics(
    db: AsyncSession, pool_id: str, range_key: str
) -> dict[str, Any]:
    """资源池页监控：整池所有 Pod 的 CPU/内存历史趋势 + 资源请求合计 + Pod 数。"""
    delta, bucket = _RANGE_CONFIG.get(range_key, _RANGE_CONFIG["24h"])

    cpu_points, mem_points = await _resource_history(
        db, resource_pool_id=pool_id, range_key=range_key
    )

    # 资源请求合计 + Pod 数（整池）
    resource_request = {"cpu_m": 0, "memory_mi": 0}
    pod_count = 0
    try:
        data = await controller_client.list_instance_pods(pool_id)
    except controller_client.ControllerError:
        data = None
    pods = data.get("items", []) if isinstance(data, dict) else []
    pod_count = len(pods)
    resource_request = {
        "cpu_m": sum(_parse_cpu_m(p.get("cpu", "")) for p in pods),
        "memory_mi": sum(_parse_mem_mi(p.get("memory", "")) for p in pods),
    }

    return {
        "cpu": cpu_points,
        "memory": mem_points,
        "resourceRequest": resource_request,
        "podCount": pod_count,
    }


async def build_instance_metrics(
    db: AsyncSession, instance, range_key: str
) -> dict[str, Any]:
    """AgentInstance 详情监控：requests/tokens 来自 LiteLLM（时序），
    cpu/memory 来自 resource_metric_samples（该实例自己 Pod 的历史趋势）。

    与 build_agent_metrics 对应；instance.litellm_config 提供 per-instance key。
    Dify 外接实例走专用路径（_build_dify_instance_metrics）：无 Pod → cpu/memory 返回 []，
    requests/tokens 从 collect_dify_usage 拉 DifyTraceDetail 分桶。
    """
    # Dify 外接实例走专用路径：dify_config 同时有 app_id + base_url 即判定为外接模式
    # （等价于 build_instance_overview 的入口判定，避免 lazy load instance.definition）
    dify_cfg = instance.dify_config if isinstance(instance.dify_config, dict) else {}
    is_external_dify = bool(dify_cfg.get("app_id")) and bool(dify_cfg.get("base_url"))
    if is_external_dify:
        return await _build_dify_instance_metrics(db, instance, range_key)

    delta, bucket = _RANGE_CONFIG.get(range_key, _RANGE_CONFIG["24h"])
    end = datetime.now(UTC)
    start = end - delta

    logs = await _fetch_instance_logs(instance, delta)
    bucketed = _bucketize(logs, start, end, bucket)

    cpu_points, mem_points = await _resource_history(
        db, instance_id=str(instance.id), range_key=range_key
    )
    resource_request = await _instance_resource_request(instance)
    shared_count = await _instance_shared_count(db, instance)

    return {
        "cpu": cpu_points,
        "memory": mem_points,
        "requests": _to_metric_points(bucketed, "requests"),
        "tokens": {
            "input": _to_metric_points(bucketed, "prompt_tokens"),
            "output": _to_metric_points(bucketed, "completion_tokens"),
        },
        "resourceRequest": resource_request,
        "attribution": {
            "exclusive": True,
            "sharedAgentCount": shared_count,
            "resourcePoolId": str(instance.resource_pool_id) if instance.resource_pool_id else None,
            "keyPresent": bool((instance.litellm_config or {}).get("key")),
            "logsFetched": len(logs),
        },
    }


async def build_instance_overview(db: AsyncSession, instance) -> dict[str, Any]:
    """AgentInstance 概览统计：对话数、Token、活跃用户、7d 对话趋势。

    Dify 外接实例走专用路径（_build_dify_instance_overview）：从 collect_dify_usage
    拿 DifyTraceDetail，按 metadata.app_id 反查（绕过 Dify #37824 bug），不调
    Langfuse list_traces(user_id=instance.id)（Dify trace user_id 不是 instance.id），
    不调 _fetch_instance_logs（Dify 不走 LiteLLM 代理，spend_logs 无 Dify 记录）。

    Hermes 实例：
    conversationCount + activeUsers 均优先取自 Langfuse traces（按 user_id=instance.id 过滤）：
    - conversationCount = meta.totalItems（1 trace = 1 次对话，与首页 Top5 同源）
    - activeUsers = traces 的 distinct sessionId（活跃会话数）
    Langfuse 未配置 / 调用失败 / traces 无 sessionId 时 fallback 到 spend_logs：
    - conversationCount = len(logs)（LLM 调用次数，偏高）
    - activeUsers = distinct session_id
    totalTokens / conversationTrend 始终来自 spend_logs（计费维度，更准）。
    注：Langfuse 单次最多取 100 条 trace，超过时 activeUsers 为近似值（下限）。
    """
    # Dify 外接实例走专用路径：dify_config 同时有 app_id + base_url 即判定为外接模式
    # （等价于 API 层 _is_external_dify：definition.engine_type==DIFY && base_url 非空，
    # 但 service 层访问 instance.definition 在 async session 下需 eager load，
    # 改用 dify_config 自带字段判定，避免 lazy load 触发 MissingGreenlet）
    dify_cfg = instance.dify_config if isinstance(instance.dify_config, dict) else {}
    is_external_dify = bool(dify_cfg.get("app_id")) and bool(dify_cfg.get("base_url"))
    if is_external_dify:
        return await _build_dify_instance_overview(db, instance, timedelta(days=30))

    lookback = timedelta(days=30)
    logs = await _fetch_instance_logs(instance, lookback)

    total_tokens = sum(int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0) for lg in logs)
    # fallback 值：Langfuse 不可用时按 spend_logs session_id 去重
    active_users = len({lg.get("session_id") for lg in logs if lg.get("session_id")})

    # 7d 对话趋势（按天）
    end = datetime.now(UTC)
    start = end - timedelta(days=7)
    daily = _bucketize(logs, start, end, timedelta(days=1))
    trend = [{"timestamp": b["timestamp"][:10], "value": b["requests"]} for b in daily]

    # conversationCount + activeUsers：优先 Langfuse traces
    conversation_count: int = len(logs)
    if langfuse_client.is_configured():
        now = datetime.now(UTC)
        from_ts = (now - lookback).isoformat()
        to_ts = now.isoformat()
        r = await langfuse_client.list_traces(
            user_id=str(instance.id), from_ts=from_ts, to_ts=to_ts, limit=100
        )
        if r is not None:
            meta = r.get("meta") or {}
            total = meta.get("totalItems")
            if total is not None:
                try:
                    conversation_count = int(total)
                except (ValueError, TypeError):
                    pass  # 留 fallback 值 len(logs)
            # activeUsers：traces 的 distinct sessionId（与 conversationCount 同源）
            traces = r.get("data") or []
            session_ids = {t.get("sessionId") for t in traces if t.get("sessionId")}
            if session_ids:
                active_users = len(session_ids)

    return {
        "conversationCount": conversation_count,
        "totalTokens": total_tokens,
        "activeUsers": active_users,
        "conversationTrend": trend,
    }


def _empty_overview() -> dict[str, Any]:
    """Dify 分支 EngineConfig 未配 / collect 失败时返回的空概览。"""
    return {
        "conversationCount": 0,
        "totalTokens": 0,
        "activeUsers": 0,
        "conversationTrend": [],
    }


async def _resolve_dify_engine(
    db: AsyncSession, instance
) -> tuple[EngineConfig, dict[str, Any]] | None:
    """从 instance.dify_config 解析外接 Dify 的 EngineConfig + agent_meta_map。

    返回 None 表示：不是 Dify 外接（缺 app_id 或 base_url）/ 按 base_url 找不到
    EngineConfig / EngineConfig 未配齐 Langfuse 凭据。供 _build_dify_instance_overview
    和 _build_dify_instance_metrics 共用，避免两个调用点重复查询逻辑。
    """
    dify_cfg = instance.dify_config if isinstance(instance.dify_config, dict) else {}
    app_id = dify_cfg.get("app_id")
    base_url = dify_cfg.get("base_url")
    if not app_id or not base_url:
        return None

    # 按 base_url 匹配 EngineConfig（同 workspace 的 admin 凭据 + Langfuse 凭据）
    ec_res = await db.execute(
        select(EngineConfig).where(
            EngineConfig.engine_type == EngineType.DIFY,
            EngineConfig.base_url == base_url,
        )
    )
    ec = ec_res.scalars().first()
    if ec is None or not build_langfuse_config(ec):
        return None  # EngineConfig 未配 Langfuse 凭据

    agent_meta_map = {
        str(app_id): {
            "agent_id": str(instance.id),
            "name": instance.name or str(instance.id),
            "group_id": str(instance.group_id) if instance.group_id else "",
        }
    }
    return ec, agent_meta_map


async def _build_dify_instance_overview(
    db: AsyncSession, instance, lookback: timedelta
) -> dict[str, Any]:
    """Dify 外接实例概览：从 collect_dify_usage 拿 DifyTraceDetail 算 4 卡 + 7d 趋势。

    与 Hermes 分支的差异：
    - 不调 Langfuse list_traces(user_id=instance.id)（Dify trace user_id 不是 instance.id，
      collect_dify_usage 内部已按 metadata.app_id 过滤）
    - 不调 _fetch_instance_logs（Dify 不走 LiteLLM 代理，spend_logs 无 Dify 记录）
    - conversationCount = len(details)（1 trace = 1 次对话，message_trace 1:1，
      workflow 1:N，与 /usage 端点同口径）
    - activeUsers = distinct details.session_id
    - totalTokens = sum(d.total_tokens)
    - conversationTrend = _bucketize(details 转 dict, 7d, 1d)
    """
    resolved = await _resolve_dify_engine(db, instance)
    if resolved is None:
        return _empty_overview()
    ec, agent_meta_map = resolved

    days = max(1, int(lookback.total_seconds() // 86400))
    try:
        details = await collect_dify_usage(ec, agent_meta_map, days=days)
    except Exception:
        return _empty_overview()

    total_tokens = sum(d.total_tokens for d in details)
    active_users = len({d.session_id for d in details if d.session_id})
    conversation_count = len(details)

    # 7d 趋势（按天），复用 _bucketize（与数据源解耦）
    end = datetime.now(UTC)
    start = end - timedelta(days=7)
    logs_for_bucket = [
        {
            "startTime": d.timestamp,
            "prompt_tokens": d.prompt_tokens,
            "completion_tokens": d.completion_tokens,
        }
        for d in details
    ]
    daily = _bucketize(logs_for_bucket, start, end, timedelta(days=1))
    trend = [{"timestamp": b["timestamp"][:10], "value": b["requests"]} for b in daily]

    return {
        "conversationCount": conversation_count,
        "totalTokens": total_tokens,
        "activeUsers": active_users,
        "conversationTrend": trend,
    }


def _empty_metrics() -> dict[str, Any]:
    """Dify 分支 EngineConfig 未配 / collect 失败时返回的空指标。"""
    return {
        "cpu": [],
        "memory": [],
        "requests": [],
        "tokens": {"input": [], "output": []},
        "resourceRequest": {"cpu_m": 0, "memory_mi": 0},
        "attribution": {
            "exclusive": True,
            "sharedAgentCount": 0,
            "resourcePoolId": None,
            "keyPresent": False,
            "logsFetched": 0,
        },
    }


async def _build_dify_instance_metrics(
    db: AsyncSession, instance, range_key: str
) -> dict[str, Any]:
    """Dify 外接实例监控：requests/tokens 从 collect_dify_usage 拿 DifyTraceDetail 分桶。

    与 Hermes 分支的差异：
    - 不调 _fetch_instance_logs（Dify 不走 LiteLLM 代理，spend_logs 无 Dify 记录）
    - 不调 _resource_history / _instance_resource_request（Dify 外接无 Pod，cpu/memory 返回 []）
    - 不调 _instance_shared_count（Dify 无 Pod 概念，sharedAgentCount 静态 0）
    - days = max(1, delta 天数)，range_key=1h 时 days=1 仍拉 24h traces，
      _bucketize 自然过滤区间外 trace
    """
    delta, bucket = _RANGE_CONFIG.get(range_key, _RANGE_CONFIG["24h"])
    end = datetime.now(UTC)
    start = end - delta

    resolved = await _resolve_dify_engine(db, instance)
    if resolved is None:
        return _empty_metrics()
    ec, agent_meta_map = resolved

    days = max(1, int(delta.total_seconds() // 86400))
    try:
        details = await collect_dify_usage(ec, agent_meta_map, days=days)
    except Exception:
        return _empty_metrics()

    logs_for_bucket = [
        {
            "startTime": d.timestamp,
            "prompt_tokens": d.prompt_tokens,
            "completion_tokens": d.completion_tokens,
        }
        for d in details
    ]
    bucketed = _bucketize(logs_for_bucket, start, end, bucket)

    return {
        "cpu": [],
        "memory": [],
        "requests": _to_metric_points(bucketed, "requests"),
        "tokens": {
            "input": _to_metric_points(bucketed, "prompt_tokens"),
            "output": _to_metric_points(bucketed, "completion_tokens"),
        },
        "resourceRequest": {"cpu_m": 0, "memory_mi": 0},
        "attribution": {
            "exclusive": True,
            "sharedAgentCount": 0,
            "resourcePoolId": None,
            "keyPresent": False,
            "logsFetched": len(details),
        },
    }


async def build_top_agents(db: AsyncSession, limit: int = 5) -> list[dict[str, Any]]:
    """热门实例排行（按近 30 天对话次数）：一次 spend_logs 按 api_key 分组映射到实例。

    spend_logs 逐行数据的 api_key 字段 = LiteLLM key_id（token_id 哈希），与
    AgentInstance.litellm_config.key_id 对应。
    注意：只传 start_date（不传 end_date），否则 LiteLLM /spend/logs 触发按天聚合模式丢 token。
    返回 [{agent_id, name, conversation_count, total_tokens}]（agent_id 语义=instance_id），
    按 conversation_count 降序，不足 limit 用 0 对话实例补齐。
    """
    from app.models import AgentInstance, AgentStatus

    # 1. 查所有 PUBLISHED 实例，建 key_id → {id, name} 映射
    res = await db.execute(
        select(AgentInstance.id, AgentInstance.name, AgentInstance.litellm_config)
        .where(AgentInstance.status == AgentStatus.PUBLISHED)
    )
    key_to_agent: dict[str, dict[str, Any]] = {}
    all_agents: list[dict[str, Any]] = []
    for aid, name, cfg in res.all():
        all_agents.append({"agent_id": str(aid), "name": name})
        litellm_cfg = cfg or {}
        key_id = litellm_cfg.get("key_id")
        if key_id:
            key_to_agent[key_id] = {"agent_id": str(aid), "name": name}

    # 2. 一次 spend_logs（只传 start_date=30d 前，避免 start+end 触发聚合丢 token）
    end = datetime.now(UTC)
    start = end - timedelta(days=30)
    try:
        resp = await litellm_client.spend_logs(
            start_date=start.strftime("%Y-%m-%d"), limit=1000
        )
    except litellm_client.LitellmError:
        resp = []
    logs = _extract_logs(resp)

    # 3. 按 api_key（=key_id）分组：对话数 + token（仅计 30d 内）
    stats: dict[str, dict[str, Any]] = {}  # agent_id → {name, count, tokens}
    for lg in logs:
        t = _parse_start_time(lg.get("startTime"))
        if t is None or not (start <= t <= end):
            continue
        kid = lg.get("api_key")
        ag = key_to_agent.get(kid) if kid else None
        if not ag:
            continue
        aid = ag["agent_id"]
        s = stats.setdefault(aid, {"name": ag["name"], "count": 0, "tokens": 0})
        s["count"] += 1
        s["tokens"] += int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0)

    # 4. 按对话数降序，不足 limit 用 0 对话 agent 补齐
    result = [
        {"agent_id": aid, "name": s["name"], "conversation_count": s["count"], "total_tokens": s["tokens"]}
        for aid, s in sorted(stats.items(), key=lambda x: x[1]["count"], reverse=True)
    ]
    ranked_ids = {r["agent_id"] for r in result}
    for a in all_agents:
        if len(result) >= limit:
            break
        if a["agent_id"] not in ranked_ids:
            result.append({**a, "conversation_count": 0, "total_tokens": 0})
    return result[:limit]
