"""ASR provider 抽象基类。

新增云厂商：实现 `AsrProvider.transcribe()`，在 `registry._PROVIDERS` 注册一行。
不改 wecom.py / base.py。详见 services/gateway/docs/wecom(callback)支持语音方案设计.md §三。
"""
from abc import ABC, abstractmethod


class AsrProvider(ABC):
    """ASR 供应商抽象。输入音频字节 + 格式，返回识别文本。

    `__init__` 读 settings 该厂商凭据，缺失抛 AsrError（registry 捕获返回 None）。
    `transcribe` 失败抛 AsrError，由 wecom.transcribe 兜底。
    """

    name: str = ""

    @abstractmethod
    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        """音频字节 → 文字。失败抛 AsrError。"""
        ...
