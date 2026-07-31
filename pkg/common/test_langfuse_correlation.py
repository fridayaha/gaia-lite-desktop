"""pkg.common.langfuse_correlation 共享哈希工具单元测试。

验证 Gateway / Manager 两端共用的软关联算法：
  - ``hash_text_16(text)`` — sha256 前 16 字符
  - ``hash_last_user_message(obj)`` — 从 OpenAI 请求体提取最后一条 user 消息并哈希

接受 bytes / dict / list 多种输入，两端用同一份算法保证哈希一致。
"""

import hashlib
import json

import pytest

from pkg.common.langfuse_correlation import hash_last_user_message, hash_text_16


def _expected_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


class TestHashText16:
    """hash_text_16 基本算法。"""

    def test_simple_text(self):
        assert hash_text_16("hello") == _expected_hash("hello")

    def test_strips_whitespace(self):
        assert hash_text_16("  hello  ") == hash_text_16("hello")

    def test_consistent_across_calls(self):
        assert hash_text_16("same") == hash_text_16("same")


class TestHashLastUserMessage_Bytes:
    """bytes 输入（Gateway 收到的原始 body）。"""

    def test_simple_body(self):
        body = json.dumps(
            {"messages": [{"role": "user", "content": "hello"}]}
        ).encode("utf-8")
        assert hash_last_user_message(body) == _expected_hash("hello")

    def test_invalid_json_bytes(self):
        assert hash_last_user_message(b"not json") is None

    def test_empty_bytes(self):
        assert hash_last_user_message(b"") is None

    def test_none(self):
        assert hash_last_user_message(None) is None


class TestHashLastUserMessage_Dict:
    """dict 输入（Manager 从 Langfuse trace.input 拿到的对象）。"""

    def test_dict_with_messages_key(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        assert hash_last_user_message(body) == _expected_hash("hello")

    def test_dict_without_messages_key(self):
        assert hash_last_user_message({"model": "gpt-4"}) is None

    def test_dict_with_empty_messages_list(self):
        assert hash_last_user_message({"messages": []}) is None

    def test_dict_multiple_user_messages_picks_last(self):
        body = {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "ans"},
                {"role": "user", "content": "second"},
            ]
        }
        assert hash_last_user_message(body) == _expected_hash("second")

    def test_dict_multimodal_content(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what's "},
                        {"type": "text", "text": "this?"},
                    ],
                }
            ]
        }
        assert hash_last_user_message(body) == _expected_hash("what's this?")

    def test_dict_single_user_message_no_messages_key(self):
        """Hermes langfuse 插件写的 trace.input 是单条 message dict，
        形如 {"role":"user","content":"..."}（无 messages 字段）。
        模拟 Hermes 真实写入 + Gateway 写入应产生相同 hash。
        """
        single_msg = {"role": "user", "content": "hello hermes"}
        assert hash_last_user_message(single_msg) == _expected_hash("hello hermes")

    def test_dict_single_user_message_matches_request_body(self):
        """端到端一致性：Hermes 单条 message dict 与 Gateway 请求体 dict
        来自同一份请求时，hash 必须一致。
        """
        user_text = "帮我算下 88 * 12"
        gateway_body = {"messages": [{"role": "user", "content": user_text}]}
        hermes_input = {"role": "user", "content": user_text}
        assert hash_last_user_message(gateway_body) == hash_last_user_message(hermes_input)

    def test_dict_single_non_user_message_returns_none(self):
        """单条 message dict 但 role != user → None。"""
        assert hash_last_user_message({"role": "assistant", "content": "hi"}) is None
        assert hash_last_user_message({"role": "system", "content": "sys"}) is None


class TestHashLastUserMessage_List:
    """list 输入（直接传 messages 数组）。"""

    def test_list_input(self):
        msgs = [{"role": "user", "content": "hello"}]
        assert hash_last_user_message(msgs) == _expected_hash("hello")

    def test_empty_list(self):
        assert hash_last_user_message([]) is None

    def test_list_with_only_system(self):
        msgs = [{"role": "system", "content": "sys"}]
        assert hash_last_user_message(msgs) is None

    def test_list_with_multiple_roles_picks_last_user(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "ans"},
            {"role": "user", "content": "second"},
        ]
        assert hash_last_user_message(msgs) == _expected_hash("second")


class TestHashLastUserMessage_EdgeCases:
    """边界 + 错误分支。"""

    def test_user_content_is_empty_string(self):
        """user 消息 content="" → 跳过该条继续反向找。"""
        body = {
            "messages": [
                {"role": "user", "content": "real"},
                {"role": "user", "content": ""},
            ]
        }
        assert hash_last_user_message(body) == _expected_hash("real")

    def test_user_content_is_whitespace(self):
        body = {
            "messages": [
                {"role": "user", "content": "real"},
                {"role": "user", "content": "  "},
            ]
        }
        assert hash_last_user_message(body) == _expected_hash("real")

    def test_all_user_contents_empty(self):
        body = {
            "messages": [
                {"role": "user", "content": ""},
                {"role": "user", "content": "  "},
            ]
        }
        assert hash_last_user_message(body) is None

    def test_message_not_dict(self):
        """messages 元素非 dict → 跳过继续找。"""
        body = {
            "messages": [
                {"role": "user", "content": "real"},
                "not a dict",
                42,
            ]
        }
        assert hash_last_user_message(body) == _expected_hash("real")

    def test_hash_is_16_chars(self):
        body = {"messages": [{"role": "user", "content": "hello"}]}
        h = hash_last_user_message(body)
        assert h is not None
        assert len(h) == 16

    def test_hash_deterministic(self):
        """相同输入多次调用应得相同哈希。"""
        body = {"messages": [{"role": "user", "content": "same"}]}
        assert hash_last_user_message(body) == hash_last_user_message(body)

    def test_bytes_and_dict_produce_same_hash(self):
        """bytes 和 dict 形式的同一份 body 应得相同哈希（两端关联关键性质）。"""
        body_dict = {"messages": [{"role": "user", "content": "hello"}]}
        body_bytes = json.dumps(body_dict).encode("utf-8")
        assert hash_last_user_message(body_dict) == hash_last_user_message(body_bytes)

    def test_list_and_dict_produce_same_hash(self):
        """list 和 dict-with-messages 形式应得相同哈希。"""
        msgs = [{"role": "user", "content": "hello"}]
        body_dict = {"messages": msgs}
        assert hash_last_user_message(msgs) == hash_last_user_message(body_dict)
