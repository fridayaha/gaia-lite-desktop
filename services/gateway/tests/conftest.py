"""Gateway 测试共享 fixtures"""

import sys
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport


# ── Mock JWT to skip auth ──
_mock_jose = MagicMock()
_mock_jose.JWTError = Exception
sys.modules["jose"] = _mock_jose
sys.modules["jose.jwt"] = MagicMock()

from app.main import app, security


@pytest.fixture(autouse=True)
def _disable_time_injection_by_default(monkeypatch):
    """默认关闭当前时间注入，保持既有「原样透传」测试语义。

    需要测注入的用例在体内 ``monkeypatch.setattr(settings, "inject_current_time", True)``
    显式开启。autouse 覆盖所有用例（含直接调 _forward_message 不走 client fixture 的）。
    """
    from app.settings import settings

    monkeypatch.setattr(settings, "inject_current_time", False)


@pytest.fixture
def client():
    """FastAPI 测试客户端（跳过 JWT 验证）"""
    app.dependency_overrides.clear()

    async def _skip_auth():
        from fastapi.security import HTTPAuthorizationCredentials
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token")
    app.dependency_overrides[security] = _skip_auth

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def mock_httpx():
    """Mock httpx.AsyncClient 防止真实网络请求"""
    with patch("app.main.httpx.AsyncClient") as mock:
        mock_client = AsyncMock()
        mock.return_value.__aenter__.return_value = mock_client
        yield mock_client
