"""企业微信 (WeCom) channel adapter — HTTP callback mode.

Uses the standard WeCom callback protocol:
- GET /callback?msg_signature=...&echostr=... → URL verification handshake
- POST /callback → Encrypted XML message body
- Reply: POST /cgi-bin/message/send?access_token=... with Markdown/Text

References hermes-agent gateway/platforms/wecom_callback.py architecture.

## 卡片消息（透传）

Profile 回复为带 `msgtype` 的 JSON 字符串时按卡片透传（template_card /
textcard / news / mpnews / markdown / image）。Gateway 只识别 `msgtype`、补
`touser`/`agentid` 后透传，不做字段映射。详见 `send_reply` / `_parse_card`。

## 交互卡片（button_interaction）

`button_interaction` 卡片可带按钮，用户点按钮后企微回调 `template_card_event`
事件，adapter 解析出 (TaskId, EventKey, ResponseCode) 放入 `raw_message.card_event`，
dispatcher 路由到 Profile → `update_template_card` 就地更新原卡片。
约定 Profile 回复带 `_update_task_id` 的 JSON 表示"更新原卡片"。
"""

import base64
import logging
import re
import time
import uuid
from xml.etree import ElementTree as ET

import httpx
from fastapi import Request, Response

from app.asr import AsrError, get_asr_provider
from app.media_resolver import (
    find_local_file_links,
    find_local_image_matches,
    normalize_path,
    resolve_file_bytes,
    resolve_image_to_data_url,
)
from app.messages import CARD_RENDER_FAILED, ENGINE_STARTING, MESSAGE_FORMAT_INVALID
from app.settings import settings

from .base import BaseChannelAdapter
from .card_utils import extract_card_json
from .models import MessageEvent, MessageType
from .registry import register
from .wecom_crypto import (
    decrypt_message as _decrypt_message,
    encrypt_message as _encrypt_message,
    pkcs7_decode as _pkcs7_decode,
    sha1_signature as _sha1_signature,
)

logger = logging.getLogger(__name__)

ACCESS_TOKEN_TTL = 7200

# 企微 media/upload 官方大小上限：图片 2MB、文件 20MB。出站前预检，避免上传失败后才报错。
WECOM_IMAGE_MAX_BYTES = 2 * 1024 * 1024
WECOM_FILE_MAX_BYTES = 20 * 1024 * 1024
WECOM_ABSOLUTE_MAX_BYTES = 20 * 1024 * 1024


def _fmt_mb(size: int) -> str:
    return f"{size / (1024 * 1024):.2f}"


def _apply_outbound_size_limit(size: int, kind: str) -> tuple[str, str | None]:
    """出站媒体大小预检 + 自动降级。返回 ``(final_type, notice)``。

    ``final_type`` ∈ ``{"image", "file", "rejected"}``；``notice`` 为面向用户的提示
    文本或 ``None``。

    - 图片 >2MB 且 ≤20MB → 降级为 file，附「已转为文件发送」提示
    - 任意 >20MB → rejected，附「超过限制，无法发送」提示

    精简自 Hermes ``_apply_file_size_limits``——当前出站仅支持 image/file 两类 msgtype。
    """
    size_mb = _fmt_mb(size)
    normalized = (kind or "file").lower()
    if size > WECOM_ABSOLUTE_MAX_BYTES:
        return "rejected", f"文件大小 {size_mb}MB 超过 20MB 限制，无法发送"
    if normalized == "image" and size > WECOM_IMAGE_MAX_BYTES:
        return "file", f"图片大小 {size_mb}MB 超过 2MB 限制，已转为文件发送"
    return normalized, None


# 匹配包裹整个文本的 markdown 代码围栏：```lang\n...\n```
_WRAP_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_+-]*\s*\n([\s\S]*?)\n```\s*$")


def _strip_wrapping_code_fence(text: str) -> str:
    """剥掉包裹整个回复的 markdown 代码围栏。

    模型常把整段回复包在 ```` ```markdown\\n...\\n``` ```` 里。围栏行本身不是用户
    想看的内容，企微会把 ```` ``` ```` 当独立文本消息渲染成空白卡片。仅当围栏包裹
    **整个**文本时才剥（首尾锚定），行内/中段的代码块保留不动。
    """
    if not text:
        return text
    m = _WRAP_CODE_FENCE_RE.match(text.strip())
    if m:
        return m.group(1)
    return text


# 企微 API 超时（秒）：部分环境到 qyapi.weixin.qq.com 较慢（gettoken 基线 ~4.5s，
# 偶发 spike >10s），原 10s 超时导致卡片下发/按钮更新偶发"渲染失败"。调到 30s 吸收 spike。
_WECOM_API_TIMEOUT: float = 30.0


# ── Adapter ────────────────────────────────────────────────────────────────


@register("wecom")
class WeComAdapter(BaseChannelAdapter):
    channel_type = "wecom"
    max_text_length = 2048
    # 支持的卡片类 msgtype（Profile 回复为带 msgtype 的 JSON 时按卡片发送，见 _parse_card）
    CARD_MSGTYPES = frozenset({"template_card", "textcard", "news", "mpnews", "markdown", "image"})

    def __init__(self, config: dict):
        super().__init__(config)
        self.token = config.get("token", "")
        self.encoding_aes_key = config.get("encoding_aes_key", "")
        self.corp_id = config.get("corp_id", "")
        self.corp_secret = config.get("secret", "")
        self.agent_id = config.get("agent_id", "")
        self._access_token: str | None = None
        self._token_expires: float = 0
        # 流式 chunk-flush：每个 chat_id 已发送的字符偏移（满 2048 字节 flush 一条新消息）
        self._stream_sent: dict[str, int] = {}

    # ── Security ────────────────────────────────────────────────────────

    async def verify_signature(self, request: Request) -> bool:
        """Verify POST callback signature using SHA1."""
        msg_signature = request.query_params.get("msg_signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")

        body = await request.body()
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return False
        encrypt = root.findtext("Encrypt", default="")

        expected = _sha1_signature(self.token, timestamp, nonce, encrypt)
        return expected == msg_signature

    # ── URL verification (GET) ──────────────────────────────────────────

    async def verify_url(self, request: Request) -> Response:
        """WeCom URL verification — decrypt echostr and return plaintext."""
        msg_signature = request.query_params.get("msg_signature", "")
        timestamp = request.query_params.get("timestamp", "")
        nonce = request.query_params.get("nonce", "")
        echostr = request.query_params.get("echostr", "")

        # Verify signature first
        expected = _sha1_signature(self.token, timestamp, nonce, echostr)
        if expected != msg_signature:
            return Response(content="signature verification failed", status_code=403)

        # Decrypt echostr
        try:
            plain = _decrypt_message(self.encoding_aes_key, echostr).decode("utf-8")
            return Response(content=plain, media_type="text/plain")
        except Exception as e:
            logger.warning("WeCom verify_url decrypt failed: %s", e)
            return Response(content="decryption failed", status_code=403)

    # ── Parsing ─────────────────────────────────────────────────────────

    async def parse_incoming(self, request: Request) -> list[MessageEvent]:
        """Parse WeCom encrypted XML callback into MessageEvent.

        处理两类消息：
        - ``text``：普通文本消息，走标准 dispatcher 流程。
        - ``event`` + ``template_card_event`` + ``button_interaction``：卡片按钮点击，
          解析 (TaskId, EventKey, ResponseCode) 放入 ``raw_message.card_event``，
          dispatcher 检测到后走 ``_process_card_event`` 专用流程（路由到 Profile →
          ``update_template_card`` 就地更新原卡片）。其他 event（如 ``enter_agent``）
          忽略，返回空列表。
        """
        body = await request.body()

        # Decrypt
        try:
            root = ET.fromstring(body)
            encrypt = root.findtext("Encrypt", default="")
            xml_text = _decrypt_message(self.encoding_aes_key, encrypt).decode("utf-8")
        except Exception as e:
            logger.warning("WeCom decrypt failed: %s", e)
            return []

        # Parse decrypted XML
        try:
            msg_root = ET.fromstring(xml_text)
        except ET.ParseError:
            return []

        msg_type = (msg_root.findtext("MsgType") or "").lower()
        user_id = msg_root.findtext("FromUserName", default="")
        msg_id = msg_root.findtext("MsgId", default="")

        # 语音：产出 VOICE event（text 留空，由 dispatcher 调 transcribe 转录）
        if msg_type == "voice":
            media_id = msg_root.findtext("MediaId", default="")
            if not media_id:
                return []
            return [
                MessageEvent(
                    text="",
                    message_type=MessageType.VOICE,
                    chat_id=user_id,
                    user_id=user_id,
                    channel_type="wecom",
                    platform_message_id=msg_id,
                    raw_message={"xml": xml_text, "media_id": media_id},
                )
            ]

        # 卡片按钮点击：event/template_card_event/button_interaction → 合成消息送引擎，
        # 带 task_id/response_code 供回复时 update_template_card 就地更新原卡片。
        if msg_type == "event":
            event_name = (msg_root.findtext("Event") or "").lower()
            if event_name == "template_card_event":
                card_type = (msg_root.findtext("CardType") or "").lower()
                task_id = msg_root.findtext("TaskId", default="") or ""
                key = msg_root.findtext("EventKey", default="") or ""
                response_code = msg_root.findtext("ResponseCode", default="") or ""
                if card_type == "button_interaction" and task_id and key:
                    logger.info(
                        "Card button click: user=%s task_id=%s key=%s", user_id, task_id, key
                    )
                    return [
                        MessageEvent(
                            text=f"【企微卡片按钮点击】task_id={task_id} key={key}",
                            chat_id=user_id,
                            user_id=user_id,
                            channel_type="wecom",
                            platform_message_id=f"card_{task_id}_{key}",
                            raw_message={
                                "xml": xml_text,
                                "card_click": True,
                                "task_id": task_id,
                                "event_key": key,
                                "response_code": response_code,
                            },
                        )
                    ]
            elif event_name == "click":
                # 自建应用底部菜单点击：EventKey 作为用户消息送引擎（admin 配菜单时
                # 把 key 设为希望送达智能体的内容/指令）
                key = msg_root.findtext("EventKey", default="") or ""
                if key:
                    logger.info("Menu click: user=%s key=%s", user_id, key)
                    return [
                        MessageEvent(
                            text=key,
                            chat_id=user_id,
                            user_id=user_id,
                            channel_type="wecom",
                            platform_message_id=msg_id,
                            raw_message={"xml": xml_text, "menu_click": True, "event_key": key},
                        )
                    ]
            logger.debug("WeCom ignoring event: %s", event_name)
            return []

        # 图片/视频：产出附件 event（text 留空，由 dispatcher 下载企微媒体 → 写工作区
        # → 转 [Attached files: path] 文本送引擎）。媒体字节不在回调报文里，只有 media_id，
        # 3 天内有效，dispatcher 调 _media_get 拉取。image 企微额外给 PicUrl（仅图片有）。
        #
        # ⚠️ 企微回调消息类型约束（官方文档）：自建应用「接收消息」只下发
        #   text / image / voice / video / location / link —— **file（文件消息）不下发**。
        #   用户在企微发文件不会触发 MsgType=file 回调。这里保留 file 分支仅为防御性
        #   （防企微后续支持 / 复用给其他 IM 通道），企微实际走不到。用户要传文件需走
        #   web 门户上传或发图片代替。
        if msg_type in ("image", "file", "video"):
            media_id = msg_root.findtext("MediaId", default="")
            logger.info(
                "WeCom attachment msg: user=%s msg_type=%s media_id=%s file_name=%s",
                user_id[:12],
                msg_type,
                media_id[:16],
                msg_root.findtext("FileName", default=""),
            )
            if not media_id:
                return []
            raw: dict = {"xml": xml_text, "media_id": media_id, "msg_type": msg_type}
            if msg_type == "image":
                raw["pic_url"] = msg_root.findtext("PicUrl", default="")
            else:
                raw["file_name"] = msg_root.findtext("FileName", default="")
            mtype = MessageType.IMAGE if msg_type == "image" else MessageType.FILE
            return [
                MessageEvent(
                    text="",
                    message_type=mtype,
                    chat_id=user_id,
                    user_id=user_id,
                    channel_type="wecom",
                    platform_message_id=msg_id,
                    raw_message=raw,
                )
            ]

        if msg_type not in ("text",):
            logger.debug("WeCom ignoring msg_type=%s", msg_type)
            return []

        content = msg_root.findtext("Content", default="").strip()
        if not content:
            return []

        event = MessageEvent(
            text=content,
            chat_id=user_id,
            user_id=user_id,
            channel_type="wecom",
            platform_message_id=msg_id,
            raw_message={"xml": xml_text},
        )
        return [event]

    # ── Send messages ───────────────────────────────────────────────────

    async def _ensure_token(self) -> str | None:
        """Get or refresh WeCom access token."""
        now = time.time()
        if self._access_token and self._token_expires > now + 60:
            return self._access_token

        try:
            async with httpx.AsyncClient(timeout=_WECOM_API_TIMEOUT) as client:
                resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={"corpid": self.corp_id, "corpsecret": self.corp_secret},
                )
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.error("WeCom gettoken failed: %s", data)
                    return None
                self._access_token = data["access_token"]
                self._token_expires = now + int(data.get("expires_in", ACCESS_TOKEN_TTL))
                return self._access_token
        except Exception as e:
            logger.error("WeCom gettoken error: %s", e)
            return None

    async def fetch_attachment_bytes(self, event) -> bytes:
        """入站附件下载（dispatcher 通用入口）：自建应用按 media_id 走 media/get API。"""
        media_id = (event.raw_message or {}).get("media_id", "")
        if not media_id:
            return b""
        return await self._media_get(media_id)

    async def _media_get(self, media_id: str) -> bytes:
        """下载企微临时媒体（语音 amr），返回字节。失败返回空。媒体在企微存 3 天。"""
        token = await self._ensure_token()
        if not token:
            return b""
        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/media/get"
            f"?access_token={token}&media_id={media_id}"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                # 失败时企微返回 JSON（errcode）；成功返回二进制音频
                if "application/json" in resp.headers.get("content-type", ""):
                    err = resp.json()
                    logger.warning("WeCom media/get failed for %s: %s", media_id, err.get("errmsg"))
                    return b""
                return resp.content
        except Exception as e:
            logger.error("WeCom media/get error: %s", e)
            return b""

    async def _media_upload(self, content: bytes, filename: str, media_type: str = "image") -> str:
        """上传临时素材到企微（media/upload），返回 media_id（3 天有效）。

        用于出站图片：引擎回复引用工作区图片时，需先把图片字节上传企微拿 media_id，
        再用 image msgtype 发送（企微不能渲染 markdown 里的本地图片路径）。
        """
        token = await self._ensure_token()
        if not token:
            return ""
        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/media/upload"
            f"?access_token={token}&type={media_type}"
        )
        try:
            async with httpx.AsyncClient(timeout=_WECOM_API_TIMEOUT) as client:
                resp = await client.post(url, files={"media": (filename, content)})
                data = resp.json()
                if data.get("errcode", 0) != 0:
                    logger.warning("WeCom media/upload failed: %s", data)
                    return ""
                return data.get("media_id", "")
        except Exception as e:
            logger.error("WeCom media/upload error: %s", e)
            return ""

    async def _send_image(self, chat_id: str, image_path: str, alt: str = "") -> bool:
        """把工作区图片解析为字节 → 上传企微 → 发 image msgtype。

        用 media_resolver.resolve_image_to_data_url（internal token 调 manager
        /files/content）拿 base64，解码后上传。失败返回 False（调用方降级发占位文本）。

        大小预检：图片 >2MB 自动降级为 file msgtype 并附用户提示（复用已解析字节，
        不重新下载）；>20MB 直接拒绝并发提示，返回 True（已发提示，消费掉此次引用，
        避免调用方再发 ``[alt]`` 占位）。
        """
        if not self.ua_agent_id:
            return False
        data_url = await resolve_image_to_data_url(self.ua_agent_id, image_path)
        if not data_url or ";base64," not in data_url:
            return False
        try:
            raw = base64.b64decode(data_url.split(";base64,", 1)[1])
        except Exception:
            return False
        fname = image_path.rsplit("/", 1)[-1] or "image.png"
        final_type, notice = _apply_outbound_size_limit(len(raw), "image")
        if final_type == "rejected":
            if notice:
                await self._send_one(chat_id, notice)
            return True
        if notice:
            await self._send_one(chat_id, notice)
        media_type = "file" if final_type == "file" else "image"
        media_id = await self._media_upload(raw, fname, media_type=media_type)
        if not media_id:
            return False
        result = await self.send_card_message(chat_id, media_type, {"media_id": media_id})
        return isinstance(result, dict) and result.get("errcode") == 0

    async def _send_file(self, chat_id: str, file_path: str, name: str) -> bool:
        """把工作区文件下载字节 → 上传企微(type=file) → 发 file msgtype。

        引擎回复含 ``[name](output/report.pptx)`` 文件链接时，企微无法渲染下载链接，
        需把文件完整字节经 manager /files/download 取出 → media/upload(type=file) 拿
        media_id → file msgtype 发送（用户在企微里可点击下载）。失败返回 False，调用方
        降级发 ``[name]`` 占位。文件上限 20MB（对齐企微 media/upload file 限制），
        超限发用户提示并返回 True（消费掉此次引用）。
        """
        if not self.ua_agent_id:
            return False
        result = await resolve_file_bytes(self.ua_agent_id, file_path)
        if not result:
            return False
        content, fname = result
        if len(content) > WECOM_FILE_MAX_BYTES:
            await self._send_one(
                chat_id, f"文件大小 {_fmt_mb(len(content))}MB 超过 20MB 限制，无法发送"
            )
            return True
        media_id = await self._media_upload(content, fname or name, media_type="file")
        if not media_id:
            return False
        res = await self.send_card_message(chat_id, "file", {"media_id": media_id})
        return isinstance(res, dict) and res.get("errcode") == 0

    async def _send_text_with_media(self, chat_id: str, text: str) -> bool:
        """发含工作区媒体引用的文本：按引用切分，逐段发（文本 + image/file msgtype）。

        引擎回复常含两种工作区引用：
        - 图片 ``![alt](output/chart.png)`` → image msgtype
        - 文件 ``[name](output/report.pptx)`` → file msgtype
        企微无法渲染 markdown 本地路径/下载链接，需把每个媒体解析成字节上传企微发对应
        msgtype，前后文本仍走 markdown。媒体发送失败降级发 ``[name]`` 占位。无媒体引用
        时走原 _split_by_bytes 文本分段。

        合并图片与文件两类 match，按 start 排序后 span 切分（文件链接 regex 已用 (?<!!)
        排除图片 ![]()，二者不重叠）。用 span 而非 find/rfind 重定位，避免错位。
        """
        media: list[tuple[int, int, str, str, str]] = []  # (start, end, kind, label, path)
        for m in find_local_image_matches(text):
            media.append((m.start(), m.end(), "image", m.group(1), m.group(2).strip()))
        for m in find_local_file_links(text):
            media.append((m.start(), m.end(), "file", m.group(1), m.group(2).strip()))
        media.sort(key=lambda x: x[0])

        if not media:
            ok = True
            for chunk in self._split_by_bytes(text, self.max_text_length):
                if not await self._send_one(chat_id, chunk):
                    ok = False
            return ok

        ok_all = True
        cursor = 0
        for start, end, kind, label, path in media:
            if start < cursor:
                continue  # 防御重叠
            before = text[cursor:start]
            if before.strip():
                for chunk in self._split_by_bytes(before, self.max_text_length):
                    if not await self._send_one(chat_id, chunk):
                        ok_all = False
            sent = await (
                self._send_image(chat_id, normalize_path(path), label)
                if kind == "image"
                else self._send_file(chat_id, normalize_path(path), label)
            )
            if not sent:
                if label:
                    if not await self._send_one(chat_id, f"[{label}]"):
                        ok_all = False
                else:
                    ok_all = False
            cursor = end
        tail = text[cursor:]
        if tail.strip():
            for chunk in self._split_by_bytes(tail, self.max_text_length):
                if not await self._send_one(chat_id, chunk):
                    ok_all = False
        return ok_all

    async def transcribe(self, event: MessageEvent) -> str:
        """voice event → 文字：media_get(amr) → ASR provider。

        provider 由 UA_ASR_PROVIDER 选择（volcengine 外部 / local 旧 sidecar /
        aliyun / tencent / huawei）。未配置或调用失败返回 ""，由 dispatcher 回兜底
        提示。详见 services/gateway/docs/wecom(callback)支持语音方案设计.md。
        """
        provider = get_asr_provider()
        if not provider:
            logger.warning("ASR provider not configured, voice transcription skipped")
            return ""
        media_id = event.raw_message.get("media_id", "")
        audio = await self._media_get(media_id) if media_id else b""
        if not audio:
            return ""
        try:
            text = await provider.transcribe(audio, fmt="amr")
            return text.strip()
        except AsrError as e:
            logger.error("ASR %s error: %s", provider.name, e)
            return ""
        except Exception as e:
            logger.error("ASR %s unexpected error: %s", provider.name, e)
            return ""

    async def create_menu(self, menu: dict) -> dict:
        """创建自建应用底部菜单（menu/create）。

        menu 为企微 menu/create 文档规定的菜单 JSON（button 列表）。
        agentid 由本适配器持有，调用方只需传 menu body。返回企微响应 dict。
        """
        token = await self._ensure_token()
        if not token:
            return {"errcode": -1, "errmsg": "no access token"}
        params = {"access_token": token}
        if self.agent_id:
            params["agentid"] = int(self.agent_id)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://qyapi.weixin.qq.com/cgi-bin/menu/create",
                    params=params,
                    json=menu,
                )
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.warning("WeCom create_menu failed: %s", data)
                return data
        except Exception as e:
            logger.error("WeCom create_menu error: %s", e)
            return {"errcode": -1, "errmsg": str(e)}

    async def send_card_click_reply(self, event: MessageEvent, response: str) -> bool:
        """按钮点击的引擎回复：含 _update_task_id → 就地 update_template_card；否则发新消息。"""
        rm = event.raw_message or {}
        upd = self._parse_card_update(response)
        if upd:
            upd_task_id, card_body = upd
            return await self.update_template_card(
                event.chat_id,
                upd_task_id,
                rm.get("response_code", ""),
                card_body,
            )
        # 不是更新指令 → 发新消息（卡片或文本，复用 send_message 的卡片探测）
        return await self.send_message(event.chat_id, response)

    async def _send_card_or_text(self, chat_id: str, text: str) -> bool:
        """统一发送：从 ``text`` 中**逐个**提取卡片 JSON（容忍前后说明文字/围栏）。

        按原文顺序循环：前导文本（markdown 分段）→ 卡片 → 后续文本 → 下一个卡片 → …
        一条回复含多个卡片时每个都正确渲染，卡片之间的文字也照常发。

        - 合法卡片：``send_card_message`` 透传；errcode≠0 或异常 → 降级"消息渲染失败"。
        - 有 ``msgtype`` 但不支持/残缺 → "消息格式异常"（不把原始 JSON 糊给用户）。
        - 无卡片 → 整段 markdown 分段发送。
        """
        # 剥掉包裹整个回复的 markdown 代码围栏（模型常把回复包在 ```markdown ... ``` 里）。
        # 否则围栏行 ```markdown / ``` 会被当成独立文本消息发给企微，渲染成空白卡片。
        remaining = _strip_wrapping_code_fence(text)
        ok_all = True
        while True:
            obj, before, after = extract_card_json(remaining, required_key="msgtype")
            if obj is None:
                # 无更多卡片：剩余文本作为 markdown 发送（含工作区图片引用时发 image msgtype）
                if remaining.strip():
                    if not await self._send_text_with_media(chat_id, remaining):
                        ok_all = False
                return ok_all
            # 先发该卡片之前的前导文本（含工作区图片引用时发 image msgtype）
            if before.strip():
                if not await self._send_text_with_media(chat_id, before):
                    ok_all = False
            msgtype = obj.get("msgtype")
            body = obj.get(msgtype)
            if msgtype in self.CARD_MSGTYPES and isinstance(body, dict):
                try:
                    result = await self.send_card_message(chat_id, msgtype, body)
                    if not (isinstance(result, dict) and result.get("errcode") == 0):
                        logger.warning(
                            "WeCom card send failed: %s, fallback to text",
                            result,
                        )
                        if not await self._send_one(chat_id, CARD_RENDER_FAILED):
                            ok_all = False
                except Exception as e:
                    logger.error("WeCom card send error, fallback: %s", e)
                    if not await self._send_one(chat_id, CARD_RENDER_FAILED):
                        ok_all = False
            else:
                logger.warning(
                    "Malformed/unsupported card content: %s",
                    (text or "")[:200],
                )
                if not await self._send_one(chat_id, MESSAGE_FORMAT_INVALID):
                    ok_all = False
            remaining = after

    async def send_message(self, chat_id: str, text: str, reply_to: str = "") -> bool:
        """发送消息：提取卡片 JSON（容忍前后文字）→ 前导文本 + 卡片，否则 markdown 分段。

        卡片发送失败/格式异常时降级文本，避免用户无响应或看到原始 JSON。
        详见 ``_send_card_or_text``。
        """
        return await self._send_card_or_text(chat_id, text)

    async def send_markdown(self, chat_id: str, markdown: str) -> bool:
        """Send markdown message (分段 + markdown 类型，与 send_message 统一)."""
        return await self.send_message(chat_id, markdown)

    async def _send_one(self, chat_id: str, content: str) -> bool:
        """Send a single (already ≤2048-byte) markdown message."""
        token = await self._ensure_token()
        if not token:
            return False

        payload = {
            "touser": chat_id,
            "msgtype": "markdown",
            "agentid": int(self.agent_id) if self.agent_id else 0,
            "markdown": {"content": content},
        }
        try:
            async with httpx.AsyncClient(timeout=_WECOM_API_TIMEOUT) as client:
                resp = await client.post(
                    f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                    json=payload,
                )
                data = resp.json()
                errcode = data.get("errcode", -1)
                if errcode == 0:
                    return True
                if errcode in (40001, 42001):
                    # Token expired — force refresh next time
                    self._access_token = None
                logger.warning("WeCom send_message failed: %s", data)
                return False
        except Exception as e:
            logger.error("WeCom send_message error: %s", e)
            return False

    @staticmethod
    def _split_by_bytes(text: str, max_bytes: int = 2048) -> list[str]:
        """按 UTF-8 字节切分成每段 ≤ max_bytes 的列表，永不切断多字节字符中段。

        优先在换行处切；单行超 max_bytes 时按字符兜底切。空文本返回 []。
        """
        if not text:
            return []
        chunks: list[str] = []
        cur = ""
        cur_bytes = 0

        def flush():
            nonlocal cur, cur_bytes
            if cur:
                chunks.append(cur)
                cur = ""
                cur_bytes = 0

        for line in text.splitlines(keepends=True):
            line_bytes = len(line.encode("utf-8"))
            if line_bytes > max_bytes:
                # 当前行本身超限：先 flush 已累积的，再按字符切该行
                flush()
                for ch in line:
                    ch_bytes = len(ch.encode("utf-8"))
                    if cur_bytes + ch_bytes > max_bytes:
                        flush()
                    cur += ch
                    cur_bytes += ch_bytes
            elif cur_bytes + line_bytes > max_bytes:
                # 加入本行会超限：先 flush，再起一段
                flush()
                cur = line
                cur_bytes = line_bytes
            else:
                cur += line
                cur_bytes += line_bytes
        flush()
        return chunks

    # ── UX: Processing indicator ────────────────────────────────────────

    async def send_processing(self, chat_id: str) -> str | None:
        """Send a 'processing…' placeholder message.

        WeCom does NOT support editing messages, so we return the message ID
        for logging but cannot update it later.
        """
        ok = await self.send_message(chat_id, ENGINE_STARTING)
        return chat_id if ok else None

    async def replace_with_response(
        self,
        chat_id: str,
        processing_msg_id: str,
        response: str,
    ) -> bool:
        """WeCom cannot edit messages — send as a new message instead.

        流式结束时调用：取未 chunk-flush 的剩余文本 ``response[sent:]``，统一按
        ``_send_card_or_text`` 发送——卡片整体下发（不被 chunk-flush 切），前导文本与
        卡片共存。已 chunk-flush 过的部分不再发。
        """
        sent = self._stream_sent.pop(chat_id, 0)
        remaining = response[sent:]
        if not remaining:
            return True
        return await self._send_card_or_text(chat_id, remaining)

    # ── 流式 chunk-flush：企微不能编辑消息，改为满 2048 字节就发一条新消息 ──

    @property
    def supports_streaming(self) -> bool:
        return True

    async def send_processing_done(self, chat_id: str, processing_msg_id: str) -> bool:
        """企微不能编辑启动卡，跳过 ✅ 更新（避免多余消息；内容到达即代表就绪）。"""
        return True

    async def send_initial_response(self, chat_id: str, text: str) -> str | None:
        """流式首个 chunk：仅缓冲，不立即发（满 2048 字节才发）。返回 sentinel。"""
        self._stream_sent[chat_id] = 0
        return chat_id

    async def send_streaming_update(
        self,
        chat_id: str,
        message_id: str,
        accumulated_text: str,
        show_status: bool = False,
    ) -> bool:
        """满 2048 字节就 flush 一条新消息（完整段），剩余 <2048 留到下次/最终。

        accumulated_text 是引擎至今累积的全文；按 _stream_sent 偏移取 delta，
        flush 其中完整的 2048 字节段，尾部留待最终 replace_with_response。

        卡片保护：若 accumulated_text 疑似卡片 JSON（lstrip 后以 ``{`` 开头，或已出现
        ``"msgtype"`` 卡片签名——即便前有说明文字），不 chunk-flush（避免把半截 JSON
        当 markdown 发出），缓冲到 replace_with_response 整体判卡片。

        图片保护：若未发送的 delta 含 markdown 图片引用 ``![``，不 chunk-flush（避免把
        ``![alt](output/x.png)`` 当纯文本发出），缓冲到 replace_with_response 由
        _send_text_with_media 解析成 image msgtype 发送。
        """
        # 卡片保护：疑似 JSON → 缓冲，不 flush
        if accumulated_text.lstrip().startswith("{") or '"msgtype"' in accumulated_text:
            return True
        sent = self._stream_sent.get(chat_id, 0)
        delta = accumulated_text[sent:]
        # 图片保护：delta 含未发送的图片引用 → 缓冲，留给最终 replace_with_response 处理
        if "![" in delta:
            return True
        chunks = self._split_by_bytes(delta, self.max_text_length)
        while len(chunks) > 1:
            # chunks[:-1] 均为完整 2048 字节段，逐条发送；最后一段可能 <2048 留待下次
            await self._send_one(chat_id, chunks[0])
            sent += len(chunks[0])
            delta = accumulated_text[sent:]
            chunks = self._split_by_bytes(delta, self.max_text_length)
        self._stream_sent[chat_id] = sent
        return True

    # ── 卡片消息（透传 + 交互按钮）─────────────────────────────────────

    async def send_card_message(self, chat_id: str, msgtype: str, body: dict) -> dict:
        """发送卡片类消息（template_card / textcard / news / markdown / image 等）。

        body 为该 msgtype 对应的内容对象（与企微 message/send 文档一致），
        本方法只补 touser / agentid / msgtype 外壳后透传，不做字段映射。

        template_card 的 task_id 须全局唯一（企微 30 天内不允许重复，否则
        errcode 42014）。agent 生成的可能重复（如照抄示例数字）→ gateway 用
        "gw_"+uuid 覆盖；点击回传此 id，agent 在更新回复里回显即可。
        """
        token = await self._ensure_token()
        if not token:
            return {"errcode": -1, "errmsg": "no access token"}

        # 防企微 42014：button_interaction 卡片 task_id 必填且须全局唯一。
        # agent 可能丢字段（不透传 validate stdout）→ gateway 一律为 button_interaction
        # 注入唯一 task_id（不管 agent 有没有带）；text_notice 等不需要 task_id 的卡片不动。
        if (
            msgtype == "template_card"
            and isinstance(body, dict)
            and body.get("card_type") == "button_interaction"
        ):
            body = {**body, "task_id": "gw_" + uuid.uuid4().hex[:12]}

        # 防企微 42039：button_interaction 的 button_list 每个按钮须有 key。
        # LLM 偶尔把 URL 跳转按钮放 button_list（有 url 无 key）→ 补 key + 删 url
        # （Button 结构体只有 text/style/key，不支持 url）
        if (
            msgtype == "template_card"
            and isinstance(body, dict)
            and body.get("card_type") == "button_interaction"
            and isinstance(body.get("button_list"), list)
        ):
            new_buttons = []
            for btn in body["button_list"]:
                if isinstance(btn, dict):
                    if not btn.get("key"):
                        btn = {**btn, "key": "gw_btn_" + uuid.uuid4().hex[:8]}
                    btn.pop("url", None)
                new_buttons.append(btn)
            body = {**body, "button_list": new_buttons}

        payload = {
            "touser": chat_id,
            "msgtype": msgtype,
            "agentid": int(self.agent_id) if self.agent_id else 0,
            msgtype: body,
        }
        try:
            async with httpx.AsyncClient(timeout=_WECOM_API_TIMEOUT) as client:
                resp = await client.post(
                    f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}",
                    json=payload,
                )
                result = resp.json()
        except Exception as e:
            logger.error("WeCom send_card_message error: %s", e)
            return {"errcode": -1, "errmsg": str(e)}

        if result.get("errcode") != 0:
            logger.warning(
                "WeCom send_card_message failed (%s): %s",
                msgtype,
                result.get("errmsg"),
            )
        return result

    async def update_template_card(
        self,
        chat_id: str,
        task_id: str,
        response_code: str,
        template_card: dict,
    ) -> dict:
        """更新已发送的 template_card（按 task_id 定位），用于按钮点击后改卡片状态。

        button_interaction 的更新必须带点击事件里的 response_code（单次有效）。
        """
        token = await self._ensure_token()
        if not token:
            return {"errcode": -1, "errmsg": "no access token"}

        url = (
            f"https://qyapi.weixin.qq.com/cgi-bin/message/update_template_card?access_token={token}"
        )
        payload = {
            "userids": [chat_id],
            "agentid": int(self.agent_id) if self.agent_id else 0,
            "task_id": task_id,
            "card_type": "button_interaction",
            "response_code": response_code,
            "template_card": template_card,
        }
        try:
            async with httpx.AsyncClient(timeout=_WECOM_API_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
                result = resp.json()
        except Exception as e:
            logger.error("WeCom update_template_card error: %s", e)
            return {"errcode": -1, "errmsg": str(e)}

        if result.get("errcode") != 0:
            logger.warning(
                "WeCom update_template_card failed (task=%s): %s",
                task_id,
                result.get("errmsg"),
            )
        return result

    def _parse_card(self, content: str) -> tuple[str, str | None, dict | None]:
        """解析回复内容，判断是卡片还是纯文本（容忍前后说明文字/代码围栏）。

        约定：Profile 需要发卡片时，回复为企微 message/send 形态的 JSON 字符串，
        如 ``{"msgtype":"template_card","template_card":{...}}``，前后可夹带说明文字。

        返回 (kind, msgtype, body)：
        - ("card", msgtype, body)    支持的卡片，可直接透传 send_card_message
        - ("bad_card", None, None)   带 msgtype 但不支持/残缺，不应把原始 JSON 发给用户
        - ("text", None, None)       纯文本（无 msgtype JSON）
        """
        obj, _, _ = extract_card_json(content, required_key="msgtype")
        if obj is None:
            return "text", None, None
        msgtype = obj.get("msgtype")
        body = obj.get(msgtype)
        if msgtype in self.CARD_MSGTYPES and isinstance(body, dict):
            return "card", msgtype, body
        # 有 msgtype 但不是支持的卡片 / body 残缺 → 不把 JSON 丢给用户
        return "bad_card", None, None

    @staticmethod
    def _parse_card_update(content: str) -> tuple[str, dict] | None:
        """解析回复为卡片更新指令。返回 (task_id, template_card_body) 或 None。

        约定：回复为
        ``{"msgtype":"template_card","template_card":{...},"_update_task_id":"<tid>"}``，
        前后可夹带说明文字/代码围栏（由 extract_card_json 容错提取）。
        """
        obj, _, _ = extract_card_json(content, required_key="_update_task_id")
        if not isinstance(obj, dict):
            return None
        tid = obj.get("_update_task_id")
        body = obj.get("template_card")
        if tid and isinstance(body, dict):
            return tid, body
        return None

    async def send_reply(self, chat_id: str, content: str) -> bool:
        """根据内容发卡片或文本（含前导文本共存）。卡片发送失败/格式异常时降级。

        等同 ``_send_card_or_text``：``card`` JSON → 前导文本 + 卡片，失败降级"渲染失败"；
        ``bad_card`` → "格式异常"；其他 → markdown 文本。
        """
        return await self._send_card_or_text(chat_id, content)
