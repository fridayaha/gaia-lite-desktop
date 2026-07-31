"""社区文章 API — /api/manager/community

公开端点（无鉴权）：
  GET /articles          公开列表（仅 PUBLISHED）
  GET /articles/{slug_or_id}  公开详情（自增 view_count）

登录用户（任意 role）：
  GET /my-articles       当前用户全部文章（含 DRAFT/PENDING/REJECTED）
  POST /articles         创建草稿
  PUT /articles/{id}     编辑（仅作者 + 仅 DRAFT/REJECTED）
  DELETE /articles/{id}  删除（作者或平台管理员）
  POST /articles/{id}/submit  提交审核（DRAFT → PENDING）

审核（仅平台管理员，service 层 is_platform_admin 兜底）：
  GET /audit/pending     待审核队列
  POST /articles/{id}/audit   审核通过/驳回
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.models import Article, User
from app.schemas import (
    ArticleAuditRequest,
    ArticleCreate,
    ArticleListItem,
    ArticleListResponse,
    ArticleResponse,
    ArticleUpdate,
)
from app.services import article_service

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/community", tags=["community"])


async def _load_author(db: AsyncSession, article: Article) -> None:
    """预加载 author 关系（response 用 author.username）。

    永远显式 db.get，不靠 lazy load：async session 下访问未加载的 relationship 会
    触发 sync IO 抛 MissingGreenlet（生产 session 的 identity map 里没有 user 对象，
    不像测试夹具同 session 创建 user + article）。
    """
    article.author = await db.get(User, article.author_id)  # type: ignore[assignment]


# ───────────────────────────────────────
# 公开端点（无鉴权）
# ───────────────────────────────────────

@router.get("/articles", response_model=ArticleListResponse)
async def list_public_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str | None = Query(None, description="按标题模糊搜索"),
    db: AsyncSession = Depends(get_db),
):
    """公开列表：仅 PUBLISHED，按 published_at DESC。"""
    items, total = await article_service.list_published(db, page=page, page_size=page_size, q=q)
    for a in items:
        await _load_author(db, a)
    return ArticleListResponse(
        items=[ArticleListItem.from_article(a) for a in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/articles/{slug_or_id}", response_model=ArticleResponse)
async def get_public_article(
    slug_or_id: str,
    db: AsyncSession = Depends(get_db),
):
    """公开详情：按 slug 或 UUID 查 PUBLISHED，自增 view_count。"""
    article = await article_service.get_published(db, slug_or_id)
    await _load_author(db, article)
    return ArticleResponse.from_article(article)


# ───────────────────────────────────────
# 登录用户端点
# ───────────────────────────────────────

@router.get("/my-articles", response_model=list[ArticleListItem])
async def list_my_articles(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户全部文章（含 DRAFT/PENDING/REJECTED），按 updated_at DESC。"""
    items = await article_service.list_by_author(db, user.id)
    for a in items:
        await _load_author(db, a)
    return [ArticleListItem.from_article(a) for a in items]


@router.post("/articles", response_model=ArticleResponse, status_code=status.HTTP_201_CREATED)
async def create_article(
    data: ArticleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """创建草稿（DRAFT 状态）。slug 留空自动生成。"""
    article = await article_service.create_article(
        db, author_id=user.id, title=data.title, content=data.content,
        slug=data.slug, excerpt=data.excerpt,
    )
    await _load_author(db, article)
    return ArticleResponse.from_article(article)


@router.put("/articles/{article_id}", response_model=ArticleResponse)
async def update_article(
    article_id: UUID,
    data: ArticleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """编辑文章（仅作者 + 仅 DRAFT/REJECTED 可改）。"""
    article = await article_service.update_article(
        db, article_id, actor_id=user.id,
        title=data.title, content=data.content, excerpt=data.excerpt,
    )
    await _load_author(db, article)
    return ArticleResponse.from_article(article)


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    article_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删除文章（作者本人或平台管理员）。"""
    await article_service.delete_article(db, article_id, actor_id=user.id)
    return None


@router.post("/articles/{article_id}/submit", response_model=ArticleResponse)
async def submit_article_for_review(
    article_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """提交审核（DRAFT/REJECTED → PENDING）。"""
    article = await article_service.submit_for_review(db, article_id, actor_id=user.id)
    await _load_author(db, article)
    return ArticleResponse.from_article(article)


# ───────────────────────────────────────
# 审核端点（仅平台管理员）
# ───────────────────────────────────────

@router.get("/audit/pending", response_model=ArticleListResponse)
async def list_pending_articles(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """待审核队列（PENDING，按 created_at ASC）。仅平台管理员。"""
    from app.core.auth import is_platform_admin
    if not is_platform_admin(user):
        raise HTTPException(status_code=403, detail="仅平台管理员可查看审核队列")
    items, total = await article_service.list_pending(db, page=page, page_size=page_size)
    for a in items:
        await _load_author(db, a)
    return ArticleListResponse(
        items=[ArticleListItem.from_article(a) for a in items],
        total=total, page=page, page_size=page_size,
    )


@router.post("/articles/{article_id}/audit", response_model=ArticleResponse)
async def audit_article(
    article_id: UUID,
    data: ArticleAuditRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """审核文章（PENDING → PUBLISHED / REJECTED）。仅平台管理员。"""
    article = await article_service.audit_article(
        db, article_id, approve=data.approve,
        reject_reason=data.reject_reason, actor_id=user.id,
    )
    await _load_author(db, article)
    return ArticleResponse.from_article(article)
