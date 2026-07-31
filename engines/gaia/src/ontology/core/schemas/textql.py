"""pydantic v2 schemas for TextQL (ontology-driven natural-language query).

Defines the QueryIR (Query Intent Representation) — a first-class structured
"query intent graph" produced by Step 1 (intent parsing) and consumed by
Step 2 (semantic recall), Step 3 (schema injection), and Step 4 (tool use /
text2sql compilation). See ADR-012 §「核心架构决策 决策一」.

Design principles (对标材料「自然语言要素 → SQL 元素 → 本体概念」三列表):
- IR is a first-class citizen: every element is tagged with its ontology role
  (objects/properties/links/filters/group_by/order_by/windows), so Step 2
  recall becomes "look up by role in the corresponding ontology element"
  rather than full-text matching.
- IR carries business nouns (Chinese display names), NOT api_names. The
  api_name mapping is Step 2's job — IR never asks the LLM to guess field
  names without ontology context.
- Derived metrics (占比/比率/增长率) use role="derived" + expr arithmetic;
  no Function ontology abstraction in this scope (ADR-012 决策三).
- IR is persisted per query step as the audit-trace carrier (决策一 额外收益).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── IR building blocks ──────────────────────────────────────────────────


class FilterSpec(BaseModel):
    """A filter condition (maps to WHERE / HAVING).

    ``subject`` is a business noun (e.g. "出厂年份") awaiting Step 2
    mapping to a property api_name. ``op`` is the operator; ``value`` is
    the comparison value (between → [min, max]; in → list; isNull /
    isNotNull ignore value).
    """

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(description="筛选主体业务名词，如 '出厂年份' '状态'")
    op: Literal[
        "eq",
        "neq",
        "gt",
        "gte",
        "lt",
        "lte",
        "in",
        "notIn",
        "contains",
        "startsWith",
        "endsWith",
        "between",
        "isNull",
        "isNotNull",
    ] = Field(description="操作符")
    value: Any = Field(
        default=None,
        description="比较值；between 用 [min,max]；in/notIn 用 list；isNull/isNotNull 忽略",
    )


class OrderBySpec(BaseModel):
    """A sort key (maps to ORDER BY)."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(description="排序主体业务名词，如 '创建时间' '金额'")
    direction: Literal["asc", "desc"] = Field(default="asc")


class PropertyRef(BaseModel):
    """A property reference (maps to a SELECT column or aggregation metric).

    ``role`` distinguishes:
    - select: a plain projected column
    - metric: an aggregation measure (SUM/COUNT/AVG target)
    - group_key: a GROUP BY dimension
    - derived: a derived metric expressed via ``expr`` arithmetic
      (e.g. "SUM(amount)/COUNT(*)"), routed to the text2sql path
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="属性业务名词，如 '销量' '总金额'")
    role: Literal["select", "metric", "group_key", "derived"] = Field(default="select")
    expr: str | None = Field(
        default=None,
        description="派生指标算式，如 'SUM(amount)/COUNT(*)'；仅 role=derived 时",
    )


class ObjectRef(BaseModel):
    """An object-type reference (maps to FROM / a JOIN target).

    ``is_primary`` marks the query anchor (the main object the user is
    asking about). Multi-object queries have one primary + N linked.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="对象类型业务名词，如 '订单' '货运车辆'")
    is_primary: bool = Field(default=True, description="是否主对象（查询锚点）")


class LinkRef(BaseModel):
    """A relationship reference (maps to a JOIN via a LinkType)."""

    model_config = ConfigDict(extra="forbid")

    from_object: str = Field(description="源对象业务名词")
    to_object: str = Field(description="目标对象业务名词")
    link_name: str | None = Field(default=None, description="关系业务名词，如 '所属' '执飞'")


class WindowSpec(BaseModel):
    """A window-function specification (maps to an OVER clause). T7/T8."""

    model_config = ConfigDict(extra="forbid")

    func: Literal["ROW_NUMBER", "RANK", "DENSE_RANK", "SUM", "AVG", "COUNT", "MIN", "MAX"] = Field(
        description="窗口函数"
    )
    partition_by: list[str] = Field(default_factory=list, description="分区业务名词列表")
    order_by: list[OrderBySpec] = Field(default_factory=list)
    alias: str = Field(description="输出别名，如 'rn' 'ratio'")


# ── QueryIR (first-class citizen) ───────────────────────────────────────


class QueryIR(BaseModel):
    """Query Intent Representation — the first-class structured query graph.

    Produced by Step 1 (LLM intent parsing via pydantic-ai ``result_type``),
    refined by Step 2 (api_name backfill from semantic recall), consumed by
    Step 4 (text2sql compiler / atomic tools), and persisted as the audit
    trace carrier.

    The IR is intentionally ontology-role-tagged so Step 2 recall can look
    up each element in the matching ontology element type (objects →
    ObjectType, properties → Property, links → LinkType) rather than
    treating the query as a flat bag of words.
    """

    model_config = ConfigDict(extra="forbid")

    raw_query: str = Field(description="原始自然语言问句")
    intent_type: Literal[
        "query",
        "aggregate",
        "topn",
        "count",
        "complex_sql",
        "multi_step",
    ] = Field(description="查询意图类型；multi_step 表示需多步 SQL 协作")

    # FROM / JOIN (材料表第2行)
    objects: list[ObjectRef] = Field(default_factory=list, description="涉及的对象类型")
    links: list[LinkRef] = Field(default_factory=list, description="对象间关联（沿 LinkType）")

    # SELECT (材料表第1行)
    properties: list[PropertyRef] = Field(default_factory=list, description="查询的属性/度量")

    # WHERE / HAVING (材料表第3行)
    filters: list[FilterSpec] = Field(default_factory=list)

    # GROUP BY (材料表第4行)
    group_by: list[str] = Field(default_factory=list, description="分组维度业务名词")

    # ORDER BY / LIMIT (材料表第5行)
    order_by: list[OrderBySpec] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)

    # Window functions (T7/T8)
    windows: list[WindowSpec] = Field(default_factory=list)

    # Derived-metric flag (T5) — routes to the text2sql path
    has_derived_metric: bool = Field(default=False, description="是否含派生指标（需 text2sql 算式表达）")

    # Recall-refinement flag — Step 2 could not fully resolve; LLM may
    # iteratively call metadata tools to补充 recall (决策二: LLM 自管理迭代)
    needs_recall_refinement: bool = Field(default=False, description="召回未决，留给 LLM 迭代补充")


# ── Step 2 recall result (backfills api_names onto the IR) ──────────────


class CandidateProperty(BaseModel):
    """A property candidate matched during semantic recall."""

    model_config = ConfigDict(extra="forbid")

    api_name: str
    display_name: str
    object_type_api_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    match_evidence: str = Field(default="")
    source: Literal["exact", "vector", "hyde", "fusion"] = Field(default="exact")


class CandidateObjectType(BaseModel):
    """An object-type candidate matched during semantic recall."""

    model_config = ConfigDict(extra="forbid")

    api_name: str
    display_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    matched_properties: list[CandidateProperty] = Field(default_factory=list)
    match_evidence: str = Field(default="")
    source: Literal["exact", "vector", "hyde", "fusion"] = Field(default="exact")


class RecallResult(BaseModel):
    """Step 2 output: candidates for the IR's business nouns.

    The recall layer maps each IR business noun to ontology api_names. The
    result is used by Step 3 (schema injection) and Step 4 (tool calls /
    SQL compilation use the resolved api_names).
    """

    model_config = ConfigDict(extra="forbid")

    object_types: list[CandidateObjectType] = Field(default_factory=list)
    needs_clarification: bool = Field(
        default=False,
        description="多个候选且置信度接近 → 让 LLM 澄清",
    )
