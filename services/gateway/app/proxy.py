"""Reverse proxy to engine pods — adapter-driven (Hermes / Dify / OpenClaw).

按 ``X-Agent-ID`` + ``X-Engine-Type`` 头选 adapter，由 adapter 负责：
  - build_upstream_url：按 engine_type 构造 DNS（hermes/openclaw:8642, dify:8080）
  - transform_headers：去 Origin/Referer + 注入引擎 key + 会话头翻译
  - transform_request_body / transform_response_body：协议归一化（Dify）

安全:
  - X-Hermes-Profile 头由服务端计算，忽略客户端传入值
  - 用户只能使用自己的 Profile（INDEPENDENT，组共享 SHARED 已下线）

Gateway 反向依赖禁止：adapter 仅靠 X-Agent-ID + DNS 命名构造 URL，不查
manager/controller（契约 §1）。Hermes 的 Profile 路由仍走 profile_resolver
（Repo2 既有行为，B3 域），其 DB 查询为路由解析，非 upstream 地址获取。
Dify 的 engine_url + app_type 解析同样属于路由解析（外部实例 vs Pod DNS），
走本文件 _resolve_dify_target 缓存查询，不查 manager service。
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from httpx import ConnectError, ConnectTimeout
from jose import JWTError, jwt
from sqlalchemy import text

from app.adapter import build_engine_dns, get_adapter
from app.attachment_hint import inject_attachment_hints, synthesize_content
from app.time_hint import inject_current_time_hint
from app.user_hint import inject_user_context_hint
from app.langfuse_client import (
    _extract_model_from_body,
    _extract_text_from_body,
    _extract_usage_from_body,
    _extract_usage_from_sse,
    _extract_text_from_sse,
    _hash_last_user_message,
    _parse_hermes_tool_progress_from_chunk,
    _parse_tool_calls_from_sse_chunk,
    end_tool_call_spans,
    finalize_chat,
    trace_chat,
    trace_run_append_output,
    trace_run_bind,
    trace_run_end,
    trace_run_get,
    trace_run_start,
    update_hermes_tool_span,
    update_tool_call_span,
)
from app.settings import settings

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)

# Dify 路由解析缓存：agent_id → ({"engine_url", "app_type"}, expires_at)
# 5min TTL 避免每请求打 DB；外部实例 URL 切换/Pod 重建需等 TTL 过期或重启 gateway。
_dify_resolve_cache: dict[str, tuple[dict, float]] = {}
_DIFY_CACHE_TTL: float = 300.0


async def _resolve_dify_target(agent_id: str) -> dict | None:
    """从 DB 解析 Dify 引擎路由目标（外部实例 URL + app_type + app_api_key）。

    - JOIN agent_deployments + agent_instances + agent_definitions 取
      dep.engine_url + ai.dify_config（per-instance 新列）+ def.model_config（fallback 用）。
    - 优先读 ai.dify_config；空则回退到 def.model_config.dify（历史快照数据）。
    - 外部 URL（http(s)://...）直接用作 base_url；集群 DNS（含 .svc.cluster.local）
      回退到 adapter._dns（Pod 模式）。
    - 缓存 5min，避免每请求打 DB。

    Gateway 反向依赖约束：此处只做路由解析（同 profile_resolver），不调 manager API。
    """
    cached = _dify_resolve_cache.get(agent_id)
    if cached:
        target, expires = cached
        if time.time() < expires:
            return target

    from pkg.common.database import async_session

    try:
        async with async_session() as db:
            row = await db.execute(
                text(
                    "SELECT dep.engine_url, ai.dify_config, def.model_config "
                    "FROM agent_deployments dep "
                    "JOIN agent_instances ai ON ai.id = dep.instance_id "
                    "JOIN agent_definitions def ON def.id = ai.definition_id "
                    "WHERE dep.instance_id = :aid "
                    "ORDER BY dep.deployed_at DESC NULLS LAST, dep.last_active_at DESC NULLS LAST LIMIT 1"
                ),
                {"aid": agent_id},
            )
            data = row.mappings().first()
    except Exception as e:
        logger.warning("Dify resolve DB error for %s: %s", agent_id[:8], e)
        return None

    if not data:
        return None

    engine_url = data.get("engine_url") or ""
    # 优先读 ai.dify_config；空则 fallback 到 def.model_config.dify
    dify_cfg = data.get("dify_config") or {}
    if not dify_cfg:
        mc = data.get("model_config") or {}
        if isinstance(mc, str):
            try:
                mc = json.loads(mc)
            except json.JSONDecodeError:
                mc = {}
        dify_cfg = (mc or {}).get("dify") or {}
    app_type = dify_cfg.get("app_type") or "chat"
    app_api_key = dify_cfg.get("app_api_key") or ""

    target = {"engine_url": engine_url, "app_type": app_type, "app_api_key": app_api_key}
    _dify_resolve_cache[agent_id] = (target, time.time() + _DIFY_CACHE_TTL)
    return target


def _is_cluster_dns(url: str) -> bool:
    """判断 URL 是否为集群内 DNS（http://engine-dify-xxx.ns.svc.cluster.local:port）。

    外部实例 URL（http(s)://your-dify.com）的 base_url 直接使用；
    集群 DNS 回退到 adapter._dns（Pod 模式，构造同一种 URL）。
    """
    return ".svc.cluster.local" in url


def verify_token(credentials: HTTPAuthorizationCredentials | None = None) -> dict:
    """JWT 鉴权 + 提取 user_id"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def build_engine_url(agent_id: str) -> str:
    """[DEPRECATED] Hermes engine base URL（向后兼容，lifecycle/test 用）。

    新代码应通过 adapter.build_upstream_url 构造（engine_type 驱动）。
    """
    return f"http://{build_engine_dns('hermes', agent_id, settings.k8s_namespace)}"


def _hop_by_hop(h: str) -> bool:
    return h.lower() in (
        "host",
        "content-length",
        "content-encoding",
        "transfer-encoding",
        "connection",
    )


def _resolve_channel_type(raw_headers: dict) -> str:
    """从请求头派生 Langfuse trace 的 channel_type。

    App 端经 X-Client-Type 上报（android/ios/harmony），其余（web/IM/OpenAI SDK）一律记 web；
    严格白名单防任意字符串污染 metadata 维度。
    """
    client_type = (raw_headers.get("x-client-type") or "").lower()
    return client_type if client_type in ("android", "ios", "harmony") else "web"


def resolve_adapter(request: Request, engine_type_override: str | None = None):
    """从 X-Engine-Type 头解析 engine_type 并返回 (engine_type, adapter)。

    engine_type_override 优先（sk- API Key 路径下从 DB 拿到的实例真实引擎类型，
    覆盖 header 默认值——OpenAI SDK 不传 X-Engine-Type，否则 Dify 实例会被当 HERMES 路由）。

    缺省 HERMES（向后兼容 Repo2 仅 hermes 的现状）。
    """
    engine_type = engine_type_override or request.headers.get("X-Engine-Type", "HERMES") or "HERMES"
    adapter = get_adapter(engine_type, k8s_namespace=settings.k8s_namespace)
    return engine_type, adapter


def _inject_attachments_if_chat(path: str, method: str, body: bytes) -> bytes:
    """对 POST /v1/chat/completions 或 /v1/runs 的 JSON body，把结构化 attachments
    合成进 content 并剥离字段；其它路径/非 JSON/无 attachments 原样返回。

    兼容两种 body：
    - /v1/chat/completions：``messages[]``，每条 message 可带 attachments
    - /v1/runs：``input``（当前轮字符串）+ 顶层 ``attachments``（当前轮）+
      ``conversation_history[]``（历史，每条可带 attachments）

    引擎只认 content 文本，attachments 是 gateway↔前端/IM 的内部约定字段，必须在转发前剥掉。
    仅在确有 attachments 时重序列化，避免无谓改变字节格式。
    """
    if method != "POST" or not body:
        return body
    norm = path.strip("/")
    if norm not in ("v1/chat/completions", "v1/runs"):
        return body
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return body
    if not isinstance(data, dict):
        return body

    changed = False
    # messages[]（chat/completions）
    messages = data.get("messages")
    if isinstance(messages, list) and any(
        isinstance(m, dict) and m.get("attachments") for m in messages
    ):
        data["messages"] = inject_attachment_hints(messages)
        changed = True
    # conversation_history[]（runs 历史轮）
    history = data.get("conversation_history")
    if isinstance(history, list) and any(
        isinstance(m, dict) and m.get("attachments") for m in history
    ):
        data["conversation_history"] = inject_attachment_hints(history)
        changed = True
    # 顶层 attachments（runs 当前轮 input）
    top_atts = data.get("attachments")
    if top_atts:
        data["input"] = synthesize_content(data.get("input", ""), top_atts)
        del data["attachments"]
        changed = True

    if not changed:
        return body
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


_SILENCE_HINT_EVENT = "gateway.silence"


def _silence_hint_frame(elapsed: float) -> bytes:
    """构造静默提示 SSE 帧（event 行 + data JSON 的 event 字段双通道，端上按 data 路由）。"""
    return (
        f"event: {_SILENCE_HINT_EVENT}\n"
        f'data: {{"event":"{_SILENCE_HINT_EVENT}","elapsed":{int(elapsed)}}}\n\n'
    ).encode()


def _is_silence_hint(chunk: bytes | str) -> bool:
    """是否为本网关注入的静默提示帧。trace 侧处理（TTFT/output 累积）应跳过，
    避免污染观测数据；提示帧总是以单个 yield 完整出现（非上游字节切片）。"""
    prefix = f"event: {_SILENCE_HINT_EVENT}"
    if isinstance(chunk, (bytes, bytearray)):
        return chunk.startswith(prefix.encode())
    return chunk.startswith(prefix)


def _is_sse_comment_only(chunk: bytes | str) -> bool:
    """chunk 是否只含 SSE 注释行（`:` 开头，如引擎 aiohttp 层每 30s 一帧的
    `: keepalive` 保活，CHAT_COMPLETIONS_SSE_KEEPALIVE_SECONDS=30.0）。

    注释帧是传输层保活信号而非内容产出：看门狗不得因此重置静默计时（否则
    端上提示的已等待时长每 ~30s 被隐形清零，实测 8→16→24→8），trace 侧也
    不得把它当作首个 token。字节切片理论上可能把内容帧截出以 `:` 起头的
    片段，故按行全量校验（所有非空行均为注释行才成立）；内容持续到达时
    无超时注入，个别误判不影响正确性。
    """
    if isinstance(chunk, (bytes, bytearray)):
        text = chunk.decode("utf-8", errors="ignore")
    else:
        text = chunk
    lines = [ln for ln in text.replace("\r\n", "\n").split("\n") if ln]
    return bool(lines) and all(ln.startswith(":") for ln in lines)


async def _aiter_sse_with_silence_hints(resp: httpx.Response, hint_seconds: float):
    """包装上游 SSE 字节流：超过 hint_seconds 无任何字节时注入 gateway.silence 提示帧。

    场景：引擎生成长工具调用参数（如几十 KB write_file 的 JSON 参数）期间
    全程无任何 SSE 事件，端上既无流式增量又无工具卡（2026-07-23 trace
    a9dcd79a 实测 53s×2 连续静默）。注入仅发生在完整帧边界（上游字节流
    以 \\n\\n 收尾）后，避免截断上游帧。读协程不随超时取消（wait 而非
    wait_for），避免反复取消 httpx 流读。

    引擎每 30s 无输出会写 `: keepalive` 注释帧保活：照常转发给端上维持
    连接，但不重置静默计时——注释帧不代表内容产出。
    """
    aiter = resp.aiter_bytes().__aiter__()
    pending = asyncio.ensure_future(aiter.__anext__())
    tail = b""  # 最近 4 字节，用于帧边界判断（\n\n 或 \r\n\r\n）
    at_boundary = True
    elapsed = 0.0
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=hint_seconds)
            if not done:
                elapsed += hint_seconds
                if at_boundary:
                    yield _silence_hint_frame(elapsed)
                continue
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            if not chunk:
                pending = asyncio.ensure_future(aiter.__anext__())
                continue
            tail = (tail + chunk)[-4:]
            at_boundary = tail.endswith(b"\n\n") or tail.endswith(b"\r\n\r\n")
            if not _is_sse_comment_only(chunk):
                elapsed = 0.0
            yield chunk
            pending = asyncio.ensure_future(aiter.__anext__())
    finally:
        if not pending.done():
            pending.cancel()


async def _stream(request: Request, upstream_url: str, headers: dict, body: bytes):
    """Stream request to engine and yield response chunks."""
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            req = client.build_request(
                request.method,
                upstream_url,
                content=body,
                headers=headers,
            )
            resp = await client.send(req, stream=True)
        except (ConnectError, ConnectTimeout):
            yield 503, {}, "application/json", False
            yield json.dumps(
                {
                    "error": "Engine not available",
                    "message": "Agent engine is not deployed or has been suspended.",
                }
            ).encode()
            return

        ct = resp.headers.get("content-type", "")
        is_sse = "text/event-stream" in ct
        yield (
            resp.status_code,
            {k: v for k, v in resp.headers.items() if not _hop_by_hop(k)},
            ct,
            is_sse,
        )
        try:
            if is_sse:
                hint_s = settings.sse_silence_hint_seconds
                if hint_s and hint_s > 0:
                    async for chunk in _aiter_sse_with_silence_hints(resp, hint_s):
                        yield chunk
                else:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
            else:
                yield await resp.aread()
        except (httpx.ReadError, httpx.RemoteProtocolError):
            pass


async def proxy_handler(
    request: Request,
    path: str,
    agent_id: str,
    user_id: str | None = None,
    is_admin: bool = False,
    extra_headers: dict | None = None,
    engine_type_override: str | None = None,
) -> Response:
    """Core proxy logic — adapter-driven forward to engine and return response.

    Args:
        request: Incoming FastAPI request.
        path: Upstream path (may include ``api/gateway/`` prefix; stripped here).
        agent_id: Target agent UUID (X-Agent-ID).
        user_id: Authenticated user UUID (from JWT). Used for Hermes Profile resolution.
        is_admin: Whether the caller is platform/sys admin (bypasses some checks).
        extra_headers: Additional headers to inject (e.g. X-Hermes-Session-Id).
        engine_type_override: sk- API Key 路径下从 DB 拿到的实例真实引擎类型，
            覆盖 request 的 X-Engine-Type 头（OpenAI SDK 不传此头）。
    """
    engine_type, adapter = resolve_adapter(request, engine_type_override=engine_type_override)

    # 剥离网关前缀（catch-all 可能带 api/gateway/、api/v1/、api/controller/ 旧前缀）
    for prefix in ("api/gateway/", "api/v1/", "api/controller/"):
        if path.startswith(prefix):
            path = path.removeprefix(prefix)
            break

    # Dify 双模：从 DB 解析 engine_url（外部实例 URL / Pod DNS）+ app_type（chat/agent/workflow）
    # 必须在 map_path 之前注入 _app_type，否则 adapter 无法派生 workflow 路径。
    if engine_type == "DIFY":
        dify_target = await _resolve_dify_target(agent_id)
        if dify_target:
            adapter._app_type = dify_target.get("app_type") or "chat"

    mapped_path = adapter.map_path(path)
    query = request.url.query

    # base URL：Hermes 走 Profile 路由（Repo2 既有，B3 域）；其余引擎用 adapter DNS
    profile_name: str | None = None
    if adapter.engine_type == "HERMES" and user_id:
        try:
            from app.profile_resolver import AccessDenied, ProfileNotFound, profile_resolver

            target = await profile_resolver.resolve(user_id, agent_id, is_admin=is_admin)
            base_url = target.engine_url or f"http://{adapter._dns(agent_id)}"
            profile_name = target.profile_name
        except AccessDenied as e:
            logger.warning("Access denied: %s", e)
            return JSONResponse(
                content={"error": "Access denied", "agent_id": agent_id},
                status_code=403,
            )
        except ProfileNotFound as e:
            # agent 不存在/未发布/无匹配 channel → 返回 404，
            # 不再回退无校验的 legacy 路由（否则=绕过权限校验）
            logger.warning("Profile not found for %s: %s", agent_id[:8], e)
            return JSONResponse(
                content={"error": "agent or channel not found", "agent_id": agent_id},
                status_code=404,
            )
        except Exception as e:
            # 基础设施异常（DB/解析失败）→ 降级 adapter DNS 路由（可用性优先）
            logger.warning(
                "Profile resolve infra error for %s, degrade to adapter DNS: %s", agent_id[:8], e
            )
            base_url = f"http://{adapter._dns(agent_id)}"
    elif adapter.engine_type == "DIFY":
        # Dify：外部实例 URL 优先；集群 DNS 或无解析结果回退 adapter._dns（Pod 模式）
        if (
            dify_target
            and dify_target.get("engine_url")
            and not _is_cluster_dns(dify_target["engine_url"])
        ):
            base_url = dify_target["engine_url"].rstrip("/")
        else:
            base_url = f"http://{adapter._dns(agent_id)}"
    else:
        base_url = f"http://{adapter._dns(agent_id)}"

    upstream_url = f"{base_url}/{mapped_path}" if mapped_path else base_url
    if query:
        upstream_url += f"?{query}"

    body = await request.body()
    raw_headers = {k.lower(): v for k, v in request.headers.items()}
    # X-Session-Id 在 transform_headers 后可能被弹出，先提取用于 Langfuse trace.session_id
    # （同一会话的多次调用在 Langfuse Sessions 视图按 session_id 聚合）
    session_id = raw_headers.get("x-session-id") or None
    channel_type = _resolve_channel_type(raw_headers)
    headers = adapter.transform_headers(raw_headers, settings.api_server_key)

    # 注入服务端计算的 Profile 名（仅 Hermes）
    if profile_name:
        headers["x-hermes-profile"] = profile_name

    # Dify：用 AgentDefinition.model_config.dify.app_api_key 覆盖客户端 Authorization
    # （客户端传的是平台 JWT，Dify 需要自己的 app_api_key）
    if adapter.engine_type == "DIFY" and dify_target and dify_target.get("app_api_key"):
        headers["authorization"] = f"Bearer {dify_target['app_api_key']}"

    if extra_headers:
        headers.update(extra_headers)

    body = adapter.transform_request_body(path, body, headers)

    # 结构化附件 → [Attached files: path] 文本提示合成（仅 chat completions / runs，
    # 引擎只认 content 文本）。前端 / IM dispatcher 产出结构化 attachments，这里在转发
    # 引擎前统一合成进 content 并剥离 attachments 字段；无 attachments 的请求原样透传。
    body = _inject_attachments_if_chat(path, request.method, body)

    # 当前时间 ephemeral system 注入（仅 Hermes POST）。每轮刷新，叠加在 core system
    # 之上不覆盖、不持久化，core prefix cache 不受影响；修正跨天历史会话报创建日期。
    if settings.inject_current_time and adapter.engine_type == "HERMES" and request.method == "POST":
        body = inject_current_time_hint(path, body, None, settings.default_timezone)

    # 当前用户身份 ephemeral system 注入（与时间注入同机制）。只注非 PII 业务身份
    # （角色/用户组/业务用户名），避免进 langfuse trace；强 PII 走 pull skill。
    # user_context 由 profile_resolver.resolve 拉取并缓存（按 profile_name 查，零额外开销）。
    if (
        settings.inject_user_context
        and adapter.engine_type == "HERMES"
        and request.method == "POST"
        and profile_name
    ):
        from app.profile_resolver import profile_resolver

        body = inject_user_context_hint(
            path, body, profile_resolver.get_user_context(profile_name)
        )

    # Langfuse trace 触发
    # - POST /v1/chat/completions: 单请求 trace（input → output 同一请求）
    # - POST /v1/runs: 异步 run，创建 trace + generation，response 回来后用
    #   run_id 绑定到内存映射；后续 GET /v1/runs/{id}/events 走同一 trace
    # - GET /v1/runs/{id}/events: 通过 run_id 查已有 trace，SSE chunk 累积到 output
    # env 未配置时所有 helper no-op
    lf_trace, lf_generation = (None, None)
    lf_run_id: str | None = None
    method = request.method
    norm_path = path.strip("/")

    if method == "POST" and norm_path == "v1/runs":
        try:
            body_json: dict = {}
            if body:
                try:
                    body_json = json.loads(body.decode("utf-8", errors="replace") or "{}")
                except Exception:
                    pass
            enduser_id = body_json.get("user") if isinstance(body_json, dict) else None
            # Gateway 收到请求的 unix 时间戳 + 最后一条 user 消息哈希，
            # 写入 trace.metadata 供 admin 监控中心做 Hermes 内部 trace 软关联
            _gw_req_time = time.time()
            _last_user_hash = _hash_last_user_message(body)
            lf_trace, lf_generation = trace_run_start(
                agent_id=agent_id,
                engine_type=adapter.engine_type,
                path=path,
                input_body=body,
                model=body_json.get("model"),
                session_id=session_id,
                enduser_id=enduser_id,
                channel_type=channel_type,
                last_user_message_hash=_last_user_hash,
                gateway_request_time=_gw_req_time,
            )
        except Exception:
            pass
    elif method == "GET" and norm_path.startswith("v1/runs/") and norm_path.endswith("/events"):
        try:
            parts = norm_path.split("/")
            if len(parts) >= 4:
                lf_run_id = parts[2]
                existing = trace_run_get(lf_run_id)
                if existing:
                    lf_trace, lf_generation, _ = existing
        except Exception:
            pass
    elif method == "POST" and "chat" in path:
        try:
            body_json: dict = {}
            if body:
                try:
                    body_json = json.loads(body.decode("utf-8", errors="replace") or "{}")
                except Exception:
                    pass
            enduser_id = body_json.get("user") if isinstance(body_json, dict) else None
            # 同 /v1/runs：写 trace.metadata.last_user_message_hash + gateway_request_time
            # 供 admin 监控中心做 Hermes 内部 trace 软关联
            _gw_req_time = time.time()
            _last_user_hash = _hash_last_user_message(body)
            lf_trace, lf_generation = trace_chat(
                agent_id=agent_id,
                engine_type=adapter.engine_type,
                path=path,
                method=method,
                model=body_json.get("model"),
                input_body=body,
                session_id=session_id,
                enduser_id=enduser_id,
                channel_type=channel_type,
                last_user_message_hash=_last_user_hash,
                gateway_request_time=_gw_req_time,
            )
        except Exception:
            pass

    ag = _stream(request, upstream_url, headers, body)
    status_code, resp_headers, content_type, is_sse = await anext(ag)

    if is_sse:
        # 防链路中间节点缓冲/拦截 SSE：no-transform 要求 DPI 缓存类透明代理不要碰流内容，
        # X-Accel-Buffering: no 压住任何一层误开 proxy_buffering 的 nginx（该头被 nginx
        # 消费后不转发给客户端，属预期）。
        # 背景：手机端实测整个 SSE 响应（含响应头）被中间盒扣留到流结束才一次性放行，
        # 表现是"思考几十秒 → 几千字一下子全出来"。
        # 引擎自带小写 cache-control: no-cache，先按两种大小写清掉再设，避免重复头。
        resp_headers.pop("cache-control", None)
        resp_headers.pop("Cache-Control", None)
        resp_headers["Cache-Control"] = "no-cache, no-transform"
        resp_headers["X-Accel-Buffering"] = "no"

    # upstream 502/503 且本请求经 Hermes profile 路由 → 缓存指向的端口可能已死
    # （pod 重启 / gateway 进程挂）。失效 resolve 缓存 + 标记 profile 为 failed，
    # 下条消息强制 re-ensure（controller 健康探测 → 必要时 --replace 重启）自愈。
    if status_code in (502, 503) and profile_name and user_id:
        try:
            from app.profile_resolver import profile_resolver

            profile_resolver.invalidate(agent_id, user_id, profile_name)
        except Exception:
            pass

    # Dify 502/503 → 失效 _dify_resolve_cache，下条消息重新解析（外部 URL 切换 / Pod 重启）
    if status_code in (502, 503) and engine_type == "DIFY":
        _dify_resolve_cache.pop(agent_id, None)

    if status_code == 503 and not is_sse:
        await anext(ag, None)
        return JSONResponse(
            content={"error": "Engine not available", "agent_id": agent_id},
            status_code=503,
        )

    if lf_trace is not None:
        resp_headers["X-Langfuse-Trace-Id"] = lf_trace.id

    # 注入 trace 给 adapter，供 SSE 流里为引擎原生事件（如 Dify agent_thought）
    # 创建 SPAN observation。每请求新建 adapter 实例（registry.get_adapter），
    # 不存在跨请求污染；lf_trace 可能为 None（未启用 Langfuse），adapter 内部会判空。
    adapter._langfuse_trace = lf_trace

    if is_sse:

        async def sse_gen():
            _first_token_at: datetime | None = None
            # /v1/chat/completions 流式：累积 SSE chunks 用于收尾时提取 usage + output 文本
            # /v1/runs 流式：累积由 trace_run_append_output 内部 buffer 处理，这里不重复
            chat_chunks: list[str] = []
            # Hermes 透传分支专用：tool_call_id → {span, args, function_name}
            # OpenAI SSE delta.tool_calls 累积容器，流结束时统一 end
            tool_call_spans: dict[str, Any] = {}
            try:
                if adapter.is_sse_transformable(path):
                    # Dify 等：按 event 边界流式转换（不缓冲整个响应、不截断事件）
                    async for out in adapter.transform_sse_stream(path, ag, headers):
                        if _first_token_at is None and out:
                            _first_token_at = datetime.now(UTC)
                        if lf_run_id and out:
                            try:
                                trace_run_append_output(
                                    lf_run_id,
                                    out.decode("utf-8", errors="replace")
                                    if isinstance(out, bytes)
                                    else str(out),
                                )
                            except Exception:
                                pass
                        elif lf_trace is not None and out:
                            # /v1/chat/completions 流式：累积到 chat_chunks（8KB 上限同 runs）
                            # 例外：含 "usage" 的 chunk 总是累积（末尾 usage chunk 在长响应中
                            # 可能超出 8KB 上限被丢弃，导致 trace.generation.usage 丢失）。
                            out_str = (
                                out.decode("utf-8", errors="replace")
                                if isinstance(out, bytes)
                                else str(out)
                            )
                            if '"usage"' in out_str or sum(len(c) for c in chat_chunks) < 8192:
                                chat_chunks.append(out_str)
                        yield out
                else:
                    # Hermes/OpenClaw：OpenAI 兼容 SSE，原样透传。
                    # 不在网关层把工作区图片路径 ![](path) 内联成 base64 data URL —— 否则
                    # 前端会把含 base64 的回复存入会话历史，下一轮 buildEngineContent 又把
                    # base64 回传引擎，每轮把整张图重发 LLM（langfuse Traces 观测到 token 暴涨）。
                    # 图片路径引用保留在 content 里，由前端渲染时按需解析（renderEnhancements）。
                    async for item in ag:
                        # 本网关注入的静默提示帧与上游 `: keepalive` 注释帧只透传给端上，
                        # 不进 trace 侧处理（两者都是保活信号而非内容产出，否则 TTFT 被
                        # 提前、非内容文本混进 run output 累积）
                        item_is_hint = _is_silence_hint(item) or _is_sse_comment_only(item)
                        if _first_token_at is None and item and not item_is_hint:
                            _first_token_at = datetime.now(UTC)
                        if lf_run_id and item and not item_is_hint:
                            try:
                                trace_run_append_output(
                                    lf_run_id,
                                    item.decode("utf-8", errors="replace")
                                    if isinstance(item, bytes)
                                    else str(item),
                                )
                            except Exception:
                                pass
                        elif lf_trace is not None and item and not item_is_hint:
                            item_str = (
                                item.decode("utf-8", errors="replace")
                                if isinstance(item, bytes)
                                else str(item)
                            )
                            if '"usage"' in item_str or sum(len(c) for c in chat_chunks) < 8192:
                                chat_chunks.append(item_str)
                        # OpenAI SSE tool_calls delta 累积 → Langfuse SPAN observation
                        if lf_trace is not None and item:
                            item_str = (
                                item.decode("utf-8", errors="replace")
                                if isinstance(item, bytes)
                                else str(item)
                            )
                            for tc in _parse_tool_calls_from_sse_chunk(item_str):
                                update_tool_call_span(
                                    lf_trace, tool_call_spans, tc, now=datetime.now(UTC)
                                )
                            for htc in _parse_hermes_tool_progress_from_chunk(item_str):
                                update_hermes_tool_span(
                                    lf_trace, tool_call_spans, htc, now=datetime.now(UTC)
                                )
                        yield item
            finally:
                _sse_end_time = datetime.now(UTC)
                # /v1/runs/{id}/events: 跨请求 trace 收尾
                if lf_run_id:
                    try:
                        trace_run_end(lf_run_id, end_time=_sse_end_time)
                    except Exception:
                        pass
                # /v1/chat/completions: 同步 trace 收尾
                # - 从累积的 chat_chunks 提取 assistant 文本作为 output（让 trace 详情能看到 LLM 回复）
                # - _first_token_at 作为 completion_start_time（TTFT）
                # - 从累积的 chat_chunks 提取 usage（OpenAI SSE 末尾 usage 事件 / Dify message_end.metadata.usage）
                elif lf_trace is not None:
                    try:
                        _merged_chat = "".join(chat_chunks) if chat_chunks else ""
                        _chat_usage = (
                            _extract_usage_from_sse(_merged_chat) if _merged_chat else None
                        )
                        _chat_output = (
                            _extract_text_from_sse(_merged_chat) if _merged_chat else None
                        )
                        finalize_chat(
                            lf_trace,
                            lf_generation,
                            _chat_output,
                            usage=_chat_usage,
                            end_time=_sse_end_time,
                            completion_start_time=_first_token_at,
                        )
                    except Exception:
                        pass
                # Hermes 透传分支：流结束统一 end 所有 tool_call SPAN
                if tool_call_spans:
                    try:
                        end_tool_call_spans(tool_call_spans, end_time=_sse_end_time)
                    except Exception:
                        pass

        return StreamingResponse(
            sse_gen(), status_code=status_code, headers=resp_headers, media_type=content_type
        )

    body_bytes = await anext(ag)
    async for _ in ag:
        pass
    body_bytes = adapter.transform_response_body(path, body_bytes, headers)
    # POST /v1/runs: 同步响应里包含 run_id，绑定到内存映射供后续 GET /events 关联
    if method == "POST" and norm_path == "v1/runs" and lf_trace is not None and body_bytes:
        try:
            resp_json = json.loads(body_bytes.decode("utf-8", errors="replace"))
            run_id = resp_json.get("run_id") or resp_json.get("id")
            if run_id:
                trace_run_bind(run_id, lf_trace, lf_generation, agent_id=agent_id)
                # generation 不在此处 end —— 等 GET /events SSE 结束时统一收尾
                resp_headers["X-Langfuse-Trace-Id"] = lf_trace.id
                return Response(
                    content=body_bytes,
                    status_code=status_code,
                    headers=resp_headers,
                    media_type=content_type,
                )
        except Exception:
            pass

    if lf_trace is not None:
        resp_headers["X-Langfuse-Trace-Id"] = lf_trace.id
    if lf_trace is not None and not lf_run_id:
        try:
            # 从非流式响应体提取 usage + assistant 文本 + model（OpenAI 兼容 JSON）
            _usage = _extract_usage_from_body(body_bytes)
            _text = _extract_text_from_body(body_bytes)
            _model = _extract_model_from_body(body_bytes)
            finalize_chat(
                lf_trace,
                lf_generation,
                _text,
                usage=_usage,
                end_time=datetime.now(UTC),
            )
            # 请求体可能没有 model 字段（如 Hermes），但响应体里有真实 model 名
            if _model and lf_generation is not None:
                try:
                    lf_generation.update(model=_model)
                except Exception:
                    pass
        except Exception:
            pass
    return Response(
        content=body_bytes, status_code=status_code, headers=resp_headers, media_type=content_type
    )
