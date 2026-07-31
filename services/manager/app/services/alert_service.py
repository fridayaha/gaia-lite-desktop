"""告警规则评估 service — 从 /alerts 端点抽出，供 endpoint + 后台轮询共用。

evaluate_rules: 读 DB 规则 + 按 5 大类分派 evaluator，返回触发的告警列表（不写 DB、不发通知）
check_and_notify: 后台轮询入口，evaluate_rules + 去重 + 发通知 + 写 AlertEvent

5 个 evaluator（按 category 分派）：
  - tracing: Langfuse trace 迭代（沿用旧逻辑）
  - resource: monitoring_service.get_resource_summary → 4 标量字段比阈值
  - service_health: monitoring_service.get_service_health_summary → 6 服务状态
  - usage: monitoring_service.get_usage_summary → 今日/当月/按 agent
  - call_analysis: monitoring_service.get_call_quality_summary → overall 聚合

数据源未配置或不可达 → evaluator 返回空列表 + log warning。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertChannel, AlertEvent, AlertRule
from app.schemas import (
    ALERT_CATEGORY_RULE_TYPES,
    ALERT_RULE_TYPES_INVERTED,
    ALERT_RULE_TYPES_NO_THRESHOLD,
)
from app.services import langfuse_client
from app.services import monitoring_service
from app.services.notify_service import notify_channels
from pkg.common.config import settings

logger = logging.getLogger(__name__)

# 去重窗口：同 rule_id + trace_id 在 1h 内不重复通知
DEDUP_WINDOW_HOURS = 1

# A 类规则（持续状态可自动恢复）：指标降下来即 resolved，后台轮询自动检测。
# B 类（tracing）是单次 trace 异常不可恢复；C 类（usage）是累积值周期结束自然清零。
AUTO_RESOLVABLE_RULE_TYPES: set[str] = {
    # resource
    "high_cpu", "high_memory", "high_disk", "pod_restart",
    # service_health
    "service_down", "high_p95_latency", "low_uptime",
    # call_analysis
    "low_success_rate", "high_p95_call_latency", "high_avg_tokens_per_request",
}


# ── rule_type → 评估字段映射 ─────────────────────────────────

# resource 类：summary 中的字段名 + 单位 + 是否反向比较
_RESOURCE_FIELD_MAP: dict[str, tuple[str, str]] = {
    "high_cpu": ("cluster_cpu_pct", "%"),
    "high_memory": ("cluster_memory_pct", "%"),
    "high_disk": ("max_disk_pct", "%"),
    "pod_restart": ("max_pod_restarts", "次"),
}

# service_health 类：rule_type → (检查维度, 字段, 单位)
# service_down：status=="down" 即触发（无阈值）
# high_p95_latency：p95_ms > threshold
# low_uptime：uptime_pct < threshold（反向）
_SERVICE_HEALTH_FIELD_MAP: dict[str, tuple[str, str]] = {
    "high_p95_latency": ("p95_ms", "ms"),
    "low_uptime": ("uptime_pct", "%"),
}


async def evaluate_rules(db: AsyncSession) -> list[dict[str, Any]]:
    """读 DB 规则 + 按 5 大类分派 evaluator，返回触发的告警列表。

    返回每条告警 dict：
    {rule_id, rule_name, rule_type, category, trace_id?, agent_id?, severity, message, created_at}
    通知渠道不挂在规则上（独立成 alert_channels 实体），由 check_and_notify 查 alert_channels 表。
    数据源未配置或不可达时该类 evaluator 返回空列表。
    """
    rules_result = await db.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))
    all_rules = rules_result.scalars().all()
    if not all_rules:
        return []

    # 按 category 分组
    rules_by_cat: dict[str, list[AlertRule]] = {}
    for r in all_rules:
        rules_by_cat.setdefault(r.category, []).append(r)

    alerts: list[dict[str, Any]] = []
    # 5 大类分派
    alerts += await _evaluate_tracing_rules(db, rules_by_cat.get("tracing", []))
    alerts += await _evaluate_resource_rules(rules_by_cat.get("resource", []))
    alerts += await _evaluate_service_health_rules(rules_by_cat.get("service_health", []))
    alerts += await _evaluate_usage_rules(rules_by_cat.get("usage", []))
    alerts += await _evaluate_call_analysis_rules(rules_by_cat.get("call_analysis", []))
    return alerts


# ── 1. tracing 链路追踪 evaluator（沿用旧逻辑） ────────────────

async def _evaluate_tracing_rules(db: AsyncSession, rules: list[AlertRule]) -> list[dict[str, Any]]:
    """Langfuse trace 迭代：error_trace / high_latency / high_tokens。

    沿用 0.8.55 之前的逻辑，未改动。
    """
    if not rules:
        return []
    if not langfuse_client.is_configured():
        return []

    rules_by_type = {r.rule_type: r for r in rules}
    error_trace_enabled = "error_trace" in rules_by_type
    latency_threshold = rules_by_type["high_latency"].threshold if "high_latency" in rules_by_type else None
    token_threshold = rules_by_type["high_tokens"].threshold if "high_tokens" in rules_by_type else None

    resp = await langfuse_client.list_traces(limit=100)
    if resp is None:
        return []
    traces = resp.get("data") or []

    import asyncio

    observations_list = await asyncio.gather(
        *[langfuse_client.list_observations(t.get("id")) for t in traces],
        return_exceptions=True,
    )

    from app.api.observability import _trace_latency_ms, _trace_status, _trace_token_total

    alerts: list[dict[str, Any]] = []
    for t, obs in zip(traces, observations_list):
        if isinstance(obs, Exception):
            obs = []
        aid = t.get("userId") or "unknown"
        trace_id = t.get("id")
        created_at = t.get("createdAt") or t.get("timestamp")
        latency = _trace_latency_ms(t, obs)
        tokens = _trace_token_total(t, obs)
        status = _trace_status(t, obs)

        if error_trace_enabled and status == "error":
            rule = rules_by_type["error_trace"]
            alerts.append(_build_alert(rule, trace_id=trace_id, agent_id=aid,
                                       message="请求返回错误或 observation 级别为 ERROR",
                                       created_at=created_at))
        if latency_threshold is not None and latency is not None and latency > latency_threshold:
            rule = rules_by_type["high_latency"]
            alerts.append(_build_alert(rule, trace_id=trace_id, agent_id=aid,
                                       message=f"延迟 {latency}ms 超阈值 {latency_threshold}ms",
                                       created_at=created_at))
        if token_threshold is not None and tokens > token_threshold:
            rule = rules_by_type["high_tokens"]
            alerts.append(_build_alert(rule, trace_id=trace_id, agent_id=aid,
                                       message=f"Token {tokens} 超阈值 {token_threshold}",
                                       created_at=created_at))
    return alerts


# ── 2. resource 资源监控 evaluator ───────────────────────────

async def _evaluate_resource_rules(rules: list[AlertRule]) -> list[dict[str, Any]]:
    """集群 CPU/内存/磁盘 + Pod 重启。

    summary = {cluster_cpu_pct, cluster_memory_pct, max_disk_pct, max_pod_restarts}
    每个 rule_type 取对应字段，反向比较规则无（low_* 不在此类）。
    """
    if not rules:
        return []

    summary = await monitoring_service.get_resource_summary()
    if summary is None:
        return []

    alerts: list[dict[str, Any]] = []
    for rule in rules:
        if rule.rule_type in ALERT_RULE_TYPES_NO_THRESHOLD:
            continue  # resource 类无 no-threshold 规则
        if rule.threshold is None:
            continue
        field_name, unit = _RESOURCE_FIELD_MAP.get(rule.rule_type, ("", ""))
        if not field_name:
            continue
        value = summary.get(field_name)
        if value is None:
            continue
        inverted = rule.rule_type in ALERT_RULE_TYPES_INVERTED
        triggered = (value < rule.threshold) if inverted else (value > rule.threshold)
        if triggered:
            alerts.append(_build_alert(
                rule,
                agent_id="cluster",
                message=f"{rule.name}：{field_name}={value}{unit} "
                        f"{'低于' if inverted else '超'}阈值 {rule.threshold}{unit}",
                created_at=datetime.now(UTC).isoformat(),
            ))
    return alerts


# ── 3. service_health 服务健康 evaluator ─────────────────────

async def _evaluate_service_health_rules(rules: list[AlertRule]) -> list[dict[str, Any]]:
    """6 个核心服务状态检查。

    service_down：任一服务 status=="down" 即触发（无阈值），每个 down 服务单独生成一条 alert
    high_p95_latency：任一服务 p95_ms > threshold
    low_uptime：任一服务 uptime_pct < threshold（反向）
    """
    if not rules:
        return []

    summary = await monitoring_service.get_service_health_summary()
    if summary is None:
        return []

    services = summary.get("services") or []
    alerts: list[dict[str, Any]] = []
    rule_by_type = {r.rule_type: r for r in rules}

    # service_down：任一服务 down 即触发
    down_rule = rule_by_type.get("service_down")
    if down_rule:
        for svc in services:
            if svc.get("status") == "down":
                alerts.append(_build_alert(
                    down_rule,
                    agent_id=svc.get("name", "unknown"),
                    message=f"服务 {svc.get('name')} 状态为 down",
                    created_at=datetime.now(UTC).isoformat(),
                ))

    # high_p95_latency / low_uptime：遍历每个服务
    for rule in rules:
        if rule.rule_type == "service_down":
            continue
        if rule.threshold is None:
            continue
        field_name, unit = _SERVICE_HEALTH_FIELD_MAP.get(rule.rule_type, ("", ""))
        if not field_name:
            continue
        inverted = rule.rule_type in ALERT_RULE_TYPES_INVERTED
        for svc in services:
            value = svc.get(field_name)
            if value is None:
                continue
            triggered = (value < rule.threshold) if inverted else (value > rule.threshold)
            if triggered:
                alerts.append(_build_alert(
                    rule,
                    agent_id=svc.get("name", "unknown"),
                    message=f"{rule.name}：服务 {svc.get('name')} {field_name}={value}{unit} "
                            f"{'低于' if inverted else '超'}阈值 {rule.threshold}{unit}",
                    created_at=datetime.now(UTC).isoformat(),
                ))
    return alerts


# ── 4. usage 用量分析 evaluator ──────────────────────────────

async def _evaluate_usage_rules(rules: list[AlertRule]) -> list[dict[str, Any]]:
    """LiteLLM spend_logs 当日/当月/按 agent 聚合。

    high_daily_tokens：today_tokens > threshold
    high_monthly_cost：monthly_cost > threshold
    high_agent_tokens：by_agent[].total_tokens > threshold，每个超阈值的 agent 单独生成 alert
    """
    if not rules:
        return []

    summary = await monitoring_service.get_usage_summary()
    if summary is None:
        return []

    alerts: list[dict[str, Any]] = []
    now_iso = datetime.now(UTC).isoformat()
    for rule in rules:
        if rule.threshold is None:
            continue
        if rule.rule_type == "high_daily_tokens":
            value = summary.get("today_tokens", 0)
            if value > rule.threshold:
                alerts.append(_build_alert(
                    rule, agent_id="global",
                    message=f"{rule.name}：今日 token {value} 超阈值 {rule.threshold}",
                    created_at=now_iso,
                ))
        elif rule.rule_type == "high_monthly_cost":
            value = summary.get("monthly_cost", 0)
            if value > rule.threshold:
                alerts.append(_build_alert(
                    rule, agent_id="global",
                    message=f"{rule.name}：当月费用 {value} USD 超阈值 {rule.threshold} USD",
                    created_at=now_iso,
                ))
        elif rule.rule_type == "high_agent_tokens":
            for agent in summary.get("by_agent", []):
                aid = agent.get("agent_id", "unknown")
                tokens = agent.get("total_tokens", 0)
                if tokens > rule.threshold:
                    alerts.append(_build_alert(
                        rule, agent_id=aid,
                        message=f"{rule.name}：智能体 {aid[:12]} token {tokens} 超阈值 {rule.threshold}",
                        created_at=now_iso,
                    ))
    return alerts


# ── 5. call_analysis 调用分析 evaluator ──────────────────────

async def _evaluate_call_analysis_rules(rules: list[AlertRule]) -> list[dict[str, Any]]:
    """Langfuse traces 全局聚合（limit=100）。

    low_success_rate：overall.success_rate < threshold（反向，%）
    high_p95_call_latency：overall.p95_latency_ms > threshold
    high_avg_tokens_per_request：overall.avg_tokens_per_request > threshold
    """
    if not rules:
        return []

    summary = await monitoring_service.get_call_quality_summary()
    if summary is None:
        return []

    overall = summary.get("overall") or {}
    alerts: list[dict[str, Any]] = []
    now_iso = datetime.now(UTC).isoformat()
    for rule in rules:
        if rule.threshold is None:
            continue
        if rule.rule_type == "low_success_rate":
            value = overall.get("success_rate", 0)
            # success_rate 是 0-1 小数，threshold 是 0-100 整数，统一乘 100 比较
            value_pct = value * 100 if value <= 1 else value
            if value_pct < rule.threshold:
                alerts.append(_build_alert(
                    rule, agent_id="global",
                    message=f"{rule.name}：成功率 {value_pct:.2f}% 低于阈值 {rule.threshold}%",
                    created_at=now_iso,
                ))
        elif rule.rule_type == "high_p95_call_latency":
            value = overall.get("p95_latency_ms", 0)
            if value > rule.threshold:
                alerts.append(_build_alert(
                    rule, agent_id="global",
                    message=f"{rule.name}：p95 延迟 {value}ms 超阈值 {rule.threshold}ms",
                    created_at=now_iso,
                ))
        elif rule.rule_type == "high_avg_tokens_per_request":
            value = overall.get("avg_tokens_per_request", 0)
            if value > rule.threshold:
                alerts.append(_build_alert(
                    rule, agent_id="global",
                    message=f"{rule.name}：均 token {value} 超阈值 {rule.threshold}",
                    created_at=now_iso,
                ))
    return alerts


# ── alert dict 构造 helper ──────────────────────────────────

def _build_alert(
    rule: AlertRule,
    *,
    trace_id: str | None = None,
    agent_id: str | None = None,
    message: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """构造 alert dict（沿用 0.8.55 shape + 新增 category 字段）。"""
    return {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "rule_type": rule.rule_type,
        "category": rule.category,
        "trace_id": trace_id,
        "agent_id": agent_id,
        "severity": rule.severity,
        "message": message,
        "created_at": created_at or datetime.now(UTC).isoformat(),
    }


# ── 去重 + 通知 + 写事件（沿用 0.8.55 逻辑） ─────────────────

async def _is_duplicate(
    db: AsyncSession,
    rule_id,
    trace_id: str | None,
    agent_id: str | None = None,
) -> bool:
    """同 rule_id + trace_id（tracing 类）或 rule_id + agent_id（非 tracing 类）的 firing 事件在 DEDUP_WINDOW_HOURS 内是否存在。

    tracing 类告警有 trace_id，按 trace_id 精确去重（同一 trace 1h 内不重复）；
    非 tracing 类告警（resource/service_health/usage/call_analysis）无 trace_id，按
    rule_id + agent_id 去重——避免 resource 类告警每次轮询 120s 都生成新事件导致风暴。

    只查 status='firing' 的事件——resolved 的事件不参与去重，下次同 rule+agent 再触发
    会生成新事件（恢复后又触发应记为新一次故障）。acknowledged 事件也不参与去重，
    人确认过 → 下次再触发应再次通知。
    """
    since = datetime.now(UTC) - timedelta(hours=DEDUP_WINDOW_HOURS)
    base_query = (
        select(AlertEvent.id)
        .where(AlertEvent.rule_id == rule_id)
        .where(AlertEvent.status == "firing")
        .where(AlertEvent.created_at >= since)
    )
    if trace_id is not None:
        # tracing 类：按 rule_id + trace_id 精确去重
        result = await db.execute(base_query.where(AlertEvent.trace_id == trace_id).limit(1))
    else:
        # 非 tracing 类：按 rule_id + agent_id 去重（agent_id 可能是 None，需要 IS NULL）
        agent_filter = (
            AlertEvent.agent_id.is_(None) if agent_id is None else AlertEvent.agent_id == agent_id
        )
        result = await db.execute(base_query.where(agent_filter).limit(1))
    return result.scalar_one_or_none() is not None


async def _channels_for_rule(db: AsyncSession, rule_id) -> list[dict[str, Any]]:
    """查询订阅了该规则的所有启用渠道 + 所有 subscribed_all=true 的渠道。

    返回 notify_service.notify_channels 期望的 dict 列表：[{type, name, webhook_url/to, ...}]。
    一个渠道订阅多条规则时，会因 AlertChannel.rules.any(rule_id) 命中一次；
    subscribed_all=true 的渠道对任何规则都命中（运行时短路，关联表无行也命中）。
    """
    from uuid import UUID

    q = await db.execute(
        select(AlertChannel).where(
            or_(
                AlertChannel.rules.any(AlertRule.id == rule_id),
                AlertChannel.subscribed_all.is_(True),
            ),
            AlertChannel.enabled.is_(True),
        )
    )
    channels = q.scalars().all()
    payload: list[dict[str, Any]] = []
    for c in channels:
        # config 存 {webhook_url} 或 {to}，展开成 notify_service 期望的扁平 dict
        entry: dict[str, Any] = {
            "type": c.channel_type,
            "name": c.name,
            **(c.config or {}),
        }
        payload.append(entry)
    return payload


def _format_alert_text(alert: dict[str, Any]) -> str:
    """飞书/钉钉/企微文本消息内容（按 category 决定链接）。

    首行固定含「告警」二字——飞书自定义机器人关键词校验需匹配（用户常设「告警」
    为关键词），缺这二字会导致所有告警被飞书拒收（code 19024 Key Words Not Found）。
    """
    severity_label = "严重" if alert["severity"] == "critical" else "警告"
    category = alert.get("category", "tracing")
    agent_id = alert.get("agent_id") or "global"
    link = _alert_link(alert)
    return (
        f"🚨 告警 [{severity_label}] {alert['rule_name']}\n"
        f"类别: {_category_label(category)}\n"
        f"对象: {agent_id[:12] if agent_id else 'global'}\n"
        f"消息: {alert['message']}\n"
        f"时间: {alert.get('created_at') or datetime.now(UTC).isoformat()}\n"
        f"链接: {link}"
    )


def _alert_link(alert: dict[str, Any]) -> str:
    """按 category 决定告警详情链接。无 URL 时返回「（无可视化链接）」。"""
    category = alert.get("category", "tracing")
    trace_id = alert.get("trace_id")
    if category == "tracing":
        base = settings.langfuse_external_url or settings.langfuse_base_url
        if not base:
            return "（Langfuse 未配置）"
        if not trace_id:
            return "（无 trace ID）"
        return f"{base}/traces/{trace_id}"
    if category in ("resource", "service_health"):
        return settings.grafana_external_url or "（Grafana 未配置）"
    if category == "usage":
        return settings.litellm_base_url or "（LiteLLM 未配置）"
    if category == "call_analysis":
        base = settings.langfuse_external_url or settings.langfuse_base_url
        return f"{base}/traces" if base else "（Langfuse 未配置）"
    return "（无可视化链接）"


def _category_label(category: str) -> str:
    return {
        "tracing": "链路追踪",
        "resource": "资源监控",
        "service_health": "服务健康",
        "usage": "用量分析",
        "call_analysis": "调用分析",
    }.get(category, category)


def _format_alert_subject(alert: dict[str, Any]) -> str:
    """邮件主题。"""
    severity_label = "严重" if alert["severity"] == "critical" else "警告"
    agent_id = alert.get("agent_id") or "global"
    return f"[UnionAgents {severity_label}] {alert['rule_name']} - {agent_id[:8]}"


async def check_and_notify(db: AsyncSession) -> int:
    """后台轮询入口：
    1. evaluate_rules(db) 拿当前触发告警
    2. A 类恢复检测：所有 status='firing' 且 rule_type IN AUTO_RESOLVABLE 的事件
       - 仍触发（同 rule_id+trace_id 或 rule_id+agent_id 在当前 alerts 里）→ 更新 last_seen_at
       - 不再触发 → 标记 resolved + resolved_at
    3. 新触发处理：对每条当前 alert
       - 同 rule + trace_id/agent_id 已有 firing 事件 → 更新 last_seen_at（持续中，不发通知）
       - 否则查 DEDUP_WINDOW 内是否有同 rule+agent 的 acknowledged 事件
         有 → 写新 firing 事件但跳过通知（人已确认过）
         无 → 写新 firing 事件 + 发通知
    B 类（tracing）和 C 类（usage）不参与恢复检测——单次/累积值无法撤销。
    返回新增事件数。
    """
    alerts = await evaluate_rules(db)

    # ── A 类恢复检测 ─────────────────────────────────────
    # 即使 alerts 为空也跑恢复检测——所有 firing A 类事件若不在当前 alerts 里则标记 resolved。
    # 数据源抖动风险：langfuse/prometheus 偶尔不可达时 evaluate_rules 返回空，会误标记 resolved。
    # 但 resolved 事件不参与去重，下次同 rule+agent 再触发会生成新事件，影响仅限于「持续触发」历史断开。
    # 取此权衡是为了让「指标恢复」场景能被自动检测——否则需要人工逐条确认。
    # 当前触发的告警 key 集合（rule_id + trace_id 或 rule_id + agent_id）
    active_keys: set[tuple] = set()
    for a in alerts:
        rid = a["rule_id"]
        if a.get("trace_id") is not None:
            active_keys.add((rid, "trace", a["trace_id"]))
        else:
            active_keys.add((rid, "agent", a.get("agent_id")))

    now = datetime.now(UTC)
    # 查 firing + acknowledged 的 A 类事件——acknowledged 的 A 类也要参与恢复检测，
    # 否则人确认过后指标降下来会卡在 acknowledged 状态永不恢复。
    firing_q = await db.execute(
        select(AlertEvent).where(
            AlertEvent.status.in_(["firing", "acknowledged"]),
            AlertEvent.rule_type.in_(AUTO_RESOLVABLE_RULE_TYPES),
        )
    )
    for event in firing_q.scalars().all():
        # 当前 alert 是否仍触发该事件对应的告警
        if event.trace_id is not None:
            still_firing = (event.rule_id, "trace", event.trace_id) in active_keys
        else:
            still_firing = (event.rule_id, "agent", event.agent_id) in active_keys
        if still_firing:
            event.last_seen_at = now
        else:
            event.status = "resolved"
            event.resolved_at = now

    # ── 新触发处理 ───────────────────────────────────────
    new_count = 0
    for alert in alerts:
        rule_id = alert["rule_id"]
        trace_id = alert.get("trace_id")
        agent_id = alert.get("agent_id")

        # 同 rule + trace_id/agent_id 的 firing 事件已存在 → 更新 last_seen_at（持续中，不发通知）
        existing_q = select(AlertEvent).where(
            AlertEvent.rule_id == rule_id,
            AlertEvent.status == "firing",
        )
        if trace_id is not None:
            existing_q = existing_q.where(AlertEvent.trace_id == trace_id)
        else:
            agent_filter = (
                AlertEvent.agent_id.is_(None) if agent_id is None else AlertEvent.agent_id == agent_id
            )
            existing_q = existing_q.where(agent_filter)
        existing = (await db.execute(existing_q.limit(1))).scalar_one_or_none()
        if existing:
            existing.last_seen_at = now
            continue

        # 不存在 firing 事件——查 DEDUP_WINDOW 内是否有 acknowledged 事件决定是否跳过通知
        since = now - timedelta(hours=DEDUP_WINDOW_HOURS)
        ack_q = select(AlertEvent.id).where(
            AlertEvent.rule_id == rule_id,
            AlertEvent.status == "acknowledged",
            AlertEvent.created_at >= since,
        )
        if trace_id is not None:
            ack_q = ack_q.where(AlertEvent.trace_id == trace_id)
        else:
            agent_filter = (
                AlertEvent.agent_id.is_(None) if agent_id is None else AlertEvent.agent_id == agent_id
            )
            ack_q = ack_q.where(agent_filter)
        was_acknowledged = (await db.execute(ack_q.limit(1))).scalar_one_or_none() is not None

        if was_acknowledged:
            # 人已确认过——仍记事件但不发通知
            notified = []
        else:
            channels = await _channels_for_rule(db, rule_id)
            if channels:
                notified = await notify_channels(
                    channels,
                    _format_alert_text(alert),
                    _format_alert_subject(alert),
                )
            else:
                notified = []

        event = AlertEvent(
            rule_id=rule_id,
            rule_name=alert["rule_name"],
            rule_type=alert["rule_type"],
            trace_id=trace_id,
            agent_id=agent_id,
            severity=alert["severity"],
            message=alert["message"],
            notified_channels=notified,
            last_seen_at=now,
        )
        db.add(event)
        new_count += 1

    await db.commit()
    return new_count
