"""ASR provider 注册表 + 工厂。

按 settings.asr_provider 返回单例 provider；未配置/未知/凭据缺失返回 None
（wecom.transcribe 收到 None 走兜底提示）。
"""
import logging

from app.asr.base import AsrProvider
from app.asr.errors import AsrError
from app.asr.providers.aliyun import AliyunAsrProvider
from app.asr.providers.huawei import HuaweiAsrProvider
from app.asr.providers.local_whisper import LocalWhisperAsrProvider
from app.asr.providers.tencent import TencentAsrProvider
from app.asr.providers.volcengine import VolcengineAsrProvider
from app.settings import settings

logger = logging.getLogger(__name__)

_PROVIDERS: dict[str, type[AsrProvider]] = {
    "volcengine": VolcengineAsrProvider,
    "local": LocalWhisperAsrProvider,
    "aliyun": AliyunAsrProvider,
    "tencent": TencentAsrProvider,
    "huawei": HuaweiAsrProvider,
}

_provider_singleton: AsrProvider | None = None


def get_asr_provider() -> AsrProvider | None:
    """按 settings.asr_provider 返回单例 provider。

    - 未配置 asr_provider → None（voice 走兜底提示）
    - 未知 provider 名 → 记 error 返回 None
    - provider __init__ 抛 AsrError（凭据缺失）→ 记 error 返回 None
    - 否则返回单例
    """
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton
    name = (settings.asr_provider or "").strip()
    if not name:
        return None
    cls = _PROVIDERS.get(name)
    if not cls:
        logger.error("Unknown ASR provider: %s", name)
        return None
    try:
        _provider_singleton = cls()
    except AsrError as e:
        logger.error("ASR provider %s init failed: %s", name, e)
        return None
    return _provider_singleton


def reset_asr_provider() -> None:
    """重置单例（测试用：改 settings 后重新初始化）。"""
    global _provider_singleton
    _provider_singleton = None
