"""Dify agent_thought + workflow node 事件 → Langfuse SPAN observation 单元测试。

覆盖：
  - create_or_update_agent_thought_span：position 缓存 / 两阶段更新 / tool_input JSON 解析 / trace=None 优雅降级
  - DifyAdapter._convert_dify_event_block：agent_thought 事件解析后调用 span 创建函数，且不产生 OpenAI chunk
  - transform_sse_stream：agent_thought 与 message 事件混合时正常输出 OpenAI chunks + 创建 spans
  - create_or_update_workflow_node_span：node_id 缓存 / 两阶段更新 / created_at 转 datetime / trace=None 降级
  - DifyAdapter._convert_dify_event_block：node_started/node_finished 事件解析 + 不产生 OpenAI chunk
  - transform_sse_stream：workflow node 事件 + text_chunk 混合时 OpenAI chunks 正常 + span 被创建
"""

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.adapter import DifyAdapter
from app.langfuse_client import (
    create_or_update_agent_thought_span,
    create_or_update_workflow_node_span,
)

NS = "unionagents"


# ── create_or_update_agent_thought_span 单元测试 ──────────────


class TestCreateOrUpdateAgentThoughtSpan:
    """span 创建/更新纯逻辑测试（mock trace）。"""

    def test_trace_none_graceful_no_op(self):
        """trace=None（Langfuse 未启用）时不崩、不创建 span。"""
        spans: dict[str, Any] = {}
        create_or_update_agent_thought_span(
            None, spans,
            position=1, thought="思考", tool="search",
            tool_input='{"q":"x"}', observation="结果",
        )
        assert spans == {}

    def test_first_call_creates_span_with_thought_and_tool(self):
        """第一次到达：创建 span，input 含 thought/tool/tool_input。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought="我需要搜索",
            tool="web_search",
            tool_input='{"query":"天气"}',
            observation=None,
        )

        trace.span.assert_called_once()
        _, kwargs = trace.span.call_args
        assert kwargs["name"] == "agent step#1"
        assert kwargs["input"]["thought"] == "我需要搜索"
        assert kwargs["input"]["tool"] == "web_search"
        # tool_input JSON 字符串被解析为对象
        assert kwargs["input"]["tool_input"] == {"query": "天气"}
        # 没有 observation 时不 update output
        span.update.assert_not_called()
        span.end.assert_called_once()
        # span 缓存到 dict
        assert spans["1"] is span

    def test_second_call_same_position_updates_observation(self):
        """同 position 二次到达：不新建 span，update output=observation。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {"1": span}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought=None, tool=None,
            tool_input=None, observation="搜索结果: 北京 25°C",
        )

        # 不新建
        trace.span.assert_not_called()
        # update output 被调用
        span.update.assert_called_once()
        _, kwargs = span.update.call_args
        assert kwargs["output"] == "搜索结果: 北京 25°C"
        # 不再 end
        span.end.assert_not_called()

    def test_second_call_same_position_updates_thought(self):
        """同 position 二次到达带 thought：update input.thought（Dify 实际模式）。

        Dify 先发空 thought 的 agent_thought，再发带 thought 的同 position 事件，
        必须把 thought 写入 span.input，否则 span 停留在空状态。
        """
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {"1": span}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought="我需要搜索天气信息",
            tool=None, tool_input=None, observation=None,
        )

        trace.span.assert_not_called()
        span.update.assert_called_once()
        _, kwargs = span.update.call_args
        assert kwargs["input"]["thought"] == "我需要搜索天气信息"

    def test_second_call_same_position_updates_tool_and_tool_input(self):
        """同 position 二次到达带 tool/tool_input：update input.tool + tool_input。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {"1": span}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought=None, tool="web_search",
            tool_input='{"q":"天气"}', observation=None,
        )

        span.update.assert_called_once()
        _, kwargs = span.update.call_args
        assert kwargs["input"]["tool"] == "web_search"
        assert kwargs["input"]["tool_input"] == {"q": "天气"}

    def test_second_call_same_position_empty_values_no_update(self):
        """同 position 二次到达但字段全空：不 update（无内容可写）。"""
        trace = MagicMock()
        span = MagicMock()
        spans: dict[str, Any] = {"1": span}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought=None, tool=None,
            tool_input=None, observation=None,
        )

        span.update.assert_not_called()
        trace.span.assert_not_called()

    def test_second_call_merges_input_and_output(self):
        """同 position 二次到达同时带 thought 和 observation：input+output 都更新。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {"1": span}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought="推理结果",
            tool=None, tool_input=None, observation="工具输出",
        )

        span.update.assert_called_once()
        _, kwargs = span.update.call_args
        assert kwargs["input"]["thought"] == "推理结果"
        assert kwargs["output"] == "工具输出"

    def test_second_call_empty_string_tool_input_skipped(self):
        """tool_input 为空字符串时不写入 input.tool_input（避免覆盖已有值）。"""
        trace = MagicMock()
        span = MagicMock()
        spans: dict[str, Any] = {"1": span}

        create_or_update_agent_thought_span(
            trace, spans,
            position=1, thought=None, tool=None,
            tool_input="", observation=None,
        )

        span.update.assert_not_called()

    def test_different_positions_create_different_spans(self):
        """不同 position 创建独立 span。"""
        trace = MagicMock()
        span1, span2 = MagicMock(), MagicMock()
        trace.span.side_effect = [span1, span2]
        spans: dict[str, Any] = {}

        create_or_update_agent_thought_span(
            trace, spans, position=1, thought="step1", tool="t1",
            tool_input=None, observation=None,
        )
        create_or_update_agent_thought_span(
            trace, spans, position=2, thought="step2", tool="t2",
            tool_input=None, observation=None,
        )

        assert spans["1"] is span1
        assert spans["2"] is span2
        assert trace.span.call_count == 2

    def test_position_none_uses_default_key(self):
        """position=None 时用 "default" key（兼容字段缺失）。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_agent_thought_span(
            trace, spans, position=None, thought="thought", tool="t",
            tool_input=None, observation=None,
        )

        assert "default" in spans
        # span 名字用 "agent_thought"（无 position 后缀）
        _, kwargs = trace.span.call_args
        assert kwargs["name"] == "agent_thought"

    def test_tool_input_invalid_json_kept_as_string(self):
        """tool_input 是字符串但非合法 JSON：保留原始字符串。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_agent_thought_span(
            trace, spans, position=1, thought="t", tool="tool",
            tool_input="not json {", observation=None,
        )

        _, kwargs = trace.span.call_args
        # 原始字符串保留（不崩）
        assert kwargs["input"]["tool_input"] == "not json {"

    def test_tool_input_object_passed_through(self):
        """tool_input 已是 dict：直接透传，不尝试 json.loads。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        obj = {"key": "value"}
        create_or_update_agent_thought_span(
            trace, spans, position=1, thought="t", tool="tool",
            tool_input=obj, observation=None,
        )

        _, kwargs = trace.span.call_args
        assert kwargs["input"]["tool_input"] is obj

    def test_first_call_with_observation_updates_and_ends(self):
        """第一次到达就带 observation：创建 span + update output + end。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_agent_thought_span(
            trace, spans, position=1, thought="t", tool="tool",
            tool_input=None, observation="即时结果",
        )

        span.update.assert_called_once_with(output="即时结果")
        span.end.assert_called_once()
        assert spans["1"] is span

    def test_span_creation_exception_logged_no_crash(self, caplog):
        """trace.span 抛异常：记日志，不崩，spans 不缓存。"""
        trace = MagicMock()
        trace.span.side_effect = RuntimeError("langfuse down")
        spans: dict[str, Any] = {}

        # 不应抛
        create_or_update_agent_thought_span(
            trace, spans, position=1, thought="t", tool="t",
            tool_input=None, observation=None,
        )

        assert spans == {}  # 异常路径不缓存

    def test_span_update_exception_logged_no_crash(self):
        """existing.update 抛异常：记日志，不崩。"""
        trace = MagicMock()
        span = MagicMock()
        span.update.side_effect = RuntimeError("update failed")
        spans: dict[str, Any] = {"1": span}

        # 不应抛
        create_or_update_agent_thought_span(
            trace, spans, position=1, thought=None, tool=None,
            tool_input=None, observation="obs",
        )

        # 异常路径不会做后续操作
        span.end.assert_not_called()


# ── DifyAdapter agent_thought 事件解析测试 ──────────────────


class TestDifyAdapterAgentThought:
    """DifyAdapter._convert_dify_event_block 处理 agent_thought 事件。"""

    @pytest.fixture
    def adapter(self):
        a = DifyAdapter(k8s_namespace=NS)
        a._app_type = "agent"  # agent 模式才会发 agent_thought
        return a

    def test_agent_thought_event_calls_span_creator(self, adapter, monkeypatch):
        """agent_thought 事件被解析后调用 create_or_update_agent_thought_span。"""
        called = {}

        def fake_create(trace, spans, *, position, thought, tool, tool_input, observation):
            called.update({
                "trace": trace, "spans": spans, "position": position,
                "thought": thought, "tool": tool, "tool_input": tool_input,
                "observation": observation,
            })

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span", fake_create
        )

        trace_obj = object()  # sentinel
        adapter._langfuse_trace = trace_obj

        block = (
            'event: agent_thought\n'
            'data: {"position":1,"thought":"我需要搜索","tool":"web_search",'
            '"tool_input":"{\\"q\\":\\"天气\\"}","observation":"北京 25°C"}'
        )
        state: dict[str, Any] = {}
        out = adapter._convert_dify_event_block(block, state)

        # agent_thought 不产生 OpenAI chunk
        assert out == ""
        # span creator 被调用，参数透传
        assert called["trace"] is trace_obj
        assert called["position"] == 1
        assert called["thought"] == "我需要搜索"
        assert called["tool"] == "web_search"
        assert called["tool_input"] == '{"q":"天气"}'
        assert called["observation"] == "北京 25°C"
        # state 里的 _agent_thought_spans dict 被创建（setdefault）
        assert "_agent_thought_spans" in state

    def test_agent_thought_no_trace_still_no_op(self, adapter, monkeypatch):
        """_langfuse_trace=None 时，agent_thought 事件不崩（span 函数内部判空）。"""
        called = {"count": 0}

        def fake_create(*args, **kwargs):
            called["count"] += 1

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span", fake_create
        )

        adapter._langfuse_trace = None
        block = 'event: agent_thought\ndata: {"position":1,"thought":"t"}'
        state: dict[str, Any] = {}
        out = adapter._convert_dify_event_block(block, state)

        assert out == ""
        # 函数仍被调用（内部判空），spans dict 已初始化
        assert called["count"] == 1
        assert "_agent_thought_spans" in state

    def test_agent_thought_invalid_json_no_crash(self, adapter, monkeypatch):
        """agent_thought 事件 data 非合法 JSON：不崩，返回空。"""
        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span",
            lambda *a, **k: None,
        )
        adapter._langfuse_trace = object()
        block = 'event: agent_thought\ndata: not-json'
        out = adapter._convert_dify_event_block(block, {})
        assert out == ""

    def test_agent_thought_missing_fields(self, adapter, monkeypatch):
        """字段缺失（无 position/thought/tool）：仍调用 span creator，传 None。"""
        captured = {}

        def fake_create(trace, spans, *, position, thought, tool, tool_input, observation):
            captured.update(position=position, thought=thought, tool=tool,
                            tool_input=tool_input, observation=observation)

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span", fake_create
        )
        adapter._langfuse_trace = object()
        block = 'event: agent_thought\ndata: {}'
        adapter._convert_dify_event_block(block, {})
        assert captured["position"] is None
        assert captured["thought"] is None
        assert captured["tool"] is None
        assert captured["tool_input"] is None
        assert captured["observation"] is None

    def test_agent_thought_data_only_format(self, adapter, monkeypatch):
        """Dify 1.14+ SSE 格式：无 event: 行，event 类型在 data JSON 的 "event" 字段。"""
        called = {"position": None}

        def fake_create(trace, spans, *, position, thought, tool, tool_input, observation):
            called["position"] = position

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span", fake_create
        )
        adapter._langfuse_trace = object()
        # 无 event: 行，event 在 JSON 里
        block = (
            'data: {"event":"agent_thought","position":3,'
            '"thought":"step3","tool":"calc","tool_input":"x=1","observation":"2"}'
        )
        adapter._convert_dify_event_block(block, {})
        assert called["position"] == 3

    def test_state_spans_dict_persists_across_events(self, adapter, monkeypatch):
        """同一 state 跨多个 agent_thought 事件保留 _agent_thought_spans 引用。"""
        seen_spans = []

        def fake_create(trace, spans, *, position, thought, tool, tool_input, observation):
            seen_spans.append(spans)

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span", fake_create
        )
        adapter._langfuse_trace = object()
        state: dict[str, Any] = {}
        block1 = 'event: agent_thought\ndata: {"position":1,"thought":"s1"}'
        block2 = 'event: agent_thought\ndata: {"position":2,"thought":"s2"}'
        adapter._convert_dify_event_block(block1, state)
        adapter._convert_dify_event_block(block2, state)
        # 两次见到同一个 spans dict（setdefault 不覆盖已存在的）
        assert len(seen_spans) == 2
        assert seen_spans[0] is seen_spans[1]
        assert seen_spans[0] is state["_agent_thought_spans"]


# ── transform_sse_stream 端到端：agent_thought 与 message 混合 ──


class TestTransformSseStreamAgentThought:
    """agent_thought 事件不应破坏正常 message 流，也不应产生 OpenAI chunk。"""

    @pytest.fixture
    def adapter(self):
        a = DifyAdapter(k8s_namespace=NS)
        a._app_type = "agent"
        return a

    async def test_agent_thought_does_not_emit_openai_chunk(self, adapter, monkeypatch):
        """agent_thought 事件被消费但不产生 OpenAI chunk，message 事件正常输出。"""
        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_agent_thought_span",
            lambda *a, **k: None,
        )
        adapter._langfuse_trace = object()

        dify_sse = (
            'event: agent_thought\ndata: {"position":1,"thought":"思考","tool":"t"}\n\n'
            'event: message\ndata: {"answer":"你好","id":"m1","conversation_id":"c1"}\n\n'
            'event: agent_thought\ndata: {"position":2,"thought":"再思考","tool":"t2"}\n\n'
            'event: message_end\ndata: {}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()

        # 只有一个 content chunk（来自 message 事件），agent_thought 不产生 chunk
        assert text.count("chat.completion.chunk") == 1
        assert "你好" in text
        assert text.endswith("data: [DONE]\n\n")
        # agent_thought 的 thought 文本不应作为输出（只进 span，不进 OpenAI delta）
        assert "思考" not in text
        assert "再思考" not in text

    async def test_agent_thought_without_trace_still_streams_message(self, adapter):
        """_langfuse_trace=None 时 agent_thought 事件被忽略，message 正常输出。"""
        adapter._langfuse_trace = None

        dify_sse = (
            'event: agent_thought\ndata: {"position":1,"thought":"t"}\n\n'
            'event: message\ndata: {"answer":"hi","id":"m1"}\n\n'
            'event: message_end\ndata: {}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert "hi" in text
        assert text.endswith("data: [DONE]\n\n")


# ── create_or_update_workflow_node_span 单元测试 ──────────────


class TestCreateOrUpdateWorkflowNodeSpan:
    """workflow node span 创建/更新纯逻辑测试（mock trace）。"""

    def test_trace_none_graceful_no_op(self):
        """trace=None（Langfuse 未启用）时不崩、不创建 span。"""
        spans: dict[str, Any] = {}
        create_or_update_workflow_node_span(
            None, spans,
            node_id="node-1", title="LLM", node_type="llm",
            inputs={"q": "hi"}, outputs={"text": "hello"},
            elapsed_time=1.23, status="succeeded", error=None,
            created_at=1782829482.123,
        )
        assert spans == {}

    def test_node_id_none_skips(self):
        """无 node_id 无法关联两阶段事件，丢弃。"""
        trace = MagicMock()
        spans: dict[str, Any] = {}
        create_or_update_workflow_node_span(
            trace, spans,
            node_id=None, title="t", node_type="llm",
            inputs=None, outputs=None, elapsed_time=None,
            status=None, error=None, created_at=None,
        )
        trace.span.assert_not_called()
        assert spans == {}

    def test_node_started_creates_span_with_inputs_and_start_time(self):
        """node_started 到达：创建 span，input=inputs，start_time 从 created_at 转 datetime。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="node-abc-def-12345", title=None, node_type=None,
            inputs={"query": "你好"}, outputs=None,
            elapsed_time=None, status=None, error=None,
            created_at=1782829482.123,
        )

        trace.span.assert_called_once()
        _, kwargs = trace.span.call_args
        # title 未到达，name 用 node:{node_id[:12]} 占位
        assert kwargs["name"] == "node:node-abc-def"
        assert kwargs["input"] == {"query": "你好"}
        # start_time 从 created_at 转 datetime
        assert isinstance(kwargs.get("start_time"), datetime)
        assert kwargs["start_time"].tzinfo == UTC
        # span.end() 立即调用（占位，node_finished 再 update）
        span.end.assert_called_once()
        # 缓存
        assert spans["node-abc-def-12345"] is span

    def test_node_finished_updates_existing_span(self):
        """node_finished 到达：update name/output/metadata/end_time。"""
        trace = MagicMock()
        span = MagicMock()
        spans: dict[str, Any] = {"node-1": span}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="node-1", title="LLM 节点", node_type="llm",
            inputs={"q": "hi"}, outputs={"text": "回答"},
            elapsed_time=1.23, status="succeeded", error=None,
            created_at=1782829483.456,
        )

        # 不新建
        trace.span.assert_not_called()
        span.update.assert_called_once()
        _, kwargs = span.update.call_args
        assert kwargs["name"] == "LLM 节点"
        assert kwargs["output"] == {"text": "回答"}
        assert kwargs["metadata"]["node_type"] == "llm"
        assert kwargs["metadata"]["elapsed_time"] == 1.23
        assert kwargs["metadata"]["status"] == "succeeded"
        assert isinstance(kwargs.get("end_time"), datetime)
        # error=None 不写 metadata
        assert "error" not in kwargs["metadata"]

    def test_node_finished_with_error_records_in_metadata(self):
        """node_finished 带 error：写入 metadata.error。"""
        trace = MagicMock()
        span = MagicMock()
        spans: dict[str, Any] = {"node-1": span}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="node-1", title="Code", node_type="code",
            inputs=None, outputs=None, elapsed_time=0.5,
            status="failed", error="执行异常: 非法输入",
            created_at=1782829484.0,
        )

        span.update.assert_called_once()
        _, kwargs = span.update.call_args
        assert kwargs["metadata"]["error"] == "执行异常: 非法输入"
        assert kwargs["metadata"]["status"] == "failed"

    def test_different_node_ids_create_different_spans(self):
        """不同 node_id 创建独立 span。"""
        trace = MagicMock()
        span1, span2 = MagicMock(), MagicMock()
        trace.span.side_effect = [span1, span2]
        spans: dict[str, Any] = {}

        create_or_update_workflow_node_span(
            trace, spans, node_id="n1", title=None, node_type=None,
            inputs={}, outputs=None, elapsed_time=None,
            status=None, error=None, created_at=None,
        )
        create_or_update_workflow_node_span(
            trace, spans, node_id="n2", title=None, node_type=None,
            inputs={}, outputs=None, elapsed_time=None,
            status=None, error=None, created_at=None,
        )

        assert spans["n1"] is span1
        assert spans["n2"] is span2
        assert trace.span.call_count == 2

    def test_created_at_invalid_no_start_time(self):
        """created_at 非法（字符串/负数）不崩，start_time=None。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="n1", title="t", node_type=None,
            inputs={}, outputs=None, elapsed_time=None,
            status=None, error=None,
            created_at="not-a-number",
        )

        _, kwargs = trace.span.call_args
        assert "start_time" not in kwargs

    def test_created_at_none_omits_start_time(self):
        """created_at=None 时不传 start_time。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="n1", title="t", node_type=None,
            inputs={}, outputs=None, elapsed_time=None,
            status=None, error=None, created_at=None,
        )

        _, kwargs = trace.span.call_args
        assert "start_time" not in kwargs

    def test_node_finished_without_existing_span_creates_new(self):
        """node_finished 先到（无 node_started 缓存）：创建新 span。"""
        trace = MagicMock()
        span = MagicMock()
        trace.span.return_value = span
        spans: dict[str, Any] = {}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="n1", title="完整节点", node_type="llm",
            inputs={"q": "hi"}, outputs={"text": "ans"},
            elapsed_time=2.0, status="succeeded", error=None,
            created_at=1782829485.0,
        )

        # 无 existing → 走创建路径
        trace.span.assert_called_once()
        _, kwargs = trace.span.call_args
        # 有 title 时 name=title
        assert kwargs["name"] == "完整节点"
        assert kwargs["input"] == {"q": "hi"}
        span.end.assert_called_once()
        assert spans["n1"] is span

    def test_span_creation_exception_logged_no_crash(self):
        """trace.span 抛异常：记日志，不崩，spans 不缓存。"""
        trace = MagicMock()
        trace.span.side_effect = RuntimeError("langfuse down")
        spans: dict[str, Any] = {}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="n1", title="t", node_type=None,
            inputs=None, outputs=None, elapsed_time=None,
            status=None, error=None, created_at=None,
        )

        assert spans == {}

    def test_span_update_exception_logged_no_crash(self):
        """existing.update 抛异常：记日志，不崩。"""
        trace = MagicMock()
        span = MagicMock()
        span.update.side_effect = RuntimeError("update failed")
        spans: dict[str, Any] = {"n1": span}

        create_or_update_workflow_node_span(
            trace, spans,
            node_id="n1", title="t", node_type="llm",
            inputs=None, outputs="out", elapsed_time=1.0,
            status="succeeded", error=None, created_at=1782829485.0,
        )

        span.update.assert_called_once()


# ── DifyAdapter workflow node 事件解析测试 ──────────────────


class TestDifyAdapterWorkflowNode:
    """DifyAdapter._convert_dify_event_block 处理 node_started/node_finished 事件。"""

    @pytest.fixture
    def adapter(self):
        a = DifyAdapter(k8s_namespace=NS)
        a._app_type = "workflow"
        return a

    def test_node_started_calls_span_creator(self, adapter, monkeypatch):
        """node_started 事件被解析后调用 create_or_update_workflow_node_span。"""
        called = {}

        def fake_create(trace, spans, *, node_id, title, node_type, inputs, outputs,
                        elapsed_time, status, error, created_at):
            called.update({
                "trace": trace, "spans": spans, "node_id": node_id,
                "title": title, "node_type": node_type, "inputs": inputs,
                "outputs": outputs, "elapsed_time": elapsed_time,
                "status": status, "error": error, "created_at": created_at,
            })

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span", fake_create
        )
        trace_obj = object()
        adapter._langfuse_trace = trace_obj

        block = (
            'event: node_started\n'
            'data: {"event":"node_started","data":{"id":"run-1",'
            '"node_id":"node-abc","inputs":{"q":"你好"},'
            '"created_at":1782829482.123}}'
        )
        state: dict[str, Any] = {}
        out = adapter._convert_dify_event_block(block, state)

        assert out == ""
        assert called["trace"] is trace_obj
        assert called["node_id"] == "node-abc"
        assert called["title"] is None
        assert called["node_type"] is None
        assert called["inputs"] == {"q": "你好"}
        assert called["outputs"] is None
        assert called["created_at"] == 1782829482.123
        assert "_workflow_node_spans" in state

    def test_node_finished_calls_span_creator(self, adapter, monkeypatch):
        """node_finished 事件被解析后调用 span creator，字段透传。"""
        called = {}

        def fake_create(trace, spans, *, node_id, title, node_type, inputs, outputs,
                        elapsed_time, status, error, created_at):
            called.update(node_id=node_id, title=title, node_type=node_type,
                          inputs=inputs, outputs=outputs, elapsed_time=elapsed_time,
                          status=status, error=error, created_at=created_at)

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span", fake_create
        )
        adapter._langfuse_trace = object()

        block = (
            'event: node_finished\n'
            'data: {"event":"node_finished","data":{"id":"run-1",'
            '"node_id":"node-abc","title":"LLM节点","node_type":"llm",'
            '"inputs":{"q":"你好"},"outputs":{"text":"回答"},'
            '"elapsed_time":1.23,"status":"succeeded","error":null,'
            '"created_at":1782829483.456}}'
        )
        adapter._convert_dify_event_block(block, {})

        assert called["node_id"] == "node-abc"
        assert called["title"] == "LLM节点"
        assert called["node_type"] == "llm"
        assert called["outputs"] == {"text": "回答"}
        assert called["elapsed_time"] == 1.23
        assert called["status"] == "succeeded"
        assert called["created_at"] == 1782829483.456

    def test_node_event_no_trace_still_no_op(self, adapter, monkeypatch):
        """_langfuse_trace=None 时 node 事件不崩（span 函数内部判空）。"""
        called = {"count": 0}

        def fake_create(*args, **kwargs):
            called["count"] += 1

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span", fake_create
        )
        adapter._langfuse_trace = None
        block = 'event: node_started\ndata: {"data":{"node_id":"n1","created_at":1.0}}'
        out = adapter._convert_dify_event_block(block, {})
        assert out == ""
        assert called["count"] == 1

    def test_node_event_invalid_json_no_crash(self, adapter, monkeypatch):
        """node 事件 data 非合法 JSON：不崩，返回空。"""
        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span",
            lambda *a, **k: None,
        )
        adapter._langfuse_trace = object()
        block = 'event: node_started\ndata: not-json'
        out = adapter._convert_dify_event_block(block, {})
        assert out == ""

    def test_node_event_chat_mode_not_handled(self, adapter, monkeypatch):
        """chat 模式（非 workflow）不处理 node 事件。"""
        adapter._app_type = "chat"
        called = {"count": 0}

        def fake_create(*args, **kwargs):
            called["count"] += 1

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span", fake_create
        )
        adapter._langfuse_trace = object()
        block = 'event: node_started\ndata: {"data":{"node_id":"n1"}}'
        adapter._convert_dify_event_block(block, {})
        assert called["count"] == 0

    def test_node_event_data_only_format(self, adapter, monkeypatch):
        """Dify 1.14+ SSE 格式：无 event: 行，event 在 JSON event 字段。"""
        called = {"node_id": None}

        def fake_create(trace, spans, *, node_id, **kwargs):
            called["node_id"] = node_id

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span", fake_create
        )
        adapter._langfuse_trace = object()
        block = (
            'data: {"event":"node_finished","data":{"node_id":"n-xyz",'
            '"title":"Code","node_type":"code","outputs":{"r":1},'
            '"elapsed_time":0.1,"status":"succeeded","created_at":2.0}}'
        )
        adapter._convert_dify_event_block(block, {})
        assert called["node_id"] == "n-xyz"

    def test_node_event_data_not_dict_skips(self, adapter, monkeypatch):
        """data 字段非 dict（异常情况）：不调 span creator。"""
        called = {"count": 0}

        def fake_create(*a, **k):
            called["count"] += 1

        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span", fake_create
        )
        adapter._langfuse_trace = object()
        # data 是字符串而非 dict
        block = 'event: node_started\ndata: {"data":"not-a-dict"}'
        out = adapter._convert_dify_event_block(block, {})
        assert out == ""
        assert called["count"] == 0


# ── transform_sse_stream 端到端：workflow node + text_chunk 混合 ──


class TestTransformSseStreamWorkflowNode:
    """workflow node 事件不应破坏 text_chunk 流，也不应产生 OpenAI chunk。"""

    @pytest.fixture
    def adapter(self):
        a = DifyAdapter(k8s_namespace=NS)
        a._app_type = "workflow"
        return a

    async def test_node_events_do_not_emit_openai_chunk(self, adapter, monkeypatch):
        """node_started/node_finished 被消费但不产生 OpenAI chunk，text_chunk 正常输出。"""
        monkeypatch.setattr(
            "app.adapter.dify.create_or_update_workflow_node_span",
            lambda *a, **k: None,
        )
        adapter._langfuse_trace = object()

        dify_sse = (
            'event: node_started\ndata: {"data":{"node_id":"n1","inputs":{"q":"hi"},"created_at":1.0}}\n\n'
            'event: text_chunk\ndata: {"data":{"text":"你好"}}\n\n'
            'event: node_finished\ndata: {"data":{"node_id":"n1","title":"LLM","outputs":{"text":"你好"},"elapsed_time":0.5,"status":"succeeded","created_at":1.5}}\n\n'
            'event: workflow_finished\ndata: {"data":{"outputs":{}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()

        # 只有一个 content chunk（来自 text_chunk），node 事件不产生 chunk
        assert text.count("chat.completion.chunk") == 1
        assert "你好" in text
        assert text.endswith("data: [DONE]\n\n")
        # node 事件的 inputs/outputs 不应作为 OpenAI delta
        assert "LLM" not in text

    async def test_node_events_without_trace_still_streams_text(self, adapter):
        """_langfuse_trace=None 时 node 事件被忽略，text_chunk 正常输出。"""
        adapter._langfuse_trace = None

        dify_sse = (
            'event: node_started\ndata: {"data":{"node_id":"n1","created_at":1.0}}\n\n'
            'event: text_chunk\ndata: {"data":{"text":"hi"}}\n\n'
            'event: workflow_finished\ndata: {"data":{"outputs":{}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert "hi" in text
        assert text.endswith("data: [DONE]\n\n")
