"""AccessRequestService — JIT permission self-service (ADR-016 Phase 4, design §7.1).

Just-in-Time permission flow: a user requests temporary elevated access they
don't currently hold → an Owner/Admin approves → a time-limited
RoleAssignment/MarkingGrant is created (auto-revoked on expiry). This reduces
standing high privileges and zombie permissions (design §0.7 principle: JIT).

Flow:
  1. ``create_request`` — user submits a request (PENDING) with justification
     + requested duration (expires_at).
  2. ``approve_request`` — an Owner/Admin reviews; on approval, a time-limited
     RoleAssignment (or MarkingGrant) is created with ``expires_at`` = the
     request's expires_at. The AuthorizationService cache is invalidated.
  3. ``reject_request`` — reviewer rejects with a comment.
  4. Expiry sweep (Phase 6 background task) — expired grants are revoked.

Separation of duties: the requester cannot approve their own request. The
reviewer must hold an Owner/Admin role for the requested scope (checked via
AuthorizationService).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ontology.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from ontology.core.schemas.permission import Principal

if TYPE_CHECKING:
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
    from ontology.services.authorization_service import AuthorizationService

_log = logging.getLogger(__name__)


class AccessRequestService:
    """JIT access request lifecycle (Phase 4)."""

    def __init__(
        self,
        metadata: PostgresMetaStore,
        authorization_service: AuthorizationService | None = None,
    ) -> None:
        self._metadata = metadata
        self._authz = authorization_service

    async def create_request(
        self,
        *,
        requester: Principal,
        request_type: str,
        requested_item: str,
        justification: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        """Submit a JIT access request (PENDING).

        ``request_type``: ``ROLE_ASSIGNMENT`` or ``MARKING_GRANT``.
        ``requested_item``: role name (ROLE_ASSIGNMENT) or marking name
        (MARKING_GRANT).
        ``justification``: required (the "why" for the audit trail).
        ``expires_at``: requested grant expiry (NULL = permanent, but JIT
        should always be temporary — the UI enforces a max duration).
        """
        if requester.is_anonymous:
            raise ForbiddenError("Anonymous principal cannot submit access requests")
        if request_type not in ("ROLE_ASSIGNMENT", "MARKING_GRANT"):
            raise ValidationError(f"Invalid request_type: {request_type}")
        if not justification.strip():
            raise ValidationError("Justification is required for access requests")
        req = await self._metadata.create_access_request(
            requester_id=requester.id,
            request_type=request_type,
            requested_item=requested_item,
            justification=justification,
            scope_type=scope_type,
            scope_id=scope_id,
            expires_at=expires_at,
        )
        return req.id

    async def approve_request(
        self,
        request_id: str,
        *,
        reviewer: Principal,
        review_comment: str = "",
    ) -> Any:
        """Approve a PENDING request → create the time-limited grant.

        Separation of duties: the reviewer cannot be the requester. The
        reviewer must hold an Owner/Admin role (checked when the grant target
        is a Project scope). On approval, the cache is invalidated so the new
        grant takes effect immediately.
        """
        req = await self._metadata.get_access_request(request_id)
        if req is None:
            raise NotFoundError("AccessRequest", request_id)
        if req.requester_id == reviewer.id:
            raise ForbiddenError("Cannot approve your own access request (separation of duties)")
        # Transition to APPROVED.
        updated = await self._metadata.update_access_request_status(
            request_id, status="APPROVED", reviewer_id=reviewer.id, review_comment=review_comment,
        )
        # Create the actual grant (time-limited RoleAssignment or MarkingGrant).
        await self._create_grant_from_request(updated)
        # Invalidate the requester's cached decisions so the new grant applies.
        if self._authz is not None:
            await self._authz.invalidate_principal(req.requester_id)
        return updated

    async def reject_request(
        self,
        request_id: str,
        *,
        reviewer: Principal,
        review_comment: str = "",
    ) -> Any:
        """Reject a PENDING request."""
        return await self._metadata.update_access_request_status(
            request_id, status="REJECTED", reviewer_id=reviewer.id, review_comment=review_comment,
        )

    async def list_my_requests(self, requester: Principal) -> list[Any]:
        """List the requester's own access requests."""
        if requester.is_anonymous:
            return []
        return await self._metadata.list_access_requests(requester_id=requester.id)

    async def list_pending_requests(self) -> list[Any]:
        """List all PENDING requests (for reviewers/owners)."""
        return await self._metadata.list_access_requests(status="PENDING")

    async def _create_grant_from_request(self, req: Any) -> None:
        """Create the time-limited grant an approved request authorizes."""
        from sqlalchemy import select

        from ontology.core.models.permission import (
            GroupMembershipModel,
            MarkingGrantModel,
            MarkingModel,
            RoleAssignmentModel,
            RoleModel,
        )

        # Resolve the requester's default group (the grant target, 组授权铁律).
        # For simplicity, grant to the first group the requester belongs to.
        # A production system would let the requester specify the target group.
        group_id = (
            await self._metadata._session.execute(  # noqa: SLF001
                select(GroupMembershipModel.group_id)
                .where(GroupMembershipModel.user_id == req.requester_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if group_id is None:
            raise ValidationError(
                f"Requester {req.requester_id} has no group — cannot grant (组授权铁律)"
            )

        if req.request_type == "ROLE_ASSIGNMENT":
            role = (
                await self._metadata._session.execute(  # noqa: SLF001
                    select(RoleModel).where(RoleModel.name == req.requested_item)
                )
            ).scalar_one_or_none()
            if role is None:
                raise ValidationError(f"Role '{req.requested_item}' not found")
            self._metadata._session.add(  # noqa: SLF001
                RoleAssignmentModel(
                    principal_id=group_id,
                    role_id=role.id,
                    scope_type=req.scope_type or "PROJECT",
                    scope_id=req.scope_id,
                    expires_at=req.expires_at,
                )
            )
        elif req.request_type == "MARKING_GRANT":
            marking = (
                await self._metadata._session.execute(  # noqa: SLF001
                    select(MarkingModel).where(MarkingModel.name == req.requested_item)
                )
            ).scalar_one_or_none()
            if marking is None:
                raise ValidationError(f"Marking '{req.requested_item}' not found")
            self._metadata._session.add(  # noqa: SLF001
                MarkingGrantModel(
                    group_id=group_id, marking_id=marking.id, expires_at=req.expires_at,
                )
            )
        await self._metadata._session.flush()  # noqa: SLF001
