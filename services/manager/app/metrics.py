"""Prometheus metrics for Manager (V3 三层模型适配版).

Three custom gauges exposed via `/metrics` (Instrumentator handles default
HTTP metrics automatically):

- `unionagents_agent_count{engine_type,status}` — AgentDefinition 总数（按 engine_type + status）
- `unionagents_deployment_count{status,engine_type}` — AgentDeployment 聚合
  （需 join AgentInstance → AgentDefinition 取 engine_type）
- `unionagents_agent_deployment_status{instance_id,engine_type,status,scope_type}`
  per-deployment gauge（1 for matching labelset; absent otherwise）
- `unionagents_dify_health{deployment_id,engine_url}` — Dify 连通性 (1=ok, 0=fail)

`refresh_metrics(db)` 从 `lifespan` 启动 + lifecycle_loop 周期调用（默认 60s）。
每次 refresh 清空 labelsets，避免删除的 agent 留下 stale series。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from app.models import (
    AgentDefinition,
    AgentDeployment,
    AgentInstance,
    DeploymentStatus,
    EngineType,
)
from prometheus_client import Gauge
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

agent_deployment_status = Gauge(
    "unionagents_agent_deployment_status",
    "Agent deployment status (1 for matching labelset, absent otherwise)",
    ["instance_id", "engine_type", "status", "scope_type"],
)

agent_count = Gauge(
    "unionagents_agent_count",
    "Total agent definitions by engine_type and status",
    ["engine_type", "status"],
)

deployment_count = Gauge(
    "unionagents_deployment_count",
    "Total deployments by status and engine_type",
    ["status", "engine_type"],
)

dify_health = Gauge(
    "unionagents_dify_health",
    "Dify instance reachability (1=ok, 0=fail)",
    ["deployment_id", "engine_url"],
)


async def refresh_metrics(db: AsyncSession) -> None:
    """Query DB and update gauges. Idempotent — safe to call repeatedly."""
    agent_deployment_status.clear()
    agent_count.clear()
    deployment_count.clear()

    # agent_count: AgentDefinition by engine_type + status
    stmt = select(
        AgentDefinition.engine_type, AgentDefinition.status, func.count()
    ).group_by(AgentDefinition.engine_type, AgentDefinition.status)
    result = await db.execute(stmt)
    for engine_type, status, cnt in result.all():
        agent_count.labels(
            engine_type=engine_type.value if engine_type else "UNKNOWN",
            status=status.value if status else "UNKNOWN",
        ).set(cnt)

    # deployment_count + agent_deployment_status:
    # AgentDeployment → AgentInstance → AgentDefinition（取 engine_type）
    dep_stmt = (
        select(
            AgentDeployment,
            AgentDefinition.engine_type,
        )
        .join(AgentInstance, AgentDeployment.instance_id == AgentInstance.id)
        .join(AgentDefinition, AgentInstance.definition_id == AgentDefinition.id)
        .where(
            AgentDeployment.status.in_(
                [
                    DeploymentStatus.RUNNING,
                    DeploymentStatus.SUSPENDED,
                    DeploymentStatus.FAILED,
                    DeploymentStatus.DEPLOYING,
                ]
            )
        )
    )
    dep_result = await db.execute(dep_stmt)
    rows = dep_result.all()

    agg: dict[tuple[str, str], int] = {}
    for dep, engine_type in rows:
        et = engine_type.value if engine_type else "UNKNOWN"
        st = dep.status.value if dep.status else "UNKNOWN"
        sc = dep.scope_type or "ALL"
        agg[(st, et)] = agg.get((st, et), 0) + 1
        agent_deployment_status.labels(
            instance_id=str(dep.instance_id),
            engine_type=et,
            status=st,
            scope_type=sc,
        ).set(1)

    for (st, et), cnt in agg.items():
        deployment_count.labels(status=st, engine_type=et).set(cnt)


async def probe_dify_health(db: AsyncSession) -> None:
    """Poll all running DIFY deployments' /v1/conversations endpoint.

    Sets `unionagents_dify_health{deployment_id,engine_url}` to 1 (ok) or 0 (fail).
    Called from lifecycle_loop after refresh_metrics. Concurrent per-deployment
    probe with 3s timeout; total time <5s for typical deployment count.
    """
    dify_health.clear()

    # AgentDeployment(RUNNING) → AgentInstance（dify_config 新列）→ AgentDefinition（engine_type=DIFY 过滤 + model_config fallback）
    stmt = (
        select(AgentDeployment, AgentInstance.dify_config, AgentDefinition.model_config)
        .join(AgentInstance, AgentDeployment.instance_id == AgentInstance.id)
        .join(AgentDefinition, AgentInstance.definition_id == AgentDefinition.id)
        .where(
            AgentDefinition.engine_type == EngineType.DIFY,
            AgentDeployment.status == DeploymentStatus.RUNNING,
        )
    )
    result = await db.execute(stmt)
    rows = result.all()
    if not rows:
        return

    async def _probe(dep: AgentDeployment, inst_dify_cfg: dict, def_model_cfg: dict) -> None:
        labels_kw = dict(deployment_id=str(dep.id), engine_url=dep.engine_url or "")
        try:
            # 优先读 inst.dify_config（per-instance 新列）；空则 fallback 到 definition.model_config.dify
            dify_cfg = inst_dify_cfg or {}
            if not dify_cfg:
                dify_cfg = (def_model_cfg or {}).get("dify") or {}
            api_key = dify_cfg.get("app_api_key", "")
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{dep.engine_url}/v1/conversations",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            dify_health.labels(**labels_kw).set(1 if resp.status_code == 200 else 0)
        except Exception as e:
            logger.warning(f"dify probe failed for {dep.id}: {e}")
            dify_health.labels(**labels_kw).set(0)

    await asyncio.gather(*[_probe(d, c, m) for d, c, m in rows], return_exceptions=True)
