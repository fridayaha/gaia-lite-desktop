"""IM 用户绑定服务层"""
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImUserBinding
from app.schemas import ImBindingCreate


async def list_bindings_by_user(
    db: AsyncSession, user_id: UUID
) -> tuple[list[ImUserBinding], int]:
    result = await db.execute(
        select(ImUserBinding)
        .where(ImUserBinding.user_id == user_id)
        .order_by(ImUserBinding.created_at.desc())
    )
    items = list(result.scalars().all())
    return items, len(items)


async def create_binding(
    db: AsyncSession, user_id: UUID, data: ImBindingCreate
) -> ImUserBinding:
    binding = ImUserBinding(
        user_id=user_id,
        channel_type=data.channel_type,
        im_user_id=data.im_user_id,
        im_user_name=data.im_user_name,
    )
    db.add(binding)
    try:
        await db.commit()
        await db.refresh(binding)
        return binding
    except Exception:
        await db.rollback()
        raise


async def delete_binding(db: AsyncSession, binding_id: UUID) -> bool:
    result = await db.execute(
        select(ImUserBinding).where(ImUserBinding.id == binding_id)
    )
    binding = result.scalar_one_or_none()
    if not binding:
        return False
    await db.delete(binding)
    await db.commit()
    return True
