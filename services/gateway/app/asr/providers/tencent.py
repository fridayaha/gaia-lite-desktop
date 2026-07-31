"""腾讯云 ASR provider（待实现）。

落地时实现 transcribe（一句话识别 HTTP，TC3-HMAC 签名），在 registry 注册。
当前 stub。
"""
from app.asr.base import AsrProvider
from app.asr.errors import AsrError


class TencentAsrProvider(AsrProvider):
    name = "tencent"

    def __init__(self):
        raise AsrError("tencent ASR provider not implemented yet")

    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        raise AsrError("tencent ASR provider not implemented yet")
