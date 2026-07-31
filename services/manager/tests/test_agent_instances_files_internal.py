"""POST /agent-instances/{id}/files/upload-internal 端点测试。

聚焦新端点的两点：
1. 鉴权：仅接受 gateway 内部令牌（is_internal=True），非 internal 调用 403。
2. internal 路径接线：resolve_instance_profile（按 instance）→ _resolve_workspace_pod
   → write_upload，返回 {filename, path, ...}。

resolve_instance_profile / write_upload 本身在 test_workspace_files.py 已有真 DB 覆盖，
这里用模块级 patch 隔离 DB / k8s，只验端点接线与鉴权。
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import app.api.agent_instances as ai
import pytest
from fastapi import HTTPException, UploadFile


def _make_upload_file(content: bytes, filename: str) -> UploadFile:
    """构造一个已塞入 content 的 UploadFile（不依赖网络请求）。"""
    return UploadFile(file=io.BytesIO(content), filename=filename)


@pytest.mark.asyncio
async def test_upload_internal_rejects_non_internal():
    """非 internal 调用（普通用户 JWT）→ 403，不触碰 DB / 文件。"""
    inst_id = uuid.uuid4()
    file = _make_upload_file(b"x", "a.png")
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await ai.upload_instance_file_internal(inst_id, file, auth=(None, False), db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_internal_rejects_when_no_profile():
    """internal 调用但解析不到 profile → 403。"""
    inst_id = uuid.uuid4()
    file = _make_upload_file(b"x", "a.png")
    db = MagicMock()
    with patch.object(ai.workspace_files, "resolve_instance_profile", AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as exc:
            await ai.upload_instance_file_internal(inst_id, file, auth=(None, True), db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_internal_success_returns_path():
    """internal 调用 → resolve_instance_profile → write_upload，返回写入字段。"""
    inst_id = uuid.uuid4()
    file = _make_upload_file(b"\x89PNGdata", "chart.png")
    db = MagicMock()

    fake_profile = MagicMock()
    fake_profile.hermes_home = "/opt/data/profiles/p1"
    fake_deployment = MagicMock()

    with (
        patch.object(
            ai.workspace_files,
            "resolve_instance_profile",
            AsyncMock(return_value=(fake_profile, fake_deployment, MagicMock())),
        ) as mock_res,
        patch.object(ai, "_resolve_workspace_pod", AsyncMock(return_value="pod-1")) as mock_pod,
        patch.object(
            ai.workspace_files,
            "write_upload",
            AsyncMock(
                return_value={
                    "filename": "chart.png",
                    "path": "uploads/chart.png",
                    "size": 9,
                    "mime": "image/png",
                    "is_image": True,
                }
            ),
        ) as mock_write,
    ):
        result = await ai.upload_instance_file_internal(inst_id, file, auth=(None, True), db=db)

    mock_res.assert_awaited_once_with(db, inst_id)
    mock_pod.assert_awaited_once()
    # write_upload 收到 k8s_manager / pod_name / workspace_root / filename / content
    assert mock_write.call_args.args[1] == "pod-1"
    assert mock_write.call_args.args[3] == "chart.png"
    assert mock_write.call_args.args[4] == b"\x89PNGdata"
    assert result["path"] == "uploads/chart.png"
    assert result["is_image"] is True


@pytest.mark.asyncio
async def test_download_file_content_disposition_with_unicode_name():
    """中文文件名下载时 Content-Disposition 合法，不触发 latin-1 编码 500（终端用户路径）。"""
    inst_id = uuid.uuid4()
    db = MagicMock()

    fake_profile = MagicMock()
    fake_profile.hermes_home = "/opt/data/profiles/p1"
    fake_deployment = MagicMock()

    with (
        patch.object(
            ai.workspace_files,
            "resolve_user_profile",
            AsyncMock(return_value=(fake_profile, fake_deployment, MagicMock())),
        ),
        patch.object(ai, "_resolve_workspace_pod", AsyncMock(return_value="pod-1")),
        patch.object(
            ai.workspace_files,
            "read_file_bytes",
            AsyncMock(
                return_value={
                    "name": "南京介绍.pdf",
                    "size": 4,
                    "bytes": b"%PDF",
                    "mime": "application/pdf",
                }
            ),
        ),
    ):
        resp = await ai.download_instance_file(inst_id, path="南京介绍.pdf", auth=(None, False), db=db)

    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert "filename*=UTF-8''%E5%8D%97%E4%BA%AC%E4%BB%8B%E7%BB%8D.pdf" in disposition
    assert "filename=\"download.pdf\"" in disposition


@pytest.mark.asyncio
async def test_download_instance_file_chinese_name_no_500():
    """中文名文件下载：Content-Disposition legacy filename 用 ASCII fallback，非 ASCII 走 filename*，
    不再因 latin-1 编码失败 500（内部路径）。

    Regression: 旧实现 ``filename="<中文名>"`` 直接塞原始中文，Starlette init_headers 用
    latin-1 编码 header 值抛 UnicodeEncodeError → 500（中文名文件下载必挂）。
    """
    inst_id = uuid.uuid4()
    db = MagicMock()
    fake_profile = MagicMock()
    fake_profile.hermes_home = "/opt/data/profiles/p1"

    with (
        patch.object(
            ai.workspace_files,
            "resolve_instance_profile",
            AsyncMock(return_value=(fake_profile, MagicMock(), MagicMock())),
        ),
        patch.object(ai, "_resolve_workspace_pod", AsyncMock(return_value="pod-1")),
        patch.object(
            ai.workspace_files,
            "read_file_bytes",
            AsyncMock(return_value={
                "name": "南京两日游攻略.pdf",
                "size": 11,
                "bytes": b"%PDF-1.4 fake",
                "mime": "application/pdf",
            }),
        ),
    ):
        resp = await ai.download_instance_file(inst_id, path="output/南京两日游攻略.pdf", auth=(None, True), db=db)

    # 不再 500：Response 正常构造，body 是原始字节
    assert resp.status_code == 200
    assert resp.body == b"%PDF-1.4 fake"
    cd = resp.headers.get("content-disposition", "")
    # legacy filename 段 ASCII 安全（无中文），中文走 RFC 5987 filename*
    assert "南京" not in cd.split("filename*=")[0]
    assert "filename*=UTF-8''" in cd
    # filename* 段是 percent-encoded 中文（不含裸中文）
    star_part = cd.split("filename*=UTF-8''", 1)[1]
    assert "南京" not in star_part
    assert "%E5%8D%97" in star_part
