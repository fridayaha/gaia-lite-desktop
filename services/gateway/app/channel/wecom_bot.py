"""企业微信 AI Bot（wecom_bot）— WS 透明桥接 channel_type。

与 `wecom_callback`（HTTP 回调）不同，AI Bot 模式是 WS 透传桥接：
- Profile 连 gateway WS（`/api/gateway/channel/wecom/{agent_id}/ws`）
- gateway 连企微 openws（`wss://openws.work.weixin.qq.com`）
- 双向 1:1 透传，不解包不拦截

gateway 不参与鉴权 / 不处理消息 / 不维护 session：
- 企微 AI Bot 平台自带 ASR（voice.content 自带转写）
- Profile 自带会话（WS 长连接内维持）
- 鉴权由 Profile 发的 aibot_subscribe（含 bot_id+secret）完成，gateway 透传

不走 dispatcher（透传，无消息处理）。本模块只提供桥接函数，由 main.py 的
WS 端点调用。不注册为 BaseChannelAdapter（非 webhook 模型）。
"""
import asyncio
import logging

import websockets
from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from app.settings import settings

logger = logging.getLogger(__name__)


async def bridge_bot_ws(profile_ws: WebSocket, openws_url: str = "", bot_id: str = ""):
    """1:1 WS 透传：profile_ws（内部 Profile）↔ 企微 openws。

    两条 pipe 并发：profile→企微、企微→profile。任一断开则关闭另一侧。
    """
    openws_url = openws_url or settings.wecom_openws_url
    closed = False

    try:
        async with websockets.connect(openws_url) as external:
            logger.info("Bot bridge established: bot_id=%s", bot_id or "?")

            async def profile_to_external():
                try:
                    while True:
                        msg = await profile_ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        text = msg.get("text")
                        data = msg.get("bytes")
                        if text is not None:
                            await external.send(text)
                        elif data is not None:
                            await external.send(data)
                except WebSocketDisconnect:
                    pass
                except (asyncio.CancelledError, ConnectionError):
                    pass
                except Exception as e:
                    logger.debug("profile→openws pipe end: %s", e)
                finally:
                    closed = True

            async def external_to_profile():
                try:
                    async for msg in external:
                        if isinstance(msg, str):
                            await profile_ws.send_text(msg)
                        elif isinstance(msg, (bytes, bytearray)):
                            await profile_ws.send_bytes(msg)
                except (asyncio.CancelledError, ConnectionError):
                    pass
                except Exception as e:
                    logger.debug("openws→profile pipe end: %s", e)
                finally:
                    closed = True

            await asyncio.gather(profile_to_external(), external_to_profile())
    except Exception as e:
        logger.error("Bot bridge failed (bot_id=%s): %s", bot_id or "?", e)
    finally:
        try:
            await profile_ws.close()
        except Exception:
            pass
        logger.info("Bot bridge closed: bot_id=%s", bot_id or "?")
