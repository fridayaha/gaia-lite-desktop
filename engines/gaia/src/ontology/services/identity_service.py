"""IdentityService — manage users, groups, and group membership (ADR-016 Phase 1).

The identity layer is the "who" of the permission system. This service handles
CRUD for Users (mapped from Better Auth / OIDC) and Groups (the sole permission
carrier — 组授权铁律). All role grants target Groups, never individuals; users
gain permissions via GroupMembership.

Design §0.1 principle 3 (组授权铁律): 100% of permissions are granted to Groups.
Personnel changes (join/transfer/leave) only touch GroupMembership — resource
permissions stay untouched. This service is the single entry point for managing
that membership.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ontology.core.exceptions import ConflictError, ForbiddenError
from ontology.core.schemas.permission import Principal
from ontology.services.authorization_service import AuthorizationService

if TYPE_CHECKING:
    from ontology.core.models.permission import GroupModel, UserModel
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)


class IdentityService:
    """Manages Users, Groups, and GroupMembership.

    All mutating operations require ``role:manage`` permission (separation of
    duties — only role managers can create groups / add members). This gate
    is enforced via AuthorizationService.check_access (fail-closed).
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        authorization_service: AuthorizationService,
    ) -> None:
        self._metadata = metadata
        self._authz = authorization_service

    async def _require_role_manage(self, principal: Principal) -> None:
        """Gate: only principals with role:manage may manage identity."""
        result = await self._authz.check_access(principal, "ROLE", "*", "role:manage")
        if not result.allowed:
            raise ForbiddenError(f"Cannot manage identity: {result.reason}")

    # ── User CRUD ──────────────────────────────────────────────────────

    async def create_user(
        self,
        *,
        email: str,
        subject: str,
        attributes: dict[str, object] | None = None,
        home_organization: str | None = None,
        admin: Principal,
    ) -> str:
        """Create a User record (maps a Better Auth / OIDC user to Gaia).

        Returns the new user id. Requires role:manage.
        """
        await self._require_role_manage(admin)
        existing = await self._metadata.get_user_by_email(email)
        if existing:
            raise ConflictError(f"User with email '{email}' already exists")
        existing_sub = await self._metadata.get_user_by_subject(subject)
        if existing_sub:
            raise ConflictError(f"User with subject '{subject}' already exists")
        user = await self._metadata.create_user(
            email=email, subject=subject, attributes=attributes or {},
            home_organization=home_organization,
        )
        _log.info("Created user '%s' (id=%s) by %s", email, user.id, admin.id)
        return user.id

    async def list_users(self, admin: Principal) -> list[UserModel]:
        """List all users. Requires role:manage."""
        await self._require_role_manage(admin)
        return await self._metadata.list_users()

    # ── Group CRUD ─────────────────────────────────────────────────────

    async def create_group(
        self,
        *,
        name: str,
        organization_id: str,
        description: str = "",
        parent_group_id: str | None = None,
        admin: Principal,
    ) -> str:
        """Create a Group (the sole permission carrier).

        Returns the new group id. Requires role:manage.
        """
        await self._require_role_manage(admin)
        existing = await self._metadata.get_group_by_name(name, organization_id)
        if existing:
            raise ConflictError(f"Group '{name}' already exists in this organization")
        group = await self._metadata.create_identity_group(
            name=name, organization_id=organization_id, description=description,
            parent_group_id=parent_group_id,
        )
        _log.info("Created group '%s' (id=%s) by %s", name, group.id, admin.id)
        return group.id

    async def list_groups(
        self, organization_id: str | None = None, admin: Principal | None = None
    ) -> list[GroupModel]:
        """List groups, optionally filtered by organization."""
        if admin is not None:
            await self._require_role_manage(admin)
        return await self._metadata.list_groups(organization_id)

    # ── GroupMembership ────────────────────────────────────────────────

    async def add_group_member(
        self, *, group_id: str, user_id: str, admin: Principal
    ) -> None:
        """Add a User to a Group (personnel change → only touches membership)."""
        await self._require_role_manage(admin)
        await self._metadata.add_group_member(group_id=group_id, user_id=user_id)
        # Invalidate the user's cached permission decisions (they may have
        # gained new roles via this group).
        await self._authz.invalidate_principal(user_id)
        _log.info("Added user %s to group %s by %s", user_id, group_id, admin.id)

    async def list_group_members(
        self, group_id: str, admin: Principal
    ) -> list[UserModel]:
        """List users in a group. Requires role:manage."""
        await self._require_role_manage(admin)
        return await self._metadata.list_group_members(group_id)

    async def remove_group_member(
        self, *, group_id: str, user_id: str, admin: Principal
    ) -> None:
        """Remove a User from a Group (personnel change → only touches membership)."""
        await self._require_role_manage(admin)
        await self._metadata.remove_group_member(group_id=group_id, user_id=user_id)
        await self._authz.invalidate_principal(user_id)
        _log.info("Removed user %s from group %s by %s", user_id, group_id, admin.id)

    async def list_user_groups(self, user_id: str) -> list[GroupModel]:
        """Return all groups a user belongs to (for display/debugging)."""
        return await self._metadata.list_user_groups(user_id)
