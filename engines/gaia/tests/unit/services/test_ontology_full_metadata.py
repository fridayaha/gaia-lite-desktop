"""Unit tests for OntologyService.assemble_ontology_metadata (ADR-020).

The aggregate is the single source of truth shared by:
  - ``describe_ontology`` tool (MCP + REST) — returns the structured payload
  - ``build_ontology_summary`` (AG-UI text injection) — renders it to markdown

These tests pin the assembly contract: it must (1) load all four entity types
in one call, (2) attach inbound/outbound links + actions to each ObjectType,
(3) key everything by api_name, and (4) be best-effort — a failing entity
type is recorded in ``omitted`` rather than raising.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ontology.core.schemas.ontology import (
    ActionType,
    LinkTypeDef,
    ObjectType,
    Ontology,
    OntologyFullMetadata,
    PropertyDef,
)
from ontology.services.ontology_service import OntologyService

_NOW = datetime.now(UTC)


def _ontology() -> Ontology:
    return Ontology(
        id="o1",
        api_name="sales",
        display_name="Sales",
        description="sales ontology",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _ot(api_name: str, ot_id: str, *, storage_type: str = "MANAGED") -> ObjectType:
    return ObjectType(
        id=ot_id,
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        description="",
        primary_key="id",
        title_property="name",
        storage_type=storage_type,  # type: ignore[arg-type]
        properties=[
            PropertyDef(
                id=f"p-{api_name}",
                object_type_id=ot_id,
                api_name="id",
                display_name="id",
                data_type="STRING",
                is_primary_key=True,
                created_at=_NOW,
                updated_at=_NOW,
            )
        ],
        links=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _link(api_name: str, src_id: str, tgt_id: str) -> LinkTypeDef:
    return LinkTypeDef(
        id=f"lt-{api_name}",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        description="",
        source_object_type_id=src_id,
        target_object_type_id=tgt_id,
        foreign_key_property_api_name="fk",
        cardinality="MANY",
        direction="OUTGOING",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _action(api_name: str, affected_ot_id: str | None) -> ActionType:
    return ActionType(
        id=f"at-{api_name}",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        description="an action",
        affected_object_type_id=affected_ot_id,
        risk_level="medium",
        operation_kind="update",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _service(
    *,
    ontology: Ontology | None = None,
    ots: list[ObjectType] | None = None,
    links: list[LinkTypeDef] | None = None,
    actions: list[ActionType] | None = None,
) -> tuple[OntologyService, AsyncMock]:
    """Build an OntologyService with a mocked metadata layer."""
    meta = AsyncMock()
    meta.get_ontology = AsyncMock(return_value=ontology or _ontology())
    meta.list_object_types = AsyncMock(return_value=ots or [])
    meta.get_link_types = AsyncMock(return_value=links or [])
    meta.list_action_types = AsyncMock(return_value=actions or [])
    meta.get_interface_types = AsyncMock(return_value=[])

    svc = OntologyService.__new__(OntologyService)
    svc._metadata = meta  # type: ignore[attr-defined]
    return svc, meta


# ── happy path ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assemble_returns_full_metadata_with_all_entity_types() -> None:
    customer = _ot("Customer", "ot-c")
    order = _ot("Order", "ot-o")
    link = _link("has_order", "ot-c", "ot-o")  # Customer → Order
    action = _action("cancel_order", "ot-o")
    svc, _meta = _service(ots=[customer, order], links=[link], actions=[action])

    result = await svc.assemble_ontology_metadata("sales")

    assert isinstance(result, OntologyFullMetadata)
    assert result.ontology.api_name == "sales"
    assert set(result.object_types) == {"Customer", "Order"}
    assert set(result.link_types) == {"has_order"}
    assert set(result.action_types) == {"cancel_order"}
    assert result.partial is False
    assert result.omitted == []


@pytest.mark.asyncio
async def test_assemble_keys_everything_by_api_name() -> None:
    svc, _meta = _service(ots=[_ot("Customer", "ot-c")])

    result = await svc.assemble_ontology_metadata("sales")

    assert "Customer" in result.object_types
    # LinkType retained as full LinkTypeDef (not a summary) — consumers need
    # source/target/cardinality/fk without a second call.
    assert all(isinstance(v, LinkTypeDef) for v in result.link_types.values())


# ── inbound/outbound links attached per ObjectType ───────────────────────


@pytest.mark.asyncio
async def test_assemble_attaches_outbound_and_inbound_links() -> None:
    customer = _ot("Customer", "ot-c")
    order = _ot("Order", "ot-o")
    # Customer → Order (outbound for Customer, inbound for Order)
    link = _link("has_order", "ot-c", "ot-o")
    svc, _meta = _service(ots=[customer, order], links=[link])

    result = await svc.assemble_ontology_metadata("sales")

    assert result.object_types["Customer"].outbound_links == ["has_order"]
    assert result.object_types["Customer"].inbound_links == []
    assert result.object_types["Order"].inbound_links == ["has_order"]
    assert result.object_types["Order"].outbound_links == []


@pytest.mark.asyncio
async def test_assemble_attaches_actions_to_affected_object_type() -> None:
    order = _ot("Order", "ot-o")
    action = _action("cancel_order", "ot-o")
    # An action with no affected OT (e.g. cross-type) attaches to nothing.
    orphan = _action("global_reindex", None)
    svc, _meta = _service(ots=[order], actions=[action, orphan])

    result = await svc.assemble_ontology_metadata("sales")

    assert result.object_types["Order"].actions == ["cancel_order"]
    # The orphan action is still in the top-level map (discoverable) but not
    # attached to any OT.
    assert "global_reindex" in result.action_types


# ── best-effort: a failing entity type is omitted, not raised ────────────


@pytest.mark.asyncio
async def test_assemble_is_best_effort_on_action_failure() -> None:
    svc, meta = _service(ots=[_ot("Customer", "ot-c")])
    meta.list_action_types = AsyncMock(side_effect=RuntimeError("action table locked"))

    result = await svc.assemble_ontology_metadata("sales")

    assert result.partial is True
    assert "action_types" in result.omitted
    # object_types still loaded
    assert "Customer" in result.object_types
    assert result.action_types == {}


@pytest.mark.asyncio
async def test_assemble_is_best_effort_on_interface_failure() -> None:
    svc, meta = _service(ots=[_ot("Customer", "ot-c")])
    meta.get_interface_types = AsyncMock(side_effect=RuntimeError("iface locked"))

    result = await svc.assemble_ontology_metadata("sales")

    assert result.partial is True
    assert "interfaces" in result.omitted
    assert result.interfaces == []


@pytest.mark.asyncio
async def test_assemble_propagates_ontology_not_found() -> None:
    """A missing ontology is a real error (404), not a best-effort omission."""
    from ontology.core.exceptions import NotFoundError

    svc, meta = _service()
    meta.get_ontology = AsyncMock(side_effect=NotFoundError("Ontology", "nope"))

    with pytest.raises(NotFoundError):
        await svc.assemble_ontology_metadata("nope")
