"""WeCom AI Bot (wecom) WS 透明桥接测试"""
import asyncio
import pytest
from unittest.mock import patch, MagicMock


class FakeProfileWS:
    """模拟 Starlette WebSocket（receive/send_text/send_bytes/close）"""
    def __init__(self, incoming):
        self._in = list(incoming) + [{"type": "websocket.disconnect", "code": 1000}]
        self.sent = []

    async def receive(self):
        return self._in.pop(0)

    async def send_text(self, t):
        self.sent.append(("text", t))

    async def send_bytes(self, b):
        self.sent.append(("bytes", b))

    async def close(self):
        pass


class FakeExternal:
    """模拟企微 openws（async iterator + send）"""
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        pass


class FakeCtx:
    def __init__(self, ext):
        self.ext = ext

    async def __aenter__(self):
        return self.ext

    async def __aexit__(self, *a):
        pass


@pytest.mark.asyncio
async def test_bot_bridge_bidirectional_passthrough():
    """profile→openws 与 openws→profile 双向透传（text + bytes）"""
    from app.channel.wecom_bot import bridge_bot_ws

    profile = FakeProfileWS([{"type": "websocket.receive", "text": "subscribe-bot1"}])
    ext = FakeExternal(["hello-from-wecom", b"binary-frame"])

    with patch("app.channel.wecom_bot.websockets.connect", return_value=FakeCtx(ext)):
        await bridge_bot_ws(profile, "wss://fake", "bot1")

    # profile → openws
    assert "subscribe-bot1" in ext.sent
    # openws → profile
    assert ("text", "hello-from-wecom") in profile.sent
    assert ("bytes", b"binary-frame") in profile.sent
