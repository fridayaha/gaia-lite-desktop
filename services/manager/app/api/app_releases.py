"""APP 发布 API — admin 上传/编辑/发布 + public 拉取已发布安装包。

数据流：
- android：base APK（带占位符）入私有桶 → admin 编辑元数据 → publish 触发
  ApkPatcher.patch（替换占位符 + zipalign + apksigner 重签）→ patched APK 入私有桶。
- harmony：admin 上传已签名 .hap（构建期已写入真实服务端点）→ publish 原样转存，
  不做 patch（无 ApkPatcher 等价物，签名由 DevEco/hvigor 在构建机完成）。
landing 下载页经 /api/manager/public/app-releases/latest?platform= 拉取，下载链接走 /apk 端点。
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_platform_admin
from app.models import AppRelease, AppReleaseStatus, User
from app.schemas import (
    AppReleaseLatestResponse,
    AppReleaseListResponse,
    AppReleasePublishRequest,
    AppReleaseResponse,
    AppReleaseUpdateRequest,
)
from app.services import app_release_storage
from app.services.apk_patcher import ApkPatchError, ApkPatcher
from pkg.common.config import settings
from pkg.common.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/manager/app-releases", tags=["app-releases"])
public_router = APIRouter(
    prefix="/api/manager/public/app-releases", tags=["app-releases-public"]
)

_MAX_PACKAGE_SIZE = 50 * 1024 * 1024  # 50MB，base APK ~20MB / hap ~2MB 留余量
# 文件名版本提取：知行-0.8.123.apk / app-release-0.8.123.apk / 知行-template-0.8.123.hap
_VERSION_RE = re.compile(r"-(\d+\.\d+\.\d+(?:[-\w.]*)?)\.(apk|hap)$", re.IGNORECASE)

PLATFORM_ANDROID = "android"
PLATFORM_HARMONY = "harmony"
_PLATFORMS = {PLATFORM_ANDROID, PLATFORM_HARMONY}


# ── helpers ───────────────────────────────────────────────────


def _icon_url(r: AppRelease) -> str | None:
    if not r.icon_object_key:
        return None
    return f"/avatars/{settings.minio_public_bucket}/{r.icon_object_key}"


def _to_response(r: AppRelease) -> AppReleaseResponse:
    return AppReleaseResponse(
        id=r.id,
        platform=r.platform,
        version=r.version,
        display_name=r.display_name,
        description=r.description,
        icon_url=_icon_url(r),
        status=r.status,
        manager_url=r.manager_url,
        gateway_url=r.gateway_url,
        created_at=r.created_at,
        published_at=r.published_at,
    )


def _get_patcher() -> ApkPatcher:
    return ApkPatcher(
        keystore_path=settings.apk_keystore_path,
        keystore_alias=settings.apk_keystore_alias,
        keystore_password=settings.apk_keystore_password,
        key_password=settings.apk_key_password,
        zipalign_bin=settings.apk_zipalign_bin,
        apksigner_bin=settings.apk_apksigner_bin,
    )


def _parse_version_from_filename(filename: str) -> str | None:
    """从安装包文件名提取 version：知行-0.8.123.apk → 0.8.123。"""
    m = _VERSION_RE.search(filename)
    return m.group(1) if m else None


def _infer_platform(filename: str) -> str | None:
    """按扩展名推断平台：.hap → harmony，.apk → android。其他返回 None。"""
    lower = filename.lower()
    if lower.endswith(".hap"):
        return PLATFORM_HARMONY
    if lower.endswith(".apk"):
        return PLATFORM_ANDROID
    return None


async def _extract_and_store_icon(release_id: _uuid.UUID, apk_bytes: bytes) -> str | None:
    """从 APK 提取应用图标存入公开桶，返回 icon_object_key。

    提取失败（aapt 不可用 / 非 APK 格式如 .hap）静默返回 None，不阻断上传流程。
    """
    try:
        from app.services import apk_icon_extractor

        icon = await apk_icon_extractor.extract_icon(apk_bytes)
        if not icon:
            return None
        object_key, _ = await app_release_storage.put_icon(
            release_id, icon.content, icon.content_type
        )
        return object_key
    except Exception as e:
        logger.warning("extract icon failed for release %s: %s", release_id, e)
        return None


# ── admin endpoints ───────────────────────────────────────────


@router.get("", response_model=AppReleaseListResponse)
async def list_app_releases(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    platform: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """分页列出 APP 发布记录（按 created_at 降序），platform 可选筛选。"""
    stmt = select(AppRelease)
    count_stmt = select(func.count(AppRelease.id))
    if platform:
        if platform not in _PLATFORMS:
            raise HTTPException(status_code=400, detail="invalid_platform")
        stmt = stmt.where(AppRelease.platform == platform)
        count_stmt = count_stmt.where(AppRelease.platform == platform)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        await db.execute(
            stmt.order_by(AppRelease.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return AppReleaseListResponse(
        items=[_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=AppReleaseResponse, status_code=status.HTTP_201_CREATED)
async def upload_base_apk(
    file: UploadFile = File(...),
    platform: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """上传 base 安装包（.apk 带占位符 / .hap 已签名）+ 创建 draft 记录。

    文件名必须含 `-<version>.(apk|hap)` 后缀（如 知行-0.8.123.apk / 知行-0.8.123.hap）。
    platform 缺省按扩展名推断；显式传 platform 时必须与扩展名一致。
    相同 (platform, version) 已存在则 409。
    """
    content = await file.read()
    if len(content) > _MAX_PACKAGE_SIZE:
        raise HTTPException(status_code=413, detail="package_too_large")

    filename = file.filename or ""
    inferred = _infer_platform(filename)
    if inferred is None:
        raise HTTPException(status_code=400, detail="filename_must_end_with_apk_or_hap")
    if platform is not None and platform != inferred:
        raise HTTPException(status_code=400, detail="platform_mismatch_with_extension")
    resolved_platform = inferred

    version = _parse_version_from_filename(filename)
    if not version:
        raise HTTPException(
            status_code=400,
            detail="filename_missing_version_pattern (expect -<version>.apk/.hap suffix)",
        )

    existing = (
        await db.execute(
            select(AppRelease).where(
                AppRelease.platform == resolved_platform,
                AppRelease.version == version,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="version_already_exists")

    release_id = _uuid.uuid4()
    ext = "hap" if resolved_platform == PLATFORM_HARMONY else "apk"
    object_key = await app_release_storage.put_base_apk(release_id, content, ext=ext)

    icon_object_key = None
    if resolved_platform == PLATFORM_ANDROID:
        icon_object_key = await _extract_and_store_icon(release_id, content)

    release = AppRelease(
        id=release_id,
        platform=resolved_platform,
        version=version,
        base_apk_object_key=object_key,
        icon_object_key=icon_object_key,
        display_name="知行",
        description="",
        status=AppReleaseStatus.DRAFT.value,
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)
    return _to_response(release)


@router.get("/{release_id}", response_model=AppReleaseResponse)
async def get_app_release(
    release_id: _uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    r = (
        await db.execute(select(AppRelease).where(AppRelease.id == release_id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="not_found")
    return _to_response(r)


@router.patch("/{release_id}", response_model=AppReleaseResponse)
async def update_app_release(
    release_id: _uuid.UUID,
    data: AppReleaseUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """编辑 display_name / description。"""
    r = (
        await db.execute(select(AppRelease).where(AppRelease.id == release_id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="not_found")
    r.display_name = data.display_name
    r.description = data.description
    await db.commit()
    await db.refresh(r)
    return _to_response(r)


@router.post("/{release_id}/icon", response_model=AppReleaseResponse)
async def upload_icon(
    release_id: _uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """上传 icon（png/jpeg/webp/gif ≤ 2MB）。旧 icon 自动删除。"""
    r = (
        await db.execute(select(AppRelease).where(AppRelease.id == release_id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="not_found")

    content = await file.read()
    try:
        object_key, _url = await app_release_storage.put_icon(
            release_id, content, file.content_type or "image/png"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if r.icon_object_key:
        await app_release_storage.delete_icon(r.icon_object_key)

    r.icon_object_key = object_key
    await db.commit()
    await db.refresh(r)
    return _to_response(r)


@router.delete("/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_app_release(
    release_id: _uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """删除记录 + MinIO 对象（base / patched / icon）。"""
    r = (
        await db.execute(select(AppRelease).where(AppRelease.id == release_id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="not_found")

    await app_release_storage.delete_object(r.base_apk_object_key)
    if r.patched_apk_object_key:
        await app_release_storage.delete_object(r.patched_apk_object_key)
    await app_release_storage.delete_icon(r.icon_object_key)

    await db.delete(r)
    await db.commit()


@router.post("/{release_id}/publish", response_model=AppReleaseResponse)
async def publish_app_release(
    release_id: _uuid.UUID,
    data: AppReleasePublishRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """发布：产出下载包存私有桶 + 标记 published。

    - android：base APK bytes → 替换占位符 → zipalign → apksigner 重签（1-3s，
      asyncio.to_thread 包裹）。
    - harmony：.hap 构建期已签名且服务端点已写入，原样转存，不做 patch。
    """
    r = (
        await db.execute(select(AppRelease).where(AppRelease.id == release_id))
    ).scalar_one_or_none()
    if not r:
        raise HTTPException(status_code=404, detail="not_found")

    base_bytes = await app_release_storage.get_apk_bytes(r.base_apk_object_key)
    if base_bytes is None:
        raise HTTPException(status_code=500, detail="base_package_not_found_in_storage")

    if r.platform == PLATFORM_HARMONY:
        patched_bytes = base_bytes
    else:
        try:
            patched_bytes = await _get_patcher().patch(
                base_bytes, data.manager_url, data.gateway_url
            )
        except ApkPatchError as e:
            logger.error("APK patch failed for release %s: %s", release_id, e)
            raise HTTPException(status_code=500, detail=f"patch_failed: {e}")

    # 覆盖发布：先删旧下载包
    if r.patched_apk_object_key:
        await app_release_storage.delete_object(r.patched_apk_object_key)
    ext = "hap" if r.platform == PLATFORM_HARMONY else "apk"
    patched_key = await app_release_storage.put_patched_apk(release_id, patched_bytes, ext=ext)

    r.patched_apk_object_key = patched_key
    r.manager_url = data.manager_url
    r.gateway_url = data.gateway_url
    r.status = AppReleaseStatus.PUBLISHED.value
    r.published_at = _dt.datetime.now(_dt.timezone.utc)

    await db.commit()
    await db.refresh(r)
    return _to_response(r)


# ── public endpoints（无 auth）─────────────────────────────────


@public_router.get("/latest", response_model=AppReleaseLatestResponse | None)
async def get_latest_published(
    platform: str = Query(PLATFORM_ANDROID),
    db: AsyncSession = Depends(get_db),
):
    """landing 下载页拉取指定平台最新 published 记录。无 published 记录返回 null。"""
    if platform not in _PLATFORMS:
        raise HTTPException(status_code=400, detail="invalid_platform")
    stmt = (
        select(AppRelease)
        .where(
            AppRelease.status == AppReleaseStatus.PUBLISHED.value,
            AppRelease.platform == platform,
        )
        .order_by(AppRelease.published_at.desc())
        .limit(1)
    )
    r = (await db.execute(stmt)).scalar_one_or_none()
    if not r:
        return None
    size = None
    if r.patched_apk_object_key:
        size = await app_release_storage.stat_apk_size(r.patched_apk_object_key)
    return AppReleaseLatestResponse(
        id=r.id,
        platform=r.platform,
        display_name=r.display_name,
        description=r.description,
        icon_url=_icon_url(r),
        version=r.version,
        size=size,
    )


@public_router.get("/by-version/{version}", response_model=AppReleaseLatestResponse | None)
async def get_published_by_version(
    version: str,
    platform: str = Query(PLATFORM_ANDROID),
    db: AsyncSession = Depends(get_db),
):
    """按版本号拉取已发布记录（app 在「版本」详情页展示当前安装版本的描述）。

    version 精确匹配 AppRelease.version（裸版本号，无 v 前缀）；无匹配 published 记录返回 null。
    """
    if platform not in _PLATFORMS:
        raise HTTPException(status_code=400, detail="invalid_platform")
    stmt = (
        select(AppRelease)
        .where(
            AppRelease.status == AppReleaseStatus.PUBLISHED.value,
            AppRelease.platform == platform,
            AppRelease.version == version,
        )
        .order_by(AppRelease.published_at.desc())
        .limit(1)
    )
    r = (await db.execute(stmt)).scalar_one_or_none()
    if not r:
        return None
    size = None
    if r.patched_apk_object_key:
        size = await app_release_storage.stat_apk_size(r.patched_apk_object_key)
    return AppReleaseLatestResponse(
        id=r.id,
        platform=r.platform,
        display_name=r.display_name,
        description=r.description,
        icon_url=_icon_url(r),
        version=r.version,
        size=size,
    )


@public_router.get("/{release_id}/apk")
async def download_apk(
    release_id: _uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """下载发布安装包。landing nginx /api/manager/ 已反代到 manager。

    StreamingResponse 边读 MinIO 边吐，TTFB 不再等整个对象读入内存；
    landing nginx 配 `proxy_buffering off`，浏览器立即收到首字节并显示下载进度。
    Content-Disposition 同时给 ASCII filename 和 UTF-8 filename*，浏览器优先取后者。

    支持单段 Range（206）：Android DownloadManager 在弱网中断后带
    `Range: bytes=N-` 断点续传，不支持时会报 ERROR_CANNOT_RESUME(1008)。
    """
    r = (
        await db.execute(select(AppRelease).where(AppRelease.id == release_id))
    ).scalar_one_or_none()
    if not r or r.status != AppReleaseStatus.PUBLISHED.value or not r.patched_apk_object_key:
        raise HTTPException(status_code=404, detail="not_published")

    # 确认对象存在并拿 Content-Length，避免流式开始后才发现 404
    size = await app_release_storage.stat_apk_size(r.patched_apk_object_key)
    if size is None:
        raise HTTPException(status_code=404, detail="patched_apk_not_found_in_storage")

    from urllib.parse import quote

    if r.platform == PLATFORM_HARMONY:
        media_type = "application/octet-stream"
        ascii_name = "zhixing.hap"
        filename = "知行.hap"
    else:
        media_type = "application/vnd.android.package-archive"
        ascii_name = "zhixing.apk"
        filename = "知行.apk"
    filename_encoded = quote(filename)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{filename_encoded}"

    range_header = request.headers.get("range")
    if range_header:
        parsed = _parse_range(range_header, size)
        if parsed is None:
            # 语法合法但范围不可满足（如 start >= size）→ 416
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}"},
            )
        if parsed != (-1, -1):
            start, end = parsed
            length = end - start + 1
            return StreamingResponse(
                app_release_storage.stream_apk(r.patched_apk_object_key, offset=start, length=length),
                status_code=206,
                media_type=media_type,
                headers={
                    "Content-Disposition": disposition,
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Accept-Ranges": "bytes",
                    "Cache-Control": "no-store",
                    "Content-Length": str(length),
                },
            )
        # (-1, -1) 哨兵：非 bytes 单位 / 多段 / 畸形 → 忽略 Range，按 200 全量返回

    return StreamingResponse(
        app_release_storage.stream_apk(r.patched_apk_object_key),
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store",
            "Content-Length": str(size),
        },
    )


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """解析单段 Range 头，返回 (start, end) 闭区间；不可满足或异常返回 None。

    - 非 bytes 单位 / 多段（含逗号）→ 返回 (-1, -1) 哨兵，调用方忽略 Range 按 200 全量返回
    - bytes=start- / bytes=start-end / bytes=-suffix 正常解析；start 被钳到 size 内
    - start >= size（不可满足）→ None → 调用方 416
    """
    if not header.startswith("bytes="):
        return (-1, -1)
    spec = header[len("bytes="):].strip()
    if "," in spec:
        return (-1, -1)
    if "-" not in spec:
        return (-1, -1)
    start_s, _, end_s = spec.partition("-")
    try:
        if start_s == "":
            # suffix 形式：bytes=-500 → 最后 500 字节
            suffix = int(end_s)
            if suffix <= 0:
                return (-1, -1)
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
    except ValueError:
        return (-1, -1)
    if start < 0 or end < 0:
        return (-1, -1)
    if start >= size:
        return None
    end = min(end, size - 1)
    if end < start:
        return (-1, -1)
    return (start, end)
