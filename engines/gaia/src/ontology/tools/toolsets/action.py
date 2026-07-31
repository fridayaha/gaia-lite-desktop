"""Action-layer tools (2) — invoke_action + validate_action.

Per docs/architecture/ontology-tool-layer.md Sprint 2 (ADR-010).

Same "shared logic + dual exposure" pattern as write.py:
  - ``invoke_action_logic`` / ``validate_action_logic`` are the protocol-
    agnostic source of truth.
  - ``build_action_toolset()`` is the AG-UI exposure (reads executor from
    ctx.deps). MCP exposure calls the same _logic functions.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ontology.core.exceptions import ValidationError as ActionValidationError
from ontology.core.schemas.action import ActionExecutionRequest, ActionTypeParameter
from ontology.services.action_validator import ParameterValidator
from ontology.tools.executor import RiskLevel, ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets._contracts import (
    INVOKE_ACTION_DESC,
    VALIDATE_ACTION_DESC,
)

# ── Shared logic (protocol-agnostic) ─────────────────────────────────────


async def invoke_action_logic(
    executor: ToolExecutor,
    ontology: str,
    object_type: str,
    action_type: str,
    parameters: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Execute a predefined action. Risk-gated by ActionType.risk_level."""
    svc = executor.container.action_service

    # Read risk_level from the ActionType metadata — drives gating.
    action_def = await executor.audit_call(
        "invoke_action.resolve",
        {"ontology": ontology, "action_type": action_type},
        svc._metadata.get_action_type(ontology, action_type),
    )
    if isinstance(action_def, dict) and "error" in action_def:
        return action_def
    risk_level: RiskLevel = action_def.risk_level
    impact = (
        f"将执行动作 {action_def.display_name} ({action_type}) "
        f"于对象类型 {object_type},risk_level={risk_level}。"
        f"参数: {parameters or {}}。"
        f"{('高危动作,请确认影响。' if risk_level == 'high' else '')}"
    )

    async def _do() -> dict[str, Any]:
        req = ActionExecutionRequest(
            parameters=parameters or {},
            idempotency_key=idempotency_key,
        )
        result = await svc.execute_action(object_type, action_type, req, ontology)
        return {
            "status": result.status,
            "action_id": result.action_id,
            "mutations": list(result.mutations) if result.mutations else [],
        }

    return cast(
        "dict[str, Any]",
        await executor.execute_write(
            "invoke_action",
            {
                "ontology": ontology,
                "object_type": object_type,
                "action_type": action_type,
                "parameters": parameters or {},
            },
            risk_level,
            impact,
            _do,
        ),
    )


async def validate_action_logic(
    executor: ToolExecutor,
    ontology: str,
    object_type: str,
    action_type: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pre-validate an action's parameters + rules WITHOUT executing. No HITL."""
    svc = executor.container.action_service
    action_def = await executor.audit_call(
        "validate_action.resolve",
        {"ontology": ontology, "action_type": action_type},
        svc._metadata.get_action_type(ontology, action_type),
    )
    if isinstance(action_def, dict) and "error" in action_def:
        return action_def

    async def _do() -> Any:
        # ActionType.parameters is stored as {"parameters": [...dict...]}
        # (see ActionService.define_action_type). Reconstruct the typed list.
        params_raw = (action_def.parameters or {}).get("parameters", [])
        params = [ActionTypeParameter.model_validate(p) for p in params_raw]
        validator = ParameterValidator()
        try:
            validator.validate(params, parameters or {})
            return {"valid": True, "errors": []}
        except ActionValidationError as exc:
            return {"valid": False, "errors": [str(exc)]}

    return cast(
        "dict[str, Any]",
        await executor.audit_call(
            "validate_action",
            {"ontology": ontology, "object_type": object_type, "action_type": action_type},
            _do(),
        ),
    )


# ── AG-UI exposure ───────────────────────────────────────────────────────


def build_action_toolset() -> FunctionToolset[AppState]:
    """Build the AG-UI action-layer toolset."""
    ts: FunctionToolset[AppState] = FunctionToolset()

    @ts.tool(
        description=INVOKE_ACTION_DESC,
        # invoke_action's risk_level is read at runtime from the ActionType
        # definition. We declare metadata.risk_level="unknown" so the
        # MetadataApprovalToolset wrapper defers the call for AG-UI interrupt
        # approval, and the frontend batch panel defaults these to per-item
        # review (no blanket-approve) — conservative for actions whose risk
        # isn't known until the ActionType is resolved.
        metadata={"risk_level": "unknown"},
    )
    async def invoke_action(
        ctx: RunContext[AppState],
        /,
        ontology: str,
        object_type: str,
        action_type: str,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute a predefined action. (See shared description.)"""
        executor = ctx.deps.executor
        if executor is None:
            return {"error": {"code": "NO_EXECUTOR", "message": "action tools require a request-scoped executor"}}
        ontology = ontology or ctx.deps.ontology
        return await invoke_action_logic(
            executor,
            ontology,
            object_type,
            action_type,
            parameters,
            idempotency_key,
        )

    @ts.tool(description=VALIDATE_ACTION_DESC)
    async def validate_action(
        ctx: RunContext[AppState],
        ontology: str,
        object_type: str,
        action_type: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Pre-validate an action's parameters without executing. (See shared description.)"""
        executor = ctx.deps.executor
        if executor is None:
            return {"error": {"code": "NO_EXECUTOR", "message": "action tools require a request-scoped executor"}}
        ontology = ontology or ctx.deps.ontology
        return await validate_action_logic(
            executor,
            ontology,
            object_type,
            action_type,
            parameters,
        )

    return ts
