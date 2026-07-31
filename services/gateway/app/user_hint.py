"""Current-user identity hint injection — ephemeral system prompt for user awareness.

与 ``time_hint`` 同一套 ephemeral system 机制：gateway 转发前往 messages 的 system /
instructions 追加一条「当前用户身份」，hermes 把它叠加在 core system prompt 之上
（不覆盖、不持久化，core prefix cache 不受影响），让智能体每轮都知道当前用户是谁。

与 time_hint 的差异：
- 数据来源：时间 gateway 本地 ``datetime.now()``；用户身份须按 user_id 从 manager
  ``user-context`` 端点拉（``profile_resolver.resolve`` 内查 + 60s 缓存）。
- PII 边界：时间无 PII 全量注；用户身份**只注非个人 PII**（角色/用户组/业务用户名），
  避免进 langfuse trace。手机号/邮箱等强 PII 由 ``current-user-info`` pull skill 按需查
  （不进 trace）。

注入点与 time_hint 并列：proxy（web）+ dispatcher _forward_message/_stream_from_engine（IM）。
"""
from __future__ import annotations

import json
from typing import Any


def format_user_context_hint(user_context: dict | None) -> str:
    """把 manager user-context（``{fields, business}``）格式化为非 PII 身份提示。

    只取业务身份字段：角色、用户组、业务用户名。**不取** 用户名/真实姓名/邮箱/手机号
    （留 pull skill，避免进 trace）。全无 → 空串（不注入）。

    格式：``当前用户身份：销售（门店A组），业务用户名 LiuWei。``
    """
    if not isinstance(user_context, dict):
        return ""
    fields = user_context.get("fields") or {}
    business = user_context.get("business") or {}
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(business, dict):
        business = {}

    role = fields.get("角色")
    group = fields.get("用户组")
    parts: list[str] = []
    if role and group:
        parts.append(f"{role}（{group}）")
    elif role:
        parts.append(str(role))
    elif group:
        parts.append(str(group))

    biz = business.get("业务用户名")
    if biz:
        parts.append(f"业务用户名 {biz}")

    if not parts:
        return ""
    return f"当前用户身份：{'，'.join(parts)}。"


def _append_hint(existing: Any, hint: str) -> str:
    """追加不覆盖：existing 为非空字符串则 ``\\n\\n`` 拼接，否则用 hint。"""
    if isinstance(existing, str) and existing.strip():
        return f"{existing}\n\n{hint}"
    return hint


def inject_user_context_hint_into_body(data: dict, path: str, user_context: dict | None) -> bool:
    """就地注入用户身份提示到已解析 body dict，返回是否有改动。

    - v1/chat/completions：messages[] 已有 system → 追加到其 content；无则末尾插新 system。
    - v1/runs：instructions 已存在 → 追加；无则新建。
    - 其它 path / 非 list messages / 无身份内容 → 不改（返回 False）。
    """
    norm = path.strip("/")
    if norm not in ("v1/chat/completions", "v1/runs"):
        return False
    hint = format_user_context_hint(user_context)
    if not hint:
        return False
    if norm == "v1/chat/completions":
        messages = data.get("messages")
        if not isinstance(messages, list):
            return False
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "system":
                m["content"] = _append_hint(m.get("content"), hint)
                return True
        messages.append({"role": "system", "content": hint})
        return True
    # v1/runs：instructions 是字符串字段，追加不覆盖
    data["instructions"] = _append_hint(data.get("instructions"), hint)
    return True


def inject_user_context_hint(path: str, body: bytes, user_context: dict | None) -> bytes:
    """bytes 版（镜像 ``time_hint.inject_current_time_hint`` 骨架）。

    非 JSON / 非 dict / 非目标 path / 空 body / 无身份内容 → 原样返回。
    """
    if not body:
        return body
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return body
    if not isinstance(data, dict):
        return body
    if inject_user_context_hint_into_body(data, path, user_context):
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    return body
