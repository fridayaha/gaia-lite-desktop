"""Container routes — Organization/Space/Project management (ADR-016 Phase 0/1).

Three-tier container hierarchy: Organization → Space (1:1 Ontology) → Project.
Creating a Space atomically creates its paired Ontology + a default Project
(design §2.2, 从动作推断意图).

All routes require an authenticated Principal. Creating containers requires
PLATFORM_ADMIN (or space:admin for Projects under an existing Space).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, ForbiddenError
from ontology.core.schemas.permission import (
    Organization,
    OrganizationCreate,
    Principal,
    Project,
    ProjectCreate,
    Space,
    SpaceCreate,
)
from ontology.services.container_service import ContainerService

router = APIRouter(prefix="/containers", tags=["containers"])


async def get_container_service() -> AsyncIterator[ContainerService]:
    service = container.container_service
    try:
        yield service
    finally:
        await service._metadata.close()  # noqa: SLF001


def _principal(request: Request) -> Principal:
    principal: Principal = request.state.principal
    return principal


# ── Organizations ──


@router.get("/organizations", response_model=list[Organization])
async def list_organizations(
    service: ContainerService = Depends(get_container_service),
) -> Any:
    orgs = await service.list_organizations()
    return [Organization.model_validate(o) for o in orgs]


@router.post("/organizations", response_model=Organization, status_code=201)
async def create_organization(
    body: OrganizationCreate,
    service: ContainerService = Depends(get_container_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        org_id = await service.create_organization(
            api_name=body.api_name, display_name=body.display_name,
            description=body.description, org_type=body.org_type, admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    orgs = await service.list_organizations()
    org = next((o for o in orgs if o.id == org_id), None)
    return Organization.model_validate(org)


# ── Spaces ──


@router.get("/spaces", response_model=list[Space])
async def list_spaces(
    service: ContainerService = Depends(get_container_service),
) -> Any:
    spaces = await service.list_spaces()
    return [Space.model_validate(s) for s in spaces]


@router.post("/spaces", response_model=Space, status_code=201)
async def create_space(
    body: SpaceCreate,
    service: ContainerService = Depends(get_container_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Create a Space atomically (Space + Ontology 1:1 + default Project)."""
    try:
        space_id = await service.create_space(
            api_name=body.api_name, display_name=body.display_name,
            description=body.description, creator=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    spaces = await service.list_spaces()
    space = next((s for s in spaces if s.id == space_id), None)
    return Space.model_validate(space)


# ── Projects ──


@router.get("/projects", response_model=list[Project])
async def list_projects(
    space_id: str | None = None,
    service: ContainerService = Depends(get_container_service),
) -> Any:
    projects = await service.list_projects(space_id)
    return [Project.model_validate(p) for p in projects]


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(
    body: ProjectCreate,
    space_id: str = "",
    service: ContainerService = Depends(get_container_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Create a Project under a Space (requires space:admin on the Space)."""
    if not space_id:
        raise HTTPException(status_code=422, detail="space_id query parameter is required")
    try:
        project_id = await service.create_project(
            api_name=body.api_name, display_name=body.display_name,
            space_id=space_id, description=body.description, admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    projects = await service.list_projects(space_id)
    project = next((p for p in projects if p.id == project_id), None)
    return Project.model_validate(project)


# ── Roles (read-only list) ──


@router.get("/roles", response_model=list[dict[str, Any]])
async def list_roles(
    service: ContainerService = Depends(get_container_service),
) -> Any:
    """List all available roles (built-in + custom)."""
    roles = await service._metadata.list_roles()  # noqa: SLF001
    return [
        {
            "id": r.id, "name": r.name, "scope_type": r.scope_type,
            "permissions": r.permissions, "description": r.description,
            "is_builtin": r.is_builtin,
        }
        for r in roles
    ]
