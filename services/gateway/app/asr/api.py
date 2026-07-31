"""终端用户语音转写 endpoint。

POST /v1/audio/transcriptions?format=m4a (body=音频字节) → {"text": "..."}

设计要点：
- JWT 鉴权（与 /v1/sessions 等终端路由同一套 verify_token，不需要 X-Agent-ID——
  语音转写不是引擎流量，不走 adapter 管线）
- 背后是付费云资源（火山按量计费）或自托管 CPU（whisper sidecar），公网暴露必须防刷：
  单请求 ≤10MB（AAC 32kbps 约 40min 音频，按住说话实际 <1min）+ per-user 滑动窗口限流
- provider 由 UA_ASR_PROVIDER 决定（volcengine / local whisper sidecar），endpoint 无感
"""
import logging
import time
from collections import deque

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from app.asr import AsrError, get_asr_provider
from app.proxy import security, verify_token

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10MB
_RATE_WINDOW_S = 60.0
_RATE_MAX_CALLS = 20  # 每用户每分钟 20 次（按住说话是低频交互）

# 允许的回传格式（仅作临时文件扩展名/提示用，白名单防注入）
_ALLOWED_FMTS = {"m4a", "amr", "wav", "mp3", "ogg", "aac"}

# per-user 滑动窗口（in-memory；gateway 单副本够用，多副本时需换 Redis）
_calls: dict[str, deque[float]] = {}


def _check_rate_limit(user_id: str) -> bool:
    """滑动窗口限流：窗口内调用数 < _RATE_MAX_CALLS 放行并记录，否则拒绝。"""
    now = time.monotonic()
    q = _calls.setdefault(user_id, deque())
    while q and now - q[0] > _RATE_WINDOW_S:
        q.popleft()
    if len(q) >= _RATE_MAX_CALLS:
        return False
    q.append(now)
    return True


def _reset_rate_limit() -> None:
    """测试用：清空限流窗口。"""
    _calls.clear()


@router.post("/v1/audio/transcriptions")
async def transcribe_audio(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    payload = verify_token(credentials)
    user_id = str(payload.get("sub") or "anonymous")

    if not _check_rate_limit(user_id):
        return JSONResponse({"error": "请求过于频繁，请稍后再试"}, status_code=429)

    audio = await request.body()
    if not audio:
        return JSONResponse({"error": "empty audio"}, status_code=400)
    if len(audio) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "音频过大"}, status_code=413)

    fmt = request.query_params.get("format", "m4a").lower().strip()
    if fmt not in _ALLOWED_FMTS:
        fmt = "m4a"

    provider = get_asr_provider()
    if provider is None:
        return JSONResponse({"error": "语音服务未配置"}, status_code=503)

    try:
        text = await provider.transcribe(audio, fmt=fmt)
    except AsrError as e:
        logger.warning("ASR transcribe failed (user=%s, fmt=%s): %s", user_id, fmt, e)
        return JSONResponse({"error": "语音识别失败，请重试"}, status_code=502)

    return {"text": text}
