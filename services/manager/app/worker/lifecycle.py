"""引擎生命周期 API — /api/controller/agents/{id}/status|deploy|suspend|resume|restart|destroy

从 router.py 拆出，路径不变。suspend/destroy 实体在 lifecycle_service（C2 集中状态机）。
"""

import asyncio
import logging
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.config import get_engine_runtime, settings
from pkg.common.database import get_db as get_manager_db
from pkg.common.models import AgentDeployment, DeploymentStatus

from ._common import (
    AgentStatusResponse,
    acquire_agent_lock as _acquire_agent_lock,
    build_engine_envs as _build_engine_envs,
    is_external_dify_deployment as _is_external_dify_deployment,
    load_instance_config as _load_instance_config,
    load_resource_spec as _load_resource_spec,
    resume_browser_pods_for_deployment,
)
from .config_skills import reconcile_skills, replay_persona_and_skills as _replay_persona_and_skills
from .k8s_manager import _pvc_name, k8s_manager
from .lifecycle_service import destroy as _do_destroy, suspend as _do_suspend
from .lifecycle_service import reconcile_status
from .minio_archiver import archiver

router = APIRouter()

logger = logging.getLogger(__name__)


@router.get("/api/controller/agents/{agent_id}/status")
async def get_agent_status(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """查询 Agent 引擎部署状态（按需 reconciliation：验证 Pod 实际存活性）"""
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()

    if not dep:
        # 无部署记录 → 新建
        return AgentStatusResponse(
            agent_id=agent_id,
            status=DeploymentStatus.PENDING,
        )

    # DEPLOYING 态：后台任务 _run_deploy 权威控制状态转移，reconciliation 不得干预。
    # 部署期 Pod 会先 Pending（否则会被下方 else 分支误判 FAILED）、再 Running 但引擎
    # DEPLOYING 态：后台任务 _run_deploy 权威控制状态转移，reconciliation 不得干预。
    # 但若 pod 已 Ready（deploy 成功）而 _run_deploy 被中断/取消未更新状态，
    # 据此恢复 RUNNING，避免永久卡 DEPLOYING（UI 无部署按钮、API 409）。
    if dep.status == DeploymentStatus.DEPLOYING:
        pod_name = None
        pod_start_time = None
        pod_phase = None
        try:
            pod_status = await k8s_manager.get_pod_status(agent_id)
            pod_name = pod_status.get("pod_name")
            pod_start_time = pod_status.get("start_time")
            pod_phase = pod_status.get("phase")
            # Pod 已 Ready → deploy 成功但状态未更新 → 恢复 RUNNING
            if pod_name and await k8s_manager.is_pod_ready(pod_name):
                dep.status = DeploymentStatus.RUNNING
                dep.error_message = None
                await db.commit()
                logger.info(
                    "get_agent_status: DEPLOYING %s pod Ready, recovered -> RUNNING",
                    agent_id[:8],
                )
                return AgentStatusResponse(
                    agent_id=str(dep.instance_id),
                    status=DeploymentStatus.RUNNING,
                    engine_url=dep.engine_url,
                    last_active_at=dep.last_active_at.isoformat() if dep.last_active_at else None,
                    error_message=None,
                    pod_name=pod_name,
                    pod_start_time=pod_start_time,
                    pod_phase=pod_phase,
                )
        except Exception:
            logger.warning(
                f"get_agent_status: K8s probe failed for DEPLOYING {agent_id[:8]}",
                exc_info=True,
            )
        return AgentStatusResponse(
            agent_id=str(dep.instance_id),
            status=DeploymentStatus.DEPLOYING,
            engine_url=dep.engine_url,
            last_active_at=dep.last_active_at.isoformat() if dep.last_active_at else None,
            error_message=dep.error_message,
            pod_name=pod_name,
            pod_start_time=pod_start_time,
            pod_phase=pod_phase,
        )

    # 外部 Dify 实例：无 Pod 可探，直接返回 RUNNING（engine_url 指向外部 SaaS/自托管 Dify）
    # 必须在 K8s Pod 查询之前短路，否则 get_pod_status 返回 NotFound 会把 RUNNING 误改为 SUSPENDED
    if _is_external_dify_deployment(dep):
        return AgentStatusResponse(
            agent_id=str(dep.instance_id),
            status=DeploymentStatus.RUNNING,
            engine_url=dep.engine_url,
            last_active_at=dep.last_active_at.isoformat() if dep.last_active_at else None,
            error_message=dep.error_message,
            pod_name=None,
            pod_start_time=None,
            pod_phase=None,
        )

    # 按需验证 K8s Pod 实际存活性，纠正陈旧状态（漂移纠正集中在 lifecycle_service.reconcile_status）
    # has_backup=None：read 路径乐观判 SUSPENDED，不引入 MinIO 备份查询避免轮询延迟。
    pod_name = None
    pod_start_time = None
    pod_phase = None
    try:
        pod_status = await k8s_manager.get_pod_status(agent_id)
        pod_name = pod_status.get("pod_name")
        pod_start_time = pod_status.get("start_time")
        pod_phase = pod_status.get("phase")
        await reconcile_status(db, dep, pod_status)
    except Exception:
        logger.warning(
            f"get_agent_status: K8s API call failed for {agent_id[:8]}, falling back to DB status",
            exc_info=True,
        )
        # K8s API 失败时降级，返回 DB 值

    return AgentStatusResponse(
        agent_id=str(dep.instance_id),
        status=dep.status,
        engine_url=dep.engine_url,
        last_active_at=dep.last_active_at.isoformat() if dep.last_active_at else None,
        error_message=dep.error_message,
        pod_name=pod_name,
        pod_start_time=pod_start_time,
        pod_phase=pod_phase,
    )


# ── V3 三层模型读取 helpers（实现在 _common，按 _ 名 re-export 供本模块内部 + 测试 patch）──


class DeployRequest(BaseModel):
    scope_type: str = "ALL"
    scope_target_id: str | None = None


@router.post("/api/controller/agents/{agent_id}/deploy")
async def deploy_agent(
    agent_id: str,
    body: DeployRequest = DeployRequest(),
    db: AsyncSession = Depends(get_manager_db),
):
    """创建/恢复 Agent 引擎部署（支持 scope 维度）。

    异步：立即将 dep 置 DEPLOYING 并返回，主体在后台任务 _run_deploy 中执行；
    前端轮询 GET /status（含 pod_phase）直至 RUNNING/FAILED。
    """
    scope_type = body.scope_type
    scope_target_id = body.scope_target_id

    # 按 scope 维度查询已有部署
    if scope_target_id:
        result = await db.execute(
            select(AgentDeployment).where(
                AgentDeployment.instance_id == agent_id,
                AgentDeployment.scope_type == scope_type,
                AgentDeployment.scope_target_id == scope_target_id,
            )
        )
    else:
        result = await db.execute(
            select(AgentDeployment).where(
                AgentDeployment.instance_id == agent_id,
                AgentDeployment.scope_type == scope_type,
                AgentDeployment.scope_target_id.is_(None),
            )
        )
    dep = result.scalar_one_or_none()

    if dep and dep.status == DeploymentStatus.RUNNING:
        # 已运行，直接返回
        return AgentStatusResponse(
            agent_id=agent_id,
            status=DeploymentStatus.RUNNING,
            engine_url=dep.engine_url,
        )
    if dep and dep.status == DeploymentStatus.DEPLOYING:
        # 防重入：部署进行中，拒绝重复触发
        raise HTTPException(status_code=409, detail="Agent is already deploying")

    # 捕获覆盖前的状态：_run_deploy 据此决定 ARCHIVED 恢复 / SUSPENDED 恢复 / 全新创建
    prev_status = dep.status if dep else None

    # V3: 按 instance_id 读取实例配置（端点入参 agent_id 语义 = instance_id）
    inst_cfg = await _load_instance_config(db, agent_id)
    if not inst_cfg:
        raise HTTPException(status_code=404, detail="Agent instance not found")

    # 同步置 DEPLOYING（已存在则更新；不存在则建行），防重入守卫立即生效
    if dep:
        dep.status = DeploymentStatus.DEPLOYING
        dep.error_message = None
    else:
        dep = AgentDeployment(
            instance_id=agent_id,
            group_id=inst_cfg.get("group_id"),
            resource_pool_id=inst_cfg.get("resource_pool_id"),
            scope_type=scope_type,
            scope_target_id=scope_target_id,
            status=DeploymentStatus.DEPLOYING,
        )
        db.add(dep)
    await db.commit()

    # 后台任务跑部署主体（自建 DB session），不阻塞请求
    _schedule_deploy(agent_id, scope_type, scope_target_id, prev_status)

    return AgentStatusResponse(
        agent_id=agent_id,
        status=DeploymentStatus.DEPLOYING,
        engine_url=dep.engine_url,
    )


def _schedule_deploy(
    agent_id: str,
    scope_type: str,
    scope_target_id: str | None,
    prev_status: DeploymentStatus | None,
) -> None:
    """启动后台部署任务（独立函数，便于测试 patch 为 no-op）。"""
    asyncio.create_task(_run_deploy(agent_id, scope_type, scope_target_id, prev_status))


async def _run_deploy(
    agent_id: str,
    scope_type: str,
    scope_target_id: str | None,
    prev_status: DeploymentStatus | None,
) -> None:
    """后台部署主体（自建 DB session）。

    prev_status 为 endpoint 覆盖 DEPLOYING 之前的状态，据此选择恢复策略：
      ARCHIVED → 从归档恢复；SUSPENDED → scale 恢复；其余 → 全新创建。
    成功 → RUNNING；失败 → FAILED + error_message。单轮异常自吞不拖垮进程。
    """
    agen = get_manager_db()
    db = await agen.__anext__()
    try:
        # 序列化：与 suspend/destroy 互斥，避免恢复与销毁竞态删刚拉起的 Pod/PVC
        await _acquire_agent_lock(db, agent_id)
        await _deploy_body(db, agent_id, scope_type, scope_target_id, prev_status)
    except Exception as e:
        logger.exception(f"_run_deploy unexpected error for agent {agent_id}")
        try:
            result = await db.execute(
                select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
            )
            dep = result.scalar_one_or_none()
            if dep and dep.status == DeploymentStatus.DEPLOYING:
                dep.status = DeploymentStatus.FAILED
                dep.error_message = str(e) or "部署异常"
                await db.commit()
        except Exception:
            logger.error(f"_run_deploy fallback FAILED-mark failed for {agent_id}", exc_info=True)
    finally:
        await agen.aclose()


async def _deploy_body(
    db: AsyncSession,
    agent_id: str,
    scope_type: str,
    scope_target_id: str | None,
    prev_status: DeploymentStatus | None,
) -> None:
    """_run_deploy 的部署主体：创建/恢复 + 等待就绪，终态写回 dep。"""
    # 本 session 内重新加载 dep（最新状态）
    if scope_target_id:
        result = await db.execute(
            select(AgentDeployment).where(
                AgentDeployment.instance_id == agent_id,
                AgentDeployment.scope_type == scope_type,
                AgentDeployment.scope_target_id == scope_target_id,
            )
        )
    else:
        result = await db.execute(
            select(AgentDeployment).where(
                AgentDeployment.instance_id == agent_id,
                AgentDeployment.scope_type == scope_type,
                AgentDeployment.scope_target_id.is_(None),
            )
        )
    dep = result.scalar_one_or_none()
    if not dep or dep.status != DeploymentStatus.DEPLOYING:
        # 已被外部改变（运行中/已销毁/已失败等），放弃
        logger.info(
            "_run_deploy %s: dep state changed to %s, skip",
            agent_id[:8],
            dep.status if dep else "none",
        )
        return

    inst_cfg = await _load_instance_config(db, agent_id)
    if not inst_cfg:
        dep.status = DeploymentStatus.FAILED
        dep.error_message = "Agent instance config not found"
        await db.commit()
        return

    model_config = inst_cfg["model_config"]
    engine_config = _build_engine_envs(model_config)

    # V3: 资源规格从 resource_pools 读取；镜像走 ENGINE_RUNTIMES[engine_type]
    engine_instance_id = inst_cfg["resource_pool_id"]
    resource_spec = None
    if engine_instance_id:
        resource_spec = await _load_resource_spec(db, engine_instance_id)
    engine_type = inst_cfg["engine_type"]
    engine_instance_image = get_engine_runtime(engine_type)["image"]
    # UserGroup 隔离：group_code 用于 MinIO 组前缀 + Pod/PVC/Service label
    _group_code = inst_cfg.get("group_code")

    # ── Dify 外部实例模式：跳过 K8s Pod 部署，直接使用 dify_config.base_url ──
    # base_url 存在 = 外部 Dify 实例（用户自管 SaaS / 自托管 Dify）；
    # base_url 缺省 = Pod 模式，走下方既有 K8s 部署流程。
    # 优先读 inst.dify_config（per-instance 新列）；空则 fallback 到 version.model_config.dify
    # （历史快照数据，cleanup PR 删 fallback）。
    if engine_type == "DIFY":
        dify_cfg = inst_cfg.get("dify_config") or {}
        if not dify_cfg:
            mc = inst_cfg.get("model_config") or {}
            dify_cfg = mc.get("dify") or {}
        external_base_url = (dify_cfg.get("base_url") or "").strip()
        if external_base_url:
            dep.status = DeploymentStatus.RUNNING
            dep.engine_url = external_base_url.rstrip("/")
            dep.last_active_at = datetime.now(UTC)
            dep.error_message = None
            dep.pod_name = None  # 外部实例无 Pod
            if not dep.deployed_at:
                dep.deployed_at = datetime.now(UTC)
            await db.commit()
            logger.info(
                "Dify external instance mode for %s — skipping Pod deployment, "
                "engine_url=%s",
                agent_id[:8],
                dep.engine_url,
            )
            return

    # V2 多 Profile 环境变量（V1 entrypoint 会忽略不识别的变量，安全）
    engine_config["SCOPE_TYPE"] = scope_type
    engine_config["SCOPE_TARGET_ID"] = scope_target_id or ""
    engine_config["PROFILES_JSON"] = "[]"  # 空列表，Profile 动态创建
    # Pod 启动后主动注册 profile 列表给 Controller（reconcile stale DB 记录）
    engine_config["CONTROLLER_URL"] = settings.controller_base_url
    engine_config["AGENT_ID"] = agent_id

    _needs_backup_restore = False  # SUSPENDED→RESUME 后需从 OSS 恢复数据（emptyDir 为空）
    _archive_tar_data = None  # ARCHIVED→RUNNING 时预下载的归档数据
    _restore_error: str | None = None  # 恢复阶段错误（非空则置 FAILED，不留空数据 Pod）
    _deploy_t0 = time.time()  # 启动计时
    pod_name: str | None = None

    try:
        # 创建或恢复（按覆盖前状态决定策略）
        _preferred_node = dep.node_name

        if prev_status == DeploymentStatus.ARCHIVED:
            # 从归档恢复：创建新 Pod，优先调度到上次运行节点
            pod_name = await k8s_manager.create_agent_engine(
                agent_id,
                engine_config,
                scope_type=scope_type,
                scope_target_id=scope_target_id,
                resource_spec=resource_spec,
                preferred_node=_preferred_node,
                engine_instance_image=engine_instance_image,
                engine_type=engine_type,
                group_code=_group_code,
            )
            # 从归档先下载 tar 数据，恢复延迟到 Pod 就绪后 exec
            if dep.archive_path:
                try:
                    _archive_tar_data = archiver.get_archive(
                        dep.archive_path.replace(f"s3://{settings.minio_bucket}/", "")
                    )
                except Exception as e:
                    _restore_error = f"archive download failed: {e}"
                    logger.error("Archive download failed for %s: %s", agent_id[:8], e)
                else:
                    if not _archive_tar_data:
                        _restore_error = "archive download returned empty data"
                    else:
                        _needs_backup_restore = True  # 非空 bytes 代表需要恢复

        elif prev_status == DeploymentStatus.SUSPENDED:
            # 从休眠恢复：scale=1
            resumed = await k8s_manager.resume(agent_id, scope_type, scope_target_id)
            if not resumed:
                # Deployment 不存在（如被误删），走全新创建，优先回原节点
                pod_name = await k8s_manager.create_agent_engine(
                    agent_id,
                    engine_config,
                    scope_type=scope_type,
                    scope_target_id=scope_target_id,
                    resource_spec=resource_spec,
                    preferred_node=_preferred_node,
                    engine_instance_image=engine_instance_image,
                    engine_type=engine_type,
                    group_code=_group_code,
                )
                # SUSPENDED 状态下 MinIO 中可能有 backup 数据，需要恢复
                # 即使 Deployment 被外部误删，backup 仍应尝试恢复
                # archiver.get_backup() 若无备份返回 None → 安全跳过
                _needs_backup_restore = True
            else:
                # Deployment 存在，scale from 0 to 1
                # PVC 存在时数据已持久化，无需 MinIO 恢复（V2 强制 PVC）
                _pvc = _pvc_name(agent_id, scope_type, scope_target_id)
                if k8s_manager.pvc_exists(_pvc):
                    _needs_backup_restore = False
                    logger.info(
                        "RESUME %s: PVC %s exists, skipping MinIO restore",
                        agent_id[:8],
                        _pvc,
                    )
                else:
                    _needs_backup_restore = True
        else:
            # 全新创建（prev_status ∈ {None, PENDING, FAILED}）
            pod_name = await k8s_manager.create_agent_engine(
                agent_id,
                engine_config,
                scope_type=scope_type,
                scope_target_id=scope_target_id,
                resource_spec=resource_spec,
                engine_instance_image=engine_instance_image,
                engine_type=engine_type,
                group_code=_group_code,
            )

        _t_k8s_create = time.time() - _deploy_t0

        # 等待 Pod Ready
        ready = await k8s_manager.wait_pod_ready(
            agent_id, scope_type=scope_type, scope_target_id=scope_target_id
        )
        _t_pod_ready = time.time() - _deploy_t0
        if not ready:
            raise RuntimeError("Engine pod startup timeout")

        # Pod 就绪后恢复数据（emptyDir 为空，无 PVC）
        if _needs_backup_restore:
            try:
                # ARCHIVED→RUNNING：优先用预先下载的归档数据
                if _archive_tar_data:
                    tar_data = _archive_tar_data
                else:
                    # SUSPENDED→RUNNING：从最近 daily 下载（回退 legacy latest）
                    tar_data = archiver.get_latest_daily(agent_id, group_code=_group_code)
                if not tar_data:
                    raise RuntimeError("no restorable backup (empty daily/archive)")
                await k8s_manager.exec_untar_data(
                    agent_id, tar_data, scope_type, scope_target_id
                )
                # 恢复后清理 stale gateway.lock，避免 PID 不匹配导致引擎启动失败
                try:
                    await k8s_manager.exec_hermes_command(
                        agent_id,
                        ["rm -f /root/.hermes/gateway.lock"],
                        scope_type,
                        scope_target_id,
                    )
                    logger.info("Cleaned up stale gateway.lock after restore")
                except Exception:
                    pass  # 非关键
            except Exception as e:
                _restore_error = _restore_error or f"data restore failed: {e}"

        # 恢复失败：不留空数据 Pod 服务用户，置 FAILED 让运维介入（数据非常重要，
        # 空数据静默 RUNNING = 用户看到空会话历史，是最危险的静默丢失）。
        if _restore_error:
            try:
                await k8s_manager.scale_to_zero(agent_id, scope_type, scope_target_id)
            except Exception:
                pass  # 清理失败不掩盖原错误
            dep.status = DeploymentStatus.FAILED
            dep.error_message = _restore_error
            await db.commit()
            logger.error("Restore failed for %s, marked FAILED: %s", agent_id[:8], _restore_error)
            return

        # 记录 Pod 所在节点（用于下次重启时的节点亲和性）
        pod_status = await k8s_manager.get_pod_status(agent_id, scope_type, scope_target_id)
        _node_name = pod_status.get("node_name")

        engine_url = await k8s_manager.get_service_url(
            agent_id, scope_type, scope_target_id, engine_type=engine_type
        )

        # 等待引擎 HTTP 就绪（Hermes 插件初始化）
        engine_online = await k8s_manager.wait_engine_ready(
            agent_id, scope_type=scope_type, scope_target_id=scope_target_id
        )
        _t_engine_ready = time.time() - _deploy_t0

        # 启动耗时日志（无论成功/超时都记录）
        logger.info(
            "Deploy timings for %s: k8s_create=%.1fs pod_ready=%.1fs "
            "engine_init=%.1fs total=%.1fs node=%s ready=%s",
            agent_id[:8],
            _t_k8s_create,
            _t_pod_ready - _t_k8s_create,
            _t_engine_ready - _t_pod_ready,
            _t_engine_ready,
            _node_name or "unknown",
            engine_online,
        )

        if not engine_online:
            raise RuntimeError("Engine HTTP not ready (startup timeout)")

        # 更新数据库：dep 已存在（endpoint 创建/更新过），直接更新终态
        dep.status = DeploymentStatus.RUNNING
        dep.engine_url = engine_url
        dep.last_active_at = datetime.now(UTC)
        dep.node_name = _node_name
        dep.error_message = None
        if pod_name:
            dep.pod_name = pod_name
        if not dep.deployed_at:
            dep.deployed_at = datetime.now(UTC)
        await db.commit()

        logger.info(f"Deploy succeeded for agent {agent_id[:8]}: {engine_url}")

        # 部署成功后重放人设(SOUL.md) + 已装技能到 Pod（best-effort，失败不影响 RUNNING）
        try:
            await _replay_persona_and_skills(agent_id, inst_cfg, db)
        except Exception:
            logger.warning(
                "replay persona/skills after deploy failed for %s", agent_id[:8], exc_info=True
            )

    except Exception as e:
        logger.exception(f"Deploy failed for agent {agent_id}")
        try:
            result = await db.execute(
                select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
            )
            dep = result.scalar_one_or_none()
            if dep and dep.status == DeploymentStatus.DEPLOYING:
                dep.status = DeploymentStatus.FAILED
                dep.error_message = str(e)
                await db.commit()
        except Exception:
            logger.error(f"Failed to mark dep FAILED for {agent_id}", exc_info=True)


# ── 模型列表（经 LiteLLM 网关，按 Agent 虚拟 Key 权限返回） ──


@router.post("/api/controller/agents/{agent_id}/suspend")
async def suspend_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """休眠引擎：存档数据 → scale=0"""
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    await _do_suspend(agent_id, db)
    return {"status": "suspended"}


@router.post("/api/controller/agents/{agent_id}/destroy")
async def destroy_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """销毁引擎：确认 SUSPEND 存档 → 清理 K8s 资源"""
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    await _do_destroy(agent_id, db)
    return {"status": "archived"}


@router.post("/api/controller/agents/{agent_id}/resume")
async def resume_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """恢复引擎：SUSPENDED → RUNNING（scale=1）。

    显式独立于 deploy：deploy 会在 SUSPENDED 时隐含 resume，此端点仅供
    「恢复」按钮直接调用，不触发全新创建/归档恢复等 deploy 复杂分支。
    Deployment 不存在时返回 409（需走 deploy 重建）。
    外部 Dify 实例：无 Pod 可 scale，直接置 RUNNING。
    """
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if _is_external_dify_deployment(dep):
        dep.status = DeploymentStatus.RUNNING
        dep.last_active_at = datetime.now(UTC)
        await db.commit()
        logger.info(
            "RESUME %s: external Dify instance, skip K8s scale ops",
            agent_id[:8],
        )
        return {"status": "running"}

    scope_type = dep.scope_type or "ALL"
    scope_target_id = dep.scope_target_id
    resumed = await k8s_manager.resume(agent_id, scope_type, scope_target_id)
    if not resumed:
        raise HTTPException(
            status_code=409,
            detail="Deployment not found in K8s; deploy required to recreate",
        )

    # browser Pod 同步恢复（从 SUSPEND scale=0 恢复到 1），best-effort 不阻断引擎恢复
    await resume_browser_pods_for_deployment(dep, agent_id)

    dep.status = DeploymentStatus.RUNNING
    dep.last_active_at = datetime.now(UTC)
    await db.commit()

    # resume 起新 Pod（scale 0→1）→ entrypoint 已 curl 全链 reconcile；此处再等引擎就绪后
    # 补一次，兜底 entrypoint curl 失败（manager 启动期不可达）+ SUSPEND 期间装的技能。
    # best-effort：reconcile 失败不阻断 resume（Pod 已 RUNNING）。
    try:
        await k8s_manager.wait_engine_ready(
            agent_id, scope_type=scope_type, scope_target_id=scope_target_id
        )
        await reconcile_skills(agent_id, db)
    except Exception:
        logger.warning("resume reconcile for %s failed", agent_id[:8], exc_info=True)
    return {"status": "running"}


@router.post("/api/controller/agents/{agent_id}/restart")
async def restart_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_manager_db),
):
    """重启引擎：滚动重启（配置/技能/人设变更生效），不改副本数。

    外部 Dify 实例：无 Pod 可重启，仅刷新 last_active_at（外部实例由用户自管）。
    """
    result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.instance_id == agent_id)
    )
    dep = result.scalar_one_or_none()
    if not dep:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if _is_external_dify_deployment(dep):
        dep.last_active_at = datetime.now(UTC)
        await db.commit()
        logger.info(
            "RESTART %s: external Dify instance, skip K8s rollout ops",
            agent_id[:8],
        )
        return {"status": "running"}

    scope_type = dep.scope_type or "ALL"
    scope_target_id = dep.scope_target_id
    try:
        await k8s_manager.rollout_restart(agent_id, scope_type, scope_target_id)
    except Exception as e:
        logger.error(f"Failed to rollout restart for agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Rollout restart failed: {e}")

    dep.last_active_at = datetime.now(UTC)
    await db.commit()
    return {"status": "restarting"}
