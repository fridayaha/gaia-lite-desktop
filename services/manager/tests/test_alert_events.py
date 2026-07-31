"""/alert-events 端点测试：分页 + 过滤 + 普通登录可查 + notified_channels 不含敏感信息。

预置 AlertEvent 行（直接 ORM 写入）→ 调 GET /alert-events 验证返回结构 + 过滤 + 分页。
端点用 Depends(get_current_user)（非 require_platform_admin），普通登录可查。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user
from app.models import AlertEvent, AlertRule, User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理 alert_events + alert_rules + users。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"alertevt_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await session.execute(text("DELETE FROM alert_events"))
    await session.execute(text("DELETE FROM alert_rules"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM alert_events"))
    await session.execute(text("DELETE FROM alert_rules"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client_as_user(db, monkeypatch):
    """普通登录用户（非平台管理员）的 httpx AsyncClient，override get_db + get_current_user。"""
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, user = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    # is_platform_admin 走真实逻辑（默认 False，因为 user.is_platform_admin 字段未设）
    # 但为确保 403 不误触发，显式 monkeypatch 返回 False
    # 用 monkeypatch（非裸赋值）确保 teardown 自动还原，避免污染后续测试。
    import app.core.auth as auth_mod
    monkeypatch.setattr(auth_mod, "is_platform_admin", lambda _u: False)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# ── helpers ────────────────────────────────────────────────


async def _seed_event(
    session: AsyncSession,
    *,
    rule_id=None,
    rule_name="高延迟",
    rule_type="high_latency",
    trace_id="t1",
    agent_id="agent_a",
    severity="warning",
    message="延迟 9000ms 超阈值 5000ms",
    notified_channels=None,
    created_at=None,
    status: str = "firing",
    acknowledged_by: str | None = None,
    acknowledged_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> AlertEvent:
    """预置一条 AlertEvent。notified_channels 默认 [{type:feishu, name:bot, ok:True}]。

    状态机字段（0.8.66）：默认 status='firing'；测试可传 acknowledged_by/at 等模拟已确认/已恢复。
    """
    event = AlertEvent(
        rule_id=rule_id,
        rule_name=rule_name,
        rule_type=rule_type,
        trace_id=trace_id,
        agent_id=agent_id,
        severity=severity,
        message=message,
        notified_channels=notified_channels if notified_channels is not None else [
            {"type": "feishu", "name": "feishu-bot", "ok": True},
        ],
        created_at=created_at or datetime.now(UTC),
        status=status,
        acknowledged_by=acknowledged_by,
        acknowledged_at=acknowledged_at,
        last_seen_at=last_seen_at,
        resolved_at=resolved_at,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


# ── /alert-events 端点 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_list_alert_events_returns_paginated(client_as_user, db):
    """预置 3 条事件，pageSize=2 currentPage=1 → 返回 2 条 + total=3。"""
    session, _ = db
    for i in range(3):
        await _seed_event(session, trace_id=f"t{i}", created_at=datetime.now(UTC) - timedelta(minutes=i))
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"pageSize": 2, "currentPage": 1},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["total"] == 3
    assert body["data"]["pageSize"] == 2
    assert body["data"]["currentPage"] == 1
    assert len(body["data"]["list"]) == 2
    # 按 created_at desc 排序，第一条应为最新（t0）
    assert body["data"]["list"][0]["trace_id"] == "t0"


@pytest.mark.asyncio
async def test_list_alert_events_filter_by_severity(client_as_user, db):
    """severity=critical 过滤：只返回 critical，warning 不返回。"""
    session, _ = db
    await _seed_event(session, trace_id="t_warn", severity="warning")
    await _seed_event(session, trace_id="t_crit", severity="critical")
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"severity": "critical"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 1
    assert items[0]["trace_id"] == "t_crit"
    assert items[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_list_alert_events_filter_by_rule_id(client_as_user, db):
    """rule_id 过滤：只返回该 rule 的事件。"""
    session, _ = db
    rule = AlertRule(
        name="r1", rule_type="high_latency", threshold=1000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    await _seed_event(session, rule_id=rule.id, trace_id="t_match")
    await _seed_event(session, rule_id=None, trace_id="t_other")
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"rule_id": str(rule.id)},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 1
    assert items[0]["trace_id"] == "t_match"


@pytest.mark.asyncio
async def test_list_alert_events_filter_by_rule_types(client_as_user, db):
    """rule_types 过滤：多值逗号分隔，只返回匹配 rule_type 的事件。"""
    session, _ = db
    await _seed_event(session, rule_type="high_latency", trace_id="t_lat")
    await _seed_event(session, rule_type="high_tokens", trace_id="t_tok")
    await _seed_event(session, rule_type="high_memory", trace_id="t_mem")
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"rule_types": "high_latency,high_tokens"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    rule_types = {it["rule_type"] for it in items}
    assert rule_types == {"high_latency", "high_tokens"}


@pytest.mark.asyncio
async def test_list_alert_events_filter_by_time_range(client_as_user, db):
    """time_from + time_to 过滤：只返回窗口内的事件。"""
    session, _ = db
    now = datetime.now(UTC)
    await _seed_event(session, trace_id="t_old", created_at=now - timedelta(hours=3))
    await _seed_event(session, trace_id="t_in", created_at=now - timedelta(minutes=30))
    await _seed_event(session, trace_id="t_future", created_at=now + timedelta(hours=1))

    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={
            "time_from": (now - timedelta(hours=1)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    trace_ids = {i["trace_id"] for i in items}
    assert trace_ids == {"t_in"}


@pytest.mark.asyncio
async def test_list_alert_events_notified_channels_no_sensitive_data(client_as_user, db):
    """notified_channels 字段不含 webhook URL/邮箱地址（写入时就没存，只存 type+name+ok）。

    这是安全约束：普通登录可查 /alert-events，但 webhook URL/邮箱只在 alert_rules 表（平台管理员可见）。
    """
    session, _ = db
    await _seed_event(
        session,
        trace_id="t1",
        notified_channels=[
            {"type": "feishu", "name": "feishu-bot", "ok": True},
            {"type": "email", "name": None, "ok": False, "error": "SMTP 未配置"},
        ],
    )
    resp = await client_as_user.get("/api/manager/observability/alert-events")
    assert resp.status_code == 200
    item = resp.json()["data"]["list"][0]
    channels = item["notified_channels"]
    assert len(channels) == 2
    # 断言：所有 channel 字典里都没有 webhook_url / to 字段
    for ch in channels:
        assert "webhook_url" not in ch
        assert "to" not in ch
    assert channels[0]["ok"] is True
    assert channels[1]["ok"] is False
    assert "SMTP" in channels[1]["error"]


@pytest.mark.asyncio
async def test_list_alert_events_returns_stats(client_as_user, db):
    """stats 按 severity 聚合：预置 2 critical + 1 warning → stats.critical=2, stats.warning=1。

    0.8.62 新增字段：前端顶部统计卡片用 stats 展示全量计数，不受分页影响。
    """
    session, _ = db
    await _seed_event(session, trace_id="c1", severity="critical")
    await _seed_event(session, trace_id="c2", severity="critical")
    await _seed_event(session, trace_id="w1", severity="warning")
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"pageSize": 2, "currentPage": 1},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["stats"]["critical"] == 2
    assert data["stats"]["warning"] == 1
    # 分页只返回 2 条，但 stats 是全量
    assert len(data["list"]) == 2
    assert data["total"] == 3


@pytest.mark.asyncio
async def test_list_alert_events_stats_not_affected_by_severity_filter(client_as_user, db):
    """severity=warning 过滤时 list 只返回 warning，但 stats 仍是全量（critical + warning 都算）。

    设计意图：用户筛选严重级别时仍能看到"全量有多少 critical / warning"，知道当前筛选的是子集。
    """
    session, _ = db
    await _seed_event(session, trace_id="c1", severity="critical")
    await _seed_event(session, trace_id="c2", severity="critical")
    await _seed_event(session, trace_id="w1", severity="warning")
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"severity": "warning"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    # list 只有 1 条 warning（过滤生效）
    assert len(data["list"]) == 1
    assert data["list"][0]["trace_id"] == "w1"
    # stats 仍是全量（不受 severity 过滤影响）
    assert data["stats"]["critical"] == 2
    assert data["stats"]["warning"] == 1
    # total 是过滤后的条数（1），不是全量
    assert data["total"] == 1


@pytest.mark.asyncio
async def test_list_alert_events_returns_langfuse_fields(client_as_user, db):
    """langfuse_configured + langfuse_url 字段存在（前端切换数据源后不丢 Langfuse 跳转能力）。"""
    session, _ = db
    await _seed_event(session, trace_id="t1")
    resp = await client_as_user.get("/api/manager/observability/alert-events")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "langfuse_configured" in data
    assert isinstance(data["langfuse_configured"], bool)
    assert "langfuse_url" in data


@pytest.mark.asyncio
async def test_list_alert_events_empty_returns_zero(client_as_user, db):
    """无事件 → total=0, list=[]。"""
    resp = await client_as_user.get("/api/manager/observability/alert-events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["total"] == 0
    assert body["data"]["list"] == []


@pytest.mark.asyncio
async def test_list_alert_events_includes_rule_name_when_rule_deleted(client_as_user, db):
    """rule_id ondelete=SET NULL：删规则后事件保留，rule_name 冗余字段仍可读。"""
    session, _ = db
    rule = AlertRule(
        name="待删规则", rule_type="error_trace", threshold=None,
        enabled=True, severity="critical",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    await _seed_event(session, rule_id=rule.id, rule_name="待删规则", trace_id="t_orphan")
    # 删规则：alert_events.rule_id 应被 SET NULL，但 rule_name 保留
    await session.delete(rule)
    await session.commit()

    resp = await client_as_user.get("/api/manager/observability/alert-events")
    items = resp.json()["data"]["list"]
    item = next(i for i in items if i["trace_id"] == "t_orphan")
    assert item["rule_id"] is None
    assert item["rule_name"] == "待删规则"


# ── 0.8.66: 状态机 + acknowledge 端点测试 ────────────────────


@pytest.mark.asyncio
async def test_acknowledge_alert_event_sets_acknowledged_by(client_as_user, db):
    """POST /alert-events/{id}/acknowledge → 事件 acknowledged_by + acknowledged_at 被设置 + status 变 acknowledged。"""
    session, user = db
    event = await _seed_event(session, trace_id="t_ack", status="firing")
    resp = await client_as_user.post(f"/api/manager/observability/alert-events/{event.id}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    await session.refresh(event)
    assert event.acknowledged_by == user.username
    assert event.acknowledged_at is not None
    assert event.status == "acknowledged"


@pytest.mark.asyncio
async def test_list_alert_events_filter_by_status(client_as_user, db):
    """status=firing 过滤：只返回 firing，resolved/acknowledged 不返回。"""
    session, _ = db
    await _seed_event(session, trace_id="t_fire", status="firing")
    await _seed_event(session, trace_id="t_res", status="resolved", resolved_at=datetime.now(UTC))
    await _seed_event(session, trace_id="t_ack", status="acknowledged", acknowledged_by="u")

    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"status": "firing"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 1
    assert items[0]["trace_id"] == "t_fire"
    assert items[0]["status"] == "firing"


@pytest.mark.asyncio
async def test_list_alert_events_returns_status_stats(client_as_user, db):
    """stats 按 status 聚合：预置 1 firing + 2 resolved + 1 acknowledged → stats 反映全量。

    不受 status 过滤影响——用户筛选 status=firing 时仍能看到全量各状态计数。
    """
    session, _ = db
    await _seed_event(session, trace_id="f1", status="firing")
    await _seed_event(session, trace_id="r1", status="resolved", resolved_at=datetime.now(UTC))
    await _seed_event(session, trace_id="r2", status="resolved", resolved_at=datetime.now(UTC))
    await _seed_event(session, trace_id="a1", status="acknowledged", acknowledged_by="u")

    # 不带 status 过滤
    resp = await client_as_user.get("/api/manager/observability/alert-events")
    assert resp.status_code == 200
    stats = resp.json()["data"]["stats"]
    assert stats["firing"] == 1
    assert stats["resolved"] == 2
    assert stats["acknowledged"] == 1

    # 带 status=firing 过滤——stats 仍是全量
    resp = await client_as_user.get(
        "/api/manager/observability/alert-events",
        params={"status": "firing"},
    )
    assert resp.status_code == 200
    stats = resp.json()["data"]["stats"]
    assert stats["firing"] == 1
    assert stats["resolved"] == 2
    assert stats["acknowledged"] == 1


@pytest.mark.asyncio
async def test_list_alert_events_severity_stats_only_counts_firing(client_as_user, db):
    """severity stats 只统计 firing 状态——resolved/acknowledged 的 critical/warning 不计入。

    顶部「严重/警告」反映「当前还有多少异常在触发」，不把已恢复/已确认的计入。
    设计意图：用户切到「已恢复」tab 看历史时，顶部仍显示当前活跃异常的概览。
    """
    session, _ = db
    # 2 条 firing critical + 1 条 firing warning
    await _seed_event(session, trace_id="f_c1", severity="critical", status="firing")
    await _seed_event(session, trace_id="f_c2", severity="critical", status="firing")
    await _seed_event(session, trace_id="f_w1", severity="warning", status="firing")
    # 3 条 resolved（2 critical + 1 warning）—— 不应计入 severity stats
    await _seed_event(session, trace_id="r_c1", severity="critical", status="resolved", resolved_at=datetime.now(UTC))
    await _seed_event(session, trace_id="r_c2", severity="critical", status="resolved", resolved_at=datetime.now(UTC))
    await _seed_event(session, trace_id="r_w1", severity="warning", status="resolved", resolved_at=datetime.now(UTC))
    # 1 条 acknowledged critical —— 不应计入 severity stats
    await _seed_event(session, trace_id="a_c1", severity="critical", status="acknowledged", acknowledged_by="u")

    resp = await client_as_user.get("/api/manager/observability/alert-events")
    stats = resp.json()["data"]["stats"]
    # severity stats 只算 firing：critical=2, warning=1（resolved/acknowledged 的不计入）
    assert stats["critical"] == 2
    assert stats["warning"] == 1
    # status stats 仍是全量
    assert stats["firing"] == 3
    assert stats["resolved"] == 3
    assert stats["acknowledged"] == 1
