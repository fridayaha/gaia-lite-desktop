"""Unit tests for query_with_sql_logic (ADR-012 path B + D7 session-leak fix).

Regression: query_with_sql_logic used to build a MetaStoreSchemaProvider from
``container.metadata`` (deprecated property that leaks an unclosed AsyncSession
per call). Under密集 Agent tool calls this exhausted the QueuePool (size 20 +
overflow 10). The fix passes compiler=None to ``execute_compiled_sql``, which
builds the provider from the service's cached ``self._metadata`` instead.

These tests verify:
  - query_with_sql_logic does NOT touch ``container.metadata`` (the leaky path)
  - it calls ``execute_compiled_sql(ontology, sql)`` with no compiler arg
  - empty SQL returns a structured error without hitting the service
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.tools.toolsets.object_query import query_with_sql_logic


def _executor_with_mock_service(rows: list[dict[str, Any]] | None = None) -> MagicMock:
    """Build an executor whose container.object_query_service is a mock that
    records how execute_compiled_sql was called."""
    exe = MagicMock()
    exe.container = MagicMock()

    svc = MagicMock()
    svc.execute_compiled_sql = AsyncMock(
        return_value=rows if rows is not None else [{"a": 1}]
    )
    exe.container.object_query_service = svc

    # The deprecated container.metadata property MUST NOT be accessed — we
    # make it raise if touched, so any regression to the leaky path fails loud.
    raise_if_touched = MagicMock()
    type(exe.container).metadata = property(  # type: ignore[misc]
        lambda self: (_ for _ in ()).throw(
            AssertionError("container.metadata must not be accessed (D7 leak)")
        )
    )
    _ = raise_if_touched  # silence unused

    async def audit_call(_name: str, _params: dict, coro: Any) -> Any:
        return await coro

    exe.audit_call = audit_call
    return exe


class TestQueryWithSqlNoSessionLeak:
    @pytest.mark.asyncio
    async def test_does_not_access_container_metadata(self) -> None:
        """修复回归：query_with_sql_logic 不应访问 container.metadata（泄漏源）。"""
        exe = _executor_with_mock_service(rows=[{"x": 1}])

        result = await query_with_sql_logic(exe, "SC", "SELECT * FROM Supplier")

        assert result == {"data": [{"x": 1}], "row_count": 1}
        # execute_compiled_sql 被调用，且 compiler 参数为 None（内部自建 provider）
        svc = exe.container.object_query_service
        svc.execute_compiled_sql.assert_awaited_once()
        call_args = svc.execute_compiled_sql.call_args
        # 只传 ontology + sql，不传 compiler
        assert call_args.args[0] == "SC"
        assert call_args.args[1] == "SELECT * FROM Supplier"
        assert "compiler" not in call_args.kwargs or call_args.kwargs["compiler"] is None

    @pytest.mark.asyncio
    async def test_empty_sql_returns_error_without_service_call(self) -> None:
        """空 SQL 不应触达 service（也不应访问 metadata）。"""
        exe = _executor_with_mock_service()

        result = await query_with_sql_logic(exe, "SC", "   ")

        assert result == {"error": {"code": "EMPTY_SQL", "message": "sql must not be empty"}}
        exe.container.object_query_service.execute_compiled_sql.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_service_error_surfaces_structured_envelope(self) -> None:
        """Service 抛错时返回 {error: {code, message}}，不向上抛。"""
        exe = _executor_with_mock_service()
        exe.container.object_query_service.execute_compiled_sql = AsyncMock(
            side_effect=RuntimeError("doris down")
        )

        result = await query_with_sql_logic(exe, "SC", "SELECT * FROM Supplier")

        assert "error" in result
        # RuntimeError 无 code 属性 → 降级 SQL_EXECUTION_ERROR
        assert result["error"]["code"] in ("SQL_EXECUTION_ERROR", "INTERNAL_ERROR")
        assert "doris down" in result["error"]["message"]

    @pytest.mark.asyncio
    async def test_ontology_error_code_preserved(self) -> None:
        """OntologyError 的 code 应透传到 error envelope。"""
        from ontology.core.exceptions import OntologyError

        exe = _executor_with_mock_service()
        exe.container.object_query_service.execute_compiled_sql = AsyncMock(
            side_effect=OntologyError("no such table", code="INVALID_TABLE")
        )

        result = await query_with_sql_logic(exe, "SC", "SELECT * FROM Ghost")

        assert result["error"]["code"] == "INVALID_TABLE"
