"""火山引擎 OpenSpeech 豆包 ASR provider（volc.seedasr.auc 录音文件大模型）。

异步 submit + query 接口，X-Api-Key 单字段鉴权（BytePlus 国际版方案，不需
App-Key），音频 base64 放 audio.data。openspeech 只支持 wav/mp3/ogg/pcm，
企微 voice 是 amr，需先转 wav（用 av 库，asr-sidecar 同款）。

端点 `openspeech.bytedance.com/api/v3/auc/bigmodel/{submit,query}`。

接口已 curl 实测验证（2026-07）：submit→query 异步，X-Api-Key 鉴权，
config 去掉（传 object 报错），返回 result.text。
"""
import asyncio
import base64
import io
import time
import uuid

import httpx

from app.asr.base import AsrProvider
from app.asr.errors import AsrError
from app.settings import settings

_DEFAULT_HOST = "https://openspeech.bytedance.com"
_DEFAULT_RESOURCE_ID = "volc.seedasr.auc"
# openspeech 支持的音频格式；amr 不在内，需转 wav
_SUPPORTED_FMTS = {"wav", "mp3", "ogg", "pcm"}


class VolcengineAsrProvider(AsrProvider):
    name = "volcengine"

    def __init__(self):
        self.api_key = settings.asr_volc_api_key
        self.resource_id = settings.asr_volc_resource_id or _DEFAULT_RESOURCE_ID
        self.host = (settings.asr_volc_endpoint or _DEFAULT_HOST).rstrip("/")
        self.timeout = settings.asr_timeout
        self.poll_interval = 1.0
        self.poll_max = 60.0
        if not self.api_key:
            raise AsrError("volcengine ASR missing UA_ASR_VOLC_API_KEY")

    @staticmethod
    def _convert_to_wav(audio: bytes, fmt: str) -> tuple[bytes, str]:
        """非 wav/mp3/ogg/pcm 格式（如 amr）转 wav（16k mono s16le）。"""
        if fmt in _SUPPORTED_FMTS:
            return audio, fmt
        try:
            import av

            in_buf = io.BytesIO(audio)
            out_buf = io.BytesIO()
            input_container = av.open(in_buf)
            output_container = av.open(out_buf, mode="w", format="wav")
            ostream = output_container.add_stream("pcm_s16le", rate=16000)
            ostream.layout = "mono"
            for frame in input_container.decode(audio=0):
                frame.pts = None
                for packet in ostream.encode(frame):
                    output_container.mux(packet)
            for packet in ostream.encode():
                output_container.mux(packet)
            output_container.close()
            input_container.close()
            return out_buf.getvalue(), "wav"
        except Exception as e:
            raise AsrError(f"volcengine ASR audio convert ({fmt}->wav) failed: {e}") from e

    def _headers(self, request_id: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": request_id,
        }

    async def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        wav_bytes, wav_fmt = self._convert_to_wav(audio, fmt)
        audio_b64 = base64.b64encode(wav_bytes).decode()
        request_id = uuid.uuid4().hex
        headers = self._headers(request_id)
        submit_payload = {
            "user": {"uid": "unionagents"},
            "audio": {"data": audio_b64, "format": wav_fmt},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.host}/api/v3/auc/bigmodel/submit",
                    json=submit_payload,
                    headers=headers,
                )
                if resp.status_code != 200:
                    raise AsrError(
                        f"volcengine ASR submit HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                # 轮询 query 直到出 result 或超时
                deadline = time.time() + self.poll_max
                while time.time() < deadline:
                    await asyncio.sleep(self.poll_interval)
                    qresp = await client.post(
                        f"{self.host}/api/v3/auc/bigmodel/query",
                        json={},
                        headers=headers,
                    )
                    if qresp.status_code != 200:
                        raise AsrError(
                            f"volcengine ASR query HTTP {qresp.status_code}: {qresp.text[:200]}"
                        )
                    data = qresp.json()
                    # 出 result 即完成（实测成功 query 返回 {"audio_info":...,"result":{"text":...}}）
                    if isinstance(data, dict) and "result" in data:
                        return str(data.get("result", {}).get("text", "")).strip()
                    # 仍在处理或错误：header.code != 0 视为错误
                    header = data.get("header") if isinstance(data, dict) else None
                    if header and header.get("code", 0) != 0:
                        raise AsrError(f"volcengine ASR query error: {data}")
                raise AsrError("volcengine ASR query timeout")
        except httpx.HTTPError as e:
            raise AsrError(f"volcengine ASR request failed: {e}") from e
