"""社区文章 API 集成测试 — 真 DB 验证 CRUD + 状态机 + 鉴权 + 公开端点。

覆盖：
- create/update/submit/audit/delete 全状态流转
- slug 冲突自动加后缀
- 跨作者改 403、非平台管理员审核 403
- 公开端点无 token 可访问 + 仅返回 PUBLISHED
- view_count 自增
- 我的文章列表含 DRAFT/PENDING/REJECTED
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import get_current_user
from app.models import Article, ArticleStatus, User
from pkg.common.config import settings


# ── fixtures ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    """真 DB session + 隔离 test user；teardown 清理本测试产生的文章 + user。"""
    engine = create_async_engine(settings.test_database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = factory()

    user = User(
        username=f"community_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    # 清空残留文章 + operation_logs（log_operation 写入）
    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM community_articles"))
    await session.commit()

    yield session, user

    await session.execute(text("DELETE FROM operation_logs"))
    await session.execute(text("DELETE FROM community_articles WHERE author_id = :uid"), {"uid": user.id})
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def second_user(db):
    """第二个普通用户，用于测跨作者 403。"""
    session, _ = db
    user = User(
        username=f"second_{uuid.uuid4().hex[:8]}",
        email=f"second_{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        is_active=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    yield user
    await session.execute(text("DELETE FROM users WHERE id = :uid"), {"uid": user.id})
    await session.commit()


@pytest_asyncio.fixture
async def client_as_user(db, monkeypatch):
    """登录用户视角（普通用户，非平台管理员）。"""
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, user = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: False)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_as_second_user(db, second_user, monkeypatch):
    """第二个用户视角，用于跨作者测试。"""
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, _ = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: second_user
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: False)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_as_admin(db, monkeypatch):
    """平台管理员视角：monkeypatch is_platform_admin → True。"""
    from app.main import app
    from pkg.common.database import get_db
    import app.core.auth as auth

    session, user = db

    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(auth, "is_platform_admin", lambda _u: True)

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client_public(db):
    """公开客户端：未登录，不 override get_current_user。

    get_current_user 默认走 JWT，无 token 时 raise 401。
    公开端点（list/get）无 Depends(get_current_user)，应正常返回。
    """
    from app.main import app
    from pkg.common.database import get_db

    session, _ = db
    app.dependency_overrides[get_db] = lambda: session
    # 故意不 override get_current_user，让端点直接跳过鉴权（因为端点本身没声明 Depends）

    transport = ASGITransport(app=app)
    c = AsyncClient(transport=transport, base_url="http://test")
    yield c
    app.dependency_overrides.clear()


# ── 公开端点测试 ───────────────────────────────────────────────


async def test_public_list_returns_only_published(client_public, db):
    """GET /articles 公开访问无 token，仅返回 PUBLISHED 文章。"""
    session, user = db
    session.add(Article(
        author_id=user.id, title="草稿文章", slug="draft-1", content="x",
        status=ArticleStatus.DRAFT,
    ))
    session.add(Article(
        author_id=user.id, title="已发布文章", slug="published-1", content="y",
        status=ArticleStatus.PUBLISHED, published_at=datetime.now(UTC),
    ))
    session.add(Article(
        author_id=user.id, title="待审核文章", slug="pending-1", content="z",
        status=ArticleStatus.PENDING,
    ))
    await session.commit()

    resp = await client_public.get("/api/manager/community/articles")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "已发布文章"
    assert data["items"][0]["author_name"] == user.username


async def test_public_get_by_slug_increments_view_count(client_public, db):
    """GET /articles/{slug} 公开访问，view_count 自增。"""
    session, user = db
    a = Article(
        author_id=user.id, title="详情测试", slug="detail-slug", content="hello",
        status=ArticleStatus.PUBLISHED, published_at=datetime.now(UTC),
        view_count=5,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)

    resp = await client_public.get("/api/manager/community/articles/detail-slug")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "详情测试"
    assert body["view_count"] == 6

    # 再请求一次，view_count 再次 +1
    resp2 = await client_public.get("/api/manager/community/articles/detail-slug")
    assert resp2.json()["view_count"] == 7


async def test_public_get_nonexistent_returns_404(client_public):
    resp = await client_public.get("/api/manager/community/articles/nonexistent-slug")
    assert resp.status_code == 404


async def test_public_get_draft_returns_404(client_public, db):
    """草稿状态文章不应被公开访问。"""
    session, user = db
    session.add(Article(
        author_id=user.id, title="草稿", slug="draft-secret", content="x",
        status=ArticleStatus.DRAFT,
    ))
    await session.commit()

    resp = await client_public.get("/api/manager/community/articles/draft-secret")
    assert resp.status_code == 404


# ── 创建/编辑/提交测试 ───────────────────────────────────────────────


async def test_create_article_draft(client_as_user, db):
    """登录用户创建文章 → DRAFT 状态，slug 自动生成。"""
    resp = await client_as_user.post("/api/manager/community/articles", json={
        "title": "我的第一篇文章",
        "content": "# 正文\nhello world",
        "excerpt": "摘要",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "DRAFT"
    assert body["title"] == "我的第一篇文章"
    assert body["slug"]  # 自动生成
    assert body["author_name"]  # 来自关系


async def test_create_article_with_explicit_slug(client_as_user):
    resp = await client_as_user.post("/api/manager/community/articles", json={
        "title": "标题",
        "slug": "my-custom-slug",
        "content": "content",
    })
    assert resp.status_code == 201
    assert resp.json()["slug"] == "my-custom-slug"


async def test_create_article_slug_conflict_auto_suffix(client_as_user, db):
    """slug 冲突时自动加 -2 后缀。"""
    session, user = db
    session.add(Article(
        author_id=user.id, title="原标题", slug="conflict-slug", content="x",
        status=ArticleStatus.DRAFT,
    ))
    await session.commit()

    resp = await client_as_user.post("/api/manager/community/articles", json={
        "title": "新标题",
        "slug": "conflict-slug",
        "content": "y",
    })
    assert resp.status_code == 201
    assert resp.json()["slug"] == "conflict-slug-2"


async def test_update_article_only_author(client_as_second_user, db):
    """非作者改他人文章 → 403。"""
    session, user = db
    article = Article(
        author_id=user.id, title="原", slug="update-403", content="x",
        status=ArticleStatus.DRAFT,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_second_user.put(
        f"/api/manager/community/articles/{article.id}", json={"title": "篡改"}
    )
    assert resp.status_code == 403


async def test_update_article_owned_draft(client_as_user, db):
    """作者改自己的 DRAFT 文章 → 成功。"""
    session, user = db
    article = Article(
        author_id=user.id, title="原", slug="update-ok", content="x",
        status=ArticleStatus.DRAFT,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_user.put(
        f"/api/manager/community/articles/{article.id}",
        json={"title": "新标题", "content": "new content"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "新标题"
    assert body["content"] == "new content"


async def test_update_published_rejected(client_as_user, db):
    """PENDING/PUBLISHED 状态不可编辑 → 400。"""
    session, user = db
    a_pending = Article(
        author_id=user.id, title="p", slug="pending-edit", content="x",
        status=ArticleStatus.PENDING,
    )
    a_pub = Article(
        author_id=user.id, title="pub", slug="published-edit", content="x",
        status=ArticleStatus.PUBLISHED, published_at=datetime.now(UTC),
    )
    session.add_all([a_pending, a_pub])
    await session.commit()
    await session.refresh(a_pending)
    await session.refresh(a_pub)

    r1 = await client_as_user.put(
        f"/api/manager/community/articles/{a_pending.id}", json={"title": "x"}
    )
    assert r1.status_code == 400

    r2 = await client_as_user.put(
        f"/api/manager/community/articles/{a_pub.id}", json={"title": "y"}
    )
    assert r2.status_code == 400


async def test_submit_for_review_draft_to_pending(client_as_user, db):
    """作者提交 DRAFT → PENDING。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="submit-test", content="x",
        status=ArticleStatus.DRAFT,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_user.post(f"/api/manager/community/articles/{article.id}/submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING"


# ── 审核测试 ───────────────────────────────────────────────


async def test_audit_approve_publishes(client_as_admin, db):
    """平台管理员审核通过 → PUBLISHED + published_at 落库。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="audit-approve", content="x",
        status=ArticleStatus.PENDING,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_admin.post(
        f"/api/manager/community/articles/{article.id}/audit",
        json={"approve": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PUBLISHED"
    assert body["published_at"] is not None


async def test_audit_reject_requires_reason(client_as_admin, db):
    """驳回必须填 reason，否则 400。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="audit-reject-no-reason", content="x",
        status=ArticleStatus.PENDING,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_admin.post(
        f"/api/manager/community/articles/{article.id}/audit",
        json={"approve": False},
    )
    assert resp.status_code == 400


async def test_audit_reject_with_reason(client_as_admin, db):
    """驳回带 reason → REJECTED + reject_reason 落库。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="audit-reject-ok", content="x",
        status=ArticleStatus.PENDING,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_admin.post(
        f"/api/manager/community/articles/{article.id}/audit",
        json={"approve": False, "reject_reason": "内容不符合规范"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "REJECTED"
    assert body["reject_reason"] == "内容不符合规范"


async def test_audit_non_admin_returns_403(client_as_user, db):
    """非平台管理员审核 → 403。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="audit-403", content="x",
        status=ArticleStatus.PENDING,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_user.post(
        f"/api/manager/community/articles/{article.id}/audit",
        json={"approve": True},
    )
    assert resp.status_code == 403


async def test_audit_non_pending_returns_400(client_as_admin, db):
    """非 PENDING 状态审核 → 400。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="audit-not-pending", content="x",
        status=ArticleStatus.DRAFT,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_admin.post(
        f"/api/manager/community/articles/{article.id}/audit",
        json={"approve": True},
    )
    assert resp.status_code == 400


# ── 审核队列 + 我的文章 ───────────────────────────────────────────────


async def test_list_pending_returns_only_pending(client_as_admin, db):
    """审核队列仅返回 PENDING。"""
    session, user = db
    session.add(Article(
        author_id=user.id, title="p1", slug="pending-list-1", content="x",
        status=ArticleStatus.PENDING,
    ))
    session.add(Article(
        author_id=user.id, title="d1", slug="draft-list-1", content="x",
        status=ArticleStatus.DRAFT,
    ))
    await session.commit()

    resp = await client_as_admin.get("/api/manager/community/audit/pending")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "p1"


async def test_list_pending_non_admin_403(client_as_user):
    """非管理员访问审核队列 → 403。"""
    resp = await client_as_user.get("/api/manager/community/audit/pending")
    assert resp.status_code == 403


async def test_my_articles_returns_all_statuses(client_as_user, db):
    """GET /my-articles 返回当前用户全部状态文章。"""
    session, user = db
    session.add(Article(
        author_id=user.id, title="my-draft", slug="my-draft", content="x",
        status=ArticleStatus.DRAFT,
    ))
    session.add(Article(
        author_id=user.id, title="my-pending", slug="my-pending", content="x",
        status=ArticleStatus.PENDING,
    ))
    session.add(Article(
        author_id=user.id, title="my-published", slug="my-published", content="x",
        status=ArticleStatus.PUBLISHED, published_at=datetime.now(UTC),
    ))
    await session.commit()

    resp = await client_as_user.get("/api/manager/community/my-articles")
    assert resp.status_code == 200
    items = resp.json()
    titles = {i["title"] for i in items}
    assert {"my-draft", "my-pending", "my-published"} <= titles


# ── 删除测试 ───────────────────────────────────────────────


async def test_delete_by_author(client_as_user, db):
    """作者删除自己的文章。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="delete-by-author", content="x",
        status=ArticleStatus.DRAFT,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_user.delete(f"/api/manager/community/articles/{article.id}")
    assert resp.status_code == 204

    # 验证 DB 已删除
    found = await session.get(Article, article.id)
    assert found is None


async def test_delete_by_non_author_403(client_as_second_user, db):
    """非作者非管理员删除他人文章 → 403。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="delete-403", content="x",
        status=ArticleStatus.DRAFT,
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_second_user.delete(f"/api/manager/community/articles/{article.id}")
    assert resp.status_code == 403


async def test_delete_by_admin(client_as_admin, db):
    """平台管理员删除任意文章。"""
    session, user = db
    article = Article(
        author_id=user.id, title="t", slug="delete-by-admin", content="x",
        status=ArticleStatus.PUBLISHED, published_at=datetime.now(UTC),
    )
    session.add(article)
    await session.commit()
    await session.refresh(article)

    resp = await client_as_admin.delete(f"/api/manager/community/articles/{article.id}")
    assert resp.status_code == 204


# ── 未登录访问受保护端点 401 ────────────────────────────────


async def test_unauthed_create_returns_401(client_public):
    """无 token 调 POST /articles → 401（get_current_user 鉴权失败）。"""
    resp = await client_public.post("/api/manager/community/articles", json={
        "title": "x", "content": "y",
    })
    assert resp.status_code == 401


async def test_unauthed_my_articles_returns_401(client_public):
    resp = await client_public.get("/api/manager/community/my-articles")
    assert resp.status_code == 401
