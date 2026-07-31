import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta

from app.api import (
    agent_definitions,
    agent_instances,
    agent_skills,
    app_releases,
    auth,
    business_bindings,
    community,
    dashboard,
    email_configs,
    engine_configs,
    hub_proxy,
    skill_engine_internal,
    skill_engine_proxy,
    im_bindings,
    litellm,
    message_feedback,
    observability,
    resource_pools,
    roles,
    sms_configs,
    user_groups,
    users,
)
from app.core.auth import get_current_user
from app.middleware.rate_limit import rate_limiter
from app.metrics import refresh_metrics
from app.models import OperationLog, User
from app.worker.background import start_background, stop_background
from app.worker.router import router as worker_router
from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.access_log import log_request, setup_access_log
from pkg.common.config import settings
from pkg.common.database import get_db
from pkg.common.logging import setup_json_logger
from pkg.common.request_id import RequestIdMiddleware
from pkg.common.security import (
    assert_api_key_hmac_secret,
    assert_credential_encryption_key,
    assert_production_secrets,
    configure_cors,
    parse_cors_origins,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: 生产环境密钥 fail-fast 校验（dev 跳过）
    assert_production_secrets(settings.jwt_secret, settings.environment)
    assert_credential_encryption_key(settings.credential_encryption_key, settings.environment)
    assert_api_key_hmac_secret(settings.api_key_hmac_secret, settings.environment)
    # Startup: ensure DB tables exist
    from app.models import Base

    from pkg.common.database import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed LiteLLM 角色/权限（幂等）
    from app.core.seed import seed_roles, seed_alert_rules

    try:
        await seed_roles()
    except Exception as e:  # noqa: BLE001
        # seed 失败不阻塞启动，记录即可
        print(f"[seed] litellm roles skipped: {e}")
    try:
        await seed_alert_rules()
    except Exception as e:  # noqa: BLE001
        print(f"[seed] alert rules skipped: {e}")
    # Seed 社区示例文章（幂等，需系统管理员存在）
    try:
        from app.core.seed_articles import seed_articles

        await seed_articles()
    except Exception as e:  # noqa: BLE001
        print(f"[seed] community articles skipped: {e}")
    # Seed 预置卡通头像到 MinIO public bucket（幂等，源文件已入仓）
    try:
        from app.core.seed_preset_avatars import seed_preset_avatars

        await seed_preset_avatars()
    except Exception as e:  # noqa: BLE001
        print(f"[seed] preset avatars skipped: {e}")
    # Backfill 预置 skill 到已存智能体（新增 preset 后自动补装，幂等，只追加不删）
    try:
        from app.worker.config_skills import backfill_presets
        from pkg.common.database import async_session

        async with async_session() as db:
            await backfill_presets(db)
    except Exception as e:  # noqa: BLE001
        print(f"[backfill] presets skipped: {e}")
    # Bootstrap base APK 到 app_releases 表（幂等，扫描 /app/base-apks/*.apk）
    try:
        from app.services.app_release_bootstrap import bootstrap_base_apks

        async with async_session() as db:
            count = await bootstrap_base_apks(db)
            if count:
                print(f"[bootstrap] registered {count} base APK(s)")
    except Exception as e:  # noqa: BLE001
        print(f"[bootstrap] base APKs skipped: {e}")
    # Initial metrics refresh so /metrics returns real data on first scrape
    from pkg.common.database import async_session

    try:
        async with async_session() as db:
            await refresh_metrics(db)
    except Exception as e:  # noqa: BLE001
        print(f"[metrics] initial refresh failed: {e}")
    # Worker 后台任务组（recycle_scheduler / metric_sampler / 3 循环），故障隔离
    await start_background()
    try:
        yield
    finally:
        await stop_background()


app = FastAPI(
    title=settings.app_name,
    version="0.9.2",
    lifespan=lifespan,
)

# JSON logging + request_id middleware（Promtail json stage 解析后入 Loki）
setup_json_logger("manager", level=getattr(settings, "log_level", "INFO"))
setup_access_log("manager")
app.add_middleware(RequestIdMiddleware)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """0.8.103 登录安全加固：/auth/login 单 IP 双闸限流。

    - 每分钟 10 次尝试（含成功+失败）→ 429 too_many_requests
    - 每小时 50 次失败 → 拉黑 1h（403 ip_banned）
    进程内 dict + asyncio.Lock（单副本够用）；多副本时升级 Redis。
    """
    if request.url.path == "/api/manager/auth/login":
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else ""
        if not ip:
            ip = request.headers.get("X-Real-IP", "").strip()
        if not ip and request.client:
            ip = request.client.host or "unknown"
        try:
            await rate_limiter.check_login(ip)
        except HTTPException as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers=e.headers or {},
            )
    return await call_next(request)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """接口日志：method/path/status/duration_ms/request_id → JSON → Loki。
    不写 DB（量大），Loki 查询足够。

    注：必须在 RequestIdMiddleware 之后执行才能读到 request_id。但 Starlette
    middleware 是 LIFO（后注册的最外层先执行），这里显式 set_request_id 兜底，
    无论顺序如何都能拿到。
    """
    from pkg.common.request_id import set_request_id
    from app.services.audit_service import set_operator_ip, set_operator_user_agent
    incoming = request.headers.get("X-Request-ID", "")
    rid = incoming.strip() or uuid.uuid4().hex[:16]
    set_request_id(rid)
    request.state.request_id = rid

    # 提取客户端 IP：优先 X-Forwarded-For 首段（nginx 透传客户端真实 IP），
    # 次 X-Real-IP，最后兜底 request.client.host（直连场景）。set 到 contextvar，
    # log_operation 自动读取，避免 71 个调用点显式传 IP。
    forwarded = request.headers.get("X-Forwarded-For", "")
    client_ip = forwarded.split(",")[0].strip() if forwarded else ""
    if not client_ip:
        client_ip = request.headers.get("X-Real-IP", "").strip()
    if not client_ip and request.client:
        client_ip = request.client.host or ""
    set_operator_ip(client_ip or None)
    # 提取 User-Agent：截断到 512 字符（DB 列长度上限），用于识别客户端类型 + 版本。
    user_agent = request.headers.get("User-Agent", "").strip()[:512] or None
    set_operator_user_agent(user_agent)

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    # 从 JWT 拿 user_id 失败时不影响日志，best-effort
    user_id = None
    try:
        # 不强制校验 token，只解析取 sub（无效 token 返回 None）
        from app.core.auth import decode_token
        authz = request.headers.get("Authorization", "")
        if authz.startswith("Bearer "):
            payload = decode_token(authz[7:])
            user_id = payload.get("sub") if payload else None
    except Exception:
        pass
    log_request(
        "manager",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        request_id=rid,
        user_id=user_id,
    )
    response.headers["X-Request-ID"] = rid
    return response


# Prometheus /metrics 端点（Instrumentator 自动暴露 HTTP 指标 + 自定义 gauge）
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

configure_cors(app, parse_cors_origins(settings.cors_origins))

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(user_groups.router)
app.include_router(im_bindings.router)
app.include_router(business_bindings.router)
app.include_router(litellm.router)
app.include_router(agent_skills.router)
app.include_router(dashboard.router)
app.include_router(engine_configs.router)
# 短信服务商配置（安全配置子菜单）
app.include_router(sms_configs.router)
# 邮件服务商配置（安全配置子菜单，v1 只支持 SMTP）
app.include_router(email_configs.router)
# 监控中心：链路追踪 / 用量分析 / 调用分析 / 异常告警
app.include_router(observability.router, prefix="/api/manager/observability", tags=["observability"])
# Hub 能力中心反代（/api/hub/* → hub，admin 前端经 manager 鉴权后访问）
app.include_router(hub_proxy.router)
# Skill Engine 反代（/api/skill-engine/* → skill-engine，admin 前端经 manager 鉴权后访问）
app.include_router(skill_engine_proxy.router)
# Skill Engine 内部接口（skill-engine 获取平台 LLM key）
app.include_router(skill_engine_internal.router)
# V3 三层模型路由
app.include_router(resource_pools.router)
app.include_router(agent_definitions.router)
app.include_router(agent_instances.router)
# 社区技术文章（公开可读 + 登录可发 + 平台管理员审核）
app.include_router(community.router)
# Controller worker 路由（/api/controller/*，路径不变，B gateway / C 前端经 nginx 直消费）
app.include_router(worker_router)
# APP 发布管理（admin CRUD + publish / public latest + download）
app.include_router(app_releases.router)
app.include_router(app_releases.public_router)
# 消息级用户反馈 / 收藏（终端用户）
app.include_router(message_feedback.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "unionagents-manager"}


@app.get("/api/manager/get-async-routes")
async def get_async_routes():
    """Admin 前端动态路由（vue-pure-admin），生产构建无 mock 需要后端提供"""
    return {"code": 0, "message": "操作成功", "data": []}


@app.get("/api/manager/mine-logs")
async def get_mine_logs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    pageSize: int = 10,  # noqa: N803  前端 vue-pure-admin 约定
    currentPage: int = 1,  # noqa: N803
):
    """当前用户的安全日志：只显示账户安全相关操作。
    - auth.login / auth.logout：登录、登出
    - user.change_password：修改密码
    - user.self_update：修改个人资料（含改邮箱/电话/姓名/头像 URL）

    其他操作（auth.refresh token 刷新 / user.update_avatar 单纯换头像）不在此展示。
    时间窗口限近 3 个月（90 天），更老的日志按审计合规要求归档不在此展示。
    """
    from pkg.common.utils import utcnow

    offset = (currentPage - 1) * pageSize
    security_actions = [
        "auth.login",
        "auth.logout",
        "user.change_password",
        "user.self_update",
    ]
    three_months_ago = utcnow() - timedelta(days=90)
    result = await db.execute(
        select(OperationLog)
        .where(
            OperationLog.actor_id == user.id,
            OperationLog.action.in_(security_actions),
            OperationLog.created_at >= three_months_ago,
        )
        .order_by(OperationLog.created_at.desc())
        .limit(pageSize)
        .offset(offset)
    )
    logs = result.scalars().all()
    total = await db.scalar(
        select(func.count())
        .select_from(OperationLog)
        .where(
            OperationLog.actor_id == user.id,
            OperationLog.action.in_(security_actions),
            OperationLog.created_at >= three_months_ago,
        )
    )
    return {
        "code": 0,
        "message": "操作成功",
        "data": {
            "list": [
                {
                    "id": str(log.id),
                    "action": log.action,
                    "target_type": log.target_type,
                    "target_id": str(log.target_id) if log.target_id else None,
                    "status": log.status,
                    "operator_ip": log.operator_ip,
                    "operator_user_agent": log.operator_user_agent,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in logs
            ],
            "total": total or 0,
            "pageSize": pageSize,
            "currentPage": currentPage,
        },
    }


@app.get("/api/manager/get-map-info")
async def get_map_info():
    """Dashboard 地图数据（替代前端 mock）"""
    return {"code": 0, "message": "操作成功", "data": []}
