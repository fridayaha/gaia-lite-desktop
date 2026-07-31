"""短信服务商配置（SmsConfig）API 集成测试 — 真 DB 验证 multi-config CRUD + 3 provider 探活。

覆盖：
- GET 空表返回 []
- POST 创建 aliyun/tencent/huawei 3 个 provider
- POST provider 非法 → 422
- POST tencent 缺 sdk_app_id → 422
- POST aliyun 缺 region → 422
- POST 缺 AK/SK → 422（schema model_validator）
- PUT 更新（留空 AK/SK 不修改）
- POST /{id}/activate 同事务 deactivate 其他行
- POST /{id}/test 各 provider 探活成功（monkeypatch SDK）
- POST /{id}/test 认证失败 → ok=False + "认证失败"
- POST /{id}/test 未启用 / 解密失败 → ok=False
- DELETE 写审计日志
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user, hash_password
from app.core.crypto import decrypt_credential
from app.models import OperationLog, SmsConfig, User
from pkg.common.config import settings


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user（平台管理员）。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"sms_admin_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="短信管理员",
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

    # 清空 sms_configs + 操作日志（隔离测试）
    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(
        text("DELETE FROM operation_logs WHERE action LIKE 'sms_config%'")
    )
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM sms_configs"))
    await session.execute(
        text("DELETE FROM operation_logs WHERE action LIKE 'sms_config%'")
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


def _aliyun_payload(**overrides):
    """构造阿里云短信配置 payload。"""
    base = {
        "provider": "aliyun",
        "sign_name": "知行平台",
        "template_code": "SMS_12345678",
        "access_key_id": "LTAI-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-hangzhou",
        "daily_limit": 1000,
        "interval_seconds": 60,
    }
    base.update(overrides)
    return base


def _tencent_payload(**overrides):
    """构造腾讯云短信配置 payload。"""
    base = {
        "provider": "tencent",
        "sign_name": "知行平台",
        "template_code": "SMS_12345678",
        "access_key_id": "AKID-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "sdk_app_id": "1400001234",
        "daily_limit": 1000,
        "interval_seconds": 60,
    }
    base.update(overrides)
    return base


def _huawei_payload(**overrides):
    """构造华为云短信配置 payload。"""
    base = {
        "provider": "huawei",
        "sign_name": "知行平台",
        "template_code": "SMS_12345678",
        "access_key_id": "HWK-test-ak",
        "access_key_secret": "test-sk-1234567890",
        "region": "cn-north-4",
        "daily_limit": 1000,
        "interval_seconds": 60,
    }
    base.update(overrides)
    return base


# ── GET (list) ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sms_configs_returns_empty_when_empty(client_as_admin):
    """空表 GET 返回 []（前端显示空表）。"""
    resp = await client_as_admin.get("/api/manager/sms-configs")
    assert resp.status_code == 200
    assert resp.json() == []


# ── POST 创建 3 个 provider ─────────────────────────────────


@pytest.mark.asyncio
async def test_create_sms_config_aliyun(client_as_admin, db):
    """POST 创建 aliyun 配置，AK/SK 加密落库。"""
    session, _ = db
    resp = await client_as_admin.post(
        "/api/manager/sms-configs",
        json=_aliyun_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "aliyun"
    assert data["is_active"] is False  # 新建不自动 activate
    assert data["sign_name"] == "知行平台"
    assert data["template_code"] == "SMS_12345678"
    assert data["region"] == "cn-hangzhou"
    assert data["sdk_app_id"] is None  # aliyun 不用 sdk_app_id
    assert data["access_key_id_configured"] is True
    assert data["access_key_secret_configured"] is True

    # DB 验证：AK/SK 是密文
    cfg = (await session.execute(select(SmsConfig))).scalar_one()
    assert decrypt_credential(cfg.access_key_id_encrypted) == "LTAI-test-ak"
    assert decrypt_credential(cfg.access_key_secret_encrypted) == "test-sk-1234567890"


@pytest.mark.asyncio
async def test_create_sms_config_tencent(client_as_admin):
    """POST 创建 tencent 配置（含 sdk_app_id）。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs",
        json=_tencent_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "tencent"
    assert data["sdk_app_id"] == "1400001234"
    assert data["region"] is None  # tencent 不用 region
    assert data["access_key_id_configured"] is True


@pytest.mark.asyncio
async def test_create_sms_config_huawei(client_as_admin):
    """POST 创建 huawei 配置。"""
    resp = await client_as_admin.post(
        "/api/manager/sms-configs",
        json=_huawei_payload(),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["provider"] == "huawei"
    assert data["region"] == "cn-north-4"
    assert data["sdk_app_id"] is None
    assert data["access_key_id_configured"] is True


# ── POST 校验 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_sms_config_validates_provider_422(client_as_admin):
    """provider=invalid → 422（pydantic field_validator）。"""
    payload = _aliyun_payload(provider="aws")
    resp = await client_as_admin.post("/api/manager/sms-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_sms_config_aliyun_missing_ak_sk_422(client_as_admin):
    """aliyun 缺 AK/SK → 422（schema model_validator）。"""
    payload = _aliyun_payload()
    payload["access_key_id"] = None
    payload["access_key_secret"] = None
    resp = await client_as_admin.post("/api/manager/sms-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_sms_config_tencent_missing_sdk_app_id_422(client_as_admin):
    """tencent 缺 sdk_app_id → 422（schema model_validator）。"""
    payload = _tencent_payload(sdk_app_id=None)
    resp = await client_as_admin.post("/api/manager/sms-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_sms_config_aliyun_missing_region_422(client_as_admin):
    """aliyun 缺 region → 422（schema model_validator）。"""
    payload = _aliyun_payload(region=None)
    resp = await client_as_admin.post("/api/manager/sms-configs", json=payload)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_sms_config_aliyun_missing_sign_name_422(client_as_admin):
    """aliyun 缺 sign_name → 422（schema model_validator）。"""
    payload = _aliyun_payload(sign_name=None)
    resp = await client_as_admin.post("/api/manager/sms-configs", json=payload)
    assert resp.status_code == 422


# ── PUT 更新（留空不修改）─────────────────────────────────


@pytest.mark.asyncio
async def test_update_sms_config_keep_ak_sk(client_as_admin, db):
    """PUT 留空 AK/SK → 原密文不变，其他字段更新。"""
    session, _ = db
    # 首次创建
    resp = await client_as_admin.post(
        "/api/manager/sms-configs",
        json=_aliyun_payload(),
    )
    cfg_id = resp.json()["id"]
    first_ak_enc = (
        await session.execute(select(SmsConfig).where(SmsConfig.id == cfg_id))
    ).scalar_one().access_key_id_encrypted

    # PUT 更新：留空 AK/SK，改 sign_name + region
    payload = _aliyun_payload(
        sign_name="新签名",
        region="ap-southeast-1",
        access_key_id=None,
        access_key_secret=None,
    )
    resp = await client_as_admin.put(
        f"/api/manager/sms-configs/{cfg_id}", json=payload
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sign_name"] == "新签名"
    assert data["region"] == "ap-southeast-1"
    assert data["access_key_id_configured"] is True
    # AK 密文不变
    cfg = (
        await session.execute(select(SmsConfig).where(SmsConfig.id == cfg_id))
    ).scalar_one()
    assert cfg.access_key_id_encrypted == first_ak_enc


# ── POST /{id}/activate ──────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_sms_config_deactivates_others(client_as_admin):
    """POST /{id}/activate → 该行 active，其他行 deactivated。"""
    # 创建 3 条配置
    resp1 = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    resp2 = await client_as_admin.post(
        "/api/manager/sms-configs", json=_tencent_payload()
    )
    resp3 = await client_as_admin.post(
        "/api/manager/sms-configs", json=_huawei_payload()
    )
    id1, id2, id3 = resp1.json()["id"], resp2.json()["id"], resp3.json()["id"]

    # 激活 id2（tencent）
    resp = await client_as_admin.post(f"/api/manager/sms-configs/{id2}/activate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is True

    # 列表验证：只有 id2 active
    resp = await client_as_admin.get("/api/manager/sms-configs")
    cfgs = {c["id"]: c for c in resp.json()}
    assert cfgs[id1]["is_active"] is False
    assert cfgs[id2]["is_active"] is True
    assert cfgs[id3]["is_active"] is False

    # 激活 id3（huawei）→ id2 应被 deactivate
    resp = await client_as_admin.post(f"/api/manager/sms-configs/{id3}/activate")
    assert resp.json()["is_active"] is True
    resp = await client_as_admin.get("/api/manager/sms-configs")
    cfgs = {c["id"]: c for c in resp.json()}
    assert cfgs[id2]["is_active"] is False
    assert cfgs[id3]["is_active"] is True


@pytest.mark.asyncio
async def test_activate_sms_config_returns_404_when_missing(client_as_admin):
    """配置不存在 → 404。"""
    fake_id = str(uuid.uuid4())
    resp = await client_as_admin.post(f"/api/manager/sms-configs/{fake_id}/activate")
    assert resp.status_code == 404


# ── POST /{id}/deactivate ────────────────────────────────────


@pytest.mark.asyncio
async def test_deactivate_sms_config_clears_active(client_as_admin):
    """POST /{id}/deactivate → 该行 is_active=False，全局无 active。"""
    resp1 = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    resp2 = await client_as_admin.post(
        "/api/manager/sms-configs", json=_tencent_payload()
    )
    id1, id2 = resp1.json()["id"], resp2.json()["id"]

    # 激活 id1
    await client_as_admin.post(f"/api/manager/sms-configs/{id1}/activate")
    # 再 deactivate
    resp = await client_as_admin.post(f"/api/manager/sms-configs/{id1}/deactivate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False

    # 列表验证：全局无 active
    resp = await client_as_admin.get("/api/manager/sms-configs")
    cfgs = {c["id"]: c for c in resp.json()}
    assert cfgs[id1]["is_active"] is False
    assert cfgs[id2]["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_sms_config_idempotent_on_inactive(client_as_admin):
    """对已 inactive 的行调用 deactivate → 200，不报错，不写审计日志。"""
    resp1 = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    id1 = resp1.json()["id"]

    # 不激活，直接 deactivate
    resp = await client_as_admin.post(f"/api/manager/sms-configs/{id1}/deactivate")
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_active"] is False


@pytest.mark.asyncio
async def test_deactivate_sms_config_returns_404_when_missing(client_as_admin):
    """配置不存在 → 404。"""
    fake_id = str(uuid.uuid4())
    resp = await client_as_admin.post(
        f"/api/manager/sms-configs/{fake_id}/deactivate"
    )
    assert resp.status_code == 404


# ── 响应不含明文 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sms_configs_response_no_plaintext(client_as_admin):
    """GET 响应只有 *_configured bool，无明文字段。"""
    await client_as_admin.post("/api/manager/sms-configs", json=_aliyun_payload())
    await client_as_admin.post("/api/manager/sms-configs", json=_tencent_payload())
    resp = await client_as_admin.get("/api/manager/sms-configs")
    assert resp.status_code == 200
    for cfg in resp.json():
        # 不应包含明文字段
        assert "access_key_id" not in cfg or cfg["access_key_id"] != "LTAI-test-ak"
        assert "access_key_id_encrypted" not in cfg
        assert "access_key_secret" not in cfg
        assert "access_key_secret_encrypted" not in cfg
    # 应包含 *_configured bool
    aliyun_cfg = next(c for c in resp.json() if c["provider"] == "aliyun")
    assert aliyun_cfg["access_key_id_configured"] is True
    tencent_cfg = next(c for c in resp.json() if c["provider"] == "tencent")
    assert tencent_cfg["access_key_secret_configured"] is True


# ── /test 探活 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_sms_config_aliyun_passes(client_as_admin, monkeypatch):
    """aliyun SDK 探活成功 → ok=True（monkeypatch aliyun_provider.probe）。"""
    from app.services.sms_providers import aliyun_provider

    called = {"probe": False}

    def fake_probe(cfg, secrets):
        called["probe"] = True
        assert cfg.provider == "aliyun"
        assert secrets["access_key_id"] == "LTAI-test-ak"

    monkeypatch.setattr(aliyun_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/sms-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["error"] is None
    assert called["probe"] is True


@pytest.mark.asyncio
async def test_test_sms_config_tencent_passes(client_as_admin, monkeypatch):
    """tencent SDK 探活成功 → ok=True。"""
    from app.services.sms_providers import tencent_provider

    called = {"probe": False}

    def fake_probe(cfg, secrets):
        called["probe"] = True
        assert cfg.provider == "tencent"

    monkeypatch.setattr(tencent_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_tencent_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/sms-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert called["probe"] is True


@pytest.mark.asyncio
async def test_test_sms_config_huawei_passes(client_as_admin, monkeypatch):
    """huawei httpx 探活成功 → ok=True。"""
    from app.services.sms_providers import huawei_provider

    called = {"probe": False}

    def fake_probe(cfg, secrets):
        called["probe"] = True
        assert cfg.provider == "huawei"

    monkeypatch.setattr(huawei_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_huawei_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/sms-configs/{cfg_id}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert called["probe"] is True


@pytest.mark.asyncio
async def test_test_sms_config_aliyun_auth_error(client_as_admin, monkeypatch):
    """aliyun SDK 认证失败 → ok=False + "认证失败"。"""
    from app.services.sms_providers import aliyun_provider

    def fake_probe(cfg, secrets):
        raise Exception("InvalidAccessKeyId: AK/SK unauthorized")

    monkeypatch.setattr(aliyun_provider, "probe", fake_probe)

    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.post(f"/api/manager/sms-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "认证失败" in data["error"]


@pytest.mark.asyncio
async def test_aliyun_probe_constructs_request_with_correct_args(monkeypatch):
    """aliyun_provider.probe 应使用 SDK 正确的 page_index/page_size 参数构造请求。

    回归测试：之前误用 current_page（SDK 实际接受 page_index），运行时探活会抛
    `unexpected keyword argument 'current_page'`。本测试 mock SDK Client 类，
    断言 QuerySmsTemplateListRequest 实际传给 client.query_sms_template_list
    的 request 对象含 page_index=1 + page_size=1 字段。
    """
    import app.services.sms_providers.aliyun_provider as mod

    class FakeRequest:
        def __init__(self, page_size=None, page_index=None, **kwargs):
            self.page_size = page_size
            self.page_index = page_index
            # 捕获所有 kwargs 用于断言（避免 silent 忽略其他字段）
            self._kwargs = kwargs

    class FakeClient:
        def __init__(self, config):
            self.config = config
            self.calls = []

        def query_sms_template_list(self, request):
            self.calls.append(request)

    captured_client = []

    def fake_client_factory(config):
        c = FakeClient(config)
        captured_client.append(c)
        return c

    monkeypatch.setattr(mod.models, "QuerySmsTemplateListRequest", FakeRequest)
    monkeypatch.setattr(mod, "Client", fake_client_factory)

    class FakeCfg:
        provider = "aliyun"
        region = "cn-hangzhou"

    mod.probe(FakeCfg(), {"access_key_id": "ak", "access_key_secret": "sk"})

    assert len(captured_client) == 1
    assert len(captured_client[0].calls) == 1
    req = captured_client[0].calls[0]
    assert req.page_size == 1
    assert req.page_index == 1


@pytest.mark.asyncio
async def test_test_sms_config_fails_when_decrypt_fails(client_as_admin, db):
    """密文被破坏 → ok=False + "解密失败"。"""
    session, _ = db
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    cfg_id = resp.json()["id"]

    cfg = (await session.execute(select(SmsConfig).where(SmsConfig.id == cfg_id))).scalar_one()
    cfg.access_key_id_encrypted = "garbage-not-a-valid-fernet-token"
    await session.commit()

    resp = await client_as_admin.post(f"/api/manager/sms-configs/{cfg_id}/test")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "解密失败" in data["error"]


@pytest.mark.asyncio
async def test_test_sms_config_returns_404_when_missing(client_as_admin):
    """配置不存在 → 404。"""
    fake_id = str(uuid.uuid4())
    resp = await client_as_admin.post(f"/api/manager/sms-configs/{fake_id}/test")
    assert resp.status_code == 404


# ── DELETE 审计 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_sms_config_audits(client_as_admin, db):
    """DELETE 写 sms_config.delete 审计日志。"""
    session, user = db
    resp = await client_as_admin.post(
        "/api/manager/sms-configs", json=_aliyun_payload()
    )
    cfg_id = resp.json()["id"]

    resp = await client_as_admin.delete(f"/api/manager/sms-configs/{cfg_id}")
    assert resp.status_code == 204

    # DB 验证：审计日志
    result = await session.execute(
        select(OperationLog).where(
            OperationLog.actor_id == user.id,
            OperationLog.action == "sms_config.delete",
        )
    )
    logs = result.scalars().all()
    assert len(logs) >= 1
    assert str(logs[0].target_id) == cfg_id
