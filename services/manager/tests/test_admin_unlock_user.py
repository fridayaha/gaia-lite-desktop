"""0.8.106 admin 解锁能力集成测试 — 真 DB 验证：

1. admin 解锁锁定用户 → failed_login_count=0, locked_until=None + audit 记录
2. admin 解锁未锁定用户 → 400 user_not_locked（幂等失败而非静默成功）
3. admin 解锁不存在 user → 404
4. 非 platform_admin 调解锁 → 403
5. GET /users 列表 + GET /users/{id} 返回 is_locked + locked_until + failed_login_count
6. 锁定剩余秒数计算正确
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import OperationLog, User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + admin user + 一个被锁定的 target user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    admin = User(
        username=f"admin_unlock_{uuid.uuid4().hex[:8]}",
        email=f"admin_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    target = User(
        username=f"locked_user_{uuid.uuid4().hex[:8]}",
        email=f"locked_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
        failed_login_count=5,
        locked_until=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    session.add_all([admin, target])
    await session.commit()
    await session.refresh(admin)
    await session.refresh(target)

    await session.execute(text("DELETE FROM operation_logs"))
    await session.commit()

    yield session, admin, target

    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(
        text("DELETE FROM users WHERE id IN (:a, :t)"),
        {"a": admin.id, "t": target.id},
    )
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client_as_admin(db, monkeypatch):
    """admin 视角：override get_db + get_current_user + is_platform_admin=True。"""
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, admin, _ = db
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: admin
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: True)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_as_non_admin(db, monkeypatch):
    """非 admin 视角：调解锁应 403。"""
    from app.main import app
    from app.core.auth import get_current_user
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, admin, _ = db
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: admin
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: False)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# ── 1. admin 解锁锁定用户 ────────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_locked_user_clears_fields_and_logs_audit(client_as_admin, db):
    """admin 解锁锁定用户 → failed_login_count=0, locked_until=None + audit 记 user.unlock。"""
    session, admin, target = db
    resp = await client_as_admin.post(f"/api/manager/users/{target.id}/unlock")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failed_login_count"] == 0
    assert body["locked_until"] is None
    assert body["is_locked"] is False
    assert body["locked_remaining_seconds"] is None

    # DB 真实状态
    await session.refresh(target)
    assert target.failed_login_count == 0
    assert target.locked_until is None

    # audit 记录
    log = (
        await session.execute(
            select(OperationLog).where(OperationLog.action == "user.unlock")
        )
    ).scalar_one()
    assert log.actor_id == admin.id
    assert log.target_id == target.id
    assert log.status == "success"
    assert log.detail["username"] == target.username
    assert log.detail["unlocked_by"] == "admin"


@pytest.mark.asyncio
async def test_unlock_user_with_failed_count_but_not_locked(client_as_admin, db):
    """用户有 failed_login_count=3 但 locked_until=None（锁定前多次失败）→ 解锁清空 count。"""
    session, _, target = db
    target.failed_login_count = 3
    target.locked_until = None
    await session.commit()

    resp = await client_as_admin.post(f"/api/manager/users/{target.id}/unlock")
    assert resp.status_code == 200, resp.text
    assert resp.json()["failed_login_count"] == 0
    await session.refresh(target)
    assert target.failed_login_count == 0
    assert target.locked_until is None


# ── 2. admin 解锁未锁定用户 ──────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_not_locked_user_returns_400(client_as_admin, db):
    """未锁定用户（count=0, locked_until=None）→ 400 user_not_locked（不静默成功）。"""
    session, _, target = db
    target.failed_login_count = 0
    target.locked_until = None
    await session.commit()

    resp = await client_as_admin.post(f"/api/manager/users/{target.id}/unlock")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "user_not_locked"


# ── 3. admin 解锁不存在 user ──────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_nonexistent_user_returns_404(client_as_admin, db):
    """不存在 user_id → 404。"""
    fake_id = uuid.uuid4()
    resp = await client_as_admin.post(f"/api/manager/users/{fake_id}/unlock")
    assert resp.status_code == 404


# ── 4. 非 admin 调解锁 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_unlock_by_non_admin_returns_403(client_as_non_admin, db):
    """非 platform_admin 调解锁 → 403。"""
    _, _, target = db
    resp = await client_as_non_admin.post(f"/api/manager/users/{target.id}/unlock")
    assert resp.status_code == 403


# ── 5. 列表 + 详情返回锁定状态字段 ──────────────────────────


@pytest.mark.asyncio
async def test_list_users_returns_lock_fields(client_as_admin, db):
    """GET /users 列表返回 is_locked + locked_until + failed_login_count。"""
    _, _, target = db
    resp = await client_as_admin.get("/api/manager/users")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    found = [u for u in items if u["id"] == str(target.id)]
    assert found, "锁定用户应在列表里"
    u = found[0]
    assert u["failed_login_count"] == 5
    assert u["locked_until"] is not None
    assert u["is_locked"] is True
    assert u["locked_remaining_seconds"] is not None
    assert u["locked_remaining_seconds"] > 0


@pytest.mark.asyncio
async def test_get_user_detail_returns_lock_fields(client_as_admin, db):
    """GET /users/{id} 详情返回 is_locked + locked_remaining_seconds。"""
    _, _, target = db
    resp = await client_as_admin.get(f"/api/manager/users/{target.id}")
    assert resp.status_code == 200, resp.text
    u = resp.json()
    assert u["is_locked"] is True
    assert u["locked_remaining_seconds"] is not None
    # 锁定 15 分钟，剩余应在 800-900s 之间（容忍测试运行时间）
    assert 800 <= u["locked_remaining_seconds"] <= 900


@pytest.mark.asyncio
async def test_unlocked_user_is_locked_false(client_as_admin, db):
    """解锁后再查详情 → is_locked=False, locked_remaining_seconds=None。"""
    _, _, target = db
    await client_as_admin.post(f"/api/manager/users/{target.id}/unlock")
    resp = await client_as_admin.get(f"/api/manager/users/{target.id}")
    u = resp.json()
    assert u["is_locked"] is False
    assert u["locked_remaining_seconds"] is None
    assert u["failed_login_count"] == 0
