"""Feishu channel adapter 单元测试

覆盖:
  - Challenge 验证
  - HMAC-SHA256 签名校验
  - JSON 消息解析 (im.message.receive_v1)
  - 消息发送 (via mock httpx)
  - 消息编辑
"""
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestFeishuChallenge:

    @pytest.mark.asyncio
    async def test_handle_challenge(self):
        """处理飞书 URL 验证 challenge"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        request = Request(scope)

        challenge_body = json.dumps({
            "type": "url_verification",
            "challenge": "test_challenge_value",
        }).encode()
        async def mock_body():
            return challenge_body
        request.body = mock_body
        request.json = lambda: __import__("asyncio").sleep(0) or __import__("functools").reduce(
            lambda r, _: json.loads(challenge_body), range(1), None
        )

        # Patch request.json
        import asyncio
        async def json_mock():
            return {"type": "url_verification", "challenge": "test_challenge_value"}
        request.json = json_mock

        response = await adapter.handle_verification(request)

        assert response is not None
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_non_challenge_returns_none(self):
        """非 challenge 的消息应返回 None"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        request = Request(scope)
        async def json_mock():
            return {"event": {"header": {"event_type": "im.message.receive_v1"}}}
        request.json = json_mock

        response = await adapter.handle_verification(request)
        assert response is None


class TestFeishuSignature:

    @pytest.mark.asyncio
    async def test_verify_signature_valid(self):
        """验证正确签名通过"""
        from app.channel.feishu import FeishuAdapter

        encrypt_key = "test_encrypt_key_12345"
        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": encrypt_key,
        }
        adapter = FeishuAdapter(config)

        timestamp = "1718000000"
        nonce = "abc123"
        body_text = '{"test": "data"}'

        expected_sig = hmac.new(
            encrypt_key.encode("utf-8"),
            f"{timestamp}{nonce}{body_text}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [
                (b"x-lark-request-timestamp", timestamp.encode()),
                (b"x-lark-request-nonce", nonce.encode()),
                (b"x-lark-signature", expected_sig.encode()),
                (b"content-type", b"application/json"),
            ],
            "query_string": b"",
        }
        request = Request(scope)
        # Request.body() reads from a cached attribute; we need to set receive
        import asyncio
        received = False
        async def mock_receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": body_text.encode(), "more_body": False}
            return {"type": "http.request", "body": b""}
        request._receive = mock_receive

        result = await adapter.verify_signature(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_signature_invalid(self):
        """验证错误签名不通过"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "encrypt_key",
        }
        adapter = FeishuAdapter(config)

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [
                (b"x-lark-request-timestamp", b"1718000000"),
                (b"x-lark-request-nonce", b"abc123"),
                (b"x-lark-signature", b"invalid_sig_here"),
                (b"content-type", b"application/json"),
            ],
            "query_string": b"",
        }
        request = Request(scope)
        async def mock_receive():
            return {"type": "http.request", "body": b'{"test": "data"}', "more_body": False}
        request._receive = mock_receive

        result = await adapter.verify_signature(request)
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_no_encrypt_key_returns_true(self):
        """无 encrypt_key 时不校验签名"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        async def mock_body():
            return b"{}"
        request.body = mock_body

        result = await adapter.verify_signature(request)
        assert result is True


class TestFeishuParseIncoming:

    @pytest.mark.asyncio
    async def test_parse_text_message(self):
        """解析飞书文本消息事件 → MessageEvent"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        payload = {
            "schema": "2.0",
            "header": {
                "event_id": "evt_test123",
                "event_type": "im.message.receive_v1",
                "create_time": "1718000000",
                "app_id": "cli_test",
            },
            "event": {
                "message": {
                    "chat_id": "oc_test_chat",
                    "chat_type": "group",
                    "message_id": "om_test_msg",
                    "message_type": "text",
                    "content": json.dumps({"text": "你好飞书"}),
                },
                "sender": {
                    "sender_id": {"open_id": "ou_test_user", "union_id": "uu_test"},
                    "name": "张三",
                },
            },
        }

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        request = Request(scope)
        async def json_mock():
            return payload
        request.json = json_mock
        async def body_mock():
            return json.dumps(payload).encode()
        request.body = body_mock

        import asyncio
        events = await adapter.parse_incoming(request)

        assert len(events) == 1
        event = events[0]
        assert event.text == "你好飞书"
        assert event.chat_id == "oc_test_chat"
        assert event.user_id == "ou_test_user"
        assert event.user_name == "张三"
        assert event.channel_type == "feishu"
        assert event.platform_message_id == "om_test_msg"

    @pytest.mark.asyncio
    async def test_parse_challenge_returns_empty(self):
        """Challenge 验证请求应返回空列表"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        payload = {
            "type": "url_verification",
            "challenge": "test_challenge",
            "token": "test_token",
        }

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
        }
        request = Request(scope)
        async def json_mock():
            return payload
        request.json = json_mock
        async def body_mock():
            return json.dumps(payload).encode()
        request.body = body_mock

        import asyncio
        events = await adapter.parse_incoming(request)

        # Challenge 请求不返回消息
        assert len(events) == 0


class TestFeishuSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """发送飞书消息成功"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"message_id": "om_sent_msg"}}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.feishu.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                mock_ctx.post = AsyncMock(return_value=mock_resp)

                result = await adapter.send_message("oc_test_chat", "你好")

        assert result is True

    @pytest.mark.asyncio
    async def test_edit_message(self):
        """编辑已发送的飞书消息"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.feishu.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                mock_ctx.patch = AsyncMock(return_value=mock_resp)

                result = await adapter.edit_message("oc_test_chat", "om_original_msg", "编辑后的内容")
                assert result is True


class TestFeishuVerifyUrl:

    @pytest.mark.asyncio
    async def test_verify_url_returns_404(self):
        """Feishu 不支持 GET 验证，verify_url 返回 404"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/callback",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)

        response = await adapter.verify_url(request)
        assert response.status_code == 404


class TestFeishuTruncation:

    @pytest.mark.asyncio
    async def test_send_message_truncates_long_text(self):
        """超长文本发送时截断"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        long_text = "A" * 20000  # 超过 10000
        truncated = adapter._truncate(long_text)

        assert len(truncated) <= 10000
        assert truncated.endswith("...")


class TestFeishuStreamingSupport:

    def test_supports_streaming_true(self):
        """Feishu 应支持流式输出"""
        from app.channel.feishu import FeishuAdapter

        adapter = FeishuAdapter({
            "app_id": "cli_test",
            "app_secret": "test_secret",
        })
        assert adapter.supports_streaming is True

    @pytest.mark.asyncio
    async def test_send_streaming_update(self):
        """send_streaming_update 应委托给 edit_message"""
        from app.channel.feishu import FeishuAdapter

        adapter = FeishuAdapter({
            "app_id": "cli_test",
            "app_secret": "test_secret",
        })

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.feishu.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                mock_ctx.patch = AsyncMock(return_value=mock_resp)

                ok = await adapter.send_streaming_update("oc_chat", "om_msg_id", "你好，我是智能体")
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_streaming_update_multiple_calls(self):
        """多次调用 send_streaming_update 应每次都更新消息内容"""
        from app.channel.feishu import FeishuAdapter

        adapter = FeishuAdapter({
            "app_id": "cli_test",
            "app_secret": "test_secret",
        })

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.feishu.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                mock_ctx.patch = AsyncMock(return_value=mock_resp)

                # 模拟第一次流式更新
                ok1 = await adapter.send_streaming_update("oc_chat", "om_msg_id", "你好")
                assert ok1 is True

                # 模拟第二次流式更新（追加更多内容）
                ok2 = await adapter.send_streaming_update("oc_chat", "om_msg_id", "你好，我是智能体")
                assert ok2 is True

        # 验证 edit_message 被调用了两次
        assert mock_ctx.patch.call_count == 2


class TestFeishuProcessing:

    @pytest.mark.asyncio
    async def test_send_processing_and_replace(self):
        """Feishu 发送处理中提示并替换为回复"""
        from app.channel.feishu import FeishuAdapter

        config = {
            "app_id": "cli_test",
            "app_secret": "test_secret",
            "verification_token": "test_token",
            "encrypt_key": "",
        }
        adapter = FeishuAdapter(config)

        mock_send_resp = MagicMock()
        mock_send_resp.json.return_value = {
            "code": 0, "data": {"message_id": "om_processing"},
        }

        mock_edit_resp = MagicMock()
        mock_edit_resp.json.return_value = {"code": 0}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.feishu.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                mock_ctx.post = AsyncMock(return_value=mock_send_resp)
                mock_ctx.patch = AsyncMock(return_value=mock_edit_resp)

                msg_id = await adapter.send_processing("oc_test_chat")
                assert msg_id == "om_processing"

                ok = await adapter.replace_with_response("oc_test_chat", msg_id, "这是回复")
                assert ok is True
