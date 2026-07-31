"""Object-query tool (1) — query_with_sql over object instances.

Per docs/architecture/ontology-tool-layer.md §5. The retrieval/aggregation/
filter/count/topn/exists tools were removed (2026-06) and the point-lookup
tools get_object/bulk_get_object were removed (2026-07) — all in favor of a
single ``query_with_sql`` entry point. See the ``query_with_sql`` docstring
for why (column mapping, parameterized binding, Doris-primary routing, and
full SQL expressiveness supersede the per-verb atomic tools; point lookups
are just ``SELECT * FROM <OT> WHERE <pk> = ?`` expressed via the same path).

Remaining tool:
  - query_with_sql: the entry point for ATTRIBUTE-DIMENSION queries —
    filter / count / aggregate / topn / join / window / arithmetic / point
    lookup (text2sql path B, ADR-012). For relationship/spatial/temporal
    queries, use the reasoning toolset's query_with_dataframe instead.

Architecture (docs/architecture/rfcs/AI-context-scoping.md):
  - ``<tool>_logic(executor, ontology, ...)`` is the protocol-agnostic
    single source of truth. MCP exposure calls it directly.
  - ``build_object_query_toolset`` produces the AG-UI exposure: ``@ts.tool``
    wrappers reading ``ctx.deps.ontology`` to default the ``ontology`` arg,
    keeping the assistant inside the current ontology.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets._contracts import QUERY_WITH_SQL_DESC

# ── Shared logic (single source of truth, protocol-agnostic) ─────────────


async def query_with_sql_logic(
    executor: ToolExecutor,
    ontology: str,
    sql: str,
) -> dict[str, Any]:
    """Execute a custom SQL query over ontology objects (text2sql path B).

    The entry point for ATTRIBUTE-DIMENSION queries (filtering, counting,
    aggregating, top-N, JOIN, point lookup by primary key). Express any
    attribute query as logical SQL; this tool compiles it and runs it.
    For relationship/spatial/temporal queries, use query_with_dataframe.

    Protocol-agnostic. The SQL uses ObjectType api_name as table name and
    property api_name as column name; the compiler (ADR-012 Step 4 path B)
    enforces ontology guardrails (table/column/join whitelist) and
    parameterizes literals before execution.

    No ``object_type`` parameter: every ObjectType referenced in the SQL is
    inferred by the compiler and treated uniformly (access-checked,
    storage-routed, column-remapped). The caller never repeats information
    already encoded in the SQL — design decision C ("keep complexity in-house,
    simplicity for the caller").
    """
    if not sql or not sql.strip():
        return {"error": {"code": "EMPTY_SQL", "message": "sql must not be empty"}}

    svc = executor.container.object_query_service
    # NOTE: do NOT build a compiler from ``container.metadata`` here — that
    # deprecated property leaks an unclosed AsyncSession per call (D7), which
    # exhausts the connection pool under密集 Agent tool calls (40+ calls
    # → QueuePool limit). Instead pass compiler=None: execute_compiled_sql
    # builds the provider from the service's cached ``self._metadata``
    # (one session, reused, closed via container.aclose()).
    try:
        rows = await executor.audit_call(
            "query_with_sql",
            {"ontology": ontology, "sql_len": len(sql)},
            svc.execute_compiled_sql(ontology, sql),
        )
    except Exception as exc:  # surface compiler/SQL errors as structured error
        # Preserve OntologyError code if present; else generic.
        code = getattr(exc, "code", "SQL_EXECUTION_ERROR")
        return {"error": {"code": code, "message": str(exc)}}
    return {"data": rows, "row_count": len(rows)}


# ── AG-UI exposure (pydantic-ai toolset, reads ctx.deps.ontology) ────────


def build_object_query_toolset(executor: ToolExecutor) -> FunctionToolset[AppState]:
    """Build the object-query toolset (query_with_sql) for the AG-UI path.

    Each ``@ts.tool`` wrapper reads ``ctx.deps.ontology`` (the ontology the
    user has open in the Web UI) and defaults the ``ontology`` arg to it when
    the LLM omits it — so queries stay inside the current ontology. See
    docs/architecture/rfcs/AI-context-scoping.md.
    """
    ts: FunctionToolset[AppState] = FunctionToolset()

    @ts.tool(description=QUERY_WITH_SQL_DESC)
    async def query_with_sql(
        ctx: RunContext[AppState],
        ontology: str = "",
        sql: str = "",
    ) -> dict[str, Any]:
        """Query ontology objects with SQL. (See shared description.)"""
        ontology = ontology or ctx.deps.ontology
        return await query_with_sql_logic(executor, ontology, sql)

    return ts
