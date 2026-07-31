"""pydantic v2 schemas for Graph Layer (graph-reasoning-design.md §4).

These are internal data structures passed between Neo4jGraphStore and
DataFrameQueryService — NOT the LLM-facing ObjectSet IR (that lives in
``core/schemas/object_set.py``). The split keeps the execution-layer
contracts separate from the transport-layer IR (C6).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 图遍历方向。Neo4j 原生双向，不存反向边。
TraversalDirection = Literal["out", "in", "both"]


class NodeFilter(BaseModel):
    """下推到 Neo4j 的节点 WHERE 谓词（图遍历剪枝）。

    仅支持 indexed 属性的等值/范围判断（剪枝字段，不存全量属性）。
    复杂过滤由 DataFrameQueryService 在 Ibis 层完成。
    """

    field: str
    op: Literal["eq", "neq", "gt", "gte", "lt", "lte", "in"] = "eq"
    value: Any | None = None
    values: list[Any] | None = None  # op="in" 时用


class EdgeTriple(BaseModel):
    """图遍历产生的一条边（source→target 关系三元组）。

    searchAround 执行时，Neo4j Cypher 额外返回 start.rid → m.rid 配对，
    填入此结构。用于画布渲染探索轨迹箭头（ADR-015 图探索）：每一步
    searchAround 的 (source, link, target) 关系保留为画布边，多步串联
    形成 "S000 → 物料 → 订单" 的可视化轨迹。

    多跳（hops>1）时只记录 start→最终target 的直连边，中间节点若未在
    结果集则不画中间边（设计权衡：避免 path 还原的复杂度与 state 膨胀）。
    """

    source_rid: str
    target_rid: str
    rel_type: str
    direction: TraversalDirection = "out"


class GraphTraversalResult(BaseModel):
    """图遍历结果。返回 rid 集 + 边三元组 + 可选路径信息（证据链用）。"""

    # 命中节点 rid 集（= object_state.id，C1 迁移口子）。
    rids: list[str] = Field(default_factory=list)
    # 命中边三元组（source→target + rel_type + direction）。
    # searchAround 专属：纯 objectType/filter 查询不产生边。
    edges: list[EdgeTriple] = Field(default_factory=list)
    # 是否被截断（达上限，C9 防线二，truncated=true + 游标续取）。
    truncated: bool = False
    # 可选路径：source_rid -> [intermediate_rids...] -> target_rid。
    # 证据链快照用（M6）。MVP 可为空。
    paths: list[list[str]] = Field(default_factory=list)
    # 各跳统计（证据链用）。
    hops: int = 0
    matched_count: int = 0


class EdgeProps(BaseModel):
    """边属性（写入投影时用）。仅存轻量字段（C1 边模型轻量）。"""

    weight: float | None = None
    start_time: str | None = None  # ISO8601；时态边用（temporal=True）
    end_time: str | None = None
    visibility: str | None = None
