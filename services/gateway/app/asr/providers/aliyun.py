"""阿里云 ASR provider（待实现）。

落地时实现 transcribe（一句话识别 HTTP，AccessKey 签名），在 registry 注册。
实现模式见方案 §十三。当前 stub：实例化即报错，避免误用。
"""
from app.asr.base import AsrProvider
from app.asr.errors import AsrError


class AliyunAsrProvider(AsrProvider):
    name = "aliyun"

    def __init__(self):
        raise AsrError("aliyun ASR provider not implemented yet")

    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        raise AsrError("aliyun ASR provider not implemented yet")
