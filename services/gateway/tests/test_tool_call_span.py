"""OpenAI SSE tool_calls delta + Hermes tool.progress → Langfuse SPAN observation 单元测试。

覆盖：
  - _parse_tool_calls_from_sse_chunk：OpenAI delta.tool_calls 解析、非 data 行跳过、JSON 残缺跳过、无 tool_calls 字段跳过
  - update_tool_call_span：OpenAI 格式 - 首次创建 / 后续累积 / function_name 缓存 / index fallback key
  - end_tool_call_spans：流结束统一 end（跳过已 end 的）
  - _parse_hermes_tool_progress_from_chunk：Hermes event: hermes.tool.progress 解析
  - update_hermes_tool_span：Hermes 格式 - running 创建 / completed 立即 end
  - trace=None 优雅降级
"""

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.langfuse_client import (
    _parse_hermes_tool_progress_from_chunk,
    _parse_tool_calls_from_sse_chunk,
    end_tool_call_spans,
    update_hermes_tool_span,
    update_tool_call_span,
)


# ── SSE 样本 chunks 构造器 ────────────────────────────────────


def _make_tool_call_chunk(
    *,
    index: int = 0,
    tool_call_id: str | None = None,
    function_name: str | None = None,
    arguments_delta: str | None = None,
) -> str:
    """构造 OpenAI 兼容 chat.completion.chunk SSE 行。

    用 json.dumps 生成 payload 避免手写 JSON 转义出错。
    """
    delta_tc: dict[str, Any] = {"index": index}
    if tool_call_id is not None:
        delta_tc["id"] = tool_call_id
        delta_tc["type"] = "function"
    if function_name is not None or arguments_delta is not None:
        fn: dict[str, Any] = {}
        if function_name is not None:
            fn["name"] = function_name
        if arguments_delta is not None:
            fn["arguments"] = arguments_delta
        delta_tc["function"] = fn
    payload = {"choices": [{"delta": {"tool_calls": [delta_tc]}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _make_text_chunk(content: str = "hello") -> str:
    """构造普通 text delta SSE 行（无 tool_calls）。"""
    payload = {"choices": [{"delta": {"content": content}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 跨 3 chunk 的 tool_call（arguments 拼接成 '{"loc":"北京"}'）
# 切分: '{"loc' + '":"' + '北京"}'  = '{"loc":"北京"}'
TOOL_CALL_CHUNK_1 = _make_tool_call_chunk(
    index=0,
    tool_call_id="call_abc",
    function_name="get_weather",
    arguments_delta='{"loc',
)
TOOL_CALL_CHUNK_2 = _make_tool_call_chunk(index=0, arguments_delta='":"')
TOOL_CALL_CHUNK_3 = _make_tool_call_chunk(index=0, arguments_delta='北京"}')
# 累积后 arguments = '{"loc":"北京"}' → json.loads == {"loc": "北京"}

# 单 chunk 完整 tool_call
SINGLE_CHUNK_TOOL_CALL = _make_tool_call_chunk(
    index=0,
    tool_call_id="call_abc",
    function_name="get_weather",
    arguments_delta='{"loc":"北京"}',
)

# 多个 tool_call 交错 chunk
MULTI_TOOL_CHUNK_1 = _make_tool_call_chunk(
    index=0,
    tool_call_id="call_a",
    function_name="get_weather",
    arguments_delta='{"loc',
)
MULTI_TOOL_CHUNK_2 = _make_tool_call_chunk(
    index=1,
    tool_call_id="call_b",
    function_name="get_time",
    arguments_delta='{"tz',
)
MULTI_TOOL_CHUNK_3 = _make_tool_call_chunk(index=0, arguments_delta='":"北京"}')
MULTI_TOOL_CHUNK_4 = _make_tool_call_chunk(index=1, arguments_delta='":"UTC"}')
# call_a args = '{"loc":"北京"}'
# call_b args = '{"tz":"UTC"}'

# 普通 text delta chunk（无 tool_calls）
TEXT_DELTA_CHUNK = _make_text_chunk("hello")

# event: ping 行（非 data）
EVENT_PING_CHUNK = "event: ping\n\n"

# data: [DONE] 行
DONE_CHUNK = "data: [DONE]\n\n"


# ── _parse_tool_calls_from_sse_chunk 单元测试 ────────────────


class TestParseToolCallsFromSseChunk:
    """SSE chunk → tool_call delta 字典解析。"""

    def test_single_chunk_single_tool_call(self):
        """单 chunk 含完整 tool_call → 返回 1 个解析结果。"""
        results = _parse_tool_calls_from_sse_chunk(SINGLE_CHUNK_TOOL_CALL)
        assert len(results) == 1
        r = results[0]
        assert r["index"] == 0
        assert r["tool_call_id"] == "call_abc"
        assert r["function_name"] == "get_weather"
        assert r["arguments_delta"] == '{"loc":"北京"}'

    def test_multi_chunk_tool_call_arguments_accumulate(self):
        """3 chunk 拼出完整 arguments（每 chunk 只返回该 chunk 的 delta）。"""
        r1 = _parse_tool_calls_from_sse_chunk(TOOL_CALL_CHUNK_1)
        r2 = _parse_tool_calls_from_sse_chunk(TOOL_CALL_CHUNK_2)
        r3 = _parse_tool_calls_from_sse_chunk(TOOL_CALL_CHUNK_3)

        assert r1[0]["arguments_delta"] == '{"loc'
        assert r1[0]["function_name"] == "get_weather"
        assert r1[0]["tool_call_id"] == "call_abc"
        # chunk 2/3 不带 function_name / tool_call_id（OpenAI 协议）
        assert r2[0]["function_name"] is None
        assert r2[0]["tool_call_id"] is None
        assert r2[0]["arguments_delta"] == '":"'
        assert r3[0]["function_name"] is None
        assert r3[0]["tool_call_id"] is None
        assert r3[0]["arguments_delta"] == '北京"}'

        # 拼接后是合法 JSON
        merged = r1[0]["arguments_delta"] + r2[0]["arguments_delta"] + r3[0]["arguments_delta"]
        assert json.loads(merged) == {"loc": "北京"}

    def test_multi_tool_calls_interleaved(self):
        """2 个 tool_call 交错 chunk：每 chunk 解析返回对应的 index。"""
        r1 = _parse_tool_calls_from_sse_chunk(MULTI_TOOL_CHUNK_1)
        r2 = _parse_tool_calls_from_sse_chunk(MULTI_TOOL_CHUNK_2)
        r3 = _parse_tool_calls_from_sse_chunk(MULTI_TOOL_CHUNK_3)
        r4 = _parse_tool_calls_from_sse_chunk(MULTI_TOOL_CHUNK_4)

        assert r1[0]["index"] == 0 and r1[0]["function_name"] == "get_weather"
        assert r2[0]["index"] == 1 and r2[0]["function_name"] == "get_time"
        assert r3[0]["index"] == 0 and r3[0]["function_name"] is None
        assert r4[0]["index"] == 1 and r4[0]["function_name"] is None

    def test_text_delta_chunk_no_tool_calls(self):
        """普通 text delta chunk → 返回空列表。"""
        results = _parse_tool_calls_from_sse_chunk(TEXT_DELTA_CHUNK)
        assert results == []

    def test_event_line_skipped(self):
        """非 data 行（event: ping）→ 返回空列表。"""
        results = _parse_tool_calls_from_sse_chunk(EVENT_PING_CHUNK)
        assert results == []

    def test_done_chunk_skipped(self):
        """data: [DONE] 行 → 返回空列表（不当作 JSON 解析）。"""
        results = _parse_tool_calls_from_sse_chunk(DONE_CHUNK)
        assert results == []

    def test_empty_chunk(self):
        """空字符串输入 → 返回空列表。"""
        assert _parse_tool_calls_from_sse_chunk("") == []

    def test_invalid_json_skipped(self):
        """JSON 残缺 → 跳过该行，不抛异常。"""
        broken = "data: {not valid json\n\n"
        assert _parse_tool_calls_from_sse_chunk(broken) == []

    def test_multiple_data_lines_in_one_chunk(self):
        """单 chunk 含多行 data: → 每行都解析。"""
        chunk = SINGLE_CHUNK_TOOL_CALL + TEXT_DELTA_CHUNK
        results = _parse_tool_calls_from_sse_chunk(chunk)
        # 第 1 行 1 个 tool_call，第 2 行 text delta 无 tool_calls
        assert len(results) == 1


# ── update_tool_call_span 单元测试 ───────────────────────────


class TestUpdateToolCallSpan:
    """tool_call SPAN 创建/累积逻辑。"""

    def test_trace_none_graceful_no_op(self):
        """trace=None（Langfuse 未启用）时不崩、不创建 span。"""
        spans: dict[str, Any] = {}
        update_tool_call_span(
            None,
            spans,
            {
                "index": 0,
                "tool_call_id": "call_abc",
                "function_name": "get_weather",
                "arguments_delta": "{}",
            },
        )
        assert spans == {}

    def test_first_chunk_creates_span(self):
        """首次见到 index：创建 span，input=arguments_delta，metadata 含 id/name。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": "call_abc",
                "function_name": "get_weather",
                "arguments_delta": '{"loc',
            },
        )

        trace.span.assert_called_once()
        _, kwargs = trace.span.call_args
        assert kwargs["name"] == "tool_call: get_weather"
        assert kwargs["input"] == '{"loc'
        assert kwargs["metadata"]["tool_call_id"] == "call_abc"
        assert kwargs["metadata"]["function_name"] == "get_weather"
        assert kwargs["metadata"]["index"] == 0
        # 创建时不立即 end
        span.end.assert_not_called()
        # key="0"（index 字符串），span + args + function_name 缓存
        assert spans["0"]["span"] is span
        assert spans["0"]["args"] == '{"loc'
        assert spans["0"]["function_name"] == "get_weather"

    def test_subsequent_chunks_accumulate_arguments(self):
        """同 index 后续 chunk（tool_call_id 缺失）：不新建 span，update input=累积 args。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        # chunk 1 创建
        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": "call_abc",
                "function_name": "get_weather",
                "arguments_delta": '{"loc',
            },
        )
        # chunk 2 累积
        update_tool_call_span(
            trace,
            spans,
            {"index": 0, "tool_call_id": None, "function_name": None, "arguments_delta": '":"'},
        )
        # chunk 3 累积
        update_tool_call_span(
            trace,
            spans,
            {"index": 0, "tool_call_id": None, "function_name": None, "arguments_delta": '北京"}'},
        )

        # 只创建一次
        trace.span.assert_called_once()
        # update 两次（chunk 2 和 chunk 3）
        assert span.update.call_count == 2
        # 最后一次 update 的 input 是累积完整 args
        _, last_kwargs = span.update.call_args
        assert last_kwargs["input"] == '{"loc":"北京"}'
        # span 缓存的 args 也累积到完整
        assert spans["0"]["args"] == '{"loc":"北京"}'
        # 仍不 end（流结束才 end）
        span.end.assert_not_called()

    def test_function_name_cached_from_first_chunk(self):
        """function_name 只在首 chunk 出现，后续 chunk 不丢失（用 spans dict 缓存）。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": "call_abc",
                "function_name": "get_weather",
                "arguments_delta": "",
            },
        )
        # 后续 chunk function_name 为 None
        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": None,
                "function_name": None,
                "arguments_delta": '{"q":"x"}',
            },
        )

        # span 已缓存 function_name
        assert spans["0"]["function_name"] == "get_weather"
        # update 时 input 用累积 args
        _, kwargs = span.update.call_args
        assert kwargs["input"] == '{"q":"x"}'

    def test_no_id_falls_back_to_index_key(self):
        """tool_call_id 缺失但 index 存在：用 index 字符串做 key。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_tool_call_span(
            trace,
            spans,
            {
                "index": 2,
                "tool_call_id": None,
                "function_name": "unknown_tool",
                "arguments_delta": "{}",
            },
        )

        assert "2" in spans
        assert spans["2"]["function_name"] == "unknown_tool"

    def test_empty_arguments_delta_does_not_update(self):
        """后续 chunk arguments_delta 为空：不 update（无内容可累积）。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {"0": {"span": span, "args": '{"loc', "function_name": "f"}}

        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": "call_abc",
                "function_name": None,
                "arguments_delta": None,
            },
        )

        span.update.assert_not_called()

    def test_multiple_tool_calls_parallel(self):
        """2 个不同 tool_call 并行（index=0/1）：独立累积，互不干扰。"""
        trace = MagicMock()
        span_a, span_b = MagicMock(), MagicMock()
        trace.span.side_effect = [span_a, span_b]
        spans: dict[str, Any] = {}

        # call_a chunk 1
        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": "call_a",
                "function_name": "get_weather",
                "arguments_delta": '{"loc',
            },
        )
        # call_b chunk 1
        update_tool_call_span(
            trace,
            spans,
            {
                "index": 1,
                "tool_call_id": "call_b",
                "function_name": "get_time",
                "arguments_delta": '{"tz',
            },
        )
        # call_a chunk 2
        update_tool_call_span(
            trace,
            spans,
            {
                "index": 0,
                "tool_call_id": None,
                "function_name": None,
                "arguments_delta": '":"北京"}',
            },
        )
        # call_b chunk 2
        update_tool_call_span(
            trace,
            spans,
            {
                "index": 1,
                "tool_call_id": None,
                "function_name": None,
                "arguments_delta": '":"UTC"}',
            },
        )

        # 两个独立 span
        assert trace.span.call_count == 2
        assert spans["0"]["args"] == '{"loc":"北京"}'
        assert spans["1"]["args"] == '{"tz":"UTC"}'
        # 每个 span 各 update 1 次
        assert span_a.update.call_count == 1
        assert span_b.update.call_count == 1


# ── end_tool_call_spans 单元测试 ────────────────────────────


class TestEndToolCallSpans:
    """流结束统一 end 所有 tool_call SPAN。"""

    def test_end_all_spans(self):
        """2 个 span：end 时各自 update final args + end。"""
        span_a, span_b = MagicMock(), MagicMock()
        spans: dict[str, Any] = {
            "0": {"span": span_a, "args": '{"loc":"北京"}', "function_name": "f"},
            "1": {"span": span_b, "args": '{"tz":"UTC"}', "function_name": "g"},
        }

        end_tool_call_spans(spans)

        # 每个 span 各 update + end
        span_a.update.assert_called_once()
        _, kwargs_a = span_a.update.call_args
        assert kwargs_a["input"] == '{"loc":"北京"}'
        span_a.end.assert_called_once()

        span_b.update.assert_called_once()
        _, kwargs_b = span_b.update.call_args
        assert kwargs_b["input"] == '{"tz":"UTC"}'
        span_b.end.assert_called_once()

    def test_end_with_no_spans_no_op(self):
        """spans 为空 dict：不抛异常。"""
        end_tool_call_spans({})
        # 不崩即通过

    def test_end_skips_none_span(self):
        """span 为 None 的 entry：跳过，不抛。"""
        spans: dict[str, Any] = {"x": {"span": None, "args": "", "function_name": "f"}}
        end_tool_call_spans(spans)  # 不抛即通过

    def test_end_uses_final_accumulated_args(self):
        """end 时 input 用最终累积的 args（不是初始 delta）。"""
        span = MagicMock()
        spans: dict[str, Any] = {"0": {"span": span, "args": '{"q":"final"}', "function_name": "f"}}

        end_tool_call_spans(spans)

        _, kwargs = span.update.call_args
        assert kwargs["input"] == '{"q":"final"}'
        span.end.assert_called_once()


# ── 集成：解析 + 累积 + end 流程 ─────────────────────────────


class TestEndToEndFlow:
    """模拟一个完整 SSE 流：3 chunk tool_call + DONE，验证累积 + end。"""

    def test_three_chunk_tool_call_accumulates_and_ends(self):
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        for chunk in [TOOL_CALL_CHUNK_1, TOOL_CALL_CHUNK_2, TOOL_CALL_CHUNK_3]:
            for tc in _parse_tool_calls_from_sse_chunk(chunk):
                update_tool_call_span(trace, spans, tc)

        end_tool_call_spans(spans)

        # 只创建 1 个 span
        trace.span.assert_called_once()
        # 3 次 update：2 次来自 chunk 累积 + 1 次来自 end_tool_call_spans 设 end_time
        assert span.update.call_count == 3
        # end 1 次
        span.end.assert_called_once()
        # 最后一次 update 的 input 是累积完整 args（合法 JSON）
        _, end_kwargs = span.update.call_args
        assert json.loads(end_kwargs["input"]) == {"loc": "北京"}

    def test_trace_none_whole_flow_no_op(self):
        """trace=None 全程 no-op，不抛异常。"""
        spans: dict[str, Any] = {}
        for chunk in [TOOL_CALL_CHUNK_1, TOOL_CALL_CHUNK_2, TOOL_CALL_CHUNK_3]:
            for tc in _parse_tool_calls_from_sse_chunk(chunk):
                update_tool_call_span(None, spans, tc)
        end_tool_call_spans(spans)
        assert spans == {}


# ── Hermes tool.progress 格式 SSE 样本 ──────────────────────


def _make_hermes_progress_chunk(
    *,
    tool_call_id: str = "call_abc",
    status: str = "running",
    tool: str = "skill_view",
    label: str | None = "customer-profile-update",
    emoji: str | None = None,
) -> str:
    """构造 Hermes hermes.tool.progress SSE 事件块。

    Hermes 不发 OpenAI 标准 delta.tool_calls，发自定义 event:
        event: hermes.tool.progress
        data: {"tool":"skill_view","label":"...","toolCallId":"call_xxx","status":"running"}

    每 tool 调用产生 2 个事件：running + completed。
    """
    payload: dict[str, Any] = {"tool": tool, "toolCallId": tool_call_id, "status": status}
    if label is not None:
        payload["label"] = label
    if emoji is not None:
        payload["emoji"] = emoji
    return f"event: hermes.tool.progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


HERMES_RUNNING_CHUNK = _make_hermes_progress_chunk(
    tool_call_id="call_lqj7",
    status="running",
    tool="skill_view",
    label="customer-profile-update",
    emoji="📚",
)
HERMES_COMPLETED_CHUNK = _make_hermes_progress_chunk(
    tool_call_id="call_lqj7",
    status="completed",
    tool="skill_view",
    label=None,  # completed 事件常省略 label
)
HERMES_TERMINAL_RUNNING = _make_hermes_progress_chunk(
    tool_call_id="call_jtdd",
    status="running",
    tool="terminal",
    label="mkdir -p .skill_tmp && python3 search.py --name 李伟",
)
HERMES_TERMINAL_COMPLETED = _make_hermes_progress_chunk(
    tool_call_id="call_jtdd",
    status="completed",
    tool="terminal",
    label=None,
)


# ── _parse_hermes_tool_progress_from_chunk 单元测试 ─────────


class TestParseHermesToolProgress:
    """Hermes event: hermes.tool.progress → event 字典解析。"""

    def test_running_event_parsed(self):
        """running 事件含 toolCallId → 返回 1 个解析结果。"""
        results = _parse_hermes_tool_progress_from_chunk(HERMES_RUNNING_CHUNK)
        assert len(results) == 1
        r = results[0]
        assert r["tool_call_id"] == "call_lqj7"
        assert r["status"] == "running"
        assert r["tool"] == "skill_view"
        assert r["label"] == "customer-profile-update"

    def test_completed_event_parsed(self):
        """completed 事件 → 返回 1 个解析结果。"""
        results = _parse_hermes_tool_progress_from_chunk(HERMES_COMPLETED_CHUNK)
        assert len(results) == 1
        assert results[0]["status"] == "completed"
        assert results[0]["label"] is None  # completed 事件常省略 label

    def test_openai_chunk_skipped(self):
        """普通 OpenAI chat.completion.chunk（无 toolCallId 字段）→ 返回空列表。"""
        results = _parse_hermes_tool_progress_from_chunk(SINGLE_CHUNK_TOOL_CALL)
        assert results == []

    def test_text_delta_chunk_skipped(self):
        """普通 text delta chunk → 返回空列表。"""
        results = _parse_hermes_tool_progress_from_chunk(TEXT_DELTA_CHUNK)
        assert results == []

    def test_event_line_without_data_skipped(self):
        """只有 event: 行没有 data: 行 → 返回空列表。"""
        results = _parse_hermes_tool_progress_from_chunk("event: hermes.tool.progress\n\n")
        assert results == []

    def test_empty_chunk(self):
        """空字符串 → 返回空列表。"""
        assert _parse_hermes_tool_progress_from_chunk("") == []

    def test_invalid_json_skipped(self):
        """data: JSON 残缺 → 跳过，不抛。"""
        broken = "event: hermes.tool.progress\ndata: {not valid\n\n"
        assert _parse_hermes_tool_progress_from_chunk(broken) == []

    def test_multiple_events_in_one_chunk(self):
        """单 chunk 含 running + completed 两个事件 → 都解析。"""
        chunk = HERMES_RUNNING_CHUNK + HERMES_COMPLETED_CHUNK
        results = _parse_hermes_tool_progress_from_chunk(chunk)
        assert len(results) == 2
        assert results[0]["status"] == "running"
        assert results[1]["status"] == "completed"

    def test_data_line_only_no_event_prefix(self):
        """dispatcher.py 按行 aiter_lines 调用时，data: 行无 event: 前缀也能解析
        （Hermes data 含 toolCallId 即识别）。"""
        data_line = 'data: {"tool":"skill_view","toolCallId":"call_x","status":"running"}'
        results = _parse_hermes_tool_progress_from_chunk(data_line)
        assert len(results) == 1
        assert results[0]["tool_call_id"] == "call_x"
        assert results[0]["status"] == "running"


# ── update_hermes_tool_span 单元测试 ─────────────────────────


class TestUpdateHermesToolSpan:
    """Hermes tool.progress SPAN 创建/end 逻辑。"""

    def test_trace_none_graceful_no_op(self):
        """trace=None 时不崩、不创建 span。"""
        spans: dict[str, Any] = {}
        update_hermes_tool_span(
            None,
            spans,
            {"tool_call_id": "call_x", "status": "running", "tool": "skill_view", "label": "f"},
        )
        assert spans == {}

    def test_running_event_creates_span(self):
        """status=running：创建 SPAN，input=""，metadata 含 tool_call_id/tool/label。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_hermes_tool_span(
            trace,
            spans,
            {
                "tool_call_id": "call_lqj7",
                "status": "running",
                "tool": "skill_view",
                "label": "customer-profile-update",
            },
        )

        trace.span.assert_called_once()
        _, kwargs = trace.span.call_args
        assert kwargs["name"] == "tool_call: customer-profile-update"
        assert kwargs["input"] == ""
        assert kwargs["metadata"]["tool_call_id"] == "call_lqj7"
        assert kwargs["metadata"]["tool"] == "skill_view"
        assert kwargs["metadata"]["label"] == "customer-profile-update"
        assert kwargs["metadata"]["source"] == "hermes.tool.progress"
        # 创建时不 end（等 completed 事件或流结束）
        span.end.assert_not_called()
        # span 缓存（key=tool_call_id）
        assert spans["call_lqj7"]["span"] is span
        assert spans["call_lqj7"]["function_name"] == "customer-profile-update"

    def test_completed_event_ends_span(self):
        """status=completed：立即 end SPAN，标记 ended=True。"""
        trace = MagicMock()
        span = MagicMock()
        spans: dict[str, Any] = {"call_lqj7": {"span": span, "args": "", "function_name": "f"}}

        update_hermes_tool_span(
            trace,
            spans,
            {
                "tool_call_id": "call_lqj7",
                "status": "completed",
                "tool": "skill_view",
                "label": None,
            },
        )

        span.end.assert_called_once()
        assert spans["call_lqj7"]["ended"] is True

    def test_completed_without_running_skipped(self):
        """status=completed 但没见过 running：忽略（不崩）。"""
        trace = MagicMock()
        spans: dict[str, Any] = {}

        update_hermes_tool_span(
            trace,
            spans,
            {
                "tool_call_id": "call_unknown",
                "status": "completed",
                "tool": "skill_view",
                "label": None,
            },
        )

        # 不创建也不 end
        trace.span.assert_not_called()

    def test_duplicate_running_skipped(self):
        """重复 running 事件：不重建 span。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_hermes_tool_span(
            trace,
            spans,
            {"tool_call_id": "call_x", "status": "running", "tool": "t", "label": "f"},
        )
        update_hermes_tool_span(
            trace,
            spans,
            {"tool_call_id": "call_x", "status": "running", "tool": "t", "label": "f"},
        )

        trace.span.assert_called_once()

    def test_duplicate_completed_skipped(self):
        """重复 completed 事件：不重复 end。"""
        trace = MagicMock()
        span = MagicMock()
        spans: dict[str, Any] = {
            "call_x": {"span": span, "args": "", "function_name": "f", "ended": True}
        }

        update_hermes_tool_span(
            trace,
            spans,
            {"tool_call_id": "call_x", "status": "completed", "tool": "t", "label": None},
        )

        span.end.assert_not_called()  # 已 end，跳过

    def test_unknown_status_skipped(self):
        """status=其他值：忽略。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_hermes_tool_span(
            trace,
            spans,
            {"tool_call_id": "call_x", "status": "paused", "tool": "t", "label": "f"},
        )

        trace.span.assert_not_called()
        assert spans == {}

    def test_missing_tool_call_id_skipped(self):
        """tool_call_id 缺失：跳过。"""
        trace = MagicMock()
        spans: dict[str, Any] = {}

        update_hermes_tool_span(
            trace,
            spans,
            {"tool_call_id": None, "status": "running", "tool": "t", "label": "f"},
        )

        trace.span.assert_not_called()
        assert spans == {}

    def test_label_falls_back_to_tool(self):
        """label 缺失时用 tool 名做 span name。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        update_hermes_tool_span(
            trace,
            spans,
            {"tool_call_id": "call_x", "status": "running", "tool": "terminal", "label": None},
        )

        _, kwargs = trace.span.call_args
        assert kwargs["name"] == "tool_call: terminal"


# ── 集成：Hermes 解析 + SPAN 创建/end 流程 ──────────────────


class TestHermesEndToEndFlow:
    """模拟 Hermes SSE 流：running + completed 事件，验证 SPAN 创建+end。"""

    def test_two_tool_calls_parallel_with_completion(self):
        """2 个 tool 并行：各发 running + completed，验证 2 个 SPAN 都创建并 end。"""
        trace = MagicMock()
        span_a, span_b = MagicMock(), MagicMock()
        trace.span.side_effect = [span_a, span_b]
        spans: dict[str, Any] = {}

        for chunk in [
            HERMES_RUNNING_CHUNK,
            HERMES_TERMINAL_RUNNING,
            HERMES_COMPLETED_CHUNK,
            HERMES_TERMINAL_COMPLETED,
        ]:
            for evt in _parse_hermes_tool_progress_from_chunk(chunk):
                update_hermes_tool_span(trace, spans, evt)

        # 2 个 SPAN 创建
        assert trace.span.call_count == 2
        # 都立即 end（completed 事件触发）
        span_a.end.assert_called_once()
        span_b.end.assert_called_once()
        # ended 标记
        assert spans["call_lqj7"]["ended"] is True
        assert spans["call_jtdd"]["ended"] is True

    def test_running_only_no_completed_end_at_stream_end(self):
        """只有 running 事件没有 completed：流结束时 end_tool_call_spans 统一 end。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        for evt in _parse_hermes_tool_progress_from_chunk(HERMES_RUNNING_CHUNK):
            update_hermes_tool_span(trace, spans, evt)

        # running 创建 SPAN，未 end
        trace.span.assert_called_once()
        span.end.assert_not_called()

        # 流结束统一 end
        end_tool_call_spans(spans)

        # end_tool_call_spans 应跳过 ended=False 的 span 并 end 它
        span.end.assert_called_once()

    def test_hermes_and_openai_formats_coexist(self):
        """Hermes + OpenAI 两种格式共用同一 spans dict，key 不冲突。

        OpenAI 用 str(index) 做 key，Hermes 用 tool_call_id 做 key，命名空间不冲突。
        """
        trace = MagicMock()
        oa_span = MagicMock()
        hermes_span = MagicMock()
        trace.span.side_effect = [oa_span, hermes_span]
        spans: dict[str, Any] = {}

        # OpenAI 格式：index=0
        for tc in _parse_tool_calls_from_sse_chunk(SINGLE_CHUNK_TOOL_CALL):
            update_tool_call_span(trace, spans, tc)
        # Hermes 格式：toolCallId="call_lqj7"
        for evt in _parse_hermes_tool_progress_from_chunk(HERMES_RUNNING_CHUNK):
            update_hermes_tool_span(trace, spans, evt)

        # 两个独立 SPAN
        assert trace.span.call_count == 2
        assert "0" in spans  # OpenAI key（str(index)）
        assert "call_lqj7" in spans  # Hermes key（tool_call_id）

    def test_trace_none_hermes_flow_no_op(self):
        """trace=None 全程 no-op，不抛异常。"""
        spans: dict[str, Any] = {}
        for chunk in [HERMES_RUNNING_CHUNK, HERMES_COMPLETED_CHUNK]:
            for evt in _parse_hermes_tool_progress_from_chunk(chunk):
                update_hermes_tool_span(None, spans, evt)
        end_tool_call_spans(spans)
        assert spans == {}
