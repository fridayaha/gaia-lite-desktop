import contextvars
import os

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.auth_context import AuthContext

_auth_context_var: contextvars.ContextVar[AuthContext] = contextvars.ContextVar(
    "auth_context", default=AuthContext()
)


def get_auth_context() -> AuthContext:
    return _auth_context_var.get()


def set_auth_context(ctx: AuthContext) -> contextvars.Token:
    return _auth_context_var.set(ctx)


def reset_auth_context(token: contextvars.Token) -> None:
    _auth_context_var.reset(token)


def _get_mode() -> str:
    mode = os.environ.get("HUB_AUTH_MODE", "dev")
    if mode in ("dev", "header", "none"):
        return mode
    return "dev"


def _dev_admin_context() -> AuthContext:
    return AuthContext(
        actor_id="dev-admin",
        actor_type="user",
        display_name="Dev Admin",
        roles=["platform_admin"],
        is_authenticated=True,
        auth_mode="dev",
    )


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        mode = _get_mode()
        if mode == "none":
            ctx = AuthContext(auth_mode="none")
        elif mode == "dev":
            ctx = AuthContext.from_headers(request.headers, auth_mode=mode)
            if not ctx.actor_id:
                ctx = _dev_admin_context()
        else:
            ctx = AuthContext.from_headers(request.headers, auth_mode=mode)

        request.state.auth_context = ctx
        token = _auth_context_var.set(ctx)
        try:
            response = await call_next(request)
        finally:
            _auth_context_var.reset(token)
        return response
