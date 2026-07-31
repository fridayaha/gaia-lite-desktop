"""Skill Engine 反向代理 —— manager 代 admin 前端访问 skill-engine。

架构：admin 前端 → ingress /api/skill-engine/* → manager（本代理）→ skill-engine:8004。
manager 校验 admin JWT，把当前用户身份映射成 skill-engine 的 X-* 头注入，
skill-engine 以这些头做工作区归属和权限判断。

为什么不在 ingress 直连 skill-engine：skill-engine 经 ingress 公网暴露，header 模式
需要可信上游注入 X-Roles；ingress 层无鉴权，直连等于无人注入 → 所有权限端点 403。
manager 是已鉴权的服务端，适合承担「JWT → X-* 头」的翻译。

角色映射（manager 角色 → skill-engine 角色）：
  - 平台管理员（is_platform_admin）→ platform_admin（可看所有工作区）
  - 其余                      → contributor（只能看自己的工作区）
"""
import logging

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from httpx import ConnectError, ConnectTimeout

from app.core.auth import User, get_current_user, is_platform_admin
from pkg.common.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skill-engine-proxy"])

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


def _map_skill_engine_roles(user: User) -> list[str]:
    """manager 用户 → skill-engine 角色列表。"""
    return ["platform_admin"] if is_platform_admin(user) else ["contributor"]


def _build_upstream_headers(request: Request, user: User) -> dict[str, str]:
    """构造转发给 skill-engine 的请求头：保留客户端非身份头 + 注入服务端计算的身份头。"""
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk in _CLIENT_IDENTITY_HEADERS:
            continue
        headers[k] = v
    # 服务端计算的身份头（skill-engine header 模式读取这些做 RBAC + 归属）
    headers["X-Actor-ID"] = str(user.id)
    headers["X-Actor-Type"] = "user"
    headers["X-User-Name"] = user.username or ""
    headers["X-User-Email"] = user.email or ""
    headers["X-Roles"] = ",".join(_map_skill_engine_roles(user))
    return headers


@router.api_route(
    "/api/skill-engine/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_skill_engine(
    request: Request,
    path: str,
    user: User = Depends(get_current_user),
) -> Response:
    """转发 /api/skill-engine/* 到 skill-engine，注入 X-* 身份头。"""
    upstream_url = f"{settings.skill_engine_base_url.rstrip('/')}/api/skill-engine/{path}"
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
            content='{"error":"skill-engine not available"}',
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
