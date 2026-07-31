from uuid import UUID

from app.core.auth import require_platform_admin
from app.models import User
from app.schemas import PermissionResponse, RoleCreate, RoleResponse, RoleUpdate
from app.services.role_service import (
    create_role,
    delete_role,
    get_role,
    list_permissions,
    list_roles,
    update_role,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from pkg.common.database import get_db

router = APIRouter(prefix="/api/manager/roles", tags=["roles"])


@router.get("")
async def get_roles(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    roles = await list_roles(db)
    result = []
    for r in roles:
        result.append(RoleResponse(
            id=r.id,
            name=r.name,
            description=r.description,
            permission_codes=[p.code for p in r.permissions],
            user_count=len(r.users) if hasattr(r, 'users') else 0,
            created_at=r.created_at,
        ))
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_role(
    data: RoleCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    role = await create_role(db, data, actor_id=user.id)
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permission_codes=[p.code for p in role.permissions],
        user_count=0,
        created_at=role.created_at,
    )


@router.get("/{role_id}")
async def get_role_detail(
    role_id: UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    role = await get_role(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permission_codes=[p.code for p in role.permissions],
        user_count=len(role.users) if hasattr(role, 'users') else 0,
        created_at=role.created_at,
    )


@router.put("/{role_id}")
async def edit_role(
    role_id: UUID,
    data: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    role = await update_role(db, role_id, data, actor_id=user.id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    return RoleResponse(
        id=role.id,
        name=role.name,
        description=role.description,
        permission_codes=[p.code for p in role.permissions],
        user_count=0,
        created_at=role.created_at,
    )


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(
    role_id: UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_platform_admin()),
):
    deleted = await delete_role(db, role_id, actor_id=user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")


@router.get("/permissions/all", response_model=list[PermissionResponse])
async def get_permissions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_platform_admin()),
):
    perms = await list_permissions(db)
    return [PermissionResponse(
        id=p.id, name=p.name, code=p.code, description=p.description, resource_type=p.resource_type
    ) for p in perms]
