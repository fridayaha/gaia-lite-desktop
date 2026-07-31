"""消息级反馈 / 收藏 API 集成测试 — 真 DB 验证 upsert + 唯一约束 + 用户隔离。

覆盖：
- 反馈 upsert：同 message_ref 重复提交更新同一行（行数不变）
- 点踩无 reason → 422 且不落库；点赞忽略 reason/comment
- value=null 取消反馈（幂等 204）
- GET 按会话恢复仅回当前用户（用户隔离）
- 收藏：增 / 幂等 / 删（幂等）/ 列表倒序 + agent_name join miss 为 None
- content_snapshot 含 markdown/unicode 原样存取
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user
from app.models import MessageFavorite, MessageFeedback, User
from pkg.common.config import settings

AGENT_ID = str(uuid.uuid4())
SESSION_ID = f"sess_{uuid.uuid4().hex[:8]}"


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理反馈/收藏 + user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"fb_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    await session.execute(text("DELETE FROM message_feedbacks"))
    await session.execute(text("DELETE FROM message_favorites"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM message_feedbacks WHERE user_id = :uid"), {"uid": user.id})
    await session.execute(text("DELETE FROM message_favorites WHERE user_id = :uid"), {"uid": user.id})
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def second_user(db):
    session, _ = db
    user = User(
        username=f"fb2_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    yield user
    await session.execute(text("DELETE FROM message_feedbacks WHERE user_id = :uid"), {"uid": user.id})
    await session.execute(text("DELETE FROM message_favorites WHERE user_id = :uid"), {"uid": user.id})
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()


def _make_client(session, user):
    from app.main import app
    from pkg.common.database import get_db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def client(db):
    session, user = db
    c = _make_client(session, user)
    yield c
    from app.main import app

    app.dependency_overrides.clear()


def _fb_body(**over):
    body = {
        "agent_id": AGENT_ID,
        "session_id": SESSION_ID,
        "message_ref": "mid:225",
        "run_id": "run_abc123",
        "value": "up",
        "content_snapshot": "这是 **AI** 的回复",
    }
    body.update(over)
    return body


async def _count(db_session, model):
    return (await db_session.execute(select(func.count()).select_from(model))).scalar_one()


# ── 反馈 upsert ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_feedback_create_then_update_same_row(client, db):
    session, user = db

    r1 = await client.put("/api/manager/message-feedback", json=_fb_body())
    assert r1.status_code == 200, r1.text
    item = r1.json()
    assert item["value"] == "up"
    assert item["message_ref"] == "mid:225"
    assert item["run_id"] == "run_abc123"

    # 真 DB 验证字段落库
    row = (
        await session.execute(select(MessageFeedback).where(MessageFeedback.user_id == user.id))
    ).scalar_one()
    assert row.agent_id == AGENT_ID
    assert row.session_id == SESSION_ID
    assert row.value == "up"
    assert row.content_snapshot == "这是 **AI** 的回复"
    assert await _count(session, MessageFeedback) == 1

    # 同 ref 改点踩 → 更新同一行，不新增
    r2 = await client.put(
        "/api/manager/message-feedback",
        json=_fb_body(value="down", reason="inaccurate", comment="事实错误"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["value"] == "down"
    assert r2.json()["reason"] == "inaccurate"
    assert await _count(session, MessageFeedback) == 1

    row2 = (
        await session.execute(select(MessageFeedback).where(MessageFeedback.user_id == user.id))
    ).scalar_one()
    assert row2.value == "down"
    assert row2.reason == "inaccurate"
    assert row2.comment == "事实错误"


@pytest.mark.asyncio
async def test_down_without_reason_422_and_not_persisted(client, db):
    session, _ = db
    r = await client.put("/api/manager/message-feedback", json=_fb_body(value="down"))
    assert r.status_code == 422
    assert await _count(session, MessageFeedback) == 0


@pytest.mark.asyncio
async def test_up_ignores_reason_and_comment(client, db):
    session, _ = db
    r = await client.put(
        "/api/manager/message-feedback",
        json=_fb_body(value="up", reason="other", comment="不该存"),
    )
    assert r.status_code == 200, r.text
    row = (await session.execute(select(MessageFeedback))).scalar_one()
    assert row.value == "up"
    assert row.reason is None
    assert row.comment is None


@pytest.mark.asyncio
async def test_cancel_feedback_idempotent(client, db):
    session, _ = db
    await client.put("/api/manager/message-feedback", json=_fb_body())
    assert await _count(session, MessageFeedback) == 1

    r1 = await client.put("/api/manager/message-feedback", json=_fb_body(value=None))
    assert r1.status_code == 204
    assert await _count(session, MessageFeedback) == 0

    # 再取消一次仍 204（幂等）
    r2 = await client.put("/api/manager/message-feedback", json=_fb_body(value=None))
    assert r2.status_code == 204


@pytest.mark.asyncio
async def test_list_feedback_scoped_to_current_user(client, db, second_user):
    session, user = db
    await client.put("/api/manager/message-feedback", json=_fb_body())
    # 第二个用户的反馈直接写 DB（两 client 共享全局 dependency_overrides，后者会覆盖前者）
    session.add(
        MessageFeedback(
            user_id=second_user.id,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            message_ref="mid:226",
            value="down",
            reason="harmful",
            content_snapshot="x",
        )
    )
    await session.commit()

    r = await client.get(f"/api/manager/message-feedback?session_id={SESSION_ID}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["message_ref"] == "mid:225"
    assert items[0]["value"] == "up"


# ── 收藏 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_favorite_add_idempotent_delete(client, db):
    session, _ = db
    body = {
        "agent_id": AGENT_ID,
        "session_id": SESSION_ID,
        "message_ref": "mid:225",
        "content_snapshot": "收藏内容",
    }
    r1 = await client.put("/api/manager/message-favorites", json=body)
    assert r1.status_code == 200, r1.text
    assert r1.json()["message_ref"] == "mid:225"
    assert r1.json()["agent_name"] is None  # 无匹配实例
    assert await _count(session, MessageFavorite) == 1

    # 幂等：重复收藏不新增
    r2 = await client.put("/api/manager/message-favorites", json=body)
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    assert await _count(session, MessageFavorite) == 1

    # 删除 + 幂等删除
    rd = await client.request(
        "DELETE",
        "/api/manager/message-favorites",
        json={"session_id": SESSION_ID, "message_ref": "mid:225"},
    )
    assert rd.status_code == 204
    assert await _count(session, MessageFavorite) == 0
    rd2 = await client.request(
        "DELETE",
        "/api/manager/message-favorites",
        json={"session_id": SESSION_ID, "message_ref": "mid:225"},
    )
    assert rd2.status_code == 204


@pytest.mark.asyncio
async def test_favorites_mine_list_order_and_isolation(client, db, second_user):
    session, _ = db
    for i in range(3):
        await client.put(
            "/api/manager/message-favorites",
            json={
                "agent_id": AGENT_ID,
                "session_id": SESSION_ID,
                "message_ref": f"mid:{100 + i}",
                "content_snapshot": f"第{i}条",
            },
        )
    # 第二个用户的收藏直接写 DB（避免 dependency_overrides 互相覆盖）
    session.add(
        MessageFavorite(
            user_id=second_user.id,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            message_ref="mid:999",
            content_snapshot="别人的",
        )
    )
    await session.commit()

    r = await client.get("/api/manager/message-favorites/mine")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 3
    assert {it["message_ref"] for it in items} == {"mid:100", "mid:101", "mid:102"}
    # 倒序：最新收藏的在前
    assert items[0]["message_ref"] == "mid:102"


@pytest.mark.asyncio
async def test_favorites_mine_pagination(client, db):
    for i in range(5):
        await client.put(
            "/api/manager/message-favorites",
            json={
                "agent_id": AGENT_ID,
                "session_id": SESSION_ID,
                "message_ref": f"mid:{i}",
                "content_snapshot": f"s{i}",
            },
        )
    r = await client.get("/api/manager/message-favorites/mine?limit=2&offset=0")
    assert len(r.json()) == 2
    r2 = await client.get("/api/manager/message-favorites/mine?limit=2&offset=4")
    assert len(r2.json()) == 1


@pytest.mark.asyncio
async def test_content_snapshot_unicode_roundtrip(client, db):
    snapshot = "含 emoji 🎉 和 **markdown** 以及换行\n第二行 ```python\ncode\n```"
    await client.put(
        "/api/manager/message-favorites",
        json={
            "agent_id": AGENT_ID,
            "session_id": SESSION_ID,
            "message_ref": "mid:uni",
            "content_snapshot": snapshot,
        },
    )
    row = (await db[0].execute(select(MessageFavorite))).scalar_one()
    assert row.content_snapshot == snapshot


@pytest.mark.asyncio
async def test_list_session_favorites_scoped(client, db, second_user):
    session, _ = db
    await client.put(
        "/api/manager/message-favorites",
        json={
            "agent_id": AGENT_ID,
            "session_id": SESSION_ID,
            "message_ref": "mid:1",
            "content_snapshot": "a",
        },
    )
    # 同 ref 不同会话 + 同会话不同用户：都不应出现在本会话列表
    await client.put(
        "/api/manager/message-favorites",
        json={
            "agent_id": AGENT_ID,
            "session_id": f"{SESSION_ID}_other",
            "message_ref": "mid:1",
            "content_snapshot": "b",
        },
    )
    session.add(
        MessageFavorite(
            user_id=second_user.id,
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            message_ref="mid:2",
            content_snapshot="c",
        )
    )
    await session.commit()

    r = await client.get(f"/api/manager/message-favorites?session_id={SESSION_ID}")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["message_ref"] == "mid:1"
