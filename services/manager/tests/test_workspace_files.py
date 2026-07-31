"""workspace_files 只读文件浏览的纯函数 + mock 测试。

覆盖：路径锚定安全（safe_resolve_ws）、敏感文件过滤、list/read 的 exec 输出解析。
不依赖 DB / k8s —— k8s_manager 用 MagicMock 模拟 exec_command_in_pod 返回值。
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from app.models import (
    AgentDeployment,
    AgentProfile,
    AgentStatus,
    DeploymentStatus,
    ResourcePool,
    User,
    UserGroup,
    user_group_members,
)
from app.schemas import (
    AgentDefinitionCreate,
    AgentInstanceCreate,
    PublishVersionRequest,
)
from app.services import definition_service, instance_service, litellm_client
from app.services import workspace_files as wf
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pkg.common.config import settings

# ── safe_resolve_ws ──


def test_safe_resolve_root():
    root = Path("/opt/data/profiles/p1")
    assert wf.safe_resolve_ws(root, ".") == root.resolve()
    assert wf.safe_resolve_ws(root, "") == root.resolve()
    assert wf.safe_resolve_ws(root, "./") == root.resolve()


def test_safe_resolve_subdir():
    root = Path("/opt/data/profiles/p1")
    resolved = wf.safe_resolve_ws(root, "skills/foo")
    assert resolved == (root / "skills" / "foo").resolve()


def test_safe_resolve_rejects_traversal():
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        wf.safe_resolve_ws(root, "../p2/secret")
    with pytest.raises(ValueError):
        wf.safe_resolve_ws(root, "skills/../../p2")


def test_safe_resolve_rejects_absolute():
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        wf.safe_resolve_ws(root, "/etc/passwd")


def test_safe_resolve_rejects_sensitive():
    root = Path("/opt/data/profiles/p1")
    for name in ["config.yaml", "secrets.enc", ".env", "id_rsa.key", "cert.pem"]:
        with pytest.raises(ValueError):
            wf.safe_resolve_ws(root, name)


def test_safe_resolve_allows_normal_dotfile_path():
    """普通文件可通过，.git 等敏感由 list 脚本过滤，resolve 层只拦敏感名。"""
    root = Path("/opt/data/profiles/p1")
    resolved = wf.safe_resolve_ws(root, "workspace/readme.md")
    assert resolved == (root / "workspace/readme.md").resolve()


# ── _is_sensitive ──


def test_is_sensitive():
    for name in [".env", "config.yaml", "config.yml", "secrets.enc", "auth.json",
                 "server.key", "tls.pem", "data.enc", "__pycache__", ".git"]:
        assert wf._is_sensitive(name) is True, name
    for name in ["readme.md", "main.py", "data.json", "skills", "workspace"]:
        assert wf._is_sensitive(name) is False, name


# ── list_files (mock exec) ──


def _mock_k8s(stdout: str) -> MagicMock:
    km = MagicMock()
    km.exec_command_in_pod = AsyncMock(return_value=stdout)
    return km


@pytest.mark.asyncio
async def test_list_files_parses_entries_and_path():
    payload = {"entries": [
        {"name": "skills", "is_dir": True, "size": 0, "mtime_ns": 1},
        {"name": "readme.md", "is_dir": False, "size": 12, "mtime_ns": 2},
    ]}
    km = _mock_k8s(json.dumps(payload))
    root = Path("/opt/data/profiles/p1")
    result = await wf.list_files(km, "pod-1", root, ".")
    assert result["path"] == "."
    names = {e["name"]: e for e in result["entries"]}
    assert names["skills"]["is_dir"] is True
    assert names["readme.md"]["is_dir"] is False
    # path 字段 = name（根目录）
    assert names["readme.md"]["path"] == "readme.md"
    # is_text 推断
    assert names["readme.md"]["is_text"] is True
    assert names["skills"]["is_text"] is False


@pytest.mark.asyncio
async def test_list_files_subdir_path_prefix():
    payload = {"entries": [
        {"name": "foo.py", "is_dir": False, "size": 5, "mtime_ns": 1},
    ]}
    km = _mock_k8s(json.dumps(payload))
    root = Path("/opt/data/profiles/p1")
    result = await wf.list_files(km, "pod-1", root, "workspace")
    entry = result["entries"][0]
    assert entry["path"] == "workspace/foo.py"


@pytest.mark.asyncio
async def test_list_files_not_found_returns_empty():
    km = _mock_k8s(json.dumps({"error": "not found", "entries": []}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.list_files(km, "pod-1", root, "nonexistent")
    assert result["entries"] == []
    assert result["error"] == "not found"


@pytest.mark.asyncio
async def test_list_files_traversal_rejected():
    km = _mock_k8s('{"entries": []}')
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        await wf.list_files(km, "pod-1", root, "../../etc")


# ── read_file_content (mock exec) ──


@pytest.mark.asyncio
async def test_read_file_content_text():
    raw = b"hello world"
    payload = {
        "size": len(raw),
        "truncated": False,
        "is_text": True,
        "content_b64": base64.b64encode(raw).decode("ascii"),
    }
    km = _mock_k8s(json.dumps(payload))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_content(km, "pod-1", root, "readme.md")
    assert result["content"] == "hello world"
    assert result["is_text"] is True
    assert result["truncated"] is False
    assert result["is_markdown"] is True
    assert result["is_image"] is False
    assert result["content_b64"] is None  # 文本不回 b64


@pytest.mark.asyncio
async def test_read_file_content_binary_image():
    raw = b"\x89PNG\r\n\x1a\n"  # PNG 头（含二进制）
    payload = {
        "size": 1000,
        "truncated": False,
        "is_text": False,
        "content_b64": base64.b64encode(raw).decode("ascii"),
    }
    km = _mock_k8s(json.dumps(payload))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_content(km, "pod-1", root, "logo.png")
    assert result["is_text"] is False
    assert result["content"] is None
    assert result["content_b64"] == base64.b64encode(raw).decode("ascii")
    assert result["is_image"] is True


@pytest.mark.asyncio
async def test_read_file_content_truncated():
    raw = b"x" * 100
    payload = {
        "size": 500_000,
        "truncated": True,
        "is_text": True,
        "content_b64": base64.b64encode(raw).decode("ascii"),
    }
    km = _mock_k8s(json.dumps(payload))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_content(km, "pod-1", root, "big.txt")
    assert result["truncated"] is True
    assert result["size"] == 500_000


@pytest.mark.asyncio
async def test_read_file_content_sensitive_rejected():
    km = _mock_k8s('{"content_b64": ""}')
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        await wf.read_file_content(km, "pod-1", root, "config.yaml")


@pytest.mark.asyncio
async def test_read_file_content_svg_keeps_b64_even_if_text():
    """SVG 无 null 字节被判为 is_text，但 is_image=True → 必须保留 content_b64，
    否则网关 resolve_image_to_data_url 的 is_image-and-content_b64 守卫失败，SVG 永不解析。"""
    raw = b"<svg/>"
    payload = {
        "size": len(raw),
        "truncated": False,
        "is_text": True,  # SVG 无 null 字节
        "content_b64": base64.b64encode(raw).decode("ascii"),
    }
    km = _mock_k8s(json.dumps(payload))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_content(km, "pod-1", root, "diagram.svg")
    assert result["is_image"] is True
    assert result["is_text"] is True
    assert result["content_b64"] == base64.b64encode(raw).decode("ascii")


# ── write_upload (mock exec, dedup 基于 Pod 实际目录) ──


def _mock_k8s_dispatch(list_entries: list[dict]):
    """k8s_manager mock：list_files 的 exec（含 scandir）返回 list_entries JSON，
    其余 exec（rm/cat/write/chown）返回空串。"""
    async def _exec(pod_name, cmd_list):
        cmd = cmd_list[0] if isinstance(cmd_list, list) else cmd_list
        if "scandir" in cmd:  # _LIST_SCRIPT
            return json.dumps({"entries": list_entries})
        return ""
    km = MagicMock()
    km.exec_command_in_pod = AsyncMock(side_effect=_exec)
    return km


@pytest.mark.asyncio
async def test_write_upload_dedup_uses_pod_state_not_local_fs():
    """同名文件已存在于 Pod → 写 -1 后缀；去重基于 Pod 目录（list_files），不碰本地 FS。"""
    km = _mock_k8s_dispatch([{"name": "f.png", "is_dir": False, "size": 1, "mtime_ns": 1}])
    root = Path("/opt/data/profiles/p1")
    result = await wf.write_upload(km, "pod-1", root, "f.png", b"\x89PNG")
    assert result["filename"] == "f-1.png"
    assert result["path"] == "uploads/f-1.png"
    # 写入命令的目标文件名应是去重后的 f-1.png
    write_cmds = [
        c[0][1][0] for c in km.exec_command_in_pod.call_args_list
        if isinstance(c[0][1][0], str) and "base64 -d" in c[0][1][0]
    ]
    assert any("f-1.png" in c for c in write_cmds)


@pytest.mark.asyncio
async def test_write_upload_no_dedup_when_dir_empty():
    """Pod uploads 目录为空 → 用原名，无后缀。"""
    km = _mock_k8s_dispatch([])
    root = Path("/opt/data/profiles/p1")
    result = await wf.write_upload(km, "pod-1", root, "new.txt", b"hi")
    assert result["filename"] == "new.txt"
    assert result["path"] == "uploads/new.txt"


# ── read_file_bytes：全文件下载（无截断）──


def _mock_k8s_binary(raw: bytes | None = None, exc: Exception | None = None) -> MagicMock:
    km = MagicMock()
    if exc is not None:
        km.exec_read_file_bytes = AsyncMock(side_effect=exc)
    else:
        km.exec_read_file_bytes = AsyncMock(return_value=raw)
    return km


@pytest.mark.asyncio
async def test_read_file_bytes_returns_full_bytes_and_mime():
    raw = b"PK\x03\x04fake-pptx-content"  # pptx 头
    km = _mock_k8s_binary(raw)
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_bytes(km, "pod-1", root, "output/report.pptx")
    assert result["bytes"] == raw
    assert result["size"] == len(raw)
    assert result["name"] == "report.pptx"
    assert result["mime"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.asyncio
async def test_read_file_bytes_too_large_returns_error():
    km = _mock_k8s_binary(exc=ValueError("file too large: 60000000"))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_bytes(km, "pod-1", root, "big.zip")
    assert result["error"] == "too large"
    assert result["size"] == 60_000_000


@pytest.mark.asyncio
async def test_read_file_bytes_not_found():
    km = _mock_k8s_binary(exc=FileNotFoundError("/opt/data/profiles/p1/missing.pdf"))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_bytes(km, "pod-1", root, "missing.pdf")
    assert result["error"] == "not found"


@pytest.mark.asyncio
async def test_read_file_bytes_fallback_to_output_subdir():
    """agent emit 裸文件名（漏 output/ 前缀）：直路径 not found → 按 output/<name> 兜底命中。

    Regression: agent 回复里 [name](南京.pdf) 但文件在 output/南京.pdf，直路径 404。
    """
    raw = b"%PDF-1.4 fake pdf body"

    async def _read(pod_name, abs_path, max_bytes):
        # 裸名直路径 → not found；output/<name> → 命中
        if "output" not in abs_path:
            raise FileNotFoundError(abs_path)
        return raw

    km = MagicMock()
    km.exec_read_file_bytes = AsyncMock(side_effect=_read)
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_bytes(km, "pod-1", root, "南京两日游攻略.pdf")
    assert result.get("bytes") == raw
    assert result["name"] == "南京两日游攻略.pdf"
    assert result["mime"].startswith("application/pdf")
    # 直路径 1 次 + output/ 兜底 1 次
    assert km.exec_read_file_bytes.await_count == 2
    second_path = km.exec_read_file_bytes.await_args_list[1].args[1]
    assert "output/南京两日游攻略.pdf" in second_path


@pytest.mark.asyncio
async def test_read_file_bytes_no_fallback_for_path_with_separator():
    """带路径分隔的 rel 不做 fallback（避免对 output/a.pdf 这种漏前缀盲目重试 output/output/a.pdf）。"""
    km = _mock_k8s_binary(exc=FileNotFoundError("/opt/data/profiles/p1/sub/missing.pdf"))
    root = Path("/opt/data/profiles/p1")
    result = await wf.read_file_bytes(km, "pod-1", root, "sub/missing.pdf")
    assert result["error"] == "not found"
    assert km.exec_read_file_bytes.await_count == 1


@pytest.mark.asyncio
async def test_read_file_bytes_traversal_rejected():
    km = _mock_k8s_binary(raw=b"")
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        await wf.read_file_bytes(km, "pod-1", root, "../../etc/passwd")


def test_guess_download_mime_known_and_unknown():
    assert wf._guess_download_mime("a.pdf").startswith("application/pdf")
    assert wf._guess_download_mime("a.xlsx").startswith("application/vnd")
    assert wf._guess_download_mime("a.png") == "image/png"
    assert wf._guess_download_mime("a.unknownext") == "application/octet-stream"


# ── sniff_mime：magic-byte 图片类型嗅探 ──


def test_sniff_mime_png_jpeg_gif_webp_bmp():
    assert wf.sniff_mime(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == "image/png"
    assert wf.sniff_mime(b"\xff\xd8\xff\xe0" + b"\x00" * 8) == "image/jpeg"
    assert wf.sniff_mime(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert wf.sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert wf.sniff_mime(b"BM" + b"\x00" * 8) == "image/bmp"


def test_sniff_mime_non_image_returns_none():
    assert wf.sniff_mime(b"plain text content") is None
    assert wf.sniff_mime(b"%PDF-1.4") is None
    assert wf.sniff_mime(b"") is None


@pytest.mark.asyncio
async def test_write_upload_sniffs_real_mime_overriding_extension():
    """PNG 字节流 + 假 .txt 扩展名 → is_image=True，mime=image/png（嗅探优先于扩展名）。"""
    km = _mock_k8s_dispatch([])
    root = Path("/opt/data/profiles/p1")
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    result = await wf.write_upload(km, "pod-1", root, "fake.txt", png_bytes)
    assert result["is_image"] is True
    assert result["mime"] == "image/png"
    # 文件名保留原扩展名（只修正 mime/is_image 判定，不强制改名）
    assert result["filename"] == "fake.txt"


@pytest.mark.asyncio
async def test_write_upload_non_image_uses_extension_guess():
    """非图片字节流 → 退回扩展名猜测 mime，is_image=False。"""
    km = _mock_k8s_dispatch([])
    root = Path("/opt/data/profiles/p1")
    result = await wf.write_upload(km, "pod-1", root, "notes.txt", b"hello world")
    assert result["is_image"] is False
    assert result["mime"] == "text/plain"


# ── resolve_user_profile：真 DB 集成测试 ──────────────────────────────────
# 复现并验证修复：同组多用户各持 own profile 时，必须解析到当前用户自己的，
# 不能因 order by created_at desc 误取同组其他用户的 profile。

_WF_TABLES = [
    "agent_profiles",
    "agent_deployments",
    "agent_instance_channels",
    "agent_instances",
    "agent_versions",
    "agent_definitions",
    "resource_pools",
    "user_group_members",
    "user_groups",
]


@pytest_asyncio.fixture
async def wf_db():
    """真 DB session + 隔离 test user；teardown 清表。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"wf_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    yield session, user

    await session.execute(text("UPDATE agent_definitions SET current_version_id = NULL"))
    for t in _WF_TABLES:
        await session.execute(text(f"DELETE FROM {t}"))
    await session.delete(user)
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest.fixture
def wf_mock_litellm(monkeypatch):
    async def _ensure_team(*a, **kw):
        return {}

    async def _generate_key(*a, **kw):
        return {"key": "sk-test-key", "token_id": "tid-123"}

    monkeypatch.setattr(litellm_client, "ensure_team", _ensure_team)
    monkeypatch.setattr(litellm_client, "generate_key", _generate_key)


@pytest.fixture
def wf_not_admin(monkeypatch):
    """绕过 is_platform_admin（其内部访问 user.roles 懒加载，async 下需 greenlet）。
    测试聚焦 profile 解析逻辑，走组成员鉴权路径，非 admin 旁路。"""
    from app.core import auth as _auth
    monkeypatch.setattr(_auth, "is_platform_admin", lambda _u: False)


@pytest_asyncio.fixture
async def wf_group(wf_db):
    session, _ = wf_db
    g = UserGroup(name=f"g_{uuid.uuid4().hex[:8]}", code=f"c{uuid.uuid4().hex[:8]}")
    session.add(g)
    await session.flush()
    g.litellm_team_id = str(g.id)
    await session.commit()
    await session.refresh(g)
    return g


@pytest_asyncio.fixture
async def wf_pool(wf_db):
    session, user = wf_db
    pool = ResourcePool(name="标准池", created_by=user.id)
    session.add(pool)
    await session.commit()
    await session.refresh(pool)
    return pool


async def _add_group_member(session, user_id, group_id):
    await session.execute(user_group_members.insert().values(user_id=user_id, group_id=group_id))
    await session.commit()


async def _make_published_instance(session, user, group, resource_pool):
    """造一个 PUBLISHED 实例 + RUNNING deployment（带 pod_name），返回 (instance, deployment)。"""
    d = await definition_service.create_definition(
        session,
        AgentDefinitionCreate(
            name=f"助手-{uuid.uuid4().hex[:6]}",
            group_id=group.id,
            model_settings={"litellm": {"model_group": "gpt-4o"}},
        ),
        user.id,
    )
    d, _v = await definition_service.publish_definition(
        session, d.id, PublishVersionRequest(), user.id
    )
    inst = await instance_service.create_instance(
        session,
        AgentInstanceCreate(
            name=f"inst-{uuid.uuid4().hex[:6]}",
            definition_id=d.id,
            resource_pool_id=resource_pool.id,
            group_id=group.id,
        ),
        user.id,
    )
    inst.status = AgentStatus.PUBLISHED
    await session.commit()
    await session.refresh(inst)
    dep = AgentDeployment(
        instance_id=inst.id,
        group_id=inst.group_id,
        resource_pool_id=resource_pool.id,
        status=DeploymentStatus.RUNNING,
        scope_type="ALL",
        pod_name=f"engine-{str(inst.id)[:8]}",
        deployed_at=datetime.now(UTC),
    )
    session.add(dep)
    await session.commit()
    await session.refresh(dep)
    return inst, dep


def _make_profile(
    instance, deployment, group, *, user_id, profile_name, created_at, is_active=True,
):
    return AgentProfile(
        instance_id=instance.id,
        resource_pool_id=instance.resource_pool_id,
        deployment_id=deployment.id,
        profile_name=profile_name,
        profile_type="INDEPENDENT",
        user_id=user_id,
        group_id=group.id,
        hermes_home=f"/opt/data/profiles/{profile_name}",
        internal_port=8642,
        is_active=is_active,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_resolve_returns_current_user_profile_even_if_others_newer(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin
):
    """同组两个用户各一条 profile，另一用户 created_at 更新 —— 必须返回当前用户的。"""
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    # 同组第二个用户
    user_b = User(
        username=f"wf_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user_b)
    await session.commit()
    await session.refresh(user_b)
    await _add_group_member(session, user_b.id, wf_group.id)

    inst, dep = await _make_published_instance(session, user_a, wf_group, wf_pool)

    now = datetime.now(UTC)
    profile_a = _make_profile(
        inst, dep, wf_group,
        user_id=user_a.id, profile_name=f"{str(inst.id)[:8]}-a",
        created_at=now - timedelta(hours=1),
    )
    profile_b = _make_profile(
        inst, dep, wf_group,
        user_id=user_b.id, profile_name=f"{str(inst.id)[:8]}-b",
        created_at=now,
    )
    session.add_all([profile_a, profile_b])
    await session.commit()

    resolved = await wf.resolve_user_profile(session, inst.id, user_a)
    assert resolved is not None
    prof, _dep, _inst = resolved
    assert prof.user_id == user_a.id
    assert prof.hermes_home == profile_a.hermes_home
    assert prof.id == profile_a.id


@pytest.mark.asyncio
async def test_resolve_no_fallback_to_ownerless_profile(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin
):
    """SHARED 已下线：当前用户无 own profile 时，即使组内存在 user_id=None 的遗留
    profile 也不退回它——恒按 user_id 精确匹配，找不到返回 None。"""
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    inst, dep = await _make_published_instance(session, user_a, wf_group, wf_pool)

    ownerless = AgentProfile(
        instance_id=inst.id,
        resource_pool_id=inst.resource_pool_id,
        deployment_id=dep.id,
        profile_name=f"{str(inst.id)[:8]}-ownerless",
        profile_type="INDEPENDENT",
        user_id=None,
        group_id=wf_group.id,
        hermes_home=f"/opt/data/profiles/{str(inst.id)[:8]}-ownerless",
        internal_port=8642,
        is_active=True,
        created_at=datetime.now(UTC),
    )
    session.add(ownerless)
    await session.commit()

    resolved = await wf.resolve_user_profile(session, inst.id, user_a)
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_profile(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin,
):
    """当前用户无 profile —— 返回 None。"""
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    inst, dep = await _make_published_instance(session, user_a, wf_group, wf_pool)
    # 不创建任何 profile
    resolved = await wf.resolve_user_profile(session, inst.id, user_a)
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_inactive_profile_skipped(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin,
):
    """当前用户的 profile is_active=False —— 不返回它，走兜底（无兜底则 None）。"""
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    inst, dep = await _make_published_instance(session, user_a, wf_group, wf_pool)

    inactive = _make_profile(
        inst, dep, wf_group,
        user_id=user_a.id, profile_name=f"{str(inst.id)[:8]}-a",
        created_at=datetime.now(UTC), is_active=False,
    )
    session.add(inactive)
    await session.commit()

    resolved = await wf.resolve_user_profile(session, inst.id, user_a)
    assert resolved is None


# ── resolve_instance_profile（gateway 内部令牌路径，按 instance_id 解析）──


@pytest.mark.asyncio
async def test_resolve_instance_profile_falls_back_to_most_recent(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin,
):
    """内部路径取最近创建的活跃 profile（不同用户各一条，按 created_at desc 取最新）。"""
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    user_b = User(
        username=f"wf_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@test.local",
        hashed_password="x",
        is_active=True,
    )
    session.add(user_b)
    await session.commit()
    await session.refresh(user_b)
    await _add_group_member(session, user_b.id, wf_group.id)
    inst, dep = await _make_published_instance(session, user_a, wf_group, wf_pool)

    now = datetime.now(UTC)
    older = _make_profile(
        inst, dep, wf_group,
        user_id=user_a.id, profile_name=f"{str(inst.id)[:8]}-old",
        created_at=now - timedelta(hours=1),
    )
    newer = _make_profile(
        inst, dep, wf_group,
        user_id=user_b.id, profile_name=f"{str(inst.id)[:8]}-new",
        created_at=now,
    )
    session.add_all([older, newer])
    await session.commit()

    resolved = await wf.resolve_instance_profile(session, inst.id)
    assert resolved is not None
    prof, _dep, _inst = resolved
    assert prof.id == newer.id


@pytest.mark.asyncio
async def test_resolve_instance_profile_none_when_no_profile(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin,
):
    """无任何活跃 profile → None。"""
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    inst, _dep = await _make_published_instance(session, user_a, wf_group, wf_pool)
    resolved = await wf.resolve_instance_profile(session, inst.id)
    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_allows_deployment_with_null_pod_name(
    wf_db, wf_group, wf_pool, wf_mock_litellm, wf_not_admin,
):
    """deployment.pod_name=None（V3 架构下 Pod 名按 label 动态查询，pod_name 字段废弃）
    不应阻塞 profile 解析 —— Pod 存在性由 _resolve_workspace_pod 负责。
    回归：admin 在 Hermes 实例工作区为空（403 "无可访问的 profile"）的根因。
    """
    session, user_a = wf_db
    await _add_group_member(session, user_a.id, wf_group.id)
    inst, dep = await _make_published_instance(session, user_a, wf_group, wf_pool)
    # 把 pod_name 设为 None（模拟 V3 部署的 deployment 行）
    dep.pod_name = None
    await session.commit()
    await session.refresh(dep)

    profile = _make_profile(
        inst, dep, wf_group,
        user_id=user_a.id, profile_name=f"{str(inst.id)[:8]}-a",
        created_at=datetime.now(UTC),
    )
    session.add(profile)
    await session.commit()

    resolved = await wf.resolve_user_profile(session, inst.id, user_a)
    assert resolved is not None, "pod_name=None 不应阻塞 resolve_user_profile"
    prof, deployment, _inst = resolved
    assert prof.id == profile.id
    assert deployment.pod_name is None  # 验证确实是 None 状态


# ── 文件管理（增删改）mock exec 测试 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_create_folder_ok():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.create_folder(km, "pod-1", root, ".", "newdir")
    assert result.get("ok") is True
    call_cmd = km.exec_command_in_pod.call_args[0][1][0]
    assert "os.makedirs" in call_cmd
    assert "/opt/data/profiles/p1/newdir" in call_cmd


@pytest.mark.asyncio
async def test_create_folder_already_exists():
    km = _mock_k8s(json.dumps({"error": "already exists"}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.create_folder(km, "pod-1", root, ".", "newdir")
    assert result.get("error") == "already exists"


@pytest.mark.asyncio
async def test_create_folder_rejects_sensitive_name():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.create_folder(km, "pod-1", root, ".", ".env")
    assert result.get("error") == "sensitive name"
    km.exec_command_in_pod.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_folder_rejects_traversal():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.create_folder(km, "pod-1", root, ".", "foo/../../../../etc")
    assert result.get("error") == "path escapes workspace"


@pytest.mark.asyncio
async def test_delete_entry_ok():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.delete_entry(km, "pod-1", root, "readme.md")
    assert result.get("ok") is True
    call_cmd = km.exec_command_in_pod.call_args[0][1][0]
    assert "os.remove" in call_cmd or "shutil.rmtree" in call_cmd
    assert "/opt/data/profiles/p1/readme.md" in call_cmd


@pytest.mark.asyncio
async def test_delete_entry_not_found():
    km = _mock_k8s(json.dumps({"error": "not found"}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.delete_entry(km, "pod-1", root, "missing.txt")
    assert result.get("error") == "not found"


@pytest.mark.asyncio
async def test_delete_entry_rejects_sensitive():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        await wf.delete_entry(km, "pod-1", root, "config.yaml")


@pytest.mark.asyncio
async def test_move_entry_ok():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.move_entry(km, "pod-1", root, "old.txt", "new.txt")
    assert result.get("ok") is True
    call_cmd = km.exec_command_in_pod.call_args[0][1][0]
    assert "os.rename" in call_cmd
    assert "/opt/data/profiles/p1/old.txt" in call_cmd
    assert "/opt/data/profiles/p1/new.txt" in call_cmd


@pytest.mark.asyncio
async def test_move_entry_destination_already_exists():
    km = _mock_k8s(json.dumps({"error": "destination already exists"}))
    root = Path("/opt/data/profiles/p1")
    result = await wf.move_entry(km, "pod-1", root, "old.txt", "existing.txt")
    assert result.get("error") == "destination already exists"


@pytest.mark.asyncio
async def test_move_entry_rejects_traversal():
    km = _mock_k8s(json.dumps({"ok": True}))
    root = Path("/opt/data/profiles/p1")
    with pytest.raises(ValueError):
        await wf.move_entry(km, "pod-1", root, "../escape.txt", "safe.txt")

