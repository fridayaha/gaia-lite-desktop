"""本地 whisper ASR provider（旧 asr-sidecar fallback）。

复用旧 asr-sidecar 的 HTTP `/transcribe` 接口（faster-whisper）。仅
`UA_ASR_PROVIDER=local` 时用，需先部署 with-asr-sidecar overlay 并 build asr-sidecar
镜像（见方案 §七.2 回退两步）。旧 asr-sidecar 代码零改动作 fallback。
"""
import httpx

from app.asr.base import AsrProvider
from app.asr.errors import AsrError
from app.settings import settings


class LocalWhisperAsrProvider(AsrProvider):
    name = "local"

    def __init__(self):
        self.asr_url = (settings.asr_url or "").rstrip("/")
        self.timeout = 60.0
        if not self.asr_url:
            raise AsrError("local whisper ASR missing UA_ASR_URL")

    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.asr_url}/transcribe",
                    content=audio,
                    headers={"Content-Type": "application/octet-stream"},
                    params={"format": fmt},
                )
        except httpx.HTTPError as e:
            raise AsrError(f"local whisper ASR request failed: {e}") from e
        if resp.status_code != 200:
            raise AsrError(f"local whisper ASR HTTP {resp.status_code}")
        return str(resp.json().get("text", "")).strip()
