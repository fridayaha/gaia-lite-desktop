"""Unit tests for write/action toolset ontology defaulting (AG-UI path).

Validates the ``ontology or ctx.deps.ontology`` fallback added to the
``@ts.tool`` wrappers in ``write.py`` / ``action.py``. Without this fallback,
the LLM frequently passes ``ontology=""`` and the user approves a request
that then fails with NotFoundError — a bad "approve then fail" UX.

The fallback mirrors the read-only toolsets (``metadata.py`` etc.):
``ontology = ontology or ctx.deps.ontology`` so the LLM can omit the ontology
arg and the request-scoped deps (set from ``forwardedProps.ontology`` in
``/ai/agent``) fills it in.

These tests exercise the ``@ts.tool`` wrappers (not the ``*_logic`` functions,
which are protocol-agnostic and tested in ``test_write_logic.py``). We pull
the tool callable off the FunctionToolset and invoke it with a RunContext
carrying ``AppState(ontology=...)``, asserting the underlying logic receives
the resolved ontology.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic_ai import RunContext

from ontology.config.container import Container
from ontology.core.schemas.ontology import ObjectType
from ontology.services.ontology_service import OntologyService
from ontology.tools import build_action_toolset, build_write_toolset
from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState

_NOW = datetime.now(UTC)


def _make_ot(api_name: str = "Coupon") -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name=api_name,
        display_name="优惠券",
        description="",
        primary_key="couponNo",
        title_property="couponNo",
        storage_type="MANAGED",
        properties=[],
        links=[],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _ctx(ontology: str = "", executor: ToolExecutor | None = None) -> RunContext[AppState]:
    """Build a minimal RunContext with the given ontology + executor in deps.

    write/action toolsets read the request-scoped executor from
    ``ctx.deps.executor`` (not from a constructor arg, unlike the read-only
    toolsets). So the executor must be on the deps.
    """
    return RunContext[AppState](
        deps=AppState(ontology=ontology, executor=executor or ToolExecutor(Container())),
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


def _executor_with_mock_ontology() -> tuple[ToolExecutor, AsyncMock]:
    container = Container()
    mock_svc = AsyncMock(spec=OntologyService)
    mock_svc.define_object_type_batch.return_value = _make_ot()
    container.service_overrides["ontology_service"] = mock_svc  # type: ignore[index]
    return ToolExecutor(container), mock_svc


# ── define_object_type ──


@pytest.mark.asyncio
async def test_define_object_type_falls_back_to_deps_ontology() -> None:
    """LLM passes ontology="" → resolved from ctx.deps.ontology.

    This is the core UX fix: without the fallback the user approves a
    define_object_type that then fails with NotFoundError. With it, the
    ontology open in the Web UI (ctx.deps.ontology) is used.
    """
    executor, mock_svc = _executor_with_mock_ontology()
    ts = build_write_toolset()

    # LLM omits ontology (passes ""). ctx.deps.ontology = "Marketing".
    await _get_tool(ts, "define_object_type")(
        _ctx("Marketing", executor),
        "",
        "Coupon",
        "优惠券",
        properties=[{"display_name": "券号", "data_type": "STRING", "is_primary_key": True}],
    )

    # The service received the RESOLVED ontology, not "".
    call_args = mock_svc.define_object_type_batch.call_args
    assert call_args.args[0] == "Marketing", "ontology should fall back to ctx.deps.ontology"


@pytest.mark.asyncio
async def test_define_object_type_explicit_ontology_wins() -> None:
    """An explicit ontology arg wins over ctx.deps.ontology."""
    executor, mock_svc = _executor_with_mock_ontology()
    mock_svc.define_object_type.return_value = _make_ot()
    ts = build_write_toolset()

    await _get_tool(ts, "define_object_type")(
        _ctx("Marketing", executor),
        "OtherOntology",
        "Coupon",
        "优惠券",
        primary_key="couponNo",
    )

    # Single-type path (no properties) calls define_object_type, not batch.
    call_args = mock_svc.define_object_type.call_args
    assert call_args.args[0] == "OtherOntology"


# ── add_property / define_link_type / link_dataset ──
# These delegate to ``*_logic`` functions; we patch the logic to capture the
# resolved ontology arg (args[1] = ontology, args[0] = executor).


@pytest.mark.asyncio
async def test_add_property_falls_back_to_deps_ontology() -> None:
    """add_property with ontology="" resolves from ctx.deps.ontology."""
    ts = build_write_toolset()
    with patch("ontology.tools.toolsets.write.add_property_logic", new=AsyncMock()) as mock_logic:
        await _get_tool(ts, "add_property")(_ctx("Marketing"), "", "Order", "折扣", "DECIMAL")
        assert mock_logic.call_args.args[1] == "Marketing"


@pytest.mark.asyncio
async def test_define_link_type_falls_back_to_deps_ontology() -> None:
    """define_link_type with ontology="" resolves from ctx.deps.ontology."""
    ts = build_write_toolset()
    with patch("ontology.tools.toolsets.write.define_link_type_logic", new=AsyncMock()) as mock_logic:
        await _get_tool(ts, "define_link_type")(
            _ctx("Marketing"),
            "",
            "属于客户",
            source_object_type="Order",
            target_object_type="Customer",
        )
        assert mock_logic.call_args.args[1] == "Marketing"


@pytest.mark.asyncio
async def test_link_dataset_falls_back_to_deps_ontology() -> None:
    """link_dataset with ontology="" resolves from ctx.deps.ontology."""
    ts = build_write_toolset()
    with patch("ontology.tools.toolsets.write.link_dataset_logic", new=AsyncMock()) as mock_logic:
        await _get_tool(ts, "link_dataset")(
            _ctx("Marketing"),
            "",
            "Order",
            "order_raw_table",
            [{"property": "order_no", "column": "order_id"}],
        )
        assert mock_logic.call_args.args[1] == "Marketing"


# ── action toolset ──


@pytest.mark.asyncio
async def test_invoke_action_falls_back_to_deps_ontology() -> None:
    """invoke_action with ontology="" resolves from ctx.deps.ontology."""
    ts = build_action_toolset()
    with patch("ontology.tools.toolsets.action.invoke_action_logic", new=AsyncMock()) as mock_logic:
        await _get_tool(ts, "invoke_action")(
            _ctx("Marketing"), "", "Order", "update_note", parameters={"order_no": "PO-001"}
        )
        assert mock_logic.call_args.args[1] == "Marketing"


@pytest.mark.asyncio
async def test_validate_action_falls_back_to_deps_ontology() -> None:
    """validate_action with ontology="" resolves from ctx.deps.ontology."""
    ts = build_action_toolset()
    with patch("ontology.tools.toolsets.action.validate_action_logic", new=AsyncMock()) as mock_logic:
        await _get_tool(ts, "validate_action")(
            _ctx("Marketing"), "", "Order", "cancel_order", parameters={"order_no": "PO-001"}
        )
        assert mock_logic.call_args.args[1] == "Marketing"
