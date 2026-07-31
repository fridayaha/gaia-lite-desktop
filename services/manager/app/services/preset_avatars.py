"""预置卡通头像 — 单一真相源。

12 个手绘 SVG 风格头像存于 `app/data/preset_avatars/`，启动时由
`core.seed_preset_avatars` 幂等上传到 MinIO public bucket。

- `preset_paths()` 返回 12 个对外可访问的相对路径，供 `/auth/preset-avatars` endpoint
  返回给前端。后端只暴露相对路径，nginx 层做 /avatars/ 反代。
- `compute_preset_index(username)` 用 md5 哈希首字节 % 12，跨进程稳定
  （避开 `hash()` 的 PYTHONHASHSEED 随机化），同名用户始终分配同一头像。
"""

import hashlib
from pathlib import Path

from pkg.common.config import settings

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "preset_avatars"
PRESET_COUNT = 12
PRESET_OBJECT_PREFIX = "presets"


def preset_paths() -> list[str]:
    """返回 12 个预置头像的对外相对路径列表。"""
    return [
        f"/avatars/{settings.minio_public_bucket}/{PRESET_OBJECT_PREFIX}/{i}.svg"
        for i in range(1, PRESET_COUNT + 1)
    ]


def compute_preset_index(username: str) -> int:
    """按 username 哈希确定性返回 0..PRESET_COUNT-1 索引。"""
    return hashlib.md5(username.encode("utf-8")).digest()[0] % PRESET_COUNT


def preset_path_for_username(username: str) -> str:
    """返回给定 username 应分配的预置头像路径。"""
    return preset_paths()[compute_preset_index(username)]
