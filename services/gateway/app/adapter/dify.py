"""Dify engine adapter — OpenAI ↔ Dify 协议归一化（双模：Pod-DNS + 外部实例）。

支持 Dify 三种 app_type：
  - chat:     v1/chat-messages（OpenAI 兼容消息流，conversation_id 跨轮继承）
  - agent:    v1/chat-messages（与 chat 同路径，agent 模式有 agent_message 事件）
  - workflow: v1/workflows/run（无 conversation_id；outputs 出参兜底取 text/answer/output）

路径映射（统一 v1/* → Dify 原生，按 app_type 派生 chat 路径）：
  v1/chat/completions → v1/chat-messages (chat/agent) / v1/workflows/run (workflow)
  v1/models           → parameters
  v1/sessions         → v1/conversations
  v1/sessions/{id}    → v1/conversations/{id}
  v1/sessions/{id}/messages → v1/conversations/{id}/messages

请求体转换（OpenAI → Dify）：
  chat/agent: {messages, stream} → {inputs, query, response_mode, conversation_id,
                                    user, auto_generate_name: false}
  workflow:   {messages, stream} → {inputs: {query}, user, response_mode}

SSE 转换（Dify → OpenAI，按 event 边界缓冲，不截断多字节字符）：
  chat:    event: message / agent_message → delta.content=answer
           event: message_end             → [DONE]
  workflow: event: text_chunk             → delta.content=data.text
            event: workflow_finished      → [DONE]（无 text_chunk 时 fallback
                                              outputs.text/answer/output）
            event: error                  → error chunk

DNS（Pod 模式）: engine-dify-{short_id}.{ns}.svc.cluster.local:8080（契约 §1）。
外部实例模式: 由 proxy 解析 AgentDeployment.engine_url 直接注入 base_url（本 adapter 不查 DB）。
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from ..langfuse_client import (
    create_or_update_agent_thought_span,
    create_or_update_workflow_node_span,
)
from .base import EngineAdapter

logger = logging.getLogger(__name__)

# Dify app_type 合法值
_VALID_APP_TYPES = frozenset({"chat", "agent", "workflow"})


class DifyAdapter(EngineAdapter):
    engine_type = "DIFY"

    def __init__(self, k8s_namespace: str = "unionagents") -> None:
        super().__init__(k8s_namespace)
        # 由 proxy 在 map_path 之前注入（来自 AgentDefinition.model_config.dify.app_type）。
        # 缺省 "chat" 保持向后兼容（Repo2 仅支持 chat）。
        self._app_type: str = "chat"
        # workflow 模式下记录本次请求是否已发 text_chunk；用于 workflow_finished 兜底。
        # 在 transform_sse_stream 入口重置。
        self._workflow_emitted: bool = False

    def _get_app_type(self) -> str:
        """返回当前 app_type（chat/agent/workflow），未注入时为 "chat"。"""
        return self._app_type if self._app_type in _VALID_APP_TYPES else "chat"

    def _resolve_dify_path(self, path: str, app_type: str) -> str:
        """按 app_type 派生 chat/completions 对应的 Dify 原生路径。

        - workflow → v1/workflows/run
        - chat/agent（及其它） → v1/chat-messages
        """
        if path == "v1/chat/completions":
            if app_type == "workflow":
                return "v1/workflows/run"
            return "v1/chat-messages"
        return path

    async def build_upstream_url(
        self, agent_id: str, path: str, query: str | None = None
    ) -> str:
        return self._build_url(agent_id, path, query)

    def map_path(self, path: str) -> str:
        """统一路径 → Dify 原生路径（chat 路径按 app_type 派生）。"""
        if path == "v1/chat/completions":
            return self._resolve_dify_path(path, self._get_app_type())
        if path == "v1/models":
            return "parameters"
        if path == "v1/sessions":
            return "v1/conversations"
        if path.startswith("v1/sessions/"):
            rest = path.removeprefix("v1/sessions/")
            return f"v1/conversations/{rest}"
        return path

    def transform_headers(
        self, raw_headers: dict[str, str], api_server_key: str
    ) -> dict[str, str]:
        headers = self._base_headers(raw_headers, api_server_key)
        # Dify 用 body 里的 conversation_id；同时透传私有头便于 transform_request_body 弹出。
        # workflow 模式不需要 conversation_id，但保留头不影响（transform_request_body 不弹）。
        session_id = headers.pop("x-session-id", "")
        if session_id:
            headers["x-dify-conversation-id"] = session_id
        return headers

    # ── Session / Files（Dify conversations API；workflow 模式下会话 API 仍可用）──

    def get_session_create_url(self, agent_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path("v1/sessions"))

    def get_session_list_url(self, agent_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path("v1/sessions"))

    def get_session_detail_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}"))

    def get_session_update_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}"))

    def get_session_delete_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}"))

    def get_session_messages_url(self, agent_id: str, session_id: str) -> str | None:
        return self._build_url(agent_id, self.map_path(f"v1/sessions/{session_id}/messages"))

    def get_files_url(self, agent_id: str, query: str | None = None) -> str | None:
        return self._build_url(agent_id, "v1/files", query)

    # ── Request body transform（OpenAI → Dify，按 app_type 分支）──

    def transform_request_body(
        self, path: str, body: bytes, headers: dict[str, str]
    ) -> bytes:
        if path != "v1/chat/completions" or not body:
            return body
        app_type = self._get_app_type()
        try:
            openai_req = json.loads(body)
        except json.JSONDecodeError:
            return body

        query_text = _extract_last_user_message(openai_req.get("messages", []))
        response_mode = "streaming" if openai_req.get("stream") else "blocking"
        user = openai_req.get("user") or "union-user"

        if app_type == "workflow":
            # workflow：无 conversation_id，query 包在 inputs 里
            dify_req = {
                "inputs": {"query": query_text},
                "response_mode": response_mode,
                "user": user,
            }
            return json.dumps(dify_req, ensure_ascii=False).encode()

        # chat / agent：conversation_id 跨轮继承；auto_generate_name=false 避免噪音
        conversation_id = headers.pop("x-dify-conversation-id", "") or ""
        dify_req = {
            "inputs": {},
            "query": query_text,
            "response_mode": response_mode,
            "conversation_id": conversation_id,
            "user": user,
            "auto_generate_name": False,
        }
        return json.dumps(dify_req, ensure_ascii=False).encode()

    # ── Response body transform（非 SSE blocking 响应：Dify JSON → OpenAI completion）──

    def transform_response_body(
        self, path: str, body: bytes, headers: dict[str, str]
    ) -> bytes:
        if path != "v1/chat/completions" or not body:
            return body
        app_type = self._get_app_type()
        try:
            d = json.loads(body)
        except json.JSONDecodeError:
            return body
        if not isinstance(d, dict):
            return body

        # workflow blocking 响应：data.outputs.text/answer/output 兜底
        if app_type == "workflow":
            outputs = (d.get("data") or {}).get("outputs") or {}
            content = (
                outputs.get("text") or outputs.get("answer") or outputs.get("output") or ""
            )
            if not content:
                return body
            completion = {
                "id": d.get("id", ""),
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
            }
            return json.dumps(completion, ensure_ascii=False).encode()

        # chat/agent blocking 响应：answer 字段
        if "answer" not in d:
            return body
        completion = {
            "id": d.get("id", ""),
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": d.get("answer", "")},
                    "finish_reason": "stop",
                }
            ],
            "conversation_id": d.get("conversation_id", ""),
        }
        return json.dumps(completion, ensure_ascii=False).encode()

    # ── SSE 流式转换（Dify event stream → OpenAI chunk stream）──

    def is_sse_transformable(self, path: str) -> bool:
        """仅 chat/completions 的 SSE 需要转换（其余 Dify 响应 OpenAI 兼容或非 SSE）。"""
        return path == "v1/chat/completions"

    async def transform_sse_stream(
        self, path: str, byte_iter: AsyncIterator[bytes], headers: dict[str, str]
    ) -> AsyncIterator[bytes]:
        """流式转换：按 ``\\n\\n`` event 边界缓冲，逐事件转 OpenAI chunk。

        不缓冲整个响应（保持流式），但保证单个 event 不被字节块截断。
        每次进入重置 workflow_emitted 标志，避免跨请求残留。
        agent_thought 事件不产生 OpenAI chunk，但若 _langfuse_trace 已注入，
        会为每个 thought 步骤创建 SPAN observation（详见 _convert_dify_event_block）。
        workflow 的 node_started/node_finished 同理，为每个节点创建 SPAN。
        """
        if not self.is_sse_transformable(path):
            async for chunk in byte_iter:
                yield chunk
            return

        self._workflow_emitted = False
        buffer = ""
        state: dict[str, Any] = {}  # 携带 msg_id / conversation_id / _agent_thought_spans 跨事件
        async for chunk in byte_iter:
            buffer += chunk.decode("utf-8", errors="replace")
            # 完整事件以空行分隔
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                out = self._convert_dify_event_block(block, state)
                if out:
                    yield out.encode("utf-8")
        # 刷出尾部无空行结束的残余事件
        if buffer.strip():
            out = self._convert_dify_event_block(buffer, state)
            if out:
                yield out.encode("utf-8")

    def _convert_dify_event_block(self, block: str, state: dict[str, Any]) -> str:
        """转换单个完整 Dify event block → OpenAI SSE 文本（可能为空）。

        按 self._app_type 派生事件名：
          chat/agent: message / agent_message / message_end
          workflow:   text_chunk / workflow_finished / error
        """
        event_type = ""
        data_line = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_line = line.removeprefix("data:").strip()

        # Dify 1.14+ SSE 格式：没有独立 event: 行，event 类型在 data JSON 的 "event" 字段里
        if not event_type and data_line:
            try:
                _p = json.loads(data_line)
                if isinstance(_p, dict) and _p.get("event"):
                    event_type = _p["event"]
            except json.JSONDecodeError:
                pass

        app_type = self._get_app_type()

        # ── chat / agent 事件 ──
        if event_type in ("message", "agent_message"):
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                return ""
            if "id" in payload:
                state["msg_id"] = payload["id"]
            if "conversation_id" in payload:
                conv_id = payload["conversation_id"]
                # 首次拿到 conversation_id 时，把它作为 session_id 写入 Langfuse trace
                # （覆盖 proxy.py 传入的 X-Session-Id 占位，Dify 自身分配的更稳定）
                if conv_id and not state.get("conversation_id"):
                    if self._langfuse_trace is not None:
                        try:
                            self._langfuse_trace.update(session_id=conv_id)
                        except Exception as e:
                            logger.warning(f"Langfuse trace session_id update failed: {e}")
                state["conversation_id"] = conv_id
            content = payload.get("answer", "")
            chunk = {
                "id": state.get("msg_id", ""),
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": content}, "index": 0}],
            }
            return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        if event_type == "message_end":
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                return "data: [DONE]\n\n"
            # Dify 1.14+ message_end 携带 metadata.usage（prompt/completion/total_tokens），
            # 转成 OpenAI 兼容 usage chunk 一并下发，让 proxy.py 的 _extract_usage_from_sse
            # 能从累积的 chat_chunks 提取 token 用量，写入 Langfuse trace.generation.usage。
            meta = payload.get("metadata") if isinstance(payload, dict) else None
            usage = meta.get("usage") if isinstance(meta, dict) else None
            parts: list[str] = []
            if isinstance(usage, dict):
                usage_chunk = {
                    "id": state.get("msg_id", ""),
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
                        "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
                        "total_tokens": usage.get("total_tokens") or usage.get("total") or 0,
                    },
                }
                parts.append(f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n")
            parts.append("data: [DONE]\n\n")
            return "".join(parts)

        # ── workflow 事件 ──
        if app_type == "workflow" and event_type == "text_chunk":
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                return ""
            data_field = payload.get("data") or {}
            content = data_field.get("text", "") if isinstance(data_field, dict) else ""
            if content:
                self._workflow_emitted = True
            chunk = {
                "id": state.get("msg_id", ""),
                "object": "chat.completion.chunk",
                "choices": [{"delta": {"content": content}, "index": 0}],
            }
            return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        # ── workflow node 事件：多节点 workflow 每个节点发 started/finished ──
        # 不产生 OpenAI chunk，为每个节点创建 SPAN observation 记录 input/output/耗时。
        # node_started 先到（带 inputs + created_at），node_finished 后到（补 title/outputs/elapsed_time）。
        if app_type == "workflow" and event_type in ("node_started", "node_finished"):
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                return ""
            data = payload.get("data") or {}
            if isinstance(data, dict):
                create_or_update_workflow_node_span(
                    self._langfuse_trace,
                    state.setdefault("_workflow_node_spans", {}),
                    node_id=data.get("node_id"),
                    title=data.get("title"),
                    node_type=data.get("node_type"),
                    inputs=data.get("inputs"),
                    outputs=data.get("outputs"),
                    elapsed_time=data.get("elapsed_time"),
                    status=data.get("status"),
                    error=data.get("error"),
                    created_at=data.get("created_at"),
                )
            return ""

        if app_type == "workflow" and event_type == "workflow_finished":
            # Dify 1.14+ workflow_finished 事件携带 data.total_tokens（顶层字段，
            # 不是 metadata.usage）。workflow 不拆分 prompt/completion，只给 total。
            # 提取后发射 OpenAI 兼容 usage chunk，让 proxy.py 的 _extract_usage_from_sse
            # 能从累积的 chat_chunks 提取 token 用量写入 Langfuse trace.generation.usage。
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                return "data: [DONE]\n\n"
            data = payload.get("data") or {}
            data_dict = data if isinstance(data, dict) else {}
            # Dify workflow：data.total_tokens 顶层；data.metadata.usage 作为兼容路径
            total = data_dict.get("total_tokens")
            meta = data_dict.get("metadata") if isinstance(data_dict, dict) else None
            usage = meta.get("usage") if isinstance(meta, dict) else None
            parts: list[str] = []
            # 已发过 text_chunk → 直接 [DONE]；否则从 outputs 兜底取一次内容
            if not self._workflow_emitted:
                outputs = data_dict.get("outputs") or {}
                content = (
                    outputs.get("text") or outputs.get("answer") or outputs.get("output") or ""
                )
                if content:
                    chunk = {
                        "id": state.get("msg_id", ""),
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"delta": {"content": content}, "index": 0},
                            {"finish_reason": "stop", "index": 0},
                        ],
                    }
                    parts.append(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n")
            # 优先用 metadata.usage（完整三字段），其次 data.total_tokens（只有总数）
            if isinstance(usage, dict):
                usage_chunk = {
                    "id": state.get("msg_id", ""),
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
                        "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
                        "total_tokens": usage.get("total_tokens") or usage.get("total") or 0,
                    },
                }
                parts.append(f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n")
            elif total:
                usage_chunk = {
                    "id": state.get("msg_id", ""),
                    "object": "chat.completion.chunk",
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": int(total),
                    },
                }
                parts.append(f"data: {json.dumps(usage_chunk, ensure_ascii=False)}\n\n")
            parts.append("data: [DONE]\n\n")
            return "".join(parts)

        if app_type == "workflow" and event_type == "error":
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                payload = {}
            message = payload.get("message") or payload.get("data", {}).get("message") or ""
            err_chunk = {
                "id": state.get("msg_id", ""),
                "object": "chat.completion.chunk",
                "choices": [
                    {"delta": {"content": ""}, "index": 0},
                ],
                "error": {"message": message, "type": "upstream_error"},
            }
            return f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"

        # ── agent_thought 事件：Dify agent 模式的推理步骤（不产生 OpenAI chunk）──
        # 为当前 trace 创建/更新 SPAN observation，记录 thought/tool/observation。
        # Dify 通常分两次发：先 thought+tool_input，后 observation（同 position）。
        if event_type == "agent_thought":
            try:
                payload = json.loads(data_line) if data_line else {}
            except json.JSONDecodeError:
                return ""
            create_or_update_agent_thought_span(
                self._langfuse_trace,
                state.setdefault("_agent_thought_spans", {}),
                position=payload.get("position"),
                thought=payload.get("thought"),
                tool=payload.get("tool"),
                tool_input=payload.get("tool_input"),
                observation=payload.get("observation"),
            )
            return ""

        return ""


def _extract_last_user_message(messages: list) -> str:
    """取最后一条 role=user 的消息 content。"""
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # OpenAI 多模态 content：拼接 text 部分
                return "".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content)
    return ""
