"""SMS / Email 服务商 provider 唯一约束测试 — 0.8.107 新增。

覆盖：
- POST 同 provider 重复创建 → 409 provider_in_use
- POST 不同 provider 创建 → 201
- DELETE 后重建同 provider → 201
- PUT 改 provider 到已存在 → 409
- PUT 改 provider 到新 provider → 200
- PUT 不改 provider → 200（不触发 409）
- _get_active_config 只查 is_active，不查 enabled（enabled 字段已删）
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.auth import _get_active_config
from app.core.auth import get_current_user, hash_password
from app.models import SmsConfig, EmailConfig, User
from pkg.common.config import settings


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user（平台管理员）。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"sec_admin_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="安全配置管理员",
        hashed_password=hash_password("Pass1234"),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    user = result.scalar_one()

    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(text("DELETE FROM email_configs"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(text("DELETE FROM email_configs"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client_as_admin(db, monkeypatch):
    """平台管理员视角（旁路 require_platform_admin）。"""
    from app.main import app
    from app.core.auth import is_platform_admin
    from pkg.common.database import get_db

    session, user = db
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda _u: True)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    await c.aclose()
    app.dependency_overrides.clear()


def _aliyun_sms_payload():
    return {
        "provider": "aliyun",
        "sign_name": "知行平台",
        "template_code": "SMS_12345678",
        "access_key_id": "LTAI-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-hangzhou",
        "daily_limit": 1000,
        "interval_seconds": 60,
    }


def _tencent_sms_payload():
    return {
        "provider": "tencent",
        "sign_name": "知行平台",
        "template_code": "SMS_12345678",
        "access_key_id": "AKID-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "sdk_app_id": "1400001234",
        "daily_limit": 1000,
        "interval_seconds": 60,
    }


def _huawei_sms_payload():
    return {
        "provider": "huawei",
        "sign_name": "知行平台",
        "template_code": "SMS_12345678",
        "access_key_id": "HWK-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-north-4",
        "daily_limit": 1000,
        "interval_seconds": 60,
    }


def _smtp_email_payload():
    return {
        "provider": "smtp",
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "encryption": "ssl",
        "username": "alerts@example.com",
        "password": "my-smtp-password",
        "from_name": "知行平台",
        "daily_limit": 200,
        "interval_seconds": 60,
    }


def _aliyun_email_payload():
    return {
        "provider": "aliyun",
        "access_key_id": "LTAI-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-hangzhou",
        "from_email": "alerts@example.com",
        "from_name": "知行平台",
        "daily_limit": 200,
        "interval_seconds": 60,
    }


# ── SMS provider 唯一约束 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_sms_same_provider_returns_409(client_as_admin):
    """已建 aliyun，再建 aliyun → 409 provider_in_use。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    assert resp.status_code == 201, resp.text

    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "provider_in_use"


@pytest.mark.asyncio
async def test_create_sms_different_provider_success(client_as_admin):
    """aliyun + tencent → 201。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    assert resp.status_code == 201
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_tencent_sms_payload()
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_delete_then_recreate_same_provider(client_as_admin):
    """删 aliyun 后再建 aliyun → 201。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.delete(f"/api/manager/sms-configs/{cfg_id}")
    assert resp.status_code == 204

    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_update_sms_provider_to_existing_returns_409(client_as_admin):
    """建 aliyun + tencent，把 tencent 改成 aliyun → 409。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_tencent_sms_payload()
    )
    tencent_id = resp.json()["id"]

    payload = _tencent_sms_payload()
    payload["provider"] = "aliyun"
    resp = await client_as_admin.put(
        f"/api/manager/sms-configs/{tencent_id}", json=payload
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "provider_in_use"


@pytest.mark.asyncio
async def test_update_sms_provider_to_new_success(client_as_admin):
    """建 tencent，改成 huawei → 200。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_tencent_sms_payload()
    )
    tencent_id = resp.json()["id"]

    payload = _huawei_sms_payload()  # provider=huawei
    resp = await client_as_admin.put(
        f"/api/manager/sms-configs/{tencent_id}", json=payload
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["provider"] == "huawei"


@pytest.mark.asyncio
async def test_update_sms_keep_provider_success(client_as_admin):
    """建 aliyun，不改 provider 只改 sign_name → 200（不触发 409）。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_sms_payload()
    )
    cfg_id = resp.json()["id"]

    payload = _aliyun_sms_payload()
    payload["sign_name"] = "新签名"
    resp = await client_as_admin.put(
        f"/api/manager/sms-configs/{cfg_id}", json=payload
    )
    assert resp.status_code == 200
    assert resp.json()["sign_name"] == "新签名"


# ── Email provider 唯一约束 ───────────────────────────────────


@pytest.mark.asyncio
async def test_create_email_same_provider_returns_409(client_as_admin):
    """已建 smtp，再建 smtp → 409。"""
    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_email_payload()
    )
    assert resp.status_code == 201

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_email_payload()
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "provider_in_use"


@pytest.mark.asyncio
async def test_create_email_different_provider_success(client_as_admin):
    """smtp + aliyun → 201。"""
    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_email_payload()
    )
    assert resp.status_code == 201
    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_aliyun_email_payload()
    )
    assert resp.status_code == 201


# ── _get_active_config 只查 is_active 不查 enabled ──────────


@pytest.mark.asyncio
async def test_get_active_config_only_checks_is_active(db):
    """active=true 行能被 _get_active_config 取到（删 enabled 后）。"""
    session, user = db
    cfg = SmsConfig(
        provider="aliyun",
        is_active=True,
        sign_name="知行平台",
        template_code="SMS_123456789",
        access_key_id_encrypted="dummy",
        access_key_secret_encrypted="dummy",
        region="cn-hangzhou",
        daily_limit=10,
        interval_seconds=60,
        created_by=user.id,
    )
    session.add(cfg)
    await session.commit()

    active = await _get_active_config(session, "sms")
    assert active is not None
    assert active.provider == "aliyun"
    assert active.is_active is True
    # enabled 字段已删，确保 model 上不再有此属性
    assert not hasattr(active, "enabled")


@pytest.mark.asyncio
async def test_get_active_config_returns_none_when_no_active(db):
    """无 active 行 → _get_active_config 返回 None。"""
    session, _ = db
    active = await _get_active_config(session, "sms")
    assert active is None
    active = await _get_active_config(session, "email")
    assert active is None
