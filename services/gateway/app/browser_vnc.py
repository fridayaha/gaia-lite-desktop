"""浏览器沙箱 VNC WS 桥接：终端用户 noVNC ↔ kasm browser Pod。

gateway 在终端用户（经 JWT 鉴权 + profile_resolver 访问校验）与 browser Pod 的 KasmVNC
之间做 1:1 二进制 WS 透传。上游要点（P0 云 amd64 实测）：
- wss:// 自签（kasmvnc.yaml ssl self.pem）→ ssl verify=False
- Basic auth（kasm_user:VNC_PW）—— VNC_PW 由 manager 经 DB internal_port_map 传入
- Origin 头必带（KasmVNC CSRF 校验，无 Origin → 404 伪装拒绝）
- WS 路径 /websockify，subprotocol "binary"（RFB over binary WS，kasm 回显）

gateway 不解包 RFB、不参与接管状态机（接管互斥在前端 runActive/browserTakeoverActive 维护）。
"""
import asyncio
import base64
import logging
import ssl

import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.settings import settings

logger = logging.getLogger(__name__)


async def bridge_vnc_ws(client_ws: WebSocket, browser_pod: str, vnc_pw: str) -> None:
    """1:1 WS 桥：client_ws（终端用户 noVNC）↔ kasm browser Pod:6901/websockify。

    任一侧断开则关另一侧。上游连接失败（browser Pod 未就绪 / VNC 未起）→ 关闭 client_ws。
    gateway 不解包 RFB（NoAuth 后 noVNC 二进制透传）；用 KasmVNC 官方 noVNC fork
    （vendored @/lib/kasm-novnc）与 KasmVNC 服务端的 RFB 扩展兼容，避免 stock noVNC 的
    "invalid pixel format" 流错位（见 docs/features/browser-sandbox-vnc-debug-status.md）。
    """
    host = f"{browser_pod}.{settings.k8s_namespace}.svc.cluster.local"
    upstream = f"wss://{host}:{settings.browser_vnc_port}/websockify"
    auth = base64.b64encode(f"kasm_user:{vnc_pw}".encode()).decode()
    # KasmVNC 自签证书：不校验（browser Pod 内 self.pem）
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    # Origin 必带（KasmVNC CSRF）；Basic auth 注入 VNC_PW（client 侧 noVNC 不持密码）
    headers = {
        "Authorization": f"Basic {auth}",
        "Origin": f"https://{host}:{settings.browser_vnc_port}",
    }

    try:
        async with websockets.connect(
            upstream,
            subprotocols=["binary"],
            ssl=ssl_ctx,
            additional_headers=headers,
            # 不发 WS ping：KasmVNC 的 websockify 不回 pong，gateway 默认 20s ping + 20s
            # timeout 会在 40s 误关连接（实测 VNC 接管后 ~40s 断）。RFB 流量（framebuffer
            # 更新 + noVNC 的 FramebufferUpdateRequest 轮询）自保活；连接生命周期由 noVNC
            # 客户端控制。
            ping_interval=None,
            ping_timeout=None,
        ) as upstream_ws:
            logger.info("VNC bridge established: browser_pod=%s", browser_pod)

            async def client_to_upstream():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        data = msg.get("bytes")
                        if data is not None:
                            await upstream_ws.send(data)
                        elif msg.get("text") is not None:
                            await upstream_ws.send(msg["text"])
                except WebSocketDisconnect:
                    pass
                except (asyncio.CancelledError, ConnectionError):
                    pass
                except Exception as e:
                    logger.debug("client→upstream pipe end: %s", e)

            async def upstream_to_client():
                try:
                    async for msg in upstream_ws:
                        if isinstance(msg, (bytes, bytearray)):
                            await client_ws.send_bytes(bytes(msg))
                        else:
                            await client_ws.send_text(msg)
                except (asyncio.CancelledError, ConnectionError):
                    pass
                except Exception as e:
                    logger.debug("upstream→client pipe end: %s", e)

            # 任一侧结束即取消另一侧：gather 默认不取消兄弟任务，若一侧断开时另一侧
            # 卡在 receive/async for 上（ping_interval=None 无心跳探活），会一直挂着——
            # 上游 WS 不关、路由协程不返回（连接泄漏）。FIRST_COMPLETED + cancel pending
            # 保证一侧断开立刻关另一侧，async with 退出 → 上游 WS 关闭。
            task_c = asyncio.create_task(client_to_upstream())
            task_u = asyncio.create_task(upstream_to_client())
            try:
                done, pending = await asyncio.wait(
                    {task_c, task_u}, return_when=asyncio.FIRST_COMPLETED
                )
            except asyncio.CancelledError:
                task_c.cancel()
                task_u.cancel()
                raise
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
    except Exception as e:
        logger.error("VNC bridge failed (browser_pod=%s): %s", browser_pod, e)
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass
        logger.info("VNC bridge closed: browser_pod=%s", browser_pod)
