"""MCP server — exposes the ontology tools to external Agents via MCP.

Per ADR-009 (Sprint 1, read-only) + ADR-010 (Sprint 2, write/action with
HITL). Standalone process (``ontology-mcp`` entry point) supporting stdio
(local IDE — Cursor / Claude Desktop) and Streamable HTTP (remote Agents).

Tool exposure — the "shared logic + dual exposure" pattern
----------------------------------------------------------
Each ontology capability is implemented once as a protocol-agnostic
``*_logic`` function (in ``ontology.tools.toolsets.*``). Two thin wrappers
then expose it:

  - **AG-UI** (built-in Web UI): a ``@ts.tool`` wrapper on a pydantic-ai
    ``FunctionToolset`` (reads ``ctx.deps.ontology`` to default the ontology
    arg, keeps the assistant inside the open ontology).
  - **MCP** (this module, external Agents): a ``@mcp.tool`` wrapper that
    takes the ``ontology`` arg explicitly (external Agents have no "current
    ontology") and calls the same ``*_logic`` function.

The human-facing tool **description** (the LLM contract) lives in a single
place — ``ontology.tools.toolsets._contracts`` — and both wrappers import
it, so the contract never drifts between the two entry points. Only the
parameter *schema* is derived from each wrapper's own function signature
(fastmcp / pydantic-ai both do this automatically), because the two
wrappers have intentionally different signatures (AG-UI takes a
``RunContext`` + optional ``ontology``; MCP takes a required ``ontology``
plus an optional ``Context`` for HITL).

HITL (ADR-010)
--------------
Write/action tools take a ``fastmcp.Context`` and delegate approval to
``MCPApprovalHandler``, which calls ``ctx.elicit``. Three outcomes are
distinguished:

  - client does not support elicitation  → ``elicit`` raises; surfaced as
    ``{error:{code:"ELICITATION_UNSUPPORTED",...}}`` (NOT a silent deny)
  - user declines / cancels the dialog    → ``{status:"DENIED",...}``
  - user accepts                          → proceeds to the ``*_logic`` body

Read-only tools are ``tool_plain`` (no ``Context``) — fastmcp derives their
schema purely from annotations.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from fastmcp import Context, FastMCP

from ontology.config.container import container
from ontology.tools import (
    ToolExecutor,
    describe_link_type_logic,
    describe_object_type_logic,
    describe_ontology_logic,
    invoke_action_logic,
    list_link_types_logic,
    list_object_types_logic,
    list_ontologies_logic,
    validate_action_logic,
)
from ontology.tools.executor import ApprovalHandler, ApprovalRequest
from ontology.tools.toolsets._contracts import (
    DESCRIBE_LINK_TYPE_DESC,
    DESCRIBE_OBJECT_TYPE_DESC,
    DESCRIBE_ONTOLOGY_DESC,
    EXISTS_LINK_DESC,
    FIND_PATHS_DESC,
    INVOKE_ACTION_DESC,
    LIST_LINK_TYPES_DESC,
    LIST_OBJECT_TYPES_DESC,
    LIST_ONTOLOGIES_DESC,
    QUERY_WITH_DATAFRAME_DESC,
    QUERY_WITH_SQL_DESC,
    TRAVERSE_LINK_DESC,
    VALIDATE_ACTION_DESC,
)

_log = logging.getLogger(__name__)

# Server-level instructions shown to MCP clients. Without these, an external
# Agent only learns the server's purpose by reading each tool's description
# one by one. fastmcp advertises `instructions` in the initialize handshake.
_SERVER_INSTRUCTIONS = (
    "Gaia ontology MCP server. Exposes an enterprise ontology (object types, "
    "link types, actions) plus query tools to external Agents.\n\n"
    "Recommended call order:\n"
    "  1. list_ontologies — discover valid `ontology` values.\n"
    "  2. describe_ontology — bootstrap a new ontology in ONE call (all object\n"
    "     types + links + actions). Preferred over the\n"
    "     list_object_types -> describe_object_type -> describe_link_type chain.\n"
    "  3. list_object_types / list_link_types — enumerate when you only need\n"
    "     names, or describe_object_type / describe_link_type for one entity's\n"
    "     full schema (e.g. filterable/sortable hints, full action parameters).\n"
    "  4. query_with_sql (attribute queries) or query_with_dataframe "
    "(relationship/spatial/temporal queries).\n"
    "  5. traverse_link / exists_link for single-hop relationships; "
    "find_paths for multi-hop connectivity.\n"
    "  6. invoke_action requires client-side elicitation support for HITL "
    "confirmation; validate_action is a read-only pre-check with no "
    "confirmation.\n\n"
    "`ontology` is a REQUIRED argument on every tool here (external Agents "
    "have no implicit 'current ontology').\n\n"
    "Capability boundary — what this server does NOT expose (use the Gaia "
    "REST API or the in-product AG-UI Agent for these):\n"
    "  - Ad-hoc ontology modeling (define_object_type / add_property / "
    "define_link_type / link_dataset). Ontology modeling is an internal "
    "capability exposed only via the in-product AG-UI Agent and the REST "
    "admin API — external Agents query and act on existing ontology; "
    "modeling is done in-product or by data engineers via REST.\n"
    "  - ActionType definition / update / version rollback (define_action_type "
    "and friends). You can invoke_action on an existing ActionType, but you "
    "cannot create or modify ActionType definitions here.\n"
    "  - Batch action execution (execute-batch). Loop invoke_action instead.\n"
    "  - Data source / dataset / pipeline management (CRUD, exploration, CDC "
    "sync tasks). These are admin/data-engineering operations.\n"
    "  - Action preview (dry-run with mutation preview) — use validate_action "
    "for input checking, or the REST /actions/preview for full dry-run."
)


class MCPApprovalHandler(ApprovalHandler):
    """ApprovalHandler backed by MCP elicitation.

    Calls ``ctx.elicit`` to ask the MCP client (Claude Desktop etc.) to
    render a native confirmation dialog. Three outcomes are distinguished
    (per the module docstring):

      - client lacks elicitation capability → ``elicit`` raises a
        ``ToolError``; we surface ``ElicitationUnsupportedError`` so the caller
        can tell "environment can't do this" apart from "user said no".
      - user declines / cancels              → return ``False`` (DENIED).
      - user accepts                         → return ``True``.

    Per ADR-010 decision 4 there is no auto-approve fallback — clients
    without elicitation support will see a structured error (not a silent
    bypass). High-risk actions use the same elicit (MCP can't enforce
    type-name confirmation).
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    async def request_approval(self, approval: ApprovalRequest) -> bool:
        message = f"确认执行工具 {approval.tool_name}? (risk={approval.risk_level})\n影响: {approval.impact}"
        if approval.diff_preview:
            message += f"\n变更预览:\n{approval.diff_preview}"
        try:
            # Use an explicit choice list ("确认" / "取消") rather than
            # response_type=bool: the list form renders as a native option
            # picker across clients (Claude Desktop / Cursor / custom),
            # avoiding the bool→{value:bool} object-schema ambiguity.
            #
            # fastmcp's elicit @overload set makes mypy match the
            # `response_type: None` overload first and reject list[str];
            # the runtime impl accepts it (see
            # fastmcp.server.context.Context.elicit impl signature).
            elicit = self._ctx.elicit
            result = await elicit(message, response_type=["确认", "取消"])  # type: ignore[arg-type]
        except Exception as exc:  # client lacks elicitation capability
            _log.warning("mcp.elicit.unsupported tool=%s error=%s", approval.tool_name, exc)
            raise ElicitationUnsupportedError(approval.tool_name, str(exc)) from exc
        action = getattr(result, "action", None)
        if action != "accept":
            return False
        # AcceptedElicitation.data is the selected string ("确认" / "取消").
        return getattr(result, "data", "") == "确认"


class ElicitationUnsupportedError(RuntimeError):
    """The MCP client does not support elicitation; HITL cannot proceed.

    Raised from ``MCPApprovalHandler.request_approval`` so the write/action
    tool wrapper can convert it to a structured error envelope instead of
    treating it as a user denial (which is what ``False`` would mean).
    """

    def __init__(self, tool_name: str, detail: str) -> None:
        super().__init__(f"elicitation unsupported for tool {tool_name}: {detail}")
        self.tool_name = tool_name
        self.detail = detail


def _register_readonly_tools(mcp: FastMCP) -> None:
    """Register the read-only toolsets for the MCP path.

    MCP serves external Agents (Cursor / Claude Desktop) which have no
    "current ontology" concept — so ``ontology`` is a REQUIRED arg here
    (unlike the AG-UI path where it defaults to the open ontology), and
    ``list_ontologies`` IS registered (it is the external Agent's ontology
    discovery entry point). See docs/architecture/rfcs/AI-context-scoping.md §9.

    Each wrapper delegates to the shared ``*_logic`` function (single source
    of truth shared with the AG-UI toolsets) and carries the shared
    description from ``_contracts`` so the LLM contract is identical across
    entry points.
    """
    executor = ToolExecutor(container)

    @mcp.tool(description=LIST_ONTOLOGIES_DESC)
    async def list_ontologies() -> Any:
        """List all ontologies available in Gaia. (See shared description.)"""
        return await list_ontologies_logic(executor)

    @mcp.tool(description=LIST_OBJECT_TYPES_DESC)
    async def list_object_types(ontology: str) -> Any:
        """List all object types in an ontology. (See shared description.)"""
        return await list_object_types_logic(executor, ontology)

    @mcp.tool(description=DESCRIBE_OBJECT_TYPE_DESC)
    async def describe_object_type(ontology: str, object_type: str) -> Any:
        """Get the full schema of an object type. (See shared description.)"""
        return await describe_object_type_logic(executor, ontology, object_type)

    @mcp.tool(description=DESCRIBE_LINK_TYPE_DESC)
    async def describe_link_type(ontology: str, link_type: str) -> Any:
        """Get the schema of a link type. (See shared description.)"""
        return await describe_link_type_logic(executor, ontology, link_type)

    @mcp.tool(description=DESCRIBE_ONTOLOGY_DESC)
    async def describe_ontology(ontology: str) -> Any:
        """Get the full metadata of an ontology in one call. (See shared description.)"""
        return await describe_ontology_logic(executor, ontology)

    @mcp.tool(description=LIST_LINK_TYPES_DESC)
    async def list_link_types(ontology: str) -> Any:
        """List all link (relationship) types in an ontology. (See shared description.)"""
        return await list_link_types_logic(executor, ontology)

    @mcp.tool(description=QUERY_WITH_SQL_DESC)
    async def query_with_sql(ontology: str, sql: str) -> Any:
        """Query ontology objects with SQL. (See shared description.)"""
        from ontology.tools.toolsets.object_query import query_with_sql_logic

        return await query_with_sql_logic(executor, ontology, sql)

    @mcp.tool(description=QUERY_WITH_DATAFRAME_DESC)
    async def query_with_dataframe(
        ontology: str,
        object_set_ir: dict[str, Any],
        cursor: str | None = None,
    ) -> Any:
        """Execute a graph-reasoning query via ObjectSet IR. (See shared description.)"""
        from ontology.tools.toolsets.reasoning import query_with_dataframe_logic

        return await query_with_dataframe_logic(executor, ontology, object_set_ir, cursor=cursor)

    @mcp.tool(description=TRAVERSE_LINK_DESC)
    async def traverse_link(
        ontology: str,
        link_type: str,
        source_keys: list[str],
        direction: str = "forward",
        target_filter: dict[str, Any] | None = None,
        target_properties: list[str] | None = None,
        include_source_mapping: bool = False,
    ) -> Any:
        """Traverse a single-hop relationship. (See shared description.)"""
        from ontology.tools.toolsets.link_traversal import traverse_link_logic

        return await traverse_link_logic(
            executor,
            ontology,
            link_type,
            source_keys,
            direction,
            target_filter,
            target_properties,
            include_source_mapping,
        )

    @mcp.tool(description=EXISTS_LINK_DESC)
    async def exists_link(
        ontology: str,
        link_type: str,
        source_key: str,
        direction: str = "forward",
        target_key: str | None = None,
    ) -> Any:
        """Check whether a relationship exists. (See shared description.)"""
        from ontology.tools.toolsets.link_traversal import exists_link_logic

        return await exists_link_logic(
            executor,
            ontology,
            link_type,
            source_key,
            direction,
            target_key,
        )

    @mcp.tool(description=FIND_PATHS_DESC)
    async def find_paths(
        ontology: str,
        source_key: str,
        target_key: str,
        link_types: list[str] | None = None,
        max_depth: int = 5,
        limit: int = 10,
    ) -> Any:
        """Find shortest paths between two objects. (See shared description.)"""
        from ontology.tools.toolsets.link_traversal import find_paths_logic

        return await find_paths_logic(executor, ontology, source_key, target_key, link_types, max_depth, limit)

    @mcp.tool(description=VALIDATE_ACTION_DESC)
    async def validate_action(
        ontology: str,
        object_type: str,
        action_type: str,
        parameters: dict[str, Any] | None = None,
    ) -> Any:
        """Pre-validate an action's parameters without executing. (See shared description.)"""
        # validate_action_logic is read-only — use a bare executor (no handler).
        return await validate_action_logic(
            ToolExecutor(container),
            ontology,
            object_type,
            action_type,
            parameters,
        )


def _register_write_action_tools(mcp: FastMCP) -> None:
    """Register write/action tools as MCP functions taking a fastmcp Context.

    Each wraps the shared ``*_logic`` function, binding an MCPApprovalHandler
    (which uses ctx.elicit) to a request-scoped ToolExecutor. If the client
    lacks elicitation support, the handler raises ``ElicitationUnsupportedError``
    and the wrapper converts it to a structured error envelope (NOT a silent
    deny — see MCPApprovalHandler docstring).
    """

    async def _run_gated(
        ctx: Context,
        fn: Any,
        *args: Any,
    ) -> Any:
        """Run a write/action ``*_logic`` fn under MCP HITL.

        Builds a request-scoped executor carrying ``MCPApprovalHandler``
        and forwards ``fn(executor, *args)``. If the client lacks
        elicitation support, the handler raises ``ElicitationUnsupportedError``
        (from inside ``fn`` → ``execute_gated`` → ``request_approval``);
        we convert it to a structured ``ELICITATION_UNSUPPORTED`` envelope
        rather than letting it surface as a generic tool error or a silent
        ``{status:DENIED}``.
        """
        executor = ToolExecutor(container, approval_handler=MCPApprovalHandler(ctx))
        try:
            return await fn(executor, *args)
        except ElicitationUnsupportedError as exc:
            return {"error": {"code": "ELICITATION_UNSUPPORTED", "message": str(exc)}}

    @mcp.tool(description=INVOKE_ACTION_DESC)
    async def invoke_action(
        ctx: Context,
        ontology: str,
        object_type: str,
        action_type: str,
        parameters: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Execute a predefined action. (See shared description.)"""
        return await _run_gated(
            ctx,
            invoke_action_logic,
            ontology,
            object_type,
            action_type,
            parameters,
            idempotency_key,
        )


def _build_fastmcp_server() -> FastMCP:
    """Build a FastMCP server with all ontology tools (read + write + action)."""
    mcp = FastMCP("gaia-ontology", instructions=_SERVER_INSTRUCTIONS)
    _register_readonly_tools(mcp)
    _register_write_action_tools(mcp)
    return mcp


def main() -> None:
    """Entry point for the ``ontology-mcp`` console script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,  # stdout is reserved for MCP stdio frames
    )
    parser = argparse.ArgumentParser(
        prog="ontology-mcp",
        description="Gaia ontology MCP server — exposes ontology tools to Agents.",
    )
    transport = parser.add_mutually_exclusive_group(required=True)
    transport.add_argument("--stdio", action="store_true", help="Run over stdio (local IDE).")
    transport.add_argument("--http", action="store_true", help="Run over Streamable HTTP.")
    parser.add_argument("--port", type=int, default=9000, help="HTTP port (default 9000).")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (default 0.0.0.0).")
    args = parser.parse_args()

    mcp = _build_fastmcp_server()
    if args.stdio:
        _log.info("starting gaia-ontology MCP server on stdio")
        mcp.run(transport="stdio")
    else:
        _log.info("starting gaia-ontology MCP server on http://%s:%s", args.host, args.port)
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
