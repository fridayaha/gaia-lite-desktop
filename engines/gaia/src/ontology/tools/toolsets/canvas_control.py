"""Canvas control tools (ADR-015 D2) — pure UI manipulation via shared state.

These tools let the Agent control the graph-explore canvas (switch view /
color by property) by writing ``CanvasSnapshot`` state. They perform **no
data query** — the frontend subscribes to ``state.canvas`` and re-renders.
The Agent calls them after a data tool (query_with_dataframe) to adjust
the visualization based on what it found.

Implementation note (ADR-015 D2): AG-UI's official split is frontend-defined
tools for UI actions (``RunAgentInput.tools``). pydantic-ai 2.0's
``AGUIAdapter`` natively supports this via ``_AGUIFrontendToolset`` (auto-
wraps ``RunAgentInput.tools`` into a backend-visible toolset). For the MVP
we implement these as **backend tools that only write state** (no data
fetch), which the frontend renders by subscribing to ``state.canvas`` —
this avoids the frontend ``TOOL_CALL_*`` lifecycle wiring for now. Both
approaches write ``CanvasSnapshot`` via ``StateSnapshotEvent`` so the
frontend subscription is identical. Migrating to true frontend-defined
tools is tracked as ADR-015 §后续工作.

Each tool returns a ``ToolReturn``: ``return_value`` is the confirmation
the Agent sees (ReAct observe), ``metadata`` carries the
``StateSnapshotEvent`` the frontend renders from (per pydantic-ai AG-UI
docs §Tools/Events).
"""

from __future__ import annotations

from typing import Any, Literal

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from ontology.tools.state import AppState


def _snapshot_event(canvas_state: Any) -> StateSnapshotEvent:
    """Build a STATE_SNAPSHOT event carrying the current canvas state."""
    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={"canvas": canvas_state.model_dump(mode="json")},
    )


def build_canvas_control_toolset() -> FunctionToolset[AppState]:
    """Build the canvas-control toolset (switch_view / color_by).

    These are state-only tools — they write ``CanvasSnapshot`` and return a
    ``StateSnapshotEvent``. The frontend subscribes to state.canvas and
    re-renders. No data is fetched; pair with query_with_dataframe for data.
    """
    ts: FunctionToolset[AppState] = FunctionToolset()

    @ts.tool
    async def switch_view(
        ctx: RunContext[AppState],
        view: Literal["graph", "map", "split"],
    ) -> StateSnapshotEvent:
        """Switch the graph-explore canvas to a different visualization view.

        Use after loading objects onto the canvas to change how they're
        rendered. The map view is appropriate when objects have GEOPOINT
        properties; the graph view shows relationship topology; split shows
        both side by side.

        Args:
            view: The view to switch to — "graph" (topology), "map"
                (geographic), or "split" (both side by side).

        Returns a STATE_SNAPSHOT event; the frontend re-renders the canvas.
        """
        new_canvas = ctx.deps.state.with_view(view)
        ctx.deps.state = new_canvas
        return ToolReturn(
            return_value={"view": view, "canvas_updated": True},
            metadata=_snapshot_event(new_canvas),
        )

    @ts.tool
    async def color_by(
        ctx: RunContext[AppState],
        property: str,
    ) -> StateSnapshotEvent:
        """Color canvas nodes by an object property (e.g. "riskLevel",
        "status", "lifecycle_stage").

        Use to highlight patterns in the loaded objects — e.g. color by
        risk level to spot high-risk clusters, or by status to see workflow
        distribution. Pass an empty string or call with property="" to clear
        coloring.

        Args:
            property: The property api_name to color by (e.g. "riskLevel").
                Use empty string to clear coloring.

        Returns a STATE_SNAPSHOT event; the frontend re-renders node colors.
        """
        prop = property or None
        new_canvas = ctx.deps.state.with_color_by(prop)
        ctx.deps.state = new_canvas
        return ToolReturn(
            return_value={"color_by": prop, "canvas_updated": True},
            metadata=_snapshot_event(new_canvas),
        )

    return ts
