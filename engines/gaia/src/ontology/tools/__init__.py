"""Ontology tool layer — exposes ontology capabilities to Agents.

Implements the "modelling-as-tools" paradigm (see
docs/architecture/ontology-tool-layer.md): tools are derived from ontology
metadata (ObjectType/LinkType/ActionType) and shared across three entry
points (MCP for external Agents, AG-UI for the built-in Web UI, REST for
scripts). Tool definitions use pydantic-ai ``FunctionToolset``; MCP exposure
uses ``FastMCP`` (pulled in via pydantic-ai's MCP extra).

MVP scope (Sprint 1): read + write/action tools across 4 toolsets:
  - metadata (5): list_ontologies / list_object_types /
                  describe_object_type / describe_link_type / list_link_types
  - object_query (1): query_with_sql
    (filter/count/aggregate/topn/exists were removed 2026-06, and
     get_object/bulk_get_object were removed 2026-07, all in favor of the
     single query_with_sql entry point — see object_query.py docstring)
  - link_traversal (2): traverse_link / exists_link
  - write (4): define_object_type / add_property / define_link_type / link_dataset
  - action (2): invoke_action / validate_action

Note: traverse_link (batch sources + target_filter) and exists_link are
implemented (graph-reasoning M4) via Neo4jGraphStore; query_with_dataframe
is the reasoning-line entry point (ObjectSet IR → Neo4j+PG multi-engine).
"""

from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets.action import (
    build_action_toolset,
    invoke_action_logic,
    validate_action_logic,
)
from ontology.tools.toolsets.canvas_control import build_canvas_control_toolset
from ontology.tools.toolsets.link_traversal import build_link_traversal_toolset, list_link_types_logic
from ontology.tools.toolsets.metadata import (
    build_metadata_toolset,
    describe_link_type_logic,
    describe_object_type_logic,
    describe_ontology_logic,
    list_object_types_logic,
    list_ontologies_logic,
)
from ontology.tools.toolsets.object_query import (
    build_object_query_toolset,
)
from ontology.tools.toolsets.reasoning import build_reasoning_toolset, query_with_dataframe_logic
from ontology.tools.toolsets.write import (
    add_property_logic,
    build_write_toolset,
    define_link_type_logic,
    define_object_type_logic,
    link_dataset_logic,
)

__all__ = [
    "AppState",
    "ToolExecutor",
    "add_property_logic",
    "build_action_toolset",
    "build_canvas_control_toolset",
    "build_link_traversal_toolset",
    "build_reasoning_toolset",
    "query_with_dataframe_logic",
    "build_metadata_toolset",
    "build_object_query_toolset",
    "build_write_toolset",
    "define_link_type_logic",
    "define_object_type_logic",
    "describe_link_type_logic",
    "describe_object_type_logic",
    "describe_ontology_logic",
    "invoke_action_logic",
    "link_dataset_logic",
    "list_link_types_logic",
    "list_object_types_logic",
    "list_ontologies_logic",
    "validate_action_logic",
]
