"""AlertRule CRUD + /alerts 端点集成测试。

- CRUD：真 DB 验证 list/create/update/delete + 平台管理员鉴权
- /alerts：mock langfuse_client，验证阈值从 alert_rules 表读取（取代硬编码常量），
  并验证 enabled=False 时该规则不触发告警
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user, require_platform_admin
from app.models import AlertRule, User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理 alert_rules + user。

    每个测试前 alert_rules 表清空，避免 seed 残留干扰断言。
    """
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"alertrule_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await session.execute(text("DELETE FROM alert_rules"))
    await session.commit()

    yield session, user

    # 清理顺序：先 operation_logs（log_operation 写入，外键 actor_id ON DELETE SET NULL），
    # 再 alert_rules + users，避免跨测试串扰（test_operation_logs_list 断言行数）。
    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM alert_rules"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def non_admin_user(db):
    """第二个 user，无 platform_admin 权限，用于测 403。"""
    session, _ = db
    user = User(
        username=f"nonadmin_{uuid.uuid4().hex[:8]}",
        email=f"nonadmin_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    yield user
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()


@pytest_asyncio.fixture
async def client_as_admin(db, monkeypatch):
    """httpx AsyncClient + ASGITransport，override get_db + get_current_user，
    monkeypatch is_platform_admin 返回 True 绕过平台管理员鉴权。
    """
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, user = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: True)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_as_non_admin(db, non_admin_user, monkeypatch):
    """非平台管理员视角：调写接口应 403，调 list 仍可。"""
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, _ = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: non_admin_user
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: False)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# ── CRUD 测试 ───────────────────────────────────────────────


async def test_list_alert_rules_returns_seeded(client_as_admin, db):
    """列表返回 DB 中所有规则，按 created_at 升序。"""
    session, _ = db
    session.add(AlertRule(
        name="错误请求告警", rule_type="error_trace", threshold=None,
        enabled=True, severity="critical",
    ))
    session.add(AlertRule(
        name="高延迟告警", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    ))
    await session.commit()

    resp = await client_as_admin.get("/api/manager/observability/alert-rules")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    rule_types = {i["rule_type"] for i in items}
    assert rule_types == {"error_trace", "high_latency"}
    # threshold 字段：error_trace 为 null，high_latency 为 5000
    error_rule = next(i for i in items if i["rule_type"] == "error_trace")
    assert error_rule["threshold"] is None
    latency_rule = next(i for i in items if i["rule_type"] == "high_latency")
    assert latency_rule["threshold"] == 5000


async def test_create_alert_rule_endpoint_removed(client_as_admin):
    """0.8.57 起规则集为系统预置，POST /alert-rules 端点已移除 → 405。

    规则只能由 seed.py 初始化（启动时跑），不允许通过 API 新增。
    """
    payload = {
        "name": "test", "rule_type": "high_latency", "threshold": 1000,
        "enabled": True, "severity": "warning",
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-rules", json=payload)
    assert resp.status_code == 405


async def test_update_alert_rule_threshold(client_as_admin, db):
    """PUT 修改 threshold + enabled，DB 写入新值。"""
    session, _ = db
    rule = AlertRule(
        name="高延迟", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    resp = await client_as_admin.put(
        f"/api/manager/observability/alert-rules/{rule.id}",
        json={"threshold": 8000, "enabled": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["threshold"] == 8000
    assert body["enabled"] is False

    # DB 真实写入
    await session.refresh(rule)
    assert rule.threshold == 8000
    assert rule.enabled is False


async def test_update_alert_rule_404_for_missing(client_as_admin):
    """PUT 不存在的 rule_id 返回 404。"""
    resp = await client_as_admin.put(
        f"/api/manager/observability/alert-rules/{uuid.uuid4()}",
        json={"threshold": 1000},
    )
    assert resp.status_code == 404


async def test_update_alert_rule_ignores_category_and_rule_type(client_as_admin, db):
    """PUT 端点忽略 category / rule_type 字段（规则集锁定，不可改类别和类型）。"""
    session, _ = db
    rule = AlertRule(
        name="高延迟", category="tracing", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    # 试图改 category 和 rule_type（应被忽略，不报错也不生效）
    resp = await client_as_admin.put(
        f"/api/manager/observability/alert-rules/{rule.id}",
        json={"category": "resource", "rule_type": "high_cpu", "threshold": 9000},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 返回体里 category / rule_type 仍是原值
    assert body["category"] == "tracing"
    assert body["rule_type"] == "high_latency"
    # threshold 已更新
    assert body["threshold"] == 9000

    # DB 真实状态：category / rule_type 未被改
    await session.refresh(rule)
    assert rule.category == "tracing"
    assert rule.rule_type == "high_latency"
    assert rule.threshold == 9000


async def test_update_alert_rule_403_for_non_admin(client_as_non_admin, db):
    """非平台管理员 PUT 返回 403。"""
    session, _ = db
    rule = AlertRule(
        name="x", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    resp = await client_as_non_admin.put(
        f"/api/manager/observability/alert-rules/{rule.id}",
        json={"threshold": 9999},
    )
    assert resp.status_code == 403


async def test_delete_alert_rule_endpoint_removed(client_as_admin, db):
    """0.8.57 起规则集为系统预置，DELETE /alert-rules/{id} 端点已移除 → 405。

    规则不允许通过 API 删除（避免误删后无法恢复，下次 seed 才会重新插入）。
    """
    session, _ = db
    rule = AlertRule(
        name="临时", rule_type="high_latency", threshold=5000,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    resp = await client_as_admin.delete(f"/api/manager/observability/alert-rules/{rule.id}")
    assert resp.status_code == 405
    # 端点移除后规则仍在 DB
    await session.invalidate()
    result = await session.execute(select(AlertRule).where(AlertRule.id == rule.id))
    assert result.scalar_one_or_none() is not None


# ── /alerts 端点：阈值从 DB 读取 ────────────────────────────


def _make_trace(tid: str, latency_ms: int, token_total: int, status: str = "ok") -> dict:
    """构造一条 Langfuse trace + observation，用 mock 的 _trace_latency_ms / _trace_token_total / _trace_status 解析。

    真实 langfuse trace 字段较复杂；为简化测试，直接 patch 三个 helper 让它们读 fixture 字段。
    """
    return {
        "id": tid,
        "userId": "agent_test",
        "createdAt": "2026-07-06T10:00:00Z",
        "_test_latency_ms": latency_ms,
        "_test_token_total": token_total,
        "_test_status": status,
    }


@pytest.mark.asyncio
async def test_alerts_uses_db_threshold(client_as_admin, db, monkeypatch):
    """/alerts 端点从 alert_rules 表读阈值：DB 设 8000ms，trace 延迟 9000ms → 触发；
    把 DB 改成 10000ms 后同 trace 不触发（验证取代硬编码 ALERT_LATENCY_MS=5000）。
    """
    session, _ = db
    session.add(AlertRule(
        name="高延迟", rule_type="high_latency", threshold=8000,
        enabled=True, severity="warning",
    ))
    session.add(AlertRule(
        name="错误", rule_type="error_trace", threshold=None,
        enabled=True, severity="critical",
    ))
    await session.commit()

    from app.api import observability as obs

    monkeypatch.setattr(obs.langfuse_client, "is_configured", lambda: True)

    async def _mock_list_traces(**kwargs):
        return {"data": [_make_trace("t1", latency_ms=9000, token_total=0)]}

    async def _mock_list_observations(_tid):
        return []

    monkeypatch.setattr(obs.langfuse_client, "list_traces", _mock_list_traces)
    monkeypatch.setattr(obs.langfuse_client, "list_observations", _mock_list_observations)

    # patch 三个 helper 直接读 _test_* 字段，绕过真实 Langfuse 解析逻辑
    monkeypatch.setattr(
        obs, "_trace_latency_ms",
        lambda t, _obs: t.get("_test_latency_ms"),
    )
    monkeypatch.setattr(
        obs, "_trace_token_total",
        lambda t, _obs: t.get("_test_token_total"),
    )
    monkeypatch.setattr(
        obs, "_trace_status",
        lambda t, _obs: t.get("_test_status"),
    )

    # 阈值 8000，延迟 9000 → 触发 high_latency 告警
    resp = await client_as_admin.get("/api/manager/observability/alerts")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["type"] == "high_latency" for i in items)
    assert any(i["trace_id"] == "t1" for i in items)

    # 把 DB 阈值改成 10000，同 trace 不再触发
    rule = (await session.execute(
        select(AlertRule).where(AlertRule.rule_type == "high_latency")
    )).scalar_one()
    rule.threshold = 10000
    await session.commit()

    resp = await client_as_admin.get("/api/manager/observability/alerts")
    items = resp.json()["items"]
    assert not any(i["type"] == "high_latency" for i in items)


@pytest.mark.asyncio
async def test_alerts_respects_disabled_rule(client_as_admin, db, monkeypatch):
    """rule_type=error_trace 设 enabled=False 后，error trace 不再触发 critical 告警。"""
    session, _ = db
    session.add(AlertRule(
        name="错误", rule_type="error_trace", threshold=None,
        enabled=False, severity="critical",  # enabled=False
    ))
    await session.commit()

    from app.api import observability as obs

    monkeypatch.setattr(obs.langfuse_client, "is_configured", lambda: True)

    async def _mock_list_traces(**kwargs):
        return {"data": [_make_trace("t_err", latency_ms=100, token_total=0, status="error")]}

    async def _mock_list_observations(_tid):
        return []

    monkeypatch.setattr(obs.langfuse_client, "list_traces", _mock_list_traces)
    monkeypatch.setattr(obs.langfuse_client, "list_observations", _mock_list_observations)
    monkeypatch.setattr(obs, "_trace_latency_ms", lambda t, _obs: t.get("_test_latency_ms"))
    monkeypatch.setattr(obs, "_trace_token_total", lambda t, _obs: t.get("_test_token_total"))
    monkeypatch.setattr(obs, "_trace_status", lambda t, _obs: t.get("_test_status"))

    resp = await client_as_admin.get("/api/manager/observability/alerts")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # error_trace 规则禁用 → 不应有 critical 告警
    assert not any(i["type"] == "error_trace" for i in items)


@pytest.mark.asyncio
async def test_alerts_langfuse_not_configured(client_as_admin, monkeypatch):
    """langfuse 未配置时返回空列表 + langfuse_configured=False。"""
    from app.api import observability as obs

    monkeypatch.setattr(obs.langfuse_client, "is_configured", lambda: False)

    resp = await client_as_admin.get("/api/manager/observability/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["langfuse_configured"] is False


# ── notify_channels 已下线（009 migration DROP COLUMN），相关用例移至 test_alert_channels.py ──


async def test_list_alert_rules_403_for_non_admin(client_as_non_admin):
    """非平台管理员 list 返回 403（规则管理为平台管理员操作）。"""
    resp = await client_as_non_admin.get("/api/manager/observability/alert-rules")
    assert resp.status_code == 403


# ── 0.8.56: 5 大类 seed 默认规则 ────────────────────────────


async def test_seed_inserts_16_rules_when_empty(db):
    """空 DB seed 后应有 16 条规则（3 tracing + 4 resource + 3 service_health + 3 usage + 3 call_analysis）。"""
    session, _ = db
    from app.core.seed import seed_alert_rules

    await seed_alert_rules(session)
    result = await session.execute(select(AlertRule))
    rules = result.scalars().all()
    assert len(rules) == 16
    # 按 category 分组校验数量
    by_cat: dict[str, int] = {}
    for r in rules:
        by_cat[r.category] = by_cat.get(r.category, 0) + 1
    assert by_cat == {"tracing": 3, "resource": 4, "service_health": 3, "usage": 3, "call_analysis": 3}


async def test_seed_idempotent_per_rule_type(db):
    """已有 1 条 high_cpu 时再 seed，不重复插入（per-rule_type 幂等）。"""
    session, _ = db
    from app.core.seed import seed_alert_rules

    # 先插入 1 条 high_cpu（用户自定义）
    custom = AlertRule(
        name="自定义 CPU 告警", category="resource", rule_type="high_cpu",
        threshold=70, enabled=True, severity="warning",
    )
    session.add(custom)
    await session.commit()

    # 再 seed
    await seed_alert_rules(session)
    result = await session.execute(select(AlertRule).where(AlertRule.rule_type == "high_cpu"))
    high_cpu_rules = result.scalars().all()
    # 用户自定义的 1 条仍在，不被覆盖；seed 跳过该 rule_type
    assert len(high_cpu_rules) == 1
    assert high_cpu_rules[0].name == "自定义 CPU 告警"
    assert high_cpu_rules[0].threshold == 70

    # 其他 rule_type 应该 seed 进来（16 - 1 = 15 条）
    all_rules = (await session.execute(select(AlertRule))).scalars().all()
    assert len(all_rules) == 16
