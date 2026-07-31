"""消息级用户反馈 / 收藏 API — /api/manager/message-feedback, /api/manager/message-favorites

反馈（赞/踩）：
  PUT  /message-feedback        upsert（value=null 取消）；down 时 reason 必填
  GET  /message-feedback        当前用户在指定会话的全部反馈（进会话恢复按钮状态）

收藏：
  PUT  /message-favorites       幂等新增
  DELETE /message-favorites     按 session_id + message_ref 删除（幂等）
  GET  /message-favorites/mine  「我的收藏」分页列表（join 实例名）
  GET  /message-favorites       当前用户在指定会话的全部收藏（恢复星标状态）

锚点 message_ref：客户端传 "mid:{引擎消息id}"（历史消息有稳定自增 id）或
"hash:{sha256(content)[:16]}"（兜底）。业务库为 source of truth；
反馈写库成功后异步镜像 Langfuse score（按 run_id 匹配 gateway 外层 trace），
未配置 / 无 run_id / 无匹配 trace 时静默跳过。收藏不镜像。
"""
import logging
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.models import AgentInstance, MessageFeedback, MessageFavorite, User
from app.schemas import (
    MessageFavoriteDeleteRequest,
    MessageFavoriteItem,
    MessageFavoriteUpsertRequest,
    MessageFeedbackItem,
    MessageFeedbackUpsertRequest,
)
from app.services import langfuse_client

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager", tags=["message-feedback"])

logger = logging.getLogger(__name__)


async def _mirror_feedback_to_langfuse(feedback_id: UUID) -> None:
    """把一条反馈镜像为 Langfuse score（fire-and-forget）。

    run → trace 映射：gateway 外层 trace 的 metadata.run_id（trace_run_bind 写入）。
    自建 Langfuse v3 服务端 metadata 过滤不生效，需 list_traces(sessionId) 后客户端过滤。
    """
    try:
        if not langfuse_client.is_configured():
            return
        # 独立 session：BackgroundTask 执行时请求级 session 已关闭
        from pkg.common.database import async_session

        async with async_session() as db:
            fb = await db.get(MessageFeedback, feedback_id)
        if fb is None or not fb.run_id:
            return
        data = await langfuse_client.list_traces(session_id=fb.session_id, limit=50)
        traces = (data or {}).get("data") or []
        trace_id = next(
            (t.get("id") for t in traces if (t.get("metadata") or {}).get("run_id") == fb.run_id),
            None,
        )
        if not trace_id:
            logger.info(f"feedback mirror: no trace for run_id={fb.run_id}")
            return
        comment_parts = [p for p in (fb.reason, fb.comment) if p]
        await langfuse_client.create_score(
            trace_id=trace_id,
            name="user_feedback",
            value=1.0 if fb.value == "up" else -1.0,
            comment="; ".join(comment_parts) or None,
            score_id=str(fb.id),
        )
    except Exception as e:
        logger.warning(f"feedback mirror to langfuse failed: {e}")


# ───────────────────────────────────────
# 反馈（赞 / 踩）
# ───────────────────────────────────────

@router.put("/message-feedback", response_model=MessageFeedbackItem)
async def upsert_feedback(
    req: MessageFeedbackUpsertRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """提交/更新/取消反馈。同一 (user, session, message_ref) 仅一条，重复提交即更新。"""
    stmt = select(MessageFeedback).where(
        MessageFeedback.user_id == user.id,
        MessageFeedback.session_id == req.session_id,
        MessageFeedback.message_ref == req.message_ref,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if req.value is None:
        # 取消反馈（幂等）
        if existing is not None:
            await db.delete(existing)
            await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    if req.value == "down" and not req.reason:
        raise HTTPException(status_code=422, detail="点踩时请选择一个原因")

    # 点赞不带原因/评论
    reason = req.reason if req.value == "down" else None
    comment = req.comment if req.value == "down" else None

    if existing is None:
        existing = MessageFeedback(
            user_id=user.id,
            agent_id=req.agent_id,
            session_id=req.session_id,
            message_ref=req.message_ref,
            run_id=req.run_id,
            value=req.value,
            reason=reason,
            comment=comment,
            content_snapshot=req.content_snapshot,
        )
        db.add(existing)
    else:
        existing.agent_id = req.agent_id
        existing.run_id = req.run_id or existing.run_id
        existing.value = req.value
        existing.reason = reason
        existing.comment = comment
        existing.content_snapshot = req.content_snapshot
    await db.commit()
    await db.refresh(existing)

    background_tasks.add_task(_mirror_feedback_to_langfuse, existing.id)
    return MessageFeedbackItem.model_validate(existing)


@router.get("/message-feedback", response_model=list[MessageFeedbackItem])
async def list_feedback(
    session_id: str = Query(..., min_length=1, max_length=128),
    agent_id: str | None = Query(None, max_length=64),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户在指定会话的全部反馈（进会话时恢复按钮状态）。"""
    stmt = select(MessageFeedback).where(
        MessageFeedback.user_id == user.id,
        MessageFeedback.session_id == session_id,
    )
    if agent_id:
        stmt = stmt.where(MessageFeedback.agent_id == agent_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [MessageFeedbackItem.model_validate(r) for r in rows]


# ───────────────────────────────────────
# 收藏
# ───────────────────────────────────────

@router.put("/message-favorites", response_model=MessageFavoriteItem, status_code=status.HTTP_200_OK)
async def upsert_favorite(
    req: MessageFavoriteUpsertRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏一条消息。幂等：已存在直接返回原记录。"""
    stmt = select(MessageFavorite).where(
        MessageFavorite.user_id == user.id,
        MessageFavorite.session_id == req.session_id,
        MessageFavorite.message_ref == req.message_ref,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is None:
        existing = MessageFavorite(
            user_id=user.id,
            agent_id=req.agent_id,
            session_id=req.session_id,
            message_ref=req.message_ref,
            run_id=req.run_id,
            content_snapshot=req.content_snapshot,
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
    return await _to_favorite_item(db, existing)


@router.delete("/message-favorites", status_code=status.HTTP_204_NO_CONTENT)
async def delete_favorite(
    req: MessageFavoriteDeleteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取消收藏（幂等：不存在也返回 204）。"""
    await db.execute(
        delete(MessageFavorite).where(
            MessageFavorite.user_id == user.id,
            MessageFavorite.session_id == req.session_id,
            MessageFavorite.message_ref == req.message_ref,
        )
    )
    await db.commit()


@router.get("/message-favorites/mine", response_model=list[MessageFavoriteItem])
async def list_my_favorites(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """「我的收藏」分页列表，按收藏时间倒序，带出实例名（实例已删为 None）。"""
    stmt = (
        select(MessageFavorite)
        .where(MessageFavorite.user_id == user.id)
        .order_by(MessageFavorite.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [await _to_favorite_item(db, r) for r in rows]


@router.get("/message-favorites", response_model=list[MessageFavoriteItem])
async def list_session_favorites(
    session_id: str = Query(..., min_length=1, max_length=128),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户在指定会话的全部收藏（进会话时恢复星标状态）。"""
    stmt = select(MessageFavorite).where(
        MessageFavorite.user_id == user.id,
        MessageFavorite.session_id == session_id,
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [await _to_favorite_item(db, r) for r in rows]


async def _to_favorite_item(db: AsyncSession, fav: MessageFavorite) -> MessageFavoriteItem:
    """组响应：agent_id(str) → 实例 name（实例可能已删，查不到为 None）。

    agent_instances.id 是 UUID，favorites.agent_id 是 str，用 cast 对齐比较。
    """
    from sqlalchemy import cast, String

    name = (
        await db.execute(
            select(AgentInstance.name).where(cast(AgentInstance.id, String) == fav.agent_id)
        )
    ).scalar_one_or_none()
    return MessageFavoriteItem(
        id=fav.id,
        agent_id=fav.agent_id,
        agent_name=name,
        session_id=fav.session_id,
        message_ref=fav.message_ref,
        content_snapshot=fav.content_snapshot,
        created_at=fav.created_at,
    )
