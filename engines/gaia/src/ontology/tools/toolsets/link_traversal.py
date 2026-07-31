"""Link-traversal tools (3) — list_link_types + traverse_link + exists_link.

Per docs/architecture/ontology-tool-layer.md §5.4. The relationship layer
mirrors the object layer's retrieval/existence split:

  - ``list_link_types``  : enumerate relationship types (orientation)
  - ``traverse_link``    : single-hop traversal, batch sources, target filter
  - ``exists_link``      : relationship existence check (boolean only)

``traverse_link`` and ``exists_link`` are now implemented (graph-reasoning
M4) via Neo4jGraphStore.search_around (single-hop) / exists_link. They were
previously skeletons returning TOOL_NOT_IMPLEMENTED.

Architecture (docs/architecture/rfcs/AI-context-scoping.md):
  - ``list_link_types_logic`` is the protocol-agnostic source of truth.
  - ``build_link_traversal_toolset`` produces the AG-UI exposure reading
    ``ctx.deps.ontology`` to default the ``ontology`` arg.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset

from ontology.tools.executor import ToolExecutor
from ontology.tools.state import AppState
from ontology.tools.toolsets._contracts import (
    EXISTS_LINK_DESC,
    LIST_LINK_TYPES_DESC,
    TRAVERSE_LINK_DESC,
)

# ── Shared logic (single source of truth, protocol-agnostic) ─────────────


async def list_link_types_logic(executor: ToolExecutor, ontology: str) -> list[dict[str, Any]]:
    """List all link (relationship) types in an ontology. Protocol-agnostic."""
    svc = executor.container.ontology_service
    links = await executor.audit_call(
        "list_link_types",
        {"ontology": ontology},
        svc.list_link_types(ontology),
    )
    if isinstance(links, dict) and "error" in links:
        return [links]
    return [
        {
            "api_name": lt.api_name,
            "source_object_type": lt.source_object_type_id,
            "target_object_type": lt.target_object_type_id,
            "cardinality": lt.cardinality,
            "direction": lt.direction,
        }
        for lt in links
    ]


async def traverse_link_logic(
    executor: ToolExecutor,
    ontology: str,
    link_type: str,
    source_keys: list[str],
    direction: str = "forward",
    target_filter: dict[str, Any] | None = None,
    target_properties: list[str] | None = None,
    include_source_mapping: bool = False,
) -> dict[str, Any]:
    """Traverse a single-hop relationship (graph-reasoning M4).

    Implemented via Neo4jGraphStore.search_around (1-hop). ``source_keys``
    are business primary-key values; the API-boundary translation layer
    (``_resolve_rids_by_pk``) resolves them to internal rids before reaching
    the graph engine. Returns target objects (hydrated).
    """
    from ontology.core.exceptions import NotFoundError
    from ontology.core.naming import graph_relationship_type

    # 方向映射：forward→out, reverse→in。
    graph_direction = "out" if direction == "forward" else "in"
    rel_type = graph_relationship_type(ontology, link_type)
    # 单跳遍历需目标 label + 源端 ObjectType（用于 pk→rid 翻译）。
    svc = executor.container.dataframe_query_service
    target_label = await svc._resolve_target_label(ontology, link_type)
    source_ot = await svc._resolve_link_endpoint_ot(ontology, link_type, "source")
    if not target_label or source_ot is None:
        return {"error": {"code": "LINK_NOT_FOUND", "message": f"link type {link_type} not in ontology {ontology}"}}

    # API 边界翻译：业务主键 → rid（防 rid 泄漏到 Agent，ADR-019 边界）。
    try:
        source_rids = await svc._resolve_rids_by_pk(ontology, source_ot.api_name, source_keys)
    except NotFoundError as exc:
        return {"error": {"code": "NOT_FOUND", "message": str(exc)}}

    store = executor.container.graph_store
    source_to_target: dict[str, list[str]] = {}
    try:
        result = await executor.audit_call(
            "traverse_link",
            {"ontology": ontology, "link_type": link_type, "source_count": len(source_rids)},
            store.search_around(
                label=target_label,
                source_rids=source_rids,
                hops=(1, 1),
                rel_types=[rel_type],
                direction=graph_direction,  # type: ignore[arg-type]
                limit=10_000,
            ),
        )
        target_rids = result.rids
        if len(source_rids) == 1:
            # 单源：map 直接从 target_rids 反推（保证与 Neo4j 结果一致）
            source_to_target = {source_rids[0]: target_rids}
        else:
            # 多源：查 PG object_links 区分每个 source 的 target
            # 用 metadata_session() 避免泄漏 session（D7）。
            async with executor.container.metadata_session() as meta:
                onto = await meta.get_ontology(ontology)
                source_to_target = await meta.query_object_links_batch(onto.id, link_type, source_rids, direction)
    except Exception as exc:
        # Neo4j 不可用 → 降级 PG object_links（C5 best-effort，1跳 SQL 查询）
        import logging

        logging.getLogger(__name__).warning("Neo4j traverse failed, falling back to PG object_links: %s", exc)
        async with executor.container.metadata_session() as meta:
            onto = await meta.get_ontology(ontology)
            source_to_target = await meta.query_object_links_batch(onto.id, link_type, source_rids, direction)
        target_rids = list({t for ts in source_to_target.values() for t in ts})

    # 水合目标对象全量属性。
    objects: list[dict[str, Any]] = []
    if target_rids:
        from ontology.core.property_mapping import backing_to_api

        async with executor.container.metadata_session() as meta:
            states = await meta.get_object_states_by_rids(target_rids)
            # object_state 存 backing_column key；按每个状态的 object_type 解析
            # OT 后转为 api_name（语义层出口）。OT 解析失败→透传。
            ot_cache: dict[str, Any] = {}
            for s in states:
                ot_api = s.get("object_type_api_name", "")
                if ot_api and ot_api not in ot_cache:
                    try:
                        ot_cache[ot_api] = await meta.get_object_type(ontology, ot_api)
                    except Exception:
                        ot_cache[ot_api] = None
                props = backing_to_api(ot_cache.get(ot_api), s.get("properties", {}))
                # target_properties 投影。
                if target_properties:
                    props = {k: props.get(k) for k in target_properties if k in props}
                objects.append(
                    {
                        "rid": s["rid"],
                        "api_name": ot_api,
                        "props": props,
                    }
                )

    response: dict[str, Any] = {"target_objects": objects}
    if include_source_mapping:
        # map 的 key 用 Agent 传入的业务主键（pk），value 是 target rid 列表
        # （与 objects[].rid 一致，Agent 需要时可关联）。
        rid_to_pk = dict(zip(source_rids, source_keys))
        response["source_to_target_map"] = {
            rid_to_pk[rid]: target_rids for rid, target_rids in source_to_target.items()
        }
    return response


async def exists_link_logic(
    executor: ToolExecutor,
    ontology: str,
    link_type: str,
    source_key: str,
    direction: str = "forward",
    target_key: str | None = None,
) -> dict[str, Any]:
    """Check relationship existence (graph-reasoning M4).

    Implemented via Neo4jGraphStore.exists_link. ANY_TARGET mode when
    target_key is None; SINGLE_TARGET when provided. ``source_key`` /
    ``target_key`` are business primary-key values; resolved to rids via
    the pk→rid translation layer before reaching the graph engine.
    """
    from ontology.core.exceptions import NotFoundError
    from ontology.core.naming import graph_relationship_type

    graph_direction = "out" if direction == "forward" else "in"
    rel_type = graph_relationship_type(ontology, link_type)
    # source/target label + ObjectType（用于 pk→rid 翻译）。
    svc = executor.container.dataframe_query_service
    source_label = await svc._resolve_source_label(ontology, link_type)
    target_label = await svc._resolve_target_label(ontology, link_type)
    if not source_label or not target_label:
        return {"error": {"code": "LINK_NOT_FOUND", "message": f"link type {link_type} not in ontology {ontology}"}}

    # API 边界翻译：业务主键 → rid。
    source_ot = await svc._resolve_link_endpoint_ot(ontology, link_type, "source")
    target_ot = await svc._resolve_link_endpoint_ot(ontology, link_type, "target")
    if source_ot is None or target_ot is None:
        return {"error": {"code": "LINK_NOT_FOUND", "message": f"link type {link_type} not in ontology {ontology}"}}
    try:
        source_rid = (await svc._resolve_rids_by_pk(ontology, source_ot.api_name, [source_key]))[0]
        target_rid: str | None = None
        if target_key is not None:
            target_rid = (await svc._resolve_rids_by_pk(ontology, target_ot.api_name, [target_key]))[0]
    except NotFoundError as exc:
        return {"error": {"code": "NOT_FOUND", "message": str(exc)}}

    store = executor.container.graph_store
    exists = await executor.audit_call(
        "exists_link",
        {"ontology": ontology, "link_type": link_type, "source": source_key, "target": target_key},
        store.exists_link(
            rel_type=rel_type,
            source_label=source_label,
            source_rid=source_rid,
            target_label=target_label,
            target_rid=target_rid,
            direction=graph_direction,  # type: ignore[arg-type]
        ),
    )
    mode = "SINGLE_TARGET" if target_key else "ANY_TARGET"
    return {"exists": bool(exists), "mode": mode}


async def find_paths_logic(
    executor: ToolExecutor,
    ontology: str,
    source_key: str,
    target_key: str,
    link_types: list[str] | None = None,
    max_depth: int = 5,
    limit: int = 10,
) -> dict[str, Any]:
    """路径推理：源→目标的最短路径（Phase 2d find_paths）。

    调 Neo4jGraphStore.find_paths（allShortestPaths Cypher）。
    ``source_key`` / ``target_key`` 是业务主键值，经 pk→rid 翻译层解析为
    rid 后再调图引擎。返回的 paths 是 rid 序列。
    """
    from ontology.config.container import container
    from ontology.core.exceptions import NotFoundError
    from ontology.core.naming import graph_relationship_type

    svc = container.dataframe_query_service
    # find_paths 不绑定单个 link_type（可跨多个 link_types），source/target
    # 的 ObjectType 未知 → 需 Agent 显式传或从元数据推断。MVP：要求至少一个
    # link_type 来解析端点 ObjectType；无 link_type 时尝试用 source_key 在
    # 全本体 object_state 按 pk 查（跨类型扫描，小规模 OK）。
    try:
        if link_types:
            first_link = link_types[0]
            source_ot = await svc._resolve_link_endpoint_ot(ontology, first_link, "source")
            target_ot = await svc._resolve_link_endpoint_ot(ontology, first_link, "target")
            if source_ot is None or target_ot is None:
                msg = f"link type {first_link} not in ontology {ontology}"
                return {"error": {"code": "LINK_NOT_FOUND", "message": msg}}
            source_rid = (await svc._resolve_rids_by_pk(ontology, source_ot.api_name, [source_key]))[0]
            target_rid = (await svc._resolve_rids_by_pk(ontology, target_ot.api_name, [target_key]))[0]
        else:
            # 无 link_type：跨类型按 pk 扫描（查全本体 object_state）。
            source_rid = await svc._resolve_rid_by_pk_any_type(ontology, source_key)
            target_rid = await svc._resolve_rid_by_pk_any_type(ontology, target_key)
    except NotFoundError as exc:
        return {"error": {"code": "NOT_FOUND", "message": str(exc)}}

    store = container.graph_store
    paths = await store.find_paths(
        source_rid=source_rid,
        target_rid=target_rid,
        rel_types=[graph_relationship_type(ontology, lt) for lt in link_types] if link_types else None,
        max_depth=max_depth,
        limit=limit,
    )
    return {"source": source_key, "target": target_key, "paths": paths, "count": len(paths)}


# ── AG-UI exposure (pydantic-ai toolset, reads ctx.deps.ontology) ────────


def build_link_traversal_toolset(executor: ToolExecutor) -> FunctionToolset[AppState]:
    """Build the link-traversal toolset for the AG-UI path.

    ``list_link_types`` is READY (delegates to OntologyService).
    ``traverse_link`` and ``exists_link`` are SKELETONS returning
    TOOL_NOT_IMPLEMENTED pending LinkTraversalService (Sprint 3+).

    Tools read ``ctx.deps.ontology`` and default the ``ontology`` arg to it
    when the LLM omits it — see docs/architecture/rfcs/AI-context-scoping.md.
    """
    ts: FunctionToolset[AppState] = FunctionToolset()

    @ts.tool(description=LIST_LINK_TYPES_DESC)
    async def list_link_types(ctx: RunContext[AppState], ontology: str = "") -> list[dict[str, Any]]:
        """List all link (relationship) types in an ontology. (See shared description.)"""
        ontology = ontology or ctx.deps.ontology
        return await list_link_types_logic(executor, ontology)

    @ts.tool(description=TRAVERSE_LINK_DESC)
    async def traverse_link(
        ctx: RunContext[AppState],
        ontology: str = "",
        link_type: str = "",
        source_keys: list[str] | None = None,
        direction: str = "forward",
        target_filter: dict[str, Any] | None = None,
        target_properties: list[str] | None = None,
        include_source_mapping: bool = False,
    ) -> dict[str, Any]:
        """Traverse a single-hop relationship. (See shared description.)"""
        ontology = ontology or ctx.deps.ontology
        return await traverse_link_logic(
            executor,
            ontology,
            link_type,
            source_keys or [],
            direction,
            target_filter,
            target_properties,
            include_source_mapping,
        )

    @ts.tool(description=EXISTS_LINK_DESC)
    async def exists_link(
        ctx: RunContext[AppState],
        ontology: str = "",
        link_type: str = "",
        source_key: str = "",
        direction: str = "forward",
        target_key: str | None = None,
    ) -> dict[str, Any]:
        """Check whether a relationship exists. (See shared description.)"""
        ontology = ontology or ctx.deps.ontology
        return await exists_link_logic(
            executor,
            ontology,
            link_type,
            source_key,
            direction,
            target_key,
        )

    @ts.tool
    async def find_paths(
        ctx: RunContext[AppState],
        ontology: str = "",
        source_key: str = "",
        target_key: str = "",
        link_types: list[str] | None = None,
        max_depth: int = 5,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Find shortest paths between two objects via graph reasoning
        (Phase 2d path reasoning). Returns all shortest paths (rid
        sequences) from source to target through the relationship graph.

        Args:
            ontology: Ontology api_name. Omit to use the open ontology.
            source_key: Source object primary key (rid).
            target_key: Target object primary key (rid).
            link_types: Optional list of link type api_names to restrict
                traversal; None = any relationship.
            max_depth: Max hops (default 5). Increase cautiously — path
                explosion is exponential.
            limit: Max paths returned (default 10).

        Returns {"source","target","paths":[[rid,...]],"count"}. Each path
        is a rid sequence [source, ..., target]. Empty paths = no connection
        within max_depth.

        One-shot:
          find_paths("manufacturing", source_key="PO-001", target_key="C001")
          -> {"paths":[["PO-001","S007","C001"]], "count":1}
        """
        ontology = ontology or ctx.deps.ontology
        return await find_paths_logic(
            executor,
            ontology,
            source_key,
            target_key,
            link_types,
            max_depth,
            limit,
        )

    return ts
