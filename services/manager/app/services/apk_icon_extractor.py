"""APK 应用图标提取——aapt dump badging 解析 + zipfile 抽取。

调用方：app.api.app_releases.upload_base_apk 接收 APK 后自动提取图标，
存入公开桶 /avatars/...，icon_object_key 写入 AppRelease。

设计：
- aapt 在 manager pod 内（/opt/android-sdk/build-tools/35.0.0/aapt），subprocess 调用
- aapt 需要文件路径不接受 stdin，先把 bytes 写 NamedTemporaryFile
- subprocess 是同步阻塞，用 asyncio.to_thread 包裹
- 任何环节失败（aapt 不可用 / APK 无图标 / zipfile 读不到）返回 None，不阻断上传
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass

logger = logging.getLogger(__name__)

AAPT_BIN = "/opt/android-sdk/build-tools/35.0.0/aapt"
_AAPT_TIMEOUT = 15

# application-icon-<density>:'<path>'，density 越大越清晰
_ICON_LINE_RE = re.compile(r"^application-icon-(\d+):\s*'([^']+)'", re.MULTILINE)
# 某些 aapt 版本只输出 application-label/icon 单行
_APPLICATION_ICON_RE = re.compile(r"^application:.*\bicon='([^']+)'", re.MULTILINE)

_EXT_TO_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


@dataclass
class ExtractedIcon:
    content: bytes
    content_type: str


async def extract_icon(apk_bytes: bytes, aapt_bin: str = AAPT_BIN) -> ExtractedIcon | None:
    """从 APK bytes 提取应用图标。

    返回 None 的场景（不抛异常，不阻断上传）：
    - aapt 不存在 / 执行失败
    - APK 内无 application-icon（极少数无图标 APK）
    - 找到路径但 zipfile 内读不到（路径在 resources.arsc 引用但 APK 缺文件）
    """
    try:
        badging = await _run_aapt_badging(apk_bytes, aapt_bin)
    except FileNotFoundError as e:
        logger.warning("aapt binary not found at %s: %s", aapt_bin, e)
        return None
    except Exception as e:
        logger.warning("aapt dump badging failed: %s", e)
        return None

    icon_path = _pick_best_icon_path(badging)
    if not icon_path:
        logger.info("APK has no application-icon in badging output")
        return None

    try:
        content = await asyncio.to_thread(_read_zip_entry, apk_bytes, icon_path)
    except KeyError:
        logger.warning("icon entry %s referenced but not in APK", icon_path)
        return None
    except Exception as e:
        logger.warning("read icon entry %s failed: %s", icon_path, e)
        return None

    if not content:
        return None

    return ExtractedIcon(
        content=content,
        content_type=_guess_content_type(icon_path),
    )


async def _run_aapt_badging(apk_bytes: bytes, aapt_bin: str) -> str:
    """写临时文件 + aapt dump badging，返回 stdout。

    Raises:
        FileNotFoundError: aapt 二进制不存在
        RuntimeError: aapt 退出码非 0
    """

    def _run() -> str:
        fd, tmp_path = tempfile.mkstemp(suffix=".apk")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(apk_bytes)
            proc = subprocess.run(
                [aapt_bin, "dump", "badging", tmp_path],
                capture_output=True,
                text=True,
                timeout=_AAPT_TIMEOUT,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"aapt exit {proc.returncode}: {proc.stderr[:200] if proc.stderr else ''}"
                )
            return proc.stdout
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    return await asyncio.to_thread(_run)


def _pick_best_icon_path(badging: str) -> str | None:
    """从 aapt badging 输出挑最高 density 的图标路径。

    优先匹配 application-icon-<density>:'<path>' 行（多 density 时取最大）；
    退化匹配 application: ... icon='<path>' 单行（某些 aapt 版本/某些 APK）。
    """
    matches = _ICON_LINE_RE.findall(badging)
    if matches:
        matches.sort(key=lambda x: int(x[0]), reverse=True)
        return matches[0][1]

    m = _APPLICATION_ICON_RE.search(badging)
    return m.group(1) if m else None


def _read_zip_entry(apk_bytes: bytes, entry_path: str) -> bytes:
    """从 APK bytes 读出指定 entry 全文。"""
    with zipfile.ZipFile(io.BytesIO(apk_bytes)) as zf:
        return zf.read(entry_path)


def _guess_content_type(path: str) -> str:
    """按文件扩展名推断 content-type，默认 png（Android 主流）。"""
    lower = path.lower()
    for ext, ct in _EXT_TO_CONTENT_TYPE.items():
        if lower.endswith(ext):
            return ct
    return "image/png"
