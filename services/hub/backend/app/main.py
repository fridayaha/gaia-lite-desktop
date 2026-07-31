import sys
import time

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.api.router import api_router
from app.core.auth_middleware import AuthMiddleware, get_auth_context
from app.core.logging import log_access, setup_json_access_logger
from app.core.request_id import RequestIdMiddleware, get_request_id

if "pytest" not in sys.modules:
    setup_json_access_logger()

app = FastAPI(title="UnionAgent-Hub", version="0.1.0")
app.add_middleware(RequestIdMiddleware)
app.add_middleware(AuthMiddleware)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    start = time.monotonic()
    error_code = None
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
    except Exception:
        error_code = "internal_error"
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        if error_code is None and status_code >= 400:
            if status_code == 404:
                error_code = "not_found"
            elif status_code == 409:
                error_code = "conflict"
            elif status_code == 422:
                error_code = "validation_error"
            elif status_code >= 500:
                error_code = "server_error"
        ctx = get_auth_context()
        log_access(
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
            request_id=get_request_id(),
            error_code=error_code,
            actor_id=ctx.actor_id,
            actor_type=ctx.actor_type,
            workspace_id=ctx.workspace_id,
            organization_id=ctx.organization_id,
        )
    return response


# hub 主挂载：/api（大部分子路由通过路径内 /hub/* 段已落到 /api/hub/*，
# 如 /api/hub/items、/api/hub/versions/{id}/approve；保持 /api 以兼容既有测试）
app.include_router(api_router, prefix="/api")

# 契约 §5/§6：health 与 runtime 路由路径无 /hub 段，额外挂载 /api/hub 使其
# 也落在统一命名空间下供 Ingress /api/hub → hub 直连（health.py/runtime.py 不改）
from app.api.health import router as health_router  # noqa: E402
from app.api.runtime import router as runtime_router  # noqa: E402
app.include_router(health_router, prefix="/api/hub")
app.include_router(runtime_router, prefix="/api/hub")
