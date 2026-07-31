"""邮件服务商配置（EmailConfig）API 集成测试 — 真 DB 验证 multi-config CRUD + 4 provider 探活。

覆盖：
- GET 空表返回 []
- POST 创建 smtp/aliyun/tencent/huawei 4 个 provider
- POST provider/encryption 非法 → 422
- POST smtp 缺 password → 422（schema model_validator）
- POST aliyun 缺 AK/SK → 422（schema model_validator）
- PUT 更新（留空 password/AK/SK 不修改）
- POST /{id}/activate 同事务 deactivate 其他行
- POST /{id}/test 各 provider 探活成功（monkeypatch SDK）
- POST /{id}/test 各 provider 认证失败 → ok=False + "认证失败"
- POST /{id}/test 未启用 / 解密失败 → ok=False
- DELETE 写审计日志
"""
from __future__ import annotations

import smtplib
import socket
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user, hash_password
from app.core.crypto import decrypt_credential, encrypt_credential
from app.models import EmailConfig, OperationLog, User
from pkg.common.config import settings


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user（平台管理员）。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"email_admin_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="邮件管理员",
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

    # 清空 email_configs + 操作日志（隔离测试）
    await session.execute(text("DELETE FROM email_configs"))
    await session.execute(
        text("DELETE FROM operation_logs WHERE action LIKE 'email_config%'")
    )
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM email_configs"))
    await session.execute(
        text("DELETE FROM operation_logs WHERE action LIKE 'email_config%'")
    )
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
    app.dependency_overrides.clear()


def _smtp_payload(**overrides):
    """构造 SMTP 邮件配置 payload。"""
    base = {
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
    base.update(overrides)
    return base


def _aliyun_payload(**overrides):
    """构造阿里云邮件配置 payload。"""
    base = {
        "provider": "aliyun",
        "access_key_id": "LTAI-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-hangzhou",
        "from_email": "alerts@example.com",
        "from_name": "知行平台",
        "daily_limit": 200,
        "interval_seconds": 60,
    }
    base.update(overrides)
    return base


def _tencent_payload(**overrides):
    """构造腾讯云邮件配置 payload。"""
    base = {
        "provider": "tencent",
        "access_key_id": "AKID-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "ap-hongkong",
        "from_email": "alerts@example.com",
        "from_name": "知行平台",
        "daily_limit": 200,
        "interval_seconds": 60,
    }
    base.update(overrides)
    return base


def _huawei_payload(**overrides):
    """构造华为云邮件配置 payload。"""
    base = {
        "provider": "huawei",
        "access_key_id": "HWK-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-north-4",
        "from_email": "alerts@example.com",
        "from_name": "知行平台",
        "daily_limit": 200,
        "interval_seconds": 60,
    }
    base.update(overrides)
    return base


class _FakeSMTPClient:
    """假 SMTP client，记录 login/quit 调用。"""

    def __init__(self):
        self.login_called = False
        self.quit_called = False

    def login(self, user, password):
        self.login_called = True

    def quit(self):
        self.quit_called = True


class _FakeSMTPSSL:
    """假 SMTP_SSL 工厂，构造时返回 _FakeSMTPClient。"""

    last_client: _FakeSMTPClient | None = None

    def __new__(cls, host, port, timeout=None):
        cls.last_client = _FakeSMTPClient()
        return cls.last_client


# ── GET (list) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_email_configs_returns_empty_when_empty(client_as_admin):
    """空表 GET 返回 []（前端显示空表）。"""
    resp = await client_as_admin.get("/api/manager/email-configs")
    assert resp.status_code == 200
    assert resp.json() == []


# ── POST 创建 4 个 provider ─────────────────────────────────


@pytest.mark.asyncio
async def test_create_email_config_smtp(client_as_admin, db):
    """POST 创建 smtp 配置，password 加密落库。"""
    session, _ = db
    resp = await client_as_admin.post(
        "/api/manager/email-configs",
        json=_smtp_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "smtp"
    assert data["is_active"] is False  # 新建不自动 activate
    assert data["smtp_host"] == "smtp.qq.com"
    assert data["smtp_port"] == 465
    assert data["encryption"] == "ssl"
    assert data["username"] == "alerts@example.com"
    assert data["from_name"] == "知行平台"
    assert data["password_configured"] is True

    # DB 验证：password 是密文
    cfg = (await session.execute(select(EmailConfig))).scalar_one()
    assert cfg.password_encrypted != "my-smtp-password"
    assert decrypt_credential(cfg.password_encrypted) == "my-smtp-password"


@pytest.mark.asyncio
async def test_create_email_config_aliyun(client_as_admin, db):
    """POST 创建 aliyun 配置，AK/SK 加密落库。"""
    session, _ = db
    resp = await client_as_admin.post(
        "/api/manager/email-configs",
        json=_aliyun_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "aliyun"
    assert data["region"] == "cn-hangzhou"
    assert data["from_email"] == "alerts@example.com"
    assert data["access_key_id_configured"] is True
    assert data["access_key_secret_configured"] is True
    # smtp 字段应为 null
    assert data["smtp_host"] is None
    assert data["password_configured"] is False

    # DB 验证：AK/SK 是密文
    cfg = (await session.execute(select(EmailConfig))).scalar_one()
    assert decrypt_credential(cfg.access_key_id_encrypted) == "LTAI-test-ak"
    assert decrypt_credential(cfg.access_key_secret_encrypted) == "test-sk-1234567890"


@pytest.mark.asyncio
async def test_create_email_config_tencent(client_as_admin, db):
    """POST 创建 tencent 配置。"""
    resp = await client_as_admin.post(
        "/api/manager/email-configs",
        json=_tencent_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "tencent"
    assert data["region"] == "ap-hongkong"
    assert data["access_key_id_configured"] is True


@pytest.mark.asyncio
async def test_create_email_config_huawei(client_as_admin, db):
    """POST 创建 huawei 配置。"""
    resp = await client_as_admin.post(
        "/api/manager/email-configs",
        json=_huawei_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "huawei"
    assert data["region"] == "cn-north-4"
    assert data["access_key_id_configured"] is True


# ── POST 校验 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_email_config_smtp_missing_password_422(client_as_admin):
    """smtp 缺 password → 422（schema model_validator）。"""
    payload = _smtp_payload()
    payload["password"] = None
    resp = await client_as_admin.post("/api/manager/email-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_email_config_aliyun_missing_ak_sk_422(client_as_admin):
    """aliyun 缺 AK/SK → 422（schema model_validator）。"""
    payload = _aliyun_payload()
    payload["access_key_id"] = None
    payload["access_key_secret"] = None
    resp = await client_as_admin.post("/api/manager/email-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_email_config_validates_provider_422(client_as_admin):
    """provider=invalid → 422（pydantic field_validator）。"""
    payload = _smtp_payload(provider="aws")
    resp = await client_as_admin.post("/api/manager/email-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_email_config_validates_encryption_422(client_as_admin):
    """encryption=invalid → 422。"""
    payload = _smtp_payload(encryption="tls")
    resp = await client_as_admin.post("/api/manager/email-configs", json=payload)
    assert resp.status_code == 422


# ── PUT 更新（留空不修改）─────────────────────────────────


@pytest.mark.asyncio
async def test_update_email_config_keep_password(client_as_admin, db):
    """PUT 留空 password → 原密文不变，其他字段更新。"""
    session, _ = db
    # 首次创建
    resp = await client_as_admin.post(
        "/api/manager/email-configs",
        json=_smtp_payload(),
    )
    cfg_id = resp.json()["id"]
    first_pwd_enc = (
        await session.execute(select(EmailConfig).where(EmailConfig.id == cfg_id))
    ).scalar_one().password_encrypted

    # PUT 更新：留空 password，改 smtp_host
    payload = _smtp_payload(
        smtp_host="smtp.163.com",
        password=None,
    )
    resp = await client_as_admin.put(
        f"/api/manager/email-configs/{cfg_id}", json=payload
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["smtp_host"] == "smtp.163.com"
    assert data["password_configured"] is True
    # password 密文不变
    cfg = (
        await session.execute(select(EmailConfig).where(EmailConfig.id == cfg_id))
    ).scalar_one()
    assert cfg.password_encrypted == first_pwd_enc


@pytest.mark.asyncio
async def test_update_email_config_keep_ak_sk(client_as_admin, db):
    """PUT 留空 AK/SK → 原密文不变。"""
    session, _ = db
    resp = await client_as_admin.post(
        "/api/manager/email-configs",
        json=_aliyun_payload(),
    )
    cfg_id = resp.json()["id"]
    first_ak_enc = (
        await session.execute(select(EmailConfig).where(EmailConfig.id == cfg_id))
    ).scalar_one().access_key_id_encrypted

    payload = _aliyun_payload(
        access_key_id=None,
        access_key_secret=None,
        region="ap-southeast-1",  # 改 region 验证其他字段更新
    )
    resp = await client_as_admin.put(
        f"/api/manager/email-configs/{cfg_id}", json=payload
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["region"] == "ap-southeast-1"
    assert data["access_key_id_configured"] is True
    # AK 密文不变
    cfg = (
        await session.execute(select(EmailConfig).where(EmailConfig.id == cfg_id))
    ).scalar_one()
    assert cfg.access_key_id_encrypted == first_ak_enc


# ── POST /{id}/activate ──────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_email_config_deactivates_others(client_as_admin, db):
    """POST /{id}/activate → 该行 active，其他行 deactivated。"""
    # 创建 3 条配置
    resp1 = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    resp2 = await client_as_admin.post(
        "/api/manager/email-configs", json=_aliyun_payload()
    )
    resp3 = await client_as_admin.post(
        "/api/manager/email-configs", json=_tencent_payload()
    )
    id1, id2, id3 = resp1.json()["id"], resp2.json()["id"], resp3.json()["id"]

    # 激活 id2
    resp = await client_as_admin.post(f"/api/manager/email-configs/{id2}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True

    # 列表验证：只有 id2 active
    resp = await client_as_admin.get("/api/manager/email-configs")
    cfgs = {c["id"]: c for c in resp.json()}
    assert cfgs[id1]["is_active"] is False
    assert cfgs[id2]["is_active"] is True
    assert cfgs[id3]["is_active"] is False

    # 激活 id3 → id2 应被 deactivate
    resp = await client_as_admin.post(f"/api/manager/email-configs/{id3}/activate")
    assert resp.json()["is_active"] is True
    resp = await client_as_admin.get("/api/manager/email-configs")
    cfgs = {c["id"]: c for c in resp.json()}
    assert cfgs[id2]["is_active"] is False
    assert cfgs[id3]["is_active"] is True


@pytest.mark.asyncio
async def test_activate_email_config_returns_404_when_missing(client_as_admin):
    """配置不存在 → 404。"""
    fake_id = str(uuid.uuid4())
    resp = await client_as_admin.post(f"/api/manager/email-configs/{fake_id}/activate")
    assert resp.status_code == 404


# ── POST /{id}/deactivate ────────────────────────────────────


@pytest.mark.asyncio
async def test_deactivate_email_config_clears_active(client_as_admin):
    """POST /{id}/deactivate → 该行 is_active=False，全局无 active。"""
    resp1 = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    resp2 = await client_as_admin.post(
        "/api/manager/email-configs", json=_aliyun_payload()
    )
    id1, id2 = resp1.json()["id"], resp2.json()["id"]

    # 激活 id1
    await client_as_admin.post(f"/api/manager/email-configs/{id1}/activate")
    # 再 deactivate
    resp = await client_as_admin.post(f"/api/manager/email-configs/{id1}/deactivate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    # 列表验证：全局无 active
    resp = await client_as_admin.get("/api/manager/email-configs")
    cfgs = {c["id"]: c for c in resp.json()}
    assert cfgs[id1]["is_active"] is False
    assert cfgs[id2]["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_email_config_idempotent_on_inactive(client_as_admin):
    """对已 inactive 的行调用 deactivate → 200，不报错。"""
    resp1 = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    id1 = resp1.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{id1}/deactivate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_email_config_returns_404_when_missing(client_as_admin):
    """配置不存在 → 404。"""
    fake_id = str(uuid.uuid4())
    resp = await client_as_admin.post(
        f"/api/manager/email-configs/{fake_id}/deactivate"
    )
    assert resp.status_code == 404


# ── 响应不含明文 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_email_configs_response_no_plaintext(client_as_admin):
    """GET 响应只有 *_configured bool，无明文字段。"""
    await client_as_admin.post("/api/manager/email-configs", json=_smtp_payload())
    await client_as_admin.post("/api/manager/email-configs", json=_aliyun_payload())
    resp = await client_as_admin.get("/api/manager/email-configs")
    assert resp.status_code == 200
    for cfg in resp.json():
        # 不应包含明文字段
        assert "password" not in cfg
        assert "password_encrypted" not in cfg
        assert "access_key_id_encrypted" not in cfg
        assert "access_key_secret" not in cfg
        assert "access_key_secret_encrypted" not in cfg
    # 应包含 *_configured bool
    smtp_cfg = next(c for c in resp.json() if c["provider"] == "smtp")
    assert smtp_cfg["password_configured"] is True
    aliyun_cfg = next(c for c in resp.json() if c["provider"] == "aliyun")
    assert aliyun_cfg["access_key_id_configured"] is True


# ── /test 探活 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_email_config_smtp_passes(client_as_admin, monkeypatch):
    """SMTP login 探活成功 → ok=True。"""
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTPSSL)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert _FakeSMTPSSL.last_client is not None
    assert _FakeSMTPSSL.last_client.login_called is True


@pytest.mark.asyncio
async def test_test_email_config_aliyun_passes(client_as_admin, monkeypatch):
    """aliyun SDK 探活成功 → ok=True（monkeypatch aliyun_provider.probe）。"""
    from app.services.email_providers import aliyun_provider

    called = {"probe": False}

    def fake_probe(cfg, secrets):
        called["probe"] = True
        assert cfg.provider == "aliyun"
        assert secrets["access_key_id"] == "LTAI-test-ak"

    monkeypatch.setattr(aliyun_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_aliyun_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert called["probe"] is True


@pytest.mark.asyncio
async def test_test_email_config_tencent_passes(client_as_admin, monkeypatch):
    """tencent SDK 探活成功 → ok=True。"""
    from app.services.email_providers import tencent_provider

    called = {"probe": False}

    def fake_probe(cfg, secrets):
        called["probe"] = True
        assert cfg.provider == "tencent"

    monkeypatch.setattr(tencent_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_tencent_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert called["probe"] is True


@pytest.mark.asyncio
async def test_test_email_config_huawei_passes(client_as_admin, monkeypatch):
    """huawei SDK 探活成功 → ok=True。"""
    from app.services.email_providers import huawei_provider

    called = {"probe": False}

    def fake_probe(cfg, secrets):
        called["probe"] = True
        assert cfg.provider == "huawei"

    monkeypatch.setattr(huawei_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_huawei_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert called["probe"] is True


@pytest.mark.asyncio
async def test_test_email_config_smtp_auth_error(client_as_admin, monkeypatch):
    """SMTP 认证失败 → ok=False + 错误信息含"认证失败"。"""

    class _AuthFailClient:
        def login(self, user, pw):
            raise smtplib.SMTPAuthenticationError(535, b"Auth fail")

        def quit(self):
            pass

    class _AuthFailSMTPSSL:
        def __new__(cls, host, port, timeout=None):
            return _AuthFailClient()

    monkeypatch.setattr(smtplib, "SMTP_SSL", _AuthFailSMTPSSL)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "认证失败" in data["error"]


@pytest.mark.asyncio
async def test_test_email_config_aliyun_auth_error(client_as_admin, monkeypatch):
    """aliyun SDK 认证失败 → ok=False + "认证失败"。"""
    from app.services.email_providers import aliyun_provider

    def fake_probe(cfg, secrets):
        raise Exception("InvalidAccessKeyId: AK/SK unauthorized")

    monkeypatch.setattr(aliyun_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_aliyun_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "认证失败" in data["error"]


@pytest.mark.asyncio
async def test_test_email_config_smtp_timeout(client_as_admin, monkeypatch):
    """SMTP 超时 → ok=False + "超时"。"""

    class _TimeoutClient:
        def login(self, user, pw):
            raise socket.timeout("timed out")

        def quit(self):
            pass

    class _TimeoutSMTPSSL:
        def __new__(cls, host, port, timeout=None):
            return _TimeoutClient()

    monkeypatch.setattr(smtplib, "SMTP_SSL", _TimeoutSMTPSSL)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "超时" in data["error"]


@pytest.mark.asyncio
async def test_test_email_config_smtp_connection_error(client_as_admin, monkeypatch):
    """SMTP 连接失败 → ok=False + "连接失败"。"""

    class _ConnFailSMTPSSL:
        def __new__(cls, host, port, timeout=None):
            raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _ConnFailSMTPSSL)

    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "连接失败" in data["error"]


@pytest.mark.asyncio
async def test_test_email_config_fails_when_password_decrypt_fails(client_as_admin, db):
    """密文被破坏 → ok=False + "解密失败"。"""
    session, _ = db
    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    cfg_id = resp.json()["id"]

    cfg = (await session.execute(select(EmailConfig).where(EmailConfig.id == cfg_id))).scalar_one()
    cfg.password_encrypted = "garbage-not-a-valid-fernet-token"
    await session.commit()

    resp = await client_as_admin.post(f"/api/manager/email-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "解密失败" in data["error"]


@pytest.mark.asyncio
async def test_test_email_config_returns_404_when_missing(client_as_admin):
    """配置不存在 → 404。"""
    fake_id = str(uuid.uuid4())
    resp = await client_as_admin.post(f"/api/manager/email-configs/{fake_id}/test")
    assert resp.status_code == 404


# ── DELETE 审计 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_email_config_audits(client_as_admin, db):
    """DELETE 写 email_config.delete 审计日志。"""
    session, user = db
    resp = await client_as_admin.post(
        "/api/manager/email-configs", json=_smtp_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.delete(f"/api/manager/email-configs/{cfg_id}")
    assert resp.status_code == 204

    # DB 验证：审计日志
    result = await session.execute(
        select(OperationLog).where(
            OperationLog.actor_id == user.id,
            OperationLog.action == "email_config.delete",
        )
    )
    logs = result.scalars().all()
    assert len(logs) >= 1
    assert str(logs[0].target_id) == cfg_id
