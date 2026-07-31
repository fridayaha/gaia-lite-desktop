"""0.8.133 登录 captcha 条件触发测试 — 默认不要求，failed_login_count >= 2 后才要求。

测试覆盖：
1. failed_login_count=0 不传 captcha → 200（默认不要求）
2. failed_login_count=0 传对 captcha + 正确密码 → 200（兼容前端 UI 状态）
3. failed_login_count=2 不传 captcha → 400 captcha_required
4. failed_login_count=2 传对 captcha + 正确密码 → 200 + reset failed_login_count
5. failed_login_count=2 传对 captcha + 错密码 → 401 + failed_login_count=3
6. failed_login_count=2 传错 captcha → 400 captcha_invalid + failed_login_count 不增
7. captcha 一次性消费
8. captcha 校验失败不调 rate_limiter.record_failure
9. captcha 校验失败不写 audit
10. login-by-contact 同样覆盖
"""
from __future__ import annotations

import uuid
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.auth import hash_password
from app.middleware.rate_limit import rate_limiter
from app.models import User
from app.services.captcha_service import captcha_service
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user。failed_login_count 默认 0。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"captchauser_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone=f"138{uuid.uuid4().hex[:8][:8]}",
        real_name="Captcha 测试用户",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
        email_verified=True,
        phone_verified=True,
        failed_login_count=0,
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
    await rate_limiter.reset()
    await captcha_service.reset()

    yield session, user

    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()
    await captcha_service.reset()


@pytest_asyncio.fixture
async def client(db):
    """裸 client，走真 login endpoint。"""
    from app.main import app
    from pkg.common.database import get_db

    session, _ = db
    app.dependency_overrides[get_db] = lambda: session

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


async def _set_failed_count(session: AsyncSession, user: User, count: int):
    """手工设 failed_login_count（绕过登录失败链路）。"""
    user.failed_login_count = count
    await session.commit()
    await session.refresh(user)


async def _gen_captcha() -> tuple[str, str]:
    """生成真 captcha，返回 (captcha_id, answer)。answer 从 generate 内部状态偷取。"""
    captcha_id, _ = await captcha_service.generate()
    async with captcha_service._lock:
        answer, _ = captcha_service._captchas[captcha_id]
    return captcha_id, answer


# ── /login：默认不要求 captcha ─────────────────────────────────


@pytest.mark.asyncio
async def test_login_without_captcha_when_count_is_zero_succeeds(client, db):
    """failed_login_count=0 + 不传 captcha → 200（默认不要求）。"""
    session, user = db
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_with_captcha_when_count_is_zero_still_succeeds(client, db):
    """failed_login_count=0 + 传对 captcha → 200（兼容前端 UI 状态，传了也校验通过）。"""
    _, user = db
    captcha_id, answer = await _gen_captcha()
    resp = await client.post(
        "/api/manager/auth/login",
        json={
            "username": user.username,
            "password": "Pass1234",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_login_wrong_password_without_captcha_when_count_is_zero_returns_401(client, db):
    """failed_login_count=0 + 错密码 + 不传 captcha → 401 + failed_login_count=1（无 captcha 要求）。"""
    session, user = db
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "WrongPass123"},
        headers={"X-Forwarded-For": "10.0.0.3"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"
    await session.refresh(user)
    assert user.failed_login_count == 1


# ── /login：failed_login_count >= 2 触发 captcha ────────────────


@pytest.mark.asyncio
async def test_login_without_captcha_when_count_is_2_returns_captcha_required(client, db):
    """failed_login_count=2 + 不传 captcha → 400 captcha_required。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    resp = await client.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "Pass1234"},
        headers={"X-Forwarded-For": "10.0.0.4"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_required"
    await session.refresh(user)
    assert user.failed_login_count == 2  # 不增


@pytest.mark.asyncio
async def test_login_with_valid_captcha_when_count_is_2_succeeds_and_resets(client, db):
    """failed_login_count=2 + 传对 captcha + 正确密码 → 200 + failed_login_count=0。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    captcha_id, answer = await _gen_captcha()
    resp = await client.post(
        "/api/manager/auth/login",
        json={
            "username": user.username,
            "password": "Pass1234",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
        headers={"X-Forwarded-For": "10.0.0.5"},
    )
    assert resp.status_code == 200, resp.text
    await session.refresh(user)
    assert user.failed_login_count == 0


@pytest.mark.asyncio
async def test_login_with_valid_captcha_but_wrong_password_when_count_is_2_returns_401(client, db):
    """failed_login_count=2 + 传对 captcha + 错密码 → 401 + failed_login_count=3。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    captcha_id, answer = await _gen_captcha()
    resp = await client.post(
        "/api/manager/auth/login",
        json={
            "username": user.username,
            "password": "WrongPass123",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
        headers={"X-Forwarded-For": "10.0.0.6"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"
    await session.refresh(user)
    assert user.failed_login_count == 3


@pytest.mark.asyncio
async def test_login_with_wrong_captcha_when_count_is_2_returns_400(client, db):
    """failed_login_count=2 + 传错 captcha → 400 captcha_invalid + failed_login_count 不增。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    captcha_id, _ = await _gen_captcha()
    resp = await client.post(
        "/api/manager/auth/login",
        json={
            "username": user.username,
            "password": "Pass1234",
            "captcha_id": captcha_id,
            "captcha_answer": "0000",
        },
        headers={"X-Forwarded-For": "10.0.0.7"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_invalid"
    await session.refresh(user)
    assert user.failed_login_count == 2  # 不增


# ── /login：captcha 校验副作用 ────────────────────────────────


@pytest.mark.asyncio
async def test_captcha_is_one_shot_when_count_is_2(client, db):
    """同一 captcha_id 第二次用 → 400 captcha_invalid（一次性消费）。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    captcha_id, answer = await _gen_captcha()
    # 第一次用错密码，captcha 已被消费（verify 不论对错都 pop）
    await client.post(
        "/api/manager/auth/login",
        json={
            "username": user.username,
            "password": "WrongPass123",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
        headers={"X-Forwarded-For": "10.0.0.8"},
    )
    # 第二次用同一 captcha_id → 应 captcha_invalid（无论 failed_login_count 现在多少）
    resp = await client.post(
        "/api/manager/auth/login",
        json={
            "username": user.username,
            "password": "Pass1234",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
        headers={"X-Forwarded-For": "10.0.0.8"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_invalid"


@pytest.mark.asyncio
async def test_captcha_failure_does_not_call_rate_limiter(client, db):
    """captcha 校验失败时不应调 rate_limiter.record_failure（避免消耗 IP 限流配额）。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    with patch.object(rate_limiter, "record_failure", new=AsyncMock()) as mock_record:
        resp = await client.post(
            "/api/manager/auth/login",
            json={
                "username": user.username,
                "password": "Pass1234",
                "captcha_id": "nonexistent",
                "captcha_answer": "0000",
            },
            headers={"X-Forwarded-For": "10.0.0.9"},
        )
        assert resp.status_code == 400
        mock_record.assert_not_called()


@pytest.mark.asyncio
async def test_captcha_failure_does_not_write_audit_log(client, db):
    """captcha 校验失败时不应写审计日志（不算真正登录尝试）。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    from app.services import audit_service

    with patch.object(audit_service, "log_operation", new=AsyncMock()) as mock_log:
        resp = await client.post(
            "/api/manager/auth/login",
            json={
                "username": user.username,
                "password": "Pass1234",
                "captcha_id": "nonexistent",
                "captcha_answer": "0000",
            },
            headers={"X-Forwarded-For": "10.0.0.10"},
        )
        assert resp.status_code == 400
        mock_log.assert_not_called()


# ── /login-by-contact：同样触发逻辑 ────────────────────────────


@pytest.mark.asyncio
async def test_login_by_contact_without_captcha_when_count_is_zero_succeeds(client, db):
    """login-by-contact + failed_login_count=0 + 不传 captcha → 200。"""
    _, user = db
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={
            "contact": user.email,
            "contact_type": "email",
            "password": "Pass1234",
        },
        headers={"X-Forwarded-For": "10.0.0.11"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_login_by_contact_without_captcha_when_count_is_2_returns_required(client, db):
    """login-by-contact + failed_login_count=2 + 不传 captcha → 400 captcha_required。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={
            "contact": user.email,
            "contact_type": "email",
            "password": "Pass1234",
        },
        headers={"X-Forwarded-For": "10.0.0.12"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_required"


@pytest.mark.asyncio
async def test_login_by_contact_with_valid_captcha_when_count_is_2_succeeds(client, db):
    """login-by-contact + failed_login_count=2 + 传对 captcha + 正确密码 → 200。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    captcha_id, answer = await _gen_captcha()
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={
            "contact": user.email,
            "contact_type": "email",
            "password": "Pass1234",
            "captcha_id": captcha_id,
            "captcha_answer": answer,
        },
        headers={"X-Forwarded-For": "10.0.0.13"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_login_by_contact_with_wrong_captcha_when_count_is_2_returns_400(client, db):
    """login-by-contact + failed_login_count=2 + 错 captcha → 400 captcha_invalid。"""
    session, user = db
    await _set_failed_count(session, user, 2)
    captcha_id, _ = await _gen_captcha()
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={
            "contact": user.email,
            "contact_type": "email",
            "password": "Pass1234",
            "captcha_id": captcha_id,
            "captcha_answer": "0000",
        },
        headers={"X-Forwarded-For": "10.0.0.14"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "captcha_invalid"
