"""adapter 包单元测试（B1 + B2）

覆盖：
  - build_engine_dns：三引擎端口/DNS 命名
  - registry：按 engine_type 选 adapter；未知 → HERMES 默认；大小写不敏感
  - HermesAdapter：URL 构造 + transform_headers（去 Origin/Referer + 注入 key + 会话头翻译）
  - OpenClawAdapter：DNS + 不做 hermes 会话头翻译
  - DifyAdapter：map_path 路径映射 + session 钩子 + :8080 + 会话头翻译
    + B2 请求体转换（OpenAI→Dify）+ SSE 流式转换（Dify→OpenAI）+ blocking 响应转换
"""

import pytest
from app.adapter import (
    DifyAdapter,
    HermesAdapter,
    OpenClawAdapter,
    build_engine_dns,
    get_adapter,
    known_engine_types,
)
from app.adapter.base import ENGINE_PORTS

NS = "unionagents"
AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"  # short_id = "550e8400"


# ── build_engine_dns ───────────────────────────────────────


class TestBuildEngineDns:
    def test_hermes_port_and_dns(self):
        assert build_engine_dns("hermes", AGENT_ID, NS) == (
            "engine-hermes-550e8400.unionagents.svc.cluster.local:8642"
        )

    def test_openclaw_port_and_dns(self):
        assert build_engine_dns("openclaw", AGENT_ID, NS) == (
            "engine-openclaw-550e8400.unionagents.svc.cluster.local:8642"
        )

    def test_dify_port_and_dns(self):
        assert build_engine_dns("dify", AGENT_ID, NS) == (
            "engine-dify-550e8400.unionagents.svc.cluster.local:8080"
        )

    def test_short_id_strips_hyphens(self):
        # "a-b-c-d-e-f-g-h" -> "abcdefg" + "h"[:8] = "abcdefgh"
        assert build_engine_dns("hermes", "a-b-c-d-e-f-g-h-ijk", NS).startswith(
            "engine-hermes-abcdefgh."
        )

    def test_engine_ports_table(self):
        assert ENGINE_PORTS == {"hermes": 8642, "openclaw": 8642, "dify": 8080}

    def test_unknown_engine_type_falls_back_to_8642(self):
        # 未知 engine_type 端口回退 8642（不应崩）
        dns = build_engine_dns("claude", AGENT_ID, NS)
        assert ":8642" in dns and dns.startswith("engine-claude-")


# ── registry ──────────────────────────────────────────────


class TestRegistry:
    def test_get_hermes(self):
        assert isinstance(get_adapter("HERMES", k8s_namespace=NS), HermesAdapter)

    def test_get_openclaw(self):
        assert isinstance(get_adapter("OPENCLAW", k8s_namespace=NS), OpenClawAdapter)

    def test_get_dify(self):
        assert isinstance(get_adapter("DIFY", k8s_namespace=NS), DifyAdapter)

    def test_case_insensitive(self):
        assert isinstance(get_adapter("dify", k8s_namespace=NS), DifyAdapter)
        assert isinstance(get_adapter("Hermes", k8s_namespace=NS), HermesAdapter)

    def test_unknown_engine_type_defaults_to_hermes(self):
        # 向后兼容 Repo2 仅 hermes 现状：未知 → HERMES
        assert isinstance(get_adapter("UNKNOWN", k8s_namespace=NS), HermesAdapter)

    def test_known_engine_types_registered(self):
        types = set(known_engine_types())
        assert {"HERMES", "OPENCLAW", "DIFY"} <= types


# ── HermesAdapter ─────────────────────────────────────────


class TestHermesAdapter:
    @pytest.fixture
    def adapter(self):
        return HermesAdapter(k8s_namespace=NS)

    async def test_build_upstream_url(self, adapter):
        url = await adapter.build_upstream_url(AGENT_ID, "v1/chat/completions", None)
        assert url == "http://engine-hermes-550e8400.unionagents.svc.cluster.local:8642/v1/chat/completions"

    async def test_build_upstream_url_with_query(self, adapter):
        url = await adapter.build_upstream_url(AGENT_ID, "v1/models", "limit=10")
        assert url.endswith("/v1/models?limit=10")

    async def test_build_upstream_url_empty_path(self, adapter):
        url = await adapter.build_upstream_url(AGENT_ID, "", None)
        assert url == "http://engine-hermes-550e8400.unionagents.svc.cluster.local:8642"

    def test_transform_headers_strips_origin_referer(self, adapter):
        raw = {
            "x-agent-id": "a1",
            "origin": "http://example.com",
            "referer": "http://example.com/page",
            "authorization": "Bearer client-jwt",
        }
        h = adapter.transform_headers(raw, "engine-key")
        assert "origin" not in h
        assert "referer" not in h

    def test_transform_headers_strips_x_hermes_profile(self, adapter):
        # 客户端传入的 X-Hermes-Profile 必须被丢弃（服务端计算）
        raw = {"x-agent-id": "a1", "x-hermes-profile": "spoofed"}
        h = adapter.transform_headers(raw, "engine-key")
        assert "x-hermes-profile" not in h

    def test_transform_headers_injects_engine_key(self, adapter):
        raw = {"authorization": "Bearer client-jwt"}
        h = adapter.transform_headers(raw, "engine-key")
        assert h["authorization"] == "Bearer engine-key"

    def test_transform_headers_pops_engine_type(self, adapter):
        raw = {"x-engine-type": "HERMES"}
        h = adapter.transform_headers(raw, "k")
        assert "x-engine-type" not in h

    def test_transform_headers_translates_session_id(self, adapter):
        raw = {"x-engine-type": "HERMES", "x-session-id": "sess-123"}
        h = adapter.transform_headers(raw, "k")
        assert h.get("x-hermes-session-id") == "sess-123"
        assert "x-session-id" not in h

    def test_transform_headers_no_session_id(self, adapter):
        raw = {"x-engine-type": "HERMES"}
        h = adapter.transform_headers(raw, "k")
        assert "x-hermes-session-id" not in h
        assert "x-session-id" not in h

    def test_session_url_hooks(self, adapter):
        # Hermes 会话 REST 在 /api/sessions（非 /v1/sessions）；adapter map_path 翻译
        assert adapter.get_session_create_url(AGENT_ID).endswith("/api/sessions")
        assert adapter.get_session_messages_url(AGENT_ID, "s1").endswith("/api/sessions/s1/messages")
        assert adapter.get_files_url(AGENT_ID).endswith("/v1/files")

    def test_map_path_sessions_to_api(self, adapter):
        """v1/sessions → api/sessions；子路径同样翻译；其他路径透传。"""
        assert adapter.map_path("v1/sessions") == "api/sessions"
        assert adapter.map_path("v1/sessions/s1") == "api/sessions/s1"
        assert adapter.map_path("v1/sessions/s1/messages") == "api/sessions/s1/messages"
        # 未映射路径原样返回（Hermes 原生 OpenAI 兼容）
        assert adapter.map_path("v1/chat/completions") == "v1/chat/completions"
        assert adapter.map_path("v1/models") == "v1/models"
        assert adapter.map_path("v1/runs") == "v1/runs"

    def test_body_transforms_identity(self, adapter):
        # B1：Hermes OpenAI 兼容，body/SSE 透传不修改
        b = b'{"model":"gpt","messages":[]}'
        assert adapter.transform_request_body("v1/chat/completions", b, {}) == b
        assert adapter.transform_response_body("v1/chat/completions", b, {}) == b


# ── OpenClawAdapter ───────────────────────────────────────


class TestOpenClawAdapter:
    @pytest.fixture
    def adapter(self):
        return OpenClawAdapter(k8s_namespace=NS)

    async def test_build_upstream_url(self, adapter):
        url = await adapter.build_upstream_url(AGENT_ID, "v1/chat/completions", None)
        assert url == "http://engine-openclaw-550e8400.unionagents.svc.cluster.local:8642/v1/chat/completions"

    def test_no_hermes_session_header_translation(self, adapter):
        # OpenClaw 不使用 x-hermes-session-id；x-session-id 丢弃
        raw = {"x-engine-type": "OPENCLAW", "x-session-id": "sess-1"}
        h = adapter.transform_headers(raw, "k")
        assert "x-hermes-session-id" not in h
        assert "x-session-id" not in h

    def test_strips_origin_referer(self, adapter):
        raw = {"origin": "http://x", "referer": "http://y"}
        h = adapter.transform_headers(raw, "k")
        assert "origin" not in h and "referer" not in h


# ── DifyAdapter ───────────────────────────────────────────


class TestDifyAdapter:
    @pytest.fixture
    def adapter(self):
        return DifyAdapter(k8s_namespace=NS)

    async def test_build_upstream_url_port_8080(self, adapter):
        url = await adapter.build_upstream_url(AGENT_ID, "v1/chat-messages", None)
        assert url == "http://engine-dify-550e8400.unionagents.svc.cluster.local:8080/v1/chat-messages"

    def test_map_path(self, adapter):
        assert adapter.map_path("v1/chat/completions") == "v1/chat-messages"
        assert adapter.map_path("v1/models") == "parameters"
        assert adapter.map_path("v1/sessions") == "v1/conversations"
        assert adapter.map_path("v1/sessions/s1") == "v1/conversations/s1"
        assert adapter.map_path("v1/sessions/s1/messages") == "v1/conversations/s1/messages"
        # 未映射路径原样返回
        assert adapter.map_path("v1/files") == "v1/files"

    def test_transform_headers_conversation_id(self, adapter):
        raw = {"x-engine-type": "DIFY", "x-session-id": "conv-1"}
        h = adapter.transform_headers(raw, "k")
        assert h.get("x-dify-conversation-id") == "conv-1"
        assert "x-session-id" not in h
        assert "x-conversation-id" not in h
        assert "x-engine-type" not in h

    def test_strips_origin_referer(self, adapter):
        raw = {"origin": "http://x", "referer": "http://y"}
        h = adapter.transform_headers(raw, "k")
        assert "origin" not in h and "referer" not in h

    def test_session_url_hooks_map_to_conversations(self, adapter):
        create = adapter.get_session_create_url(AGENT_ID)
        assert create.endswith("/v1/conversations")
        msgs = adapter.get_session_messages_url(AGENT_ID, "c1")
        assert msgs.endswith("/v1/conversations/c1/messages")

    def test_transform_request_body_openai_to_dify(self, adapter):
        """OpenAI messages → Dify {inputs,query,response_mode,conversation_id,user}"""
        import json
        body = json.dumps({
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "嗯"},
                {"role": "user", "content": "再说一次"},
            ],
            "stream": True,
        }).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out["query"] == "再说一次"  # 取最后一条 user
        assert out["response_mode"] == "streaming"
        assert out["inputs"] == {}
        assert out["user"] == "union-user"

    def test_transform_request_body_user_passthrough_from_openai_req(self, adapter):
        """OpenAI 请求体含 user → Dify 请求体 user 透传（用于终端用户追踪）"""
        import json
        body = json.dumps({
            "messages": [{"role": "user", "content": "hi"}],
            "user": "enduser-uuid-1234",
        }).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out["user"] == "enduser-uuid-1234"

    def test_transform_request_body_user_default_when_missing(self, adapter):
        """OpenAI 请求体不含 user → Dify 用 union-user 兜底（不丢字段）"""
        import json
        body = json.dumps({
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out["user"] == "union-user"

    def test_transform_request_body_blocking_mode(self, adapter):
        import json
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out["response_mode"] == "blocking"

    def test_transform_request_body_uses_conversation_id_header(self, adapter):
        import json
        body = json.dumps({
            "messages": [{"role": "user", "content": "hi"}], "stream": True,
        }).encode()
        hdrs = {"x-dify-conversation-id": "conv-9"}
        out = json.loads(adapter.transform_request_body(
            "v1/chat/completions", body, hdrs))
        assert out["conversation_id"] == "conv-9"
        # transform_request_body 弹出私有头，原 dict 不再包含（避免回透给 upstream）
        assert "x-dify-conversation-id" not in hdrs

    def test_transform_request_body_chat_has_auto_generate_name_false(self, adapter):
        """chat 模式注入 auto_generate_name=false（避免 Dify 自动生成会话标题噪音）"""
        import json
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out.get("auto_generate_name") is False

    def test_transform_request_body_workflow_branch(self, adapter):
        """workflow 模式：query 包在 inputs 里，无 conversation_id"""
        import json
        adapter._app_type = "workflow"
        body = json.dumps({
            "messages": [
                {"role": "user", "content": "执行流程"}
            ],
            "stream": True,
        }).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out["inputs"] == {"query": "执行流程"}
        assert out["response_mode"] == "streaming"
        assert "conversation_id" not in out
        assert "auto_generate_name" not in out

    def test_map_path_workflow_dispatches_to_workflows_run(self, adapter):
        """workflow app_type → v1/workflows/run；chat/agent → v1/chat-messages"""
        adapter._app_type = "workflow"
        assert adapter.map_path("v1/chat/completions") == "v1/workflows/run"
        adapter._app_type = "chat"
        assert adapter.map_path("v1/chat/completions") == "v1/chat-messages"
        adapter._app_type = "agent"
        assert adapter.map_path("v1/chat/completions") == "v1/chat-messages"

    def test_map_path_invalid_app_type_falls_back_to_chat(self, adapter):
        """非法 _app_type 值回退到 chat（不崩）"""
        adapter._app_type = "bogus"
        assert adapter.map_path("v1/chat/completions") == "v1/chat-messages"

    def test_transform_request_body_non_chat_identity(self, adapter):
        """非 chat/completions 路径不转换"""
        b = b'{"foo":1}'
        assert adapter.transform_request_body("v1/models", b, {}) == b
        assert adapter.transform_request_body("v1/sessions", b, {}) == b

    def test_transform_request_body_empty_or_invalid_identity(self, adapter):
        assert adapter.transform_request_body("v1/chat/completions", b"", {}) == b""
        assert adapter.transform_request_body("v1/chat/completions", b"not json", {}) == b"not json"

    def test_transform_request_body_multimodal_content(self, adapter):
        """OpenAI 多模态 content（list）拼接 text 部分"""
        import json
        body = json.dumps({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]}],
            "stream": True}).encode()
        out = json.loads(adapter.transform_request_body("v1/chat/completions", body, {}))
        assert out["query"] == "hello world"

    def test_transform_response_body_blocking_dify_to_openai(self, adapter):
        """Dify blocking JSON {answer,...} → OpenAI chat.completion"""
        import json
        body = json.dumps({
            "id": "msg-1", "answer": "最终答案", "conversation_id": "c1",
        }).encode()
        out = json.loads(adapter.transform_response_body("v1/chat/completions", body, {}))
        assert out["object"] == "chat.completion"
        assert out["choices"][0]["message"]["content"] == "最终答案"
        assert out["choices"][0]["message"]["role"] == "assistant"
        assert out["conversation_id"] == "c1"

    def test_transform_response_body_non_dify_identity(self, adapter):
        """非 Dify blocking 响应（无 answer 字段）原样返回"""
        b = b'{"error":"something"}'
        assert adapter.transform_response_body("v1/chat/completions", b, {}) == b

    def test_is_sse_transformable_only_chat(self, adapter):
        assert adapter.is_sse_transformable("v1/chat/completions") is True
        assert adapter.is_sse_transformable("v1/models") is False
        assert adapter.is_sse_transformable("v1/sessions") is False

    async def test_transform_sse_stream_dify_to_openai(self, adapter):
        """Dify SSE message/message_end → OpenAI chunk + [DONE]"""
        dify_sse = (
            'event: message\ndata: {"answer":"你","id":"m1","conversation_id":"c1"}\n\n'
            'event: message\ndata: {"answer":"好","id":"m1"}\n\n'
            'event: message_end\ndata: {"conversation_id":"c1"}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        # 两个 content chunk + [DONE]
        assert text.count("chat.completion.chunk") == 2
        # 多字节字符不截断（两个 event 各含一个字符）
        assert "你" in text and "好" in text
        assert text.endswith("data: [DONE]\n\n")
        # msg_id 携带（json.dumps 默认带空格）
        assert '"id": "m1"' in text

    async def test_message_end_extracts_usage_chunk(self, adapter):
        """message_end 携带 metadata.usage → 输出 OpenAI 兼容 usage chunk + [DONE]"""
        dify_sse = (
            'event: message\ndata: {"answer":"hi","id":"m1"}\n\n'
            'event: message_end\ndata: {"id":"m1","metadata":{"usage":'
            '{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        # usage chunk 在 [DONE] 之前
        assert '"usage":' in text
        assert '"prompt_tokens": 10' in text
        assert '"completion_tokens": 5' in text
        assert '"total_tokens": 15' in text
        # 末尾仍是 [DONE]
        assert text.endswith("data: [DONE]\n\n")
        # usage chunk 也带 msg_id（便于关联）
        assert '"id": "m1"' in text

    async def test_message_end_no_usage_only_done(self, adapter):
        """message_end 无 metadata.usage → 只输出 [DONE]，不构造空 usage chunk"""
        dify_sse = (
            'event: message\ndata: {"answer":"hi","id":"m1"}\n\n'
            'event: message_end\ndata: {"conversation_id":"c1"}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert '"usage"' not in text
        assert text.endswith("data: [DONE]\n\n")

    async def test_message_end_usage_alt_field_names(self, adapter):
        """Dify 部分版本 usage 字段名是 input_tokens/output_tokens/total → 归一化"""
        dify_sse = (
            'event: message_end\ndata: {"metadata":{"usage":'
            '{"input_tokens":7,"output_tokens":3,"total":10}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert '"prompt_tokens": 7' in text
        assert '"completion_tokens": 3' in text
        assert '"total_tokens": 10' in text

    async def test_message_end_malformed_json_falls_back_to_done(self, adapter):
        """message_end data JSON 解析失败 → 兜底返回 [DONE]，不崩"""
        dify_sse = b'event: message_end\ndata: {bad json\n\n'

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert text == "data: [DONE]\n\n"

    async def test_transform_sse_stream_handles_chunk_split(self, adapter):
        """event 跨字节块分割时不截断（缓冲按 \\n\\n 边界）"""
        full = (
            b'event: message\ndata: {"answer":"hi","id":"m1"}\n\n'
            b'event: message_end\ndata: {}\n\n'
        )
        # 切成 3 段，在 event 中间断开
        p1, p2, p3 = full[:10], full[10:40], full[40:]

        async def byte_iter():
            for p in (p1, p2, p3):
                yield p

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert "hi" in text
        assert text.endswith("data: [DONE]\n\n")

    async def test_transform_sse_stream_non_chat_passthrough(self, adapter):
        """非 chat 路径 SSE 透传不修改"""
        raw = b"event: ping\ndata: {}\n\n"

        async def byte_iter():
            yield raw

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/models", byte_iter(), {})])
        assert out == raw

    async def test_build_upstream_url_uses_mapped_path(self, adapter):
        # 调用方负责 map_path（proxy_handler）；adapter.build_upstream_url 不二次映射
        mapped = adapter.map_path("v1/chat/completions")
        url = await adapter.build_upstream_url(AGENT_ID, mapped, None)
        assert url.endswith("/v1/chat-messages")

    # ── workflow SSE 转换分支（B2 双模扩展）──

    async def test_transform_sse_stream_workflow_text_chunk(self, adapter):
        """workflow text_chunk 事件 → OpenAI chunk with data.text"""
        adapter._app_type = "workflow"
        dify_sse = (
            'event: text_chunk\ndata: {"data":{"text":"你好"}}\n\n'
            'event: workflow_finished\ndata: {"data":{"outputs":{}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert "chat.completion.chunk" in text
        assert "你好" in text
        assert text.endswith("data: [DONE]\n\n")

    async def test_transform_sse_stream_workflow_finished_fallback_outputs(self, adapter):
        """无 text_chunk 时，workflow_finished 兜底取 outputs.text/answer/output"""
        adapter._app_type = "workflow"
        dify_sse = (
            'event: workflow_finished\ndata: {"data":{"outputs":'
            '{"text":"兜底文本"}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert "兜底文本" in text
        assert text.endswith("data: [DONE]\n\n")

    async def test_workflow_finished_extracts_usage_with_text_chunk(self, adapter):
        """已发过 text_chunk → workflow_finished 只发 usage chunk + [DONE]，不重复发文本。
        Dify 真实格式：data.total_tokens 顶层字段（workflow 不拆分 prompt/completion）。"""
        adapter._app_type = "workflow"
        dify_sse = (
            'event: text_chunk\ndata: {"data":{"text":"hi"}}\n\n'
            'event: workflow_finished\ndata: {"data":{"outputs":{"text":"hi"},'
            '"total_tokens":177,"total_steps":3}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        # usage chunk 在 [DONE] 之前
        assert '"usage":' in text
        # workflow 只有 total_tokens，prompt/completion 为 0
        assert '"prompt_tokens": 0' in text
        assert '"completion_tokens": 0' in text
        assert '"total_tokens": 177' in text
        assert text.endswith("data: [DONE]\n\n")
        # 已发过 text_chunk，不应再发兜底 outputs.text
        assert text.count('"hi"') == 1

    async def test_workflow_finished_extracts_usage_metadata_path(self, adapter):
        """部分 Dify 版本 usage 在 data.metadata.usage（完整三字段）→ 优先用此路径"""
        adapter._app_type = "workflow"
        dify_sse = (
            'event: text_chunk\ndata: {"data":{"text":"hi"}}\n\n'
            'event: workflow_finished\ndata: {"data":{"outputs":{"text":"hi"},'
            '"metadata":{"usage":{"prompt_tokens":8,"completion_tokens":2,'
            '"total_tokens":10}}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        # metadata.usage 优先，三字段完整
        assert '"prompt_tokens": 8' in text
        assert '"completion_tokens": 2' in text
        assert '"total_tokens": 10' in text

    async def test_workflow_finished_no_usage_only_done(self, adapter):
        """workflow_finished 无 total_tokens 也无 metadata.usage → 只发 [DONE]"""
        adapter._app_type = "workflow"
        dify_sse = (
            'event: text_chunk\ndata: {"data":{"text":"hi"}}\n\n'
            'event: workflow_finished\ndata: {"data":{"outputs":{}}}\n\n'
        ).encode()

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert '"usage"' not in text
        assert text.endswith("data: [DONE]\n\n")

    async def test_workflow_finished_malformed_json_falls_back_to_done(self, adapter):
        """workflow_finished data JSON 解析失败 → 兜底返回 [DONE]，不崩"""
        adapter._app_type = "workflow"
        dify_sse = b'event: workflow_finished\ndata: {bad json\n\n'

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert text == "data: [DONE]\n\n"

    async def test_transform_sse_stream_workflow_error_event(self, adapter):
        """workflow error 事件 → OpenAI chunk 带 error.message"""
        adapter._app_type = "workflow"
        dify_sse = b'event: error\ndata: {"message":"workflow crashed"}\n\n'

        async def byte_iter():
            yield dify_sse

        out = b"".join([c async for c in adapter.transform_sse_stream(
            "v1/chat/completions", byte_iter(), {})])
        text = out.decode()
        assert '"error"' in text
        assert "workflow crashed" in text

    def test_transform_response_body_workflow_blocking_outputs(self, adapter):
        """workflow blocking 响应：data.outputs.text/answer/output 兜底"""
        import json
        adapter._app_type = "workflow"
        body = json.dumps({
            "id": "wf-1",
            "data": {"outputs": {"text": "workflow 答案"}},
        }).encode()
        out = json.loads(adapter.transform_response_body("v1/chat/completions", body, {}))
        assert out["object"] == "chat.completion"
        assert out["choices"][0]["message"]["content"] == "workflow 答案"

    def test_transform_response_body_workflow_blocking_no_outputs_identity(self, adapter):
        """workflow blocking 响应无 outputs → 原样返回"""
        import json
        adapter._app_type = "workflow"
        body = json.dumps({"id": "wf-1", "data": {}}).encode()
        assert adapter.transform_response_body("v1/chat/completions", body, {}) == body

    async def test_workflow_emitted_flag_resets_per_request(self, adapter):
        """transform_sse_stream 入口重置 _workflow_emitted，避免跨请求残留"""
        adapter._app_type = "workflow"
        adapter._workflow_emitted = True

        async def byte_iter():
            return
            yield b""  # make it an async generator

        # 触发 transform_sse_stream 入口重置
        async for _ in adapter.transform_sse_stream("v1/chat/completions", byte_iter(), {}):
            pass
        assert adapter._workflow_emitted is False
