"""企业微信「智能机器人」（URL 回调模式）channel adapter — wecom_bot_callback.

与自建应用 ``wecom``（HTTP 回调 + access_token 推 message/send）不同，智能机器人
采用**被动回复**模型：

- 回调信封是 JSON ``{"encrypt": "..."}``（自建应用是 XML ``<Encrypt>``），AES
  加解密算法与自建应用完全一致，仅 ``receiveid`` 传空串。
- 回复在回调的 HTTP 响应里**同步**返回加密 JSON envelope
  ``{"encrypt","msgsignature","timestamp","nonce"}``（nonce 复用请求 nonce）。
- **流式**：消息推送回调同步返回 ``stream`` 首帧 → 企微反复推「流式刷新」回调
  (``msgtype=stream``, ``stream.id``) → 每次返回**累积全文** → ``finish=true`` 结束。
  这是拉模型（企微轮询我们），与自建应用的推模型根本不同。

## 流式状态存储

复用 dispatcher 的流式循环（``_process_one_streaming`` 周期性调用
``send_initial_response``/``send_streaming_update``/``replace_with_response`` 并
传入累积 ``full_text``），把这些方法的语义从「推送到企微」改为「写入模块级流式
状态存储」。企微的刷新回调从该存储读取并同步返回。dispatcher 零改动。

``stream_id`` 传递：parse 阶段生成 ``stream_id`` 并记 ``_active[chat_id]``；
``send_initial_response`` 从 ``_active[chat_id]`` 取回作为 sentinel 返回，后续
``send_streaming_update``/``replace_with_response`` 据此写存储。

## PoC 范围

仅实现文本 + 流式 + markdown（含表格）。附件（image/voice/mixed/file/video）、
模板卡片、userid 加密场景留后续。机器人由企业超管创建 → ``from.userid`` 明文，
直接复用 ``profile_resolver`` / ``im_user_bindings``。
"""
import base64
from pathlib import Path
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse

from app.media_resolver import (
    find_local_image_matches,
    normalize_path,
    resolve_file_share_url,
    resolve_image_to_data_url,
)
from app.messages import ENGINE_STARTING, PROFILE_PREPARING
from app.redis_client import get_redis
from .base import BaseChannelAdapter
from .card_utils import extract_card_json
from .models import MessageEvent, MessageType
from .registry import register
from .wecom_crypto import (
    decrypt_media,
    decrypt_message,
    encrypt_message,
    sha1_signature,
)

logger = logging.getLogger(__name__)

# 企微智能机器人 stream.content 上限 20480 字节（UTF-8）。
_STREAM_MAX_BYTES = 20480
# 流式状态存储 TTL（秒）—— > 企微 6 分钟刷新窗口，到期清理防泄漏。
_STREAM_TTL = 420.0
# 思考过程 <think> 标签内文本上限（字符）—— 限定展开高度，避免无限长思考撑满屏幕。
_THINKING_MAX_CHARS = 1500
# 工具卡片摘要上限（字符）—— 限定 template_card 高度。
_TOOL_SUMMARY_MAX_CHARS = 600

# transient 处理中提示：经 send_message 投递但**不应结束流**（后续会被真实流式内容覆盖）。
# 终态错误（未绑定/引擎失败/空响应等）经 send_message 投递时则应标记 done 让企微停止轮询。
_TRANSIENT_NOTICES = frozenset({ENGINE_STARTING, PROFILE_PREPARING})

# 裸图片路径（agent 常以文本提及 /root/xxx.png 或 output/xxx.png，非 ![]() 语法）。
# 出站时解析为 stream.msg_item base64 图片（仅 finish 帧支持，最多 10 张）。
_BARE_IMAGE_PATH_RE = re.compile(
    r"(?<![\w./-])(/?(?:root/|output/)?[\w.-]+\.(?:png|jpg|jpeg))", re.IGNORECASE
)
# 非图片文件扩展名（agent 生成的报告/数据文件，企微不支持附件→发下载链接）。
_FILE_EXTS = (".pdf", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt",
              ".txt", ".md", ".json", ".zip", ".tar", ".gz", ".ipynb", ".py", ".html")


def _normalize_robot_path(path: str) -> str:
    """智能机器人图片路径归一：剥 ``/root/`` 前缀（工作区根）后复用 media_resolver.normalize_path。"""
    p = path.strip()
    if p.startswith("/root/"):
        p = p[len("/root/"):]
    return normalize_path(p)


@dataclass
class _StreamState:
    """单条流式消息的累积状态，由 dispatcher 流式循环写、刷新回调读。"""

    accumulated: str = ""
    done: bool = False
    created_at: float = 0.0
    # 思考过程累积（reasoning_content），刷新时包 <think> 标签前置到 content（企微原生折叠）。
    thinking: str = ""
    # 工具调用摘要累积，finish 时一次性发 template_card（stream_with_template_card，紧凑限高）。
    tool_summary: str = ""
    card_sent: bool = False
    # response_url（主动回复用）：finish 时若有 agent 卡片，用此 URL 单独 POST 卡片（standalone，
    # 渲染可靠），不依赖 stream_with_template_card（企微对该组合渲染不稳定）。
    response_url: str = ""


# stream_id → 状态。模块级单例（adapter 每次 dispatch 新建实例，存储须跨实例共享）。
_streams: dict[str, _StreamState] = {}
# chat_id → 当前 stream_id（send_initial_response 据此恢复 stream_id）。
_active: dict[str, str] = {}
# chat_id → response_url（send_message 兜底主动回复，一次性）。
_response_urls: dict[str, str] = {}


def _prune_streams(now: float) -> None:
    """清理过期流式状态（内存模式；Redis 靠 TTL 自动过期）。"""
    expired = [sid for sid, s in _streams.items() if now - s.created_at > _STREAM_TTL]
    for sid in expired:
        _streams.pop(sid, None)


# ── 流式状态存储（Redis 共享 / 内存降级）─────────────────────────────────
# 多副本下 push/refresh 可能命中不同 pod，状态须跨副本共享。
# UA_REDIS_URL 配置时走 Redis（HASH/STRING + TTL），否则降级内存（单副本/本地冒烟/单测）。
_STREAM_KEY = "wbc:stream:{sid}"
_ACTIVE_KEY = "wbc:active:{chat_id}"
_RURL_KEY = "wbc:rurl:{chat_id}"


def _stream_fields(state: _StreamState) -> dict:
    return {
        "accumulated": state.accumulated,
        "done": "1" if state.done else "0",
        "thinking": state.thinking,
        "tool_summary": state.tool_summary,
        "card_sent": "1" if state.card_sent else "0",
        "created_at": str(state.created_at),
        "response_url": state.response_url,
    }


async def _store_create(sid: str, chat_id: str, response_url: str) -> None:
    r = await get_redis()
    if r is None:
        _prune_streams(time.time())
        _streams[sid] = _StreamState(created_at=time.time(), response_url=response_url)
        _active[chat_id] = sid
        if response_url:
            _response_urls[chat_id] = response_url
        return
    ttl = int(_STREAM_TTL)
    init_state = _StreamState(created_at=time.time(), response_url=response_url)
    pipe = r.pipeline()
    pipe.hset(_STREAM_KEY.format(sid=sid), mapping=_stream_fields(init_state))
    pipe.expire(_STREAM_KEY.format(sid=sid), ttl)
    pipe.set(_ACTIVE_KEY.format(chat_id=chat_id), sid, ex=ttl)
    if response_url:
        pipe.set(_RURL_KEY.format(chat_id=chat_id), response_url, ex=ttl)
    await pipe.execute()


async def _store_get(sid: str) -> _StreamState | None:
    """读流式状态快照（refresh 回调用，跨 pod 一致）。"""
    r = await get_redis()
    if r is None:
        return _streams.get(sid)
    data = await r.hgetall(_STREAM_KEY.format(sid=sid))
    if not data:
        return None
    return _StreamState(
        accumulated=data.get("accumulated", ""),
        done=data.get("done") == "1",
        thinking=data.get("thinking", ""),
        tool_summary=data.get("tool_summary", ""),
        card_sent=data.get("card_sent") == "1",
        created_at=float(data.get("created_at") or 0),
        response_url=data.get("response_url", ""),
    )


async def _store_get_active_sid(chat_id: str) -> str:
    r = await get_redis()
    if r is None:
        return _active.get(chat_id, "")
    return (await r.get(_ACTIVE_KEY.format(chat_id=chat_id))) or ""


async def _store_set_accumulated(sid: str, text: str) -> None:
    r = await get_redis()
    if r is None:
        if sid in _streams:
            _streams[sid].accumulated = text
        return
    key = _STREAM_KEY.format(sid=sid)
    pipe = r.pipeline()
    pipe.hset(key, "accumulated", text)
    pipe.expire(key, int(_STREAM_TTL))
    await pipe.execute()


async def _store_finish(sid: str, text: str) -> None:
    r = await get_redis()
    if r is None:
        if sid in _streams:
            _streams[sid].accumulated = text
            _streams[sid].done = True
        return
    key = _STREAM_KEY.format(sid=sid)
    pipe = r.pipeline()
    pipe.hset(key, mapping={"accumulated": text, "done": "1"})
    pipe.expire(key, int(_STREAM_TTL))
    await pipe.execute()


async def _store_append(sid: str, field: str, text: str, max_chars: int) -> str | None:
    """原子追加到 stream hash 的某字段（thinking/tool_summary），cap 到 max_chars。
    返回追加后的完整值（供首次写入日志判断），或 None（流不存在/已满）。"""
    r = await get_redis()
    if r is None:
        s = _streams.get(sid)
        if not s:
            return None
        cur = getattr(s, "thinking" if field == "thinking" else "tool_summary")
        if len(cur) >= max_chars:
            return cur
        cur += text
        if len(cur) > max_chars:
            cur = cur[:max_chars] + "..."
        setattr(s, "thinking" if field == "thinking" else "tool_summary", cur)
        return cur
    key = _STREAM_KEY.format(sid=sid)
    cur = await r.hget(key, field) or ""
    if len(cur) >= max_chars:
        return cur
    cur += text
    if len(cur) > max_chars:
        cur = cur[:max_chars] + "..."
    pipe = r.pipeline()
    pipe.hset(key, field, cur)
    pipe.expire(key, int(_STREAM_TTL))
    await pipe.execute()
    return cur


async def _store_mark_card_sent(sid: str) -> None:
    r = await get_redis()
    if r is None:
        if sid in _streams:
            _streams[sid].card_sent = True
        return
    await r.hset(_STREAM_KEY.format(sid=sid), "card_sent", "1")


async def _store_pop_response_url(chat_id: str) -> str:
    """一次性消费 response_url（GETDEL）。"""
    r = await get_redis()
    if r is None:
        return _response_urls.pop(chat_id, None) or ""
    return (await r.getdel(_RURL_KEY.format(chat_id=chat_id))) or ""


def _truncate_bytes(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断，不切断多字节字符中段。"""
    if not text:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # 按字节截到 max_bytes，再解码（errors="ignore" 丢弃末尾不完整字符）
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


# 群聊 @机器人 前缀剥离：content 形如 "@RobotA hello" → "hello"
_MENTION_PREFIX_RE = re.compile(r"^@\S+\s+")


@register("wecom_bot_callback")
class WeComBotCallbackAdapter(BaseChannelAdapter):
    """企业微信智能机器人（URL 回调）适配器。"""

    channel_type = "wecom_bot_callback"
    # stream.content 上限 20480 字节（仅用于文档，实际截断在刷新返回时按字节处理）。
    max_text_length = 20480

    def __init__(self, config: dict):
        super().__init__(config)
        self.token = config.get("token", "")
        self.encoding_aes_key = config.get("encoding_aes_key", "")
        self.bot_id = config.get("bot_id", "")  # 可选，用于校验回调 aibotid

    # ── Security ────────────────────────────────────────────────────────

    async def verify_signature(self, request: Request) -> bool:
        """校验 POST 回调签名（与自建应用同算法，信封是 JSON）。"""
        msg_signature = request.query_params.get("msg_signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        try:
            body = await request.body()
            encrypt = (json.loads(body).get("encrypt", "")) if body else ""
        except (json.JSONDecodeError, ValueError):
            return False
        expected = sha1_signature(self.token, timestamp, nonce, encrypt)
        return expected == msg_signature

    # ── URL verification (GET) ──────────────────────────────────────────

    async def verify_url(self, request: Request) -> Response:
        """GET echostr 解密返回明文（与自建应用同流程，receiveid 忽略）。"""
        msg_signature = request.query_params.get("msg_signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        echostr = request.query_params.get("echostr", "")

        expected = sha1_signature(self.token, timestamp, nonce, echostr)
        if expected != msg_signature:
            return Response(content="signature verification failed", status_code=403)
        try:
            plain = decrypt_message(self.encoding_aes_key, echostr).decode("utf-8")
            return Response(content=plain, media_type="text/plain")
        except Exception as e:
            logger.warning("wecom_bot_callback verify_url decrypt failed: %s", e)
            return Response(content="decryption failed", status_code=403)

    # ── Inbound attachment download ─────────────────────────────────────

    async def fetch_attachment_bytes(self, event: MessageEvent) -> bytes:
        """下载智能机器人加密 URL 媒体 → AES 解密 → 返回文件字节。

        与自建应用 ``media_id + media/get API`` 不同：机器人回调里媒体是加密 URL，
        HTTP GET 拿到密文后用回调同款 AESKey 解密（IV=AESKey[:16]，PKCS7）。
        """
        url = (event.raw_message or {}).get("url", "")
        if not url:
            return b""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(
                        "wecom_bot_callback media download failed (%s): %s",
                        resp.status_code,
                        resp.text[:120],
                    )
                    return b""
                ciphertext = resp.content
        except Exception as e:
            logger.warning("wecom_bot_callback media download error: %s", e)
            return b""
        if not ciphertext:
            return b""
        plaintext = decrypt_media(self.encoding_aes_key, ciphertext)
        if not plaintext:
            logger.warning("wecom_bot_callback media decrypt empty: user=%s", event.user_id[:12])
        return plaintext

    async def transcribe(self, event: MessageEvent) -> str:
        """语音转录：机器人自带 ``voice.content`` 转写文本，直接返回（无需 ASR）。"""
        return (event.raw_message or {}).get("voice_content", "")

    async def on_reasoning(self, event: MessageEvent, text: str) -> None:
        """累积思考过程到流式状态，刷新时包 ``<think>`` 标签前置（企微原生折叠）。

        长度 cap 到 ``_THINKING_MAX_CHARS``——限定展开高度，避免无限长思考撑满屏幕。
        """
        if not text:
            return
        sid = await _store_get_active_sid(event.chat_id)
        if not sid:
            return
        result = await _store_append(sid, "thinking", text, _THINKING_MAX_CHARS)
        if result is not None and len(result) <= len(text):  # 首次写入
            logger.info("wecom_bot_callback on_reasoning: stream=%s (thinking accumulating)", sid[:12])

    async def on_tool_progress(self, event: MessageEvent, tool_info: dict) -> None:
        """累积工具调用摘要到流式状态，finish 时一次性发 template_card（紧凑限高）。

        ``tool_info`` 来源：
        - Hermes ``hermes.tool.progress``：``{tool, label, toolCallId, status}``，
          每 tool 有 running + completed 两事件，仅在 running 时加一行（避免噪声）。
        - OpenAI ``delta.tool_calls``：``{function_name, arguments_delta, ...}``，
          仅在 ``function_name`` 出现时加一行（首个 chunk），arguments delta 跳过。
        """
        sid = await _store_get_active_sid(event.chat_id)
        if not sid:
            return
        # 先读快照判断是否已满（避免无谓追加）
        state = await _store_get(sid)
        if not state or len(state.tool_summary) >= _TOOL_SUMMARY_MAX_CHARS:
            return
        tool = tool_info.get("tool") or tool_info.get("function_name") or ""
        label = tool_info.get("label") or ""
        status = tool_info.get("status") or ""
        # Hermes：仅 running 加行（completed 跳过避免重复）
        if status == "completed":
            return
        # OpenAI delta.tool_calls：仅 function_name 首现加行，arguments delta 跳过
        if not status and not tool_info.get("function_name"):
            return
        if not tool:
            return
        # label 截断（terminal 命令等可能很长，只显示首行 + 限长，避免折叠块里塞整段命令）
        short_label = ""
        if label:
            first_line = label.splitlines()[0].strip() if label.strip() else ""
            short_label = (first_line[:50] + "...") if len(first_line) > 50 else first_line
        line = f"🔧 {tool}" + (f" — {short_label}" if short_label else "")
        sep = "" if not state.tool_summary else "\n"
        await _store_append(sid, "tool_summary", sep + line, _TOOL_SUMMARY_MAX_CHARS)
        logger.info("wecom_bot_callback on_tool_progress: %s %s — stream=%s", tool, status, sid[:12])

    # ── Parsing ─────────────────────────────────────────────────────────

    async def parse_incoming(self, request: Request) -> list[MessageEvent]:
        """解密 JSON 信封 → 解析 msgtype。

        - ``text``：生成 stream_id、初始化流式状态，返回待 dispatch 事件。
        - ``stream``：流式刷新回调，返回 sentinel 事件（``stream_refresh=True``），
          由 ``handle_callback`` 内联处理，**不进 dispatcher**。
        - ``image``/``file``/``video``：加密 URL 附件，走 dispatcher 附件链路下载解密。
        - ``voice``：自带转写 ``voice.content``，``transcribe`` 直接返回，无需 ASR。
        - ``mixed``（图文混排）：PoC 仅取文本，图片留后续。
        """
        body = await request.body()
        try:
            encrypt = json.loads(body).get("encrypt", "") if body else ""
            plaintext = decrypt_message(self.encoding_aes_key, encrypt).decode("utf-8")
            data = json.loads(plaintext)
        except Exception as e:
            logger.warning("wecom_bot_callback parse_incoming decrypt/parse failed: %s", e)
            return []

        msgtype = data.get("msgtype", "")
        userid = (data.get("from") or {}).get("userid", "")
        msgid = data.get("msgid", "")
        chattype = data.get("chattype", "single")
        aibotid = data.get("aibotid", "")

        # 流式刷新回调：不 dispatch，handle_callback 内联读存储返回累积内容
        if msgtype == "stream":
            sid = (data.get("stream") or {}).get("id", "")
            return [
                MessageEvent(
                    text="",
                    chat_id=userid,
                    user_id=userid,
                    channel_type="wecom_bot_callback",
                    platform_message_id=msgid,
                    raw_message={
                        "stream_refresh": True,
                        "stream_id": sid,
                        "aibotid": aibotid,
                    },
                )
            ]

        # 流式刷新以外的回调才带 response_url（消息推送场景）
        response_url = data.get("response_url", "")

        # 模板卡片按钮点击（msgtype=event, event.eventtype=template_card_event）：
        # 智能机器人卡片事件回调 5s 超时，无法等异步引擎响应做原地 update_template_card。
        # 务实方案：合成文本事件（event_key）走流式回复，用户得到流式回复消息。
        # 原地 update_template_card 待后续同步调用方案（见 update_template_card 方法）。
        if msgtype == "event":
            evt = data.get("event") or {}
            if evt.get("eventtype") == "template_card_event":
                tc = evt.get("template_card_event") or {}
                event_key = tc.get("event_key", "")
                task_id = tc.get("task_id", "")
                if not event_key:
                    return [self._noop_event(userid, msgid, aibotid, "empty_card_click")]
                click_text = f"【企微卡片按钮点击】key={event_key}"
                return [await self._build_text_event(
                    click_text, chattype, userid, msgid, response_url, aibotid
                )]
            return [self._noop_event(userid, msgid, aibotid, f"event_{evt.get('eventtype','')}")]

        # 语音：自带转写，transcribe 直接返回 voice.content（无需 ASR/下载）
        if msgtype == "voice":
            voice_content = ((data.get("voice") or {}).get("content") or "").strip()
            stream_id = await self._init_stream(userid, response_url)
            return [
                MessageEvent(
                    text="",
                    message_type=MessageType.VOICE,
                    chat_id=userid,
                    user_id=userid,
                    channel_type="wecom_bot_callback",
                    platform_message_id=msgid,
                    raw_message={
                        "stream_id": stream_id,
                        "voice_content": voice_content,
                        "response_url": response_url,
                        "aibotid": aibotid,
                        "chattype": chattype,
                    },
                )
            ]

        # 图片/文件/视频：加密 URL 附件，走 dispatcher 附件链路
        if msgtype in ("image", "file", "video"):
            url = (data.get(msgtype) or {}).get("url", "")
            stream_id = await self._init_stream(userid, response_url)
            if not url:
                return [self._noop_event(userid, msgid, aibotid, "no_url")]
            mtype = MessageType.IMAGE if msgtype == "image" else MessageType.FILE
            return [
                MessageEvent(
                    text="",
                    message_type=mtype,
                    chat_id=userid,
                    user_id=userid,
                    channel_type="wecom_bot_callback",
                    platform_message_id=msgid,
                    raw_message={
                        "stream_id": stream_id,
                        "url": url,
                        "msg_type": msgtype,
                        "response_url": response_url,
                        "aibotid": aibotid,
                        "chattype": chattype,
                    },
                )
            ]

        # 图文混排：PoC 仅取文本，图片留后续
        if msgtype == "mixed":
            items = (data.get("mixed") or {}).get("msg_item") or []
            text_parts = [
                (i.get("text") or {}).get("content", "")
                for i in items
                if i.get("msgtype") == "text"
            ]
            content = "\n".join(p for p in text_parts if p).strip()
            if not content:
                return [self._noop_event(userid, msgid, aibotid, "empty_mixed")]
            # 复用文本路径
            return [await self._build_text_event(
                content, chattype, userid, msgid, response_url, aibotid
            )]

        if msgtype != "text":
            logger.info("wecom_bot_callback unsupported msgtype=%s (deferred)", msgtype)
            return [self._noop_event(userid, msgid, aibotid, msgtype)]

        content = (data.get("text") or {}).get("content", "").strip()
        # 群聊剥离 @机器人 前缀
        if chattype == "group":
            content = _MENTION_PREFIX_RE.sub("", content, count=1).strip()
        if not content:
            return [self._noop_event(userid, msgid, aibotid, "empty_text")]
        return [await self._build_text_event(
            content, chattype, userid, msgid, response_url, aibotid
        )]

    async def _init_stream(self, userid: str, response_url: str) -> str:
        """初始化流式状态（stream_id + store create）。"""
        stream_id = uuid.uuid4().hex
        await _store_create(stream_id, userid, response_url)
        return stream_id

    async def _build_text_event(
        self, content, chattype, userid, msgid, response_url, aibotid
    ) -> MessageEvent:
        stream_id = await self._init_stream(userid, response_url)
        return MessageEvent(
            text=content,
            chat_id=userid,
            user_id=userid,
            channel_type="wecom_bot_callback",
            platform_message_id=msgid,
            raw_message={
                "stream_id": stream_id,
                "response_url": response_url,
                "aibotid": aibotid,
                "chattype": chattype,
            },
        )

    @staticmethod
    def _noop_event(userid, msgid, aibotid, msgtype) -> MessageEvent:
        return MessageEvent(
            text="",
            chat_id=userid,
            user_id=userid,
            channel_type="wecom_bot_callback",
            platform_message_id=msgid,
            raw_message={"noop": True, "msgtype": msgtype, "aibotid": aibotid},
        )

    # ── Synchronous callback handling (passive reply) ──────────────────

    async def handle_callback(self, request: Request, events: list[MessageEvent], dispatch):
        """同步被动回复：消息推送返回 stream 首帧；刷新回调返回累积内容。"""
        nonce = request.query_params.get("nonce", "")
        if not events:
            return JSONResponse({"status": "accepted"})

        first = events[0]
        rm = first.raw_message or {}

        # 流式刷新回调：读存储返回累积内容，不 dispatch
        if rm.get("stream_refresh"):
            return await self._stream_refresh_response(
                first.agent_id, rm.get("stream_id", ""), nonce
            )

        # noop（不支持的消息类型 / 空文本）：200 ack，不 dispatch
        if rm.get("noop"):
            return JSONResponse({"status": "accepted"})

        # 消息推送：入队后台处理，同步返回 stream 首帧（content 空，finish=false）
        for event in events:
            await dispatch(event)
        stream_id = rm.get("stream_id", "")
        return self._encrypt_response(
            {"msgtype": "stream", "stream": {"id": stream_id, "finish": False, "content": ""}},
            nonce,
        )

    async def _stream_refresh_response(self, agent_id: str, stream_id: str, nonce: str) -> JSONResponse:
        """流式刷新：返回当前累积内容（累积全文，非增量）。

        内容结构：``<think>工具调用 + 思考过程</think>\\n正式回复``。
        工具调用与思考过程放进 ``<think>`` 折叠块前置（企微客户端原生折叠、不可点击、
        cap 长度限展开高度），正式回复文本在后——对齐 web 端「先工具/思考卡，再文本」的体验。
        finish 帧可附图片 ``msg_item``（base64，仅 finish 支持）。
        """
        state = await _store_get(stream_id)
        if state is None:
            # 未知/过期流：返回 finish=true 让企微停止轮询
            logger.warning("wecom_bot_callback stream refresh: unknown stream_id=%s", stream_id[:12])
            return self._encrypt_response(
                {"msgtype": "stream", "stream": {"id": stream_id, "finish": True, "content": ""}},
                nonce,
            )
        # 构造 <think> 折叠块：工具调用在前，思考过程在后
        think_parts: list[str] = []
        if state.tool_summary:
            think_parts.append(f"🔧 工具调用\n{state.tool_summary}")
        if state.thinking:
            think_parts.append(state.thinking)
        content = state.accumulated
        # 卡片保护：流式过程中（未 finish）若累积内容疑似卡片 JSON，缓冲不显示原始 JSON
        # （agent 边生成边流式会暴露 {"msgtype":... 半截文本，企微不会用 finish 帧覆盖）。
        # finish 时由下方 extract_card_json 提取卡片渲染，原始 JSON 永不显示给用户。
        if not state.done and content and (
            content.lstrip().startswith("{") or '"msgtype"' in content
        ):
            content = ""
        # 提取 agent 回复里的 template_card JSON（如试驾报告卡片）—— 智能机器人用
        # stream_with_template_card 渲染成真卡片，不把原始 JSON 当文本显示。
        agent_card: dict | None = None
        card_obj, card_before, card_after = extract_card_json(content, required_key="msgtype")
        if (
            isinstance(card_obj, dict)
            and card_obj.get("msgtype") == "template_card"
            and isinstance(card_obj.get("template_card"), dict)
        ):
            agent_card = card_obj["template_card"]
            content = (card_before + "\n" + card_after).strip()
            logger.info(
                "wecom_bot_callback card extracted: stripped=%r content_after=%r",
                bool(card_obj), content[:200],
            )
        # 剥掉 MEDIA:<path> 标记（agent 的图片/文件标记，已由 msg_item/下载链接单独投递，
        # 不在正文显示原始 MEDIA: 文本）
        content = re.sub(r"MEDIA:\S+", "", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if think_parts:
            content = "<think>" + "\n\n".join(think_parts) + "</think>\n" + content
        content = _truncate_bytes(content, _STREAM_MAX_BYTES)
        stream_block = {"id": stream_id, "finish": state.done, "content": content}
        # finish 帧：附 template_card（agent 卡片优先，否则工具摘要）+ 图片 msg_item + 文件链接
        if state.done and not state.card_sent:
            msg_items = await self._resolve_images(agent_id, state.accumulated) if agent_id else []
            file_links = await self._resolve_file_links(agent_id, state.accumulated) if agent_id else []
            if file_links:
                stream_block["content"] = _truncate_bytes(
                    content + "\n\n" + "\n".join(file_links), _STREAM_MAX_BYTES
                )
                logger.info(
                    "wecom_bot_callback finish frame: %d file link(s) stream=%s",
                    len(file_links), stream_id[:12],
                )
            if msg_items:
                stream_block["msg_item"] = msg_items
                logger.info(
                    "wecom_bot_callback finish frame: %d image(s) stream=%s",
                    len(msg_items), stream_id[:12],
                )
            # template_card：agent 卡片优先用 response_url 单独 POST（standalone，渲染可靠）。
            # stream_with_template_card 组合企微渲染不稳定（button_interaction 经常不显示）。
            # 无 response_url 时 fallback 到 stream_with_template_card。
            if agent_card:
                if state.response_url:
                    # 用 response_url 单独发卡片（standalone template_card）
                    await self._post_card_via_response_url(state.response_url, agent_card)
                    logger.info(
                        "wecom_bot_callback finish frame: card via response_url (%s) stream=%s",
                        agent_card.get("card_type", "?"), stream_id[:12],
                    )
                    payload = {"msgtype": "stream", "stream": stream_block}
                else:
                    logger.info(
                        "wecom_bot_callback finish frame: card via stream_with_template_card (%s) stream=%s",
                        agent_card.get("card_type", "?"), stream_id[:12],
                    )
                    payload = {
                        "msgtype": "stream_with_template_card",
                        "stream": stream_block,
                        "template_card": {**agent_card, "task_id": "gw_" + uuid.uuid4().hex[:12]},
                    }
            else:
                payload = {"msgtype": "stream", "stream": stream_block}
            state.card_sent = True
            await _store_mark_card_sent(stream_id)
            return self._encrypt_response(payload, nonce)
        return self._encrypt_response({"msgtype": "stream", "stream": stream_block}, nonce)

    async def _resolve_images(self, agent_id: str, content: str) -> list[dict]:
        """从回复内容解析图片引用 → stream.msg_item 列表（base64+md5）。

        检测两种引用：
        - markdown 图片 ``![alt](path)``
        - 裸图片路径（agent 常以文本提及 ``/root/xxx.png`` 或 ``output/xxx.png``）
        经 manager ``/files/content`` 解析为 base64（企微 stream.msg_item 仅 finish 帧支持，
        最多 10 张，JPG/PNG）。解析失败的跳过。
        """
        if not agent_id or not content:
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for m in find_local_image_matches(content):
            p = m.group(2).strip()
            if p and p not in seen:
                paths.append(p)
                seen.add(p)
        for m in _BARE_IMAGE_PATH_RE.finditer(content):
            p = m.group(1).strip()
            if p and p not in seen:
                paths.append(p)
                seen.add(p)
        # agent 的 MEDIA:<path> 图片标记约定（如 MEDIA:/tmp/xxx.png）
        for m in re.finditer(r"MEDIA:(\S+\.(?:png|jpg|jpeg))", content, re.IGNORECASE):
            p = m.group(1).strip()
            if p and p not in seen:
                paths.append(p)
                seen.add(p)
        logger.info(
            "wecom_bot_callback _resolve_images: agent=%s detected_paths=%s content_tail=%r",
            agent_id[:8], paths, content[-120:],
        )
        msg_items: list[dict] = []
        for p in paths[:10]:
            # /tmp/ 路径原样传（resolve_image_to_data_url 路由到 /files/media）；
            # 其它路径剥 /root/ 前缀后归一（走 /files/content 工作区）
            # 绝对路径（/tmp/、/opt/data/profiles/、/root/）原样传（走 /files/media 直读）；
            # 相对路径归一（走 /files/content 按 profile 解析）
            np = p if p.startswith(("/tmp/", "/opt/data/profiles/", "/root/")) else _normalize_robot_path(p)
            data_url = await resolve_image_to_data_url(agent_id, np)
            if not data_url or ";base64," not in data_url:
                logger.info("wecom_bot_callback image resolve failed: %s -> %s", p, np)
                continue
            try:
                b64 = data_url.split(";base64,", 1)[1]
                md5 = hashlib.md5(base64.b64decode(b64)).hexdigest()
            except Exception as e:
                logger.warning("wecom_bot_callback image decode failed: %s", e)
                continue
            msg_items.append({"msgtype": "image", "image": {"base64": b64, "md5": md5}})
        return msg_items

    async def _resolve_file_links(self, agent_id: str, content: str) -> list[str]:
        """检测文件引用（非图片扩展名）→ manager 签名分享链接 → markdown 下载行。

        企微智能机器人不支持文件附件，只能发下载 URL。检测 ``MEDIA:<path>`` + 裸文件路径
        （.pdf/.csv/.pptx 等），跳过图片（由 _resolve_images 处理），返回 markdown 链接行。
        """
        if not agent_id or not content:
            return []
        file_paths: list[str] = []
        seen: set[str] = set()
        # MEDIA:<path>（agent 文件标记约定，含任意扩展名）
        for m in re.finditer(r"MEDIA:(\S+)", content):
            p = m.group(1).strip()
            if p and p not in seen:
                file_paths.append(p)
                seen.add(p)
        # 裸文件路径（/tmp/xxx.pdf、output/xxx.csv）
        for m in re.finditer(
            r"(?<![\w./-])(/?(?:root/|output/|tmp/)?[\w./-]+\.(?:pdf|csv|xlsx|xls|docx|doc|pptx|ppt|txt|md|json|zip|tar|gz|ipynb))",
            content, re.IGNORECASE,
        ):
            p = m.group(1).strip()
            if p and p not in seen:
                file_paths.append(p)
                seen.add(p)
        links: list[str] = []
        for p in file_paths[:10]:
            if p.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg")):
                continue  # 图片由 _resolve_images 处理
            share_path = p if p.startswith("/tmp/") else _normalize_robot_path(p)
            result = await resolve_file_share_url(agent_id, share_path)
            if not result or not result[0]:
                logger.info("wecom_bot_callback file share-link failed: %s -> %s", p, share_path)
                continue
            url, filename = result
            links.append(f"📥 [{filename or Path(p).name}]({url})")
        return links

    def update_template_card(
        self, chat_id: str, task_id: str, response_code: str, template_card: dict
    ) -> dict:
        """构造原地更新 template_card 的被动回复 payload（智能机器人无 response_code）。

        智能机器人卡片事件回调 5s 超时，dispatcher 异步引擎响应无法在 5s 内返回原地更新。
        本方法为构建块——当前卡片点击走流式回复（合成文本事件），原地 update 留后续
        同步调用方案。返回 ``{response_type:update_template_card, userids, template_card}``
        （task_id 须与回调一致；userids 仅群聊有效，单聊留空由企微处理）。
        """
        return {
            "response_type": "update_template_card",
            "userids": [chat_id] if chat_id else [],
            "template_card": template_card,
        }

    def _encrypt_response(self, payload: dict, nonce: str) -> JSONResponse:
        """构造被动回复加密 envelope（receiveid="", nonce 复用请求 nonce）。"""
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        encrypt = encrypt_message(self.encoding_aes_key, "", plaintext)
        timestamp = str(int(time.time()))
        msgsignature = sha1_signature(self.token, timestamp, nonce, encrypt)
        return JSONResponse(
            {"encrypt": encrypt, "msgsignature": msgsignature, "timestamp": timestamp, "nonce": nonce}
        )

    async def _post_card_via_response_url(self, response_url: str, template_card: dict) -> None:
        """用 response_url 主动回复一条 standalone template_card（渲染可靠）。

        stream_with_template_card 组合企微渲染不稳定（button_interaction 经常不显示）。
        response_url 是企微回调里的临时 URL（1h 有效，一次性），POST 明文 JSON 即可。
        """
        # 确保 task_id 唯一（button_interaction 等交互卡片必填，空/重复报 errcode 42014）
        template_card = {**template_card, "task_id": "gw_" + uuid.uuid4().hex[:12]}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    response_url,
                    json={"msgtype": "template_card", "template_card": template_card},
                )
                logger.info(
                    "wecom_bot_callback response_url card POST: status=%s body=%s",
                    resp.status_code, resp.text[:300],
                )
                if resp.status_code != 200:
                    logger.warning(
                        "wecom_bot_callback response_url card failed (%s): %s",
                        resp.status_code, resp.text[:200],
                    )
        except Exception as e:
            logger.warning("wecom_bot_callback response_url card error: %s", e)

    # ── Streaming store writes (called by dispatcher streaming loop) ───

    @property
    def supports_streaming(self) -> bool:
        return True

    async def send_processing(self, chat_id: str) -> str | None:
        """无独立推送通道——stream bubble 本身即占位，不发额外消息。"""
        return None

    async def send_processing_done(self, chat_id: str, processing_msg_id: str) -> bool:
        """无启动卡可更新，跳过。"""
        return True

    async def send_initial_response(self, chat_id: str, text: str) -> str | None:
        """首个 chunk：从 store 取 active stream_id，写累积，返回 stream_id 作 sentinel。"""
        sid = await _store_get_active_sid(chat_id)
        if sid:
            await _store_set_accumulated(sid, text)
        return sid or chat_id

    async def send_streaming_update(
        self, chat_id: str, message_id: str, accumulated_text: str, show_status: bool = False
    ) -> bool:
        """后续 chunk：用 sentinel（stream_id）定位存储，覆盖累积全文。"""
        if message_id:
            await _store_set_accumulated(message_id, accumulated_text)
        return True

    async def replace_with_response(
        self, chat_id: str, message_id: str, response: str
    ) -> bool:
        """流式结束：写最终全文并标记 done，刷新回调据此返回 finish=true。"""
        sid = message_id
        if not sid or await _store_get(sid) is None:
            # 兜底：sentinel 不是 stream_id——按 chat_id 找 active
            sid = await _store_get_active_sid(chat_id)
        if sid:
            await _store_finish(sid, response)
        return True

    # ── One-shot send (error / fallback paths) ─────────────────────────

    async def send_message(self, chat_id: str, text: str, reply_to: str = "") -> bool:
        """非流式兜底：优先写入当前流式存储（由刷新回调投递）；否则用 response_url 主动回复。

        dispatcher 的错误/拒绝路径（权限闸门、引擎启动失败、空流等）调用此方法——
        这些是**终态**，写存储后标记 done 让企微停止轮询。
        但 PROFILE_PREPARING / ENGINE_STARTING 是 transient 处理中提示（后续会被真实
        流式内容覆盖），不应标记 done，否则企微看到 finish=true 提前停止轮询，
        真实内容来不及写入存储。成功路径走流式存储，不经过这里。
        """
        sid = await _store_get_active_sid(chat_id)
        if sid and await _store_get(sid) is not None:
            # 活跃流存在：写入（终态标 done，transient 不标）
            if text not in _TRANSIENT_NOTICES:
                await _store_finish(sid, text)
            else:
                await _store_set_accumulated(sid, text)
            return True

        # response_url 主动回复（明文 JSON，一次性）
        url = await _store_pop_response_url(chat_id)
        if url:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        url,
                        json={"msgtype": "markdown", "markdown": {"content": text}},
                    )
                    return resp.status_code == 200
            except Exception as e:
                logger.warning("wecom_bot_callback response_url reply failed: %s", e)
                return False

        logger.warning("wecom_bot_callback send_message: no stream/response_url for %s", chat_id[:12])
        return False
