"""Engine Instance Pod 管理 API — /api/controller/engine-instances/{id}/pods/*

Pod 列表 / 日志 / 日志来源 / 重启 / 指标。从 router.py 拆出，路径不变。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db as get_manager_db

from ._common import PodInfo, PodListResponse
from .k8s_manager import k8s_manager

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/api/controller/engine-instances/{instance_id}/pods")
async def get_instance_pods(
    instance_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """列出指定 EngineInstance 下所有运行中的 K8s Pod"""
    try:
        pod_list = await k8s_manager.get_pods_for_instance(instance_id, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list pods: {e}")
    return PodListResponse(items=[PodInfo(**p) for p in pod_list], total=len(pod_list))


@router.get("/api/controller/engine-instances/{instance_id}/pods/{pod_name}/logs")
async def get_pod_logs(
    instance_id: str,
    pod_name: str,
    tail_lines: int = Query(200, ge=10, le=5000),
    source: str = Query("engine", pattern="^(engine|gateway)$"),
    profile: str | None = Query(None),
):
    """获取指定 Pod 的日志（最后 N 行）。

    source=engine（默认）：容器 stdout（nginx + entrypoint 启动日志）。
    source=gateway：某 profile 网关日志（/tmp/gateway-{profile}.log），
    profile 缺省时仅返回可用 profile 列表。
    """
    try:
        if source == "gateway":
            if not profile:
                profiles = await k8s_manager.list_profile_log_files(pod_name)
                return {
                    "pod_name": pod_name,
                    "source": "gateway",
                    "profile": None,
                    "profiles": profiles,
                    "logs": "",
                }
            logs = await k8s_manager.get_profile_gateway_logs(pod_name, profile, tail_lines)
            return {"pod_name": pod_name, "source": "gateway", "profile": profile, "logs": logs}
        logs = await k8s_manager.get_pod_logs(pod_name, tail_lines)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get logs: {e}")
    return {"pod_name": pod_name, "source": "engine", "logs": logs}


@router.get("/api/controller/engine-instances/{instance_id}/pods/{pod_name}/logs/sources")
async def get_pod_log_sources(
    instance_id: str,
    pod_name: str,
):
    """列出该 Pod 可用的日志来源：引擎 stdout + 各 profile 网关日志文件。"""
    try:
        profiles = await k8s_manager.list_profile_log_files(pod_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list log sources: {e}")
    return {"engine": True, "profiles": profiles}


@router.post("/api/controller/engine-instances/{instance_id}/pods/{pod_name}/restart")
async def restart_pod(
    instance_id: str,
    pod_name: str,
):
    """重启指定 Pod（通过删除 Pod 让 K8s Deployment 重建）"""
    try:
        await k8s_manager.delete_pod(pod_name)
        logger.info(f"Deleted pod {pod_name} for restart (instance {instance_id})")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restart pod: {e}")
    return {
        "status": "restarting",
        "pod_name": pod_name,
        "message": "Pod 正在重建，请稍后刷新",
    }


@router.get("/api/controller/engine-instances/{instance_id}/pods/metrics")
async def get_instance_pods_metrics(
    instance_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """返回该 EngineInstance 下所有 Pod 的当前 CPU/内存用量（metrics-server）。

    metrics-server 未部署时返回 501，调用方降级为空。
    """
    try:
        pod_list = await k8s_manager.get_pods_for_instance(instance_id, db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list pods: {e}")
    pod_names = [p["name"] for p in pod_list if p.get("name")]
    try:
        metrics = await k8s_manager.get_pod_metrics(pod_names)
    except Exception as e:
        raise HTTPException(status_code=501, detail=f"metrics-server 不可用: {e}")
    return {"items": [{"name": n, **m} for n, m in metrics.items()]}
