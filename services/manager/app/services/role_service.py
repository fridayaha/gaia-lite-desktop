from uuid import UUID

from app.models import Permission, Role
from app.schemas import RoleCreate, RoleUpdate
from app.services.audit_service import log_operation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


async def list_roles(db: AsyncSession) -> list[Role]:
    result = await db.execute(
        select(Role)
        .options(selectinload(Role.permissions), selectinload(Role.users))
        .order_by(Role.created_at)
    )
    return list(result.scalars().all())


async def create_role(
    db: AsyncSession, data: RoleCreate, *, actor_id: UUID
) -> Role:
    role = Role(name=data.name, description=data.description)
    if data.permission_ids:
        perm_result = await db.execute(
            select(Permission).where(Permission.id.in_(data.permission_ids))
        )
        role.permissions = list(perm_result.scalars().all())
    db.add(role)
    await db.flush()
    await log_operation(
        db,
        actor_id=actor_id,
        action="role.create",
        target_type="role",
        target_id=role.id,
        detail={"name": role.name, "permission_ids": [str(p) for p in data.permission_ids]},
    )
    await db.commit()
    await db.refresh(role, ["permissions"])
    return role


async def get_role(db: AsyncSession, role_id: UUID) -> Role | None:
    result = await db.execute(
        select(Role)
        .options(selectinload(Role.permissions), selectinload(Role.users))
        .where(Role.id == role_id)
    )
    return result.scalar_one_or_none()


async def update_role(
    db: AsyncSession, role_id: UUID, data: RoleUpdate, *, actor_id: UUID
) -> Role | None:
    role = await get_role(db, role_id)
    if not role:
        return None
    if data.name is not None:
        role.name = data.name
    if data.description is not None:
        role.description = data.description
    if data.permission_ids is not None:
        perm_result = await db.execute(
            select(Permission).where(Permission.id.in_(data.permission_ids))
        )
        role.permissions = list(perm_result.scalars().all())
    await log_operation(
        db,
        actor_id=actor_id,
        action="role.update",
        target_type="role",
        target_id=role.id,
        detail={
            "name": role.name,
            "fields": [k for k in data.model_fields_set if getattr(data, k) is not None],
        },
    )
    await db.commit()
    await db.refresh(role, ["permissions"])
    return role


async def delete_role(
    db: AsyncSession, role_id: UUID, *, actor_id: UUID
) -> bool:
    role = await get_role(db, role_id)
    if not role:
        return False
    name = role.name
    await log_operation(
        db,
        actor_id=actor_id,
        action="role.delete",
        target_type="role",
        target_id=role_id,
        detail={"name": name},
    )
    await db.delete(role)
    await db.commit()
    return True


async def list_permissions(db: AsyncSession) -> list[Permission]:
    result = await db.execute(select(Permission).order_by(Permission.resource_type, Permission.code))
    return list(result.scalars().all())
