"""MarkingService — MAC marking management with separation of duties (ADR-016 Phase 2).

Two operation classes, executed by different roles (design §2.4):

  MARKING_ADMIN (manages classification):
    - create_marking_category / create_marking — define markings
    - grant_marking — grant a marking to a Group (组授权铁律)

  PROJECT_OWNER / PROJECT_EDITOR (applies classification to resources):
    - assign_marking — apply an EXISTING marking to a resource
    - revoke_marking_assignment — remove a marking from a resource

This split prevents either role from single-handedly loosening data access:
  - MARKING_ADMIN can't apply markings to resources (can't see project data)
  - PROJECT_OWNER can't create/grant markings (can't loosen classification)

The 合取校验 (boolean AND) itself lives in AuthorizationService._check_marking_and_row
(Layer 5), not here — this service only manages the marking data.

MarkingService is a thin orchestration layer over PostgresMetaStore (which
owns the actual CRUD). It enforces the separation-of-duties role checks via
AuthorizationService before delegating to the store.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from ontology.core.exceptions import ForbiddenError, ValidationError
from ontology.core.permission_roles import OP_MARKING_ASSIGN
from ontology.core.schemas.permission import Principal

if TYPE_CHECKING:

    from ontology.core.models.permission import MarkingCategoryModel, MarkingModel
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
    from ontology.services.authorization_service import AuthorizationService

_log = logging.getLogger(__name__)


class MarkingService:
    """Marking MAC management with separation of duties (Phase 2)."""

    def __init__(
        self,
        metadata: PostgresMetaStore,
        authorization_service: AuthorizationService | None = None,
    ) -> None:
        self._metadata = metadata
        self._authz = authorization_service

    # ── MARKING_ADMIN operations: define + grant ──

    async def create_marking_category(
        self,
        name: str,
        description: str,
        *,
        admin: Principal,
    ) -> str:
        """Create a marking category (MARKING_ADMIN only).

        治理红线 (design §1.4): ≤ 3 categories to prevent marking explosion.
        Fine-grained department isolation should use RowSecurityPolicy, not
        markings.
        """
        await self._require_marking_admin(admin)
        # Governance red line: cap categories.
        from sqlalchemy import func, select

        from ontology.core.models.permission import MarkingCategoryModel

        count = (
            await self._metadata._session.execute(  # noqa: SLF001 — governance query
                select(func.count(MarkingCategoryModel.id))
            )
        ).scalar_one()
        if count >= 3:
            raise ValidationError(
                "Marking category limit reached (≤ 3). Use RowSecurityPolicy for "
                "fine-grained isolation, not markings."
            )
        return await self._metadata.create_marking_category(name, description)

    async def create_marking(
        self,
        category_id: str,
        name: str,
        display_name: str,
        description: str,
        *,
        admin: Principal,
    ) -> str:
        """Create a marking value (MARKING_ADMIN only).

        治理红线 (design §1.4): ≤ 20 markings globally.
        """
        await self._require_marking_admin(admin)
        from sqlalchemy import func, select

        from ontology.core.models.permission import MarkingModel

        count = (
            await self._metadata._session.execute(  # noqa: SLF001 — governance query
                select(func.count(MarkingModel.id))
            )
        ).scalar_one()
        if count >= 20:
            raise ValidationError(
                "Marking limit reached (≤ 20). Use RowSecurityPolicy for fine-grained isolation."
            )
        return await self._metadata.create_marking(
            category_id, name, display_name, description
        )

    async def grant_marking(
        self,
        marking_id: str,
        group_id: str,
        *,
        admin: Principal,
        expires_at: datetime | None = None,
    ) -> None:
        """Grant a marking to a Group (MARKING_ADMIN only, 组授权铁律)."""
        await self._require_marking_admin(admin)
        await self._metadata.grant_marking(marking_id, group_id, expires_at=expires_at)

    # ── PROJECT_OWNER/EDITOR operations: apply markings to resources ──

    async def assign_marking(
        self,
        resource_type: str,
        resource_id: str,
        marking_id: str,
        *,
        principal: Principal,
    ) -> None:
        """Apply an EXISTING marking to a resource (PROJECT_OWNER/EDITOR only).

        Separation of duties: the principal must have marking:assign permission
        (granted via PROJECT_OWNER/EDITOR roles) AND must have access to the
        resource (Layer 1-4 check). This prevents a project owner from applying
        markings to resources they can't access.
        """
        await self._require_marking_assign(principal, resource_type, resource_id)
        await self._metadata.assign_marking(resource_type, resource_id, marking_id)

    async def revoke_marking_assignment(
        self,
        resource_type: str,
        resource_id: str,
        marking_id: str,
        *,
        principal: Principal,
    ) -> None:
        """Remove a marking from a resource (PROJECT_OWNER/EDITOR only)."""
        await self._require_marking_assign(principal, resource_type, resource_id)
        await self._metadata.revoke_marking_assignment(resource_type, resource_id, marking_id)

    # ── Read helpers (any authenticated principal) ──

    async def list_marking_categories(self) -> list[MarkingCategoryModel]:
        from sqlalchemy import select

        from ontology.core.models.permission import MarkingCategoryModel

        return list(
            (
                await self._metadata._session.execute(  # noqa: SLF001 — read query
                    select(MarkingCategoryModel).order_by(MarkingCategoryModel.name)
                )
            ).scalars().all()
        )

    async def list_markings(self, category_id: str | None = None) -> list[MarkingModel]:
        from sqlalchemy import select

        from ontology.core.models.permission import MarkingModel

        stmt = select(MarkingModel).order_by(MarkingModel.name)
        if category_id is not None:
            stmt = stmt.where(MarkingModel.category_id == category_id)
        return list((await self._metadata._session.execute(stmt)).scalars().all())

    async def list_resource_markings(
        self, resource_type: str, resource_id: str
    ) -> list[str]:
        """Return marking ids applied to a resource."""
        return await self._metadata.get_resource_markings(resource_type, resource_id)

    # ── Separation-of-duties enforcement ──

    async def _require_marking_admin(self, principal: Principal) -> None:
        """MARKING_ADMIN role required (manages classification, not projects)."""
        if principal.is_anonymous:
            raise ForbiddenError("Anonymous principal cannot manage markings")
        if "MARKING_ADMIN" not in principal.roles and "PLATFORM_ADMIN" not in principal.roles:
            raise ForbiddenError(
                "MARKING_ADMIN role required to define or grant markings "
                "(separation of duties: classification management is distinct from project ownership)"
            )

    async def _require_marking_assign(
        self, principal: Principal, resource_type: str, resource_id: str
    ) -> None:
        """PROJECT_OWNER/EDITOR required + resource access (marking:assign op).

        Separation of duties: the principal must be able to assign markings
        (marking:assign permission via PROJECT_OWNER/EDITOR) AND must have
        Layer 1-4 access to the target resource. A MARKING_ADMIN without
        project access CANNOT apply markings to resources (and vice versa).
        """
        if principal.is_anonymous:
            raise ForbiddenError("Anonymous principal cannot assign markings")
        # Layer 1-4 access check (can the principal see this resource at all?).
        if self._authz is not None:
            access = await self._authz.check_access(
                principal, resource_type, resource_id, OP_MARKING_ASSIGN
            )
            if not access.allowed:
                raise ForbiddenError(
                    f"Cannot assign marking to {resource_type}:{resource_id} — {access.reason}"
                )
        else:
            # Dev fallback: require a PROJECT role in the header.
            if not any(r in ("OWNER", "EDITOR", "SPACE_OWNER", "SPACE_EDITOR")
                       for r in principal.roles):
                raise ForbiddenError(
                    "PROJECT_OWNER/EDITOR role required to assign markings"
                )
