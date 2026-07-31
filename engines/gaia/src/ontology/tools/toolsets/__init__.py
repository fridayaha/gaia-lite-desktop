"""Toolsets — grouped collections of ontology tools.

Each module builds a ``FunctionToolset`` for one capability family:
  - metadata: orientation tools (list_ontologies, describe_object_type, ...)
  - object_query: retrieval + aggregation (get, filter, count, aggregate, ...)
  - link_traversal: relationship traversal (list_link_types, traverse_link,
                    exists_link)

Toolsets are combined via ``FunctionToolset`` composition (or registered
individually onto an Agent / FastMCP server) depending on the entry point.
"""
