"""Reasoning tool (1) — query_with_dataframe (推理线统一入口).

Per docs/architecture/graph-reasoning-design.md §9. The reasoning-line
counterpart of ``query_with_sql``: NL → ObjectSet IR → Ibis+Neo4j+PG
multi-engine orchestration. Independent of the SQL line (C5/C12): SQL line
goes Doris/Trino; reasoning line goes Neo4j+PostGIS+TimescaleDB, hydrating
full attributes via object_state.

Architecture (docs/architecture/rfcs/AI-context-scoping.md):
  - ``query_with_dataframe_logic(executor, ontology, ...)`` is the protocol-
    agnostic single source of truth. MCP exposure calls it directly.
  - ``build_reasoning_toolset`` produces the AG-UI exposure.
"""

from __future__ import annotations

from typing import Any

from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.toolsets import FunctionToolset

from ontology.core.schemas.canvas import CanvasEdge, CanvasObject
from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState

# ── Shared logic (single source of truth, protocol-agnostic) ─────────────


async def query_with_dataframe_logic(
    executor: ToolExecutor,
    ontology: str,
    object_set_ir: dict[str, Any],
    cursor: str | None = None,
) -> dict[str, Any]:
    """Execute a graph-reasoning query via ObjectSet IR (推理线).

    The single entry point for relationship traversal, spatial, and temporal
    analysis. Pass a Palantir-aligned ObjectSet IR (JSON); the
    DataFrameQueryService orchestrates Neo4j (graph traversal) + PostGIS
    (spatial filter) + TimescaleDB (temporal filter), hydrating full object
    attributes at the end.

    Protocol-agnostic. Independent of ``query_with_sql`` (SQL line): the SQL
    line handles attribute filtering/aggregation over Doris; this tool
    handles relationship traversal + spatial/temporal analysis over
    Neo4j+PG. Two lines converge only at hydration (C12).

    Cursor semantics (ADR-019 §4 操作面对等原则):
      - ``cursor`` is the ``next_cursor`` value returned by the previous call.
      - It is the last rid of the previous page; hydration resumes from the
        next rid. ``None`` starts from the beginning.
      - The IR MUST be identical across paginated calls (same filters / same
        order_by) — cursor pagination assumes a stable underlying rid
        ordering. Changing the IR between calls invalidates the cursor.
      - A cursor is tied to a specific IR + ontology; do not mix cursors
        across different queries.

    ObjectSet IR structure (type-discriminated union):
      - {"type":"objectType","object_type":"<OT>","filters":[...]}  起始集
      - {"type":"static","objects":["<rid>",...]}  显式 rid
      - {"type":"filter","object_set":<IR>,"filters":[...]}  过滤子集
      - {"type":"searchAround","link":"<link>","object_set":<IR>,"hops":[min,max],"direction":"out|in|both"}  图遍历
      - {"type":"union","object_sets":[<IR>,<IR>,...]}  集合并
      - {"type":"intersect","object_sets":[<IR>,<IR>,...]}  集合交
      - {"type":"subtract","object_sets":[<IR>,<IR>,...]}  集合差
      - {"type":"aggregate","object_set":<IR>,"group_by":["<field>"],
         "aggregations":[{"func":"count|sum|avg|min|max","field":"<f>","alias":"<a>"}]}  聚合
      - {"type":"select","object_set":<IR>,"select_fields":["<field>",...]}  投影

    可选 order_by: [{"field":"<field>","desc":false}]  排序（保证分页稳定）

    Filter ops (16): exactMatch/notEqual/in/notIn/range/greaterThan/lessThan/
    contains/startsWith/endsWith/withinDistance/withinPolygon/withinBoundingBox/
    timeRange/isNull/isNotNull.
      - in/notIn: value=[v1,v2,...]
      - greaterThan/lessThan: value=标量
      - withinDistance: center=[lon,lat], max_distance=米
      - range: value={min,max}; timeRange: value={start,end}

    Returns {objects[], aggregates[], truncated, next_cursor?, stats{...}}.
    aggregate 返回 aggregates（分组+聚合值），objects 为空。
    """
    from ontology.core.schemas.object_set import ObjectSetIR

    ir = ObjectSetIR.model_validate(object_set_ir)
    svc = executor.container.dataframe_query_service
    result = await executor.audit_call(
        "query_with_dataframe",
        {"ontology": ontology, "ir_type": ir.type, "cursor": cursor},
        svc.execute(ir, ontology, cursor=cursor),
    )
    return dict(result.model_dump())


def _detect_search_around(ir_dict: dict[str, Any]) -> tuple[bool, str]:
    """递归检测 IR 树是否含 searchAround，返回 (含?, 代表 link api_name)。

    用于区分「探索查询」（累积节点+边，画轨迹）与「纯查询」（覆盖刷新）。
    代表 link 取深度优先遇到的第一个 searchAround 的 link（记入 expanded_links）。
    嵌套如 filter(searchAround(...)) 也算探索。"""
    if not isinstance(ir_dict, dict):
        return False, ""
    if ir_dict.get("type") == "searchAround":
        return True, ir_dict.get("link", "") or ""
    child = ir_dict.get("object_set")
    if isinstance(child, dict):
        hit, link = _detect_search_around(child)
        if hit:
            return True, link
    for sub in ir_dict.get("object_sets", []) or []:
        hit, link = _detect_search_around(sub)
        if hit:
            return True, link
    return False, ""


# ── AG-UI exposure (pydantic-ai toolset, reads ctx.deps.ontology) ────────


def build_reasoning_toolset(executor: ToolExecutor) -> FunctionToolset[AppState]:
    """Build the reasoning toolset for the AG-UI path.

    ``query_with_dataframe`` is the reasoning-line entry point (graph
    traversal + spatial/temporal analysis). Reads ``ctx.deps.ontology`` and
    defaults the ``ontology`` arg to it when the LLM omits it.
    """
    ts: FunctionToolset[AppState] = FunctionToolset()

    @ts.tool
    async def query_with_dataframe(
        ctx: RunContext[AppState],
        ontology: str = "",
        object_set_ir: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Execute a graph-reasoning query (relationship traversal / spatial /
        temporal analysis / set operations / aggregation) via ObjectSet IR.
        Orchestrates Neo4j (graph traversal) + PostGIS (spatial) +
        TimescaleDB (temporal), hydrating full object attributes at the end.
        Also drives the graph-explore canvas (loads objects + edges onto the
        canvas for visualization) — query_with_sql does not touch the canvas.

        WHEN TO USE THIS vs query_with_sql (the two query tools):
        ────────────────────────────────────────────────────────────────
        Use query_with_dataframe (this tool) for RELATIONSHIP / SPATIAL /
        TEMPORAL / SET-OPERATION queries:
          - multi-hop relationship traversal (searchAround, up to 3 hops —
            SQL can only JOIN two tables, not traverse a graph path)
          - spatial filter (withinDistance / withinPolygon / withinBoundingBox)
          - temporal filter (timeRange)
          - set operations (union / intersect / subtract of object sets)
          - when you want results loaded onto the graph-explore canvas
        Use query_with_sql (the other tool) for ATTRIBUTE queries:
          - filter / list / count / exists / top-N / point lookup
          - aggregate (SUM/COUNT/AVG + GROUP BY/HAVING)
          - JOIN across two linked types (flat JOIN is faster in SQL)
          - window functions, arithmetic, ratio, time functions
        Rule of thumb: if the question is about RELATIONSHIPS ("who supplies
        S001", "find all orders connected to this supplier within 2 hops"),
        SPACE ("within 5km of this point"), or TIME ("in the last 7 days") →
        this tool. If it's about object PROPERTIES ("how many", "what's the
        total", "list customers where region=EAST") → query_with_sql.
        NOTE: this tool CAN do flat attribute filtering too (filter type),
        but it routes through PG object_state which is slower than Doris.
        For pure attribute queries prefer query_with_sql; use this tool's
        filter type only when chaining it with searchAround/spatial/temporal.

        Args:
            ontology: Ontology api_name. Omit to use the ontology currently
                open in the Web UI.
            object_set_ir: ObjectSet IR JSON. The ``type`` field is REQUIRED
                on every IR object (discriminated union). Minimal structures:

                Load ALL objects of a type (most common starting point):
                  {"type":"objectType","object_type":"Customer"}

                Load a type WITH a filter:
                  {"type":"objectType","object_type":"Supplier",
                   "filters":[{"field":"riskLevel","op":"exactMatch","value":"high"}]}

                Load specific objects by id (static needs a NON-EMPTY list):
                  {"type":"static","objects":["S001","S002"]}

                Graph traversal (expand a link from an object set, ≤3 hops):
                  {"type":"searchAround","link":"supplies","direction":"out",
                   "object_set":{"type":"objectType","object_type":"Order"}}

                Filter an existing object set (nested):
                  {"type":"filter",
                   "filters":[{"field":"status","op":"exactMatch","value":"open"}],
                   "object_set":{...any IR...}}

                Set ops: {"type":"union","object_sets":[IR,IR,...]}
                  (also intersect / subtract, same shape)

                Aggregate: {"type":"aggregate","object_set":IR,
                  "group_by":["riskLevel"],
                  "aggregations":[{"func":"count","field":"","alias":"cnt"}]}

                Projection: {"type":"select","object_set":IR,
                  "select_fields":["name","city"]}

                Filter ops (16): exactMatch/notEqual/in/notIn/range/
                greaterThan/lessThan/contains/startsWith/endsWith/
                withinDistance/withinPolygon/withinBoundingBox/timeRange/
                isNull/isNotNull.
                  - in/notIn: value=[v1,v2,...]
                  - greaterThan/lessThan: value=scalar
                  - range: value={min,max}; timeRange: value={start,end}
                  - withinDistance: center=[lon,lat], max_distance=meters
                Optional order_by: [{field, desc}] for stable pagination.

        Returns {objects[], aggregates[], truncated, next_cursor?, stats{...}}.
        objects are hydrated full objects (rid + api_name + props).
        aggregates (aggregate type only): [{group, aggregates}].
        truncated=true when result exceeds hydrate limit (use next_cursor).
        cursor: pass the previous call's next_cursor to fetch the next page;
            the IR must stay identical across paginated calls.

        One-shot (find suppliers of unfulfilled orders, starting from S001):
          query_with_dataframe("SupplyChain", {
            "type":"filter",
            "filters":[{"field":"status","op":"exactMatch","value":"unfulfilled"}],
            "object_set":{"type":"searchAround","link":"supplies",
              "object_set":{"type":"static","objects":["S001"]}}
          })
          -> {"objects":[{"rid":"O1","api_name":"Order","props":{...}}, ...],
              "stats":{"steps":3,"engines_used":["postgres","neo4j"]}}

        Boundary: searchAround ≤ 3 hops (Palantir hard limit). Spatial
        filters require the ObjectType to have GEOPOINT/GEOSHAPE properties.
        """
        ontology = ontology or ctx.deps.ontology
        result = await query_with_dataframe_logic(executor, ontology, object_set_ir or {}, cursor=cursor)

        # ADR-015: write the result objects into the canvas shared state and
        # emit a STATE_SNAPSHOT event. The frontend subscribes to state.canvas
        # and re-renders the graph; the Agent reads ctx.deps.state next turn
        # (ReAct observe). This replaces the deleted explore-plan "load" step.
        #
        # We use ToolReturn to split the two audiences (per pydantic-ai AG-UI
        # docs): return_value is the data the Agent sees (objects_count +
        # summary → 0 objects lets it terminate gracefully, ADR-015 D5);
        # metadata carries the StateSnapshotEvent the frontend renders from.
        # We cap stashed objects to keep the snapshot small — full attributes
        # stay queryable via query_with_sql / object_state.
        result_objects = result.get("objects", [])
        canvas_objects = [
            CanvasObject(
                rid=o.get("rid", ""),
                api_name=o.get("api_name", ""),
                title=str(o.get("props", {}).get("title", "") or o.get("rid", "")),
                summary={k: v for k, v in list(o.get("props", {}).items())[:5]},
            )
            for o in result_objects[:200]
        ]
        ir_type = (object_set_ir or {}).get("type", "unknown")
        first_ot = result_objects[0].get("api_name") if result_objects else ir_type
        query_summary = f"{first_ot} ({len(result_objects)} 个对象)"

        # 区分探索查询 vs 纯查询（ADR-015 探索轨迹）：
        # - 含 searchAround：累积节点 + 边，多步串联形成可视化轨迹
        #   （如 S000→物料→订单），用 with_search_around。
        # - 纯查询（objectType/filter/aggregate）：覆盖刷新画布，不产生边。
        is_explore, explore_link = _detect_search_around(object_set_ir or {})
        if is_explore and result_objects:
            canvas_edges = [
                CanvasEdge(
                    source_rid=e.get("source_rid", ""),
                    target_rid=e.get("target_rid", ""),
                    link_type=e.get("link_type", explore_link),
                    direction=e.get("direction", "out"),
                )
                for e in result.get("edges", [])
                if e.get("source_rid") and e.get("target_rid")
            ]
            new_canvas = ctx.deps.state.with_search_around(
                canvas_objects,
                canvas_edges,
                link=explore_link,
                query_summary=query_summary,
            )
        else:
            new_canvas = ctx.deps.state.with_objects(canvas_objects, query_summary=query_summary, append=False)
        ctx.deps.state = new_canvas
        return ToolReturn(
            return_value={
                "objects_count": len(result_objects),
                "objects": result_objects,
                "aggregates": result.get("aggregates", []),
                "truncated": result.get("truncated", False),
                "next_cursor": result.get("next_cursor"),
                "canvas_updated": True,
                "query_summary": query_summary,
            },
            metadata=StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot={"canvas": new_canvas.model_dump(mode="json")},
            ),
        )

    return ts
