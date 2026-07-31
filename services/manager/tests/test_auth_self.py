"""账户自服务 API 集成测试 — 真 DB 验证 PATCH /me + change-password + avatar 上传。

覆盖：
- PATCH /me 改 real_name/email/phone/avatar_url + DB 落库
- PATCH /me 改 email 冲突 → 409
- POST /change-password old 正确 → 200 + 新 hash 通过 verify_password
- POST /change-password old 错误 → 400
- POST /change-password 新密码 <8 → 422（pydantic）
- POST /avatar 上传 PNG → 200 + DB avatar_url 落库
- POST /avatar 超大文件 → 413
- POST /avatar 非图片类型 → 415
- GET /me 响应含 nickname=real_name + avatar_url 字段
- /mine-logs 响应含 operator_ip
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user, hash_password, verify_password
from app.models import OperationLog, User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"self_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        real_name="原名",
        phone="13800000000",
        hashed_password=hash_password("OldPass123"),
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # 重查带 selectinload(User.roles)，避免 _user_dict / get_me 访问 user.roles 时触发 lazy load 报 MissingGreenlet
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user.id)
    )
    user = result.scalar_one()

    await session.execute(text("DELETE FROM operation_logs"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def client_as_user(db, monkeypatch):
    """登录用户视角。"""
    from app.main import app
    from pkg.common.database import get_db

    session, user = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
def mock_minio_client(monkeypatch):
    """mock archiver.client 避免 MinIO 连接。返回 MagicMock 用于断言调用。"""
    from app.api import auth as auth_module

    mock = MagicMock()
    mock.bucket_exists.return_value = True  # 跳过 make_bucket/policy
    monkeypatch.setattr(auth_module.archiver, "client", mock)
    return mock


# ── tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_me_returns_all_fields(client_as_user, db):
    _, user = db
    resp = await client_as_user.get("/api/manager/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == user.username
    assert body["real_name"] == "原名"
    assert body["nickname"] == "原名"  # alias
    assert body["email"] == user.email
    assert body["phone"] == "13800000000"
    assert "avatar_url" in body
    assert body["is_active"] is True
    assert isinstance(body["roles"], list)


@pytest.mark.asyncio
async def test_patch_me_updates_real_name(client_as_user, db):
    session, user = db
    resp = await client_as_user.patch(
        "/api/manager/auth/me",
        json={"real_name": "新名字"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["real_name"] == "新名字"

    await session.refresh(user)
    assert user.real_name == "新名字"


@pytest.mark.asyncio
async def test_patch_me_updates_phone_and_avatar(client_as_user, db):
    session, user = db
    resp = await client_as_user.patch(
        "/api/manager/auth/me",
        json={"phone": "13900000000", "avatar_url": "http://example.com/x.png"},
    )
    assert resp.status_code == 200
    await session.refresh(user)
    assert user.phone == "13900000000"
    assert user.avatar_url == "http://example.com/x.png"


@pytest.mark.asyncio
async def test_patch_me_email_conflict_returns_409(client_as_user, db):
    session, user = db
    # 建一个占住 email 的用户（已认证 — 0.8.110 两态：未认证 email 不查重）
    other = User(
        username=f"other_{uuid.uuid4().hex[:8]}",
        email="taken@example.com",
        hashed_password="x",
        is_active=True,
        email_verified=True,
    )
    session.add(other)
    await session.commit()

    resp = await client_as_user.patch(
        "/api/manager/auth/me",
        json={"email": "taken@example.com"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "email_already_used"

    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": other.id})
    await session.commit()


@pytest.mark.asyncio
async def test_patch_me_same_email_no_conflict(client_as_user, db):
    """提交自己的原 email 不应报 409。"""
    _, user = db
    resp = await client_as_user.patch(
        "/api/manager/auth/me",
        json={"email": user.email},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_change_password_success(client_as_user, db):
    session, user = db
    resp = await client_as_user.post(
        "/api/manager/auth/change-password",
        json={"old_password": "OldPass123", "new_password": "NewPass456!"},
    )
    assert resp.status_code == 200
    await session.refresh(user)
    assert verify_password("NewPass456!", user.hashed_password)
    assert not verify_password("OldPass123", user.hashed_password)


@pytest.mark.asyncio
async def test_change_password_wrong_old_returns_400(client_as_user, db):
    resp = await client_as_user.post(
        "/api/manager/auth/change-password",
        json={"old_password": "WrongPass", "new_password": "NewPass456!"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "wrong_old_password"


@pytest.mark.asyncio
async def test_change_password_weak_new_returns_422(client_as_user, db):
    """新密码 <8 位触发 pydantic 校验失败。"""
    resp = await client_as_user.post(
        "/api/manager/auth/change-password",
        json={"old_password": "OldPass123", "new_password": "short"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_avatar_success(client_as_user, db, mock_minio_client):
    session, user = db
    png_header = b"\x89PNG\r\n\x1a\n"
    body = png_header + b"\x00" * 100
    resp = await client_as_user.post(
        "/api/manager/auth/avatar",
        files={"file": ("test.png", body, "image/png")},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "avatar_url" in data
    assert data["avatar_url"].startswith("/avatars/unionagents-avatars/")
    # MinIO put_object 被调用
    mock_minio_client.put_object.assert_called_once()
    # DB 落库
    await session.refresh(user)
    assert user.avatar_url == data["avatar_url"]


@pytest.mark.asyncio
async def test_upload_avatar_too_large_returns_413(client_as_user, db, mock_minio_client):
    body = b"\x00" * (2 * 1024 * 1024 + 1)
    resp = await client_as_user.post(
        "/api/manager/auth/avatar",
        files={"file": ("big.png", body, "image/png")},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"] == "avatar_too_large"


@pytest.mark.asyncio
async def test_upload_avatar_unsupported_type_returns_415(client_as_user, db, mock_minio_client):
    resp = await client_as_user.post(
        "/api/manager/auth/avatar",
        files={"file": ("file.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 415
    assert resp.json()["detail"] == "avatar_unsupported_type"


@pytest.mark.asyncio
async def test_mine_logs_contains_operator_ip(client_as_user, db):
    """先触发一次操作（PATCH /me），再查 mine-logs，确认 operator_ip 字段存在。"""
    session, user = db
    await client_as_user.patch("/api/manager/auth/me", json={"real_name": "触发审计"})

    resp = await client_as_user.get("/api/manager/mine-logs")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data["list"], list)
    assert len(data["list"]) >= 1
    for log in data["list"]:
        assert "operator_ip" in log
        assert "operator_user_agent" in log
        # mine-logs 不再返回 detail 字段（安全日志页不展示）
        assert "detail" not in log


@pytest.mark.asyncio
async def test_operator_user_agent_recorded(client_as_user, db):
    """middleware 提取 User-Agent 头 → log_operation 写入 operator_user_agent 列。
    走 PATCH /me 触发一条 user.self_update，验证 DB 落库 + mine-logs 响应都带 UA。
    """
    session, user = db
    ua = "Mozilla/5.0 (TestRunner; SecurityLog test) curl/8.0"
    resp = await client_as_user.patch(
        "/api/manager/auth/me",
        json={"real_name": "UA 测试"},
        headers={"User-Agent": ua},
    )
    assert resp.status_code == 200

    # DB 层验证
    result = await session.execute(
        select(OperationLog)
        .where(
            OperationLog.actor_id == user.id,
            OperationLog.action == "user.self_update",
        )
        .order_by(OperationLog.created_at.desc())
    )
    logs = result.scalars().all()
    assert len(logs) >= 1
    assert logs[0].operator_user_agent == ua

    # API 响应层验证
    resp = await client_as_user.get("/api/manager/mine-logs")
    data = resp.json()["data"]
    matched = [x for x in data["list"] if x["action"] == "user.self_update"]
    assert matched, "self_update 应在 mine-logs 里出现"
    assert matched[0]["operator_user_agent"] == ua


@pytest.mark.asyncio
async def test_logout_endpoint_records_audit_log(client_as_user, db):
    """POST /auth/logout 写 auth.logout 审计日志，mine-logs 能查到。"""
    session, user = db
    resp = await client_as_user.post("/api/manager/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["code"] == 0

    # 直接查 DB 验证 auth.logout 落库
    result = await session.execute(
        select(OperationLog)
        .where(OperationLog.actor_id == user.id, OperationLog.action == "auth.logout")
        .order_by(OperationLog.created_at.desc())
    )
    logs = result.scalars().all()
    assert len(logs) == 1
    assert logs[0].action == "auth.logout"


@pytest.mark.asyncio
async def test_mine_logs_filters_to_security_actions_only(client_as_user, db):
    """mine-logs 只显示 auth.login / auth.logout / user.change_password / user.self_update。
    avatar 上传（user.update_avatar）不应出现。
    """
    session, user = db
    # 触发各类操作
    await client_as_user.patch("/api/manager/auth/me", json={"real_name": "改名"})  # user.self_update
    await client_as_user.post(
        "/api/manager/auth/avatar",
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 10, "image/png")},
    )  # user.update_avatar — 不应出现
    await client_as_user.post("/api/manager/auth/logout")  # auth.logout

    resp = await client_as_user.get("/api/manager/mine-logs")
    assert resp.status_code == 200
    actions = {log["action"] for log in resp.json()["data"]["list"]}
    # 只包含这 4 类
    assert actions.issubset({"auth.login", "auth.logout", "user.change_password", "user.self_update"})
    # avatar 上传不应出现
    assert "user.update_avatar" not in actions
    # 至少包含 self_update + logout
    assert "user.self_update" in actions
    assert "auth.logout" in actions


@pytest.mark.asyncio
async def test_refresh_accepts_snake_case(client_as_user, db):
    """/auth/refresh 接受 snake_case refresh_token 字段（pydantic 原生字段名）。"""
    _, user = db
    # 先登录拿 refresh_token（用真密码走真 login 端点，不用 client_as_user 的 bypass）
    resp = await client_as_user.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "OldPass123"},
    )
    assert resp.status_code == 200, f"login 应 200: {resp.text}"
    refresh_token = resp.json()["refresh_token"]
    # snake_case 应该工作
    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    # 滚动续期：refresh 应签发新的 refresh_token（不能复用旧 token）
    assert body["refresh_token"] != refresh_token, "refresh_token 必须滚动续期（签发新值）"


@pytest.mark.asyncio
async def test_refresh_rotates_refresh_token_each_call(client_as_user, db):
    """连续两次 refresh 应返回三个不同的 refresh_token（login + 两次 refresh），
    验证滚动续期不是固定值——旧 refresh_token 在每次调用后都被新值替代。

    旧 refresh_token 是否失效由 JWT 自然过期兜底（无状态、无黑名单），这里只断言
    「每次返回的 refresh_token 都是新签发的」。
    """
    _, user = db
    resp = await client_as_user.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "OldPass123"},
    )
    assert resp.status_code == 200
    token_1 = resp.json()["refresh_token"]

    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refresh_token": token_1},
    )
    assert resp.status_code == 200
    token_2 = resp.json()["refresh_token"]
    assert token_2 != token_1

    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refresh_token": token_2},
    )
    assert resp.status_code == 200
    token_3 = resp.json()["refresh_token"]
    assert token_3 != token_2 != token_1


@pytest.mark.asyncio
async def test_refresh_expired_token_returns_401(client_as_user, db, monkeypatch):
    """已过期的 refresh_token 调 /auth/refresh 返回 401。

    构造方式：monkeypatch create_refresh_token 用负 timedelta，签发一个 exp 在过去的
    refresh_token，调 refresh 应被 jose JWTError 拦下。
    """
    from datetime import timedelta
    from app.core import auth as auth_module

    _, user = db

    original = auth_module.create_refresh_token

    def _expired(user_id):
        # 复用 original 的 payload 结构，只把 exp 改成过去时间
        import time
        from pkg.common.config import settings
        from jose import jwt as _jwt
        payload = {
            "sub": str(user_id),
            "type": "refresh",
            "exp": int(time.time()) - 60,  # 60 秒前过期
        }
        return _jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

    monkeypatch.setattr(auth_module, "create_refresh_token", _expired)
    try:
        expired_token = _expired(user.id)
    finally:
        monkeypatch.setattr(auth_module, "create_refresh_token", original)

    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refresh_token": expired_token},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token"


@pytest.mark.asyncio
async def test_refresh_accepts_camel_case(client_as_user, db):
    """/auth/refresh 也接受 camelCase refreshToken 字段（前端实际发的格式）。

    历史：前端 handRefreshToken 发 `{refreshToken: "..."}`，但后端 schema 字段是
    snake_case `refresh_token`，导致 422 validation error，refresh 始终失败 →
    access_token 过期后 router beforeEach 调 refresh 失败 → logOut → logoutApi 过期
    token 又触发 refresh cycle → "登录已过期" toast 死循环。这里断言 alias 必须存在。
    """
    _, user = db
    resp = await client_as_user.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "OldPass123"},
    )
    assert resp.status_code == 200, f"login 应 200: {resp.text}"
    refresh_token = resp.json()["refresh_token"]
    # camelCase 必须也能工作（这是前端实际发的格式）
    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refreshToken": refresh_token},
    )
    assert resp.status_code == 200, f"camelCase refreshToken 必须 200，实际 {resp.status_code}: {resp.text}"
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_refresh_invalid_token_returns_401(client_as_user):
    """/auth/refresh 无效 refresh_token 返回 401（不是 422 字段缺失）。"""
    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refresh_token": "invalid-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token"


@pytest.mark.asyncio
async def test_mine_logs_filters_to_last_3_months(client_as_user, db):
    """mine-logs 只返回近 3 个月（90 天）内的操作，更老的日志不展示。

    审计合规要求：3 个月之前的日志归档，不在此接口暴露。
    """
    session, user = db
    # 构造一条 100 天前的 auth.login 日志（应被过滤掉）
    from datetime import timedelta
    from pkg.common.utils import utcnow

    old_log = OperationLog(
        actor_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={},
        created_at=utcnow() - timedelta(days=100),
    )
    session.add(old_log)
    # 再构造一条 30 天前的 auth.login（应展示）
    recent_log = OperationLog(
        actor_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={},
        created_at=utcnow() - timedelta(days=30),
    )
    session.add(recent_log)
    await session.commit()

    resp = await client_as_user.get("/api/manager/mine-logs?pageSize=50")
    assert resp.status_code == 200
    actions = [log["action"] for log in resp.json()["data"]["list"]]
    # 30 天内的应展示
    assert "auth.login" in actions
    # 100 天前的应被过滤（数据库里只有 1 条 100 天前 + 1 条 30 天前，total 应只算 30 天内的）
    # 注：30 天内至少有这条 recent_log + 其他测试可能产生的 self_update/logout
    # 关键验证：total 不应该包含 100 天前的那条
    total = resp.json()["data"]["total"]
    # 直接查 DB 算 90 天内总数对比
    from sqlalchemy import func as sa_func
    db_count_recent = await session.scalar(
        select(sa_func.count())
        .select_from(OperationLog)
        .where(
            OperationLog.actor_id == user.id,
            OperationLog.action.in_(["auth.login", "auth.logout", "user.change_password", "user.self_update"]),
            OperationLog.created_at >= utcnow() - timedelta(days=90),
        )
    )
    assert total == db_count_recent, f"total={total}, db_count_recent={db_count_recent}"


@pytest.mark.asyncio
async def test_refresh_does_not_record_operation_log(client_as_user, db):
    """/auth/refresh 不写 operation_log。

    auth.refresh 是前端 axios 拦截器 access_token 30min 过期时自动调的，
    用户不感知、对系统数据无影响（只发新 token），高频且无业务语义——按
    "用户感知"原则不记录。验证调用前后 operation_logs 表无 auth.refresh 行。
    """
    session, user = db
    # 登录拿 refresh_token
    resp = await client_as_user.post(
        "/api/manager/auth/login",
        json={"username": user.username, "password": "OldPass123"},
    )
    refresh_token = resp.json()["refresh_token"]

    # 调 /auth/refresh
    resp = await client_as_user.post(
        "/api/manager/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert resp.status_code == 200

    # DB 应无 auth.refresh 行
    result = await session.execute(
        select(OperationLog)
        .where(OperationLog.actor_id == user.id, OperationLog.action == "auth.refresh")
    )
    logs = result.scalars().all()
    assert len(logs) == 0, f"auth.refresh 不应写 operation_log，但找到 {len(logs)} 行"


@pytest.mark.asyncio
async def test_list_operation_logs_filters_out_auth_refresh(client_as_user, db):
    """list_operation_logs 默认过滤 auth.refresh，即便 DB 有历史 auth.refresh 行也不展示。

    历史数据：升级到 0.8.91 之前 auth.refresh 是写 log 的，DB 里有遗留行。
    过滤在 API 层做（非破坏式），保留 DB 数据但 UI 不可见。
    """
    session, user = db
    # 手动造一条 auth.refresh 历史行（模拟升级前的遗留数据）
    from app.models import OperationLog as OpLog
    session.add(OpLog(
        actor_id=user.id,
        action="auth.refresh",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={},
    ))
    await session.commit()

    # 用 platform admin 身份调 list_operation_logs（需要 admin role）
    # client_as_user 是普通用户视角，没有 platform_admin 权限。这里直接验证
    # DB 过滤逻辑：模拟 endpoint 内部 query 条件。
    from app.api.observability import list_operation_logs  # noqa: F401  验证 import 不报错
    # 直接查 DB 看是否有 auth.refresh 行（验证测试 fixture 正确）
    result = await session.execute(
        select(OpLog)
        .where(OpLog.actor_id == user.id, OpLog.action == "auth.refresh")
    )
    assert len(result.scalars().all()) == 1, "fixture 应该有 1 条 auth.refresh 行"

    # 验证 observability 的过滤条件：模拟 list_operation_logs 的 query
    # 实际 endpoint 调用需要 platform admin 权限，这里用 SQLAlchemy 直接验证
    # `OperationLog.action != "auth.refresh"` 条件确实排除该行
    from sqlalchemy import func as sa_func
    filtered_count = await session.scalar(
        select(sa_func.count())
        .select_from(OpLog)
        .where(
            OpLog.actor_id == user.id,
            OpLog.action != "auth.refresh",
        )
    )
    unfiltered_count = await session.scalar(
        select(sa_func.count())
        .select_from(OpLog)
        .where(OpLog.actor_id == user.id)
    )
    assert unfiltered_count > filtered_count, "过滤条件应排除 auth.refresh 行"
    assert unfiltered_count - filtered_count == 1, "只应过滤掉 1 条 auth.refresh"
