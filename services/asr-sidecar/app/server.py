"""ASR sidecar HTTP 服务。

POST /transcribe (body=音频字节, ?format=amr) → {"text": "..."}
GET  /health → {"status": "ok"}

faster-whisper 后端，直收 amr。transcribe 同步，用 asyncio.to_thread 不阻塞事件循环。
"""

import asyncio
import logging
import os

from aiohttp import web

from .asr import ASR

log = logging.getLogger("asr_sidecar")

_asr: ASR = None


async def handle_transcribe(request: web.Request) -> web.Response:
    audio = await request.read()
    if not audio:
        return web.json_response({"error": "empty audio"}, status=400)
    fmt = request.query.get("format", "amr")
    try:
        # faster-whisper transcribe 是同步阻塞 → 放线程池
        text = await asyncio.to_thread(_asr.transcribe, audio, fmt)
        return web.json_response({"text": text})
    except Exception as e:
        log.error("transcribe error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/transcribe", handle_transcribe)
    app.router.add_get("/health", handle_health)
    return app


def main():
    global _asr
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    _asr = ASR()
    _asr.load()  # 启动加载模型（阻塞，几十秒）
    port = int(os.environ.get("PORT", "9100"))
    log.info("ASR sidecar listening on :%d", port)
    web.run_app(make_app(), port=port)


if __name__ == "__main__":
    main()
