"""社区文章服务 — 全局平台级（无 group 隔离）。

状态机：DRAFT → PENDING（提交审核）→ PUBLISHED / REJECTED。
- 作者 create(DRAFT) → submit(PENDING) → admin audit(PUBLISHED/REJECTED)
- 公开 list/get 只返回 PUBLISHED；view_count 在 get 时自增（UPDATE 不 fetch）
- 作者可改 DRAFT/REJECTED；PENDING/PUBLISHED 不可改（需先下架）
- 删除：作者本人或平台管理员

slug 生成：作者未填时从 title 自动转（kebab-case + 中文保留）；冲突自动加后缀 -2/-3。
"""
import re
import unicodedata
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleStatus, User
from app.services.audit_service import log_operation


def _slugify(title: str) -> str:
    """title → slug：中文转 kebab-case 保留（URL 友好），英文小写 + 连字符。

    非字母数字字符替换为 '-'，连续 '-' 折叠，首尾 '-' 去除。
    中文直接保留（现代浏览器/搜索引擎支持），复杂字库可用 pypinyin 增强（本次不引入）。
    """
    s = unicodedata.normalize("NFKC", title.strip())
    s = re.sub(r"[^\w一-鿿]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-").lower()
    return s or "untitled"


async def _ensure_unique_slug(db: AsyncSession, slug: str, exclude_id: UUID | None = None) -> str:
    """slug 冲突时自动加 -2 / -3 后缀直到唯一。"""
    candidate = slug
    suffix = 2
    while True:
        q = select(Article).where(Article.slug == candidate)
        if exclude_id is not None:
            q = q.where(Article.id != exclude_id)
        existing = (await db.execute(q)).scalar_one_or_none()
        if not existing:
            return candidate
        candidate = f"{slug}-{suffix}"
        suffix += 1


async def create_article(
    db: AsyncSession,
    *,
    author_id: UUID,
    title: str,
    content: str,
    slug: str | None = None,
    excerpt: str | None = None,
) -> Article:
    """作者创建文章（DRAFT 状态）。slug 留空自动生成；冲突自动加后缀。"""
    base_slug = _slugify(slug) if slug else _slugify(title)
    final_slug = await _ensure_unique_slug(db, base_slug)
    article = Article(
        author_id=author_id,
        title=title,
        slug=final_slug,
        content=content,
        excerpt=excerpt,
        status=ArticleStatus.DRAFT,
    )
    db.add(article)
    await db.flush()
    await log_operation(
        db, actor_id=author_id, action="article.create",
        target_type="article", target_id=article.id,
        detail={"title": title, "slug": final_slug},
    )
    await db.commit()
    await db.refresh(article)
    return article


async def update_article(
    db: AsyncSession,
    article_id: UUID,
    *,
    actor_id: UUID,
    title: str | None = None,
    content: str | None = None,
    excerpt: str | None = None,
) -> Article:
    """作者改文章（仅 DRAFT/REJECTED 可改；PENDING/PUBLISHED 需先下架）。"""
    article = await _get_owned_article(db, article_id, actor_id)
    if article.status not in (ArticleStatus.DRAFT, ArticleStatus.REJECTED):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {article.status} 不可编辑，请先下架或撤回审核",
        )
    if title is not None:
        article.title = title
    if content is not None:
        article.content = content
    if excerpt is not None:
        article.excerpt = excerpt
    await log_operation(
        db, actor_id=actor_id, action="article.update",
        target_type="article", target_id=article.id,
        detail={"title": article.title},
    )
    await db.commit()
    await db.refresh(article)
    return article


async def submit_for_review(db: AsyncSession, article_id: UUID, *, actor_id: UUID) -> Article:
    """DRAFT → PENDING（提交审核）。REJECTED 也可重新提交。"""
    article = await _get_owned_article(db, article_id, actor_id)
    if article.status not in (ArticleStatus.DRAFT, ArticleStatus.REJECTED):
        raise HTTPException(
            status_code=400,
            detail=f"当前状态 {article.status} 不可提交审核",
        )
    article.status = ArticleStatus.PENDING
    article.reject_reason = None  # 清空上次驳回理由
    await log_operation(
        db, actor_id=actor_id, action="article.submit",
        target_type="article", target_id=article.id,
        detail={"title": article.title},
    )
    await db.commit()
    await db.refresh(article)
    return article


async def audit_article(
    db: AsyncSession,
    article_id: UUID,
    *,
    approve: bool,
    reject_reason: str | None,
    actor_id: UUID,
) -> Article:
    """PENDING → PUBLISHED（审核通过，设 published_at）/ REJECTED（驳回，必填 reason）。"""
    from app.core.auth import is_platform_admin
    from app.models import User as UserModel

    article = await _get_article(db, article_id)
    if article.status != ArticleStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"仅 PENDING 状态可审核，当前 {article.status}",
        )
    # 审核权限：仅平台管理员（V3 当前不强制 perm，靠 is_platform_admin 兜底）
    auditor = await db.get(UserModel, actor_id)
    if not auditor or not is_platform_admin(auditor):
        raise HTTPException(status_code=403, detail="仅平台管理员可审核文章")

    if approve:
        article.status = ArticleStatus.PUBLISHED
        article.published_at = datetime.now(UTC)
        article.reject_reason = None
        action = "article.publish"
        detail = {"title": article.title}
    else:
        if not reject_reason:
            raise HTTPException(status_code=400, detail="驳回时必须填写理由")
        article.status = ArticleStatus.REJECTED
        article.reject_reason = reject_reason
        action = "article.reject"
        detail = {"title": article.title, "reason": reject_reason}

    await log_operation(
        db, actor_id=actor_id, action=action,
        target_type="article", target_id=article.id,
        detail=detail,
    )
    await db.commit()
    await db.refresh(article)
    return article


async def delete_article(
    db: AsyncSession, article_id: UUID, *, actor_id: UUID
) -> bool:
    """删除：作者本人或平台管理员。"""
    from app.core.auth import is_platform_admin
    from app.models import User as UserModel

    article = await _get_article(db, article_id)
    actor = await db.get(UserModel, actor_id)
    is_admin = actor is not None and is_platform_admin(actor)
    if article.author_id != actor_id and not is_admin:
        raise HTTPException(status_code=403, detail="无权删除他人文章")

    title = article.title
    await db.delete(article)
    await log_operation(
        db, actor_id=actor_id, action="article.delete",
        target_type="article", target_id=article_id,
        detail={"title": title},
    )
    await db.commit()
    return True


async def list_published(
    db: AsyncSession, *, page: int = 1, page_size: int = 20, q: str | None = None
) -> tuple[list[Article], int]:
    """公开列表：仅 PUBLISHED，按 published_at DESC。支持标题模糊搜索。"""
    conditions = [Article.status == ArticleStatus.PUBLISHED]
    if q:
        conditions.append(Article.title.ilike(f"%{q}%"))
    base = select(Article).where(*conditions).order_by(Article.published_at.desc())
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    items = (
        await db.execute(
            base.offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return list(items), total or 0


async def get_published(db: AsyncSession, slug_or_id: str) -> Article:
    """公开详情：按 slug 或 UUID 查 PUBLISHED 文章，自增 view_count。"""
    article = await _find_by_slug_or_id(db, slug_or_id, only_published=True)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在或未发布")
    # 自增 view_count（UPDATE 不 fetch，避免并发覆盖）
    await db.execute(
        update(Article).where(Article.id == article.id).values(view_count=Article.view_count + 1)
    )
    await db.commit()
    await db.refresh(article)
    return article


async def list_by_author(
    db: AsyncSession, author_id: UUID
) -> list[Article]:
    """我的文章：含 DRAFT/PENDING/REJECTED，按 updated_at DESC。"""
    items = (
        await db.execute(
            select(Article)
            .where(Article.author_id == author_id)
            .order_by(Article.updated_at.desc())
        )
    ).scalars().all()
    return list(items)


async def list_pending(
    db: AsyncSession, *, page: int = 1, page_size: int = 20
) -> tuple[list[Article], int]:
    """审核队列：PENDING，按 created_at ASC（先提交先审）。"""
    base = select(Article).where(Article.status == ArticleStatus.PENDING).order_by(Article.created_at.asc())
    total = await db.scalar(select(func.count()).select_from(base.subquery()))
    items = (
        await db.execute(
            base.offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return list(items), total or 0


async def _get_article(db: AsyncSession, article_id: UUID) -> Article:
    article = await db.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="文章不存在")
    return article


async def _get_owned_article(
    db: AsyncSession, article_id: UUID, actor_id: UUID
) -> Article:
    """获取文章并校验作者身份（编辑/提交等操作前置）。"""
    article = await _get_article(db, article_id)
    if article.author_id != actor_id:
        raise HTTPException(status_code=403, detail="无权操作他人文章")
    return article


async def _find_by_slug_or_id(
    db: AsyncSession, slug_or_id: str, *, only_published: bool = False
) -> Article | None:
    """按 slug 或 UUID 查找文章。"""
    try:
        aid = UUID(slug_or_id)
        q = select(Article).where(Article.id == aid)
    except ValueError:
        q = select(Article).where(Article.slug == slug_or_id)
    if only_published:
        q = q.where(Article.status == ArticleStatus.PUBLISHED)
    return (await db.execute(q)).scalar_one_or_none()
