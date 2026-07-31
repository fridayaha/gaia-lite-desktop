"""请求级 trace_id 跨度传播 — contextvars + FastAPI middleware。

每个请求生成/继承 X-Request-ID，注入 contextvars 供 logging formatter 读取，
并回写到响应头。asyncio + contextvars 每个 task 独立 context，无跨请求泄漏。

用法：
    from pkg.common.request_id import request_id_middleware
    request_id_middleware(app)  # 在 main.py lifespan 后调用
"""

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_MAX_LENGTH = 128

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)


def get_request_id() -> str:
    return _request_id_var.get()


def set_request_id(rid: str) -> None:
    _request_id_var.set(rid)


def _normalize_request_id(value: str | None) -> str:
    if not value or not value.strip():
        return str(uuid.uuid4())
    if len(value) > _REQUEST_ID_MAX_LENGTH:
        return str(uuid.uuid4())
    return value.strip()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER)
        request_id = _normalize_request_id(incoming)
        request.state.request_id = request_id
        _request_id_var.set(request_id)

        response = await call_next(request)
        response.headers[_REQUEST_ID_HEADER] = request_id
        return response


def request_id_middleware(app) -> None:
    """注册 RequestIdMiddleware 到 app。"""
    app.add_middleware(RequestIdMiddleware)
