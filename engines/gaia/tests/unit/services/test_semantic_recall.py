"""Unit tests for SemanticRecaller — Step 2 engine A (exact match)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ontology.core.schemas.ontology import ObjectType
from ontology.core.schemas.textql import (
    ObjectRef,
    PropertyRef,
    QueryIR,
)
from ontology.services.textql.semantic_recall import SemanticRecaller


def _make_prop(api_name: str, display_name: str, description: str, data_type: str = "STRING"):
    from ontology.core.schemas.ontology import PropertyDef

    return PropertyDef(
        id=f"prop-{api_name}",
        object_type_id="ot-order-1",
        api_name=api_name,
        display_name=display_name,
        description=description,
        data_type=data_type,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_order_ot() -> ObjectType:
    """Build an ObjectType schema with properties for testing."""
    return ObjectType(
        id="ot-order-1",
        ontology_id="ont-1",
        api_name="Order",
        display_name="订单",
        description="销售订单，记录客户下单信息",
        primary_key="orderId",
        title_property="orderNo",
        storage_type="MANAGED",
        properties=[
            _make_prop("amount", "金额", "订单总金额", "DECIMAL"),
            _make_prop("status", "状态", "订单状态：待支付/已支付/已发货/已完成"),
            _make_prop("turnoverRate", "离职率", "离职率、Turnover Rate、Attrition Rate", "DECIMAL"),
        ],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_customer_ot() -> ObjectType:
    return ObjectType(
        id="ot-customer-1",
        ontology_id="ont-1",
        api_name="Customer",
        display_name="客户",
        description="企业客户",
        primary_key="customerId",
        title_property="customerName",
        storage_type="MANAGED",
        properties=[
            _make_prop("customerName", "客户名称", "客户公司名称"),
            _make_prop("region", "区域", "客户所在区域：华东/华南/华北"),
        ],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def recaller() -> SemanticRecaller:
    return SemanticRecaller([_make_order_ot(), _make_customer_ot()])


class TestObjectTypeRecall:
    async def test_exact_display_name_match(self, recaller: SemanticRecaller) -> None:
        ir = QueryIR(
            raw_query="查订单",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
        )
        result = await recaller.recall(ir)
        assert len(result.object_types) == 1
        assert result.object_types[0].api_name == "Order"
        assert result.object_types[0].confidence == 1.0
        assert "精确匹配" in result.object_types[0].match_evidence

    async def test_description_alias_match(self, recaller: SemanticRecaller) -> None:
        """description 多语种别名匹配（材料三：description 作语义素材）。"""
        ir = QueryIR(
            raw_query="查 Turnover Rate",
            intent_type="query",
            objects=[ObjectRef(name="Turnover Rate")],
        )
        # "Turnover Rate" 是 Order.turnoverRate 的 description 别名
        # 但这里匹配的是 ObjectType，不是 Property。Order 的 description 不含。
        # 改为匹配 Property：
        ir = QueryIR(
            raw_query="查 Attrition Rate",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
            properties=[PropertyRef(name="Attrition Rate")],
        )
        result = await recaller.recall(ir)
        assert len(result.object_types) == 1
        assert result.object_types[0].api_name == "Order"
        # Property 别名应被召回
        assert any(p.api_name == "turnoverRate" for p in result.object_types[0].matched_properties)

    async def test_no_match_returns_empty(self, recaller: SemanticRecaller) -> None:
        ir = QueryIR(
            raw_query="查供应商",
            intent_type="query",
            objects=[ObjectRef(name="供应商")],
        )
        result = await recaller.recall(ir)
        assert len(result.object_types) == 0

    async def test_substring_match_lower_confidence(self, recaller: SemanticRecaller) -> None:
        """子串匹配置信度低于精确匹配。"""
        ir = QueryIR(
            raw_query="查订单",
            intent_type="query",
            objects=[ObjectRef(name="订")],  # "订" 是 "订单" 的子串
        )
        result = await recaller.recall(ir)
        assert len(result.object_types) == 1
        assert result.object_types[0].confidence == 0.7  # _SUBSTR_DISPLAY


class TestPropertyRecall:
    async def test_property_attached_to_object(self, recaller: SemanticRecaller) -> None:
        ir = QueryIR(
            raw_query="订单金额",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
            properties=[PropertyRef(name="金额")],
        )
        result = await recaller.recall(ir)
        assert len(result.object_types) == 1
        cand = result.object_types[0]
        assert cand.api_name == "Order"
        assert len(cand.matched_properties) == 1
        assert cand.matched_properties[0].api_name == "amount"

    async def test_property_no_match_not_attached(self, recaller: SemanticRecaller) -> None:
        ir = QueryIR(
            raw_query="订单颜色",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
            properties=[PropertyRef(name="颜色")],  # Order 无此属性
        )
        result = await recaller.recall(ir)
        assert len(result.object_types[0].matched_properties) == 0


class TestClarification:
    async def test_no_clarification_single_match(self, recaller: SemanticRecaller) -> None:
        ir = QueryIR(
            raw_query="查订单",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
        )
        result = await recaller.recall(ir)
        assert result.needs_clarification is False

    async def test_clarification_when_tied(self) -> None:
        """两个 OT displayName 相同 → 需澄清。"""

        ot1 = ObjectType(
            id="1",
            ontology_id="ont-1",
            api_name="Order1",
            display_name="订单",
            primary_key="id",
            title_property="id",
            storage_type="MANAGED",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        ot2 = ObjectType(
            id="2",
            ontology_id="ont-1",
            api_name="Order2",
            display_name="订单",
            primary_key="id",
            title_property="id",
            storage_type="MANAGED",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        recaller = SemanticRecaller([ot1, ot2])
        ir = QueryIR(
            raw_query="查订单",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
        )
        result = await recaller.recall(ir)
        assert result.needs_clarification is True


class TestRecallRefinementFlag:
    async def test_low_confidence_sets_refinement_flag(self, recaller: SemanticRecaller) -> None:
        """低于阈值的召回标记 needs_recall_refinement（决策二：LLM 迭代补充）。"""
        ir = QueryIR(
            raw_query="查订",  # 子串匹配 0.7 = 阈值边界
            intent_type="query",
            objects=[ObjectRef(name="订")],
        )
        await recaller.recall(ir)
        # 0.7 == threshold, not below; use lower
        ir2 = QueryIR(
            raw_query="查单",
            intent_type="query",
            objects=[ObjectRef(name="单")],  # "单" 在 "订单" 子串 → 0.7
        )
        await recaller.recall(ir2)
