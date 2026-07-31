"""Unit tests for TextQL orchestrator (Step 1-3 wiring + ontology summary)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from ontology.core.schemas.ontology import ActionType, LinkTypeDef, ObjectType, PropertyDef
from ontology.services.textql.orchestrator import build_injected_schema, build_ontology_summary


def _prop(
    api: str,
    display: str,
    *,
    data_type: str = "STRING",
    pk: bool = False,
    title: bool = False,
    nullable: bool = True,
) -> PropertyDef:
    return PropertyDef(
        id=f"p-{api}",
        object_type_id="ot-1",
        api_name=api,
        display_name=display,
        data_type=data_type,  # type: ignore[arg-type]
        is_primary_key=pk,
        is_title_property=title,
        nullable=nullable,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _ot(api: str, display: str) -> ObjectType:
    return ObjectType(
        id=f"ot-{api}",
        ontology_id="ont-1",
        api_name=api,
        display_name=display,
        description=f"{display}对象",
        primary_key="id",
        title_property="name",
        storage_type="MANAGED",
        properties=[_prop("name", "名称", title=True)],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _link(
    api: str,
    display: str,
    *,
    src_id: str,
    tgt_id: str,
    fk: str | None,
    cardinality: str = "MANY",
    direction: str = "OUTGOING",
    description: str = "",
) -> LinkTypeDef:
    return LinkTypeDef(
        id=f"lt-{api}",
        ontology_id="ont-1",
        api_name=api,
        display_name=display,
        description=description,
        source_object_type_id=src_id,
        target_object_type_id=tgt_id,
        foreign_key_property_api_name=fk,
        cardinality=cardinality,  # type: ignore[arg-type]
        direction=direction,  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_container(
    ots: list[ObjectType],
    links: list[LinkTypeDef] | None = None,
    actions: list[ActionType] | None = None,
) -> MagicMock:
    """Build a mock container backing BOTH build_ontology_summary and
    build_injected_schema.

    Since ADR-020, build_ontology_summary renders from the shared
    assemble_ontology_metadata aggregate (via container.ontology_service),
    while the legacy build_injected_schema still reads container.metadata_session
    directly. The fixture wires both: a real OntologyService over a mocked
    metadata layer (for the aggregate path) + the metadata_session async
    context manager (for the legacy TextQL path).
    """
    from ontology.core.schemas.ontology import Ontology
    from ontology.services.ontology_service import OntologyService

    meta = AsyncMock()
    meta.get_ontology = AsyncMock(
        return_value=Ontology(
            id="ont-1",
            api_name="Airline",
            display_name="Airline",
            description="",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    meta.list_object_types = AsyncMock(return_value=ots)
    meta.get_link_types = AsyncMock(return_value=links or [])
    meta.list_action_types = AsyncMock(return_value=actions or [])
    meta.get_interface_types = AsyncMock(return_value=[])

    svc = OntologyService.__new__(OntologyService)
    svc._metadata = meta  # type: ignore[attr-defined]

    container = MagicMock()
    container.ontology_service = svc
    # Legacy metadata_session path (build_injected_schema) — async ctx mgr.
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=meta)
    session_ctx.__aexit__ = AsyncMock(return_value=None)
    container.metadata_session = MagicMock(return_value=session_ctx)
    return container


class TestBuildOntologySummary:
    """Lightweight schema summary injected on every /ai/agent run (ADR-009).

    The summary must carry enough for the Agent to write SQL and call
    traverse_link in a single turn — without it the ReAct loop falls back to
    list_object_types / describe_object_type / list_link_types round-trips
    (one extra LLM turn each), which is the root cause of the graph-explore
    latency. These tests pin the rendered fields so a regression that drops
    types / links / cardinality is caught before it silently re-adds round-trips.
    """

    async def test_empty_ontology_returns_empty(self) -> None:
        """No ontology scoped → no summary (agent runs without one)."""
        container = _make_container([_ot("Order", "订单")])
        block = await build_ontology_summary(container, "")
        assert block == ""

    async def test_no_object_types_returns_empty(self) -> None:
        container = _make_container([])
        block = await build_ontology_summary(container, "Airline")
        assert block == ""

    async def test_metadata_failure_returns_empty(self) -> None:
        """DB failure is non-fatal — empty block, agent still runs."""
        container = _make_container([_ot("Order", "订单")])
        container.ontology_service.assemble_ontology_metadata = AsyncMock(
            side_effect=RuntimeError("DB down")
        )
        block = await build_ontology_summary(container, "Airline")
        assert block == ""

    async def test_object_types_render_with_types_and_pk_title_markers(self) -> None:
        """OT block carries data_type + pk/title markers so the Agent can write
        SQL without calling describe_object_type (the redundant round-trip)."""
        order = ObjectType(
            id="ot-order",
            ontology_id="ont-1",
            api_name="Order",
            display_name="订单",
            description="客户订单",
            primary_key="orderId",
            title_property="orderNo",
            storage_type="MANAGED",
            properties=[
                _prop("orderId", "订单ID", data_type="STRING", pk=True, nullable=False),
                _prop("orderNo", "订单号", data_type="STRING", title=True),
                _prop("amount", "金额", data_type="DECIMAL"),
                _prop("createdAt", "创建时间", data_type="TIMESTAMP"),
            ],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        container = _make_container([order])
        block = await build_ontology_summary(container, "Airline")
        # Object section header carries pk + title markers.
        assert "### Order (订单)[pk=orderId] [title=orderNo]" in block
        assert "客户订单" in block  # description
        # Each property shows its data_type + marker.
        assert "- orderId: STRING (pk, not null)" in block
        assert "- orderNo: STRING (title)" in block
        assert "- amount: DECIMAL" in block
        assert "- createdAt: TIMESTAMP" in block

    async def test_links_render_with_source_target_fk_and_cardinality(self) -> None:
        """Link block renders source→target api_names (not UUIDs), the FK
        property, and a human cardinality label — everything traverse_link
        needs in one turn, eliminating describe_link_type round-trips."""
        order = ObjectType(
            id="ot-order",
            ontology_id="ont-1",
            api_name="Order",
            display_name="订单",
            primary_key="orderId",
            title_property="orderNo",
            storage_type="MANAGED",
            properties=[_prop("customerId", "客户ID", data_type="STRING")],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        customer = ObjectType(
            id="ot-cust",
            ontology_id="ont-1",
            api_name="Customer",
            display_name="客户",
            primary_key="customerId",
            title_property="name",
            storage_type="MANAGED",
            properties=[_prop("customerId", "客户ID", data_type="STRING", pk=True)],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        # MANY + OUTGOING = FK on source (Order.customerId → Customer) = 多对一.
        belongs = _link(
            "belongs_to",
            "属于",
            src_id="ot-order",
            tgt_id="ot-cust",
            fk="customerId",
            cardinality="MANY",
            direction="OUTGOING",
            description="订单所属的客户",
        )
        container = _make_container([order, customer], [belongs])
        block = await build_ontology_summary(container, "Airline")
        assert "## 关联关系（LinkType）" in block
        # source→target rendered as api_names, not UUIDs.
        assert "Order → Customer" in block
        assert "via customerId" in block
        assert "多对一" in block  # cardinality label, not raw "MANY"
        assert "订单所属的客户" in block  # description
        # Link api_name is present so the Agent can pass it to traverse_link.
        assert "belongs_to" in block
        assert "ot-order" not in block and "ot-cust" not in block  # UUIDs not leaked

    async def test_one_to_many_cardinality_label(self) -> None:
        """MANY + INCOMING = FK on target (source is the "one" side) = 一对多."""
        customer = ObjectType(
            id="ot-cust",
            ontology_id="ont-1",
            api_name="Customer",
            display_name="客户",
            primary_key="customerId",
            title_property="name",
            storage_type="MANAGED",
            properties=[_prop("customerId", "客户ID", data_type="STRING", pk=True)],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        order = ObjectType(
            id="ot-order",
            ontology_id="ont-1",
            api_name="Order",
            display_name="订单",
            primary_key="orderId",
            title_property="orderNo",
            storage_type="MANAGED",
            properties=[_prop("customerId", "客户ID", data_type="STRING")],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        placed = _link(
            "placed",
            "下单",
            src_id="ot-cust",
            tgt_id="ot-order",
            fk="customerId",
            cardinality="MANY",
            direction="INCOMING",
        )
        container = _make_container([customer, order], [placed])
        block = await build_ontology_summary(container, "Airline")
        assert "Customer → Order" in block
        assert "一对多" in block

    async def test_one_to_one_cardinality_label(self) -> None:
        """cardinality=ONE always renders as 一对一 regardless of direction."""
        a = ObjectType(
            id="ot-a",
            ontology_id="ont-1",
            api_name="Profile",
            display_name="档案",
            primary_key="profileId",
            title_property="profileId",
            storage_type="MANAGED",
            properties=[_prop("profileId", "档案ID", data_type="STRING", pk=True)],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        b = ObjectType(
            id="ot-b",
            ontology_id="ont-1",
            api_name="User",
            display_name="用户",
            primary_key="userId",
            title_property="userId",
            storage_type="MANAGED",
            properties=[_prop("profileId", "档案ID", data_type="STRING")],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        link = _link(
            "has_profile",
            "档案",
            src_id="ot-b",
            tgt_id="ot-a",
            fk="profileId",
            cardinality="ONE",
            direction="OUTGOING",
        )
        container = _make_container([a, b], [link])
        block = await build_ontology_summary(container, "Airline")
        assert "一对一" in block

    async def test_no_links_omits_link_section(self) -> None:
        """When an ontology has OTs but no LinkTypes, the link section is absent
        (not an empty header) — keeps the prompt tight for ontology-modelling."""
        container = _make_container([_ot("Order", "订单")], [])
        block = await build_ontology_summary(container, "Airline")
        assert "## 关联关系" not in block
        assert "### Order (订单)" in block

    async def test_link_with_unknown_ot_id_falls_back_to_uuid(self) -> None:
        """A LinkType whose source/target OT was soft-deleted (so not in the
        list) renders the raw UUID instead of crashing — best-effort, the
        Agent can still call describe_link_type for that one edge case."""
        order = _ot("Order", "订单")  # id = "ot-Order"
        # source resolves to api_name; target not in the list → UUID stays.
        link = _link("belongs_to", "属于", src_id=order.id, tgt_id="ghost-id", fk="customerId")
        container = _make_container([order], [link])
        block = await build_ontology_summary(container, "Airline")
        assert "Order → ghost-id" in block

    async def test_summary_tells_agent_not_to_re_explore(self) -> None:
        """The summary ends with an instruction telling the Agent the listed
        info is enough for basic queries — directly targets the redundant
        describe_* round-trips (the graph-explore latency root cause)."""
        container = _make_container([_ot("Order", "订单")])
        block = await build_ontology_summary(container, "Airline")
        assert "不要为了写基础查询而反复探索" in block

    async def test_actions_render_with_target_and_risk(self) -> None:
        """ADR-020: the summary includes an Action概要 section so the built-in
        Agent's awareness of available actions matches the MCP describe_ontology
        payload (ADR-019 capability parity). Full parameters are NOT injected —
        only api_name + display_name + target + risk + description."""
        order = ObjectType(
            id="ot-order",
            ontology_id="ont-1",
            api_name="Order",
            display_name="订单",
            primary_key="orderId",
            title_property="orderId",
            storage_type="MANAGED",
            properties=[_prop("orderId", "订单ID", data_type="STRING", pk=True)],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        cancel = ActionType(
            id="at-cancel",
            ontology_id="ont-1",
            api_name="cancel_order",
            display_name="取消订单",
            description="取消一个订单",
            affected_object_type_id="ot-order",
            risk_level="medium",
            operation_kind="update",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        container = _make_container([order], actions=[cancel])
        block = await build_ontology_summary(container, "Airline")
        assert "## 可用动作（ActionType）" in block
        assert "cancel_order (取消订单)" in block
        assert "作用于 Order" in block  # target OT rendered as api_name
        assert "[risk=medium]" in block  # non-low risk flagged
        assert "取消一个订单" in block  # description
        # Full parameters schema is NOT in the summary (call validate_action).
        assert "parameters" not in block.lower()

    async def test_no_actions_omits_action_section(self) -> None:
        """An ontology with OTs but no ActionTypes omits the action section
        (not an empty header) — keeps the prompt tight for read-only work."""
        container = _make_container([_ot("Order", "订单")])
        block = await build_ontology_summary(container, "Airline")
        assert "## 可用动作" not in block


class TestBuildInjectedSchema:
    """Step 1-3 orchestrator wiring."""

    async def test_empty_ontology_returns_empty(self) -> None:
        """No ontology scoped → no injection (agent runs without schema)."""
        container = _make_container([_ot("Order", "订单")])
        block = await build_injected_schema(container, "", "查订单")
        assert block == ""

    async def test_empty_message_returns_empty(self) -> None:
        container = _make_container([_ot("Order", "订单")])
        block = await build_injected_schema(container, "Airline", "")
        assert block == ""

    async def test_no_object_types_returns_empty(self) -> None:
        container = _make_container([])
        block = await build_injected_schema(container, "Airline", "查订单")
        assert block == ""

    async def test_full_pipeline_produces_schema_block(self) -> None:
        """Step 1-3 happy path: IR → recall → injection produces a block."""
        from ontology.core.schemas.textql import (
            ObjectRef,
            PropertyRef,
            QueryIR,
        )

        container = _make_container([_ot("Order", "订单")])
        ir = QueryIR(
            raw_query="查订单",
            intent_type="query",
            objects=[ObjectRef(name="订单")],
            properties=[PropertyRef(name="名称")],
        )
        with patch("ontology.services.textql.orchestrator.parse_intent", new=AsyncMock(return_value=ir)):
            block = await build_injected_schema(container, "Airline", "查订单")
        assert "ObjectType: Order" in block
        assert "禁止编造" in block
        assert "名称" in block  # property display name

    async def test_intent_parse_failure_falls_back_to_full_schema(self) -> None:
        """Step 1 LLM failure is non-fatal — inject full ontology schema."""
        container = _make_container([_ot("Order", "订单"), _ot("Customer", "客户")])
        with patch(
            "ontology.services.textql.orchestrator.parse_intent",
            new=AsyncMock(side_effect=RuntimeError("LLM down")),
        ):
            block = await build_injected_schema(container, "Airline", "查订单")
        # Fall-back injects all OTs (no recall narrowing).
        assert "ObjectType: Order" in block
        assert "ObjectType: Customer" in block

    async def test_metadata_failure_returns_empty(self) -> None:
        """Any pipeline failure is non-fatal — empty block, agent still runs."""
        container = _make_container([_ot("Order", "订单")])
        # Make metadata_session throw.
        container.metadata_session = MagicMock(side_effect=RuntimeError("DB down"))
        block = await build_injected_schema(container, "Airline", "查订单")
        assert block == ""

    async def test_block_contains_guardrail_header(self) -> None:
        """Injected block always carries the '禁止编造' guardrail instruction."""
        from ontology.core.schemas.textql import ObjectRef, QueryIR

        container = _make_container([_ot("Order", "订单")])
        ir = QueryIR(raw_query="查订单", intent_type="query", objects=[ObjectRef(name="订单")])
        with patch("ontology.services.textql.orchestrator.parse_intent", new=AsyncMock(return_value=ir)):
            block = await build_injected_schema(container, "Airline", "查订单")
        assert "本体 Schema" in block
        assert "禁止编造" in block
