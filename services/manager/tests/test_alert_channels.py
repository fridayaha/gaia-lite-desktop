"""AlertChannel CRUD + config 校验 + 关联表 + CASCADE 集成测试。

- CRUD：真 DB 验证 list/create/update/delete + 平台管理员鉴权
- config 校验：feishu/dingtalk/wecom 必须有 webhook_url，email 必须有 to 数组
- subscribed_rule_ids：创建时写入关联表，update 整体替换
- subscribed_all：true 时关联表不写行（运行时短路）
- CASCADE：删渠道自动清关联表行；删规则也自动清
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user, require_platform_admin
from app.models import AlertChannel, AlertRule, User, channel_rule_subscriptions
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理 alert_* 表 + users。

    每个测试前清空 alert_channels + alert_rules + 关联表，避免 seed 残留干扰断言。
    """
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"alertch_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # 清理顺序：alert_events（FK rule_id SET NULL）→ channel_rule_subscriptions（FK CASCADE）→
    # alert_channels → alert_rules → users
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
    """非平台管理员视角：调写接口应 403。"""
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


# ── helpers ────────────────────────────────────────────────


async def _seed_rule(session: AsyncSession, name="高延迟", rule_type="high_latency", threshold=5000) -> AlertRule:
    rule = AlertRule(
        name=name, rule_type=rule_type, threshold=threshold,
        enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


# ── CRUD 测试 ───────────────────────────────────────────────


async def test_list_alert_channels_returns_seeded(client_as_admin, db):
    """列表返回 DB 中所有渠道，按 created_at 升序。"""
    session, _ = db
    session.add(AlertChannel(
        name="飞书群", channel_type="feishu",
        config={"webhook_url": "https://x"},
    ))
    session.add(AlertChannel(
        name="邮箱组", channel_type="email",
        config={"to": ["a@b.com"]},
    ))
    await session.commit()

    resp = await client_as_admin.get("/api/manager/observability/alert-channels")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    types = {i["channel_type"] for i in items}
    assert types == {"feishu", "email"}


async def test_create_channel_with_subscribed_rules_writes_db(client_as_admin, db):
    """create 携带 subscribed_rule_ids → DB 写入渠道 + 关联表（真 DB 验证字段值，不只断言 commit）。"""
    session, _ = db
    r1 = await _seed_rule(session, name="r1", rule_type="high_latency", threshold=5000)
    r2 = await _seed_rule(session, name="r2", rule_type="high_tokens", threshold=10000)

    payload = {
        "name": "飞书告警群",
        "channel_type": "feishu",
        "config": {"webhook_url": "https://open.feishu.cn/x"},
        "subscribed_all": False,
        "subscribed_rule_ids": [str(r1.id), str(r2.id)],
        "enabled": True,
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "飞书告警群"
    assert body["channel_type"] == "feishu"
    assert body["config"] == {"webhook_url": "https://open.feishu.cn/x"}
    assert body["subscribed_all"] is False
    assert set(body["subscribed_rule_ids"]) == {str(r1.id), str(r2.id)}

    # DB 真实写入：alert_channels 行
    ch_q = await session.execute(select(AlertChannel).where(AlertChannel.id == body["id"]))
    ch = ch_q.scalar_one_or_none()
    assert ch is not None
    assert ch.name == "飞书告警群"
    assert ch.channel_type == "feishu"
    assert ch.config == {"webhook_url": "https://open.feishu.cn/x"}
    assert ch.subscribed_all is False

    # DB 真实写入：channel_rule_subscriptions 2 行
    sub_q = await session.execute(
        select(channel_rule_subscriptions).where(
            channel_rule_subscriptions.c.channel_id == body["id"]
        )
    )
    rows = sub_q.all()
    assert len(rows) == 2
    sub_rule_ids = {str(r[1]) for r in rows}  # r[1] = rule_id
    assert sub_rule_ids == {str(r1.id), str(r2.id)}


async def test_create_channel_with_subscribed_all_no_assoc_rows(client_as_admin, db):
    """subscribed_all=true → 关联表不写行（运行时短路，避免「全部」与显式订阅重复）。"""
    session, _ = db
    r1 = await _seed_rule(session, name="r1")
    payload = {
        "name": "全量飞书",
        "channel_type": "feishu",
        "config": {"webhook_url": "https://x"},
        "subscribed_all": True,
        "subscribed_rule_ids": [str(r1.id)],  # 即使传了 rule_ids，subscribed_all=true 时也应被忽略
        "enabled": True,
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["subscribed_all"] is True
    assert body["subscribed_rule_ids"] == []  # 运行时短路，response 也无 rule_ids

    # 关联表无行
    sub_q = await session.execute(
        select(channel_rule_subscriptions).where(
            channel_rule_subscriptions.c.channel_id == body["id"]
        )
    )
    assert sub_q.all() == []


async def test_create_channel_403_for_non_admin(client_as_non_admin):
    """非平台管理员创建渠道返回 403。"""
    payload = {
        "name": "x", "channel_type": "feishu",
        "config": {"webhook_url": "https://x"},
    }
    resp = await client_as_non_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 403


async def test_create_channel_validates_unknown_type(client_as_admin):
    """channel_type=telegram 不在白名单 → 422。"""
    payload = {
        "name": "x", "channel_type": "telegram",
        "config": {"webhook_url": "https://x"},
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 422


async def test_create_channel_validates_webhook_url_scheme(client_as_admin):
    """feishu 渠道 config.webhook_url 非 http(s):// → 422。"""
    payload = {
        "name": "x", "channel_type": "feishu",
        "config": {"webhook_url": "ftp://bad"},
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 422


async def test_create_channel_validates_email_to_nonempty(client_as_admin):
    """email 渠道 config.to 为空数组 → 422。"""
    payload = {
        "name": "x", "channel_type": "email",
        "config": {"to": []},
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 422


async def test_create_channel_validates_email_address_format(client_as_admin):
    """email config.to 中含非法地址（无 @）→ 422。"""
    payload = {
        "name": "x", "channel_type": "email",
        "config": {"to": ["bad-address"]},
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 422


async def test_create_channel_validates_nonexistent_rule_id(client_as_admin, db):
    """subscribed_rule_ids 含不存在的 rule_id → 400。"""
    payload = {
        "name": "x", "channel_type": "feishu",
        "config": {"webhook_url": "https://x"},
        "subscribed_rule_ids": [str(uuid.uuid4())],  # 不存在
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 400
    assert "不存在" in resp.json()["detail"]


# ── update：subscribed_rule_ids 整体替换 ────────────────────


async def test_update_channel_replace_rules(client_as_admin, db):
    """PUT subscribed_rule_ids 整体替换：旧关联清空 + 新关联写入。"""
    session, _ = db
    r1 = await _seed_rule(session, name="r1", rule_type="high_latency", threshold=5000)
    r2 = await _seed_rule(session, name="r2", rule_type="high_tokens", threshold=10000)
    r3 = await _seed_rule(session, name="r3", rule_type="error_trace", threshold=None)

    ch = AlertChannel(
        name="飞书群", channel_type="feishu",
        config={"webhook_url": "https://x"},
        enabled=True,
    )
    ch.rules.append(r1)
    ch.rules.append(r2)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)

    # 替换订阅：[r1, r2] → [r2, r3]
    resp = await client_as_admin.put(
        f"/api/manager/observability/alert-channels/{ch.id}",
        json={"subscribed_rule_ids": [str(r2.id), str(r3.id)]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["subscribed_rule_ids"]) == {str(r2.id), str(r3.id)}

    # DB 真实写入：旧关联 r1 已清，新关联 r3 已加，r2 保留
    sub_q = await session.execute(
        select(channel_rule_subscriptions).where(
            channel_rule_subscriptions.c.channel_id == ch.id
        )
    )
    rows = sub_q.all()
    rule_ids = {str(r[1]) for r in rows}
    assert rule_ids == {str(r2.id), str(r3.id)}


async def test_update_channel_to_subscribed_all_clears_assoc(client_as_admin, db):
    """PUT 把 subscribed_all 改为 true → 关联表行清空（运行时短路）。"""
    session, _ = db
    r1 = await _seed_rule(session, name="r1")
    ch = AlertChannel(
        name="飞书群", channel_type="feishu",
        config={"webhook_url": "https://x"},
        enabled=True,
    )
    ch.rules.append(r1)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)

    resp = await client_as_admin.put(
        f"/api/manager/observability/alert-channels/{ch.id}",
        json={"subscribed_all": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["subscribed_all"] is True

    # 关联表行被清空（update 端点里 subscribed_all=true 时 new_rule_ids 强制空集）
    sub_q = await session.execute(
        select(channel_rule_subscriptions).where(
            channel_rule_subscriptions.c.channel_id == ch.id
        )
    )
    assert sub_q.all() == []


async def test_update_channel_404_for_missing(client_as_admin):
    """PUT 不存在的 channel_id 返回 404。"""
    resp = await client_as_admin.put(
        f"/api/manager/observability/alert-channels/{uuid.uuid4()}",
        json={"name": "x"},
    )
    assert resp.status_code == 404


# ── delete：CASCADE 清理关联表 ─────────────────────────────


async def test_delete_channel_cascades_assoc(client_as_admin, db):
    """删渠道 → channel_rule_subscriptions 自动清（ondelete=CASCADE）。"""
    session, _ = db
    r1 = await _seed_rule(session, name="r1")
    ch = AlertChannel(
        name="飞书群", channel_type="feishu",
        config={"webhook_url": "https://x"},
    )
    ch.rules.append(r1)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)
    channel_id = ch.id

    resp = await client_as_admin.delete(
        f"/api/manager/observability/alert-channels/{channel_id}"
    )
    assert resp.status_code == 204

    # 渠道行删除
    q = await session.execute(select(AlertChannel).where(AlertChannel.id == channel_id))
    assert q.scalar_one_or_none() is None

    # 关联表行自动清空（CASCADE）
    sub_q = await session.execute(
        select(channel_rule_subscriptions).where(
            channel_rule_subscriptions.c.channel_id == channel_id
        )
    )
    assert sub_q.all() == []


async def test_delete_channel_403_for_non_admin(client_as_non_admin, db):
    """非平台管理员 DELETE 返回 403。"""
    session, _ = db
    ch = AlertChannel(
        name="x", channel_type="feishu",
        config={"webhook_url": "https://x"},
    )
    session.add(ch)
    await session.commit()
    await session.refresh(ch)

    resp = await client_as_non_admin.delete(
        f"/api/manager/observability/alert-channels/{ch.id}"
    )
    assert resp.status_code == 403


async def test_delete_channel_404_for_missing(client_as_admin):
    """DELETE 不存在的 channel_id 返回 404。"""
    resp = await client_as_admin.delete(
        f"/api/manager/observability/alert-channels/{uuid.uuid4()}"
    )
    assert resp.status_code == 404


# ── list 403（config 含 webhook URL 敏感） ─────────────────


async def test_list_alert_channels_403_for_non_admin(client_as_non_admin):
    """非平台管理员 list 返回 403（config 含 webhook URL/邮箱地址敏感信息）。"""
    resp = await client_as_non_admin.get("/api/manager/observability/alert-channels")
    assert resp.status_code == 403


# ── 0.8.56: 订阅 resource 类规则 ────────────────────────────


async def test_create_channel_subscribed_to_resource_rule(client_as_admin, db):
    """建渠道订阅 high_cpu 规则（resource 类），验证 subscribed_rule_ids 含该规则 ID。"""
    session, _ = db
    rule = AlertRule(
        name="集群 CPU 高", category="resource", rule_type="high_cpu",
        threshold=80, enabled=True, severity="warning",
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    payload = {
        "name": "SRE 飞书群",
        "channel_type": "feishu",
        "config": {"webhook_url": "https://open.feishu.cn/x"},
        "subscribed_all": False,
        "subscribed_rule_ids": [str(rule.id)],
        "enabled": True,
    }
    resp = await client_as_admin.post("/api/manager/observability/alert-channels", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["subscribed_rule_ids"] == [str(rule.id)]
