"""业务用户绑定服务层（1:1）。

与 im_binding_service 对称，但 1:1（一个 UA user 一个业务身份），
故用 upsert 代替 create，按 user_id 查/删。
"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessUserBinding
from app.schemas import BusinessBindingCreate


async def get_binding(db: AsyncSession, user_id: UUID) -> BusinessUserBinding | None:
    result = await db.execute(
        select(BusinessUserBinding).where(BusinessUserBinding.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def upsert_binding(
    db: AsyncSession, user_id: UUID, data: BusinessBindingCreate
) -> BusinessUserBinding:
    """有则更新、无则创建（1:1）。"""
    binding = await get_binding(db, user_id)
    if binding:
        binding.business_username = data.business_username
        binding.business_phone = data.business_phone
        binding.business_email = data.business_email
    else:
        binding = BusinessUserBinding(
            user_id=user_id,
            business_username=data.business_username,
            business_phone=data.business_phone,
            business_email=data.business_email,
        )
        db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return binding


async def delete_binding(db: AsyncSession, user_id: UUID) -> bool:
    binding = await get_binding(db, user_id)
    if not binding:
        return False
    await db.delete(binding)
    await db.commit()
    return True
