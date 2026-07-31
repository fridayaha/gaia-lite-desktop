"""Engine lifecycle management — health checks and pod startup orchestration.

Gateway 需要在收到 Channel Webhook 时确保 Engine Pod 就绪。
如果 Pod 已休眠（SUSPENDED）或未部署，调用 Controller deploy API 唤醒。
"""
import asyncio
import logging

import httpx
from httpx import ConnectError, ConnectTimeout
from sqlalchemy import text

from app.settings import settings
from pkg.common.database import async_session

logger = logging.getLogger(__name__)


def build_engine_url(agent_id: str) -> str:
    """[DEPRECATED] Hermes engine base URL（向后兼容，不带 scope_hash）。

    仅在部署名不带 scope_hash（scope=ALL）时正确。
    新代码应使用 resolve_engine_url（从 DB 查实际 pod_name）或 adapter.build_upstream_url。
    保留作为 fallback，不删除以免影响降级路径。
    """
    from app.adapter import build_engine_dns
    return f"http://{build_engine_dns('hermes', agent_id, settings.k8s_namespace)}"


async def resolve_engine_url(agent_id: str) -> str:
    """查询 DB 获取实际的 pod_name 构造 engine URL。

    部署名可能带 scope_hash 后缀（如 engine-hermes-{id}-a1b2c3），
    DNS 规范假设不带 hash，需从 DB 获取实际 pod_name。
    fallback 到 build_engine_url（不带 hash）。
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                text("SELECT pod_name FROM agent_deployments WHERE instance_id = :aid"),
                {"aid": agent_id},
            )
            row = result.mappings().first()
            if row and row.get("pod_name"):
                pod_name = row["pod_name"]
                return f"http://{pod_name}.{settings.k8s_namespace}.svc.cluster.local:{settings.engine_port}"
    except Exception as e:
        logger.warning("Failed to query pod_name for %s: %s, falling back to DNS convention",
                       agent_id[:8], e)
    return build_engine_url(agent_id)


async def check_engine_health(agent_id: str) -> bool:
    """快速探测引擎是否就绪（HTTP GET /health）"""
    url = f"{await resolve_engine_url(agent_id)}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except (ConnectError, ConnectTimeout, httpx.ReadTimeout):
        return False


async def trigger_deploy(agent_id: str) -> bool:
    """触发 Controller 部署/恢复引擎 Pod（短超时，仅负责触发）。

    Controller 内部部署耗时可能长达 420s（120s pod 等待 + 300s engine 就绪等待），
    但 Gateway 不应该阻塞等 Controller 完成——Controller 完成后还会冗余等 120s。
    改为短超时触发：Controller 收到请求即返回，Gateway 自行轮询引擎 /health。

    即使超时也不视为失败——Controller 可能已收到请求只是响应慢，
    上层应继续轮询引擎 /health。
    """
    url = f"{settings.controller_url}/api/controller/agents/{agent_id}/deploy"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url)
            if resp.status_code == 200:
                logger.info("Engine %s deploy triggered successfully", agent_id[:8])
                return True
            logger.warning("Engine %s deploy returned %d: %s",
                           agent_id[:8], resp.status_code, resp.text[:200])
            return False
    except httpx.TimeoutException:
        logger.info("Engine %s deploy trigger timed out (controller still working)", agent_id[:8])
        return False
    except Exception as e:
        logger.error("Engine %s deploy trigger error: %s", agent_id[:8], e)
        return False


async def ensure_engine_ready(agent_id: str, max_wait: int = 300) -> tuple[bool, bool]:
    """确保引擎 Pod 就绪。

    流程：
    1. 快速健康检查 → 已就绪则直接返回（热启动）
    2. 触发 Controller 部署（短超时，不等完成）
    3. 直接轮询引擎 /health（通过 DNS 命名规范，无需查询 Controller）

    消除了旧方案的两个问题：
    - Controller deploy 120s 超时 < 实际 420s 耗时 → 假阳性故障
    - Controller 返回 200（已确认就绪）后 Gateway 又冗余轮询 120s

    Returns:
        (ready: bool, was_already_running: bool)
    """
    # Step 1: 快速探测
    if await check_engine_health(agent_id):
        return True, True  # 热启动 / 已运行

    # Step 2: 触发部署（不等 Controller 完成）
    await trigger_deploy(agent_id)

    # Step 3: 统一轮询引擎 /health（直接 DNS，不经过 Controller）
    for i in range(max_wait):
        if await check_engine_health(agent_id):
            elapsed = i + 1
            logger.info("Engine %s ready after ~%ds", agent_id[:8], elapsed)
            return True, False
        await asyncio.sleep(1)

    logger.error("Engine %s did not become ready within %ds", agent_id[:8], max_wait)
    return False, False
