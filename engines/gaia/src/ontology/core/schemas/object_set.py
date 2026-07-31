"""ObjectSet IR — 推理线传输层契约 (graph-reasoning-design.md §7.2, C6/C7)。

LLM 产此 JSON（受控白名单护栏），DataFrameQueryService 翻译为执行层
（Ibis TableExpr / Neo4j Cypher）。两层 IR 分离：安全 + 可靠。

对齐 Palantir ObjectSet 真实结构：searchAround 是顶层 type（非 transform）。
MVP 支持 type: objectType / static / filter / searchAround。
列二期: union / intersect / subtract / nearestNeighbors。

嵌套深度: searchAround ≤ 3 层（对齐 Palantir 硬限）。
"""

from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator

# ObjectSet 操作类型（C7 四种基础 + 集合运算 + 聚合，对齐 Palantir/Ibis）。
ObjectSetType = Literal[
    "objectType",
    "static",
    "filter",
    "searchAround",
    "union",
    "intersect",
    "subtract",
    "aggregate",
    "select",
    "withProperties",
    "reference",
    "interfaceBase",
    "interfaceLinkSearchAround",
]

# Filter 算子。空间算子对齐 Palantir GeoFilter；时序算子对齐 TimeSeriesFilter。
# 属性算子对齐 Ibis 表达式能力（in/notIn/notEqual/greaterThan/lessThan/startsWith/endsWith）。
FilterOp = Literal[
    "exactMatch",
    "notEqual",
    "in",
    "notIn",
    "range",
    "greaterThan",
    "lessThan",
    "contains",
    "startsWith",
    "endsWith",
    "withinDistance",
    "withinPolygon",
    "withinBoundingBox",
    "timeRange",
    "isNull",
    "isNotNull",
]


class Filter(BaseModel):
    """过滤条件。field 经白名单校验（必须在本体 properties 内）。"""

    field: str
    op: FilterOp
    # exactMatch/notEqual/contains/startsWith/endsWith: value = 标量
    # in/notIn: value = [标量, ...]（枚举值列表）
    # greaterThan/lessThan: value = 标量（开区间单边）
    # range: value = {"min": ..., "max": ...}
    # timeRange: value = {"start": ..., "end": ...}
    value: Any | None = None
    # 空间算子用 coords（[[lon, lat], ...]）：
    # withinPolygon: 多边形顶点
    # withinBoundingBox: [[minLon,minLat],[maxLon,maxLat]]
    coords: list[list[float]] | None = None
    # withinDistance: center=[lon,lat] + max_distance（米）
    center: list[float] | None = None
    max_distance: float | None = None

    @model_validator(mode="after")
    def _validate_spatial_args(self) -> Filter:
        """空间算子必须提供对应几何参数。"""
        if self.op == "withinPolygon" and not self.coords:
            raise ValueError("withinPolygon requires coords")
        if self.op == "withinBoundingBox" and not self.coords:
            raise ValueError("withinBoundingBox requires coords (bbox)")
        if self.op == "withinDistance" and (not self.center or self.max_distance is None):
            raise ValueError("withinDistance requires center + max_distance")
        if self.op in ("in", "notIn") and not isinstance(self.value, list):
            raise ValueError(f"{self.op} requires value=[...] (list)")
        if self.op in ("notEqual", "greaterThan", "lessThan", "startsWith", "endsWith") and self.value is None:
            raise ValueError(f"{self.op} requires value")
        if self.op == "range" and not isinstance(self.value, dict):
            raise ValueError("range requires value={min,max}")
        if self.op == "timeRange" and not isinstance(self.value, dict):
            raise ValueError("timeRange requires value={start,end}")
        return self


# ── WhereClause：嵌套逻辑组合（对齐 Palantir SearchJsonQueryV2 and/or/not）──
# 叶子节点是 Filter，分支节点是 and/or/not 逻辑组合。
# filters (flat list) 保留作 AND 简写，where 用于复杂嵌套逻辑。


class AndClause(BaseModel):
    """逻辑与：所有子条件都满足。"""

    type: Literal["and"] = "and"
    value: list[WhereClause]


class OrClause(BaseModel):
    """逻辑或：任一子条件满足。"""

    type: Literal["or"] = "or"
    value: list[WhereClause]


class NotClause(BaseModel):
    """逻辑非：子条件不满足。"""

    type: Literal["not"] = "not"
    value: WhereClause


# 判别联合：Filter（叶子） | AndClause | OrClause | NotClause
WhereClause = Union[Filter, AndClause, OrClause, NotClause]  # noqa: UP007  # 判别联合需 Union


class ObjectSetIR(BaseModel):
    """判别联合，对齐 Palantir ObjectSet。type 区分操作。LLM 产此 JSON。

    objectType: 起始对象集（某 ObjectType 全集，可带 filter）
    static: 直接给定 pk 列表
    filter: 对子 objectSet 应用 filters（嵌套）
    searchAround: 从子 objectSet 出发图遍历 link（嵌套，≤3 层）
    """

    type: ObjectSetType

    # objectType: 目标 ObjectType api_name
    object_type: str | None = None
    # objectType 可选内联 filter（起始集就过滤）。filters 是 flat AND 简写。
    filters: list[Filter] | None = None
    # filter/objectType 可选：嵌套逻辑组合（and/or/not），对齐 Palantir SearchJsonQueryV2。
    # where 与 filters 互斥（同时给时 where 优先）。
    where: WhereClause | None = None

    # static: 业务主键值列表（执行时解析为 rid）。复用上面的 object_type 字段
    # 指定 ObjectType，翻译层据此取 primary_key 字段名查 object_state。
    objects: list[str] | None = None

    # filter / searchAround: 子 ObjectSet（嵌套）
    object_set: ObjectSetIR | None = None

    # union/intersect/subtract: 子 ObjectSet 列表（≥2 个）
    object_sets: list[ObjectSetIR] | None = None

    # searchAround: 遍历的 link api_name
    link: str | None = None
    # searchAround 可选：跳数范围 (min,max)，默认 (1,3)
    hops: tuple[int, int] | None = None
    # searchAround 可选：方向，默认 both
    direction: Literal["out", "in", "both"] | None = None

    # 可选：排序（保证 cursor 分页稳定性）。field=属性 api_name，desc=降序。
    # 顶层 IR 带时，求值后的 rid 集按此字段排序再水合。
    order_by: list[dict[str, Any]] | None = None
    # 例：[{"field": "createdAt", "desc": false}]

    # aggregate: 分组字段列表（可空=全局聚合）
    group_by: list[str] | None = None
    # aggregate: 聚合函数列表 [{"func": "count|sum|avg|min|max", "field": "属性", "alias": "别名"}]
    aggregations: list[dict[str, Any]] | None = None

    # select: 投影字段列表（只水合这些字段，减少 IO）
    select_fields: list[str] | None = None

    # withProperties: 派生属性定义（{name: {expression/type}}，实验性，待表达式引擎）
    derived_properties: dict[str, Any] | None = None
    # reference: 引用的 ObjectSet RID（持久化 ObjectSet，待存储基础设施）
    reference: str | None = None

    # interfaceBase: Interface api_name（跨类型起始集，所有实现此 Interface 的对象）
    interface: str | None = None
    # interfaceLinkSearchAround: 按 Interface 关系遍历（link + object_set + hops + direction）

    @model_validator(mode="after")
    def _validate_dispatch(self) -> ObjectSetIR:
        """按 type 校验必填字段。"""
        if self.type == "objectType":
            if not self.object_type:
                raise ValueError("objectType requires object_type")
        elif self.type == "static":
            if not self.objects:
                raise ValueError("static requires objects")
        elif self.type == "filter":
            if not self.object_set or (not self.filters and not self.where):
                raise ValueError("filter requires object_set + (filters or where)")
        elif self.type == "searchAround":
            if not self.object_set or not self.link:
                raise ValueError("searchAround requires object_set + link")
        elif self.type in ("union", "intersect", "subtract"):
            if not self.object_sets or len(self.object_sets) < 2:
                raise ValueError(f"{self.type} requires object_sets (>=2)")
        elif self.type == "aggregate":
            if not self.object_set or not self.aggregations:
                raise ValueError("aggregate requires object_set + aggregations")
        elif self.type == "select":
            if not self.object_set or not self.select_fields:
                raise ValueError("select requires object_set + select_fields")
        elif self.type == "withProperties":
            if not self.object_set or not self.derived_properties:
                raise ValueError("withProperties requires object_set + derived_properties")
        elif self.type == "reference":
            if not self.reference:
                raise ValueError("reference requires reference (ObjectSet RID)")
        elif self.type == "interfaceBase":
            if not self.interface:
                raise ValueError("interfaceBase requires interface (Interface api_name)")
        elif self.type == "interfaceLinkSearchAround":
            if not self.object_set or not self.link or not self.interface:
                raise ValueError("interfaceLinkSearchAround requires object_set + link + interface")
        return self

    def search_around_depth(self) -> int:
        """计算整棵 IR 树中 searchAround 的最大嵌套深度（≤3，C7 Palantir 硬限）。

        递归遍历子 object_set，取最深 searchAround 链。中间夹的 filter
        不打断计数（filter 的子 object_set 仍可能含 searchAround）。
        """
        if self.type != "searchAround":
            # 非根 searchAround 时，递归子树找 searchAround 深度。
            return self._child_search_around_depth()
        # 根是 searchAround：1 + 子树最深 searchAround 链。
        return 1 + self._child_search_around_depth()

    def _child_search_around_depth(self) -> int:
        """子 object_set 的最深 searchAround 链长度。"""
        if self.object_set is None:
            return 0
        return self.object_set.search_around_depth()


ObjectSetIR.model_rebuild()


class ReasoningResult(BaseModel):
    """推理查询返回结果（query_with_dataframe 工具返回）。"""

    objects: list[dict[str, Any]] = Field(default_factory=list)
    # searchAround 产生的边三元组（source_rid/target_rid/link_type/direction）。
    # 纯查询（objectType/filter/aggregate）为空。用于画布渲染探索轨迹箭头。
    edges: list[dict[str, Any]] = Field(default_factory=list)
    # aggregate 结果：[{"group": {field:value}, "aggregates": {alias: value}}]
    aggregates: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    evidence_id: str | None = None
