"""MinIO public bucket 公共逻辑。

`upload_avatar` 和 `seed_preset_avatars` 共用此模块确保 bucket 存在 + 公开可读。
"""

import asyncio
import json

from app.worker.minio_archiver import archiver
from pkg.common.config import settings

_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{settings.minio_public_bucket}/*"],
        }
    ],
}


async def ensure_public_bucket() -> None:
    """幂等：bucket 不存在才创建 + 设 policy；已存在直接返回。"""
    client = archiver.client
    bucket = settings.minio_public_bucket
    if not await asyncio.to_thread(client.bucket_exists, bucket):
        await asyncio.to_thread(client.make_bucket, bucket)
        await asyncio.to_thread(client.set_bucket_policy, bucket, json.dumps(_POLICY))
