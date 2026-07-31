"""faster-whisper ASR 封装。

启动加载模型，transcribe(bytes, fmt) → 文字。直收 amr（faster-whisper 内置 av 解码）。
transcribe 是同步阻塞调用，server 侧用 asyncio.to_thread 包装。
"""

import logging
import os
import tempfile

log = logging.getLogger("asr_sidecar.asr")


class ASR:
    def __init__(self):
        self._model = None

    def load(self):
        """加载 whisper 模型（启动时调用，阻塞）。"""
        import glob
        from faster_whisper import WhisperModel

        model_size = os.environ.get("WHISPER_MODEL", "small")
        device = os.environ.get("WHISPER_DEVICE", "cpu")
        compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
        # cpu_threads：CTranslate2 默认用 os.cpu_count()（容器内=宿主核数，常见 8+），
        # 短语音（企微 voice）下多线程竞争/上下文切换开销 >> 并行收益，实测 2s 音频
        # 8线程=8.6s、4线程=9s、1线程=3.7s。默认 1；长音频场景可调高。
        cpu_threads = int(os.environ.get("WHISPER_CPU_THREADS", "1"))

        # 优先用本地缓存路径（跳过 download_model，避免连 HuggingFace 挂住）
        cache_pattern = (
            f"/root/.cache/huggingface/hub/"
            f"models--Systran--faster-whisper-{model_size}/snapshots/*"
        )
        cached_paths = glob.glob(cache_pattern)
        model_path = cached_paths[0] if cached_paths else model_size

        log.info("Loading whisper model=%s path=%s device=%s compute=%s cpu_threads=%s",
                 model_size, model_path, device, compute_type, cpu_threads)
        self._model = WhisperModel(
            model_path, device=device, compute_type=compute_type, cpu_threads=cpu_threads
        )
        log.info("Whisper model loaded")

    def transcribe(self, audio: bytes, fmt: str = "amr") -> str:
        """音频字节 → 文字。fmt 用于临时文件扩展名（av 据此 + 内容探测格式）。"""
        if not self._model:
            raise RuntimeError("model not loaded")
        # 强制语言（默认 zh）：faster-whisper 对短音频自动检测不稳，曾把 2s 中文
        # 误判为土耳其语。业务以中文为主，默认锁定 zh；可经 WHISPER_LANGUAGE 覆盖。
        language = os.environ.get("WHISPER_LANGUAGE", "zh")
        # 引导输出简体中文：whisper 中文默认输出繁体，用普通话 initial_prompt 把
        # 解码器导向简体（实测有效）。可经 WHISPER_INITIAL_PROMPT 覆盖，置空则不引导。
        initial_prompt = os.environ.get("WHISPER_INITIAL_PROMPT", "以下是普通话的句子。")
        # 写临时文件（带扩展名），让 faster-whisper/av 正确探测格式（amr 等）
        with tempfile.NamedTemporaryFile(suffix=f".{fmt}", delete=True) as f:
            f.write(audio)
            f.flush()
            kwargs = {"language": language}
            if initial_prompt:
                kwargs["initial_prompt"] = initial_prompt
            segments, _info = self._model.transcribe(f.name, **kwargs)
            text = "".join(seg.text for seg in segments).strip()
        return text
