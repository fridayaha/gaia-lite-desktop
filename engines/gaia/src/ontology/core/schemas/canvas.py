"""CanvasSnapshot — 图探索画布的 AG-UI shared state（ADR-015）。

Agent 通过工具返回 ``StateSnapshotEvent`` 写入此 state，前端画布订阅
``state.canvas`` 变化重绘；Agent 每轮通过 ``@agent.instructions`` 读取
当前画布状态（ReAct 的「当前状态」感知通道）。

机制参照 pydantic-ai 官方示例 ``examples/ag_ui/api/shared_state.py``
（``RecipeSnapshot`` + ``StateDeps`` + 工具返回 ``StateSnapshotEvent``）。

字段命名 snake_case，前端 TS 类型必须对齐（见 ``types/canvas.ts``）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CanvasObject(BaseModel):
    """画布中一个对象的轻量表示（供前端渲染 + Agent 理解上下文）。"""

    rid: str
    api_name: str
    """ObjectType api_name（前端据此渲染节点类型/图标）。"""
    title: str = ""
    """标题属性值（节点显示名），无则空串。"""
    summary: dict[str, Any] = Field(default_factory=dict)
    """少量关键属性（如 status/riskLevel），供 Agent 决策 + 前端 tooltip。
    不含全量属性——全量走 object_state 点查，避免 state 膨胀。"""


class CanvasEdge(BaseModel):
    """画布中一条探索轨迹边（searchAround 产生的关系箭头）。

    只有明确关系链的探索（如 "S000 的物料用在了哪些订单"）才产生边；
    纯 objectType/filter 查询不产生边（它们是起点集/筛选，无来源关系）。
    多步 searchAround 串联时边累积保留，形成可视化轨迹（ADR-015）。"""

    source_rid: str
    target_rid: str
    link_type: str
    """LinkType api_name（前端据此渲染边样式/标签）。"""
    direction: Literal["out", "in", "both"] = "out"


class CanvasSnapshot(BaseModel):
    """图探索画布的 AG-UI shared state。

    前端 ``useAgent`` 订阅此 state → 调 ``explore.loadStartSet`` /
    ``setView`` / ``setLayerStyle`` 同步画布。Agent 每轮读
    ``ctx.deps.state.canvas`` 决策下一步（如 ``object_count == 0`` 时
    自然终止，不再空转编结论——ADR-015 D5）。
    """

    objects: list[CanvasObject] = Field(default_factory=list)
    """当前画布对象集。Agent 调 query_with_dataframe / traverse_link 后，
    工具把结果对象写入此字段（截断到合理上限，避免 state 过大）。"""
    edges: list[CanvasEdge] = Field(default_factory=list)
    """画布探索轨迹边集。仅 searchAround 产生（纯查询不产生边），
    多步串联时累积保留，形成可视化轨迹（ADR-015）。"""
    view: Literal["graph", "map", "split"] = "graph"
    """当前视图模式。Agent 调 switch_view 工具（前端 tool）时更新。"""
    color_by: str | None = None
    """当前着色属性 api_name。None 表示未着色。"""
    expanded_links: list[str] = Field(default_factory=list)
    """已展开的 link api_name 列表，避免重复展开 + 供 Agent 知道探索过的关系。"""
    object_count: int = 0
    """画布对象总数（== len(objects)，冗余字段方便 Agent 快速判断空画布）。"""
    last_query_summary: str = ""
    """上一步查询的摘要，Agent 决策依据。如 "Dealership (0 个对象)"、
    "searchAround supplies from S001 → 12 个 Order"。object_count == 0 时
    Agent 应如实告知用户无法分析并终止，不编造结论（ADR-015 D5）。"""

    def with_objects(
        self,
        objects: list[CanvasObject],
        *,
        query_summary: str = "",
        append: bool = False,
    ) -> CanvasSnapshot:
        """返回更新了 objects 的新 snapshot（不可变更新，便于 state diff）。

        用于纯查询（objectType/filter/aggregate）——覆盖式刷新画布对象集，
        不产生边（纯查询无来源关系）。searchAround 探索请用
        :meth:`with_search_around`（累积节点 + 边）。"""
        new_objects = (self.objects + objects) if append else objects
        # 覆盖式纯查询（append=False）：重新开始看一个对象集，清空探索轨迹边。
        # 追加式（append=True）：保留既有边（增量加载更多对象）。
        new_edges = self.edges if append else []
        new_expanded = self.expanded_links if append else []
        return self.model_copy(
            update={
                "objects": new_objects,
                "object_count": len(new_objects),
                "edges": new_edges,
                "expanded_links": new_expanded,
                "last_query_summary": query_summary or self.last_query_summary,
            }
        )

    def with_search_around(
        self,
        objects: list[CanvasObject],
        edges: list[CanvasEdge],
        *,
        link: str,
        query_summary: str = "",
        max_objects: int = 2000,
        max_edges: int = 4000,
    ) -> CanvasSnapshot:
        """累积 searchAround 探索结果（节点去重 + 边去重 + 软上限）。

        与 :meth:`with_objects`（覆盖式纯查询）的区别：searchAround 产生
        明确的 source→target 关系，必须保留为画布边并累积，多步串联形成
        可视化轨迹（如 S000→物料→订单）。节点超 ``max_objects`` 时丢弃
        最早加入的节点（保轨迹连续性的同时防 state 无限膨胀）。

        Args:
            objects: 本步命中的对象（节点）。
            edges: 本步命中的边三元组（source→target + link）。
            link: 本步展开的 LinkType api_name（记入 expanded_links）。
            query_summary: 本步查询摘要（覆盖 last_query_summary）。
            max_objects/max_edges: 软上限，超限丢弃最早项。
        """
        existing_rids = {o.rid for o in self.objects}
        merged_objects = list(self.objects)
        for obj in objects:
            if obj.rid not in existing_rids:
                merged_objects.append(obj)
                existing_rids.add(obj.rid)
        # 软上限：超限丢弃最早加入的节点（保轨迹连续性，不硬截断当前步）
        if len(merged_objects) > max_objects:
            merged_objects = merged_objects[-max_objects:]

        existing_edge_keys = {(e.source_rid, e.target_rid, e.link_type) for e in self.edges}
        merged_edges = list(self.edges)
        for edge in edges:
            key = (edge.source_rid, edge.target_rid, edge.link_type)
            if key not in existing_edge_keys:
                merged_edges.append(edge)
                existing_edge_keys.add(key)
        if len(merged_edges) > max_edges:
            merged_edges = merged_edges[-max_edges:]

        expanded = self.with_expanded_link(link).expanded_links
        return self.model_copy(
            update={
                "objects": merged_objects,
                "object_count": len(merged_objects),
                "edges": merged_edges,
                "expanded_links": expanded,
                "last_query_summary": query_summary or self.last_query_summary,
            }
        )

    def with_view(self, view: Literal["graph", "map", "split"]) -> CanvasSnapshot:
        return self.model_copy(update={"view": view})

    def with_color_by(self, prop: str | None) -> CanvasSnapshot:
        return self.model_copy(update={"color_by": prop})

    def with_expanded_link(self, link: str) -> CanvasSnapshot:
        if link in self.expanded_links:
            return self
        return self.model_copy(update={"expanded_links": [*self.expanded_links, link]})
