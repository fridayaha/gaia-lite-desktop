"""AG-UI shared state — the request-scoped deps passed to the Agent.

``AppState`` lives in the ``ontology.tools`` package (not ``ai_agent``)
to avoid a circular import: write/action toolsets need to reference it in
their ``RunContext[AppState]`` type annotation, and ``ai_agent`` imports
both the toolsets and ``AppState`` from here.

ADR-015 (2026-07-04): ``AppState`` is now a **dataclass implementing the
``StateHandler`` protocol** — it carries a ``state: CanvasSnapshot`` field
that pydantic-ai's AG-UI adapter syncs bidirectionally with the frontend
(``dispatch_request`` uses ``dataclasses.replace`` to inject the client-sent
state into ``state``, leaving ``executor`` / ``ontology`` / ``injected_schema``
untouched). This is the "Agent drives canvas via shared state" mechanism:
tools write ``CanvasSnapshot`` by returning ``StateSnapshotEvent``; the
frontend subscribes to ``state.canvas`` and re-renders the canvas; the Agent
reads ``ctx.deps.state`` each turn (ReAct's "current state" channel).

Sprint 2 (ADR-010): carries ``thread_id`` + ``executor`` so write/action
tools (which take ``RunContext[AppState]``) can reach the request-scoped
ToolExecutor for Service access + audit. HITL on the AG-UI path is handled
by pydantic-ai's ``requires_approval`` + AGUIAdapter interrupt/resume, not
by the executor (the executor's approval handler is MCP-only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ontology.core.schemas.canvas import CanvasSnapshot

if TYPE_CHECKING:
    from ontology.tools.executor import ToolExecutor


@dataclass
class AppState:
    """AG-UI run deps + shared state (implements ``StateHandler``).

    The ``state`` field is the AG-UI shared state (``CanvasSnapshot``) —
    pydantic-ai's ``dispatch_request`` replaces it per-run from the
    client-sent state, so it MUST be the field named ``state`` and the
    class MUST be a dataclass (per the ``StateHandler`` protocol). Other
    fields (``executor`` / ``ontology`` / ``injected_schema``) are
    request-scoped runtime deps that survive the state replacement.

    Serialized to AG-UI ``STATE_SNAPSHOT`` / ``STATE_DELTA`` and mirrored
    on the frontend as ``thread.state``. Field names are snake_case; the
    frontend TS type MUST use the same snake_case names.

    Fields:
        state: AG-UI shared state (``CanvasSnapshot``). Tools write it by
            returning ``StateSnapshotEvent``; the Agent reads it each turn
            via ``ctx.deps.state`` (e.g. ``state.object_count == 0`` →
            terminate gracefully, ADR-015 D5).
        thread_id: AG-UI thread id (from RunAgentInput). Used for audit
            logging context. Empty outside AG-UI.
        executor: Request-scoped ToolExecutor (for Service access + audit).
            Set by fresh_deps() per run; write/action tools read it via
            ctx.deps.executor. The AG-UI path mounts no approval handler
            (HITL is owned by pydantic-ai requires_approval + AGUIAdapter);
            the MCP path mounts an MCPApprovalHandler. None in read-only
            contexts (those use a module-level executor).
        ontology: The ontology api_name the user currently has open in the
            Web UI (AG-UI path). Empty for MCP / other paths. Read-only
            toolsets use it to default the ``ontology`` arg of tools.
        injected_schema: TextQL Step 3 deterministic schema-injection block
            (ADR-012). Populated by /ai/agent before the agent runs.
        recent_calls: Reserved for a future state-panel feature.
    """

    # NOTE: `state` MUST be the field named `state` and this class MUST be a
    # dataclass for the StateHandler protocol (pydantic-ai dispatch_request
    # uses dataclasses.replace to inject client state here, preserving the
    # other fields). Default to a fresh CanvasSnapshot so non-AG-UI paths
    # (MCP / direct Agent.run) still work without explicit state.
    state: CanvasSnapshot = field(default_factory=CanvasSnapshot)
    thread_id: str = ""
    executor: ToolExecutor | None = None
    ontology: str = ""
    injected_schema: str = ""
    recent_calls: list[dict[str, Any]] = field(default_factory=list)
