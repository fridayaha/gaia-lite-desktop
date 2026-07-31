"""finalize_chat / trace_run_end 的 output 包装单元测试。

验证：非空字符串 output 被包装成 {"text": ...} dict，避免 Langfuse SDK 2.x
把字符串误判为 LangfuseMedia 触发 "Upload handling failed" 错误。
"""

from unittest.mock import MagicMock, patch

import pytest


class TestFinalizeChatOutputWrapping:
    """finalize_chat 的 output 包装逻辑。"""

    def test_non_empty_string_output_wrapped_as_dict(self):
        """非空字符串 output → generation.end(output={"text": ...})。"""
        from app.langfuse_client import finalize_chat

        trace = MagicMock()
        generation = MagicMock()
        finalize_chat(trace, generation, "hello world")

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") == {"text": "hello world"}
        # trace.update 也应收到 dict
        _, trace_kwargs = trace.update.call_args
        assert trace_kwargs.get("output") == {"text": "hello world"}

    def test_empty_string_output_becomes_none(self):
        """空字符串 output → output=None（不创建 media 字段）。"""
        from app.langfuse_client import finalize_chat

        trace = MagicMock()
        generation = MagicMock()
        finalize_chat(trace, generation, "")

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") is None

    def test_whitespace_only_output_becomes_none(self):
        """纯空白 output → None。"""
        from app.langfuse_client import finalize_chat

        trace = MagicMock()
        generation = MagicMock()
        finalize_chat(trace, generation, "   \n  ")

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") is None

    def test_none_output_stays_none(self):
        """None output → None。"""
        from app.langfuse_client import finalize_chat

        trace = MagicMock()
        generation = MagicMock()
        finalize_chat(trace, generation, None)

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") is None

    def test_output_with_usage_and_end_time(self):
        """output + usage + end_time 同时传递，dict 包装不影响其他字段。"""
        from datetime import UTC, datetime

        from app.langfuse_client import finalize_chat

        trace = MagicMock()
        generation = MagicMock()
        end_time = datetime.now(UTC)
        finalize_chat(
            trace,
            generation,
            "response text",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            end_time=end_time,
        )

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") == {"text": "response text"}
        assert kwargs.get("usage") == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        assert kwargs.get("end_time") == end_time


class TestTraceRunEndOutputWrapping:
    """trace_run_end 的 output 包装逻辑（从 SSE buffer 提取文本后同样包装成 dict）。"""

    def test_extracted_text_wrapped_as_dict(self):
        """SSE buffer 提取的文本 → output={"text": ...}。"""
        from app.langfuse_client import _run_traces, trace_run_end

        trace = MagicMock()
        generation = MagicMock()
        # 模拟 SSE buffer 含 OpenAI delta 文本
        sse_buffer = [
            'data: {"delta":"hello"}\n\n',
            'data: {"delta":" world"}\n\n',
            'data: [DONE]\n\n',
        ]
        _run_traces["run-test-1"] = (trace, generation, list(sse_buffer), 0.0, None)

        trace_run_end("run-test-1")

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") == {"text": "hello world"}

    def test_empty_buffer_output_none(self):
        """空 buffer → output=None。"""
        from app.langfuse_client import _run_traces, trace_run_end

        trace = MagicMock()
        generation = MagicMock()
        _run_traces["run-test-2"] = (trace, generation, [], 0.0, None)

        trace_run_end("run-test-2")

        _, kwargs = generation.end.call_args
        assert kwargs.get("output") is None

    def test_no_usable_text_output_none(self):
        """SSE 只有 [DONE] 无 delta → output=None。"""
        from app.langfuse_client import _run_traces, trace_run_end

        trace = MagicMock()
        generation = MagicMock()
        _run_traces["run-test-3"] = (trace, generation, ['data: [DONE]\n\n'], 0.0, None)

        trace_run_end("run-test-3")

        _, kwargs = generation.end.call_args
        # _extract_text_from_sse 对无 delta 的情况返回 None（不返回 raw 避免 media 误判）
        assert kwargs.get("output") is None


class TestExtractTextFromSse:
    """_extract_text_from_sse 对两种 SSE 格式的解析。"""

    def test_hermes_delta_string(self):
        """Hermes 格式：delta 是字符串。"""
        from app.langfuse_client import _extract_text_from_sse

        raw = (
            'data: {"event":"message.delta","delta":"hello"}\n\n'
            'data: {"event":"message.delta","delta":" world"}\n\n'
            'data: [DONE]\n\n'
        )
        assert _extract_text_from_sse(raw) == "hello world"

    def test_openai_chunk_delta_dict(self):
        """OpenAI 格式：delta 是 dict，取 delta.content。"""
        from app.langfuse_client import _extract_text_from_sse

        raw = (
            'data: {"id":"","object":"chat.completion.chunk","choices":[{"delta":{"content":"你好"},"index":0}]}\n\n'
            'data: {"id":"","object":"chat.completion.chunk","choices":[{"delta":{"content":"世界"},"index":0}]}\n\n'
            'data: [DONE]\n\n'
        )
        assert _extract_text_from_sse(raw) == "你好世界"

    def test_openai_chunk_role_only_delta_skipped(self):
        """OpenAI 首 chunk 通常只有 role 无 content → 跳过，不报错。"""
        from app.langfuse_client import _extract_text_from_sse

        raw = (
            'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}\n\n'
            'data: {"choices":[{"delta":{"content":"hi"},"index":0}]}\n\n'
        )
        assert _extract_text_from_sse(raw) == "hi"

    def test_no_delta_returns_none_not_raw(self):
        """提取不到文本返回 None，不返回 raw（避免 SDK 把 'data: ...' 当 media 误判）。"""
        from app.langfuse_client import _extract_text_from_sse

        raw = 'data: {"choices":[{"delta":{},"index":0}]}\n\ndata: [DONE]\n\n'
        # 关键：返回 None，不是 raw[:4000]（以 "data: " 开头会触发 SDK media 误判）
        assert _extract_text_from_sse(raw) is None

    def test_empty_raw_returns_none(self):
        from app.langfuse_client import _extract_text_from_sse

        assert _extract_text_from_sse("") is None
        assert _extract_text_from_sse(None) is None  # type: ignore[arg-type]


class TestExtractUsageFromSse:
    """_extract_usage_from_sse 对 OpenAI usage chunk 和 Dify metadata.usage 的提取。"""

    def test_openai_usage_chunk_at_end(self):
        """OpenAI 兼容 SSE 末尾 usage chunk → 提取三字段。"""
        from app.langfuse_client import _extract_usage_from_sse

        raw = (
            'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":10,"total_tokens":15}}\n\n'
            'data: [DONE]\n\n'
        )
        u = _extract_usage_from_sse(raw)
        assert u == {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15}

    def test_dify_metadata_usage_path(self):
        """Dify metadata.usage 路径 → 提取。"""
        from app.langfuse_client import _extract_usage_from_sse

        raw = (
            'data: {"event":"message_end","metadata":{"usage":'
            '{"prompt_tokens":7,"completion_tokens":3,"total_tokens":10}}}\n\n'
        )
        u = _extract_usage_from_sse(raw)
        assert u == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}

    def test_workflow_total_tokens_only(self):
        """Dify workflow 只有 data.total_tokens 顶层字段（无 prompt/completion 拆分）。
        此场景由 DifyAdapter 转成 OpenAI usage chunk（prompt=0, completion=0, total=N），
        _extract_usage_from_sse 应正确提取 total_tokens。"""
        from app.langfuse_client import _extract_usage_from_sse

        raw = (
            'data: {"choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":146}}\n\n'
            'data: [DONE]\n\n'
        )
        u = _extract_usage_from_sse(raw)
        assert u == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 146}

    def test_large_content_then_usage_chunk(self):
        """长响应（超 8KB）+ 末尾 usage chunk → usage 仍能提取。
        模拟 chat_chunks 累积场景：8KB 限制只对内容生效，usage chunk 总是累积。"""
        from app.langfuse_client import _extract_usage_from_sse

        # 构造 ~10KB 内容 + 末尾 usage chunk
        big_content = "x" * 10000
        raw = (
            f'data: {{"choices":[{{"delta":{{"content":"{big_content}"}}}}]}}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":177}}\n\n'
            'data: [DONE]\n\n'
        )
        u = _extract_usage_from_sse(raw)
        assert u == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 177}

    def test_multiple_usage_chunks_last_wins(self):
        """多个 usage 事件 → 取最后一个（OpenAI 规范：末尾 usage 是最终值）。"""
        from app.langfuse_client import _extract_usage_from_sse

        raw = (
            'data: {"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}\n\n'
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":10,"total_tokens":15}}\n\n'
        )
        u = _extract_usage_from_sse(raw)
        assert u == {"prompt_tokens": 5, "completion_tokens": 10, "total_tokens": 15}

    def test_no_usage_returns_none(self):
        """无 usage 事件 → None。"""
        from app.langfuse_client import _extract_usage_from_sse

        raw = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        assert _extract_usage_from_sse(raw) is None

    def test_all_zero_usage_returns_none(self):
        """usage 全 0 → None（_normalize_usage 视为无 usage 数据）。"""
        from app.langfuse_client import _extract_usage_from_sse

        raw = 'data: {"usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}\n\n'
        assert _extract_usage_from_sse(raw) is None

    def test_empty_raw_returns_none(self):
        from app.langfuse_client import _extract_usage_from_sse

        assert _extract_usage_from_sse("") is None
        assert _extract_usage_from_sse(None) is None  # type: ignore[arg-type]


class TestExtractTextFromBody:
    """_extract_text_from_body：从非流式 OpenAI 兼容 JSON 响应提取 assistant 文本。"""

    def test_normal_response_extracts_content(self):
        from app.langfuse_client import _extract_text_from_body

        body = (
            b'{"id":"chatcmpl-1","model":"m1","choices":[{"index":0,'
            b'"message":{"role":"assistant","content":"hello world"},'
            b'"finish_reason":"stop"}],"usage":{"prompt_tokens":1,'
            b'"completion_tokens":2,"total_tokens":3}}'
        )
        assert _extract_text_from_body(body) == "hello world"

    def test_missing_choices_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = b'{"id":"x","model":"m","usage":{}}'
        assert _extract_text_from_body(body) is None

    def test_empty_choices_array_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = b'{"id":"x","model":"m","choices":[]}'
        assert _extract_text_from_body(body) is None

    def test_empty_content_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = (
            b'{"choices":[{"message":{"role":"assistant","content":""}}]}'
        )
        assert _extract_text_from_body(body) is None

    def test_missing_message_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = b'{"choices":[{"index":0}]}'
        assert _extract_text_from_body(body) is None

    def test_non_string_content_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = b'{"choices":[{"message":{"role":"assistant","content":null}}]}'
        assert _extract_text_from_body(body) is None

    def test_long_content_truncated_to_4000(self):
        from app.langfuse_client import _extract_text_from_body

        long_text = "x" * 5000
        body = (
            b'{"choices":[{"message":{"role":"assistant","content":"'
            + long_text.encode()
            + b'"}}]}'
        )
        result = _extract_text_from_body(body)
        assert result is not None
        assert len(result) == 4000

    def test_malformed_json_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = b'not json'
        assert _extract_text_from_body(body) is None

    def test_empty_body_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        assert _extract_text_from_body(b"") is None
        assert _extract_text_from_body(None) is None  # type: ignore[arg-type]

    def test_non_object_json_returns_none(self):
        from app.langfuse_client import _extract_text_from_body

        body = b'[1,2,3]'
        assert _extract_text_from_body(body) is None


class TestExtractModelFromBody:
    """_extract_model_from_body：从响应体提取 model 字段（Hermes 响应有真实 model 名）。"""

    def test_normal_response_extracts_model(self):
        from app.langfuse_client import _extract_model_from_body

        body = b'{"id":"chatcmpl-1","model":"98a9fc14-f6df1d-29000329","choices":[]}'
        assert _extract_model_from_body(body) == "98a9fc14-f6df1d-29000329"

    def test_missing_model_returns_none(self):
        from app.langfuse_client import _extract_model_from_body

        body = b'{"id":"x","choices":[]}'
        assert _extract_model_from_body(body) is None

    def test_empty_model_string_returns_none(self):
        from app.langfuse_client import _extract_model_from_body

        body = b'{"id":"x","model":"","choices":[]}'
        assert _extract_model_from_body(body) is None

    def test_non_string_model_returns_none(self):
        from app.langfuse_client import _extract_model_from_body

        body = b'{"id":"x","model":123}'
        assert _extract_model_from_body(body) is None

    def test_malformed_json_returns_none(self):
        from app.langfuse_client import _extract_model_from_body

        body = b'not json'
        assert _extract_model_from_body(body) is None

    def test_empty_body_returns_none(self):
        from app.langfuse_client import _extract_model_from_body

        assert _extract_model_from_body(b"") is None
        assert _extract_model_from_body(None) is None  # type: ignore[arg-type]

