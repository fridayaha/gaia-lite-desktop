"""Unit tests for write-tool shared logic (ADR-010, pydantic-ai 2.0 native HITL).

Validates the "shared logic + dual exposure" pattern:
  - ``define_object_type_logic`` runs the Service call via ``execute_write``.
    On the AG-UI path (no handler) it executes immediately — HITL is owned
    by pydantic-ai ``requires_approval`` + AGUIAdapter interrupt/resume, so
    the tool body only runs after the user approves the batch.
  - impact summary is built correctly (captured via execute_write spy).
  - plain create vs batch create (with properties) routing.

Uses a mocked OntologyService so no real DB is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ontology.config.container import Container
from ontology.core.schemas.ontology import ObjectType
from ontology.services.ontology_service import OntologyService
from ontology.tools import define_object_type_logic
from ontology.tools.executor import ToolExecutor

_NOW = datetime.now(UTC)


def _make_ot(api_name: str = "OrderItem") -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name=api_name,
        display_name="订单明细",
        description="",
        primary_key="item_id",
        title_property="item_name",
        storage_type="MANAGED",
        properties=[],
        links=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def container_with_mock_ontology() -> tuple[Container, AsyncMock]:
    container = Container()
    mock_svc = AsyncMock(spec=OntologyService)
    mock_svc.define_object_type.return_value = _make_ot()
    container.service_overrides["ontology_service"] = mock_svc  # type: ignore[index]
    return container, mock_svc


@pytest.mark.asyncio
async def test_write_logic_agui_path_executes_directly(
    container_with_mock_ontology: tuple[Container, AsyncMock],
) -> None:
    """AG-UI path (no handler): the write executes immediately. HITL is
    owned by pydantic-ai requires_approval (the tool body only runs after
    the user approves the batch via AG-UI interrupt/resume)."""
    container, mock_svc = container_with_mock_ontology
    executor = ToolExecutor(container)  # no handler = AG-UI path

    result = await define_object_type_logic(
        executor,
        "mfg",
        "OrderItem",
        "订单明细",
        primary_key="itemId",
        title_property="itemName",
    )

    assert result["status"] == "created"
    assert result["api_name"] == "OrderItem"
    mock_svc.define_object_type.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_logic_impact_includes_managed_side_effects(
    container_with_mock_ontology: tuple[Container, AsyncMock],
) -> None:
    """MANAGED storage_type impact mentions the Doris pipeline side effect.

    Impact is constructed inside the logic and passed to execute_write; it
    isn't returned to the caller, so we spy on execute_write to capture it."""
    container, _ = container_with_mock_ontology
    executor = ToolExecutor(container)
    executor.execute_write = AsyncMock(side_effect=executor.execute_write)  # type: ignore[method-assign]

    await define_object_type_logic(
        executor,
        "mfg",
        "OrderItem",
        "订单明细",
        primary_key="itemId",
        title_property="itemName",
        storage_type="MANAGED",
    )

    # execute_write(tool_name, args, risk_level, impact, ...)
    impact_arg = executor.execute_write.call_args.args[3]
    assert "Doris" in impact_arg
    assert "MANAGED" in impact_arg


@pytest.mark.asyncio
async def test_write_logic_plain_create_reports_zero_properties(
    container_with_mock_ontology: tuple[Container, AsyncMock],
) -> None:
    """Plain create (no properties) returns properties_created=0 and routes
    to define_object_type (not the batch variant)."""
    container, mock_svc = container_with_mock_ontology
    executor = ToolExecutor(container)

    result = await define_object_type_logic(
        executor,
        "mfg",
        "OrderItem",
        "订单明细",
        primary_key="itemId",
        title_property="itemName",
    )

    assert result["status"] == "created"
    assert result["api_name"] == "OrderItem"
    assert result["properties_created"] == 0
    mock_svc.define_object_type.assert_awaited_once()
    mock_svc.define_object_type_batch.assert_not_called()


@pytest.mark.asyncio
async def test_write_logic_with_properties_uses_batch_create(
    container_with_mock_ontology: tuple[Container, AsyncMock],
) -> None:
    """Passing properties routes to define_object_type_batch (not the plain
    define_object_type), and returns properties_created count."""
    container, mock_svc = container_with_mock_ontology
    mock_svc.define_object_type_batch.return_value = _make_ot()
    executor = ToolExecutor(container)

    result = await define_object_type_logic(
        executor,
        "mfg",
        "OrderItem",
        "订单明细",
        primary_key="itemId",
        title_property="itemName",
        storage_type="MANAGED",
        properties=[
            {"display_name": "明细编号", "data_type": "STRING", "is_primary_key": True},
            {"display_name": "数量", "data_type": "INTEGER"},
        ],
    )

    assert result["status"] == "created"
    assert result["properties_created"] == 2
    mock_svc.define_object_type_batch.assert_awaited_once()
    mock_svc.define_object_type.assert_not_called()
