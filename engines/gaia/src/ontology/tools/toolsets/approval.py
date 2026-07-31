"""Metadata-carrying approval toolset wrapper.

pydantic-ai 2.0's deferred-approval path has two modes:

1. A tool declared ``requires_approval=True`` gets ``ToolDefinition.kind =
   'unapproved'``. pydantic-ai collects these into
   ``DeferredToolRequests.approvals`` **without** calling ``call_tool``, so
   the tool's static ``metadata=`` never reaches
   ``DeferredToolRequests.metadata`` (and thus never reaches the AG-UI
   ``Interrupt.metadata`` the frontend batch-approval panel reads).

2. A tool whose ``call_tool`` raises ``ApprovalRequired(metadata=...)`` has
   that metadata recorded into ``DeferredToolRequests.metadata[tool_call_id]``
   — but this only fires when ``call_tool`` is actually invoked, i.e. when
   the tool is *not* statically marked ``requires_approval=True``.

This wrapper implements mode 2 in a way that also carries the tool's static
``metadata`` (notably ``risk_level``) onto the interrupt. Tools are
declared with ``metadata={"risk_level": "medium" | "high" | "unknown",
...}`` (NO ``requires_approval=True``); this wrapper raises
``ApprovalRequired(metadata=tool.tool_def.metadata)`` on the first call and
executes the tool body on the resume run (``ctx.tool_call_approved`` is
True). The net effect: the AG-UI interrupt carries ``risk_level`` so the
frontend can disable blanket-approve for high/unknown-risk items.

**HITL preview enrichment** (P0 optimisation, 2026-07-08): before raising
``ApprovalRequired``, the wrapper calls :func:`build_impact` to attach an
``impact_summary`` (plain-language "将创建对象类型..." instead of raw JSON)
and ``resolved_args`` (defaults applied — e.g. ``ontology="Marketing"``
instead of the LLM's ``""``) onto the metadata. The frontend
``BatchApprovalPanel`` reads ``metadata.impact_summary`` to render a
human-readable preview, per the HITL best practice "show the effect of the
action, not the JSON" (see ``impact_builder.py`` docstring for references).

Used by ``ontology.services.ai_agent`` to wrap the write/action toolsets on
the AG-UI path. The MCP path does not use this wrapper (MCP approvals are
synchronous via ``ctx.elicit``, not interrupt/resume).
"""

from __future__ import annotations

from typing import Any

from pydantic_ai._run_context import AgentDepsT, RunContext
from pydantic_ai.exceptions import ApprovalRequired
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai.toolsets.wrapper import WrapperToolset

from ontology.tools.toolsets.impact_builder import build_impact

__all__ = ["MetadataApprovalToolset"]


class MetadataApprovalToolset(WrapperToolset[AgentDepsT]):
    """Approval wrapper that forwards tool ``metadata`` + impact preview onto the interrupt.

    Wrap a toolset whose tools declare ``metadata={"risk_level": ...}``.
    When the model calls such a tool:

    - **First run** (``ctx.tool_call_approved`` is False): build an impact
      preview (``impact_summary`` + ``resolved_args``) via
      :func:`build_impact`, merge it onto the tool's static ``metadata``,
      and raise ``ApprovalRequired(metadata=...)``. pydantic-ai records this
      into ``DeferredToolRequests.metadata[tool_call_id]``, which
      ``AGUIAdapter`` forwards onto ``Interrupt.metadata`` for the frontend
      batch-approval panel. The panel reads ``risk_level`` to decide
      per-item vs blanket-approve, and ``impact_summary`` to render a
      human-readable preview instead of the raw JSON args.
    - **Resume run** (``ctx.tool_call_approved`` is True, after the user
      approved via AG-UI ``resume``): execute the tool body directly via
      ``super().call_tool``.

    Tools with no ``risk_level`` in their metadata pass through untouched
    (read-only tools need no approval). Tools without a registered impact
    builder (see ``impact_builder.py``) fall back to the static metadata
    only (no ``impact_summary``) — the frontend then renders the raw-args
    message as before.
    """

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDepsT],
        tool: ToolsetTool[AgentDepsT],
    ) -> Any:
        meta = dict(tool.tool_def.metadata or {})
        if not ctx.tool_call_approved and "risk_level" in meta:
            # Enrich the metadata with a human-readable impact preview +
            # the resolved (defaults-applied) args, so the frontend can show
            # "将创建对象类型 优惠券..." instead of the raw JSON with
            # ontology="". Falls back to no-op when no builder is registered
            # for this tool (the frontend then renders the raw message).
            preview = build_impact(name, tool_args, ctx)
            if preview is not None:
                meta["impact_summary"] = preview["impact_summary"]
                meta["resolved_args"] = preview["resolved_args"]
            raise ApprovalRequired(metadata=meta)
        return await super().call_tool(name, tool_args, ctx, tool)
