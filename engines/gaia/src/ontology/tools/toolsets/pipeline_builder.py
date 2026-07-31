"""Pipeline builder toolset (ADR-018 §14.5) — AI 操纵管道画布的工具。

对标图探索的 ``canvas_control`` toolset（ADR-015），但操作的是管道 IR
画布（nodes/edges）。Agent 通过这些工具增删改画布节点和连接，每次操作
返回 ``StateSnapshotEvent``，前端订阅 ``state.pipeline_canvas`` 重绘。

8 个工具：
- list_datasets / get_dataset_schema（查询可用数据）
- add_source / add_transform / add_sink（添加节点）
- modify_node / remove_node（编辑节点）
- connect（连接节点）

设计原则：
- 工具只操作 AG-UI shared state（``ctx.deps.state``），不直接落库——
  落库是前端「保存」按钮的职责（走 ``/api/v1/pipelines`` REST）。
- 节点位置由工具自动计算（基于现有节点数偏移），AI 不需要关心布局；
  前端 ELK 会在结构变化时自动重排。
- ``config`` 参数为宽松 dict，AI 传什么存什么（后端 SchemaInferenceEngine
  在保存/校验时验证）。
"""

from __future__ import annotations

from typing import Any

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from ontology.config.container import container
from ontology.core.schemas.pipeline_canvas import (
    PipelineCanvasEdge,
    PipelineCanvasNode,
    PipelineCanvasSnapshot,
)
from ontology.tools.pipeline_state import PipelineAppState


def _snapshot_event(canvas: PipelineCanvasSnapshot) -> StateSnapshotEvent:
    """Build a STATE_SNAPSHOT event carrying the current pipeline canvas state."""
    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={"pipeline_canvas": canvas.model_dump(mode="json")},
    )


def _next_position(canvas: PipelineCanvasSnapshot) -> dict[str, float]:
    """Compute a position for a new node (simple diagonal offset).

    The frontend ELK layout will re-arrange on structural change, so this
    only needs to avoid stacking all new nodes at (0,0).
    """
    n = len(canvas.nodes)
    return {"x": float(n * 40), "y": float(n * 40)}


def _replace_state(ctx: RunContext[PipelineAppState], canvas: PipelineCanvasSnapshot) -> None:
    """Write the new canvas back into deps state (in-place field update)."""
    ctx.deps.state = canvas


def build_pipeline_builder_toolset() -> FunctionToolset[PipelineAppState]:
    """Build the pipeline-builder toolset (8 tools).

    Each tool writes ``PipelineCanvasSnapshot`` and returns a
    ``StateSnapshotEvent``. The frontend subscribes to
    ``state.pipeline_canvas`` and re-renders the canvas.
    """
    ts: FunctionToolset[PipelineAppState] = FunctionToolset()

    # ── 查询工具 ──

    @ts.tool
    async def list_datasets(ctx: RunContext[PipelineAppState]) -> dict[str, Any]:
        """List available datasets (Iceberg tables) that can be used as pipeline sources.

        Returns a list of datasets with their api_name, storage location, and
        column count. Use this before add_source to pick the right dataset.

        No canvas change — this is a read-only query.
        """
        svc = container.datasource_service
        datasets = await svc.list_datasets()
        return {
            "datasets": [
                {
                    "api_name": d.api_name,
                    "display_name": getattr(d, "display_name", d.api_name),
                    "storage_location": d.storage_location,
                    "column_count": len(getattr(d, "schema_", None).columns) if getattr(d, "schema_", None) else 0,
                }
                for d in datasets
            ]
        }

    @ts.tool
    async def get_dataset_schema(
        ctx: RunContext[PipelineAppState],
        dataset_api_name: str,
    ) -> dict[str, Any]:
        """Get the column schema of a dataset (Iceberg table).

        Use after list_datasets to inspect a dataset's columns before adding
        it as a source, or to decide which columns to select/filter/join on.

        Args:
            dataset_api_name: The dataset's api_name (from list_datasets).

        No canvas change — read-only query.
        """
        svc = container.datasource_service
        try:
            schema = await svc.get_dataset_schema(dataset_api_name)
        except Exception as exc:  # noqa: BLE001 — surface error to LLM, don't crash the run
            return {
                "dataset": dataset_api_name,
                "error": f"dataset schema not available: {exc}",
                "hint": "call list_datasets to see available api_names",
            }
        return {
            "dataset": dataset_api_name,
            "columns": [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in schema.columns],
        }

    # ── 节点添加工具 ──

    @ts.tool
    async def add_source(
        ctx: RunContext[PipelineAppState],
        node_id: str,
        label: str,
        dataset_api_name: str,
    ) -> StateSnapshotEvent:
        """Add a Source node that reads from a dataset (Iceberg table).

        A Source is the pipeline entry point — it loads data from a dataset
        for downstream transforms. One pipeline typically has 1-2 sources.

        Args:
            node_id: A unique id for the new node (e.g. "source_orders").
                Use descriptive ids, not random ones.
            label: Human-readable label (e.g. "订单表").
            dataset_api_name: The dataset to read (from list_datasets).

        Returns a STATE_SNAPSHOT event; the frontend adds the node to the canvas.
        """
        canvas = ctx.deps.state
        node = PipelineCanvasNode(
            id=node_id,
            type="Source",
            operator_type="Source",
            label=label,
            config={"extra": {"dataset": dataset_api_name}},
            position=_next_position(canvas),
        )
        new_canvas = canvas.with_nodes([*canvas.nodes, node]).with_selection(node_id)
        _replace_state(ctx, new_canvas)
        return ToolReturn(
            return_value={"added": node_id, "type": "Source"},
            metadata=_snapshot_event(new_canvas),
        )

    @ts.tool
    async def add_transform(
        ctx: RunContext[PipelineAppState],
        node_id: str,
        operator_type: str,
        label: str,
        config: dict[str, Any] | None = None,
    ) -> StateSnapshotEvent:
        """Add a Transform node that processes upstream data.

        Common operator_type values: "Filter", "Select", "Join", "Aggregate",
        "Rename", "TypeCast", "Union", "Expression", "Deduplicate", "Sort".

        The config dict holds operator-specific parameters, e.g.:
        - Filter: {"filter_conditions": [{"column": "...", "op": "eq", "value": "..."}]}
        - Select: {"columns": ["col1", "col2"]}
        - Join: {"join_type": "INNER", "join_conditions": [{"left_column": "...", "right_column": "..."}]}
        - Aggregate: {"group_by": ["col1"], "aggregations": [{"field": "...", "function": "sum", "alias": "..."}]}
        - Sort: {"sort_keys": [{"column": "...", "direction": "asc"}]}

        Args:
            node_id: Unique id (e.g. "filter_active").
            operator_type: One of the transform operator types.
            label: Human-readable label (e.g. "过滤有效订单").
            config: Operator-specific config (see above). Omit for operators
                that need no config.

        Returns a STATE_SNAPSHOT event; the frontend adds the node (use
        ``connect`` to wire it to upstream/downstream nodes).
        """
        canvas = ctx.deps.state
        node = PipelineCanvasNode(
            id=node_id,
            type="Transform",
            operator_type=operator_type,
            label=label,
            config=config or {},
            position=_next_position(canvas),
        )
        new_canvas = canvas.with_nodes([*canvas.nodes, node]).with_selection(node_id)
        _replace_state(ctx, new_canvas)
        return ToolReturn(
            return_value={"added": node_id, "type": "Transform", "operator_type": operator_type},
            metadata=_snapshot_event(new_canvas),
        )

    @ts.tool
    async def add_sink(
        ctx: RunContext[PipelineAppState],
        node_id: str,
        label: str,
        dataset_api_name: str,
        write_mode: str = "FULL_REFRESH",
    ) -> StateSnapshotEvent:
        """Add a Sink node that writes pipeline output to a dataset.

        A Sink is the pipeline exit point. Typically one per pipeline.

        Args:
            node_id: Unique id (e.g. "sink_result").
            label: Human-readable label (e.g. "写入结果表").
            dataset_api_name: The target dataset to write to.
            write_mode: "FULL_REFRESH" (replace all rows) or "APPEND" (add rows).
                Default FULL_REFRESH.

        Returns a STATE_SNAPSHOT event; the frontend adds the node.
        """
        canvas = ctx.deps.state
        node = PipelineCanvasNode(
            id=node_id,
            type="Sink",
            operator_type="Sink",
            label=label,
            config={"extra": {"dataset": dataset_api_name, "write_mode": write_mode}},
            position=_next_position(canvas),
        )
        new_canvas = canvas.with_nodes([*canvas.nodes, node]).with_selection(node_id)
        _replace_state(ctx, new_canvas)
        return ToolReturn(
            return_value={"added": node_id, "type": "Sink"},
            metadata=_snapshot_event(new_canvas),
        )

    # ── 节点编辑工具 ──

    @ts.tool
    async def modify_node(
        ctx: RunContext[PipelineAppState],
        node_id: str,
        config: dict[str, Any] | None = None,
        label: str | None = None,
    ) -> StateSnapshotEvent:
        """Modify an existing node's config and/or label.

        Use to adjust a node's parameters after adding it (e.g. change a
        Filter's condition, add a column to Select). At least one of
        ``config`` or ``label`` must be provided.

        Args:
            node_id: The id of the node to modify.
            config: New config to merge into the node (existing keys not in
                this dict are preserved). Omit to keep config unchanged.
            label: New label. Omit to keep the existing label.

        Returns a STATE_SNAPSHOT event; the frontend updates the node.
        """
        canvas = ctx.deps.state
        found = False
        new_nodes: list[PipelineCanvasNode] = []
        for n in canvas.nodes:
            if n.id == node_id:
                found = True
                merged_config = {**n.config, **(config or {})}
                new_nodes.append(
                    n.model_copy(
                        update={
                            "config": merged_config,
                            **({"label": label} if label is not None else {}),
                        }
                    )
                )
            else:
                new_nodes.append(n)
        if not found:
            return ToolReturn(
                return_value={"error": f"node not found: {node_id}"},
                metadata=_snapshot_event(canvas),
            )
        new_canvas = canvas.with_nodes(new_nodes).with_selection(node_id)
        _replace_state(ctx, new_canvas)
        return ToolReturn(
            return_value={"modified": node_id},
            metadata=_snapshot_event(new_canvas),
        )

    @ts.tool
    async def remove_node(
        ctx: RunContext[PipelineAppState],
        node_id: str,
    ) -> StateSnapshotEvent:
        """Remove a node and all its connections from the canvas.

        Args:
            node_id: The id of the node to remove.

        Returns a STATE_SNAPSHOT event; the frontend removes the node and
        any edges connected to it.
        """
        canvas = ctx.deps.state
        new_nodes = [n for n in canvas.nodes if n.id != node_id]
        new_edges = [e for e in canvas.edges if e.source_id != node_id and e.target_id != node_id]
        new_canvas = canvas.with_nodes(new_nodes).with_edges(new_edges).with_selection(None)
        _replace_state(ctx, new_canvas)
        return ToolReturn(
            return_value={"removed": node_id},
            metadata=_snapshot_event(new_canvas),
        )

    @ts.tool
    async def connect(
        ctx: RunContext[PipelineAppState],
        source_node_id: str,
        target_node_id: str,
        edge_id: str | None = None,
    ) -> StateSnapshotEvent:
        """Connect two nodes (data flows from source to target).

        A Source connects to a Transform; a Transform connects to another
        Transform or a Sink. Joins take two inputs (connect twice).

        Args:
            source_node_id: The upstream node id (data flows out).
            target_node_id: The downstream node id (data flows in).
            edge_id: Optional edge id. If omitted, generated from node ids.

        Returns a STATE_SNAPSHOT event; the frontend draws the connection.
        """
        canvas = ctx.deps.state
        # Validate both nodes exist
        node_ids = {n.id for n in canvas.nodes}
        if source_node_id not in node_ids:
            return ToolReturn(
                return_value={"error": f"source node not found: {source_node_id}"},
                metadata=_snapshot_event(canvas),
            )
        if target_node_id not in node_ids:
            return ToolReturn(
                return_value={"error": f"target node not found: {target_node_id}"},
                metadata=_snapshot_event(canvas),
            )
        eid = edge_id or f"{source_node_id}->{target_node_id}"
        # Avoid duplicate edges
        if any(e.id == eid for e in canvas.edges):
            return ToolReturn(
                return_value={"exists": eid},
                metadata=_snapshot_event(canvas),
            )
        edge = PipelineCanvasEdge(id=eid, source_id=source_node_id, target_id=target_node_id)
        new_canvas = canvas.with_edges([*canvas.edges, edge])
        _replace_state(ctx, new_canvas)
        return ToolReturn(
            return_value={"connected": eid},
            metadata=_snapshot_event(new_canvas),
        )

    return ts
