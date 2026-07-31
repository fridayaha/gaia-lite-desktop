"""Action routes — data write operations with full lifecycle.

Implements the HTTP layer for Action Type definition and Action execution.
All business logic is delegated to ActionService following the thin-routes pattern.
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ontology.config.container import container
from ontology.core.exceptions import (
    NotFoundError,
)
from ontology.core.schemas.action import (
    ActionContext,
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionPreviewResult,
    ActionTypeCreate,
    ActionTypeVersion,
    BatchActionRequest,
    BatchActionResult,
)
from ontology.core.schemas.ontology import ActionType
from ontology.core.schemas.permission import Principal
from ontology.services.action_service import ActionService

router = APIRouter(prefix="/actions", tags=["actions"])


async def get_action_service() -> AsyncIterator[ActionService]:
    """Yield a request-scoped ActionService and close its session after."""
    service = container.action_service
    try:
        yield service
    finally:
        await service.aclose()


def build_context(request: Request) -> ActionContext:
    """Build an ActionContext from the request's resolved Principal.

    ADR-016 Phase 1: the AuthMiddleware already resolved the Principal
    (dev mode: X-User-Id/X-User-Roles headers; Phase 5: Better Auth JWT)
    and injected it onto ``request.state.principal``. We carry it into the
    ActionContext so ActionAuthorizer + ActionService can run the five-layer
    check. ``user_roles`` is derived from ``principal.roles`` for backward
    compat with rule-engine expressions.
    """
    principal: Principal = request.state.principal
    workspace = request.headers.get("X-Workspace-Id", "")
    return ActionContext(
        principal=principal,
        workspace_id=workspace,
        user_roles=list(principal.roles),
    )


@router.post("/execute/{ontology}/{object_type}/{action}", status_code=200)
async def execute_action(
    ontology: str,
    object_type: str,
    action: str,
    request: ActionExecutionRequest,
    service: ActionService = Depends(get_action_service),
    context: ActionContext = Depends(build_context),
) -> ActionExecutionResult:
    """Execute an action against an object type in an ontology.

    Full lifecycle:
    1. Idempotency check → reject duplicates (returns cached result)
    2. Parameter validation → reject invalid input
    3. Rule evaluation → compute derived values
    4. Mutation building → generate change intents
    5. Row-level OCC → detect and reject conflicts
    6. Atomic commit (object_state + execution_log + outbox)
    7. Async CDC to Iceberg + Outbox Executor for side effects

    Status codes (mapped by the global OntologyError handler):
    - 200: Action applied, or accepted (idempotent replay)
    - 404: ActionType or ObjectType not found  (NotFoundError)
    - 403: Write access denied                  (ForbiddenError)
    - 409: Optimistic lock conflict             (ConflictError)
    - 422: Parameter / rule validation failed   (ValidationError)

    Domain exceptions propagate to the global handler so the response carries
    a stable ``code`` (e.g. VALIDATION_FAILED, OBJECT_NOT_FOUND) — do NOT
    re-wrap them in HTTPException here, which would drop the code.
    """
    return await service.execute_action(
        object_type_api_name=object_type,
        action_api_name=action,
        request=request,
        ontology_api_name=ontology,
        context=context,
    )


@router.post("/execute-batch/{ontology}/{object_type}/{action}", status_code=200)
async def execute_batch_action(
    ontology: str,
    object_type: str,
    action: str,
    request: BatchActionRequest,
    service: ActionService = Depends(get_action_service),
    context: ActionContext = Depends(build_context),
) -> BatchActionResult:
    """Execute an action against a batch of objects (P2 — Batch Action).

    Applies one ActionType to every item in ``request.items``. Each item is
    its own atomic unit (own PG transaction + idempotency key), so partial
    success is reported per item — a single OCC conflict / validation error
    does not abort the whole batch unless ``fail_fast=True``.

    The ActionType must have ``batch_enabled = True`` (definition-time gate).

    Returns:
        BatchActionResult with aggregate status (applied/partial/failed/
        rejected) + per-item detail.
    """
    return await service.execute_batch_action(
        object_type_api_name=object_type,
        action_api_name=action,
        request=request,
        ontology_api_name=ontology,
        context=context,
    )


@router.post("/definitions/{ontology}/{action_type}", status_code=201)
async def define_action_type(
    ontology: str,
    action_type: str,
    definition: ActionTypeCreate,
    service: ActionService = Depends(get_action_service),
) -> ActionType:
    """Define a new ActionType with parameter/rules/effect specification.

    The action type defines the contract for how Actions are executed:
    - parameters: Input fields with types, defaults, and required flags
    - rules: Derivation (compute values) and constraint (validate) expressions
    - effects: Side effects (webhook, write-back) to execute after commit
    """
    try:
        definition.api_name = action_type
        result = await service.define_action_type(
            ontology_api_name=ontology,
            action_type_def=definition,
        )
        return result
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/definitions/{ontology}/{action_type}")
async def update_action_type(
    ontology: str,
    action_type: str,
    updates: dict[str, Any],
    service: ActionService = Depends(get_action_service),
) -> ActionType:
    """Update an ActionType and publish a new version snapshot (P1, ADR-011)."""
    try:
        return await service.update_action_type(ontology, action_type, updates)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/definitions/{ontology}/{action_type}", response_model=ActionType)
async def get_action_type(
    ontology: str,
    action_type: str,
    service: ActionService = Depends(get_action_service),
) -> ActionType:
    """Fetch a single ActionType definition (ADR Action Mutation Mapping).

    Used by the frontend ActionTypeEditor to load the full definition
    (parameters / ontology_rules / effects) for editing.
    """
    try:
        return await service.get_action_type(ontology, action_type)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/definitions/{ontology}/{action_type}", status_code=204)
async def delete_action_type(
    ontology: str,
    action_type: str,
    service: ActionService = Depends(get_action_service),
) -> None:
    """Soft-delete an ActionType by marking it DEPRECATED (ADR Action Mutation Mapping).

    Keeps the row for audit / version snapshots / rollback; the active list
    (list_action_types) hides DEPRECATED entries.
    """
    try:
        await service.delete_action_type(ontology, action_type)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/definitions/{ontology}/{action_type}/versions")
async def list_action_type_versions(
    ontology: str,
    action_type: str,
    service: ActionService = Depends(get_action_service),
) -> list[ActionTypeVersion]:
    """List historical versions of an ActionType (P1, ADR-011)."""
    try:
        models = await service.list_action_type_versions(ontology, action_type)
        return [
            ActionTypeVersion(
                id=m.id,
                action_type_id=m.action_type_id,
                version=m.version,
                snapshot=m.snapshot,
                published_by=m.published_by,
                created_at=m.created_at,
            )
            for m in models
        ]
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/definitions/{ontology}/{action_type}/rollback/{version}")
async def rollback_action_type(
    ontology: str,
    action_type: str,
    version: int,
    service: ActionService = Depends(get_action_service),
) -> ActionType:
    """Roll back an ActionType to a prior version (P1, ADR-011)."""
    try:
        return await service.rollback_action_type(ontology, action_type, version)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/preview/{ontology}/{object_type}/{action}")
async def preview_action(
    ontology: str,
    object_type: str,
    action: str,
    request: ActionExecutionRequest,
    service: ActionService = Depends(get_action_service),
    context: ActionContext = Depends(build_context),
) -> ActionPreviewResult:
    """Dry-run an action without persisting (P1, ADR-011 — OMA debug panel).

    Runs validation + rule evaluation + mutation building and returns the
    expected mutations + before_snapshots, but writes nothing to object_state
    or outbox. Validation failures are reported as ``valid=False`` in the
    result body (preview is a diagnostic dry-run); permission / not-found
    errors propagate to the global OntologyError handler.
    """
    return await service.preview_action(
        object_type_api_name=object_type,
        action_api_name=action,
        request=request,
        ontology_api_name=ontology,
        context=context,
    )


@router.post("/validate/{ontology}/{object_type}/{action}")
async def validate_action(
    ontology: str,
    object_type: str,
    action: str,
    request: ActionExecutionRequest,
) -> dict[str, Any]:
    """Pre-validate an action's parameters + rules WITHOUT executing.

    Lightweight check (no HITL, no side effects): resolves the ActionType,
    runs parameter validation against its contract, and returns
    ``{"valid": bool, "errors": [str, ...]}``. Call before ``/execute`` to
    catch parameter/rule violations early with a clear 4xx-free signal —
    unlike ``/preview`` (which also runs rule evaluation + mutation building
    and returns a full dry-run result), this endpoint only validates inputs.

    Shares the same ``validate_action_logic`` as the MCP / AG-UI
    ``validate_action`` tool, so all three entry points agree on what
    "valid" means.
    """
    from ontology.tools.executor import ToolExecutor
    from ontology.tools.toolsets.action import validate_action_logic

    executor = ToolExecutor(container)
    return await validate_action_logic(executor, ontology, object_type, action, request.parameters)
