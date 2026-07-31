"""Observability operation-logs + logs/search 端点集成测试。

- operation-logs：真 DB 验证 isouter join + 过滤/分页/权限
- logs/search：用 respx mock Loki 响应，验证 LogQL 拼接 + 字段提取 + 503 容错
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user, require_platform_admin
from app.models import OperationLog, User
from app.services.audit_service import log_operation
from pkg.common.config import settings


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理 operation_logs + user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    # setup 即清 operation_logs：前序测试经审计中间件写的日志可能残留（teardown
    # 在本 fixture 末尾清，但若前序测试用别的 fixture 不清表，行会泄漏进来，导致
    # 断言 len==3 实测 5）。setup 清一次保证本测试起点干净。
    await session.execute(text("DELETE FROM operation_logs"))

    user = User(
        username=f"obslog_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield session, user

    await session.execute(text("DELETE FROM operation_logs"))
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
    monkeypatch app.core.auth.is_platform_admin 返回 True 绕过权限检查。
    require_platform_admin() 是工厂，dependency_overrides key 不匹配，
    所以直接 patch is_platform_admin 让 _dep 内部调用返回 True。
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
    """非平台管理员视角：is_platform_admin 返回 False，调 operation-logs 应 403。"""
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


# ── operation-logs 测试 ──────────────────────────────────────


async def _seed_logs(db, user, n: int = 3) -> list[OperationLog]:
    """插 n 条 OperationLog，第一条 actor_id=None（模拟用户已删）。"""
    session, _ = db
    logs: list[OperationLog] = []
    for i in range(n):
        log = OperationLog(
            action=f"agent_instance.create_{i}",
            target_type="agent_instance",
            target_id=uuid.uuid4(),
            status="success",
            detail={"name": f"agent-{i}", "idx": i},
            actor_id=None if i == 0 else user.id,
        )
        session.add(log)
        logs.append(log)
    await session.commit()
    for log in logs:
        await session.refresh(log)
    return logs


async def test_operation_logs_list(client_as_admin, db):
    """列表返回所有行；actor_id=None 行也保留（isouter join），actor_name 为 null。"""
    session, user = db
    await _seed_logs(db, user, n=3)

    resp = await client_as_admin.get("/api/manager/observability/operation-logs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    items = body["data"]["list"]
    assert len(items) == 3
    # actor_id=None 的行 actor_name 应为 null
    null_actor_rows = [i for i in items if i["actor_name"] is None]
    assert len(null_actor_rows) == 1
    # 其余行 actor_name 应匹配 user.username
    named_rows = [i for i in items if i["actor_name"] is not None]
    assert all(i["actor_name"] == user.username for i in named_rows)


async def test_operation_logs_filter_by_action(client_as_admin, db):
    """按 action 过滤。"""
    session, user = db
    await _seed_logs(db, user, n=3)

    resp = await client_as_admin.get(
        "/api/manager/observability/operation-logs",
        params={"action": "agent_instance.create_1"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 1
    assert items[0]["action"] == "agent_instance.create_1"


async def test_operation_logs_filter_by_status(client_as_admin, db):
    """按 status 过滤。"""
    session, user = db
    session.add(OperationLog(
        action="x.fail", target_type="t", status="failure",
        detail={}, actor_id=user.id,
    ))
    session.add(OperationLog(
        action="x.ok", target_type="t", status="success",
        detail={}, actor_id=user.id,
    ))
    await session.commit()

    resp = await client_as_admin.get(
        "/api/manager/observability/operation-logs",
        params={"status": "failure"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 1
    assert items[0]["status"] == "failure"


async def test_operation_logs_pagination(client_as_admin, db):
    """pageSize/currentPage 分页。"""
    session, user = db
    for i in range(5):
        session.add(OperationLog(
            action=f"pagetest.{i}", target_type="t", status="success",
            detail={}, actor_id=user.id,
        ))
    await session.commit()

    resp = await client_as_admin.get(
        "/api/manager/observability/operation-logs",
        params={"pageSize": 2, "currentPage": 1},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 5
    assert len(body["list"]) == 2
    assert body["pageSize"] == 2
    assert body["currentPage"] == 1


async def test_operation_logs_keyword_search(client_as_admin, db):
    """keyword 在 detail 里 ILIKE 匹配。"""
    session, user = db
    session.add(OperationLog(
        action="kw.test", target_type="t", status="success",
        detail={"name": "special_keyword_xyz"}, actor_id=user.id,
    ))
    session.add(OperationLog(
        action="kw.other", target_type="t", status="success",
        detail={"name": "no_match"}, actor_id=user.id,
    ))
    await session.commit()

    resp = await client_as_admin.get(
        "/api/manager/observability/operation-logs",
        params={"keyword": "special_keyword_xyz"},
    )
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 1
    assert items[0]["action"] == "kw.test"


async def test_operation_logs_target_name_resolution(client_as_admin, db):
    """target_name 按 target_type 批量解析：命中时返回业务名称，未命中返回 null。"""
    session, user = db
    # 命中：target_type=user + target_id=user.id → target_name=username
    session.add(OperationLog(
        action="user.update", target_type="user", target_id=user.id,
        status="success", detail={}, actor_id=user.id,
    ))
    # 未命中：target_type=user + target_id 是随机 UUID（用户已删）
    session.add(OperationLog(
        action="user.update", target_type="user", target_id=uuid.uuid4(),
        status="success", detail={}, actor_id=user.id,
    ))
    # 未命中：target_type=agent_instance + 随机 UUID（实例已删）
    session.add(OperationLog(
        action="agent_instance.delete", target_type="agent_instance",
        target_id=uuid.uuid4(), status="success", detail={}, actor_id=user.id,
    ))
    # 不支持的 target_type（如 litellm_model，target_id 非 UUID 时前端 fallback）
    session.add(OperationLog(
        action="litellm_model.create", target_type="litellm_model",
        target_id=uuid.uuid4(), status="success", detail={}, actor_id=user.id,
    ))
    await session.commit()

    resp = await client_as_admin.get("/api/manager/observability/operation-logs")
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    by_action = {}
    for i in items:
        by_action.setdefault(i["action"], []).append(i)

    # user.update 命中行：target_name 等于 user.username（user 无 real_name）
    hit_rows = [i for i in by_action.get("user.update", []) if i["target_id"] == str(user.id)]
    assert len(hit_rows) == 1
    assert hit_rows[0]["target_name"] == user.username

    # user.update 未命中行：target_name 是 None
    miss_rows = [i for i in by_action.get("user.update", []) if i["target_name"] is None]
    assert len(miss_rows) == 1

    # agent_instance.delete target_id 随机 → target_name=None
    ai_rows = by_action.get("agent_instance.delete", [])
    assert len(ai_rows) == 1
    assert ai_rows[0]["target_name"] is None

    # litellm_model.create 未支持的 target_type → target_name=None（前端 fallback 短 UUID）
    llm_rows = by_action.get("litellm_model.create", [])
    assert len(llm_rows) == 1
    assert llm_rows[0]["target_name"] is None


async def test_operation_logs_require_platform_admin(client_as_non_admin):
    """非平台管理员 403。"""
    resp = await client_as_non_admin.get("/api/manager/observability/operation-logs")
    assert resp.status_code == 403


async def test_operation_logs_operator_ip(client_as_admin, db, monkeypatch):
    """operator_ip 由 middleware set 到 contextvar，log_operation 自动读取。
    模拟 middleware 行为：set_operator_ip("1.2.3.4") 后写日志，列表应回传该 IP。"""
    session, user = db
    from app.services.audit_service import set_operator_ip

    # 模拟 middleware 提取到客户端 IP
    set_operator_ip("203.0.113.42")
    await log_operation(
        session,
        actor_id=user.id,
        action="user.login",
        target_type="user",
        target_id=user.id,
    )
    # 一条不设 IP（模拟未走 middleware 的旧路径）
    set_operator_ip(None)
    await log_operation(
        session,
        actor_id=user.id,
        action="user.logout",
        target_type="user",
        target_id=user.id,
    )
    await session.commit()

    resp = await client_as_admin.get("/api/manager/observability/operation-logs")
    assert resp.status_code == 200
    items = resp.json()["data"]["list"]
    assert len(items) == 2
    # 第一条带 IP（最新，order_by desc — user.logout 在后所以排前）
    # 但两条中只有 user.login 那条有 IP
    with_ip = [i for i in items if i["operator_ip"] == "203.0.113.42"]
    assert len(with_ip) == 1
    assert with_ip[0]["action"] == "user.login"
    # 另一条 operator_ip 为 null
    without_ip = [i for i in items if i["operator_ip"] is None]
    assert len(without_ip) == 1
    assert without_ip[0]["action"] == "user.logout"


# ── logs/search 测试 ─────────────────────────────────────────


def _loki_response(lines: list[str]) -> dict:
    """构造 Loki /loki/api/v1/query_range 成功响应。"""
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {"stream": {"service": "manager", "level": "INFO"},
                 "values": [[str(int(datetime.now(UTC).timestamp() * 1e9)), line] for line in lines]}
            ],
        },
    }


async def test_logs_search(monkeypatch, client_as_admin):
    """mock Loki 响应，验证 LogQL 拼接 + JSON 解析。"""
    log_line = json.dumps({
        "timestamp": "2026-07-06T02:00:00+00:00",
        "level": "INFO",
        "service": "manager",
        "logger": "manager.access",
        "message": "GET /health 200",
        "request_id": "rid-001",
    })
    loki_resp = _loki_response([log_line])

    captured_params: dict = {}

    class _MockResp:
        status_code = 200
        text = json.dumps(loki_resp)

        def json(self):
            return loki_resp

    class _MockClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            captured_params["url"] = url
            captured_params["params"] = params
            return _MockResp()

    import app.api.observability as obs
    monkeypatch.setattr(obs.httpx, "AsyncClient", _MockClient)

    resp = await client_as_admin.get(
        "/api/manager/observability/logs/search",
        params={"service": "manager", "level": "INFO", "keyword": "health"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["service"] == "manager"
    assert item["level"] == "INFO"
    assert item["request_id"] == "rid-001"
    assert item["message"] == "GET /health 200"
    # LogQL 拼接验证
    query = captured_params["params"]["query"]
    assert 'namespace="unionagents"' in query
    assert 'service="manager"' in query
    assert 'level="INFO"' in query
    assert '|= "health"' in query
    assert body["grafana_url"] is not None


async def test_logs_search_default_service_filter(monkeypatch, client_as_admin):
    """不传 service 时默认只查 manager+gateway，避免 litellm 等无 service label 的
    plain text 行污染结果。"""
    log_line = json.dumps({
        "timestamp": "2026-07-06T02:00:00+00:00",
        "level": "INFO", "service": "manager", "logger": "manager.access",
        "message": "GET /health 200", "request_id": "rid-001",
    })
    loki_resp = _loki_response([log_line])

    captured: dict = {}

    class _MockResp:
        status_code = 200
        text = json.dumps(loki_resp)
        def json(self): return loki_resp

    class _MockClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, params=None):
            captured["params"] = params
            return _MockResp()

    import app.api.observability as obs
    monkeypatch.setattr(obs.httpx, "AsyncClient", _MockClient)

    resp = await client_as_admin.get("/api/manager/observability/logs/search")
    assert resp.status_code == 200
    query = captured["params"]["query"]
    # 默认 LogQL 应包含 service=~"manager|gateway" 正则
    assert 'service=~"manager|gateway"' in query
    # 不应只匹配单个 service
    assert 'service="manager"' not in query
    # 应过滤 uvicorn.access 重复日志 + health/metrics 探针噪音
    assert '!= "uvicorn.access"' in query
    assert 'path != "/health"' in query
    assert 'path != "/metrics"' in query


async def test_logs_search_loki_unavailable(monkeypatch, client_as_admin):
    import httpx as real_httpx
    import app.api.observability as obs

    class _BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            raise real_httpx.ConnectError("connection refused")

    monkeypatch.setattr(obs.httpx, "AsyncClient", _BoomClient)

    resp = await client_as_admin.get("/api/manager/observability/logs/search")
    assert resp.status_code == 503
    assert "Loki" in resp.json().get("detail", "")


async def test_logs_search_time_range_too_wide(client_as_admin):
    """时间范围超 24h 返回 400。"""
    now = datetime.now(UTC)
    resp = await client_as_admin.get(
        "/api/manager/observability/logs/search",
        params={
            "time_from": (now - timedelta(hours=48)).isoformat(),
            "time_to": now.isoformat(),
        },
    )
    assert resp.status_code == 400
