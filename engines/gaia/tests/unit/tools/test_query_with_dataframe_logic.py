"""query_with_dataframe_logic cursor pagination + audit coverage.

These tests pin the ADR-019 §4 contract: the ``cursor`` parameter is a
first-class argument on ``query_with_dataframe_logic`` (not just on the
REST route), so MCP / AG-UI / REST all share one pagination path. Before
this change cursor lived only on the REST route — MCP and AG-UI clients
got ``truncated=true`` with no way to fetch the next page.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.object_set import ReasoningResult
from ontology.tools.executor import ToolExecutor
from ontology.tools.toolsets.reasoning import query_with_dataframe_logic


def _make_executor(result: ReasoningResult) -> tuple[ToolExecutor, AsyncMock, AsyncMock]:
    """Build an executor whose container.dataframe_query_service.execute
    returns ``result`` and records how it was called."""
    container = MagicMock()
    svc = AsyncMock()
    svc.execute = AsyncMock(return_value=result)
    container.dataframe_query_service = svc

    executor = ToolExecutor(container)

    # audit_call just awaits the passed coroutine and returns its result.
    async def _audit(name: str, ctx: dict[str, Any], coro: Any) -> Any:
        return await coro

    executor.audit_call = _audit  # type: ignore[method-assign]
    return executor, svc, AsyncMock()


@pytest.mark.asyncio
async def test_cursor_is_forwarded_to_service_execute() -> None:
    """cursor passed to query_with_dataframe_logic reaches svc.execute as
    cursor=... — not dropped at the logic boundary."""
    result = ReasoningResult(objects=[], aggregates=[], truncated=True, next_cursor="vid-99")
    executor, svc, _ = _make_executor(result)

    await query_with_dataframe_logic(
        executor,
        "SupplyChain",
        {"type": "objectType", "object_type": "Order"},
        cursor="vid-50",
    )

    svc.execute.assert_awaited_once()
    # svc.execute(ir, ontology, cursor=cursor) — cursor is a kwarg.
    assert svc.execute.call_args.kwargs["cursor"] == "vid-50"


@pytest.mark.asyncio
async def test_cursor_defaults_to_none_when_omitted() -> None:
    """Omitting cursor (the MCP/AG-UI common case) passes cursor=None to
    svc.execute — equivalent to starting from the beginning."""
    result = ReasoningResult(objects=[], aggregates=[], truncated=False)
    executor, svc, _ = _make_executor(result)

    await query_with_dataframe_logic(
        executor,
        "SupplyChain",
        {"type": "objectType", "object_type": "Order"},
    )

    assert svc.execute.call_args.kwargs["cursor"] is None


@pytest.mark.asyncio
async def test_next_cursor_propagated_in_return_dict() -> None:
    """When svc.execute returns a next_cursor (truncated result), the logic
    function surfaces it in the returned dict so MCP/AG-UI callers can pass
    it back as cursor on the next call."""
    result = ReasoningResult(objects=[], aggregates=[], truncated=True, next_cursor="vid-99")
    executor, _, _ = _make_executor(result)

    out = await query_with_dataframe_logic(
        executor,
        "SupplyChain",
        {"type": "objectType", "object_type": "Order"},
    )

    assert out["truncated"] is True
    assert out["next_cursor"] == "vid-99"


@pytest.mark.asyncio
async def test_cursor_recorded_in_audit_context() -> None:
    """The audit_call context dict includes the cursor value — so paged
    queries are auditable (which page was requested). This protects the
    ADR-019 §4 audit-parity guarantee: REST used to bypass audit entirely
    (called svc.execute directly); now all three entry points go through
    query_with_dataframe_logic → audit_call."""
    result = ReasoningResult(objects=[], aggregates=[], truncated=False)
    container = MagicMock()
    svc = AsyncMock()
    svc.execute = AsyncMock(return_value=result)
    container.dataframe_query_service = svc
    executor = ToolExecutor(container)

    captured: dict[str, Any] = {}

    async def _audit(name: str, ctx: dict[str, Any], coro: Any) -> Any:
        captured.update(ctx)
        return await coro

    executor.audit_call = _audit  # type: ignore[method-assign]

    await query_with_dataframe_logic(
        executor,
        "SupplyChain",
        {"type": "objectType", "object_type": "Order"},
        cursor="vid-50",
    )

    assert captured["cursor"] == "vid-50"
    assert captured["ontology"] == "SupplyChain"
