"""华为云 ASR provider（待实现）。

落地时实现 transcribe（一句话识别 HTTP，AK/SK 签名），在 registry 注册。
当前 stub。
"""
from app.asr.base import AsrProvider
from app.asr.errors import AsrError


class HuaweiAsrProvider(AsrProvider):
    name = "huawei"

    def __init__(self):
        raise AsrError("huawei ASR provider not implemented yet")

    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        raise AsrError("huawei ASR provider not implemented yet")
