"""DingTalk channel adapter 单元测试

覆盖:
  - Challenge 验证
  - HMAC-SHA256 签名校验
  - JSON 消息解析
  - 消息发送 (via mock httpx)
  - 消息截断
"""
import base64
import hashlib
import hmac
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDingTalkChallenge:

    @pytest.mark.asyncio
    async def test_handle_check_url(self):
        """处理钉钉 URL 验证 checkUrl 事件"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

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
            return {"eventType": "check_url", "eventId": "evt_test"}
        request.json = json_mock

        response = await adapter.handle_verification(request)

        assert response is not None
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_non_challenge_returns_none(self):
        """非 checkUrl 事件应返回 None"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

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
            return {"eventType": "im.message_receive", "msgtype": "text"}
        request.json = json_mock

        response = await adapter.handle_verification(request)
        assert response is None


class TestDingTalkSignature:

    @pytest.mark.asyncio
    async def test_verify_signature_valid(self):
        """验证正确签名通过"""
        from app.channel.dingtalk import DingTalkAdapter

        app_secret = "test_secret_key_12345"
        config = {
            "app_key": "test_key",
            "app_secret": app_secret,
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        timestamp = "1718000000"
        expected_sig = base64.b64encode(
            hmac.new(
                app_secret.encode("utf-8"),
                f"{timestamp}\n".encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode()

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [
                (b"timestamp", timestamp.encode()),
                (b"sign", expected_sig.encode()),
                (b"content-type", b"application/json"),
            ],
            "query_string": b"",
        }
        request = Request(scope)
        async def mock_body():
            return b"{}"
        request.body = mock_body

        result = await adapter.verify_signature(request)
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_signature_invalid(self):
        """验证错误签名不通过"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        from fastapi import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/callback",
            "headers": [
                (b"timestamp", b"1718000000"),
                (b"sign", b"invalid_signature"),
                (b"content-type", b"application/json"),
            ],
            "query_string": b"",
        }
        request = Request(scope)

        result = await adapter.verify_signature(request)
        assert result is False


class TestDingTalkParseIncoming:

    @pytest.mark.asyncio
    async def test_parse_text_message(self):
        """解析钉钉文本消息事件 → MessageEvent"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        payload = {
            "conversationId": "cid_test_chat",
            "conversationType": "1",
            "msgId": "msg_test_123",
            "senderId": "user_test_open",
            "senderNick": "测试用户",
            "msgtype": "text",
            "text": {"content": " 你好钉钉 "},
            "robotCode": "test_robot",
            "isInAtList": True,
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

        events = await adapter.parse_incoming(request)

        assert len(events) == 1
        event = events[0]
        assert event.text == "你好钉钉"
        assert event.chat_id == "cid_test_chat"
        assert event.user_id == "user_test_open"
        assert event.user_name == "测试用户"
        assert event.channel_type == "dingtalk"
        assert event.platform_message_id == "msg_test_123"

    @pytest.mark.asyncio
    async def test_parse_check_url_returns_empty(self):
        """checkUrl 验证请求应返回空列表"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        payload = {
            "eventType": "check_url",
            "eventId": "evt_test",
            "timestamp": 1718000000,
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

        events = await adapter.parse_incoming(request)

        assert len(events) == 0


class TestDingTalkSendMessage:

    @pytest.mark.asyncio
    async def test_send_message_success(self):
        """发送钉钉消息成功"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"processQueryKey": "qry_123"}

        with patch.object(adapter, "_ensure_token", return_value="test_token"):
            with patch("app.channel.dingtalk.httpx.AsyncClient") as mock_client_class:
                mock_ctx = AsyncMock()
                mock_client_class.return_value.__aenter__.return_value = mock_ctx
                mock_ctx.post = AsyncMock(return_value=mock_resp)

                result = await adapter.send_message("user001", "你好钉钉")

        assert result is True

    @pytest.mark.asyncio
    async def test_send_message_truncates_long_text(self):
        """超长文本发送时截断"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        long_text = "B" * 10000
        truncated = adapter._truncate(long_text)

        assert len(truncated) <= 5000
        assert truncated.endswith("...")


class TestDingTalkToken:

    @pytest.mark.asyncio
    async def test_ensure_token(self):
        """获取钉钉 access token"""
        from app.channel.dingtalk import DingTalkAdapter

        config = {
            "app_key": "test_key",
            "app_secret": "test_secret",
            "robot_code": "test_robot",
        }
        adapter = DingTalkAdapter(config)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "accessToken": "dt_token_abc",
            "expireIn": 7200,
        }

        with patch("app.channel.dingtalk.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            token = await adapter._ensure_token()

        assert token == "dt_token_abc"
        assert adapter._access_token == "dt_token_abc"
