"""Unit tests for SchemaInjector — Step 3 deterministic schema injection."""

from __future__ import annotations

from datetime import UTC, datetime

from ontology.core.schemas.ontology import ObjectType, PropertyDef
from ontology.core.schemas.textql import (
    CandidateObjectType,
    RecallResult,
)
from ontology.services.textql.schema_injector import SchemaInjector


def _prop(api: str, display: str, desc: str = "", dtype: str = "STRING") -> PropertyDef:
    return PropertyDef(
        id=f"p-{api}",
        object_type_id="ot-1",
        api_name=api,
        display_name=display,
        description=desc,
        data_type=dtype,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _ot(api: str, display: str, props: list[PropertyDef]) -> ObjectType:
    return ObjectType(
        id=f"ot-{api}",
        ontology_id="ont-1",
        api_name=api,
        display_name=display,
        description=f"{display}对象",
        primary_key="id",
        title_property="name",
        storage_type="MANAGED",
        properties=props,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestBuildContextBlock:
    def test_renders_object_type_with_properties(self) -> None:
        order = _ot("Order", "订单", [_prop("amount", "金额", "订单金额", "DECIMAL")])
        injector = SchemaInjector()
        recall = RecallResult(object_types=[CandidateObjectType(api_name="Order", display_name="订单", confidence=1.0)])
        block = injector.build_context_block([order], recall)
        assert "ObjectType: Order" in block
        assert "displayName: 订单" in block
        assert "amount (DECIMAL" in block
        assert "displayName=金额" in block
        assert "订单金额" in block  # description rendered

    def test_header_includes_guardrail_instruction(self) -> None:
        order = _ot("Order", "订单", [])
        injector = SchemaInjector()
        recall = RecallResult(object_types=[CandidateObjectType(api_name="Order", display_name="订单", confidence=1.0)])
        block = injector.build_context_block([order], recall)
        assert "禁止编造" in block
        assert "api_name" in block

    def test_recall_candidates_ordered_first(self) -> None:
        """High-confidence recall candidates appear before filler OTs."""
        order = _ot("Order", "订单", [])
        customer = _ot("Customer", "客户", [])
        product = _ot("Product", "产品", [])
        injector = SchemaInjector()
        # Recall only matched Customer; Order/Product are filler.
        recall = RecallResult(
            object_types=[CandidateObjectType(api_name="Customer", display_name="客户", confidence=1.0)]
        )
        block = injector.build_context_block([order, customer, product], recall)
        cust_pos = block.index("ObjectType: Customer")
        order_pos = block.index("ObjectType: Order")
        assert cust_pos < order_pos  # recall candidate first

    def test_caps_injected_object_types(self) -> None:
        """MAX_INJECT_OBJECT_TYPES bounds token usage."""
        from ontology.services.textql.schema_injector import MAX_INJECT_OBJECT_TYPES

        ots = [_ot(f"OT{i}", f"对象{i}", []) for i in range(MAX_INJECT_OBJECT_TYPES + 5)]
        injector = SchemaInjector()
        recall = RecallResult(object_types=[])
        block = injector.build_context_block(ots, recall)
        # Count ObjectType headers — should be capped.
        assert block.count("## ObjectType:") == MAX_INJECT_OBJECT_TYPES

    def test_empty_properties_rendered(self) -> None:
        order = _ot("Order", "订单", [])
        injector = SchemaInjector()
        recall = RecallResult(object_types=[CandidateObjectType(api_name="Order", display_name="订单", confidence=1.0)])
        block = injector.build_context_block([order], recall)
        assert "(无属性)" in block

    def test_property_constraints_rendered(self) -> None:
        """required / PK / title constraints shown."""
        pk_prop = PropertyDef(
            id="p-id",
            object_type_id="ot-1",
            api_name="orderId",
            display_name="订单号",
            data_type="STRING",
            is_primary_key=True,
            nullable=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        order = _ot("Order", "订单", [pk_prop])
        injector = SchemaInjector()
        recall = RecallResult(object_types=[CandidateObjectType(api_name="Order", display_name="订单", confidence=1.0)])
        block = injector.build_context_block([order], recall)
        assert "[required, PK]" in block

    def test_no_recall_candidates_fills_from_all_ots(self) -> None:
        """When recall found nothing, inject first N OTs as context."""
        order = _ot("Order", "订单", [_prop("amount", "金额")])
        injector = SchemaInjector()
        recall = RecallResult(object_types=[])  # no candidates
        block = injector.build_context_block([order], recall)
        assert "ObjectType: Order" in block
