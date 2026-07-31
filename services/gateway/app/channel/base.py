"""Abstract base class for all IM channel adapters."""
from abc import ABC, abstractmethod
from typing import Optional

from fastapi import Request

from .models import MessageEvent


class BaseChannelAdapter(ABC):
    """Channel adapter base — all IM platform adapters inherit from this.

    Each adapter handles:
    - Webhook signature verification
    - Incoming message parsing → MessageEvent
    - Sending messages/responses back to the platform
    - Processing indicator UX (optional, based on platform capability)
    """

    def __init__(self, config: dict):
        self.config = config
        # 当前 dispatch 的 UnionAgents agent UUID（由 dispatcher 在创建 adapter 后设置）。
        # 注意：与平台自身的 agent_id（如企微数字 agent_id，子类 __init__ 从 config 读到
        # self.agent_id）是两个不同概念——这里用独立属性 ua_agent_id 存 UUID，避免覆盖。
        # adapter 每次 dispatch 新建实例（registry 只缓存类），无跨请求共享竞态。
        # 出站时用于解析引擎回复里引用的工作区图片（IM 通道无法直接 fetch 工作区文件）。
        self.ua_agent_id: str = ""

    # ── Mandatory: security ────────────────────────────────────────────

    @abstractmethod
    async def verify_signature(self, request: Request) -> bool:
        """Validate the webhook request's authenticity signature."""

    # ── Mandatory: parsing ─────────────────────────────────────────────

    @abstractmethod
    async def parse_incoming(self, request: Request) -> list[MessageEvent]:
        """Parse the platform's webhook payload into unified MessageEvent list."""

    # ── Optional: platform verification (Challenge, URL verify) ────────

    async def handle_verification(self, request: Request):
        """Handle platform-specific verification requests (e.g. Feishu Challenge).

        Returns a Response if the request is a verification handshake,
        or None if it's a regular message (let parse_incoming handle it).
        """
        return None

    # ── Optional: synchronous callback handling ───────────────────────

    async def handle_callback(self, request: Request, events: list["MessageEvent"], dispatch):
        """Handle a webhook callback synchronously and return a Response.

        Most platforms use the default async model: webhook returns 202 immediately,
        processing happens in background via ``dispatch``. Override this to return a
        synchronous Response in the callback's HTTP body — required by platforms whose
        protocol demands an immediate encrypted reply in the response (e.g. WeCom
        smart-robot passive reply / streaming refresh).

        ``dispatch`` is ``ChannelDispatcher.dispatch``; call it to enqueue events for
        background processing before returning the synchronous Response.

        Returns:
            A Response to return to the platform, or None to fall through to the
            default async-dispatch + 202 path.
        """
        return None

    # ── Mandatory: sending ─────────────────────────────────────────────

    @abstractmethod
    async def send_message(self, chat_id: str, text: str, reply_to: str = "") -> bool:
        """Send a text message to a chat/group conversation."""

    async def send_markdown(self, chat_id: str, markdown: str) -> bool:
        """Send a markdown-formatted message. Default falls back to send_message."""
        return await self.send_message(chat_id, markdown)

    # ── Optional: voice transcription ──────────────────────────────────

    async def transcribe(self, event: "MessageEvent") -> str:
        """Transcribe a VOICE event to text.

        Called by dispatcher when ``event.message_type == VOICE``. Adapter
        downloads the platform's voice media and calls its ASR backend.
        Default: no transcription (returns event.text). Failure should return "".
        """
        return event.text

    # ── Optional: inbound attachment download ──────────────────────────

    async def fetch_attachment_bytes(self, event: "MessageEvent") -> bytes:
        """Download inbound attachment (image/file/video) raw bytes.

        Called by ``dispatcher._process_attachment``. Each adapter implements
        its platform-specific media retrieval:

        - WeCom self-built app (``wecom``): ``media_id`` → ``media/get`` API
          with access_token (see ``_media_get``).
        - WeCom smart robot (``wecom_bot_callback``): encrypted ``url`` → HTTP GET →
          AES-256-CBC decrypt with the callback AESKey.

        The dispatcher handles size limits, magic-byte checks, and writing to
        the engine workspace — adapters only need to return the raw bytes here.
        Default returns ``b""`` (treated as download failure → ATTACHMENT_FAILED).
        """
        return b""

    # ── Optional: streaming reasoning / tool progress ──────────────────

    async def on_reasoning(self, event: "MessageEvent", text: str) -> None:
        """Called during SSE streaming when the engine emits reasoning text
        (``delta.reasoning_content`` / ``delta.reasoning``).

        Adapters that surface thinking (e.g. WeCom smart robot wraps it in
        ``<think></think>`` tags for native folding) override this. Default
        no-op — reasoning is ignored (current behavior for feishu/wecom).
        """

    async def on_tool_progress(self, event: "MessageEvent", tool_info: dict) -> None:
        """Called during SSE streaming when the engine emits a tool-call event.

        ``tool_info`` keys: ``tool_call_id``, ``status`` (running/completed),
        ``tool`` (name), ``label`` (Hermes) or ``function_name``/``arguments_delta``
        (OpenAI delta.tool_calls). Adapters that surface tool cards override
        this. Default no-op.
        """

    # ── Optional: card click reply ─────────────────────────────────────

    async def send_card_click_reply(self, event: "MessageEvent", response: str) -> bool:
        """Reply to a card-button-click event.

        Called by dispatcher when ``event.raw_message.get("card_click")``. Adapter
        may update the original card in-place (e.g. WeCom update_template_card with
        the click's response_code) or send a new message. Default: send_message.
        """
        return await self.send_message(event.chat_id, response)

    # ── UX: processing indicator ───────────────────────────────────────

    async def send_processing(self, chat_id: str) -> Optional[str]:
        """Send a 'processing…' placeholder. Returns message_id for later update.

        Platforms like Feishu/DingTalk support editing messages, so the
        placeholder can be replaced with the actual response.
        WeCom does not support editing — returns None (falls back to new message).
        """
        return None

    async def replace_with_response(
        self, chat_id: str, processing_msg_id: str, response: str,
    ) -> bool:
        """Replace the processing placeholder with the actual response.

        Feishu/DingTalk: edit_message() to replace content in-place.
        WeCom: returns False (fallback to send_message for a new message).
        """
        return False

    async def replace_with_error(
        self, chat_id: str, processing_msg_id: str, error_msg: str,
    ) -> bool:
        """Replace the processing placeholder with an error message."""
        return await self.replace_with_response(chat_id, processing_msg_id, error_msg)

    # ── UX: startup progress + response as separate cards ─────────────

    async def send_processing_done(self, chat_id: str, processing_msg_id: str) -> bool:
        """Update the startup-progress card to show 'completed' status.

        Called when the engine finishes starting — transforms the
        '🤖 正在启动…' card into a '✅ 引擎已就绪' status card.
        The actual AI response is sent as a separate message.
        """
        return await self.replace_with_response(chat_id, processing_msg_id, "✅ 引擎已就绪")

    async def send_initial_response(self, chat_id: str, text: str) -> Optional[str]:
        """Send a new message with the first chunk of the AI response.

        Only called when supports_streaming=True.
        Returns the new message_id (or None on failure).
        After this, streaming updates are applied to this new message
        via send_streaming_update / replace_with_response.

        Default fallback: calls send_message and returns chat_id.
        """
        ok = await self.send_message(chat_id, text)
        return chat_id if ok else None

    # ── Optional: URL verification (GET) ───────────────────────────────

    async def verify_url(self, request: Request):
        """Handle platform URL verification (GET) — e.g. WeCom echostr challenge.

        Returns a Response on verification, or a 404 Response if not supported.
        Default: return 404 Not Found.
        """
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)

    # ── Streaming output support ──────────────────────────────────────

    @property
    def supports_streaming(self) -> bool:
        """Whether the platform supports incremental message updates (streaming output).

        When True, the dispatcher will use SSE streaming from the engine and
        periodically update the message via send_streaming_update(), giving the
        user a progressive output experience. When False, the dispatcher waits
        for the full response and sends it all at once.
        """
        return False

    async def send_streaming_update(self, chat_id: str, message_id: str, accumulated_text: str,
                                     show_status: bool = False) -> bool:
        """Update a streaming message with accumulated text.

        Only called when supports_streaming=True. Periodically called during
        engine response generation to show progressive output.

        Args:
            show_status: When True, include a status indicator (e.g. '✅ engine ready')
                         alongside the response. Only meaningful for cold-start messages.
        Default falls back to replace_with_response (final-only update).
        """
        return await self.replace_with_response(chat_id, message_id, accumulated_text)

    # ── Message length constraints ─────────────────────────────────────

    @property
    def max_text_length(self) -> int:
        """Platform's maximum text message length in characters."""
        return 0  # 0 = no limit (platform handles it)

    def _truncate(self, text: str) -> str:
        """Truncate text to platform's max length (with ellipsis)."""
        n = self.max_text_length
        if n > 0 and len(text) > n:
            return text[: n - 3] + "..."
        return text

    # ── Identity ───────────────────────────────────────────────────────

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Unique identifier for this channel type (e.g. 'wecom', 'feishu')."""
