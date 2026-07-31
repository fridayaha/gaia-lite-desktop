"""Gateway main.py 测试

覆盖:
  - X-Engine-Type header 转换
  - 鉴权
  - 引擎 URL 构造
"""

from unittest.mock import AsyncMock, patch

import json

import pytest

from app.settings import settings


class TestInjectAttachmentsIfChat:
    """_inject_attachments_if_chat 纯函数：覆盖 chat/completions + runs 两种 body 形态。"""

    def _call(self, path: str, body: dict) -> dict:
        from app.proxy import _inject_attachments_if_chat
        out = _inject_attachments_if_chat(path, "POST", json.dumps(body).encode("utf-8"))
        return json.loads(out.decode("utf-8"))

    def test_runs_input_with_attachments_synthesized(self):
        """/v1/runs 当前轮 input + 顶层 attachments → input 合成 hint，attachments 剥离"""
        body = {
            "input": "看一下",
            "attachments": [{"path": "uploads/a.pdf"}],
            "session_id": "s1",
            "conversation_history": [],
        }
        out = self._call("v1/runs", body)
        assert out["input"] == "看一下\n\n[Attached files: uploads/a.pdf]"
        assert "attachments" not in out
        assert out["session_id"] == "s1"

    def test_runs_input_empty_uses_fallback(self):
        body = {"input": "", "attachments": [{"path": "uploads/a.pdf"}]}
        out = self._call("v1/runs", body)
        assert out["input"] == "I've uploaded 1 file(s): uploads/a.pdf"
        assert "attachments" not in out

    def test_runs_conversation_history_injected(self):
        """/v1/runs 历史轮 conversation_history 每条 attachments 合成进 content"""
        body = {
            "input": "继续",
            "conversation_history": [
                {"role": "user", "content": "看这个", "attachments": [{"path": "u/a.pdf"}]},
                {"role": "assistant", "content": "好的"},
            ],
        }
        out = self._call("v1/runs", body)
        assert out["conversation_history"][0]["content"] == "看这个\n\n[Attached files: u/a.pdf]"
        assert "attachments" not in out["conversation_history"][0]
        assert out["conversation_history"][1]["content"] == "好的"

    def test_no_attachments_bytes_untouched(self):
        """无 attachments → 原样返回同一 bytes（不重序列化）"""
        from app.proxy import _inject_attachments_if_chat
        raw = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                         ensure_ascii=False).encode("utf-8")
        assert _inject_attachments_if_chat("v1/chat/completions", "POST", raw) is raw

    def test_non_chat_path_untouched(self):
        from app.proxy import _inject_attachments_if_chat
        raw = b'{"foo": "bar"}'
        assert _inject_attachments_if_chat("v1/models", "POST", raw) is raw

    def test_get_method_untouched(self):
        from app.proxy import _inject_attachments_if_chat
        raw = b'{"foo": "bar"}'
        assert _inject_attachments_if_chat("v1/chat/completions", "GET", raw) is raw


class TestInjectCurrentTime:
    """proxy 转发前注入当前时间 ephemeral system（仅 HERMES POST）。"""

    AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"

    @pytest.mark.asyncio
    async def test_chat_completions_injects_system_message(self, client, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(settings, "inject_current_time", True)
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        with patch("app.proxy._stream", _capture_stream), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "今天几号"}]},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 200
        sent = json.loads(capture["body"].decode("utf-8"))
        # 末尾追加 system 时间提示，原 user 不变
        assert sent["messages"][0] == {"role": "user", "content": "今天几号"}
        assert sent["messages"][-1]["role"] == "system"
        assert "Asia/Shanghai" in sent["messages"][-1]["content"]
        assert "请以此为准" in sent["messages"][-1]["content"]

    @pytest.mark.asyncio
    async def test_runs_injects_instructions(self, client, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(settings, "inject_current_time", True)
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        body = {"input": "今天几号", "conversation_history": [], "session_id": "s1", "model": "m"}
        with patch("app.proxy._stream", _capture_stream), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/runs", json=body,
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 200
        sent = json.loads(capture["body"].decode("utf-8"))
        assert "Asia/Shanghai" in sent["instructions"]
        assert sent["input"] == "今天几号"  # 不变
        assert sent["conversation_history"] == []

    @pytest.mark.asyncio
    async def test_inject_appends_to_existing_system(self, client, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(settings, "inject_current_time", True)
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        with patch("app.proxy._stream", _capture_stream), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            await client.post(
                "/v1/chat/completions",
                json={"model": "m",
                      "messages": [{"role": "system", "content": "You are X"},
                                   {"role": "user", "content": "hi"}]},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        sent = json.loads(capture["body"].decode("utf-8"))
        assert len(sent["messages"]) == 2  # 追加不新增
        assert sent["messages"][0]["content"].startswith("You are X")
        assert "Asia/Shanghai" in sent["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_inject_disabled_when_setting_off(self, client, monkeypatch):
        from types import SimpleNamespace
        monkeypatch.setattr(settings, "inject_current_time", False)
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        with patch("app.proxy._stream", _capture_stream), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            await client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        sent = json.loads(capture["body"].decode("utf-8"))
        assert all(m.get("role") != "system" for m in sent["messages"])  # 关闭不注入

    @pytest.mark.asyncio
    async def test_inject_skipped_for_non_hermes(self, client, monkeypatch):
        monkeypatch.setattr(settings, "inject_current_time", True)
        capture = {}

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        with patch("app.proxy._stream", _capture_stream):
            await client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "OPENCLAW"})
        sent = json.loads(capture["body"].decode("utf-8"))
        assert all(m.get("role") != "system" for m in sent["messages"])  # 非 Hermes 不注入

# 在 test 中直接测试 proxy 的 header 处理逻辑，
# 通过 mock _stream 异步生成器来隔离测试


class TestHeaderTransformation:
    """header 转换逻辑现在由 HermesAdapter.transform_headers 实现（B1）。"""

    def test_header_transformation_direct(self):
        from app.adapter import HermesAdapter
        adapter = HermesAdapter(k8s_namespace="unionagents")

        raw_headers = {
            "x-agent-id": "test-agent",
            "x-engine-type": "HERMES",
            "x-session-id": "session-123",
            "authorization": "Bearer test-token",
        }
        headers = adapter.transform_headers(raw_headers, "engine-key")

        assert headers.get("x-hermes-session-id") == "session-123"
        assert "x-engine-type" not in headers
        assert "x-session-id" not in headers
        assert headers.get("authorization") == "Bearer engine-key"

    def test_no_session_id_no_transform(self):
        from app.adapter import HermesAdapter
        adapter = HermesAdapter(k8s_namespace="unionagents")

        headers = adapter.transform_headers(
            {"x-agent-id": "test-agent", "x-engine-type": "HERMES"}, "engine-key"
        )
        assert "x-hermes-session-id" not in headers
        assert "x-session-id" not in headers

    def test_origin_referer_stripped(self):
        from app.adapter import HermesAdapter
        adapter = HermesAdapter(k8s_namespace="unionagents")

        headers = adapter.transform_headers(
            {
                "x-agent-id": "test-agent",
                "origin": "http://example.com",
                "referer": "http://example.com/page",
                "authorization": "Bearer test",
                "x-engine-type": "HERMES",
                "x-session-id": "s1",
            },
            "engine-key",
        )
        assert "origin" not in headers
        assert "referer" not in headers

    def test_client_type_not_forwarded_to_engine(self):
        """x-client-type 是 gateway 内部头（Langfuse channel_type 派生），不透传引擎。"""
        from app.adapter import HermesAdapter
        adapter = HermesAdapter(k8s_namespace="unionagents")

        headers = adapter.transform_headers(
            {
                "x-agent-id": "test-agent",
                "x-engine-type": "HERMES",
                "x-client-type": "android",
            },
            "engine-key",
        )
        assert "x-client-type" not in headers


class TestResolveChannelType:
    """proxy._resolve_channel_type：X-Client-Type 头 → Langfuse channel_type 白名单映射。"""

    def test_android_header_maps_to_android(self):
        from app.proxy import _resolve_channel_type

        assert _resolve_channel_type({"x-client-type": "android"}) == "android"

    def test_ios_header_maps_to_ios(self):
        from app.proxy import _resolve_channel_type

        assert _resolve_channel_type({"x-client-type": "ios"}) == "ios"

    def test_harmony_header_maps_to_harmony(self):
        from app.proxy import _resolve_channel_type

        assert _resolve_channel_type({"x-client-type": "harmony"}) == "harmony"

    def test_case_insensitive(self):
        from app.proxy import _resolve_channel_type

        assert _resolve_channel_type({"x-client-type": "Android"}) == "android"

    def test_missing_header_defaults_web(self):
        from app.proxy import _resolve_channel_type

        assert _resolve_channel_type({}) == "web"

    def test_bogus_value_falls_back_web(self):
        """白名单外任意字符串（含伪造 web 以外维度）一律记 web。"""
        from app.proxy import _resolve_channel_type

        assert _resolve_channel_type({"x-client-type": "curl"}) == "web"
        assert _resolve_channel_type({"x-client-type": "feishu"}) == "web"


class TestGatewayAuth:

    @pytest.mark.asyncio
    async def test_no_agent_id_returns_400(self, client):
        """缺少 X-Agent-ID 头应返回 400"""
        resp = await client.post("/v1/chat/completions", json={
            "model": "test", "messages": [],
        }, headers={
            "Authorization": "Bearer test-token",
        })
        assert resp.status_code == 400
        assert "X-Agent-ID" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_health_endpoint_no_auth(self, client):
        """健康检查端点不需要鉴权"""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestBuildEngineUrl:

    def test_url_naming_convention(self):
        """引擎 URL 使用 DNS 命名规范构造"""
        from app.proxy import build_engine_url

        url = build_engine_url("550e8400-e29b-41d4-a716-446655440000")
        # UUID 去掉连字符后取前 8 位
        short_id = "550e8400"
        expected = f"http://engine-hermes-{short_id}.unionagents.svc.cluster.local:8642"
        assert url == expected


class TestAdapterRoutingEndToEnd:
    """端到端：显式 /v1 路由 → proxy_handler → adapter → _stream 全链路。

    patch _stream 捕获 upstream URL + headers，验证三引擎按 X-Engine-Type
    走对应 adapter 的 DNS/端口/路径映射，且 Origin/Referer 被去除、Bearer key 注入。
    兼作 B1 本地冒烟证据（A 真实引擎未就绪，用 mock upstream）。
    """

    AGENT_ID = "550e8400-e29b-41d4-a716-446655440000"

    def _make_stream_mock(self, capture: dict):
        """返回 mock _stream：捕获 upstream_url/headers，返回 200 JSON。"""
        async def _mock_stream(request, upstream_url, headers, body):
            capture["url"] = upstream_url
            capture["headers"] = headers
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'
        return _mock_stream

    def _make_sse_stream_mock(self, capture: dict, sse_bytes: bytes):
        """返回 mock _stream：返回 SSE 流（is_sse=True），透传 sse_bytes。"""
        async def _mock_stream(request, upstream_url, headers, body):
            capture["url"] = upstream_url
            capture["headers"] = headers
            yield 200, {}, "text/event-stream", True
            yield sse_bytes
        return _mock_stream

    @pytest.mark.asyncio
    async def test_openclaw_routes_to_openclaw_dns(self, client):
        capture = {}
        with patch("app.proxy._stream", self._make_stream_mock(capture)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "OPENCLAW",
                         "Origin": "http://evil.example"})
        assert resp.status_code == 200
        assert capture["url"] == (
            "http://engine-openclaw-550e8400.unionagents.svc.cluster.local:8642/v1/chat/completions"
        )
        assert capture["headers"]["authorization"] == "Bearer change-me"
        assert "origin" not in capture["headers"]
        assert "referer" not in capture["headers"]

    @pytest.mark.asyncio
    async def test_dify_routes_to_chat_messages_8080(self, client):
        capture = {}
        with patch("app.proxy._stream", self._make_stream_mock(capture)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "DIFY",
                         "Origin": "http://evil.example"})
        assert resp.status_code == 200
        # Dify 路径映射 v1/chat/completions → v1/chat-messages，端口 8080
        assert capture["url"] == (
            "http://engine-dify-550e8400.unionagents.svc.cluster.local:8080/v1/chat-messages"
        )
        assert "origin" not in capture["headers"]

    @pytest.mark.asyncio
    async def test_dify_models_maps_to_parameters(self, client):
        capture = {}
        with patch("app.proxy._stream", self._make_stream_mock(capture)):
            resp = await client.get(
                "/v1/models",
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "DIFY"})
        assert resp.status_code == 200
        assert capture["url"].endswith("/parameters")

    @pytest.mark.asyncio
    async def test_hermes_routes_with_profile_fallback(self, client):
        """Hermes 走 profile_resolver；profile 无 engine_url 时回退 adapter DNS。"""
        from types import SimpleNamespace
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")
        with patch("app.proxy._stream", self._make_stream_mock(capture)), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES",
                         "X-Session-Id": "sess-1"})
        assert resp.status_code == 200
        assert capture["url"] == (
            "http://engine-hermes-550e8400.unionagents.svc.cluster.local:8642/v1/chat/completions"
        )
        # Hermes 会话头翻译
        assert capture["headers"].get("x-hermes-session-id") == "sess-1"

    @pytest.mark.asyncio
    async def test_hermes_security_headers_profile_injection(self, client):
        """F-GW-003：客户端伪造 X-Hermes-Profile + Origin 被丢弃，服务端注入计算值，
        authorization Bearer 注入。浏览器 SSE 不再因 Origin 返 403。"""
        from types import SimpleNamespace
        capture = {}
        # profile_resolver 返回服务端计算的 profile_name
        mock_target = SimpleNamespace(engine_url="", profile_name="computed-profile-abc")
        with patch("app.proxy._stream", self._make_stream_mock(capture)), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES",
                         # 客户端伪造（必须被忽略）
                         "X-Hermes-Profile": "spoofed-by-client",
                         "Origin": "https://evil.example",
                         "Referer": "https://evil.example/page"})
        assert resp.status_code == 200
        h = capture["headers"]
        # 服务端计算值注入，客户端伪造值被丢弃
        assert h.get("x-hermes-profile") == "computed-profile-abc"
        # Origin/Referer 去除（Hermes 收 Origin 返 403 → 浏览器 SSE Failed to fetch 主因）
        assert "origin" not in h
        assert "referer" not in h
        # 引擎 key 注入（替换客户端 JWT）
        assert h["authorization"] == "Bearer change-me"

    @pytest.mark.asyncio
    async def test_hermes_profile_resolver_access_denied_returns_403(self, client):
        """F-GW-002/004：权限闸门 AccessDenied → 403，不回退无校验路由。"""
        from app.profile_resolver import AccessDenied
        capture = {}
        with patch("app.proxy._stream", self._make_stream_mock(capture)), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(side_effect=AccessDenied("no access"))):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 403
        assert "capture" not in capture or capture.get("url") is None  # 未转发到引擎

    @pytest.mark.asyncio
    async def test_hermes_profile_not_found_returns_404(self, client):
        """F-GW-004⑥：ProfileNotFound → 404，不回退 legacy 无校验路由（避免绕过权限）。"""
        from app.profile_resolver import ProfileNotFound
        with patch("app.proxy._stream", self._make_stream_mock({})), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(side_effect=ProfileNotFound("not found"))):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_catch_all_falls_through_adapter(self, client):
        """catch-all 兜底代理同样走 adapter（未显式路由的 /v1/embeddings）。"""
        capture = {}
        with patch("app.proxy._stream", self._make_stream_mock(capture)):
            resp = await client.post(
                "/v1/embeddings", json={},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "OPENCLAW"})
        assert resp.status_code == 200
        assert capture["url"].endswith("/v1/embeddings")
        assert "engine-openclaw-" in capture["url"]

    @pytest.mark.asyncio
    async def test_missing_engine_type_defaults_hermes(self, client):
        """无 X-Engine-Type 缺省 HERMES（向后兼容）。"""
        from types import SimpleNamespace
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")
        with patch("app.proxy._stream", self._make_stream_mock(capture)), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID})
        assert resp.status_code == 200
        assert "engine-hermes-" in capture["url"]

    @pytest.mark.asyncio
    async def test_dify_sse_converted_to_openai(self, client):
        """Dify SSE 经 adapter 流式转换为 OpenAI chunk + [DONE]（端到端）"""
        capture = {}
        dify_sse = (
            'event: message\ndata: {"answer":"你好","id":"m1","conversation_id":"c1"}\n\n'
            'event: message_end\ndata: {"conversation_id":"c1"}\n\n'
        ).encode()
        with patch("app.proxy._stream", self._make_sse_stream_mock(capture, dify_sse)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": [], "stream": True},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "DIFY"})
        assert resp.status_code == 200
        body = resp.text
        assert "chat.completion.chunk" in body
        assert "你好" in body  # 多字节字符不截断
        assert body.endswith("data: [DONE]\n\n")
        # 请求体也被转换：upstream 收到 Dify body（含 query）
        assert capture["url"].endswith("/v1/chat-messages")

    @pytest.mark.asyncio
    async def test_hermes_sse_passthrough_not_transformed(self, client):
        """Hermes SSE OpenAI 兼容，透传不转换（is_sse_transformable=False）"""
        from types import SimpleNamespace
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")
        openai_sse = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
        with patch("app.proxy._stream", self._make_sse_stream_mock(capture, openai_sse)), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": [], "stream": True},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 200
        assert resp.text == openai_sse.decode()  # 原样透传

    @pytest.mark.asyncio
    async def test_sse_response_carries_anti_buffering_headers(self, client):
        """SSE 响应必须带防缓冲头：手机端实测透明代理会把整个 SSE 响应扣到流结束
        才放行（"思考几十秒→内容一下子全出来"），Cache-Control: no-transform 要求
        中间盒不要碰流内容，X-Accel-Buffering: no 压住链路里任何 nginx 的缓冲。
        引擎自带的 cache-control 须被替换而非追加（避免大小写重复键产生双头）。"""
        from types import SimpleNamespace
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")
        openai_sse = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

        async def _sse_with_engine_cache_header(request, upstream_url, headers, body):
            yield 200, {"cache-control": "no-cache"}, "text/event-stream", True
            yield openai_sse

        with patch("app.proxy._stream", _sse_with_engine_cache_header), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": [], "stream": True},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-cache, no-transform"
        assert resp.headers.get_list("cache-control") == ["no-cache, no-transform"]
        assert resp.headers["x-accel-buffering"] == "no"

    @pytest.mark.asyncio
    async def test_non_sse_response_has_no_anti_buffering_headers(self, client):
        """非 SSE 响应不叠加防缓冲头（避免影响正常 JSON 的缓存语义）"""
        capture = {}
        with patch("app.proxy._stream", self._make_stream_mock(capture)):
            resp = await client.post(
                "/v1/chat/completions", json={"model": "m", "messages": []},
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "OPENCLAW"})
        assert resp.status_code == 200
        assert "x-accel-buffering" not in resp.headers
        assert "no-transform" not in resp.headers.get("cache-control", "")

    @pytest.mark.asyncio
    async def test_chat_completions_attachments_injected_into_content(self, client):
        """结构化 attachments → 转发引擎前合成进 content 并剥离字段"""
        from types import SimpleNamespace
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        body = {
            "model": "m",
            "messages": [
                {"role": "user", "content": "看一下",
                 "attachments": [{"path": "uploads/a.pdf"}, {"path": "uploads/b.png"}]},
            ],
        }
        with patch("app.proxy._stream", _capture_stream), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json=body,
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 200
        sent = json.loads(capture["body"].decode("utf-8"))
        msg = sent["messages"][0]
        assert msg["content"] == "看一下\n\n[Attached files: uploads/a.pdf, uploads/b.png]"
        assert "attachments" not in msg  # 字段已剥离，引擎不认

    @pytest.mark.asyncio
    async def test_chat_completions_no_attachments_passthrough_untouched(self, client):
        """无 attachments 的请求不重序列化、原样透传 body"""
        from types import SimpleNamespace
        capture = {}
        mock_target = SimpleNamespace(engine_url="", profile_name="")

        async def _capture_stream(request, upstream_url, headers, body):
            capture["body"] = body
            yield 200, {}, "application/json", False
            yield b'{"ok":true}'

        payload = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        with patch("app.proxy._stream", _capture_stream), \
             patch("app.profile_resolver.profile_resolver.resolve",
                   new=AsyncMock(return_value=mock_target)):
            resp = await client.post(
                "/v1/chat/completions", json=payload,
                headers={"X-Agent-ID": self.AGENT_ID, "X-Engine-Type": "HERMES"})
        assert resp.status_code == 200
        # 无 attachments → 不重序列化，content 不变
        sent = json.loads(capture["body"].decode("utf-8"))
        assert sent["messages"][0]["content"] == "hi"
        assert "attachments" not in sent["messages"][0]
