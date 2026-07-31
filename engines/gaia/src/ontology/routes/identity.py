"""Identity routes — User/Group/GroupMembership management (ADR-016 Phase 1).

All routes require an authenticated Principal with ``role:manage`` permission
(separation of duties — only role managers can manage identity). This gate is
enforced by IdentityService via AuthorizationService.check_access.

Design §7.2: identity management is the "who" half of the permission system.
Groups are the sole permission carrier (组授权铁律): 100% of role grants target
Groups, never individuals. Users gain permissions by joining a Group.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, ForbiddenError
from ontology.core.schemas.permission import (
    Group,
    GroupCreate,
    GroupMembershipCreate,
    Principal,
    User,
    UserCreate,
)
from ontology.services.identity_service import IdentityService

router = APIRouter(prefix="/identity", tags=["identity"])


async def get_identity_service() -> AsyncIterator[IdentityService]:
    service = container.identity_service
    try:
        yield service
    finally:
        await service._metadata.close()  # noqa: SLF001 — request-scoped session


def _principal(request: Request) -> Principal:
    principal: Principal = request.state.principal
    return principal


# ── JIT auto-provisioning (design §2.3) ──
# Better Auth's databaseHooks.user.create.after calls POST /identity/users
# with X-Provision-Token to auto-create a Gaia user record on signup.
# This bypasses the role:manage gate (it's an internal system call, not a
# user action). The token is shared between Better Auth and Gaia via env.
_SYSTEM_ADMIN = Principal(
    id="system-provisioner",
    principal_type="USER",
    display_name="JIT Auto-Provisioner",
    roles=["PLATFORM_ADMIN"],
    is_anonymous=False,
)


def _resolve_admin(request: Request) -> Principal:
    """Return a system admin if the request carries a valid provision token.

    Used by Better Auth's JIT hook to auto-create Gaia users without a
    human admin's JWT. Falls back to the request's principal (which must
    have role:manage) for normal admin operations.
    """
    from ontology.config.settings import settings

    provision_token = request.headers.get("X-Provision-Token", "")
    expected = getattr(settings, "gaia_provision_token", "") or ""
    if provision_token and expected and provision_token == expected:
        return _SYSTEM_ADMIN
    return request.state.principal


# ── User management ──


@router.post("/users", response_model=User, status_code=201)
async def create_user(
    body: UserCreate,
    service: IdentityService = Depends(get_identity_service),
    request: Request = None,  # injected by FastAPI
) -> Any:
    """Create a User record (maps a Better Auth / OIDC user to Gaia).

    Requires ``role:manage`` (or a valid X-Provision-Token for JIT
    auto-provisioning from Better Auth). The ``subject`` is the OIDC sub
    claim (immutable IdP-side identifier). ``attributes`` holds
    department/region/level etc. synced from OIDC claims — these power
    row-level security.
    """
    principal = _resolve_admin(request)
    try:
        await service.create_user(
            email=body.email, subject=body.subject, attributes=body.attributes,
            home_organization=body.home_organization, admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # Re-read for full fields.
    user = await service._metadata.get_user_by_email(body.email)  # noqa: SLF001
    return User.model_validate(user)


@router.get("/users", response_model=list[User])
async def list_users(
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        users = await service.list_users(principal)
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return [User.model_validate(u) for u in users]


# ── Group management ──


@router.post("/groups", response_model=Group, status_code=201)
async def create_group(
    body: GroupCreate,
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Create a Group (the sole permission carrier, 组授权铁律)."""
    try:
        group_id = await service.create_group(
            name=body.name, organization_id=body.organization_id,
            description=body.description, parent_group_id=body.parent_group_id,
            admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # Re-read for full fields by listing and filtering (simple — group count is small).
    groups = await service._metadata.list_groups(body.organization_id)  # noqa: SLF001
    group = next((g for g in groups if g.id == group_id), None)
    return Group.model_validate(group)


@router.get("/groups", response_model=list[Group])
async def list_groups(
    organization_id: str | None = None,
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        groups = await service.list_groups(organization_id, admin=principal)
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return [Group.model_validate(g) for g in groups]


# ── GroupMembership ──


@router.post("/groups/{group_id}/members", status_code=201)
async def add_group_member(
    group_id: str,
    body: GroupMembershipCreate,
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> dict[str, str]:
    """Add a User to a Group (personnel change → only touches membership)."""
    try:
        await service.add_group_member(
            group_id=group_id, user_id=body.user_id, admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"status": "added", "group_id": group_id, "user_id": body.user_id}


@router.get("/groups/{group_id}/members", response_model=list[User])
async def list_group_members(
    group_id: str,
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        members = await service.list_group_members(group_id, principal)
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return [User.model_validate(u) for u in members]


@router.delete("/groups/{group_id}/members/{user_id}", status_code=204)
async def remove_group_member(
    group_id: str,
    user_id: str,
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> None:
    """Remove a User from a Group (personnel change → only touches membership)."""
    try:
        await service.remove_group_member(
            group_id=group_id, user_id=user_id, admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e


# ── User detail (groups + role assignments) ──


@router.get("/users/{user_id}/groups", response_model=list[Group])
async def list_user_groups(
    user_id: str,
    service: IdentityService = Depends(get_identity_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """List all groups a user belongs to (for the user detail panel)."""
    try:
        await service._require_role_manage(principal)  # noqa: SLF001
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    groups = await service.list_user_groups(user_id)
    return [Group.model_validate(g) for g in groups]
