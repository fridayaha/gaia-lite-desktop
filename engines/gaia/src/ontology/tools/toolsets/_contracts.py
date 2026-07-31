"""Single source of truth for MCP/AG-UI tool contracts (name + description).

Problem this module solves
--------------------------
Before this module, each tool's name, description (the LLM-visible contract),
and parameter schema were maintained in THREE places:

  1. the ``*_logic`` function signature (protocol-agnostic body),
  2. the AG-UI ``@ts.tool`` wrapper docstring (``toolsets/<x>.py``),
  3. the MCP ``@mcp.tool`` wrapper docstring (``protocols/mcp_server.py``).

The two docstrings drifted: the AG-UI side carried detailed one-shot examples
and return-shape notes, while the MCP side carried a shorter, different
summary. An LLM calling the same capability over the two entry points thus
saw *different* tool contracts — a silent source of behavioral drift.

This module centralizes the human-facing description for every tool. Both
the AG-UI toolset wrappers and the MCP server wrappers import these
constants so the contract is written once.

Why descriptions and not full schemas
-------------------------------------
The parameter *schema* is already derived from the function signature by
both pydantic-ai (``FunctionToolset``) and fastmcp (``@mcp.tool``) — those
stay on the wrapper functions where the signature lives. Only the free-form
*description* (which fastmcp / pydantic-ai both let you override via kwarg)
needs a single home, because it's the part that duplicates and drifts.

Convention
----------
- ``<tool>_NAME``: the tool name (must match the wrapper function name).
- ``<tool>_DESC``: the description shown to the LLM. Written as the
  union of the two old docstrings — orientation + when-to-use + return shape
  + one-shot — so both entry points see the richest contract.
"""

from __future__ import annotations

# ── Metadata / orientation (read-only) ───────────────────────────────────

LIST_ONTOLOGIES_DESC = """List all ontologies available in Gaia. Call this FIRST to discover
valid `ontology` values for other tools.

Returns a list of ontologies, each with api_name (use as the `ontology`
argument everywhere else), display_name, and description.
"""

DESCRIBE_ONTOLOGY_DESC = """Get the FULL metadata of an ontology in a single call: all object types
(with properties + inbound/outbound links + applicable actions), all link
types, all action types (summaries), and interfaces.

This is the bootstrap endpoint for a new ontology — call it ONCE after
list_ontologies to avoid the list_object_types -> describe_object_type(xN)
-> describe_link_type(xM) round-trip chain. The built-in Gaia Web UI Agent
already receives this as injected context, so it does not expose this tool;
this is for external Agents (MCP) and scripts (REST) with no implicit
ontology context.

Mirrors Palantir Foundry /fullMetadata. The response is best-effort: if one
entity type fails to load, `partial` is true and `omitted` lists what was
skipped (the rest still loads) — do not treat a partial response as an error.

Args:
    ontology: Ontology api_name (call list_ontologies first). Case-sensitive.

Returns:
    ontology: {api_name, display_name, description}
    object_types: map<api_name, {api_name, display_name, primary_key,
        title_property, storage_type, properties[], inbound_links[],
        outbound_links[], actions[]}>
    link_types: map<api_name, LinkTypeDef {source/target via api_name in
        each ObjectTypeFullMetadata, cardinality, direction, foreign_key}>
    action_types: map<api_name, {api_name, display_name, description,
        affected_object_type_api_name, risk_level, operation_kind}> (no full
        parameters schema — call validate_action / describe_action_type for that)
    interfaces: list<InterfaceType>
    partial: bool, omitted: list[str]
"""

LIST_OBJECT_TYPES_DESC = """List all object types in an ontology.

Call this after list_ontologies to see which object types exist, then call
describe_object_type to confirm property names and the primary key before
any instance-layer tool.

Args:
    ontology: Ontology api_name (call list_ontologies first). Case-sensitive.

Returns a list of object types, each with api_name (use as `object_type`
elsewhere), display_name, description, and storage_type. storage_type
determines the query path: MANAGED objects live in Iceberg+Doris (with
time-travel); VIRTUAL objects federate to an external source via Trino
(real-time, no time-travel).
"""

DESCRIBE_OBJECT_TYPE_DESC = """Get the full schema of an object type: properties, types, primary key,
filterable/sortable hints.

Call this before query_with_sql / query_with_dataframe / traverse_link to
confirm property names and the primary key — it is the single biggest lever
for reducing tool call errors.

Args:
    ontology: Ontology api_name.
    object_type: Object type api_name (call list_object_types first).

Returns api_name, primary_key, storage_type, and properties[] each with
{api_name, data_type, is_primary_key, nullable, filterable, sortable}.
data_type determines allowed filter operators: STRING supports
eq/neq/contains/in; DECIMAL/INTEGER support gt/gte/lt/lte; ENUM supports
eq/in. property api_name is case-sensitive — writing the wrong name in a
filter returns INVALID_FILTER.
"""

DESCRIBE_LINK_TYPE_DESC = """Get the schema of a link type: source/target object types,
cardinality, direction, whether the link carries its own properties.

Args:
    ontology: Ontology api_name.
    link_type: Link type api_name (call list_link_types first).

Returns api_name, source_object_type, target_object_type, cardinality
("ONE" or "MANY"), direction ("OUTGOING"/"INCOMING"), directional (whether
reverse traversal is meaningful), has_properties (whether the link itself
carries attributes).

cardinality determines traverse_link's return shape: ONE returns a single
object (or null); MANY returns a list. Call this before multi-hop traversal
to confirm direction and connectivity at each hop.
"""

LIST_LINK_TYPES_DESC = """List all link (relationship) types in an ontology.

Call this before traverse_link / exists_link to confirm available
relationships and their direction.

Args:
    ontology: Ontology api_name.

Returns a list of link types, each with api_name (use as `link_type` in
traverse_link / exists_link), source_object_type, target_object_type,
cardinality, and direction. api_name is case-sensitive.
"""

# ── Object query (read-only) ─────────────────────────────────────────────

QUERY_WITH_SQL_DESC = """Query ontology objects with SQL — the entry point for ATTRIBUTE-
DIMENSION queries (filter / count / aggregate / join / window / arithmetic).

Runs on Doris (the online read primary source, fast) with Trino fallback.
Compiles your logical SQL (ObjectType api_name as table name, property
api_name as column name) to physical columns, parameterizes literals, and
enforces ontology guardrails.

WHEN TO USE THIS vs query_with_dataframe (the two query tools):
- THIS tool (SQL): ATTRIBUTE queries — filter / list / count / exists /
  top-N, aggregate (SUM/COUNT/AVG/MIN/MAX + GROUP BY/HAVING), JOIN across
  linked ObjectTypes, window functions, arithmetic, ratio, point lookup by
  primary key.
- query_with_dataframe: RELATIONSHIP / SPATIAL / TEMPORAL queries —
  multi-hop graph traversal (≤3 hops), spatial filter (within polygon /
  within distance), temporal filter (timeRange), set operations.
Rule of thumb: object PROPERTIES ("how many", "total", "list where
region=EAST") → SQL. RELATIONSHIPS ("who supplies S001", "orders connected
within 2 hops") or SPACE/TIME → query_with_dataframe. A flat JOIN across
two linked types still belongs here (SQL is faster); only multi-hop graph
traversal needs query_with_dataframe.

Args:
    ontology: Ontology api_name.
    sql: Logical SQL using ObjectType api_name as table name and property
        api_name as column name. Every ObjectType referenced in the SQL is
        auto-inferred for access check, storage routing, and column
        remapping — do NOT pass a separate object_type argument, the SQL is
        the single source of truth. JOIN pairs must be defined LinkType
        (else INVALID_JOIN). Table/column names are validated against the
        ontology; literals are parameterized (injection-safe).

Returns {data: [...], row_count} on success, or
{error: {code, message}} on a guardrail violation.
Supported (Phase 1): single-table, JOIN ≤5 tables, subqueries,
aggregation+GROUP BY+HAVING, window functions, arithmetic, ratio, time
functions. Not supported: CTE (WITH), UNION, UPDATE/INSERT (use Action
tools). Joining MANAGED and VIRTUAL OTs in one query is supported — it
routes to Trino cross-catalog federation.
"""

QUERY_WITH_DATAFRAME_DESC = """Execute a graph-reasoning query via ObjectSet IR — the entry point for
RELATIONSHIP / SPATIAL / TEMPORAL / SET-OPERATION queries.

Orchestrates Neo4j (graph traversal) + PostGIS (spatial filter) +
TimescaleDB (temporal filter), hydrating full object attributes at the end.
Independent of query_with_sql (the SQL line): SQL handles attribute
filtering/aggregation over Doris; this tool handles relationship traversal
+ spatial/temporal analysis over Neo4j+PG.

WHEN TO USE THIS vs query_with_sql:
- THIS tool (ObjectSet IR): multi-hop relationship traversal (searchAround,
  ≤3 hops), spatial filter (withinDistance/withinPolygon/
  withinBoundingBox), temporal filter (timeRange), set operations
  (union/intersect/subtract).
- query_with_sql: attribute queries — filter/list/count/aggregate/JOIN/
  window/arithmetic. Runs on Doris (faster for flat attribute queries; this
  tool routes attribute filters through PG).
Rule of thumb: RELATIONSHIPS ("who supplies S001", "orders connected within
2 hops") or SPACE/TIME → this tool. object PROPERTIES ("how many", "total",
"list where region=EAST") → query_with_sql.

Args:
    ontology: Ontology api_name.
    object_set_ir: ObjectSet IR JSON. Types: objectType/static (起始集),
      filter (过滤), searchAround (图遍历), union/intersect/subtract
      (集合运算), aggregate (group_by + count/sum/avg/min/max),
      select (投影 select_fields).
      Filter ops (16): exactMatch/notEqual/in/notIn/range/greaterThan/
      lessThan/contains/startsWith/endsWith/withinDistance/withinPolygon/
      withinBoundingBox/timeRange/isNull/isNotNull.
      Optional order_by for stable pagination.
    cursor: Pagination cursor — the next_cursor value from the previous
        call's response. Pass it to fetch the next page when truncated=true.
        None starts from the beginning. The IR MUST stay identical across
        paginated calls (cursor assumes a stable rid ordering).
"""

# ── Link traversal (read-only) ───────────────────────────────────────────

TRAVERSE_LINK_DESC = """Traverse a single-hop relationship from one or more source objects to
their linked target object(s). Supports batch sources — pass a list of
source primary keys and the engine looks up the adjacency index in one
batch, deduplicates targets, and reads their properties in one pass.

Args:
    ontology: Ontology api_name.
    link_type: Link type api_name (call list_link_types first).
    source_keys: Primary key values of the source objects (NOT RIDs). Must
        all be the same object type — the source end per describe_link_type
        (the target end when direction="reverse"). Duplicates are
        auto-deduplicated. For a single source pass a one-element list.
    direction: "forward" (default) or "reverse". Forward = source to target;
        reverse = target to source. For directional links, confirm reverse
        traversal is meaningful via describe_link_type.directional first.
    target_filter: Optional filter predicate on the target objects, pushed
        down to the storage layer and merged with permission predicates —
        filtered before property read, so invalid targets are never loaded.
    target_properties: Optional projection of target properties to return
        (saves tokens); defaults to all permitted properties.
    include_source_mapping: When true, returns a source_to_target_map so
        each source's linked targets can be attributed. Recommended for
        batch sources.

Returns {"target_objects":[...], "source_to_target_map"?:{<source_key>:
[<target_key>...]}}. target_objects is the deduplicated target list. The
return shape is also governed by cardinality: ONE returns a single object
(or null) per source; MANY returns a list — predict the shape via
describe_link_type.cardinality to avoid destructuring errors.
Permission-filtered: invisible linked targets simply do not appear.

Boundary: single-hop only; multi-hop needs chained calls (each hop's target
primary key feeds the next hop's source_keys).
"""

EXISTS_LINK_DESC = """Check whether a relationship exists — returns only a boolean, no linked
object properties. Use this instead of traverse_link when you only need a
yes/no answer: write-before-ownership-check, duplicate-binding prevention,
reasoning branch forks.

Args:
    ontology: Ontology api_name.
    link_type: Link type api_name (call list_link_types first).
    source_key: Primary key of a SINGLE source object (batch source checking
        is NOT supported — loop or use traverse_link for batches).
    direction: "forward" (default) or "reverse".
    target_key: Optional target primary key. When present → SINGLE_TARGET
        mode (does source relate to THIS specific target?). When absent →
        ANY_TARGET mode (does source have at least one association?).

Returns {"exists": bool, "mode": "ANY_TARGET"|"SINGLE_TARGET"}. Objects/
links you lack permission to see count as NOT existing — do not use this
tool to probe permissions. Returns only yes/no — never count, properties,
or RIDs.
"""

FIND_PATHS_DESC = """Find shortest paths between two objects through the relationship graph
(Phase 2d path reasoning).

Returns ALL shortest paths (rid sequences) from source to target. Use this
for connectivity questions traverse_link cannot answer (traverse_link is
single-hop only): "are these two objects connected within 3 hops?",
"what's the shortest chain linking supplier S001 to order O-77?".

Args:
    ontology: Ontology api_name.
    source_key: Source object primary key (rid).
    target_key: Target object primary key (rid).
    link_types: Optional list of link type api_names to restrict traversal;
        None = any relationship.
    max_depth: Max hops (default 5). Increase cautiously — path explosion
        is exponential.
    limit: Max paths returned (default 10).

Returns {"source", "target", "paths": [[rid,...],...], "count"}. Each path
is a list of rids from source to target. Returns count=0 (empty paths) when
no path exists within max_depth — distinct from an error.
"""

# ── Write / modelling (medium-risk, HITL gated) ──────────────────────────

DEFINE_OBJECT_TYPE_DESC = """Create a new object type in an ontology. Medium-risk: requires
confirmation listing the object shape before creation.

api_name is PascalCase (e.g. "OrderItem"), caller-supplied — derive it from
display_name (for Chinese names, prefer an English PascalCase translation).
primary_key / title_property are resolved from the property is_primary_key /
is_title_property flags when omitted. Property/Link api_names are
auto-derived by the backend (camelCase).

Args:
    ontology: Ontology api_name.
    api_name: Object type api_name, PascalCase (e.g. "OrderItem").
    display_name: Human-readable name (e.g. "订单明细").
    primary_key: Optional property api_name/display_name serving as primary
        key. Required when no properties are given; otherwise omit and set
        is_primary_key=true on a property.
    title_property: Optional property api_name/display_name for title.
    storage_type: "MANAGED" (lands in Iceberg+Doris, default) or "VIRTUAL"
        (federates to external source via Trino, no write).
    description: Optional description.
    properties: Optional property list at creation. Each entry:
        {display_name, data_type, is_primary_key?, is_title_property?,
        indexed?}. Property api_name is derived.

Returns {api_name, id, display_name, storage_type, properties_created,
status:"created"} on success.
"""

ADD_PROPERTY_DESC = """Add a property to an existing object type. Medium-risk: confirms which
object type gains which property before mutating.

api_name is auto-derived by the backend from display_name (camelCase); do
NOT pass api_name.

Args:
    ontology: Ontology api_name.
    object_type: Target object type api_name.
    display_name: Human-readable property name.
    data_type: STRING | INTEGER | DECIMAL | BOOLEAN | DATE | TIMESTAMP.
    indexed: Build a Doris index for this property (default false).
    nullable: Whether the property may be null (default true).
    description: Optional description.

Returns {api_name, object_type, status:"added"} on success.
"""

DEFINE_LINK_TYPE_DESC = """Define a relationship type between two object types. Medium-risk:
confirms the link's endpoints + cardinality before creation.

api_name is auto-derived by the backend from display_name (camelCase); do
NOT pass api_name.

Args:
    ontology: Ontology api_name.
    display_name: Human-readable name.
    source_object_type: Source object type api_name.
    target_object_type: Target object type api_name.
    cardinality: "ONE" or "MANY" (from source's perspective).
    direction: "OUTGOING" (default) or "INCOMING".
    foreign_key_property: Optional property api_name enabling traversal.
    description: Optional description.

Returns {api_name, status:"created"} on success.
"""

LINK_DATASET_DESC = """Bind an object type's properties to physical dataset columns.
Medium-risk: confirms the mapping before writing.

Args:
    ontology: Ontology api_name.
    object_type: Object type api_name.
    dataset_api_name: Physical dataset api_name to bind.
    column_mappings: List of {property, column}.

Returns {object_type, dataset_api_name, mapped_properties, status:"linked"}
on success.
"""

# ── Action (risk-gated by ActionType.risk_level) ─────────────────────────

INVOKE_ACTION_DESC = """Execute a predefined action on an object type. Risk-gated by the
action's risk_level (defined at modelling time).

Because the risk_level is only known at runtime (from the ActionType
definition), every invoke_action is treated as requiring confirmation.

Args:
    ontology: Ontology api_name.
    object_type: Target object type api_name.
    action_type: ActionType api_name to execute.
    parameters: Action parameters per the ActionType's parameter contract.
    idempotency_key: Optional key for exactly-once semantics.

Returns ActionExecutionResult (applied/accepted/conflict/validation_failed)
with {status, action_id, mutations}.
"""

VALIDATE_ACTION_DESC = """Pre-validate an action's parameters + rules WITHOUT executing. No HITL
(pure check, no side effects). Call before invoke_action to catch
parameter/rule violations early.

Args:
    ontology: Ontology api_name.
    object_type: Target object type api_name.
    action_type: ActionType api_name.
    parameters: Action parameters to validate.

Returns {"valid": bool, "errors": [...]}.
"""
