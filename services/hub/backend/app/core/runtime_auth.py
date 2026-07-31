from fastapi import Depends, HTTPException, Request
from starlette.status import HTTP_403_FORBIDDEN

from app.core.auth_context import AuthContext
from app.core.auth_middleware import get_auth_context


def _is_runtime_role(ctx: AuthContext) -> bool:
    if ctx.auth_mode == "none":
        return True
    if not ctx.is_authenticated:
        return False
    return "platform_admin" in ctx.roles or "runtime_consumer" in ctx.roles


def has_runtime_role(ctx: AuthContext) -> bool:
    return _is_runtime_role(ctx)


def has_runtime_scope(ctx: AuthContext, scope: str) -> bool:
    if not _is_runtime_role(ctx):
        return False
    if "platform_admin" in ctx.roles:
        return True
    return scope in ctx.scopes


def require_runtime_permission(scope: str, fallback_scopes: list[str] | None = None):
    required_scope = scope
    fallback = fallback_scopes or []

    def checker(request: Request) -> None:
        ctx = getattr(request.state, "auth_context", None)
        if ctx is None:
            ctx = get_auth_context()

        if ctx.auth_mode == "none":
            return

        if not _is_runtime_role(ctx):
            raise HTTPException(
                status_code=HTTP_403_FORBIDDEN,
                detail="runtime access denied: no runtime role",
            )

        if "platform_admin" in ctx.roles:
            return

        scopes_to_check = [required_scope] + fallback
        if any(s in ctx.scopes for s in scopes_to_check):
            return

        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="runtime access denied: missing required scope",
        )

    return Depends(checker)
