"""Authz routes — Check Access + JIT access requests + audit logs (ADR-016 Phase 4).

Three groups (design §7.1 + §7.6):

  /authz/check — explainability: input principal + resource + action →
    per-layer decision + provenance + missing permissions (Check Access panel).

  /authz/access-requests — JIT self-service: submit/approve/reject temporary
    permission requests (PENDING → APPROVED/REJECTED/EXPIRED).

  /audit-logs — query the append-only audit trail (by principal/resource/time).

All routes require an authenticated Principal (from AuthMiddleware).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ontology.core.schemas.permission import (
    AccessRequest,
    AccessRequestCreate,
    AccessRequestReview,
    AllowedActionsRequest,
    AllowedActionsResponse,
    AuditLog,
    CheckAccessResult,
    MigrationImpact,
    PolicyGenerationRequest,
    PolicyGenerationResult,
    Principal,
    RoleAssignmentCreate,
    RoleAssignmentResponse,
    RowSecurityPolicy,
    RowSecurityPolicyCreate,
)
from ontology.services.access_request_service import AccessRequestService
from ontology.services.authorization_service import AuthorizationService
from ontology.services.permission_envelope import action_registry

router = APIRouter(prefix="/authz", tags=["authz"])


def _principal(request: Request) -> Principal:
    principal: Principal = request.state.principal
    return principal


async def get_authz_service() -> AsyncIterator[AuthorizationService]:
    svc = container.authorization_service
    try:
        yield svc
    finally:
        await svc._metadata.close()  # noqa: SLF001


async def get_access_request_service() -> AsyncIterator[AccessRequestService]:
    svc = container.access_request_service
    try:
        yield svc
    finally:
        await svc._metadata.close()  # noqa: SLF001


# ── Check Access (explainability) ──


@router.get("/check", response_model=CheckAccessResult)
async def check_access(
    resource_type: str = Query(..., description="OBJECT_TYPE / ACTION_TYPE / DATASET ..."),
    resource_id: str = Query(..., description="Resource api_name or id"),
    action: str = Query(..., description="object:view / action_type:execute ..."),
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Explainable access check — per-layer status + provenance + missing.

    Unlike the internal check_access (flat allow/deny + cached + audited),
    this is a read-only probe: no cache, no audit entry. Returns the full
    CheckAccessResult for the debug panel + Agent self-probing.
    """
    return await authz.check_access_explained(
        principal, resource_type, resource_id, action
    )


# ── JIT Access Requests ──


@router.post("/access-requests", response_model=AccessRequest, status_code=201)
async def create_access_request(
    body: AccessRequestCreate,
    service: AccessRequestService = Depends(get_access_request_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        req_id = await service.create_request(
            requester=principal,
            request_type=body.request_type,
            requested_item=body.requested_item,
            justification=body.justification,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            expires_at=body.expires_at,
        )
    except (ForbiddenError, ValidationError) as e:
        raise HTTPException(status_code=403 if isinstance(e, ForbiddenError) else 422,
                            detail=str(e)) from e
    # Return the created request (re-read for full fields).
    req = await service._metadata.get_access_request(req_id)  # noqa: SLF001
    return AccessRequest.model_validate(req)


@router.get("/access-requests", response_model=list[AccessRequest])
async def list_access_requests(
    pending_only: bool = Query(False, description="List PENDING requests (for reviewers)"),
    service: AccessRequestService = Depends(get_access_request_service),
    principal: Principal = Depends(_principal),
) -> Any:
    if pending_only:
        reqs = await service.list_pending_requests()
    else:
        reqs = await service.list_my_requests(principal)
    return [AccessRequest.model_validate(r) for r in reqs]


@router.post("/access-requests/{request_id}/approve", response_model=AccessRequest)
async def approve_access_request(
    request_id: str,
    body: AccessRequestReview,
    service: AccessRequestService = Depends(get_access_request_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        req = await service.approve_request(
            request_id, reviewer=principal, review_comment=body.review_comment
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return AccessRequest.model_validate(req)


@router.post("/access-requests/{request_id}/reject", response_model=AccessRequest)
async def reject_access_request(
    request_id: str,
    body: AccessRequestReview,
    service: AccessRequestService = Depends(get_access_request_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        req = await service.reject_request(
            request_id, reviewer=principal, review_comment=body.review_comment
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return AccessRequest.model_validate(req)


# ── Audit Logs (read-only query) ──


@router.get("/audit-logs", response_model=list[AuditLog])
async def list_audit_logs(
    principal_id: str | None = Query(None),
    resource_type: str | None = Query(None),
    result: str | None = Query(None, description="ALLOW or DENY"),
    layer: str | None = Query(None, description="IDENTITY/ORG/SPACE/PROJECT/MARKING/ROW"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Query the append-only audit log (AUDIT_ADMIN or scoped by permission)."""
    # Phase 4: any authenticated principal can query (Phase 5 adds AUDIT_ADMIN
    # gating — the route returns what the principal is allowed to see).
    if principal.is_anonymous:
        raise HTTPException(status_code=401, detail="Authentication required")
    logs = await authz._metadata.list_audit_logs(  # noqa: SLF001
        principal_id=principal_id,
        resource_type=resource_type,
        result=result,
        layer=layer,
        limit=limit,
        offset=offset,
    )
    return [AuditLog.model_validate(log) for log in logs]


# ── Ship-the-decision: batch allowedActions (design §8.2) ──


@router.post("/allowed-actions", response_model=AllowedActionsResponse)
async def batch_allowed_actions(
    body: AllowedActionsRequest,
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Batch-resolve allowedActions for N resources in one call.

    This is the ship-the-decision channel (design §8.2): the frontend renders
    permission state from these decisions instead of re-deriving rules or
    calling ``/authz/check`` per resource. One request per page load, not one
    per resource — no N+1.

    Returns ``{resource_type, decisions: {resource_id: {allowedActions, disabledReasons}}}``.
    The actions resolved per resource come from the centralized
    ``action_registry`` (one declarative source, not scattered per-route).
    """
    actions = action_registry.actions_for(body.resource_type)
    if not actions or not body.resource_ids:
        return AllowedActionsResponse(resource_type=body.resource_type, decisions={})
    # Build one batch: every resource × every registered action.
    batch = [
        (body.resource_type, rid, action)
        for rid in body.resource_ids
        for action in actions
    ]
    results = await authz.check_access_batch(principal, batch)
    decisions: dict[str, dict[str, Any]] = {}
    for rid in body.resource_ids:
        allowed: list[str] = []
        disabled: dict[str, str] = {}
        for action in actions:
            r = results[(body.resource_type, rid, action)]
            if r.allowed:
                allowed.append(action)
            else:
                disabled[action] = r.reason or "无权限"
        decisions[rid] = {"allowedActions": allowed, "disabledReasons": disabled}
    return AllowedActionsResponse(resource_type=body.resource_type, decisions=decisions)


# ── Role Assignment (design §7.3 — grant roles to Groups, not individuals) ──


@router.post("/role-assignments", response_model=RoleAssignmentResponse, status_code=201)
async def create_role_assignment(
    body: RoleAssignmentCreate,
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """Grant a role to a Group at a scope (组授权铁律: Group, not User).

    Only principals with ``role:manage`` permission may grant roles
    (separation of duties, design §0.1 principle 6). The grant is
    invalidated from the permission cache on success (design §2.1).
    """
    # Permission gate: role:manage is a high-sensitivity action — always
    #实时校验, never cached.
    gate = await authz.check_access(principal, "ROLE", "*", "role:manage")
    if not gate.allowed:
        raise HTTPException(status_code=403, detail=f"Cannot grant roles: {gate.reason}")
    try:
        assignment = await authz._metadata.create_role_assignment(  # noqa: SLF001
            group_id=body.group_id,
            role_name=body.role_name,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            expires_at=body.expires_at,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # Invalidate the affected group's cached decisions (fail-closed refresh).
    await authz.invalidate_principal(body.group_id)
    return RoleAssignmentResponse(
        id=assignment.id,
        group_id=assignment.principal_id,
        role_name=body.role_name,
        scope_type=assignment.scope_type,
        scope_id=assignment.scope_id,
        expires_at=assignment.expires_at,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


@router.get("/role-assignments", response_model=list[RoleAssignmentResponse])
async def list_role_assignments(
    scope_id: str | None = Query(None, description="Filter by scope (Space/Project id)"),
    group_id: str | None = Query(None, description="Filter by group id"),
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> Any:
    """List role assignments, optionally filtered by scope or group.

    Requires ``role:manage`` (only role managers see the full grant list).
    """
    gate = await authz.check_access(principal, "ROLE", "*", "role:manage")
    if not gate.allowed:
        raise HTTPException(status_code=403, detail=f"Cannot list role assignments: {gate.reason}")
    rows = await authz._metadata.list_role_assignments(  # noqa: SLF001
        scope_id=scope_id, group_id=group_id
    )
    return [
        RoleAssignmentResponse(
            id=a.id, group_id=a.principal_id, role_name=role_name,
            scope_type=a.scope_type, scope_id=a.scope_id,
            expires_at=a.expires_at, created_at=a.created_at, updated_at=a.updated_at,
        )
        for a, role_name in rows
    ]


@router.delete("/role-assignments/{assignment_id}", status_code=204)
async def delete_role_assignment(
    assignment_id: str,
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> None:
    """Revoke a role assignment by id (requires ``role:manage``)."""
    gate = await authz.check_access(principal, "ROLE", "*", "role:manage")
    if not gate.allowed:
        raise HTTPException(status_code=403, detail=f"Cannot revoke roles: {gate.reason}")
    try:
        await authz._metadata.delete_role_assignment(assignment_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    # Cache invalidation: we don't know the group_id here without a read,
    # so invalidate broadly (the grant list changed). A follow-up could read
    # the assignment before delete for targeted invalidation.
    await authz.invalidate_resource("ROLE", "*")


# ═══════════════════════════════════════════════════════════════════
# Row Security Policies (Phase 3, ABAC) + LLM-assisted generation (Phase 7)
# ═══════════════════════════════════════════════════════════════════


@router.post("/row-security-policies", response_model=RowSecurityPolicy, status_code=201)
async def create_row_security_policy(
    body: RowSecurityPolicyCreate,
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> RowSecurityPolicy:
    """Create a row-level security policy (Cedar condition) for an ObjectType.

    Requires ``object_type:manage`` on the target ObjectType's Project.
    When ``generated_by="llm"``, the expression was produced by the LLM
    policy assistant and ``generation_meta`` records its provenance
    (prompt/model/reviewer) for audit (ADR-017 D6).
    """
    gate = await authz.check_access(principal, "ObjectType", body.object_type_id, "object_type:manage")
    if not gate.allowed:
        raise HTTPException(status_code=403, detail=f"Cannot manage row security policies: {gate.reason}")
    # Re-validate the expression before saving (defense-in-depth: even if
    # the UI tampered with it, we never save an invalid Cedar expression).
    from ontology.services.ai_policy_generate import _validate_expression

    props = await authz._metadata.get_properties(body.object_type_id)
    resource_attrs = {p.api_name: "String" for p in props}

    passed, errors = _validate_expression(
        body.expression,
        principal_attributes=principal.attributes,
        principal_markings=principal.markings,
        resource_attributes=resource_attrs,
    )
    if not passed:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid Cedar expression: {'; '.join(errors)}",
        )
    try:
        policy = await authz._metadata.create_row_security_policy(
            object_type_id=body.object_type_id,
            expression=body.expression,
            description=body.description,
            generated_by=body.generated_by,
            generation_meta=body.generation_meta,
        )
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    await authz.invalidate_resource("ObjectType", body.object_type_id)
    return RowSecurityPolicy.model_validate(policy)


@router.get("/row-security-policies", response_model=list[RowSecurityPolicy])
async def list_row_security_policies(
    object_type_id: str | None = Query(None),
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> list[RowSecurityPolicy]:
    """List row security policies, optionally filtered by ObjectType."""
    policies = await authz._metadata.list_row_security_policies(object_type_id)
    # Filter to those the principal can see (object_type:view on the Project).
    visible: list[RowSecurityPolicy] = []
    for p in policies:
        gate = await authz.check_access(principal, "ObjectType", p.object_type_id, "object_type:view")
        if gate.allowed:
            visible.append(RowSecurityPolicy.model_validate(p))
    return visible


@router.delete("/row-security-policies/{policy_id}", status_code=204)
async def delete_row_security_policy(
    policy_id: str,
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> None:
    """Delete a row security policy (requires ``object_type:manage``)."""
    policy = await authz._metadata.get_row_security_policy_by_id(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Row security policy not found")
    gate = await authz.check_access(principal, "ObjectType", policy.object_type_id, "object_type:manage")
    if not gate.allowed:
        raise HTTPException(status_code=403, detail=f"Cannot delete row security policy: {gate.reason}")
    await authz._metadata.delete_row_security_policy(policy_id)
    await authz.invalidate_resource("ObjectType", policy.object_type_id)


@router.post("/generate-policy", response_model=PolicyGenerationResult)
async def generate_policy(
    body: PolicyGenerationRequest,
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> PolicyGenerationResult:
    """Generate a Cedar row-security policy from natural language (ADR-017 D6).

    Verifier-guided loop: LLM proposes → cedarpy validate → repair → dry-run
    preview. The result is a DRAFT — it is NOT saved. The caller must review
    the floor/ceiling previews and POST ``/row-security-policies`` to save.

    Requires ``object_type:manage`` on the target ObjectType (only users who
    can create policies can ask the LLM to draft them).
    """
    gate = await authz.check_access(principal, "ObjectType", body.object_type_id, "object_type:manage")
    if not gate.allowed:
        raise HTTPException(status_code=403, detail=f"Cannot generate policies: {gate.reason}")
    from ontology.services.ai_policy_generate import generate_policy as _generate

    return await _generate(body, authz._metadata)


# ═══════════════════════════════════════════════════════════════════
# Option B→A migration (ADR-016 D3, resource ownership evolution)
# ═══════════════════════════════════════════════════════════════════


@router.get("/migration-impact/{object_type_id}", response_model=MigrationImpact)
async def preview_migration_impact(
    object_type_id: str,
    target_project_id: str = Query(..., description="The Project to migrate to"),
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> MigrationImpact:
    """Preview the permission impact of migrating an ObjectType to a new Project.

    Returns a per-group diff (gain/lose/unchanged) so the admin can verify
    no users unexpectedly lose access before committing. Requires
    ``project:manage`` on the current Project (the caller must be an Owner
    of the resource being migrated).
    """
    try:
        impact = await authz.preview_migration_impact(object_type_id, target_project_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    # Permission: must be OWNER of the current Project.
    gate = await authz.check_access(
        principal, "PROJECT", impact.current_project_id, "project:manage"
    )
    if not gate.allowed:
        raise HTTPException(
            status_code=403, detail=f"Cannot preview migration: {gate.reason}"
        )
    return impact


@router.post("/migrate-object-type", response_model=MigrationImpact)
async def migrate_object_type_to_project(
    object_type_id: str = Query(..., description="The ObjectType to migrate"),
    target_project_id: str = Query(..., description="The target Project"),
    authz: AuthorizationService = Depends(get_authz_service),
    principal: Principal = Depends(_principal),
) -> MigrationImpact:
    """Migrate an ObjectType to a different Project (option B→A, ADR-016 D3).

    Requires ``project:manage`` on BOTH the current and target Projects
    (Palantir: the caller must own both source and target). The migration
    is irreversible at the DB level (project_id is NOT NULL after the
    option A contract migration), but the ObjectType can be migrated again
    to another Project later.
    """
    try:
        return await authz.migrate_object_type_to_project(
            object_type_id, target_project_id, principal
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
