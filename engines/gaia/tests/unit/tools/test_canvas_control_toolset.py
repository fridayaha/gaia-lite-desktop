"""Unit tests for canvas_control toolset (ADR-015 D2).

switch_view / color_by are state-only tools: they write CanvasSnapshot and
return a StateSnapshotEvent. No data is fetched. Validates:
  - switch_view updates state.view and emits snapshot
  - color_by updates state.color_by (and empty string clears it)
  - tools return StateSnapshotEvent (frontend re-renders)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn

from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets.canvas_control import build_canvas_control_toolset


def _ctx() -> RunContext[AppState]:
    return RunContext[AppState](
        deps=AppState(ontology="Mkt", executor=MagicMock(spec=ToolExecutor)),
        model=None,  # type: ignore[arg-type]
        usage=None,  # type: ignore[arg-type]
        prompt=None,  # type: ignore[arg-type]
        messages=[],
        tool_call_id="tc1",
        retry=0,
        run_step=0,
        tool_name="test",
    )


def _get_tool(toolset: Any, name: str) -> Any:
    return toolset.tools[name].function


class TestSwitchView:
    @pytest.mark.asyncio
    async def test_switches_view_and_emits_snapshot(self) -> None:
        ts = build_canvas_control_toolset()
        ctx = _ctx()
        assert ctx.deps.state.view == "graph"  # default

        result = await _get_tool(ts, "switch_view")(ctx, view="map")

        assert isinstance(result, ToolReturn)
        assert result.return_value["view"] == "map"
        assert result.return_value["canvas_updated"] is True
        # metadata 携带 STATE_SNAPSHOT 事件（前端渲染）
        assert isinstance(result.metadata, StateSnapshotEvent)
        assert result.metadata.type == EventType.STATE_SNAPSHOT
        assert ctx.deps.state.view == "map"
        # snapshot carries the canvas
        assert result.metadata.snapshot["canvas"]["view"] == "map"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("view", ["graph", "map", "split"])
    async def test_all_views(self, view: str) -> None:
        ts = build_canvas_control_toolset()
        ctx = _ctx()
        await _get_tool(ts, "switch_view")(ctx, view=view)
        assert ctx.deps.state.view == view


class TestColorBy:
    @pytest.mark.asyncio
    async def test_sets_color_by(self) -> None:
        ts = build_canvas_control_toolset()
        ctx = _ctx()
        assert ctx.deps.state.color_by is None

        result = await _get_tool(ts, "color_by")(ctx, property="riskLevel")

        assert isinstance(result, ToolReturn)
        assert result.return_value["color_by"] == "riskLevel"
        assert isinstance(result.metadata, StateSnapshotEvent)
        assert ctx.deps.state.color_by == "riskLevel"
        assert result.metadata.snapshot["canvas"]["color_by"] == "riskLevel"

    @pytest.mark.asyncio
    async def test_empty_string_clears_color(self) -> None:
        ts = build_canvas_control_toolset()
        ctx = _ctx()
        ctx.deps.state = ctx.deps.state.with_color_by("status")

        await _get_tool(ts, "color_by")(ctx, property="")

        assert ctx.deps.state.color_by is None

    @pytest.mark.asyncio
    async def test_preserves_other_state(self) -> None:
        """color_by 不应破坏已加载的对象（不可变更新）。"""
        ts = build_canvas_control_toolset()
        ctx = _ctx()
        # 模拟已有对象在画布上
        from ontology.core.schemas.canvas import CanvasObject

        ctx.deps.state = ctx.deps.state.with_objects(
            [CanvasObject(rid="v1", api_name="Lead")], query_summary="Lead (1)"
        )

        await _get_tool(ts, "color_by")(ctx, property="status")

        assert ctx.deps.state.color_by == "status"
        # objects preserved
        assert ctx.deps.state.object_count == 1
        assert ctx.deps.state.last_query_summary == "Lead (1)"
