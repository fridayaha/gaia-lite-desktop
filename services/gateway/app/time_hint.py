"""Current-time hint injection — ephemeral system prompt for date/time freshness.

hermes 引擎的系统提示词里 ``Conversation started: <日期>`` 在会话首次构建时固化
（为保 prefix cache，session 内不重建），跨天续会时模型会报创建当天的日期。本模块
在 gateway 转发引擎前，每轮注入一条「当前时间」作为 ephemeral system prompt —— hermes
会把它叠加在缓存的 core system prompt 之上（不覆盖、不持久化），模型即可每轮看到
最新时间，core 的 prefix cache 不受影响。

注入点（与 attachment_hint 同阶段；dispatcher 绕过 proxy 故两处分别接）：
- proxy 转发 /v1/chat/completions、/v1/runs 前（web 门户路径）
- dispatcher 转发到引擎前（IM 路径，直连引擎不经 proxy）

两条路径字段不同：
- /v1/chat/completions：往 messages[] 末尾插一条 system（hermes 合并所有 system 为 ephemeral）
- /v1/runs：往请求体加 instructions 字段（hermes 取 instructions 作 ephemeral）
两条都追加不覆盖。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
_GUIDANCE = "。如被问及日期或时间，请以此为准。"


def _resolve_zone(timezone: str | None, default_timezone: str) -> ZoneInfo:
    """按 timezone → default_timezone → UTC 顺序解析，任一无效逐级回落，绝不抛异常。"""
    for name in (timezone, default_timezone, "UTC"):
        if isinstance(name, str) and name:
            try:
                return ZoneInfo(name)
            except Exception:  # 时区解析绝不能阻断转发
                continue
    return ZoneInfo("UTC")


def format_current_time(
    timezone: str | None = None, default_timezone: str = "Asia/Shanghai"
) -> str:
    """按指定时区格式化当前时间（分钟精度），无效时区逐级回落到 default → UTC。

    格式：``2026年7月29日 星期三 14:30（Asia/Shanghai）`` + 引导语。
    """
    zone = _resolve_zone(timezone, default_timezone)
    now = datetime.now(zone)
    weekday = _WEEKDAYS[now.weekday()]
    return (
        f"{now.year}年{now.month}月{now.day}日 {weekday} "
        f"{now.hour:02d}:{now.minute:02d}（{zone.key}）{_GUIDANCE}"
    )


def _append_hint(existing: Any, hint: str) -> str:
    """追加不覆盖：existing 为非空字符串则 ``\\n\\n`` 拼接，否则用 hint。"""
    if isinstance(existing, str) and existing.strip():
        return f"{existing}\n\n{hint}"
    return hint


def inject_current_time_hint_into_body(
    data: dict,
    path: str,
    timezone: str | None = None,
    default_timezone: str = "Asia/Shanghai",
) -> bool:
    """就地注入当前时间提示到已解析 body dict，返回是否有改动。

    - v1/chat/completions：messages[] 已有 system → 追加到其 content；无则末尾插新 system。
    - v1/runs：instructions 已存在 → 追加；无则新建。
    - 其它 path / 非 list messages → 不改（返回 False）。
    """
    norm = path.strip("/")
    if norm not in ("v1/chat/completions", "v1/runs"):
        return False
    hint = format_current_time(timezone, default_timezone)
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


def inject_current_time_hint(
    path: str,
    body: bytes,
    timezone: str | None = None,
    default_timezone: str = "Asia/Shanghai",
) -> bytes:
    """bytes 版（镜像 ``_inject_attachments_if_chat`` 骨架）。

    非 JSON / 非 dict / 非目标 path / 空 body 原样返回，避免无谓改变字节格式。
    """
    if not body:
        return body
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return body
    if not isinstance(data, dict):
        return body
    if inject_current_time_hint_into_body(data, path, timezone, default_timezone):
        return json.dumps(data, ensure_ascii=False).encode("utf-8")
    return body
