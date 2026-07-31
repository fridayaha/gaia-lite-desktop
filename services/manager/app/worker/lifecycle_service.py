"""引擎状态机核心 — suspend/destroy 集中实现 + set_status 统一写入口（C2）。

_do_suspend/_do_destroy 被 lifecycle 端点与 scheduler 后台循环共用（单向收敛），
迁到此模块后 scheduler/background 不再依赖 router。set_status 为状态写入唯一入口，
记 from→to 日志；其他字段(backup_at/archived_at/error_message)由调用方在 commit 前设置。
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import settings
from pkg.common.models import AgentDeployment, DeploymentStatus

from ._common import (
    acquire_agent_lock as _acquire_agent_lock,
    delete_browser_pods_for_deployment,
    is_external_dify_deployment as _is_external_dify_deployment,
    load_group_code as _load_group_code,
    suspend_browser_pods_for_deployment,
)
from .k8s_manager import _pvc_name, k8s_manager
from .minio_archiver import archiver

logger = logging.getLogger(__name__)

# SUSPEND 备份连续失败计数（agent_id -> 次数）。超过 _SUSPEND_MAX_FAILS 置 FAILED，
# 避免引擎 exec 损坏时无声占用资源、永不休眠。成功后清零。
_suspend_fail_count: dict[str, int] = {}
_SUSPEND_MAX_FAILS = 3


async def set_status(
    db: AsyncSession,
    dep: AgentDeployment | None,
    target: DeploymentStatus,
    *,
    commit: bool = True,
    log: bool = True,
) -> None:
    """集中写 dep.status：记 from→to 日志 + 可选 commit。

    仅写 status 字段；error_message/backup_at/archived_at 等由调用方在 commit 前
    自行设置（保留各路径既有副作用语义，不引入行为变更）。log=False 时抑制通用
    transition 日志（由调用方记上下文日志，如 reconcile_status）。
    """
    if dep is None:
        return
    prev = dep.status
    dep.status = target
    if log and prev != target:
        logger.info("status %s: %s -> %s", str(dep.instance_id)[:8], prev, target)
    if commit:
        await db.commit()


# ── 漂移纠正（reconcile）─────────────────────────────────
# get_agent_status（读路径，乐观）与 _update_last_active（定时巡检，查备份）共用。
# race 保护集中在 classify_drift 单一实现：SUSPENDED/ARCHIVED 的 Failed pod 是
# scale_to_zero 杀的预期相，不覆盖；terminating 窗口（suspend/destroy 杀 pod）不标 FAILED。


def classify_drift(
    dep: AgentDeployment,
    pod_status: dict,
    *,
    has_backup: bool | None = None,
) -> DeploymentStatus | None:
    """根据 dep 当前状态 + pod_status 判定应写入的目标状态；None 表示不变。

    - running：dep 非 RUNNING → RUNNING（恢复）；已 RUNNING → None（调用方刷 last_active）
    - NotFound：dep 非 RUNNING → None（FAILED/ARCHIVED 保持）；dep RUNNING →
      has_backup=False → FAILED（外部误删）；has_backup=True/None → SUSPENDED
      （None = read 路径乐观，不查备份）
    - 其它（unhealthy）：terminating → None；dep ∈ {FAILED,SUSPENDED,ARCHIVED} → None；
      否则 FAILED
    """
    if pod_status.get("running"):
        if dep.status != DeploymentStatus.RUNNING:
            return DeploymentStatus.RUNNING
        return None
    if pod_status.get("phase") == "NotFound":
        # 仅 RUNNING 态纠正（FAILED/ARCHIVED 确无 pod，保持）
        if dep.status != DeploymentStatus.RUNNING:
            return None
        if has_backup is False:
            return DeploymentStatus.FAILED
        return DeploymentStatus.SUSPENDED  # has_backup True 或 None（乐观）
    # pod 存在但非运行（Pending/CrashLoopBackOff/Failed phase）
    if pod_status.get("terminating"):
        return None
    if dep.status in (
        DeploymentStatus.FAILED,
        DeploymentStatus.SUSPENDED,
        DeploymentStatus.ARCHIVED,
    ):
        return None
    return DeploymentStatus.FAILED


async def reconcile_status(
    db: AsyncSession,
    dep: AgentDeployment,
    pod_status: dict,
    *,
    has_backup: bool | None = None,
) -> DeploymentStatus:
    """按 pod 漂移纠正 dep.status（经 classify_drift），返回（可能更新后的）status。

    命中目标则写 status + error_message + commit + 上下文日志；未命中（None）不动。
    has_backup=None（read 路径）时 NotFound 乐观判 SUSPENDED；显式 True/False（sweep）精确判。
    error_message：RUNNING/SUSPENDED 清空；unhealthy FAILED 用 phase/reason；
    NotFound FAILED（外部误删）用固定提示。
    """
    target = classify_drift(dep, pod_status, has_backup=has_backup)
    if target is None:
        return dep.status
    aid = str(dep.instance_id)[:8]
    if target == DeploymentStatus.RUNNING:
        dep.error_message = None
        await set_status(db, dep, target, log=False)
        logger.info("reconcile %s: pod running, recovered -> RUNNING", aid)
    elif target == DeploymentStatus.SUSPENDED:
        dep.error_message = None
        await set_status(db, dep, target, log=False)
        logger.info("reconcile %s: pod not found%s -> SUSPENDED", aid, " (backup)" if has_backup else "")
    else:  # FAILED
        if pod_status.get("phase") == "NotFound":
            dep.error_message = (
                "Engine Pod was removed externally, "
                "session data may have been lost. Redeploy to start fresh."
            )
        else:
            dep.error_message = (
                f"Pod phase: {pod_status.get('phase')}, reason: {pod_status.get('reason')}"
            )
        await set_status(db, dep, target, log=False)
        logger.error(
            "reconcile %s: -> FAILED (phase=%s, has_backup=%s)",
            aid, pod_status.get("phase"), has_backup,
        )
    return dep.status


async def suspend(agent_id: str, db: AsyncSession | None = None):
    """存档数据到 MinIO → scale=0 → 更新状态

    备份失败时 **不** scale_to_zero、**不** 置 SUSPENDED、**不** 写 backup_at：
    状态保持 RUNNING，下轮 _check_and_suspend（只选 RUNNING）真正重试——修正原来
    「吞异常 + 仍记 backup_at → 24h 后无备份 DESTROY 删 PVC」的静默丢数据路径。
    连续失败超上限则置 FAILED 交运维。
    """
    from pkg.common.database import get_db

    if db is None:
        async for session in get_db():
            db = session
            break

    # 序列化：与 destroy/deploy 互斥，避免并发 suspend/destroy 冲突
    await _acquire_agent_lock(db, agent_id)

    # 取 dep 提前判断外部 Dify（无 Pod，跳过 K8s ops + 备份）
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()
    if _is_external_dify_deployment(dep):
        if dep:
            dep.backup_at = datetime.now(UTC)
            await set_status(db, dep, DeploymentStatus.SUSPENDED)
        logger.info(
            "SUSPEND %s: external Dify instance, skip K8s/backup ops",
            agent_id[:8],
        )
        return

    # UserGroup 隔离：取 group_code 用于 MinIO 组前缀（instance 不存在时回退 default）
    _group_code = await _load_group_code(db, agent_id)

    # Step 1: exec tar → MinIO daily（PVC 存在且配置跳过时不做；V2 强制 PVC）
    _skip_backup = False
    if settings.pvc_skip_backup_on_suspend:
        try:
            _pvc = _pvc_name(agent_id)
            if k8s_manager.pvc_exists(_pvc):
                _skip_backup = True
                logger.info(
                    "SUSPEND %s: PVC %s exists, skipping tar backup (DR-only)", agent_id[:8], _pvc
                )
        except Exception:
            pass  # PVC 检测失败时回退到 tar 备份

    if not _skip_backup:
        try:
            tar_data = await k8s_manager.exec_tar_data(agent_id)
            archiver.save_daily(agent_id, tar_data, group_code=_group_code)
            _suspend_fail_count.pop(agent_id, None)  # 成功，清零
        except Exception as e:
            cnt = _suspend_fail_count.get(agent_id, 0) + 1
            _suspend_fail_count[agent_id] = cnt
            if cnt >= _SUSPEND_MAX_FAILS:
                # 持续失败：置 FAILED 让运维介入，避免无声资源占用
                result = await db.execute(
                    select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
                )
                dep = result.scalar_one_or_none()
                if dep:
                    dep.error_message = f"suspend backup failed {cnt} times: {e}"
                    await set_status(db, dep, DeploymentStatus.FAILED)
                _suspend_fail_count.pop(agent_id, None)
                logger.error(
                    "SUSPEND %s marked FAILED after %d backup failures: %s",
                    agent_id[:8], cnt, e,
                )
                return
            # 未达上限：保持 RUNNING，下轮重试（不 scale / 不置 SUSPENDED / 不写 backup_at）
            logger.warning(
                "SUSPEND backup failed for %s (attempt %d, will retry next round): %s",
                agent_id[:8], cnt, e,
            )
            raise

    # Step 2: 先置 SUSPENDED（避免 get_agent_status 在 scale_to_zero 杀 pod 的窗口内
    # 看到 Failed pod 把状态覆盖成 FAILED）。scale 失败时 pod 仍 Running，get_agent_status
    # 会自动恢复 RUNNING，自愈。
    if dep:
        dep.backup_at = datetime.now(UTC)
        await set_status(db, dep, DeploymentStatus.SUSPENDED)

    # Step 3: scale to 0（先移除 finalizer：备份已同步完成，避免 reconcile 重复 tar）
    try:
        await k8s_manager.remove_finalizer_from_agent_pods(agent_id)
    except Exception as e:
        logger.warning("remove finalizer before suspend %s failed: %s", agent_id[:8], e)
    # browser Pod 同步休眠（scale=0，留 PVC 登录态），best-effort 不阻断引擎休眠
    if dep:
        await suspend_browser_pods_for_deployment(dep, agent_id)
    await k8s_manager.scale_to_zero(agent_id)


async def destroy(agent_id: str, db: AsyncSession | None = None):
    """确认 SUSPEND 存档 → 复制到 archives → 删除 K8s 资源 → 原子清理所有状态

    数据安全约束：若 pvc_reclaim_on_destroy=True（要删 PVC）却没有有效归档，
    **raise 保持 SUSPENDED 不删 PVC**——避免 MinIO/OBS 在销毁窗口不可用时永久丢数据。
    reclaim=False 时 PVC 保留，无需归档（skip 模式 DR-only 场景）。
    """
    from pkg.common.database import get_db

    if db is None:
        async for session in get_db():
            db = session
            break

    # 序列化：与 suspend/deploy 互斥，避免销毁与恢复竞态删刚拉起的 Pod/PVC
    await _acquire_agent_lock(db, agent_id)

    # 取 dep 提前判断外部 Dify（无 K8s 资源，跳过 delete_agent_engine + 归档）
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()
    if _is_external_dify_deployment(dep):
        if dep:
            dep.archived_at = datetime.now(UTC)
            dep.internal_port_map = {"profiles": {}}
            await set_status(db, dep, DeploymentStatus.ARCHIVED)
        # 外部实例无 AgentProfile 记录（Profile 概念仅 Hermes 适用），仍兜底清理
        await db.execute(
            text("DELETE FROM agent_profiles WHERE instance_id = :aid"),
            {"aid": agent_id},
        )
        await db.commit()
        logger.info(
            "DESTROY %s: external Dify instance, skip K8s/archive ops",
            agent_id[:8],
        )
        return

    # UserGroup 隔离：取 group_code 用于 MinIO 组前缀（instance 不存在时回退 default）
    _group_code = await _load_group_code(db, agent_id)

    archive_path = None
    if archiver.backup_exists(agent_id, group_code=_group_code):
        archive_path = archiver.archive_backup(agent_id, group_code=_group_code)
        logger.info(f"Archived data for {agent_id}: {archive_path}")

    # 删 PVC 却无有效归档 → 拒删保现场（MinIO 不可用 / copy 失败 / 无备份）
    if settings.pvc_reclaim_on_destroy and not archive_path:
        raise RuntimeError(
            f"DESTROY refused for {agent_id[:8]}: PVC would be deleted but no valid archive "
            "(MinIO unavailable or no backup). Keeping SUSPENDED for retry."
        )

    # 先移除 finalizer（备份已在上面完成，避免 reconcile 重复 tar 一个要死的 pod）
    try:
        await k8s_manager.remove_finalizer_from_agent_pods(agent_id)
    except Exception as e:
        logger.warning("remove finalizer before destroy %s failed: %s", agent_id[:8], e)

    # 删除 K8s 资源（delete_agent_engine 内部按 pvc_reclaim_on_destroy 决定是否删 PVC）
    # browser Pod 先于引擎删（独立 Pod+PVC，best-effort 不阻断引擎销毁）
    if dep:
        await delete_browser_pods_for_deployment(dep, agent_id)
    await k8s_manager.delete_agent_engine(agent_id)

    # 更新 DB（dep 已在前面取）
    if dep:
        dep.archived_at = datetime.now(UTC)
        if archive_path:
            # archive_path 已含 groups/{group_code}/ 前缀，直接拼 s3://
            dep.archive_path = f"s3://{settings.minio_bucket}/{archive_path}"
        # 清空 port_map（防止 stale profile port 残留）
        dep.internal_port_map = {"profiles": {}}
        await set_status(db, dep, DeploymentStatus.ARCHIVED)

    # 删除 AgentProfile 记录（PVC 已清，DB 记录必须同步清理）
    await db.execute(
        text("DELETE FROM agent_profiles WHERE instance_id = :aid"),
        {"aid": agent_id},
    )
    await db.commit()
    logger.info("Cleaned up AgentProfile records for %s", agent_id[:8])

    # 清理孤儿 engine-config 对象（DESTROY 成功后，避免存储泄漏；best-effort）
    try:
        archiver.delete_engine_config(agent_id, group_code=_group_code)
    except Exception as e:
        logger.warning(f"cleanup engine-config for {agent_id[:8]} failed: {e}")
