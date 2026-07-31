"""user_hint 纯函数测试。

注入逻辑（chat 插 system / runs 加 instructions、追加不覆盖、bytes 骨架）与
非 PII 边界（只取角色/用户组/业务用户名，PII 不进 hint）。
不依赖 settings 开关（直接调函数）。
"""
from __future__ import annotations

import json

from app.user_hint import (
    format_user_context_hint,
    inject_user_context_hint,
    inject_user_context_hint_into_body,
)

# ── format_user_context_hint ──────────────────────────────────────


def _full_context() -> dict:
    """模拟 serialize_user_context 输出（含 PII + 非 PII）。"""
    return {
        "fields": {
            "用户名": "alice",
            "真实姓名": "张三",
            "邮箱": "alice@example.com",
            "手机号": "13800000000",
            "角色": "销售",
            "用户组": "门店A组",
        },
        "business": {
            "业务用户名": "LiuWei",
            "业务手机号": "13900000000",
            "业务邮箱": "lw@corp.com",
        },
    }


def test_format_full_context():
    s = format_user_context_hint(_full_context())
    assert s.startswith("当前用户身份：")
    assert "销售（门店A组）" in s
    assert "业务用户名 LiuWei" in s


def test_format_excludes_pii():
    """只注非 PII 业务身份；用户名/姓名/邮箱/手机号绝不出现。"""
    s = format_user_context_hint(_full_context())
    assert "alice" not in s
    assert "张三" not in s
    assert "alice@example.com" not in s
    assert "13800000000" not in s
    assert "13900000000" not in s
    assert "lw@corp.com" not in s


def test_format_role_only():
    ctx = {"fields": {"角色": "运营"}, "business": {}}
    assert format_user_context_hint(ctx) == "当前用户身份：运营。"


def test_format_group_only():
    ctx = {"fields": {"用户组": "门店B组"}, "business": {}}
    assert format_user_context_hint(ctx) == "当前用户身份：门店B组。"


def test_format_biz_username_only():
    ctx = {"fields": {}, "business": {"业务用户名": "Tom"}}
    assert format_user_context_hint(ctx) == "当前用户身份：业务用户名 Tom。"


def test_format_role_and_biz_without_group():
    ctx = {"fields": {"角色": "销售"}, "business": {"业务用户名": "LiuWei"}}
    s = format_user_context_hint(ctx)
    assert "销售" in s and "门店" not in s
    assert "业务用户名 LiuWei" in s


def test_format_none_returns_empty():
    assert format_user_context_hint(None) == ""


def test_format_empty_context_returns_empty():
    assert format_user_context_hint({}) == ""
    assert format_user_context_hint({"fields": {}, "business": {}}) == ""


def test_format_no_identity_fields_returns_empty():
    """只有 PII 字段（无角色/组/业务用户名）→ 空串（不注入）。"""
    ctx = {"fields": {"用户名": "alice", "手机号": "138"}, "business": {}}
    assert format_user_context_hint(ctx) == ""


# ── inject_user_context_hint_into_body (dict 版) ──────────────────


def test_inject_chat_adds_system_when_none():
    data = {"messages": [{"role": "user", "content": "hi"}]}
    assert inject_user_context_hint_into_body(data, "/v1/chat/completions", _full_context()) is True
    msgs = data["messages"]
    assert msgs[0] == {"role": "user", "content": "hi"}  # 原消息不变
    assert msgs[-1]["role"] == "system"
    assert "销售" in msgs[-1]["content"]


def test_inject_chat_appends_to_existing_system():
    data = {
        "messages": [
            {"role": "system", "content": "You are X"},
            {"role": "user", "content": "hi"},
        ],
    }
    assert inject_user_context_hint_into_body(data, "/v1/chat/completions", _full_context()) is True
    assert len(data["messages"]) == 2  # 无新消息
    assert data["messages"][0]["content"].startswith("You are X")
    assert "\n\n" in data["messages"][0]["content"]
    assert "销售" in data["messages"][0]["content"]


def test_inject_runs_appends_to_instructions():
    data = {"instructions": "do task", "input": [{"role": "user", "content": "hi"}]}
    assert inject_user_context_hint_into_body(data, "/v1/runs", _full_context()) is True
    assert data["instructions"].startswith("do task")
    assert "\n\n" in data["instructions"]
    assert "销售" in data["instructions"]


def test_inject_runs_creates_instructions_when_absent():
    data = {"input": [{"role": "user", "content": "hi"}]}
    assert inject_user_context_hint_into_body(data, "/v1/runs", _full_context()) is True
    assert "销售" in data["instructions"]


def test_inject_non_target_path_returns_false():
    data = {"messages": []}
    assert inject_user_context_hint_into_body(data, "/v1/models", _full_context()) is False
    assert data == {"messages": []}


def test_inject_none_context_returns_false():
    data = {"messages": [{"role": "user", "content": "hi"}]}
    assert inject_user_context_hint_into_body(data, "/v1/chat/completions", None) is False
    assert data["messages"] == [{"role": "user", "content": "hi"}]


def test_inject_empty_identity_returns_false():
    """user_context 无身份字段 → 不注入（不插空 system）。"""
    data = {"messages": [{"role": "user", "content": "hi"}]}
    ctx = {"fields": {"用户名": "alice"}, "business": {}}
    assert inject_user_context_hint_into_body(data, "/v1/chat/completions", ctx) is False
    assert data["messages"] == [{"role": "user", "content": "hi"}]


def test_inject_chat_non_list_messages_returns_false():
    data = {"messages": "not a list"}
    assert inject_user_context_hint_into_body(data, "/v1/chat/completions", _full_context()) is False


# ── inject_user_context_hint (bytes 版) ───────────────────────────


def test_bytes_injects_chat_system():
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    out = inject_user_context_hint("/v1/chat/completions", body, _full_context())
    data = json.loads(out)
    assert data["messages"][-1]["role"] == "system"
    assert "销售" in data["messages"][-1]["content"]


def test_bytes_empty_body_unchanged():
    assert inject_user_context_hint("/v1/chat/completions", b"", _full_context()) == b""


def test_bytes_non_json_unchanged():
    body = b"not json"
    assert inject_user_context_hint("/v1/chat/completions", body, _full_context()) == body


def test_bytes_none_context_unchanged():
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
    assert inject_user_context_hint("/v1/chat/completions", body, None) == body
