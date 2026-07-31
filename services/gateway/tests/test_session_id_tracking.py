"""Langfuse trace session_id 跟踪单元测试。

覆盖：
  - trace_chat / trace_run_start：session_id 传入 → lf.trace(session_id=...)；
    不传 → 不带 session_id key（不传 None 让 SDK 误处理）
  - DifyAdapter._convert_dify_event_block：message/agent_message 事件带 conversation_id
    时调用 self._langfuse_trace.update(session_id=...)；空 conversation_id 不调用
"""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.adapter import DifyAdapter


# ── trace_chat / trace_run_start 的 session_id 透传 ──────────────


class TestTraceChatSessionId:
    """trace_chat 的 session_id 参数透传。"""

    def test_trace_chat_with_session_id_passes_to_lf_trace(self):
        """session_id 非空 → lf.trace(session_id=...) 被调用。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{"messages":[]}',
                session_id="sess-abc-123",
            )

        _, kwargs = lf.trace.call_args
        assert kwargs.get("session_id") == "sess-abc-123"
        assert kwargs.get("user_id") == "agent-1"
        assert kwargs.get("name") == "chat_completion"

    def test_trace_chat_without_session_id_omits_key(self):
        """session_id=None → lf.trace 调用 kwargs 不含 session_id 键。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{"messages":[]}',
                session_id=None,
            )

        _, kwargs = lf.trace.call_args
        assert "session_id" not in kwargs

    def test_trace_chat_with_empty_session_id_omits_key(self):
        """session_id="" 空字符串 → 同样不传（被 falsy 判定跳过）。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{}',
                session_id="",
            )

        _, kwargs = lf.trace.call_args
        assert "session_id" not in kwargs


class TestTraceChatModelFallback:
    """trace_chat / trace_run_start 的 model fallback：Dify 请求用 'dify-app' 占位。"""

    def test_trace_chat_dify_uses_dify_app_fallback(self):
        """Dify 请求 model=None → generation.model='dify-app'（不是 'unknown'）。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        gen = MagicMock()
        trace.generation.return_value = gen

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="a1",
                engine_type="DIFY",
                path="/v1/chat/completions",
                method="POST",
                model=None,  # Dify 请求体没有 model 字段
                input_body=b'{"inputs":{"query":"hi"}}',
            )

        _, kwargs = trace.generation.call_args
        assert kwargs.get("model") == "dify-app"

    def test_trace_chat_hermes_uses_unknown_fallback(self):
        """非 Dify 请求 model=None → generation.model='unknown'（保持原行为）。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="a1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model=None,
                input_body=b'{}',
            )

        _, kwargs = trace.generation.call_args
        assert kwargs.get("model") == "unknown"

    def test_trace_chat_with_explicit_model_keeps_it(self):
        """请求体带 model 字段时，不触发 fallback。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="a1",
                engine_type="DIFY",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4o",
                input_body=b'{"model":"gpt-4o"}',
            )

        _, kwargs = trace.generation.call_args
        assert kwargs.get("model") == "gpt-4o"

    def test_trace_run_start_dify_uses_dify_app_fallback(self):
        """trace_run_start 同样对 Dify 请求用 'dify-app' 占位。"""
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="a1",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{}',
            )

        _, kwargs = trace.generation.call_args
        assert kwargs.get("model") == "dify-app"


class TestTraceRunStartSessionId:
    """trace_run_start 的 session_id 参数透传。"""

    def test_trace_run_start_with_session_id(self):
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="agent-2",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{"input":"hi"}',
                session_id="sess-run-xyz",
            )

        _, kwargs = lf.trace.call_args
        assert kwargs.get("session_id") == "sess-run-xyz"
        assert kwargs.get("name") == "run"

    def test_trace_run_start_without_session_id(self):
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="agent-2",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{}',
            )

        _, kwargs = lf.trace.call_args
        assert "session_id" not in kwargs


# ── trace_chat / trace_run_start 的 enduser_id 透传 ──────────────


class TestTraceChatEnduserId:
    """trace_chat 的 enduser_id 参数透传到 trace.metadata.enduser_id。"""

    def test_trace_chat_with_enduser_id_writes_to_metadata(self):
        """enduser_id 非空 → lf.trace(metadata.enduser_id=...) 被调用。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{"messages":[],"user":"enduser-abc"}',
                enduser_id="enduser-abc",
            )

        _, kwargs = lf.trace.call_args
        assert kwargs.get("user_id") == "agent-1"  # userId 维度仍是 agent_id
        metadata = kwargs.get("metadata", {})
        assert metadata.get("enduser_id") == "enduser-abc"

    def test_trace_chat_without_enduser_id_omits_metadata_key(self):
        """enduser_id=None → metadata 不含 enduser_id 键（其他 metadata 仍存在）。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{}',
                enduser_id=None,
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert "enduser_id" not in metadata
        # 其他 metadata 字段仍存在
        assert metadata.get("engine_type") == "HERMES"

    def test_trace_chat_with_empty_enduser_id_omits_metadata_key(self):
        """enduser_id="" 空字符串 → 同样不传（被 falsy 判定跳过）。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{}',
                enduser_id="",
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert "enduser_id" not in metadata


class TestTraceRunStartEnduserId:
    """trace_run_start 的 enduser_id 参数透传到 trace.metadata.enduser_id。"""

    def test_trace_run_start_with_enduser_id_writes_to_metadata(self):
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="agent-2",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{"input":"hi","user":"enduser-xyz"}',
                enduser_id="enduser-xyz",
            )

        _, kwargs = lf.trace.call_args
        assert kwargs.get("user_id") == "agent-2"
        metadata = kwargs.get("metadata", {})
        assert metadata.get("enduser_id") == "enduser-xyz"

    def test_trace_run_start_without_enduser_id_omits_metadata_key(self):
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="agent-2",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{}',
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert "enduser_id" not in metadata


# ── trace_chat / trace_run_start 的 channel_type 透传 ──────────────


class TestTraceChatChannelType:
    """trace_chat 的 channel_type 参数透传到 trace.metadata.channel_type。"""

    def test_trace_chat_with_channel_type_writes_to_metadata(self):
        """channel_type 非空 → lf.trace(metadata.channel_type=...) 被调用。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{"messages":[]}',
                channel_type="feishu",
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert metadata.get("channel_type") == "feishu"

    def test_trace_chat_without_channel_type_omits_metadata_key(self):
        """channel_type=None → metadata 不含 channel_type 键（其他 metadata 仍存在）。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{}',
                channel_type=None,
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert "channel_type" not in metadata
        assert metadata.get("engine_type") == "HERMES"

    def test_trace_chat_with_empty_channel_type_omits_metadata_key(self):
        """channel_type="" 空字符串 → 同样不传。"""
        from app.langfuse_client import trace_chat

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{}',
                channel_type="",
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert "channel_type" not in metadata


class TestTraceRunStartChannelType:
    """trace_run_start 的 channel_type 参数透传到 trace.metadata.channel_type。"""

    def test_trace_run_start_with_channel_type_writes_to_metadata(self):
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="agent-2",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{"input":"hi"}',
                channel_type="web",
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert metadata.get("channel_type") == "web"

    def test_trace_run_start_without_channel_type_omits_metadata_key(self):
        from app.langfuse_client import trace_run_start

        lf = MagicMock()
        trace = MagicMock()
        lf.trace.return_value = trace
        trace.generation.return_value = MagicMock()

        with patch("app.langfuse_client.get_langfuse", return_value=lf):
            trace_run_start(
                agent_id="agent-2",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{}',
            )

        _, kwargs = lf.trace.call_args
        metadata = kwargs.get("metadata", {})
        assert "channel_type" not in metadata


# ── finalize_chat_from_sse / finalize_chat_from_body 公共 helper ─────


class TestFinalizeChatFromSse:
    """finalize_chat_from_sse 从 SSE 原始文本提取 text + usage 调 finalize_chat。"""

    def test_finalizes_with_extracted_text_and_usage(self):
        from app.langfuse_client import finalize_chat_from_sse

        trace = MagicMock()
        generation = MagicMock()

        raw_sse = (
            'data: {"choices":[{"delta":{"content":"hello"}}]}\n'
            'data: {"choices":[{"delta":{"content":" world"}}]}\n'
            'data: {"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}\n'
            'data: [DONE]\n'
        )

        with patch("app.langfuse_client.finalize_chat") as mock_finalize:
            finalize_chat_from_sse(trace, generation, raw_sse, end_time="end", completion_start_time="ttft")

        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        assert args[0] is trace
        assert args[1] is generation
        # 提取的文本应是 "hello world"
        assert args[2] == "hello world"
        assert kwargs.get("usage") == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        assert kwargs.get("end_time") == "end"
        assert kwargs.get("completion_start_time") == "ttft"

    def test_none_trace_and_generation_is_noop(self):
        """trace 和 generation 都 None → 不调 finalize_chat。"""
        from app.langfuse_client import finalize_chat_from_sse

        with patch("app.langfuse_client.finalize_chat") as mock_finalize:
            finalize_chat_from_sse(None, None, "data: whatever", end_time="end")

        mock_finalize.assert_not_called()

    def test_empty_raw_sse_passes_none_text(self):
        """raw_sse=None → 提取的 text 也是 None，仍调 finalize_chat。"""
        from app.langfuse_client import finalize_chat_from_sse

        trace = MagicMock()
        generation = MagicMock()

        with patch("app.langfuse_client.finalize_chat") as mock_finalize:
            finalize_chat_from_sse(trace, generation, None, end_time="end")

        mock_finalize.assert_called_once()
        args, _ = mock_finalize.call_args
        assert args[2] is None  # extracted text


class TestFinalizeChatFromBody:
    """finalize_chat_from_body 从非流式 JSON 响应体提取 text + usage。"""

    def test_finalizes_with_extracted_text_and_usage(self):
        from app.langfuse_client import finalize_chat_from_body

        trace = MagicMock()
        generation = MagicMock()

        body = (
            b'{"choices":[{"message":{"content":"hi there"}}],'
            b'"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}'
        )

        with patch("app.langfuse_client.finalize_chat") as mock_finalize:
            finalize_chat_from_body(trace, generation, body, end_time="end")

        mock_finalize.assert_called_once()
        args, kwargs = mock_finalize.call_args
        assert args[0] is trace
        assert args[1] is generation
        assert args[2] == "hi there"
        assert kwargs.get("usage") == {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
        assert kwargs.get("end_time") == "end"

    def test_none_trace_and_generation_is_noop(self):
        from app.langfuse_client import finalize_chat_from_body

        with patch("app.langfuse_client.finalize_chat") as mock_finalize:
            finalize_chat_from_body(None, None, b'{}', end_time="end")

        mock_finalize.assert_not_called()


# ── DifyAdapter 的 conversation_id → session_id 桥接 ──────────────


class TestDifyConversationIdToSessionId:
    """DifyAdapter 在 message/agent_message 事件里把 conversation_id 写入 trace.session_id。"""

    def _make_adapter(self) -> DifyAdapter:
        adapter = DifyAdapter(k8s_namespace="unionagents")
        adapter._app_type = "chat"
        adapter._langfuse_trace = MagicMock()
        return adapter

    def test_message_event_with_conversation_id_updates_trace(self):
        """message 事件带 conversation_id → trace.update(session_id=...) 被调用一次。"""
        adapter = self._make_adapter()
        block = (
            'data: {"event":"message","id":"m1","conversation_id":"conv-123","answer":"hi"}'
        )
        adapter._convert_dify_event_block(block, {})
        adapter._langfuse_trace.update.assert_called_once_with(session_id="conv-123")

    def test_agent_message_event_with_conversation_id_updates_trace(self):
        """agent_message 事件同样触发 session_id 更新。"""
        adapter = self._make_adapter()
        adapter._app_type = "agent"
        block = (
            'data: {"event":"agent_message","id":"m2","conversation_id":"conv-456","answer":"hello"}'
        )
        adapter._convert_dify_event_block(block, {})
        adapter._langfuse_trace.update.assert_called_once_with(session_id="conv-456")

    def test_message_event_empty_conversation_id_no_update(self):
        """conversation_id 为空字符串 → 不调用 trace.update。"""
        adapter = self._make_adapter()
        block = 'data: {"event":"message","id":"m3","conversation_id":"","answer":"x"}'
        adapter._convert_dify_event_block(block, {})
        adapter._langfuse_trace.update.assert_not_called()

    def test_message_event_missing_conversation_id_no_update(self):
        """payload 不含 conversation_id 字段 → 不调用 trace.update。"""
        adapter = self._make_adapter()
        block = 'data: {"event":"message","id":"m4","answer":"x"}'
        adapter._convert_dify_event_block(block, {})
        adapter._langfuse_trace.update.assert_not_called()

    def test_second_message_event_does_not_re_update_session_id(self):
        """同 SSE 流里第二次 message 事件再带相同 conversation_id → 不重复调用 update（首次已写入）。"""
        adapter = self._make_adapter()
        block1 = 'data: {"event":"message","id":"m5","conversation_id":"conv-789","answer":"a"}'
        block2 = 'data: {"event":"message","id":"m6","conversation_id":"conv-789","answer":"b"}'
        state: dict[str, Any] = {}
        adapter._convert_dify_event_block(block1, state)
        adapter._convert_dify_event_block(block2, state)
        adapter._langfuse_trace.update.assert_called_once_with(session_id="conv-789")

    def test_no_langfuse_trace_no_raise(self):
        """adapter._langfuse_trace = None（Langfuse 未启用）→ 不抛异常，正常返回 OpenAI chunk。"""
        adapter = DifyAdapter(k8s_namespace="unionagents")
        adapter._app_type = "chat"
        adapter._langfuse_trace = None
        block = 'data: {"event":"message","id":"m7","conversation_id":"conv-x","answer":"hi"}'
        out = adapter._convert_dify_event_block(block, {})
        assert "hi" in out

    def test_trace_update_exception_does_not_break_sse(self):
        """trace.update 抛异常 → 被吞掉，OpenAI chunk 仍正常返回。"""
        adapter = self._make_adapter()
        adapter._langfuse_trace.update.side_effect = RuntimeError("langfuse down")
        block = 'data: {"event":"message","id":"m8","conversation_id":"conv-y","answer":"hi"}'
        out = adapter._convert_dify_event_block(block, {})
        assert "hi" in out

    def test_workflow_text_chunk_does_not_set_session_id(self):
        """workflow 模式的 text_chunk 事件不含 conversation_id，不应触发 session_id 更新。"""
        adapter = self._make_adapter()
        adapter._app_type = "workflow"
        block = 'data: {"event":"text_chunk","data":{"text":"hi"}}'
        adapter._convert_dify_event_block(block, {})
        adapter._langfuse_trace.update.assert_not_called()
