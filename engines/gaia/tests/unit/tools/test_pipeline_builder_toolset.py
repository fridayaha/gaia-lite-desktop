"""Unit tests for pipeline_builder toolset (ADR-018 §14.5).

add_source / add_transform / add_sink / modify_node / remove_node / connect
are state-only tools: they write PipelineCanvasSnapshot and return a
StateSnapshotEvent. list_datasets / get_dataset_schema query the
DataSourceService (mocked here).

Validates:
  - add_* tools add a node + emit snapshot + set selection
  - modify_node merges config + preserves other fields
  - remove_node removes node + its edges
  - connect adds edge (and rejects missing/duplicate)
  - list_datasets / get_dataset_schema call the service (mocked)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn

from ontology.core.schemas.dataset import ColumnDef, DatasetSchema
from ontology.tools.executor import ToolExecutor
from ontology.tools.pipeline_state import PipelineAppState
from ontology.tools.toolsets.pipeline_builder import build_pipeline_builder_toolset


def _ctx(state: PipelineAppState | None = None) -> RunContext[PipelineAppState]:
    return RunContext[PipelineAppState](
        deps=state or PipelineAppState(pipeline_api_name="p1", executor=MagicMock(spec=ToolExecutor)),
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


def _snapshot(result: Any) -> dict[str, Any]:
    """Extract the pipeline_canvas snapshot from a ToolReturn's metadata."""
    assert isinstance(result, ToolReturn)
    assert isinstance(result.metadata, StateSnapshotEvent)
    assert result.metadata.type == EventType.STATE_SNAPSHOT
    return result.metadata.snapshot["pipeline_canvas"]


class TestAddSource:
    @pytest.mark.asyncio
    async def test_adds_node_and_emits_snapshot(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()

        result = await _get_tool(ts, "add_source")(
            ctx, node_id="s1", label="门店", dataset_api_name="dealership"
        )

        snap = _snapshot(result)
        assert len(snap["nodes"]) == 1
        assert snap["nodes"][0]["id"] == "s1"
        assert snap["nodes"][0]["type"] == "Source"
        assert snap["nodes"][0]["config"]["extra"]["dataset"] == "dealership"
        assert snap["selected_node_id"] == "s1"
        # state written back
        assert ctx.deps.state.nodes[0].id == "s1"

    @pytest.mark.asyncio
    async def test_position_offsets_with_existing_nodes(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        # seed one node
        await _get_tool(ts, "add_source")(ctx, node_id="s1", label="A", dataset_api_name="d1")

        await _get_tool(ts, "add_source")(ctx, node_id="s2", label="B", dataset_api_name="d2")

        positions = [n.position for n in ctx.deps.state.nodes]
        assert positions[1] != positions[0]
        assert positions[1]["x"] > positions[0]["x"]


class TestAddTransform:
    @pytest.mark.asyncio
    async def test_adds_transform_with_config(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()

        result = await _get_tool(ts, "add_transform")(
            ctx,
            node_id="f1",
            operator_type="Filter",
            label="过滤有效",
            config={"filter_conditions": [{"column": "status", "op": "eq", "value": "active"}]},
        )

        snap = _snapshot(result)
        assert snap["nodes"][0]["type"] == "Transform"
        assert snap["nodes"][0]["operator_type"] == "Filter"
        assert snap["nodes"][0]["config"]["filter_conditions"][0]["column"] == "status"

    @pytest.mark.asyncio
    async def test_config_defaults_to_empty_dict(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()

        await _get_tool(ts, "add_transform")(
            ctx, node_id="t1", operator_type="Sort", label="排序"
        )

        assert ctx.deps.state.nodes[0].config == {}


class TestAddSink:
    @pytest.mark.asyncio
    async def test_adds_sink_with_write_mode(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()

        result = await _get_tool(ts, "add_sink")(
            ctx, node_id="sk1", label="写入结果", dataset_api_name="out_table"
        )

        snap = _snapshot(result)
        assert snap["nodes"][0]["type"] == "Sink"
        assert snap["nodes"][0]["config"]["extra"]["write_mode"] == "FULL_REFRESH"
        assert snap["nodes"][0]["config"]["extra"]["dataset"] == "out_table"


class TestModifyNode:
    @pytest.mark.asyncio
    async def test_merges_config_and_preserves_others(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        await _get_tool(ts, "add_source")(
            ctx, node_id="s1", label="A", dataset_api_name="d1"
        )

        result = await _get_tool(ts, "modify_node")(
            ctx, node_id="s1", config={"dataset_api_name": "d2", "extra_key": "v"}
        )

        snap = _snapshot(result)
        # original label preserved, config merged
        assert snap["nodes"][0]["label"] == "A"
        assert snap["nodes"][0]["config"]["dataset_api_name"] == "d2"
        assert snap["nodes"][0]["config"]["extra_key"] == "v"

    @pytest.mark.asyncio
    async def test_updates_label(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        await _get_tool(ts, "add_source")(
            ctx, node_id="s1", label="A", dataset_api_name="d1"
        )

        await _get_tool(ts, "modify_node")(ctx, node_id="s1", label="新名")

        assert ctx.deps.state.nodes[0].label == "新名"

    @pytest.mark.asyncio
    async def test_missing_node_returns_error(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()

        result = await _get_tool(ts, "modify_node")(ctx, node_id="ghost", config={"a": 1})

        assert result.return_value["error"].startswith("node not found")


class TestRemoveNode:
    @pytest.mark.asyncio
    async def test_removes_node_and_connected_edges(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        await _get_tool(ts, "add_source")(ctx, node_id="s1", label="A", dataset_api_name="d1")
        await _get_tool(ts, "add_transform")(ctx, node_id="t1", operator_type="Filter", label="F")
        await _get_tool(ts, "connect")(ctx, source_node_id="s1", target_node_id="t1")
        assert len(ctx.deps.state.edges) == 1

        result = await _get_tool(ts, "remove_node")(ctx, node_id="s1")

        snap = _snapshot(result)
        assert len(snap["nodes"]) == 1
        assert snap["nodes"][0]["id"] == "t1"
        # edge to removed node also gone
        assert len(snap["edges"]) == 0
        assert snap["selected_node_id"] is None


class TestConnect:
    @pytest.mark.asyncio
    async def test_connects_two_nodes(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        await _get_tool(ts, "add_source")(ctx, node_id="s1", label="A", dataset_api_name="d1")
        await _get_tool(ts, "add_transform")(ctx, node_id="t1", operator_type="Filter", label="F")

        result = await _get_tool(ts, "connect")(
            ctx, source_node_id="s1", target_node_id="t1"
        )

        snap = _snapshot(result)
        assert len(snap["edges"]) == 1
        assert snap["edges"][0]["source_id"] == "s1"
        assert snap["edges"][0]["target_id"] == "t1"
        assert result.return_value["connected"] == "s1->t1"

    @pytest.mark.asyncio
    async def test_rejects_missing_source(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        await _get_tool(ts, "add_source")(ctx, node_id="s1", label="A", dataset_api_name="d1")

        result = await _get_tool(ts, "connect")(
            ctx, source_node_id="ghost", target_node_id="s1"
        )

        assert "error" in result.return_value

    @pytest.mark.asyncio
    async def test_rejects_duplicate_edge(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        await _get_tool(ts, "add_source")(ctx, node_id="s1", label="A", dataset_api_name="d1")
        await _get_tool(ts, "add_transform")(ctx, node_id="t1", operator_type="Filter", label="F")
        await _get_tool(ts, "connect")(ctx, source_node_id="s1", target_node_id="t1")

        result = await _get_tool(ts, "connect")(
            ctx, source_node_id="s1", target_node_id="t1"
        )

        assert result.return_value.get("exists") == "s1->t1"
        assert len(ctx.deps.state.edges) == 1


class TestListDatasets:
    @pytest.mark.asyncio
    async def test_calls_datasource_service(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        mock_svc = MagicMock()
        mock_ds = MagicMock()
        mock_ds.api_name = "orders"
        mock_ds.display_name = "订单"
        mock_ds.storage_location = "s3://bucket/orders"
        mock_schema = MagicMock()
        mock_schema.columns = [ColumnDef(name="id", type="string")]
        mock_ds.schema_ = mock_schema
        mock_svc.list_datasets = AsyncMock(return_value=[mock_ds])

        with patch(
            "ontology.tools.toolsets.pipeline_builder.container"
        ) as mock_container:
            mock_container.datasource_service = mock_svc
            result = await _get_tool(ts, "list_datasets")(ctx)

        assert result["datasets"][0]["api_name"] == "orders"
        assert result["datasets"][0]["column_count"] == 1


class TestGetDatasetSchema:
    @pytest.mark.asyncio
    async def test_returns_columns(self) -> None:
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        mock_svc = MagicMock()
        mock_svc.get_dataset_schema = AsyncMock(
            return_value=DatasetSchema(
                columns=[ColumnDef(name="id", type="string", nullable=False)]
            )
        )

        with patch(
            "ontology.tools.toolsets.pipeline_builder.container"
        ) as mock_container:
            mock_container.datasource_service = mock_svc
            result = await _get_tool(ts, "get_dataset_schema")(
                ctx, dataset_api_name="orders"
            )

        assert result["dataset"] == "orders"
        assert result["columns"][0]["name"] == "id"
        assert result["columns"][0]["nullable"] is False

    @pytest.mark.asyncio
    async def test_surfaces_error_when_dataset_missing(self) -> None:
        """When the dataset is not found, the tool must return an error dict
        instead of raising — so the LLM can recover (e.g. call list_datasets)."""
        ts = build_pipeline_builder_toolset()
        ctx = _ctx()
        mock_svc = MagicMock()
        mock_svc.get_dataset_schema = AsyncMock(
            side_effect=RuntimeError("Dataset not found: orders_raw")
        )

        with patch(
            "ontology.tools.toolsets.pipeline_builder.container"
        ) as mock_container:
            mock_container.datasource_service = mock_svc
            result = await _get_tool(ts, "get_dataset_schema")(
                ctx, dataset_api_name="orders_raw"
            )

        assert result["dataset"] == "orders_raw"
        assert "error" in result
        assert "not found" in result["error"]
        assert result["hint"] == "call list_datasets to see available api_names"
