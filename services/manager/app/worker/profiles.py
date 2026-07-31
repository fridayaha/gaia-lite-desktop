"""Hermes Profile 生命周期 API — /api/controller/profiles/*

create/register/ensure/delete + _do_create_profile + seed helpers + pod 调度。
从 router.py 拆出，路径不变。配置渲染/heal/port_map/load 在 _common。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pkg.common.config import get_engine_runtime, settings
from pkg.common.database import get_db as get_manager_db
from pkg.common.models import AgentDeployment, DeploymentStatus

from ._common import (
    _set_browser_pod_in_port_map,
    build_engine_envs as _build_engine_envs,
    ensure_browser_pod_for_profile as _ensure_browser_pod_for_profile,
    heal_profile_runtime_config as _heal_profile_runtime_config,
    load_agent_configs as _load_agent_configs,
    load_instance_config as _load_instance_config,
    load_resource_spec as _load_resource_spec,
    port_map_all as _port_map_all,
    port_map_alloc as _port_map_alloc,
    port_map_exec as _port_map_exec,
    sync_deployment_port_map_mirror as _sync_deployment_port_map_mirror,
)
from .config_skills import _ensure_shared_skill_dir
from .k8s_manager import _engine_port, _scope_hash, k8s_manager

router = APIRouter()

logger = logging.getLogger(__name__)


# ── Pod 调度 helpers ───────────────────────────────────────


async def _get_instance_pods(db: AsyncSession, instance_id: str) -> list:
    """查询某实例下所有 Deployment（per-instance，不跨 resource pool；不过滤 status）。

    gateway 按 instance_id 路由到实例自己的 pod（_get_deployment WHERE instance_id，
    1:1 UNIQUE），故 profile 也必须建在实例自己的 deployment 上，否则 port_map 落不到
    本实例 deployment → Fix A（port_map 有端口就跳过 ensure）失效 → 每条消息走 ensure。
    不过滤 status：pod 重启期 DB status 可能短暂为 FAILED/PENDING（reconciliation 未同步），
    若过滤 RUNNING 会返回空 → 误进扩容分支（_ensure_pod_exists）→ 触发预存 bug 崩溃。
    实例 1:1 只有 1 个 deployment，直接复用即可，pod 真死时 k8s exec 会优雅失败。
    """
    result = await db.execute(
        select(AgentDeployment).where(
            AgentDeployment.instance_id == instance_id,
        )
    )
    return list(result.scalars().all())


async def _select_pod_by_load(db: AsyncSession, instance_id: str, max_profiles: int):
    """按负载选择最空闲的 Pod，全满时返回 None（per-instance，不跨 resource pool）"""
    deployments = await _get_instance_pods(db, instance_id)
    if not deployments:
        return None

    best = None
    best_count = max_profiles  # 起始阈值就是上限

    for dep in deployments:
        port_map = dep.internal_port_map or {}
        profiles = port_map.get("profiles", {})
        count = len(profiles)
        if count < best_count:
            best = dep
            best_count = count

    if best_count >= max_profiles:
        return None  # 最空闲的也满了
    return best


async def _ensure_pod_exists(
    agent_id: str,
    engine_instance_id: str,
    scope_type: str,
    scope_target_id: str | None,
    engine_config: dict,
    resource_spec: dict | None,
    engine_instance_image: str | None,
    db: AsyncSession,
    engine_type: str | None = None,
    group_code: str | None = None,
):
    """确保存在可用的 Pod 来承载新的 Profile。有可用则返回现有，否则新建。"""
    max_profiles = (resource_spec or {}).get("max_profiles_per_pod", 20)

    dep = await _select_pod_by_load(db, agent_id, max_profiles)
    if dep:
        return dep, False  # 现有 Pod，未新建

    # 所有 Pod 都满了 → 新建一个 Deployment
    from pkg.common.models import AgentDeployment as ADModel

    # 需要一个新的 scope_hash 来区分不同的 Pod
    # 多个 Pod 共享同一个 agent_id + scope，用递增序号区分
    existing = await _get_instance_pods(db, agent_id)
    seq = len(existing) + 1
    scoped_scope_target = f"{scope_target_id or 'default'}/pod{seq}"

    await k8s_manager.create_agent_engine(
        agent_id,
        engine_config,
        scope_type=scope_type,
        scope_target_id=scoped_scope_target,
        resource_spec=resource_spec,
        engine_instance_image=engine_instance_image,
        engine_type=engine_type,
        group_code=group_code,
    )

    # 创建 DB 记录
    short_id = agent_id.replace("-", "")[:8]
    shash = _scope_hash(scope_type, scoped_scope_target)
    pod_name = f"engine-hermes-{short_id}-{shash}"
    port = _engine_port(engine_type)
    dep = ADModel(
        instance_id=agent_id,
        engine_instance_id=engine_instance_id,
        scope_type=scope_type,
        scope_target_id=scoped_scope_target,
        status=DeploymentStatus.RUNNING,
        pod_name=pod_name,
        engine_url=f"http://{pod_name}.{k8s_manager.namespace}.svc.cluster.local:{port}",
        internal_port_map={"profiles": {}},
    )
    db.add(dep)
    await db.commit()
    await db.refresh(dep)
    return dep, True  # 新建


# ── Schemas ──────────────────────────────────────────────


class CreateProfileRequest(BaseModel):
    agent_id: str
    engine_instance_id: str
    user_id: str | None = None
    group_id: str | None = None
    profile_type: str  # INDEPENDENT (SHARED retired)
    profile_name: str


class RegisterProfilesRequest(BaseModel):
    """Pod 启动时上报自己的 profile 列表"""

    agent_id: str
    profiles: list[str]  # profile_name 列表（ls /opt/data/profiles/ 的结果）


# ── seed helpers ────────────────────────────────────────


async def _seed_persona(
    agent_id: str,
    profile_name: str,
    scope_type: str,
    scope_target_id: str | None,
    db: AsyncSession,
) -> None:
    """新建 profile 时写入 SOUL.md（人设）。Hermes 按会话读取，写文件即生效。

    用 exec_write_file(agent_id, scope) 解析 Pod（按 app=engine-hermes-{agent} label），
    而非 deployment.pod_name（那是 Deployment 名，非实际 Pod 名，exec 会找不到）。
    """
    configs = await _load_agent_configs(agent_id, db)
    if configs is None:
        return
    model_config, _, _ = configs
    soul = (model_config or {}).get("system_prompt") or ""
    if not soul.strip():
        return
    try:
        await k8s_manager.exec_write_file(
            agent_id,
            f"/opt/data/profiles/{profile_name}/SOUL.md",
            soul,
            scope_type=scope_type,
            scope_target_id=scope_target_id,
        )
    except Exception as e:
        logger.warning("seed SOUL.md for profile %s failed: %s", profile_name[:16], e)


def _schedule_profile_seeds(
    agent_id: str,
    profile_name: str,
    user_id: str | None,
    scope_type: str,
    scope_target_id: str | None,
    pod_name: str | None,
) -> None:
    """启动后台 seed 任务（独立函数，便于测试 patch 为 no-op）。

    seeds（SOUL.md / skills）不阻塞 ensure 响应：_do_create_profile 在
    commit 后调本函数即返回，seed 在后台自建 DB session 跑。失败仅 warn 不影响 profile
    可用性（engine 回退默认人设）。manager 重启会丢失未完成的任务（asyncio.create_task
    是内存态），但 seeds 幂等可由后续操作重跑——后续可加 seeds_state 列 + 对账扫描。
    """
    asyncio.create_task(
        _run_profile_seeds(agent_id, profile_name, user_id, scope_type, scope_target_id, pod_name)
    )


async def _run_profile_seeds(
    agent_id: str,
    profile_name: str,
    user_id: str | None,
    scope_type: str,
    scope_target_id: str | None,
    pod_name: str | None,
) -> None:
    """后台 seed 主体（自建 DB session，复用 _run_deploy 模式）。

    顺序：SOUL.md（人设）。用户基本信息不再 seed 到 USER.md——智能体经
    current-user-info 预置 skill 实时 pull /user-context 端点获取，无需写文件。
    外层 try/except 兜底任何异常，避免「Task exception was never retrieved」。
    skill-dir 准备（.definition_id + 共享目录）留在同步路径——launch 依赖 _defid 加 skill 组。
    """
    agen = get_manager_db()
    db = await agen.__anext__()
    try:
        await _seed_persona(agent_id, profile_name, scope_type, scope_target_id, db)
    except Exception as e:
        logger.warning("background seeds for profile %s failed: %s", profile_name[:16], e)
    finally:
        await agen.aclose()


# ── 端点 ────────────────────────────────────────────────


@router.post("/api/controller/profiles")
async def create_profile(req: CreateProfileRequest, db: AsyncSession = Depends(get_manager_db)):
    """创建 Hermes Profile 并分配到某个 Engine Pod"""
    return await _do_create_profile(req, db)


@router.post("/api/controller/profiles/register")
async def register_profiles(
    req: RegisterProfilesRequest, db: AsyncSession = Depends(get_manager_db)
):
    """Pod 启动后主动上报 profile 列表，Controller 对比 DB 删除 stale 记录。

    被 engine entrypoint-v2.sh 调用。
    """
    from pkg.common.models import AgentProfile

    result = await db.execute(select(AgentProfile).where(AgentProfile.instance_id == req.agent_id))
    db_profiles = result.scalars().all()
    pod_set = set(req.profiles)
    deleted = []
    for prof in db_profiles:
        if prof.profile_name not in pod_set and prof.profile_name != "base":
            await db.execute(
                text("DELETE FROM agent_profiles WHERE id = :pid"),
                {"pid": str(prof.id)},
            )
            deleted.append(prof.profile_name)
    if deleted:
        await db.commit()
        logger.info(
            "Pod register: deleted %d stale profiles for agent %s: %s",
            len(deleted),
            req.agent_id[:8],
            deleted,
        )
    return {"deleted": deleted, "kept": len(db_profiles) - len(deleted)}


@router.post("/api/controller/profiles/ensure")
async def ensure_profile(req: CreateProfileRequest, db: AsyncSession = Depends(get_manager_db)):
    """确保 Profile 存在（幂等），Gateway 调此接口"""
    # 检查是否已存在
    from pkg.common.models import AgentProfile

    existing = await db.execute(
        select(AgentProfile).where(
            AgentProfile.profile_name == req.profile_name,
            AgentProfile.instance_id == req.agent_id,
        )
    )
    profile = existing.scalar_one_or_none()
    # 只有 internal_port 不为空才算已创建（profile_resolver._ensure_profile
    # 会在 Gateway 侧预先创建 DB 记录但 port 为空）
    if profile and profile.internal_port is not None:
        # 查 deployment 取 scope（调 port_map.py 需要）
        dep_r = await db.execute(
            select(AgentDeployment).where(AgentDeployment.id == profile.deployment_id)
        )
        deployment = dep_r.scalar_one_or_none()
        pn = profile.profile_name

        # port_map.json 为唯一真相：读 Pod 上真实端口，DB internal_port 可能 stale，以真相为准。
        port = profile.internal_port
        if deployment:
            try:
                truth_out = await _port_map_exec(
                    req.agent_id,
                    f"get {pn}",
                    deployment.scope_type,
                    deployment.scope_target_id,
                )
                if truth_out:
                    truth_port = int(truth_out)
                    if truth_port != profile.internal_port:
                        await db.execute(
                            text("UPDATE agent_profiles SET internal_port = :p WHERE id = :id"),
                            {"p": truth_port, "id": profile.id},
                        )
                        profile.internal_port = truth_port
                        logger.info(
                            "profile %s port synced to port_map truth: %d (was %s)",
                            pn[:16], truth_port, port,
                        )
                    port = truth_port
            except Exception as e:
                logger.warning("port_map get for %s failed: %s", pn[:16], e)

        # 确保 Gateway 进程在运行 + nginx 路由配置（Pod 重启后可能丢失）
        profile_exists_on_pod = False
        try:
            # 先检测 profile 目录是否在 Pod 上存在（destroy/redeploy 后 PVC 可能已清）
            check = await k8s_manager.exec_hermes_command(
                agent_id=req.agent_id,
                commands=[f"test -d /opt/data/profiles/{pn} && echo EXISTS || echo MISSING"],
            )
            profile_exists_on_pod = "EXISTS" in (check or "")
        except Exception:
            profile_exists_on_pod = False

        if not profile_exists_on_pod:
            # Pod 上 profile 目录不存在（destroy/redeploy 后 PVC 丢失）
            # → 删 stale DB 记录，走创建路径
            logger.warning("Profile %s dir missing on pod, recreating (stale DB record)", pn[:16])
            await db.execute(
                text("DELETE FROM agent_profiles WHERE instance_id = :aid AND profile_name = :pn"),
                {"aid": req.agent_id, "pn": pn},
            )
            await db.commit()
            return await _do_create_profile(req, db)

        # Heal 运行配置：PVC 持久化的 stale profile 可能残留 provider:auto + DEEPSEEK_API_KEY
        # 直连 DeepSeek，绕过 LiteLLM。每次 ensure 时强制对齐，使下次消息即自愈。
        # port 为 port_map.json 真相（已同步），写 .env 的 API_SERVER_PORT 与之一致。
        await _heal_profile_runtime_config(req.agent_id, pn, db, port=port)

        try:
            # 健康探测端口：gateway 已在跑就不重启（避免每条消息 --replace 导致请求
            # 落在重启间隙 502）。仅 gateway 挂了才 --replace。
            alive = False
            try:
                check = await k8s_manager.exec_hermes_command(
                    agent_id=req.agent_id,
                    commands=[
                        # 用 python 探测（V2 镜像无 curl）：urlopen 成功且 status==200 → exit 0 → echo 200；
                        # gateway 挂了/非 200 → 异常或 exit 1 → echo 000。2>/dev/null 抑制异常 traceback。
                        f"python3 -c \"import urllib.request,sys; "
                        f"sys.exit(0 if urllib.request.urlopen("
                        f"'http://127.0.0.1:{port}/health',timeout=2).status==200 else 1)\" "
                        f"2>/dev/null && echo 200 || echo 000"
                    ],
                )
                alive = "200" in (check or "")
            except Exception:
                alive = False

            if alive:
                logger.info("Gateway already running for profile %s (port %d), skip restart", pn[:16], port)
            else:
                # gateway 挂了 → --replace 重启（旧实例在跑也能替换，避免端口漂移）
                # 传 definition_id → profile_isolation 加 skill 补充组（external_dirs 读权限）
                _ensure_defid = None
                try:
                    _ec = await _load_instance_config(db, req.agent_id)
                    if _ec:
                        _ensure_defid = _ec.get("definition_id")
                except Exception:
                    pass
                _restart_cmd = f"python3 /opt/scripts/profile_isolation.py launch {pn} /opt/data/profiles/{pn} {port}"
                if _ensure_defid:
                    _restart_cmd += f" {_ensure_defid}"
                await k8s_manager.exec_hermes_command(
                    agent_id=req.agent_id,
                    commands=[
                        # 重启同样走 launch 脚本（幂等：目录属主 truth → 恢复 uid → 降权启动）
                        _restart_cmd,
                    ],
                )
                logger.info("Gateway restarted for profile %s (port %d)", pn[:16], port)
            # 从 port_map.json 真相刷新 nginx + 同步 DB 镜像（幂等，每次对齐）
            if deployment:
                _pm = await _port_map_all(
                    req.agent_id, deployment.scope_type, deployment.scope_target_id
                )
                if _pm:
                    await _sync_deployment_port_map_mirror(db, deployment.id, _pm)
                    await db.commit()
                    await k8s_manager.update_nginx_config(
                        req.agent_id,
                        _pm,
                        scope_type=deployment.scope_type,
                        scope_target_id=deployment.scope_target_id,
                    )
        except Exception as e:
            logger.warning(
                "Gateway ensure for profile %s failed: %s (will retry on next message)", pn[:16], e
            )

        return {
            "profile_name": profile.profile_name,
            "deployment_id": str(profile.deployment_id),
            "port": profile.internal_port,
            "created": False,
        }
    return await _do_create_profile(req, db)


@router.get("/api/controller/profiles/{profile_name}/user-context")
async def get_profile_user_context(
    profile_name: str,
    db: AsyncSession = Depends(get_manager_db),
    x_internal_token: str | None = Header(None, alias="X-Internal-Token"),
):
    """按 profile_name 反查当前用户基本信息（脱敏），供智能体只读 pull。

    智能体经 current-user-info 预置 skill 调本端点获取实时用户信息——不注入
    system prompt、不写引擎文件，避免会话冻结取旧值与引擎耦合。profile_name 始终
    含 user_id（gateway profile_resolver._build_profile_name），故按 profile_name
    反查 agent_profiles → user_id → users 即当前用户。

    鉴权：配置了 UA_INTERNAL_TOKEN 时强制校验 X-Internal-Token（生产）；未配置时
    放行（本地 dev，靠 k8s 网络隔离）。返回 PII 已脱敏（无凭据/登录态）。
    """
    if settings.internal_token and x_internal_token != settings.internal_token:
        raise HTTPException(status_code=401, detail="Invalid internal token")

    from app.models import BusinessUserBinding, User
    from app.services.user_info_renderer import serialize_user_context
    from pkg.common.models import AgentProfile

    prof = (
        await db.execute(
            select(AgentProfile).where(AgentProfile.profile_name == profile_name)
        )
    ).scalar_one_or_none()
    if not prof or not prof.user_id:
        raise HTTPException(status_code=404, detail="profile not found")

    user = (
        await db.execute(
            select(User)
            .options(selectinload(User.roles), selectinload(User.groups))
            .where(User.id == prof.user_id)
        )
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    business_binding = (
        await db.execute(
            select(BusinessUserBinding).where(BusinessUserBinding.user_id == prof.user_id)
        )
    ).scalar_one_or_none()

    return serialize_user_context(user, business_binding)


async def _do_create_profile(req: CreateProfileRequest, db: AsyncSession):
    """创建 Profile: 调度 Pod → 分配端口 → 创建 Hermes Profile → 更新 nginx"""
    # 兜底：group_id 缺失时查 instance.group_id（agent_profiles.group_id NOT NULL）
    if not req.group_id:
        r = await db.execute(
            text("SELECT group_id FROM agent_instances WHERE id = :id"),
            {"id": req.agent_id},
        )
        gid = r.scalar()
        if gid:
            req.group_id = str(gid)
    # 1. 查询 ResourcePool 的 max_sessions_per_pod（兼容键 max_profiles_per_pod）
    pool_spec = await _load_resource_spec(db, str(req.engine_instance_id))
    max_profiles = (pool_spec or {}).get("max_profiles_per_pod", 20)

    # 2. Pod 调度：找到最空闲的 Pod，或新建（per-instance，确保 profile 建在本实例 deployment）
    deployment = await _select_pod_by_load(db, req.agent_id, max_profiles)
    if not deployment:
        # 所有 Pod 都满了 → 自动扩 Pod
        # 需要 instance 的 model_config 和 resource_pool 信息来创建
        inst_cfg = await _load_instance_config(db, req.agent_id)
        if not inst_cfg:
            raise HTTPException(status_code=404, detail="Agent instance not found")
        model_config = inst_cfg["model_config"]
        engine_config = _build_engine_envs(model_config)
        resource_spec = pool_spec
        engine_instance_image = get_engine_runtime(inst_cfg["engine_type"])["image"]
        # ensure 扩容新建 Pod 时补 CONTROLLER_URL（对齐 lifecycle._run_deploy），
        # 供 entrypoint 回调 + current-user-info skill 调 manager 端点。
        engine_config["CONTROLLER_URL"] = settings.controller_base_url

        deployment, _ = await _ensure_pod_exists(
            req.agent_id,
            str(req.engine_instance_id),
            "ALL",
            None,
            engine_config,
            resource_spec,
            engine_instance_image,
            db,
            engine_type=inst_cfg["engine_type"],
            group_code=inst_cfg.get("group_code"),
        )

    # 3. 分配端口：port_map.json 为 Pod 内唯一真相（flock 原子分配，幂等）。
    # 不再读 DB internal_port_map.next_port —— 消除 DB/.env 双源漂移导致串号。
    # 加 deployment 级 advisory lock：同 Pod 的端口分配串行（跨 Pod 不阻塞），
    # 与 port_map.py 的 flock 双保险，防并发 alloc 撞端口 / nginx 写互踩。
    # 事务级锁随本函数末尾 commit 释放。
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:did))"),
        {"did": str(deployment.id)},
    )
    port = await _port_map_alloc(
        req.agent_id, req.profile_name, deployment.scope_type, deployment.scope_target_id
    )

    # 3b. 立即插入 AgentProfile（带 port）并 commit —— 释放 advisory lock。
    # 锁只保护端口分配，绝不横跨下面 clone/heal/skill-dir/launch 等慢 k8s exec：
    # 否则任一 exec 挂死（websocket 不响应、_ws_exec_sync 的 timeout 不生效）会永久持锁，
    # 阻塞所有后续 ensure（实测卡 8min+，全 agent 不可用）。提前插行也让并发 ensure
    # 走 ensure_profile 自愈分支（internal_port 非空）而非重复 _do_create_profile。
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from pkg.common.models import AgentProfile

    _profile_stmt = (
        pg_insert(AgentProfile)
        .values(
            instance_id=req.agent_id,
            resource_pool_id=req.engine_instance_id,
            deployment_id=deployment.id,
            profile_name=req.profile_name,
            profile_type=req.profile_type,
            user_id=req.user_id,
            group_id=req.group_id,
            hermes_home=f"/opt/data/profiles/{req.profile_name}",
            internal_port=port,
            is_active=True,
        )
        .on_conflict_do_update(
            constraint="uq_user_profile_per_instance",
            set_=dict(internal_port=port, is_active=True),
        )
    )
    try:
        await db.execute(_profile_stmt)
        await db.commit()  # 释放 advisory lock —— 后续 heal/launch 不持锁
    except IntegrityError as e:
        # user_id FK 违约 = 用户已被删除（删用户时其 INDEPENDENT profile 已被
        # teardown_profile 清理；此处 ensure 试图重建 → user_id 不在 users 表）。
        # 返回 404 让 gateway 硬拒绝（不回退 base profile），避免已删用户继续用智能体。
        # 其它 FK（instance/deployment/group）理应存在（上面刚查过），违约属异常 → 原样抛。
        await db.rollback()
        if "user_id" in str(e):
            raise HTTPException(status_code=404, detail="user not found")
        raise

    # 4. 在 Pod 上创建 Hermes Profile（让 hermes 自己创建目录，避免 root 所有权）
    try:
        await k8s_manager.exec_hermes_command(
            agent_id=req.agent_id,
            scope_type=deployment.scope_type,
            scope_target_id=deployment.scope_target_id,
            commands=[
                f"hermes profile create {req.profile_name} --clone --clone-from base",
                # 目录权限由 profile_isolation.py launch 接管：chown {uid}:{uid} + chmod 0700
            ],
        )
    except Exception as e:
        logger.warning("Profile create exec failed: %s", e)

    # 5. 强制对齐 LiteLLM 运行配置 + API_SERVER_PORT
    # （PVC 持久化的克隆 profile 可能残留 provider:auto + DEEPSEEK_API_KEY 直连，
    # 绕过 LiteLLM 导致 spend_logs 不归因；此处 heal config.yaml + .env key + 端口）
    try:
        pn = req.profile_name
        # heal 必须在 launch 前：写 config.yaml（含 model）+ patch .env（端口/OPENAI_*），
        # 否则 gateway 启动读到空 model → LiteLLM 400。
        await _heal_profile_runtime_config(
            req.agent_id,
            pn,
            db,
            scope_type=deployment.scope_type,
            scope_target_id=deployment.scope_target_id,
            port=port,
        )
        # skill-dir 准备（必须在 launch 前）：写 .definition_id + 确保共享 skill 目录 + 补充组；
        # launch 带 _defid → profile_isolation 把 profile UID 加进 skill 补充组（读共享 skill）。
        # SOUL.md/USER.md 不阻塞 launch，移到后台 _schedule_profile_seeds（commit 后跑）。
        # external_dirs 模型：新 profile 不再种子化 skill 文件，而是
        # ① 写 .definition_id（entrypoint 重启时据此传给 profile_isolation 加 skill 组）
        # ② 确保该 Pod 上本 definition 的共享 skill 目录 + 补充组就绪（装了 skill 才有）
        # profile 的 config.yaml 已含 skills.external_dirs 指向共享目录（_heal 上一步写入），
        # gateway 启动即经 external_dirs 读到共享 skill，零文件复制。
        _defid = None
        try:
            inst_cfg = await _load_instance_config(db, req.agent_id)
            if inst_cfg and inst_cfg.get("definition_id"):
                _defid = inst_cfg["definition_id"]
                await k8s_manager.exec_write_file(
                    req.agent_id,
                    f"/opt/data/profiles/{pn}/.definition_id",
                    _defid,
                    scope_type=deployment.scope_type,
                    scope_target_id=deployment.scope_target_id,
                )
                if deployment.pod_name:
                    await _ensure_shared_skill_dir(deployment.pod_name, _defid)
                else:
                    # deployment.pod_name 在 DB 里常 stale/空（pod 重启名变，DB 不更新），
                    # 直接用会跳过 _ensure_shared_skill_dir → skill 目录+组没建 →
                    # gateway 读 external_dirs Permission denied。改为现取 pod_name。
                    _pod_status = await k8s_manager.get_pod_status(
                        req.agent_id, deployment.scope_type, deployment.scope_target_id
                    )
                    _pod_name = (_pod_status or {}).get("pod_name")
                    if _pod_name:
                        await _ensure_shared_skill_dir(_pod_name, _defid)
        except Exception as e:
            logger.warning("seed skill dir/.definition_id for %s failed: %s", pn[:16], e)
        # launch 传 definition_id → profile_isolation 把 profile UID 加进共享 skill 补充组
        _launch_cmd = f"python3 /opt/scripts/profile_isolation.py launch {pn} /opt/data/profiles/{pn} {port}"
        if _defid:
            _launch_cmd += f" {_defid}"
        await k8s_manager.exec_hermes_command(
            agent_id=req.agent_id,
            scope_type=deployment.scope_type,
            scope_target_id=deployment.scope_target_id,
            commands=[
                # profile_isolation.py：分配/恢复 uid + chown 0700 + 加固 secrets.enc
                # + 加 skill 补充组 + 以该 uid 降权启动 hermes gateway（preexec_fn setuid，非 root 运行）
                _launch_cmd
            ],
        )
        logger.info("Gateway start initiated for %s on port %d (async)", pn, port)
    except Exception as e:
        logger.warning("heal/launch for profile %s failed: %s", req.profile_name, e)

    # 6. nginx 配置 + DB 镜像：以 port_map.json (all) 为唯一真相。
    # launch 失败也不回滚 port_map（保留唯一端口 → 该 profile 流量 502，绝不串到别的 profile）。
    profiles_in_pod = await _port_map_all(
        req.agent_id, deployment.scope_type, deployment.scope_target_id
    )
    if not profiles_in_pod:
        # exec 失败兜底：至少保证刚 alloc 的 profile 在镜像里
        profiles_in_pod = {req.profile_name: port}
    # 合并而非整体覆盖：保留 browsers 等其他子键（create 路径会抹掉同 deployment 其他 profile
    # 的 browser Pod 记录，致 VNC 接管 403）。profiles 以 port_map.json 真相为准。
    _pm = dict(deployment.internal_port_map or {})
    _pm["profiles"] = dict(profiles_in_pod)
    deployment.internal_port_map = _pm

    await k8s_manager.update_nginx_config(
        req.agent_id,
        profiles_in_pod,
        scope_type=deployment.scope_type,
        scope_target_id=deployment.scope_target_id,
    )

    # 7. 持久化 deployment 的 port_map 镜像（AgentProfile 已在 3b 提前插入并 commit）
    db.add(deployment)  # 持久化 port_map 更新
    await db.commit()

    # 8. 浏览器沙箱：实例启用则为该 profile 拉起 browser Pod（best-effort，不阻塞 ensure）
    # pod_name 写 internal_port_map["browsers"][profile_name]，供 gateway 直查取。未启用返回 None。
    if await _ensure_browser_pod_for_profile(req.agent_id, req.profile_name, deployment, db):
        db.add(deployment)
        await db.commit()

    # seeds（SOUL.md / USER.md / skills）放后台，不阻塞 ensure 响应。
    # 失败仅 warn；幂等可重跑。manager 重启会丢任务（内存态），engine 回退默认人设不崩。
    _schedule_profile_seeds(
        req.agent_id,
        req.profile_name,
        req.user_id,
        deployment.scope_type,
        str(deployment.scope_target_id) if deployment.scope_target_id else None,
        getattr(deployment, "pod_name", None),
    )

    return {
        "profile_name": req.profile_name,
        "deployment_id": str(deployment.id),
        "pod_name": getattr(deployment, "pod_name", None),
        "port": port,
        "created": True,
    }


# 停 gateway 的内联脚本（Pod 内 python3 执行）。
# 不读 gateway.pid（hermes gateway run 内部 fork/setsid 守护化，pid 文件记的 PID 与最终
# 存活进程 off-by-one，不可靠），改按 profile 目录属主 uid + cmdline 'gateway run' 反查真 PID
# （/proc/<pid>/environ 与 /proc/<pid>/fd readlink 被 aegis 跨 uid 拦截，不可用；cmdline/status
# 跨 uid 可读）。再 setuid 降权到该 uid 写 planned_stop_marker（marker 文件 0600，须同 uid 持有
# gateway watcher 才读得到），让 gateway 内部 watcher 自退——不依赖 kill 信号，绕过阿里云
# aegis BPF LSM (modret_security_task_kill) 对容器内 SIGTERM/SIGKILL 的拦截。aegis 未拦时
# 兜底 SIGTERM 同样生效。best-effort，进程未退也不抛（防漂移优先）。
_STOP_GATEWAY_SCRIPT = r"""cat > /tmp/_ua_stop_gw.py <<'PYEOF'
import os, sys, time, signal
profile = sys.argv[1]
profile_dir = f"/opt/data/profiles/{profile}"

def _alive(pid):
    try:
        os.kill(pid, 0)  # signal 0 = 存在性探测，aegis 不拦
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False

def _uid_of(pid):
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("Uid:"):
                return line.split()[1]
    except OSError:
        pass
    return None

def gateway_pids_for_uid(target_uid):
    # cmdline 含 'gateway run' 且 uid == target_uid 的 PID（hermes fork 可能短暂多个）
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if "gateway run" not in cmd:
            continue
        if _uid_of(pid) == str(target_uid):
            out.append(int(pid))
    return out

try:
    target_uid = os.stat(profile_dir).st_uid
except OSError:
    sys.exit(0)  # profile 目录不存在 → 无 gateway 可停

# 以 root 找 gateway PID（cmdline/status 跨 uid 可读；environ 与 /proc/<pid>/fd readlink
# 被 aegis 跨 uid 拦截不可用，故按 uid+cmdline 反查，不读不可靠的 gateway.pid）
pids = gateway_pids_for_uid(target_uid)
if not pids:
    sys.exit(0)  # 无 gateway 在跑

# 降权到 profile uid：marker 文件 _write_json_file 以 0600 写，必须由 gateway 同 uid 持有，
# gateway watcher 才读得到（root 写的 0600 文件，uid-10000 gateway 读不到 → marker 失效）
if target_uid != 0:
    try:
        os.setgroups([])
        os.setgid(target_uid)
        os.setuid(target_uid)
    except OSError:
        pass  # 降权失败则 marker 可能不可读，兜底 kill 仍会尝试

# 降权后设 HERMES_HOME 并写 marker（文件属 target_uid 0600，gateway 同 uid 可读）
os.environ["HERMES_HOME"] = profile_dir
try:
    from gateway.status import write_planned_stop_marker
except Exception:
    write_planned_stop_marker = None

# marker 是单目标文件，round-robin 给每个存活 pid 写，对应 watcher 自退（绕过 aegis kill）
if write_planned_stop_marker:
    for _ in range(20):  # 最多 10s
        alive = [p for p in pids if _alive(p)]
        if not alive:
            break
        for p in alive:
            try:
                write_planned_stop_marker(p)
            except Exception:
                pass
        time.sleep(0.5)

# 兜底 SIGTERM（同 uid kill，aegis 拦则 EPERM 无害；marker 已致自退则 ProcessLookupError）
for p in pids:
    if _alive(p):
        try:
            os.kill(p, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
for _ in range(10):  # 再等 5s
    if not any(_alive(p) for p in pids):
        break
    time.sleep(0.5)
PYEOF
"""


async def teardown_profile(db: AsyncSession, profile) -> None:
    """停止 Profile gateway 并清理全链路资源：userdel/kill/rm 目录 → port_map.remove
    → nginx 重载 + DB 镜像同步 → 删 AgentProfile 行。

    best-effort：k8s exec 失败仅 warn 不抛——防 profile 漂移优先于 exec 成功，
    DB 行始终删除（残留目录由 entrypoint reconcile 兜底）。被 delete_profile 端点
    与删用户路径（user_service.delete_user）复用。
    """
    # 查找关联的 Deployment
    dep_result = await db.execute(
        select(AgentDeployment).where(AgentDeployment.id == profile.deployment_id)
    )
    deployment = dep_result.scalar_one_or_none()

    if deployment:
        # 一次 exec 原子完成：停 gateway → userdel → port_map.remove → rm 目录。
        # 合并成单次 exec 避免分两次时第二次失败致 port_map/进程残留（曾出现 gateway
        # 孤儿占端口 + port_map 残留 → 已删用户仍可经孤儿 gateway 请求）。
        # 停 gateway 走 planned_stop_marker（gateway 自退，不依赖 kill 信号，绕过 aegis
        # BPF LSM 对容器内 SIGTERM/SIGKILL 的拦截）——详见 _STOP_GATEWAY_SCRIPT 注释。
        try:
            await k8s_manager.exec_hermes_command(
                agent_id=str(profile.instance_id),
                scope_type=deployment.scope_type,
                scope_target_id=deployment.scope_target_id,
                commands=[
                    _STOP_GATEWAY_SCRIPT
                    + f"python3 /tmp/_ua_stop_gw.py {profile.profile_name}",
                    f"python3 /opt/scripts/profile_isolation.py cleanup {profile.profile_name}",
                    f"python3 /opt/scripts/port_map.py remove {profile.profile_name}",
                    f"rm -rf /opt/data/profiles/{profile.profile_name}",
                ],
            )
        except Exception as e:
            logger.warning("Failed to stop profile %s: %s", profile.profile_name, e)

        # reload nginx + 同步 DB 镜像（以 Pod port_map.json 为唯一真相，remove 已在上一步执行）
        profiles_in_pod = await _port_map_all(
            str(profile.instance_id), deployment.scope_type, deployment.scope_target_id
        )
        # 保留 browsers 段：profile teardown 只清引擎 profile 端口映射，不应丢掉
        # browser Pod 记录（下方要按它删本 profile 的 browser Pod，其余 profile 的保留）。
        # 此前整体覆盖 `{"profiles": ...}` 会把 browsers 键冲掉，致下方删除块永远 False、
        # browser Pod 永不清理（且 DB 记录丢失后 SUSPEND/DESTROY 也回收不了）。
        new_port_map: dict = {"profiles": dict(profiles_in_pod)}
        browsers_existing = (deployment.internal_port_map or {}).get("browsers")
        if browsers_existing:
            new_port_map["browsers"] = browsers_existing
        deployment.internal_port_map = new_port_map

        if profiles_in_pod:
            await k8s_manager.update_nginx_config(
                str(profile.instance_id),
                profiles_in_pod,
                scope_type=deployment.scope_type,
                scope_target_id=deployment.scope_target_id,
            )

        # 浏览器沙箱：删该 profile 的 browser Pod + 清 internal_port_map["browsers"][name]
        # （只删本 profile，不影响同 deployment 其他 profile 的 browser Pod）
        browsers = (deployment.internal_port_map or {}).get("browsers") or {}
        if profile.profile_name in browsers:
            try:
                await k8s_manager.delete_browser_pod(
                    str(profile.instance_id), profile.profile_name
                )
            except Exception as e:
                logger.warning(
                    "delete_browser_pod for %s failed: %s", profile.profile_name, e
                )
            _set_browser_pod_in_port_map(deployment, profile.profile_name, None)
            db.add(deployment)

    await db.delete(profile)
    await db.commit()


@router.delete("/api/controller/profiles/{profile_id}")
async def delete_profile(profile_id: str, db: AsyncSession = Depends(get_manager_db)):
    """停止 Profile gateway 并清理"""
    from pkg.common.models import AgentProfile

    result = await db.execute(select(AgentProfile).where(AgentProfile.id == profile_id))
    profile = result.scalar_one_or_none()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    await teardown_profile(db, profile)
    return {"status": "deleted"}
