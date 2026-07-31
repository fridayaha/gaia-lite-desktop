from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pkg.common.config import settings
from pkg.common.database import get_db
from app.models import User, Role

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
# 可选 Bearer：内部令牌缺失时也不立即 401，由 user_or_internal 决定
_security_optional = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: UUID, roles: list[str] = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "roles": roles or [],
        "exp": expire,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "exp": expire,
        "jti": uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    user_id = payload.get("sub")
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


async def user_or_internal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_security_optional),
    db: AsyncSession = Depends(get_db),
) -> tuple[User | None, bool]:
    """鉴权依赖：接受 gateway 内部令牌（X-Internal-Token）或普通用户 JWT。

    返回 ``(user, is_internal)``。内部令牌命中时 ``user=None, is_internal=True``，
    调用方按 instance_id 解析 profile（不做用户级鉴权——gateway 已对 client 鉴权）。
    否则回落到 ``get_current_user`` 的 JWT 校验。
    """
    itok = request.headers.get("x-internal-token", "")
    if settings.internal_token and itok and itok == settings.internal_token:
        return None, True
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = await get_current_user(credentials, db)
    return user, False


# ── LiteLLM 权限收敛辅助 ────────────────────────────────
# 权限码：
#   litellm:model:manage  管理全局上游模型组（系统管理员/平台管理员）
#   litellm:key:manage    管理 virtual key（系统管理员/平台管理员/组管理员，范围=所属 UserGroup）
# 平台管理员及以上（litellm:model:manage）不受 UserGroup 范围限制。


def user_permission_codes(user: User) -> set[str]:
    """展开用户所有角色的权限 code。需要 User.roles 已加载且各 role.permissions 已加载。"""
    codes: set[str] = set()
    for role in getattr(user, "roles", []) or []:
        for perm in getattr(role, "permissions", []) or []:
            if perm.code:
                codes.add(perm.code)
    return codes


def is_platform_admin(user: User) -> bool:
    # 拥有 litellm:model:manage 权限，或为「系统管理员」超管角色
    if "litellm:model:manage" in user_permission_codes(user):
        return True
    return any(getattr(r, "name", "") == "系统管理员" for r in getattr(user, "roles", []) or [])


def require_permission(code: str):
    """FastAPI 依赖：要求当前用户拥有指定权限 code。

    平台管理员（litellm:model:manage 或系统管理员）拥有全部 litellm 权限。
    """

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if is_platform_admin(user):
            return user
        if code not in user_permission_codes(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权限")
        return user

    return _dep


def require_platform_admin():
    """FastAPI 依赖：要求当前用户是平台管理员（用户/角色等平台级管理用）。"""

    async def _dep(user: User = Depends(get_current_user)) -> User:
        if not is_platform_admin(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可操作"
            )
        return user

    return _dep

