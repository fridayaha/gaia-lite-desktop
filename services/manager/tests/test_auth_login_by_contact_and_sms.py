"""0.8.116 邮箱/手机登录 + 手机验证码登录 + 渠道查询集成测试 — 真 DB 验证。

测试覆盖：
1. POST /login-by-contact email + 正确密码 → 200 + access_token
2. POST /login-by-contact phone + 正确密码 → 200
3. POST /login-by-contact 未认证 email → 401 invalid_credentials
4. POST /login-by-contact 不存在 contact → 401 invalid_credentials（防枚举）
5. POST /login-by-contact 错密码 → 401 + failed_login_count +1
6. POST /login-by-contact 5 次错密码 → 423 account_locked
7. POST /login-by-sms-code 正确 code → 200 + access_token
8. POST /login-by-sms-code 错 code → 401 invalid_code
9. POST /login-by-sms-code phone_verified=False → 401 invalid_credentials
10. GET /verification-channels 返回 {email: bool, sms: bool}
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

from app.core.auth import hash_password
from app.middleware.rate_limit import rate_limiter
from app.models import EmailConfig, SmsConfig, User, VerificationCode
from app.services.captcha_service import captcha_service
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_captcha(monkeypatch):
    """patch captcha_service.verify 永远返 True — 让 5 次失败锁定等测试能跑下去。
    captcha 条件触发逻辑由 test_auth_login_captcha.py 专门覆盖。
    """
    async def _always_true(*args, **kwargs):
        return True
    monkeypatch.setattr(captcha_service, "verify", _always_true)


@pytest_asyncio.fixture
async def db():
    """真 DB session。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()
    await session.execute(text("DELETE FROM operation_logs"))
    await session.commit()
    await rate_limiter.reset()

    yield session

    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM verification_codes"))
    await session.execute(text("DELETE FROM verification_tickets"))
    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(text("DELETE FROM email_configs"))
    await session.execute(text("DELETE FROM users WHERE username LIKE 'login_by_%'"))
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db):
    """裸 client，走真 login endpoint，不 bypass 鉴权。"""
    from app.main import app
    from pkg.common.database import get_db

    session = db
    app.dependency_overrides[get_db] = lambda: session

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


async def _make_user(db, *, email_verified=False, phone_verified=False, password="Pass1234"):
    """创建测试用户。默认邮箱/手机都未认证。"""
    user = User(
        username=f"login_by_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone=f"138{uuid.uuid4().hex[:8][:8]}",
        hashed_password=hash_password(password),
        is_active=True,
        email_verified=email_verified,
        phone_verified=phone_verified,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    # 重查带 selectinload(User.roles)
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    return result.scalar_one()


async def _write_sms_code(db, phone, code="123456", purpose="login"):
    """直接往 DB 写 verification_code，绕过 issue_code + sender。"""
    record = VerificationCode(
        channel="sms",
        target=phone,
        purpose=purpose,
        code_hash=hash_password(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ip=None,
    )
    db.add(record)
    await db.commit()


# ── 1-6. POST /login-by-contact ──────────────────────────────


@pytest.mark.asyncio
async def test_login_by_contact_email_success(client, db):
    """已认证 email + 正确密码 → 200 + access_token。"""
    user = await _make_user(db, email_verified=True, password="Pass1234")
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={"contact": user.email, "contact_type": "email", "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    await db.refresh(user)
    assert user.failed_login_count == 0
    assert user.last_login_at is not None


@pytest.mark.asyncio
async def test_login_by_contact_phone_success(client, db):
    """已认证 phone + 正确密码 → 200。"""
    user = await _make_user(db, phone_verified=True, password="Pass1234")
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={"contact": user.phone, "contact_type": "phone", "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.2"},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_by_contact_email_not_verified_returns_401(client, db):
    """email_verified=False → 401 invalid_credentials（防枚举，不告诉前端为什么）。"""
    user = await _make_user(db, email_verified=False, password="Pass1234")
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={"contact": user.email, "contact_type": "email", "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.3"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_by_contact_user_not_found_returns_401(client, db):
    """不存在的 email → 401 invalid_credentials（不区分 reason 防枚举）。"""
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={"contact": "nonexistent_" + uuid.uuid4().hex[:8] + "@example.com",
              "contact_type": "email", "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.4"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_login_by_contact_wrong_password_increments_failure_count(client, db):
    """错密码 → 401 + failed_login_count=1。"""
    user = await _make_user(db, email_verified=True, password="Pass1234")
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={"contact": user.email, "contact_type": "email", "password": "WrongPass123", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.5"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"
    await db.refresh(user)
    assert user.failed_login_count == 1


@pytest.mark.asyncio
async def test_login_by_contact_locked_after_5_failures(client, db):
    """5 次错密码 → 423 account_locked。"""
    user = await _make_user(db, email_verified=True, password="Pass1234")
    for _ in range(5):
        resp = await client.post(
            "/api/manager/auth/login-by-contact",
            json={"contact": user.email, "contact_type": "email", "password": "WrongPass123", "captcha_id": "x", "captcha_answer": "x"},
            headers={"X-Forwarded-For": "10.0.0.6"},
        )
        assert resp.status_code == 401
    # 第 6 次正确密码也应该被锁
    resp = await client.post(
        "/api/manager/auth/login-by-contact",
        json={"contact": user.email, "contact_type": "email", "password": "Pass1234", "captcha_id": "x", "captcha_answer": "x"},
        headers={"X-Forwarded-For": "10.0.0.6"},
    )
    assert resp.status_code == 423
    assert resp.json()["detail"] == "account_locked"


# ── 7-9. POST /login-by-sms-code ──────────────────────────────


@pytest.mark.asyncio
async def test_login_by_sms_code_success(client, db):
    """已认证 phone + 正确 code → 200 + access_token。"""
    user = await _make_user(db, phone_verified=True, password="Pass1234")
    await _write_sms_code(db, user.phone, code="123456")
    resp = await client.post(
        "/api/manager/auth/login-by-sms-code",
        json={"phone": user.phone, "code": "123456"},
        headers={"X-Forwarded-For": "10.0.0.7"},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()
    await db.refresh(user)
    assert user.last_login_at is not None


@pytest.mark.asyncio
async def test_login_by_sms_code_invalid_code_returns_401(client, db):
    """错 code → 401 invalid_code。"""
    user = await _make_user(db, phone_verified=True, password="Pass1234")
    await _write_sms_code(db, user.phone, code="123456")
    resp = await client.post(
        "/api/manager/auth/login-by-sms-code",
        json={"phone": user.phone, "code": "000000"},
        headers={"X-Forwarded-For": "10.0.0.8"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_code"


@pytest.mark.asyncio
async def test_login_by_sms_code_phone_not_verified_returns_401(client, db):
    """phone_verified=False → code 通过但 user 找不到 → 401 invalid_credentials。
    注：实际场景中 code 只会发给 verified user 的 phone，这里 DB 直接写 code 模拟 code 通过。"""
    user = await _make_user(db, phone_verified=False, password="Pass1234")
    # 直接写一个 code 让 verify_code 能通过
    await _write_sms_code(db, user.phone, code="123456")
    resp = await client.post(
        "/api/manager/auth/login-by-sms-code",
        json={"phone": user.phone, "code": "123456"},
        headers={"X-Forwarded-For": "10.0.0.9"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_credentials"


# ── 10. GET /verification-channels ──────────────────────────────


@pytest.mark.asyncio
async def test_get_verification_channels_returns_active_states(client, db):
    """插入 SmsConfig is_active=True + 不插 EmailConfig → 返回 {email: false, sms: true}。"""
    # 清掉旧配置
    await db.execute(text("DELETE FROM sms_configs"))
    await db.execute(text("DELETE FROM email_configs"))
    await db.commit()
    # SmsConfig.created_by 是 nullable=False 外键，需要先建一个 user 拿 id
    creator = await _make_user(db, email_verified=False, phone_verified=False, password="Pass1234")
    # 只插 SMS active
    sms = SmsConfig(
        provider="aliyun",
        sign_name="测试",
        template_code="SMSTEST",
        access_key_id_encrypted="enc_aki",
        access_key_secret_encrypted="enc_aks",
        region="cn-hangzhou",
        is_active=True,
        created_by=creator.id,
    )
    db.add(sms)
    await db.commit()

    resp = await client.get("/api/manager/auth/verification-channels")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"email": False, "sms": True}
