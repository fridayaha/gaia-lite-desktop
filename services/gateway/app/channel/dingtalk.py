"""钉钉 (DingTalk) channel adapter — HTTP callback mode.

Protocol:
- Challenge: POST with "checkUrl" event → respond {"message": "success"}
- Message receive: POST → JSON body with HMAC-SHA256 signature
- Signature: base64(HMAC-SHA256(app_secret, timestamp + "\n")) in "sign" header
- Send: POST /v1.0/im/messages/send (via OAuth 2.0 access token)
- Edit: DingTalk does NOT support editing messages
"""
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import httpx
from fastapi import Request, Response

from .base import BaseChannelAdapter
from .models import MessageEvent
from .registry import register

logger = logging.getLogger(__name__)

DINGTALK_BASE_URL = "https://oapi.dingtalk.com"
DINGTALK_API_URL = "https://api.dingtalk.com"
ACCESS_TOKEN_TTL = 7200


@register("dingtalk")
class DingTalkAdapter(BaseChannelAdapter):
    channel_type = "dingtalk"
    max_text_length = 5000

    def __init__(self, config: dict):
        super().__init__(config)
        self.app_key = config.get("app_key", "")
        self.app_secret = config.get("app_secret", "")
        self.robot_code = config.get("robot_code", "")
        self._access_token: str | None = None
        self._token_expires: float = 0

    # ── Security ────────────────────────────────────────────────────────

    async def verify_signature(self, request: Request) -> bool:
        """Verify DingTalk webhook signature.

        DingTalk uses HMAC-SHA256 of (timestamp + "\n") with app_secret,
        base64-encoded, in the "sign" header.
        """
        timestamp = request.headers.get("timestamp", "")
        sign = request.headers.get("sign", "")
        if not timestamp or not sign:
            return False

        expected = hmac.new(
            self.app_secret.encode("utf-8"),
            f"{timestamp}\n".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(expected).decode() == sign

    # ── Verification (Challenge) ────────────────────────────────────────

    async def handle_verification(self, request: Request):
        """Handle DingTalk URL verification (checkUrl event)."""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return None

        event_type = body.get("eventType", "")
        if event_type == "check_url":
            # DingTalk URL verification handshake
            return Response(
                content=json.dumps({"message": "success"}),
                media_type="application/json",
            )

        return None

    # ── Parsing ─────────────────────────────────────────────────────────

    async def parse_incoming(self, request: Request) -> list[MessageEvent]:
        """Parse DingTalk webhook payload into MessageEvent(s)."""
        try:
            raw = await request.json()
        except json.JSONDecodeError:
            return []

        # Determine message type from the callback payload
        msgtype = raw.get("msgtype", "")

        # Check if this is a conversation event (user → bot message)
        conversation_type = raw.get("conversationType", "")
        if not msgtype or not conversation_type:
            logger.debug("DingTalk ignoring non-message event: %s", raw.get("eventType", ""))
            return []

        msg_id = raw.get("msgId", "")
        chat_id = raw.get("conversationId", "")
        sender_id = raw.get("senderId", "") or raw.get("senderStaffId", "")
        sender_name = raw.get("senderNick", sender_id)

        text = ""
        if msgtype == "text":
            text_content = raw.get("text", {})
            text = (text_content.get("content") or "").strip()
        elif msgtype in ("picture", "file"):
            resource_key = raw.get(msgtype, {}).get("downloadCode", "")
            text = f"[{msgtype}: {resource_key}]"

        if not text:
            return []

        event = MessageEvent(
            text=text,
            chat_id=chat_id,
            user_id=sender_id,
            user_name=sender_name,
            channel_type="dingtalk",
            platform_message_id=msg_id,
            raw_message=raw,
        )
        return [event]

    # ── Send messages ───────────────────────────────────────────────────

    async def _ensure_token(self) -> str | None:
        """Get or refresh DingTalk access token via OAuth 2.0."""
        now = time.time()
        if self._access_token and self._token_expires > now + 60:
            return self._access_token

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{DINGTALK_API_URL}/v1.0/oauth2/accessToken",
                    json={"appKey": self.app_key, "appSecret": self.app_secret},
                )
                data = resp.json()
                token = data.get("accessToken")
                if not token:
                    logger.error("DingTalk token failed: %s", data)
                    return None
                self._access_token = token
                self._token_expires = now + int(data.get("expireIn", ACCESS_TOKEN_TTL))
                return self._access_token
        except Exception as e:
            logger.error("DingTalk token error: %s", e)
            return None

    async def send_message(self, chat_id: str, text: str, reply_to: str = "") -> bool:
        """Send a text message to a DingTalk user/group via app robot API."""
        token = await self._ensure_token()
        if not token:
            return False

        msg_param = json.dumps({"content": self._truncate(text)})
        payload = {
            "robotCode": self.robot_code,
            "msgKey": "sampleText",
            "msgParam": msg_param,
        }

        # Determine recipient: use conversationId for groups, userId for single chat
        if reply_to:
            # Reply in same conversation (DingTalk doesn't support reply_to natively,
            # but we include the conversation context)
            pass

        # For group chats (conversationId starts with "cid")
        if chat_id.startswith("cid"):
            payload["conversationId"] = chat_id
        else:
            payload["userId"] = chat_id

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{DINGTALK_API_URL}/v1.0/im/messages/send",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-acs-dingtalk-access-token": token,
                    },
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("processQueryKey"):
                    return True
                logger.warning("DingTalk send_message failed: %s", data)
                return False
        except Exception as e:
            logger.error("DingTalk send_message error: %s", e)
            return False

    async def send_markdown(self, chat_id: str, markdown: str) -> bool:
        """Send a markdown message via DingTalk app robot."""
        token = await self._ensure_token()
        if not token:
            return False

        msg_param = json.dumps({"title": "", "text": self._truncate(markdown)})
        payload = {
            "robotCode": self.robot_code,
            "msgKey": "sampleMarkdown",
            "msgParam": msg_param,
        }

        if chat_id.startswith("cid"):
            payload["conversationId"] = chat_id
        else:
            payload["userId"] = chat_id

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{DINGTALK_API_URL}/v1.0/im/messages/send",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "x-acs-dingtalk-access-token": token,
                    },
                )
                return resp.status_code == 200
        except Exception as e:
            logger.error("DingTalk send_markdown error: %s", e)
            return False
