"""引擎生命周期后台调度循环 — 由 background.py 驱动。

_check_and_suspend/_check_and_destroy/_check_and_daily_backup/_check_and_daily_cleanup/
_update_last_active/_reconcile_finalizers/_backup_pod_on_destroy。从 router.py 拆出。

suspend/destroy 实体仍在 router（阶段6迁 lifecycle_service），本模块 from .router import。
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from pkg.common.config import settings
from pkg.common import database as _db
from pkg.common.models import AgentDeployment, DeploymentStatus

from ._common import is_external_dify_deployment as _is_external_dify_deployment
from ._common import load_group_code as _load_group_code
from .k8s_manager import k8s_manager
from .lifecycle_service import destroy as _do_destroy, suspend as _do_suspend
from .lifecycle_service import reconcile_status
from .minio_archiver import archiver

logger = logging.getLogger(__name__)

# 每日全量备份上次执行日期（UTC YYYYMMDD），避免在同一触发小时内重复执行
_daily_backup_last_run_date: str | None = None


async def _check_and_suspend():
    """遍历所有 RUNNING 部署，空闲超 30min 则存档+休眠

    外部 Dify 实例不参与自动休眠（外部实例由用户自管，无 Pod 可 scale）。
    """
    async for db in _db.get_db():
        try:
            threshold = datetime.now(UTC) - timedelta(minutes=settings.idle_suspend_minutes)
            result = await db.execute(
                select(AgentDeployment).where(
                    AgentDeployment.status == DeploymentStatus.RUNNING,
                    AgentDeployment.last_active_at < threshold,
                )
            )
            deployments = result.scalars().all()
            for dep in deployments:
                if _is_external_dify_deployment(dep):
                    # 外部 Dify 实例不自动休眠
                    continue
                agent_id = str(dep.instance_id)
                logger.info(f"Idle suspend: agent {agent_id} (last_active: {dep.last_active_at})")
                try:
                    await _do_suspend(agent_id, db)
                except Exception as e:
                    logger.error(f"Failed to suspend agent {agent_id}: {e}")
        finally:
            await db.close()


async def _backup_pod_on_destroy(agent_id: str, pod_name: str) -> None:
    """销毁前把 Pod 内 /opt/data 备份到当日 daily（finalizer reconcile 与外部销毁共用）。

    group_code 从 DB 取（instance 不存在时回退 default）；tar 失败则向上抛出，由
    reconcile 决定重试或超时放行。
    """
    _group_code = None
    async for db in _db.get_db():
        try:
            _group_code = await _load_group_code(db, agent_id)
        finally:
            await db.close()
        break
    tar_data = await k8s_manager.exec_tar_data_by_pod(pod_name, agent_id_tag=agent_id)
    archiver.save_daily(agent_id, tar_data, group_code=_group_code)
    logger.info("finalizer backup saved for %s (pod %s)", agent_id[:8], pod_name)


async def _reconcile_finalizers():
    """扫描 Terminating 且带 data-backup finalizer 的引擎 Pod，销毁前备份再放行。

    容器已 terminated（k8s 在 grace period 后 SIGKILL）时跳过备份、直接移除 finalizer：
    finalizer 只阻止 pod 对象删除，不阻止容器终止 → 容器死后 exec 进不去 → 备份无意义，
    数据在 PVC 上（retained）或已归档（SUSPEND 时备份过）。
    超时兜底：容器还活着但 exec 持续失败超 finalizer_backup_timeout_minutes → 强制放行。
    """
    try:
        pods = await k8s_manager.list_terminating_engine_pods()
    except Exception as e:
        logger.warning("reconcile finalizers list failed: %s", e)
        return
    now = datetime.now(UTC)
    timeout = timedelta(minutes=settings.finalizer_backup_timeout_minutes)
    for p in pods:
        agent_id = p["agent_id"]
        pod_name = p["pod_name"]
        if not agent_id or not pod_name:
            continue

        # 容器已 terminated → exec 进不去 → 跳过备份，直接放行
        # （finalizer 不阻止 k8s 杀容器，只阻止 pod 对象删除；容器死后备份无意义）
        if not await k8s_manager.is_pod_container_running(pod_name):
            logger.info(
                "finalizer: pod %s container not running, skip backup, remove finalizer",
                pod_name,
            )
            try:
                await k8s_manager.remove_finalizer(pod_name)
            except Exception as ee:
                logger.error("skip-backup remove finalizer %s failed: %s", pod_name, ee)
            continue

        try:
            await _backup_pod_on_destroy(agent_id, pod_name)
            await k8s_manager.remove_finalizer(pod_name)
        except Exception as e:
            # 备份失败后再次检查容器是否还活着（可能在 exec 期间被 k8s 杀掉）
            if not await k8s_manager.is_pod_container_running(pod_name):
                logger.info(
                    "finalizer: pod %s container died during backup, skip, remove finalizer",
                    pod_name,
                )
                try:
                    await k8s_manager.remove_finalizer(pod_name)
                except Exception as ee:
                    logger.error("post-fail remove finalizer %s failed: %s", pod_name, ee)
                continue

            ts = p.get("terminating_since")
            if ts and (now - ts) > timeout:
                logger.error(
                    "finalizer backup timeout for %s (pod %s), force-removing finalizer: %s",
                    agent_id[:8], pod_name, e,
                )
                try:
                    await k8s_manager.remove_finalizer(pod_name)
                except Exception as ee:
                    logger.error("force-remove finalizer %s failed: %s", pod_name, ee)
            else:
                logger.warning(
                    "finalizer backup failed for %s (pod %s), will retry next round: %s",
                    agent_id[:8], pod_name, e,
                )


async def _check_and_daily_backup():
    """每日定时对 RUNNING 引擎全量备份到 daily-{date}（缩小 RPO 到 ≤1 天）。

    触发条件：UTC 小时 == daily_backup_hour 且今日尚未执行。逐引擎 tar → save_daily，
    单引擎失败不阻断其他。finalizer 已覆盖销毁即时备份，此处兜底运行中状态的日快照。
    """
    global _daily_backup_last_run_date
    now = datetime.now(UTC)
    if now.hour != settings.daily_backup_hour:
        return
    today = now.strftime("%Y%m%d")
    if _daily_backup_last_run_date == today:
        return
    _daily_backup_last_run_date = today

    async for db in _db.get_db():
        try:
            result = await db.execute(
                select(AgentDeployment).where(
                    AgentDeployment.status == DeploymentStatus.RUNNING
                )
            )
            for dep in result.scalars().all():
                agent_id = str(dep.instance_id)
                try:
                    _gc = await _load_group_code(db, agent_id)
                    tar_data = await k8s_manager.exec_tar_data(agent_id)
                    archiver.save_daily(agent_id, tar_data, group_code=_gc, date_str=today)
                except Exception as e:
                    logger.warning("daily backup failed for %s: %s", agent_id[:8], e)
        finally:
            await db.close()
        break
    logger.info("daily backup pass done for %s", today)


async def _check_and_daily_cleanup():
    """清理 daily_backup_retain_days 天前的 daily-* 备份（永久 archive 不受此限）。"""
    async for db in _db.get_db():
        try:
            result = await db.execute(select(AgentDeployment))
            for dep in result.scalars().all():
                agent_id = str(dep.instance_id)
                try:
                    _gc = await _load_group_code(db, agent_id)
                    archiver.delete_daily_older_than(
                        agent_id, group_code=_gc, days=settings.daily_backup_retain_days
                    )
                except Exception as e:
                    logger.warning("daily cleanup failed for %s: %s", agent_id[:8], e)
        finally:
            await db.close()
        break


async def _check_and_destroy():
    """遍历所有 SUSPENDED 部署，存档超 24h 则清理 K8s 资源"""
    async for db in _db.get_db():
        try:
            threshold = datetime.now(UTC) - timedelta(hours=settings.idle_destroy_hours)
            result = await db.execute(
                select(AgentDeployment).where(
                    AgentDeployment.status == DeploymentStatus.SUSPENDED,
                    AgentDeployment.backup_at.isnot(None),
                    AgentDeployment.backup_at < threshold,
                )
            )
            deployments = result.scalars().all()
            for dep in deployments:
                agent_id = str(dep.instance_id)
                logger.info(f"Idle destroy: agent {agent_id} (backup_at: {dep.backup_at})")
                try:
                    await _do_destroy(agent_id, db)
                except Exception as e:
                    logger.error(f"Failed to destroy agent {agent_id}: {e}")
        finally:
            await db.close()


async def _update_last_active():
    """更新 RUNNING 部署的 last_active_at（通过 K8s 获取 Pod 状态确认存活）

    同时修正 DB status：
      - Pod 不存在但有 MinIO backup（正常 SUSPEND）→ SUSPENDED
      - Pod 不存在且无 backup（外部误删）→ FAILED
      - Pod 非运行 → FAILED
    同时巡检 profile 一致性：
      - Pod Running 但 DB AgentProfile 记录的目录在 Pod 上不存在 → 删 stale 记录
    """
    from pkg.common.models import AgentProfile

    async for db in _db.get_db():
        try:
            now = datetime.now(UTC)
            result = await db.execute(
                select(AgentDeployment).where(
                    AgentDeployment.status == DeploymentStatus.RUNNING,
                )
            )
            deployments = result.scalars().all()
            for dep in deployments:
                agent_id = str(dep.instance_id)
                # 外部 Dify 实例：无 Pod 可探，直接刷新 last_active_at（外部实例由用户自管）
                if _is_external_dify_deployment(dep):
                    dep.last_active_at = now
                    await db.commit()
                    continue
                try:
                    pod_status = await k8s_manager.get_pod_status(agent_id)
                    if pod_status["running"]:
                        dep.last_active_at = now
                        await db.commit()

                        # 巡检 profile 一致性：DB 有记录但 Pod 上目录不存在 → 删 stale
                        prof_result = await db.execute(
                            select(AgentProfile).where(AgentProfile.instance_id == agent_id)
                        )
                        profiles = prof_result.scalars().all()
                        for prof in profiles:
                            if not prof.internal_port:
                                continue
                            try:
                                check = await k8s_manager.exec_hermes_command(
                                    agent_id=agent_id,
                                    commands=[
                                        f"test -d /opt/data/profiles/{prof.profile_name} "
                                        "&& echo EXISTS || echo MISSING"
                                    ],
                                )
                                if "MISSING" in (check or ""):
                                    logger.warning(
                                        "Reconcile: profile %s dir missing on pod for agent %s, "
                                        "deleting stale DB record",
                                        prof.profile_name[:16],
                                        agent_id[:8],
                                    )
                                    await db.execute(
                                        text("DELETE FROM agent_profiles WHERE id = :pid"),
                                        {"pid": str(prof.id)},
                                    )
                                    await db.commit()
                            except Exception:
                                pass  # 单次 exec 失败不影响其他
                    else:
                        # NotFound：查 MinIO 备份精确判 SUSPENDED/FAILED；unhealthy：race 保护标 FAILED。
                        # 漂移纠正集中在 lifecycle_service.reconcile_status。
                        has_backup: bool | None = None
                        if pod_status.get("phase") == "NotFound":
                            _gc = await _load_group_code(db, agent_id)
                            has_backup = archiver.backup_exists(agent_id, group_code=_gc)
                        await reconcile_status(db, dep, pod_status, has_backup=has_backup)
                except Exception:
                    pass  # 单次失败不影响其他部署
        finally:
            await db.close()
