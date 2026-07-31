"""IntentParser — Step 1 of the TextQL pipeline.

Parses a user's natural-language question into a structured QueryIR (the
first-class query intent graph, ADR-012 决策一). Uses pydantic-ai's
``result_type=QueryIR`` for structured output — the LLM is constrained to
produce the IR schema directly.

Design principles (ADR-012 §「Step 1」):
- IR carries business nouns (Chinese display names), NEVER api_names.
  The api_name mapping is Step 2's job.
- Each element is tagged with its ontology role (objects/properties/links/
  filters/group_by/order_by/windows) so Step 2 recall looks up by role.
- Derived metrics (占比/比率) use role="derived" + expr arithmetic.
- Multi-step queries (YoY, multi-hop) are flagged multi_step +
  needs_recall_refinement; the LLM orchestrates them (决策二).

This is the ONLY place the LLM "creates" structured content in the pipeline.
The constrained IR schema keeps hallucination risk low.
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent, ModelSettings

from ontology.config.settings import settings
from ontology.core.schemas.textql import QueryIR

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是查询意图解析器。把用户自然语言问句解析为结构化的 QueryIR。

核心规则（按本体概念分类抽取，对标自然语言→SQL→本体三列表）：
1. objects（对应 FROM/JOIN）：用户要查的"对象"，如"订单""货运车辆"。
   多对象时主对象标 is_primary=true。
2. properties（对应 SELECT）：用户要的属性/度量。
   普通列 role=select；聚合度量 role=metric；分组维度放 group_by 字段；
   派生指标（占比/比率/增长率）role=derived 且填 expr 算式。
3. links（对应 JOIN）：对象间关系，填 from_object/to_object 业务名词
   + link_name（如"所属""执飞"）。
4. filters（对应 WHERE/HAVING）：筛选条件，subject 填业务名词
   （如"出厂年份"），op 用枚举值，between 的 value 用 [min,max]，in 用 list。
5. group_by：分组维度业务名词列表（如"区域""产品类别"）。
6. order_by：排序，subject 填业务名词，direction asc/desc。
7. windows：窗口函数（排名/TopN占比），func 用 ROW_NUMBER/RANK/SUM 等，
   填 partition_by/order_by/alias。
8. limit/offset：分页与限制。

关键约束：
- subject/name/from_object/to_object 等所有名词字段填【中文业务名词】，
  绝不要填字段名(api_name)或表名。例：填"出厂年份"而非"produce_year"。
- 派生指标（如"占比""复购率""同比增长率"）必须用 role=derived + expr 算式表达，
  并设 has_derived_metric=true。
- 多步查询（同比环比需 SELF JOIN、多跳推理需多次查询）intent_type=multi_step，
  needs_recall_refinement=true。
- 意图分类：单表简单查询=query；统计聚合=aggregate；TopN=topn；
  仅计数=count；复杂SQL（多表JOIN+子查询+窗口）=complex_sql。
- 不确定的填 needs_recall_refinement=true，留给后续召回补充。

只输出 QueryIR 结构，不要解释。"""


def build_intent_agent() -> Agent[None, QueryIR]:
    """Build the intent-parsing Agent with QueryIR structured output."""
    return Agent(
        settings.ai_model,
        system_prompt=_SYSTEM_PROMPT,
        output_type=QueryIR,
        model_settings=ModelSettings(
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
        ),
        retries=settings.ai_retries,
        defer_model_check=True,
    )


# Module-level agent (stateless, cheap to reuse — model inference cached).
_intent_agent = build_intent_agent()


async def parse_intent(question: str) -> QueryIR:
    """Parse a natural-language question into a QueryIR.

    This is Step 1 of the TextQL pipeline. The IR is the first-class
    query intent graph consumed by Step 2 (recall), Step 3 (schema
    injection), and Step 4 (tool use / text2sql).

    Raises pydantic_ai exceptions on LLM/validation failure — callers
    should catch and surface a user-friendly error.
    """
    logger.debug("Parsing intent for: %s", question[:80])
    result = await _intent_agent.run(question)
    ir = result.output
    logger.info(
        "Intent parsed: intent_type=%s, objects=%d, properties=%d, filters=%d, derived=%s, multi_step=%s",
        ir.intent_type,
        len(ir.objects),
        len(ir.properties),
        len(ir.filters),
        ir.has_derived_metric,
        ir.intent_type == "multi_step",
    )
    return ir
