"""业务用户绑定 API（1:1）。

与 im_bindings 对称：同一 user_id 维度的 scope 校验（_check_user_scope 复用）。
差异：1:1 用 PUT upsert 代替 POST，GET 返单条或空。
业务绑定信息不再 fan-out 写 USER.md——智能体经 current-user-info 预置 skill
实时 pull /user-context 端点获取（含业务绑定字段）。
"""

from uuid import UUID

from app.api.im_bindings import _check_user_scope
from app.core.auth import get_current_user
from app.core.group_scope import get_current_group_ids
from app.models import User
from app.schemas import BusinessBindingCreate, BusinessBindingResponse
from app.services.audit_service import log_operation
from app.services.business_binding_service import delete_binding, get_binding, upsert_binding
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(
    prefix="/api/manager/users/{user_id}/business-bindings",
    tags=["business-bindings"],
)


@router.get("", response_model=BusinessBindingResponse | None)
async def get_business_binding(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _check_user_scope(db, user, user_id, group_ids)
    binding = await get_binding(db, user_id)
    return BusinessBindingResponse.model_validate(binding) if binding else None


@router.put("", response_model=BusinessBindingResponse)
async def upsert_business_binding(
    user_id: UUID,
    data: BusinessBindingCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _check_user_scope(db, user, user_id, group_ids)
    binding = await upsert_binding(db, user_id, data)
    await log_operation(
        db,
        actor_id=user.id,
        action="user.business_binding_upsert",
        target_type="user",
        target_id=user_id,
        detail={"business_username": binding.business_username},
    )
    return BusinessBindingResponse.model_validate(binding)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_business_binding(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    group_ids: list[UUID] | None = Depends(get_current_group_ids),
):
    await _check_user_scope(db, user, user_id, group_ids)
    deleted = await delete_binding(db, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="业务用户绑定未找到")
    await log_operation(
        db,
        actor_id=user.id,
        action="user.business_binding_delete",
        target_type="user",
        target_id=user_id,
    )
