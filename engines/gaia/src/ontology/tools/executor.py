"""Tool executor — the single governance chokepoint for all tool calls.

Per docs/architecture/ontology-tool-layer.md §四 and ADR-009/010, every
tool call (from MCP, AG-UI, or REST) flows through this executor. It
applies the governance cross-cutting concerns:

  - Audit logging: every call logged with name/args/duration/status.
    Principal is ``"anonymous"`` until Sprint 3 adds the Principal
    abstraction (auth from MCP OAuth / AG-UI business field / REST JWT).
  - HITL gating (ADR-010): graded approval for write/action tools.
    - AG-UI path (built-in Web UI): tools are declared
      ``requires_approval=True`` at the pydantic-ai layer (see
      ``toolsets/write.py`` / ``toolsets/action.py``). pydantic-ai collects
      the pending approvals into a ``DeferredToolRequests`` output;
      ``AGUIAdapter`` turns that into an AG-UI ``RUN_FINISHED { outcome:
      { type: "interrupt" } }`` with one ``Interrupt`` per tool call. The
      frontend renders a batch-approval panel and submits ``resume``;
      ``AGUIAdapter.deferred_tool_results`` maps it back to
      ``DeferredToolResults``, and the agent re-runs executing the approved
      tools. The executor is NOT involved in the AG-UI approval flow — it
      only runs the (already-approved) tool body on resume.
    - MCP path (external Agents): ``MCPApprovalHandler`` calls
      ``ctx.elicit`` synchronously (a native client dialog) and returns a
      bool. The executor's ``execute_gated`` drives this path: medium/high
      risk delegate to the handler, low risk executes immediately.
  - Permission checks (Sprint 3): functional + row/column level.

The executor holds NO business logic. Tools are thin wrappers over Service
methods; the executor sits between the tool function and the Service call
so audit + (MCP) HITL are centralized regardless of entry point.

storage_type routing (MANAGED → Doris+Iceberg, VIRTUAL → Trino federation)
is NOT handled here — it lives in ObjectQueryService per the existing
_load_physical / _load_virtual pattern.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ontology.config.container import Container
from ontology.middleware.tracing import get_trace_id

_log = logging.getLogger(__name__)

RiskLevel = str  # "low" | "medium" | "high"


@dataclass
class ApprovalRequest:
    """A pending HITL approval for an MCP write/action tool call.

    Used only by the synchronous MCP elicitation path
    (``MCPApprovalHandler.request_approval``) — the AG-UI path no longer
    routes through ``execute_gated`` for approvals (it uses pydantic-ai's
    native ``requires_approval`` + AG-UI interrupt/resume instead). Kept as
    a data carrier so the MCP handler receives the tool name, args,
    risk_level, and impact for its elicit message.
    """

    tool_name: str
    args: dict[str, Any]
    risk_level: RiskLevel
    impact: str  # human-readable impact summary (built by the tool)
    diff_preview: str | None  # optional structured diff (e.g. for create)
    principal: str = "anonymous"


class ApprovalHandler(Protocol):
    """Protocol-specific approval interaction (MCP elicitation).

    The executor calls ``request_approval`` when an MCP tool needs HITL.
    The handler implements the protocol-specific UX:
      - MCP: ``Context.elicit()`` → client renders a native dialog → bool.

    The handler returns ``True`` (approved) or ``False`` (denied).

    Note: the AG-UI path does NOT use this handler — AG-UI tools declare
    ``requires_approval=True`` and let pydantic-ai + AGUIAdapter handle the
    interrupt/resume lifecycle. This protocol is MCP-only.
    """

    async def request_approval(self, approval: ApprovalRequest) -> bool: ...


class ToolExecutor:
    """Single governance chokepoint for all ontology tool calls.

    Holds a reference to the DI ``Container`` (protocol-agnostic — works in
    both the FastAPI process and the standalone MCP process) and an optional
    ``ApprovalHandler``. The handler is None for read-only contexts and set
    by the MCP entry point for write/action tools. The AG-UI path does not
    set a handler (its approvals are handled by pydantic-ai/AGUIAdapter).
    """

    def __init__(
        self,
        container: Container,
        approval_handler: ApprovalHandler | None = None,
    ) -> None:
        self._container = container
        self._approval_handler = approval_handler

    @property
    def container(self) -> Container:
        """The DI container — tools use this to reach Service instances."""
        return self._container

    async def audit_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        coro: Any,
        *,
        principal: str = "anonymous",
    ) -> Any:
        """Await ``coro`` with audit logging around it.

        Tools use this to wrap a Service call so every invocation is logged
        uniformly (start, duration, error). The coroutine may return any
        JSON-serializable value. On error the result is normalized to the
        standard ``{"error": {"code","message"}}`` envelope so the tool
        layer never raises into the LLM/MCP boundary.

        Callers type-narrow the success result themselves — the executor
        returns ``Any`` because different Services return different shapes.
        """
        started = time.perf_counter()
        _log.info(
            "tool.call.start tool=%s trace_id=%s principal=%s args=%s", tool_name, get_trace_id(), principal, args
        )
        try:
            result = await coro
            duration_ms = int((time.perf_counter() - started) * 1000)
            _log.info("tool.call.ok tool=%s trace_id=%s duration_ms=%s", tool_name, get_trace_id(), duration_ms)
            return result
        except Exception as exc:  # noqa: BLE001 — convert all errors to dict
            duration_ms = int((time.perf_counter() - started) * 1000)
            code = _classify_error(exc)
            _log.warning(
                "tool.call.error tool=%s trace_id=%s duration_ms=%s code=%s error=%s",
                tool_name,
                get_trace_id(),
                duration_ms,
                code,
                exc,
            )
            return {"error": {"code": code, "message": str(exc) or exc.__class__.__name__}}

    async def execute_gated(
        self,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel,
        impact: str,
        execute: Callable[[], Awaitable[Any]],
        *,
        diff_preview: str | None = None,
        principal: str = "anonymous",
    ) -> Any:
        """Run an MCP write/action tool call through the HITL + audit gate.

        This is the MCP-path entry point for tools with side effects. The
        AG-UI path does NOT call this for approvals — AG-UI tools are
        declared ``requires_approval=True`` and pydantic-ai + AGUIAdapter
        own the interrupt/resume lifecycle; the tool body only runs after
        approval, at which point it calls ``audit_call`` directly (not
        ``execute_gated``).

        MCP flow:
          1. risk_level == "low" → skip approval, execute immediately.
          2. risk_level in {"medium","high"} → build ApprovalRequest,
             delegate to ApprovalHandler (MCP elicitation, synchronous bool).
          3. approved → execute + audit.
          4. denied → return DENIED marker.

        Args:
            tool_name: Logical tool name.
            args: Tool arguments (for audit + impact display).
            risk_level: "low" | "medium" | "high".
            impact: Human-readable impact summary (built by the tool).
            execute: Zero-arg factory returning the Service-call coroutine.
            diff_preview: Optional structured change preview.
            principal: Caller identity (anonymous until Sprint 3).
        """
        if risk_level == "low":
            return await self.audit_call(tool_name, args, execute(), principal=principal)

        if self._approval_handler is None:
            # No handler mounted (e.g. read-only / AG-UI context) — refuse to
            # run medium/high-risk MCP tools rather than silently bypass HITL.
            # The AG-UI path never reaches here (its tools use
            # requires_approval, not execute_gated, for approvals).
            return {
                "error": {
                    "code": "NO_APPROVAL_HANDLER",
                    "message": (
                        f"tool {tool_name} requires risk_level={risk_level} approval "
                        "but no ApprovalHandler is mounted on this executor"
                    ),
                }
            }

        approval = ApprovalRequest(
            tool_name=tool_name,
            args=args,
            risk_level=risk_level,
            impact=impact,
            diff_preview=diff_preview,
            principal=principal,
        )
        approved = await self._approval_handler.request_approval(approval)
        if not approved:
            _log.info("tool.approval.denied tool=%s trace_id=%s", tool_name, get_trace_id())
            return {
                "status": "DENIED",
                "message": f"用户拒绝执行工具 {tool_name}",
            }
        _log.info("tool.approval.approved tool=%s trace_id=%s", tool_name, get_trace_id())
        return await self.audit_call(tool_name, args, execute(), principal=principal)

    async def execute_write(
        self,
        tool_name: str,
        args: dict[str, Any],
        risk_level: RiskLevel,
        impact: str,
        execute: Callable[[], Awaitable[Any]],
        *,
        diff_preview: str | None = None,
        principal: str = "anonymous",
    ) -> Any:
        """Protocol-adaptive write/action execution (shared by AG-UI + MCP).

        The single entry point the ``*_logic`` functions call. It branches on
        whether an ``ApprovalHandler`` is mounted:

        - **No handler (AG-UI path)**: the tool is declared
          ``requires_approval=True`` at the pydantic-ai layer, so HITL is
          already owned by pydantic-ai + AGUIAdapter interrupt/resume. By the
          time the tool body runs, the user has approved (or the call never
          reaches here). Execute immediately via ``audit_call``.
        - **Handler mounted (MCP path)**: delegate to ``execute_gated`` for
          synchronous ``ctx.elicit`` approval.

        This lets the ``*_logic`` functions stay protocol-agnostic — the same
        function serves both the AG-UI toolset (requires_approval) and the
        MCP toolset (MCPApprovalHandler), branching purely on whether the
        executor carries a handler.
        """
        if self._approval_handler is None:
            return await self.audit_call(tool_name, args, execute(), principal=principal)
        return await self.execute_gated(
            tool_name,
            args,
            risk_level,
            impact,
            execute,
            diff_preview=diff_preview,
            principal=principal,
        )


def _classify_error(exc: Exception) -> str:
    """Map an exception to a stable error code for the tool contract.

    Aligned with docs/architecture/ontology-tool-layer.md §五 error codes.
    If the exception carries an explicit ``code`` (OntologyError.code), use
    it verbatim — this lets the Service layer surface contract-specific
    codes like INVALID_AGGREGATION / INVALID_GROUP_BY / INVALID_FILTER
    rather than collapsing them all to ONTOLOGY_ERROR. Unknown exceptions
    fall back to INTERNAL_ERROR.
    """
    from ontology.core.exceptions import (
        ForbiddenError,
        NotFoundError,
        OntologyError,
        ValidationError,
    )

    # Explicit code on OntologyError wins (subclasses inherit the attr).
    if isinstance(exc, OntologyError):
        code: str | None = getattr(exc, "code", None)
        if code:
            return code
    if isinstance(exc, NotFoundError):
        return "OBJECT_NOT_FOUND"
    if isinstance(exc, ForbiddenError):
        return "PERMISSION_DENIED"
    if isinstance(exc, ValidationError):
        return "INVALID_PARAMETER"
    if isinstance(exc, OntologyError):
        return "ONTOLOGY_ERROR"
    return "INTERNAL_ERROR"
