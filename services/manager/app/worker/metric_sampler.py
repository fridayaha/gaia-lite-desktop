"""Metric sampler — 周期采样引擎 Pod 的 CPU/内存用量，写入 resource_metric_samples。

运行在 Controller 进程中的后台循环（每 60s 一次），为 agent 详情 / 引擎实例页的
CPU/内存趋势图提供历史时序数据。metrics-server 仅提供瞬时值，必须采样落库才能画趋势。

数据流：
  DB 查 RUNNING AgentDeployment → 逐个 get_pod_status 解析真实 pod_name
  → 一次 get_pod_metrics 拉全量瞬时用量 → 解析 cpu_m/memory_mi → 批量 INSERT
  → 每小时清理 ts < now-7d 的旧样本

metrics-server 不可用（ApiException）时跳过本轮并记 warning，历史已落库数据不丢。
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from pkg.common.database import get_db
from pkg.common.models import AgentDeployment, DeploymentStatus, ResourceMetricSample

from .k8s_manager import k8s_manager

logger = logging.getLogger(__name__)

SAMPLE_INTERVAL_SEC = 60  # 采样间隔
RETENTION_DAYS = 7  # 保留期
CLEANUP_EVERY_N_CYCLES = 60  # 每小时清理一次（60 × 60s）


class MetricSampler:
    """周期采样引擎 Pod 资源用量到 DB。"""

    def __init__(self):
        self._task: asyncio.Task | None = None

    def start(self):
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "MetricSampler started: sample every %ds, retain %dd",
            SAMPLE_INTERVAL_SEC,
            RETENTION_DAYS,
        )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self):
        cycle = 0
        while True:
            try:
                await asyncio.sleep(SAMPLE_INTERVAL_SEC)
                await self._sample_once()
                cycle += 1
                if cycle % CLEANUP_EVERY_N_CYCLES == 0:
                    await self._cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("MetricSampler loop error: %s", e)

    async def _sample_once(self):
        """采集一轮：RUNNING 部署 → pod metrics → INSERT。"""
        # 1. 查所有 RUNNING 部署，解析真实 pod_name
        pod_rows: list[tuple[str, str, str]] = []  # (pod_name, instance_id, resource_pool_id)
        async for db in get_db():
            try:
                result = await db.execute(
                    select(AgentDeployment).where(
                        AgentDeployment.status == DeploymentStatus.RUNNING,
                        AgentDeployment.resource_pool_id.is_not(None),
                    )
                )
                deployments = result.scalars().all()
            finally:
                await db.close()

        for dep in deployments:
            agent_id = str(dep.instance_id)
            try:
                status = await k8s_manager.get_pod_status(
                    agent_id,
                    dep.scope_type,
                    dep.scope_target_id,
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("get_pod_status failed for agent %s: %s", agent_id[:8], e)
                continue
            pod_name = status.get("pod_name")
            if not pod_name or not status.get("running"):
                continue
            pod_rows.append((pod_name, agent_id, str(dep.resource_pool_id)))

        if not pod_rows:
            return

        # 2. 一次拉全量 pod metrics（metrics-server）
        pod_names = [r[0] for r in pod_rows]
        try:
            metrics_map = await k8s_manager.get_pod_metrics(pod_names)
        except Exception as e:  # noqa: BLE001
            # metrics-server 未部署 / RBAC 缺失 → 跳过本轮，不阻塞
            logger.warning("MetricSampler: metrics-server unavailable, skip cycle: %s", e)
            return

        # 3. 解析并批量写入
        now = datetime.now(UTC)
        samples: list[ResourceMetricSample] = []
        for pod_name, agent_id, pool_id in pod_rows:
            m = metrics_map.get(pod_name) or {}
            cpu_str = m.get("cpu", "") if isinstance(m, dict) else ""
            mem_str = m.get("memory", "") if isinstance(m, dict) else ""
            # get_pod_metrics 已用 _parse_cpu/_format_mem 转成 "Nm"/"NMi"，空串=0
            cpu_m = _parse_metric_cpu(cpu_str)
            memory_mi = _parse_metric_mem(mem_str)
            samples.append(
                ResourceMetricSample(
                    resource_pool_id=pool_id,
                    pod_name=pod_name,
                    instance_id=agent_id,
                    ts=now,
                    cpu_m=cpu_m,
                    memory_mi=memory_mi,
                )
            )

        if not samples:
            return

        async for db in get_db():
            try:
                db.add_all(samples)
                await db.commit()
                logger.debug("MetricSampler: wrote %d samples", len(samples))
            except Exception as e:  # noqa: BLE001
                logger.warning("MetricSampler: insert failed: %s", e)
                await db.rollback()
            finally:
                await db.close()

    async def _cleanup(self):
        """清理超期样本（>7d）。"""
        threshold = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
        async for db in get_db():
            try:
                await db.execute(
                    delete(ResourceMetricSample).where(ResourceMetricSample.ts < threshold)
                )
                await db.commit()
                logger.info("MetricSampler: cleaned samples older than %dd", RETENTION_DAYS)
            except Exception as e:  # noqa: BLE001
                logger.warning("MetricSampler: cleanup failed: %s", e)
                await db.rollback()
            finally:
                await db.close()


def _parse_metric_cpu(q: str) -> int:
    """get_pod_metrics 输出（'Nm' 或 ''）→ millicores。防御性解析。"""
    if not q:
        return 0
    s = str(q).strip()
    suffixes = {"n": 1e-6, "u": 1e-3, "m": 1, "k": 1e6, "M": 1e9, "G": 1e12, "T": 1e15}
    try:
        for suf, mult in suffixes.items():
            if s.endswith(suf):
                return int(float(s[: -len(suf)]) * mult)
        return int(float(s) * 1000)
    except (ValueError, TypeError):
        return 0


def _parse_metric_mem(q: str) -> int:
    """get_pod_metrics 输出（'NMi' 或 ''）→ Mi。"""
    if not q:
        return 0
    s = str(q).strip()
    try:
        if s.endswith("Mi"):
            return int(float(s[:-2]))
        if s.endswith("Gi"):
            return int(float(s[:-2]) * 1024)
        if s.endswith("Ki"):
            return int(float(s[:-2]) // 1024)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


# Singleton
metric_sampler = MetricSampler()
