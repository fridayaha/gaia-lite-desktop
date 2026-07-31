"""Hub 能力中心反向代理 —— manager 代 admin 前端访问 hub。

架构：admin 前端 → ingress /api/hub/* → manager（本代理）→ hub:8003。
manager 校验 admin JWT，把当前用户身份映射成 hub 的 X-* 头注入，
hub 以 ``HUB_AUTH_MODE=header`` 信任这些头做 RBAC。

为什么不在 ingress 直连 hub：hub 经 ingress 公网暴露，header 模式需要可信
上游注入 X-Roles；ingress 层无鉴权，直连等于无人注入 → 所有权限端点 403。
manager 是已鉴权的服务端，适合承担「JWT → X-* 头」的翻译。

角色映射（manager 角色 → hub 角色，rbac.py ROLE_PERMISSIONS）：
  - 平台管理员（is_platform_admin）→ platform_admin（hub 超级角色）
  - 其余                      → runtime_consumer（空权限，hub 侧 403 拦截）
"""
import logging

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from httpx import ConnectError, ConnectTimeout

from app.core.auth import User, get_current_user, is_platform_admin
from pkg.common.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hub-proxy"])

# 转发时剥离的请求头（hop-by-hop + 客户端不应自带的身份头，由 manager 服务端重写）
_HOP_BY_HOP = {
    "host", "content-length", "content-encoding", "transfer-encoding",
    "connection", "authorization",
}
# 客户端可能伪造的身份头 —— 一律忽略，由本代理按 JWT 服务端计算后注入
_CLIENT_IDENTITY_HEADERS = {
    "x-actor-id", "x-actor-type", "x-user-name", "x-user-email",
    "x-roles", "x-scopes", "x-groups", "x-organization-id",
    "x-workspace-id", "x-service-name",
}


def _map_hub_roles(user: User) -> list[str]:
    """manager 用户 → hub 角色列表。"""
    return ["platform_admin"] if is_platform_admin(user) else ["runtime_consumer"]


def _build_upstream_headers(request: Request, user: User) -> dict[str, str]:
    """构造转发给 hub 的请求头：保留客户端非身份头 + 注入服务端计算的身份头。"""
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk in _CLIENT_IDENTITY_HEADERS:
            continue
        headers[k] = v
    # 服务端计算的身份头（hub header 模式读取这些做 RBAC + 审计）
    headers["X-Actor-ID"] = str(user.id)
    headers["X-Actor-Type"] = "user"
    headers["X-User-Name"] = user.username or ""
    headers["X-User-Email"] = user.email or ""
    headers["X-Roles"] = ",".join(_map_hub_roles(user))
    return headers


@router.api_route(
    "/api/hub/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_hub(
    request: Request,
    path: str,
    user: User = Depends(get_current_user),
) -> Response:
    """转发 /api/hub/* 到 hub，注入 X-* 身份头。"""
    upstream_url = f"{settings.hub_base_url.rstrip('/')}/api/hub/{path}"
    query = request.url.query
    if query:
        upstream_url += f"?{query}"

    body = await request.body()
    headers = _build_upstream_headers(request, user)

    # 注意：不能用 ``async with AsyncClient()``——return StreamingResponse 后
    # async with 会立即关闭 client/resp，导致 body 流被截断（0 字节）。
    # 改为手动管理：client 在 body 迭代完毕后于 _iter() 的 finally 里关闭。
    client = httpx.AsyncClient(timeout=300.0)
    try:
        req = client.build_request(
            request.method, upstream_url, content=body, headers=headers,
        )
        resp = await client.send(req, stream=True)
    except (ConnectError, ConnectTimeout):
        await client.aclose()
        return Response(
            content='{"error":"hub not available"}',
            status_code=503,
            media_type="application/json",
        )

    # 转发响应头（剥离 hop-by-hop + content-encoding：用 aiter_bytes 解码后转发）
    resp_headers = {
        k: v for k, v in resp.headers.items()
        if k.lower() not in ("content-length", "content-encoding",
                             "transfer-encoding", "connection")
    }

    async def _iter():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(
        _iter(),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )
