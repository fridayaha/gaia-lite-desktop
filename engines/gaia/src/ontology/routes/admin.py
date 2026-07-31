"""Admin routes — 运维/管理端点（图/时空重建、数据修复等）。

这些端点会触发大规模数据扫描和写入，仅限 PLATFORM_ADMIN 角色调用。
权限校验通过 AuthorizationService.check_access(principal, "*", "*", "*")
（PLATFORM_ADMIN 拥有 OP_PLATFORM_ADMIN 通配权限）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from ontology.config.container import container
from ontology.core.exceptions import ForbiddenError
from ontology.core.schemas.permission import Principal
from ontology.routes._deps import get_authz_service, get_principal
from ontology.services.authorization_service import AuthorizationService

router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_platform_admin(
    principal: Principal,
    authz: AuthorizationService,
) -> None:
    """Gate: only PLATFORM_ADMIN may call admin endpoints.

    check_access(principal, "*", "*", "*") — PLATFORM_ADMIN 拥有
    OP_PLATFORM_ADMIN 通配权限（permission_roles.py），其他角色拒绝。
    """
    result = await authz.check_access(principal, "*", "*", "*")
    if not result.allowed:
        raise ForbiddenError(f"Admin operation denied: {result.reason}")


@router.post("/project/rebuild/{ontology_api_name}/{object_type_api_name}")
async def rebuild_projections(
    ontology_api_name: str,
    object_type_api_name: str,
    request: Request,
    dataset_api_name: str | None = None,
    limit: int = 10_000,
    principal: Principal = Depends(get_principal),
    authz: AuthorizationService = Depends(get_authz_service),
) -> dict[str, int]:
    """从 Iceberg 全量重建某个 ObjectType 的图+时空投影。

    用于外部数据接入后首次填充，或 Neo4j/PostGIS 数据修复。
    Action 写入路径的投影不受影响（由 OutboxExecutor 实时消费）。

    需要 PLATFORM_ADMIN 角色。
    """
    try:
        await _require_platform_admin(principal, authz)
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    svc = container.object_index_funnel
    return await svc.project_for_object_type(
        ontology_api_name=ontology_api_name,
        object_type_api_name=object_type_api_name,
        dataset_api_name=dataset_api_name,
        limit=limit,
    )


@router.post("/project/rebuild-for-dataset/{dataset_api_name}")
async def rebuild_projections_for_dataset(
    dataset_api_name: str,
    request: Request,
    limit: int = 10_000,
    principal: Principal = Depends(get_principal),
    authz: AuthorizationService = Depends(get_authz_service),
) -> dict[str, dict[str, int]]:
    """从 Iceberg 全量重建某 dataset 关联的所有 ObjectType 的图+时空投影。

    需要 PLATFORM_ADMIN 角色。
    """
    try:
        await _require_platform_admin(principal, authz)
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    svc = container.object_index_funnel
    return await svc.project_for_dataset(
        dataset_api_name=dataset_api_name,
        limit=limit,
    )


@router.post("/project/rebuild-for-virtual/{ontology_api_name}/{object_type_api_name}")
async def rebuild_projections_for_virtual(
    ontology_api_name: str,
    object_type_api_name: str,
    request: Request,
    principal: Principal = Depends(get_principal),
    authz: AuthorizationService = Depends(get_authz_service),
) -> dict[str, int | str | bool]:
    """全量重建某 VIRTUAL ObjectType 的图拓扑投影（ADR-021 §3.3）。

    从 Trino 联邦查外部源拉取全量行，合成 object_state 后批量 MERGE 进 Neo4j
    （节点骨架 + FK→边），再清理本次未触及的孤儿节点。适用于外部源数据变更
    后手动刷新拓扑（二期会有定时任务自动触发）。

    幂等：重复调用 = 重新投影（Neo4j MERGE 天然幂等 + cleanup 清孤儿）。
    需要 PLATFORM_ADMIN 角色。
    """
    try:
        await _require_platform_admin(principal, authz)
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e

    svc = container.object_index_funnel
    return await svc.project_for_virtual_object_type(
        ontology_api_name=ontology_api_name,
        object_type_api_name=object_type_api_name,
    )
