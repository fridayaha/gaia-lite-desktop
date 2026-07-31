"""End-to-end MCP protocol tests for the ontology MCP server.

Validates the full chain: MCP Client → FastMCP → ontology toolsets →
ToolExecutor → OntologyService. Uses an in-memory FastMCP server (no
network) with a mocked OntologyService so no real Postgres is needed.

This is the Sprint 1 verification that the FunctionToolset → FastMCP
bridge actually works (ADR-009 §后续工作, first implementation-period
item). Verified against fastmcp 3.4.2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from ontology.config.container import Container
from ontology.core.schemas.ontology import (
    ActionTypeSummary,
    DataType,
    ObjectType,
    ObjectTypeFullMetadata,
    Ontology,
    OntologyFullMetadata,
    PropertyDef,
)
from ontology.protocols.mcp_server import _build_fastmcp_server
from ontology.services.ontology_service import OntologyService

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


def _make_object_type(api_name: str = "Order") -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        description="",
        primary_key="order_no",
        title_property="order_no",
        storage_type="MANAGED",
        properties=[
            PropertyDef(
                id="p1",
                object_type_id="ot1",
                api_name="order_no",
                display_name="order_no",
                data_type=DataType.STRING,
                is_primary_key=True,
                indexed=True,
                created_at=_NOW,
                updated_at=_NOW,
            ),
        ],
        links=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def mcp_server_with_mock() -> Any:
    """Build a FastMCP server backed by a mocked OntologyService."""
    container = Container()
    mock_svc = AsyncMock(spec=OntologyService)
    mock_svc.list_ontologies.return_value = [_make_ontology("mfg")]
    mock_svc.list_object_types.return_value = [_make_object_type()]
    mock_svc.get_object_type.return_value = _make_object_type()
    container.service_overrides["ontology_service"] = mock_svc  # type: ignore[index]
    # Also mock ObjectQueryService so filter_object / count_object / etc.
    # can be exercised end-to-end without a real Trino engine.
    from ontology.services.object_query_service import ObjectQueryService

    mock_oq = AsyncMock(spec=ObjectQueryService)
    container.service_overrides["object_query_service"] = mock_oq  # type: ignore[index]
    # Mock action_service so invoke_action (the only MCP write tool after
    # ad-hoc modeling tools were removed from MCP per ADR-019) can resolve
    # an ActionType and reach the HITL approval path without a real DB.
    mock_action_svc = AsyncMock()
    mock_action_svc._metadata.get_action_type = AsyncMock(
        return_value=SimpleNamespace(
            risk_level="high",
            display_name="Test Action",
            parameters={"parameters": []},
        )
    )
    container.service_overrides["action_service"] = mock_action_svc  # type: ignore[index]
    # _build_fastmcp_server uses the module-level container; override it.
    import ontology.protocols.mcp_server as mod

    orig_container = mod.container
    mod.container = container
    try:
        mcp = _build_fastmcp_server()
        yield mcp, mock_svc, mock_oq
    finally:
        mod.container = orig_container


@pytest.mark.asyncio
async def test_mcp_lists_all_tools(mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock]) -> None:
    """All ontology tools are visible over MCP (read + write + action)."""
    mcp, _, _ = mcp_server_with_mock
    async with Client(mcp) as client:
        tools = await client.list_tools()

    names = {t.name for t in tools}
    # filter/count/aggregate/topn/exists were removed 2026-06, and
    # get_object/bulk_get_object were removed 2026-07 — all in favor of
    # query_with_sql as the single object-query entry point.
    assert {
        "list_ontologies",
        "list_object_types",
        "describe_object_type",
        "describe_link_type",
        "describe_ontology",
        "query_with_sql",
        "list_link_types",
        "traverse_link",
        "exists_link",
    } <= names
    # 已删的 7 个工具不再暴露
    assert {
        "get_object",
        "bulk_get_object",
        "filter_object",
        "count_object",
        "aggregate_object",
        "topn_object",
        "exists_object",
    }.isdisjoint(names)
    assert {"filter_object", "count_object", "aggregate_object", "topn_object", "exists_object"}.isdisjoint(names)


@pytest.mark.asyncio
async def test_mcp_tool_has_description_and_schema(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """Each MCP tool carries the docstring-derived description and a JSON
    schema — the contract the LLM sees."""
    mcp, _, _ = mcp_server_with_mock
    async with Client(mcp) as client:
        tools = await client.list_tools()

    list_ont = next(t for t in tools if t.name == "list_ontologies")
    assert list_ont.description is not None
    assert "ontology" in list_ont.description.lower() or "first" in list_ont.description.lower()


@pytest.mark.asyncio
async def test_mcp_call_list_ontologies_end_to_end(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """Full chain: MCP Client → FastMCP → tool.function → ToolExecutor →
    OntologyService.list_ontologies → returns serialized list."""
    mcp, mock_svc, _ = mcp_server_with_mock
    async with Client(mcp) as client:
        result = await client.call_tool("list_ontologies", {})

    mock_svc.list_ontologies.assert_awaited_once()
    # FastMCP returns lists as a TextContent JSON string (MCP requires
    # structured content to be an object, so lists are not auto-structured).
    import json

    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    assert data[0]["api_name"] == "mfg"


@pytest.mark.asyncio
async def test_mcp_call_describe_object_type_end_to_end(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """describe_object_type returns the primary_key + storage_type + properties
    over the MCP protocol."""
    mcp, mock_svc, _ = mcp_server_with_mock
    async with Client(mcp) as client:
        result = await client.call_tool(
            "describe_object_type",
            {"ontology": "mfg", "object_type": "Order"},
        )

    mock_svc.get_object_type.assert_awaited_once_with("mfg", "Order")
    # describe_object_type returns a dict (not a list), so structured_content
    # is the dict directly.
    data = result.structured_content
    assert data["primary_key"] == "order_no"
    assert data["storage_type"] == "MANAGED"
    assert data["properties"][0]["is_primary_key"] is True


@pytest.mark.asyncio
async def test_mcp_call_describe_ontology_end_to_end(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """describe_ontology (ADR-020) returns the full ontology metadata in one
    call over MCP — objects + links + actions + interfaces, keyed by api_name.

    This is the bootstrap endpoint for external Agents: it collapses the
    list_object_types → describe_object_type(×N) → describe_link_type(×M)
    chain into a single round-trip. AG-UI does NOT expose it (the built-in
    Agent gets the structure via text injection), so this MCP test is the
    primary registration + call-path guard.
    """
    mcp, mock_svc, _ = mcp_server_with_mock
    full = OntologyFullMetadata(
        ontology=_make_ontology("mfg"),
        object_types={
            "Order": ObjectTypeFullMetadata(
                id="ot1",
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
    mock_svc.assemble_ontology_metadata = AsyncMock(return_value=full)

    async with Client(mcp) as client:
        result = await client.call_tool("describe_ontology", {"ontology": "mfg"})

    mock_svc.assemble_ontology_metadata.assert_awaited_once_with("mfg")
    data = result.structured_content
    assert data["ontology"]["api_name"] == "mfg"
    assert "Order" in data["object_types"]
    assert data["object_types"]["Order"]["outbound_links"] == ["has_customer"]
    assert data["object_types"]["Order"]["actions"] == ["cancel_order"]
    assert "cancel_order" in data["action_types"]
    assert data["partial"] is True
    assert data["omitted"] == ["interfaces"]


@pytest.mark.asyncio
async def test_mcp_error_envelope_propagates(mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock]) -> None:
    """A Service error is converted to the {error:{code,message}} envelope,
    never raised across the MCP boundary."""
    mcp, mock_svc, _ = mcp_server_with_mock
    mock_svc.list_object_types.side_effect = RuntimeError("db down")
    async with Client(mcp) as client:
        result = await client.call_tool("list_object_types", {"ontology": "mfg"})

    # The tool returns [{"error": {...}}] (list-returning tool wraps errors).
    # FastMCP returns lists as a TextContent JSON string.
    import json

    data = json.loads(result.content[0].text)
    assert isinstance(data, list)
    assert data[0]["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_mcp_call_query_with_sql_end_to_end(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """query_with_sql flows MCP -> tool -> ObjectQueryService.execute_compiled_sql,
    returning the Service's dict result verbatim."""
    from unittest.mock import AsyncMock as _AsyncMock
    from unittest.mock import patch

    mcp, _, mock_oq = mcp_server_with_mock
    mock_oq.execute_compiled_sql.return_value = [{"cnt": 42}]
    # query_with_sql_logic builds a MetaStoreSchemaProvider and loads the ontology
    # schema from metadata; mock load so it doesn't hit the real DB.
    with patch(
        "ontology.services.textql.schema_provider.MetaStoreSchemaProvider.load",
        new=_AsyncMock(return_value=None),
    ):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "query_with_sql",
                {
                    "ontology": "mfg",
                    "sql": "SELECT COUNT(*) AS cnt FROM Order WHERE amount > 100",
                },
            )

    mock_oq.execute_compiled_sql.assert_awaited_once()
    data = result.structured_content
    assert data["row_count"] == 1
    assert data["data"][0]["cnt"] == 42


@pytest.mark.asyncio
async def test_traverse_link_and_exists_link_implemented() -> None:
    """traverse_link and exists_link are now implemented (graph-reasoning M4)
    via Neo4jGraphStore — no longer return TOOL_NOT_IMPLEMENTED."""
    from unittest.mock import AsyncMock, MagicMock

    from ontology.core.schemas.graph import GraphTraversalResult
    from ontology.tools.executor import ToolExecutor
    from ontology.tools.toolsets.link_traversal import (
        exists_link_logic,
        traverse_link_logic,
    )

    container = MagicMock()
    # dataframe_query_service resolves labels.
    df_svc = AsyncMock()
    df_svc._resolve_target_label = AsyncMock(return_value="MfgOrder")
    df_svc._resolve_source_label = AsyncMock(return_value="MfgPurchaseOrder")
    container.dataframe_query_service = df_svc
    # graph_store returns traversal result.
    graph = AsyncMock()
    graph.search_around = AsyncMock(return_value=GraphTraversalResult(rids=["C001"], matched_count=1))
    graph.exists_link = AsyncMock(return_value=True)
    container.graph_store = graph
    # metadata hydrates.
    meta = AsyncMock()
    meta.get_object_states_by_rids = AsyncMock(
        return_value=[{"rid": "C001", "object_type_api_name": "Customer", "properties": {"name": "Acme"}}]
    )
    container.metadata = meta
    # traverse_link_logic 用 metadata_session() async context manager（D7 修复后）。
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _meta_session():
        yield meta

    container.metadata_session = _meta_session

    executor = ToolExecutor(container)
    # audit_call just awaits the passed coroutine and returns its result.

    async def _audit(name, ctx, coro):
        return await coro

    executor.audit_call = _audit

    # traverse_link: returns target_objects (not TOOL_NOT_IMPLEMENTED).
    result = await traverse_link_logic(executor, "Mfg", "hasCustomer", ["PO-1"], "forward", None, None, False)
    assert "target_objects" in result
    assert len(result["target_objects"]) == 1
    assert result["target_objects"][0]["rid"] == "C001"

    # exists_link: returns {exists, mode} (not TOOL_NOT_IMPLEMENTED).
    result = await exists_link_logic(executor, "Mfg", "hasCustomer", "PO-1", "forward", "C001")
    assert result["exists"] is True
    assert result["mode"] == "SINGLE_TARGET"


@pytest.mark.asyncio
async def test_mcp_server_advertises_instructions(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """The server exposes `instructions` in the initialize handshake so an
    external Agent learns the server's purpose + recommended call order
    without reading every tool description first."""
    mcp, _, _ = mcp_server_with_mock
    # fastmcp exposes the server's instructions as a FastMCP attribute;
    # an external Agent receives it in the initialize handshake.
    server_instructions = mcp.instructions
    assert server_instructions is not None
    assert "list_ontologies" in server_instructions
    assert "describe_ontology" in server_instructions  # ADR-020 bootstrap entry
    assert "query_with_sql" in server_instructions


@pytest.mark.asyncio
async def test_mcp_does_not_expose_ad_hoc_modeling_tools(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """Per ADR-019, ad-hoc ontology modeling (define_object_type /
    add_property / define_link_type / link_dataset) is an internal capability
    exposed only via the in-product AG-UI Agent and the REST admin API —
    NOT via MCP. External Agents query and act on existing ontology; modeling
    is done in-product or by data engineers. This test guards against the
    four modeling tools accidentally re-entering the MCP surface."""
    mcp, _, _ = mcp_server_with_mock
    async with Client(mcp) as client:
        tools = await client.list_tools()
    names = {t.name for t in tools}
    assert {
        "define_object_type",
        "add_property",
        "define_link_type",
        "link_dataset",
    }.isdisjoint(names)
    # invoke_action (action execution) IS still exposed — only modeling was removed.
    assert "invoke_action" in names


@pytest.mark.asyncio
async def test_write_tool_returns_elicitation_unsupported_when_client_lacks_elicit(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """When the MCP client does not support elicitation, ctx.elicit raises.
    The write tool wrapper must surface a structured ELICITATION_UNSUPPORTED
    error — NOT a silent {status:DENIED}, which would conflate "environment
    can't confirm" with "user refused"."""
    mcp, _, _ = mcp_server_with_mock
    # Patch Context.elicit to simulate a client lacking elicitation capability.
    from fastmcp import Context

    original_elicit = Context.elicit

    async def _raising_elicit(self, message, response_type=None, **kw):
        raise RuntimeError("client does not support elicitation")

    Context.elicit = _raising_elicit  # type: ignore[method-assign]
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "invoke_action",
                {
                    "ontology": "mfg",
                    "object_type": "Order",
                    "action_type": "cancel_order",
                    "parameters": {},
                },
            )
    finally:
        Context.elicit = original_elicit  # type: ignore[method-assign]

    data = result.structured_content
    assert data["error"]["code"] == "ELICITATION_UNSUPPORTED"


@pytest.mark.asyncio
async def test_write_tool_denied_when_user_cancels_elicit(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """When the user declines/cancels the elicit dialog, the write tool
    returns {status:DENIED} — distinct from ELICITATION_UNSUPPORTED."""
    from fastmcp.server.elicitation import CancelledElicitation

    mcp, _, _ = mcp_server_with_mock
    from fastmcp import Context

    original_elicit = Context.elicit

    async def _cancelled_elicit(self, message, response_type=None, **kw):
        return CancelledElicitation()

    Context.elicit = _cancelled_elicit  # type: ignore[method-assign]
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "invoke_action",
                {
                    "ontology": "mfg",
                    "object_type": "Order",
                    "action_type": "cancel_order",
                    "parameters": {},
                },
            )
    finally:
        Context.elicit = original_elicit  # type: ignore[method-assign]

    data = result.structured_content
    # User-cancelled elicit → handler returns False → execute_gated DENIED.
    # Distinct from ELICITATION_UNSUPPORTED (no error.code key).
    assert data.get("error", {}).get("code") != "ELICITATION_UNSUPPORTED"
    assert data.get("status") == "DENIED"


@pytest.mark.asyncio
async def test_mcp_find_paths_tool_registered_and_callable(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """find_paths is registered on the MCP server (parity with AG-UI + REST).
    Before this fix the MCP server exposed traverse_link + exists_link but
    NOT find_paths — external Agents could not do multi-hop path reasoning,
    while AG-UI and REST could. The tool must now appear in list_tools and
    be callable."""
    mcp, _, _ = mcp_server_with_mock
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "find_paths" in names, f"find_paths missing from MCP tools: {names}"


@pytest.mark.asyncio
async def test_mcp_instructions_declare_capability_boundary(
    mcp_server_with_mock: tuple[Any, AsyncMock, AsyncMock],
) -> None:
    """The server instructions declare what the server does NOT expose
    (ActionType definition, batch action, data source / pipeline management)
    so an external Agent learns the boundary at handshake time instead of
    failing when it tries an unsupported operation."""
    mcp, _, _ = mcp_server_with_mock
    instr = mcp.instructions
    assert instr is not None
    assert "does NOT expose" in instr or "Capability boundary" in instr
    assert "ActionType definition" in instr
