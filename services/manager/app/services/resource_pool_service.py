"""V3 资源池 service — K8s 资源规格 + 回收策略 CRUD。与引擎类型解耦。"""

from uuid import UUID

from app.core.group_scope import visible_filter
from app.models import AgentInstance, ResourcePool
from app.schemas import ResourcePoolCreate, ResourcePoolUpdate
from app.services.audit_service import log_operation
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

_FIELDS = [
    "name", "description", "min_cpu", "max_cpu", "min_memory", "max_memory",
    "min_replicas", "max_replicas", "max_sessions_per_pod",
    "auto_recycle", "idle_suspend_minutes", "idle_destroy_hours",
]


async def list_resource_pools(
    db: AsyncSession,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    group_ids: list[UUID] | None = None,
) -> tuple[list[ResourcePool], int]:
    query = select(ResourcePool).options(
        joinedload(ResourcePool.creator),
        joinedload(ResourcePool.group),
    )
    cond = visible_filter(group_ids)
    if cond is not None:
        query = query.where(cond)
    if search:
        query = query.where(ResourcePool.name.ilike(f"%{search}%"))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(ResourcePool.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return list((await db.execute(query)).scalars().all()), total


async def get_resource_pool(db: AsyncSession, pool_id: UUID) -> ResourcePool | None:
    result = await db.execute(
        select(ResourcePool)
        .options(joinedload(ResourcePool.creator), joinedload(ResourcePool.group))
        .where(ResourcePool.id == pool_id)
    )
    return result.scalar_one_or_none()


async def create_resource_pool(
    db: AsyncSession, data: ResourcePoolCreate, user_id: UUID
) -> ResourcePool:
    pool = ResourcePool(
        created_by=user_id,
        group_id=data.group_id,
        **{f: getattr(data, f) for f in _FIELDS},
    )
    db.add(pool)
    await db.flush()
    await log_operation(
        db,
        actor_id=user_id,
        action="resource_pool.create",
        target_type="resource_pool",
        target_id=pool.id,
        group_id=data.group_id,
        detail={"name": pool.name, "min_cpu": pool.min_cpu, "max_cpu": pool.max_cpu},
    )
    await db.commit()
    await db.refresh(pool)
    # 重新载入（含 creator/group 关系），避免 _to_response 读 group.name 触发 async 懒加载
    return await get_resource_pool(db, pool.id)


async def update_resource_pool(
    db: AsyncSession, pool_id: UUID, data: ResourcePoolUpdate, *, actor_id: UUID
) -> ResourcePool | None:
    pool = await get_resource_pool(db, pool_id)
    if not pool:
        return None
    for f in _FIELDS:
        v = getattr(data, f)
        if v is not None:
            setattr(pool, f, v)
    await log_operation(
        db,
        actor_id=actor_id,
        action="resource_pool.update",
        target_type="resource_pool",
        target_id=pool.id,
        group_id=pool.group_id,
        detail={"name": pool.name, "fields": [k for k in data.model_fields_set if getattr(data, k) is not None]},
    )
    await db.commit()
    await db.refresh(pool)
    return pool


async def delete_resource_pool(
    db: AsyncSession, pool_id: UUID, *, actor_id: UUID
) -> bool:
    pool = await get_resource_pool(db, pool_id)
    if not pool:
        return False
    ref = (
        await db.execute(
            select(func.count())
            .select_from(
                select(AgentInstance).where(AgentInstance.resource_pool_id == pool_id).subquery()
            )
        )
    ).scalar() or 0
    if ref > 0:
        raise ValueError(f"存在 {ref} 个引用此资源池的实例，无法删除")
    name = pool.name
    await log_operation(
        db,
        actor_id=actor_id,
        action="resource_pool.delete",
        target_type="resource_pool",
        target_id=pool_id,
        group_id=pool.group_id,
        detail={"name": name},
    )
    await db.delete(pool)
    await db.commit()
    return True


async def clone_resource_pool(db: AsyncSession, pool_id: UUID, user_id: UUID) -> ResourcePool | None:
    src = await get_resource_pool(db, pool_id)
    if not src:
        return None
    clone = ResourcePool(
        name=f"{src.name} (副本)",
        created_by=user_id,
        group_id=src.group_id,
        **{f: getattr(src, f) for f in _FIELDS if f != "name"},
    )
    db.add(clone)
    await db.flush()
    await log_operation(
        db,
        actor_id=user_id,
        action="resource_pool.clone",
        target_type="resource_pool",
        target_id=clone.id,
        group_id=clone.group_id,
        detail={"name": clone.name, "source_id": str(pool_id)},
    )
    await db.commit()
    await db.refresh(clone)
    return await get_resource_pool(db, clone.id)
