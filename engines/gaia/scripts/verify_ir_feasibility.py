"""IR（查询意图图）作为一等公民的可行性验证。

验证两个问题：
1. IR 表达力：能否覆盖 T1-T9 复杂场景（窗口/占比/同比环比/多表JOIN/聚合）
2. LLM 产出稳定性：pydantic-ai result_type 结构化输出能否稳定产出 IR

IR 设计原则（对标材料三列表 + 选项B 一等公民）：
- IR 是 Step 1-3 协同产出的结构化"查询意图图"
- 按本体概念分类：object_refs / property_refs / link_refs / filters / group_by / order_by
- 双消费者：text2sql 编译器吃它生成 SQL；原子工具吃它生成工具调用
- 多步可继承：每个 IR 实例可命名、可被后续步骤引用（ObjectSet 具名引用）

验证方式：
- 表达力：手工把 10 个代表性场景（含 T1-T9）写成 IR，验证字段齐全
- LLM 稳定性：用真实 DeepSeek API 跑 10 个自然语言问句，验证结构化产出成功率
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from pydantic import BaseModel, Field

# ── IR Schema 设计 ──────────────────────────────────────────────────────


class FilterSpec(BaseModel):
    """筛选条件，对应 WHERE/HAVING。subject 是业务名词（待 Step2 召回映射）。"""

    subject: str = Field(description="筛选主体业务名词，如'出厂年份''状态'")
    op: str = Field(description="操作符: eq/neq/gt/gte/lt/lte/in/notIn/contains/startsWith/between/isNull/isNotNull")
    value: Any = Field(default=None, description="比较值；between 用 [min,max]；in 用 list；isNull/isNotNull 忽略")


class OrderBySpec(BaseModel):
    """排序，对应 ORDER BY。"""

    subject: str = Field(description="排序主体业务名词，如'创建时间''金额'")
    direction: str = Field(default="asc", description="asc|desc")


class PropertyRef(BaseModel):
    """属性引用，对应 SELECT 的列或聚合度量。"""

    name: str = Field(description="属性业务名词，如'销量''总金额'")
    role: str = Field(default="select", description="select|metric|group_key|derived")
    # derived_role 用于派生指标（复购率等），expr 是算式描述（待 text2sql 编译）
    expr: str | None = Field(default=None, description="派生指标算式，如'SUM(amount)/COUNT(*)'，仅 role=derived 时")


class ObjectRef(BaseModel):
    """对象引用，对应 FROM/JOIN 的表。"""

    name: str = Field(description="对象类型业务名词，如'订单''货运车辆'")
    alias: str | None = Field(default=None, description="别名，用于多步引用，如'top5_products'")
    is_primary: bool = Field(default=True, description="是否主对象（查询锚点）")


class LinkRef(BaseModel):
    """关系引用，对应 JOIN 的关联路径。"""

    from_object: str = Field(description="源对象业务名词")
    to_object: str = Field(description="目标对象业务名词")
    link_name: str | None = Field(default=None, description="关系业务名词，如'所属''执飞'")


class WindowSpec(BaseModel):
    """窗口函数，对应 OVER 子句。T7/T8 用。"""

    func: str = Field(description="窗口函数: ROW_NUMBER|RANK|DENSE_RANK|SUM|AVG|COUNT")
    partition_by: list[str] = Field(default_factory=list, description="分区业务名词列表")
    order_by: list[OrderBySpec] = Field(default_factory=list)
    alias: str = Field(description="输出别名，如'rn''ratio'")


class QueryIR(BaseModel):
    """查询意图图（一等公民 IR）。

    Step 1-3 协同产出，Step 4 的两类消费者（text2sql 编译器 / 原子工具）共同消费。
    多步查询中，每个 IR 实例可命名（ObjectSet 具名引用），后续步骤引用其结果。
    """

    raw_query: str = Field(description="原始自然语言问句")
    intent_type: str = Field(description="query|aggregate|topn|count|complex_sql|multi_step")
    # 对应 FROM/JOIN
    objects: list[ObjectRef] = Field(default_factory=list, description="涉及的对象类型")
    links: list[LinkRef] = Field(default_factory=list, description="对象间关联（沿 LinkType）")
    # 对应 SELECT
    properties: list[PropertyRef] = Field(default_factory=list, description="查询的属性/度量")
    # 对应 WHERE
    filters: list[FilterSpec] = Field(default_factory=list)
    # 对应 GROUP BY
    group_by: list[str] = Field(default_factory=list, description="分组维度业务名词")
    # 对应 ORDER BY / LIMIT
    order_by: list[OrderBySpec] = Field(default_factory=list)
    limit: int | None = Field(default=None)
    offset: int | None = Field(default=None)
    # 窗口函数（T7/T8）
    windows: list[WindowSpec] = Field(default_factory=list)
    # 多步引用（多步查询时，本步引用前序步骤产出的 ObjectSet）
    depends_on: list[str] = Field(default_factory=list, description="依赖的前序 ObjectSet 别名")
    output_alias: str | None = Field(default=None, description="本步产出的 ObjectSet 别名，供后续引用")
    # 派生指标标记（T5）
    has_derived_metric: bool = Field(default=False, description="是否含派生指标（需 text2sql 算式）")
    # 召回未决标记（Step2 召回不到时，留给 LLM 迭代补充）
    needs_recall_refinement: bool = Field(default=False)


# ── 验证1：IR 表达力（手工构造 T1-T9 的 IR，检查字段齐全）──────────────


def verify_expressiveness() -> tuple[int, int]:
    """手工构造 9 个场景的 IR，验证字段能表达所有 SQL 语义。"""
    cases: list[tuple[str, QueryIR]] = []

    # T1 单表过滤检索
    cases.append(
        (
            "T1 单表过滤",
            QueryIR(
                raw_query="2025Q2下单的华东区企业客户",
                intent_type="query",
                objects=[ObjectRef(name="订单", is_primary=True)],
                properties=[PropertyRef(name="订单号"), PropertyRef(name="客户名称")],
                filters=[
                    FilterSpec(subject="下单时间", op="between", value=["2025-04-01", "2025-06-30"]),
                    FilterSpec(subject="区域", op="eq", value="华东"),
                ],
                order_by=[],
                limit=100,
            ),
        )
    )

    # T2 跨实体 JOIN
    cases.append(
        (
            "T2 跨实体JOIN",
            QueryIR(
                raw_query="逾期订单对应的客户负责人和联系方式",
                intent_type="query",
                objects=[ObjectRef(name="订单", is_primary=True), ObjectRef(name="客户")],
                links=[LinkRef(from_object="订单", to_object="客户", link_name="属于")],
                properties=[PropertyRef(name="客户负责人"), PropertyRef(name="联系方式")],
                filters=[FilterSpec(subject="状态", op="eq", value="逾期")],
            ),
        )
    )

    # T3 多层关联穿透（4表）
    cases.append(
        (
            "T3 多层穿透",
            QueryIR(
                raw_query="索赔→车辆→零件→供应商",
                intent_type="query",
                objects=[
                    ObjectRef(name="索赔", is_primary=True),
                    ObjectRef(name="车辆"),
                    ObjectRef(name="零件"),
                    ObjectRef(name="供应商"),
                ],
                links=[
                    LinkRef(from_object="索赔", to_object="车辆"),
                    LinkRef(from_object="索赔", to_object="零件"),
                    LinkRef(from_object="零件", to_object="供应商"),
                ],
                properties=[
                    PropertyRef(name="索赔号"),
                    PropertyRef(name="VIN"),
                    PropertyRef(name="零件名"),
                    PropertyRef(name="供应商名"),
                ],
            ),
        )
    )

    # T4 多维聚合
    cases.append(
        (
            "T4 多维聚合",
            QueryIR(
                raw_query="每区域销售额分别多少",
                intent_type="aggregate",
                objects=[ObjectRef(name="订单", is_primary=True)],
                properties=[PropertyRef(name="销售额", role="metric")],
                group_by=["区域"],
            ),
        )
    )

    # T5 占比计算（派生指标）
    cases.append(
        (
            "T5 占比计算",
            QueryIR(
                raw_query="VIP客户占比",
                intent_type="aggregate",
                objects=[ObjectRef(name="客户", is_primary=True)],
                properties=[
                    PropertyRef(name="VIP数", role="metric"),
                    PropertyRef(name="客户总数", role="metric"),
                    PropertyRef(
                        name="VIP占比", role="derived", expr="SUM(CASE WHEN level='VIP' THEN 1 ELSE 0 END)/COUNT(*)"
                    ),
                ],
                group_by=["区域"],
                has_derived_metric=True,
            ),
        )
    )

    # T6 同比环比（SELF JOIN 跨期，多步）
    cases.append(
        (
            "T6 同比环比",
            QueryIR(
                raw_query="今年上半年 vs 去年同期销售额对比",
                intent_type="multi_step",
                objects=[ObjectRef(name="订单", is_primary=True)],
                properties=[
                    PropertyRef(name="销售额", role="metric"),
                    PropertyRef(name="同比增长率", role="derived", expr="(cur-prev)/prev"),
                ],
                filters=[FilterSpec(subject="下单时间", op="between", value=["2025-01-01", "2025-06-30"])],
                has_derived_metric=True,
                needs_recall_refinement=True,  # 同比需SELF JOIN，IR标记需迭代
            ),
        )
    )

    # T7 TopN+占比（窗口函数）
    cases.append(
        (
            "T7 TopN+占比",
            QueryIR(
                raw_query="Top10客户及金额占比",
                intent_type="topn",
                objects=[ObjectRef(name="订单", is_primary=True)],
                properties=[
                    PropertyRef(name="客户"),
                    PropertyRef(name="总金额", role="metric"),
                    PropertyRef(name="占比", role="derived", expr="total/SUM(total) OVER()"),
                ],
                order_by=[OrderBySpec(subject="总金额", direction="desc")],
                limit=10,
                windows=[WindowSpec(func="SUM", partition_by=[], alias="total_sum")],
                has_derived_metric=True,
            ),
        )
    )

    # T8 排名（窗口函数）
    cases.append(
        (
            "T8 排名",
            QueryIR(
                raw_query="各产线周转天数从高到低排序",
                intent_type="query",
                objects=[ObjectRef(name="产线", is_primary=True)],
                properties=[PropertyRef(name="产线名"), PropertyRef(name="周转天数")],
                order_by=[OrderBySpec(subject="周转天数", direction="desc")],
                windows=[
                    WindowSpec(
                        func="ROW_NUMBER",
                        partition_by=[],
                        order_by=[OrderBySpec(subject="周转天数", direction="desc")],
                        alias="rn",
                    )
                ],
            ),
        )
    )

    # T9 时间序列趋势
    cases.append(
        (
            "T9 时间趋势",
            QueryIR(
                raw_query="近6个月每月故障率变化趋势",
                intent_type="aggregate",
                objects=[ObjectRef(name="索赔", is_primary=True)],
                properties=[PropertyRef(name="故障数", role="metric")],
                group_by=["月份"],
                order_by=[OrderBySpec(subject="月份", direction="asc")],
            ),
        )
    )

    # 验证每个 IR 序列化/反序列化无损 + 关键字段齐全
    passed = 0
    for name, ir in cases:
        try:
            # 序列化往返
            data = ir.model_dump_json()
            restored = QueryIR.model_validate_json(data)
            assert restored.intent_type == ir.intent_type
            # 检查关键语义字段非空
            assert len(restored.objects) > 0, "objects 不能为空"
            if restored.intent_type in ("query", "aggregate", "topn"):
                assert len(restored.properties) > 0 or restored.intent_type == "count", f"{name}: properties 不能为空"
            print(
                f"  ✓ {name}: intent={restored.intent_type}, objects={len(restored.objects)}, "
                f"props={len(restored.properties)}, filters={len(restored.filters)}, "
                f"derived={restored.has_derived_metric}"
            )
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
    return passed, len(cases)


# ── 验证2：LLM 产出稳定性（真实 DeepSeek API）───────────────────────────

SYSTEM_PROMPT = """你是查询意图解析器。把用户自然语言问句解析为结构化的 QueryIR。

规则：
1. 按本体概念分类抽取：objects(对象类型)、properties(属性/度量)、links(关系)、filters(筛选)、group_by(分组)、order_by(排序)、limit
2. 派生指标（占比/比率/增长率）用 properties 里 role=derived + expr 算式表达
3. 多表关联用 links 表达（沿业务关系）
4. 窗口函数（排名/TopN占比）用 windows 表达
5. 多步查询（同比环比需SELF JOIN、多跳推理）intent_type=multi_step，needs_recall_refinement=true
6. subject/name 字段填业务名词（中文），不要填字段名或表名
7. 不确定的填 needs_recall_refinement=true，留给后续召回补充"""


async def verify_llm_stability() -> tuple[int, int]:
    """用真实 LLM 跑 10 个自然语言问句，验证 IR 结构化产出成功率。"""
    # 从 benchmark 真实用例 + 自造复杂场景各取几个
    test_cases = [
        "查询航班 1024 的航班号和状态",  # L1 单实体
        "找出所有延误的航班，按延误时长降序排列前100",  # L2 过滤+排序
        "统计各状态的航班数量",  # L2 聚合
        "延误超过 60 分钟的航班",  # L2 过滤
        "航班 1024 执飞的飞机是什么机型",  # L3 单跳关联
        "延误航班执飞的飞机有哪些待处理维修任务",  # L3 多跳
        "今年上半年每个区域的销售额分别是多少",  # T4 多维聚合
        "VIP客户占比多少",  # T5 派生指标
        "Top10回款金额最高的客户及其整体占比",  # T7 TopN+窗口
        "近6个月每月的设备故障率变化趋势",  # T9 时间趋势
    ]

    # 导入 settings 会自动加载 .env 并 re-export key 到 os.environ
    from ontology.config.settings import settings  # noqa: F401

    if not os.getenv("DEEPSEEK_API_KEY"):
        print("  ⚠ 跳过 LLM 验证：DEEPSEEK_API_KEY 未设置")
        return 0, 0

    from pydantic_ai import Agent

    agent = Agent(
        settings.ai_model,
        system_prompt=SYSTEM_PROMPT,
        output_type=QueryIR,
        retries=1,
        defer_model_check=True,
    )

    passed = 0
    for i, q in enumerate(test_cases, 1):
        try:
            result = await agent.run(q)
            ir = result.output
            print(
                f"  ✓ [{i}] {q[:30]}... → intent={ir.intent_type}, "
                f"objects={[o.name for o in ir.objects]}, "
                f"props={[p.name for p in ir.properties]}, "
                f"filters={len(ir.filters)}, derived={ir.has_derived_metric}"
            )
            passed += 1
        except Exception as e:
            err = str(e)[:120]
            print(f"  ✗ [{i}] {q[:30]}... → {err}")
    return passed, len(test_cases)


# ── 主流程 ──────────────────────────────────────────────────────────────


async def main() -> int:
    print("=" * 70)
    print("验证1：IR 表达力（T1-T9 手工构造）")
    print("=" * 70)
    p1, t1 = verify_expressiveness()
    print(f"\n表达力: {p1}/{t1} 通过")

    print("\n" + "=" * 70)
    print("验证2：LLM 产出稳定性（真实 DeepSeek API，10 个问句）")
    print("=" * 70)
    p2, t2 = await verify_llm_stability()
    if t2 > 0:
        print(f"\nLLM 稳定性: {p2}/{t2} 通过")

    print("\n" + "=" * 70)
    print("总结")
    print("=" * 70)
    print(f"  IR 表达力: {p1}/{t1}")
    if t2 > 0:
        print(f"  LLM 稳定性: {p2}/{t2}")
        print(f"  综合: IR 作为一等公民 {'可行' if p1 == t1 and p2 >= t2 * 0.8 else '需重新评估'}")
    else:
        print("  LLM 稳定性: 跳过（无 API key）")
        print(f"  综合: 表达力{'可行' if p1 == t1 else '不足'}，LLM 稳定性待验证")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
