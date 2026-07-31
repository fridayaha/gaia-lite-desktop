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
