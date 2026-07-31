"""Channel webhook router — IM platform callback endpoints.

Routes:
  POST /{channel_type}/{agent_id}/callback  → Receive IM messages
  GET  /{channel_type}/{agent_id}/callback  → URL verification (generic)
  POST /{channel_type}/{agent_id}/send      → Hermes 主动发消息（出站）
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.models import get_channel_config
from app.settings import settings
from .registry import get_adapter
from .dispatcher import dispatcher

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/{channel_type}/{agent_id}/callback")
async def channel_webhook(channel_type: str, agent_id: str, request: Request):
    """IM platform message callback entry point.

    1. Load channel config from DB
    2. Verify webhook signature
    3. Parse payload into MessageEvent(s)
    4. Queue for async processing → return 200 immediately
    """
    config = await get_channel_config(agent_id, channel_type)
    if not config:
        logger.warning("Channel %s not configured for agent %s", channel_type, agent_id[:8])
        return JSONResponse({"error": "channel not configured"}, status_code=404)

    adapter = get_adapter(channel_type, config)
    if not adapter:
        return JSONResponse({"error": f"unsupported channel: {channel_type}"}, status_code=400)

    # Verify signature
    if not await adapter.verify_signature(request):
        logger.warning("Signature verification failed for %s/%s", channel_type, agent_id[:8])
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    # Handle platform verification (e.g. Feishu Challenge)
    verify_resp = await adapter.handle_verification(request)
    if verify_resp is not None:
        return verify_resp

    # Parse incoming message(s)
    events = await adapter.parse_incoming(request)
    if not events:
        return JSONResponse({"error": "empty message"}, status_code=400)

    # Fill metadata (before handle_callback — adapters that dispatch inline
    # need agent_id/channel_type already set on the events).
    for event in events:
        event.agent_id = agent_id
        event.channel_type = channel_type

    # Allow adapters to handle the callback synchronously and return a Response
    # in the HTTP body (e.g. WeCom smart-robot passive reply / streaming refresh).
    # Returns None for the default async model.
    sync_resp = await adapter.handle_callback(request, events, dispatcher.dispatch)
    if sync_resp is not None:
        return sync_resp

    # Queue for async processing
    for event in events:
        await dispatcher.dispatch(event)

    # Return 200 immediately — processing happens in background
    return JSONResponse({"status": "accepted"})


@router.get("/{channel_type}/{agent_id}/callback")
async def channel_verify(channel_type: str, agent_id: str, request: Request):
    """URL verification endpoint — used by WeCom/DingTalk for callback URL validation.

    Delegates to the adapter's verify_url() method for platform-specific logic.
    """
    config = await get_channel_config(agent_id, channel_type)
    if not config:
        return JSONResponse({"error": "channel not configured"}, status_code=404)

    adapter = get_adapter(channel_type, config)
    if not adapter:
        return JSONResponse({"error": f"unsupported channel: {channel_type}"}, status_code=400)

    return await adapter.verify_url(request)


@router.post("/{channel_type}/{agent_id}/menu")
async def channel_create_menu(channel_type: str, agent_id: str, request: Request):
    """创建自建应用底部菜单（目前仅 wecom_callback 支持）。

    body 为企微 menu/create 的菜单 JSON。内部管理接口（内网调用）。
    """
    config = await get_channel_config(agent_id, channel_type)
    if not config:
        return JSONResponse({"error": "channel not configured"}, status_code=404)
    adapter = get_adapter(channel_type, config)
    if not adapter or not hasattr(adapter, "create_menu"):
        return JSONResponse(
            {"error": f"channel {channel_type} does not support menu"}, status_code=400
        )
    menu = await request.json()
    result = await adapter.create_menu(menu)
    return JSONResponse(result)


@router.post("/{channel_type}/{agent_id}/send")
async def channel_send(
    channel_type: str,
    agent_id: str,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Hermes 主动发消息端点（出站）。

    Hermes Cron/prompt 通过 send.py 调用此端点，gateway 调企微 message/send 下发。
    鉴权：``Bearer {api_server_key}``。详见 wecom(callback)流程设计.md 2.5。

    Body:
      touser: 企微 user_id（单聊）
      msgtype: markdown / text / template_card
      content: markdown/text 内容，或 template_card 的 JSON 字符串
      chat_id: 群聊发送暂未支持（传非空 chat_id 返回 501），当前仅支持单聊 touser
    """
    if not authorization or authorization != f"Bearer {settings.api_server_key}":
        raise HTTPException(status_code=401, detail="invalid api key")

    config = await get_channel_config(agent_id, channel_type)
    if not config:
        logger.warning("Send: channel %s not configured for agent %s", channel_type, agent_id[:8])
        return JSONResponse({"error": "channel not configured"}, status_code=404)
    adapter = get_adapter(channel_type, config)
    if not adapter:
        return JSONResponse({"error": f"unsupported channel: {channel_type}"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json body"}, status_code=400)

    touser = body.get("touser", "") or ""
    group_chat_id = body.get("chat_id", "") or ""
    msgtype = (body.get("msgtype") or "markdown").lower()
    content = body.get("content", "")
    # 群聊发送（chat_id 非空）暂未实现：适配器只支持单聊 message/send，把群 ID 当
    # touser 会被企微拒收且静默丢消息。显式 501 让调用方感知，而非静默失败。
    if group_chat_id:
        return JSONResponse(
            {"error": "暂不支持群聊发送，仅支持单聊 touser"}, status_code=501
        )
    chat_id = group_chat_id or touser
    if not chat_id:
        return JSONResponse({"error": "missing touser or chat_id"}, status_code=400)
    if not content:
        return JSONResponse({"error": "missing content"}, status_code=400)

    if msgtype in ("text", "markdown"):
        # send_message 内部已处理卡片 JSON 提取 + markdown 分段
        ok = await adapter.send_message(chat_id, content)
        return JSONResponse({"ok": ok})
    if msgtype == "template_card":
        if not hasattr(adapter, "send_card_message"):
            return JSONResponse(
                {"error": f"channel {channel_type} does not support card"}, status_code=400
            )
        try:
            card_body = json.loads(content) if isinstance(content, str) else content
        except json.JSONDecodeError:
            return JSONResponse({"error": "content is not valid card JSON"}, status_code=400)
        result = await adapter.send_card_message(chat_id, "template_card", card_body)
        errcode = result.get("errcode", -1) if isinstance(result, dict) else -1
        return JSONResponse({"ok": errcode == 0, "raw": result})
    return JSONResponse({"error": f"unsupported msgtype: {msgtype}"}, status_code=400)
