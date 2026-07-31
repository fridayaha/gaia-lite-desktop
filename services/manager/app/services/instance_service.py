"""V3 智能体实例 service — 定义×版本×资源池×访问范围的关联 + 业务生命周期。

与现有 agent_service.py 并存；API 切换后下线老 service。
- 上线/停用：实例业务可见性（DRAFT/PUBLISHED/OFFLINE）
- 运行时生命周期（部署/暂停/恢复/销毁/重启）作用于 AgentDeployment，由 controller 接入
- per-instance LiteLLM key：计费 Team = 实例所属 UserGroup（litellm_team_id）
"""

import base64
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.models import (
    AgentDeployment,
    AgentInstance,
    AgentStatus,
    AgentVersion,
    DeploymentStatus,
    EngineType,
    ResourcePool,
    UserGroup,
    user_group_members,
)
from app.schemas import AgentInstanceCreate, AgentInstanceUpdate
from app.services import litellm_client
from app.services.audit_service import log_operation
from app.services.definition_service import get_definition
from app.worker import client as controller_client
from app.worker.minio_archiver import archiver
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

logger = logging.getLogger(__name__)


# ── LiteLLM key 事务补偿 ────────────────────────────────
# _provision_litellm 在 DB commit 之前就调用 LiteLLM generate_key（外部副作用）。
# 若 commit 失败回滚，DB 里实例行/配置回滚了，但 LiteLLM 里已建的 key 仍在 —— 它的
# metadata.instance_id 指向不存在或已回退的实例，成为「无所属智能体」的孤儿 key。
# 补偿：把本次事务中新建的 key 登记到 session.info，commit 失败时删掉它们。
_COMPENSATION_INFO_KEY = "_ua_pending_litellm_key_compensation"


def _track_key_compensation(db: AsyncSession, key_token: str) -> None:
    """登记刚创建的 LiteLLM key，供 commit 失败时补偿删除。"""
    db.sync_session.info.setdefault(_COMPENSATION_INFO_KEY, []).append(key_token)


async def _commit_or_compensate(db: AsyncSession) -> None:
    """提交事务；若提交失败，回滚并删除本次事务中外部已建的 LiteLLM key（防孤儿）。

    成功路径等价于 db.commit()；失败路径额外补偿删除已登记的 key 后重新抛出原异常。
    """
    pending = db.sync_session.info.pop(_COMPENSATION_INFO_KEY, [])
    try:
        await db.commit()
    except BaseException:
        await db.rollback()
        for token in pending:
            try:
                await litellm_client.delete_key(token)
            except Exception:
                logger.warning(
                    "compensate: failed to delete orphan LiteLLM key %s after commit failure",
                    (token or "")[:14],
                )
        raise


# ── LiteLLM key 配置 ────────────────────────────────────


async def _provision_litellm(
    db: AsyncSession,
    instance: AgentInstance,
    version: AgentVersion | None,
    *,
    force: bool = False,
) -> None:
    """为实例生成 per-instance LiteLLM key，写回 instance.litellm_config。

    model_group 取自版本快照的 model_config.litellm.model_group。
    计费 Team = 实例所属 UserGroup 的 litellm_team_id（= str(group_id)）。
    重新生成时先删旧 key（key_alias 唯一）。

    幂等：force=False 时，若实例已有有效 key 且 model_group/team_id 未变则跳过
    重生成——避免 switch_version 在 model_group 不变时无谓删旧建新 key，导致
    运行中引擎仍持有已删除的旧 key 而 401（key 漂移）。force=True（reprovision）
    时强制重生成。
    """
    if version is None:
        return
    litellm_cfg = (version.model_config or {}).get("litellm") or {}
    model_group = litellm_cfg.get("model_group")
    if not model_group:
        return  # 版本未配置 LiteLLM 模型，跳过

    group = await db.get(UserGroup, instance.group_id)
    if not group:
        raise ValueError("实例所属用户组不存在")
    team_id = group.litellm_team_id or str(group.id)
    alias = group.name

    old = instance.litellm_config or {}
    old_key = old.get("key")

    if (
        not force
        and old_key
        and old.get("model_group") == model_group
        and old.get("team_id") == team_id
    ):
        return  # key 仍有效且维度未变，跳过重生成（防 key 漂移）

    try:
        await litellm_client.ensure_team(team_id, alias)
        if old_key:
            try:
                await litellm_client.delete_key(old_key)
            except litellm_client.LitellmError:
                pass  # 旧 key 可能已删
        resp = await litellm_client.generate_key(
            team_id=team_id,
            models=[model_group],
            metadata={"instance_id": str(instance.id), "group_id": str(group.id)},
            key_alias=f"instance:{str(instance.id)[:8]}",
        )
        key = resp.get("key")
        key_id = resp.get("token_id")
        if not key:
            raise ValueError("LiteLLM 未返回 key")
        # 登记新 key：若后续 commit 失败，_commit_or_compensate 会删掉它避免孤儿
        _track_key_compensation(db, key)
    except litellm_client.LitellmError as e:
        raise ValueError(f"模型配置失败: {e.message}") from e

    instance.litellm_config = {
        "team_id": team_id,
        "key_id": key_id,
        "key": key,
        "model_group": model_group,
    }


# ── 列表 / 查询 ────────────────────────────────────


async def list_instances(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    status: AgentStatus | None = None,
    definition_id: UUID | None = None,
    group_ids: list[UUID] | None = None,
) -> tuple[list[AgentInstance], int]:
    query = select(AgentInstance).options(
        joinedload(AgentInstance.creator),
        joinedload(AgentInstance.definition),
        joinedload(AgentInstance.version),
        joinedload(AgentInstance.resource_pool),
        joinedload(AgentInstance.group),
    )
    if group_ids is not None:
        query = query.where(AgentInstance.group_id.in_(group_ids))
    if search:
        query = query.where(AgentInstance.name.ilike(f"%{search}%"))
    if status:
        query = query.where(AgentInstance.status == status)
    if definition_id:
        query = query.where(AgentInstance.definition_id == definition_id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(AgentInstance.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def get_instance(db: AsyncSession, instance_id: UUID) -> AgentInstance | None:
    result = await db.execute(
        select(AgentInstance)
        .options(
            joinedload(AgentInstance.creator),
            joinedload(AgentInstance.definition),
            joinedload(AgentInstance.version),
            joinedload(AgentInstance.resource_pool),
            joinedload(AgentInstance.group),
        )
        .where(AgentInstance.id == instance_id)
    )
    return result.scalar_one_or_none()


async def list_accessible_instances(
    db: AsyncSession, user_id: UUID, is_admin: bool = False
) -> list[AgentInstance]:
    """终端用户可访问的已上线实例（PUBLISHED）。

    平台管理员(is_admin) 跨组可见全部；否则按用户所属组过滤，跨组天然不可见。
    """
    query = (
        select(AgentInstance)
        .options(joinedload(AgentInstance.definition))
        .where(AgentInstance.status == AgentStatus.PUBLISHED)
    )
    if not is_admin:
        user_group_ids = select(user_group_members.c.group_id).where(
            user_group_members.c.user_id == user_id
        )
        query = query.where(AgentInstance.group_id.in_(user_group_ids))
    query = query.order_by(AgentInstance.name.asc())
    result = await db.execute(query)
    return list(result.scalars().all())


# ── CRUD ────────────────────────────────────


async def create_instance(
    db: AsyncSession, data: AgentInstanceCreate, user_id: UUID
) -> AgentInstance:
    d = await get_definition(db, data.definition_id)
    if not d:
        raise ValueError("智能体定义不存在")
    if d.group_id != data.group_id:
        raise ValueError("实例必须与其定义属于同一用户组")

    version_id = data.version_id or d.current_version_id
    if not version_id:
        raise ValueError("定义尚未发布版本，无法创建实例")

    # Dify 外接模式（dify_config.base_url 存在）跳过资源池校验
    is_dify_external = d.engine_type == "DIFY" and bool((data.dify_config or {}).get("base_url"))
    if is_dify_external:
        inst_resource_pool_id = None
    else:
        if not data.resource_pool_id:
            raise ValueError("资源池必填")
        pool = await db.get(ResourcePool, data.resource_pool_id)
        if not pool:
            raise ValueError("资源池不存在")
        # 平台共享池(group_id NULL)任意组可用；组私有池仅归属组可用
        if pool.group_id is not None and pool.group_id != data.group_id:
            raise ValueError("资源池不属于该用户组")
        inst_resource_pool_id = data.resource_pool_id

    inst = AgentInstance(
        name=data.name,
        description=data.description,
        definition_id=d.id,
        version_id=version_id,
        resource_pool_id=inst_resource_pool_id,
        group_id=data.group_id,
        dify_config=data.dify_config or {},
        runtime_config=data.runtime_config or {},
        created_by=user_id,
    )

    db.add(inst)
    await db.flush()  # 取 instance.id 供 key metadata

    version = await db.get(AgentVersion, version_id)
    await _provision_litellm(db, inst, version)

    await log_operation(
        db,
        actor_id=user_id,
        action="agent_instance.create",
        target_type="agent_instance",
        target_id=inst.id,
        group_id=inst.group_id,
        detail={
            "name": inst.name,
            "definition_id": str(inst.definition_id),
            "version_id": str(inst.version_id),
        },
    )
    await _commit_or_compensate(db)
    await db.refresh(inst)
    await db.refresh(inst, ["group", "definition", "version", "resource_pool"])
    return inst


async def update_instance(
    db: AsyncSession, instance_id: UUID, data: AgentInstanceUpdate, *, actor_id: UUID
) -> AgentInstance | None:
    inst = await get_instance(db, instance_id)
    if not inst:
        return None

    changes: dict[str, Any] = {}
    if data.name is not None:
        inst.name = data.name
        changes["name"] = data.name
    if data.description is not None:
        inst.description = data.description
    if data.dify_config is not None:
        inst.dify_config = data.dify_config
        changes["dify_config"] = True
    if data.runtime_config is not None:
        inst.runtime_config = data.runtime_config
        changes["runtime_config"] = True
    if data.group_id is not None and data.group_id != inst.group_id:
        # 改用户组：校验新组下定义存在（实例必须与定义同组）
        d = await get_definition(db, inst.definition_id)
        if not d:
            raise ValueError("智能体模版不存在")
        if d.group_id != data.group_id:
            raise ValueError("当前绑定的智能体模版不属于该用户组，请先切换模版")
        # 校验资源池归属（Dify 外接模式 resource_pool_id 为 None，跳过）
        if inst.resource_pool_id:
            pool = await db.get(ResourcePool, inst.resource_pool_id)
            if pool and pool.group_id is not None and pool.group_id != data.group_id:
                raise ValueError("资源池不属于该用户组，请先切换资源池")
        changes["group_id"] = {"from": str(inst.group_id), "to": str(data.group_id)}
        inst.group_id = data.group_id
        # 改组后 team_id 变了，强制重新 provision per-instance LiteLLM key
        version = await db.get(AgentVersion, inst.version_id) if inst.version_id else None
        await _provision_litellm(db, inst, version, force=True)
    if data.resource_pool_id is not None:
        pool = await db.get(ResourcePool, data.resource_pool_id)
        if not pool:
            raise ValueError("资源池不存在")
        if pool.group_id is not None and pool.group_id != inst.group_id:
            raise ValueError("资源池不属于该实例的用户组")
        inst.resource_pool_id = data.resource_pool_id
        changes["resource_pool_id"] = str(data.resource_pool_id)

    version_changed = data.version_id is not None and data.version_id != inst.version_id
    if version_changed:
        inst.version_id = data.version_id
        changes["version_id"] = str(data.version_id)

    # 版本切换 → 重新生成 per-instance key（model_group 可能变化）
    if version_changed:
        version = await db.get(AgentVersion, inst.version_id) if inst.version_id else None
        await _provision_litellm(db, inst, version)

    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.update",
        target_type="agent_instance",
        target_id=inst.id,
        group_id=inst.group_id,
        detail=changes,
    )
    await _commit_or_compensate(db)
    await db.refresh(inst)
    return inst


async def delete_instance(db: AsyncSession, instance_id: UUID, *, actor_id: UUID) -> bool:
    """删除实例记录。运行态清理（destroy）由 controller 接入后在前置步骤完成。"""
    inst = await get_instance(db, instance_id)
    if not inst:
        return False
    inst_name = inst.name
    inst_group_id = inst.group_id
    # 吊销该实例的全部 per-instance LiteLLM key（按 metadata.instance_id 过滤，best-effort）。
    # 不只删 litellm_config 里的当前 key —— 历史孤儿（commit 失败/老 key 删除失败残留的）
    # 也一并清掉，避免残留指向已删实例的「无所属智能体」垃圾 key。
    try:
        await litellm_client.delete_keys_by_instance(str(instance_id))
    except litellm_client.LitellmError:
        pass
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.delete",
        target_type="agent_instance",
        target_id=instance_id,
        group_id=inst_group_id,
        detail={"name": inst_name},
    )
    await db.delete(inst)
    await db.commit()
    return True


# ── 业务生命周期（上线/停用/版本切换/克隆）─────────────────


async def publish_instance(
    db: AsyncSession, instance_id: UUID, *, actor_id: UUID
) -> AgentInstance | None:
    """上线：实例对终端可见。DRAFT/OFFLINE→PUBLISHED。

    Dify 外接模式（dify_config.base_url 存在）同步内联建 AgentDeployment：
    设 status=RUNNING + engine_url=base_url，让 gateway 立即可路由、详情页不显"等待中"。
    """
    inst = await get_instance(db, instance_id)
    if not inst:
        return None
    inst.status = AgentStatus.PUBLISHED
    inst.published_at = datetime.now(UTC)
    # 上线时确保存在 http 渠道（web 聊天必需，幂等）
    try:
        from app.services.channel_service import ensure_http_channel

        await ensure_http_channel(db, inst)
    except Exception as e:  # noqa: BLE001
        print(f"[channel] ensure http channel skipped: {e}")

    # Dify 外接模式：内联建/更新 AgentDeployment（无需走 worker deploy，因为没有 Pod）
    # engine_type 在 definition 上（实例快照不冗余存），get_instance 已 joinedload。
    if inst.definition and inst.definition.engine_type == EngineType.DIFY:
        dify_cfg = (inst.dify_config or {}) if inst.dify_config else {}
        external_base_url = (dify_cfg.get("base_url") or "").strip()
        if external_base_url:
            result = await db.execute(
                select(AgentDeployment).where(
                    AgentDeployment.instance_id == inst.id,
                    AgentDeployment.scope_type == "ALL",
                    AgentDeployment.scope_target_id.is_(None),
                )
            )
            dep = result.scalar_one_or_none()
            now = datetime.now(UTC)
            if dep is None:
                dep = AgentDeployment(
                    instance_id=inst.id,
                    group_id=inst.group_id,
                    resource_pool_id=None,
                    scope_type="ALL",
                    scope_target_id=None,
                    status=DeploymentStatus.RUNNING,
                    engine_url=external_base_url.rstrip("/"),
                    pod_name=None,
                    deployed_at=now,
                    last_active_at=now,
                )
                db.add(dep)
            else:
                dep.status = DeploymentStatus.RUNNING
                dep.engine_url = external_base_url.rstrip("/")
                dep.pod_name = None
                dep.error_message = None
                if not dep.deployed_at:
                    dep.deployed_at = now
                dep.last_active_at = now
            logger.info(
                "publish_instance: Dify external %s inline deployment engine_url=%s",
                str(inst.id)[:8],
                dep.engine_url,
            )
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.publish",
        target_type="agent_instance",
        target_id=inst.id,
        group_id=inst.group_id,
        detail={"name": inst.name, "version_id": str(inst.version_id) if inst.version_id else None},
    )
    await db.commit()
    await db.refresh(inst)
    return inst


async def offline_instance(
    db: AsyncSession, instance_id: UUID, *, actor_id: UUID
) -> AgentInstance | None:
    """停用：终端不可见（Pod 可保留）。PUBLISHED→OFFLINE。"""
    inst = await get_instance(db, instance_id)
    if not inst:
        return None
    inst.status = AgentStatus.OFFLINE
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.offline",
        target_type="agent_instance",
        target_id=inst.id,
        group_id=inst.group_id,
        detail={"name": inst.name},
    )
    await db.commit()
    await db.refresh(inst)
    return inst


async def switch_version(
    db: AsyncSession, instance_id: UUID, version_id: UUID, *, actor_id: UUID
) -> AgentInstance | None:
    """切换实例绑定的版本（升级/回滚）。重新生成 key（model_group 可能变化）。

    运行时重启由 controller 接入后触发（Batch 4）。
    """
    inst = await get_instance(db, instance_id)
    if not inst:
        return None
    version = await db.get(AgentVersion, version_id)
    if not version or version.definition_id != inst.definition_id:
        raise ValueError("版本不存在或不属于该实例的定义")

    old_version_id = inst.version_id
    inst.version_id = version_id
    await _provision_litellm(db, inst, version)
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.switch_version",
        target_type="agent_instance",
        target_id=inst.id,
        group_id=inst.group_id,
        detail={
            "from_version_id": str(old_version_id) if old_version_id else None,
            "to_version_id": str(version_id),
        },
    )
    await _commit_or_compensate(db)
    await db.refresh(inst)
    return inst


def _skill_names(version: AgentVersion | None) -> set[str]:
    """从版本快照 skill_config 提取技能名集合（install/uninstall 的 key）。"""
    if not version or not version.skill_config:
        return set()
    skills = (version.skill_config or {}).get("skills") or []
    return {str(s.get("name")) for s in skills if s.get("name")}


def _model_group(version: AgentVersion | None) -> str | None:
    """版本快照的 litellm.model_group（per-instance key 的计费/模型维度）。"""
    if not version or not version.model_config:
        return None
    return ((version.model_config or {}).get("litellm") or {}).get("model_group")


async def upgrade_version(
    db: AsyncSession, instance_id: UUID, version_id: UUID, *, actor_id: UUID
) -> dict:
    """切换实例绑定版本并增量热推送到运行中 Pod（不重建 Pod）。

    与 switch_version 的区别：switch_version 仅改 DB + 重生成 key，运行时配置不推送
    （故历史需重型 redeploy 才能生效）；upgrade_version 在 DB 切换后按 old/new 版本 diff
    调 controller 热更新原语把新版本元数据推到运行中 Pod：
      - 人设 → sync_persona 写 SOUL.md（热，不重启）
      - 技能 → 增量 install/uninstall + sync_skills_config 重写 config.yaml（热，不重启）
      - 模型 → sync_skills_config 重写 config.yaml 的 model 字段（按会话生效，热）
    仅当 litellm.model_group 变化（per-instance key 重生成，新 key 需 patch 进 Deployment env）
    时额外调 apply_agent_config 做一次轻量 rollout restart（~30-60s，非重型 deploy）。

    未部署/SUSPENDED/ARCHIVED 实例只更新 DB，待 resume/deploy 时自然用新版本。
    """
    inst = await get_instance(db, instance_id)
    if not inst:
        raise ValueError("实例不存在")

    new_version = await db.get(AgentVersion, version_id)
    if not new_version or new_version.definition_id != inst.definition_id:
        raise ValueError("版本不存在或不属于该实例的定义")

    old_version = await db.get(AgentVersion, inst.version_id) if inst.version_id else None

    # diff（在 switch_version 改 DB 前对比 old/new 快照）
    persona_changed = (
        old_version.persona_config if old_version else None
    ) != new_version.persona_config
    old_skills = _skill_names(old_version)
    new_skills = _skill_names(new_version)
    skills_added = new_skills - old_skills
    skills_removed = old_skills - new_skills
    skills_changed = bool(skills_added or skills_removed)
    model_group_changed = _model_group(old_version) != _model_group(new_version)

    # 1. DB 切版本 + 按需重生成 LiteLLM key（switch_version 内部 commit + 埋 switch_version log）
    await switch_version(db, instance_id, version_id, actor_id=actor_id)

    changed: list[str] = []
    if persona_changed:
        changed.append("persona")
    if skills_changed:
        changed.append("skills")
    if model_group_changed:
        changed.append("model")

    # 2. 查实例部署状态；未 RUNNING 则仅 DB 更新，不推送
    agent_id = str(instance_id)
    try:
        status_resp = await controller_client.get_agent_status(agent_id)
    except controller_client.ControllerError as e:
        raise ValueError(f"查询实例部署状态失败: {e.message}") from e

    if (status_resp or {}).get("status") != "RUNNING":
        await log_operation(
            db,
            actor_id=actor_id,
            action="agent_instance.upgrade",
            target_type="agent_instance",
            target_id=instance_id,
            group_id=inst.group_id,
            detail={
                "version_id": str(version_id),
                "changed": changed,
                "applied": False,
                "reason": "not_running",
            },
        )
        await db.commit()
        return {
            "applied": False,
            "reason": "not_running",
            "version_id": str(version_id),
            "changed": changed,
            "restarted": False,
            "message": "实例未在运行，已更新版本绑定，待部署/恢复时生效",
        }

    # 3. 增量热推送（best-effort：单点失败不阻断其余项）
    if persona_changed:
        try:
            await controller_client.sync_persona(agent_id)
        except controller_client.ControllerError:
            logger.warning("upgrade: sync_persona for %s failed", agent_id[:8], exc_info=True)

    # 扫描 Pod 已装技能，补「新旧版本都有但 Pod 缺失」的技能（fan-out 历史失败 /
    # 文件丢失 / 升级前 Pod 未装）。扫描不可用（未部署/ControllerError）则保守只装
    # added，不补缺失——避免误重装已有技能覆盖 secrets.enc。
    on_pod: set[str] | None = None
    try:
        engine_res = await controller_client.list_engine_skills(agent_id)
        if engine_res.get("engine_deployed"):
            on_pod = {
                str(it.get("name")) for it in (engine_res.get("items") or []) if it.get("name")
            }
    except controller_client.ControllerError:
        logger.warning(
            "upgrade: list_engine_skills for %s failed, skip missing-补",
            agent_id[:8],
            exc_info=True,
        )
    skills_missing = ((new_skills & old_skills) - on_pod) if on_pod is not None else set()
    to_install = skills_added | skills_missing
    if skills_missing and "skills" not in changed:
        changed.append("skills")

    if to_install or skills_removed:
        definition_id = str(inst.definition_id)
        for name in to_install:
            try:
                zip_bytes = archiver.get_skill_zip(definition_id, name)
                if not zip_bytes:
                    logger.warning("upgrade: skill %s zip missing in MinIO, skip install", name)
                    continue
                await controller_client.install_skill(
                    agent_id, name, base64.b64encode(zip_bytes).decode()
                )
            except controller_client.ControllerError:
                logger.warning(
                    "upgrade: install skill %s for %s failed", name, agent_id[:8], exc_info=True
                )
        for name in skills_removed:
            try:
                await controller_client.uninstall_skill(agent_id, name)
            except controller_client.ControllerError:
                logger.warning(
                    "upgrade: uninstall skill %s for %s failed", name, agent_id[:8], exc_info=True
                )

    # 重写 config.yaml：新模型名 + 新 skills.disabled（热，不重启）
    try:
        await controller_client.sync_skills_config(agent_id)
    except controller_client.ControllerError:
        logger.warning("upgrade: sync_skills_config for %s failed", agent_id[:8], exc_info=True)

    # 4. model_group 变化 → patch env + 轻量 rollout restart（新 key 生效）
    restarted = False
    if model_group_changed:
        try:
            await controller_client.apply_agent_config(agent_id)
            restarted = True
        except controller_client.ControllerError:
            logger.warning("upgrade: apply_agent_config for %s failed", agent_id[:8], exc_info=True)

    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.upgrade",
        target_type="agent_instance",
        target_id=instance_id,
        group_id=inst.group_id,
        detail={
            "version_id": str(version_id),
            "changed": changed,
            "applied": True,
            "restarted": restarted,
            "skills_added": list(skills_added),
            "skills_removed": list(skills_removed),
        },
    )
    await db.commit()
    return {
        "applied": True,
        "version_id": str(version_id),
        "changed": changed,
        "restarted": restarted,
        "message": (
            "模型组变更，引擎滚动重启中（约 30-60 秒）" if restarted else "已热更新生效，无需重启"
        ),
    }


async def reprovision_instance_key(
    db: AsyncSession, instance_id: UUID, *, actor_id: UUID
) -> AgentInstance | None:
    """重新生成 per-instance LiteLLM key（key 丢失/计费 Team 变更/老 key 统一时用）。

    不改版本与 access，仅按当前 version + access_scope 重新 provision。
    _provision_litellm 内部会先删旧 key 再建新 key，写回 instance.litellm_config。
    版本未配置 litellm.model_group 时为 no-op（_provision_litellm 内部 return）。
    """
    inst = await get_instance(db, instance_id)
    if not inst:
        return None
    version = await db.get(AgentVersion, inst.version_id) if inst.version_id else None
    await _provision_litellm(db, inst, version, force=True)
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_instance.reprovision_key",
        target_type="agent_instance",
        target_id=inst.id,
        group_id=inst.group_id,
        detail={"name": inst.name},
    )
    await _commit_or_compensate(db)
    await db.refresh(inst)
    return inst


async def clone_instance(
    db: AsyncSession, instance_id: UUID, user_id: UUID
) -> AgentInstance | None:
    """克隆实例：复用同定义+版本+资源池+访问范围，生成独立 key 的新实例。"""
    src = await get_instance(db, instance_id)
    if not src:
        return None

    clone = AgentInstance(
        name=f"{src.name} (副本)",
        description=src.description,
        definition_id=src.definition_id,
        version_id=src.version_id,
        resource_pool_id=src.resource_pool_id,
        group_id=src.group_id,
        dify_config=src.dify_config or {},  # 显式拷贝 Dify 应用绑定
        runtime_config=src.runtime_config or {},  # 显式拷贝运行时开关（如浏览器沙箱）
        created_by=user_id,
    )

    db.add(clone)
    await db.flush()

    version = await db.get(AgentVersion, src.version_id) if src.version_id else None
    await _provision_litellm(db, clone, version)

    await log_operation(
        db,
        actor_id=user_id,
        action="agent_instance.clone",
        target_type="agent_instance",
        target_id=clone.id,
        group_id=clone.group_id,
        detail={"source_id": str(instance_id), "name": clone.name},
    )
    await _commit_or_compensate(db)
    await db.refresh(clone)
    await db.refresh(clone, ["group", "definition", "version", "resource_pool"])
    return clone
