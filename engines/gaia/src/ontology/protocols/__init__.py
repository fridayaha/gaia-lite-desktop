"""Protocol entry points — expose ontology toolsets to external consumers.

  - mcp_server: MCP server (FastMCP) for external Agents (Cursor / Claude
    Desktop / custom). Standalone process via the ``ontology-mcp`` entry
    point. See ADR-009 decision 7.

REST routes (the third entry point per the design doc) live in
``ontology.routes`` as before; AG-UI lives in ``routes/ai.py``. This
package holds only the MCP adapter — the protocol-specific glue. The
shared tool definitions live in ``ontology.tools``.
"""
