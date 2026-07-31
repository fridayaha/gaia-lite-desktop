"""Unit tests for ToolExecutor governance (ADR-010, pydantic-ai 2.0 native HITL).

Validates:
  - audit_call wraps coroutines with start/ok/error logging and normalizes
    exceptions to the {"error": {...}} envelope.
  - execute_gated (MCP path): low risk executes immediately; medium/high
    delegate to the ApprovalHandler (synchronous bool elicit); no handler
    mounted → refuse (no silent bypass); denied → DENIED marker.
  - execute_write (protocol-adaptive): no handler (AG-UI path) executes
    immediately (HITL owned by pydantic-ai requires_approval); handler
    mounted (MCP path) delegates to execute_gated.

The AG-UI interrupt/resume lifecycle (DeferredToolRequests → AG-UI
interrupt → resume → DeferredToolResults) is NOT tested here — it is owned
by pydantic-ai + AGUIAdapter and covered by the spike in
test_agui_interrupt.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology.config.container import Container
from ontology.tools.executor import ApprovalHandler, ApprovalRequest, ToolExecutor


class _BoolHandler:
    """Simulates MCP elicitation: returns a fixed bool synchronously."""

    def __init__(self, decision: bool) -> None:
        self.decision = decision
        self.received: list[ApprovalRequest] = []

    async def request_approval(self, approval: ApprovalRequest) -> bool:
        self.received.append(approval)
        return self.decision


@pytest.fixture
def executor() -> ToolExecutor:
    """Bare executor with no handler (read-only / AG-UI context)."""
    return ToolExecutor(Container())


# ── audit_call ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_call_returns_result(executor: ToolExecutor) -> None:
    async def _do() -> Any:
        return {"ok": True}

    result = await executor.audit_call("some_tool", {"x": 1}, _do())
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_audit_call_normalizes_exception_to_error_envelope(executor: ToolExecutor) -> None:
    async def _do() -> Any:
        raise RuntimeError("boom")

    result = await executor.audit_call("some_tool", {"x": 1}, _do())
    assert result == {"error": {"code": "INTERNAL_ERROR", "message": "boom"}}


# ── execute_gated (MCP path) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_low_risk_skips_approval_and_executes(executor: ToolExecutor) -> None:
    """low risk_level bypasses the handler entirely and runs immediately."""
    called: list[str] = []

    async def _do() -> Any:
        called.append("executed")
        return {"ok": True}

    result = await executor.execute_gated("some_read_tool", {"x": 1}, "low", "impact", _do)
    assert result == {"ok": True}
    assert called == ["executed"]


@pytest.mark.asyncio
async def test_medium_risk_without_handler_refuses(executor: ToolExecutor) -> None:
    """A medium-risk MCP call with no ApprovalHandler mounted returns an
    error rather than silently bypassing HITL."""

    async def _do() -> Any:
        return {"ok": True}

    result = await executor.execute_gated("define_object_type", {}, "medium", "impact", _do)
    assert result["error"]["code"] == "NO_APPROVAL_HANDLER"


@pytest.mark.asyncio
async def test_high_risk_without_handler_refuses(executor: ToolExecutor) -> None:
    async def _do() -> Any:
        return {"ok": True}

    result = await executor.execute_gated("invoke_action", {}, "high", "impact", _do)
    assert result["error"]["code"] == "NO_APPROVAL_HANDLER"


@pytest.mark.asyncio
async def test_medium_risk_mcp_approved_executes() -> None:
    handler = _BoolHandler(decision=True)
    ex = ToolExecutor(Container(), approval_handler=handler)
    called: list[str] = []

    async def _do() -> Any:
        called.append("executed")
        return {"created": True}

    result = await ex.execute_gated("define_object_type", {"api_name": "x"}, "medium", "impact", _do)
    assert result == {"created": True}
    assert called == ["executed"]
    assert len(handler.received) == 1
    assert handler.received[0].risk_level == "medium"


@pytest.mark.asyncio
async def test_high_risk_mcp_denied_does_not_execute() -> None:
    handler = _BoolHandler(decision=False)
    ex = ToolExecutor(Container(), approval_handler=handler)
    called: list[str] = []

    async def _do() -> Any:
        called.append("executed")
        return {"created": True}

    result = await ex.execute_gated("invoke_action", {"action_type": "cancel"}, "high", "impact", _do)
    assert result["status"] == "DENIED"
    assert called == []


@pytest.mark.asyncio
async def test_high_risk_mcp_approved_executes() -> None:
    handler = _BoolHandler(decision=True)
    ex = ToolExecutor(Container(), approval_handler=handler)

    async def _do() -> Any:
        return {"created": True}

    result = await ex.execute_gated("invoke_action", {}, "high", "impact", _do)
    assert result == {"created": True}


# ── execute_write (protocol-adaptive) ────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_write_without_handler_executes_immediately(executor: ToolExecutor) -> None:
    """AG-UI path (no handler): execute_write runs immediately — HITL is
    owned by pydantic-ai requires_approval + AGUIAdapter interrupt/resume,
    so by the time the tool body runs the user has already approved."""
    called: list[str] = []

    async def _do() -> Any:
        called.append("executed")
        return {"created": True}

    result = await executor.execute_write("define_object_type", {}, "medium", "impact", _do)
    assert result == {"created": True}
    assert called == ["executed"]


@pytest.mark.asyncio
async def test_execute_write_with_handler_delegates_to_gated() -> None:
    """MCP path (handler mounted): execute_write delegates to execute_gated
    for synchronous ctx.elicit approval."""
    handler = _BoolHandler(decision=False)
    ex = ToolExecutor(Container(), approval_handler=handler)
    called: list[str] = []

    async def _do() -> Any:
        called.append("executed")
        return {"created": True}

    result = await ex.execute_write("define_object_type", {}, "medium", "impact", _do)
    assert result["status"] == "DENIED"
    assert called == []
    assert len(handler.received) == 1


@pytest.mark.asyncio
async def test_execute_write_low_risk_with_handler_still_executes() -> None:
    """low risk via execute_write executes immediately even with a handler
    (low risk never gates, regardless of path)."""
    handler = _BoolHandler(decision=False)  # would deny, but low risk skips
    ex = ToolExecutor(Container(), approval_handler=handler)

    async def _do() -> Any:
        return {"ok": True}

    result = await ex.execute_write("some_read_tool", {}, "low", "impact", _do)
    assert result == {"ok": True}
    assert handler.received == []  # handler never consulted


# ── ApprovalRequest is a data carrier for the MCP handler ────────────────


def test_approval_request_carries_fields() -> None:
    req = ApprovalRequest(
        tool_name="invoke_action",
        args={"a": 1},
        risk_level="high",
        impact="将执行 cancel_order",
        diff_preview="diff",
    )
    assert req.tool_name == "invoke_action"
    assert req.risk_level == "high"
    assert req.impact == "将执行 cancel_order"
    assert req.diff_preview == "diff"
    assert req.principal == "anonymous"


def test_approval_handler_is_protocol() -> None:
    """ApprovalHandler is a Protocol; _BoolHandler satisfies it structurally."""
    h: ApprovalHandler = _BoolHandler(True)
    assert h is not None
