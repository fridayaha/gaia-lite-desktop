"""Dify 外接模式用量反查 collector。

从 Langfuse 拉 Dify trace，从 Dify Console API 拿 per-day per-app cost，
按 agent 维度平铺成明细 list，供 observability.get_usage 注入 fake log 合并到
by_agent / by_group / by_model / trend。

## 数据源

- **token + model**：Langfuse trace + observation
  - `message_trace`（agent-chat/chat/completion 模式）：observation 因 Dify #37824 bug
    100% 丢失，从 `trace.input.message_tokens`/`answer_tokens`/`total_tokens` 拿 token，
    从 `trace.metadata.ls_model_name` 拿 model
  - `workflow_trace`（workflow 模式）：顶层 trace 无 model 信息（编排会调多个 LLM 节点），
    从该 trace 关联的 GENERATION observation 反查 — 每条 GENERATION observation 单独成
    一条 detail（model=obs.model, token=obs.usage.input/output/total）；
    若该 workflow trace 无 GENERATION observation（edge case），fallback 用
    `trace.metadata.total_tokens` + model="unknown"
- **cost（USD）**：Dify Console API `GET /apps/<app_id>/statistics/token-costs`
  - Dify plugin 调真实 LLM 时已用 plugin YAML pricing 算好 cost 存到 `messages.total_price`
  - 该端点按 day 聚合返回 `total_price`（USD），manager 按 (app_id, date) 平摊到当天该 app
    的每条 detail（包括 workflow 拆出的多条 detail）
  - workflow 模式 Dify 1.14.2 不返回 total_price（workflow_runs 不存 cost），cost=0 降级

## 鉴权

- Langfuse：EngineConfig.langfuse_host + public_key + secret_key（用户在 Dify workspace 配同款）
- Dify Console API：EngineConfig.admin_email + admin_password（DifyConsoleClient 自动登录 + 30 天缓存）
  - 未配 admin 账号 → cost 降级 0，token 仍从 Langfuse 拿

## agent_meta_map

{app_id: {agent_id, name, group_id}} 由调用方预先查好传入，
collector 不直接查 DB。找不到对应 agent 的 trace 直接跳过（用户明确：
"dify 应用在我们上面没有对应智能体实例，不用管"）。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.crypto import decrypt_credential
from app.core.dify_console_client import DifyConsoleClient
from app.models import EngineConfig
from app.services.langfuse_client import LangfuseConfig, list_observations, list_traces

logger = logging.getLogger(__name__)

_MAX_TRACES = 1000


@dataclass
class DifyTraceDetail:
    """单条 trace 或单条 GENERATION observation 维度的 Dify 用量明细。

    - message_trace：1 trace → 1 detail（trace 维度，observation 全丢走 trace.input 反查）
    - workflow_trace：1 trace → N detail（每条 GENERATION observation 一条，N = LLM 节点数；
      若该 trace 无 GENERATION observation，fallback 1 条 detail model="unknown"）

    fake log 注入 filtered_logs 后，每条对应一次 conversation_count。
    """

    agent_id: str
    agent_name: str
    group_id: str
    dify_app_id: str  # 保留 debug
    trace_id: str
    session_id: str | None  # trace.sessionId，用于 activeUsers distinct
    timestamp: str  # ISO 8601 UTC，trace.createdAt
    model: str  # message_trace: metadata.ls_model_name；workflow_trace: obs.model
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float  # 按 (app_id, date) 平摊到每条 detail


def build_langfuse_config(cfg: EngineConfig) -> LangfuseConfig | None:
    """从 EngineConfig 构造 LangfuseConfig。secret_key 用 Fernet 解密。

    返回 None 表示 EngineConfig 未配置 Langfuse 集成（调用方应提示用户先配置）。
    """
    if not (
        cfg.langfuse_host
        and cfg.langfuse_public_key
        and cfg.langfuse_secret_key_encrypted
    ):
        return None
    try:
        secret = decrypt_credential(cfg.langfuse_secret_key_encrypted)
    except Exception as e:
        logger.warning(f"EngineConfig {cfg.id} langfuse secret decrypt failed: {e}")
        return None
    return LangfuseConfig(
        base_url=cfg.langfuse_host,
        public_key=cfg.langfuse_public_key,
        secret_key=secret,
    )


async def _fetch_all_traces(
    from_ts: str,
    to_ts: str,
    lf_config: LangfuseConfig,
) -> list[dict]:
    """拉时间窗口内的全部 trace（不带 metadata 过滤，分页）。

    Langfuse v3 自建版的 metadata[<k>]=<v> 服务端过滤实测不生效（v3.198.0），
    返回全部 trace。改为客户端按 trace.metadata.app_id 分组。
    """
    traces: list[dict] = []
    offset = 0
    while True:
        resp = await list_traces(
            from_ts=from_ts,
            to_ts=to_ts,
            limit=100,
            offset=offset,
            config=lf_config,
        )
        if not resp or not resp.get("data"):
            break
        traces.extend(resp["data"])
        meta = resp.get("meta") or {}
        total_pages = meta.get("totalPages", 1)
        if meta.get("page", 1) >= total_pages:
            break
        offset += 100
        if offset >= _MAX_TRACES:
            break
    return traces


def _group_traces_by_app_id(traces: list[dict]) -> dict[str, list[dict]]:
    """按 trace.metadata.app_id 客户端分组（debug / 测试用）。

    Dify 1.x Langfuse 集成上报 trace 时 metadata 含 app_id。
    无 app_id（如 Hermes trace）的跳过。
    """
    grouped: dict[str, list[dict]] = {}
    for t in traces:
        md = t.get("metadata") or {}
        if not isinstance(md, dict):
            continue
        app_id = md.get("app_id")
        if not app_id:
            continue
        grouped.setdefault(str(app_id), []).append(t)
    return grouped


async def _fetch_all_observations(
    from_ts: str,
    to_ts: str,
    lf_config: LangfuseConfig,
) -> list[dict]:
    """拉时间窗内全部 GENERATION observation（按 trace_id 关联回 workflow trace）。

    用于 workflow trace 反查每个 LLM 节点的 model + token（workflow 顶层 trace 无 model）。
    message_trace 的 observation 因 #37824 bug 100% 丢失，拉 GENERATION 也不会带回 message
    部分，所以这里只服务 workflow。

    返回 list[observation]，每个 observation 含 traceId/model/usage 等字段。
    """
    observations: list[dict] = []
    offset = 0
    while True:
        batch = await list_observations(
            type="GENERATION",
            from_ts=from_ts,
            to_ts=to_ts,
            limit=100,
            offset=offset,
            config=lf_config,
        )
        if not batch:
            break
        observations.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
        if offset >= _MAX_TRACES:
            break
    return observations


def _iso_to_date_str(ts: str | None) -> str | None:
    """ISO 8601 timestamp → 'YYYY-MM-DD'（UTC），用于按天聚合 cost 平摊。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def _normalize_dify_model(provider: str | None, model: str | None) -> str:
    """把 Dify 的 (ls_provider, ls_model_name) 归一化为 LiteLLM 风格 `provider/model`。

    Dify ls_provider = "langgenius/deepseek/deepseek"（plugin 路径，3 段），
    ls_model_name = "deepseek-chat"。LiteLLM spend_logs.model = "deepseek/deepseek-chat"。
    为让 by_model 把 Dify 和 LiteLLM 同一 model 合并到同一个桶，统一成 `provider/model` 格式。

    - provider 缺失或无法解析 → 返回 model 原值（不带前缀）
    - model 缺失 → 返回 "unknown"
    """
    m = str(model or "unknown")
    if not provider:
        return m
    p = str(provider).split("/")[-1]
    if not p:
        return m
    return f"{p}/{m}"


async def _fetch_app_costs(
    engine_config: EngineConfig,
    agent_meta_map: dict[str, dict],
    from_ts: str,
    to_ts: str,
) -> dict[str, dict[str, float]]:
    """调 Dify Console API 拿 per-day per-app cost。

    返回 {app_id: {date_str: total_cost_usd}}。

    需要 EngineConfig 配 admin_email + admin_password。未配则返回空 dict（cost 降级 0）。
    """
    if not engine_config.admin_email or not engine_config.admin_password_encrypted:
        logger.warning(
            f"EngineConfig {engine_config.id} 未配 admin 账号，Dify cost 降级 0"
        )
        return {}

    try:
        password = decrypt_credential(engine_config.admin_password_encrypted)
    except Exception as e:
        logger.warning(
            f"EngineConfig {engine_config.id} admin password decrypt failed: {e}"
        )
        return {}

    client = DifyConsoleClient(
        base_url=engine_config.base_url or "",
        email=engine_config.admin_email,
        password=password,
    )

    try:
        try:
            apps = await client.list_apps()
        except Exception as e:
            logger.warning(f"Dify list_apps failed for EngineConfig {engine_config.id}: {e}")
            return {}
        app_modes = {str(a.get("id")): a.get("mode") for a in apps}

        try:
            start_dt = datetime.fromisoformat(from_ts.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(to_ts.replace("Z", "+00:00"))
        except Exception:
            start_dt = datetime.now(timezone.utc) - timedelta(days=30)
            end_dt = datetime.now(timezone.utc)
        start_str = start_dt.strftime("%Y-%m-%d %H:%M")
        end_str = end_dt.strftime("%Y-%m-%d %H:%M")

        app_costs: dict[str, dict[str, float]] = {}
        for app_id in agent_meta_map.keys():
            mode = app_modes.get(app_id)
            if not mode:
                continue  # agent 配的 app_id 在 Dify 平台找不到，跳过
            try:
                rows = await client.get_app_token_costs(app_id, mode, start_str, end_str)
            except Exception as e:
                logger.warning(
                    f"get_app_token_costs app={app_id} mode={mode} failed: {e}"
                )
                continue
            for row in rows:
                date_str = row.get("date")
                total_price = row.get("total_price")
                if not date_str or total_price is None:
                    continue  # workflow 模式无 total_price
                try:
                    app_costs.setdefault(app_id, {})[str(date_str)] = float(total_price)
                except (ValueError, TypeError):
                    continue
        return app_costs
    finally:
        await client.close()


def _collect_trace_details(
    agent_meta_map: dict[str, dict],
    traces: list[dict],
    workflow_observations: list[dict],
    app_costs: dict[str, dict[str, float]],
) -> list[DifyTraceDetail]:
    """从 trace + workflow observation 提取 token + model，按 (app_id, date) 平摊 cost。

    - message_trace: token=trace.input.message_tokens/answer_tokens, model=trace.metadata.ls_model_name
      （observation 因 #37824 bug 100% 丢失，走 trace 顶层字段反查）
    - workflow_trace: 从该 trace 关联的 GENERATION observation 反查 — 每条 observation 单独成一条 detail
      （model=obs.model, token=obs.usage.input/output/total）；
      无 GENERATION observation 时 fallback 用 trace.metadata.total_tokens + model="unknown"
    - cost 按 (app_id, date) 平摊到每条 detail（包括 workflow 拆出的多条），
      分母 = 当天该 app 的 detail 数，避免 workflow 多 detail 重复计算
    - 找不到 trace.createdAt / metadata.app_id / agent 的 trace 跳过
    """
    obs_by_trace: dict[str, list[dict]] = {}
    for o in workflow_observations:
        tid = o.get("traceId")
        if tid:
            obs_by_trace.setdefault(str(tid), []).append(o)

    raw_details: list[dict[str, Any]] = []
    for t in traces:
        md = t.get("metadata") or {}
        if not isinstance(md, dict):
            continue
        app_id = md.get("app_id")
        if not app_id:
            continue
        agent_meta = agent_meta_map.get(str(app_id))
        if not agent_meta:
            continue

        created_at = t.get("createdAt")
        if not created_at:
            continue
        timestamp = created_at if isinstance(created_at, str) else str(created_at)
        trace_id = str(t.get("id") or "")
        trace_name = t.get("name") or ""
        session_id = t.get("sessionId")
        session_id = str(session_id) if session_id else None

        if trace_name == "message":
            inp = t.get("input") or {}
            if not isinstance(inp, dict):
                continue
            prompt = int(inp.get("message_tokens") or 0)
            completion = int(inp.get("answer_tokens") or 0)
            total = int(inp.get("total_tokens") or (prompt + completion))
            model = _normalize_dify_model(md.get("ls_provider"), md.get("ls_model_name"))
            raw_details.append({
                "agent_meta": agent_meta,
                "app_id": str(app_id),
                "trace_id": trace_id,
                "session_id": session_id,
                "timestamp": timestamp,
                "model": model,
                "prompt": prompt,
                "completion": completion,
                "total": total,
            })
        elif trace_name == "workflow":
            obs_list = obs_by_trace.get(trace_id) or []
            gen_obs = [o for o in obs_list if o.get("type") == "GENERATION"]
            if gen_obs:
                for o in gen_obs:
                    u = o.get("usage") or {}
                    if not isinstance(u, dict):
                        u = {}
                    prompt = int(u.get("input") or 0)
                    completion = int(u.get("output") or 0)
                    total = int(u.get("total") or (prompt + completion))
                    o_md = o.get("metadata") or {}
                    if not isinstance(o_md, dict):
                        o_md = {}
                    # 优先用 metadata.model_provider + model_name 拼接成 provider/model
                    model = _normalize_dify_model(
                        o_md.get("model_provider"),
                        o_md.get("model_name") or o.get("model"),
                    )
                    raw_details.append({
                        "agent_meta": agent_meta,
                        "app_id": str(app_id),
                        "trace_id": trace_id,
                        "session_id": session_id,
                        "timestamp": timestamp,
                        "model": model,
                        "prompt": prompt,
                        "completion": completion,
                        "total": total,
                    })
            else:
                # fallback：workflow trace 无 GENERATION observation（异常情况）
                total = int(md.get("total_tokens") or 0)
                raw_details.append({
                    "agent_meta": agent_meta,
                    "app_id": str(app_id),
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "timestamp": timestamp,
                    "model": "unknown",
                    "prompt": 0,
                    "completion": total,
                    "total": total,
                })
        else:
            continue

    # 按 (app_id, date) 算 detail 数（cost 平摊分母）
    detail_counts: dict[tuple[str, str], int] = {}
    for r in raw_details:
        date_str = _iso_to_date_str(r["timestamp"])
        if not date_str:
            continue
        key = (r["app_id"], date_str)
        detail_counts[key] = detail_counts.get(key, 0) + 1

    details: list[DifyTraceDetail] = []
    for r in raw_details:
        date_str = _iso_to_date_str(r["timestamp"])
        cost = 0.0
        if date_str:
            app_cost_map = app_costs.get(r["app_id"]) or {}
            day_cost = app_cost_map.get(date_str) or 0.0
            count = detail_counts.get((r["app_id"], date_str), 1)
            cost = day_cost / count if count > 0 else 0.0
        am = r["agent_meta"]
        details.append(
            DifyTraceDetail(
                agent_id=str(am["agent_id"]),
                agent_name=str(am.get("name") or ""),
                group_id=str(am.get("group_id") or ""),
                dify_app_id=r["app_id"],
                trace_id=r["trace_id"],
                session_id=r.get("session_id"),
                timestamp=r["timestamp"],
                model=r["model"],
                prompt_tokens=r["prompt"],
                completion_tokens=r["completion"],
                total_tokens=r["total"],
                cost_usd=round(cost, 6),
            )
        )

    return details


async def collect_dify_usage(
    engine_config: EngineConfig,
    agent_meta_map: dict[str, dict],
    days: int = 30,
) -> list[DifyTraceDetail]:
    """从 Langfuse 拉 Dify trace + workflow observation，从 Dify Console API 拿 per-day per-app cost，
    按 agent 平铺。

    流程：
    1. agent_meta_map 为空直接返回 []（无 PUBLISHED Dify agent）
    2. 校验 EngineConfig 配了 Langfuse 凭据
    3. 并发拉：时间窗内全部 traces + 全部 GENERATION observations + per-day per-app cost
    4. message_trace 从 trace.input 拿 token + metadata.ls_model_name 拿 model
       （observation 因 #37824 bug 100% 丢失）
    5. workflow_trace 从关联的 GENERATION observation 反查每个 LLM 节点的 model + token
       （每条 observation 单独成一条 detail）；无 observation 时 fallback trace.metadata.total_tokens
    6. cost 按 (app_id, date) 平摊到每条 detail（分母 = detail 数）
    7. 返回 list[DifyTraceDetail]

    agent_meta_map 由调用方预先查好（observability._resolve_dify_agents）。

    Raises:
        ValueError: EngineConfig 未配 Langfuse
    """
    if not agent_meta_map:
        return []

    lf_config = build_langfuse_config(engine_config)
    if lf_config is None:
        raise ValueError(
            "EngineConfig 未配置 Langfuse 集成（langfuse_host/public_key/secret_key）"
        )

    now = datetime.now(timezone.utc)
    from_ts = (now - timedelta(days=days)).isoformat()
    to_ts = now.isoformat()

    traces, workflow_observations, app_costs = await asyncio.gather(
        _fetch_all_traces(from_ts, to_ts, lf_config),
        _fetch_all_observations(from_ts, to_ts, lf_config),
        _fetch_app_costs(engine_config, agent_meta_map, from_ts, to_ts),
    )

    return _collect_trace_details(agent_meta_map, traces, workflow_observations, app_costs)
