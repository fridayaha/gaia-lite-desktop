"""APP 发布物存储——base/patched APK 入私有桶，icon 入公开桶。

复用 minio_archiver 单例 client（与 avatar 上传同源），保持现有访问 pattern。
- 私有桶 `unionagents-archives`：base APK + patched APK，经 manager 端点流式吐（不直接对外）
- 公开桶 `unionagents-avatars`：icon，nginx 反代 `/avatars/` 直接访问

URL 约定：
- base/patched APK：仅存 object_key，下载时 manager 调 `archiver.client.get_object` 流式
- icon：存相对路径 `/avatars/{public_bucket}/{object_key}`，前端直接访问
"""
from __future__ import annotations

import io
import logging
from uuid import UUID, uuid4

from app.worker.minio_archiver import archiver
from app.services.minio_public import ensure_public_bucket
from pkg.common.config import settings

logger = logging.getLogger(__name__)

_APK_CONTENT_TYPE = "application/vnd.android.package-archive"
_BASE_PREFIX = "app-releases/base"
_PATCHED_PREFIX = "app-releases/patched"
_ICON_PREFIX = "app-icons"

_MAX_ICON_SIZE = 2 * 1024 * 1024  # 2MB
_ALLOWED_ICON_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


async def put_base_apk(release_id: UUID, apk_bytes: bytes, ext: str = "apk") -> str:
    """上传 base 安装包到私有桶，返回 object_key。ext 为 apk / hap。"""
    object_key = f"{_BASE_PREFIX}/{release_id}.{ext}"
    content_type = _APK_CONTENT_TYPE if ext == "apk" else "application/octet-stream"
    await _put_private(object_key, apk_bytes, content_type)
    return object_key


async def put_patched_apk(release_id: UUID, apk_bytes: bytes, ext: str = "apk") -> str:
    """上传发布下载包到私有桶，返回 object_key。ext 为 apk / hap。"""
    object_key = f"{_PATCHED_PREFIX}/{release_id}.{ext}"
    content_type = _APK_CONTENT_TYPE if ext == "apk" else "application/octet-stream"
    await _put_private(object_key, apk_bytes, content_type)
    return object_key


async def get_apk_bytes(object_key: str) -> bytes | None:
    """下载 APK 全文 bytes。不存在返回 None。

    19MB APK 偶发下载场景可接受常驻内存；高频下载场景再换流式。
    """
    try:
        response = await _to_thread(archiver.client.get_object, settings.minio_bucket, object_key)
        data = await _to_thread(response.read)
        await _to_thread(response.close)
        await _to_thread(response.release_conn)
        return data
    except Exception:
        return None


async def stream_apk(object_key: str, offset: int = 0, length: int = 0):
    """异步生成器：按 64KB 块流式输出 MinIO 对象内容。

    用于 StreamingResponse，TTFB 不再等待整个对象读入内存。
    offset/length 透传 MinIO get_object，支持 Range 断点续传（length=0 表示到末尾）。
    任何异常 → 静默终止流（响应头已发，无法改 status code，但客户端能看到截断）。
    """
    try:
        response = await _to_thread(
            archiver.client.get_object,
            settings.minio_bucket,
            object_key,
            offset=offset,
            length=length,
        )
        try:
            for chunk in response.stream(64 * 1024):
                if chunk:
                    # minio SDK stream 返回 bytes 块，直接 yield 不需切线程
                    # （每个 chunk 已经在 socket read 时阻塞过，asyncio 包装上层即可）
                    yield chunk
        finally:
            await _to_thread(response.close)
            await _to_thread(response.release_conn)
    except Exception as e:
        logger.warning("stream_apk failed for %s: %s", object_key, e)


async def stat_apk_size(object_key: str) -> int | None:
    """查询 APK 文件大小（bytes），不下载内容。对象不存在或 stat 失败返回 None。"""
    try:
        stat = await _to_thread(archiver.client.stat_object, settings.minio_bucket, object_key)
        return stat.size
    except Exception:
        return None


async def delete_object(object_key: str) -> None:
    """幂等删除私有桶对象（不存在静默跳过）。"""
    try:
        await _to_thread(archiver.client.remove_object, settings.minio_bucket, object_key)
    except Exception as e:
        logger.warning("delete_object failed for %s: %s", object_key, e)


async def put_icon(release_id: UUID, content: bytes, content_type: str) -> tuple[str, str]:
    """上传 icon 到公开桶，返回 (object_key, 相对 URL)。

    相对 URL 形如 `/avatars/{public_bucket}/app-icons/{release_id}/{uuid}.{ext}`，
    前端经 nginx /avatars/ 反代直接访问。
    """
    if len(content) > _MAX_ICON_SIZE:
        raise ValueError("icon_too_large")
    if content_type not in _ALLOWED_ICON_TYPES:
        raise ValueError("icon_unsupported_type")

    await ensure_public_bucket()
    ext = _ext_from_content_type(content_type)
    object_key = f"{_ICON_PREFIX}/{release_id}/{uuid4().hex}.{ext}"
    await _to_thread(
        archiver.client.put_object,
        bucket_name=settings.minio_public_bucket,
        object_name=object_key,
        data=io.BytesIO(content),
        length=len(content),
        content_type=content_type,
    )
    url = f"/avatars/{settings.minio_public_bucket}/{object_key}"
    return object_key, url


async def delete_icon(object_key: str | None) -> None:
    """幂等删除公开桶 icon（不存在或 object_key 为 None 静默跳过）。"""
    if not object_key:
        return
    try:
        await _to_thread(archiver.client.remove_object, settings.minio_public_bucket, object_key)
    except Exception as e:
        logger.warning("delete_icon failed for %s: %s", object_key, e)


# ── internal ─────────────────────────────────────────────────


async def _put_private(object_key: str, data: bytes, content_type: str) -> None:
    """上传到私有桶，stat 校验 size 一致防截断。"""
    length = len(data)
    await _to_thread(
        archiver.client.put_object,
        bucket_name=settings.minio_bucket,
        object_name=object_key,
        data=io.BytesIO(data),
        length=length,
        content_type=content_type,
    )
    try:
        stat = await _to_thread(archiver.client.stat_object, settings.minio_bucket, object_key)
    except Exception as e:
        raise RuntimeError(f"stat verify failed for {object_key}: {e}")
    if stat.size != length:
        raise RuntimeError(
            f"upload size mismatch for {object_key}: wrote {length}, stat {stat.size}"
        )
    logger.info("Saved %s (%d bytes, verified)", object_key, length)


async def _to_thread(fn, *args, **kwargs):
    """asyncio.to_thread 包装，便于 mock。"""
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)


def _ext_from_content_type(content_type: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(content_type, "png")
