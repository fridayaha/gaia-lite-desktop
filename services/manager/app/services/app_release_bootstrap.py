"""manager 启动期自动注册 base APK 到 app_releases 表。

dev 把构建好的 base APK（带占位符）拷到 manager 镜像的 /app/base-apks/ 目录，
镜像启动时本模块扫描该目录，对每个未注册的 version 在 DB 创建 draft 记录。

文件名约定：`<任意名>-<版本>.apk`，如 `知行-template-0.8.123.apk` → version=0.8.123。
version 唯一约束保证相同 versionName 不重复注册（重启幂等）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.app_releases import _parse_version_from_filename
from app.models import AppRelease, AppReleaseStatus
from app.services import app_release_storage
from pkg.common.config import settings

logger = logging.getLogger(__name__)


async def bootstrap_base_apks(db: AsyncSession) -> int:
    """扫描 base-apks 目录，注册未注册的 APK。返回新注册数。

    目录不存在或为空静默返回 0（dev 本地不起 manager 时无此目录）。
    """
    apk_dir = Path(settings.apk_base_dir)
    if not apk_dir.is_dir():
        return 0

    registered = 0
    for apk_path in sorted(apk_dir.glob("*.apk")):
        version = _parse_version_from_filename(apk_path.name)
        if not version:
            logger.warning("skip %s: filename missing -<version>.apk suffix", apk_path.name)
            continue

        existing = (
            await db.execute(
                select(AppRelease).where(
                    AppRelease.platform == "android",
                    AppRelease.version == version,
                )
            )
        ).scalar_one_or_none()
        if existing:
            logger.info("skip %s: version %s already registered", apk_path.name, version)
            continue

        try:
            content = apk_path.read_bytes()
            from uuid import uuid4

            release_id = uuid4()
            object_key = await app_release_storage.put_base_apk(release_id, content)

            icon_object_key = None
            try:
                from app.services import apk_icon_extractor

                icon = await apk_icon_extractor.extract_icon(content)
                if icon:
                    icon_object_key, _ = await app_release_storage.put_icon(
                        release_id, icon.content, icon.content_type
                    )
            except Exception as icon_err:
                logger.warning(
                    "extract icon failed for %s: %s", apk_path.name, icon_err
                )

            release = AppRelease(
                id=release_id,
                version=version,
                base_apk_object_key=object_key,
                icon_object_key=icon_object_key,
                display_name="知行",
                description="",
                status=AppReleaseStatus.DRAFT.value,
            )
            db.add(release)
            await db.commit()
            registered += 1
            logger.info("registered base APK: %s (version=%s)", apk_path.name, version)
        except Exception as e:
            logger.error("failed to register %s: %s", apk_path.name, e)
            await db.rollback()
            continue

    return registered
