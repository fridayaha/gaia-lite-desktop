"""apk_icon_extractor 单元测试——mock aapt subprocess + 真 zipfile。"""
from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest

from app.services import apk_icon_extractor
from app.services.apk_icon_extractor import (
    ExtractedIcon,
    _guess_content_type,
    _pick_best_icon_path,
    extract_icon,
)


# ── 纯函数测试（不调 subprocess）────────────────────────────


def test_pick_best_icon_path_picks_highest_density():
    badging = (
        "package: name='com.example.app'\n"
        "application-icon-120:'res/drawable-ldpi/ic.png'\n"
        "application-icon-240:'res/drawable-hdpi/ic.png'\n"
        "application-icon-480:'res/drawable-xxhdpi/ic.png'\n"
        "application-icon-320:'res/drawable-xhdpi/ic.png'\n"
    )
    assert _pick_best_icon_path(badging) == "res/drawable-xxhdpi/ic.png"


def test_pick_best_icon_path_fallback_to_application_icon_line():
    """某些 aapt 版本只输出 application: ... icon='...' 单行。"""
    badging = (
        "package: name='com.example.app'\n"
        "application: label='Example' icon='res/mipmap/ic_launcher.webp'\n"
    )
    assert _pick_best_icon_path(badging) == "res/mipmap/ic_launcher.webp"


def test_pick_best_icon_path_returns_none_when_no_icon():
    badging = "package: name='com.example.app'\napplication: label='No Icon'\n"
    assert _pick_best_icon_path(badging) is None


def test_pick_best_icon_path_density_fallback_when_only_application_has_icon():
    """application-icon-XXX 优先级高于 application: icon=。"""
    badging = (
        "application: label='X' icon='res/low.png'\n"
        "application-icon-640:'res/high.png'\n"
    )
    assert _pick_best_icon_path(badging) == "res/high.png"


def test_guess_content_type_by_extension():
    assert _guess_content_type("res/drawable/ic.png") == "image/png"
    assert _guess_content_type("res/drawable/ic.JPG") == "image/jpeg"
    assert _guess_content_type("res/drawable/ic.jpeg") == "image/jpeg"
    assert _guess_content_type("res/drawable/ic.webp") == "image/webp"
    assert _guess_content_type("res/drawable/ic.gif") == "image/gif"
    # 未知扩展名默认 png（Android 主流）
    assert _guess_content_type("res/drawable/ic") == "image/png"


# ── extract_icon 集成（mock subprocess + 真 zipfile）────────


def _build_fake_apk(entries: dict[str, bytes]) -> bytes:
    """构造含给定 entry 的 zip bytes。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_extract_icon_success_returns_png_bytes():
    """aapt 输出多 density 图标 + APK 内有对应文件 → 提取最高 density 的 bytes。"""
    icon_bytes = b"\x89PNG\r\n\x1a\n" + b"icon-data-high-res"
    apk = _build_fake_apk({
        "AndroidManifest.xml": b"manifest",
        "res/drawable-hdpi/ic.png": b"low-res",
        "res/drawable-xxhdpi/ic.png": icon_bytes,
    })
    fake_badging = (
        "package: name='com.example'\n"
        "application-icon-240:'res/drawable-hdpi/ic.png'\n"
        "application-icon-480:'res/drawable-xxhdpi/ic.png'\n"
    )

    with patch.object(
        apk_icon_extractor, "_run_aapt_badging", return_value=fake_badging
    ):
        result = await extract_icon(apk)

    assert result is not None
    assert result.content == icon_bytes
    assert result.content_type == "image/png"


@pytest.mark.asyncio
async def test_extract_icon_picks_webp_content_type():
    icon_bytes = b"RIFF....WEBP"
    apk = _build_fake_apk({"res/mipmap/ic.webp": icon_bytes})
    fake_badging = "application: label='X' icon='res/mipmap/ic.webp'\n"

    with patch.object(
        apk_icon_extractor, "_run_aapt_badging", return_value=fake_badging
    ):
        result = await extract_icon(apk)

    assert result is not None
    assert result.content_type == "image/webp"
    assert result.content == icon_bytes


@pytest.mark.asyncio
async def test_extract_icon_aapt_missing_returns_none():
    """aapt 二进制不存在（FileNotFoundError）→ 静默返回 None。"""
    apk = _build_fake_apk({"res/ic.png": b"x"})

    with patch.object(
        apk_icon_extractor,
        "_run_aapt_badging",
        side_effect=FileNotFoundError("aapt not found"),
    ):
        result = await extract_icon(apk)

    assert result is None


@pytest.mark.asyncio
async def test_extract_icon_aapt_nonzero_exit_returns_none():
    """aapt 退出码非 0 → 静默返回 None。"""
    apk = _build_fake_apk({"res/ic.png": b"x"})

    with patch.object(
        apk_icon_extractor,
        "_run_aapt_badging",
        side_effect=RuntimeError("aapt exit 1: corrupted apk"),
    ):
        result = await extract_icon(apk)

    assert result is None


@pytest.mark.asyncio
async def test_extract_icon_no_icon_in_badging_returns_none():
    """APK 内无 application-icon（罕见）→ None。"""
    apk = _build_fake_apk({"AndroidManifest.xml": b"x"})
    fake_badging = "package: name='com.example'\napplication: label='No Icon'\n"

    with patch.object(
        apk_icon_extractor, "_run_aapt_badging", return_value=fake_badging
    ):
        result = await extract_icon(apk)

    assert result is None


@pytest.mark.asyncio
async def test_extract_icon_entry_missing_in_zip_returns_none():
    """aapt 输出了路径但 APK 内没该文件（resources.arsc 引用错位）→ None。"""
    apk = _build_fake_apk({"AndroidManifest.xml": b"x"})  # 没有 res/drawable/ic.png
    fake_badging = "application-icon-480:'res/drawable/ic.png'\n"

    with patch.object(
        apk_icon_extractor, "_run_aapt_badging", return_value=fake_badging
    ):
        result = await extract_icon(apk)

    assert result is None
