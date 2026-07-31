"""仪表盘聚合 API。

数据来源：
- 活动动态：manager DB 的 Agent 生命周期（创建/发布/下架/更新），无独立审计表。
- 用户组概览：manager DB 的 group 成员/agent + LiteLLM spend（team_id=group_id）。
- 个人对话趋势：Langfuse traces，按 metadata.enduser_id 过滤（1 trace = 1 对话）。

资源消耗折线图（24h cpu/mem）因无历史采样源，本轮保留前端 sample 标注。
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from app.core.auth import get_current_user, is_platform_admin
from app.models import (
    AgentDeployment,
    AgentInstance,
    AgentStatus,
    DeploymentStatus,
    ResourcePool,
    User,
    UserGroup,
)
from app.services import instance_service, langfuse_client, litellm_client, metrics_service
from app.worker import client as controller_client
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from pkg.common.config import settings
from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/dashboard", tags=["dashboard"])


# ── 最近活动动态 ────────────────────────────────────────


@router.get("/activities")
async def get_activities(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """从 AgentInstance 生命周期派生最近活动（创建/发布/下架/更新）。

    无独立审计表，按 AgentInstance.updated_at 倒序取最近 N 条并推断动作。
    """
    res = await db.execute(
        select(AgentInstance)
        .options(joinedload(AgentInstance.creator))
        .order_by(AgentInstance.updated_at.desc())
        .limit(limit * 2)  # 多取一些以便按事件时间排序后截断
    )
    agents = list(res.scalars().all())

    items: list[dict] = []
    now = datetime.now(UTC)
    for a in agents:
        creator = a.creator.username if a.creator else "—"
        # 选择该 agent 最具代表性的事件时间与动作
        if a.status == AgentStatus.PUBLISHED and a.published_at:
            action, etype, ts = "发布了智能体", "publish", a.published_at
        elif a.status == AgentStatus.OFFLINE:
            action, etype, ts = "下架了智能体", "offline", a.updated_at or a.created_at
        elif a.created_at and (a.updated_at is None or abs((a.updated_at - a.created_at).total_seconds()) < 1):
            action, etype, ts = "创建了智能体", "create", a.created_at
        else:
            action, etype, ts = "更新了智能体", "edit", a.updated_at or a.created_at

        if ts is None:
            continue
        # 跳过过旧（>30d）的，避免历史噪音
        if (now - ts).days > 30:
            continue
        items.append({
            "user": creator,
            "action": action,
            "target": a.name,
            "time": ts.isoformat(),
            "type": etype,
        })

    items.sort(key=lambda x: x["time"], reverse=True)
    return {"items": items[:limit]}


# ── 用户组管理员概览 ────────────────────────────────────


async def _user_first_group(db: AsyncSession, user: User) -> UserGroup | None:
    res = await db.execute(
        select(UserGroup)
        .options(selectinload(UserGroup.members))
        .where(UserGroup.members.any(User.id == user.id))
        .order_by(UserGroup.created_at)
    )
    return res.scalars().first()


@router.get("/group")
async def get_group_overview(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前用户所属用户组的概览：成员数、agent 数、状态分布、今日对话/月度 Token。

    组用户取第一个组；平台管理员无组时返回全平台聚合。
    """
    group = await _user_first_group(db, user)
    if group is None:
        # 平台管理员无组 → 全平台聚合；组用户无组 → 404
        if not is_platform_admin(user):
            raise HTTPException(status_code=404, detail="当前用户未归属任何用户组")
        return await _platform_overview(db)

    # 组内实例（归属该组）
    agents_res = await db.execute(
        select(AgentInstance).where(AgentInstance.group_id == group.id)
    )
    group_agents = list(agents_res.scalars().all())

    status_map: dict[str, int] = {}
    for a in group_agents:
        status_map[a.status.value] = status_map.get(a.status.value, 0) + 1

    distribution = [
        {"name": "已发布", "value": status_map.get("PUBLISHED", 0), "color": "#00a870"},
        {"name": "草稿", "value": status_map.get("DRAFT", 0), "color": "#f59e0b"},
        {"name": "已下架", "value": status_map.get("OFFLINE", 0), "color": "#909399"},
    ]

    # LiteLLM 用量（team_id = str(group.id)）
    today_conversations = 0
    monthly_tokens = 0
    try:
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        today_logs = await litellm_client.spend_logs(
            start_date=today_start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            team_id=str(group.id),
            limit=1000,
        )
        today_conversations = len(_extract_logs(today_logs))

        month_logs = await litellm_client.spend_logs(
            start_date=month_start.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=now.strftime("%Y-%m-%d %H:%M:%S"),
            team_id=str(group.id),
            limit=1000,
        )
        monthly_tokens = sum(
            int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0)
            for lg in _extract_logs(month_logs)
        )
    except litellm_client.LitellmError:
        pass  # LiteLLM 不可达时用量置 0

    return {
        "groupName": group.name,
        "agentCount": len(group_agents),
        "memberCount": len(group.members),
        "todayConversations": today_conversations,
        "monthlyTokens": monthly_tokens,
        "agentDistribution": distribution,
    }


async def _platform_overview(db: AsyncSession) -> dict:
    """平台管理员全平台概览：所有实例/成员/用量（各组合计）。"""
    agents_res = await db.execute(select(AgentInstance))
    all_agents = list(agents_res.scalars().all())
    status_map: dict[str, int] = {}
    for a in all_agents:
        status_map[a.status.value] = status_map.get(a.status.value, 0) + 1
    distribution = [
        {"name": "已发布", "value": status_map.get("PUBLISHED", 0), "color": "#00a870"},
        {"name": "草稿", "value": status_map.get("DRAFT", 0), "color": "#f59e0b"},
        {"name": "已下架", "value": status_map.get("OFFLINE", 0), "color": "#909399"},
    ]

    groups_res = await db.execute(select(UserGroup).options(selectinload(UserGroup.members)))
    all_groups = list(groups_res.scalars().all())
    member_count = sum(len(g.members or []) for g in all_groups)

    today_conversations = 0
    monthly_tokens = 0
    try:
        now = datetime.now(UTC)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0)
        for g in all_groups:
            tid = g.litellm_team_id or str(g.id)
            today_logs = await litellm_client.spend_logs(
                start_date=today_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                team_id=tid, limit=1000,
            )
            today_conversations += len(_extract_logs(today_logs))
            month_logs = await litellm_client.spend_logs(
                start_date=month_start.strftime("%Y-%m-%d %H:%M:%S"),
                end_date=now.strftime("%Y-%m-%d %H:%M:%S"),
                team_id=tid, limit=1000,
            )
            monthly_tokens += sum(
                int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0)
                for lg in _extract_logs(month_logs)
            )
    except litellm_client.LitellmError:
        pass

    return {
        "groupName": "全平台",
        "agentCount": len(all_agents),
        "memberCount": member_count,
        "todayConversations": today_conversations,
        "monthlyTokens": monthly_tokens,
        "agentDistribution": distribution,
    }


def _extract_logs(resp) -> list[dict]:
    if isinstance(resp, dict):
        return resp.get("data") or resp.get("logs") or []
    return resp if isinstance(resp, list) else []


# ── 系统健康探活 ────────────────────────────────────────


async def _probe(
    name: str,
    url: str | None,
    *,
    undeployed_on_conn_error: bool = False,
) -> dict:
    """探测单个服务 /health，返回 {name, status, latencyMs}。

    - url 为 None 表示内部自检，直接返回 ok。
    - undeployed_on_conn_error=True 时，连接错误（DNS 解析失败/拒绝连接）返回 "undeployed"
      而非 "down"，用于可选部署的服务（如 Langfuse 跨 namespace 探活）。
    - HTTP 4xx/5xx 或其他异常（读超时等）一律返回 "down"。
    """
    if not url:
        return {"name": name, "status": "ok", "latencyMs": 0}
    start = datetime.now(UTC)
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(url)
        status = "ok" if resp.status_code < 400 else "down"
    except (httpx.ConnectError, httpx.ConnectTimeout):
        status = "undeployed" if undeployed_on_conn_error else "down"
    except Exception:  # noqa: BLE001
        status = "down"
    latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
    return {"name": name, "status": status, "latencyMs": latency}


def _storage_kind(endpoint: str) -> str:
    """从对象存储 endpoint 判定类型：oss / cos / minio。

    与 minio_archiver 的判定保持一致：aliyuncs.com→OSS、myqcloud.com→COS、
    其余（minio:9000 等）→MinIO。
    """
    e = (endpoint or "").lower()
    if "aliyuncs.com" in e:
        return "oss"
    if "myqcloud.com" in e:
        return "cos"
    return "minio"


async def _probe_storage() -> dict:
    """对象存储探活，按 endpoint 类型走不同路径。

    - MinIO：GET /minio/health/live 公开端点（无需凭据）。
    - OSS/COS：复用 archiver 的 minio client 做 authed list_buckets——能列出 bucket
      说明 endpoint 可达 + 凭据有效。OSS/COS 无公开 health 端点，未授权 GET 根路径
      返回 403/404 无法区分"服务 down"与"缺权限"，故必须走 authed 调用。
    """
    endpoint = settings.minio_endpoint
    kind = _storage_kind(endpoint)
    start = datetime.now(UTC)
    if kind == "minio":
        return await _probe("对象存储", f"{endpoint.rstrip('/')}/minio/health/live")
    try:
        from app.worker.minio_archiver import archiver

        await asyncio.wait_for(asyncio.to_thread(archiver.client.list_buckets), timeout=3.0)
        status = "ok"
    except Exception:  # noqa: BLE001
        status = "down"
    latency = int((datetime.now(UTC) - start).total_seconds() * 1000)
    return {"name": "对象存储", "status": status, "latencyMs": latency}


@router.get("/health")
async def get_health(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """并行探活 Manager / Gateway / PostgreSQL / 对象存储 / LiteLLM / Langfuse。

    核心服务（Manager/Gateway/PostgreSQL/对象存储/LiteLLM）任一不可达返回 down；
    Langfuse 为可选部署（monitoring namespace），未部署（连接错误）返回 undeployed，
    不计入"异常"，不阻塞首页。
    对象存储按 endpoint 类型自适应：MinIO 走 /minio/health/live，OSS/COS 走 authed
    list_buckets（云对象存储无公开 health 端点）。
    """
    pg_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        pg_status = "down"

    litellm_url = f"{settings.litellm_base_url.rstrip('/')}/health/liveliness"
    langfuse_url = f"{settings.langfuse_base_url.rstrip('/')}/api/public/health"

    gw, storage, litellm, langfuse = await asyncio.gather(
        _probe("Gateway", f"{settings.gateway_base_url.rstrip('/')}/health"),
        _probe_storage(),
        _probe("LiteLLM", litellm_url),
        _probe("Langfuse", langfuse_url, undeployed_on_conn_error=True),
    )
    return {
        "items": [
            {"name": "Manager", "status": "ok", "latencyMs": 0},
            gw,
            {"name": "PostgreSQL", "status": pg_status, "latencyMs": 0},
            storage,
            litellm,
            langfuse,
        ]
    }


# ── 全平台资源消耗（实时） ────────────────────────────────


def _parse_cpu_m(q: str) -> int:
    """CPU 配额 → millicores。'500m'→500, '2'→2000, '0.5'→500。"""
    if not q:
        return 0
    s = str(q).strip()
    if s.endswith("m"):
        return int(s[:-1]) if s[:-1].isdigit() else 0
    try:
        return int(float(s) * 1000)
    except ValueError:
        return 0


def _parse_mem_mi(q: str) -> int:
    """内存配额 → Mi。'256Mi'→256, '2Gi'→2048, 裸数字按 Mi。"""
    if not q:
        return 0
    s = str(q).strip()
    if s.endswith("Mi"):
        return int(s[:-2]) if s[:-2].isdigit() else 0
    if s.endswith("Gi"):
        try:
            return int(float(s[:-2]) * 1024)
        except ValueError:
            return 0
    if s.endswith("M"):
        try:
            return int(float(s[:-1]))
        except ValueError:
            return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


@router.get("/resources")
async def get_resources(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """全平台资源池当前 CPU/内存用量（metrics-server 实时值，非历史时序）。

    按 RUNNING 部署的 distinct resource_pool_id 聚合（避免共享池重复计数），
    限额取对应 ResourcePool 的 max_cpu/max_memory 之和。
    用量优先取 metrics-server 实时值；不可用（controller SA 缺 metrics.k8s.io RBAC / 未部署）时
    回退到 pod 资源 requests，并置 metricsAvailable=False 供前端区分「实时/已分配」。
    """
    # distinct 运行中资源池
    res = await db.execute(
        select(AgentDeployment.resource_pool_id)
        .where(AgentDeployment.status == DeploymentStatus.RUNNING)
        .where(AgentDeployment.resource_pool_id.is_not(None))
        .distinct()
    )
    instance_ids = [str(r[0]) for r in res.all()]
    if not instance_ids:
        return {"cpuUsed": 0, "cpuLimit": 0, "memUsed": 0, "memLimit": 0, "podCount": 0}

    # 限额
    ei_res = await db.execute(
        select(ResourcePool).where(ResourcePool.id.in_([UUID(i) for i in instance_ids]))
    )
    instances = {str(ei.id): ei for ei in ei_res.scalars().all()}
    cpu_limit = sum(_parse_cpu_m(instances[i].max_cpu) for i in instance_ids if i in instances)
    mem_limit = sum(_parse_mem_mi(instances[i].max_memory) for i in instance_ids if i in instances)

    cpu_used = 0
    mem_used = 0
    pod_count = 0
    metrics_available = False
    for iid in instance_ids:
        # /pods 始终可用（含资源 requests 兜底），用于 pod 计数与用量兜底
        try:
            pods_data = await controller_client.list_instance_pods(iid)
        except controller_client.ControllerError:
            pods_data = None
        pods = pods_data.get("items", []) if isinstance(pods_data, dict) else []
        if not pods:
            continue
        pod_count += len(pods)
        # 优先 metrics-server 实时用量；不可用（RBAC 403 / 未部署 501）时回退 requests
        metrics_map = None
        try:
            metrics_map = await controller_client.list_instance_pod_metrics(iid)
        except controller_client.ControllerError:
            metrics_map = None
        use_metrics = bool(metrics_map)
        if use_metrics:
            metrics_available = True
        for p in pods:
            name = p.get("name", "")
            if use_metrics and name in metrics_map:
                m = metrics_map[name]
                cpu_used += _parse_cpu_m(m.get("cpu", ""))
                mem_used += _parse_mem_mi(m.get("memory", ""))
            else:
                cpu_used += _parse_cpu_m(p.get("cpu", ""))
                mem_used += _parse_mem_mi(p.get("memory", ""))

    return {
        "cpuUsed": cpu_used,
        "cpuLimit": cpu_limit,
        "memUsed": mem_used,
        "memLimit": mem_limit,
        "podCount": pod_count,
        "metricsAvailable": metrics_available,
    }


# ── 引擎实例状态分布 ────────────────────────────────────


@router.get("/instance-status")
async def get_instance_status(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """引擎部署状态分布（基于 AgentDeployment.status，对应 SUSPEND/ARCHIVE 生命周期）。

    纯 DB 聚合，无外部调用。返回 [{name, value, color}]。
    """
    res = await db.execute(
        select(AgentDeployment.status, func.count(AgentDeployment.id))
        .group_by(AgentDeployment.status)
    )
    counts = {row[0]: row[1] for row in res.all()}

    spec = [
        ("运行中", DeploymentStatus.RUNNING, "#00a870"),
        ("已挂起", DeploymentStatus.SUSPENDED, "#e6a23c"),
        ("已归档", DeploymentStatus.ARCHIVED, "#909399"),
        ("部署中", DeploymentStatus.DEPLOYING, "#386bf5"),
        ("待部署", DeploymentStatus.PENDING, "#a0cfff"),
        ("异常", DeploymentStatus.FAILED, "#f56c6c"),
    ]
    return {
        "items": [
            {"name": label, "value": counts.get(st, 0), "color": color}
            for label, st, color in spec
        ]
    }


# ── Token / 计费概览 ────────────────────────────────────


@router.get("/billing")
async def get_billing(
    _: User = Depends(get_current_user),
):
    """全平台 Token / 费用概览。

    today/monthly tokens 来自 LiteLLM spend_logs（逐行，limit=1000，超过会截断，dashboard 场景可接受）；
    月度费用来自 spend_teams 全 team 聚合（USD→CNY 按 settings.spend_usd_to_cny）。
    各指标独立 try，互不连累。

    注意：LiteLLM /spend/logs 同时传 start_date+end_date 会触发按天聚合模式（丢 token），
    故只传 start_date（date-only），客户端按 startTime 过滤今日/本月。
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    today_tokens = 0
    monthly_tokens = 0
    monthly_cost_cny = 0.0

    try:
        # 一次拉本月全部逐行 logs（start=月初），客户端按 [today_start,now] / [month_start,now] 过滤
        logs = _extract_logs(await litellm_client.spend_logs(
            start_date=month_start.strftime("%Y-%m-%d"), limit=1000,
        ))
        for lg in logs:
            t = _parse_log_time(lg.get("startTime"))
            if t is None:
                continue
            tok = int(lg.get("prompt_tokens") or 0) + int(lg.get("completion_tokens") or 0)
            if today_start <= t <= now:
                today_tokens += tok
            if month_start <= t <= now:
                monthly_tokens += tok
    except litellm_client.LitellmError:
        pass

    try:
        teams = await litellm_client.spend_teams()
        per = teams.get("total_spend_per_team", []) if isinstance(teams, dict) else []
        monthly_cost_usd = sum(float(t.get("total_spend") or 0) for t in per)
        monthly_cost_cny = round(monthly_cost_usd * settings.spend_usd_to_cny, 2)
    except litellm_client.LitellmError:
        pass

    return {
        "todayTokens": today_tokens,
        "monthlyTokens": monthly_tokens,
        "monthlyCost": monthly_cost_cny,
    }


def _parse_log_time(raw) -> datetime | None:
    """解析 LiteLLM spend log 的 startTime（ISO 字符串或 epoch 秒/毫秒）。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw > 1e12 else raw
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


@router.get("/top-agents")
async def get_top_agents(
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """热门 Agent 排行（近 30 天对话次数 Top N）。

    数据来自 LiteLLM spend_logs 按 api_key（=agent key_id）分组映射到 agent。
    返回 {items: [{agent_id, name, conversation_count, total_tokens}]}。
    """
    items = await metrics_service.build_top_agents(db, limit=limit)
    return {"items": items}


@router.get("/my-conversation-trend")
async def get_my_conversation_trend(
    days: int = Query(7, ge=1, le=30),
    current_user: User = Depends(get_current_user),
):
    """终端用户最近 N 天对话次数趋势（首页"近 7 天对话趋势"图用）。

    数据源：Langfuse traces，按 metadata.enduser_id == str(current_user.id) 过滤
    （1 trace = 1 次对话）。enduser-portal 的 chat 请求体 user 字段 = 登录用户 id，
    gateway 把它写入 trace.metadata.enduser_id。

    返回 {items: [{date: "MM-DD", value: int}, ...]}，长度 = days。
    Langfuse 未配置 / 拉取失败 / 无 trace → 全 0 数组。
    注：Langfuse v3 list_traces limit 上限 100，超过会截断（反映最近 100 条对话分布）。
    """
    today = datetime.now(UTC).date()
    from_ts = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    to_ts = datetime.now(UTC).isoformat()

    counts: dict[str, int] = {}
    if langfuse_client.is_configured():
        resp = await langfuse_client.list_traces(
            metadata={"enduser_id": str(current_user.id)},
            from_ts=from_ts,
            to_ts=to_ts,
            limit=100,
        )
        if resp:
            for t in resp.get("data") or []:
                ts_str = t.get("createdAt") or t.get("timestamp")
                if not ts_str:
                    continue
                try:
                    d = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).date()
                except ValueError:
                    continue
                counts[d.isoformat()] = counts.get(d.isoformat(), 0) + 1

    items = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        items.append({"date": d.strftime("%m-%d"), "value": counts.get(d.isoformat(), 0)})
    return {"items": items}


@router.get("/my-stats")
async def get_my_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """终端用户首页统计：可访问 Agent 数 + 本月对话次数。

    - accessible_agents: instance_service.list_accessible_instances 返回的列表长度
      （PUBLISHED + group 权限过滤，与 /instances/accessible endpoint 同源）
    - monthly_conversations: Langfuse traces 按 metadata.enduser_id 过滤，
      limit=1 + meta.totalItems 拿精确总数（避免 100 条截断）。
    """
    instances = await instance_service.list_accessible_instances(
        db, current_user.id, is_platform_admin(current_user)
    )
    accessible_agents = len(instances)

    monthly_conversations = 0
    if langfuse_client.is_configured():
        now = datetime.now(UTC)
        from_ts = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        to_ts = now.isoformat()
        resp = await langfuse_client.list_traces(
            metadata={"enduser_id": str(current_user.id)},
            from_ts=from_ts,
            to_ts=to_ts,
            limit=1,
        )
        if resp:
            monthly_conversations = (resp.get("meta") or {}).get("totalItems") or 0

    return {
        "accessible_agents": accessible_agents,
        "monthly_conversations": monthly_conversations,
    }
