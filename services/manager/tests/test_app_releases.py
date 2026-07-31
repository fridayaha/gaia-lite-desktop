"""APP 发布 API 集成测试 — 真 DB 验证 + ApkPatcher 单元测试。

覆盖：
- POST /api/manager/app-releases 上传 base APK + 创建 draft 记录 + DB 落库
- POST 同 version 冲突 → 409
- POST 文件名缺 version 后缀 → 400
- PATCH /{id} 编辑 display_name / description + DB 落库
- POST /{id}/publish 触发 ApkPatcher.patch + DB status=published
- GET /api/manager/public/app-releases/latest 返回 published 记录
- 未发布时 public latest 返回 null
- GET /api/manager/public/app-releases/{id}/apk 返回 patched APK bytes
- ApkPatcher._replace_server_config 纯 zip 操作（不调 subprocess）
"""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user, hash_password
from app.models import AppRelease, AppReleaseStatus, User
from app.services.apk_patcher import ApkPatcher, _is_v1_signature_file
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test data。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    # 清空表
    await session.execute(text("DELETE FROM app_releases"))
    await session.commit()

    yield session

    await session.execute(text("DELETE FROM app_releases"))
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(db):
    """建一个 admin user（直接 DB 写入）。"""
    user = User(
        username=f"admin_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="管理员",
        hashed_password=hash_password("AdminPass123"),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    yield user
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await db.commit()


@pytest_asyncio.fixture
async def client_as_admin(db, admin_user, monkeypatch):
    """admin 用户视角 + MinIO mock + ApkPatcher mock。"""
    from app.api import app_releases as app_releases_module
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    # mock archiver.client（避免 MinIO 连接）
    mock_minio = _make_mock_minio()
    monkeypatch.setattr(
        "app.services.app_release_storage.archiver.client", mock_minio
    )

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: admin_user
    # bypass is_platform_admin（admin_user 没真实加载 roles/permissions，避免 greenlet 漂移）
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: True)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
def public_client(db, monkeypatch):
    """public 端点视角（无 auth）。"""
    from app.main import app
    from pkg.common.database import get_db

    mock_minio = _make_mock_minio(apk_bytes=b"fake-patched-bytes")
    monkeypatch.setattr(
        "app.services.app_release_storage.archiver.client", mock_minio
    )

    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


def _make_mock_minio(apk_bytes: bytes = b"fake-apk-bytes"):
    """构造一个会按上传 length 反馈 stat.size + 按 object key 回放上传内容的 MinIO mock。"""
    mock_minio = MagicMock()
    mock_minio.bucket_exists.return_value = True

    # 跟踪每个 object key 的 size + content，stat.size 反馈 length，get_object 反馈 content
    _store: dict[str, tuple[int, bytes]] = {}

    def _put(bucket_name, object_name, data, length, content_type, **kw):
        # data 可能是 io.BytesIO（_put_private 用 BytesIO 包裹）或 bytes
        if hasattr(data, "read"):
            data_bytes = data.read()
        else:
            data_bytes = data
        _store[object_name] = (length, data_bytes)
        return MagicMock(object_name=object_name, size=length)

    def _stat(bucket, object_name):
        size, _ = _store.get(object_name, (0, None))
        return MagicMock(size=size)

    def _get_object(bucket, object_name, offset=0, length=0, **kw):
        _, content = _store.get(object_name, (0, apk_bytes))
        # 模拟 minio get_object 的 offset/length 语义（length=0 到末尾）
        if offset or length:
            sliced = content[offset : offset + length if length else None]
        else:
            sliced = content
        resp = MagicMock()
        resp.read.return_value = sliced
        resp.close.return_value = None
        resp.release_conn.return_value = None
        # stream_apk 用 next(response.stream(chunk_size), None) 迭代；mock 必须在末尾抛 StopIteration
        def _stream_iter(chunk_size):
            if not sliced:
                return
            for i in range(0, len(sliced), chunk_size):
                yield sliced[i : i + chunk_size]
        resp.stream.side_effect = lambda chunk_size=65536: _stream_iter(chunk_size)
        return resp

    mock_minio.put_object.side_effect = _put
    mock_minio.stat_object.side_effect = _stat
    mock_minio.get_object.side_effect = _get_object
    return mock_minio


@pytest.fixture
def fake_apk_bytes():
    """构造一个最小可用 APK zip（含 assets/server_config.json 占位符 + AndroidManifest.xml + 资源）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "assets/server_config.json",
            json.dumps({"manager_url": "__UA_MANAGER_URL__", "gateway_url": "__UA_GATEWAY_URL__"}),
        )
        z.writestr("AndroidManifest.xml", b"binary-axml-placeholder".decode("utf-8", errors="ignore"))
        z.writestr("resources.arsc", "x" * 1024, compress_type=zipfile.ZIP_STORED)
        z.writestr("classes.dex", "y" * 512)
        # v1 签名文件（patcher 应跳过）
        z.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        z.writestr("META-INF/CERT.SF", "Signature-Version: 1.0\n")
        z.writestr("META-INF/CERT.RSA", "mock-rsa-block")
        # META-INF 非 signature 文件（应保留）
        z.writestr("META-INF/services/java.lang.Object", "x")
    return buf.getvalue()


# ── ApkPatcher 单元测试（无 subprocess）───────────────────────────


def test_is_v1_signature_file():
    assert _is_v1_signature_file("META-INF/MANIFEST.MF")
    assert _is_v1_signature_file("META-INF/CERT.SF")
    assert _is_v1_signature_file("META-INF/CERT.RSA")
    assert _is_v1_signature_file("META-INF/KEY.DSA")
    assert _is_v1_signature_file("META-INF/ALIAS.EC")
    # 非 signature 文件不跳过
    assert not _is_v1_signature_file("META-INF/services/java.lang.Object")
    assert not _is_v1_signature_file("assets/server_config.json")
    assert not _is_v1_signature_file("AndroidManifest.xml")


def test_replace_server_config_replaces_placeholder_and_skips_v1_signatures(fake_apk_bytes):
    """验证 patcher 替换 assets/server_config.json + 跳过 v1 签名文件。不调 subprocess。"""
    patcher = ApkPatcher(
        keystore_path="/fake.keystore",
        keystore_alias="alias",
        keystore_password="pw",
        key_password="pw",
    )
    replaced = patcher._replace_server_config(
        fake_apk_bytes,
        manager_url="http://ecs.example.com/api/manager/",
        gateway_url="http://ecs.example.com/api/gateway/",
    )
    # 验证新内容
    with zipfile.ZipFile(io.BytesIO(replaced), "r") as z:
        cfg = json.loads(z.read("assets/server_config.json"))
        assert cfg["manager_url"] == "http://ecs.example.com/api/manager/"
        assert cfg["gateway_url"] == "http://ecs.example.com/api/gateway/"
        # v1 签名文件被跳过
        names = z.namelist()
        assert not any(n == "META-INF/MANIFEST.MF" for n in names)
        assert not any(n.endswith(".SF") or n.endswith(".RSA") for n in names)
        # 非 signature META-INF 文件保留
        assert "META-INF/services/java.lang.Object" in names
        # 其他 asset 保留
        assert "AndroidManifest.xml" in names
        assert "resources.arsc" in names
        # resources.arsc 仍 STORED（compress_type 保留）
        arsc_info = z.getinfo("resources.arsc")
        assert arsc_info.compress_type == zipfile.ZIP_STORED


# ── API 集成测试 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_empty_returns_200(client_as_admin):
    resp = await client_as_admin.get("/api/manager/app-releases")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "page": 1, "page_size": 20}


@pytest.mark.asyncio
async def test_upload_creates_draft_and_calls_minio(client_as_admin, db, fake_apk_bytes):
    """上传 base APK → 创建 draft 记录 + MinIO put_object 被调。"""
    files = {"file": ("知行-0.8.123.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == "0.8.123"
    assert body["status"] == "draft"
    assert body["display_name"] == "知行"

    # DB 落库
    stmt = select(AppRelease).where(AppRelease.version == "0.8.123")
    r = (await db.execute(stmt)).scalar_one()
    assert r.base_apk_object_key.startswith("app-releases/base/")
    assert r.status == AppReleaseStatus.DRAFT.value


@pytest.mark.asyncio
async def test_upload_extracts_icon_and_stores_in_public_bucket(
    client_as_admin, db, fake_apk_bytes, monkeypatch
):
    """上传 APK → extractor 提取图标 → put_icon 被调 + DB icon_object_key 有值。"""
    from app.services import apk_icon_extractor
    from app.services.apk_icon_extractor import ExtractedIcon

    fake_icon = ExtractedIcon(content=b"\x89PNG fake-icon-bytes", content_type="image/png")

    async def _fake_extract(apk_bytes, aapt_bin=apk_icon_extractor.AAPT_BIN):
        return fake_icon

    monkeypatch.setattr(apk_icon_extractor, "extract_icon", _fake_extract)

    files = {"file": ("知行-0.8.150.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201

    # response 含 icon_url
    body = resp.json()
    assert body["icon_url"] is not None
    assert body["icon_url"].startswith("/avatars/")

    # DB 落库
    stmt = select(AppRelease).where(AppRelease.version == "0.8.150")
    r = (await db.execute(stmt)).scalar_one()
    assert r.icon_object_key is not None
    assert r.icon_object_key.startswith("app-icons/")


@pytest.mark.asyncio
async def test_upload_succeeds_when_icon_extraction_returns_none(
    client_as_admin, db, fake_apk_bytes, monkeypatch
):
    """extractor 返回 None（APK 无图标 / aapt 不可用）→ 上传仍成功，icon_object_key 为 None。"""
    from app.services import apk_icon_extractor

    async def _fake_extract(apk_bytes, aapt_bin=apk_icon_extractor.AAPT_BIN):
        return None

    monkeypatch.setattr(apk_icon_extractor, "extract_icon", _fake_extract)

    files = {"file": ("知行-0.8.151.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201
    assert resp.json()["icon_url"] is None

    stmt = select(AppRelease).where(AppRelease.version == "0.8.151")
    r = (await db.execute(stmt)).scalar_one()
    assert r.icon_object_key is None


@pytest.mark.asyncio
async def test_upload_succeeds_when_icon_extraction_raises(
    client_as_admin, db, fake_apk_bytes, monkeypatch
):
    """extractor 抛异常 → 上传仍成功（_extract_and_store_icon 内部 try/except 兜底）。"""
    from app.services import apk_icon_extractor

    async def _exploding_extract(apk_bytes, aapt_bin=apk_icon_extractor.AAPT_BIN):
        raise RuntimeError("boom")

    monkeypatch.setattr(apk_icon_extractor, "extract_icon", _exploding_extract)

    files = {"file": ("知行-0.8.152.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201
    assert resp.json()["icon_url"] is None


@pytest.mark.asyncio
async def test_upload_duplicate_version_returns_409(client_as_admin, db, fake_apk_bytes):
    files = {"file": ("知行-0.8.123.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp1 = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp1.status_code == 201

    files2 = {"file": ("知行-template-0.8.123.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp2 = await client_as_admin.post("/api/manager/app-releases", files=files2)
    assert resp2.status_code == 409
    assert resp2.json()["detail"] == "version_already_exists"


@pytest.mark.asyncio
async def test_upload_missing_version_pattern_returns_400(client_as_admin, fake_apk_bytes):
    files = {"file": ("知行.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 400
    assert "version_pattern" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_modifies_name_and_description(client_as_admin, db, fake_apk_bytes):
    # 先上传
    files = {"file": ("知行-0.8.124.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    # 编辑
    resp2 = await client_as_admin.patch(
        f"/api/manager/app-releases/{release_id}",
        json={"display_name": "知行 Pro", "description": "新的描述"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["display_name"] == "知行 Pro"
    assert resp2.json()["description"] == "新的描述"

    # DB 落库
    stmt = select(AppRelease).where(AppRelease.id == release_id)
    r = (await db.execute(stmt)).scalar_one()
    assert r.display_name == "知行 Pro"
    assert r.description == "新的描述"


@pytest.mark.asyncio
async def test_publish_triggers_patch_and_marks_published(client_as_admin, db, fake_apk_bytes, monkeypatch):
    # 先上传
    files = {"file": ("知行-0.8.125.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    # mock ApkPatcher.patch 避免真实 zipalign/apksigner
    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        # 真的跑一次 _replace_server_config（验证 zip 操作）
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)
    # 重新构造 patcher（_get_patcher 内部 new，patch 方法被 monkeypatch 到 class）

    resp2 = await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={
            "manager_url": "http://ecs.example.com/api/manager/",
            "gateway_url": "http://ecs.example.com/api/gateway/",
        },
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["status"] == "published"
    assert body["manager_url"] == "http://ecs.example.com/api/manager/"
    assert body["gateway_url"] == "http://ecs.example.com/api/gateway/"
    assert body["published_at"] is not None

    # DB 落库
    stmt = select(AppRelease).where(AppRelease.id == release_id)
    r = (await db.execute(stmt)).scalar_one()
    assert r.status == AppReleaseStatus.PUBLISHED.value
    assert r.patched_apk_object_key is not None
    assert r.patched_apk_object_key.startswith("app-releases/patched/")


@pytest.mark.asyncio
async def test_public_latest_returns_null_when_no_published(public_client):
    resp = await public_client.get("/api/manager/public/app-releases/latest")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_public_latest_returns_published_record(public_client, db, fake_apk_bytes, client_as_admin, monkeypatch):
    # 上传 + 发布（admin client）
    files = {"file": ("知行-0.8.126.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={
            "manager_url": "http://ecs.example.com/api/manager/",
            "gateway_url": "http://ecs.example.com/api/gateway/",
        },
    )

    # public latest 应返回该记录
    resp2 = await public_client.get("/api/manager/public/app-releases/latest")
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["version"] == "0.8.126"
    assert body["display_name"] == "知行"
    # size 字段必须存在（值由 MinIO stat 决定；mock 环境下可能是 0 但键不能缺）
    assert "size" in body
    assert body["size"] is None or isinstance(body["size"], int)


@pytest.mark.asyncio
async def test_public_latest_size_reflects_stat_result(public_client, db, fake_apk_bytes, client_as_admin, monkeypatch):
    """latest.size 直接来自 stat_apk_size()，stat 返回什么 endpoint 就吐什么。"""
    files = {"file": ("知行-0.8.130.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={"manager_url": "http://x/api/manager/", "gateway_url": "http://x/api/gateway/"},
    )

    # stat 返回特定 size → endpoint 原样返回
    async def _fake_stat_size(object_key):
        return 19_980_123

    monkeypatch.setattr(
        "app.api.app_releases.app_release_storage.stat_apk_size", _fake_stat_size
    )

    resp2 = await public_client.get("/api/manager/public/app-releases/latest")
    body = resp2.json()
    assert body["size"] == 19_980_123


@pytest.mark.asyncio
async def test_public_latest_size_null_when_stat_fails(public_client, db, fake_apk_bytes, client_as_admin, monkeypatch):
    """stat 异常（对象丢失 / MinIO 故障）→ size=null，不阻断 latest 接口。"""
    files = {"file": ("知行-0.8.131.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={"manager_url": "http://x/api/manager/", "gateway_url": "http://x/api/gateway/"},
    )

    async def _failing_stat(object_key):
        return None

    monkeypatch.setattr(
        "app.api.app_releases.app_release_storage.stat_apk_size", _failing_stat
    )

    resp2 = await public_client.get("/api/manager/public/app-releases/latest")
    body = resp2.json()
    assert body["size"] is None


@pytest.mark.asyncio
async def test_public_download_returns_patched_bytes(public_client, db, fake_apk_bytes, client_as_admin, monkeypatch):
    # 上传 + 发布
    files = {"file": ("知行-0.8.127.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={
            "manager_url": "http://ecs.example.com/api/manager/",
            "gateway_url": "http://ecs.example.com/api/gateway/",
        },
    )

    # 下载
    resp2 = await public_client.get(f"/api/manager/public/app-releases/{release_id}/apk")
    assert resp2.status_code == 200
    assert resp2.headers["content-type"] == "application/vnd.android.package-archive"
    assert "attachment" in resp2.headers["content-disposition"]
    assert "filename*=UTF-8''" in resp2.headers["content-disposition"]


@pytest.mark.asyncio
async def test_public_download_streams_full_bytes_with_content_length(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    """StreamingResponse 端点：响应头带 Content-Length，body 字节数 = stat 报告的 size。"""
    files = {"file": ("知行-0.8.151.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={
            "manager_url": "http://ecs.example.com/api/manager/",
            "gateway_url": "http://ecs.example.com/api/gateway/",
        },
    )

    resp2 = await public_client.get(f"/api/manager/public/app-releases/{release_id}/apk")
    assert resp2.status_code == 200
    body = resp2.content
    # 响应头声明 Content-Length 且与实际字节数一致（streaming 端点也会带）
    assert "content-length" in resp2.headers
    assert int(resp2.headers["content-length"]) == len(body)
    # patched 内容应该是 fake_apk_bytes 经过 _replace_server_config 处理后的版本
    from app.services.apk_patcher import ApkPatcher as _AP
    patcher = _AP("/fake.keystore", "alias", "pw", "pw")
    expected = patcher._replace_server_config(
        fake_apk_bytes,
        "http://ecs.example.com/api/manager/",
        "http://ecs.example.com/api/gateway/",
    )
    assert body == expected


@pytest.mark.asyncio
async def test_public_download_unpublished_returns_404(public_client, db, fake_apk_bytes, client_as_admin):
    # 上传但不发布
    files = {"file": ("知行-0.8.128.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    release_id = resp.json()["id"]

    resp2 = await public_client.get(f"/api/manager/public/app-releases/{release_id}/apk")
    assert resp2.status_code == 404


# ── bootstrap_base_apks 测试（启动期扫描 base-apks/ 自动注册）───────────


@pytest.mark.asyncio
async def test_bootstrap_extracts_icon_from_apk(db, monkeypatch):
    """启动期 bootstrap 扫描 base-apks/ → 注册 draft → 自动提取图标。"""
    import tempfile
    from pathlib import Path

    from app.services import apk_icon_extractor, app_release_storage
    from app.services.apk_icon_extractor import ExtractedIcon
    from app.services.app_release_bootstrap import bootstrap_base_apks
    from pkg.common.config import settings

    monkeypatch.setattr(
        "app.services.app_release_storage.archiver.client", _make_mock_minio()
    )

    fake_icon = ExtractedIcon(content=b"\x89PNG fake-bootstrap-icon", content_type="image/png")

    async def _fake_extract(apk_bytes, aapt_bin=apk_icon_extractor.AAPT_BIN):
        return fake_icon

    monkeypatch.setattr(apk_icon_extractor, "extract_icon", _fake_extract)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_apk = Path(tmp_dir) / "知行-0.8.160.apk"
        tmp_apk.write_bytes(b"fake-apk-bytes")

        monkeypatch.setattr(settings, "apk_base_dir", str(tmp_dir))

        count = await bootstrap_base_apks(db)
        assert count == 1

        stmt = select(AppRelease).where(AppRelease.version == "0.8.160")
        r = (await db.execute(stmt)).scalar_one()
        assert r.base_apk_object_key.startswith("app-releases/base/")
        # icon_object_key 被提取并落库
        assert r.icon_object_key is not None
        assert r.icon_object_key.startswith("app-icons/")
        assert r.status == AppReleaseStatus.DRAFT.value


@pytest.mark.asyncio
async def test_bootstrap_succeeds_when_icon_extraction_fails(db, monkeypatch):
    """extractor 抛异常 → bootstrap 不阻断，icon_object_key 为 None。"""
    import tempfile
    from pathlib import Path

    from app.services import apk_icon_extractor
    from app.services.app_release_bootstrap import bootstrap_base_apks
    from pkg.common.config import settings

    monkeypatch.setattr(
        "app.services.app_release_storage.archiver.client", _make_mock_minio()
    )

    async def _exploding_extract(apk_bytes, aapt_bin=apk_icon_extractor.AAPT_BIN):
        raise RuntimeError("boom")

    monkeypatch.setattr(apk_icon_extractor, "extract_icon", _exploding_extract)

    with tempfile.TemporaryDirectory() as tmp_dir:
        (Path(tmp_dir) / "知行-0.8.161.apk").write_bytes(b"fake-apk")

        monkeypatch.setattr(settings, "apk_base_dir", str(tmp_dir))

        count = await bootstrap_base_apks(db)
        assert count == 1

        stmt = select(AppRelease).where(AppRelease.version == "0.8.161")
        r = (await db.execute(stmt)).scalar_one()
        assert r.icon_object_key is None
        assert r.base_apk_object_key.startswith("app-releases/base/")


@pytest.mark.asyncio
async def test_bootstrap_skips_already_registered_versions(db, monkeypatch, fake_apk_bytes):
    """目录里已有已注册的 version → 跳过（幂等）。"""
    import tempfile
    from pathlib import Path

    from app.services.app_release_bootstrap import bootstrap_base_apks
    from pkg.common.config import settings

    with tempfile.TemporaryDirectory() as tmp_dir:
        (Path(tmp_dir) / "知行-0.8.123.apk").write_bytes(fake_apk_bytes)

        monkeypatch.setattr(settings, "apk_base_dir", str(tmp_dir))

        # 0.8.123 在 test_upload_creates_draft 系列中可能已被注册，先确保存在
        stmt = select(AppRelease).where(
            AppRelease.platform == "android", AppRelease.version == "0.8.123"
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if not existing:
            from uuid import uuid4

            db.add(AppRelease(
                id=uuid4(),
                version="0.8.123",
                base_apk_object_key="app-releases/base/pre-existing.apk",
                display_name="知行",
                description="",
                status=AppReleaseStatus.DRAFT.value,
            ))
            await db.commit()

        count = await bootstrap_base_apks(db)
        assert count == 0  # 跳过已注册的


# ── platform（android / harmony）相关测试 ────────────────────────


@pytest.mark.asyncio
async def test_upload_hap_creates_harmony_draft_without_icon_extraction(
    client_as_admin, db, monkeypatch
):
    """上传 .hap → platform=harmony + 不触发 aapt 图标提取（icon_object_key 为 None）。"""
    from app.services import apk_icon_extractor

    async def _exploding_extract(apk_bytes, aapt_bin=apk_icon_extractor.AAPT_BIN):
        raise AssertionError("icon extraction must not run for .hap")

    monkeypatch.setattr(apk_icon_extractor, "extract_icon", _exploding_extract)

    files = {"file": ("知行-0.8.200.hap", b"fake-hap-bytes", "application/octet-stream")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201
    body = resp.json()
    assert body["platform"] == "harmony"
    assert body["version"] == "0.8.200"
    assert body["icon_url"] is None

    stmt = select(AppRelease).where(
        AppRelease.platform == "harmony", AppRelease.version == "0.8.200"
    )
    r = (await db.execute(stmt)).scalar_one()
    assert r.base_apk_object_key.endswith(".hap")
    assert r.icon_object_key is None


@pytest.mark.asyncio
async def test_upload_same_version_different_platform_allowed(client_as_admin, db, fake_apk_bytes):
    """同 version 可分别注册 android 与 harmony（复合唯一约束）。"""
    files_apk = {"file": ("知行-0.8.201.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp1 = await client_as_admin.post("/api/manager/app-releases", files=files_apk)
    assert resp1.status_code == 201
    assert resp1.json()["platform"] == "android"

    files_hap = {"file": ("知行-0.8.201.hap", b"fake-hap-bytes", "application/octet-stream")}
    resp2 = await client_as_admin.post("/api/manager/app-releases", files=files_hap)
    assert resp2.status_code == 201
    assert resp2.json()["platform"] == "harmony"

    # 但同平台同 version 仍 409
    files_hap2 = {"file": ("知行-template-0.8.201.hap", b"fake-hap-bytes", "application/octet-stream")}
    resp3 = await client_as_admin.post("/api/manager/app-releases", files=files_hap2)
    assert resp3.status_code == 409


@pytest.mark.asyncio
async def test_upload_explicit_platform_mismatch_returns_400(client_as_admin, fake_apk_bytes):
    """显式 platform 与扩展名不一致 → 400。"""
    files = {"file": ("知行-0.8.202.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post(
        "/api/manager/app-releases", files=files, data={"platform": "harmony"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "platform_mismatch_with_extension"


@pytest.mark.asyncio
async def test_upload_unknown_extension_returns_400(client_as_admin):
    files = {"file": ("知行-0.8.203.zip", b"fake", "application/zip")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "filename_must_end_with_apk_or_hap"


@pytest.mark.asyncio
async def test_list_pagination_and_platform_filter(client_as_admin, db, fake_apk_bytes):
    """分页：total 反映全量，items 按 page/page_size 切片；platform 筛选生效。"""
    # 造 3 条 android + 2 条 harmony
    for i in range(3):
        files = {"file": (f"知行-0.9.{10 + i}.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
        resp = await client_as_admin.post("/api/manager/app-releases", files=files)
        assert resp.status_code == 201
    for i in range(2):
        files = {"file": (f"知行-0.9.{20 + i}.hap", b"fake-hap", "application/octet-stream")}
        resp = await client_as_admin.post("/api/manager/app-releases", files=files)
        assert resp.status_code == 201

    # 第一页 page_size=2
    resp = await client_as_admin.get("/api/manager/app-releases?page=1&page_size=2")
    body = resp.json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 2
    assert len(body["items"]) == 2

    # 第三页只剩 1 条
    resp = await client_as_admin.get("/api/manager/app-releases?page=3&page_size=2")
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 1

    # platform 筛选
    resp = await client_as_admin.get("/api/manager/app-releases?platform=harmony")
    body = resp.json()
    assert body["total"] == 2
    assert all(item["platform"] == "harmony" for item in body["items"])

    resp = await client_as_admin.get("/api/manager/app-releases?platform=android")
    body = resp.json()
    assert body["total"] == 3
    assert all(item["platform"] == "android" for item in body["items"])

    # 非法 platform → 400
    resp = await client_as_admin.get("/api/manager/app-releases?platform=ios")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_publish_harmony_skips_patcher_and_stores_original_bytes(
    client_as_admin, db, monkeypatch
):
    """harmony publish：不调 ApkPatcher，patched 字节 == base 字节。"""
    hap_bytes = b"fake-signed-hap-bytes"
    files = {"file": ("知行-0.8.210.hap", hap_bytes, "application/octet-stream")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201
    release_id = resp.json()["id"]

    from app.api import app_releases as ar_module

    async def _exploding_patch(self, base_bytes, manager_url, gateway_url):
        raise AssertionError("ApkPatcher must not run for harmony")

    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _exploding_patch)

    resp2 = await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={"manager_url": "http://x/api/manager/", "gateway_url": "http://x/api/gateway/"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "published"

    stmt = select(AppRelease).where(AppRelease.id == release_id)
    r = (await db.execute(stmt)).scalar_one()
    assert r.patched_apk_object_key.endswith(".hap")

    # 下载应原样返回上传字节
    resp3 = await client_as_admin.get(f"/api/manager/public/app-releases/{release_id}/apk")
    assert resp3.status_code == 200
    assert resp3.content == hap_bytes
    assert resp3.headers["content-type"] == "application/octet-stream"
    assert "zhixing.hap" in resp3.headers["content-disposition"]


@pytest.mark.asyncio
async def test_public_latest_platform_isolation(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    """latest 按 platform 隔离：android/harmony 各有 published 时互不可见。"""
    # 发布 android 0.8.220
    files = {"file": ("知行-0.8.220.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    apk_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{apk_id}/publish",
        json={"manager_url": "http://x/api/manager/", "gateway_url": "http://x/api/gateway/"},
    )

    # 发布 harmony 0.8.221
    files = {"file": ("知行-0.8.221.hap", b"fake-hap", "application/octet-stream")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    hap_id = resp.json()["id"]
    await client_as_admin.post(
        f"/api/manager/app-releases/{hap_id}/publish",
        json={"manager_url": "http://x/api/manager/", "gateway_url": "http://x/api/gateway/"},
    )

    # 默认 platform=android → 只看见 apk
    resp = await public_client.get("/api/manager/public/app-releases/latest")
    body = resp.json()
    assert body["platform"] == "android"
    assert body["version"] == "0.8.220"

    # 显式 harmony → 只看见 hap（尽管它 published_at 更新）
    resp = await public_client.get("/api/manager/public/app-releases/latest?platform=harmony")
    body = resp.json()
    assert body["platform"] == "harmony"
    assert body["version"] == "0.8.221"

    # by-version 同样按 platform 隔离
    resp = await public_client.get("/api/manager/public/app-releases/by-version/0.8.220?platform=harmony")
    assert resp.json() is None
    resp = await public_client.get("/api/manager/public/app-releases/by-version/0.8.221?platform=harmony")
    assert resp.json()["version"] == "0.8.221"

    # 非法 platform → 400
    resp = await public_client.get("/api/manager/public/app-releases/latest?platform=ios")
    assert resp.status_code == 400


# ── Range 断点续传（Android DownloadManager 弱网恢复）────────────────


async def _publish_apk_release(client_as_admin, fake_apk_bytes, monkeypatch, version: str) -> str:
    """上传 + 发布一条 android 记录，返回 release_id。"""
    files = {"file": (f"知行-{version}.apk", fake_apk_bytes, "application/vnd.android.package-archive")}
    resp = await client_as_admin.post("/api/manager/app-releases", files=files)
    assert resp.status_code == 201
    release_id = resp.json()["id"]

    async def _fake_patch(self, base_bytes, manager_url, gateway_url):
        from app.services.apk_patcher import ApkPatcher as _AP
        patcher = _AP("/fake.keystore", "alias", "pw", "pw")
        return patcher._replace_server_config(base_bytes, manager_url, gateway_url)

    from app.api import app_releases as ar_module
    monkeypatch.setattr(ar_module.ApkPatcher, "patch", _fake_patch)

    await client_as_admin.post(
        f"/api/manager/app-releases/{release_id}/publish",
        json={"manager_url": "http://x/api/manager/", "gateway_url": "http://x/api/gateway/"},
    )
    return release_id


@pytest.mark.asyncio
async def test_download_full_response_advertises_accept_ranges(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    release_id = await _publish_apk_release(client_as_admin, fake_apk_bytes, monkeypatch, "0.8.230")
    resp = await public_client.get(f"/api/manager/public/app-releases/{release_id}/apk")
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_download_range_resume_returns_206_with_correct_slice(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    """DownloadManager 断网恢复场景：Range: bytes=N- → 206 + 剩余字节。"""
    release_id = await _publish_apk_release(client_as_admin, fake_apk_bytes, monkeypatch, "0.8.231")

    # 全量内容（patcher 处理后的预期字节）
    from app.services.apk_patcher import ApkPatcher as _AP
    patcher = _AP("/fake.keystore", "alias", "pw", "pw")
    full = patcher._replace_server_config(
        fake_apk_bytes, "http://x/api/manager/", "http://x/api/gateway/"
    )
    total = len(full)

    offset = total // 2
    resp = await public_client.get(
        f"/api/manager/public/app-releases/{release_id}/apk",
        headers={"Range": f"bytes={offset}-"},
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes {offset}-{total - 1}/{total}"
    assert int(resp.headers["content-length"]) == total - offset
    assert resp.content == full[offset:]


@pytest.mark.asyncio
async def test_download_range_with_explicit_end(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    release_id = await _publish_apk_release(client_as_admin, fake_apk_bytes, monkeypatch, "0.8.232")

    from app.services.apk_patcher import ApkPatcher as _AP
    patcher = _AP("/fake.keystore", "alias", "pw", "pw")
    full = patcher._replace_server_config(
        fake_apk_bytes, "http://x/api/manager/", "http://x/api/gateway/"
    )

    resp = await public_client.get(
        f"/api/manager/public/app-releases/{release_id}/apk",
        headers={"Range": "bytes=0-99"},
    )
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-99/{len(full)}"
    assert resp.content == full[:100]


@pytest.mark.asyncio
async def test_download_range_unsatisfiable_returns_416(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    release_id = await _publish_apk_release(client_as_admin, fake_apk_bytes, monkeypatch, "0.8.233")

    from app.services.apk_patcher import ApkPatcher as _AP
    patcher = _AP("/fake.keystore", "alias", "pw", "pw")
    full = patcher._replace_server_config(
        fake_apk_bytes, "http://x/api/manager/", "http://x/api/gateway/"
    )

    resp = await public_client.get(
        f"/api/manager/public/app-releases/{release_id}/apk",
        headers={"Range": f"bytes={len(full) + 100}-"},
    )
    assert resp.status_code == 416
    assert resp.headers["content-range"] == f"bytes */{len(full)}"


@pytest.mark.asyncio
async def test_download_multi_range_ignored_returns_200(
    public_client, db, fake_apk_bytes, client_as_admin, monkeypatch
):
    """多段 Range（极少客户端用）→ 忽略，按 200 全量返回（RFC 7233 允许）。"""
    release_id = await _publish_apk_release(client_as_admin, fake_apk_bytes, monkeypatch, "0.8.234")
    resp = await public_client.get(
        f"/api/manager/public/app-releases/{release_id}/apk",
        headers={"Range": "bytes=0-99,200-299"},
    )
    assert resp.status_code == 200


def test_parse_range_helper():
    from app.api.app_releases import _parse_range

    size = 1000
    assert _parse_range("bytes=0-99", size) == (0, 99)
    assert _parse_range("bytes=500-", size) == (500, 999)
    assert _parse_range("bytes=-100", size) == (900, 999)  # suffix 形式
    assert _parse_range("bytes=800-5000", size) == (800, 999)  # end 钳到 size-1
    assert _parse_range("bytes=1000-", size) is None  # start >= size → 416
    assert _parse_range("bytes=0-99,200-299", size) == (-1, -1)  # 多段 → 忽略
    assert _parse_range("items=0-99", size) == (-1, -1)  # 非 bytes 单位 → 忽略
    assert _parse_range("bytes=abc-def", size) == (-1, -1)  # 语法错误 → 忽略
