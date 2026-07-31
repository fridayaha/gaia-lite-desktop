"""Unit tests for AI suggestion schemas — ai.py.

Covers AiPropertySuggestion, AiLinkSuggestion, AiObjectTypeSuggestion,
AiGenerateRequest, AiGenerateResponse, and SuggestionDataType enum.
"""

import pytest

from ontology.core.schemas.ai import (
    AiGenerateRequest,
    AiGenerateResponse,
    AiLinkSuggestion,
    AiObjectTypeSuggestion,
    AiPropertySuggestion,
    SuggestionDataType,
)


class TestSuggestionDataType:
    """Enum values for data types the LLM can suggest."""

    def test_standard_types(self):
        assert SuggestionDataType.STRING == "STRING"
        assert SuggestionDataType.INTEGER == "INTEGER"
        assert SuggestionDataType.BOOLEAN == "BOOLEAN"
        assert SuggestionDataType.DECIMAL == "DECIMAL"
        assert SuggestionDataType.TIMESTAMP == "TIMESTAMP"

    def test_all_types_present(self):
        """All expected data types are defined."""
        expected = {
            "STRING",
            "INTEGER",
            "LONG",
            "SHORT",
            "BOOLEAN",
            "FLOAT",
            "DOUBLE",
            "DECIMAL",
            "DATE",
            "TIMESTAMP",
            "ARRAY",
            "STRUCT",
        }
        actual = set(SuggestionDataType.__members__.keys())
        assert expected.issubset(actual)


class TestAiPropertySuggestion:
    """Schema for a single AI-suggested property."""

    def test_minimal_property(self):
        p = AiPropertySuggestion(
            api_name="order_status",
            display_name="订单状态",
            data_type=SuggestionDataType.STRING,
        )
        assert p.api_name == "order_status"
        assert p.display_name == "订单状态"
        assert p.description == ""
        assert p.is_primary_key is False
        assert p.is_title_property is False
        assert p.indexed is False

    def test_primary_key_property(self):
        p = AiPropertySuggestion(
            api_name="id",
            display_name="ID",
            data_type=SuggestionDataType.STRING,
            is_primary_key=True,
        )
        assert p.is_primary_key is True

    def test_title_property(self):
        p = AiPropertySuggestion(
            api_name="name",
            display_name="名称",
            data_type=SuggestionDataType.STRING,
            is_title_property=True,
        )
        assert p.is_title_property is True

    def test_indexed_property(self):
        p = AiPropertySuggestion(
            api_name="created_at",
            display_name="创建时间",
            data_type=SuggestionDataType.TIMESTAMP,
            indexed=True,
        )
        assert p.indexed is True

    def test_decimal_for_money(self):
        p = AiPropertySuggestion(
            api_name="price",
            display_name="价格",
            data_type=SuggestionDataType.DECIMAL,
        )
        assert p.data_type == SuggestionDataType.DECIMAL

    def test_full_property(self):
        p = AiPropertySuggestion(
            api_name="employee_name",
            display_name="员工姓名",
            description="Full name of the employee",
            data_type=SuggestionDataType.STRING,
            is_primary_key=False,
            is_title_property=True,
            indexed=True,
        )
        assert p.description == "Full name of the employee"


class TestAiLinkSuggestion:
    """Schema for a relationship suggested by AI."""

    def test_minimal_link(self):
        link = AiLinkSuggestion(
            api_name="belongs_to_customer",
            display_name="所属客户",
            target_object_type="customer",
        )
        assert link.api_name == "belongs_to_customer"
        assert link.target_object_type == "customer"
        assert link.cardinality == "ONE"

    def test_many_cardinality(self):
        link = AiLinkSuggestion(
            api_name="has_orders",
            display_name="拥有订单",
            target_object_type="order",
            cardinality="MANY",
        )
        assert link.cardinality == "MANY"

    def test_invalid_cardinality(self):
        with pytest.raises(ValueError):
            AiLinkSuggestion(
                api_name="bad_link",
                display_name="Bad",
                target_object_type="x",
                cardinality="INVALID",  # type: ignore[arg-type]
            )


class TestAiObjectTypeSuggestion:
    """Schema for a complete AI-suggested object type."""

    def test_minimal_object_type(self):
        ot = AiObjectTypeSuggestion(
            api_name="work_order",
            display_name="工单",
            description="A work order for tracking tasks",
            properties=[
                AiPropertySuggestion(
                    api_name="id",
                    display_name="ID",
                    description="Primary key",
                    data_type=SuggestionDataType.STRING,
                    is_primary_key=True,
                ),
            ],
        )
        assert ot.api_name == "work_order"
        assert ot.display_name == "工单"
        assert ot.description != ""
        assert ot.storage_type == "MANAGED"
        assert len(ot.properties) == 1
        assert ot.links == []

    def test_virtual_object_type(self):
        ot = AiObjectTypeSuggestion(
            api_name="active_orders",
            display_name="活跃订单",
            description="Virtual view of active orders",
            storage_type="VIRTUAL",
            properties=[
                AiPropertySuggestion(
                    api_name="order_id",
                    display_name="订单ID",
                    description="Order identifier",
                    data_type=SuggestionDataType.STRING,
                    is_primary_key=True,
                ),
            ],
        )
        assert ot.storage_type == "VIRTUAL"

    def test_with_links(self):
        ot = AiObjectTypeSuggestion(
            api_name="order",
            display_name="订单",
            description="An order in the system",
            properties=[
                AiPropertySuggestion(
                    api_name="id",
                    display_name="ID",
                    description="Primary key",
                    data_type=SuggestionDataType.STRING,
                    is_primary_key=True,
                ),
            ],
            links=[
                AiLinkSuggestion(
                    api_name="belongs_to_customer",
                    display_name="所属客户",
                    target_object_type="customer",
                ),
            ],
        )
        assert len(ot.links) == 1
        assert ot.links[0].target_object_type == "customer"

    def test_must_have_at_least_one_property(self):
        with pytest.raises(ValueError, match="at least 1 item"):
            AiObjectTypeSuggestion(
                api_name="empty",
                display_name="Empty",
                description="Should fail",
                properties=[],
            )

    def test_invalid_storage_type(self):
        with pytest.raises(ValueError):
            AiObjectTypeSuggestion(
                api_name="bad",
                display_name="Bad",
                storage_type="BOGUS",  # type: ignore[arg-type]
                properties=[
                    AiPropertySuggestion(
                        api_name="id",
                        display_name="ID",
                        data_type=SuggestionDataType.STRING,
                        is_primary_key=True,
                    ),
                ],
            )


class TestAiGenerateRequest:
    """Request schema for AI generation endpoint."""

    def test_valid_request(self):
        req = AiGenerateRequest(description="汽车制造领域，需要管理车型配置")
        assert req.description == "汽车制造领域，需要管理车型配置"

    def test_minimum_length(self):
        with pytest.raises(ValueError):
            AiGenerateRequest(description="ab")  # < 3 chars


class TestAiGenerateResponse:
    """Response schema for AI generation endpoint."""

    def test_empty_suggestions(self):
        resp = AiGenerateResponse(suggestions=[])
        assert resp.suggestions == []

    def test_with_suggestions(self):
        resp = AiGenerateResponse(
            suggestions=[
                AiObjectTypeSuggestion(
                    api_name="car",
                    display_name="车型",
                    description="A car model in the manufacturing system",
                    properties=[
                        AiPropertySuggestion(
                            api_name="car_id",
                            display_name="车型ID",
                            description="Car identifier",
                            data_type=SuggestionDataType.STRING,
                            is_primary_key=True,
                        ),
                    ],
                ),
            ],
        )
        assert len(resp.suggestions) == 1
        assert resp.suggestions[0].api_name == "car"
