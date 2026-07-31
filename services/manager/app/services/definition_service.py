"""V3 智能体定义 service — 元数据层（CRUD + 版本快照发布）。

与现有 agent_service.py 并存；API 切换后下线老 service。
技能管理属于定义层，技能相关 service 在 agent_skills（后续迁入）。
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from app.models import (
    AgentDefinition,
    AgentInstance,
    AgentVersion,
    DefinitionStatus,
    EngineType,
)
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionUpdate,
    PublishVersionRequest,
)
from app.services.audit_service import log_operation
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

logger = logging.getLogger(__name__)


# ── 版本号生成 ────────────────────────────────────


def _next_version_no(existing_count: int) -> str:
    """简单递增语义化版本：1.0.0, 1.0.1, 1.0.2 ..."""
    return f"1.0.{existing_count}"


# ── 列表 / 查询 ────────────────────────────────────


async def list_definitions(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    status: DefinitionStatus | None = None,
    engine_type: EngineType | None = None,
    group_ids: list[UUID] | None = None,
) -> tuple[list[AgentDefinition], int]:
    query = select(AgentDefinition).options(
        joinedload(AgentDefinition.creator),
        joinedload(AgentDefinition.group),
        selectinload(AgentDefinition.versions),
    )
    if group_ids is not None:
        query = query.where(AgentDefinition.group_id.in_(group_ids))
    if search:
        query = query.where(AgentDefinition.name.ilike(f"%{search}%"))
    if status:
        query = query.where(AgentDefinition.status == status)
    if engine_type:
        query = query.where(AgentDefinition.engine_type == engine_type)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(AgentDefinition.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(query)
    return list(result.scalars().all()), total


async def get_definition(db: AsyncSession, definition_id: UUID) -> AgentDefinition | None:
    result = await db.execute(
        select(AgentDefinition)
        .options(
            joinedload(AgentDefinition.creator),
            joinedload(AgentDefinition.group),
            selectinload(AgentDefinition.versions),
        )
        .where(AgentDefinition.id == definition_id)
    )
    return result.scalar_one_or_none()


async def list_versions(db: AsyncSession, definition_id: UUID) -> list[AgentVersion]:
    result = await db.execute(
        select(AgentVersion)
        .where(AgentVersion.definition_id == definition_id)
        .order_by(AgentVersion.created_at.desc())
    )
    return list(result.scalars().all())


async def instance_counts_for(
    db: AsyncSession, definition_ids: list[UUID]
) -> dict[UUID, int]:
    """批量统计每个定义下的实例数（列表页「N 智能体」用，避免 N+1）。"""
    if not definition_ids:
        return {}
    rows = await db.execute(
        select(AgentInstance.definition_id, func.count(AgentInstance.id))
        .where(AgentInstance.definition_id.in_(definition_ids))
        .group_by(AgentInstance.definition_id)
    )
    return {did: cnt for did, cnt in rows.all()}


# ── CRUD ────────────────────────────────────


async def create_definition(
    db: AsyncSession, data: AgentDefinitionCreate, user_id: UUID
) -> AgentDefinition:
    # 预填出厂预制 skill（plan/searxng-search/concept-diagrams/fastmcp/one-three-one-rule）
    # 到 skill_config。仅当调用方未显式指定 skill_config 时预填（开箱默认语义）；
    # 显式传入技能的调用方（含测试/迁移）尊重其意图，不加 preset。
    # zip 在 commit 后用真 definition_id 存入 MinIO（见下方 save_preset_zips）。
    from app.services.preset_skills import prefill_skill_config, save_preset_zips

    raw_sc = data.skill_config
    sc_empty = not raw_sc or not (
        raw_sc.get("skills") if isinstance(raw_sc, dict) else raw_sc
    )
    skill_config = prefill_skill_config(raw_sc) if sc_empty else (raw_sc or {})
    d = AgentDefinition(
        name=data.name,
        group_id=data.group_id,
        description=data.description,
        avatar_color=data.avatar_color,
        engine_type=data.engine_type,
        persona_config=data.persona_config or {},
        model_config=data.model_settings or {},
        skill_config=skill_config,
        memory_config=data.memory_config or {},
        created_by=user_id,
    )
    db.add(d)
    await db.flush()
    await log_operation(
        db,
        actor_id=user_id,
        action="agent_definition.create",
        target_type="agent_definition",
        target_id=d.id,
        group_id=d.group_id,
        detail={"name": d.name, "engine_type": d.engine_type.value if d.engine_type else None},
    )
    await db.commit()
    await db.refresh(d)  # commit 后对象过期，refresh 取 id
    # definition_id 现已可用：把 preset zip 存入 MinIO，供 _seed_skills 取回 fan-out 到新 profile。
    # best-effort：MinIO 不可用仅告警，不影响模版创建。
    if sc_empty:
        try:
            save_preset_zips(d.id)
        except Exception:
            logger.warning("save_preset_zips for %s failed", str(d.id)[:8], exc_info=True)
    # 重新载入（含 creator/group/versions 关系），避免 _to_response 读 group.name 触发 async 懒加载
    return await get_definition(db, d.id)


async def update_definition(
    db: AsyncSession, definition_id: UUID, data: AgentDefinitionUpdate, *, actor_id: UUID
) -> AgentDefinition | None:
    """更新定义草稿配置。已发布定义也可编辑（产生新草稿，下次发布生成新版本）。"""
    d = await get_definition(db, definition_id)
    if not d:
        return None
    changes: dict[str, object] = {}
    if data.name is not None:
        d.name = data.name
        changes["name"] = data.name
    if data.description is not None:
        d.description = data.description
    if data.avatar_color is not None:
        d.avatar_color = data.avatar_color
        changes["avatar_color"] = data.avatar_color
    if data.engine_type is not None:
        d.engine_type = data.engine_type
        changes["engine_type"] = data.engine_type.value if data.engine_type else None
    if data.persona_config is not None:
        d.persona_config = data.persona_config
        changes["persona_config"] = True
    if data.model_settings is not None:
        d.model_config = data.model_settings
        changes["model_config"] = True
    if data.skill_config is not None:
        d.skill_config = data.skill_config
        changes["skill_config"] = True
    if data.memory_config is not None:
        d.memory_config = data.memory_config
        changes["memory_config"] = True
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_definition.update",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=d.group_id,
        detail=changes,
    )
    await db.commit()
    # 重新载入（含 versions 关系），避免 _to_response 读 current_version_no 时触发 async 懒加载
    return await get_definition(db, definition_id)


async def delete_definition(db: AsyncSession, definition_id: UUID, *, actor_id: UUID) -> bool:
    """删除定义。存在引用此定义的实例时拒绝。"""
    d = await get_definition(db, definition_id)
    if not d:
        return False
    ref_count = (
        await db.execute(
            select(func.count())
            .select_from(
                select(AgentInstance)
                .where(AgentInstance.definition_id == definition_id)
                .subquery()
            )
        )
    ).scalar() or 0
    if ref_count > 0:
        raise ValueError(f"存在 {ref_count} 个引用此定义的实例，无法删除")
    await log_operation(
        db,
        actor_id=actor_id,
        action="agent_definition.delete",
        target_type="agent_definition",
        target_id=definition_id,
        group_id=d.group_id,
        detail={"name": d.name},
    )
    # 打破 current_version_id ↔ version.definition_id 循环 FK；version 行被 get_definition
    # selectinload 载入内存，ORM db.delete(d) 会对已加载集合置 definition_id=NULL（违反 NOT NULL）。
    # 改用 Core SQL 删定义，交由 DB ondelete=CASCADE 级联清理版本，绕过 ORM deassociate。
    await db.execute(
        update(AgentDefinition).where(AgentDefinition.id == definition_id).values(current_version_id=None)
    )
    await db.execute(delete(AgentDefinition).where(AgentDefinition.id == definition_id))
    await db.commit()
    return True


# ── 发布版本（生成不可变快照）────────────────────────────


async def publish_definition(
    db: AsyncSession,
    definition_id: UUID,
    req: PublishVersionRequest,
    user_id: UUID,
) -> tuple[AgentDefinition, AgentVersion]:
    """发布定义：将当前草稿配置生成为不可变 AgentVersion 快照，置为 current_version。

    定义状态 DRAFT→PUBLISHED。旧版本保留以支持实例回滚。
    """
    d = await get_definition(db, definition_id)
    if not d:
        raise ValueError("定义不存在")

    versions = await list_versions(db, definition_id)
    version_no = _next_version_no(len(versions))

    v = AgentVersion(
        definition_id=d.id,
        group_id=d.group_id,
        version_no=version_no,
        persona_config=d.persona_config or {},
        model_config=d.model_config or {},
        skill_config=d.skill_config or {},
        memory_config=d.memory_config or {},
        engine_type=d.engine_type,
        change_log=req.change_log,
        created_by=user_id,
    )
    db.add(v)
    await db.flush()  # 取 v.id

    d.current_version_id = v.id
    d.status = DefinitionStatus.PUBLISHED
    d.published_at = datetime.now(UTC)

    await log_operation(
        db,
        actor_id=user_id,
        action="agent_definition.publish",
        target_type="agent_definition",
        target_id=d.id,
        group_id=d.group_id,
        detail={"version_no": version_no, "version_id": str(v.id), "change_log": req.change_log},
    )
    await db.commit()
    await db.refresh(d)
    await db.refresh(v)
    return d, v
