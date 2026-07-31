"""hub-proxy 反代单测：JWT 鉴权 → 角色映射 → X-* 头注入 → httpx 转发。"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import hub_proxy
from app.core.auth import get_current_user
from app.main import app
from app.models import User

from .conftest import FakeObj, TEST_USER_ID, TEST_USERNAME


def _admin_user() -> User:
    """有 litellm:model:manage 权限的平台管理员。"""
    perm = FakeObj(code="litellm:model:manage")
    role = FakeObj(name="系统管理员", permissions=[perm])
    user = MagicMock(spec=User)
    user.id = TEST_USER_ID
    user.username = TEST_USERNAME
    user.email = "admin@example.com"
    user.is_active = True
    user.roles = [role]
    return user


def _plain_user() -> User:
    """无平台管理员权限的普通用户。"""
    user = MagicMock(spec=User)
    user.id = TEST_USER_ID
    user.username = "operator1"
    user.email = "op@example.com"
    user.is_active = True
    user.roles = []
    return user


class _FakeStreamResp:
    """模拟 httpx 流式响应。"""

    def __init__(self, status_code=200, body=b'{"ok":1}', headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {"content-type": "application/json"}
        self.aclose = AsyncMock()

    async def aiter_bytes(self):
        yield self._body


class _FakeClient:
    """模拟 httpx.AsyncClient，捕获转发请求 + 返回固定响应。"""

    captured: dict = {}

    def __init__(self, *args, **kwargs):
        self._resp = _FakeClient._resp
        self.aclose = AsyncMock()

    def build_request(self, method, url, content=None, headers=None):
        _FakeClient.captured = {
            "method": method,
            "url": url,
            "body": content,
            "headers": dict(headers or {}),
        }
        return MagicMock()

    async def send(self, req, stream=True):
        return _FakeClient._resp


@pytest.fixture
def fake_httpx(monkeypatch):
    _FakeClient.captured = {}
    _FakeClient._resp = _FakeStreamResp()
    monkeypatch.setattr(hub_proxy.httpx, "AsyncClient", _FakeClient)
    return _FakeClient


@pytest.fixture
def admin_client(fake_httpx, mock_db_session):
    app.dependency_overrides[get_current_user] = lambda: _admin_user()
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def test_map_hub_roles_admin():
    assert hub_proxy._map_hub_roles(_admin_user()) == ["platform_admin"]


def test_map_hub_roles_plain():
    assert hub_proxy._map_hub_roles(_plain_user()) == ["runtime_consumer"]


@pytest.mark.asyncio
async def test_proxy_injects_identity_headers(admin_client):
    """平台管理员 → X-Roles: platform_admin，且注入 X-Actor-ID 等身份头。"""
    resp = await admin_client.post(
        "/api/hub/presets/init",
        json={"x": 1},
        headers={"X-Roles": "runtime_consumer"},  # 客户端伪造，应被覆盖
    )
    assert resp.status_code == 200
    cap = _FakeClient.captured
    assert cap["method"] == "POST"
    assert cap["url"].endswith("/api/hub/presets/init")
    assert cap["headers"]["X-Roles"] == "platform_admin"
    assert cap["headers"]["X-Actor-ID"] == str(TEST_USER_ID)
    assert cap["headers"]["X-Actor-Type"] == "user"
    assert cap["headers"]["X-User-Name"] == TEST_USERNAME
    # 客户端伪造的 X-Roles 被服务端值覆盖（不被信任）
    assert cap["headers"]["X-Roles"] != "runtime_consumer"


@pytest.mark.asyncio
async def test_proxy_plain_user_gets_runtime_consumer(fake_httpx, mock_db_session):
    """普通用户 → X-Roles: runtime_consumer（hub 侧 RBAC 自行拦截）。"""
    app.dependency_overrides[get_current_user] = lambda: _plain_user()
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    try:
        await c.get("/api/hub/items")
        assert _FakeClient.captured["headers"]["X-Roles"] == "runtime_consumer"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_proxy_forwards_query_and_body(admin_client):
    await admin_client.get("/api/hub/items?type=skill&limit=10")
    assert "/api/hub/items?type=skill&limit=10" in _FakeClient.captured["url"]


@pytest.mark.asyncio
async def test_proxy_passes_through_response(fake_httpx, mock_db_session):
    """hub 响应状态码/内容透传。"""
    _FakeClient._resp = _FakeStreamResp(status_code=201, body=b'{"created":5}')
    app.dependency_overrides[get_current_user] = lambda: _admin_user()
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    try:
        resp = await c.post("/api/hub/presets/init")
        assert resp.status_code == 201
        assert resp.json() == {"created": 5}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_proxy_401_without_auth(fake_httpx, mock_db_session):
    """无 JWT（get_current_user 抛 401）→ 401，不转发到 hub。"""
    from fastapi import HTTPException

    async def _raise():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_current_user] = _raise
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    try:
        resp = await c.get("/api/hub/items")
        assert resp.status_code == 401
        assert _FakeClient.captured == {}  # 未转发
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_proxy_503_on_connect_error(monkeypatch, mock_db_session):
    """hub 不可达 → 503。"""
    from httpx import ConnectError

    class _BoomClient:
        def __init__(self, *a, **kw):
            self.aclose = AsyncMock()

        def build_request(self, *a, **kw):
            return MagicMock()

        async def send(self, *a, **kw):
            raise ConnectError("refused")

    monkeypatch.setattr(hub_proxy.httpx, "AsyncClient", _BoomClient)
    app.dependency_overrides[get_current_user] = lambda: _admin_user()
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    try:
        resp = await c.get("/api/hub/items")
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()
