"""预置卡通头像幂等 seed。

启动时把 `app/data/preset_avatars/*.svg` 上传到 MinIO public bucket 的
`presets/` 前缀下。已存在对象不覆盖（stat_object 成功即跳过），bucket 清空自愈。

- 失败不阻塞启动（startup 外层已包 try/except）
- 源文件缺失仅 warning，不抛错（避免开发环境未生成时启动失败）
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

from minio.error import S3Error

from app.services.minio_public import ensure_public_bucket
from app.services.preset_avatars import (
    DATA_DIR,
    PRESET_COUNT,
    PRESET_OBJECT_PREFIX,
)
from app.worker.minio_archiver import archiver
from pkg.common.config import settings

logger = logging.getLogger(__name__)


async def seed_preset_avatars() -> int:
    """幂等上传 12 个预置头像到 MinIO。返回新上传的对象数。"""
    await ensure_public_bucket()
    client = archiver.client
    bucket = settings.minio_public_bucket
    uploaded = 0

    for i in range(1, PRESET_COUNT + 1):
        object_name = f"{PRESET_OBJECT_PREFIX}/{i}.svg"
        local = DATA_DIR / f"{i}.svg"
        if not local.exists():
            logger.warning("preset source missing: %s", local)
            continue
        try:
            await asyncio.to_thread(client.stat_object, bucket, object_name)
            continue  # 已存在不覆盖
        except S3Error:
            pass
        data = local.read_bytes()
        await asyncio.to_thread(
            client.put_object,
            bucket_name=bucket,
            object_name=object_name,
            data=BytesIO(data),
            length=len(data),
            content_type="image/svg+xml",
        )
        uploaded += 1
        logger.info("seeded preset avatar: %s", object_name)

    return uploaded
