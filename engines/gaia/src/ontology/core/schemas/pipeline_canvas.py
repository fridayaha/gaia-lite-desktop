"""PipelineCanvasSnapshot — 管道构建器画布的 AG-UI shared state（ADR-018 §14.5）。

对标图探索的 ``CanvasSnapshot``（ADR-015），但承载的是管道 IR 画布
（节点 + 边），而非图探索对象集。Agent 通过 pipeline_builder toolset
的工具返回 ``StateSnapshotEvent`` 写入此 state，前端画布订阅
``state.pipeline_canvas`` 变化重绘（见 ``usePipelineBuilderAgent.ts``）。

字段命名 snake_case，前端 TS 类型必须对齐（``PipelineCanvasSnapshot``
in ``usePipelineBuilderAgent.ts``）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PipelineCanvasNode(BaseModel):
    """画布中一个管道节点（对齐前端 IRNode 子集）。"""

    id: str
    type: str
    """节点类型：Source / Transform / Sink / QualityCheck / GenericKestraTask。"""
    operator_type: str = ""
    """具体算子类型（如 Join / Filter / Aggregate）。"""
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    """节点配置（算子参数）。"""
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


class PipelineCanvasEdge(BaseModel):
    """画布中一条连接（对齐前端 IREdge）。"""

    id: str
    source_id: str
    target_id: str


class PipelineCanvasSnapshot(BaseModel):
    """管道构建器画布的 AG-UI shared state。

    前端 ``usePipelineBuilderAgent`` 订阅 STATE_SNAPSHOT 的
    ``pipeline_canvas`` 字段 → 重建 irNodes/irEdges。Agent 每轮读
    ``ctx.deps.state`` 决策下一步（如画布为空时先 list_datasets 再
    add_source）。
    """

    nodes: list[PipelineCanvasNode] = Field(default_factory=list)
    edges: list[PipelineCanvasEdge] = Field(default_factory=list)
    selected_node_id: str | None = None

    def with_nodes(self, nodes: list[PipelineCanvasNode]) -> PipelineCanvasSnapshot:
        """Return a copy with the given nodes (edges unchanged)."""
        return self.model_copy(update={"nodes": nodes})

    def with_edges(self, edges: list[PipelineCanvasEdge]) -> PipelineCanvasSnapshot:
        """Return a copy with the given edges (nodes unchanged)."""
        return self.model_copy(update={"edges": edges})

    def with_selection(self, node_id: str | None) -> PipelineCanvasSnapshot:
        """Return a copy with the given selected node id."""
        return self.model_copy(update={"selected_node_id": node_id})
