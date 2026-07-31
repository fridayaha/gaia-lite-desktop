"""Query routes — load objects + aggregate + graph-reasoning with routing and fallback."""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ontology.config.container import container

# 属性过滤用的 PG engine（object_state JSONB 过滤）。
from ontology.config.database import engine as _query_engine
from ontology.core.schemas.object_set import ObjectSetIR, ReasoningResult
from ontology.core.schemas.query import (
    AggregationRequest,
    ExistsLinkRequest,
    FindPathsRequest,
    TextSqlRequest,
    TraverseRequest,
)
from ontology.services.object_query_service import ObjectQueryService
from ontology.services.object_set_executor import DataFrameQueryService


class SpatialFilterRequest(BaseModel):
    """Phase 2b 空间过滤请求体。"""

    object_type: str
    candidate_rids: list[str]
    op: str
    center: list[float] | None = None
    max_distance: float | None = None
    coords: list[list[float]] | None = None
    bbox: list[list[float]] | None = None
    geom_column: str = "location"


class SeriesQueryRequest(BaseModel):
    """Phase 2b 轨迹回放请求体。"""

    object_type: str
    series_property: str
    series_ids: list[str]
    time_start: str | None = None
    time_end: str | None = None
    limit: int = Field(default=10000, ge=1, le=100000)


router = APIRouter(prefix="/objects", tags=["objects"])


async def get_query_service() -> AsyncIterator[ObjectQueryService]:
    """Yield a request-scoped ObjectQueryService and close its session after.

    ``container.object_query_service`` builds a fresh service with a fresh
    ``AsyncSession`` on each access; if we don't close it the connection
    leaks (idle-in-transaction) and the QueuePool exhausts under load.
    """
    service = container.object_query_service
    try:
        yield service
    finally:
        await service.aclose()


@router.post("/aggregate", response_model=list[dict[str, Any]])
async def aggregate_objects(
    request: AggregationRequest,
    http_request: Request,
    service: ObjectQueryService = Depends(get_query_service),
) -> list[dict[str, object]]:
    """Aggregate over an object set, optionally grouped.

    Primary path: Doris (online read source). Falls back to Trino for
    VIRTUAL types or when Doris is unavailable.
    """
    return await service.aggregate_by_request(request, principal=getattr(http_request.state, "principal", None))


@router.post("/textsql", response_model=list[dict[str, Any]])
async def textsql_query(
    request: TextSqlRequest,
    http_request: Request,
    service: ObjectQueryService = Depends(get_query_service),
) -> list[dict[str, object]]:
    """Run a text2sql-compiled query (ADR-012 Step 4 path B).

    Compiles ``logical_sql`` (ObjectType api_name as table, property
    api_name as column) to physical Doris SQL via the OntologySqlCompiler
    (enforcing table/column/join guardrails), runs it on Doris, and falls
    back to Trino when Doris is unavailable. Rows return with property
    api_names as keys.

    Every ObjectType referenced in the SQL is auto-inferred for access
    check, storage routing, and column remapping — no separate
    ``object_type`` field in the request. This endpoint exposes the
    deterministic compile+execute path used by TextQL without requiring
    an LLM round-trip — useful for benchmarks, ad-hoc ontology SQL, and
    validating that a logical SQL compiles.
    """
    return await service.execute_compiled_sql(
        request.ontology_api_name,
        request.logical_sql,
        principal=getattr(http_request.state, "principal", None),
    )


# ── Graph-reasoning (推理线, graph-reasoning-design.md §11) ──


async def get_dataframe_service() -> AsyncIterator[DataFrameQueryService]:
    """Yield a request-scoped DataFrameQueryService.

    Uses ``container.metadata_session`` so the metadata AsyncSession is
    properly closed (the service reads ObjectType/LinkType metadata and
    hydrates via object_state).
    """
    async with container.metadata_session() as meta:
        svc = DataFrameQueryService(
            graph_store=container.graph_store,
            geotime_store=container.geotime_store,
            metadata=meta,
            attr_engine=_query_engine,
        )
        yield svc


@router.post("/{ontology}/query-dataframe", response_model=ReasoningResult)
async def query_dataframe(
    ontology: str,
    object_set_ir: ObjectSetIR,
    cursor: str | None = None,
) -> ReasoningResult:
    """Execute a graph-reasoning query via ObjectSet IR (推理线).

    NL → ObjectSet IR → Neo4j (graph traversal) + PostGIS (spatial) +
    TimescaleDB (temporal) + object_state hydration. Independent of the
    SQL line (``/objects/textsql``): two lines converge only at hydration.

    cursor: 分页游标（上页 next_cursor），从此 rid 之后开始水合。None 从头开始。
    分页时 IR 必须与上一次调用完全一致（cursor 依赖稳定的 rid 顺序）。

    Shares ``query_with_dataframe_logic`` with the MCP / AG-UI tool so all
    three entry points agree on cursor semantics + audit coverage
    (ADR-019 §4 操作面对等原则).
    """
    from ontology.tools.executor import ToolExecutor
    from ontology.tools.toolsets.reasoning import query_with_dataframe_logic

    executor = ToolExecutor(container)
    result = await query_with_dataframe_logic(executor, ontology, object_set_ir.model_dump(), cursor=cursor)
    return ReasoningResult.model_validate(result)


# 注：``/object-set`` 别名已移除（曾与 query-dataframe 重复）。历史调用方请改用
# ``/query-dataframe``——两者实现完全相同，仅保留单一入口，避免外部集成者
# 在两个等价端点间困惑。NL/IR 查询统一走此端点；NL → IR 的转换由
# ``/ai/agent`` (AG-UI ReAct Agent) 或 MCP ``query_with_dataframe`` 工具完成。

# 注：query-nl / explore-plan 路由已删除（ADR-015）。
# 图探索的 NL 查询统一走 /ai/agent（AG-UI ReAct Agent），
# 由 Agent 自行决定调用 query_with_dataframe / traverse_link 等工具，
# 并通过 CanvasSnapshot shared state 驱动画布。
# 旧的 should_route_to_object_set 关键词路由 + 一次性编排（explore_plan_parser）
# 一并移除——见 docs/architecture/adr-015-agent-driven-graph-explore.md。


@router.post("/{ontology}/traverse")
async def traverse_link_route(ontology: str, body: TraverseRequest) -> dict[str, Any]:
    """Single-hop relationship traversal (graph-reasoning §11.1).

    Traverse from one or more source objects to their linked target(s).
    See ``TraverseRequest`` for the body contract; the response shape is
    governed by the link's cardinality (ONE → single object/null per source,
    MANY → list).
    """
    from ontology.tools.executor import ToolExecutor
    from ontology.tools.toolsets.link_traversal import traverse_link_logic

    executor = ToolExecutor(container)
    return await traverse_link_logic(
        executor,
        ontology,
        body.link_type,
        body.source_keys,
        body.direction,
        body.target_filter,
        body.target_properties,
        body.include_source_mapping,
    )


@router.post("/{ontology}/exists-link")
async def exists_link_route(ontology: str, body: ExistsLinkRequest) -> dict[str, Any]:
    """Check relationship existence (graph-reasoning §11.1).

    Returns ``{"exists": bool, "mode": "ANY_TARGET"|"SINGLE_TARGET"}``.
    Objects/links you lack permission to see count as NOT existing.
    """
    from ontology.tools.executor import ToolExecutor
    from ontology.tools.toolsets.link_traversal import exists_link_logic

    executor = ToolExecutor(container)
    return await exists_link_logic(
        executor,
        ontology,
        body.link_type,
        body.source_key,
        body.direction,
        body.target_key,
    )


@router.post("/{ontology}/find-paths")
async def find_paths_route(ontology: str, body: FindPathsRequest) -> dict[str, Any]:
    """Find shortest paths between two objects (Phase 2d path reasoning).

    Returns ``{"source", "target", "paths": [[rid,...],...], "count"}``.
    """
    from ontology.tools.executor import ToolExecutor
    from ontology.tools.toolsets.link_traversal import find_paths_logic

    executor = ToolExecutor(container)
    return await find_paths_logic(
        executor,
        ontology,
        body.source_key,
        body.target_key,
        body.link_types,
        body.max_depth,
        body.limit,
    )


@router.get("/{ontology}/analysis/{analysis_id}")
async def get_analysis_record(ontology: str, analysis_id: str) -> dict[str, Any]:
    """Retrieve an analysis-record evidence snapshot (graph-reasoning §11.1, M6).

    Returns the stored ObjectSet IR + per-step engine timings + matched
    object count + evidence pointers. Used for compliance traceability
    ("who queried what, when, via which engines, hitting which objects").
    """
    from ontology.services.analysis_record_store import AnalysisRecordStore

    async with container.metadata_session() as meta:
        store = AnalysisRecordStore(meta.session)
        record = await store.get(analysis_id)
    if record is None:
        from ontology.core.exceptions import NotFoundError

        raise NotFoundError("AnalysisRecord", analysis_id)
    return record.model_dump(mode="json")


# ── 空间/时序查询（Phase 2b 前端地图 + 轨迹回放）──


@router.post("/{ontology}/spatial-filter")
async def spatial_filter(ontology: str, req: SpatialFilterRequest) -> list[str]:
    """空间过滤：从候选 rid 集中返回命中空间条件的 rid（PostGIS GiST 索引）。

    供前端 MapPanel 框选/圈选/多边形过滤。返回命中的 rid 列表，
    前端再据此刻画/高亮图谱节点（F6）。
    """
    from typing import cast

    from ontology.core.naming import geo_table
    from ontology.core.schemas.geotime import SpatialFilter, SpatialOp

    table = geo_table(ontology, req.object_type)
    store = container.geotime_store
    # 确保空间表存在（未投影时返回空）。
    if not await store.table_exists(table):
        return []
    spatial = SpatialFilter(
        op=cast(SpatialOp, req.op),
        center=req.center,
        max_distance=req.max_distance,
        coords=req.coords,
        bbox=req.bbox,
    )
    return await store.spatial_filter(table, req.candidate_rids, spatial, geom_column=req.geom_column)


@router.post("/{ontology}/series-query")
async def series_query(ontology: str, req: SeriesQueryRequest) -> list[dict[str, Any]]:
    """时序查询：按 series_id + 时间窗口返回轨迹点（TimescaleDB hypertable）。

    供前端 TrajectoryPlayer 轨迹回放（F7）。每行含 series_id/timestamp/
    可选空间列（location）/ 可选值列。
    """
    from datetime import datetime

    from ontology.core.naming import timeseries_hypertable

    table = timeseries_hypertable(ontology, req.object_type, req.series_property)
    store = container.geotime_store
    if not await store.table_exists(table):
        return []
    time_range: tuple[Any, Any] | None = None
    if req.time_start and req.time_end:
        time_range = (
            datetime.fromisoformat(req.time_start),
            datetime.fromisoformat(req.time_end),
        )
    return await store.series_query(table, req.series_ids, time_range=time_range, limit=req.limit)
