"""DataSource, Credential, SyncTask, Dataset REST API routes.

These routes form the data layer HTTP interface, following the
Palantir-style API shape defined in docs/data-layer-design.md §6.
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.schemas.datasource import (
    ConnectionTestResult,
    CredentialCreate,
    CredentialResponse,
    DatasetGovernance,
    DatasetGovernanceCreate,
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    ExploreRequest,
    ExploreResult,
    ImpactAnalysis,
    ImpactAnalysisRequest,
    PaginatedDatasets,
    SyncTask,
    SyncTaskCreate,
    TableInfo,
    VirtualTableRegistration,
)
from ontology.services.datasource_service import DataSourceService

router = APIRouter(prefix="/api", tags=["datasource"])


async def get_datasource_service() -> AsyncIterator[DataSourceService]:
    """Yield a request-scoped DataSourceService and close its session after."""
    service = container.datasource_service
    try:
        yield service
    finally:
        await service.aclose()


# ═════════════════════════════════════════════════════════════════
# Credentials
# ═════════════════════════════════════════════════════════════════


@router.post("/credentials", response_model=CredentialResponse, status_code=201)
async def create_credential(
    cred: CredentialCreate, service: DataSourceService = Depends(get_datasource_service)
) -> CredentialResponse:
    """Create a new credential."""
    try:
        return await service.create_credential(cred)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/credentials", response_model=list[CredentialResponse])
async def list_credentials(service: DataSourceService = Depends(get_datasource_service)) -> list[CredentialResponse]:
    """List all credentials (secrets masked)."""
    return await service.list_credentials()


@router.delete("/credentials/{api_name}", status_code=204)
async def delete_credential(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> None:
    """Delete a credential."""
    try:
        await service.delete_credential(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# DataSources
# ═════════════════════════════════════════════════════════════════


@router.post("/datasources", response_model=DataSource, status_code=201)
async def create_datasource(
    ds: DataSourceCreate, service: DataSourceService = Depends(get_datasource_service)
) -> DataSource:
    """Create a new data source.

    Registers the source in PG metadata and in Gravitino as a JDBC Catalog.
    """
    try:
        return await service.create_datasource(ds)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/datasources", response_model=list[DataSource])
async def list_datasources(service: DataSourceService = Depends(get_datasource_service)) -> list[DataSource]:
    """List all data sources."""
    return await service.list_datasources()


@router.get("/datasources/{api_name}", response_model=DataSource)
async def get_datasource(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> DataSource:
    """Get a data source by api_name."""
    try:
        return await service.get_datasource(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/datasources/{api_name}", response_model=DataSource)
async def update_datasource(
    api_name: str, updates: DataSourceUpdate, service: DataSourceService = Depends(get_datasource_service)
) -> DataSource:
    """Update a data source."""
    try:
        return await service.update_datasource(api_name, updates)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/datasources/{api_name}", status_code=204)
async def delete_datasource(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> None:
    """Delete a data source and its Gravitino catalog.

    Cascades to all associated sync tasks.
    """
    try:
        await service.delete_datasource(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# Connection Test
# ═════════════════════════════════════════════════════════════════


@router.post("/datasources/{api_name}/test-connection", response_model=ConnectionTestResult)
async def test_connection(
    api_name: str, service: DataSourceService = Depends(get_datasource_service)
) -> ConnectionTestResult:
    """Test connectivity to a data source via Trino."""
    try:
        return await service.test_connection(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# Exploration
# ═════════════════════════════════════════════════════════════════


@router.post("/datasources/{api_name}/explore", response_model=ExploreResult)
async def explore_datasource(
    api_name: str,
    request: ExploreRequest = ExploreRequest(),
    service: DataSourceService = Depends(get_datasource_service),
) -> ExploreResult:
    """Explore a data source's schema.

    Returns list of tables WITHOUT column details — columns are
    loaded on-demand via describe_table (POST /explore/{database}/{table}).
    This keeps initial load instant regardless of the number of tables.
    """
    try:
        return await service.explore(api_name, request.database)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post(
    "/datasources/{api_name}/explore/{database}/{table}",
    response_model=TableInfo,
)
async def describe_table(
    api_name: str, database: str, table: str, service: DataSourceService = Depends(get_datasource_service)
) -> TableInfo:
    """Describe a single table's columns — lazy-loaded on user click."""
    try:
        return await service.describe_table(api_name, database, table)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/datasources/{api_name}/explore/{database}/{table}/sample")
async def sample_data(
    api_name: str,
    database: str,
    table: str,
    limit: int = 10,
    service: DataSourceService = Depends(get_datasource_service),
) -> list[dict[str, Any]]:
    """Sample rows from a data source table."""
    try:
        return await service.sample_data(api_name, database, table, limit)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# Virtual Table Registration
# ═════════════════════════════════════════════════════════════════


@router.post(
    "/datasources/{datasource_api_name}/virtual-tables",
    response_model=DatasetGovernance,
    status_code=201,
)
async def register_virtual_table(
    datasource_api_name: str,
    body: VirtualTableRegistration,
    service: DataSourceService = Depends(get_datasource_service),
) -> DatasetGovernance:
    """Register an external table as a kind=VIRTUAL dataset.

    The external table is added to the dataset catalog as a Virtual Table
    (Trino-federated proxy, no physical storage). It can then be bound by a
    storage_type=VIRTUAL ObjectType. See dataset-ontology-binding.md §3.2.
    """
    try:
        return await service.register_virtual_table(
            datasource_api_name=datasource_api_name,
            database=body.database,
            table=body.table,
            api_name=body.api_name,
            display_name=body.display_name,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# Sync Tasks
# ═════════════════════════════════════════════════════════════════


@router.post("/datasources/{datasource_api_name}/sync-tasks", response_model=SyncTask, status_code=201)
async def create_sync_task(
    datasource_api_name: str, task: SyncTaskCreate, service: DataSourceService = Depends(get_datasource_service)
) -> SyncTask:
    """Create a sync task for a data source."""
    try:
        # Resolve data source ID from api_name
        ds = await service.get_datasource(datasource_api_name)
        task_with_ds = SyncTaskCreate(
            api_name=task.api_name,
            data_source_id=ds.id,
            sync_type=task.sync_type,
            source_config=task.source_config,
            target_dataset_api_name=task.target_dataset_api_name,
            sync_mode=task.sync_mode,
            transaction_type=task.transaction_type,
            allow_schema_changes=task.allow_schema_changes,
            max_duration_minutes=task.max_duration_minutes,
            file_filters=task.file_filters,
            schedule=task.schedule,
        )
        return await service.create_sync_task(task_with_ds)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/datasources/{datasource_api_name}/sync-tasks", response_model=list[SyncTask])
async def list_sync_tasks(
    datasource_api_name: str, service: DataSourceService = Depends(get_datasource_service)
) -> list[SyncTask]:
    """List all sync tasks for a data source."""
    try:
        return await service.list_sync_tasks(datasource_api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/sync-tasks/{api_name}", response_model=SyncTask)
async def get_sync_task(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> SyncTask:
    """Get a sync task by api_name."""
    try:
        return await service.get_sync_task(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/sync-tasks/{api_name}/start", response_model=SyncTask)
async def start_sync_task(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> SyncTask:
    """Start a sync task."""
    try:
        return await service.start_sync(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


class CdcSyncRequest(BaseModel):
    """Request body for starting an external-source CDC → Iceberg sync (§7.3)."""

    datasource_api_name: str
    source_table: str
    target_dataset_api_name: str
    cdc_config: dict[str, Any] = Field(default_factory=dict)
    primary_keys: list[str] | None = None
    task_api_name: str | None = None


class TimeseriesSyncRequest(BaseModel):
    """Request body for starting a Kafka → TimescaleDB hypertable sync (§5.3, C3)."""

    kafka_topic: str
    target_hypertable: str
    schema_fields: dict[str, str] = Field(default_factory=dict)
    primary_keys: list[str] | None = None
    consumer_group: str | None = None
    task_api_name: str | None = None


@router.post("/datasources/{datasource_api_name}/cdc-sync", response_model=SyncTask)
async def start_cdc_sync(
    datasource_api_name: str,
    body: CdcSyncRequest,
    service: DataSourceService = Depends(get_datasource_service),
) -> SyncTask:
    """Start an external-source CDC → Iceberg sync (post-spike, §7.3).

    Streams changes from an external business DB (MySQL/PG/OpenGauss/TiDB)
    into an Iceberg managed table. Requires the CDC spike to have validated
    the path. ``primary_keys`` is strongly recommended to avoid append-only
    CDC data loss (SeaTunnel #10747).
    """
    # URL path param wins for the datasource identity; body field is
    # accepted for API symmetry but ignored.
    try:
        return await service.start_cdc_sync(
            datasource_api_name=datasource_api_name,
            source_table=body.source_table,
            target_dataset_api_name=body.target_dataset_api_name,
            cdc_config=body.cdc_config,
            primary_keys=body.primary_keys,
            task_api_name=body.task_api_name,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/datasources/{datasource_api_name}/timeseries-sync", response_model=SyncTask)
async def start_timeseries_sync(
    datasource_api_name: str,
    body: TimeseriesSyncRequest,
    service: DataSourceService = Depends(get_datasource_service),
) -> SyncTask:
    """Start a Kafka → TimescaleDB hypertable streaming sync (§5.3, C3).

    动态时序数据走流式独立链路（不经 Iceberg/Action/object_state）。超表必须
    由 GeoTimeStore.create_timeseries_hypertable 预建（define 含时序属性的
    ObjectType 时自动创建）。
    """
    try:
        return await service.start_timeseries_sync(
            datasource_api_name=datasource_api_name,
            kafka_topic=body.kafka_topic,
            target_hypertable=body.target_hypertable,
            schema_fields=body.schema_fields,
            primary_keys=body.primary_keys,
            consumer_group=body.consumer_group,
            task_api_name=body.task_api_name,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/sync-tasks/{api_name}/stop", response_model=SyncTask)
async def stop_sync_task(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> SyncTask:
    """Stop a sync task."""
    try:
        return await service.stop_sync(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/sync-tasks/{api_name}/refresh", response_model=SyncTask)
async def refresh_sync_task(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> SyncTask:
    """Reconcile a sync task's status with SeaTunnel.

    Polls SeaTunnel for the job's real state and persists it. The UI
    should call this when displaying a sync task list or detail page so
    that "已完成 / 失败 / 运行中" reflects SeaTunnel truth rather than the
    optimistic value written when the task was started.
    """
    try:
        return await service.refresh_sync_status(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post(
    "/datasources/{datasource_api_name}/sync-tasks/refresh-batch",
    response_model=list[SyncTask],
)
async def refresh_all_sync_tasks(
    datasource_api_name: str,
    service: DataSourceService = Depends(get_datasource_service),
) -> list[SyncTask]:
    """Batch-reconcile ALL of a datasource's sync tasks in 2 SeaTunnel calls.

    Replaces the N+1 anti-pattern of calling ``/sync-tasks/{name}/refresh``
    once per task (each call re-fetching the full SeaTunnel job lists).
    Fetches both running + finished lists once and matches every task
    locally. Use this when loading a sync task list to avoid request
    storms on datasources with many tasks.
    """
    try:
        return await service.refresh_all_sync_status(datasource_api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/sync-tasks/{api_name}", status_code=204)
async def delete_sync_task(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> None:
    """Delete a sync task."""
    try:
        await service.delete_sync_task(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# Datasets
# ═════════════════════════════════════════════════════════════════


@router.post("/datasets", response_model=DatasetGovernance, status_code=201)
async def register_dataset(
    ds: DatasetGovernanceCreate, service: DataSourceService = Depends(get_datasource_service)
) -> DatasetGovernance:
    """Register a dataset in PG governance metadata."""
    try:
        return await service.register_dataset(ds)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/datasets", response_model=list[DatasetGovernance])
async def list_datasets(service: DataSourceService = Depends(get_datasource_service)) -> list[DatasetGovernance]:
    """List all datasets (full, unpaginated).

    Kept for callers that need the full list (DataSourceDetail,
    CreateObjectWizard, etc.). The datasets list page should use
    ``GET /datasets/paginated`` instead to avoid loading everything.
    """
    return await service.list_datasets()


@router.get("/datasets/paginated", response_model=PaginatedDatasets)
async def list_datasets_paginated(
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    type: str = "",
    ontology: str = "",
    service: DataSourceService = Depends(get_datasource_service),
) -> PaginatedDatasets:
    """Paginated, filtered dataset list for the datasets management page.

    Returns items + total + page metadata so the client can render a pager
    without a second round-trip. page is 1-based; page_size clamped to [1, 200].
    Filters: search (substring on api_name/display_name), type (managed|virtual|
    transform), ontology (ontology api_name — filters by backing reference).
    """
    page = max(1, page)
    page_size = max(1, min(200, page_size))
    items, total = await service.list_datasets_paginated(
        page=page,
        page_size=page_size,
        search=search.strip(),
        type_filter=type.strip(),
        ontology_api_name=ontology.strip(),
    )
    return PaginatedDatasets(items=items, total=total, page=page, page_size=page_size)


@router.get("/datasets/ontology-map")
async def get_dataset_ontology_map(
    service: DataSourceService = Depends(get_datasource_service),
) -> dict[str, list[dict[str, str]]]:
    """Reverse-lookup map: dataset api_name -> referencing ontologies.

    Aggregates ObjectType top-level + PropertyDef per-column backing refs in
    one pass. Lets the datasets list page show "归属本体" and filter by
    ontology without N+1 ObjectType detail fetches on the client.
    """
    return await service.get_dataset_ontology_map()


@router.get("/datasets/{api_name}", response_model=DatasetGovernance)
async def get_dataset(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> DatasetGovernance:
    """Get dataset governance metadata."""
    try:
        return await service.get_dataset(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/datasets/{api_name}/refresh-stats", response_model=DatasetGovernance)
async def refresh_dataset_stats(
    api_name: str, service: DataSourceService = Depends(get_datasource_service)
) -> DatasetGovernance:
    """Refresh a dataset's row_count_estimate via Trino (Iceberg for MANAGED,
    federation for VIRTUAL) and return the updated governance record."""
    try:
        await service.refresh_row_count(api_name)
        return await service.get_dataset(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/datasets/{api_name}", response_model=DatasetGovernance)
async def update_dataset(
    api_name: str, updates: dict[str, Any], service: DataSourceService = Depends(get_datasource_service)
) -> DatasetGovernance:
    """Update dataset governance metadata."""
    try:
        return await service.update_dataset(api_name, updates)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/datasets/{api_name}", status_code=204)
async def delete_dataset(api_name: str, service: DataSourceService = Depends(get_datasource_service)) -> None:
    """Delete dataset governance metadata."""
    try:
        return await service.delete_dataset(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/datasets/{api_name}/schema")
async def get_dataset_schema(
    api_name: str, service: DataSourceService = Depends(get_datasource_service)
) -> dict[str, Any]:
    """Get the Iceberg physical schema for a dataset."""
    try:
        schema = await service.get_dataset_schema(api_name)
        return schema.model_dump(by_alias=True)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/datasets/{api_name}/snapshots")
async def get_dataset_snapshots(
    api_name: str, service: DataSourceService = Depends(get_datasource_service)
) -> list[dict[str, Any]]:
    """Get the Iceberg snapshot history for a dataset."""
    try:
        snapshots = await service.get_dataset_snapshots(api_name)
        return [s.model_dump() for s in snapshots]
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═════════════════════════════════════════════════════════════════
# Impact Analysis
# ═════════════════════════════════════════════════════════════════


@router.post("/impact-analysis", response_model=ImpactAnalysis)
async def analyze_impact(
    request: ImpactAnalysisRequest, service: DataSourceService = Depends(get_datasource_service)
) -> ImpactAnalysis:
    """Analyze the impact of a proposed destructive operation.

    Used by the frontend for confirmation dialogs:
      LOW → simple dialog
      MEDIUM → list affected resources
      HIGH → require typing the name to confirm
    """
    try:
        return await service.analyze_impact(request)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
