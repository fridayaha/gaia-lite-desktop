"""user_service.create_user + /auth/preset-avatars 集成测试 — 真 DB 验证预置头像分配。

覆盖：
- create_user 按 username md5 哈希分配预置头像 → DB 落库
- preset_path_for_username 是纯函数：同 username 多次调用结果一致
- GET /auth/preset-avatars 返回 12 个相对路径，每条 startswith /avatars/.../presets/
- 12 个预置路径无重复
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from app.core.auth import get_current_user, hash_password
from app.models import User
from app.schemas import UserCreate
from app.services import user_service
from app.services.preset_avatars import (
    PRESET_COUNT,
    compute_preset_index,
    preset_path_for_username,
    preset_paths,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from pkg.common.config import settings

# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    yield session

    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(db):
    """制造一个 actor 用于 log_operation.actor_id。"""
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

    await db.execute(text("DELETE FROM operation_logs WHERE actor_id = :uid"), {"uid": user.id})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await db.commit()


@pytest_asyncio.fixture
async def client_as_user(db, admin_user):
    """登录用户视角（用于 /preset-avatars endpoint 测试）。"""
    from app.main import app

    from pkg.common.database import get_db

    session = db

    # 重查带 selectinload(User.roles)，避免 get_me lazy load 报 MissingGreenlet
    result = await session.execute(
        select(User).options(selectinload(User.roles)).where(User.id == admin_user.id)
    )
    user = result.scalar_one()

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
def mock_litellm(monkeypatch):
    """mock litellm_client.ensure_user 避免 LiteLLM 网络调用。"""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(user_service.litellm_client, "ensure_user", _noop)


# ── tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_user_assigns_preset_avatar(db, admin_user, mock_litellm):
    """create_user 给新用户分配预置头像，路径与 username md5 哈希结果一致。"""
    username = f"alice_preset_{uuid.uuid4().hex[:6]}"
    data = UserCreate(
        username=username,
        real_name="Alice",
        email=f"{username}@example.com",
        phone="13800000000",
        password="Alice-Strong-99!",
    )
    user = await user_service.create_user(db, data, actor_id=admin_user.id)

    expected = preset_path_for_username(username)
    assert user.avatar_url == expected, f"avatar_url={user.avatar_url}, expected={expected}"
    assert user.avatar_url.startswith("/avatars/unionagents-avatars/presets/")
    assert user.avatar_url.endswith(".svg")

    # DB 落库验证
    await db.refresh(user)
    assert user.avatar_url == expected

    # 清理
    await db.execute(text("DELETE FROM operation_logs WHERE target_id = :uid"), {"uid": user.id})
    await db.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await db.commit()


@pytest.mark.asyncio
async def test_preset_path_for_username_is_deterministic():
    """同一 username 多次调用返回相同路径（纯函数，跨进程稳定）。"""
    username = "bob_deterministic_test"
    results = [preset_path_for_username(username) for _ in range(5)]
    assert all(r == results[0] for r in results), "同 username 应返回相同路径"
    assert results[0].startswith("/avatars/unionagents-avatars/presets/")
    assert results[0].endswith(".svg")


@pytest.mark.asyncio
async def test_preset_paths_returns_12_unique_items():
    """preset_paths() 返回 12 个不重复的相对路径。"""
    paths = preset_paths()
    assert len(paths) == PRESET_COUNT == 12
    assert len(set(paths)) == 12, "12 个路径应互不重复"
    for p in paths:
        assert p.startswith("/avatars/unionagents-avatars/presets/")
        assert p.endswith(".svg")


@pytest.mark.asyncio
async def test_compute_preset_index_in_range():
    """compute_preset_index 返回值在 [0, PRESET_COUNT-1] 范围内。"""
    for name in ["a", "b", "用户名1", "Alice", "Bob", "Charlie", "test_user_with_long_name"]:
        idx = compute_preset_index(name)
        assert 0 <= idx < PRESET_COUNT, f"{name} → idx={idx} 超出 [0, {PRESET_COUNT - 1}]"


@pytest.mark.asyncio
async def test_list_preset_avatars_endpoint(client_as_user):
    """GET /api/manager/auth/preset-avatars 返回 12 个相对路径。"""
    resp = await client_as_user.get("/api/manager/auth/preset-avatars")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert len(items) == 12
    assert len(set(items)) == 12
    for path in items:
        assert path.startswith("/avatars/unionagents-avatars/presets/")
        assert path.endswith(".svg")
