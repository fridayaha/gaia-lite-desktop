"""POST /v1/audio/transcriptions 单元测试。

覆盖：鉴权、空音频 400、超限 413、provider 未配置 503、ASR 失败 502、
成功透传文本、format 白名单兜底、per-user 限流 429。
"""
import pytest

from app.asr import reset_asr_provider
from app.asr.api import _reset_rate_limit
from app.main import app


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每个测试：假 provider + 真 user payload + 清限流窗口。"""
    reset_asr_provider()
    _reset_rate_limit()
    monkeypatch.setattr(
        "app.asr.api.verify_token",
        lambda cred: {"sub": "user-1"},
    )
    yield
    reset_asr_provider()
    _reset_rate_limit()


class _FakeProvider:
    name = "fake"

    def __init__(self, text="识别结果", error=None):
        self.text = text
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        self.calls.append((audio, fmt))
        if self.error is not None:
            raise self.error
        return self.text


def _patch_provider(monkeypatch, provider):
    monkeypatch.setattr("app.asr.api.get_asr_provider", lambda: provider)


@pytest.mark.asyncio
async def test_success_returns_text(client, monkeypatch):
    fake = _FakeProvider(text="你好世界")
    _patch_provider(monkeypatch, fake)
    resp = await client.post(
        "/v1/audio/transcriptions?format=m4a",
        content=b"\x00\x01fake-audio",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"text": "你好世界"}
    # fmt 透传给 provider
    assert fake.calls == [(b"\x00\x01fake-audio", "m4a")]


@pytest.mark.asyncio
async def test_empty_audio_400(client, monkeypatch):
    _patch_provider(monkeypatch, _FakeProvider())
    resp = await client.post("/v1/audio/transcriptions", content=b"")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_oversized_audio_413(client, monkeypatch):
    from app.asr.api import MAX_AUDIO_BYTES
    _patch_provider(monkeypatch, _FakeProvider())
    resp = await client.post(
        "/v1/audio/transcriptions",
        content=b"x" * (MAX_AUDIO_BYTES + 1),
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_provider_unconfigured_503(client, monkeypatch):
    _patch_provider(monkeypatch, None)
    resp = await client.post("/v1/audio/transcriptions", content=b"audio")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_asr_error_502(client, monkeypatch):
    from app.asr import AsrError
    _patch_provider(monkeypatch, _FakeProvider(error=AsrError("upstream down")))
    resp = await client.post("/v1/audio/transcriptions", content=b"audio")
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_unknown_format_falls_back(client, monkeypatch):
    fake = _FakeProvider()
    _patch_provider(monkeypatch, fake)
    resp = await client.post(
        "/v1/audio/transcriptions?format=../../etc/passwd",
        content=b"audio",
    )
    assert resp.status_code == 200
    # 非白名单 format 兜底 m4a，不作文件扩展名透传
    assert fake.calls[0][1] == "m4a"


@pytest.mark.asyncio
async def test_rate_limit_429(client, monkeypatch):
    from app.asr.api import _RATE_MAX_CALLS
    _patch_provider(monkeypatch, _FakeProvider())
    for _ in range(_RATE_MAX_CALLS):
        resp = await client.post("/v1/audio/transcriptions", content=b"a")
        assert resp.status_code == 200
    resp = await client.post("/v1/audio/transcriptions", content=b"a")
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_unauthenticated_401(monkeypatch):
    """不带 Authorization 头 → verify_token(None) 抛 401。"""
    from httpx import AsyncClient, ASGITransport
    from app.proxy import verify_token as real_verify_token
    # 恢复真实 verify_token（autouse fixture 里被替换成了永远放行的假实现）
    monkeypatch.setattr("app.asr.api.verify_token", real_verify_token)
    app.dependency_overrides.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        resp = await raw_client.post("/v1/audio/transcriptions", content=b"a")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_prefixed_path_also_served(client, monkeypatch):
    """生产 nginx 不剥 /api/gateway 前缀：带前缀路径必须命中同一 endpoint（不落进 catch-all）。"""
    fake = _FakeProvider(text="前缀路径")
    _patch_provider(monkeypatch, fake)
    resp = await client.post(
        "/api/gateway/v1/audio/transcriptions?format=m4a",
        content=b"\x00\x01fake-audio",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"text": "前缀路径"}
