"""time_hint 纯函数测试。

注入逻辑（chat 插 system / runs 加 instructions、追加不覆盖）与时区回落、bytes 骨架。
不依赖 settings 开关（直接调函数），conftest 的 autouse 关注入 fixture 不影响此处。
"""
from __future__ import annotations

import json
import re

from app.time_hint import (
    format_current_time,
    inject_current_time_hint,
    inject_current_time_hint_into_body,
)

# ── format_current_time ────────────────────────────────────────────


def test_format_current_time_shanghai():
    s = format_current_time("Asia/Shanghai")
    assert "Asia/Shanghai" in s
    assert "年" in s and "月" in s and "日" in s
    assert "星期" in s
    assert re.search(r"\d{2}:\d{2}", s)


def test_format_current_time_none_uses_default():
    assert "Asia/Shanghai" in format_current_time(None, default_timezone="Asia/Shanghai")


def test_format_current_time_invalid_tz_falls_back_to_default():
    # 无效时区 → 回落 default
    assert "Asia/Shanghai" in format_current_time("Invalid/Zone", default_timezone="Asia/Shanghai")


def test_format_current_time_invalid_default_falls_back_to_utc():
    # default 也无效 → UTC
    assert "UTC" in format_current_time(None, default_timezone="Bad/Zone")


def test_format_current_time_minute_precision():
    s = format_current_time("Asia/Shanghai")
    assert re.search(r"\d{2}:\d{2}（Asia/Shanghai）", s)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", s)  # 无秒


def test_format_current_time_has_guidance():
    assert "请以此为准" in format_current_time("Asia/Shanghai")


# ── inject_current_time_hint_into_body (dict 版) ───────────────────


def test_inject_chat_adds_system_when_none():
    data = {"messages": [{"role": "user", "content": "hi"}]}
    assert inject_current_time_hint_into_body(data, "/v1/chat/completions", "Asia/Shanghai") is True
    msgs = data["messages"]
    assert msgs[0] == {"role": "user", "content": "hi"}  # 原消息不变
    assert msgs[-1]["role"] == "system"
    assert "Asia/Shanghai" in msgs[-1]["content"]


def test_inject_chat_appends_to_existing_system():
    data = {
        "messages": [
            {"role": "system", "content": "You are X"},
            {"role": "user", "content": "hi"},
        ],
    }
    assert inject_current_time_hint_into_body(data, "/v1/chat/completions", "Asia/Shanghai") is True
    assert len(data["messages"]) == 2  # 无新消息
    assert data["messages"][0]["content"].startswith("You are X")
    assert "\n\n" in data["messages"][0]["content"]
    assert "Asia/Shanghai" in data["messages"][0]["content"]


def test_inject_chat_multiple_system_appends_to_first():
    data = {
        "messages": [
            {"role": "system", "content": "A"},
            {"role": "system", "content": "B"},
            {"role": "user", "content": "hi"},
        ]
    }
    inject_current_time_hint_into_body(data, "/v1/chat/completions", "Asia/Shanghai")
    assert data["messages"][0]["content"].startswith("A\n\n")
    assert "Asia/Shanghai" in data["messages"][0]["content"]
    assert data["messages"][1]["content"] == "B"  # 第二条 system 不变


def test_inject_chat_empty_system_content_replaced():
    data = {"messages": [{"role": "system", "content": ""}, {"role": "user", "content": "hi"}]}
    inject_current_time_hint_into_body(data, "/v1/chat/completions", "Asia/Shanghai")
    content = data["messages"][0]["content"]
    assert "Asia/Shanghai" in content
    assert "\n\n" not in content  # 空 content 直接用 hint，不产生空行


def test_inject_runs_sets_instructions_when_absent():
    data = {"input": "hi", "conversation_history": [], "session_id": "s1"}
    assert inject_current_time_hint_into_body(data, "/v1/runs", "Asia/Shanghai") is True
    assert "Asia/Shanghai" in data["instructions"]
    assert data["input"] == "hi"  # 不变
    assert data["conversation_history"] == []


def test_inject_runs_appends_to_existing_instructions():
    data = {"input": "hi", "instructions": "You are X"}
    inject_current_time_hint_into_body(data, "/v1/runs", "Asia/Shanghai")
    assert data["instructions"].startswith("You are X")
    assert "\n\n" in data["instructions"]
    assert "Asia/Shanghai" in data["instructions"]


def test_inject_non_chat_path_returns_false():
    data = {"messages": [{"role": "user", "content": "hi"}]}
    assert inject_current_time_hint_into_body(data, "/v1/models", "Asia/Shanghai") is False
    assert "instructions" not in data
    assert data["messages"] == [{"role": "user", "content": "hi"}]


def test_inject_chat_non_list_messages_returns_false():
    assert inject_current_time_hint_into_body(
        {"messages": "not a list"}, "/v1/chat/completions", "Asia/Shanghai"
    ) is False


# ── inject_current_time_hint (bytes 版) ─────────────────────────────


def test_inject_bytes_chat_adds_system():
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    data = json.loads(inject_current_time_hint("/v1/chat/completions", body, "Asia/Shanghai"))
    assert data["messages"][-1]["role"] == "system"
    assert data["messages"][0]["content"] == "hi"


def test_inject_bytes_runs_adds_instructions():
    body = json.dumps({"input": "hi"}).encode()
    data = json.loads(inject_current_time_hint("/v1/runs", body, "Asia/Shanghai"))
    assert "Asia/Shanghai" in data["instructions"]


def test_inject_bytes_preserves_existing_system():
    body = json.dumps(
        {"messages": [{"role": "system", "content": "X"}, {"role": "user", "content": "hi"}]}
    ).encode()
    data = json.loads(inject_current_time_hint("/v1/chat/completions", body, "Asia/Shanghai"))
    assert data["messages"][0]["content"].startswith("X")
    assert len(data["messages"]) == 2  # 追加不新增


def test_inject_bytes_empty_body_untouched():
    assert inject_current_time_hint("/v1/chat/completions", b"", "Asia/Shanghai") == b""


def test_inject_bytes_invalid_json_untouched():
    body = b"not json"
    assert inject_current_time_hint("/v1/chat/completions", body, "Asia/Shanghai") == body


def test_inject_bytes_non_dict_untouched():
    body = b"[1, 2, 3]"
    assert inject_current_time_hint("/v1/chat/completions", body, "Asia/Shanghai") == body


def test_inject_bytes_non_target_path_untouched():
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    assert inject_current_time_hint("/v1/models", body, "Asia/Shanghai") == body
