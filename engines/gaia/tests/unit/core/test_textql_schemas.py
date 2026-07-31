"""Unit tests for TextQL schemas — textql.py.

Covers QueryIR (first-class citizen) and its building blocks, plus the
Step 2 RecallResult. Validates:
- IR can express T1-T9 query types (expressiveness)
- serialization round-trips losslessly (audit persistence)
- business nouns (not api_names) are what IR carries
- derived-metric / window / multi-object fields are wired correctly
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ontology.core.schemas.textql import (
    CandidateObjectType,
    CandidateProperty,
    FilterSpec,
    LinkRef,
    ObjectRef,
    OrderBySpec,
    PropertyRef,
    QueryIR,
    RecallResult,
    WindowSpec,
)


class TestFilterSpec:
    """Filter condition (WHERE/HAVING)."""

    def test_eq_filter(self) -> None:
        f = FilterSpec(subject="状态", op="eq", value="逾期")
        assert f.op == "eq"
        assert f.value == "逾期"

    def test_between_filter_value_is_list(self) -> None:
        f = FilterSpec(subject="下单时间", op="between", value=["2025-04-01", "2025-06-30"])
        assert f.value == ["2025-04-01", "2025-06-30"]

    def test_is_null_ignores_value(self) -> None:
        f = FilterSpec(subject="备注", op="isNull")
        assert f.value is None

    def test_invalid_op_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FilterSpec(subject="x", op="invalid_op")  # type: ignore[arg-type]

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FilterSpec(subject="x", op="eq", value="y", extra="nope")  # type: ignore[call-arg]


class TestPropertyRef:
    """Property reference (SELECT column / metric / derived)."""

    def test_select_role_default(self) -> None:
        p = PropertyRef(name="订单号")
        assert p.role == "select"
        assert p.expr is None

    def test_derived_metric_with_expr(self) -> None:
        p = PropertyRef(name="VIP占比", role="derived", expr="SUM(CASE WHEN level='VIP' THEN 1 ELSE 0 END)/COUNT(*)")
        assert p.role == "derived"
        assert p.expr is not None

    def test_derived_without_expr_allowed(self) -> None:
        """expr is optional even for derived (LLM may omit; compiler fills)."""
        p = PropertyRef(name="占比", role="derived")
        assert p.expr is None


class TestObjectRef:
    """Object-type reference (FROM/JOIN)."""

    def test_primary_default(self) -> None:
        o = ObjectRef(name="订单")
        assert o.is_primary is True

    def test_non_primary(self) -> None:
        o = ObjectRef(name="客户", is_primary=False)
        assert o.is_primary is False


class TestQueryIR:
    """QueryIR — the first-class query intent graph."""

    def test_minimal_query(self) -> None:
        ir = QueryIR(raw_query="查订单", intent_type="query", objects=[ObjectRef(name="订单")])
        assert ir.intent_type == "query"
        assert len(ir.objects) == 1
        assert ir.has_derived_metric is False
        assert ir.needs_recall_refinement is False

    def test_t1_single_table_filter(self) -> None:
        """T1: single-table filter + sort + page."""
        ir = QueryIR(
            raw_query="2025Q2下单的华东区企业客户",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
            properties=[PropertyRef(name="订单号"), PropertyRef(name="客户名称")],
            filters=[
                FilterSpec(subject="下单时间", op="between", value=["2025-04-01", "2025-06-30"]),
                FilterSpec(subject="区域", op="eq", value="华东"),
            ],
            limit=100,
        )
        assert len(ir.filters) == 2
        assert ir.limit == 100

    def test_t2_cross_entity_join(self) -> None:
        """T2: cross-entity JOIN via LinkType."""
        ir = QueryIR(
            raw_query="逾期订单对应的客户负责人",
            intent_type="query",
            objects=[ObjectRef(name="订单"), ObjectRef(name="客户", is_primary=False)],
            links=[LinkRef(from_object="订单", to_object="客户", link_name="属于")],
            properties=[PropertyRef(name="客户负责人"), PropertyRef(name="联系方式")],
            filters=[FilterSpec(subject="状态", op="eq", value="逾期")],
        )
        assert len(ir.objects) == 2
        assert len(ir.links) == 1
        assert ir.links[0].link_name == "属于"

    def test_t5_derived_metric_flag(self) -> None:
        """T5: derived metric (占比) sets has_derived_metric."""
        ir = QueryIR(
            raw_query="VIP客户占比",
            intent_type="aggregate",
            objects=[ObjectRef(name="客户")],
            properties=[
                PropertyRef(name="VIP数", role="metric"),
                PropertyRef(name="客户总数", role="metric"),
                PropertyRef(
                    name="VIP占比", role="derived", expr="SUM(CASE WHEN level='VIP' THEN 1 ELSE 0 END)/COUNT(*)"
                ),
            ],
            group_by=["区域"],
            has_derived_metric=True,
        )
        assert ir.has_derived_metric is True
        assert any(p.role == "derived" for p in ir.properties)

    def test_t7_topn_with_window(self) -> None:
        """T7: TopN + window function."""
        ir = QueryIR(
            raw_query="Top10客户及金额占比",
            intent_type="topn",
            objects=[ObjectRef(name="订单")],
            properties=[
                PropertyRef(name="客户"),
                PropertyRef(name="总金额", role="metric"),
                PropertyRef(name="占比", role="derived", expr="total/SUM(total) OVER()"),
            ],
            order_by=[OrderBySpec(subject="总金额", direction="desc")],
            limit=10,
            windows=[WindowSpec(func="SUM", partition_by=[], alias="total_sum")],
            has_derived_metric=True,
        )
        assert ir.intent_type == "topn"
        assert len(ir.windows) == 1
        assert ir.limit == 10

    def test_t6_multi_step_flag(self) -> None:
        """T6: 同比环比 marked multi_step + needs_recall_refinement."""
        ir = QueryIR(
            raw_query="今年上半年 vs 去年同期销售额对比",
            intent_type="multi_step",
            objects=[ObjectRef(name="订单")],
            properties=[
                PropertyRef(name="销售额", role="metric"),
                PropertyRef(name="同比增长率", role="derived", expr="(cur-prev)/prev"),
            ],
            has_derived_metric=True,
            needs_recall_refinement=True,
        )
        assert ir.intent_type == "multi_step"
        assert ir.needs_recall_refinement is True

    def test_serialization_roundtrip(self) -> None:
        """IR round-trips through JSON losslessly (audit persistence)."""
        ir = QueryIR(
            raw_query="各区域销售额",
            intent_type="aggregate",
            objects=[ObjectRef(name="订单")],
            properties=[PropertyRef(name="销售额", role="metric")],
            group_by=["区域"],
            order_by=[OrderBySpec(subject="销售额", direction="desc")],
        )
        data = ir.model_dump_json()
        restored = QueryIR.model_validate_json(data)
        assert restored.raw_query == ir.raw_query
        assert restored.intent_type == ir.intent_type
        assert [p.name for p in restored.properties] == [p.name for p in ir.properties]
        assert restored.group_by == ir.group_by

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            QueryIR(raw_query="x", intent_type="query", objects=[ObjectRef(name="o")], limit=0)

    def test_offset_must_be_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            QueryIR(raw_query="x", intent_type="query", objects=[ObjectRef(name="o")], offset=-1)

    def test_invalid_intent_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QueryIR(raw_query="x", intent_type="delete", objects=[ObjectRef(name="o")])  # type: ignore[arg-type]

    def test_carries_business_nouns_not_api_names(self) -> None:
        """IR carries Chinese business nouns, never api_names (Step 2's job)."""
        ir = QueryIR(
            raw_query="查航班状态",
            intent_type="query",
            objects=[ObjectRef(name="航班")],
            properties=[PropertyRef(name="航班号"), PropertyRef(name="状态")],
            filters=[FilterSpec(subject="状态", op="eq", value="延误")],
        )
        # All subjects/names are business nouns, not snake_case api_names
        assert ir.objects[0].name == "航班"
        assert all("_" not in p.name for p in ir.properties)
        assert all("_" not in f.subject for f in ir.filters)


class TestRecallResult:
    """Step 2 recall output (backfills api_names)."""

    def test_candidates_default_empty(self) -> None:
        r = RecallResult()
        assert r.object_types == []
        assert r.needs_clarification is False

    def test_with_candidates(self) -> None:
        r = RecallResult(
            object_types=[
                CandidateObjectType(
                    api_name="Order",
                    display_name="订单",
                    confidence=0.95,
                    matched_properties=[
                        CandidateProperty(
                            api_name="amount",
                            display_name="金额",
                            object_type_api_name="Order",
                            confidence=0.9,
                        )
                    ],
                )
            ],
            needs_clarification=False,
        )
        assert len(r.object_types) == 1
        assert r.object_types[0].api_name == "Order"
        assert r.object_types[0].matched_properties[0].api_name == "amount"

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            CandidateObjectType(api_name="X", display_name="x", confidence=1.5)
        with pytest.raises(ValidationError):
            CandidateObjectType(api_name="X", display_name="x", confidence=-0.1)
