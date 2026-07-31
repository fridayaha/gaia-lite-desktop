"""飞书 (Feishu/Lark) channel adapter — HTTP callback mode.

Protocol:
- Challenge: POST returns {"challenge": "xxx"} for URL verification
- Message receive: POST → JSON body with signature verification
- Reply: POST /open-apis/im/v1/messages/{message_id}/reply
- Edit: PATCH /open-apis/im/v1/messages/{message_id}

References hermes-agent gateway/platforms/feishu.py architecture.
"""
import asyncio
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

FEISHU_BASE_URL = "https://open.feishu.cn"
LARK_BASE_URL = "https://open.larksuite.com"
TENANT_TOKEN_TTL = 7200


@register("feishu")
class FeishuAdapter(BaseChannelAdapter):
    channel_type = "feishu"
    max_text_length = 10000
    supports_streaming = True

    def __init__(self, config: dict):
        super().__init__(config)
        self.app_id = config.get("app_id", "")
        self.app_secret = config.get("app_secret", "")
        self.verification_token = config.get("verification_token", "")
        self.encrypt_key = config.get("encrypt_key", "")
        self._base_url = FEISHU_BASE_URL
        self._tenant_token: str | None = None
        self._token_expires: float = 0

    # ── Security ────────────────────────────────────────────────────────

    async def verify_signature(self, request: Request) -> bool:
        """Verify Feishu webhook signature.

        HMAC-SHA256(timestamp + nonce + body) vs X-Lark-Signature header.
        """
        if not self.encrypt_key:
            return True  # No encryption configured — trust network layer

        timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
        nonce = request.headers.get("X-Lark-Request-Nonce", "")
        signature = request.headers.get("X-Lark-Signature", "")
        body = await request.body()

        expected = hmac.new(
            self.encrypt_key.encode("utf-8"),
            f"{timestamp}{nonce}{body.decode()}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return expected == signature

    # ── Verification (Challenge) ────────────────────────────────────────

    async def handle_verification(self, request: Request):
        """Handle Feishu Challenge verification."""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return None

        if body.get("type") == "url_verification":
            challenge = body.get("challenge", "")
            return Response(
                content=json.dumps({"challenge": challenge}),
                media_type="application/json",
            )

        # Encrypted challenge
        encrypt = body.get("encrypt", "")
        if encrypt:
            try:
                decrypted = self._decrypt_event(encrypt)
                data = json.loads(decrypted)
                if data.get("type") == "url_verification":
                    return Response(
                        content=json.dumps({"challenge": data["challenge"]}),
                        media_type="application/json",
                    )
            except Exception as e:
                logger.warning("Feishu decrypt challenge failed: %s", e)
                return Response(status_code=403)

        return None

    async def verify_url(self, request: Request):
        """Feishu 使用 POST Challenge（handle_verification），不支持 GET 验证"""
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)

    # ── Parsing ─────────────────────────────────────────────────────────

    async def parse_incoming(self, request: Request) -> list[MessageEvent]:
        """Parse Feishu webhook payload into MessageEvent(s)."""

        try:
            raw = await request.json()
        except json.JSONDecodeError:
            return []

        # Decrypt if encrypted
        if raw.get("encrypt"):
            try:
                decrypted = self._decrypt_event(raw["encrypt"])
                raw = json.loads(decrypted)
            except Exception as e:
                logger.warning("Feishu decrypt failed: %s", e)
                return []

        event_type = (
            raw.get("event", {}).get("header", {}).get("event_type", "")
            or raw.get("header", {}).get("event_type", "")
        )

        if event_type != "im.message.receive_v1":
            logger.debug("Feishu ignoring event_type=%s", event_type)
            return []

        event_body = raw.get("event", {})
        message = event_body.get("message", {})
        sender = event_body.get("sender", {})

        chat_id = message.get("chat_id", "")
        sender_id = (sender.get("sender_id") or {}).get("open_id", "")
        sender_name = sender.get("name", sender_id)
        msg_type = message.get("message_type", "")
        content_str = message.get("content", "{}")
        msg_id = message.get("message_id", "")

        # Parse content based on type
        try:
            content = json.loads(content_str) if isinstance(content_str, str) else content_str
        except json.JSONDecodeError:
            content = {}

        text = ""
        if msg_type == "text":
            text = content.get("text", "")
        elif msg_type in ("image", "file"):
            # Image/file messages need to be fetched via API — store the
            # resource key for later retrieval.
            text = f"[{msg_type}: {content.get('image_key', content.get('file_key', ''))}]"

        if not text:
            return []

        event = MessageEvent(
            text=text,
            chat_id=chat_id,
            user_id=sender_id,
            user_name=sender_name,
            channel_type="feishu",
            platform_message_id=msg_id,
            raw_message=raw,
        )
        return [event]

    # ── Send messages ───────────────────────────────────────────────────

    async def _ensure_token(self) -> str | None:
        """Get or refresh Feishu tenant_access_token."""
        now = time.time()
        if self._tenant_token and self._token_expires > now + 60:
            return self._tenant_token

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                )
                data = resp.json()
                if data.get("code") != 0:
                    logger.error("Feishu token failed: %s", data)
                    return None
                self._tenant_token = data["tenant_access_token"]
                self._token_expires = now + int(data.get("expire", TENANT_TOKEN_TTL))
                return self._tenant_token
        except Exception as e:
            logger.error("Feishu token error: %s", e)
            return None

    async def send_message(self, chat_id: str, text: str, reply_to: str = "") -> bool:
        """Send a text message to a Feishu chat."""
        token = await self._ensure_token()
        if not token:
            return False

        truncated_text = self._truncate(text)

        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": truncated_text}),
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                content = json.dumps({"text": truncated_text})
                if reply_to:
                    resp = await client.post(
                        f"{self._base_url}/open-apis/im/v1/messages/{reply_to}/reply",
                        json={"msg_type": "text", "content": content},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                else:
                    resp = await client.post(
                        f"{self._base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning("Feishu send_message failed: %s", data)
                    return False
                return True
        except Exception as e:
            logger.error("Feishu send_message error: %s", e)
            return False

    async def send_markdown(self, chat_id: str, markdown: str) -> bool:
        """Send a markdown message via post content type."""
        token = await self._ensure_token()
        if not token:
            return False

        content = {
            "zh_cn": {
                "title": "",
                "content": [[{"tag": "markdown", "text": self._truncate(markdown)}]],
            }
        }
        payload = {
            "receive_id": chat_id,
            "msg_type": "post",
            "content": json.dumps(content),
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                return resp.json().get("code") == 0
        except Exception as e:
            logger.error("Feishu send_markdown error: %s", e)
            return False

    async def edit_message(self, chat_id: str, message_id: str, content: str) -> bool:
        """Edit a previously sent card — replaces elements with a single markdown.

        用于最终替换（非流式路径），直接用最终内容替换整张卡片。
        """
        return await self._patch_card(message_id, [self._md(content)])

    async def update_streaming_card(self, chat_id: str, message_id: str, accumulated_text: str) -> bool:
        """Update the streaming card — keeps the processing indicator element and
        adds/appends the AI response text as a second markdown element.

        修复飞书客户端 PATCH 替换同一元素时的布局残留问题：
        旧元素（"正在启动"）保留在卡片中作为状态指示，
        新内容作为一个独立元素追加在下方。
        """
        return await self._patch_card(message_id, [
            self._md("✅ 引擎已就绪"),
            self._md(accumulated_text),
        ])

    async def _patch_card(self, message_id: str, elements: list[dict]) -> bool:
        """Low-level PATCH to replace card elements. Shared by both
        edit_message (final) and update_streaming_card (streaming)."""
        token = await self._ensure_token()
        if not token:
            return False

        card = {
            "config": {"wide_screen_mode": True},
            "elements": elements,
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.patch(
                    f"{self._base_url}/open-apis/im/v1/messages/{message_id}",
                    json={"content": json.dumps(card)},
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning("Feishu _patch_card failed: %s", data)
                    return False
                return True
        except Exception as e:
            logger.error("Feishu _patch_card error: %s", e)
            return False

    @staticmethod
    def _md(text: str) -> dict:
        """Shorthand for a markdown card element."""
        return {"tag": "markdown", "content": text}

    @staticmethod
    def _make_card(markdown_content: str) -> dict:
        """Build a minimal Feishu interactive card with markdown content.

        无 header、无边框，外观接近普通文本消息，同时支持 PATCH 编辑。
        """
        return {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": markdown_content},
            ],
        }

    # ── UX: Processing indicator ────────────────────────────────────────

    async def send_processing(self, chat_id: str) -> Optional[str]:
        """Send a processing placeholder as interactive card message. Returns message_id.

        使用卡片消息（interactive）而非纯文本，因为飞书 PATCH API
        仅支持编辑卡片消息。后续的流式更新（send_streaming_update）
        通过 PATCH 修改此卡片的 markdown 内容实现。
        """
        token = await self._ensure_token()
        if not token:
            return None

        card = self._make_card("🤖 正在启动智能体引擎，请稍候... ⏳")
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card),
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                if data.get("code") == 0:
                    msg_id = data.get("data", {}).get("message_id", "")
                    return msg_id
                logger.warning("Feishu send_processing card failed: %s", data)
                return None
        except Exception as e:
            logger.error("Feishu send_processing error: %s", e)
            return None

    async def replace_with_response(self, chat_id: str, processing_msg_id: str, response: str) -> bool:
        """Final replace — show only the AI response (no status indicator)."""
        return await self._patch_card(processing_msg_id, [self._md(response)])

    async def replace_with_error(self, chat_id: str, processing_msg_id: str, error_msg: str) -> bool:
        return await self._patch_card(processing_msg_id, [self._md(error_msg)])

    async def send_streaming_update(self, chat_id: str, message_id: str, accumulated_text: str,
                                     show_status: bool = False) -> bool:
        """Periodically update the streaming message during engine response.

        Args:
            show_status: When True, include '✅ 引擎已就绪' status element above the response.
                         Used only for cold-start messages where the status card was shown.
        """
        if show_status:
            return await self.update_streaming_card(chat_id, message_id, accumulated_text)
        return await self._patch_card(message_id, [self._md(accumulated_text)])

    # ── Startup card (separate from response card) ────────────────────

    async def send_processing_done(self, chat_id: str, processing_msg_id: str) -> bool:
        """Update the startup-progress card to '✅ 引擎已就绪'.

        启动状态卡独立于 AI 回复卡，完成后固定显示就绪状态，
        不会被后续流式内容覆盖。
        """
        return await self._patch_card(processing_msg_id, [
            self._md("✅ 引擎已就绪"),
        ])

    async def send_initial_response(self, chat_id: str, text: str) -> Optional[str]:
        """Send a NEW card for the AI response (separate from startup card).

        冷启动时：启动状态卡 + 回复卡两条独立消息。
        热启动时：仅回复卡，无启动卡。
        Returns the new message_id.
        """
        token = await self._ensure_token()
        if not token:
            return None

        card = self._make_card(text)
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card),
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self._base_url}/open-apis/im/v1/messages?receive_id_type=chat_id",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
                if data.get("code") == 0:
                    return data.get("data", {}).get("message_id", "")
                logger.warning("Feishu send_initial_response failed: %s", data)
                return None
        except Exception as e:
            logger.error("Feishu send_initial_response error: %s", e)
            return None

    # ── Encryption helpers ──────────────────────────────────────────────

    def _decrypt_event(self, encrypt: str) -> str:
        """Decrypt Feishu encrypted event payload (AES-256-CBC)."""
        import base64
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        aes_key = hashlib.sha256(self.encrypt_key.encode("utf-8")).digest()
        encrypted = base64.b64decode(encrypt)
        iv = encrypted[:16]
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        pad = 16 - (len(encrypted[16:]) % 16)
        padded = decryptor.update(encrypted[16:]) + decryptor.finalize()
        # Remove PKCS7 padding
        pad_len = padded[-1]
        return padded[:-pad_len].decode("utf-8")
