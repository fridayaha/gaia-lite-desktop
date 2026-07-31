"""Unit tests for the metadata-layer toolset (ADR-009 / ontology-tool-layer.md §5.1).

Validates that the metadata tools are thin wrappers over OntologyService:
  - list_ontologies_logic / list_object_types / describe_object_type / describe_link_type
  - Correct serialization of pydantic schemas to plain dicts
  - Error envelope propagation ({"error": {"code","message"}}) on Service failure
  - No business logic in the tool layer

Context-scoping (AI-context-scoping RFC): the AG-UI toolset reads
``ctx.deps.ontology`` to default the ``ontology`` arg, and does NOT register
``list_ontologies`` (MCP-only). ``list_ontologies_logic`` is tested directly.

ObjectQueryService is not exercised here (metadata tools don't use it).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic_ai import RunContext

from ontology.config.container import Container
from ontology.core.exceptions import NotFoundError
from ontology.core.schemas.ontology import (
    DataType,
    LinkTypeDef,
    ObjectType,
    Ontology,
    PropertyDef,
)
from ontology.services.ontology_service import OntologyService
from ontology.tools import ToolExecutor, build_metadata_toolset
from ontology.tools.state import AppState
from ontology.tools.toolsets.metadata import (
    describe_link_type_logic,
    describe_ontology_logic,
    list_object_types_logic,
    list_ontologies_logic,
)

_NOW = datetime.now(UTC)


def _make_ontology(api_name: str = "manufacturing") -> Ontology:
    return Ontology(
        id="o1",
        api_name=api_name,
        display_name=api_name.title(),
        description="test ontology",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_object_type(
    api_name: str = "Order",
    storage_type: str = "MANAGED",
    primary_key: str = "order_no",
) -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        description="",
        primary_key=primary_key,
        title_property=primary_key,
        storage_type=storage_type,  # type: ignore[arg-type]
        properties=[
            PropertyDef(
                id="p1",
                object_type_id="ot1",
                api_name=primary_key,
                display_name=primary_key,
                data_type=DataType.STRING,
                is_primary_key=True,
                indexed=True,
                created_at=_NOW,
                updated_at=_NOW,
            ),
            PropertyDef(
                id="p2",
                object_type_id="ot1",
                api_name="amount",
                display_name="amount",
                data_type=DataType.DECIMAL,
                indexed=True,
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ],
        links=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _make_link_type(api_name: str = "has_customer") -> LinkTypeDef:
    return LinkTypeDef(
        id="lt1",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        description="",
        source_object_type_id="ot1",
        target_object_type_id="ot2",
        foreign_key_property_api_name="customer_no",
        cardinality="MANY",
        direction="OUTGOING",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def executor_with_mock_ontology() -> tuple[ToolExecutor, AsyncMock]:
    """Build a ToolExecutor whose container.ontology_service is mocked."""
    container = Container()
    mock_svc = AsyncMock(spec=OntologyService)
    container.service_overrides["ontology_service"] = mock_svc
    return ToolExecutor(container), mock_svc


def _ctx(ontology: str = "") -> RunContext[AppState]:
    """Build a minimal RunContext with the given ontology in deps."""
    return RunContext[AppState](
        deps=AppState(ontology=ontology),
        model=None,  # type: ignore[arg-type]
        usage=None,  # type: ignore[arg-type]
        prompt=None,  # type: ignore[arg-type]
        messages=[],
        tool_call_id="test",
        retry=0,
        run_step=0,
        tool_name="test",
    )


def _get_tool(toolset: Any, name: str) -> Any:
    """Pull a registered tool callable off the FunctionToolset."""
    return toolset.tools[name].function


# ── list_ontologies (logic, MCP-only) ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_ontologies_serializes_schemas(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_ontologies.return_value = [_make_ontology("mfg"), _make_ontology("sc")]

    result = await list_ontologies_logic(executor)

    assert [o["api_name"] for o in result] == ["mfg", "sc"]
    assert result[0]["display_name"] == "Mfg"
    assert result[0]["description"] == "test ontology"
    mock_svc.list_ontologies.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_ontologies_propagates_error_envelope(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_ontologies.side_effect = RuntimeError("db down")

    result = await list_ontologies_logic(executor)

    assert result and isinstance(result, list)
    assert result[0]["error"]["code"] == "INTERNAL_ERROR"


# ── list_object_types ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_object_types_includes_storage_type(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_object_types.return_value = [
        _make_object_type("Order", "MANAGED"),
        _make_object_type("Supplier", "VIRTUAL"),
    ]
    ts = build_metadata_toolset(executor)

    # AG-UI tool: defaults ontology from ctx.deps.ontology.
    result = await _get_tool(ts, "list_object_types")(_ctx("manufacturing"))

    assert {o["api_name"]: o["storage_type"] for o in result} == {
        "Order": "MANAGED",
        "Supplier": "VIRTUAL",
    }
    mock_svc.list_object_types.assert_awaited_once_with("manufacturing")


@pytest.mark.asyncio
async def test_list_object_types_explicit_arg_overrides_ctx(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    """An explicit ontology arg wins over ctx.deps.ontology."""
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_object_types.return_value = []
    ts = build_metadata_toolset(executor)

    await _get_tool(ts, "list_object_types")(_ctx("manufacturing"), "other")

    mock_svc.list_object_types.assert_awaited_once_with("other")


@pytest.mark.asyncio
async def test_list_object_types_uses_logic_directly(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_object_types.return_value = [_make_object_type("Order", "MANAGED")]

    result = await list_object_types_logic(executor, "manufacturing")

    assert [o["api_name"] for o in result] == ["Order"]
    mock_svc.list_object_types.assert_awaited_once_with("manufacturing")


# ── describe_object_type ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_object_type_exposes_primary_key_and_filterable(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.get_object_type.return_value = _make_object_type()
    ts = build_metadata_toolset(executor)

    result = await _get_tool(ts, "describe_object_type")(_ctx("manufacturing"), object_type="Order")

    assert result["primary_key"] == "order_no"
    assert result["storage_type"] == "MANAGED"
    pk_prop = next(p for p in result["properties"] if p["is_primary_key"])
    assert pk_prop["api_name"] == "order_no"
    assert pk_prop["filterable"] is True  # indexed or primary key
    amount_prop = next(p for p in result["properties"] if p["api_name"] == "amount")
    assert amount_prop["data_type"] == "DECIMAL"
    mock_svc.get_object_type.assert_awaited_once_with("manufacturing", "Order")


@pytest.mark.asyncio
async def test_describe_object_type_not_found_envelope(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.get_object_type.side_effect = NotFoundError("ObjectType", "Missing")
    ts = build_metadata_toolset(executor)

    result = await _get_tool(ts, "describe_object_type")(_ctx("manufacturing"), object_type="Missing")

    assert result["error"]["code"] == "OBJECT_NOT_FOUND"


# ── describe_link_type ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_link_type_returns_cardinality_and_direction(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_link_types.return_value = [_make_link_type("has_customer")]
    ts = build_metadata_toolset(executor)

    result = await _get_tool(ts, "describe_link_type")(_ctx("manufacturing"), link_type="has_customer")

    assert result["api_name"] == "has_customer"
    assert result["cardinality"] == "MANY"
    assert result["direction"] == "OUTGOING"
    assert result["directional"] is True


@pytest.mark.asyncio
async def test_describe_link_type_missing_returns_not_found(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_link_types.return_value = [_make_link_type("has_customer")]
    ts = build_metadata_toolset(executor)

    result = await _get_tool(ts, "describe_link_type")(_ctx("manufacturing"), link_type="nope")

    assert result["error"]["code"] == "OBJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_describe_link_type_logic_directly(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.list_link_types.return_value = [_make_link_type("has_customer")]

    result = await describe_link_type_logic(executor, "manufacturing", "has_customer")

    assert result["api_name"] == "has_customer"


# ── toolset registration ─────────────────────────────────────────────────


def test_metadata_toolset_registers_three_tools_no_list_ontologies(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    """AG-UI toolset registers 3 tools; list_ontologies + describe_ontology are MCP-only.

    describe_ontology is NOT on the AG-UI path (ADR-020): the built-in Agent
    already receives the ontology structure via build_ontology_summary text
    injection, so exposing describe_ontology would only induce redundant
    full-payload calls. It is MCP + REST only.
    """
    executor, _ = executor_with_mock_ontology
    ts = build_metadata_toolset(executor)

    assert set(ts.tools.keys()) == {
        "list_object_types",
        "describe_object_type",
        "describe_link_type",
    }
    assert "list_ontologies" not in ts.tools
    assert "describe_ontology" not in ts.tools


# ── describe_ontology (logic, MCP + REST only — not AG-UI) ───────────────


def _make_full_metadata() -> Any:
    """Build a minimal OntologyFullMetadata for the logic-wrapper test."""
    from ontology.core.schemas.ontology import (
        ActionTypeSummary,
        ObjectTypeFullMetadata,
        OntologyFullMetadata,
    )

    return OntologyFullMetadata(
        ontology=_make_ontology(),
        object_types={
            "Order": ObjectTypeFullMetadata(
                api_name="Order",
                display_name="Order",
                primary_key="order_no",
                title_property="order_no",
                storage_type="MANAGED",
                outbound_links=["has_customer"],
                actions=["cancel_order"],
            )
        },
        link_types={},
        action_types={
            "cancel_order": ActionTypeSummary(
                api_name="cancel_order",
                display_name="Cancel Order",
                affected_object_type_api_name="Order",
            )
        },
        partial=True,
        omitted=["interfaces"],
    )


@pytest.mark.asyncio
async def test_describe_ontology_logic_delegates_to_assemble(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    """describe_ontology_logic is a thin wrapper over service.assemble_ontology_metadata."""
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.assemble_ontology_metadata = AsyncMock(return_value=_make_full_metadata())

    result = await describe_ontology_logic(executor, "sales")

    mock_svc.assemble_ontology_metadata.assert_awaited_once_with("sales")
    assert result["ontology"]["api_name"] == "manufacturing"
    assert "Order" in result["object_types"]
    assert result["object_types"]["Order"]["outbound_links"] == ["has_customer"]
    assert result["object_types"]["Order"]["actions"] == ["cancel_order"]
    assert "cancel_order" in result["action_types"]
    assert result["partial"] is True
    assert result["omitted"] == ["interfaces"]


@pytest.mark.asyncio
async def test_describe_ontology_logic_propagates_error_envelope(
    executor_with_mock_ontology: tuple[ToolExecutor, AsyncMock],
) -> None:
    """A NotFound ontology surfaces as an error envelope, not an exception."""
    executor, mock_svc = executor_with_mock_ontology
    mock_svc.assemble_ontology_metadata = AsyncMock(side_effect=RuntimeError("db down"))

    result = await describe_ontology_logic(executor, "nope")

    assert isinstance(result, dict)
    assert result["error"]["code"] == "INTERNAL_ERROR"
