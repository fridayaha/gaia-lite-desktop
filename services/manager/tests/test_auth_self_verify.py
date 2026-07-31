"""0.8.111 用户自服务邮箱/手机认证集成测试 — 真 DB 验证 POST /me/verify-email /me/verify-phone。

覆盖：
1. 用户有 email 未认证 + 直接插 code → 输入正确 code → 200 + email_verified=True
2. 输入错 code → 400 invalid_code + DB email_verified 仍 False
3. 无 email 用户 → 400 user_no_email
4. 已认证用户 → 200 幂等（不消费 code）
5. 用户 A 已认证 a@x.com，用户 B 改 email 到 a@x.com 后尝试认证 → 409 email_already_verified
6. phone 版本 success
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user, hash_password
from app.models import User, VerificationCode
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user（有 email/phone 未认证）。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"self_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="原名",
        phone="13800000000",
        hashed_password=hash_password("OldPass123"),
        is_active=True,
        email_verified=False,
        phone_verified=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    user = result.scalar_one()

    await session.execute(text("DELETE FROM operation_logs"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM verification_codes WHERE target = :t"), {"t": user.email})
    await session.execute(text("DELETE FROM verification_codes WHERE target = :t"), {"t": user.phone})
    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client_as_user(db):
    """登录用户视角（带 Bearer 等价于 dependency_overrides）。"""
    from app.main import app
    from pkg.common.database import get_db

    session, user = db
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


async def _insert_code(session, *, channel: str, target: str, purpose: str, code: str = "123456"):
    """直接往 DB 写一条 VerificationCode 记录，绕过 issue_code + 发码。"""
    record = VerificationCode(
        channel=channel,
        target=target,
        purpose=purpose,
        code_hash=hash_password(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ip=None,
    )
    session.add(record)
    await session.commit()


# ── 1. 正确 code → 200 + email_verified=True ──────────────


@pytest.mark.asyncio
async def test_verify_my_email_success_sets_verified_true(client_as_user, db):
    """用户有 email 未认证 → 直接插 code 123456 → 调 /me/verify-email → 200 + verified=True。"""
    session, user = db
    await _insert_code(session, channel="email", target=user.email, purpose="verify_email", code="123456")

    resp = await client_as_user.post(
        "/api/manager/auth/me/verify-email", json={"code": "123456"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_verified"] is True

    await session.refresh(user)
    assert user.email_verified is True


# ── 2. 错 code → 400 invalid_code ──────────────────────────


@pytest.mark.asyncio
async def test_verify_my_email_wrong_code_returns_400(client_as_user, db):
    """错 code → 400 invalid_code + DB email_verified 仍 False。"""
    session, user = db
    await _insert_code(session, channel="email", target=user.email, purpose="verify_email", code="123456")

    resp = await client_as_user.post(
        "/api/manager/auth/me/verify-email", json={"code": "000000"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "invalid_code"

    await session.refresh(user)
    assert user.email_verified is False


# ── 3. 无 email 用户 → 400 user_no_email ───────────────────


@pytest.mark.asyncio
async def test_verify_my_email_no_email_returns_400(db):
    """用户 email=None → 400 user_no_email。"""
    from app.main import app
    from pkg.common.database import get_db as _get_db

    session, _ = db
    # 用一个无 email 的 user 替换 dependency override
    no_email_user = User(
        username=f"no_email_{uuid.uuid4().hex[:8]}",
        email=None,
        hashed_password="x",
        is_active=True,
    )
    session.add(no_email_user)
    await session.commit()
    await session.refresh(no_email_user)
    res = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == no_email_user.id)
    )
    no_email_user = res.scalar_one()

    app.dependency_overrides[_get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: no_email_user
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")

    resp = await c.post("/api/manager/auth/me/verify-email", json={"code": "123456"})
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "user_no_email"

    app.dependency_overrides.clear()
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": no_email_user.id})
    await session.commit()


# ── 4. 已认证用户 → 200 幂等 ───────────────────────────────


@pytest.mark.asyncio
async def test_verify_my_email_already_verified_returns_200_idempotent(client_as_user, db):
    """email_verified=True → 200 幂等（不消费 code，直接返回 ok）。"""
    session, user = db
    user.email_verified = True
    await session.commit()

    resp = await client_as_user.post(
        "/api/manager/auth/me/verify-email", json={"code": "999999"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email_verified"] is True


# ── 5. 用户 A 已认证 a@x.com，用户 B 改 email 到 a@x.com 后尝试认证 → 409 ──


@pytest.mark.asyncio
async def test_verify_my_email_collision_returns_409(client_as_user, db):
    """A 已认证 a@x.com，B（当前登录用户）改 email 到 a@x.com 后尝试认证 → 409 email_already_verified。"""
    session, user = db  # user 就是 B
    shared_email = f"coll_{uuid.uuid4().hex[:8]}@example.com"

    # A：已认证 a@x.com
    other = User(
        username=f"coll_a_{uuid.uuid4().hex[:8]}",
        email=shared_email,
        hashed_password="x",
        is_active=True,
        email_verified=True,
    )
    session.add(other)
    await session.commit()

    # B：改 email 到 a@x.com（未认证状态）
    user.email = shared_email
    user.email_verified = False
    await session.commit()

    # 给 B 插一条 code
    await _insert_code(session, channel="email", target=shared_email, purpose="verify_email", code="123456")

    resp = await client_as_user.post(
        "/api/manager/auth/me/verify-email", json={"code": "123456"}
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "email_already_verified"

    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
    await session.execute(text("DELETE FROM verification_codes WHERE target = :t"), {"t": shared_email})
    await session.commit()


# ── 6. phone 版本 success ──────────────────────────────────


@pytest.mark.asyncio
async def test_verify_my_phone_success_sets_verified_true(client_as_user, db):
    """phone 版本：正确 code → 200 + phone_verified=True。"""
    session, user = db
    await _insert_code(session, channel="sms", target=user.phone, purpose="verify_phone", code="123456")

    resp = await client_as_user.post(
        "/api/manager/auth/me/verify-phone", json={"code": "123456"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["phone_verified"] is True

    await session.refresh(user)
    assert user.phone_verified is True
