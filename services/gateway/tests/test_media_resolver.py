"""Unit tests for app.media_resolver (image resolution + path normalization)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.media_resolver import (
    find_local_file_links,
    is_safe_media_path,
    looks_like_image,
    normalize_path,
    resolve_file_bytes,
    resolve_image_to_data_url,
)


def test_find_local_file_links_excludes_images_and_remote():
    """[name](local) 匹配；![](img) 不匹配；http 链接不匹配。"""
    text = (
        "![图](output/chart.png) 报告 [r.pptx](output/r.pptx) "
        "官网 [docs](https://e.com/x)"
    )
    links = find_local_file_links(text)
    paths = [m.group(2).strip() for m in links]
    names = [m.group(1) for m in links]
    assert paths == ["output/r.pptx"]  # 图片 ![](chart.png) 和 https 链接都排除
    assert names == ["r.pptx"]


def test_find_local_file_links_span_correct():
    """match span 覆盖完整 [name](path)，用于切分。"""
    text = "前[x.pptx](output/x.pptx)后"
    links = find_local_file_links(text)
    assert len(links) == 1
    m = links[0]
    assert text[m.start():m.end()] == "[x.pptx](output/x.pptx)"
    assert text[:m.start()] == "前"
    assert text[m.end():] == "后"


class _FakeAsyncClient:
    """真实 async context manager：`async with AsyncClient() as c` → yields inner client."""

    def __init__(self, inner):
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *exc):
        return None


def _patch_httpx_client(mock_client):
    """Patch httpx.AsyncClient to a factory returning a _FakeAsyncClient(mock_client).

    Returns (patcher, ctor_mock). ctor_mock.call_count == number of AsyncClient() calls.
    """
    ctor_mock = MagicMock(side_effect=lambda *a, **k: _FakeAsyncClient(mock_client))
    mc = patch("app.media_resolver.httpx.AsyncClient", new=ctor_mock)
    mc.start()
    return mc, ctor_mock


@pytest.mark.asyncio(loop_scope="module")
class TestResolveImageUrl:
    async def test_svg_resolves_when_content_b64_present(self):
        """SVG 被判为 is_text 但 manager 返回 content_b64（图片保留 b64 修复后）→ 能解析。"""
        svg_b64 = "PHN2Zy8+"  # base64 of "<svg/>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "is_image": True,
            "is_text": True,
            "content_b64": svg_b64,
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mc, _mock = _patch_httpx_client(mock_client)
        try:
            url = await resolve_image_to_data_url("agent-1", "output/diagram.svg")
        finally:
            mc.stop()
        assert url is not None
        assert url.startswith("data:image/svg+xml;base64,")

    async def test_returns_none_when_not_image(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"is_image": False, "content_b64": "abc"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mc, _mock = _patch_httpx_client(mock_client)
        try:
            url = await resolve_image_to_data_url("agent-1", "output/note.txt")
        finally:
            mc.stop()
        assert url is None

    async def test_sends_internal_token_header(self):
        """不再转发 client token，改送 X-Internal-Token。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"is_image": False}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("app.media_resolver.settings") as s:
            s.internal_token = "secret-xyz"
            s.controller_url = "http://manager:8002"
            mc, _mock = _patch_httpx_client(mock_client)
            try:
                await resolve_image_to_data_url("agent-1", "x.png")
            finally:
                mc.stop()
        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"].get("X-Internal-Token") == "secret-xyz"
        assert "Authorization" not in kwargs["headers"]

    async def test_jwt_client_token_forwarded_as_bearer(self):
        """JWT 客户端：转发 Authorization: Bearer <jwt>（manager 走用户隔离），不用内部令牌。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"is_image": False}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("app.media_resolver.settings") as s:
            s.internal_token = "secret-xyz"
            s.controller_url = "http://manager:8002"
            mc, _mock = _patch_httpx_client(mock_client)
            try:
                # 非 sk- 开头 → 视为 JWT 转发
                await resolve_image_to_data_url("agent-1", "x.png", client_token="eyJ.jwt.token")
            finally:
                mc.stop()
        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"].get("Authorization") == "Bearer eyJ.jwt.token"
        assert "X-Internal-Token" not in kwargs["headers"]

    async def test_sk_key_client_uses_internal_token(self):
        """sk- 客户端：用内部令牌（manager 走 instance 隔离），不转发 sk-（会被当 JWT 401）。"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"is_image": False}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("app.media_resolver.settings") as s:
            s.internal_token = "secret-xyz"
            s.controller_url = "http://manager:8002"
            mc, _mock = _patch_httpx_client(mock_client)
            try:
                await resolve_image_to_data_url("agent-1", "x.png", client_token="sk-abc123")
            finally:
                mc.stop()
        _, kwargs = mock_client.get.call_args
        assert kwargs["headers"].get("X-Internal-Token") == "secret-xyz"
        assert "Authorization" not in kwargs["headers"]


# ── normalize_path：绝对 profile 路径归一（模型以 /opt/data/profiles/<p>/... 引用）──


def test_normalize_absolute_profile_path_stripped():
    """模型以绝对 profile 路径引用工作区内文件 → 剥前缀归一为相对路径。"""
    assert normalize_path(
        "/opt/data/profiles/d38e436e-cfd2a9-c246cea4/home/bill_products.png"
    ) == "home/bill_products.png"


def test_normalize_legacy_profiles_prefix_still_works():
    """旧 /profiles/<profile>/ 格式仍归一。"""
    assert normalize_path("/profiles/p1/output/chart.png") == "output/chart.png"


def test_normalize_workspace_relative_unchanged():
    assert normalize_path("./output/chart.png") == "output/chart.png"
    assert normalize_path("output/chart.png") == "output/chart.png"


# ── looks_like_image：magic bytes 校验 ────────────────────────────────────────


def test_looks_like_image_detects_known_formats():
    assert looks_like_image(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32) is True  # PNG
    assert looks_like_image(b"\xff\xd8\xff\xe0" + b"\x00" * 32) is True   # JPEG
    assert looks_like_image(b"GIF89a" + b"\x00" * 32) is True             # GIF
    assert looks_like_image(b"BM" + b"\x00" * 32) is True                 # BMP
    assert looks_like_image(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 4) is True  # WEBP


def test_looks_like_image_rejects_non_image():
    """HTML 错误页 / 短字节 / 纯文本不应被判为图片。"""
    assert looks_like_image(b"") is False
    assert looks_like_image(b"\x00\x01") is False  # 太短
    assert looks_like_image(b"<html><body>error</body></html>") is False
    assert looks_like_image(b"{" + b" " * 32) is False  # JSON 错误响应


# ── is_safe_media_path：defense-in-depth 前置过滤 ──────────────────────────────


def test_is_safe_media_path_accepts_normal():
    assert is_safe_media_path("output/chart.png") is True
    assert is_safe_media_path("uploads/abc.jpg") is True
    assert is_safe_media_path("home/x.png") is True


def test_is_safe_media_path_rejects_traversal():
    assert is_safe_media_path("../etc/passwd") is False
    assert is_safe_media_path("output/../../secret") is False
    assert is_safe_media_path("a/../b") is False


def test_is_safe_media_path_rejects_credential_paths():
    assert is_safe_media_path(".ssh/id_rsa") is False
    assert is_safe_media_path("uploads/.aws/credentials") is False
    assert is_safe_media_path(".env") is False
    assert is_safe_media_path(".gitconfig") is False


def test_is_safe_media_path_rejects_empty():
    assert is_safe_media_path("") is False
    assert is_safe_media_path("   ") is False


@pytest.mark.asyncio(loop_scope="module")
class TestUnsafePathBlocked:
    """.. / 凭据路径在到达 manager 前被拦——httpx 不应被调用。"""

    async def test_resolve_image_blocks_traversal(self):
        mock_client = AsyncMock()
        mc, _ctor = _patch_httpx_client(mock_client)
        try:
            url = await resolve_image_to_data_url("agent-1", "../../etc/passwd")
        finally:
            mc.stop()
        assert url is None
        # is_safe_media_path 在 _resolve_one 内拦截，不发起 manager GET
        assert mock_client.get.call_count == 0

    async def test_resolve_file_blocks_credential_path(self):
        mock_client = AsyncMock()
        mc, ctor = _patch_httpx_client(mock_client)
        try:
            result = await resolve_file_bytes("agent-1", ".ssh/id_rsa")
        finally:
            mc.stop()
        assert result is None
        assert mock_client.get.call_count == 0
        assert ctor.call_count == 0
