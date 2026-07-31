"""V3 资源池 API — /api/manager/resource-pools"""

from uuid import UUID

from app.core.auth import get_current_user, is_platform_admin
from app.core.group_scope import assert_group_writable, get_current_group_ids
from app.models import AgentInstance, User
from app.schemas import (
    ResourcePoolCreate,
    ResourcePoolListResponse,
    ResourcePoolResponse,
    ResourcePoolUpdate,
)
from app.services import metrics_service, profile_service, resource_pool_service
from app.worker import client as controller_client
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/resource-pools", tags=["resource-pools"])


def _to_response(pool) -> ResourcePoolResponse:
    return ResourcePoolResponse(
        id=pool.id,
        name=pool.name,
        description=pool.description,
        group_id=pool.group_id,
        group_name=pool.group.name if pool.group else None,
        min_cpu=pool.min_cpu,
        max_cpu=pool.max_cpu,
        min_memory=pool.min_memory,
        max_memory=pool.max_memory,
        min_replicas=pool.min_replicas,
        max_replicas=pool.max_replicas,
        max_sessions_per_pod=pool.max_sessions_per_pod,
        auto_recycle=pool.auto_recycle,
        idle_suspend_minutes=pool.idle_suspend_minutes,
        idle_destroy_hours=pool.idle_destroy_hours,
        created_by=pool.created_by,
        creator_name=pool.creator.username if pool.creator else "",
        instance_count=0,
        created_at=pool.created_at,
        updated_at=pool.updated_at,
    )


async def _require_pool(
    db: AsyncSession, pool_id: UUID, group_ids: list[UUID] | None = None
):
    """取资源池并校验组隔离：平台共享池(group_id NULL)各组可见；组私有池仅归属组可见。"""
    pool = await resource_pool_service.get_resource_pool(db, pool_id)
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源池不存在")
    if (
        group_ids is not None
        and pool.group_id is not None
        and pool.group_id not in group_ids
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源池不存在")
    return pool


@router.get("", response_model=ResourcePoolListResponse)
async def list_pools(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str = None,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    pools, total = await resource_pool_service.list_resource_pools(
        db, page, page_size, search, group_ids=group_ids
    )
    return ResourcePoolListResponse(
        items=[_to_response(p) for p in pools], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=ResourcePoolResponse, status_code=status.HTTP_201_CREATED)
async def create_pool(
    data: ResourcePoolCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    # 平台共享池(group_id None)仅平台管理员可建；组私有池必须 ∈ 调用者所属组
    if data.group_id is None:
        if not is_platform_admin(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="仅平台管理员可创建平台共享资源池"
            )
    else:
        assert_group_writable(data.group_id, group_ids)
    pool = await resource_pool_service.create_resource_pool(db, data, user.id)
    return _to_response(pool)


@router.get("/{pool_id}", response_model=ResourcePoolResponse)
async def get_pool(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    pool = await _require_pool(db, pool_id, group_ids)
    return _to_response(pool)


@router.put("/{pool_id}", response_model=ResourcePoolResponse)
async def update_pool(
    pool_id: UUID,
    data: ResourcePoolUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_pool(db, pool_id, group_ids)
    pool = await resource_pool_service.update_resource_pool(db, pool_id, data, actor_id=user.id)
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源池不存在")
    return _to_response(pool)


@router.delete("/{pool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pool(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_pool(db, pool_id, group_ids)
    try:
        if not await resource_pool_service.delete_resource_pool(db, pool_id, actor_id=user.id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源池不存在")
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))


@router.post("/{pool_id}/clone", response_model=ResourcePoolResponse, status_code=status.HTTP_201_CREATED)
async def clone_pool(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _require_pool(db, pool_id, group_ids)
    pool = await resource_pool_service.clone_resource_pool(db, pool_id, user.id)
    if not pool:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资源池不存在")
    return _to_response(pool)


# ── 详情页子资源（Pods/监控/日志）────────────────────────


@router.get("/{pool_id}/metrics")
async def get_pool_metrics(
    pool_id: UUID,
    range: str = Query("24h", pattern="^(1h|6h|24h|7d)$"),
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """资源池监控：整池所有 Pod 的 CPU/内存历史趋势 + 资源请求合计 + Pod 数。"""
    await _require_pool(db, pool_id, group_ids)
    return await metrics_service.build_pool_metrics(db, str(pool_id), range)


@router.get("/{pool_id}/pods")
async def get_pool_pods(
    pool_id: UUID,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """列出该资源池下所有实例的 Pod（含 metrics-server 实时用量）。"""
    await _require_pool(db, pool_id, group_ids)
    try:
        data = await controller_client.list_instance_pods(str(pool_id))
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)

    pods = data.get("items", []) if isinstance(data, dict) else []
    metrics_map = await controller_client.list_instance_pod_metrics(str(pool_id))

    # agent_id -> 智能体名称，让管理员一眼看出 Pod 属于哪个智能体
    agent_ids = [p.get("agent_id") for p in pods if p.get("agent_id")]
    agent_map: dict[str, str] = {}
    if agent_ids:
        try:
            ids = [UUID(a) for a in agent_ids]
        except (ValueError, AttributeError):
            ids = []
        if ids:
            rows = await db.execute(
                select(AgentInstance.id, AgentInstance.name).where(AgentInstance.id.in_(ids))
            )
            agent_map = {str(rid): name for rid, name in rows.all()}

    items = []
    for p in pods:
        name = p.get("name", "")
        aid = p.get("agent_id", "")
        live = (metrics_map or {}).get(name, {})
        items.append({
            "name": name,
            "node": p.get("node", ""),
            "status": p.get("status", ""),
            "cpu": live.get("cpu") or p.get("cpu", ""),
            "memory": live.get("memory") or p.get("memory", ""),
            "restarts": p.get("restarts", 0),
            "age": p.get("age", ""),
            "agent_id": aid,
            "agent_name": agent_map.get(aid, ""),
            "created_at": "",
        })

    running = sum(1 for i in items if i["status"] == "Running")
    stopped = sum(1 for i in items if i["status"] in ("Pending", "Terminating", "Succeeded"))
    abnormal = sum(1 for i in items if i["status"] in ("CrashLoopBackOff", "Failed", "Unknown"))

    return {"items": items, "summary": {"running": running, "stopped": stopped, "abnormal": abnormal}}


@router.get("/{pool_id}/pods/{pod_name}/logs")
async def get_pool_pod_logs(
    pool_id: UUID,
    pod_name: str,
    tail_lines: int = Query(200, ge=10, le=5000),
    source: str = Query("engine", pattern="^(engine|gateway)$"),
    profile: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """获取指定 Pod 的日志（代理 controller）。

    source=engine：容器 stdout（nginx/启动日志）；source=gateway：某 profile 网关日志。
    """
    await _require_pool(db, pool_id, group_ids)
    try:
        if source == "gateway":
            if not profile:
                # 返回可用日志来源（含各 profile 网关），富化用户信息
                data = await controller_client.list_pod_log_sources(str(pool_id), pod_name)
                return await _enrich_sources(data, db, pool_id=pool_id)
            return await controller_client.get_profile_gateway_logs(
                str(pool_id), pod_name, profile, tail_lines
            )
        return await controller_client.get_pod_logs(str(pool_id), pod_name, tail_lines)
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)


async def _enrich_sources(
    data: dict, db: AsyncSession, *, pool_id: UUID | None = None, instance_id: UUID | None = None
) -> dict:
    """把 controller 返回的 {engine, profiles:[str]} 富化为带用户信息的 profiles 对象数组。"""
    profile_names = (data or {}).get("profiles", []) or []
    user_map = await profile_service.map_profiles_to_users(
        db, profile_names, pool_id=pool_id, instance_id=instance_id
    )
    return {
        "engine": (data or {}).get("engine", True),
        "profiles": profile_service.enrich_profiles(profile_names, user_map),
    }


@router.get("/{pool_id}/pods/{pod_name}/logs/sources")
async def get_pool_pod_log_sources(
    pool_id: UUID,
    pod_name: str,
    db: AsyncSession = Depends(get_db),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    """列出该 Pod 可用日志来源（引擎 stdout + 各 profile 网关，含用户信息）。"""
    await _require_pool(db, pool_id, group_ids)
    try:
        data = await controller_client.list_pod_log_sources(str(pool_id), pod_name)
    except controller_client.ControllerError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    return await _enrich_sources(data, db, pool_id=pool_id)
