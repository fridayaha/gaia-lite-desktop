"""WeCom channel adapter 单元测试

覆盖:
  - SHA1 签名校验
  - URL 验证 (echostr)
  - XML 消息解析
  - 消息发送 (via mock httpx)
"""
import base64
import hashlib
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from xml.etree import ElementTree as ET


class TestWeComSignature:

    def test_sha1_signature(self):
        """测试 WeCom SHA1 签名算法"""
        from app.channel.wecom import _sha1_signature

        token = "test_token_123"
        timestamp = "1718000000"
        nonce = "1234567890"
        encrypt = "encrypted_content_here"

        sig = _sha1_signature(token, timestamp, nonce, encrypt)

        # 算法: sha1(sorted([token, timestamp, nonce, encrypt]))
        parts = sorted([token, timestamp, nonce, encrypt])
        expected = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()
        assert sig == expected

    def test_verify_signature_valid(self):
        """验证正确签名的校验通过"""
        from app.channel.wecom import WeComAdapter, _sha1_signature

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        # 构造带正确签名的请求
        timestamp = "1718000000"
        nonce = "123456"
        encrypt_xml = (
            '<xml><Encrypt><![CDATA[encrypted_data]]></Encrypt>'
            '<MsgSignature>dummy</MsgSignature>'
            '<TimeStamp>1718000000</TimeStamp>'
            '<Nonce>123456</Nonce></xml>'
        )
        msg_signature = _sha1_signature(
            config["token"], timestamp, nonce, "encrypted_data"
        )

        # 构造 mock Request
        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [],
            "query_string": (
                f"msg_signature={msg_signature}&timestamp={timestamp}&nonce={nonce}"
            ).encode(),
        }
        request = Request(scope)
        # Override body
        async def mock_body():
            return encrypt_xml.encode("utf-8")
        request.body = mock_body

        import asyncio
        result = asyncio.run(adapter.verify_signature(request))
        assert result is True

    def test_verify_signature_invalid(self):
        """验证错误签名的校验不通过"""
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [],
            "query_string": (
                b"msg_signature=invalid_sig&timestamp=1718000000&nonce=123456"
            ),
        }
        from fastapi import Request
        request = Request(scope)
        async def mock_body():
            return b"<xml><Encrypt>bad</Encrypt></xml>"
        request.body = mock_body

        import asyncio
        result = asyncio.run(adapter.verify_signature(request))
        assert result is False


class TestWeComParseIncoming:

    def test_parse_text_message(self):
        """解析 WeCom 文本消息 XML → MessageEvent"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageType

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        # Mock: 消息已解密后的 XML
        decrypted_xml = (
            '<xml><ToUserName><![CDATA[ww-test]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[text]]></MsgType>'
            '<Content><![CDATA[你好，智能体]]></Content>'
            '<MsgId>1234567890</MsgId>'
            '<AgentID>1000001</AgentID>'
            '</xml>'
        )

        # Patch _decrypt_message to return known XML
        with patch("app.channel.wecom._decrypt_message", return_value=decrypted_xml.encode("utf-8")):
            from fastapi import Request
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/callback",
                "headers": [],
                "query_string": b"msg_signature=sig&timestamp=1718000000&nonce=123456",
            }
            request = Request(scope)
            async def mock_body():
                return b"<xml><Encrypt>encrypted</Encrypt></xml>"
            request.body = mock_body

            import asyncio
            events = asyncio.run(adapter.parse_incoming(request))

        assert len(events) == 1
        event = events[0]
        assert event.text == "你好，智能体"
        assert event.chat_id == "user001"
        assert event.user_id == "user001"
        assert event.channel_type == "wecom"
        assert event.platform_message_id == "1234567890"

    def test_ignore_non_text_message(self):
        """非 text 类型的消息（event）应被忽略"""
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        # Event 消息（如 enter_agent）
        event_xml = (
            '<xml><ToUserName><![CDATA[ww-test]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[event]]></MsgType>'
            '<Event><![CDATA[enter_agent]]></Event>'
            '<AgentID>1000001</AgentID>'
            '</xml>'
        )

        with patch("app.channel.wecom._decrypt_message", return_value=event_xml.encode("utf-8")):
            from fastapi import Request
            scope = {
                "type": "http",
                "method": "POST",
                "path": "/callback",
                "headers": [],
                "query_string": b"msg_signature=sig&timestamp=1718000000&nonce=123456",
            }
            request = Request(scope)
            async def mock_body():
                return b"<xml><Encrypt>encrypted</Encrypt></xml>"
            request.body = mock_body

            import asyncio
            events = asyncio.run(adapter.parse_incoming(request))

        assert len(events) == 0


class TestWeComSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """发送文本消息成功"""
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        # Mock the send endpoint response
        mock_send_resp = MagicMock()
        mock_send_resp.status_code = 200
        mock_send_resp.json.return_value = {"errcode": 0, "msgid": "msg_123"}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                # Make post() return an awaitable that resolves to mock_send_resp
                mock_ctx.post = AsyncMock(return_value=mock_send_resp)

                result = await adapter.send_message("user001", "你好，有什么可以帮助你的？")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_message_segments_long_text(self):
        """超长文本应按字节分段多条发送（不截断丢内容）"""
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        # 1000 字中文 ≈ 3000 字节，超过 2048 限制
        long_text = "关公骑赤兔马驰骋于赛博坦。" * 100
        sent_chunks: list[str] = []

        async def fake_send_one(chat_id, content):
            sent_chunks.append(content)
            return True

        with patch.object(adapter, "_send_one", side_effect=fake_send_one):
            result = await adapter.send_message("user001", long_text)

        assert result is True
        # 应分段多条，每段 ≤ 2048 字节
        assert len(sent_chunks) > 1
        for chunk in sent_chunks:
            assert len(chunk.encode("utf-8")) <= 2048
        # 拼接还原完整内容（不丢内容）
        assert "".join(sent_chunks) == long_text

    def test_split_by_bytes(self):
        """_split_by_bytes：字节切、换行边界、不切断字符、空文本"""
        from app.channel.wecom import WeComAdapter

        # 空文本
        assert WeComAdapter._split_by_bytes("") == []
        # 短文本单段
        assert WeComAdapter._split_by_bytes("hi") == ["hi"]
        # 1000+字中文 → 多段各 ≤2048 字节，拼接还原
        novel = "关公骑赤兔马驰骋于赛博坦。" * 100
        chunks = WeComAdapter._split_by_bytes(novel)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.encode("utf-8")) <= 2048
        assert "".join(chunks) == novel
        # 换行边界优先
        multi = "第一行\n第二行\n" + "长" * 2000 + "\n第三行"
        chunks2 = WeComAdapter._split_by_bytes(multi)
        assert "".join(chunks2) == multi
        for c in chunks2:
            assert len(c.encode("utf-8")) <= 2048


class TestWeComOutboundMediaSize:
    """出站图片/文件大小预检 + 自动降级（WeCom 图片 2MB / 文件 20MB）。"""

    def _make_adapter(self):
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)
        adapter.ua_agent_id = "agent-uuid-1"
        return adapter

    @pytest.mark.asyncio
    async def test_send_image_small_uses_image_msgtype(self):
        """图片 ≤2MB → 正常 image msgtype，不降级。"""
        adapter = self._make_adapter()
        small = b"\x89PNG\r\n\x1a\n" + b"\x00" * (512 * 1024)  # 0.5MB
        data_url = "data:image/png;base64," + base64.b64encode(small).decode()

        with patch("app.channel.wecom.resolve_image_to_data_url",
                   new=AsyncMock(return_value=data_url)), \
             patch.object(adapter, "_media_upload",
                          new=AsyncMock(return_value="mid")) as mu, \
             patch.object(adapter, "send_card_message",
                          new=AsyncMock(return_value={"errcode": 0})) as sc, \
             patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as so:
            ok = await adapter._send_image("u1", "output/x.png", "图")

        assert ok is True
        mu.assert_awaited_once_with(small, "x.png", media_type="image")
        sc.assert_awaited_once()
        assert sc.call_args.args[1] == "image"  # image msgtype
        so.assert_not_awaited()  # 无降级提示

    @pytest.mark.asyncio
    async def test_send_image_over_2mb_degrades_to_file(self):
        """图片 >2MB 且 ≤20MB → 降级：先发提示文本，再以 file 上传 + file msgtype。"""
        adapter = self._make_adapter()
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024)  # 5MB
        data_url = "data:image/png;base64," + base64.b64encode(big).decode()

        with patch("app.channel.wecom.resolve_image_to_data_url",
                   new=AsyncMock(return_value=data_url)), \
             patch.object(adapter, "_media_upload",
                          new=AsyncMock(return_value="mid")) as mu, \
             patch.object(adapter, "send_card_message",
                          new=AsyncMock(return_value={"errcode": 0})) as sc, \
             patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as so:
            ok = await adapter._send_image("u1", "output/big.png", "图")

        assert ok is True
        # 先发降级提示
        assert so.await_count == 1
        assert "转为文件发送" in so.call_args.args[1]
        # 以 file 类型上传 + file msgtype
        mu.assert_awaited_once_with(big, "big.png", media_type="file")
        assert sc.call_args.args[1] == "file"

    @pytest.mark.asyncio
    async def test_send_image_over_20mb_rejected(self):
        """图片 >20MB → 拒绝：只发提示，不上传、不发卡片。"""
        adapter = self._make_adapter()
        huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (25 * 1024 * 1024)  # 25MB
        data_url = "data:image/png;base64," + base64.b64encode(huge).decode()

        with patch("app.channel.wecom.resolve_image_to_data_url",
                   new=AsyncMock(return_value=data_url)), \
             patch.object(adapter, "_media_upload", new=AsyncMock(return_value="mid")) as mu, \
             patch.object(adapter, "send_card_message",
                          new=AsyncMock(return_value={"errcode": 0})) as sc, \
             patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as so:
            ok = await adapter._send_image("u1", "output/huge.png", "图")

        assert ok is True  # 已发提示，消费掉引用
        assert so.await_count == 1
        assert "无法发送" in so.call_args.args[1]
        mu.assert_not_awaited()
        sc.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_file_over_20mb_rejected(self):
        """文件 >20MB → 发提示，不上传。"""
        adapter = self._make_adapter()
        huge = b"%PDF-1.4\n" + b"\x00" * (25 * 1024 * 1024)  # 25MB

        with patch("app.channel.wecom.resolve_file_bytes",
                   new=AsyncMock(return_value=(huge, "big.pdf"))), \
             patch.object(adapter, "_media_upload", new=AsyncMock(return_value="mid")) as mu, \
             patch.object(adapter, "send_card_message",
                          new=AsyncMock(return_value={"errcode": 0})) as sc, \
             patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as so:
            ok = await adapter._send_file("u1", "output/big.pdf", "big.pdf")

        assert ok is True
        assert so.await_count == 1
        assert "无法发送" in so.call_args.args[1]
        mu.assert_not_awaited()
        sc.assert_not_awaited()


class TestWeComProcessing:

    @pytest.mark.asyncio
    async def test_send_processing(self):
        """发送处理中提示"""
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        with patch.object(adapter, "send_message", return_value=True) as mock_send:
            msg_id = await adapter.send_processing("user001")

        assert msg_id == "user001"
        mock_send.assert_called_once()
        assert "智能体启动中" in mock_send.call_args[0][1]

    @pytest.mark.asyncio
    async def test_replace_with_response_sends_new_message(self):
        """WeCom 不支持编辑，replace_with_response 走 send_message"""
        from app.channel.wecom import WeComAdapter

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        with patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as mock_send:
            result = await adapter.replace_with_response("user001", "processing_id", "这是回复")

        assert result is True
        assert mock_send.await_args[0][0] == "user001"
        assert mock_send.await_args[0][1] == "这是回复"
        assert mock_send.call_args[0][1] == "这是回复"


class TestWeComVoice:

    def test_parse_voice_message(self):
        """voice 消息解析为 VOICE MessageEvent（text 空，带 media_id）"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageType

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)

        voice_xml = (
            '<xml><ToUserName><![CDATA[ww-test]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[voice]]></MsgType>'
            '<MediaId><![CDATA[media_abc123]]></MediaId>'
            '<Format><![CDATA[amr]]></Format>'
            '<MsgId>9999999999</MsgId>'
            '<AgentID>1000001</AgentID>'
            '</xml>'
        )

        with patch("app.channel.wecom._decrypt_message", return_value=voice_xml.encode("utf-8")):
            from fastapi import Request
            scope = {
                "type": "http", "method": "POST", "path": "/callback",
                "headers": [],
                "query_string": b"msg_signature=sig&timestamp=1718000000&nonce=123456",
            }
            request = Request(scope)
            async def mock_body():
                return b"<xml><Encrypt>encrypted</Encrypt></xml>"
            request.body = mock_body

            import asyncio
            events = asyncio.run(adapter.parse_incoming(request))

        assert len(events) == 1
        event = events[0]
        assert event.message_type == MessageType.VOICE
        assert event.text == ""
        assert event.raw_message.get("media_id") == "media_abc123"
        assert event.chat_id == "user001"

    @pytest.mark.asyncio
    async def test_transcribe_success(self):
        """transcribe：media_get + ASR provider → 文字"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        config = {
            "token": "test-token",
            "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww-test",
            "secret": "test-secret",
            "agent_id": "1000001",
        }
        adapter = WeComAdapter(config)
        event = MessageEvent(
            text="", message_type=MessageType.VOICE, chat_id="user001",
            user_id="user001", channel_type="wecom",
            raw_message={"media_id": "media_abc123"},
        )

        mock_provider = MagicMock()
        mock_provider.name = "volcengine"
        mock_provider.transcribe = AsyncMock(return_value="你好,这是一段语音")

        with patch.object(adapter, "_media_get", return_value=b"amr-bytes"), \
             patch("app.channel.wecom.get_asr_provider", return_value=mock_provider):
            text = await adapter.transcribe(event)

        mock_provider.transcribe.assert_awaited_once_with(b"amr-bytes", fmt="amr")
        assert text == "你好,这是一段语音"

    @pytest.mark.asyncio
    async def test_transcribe_no_provider_returns_empty(self):
        """ASR provider 未配置 → transcribe 返回空，不调 media_get"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        with patch("app.channel.wecom.get_asr_provider", return_value=None), \
             patch.object(adapter, "_media_get") as mock_media:
            text = await adapter.transcribe(event)

        assert text == ""
        mock_media.assert_not_called()

    @pytest.mark.asyncio
    async def test_transcribe_empty_audio_returns_empty(self):
        """media 下载失败 → transcribe 返回空（provider 在但不调）"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        mock_provider = MagicMock()
        mock_provider.transcribe = AsyncMock(return_value="should-not-be-called")

        with patch.object(adapter, "_media_get", return_value=b""), \
             patch("app.channel.wecom.get_asr_provider", return_value=mock_provider):
            text = await adapter.transcribe(event)

        assert text == ""
        mock_provider.transcribe.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_transcribe_provider_error_returns_empty(self):
        """provider 抛 AsrError → transcribe 返回空（兜底）"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType
        from app.asr import AsrError

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        mock_provider = MagicMock()
        mock_provider.name = "volcengine"
        mock_provider.transcribe = AsyncMock(side_effect=AsrError("HTTP 500"))

        with patch.object(adapter, "_media_get", return_value=b"amr-bytes"), \
             patch("app.channel.wecom.get_asr_provider", return_value=mock_provider):
            text = await adapter.transcribe(event)

        assert text == ""

    @pytest.mark.asyncio
    async def test_transcribe_asr_url_not_configured(self):
        """ASR_URL 未配置 → 直接返回空（不下载媒体、不调 ASR）"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        with patch("app.channel.wecom.settings", MagicMock(asr_url="")), \
             patch.object(adapter, "_media_get", AsyncMock(return_value=b"amr")) as mock_media:
            text = await adapter.transcribe(event)

        assert text == ""
        mock_media.assert_not_awaited()  # 未配 ASR_URL → 不下载媒体

    @pytest.mark.asyncio
    async def test_transcribe_asr_non_200_returns_empty(self):
        """ASR sidecar 返回非 200 → 返回空"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        mock_asr_resp = MagicMock()
        mock_asr_resp.status_code = 500

        with patch("app.channel.wecom.settings", MagicMock(asr_url="http://asr:9100")), \
             patch.object(adapter, "_media_get", return_value=b"amr-bytes"), \
             patch("app.channel.wecom.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_asr_resp)

            text = await adapter.transcribe(event)

        assert text == ""

    @pytest.mark.asyncio
    async def test_transcribe_asr_exception_returns_empty(self):
        """ASR sidecar 抛异常（连接超时/拒绝）→ 返回空"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        with patch("app.channel.wecom.settings", MagicMock(asr_url="http://asr:9100")), \
             patch.object(adapter, "_media_get", return_value=b"amr-bytes"), \
             patch("app.channel.wecom.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(side_effect=RuntimeError("connect timeout"))

            text = await adapter.transcribe(event)

        assert text == ""

    @pytest.mark.asyncio
    async def test_transcribe_asr_200_no_text_returns_empty(self):
        """ASR 返回 200 但 body 无 text 字段 → 返回空"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent, MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(
            text="", message_type=MessageType.VOICE,
            raw_message={"media_id": "media_x"},
        )

        mock_asr_resp = MagicMock()
        mock_asr_resp.status_code = 200
        mock_asr_resp.json.return_value = {"error": "no text here"}  # 无 text 字段

        with patch("app.channel.wecom.settings", MagicMock(asr_url="http://asr:9100")), \
             patch.object(adapter, "_media_get", return_value=b"amr-bytes"), \
             patch("app.channel.wecom.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_asr_resp)

            text = await adapter.transcribe(event)

        assert text == ""



class TestWeComCard:

    def test_parse_card_variants(self):
        """_parse_card：卡片/文本/坏卡/代码围栏/空"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card = '{"msgtype":"template_card","template_card":{"main_title":{"title":"hi"}}}'
        assert adapter._parse_card(card)[:2] == ("card", "template_card")
        assert adapter._parse_card("你好")[0] == "text"
        assert adapter._parse_card('{"msgtype":"unsupported","x":1}')[0] == "bad_card"
        assert adapter._parse_card('```json\n{"msgtype":"news","news":{"articles":[]}}\n```')[:2] == ("card", "news")
        assert adapter._parse_card("")[0] == "text"
        # JSON 无 msgtype → text（不当坏卡）
        assert adapter._parse_card('{"a":1}')[0] == "text"
        # 容错：前导说明文字 / 尾部文字 / 前后文字+围栏 / prose 含 {示例} / 纯 \n\n 前缀
        assert adapter._parse_card(f"好的，查到了：\n{card}")[:2] == ("card", "template_card")
        assert adapter._parse_card(f"{card}\n点击查看")[:2] == ("card", "template_card")
        assert adapter._parse_card(f"好的\n```json\n{card}\n```\n以上")[:2] == ("card", "template_card")
        assert adapter._parse_card(f"按 {{销售}} 查询：\n{card}")[:2] == ("card", "template_card")
        assert adapter._parse_card(f"\n\n{card}")[:2] == ("card", "template_card")
        # prose 里只有非法 {示例}，无 msgtype JSON → text（无误判）
        assert adapter._parse_card("建议 {改天} 再约")[0] == "text"

    @pytest.mark.asyncio
    async def test_send_message_card_routes_to_send_card(self):
        """send_message 收到卡片 JSON → 走 send_card_message（template_card 透传）"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card = ('{"msgtype":"template_card","template_card":{'
                '"card_type":"button_interaction",'
                '"main_title":{"title":"hi"},"task_id":"x",'
                '"button_list":[{"text":"ok","key":"select_1"}]}}')
        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_message("u1", card)
        assert ok is True
        sent = ctx.post.call_args.kwargs["json"]
        assert sent["msgtype"] == "template_card"
        # button_interaction 的 task_id 被 gateway 覆盖为唯一值（gw_ 前缀）
        assert sent["template_card"]["task_id"].startswith("gw_")

    @pytest.mark.asyncio
    async def test_send_message_prose_and_card(self):
        """send_message 收到「前导文字 + 卡片 JSON」→ 先发文本再发卡片；\n\n 前缀只发卡片"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card = '{"msgtype":"template_card","template_card":{"main_title":{"title":"hi"}}}'
        # text_notice 无 task_id（不需要）→ gateway 不注入

        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}

        # 场景 1：前导文字 + 卡片 → 先发一条 markdown 文本，再发卡片
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_message("u1", f"好的，查到了：\n{card}")
        assert ok is True
        posts = [c.kwargs["json"] for c in ctx.post.call_args_list]
        # 第一条：前导文本 markdown；第二条：卡片
        assert len(posts) == 2
        assert posts[0]["msgtype"] == "markdown"
        assert "好的，查到了：" in posts[0]["markdown"]["content"]
        assert posts[1]["msgtype"] == "template_card"

        # 场景 2：纯 \n\n 前缀（whitespace）→ 只发卡片，不发文本
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_message("u1", f"\n\n{card}")
        assert ok is True
        posts = [c.kwargs["json"] for c in ctx.post.call_args_list]
        assert len(posts) == 1
        assert posts[0]["msgtype"] == "template_card"

    @pytest.mark.asyncio
    async def test_button_interaction_task_id_injected_when_missing(self):
        """button_interaction 卡片缺 task_id（agent 丢了）→ gateway 注入唯一 gw_<uuid>"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        # agent 丢了 task_id 的 button_interaction 卡片
        card = ('{"msgtype":"template_card","template_card":{'
                '"card_type":"button_interaction",'
                '"main_title":{"title":"选择客户"},'
                '"button_list":[{"text":"王先生","key":"select_1"}]}}')
        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_message("u1", card)
        assert ok is True
        sent = ctx.post.call_args.kwargs["json"]
        assert sent["template_card"]["task_id"].startswith("gw_")
        assert len(sent["template_card"]["task_id"]) == 15  # gw_ + 12 hex

    @pytest.mark.asyncio
    async def test_text_notice_no_task_id_injected(self):
        """text_notice 卡片（非 button_interaction）→ gateway 不注入 task_id"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card = ('{"msgtype":"template_card","template_card":{'
                '"card_type":"text_notice",'
                '"main_title":{"title":"画像"}}}')
        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_message("u1", card)
        assert ok is True
        sent = ctx.post.call_args.kwargs["json"]
        assert "task_id" not in sent["template_card"]

    @pytest.mark.asyncio
    async def test_send_message_multiple_cards(self):
        """一条回复含多个卡片 → 每个卡片都单独发送，无原始 JSON 残留"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card1 = '{"msgtype":"template_card","template_card":{"main_title":{"title":"报告A"}}}'
        card2 = '{"msgtype":"template_card","template_card":{"main_title":{"title":"报告B"}}}'
        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}

        # 前导文字 + 卡片1 + 中间文字 + 卡片2 + 尾部文字
        reply = f"找到2条：\n{card1}\n以及\n{card2}\n以上"
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_message("u1", reply)
        assert ok is True
        posts = [c.kwargs["json"] for c in ctx.post.call_args_list]
        # 顺序：前导文本(markdown) → 卡片1 → 中间文本(markdown) → 卡片2 → 尾部文本(markdown)
        assert [p["msgtype"] for p in posts] == [
            "markdown", "template_card", "markdown", "template_card", "markdown",
        ]
        assert "报告A" in posts[1]["template_card"]["main_title"]["title"]
        assert "报告B" in posts[3]["template_card"]["main_title"]["title"]
        # 文本消息里不含残留的卡片 JSON
        for p in posts:
            if p["msgtype"] == "markdown":
                assert "msgtype" not in p["markdown"]["content"]

    @pytest.mark.asyncio
    async def test_send_message_card_failure_falls_back_to_text(self):
        """卡片发送失败（errcode≠0）→ 降级"消息展示失败"文本（修 send_card_message 返回 dict 的 bool 误判）"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card = '{"msgtype":"template_card","template_card":{"main_title":{"title":"hi"}}}'
        fail_resp = MagicMock(); fail_resp.status_code = 200
        fail_resp.json.return_value = {"errcode": 42014, "errmsg": "dup task_id"}
        ok_resp = MagicMock(); ok_resp.status_code = 200
        ok_resp.json.return_value = {"errcode": 0}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                # 第一次（卡片）失败，第二次（降级文本）成功
                ctx.post = AsyncMock(side_effect=[fail_resp, ok_resp])
                ok = await adapter.send_message("u1", card)
        assert ok is True
        posts = [c.kwargs["json"] for c in ctx.post.call_args_list]
        assert posts[0]["msgtype"] == "template_card"
        assert posts[1]["msgtype"] == "markdown"
        assert "消息展示失败" in posts[1]["markdown"]["content"]

    @pytest.mark.asyncio
    async def test_streaming_update_skips_json(self):
        """流式更新：accumulated_text 以 { 开头时不 chunk-flush（卡片保护）"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        flushed = []
        async def fake_send_one(chat_id, content):
            flushed.append(content); return True
        with patch.object(adapter, "_send_one", side_effect=fake_send_one):
            r = await adapter.send_streaming_update("u1", "mid", '{"msgtype":"template_card"')
        assert r is True and flushed == []

    @pytest.mark.asyncio
    async def test_replace_with_response_card(self):
        """流式结束：完整文本是卡片 JSON → send_card_message 整体下发"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        card = '{"msgtype":"textcard","textcard":{"title":"t","url":"http://x"}}'
        with patch.object(adapter, "send_card_message", new=AsyncMock(return_value={"errcode": 0})) as sc:
            with patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as so:
                adapter._stream_sent["u1"] = 0
                r = await adapter.replace_with_response("u1", "mid", card)
        assert r is True and sc.await_count == 1
        # 卡片成功 → 不走文本降级
        assert so.await_count == 0

    @pytest.mark.asyncio
    async def test_replace_with_response_text_flushes_tail(self):
        """流式结束：纯文本 → flush 剩余 tail（已发的不再发）"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        with patch.object(adapter, "_send_one", new=AsyncMock(return_value=True)) as so:
            adapter._stream_sent["u1"] = 5
            await adapter.replace_with_response("u1", "mid", "hello world")
        # 已发的 5 字节不再发，只 flush 剩余 tail " world"
        so.assert_awaited_with("u1", " world")


class TestWeComCardClick:

    def test_parse_button_click_event(self):
        """event/template_card_event/button_interaction → card_click event"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        click_xml = (
            '<xml><FromUserName><![CDATA[u1]]></FromUserName>'
            '<MsgType><![CDATA[event]]></MsgType>'
            '<Event><![CDATA[template_card_event]]></Event>'
            '<CardType><![CDATA[button_interaction]]></CardType>'
            '<TaskId><![CDATA[gw_abc]]></TaskId>'
            '<EventKey><![CDATA[approve]]></EventKey>'
            '<ResponseCode><![CDATA[rc_x]]></ResponseCode></xml>'
        )
        with patch("app.channel.wecom._decrypt_message", return_value=click_xml.encode()):
            from fastapi import Request
            req = Request({"type": "http", "method": "POST", "path": "/cb",
                           "headers": [], "query_string": b"msg_signature=s&timestamp=1&nonce=n"})
            async def mb(): return b"<xml><Encrypt>x</Encrypt></xml>"
            req.body = mb
            import asyncio
            events = asyncio.run(adapter.parse_incoming(req))
        assert len(events) == 1
        ev = events[0]
        assert ev.raw_message.get("card_click") is True
        assert ev.raw_message.get("task_id") == "gw_abc"
        assert ev.raw_message.get("response_code") == "rc_x"
        assert "task_id=gw_abc" in ev.text and "key=approve" in ev.text

    def test_parse_card_update(self):
        """_parse_card_update：带 _update_task_id → (tid, body)；否则 None"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        upd = '{"msgtype":"template_card","template_card":{"main_title":{"title":"ok"}},"_update_task_id":"gw_1"}'
        assert adapter._parse_card_update(upd) == ("gw_1", {"main_title": {"title": "ok"}})
        assert adapter._parse_card_update("普通文本") is None
        assert adapter._parse_card_update('{"msgtype":"template_card","template_card":{}}') is None
        # 容错：前导文字 + 围栏 → 仍命中 (tid, body)
        assert adapter._parse_card_update(f"好的\n```json\n{upd}\n```") == (
            "gw_1", {"main_title": {"title": "ok"}},
        )

    @pytest.mark.asyncio
    async def test_click_reply_update_routes_to_update_template_card(self):
        """send_card_click_reply(update) → update_template_card（带 task_id+response_code）"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(text="", chat_id="u1", user_id="u1", channel_type="wecom",
                             raw_message={"card_click": True, "task_id": "gw_1", "response_code": "rc_x"})
        upd = '{"msgtype":"template_card","template_card":{"main_title":{"title":"ok"}},"_update_task_id":"gw_1"}'
        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter.send_card_click_reply(event, upd)
        # update_template_card 返回企微响应 dict（errcode==0 即成功）
        assert ok == {"errcode": 0}
        url = ctx.post.call_args.args[0]
        assert "update_template_card" in url
        body = ctx.post.call_args.kwargs["json"]
        assert body["task_id"] == "gw_1" and body["response_code"] == "rc_x"

    @pytest.mark.asyncio
    async def test_click_reply_non_update_routes_to_send_message(self):
        """send_card_click_reply(普通回复) → send_message 发新消息"""
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageEvent
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        event = MessageEvent(text="", chat_id="u1", raw_message={"card_click": True})
        with patch.object(adapter, "send_message", new=AsyncMock(return_value=True)) as sm:
            ok = await adapter.send_card_click_reply(event, "好的，已记录")
        assert ok is True and sm.await_count == 1


class TestWeComMenu:

    def test_parse_menu_click_event(self):
        """event/click → text=EventKey（菜单点击当用户消息送引擎）"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1",
        })
        click_xml = (
            '<xml><FromUserName><![CDATA[u1]]></FromUserName>'
            '<MsgType><![CDATA[event]]></MsgType>'
            '<Event><![CDATA[click]]></Event>'
            '<EventKey><![CDATA[menu_sales_report]]></EventKey></xml>'
        )
        with patch("app.channel.wecom._decrypt_message", return_value=click_xml.encode()):
            from fastapi import Request
            req = Request({"type": "http", "method": "POST", "path": "/cb",
                           "headers": [], "query_string": b"msg_signature=s&timestamp=1&nonce=n"})
            async def mb(): return b"<xml><Encrypt>x</Encrypt></xml>"
            req.body = mb
            import asyncio
            events = asyncio.run(adapter.parse_incoming(req))
        assert len(events) == 1
        assert events[0].text == "menu_sales_report"
        assert events[0].raw_message.get("menu_click") is True

    @pytest.mark.asyncio
    async def test_create_menu(self):
        """create_menu → POST menu/create（带 agentid + menu body）"""
        from app.channel.wecom import WeComAdapter
        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1000003",
        })
        mock_resp = MagicMock(); mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                res = await adapter.create_menu({"button": [{"type": "click", "name": "日报", "key": "k1"}]})
        assert res["errcode"] == 0
        assert "menu/create" in ctx.post.call_args.args[0]
        assert ctx.post.call_args.kwargs["params"]["agentid"] == 1000003
        assert ctx.post.call_args.kwargs["json"]["button"][0]["key"] == "k1"


def _make_parse_request(decrypted_xml: str):
    """构造一个 body=<Encrypt> 的 Request，_decrypt_message 被 patch 返回 decrypted_xml。"""
    from fastapi import Request

    scope = {
        "type": "http", "method": "POST", "path": "/callback",
        "headers": [],
        "query_string": b"msg_signature=sig&timestamp=1718000000&nonce=123456",
    }
    request = Request(scope)

    async def mock_body():
        return b"<xml><Encrypt>encrypted</Encrypt></xml>"

    request.body = mock_body
    return request


class TestWeComParseAttachments:
    """入站图片/文件/视频消息解析（对齐 web 通道附件能力）"""

    @pytest.mark.asyncio
    async def test_parse_image_message(self):
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1000001",
        })
        xml = (
            '<xml><ToUserName><![CDATA[ww]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[image]]></MsgType>'
            '<PicUrl><![CDATA[https://pic/url.png]]></PicUrl>'
            '<MediaId><![CDATA[media_img_1]]></MediaId>'
            '<MsgId>777</MsgId></xml>'
        )
        with patch("app.channel.wecom._decrypt_message", return_value=xml.encode("utf-8")):
            events = await adapter.parse_incoming(_make_parse_request(xml))
        assert len(events) == 1
        ev = events[0]
        assert ev.message_type == MessageType.IMAGE
        assert ev.text == ""
        assert ev.raw_message["media_id"] == "media_img_1"
        assert ev.raw_message["pic_url"] == "https://pic/url.png"
        assert ev.chat_id == "user001"

    @pytest.mark.asyncio
    async def test_parse_file_message(self):
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1000001",
        })
        xml = (
            '<xml><ToUserName><![CDATA[ww]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[file]]></MsgType>'
            '<MediaId><![CDATA[media_file_1]]></MediaId>'
            '<FileName><![CDATA[report.pdf]]></FileName>'
            '<MsgId>778</MsgId></xml>'
        )
        with patch("app.channel.wecom._decrypt_message", return_value=xml.encode("utf-8")):
            events = await adapter.parse_incoming(_make_parse_request(xml))
        assert len(events) == 1
        ev = events[0]
        assert ev.message_type == MessageType.FILE
        assert ev.raw_message["media_id"] == "media_file_1"
        assert ev.raw_message["file_name"] == "report.pdf"

    @pytest.mark.asyncio
    async def test_parse_video_message(self):
        from app.channel.wecom import WeComAdapter
        from app.channel.models import MessageType

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1000001",
        })
        xml = (
            '<xml><ToUserName><![CDATA[ww]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[video]]></MsgType>'
            '<MediaId><![CDATA[media_vid_1]]></MediaId>'
            '<MsgId>779</MsgId></xml>'
        )
        with patch("app.channel.wecom._decrypt_message", return_value=xml.encode("utf-8")):
            events = await adapter.parse_incoming(_make_parse_request(xml))
        assert len(events) == 1
        assert events[0].message_type == MessageType.FILE
        assert events[0].raw_message["msg_type"] == "video"

    @pytest.mark.asyncio
    async def test_parse_image_without_media_id_ignored(self):
        from app.channel.wecom import WeComAdapter

        adapter = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1000001",
        })
        xml = (
            '<xml><ToUserName><![CDATA[ww]]></ToUserName>'
            '<FromUserName><![CDATA[user001]]></FromUserName>'
            '<CreateTime>1718000000</CreateTime>'
            '<MsgType><![CDATA[image]]></MsgType>'
            '<MsgId>780</MsgId></xml>'
        )
        with patch("app.channel.wecom._decrypt_message", return_value=xml.encode("utf-8")):
            events = await adapter.parse_incoming(_make_parse_request(xml))
        assert events == []


class TestWeComOutboundImages:
    """出站图片：引擎回复引用工作区图片时发 image msgtype"""

    def _adapter(self):
        from app.channel.wecom import WeComAdapter
        a = WeComAdapter({
            "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
            "corp_id": "ww", "secret": "s", "agent_id": "1000001",
        })
        a.ua_agent_id = "agent-xyz"
        return a

    @pytest.mark.asyncio
    async def test_send_one_uses_numeric_agent_id_not_uuid(self):
        """回归：dispatcher 透传的 ua_agent_id 是 UUID，不得覆盖企微数字 agent_id。
        _send_one 构造 payload 的 agentid 必须是 config 里的数字 1000001，不能 int(UUID) 崩。
        """
        adapter = self._adapter()
        # 模拟 dispatcher 设置 UUID（之前 bug：覆盖了 self.agent_id 导致 int() 崩）
        adapter.ua_agent_id = "d38e436e-a4ae-4706-8b24-93e3f9d7bd15"
        assert adapter.agent_id == "1000001"  # 企微数字 agent_id 未被覆盖

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"errcode": 0, "msgid": "m1"}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                ok = await adapter._send_one("user001", "文本")
        assert ok is True
        # payload agentid 是数字 1000001，不是 UUID
        payload = ctx.post.call_args.kwargs["json"]
        assert payload["agentid"] == 1000001
        assert payload["touser"] == "user001"

    @pytest.mark.asyncio
    async def test_media_upload_returns_media_id(self):
        adapter = self._adapter()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"errcode": 0, "media_id": "m1", "created_at": "1"}
        with patch.object(adapter, "_ensure_token", return_value="tok"):
            with patch("app.channel.wecom.httpx.AsyncClient") as mc:
                ctx = AsyncMock(); mc.return_value.__aenter__.return_value = ctx
                ctx.post = AsyncMock(return_value=mock_resp)
                mid = await adapter._media_upload(b"png-bytes", "x.png")
        assert mid == "m1"
        assert "media/upload" in ctx.post.call_args.args[0]

    @pytest.mark.asyncio
    async def test_send_image_success(self):
        adapter = self._adapter()
        with patch("app.channel.wecom.resolve_image_to_data_url",
                   AsyncMock(return_value="data:image/png;base64,iVBORw==")):
            with patch.object(adapter, "_media_upload", AsyncMock(return_value="m1")):
                with patch.object(adapter, "send_card_message",
                                  AsyncMock(return_value={"errcode": 0})) as mock_card:
                    ok = await adapter._send_image("user001", "output/chart.png", "图")
        assert ok is True
        mock_card.assert_awaited_once()
        assert mock_card.call_args.args[1] == "image"
        assert mock_card.call_args.args[2] == {"media_id": "m1"}

    @pytest.mark.asyncio
    async def test_send_image_without_agent_id_fails(self):
        adapter = self._adapter()
        adapter.ua_agent_id = ""
        with patch("app.channel.wecom.resolve_image_to_data_url", AsyncMock()) as mock_res:
            ok = await adapter._send_image("user001", "output/chart.png", "图")
        assert ok is False
        mock_res.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_text_with_media_splits_text_and_image(self):
        adapter = self._adapter()
        text = "前文说明\n\n![图](output/chart.png)\n\n后文说明"
        with patch.object(adapter, "_send_image", AsyncMock(return_value=True)) as mock_img:
            with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                ok = await adapter._send_text_with_media("user001", text)
        assert ok is True
        mock_img.assert_awaited_once_with("user001", "output/chart.png", "图")
        # 前文 + 后文 两段文本各至少发一次
        sent_texts = "".join(c.args[1] for c in mock_one.call_args_list)
        assert "前文说明" in sent_texts
        assert "后文说明" in sent_texts

    @pytest.mark.asyncio
    async def test_send_text_with_media_no_refs_falls_back_to_text(self):
        adapter = self._adapter()
        with patch.object(adapter, "_send_image", AsyncMock()) as mock_img:
            with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                ok = await adapter._send_text_with_media("user001", "纯文本无图")
        assert ok is True
        mock_img.assert_not_awaited()
        mock_one.assert_awaited()

    @pytest.mark.asyncio
    async def test_send_text_with_media_duplicate_path_both_sent(self):
        """同一路径重复出现：两张图都要发，中间文本不丢（span 切分不靠 find 重定位）。"""
        adapter = self._adapter()
        text = "![a](output/x.png) 中间文字 ![b](output/x.png) 尾巴"
        with patch.object(adapter, "_send_image", AsyncMock(return_value=True)) as mock_img:
            with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                ok = await adapter._send_text_with_media("user001", text)
        assert ok is True
        assert mock_img.await_count == 2
        sent_texts = "".join(c.args[1] for c in mock_one.call_args_list)
        assert "中间文字" in sent_texts
        assert "尾巴" in sent_texts

    @pytest.mark.asyncio
    async def test_send_text_with_media_path_in_alt_not_mislocated(self):
        """alt 文本里含路径串：旧 find/rfind 会错位，span 切分按真实 match 边界走。"""
        adapter = self._adapter()
        # alt 里也出现 output/chart.png，path 同名 —— 必须按整段 ![alt](path) 的 span 切
        text = "前![output/chart.png](output/chart.png)后"
        with patch.object(adapter, "_send_image", AsyncMock(return_value=True)) as mock_img:
            with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                ok = await adapter._send_text_with_media("user001", text)
        assert ok is True
        mock_img.assert_awaited_once_with("user001", "output/chart.png", "output/chart.png")
        sent_texts = "".join(c.args[1] for c in mock_one.call_args_list)
        assert "前" in sent_texts
        assert "后" in sent_texts
        # alt 里的路径串不能被当成第二张图重复发
        assert mock_img.await_count == 1

    @pytest.mark.asyncio
    async def test_send_file_uploads_and_sends_file_msgtype(self):
        """文件链接 [name](path) → resolve_file_bytes 拿字节 → media_upload(file) → file msgtype"""
        adapter = self._adapter()
        with patch("app.channel.wecom.resolve_file_bytes",
                   AsyncMock(return_value=(b"pptx-bytes", "report.pptx"))) as mock_res:
            with patch.object(adapter, "_media_upload", AsyncMock(return_value="mid1")) as mock_up:
                with patch.object(adapter, "send_card_message",
                                  AsyncMock(return_value={"errcode": 0})) as mock_card:
                    ok = await adapter._send_file("user001", "output/report.pptx", "report.pptx")
        assert ok is True
        mock_res.assert_awaited_once_with("agent-xyz", "output/report.pptx")
        # media_upload 用 type=file
        assert mock_up.call_args.args[0] == b"pptx-bytes"
        assert mock_up.call_args.kwargs.get("media_type") == "file"
        assert mock_card.call_args.args[1] == "file"
        assert mock_card.call_args.args[2] == {"media_id": "mid1"}

    @pytest.mark.asyncio
    async def test_send_file_without_agent_id_fails(self):
        adapter = self._adapter()
        adapter.ua_agent_id = ""
        with patch("app.channel.wecom.resolve_file_bytes", AsyncMock()) as mock_res:
            ok = await adapter._send_file("user001", "output/x.pptx", "x.pptx")
        assert ok is False
        mock_res.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_file_resolve_failure_returns_false(self):
        """resolve_file_bytes 返回 None（文件不存在/超限）→ _send_file 返回 False"""
        adapter = self._adapter()
        with patch("app.channel.wecom.resolve_file_bytes", AsyncMock(return_value=None)):
            with patch.object(adapter, "_media_upload", AsyncMock()) as mock_up:
                ok = await adapter._send_file("user001", "output/missing.pptx", "missing.pptx")
        assert ok is False
        mock_up.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_text_with_media_file_link_split(self):
        """文本 + 文件链接 → 文本走 markdown，文件走 file msgtype"""
        adapter = self._adapter()
        text = "已生成报告：[Aliyun_Billing.pptx](output/Aliyun_Billing.pptx) 请查收"
        with patch.object(adapter, "_send_file", AsyncMock(return_value=True)) as mock_file:
            with patch.object(adapter, "_send_image", AsyncMock()) as mock_img:
                with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                    ok = await adapter._send_text_with_media("user001", text)
        assert ok is True
        mock_file.assert_awaited_once_with("user001", "output/Aliyun_Billing.pptx", "Aliyun_Billing.pptx")
        mock_img.assert_not_awaited()  # 不是图片
        sent_texts = "".join(c.args[1] for c in mock_one.call_args_list)
        assert "已生成报告" in sent_texts
        assert "请查收" in sent_texts

    @pytest.mark.asyncio
    async def test_send_text_with_media_mixed_image_and_file(self):
        """图片 + 文件混合：分别走 image / file msgtype，文本不丢"""
        adapter = self._adapter()
        text = "![图](output/chart.png) 中间 [报告.pptx](output/报告.pptx) 尾巴"
        with patch.object(adapter, "_send_image", AsyncMock(return_value=True)) as mock_img:
            with patch.object(adapter, "_send_file", AsyncMock(return_value=True)) as mock_file:
                with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                    ok = await adapter._send_text_with_media("user001", text)
        assert ok is True
        mock_img.assert_awaited_once_with("user001", "output/chart.png", "图")
        mock_file.assert_awaited_once_with("user001", "output/报告.pptx", "报告.pptx")
        sent_texts = "".join(c.args[1] for c in mock_one.call_args_list)
        assert "中间" in sent_texts
        assert "尾巴" in sent_texts

    @pytest.mark.asyncio
    async def test_send_text_with_media_file_link_failure_fallback_placeholder(self):
        """文件发送失败 → 降级发 [name] 占位"""
        adapter = self._adapter()
        text = "[报告.pptx](output/报告.pptx)"
        with patch.object(adapter, "_send_file", AsyncMock(return_value=False)):
            with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
                ok = await adapter._send_text_with_media("user001", text)
        assert ok is True
        sent_texts = "".join(c.args[1] for c in mock_one.call_args_list)
        assert "[报告.pptx]" in sent_texts

    @pytest.mark.asyncio
    async def test_send_streaming_update_buffers_image_ref(self):
        """含未发送图片引用的 delta 不 chunk-flush，留给最终 replace_with_response"""
        adapter = self._adapter()
        adapter._stream_sent["user001"] = 0
        # delta 含 ![
        with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
            await adapter.send_streaming_update("user001", "mid", "前面文字![图](output/x.png)")
        mock_one.assert_not_awaited()
        # _stream_sent 未推进
        assert adapter._stream_sent["user001"] == 0

    @pytest.mark.asyncio
    async def test_send_card_or_text_routes_image_via_msgtype(self):
        """send_message 收到含图片引用的文本 → 走 _send_text_with_media（发 image msgtype）"""
        adapter = self._adapter()
        text = "![图](output/chart.png)"
        with patch.object(adapter, "_send_image", AsyncMock(return_value=True)) as mock_img:
            with patch.object(adapter, "_send_one", AsyncMock(return_value=True)):
                ok = await adapter.send_message("user001", text)
        assert ok is True
        mock_img.assert_awaited_once()


def test_strip_wrapping_code_fence():
    """模型常把回复包在 ```markdown ... ``` 里，围栏行会渲染成空白卡片，需剥掉。"""
    from app.channel.wecom import _strip_wrapping_code_fence

    fenced = "```markdown\n![产品分布图](/opt/data/profiles/p/aliyun_daily_consumption.png)\n```"
    assert _strip_wrapping_code_fence(fenced) == \
        "![产品分布图](/opt/data/profiles/p/aliyun_daily_consumption.png)"

    # 带语言标记 / 前后空白
    assert _strip_wrapping_code_fence("  ```\nplain text\n```  ") == "plain text"

    # 非包裹整个文本的围栏（行内/中段）不动
    inline = "说明\n```python\nprint(1)\n```\n结束"
    assert _strip_wrapping_code_fence(inline) == inline

    # 无围栏不动
    assert _strip_wrapping_code_fence("![图](x.png)") == "![图](x.png)"


@pytest.mark.asyncio
async def test_send_message_strips_code_fence_around_image():
    """回复是 ```markdown\\n![alt](path)\\n``` → 只发图片，围栏行不发成空白卡片。"""
    from app.channel.wecom import WeComAdapter

    adapter = WeComAdapter({
        "token": "t", "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789abcde",
        "corp_id": "ww", "secret": "s", "agent_id": "1000001",
    })
    adapter.ua_agent_id = "agent-xyz"
    text = "```markdown\n![产品分布图](output/aliyun_daily_consumption.png)\n```"
    with patch.object(adapter, "_send_image", AsyncMock(return_value=True)) as mock_img:
        with patch.object(adapter, "_send_one", AsyncMock(return_value=True)) as mock_one:
            ok = await adapter.send_message("user001", text)
    assert ok is True
    mock_img.assert_awaited_once()  # 图片发了
    # 围栏行 ```markdown / ``` 不应作为独立文本消息发
    sent_texts = [c.args[1] for c in mock_one.call_args_list]
    assert not any("```" in t for t in sent_texts)
