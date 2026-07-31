"""Observability API — 监控中心数据聚合。

5 个 endpoint：
- GET /traces        链路列表（Langfuse list_traces）
- GET /traces/{id}   trace 详情（get_trace + list_observations）
- GET /usage         用量分析（LiteLLM spend_logs + 按 agent 聚合）
- GET /quality       调用分析（Langfuse 客户端聚合：成功率/延迟/成本）
- GET /alerts        异常告警（错误 trace + 阈值告警）

数据源策略：链路/质量/异常走 Langfuse，用量走 LiteLLM。不在 manager DB 重新存
trace/usage 数据，manager 只做聚合 API。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from app.core.auth import get_current_user, is_platform_admin, require_platform_admin
from app.models import (
    AlertChannel,
    AlertEvent,
    AlertRule,
    EngineConfig,
    OperationLog,
    User,
    channel_rule_subscriptions,
)
from app.schemas import (
    AlertChannelCreate,
    AlertChannelResponse,
    AlertChannelUpdate,
    AlertEventResponse,
    AlertRuleResponse,
    AlertRuleUpdate,
)
from app.services import langfuse_client, litellm_client, metrics_service
from app.services import prometheus_client
from app.services.audit_service import log_operation
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings
from pkg.common.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


# ── helpers ────────────────────────────────────────────────


def _langfuse_url() -> str:
    """返回浏览器可访问的 Langfuse 地址：优先 external_url，空则 fallback 到 base_url。"""
    return settings.langfuse_external_url or settings.langfuse_base_url


def _grafana_url() -> str:
    """返回浏览器可访问的 Grafana 地址。空则返回空串，前端隐藏外链。"""
    return settings.grafana_external_url or ""


def _parse_ts(ts: str | None) -> datetime | None:
    """解析 ISO 8601 时间戳。"""
    if not ts:
        return None
    try:
        # 兼容带 Z 和不带 Z 的格式
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _obs_latency_ms(obs: dict[str, Any]) -> int | None:
    """从 observation 的 startTime/endTime 算延迟（毫秒）。"""
    start = _parse_ts(obs.get("startTime"))
    end = _parse_ts(obs.get("endTime"))
    if not start or not end:
        return None
    delta = (end - start).total_seconds() * 1000
    return int(delta) if delta >= 0 else None


def _obs_ttft_ms(obs: dict[str, Any]) -> int | None:
    """从 observation 的 completionStartTime/startTime 算 TTFT（首 token 时延，毫秒）。

    Langfuse v3 GENERATION observation 有 completionStartTime 字段（gateway 在 SSE
    首 chunk 时通过 SDK 的 completion_start_time 参数写入）。非流式响应该字段为 null。
    """
    start = _parse_ts(obs.get("startTime"))
    completion = _parse_ts(obs.get("completionStartTime"))
    if not start or not completion:
        return None
    delta = (completion - start).total_seconds() * 1000
    return int(delta) if delta >= 0 else None


def _trace_latency_breakdown(
    trace: dict[str, Any], observations: list[dict[str, Any]] | None
) -> tuple[int | None, int | None, int | None]:
    """返回 (e2e_ms, ttft_ms, avg_incremental_ms)。

    - e2e_ms: 端到端延迟 = trace.latency * 1000（v3 顶层字段）
    - ttft_ms: 首 token 时延 = 首个 GENERATION 的 completionStartTime - startTime
    - avg_incremental_ms: 平均增量时延 = (e2e - ttft) / max(output_tokens - 1, 1)
      含义：从首 token 到末 token 的平均每 token 间隔（仅流式响应有 ttft 时有效）

    无 ttft（非流式响应）时 avg_incremental_ms 为 None。
    """
    e2e = _trace_latency_ms(trace, observations)
    if not observations:
        return e2e, None, None

    # 找首个 GENERATION observation 的 TTFT
    ttft: int | None = None
    output_tokens = 0
    for o in observations:
        if not isinstance(o, dict):
            continue
        if o.get("type") == "GENERATION":
            ttft = _obs_ttft_ms(o)
            _, out = _obs_token_breakdown(o)
            output_tokens += out

    if ttft is None or e2e is None:
        return e2e, ttft, None

    # 平均增量：从首 token 到末 token 的总时间 / (token 数 - 1)
    # output_tokens <= 1 时无法算增量（至少要 2 个 token 才有"间隔"）
    if output_tokens <= 1:
        return e2e, ttft, None
    gen_phase = e2e - ttft
    if gen_phase < 0:
        return e2e, ttft, None
    avg_inc = int(gen_phase / (output_tokens - 1))
    return e2e, ttft, avg_inc


def _obs_tokens(obs: dict[str, Any]) -> int:
    """从 observation.usage 取 total_tokens。"""
    usage = obs.get("usage") or {}
    if not isinstance(usage, dict):
        return 0
    return int(usage.get("total_tokens") or usage.get("total") or 0)


def _obs_token_breakdown(obs: dict[str, Any]) -> tuple[int, int]:
    """从 observation 取 (input_tokens, output_tokens)。"""
    usage = obs.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0
    inp = int(usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("input") or 0)
    out = int(usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("output") or 0)
    return inp, out


def _trace_status(trace: dict[str, Any], observations: list[dict[str, Any]] | None) -> str:
    """判断 trace 状态：ok / error。

    error 条件：任一 observation level=ERROR，或 trace.output 含 "error"。
    """
    if observations:
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            if str(obs.get("level", "")).upper() == "ERROR":
                return "error"
    output = trace.get("output")
    if isinstance(output, str) and "error" in output.lower():
        return "error"
    if isinstance(output, dict) and (
        output.get("error") or output.get("error_message")
    ):
        return "error"
    return "ok"


def _trace_latency_ms(trace: dict[str, Any], observations: list[dict[str, Any]] | None) -> int | None:
    """trace 总延迟（毫秒）。

    v3 API 顶层有 latency 字段（单位：秒，float），优先用之；
    无则回退到 max(observation.endTime) - min(observation.startTime)。
    """
    top_latency = trace.get("latency")
    if isinstance(top_latency, (int, float)):
        return int(top_latency * 1000)
    if not observations:
        return None
    starts = [_parse_ts(o.get("startTime")) for o in observations if isinstance(o, dict)]
    ends = [_parse_ts(o.get("endTime")) for o in observations if isinstance(o, dict)]
    starts = [s for s in starts if s]
    ends = [e for e in ends if e]
    if not starts or not ends:
        return None
    delta = (max(ends) - min(starts)).total_seconds() * 1000
    return int(delta) if delta >= 0 else None


def _trace_token_total(trace: dict[str, Any], observations: list[dict[str, Any]] | None) -> int:
    """trace 总 token = sum(observation.usage.total_tokens)。

    v3 list_traces 不返回 usage，需要 fetch observations；无 observations 时返回 0。
    """
    if not observations:
        return 0
    return sum(_obs_tokens(o) for o in observations if isinstance(o, dict))


def _trace_token_breakdown(trace: dict[str, Any], observations: list[dict[str, Any]] | None) -> tuple[int, int]:
    """trace 总 (input, output) token = sum 各 observation 的 (prompt, completion)。

    用于列表 tooltip 展示 token 构成：input 含 system prompt + tools + 历史，output 是模型回复。
    """
    if not observations:
        return 0, 0
    inp = out = 0
    for o in observations:
        if not isinstance(o, dict):
            continue
        i, o2 = _obs_token_breakdown(o)
        inp += i
        out += o2
    return inp, out


def _trace_observation_count(observations: list[dict[str, Any]] | None) -> int:
    """trace 下 observation 数量（含 GENERATION/SPAN/EVENT）。"""
    if not observations:
        return 0
    return sum(1 for o in observations if isinstance(o, dict))


def _trace_cost(observations: list[dict[str, Any]] | None) -> float | None:
    """聚合 trace 下所有 observation 的 calculatedTotalCost（Langfuse v3 顶层无 totalCost 字段）。

    Langfuse 返回的 calculatedTotalCost 是 USD；乘 ``settings.spend_usd_to_cny``
    转成 CNY 返回，与 /usage endpoint 保持一致。

    自托管 Langfuse 默认未配模型定价表时，所有 observation 的 calculatedTotalCost=0，
    此时返回 None（前端显示 — 而非 ¥0.0000，避免误导）。
    """
    if not observations:
        return None
    total_usd = 0.0
    has_non_zero = False
    for o in observations:
        if not isinstance(o, dict):
            continue
        c = o.get("calculatedTotalCost")
        if c is None:
            continue
        try:
            v = float(c)
        except (TypeError, ValueError):
            continue
        total_usd += v
        if v > 0:
            has_non_zero = True
    if not has_non_zero:
        return None
    rate = float(getattr(settings, "spend_usd_to_cny", 7.0) or 7.0)
    return round(total_usd * rate, 6)


def _percentile(sorted_vals: list[int], p: int) -> int:
    """算 p 分位数（p=50/95/99）。sorted_vals 必须已升序排序。"""
    if not sorted_vals:
        return 0
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
    return sorted_vals[idx]


# ── LiteLLM 模糊匹配（trace 精确成本） ────────────────────────


def _model_matches(obs_model: str | None, log_model: str | None) -> bool:
    """模型名包含匹配（Hermes 传给 LiteLLM 的可能是 model_group，spend_log 里是 deployment model）。

    例如 observation.model="deepseek-chat"，spend_log.model="deepseek/deepseek-chat" 视为匹配。
    任一为空返回 False。
    """
    if not obs_model or not log_model:
        return False
    a, b = obs_model.lower(), log_model.lower()
    return a in b or b in a


def _score_obs_log_match(
    obs_start: datetime,
    obs_prompt: int,
    obs_completion: int,
    log: dict[str, Any],
) -> tuple[int, int] | None:
    """给一条 observation 和一条 spend_log 算匹配分数，返回 (time_diff_s, token_diff)。

    time_diff = |log.startTime - obs.startTime| 秒
    token_diff = |log.prompt - obs.prompt| + |log.completion - obs.completion|

    返回 None 表示模型不匹配或时间差过大（> 5min）。
    token_diff 不设硬阈值 —— Hermes 报告的 obs.usage 跟 LiteLLM log 的 prompt_tokens
    可能差异巨大（Hermes 把对话历史累加进 prompt，LiteLLM 只算实际发给上游的 token，
    且 LiteLLM 理解 prompt caching，cached token 可能不计入 prompt_tokens）。
    故 token_diff 仅作为多候选时的 tiebreaker，不作为过滤条件。
    """
    log_start = _parse_ts(log.get("startTime"))
    if log_start is None:
        return None
    time_diff = int(abs((log_start - obs_start).total_seconds()))
    if time_diff > 300:  # 5min
        return None
    log_prompt = int(log.get("prompt_tokens") or 0)
    log_completion = int(log.get("completion_tokens") or 0)
    token_diff = abs(log_prompt - obs_prompt) + abs(log_completion - obs_completion)
    return (time_diff, token_diff)


def _match_observations_to_logs(
    observations: list[dict[str, Any]],
    logs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """对每个 GENERATION observation 找最佳匹配的 spend_log，返回匹配的 log 列表。

    匹配维度：(model, startTime ±5min, prompt_tokens ±50, completion_tokens ±50)。
    每个 log 最多匹配一个 observation（避免重复计费）。
    """
    # 按 startTime 升序排（先匹配早的 observation）
    gen_obs = []
    for o in observations:
        if not isinstance(o, dict) or o.get("type") != "GENERATION":
            continue
        start = _parse_ts(o.get("startTime"))
        if start is None:
            continue
        prompt, completion = _obs_token_breakdown(o)
        gen_obs.append((start, prompt, completion, o.get("model")))

    gen_obs.sort(key=lambda x: x[0])
    used_logs: set[int] = set()  # log 的 id() 标记已用
    matched: list[dict[str, Any]] = []

    for obs_start, obs_prompt, obs_completion, obs_model in gen_obs:
        best_score: tuple[int, int] | None = None
        best_log: dict[str, Any] | None = None
        best_log_id: int | None = None
        for log in logs:
            if not isinstance(log, dict):
                continue
            if not _model_matches(obs_model, log.get("model")):
                continue
            log_id = id(log)
            if log_id in used_logs:
                continue
            score = _score_obs_log_match(obs_start, obs_prompt, obs_completion, log)
            if score is None:
                continue
            if best_score is None or score < best_score:
                best_score = score
                best_log = log
                best_log_id = log_id
        if best_log is not None and best_log_id is not None:
            matched.append(best_log)
            used_logs.add(best_log_id)

    return matched


async def _resolve_agent_key_id(db: AsyncSession, agent_id: str) -> str | None:
    """agent_id (=Langfuse trace.userId) → AgentInstance.litellm_config.key_id。

    不存在或未配置 LiteLLM key 返回 None。
    """
    from app.models import AgentInstance

    res = await db.execute(
        select(AgentInstance.litellm_config).where(AgentInstance.id == agent_id)
    )
    row = res.first()
    if not row:
        return None
    # SQLAlchemy Row 不是 tuple/list（isinstance 返回 False），直接用 row[0] 取首列
    cfg = row[0]
    if not isinstance(cfg, dict):
        return None
    return cfg.get("key_id")


async def _litellm_cost_for_trace(
    db: AsyncSession,
    trace: dict[str, Any] | None,
    observations: list[dict[str, Any]] | None,
) -> float | None:
    """从 LiteLLM spend_log 模糊匹配 trace 的精确成本（CNY）。

    Hermes 不透传 trace_id 给 LiteLLM，无法精确关联。改用 (api_key, model, startTime, tokens)
    五元组模糊匹配。LiteLLM 的 spend 理解 prompt cache（cache_read_input_token_cost = input 的 1/10），
    精度比 Langfuse calculatedTotalCost 高。

    返回 None 表示未匹配或无法解析，调用方应回退到 _trace_cost（Langfuse 聚合）。
    """
    if not trace or not observations:
        return None
    agent_id = trace.get("userId") or trace.get("user_id")
    if not agent_id:
        return None
    key_id = await _resolve_agent_key_id(db, str(agent_id))
    if not key_id:
        return None

    # 时间窗：trace 最早 observation - 5min 到最晚 + 5min
    starts = [_parse_ts(o.get("startTime")) for o in observations if isinstance(o, dict)]
    starts = [s for s in starts if s]
    if not starts:
        # 回退到 trace.createdAt
        ct = _parse_ts(trace.get("createdAt") or trace.get("timestamp"))
        if ct is None:
            return None
        starts = [ct]
    earliest = min(starts) - timedelta(minutes=5)
    latest = max(starts) + timedelta(minutes=5)

    # LiteLLM /spend/logs 只接 date-only。**只传 start_date 不传 end_date** ——
    # 同时传 start+end 会触发按天聚合模式，返回的 log 字段（api_key/model/tokens）全为 None
    # （metrics_service.build_top_agents 注释里提过这个坑）。
    # 客户端按精确时间窗 + api_key 过滤。
    start_date = (earliest - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        resp = await litellm_client.spend_logs(
            start_date=start_date, limit=1000
        )
    except litellm_client.LitellmError:
        return None
    logs_raw = (
        resp.get("data") if isinstance(resp, dict) else resp
    ) if resp else []
    if not isinstance(logs_raw, list):
        logs_raw = []

    # 客户端过滤：api_key=key_id（前缀匹配） + 时间窗内
    # LiteLLM /spend/logs 返回的 api_key 是 key_id 的前 20 字符（截断显示，防泄露），
    # 需用 startswith 匹配，不能直接 ==。
    candidate_logs: list[dict[str, Any]] = []
    for log in logs_raw:
        if not isinstance(log, dict):
            continue
        log_api_key = log.get("api_key")
        if not log_api_key or not key_id.startswith(str(log_api_key)):
            continue
        log_start = _parse_ts(log.get("startTime"))
        if log_start is None:
            continue
        if earliest <= log_start <= latest:
            candidate_logs.append(log)

    if not candidate_logs:
        return None

    matched = _match_observations_to_logs(observations, candidate_logs)
    if not matched:
        return None

    total_usd = 0.0
    for log in matched:
        try:
            total_usd += float(log.get("spend") or 0)
        except (TypeError, ValueError):
            continue
    if total_usd <= 0:
        return None
    rate = float(getattr(settings, "spend_usd_to_cny", 7.0) or 7.0)
    return round(total_usd * rate, 6)


async def _resolve_agent_ids(
    db: AsyncSession,
    *,
    agent_id: str | None,
    user_group_id: str | None,
) -> list[str] | None:
    """根据筛选条件解析 agent_id 集合。None 表示无筛选（全部 agents）。

    映射：
    - user_group_id → 该 group 下的 AgentInstance.ids
    - agent_id（同时传时）与上面取交集

    同时传 agent_id + user_group_id 时取交集（agent 必须在该 group 下）。

    注：终端用户 ID（enduser_id）不在此函数处理——它筛的是 trace
    metadata.enduser_id，需在拉完 Langfuse traces 后用
    ``_filter_traces_by_enduser`` 客户端过滤。
    """
    from app.models import AgentInstance, UserGroup, user_group_members

    if not agent_id and not user_group_id:
        return None

    candidate_sets: list[set[str]] = []

    if user_group_id:
        res = await db.execute(
            select(AgentInstance.id).where(AgentInstance.group_id == user_group_id)
        )
        candidate_sets.append({str(r) for r in res.scalars().all()})

    if agent_id:
        candidate_sets.append({agent_id})

    if not candidate_sets:
        return None

    result = candidate_sets[0]
    for s in candidate_sets[1:]:
        result &= s
    return list(result) if result else []


def _filter_traces_by_enduser(
    traces: list[dict[str, Any]],
    enduser_id: str | None,
) -> list[dict[str, Any]]:
    """客户端过滤 Langfuse traces：metadata.enduser_id == enduser_id。

    Langfuse v3 REST 不支持 metadata 服务端过滤（只支持 userId/sessionId/
    name/timestamps），只能拉完再过滤。enduser_id 为 None 时不过滤（返回原列表）。
    """
    if not enduser_id:
        return traces
    return [
        t for t in traces
        if (t.get("metadata") or {}).get("enduser_id") == enduser_id
    ]


def _filter_traces_by_channel_type(
    traces: list[dict[str, Any]],
    channel_type: str | None,
) -> list[dict[str, Any]]:
    """客户端过滤 Langfuse traces：metadata.channel_type == channel_type。

    channel_type 取值：web（终端门户）/ wecom / feishu / dingtalk / wecom_bot。
    为 None 时不过滤（返回原列表）。
    """
    if not channel_type:
        return traces
    return [
        t for t in traces
        if (t.get("metadata") or {}).get("channel_type") == channel_type
    ]


def _filter_out_internal_hermes_traces(
    traces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """排除 Hermes langfuse 插件写的内部 trace（name == "Hermes turn"）。

    链路追踪列表只展示 Gateway 写的对外 trace（chat_completion / run），
    Hermes 内层 trace 通过 trace 详情页 hermes-correlation 端点关联展示。
    Langfuse v3 REST 的 name 参数是 eq 过滤，不支持 neq，只能拉完客户端排除。
    """
    return [t for t in traces if t.get("name") != "Hermes turn"]


def _parse_iso_to_unix(iso_str: Any) -> float | None:
    """ISO 8601 时间戳 → unix 秒。None / 非字符串 / 解析失败 → None。

    Langfuse 的 trace.timestamp / observation.startTime 都是 ISO 8601（Z 结尾）。
    """
    if not isinstance(iso_str, str) or not iso_str:
        return None
    try:
        # Z 结尾 → +00:00，datetime.fromisoformat 才能解析
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt.timestamp()
    except Exception:
        return None


def _collect_subtree_observations(
    observations: list[dict[str, Any]],
    root_id: str,
) -> list[dict[str, Any]]:
    """从 observations 列表里收集 root_id 自己 + 它所有后代 observation。

    Hermes merged trace 场景下，同一 trace 挂了多个 name="Hermes turn" 的子 turn
    observation，每个子 turn 下挂自己的 LLM call / tool call。本函数按
    parentObservationId 链递归收集 root_id 子树下的所有 observation。

    返回顺序：root 在前，子按 startTime 升序。root 不在 observations 列表里
    （跨 trace 引用）→ 返回空 list。
    """
    if not root_id:
        return []
    children_map: dict[str, list[dict[str, Any]]] = {}
    root_obs: dict[str, Any] | None = None
    for o in observations:
        if not isinstance(o, dict):
            continue
        oid = o.get("id")
        if oid == root_id:
            root_obs = o
            continue
        parent = o.get("parentObservationId")
        if parent:
            children_map.setdefault(parent, []).append(o)
    if root_obs is None:
        return []
    result: list[dict[str, Any]] = [root_obs]
    # BFS 收集所有后代
    queue: list[str] = [root_id]
    while queue:
        next_queue: list[str] = []
        for parent_id in queue:
            kids = children_map.get(parent_id, [])
            kids_sorted = sorted(kids, key=lambda x: x.get("startTime") or "")
            result.extend(kids_sorted)
            next_queue.extend(k.get("id") for k in kids_sorted if k.get("id"))
        queue = next_queue
    return result


async def _resolve_agent_names(
    db: AsyncSession, agent_ids: list[str]
) -> dict[str, str]:
    """agent_id 列表 → {agent_id: name} 映射。空列表返回 {}，不查 DB。"""
    if not agent_ids:
        return {}
    from app.models import AgentInstance

    res = await db.execute(
        select(AgentInstance.id, AgentInstance.name).where(
            AgentInstance.id.in_(agent_ids)
        )
    )
    return {str(aid): name for aid, name in res.all()}


async def _resolve_group_names(
    db: AsyncSession, group_ids: list[str]
) -> dict[str, str]:
    """group_id 列表 → {group_id: name} 映射。空列表返回 {}，不查 DB。"""
    if not group_ids:
        return {}
    from app.models import UserGroup

    res = await db.execute(
        select(UserGroup.id, UserGroup.name).where(UserGroup.id.in_(group_ids))
    )
    return {str(gid): name for gid, name in res.all()}


async def _resolve_key_ids_for_agents(
    db: AsyncSession, agent_ids: list[str] | None
) -> dict[str, dict[str, str]]:
    """agent_id 列表 → {key_id: {agent_id, name, group_id}} 映射。

    agent_ids=None：返回所有 PUBLISHED agents 的映射（无筛选，全部 agents）。
    agent_ids=[]：返回空 dict（筛选后无 agent）。
    agent_ids=["id1","id2"]：返回这些 agents 的映射。

    用于过滤 LiteLLM spend_logs：只保留 api_key（=key_id 前 20 字符）属于这些 key_id 的 log。
    group_id 用于 by_group 聚合（直接 FK 列，无 join）。
    """
    from app.models import AgentInstance, AgentStatus

    stmt = select(
        AgentInstance.id, AgentInstance.name, AgentInstance.litellm_config, AgentInstance.group_id
    ).where(AgentInstance.status == AgentStatus.PUBLISHED)
    if agent_ids is not None:
        if not agent_ids:
            return {}
        stmt = stmt.where(AgentInstance.id.in_(agent_ids))
    res = await db.execute(stmt)
    mapping: dict[str, dict[str, str]] = {}
    for aid, name, cfg, gid in res.all():
        if isinstance(cfg, dict) and cfg.get("key_id"):
            mapping[cfg["key_id"]] = {
                "agent_id": str(aid),
                "name": name or str(aid),
                "group_id": str(gid) if gid else "",
            }
    return mapping


async def _resolve_dify_agents(
    db: AsyncSession, agent_ids: list[str] | None, user_group_id: str | None = None
) -> dict[str, dict[str, str]]:
    """查 PUBLISHED Dify agent，返回 {app_id: {agent_id, name, group_id}} 映射。

    用于 dify_usage_collector.collect_dify_usage 反查 trace.metadata.app_id → agent_id。
    agent_ids=None：返回所有 PUBLISHED Dify agent；[]：返回空 dict。
    user_group_id：可选按用户组过滤（直接 FK 列，无 join）。
    """
    from app.models import AgentInstance, AgentStatus

    stmt = select(
        AgentInstance.id, AgentInstance.name, AgentInstance.dify_config, AgentInstance.group_id
    ).where(AgentInstance.status == AgentStatus.PUBLISHED)
    if agent_ids is not None:
        if not agent_ids:
            return {}
        stmt = stmt.where(AgentInstance.id.in_(agent_ids))
    if user_group_id:
        stmt = stmt.where(AgentInstance.group_id == user_group_id)
    res = await db.execute(stmt)
    mapping: dict[str, dict[str, str]] = {}
    for aid, name, cfg, gid in res.all():
        if isinstance(cfg, dict) and cfg.get("app_id"):
            mapping[str(cfg["app_id"])] = {
                "agent_id": str(aid),
                "name": name or str(aid),
                "group_id": str(gid) if gid else "",
            }
    return mapping


async def _fetch_dify_trace_details(
    db: AsyncSession,
    start_dt: datetime,
    end_dt: datetime,
    agent_ids: list[str] | None,
    user_group_id: str | None,
) -> list[dict[str, Any]]:
    """拉所有 DIFY EngineConfig 的 Dify trace 明细，合并成 fake log list。

    流程：
    1. 查所有 PUBLISHED Dify agent，构造 {app_id: {agent_id, name, group_id}} 映射
    2. 查所有 DIFY EngineConfig（配了 Langfuse 凭据的）
    3. 对每个 EngineConfig 调 collect_dify_usage 拉明细
    4. 按 start_dt/end_dt 过滤明细时间窗（collector 内部按 now-days 拉取，可能超出 end_dt）
    5. 把 DifyTraceDetail 转成 fake spend_log dict（带 agent_id/group_id/model/startTime/spend）
    6. 若 agent_ids 非空，按 agent_id 过滤

    返回 fake log list，供 get_usage 合并到 filtered_logs。
    """
    from app.models import EngineConfig, EngineType
    from app.services.dify_usage_collector import collect_dify_usage

    agent_meta_map = await _resolve_dify_agents(db, agent_ids, user_group_id)
    if not agent_meta_map:
        return []

    stmt = select(EngineConfig).where(EngineConfig.engine_type == EngineType.DIFY)
    ec_res = await db.execute(stmt)
    engine_configs = ec_res.scalars().all()

    days = max(1, int((end_dt - start_dt).total_seconds() // 86400) + 1)

    fake_logs: list[dict[str, Any]] = []
    for ec in engine_configs:
        if not (ec.langfuse_host and ec.langfuse_public_key and ec.langfuse_secret_key_encrypted):
            continue
        try:
            details = await collect_dify_usage(ec, agent_meta_map, days=days)
        except Exception as e:
            logger.warning(f"collect_dify_usage for EngineConfig {ec.id} failed: {e}")
            continue
        for d in details:
            t = _parse_ts(d.timestamp)
            if t is None or not (start_dt <= t <= end_dt):
                continue
            if agent_ids is not None and d.agent_id not in agent_ids:
                continue
            fake_logs.append({
                "agent_id": d.agent_id,
                "agent_name": d.agent_name,
                "group_id": d.group_id,
                "model": d.model,
                "prompt_tokens": d.prompt_tokens,
                "completion_tokens": d.completion_tokens,
                "api_key": "",  # 空，_aggregate_by_* 走 fallback
                "session_id": d.session_id,  # trace.sessionId，build_instance_overview 复用
                "spend": d.cost_usd,  # USD
                "startTime": d.timestamp,
            })
    return fake_logs


# ── 1. 链路追踪 ────────────────────────────────────────────


@router.get("/traces")
async def list_traces(
    agent_id: str | None = Query(None, description="按 agent_id（=Langfuse userId）过滤"),
    enduser_id: str | None = Query(None, description="按终端用户 ID（=trace.metadata.enduser_id）过滤"),
    channel_type: str | None = Query(None, description="按渠道过滤（web/wecom/feishu/dingtalk/wecom_bot）"),
    session_id: str | None = Query(None, description="按会话 ID（=Langfuse sessionId）过滤"),
    from_ts: str | None = Query(None, description="起始时间 ISO 8601，如 2026-06-30T00:00:00Z"),
    to_ts: str | None = Query(None, description="结束时间 ISO 8601"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    _: User = Depends(get_current_user),
):
    """链路追踪列表。

    数据源：Langfuse GET /api/public/traces。
    返回 {items: [{id, name, agent_id, session_id, enduser_id, channel_type, created_at,
    latency_ms, token_total, status, metadata}], total, langfuse_url}。
    Langfuse 未配置时返回 {items: [], total: 0, langfuse_configured: false}。

    enduser_id / channel_type 过滤：Langfuse v3 REST 不支持 metadata 服务端过滤，拉完客户端过滤。
    """
    if not langfuse_client.is_configured():
        return {
            "items": [],
            "total": 0,
            "langfuse_configured": False,
            "langfuse_url": _langfuse_url(),
        }
    resp = await langfuse_client.list_traces(
        user_id=agent_id,
        session_id=session_id,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        offset=offset,
    )
    if resp is None:
        return {
            "items": [],
            "total": 0,
            "langfuse_configured": True,
            "langfuse_url": _langfuse_url(),
        }
    data = resp.get("data") or []
    meta = resp.get("meta") or {}
    # 客户端过滤 metadata.enduser_id 和 metadata.channel_type
    # （v3 REST 不支持服务端 metadata 过滤）
    data = _filter_traces_by_enduser(data, enduser_id)
    data = _filter_traces_by_channel_type(data, channel_type)
    # 排除 Hermes langfuse 插件写的内部 trace（name == "Hermes turn"），
    # 这些 trace 通过 trace 详情页 hermes-correlation 端点关联展示
    data = _filter_out_internal_hermes_traces(data)
    # v3 list_traces 只返回 observation ID 列表，不含 usage；并发 fetch 各 trace 的 observations 算 token
    obs_list = await asyncio.gather(
        *[langfuse_client.list_observations(t.get("id")) for t in data],
        return_exceptions=True,
    )
    items = []
    for t, obs in zip(data, obs_list):
        if isinstance(obs, Exception):
            obs = []
        token_in, token_out = _trace_token_breakdown(t, obs)
        e2e, ttft, avg_inc = _trace_latency_breakdown(t, obs)
        metadata = t.get("metadata") or {}
        items.append({
            "id": t.get("id"),
            "name": t.get("name"),
            "agent_id": t.get("userId"),
            "session_id": t.get("sessionId"),
            "enduser_id": metadata.get("enduser_id"),
            "channel_type": metadata.get("channel_type"),
            "created_at": t.get("createdAt") or t.get("timestamp"),
            "latency_ms": e2e,
            "ttft_ms": ttft,
            "avg_incremental_ms": avg_inc,
            "token_total": _trace_token_total(t, obs),
            "token_input": token_in,
            "token_output": token_out,
            "observation_count": _trace_observation_count(obs),
            "status": _trace_status(t, obs),
            "cost": _trace_cost(obs),
            "metadata": metadata,
        })
    filtered = bool(enduser_id or channel_type)
    return {
        "items": items,
        # 注：Hermes turn 总是被客户端排除，total 仍是 meta.totalItems（含 Hermes turn 计数），
        # 用户翻页时偶有不满页是可接受的——优于让链路追踪列表出现内部 trace
        "total": len(items) if filtered else meta.get("totalItems", len(items)),
        "langfuse_configured": True,
        "langfuse_url": _langfuse_url(),
    }


@router.get("/traces/{trace_id}")
async def get_trace_detail(
    trace_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """trace 详情：trace 本体 + observations 列表 + 精确成本。

    成本优先用 LiteLLM spend_log 模糊匹配（理解 prompt cache，精度高），
    匹配不到回退到 Langfuse calculatedTotalCost 聚合。
    """
    if not langfuse_client.is_configured():
        return {"trace": None, "observations": [], "langfuse_configured": False}
    trace, observations = await asyncio.gather(
        langfuse_client.get_trace(trace_id),
        langfuse_client.list_observations(trace_id),
    )
    if isinstance(trace, dict):
        # 优先 LiteLLM 精确成本（含 cache 折扣），回退 Langfuse calculatedTotalCost
        litellm_cost = await _litellm_cost_for_trace(db, trace, observations)
        trace["totalCost"] = litellm_cost if litellm_cost is not None else _trace_cost(observations)
    return {
        "trace": trace,
        "observations": observations or [],
        "langfuse_configured": True,
        "langfuse_url": _langfuse_url(),
    }


@router.get("/traces/{trace_id}/hermes-correlation")
async def get_hermes_correlation(
    trace_id: str,
    _: User = Depends(get_current_user),
):
    """查询与 Gateway trace 关联的 Hermes 内层 trace + observations。

    关联键（软关联）：
      - 确定性 trace_id（主路径）: Hermes 插件 trace_id =
        sha256(f"{session_id}::{session_id}")[:32]，由 Gateway trace.sessionId
        本地复算后直取。不能依赖 Hermes trace 行的 sessionId 字段——长寿命
        profile 进程会把上一 run 的 session 写进下一 run 的 trace 行
        （"错位一格"污染，2026-07-22 定位），按 sessionId 过滤会关联不上
      - list_traces(sessionId=...)（兜底路径）: 覆盖 task_id != session_id
        的 seed 及 sessionId 未污染场景
      - last_user_message_hash: Gateway 写 trace.metadata.last_user_message_hash，
        Hermes 子 turn observation.input 含同一份用户消息，admin 端哈希后比对
      - gateway_request_time: 用于多条同消息（"继续" "可以" 等）候选子 turn
        时按 startTime 选最近的，避免误关联到同 session 同文本的其他请求

    Hermes 的 Langfuse plugin 用 ``client.create_trace_id(seed=f"{session_id}::{task_id}")``
    生成 trace_id。Hermes 的 /v1/runs handler 把 ``effective_task_id = session_id or run_id``，
    所以 Portal 同一会话每次请求 seed 都一样 → Langfuse SDK 算出同一 trace_id →
    **后续请求的 LLM observation 全部合并追加到同一条 Hermes trace 上**，trace
    自身的 timestamp 不会更新（保持首次创建时间），trace.input 也只反映首次请求。

    因此不能再用 trace.input 哈希做关联——会误关联到同 session 同文本的不同请求
    （例如 "继续" 发了多次，trace.input 永远是第一次的"继续"）。

    实际数据形态（merged trace 场景）：父 Hermes turn trace 下挂 N 条
    name="Hermes turn" type=CHAIN 的子 turn observation，每个子 turn 下面挂自己
    的 LLM call / tool call。本端点改为：
      1. 在候选 hermes trace 的 observations 里找子 turn observation
      2. 用子 turn.input 哈希匹配 Gateway hash
      3. 多条匹配按 startTime 跟 gateway_request_time 最近选 + 加 ±10min 时间窗
      4. 只返回匹配子 turn + 它下面的子树 observations（递归收集）

    单 turn trace 场景（trace 没有子 turn observation，task_id != session_id）：
    trace.input 就是当前请求，按 trace.input 匹配 + trace.timestamp 时间窗。

    /v1/chat/completions 场景：Gateway trace.metadata.path == "v1/chat/completions"
    时，Hermes 只代理 LLM 调用，不进 agent loop，**不会写 Hermes turn 内部 trace**。
    早返回 reason="direct_llm_call"，避免把"本应没有内部 trace"误显示为关联失败。

    返回 ``{hermes_trace, observations, matched_sub_turn_id, reason, ...}``。
    """
    if not langfuse_client.is_configured():
        return {
            "hermes_trace": None,
            "observations": [],
            "langfuse_configured": False,
            "reason": "langfuse_not_configured",
        }

    from pkg.common.langfuse_correlation import (
        hash_last_user_message,
        hermes_session_trace_id,
    )

    # Hermes merged trace 场景：trace timestamp 是首次创建时间（可能数小时前），
    # 但子 turn startTime 是该请求实际处理时间——子 turn startTime 应在
    # gateway_request_time 附近。±10min 容忍 LLM 长生成 + 时钟漂移。
    TIME_WINDOW_SECONDS = 600

    # 1. 取 Gateway trace → 提取 session_id + last_user_message_hash + gateway_request_time
    gateway_trace = await langfuse_client.get_trace(trace_id)
    if not isinstance(gateway_trace, dict):
        return {
            "hermes_trace": None,
            "observations": [],
            "langfuse_configured": True,
            "langfuse_url": _langfuse_url(),
            "reason": "gateway_trace_not_found",
        }
    session_id = gateway_trace.get("sessionId")
    metadata = gateway_trace.get("metadata") or {}
    gw_hash = metadata.get("last_user_message_hash")
    gw_req_time_raw = metadata.get("gateway_request_time")
    gw_req_time: float | None
    if isinstance(gw_req_time_raw, (int, float)):
        gw_req_time = float(gw_req_time_raw)
    else:
        gw_req_time = None

    # /v1/chat/completions 走直接 LLM 代理路径，Hermes 不进 agent loop，
    # 不会写 "Hermes turn" 内部 trace——早返回，避免无意义的 langfuse 查询
    # 和把"未关联"误显示为关联失败。
    gateway_path = metadata.get("path")
    if gateway_path == "v1/chat/completions":
        return {
            "hermes_trace": None,
            "observations": [],
            "langfuse_configured": True,
            "langfuse_url": _langfuse_url(),
            "reason": "direct_llm_call",
            "gateway_metadata": metadata,
        }

    if not session_id or not gw_hash:
        return {
            "hermes_trace": None,
            "observations": [],
            "langfuse_configured": True,
            "langfuse_url": _langfuse_url(),
            "reason": "no_correlation_keys_in_gateway_trace",
            "gateway_metadata": metadata,
        }

    # 2. list_traces(sessionId=<session_id>, name="Hermes turn") → 该 session 下
    #    所有 Hermes plugin 写的 trace（兜底路径，覆盖 task_id != session_id 的
    #    seed 及 sessionId 未污染场景）
    resp = await langfuse_client.list_traces(
        session_id=session_id,
        name="Hermes turn",
        limit=50,
    )
    if resp is None:
        return {
            "hermes_trace": None,
            "observations": [],
            "langfuse_configured": True,
            "langfuse_url": _langfuse_url(),
            "reason": "list_traces_failed",
            "gateway_metadata": metadata,
        }
    candidates = resp.get("data") or []

    # 3. 并发拉每条候选 trace 的完整详情 + observations。
    #    候选集 = list_traces 结果 ∪ 确定性 trace_id 直取（主路径）：
    #    插件 trace_id = sha256(f"{session_id}::{session_id}")[:32] 可本地复算
    #    （/v1/runs 下 effective_task_id = session_id）。必须补此路径——
    #    插件写的 trace 行 sessionId 字段存在"错位一格"污染（长寿命 profile
    #    进程残留上一 run 的会话上下文，trace 行创建时把上一 run 的 session
    #    写进 sessionId），按 sessionId 过滤除进程重启后首 run 外全部
    #    关联不上（2026-07-22 定位，种子哈希与真实 trace id 三次验证一致）。
    candidate_ids = [c.get("id") for c in candidates if isinstance(c, dict) and c.get("id")]
    det_id = hermes_session_trace_id(session_id)
    if det_id not in candidate_ids:
        det_trace = await langfuse_client.get_trace(det_id)
        if isinstance(det_trace, dict):
            candidate_ids.append(det_id)

    async def _fetch_pair(cid: str) -> tuple[dict | None, list | None]:
        trace, obs = await asyncio.gather(
            langfuse_client.get_trace(cid),
            langfuse_client.list_observations(cid),
        )
        return trace, obs

    pairs = await asyncio.gather(
        *[_fetch_pair(cid) for cid in candidate_ids],
        return_exceptions=True,
    )

    # 4. 在每个 trace 的 observations 里找子 turn observation（name="Hermes turn"
    #    + type=CHAIN），用子 turn.input 哈希匹配 + startTime 时间窗选最近。
    #    单 turn trace（无子 turn observation）fallback 用 trace.input + timestamp。
    best_match: dict[str, Any] | None = None  # {trace, obs_list, sub_turn_id, distance}

    for entry in pairs:
        if isinstance(entry, Exception) or not isinstance(entry, tuple):
            continue
        c_trace, c_obs = entry
        if not isinstance(c_trace, dict) or not isinstance(c_obs, list):
            continue

        # 找子 turn observation
        sub_turns = [
            o for o in c_obs
            if isinstance(o, dict)
            and o.get("name") == "Hermes turn"
            and o.get("type") == "CHAIN"
        ]

        if sub_turns:
            # merged trace：子 turn.input 哈希匹配 + startTime 时间窗
            for st in sub_turns:
                st_hash = hash_last_user_message(st.get("input"))
                if st_hash != gw_hash:
                    continue
                if gw_req_time is None:
                    # 无 gateway_request_time → 退化为"最近 startTime"
                    # （没有时间锚点无法过滤，仍按 startTime DESC 取最新匹配项）
                    st_start = _parse_iso_to_unix(st.get("startTime")) or 0.0
                    distance = 0.0
                    if best_match is None or st_start > (best_match.get("sub_turn_start") or 0):
                        best_match = {
                            "trace": c_trace,
                            "obs_list": c_obs,
                            "sub_turn_id": st.get("id"),
                            "sub_turn_start": st_start,
                            "distance": distance,
                            "reason": "sub_turn_hash_matched",
                        }
                    continue
                st_start = _parse_iso_to_unix(st.get("startTime"))
                if st_start is None:
                    continue
                distance = abs(st_start - gw_req_time)
                if distance > TIME_WINDOW_SECONDS:
                    continue
                if best_match is None or distance < best_match["distance"]:
                    best_match = {
                        "trace": c_trace,
                        "obs_list": c_obs,
                        "sub_turn_id": st.get("id"),
                        "sub_turn_start": st_start,
                        "distance": distance,
                        "reason": "sub_turn_hash_matched",
                    }
        else:
            # 单 turn trace：trace.input 匹配 + trace.timestamp 时间窗
            c_hash = hash_last_user_message(c_trace.get("input"))
            if c_hash != gw_hash:
                continue
            if gw_req_time is None:
                if best_match is None:
                    best_match = {
                        "trace": c_trace,
                        "obs_list": c_obs,
                        "sub_turn_id": None,
                        "sub_turn_start": 0.0,
                        "distance": 0.0,
                        "reason": "trace_input_hash_matched",
                    }
                continue
            c_ts = _parse_iso_to_unix(c_trace.get("timestamp"))
            if c_ts is None:
                continue
            distance = abs(c_ts - gw_req_time)
            if distance > TIME_WINDOW_SECONDS:
                continue
            if best_match is None or distance < best_match["distance"]:
                best_match = {
                    "trace": c_trace,
                    "obs_list": c_obs,
                    "sub_turn_id": None,
                    "sub_turn_start": c_ts,
                    "distance": distance,
                    "reason": "trace_input_hash_matched",
                }

    if best_match is None:
        return {
            "hermes_trace": None,
            "observations": [],
            "langfuse_configured": True,
            "langfuse_url": _langfuse_url(),
            "reason": "no_matching_hermes_trace",
            "gateway_metadata": metadata,
            "candidate_count": len(candidate_ids),
        }

    # 5. 子 turn 场景：递归收集子 turn 自己 + 它下面的子树 observations
    sub_turn_id = best_match.get("sub_turn_id")
    if sub_turn_id:
        return_observations = _collect_subtree_observations(
            best_match["obs_list"], sub_turn_id
        )
    else:
        # 单 turn 场景：返回 trace 全部 observations
        return_observations = best_match["obs_list"]

    return {
        "hermes_trace": best_match["trace"],
        "observations": return_observations,
        "matched_sub_turn_id": sub_turn_id,
        "langfuse_configured": True,
        "langfuse_url": _langfuse_url(),
        "reason": best_match["reason"],
        "gateway_metadata": metadata,
        "candidate_count": len(candidate_ids),
    }


# ── 2. 用量分析 ────────────────────────────────────────────


@router.get("/usage")
async def get_usage(
    days: int = Query(30, ge=1, le=90, description="趋势/agent 聚合时间窗（from_ts/to_ts 优先）"),
    agent_id: str | None = Query(None, description="按智能体 ID 过滤"),
    enduser_id: str | None = Query(None, description="按终端用户 ID（chat 请求体 user 字段）过滤"),
    user_group_id: str | None = Query(None, description="按用户组过滤"),
    from_ts: str | None = Query(None, description="起始时间 ISO 8601（与 to_ts 同时传时优先于 days）"),
    to_ts: str | None = Query(None, description="结束时间 ISO 8601"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """用量分析：Token 消耗 + 成本，按 agent/model 维度 + 日趋势。

    数据源：LiteLLM spend_logs（按 api_key→key_id 映射到 agent）。
    所有维度（by_agent / by_model / trend / today_tokens / monthly_tokens）按筛选条件过滤。

    注：by_model 改用 spend_logs 客户端按 model 字段聚合，不再调 spend_models ——
    spend_models 同时传 start+end 会触发 LiteLLM daily 聚合模式丢 token（见
    [[litellm-api-quirks]]）。

    enduser_id 过滤：主路径是 spend_logs.user（若 engine 透传 user 给 LiteLLM）；
    若 spend_logs.user 全空（engine 没透传），走兜底——用 Langfuse trace
    metadata.enduser_id 反查该 enduser 调用过的 agent_ids，再按这些 agents 过滤 spend_logs。
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 时间窗：from_ts/to_ts 优先，否则用 days
    if from_ts and to_ts:
        start_dt = _parse_ts(from_ts) or (now - timedelta(days=days))
        end_dt = _parse_ts(to_ts) or now
    else:
        start_dt = now - timedelta(days=days)
        end_dt = now
    range_start = start_dt

    # agent_ids 筛选（user_group_id/agent_id → 集合）
    agent_ids = await _resolve_agent_ids(
        db, agent_id=agent_id, user_group_id=user_group_id
    )
    # key_id → {agent_id, name} 映射（agent_ids=None 时返回全部 PUBLISHED agents）
    key_to_agent_meta = await _resolve_key_ids_for_agents(db, agent_ids)

    # enduser_id 兜底：用 Langfuse 反查该 enduser 调用过的 agent_ids
    # 主路径（spend_logs.user 过滤）在下面 logs 拉取时传 user=enduser_id
    # 兜底路径：若主路径拉不到数据，用 Langfuse 反查的 agent_ids 过滤
    enduser_agent_ids: set[str] | None = None
    if enduser_id and langfuse_client.is_configured():
        try:
            lf_traces = await _fetch_traces_for_agents(agent_ids, from_ts, to_ts)
            enduser_agent_ids = {
                t.get("userId") for t in _filter_traces_by_enduser(lf_traces, enduser_id)
                if t.get("userId")
            }
        except Exception:
            enduser_agent_ids = None

    # 一次拉 range 内全部逐行 logs（只传 start_date，避免 daily 聚合 bug）
    # 主路径：若 enduser_id 有值，传 user=enduser_id 让 LiteLLM 服务端过滤
    try:
        resp = await litellm_client.spend_logs(
            start_date=(range_start - timedelta(days=1)).strftime("%Y-%m-%d"),
            user=enduser_id,
            limit=1000,
        )
    except litellm_client.LitellmError:
        resp = []
    logs = _extract_logs(resp)

    # 客户端按 (时间窗, api_key ∈ key_ids) 过滤
    # agent_ids is not None 表示有筛选（即便空列表，也要过滤到无 log）
    has_agent_filter = agent_ids is not None
    filtered_logs: list[dict[str, Any]] = []
    for lg in logs:
        t = _parse_log_time(lg.get("startTime"))
        if t is None or not (start_dt <= t <= end_dt):
            continue
        if has_agent_filter:
            log_api_key = lg.get("api_key")
            if not log_api_key:
                continue
            # log.api_key 是 key_id 前 20 字符（截断显示，见 [[litellm-api-quirks]]）
            matched = next(
                (kid for kid in key_to_agent_meta if kid.startswith(str(log_api_key))),
                None,
            )
            if not matched:
                continue
        filtered_logs.append(lg)

    # 兜底路径：若主路径（spend_logs.user 过滤）拉不到数据，但 enduser_id 有值
    # 且 Langfuse 反查到了 agent_ids，则按这些 agent_ids 重新过滤（不带 user）
    if not filtered_logs and enduser_id and enduser_agent_ids:
        try:
            resp_fb = await litellm_client.spend_logs(
                start_date=(range_start - timedelta(days=1)).strftime("%Y-%m-%d"),
                limit=1000,
            )
        except litellm_client.LitellmError:
            resp_fb = []
        logs_fb = _extract_logs(resp_fb)
        for lg in logs_fb:
            t = _parse_log_time(lg.get("startTime"))
            if t is None or not (start_dt <= t <= end_dt):
                continue
            log_api_key = lg.get("api_key")
            if not log_api_key:
                continue
            matched = next(
                (kid for kid in key_to_agent_meta if kid.startswith(str(log_api_key))),
                None,
            )
            if not matched:
                continue
            # 只保留属于 enduser 调用过的 agents 的 log
            meta = key_to_agent_meta.get(matched, {})
            if meta.get("agent_id") in enduser_agent_ids:
                filtered_logs.append(lg)

    # 注入 Dify 外接用量（fake log）：从 Langfuse 拉 Dify trace 明细，
    # 按 agent_id 反查合并到 filtered_logs。by_agent/by_group/by_model/trend/today/monthly
    # 全部基于 merged filtered_logs，自然含 Dify 部分。
    # 一个智能体只能配一种引擎类型（Hermes 或 Dify），Dify 和 Hermes trace 不重叠，不会双计。
    try:
        dify_fake_logs = await _fetch_dify_trace_details(
            db, start_dt, end_dt, agent_ids, user_group_id
        )
        filtered_logs.extend(dify_fake_logs)
    except Exception as e:
        logger.warning(f"fetch_dify_trace_details failed, Dify usage not merged: {e}")

    # 过滤失败请求：LiteLLM spend_logs 会记录 status="failure" 的 log（如用户输错 model 名
    # 导致 ProxyModelNotFoundError），这些请求 0 token 0 cost 但 model 字段可能是任意字符串，
    # 不过滤会污染 by_model（出现不存在的 model 名）。Dify fake log 无 status 字段，不受影响。
    filtered_logs = [lg for lg in filtered_logs if lg.get("status") != "failure"]

    # today / monthly tokens（基于 filtered_logs）
    today_tokens = 0
    monthly_tokens = 0
    monthly_cost = 0.0
    for lg in filtered_logs:
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

    # by_agent：按 api_key 分组映射到 agent
    by_agent = _aggregate_by_agent(filtered_logs, key_to_agent_meta)

    # by_model：客户端按 model 字段聚合（修复 token=0 bug）
    by_model = _aggregate_by_model(filtered_logs)

    # by_group：按 group_id 聚合 + DB enrich group name
    by_group = _aggregate_by_group(filtered_logs, key_to_agent_meta)
    gids = [g["group_id"] for g in by_group if g.get("group_id")]
    gname_map = await _resolve_group_names(db, gids)
    for g in by_group:
        g["name"] = gname_map.get(g["group_id"], "—")

    # trend：按日聚合
    trend = _aggregate_daily_tokens(filtered_logs, start_dt, end_dt)

    return {
        "today_tokens": today_tokens,
        "monthly_tokens": monthly_tokens,
        "monthly_cost": round(monthly_cost * getattr(settings, "spend_usd_to_cny", 7.0), 2),
        "by_agent": by_agent,
        "by_model": by_model,
        "by_group": by_group,
        "trend": trend,
        "litellm_url": settings.litellm_base_url,
    }


def _extract_logs(resp: Any) -> list[dict[str, Any]]:
    """从 spend_logs 响应提取 logs 数组（兼容 dict.data / dict.logs / list 三种）。"""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("logs") or []
    return []


def _parse_log_time(ts: str | None) -> datetime | None:
    """spend_logs 的 startTime 是 ISO 字符串。"""
    return _parse_ts(ts)


def _aggregate_by_agent(
    logs: list[dict[str, Any]], key_to_agent_meta: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """按 api_key 分组映射到 agent，返回 [{agent_id, name, conversation_count, total_tokens}]。

    key_to_agent_meta: {key_id: {agent_id, name}} —— 调用方预先查 DB 得到。
    log.api_key 是 key_id 前 20 字符，用 startswith 匹配。

    Fallback：log 带 `agent_id` 字段时直取（Dify trace 注入的 fake log 走这条路径，
    不查 key_to_agent_meta）。
    """
    stats: dict[str, dict[str, Any]] = {}
    for lg in logs:
        aid_direct = lg.get("agent_id")
        if aid_direct:
            aid = str(aid_direct)
            name = lg.get("agent_name") or aid
        else:
            log_api_key = lg.get("api_key")
            if not log_api_key:
                continue
            matched_kid = next(
                (kid for kid in key_to_agent_meta if kid.startswith(str(log_api_key))),
                None,
            )
            if not matched_kid:
                continue
            meta = key_to_agent_meta[matched_kid]
            aid = meta["agent_id"]
            name = meta["name"]
        s = stats.setdefault(
            aid, {"agent_id": aid, "name": name, "conversation_count": 0, "total_tokens": 0}
        )
        s["conversation_count"] += 1
        s["total_tokens"] += int(lg.get("prompt_tokens") or 0) + int(
            lg.get("completion_tokens") or 0
        )
    # 按对话数降序
    return sorted(stats.values(), key=lambda x: x["conversation_count"], reverse=True)


def _aggregate_by_group(
    logs: list[dict[str, Any]], key_to_agent_meta: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """按 group_id 聚合，返回 [{group_id, conversation_count, total_tokens, total_cost}]。

    group_name 不在此 enrich：key_to_agent_meta 只到 agent 维度，group name 在 user_groups 表，
    由调用方 _resolve_group_names 单独查 DB 后填 name 字段。

    Fallback：log 带 `group_id` 字段时直取（Dify trace 注入的 fake log 走这条路径）。
    """
    stats: dict[str, dict[str, Any]] = {}
    for lg in logs:
        gid_direct = lg.get("group_id")
        if gid_direct:
            gid = str(gid_direct)
        else:
            log_api_key = lg.get("api_key")
            if not log_api_key:
                continue
            matched_kid = next(
                (kid for kid in key_to_agent_meta if kid.startswith(str(log_api_key))),
                None,
            )
            if not matched_kid:
                continue
            meta = key_to_agent_meta[matched_kid]
            gid = meta.get("group_id") or ""
            if not gid:
                continue
        s = stats.setdefault(
            gid,
            {
                "group_id": gid,
                "name": "",
                "conversation_count": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
            },
        )
        s["conversation_count"] += 1
        s["total_tokens"] += int(lg.get("prompt_tokens") or 0) + int(
            lg.get("completion_tokens") or 0
        )
        s["total_cost"] += float(lg.get("spend") or 0)
    # 按 token 数降序
    return sorted(stats.values(), key=lambda x: x["total_tokens"], reverse=True)


def _aggregate_by_model(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 model 字段聚合，返回 [{model, total_tokens, total_cost}]。

    弃用 litellm_client.spend_models（同时传 start+end 触发 daily 聚合 bug，
    返回 total_tokens 为 0）。改用 spend_logs 客户端聚合，数据源跟 by_agent 一致。

    model 为空的 log（失败请求，未选模型就报错）跳过，不入 unknown 桶——
    模型明细只展示真实有调用的模型，避免误导用户以为有个叫 "unknown" 的模型。
    """
    by_model_map: dict[str, dict[str, Any]] = {}
    for lg in logs:
        m = lg.get("model")
        if not m:
            continue
        bucket = by_model_map.setdefault(
            m, {"model": m, "total_tokens": 0, "total_cost": 0.0}
        )
        bucket["total_tokens"] += int(lg.get("prompt_tokens") or 0) + int(
            lg.get("completion_tokens") or 0
        )
        bucket["total_cost"] += float(lg.get("spend") or 0)
    # 按 token 数降序
    return sorted(by_model_map.values(), key=lambda x: x["total_tokens"], reverse=True)


def _aggregate_daily_tokens(logs: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    """按日聚合 token + 成本，返回 [{date, tokens, cost}]。"""
    buckets: dict[str, dict[str, Any]] = {}
    cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        buckets[cur.strftime("%Y-%m-%d")] = {"date": cur.strftime("%Y-%m-%d"), "tokens": 0, "cost": 0.0}
        cur += timedelta(days=1)
    for lg in logs:
        t = _parse_log_time(lg.get("startTime"))
        if t is None or not (start <= t <= end):
            continue
        key = t.strftime("%Y-%m-%d")
        if key not in buckets:
            continue
        buckets[key]["tokens"] += int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0)
        buckets[key]["cost"] += float(lg.get("spend") or 0)
    return list(buckets.values())


# ── 3. 调用分析 ────────────────────────────────────────────


def _empty_overall() -> dict[str, Any]:
    """空 overall 结构，保证筛选无匹配时返回的字段一致。"""
    return {
        "request_count": 0,
        "success_rate": 0,
        "p50_latency_ms": 0,
        "p95_latency_ms": 0,
        "avg_tokens_per_request": 0,
    }


async def _fetch_traces_for_agents(
    agent_ids: list[str] | None,
    from_ts: str | None,
    to_ts: str | None,
) -> list[dict[str, Any]]:
    """按 agent_ids 拉 Langfuse traces 并合并。

    agent_ids=None：无筛选，拉全部（单次调用，limit=100）。
    agent_ids=[id1, id2, ...]：并发拉每个 agent 的 trace（limit=100/agent），
        合并后按 createdAt 倒序排序。
    agent_ids=[]：不应进入此函数（调用方应提前返回空）。

    并发限制 10，避免 agent 多时压垮 Langfuse。
    """
    if agent_ids is None:
        responses = [await langfuse_client.list_traces(
            from_ts=from_ts, to_ts=to_ts, limit=100
        )]
    else:
        sem = asyncio.Semaphore(10)

        async def _fetch_one(aid: str):
            async with sem:
                return await langfuse_client.list_traces(
                    user_id=aid, from_ts=from_ts, to_ts=to_ts, limit=100
                )

        responses = await asyncio.gather(
            *[_fetch_one(aid) for aid in agent_ids], return_exceptions=True
        )

    traces: list[dict[str, Any]] = []
    for r in responses:
        if isinstance(r, Exception) or r is None:
            continue
        traces.extend(r.get("data") or [])
    # 多 agent 合并后按 createdAt 倒序，保持与单次调用一致的排序
    if agent_ids is not None and len(agent_ids) > 1:
        traces.sort(key=lambda t: t.get("createdAt") or "", reverse=True)
    return traces


@router.get("/quality")
async def get_quality(
    agent_id: str | None = Query(None, description="按智能体过滤"),
    enduser_id: str | None = Query(None, description="按终端用户 ID（chat 请求体 user 字段）过滤"),
    user_group_id: str | None = Query(None, description="按用户组过滤"),
    from_ts: str | None = Query(None, description="起始时间 ISO 8601"),
    to_ts: str | None = Query(None, description="结束时间 ISO 8601"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """调用分析：成功率 / 延迟 P50/P95 / 平均成本，按智能体拆分。

    数据源：Langfuse 客户端聚合（拉最近 100 条 trace + 各自 observations）。
    Langfuse REST 不支持聚合，limit≤100 MVP 可接受。

    userId 为空的 trace（测试 trace 或写埋点失败的 trace）跳过，不入 unknown 桶。

    user_group_id 筛选：反查该 group 下的 agent_ids 集合，再并发拉每个 agent 的 trace。
    enduser_id 筛选：Langfuse v3 REST 不支持 metadata 服务端过滤，拉完客户端过滤
    metadata.enduser_id。单 agent 时直接传 agent_id 给 Langfuse（服务端过滤）；
    多 agent 时并发拉后合并。
    """
    if not langfuse_client.is_configured():
        return {"langfuse_configured": False, "by_agent": [], "overall": _empty_overall()}

    # 解析 agent_ids：None=无筛选，[]=筛选后空集，[id...]=筛选后非空
    agent_ids = await _resolve_agent_ids(
        db, agent_id=agent_id, user_group_id=user_group_id
    )

    if agent_ids == []:
        # 筛选条件匹配不到任何 agent
        return {"langfuse_configured": True, "by_agent": [], "overall": _empty_overall()}

    traces = await _fetch_traces_for_agents(agent_ids, from_ts, to_ts)
    # 客户端过滤 enduser_id（Langfuse v3 REST 不支持 metadata 服务端过滤）
    traces = _filter_traces_by_enduser(traces, enduser_id)

    # 并发拉每条 trace 的 observations（v3 list_traces 只返回 ID 列表，需补拉）
    async def _fetch_observations(t: dict[str, Any]) -> list[dict[str, Any]]:
        o = await langfuse_client.list_observations(t.get("id"))
        return o or []

    observations_list = await asyncio.gather(*[_fetch_observations(t) for t in traces], return_exceptions=True)

    # 按 agent_id 分组聚合
    by_agent: dict[str, dict[str, Any]] = {}
    all_latencies: list[int] = []
    all_tokens: list[int] = []
    success_count = 0
    total_count = 0

    for t, obs in zip(traces, observations_list):
        if isinstance(obs, Exception):
            obs = []
        aid = t.get("userId")
        # userId 为空（测试 trace 或写埋点失败）跳过，避免污染 unknown 桶
        if not aid:
            continue
        latency = _trace_latency_ms(t, obs)
        tokens = _trace_token_total(t, obs)
        status = _trace_status(t, obs)

        bucket = by_agent.setdefault(aid, {
            "agent_id": aid,
            "request_count": 0,
            "success_count": 0,
            "latencies": [],
            "tokens": 0,
        })
        bucket["request_count"] += 1
        if status == "ok":
            bucket["success_count"] += 1
            success_count += 1
        if latency is not None:
            bucket["latencies"].append(latency)
            all_latencies.append(latency)
        bucket["tokens"] += tokens
        all_tokens.append(tokens)
        total_count += 1

    # 查 DB 拿 agent_id → name 映射（一次性）
    aid_to_name = await _resolve_agent_names(db, list(by_agent.keys()))

    # 算每组分位数 + 成功率
    by_agent_list = []
    for aid, b in by_agent.items():
        latencies_sorted = sorted(b["latencies"])
        by_agent_list.append({
            "agent_id": aid,
            "name": aid_to_name.get(aid),
            "request_count": b["request_count"],
            "success_rate": round(b["success_count"] / b["request_count"], 4) if b["request_count"] else 0,
            "p50_latency_ms": _percentile(latencies_sorted, 50),
            "p95_latency_ms": _percentile(latencies_sorted, 95),
            "avg_tokens": int(b["tokens"] / b["request_count"]) if b["request_count"] else 0,
        })

    all_latencies_sorted = sorted(all_latencies)
    return {
        "langfuse_configured": True,
        "overall": {
            "request_count": total_count,
            "success_rate": round(success_count / total_count, 4) if total_count else 0,
            "p50_latency_ms": _percentile(all_latencies_sorted, 50),
            "p95_latency_ms": _percentile(all_latencies_sorted, 95),
            "avg_tokens_per_request": int(sum(all_tokens) / total_count) if total_count else 0,
        },
        "by_agent": by_agent_list,
        "langfuse_url": _langfuse_url(),
    }


# ── 3.5 热门智能体排行 ─────────────────────────────────────


@router.get("/top-agents")
async def get_top_agents(
    limit: int = Query(5, ge=1, le=20),
    days: int = Query(30, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """热门智能体 Top N（近 N 天按 Langfuse trace 数排行）。

    数据源 Langfuse traces，按 userId(=agent_id) 分组 count。与调用分析页同源，
    统计口径=对话次数（1 trace = 1 次对话），不是 LLM 调用次数。

    实现：对每个 PUBLISHED agent 并发调 list_traces(limit=1)，取 meta.totalItems
    作为该 agent 的 trace 总数。避免拉全部 trace（Langfuse REST limit≤100 截断 +
    网络开销大）。并发限制 10。

    返回 {items: [{agent_id, name, conversation_count, total_tokens}]}。
    total_tokens 固定 0（本端点不拉 trace 详情，无法统计 token；如需 token 走 /quality）。
    """
    if not langfuse_client.is_configured():
        return {"langfuse_configured": False, "items": []}

    from app.models import AgentInstance, AgentStatus

    res = await db.execute(
        select(AgentInstance.id, AgentInstance.name)
        .where(AgentInstance.status == AgentStatus.PUBLISHED)
    )
    agents = [(str(aid), name) for aid, name in res.all()]

    if not agents:
        return {"langfuse_configured": True, "items": []}

    now = datetime.now(UTC)
    from_ts = (now - timedelta(days=days)).isoformat()
    to_ts = now.isoformat()

    sem = asyncio.Semaphore(10)

    async def _count_one(agent_id: str) -> int:
        async with sem:
            r = await langfuse_client.list_traces(
                user_id=agent_id, from_ts=from_ts, to_ts=to_ts, limit=1
            )
            if r is None:
                return 0
            meta = r.get("meta") or {}
            try:
                return int(meta.get("totalItems") or 0)
            except (ValueError, TypeError):
                return 0

    counts = await asyncio.gather(*[_count_one(aid) for aid, _ in agents])

    items = [
        {
            "agent_id": aid,
            "name": name,
            "conversation_count": cnt,
            "total_tokens": 0,
        }
        for (aid, name), cnt in zip(agents, counts)
    ]
    items.sort(key=lambda x: x["conversation_count"], reverse=True)
    return {"langfuse_configured": True, "items": items[:limit]}


# ── 4. 异常告警 ────────────────────────────────────────────


@router.get("/alerts")
async def get_alerts(
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """异常告警：5 大类规则触发列表（被动查看）。

    Phase 2 起：后台 _alert_check_loop 每 120s 调 alert_service.check_and_notify 主动推送，
    本端点为被动查看（admin 访问页面才计算），逻辑与轮询共用 alert_service.evaluate_rules。
    Langfuse 未配置时 tracing 类返回空，但其他 4 类（resource/service_health/usage/call_analysis）仍可触发。
    """
    from app.services.alert_service import evaluate_rules

    alerts = await evaluate_rules(db)
    # 字段映射：evaluate_rules 返回 rule_type + category，前端期望 type + category
    items = [
        {
            "type": a["rule_type"],
            "category": a.get("category", "tracing"),
            "severity": a["severity"],
            "agent_id": a["agent_id"],
            "trace_id": a["trace_id"],
            "message": a["message"],
            "created_at": a["created_at"],
        }
        for a in alerts
    ]
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {
        "items": items[:limit],
        "total": len(items),
        "langfuse_configured": langfuse_client.is_configured(),
        "langfuse_url": _langfuse_url(),
    }


# ── 4.1 告警规则配置 CRUD ────────────────────────────────


@router.get("/alert-rules", response_model=list[AlertRuleResponse])
async def list_alert_rules(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """列出全部告警规则（仅平台管理员）。按 created_at 升序，便于 admin 稳定展示。"""
    result = await db.execute(select(AlertRule).order_by(AlertRule.created_at))
    return list(result.scalars().all())


@router.put("/alert-rules/{rule_id}", response_model=AlertRuleResponse)
async def update_alert_rule(
    rule_id: UUID,
    payload: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """编辑告警规则（仅平台管理员）。

    规则集为系统预置（5 大类 × 16 子规则），不允许新增/删除。
    rule_type / category 不可改（避免语义漂移、evaluator 路由错乱）；
    可改字段：name / threshold / enabled / severity / description。
    """
    rule = await db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    changes: dict[str, Any] = {}
    if payload.name is not None and payload.name != rule.name:
        rule.name = payload.name
        changes["name"] = payload.name
    if payload.threshold is not None and payload.threshold != rule.threshold:
        rule.threshold = payload.threshold
        changes["threshold"] = payload.threshold
    if payload.enabled is not None and payload.enabled != rule.enabled:
        rule.enabled = payload.enabled
        changes["enabled"] = payload.enabled
    if payload.severity is not None and payload.severity != rule.severity:
        rule.severity = payload.severity
        changes["severity"] = payload.severity
    if payload.description is not None and payload.description != rule.description:
        rule.description = payload.description
        changes["description"] = payload.description
    if not changes:
        return rule
    await log_operation(
        db,
        actor_id=user.id,
        action="alert_rule.update",
        target_type="alert_rule",
        target_id=rule.id,
        detail=changes,
    )
    await db.commit()
    await db.refresh(rule)
    return rule


# ── 4.2 告警渠道 CRUD（独立实体，订阅规则） ────────────────


@router.get("/alert-channels", response_model=list[AlertChannelResponse])
async def list_alert_channels(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """列出全部告警渠道（仅平台管理员，config 含 webhook URL/邮箱地址敏感）。

    返回每条渠道含 subscribed_rule_ids 派生字段（subscribed_all=true 时为空数组）。
    按 created_at 升序，便于 admin 稳定展示。
    """
    result = await db.execute(
        select(AlertChannel).order_by(AlertChannel.created_at)
    )
    channels = result.scalars().all()
    resp = []
    for c in channels:
        # 直接查关联表，避免 ORM 关系懒加载在 async 下触发 greenlet
        rule_ids_q = await db.execute(
            select(channel_rule_subscriptions.c.rule_id)
            .where(channel_rule_subscriptions.c.channel_id == c.id)
        )
        rule_ids = [r for (r,) in rule_ids_q.all()]
        resp.append(
            AlertChannelResponse.model_validate({
                **{k: getattr(c, k) for k in (
                    "id", "name", "channel_type", "config",
                    "subscribed_all", "enabled", "created_at", "updated_at",
                )},
                "subscribed_rule_ids": rule_ids,
            })
        )
    return resp


async def _get_channel_with_rule_ids(
    db: AsyncSession, channel_id: UUID
) -> AlertChannelResponse | None:
    """单条渠道详情，含 subscribed_rule_ids。不存在返回 None。"""
    c = await db.get(AlertChannel, channel_id)
    if c is None:
        return None
    rule_ids_q = await db.execute(
        select(channel_rule_subscriptions.c.rule_id)
        .where(channel_rule_subscriptions.c.channel_id == channel_id)
    )
    rule_ids = [r for (r,) in rule_ids_q.all()]
    return AlertChannelResponse.model_validate({
        **{k: getattr(c, k) for k in (
            "id", "name", "channel_type", "config",
            "subscribed_all", "enabled", "created_at", "updated_at",
        )},
        "subscribed_rule_ids": rule_ids,
    })


async def _validate_rule_ids_exist(
    db: AsyncSession, rule_ids: list[UUID]
) -> set[UUID]:
    """校验 rule_ids 全部存在，返回存在的 ID 集合。不存在则抛 400。"""
    if not rule_ids:
        return set()
    valid_q = await db.execute(
        select(AlertRule.id).where(AlertRule.id.in_(rule_ids))
    )
    valid_ids = {r for (r,) in valid_q.all()}
    invalid = set(rule_ids) - valid_ids
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"订阅的 rule_id 不存在: {sorted(str(i) for i in invalid)}",
        )
    return valid_ids


@router.post("/alert-channels", response_model=AlertChannelResponse, status_code=201)
async def create_alert_channel(
    payload: AlertChannelCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """新增告警渠道（仅平台管理员）。

    subscribed_all=true 时 subscribed_rule_ids 自动忽略（关联表不写行，运行时短路）。
    """
    # 校验订阅的 rule_id 全部存在（subscribed_all=true 时跳过，反正不写关联表）
    if not payload.subscribed_all and payload.subscribed_rule_ids:
        await _validate_rule_ids_exist(db, payload.subscribed_rule_ids)

    channel = AlertChannel(
        name=payload.name,
        channel_type=payload.channel_type,
        config=payload.config,
        subscribed_all=payload.subscribed_all,
        enabled=payload.enabled,
    )
    db.add(channel)
    # flush 拿 channel.id（同事务内，未 commit），用于插关联表
    await db.flush()

    # 关联订阅规则（subscribed_all=true 时不写，运行时短路）。
    # 直接插关联表，避免 ORM 关系赋值在 async 下触发 lazy load → MissingGreenlet。
    if not payload.subscribed_all and payload.subscribed_rule_ids:
        await db.execute(
            channel_rule_subscriptions.insert(),
            [
                {"channel_id": channel.id, "rule_id": rid}
                for rid in payload.subscribed_rule_ids
            ],
        )

    await log_operation(
        db,
        actor_id=user.id,
        action="alert_channel.create",
        target_type="alert_channel",
        target_id=channel.id,
        detail={
            "name": payload.name,
            "channel_type": payload.channel_type,
            "subscribed_all": payload.subscribed_all,
            "subscribed_rule_ids_count": 0 if payload.subscribed_all else len(payload.subscribed_rule_ids or []),
        },
    )
    await db.commit()
    await db.refresh(channel)
    resp = await _get_channel_with_rule_ids(db, channel.id)
    assert resp is not None  # 刚 commit 完必然存在
    return resp


@router.put("/alert-channels/{channel_id}", response_model=AlertChannelResponse)
async def update_alert_channel(
    channel_id: UUID,
    payload: AlertChannelUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """编辑告警渠道（仅平台管理员）。subscribed_rule_ids 传入时整体替换关联表。"""
    channel = await db.get(AlertChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="告警渠道不存在")
    # 记录原 subscribed_all，用于判断 False→True 转换时清空关联表
    was_subscribed_all = channel.subscribed_all
    changes: dict[str, Any] = {}
    if payload.name is not None and payload.name != channel.name:
        channel.name = payload.name
        changes["name"] = payload.name
    if payload.channel_type is not None and payload.channel_type != channel.channel_type:
        channel.channel_type = payload.channel_type
        changes["channel_type"] = payload.channel_type
    if payload.config is not None and payload.config != channel.config:
        channel.config = payload.config
        changes["config_updated"] = True
    if payload.enabled is not None and payload.enabled != channel.enabled:
        channel.enabled = payload.enabled
        changes["enabled"] = payload.enabled
    if payload.subscribed_all is not None and payload.subscribed_all != channel.subscribed_all:
        channel.subscribed_all = payload.subscribed_all
        changes["subscribed_all"] = payload.subscribed_all

    # subscribed_rule_ids 整体替换：subscribed_all=true 时强制清空（运行时短路）。
    # 直接操作关联表，避免 ORM 关系赋值触发 lazy load。
    if payload.subscribed_rule_ids is not None:
        if channel.subscribed_all:
            new_rule_ids: set[UUID] = set()
        else:
            new_rule_ids = await _validate_rule_ids_exist(db, payload.subscribed_rule_ids)
        changes["subscribed_rule_ids_count"] = len(new_rule_ids)

        # 查当前关联（直接 select 关联表，不触发 ORM 懒加载）
        current_q = await db.execute(
            select(channel_rule_subscriptions.c.rule_id)
            .where(channel_rule_subscriptions.c.channel_id == channel_id)
        )
        current_ids = {r for (r,) in current_q.all()}
        if current_ids != new_rule_ids:
            # 整体替换：先删旧的，再插新的
            await db.execute(
                channel_rule_subscriptions.delete().where(
                    channel_rule_subscriptions.c.channel_id == channel_id
                )
            )
            if new_rule_ids:
                await db.execute(
                    channel_rule_subscriptions.insert(),
                    [
                        {"channel_id": channel_id, "rule_id": rid}
                        for rid in new_rule_ids
                    ],
                )
    elif payload.subscribed_all is True and not was_subscribed_all:
        # subscribed_all 从 False → True，且未传 subscribed_rule_ids：
        # 显式订阅此时冗余，清空关联表（运行时短路，subscribed_all=true 命中所有规则）。
        await db.execute(
            channel_rule_subscriptions.delete().where(
                channel_rule_subscriptions.c.channel_id == channel_id
            )
        )
        changes["subscribed_rule_ids_count"] = 0

    if not changes:
        return await _get_channel_with_rule_ids(db, channel_id)  # type: ignore[return-value]

    await log_operation(
        db,
        actor_id=user.id,
        action="alert_channel.update",
        target_type="alert_channel",
        target_id=channel.id,
        detail=changes,
    )
    await db.commit()
    await db.refresh(channel)
    resp = await _get_channel_with_rule_ids(db, channel_id)
    assert resp is not None
    return resp


@router.delete("/alert-channels/{channel_id}", status_code=204)
async def delete_alert_channel(
    channel_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """删除告警渠道（仅平台管理员）。channel_rule_subscriptions ondelete=CASCADE 自动清理关联。"""
    channel = await db.get(AlertChannel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="告警渠道不存在")
    await log_operation(
        db,
        actor_id=user.id,
        action="alert_channel.delete",
        target_type="alert_channel",
        target_id=channel.id,
        detail={"name": channel.name, "channel_type": channel.channel_type},
    )
    await db.delete(channel)
    await db.commit()


# ── 4.3 告警事件历史 ───────────────────────────────────


@router.get("/alert-events")
async def list_alert_events(
    rule_id: UUID | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None),
    rule_types: str | None = Query(None, description="按 rule_type 过滤，逗号分隔多值，如 high_latency,high_tokens"),
    time_from: datetime | None = Query(None),
    time_to: datetime | None = Query(None),
    pageSize: int = Query(20, ge=1, le=100),  # noqa: N803  前端 vue-pure-admin 约定
    currentPage: int = Query(1, ge=1),  # noqa: N803
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """告警事件历史查询（普通登录可查，notified_channels 不含 webhook URL/邮箱）。

    返回 {code, message, data:{list, total, pageSize, currentPage, stats}}。
    stats 按 severity + status 双维度聚合（不受分页/过滤影响），供顶部统计卡片 + status tab 展示。
    rule_types 用于按类型/分类过滤：前端把分类展开为该分类下所有 rule_type 逗号拼接传入。
    """
    # 逗号分隔转 list，去空白去空值
    rule_type_list = [t.strip() for t in rule_types.split(",")] if rule_types else None
    rule_type_list = [t for t in rule_type_list if t] if rule_type_list else None

    query = select(AlertEvent)
    if rule_id is not None:
        query = query.where(AlertEvent.rule_id == rule_id)
    if severity:
        query = query.where(AlertEvent.severity == severity)
    if status:
        query = query.where(AlertEvent.status == status)
    if rule_type_list:
        query = query.where(AlertEvent.rule_type.in_(rule_type_list))
    if time_from:
        query = query.where(AlertEvent.created_at >= time_from)
    if time_to:
        query = query.where(AlertEvent.created_at <= time_to)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(AlertEvent.created_at.desc())
        .offset((currentPage - 1) * pageSize)
        .limit(pageSize)
    )
    result = await db.execute(query)
    events = result.scalars().all()

    # stats：按 severity 聚合，固定只算 status='firing' 的——顶部「异常总数/严重/警告」
    # 反映「当前还有多少异常在触发」，不把已恢复/已确认的计入（用户切到「已恢复」tab
    # 时仍能看到活跃异常的概览）。不受分页/severity/status 过滤影响。
    stats_query = select(
        AlertEvent.severity,
        func.count(),
    ).where(AlertEvent.status == "firing").group_by(AlertEvent.severity)
    if rule_id is not None:
        stats_query = stats_query.where(AlertEvent.rule_id == rule_id)
    if rule_type_list:
        stats_query = stats_query.where(AlertEvent.rule_type.in_(rule_type_list))
    if time_from:
        stats_query = stats_query.where(AlertEvent.created_at >= time_from)
    if time_to:
        stats_query = stats_query.where(AlertEvent.created_at <= time_to)
    stats_result = (await db.execute(stats_query)).all()
    stats = {row[0]: row[1] for row in stats_result}

    # status 聚合（firing/resolved/acknowledged），全量计数供 status tab 展示
    status_stats_query = select(
        AlertEvent.status,
        func.count(),
    ).group_by(AlertEvent.status)
    if rule_id is not None:
        status_stats_query = status_stats_query.where(AlertEvent.rule_id == rule_id)
    if rule_type_list:
        status_stats_query = status_stats_query.where(AlertEvent.rule_type.in_(rule_type_list))
    if time_from:
        status_stats_query = status_stats_query.where(AlertEvent.created_at >= time_from)
    if time_to:
        status_stats_query = status_stats_query.where(AlertEvent.created_at <= time_to)
    status_result = (await db.execute(status_stats_query)).all()
    status_stats = {row[0]: row[1] for row in status_result}

    return {
        "code": 0,
        "message": "操作成功",
        "data": {
            "list": [
                {
                    "id": str(e.id),
                    "rule_id": str(e.rule_id) if e.rule_id else None,
                    "rule_name": e.rule_name,
                    "rule_type": e.rule_type,
                    "trace_id": e.trace_id,
                    "agent_id": e.agent_id,
                    "severity": e.severity,
                    "message": e.message,
                    "notified_channels": e.notified_channels or [],
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "status": e.status,
                    "acknowledged_by": e.acknowledged_by,
                    "acknowledged_at": e.acknowledged_at.isoformat() if e.acknowledged_at else None,
                    "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
                    "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
                }
                for e in events
            ],
            "total": total,
            "pageSize": pageSize,
            "currentPage": currentPage,
            "stats": {
                "critical": stats.get("critical", 0),
                "warning": stats.get("warning", 0),
                "firing": status_stats.get("firing", 0),
                "resolved": status_stats.get("resolved", 0),
                "acknowledged": status_stats.get("acknowledged", 0),
            },
            "langfuse_configured": langfuse_client.is_configured(),
            "langfuse_url": _langfuse_url(),
        },
    }


@router.post("/alert-events/{event_id}/acknowledge")
async def acknowledge_alert_event(
    event_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """标记事件为 acknowledged（人已确认）。

    设计：acknowledged 是正交状态——不影响 firing/resolved 判定，但 acknowledged 的事件
    在 check_and_notify 中不再触发重复通知（同 rule+agent 在 DEDUP_WINDOW 内有 acknowledged
    事件时，新触发会写 firing 事件但跳过 notify_channels）。

    firing 状态下标记 acknowledged：status 直接改为 acknowledged（便于查询过滤）。
    resolved 状态下标记 acknowledged：保持 resolved（已是终态），仅记录 acknowledged_by/at。
    """
    event = await db.get(AlertEvent, event_id)
    if event is None:
        raise HTTPException(404, "事件不存在")
    event.acknowledged_by = user.username
    event.acknowledged_at = datetime.now(UTC)
    if event.status == "firing":
        event.status = "acknowledged"
    await db.commit()

    await log_operation(
        db,
        actor_id=user.id,
        action="alert_event.acknowledge",
        target_type="alert_event",
        target_id=event.id,
        detail={"rule_name": event.rule_name, "rule_type": event.rule_type},
    )
    return {"code": 0, "message": "操作成功"}


# ── 5. 资源监控 ────────────────────────────────────────────

# range → (seconds, step) 映射
_RANGE_CONFIG: dict[str, tuple[int, str]] = {
    "1h": (3600, "60s"),
    "6h": (21600, "300s"),
    "24h": (86400, "600s"),
    "7d": (604800, "3600s"),
}


def _compute_step(seconds: float) -> str:
    """根据时间跨度自动选 step（控制趋势图点数在 60~200 之间）。

    - ≤2h → 60s
    - ≤12h → 300s
    - ≤2d → 600s
    - ≤14d → 1800s
    - >14d → 3600s
    """
    if seconds <= 7200:
        return "60s"
    if seconds <= 43200:
        return "300s"
    if seconds <= 172800:
        return "600s"
    if seconds <= 1209600:
        return "1800s"
    return "3600s"

# PromQL 查询模板
_PROM_CLUSTER_CPU_PCT = "1 - avg(rate(node_cpu_seconds_total{mode=\"idle\"}[5m]))"
_PROM_CLUSTER_MEM_PCT = "1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes)"
_PROM_POD_COUNT = 'count(kube_pod_status_phase{namespace="unionagents",phase="Running"})'
_PROM_TOP_NODES_CPU = "topk(5, (1 - rate(node_cpu_seconds_total{mode=\"idle\"}[5m])) * 100)"
_PROM_TOP_NODES_MEM = "topk(5, (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes) * 100)"
_PROM_TOP_NODES_DISK = 'topk(5, (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) * 100)'
# 注意：cAdvisor 的 container_cpu_usage_seconds_total 在本集群缺少 namespace/container 标签
# （cAdvisor 独立部署，未与 kubelet 集成打 K8s 元数据）。改用 kube-state-metrics 的
# resource_requests 指标——显示 Pod 资源申请量（非实际使用量），数据可靠且带 namespace/pod 标签。
# sum by (pod) 把多容器 Pod 的 requests 合并到 Pod 维度。
_PROM_TOP_PODS_CPU = 'topk(5, sum by (pod) (kube_pod_container_resource_requests{resource="cpu",namespace="unionagents"}))'
_PROM_TOP_PODS_MEM = 'topk(5, sum by (pod) (kube_pod_container_resource_requests{resource="memory",namespace="unionagents"}))'
_PROM_POD_RESTARTS = 'max by (pod) (kube_pod_container_status_restarts_total{namespace="unionagents"})'
_PROM_FIRING_ALERTS = 'count(ALERTS{alertstate="firing"} == 1)'


def _scalar_value(result: list[dict[str, Any]] | None) -> float | None:
    """从 Prometheus 即时查询结果提取标量值。

    Prometheus 即时查询返回 `[{"metric": {}, "value": [ts, "0.234"]}]`。
    """
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
    """从 topk() 查询结果提取 [(labels, value), ...] 列表，按 value 降序。

    topk 返回 `[{"metric": {instance: "X"}, "value": [ts, "45.2"]}, ...]`
    """
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


def _trend_series(result: list[dict[str, Any]] | None) -> list[tuple[int, float]]:
    """从 query_range 结果提取 [(ts, value), ...] 列表。

    query_range 返回 `[{"metric": {}, "values": [[ts, "0.23"], ...]}]`。
    单条 series（聚合查询）取第一项。
    """
    if not result:
        return []
    item = result[0]
    values = item.get("values") or []
    out: list[tuple[int, float]] = []
    for v in values:
        if isinstance(v, list) and len(v) >= 2:
            try:
                out.append((int(float(v[0])), float(v[1])))
            except (ValueError, TypeError):
                continue
    return out


@router.get("/resources")
async def get_resources(
    range: str = Query("1h", pattern="^(1h|6h|24h|7d)$"),
    start_ts: float | None = Query(None, description="Unix 秒。提供后覆盖 range，启用自定义时间范围"),
    end_ts: float | None = Query(None, description="Unix 秒。提供后覆盖 range，启用自定义时间范围"),
    _: User = Depends(get_current_user),
):
    """资源监控：集群概览 + 趋势 + Top 节点/Pod + 告警计数。

    数据源 Prometheus（node_exporter + cAdvisor + kube-state-metrics + alertmanager）。
    Prometheus 不可达时 metrics_available=False，前端展示空状态。

    时间范围：传 start_ts+end_ts 启用自定义范围（step 自动按跨度计算）；
    否则用 range 预设（1h/6h/24h/7d）。
    """
    if not prometheus_client.is_configured():
        return {
            "metrics_available": False,
            "range": range,
            "cluster": {"cpu_pct": 0.0, "memory_pct": 0.0, "pod_count": 0},
            "trend": [],
            "top_nodes": [],
            "top_pods": [],
            "firing_alerts": 0,
            "grafana_url": _grafana_url(),
        }

    now = datetime.now(UTC)
    if start_ts is not None and end_ts is not None:
        # 自定义范围：以传入时间为准，step 按跨度自动计算
        start_ts = float(start_ts)
        end_ts = float(end_ts)
        # 防御：end 必须 > start；跨度限制在 1 分钟 ~ 30 天
        if end_ts <= start_ts:
            raise HTTPException(status_code=400, detail="end_ts must be greater than start_ts")
        span = end_ts - start_ts
        if span < 60:
            raise HTTPException(status_code=400, detail="time range must be at least 60 seconds")
        if span > 30 * 86400:
            raise HTTPException(status_code=400, detail="time range must not exceed 30 days")
        step = _compute_step(span)
        range_label = "custom"
    else:
        seconds, step = _RANGE_CONFIG[range]
        start_ts = now.timestamp() - seconds
        end_ts = now.timestamp()
        range_label = range

    # 并发拉所有指标
    cluster_cpu_res, cluster_mem_res, pod_count_res, firing_res, trend_cpu_res, trend_mem_res, \
        top_nodes_cpu_res, top_nodes_mem_res, top_nodes_disk_res, \
        top_pods_cpu_res, top_pods_mem_res, pod_restarts_res = await asyncio.gather(
            prometheus_client.query(_PROM_CLUSTER_CPU_PCT),
            prometheus_client.query(_PROM_CLUSTER_MEM_PCT),
            prometheus_client.query(_PROM_POD_COUNT),
            prometheus_client.query(_PROM_FIRING_ALERTS),
            prometheus_client.query_range(_PROM_CLUSTER_CPU_PCT, start_ts, end_ts, step),
            prometheus_client.query_range(_PROM_CLUSTER_MEM_PCT, start_ts, end_ts, step),
            prometheus_client.query(_PROM_TOP_NODES_CPU),
            prometheus_client.query(_PROM_TOP_NODES_MEM),
            prometheus_client.query(_PROM_TOP_NODES_DISK),
            prometheus_client.query(_PROM_TOP_PODS_CPU),
            prometheus_client.query(_PROM_TOP_PODS_MEM),
            prometheus_client.query(_PROM_POD_RESTARTS),
        )

    # 任意一个核心查询成功即视为 metrics_available（部分失败容忍）
    any_ok = any([
        cluster_cpu_res is not None,
        cluster_mem_res is not None,
        pod_count_res is not None,
    ])

    cluster_cpu = _scalar_value(cluster_cpu_res)
    cluster_mem = _scalar_value(cluster_mem_res)
    pod_count_raw = _scalar_value(pod_count_res)
    firing = _scalar_value(firing_res)

    # trend：两条 series 对齐时间戳
    cpu_trend = dict(_trend_series(trend_cpu_res))
    mem_trend = dict(_trend_series(trend_mem_res))
    all_ts = sorted(set(cpu_trend.keys()) | set(mem_trend.keys()))
    trend = [
        {"ts": ts, "cpu_pct": cpu_trend.get(ts, 0.0), "memory_pct": mem_trend.get(ts, 0.0)}
        for ts in all_ts
    ]

    # top nodes：按 instance 标签合并 CPU/Mem/Disk
    nodes_cpu = {labels.get("instance", ""): v for labels, v in _topk_list(top_nodes_cpu_res)}
    nodes_mem = {labels.get("instance", ""): v for labels, v in _topk_list(top_nodes_mem_res)}
    nodes_disk = {labels.get("instance", ""): v for labels, v in _topk_list(top_nodes_disk_res)}
    all_nodes = set(nodes_cpu) | set(nodes_mem) | set(nodes_disk)
    # 按 CPU 排序（无 CPU 则按 Mem，再按 Disk）
    top_nodes = sorted(all_nodes, key=lambda n: -(nodes_cpu.get(n, 0)))[:5]
    top_nodes_out = [
        {
            "instance": n,
            "cpu_pct": round(nodes_cpu.get(n, 0.0), 2),
            "memory_pct": round(nodes_mem.get(n, 0.0), 2),
            "disk_pct": round(nodes_disk.get(n, 0.0), 2),
        }
        for n in top_nodes
        if n  # 跳过空 instance
    ]

    # top pods：按 pod 标签合并 CPU/Mem/Restarts
    pods_cpu = {labels.get("pod", ""): v for labels, v in _topk_list(top_pods_cpu_res)}
    pods_mem = {labels.get("pod", ""): v for labels, v in _topk_list(top_pods_mem_res)}
    # pod_restarts 是所有 pod 的累积计数，非 topk，结果格式不同（每条带 pod label）
    pod_restarts: dict[str, int] = {}
    if pod_restarts_res:
        for item in pod_restarts_res:
            labels = item.get("metric") or {}
            pname = labels.get("pod", "")
            val = item.get("value")
            if isinstance(val, list) and len(val) >= 2 and pname:
                try:
                    pod_restarts[pname] = int(float(val[1]))
                except (ValueError, TypeError):
                    continue
    all_pods = set(pods_cpu) | set(pods_mem)
    top_pods = sorted(all_pods, key=lambda p: -(pods_cpu.get(p, 0.0)))[:5]
    top_pods_out = [
        {
            "pod": p,
            "namespace": "unionagents",
            "cpu_used_cores": round(pods_cpu.get(p, 0.0), 3),
            "memory_used_mb": round(pods_mem.get(p, 0.0) / 1024 / 1024, 1),
            "restarts": pod_restarts.get(p, 0),
        }
        for p in top_pods
        if p
    ]

    return {
        "metrics_available": any_ok,
        "range": range_label,
        "cluster": {
            "cpu_pct": round(cluster_cpu * 100, 2) if cluster_cpu is not None else 0.0,
            "memory_pct": round(cluster_mem * 100, 2) if cluster_mem is not None else 0.0,
            "pod_count": int(pod_count_raw) if pod_count_raw is not None else 0,
        },
        "trend": [
            {
                "ts": t["ts"],
                "cpu_pct": round(t["cpu_pct"] * 100, 2),
                "memory_pct": round(t["memory_pct"] * 100, 2),
            }
            for t in trend
        ],
        "top_nodes": top_nodes_out,
        "top_pods": top_pods_out,
        "firing_alerts": int(firing) if firing is not None else 0,
        "grafana_url": _grafana_url(),
    }


# ── 服务健康 ────────────────────────────────────────────────────────────
# 服务清单：name=前端展示名；kind=查询类型（up 用 scrape job，probe 用 blackbox instance）
# 状态字段优先用 probe_success（更准），up 作 fallback（无 blackbox 时）
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
_SLO = {"latency_p95_ms": 500.0, "uptime_pct": 99.5}
_GRAFANA_DASHBOARD_UID = "unionagents-overview"


def _range_str_for_avg(seconds: float) -> str:
    """把秒数转成 PromQL 的 range vector 字符串：60s→1m，3600s→1h，3601s→3601s。"""
    if seconds <= 60:
        return "1m"
    if seconds % 3600 == 0:
        return f"{int(seconds // 3600)}h"
    if seconds % 60 == 0:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds)}s"


@router.get("/service-health")
async def get_service_health(
    range: str = Query("1h", pattern="^(1h|6h|24h|7d)$"),
    start_ts: float | None = Query(None, description="Unix 秒。提供后覆盖 range"),
    end_ts: float | None = Query(None, description="Unix 秒。提供后覆盖 range"),
    _: User = Depends(get_current_user),
):
    """服务健康：6 个核心服务的当前状态 + 延迟趋势 + 可用率 + SLO 达标。

    数据源 Prometheus（scrape `up` + blackbox-exporter `probe_*`）。
    Prometheus 不可达时 metrics_available=False，前端展示空状态。

    时间范围：传 start_ts+end_ts 启用自定义范围；否则用 range 预设。
    """
    if not prometheus_client.is_configured():
        return {
            "metrics_available": False,
            "range": range,
            "overall": {"up_count": 0, "total_count": len(_SERVICES),
                        "avg_p95_ms": None, "avg_uptime_pct": 0.0},
            "items": [],
            "trend": [],
            "grafana_url": _grafana_url(),
            "grafana_dashboard_uid": _GRAFANA_DASHBOARD_UID,
        }

    now = datetime.now(UTC)
    if start_ts is not None and end_ts is not None:
        start_ts = float(start_ts)
        end_ts = float(end_ts)
        if end_ts <= start_ts:
            raise HTTPException(status_code=400, detail="end_ts must be greater than start_ts")
        span = end_ts - start_ts
        if span < 60:
            raise HTTPException(status_code=400, detail="time range must be at least 60 seconds")
        if span > 30 * 86400:
            raise HTTPException(status_code=400, detail="time range must not exceed 30 days")
        step = _compute_step(span)
        avg_range = _range_str_for_avg(span)
        range_label = "custom"
    else:
        seconds, step = _RANGE_CONFIG[range]
        start_ts = now.timestamp() - seconds
        end_ts = now.timestamp()
        avg_range = range
        range_label = range

    # 为每个服务构造 PromQL，并发查
    queries: list[tuple[str, str, str, str]] = []  # (service_name, kind, query_type, promql)
    for svc in _SERVICES:
        name = svc["name"]
        if svc["kind"] == "up":
            # 仅 up（无 blackbox probe）—— 状态用 up
            queries.append((name, "up", "status", f'up{{job="{svc["job"]}"}}'))
            queries.append((name, "up", "uptime",
                            f'avg_over_time(up{{job="{svc["job"]}"}}[{avg_range}]) * 100'))
        elif svc["kind"] == "probe":
            # 仅 probe（无 scrape job）
            queries.append((name, "probe", "status",
                            f'probe_success{{instance="{svc["instance"]}"}}'))
            queries.append((name, "probe", "latency",
                            f'probe_duration_seconds{{instance="{svc["instance"]}"}} * 1000'))
            queries.append((name, "probe", "uptime",
                            f'avg_over_time(probe_success{{instance="{svc["instance"]}"}}[{avg_range}]) * 100'))
            queries.append((name, "probe", "p50",
                            f'quantile_over_time(0.50, probe_duration_seconds{{instance="{svc["instance"]}"}}[{avg_range}]) * 1000'))
            queries.append((name, "probe", "p95",
                            f'quantile_over_time(0.95, probe_duration_seconds{{instance="{svc["instance"]}"}}[{avg_range}]) * 1000'))
        else:  # kind == "both" —— 优先 probe，up 作 fallback
            queries.append((name, "both", "status",
                            f'probe_success{{instance="{svc["instance"]}"}}'))
            queries.append((name, "both", "latency",
                            f'probe_duration_seconds{{instance="{svc["instance"]}"}} * 1000'))
            queries.append((name, "both", "uptime",
                            f'avg_over_time(probe_success{{instance="{svc["instance"]}"}}[{avg_range}]) * 100'))
            queries.append((name, "both", "p50",
                            f'quantile_over_time(0.50, probe_duration_seconds{{instance="{svc["instance"]}"}}[{avg_range}]) * 1000'))
            queries.append((name, "both", "p95",
                            f'quantile_over_time(0.95, probe_duration_seconds{{instance="{svc["instance"]}"}}[{avg_range}]) * 1000'))

    # 并发执行所有即时查询
    results = await asyncio.gather(*[
        prometheus_client.query(q) for _, _, _, q in queries
    ])

    # 按 (service_name, query_type) 索引结果
    by_service: dict[str, dict[str, float | None]] = {svc["name"]: {} for svc in _SERVICES}
    for (name, _kind, qtype, _q), res in zip(queries, results):
        val = _scalar_value(res)
        by_service[name][qtype] = val

    # 趋势查询：每个 probe/both 服务的 probe_duration_seconds
    trend_queries: list[tuple[str, str]] = []  # (service_name, promql)
    for svc in _SERVICES:
        if svc["kind"] in ("probe", "both"):
            trend_queries.append((svc["name"],
                f'probe_duration_seconds{{instance="{svc["instance"]}"}} * 1000'))
    trend_results = await asyncio.gather(*[
        prometheus_client.query_range(q, start_ts, end_ts, step) for _, q in trend_queries
    ])
    # 每个 series 提取 [(ts, latency_ms), ...]，按 ts 对齐所有服务
    by_service_trend: dict[str, dict[int, float]] = {}
    for (name, _q), res in zip(trend_queries, trend_results):
        by_service_trend[name] = dict(_trend_series(res))
    all_ts = sorted(set().union(*(d.keys() for d in by_service_trend.values()))) if by_service_trend else []
    trend = [
        {"ts": ts, "latencies": {name: by_service_trend[name].get(ts) for name in by_service_trend}}
        for ts in all_ts
    ]

    # 构造 items + overall
    items: list[dict] = []
    up_count = 0
    p95_values: list[float] = []
    uptime_values: list[float] = []
    for svc in _SERVICES:
        name = svc["name"]
        d = by_service[name]
        # 状态：probe_success 优先，None 时 fallback 到 up
        status_val = d.get("status")
        uptime_val = d.get("uptime")
        if status_val is None and svc["kind"] == "both":
            # both 模式 probe 查不到，fallback 用 up
            # 但 up 的查询在 kind=up 时才有，both 模式没查 up——这里状态就判 down
            status_val = 0.0
        status = "ok" if (status_val is not None and status_val >= 1.0) else "down"
        if status == "ok":
            up_count += 1
        latency = d.get("latency")
        p50 = d.get("p50")
        p95 = d.get("p95")
        if uptime_val is not None:
            uptime_values.append(uptime_val)
        if p95 is not None:
            p95_values.append(p95)
        slo_met = (
            uptime_val is not None and uptime_val >= _SLO["uptime_pct"]
            and p95 is not None and p95 <= _SLO["latency_p95_ms"]
        )
        items.append({
            "name": name,
            "status": status,
            "latency_ms": round(latency, 2) if latency is not None else None,
            "p50_ms": round(p50, 2) if p50 is not None else None,
            "p95_ms": round(p95, 2) if p95 is not None else None,
            "uptime_pct": round(uptime_val, 2) if uptime_val is not None else 0.0,
            "slo_met": slo_met,
            "last_down_ts": None,  # v2: 查 ALERTS 历史
            "is_tcp": svc.get("is_tcp") == "1",
        })

    avg_p95 = round(sum(p95_values) / len(p95_values), 2) if p95_values else None
    avg_uptime = round(sum(uptime_values) / len(uptime_values), 2) if uptime_values else 0.0

    return {
        "metrics_available": True,
        "range": range_label,
        "overall": {
            "up_count": up_count,
            "total_count": len(_SERVICES),
            "avg_p95_ms": avg_p95,
            "avg_uptime_pct": avg_uptime,
        },
        "items": items,
        "trend": trend,
        "grafana_url": _grafana_url(),
        "grafana_dashboard_uid": _GRAFANA_DASHBOARD_UID,
    }


# ── 操作日志（OperationLog 全量审计查询） ────────────────────


async def _resolve_target_names(
    db: AsyncSession, rows: list[OperationLog]
) -> dict[tuple[str, str], str]:
    """按 target_type 批量查业务表拿 target_name。返回 (target_type, target_id_str) -> name。
    未命中（target_id 为 None / 表里找不到 / 未支持的 target_type）→ 不放入 map。"""
    from app.models import (
        AgentDefinition,
        AgentInstance,
        EngineConfig,
        ResourcePool,
        Role,
        UserGroup,
    )

    by_type: dict[str, list[UUID]] = {}
    for log in rows:
        if log.target_id is None:
            continue
        by_type.setdefault(log.target_type, []).append(log.target_id)

    name_map: dict[tuple[str, str], str] = {}
    if not by_type:
        return name_map

    # 对每个 target_type 批量查对应业务表的 name 字段
    for ttype, ids in by_type.items():
        if not ids:
            continue
        if ttype == "user":
            r = await db.execute(
                select(User.id, User.username, User.real_name).where(User.id.in_(ids))
            )
            for uid, username, real_name in r.all():
                name = f"{username} ({real_name})" if real_name else username
                name_map[("user", str(uid))] = name
        elif ttype == "agent_instance":
            r = await db.execute(
                select(AgentInstance.id, AgentInstance.name).where(AgentInstance.id.in_(ids))
            )
            for aid, name in r.all():
                name_map[("agent_instance", str(aid))] = name
        elif ttype == "agent_definition":
            r = await db.execute(
                select(AgentDefinition.id, AgentDefinition.name).where(AgentDefinition.id.in_(ids))
            )
            for did, name in r.all():
                name_map[("agent_definition", str(did))] = name
        elif ttype == "resource_pool":
            r = await db.execute(
                select(ResourcePool.id, ResourcePool.name).where(ResourcePool.id.in_(ids))
            )
            for rid, name in r.all():
                name_map[("resource_pool", str(rid))] = name
        elif ttype == "user_group":
            r = await db.execute(
                select(UserGroup.id, UserGroup.name).where(UserGroup.id.in_(ids))
            )
            for gid, name in r.all():
                name_map[("user_group", str(gid))] = name
        elif ttype == "role":
            r = await db.execute(select(Role.id, Role.name).where(Role.id.in_(ids)))
            for rid, name in r.all():
                name_map[("role", str(rid))] = name
        elif ttype == "engine_config":
            r = await db.execute(
                select(EngineConfig.id, EngineConfig.engine_type).where(EngineConfig.id.in_(ids))
            )
            for eid, etype in r.all():
                # engine_type 是 Enum，取 .value 拿字符串（如 "DIFY"）
                name_map[("engine_config", str(eid))] = str(etype.value) if etype else "—"
        # channel / agent_skill / litellm_*: target_id 非 UUID 或无 name 列，跳过（前端 fallback 短 UUID）

    return name_map


@router.get("/operation-logs")
async def list_operation_logs(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
    actor_id: UUID | None = Query(None),
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    target_id: UUID | None = Query(None),
    status: str | None = Query(None),
    group_id: UUID | None = Query(None),
    keyword: str | None = Query(None, description="detail ILIKE 模糊匹配"),
    time_from: datetime | None = Query(None),
    time_to: datetime | None = Query(None),
    pageSize: int = Query(10, ge=1, le=200),  # noqa: N803  前端 vue-pure-admin 约定
    currentPage: int = Query(1, ge=1),  # noqa: N803
):
    """全量操作日志查询（平台管理员）。actor_id nullable + isouter join User，
    保证被删用户的日志行不丢，actor_name 为 null 时前端显示"已删除用户"。
    target_name 由 _resolve_target_names 批量按 target_type join 各业务表，
    命中时前端显示业务名称，未命中时 fallback 显示短 UUID。

    系统自动调用的高频无业务语义操作（auth.refresh：前端 axios 拦截器 30min 自动
    刷新 token）默认过滤不展示——用户不感知、对系统数据无影响，留着会刷屏。"""
    conditions = []
    if actor_id is not None:
        conditions.append(OperationLog.actor_id == actor_id)
    if action:
        conditions.append(OperationLog.action == action)
    if target_type:
        conditions.append(OperationLog.target_type == target_type)
    if target_id is not None:
        conditions.append(OperationLog.target_id == target_id)
    if status:
        conditions.append(OperationLog.status == status)
    if group_id is not None:
        conditions.append(OperationLog.group_id == group_id)
    if keyword:
        # JSON 列需 cast 成 text 才能 ILIKE
        conditions.append(cast(OperationLog.detail, Text).ilike(f"%{keyword}%"))
    if time_from is not None:
        conditions.append(OperationLog.created_at >= time_from)
    if time_to is not None:
        conditions.append(OperationLog.created_at <= time_to)
    # 始终过滤系统自动操作（即便显式传 action=auth.refresh 也返回空）
    conditions.append(OperationLog.action != "auth.refresh")

    base = (
        select(OperationLog, User.username, User.real_name)
        .join(User, User.id == OperationLog.actor_id, isouter=True)
    )
    if conditions:
        base = base.where(*conditions)

    total = await db.scalar(
        select(func.count()).select_from(base.subquery())
    )
    rows = list((await db.execute(
        base.order_by(OperationLog.created_at.desc())
        .limit(pageSize)
        .offset((currentPage - 1) * pageSize)
    )).all())

    # 批量解析 target_name（按 target_type 分组 N+1 优化）
    logs_only = [log for log, *_ in rows]
    name_map = await _resolve_target_names(db, logs_only)

    items = []
    for log, username, real_name in rows:
        target_id_str = str(log.target_id) if log.target_id else None
        target_name = name_map.get((log.target_type, target_id_str)) if target_id_str else None
        items.append({
            "id": str(log.id),
            "action": log.action,
            "target_type": log.target_type,
            "target_id": target_id_str,
            "target_name": target_name,
            "status": log.status,
            "detail": log.detail,
            "group_id": str(log.group_id) if log.group_id else None,
            "actor_id": str(log.actor_id) if log.actor_id else None,
            "actor_name": username,
            "actor_real_name": real_name,
            "operator_ip": log.operator_ip,
            "operator_user_agent": log.operator_user_agent,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {
        "code": 0,
        "message": "操作成功",
        "data": {
            "list": items,
            "total": total or 0,
            "pageSize": pageSize,
            "currentPage": currentPage,
        },
    }


# ── 日志检索（Loki 代理） ────────────────────────────────────


def _build_logql(
    service: str | None,
    level: str | None,
    keyword: str | None,
    request_id: str | None,
) -> str:
    """构造 Loki LogQL：{namespace="unionagents",service=...,level=...} |= "kw" |= "rid"

    - 不传 service 时默认只查 manager+gateway（litellm 等无 service label 的 plain text
      行被自动排除）。
    - 过滤 uvicorn.access 默认 access log（与 manager.access/gateway.access 结构化日志
      重复，同一请求两条日志）。
    - JSON 解析后按 path 字段精确过滤 /health /metrics 探针和 Prometheus 抓取噪音
     （kubelet 每 5-30s 一次的 livenessProbe/readinessProbe）。
    """
    selectors = ['namespace="unionagents"']
    if service:
        selectors.append(f'service="{service}"')
    else:
        selectors.append('service=~"manager|gateway"')
    if level:
        selectors.append(f'level="{level}"')
    expr = "{" + ",".join(selectors) + "}"
    # 过滤 uvicorn 默认 access log（logger="uvicorn.access"）
    expr += ' != "uvicorn.access"'
    if keyword:
        expr += f' |= "{keyword}"'
    if request_id:
        expr += f' |= "{request_id}"'
    # JSON 解析后按 path 字段精确过滤探针/metrics 噪音
    expr += ' | json | path != "/health" | path != "/metrics"'
    return expr


def _parse_loki_log_line(line: str) -> dict[str, Any]:
    """解析 Loki 返回的 JSON 日志行，提取关键字段。raw 保留原始 JSON。"""
    try:
        parsed = json.loads(line)
    except Exception:
        return {
            "raw": line, "message": line,
            "service": "", "level": "", "request_id": "", "ts": "",
        }
    return {
        "ts": parsed.get("timestamp", ""),
        "service": parsed.get("service", ""),
        "level": parsed.get("level", ""),
        "logger": parsed.get("logger", ""),
        "message": parsed.get("message", ""),
        "request_id": parsed.get("request_id", ""),
        "user_id": parsed.get("user_id", ""),
        "raw": parsed,
    }


@router.get("/logs/search")
async def search_logs(
    _: User = Depends(get_current_user),
    service: str | None = Query(None, description="manager/gateway"),
    level: str | None = Query(None, description="INFO/WARN/ERROR"),
    request_id: str | None = Query(None),
    keyword: str | None = Query(None),
    time_from: datetime | None = Query(None),
    time_to: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Loki 日志搜索代理。manager 跨 ns 调 loi.monitoring:3100，前端不直连 Loki。
    Loki 不可达时返回 503，前端降级提示。"""
    loki_url = settings.loki_internal_url
    if not loki_url:
        raise HTTPException(status_code=503, detail="Loki 地址未配置")

    expr = _build_logql(service, level, keyword, request_id)

    # 时间范围：默认最近 1h，最大 24h
    now = datetime.now(UTC)
    end_ts = time_to or now
    start_ts = time_from or (now - timedelta(hours=1))
    if (end_ts - start_ts) > timedelta(hours=24):
        raise HTTPException(
            status_code=400,
            detail="时间范围超过 24h，请缩小范围或直接在 Grafana 中查询",
        )

    start_ns = int(start_ts.timestamp() * 1_000_000_000)
    end_ns = int(end_ts.timestamp() * 1_000_000_000)

    params = {
        "query": expr,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(limit),
        "direction": "backward",  # Loki 只支持 forward/backward，backward=最新在前
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{loki_url}/loki/api/v1/query_range",
                params=params,
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Loki 查询失败: {resp.status_code} {resp.text[:200]}",
                )
            data = resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Loki 查询超时（10s）")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Loki 服务不可达，请检查 monitoring 命名空间")
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Loki 查询异常: {e}")

    # Loki 返回 {streams: [{values: [[ts, line], ...]}]}
    items = []
    for stream in data.get("data", {}).get("result", []) or []:
        for ts, line in stream.get("values", []):
            parsed = _parse_loki_log_line(line)
            parsed["loki_ts"] = ts
            items.append(parsed)

    return {
        "items": items,
        "total": len(items),
        "query": expr,
        "grafana_url": _grafana_url(),
    }

