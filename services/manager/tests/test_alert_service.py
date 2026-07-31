"""alert_service 测试：evaluate_rules / _is_duplicate / _format_alert_* / check_and_notify。

- evaluate_rules：mock langfuse_client + _trace_* helpers，验证 3 类告警触发 + Langfuse 不可达容错
- _is_duplicate：真 DB 插 AlertEvent 后查重，1h 窗口外/无 trace_id 边界
- check_and_notify：完整链路（评估 → 查 alert_channels → 去重 → 发通知 → 写 AlertEvent）
  按 CLAUDE.md 反模式要求：不只断言 commit 被调，必须从 DB 查回 AlertEvent 行验证字段
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AlertChannel, AlertEvent, AlertRule, User
from app.services import alert_service, langfuse_client
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理 alert_events + alert_channels + alert_rules + users。

    每个测试前清空 alert_* 表，避免 seed/前置测试残留干扰断言。
    """
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"alertsvc_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # 注意顺序：alert_events 有 rule_id FK（ON DELETE SET NULL），先清 events；
    # channel_rule_subscriptions 有 FK 到 alert_channels + alert_rules，先清关联再清主表。
    await session.execute(text("DELETE FROM alert_events"))
    await session.execute(text("DELETE FROM channel_rule_subscriptions"))
    await session.execute(text("DELETE FROM alert_channels"))
    await session.execute(text("DELETE FROM alert_rules"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM alert_events"))
    await session.execute(text("DELETE FROM channel_rule_subscriptions"))
    await session.execute(text("DELETE FROM alert_channels"))
    await session.execute(text("DELETE FROM alert_rules"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


def _make_trace(
    tid: str,
    latency_ms: int | None = 0,
    token_total: int | None = 0,
    status: str = "ok",
    agent_id: str = "agent_test",
) -> dict:
    """构造 Langfuse trace fixture，配合 monkeypatch 的 _trace_* helpers 读 _test_* 字段。"""
    return {
        "id": tid,
        "userId": agent_id,
        "createdAt": "2026-07-06T10:00:00Z",
        "_test_latency_ms": latency_ms,
        "_test_token_total": token_total,
        "_test_status": status,
    }


@pytest.fixture
def patch_langfuse(monkeypatch):
    """patch langfuse_client + observability._trace_* helpers，让 evaluate_rules 读 fixture 字段。

    返回 dict 控制器，测试可改 traces / list_observations 返回值。
    """
    from app.api import observability as obs

    monkeypatch.setattr(langfuse_client, "is_configured", lambda: True)
    state = {"traces": [], "observations": []}

    async def _mock_list_traces(**kwargs):
        return {"data": state["traces"]}

    async def _mock_list_observations(_tid):
        return state["observations"]

    monkeypatch.setattr(langfuse_client, "list_traces", _mock_list_traces)
    monkeypatch.setattr(langfuse_client, "list_observations", _mock_list_observations)
    monkeypatch.setattr(obs, "_trace_latency_ms", lambda t, _obs: t.get("_test_latency_ms"))
    monkeypatch.setattr(obs, "_trace_token_total", lambda t, _obs: t.get("_test_token_total"))
    monkeypatch.setattr(obs, "_trace_status", lambda t, _obs: t.get("_test_status"))
    return state


# ── evaluate_rules ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_rules_langfuse_not_configured(db, monkeypatch):
    """langfuse 未配置 → 返回空列表，不抛。"""
    session, _ = db
    monkeypatch.setattr(langfuse_client, "is_configured", lambda: False)
    alerts = await alert_service.evaluate_rules(session)
    assert alerts == []


@pytest.mark.asyncio
async def test_evaluate_rules_langfuse_unreachable_returns_empty(db, patch_langfuse, monkeypatch):
    """list_traces 返回 None（HTTP 不可达）→ 返回空列表。"""
    session, _ = db

    async def _none(**kw):
        return None

    monkeypatch.setattr(langfuse_client, "list_traces", _none)
    alerts = await alert_service.evaluate_rules(session)
    assert alerts == []


@pytest.mark.asyncio
async def test_evaluate_rules_triggers_all_three_types(db, patch_langfuse):
    """3 条 trace 各触发一类告警：error / high_latency / high_tokens。"""
    session, _ = db
    session.add(AlertRule(
        name="错误", rule_type="error_trace", threshold=None,
        enabled=True, severity="critical",
    ))
    session.add(AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    ))
    session.add(AlertRule(
        name="高 Token", rule_type="high_tokens", threshold=10000,
        enabled=True, severity="warning",
    ))
    await session.commit()

    patch_langfuse["traces"] = [
        _make_trace("t_err", status="error"),
        _make_trace("t_lat", latency_ms=9000),
        _make_trace("t_tok", token_total=15000),
    ]
    alerts = await alert_service.evaluate_rules(session)
    types = {a["rule_type"] for a in alerts}
    assert types == {"error_trace", "high_latency", "high_tokens"}
    # 严重级别 + trace_id 透传
    err_alert = next(a for a in alerts if a["rule_type"] == "error_trace")
    assert err_alert["severity"] == "critical"
    assert err_alert["trace_id"] == "t_err"
    # latency 告警 message 含阈值
    lat_alert = next(a for a in alerts if a["rule_type"] == "high_latency")
    assert "9000ms" in lat_alert["message"]
    assert "5000ms" in lat_alert["message"]


@pytest.mark.asyncio
async def test_evaluate_rules_respects_disabled_rule(db, patch_langfuse):
    """high_latency 规则 enabled=False → 即使 latency 超阈值也不触发。"""
    session, _ = db
    session.add(AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=False, severity="warning",
    ))
    await session.commit()

    patch_langfuse["traces"] = [_make_trace("t1", latency_ms=9000)]
    alerts = await alert_service.evaluate_rules(session)
    assert alerts == []


@pytest.mark.asyncio
async def test_evaluate_rules_no_rule_no_alert(db, patch_langfuse):
    """DB 中无规则（rule 不存在）→ trace 超阈值也不触发（取代硬编码常量）。"""
    session, _ = db
    patch_langfuse["traces"] = [_make_trace("t1", latency_ms=99999, token_total=99999, status="error")]
    alerts = await alert_service.evaluate_rules(session)
    assert alerts == []


# ── _is_duplicate ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_is_duplicate_true_within_window(db):
    """1h 内有同 rule+trace 事件 → True。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_latency", threshold=1000, enabled=True, severity="warning")
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="r", rule_type="high_latency",
        trace_id="trace_x", agent_id="a", severity="warning",
        message="m", notified_channels=[],
    )
    session.add(event)
    await session.commit()

    assert await alert_service._is_duplicate(session, rule.id, "trace_x") is True


@pytest.mark.asyncio
async def test_is_duplicate_false_outside_window(db):
    """2h 前的事件不算重复（超过 1h 去重窗口）。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_latency", threshold=1000, enabled=True, severity="warning")
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="r", rule_type="high_latency",
        trace_id="trace_old", agent_id="a", severity="warning",
        message="m", notified_channels=[],
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(event)
    await session.commit()

    assert await alert_service._is_duplicate(session, rule.id, "trace_old") is False


@pytest.mark.asyncio
async def test_is_duplicate_non_tracing_class_by_agent_id(db):
    """trace_id=None 时按 rule_id + agent_id 去重（非 tracing 类告警）。

    0.8.63 修复：原 trace_id=None 直接返回 False 不去重，导致 resource/service_health 类
    告警每次轮询 120s 都生成新事件（high_memory 触发 140 条/200）。改为按 agent_id 去重，
    同 rule + 同 agent_id 在 1h 窗口内不重复。
    """
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_memory", threshold=90, enabled=True, severity="warning")
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="r", rule_type="high_memory",
        trace_id=None, agent_id="cluster", severity="warning",
        message="m", notified_channels=[],
    )
    session.add(event)
    await session.commit()

    # 同 rule + 同 agent_id → 重复
    assert await alert_service._is_duplicate(session, rule.id, None, "cluster") is True


@pytest.mark.asyncio
async def test_is_duplicate_non_tracing_different_agent_not_duplicate(db):
    """trace_id=None + 不同 agent_id → 不重复（不同对象分别告警）。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_memory", threshold=90, enabled=True, severity="warning")
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="r", rule_type="high_memory",
        trace_id=None, agent_id="cluster", severity="warning",
        message="m", notified_channels=[],
    )
    session.add(event)
    await session.commit()

    # 同 rule + 不同 agent_id → 不重复
    assert await alert_service._is_duplicate(session, rule.id, None, "node-1") is False


@pytest.mark.asyncio
async def test_is_duplicate_non_tracing_outside_window(db):
    """trace_id=None + 同 agent_id 但 2h 前 → 不重复（超 1h 去重窗口）。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_memory", threshold=90, enabled=True, severity="warning")
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="r", rule_type="high_memory",
        trace_id=None, agent_id="cluster", severity="warning",
        message="m", notified_channels=[],
        created_at=datetime.now(UTC) - timedelta(hours=2),
    )
    session.add(event)
    await session.commit()

    # 超 1h 窗口 → 不重复
    assert await alert_service._is_duplicate(session, rule.id, None, "cluster") is False


# ── _format_alert_text / subject ───────────────────────────


def test_format_alert_text_critical_with_external_url(monkeypatch):
    """severity=critical → 含 '严重'；langfuse_external_url 优先于 base_url。"""
    monkeypatch.setattr(alert_service.settings, "langfuse_external_url", "https://lf.public.example.com")
    monkeypatch.setattr(alert_service.settings, "langfuse_base_url", "https://lf.internal.example.com")
    alert = {
        "severity": "critical",
        "rule_name": "错误请求告警",
        "agent_id": "agent_abc123",
        "message": "请求返回错误",
        "trace_id": "trace_xyz",
        "created_at": "2026-07-06T10:00:00Z",
    }
    text = alert_service._format_alert_text(alert)
    assert "严重" in text
    assert "错误请求告警" in text
    assert "https://lf.public.example.com/traces/trace_xyz" in text


def test_format_alert_text_warning_no_url():
    """severity=warning → 含 '警告'；无 base_url + 无 trace_id → 显示（无 trace ID）。"""
    alert = {
        "severity": "warning",
        "rule_name": "高延迟",
        "agent_id": "agent_x",
        "message": "延迟 9000ms",
        "trace_id": None,
        "created_at": None,
    }
    # 确保没有 url 配置（其它测试可能 monkeypatch 过 settings）
    text = alert_service._format_alert_text(alert)
    assert "警告" in text
    assert "（无 trace ID）" in text


@pytest.mark.parametrize(
    "category,rule_type,rule_name",
    [
        ("tracing", "error_trace", "错误请求告警"),
        ("resource", "high_cpu", "集群 CPU 高"),
        ("service_health", "service_down", "服务下线告警"),
        ("usage", "high_monthly_cost", "月费用告警"),
        ("call_analysis", "low_success_rate", "成功率低告警"),
    ],
)
def test_format_alert_text_contains_keyword_for_feishu(category, rule_type, rule_name):
    """所有 category 的告警文本首行必须含「告警」二字。

    飞书自定义机器人关键词校验需匹配（用户常设「告警」为关键词），缺这二字会导致
    所有告警被飞书拒收（code 19024 Key Words Not Found）。0.8.61 修复。
    """
    alert = {
        "severity": "warning",
        "rule_name": rule_name,
        "rule_type": rule_type,
        "category": category,
        "agent_id": "agent_x",
        "message": "test",
        "trace_id": None,
        "created_at": None,
    }
    text = alert_service._format_alert_text(alert)
    # 首行必须含「告警」二字（飞书关键词匹配）
    first_line = text.split("\n", 1)[0]
    assert "告警" in first_line, f"category={category} rule_type={rule_type} 首行缺「告警」关键词: {first_line}"


def test_format_alert_subject():
    """邮件主题含严重级别 + rule_name + agent_id 前 8 字符。"""
    alert = {
        "severity": "critical",
        "rule_name": "错误请求告警",
        "agent_id": "agent_abc12345",
    }
    subject = alert_service._format_alert_subject(alert)
    assert "严重" in subject
    assert "错误请求告警" in subject
    assert "agent_ab" in subject  # [:8]


# ── _channels_for_rule：渠道订阅查询 ─────────────────────────


@pytest.mark.asyncio
async def test_channels_for_rule_returns_explicit_subscribers(db):
    """渠道订阅了该规则 → 返回该渠道（含 webhook_url 从 config 展开）。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_latency", threshold=1000, enabled=True, severity="warning")
    session.add(rule)
    # 另一条规则（不被订阅）
    other = AlertRule(name="other", rule_type="error_trace", threshold=None, enabled=True, severity="critical")
    session.add(other)
    ch = AlertChannel(
        name="飞书群", channel_type="feishu",
        config={"webhook_url": "https://open.feishu.cn/x"},
        enabled=True,
    )
    ch.rules.append(rule)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)

    result = await alert_service._channels_for_rule(session, rule.id)
    assert len(result) == 1
    assert result[0]["type"] == "feishu"
    assert result[0]["name"] == "飞书群"
    assert result[0]["webhook_url"] == "https://open.feishu.cn/x"


@pytest.mark.asyncio
async def test_channels_for_rule_includes_subscribed_all(db):
    """subscribed_all=true 的渠道对任何规则都命中（运行时短路，关联表无行也命中）。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_latency", threshold=1000, enabled=True, severity="warning")
    session.add(rule)
    all_ch = AlertChannel(
        name="全量通知", channel_type="feishu",
        config={"webhook_url": "https://x"},
        subscribed_all=True, enabled=True,
    )
    session.add(all_ch)
    await session.commit()

    result = await alert_service._channels_for_rule(session, rule.id)
    assert len(result) == 1
    assert result[0]["name"] == "全量通知"


@pytest.mark.asyncio
async def test_channels_for_rule_excludes_disabled(db):
    """enabled=false 的渠道不通知（即使订阅了规则）。"""
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_latency", threshold=1000, enabled=True, severity="warning")
    session.add(rule)
    ch = AlertChannel(
        name="停用渠道", channel_type="feishu",
        config={"webhook_url": "https://x"},
        enabled=False,
    )
    ch.rules.append(rule)
    session.add(ch)
    await session.commit()

    result = await alert_service._channels_for_rule(session, rule.id)
    assert result == []


# ── check_and_notify：完整链路 + 真 DB 写入断言 ─────────────


@pytest.mark.asyncio
async def test_check_and_notify_writes_alert_event_with_real_fields(db, patch_langfuse, monkeypatch):
    """触发告警 + 有渠道订阅该规则 → 调 notify_channels + 写 AlertEvent。
    按 CLAUDE.md 反模式要求：从 DB 查回 AlertEvent 行，验证字段值，不只断言 commit。
    """
    session, _ = db
    rule = AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    ch = AlertChannel(
        name="飞书告警群", channel_type="feishu",
        config={"webhook_url": "https://open.feishu.cn/x"},
        enabled=True,
    )
    ch.rules.append(rule)
    session.add(ch)
    await session.commit()
    await session.refresh(rule)
    await session.refresh(ch)

    patch_langfuse["traces"] = [_make_trace("t1", latency_ms=9000)]

    # mock notify_channels：返回 [{type,name,ok}] 不含 webhook URL/邮箱（service 已如此）
    notified_result = [
        {"type": "feishu", "name": "飞书告警群", "ok": True},
    ]
    notify_calls = []

    async def _spy_notify(channels, text, subject):
        notify_calls.append({"channels": channels, "text": text, "subject": subject})
        return notified_result

    monkeypatch.setattr(alert_service, "notify_channels", _spy_notify)

    n = await alert_service.check_and_notify(session)
    assert n == 1

    # notify_channels 被调用一次，传入的渠道含 webhook_url（service 内部用）
    assert len(notify_calls) == 1
    assert len(notify_calls[0]["channels"]) == 1
    assert notify_calls[0]["channels"][0]["type"] == "feishu"
    assert notify_calls[0]["channels"][0]["webhook_url"] == "https://open.feishu.cn/x"

    # 真 DB 写入断言：从 DB 查回 AlertEvent
    result = await session.execute(select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    event = result.scalar_one_or_none()
    assert event is not None
    assert event.rule_name == "高延迟"
    assert event.rule_type == "high_latency"
    assert event.trace_id == "t1"
    assert event.agent_id == "agent_test"
    assert event.severity == "warning"
    assert "9000ms" in event.message
    assert "5000ms" in event.message
    # notified_channels 存的是 notify_channels 的返回值（不含 webhook URL/邮箱）
    assert event.notified_channels == notified_result
    assert all("webhook_url" not in ch for ch in event.notified_channels)


@pytest.mark.asyncio
async def test_check_and_notify_with_subscribed_all_channel(db, patch_langfuse, monkeypatch):
    """subscribed_all=true 的渠道对未显式订阅的规则也命中（验证运行时短路）。"""
    session, _ = db
    rule = AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    # 渠道 subscribed_all=true，未在 rules 关联表里写
    all_ch = AlertChannel(
        name="全量飞书", channel_type="feishu",
        config={"webhook_url": "https://x"},
        subscribed_all=True, enabled=True,
    )
    session.add(all_ch)
    await session.commit()
    await session.refresh(rule)

    patch_langfuse["traces"] = [_make_trace("t_all", latency_ms=9000)]

    notify_calls = []

    async def _spy_notify(channels, text, subject):
        notify_calls.append(channels)
        return [{"type": "feishu", "name": "全量飞书", "ok": True}]

    monkeypatch.setattr(alert_service, "notify_channels", _spy_notify)

    n = await alert_service.check_and_notify(session)
    assert n == 1
    assert len(notify_calls) == 1
    assert notify_calls[0][0]["name"] == "全量飞书"


@pytest.mark.asyncio
async def test_check_and_notify_skips_notify_when_no_channel(db, patch_langfuse, monkeypatch):
    """无渠道订阅该规则 → 不调 notify_channels，但仍写 AlertEvent（notified_channels=[]）。

    场景：用户配了规则但还没建渠道，仍需记录事件历史。
    """
    session, _ = db
    rule = AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()

    patch_langfuse["traces"] = [_make_trace("t1", latency_ms=9000)]

    async def _should_not_be_called(*a, **kw):
        raise AssertionError("notify_channels 不应被调用")

    monkeypatch.setattr(alert_service, "notify_channels", _should_not_be_called)

    n = await alert_service.check_and_notify(session)
    assert n == 1

    result = await session.execute(select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    event = result.scalar_one_or_none()
    assert event is not None
    assert event.notified_channels == []


@pytest.mark.asyncio
async def test_check_and_notify_channel_disabled_not_notified(db, patch_langfuse, monkeypatch):
    """订阅了该规则的渠道 enabled=false → 不通知，但仍写 AlertEvent（notified_channels=[]）。"""
    session, _ = db
    rule = AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    ch = AlertChannel(
        name="停用渠道", channel_type="feishu",
        config={"webhook_url": "https://x"},
        enabled=False,
    )
    ch.rules.append(rule)
    session.add(ch)
    await session.commit()

    patch_langfuse["traces"] = [_make_trace("t1", latency_ms=9000)]

    async def _should_not_be_called(*a, **kw):
        raise AssertionError("notify_channels 不应被调用（渠道 enabled=false）")

    monkeypatch.setattr(alert_service, "notify_channels", _should_not_be_called)

    n = await alert_service.check_and_notify(session)
    assert n == 1
    result = await session.execute(select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    event = result.scalar_one_or_none()
    assert event is not None
    assert event.notified_channels == []


@pytest.mark.asyncio
async def test_check_and_notify_dedup_skips_second_run(db, patch_langfuse, monkeypatch):
    """同 rule+trace 第二次跑 → 跳过，不写新事件（去重生效）。"""
    session, _ = db
    rule = AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    patch_langfuse["traces"] = [_make_trace("t_dup", latency_ms=9000)]

    # 第一次：写 1 条事件
    n1 = await alert_service.check_and_notify(session)
    assert n1 == 1

    # 第二次：同 trace_id，去重窗口内（1h）→ 跳过
    n2 = await alert_service.check_and_notify(session)
    assert n2 == 0

    # DB 中仍只有 1 条事件
    count = (await session.execute(
        select(AlertEvent).where(AlertEvent.rule_id == rule.id)
    )).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_check_and_notify_no_alerts_returns_zero(db, patch_langfuse):
    """无规则或 trace 未超阈值 → evaluate_rules 返回空 → 不写事件，返回 0。"""
    session, _ = db
    patch_langfuse["traces"] = [_make_trace("t1", latency_ms=100, token_total=10, status="ok")]
    n = await alert_service.check_and_notify(session)
    assert n == 0

    rows = (await session.execute(select(AlertEvent))).scalars().all()
    assert rows == []


# ── 0.8.56: 5 大类 evaluator 测试 ────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_resource_rules_high_cpu(db, monkeypatch):
    """resource 类：mock summary 返回 cpu=85%，rule threshold=80% → 触发 1 条告警。"""
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=80, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()

    async def _mock_summary():
        return {"cluster_cpu_pct": 85.0, "cluster_memory_pct": 50.0,
                "max_disk_pct": 40.0, "max_pod_restarts": 0}

    monkeypatch.setattr(alert_service.monitoring_service, "get_resource_summary", _mock_summary)

    alerts = await alert_service.evaluate_rules(session)
    cpu_alerts = [a for a in alerts if a["rule_type"] == "high_cpu"]
    assert len(cpu_alerts) == 1
    assert "85.0%" in cpu_alerts[0]["message"]
    assert "超阈值 80%" in cpu_alerts[0]["message"]
    assert cpu_alerts[0]["category"] == "resource"


@pytest.mark.asyncio
async def test_evaluate_resource_rules_skip_when_prometheus_unconfigured(db, monkeypatch):
    """prometheus 未配置 → get_resource_summary 返回 None → 不触发告警。"""
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=80, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()

    async def _mock_summary():
        return None

    monkeypatch.setattr(alert_service.monitoring_service, "get_resource_summary", _mock_summary)
    alerts = await alert_service.evaluate_rules(session)
    assert alerts == []


@pytest.mark.asyncio
async def test_evaluate_service_health_rules_service_down(db, monkeypatch):
    """service_health 类：mock 1 个 down 服务 → 触发 1 条 critical 告警。"""
    session, _ = db
    rule = AlertRule(
        name="服务下线", category="service_health", rule_type="service_down",
        threshold=None, enabled=True, severity="critical",
    )
    session.add(rule)
    await session.commit()

    async def _mock_summary():
        return {"services": [
            {"name": "Manager", "status": "ok", "p95_ms": 100.0, "uptime_pct": 99.9},
            {"name": "PostgreSQL", "status": "down", "p95_ms": None, "uptime_pct": 0.0},
        ]}

    monkeypatch.setattr(alert_service.monitoring_service, "get_service_health_summary", _mock_summary)
    alerts = await alert_service.evaluate_rules(session)
    down_alerts = [a for a in alerts if a["rule_type"] == "service_down"]
    assert len(down_alerts) == 1
    assert down_alerts[0]["severity"] == "critical"
    assert "PostgreSQL" in down_alerts[0]["message"]


@pytest.mark.asyncio
async def test_evaluate_service_health_rules_low_uptime_inverted(db, monkeypatch):
    """low_uptime 反向比较：uptime=98% < threshold=99% → 触发（验证 < 而非 >）。"""
    session, _ = db
    rule = AlertRule(
        name="服务可用性低", category="service_health", rule_type="low_uptime",
        threshold=99, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()

    async def _mock_summary():
        return {"services": [
            {"name": "Manager", "status": "ok", "p95_ms": 100.0, "uptime_pct": 98.0},
        ]}

    monkeypatch.setattr(alert_service.monitoring_service, "get_service_health_summary", _mock_summary)
    alerts = await alert_service.evaluate_rules(session)
    uptime_alerts = [a for a in alerts if a["rule_type"] == "low_uptime"]
    assert len(uptime_alerts) == 1
    assert "低于" in uptime_alerts[0]["message"]
    assert "98.0%" in uptime_alerts[0]["message"]


@pytest.mark.asyncio
async def test_evaluate_usage_rules_high_monthly_cost(db, monkeypatch):
    """usage 类：mock monthly_cost=120，threshold=100 → 触发。"""
    session, _ = db
    rule = AlertRule(
        name="月费用告警", category="usage", rule_type="high_monthly_cost",
        threshold=100, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()

    async def _mock_summary():
        return {
            "today_tokens": 50000,
            "monthly_tokens": 500000,
            "monthly_cost": 120.0,
            "by_agent": [],
        }

    monkeypatch.setattr(alert_service.monitoring_service, "get_usage_summary", _mock_summary)
    alerts = await alert_service.evaluate_rules(session)
    cost_alerts = [a for a in alerts if a["rule_type"] == "high_monthly_cost"]
    assert len(cost_alerts) == 1
    assert "120" in cost_alerts[0]["message"]
    assert "USD" in cost_alerts[0]["message"]


@pytest.mark.asyncio
async def test_evaluate_call_analysis_rules_low_success_rate(db, monkeypatch):
    """call_analysis 类：mock success_rate=0.85（85%），threshold=90% → 触发（反向比较）。"""
    session, _ = db
    rule = AlertRule(
        name="成功率低告警", category="call_analysis", rule_type="low_success_rate",
        threshold=90, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()

    async def _mock_summary():
        return {"overall": {
            "request_count": 100,
            "success_rate": 0.85,
            "p50_latency_ms": 500,
            "p95_latency_ms": 2000,
            "avg_tokens_per_request": 5000,
        }, "by_agent": []}

    monkeypatch.setattr(alert_service.monitoring_service, "get_call_quality_summary", _mock_summary)
    alerts = await alert_service.evaluate_rules(session)
    success_alerts = [a for a in alerts if a["rule_type"] == "low_success_rate"]
    assert len(success_alerts) == 1
    assert "85" in success_alerts[0]["message"]
    assert "低于" in success_alerts[0]["message"]


def test_format_alert_text_resource_rule_no_langfuse_url(monkeypatch):
    """resource 类规则不显示 Langfuse 链接（显示 Grafana 或未配置提示）。"""
    monkeypatch.setattr(alert_service.settings, "langfuse_external_url", "https://lf.example.com")
    monkeypatch.setattr(alert_service.settings, "grafana_external_url", "")
    alert = {
        "severity": "warning",
        "rule_name": "集群 CPU 高",
        "rule_type": "high_cpu",
        "category": "resource",
        "agent_id": "cluster",
        "message": "CPU 85% 超阈值 80%",
        "trace_id": None,
        "created_at": None,
    }
    text = alert_service._format_alert_text(alert)
    assert "资源监控" in text
    assert "cluster" in text
    assert "Grafana 未配置" in text
    # 不应出现 Langfuse URL
    assert "lf.example.com" not in text


# ── 0.8.66: 状态机 + 自动恢复测试 ────────────────────────────


@pytest.mark.asyncio
async def test_check_and_notify_auto_resolves_a_class_event(db, monkeypatch):
    """预置 firing 的 high_cpu 事件（A 类），evaluate_rules 返回空 → 事件被标记 resolved。

    A 类规则（resource/service_health/call_analysis）指标降下来即恢复，后台轮询自动检测。
    """
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=85, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="集群 CPU 高", rule_type="high_cpu",
        trace_id=None, agent_id="cluster", severity="warning",
        message="CPU 90% 超阈值 85%", notified_channels=[],
        status="firing", last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    # evaluate_rules 返回空（指标恢复）
    async def _empty(_db):
        return []
    monkeypatch.setattr(alert_service, "evaluate_rules", _empty)

    n = await alert_service.check_and_notify(session)
    assert n == 0

    await session.refresh(event)
    assert event.status == "resolved"
    assert event.resolved_at is not None


@pytest.mark.asyncio
async def test_check_and_notify_keeps_firing_when_still_triggered(db, monkeypatch):
    """预置 firing 事件，evaluate_rules 仍返回该告警 → last_seen_at 更新，不写新事件。"""
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=85, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    old_seen = datetime.now(UTC) - timedelta(minutes=10)
    event = AlertEvent(
        rule_id=rule.id, rule_name="集群 CPU 高", rule_type="high_cpu",
        trace_id=None, agent_id="cluster", severity="warning",
        message="CPU 90% 超阈值 85%", notified_channels=[],
        status="firing", last_seen_at=old_seen,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    async def _still_firing(_db):
        return [{
            "rule_id": rule.id, "rule_name": "集群 CPU 高", "rule_type": "high_cpu",
            "category": "resource", "trace_id": None, "agent_id": "cluster",
            "severity": "warning", "message": "CPU 90% 超阈值 85%",
            "created_at": datetime.now(UTC).isoformat(),
        }]
    monkeypatch.setattr(alert_service, "evaluate_rules", _still_firing)
    monkeypatch.setattr(alert_service, "_channels_for_rule", lambda _db, _rid: [])

    n = await alert_service.check_and_notify(session)
    assert n == 0

    await session.refresh(event)
    assert event.status == "firing"
    assert event.last_seen_at > old_seen

    rows = (await session.execute(
        select(AlertEvent).where(AlertEvent.rule_id == rule.id)
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_check_and_notify_does_not_resolve_b_class(db, monkeypatch):
    """预置 firing 的 error_trace 事件（B 类），evaluate_rules 返回空 → 事件保持 firing。

    B 类（tracing 3 条）是单次 trace 异常，已发生无法撤销——不参与自动恢复。
    """
    session, _ = db
    rule = AlertRule(
        name="错误请求", category="tracing", rule_type="error_trace",
        threshold=None, enabled=True, severity="critical",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="错误请求", rule_type="error_trace",
        trace_id="trace_x", agent_id="agent_a", severity="critical",
        message="请求返回错误", notified_channels=[],
        status="firing", last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    async def _empty(_db):
        return []
    monkeypatch.setattr(alert_service, "evaluate_rules", _empty)

    n = await alert_service.check_and_notify(session)
    assert n == 0

    await session.refresh(event)
    assert event.status == "firing"
    assert event.resolved_at is None


@pytest.mark.asyncio
async def test_check_and_notify_skips_notify_when_acknowledged(db, monkeypatch):
    """预置 acknowledged 事件，evaluate_rules 仍返回该告警 → 写新 firing 事件但不调 notify_channels。

    人已确认过的告警不再重复通知，但事件历史仍记录新一次触发（status=firing）。
    """
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=85, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    old_event = AlertEvent(
        rule_id=rule.id, rule_name="集群 CPU 高", rule_type="high_cpu",
        trace_id=None, agent_id="cluster", severity="warning",
        message="CPU 90% 超阈值 85%", notified_channels=[],
        status="acknowledged", acknowledged_by="tester",
        acknowledged_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    session.add(old_event)
    await session.commit()

    async def _still_firing(_db):
        return [{
            "rule_id": rule.id, "rule_name": "集群 CPU 高", "rule_type": "high_cpu",
            "category": "resource", "trace_id": None, "agent_id": "cluster",
            "severity": "warning", "message": "CPU 90% 超阈值 85%",
            "created_at": datetime.now(UTC).isoformat(),
        }]
    monkeypatch.setattr(alert_service, "evaluate_rules", _still_firing)

    async def _should_not_be_called(*a, **kw):
        raise AssertionError("notify_channels 不应被调用（事件已被 acknowledged）")
    monkeypatch.setattr(alert_service, "notify_channels", _should_not_be_called)

    n = await alert_service.check_and_notify(session)
    assert n == 1

    rows = (await session.execute(
        select(AlertEvent).where(AlertEvent.rule_id == rule.id).order_by(AlertEvent.created_at.desc())
    )).scalars().all()
    assert len(rows) == 2
    new_event = rows[0]
    assert new_event.status == "firing"
    assert new_event.notified_channels == []


@pytest.mark.asyncio
async def test_is_duplicate_only_checks_firing_events(db):
    """预置 resolved 事件，调 _is_duplicate → False（resolved 不参与去重）。

    resolved 事件不参与去重——下次同 rule+agent 再触发会生成新事件，记录为新一次故障。
    acknowledged 事件同理不参与去重——人确认过后再触发应再次通知。
    """
    session, _ = db
    rule = AlertRule(name="r", rule_type="high_latency", threshold=1000, enabled=True, severity="warning")
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="r", rule_type="high_latency",
        trace_id="trace_x", agent_id="a", severity="warning",
        message="m", notified_channels=[],
        status="resolved", resolved_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(event)
    await session.commit()

    assert await alert_service._is_duplicate(session, rule.id, "trace_x") is False


@pytest.mark.asyncio
async def test_check_and_notify_auto_resolves_acknowledged_a_class_event(db, monkeypatch):
    """预置 acknowledged 的 A 类事件（high_cpu），evaluate_rules 返回空 → 事件被标记 resolved。

    acknowledged 的 A 类也要参与恢复检测——否则人确认过后指标降下来会卡在
    acknowledged 状态永不恢复。resolved 是终态，覆盖 acknowledged。
    """
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=85, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    event = AlertEvent(
        rule_id=rule.id, rule_name="集群 CPU 高", rule_type="high_cpu",
        trace_id=None, agent_id="cluster", severity="warning",
        message="CPU 90% 超阈值 85%", notified_channels=[],
        status="acknowledged", acknowledged_by="tester",
        acknowledged_at=datetime.now(UTC) - timedelta(minutes=10),
        last_seen_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    async def _empty(_db):
        return []
    monkeypatch.setattr(alert_service, "evaluate_rules", _empty)

    n = await alert_service.check_and_notify(session)
    assert n == 0

    await session.refresh(event)
    assert event.status == "resolved"
    assert event.resolved_at is not None
