import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    is_platform_admin,
    verify_password,
)
from app.core.crypto import decrypt_credential
from app.middleware.rate_limit import code_rate_limiter, rate_limiter
from app.models import EmailConfig, SmsConfig, User, VerificationCode, VerificationTicket
from app.schemas import (
    CaptchaResponse,
    ChangeEmailRequest,
    ChangePhoneRequest,
    LoginByContactRequest,
    LoginBySmsCodeRequest,
    LoginRequest,
    ResetPasswordRequest,
    TokenRefreshRequest,
    TokenResponse,
    UnlockAccountRequest,
    UserVerifyCode,
    VerificationCodeSendRequest,
    VerificationCodeSendResponse,
    VerificationCodeVerifyRequest,
    VerificationCodeVerifyResponse,
)
from app.services.audit_service import log_operation
from app.services.captcha_service import captcha_service
from app.services.email_providers import get_sender as get_email_sender
from app.services.minio_public import ensure_public_bucket
from app.services.preset_avatars import preset_paths
from app.services.sms_providers import get_sender as get_sms_sender
from app.services.verification_code_service import (
    consume_ticket,
    invalidate_target_codes,
    issue_code,
    verify_code,
)
from app.worker.minio_archiver import archiver
from pkg.common.config import settings
from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/auth", tags=["auth"])


# 假 hash 用于「用户不存在」路径的 constant-time 防侧信道：
# 跑一次 verify_password(dummy) 让响应时间与真实用户路径一致，避免探测账号是否存在。
# 启动时算一次，cost factor 与真 hash 一致（passlib 默认 12）。
_DUMMY_HASH = hash_password("dummy_for_timing_attack_defense_x9k2m")


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """登录端点（0.8.103 加固版）。

    安全策略：
    - 失败计数：连续 5 次失败 → 账号锁 15min，期间返回 423 account_locked
    - IP 限流：每分钟 10 次尝试，每小时 50 次失败拉黑 1h（middleware 实现）
    - 用户枚举防御：所有失败路径统一返回 401 invalid_credentials（不区分 reason）
    - 防侧信道：用户不存在时跑假 hash 校验，响应时间与真实用户一致
    - 审计：所有失败路径记 audit（reason=user_not_found/invalid_password/user_disabled/
      account_locked_attempt/locked_after_5_failures），actor_id 可为 null
    - 成功：reset 失败计数 + 落 last_login_at/ip/user_agent
    """
    # 提取客户端 IP + UA（middleware 已 set contextvar，这里再取一次用于 last_login 落库）
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.headers.get("X-Real-IP", "").strip()
          or (request.client.host if request.client else "unknown"))
    ua = request.headers.get("User-Agent", "")[:256]

    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.username == data.username)
    )
    user = result.scalar_one_or_none()

    # 用户不存在：跑假校验防侧信道 + 记 audit（actor_id=null）+ IP 限流计数 +1 + 401 generic
    if not user:
        verify_password(data.password, _DUMMY_HASH)  # 假校验，恒 False 但耗时同真实路径
        await log_operation(
            db,
            actor_id=None,
            action="auth.login",
            target_type="user",
            target_id=None,
            status="failure",
            detail={"username": data.username, "reason": "user_not_found"},
        )
        await db.commit()
        await rate_limiter.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    now = datetime.now(timezone.utc)

    # 账号锁定检查：返回 423 + Retry-After（不消费密码，不增计数）
    if user.locked_until and user.locked_until > now:
        wait_seconds = int((user.locked_until - now).total_seconds())
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"username": data.username, "reason": "account_locked_attempt"},
        )
        await db.commit()
        raise HTTPException(
            status_code=423,
            detail="account_locked",
            headers={"Retry-After": str(wait_seconds)},
        )

    # captcha 条件触发：failed_login_count >= 2 后才要求（前端默认不显示 UI，收到 400 captcha_required 后才显示）
    # 校验失败不调 rate_limiter.record_failure（避免消耗 IP 限流配额）+ 不写 audit（不算真正登录尝试）+ 不增 failed_login_count
    if (user.failed_login_count or 0) >= 2:
        if not data.captcha_id or not data.captcha_answer:
            raise HTTPException(status_code=400, detail="captcha_required")
        if not await captcha_service.verify(data.captcha_id, data.captcha_answer):
            raise HTTPException(status_code=400, detail="captcha_invalid")

    # 用户禁用：改 401 invalid_credentials（消除枚举向量），审计仍记真实 reason
    if not user.is_active:
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"username": data.username, "reason": "user_disabled"},
        )
        await db.commit()
        await rate_limiter.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    # 密码校验：错 → 失败计数 +1，达 5 次锁 15min
    if not verify_password(data.password, user.hashed_password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        reason = "invalid_password"
        if user.failed_login_count >= 5:
            user.locked_until = now + timedelta(minutes=15)
            user.failed_login_count = 0  # 锁定期间归零，解锁后重新计数
            reason = "locked_after_5_failures"
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={
                "username": data.username,
                "reason": reason,
                "failed_count": user.failed_login_count,
            },
        )
        await db.commit()
        await rate_limiter.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    # 成功：reset 计数 + 落 last_login
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip
    user.last_login_user_agent = ua
    role_names = [r.name for r in user.roles]
    await log_operation(
        db,
        actor_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        detail={"username": data.username, "roles": role_names},
    )
    await db.commit()
    await db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id, role_names),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login-by-contact", response_model=TokenResponse)
async def login_by_contact(
    data: LoginByContactRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """已认证邮箱/手机 + 密码登录（0.8.116）。

    仅查 email_verified=True / phone_verified=True 的用户——未认证联系方式不能用于登录。
    复用 login 的失败计数 + IP 限流 + 锁定 + 审计逻辑。
    用户不存在/密码错/禁用统一 401 invalid_credentials（防枚举）。
    """
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.headers.get("X-Real-IP", "").strip()
          or (request.client.host if request.client else "unknown"))
    ua = request.headers.get("User-Agent", "")[:256]

    if data.contact_type == "email":
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(
                User.email == data.contact, User.email_verified.is_(True)
            )
        )
    else:  # phone
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(
                User.phone == data.contact, User.phone_verified.is_(True)
            )
        )
    user = result.scalar_one_or_none()

    # 用户不存在：跑假校验 + 记 audit + 401（不区分 reason 防枚举）
    if not user:
        verify_password(data.password, _DUMMY_HASH)
        await log_operation(
            db,
            actor_id=None,
            action="auth.login_by_contact",
            target_type="user",
            target_id=None,
            status="failure",
            detail={"contact_type": data.contact_type, "reason": "user_not_found"},
        )
        await db.commit()
        await rate_limiter.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    now = datetime.now(timezone.utc)

    # 账号锁定：返回 423 + Retry-After
    if user.locked_until and user.locked_until > now:
        wait_seconds = int((user.locked_until - now).total_seconds())
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login_by_contact",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"contact_type": data.contact_type, "reason": "account_locked_attempt"},
        )
        await db.commit()
        raise HTTPException(
            status_code=423,
            detail="account_locked",
            headers={"Retry-After": str(wait_seconds)},
        )

    # captcha 条件触发：failed_login_count >= 2 后才要求（同 /login 逻辑）
    if (user.failed_login_count or 0) >= 2:
        if not data.captcha_id or not data.captcha_answer:
            raise HTTPException(status_code=400, detail="captcha_required")
        if not await captcha_service.verify(data.captcha_id, data.captcha_answer):
            raise HTTPException(status_code=400, detail="captcha_invalid")

    # 用户禁用：401 invalid_credentials（消除枚举向量）
    if not user.is_active:
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login_by_contact",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"contact_type": data.contact_type, "reason": "user_disabled"},
        )
        await db.commit()
        await rate_limiter.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    # 密码校验：错 → 失败计数 +1，达 5 次锁 15min
    if not verify_password(data.password, user.hashed_password):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        reason = "invalid_password"
        if user.failed_login_count >= 5:
            user.locked_until = now + timedelta(minutes=15)
            user.failed_login_count = 0
            reason = "locked_after_5_failures"
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login_by_contact",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={
                "contact_type": data.contact_type,
                "reason": reason,
                "failed_count": user.failed_login_count,
            },
        )
        await db.commit()
        await rate_limiter.record_failure(ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    # 成功：reset 计数 + 落 last_login
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip
    user.last_login_user_agent = ua
    role_names = [r.name for r in user.roles]
    await log_operation(
        db,
        actor_id=user.id,
        action="auth.login_by_contact",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"contact_type": data.contact_type, "roles": role_names},
    )
    await db.commit()
    await db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id, role_names),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login-by-sms-code", response_model=TokenResponse)
async def login_by_sms_code(
    data: LoginBySmsCodeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """已认证手机号 + 验证码登录（0.8.116，无需密码）。

    code 通过 verify_code 验证即证明手机所有权——不需要密码。
    code 错 → 401 invalid_code；user 不存在（phone_verified=False）→ 401 invalid_credentials（防御性兜底）。
    复用 login 的锁定 + 禁用检查 + 审计。
    """
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.headers.get("X-Real-IP", "").strip()
          or (request.client.host if request.client else "unknown"))
    ua = request.headers.get("User-Agent", "")[:256]

    # 先验证 code（成功会消费 code + 生成无用的 ticket，接受这个副作用）
    ticket_id = await verify_code(
        db,
        channel="sms",
        target=data.phone,
        purpose="login",
        code=data.code,
    )
    if not ticket_id:
        await log_operation(
            db,
            actor_id=None,
            action="auth.login_by_sms_code",
            target_type="user",
            target_id=None,
            status="failure",
            detail={"phone": data.phone, "reason": "invalid_code"},
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_code")

    # 通过则查 phone_verified=True 的 user
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(
            User.phone == data.phone, User.phone_verified.is_(True)
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        # code 对了但 phone 没有 verified user？几乎不可能（code 只发给 verified user 的 phone）
        # 作为防御性兜底
        await log_operation(
            db,
            actor_id=None,
            action="auth.login_by_sms_code",
            target_type="user",
            target_id=None,
            status="failure",
            detail={"phone": data.phone, "reason": "user_not_found"},
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    now = datetime.now(timezone.utc)

    # 账号锁定检查
    if user.locked_until and user.locked_until > now:
        wait_seconds = int((user.locked_until - now).total_seconds())
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login_by_sms_code",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"reason": "account_locked_attempt"},
        )
        await db.commit()
        raise HTTPException(
            status_code=423,
            detail="account_locked",
            headers={"Retry-After": str(wait_seconds)},
        )
    if not user.is_active:
        await log_operation(
            db,
            actor_id=user.id,
            action="auth.login_by_sms_code",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"reason": "user_disabled"},
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    # 成功：reset 计数 + 落 last_login（不需要密码，code 已证明所有权）
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    user.last_login_ip = ip
    user.last_login_user_agent = ua
    role_names = [r.name for r in user.roles]
    await log_operation(
        db,
        actor_id=user.id,
        action="auth.login_by_sms_code",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"phone": data.phone, "roles": role_names},
    )
    await db.commit()
    await db.refresh(user)
    return TokenResponse(
        access_token=create_access_token(user.id, role_names),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: TokenRefreshRequest, db: AsyncSession = Depends(get_db)):
    payload = decode_token(data.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    role_names = [r.name for r in user.roles]
    # auth.refresh 不写 operation_log：前端 access_token 30min 过期时由 axios 拦截器自动调，
    # 用户不感知、对系统数据无影响（只发新 token），高频且无业务语义，按"用户感知"原则过滤。
    await db.commit()
    return TokenResponse(
        access_token=create_access_token(user.id, role_names),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    return {
        "id": str(user.id),
        "username": user.username,
        "real_name": user.real_name,
        "nickname": user.real_name,  # 前端 alias（前端 UserInfo.nickname 字段映射到此）
        "email": user.email,
        "phone": user.phone,
        "email_verified": user.email_verified or False,
        "phone_verified": user.phone_verified or False,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "roles": [r.name for r in user.roles],
        "is_platform_admin": is_platform_admin(user),
    }


class UserSelfUpdate(BaseModel):
    real_name: Optional[str] = Field(None, max_length=128)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=32)
    avatar_url: Optional[str] = Field(None, max_length=512)


def _user_dict(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "real_name": user.real_name,
        "nickname": user.real_name,
        "email": user.email,
        "phone": user.phone,
        "email_verified": user.email_verified or False,
        "phone_verified": user.phone_verified or False,
        "avatar_url": user.avatar_url,
        "is_active": user.is_active,
        "roles": [r.name for r in user.roles],
        "is_platform_admin": is_platform_admin(user),
    }


@router.patch("/me")
async def update_me(
    data: UserSelfUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """自服务改资料：real_name / email / phone / avatar_url。

    email 改动：若 new_email 已被其他用户认证占用（email_verified=True），拒绝（409）。
    未认证 email 可多人重，不冲突。改 email 后回退 email_verified=False。
    phone 同理。
    """
    if data.email is not None and data.email != user.email:
        exists = await db.scalar(
            select(User).where(
                User.email == data.email,
                User.email_verified.is_(True),
                User.id != user.id,
            )
        )
        if exists:
            raise HTTPException(status_code=409, detail="email_already_used")
    if data.phone is not None and data.phone != user.phone:
        exists = await db.scalar(
            select(User).where(
                User.phone == data.phone,
                User.phone_verified.is_(True),
                User.id != user.id,
            )
        )
        if exists:
            raise HTTPException(status_code=409, detail="phone_already_used")
    changed_fields = list(data.model_dump(exclude_unset=True).keys())
    if data.email is not None and data.email != user.email:
        user.email_verified = False
    if data.phone is not None and data.phone != user.phone:
        user.phone_verified = False
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    await log_operation(
        db,
        actor_id=user.id,
        action="user.self_update",
        target_type="user",
        target_id=user.id,
        detail={"fields": changed_fields},
    )
    await db.commit()
    return {"code": 0, "message": "ok", "data": _user_dict(user)}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_strength(cls, v: str) -> str:
        from app.schemas import _validate_password_strength
        return _validate_password_strength(v)


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """自服务改密码：校验 old 通过后写新 hash。token 不主动失效（维持现状）。"""
    if not verify_password(data.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="wrong_old_password")
    user.hashed_password = hash_password(data.new_password)
    await log_operation(
        db,
        actor_id=user.id,
        action="user.change_password",
        target_type="user",
        target_id=user.id,
        detail={},
    )
    await db.commit()
    return {"code": 0, "message": "ok"}


@router.post("/logout")
async def logout(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """记录登出操作。token 不主动失效（维持现状），仅写审计日志供安全日志展示。"""
    await log_operation(
        db,
        actor_id=user.id,
        action="auth.logout",
        target_type="user",
        target_id=user.id,
        detail={},
    )
    await db.commit()
    return {"code": 0, "message": "ok"}


ALLOWED_AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2MB


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传头像到 MinIO public bucket，返回 URL 并写入 user.avatar_url。"""
    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(status_code=413, detail="avatar_too_large")
    if file.content_type not in ALLOWED_AVATAR_TYPES:
        raise HTTPException(status_code=415, detail="avatar_unsupported_type")

    public_bucket = settings.minio_public_bucket
    client = archiver.client
    # 首次访问：创建 bucket + set public-read policy（其它对象仍走主 bucket 保持私有）
    await ensure_public_bucket()

    ext = (file.filename.rsplit(".", 1)[-1] if file.filename else "png").lower()
    object_key = f"avatars/{user.id}/{uuid.uuid4().hex}.{ext}"
    await asyncio.to_thread(
        client.put_object,
        bucket_name=public_bucket,
        object_name=object_key,
        data=BytesIO(content),
        length=len(content),
        content_type=file.content_type or "image/png",
    )
    # 存相对路径：浏览器经 admin nginx /avatars/ 反代访问 minio，避免 nip.io host 公网不可达
    url = f"/avatars/{public_bucket}/{object_key}"
    user.avatar_url = url
    await log_operation(
        db,
        actor_id=user.id,
        action="user.update_avatar",
        target_type="user",
        target_id=user.id,
        detail={"avatar_url": url},
    )
    await db.commit()
    return {"code": 0, "message": "ok", "data": {"avatar_url": url}}


@router.get("/preset-avatars")
async def list_preset_avatars(user: User = Depends(get_current_user)):
    """返回 12 个预置卡通头像相对路径，前端在 Profile 页选择。"""
    return {"code": 0, "message": "ok", "data": {"items": preset_paths()}}


@router.get("/verification-channels")
async def get_verification_channels(db: AsyncSession = Depends(get_db)):
    """登录页 / 忘记密码页查询当前激活的发码渠道（0.8.116）。

    公开 endpoint，无 Bearer token——登录页未登录时调用。
    返回 {email: bool, sms: bool}——前端按渠道是否开启条件渲染
    「邮箱找回 / 短信找回」radio、「手机验证码登录」tab。
    """
    sms_active = (await db.execute(
        select(SmsConfig.id).where(SmsConfig.is_active.is_(True)).limit(1)
    )).scalar_one_or_none() is not None
    email_active = (await db.execute(
        select(EmailConfig.id).where(EmailConfig.is_active.is_(True)).limit(1)
    )).scalar_one_or_none() is not None
    return {"email": email_active, "sms": sms_active}


# =========================================
# Phase 1（0.8.104+）验证码 + 改绑 endpoint
# 场景：忘记密码 / 改绑手机邮箱 / 账号锁定邮件解锁
# 安全：图形验证码 + 限速 + 用户枚举防御 + ticket 单次使用
# =========================================


async def _get_active_config(db: AsyncSession, channel: str):
    """取 active 的 SmsConfig / EmailConfig 行（全局仅一行，partial unique index 保证）。
    无配置返回 None。"""
    if channel == "sms":
        stmt = select(SmsConfig).where(SmsConfig.is_active.is_(True))
    else:
        stmt = select(EmailConfig).where(EmailConfig.is_active.is_(True))
    return (await db.execute(stmt)).scalar_one_or_none()


def _decrypt_secrets(cfg) -> dict:
    """解密 active provider 的 AK/SK 或 SMTP 密码。

    云厂商（aliyun/tencent/huawei）走 access_key_id_encrypted / access_key_secret_encrypted；
    SMTP 走 password_encrypted。
    """
    if hasattr(cfg, "access_key_id_encrypted") and cfg.access_key_id_encrypted:
        return {
            "access_key_id": decrypt_credential(cfg.access_key_id_encrypted),
            "access_key_secret": decrypt_credential(cfg.access_key_secret_encrypted),
        }
    if hasattr(cfg, "password_encrypted") and cfg.password_encrypted:
        return {"password": decrypt_credential(cfg.password_encrypted)}
    raise Exception("provider secrets not configured")


@router.get("/captcha", response_model=CaptchaResponse)
async def get_captcha():
    """生成图形验证码。返回 captcha_id + base64 PNG 图片。

    5min 有效，1 次性使用（错即失效）。无认证 — 公开 endpoint。
    """
    captcha_id, image_b64 = await captcha_service.generate()
    return CaptchaResponse(captcha_id=captcha_id, image_base64=image_b64)


@router.post("/verification-code/send", response_model=VerificationCodeSendResponse)
async def send_verification_code(
    data: VerificationCodeSendRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """发码 endpoint — 先校验图形验证码 + 限速，再调 active provider 发码 + 落库。

    用户枚举防御：不存在的 target 也返回 sent=true，但实际不发码。
    无 active provider 也返回 sent=true，避免依赖配置的用户被锁出。
    无认证 — 公开 endpoint。
    """
    ip = request.client.host if request.client else "unknown"

    # 1. 校验图形验证码（1 次性使用）
    if not await captcha_service.verify(data.captcha_id, data.captcha_answer):
        raise HTTPException(status_code=400, detail="captcha_invalid")

    # 2. 查 target 是否存在（reset_password / account_unlock 需校验 user）
    # 0.8.110 两态模型：未认证 email/phone 不可用于找回密码/账号解锁 — 加 verified=True 过滤
    user = None
    if data.purpose in ("reset_password", "account_unlock"):
        if data.channel == "email":
            user = (
                await db.execute(
                    select(User).where(
                        User.email == data.target,
                        User.email_verified.is_(True),
                    )
                )
            ).scalar_one_or_none()
        else:
            user = (
                await db.execute(
                    select(User).where(
                        User.phone == data.target,
                        User.phone_verified.is_(True),
                    )
                )
            ).scalar_one_or_none()

    # 3. 限速（不论 target 是否存在都查，防探测 + 防轰炸不存在账号）
    cfg = await _get_active_config(db, data.channel)
    daily_limit = cfg.daily_limit if cfg else 10
    interval = cfg.interval_seconds if cfg else 60
    await code_rate_limiter.check_send(data.target, ip, daily_limit, interval)

    # 4. 不存在的 target → 假装成功，记 audit，但不发码
    if not user and data.purpose in ("reset_password", "account_unlock"):
        await log_operation(
            db,
            actor_id=None,
            action="auth.verification_code.send",
            target_type="user",
            target_id=None,
            status="failure",
            detail={
                "channel": data.channel,
                "target": data.target,
                "purpose": data.purpose,
                "reason": "user_not_found",
            },
        )
        await db.commit()
        return VerificationCodeSendResponse(sent=True, expires_in=600)

    # 5. 拿 active provider 的 send 函数；没配置也假装成功
    if cfg:
        sender = (
            get_sms_sender(cfg.provider)
            if data.channel == "sms"
            else get_email_sender(cfg.provider)
        )
    else:
        sender = None
    if not sender:
        await log_operation(
            db,
            actor_id=user.id if user else None,
            action="auth.verification_code.send",
            target_type="user",
            target_id=user.id if user else None,
            status="failure",
            detail={
                "channel": data.channel,
                "target": data.target,
                "purpose": data.purpose,
                "reason": "no_active_provider",
            },
        )
        await db.commit()
        return VerificationCodeSendResponse(sent=True, expires_in=600)

    # 6. 生成 + 落库 + 发码
    # 缓存 user_id 在 rollback 前 — rollback 会 expire ORM 对象，之后访问 user.id
    # 会触发 sync lazy refresh → MissingGreenlet（async session 不允许 sync IO）
    user_id = user.id if user else None
    code = await issue_code(
        db,
        channel=data.channel,
        target=data.target,
        purpose=data.purpose,
        ip=ip,
    )
    try:
        if data.channel == "sms":
            await asyncio.to_thread(
                sender, cfg, _decrypt_secrets(cfg), data.target, {"code": code}
            )
        else:
            subject = "知行平台验证码"
            html = (
                f"<p>您的验证码是：<strong>{code}</strong>，"
                "10 分钟内有效。</p>"
                "<p>如果不是您本人操作，请忽略此邮件。</p>"
            )
            await asyncio.to_thread(
                sender, cfg, _decrypt_secrets(cfg), data.target, subject, html
            )
    except Exception as e:
        await db.rollback()
        # 失败时 rollback code 记录，返回 503 让用户重试
        await log_operation(
            db,
            actor_id=user_id,
            action="auth.verification_code.send",
            target_type="user",
            target_id=user_id,
            status="failure",
            detail={
                "channel": data.channel,
                "target": data.target,
                "purpose": data.purpose,
                "reason": "provider_send_failed",
                "error": str(e)[:200],
            },
        )
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="code_send_failed",
            headers={"Retry-After": "60"},
        )

    await log_operation(
        db,
        actor_id=user_id,
        action="auth.verification_code.send",
        target_type="user",
        target_id=user_id,
        status="success",
        detail={
            "channel": data.channel,
            "target": data.target,
            "purpose": data.purpose,
        },
    )
    await db.commit()
    return VerificationCodeSendResponse(sent=True, expires_in=600)


@router.post("/verification-code/verify", response_model=VerificationCodeVerifyResponse)
async def verify_verification_code(
    data: VerificationCodeVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """校验验证码 — 成功返回 ticket UUID，失败 401 invalid_code（不区分 reason）。

    ticket 用于后续 reset-password / unlock-account / change-email / change-phone。
    无认证 — 公开 endpoint。
    """
    ticket = await verify_code(
        db,
        channel=data.channel,
        target=data.target,
        purpose=data.purpose,
        code=data.code,
    )
    if not ticket:
        await log_operation(
            db,
            actor_id=None,
            action="auth.verification_code.verify",
            target_type="user",
            target_id=None,
            status="failure",
            detail={
                "channel": data.channel,
                "target": data.target,
                "purpose": data.purpose,
            },
        )
        await db.commit()
        raise HTTPException(status_code=401, detail="invalid_code")

    await log_operation(
        db,
        actor_id=None,
        action="auth.verification_code.verify",
        target_type="user",
        target_id=None,
        status="success",
        detail={
            "channel": data.channel,
            "target": data.target,
            "purpose": data.purpose,
        },
    )
    await db.commit()
    return VerificationCodeVerifyResponse(verified=True, ticket=ticket)


@router.post("/reset-password")
async def reset_password(
    data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """用 ticket 重置密码。ticket 单次使用，10min 有效，purpose=reset_password。

    用 ticket.target 反查 user（不信任前端传的 target）。
    reset_password 可能用 email 或 phone 发码，先按 email 查，找不到按 phone 查。
    无认证 — 公开 endpoint。
    """
    ticket = await consume_ticket(db, data.ticket, purpose="reset_password")
    if not ticket:
        raise HTTPException(status_code=401, detail="ticket_invalid")

    # 用 ticket.target 反查 user
    user = (
        await db.execute(select(User).where(User.email == ticket.target))
    ).scalar_one_or_none()
    if not user:
        # reset_password 可能用手机号发码，按 phone 试一下
        user = (
            await db.execute(select(User).where(User.phone == ticket.target))
        ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="ticket_invalid")

    user.hashed_password = hash_password(data.new_password)
    await log_operation(
        db,
        actor_id=user.id,
        action="auth.reset_password",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"target": ticket.target},
    )
    await db.commit()
    return {"ok": True}


@router.post("/unlock-account")
async def unlock_account(
    data: UnlockAccountRequest,
    db: AsyncSession = Depends(get_db),
):
    """用 ticket 解锁账号 — 清空 failed_login_count + locked_until。

    ticket 单次使用，10min 有效，purpose=account_unlock。
    account_unlock 仅走 email 渠道（锁定时不知道用户名，只能用 email）。
    无认证 — 公开 endpoint。
    """
    ticket = await consume_ticket(db, data.ticket, purpose="account_unlock")
    if not ticket:
        raise HTTPException(status_code=401, detail="ticket_invalid")

    user = (
        await db.execute(select(User).where(User.email == ticket.target))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="ticket_invalid")

    user.failed_login_count = 0
    user.locked_until = None
    await log_operation(
        db,
        actor_id=user.id,
        action="auth.unlock_account",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"target": ticket.target},
    )
    await db.commit()
    return {"ok": True}


@router.post("/me/change-email")
async def change_my_email(
    data: ChangeEmailRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """改自己邮箱。先发码到 new_email (purpose=change_email)，用户输入 code 调本 endpoint。

    new_email 不能被其他用户已认证占用（409 email_in_use）；未认证 email 可多人重不冲突。
    改绑后旧 email 上所有未消费 code 立即失效（防改完一个再改一个）。
    改绑后 email_verified=False，需重新认证（按 0.8.110 D5）。
    需 Bearer token — 已登录 endpoint。
    """
    existing = (
        await db.execute(
            select(User).where(
                User.email == data.new_email,
                User.email_verified.is_(True),
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="email_in_use")

    # 校验 code（不调 verify_code 而是直接查 DB，因为本 endpoint 需要 ticket 而非消费完返回）
    if not await verify_code(
        db,
        channel="email",
        target=data.new_email,
        purpose="change_email",
        code=data.code,
    ):
        raise HTTPException(status_code=401, detail="invalid_code")

    # 失效旧 email 的所有 code（防改完一个再改一个）
    if user.email:
        await invalidate_target_codes(db, user.email)

    old_email = user.email
    user.email = data.new_email
    user.email_verified = False  # 改绑后回退，需重新认证
    await log_operation(
        db,
        actor_id=user.id,
        action="user.change_email",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"old": old_email, "new": data.new_email, "verified_reset": True},
    )
    await db.commit()
    return {"ok": True}


@router.post("/me/change-phone")
async def change_my_phone(
    data: ChangePhoneRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """改自己手机。先发码到 new_phone (purpose=change_phone)，用户输入 code 调本 endpoint。

    new_phone 不能被其他用户已认证占用（409 phone_in_use）；未认证 phone 可多人重。
    改绑后旧 phone 上所有未消费 code 立即失效。
    改绑后 phone_verified=False，需重新认证（按 0.8.110 D5）。
    需 Bearer token — 已登录 endpoint。
    """
    existing = (
        await db.execute(
            select(User).where(
                User.phone == data.new_phone,
                User.phone_verified.is_(True),
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="phone_in_use")

    if not await verify_code(
        db,
        channel="sms",
        target=data.new_phone,
        purpose="change_phone",
        code=data.code,
    ):
        raise HTTPException(status_code=401, detail="invalid_code")

    if user.phone:
        await invalidate_target_codes(db, user.phone)

    old_phone = user.phone
    user.phone = data.new_phone
    user.phone_verified = False  # 改绑后回退
    await log_operation(
        db,
        actor_id=user.id,
        action="user.change_phone",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"old": old_phone, "new": data.new_phone, "verified_reset": True},
    )
    await db.commit()
    return {"ok": True}


@router.post("/me/verify-email")
async def verify_my_email(
    data: UserVerifyCode,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """自服务邮箱认证 — 先用公开 /verification-code/send 发码到 user.email
    (purpose=verify_email)，用户输入 code 调本 endpoint。

    无 email → 400 user_no_email；已认证 → 200 幂等；错 code → 400 invalid_code；
    被其他用户认证占用 → 409 email_already_verified。
    """
    if not user.email:
        raise HTTPException(status_code=400, detail="user_no_email")
    if user.email_verified:
        return {"ok": True, "email_verified": True}
    collision = (
        await db.execute(
            select(User.id).where(
                User.email == user.email,
                User.email_verified.is_(True),
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()
    if collision:
        raise HTTPException(status_code=409, detail="email_already_verified")

    ticket_id = await verify_code(
        db,
        channel="email",
        target=user.email,
        purpose="verify_email",
        code=data.code,
    )
    if not ticket_id:
        await log_operation(
            db,
            actor_id=user.id,
            action="user.verify_email",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"reason": "invalid_code"},
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="invalid_code")

    user.email_verified = True
    await log_operation(
        db,
        actor_id=user.id,
        action="user.verify_email",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"email": user.email},
    )
    await db.commit()
    return {"ok": True, "email_verified": True}


@router.post("/me/verify-phone")
async def verify_my_phone(
    data: UserVerifyCode,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """自服务手机认证 — 流程同 verify-email，channel=sms。"""
    if not user.phone:
        raise HTTPException(status_code=400, detail="user_no_phone")
    if user.phone_verified:
        return {"ok": True, "phone_verified": True}
    collision = (
        await db.execute(
            select(User.id).where(
                User.phone == user.phone,
                User.phone_verified.is_(True),
                User.id != user.id,
            )
        )
    ).scalar_one_or_none()
    if collision:
        raise HTTPException(status_code=409, detail="phone_already_verified")

    ticket_id = await verify_code(
        db,
        channel="sms",
        target=user.phone,
        purpose="verify_phone",
        code=data.code,
    )
    if not ticket_id:
        await log_operation(
            db,
            actor_id=user.id,
            action="user.verify_phone",
            target_type="user",
            target_id=user.id,
            status="failure",
            detail={"reason": "invalid_code"},
        )
        await db.commit()
        raise HTTPException(status_code=400, detail="invalid_code")

    user.phone_verified = True
    await log_operation(
        db,
        actor_id=user.id,
        action="user.verify_phone",
        target_type="user",
        target_id=user.id,
        status="success",
        detail={"phone": user.phone},
    )
    await db.commit()
    return {"ok": True, "phone_verified": True}
