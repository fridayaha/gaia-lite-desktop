import asyncio
from uuid import UUID
from datetime import datetime, timezone

from app.api.auth import _decrypt_secrets, _get_active_config
from app.core.auth import require_platform_admin
from app.models import User
from app.schemas import UserCreate, UserListResponse, UserResponse, UserUpdate, UserVerifyCode
from app.services.audit_service import log_operation
from app.services.email_providers import get_sender as get_email_sender
from app.services.sms_providers import get_sender as get_sms_sender
from app.services.user_service import (
    create_user,
    delete_user,
    get_user,
    list_user_profiles,
    list_users,
    update_user,
)
from app.services.verification_code_service import issue_code, verify_code
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/users", tags=["users"])


def _to_response(u: User) -> UserResponse:
    # is_locked + locked_remaining_seconds 是计算属性，不存 DB
    now = datetime.now(timezone.utc)
    locked_until = u.locked_until
    if locked_until and locked_until > now:
        is_locked = True
        remaining = int((locked_until - now).total_seconds())
    else:
        is_locked = False
        remaining = None
    return UserResponse(
        id=u.id,
        username=u.username,
        real_name=u.real_name,
        email=u.email,
        phone=u.phone,
        email_verified=u.email_verified or False,
        phone_verified=u.phone_verified or False,
        avatar_url=u.avatar_url,
        is_active=u.is_active,
        roles=[r.name for r in u.roles],
        created_at=u.created_at,
        last_login_at=u.last_login_at,
        last_login_ip=u.last_login_ip,
        last_login_user_agent=u.last_login_user_agent,
        failed_login_count=u.failed_login_count or 0,
        locked_until=locked_until,
        is_locked=is_locked,
        locked_remaining_seconds=remaining,
    )


@router.get("", response_model=UserListResponse)
async def get_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = None,
    role_id: UUID = None,
    is_active: bool = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    users, total = await list_users(db, page, page_size, search, role_id, is_active)
    return UserListResponse(
        items=[_to_response(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def add_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    # 0.8.110 两态模型：admin create user 不做 email/phone 查重（未认证可重）
    # username 仍全局唯一，保留 pre-query 给精确 UX
    dup_username = (
        await db.execute(select(User.id).where(User.username == data.username))
    ).scalar_one_or_none()
    if dup_username:
        raise HTTPException(status_code=409, detail="username_already_used")
    try:
        created = await create_user(db, data, actor_id=user.id)
    except IntegrityError:
        await db.rollback()
        dup_username = (
            await db.execute(select(User.id).where(User.username == data.username))
        ).scalar_one_or_none()
        if dup_username:
            raise HTTPException(status_code=409, detail="username_already_used")
        raise HTTPException(status_code=409, detail="create_failed")
    return _to_response(created)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_detail(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    user = await get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _to_response(user)


@router.put("/{user_id}", response_model=UserResponse)
async def edit_user(
    user_id: UUID,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    # 0.8.110：未认证 email/phone 不查重，update_user service 层改 email/phone 时自动回退 verified=False
    # username 仍全局唯一，保留 pre-query（排除自身 id）
    if data.username is not None:
        dup_username = (
            await db.execute(
                select(User.id).where(
                    User.username == data.username, User.id != user_id
                )
            )
        ).scalar_one_or_none()
        if dup_username:
            raise HTTPException(status_code=409, detail="username_already_used")
    try:
        updated = await update_user(db, user_id, data, actor_id=user.id)
    except IntegrityError:
        await db.rollback()
        if data.username is not None:
            dup_username = (
                await db.execute(
                    select(User.id).where(
                        User.username == data.username, User.id != user_id
                    )
                )
            ).scalar_one_or_none()
            if dup_username:
                raise HTTPException(status_code=409, detail="username_already_used")
        raise HTTPException(status_code=409, detail="update_failed")
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _to_response(updated)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    deleted = await delete_user(db, user_id, actor_id=user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.get("/{user_id}/profiles")
async def get_user_profiles(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    """查用户在哪些智能体实例上有独立会话空间（删用户确认框用）。"""
    items, total = await list_user_profiles(db, user_id)
    return {"count": total, "items": items}


@router.post("/{user_id}/unlock", response_model=UserResponse)
async def unlock_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """admin 解锁被锁定的用户。

    清空 failed_login_count + locked_until。记 audit。
    用户未锁定时返回 400 user_not_locked（幂等失败而非静默成功）。
    """
    target = await get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not target.locked_until and (target.failed_login_count or 0) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_not_locked")

    target.failed_login_count = 0
    target.locked_until = None

    await log_operation(
        db,
        actor_id=user.id,
        action="user.unlock",
        target_type="user",
        target_id=target.id,
        status="success",
        detail={"username": target.username, "unlocked_by": "admin"},
    )
    await db.commit()
    # refresh 标量 + roles 关系，避免 _to_response 访问 user.roles 时触发 lazy load
    # （refresh 默认不刷新 relationship，必须显式指定）
    await db.refresh(target, ["roles"])
    return _to_response(target)


# ── 0.8.110 邮箱/手机「未认证 / 已认证」两态：admin 发起认证 ─────────────


async def _send_verification_code(
    db: AsyncSession,
    target: User,
    channel: str,
    target_value: str,
    purpose: str,
    actor: User,
) -> str:
    """admin 发起认证：生成验证码 + 调 active provider 发码给 target_value。

    成功返回 sent=true；无 active provider 抛 400。
    """
    cfg = await _get_active_config(db, channel)
    if not cfg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no_active_provider",
        )
    sender = (
        get_sms_sender(cfg.provider)
        if channel == "sms"
        else get_email_sender(cfg.provider)
    )
    if not sender:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provider_sender_unavailable",
        )

    code = await issue_code(
        db,
        channel=channel,
        target=target_value,
        purpose=purpose,
        ip=None,  # admin 发起，无 client ip
    )
    try:
        if channel == "sms":
            await asyncio.to_thread(
                sender, cfg, _decrypt_secrets(cfg), target_value, {"code": code}
            )
        else:
            subject = "知行平台邮箱认证验证码"
            html = (
                f"<p>管理员正在为您认证邮箱，验证码是：<strong>{code}</strong>，"
                "10 分钟内有效。</p>"
                "<p>如果不是您本人操作，请忽略此邮件。</p>"
            )
            await asyncio.to_thread(
                sender, cfg, _decrypt_secrets(cfg), target_value, subject, html
            )
    except Exception as e:
        await db.rollback()
        await log_operation(
            db,
            actor_id=actor.id,
            action="user.verify_channel.send",
            target_type="user",
            target_id=target.id,
            status="failure",
            detail={
                "channel": channel,
                "purpose": purpose,
                "reason": "provider_send_failed",
                "error": str(e),
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="provider_send_failed",
        )

    await log_operation(
        db,
        actor_id=actor.id,
        action="user.verify_channel.send",
        target_type="user",
        target_id=target.id,
        status="success",
        detail={"channel": channel, "purpose": purpose},
    )
    await db.commit()
    return "sent"


@router.post("/{user_id}/initiate-email-verify")
async def initiate_email_verify(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """admin 发起邮箱认证 → 后端调 active email provider 发码给 user.email。

    用户无 email / 已认证 → 400。
    """
    target = await get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not target.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="user_no_email"
        )
    if target.email_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="email_already_verified"
        )
    await _send_verification_code(
        db, target, "email", target.email, "verify_email", user
    )
    return {"sent": True, "expires_in": 600}


@router.post("/{user_id}/initiate-phone-verify")
async def initiate_phone_verify(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """admin 发起手机认证 → 后端调 active sms provider 发码给 user.phone。

    用户无 phone / 已认证 → 400。
    """
    target = await get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not target.phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="user_no_phone"
        )
    if target.phone_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="phone_already_verified"
        )
    await _send_verification_code(
        db, target, "sms", target.phone, "verify_phone", user
    )
    return {"sent": True, "expires_in": 600}


@router.post("/{user_id}/verify-email", response_model=UserResponse)
async def verify_user_email(
    user_id: UUID,
    data: UserVerifyCode,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """admin 输入 user 收到的 6 位验证码 → 通过则置 email_verified=True。

    错 code → 400 invalid_code。
    已认证 → 200 幂等返回（不重新发码也不验证码）。
    """
    target = await get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not target.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="user_no_email"
        )
    if target.email_verified:
        # 幂等返回（已认证无需再认证）
        await db.refresh(target, ["roles"])
        return _to_response(target)

    # pre-check 查重：理论上 partial unique index 兜底，但给精确 UX
    collision = (
        await db.execute(
            select(User.id).where(
                User.email == target.email,
                User.email_verified.is_(True),
                User.id != target.id,
            )
        )
    ).scalar_one_or_none()
    if collision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="email_already_verified",
        )

    ticket = await verify_code(
        db,
        channel="email",
        target=target.email,
        purpose="verify_email",
        code=data.code,
    )
    if not ticket:
        await log_operation(
            db,
            actor_id=user.id,
            action="user.verify_email",
            target_type="user",
            target_id=target.id,
            status="failure",
            detail={"reason": "invalid_code"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_code"
        )

    target.email_verified = True
    await log_operation(
        db,
        actor_id=user.id,
        action="user.verify_email",
        target_type="user",
        target_id=target.id,
        status="success",
        detail={"email": target.email},
    )
    await db.commit()
    await db.refresh(target, ["roles"])
    return _to_response(target)


@router.post("/{user_id}/verify-phone", response_model=UserResponse)
async def verify_user_phone(
    user_id: UUID,
    data: UserVerifyCode,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    """admin 输入 user 收到的 6 位验证码 → 通过则置 phone_verified=True。

    错 code → 400 invalid_code。
    已认证 → 200 幂等返回。
    """
    target = await get_user(db, user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not target.phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="user_no_phone"
        )
    if target.phone_verified:
        await db.refresh(target, ["roles"])
        return _to_response(target)

    collision = (
        await db.execute(
            select(User.id).where(
                User.phone == target.phone,
                User.phone_verified.is_(True),
                User.id != target.id,
            )
        )
    ).scalar_one_or_none()
    if collision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="phone_already_verified",
        )

    ticket = await verify_code(
        db,
        channel="sms",
        target=target.phone,
        purpose="verify_phone",
        code=data.code,
    )
    if not ticket:
        await log_operation(
            db,
            actor_id=user.id,
            action="user.verify_phone",
            target_type="user",
            target_id=target.id,
            status="failure",
            detail={"reason": "invalid_code"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_code"
        )

    target.phone_verified = True
    await log_operation(
        db,
        actor_id=user.id,
        action="user.verify_phone",
        target_type="user",
        target_id=target.id,
        status="success",
        detail={"phone": target.phone},
    )
    await db.commit()
    await db.refresh(target, ["roles"])
    return _to_response(target)
