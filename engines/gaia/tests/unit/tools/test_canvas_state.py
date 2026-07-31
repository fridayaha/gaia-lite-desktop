"""Unit tests for the AG-UI canvas shared state (ADR-015).

Validates:
  - ``CanvasSnapshot`` immutable updates (with_objects/with_view/with_color_by/
    with_expanded_link) — the state diff mechanism pydantic-ai's AG-UI adapter
    relies on.
  - ``AppState`` is a dataclass implementing ``StateHandler`` so
    ``dispatch_request`` can inject client state via ``dataclasses.replace``
    without clobbering ``executor`` / ``ontology``.
  - ``query_with_dataframe`` tool writes the result into ``ctx.deps.state``
    and returns a ``StateSnapshotEvent`` (the ADR-015 D2 "data tool drives
    canvas" mechanism). Crucially verifies the ReAct "observe" channel: when
    the query returns 0 objects, ``state.object_count == 0`` and
    ``last_query_summary`` reflects the empty result — this is what lets the
    Agent terminate gracefully instead of fabricating a multi-step analysis
    (ADR-015 D5, no if-rule).
"""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn

from ontology.core.schemas.canvas import CanvasObject, CanvasSnapshot
from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets.reasoning import build_reasoning_toolset


class TestCanvasSnapshotImmutableUpdates:
    """CanvasSnapshot 的不可变更新（pydantic-ai state diff 依赖）。"""

    def test_default_state(self) -> None:
        c = CanvasSnapshot()
        assert c.object_count == 0
        assert c.view == "graph"
        assert c.color_by is None
        assert c.expanded_links == []
        assert c.last_query_summary == ""

    def test_with_objects_replaces(self) -> None:
        c = CanvasSnapshot()
        objs = [CanvasObject(rid="v1", api_name="Order", title="O1")]
        c2 = c.with_objects(objs, query_summary="Order (1 个对象)")
        # immutable: original unchanged
        assert c.object_count == 0
        assert c2.object_count == 1
        assert c2.objects[0].rid == "v1"
        assert c2.last_query_summary == "Order (1 个对象)"

    def test_with_objects_append(self) -> None:
        c = CanvasSnapshot(objects=[CanvasObject(rid="v1", api_name="Order")])
        c2 = c.with_objects([CanvasObject(rid="v2", api_name="Order")], append=True)
        assert c2.object_count == 2
        assert [o.rid for o in c2.objects] == ["v1", "v2"]

    def test_with_view(self) -> None:
        c = CanvasSnapshot().with_view("map")
        assert c.view == "map"

    def test_with_color_by(self) -> None:
        c = CanvasSnapshot().with_color_by("riskLevel")
        assert c.color_by == "riskLevel"

    def test_with_expanded_link_dedupes(self) -> None:
        c = CanvasSnapshot().with_expanded_link("supplies")
        c2 = c.with_expanded_link("supplies")  # idempotent
        assert c2.expanded_links == ["supplies"]
        c3 = c2.with_expanded_link("produces")
        assert c3.expanded_links == ["supplies", "produces"]


class TestCanvasSnapshotSearchAroundAccumulation:
    """with_search_around 累积节点+边+去重（ADR-015 探索轨迹）。"""

    def test_accumulates_objects_and_edges(self) -> None:
        """searchAround 结果累积进画布，不覆盖既有节点/边。"""
        from ontology.core.schemas.canvas import CanvasEdge

        start = CanvasSnapshot().with_objects(
            [CanvasObject(rid="S000", api_name="Supplier")],
            query_summary="Supplier (1)",
        )
        materials = [CanvasObject(rid="M1", api_name="Material"), CanvasObject(rid="M2", api_name="Material")]
        edges = [
            CanvasEdge(source_rid="S000", target_rid="M1", link_type="supplies"),
            CanvasEdge(source_rid="S000", target_rid="M2", link_type="supplies"),
        ]
        step1 = start.with_search_around(materials, edges, link="supplies", query_summary="Material (2)")
        # S000 保留，M1/M2 累积
        assert {o.rid for o in step1.objects} == {"S000", "M1", "M2"}
        assert step1.object_count == 3
        # 边累积
        assert len(step1.edges) == 2
        assert {e.target_rid for e in step1.edges} == {"M1", "M2"}
        # expanded_links 记录
        assert "supplies" in step1.expanded_links
        assert step1.last_query_summary == "Material (2)"

    def test_dedupes_objects_and_edges_across_steps(self) -> None:
        """多步串联时节点和边去重，不重复。"""
        from ontology.core.schemas.canvas import CanvasEdge

        c = CanvasSnapshot().with_objects([CanvasObject(rid="S000", api_name="Supplier")])
        edges1 = [CanvasEdge(source_rid="S000", target_rid="M1", link_type="supplies")]
        c = c.with_search_around([CanvasObject(rid="M1", api_name="Material")], edges1, link="supplies")
        # 第二步：再次展开 supplies（M1 已在），不重复加
        c = c.with_search_around([CanvasObject(rid="M1", api_name="Material")], edges1, link="supplies")
        assert c.object_count == 2  # S000 + M1，不重复
        assert len(c.edges) == 1  # 同一条边不重复

    def test_pure_query_with_objects_does_not_add_edges(self) -> None:
        """纯查询（with_objects）不产生边——边仅 searchAround 专属。"""
        c = CanvasSnapshot().with_objects([CanvasObject(rid="O1", api_name="Order")])
        assert c.edges == []

    def test_object_soft_limit_drops_oldest(self) -> None:
        """节点超软上限时丢弃最早加入的，保轨迹连续性。"""
        c = CanvasSnapshot()
        for i in range(5):
            c = c.with_search_around(
                [CanvasObject(rid=f"v{i}", api_name="T")],
                [],
                link="l",
                max_objects=3,
            )
        # 只保留最后 3 个
        assert c.object_count == 3
        assert {o.rid for o in c.objects} == {"v2", "v3", "v4"}


class TestAppStateStateHandler:
    """AppState 必须满足 StateHandler：dataclass + state 字段 + replace 不破坏其他字段。"""

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(AppState)

    def test_has_state_field(self) -> None:
        fields = {f.name for f in dataclasses.fields(AppState)}
        assert "state" in fields
        assert "executor" in fields
        assert "ontology" in fields

    def test_satisfies_state_handler_protocol(self) -> None:
        from pydantic_ai.ui import StateHandler

        deps = AppState(ontology="Mkt")
        assert isinstance(deps, StateHandler)

    def test_replace_preserves_executor_and_ontology(self) -> None:
        """dispatch_request 用 replace(deps, state=前端state) 注入 state，
        executor / ontology 必须保留（不被前端 state 覆盖）。"""
        executor = MagicMock(spec=ToolExecutor)
        deps = AppState(state=CanvasSnapshot(), executor=executor, ontology="Marketing")
        new_canvas = CanvasSnapshot().with_objects([CanvasObject(rid="x", api_name="Lead")], query_summary="Lead (1)")
        deps2 = dataclasses.replace(deps, state=new_canvas)
        # state replaced
        assert deps2.state.object_count == 1
        # runtime deps preserved
        assert deps2.executor is executor
        assert deps2.ontology == "Marketing"

    def test_default_state_is_fresh_canvas(self) -> None:
        deps = AppState(ontology="Mkt")
        assert isinstance(deps.state, CanvasSnapshot)
        assert deps.state.object_count == 0


def _ctx(ontology: str = "", prompt: str = "") -> RunContext[AppState]:
    """Build a minimal RunContext with the given ontology + prompt in deps."""
    return RunContext[AppState](
        deps=AppState(ontology=ontology, executor=MagicMock(spec=ToolExecutor)),
        model=None,  # type: ignore[arg-type]
        usage=None,  # type: ignore[arg-type]
        prompt=None,  # type: ignore[arg-type]
        messages=[],
        tool_call_id="tc1",
        retry=0,
        run_step=0,
        tool_name="query_with_dataframe",
    )


def _get_tool(toolset: Any, name: str) -> Any:
    """Pull a registered tool callable off the FunctionToolset."""
    return toolset.tools[name].function


class TestQueryWithDataFrameWritesCanvasState:
    """query_with_dataframe 工具写 CanvasSnapshot + 返回 StateSnapshotEvent（ADR-015 D2）。"""

    @pytest.mark.asyncio
    async def test_writes_objects_to_state_and_emits_snapshot(self) -> None:
        """工具执行后 ctx.deps.state 应含返回的对象，且返回 StateSnapshotEvent。"""
        executor = MagicMock(spec=ToolExecutor)
        # 模拟 query_with_dataframe_logic 返回 2 个对象
        fake_result: dict[str, Any] = {
            "objects": [
                {"rid": "v1", "api_name": "Order", "props": {"title": "O1", "status": "open"}},
                {"rid": "v2", "api_name": "Order", "props": {"title": "O2", "status": "closed"}},
            ],
            "aggregates": [],
            "truncated": False,
            "stats": {"steps": 1, "engines_used": ["postgres"]},
        }
        ts = build_reasoning_toolset(executor)
        tool = _get_tool(ts, "query_with_dataframe")

        ctx = _ctx("SC")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ontology.tools.toolsets.reasoning.query_with_dataframe_logic",
                AsyncMock(return_value=fake_result),
            )
            result = await tool(ctx, ontology="", object_set_ir={"type": "objectType", "object_type": "Order"})

        # 返回 ToolReturn：return_value 给 Agent（数据），metadata 给前端（事件）
        assert isinstance(result, ToolReturn)
        assert result.return_value["objects_count"] == 2
        assert result.return_value["canvas_updated"] is True
        assert isinstance(result.metadata, StateSnapshotEvent)
        assert result.metadata.type == EventType.STATE_SNAPSHOT
        # state 已写入对象
        canvas = ctx.deps.state
        assert canvas.object_count == 2
        assert canvas.objects[0].rid == "v1"
        assert canvas.objects[0].api_name == "Order"
        assert canvas.last_query_summary == "Order (2 个对象)"
        # summary 截断到 5 个属性
        assert len(ctx.deps.state.objects[0].summary) <= 5

    @pytest.mark.asyncio
    async def test_zero_objects_sets_empty_state_for_react_termination(self) -> None:
        """0 对象时 state.object_count==0 + last_query_summary 反映空结果。
        这是 ADR-015 D5 的核心：Agent 读 state 看到 0 对象 → 自然终止，
        不需要空结果守卫 if 规则。"""
        executor = MagicMock(spec=ToolExecutor)
        fake_result: dict[str, Any] = {
            "objects": [],
            "aggregates": [],
            "truncated": False,
            "stats": {"steps": 1, "engines_used": ["postgres"]},
        }
        ts = build_reasoning_toolset(executor)
        tool = _get_tool(ts, "query_with_dataframe")

        ctx = _ctx("Marketing")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ontology.tools.toolsets.reasoning.query_with_dataframe_logic",
                AsyncMock(return_value=fake_result),
            )
            await tool(ctx, ontology="", object_set_ir={"type": "objectType", "object_type": "Dealership"})

        # ReAct observe 通道：Agent 下一轮读到这个就能判断"无法分析"并终止
        assert ctx.deps.state.object_count == 0
        assert "0 个对象" in ctx.deps.state.last_query_summary

    @pytest.mark.asyncio
    async def test_uses_deps_ontology_when_omitted(self) -> None:
        """工具 omit ontology 时用 ctx.deps.ontology（ADR-009 上下文作用域）。"""
        executor = MagicMock(spec=ToolExecutor)
        fake_result: dict[str, Any] = {"objects": [], "aggregates": [], "truncated": False}
        ts = build_reasoning_toolset(executor)
        tool = _get_tool(ts, "query_with_dataframe")

        ctx = _ctx("Marketing")

        called_ontology: list[str] = []

        async def fake_logic(_exec: Any, ontology: str, _ir: Any, cursor: str | None = None) -> dict[str, Any]:
            called_ontology.append(ontology)
            return fake_result

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ontology.tools.toolsets.reasoning.query_with_dataframe_logic",
                fake_logic,
            )
            await tool(ctx, ontology="", object_set_ir={"type": "objectType", "object_type": "Lead"})

        assert called_ontology == ["Marketing"]

    @pytest.mark.asyncio
    async def test_searcharound_accumulates_with_edges(self) -> None:
        """含 searchAround 的查询：累积节点 + 写边（探索轨迹），不覆盖既有画布。"""
        executor = MagicMock(spec=ToolExecutor)
        fake_result: dict[str, Any] = {
            "objects": [
                {"rid": "M1", "api_name": "Material", "props": {"title": "螺丝"}},
                {"rid": "M2", "api_name": "Material", "props": {"title": "螺母"}},
            ],
            "edges": [
                {"source_rid": "S000", "target_rid": "M1", "link_type": "supplies", "direction": "out"},
                {"source_rid": "S000", "target_rid": "M2", "link_type": "supplies", "direction": "out"},
            ],
            "aggregates": [],
            "truncated": False,
            "stats": {"steps": 2, "engines_used": ["neo4j"]},
        }
        ts = build_reasoning_toolset(executor)
        tool = _get_tool(ts, "query_with_dataframe")
        # 画布已有 S000（上一步纯查询加载的起始集）
        ctx = _ctx("SC")
        from ontology.core.schemas.canvas import CanvasObject

        ctx.deps.state = ctx.deps.state.with_objects(
            [CanvasObject(rid="S000", api_name="Supplier")], query_summary="Supplier (1)"
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ontology.tools.toolsets.reasoning.query_with_dataframe_logic",
                AsyncMock(return_value=fake_result),
            )
            await tool(
                ctx,
                ontology="",
                object_set_ir={
                    "type": "searchAround",
                    "link": "supplies",
                    "object_set": {"type": "static", "objects": ["S000"]},
                },
            )

        canvas = ctx.deps.state
        # 节点累积：S000 保留 + M1/M2 加入
        assert {o.rid for o in canvas.objects} == {"S000", "M1", "M2"}
        # 边写入画布
        assert len(canvas.edges) == 2
        assert {(e.source_rid, e.target_rid, e.link_type) for e in canvas.edges} == {
            ("S000", "M1", "supplies"),
            ("S000", "M2", "supplies"),
        }
        # expanded_links 记录
        assert "supplies" in canvas.expanded_links

    @pytest.mark.asyncio
    async def test_pure_query_overwrites_without_edges(self) -> None:
        """纯 objectType 查询：覆盖刷新画布，不产生边，不累积。"""
        executor = MagicMock(spec=ToolExecutor)
        fake_result: dict[str, Any] = {
            "objects": [
                {"rid": "O1", "api_name": "Order", "props": {"status": "open"}},
            ],
            "edges": [],  # 纯查询无边
            "aggregates": [],
            "truncated": False,
        }
        ts = build_reasoning_toolset(executor)
        tool = _get_tool(ts, "query_with_dataframe")
        ctx = _ctx("SC")
        from ontology.core.schemas.canvas import CanvasEdge, CanvasObject

        # 画布已有上一步探索的节点+边
        ctx.deps.state = ctx.deps.state.with_objects(
            [CanvasObject(rid="S000", api_name="Supplier")], query_summary="prev"
        )
        ctx.deps.state = ctx.deps.state.with_search_around(
            [CanvasObject(rid="M1", api_name="Material")],
            [CanvasEdge(source_rid="S000", target_rid="M1", link_type="supplies")],
            link="supplies",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ontology.tools.toolsets.reasoning.query_with_dataframe_logic",
                AsyncMock(return_value=fake_result),
            )
            await tool(ctx, ontology="", object_set_ir={"type": "objectType", "object_type": "Order"})

        canvas = ctx.deps.state
        # 覆盖：只剩 O1，S000/M1 被覆盖
        assert {o.rid for o in canvas.objects} == {"O1"}
        # 不产生边（之前的边也清空，因为 with_objects 覆盖式不保留 edges）
        assert canvas.edges == []

    @pytest.mark.asyncio
    async def test_nested_searcharound_in_filter_accumulates(self) -> None:
        """filter 包着 searchAround 也算探索（递归检测），累积+边。"""
        executor = MagicMock(spec=ToolExecutor)
        fake_result: dict[str, Any] = {
            "objects": [{"rid": "O1", "api_name": "Order", "props": {}}],
            "edges": [{"source_rid": "M1", "target_rid": "O1", "link_type": "used_in"}],
            "aggregates": [],
            "truncated": False,
        }
        ts = build_reasoning_toolset(executor)
        tool = _get_tool(ts, "query_with_dataframe")
        ctx = _ctx("SC")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "ontology.tools.toolsets.reasoning.query_with_dataframe_logic",
                AsyncMock(return_value=fake_result),
            )
            await tool(
                ctx,
                ontology="",
                object_set_ir={
                    "type": "filter",
                    "filters": [{"field": "status", "op": "exactMatch", "value": "open"}],
                    "object_set": {
                        "type": "searchAround",
                        "link": "used_in",
                        "object_set": {"type": "static", "objects": ["M1"]},
                    },
                },
            )

        # 嵌套 searchAround 被检测到 → 累积 + 边
        assert len(ctx.deps.state.edges) == 1
        assert "used_in" in ctx.deps.state.expanded_links
