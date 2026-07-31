"""浏览器沙箱 VNC WS 桥接测试。

两类：
1. bridge_vnc_ws —— 1:1 二进制透传 + 上游连接参数（wss/Basic auth/Origin/binary 子协议）
2. browser_vnc_ws 路由 —— JWT 鉴权 + profile_resolver 解析 + close code（4401/4403/4404）
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class FakeClientWS:
    """模拟 Starlette WebSocket（receive/send_bytes/send_text/close/accept）"""

    def __init__(self, incoming=None):
        self._in = list(incoming or []) + [{"type": "websocket.disconnect", "code": 1000}]
        self.sent = []
        self.accepted = False
        self.closed_with = None

    async def accept(self):
        self.accepted = True

    async def receive(self):
        return self._in.pop(0)

    async def send_text(self, t):
        self.sent.append(("text", t))

    async def send_bytes(self, b):
        self.sent.append(("bytes", b))

    async def close(self, code=None):
        self.closed_with = code


class FakeUpstream:
    """模拟 kasm VNC 上游 WS（async iterator + send）"""

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
    def __init__(self, upstream):
        self.upstream = upstream

    async def __aenter__(self):
        return self.upstream

    async def __aexit__(self, *a):
        pass


# ── bridge_vnc_ws ──


@pytest.mark.asyncio
async def test_vnc_bridge_bidirectional_bytes_passthrough():
    """client→upstream 与 upstream→client 双向透传（bytes + text）"""
    from app.browser_vnc import bridge_vnc_ws

    client = FakeClientWS([{"type": "websocket.receive", "bytes": b"mouse-event"}])
    upstream = FakeUpstream([b"framebuffer-update", "text-frame"])

    with patch("app.browser_vnc.websockets.connect", return_value=FakeCtx(upstream)) as mk_conn:
        await bridge_vnc_ws(client, "browser-pod-abc", "vnc-pw")

    # client → upstream
    assert b"mouse-event" in upstream.sent
    # upstream → client
    assert ("bytes", b"framebuffer-update") in client.sent
    assert ("text", "text-frame") in client.sent


@pytest.mark.asyncio
async def test_vnc_bridge_connect_args_basic_auth_origin_subprotocol():
    """上游连接参数：wss + subprotocols=[binary] + Basic auth + Origin 头"""
    from app.browser_vnc import bridge_vnc_ws
    import base64

    client = FakeClientWS()
    upstream = FakeUpstream([])

    with patch("app.browser_vnc.websockets.connect", return_value=FakeCtx(upstream)) as mk_conn:
        await bridge_vnc_ws(client, "browser-pod-abc", "secret-pw")

    assert mk_conn.called
    args, kwargs = mk_conn.call_args
    upstream_url = args[0] if args else kwargs.get("uri")
    assert upstream_url.startswith("wss://")
    assert "browser-pod-abc" in upstream_url
    assert "/websockify" in upstream_url
    # subprotocol binary
    assert kwargs.get("subprotocols") == ["binary"]
    # ssl 上下文（自签不校验）
    assert kwargs.get("ssl") is not None
    # Basic auth + Origin
    headers = kwargs.get("additional_headers") or {}
    expected_auth = base64.b64encode(b"kasm_user:secret-pw").decode()
    assert headers.get("Authorization") == f"Basic {expected_auth}"
    assert "Origin" in headers


@pytest.mark.asyncio
async def test_vnc_bridge_upstream_failure_closes_client():
    """上游连接失败 → 关闭 client_ws，不抛"""
    from app.browser_vnc import bridge_vnc_ws

    client = FakeClientWS()
    with patch("app.browser_vnc.websockets.connect", side_effect=ConnectionRefusedError("no pod")):
        await bridge_vnc_ws(client, "browser-pod-abc", "pw")  # 不抛
    # client 在 finally 被 close
    assert client.closed_with is None or client.accepted is False


class BlockingUpstream:
    """永不 yield 的上游 WS：__anext__ 挂在永不 set 的事件上（模拟 kasm 静默无帧）。

    旧 gather 实现下 client 断开时 upstream_to_client 卡在此 → bridge 永不返回（挂死）。
    新 FIRST_COMPLETED + cancel 实现下，client 任务结束即取消此任务 → bridge 正常返回。
    """
    def __init__(self):
        self.sent = []
        self._block = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._block.wait()  # 永不 set
        raise StopAsyncIteration

    async def send(self, msg):
        self.sent.append(msg)

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_vnc_bridge_client_disconnect_cancels_idle_upstream():
    """client 断开时，卡在 async for 的 upstream 任务被取消 → bridge 不挂死、关闭 client_ws。

    回归：旧 asyncio.gather 不取消兄弟任务，upstream_to_client 卡在 BlockingUpstream →
    bridge 永不返回（上游 WS + 路由协程泄漏）。
    """
    from app.browser_vnc import bridge_vnc_ws

    # client 第一条即 disconnect
    client = FakeClientWS([{"type": "websocket.disconnect", "code": 1000}])
    upstream = BlockingUpstream()

    with patch("app.browser_vnc.websockets.connect", return_value=FakeCtx(upstream)):
        # 若未取消兄弟任务，此 await 会挂死直到 pytest 超时
        await asyncio.wait_for(bridge_vnc_ws(client, "browser-pod-abc", "pw"), timeout=3.0)

    # client 在 finally 被 close（即使 upstream 永远没收到帧）
    assert client.closed_with is not None or client.accepted is False


# ── browser_vnc_ws 路由（鉴权 + 解析 + close code）──


def _make_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_route_invalid_token_closes_4401():
    from app.main import browser_vnc_ws
    ws = _make_ws()
    with patch("app.main.verify_token", side_effect=Exception("bad token")):
        await browser_vnc_ws(ws, "agent-1", token="bad")
    ws.close.assert_awaited_once()
    assert ws.close.call_args.kwargs.get("code") == 4401 or ws.close.call_args.args[0] == 4401
    ws.accept.assert_not_called()


@pytest.mark.asyncio
async def test_route_access_denied_closes_4403():
    from app.main import browser_vnc_ws
    from app.profile_resolver import AccessDenied
    ws = _make_ws()
    payload = {"sub": "u-1", "roles": []}
    with patch("app.main.verify_token", return_value=payload), \
         patch("app.profile_resolver.profile_resolver.resolve_browser_target",
               new=AsyncMock(side_effect=AccessDenied("no"))):
        await browser_vnc_ws(ws, "agent-1", token="tok")
    ws.close.assert_awaited_once()
    code = ws.close.call_args.kwargs.get("code") or ws.close.call_args.args[0]
    assert code == 4403
    ws.accept.assert_not_called()


@pytest.mark.asyncio
async def test_route_no_browser_pod_closes_4404():
    from app.main import browser_vnc_ws
    from app.profile_resolver import ProfileNotFound
    ws = _make_ws()
    payload = {"sub": "u-1", "roles": []}
    with patch("app.main.verify_token", return_value=payload), \
         patch("app.profile_resolver.profile_resolver.resolve_browser_target",
               new=AsyncMock(side_effect=ProfileNotFound("no pod"))):
        await browser_vnc_ws(ws, "agent-1", token="tok")
    ws.close.assert_awaited_once()
    code = ws.close.call_args.kwargs.get("code") or ws.close.call_args.args[0]
    assert code == 4404
    ws.accept.assert_not_called()


@pytest.mark.asyncio
async def test_route_success_accepts_and_bridges():
    from app.main import browser_vnc_ws
    ws = _make_ws()
    payload = {"sub": "u-1", "roles": ["平台管理员"]}
    with patch("app.main.verify_token", return_value=payload), \
         patch("app.profile_resolver.profile_resolver.resolve_browser_target",
               new=AsyncMock(return_value=("pn", "browser-pod", "pw"))) as mk_resolve, \
         patch("app.browser_vnc.bridge_vnc_ws", new=AsyncMock()) as mk_bridge:
        await browser_vnc_ws(ws, "agent-1", token="tok")
    ws.accept.assert_awaited_once()
    # is_admin 从 roles 派生（平台管理员）→ 位置参数传入
    assert mk_resolve.call_args.args == ("u-1", "agent-1", True)
    mk_bridge.assert_awaited_once_with(ws, "browser-pod", "pw")
