"""0.8.110 用户查重集成测试 — 真 DB 验证：

0.8.110 改为两态模型后 email/phone 未认证不查重，所以本文件只测 username 查重（仍全局唯一）。
email/phone 认证相关测试在 test_user_verified.py。

1. POST /api/manager/users username 已被占用 → 409 username_already_used
2. POST /api/manager/users 新 username → 201
3. PUT /api/manager/users/{id} 改 username 到已占用 → 409 username_already_used
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + admin + 占用 username 的用户。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    admin = User(
        username=f"admin_dup_{uuid.uuid4().hex[:8]}",
        email=f"admin_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    occupier = User(
        username=f"occupier_{uuid.uuid4().hex[:8]}",
        email=f"occupier_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add_all([admin, occupier])
    await session.commit()
    await session.refresh(admin)
    await session.refresh(occupier)

    yield session, admin, occupier

    await session.execute(
        text("DELETE FROM users WHERE id IN (:a, :o)"),
        {"a": admin.id, "o": occupier.id},
    )
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client_as_admin(db, monkeypatch):
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


# ── POST create username 查重 ───────────────────────────────


@pytest.mark.asyncio
async def test_create_user_username_already_used_returns_409(client_as_admin, db):
    """username 已被占用 → 409 username_already_used。"""
    _, _, occupier = db
    resp = await client_as_admin.post(
        "/api/manager/users",
        json={
            "username": occupier.username,
            "email": f"new_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Admin@2026",
        },
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "username_already_used"


@pytest.mark.asyncio
async def test_create_user_new_username_success(client_as_admin, db):
    """新 username + 新 email → 201。"""
    session, _, _ = db
    resp = await client_as_admin.post(
        "/api/manager/users",
        json={
            "username": f"new_user_{uuid.uuid4().hex[:8]}",
            "email": f"new_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Admin@2026",
        },
    )
    assert resp.status_code == 201, resp.text
    created_id = resp.json()["id"]
    await session.execute(text("DELETE FROM users WHERE id = :u"), {"u": created_id})
    await session.commit()


# ── PUT update username 查重 ────────────────────────────────


@pytest.mark.asyncio
async def test_update_user_username_to_occupied_returns_409(client_as_admin, db):
    """PUT 把 username 改成别人已占用的 → 409 username_already_used。"""
    _, admin, occupier = db
    resp = await client_as_admin.put(
        f"/api/manager/users/{admin.id}",
        json={"username": occupier.username},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "username_already_used"
