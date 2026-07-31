"""WeCom 智能机器人 (wecom_bot_callback) channel adapter 单元测试

覆盖:
  - 加解密往返（receiveid="" 场景）
  - JSON 信封签名校验
  - URL 验证 (echostr)
  - parse_incoming: text / stream 刷新 / 不支持类型
  - handle_callback: 消息推送返回加密 stream 首帧；刷新返回累积内容
  - 流式状态存储: send_initial_response / send_streaming_update / replace_with_response
  - content 字节截断
  - _encrypt_response nonce 复用
  - send_message 写活跃流 + response_url 兜底
"""
import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from fastapi import Request

from app.channel.models import MessageType
from app.channel.wecom_crypto import decrypt_message, encrypt_message, sha1_signature

AES_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY"  # 43 chars → 32-byte AES key
TOKEN = "robot-token"
CONFIG = {"token": TOKEN, "encoding_aes_key": AES_KEY, "bot_id": "BOT123"}


def _make_request(body: bytes, query: dict[str, str]) -> Request:
    """构造带 body + query_string 的 mock Request（query 值 URL 编码，模拟企微真实回调）。"""
    qs = "&".join(f"{k}={quote(v, safe='')}" for k, v in query.items()).encode()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/callback",
        "headers": [(b"content-type", b"application/json")],
        "query_string": qs,
    }
    request = Request(scope)

    async def mock_body():
        return body

    request.body = mock_body
    return request


def _encrypt_body(payload: dict) -> bytes:
    """构造智能机器人加密 JSON 信封 {"encrypt": ...}。"""
    plaintext = json.dumps(payload, ensure_ascii=False)
    return json.dumps({"encrypt": encrypt_message(AES_KEY, "", plaintext)}).encode("utf-8")


def _decrypt_envelope(envelope: dict) -> dict:
    """解密被动回复 envelope 的 encrypt 字段。"""
    return json.loads(decrypt_message(AES_KEY, envelope["encrypt"]).decode("utf-8"))


# ── Crypto ──────────────────────────────────────────────────────────────


class TestCrypto:
    def test_roundtrip_empty_receiveid(self):
        """encrypt_message(receiveid='') → decrypt_message 往返还原（智能机器人场景）。"""
        payload = {"msgtype": "stream", "stream": {"id": "sid", "finish": False, "content": "你好"}}
        encrypt = encrypt_message(AES_KEY, "", json.dumps(payload, ensure_ascii=False))
        decrypted = json.loads(decrypt_message(AES_KEY, encrypt).decode("utf-8"))
        assert decrypted == payload

    def test_sha1_signature(self):
        sig = sha1_signature(TOKEN, "1718000000", "nonce1", "encryptdata")
        expected = hashlib.sha1(
            "".join(sorted([TOKEN, "1718000000", "nonce1", "encryptdata"])).encode("utf-8")
        ).hexdigest()
        assert sig == expected


# ── verify_signature / verify_url ───────────────────────────────────────


class TestSignatureAndVerify:
    def test_verify_signature_valid(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        timestamp, nonce = "1718000000", "123456"
        body = _encrypt_body({"msgtype": "text", "text": {"content": "hi"}})
        # 从 body 取 encrypt 计算签名（与 verify_signature 同源）
        encrypt = json.loads(body)["encrypt"]
        sig = sha1_signature(TOKEN, timestamp, nonce, encrypt)
        request = _make_request(body, {"msg_signature": sig, "timestamp": timestamp, "nonce": nonce})
        assert asyncio.run(adapter.verify_signature(request)) is True

    def test_verify_signature_invalid(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        body = _encrypt_body({"msgtype": "text", "text": {"content": "hi"}})
        request = _make_request(
            body, {"msg_signature": "bad", "timestamp": "1718000000", "nonce": "n"}
        )
        assert asyncio.run(adapter.verify_signature(request)) is False

    def test_verify_url_decrypts_echostr(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        echostr_plain = "verify-plaintext-123"
        echostr = encrypt_message(AES_KEY, "", echostr_plain)
        timestamp, nonce = "1718000000", "123456"
        sig = sha1_signature(TOKEN, timestamp, nonce, echostr)
        request = _make_request(
            b"",
            {"msg_signature": sig, "timestamp": timestamp, "nonce": nonce, "echostr": echostr},
        )
        resp = asyncio.run(adapter.verify_url(request))
        assert resp.status_code == 200
        assert resp.body == echostr_plain.encode("utf-8")

    def test_verify_url_bad_signature(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        request = _make_request(
            b"",
            {"msg_signature": "bad", "timestamp": "1", "nonce": "n", "echostr": "x"},
        )
        resp = asyncio.run(adapter.verify_url(request))
        assert resp.status_code == 403


# ── parse_incoming ──────────────────────────────────────────────────────


class TestParseIncoming:
    def setup_method(self):
        from app.channel import wecom_bot_callback

        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()

    def test_parse_text_message(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _active

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MSG1",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_A"},
            "response_url": "https://qyapi.example/aibot/response?response_code=CODE",
            "msgtype": "text",
            "text": {"content": "你好机器人"},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))

        assert len(events) == 1
        ev = events[0]
        assert ev.text == "你好机器人"
        assert ev.user_id == "USER_A"
        assert ev.chat_id == "USER_A"
        assert ev.channel_type == "wecom_bot_callback"
        assert ev.platform_message_id == "MSG1"
        sid = ev.raw_message["stream_id"]
        assert sid in _streams
        assert _active["USER_A"] == sid
        assert ev.raw_message["response_url"].endswith("CODE")

    def test_parse_text_group_strips_mention(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MSG2",
            "aibotid": "BOT123",
            "chatid": "CHAT1",
            "chattype": "group",
            "from": {"userid": "USER_A"},
            "response_url": "https://example/x",
            "msgtype": "text",
            "text": {"content": "@RobotA hello world"},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert events[0].text == "hello world"

    def test_parse_stream_refresh(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MSG3",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_A"},
            "msgtype": "stream",
            "stream": {"id": "STREAMID_X"},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert len(events) == 1
        assert events[0].raw_message["stream_refresh"] is True
        assert events[0].raw_message["stream_id"] == "STREAMID_X"

    def test_parse_unsupported_msgtype_noop(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MSG4",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_A"},
            "response_url": "https://example/x",
            "msgtype": "location",
            "location": {"latitude": 1, "longitude": 2},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert len(events) == 1
        assert events[0].raw_message.get("noop") is True


# ── attachments (image/voice/file/video/mixed) ──────────────────────────


class TestAttachments:
    def setup_method(self):
        from app.channel import wecom_bot_callback

        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()

    def test_decrypt_media_envelope_format(self):
        """媒体密文为信封格式（16B random + msg_len + 文件字节 + receiveid）时正确还原。"""
        import struct
        import base64
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from app.channel.wecom_crypto import pkcs7_decode, decrypt_media

        file_bytes = b"\x89PNG\r\n\x1a\n" + b"fake-png-body" * 10  # 模拟图片
        aes_key = base64.b64decode(AES_KEY + "=")
        raw = bytearray()
        raw.extend(b"\x11" * 16)  # random
        raw.extend(struct.pack(">I", len(file_bytes)))
        raw.extend(file_bytes)
        raw.extend(b"")  # receiveid 空
        block_size = 32
        pad_len = block_size - (len(raw) % block_size)
        raw.extend(bytes([pad_len]) * pad_len)
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
        ciphertext = cipher.encryptor().update(bytes(raw)) + cipher.encryptor().finalize()
        assert decrypt_media(AES_KEY, ciphertext) == file_bytes

    def test_decrypt_media_raw_format(self):
        """媒体密文为纯 PKCS7 填充文件字节（无信封头）时回退正确。"""
        import base64
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from app.channel.wecom_crypto import decrypt_media

        file_bytes = b"plain-file-content-no-header"
        aes_key = base64.b64decode(AES_KEY + "=")
        block_size = 32
        pad_len = block_size - (len(file_bytes) % block_size)
        raw = file_bytes + bytes([pad_len]) * pad_len
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
        ciphertext = cipher.encryptor().update(raw) + cipher.encryptor().finalize()
        assert decrypt_media(AES_KEY, ciphertext) == file_bytes

    def test_transcribe_returns_voice_content(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter
        from app.channel.models import MessageEvent

        adapter = WeComBotCallbackAdapter(CONFIG)
        event = MessageEvent(
            text="",
            message_type=MessageType.VOICE,
            chat_id="U",
            user_id="U",
            channel_type="wecom_bot_callback",
            raw_message={"voice_content": "你好今天天气怎样"},
        )
        assert asyncio.run(adapter.transcribe(event)) == "你好今天天气怎样"

    def test_parse_voice_event(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _active

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MV",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_V"},
            "response_url": "https://example/x",
            "msgtype": "voice",
            "voice": {"content": "语音转写文本"},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert len(events) == 1
        ev = events[0]
        assert ev.message_type == MessageType.VOICE
        assert ev.raw_message["voice_content"] == "语音转写文本"
        assert ev.raw_message["stream_id"] in _active.values()

    def test_parse_image_event(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MI",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_I"},
            "response_url": "https://example/x",
            "msgtype": "image",
            "image": {"url": "https://wecom/encrypted/img"},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert len(events) == 1
        ev = events[0]
        assert ev.message_type == MessageType.IMAGE
        assert ev.raw_message["url"] == "https://wecom/encrypted/img"
        assert ev.raw_message["msg_type"] == "image"

    def test_parse_file_event(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MF",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_F"},
            "response_url": "https://example/x",
            "msgtype": "file",
            "file": {"url": "https://wecom/encrypted/file"},
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert events[0].message_type == MessageType.FILE
        assert events[0].raw_message["url"] == "https://wecom/encrypted/file"

    def test_parse_mixed_text_only(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MM",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_M"},
            "response_url": "https://example/x",
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "看这张图"}},
                    {"msgtype": "image", "image": {"url": "https://wecom/img"}},
                ]
            },
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        # PoC：mixed 仅取文本
        assert events[0].text == "看这张图"
        assert events[0].message_type == MessageType.TEXT

    def test_parse_card_click_synthesizes_text(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MC",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_C"},
            "response_url": "https://example/x",
            "msgtype": "event",
            "event": {
                "eventtype": "template_card_event",
                "template_card_event": {
                    "card_type": "button_interaction",
                    "event_key": "btn_confirm",
                    "task_id": "task_123",
                },
            },
        }
        request = _make_request(_encrypt_body(payload), {})
        events = asyncio.run(adapter.parse_incoming(request))
        assert len(events) == 1
        # 卡片点击合成文本事件（走流式回复）
        assert events[0].message_type == MessageType.TEXT
        assert "btn_confirm" in events[0].text
        assert events[0].raw_message["stream_id"]  # 走流式

    def test_update_template_card_payload(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        result = adapter.update_template_card(
            "USER_C", "task_123", "", {"card_type": "text_notice", "sub_title_text": "已确认"}
        )
        assert result["response_type"] == "update_template_card"
        assert result["userids"] == ["USER_C"]
        assert result["template_card"]["sub_title_text"] == "已确认"

    def test_fetch_attachment_bytes_decrypts(self):
        """fetch_attachment_bytes: httpx GET 密文 → AES 解密 → 文件字节。"""
        import base64
        import struct
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter
        from app.channel.models import MessageEvent

        file_bytes = b"\x89PNG\r\n\x1a\nfake-image-body"
        aes_key = base64.b64decode(AES_KEY + "=")
        # 信封格式加密
        raw = bytearray(b"\x11" * 16)
        raw.extend(struct.pack(">I", len(file_bytes)))
        raw.extend(file_bytes)
        pad_len = 32 - (len(raw) % 32)
        raw.extend(bytes([pad_len]) * pad_len)
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
        ciphertext = cipher.encryptor().update(bytes(raw)) + cipher.encryptor().finalize()

        adapter = WeComBotCallbackAdapter(CONFIG)
        event = MessageEvent(
            text="",
            message_type=MessageType.IMAGE,
            chat_id="U",
            user_id="U",
            channel_type="wecom_bot_callback",
            raw_message={"url": "https://wecom/encrypted/img"},
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = ciphertext
        with patch("app.channel.wecom_bot_callback.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(return_value=mock_resp)
            result = asyncio.run(adapter.fetch_attachment_bytes(event))
        assert result == file_bytes


# ── handle_callback ─────────────────────────────────────────────────────


class TestHandleCallback:
    def setup_method(self):
        from app.channel import wecom_bot_callback

        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()

    def _text_event(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "MSG1",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_A"},
            "response_url": "https://example/x",
            "msgtype": "text",
            "text": {"content": "hi"},
        }
        request = _make_request(_encrypt_body(payload), {"nonce": "NONCE1"})
        events = asyncio.run(adapter.parse_incoming(request))
        return adapter, events, request

    def test_message_push_returns_stream_init_frame(self):
        from app.channel.wecom_bot_callback import _streams

        adapter, events, request = self._text_event()
        dispatch = AsyncMock()
        resp = asyncio.run(adapter.handle_callback(request, events, dispatch))

        # dispatch 被调用（入队后台处理）
        dispatch.assert_awaited_once_with(events[0])
        # 返回加密 envelope，解密后是 stream 首帧
        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert set(body.keys()) == {"encrypt", "msgsignature", "timestamp", "nonce"}
        # nonce 复用请求 nonce
        assert body["nonce"] == "NONCE1"
        # msgsignature 校验
        expected_sig = sha1_signature(TOKEN, body["timestamp"], body["nonce"], body["encrypt"])
        assert body["msgsignature"] == expected_sig
        frame = _decrypt_envelope(body)
        assert frame["msgtype"] == "stream"
        assert frame["stream"]["finish"] is False
        assert frame["stream"]["content"] == ""
        sid = events[0].raw_message["stream_id"]
        assert frame["stream"]["id"] == sid
        assert sid in _streams

    def test_stream_refresh_returns_accumulated(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _StreamState

        adapter = WeComBotCallbackAdapter(CONFIG)
        # 先模拟一条已存在的流（dispatcher 写过累积内容）
        _streams["SID1"] = _StreamState(
            accumulated="部分内容", done=False, created_at=0.0
        )

        refresh_payload = {
            "msgid": "MSG3",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_A"},
            "msgtype": "stream",
            "stream": {"id": "SID1"},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        dispatch = AsyncMock()
        resp = asyncio.run(adapter.handle_callback(request, events, dispatch))

        # 刷新回调不 dispatch
        dispatch.assert_not_awaited()
        frame = _decrypt_envelope(json.loads(resp.body))
        assert frame["stream"]["content"] == "部分内容"
        assert frame["stream"]["finish"] is False

    def test_stream_refresh_finish_when_done(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _StreamState

        adapter = WeComBotCallbackAdapter(CONFIG)
        _streams["SID2"] = _StreamState(
            accumulated="最终内容", done=True, created_at=0.0
        )
        refresh_payload = {
            "msgid": "M",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "U"},
            "msgtype": "stream",
            "stream": {"id": "SID2"},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        assert frame["stream"]["finish"] is True
        assert frame["stream"]["content"] == "最终内容"

    def test_stream_refresh_unknown_stream_finishes(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        refresh_payload = {
            "msgid": "M",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "U"},
            "msgtype": "stream",
            "stream": {"id": "UNKNOWN"},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        assert frame["stream"]["finish"] is True  # 未知流让企微停止轮询

    def test_noop_returns_202(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        payload = {
            "msgid": "M",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "U"},
            "response_url": "https://example/x",
            "msgtype": "location",
            "location": {"latitude": 1, "longitude": 2},
        }
        request = _make_request(_encrypt_body(payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        dispatch = AsyncMock()
        resp = asyncio.run(adapter.handle_callback(request, events, dispatch))
        dispatch.assert_not_awaited()
        assert json.loads(resp.body) == {"status": "accepted"}


# ── streaming store writes ──────────────────────────────────────────────


class TestStreamingStore:
    def setup_method(self):
        from app.channel import wecom_bot_callback

        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()

    def test_streaming_writes_accumulate_to_store(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _active

        adapter = WeComBotCallbackAdapter(CONFIG)
        # 模拟 parse 阶段为 USER_A 建流
        from app.channel.wecom_bot_callback import _StreamState

        sid = "SID_STREAM"
        _streams[sid] = _StreamState(accumulated="", created_at=0.0)
        _active["USER_A"] = sid

        # send_initial_response 返回 stream_id sentinel 并写首帧
        sentinel = asyncio.run(adapter.send_initial_response("USER_A", "hello"))
        assert sentinel == sid
        assert _streams[sid].accumulated == "hello"
        assert _streams[sid].done is False

        # send_streaming_update 覆盖累积全文
        asyncio.run(adapter.send_streaming_update("USER_A", sentinel, "hello world"))
        assert _streams[sid].accumulated == "hello world"

        # replace_with_response 写最终 + done
        asyncio.run(adapter.replace_with_response("USER_A", sentinel, "hello world final"))
        assert _streams[sid].accumulated == "hello world final"
        assert _streams[sid].done is True

    def test_send_message_writes_active_stream_and_done(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _active, _StreamState

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid = "SID_ERR"
        _streams[sid] = _StreamState(accumulated="", created_at=0.0)
        _active["USER_A"] = sid

        ok = asyncio.run(adapter.send_message("USER_A", "出错了"))
        assert ok is True
        assert _streams[sid].accumulated == "出错了"
        assert _streams[sid].done is True

    def test_send_message_transient_notice_does_not_finish(self):
        """PROFILE_PREPARING 等 transient 提示不应标记 done（否则企微提前停止轮询，真实流式内容来不及写入）。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _active, _StreamState
        from app.messages import PROFILE_PREPARING

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid = "SID_PREP"
        _streams[sid] = _StreamState(accumulated="", created_at=0.0)
        _active["USER_A"] = sid

        ok = asyncio.run(adapter.send_message("USER_A", PROFILE_PREPARING))
        assert ok is True
        assert _streams[sid].accumulated == PROFILE_PREPARING
        assert _streams[sid].done is False  # transient：不结束流

    def test_send_message_falls_back_to_response_url(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _response_urls

        adapter = WeComBotCallbackAdapter(CONFIG)
        _response_urls["USER_B"] = "https://qyapi.example/aibot/response?response_code=C"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("app.channel.wecom_bot_callback.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            ok = asyncio.run(adapter.send_message("USER_B", "fallback"))
        assert ok is True
        # response_url 一次性，用后即删
        assert "USER_B" not in _response_urls


# ── thinking / tool cards ───────────────────────────────────────────────


class TestCards:
    def setup_method(self):
        from app.channel import wecom_bot_callback

        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()

    def _seed_stream(self, userid="USER_A"):
        from app.channel.wecom_bot_callback import _streams, _active, _StreamState

        sid = "SID_CARD"
        _streams[sid] = _StreamState(accumulated="", created_at=0.0)
        _active[userid] = sid
        return sid, _streams[sid]

    def _event(self, userid="USER_A"):
        from app.channel.models import MessageEvent

        return MessageEvent(
            text="", chat_id=userid, user_id=userid, channel_type="wecom_bot_callback"
        )

    def test_on_reasoning_accumulates_and_caps(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _THINKING_MAX_CHARS

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        # 累积多段
        asyncio.run(adapter.on_reasoning(self._event(), "第一段思考"))
        asyncio.run(adapter.on_reasoning(self._event(), "第二段思考"))
        assert state.thinking == "第一段思考第二段思考"
        # cap：超长截断 + ...
        long_text = "X" * (_THINKING_MAX_CHARS + 100)
        state.thinking = ""
        asyncio.run(adapter.on_reasoning(self._event(), long_text))
        assert state.thinking.endswith("...")
        assert len(state.thinking) == _THINKING_MAX_CHARS + 3

    def test_on_tool_progress_hermes_running_adds_line(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        asyncio.run(
            adapter.on_tool_progress(
                self._event(),
                {"tool": "skill_view", "label": "客户档案", "status": "running"},
            )
        )
        assert "skill_view" in state.tool_summary
        assert "客户档案" in state.tool_summary
        # completed 不再加行（避免噪声）
        asyncio.run(
            adapter.on_tool_progress(
                self._event(),
                {"tool": "skill_view", "label": "客户档案", "status": "completed"},
            )
        )
        assert state.tool_summary.count("skill_view") == 1

    def test_on_tool_progress_openai_function_name_only(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        # 首个 chunk 带 function_name → 加行
        asyncio.run(
            adapter.on_tool_progress(
                self._event(), {"function_name": "get_weather", "arguments_delta": "{"}
            )
        )
        assert "get_weather" in state.tool_summary
        before = state.tool_summary
        # 后续 arguments delta（无 function_name）→ 跳过
        asyncio.run(
            adapter.on_tool_progress(self._event(), {"arguments_delta": '"loc":"sh"}'})
        )
        assert state.tool_summary == before

    def test_refresh_prepends_think_tag(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        state.accumulated = "正式回复"
        state.thinking = "思考过程"
        refresh_payload = {
            "msgid": "M",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "USER_A"},
            "msgtype": "stream",
            "stream": {"id": sid},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        assert frame["stream"]["content"] == "<think>思考过程</think>\n正式回复"

    def test_refresh_tools_thinking_in_think_block(self):
        """工具调用 + 思考过程放进 <think> 折叠块前置，文本在后（对齐 web 端体验）。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        state.accumulated = "回复完成"
        state.tool_summary = "🔧 skill_view — 客户档案"
        state.thinking = "思考中"
        state.done = True
        refresh_payload = {
            "msgid": "M", "aibotid": "BOT123", "chattype": "single",
            "from": {"userid": "USER_A"}, "msgtype": "stream", "stream": {"id": sid},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        events[0].agent_id = "AGENT-1"
        resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        # 工具摘要只在 <think> 折叠块（不单独发 text_notice 卡片，避免与思考块重复）
        assert frame["msgtype"] == "stream"
        assert "template_card" not in frame
        content = frame["stream"]["content"]
        assert content.startswith("<think>")
        assert "🔧 工具调用" in content
        assert "skill_view" in content
        assert "思考中" in content
        assert content.endswith("回复完成")
        # 工具/思考在 <think> 块内，文本在块外之后
        think_end = content.index("</think>")
        assert "回复完成" in content[think_end:]

    def test_refresh_agent_template_card_extracted(self):
        """agent 回复含 template_card JSON → 提取卡片渲染，正文剥掉 JSON 不显示原始文本。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        agent_card_json = (
            '{"msgtype":"template_card","template_card":{"card_type":"text_notice",'
            '"main_title":{"title":"🚗 试驾报告"},"card_action":{"type":1,"url":"https://x"}}}'
        )
        state.accumulated = "试驾完成。\n" + agent_card_json + "\n点击查看详情。"
        state.done = True
        refresh_payload = {
            "msgid": "M", "aibotid": "BOT123", "chattype": "single",
            "from": {"userid": "USER_A"}, "msgtype": "stream", "stream": {"id": sid},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        events[0].agent_id = "AGENT-1"
        resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        # 卡片提取出来用 stream_with_template_card 渲染
        assert frame["msgtype"] == "stream_with_template_card"
        assert frame["template_card"]["main_title"]["title"] == "🚗 试驾报告"
        # 正文里不再有原始 JSON
        content = frame["stream"]["content"]
        assert "msgtype" not in content
        assert "template_card" not in content
        assert "试驾完成" in content
        assert "点击查看详情" in content

    def test_resolve_images_bare_path(self):
        """裸路径 /root/xxx.png → 剥 /root/ → manager 解析 → msg_item base64+md5。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter
        import base64 as _b64
        import hashlib as _hl

        adapter = WeComBotCallbackAdapter(CONFIG)
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        data_url = "data:image/png;base64," + _b64.b64encode(png).decode()
        with patch("app.channel.wecom_bot_callback.resolve_image_to_data_url",
                   AsyncMock(return_value=data_url)):
            items = asyncio.run(adapter._resolve_images("AGENT-1", "图保存到 /root/output.png"))
        assert len(items) == 1
        assert items[0]["msgtype"] == "image"
        assert items[0]["image"]["base64"] == _b64.b64encode(png).decode()
        assert items[0]["image"]["md5"] == _hl.md5(png).hexdigest()

    def test_refresh_finish_attaches_image_msg_item(self):
        """finish 帧：内容含裸图片路径 → stream.msg_item 附 base64 图片。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter
        import base64 as _b64

        adapter = WeComBotCallbackAdapter(CONFIG)
        sid, state = self._seed_stream()
        state.accumulated = "图保存到 /root/chart.png"
        state.done = True
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        data_url = "data:image/png;base64," + _b64.b64encode(png).decode()
        refresh_payload = {
            "msgid": "M", "aibotid": "BOT123", "chattype": "single",
            "from": {"userid": "USER_A"}, "msgtype": "stream", "stream": {"id": sid},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        events[0].agent_id = "AGENT-1"  # 模拟 router 设置
        with patch("app.channel.wecom_bot_callback.resolve_image_to_data_url",
                   AsyncMock(return_value=data_url)):
            resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        assert frame["msgtype"] == "stream"
        assert frame["stream"]["msg_item"][0]["image"]["base64"] == _b64.b64encode(png).decode()


# ── content truncation ──────────────────────────────────────────────────


class TestTruncation:
    def test_truncate_bytes_multibyte_safe(self):
        from app.channel.wecom_bot_callback import _truncate_bytes

        # 中文每字 3 字节；截到 7 字节应保留 2 个完整字（6 字节），丢弃第 3 个不完整
        text = "你好世界"  # 12 字节
        truncated = _truncate_bytes(text, 7)
        assert truncated.encode("utf-8") == "你好".encode("utf-8")

    def test_truncate_noop_under_limit(self):
        from app.channel.wecom_bot_callback import _truncate_bytes

        assert _truncate_bytes("short", 20480) == "short"

    def test_refresh_truncates_oversize_content(self):
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter, _streams, _StreamState

        adapter = WeComBotCallbackAdapter(CONFIG)
        # 构造 > 20480 字节的累积内容
        big = "中" * 7000  # 21000 字节
        _streams["SID_BIG"] = _StreamState(accumulated=big, done=True, created_at=0.0)
        refresh_payload = {
            "msgid": "M",
            "aibotid": "BOT123",
            "chattype": "single",
            "from": {"userid": "U"},
            "msgtype": "stream",
            "stream": {"id": "SID_BIG"},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter.parse_incoming(request))
        resp = asyncio.run(adapter.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        assert len(frame["stream"]["content"].encode("utf-8")) <= 20480


# ── file share links ──────────────────────────────────────────────────────


class TestFileLinks:
    def setup_method(self):
        from app.channel import wecom_bot_callback

        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()

    def test_resolve_file_links_media_pdf(self):
        """MEDIA:/tmp/x.pdf → 分享链接 markdown 行；图片扩展名跳过。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        with patch("app.channel.wecom_bot_callback.resolve_file_share_url",
                   AsyncMock(return_value=("https://share/abc", "report.pdf"))):
            links = asyncio.run(
                adapter._resolve_file_links("AGENT-1", "已转PDF: MEDIA:/tmp/report.pdf")
            )
        assert len(links) == 1
        assert "report.pdf" in links[0]
        assert "https://share/abc" in links[0]

    def test_resolve_file_links_skips_images(self):
        """MEDIA:/tmp/x.png 是图片 → 跳过（由 _resolve_images 处理）。"""
        from app.channel.wecom_bot_callback import WeComBotCallbackAdapter

        adapter = WeComBotCallbackAdapter(CONFIG)
        with patch("app.channel.wecom_bot_callback.resolve_file_share_url",
                   AsyncMock(return_value=("https://share/abc", "x.png"))):
            links = asyncio.run(
                adapter._resolve_file_links("AGENT-1", "MEDIA:/tmp/chart.png")
            )
        assert links == []


# ── Redis 共享存储模式（fakeredis）────────────────────────────────────────


class TestRedisStore:
    """Redis 模式：get_redis 返回 fakeredis，验证跨实例共享 + TTL + GETDEL。"""

    @pytest.fixture
    def fake_redis(self):
        pytest.importorskip("fakeredis")  # 仅测试用，CI 未装则跳过本类测试
        import fakeredis.aioredis

        return fakeredis.aioredis.FakeRedis(decode_responses=True)

    @pytest.fixture(autouse=True)
    def patch_get_redis(self, fake_redis, monkeypatch):
        """所有 store 函数走 fakeredis（非内存降级）。"""
        from app.channel import wecom_bot_callback

        async def _fake():
            return fake_redis

        monkeypatch.setattr(wecom_bot_callback, "get_redis", _fake)
        # 清内存（确保走 Redis 不走内存）
        wecom_bot_callback._streams.clear()
        wecom_bot_callback._active.clear()
        wecom_bot_callback._response_urls.clear()
        yield
        import asyncio as _a
        _a.run(fake_redis.flushall())

    def test_create_get_roundtrip(self):
        from app.channel.wecom_bot_callback import _store_create, _store_get

        asyncio.run(_store_create("SID1", "USER_A", "https://resp/url"))
        state = asyncio.run(_store_get("SID1"))
        assert state is not None
        assert state.accumulated == ""
        assert state.done is False
        assert state.card_sent is False

    def test_active_sid_and_response_url_pop(self):
        from app.channel.wecom_bot_callback import (
            _store_create, _store_get_active_sid, _store_pop_response_url,
        )

        asyncio.run(_store_create("SID2", "USER_B", "https://resp/2"))
        assert asyncio.run(_store_get_active_sid("USER_B")) == "SID2"
        # GETDEL 一次性
        assert asyncio.run(_store_pop_response_url("USER_B")) == "https://resp/2"
        assert asyncio.run(_store_pop_response_url("USER_B")) == ""

    def test_append_thinking_cap(self):
        from app.channel.wecom_bot_callback import _store_create, _store_append, _store_get

        asyncio.run(_store_create("SID3", "USER_C", ""))
        asyncio.run(_store_append("SID3", "thinking", "第一段", 1500))
        asyncio.run(_store_append("SID3", "thinking", "第二段", 1500))
        state = asyncio.run(_store_get("SID3"))
        assert state.thinking == "第一段第二段"
        # cap
        asyncio.run(_store_append("SID3", "thinking", "X" * 2000, 1500))
        state = asyncio.run(_store_get("SID3"))
        assert state.thinking.endswith("...")
        assert len(state.thinking) == 1503

    def test_finish_marks_done(self):
        from app.channel.wecom_bot_callback import _store_create, _store_finish, _store_get

        asyncio.run(_store_create("SID4", "USER_D", ""))
        asyncio.run(_store_finish("SID4", "最终回复"))
        state = asyncio.run(_store_get("SID4"))
        assert state.accumulated == "最终回复"
        assert state.done is True

    def test_cross_instance_refresh_reads_redis(self):
        """模拟跨 pod：A pod 写，B pod（新 adapter 实例）读——Redis 共享。"""
        from app.channel.wecom_bot_callback import (
            WeComBotCallbackAdapter, _store_create, _store_set_accumulated, _store_finish,
            _store_get_active_sid,
        )

        # pod A: create + 写累积
        asyncio.run(_store_create("SID_X", "USER_X", ""))
        asyncio.run(_store_set_accumulated("SID_X", "流式中"))
        # pod B（新 adapter 实例，内存空）：refresh 读 Redis
        adapter_b = WeComBotCallbackAdapter(CONFIG)
        sid = asyncio.run(_store_get_active_sid("USER_X"))
        assert sid == "SID_X"  # 跨实例能取到（内存模式下取不到）
        refresh_payload = {
            "msgid": "M", "aibotid": "BOT", "chattype": "single",
            "from": {"userid": "USER_X"}, "msgtype": "stream", "stream": {"id": "SID_X"},
        }
        request = _make_request(_encrypt_body(refresh_payload), {"nonce": "N"})
        events = asyncio.run(adapter_b.parse_incoming(request))
        resp = asyncio.run(adapter_b.handle_callback(request, events, AsyncMock()))
        frame = _decrypt_envelope(json.loads(resp.body))
        assert frame["stream"]["content"] == "流式中"
        assert frame["stream"]["finish"] is False

    def test_ttl_set(self, fake_redis):
        from app.channel.wecom_bot_callback import _store_create, _STREAM_KEY, _STREAM_TTL

        asyncio.run(_store_create("SID_TTL", "USER_T", ""))
        ttl = asyncio.run(fake_redis.ttl(_STREAM_KEY.format(sid="SID_TTL")))
        assert 0 < ttl <= int(_STREAM_TTL)
