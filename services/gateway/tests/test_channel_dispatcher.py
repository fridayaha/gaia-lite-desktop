"""Channel dispatcher 测试

覆盖:
  - 消息调度队列 (per-agent)
  - dispatcher._get_or_create_session (新建/复用/TTL/引擎重启)
  - dispatcher._forward_message (成功/失败)
  - 消息去重
  - 重试机制
  - 流式输出 (_stream_from_engine / _process_one_streaming)
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.channel.models import MessageEvent


class TestDispatcher:
    """消息调度器测试"""

    @pytest.mark.asyncio
    async def test_dispatch_queues_message(self):
        """dispatch 应将消息推送到 per-agent 队列"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="你好",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )

        await dispatcher.dispatch(event)
        assert event.agent_id in dispatcher._queues
        assert dispatcher._queues[event.agent_id].qsize() == 1

    @pytest.mark.asyncio
    async def test_dispatch_dedup(self):
        """相同 (agent_id, platform_message_id) 应去重"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="u1",
            user_id="u1",
            platform_message_id="dup-msg",
        )

        # 第一次 dispatch → 入队
        await dispatcher.dispatch(event)
        assert "agent-001" in dispatcher._queues

        # 第二次 dispatch（相同 msg_id）→ 被去重，不入队
        qsize_before = dispatcher._queues["agent-001"].qsize()
        await dispatcher.dispatch(event)

        # 由于 worker 可能已消费，只看去重逻辑是否阻止了再次入队
        assert dispatcher._queues["agent-001"].qsize() == qsize_before
        assert ("agent-001", "dup-msg") in dispatcher._dedup

    @pytest.mark.asyncio
    async def test_multiple_agents_separate_queues(self):
        """不同 agent 的消息应进入不同队列"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()

        event1 = MessageEvent(
            text="msg1", agent_id="agent-001", channel_type="wecom",
            chat_id="u1", user_id="u1", platform_message_id="m1")
        event2 = MessageEvent(
            text="msg2", agent_id="agent-002", channel_type="feishu",
            chat_id="u2", user_id="u2", platform_message_id="m2")

        await dispatcher.dispatch(event1)
        await dispatcher.dispatch(event2)

        assert "agent-001" in dispatcher._queues
        assert "agent-002" in dispatcher._queues
        assert dispatcher._queues["agent-001"] is not dispatcher._queues["agent-002"]


class TestSessionManagement:

    @pytest.mark.asyncio
    async def test_get_or_create_session_new(self):
        """首次消息应创建 Engine session"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            user_name="张三",
            platform_message_id="msg001",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "session-abc-123"}

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            session_id = await dispatcher._get_or_create_session(event)

        # session_id 现在是确定性的（SHA256 哈希）
        assert len(session_id) == 24
        # key 格式: {agent_id}:{channel_type}:{chat_id}
        expected_key = "550e8400-e29b-41d4-a716-446655440000:wecom:user001"
        assert expected_key in dispatcher._sessions
        # 验证确定性：再次调用应返回相同的 session_id
        session_id2 = await dispatcher._get_or_create_session(event)
        assert session_id == session_id2

    @pytest.mark.asyncio
    async def test_get_or_create_session_reuse(self):
        """相同 session_key 应复用已创建的 session"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )

        key = dispatcher._session_key(event)
        expected = dispatcher._deterministic_session_id(key)
        dispatcher._sessions[key] = (expected, 9999999999.0)

        session_id = await dispatcher._get_or_create_session(event)
        assert session_id == expected

    @pytest.mark.asyncio
    async def test_get_or_create_session_engine_restart(self):
        """引擎重启后（engine_just_started=True）应重建 session"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )

        key = dispatcher._session_key(event)
        dispatcher._sessions[key] = ("old-session", 9999999999.0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "new-session"}

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            session_id = await dispatcher._get_or_create_session(event, engine_just_started=True)

        # session_id 是确定性的，忽略引擎返回的 "new-session"
        assert len(session_id) == 24
        assert dispatcher._sessions[key][0] == session_id

    @pytest.mark.asyncio
    async def test_get_or_create_session_ttl_expired(self):
        """TTL 过期后应重建 session"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        dispatcher._session_ttl = 0.01  # 极短 TTL
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )

        key = dispatcher._session_key(event)
        dispatcher._sessions[key] = ("old-session", 0.0)  # 远古时间戳

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "renewed-session"}

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            session_id = await dispatcher._get_or_create_session(event)

        # session_id 是确定性的，忽略引擎返回的 "renewed-session"
        assert len(session_id) == 24
        assert dispatcher._sessions[key][0] == session_id

    @pytest.mark.asyncio
    async def test_invalidate_agent_sessions(self):
        """按 agent_id 清除 session 缓存"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        dispatcher._sessions["agent-001:wecom:u1"] = ("s1", 1.0)
        dispatcher._sessions["agent-001:feishu:g1"] = ("s2", 1.0)
        dispatcher._sessions["agent-002:wecom:u2"] = ("s3", 1.0)

        dispatcher._invalidate_agent_sessions("agent-001")

        assert "agent-001:wecom:u1" not in dispatcher._sessions
        assert "agent-001:feishu:g1" not in dispatcher._sessions
        assert "agent-002:wecom:u2" in dispatcher._sessions  # 其他 agent 不受影响


class TestForwardMessage:

    @pytest.mark.asyncio
    async def test_forward_message_success(self):
        """转发消息到引擎成功"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="你好",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "你好，有什么可以帮助的？"}}],
        }

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            response = await dispatcher._forward_message(event, "test-session")

        assert response == "你好，有什么可以帮助的？"

    @pytest.mark.asyncio
    async def test_forward_message_injects_attachment_hint(self):
        """event.attachments → 转发引擎的 payload content 含 [Attached files:] 且无 attachments 字段"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="",  # IM 图片 event 无文本
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
            attachments=[{"path": "uploads/abc.jpg", "name": "abc.jpg", "is_image": True}],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "收到图片"}}]}
        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            await dispatcher._forward_message(event, "test-session")

        # 转发到引擎的 payload：content 是 fallback 文案（event.text 为空），无 attachments
        sent_payload = mock_ctx.post.call_args.kwargs["json"]
        assert sent_payload["messages"][0]["content"] == \
            "I've uploaded 1 file(s): uploads/abc.jpg"
        assert "attachments" not in sent_payload["messages"][0]

    @pytest.mark.asyncio
    async def test_forward_message_with_text_and_attachment(self):
        """event.text 非空 + attachments → content 追加 hint"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="帮我看下这个文件",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
            attachments=[{"path": "uploads/report.pdf", "name": "report.pdf", "is_image": False}],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "好的"}}]}
        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            await dispatcher._forward_message(event, "test-session")

        sent_payload = mock_ctx.post.call_args.kwargs["json"]
        assert sent_payload["messages"][0]["content"] == \
            "帮我看下这个文件\n\n[Attached files: uploads/report.pdf]"
        assert "attachments" not in sent_payload["messages"][0]

    @pytest.mark.asyncio
    async def test_forward_message_injects_time_hint(self, monkeypatch):
        """开启时间注入 → 转发 payload 末尾追加 system 时间提示"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.settings import settings
        monkeypatch.setattr(settings, "inject_current_time", True)

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="今天几号",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "今天是..."}}]}
        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            await dispatcher._forward_message(event, "test-session")
        sent_payload = mock_ctx.post.call_args.kwargs["json"]
        assert sent_payload["messages"][-1]["role"] == "system"
        assert "Asia/Shanghai" in sent_payload["messages"][-1]["content"]
        assert sent_payload["messages"][0]["content"] == "今天几号"  # user 不变

    @pytest.mark.asyncio
    async def test_forward_message_time_injection_disabled(self, monkeypatch):
        """关闭时间注入 → payload 无 system 时间提示"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.settings import settings
        monkeypatch.setattr(settings, "inject_current_time", False)

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="今天几号",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)
            await dispatcher._forward_message(event, "test-session")
        sent_payload = mock_ctx.post.call_args.kwargs["json"]
        assert all(m.get("role") != "system" for m in sent_payload["messages"])


        """引擎返回错误时应返回 None"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg001",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            response = await dispatcher._forward_message(event, "test-session")
        assert response is None


class TestDispatcherTrace:
    """dispatcher 写 Langfuse trace 测试。

    覆盖：
      - _forward_message / _stream_from_engine 创建 trace 时传入
        enduser_id=event.user_id, channel_type=event.channel_type
      - 响应完成后调 finalize_chat_from_body / finalize_chat_from_sse 关闭 trace
      - trace_chat 返回 (None, None) 时（Langfuse 未配置）dispatcher 仍能正常转发
    """

    @pytest.mark.asyncio
    async def test_forward_message_creates_trace_with_channel_and_enduser(self):
        """_forward_message 调 trace_chat 时传 enduser_id=event.user_id, channel_type=event.channel_type"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hi",
            agent_id="agent-feishu-001",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_feishu_user",
            platform_message_id="msg-feishu-001",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = (
            b'{"choices":[{"message":{"content":"hello back"}}],'
            b'"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}'
        )
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "hello back"}}],
        }

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class, \
             patch("app.langfuse_client.trace_chat") as mock_trace_chat, \
             patch("app.langfuse_client.finalize_chat_from_body") as mock_finalize:
            mock_trace_chat.return_value = (MagicMock(), MagicMock())
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            response = await dispatcher._forward_message(event, "sess-feishu")

        assert response == "hello back"
        mock_trace_chat.assert_called_once()
        _, kwargs = mock_trace_chat.call_args
        assert kwargs.get("agent_id") == "agent-feishu-001"
        assert kwargs.get("enduser_id") == "ou_feishu_user"
        assert kwargs.get("channel_type") == "feishu"
        assert kwargs.get("engine_type") == "hermes"
        assert kwargs.get("session_id") == "sess-feishu"
        # finalize 必须被调用
        mock_finalize.assert_called_once()

    @pytest.mark.asyncio
    async def test_forward_message_trace_failure_does_not_break_dispatch(self):
        """trace_chat 返回 (None, None) → dispatcher 仍能正常转发，不抛异常。"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hi",
            agent_id="agent-wecom-001",
            channel_type="wecom",
            chat_id="wecom_user",
            user_id="wecom_user_id",
            platform_message_id="msg-wecom-001",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b'{"choices":[{"message":{"content":"reply"}}]}'
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "reply"}}],
        }

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class, \
             patch("app.langfuse_client.trace_chat", return_value=(None, None)), \
             patch("app.langfuse_client.finalize_chat_from_body") as mock_finalize:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            response = await dispatcher._forward_message(event, "sess-wecom")

        assert response == "reply"
        # finalize 仍被调用（内部 None-safe，不会崩）
        mock_finalize.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_from_engine_creates_trace_with_channel_and_enduser(self):
        """_stream_from_engine 调 trace_chat 时传 enduser_id=event.user_id, channel_type=event.channel_type"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hi",
            agent_id="agent-feishu-stream",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_feishu_stream_user",
            platform_message_id="msg-stream-001",
        )

        sse_lines = [
            'data: {"choices":[{"delta":{"content":"你好"}}]}',
            'data: {"choices":[{"delta":{"content":"，我是"}}]}',
            'data: {"choices":[{"delta":{"content":"智能体"}}]}',
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":4,"total_tokens":9}}',
            "data: [DONE]",
        ]

        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = mock_aiter_lines

        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        with patch("app.channel.dispatcher.httpx.AsyncClient", return_value=mock_client), \
             patch("app.langfuse_client.trace_chat") as mock_trace_chat, \
             patch("app.langfuse_client.finalize_chat_from_sse") as mock_finalize:
            mock_trace_chat.return_value = (MagicMock(), MagicMock())

            chunks = []
            async for chunk in dispatcher._stream_from_engine(event, "sess-stream"):
                chunks.append(chunk)

        assert "".join(chunks) == "你好，我是智能体"
        mock_trace_chat.assert_called_once()
        _, kwargs = mock_trace_chat.call_args
        assert kwargs.get("enduser_id") == "ou_feishu_stream_user"
        assert kwargs.get("channel_type") == "feishu"
        assert kwargs.get("session_id") == "sess-stream"
        # SSE 结束后必须 finalize
        mock_finalize.assert_called_once()
        # finalize 调用参数应包含原始 SSE 文本（含 content 和 usage 行）
        # dispatcher 用位置参数调 finalize_chat_from_sse(trace, gen, raw_sse, ...)
        # 注意：[DONE] 行在 return 前不 append，所以 raw 里没有 [DONE]，但 usage 行有
        fin_args, _ = mock_finalize.call_args
        raw = fin_args[2] if len(fin_args) > 2 else ""
        assert "你好" in raw
        assert "usage" in raw
        assert "total_tokens" in raw

    @pytest.mark.asyncio
    async def test_stream_from_engine_finalize_called_on_connection_error(self):
        """SSE 连接异常时 finally 仍应调 finalize（trace 不泄漏）。"""
        from app.channel.dispatcher import ChannelDispatcher
        from httpx import ConnectError

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hi",
            agent_id="agent-conn-err",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_conn_err_user",
            platform_message_id="msg-conn-err",
        )

        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(side_effect=ConnectError("conn refused"))
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        with patch("app.channel.dispatcher.httpx.AsyncClient", return_value=mock_client), \
             patch("app.langfuse_client.trace_chat", return_value=(MagicMock(), MagicMock())), \
             patch("app.langfuse_client.finalize_chat_from_sse") as mock_finalize:
            chunks = []
            async for chunk in dispatcher._stream_from_engine(event, "sess-conn"):
                chunks.append(chunk)

        assert chunks == []
        # finally 块必须执行 finalize
        mock_finalize.assert_called_once()


class TestRetry:

    @pytest.mark.asyncio
    async def test_retry_on_503(self):
        """503 错误应触发重试，最终成功"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="u1",
            user_id="u1",
            platform_message_id="msg001",
        )

        # 第一次 503，第二次成功
        mock_fail = MagicMock()
        mock_fail.status_code = 503
        mock_fail.text = "Service unavailable"

        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.json.return_value = {
            "choices": [{"message": {"content": "ok"}}],
        }

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(side_effect=[mock_fail, mock_ok])

            result = await dispatcher._forward_message_with_retry(event, "session-1")

        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retry_all_fail(self):
        """所有重试都失败应返回 None"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hello",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="u1",
            user_id="u1",
            platform_message_id="msg001",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.text = "Service unavailable"

        with patch("app.channel.dispatcher.httpx.AsyncClient") as mock_client_class:
            mock_ctx = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_ctx
            mock_ctx.post = AsyncMock(return_value=mock_resp)

            result = await dispatcher._forward_message_with_retry(event, "session-1")

        assert result is None


class TestDedupCleanup:

    @pytest.mark.asyncio
    async def test_clean_dedup_removes_expired(self):
        """_clean_dedup 应移除过期条目"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        now = 1000.0
        dispatcher._dedup[("a", "m1")] = now - 1  # 已过期
        dispatcher._dedup[("a", "m2")] = now + 100  # 未过期

        dispatcher._clean_dedup(now)

        assert ("a", "m1") not in dispatcher._dedup
        assert ("a", "m2") in dispatcher._dedup


class TestStreaming:
    """流式输出测试"""

    SS_EVENTS = [
        'data: {"choices":[{"delta":{"content":"你好"}}]}',
        'data: {"choices":[{"delta":{"content":"，"}}]}',
        'data: {"choices":[{"delta":{"content":"我是"}}]}',
        'data: {"choices":[{"delta":{"content":"智能体"}}]}',
        "data: [DONE]",
    ]

    @staticmethod
    def _make_sse_mock(sse_lines, status_code=200):
        """Helper: create mock httpx client that returns SSE lines."""
        async def mock_aiter_lines():
            for line in sse_lines:
                yield line

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.aiter_lines = mock_aiter_lines

        mock_stream_cm = MagicMock()
        mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_stream_cm.__aexit__ = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.stream = MagicMock(return_value=mock_stream_cm)

        return mock_client

    @pytest.mark.asyncio
    async def test_stream_from_engine_yields_chunks(self):
        """_stream_from_engine 应从 SSE 中提取所有 content"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="你好",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg001",
        )

        mock_client = self._make_sse_mock(self.SS_EVENTS)
        with patch("app.channel.dispatcher.httpx.AsyncClient", return_value=mock_client):
            chunks = []
            async for chunk in dispatcher._stream_from_engine(event, "session-1"):
                chunks.append(chunk)

        assert chunks == ["你好", "，", "我是", "智能体"]
        assert "".join(chunks) == "你好，我是智能体"

    @pytest.mark.asyncio
    async def test_stream_from_engine_injects_time_hint(self, monkeypatch):
        """开启时间注入 → 流式转发 payload 末尾追加 system 时间提示"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent
        from app.settings import settings
        monkeypatch.setattr(settings, "inject_current_time", True)

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="今天几号",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg001",
        )
        mock_client = self._make_sse_mock(self.SS_EVENTS)
        with patch("app.channel.dispatcher.httpx.AsyncClient", return_value=mock_client):
            chunks = []
            async for chunk in dispatcher._stream_from_engine(event, "session-1"):
                chunks.append(chunk)
        sent_payload = mock_client.stream.call_args.kwargs["json"]
        assert sent_payload["messages"][-1]["role"] == "system"
        assert "Asia/Shanghai" in sent_payload["messages"][-1]["content"]
        assert sent_payload["messages"][0]["content"] == "今天几号"  # user 不变

    @pytest.mark.asyncio
    async def test_stream_from_engine_http_error(self):
        """引擎返回非 200 时应静默终止"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hi",
            agent_id="agent-001",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg002",
        )

        mock_client = self._make_sse_mock([], status_code=503)
        with patch("app.channel.dispatcher.httpx.AsyncClient", return_value=mock_client):
            chunks = []
            async for chunk in dispatcher._stream_from_engine(event, "session-1"):
                chunks.append(chunk)

        assert chunks == []

    @pytest.mark.asyncio
    async def test_stream_from_engine_empty_content_skipped(self):
        """空的 content 应被跳过"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent

        dispatcher = ChannelDispatcher()
        event = MessageEvent(
            text="hi",
            agent_id="agent-001",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg003",
        )

        events = [
            'data: {"choices":[{"delta":{}}]}',              # 无 content
            'data: {"choices":[{"delta":{"content":""}}]}',   # 空字符串
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            "data: [DONE]",
        ]

        mock_client = self._make_sse_mock(events)
        with patch("app.channel.dispatcher.httpx.AsyncClient", return_value=mock_client):
            chunks = []
            async for chunk in dispatcher._stream_from_engine(event, "session-1"):
                chunks.append(chunk)

        assert chunks == ["hello"]

    @pytest.mark.asyncio
    async def test_process_one_streaming_periodic_updates(self):
        """_process_one_streaming 应周期性调 send_streaming_update"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent

        dispatcher = ChannelDispatcher()

        # Mock adapter with streaming support
        adapter = AsyncMock()
        adapter.supports_streaming = True
        adapter.send_processing.return_value = "om_placeholder"
        adapter.send_initial_response.return_value = "om_response_card"

        event = MessageEvent(
            text="你好",
            agent_id="agent-001",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg-stream-1",
        )

        # Mock _stream_from_engine — yield with real delays to trigger flush
        chunks = ["你好，", "我是智能体", "小明。"]

        async def mock_stream(evt, sid, model="", profile_name="", adapter=None):
            for c in chunks:
                yield c
                await asyncio.sleep(0.25)

        with patch.object(dispatcher, "_stream_from_engine", mock_stream):
            await dispatcher._process_one_streaming(event, adapter, "session-1", "om_placeholder")

        # 验证：最终 replace_with_response 被调用（在回复卡上），包含完整文本
        adapter.replace_with_response.assert_called_once_with(
            "oc_chat", "om_response_card", "你好，我是智能体小明。",
        )

        # 验证：冷启动 → 调用了 send_processing_done
        adapter.send_processing_done.assert_called_once_with(
            "oc_chat", "om_placeholder",
        )

        # 验证：至少有一次 send_streaming_update（节流后，>500ms）
        assert adapter.send_streaming_update.call_count >= 1

    @pytest.mark.asyncio
    async def test_process_one_streaming_error_append(self):
        """流式中断应追加错误提示到已有内容"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent

        dispatcher = ChannelDispatcher()

        adapter = AsyncMock()
        adapter.supports_streaming = True
        adapter.send_processing.return_value = "om_placeholder"
        adapter.send_initial_response.return_value = "om_response_card"

        event = MessageEvent(
            text="test",
            agent_id="agent-001",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg-stream-2",
        )

        # Mock _stream_from_engine — yield a few chunks then raise
        async def mock_stream(evt, sid, model="", profile_name="", adapter=None):
            yield "部分"
            yield "内容"
            raise ConnectionError("SSE connection lost")

        with patch.object(dispatcher, "_stream_from_engine", mock_stream):
            await dispatcher._process_one_streaming(event, adapter, "session-1", "om_placeholder")

        # 验证：错误时发送的内容应包含中断提示（在回复卡上）
        call_args = adapter.replace_with_response.call_args
        assert call_args is not None
        # 回复卡 ID 应为 om_response_card
        assert call_args[0][1] == "om_response_card"
        final_text = call_args[0][2]
        assert "部分内容" in final_text
        assert "回复生成中断" in final_text

    @pytest.mark.asyncio
    async def test_process_one_streaming_no_msg_id_fallback(self):
        """没有占位消息 ID 时应降级为非流式"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent

        dispatcher = ChannelDispatcher()

        adapter = AsyncMock()
        adapter.supports_streaming = True
        adapter.send_processing.return_value = None  # 发送占位失败

        event = MessageEvent(
            text="test",
            agent_id="agent-001",
            channel_type="feishu",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg-stream-3",
        )

        await dispatcher._process_one_streaming(event, adapter, "session-1", None)

        # send_initial_response 返回 None → 无启动卡，无回复卡 → 静默结束
        adapter.send_processing_done.assert_not_called()
        adapter.replace_with_response.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_one_streaming_empty_stream_sends_notice(self):
        """引擎 200 但空流（LLM 首 token 前失败，如 401/500/timeout）→ 兜底发
        ENGINE_EMPTY_RESPONSE，不静默无响应。"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageEvent
        from app.messages import ENGINE_EMPTY_RESPONSE

        dispatcher = ChannelDispatcher()

        adapter = AsyncMock()
        adapter.supports_streaming = True
        adapter.send_processing.return_value = "om_placeholder"

        event = MessageEvent(
            text="test",
            agent_id="agent-001",
            channel_type="wecom",
            chat_id="oc_chat",
            user_id="ou_user",
            platform_message_id="msg-stream-empty",
        )

        # Mock _stream_from_engine — 200-空流：yield 0 chunk、不抛异常
        async def mock_stream(evt, sid, model="", profile_name="", adapter=None):
            return
            yield  # 使函数成为 async generator（unreachable，保持空生成器语义）

        with patch.object(dispatcher, "_stream_from_engine", mock_stream):
            await dispatcher._process_one_streaming(event, adapter, "session-1", "om_placeholder")

        # 验证：空流 → 发 ENGINE_EMPTY_RESPONSE 兜底，不静默
        adapter.send_message.assert_called_once_with("oc_chat", ENGINE_EMPTY_RESPONSE)
        adapter.send_initial_response.assert_not_called()
        adapter.replace_with_response.assert_not_called()


class TestDeterministicSessionId:
    """_deterministic_session_id 单元测试"""

    @pytest.mark.asyncio
    async def test_same_key_same_id(self):
        """相同 session_key → 相同 session_id（确定性）"""
        from app.channel.dispatcher import ChannelDispatcher

        d = ChannelDispatcher()
        id1 = d._deterministic_session_id("agent-a:wecom:user001")
        id2 = d._deterministic_session_id("agent-a:wecom:user001")
        assert id1 == id2

    @pytest.mark.asyncio
    async def test_different_keys_different_ids(self):
        """不同 session_key → 不同 session_id"""
        from app.channel.dispatcher import ChannelDispatcher

        d = ChannelDispatcher()
        id1 = d._deterministic_session_id("agent-a:wecom:user001")
        id2 = d._deterministic_session_id("agent-a:wecom:user002")
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_different_chat_ids_different(self):
        """同一 agent 不同 chat_id → 不同 session_id"""
        from app.channel.dispatcher import ChannelDispatcher

        d = ChannelDispatcher()
        id1 = d._deterministic_session_id("agent-a:wecom:chat001")
        id2 = d._deterministic_session_id("agent-a:wecom:chat002")
        assert id1 != id2

    @pytest.mark.asyncio
    async def test_format_is_24_hex_chars(self):
        """session_id 应为 24 位十六进制字符串"""
        from app.channel.dispatcher import ChannelDispatcher

        d = ChannelDispatcher()
        sid = d._deterministic_session_id("test:test:test")
        assert len(sid) == 24
        int(sid, 16)  # 不抛出 ValueError


class TestAccessGate:
    """权限闸门 _check_im_access 测试"""

    def _make_event(self, **overrides):
        from app.channel.models import MessageEvent
        defaults = dict(
            text="hi",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="im_user_001",
            platform_message_id="msg-gate-1",
        )
        defaults.update(overrides)
        return MessageEvent(**defaults)

    @pytest.mark.asyncio
    async def test_not_bound_sends_hint_and_blocks(self):
        """未绑定 → 回 IM 未绑定提示并阻断"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.profile_resolver import NotBound, profile_resolver

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        event = self._make_event()

        with patch.object(profile_resolver, "check_access",
                          AsyncMock(side_effect=NotBound("nb"))):
            ok = await dispatcher._check_im_access(event, adapter)

        assert ok is False
        adapter.send_message.assert_awaited_once()
        args = adapter.send_message.call_args
        assert args[0][0] == "user001"
        assert "尚未绑定" in args[0][1]
        assert args.kwargs.get("reply_to") == "msg-gate-1"

    @pytest.mark.asyncio
    async def test_access_denied_sends_hint_and_blocks(self):
        """已映射但无权限 → 回无权限提示并阻断"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.profile_resolver import AccessDenied, profile_resolver

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        event = self._make_event()

        with patch.object(profile_resolver, "check_access",
                          AsyncMock(side_effect=AccessDenied("ad"))):
            ok = await dispatcher._check_im_access(event, adapter)

        assert ok is False
        assert "暂无权限" in adapter.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_profile_not_found_sends_hint_and_blocks(self):
        """agent/channel 不存在 → 回不可用提示并阻断"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.profile_resolver import ProfileNotFound, profile_resolver

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        event = self._make_event()

        with patch.object(profile_resolver, "check_access",
                          AsyncMock(side_effect=ProfileNotFound("nf"))):
            ok = await dispatcher._check_im_access(event, adapter)

        assert ok is False
        assert "暂不可用" in adapter.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_pass_no_message_sent(self):
        """通过校验 → 放行，不回消息"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.profile_resolver import profile_resolver

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        event = self._make_event()

        with patch.object(profile_resolver, "check_access", AsyncMock(return_value=None)):
            ok = await dispatcher._check_im_access(event, adapter)

        assert ok is True
        adapter.send_message.assert_not_called()


class TestEngineStartupUX:
    """F-GW-031 冷启动 UX：引擎未就绪 → 友好失败提示。"""

    def _make_event(self, **overrides):
        defaults = dict(
            text="你好",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="user001",
            platform_message_id="msg-ux-1",
        )
        defaults.update(overrides)
        return MessageEvent(**defaults)

    @pytest.mark.asyncio
    async def test_engine_not_ready_sends_failure_hint(self):
        """ensure_engine_ready 返回未就绪 → 回 '😔 智能体启动异常' 并终止"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.supports_streaming = False
        event = self._make_event()

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(False, False))):
            await dispatcher._process_one(event)

        # 发送了启动失败提示，且未转发到引擎
        adapter.send_message.assert_awaited_once()
        assert "启动异常" in adapter.send_message.call_args[0][1]
        adapter.send_processing.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cold_start_sends_processing_placeholder(self):
        """冷启动（就绪但 was_already_running=False）→ 发 '🤖 正在启动' 占位"""
        from app.channel.dispatcher import ChannelDispatcher

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.supports_streaming = True
        adapter.send_processing.return_value = "om_placeholder"
        adapter.send_initial_response.return_value = None  # 回复卡发送失败，提前返回
        event = self._make_event()

        async def mock_stream(evt, sid, model="", profile_name="", adapter=None):
            yield "hi"

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, False))), \
             patch.object(dispatcher, "_get_or_create_session", AsyncMock(return_value="s1")), \
             patch.object(dispatcher, "_resolve_profile", AsyncMock(return_value=("", False))), \
             patch("app.models.get_agent_model_config", AsyncMock(return_value={})), \
             patch("app.models.get_default_model", return_value="m"), \
             patch.object(dispatcher, "_stream_from_engine", mock_stream):
            await dispatcher._process_one(event)

        # 冷启动发了占位卡
        adapter.send_processing.assert_awaited_once_with("user001")

    @pytest.mark.asyncio
    async def test_infra_error_degrades_and_allows(self):
        """基础设施异常 → 降级放行（不回拒绝消息）"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.profile_resolver import profile_resolver

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        event = self._make_event()

        with patch.object(profile_resolver, "check_access",
                          AsyncMock(side_effect=RuntimeError("db down"))):
            ok = await dispatcher._check_im_access(event, adapter)

        assert ok is True
        adapter.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_one_gate_blocks_before_engine_start(self):
        """闸门拒绝时不启动引擎、不转发，仅回 IM 提示"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.profile_resolver import NotBound, profile_resolver

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        event = self._make_event(platform_message_id="msg-block-1")
        config = {"id": "ch1", "channel_type": "wecom"}

        with patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value=config)), \
             patch("app.channel.dispatcher.get_adapter",
                   MagicMock(return_value=adapter)), \
             patch.object(profile_resolver, "check_access",
                          AsyncMock(side_effect=NotBound("nb"))), \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))) as mock_engine:
            await dispatcher._process_one(event)

        mock_engine.assert_not_called()  # 闸门拒绝 → 不启动引擎
        adapter.send_message.assert_awaited_once()
        assert "尚未绑定" in adapter.send_message.call_args[0][1]

    @pytest.mark.asyncio
    async def test_voice_transcribe_empty_sends_failure_hint(self):
        """VOICE → transcribe 返回空 → 回 '没听清，请重试' 并终止（不启动引擎）"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageType

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.transcribe = AsyncMock(return_value="")
        event = self._make_event(text="", message_type=MessageType.VOICE)

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))) as mock_engine:
            await dispatcher._process_one(event)

        adapter.transcribe.assert_awaited_once()
        adapter.send_message.assert_awaited_once()
        assert "没听清" in adapter.send_message.call_args[0][1]
        mock_engine.assert_not_called()  # 转录失败 → 不启动引擎

    @pytest.mark.asyncio
    async def test_voice_transcribe_success_forwards_text(self):
        """VOICE → transcribe 成功 → 转为 TEXT 复用文本链路，转发到引擎"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageType

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.supports_streaming = False
        adapter.transcribe = AsyncMock(return_value="你好呀，这是语音转的文字")
        event = self._make_event(text="", message_type=MessageType.VOICE)

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))), \
             patch.object(dispatcher, "_get_or_create_session", AsyncMock(return_value="s1")), \
             patch.object(dispatcher, "_resolve_profile", AsyncMock(return_value=("", False))), \
             patch("app.models.get_agent_model_config", AsyncMock(return_value={})), \
             patch("app.models.get_default_model", return_value="m"), \
             patch.object(dispatcher, "_process_one_response", AsyncMock()) as mock_forward:
            await dispatcher._process_one(event)

        adapter.transcribe.assert_awaited_once()
        # 转录文本被写入 event 并转为 TEXT，且转发到引擎
        assert event.text == "你好呀，这是语音转的文字"
        assert event.message_type == MessageType.TEXT
        mock_forward.assert_awaited_once()


class TestAttachments:
    """Step 2.5 附件处理：IM 图片/文件 → 下载 → 写工作区 → 结构化 attachments"""

    def _make_event(self, **overrides):
        defaults = dict(
            text="",
            agent_id="550e8400-e29b-41d4-a716-446655440000",
            channel_type="wecom",
            chat_id="user001",
            user_id="im_user_001",
            platform_message_id="msg-att-1",
        )
        defaults.update(overrides)
        return MessageEvent(**defaults)

    @pytest.mark.asyncio
    async def test_attachment_image_success_forwards_path_text(self):
        """IMAGE → 下载 + 写工作区 → event.attachments 结构化，text 清空，转 TEXT 转发引擎"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageType

        # 合法 PNG magic bytes（\x89PNG\r\n\x1a\n）—— 通过 looks_like_image 校验
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.supports_streaming = False
        adapter.fetch_attachment_bytes = AsyncMock(return_value=png_bytes)
        event = self._make_event(
            message_type=MessageType.IMAGE,
            raw_message={"media_id": "m1", "msg_type": "image", "pic_url": ""},
        )

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch.object(dispatcher, "_write_to_workspace",
                          AsyncMock(return_value="uploads/x.png")) as mock_write, \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))), \
             patch.object(dispatcher, "_get_or_create_session", AsyncMock(return_value="s1")), \
             patch.object(dispatcher, "_resolve_profile", AsyncMock(return_value=("", False))), \
             patch("app.models.get_agent_model_config", AsyncMock(return_value={})), \
             patch("app.models.get_default_model", return_value="m"), \
             patch.object(dispatcher, "_process_one_response", AsyncMock()) as mock_forward:
            await dispatcher._process_one(event)

        adapter.fetch_attachment_bytes.assert_awaited_once()
        # 写工作区收到 (agent_id, filename, bytes)
        assert mock_write.call_args.args[2] == png_bytes
        # 结构化附件挂到 event.attachments，text 清空（hint 在转发引擎时合成）
        assert event.attachments == [
            {"path": "uploads/x.png", "name": "m1.jpg", "is_image": True}
        ]
        assert event.text == ""
        assert event.message_type == MessageType.TEXT
        mock_forward.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_attachment_download_empty_sends_failure_hint(self):
        """下载媒体为空 → ensure 引擎后回 '附件处理失败' 并终止（不转发到引擎）"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageType

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.fetch_attachment_bytes = AsyncMock(return_value=b"")
        event = self._make_event(
            message_type=MessageType.FILE,
            raw_message={"media_id": "m1", "file_name": "report.pdf", "msg_type": "file"},
        )

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))), \
             patch.object(dispatcher, "_process_one_response", AsyncMock()) as mock_forward:
            await dispatcher._process_one(event)

        adapter.fetch_attachment_bytes.assert_awaited_once()
        adapter.send_message.assert_awaited_once()
        assert "附件" in adapter.send_message.call_args[0][1]
        mock_forward.assert_not_awaited()  # 附件失败 → 不转发到引擎

    @pytest.mark.asyncio
    async def test_attachment_image_non_magic_bytes_rejected(self):
        """IMAGE 字节非图片 magic bytes（如 HTML 错误页）→ 不写工作区，回失败提示"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageType

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.fetch_attachment_bytes = AsyncMock(return_value=b"<html>error</html>")
        event = self._make_event(
            message_type=MessageType.IMAGE,
            raw_message={"media_id": "m1", "msg_type": "image"},
        )

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch.object(dispatcher, "_write_to_workspace",
                          AsyncMock(return_value="uploads/x.png")) as mock_write, \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))), \
             patch.object(dispatcher, "_process_one_response", AsyncMock()) as mock_forward:
            await dispatcher._process_one(event)

        mock_write.assert_not_awaited()  # 非 magic bytes → 不写工作区
        mock_forward.assert_not_awaited()  # 不转发引擎

    @pytest.mark.asyncio
    async def test_attachment_image_oversize_rejected(self):
        """IMAGE 字节超过入站上限 → 不写工作区"""
        from app.channel.dispatcher import ChannelDispatcher, INBOUND_IMAGE_MAX_BYTES
        from app.channel.models import MessageType

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        # 合法 PNG 头 + 超限体积
        oversize = b"\x89PNG\r\n\x1a\n" + b"\x00" * (INBOUND_IMAGE_MAX_BYTES + 1)
        adapter.fetch_attachment_bytes = AsyncMock(return_value=oversize)
        event = self._make_event(
            message_type=MessageType.IMAGE,
            raw_message={"media_id": "m1", "msg_type": "image"},
        )

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch.object(dispatcher, "_write_to_workspace",
                          AsyncMock(return_value="uploads/x.png")) as mock_write, \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))), \
             patch.object(dispatcher, "_process_one_response", AsyncMock()) as mock_forward:
            await dispatcher._process_one(event)

        mock_write.assert_not_awaited()
        mock_forward.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_attachment_file_skips_magic_check(self):
        """FILE 类型不做 magic bytes 校验——任意字节均可写入工作区"""
        from app.channel.dispatcher import ChannelDispatcher
        from app.channel.models import MessageType

        dispatcher = ChannelDispatcher()
        adapter = AsyncMock()
        adapter.supports_streaming = False
        # 非 magic 字节（PDF 头 %PDF-1.4 也不是图片 magic），FILE 应放行
        pdf_bytes = b"%PDF-1.4\n" + b"\x00" * 32
        adapter.fetch_attachment_bytes = AsyncMock(return_value=pdf_bytes)
        event = self._make_event(
            message_type=MessageType.FILE,
            raw_message={"media_id": "m1", "file_name": "report.pdf", "msg_type": "file"},
        )

        with patch("app.channel.dispatcher.get_adapter", return_value=adapter), \
             patch("app.models.get_channel_config_cached",
                   AsyncMock(return_value={"app_id": "x"})), \
             patch.object(dispatcher, "_check_im_access", AsyncMock(return_value=True)), \
             patch.object(dispatcher, "_write_to_workspace",
                          AsyncMock(return_value="uploads/report.pdf")) as mock_write, \
             patch("app.lifecycle.ensure_engine_ready",
                   AsyncMock(return_value=(True, True))), \
             patch.object(dispatcher, "_get_or_create_session", AsyncMock(return_value="s1")), \
             patch.object(dispatcher, "_resolve_profile", AsyncMock(return_value=("", False))), \
             patch("app.models.get_agent_model_config", AsyncMock(return_value={})), \
             patch("app.models.get_default_model", return_value="m"), \
             patch.object(dispatcher, "_process_one_response", AsyncMock()) as mock_forward:
            await dispatcher._process_one(event)

        mock_write.assert_awaited_once()  # FILE 放行，写入工作区
        assert mock_write.call_args.args[2] == pdf_bytes
        mock_forward.assert_awaited_once()
