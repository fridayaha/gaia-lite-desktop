"""UnionAgents Gateway — reverse proxy + IM channel integration.

Routing:
- /v1/chat/completions, /v1/models, /v1/sessions(*), /v1/files → 显式 adapter 路由
- /{path:path} → catch-all 兜底代理（同样走 adapter 管线）
- /api/gateway/channel/{channel_type}/{agent_id}/callback → IM platform webhooks
- /health → Health check

adapter 按 X-Engine-Type 选（HERMES / OPENCLAW / DIFY），负责协议归一化；
gateway 负责路由解析 / 鉴权 / 缓存（契约 §4）。
"""
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from prometheus_fastapi_instrumentator import Instrumentator

from app.proxy import proxy_handler, security, verify_token
from app.settings import settings
from pkg.common.access_log import log_request, setup_access_log
from pkg.common.logging import setup_json_logger
from pkg.common.request_id import RequestIdMiddleware, set_request_id
from pkg.common.security import (
    assert_api_key_hmac_secret,
    assert_production_secrets,
    configure_cors,
    parse_cors_origins,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # JSON logging + request_id middleware（Promtail json stage 解析后入 Loki）
    setup_json_logger("gateway", level=getattr(settings, "log_level", "INFO"))
    setup_access_log("gateway")
    # 生产环境密钥 fail-fast 校验（dev 跳过）
    assert_production_secrets(settings.jwt_secret, settings.environment)
    assert_api_key_hmac_secret(settings.api_key_hmac_secret, settings.environment)
    logging.getLogger(__name__).info("Gateway starting up...")

    # Startup: ensure DB tables exist for channel config reading
    try:
        from app.models import ensure_tables
        await ensure_tables()
    except Exception:
        pass  # Non-fatal; Gateway can still proxy without channel support
    yield


app = FastAPI(
    title="UnionAgents-Gateway",
    version="0.9.2",
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """接口日志：method/path/status/duration_ms/request_id → JSON → Loki。

    注：Starlette middleware 是 LIFO（后注册的最外层先执行），这里显式 set_request_id
    兜底，无论 middleware 顺序如何都能拿到 request_id。
    """
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming.strip() or uuid.uuid4().hex[:16]
    set_request_id(rid)
    request.state.request_id = rid

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    user_id = None
    try:
        authz = request.headers.get("Authorization", "")
        if authz.startswith("Bearer "):
            cred = authz[7:]
            if cred.startswith("sk-"):
                # API Key 路径：记 prefix 不记明文，方便日志检索
                user_id = f"apikey:{cred[:14]}"
            else:
                payload = verify_token(HTTPAuthorizationCredentials(
                    scheme="Bearer", credentials=cred
                ))
                user_id = payload.get("sub") if payload else None
    except Exception:
        pass
    log_request(
        "gateway",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id=rid,
        user_id=user_id,
    )
    response.headers["X-Request-ID"] = rid
    return response


# Prometheus /metrics 端点
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
configure_cors(app, parse_cors_origins(settings.cors_origins))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "unionagents-gateway", "version": "0.9.2"}


# ── Channel Webhook Router (no JWT required — verified by adapter signature) ──
try:
    from app.channel.router import router as channel_router
    app.include_router(channel_router, prefix="/api/gateway/channel")
except ImportError as e:
    import logging
    logging.getLogger(__name__).warning("Channel router not loaded: %s", e)

# ── 终端用户语音转写（JWT 鉴权，不走引擎代理）──
# 双注册：直连（本地 dev / port-forward）走 /v1/*；生产经 landing nginx 不剥前缀，
# 流量带 /api/gateway 前缀到达（同 catch-all 里 proxy_handler 的剥前缀约定）。
from app.asr.api import router as asr_router
app.include_router(asr_router)
app.include_router(asr_router, prefix="/api/gateway")


# ── WeCom AI Bot WS 透明桥接（wecom_bot channel_type，无 JWT — 内网 Profile）──
@app.websocket("/api/gateway/channel/wecom_bot/{agent_id}/ws")
async def wecom_bot_ws(ws: WebSocket, agent_id: str):
    """WeCom AI Bot WS 桥接：Profile ↔ gateway ↔ 企微 openws，1:1 透传。

    与 callback（HTTP webhook）不同，Bot 模式是 WS 长连接透传，不走 dispatcher。
    Profile 在 WS 内发 aibot_subscribe（含 bot_id+secret）完成企微鉴权，gateway 只桥接。
    """
    from app.channel.wecom_bot import bridge_bot_ws
    from app.models import get_channel_config

    config = await get_channel_config(agent_id, "wecom_bot") or {}
    bot_id = config.get("bot_id", "")
    await ws.accept()
    await bridge_bot_ws(ws, settings.wecom_openws_url, bot_id)


# ── 浏览器沙箱 VNC WS 桥（终端用户 noVNC ↔ browser Pod KasmVNC）──
# 路径带 /api/gateway 前缀：chat-ingress 不剥前缀，gateway 收到的就是 /api/gateway/...
# （同 wecom_bot WS 路由 /api/gateway/channel/... 的模式）
@app.websocket("/api/gateway/v1/browser/{agent_id}/vnc")
async def browser_vnc_ws(ws: WebSocket, agent_id: str, token: str = Query(...)):
    """浏览器沙箱 VNC 桥：终端用户 noVNC（canvas）↔ browser Pod:6901/websockify。

    鉴权：JWT 经 ?token= query（浏览器原生 WS 不能设 header，同 deploy-events SSE 先例）。
    profile_resolver.resolve_browser_target 做组隔离校验 + 解析 profile_name + browser Pod +
    vnc_pw（从 internal_port_map["browsers"][profile] 取，gateway 不调 k8s API）。
    bridge_vnc_ws 注入 Basic auth + Origin + binary 子协议，1:1 二进制透传。
    """
    from app.profile_resolver import AccessDenied, ProfileNotFound, profile_resolver

    # 1. JWT 鉴权（query token）
    try:
        payload = verify_token(
            HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        )
    except Exception:
        await ws.close(code=4401)  # 未授权
        return
    user_id = payload.get("sub")
    roles = payload.get("roles") or []
    is_admin = "平台管理员" in roles or "系统管理员" in roles

    # 2. 解析 browser Pod（含组隔离校验）
    try:
        _profile, browser_pod, vnc_pw = await profile_resolver.resolve_browser_target(
            user_id, agent_id, is_admin
        )
    except AccessDenied:
        await ws.close(code=4403)
        return
    except ProfileNotFound:
        await ws.close(code=4404)  # 沙箱未启用 / browser Pod 未建 / agent 不存在
        return

    # 3. 桥接（accept 后双向透传）。仅当客户端请求了 binary 子协议时回显（spec 要求：
    # 响应含 Sec-WebSocket-Protocol 则请求必须也有）。noVNC 1.7 RFB 不一定带，不带就裸 accept
    # （binaryType=arraybuffer，RFB 二进制帧照常）。
    offered = ws.headers.get("sec-websocket-protocol", "")
    await ws.accept(subprotocol="binary" if "binary" in offered else None)
    from app.browser_vnc import bridge_vnc_ws
    await bridge_vnc_ws(ws, browser_pod, vnc_pw)


# ── Explicit /v1 routes (JWT or sk- API key, adapter-driven) ──
async def _resolve_auth(
    credentials: HTTPAuthorizationCredentials | None,
) -> tuple[str | None, str | None, bool, str | None]:
    """鉴权分流：sk- 走 API Key 路径（user_id=None, agent_id 来自 Key, engine_type 来自 DB），
    其余走 JWT。

    返回 (user_id, agent_id, is_admin, engine_type)。
    sk- 路径 user_id=None、agent_id + engine_type 由 Key 决定（OpenAI SDK 不传 X-Engine-Type）；
    JWT 路径 user_id 来自 token、agent_id 由 X-Agent-ID 头传入、engine_type 由 X-Engine-Type 头传入。
    """
    if credentials and credentials.credentials.startswith("sk-"):
        from app.api_key_auth import verify_api_key
        instance_id, _, _, engine_type = await verify_api_key(credentials)
        return None, str(instance_id), False, engine_type
    payload = verify_token(credentials)
    roles = payload.get("roles") or []
    is_admin = "平台管理员" in roles or "系统管理员" in roles
    return payload.get("sub"), None, is_admin, None


async def _proxy_v1(
    request: Request, path: str,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Response:
    """显式 /v1 路由共享处理：JWT 或 sk- API Key 鉴权 → 走 adapter 管线。"""
    user_id, agent_id_from_key, is_admin, engine_type = await _resolve_auth(credentials)
    # sk- key 路径下 agent_id 由 Key 决定，忽略客户端 X-Agent-ID（防伪造）
    agent_id = agent_id_from_key or request.headers.get("X-Agent-ID")
    if not agent_id:
        return JSONResponse({"error": "X-Agent-ID header is required"}, status_code=400)

    if request.headers.get("X-Hermes-Profile"):
        logger.warning("Client X-Hermes-Profile header ignored (server-computed)")

    return await proxy_handler(
        request, path, agent_id,
        user_id=user_id, is_admin=is_admin,
        engine_type_override=engine_type,
    )


@app.post("/v1/chat/completions")
async def v1_chat_completions(request: Request,
                              credentials: HTTPAuthorizationCredentials = Depends(security)):
    """SSE 流式 chat（F-GW-010；adapter 按 engine_type 转协议）"""
    return await _proxy_v1(request, "v1/chat/completions", credentials)


@app.get("/v1/models")
async def v1_models(request: Request,
                    credentials: HTTPAuthorizationCredentials = Depends(security)):
    """动态模型加载（F-END-050；Dify → /parameters）"""
    return await _proxy_v1(request, "v1/models", credentials)


@app.api_route("/v1/sessions", methods=["POST", "GET", "PATCH", "DELETE"])
async def v1_sessions(request: Request,
                      credentials: HTTPAuthorizationCredentials = Depends(security)):
    """会话 CRUD（F-GW-041；Dify → /v1/conversations）"""
    return await _proxy_v1(request, "v1/sessions", credentials)


@app.api_route("/v1/sessions/{session_id}", methods=["GET", "PATCH", "DELETE"])
async def v1_session_detail(request: Request, session_id: str,
                             credentials: HTTPAuthorizationCredentials = Depends(security)):
    return await _proxy_v1(request, f"v1/sessions/{session_id}", credentials)


@app.get("/v1/sessions/{session_id}/messages")
async def v1_session_messages(request: Request, session_id: str,
                              credentials: HTTPAuthorizationCredentials = Depends(security)):
    """会话消息（引擎托管优先、SQLite 兜底 B7）"""
    return await _proxy_v1(request, f"v1/sessions/{session_id}/messages", credentials)


@app.get("/v1/files")
async def v1_files(request: Request,
                   credentials: HTTPAuthorizationCredentials = Depends(security)):
    """文件浏览器（F-END-030）"""
    return await _proxy_v1(request, "v1/files", credentials)


# ── Catch-all proxy route (JWT or sk- API key required) ──
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(request: Request, path: str,
                credentials: HTTPAuthorizationCredentials = Depends(security)):
    """兜底代理到 Engine Pod — adapter 驱动（显式 /v1 路由未覆盖的路径）"""
    if path.startswith("api/gateway/channel/"):
        return JSONResponse({"error": "Not found"}, status_code=404)

    user_id, agent_id_from_key, is_admin, engine_type = await _resolve_auth(credentials)
    # sk- key 路径下 agent_id 由 Key 决定，忽略客户端 X-Agent-ID（防伪造）
    agent_id = agent_id_from_key or request.headers.get("X-Agent-ID")
    if not agent_id:
        return JSONResponse({"error": "X-Agent-ID header is required"}, status_code=400)

    # 安全：记录并忽略客户端传入的 X-Hermes-Profile
    if request.headers.get("X-Hermes-Profile"):
        logger.warning("Client X-Hermes-Profile header ignored (server-computed)")

    return await proxy_handler(
        request, path, agent_id,
        user_id=user_id, is_admin=is_admin,
        engine_type_override=engine_type,
    )
