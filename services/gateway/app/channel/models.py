"""Unified message model for all IM platforms."""
from dataclasses import dataclass, field
from enum import Enum


class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    VOICE = "voice"


@dataclass
class MessageEvent:
    """Normalized incoming message — all platform adapters produce this."""
    text: str
    message_type: MessageType = MessageType.TEXT
    agent_id: str = ""
    channel_type: str = ""
    chat_id: str = ""
    user_id: str = ""
    user_name: str = ""
    platform_message_id: str = ""
    media_urls: list[str] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)
    # 结构化附件（IM 入站图片/文件写入工作区后产出）：[{path, name, is_image}]
    # 转发引擎前由 attachment_hint.inject_attachment_hints 合成 [Attached files: path]
    # 进 content，不在 event.text 里拼文本约定。
    attachments: list[dict] = field(default_factory=list)
