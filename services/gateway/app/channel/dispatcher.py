"""Message dispatcher — orchestrates the full lifecycle for channel messages.

Flow per message:
  1. Dedup check (same agent_id + platform_message_id)
  2. Quick engine health check → send processing indicator if starting
  3. Ensure engine pod is running (call Controller if needed)
  4. Get or create engine-side session (30 min TTL; invalidated on restart)
  5. Forward message to engine via proxy
     - Streaming channels (Feishu): SSE streaming, periodic IM message updates
     - Non-streaming channels: full response, then one-shot send
  6. Send response back to user via platform API

Gateway 约束：不查询 Controller 或其他服务获取 upstream 地址，
直接通过 DNS 命名规范构造 URL: engine-hermes-{agent_id[:8]}.svc
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any

import httpx
from httpx import ConnectError, ConnectTimeout

from app.attachment_hint import inject_attachment_hints
from app.time_hint import inject_current_time_hint_into_body
from app.user_hint import inject_user_context_hint_into_body
from app.media_resolver import (
    INBOUND_FILE_MAX_BYTES,
    INBOUND_IMAGE_MAX_BYTES,
    INBOUND_VOICE_MAX_BYTES,
    looks_like_image,
)
from app.messages import (
    ACCESS_DENIED,
    AGENT_UNAVAILABLE,
    ATTACHMENT_FAILED,
    ENGINE_EMPTY_RESPONSE,
    ENGINE_START_FAILED,
    ENGINE_STARTING,
    NOT_BOUND,
    PROFILE_PREPARING,
    REPLY_FAILED,
    SESSION_RESET,
    SESSION_RESET_FAILED,
    STREAM_INTERRUPTED,
    VOICE_RECOGNIZE_FAILED,
)
from app.settings import settings
from .card_utils import extract_card_json
from .models import MessageEvent, MessageType
from .registry import get_adapter

logger = logging.getLogger(__name__)

# 消息去重 TTL（秒）—— 与企微回调重试窗口对齐（120s 内同 MsgId 重复投递只处理一次）
_DEDUP_TTL: float = 120.0

# 会话重置自助命令集（settings 配置，逗号分隔；trim+小写后精确匹配，不做包含/前缀匹配）
_RESET_COMMANDS: set[str] = {
    c.strip().lower() for c in settings.session_reset_commands.split(",") if c.strip()
}


def _is_reset_command(text: str) -> bool:
    """是否为会话重置命令。精确匹配（trim+小写），避免自然语言误触。"""
    if not text:
        return False
    return text.strip().lower() in _RESET_COMMANDS

# 引擎转发重试配置
_RETRY_MAX_ATTEMPTS = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]  # 指数退避（秒）

# 流式输出配置
_STREAM_FLUSH_INTERVAL = 0.5  # 刷新到 IM 的最小间隔（秒）
_STREAM_MIN_FLUSH_CHARS = 5  # 刷新到 IM 的最小新增字符数
# 流式硬超时（秒）—— 不管引擎是否持续发事件，流式总时长上限。
# 防止 agent 陷入工具重试死循环时 SSE 持续活跃、300s 读超时被刷新永不触发，
# 导致 per-agent 顺序队列被单个卡死请求阻塞。5min < 企微 6min 流式窗口，
# 超时后用已累积内容 + STREAM_INTERRUPTED 收尾，仍能在窗口内投递。
_STREAM_HARD_TIMEOUT = 300.0

# 入站附件大小上限（按消息类型）—— 超限丢弃，防御性兜底
_INBOUND_SIZE_LIMIT = {
    MessageType.IMAGE: INBOUND_IMAGE_MAX_BYTES,
    MessageType.VOICE: INBOUND_VOICE_MAX_BYTES,
    MessageType.FILE: INBOUND_FILE_MAX_BYTES,
}


class ChannelDispatcher:
    """Per-agent sequential message processing with lifecycle management."""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        # {channel_type:chat_id → (session_id, created_at)}
        self._sessions: dict[str, tuple[str, float]] = {}
        self._session_ttl: float = 1800.0  # 30 分钟
        # {(agent_id, platform_message_id) → expire_at}
        self._dedup: dict[tuple[str, str], float] = {}

    # ── Message dispatching ────────────────────────────────────────────

    async def dispatch(self, event: MessageEvent):
        """Queue a message for async processing.

        Returns immediately after dedup + queueing.
        """
        # 消息去重：同一 agent 的同一消息只处理一次
        dedup_key = (event.agent_id, event.platform_message_id)
        now = time.time()

        if dedup_key in self._dedup:
            logger.info(
                "Dedup: skipped duplicate message %s for agent %s",
                event.platform_message_id[:12],
                event.agent_id[:8],
            )
            return

        # 记录去重（连同清理过期的 dedup 条目）
        self._dedup[dedup_key] = now + _DEDUP_TTL
        self._clean_dedup(now)

        # 创建 per-agent 队列和工作协程（懒初始化）
        agent_id = event.agent_id
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
            self._workers[agent_id] = asyncio.create_task(self._process_queue(agent_id))
        await self._queues[agent_id].put(event)

    def _clean_dedup(self, now: float | None = None):
        """清理过期的去重记录（每次新增时顺带清理，O(n) 可接受）"""
        if now is None:
            now = time.time()
        expired = [k for k, v in self._dedup.items() if v <= now]
        for k in expired:
            del self._dedup[k]

    async def _process_queue(self, agent_id: str):
        """Single per-agent worker — processes messages sequentially."""
        queue = self._queues[agent_id]
        while True:
            event = await queue.get()
            try:
                await self._process_one(event)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Failed to process message for %s: %s", agent_id[:8], e)
            finally:
                queue.task_done()

    async def _process_one(self, event: MessageEvent):
        """Process a single message event — full lifecycle."""
        # 延迟导入避免测试环境下 pkg 路径问题
        from app.lifecycle import ensure_engine_ready
        from app.models import get_agent_model_config, get_channel_config_cached, get_default_model

        # 加载渠道配置（带 60s 缓存）
        config = await get_channel_config_cached(event.agent_id, event.channel_type)
        if not config:
            logger.warning("No channel config for %s/%s", event.agent_id[:8], event.channel_type)
            return

        adapter = get_adapter(event.channel_type, config)
        if not adapter:
            logger.warning("No adapter for channel type: %s", event.channel_type)
            return
        # 透传 UnionAgents agent UUID 给 adapter：出站时解析引擎回复里引用的工作区
        # 图片用（每次 dispatch 新建 adapter 实例，无共享竞态）。用独立属性 ua_agent_id，
        # 不覆盖子类自有的平台 agent_id（如企微数字 agent_id）。
        adapter.ua_agent_id = event.agent_id

        # Step 0: 权限闸门 — 未绑定/无权限/不可用时回 IM 提示并终止，
        #         不启动引擎（避免冷启动 DoS）、不转发（避免越权）。
        if not await self._check_im_access(event, adapter):
            return

        # Step 0.5: 语音转录 — VOICE event 调 adapter.transcribe 转文字，
        #           失败回兜底提示终止（不启动引擎）；成功转为 TEXT 复用文本链路。
        if event.message_type == MessageType.VOICE:
            text = await adapter.transcribe(event)
            if not text:
                await adapter.send_message(
                    event.chat_id,
                    VOICE_RECOGNIZE_FAILED,
                    reply_to=event.platform_message_id,
                )
                return
            logger.info("Voice transcribed: user=%s text=%s", event.user_id[:12], text[:80])
            event.text = text
            event.message_type = MessageType.TEXT

        # Step 0.6: 卡片按钮点击 — 走专用路径（转发合成消息到引擎，回复按
        #           _update_task_id 就地更新原卡片或发新消息），不走流式/启动卡。
        if event.raw_message.get("card_click"):
            await self._process_card_click(event, adapter)
            return

        # Step 1: 确保引擎运行，获取启动状态
        ready, was_already_running = await ensure_engine_ready(event.agent_id)
        if not ready:
            logger.error("Engine %s failed to start", event.agent_id[:8])
            err = ENGINE_START_FAILED
            await adapter.send_message(event.chat_id, err)
            return

        # Step 2: 引擎刚启动 → 清理该 agent 的所有 session 缓存
        if not was_already_running:
            self._invalidate_agent_sessions(event.agent_id)

        # Step 2.1: 会话重置命令 — 删引擎 session + 清 gateway 缓存，不转发引擎。
        # 放在 ensure_engine_ready 之后：DELETE 需引擎运行态；冷启动成本与正常对话一致。
        # 只影响发送者自己的 session（chat_id 即其 IM userid），权限闸门已校验。
        if event.message_type == MessageType.TEXT and _is_reset_command(event.text):
            ok = await self.reset_session(event)
            await adapter.send_message(
                event.chat_id,
                SESSION_RESET if ok else SESSION_RESET_FAILED,
                reply_to=event.platform_message_id,
            )
            return

        # Step 2.5: 附件处理 — IMAGE/FILE event 调 adapter 下载企微媒体 →
        #           写入引擎工作区 uploads/（经 manager /files/upload-internal）→
        #           产出结构化附件 {path, name, is_image} 挂到 event.attachments。
        #           [Attached files: path] 文本提示由 inject_attachment_hints 在转发
        #           引擎前合成进 content（与 web 通道统一约定），不在 event.text 里拼。
        #           必须在 ensure_engine_ready 之后：写工作区要 k8s exec 进引擎 Pod，
        #           引擎 SUSPEND（30min 空闲）后 Pod 销毁，冷启动时需先 ensure 拉起 Pod。
        if event.message_type in (MessageType.IMAGE, MessageType.FILE):
            att = await self._process_attachment(event, adapter)
            if not att:
                await adapter.send_message(
                    event.chat_id,
                    ATTACHMENT_FAILED,
                    reply_to=event.platform_message_id,
                )
                return
            logger.info(
                "Attachment processed: user=%s file=%s path=%s",
                event.user_id[:12],
                att.get("name"),
                att.get("path"),
            )
            event.attachments = [att]
            event.text = ""  # IM 图片/文件无文本；hint 由 inject_attachment_hints 合成
            event.message_type = MessageType.TEXT

        # Step 3: 仅在引擎刚刚启动时发送"处理中"提示
        processing_msg_id = None
        if not was_already_running:
            processing_msg_id = await adapter.send_processing(event.chat_id)

        # Step 4: 创建/恢复引擎端 session
        session_id = await self._get_or_create_session(event, was_already_running)

        # Step 5: 解析 Profile（用于 V2 多 Profile 隔离，V1 引擎忽略此 header）
        profile_name, profile_was_cold = await self._resolve_profile(event)
        if profile_name:
            logger.info(
                "Resolved profile %s for user %s agent %s",
                profile_name[:16],
                event.user_id[:12],
                event.agent_id[:8],
            )
        else:
            logger.debug("No profile resolved for user %s (V1 mode)", event.user_id[:12])

        # profile gateway 冷启动（engine 热启动时）→ 立即发"准备会话"提示。
        # engine 冷启动时已有 send_processing "🤖 启动中"，不重复发。
        if profile_was_cold and was_already_running and not processing_msg_id:
            await adapter.send_message(event.chat_id, PROFILE_PREPARING)

        # Step 6: 加载模型配置（从 agent model_config）
        model_config = await get_agent_model_config(event.agent_id)
        model = get_default_model(model_config)

        # Step 6.5: 卡片按钮点击事件 → 走专用处理（路由到 Profile → update_template_card）
        # event.raw_message["card_event"] 由 WeCom adapter.parse_incoming 设置
        if event.raw_message.get("card_event"):
            await self._process_card_event(event, adapter, session_id, model, profile_name)
            return

        # Step 7-8: 流式或非流式转发
        if adapter.supports_streaming:
            await self._process_one_streaming(
                event, adapter, session_id, processing_msg_id, model, profile_name
            )
        else:
            await self._process_one_response(
                event, adapter, session_id, processing_msg_id, model, profile_name
            )

    async def _process_card_click(self, event: MessageEvent, adapter):
        """button_interaction 按钮点击：转发合成消息到引擎，回复按 _update_task_id
        就地更新原卡片（update_template_card，带点击的 response_code）或发新消息。

        不走流式/启动卡（点击期望卡片更新，回复为 JSON 指令）。
        """
        from app.models import get_agent_model_config, get_default_model
        from app.lifecycle import ensure_engine_ready

        ready, was_already_running = await ensure_engine_ready(event.agent_id)
        if not ready:
            logger.error("Engine %s failed to start (card click)", event.agent_id[:8])
            await adapter.send_message(event.chat_id, ENGINE_START_FAILED)
            return
        if not was_already_running:
            self._invalidate_agent_sessions(event.agent_id)

        session_id = await self._get_or_create_session(event, was_already_running)
        profile_name, _profile_was_cold = await self._resolve_profile(event)
        model_config = await get_agent_model_config(event.agent_id)
        model = get_default_model(model_config)

        response = await self._forward_message_with_retry(event, session_id, model, profile_name)
        if not response:
            logger.warning(
                "Card click profile reply empty: user=%s task=%s key=%s",
                event.user_id[:12],
                event.raw_message.get("task_id", "")[:12],
                event.raw_message.get("event_key", ""),
            )
            return
        logger.info(
            "Card click profile reply: user=%s reply=%s", event.user_id[:12], response[:200]
        )
        await adapter.send_card_click_reply(event, response)

    async def _process_one_streaming(
        self,
        event: MessageEvent,
        adapter,
        session_id: str,
        processing_msg_id: str | None,
        model: str = "",
        profile_name: str = "",
    ):
        """流式处理消息 — 适用于支持增量编辑的平台（如飞书）。

        两条独立消息：
        1. 启动状态卡（仅在冷启动时）："🤖 正在启动…" → "✅ 引擎已就绪"
        2. AI 回复卡（每次都有）：纯回复内容，流式更新

        热启动时引擎已运行，直接发回复卡，无启动卡。
        """
        engine_was_cold = processing_msg_id is not None
        response_msg_id = None
        full_text = ""
        last_flush_text = ""
        last_flush_time = time.time()
        stream_error = False

        # "思考中..."提示已去掉（企微撤回会留痕迹，留着又干扰），用户等待正式回复即可。

        try:
            # 硬超时：不管引擎是否持续发事件，流式总时长上限 _STREAM_HARD_TIMEOUT。
            # 防止 agent 工具死循环时 SSE 持续活跃导致队列阻塞。
            async with asyncio.timeout(_STREAM_HARD_TIMEOUT):
                async for chunk in self._stream_from_engine(event, session_id, model, profile_name, adapter):
                    full_text += chunk

                    if response_msg_id is None:
                        # 首个 chunk → 发回复卡（新消息，与启动卡独立）
                        response_msg_id = await adapter.send_initial_response(
                            event.chat_id,
                            full_text,
                        )
                        if not response_msg_id:
                            # 回复卡发送失败，降级
                            logger.warning("Failed to send initial response card")
                            return

                        # 冷启动：更新启动卡为 "✅ 引擎已就绪"
                        if engine_was_cold:
                            await adapter.send_processing_done(
                                event.chat_id,
                                processing_msg_id,
                            )
                        continue  # 首个 chunk 不节流，立即展示

                    # 后续 chunk：节流刷新回复卡
                    now = time.time()
                    if (
                        len(full_text) - len(last_flush_text) >= _STREAM_MIN_FLUSH_CHARS
                        and now - last_flush_time >= _STREAM_FLUSH_INTERVAL
                    ):
                        try:
                            await adapter.send_streaming_update(
                                event.chat_id,
                                response_msg_id,
                                full_text,
                                show_status=engine_was_cold,
                            )
                            last_flush_text = full_text
                            last_flush_time = now
                        except Exception as e:
                            logger.warning("Streaming update failed (non-fatal): %s", e)
        except TimeoutError:
            logger.warning(
                "Engine stream hard timeout (%.0fs) for %s, interrupting with partial content",
                _STREAM_HARD_TIMEOUT, event.agent_id[:8],
            )
            stream_error = True
        except Exception as e:
            logger.warning("Engine stream error for %s: %s", event.agent_id[:8], e)
            stream_error = True

        # 最终：清理回复卡
        if full_text and response_msg_id:
            if stream_error:
                full_text += "\n\n" + STREAM_INTERRUPTED
            await adapter.replace_with_response(event.chat_id, response_msg_id, full_text)
        elif stream_error:
            await adapter.send_message(event.chat_id, REPLY_FAILED)
        elif not full_text and response_msg_id is None:
            # 引擎流式返回 200 但 0 内容 chunk（LLM 首 token 前失败：401/500/timeout 等，
            # 流式协议 200 先发、LLM 失败前无 token → 空流）。原逻辑静默不发 → 用户无响应。
            # 兜底发一条提示（无法区分具体错误，generic）。
            logger.warning(
                "Engine %s returned 200 but empty stream (LLM before-delivery failure?)",
                event.agent_id[:8],
            )
            try:
                await adapter.send_message(event.chat_id, ENGINE_EMPTY_RESPONSE)
            except Exception as e:
                logger.warning("Send empty-stream notice failed (non-fatal): %s", e)

    async def _process_one_response(
        self,
        event: MessageEvent,
        adapter,
        session_id: str,
        processing_msg_id: str | None,
        model: str = "",
        profile_name: str = "",
    ):
        """非流式处理消息 — 获取完整回复后一次性发送。

         适用于不支持增量编辑的平台（企微、钉钉）。

         WeCom 卡片透传：adapter 提供 ``send_reply`` 时走卡片/文本智能分发
        （``_parse_card`` 识别 ``msgtype`` JSON → ``send_card_message`` 透传，
         否则降级文本）。其他 adapter 走 ``send_message`` / ``replace_with_response``。
        """
        # Step 5: 转发消息到引擎（带重试）
        response = await self._forward_message_with_retry(event, session_id, model, profile_name)

        # Step 6: 发送回复给用户
        if not response:
            return

        send_reply = getattr(adapter, "send_reply", None)
        if callable(send_reply):
            # WeCom：卡片/文本智能分发
            await send_reply(event.chat_id, response)
            return

        if processing_msg_id:
            ok = await adapter.replace_with_response(
                event.chat_id,
                processing_msg_id,
                response,
            )
            if not ok:
                await adapter.send_message(
                    event.chat_id,
                    response,
                    reply_to=event.platform_message_id,
                )
        else:
            await adapter.send_message(
                event.chat_id,
                response,
                reply_to=event.platform_message_id,
            )

    # ── Card event (button_interaction click) ──────────────────────────

    async def _process_card_event(
        self,
        event: MessageEvent,
        adapter,
        session_id: str,
        model: str = "",
        profile_name: str = "",
    ):
        """处理 button_interaction 按钮点击：路由到 Profile，按回复更新原卡片或发新消息。

        WeCom ``template_card_event`` 点击事件由 adapter.parse_incoming 解析后放入
        ``event.raw_message["card_event"]``（含 task_id / key / response_code）。

        合成点击消息送达 Profile：``【企微卡片按钮点击】task_id=<tid> key=<key>``
        Profile 回复：
        - JSON 含 ``_update_task_id`` + ``template_card`` → ``update_template_card`` 更新原卡片
        - 其他（card/text）→ ``send_reply`` 发新消息
        - 空 → 仅日志（点击失败不打扰用户）
        """
        card_event = event.raw_message.get("card_event", {})
        task_id = card_event.get("task_id", "")
        key = card_event.get("key", "")
        response_code = card_event.get("response_code", "")

        # event.text 已由 parse_incoming 设置为合成消息
        reply = await self._forward_message_with_retry(event, session_id, model, profile_name)
        logger.info(
            "Card click profile reply: user=%s task=%s key=%s reply=%s",
            event.user_id[:12],
            task_id,
            key,
            (reply or "")[:400],
        )

        if not reply:
            logger.warning(
                "Card click profile reply empty: user=%s task=%s key=%s",
                event.user_id[:12],
                task_id,
                key,
            )
            return

        # 解析回复为卡片更新指令
        upd = self._parse_card_update(reply)
        if upd:
            upd_task_id, card_body = upd
            update_card = getattr(adapter, "update_template_card", None)
            if callable(update_card):
                result = await update_card(
                    event.chat_id,
                    upd_task_id,
                    response_code,
                    card_body,
                )
                if isinstance(result, dict) and result.get("errcode") not in (0, None):
                    logger.warning("update_template_card failed: %s", result)
            else:
                logger.warning(
                    "Adapter %s does not support update_template_card, skipping card update",
                    type(adapter).__name__,
                )
            return

        # 否则发新消息（卡片或文本）
        send_reply = getattr(adapter, "send_reply", None)
        if callable(send_reply):
            await send_reply(event.chat_id, reply)
        else:
            await adapter.send_message(
                event.chat_id,
                reply,
                reply_to=event.platform_message_id,
            )

    @staticmethod
    def _parse_card_update(content: str) -> tuple[str, dict] | None:
        """解析回复为卡片更新指令。返回 (task_id, template_card_body) 或 None。

        约定：回复为
        ``{"msgtype":"template_card","template_card":{...},"_update_task_id":"<tid>"}``，
        前后可夹带说明文字/代码围栏（由 card_utils.extract_card_json 容错提取）。
        与 WeComAdapter._parse_card_update 共用提取器；仅企微 card_event 路径可达。
        """
        obj, _, _ = extract_card_json(content, required_key="_update_task_id")
        if not isinstance(obj, dict):
            return None
        tid = obj.get("_update_task_id")
        body = obj.get("template_card")
        if tid and isinstance(body, dict):
            return tid, body
        return None

    # ── Session management ─────────────────────────────────────────────

    def _session_key(self, event: MessageEvent) -> str:
        """生成 session 缓存 key（含 agent_id 前缀，方便按 agent 批量清除）"""
        return f"{event.agent_id}:{event.channel_type}:{event.chat_id}"

    def _invalidate_agent_sessions(self, agent_id: str):
        """清除指定 agent 的所有 session 缓存（引擎重启后调用）"""
        keys_to_remove = [k for k in self._sessions if k.startswith(f"{agent_id}:")]
        for k in keys_to_remove:
            del self._sessions[k]
        if keys_to_remove:
            logger.info("Invalidated %d session(s) for agent %s", len(keys_to_remove), agent_id[:8])

    async def reset_session(self, event: MessageEvent) -> bool:
        """重置单个用户的会话：删除引擎端 session（state.db 历史+消息+文件）+ 清 gateway 缓存。

        用确定性 session_id 定位引擎 session（与 _get_or_create_session 同一派生）；
        删除后下条消息以同一 id 重建空会话，im_user_binding 等不受影响。引擎需处于
        运行态（调用前已 ensure_engine_ready）。404 视为成功（会话本就不存在，幂等）。

        DELETE 必须带 ``x-hermes-profile`` 头：多 profile Pod 下 nginx 按该头路由到
        目标 profile，否则路由 base profile，base 无该 session 返回 404 误判成功。
        """
        session_key = self._session_key(event)
        session_id = self._deterministic_session_id(session_key)
        # 清 gateway 缓存，避免 30min TTL 内复用旧 session 行
        self._sessions.pop(session_key, None)
        from app.lifecycle import resolve_engine_url

        # 解析 profile_name 用于 nginx 路由（多 profile Pod 必需）。
        # _resolve_profile 命中 60s 缓存通常不触发 ensure；失败则降级不带 header。
        profile_name = ""
        try:
            profile_name, _ = await self._resolve_profile(event)
        except Exception as e:
            logger.warning("reset_session resolve profile failed: %s", e)

        try:
            engine_url = await resolve_engine_url(event.agent_id)
            headers = {"authorization": f"Bearer {settings.api_server_key}"}
            if profile_name:
                headers["x-hermes-profile"] = profile_name
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{engine_url}/api/sessions/{session_id}",
                    headers=headers,
                )
            if resp.status_code in (200, 204, 404):
                logger.info("Session %s reset for %s", session_id[:8], session_key)
                return True
            logger.warning(
                "Session reset failed: %s %s", resp.status_code, resp.text[:200]
            )
            return False
        except Exception as e:
            logger.warning("Session reset error for %s: %s", session_key, e)
            return False

    @staticmethod
    def _deterministic_session_id(session_key: str) -> str:
        """从 session key 生成确定性 Hermes session ID。

        同一 agent + 渠道 + 用户 => 永远映射到同一个 session_id，
        确保跨 Gateway/引擎重启后会话上下文仍然连续。
        """
        return hashlib.sha256(session_key.encode()).hexdigest()[:24]

    async def _get_or_create_session(
        self, event: MessageEvent, engine_just_started: bool = False
    ) -> str:
        """获取或创建引擎端 Hermes session。

        使用确定性 session_id（由 agent_id + channel_type + chat_id 的
        SHA256 哈希派生），确保同一用户同一渠道的会话永远映射到同一个
        session_id，引擎端 state.db 随消息逐步累积历史记录。

        Returns:
            session_id（即使 API 调用失败也返回确定性 ID 作为兜底）
        """
        session_key = self._session_key(event)
        session_id = self._deterministic_session_id(session_key)
        now = time.time()

        # 缓存命中（且引擎未重启）→ 跳过重复的 POST /api/sessions 调用
        if not engine_just_started:
            cached = self._sessions.get(session_key)
            if cached is not None:
                _, created_at = cached
                if now < created_at + self._session_ttl:
                    return session_id

        # 尝试在引擎端创建 session 行（存 origin 元数据，非必需）
        from app.lifecycle import resolve_engine_url

        engine_url = await resolve_engine_url(event.agent_id)
        session_name = f"{event.channel_type}-{event.chat_id[:12]}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{engine_url}/api/sessions",
                    json={
                        "id": session_id,
                        "name": session_name,
                        "origin": {
                            "platform": event.channel_type,
                            "chat_id": event.chat_id,
                            "user_id": event.user_id,
                            "user_name": event.user_name,
                        },
                    },
                    headers={"authorization": f"Bearer {settings.api_server_key}"},
                )
                if resp.status_code in (200, 201):
                    self._sessions[session_key] = (session_id, now)
                    logger.debug(
                        "Session %s ready for %s (%d)",
                        session_id[:8],
                        session_key,
                        resp.status_code,
                    )
                    return session_id
                elif resp.status_code == 409:
                    # Session already exists — 正常情况，忽略
                    self._sessions[session_key] = (session_id, now)
                    logger.debug("Session %s already exists for %s", session_id[:8], session_key)
                    return session_id
        except Exception as e:
            logger.warning("Failed to create session row for %s: %s", session_key, e)

        # 兜底：即使 API 调用失败，确定性 ID 仍然是正确的
        self._sessions[session_key] = (session_id, now)
        logger.debug("Session %s (deterministic fallback) for %s", session_id[:8], session_key)
        return session_id

    # ── Profile resolution (V2 multi-profile) ──────────────────────────

    async def _check_im_access(self, event: MessageEvent, adapter) -> bool:
        """权限闸门：转发引擎前校验 IM 映射 + 访问权限 + channel 存在性。

        命中拒绝时向 IM 用户回明确提示并返回 False（调用方应 return 不继续）。
        基础设施类异常（DB/Controller 不可用）不拦截，返回 True 降级继续，
        由后续 _resolve_profile 处理。

        Returns:
            True=放行；False=已拒绝（已回 IM 提示，调用方应终止）。
        """
        from app.profile_resolver import (
            AccessDenied,
            NotBound,
            ProfileNotFound,
            profile_resolver,
        )

        try:
            await profile_resolver.check_access(
                event.user_id,
                event.agent_id,
                event.channel_type,
            )
            return True
        except NotBound:
            msg = NOT_BOUND
            reason = "not_bound"
        except AccessDenied:
            msg = ACCESS_DENIED
            reason = "access_denied"
        except ProfileNotFound:
            msg = AGENT_UNAVAILABLE
            reason = "not_found"
        except Exception as e:
            # 基础设施异常 → 不拦截，降级继续
            logger.warning(
                "Access check infra error for agent %s: %s (will degrade)", event.agent_id[:8], e
            )
            return True

        await adapter.send_message(
            event.chat_id,
            msg,
            reply_to=event.platform_message_id,
        )
        logger.info(
            "Access gate blocked user %s agent %s: %s",
            event.user_id[:12],
            event.agent_id[:8],
            reason,
        )
        return False

    async def _resolve_profile(self, event: MessageEvent) -> tuple[str, bool]:
        """解析当前用户所属的 Hermes Profile 名称。

        V2 引擎用：Profile 名由 agent_id + scope + user_id 确定性派生。
        V1 引擎忽略此值（不传 X-Hermes-Profile header 即可）。
        如果 Controller ensure_profile 失败，返回空（降级为 V1 模式）。

        返回 (profile_name, was_cold)：was_cold=True 表示 profile 冷启动
        （internal_port 为空，调了 Controller ensure）。

        注意：权限/路由类异常（NotBound/AccessDenied/ProfileNotFound）由
        _check_im_access 闸门先行拦截；此处若仍抛出（竞态），必须向上传播，
        绝不降级为 V1——否则等于静默转发=越权。仅基础设施异常降级。
        """
        from app.profile_resolver import (
            AccessDenied,
            NotBound,
            ProfileNotFound,
            profile_resolver,
        )
        from app.settings import settings as gw_settings

        try:
            target = await profile_resolver.resolve(
                user_id=event.user_id,
                agent_id=event.agent_id,
                channel_type=event.channel_type,
            )

            # was_cold 由 profile_resolver.resolve 设定（cached_port 无 or force_ensure
            # → 调了 controller ensure = profile 冷启动）
            return target.profile_name, target.was_cold
        except (NotBound, AccessDenied, ProfileNotFound):
            # 权限/路由类异常：闸门已拦截，此处为竞态兜底，向上抛出避免静默转发
            raise
        except Exception as e:
            logger.debug(
                "Profile resolution skipped for %s: %s (V1 mode fallback)", event.user_id[:12], e
            )
            return "", False  # V1 降级（非冷启动）

    # ── Message forwarding (with retry) ────────────────────────────────

    async def _process_attachment(self, event: MessageEvent, adapter) -> dict | None:
        """IM 附件（图片/文件/视频）→ 下载 → 写引擎工作区 → 返回结构化附件 dict。

        企微回调只给 media_id（文件不在报文里，3 天内需 media/get 拉），与 web 通道
        浏览器 FormData 直传不同：这里先下载字节，再经 manager /files/upload-internal
        写入同一个 profile 工作区 uploads/。返回 ``{path, name, is_image}``，由调用方
        挂到 event.attachments；``[Attached files: path]`` 文本提示在转发引擎前由
        inject_attachment_hints 统一合成。失败返回 None，由调用方回兜底提示。
        """
        rm = event.raw_message or {}
        media_id = rm.get("media_id", "")
        url = rm.get("url", "")
        msg_type = rm.get("msg_type", "")
        logger.info(
            "Attachment process start: user=%s msg_type=%s media_id=%s url=%s file_name=%s",
            event.user_id[:12],
            msg_type,
            media_id[:16],
            bool(url),
            rm.get("file_name", ""),
        )
        # 定位符：自建应用用 media_id，智能机器人用加密 url，二者至少有一个
        if not media_id and not url:
            logger.warning(
                "Attachment event without media_id/url: user=%s", event.user_id[:12]
            )
            return None
        # 文件名：file/video 带 FileName；image 企微不给文件名，按定位符生成 + 扩展名
        file_name = rm.get("file_name") or ""
        if not file_name:
            locator = media_id or url
            ext = "jpg" if event.message_type == MessageType.IMAGE else "bin"
            file_name = f"{locator[:16]}.{ext}"
        media_bytes = await adapter.fetch_attachment_bytes(event)
        logger.info(
            "Attachment download: user=%s msg_type=%s bytes=%d",
            event.user_id[:12],
            msg_type,
            len(media_bytes),
        )
        if not media_bytes:
            logger.warning(
                "Attachment media download empty: user=%s media_id=%s url=%s",
                event.user_id[:12],
                media_id[:16],
                bool(url),
            )
            return None
        # 大小上限（防御性兜底）：按类型取上限，超限丢弃，caller 回兜底提示
        size_limit = _INBOUND_SIZE_LIMIT.get(event.message_type, INBOUND_FILE_MAX_BYTES)
        if len(media_bytes) > size_limit:
            logger.warning(
                "Attachment oversize: user=%s msg_type=%s bytes=%d limit=%d",
                event.user_id[:12],
                msg_type,
                len(media_bytes),
                size_limit,
            )
            return None
        # Magic bytes（仅图片）：防止把 HTML 错误页/恶意内容当图片写入 workspace
        if event.message_type == MessageType.IMAGE and not looks_like_image(media_bytes):
            logger.warning(
                "Attachment image magic-bytes mismatch: user=%s media_id=%s",
                event.user_id[:12],
                media_id[:16],
            )
            return None
        path = await self._write_to_workspace(event.agent_id, file_name, media_bytes)
        logger.info(
            "Attachment write: user=%s file=%s path=%s", event.user_id[:12], file_name, path
        )
        if not path:
            return None
        return {
            "path": path,
            "name": file_name,
            "is_image": event.message_type == MessageType.IMAGE,
        }

    async def _write_to_workspace(self, agent_id: str, filename: str, content: bytes) -> str:
        """经 manager /files/upload-internal 把附件字节写入引擎 profile 工作区 uploads/。

        gateway↔manager 服务间信任：用 X-Internal-Token 鉴权（同 media_resolver 模式）。
        返回相对工作区路径（如 uploads/xxx.png）；失败返回空串。
        """
        token = settings.internal_token
        if not token:
            logger.error("INTERNAL_TOKEN not configured, cannot write attachment to workspace")
            return ""
        url = (
            f"{settings.controller_url}/api/manager/agent-instances/"
            f"{agent_id}/files/upload-internal"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    headers={"X-Internal-Token": token},
                    files={"file": (filename, content)},
                )
                if resp.status_code != 200:
                    logger.warning(
                        "upload-internal failed (%s): %s", resp.status_code, resp.text[:200]
                    )
                    return ""
                data = resp.json()
                return data.get("path", "")
        except Exception as e:
            logger.error("upload-internal error for agent %s: %s", agent_id[:8], e)
            return ""

    async def _forward_message_with_retry(
        self, event: MessageEvent, session_id: str, model: str = "", profile_name: str = ""
    ) -> str | None:
        """转发消息到引擎，失败时带指数退避重试。"""
        last_error = None
        for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
            result = await self._forward_message(event, session_id, model, profile_name)
            if result is not None:
                return result  # 成功

            # 不需要重试的情况由 _forward_message 内部的 None 表示
            # 但如果是超时/连接类错误，我们尝试重试
            if attempt < _RETRY_MAX_ATTEMPTS:
                wait = _RETRY_BACKOFF[min(attempt - 1, len(_RETRY_BACKOFF) - 1)]
                logger.info(
                    "Retry %d/%d for agent %s in %.1fs",
                    attempt,
                    _RETRY_MAX_ATTEMPTS,
                    event.agent_id[:8],
                    wait,
                )
                await asyncio.sleep(wait)

        logger.error("All %d retries failed for agent %s", _RETRY_MAX_ATTEMPTS, event.agent_id[:8])
        return None

    def _build_engine_messages(self, event: MessageEvent) -> list[dict]:
        """构造发给引擎的 messages：user content + 结构化 attachments（若有）。

        attachments 由 inject_attachment_hints 在调用处合成 [Attached files: path] 进
        content 并剥离字段；event.text 保持干净（IM 图片/文件 event.text 为空）。
        """
        msg: dict = {"role": "user", "content": event.text}
        if event.attachments:
            msg["attachments"] = event.attachments
        return [msg]

    async def _forward_message(
        self, event: MessageEvent, session_id: str, model: str = "", profile_name: str = ""
    ) -> str | None:
        """Forward the message to the engine and return the response text.

        返回 None 表示失败（需要上层决定是否重试）。
        """
        from app.lifecycle import resolve_engine_url
        from app.langfuse_client import (
            _hash_last_user_message,
            trace_chat,
            finalize_chat_from_body,
        )
        from datetime import datetime, UTC
        import time as _time

        engine_url = await resolve_engine_url(event.agent_id)
        messages = inject_attachment_hints(self._build_engine_messages(event))
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if settings.inject_current_time:
            inject_current_time_hint_into_body(
                payload, "/v1/chat/completions", None, settings.default_timezone
            )
        if settings.inject_user_context and profile_name:
            from app.profile_resolver import profile_resolver

            inject_user_context_hint_into_body(
                payload, "/v1/chat/completions",
                profile_resolver.get_user_context(profile_name),
            )
        headers = {
            "authorization": f"Bearer {settings.api_server_key}",
            "x-hermes-session-id": session_id,
            "content-type": "application/json",
        }
        if profile_name:
            headers["x-hermes-profile"] = profile_name

        payload_bytes = json.dumps(payload).encode("utf-8")
        lf_trace, lf_gen = trace_chat(
            agent_id=event.agent_id,
            engine_type="hermes",
            path="/v1/chat/completions",
            method="POST",
            model=model or None,
            input_body=payload_bytes,
            session_id=session_id,
            enduser_id=event.user_id,
            channel_type=event.channel_type,
            last_user_message_hash=_hash_last_user_message(payload_bytes),
            gateway_request_time=_time.time(),
        )

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                resp = await client.post(
                    f"{engine_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code == 503:
                    # 引擎不可用 — 可重试
                    logger.warning(
                        "Engine %s 503 (attempt will retry): %s",
                        event.agent_id[:8],
                        resp.text[:200],
                    )
                    return None
                if resp.status_code != 200:
                    logger.error(
                        "Engine %s returned %d: %s",
                        event.agent_id[:8],
                        resp.status_code,
                        resp.text[:200],
                    )
                    return None
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                content = (choice.get("message") or choice.get("delta") or {}).get("content", "")
                # 关闭 trace：从响应体提取 text + usage
                try:
                    finalize_chat_from_body(
                        lf_trace, lf_gen, resp.content, end_time=datetime.now(UTC)
                    )
                except Exception as e:
                    logger.warning("Langfuse finalize_chat_from_body failed: %s", e)
                return content
        except (ConnectError, ConnectTimeout, httpx.ReadTimeout) as e:
            logger.warning(
                "Engine %s connection error (attempt will retry): %s", event.agent_id[:8], e
            )
            return None  # 连接错误可重试
        except Exception as e:
            logger.exception("Forward message to engine %s failed: %s", event.agent_id[:8], e)
            return None

    # ── SSE streaming ──────────────────────────────────────────────────

    async def _stream_from_engine(
        self, event: MessageEvent, session_id: str, model: str = "", profile_name: str = "",
        adapter=None,
    ):
        """Stream engine response via SSE, yielding text content chunks.

        使用 OpenAI 兼容的 SSE 格式（stream: true），
        适用于支持流式编辑的平台（如飞书）。
        """
        from app.lifecycle import resolve_engine_url
        from app.langfuse_client import (
            _hash_last_user_message,
            _parse_hermes_tool_progress_from_chunk,
            _parse_tool_calls_from_sse_chunk,
            end_tool_call_spans,
            finalize_chat_from_sse,
            trace_chat,
            update_hermes_tool_span,
            update_tool_call_span,
        )
        from datetime import datetime, UTC
        import time as _time

        engine_url = await resolve_engine_url(event.agent_id)
        messages = inject_attachment_hints(self._build_engine_messages(event))
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if settings.inject_current_time:
            inject_current_time_hint_into_body(
                payload, "/v1/chat/completions", None, settings.default_timezone
            )
        if settings.inject_user_context and profile_name:
            from app.profile_resolver import profile_resolver

            inject_user_context_hint_into_body(
                payload, "/v1/chat/completions",
                profile_resolver.get_user_context(profile_name),
            )
        headers = {
            "authorization": f"Bearer {settings.api_server_key}",
            "x-hermes-session-id": session_id,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }
        if profile_name:
            headers["x-hermes-profile"] = profile_name

        payload_bytes = json.dumps(payload).encode("utf-8")
        lf_trace, lf_gen = trace_chat(
            agent_id=event.agent_id,
            engine_type="hermes",
            path="/v1/chat/completions",
            method="POST",
            model=model or None,
            input_body=payload_bytes,
            session_id=session_id,
            enduser_id=event.user_id,
            channel_type=event.channel_type,
            last_user_message_hash=_hash_last_user_message(payload_bytes),
            gateway_request_time=_time.time(),
        )

        first_token_at: datetime | None = None
        raw_sse_lines: list[str] = []
        # OpenAI SSE tool_calls delta 累积容器，流结束统一 end
        tool_call_spans: dict[str, Any] = {}

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST", f"{engine_url}/v1/chat/completions", json=payload, headers=headers
                ) as resp:
                    if resp.status_code != 200:
                        logger.error(
                            "Engine %s SSE returned %d", event.agent_id[:8], resp.status_code
                        )
                        return

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if line.startswith("data: "):
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                return
                            raw_sse_lines.append(line)
                            # OpenAI SSE tool_calls delta + Hermes tool.progress
                            # → Langfuse SPAN observation + adapter.on_tool_progress hook
                            for tc in _parse_tool_calls_from_sse_chunk(line):
                                if lf_trace is not None:
                                    update_tool_call_span(
                                        lf_trace, tool_call_spans, tc, now=datetime.now(UTC)
                                    )
                                if adapter is not None:
                                    await adapter.on_tool_progress(event, tc)
                            for htc in _parse_hermes_tool_progress_from_chunk(line):
                                if lf_trace is not None:
                                    update_hermes_tool_span(
                                        lf_trace, tool_call_spans, htc, now=datetime.now(UTC)
                                    )
                                if adapter is not None:
                                    await adapter.on_tool_progress(event, htc)
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                # reasoning（DeepSeek reasoning_content / Qwen reasoning）
                                # → adapter.on_reasoning hook（默认 no-op，wecom_bot_callback 包 <think>）
                                reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                                if reasoning and adapter is not None:
                                    await adapter.on_reasoning(event, reasoning)
                                if content:
                                    if first_token_at is None:
                                        first_token_at = datetime.now(UTC)
                                    yield content
                            except (json.JSONDecodeError, IndexError, KeyError):
                                continue

        except (ConnectError, ConnectTimeout) as e:
            logger.warning("Engine %s SSE connection error: %s", event.agent_id[:8], e)
            return
        finally:
            # generator 自然结束 / 异常 / 提前 break 都会执行——关闭 trace
            raw = "\n".join(raw_sse_lines)
            try:
                finalize_chat_from_sse(
                    lf_trace,
                    lf_gen,
                    raw,
                    end_time=datetime.now(UTC),
                    completion_start_time=first_token_at,
                )
            except Exception as e:
                logger.warning("Langfuse finalize_chat_from_sse failed: %s", e)
            # 统一 end 所有 tool_call SPAN
            if tool_call_spans:
                try:
                    end_tool_call_spans(tool_call_spans, end_time=datetime.now(UTC))
                except Exception as e:
                    logger.warning("Langfuse end_tool_call_spans failed: %s", e)


# Global singleton
dispatcher = ChannelDispatcher()
