"""_hash_last_user_message 单元测试。

验证 Gateway 写入 Langfuse trace.metadata.last_user_message_hash 的逻辑：
从 OpenAI 请求体提取最后一条 role=user 消息并 sha256 哈希前 16 字符，供
admin 监控中心做 Gateway trace ↔ Hermes 内部 trace 软关联。
"""

import hashlib
import json

import pytest


def _expected_hash(text: str) -> str:
    """与 langfuse_client._hash_last_user_message 同算法，独立实现避免 import 循环。"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


class TestHashLastUserMessage_HappyPath:
    """正常路径：从请求体正确提取最后一条 user 消息并哈希。"""

    def test_simple_string_content(self):
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "model": "gpt-4",
                "messages": [
                    {"role": "system", "content": "you are helpful"},
                    {"role": "user", "content": "hello"},
                ],
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("hello")

    def test_multimodal_list_content(self):
        """多模态 content（list of {type:text,text:...}）应拼接所有 text 字段后哈希。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "what's in "},
                            {"type": "text", "text": "this image?"},
                        ],
                    }
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("what's in this image?")

    def test_multiple_user_messages_picks_last(self):
        """多条 user 消息取最后一条（反向遍历，命中即返回）。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": "follow up"},
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("follow up")

    def test_strips_whitespace(self):
        """空白应被 strip 掉，避免不同客户端换行差异导致哈希不一致。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {"messages": [{"role": "user", "content": "  hello  \n"}]}
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("hello")


class TestHashLastUserMessage_HermesRunsBody:
    """Hermes /v1/runs 请求体格式：{"input": "...", "conversation_history": [...]}。

    Gateway 转发 Hermes /v1/runs 时 body 用此格式，不是 OpenAI messages 字段。
    hash_last_user_message 应从 ``input`` 字段提取用户消息并哈希，与 Hermes
    langfuse 插件写的 trace.input={"role":"user","content":"..."} 哈希对齐。
    """

    def test_hermes_runs_body_input_string(self):
        """Hermes runs body.input 为字符串 → 直接哈希 input。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "session_id": "api_123",
                "input": "继续",
                "model": "deepseek-chat",
                "conversation_history": [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好！"},
                ],
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("继续")

    def test_hermes_runs_body_input_strips_whitespace(self):
        """input 前后空白应被 strip 掉，避免不同客户端换行差异。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps({"input": "  hello\n"}).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("hello")

    def test_hermes_runs_body_input_empty_falls_back_to_history(self):
        """input="" → strip 后为空，回退到 conversation_history 最后一条 user。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "input": "",
                "conversation_history": [
                    {"role": "user", "content": "first question"},
                    {"role": "assistant", "content": "answer"},
                    {"role": "user", "content": "follow up"},
                ],
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("follow up")

    def test_hermes_runs_body_input_whitespace_only_falls_back_to_history(self):
        """input="   " → strip 后为空，回退到 conversation_history。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "input": "  \n  ",
                "conversation_history": [
                    {"role": "user", "content": "real question"},
                ],
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("real question")

    def test_hermes_runs_body_input_empty_no_history(self):
        """input 空且无 conversation_history → None。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps({"input": "", "model": "gpt-4"}).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_hermes_runs_body_no_input_no_history(self):
        """无 input 字段且无 conversation_history → None。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps({"session_id": "x", "model": "gpt-4"}).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_hermes_runs_body_input_as_list(self):
        """input 为 list（多模态或 messages 数组）→ 当 messages 处理。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "input": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "hi"},
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("hi")

    def test_hermes_runs_body_input_as_single_message_dict(self):
        """input 为单条 message dict（role=user）→ 当单元素 messages 处理。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {"input": {"role": "user", "content": "hello"}}
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("hello")

    def test_cross_check_gateway_body_hash_matches_hermes_plugin_trace_input(self):
        """关键一致性验证：Gateway 端 body.input 哈希 == Hermes plugin 端
        trace.input={"role":"user","content":"<同一段文本>"} 哈希。

        这是 hermes-correlation 软关联能跑通的前提：两端必须算出同一个哈希。
        """
        from app.langfuse_client import _hash_last_user_message

        user_text = "南京周边有什么推荐带小孩去历练的地方？"
        # Gateway 收到的 Hermes /v1/runs body
        gateway_body = json.dumps(
            {
                "session_id": "api_123",
                "input": user_text,
                "model": "deepseek-chat",
                "conversation_history": [
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "你好！"},
                ],
            }
        ).encode("utf-8")
        # Hermes langfuse 插件写的 trace.input（单条 message dict）
        hermes_trace_input = {"role": "user", "content": user_text}

        gw_hash = _hash_last_user_message(gateway_body)
        hermes_hash = _hash_last_user_message(hermes_trace_input)
        assert gw_hash is not None
        assert hermes_hash is not None
        assert gw_hash == hermes_hash, (
            f"Gateway hash {gw_hash} != Hermes hash {hermes_hash}，"
            f"软关联将无法匹配"
        )


class TestHashLastUserMessage_EdgeCases:
    """边界 + 错误分支：返回 None 而非抛异常。"""

    def test_none_body(self):
        from app.langfuse_client import _hash_last_user_message

        assert _hash_last_user_message(None) is None

    def test_empty_body(self):
        from app.langfuse_client import _hash_last_user_message

        assert _hash_last_user_message(b"") is None

    def test_invalid_json(self):
        from app.langfuse_client import _hash_last_user_message

        assert _hash_last_user_message(b"not json at all") is None

    def test_json_array_top_level(self):
        """非 dict 顶层（如 JSON 数组）→ None。"""
        from app.langfuse_client import _hash_last_user_message

        assert _hash_last_user_message(b"[1,2,3]") is None

    def test_no_messages_field(self):
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps({"model": "gpt-4"}).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_empty_messages_list(self):
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps({"messages": []}).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_no_user_role_message(self):
        """messages 只含 system/assistant → 反向遍历找不到 user → None。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "assistant", "content": "ans"},
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_empty_string_content(self):
        """user 消息 content="" → strip 后为空，跳过该条；继续反向找前一条 user。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "real question"},
                    {"role": "assistant", "content": "ans"},
                    {"role": "user", "content": ""},
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("real question")

    def test_all_empty_user_contents(self):
        """所有 user 消息 content 都为空 → None。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": ""},
                    {"role": "user", "content": "   "},
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_messages_not_list(self):
        """messages 字段非 list → None。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps({"messages": "not a list"}).encode("utf-8")
        assert _hash_last_user_message(body) is None

    def test_message_not_dict(self):
        """messages 元素非 dict → 反向遍历跳过，继续找前一条。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "real"},
                    "not a dict",
                    42,
                ]
            }
        ).encode("utf-8")
        assert _hash_last_user_message(body) == _expected_hash("real")

    def test_hash_is_16_chars(self):
        """哈希长度固定 16 字符（sha256 前 16）。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {"messages": [{"role": "user", "content": "hello"}]}
        ).encode("utf-8")
        h = _hash_last_user_message(body)
        assert h is not None
        assert len(h) == 16

    def test_consistency_across_calls(self):
        """相同消息多次调用应得相同哈希（确定性）。"""
        from app.langfuse_client import _hash_last_user_message

        body = json.dumps(
            {"messages": [{"role": "user", "content": "same content"}]}
        ).encode("utf-8")
        h1 = _hash_last_user_message(body)
        h2 = _hash_last_user_message(body)
        assert h1 == h2


class TestTraceChatMetadataPropagation:
    """trace_chat / trace_run_start 透传 last_user_message_hash + gateway_request_time 到 trace.metadata。"""

    def test_trace_chat_writes_hash_and_time_to_metadata(self, monkeypatch):
        """trace_chat 应把 last_user_message_hash + gateway_request_time 写入 trace.metadata。"""
        from app.langfuse_client import trace_chat
        from unittest.mock import MagicMock, patch

        fake_lf = MagicMock()
        fake_trace = MagicMock()
        fake_lf.trace.return_value = fake_trace
        with patch("app.langfuse_client.get_langfuse", return_value=fake_lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{"messages":[{"role":"user","content":"hi"}]}',
                session_id="sess-1",
                last_user_message_hash="abc123def456abc1",
                gateway_request_time=1752700000.5,
            )
        _, kwargs = fake_lf.trace.call_args
        metadata = kwargs["metadata"]
        assert metadata["last_user_message_hash"] == "abc123def456abc1"
        assert metadata["gateway_request_time"] == 1752700000.5
        assert kwargs["session_id"] == "sess-1"

    def test_trace_chat_omits_hash_when_none(self, monkeypatch):
        """未传 last_user_message_hash → metadata 不含该字段（不写 None 兜底）。"""
        from app.langfuse_client import trace_chat
        from unittest.mock import MagicMock, patch

        fake_lf = MagicMock()
        fake_trace = MagicMock()
        fake_lf.trace.return_value = fake_trace
        with patch("app.langfuse_client.get_langfuse", return_value=fake_lf):
            trace_chat(
                agent_id="agent-1",
                engine_type="HERMES",
                path="/v1/chat/completions",
                method="POST",
                model="gpt-4",
                input_body=b'{"messages":[]}',
                session_id="sess-1",
            )
        _, kwargs = fake_lf.trace.call_args
        metadata = kwargs["metadata"]
        assert "last_user_message_hash" not in metadata
        assert "gateway_request_time" not in metadata

    def test_trace_run_start_writes_hash_and_time_to_metadata(self, monkeypatch):
        from app.langfuse_client import trace_run_start
        from unittest.mock import MagicMock, patch

        fake_lf = MagicMock()
        fake_trace = MagicMock()
        fake_lf.trace.return_value = fake_trace
        with patch("app.langfuse_client.get_langfuse", return_value=fake_lf):
            trace_run_start(
                agent_id="agent-1",
                engine_type="DIFY",
                path="/v1/runs",
                input_body=b'{"messages":[{"role":"user","content":"hi"}]}',
                session_id="sess-1",
                last_user_message_hash="hash123def456abc",
                gateway_request_time=1752700000.5,
            )
        _, kwargs = fake_lf.trace.call_args
        metadata = kwargs["metadata"]
        assert metadata["last_user_message_hash"] == "hash123def456abc"
        assert metadata["gateway_request_time"] == 1752700000.5
