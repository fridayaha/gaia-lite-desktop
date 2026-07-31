"""Attachment hint synthesis — the single owner of the ``[Attached files: path]`` convention.

引擎 Hermes 是黑盒（OpenAI 兼容 API），只认 ``messages[].content`` 文本，必须靠
``[Attached files: path]`` 文本提示才知道有附件。本模块是该提示格式的唯一 owner：
前端 / IM dispatcher 只产出结构化 ``attachments``，由本模块在转发引擎前统一合成
进 content，避免格式串散落多处。

合成点：
- proxy 转发 /v1/chat/completions、/v1/runs 前（web 门户路径）
- dispatcher 转发到引擎前（IM 路径，直连引擎不经 proxy）
"""
from __future__ import annotations

from typing import Any

# 附件结构里承载路径的字段名（前端 Attachment / dispatcher 产出统一用 path）
_PATH_KEYS = ("path", "filePath", "url")


def _extract_path(attachment: Any) -> str | None:
    """从结构化附件里取路径：优先 path，其次 filePath/url；字符串直接返回。"""
    if isinstance(attachment, str):
        return attachment
    if isinstance(attachment, dict):
        for key in _PATH_KEYS:
            v = attachment.get(key)
            if isinstance(v, str) and v:
                return v
    return None


def format_attachment_hint(attachments: list[Any]) -> str:
    """把结构化附件列表合成 ``[Attached files: p1, p2]`` 文本。

    无可用路径返回空串。保持与历史约定一致：路径逗号+空格分隔，整体方括号包裹。
    """
    paths = [p for p in (_extract_path(a) for a in attachments) if p]
    if not paths:
        return ""
    return f"[Attached files: {', '.join(paths)}]"


def synthesize_content(content: Any, attachments: list[Any]) -> Any:
    """把附件 hint 合进 content：非空 content 追加 ``\\n\\n<hint>``，空 content 用 fallback。

    与前端历史 buildEngineContent 行为一致。无可用路径时原样返回 content。
    """
    hint = format_attachment_hint(attachments)
    if not hint:
        return content
    if isinstance(content, str) and content.strip():
        return f"{content}\n\n{hint}"
    paths = [p for p in (_extract_path(a) for a in attachments) if p]
    return f"I've uploaded {len(paths)} file(s): {', '.join(paths)}"


def inject_attachment_hints(messages: list[dict]) -> list[dict]:
    """对每条带 ``attachments`` 的 message，把 hint 合进 content 并剥离 attachments 字段。

    返回新列表（不就地修改入参，避免污染 proxy/dispatcher 持有的原始结构）。
    content 非空时追加 ``\\n\\n<hint>``；为空时用 fallback 文案（与前端历史行为一致）。
    无 attachments 或路径全空的 message 原样返回（但仍剥离空 attachments 字段以保持干净）。
    """
    out: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict) or "attachments" not in msg:
            out.append(msg)
            continue
        attachments = msg.get("attachments") or []
        new_msg = {k: v for k, v in msg.items() if k != "attachments"}
        hint = format_attachment_hint(attachments)
        if hint:
            new_msg["content"] = synthesize_content(new_msg.get("content", ""), attachments)
        out.append(new_msg)
    return out
