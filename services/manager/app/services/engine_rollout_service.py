"""引擎镜像滚动发布 service（A6）。

发版后存量引擎 Deployment 的 image 是创建时烘入的旧值，不随 manager 的 UA_ENGINE_IMAGE
更新。本 service 把所有引擎 Deployment 的 image 批量滚到目标镜像：后台分批 patch image
+ 等 ready，替代手动 kubectl set image。

状态分类（对接 C2 状态机）：
- RUNNING/DEPLOYING/FAILED/PENDING → patch image + 等 ready（READY/FAILED）
- SUSPENDED → 只 patch image 不等 ready（PATCHED），下次 resume 自动拉新镜像
- ARCHIVED / DIFY 外部 → 跳过（SKIPPED）

并发控制：每引擎 patch 前后用 pg_advisory_xact_lock 序列化，避免与 ensure/suspend/destroy
竞态；等 ready 期间不持锁（suspend→replicas=0→wait 返回 True；destroy→404→标 FAILED，
均优雅降级不致损坏）。
"""

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from app.models import (
    AgentDefinition,
    AgentDeployment,
    AgentInstance,
    DeploymentStatus,
    EngineRollout,
    EngineRolloutItem,
    RolloutItemStatus,
    RolloutStatus,
)
from app.worker.k8s_manager import _engine_name, k8s_manager
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pkg.common.config import get_engine_runtime
from pkg.common.database import async_session

logger = logging.getLogger(__name__)

# SUSPENDED 等待会无 pod，只 patch；其余活跃态 patch 后等 ready。
_NEED_WAIT_STATUSES = {
    DeploymentStatus.RUNNING,
    DeploymentStatus.DEPLOYING,
    DeploymentStatus.FAILED,
    DeploymentStatus.PENDING,
}

READY_TIMEOUT = 300  # 单引擎等 ready 超时（秒）

# 是否在 create_rollout 后自动起后台任务跑 run_rollout。测试置 False 以显式 await。
_autolaunch: bool = True

# summary 持久化字段的规范结构（create/run 共用，避免中途字段漂移）。
SUMMARY_KEYS = ("total", "ready", "patched", "failed", "skipped")


def _empty_summary(total: int = 0) -> dict:
    return {k: (total if k == "total" else 0) for k in SUMMARY_KEYS}


def _resolve_target_image(engine_type: str | None) -> str:
    """目标镜像：优先调用方传入，缺省取当前 ENGINE_RUNTIMES（发版后 manager 认定的镜像）。"""
    return get_engine_runtime(engine_type)["image"]


def _is_external_dify(dep: AgentDeployment, engine_type: str | None) -> bool:
    """DIFY 外部实例（无 Pod，engine_url 指外部）→ 跳过。

    镜像 router._is_external_dify_deployment 判定逻辑。
    """
    if (engine_type or "").upper() == "DIFY":
        return True
    url = dep.engine_url or ""
    return bool(url) and ".svc.cluster.local" not in url


async def list_candidates(
    db: AsyncSession, engine_type: str | None
) -> list[tuple[AgentDeployment, str, str]]:
    """返回可参与 rollout 的 (dep, agent_id, engine_type) 列表。

    排除 ARCHIVED（Deployment 已删）与 DIFY 外部实例。engine_type 过滤按定义上的引擎类型。
    """
    stmt = (
        select(AgentDeployment, AgentDefinition.engine_type)
        .join(AgentInstance, AgentDeployment.instance_id == AgentInstance.id)
        .join(AgentDefinition, AgentInstance.definition_id == AgentDefinition.id)
    )
    rows = (await db.execute(stmt)).all()
    out: list[tuple[AgentDeployment, str, str]] = []
    for dep, et in rows:
        et_str = (et.value if hasattr(et, "value") else str(et or "HERMES")).upper()
        if engine_type and et_str != engine_type.upper():
            continue
        if dep.status == DeploymentStatus.ARCHIVED:
            continue
        if _is_external_dify(dep, et_str):
            continue
        out.append((dep, str(dep.instance_id), et_str))
    return out


async def create_rollout(
    db: AsyncSession,
    *,
    engine_type: str | None = None,
    target_image: str | None = None,
    batch_size: int = 5,
    force_repull: bool = False,
    dry_run: bool = False,
    triggered_by: UUID | None = None,
) -> dict:
    """创建 rollout 记录 + items（dry_run 只返回预览不落库、不执行）。

    返回 {rollout_id?, total, running, suspended, skipped, items:[...]}
    items 元素含 agent_id / deployment_name / prev_image / target_image / action。

    engine_type 必填：不同引擎类型镜像不同（HERMES/OPENCLAW 各自独立镜像），无法用单一
    target_image 跨类型滚动。如需滚动全部类型，按类型分别发起 rollout。
    """
    if not engine_type:
        raise ValueError("engine_type 必填")
    target = target_image or _resolve_target_image(engine_type)
    candidates = await list_candidates(db, engine_type)

    items_preview: list[dict] = []
    counts = {"total": 0, "running": 0, "suspended": 0, "skipped": 0}
    for dep, agent_id, et in candidates:
        name = _engine_name(agent_id, dep.scope_type, dep.scope_target_id)
        prev = await asyncio.to_thread(k8s_manager.read_engine_image, name)
        if prev is None:
            # Deployment 不在 K8s（ARCHIVED 态或已删）→ 跳过
            counts["skipped"] += 1
            items_preview.append(
                {
                    "agent_id": agent_id,
                    "deployment_name": name,
                    "prev_image": None,
                    "target_image": target,
                    "action": "skip",
                }
            )
            continue
        action = "wait" if dep.status in _NEED_WAIT_STATUSES else "patch_only"
        if action == "wait":
            counts["running"] += 1
        else:
            counts["suspended"] += 1
        counts["total"] += 1
        items_preview.append(
            {
                "agent_id": agent_id,
                "deployment_name": name,
                "prev_image": prev,
                "target_image": target,
                "action": action,
                "engine_status": dep.status.value
                if hasattr(dep.status, "value")
                else str(dep.status),
            }
        )

    if dry_run:
        return {"dry_run": True, "target_image": target, **counts, "items": items_preview}

    rollout = EngineRollout(
        engine_type=engine_type,
        target_image=target,
        status=RolloutStatus.RUNNING,
        batch_size=max(1, batch_size),
        force_repull=force_repull,
        dry_run=False,
        summary=_empty_summary(counts["total"]),
        triggered_by=triggered_by,
    )
    db.add(rollout)
    await db.flush()
    for dep, agent_id, et in candidates:
        name = _engine_name(agent_id, dep.scope_type, dep.scope_target_id)
        prev = next(
            (it["prev_image"] for it in items_preview if it["deployment_name"] == name),
            None,
        )
        if prev is None:
            continue  # skipped（K8s 无 Deployment），不入 item
        db.add(
            EngineRolloutItem(
                rollout_id=rollout.id,
                agent_id=agent_id,
                deployment_name=name,
                prev_image=prev,
                engine_status=dep.status.value if hasattr(dep.status, "value") else str(dep.status),
                status=RolloutItemStatus.PENDING,
            )
        )
    await db.commit()

    # 后台分批执行（不阻塞请求）
    if _autolaunch:
        asyncio.create_task(run_rollout(str(rollout.id)))
    return {"rollout_id": str(rollout.id), "target_image": target, **counts}


async def _patch_under_lock(
    agent_id: str, name: str, target_image: str, force_repull: bool
) -> str | None:
    """持 agent 级 advisory 锁 patch image，返回旧镜像。

    锁仅覆盖 patch（K8s 调用），不覆盖后续 wait。
    """
    from sqlalchemy import text

    async with async_session() as lock_db:
        await lock_db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:aid))"), {"aid": agent_id}
        )
        old = await asyncio.to_thread(
            k8s_manager.patch_engine_image, name, target_image, force_repull
        )
        await lock_db.commit()
        return old


async def _process_item(item: EngineRolloutItem, rollout: EngineRollout) -> None:
    """处理单个 item：patch image（必要时等 ready），更新 item 状态。"""
    now = datetime.now(UTC)
    item.started_at = now
    item.status = RolloutItemStatus.PATCHED  # 先置 PATCHED，wait 成功再升 READY
    need_wait = (item.engine_status or "RUNNING").upper() in {
        s.value if hasattr(s, "value") else str(s) for s in _NEED_WAIT_STATUSES
    }
    # 镜像已是目标且不强制重拉 → 跳过
    if item.prev_image == rollout.target_image and not rollout.force_repull:
        item.status = RolloutItemStatus.SKIPPED
        item.finished_at = datetime.now(UTC)
        return
    try:
        await _patch_under_lock(
            item.agent_id, item.deployment_name, rollout.target_image, rollout.force_repull
        )
        if not need_wait:
            # SUSPENDED：只 patch 不等 ready
            item.status = RolloutItemStatus.PATCHED
            item.finished_at = datetime.now(UTC)
            return
        # wait_deployment_ready 是同步阻塞调用，丢线程池跑以免阻塞事件循环
        ready = await asyncio.to_thread(
            k8s_manager.wait_deployment_ready,
            item.deployment_name,
            rollout.target_image,
            READY_TIMEOUT,
        )
        item.status = RolloutItemStatus.READY if ready else RolloutItemStatus.FAILED
        if not ready:
            item.error = "等待引擎就绪超时"
    except Exception as e:  # noqa: BLE001 — 记录任意失败原因到 item
        logger.exception("rollout item %s failed", item.deployment_name)
        item.status = RolloutItemStatus.FAILED
        item.error = str(e)[:500]
    item.finished_at = datetime.now(UTC)


async def interrupt_stale_rollouts(db: AsyncSession) -> int:
    """启动时清理：把所有 RUNNING 的 rollout 标 FAILED。

    run_rollout 是内存中的 asyncio 后台任务，进程重启即丢失，未完成的 rollout 会永久
    卡在 RUNNING。manager 启动时调本函数把残留 RUNNING 标为 FAILED（error=进程重启中断），
    避免前端轮询永远转圈。返回清理条数。
    """
    rows = (
        (
            await db.execute(
                select(EngineRollout).where(EngineRollout.status == RolloutStatus.RUNNING)
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.status = RolloutStatus.FAILED
        r.finished_at = datetime.now(UTC)
        summary = dict(r.summary or _empty_summary())
        summary["failed"] = summary.get("total", 0)
        summary["interrupted"] = True
        r.summary = summary
    if rows:
        await db.commit()
        logger.warning("interrupted %d stale RUNNING rollouts on startup", len(rows))
    return len(rows)


async def run_rollout(rollout_id: str) -> None:
    """后台分批执行 rollout。批内并发 patch+wait，批间串行。"""
    try:
        await _run_rollout(rollout_id)
    except Exception:
        logger.exception("rollout %s crashed", rollout_id)
        # 兜底：任何未预期异常都把 rollout 标 FAILED，避免永久卡在 RUNNING
        try:
            async with async_session() as db:
                rollout = (
                    await db.execute(
                        select(EngineRollout).where(EngineRollout.id == UUID(rollout_id))
                    )
                ).scalar_one_or_none()
                if rollout and rollout.status == RolloutStatus.RUNNING:
                    rollout.status = RolloutStatus.FAILED
                    rollout.finished_at = datetime.now(UTC)
                    summary = dict(rollout.summary or {})
                    summary["crashed"] = True
                    rollout.summary = summary
                    await db.commit()
        except Exception:
            logger.exception("rollout %s crash fallback failed", rollout_id)


async def _run_rollout(rollout_id: str) -> None:
    async with async_session() as db:
        rollout = (
            await db.execute(
                select(EngineRollout)
                .where(EngineRollout.id == UUID(rollout_id))
                .options(selectinload(EngineRollout.items))
            )
        ).scalar_one_or_none()
        if not rollout:
            logger.error("rollout %s not found", rollout_id)
            return

        items = sorted(rollout.items, key=lambda it: it.deployment_name)
        batch_size = max(1, rollout.batch_size)
        summary = _empty_summary(len(items))

        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            await asyncio.gather(*(_process_item(it, rollout) for it in batch))
            # 每批提交一次，外部可查中间进度
            for it in batch:
                _tally(summary, it.status)
                db.add(it)
            rollout.summary = dict(summary)
            await db.commit()

        rollout.status = (
            RolloutStatus.FAILED
            if summary["failed"] == summary["total"] and summary["total"] > 0
            else RolloutStatus.FINISHED
        )
        rollout.finished_at = datetime.now(UTC)
        rollout.summary = dict(summary)
        await db.commit()
        logger.info("rollout %s finished: %s", rollout_id, summary)


def _tally(summary: dict, status: RolloutItemStatus) -> None:
    key = {
        RolloutItemStatus.READY: "ready",
        RolloutItemStatus.PATCHED: "patched",
        RolloutItemStatus.FAILED: "failed",
        RolloutItemStatus.SKIPPED: "skipped",
        RolloutItemStatus.PENDING: "pending",
    }.get(status)
    if key and key in summary:
        summary[key] += 1


async def get_rollout(db: AsyncSession, rollout_id: UUID) -> dict:
    """返回 rollout 进度（summary + items 分组计数 + 详情）。"""
    rollout = (
        await db.execute(
            select(EngineRollout)
            .where(EngineRollout.id == rollout_id)
            .options(selectinload(EngineRollout.items))
        )
    ).scalar_one_or_none()
    if not rollout:
        return None
    items = [
        {
            "agent_id": it.agent_id,
            "deployment_name": it.deployment_name,
            "prev_image": it.prev_image,
            "target_image": rollout.target_image,
            "status": it.status.value if hasattr(it.status, "value") else str(it.status),
            "error": it.error,
            "started_at": it.started_at.isoformat() if it.started_at else None,
            "finished_at": it.finished_at.isoformat() if it.finished_at else None,
        }
        for it in rollout.items
    ]
    return {
        "rollout_id": str(rollout.id),
        "engine_type": rollout.engine_type,
        "target_image": rollout.target_image,
        "status": rollout.status.value if hasattr(rollout.status, "value") else str(rollout.status),
        "batch_size": rollout.batch_size,
        "force_repull": rollout.force_repull,
        "dry_run": rollout.dry_run,
        "summary": rollout.summary,
        "started_at": rollout.started_at.isoformat() if rollout.started_at else None,
        "finished_at": rollout.finished_at.isoformat() if rollout.finished_at else None,
        "items": items,
    }


async def list_rollouts(db: AsyncSession, limit: int = 20) -> list[dict]:
    rows = (
        (
            await db.execute(
                select(EngineRollout).order_by(EngineRollout.started_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "rollout_id": str(r.id),
            "engine_type": r.engine_type,
            "target_image": r.target_image,
            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "summary": r.summary,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]
