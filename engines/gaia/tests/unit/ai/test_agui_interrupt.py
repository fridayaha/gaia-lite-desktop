"""Spike/integration test: pydantic-ai 2.0 native interrupt/resume end-to-end.

Validates the core of ADR-010 v2 (AG-UI native interrupt/resume, see
docs/bugfix/hitl-batch-approval-pending-pydantic-ai.md §4):
  1. An Agent with a ``requires_approval=True`` tool, when the model calls
     that tool, ends the run with a ``DeferredToolRequests`` output whose
     ``approvals`` lists the pending tool call.
  2. ``DeferredToolRequests.metadata`` carries the tool's static
     ``metadata=`` (e.g. risk_level) keyed by tool_call_id — this is what
     AGUIAdapter forwards onto ``Interrupt.metadata`` for the frontend.
  3. Feeding ``DeferredToolResults(approvals={tool_call_id: ToolApproved})``
     back into a second run resumes execution: the tool body runs and the
     agent finishes with a normal (non-deferred) output.
  4. ``ToolDenied`` in the resume skips the tool body.

Uses ``TestModel`` (no real LLM). The tool body is a simple counter so we
can assert whether it ran.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)
from pydantic_ai.toolsets import FunctionToolset

from ontology.tools.toolsets.approval import MetadataApprovalToolset


def _build_agent() -> tuple[Agent[None, Any], list[str]]:
    """Build a minimal agent with one requires_approval tool.

    The toolset is wrapped in ``MetadataApprovalToolset`` (mirroring
    ``ai_agent.build_agent``) so the tool's static ``metadata=`` flows into
    ``DeferredToolRequests.metadata`` and onward to AG-UI
    ``Interrupt.metadata`` — the property the frontend batch-approval panel
    reads to decide per-item vs blanket-approve.

    Returns the agent + a shared ``calls`` list the tool appends to, so tests
    can assert whether the (approved) tool body actually ran.
    """
    calls: list[str] = []
    ts: FunctionToolset[None] = FunctionToolset()

    @ts.tool(
        metadata={"risk_level": "medium"},
    )
    async def create_thing(ctx: RunContext[None], name: str) -> dict[str, Any]:
        """Create a thing."""
        calls.append(name)
        return {"created": name}

    agent: Agent[None, Any] = Agent(
        TestModel(),
        deps_type=None,
        system_prompt="",
        toolsets=[MetadataApprovalToolset(ts)],
        output_type=[str, DeferredToolRequests],
        defer_model_check=True,
    )
    return agent, calls


@pytest.mark.asyncio
async def test_requires_approval_tool_defers_into_deferred_tool_requests() -> None:
    """Run 1: model calls the requires_approval tool → run ends with
    DeferredToolRequests.approvals containing the pending tool call, and
    metadata carries the tool's risk_level."""
    agent, calls = _build_agent()
    result = await agent.run("create a thing named widget")

    # The tool body must NOT have run — it's pending approval.
    assert calls == []

    # Output is a DeferredToolRequests with one pending approval.
    output = result.output
    assert isinstance(output, DeferredToolRequests)
    assert len(output.approvals) == 1
    pending = output.approvals[0]
    assert pending.tool_name == "create_thing"
    tool_call_id = pending.tool_call_id

    # metadata keyed by tool_call_id carries the static risk_level.
    assert tool_call_id in output.metadata
    assert output.metadata[tool_call_id]["risk_level"] == "medium"


@pytest.mark.asyncio
async def test_resume_with_tool_approved_runs_tool_body() -> None:
    """Run 2 (resume): feeding DeferredToolResults with ToolApproved for the
    pending tool_call_id makes the agent re-run and execute the tool body."""
    agent, calls = _build_agent()

    # Run 1 — collect the deferred request.
    run1 = await agent.run("create a thing named widget")
    output = run1.output
    assert isinstance(output, DeferredToolRequests)
    tool_call_id = output.approvals[0].tool_call_id
    assert calls == []

    # Run 2 — resume with approval. message_history carries the prior run.
    results = DeferredToolResults(approvals={tool_call_id: ToolApproved()})
    run2 = await agent.run(
        "resume",
        message_history=run1.all_messages(),
        deferred_tool_results=results,
    )

    # The tool body ran on resume (TestModel supplies the arg, not the
    # prompt text — assert it ran exactly once with the model-generated arg).
    assert len(calls) == 1
    # The second run finishes with a normal output (not DeferredToolRequests).
    assert not isinstance(run2.output, DeferredToolRequests)


@pytest.mark.asyncio
async def test_resume_with_tool_denied_skips_tool_body() -> None:
    """Run 2 (resume): ToolDenied skips the tool body — the agent finishes
    without executing the tool."""
    agent, calls = _build_agent()

    run1 = await agent.run("create a thing named widget")
    output = run1.output
    assert isinstance(output, DeferredToolRequests)
    tool_call_id = output.approvals[0].tool_call_id

    results = DeferredToolResults(approvals={tool_call_id: ToolDenied()})
    run2 = await agent.run(
        "resume",
        message_history=run1.all_messages(),
        deferred_tool_results=results,
    )

    # The tool body never ran.
    assert calls == []
    assert not isinstance(run2.output, DeferredToolRequests)


@pytest.mark.asyncio
async def test_build_results_approve_all_helper() -> None:
    """DeferredToolRequests.build_results(approve_all=True) approves every
    pending approval — the convenience path the frontend 'approve all' button
    maps to (batch approval)."""
    agent, calls = _build_agent()

    run1 = await agent.run("create a thing named widget")
    output = run1.output
    assert isinstance(output, DeferredToolRequests)

    results = output.build_results(approve_all=True)
    await agent.run(
        "resume",
        message_history=run1.all_messages(),
        deferred_tool_results=results,
    )

    assert len(calls) == 1
