"""ASR provider 抽象层。

对外入口 `get_asr_provider()`：按 UA_ASR_PROVIDER 返回 provider 单例。
新增云厂商见 services/gateway/docs/wecom(callback)支持语音方案设计.md §三。
"""
from app.asr.base import AsrProvider
from app.asr.errors import AsrError
from app.asr.registry import get_asr_provider, reset_asr_provider

__all__ = ["get_asr_provider", "reset_asr_provider", "AsrProvider", "AsrError"]
