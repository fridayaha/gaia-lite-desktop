"""pydantic v2 schemas for query operations (ObjectQueryService)."""

from typing import Any, Literal

from pydantic import BaseModel


class QueryFilter(BaseModel):
    """Recursive query filter tree.

    Supports logical combinators (and/or), equality (eq), link traversal
    (search_around), and range predicates (range, with min/max). Range is
    inclusive on both bounds; either bound may be omitted for one-sided
    ranges (e.g. max-only → ``field <= max``).
    """

    type: Literal["eq", "and", "or", "search_around", "range"]
    field: str | None = None
    value: str | int | float | bool | None = None
    min: str | int | float | None = None
    max: str | int | float | None = None
    filters: list["QueryFilter"] | None = None
    link_type_api_name: str | None = None
    source_object_set: "ObjectSet | None" = None


class ObjectSet(BaseModel):
    """Set of objects of a specific type, optionally filtered."""

    object_type_api_name: str
    object_ids: list[str] | None = None
    filter: QueryFilter | None = None


class AggregationMetric(BaseModel):
    """Metric definition for aggregation queries."""

    field: str
    func: Literal["count", "sum", "min", "max", "avg", "cardinality"]
    alias: str | None = None


class AggregationRequest(BaseModel):
    """Aggregation query specification."""

    object_set: ObjectSet
    metrics: list[AggregationMetric]
    group_by: list[str] | None = None


class TextSqlRequest(BaseModel):
    """Request to run a text2sql-compiled query (ADR-012 Step 4 path B).

    Carries a logical SQL (ObjectType api_name as table, property api_name
    as column). The OntologySqlCompiler rewrites it to physical Doris/Trino
    SQL enforcing the three ontology guardrails.

    No ``object_type`` field: every ObjectType referenced in the SQL is
    auto-inferred by the compiler for access check, storage routing, and
    column remapping (design decision C — the SQL is the single source of
    truth, callers never repeat info already encoded in it).
    """

    ontology_api_name: str
    logical_sql: str


# ── Link-traversal requests (graph-reasoning §11.1) ──────────────────────
# These mirror the ``traverse_link_logic`` / ``exists_link_logic`` /
# ``find_paths_logic`` signatures in tools/toolsets/link_traversal.py so the
# REST contract, the MCP tool contract, and the AG-UI tool contract all share
# one parameter shape. Before this, the three endpoints accepted a bare
# ``dict[str, Any]`` — field typos surfaced as 500 KeyError instead of 422,
# and OpenAPI showed an empty request body.


class TraverseRequest(BaseModel):
    """Single-hop relationship traversal.

    ``source_keys`` are primary-key values of the source objects (NOT RIDs);
    pass a one-element list for a single source. ``direction`` selects which
    end of the link to start from (see describe_link_type for
    source/target semantics).
    """

    link_type: str
    source_keys: list[str]
    direction: Literal["forward", "reverse"] = "forward"
    target_filter: dict[str, Any] | None = None
    target_properties: list[str] | None = None
    include_source_mapping: bool = False


class ExistsLinkRequest(BaseModel):
    """Check whether a relationship exists (yes/no only).

    With ``target_key`` → SINGLE_TARGET mode (does source relate to THIS
    target?). Without → ANY_TARGET mode (does source have any association?).
    Batch source checking is NOT supported — loop or use traverse for batches.
    """

    link_type: str
    source_key: str
    direction: Literal["forward", "reverse"] = "forward"
    target_key: str | None = None


class FindPathsRequest(BaseModel):
    """Find shortest paths between two objects (Phase 2d path reasoning).

    Returns all shortest paths (vid sequences) from source to target through
    the relationship graph. ``max_depth`` is hops — increase cautiously,
    path explosion is exponential.
    """

    source_key: str
    target_key: str
    link_types: list[str] | None = None
    max_depth: int = 5
    limit: int = 10
