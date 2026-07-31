"""引擎配置（EngineConfig）相关测试。

测试分两类：
1. 纯 mock 测试（不依赖 DB）— DifyConsoleClient + _validate_dify_config_async + _verify_dify_service_api
2. DB 集成测试 — upsert/get/masks（本地无 PostgreSQL 时跳过）
"""

from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.core.dify_console_client import (
    DifyConsoleClient,
    DifyConsoleError,
    map_dify_mode_to_app_type,
)


# ── map_dify_mode_to_app_type ──


def test_map_mode_chat():
    assert map_dify_mode_to_app_type("chat") == "chat"


def test_map_mode_agent_chat():
    assert map_dify_mode_to_app_type("agent-chat") == "agent"


def test_map_mode_advanced_chat():
    assert map_dify_mode_to_app_type("advanced-chat") == "workflow"


def test_map_mode_workflow():
    assert map_dify_mode_to_app_type("workflow") == "workflow"


def test_map_mode_completion_unsupported():
    # completion 不支持，返回 None（调用方应过滤）
    assert map_dify_mode_to_app_type("completion") is None


def test_map_mode_none_or_empty():
    assert map_dify_mode_to_app_type(None) is None
    assert map_dify_mode_to_app_type("") is None


# ── DifyConsoleClient.login ──


@pytest.mark.asyncio
async def test_client_login_success():
    """账号密码换 token 成功，返回 (token, expires_at)。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {"access_token": "jwt-token-xyz", "refresh_token": "rt"}
    }
    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        token, expires_at = await client.login()
    assert token == "jwt-token-xyz"
    assert expires_at is not None  # 应该是未来时间


@pytest.mark.asyncio
async def test_client_login_401_invalid_credentials():
    """401 → DifyConsoleError(is_auth_error=True)。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "wrong")
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = '{"message": "Invalid email or password"}'
    mock_resp.json.return_value = {"message": "Invalid email or password"}
    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(DifyConsoleError) as exc:
            await client.login()
    assert exc.value.is_auth_error is True
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_client_login_missing_access_token():
    """响应缺 access_token（body + cookie 都没有）→ DifyConsoleError。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {}}  # 缺 access_token
    mock_resp.cookies = httpx.Cookies()  # cookie 也为空
    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(DifyConsoleError, match="access_token"):
            await client.login()


@pytest.mark.asyncio
async def test_client_login_cookie_based_dify_1x():
    """Dify 1.x: 响应体只返回 {"result": "success"}，token 在 Set-Cookie 里。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": "success"}
    mock_resp.cookies = httpx.Cookies({"access_token": "cookie-token-xyz"})
    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        token, expires_at = await client.login()
    assert token == "cookie-token-xyz"
    assert expires_at is not None


@pytest.mark.asyncio
async def test_client_login_extracts_csrf_token_cookie():
    """Dify 1.x login 同时下发 csrf_token cookie，后续请求需 X-CSRF-Token 头匹配。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"result": "success"}
    mock_resp.cookies = httpx.Cookies({
        "access_token": "cookie-token-xyz",
        "csrf_token": "csrf-jwt-abc",
    })
    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        await client.login()
    assert client._csrf_token == "csrf-jwt-abc"


@pytest.mark.asyncio
async def test_list_apps_sends_csrf_header():
    """list_apps 请求带 X-CSRF-Token 头（Dify 1.x login_required 强制校验）。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    client._token = "tok"
    client._expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    client._csrf_token = "csrf-jwt-abc"

    apps_resp = MagicMock()
    apps_resp.status_code = 200
    apps_resp.json.return_value = {"data": [], "has_more": False}
    captured: dict = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return apps_resp

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=fake_request)):
        await client.list_apps()

    headers = captured.get("headers", {})
    assert headers.get("X-CSRF-Token") == "csrf-jwt-abc", "必须发 X-CSRF-Token 头匹配 cookie"
    assert headers.get("Authorization") == "Bearer tok"
    await client.close()


@pytest.mark.asyncio
async def test_client_login_password_is_base64_encoded():
    """Dify 1.x 期望 password 字段是 Base64 编码（不是明文）。
    参考 /app/api/libs/encryption.py FieldEncryption.decrypt_field。
    """
    import base64 as _b64

    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "plain-pw-123")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"access_token": "tok"}}
    captured: dict = {}

    async def fake_request(method, url, **kwargs):
        captured.update(kwargs)
        return mock_resp

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=fake_request)):
        await client.login()

    payload = captured.get("json", {})
    sent_pw = payload.get("password", "")
    assert sent_pw != "plain-pw-123", "password 不能明文发送"
    assert _b64.b64decode(sent_pw).decode("utf-8") == "plain-pw-123", "Base64 解码后必须是原密码"


# ── DifyConsoleClient.list_apps ──


@pytest.mark.asyncio
async def test_list_apps_filters_completion_mode():
    """list_apps 过滤掉 completion 模式（我们 app_type 不支持）。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    # 模拟 login 成功
    login_resp = MagicMock()
    login_resp.status_code = 200
    login_resp.json.return_value = {"data": {"access_token": "tok"}}

    # 模拟 list_apps 返回 4 个应用，其中 1 个 completion 应被过滤
    apps_resp = MagicMock()
    apps_resp.status_code = 200
    apps_resp.json.return_value = {
        "data": [
            {"id": "a1", "name": "Chat App", "mode": "chat"},
            {"id": "a2", "name": "Agent App", "mode": "agent-chat"},
            {"id": "a3", "name": "Workflow App", "mode": "workflow"},
            {"id": "a4", "name": "Completion App", "mode": "completion"},  # 应被过滤
        ],
        "has_more": False,
    }

    async def mock_request_side_effect(method, url, **kwargs):
        if "/login" in url:
            return login_resp
        if "/apps" in url:
            return apps_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=mock_request_side_effect)):
        # 先登录拿 token
        await client.login()
        apps = await client.list_apps()

    # completion 被过滤
    assert len(apps) == 3
    modes = [a["mode"] for a in apps]
    assert "completion" not in modes


# ── DifyConsoleClient.get_app_api_keys ──


@pytest.mark.asyncio
async def test_get_app_api_keys_returns_token_field():
    """get_app_api_keys 返回 Dify 数据，每个 key 含 token 字段。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    login_resp = MagicMock(status_code=200)
    login_resp.json.return_value = {"data": {"access_token": "tok"}}

    keys_resp = MagicMock(status_code=200)
    keys_resp.json.return_value = {
        "data": [{"id": "k1", "token": "app-api-key-abc", "last_used_at": None}]
    }

    async def side_effect(method, url, **kwargs):
        if "/login" in url:
            return login_resp
        if "/api-keys" in url:
            return keys_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=side_effect)):
        await client.login()
        keys = await client.get_app_api_keys("app-123")

    assert len(keys) == 1
    assert keys[0]["token"] == "app-api-key-abc"


# ── DifyConsoleClient.create_app_api_key ──


@pytest.mark.asyncio
async def test_create_app_api_key_returns_token():
    """create_app_api_key 返回新创建的 token。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    login_resp = MagicMock(status_code=200)
    login_resp.json.return_value = {"data": {"access_token": "tok"}}

    create_resp = MagicMock(status_code=200)
    create_resp.json.return_value = {"id": "k-new", "token": "new-api-key-xyz"}

    async def side_effect(method, url, **kwargs):
        if "/login" in url:
            return login_resp
        if method == "POST" and "/api-keys" in url:
            return create_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=side_effect)):
        await client.login()
        new_key = await client.create_app_api_key("app-123")

    assert new_key["token"] == "new-api-key-xyz"


# ── DifyConsoleClient.get_app_token_costs ──


@pytest.mark.asyncio
async def test_get_app_token_costs_message_mode_uses_statistics_endpoint():
    """message/agent-chat/chat 模式调 /statistics/token-costs，返回含 total_price。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    login_resp = MagicMock(status_code=200)
    login_resp.json.return_value = {"data": {"access_token": "tok"}}

    cost_resp = MagicMock(status_code=200)
    cost_resp.json.return_value = {
        "data": [
            {"date": "2026-07-04", "token_count": 400, "total_price": "0.0007920", "currency": "USD"}
        ]
    }

    captured: dict = {}

    async def side_effect(method, url, **kwargs):
        if "/login" in url:
            return login_resp
        if "/statistics/token-costs" in url:
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return cost_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=side_effect)):
        await client.login()
        rows = await client.get_app_token_costs("app-1", "agent-chat", "2026-07-01 00:00", "2026-07-05 00:00")

    assert len(rows) == 1
    assert rows[0]["total_price"] == "0.0007920"
    assert rows[0]["currency"] == "USD"
    # URL 走 /statistics/token-costs（非 workflow）
    assert "/workflow/" not in captured["url"]
    assert captured["params"] == {"start": "2026-07-01 00:00", "end": "2026-07-05 00:00"}


@pytest.mark.asyncio
async def test_get_app_token_costs_workflow_mode_uses_workflow_endpoint():
    """workflow 模式调 /workflow/statistics/token-costs，无 total_price 字段。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    login_resp = MagicMock(status_code=200)
    login_resp.json.return_value = {"data": {"access_token": "tok"}}

    cost_resp = MagicMock(status_code=200)
    cost_resp.json.return_value = {
        "data": [{"date": "2026-07-04", "token_count": "126"}]  # 无 total_price
    }

    captured: dict = {}

    async def side_effect(method, url, **kwargs):
        if "/login" in url:
            return login_resp
        if "/workflow/statistics/token-costs" in url:
            captured["url"] = url
            return cost_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=side_effect)):
        await client.login()
        rows = await client.get_app_token_costs("app-wf", "workflow", "2026-07-01 00:00", "2026-07-05 00:00")

    assert len(rows) == 1
    assert "total_price" not in rows[0] or rows[0].get("total_price") is None
    assert "/workflow/statistics/token-costs" in captured["url"]


@pytest.mark.asyncio
async def test_get_app_token_costs_returns_empty_list_when_no_data():
    """Dify 返回空 data → 返回空 list。"""
    client = DifyConsoleClient("https://dify.example.com", "x@y.com", "pw")
    login_resp = MagicMock(status_code=200)
    login_resp.json.return_value = {"data": {"access_token": "tok"}}

    cost_resp = MagicMock(status_code=200)
    cost_resp.json.return_value = {"data": []}

    async def side_effect(method, url, **kwargs):
        if "/login" in url:
            return login_resp
        if "/statistics/token-costs" in url:
            return cost_resp
        return MagicMock(status_code=404)

    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=side_effect)):
        await client.login()
        rows = await client.get_app_token_costs("app-1", "chat", "2026-07-01 00:00", "2026-07-05 00:00")

    assert rows == []


# ── _verify_dify_service_api ──


@pytest.mark.asyncio
async def test_verify_dify_service_api_success():
    """调 /v1/info 成功，返回 name + mode。"""
    from app.api.agent_instances import _verify_dify_service_api

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "name": "My Chat App",
        "mode": "chat",
        "description": "客服机器人",
    }
    with patch("httpx.AsyncClient.request", new=AsyncMock(return_value=mock_resp)):
        # httpx.AsyncClient 是 context manager，需要 mock __aenter__/__aexit__
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            info = await _verify_dify_service_api("https://dify.example.com", "key-abc")

    assert info["name"] == "My Chat App"
    assert info["mode"] == "chat"
    assert info["description"] == "客服机器人"


@pytest.mark.asyncio
async def test_verify_dify_service_api_401_raises_400():
    """401 → HTTPException(400)。"""
    from fastapi import HTTPException

    from app.api.agent_instances import _verify_dify_service_api

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "Unauthorized"
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get = AsyncMock(return_value=mock_resp)
    with patch("httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(HTTPException) as exc:
            await _verify_dify_service_api("https://dify.example.com", "bad-key")
    assert exc.value.status_code == 400
    assert "app_api_key 无效" in str(exc.value.detail)


# ── _validate_dify_config_async（不需要真 DB，用 mock session）──


@pytest.mark.asyncio
async def test_validate_dify_managed_mode_no_credentials_required():
    """MANAGED 模式 → 不要求 base_url/app_api_key。"""
    from app.api.agent_instances import _validate_dify_config_async
    from app.models import DifyEngineMode

    # mock db.execute 返回一个 MANAGED 模式的 EngineConfig
    mock_cfg = MagicMock()
    mock_cfg.mode = DifyEngineMode.MANAGED
    mock_cfg.admin_email = None
    mock_cfg.admin_password_encrypted = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_cfg
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    # dify_config 为空，MANAGED 模式应该不报错
    await _validate_dify_config_async(
        "DIFY", {}, mock_db
    )  # 不抛异常即通过


@pytest.mark.asyncio
async def test_validate_dify_external_with_admin_requires_app_id():
    """EXTERNAL + 配了 admin → 要求 app_id。"""
    from app.api.agent_instances import _validate_dify_config_async
    from app.models import DifyEngineMode
    from fastapi import HTTPException

    mock_cfg = MagicMock()
    mock_cfg.mode = DifyEngineMode.EXTERNAL
    mock_cfg.base_url = "https://dify.example.com"
    mock_cfg.admin_email = "x@y.com"
    mock_cfg.admin_password_encrypted = "encrypted-token"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_cfg
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    # 没填 app_id → 报错
    with pytest.raises(HTTPException, match="app_id"):
        await _validate_dify_config_async("DIFY", {"base_url": "https://dify.example.com", "app_api_key": "k", "app_type": "chat"}, mock_db)


@pytest.mark.asyncio
async def test_validate_dify_external_no_admin_requires_base_url_and_key():
    """EXTERNAL + 未配 admin → 要求 base_url + app_api_key + app_type（保持现状）。"""
    from app.api.agent_instances import _validate_dify_config_async
    from app.models import DifyEngineMode
    from fastapi import HTTPException

    # mock：EXTERNAL 模式但未配 admin 账号
    mock_cfg = MagicMock()
    mock_cfg.mode = DifyEngineMode.EXTERNAL
    mock_cfg.base_url = "https://dify.example.com"
    mock_cfg.admin_email = None
    mock_cfg.admin_password_encrypted = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_cfg
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    # 缺 base_url → 报错
    with pytest.raises(HTTPException, match="base_url 必填"):
        await _validate_dify_config_async("DIFY", {"app_api_key": "k", "app_type": "chat"}, mock_db)

    # 缺 app_api_key → 报错
    with pytest.raises(HTTPException, match="app_api_key"):
        await _validate_dify_config_async(
            "DIFY", {"base_url": "https://x.com", "app_type": "chat"}, mock_db
        )

    # 缺 app_type → 报错
    with pytest.raises(HTTPException, match="app_type"):
        await _validate_dify_config_async(
            "DIFY",
            {"base_url": "https://x.com", "app_api_key": "k"},
            mock_db,
        )

    # 三个都填 → 通过
    await _validate_dify_config_async(
        "DIFY",
        {"base_url": "https://x.com", "app_api_key": "k", "app_type": "chat"},
        mock_db,
    )


@pytest.mark.asyncio
async def test_validate_dify_no_engine_config_treats_as_external_no_admin():
    """没有 EngineConfig（None）→ 视为 EXTERNAL + 无 admin，要求 base_url + app_api_key。"""
    from app.api.agent_instances import _validate_dify_config_async
    from fastapi import HTTPException

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None  # 没有 EngineConfig
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException, match="base_url 必填"):
        await _validate_dify_config_async("DIFY", {}, mock_db)


# ── _validate_dify_config（同步版本，字段格式校验）──


def test_validate_dify_config_base_url_format():
    """base_url 不合法 → 报错。"""
    from app.api.agent_instances import _validate_dify_config
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="base_url 必须"):
        _validate_dify_config("DIFY", {"base_url": "ftp://wrong.com"})

    # http:// 合法
    _validate_dify_config("DIFY", {"base_url": "http://ok.com", "app_api_key": "k", "app_type": "chat"})


def test_validate_dify_config_app_type_invalid():
    """app_type 不在合法值列表 → 报错。"""
    from app.api.agent_instances import _validate_dify_config
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="app_type 必须"):
        _validate_dify_config("DIFY", {"app_type": "invalid"})


def test_validate_dify_config_non_dify_skip():
    """engine_type 非 DIFY → 跳过校验。"""
    from app.api.agent_instances import _validate_dify_config

    # HERMES 不校验 dify 字段
    _validate_dify_config("HERMES", {})  # 不抛异常


def test_validate_dify_config_dify_not_object():
    """dify_config 非对象 → 报错。"""
    from app.api.agent_instances import _validate_dify_config
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="必须是对象"):
        _validate_dify_config("DIFY", "not-an-object")


# ── /api/manager/engine-configs/{id}/test-langfuse ──────────────


@pytest.fixture(autouse=True)
def bypass_platform_admin(monkeypatch):
    """旁路 require_platform_admin()（其内部调 is_platform_admin，mock 成 True）。

    autouse：本文件所有 HTTP endpoint 测试都需要，纯函数测试不受影响（不调 is_platform_admin）。
    """
    monkeypatch.setattr("app.core.auth.is_platform_admin", lambda _u: True)


def _make_engine_config_with_langfuse(*, with_langfuse: bool = True):
    """构造一个 EngineConfig-like 对象（用 SimpleNamespace 模拟列属性）。"""
    from types import SimpleNamespace
    from app.core.crypto import encrypt_credential

    if with_langfuse:
        langfuse_host = "https://lf.example.com"
        langfuse_public_key = "pk-lf-xxx"
        langfuse_secret_key_encrypted = encrypt_credential("sk-lf-yyy")
    else:
        langfuse_host = None
        langfuse_public_key = None
        langfuse_secret_key_encrypted = None

    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        engine_type="DIFY",
        mode="EXTERNAL",
        base_url="https://dify.example.com",
        admin_email="admin@example.com",
        admin_password_encrypted=encrypt_credential("secret"),
        langfuse_host=langfuse_host,
        langfuse_public_key=langfuse_public_key,
        langfuse_secret_key_encrypted=langfuse_secret_key_encrypted,
        cached_access_token_encrypted=None,
        cached_token_expires_at=None,
    )


@pytest.mark.asyncio
async def test_langfuse_endpoint_returns_ok_with_trace_count(client, mock_db_session, monkeypatch):
    """Langfuse 配齐 + list_traces 返回数据 → ok=True + trace_count=meta.totalItems。"""
    from app.api import engine_configs

    cfg = _make_engine_config_with_langfuse(with_langfuse=True)
    mock_db_session.get = AsyncMock(return_value=cfg)

    async def _fake_list_traces(**kwargs):
        return {
            "data": [{"id": "t1"}],
            "meta": {"page": 1, "totalPages": 100, "totalItems": 42},
        }

    monkeypatch.setattr(engine_configs, "list_traces", _fake_list_traces)

    resp = await client.post(
        "/api/manager/engine-configs/00000000-0000-0000-0000-000000000001/test-langfuse"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["trace_count"] == 42
    assert data.get("error") is None


@pytest.mark.asyncio
async def test_langfuse_endpoint_returns_not_configured(client, mock_db_session, monkeypatch):
    """EngineConfig 无 Langfuse 凭据 → ok=False + error 含'未配置'。"""
    from app.api import engine_configs

    cfg = _make_engine_config_with_langfuse(with_langfuse=False)
    mock_db_session.get = AsyncMock(return_value=cfg)

    # list_traces 不应被调用
    async def _should_not_call(**kwargs):
        raise AssertionError("list_traces 不应被调用")

    monkeypatch.setattr(engine_configs, "list_traces", _should_not_call)

    resp = await client.post(
        "/api/manager/engine-configs/00000000-0000-0000-0000-000000000001/test-langfuse"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "未配置" in data["error"]
    assert data.get("trace_count") is None


@pytest.mark.asyncio
async def test_langfuse_endpoint_returns_error_on_list_traces_exception(
    client, mock_db_session, monkeypatch
):
    """list_traces 抛异常 → ok=False + error 含'Langfuse 调用失败'。"""
    from app.api import engine_configs

    cfg = _make_engine_config_with_langfuse(with_langfuse=True)
    mock_db_session.get = AsyncMock(return_value=cfg)

    async def _boom(**kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(engine_configs, "list_traces", _boom)

    resp = await client.post(
        "/api/manager/engine-configs/00000000-0000-0000-0000-000000000001/test-langfuse"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "Langfuse 调用失败" in data["error"]
    assert "connection refused" in data["error"]


@pytest.mark.asyncio
async def test_langfuse_endpoint_returns_404_when_config_missing(client, mock_db_session):
    """EngineConfig 不存在 → 404。"""
    mock_db_session.get = AsyncMock(return_value=None)
    resp = await client.post(
        "/api/manager/engine-configs/00000000-0000-0000-0000-000000000001/test-langfuse"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_langfuse_endpoint_falls_back_to_data_length_when_no_total_items(
    client, mock_db_session, monkeypatch
):
    """老版本 Langfuse meta 无 totalItems → 降级用 len(data)。"""
    from app.api import engine_configs

    cfg = _make_engine_config_with_langfuse(with_langfuse=True)
    mock_db_session.get = AsyncMock(return_value=cfg)

    async def _fake_list_traces(**kwargs):
        return {
            "data": [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}],
            "meta": {"page": 1, "totalPages": 1},  # 无 totalItems
        }

    monkeypatch.setattr(engine_configs, "list_traces", _fake_list_traces)

    resp = await client.post(
        "/api/manager/engine-configs/00000000-0000-0000-0000-000000000001/test-langfuse"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["trace_count"] == 3  # len(data)
