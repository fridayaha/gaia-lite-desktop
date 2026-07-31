"""Pipeline Builder REST API routes (ADR-018 D9).

Design follows Palantir Deploy/Build separation, Schedule as independent
resource, Release Stage annotation via x-release-stage.

Endpoint prefix: /api/v1/pipelines
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.schemas.pipeline_builder import (
    BuildDetailResponse,
    BuildRequest,
    BuildResponse,
    DeployRequest,
    DeployResponse,
    OperatorCatalogResponse,
    OperatorSpecResponse,
    PipelineCreate,
    PipelineIR,
    PipelineListResponse,
    PipelineResponse,
    PipelineUpdate,
    PipelineVersionResponse,
    ScheduleCreate,
    ScheduleResponse,
    ScheduleUpdate,
    ValidationResponse,
)
from ontology.services.pipeline_builder_service import PipelineBuilderService

router = APIRouter(prefix="/api/v1/pipelines", tags=["pipelines"])


async def get_pipeline_service() -> AsyncIterator[PipelineBuilderService]:
    """Yield a request-scoped PipelineBuilderService and close its session after."""
    svc = container.pipeline_builder_service
    try:
        yield svc
    finally:
        await svc.aclose()


# ═══════════════════════════════════════════════════════════════════
# Pipeline CRUD
# ═══════════════════════════════════════════════════════════════════


@router.post("", response_model=PipelineResponse, status_code=201, openapi_extra={"x-release-stage": "beta"})
async def create_pipeline(
    data: PipelineCreate,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> PipelineResponse:
    """Create a pipeline with an initial version.

    The ``graph`` field in the request body is the Pipeline IR (nodes + edges).
    The initial version is saved as version 1.
    """
    try:
        return await service.create_pipeline(data)
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("", response_model=PipelineListResponse, openapi_extra={"x-release-stage": "beta"})
async def list_pipelines(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> PipelineListResponse:
    """List pipelines with optional filters and pagination."""
    items, total = await service.list_pipelines(
        project_id=project_id,
        status=status,
        offset=offset,
        limit=limit,
    )
    return PipelineListResponse(items=items, total=total, offset=offset, limit=limit)


@router.get("/operators", response_model=OperatorCatalogResponse, openapi_extra={"x-release-stage": "beta"})
async def list_operators(
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> OperatorCatalogResponse:
    """List all available operators (core + Kestra plugin extensions).

    Core operators are the 14 Gaia-native transforms with full schema
    inference support. Kestra plugins are proxied from Kestra's plugin
    endpoint (availability depends on Kestra connectivity).
    """
    from ontology.config.container import container as c

    engine = c.schema_inference_engine
    operators = []
    for spec in engine.registry.get_all():
        operators.append(
            OperatorSpecResponse(
                type=spec.type,
                category=spec.category,
                display_name=spec.display_name,
                description=spec.description,
                input_ports=spec.input_ports,
                output_ports=spec.output_ports,
                config_schema=spec.config_schema,
                output_schema_rule=spec.output_schema_rule if hasattr(spec, 'output_schema_rule') else "",
            )
        )

    # Try to fetch Kestra plugins
    kestra_plugins = []
    try:
        kestra = c.kestra_engine
        plugins = await kestra.list_plugins()
        for p in plugins[:50]:  # Limit to first 50
            kestra_plugins.append(
                {
                    "type": p.get("type", ""),
                    "display_name": p.get("title", p.get("type", "")),
                    "category": p.get("group", "script"),
                    "no_code_schema": p.get("properties", {}),
                }
            )
    except Exception:
        pass  # Kestra may not be available

    # Build proper response (convert dicts to model instances)
    from ontology.core.schemas.pipeline_builder import KestraPluginResponse

    return OperatorCatalogResponse(
        operators=operators,
        kestra_plugins=[KestraPluginResponse(**p) for p in kestra_plugins],
    )


@router.post("/validate", response_model=ValidationResponse, openapi_extra={"x-release-stage": "experimental"})
async def validate_raw_graph(
    graph: PipelineIR,
    sink_dataset_api_name: str | None = Query(default=None),
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> ValidationResponse:
    """Validate a raw Pipeline IR (without saving).

    Useful for pre-submit checks before PATCH.
    """
    return await service.validate_pipeline(
        graph=graph,
        sink_dataset_api_name=sink_dataset_api_name,
    )


# ═══════════════════════════════════════════════════════════════════
# Pipeline CRUD (static routes above must precede /{api_name} to avoid
# being captured by the path parameter)
# ═══════════════════════════════════════════════════════════════════


@router.get("/{api_name}", response_model=PipelineResponse, openapi_extra={"x-release-stage": "beta"})
async def get_pipeline(
    api_name: str,
    include_deleted: bool = Query(default=False),
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> PipelineResponse:
    """Get pipeline details by api_name."""
    try:
        return await service.get_pipeline(api_name, include_deleted=include_deleted)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/{api_name}", response_model=PipelineResponse, openapi_extra={"x-release-stage": "beta"})
async def update_pipeline(
    api_name: str,
    data: PipelineUpdate,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> PipelineResponse:
    """Update pipeline — creates a new version when ``graph`` is supplied.

    Each save of ``graph`` produces a new version (version_number auto-incremented).
    Scalar field updates (display_name, description, etc.) do NOT create versions.
    """
    try:
        return await service.update_pipeline(api_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/{api_name}", status_code=204, openapi_extra={"x-release-stage": "beta"})
async def delete_pipeline(
    api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> None:
    """Soft-delete a pipeline (preserves execution history)."""
    try:
        await service.delete_pipeline(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════════
# Pipeline Versions
# ═══════════════════════════════════════════════════════════════════


@router.get("/{api_name}/versions", response_model=list[PipelineVersionResponse], openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def list_versions(
    api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> list[PipelineVersionResponse]:
    """List all versions of a pipeline (newest first)."""
    try:
        return await service.list_versions(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{api_name}/versions/{version_number}", response_model=PipelineVersionResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def get_version(
    api_name: str,
    version_number: int,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> PipelineVersionResponse:
    """Get a specific version with its full Pipeline IR."""
    try:
        return await service.get_version(api_name, version_number)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{api_name}/versions/{version_number}/rollback", response_model=PipelineResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def rollback_version(
    api_name: str,
    version_number: int,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> PipelineResponse:
    """Rollback to a specific version (switch current_version_id).

    This updates the logical definition only — it does NOT trigger a data build.
    To materialise data after rollback, call POST /builds.
    """
    try:
        return await service.rollback_version(api_name, version_number)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════


@router.post("/{api_name}/validate", response_model=ValidationResponse, openapi_extra={"x-release-stage": "beta"})
async def validate_pipeline(
    api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> ValidationResponse:
    """Validate the current pipeline IR — synchronous schema inference.

    Returns contract violations (ERROR/WARNING/INFO) and the final
    inferred output schema. Does NOT touch any data.
    """
    try:
        return await service.validate_pipeline(api_name=api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════════
# Deploy & Build
# ═══════════════════════════════════════════════════════════════════


@router.post("/{api_name}/deploy", response_model=DeployResponse, openapi_extra={"x-release-stage": "beta"})
async def deploy_pipeline(
    api_name: str,
    request: DeployRequest | None = None,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> DeployResponse:
    """Deploy a pipeline: DRAFT → PUBLISHED, prepare for Kestra execution.

    Deploy updates the logical definition (translated to Kestra Flow).
    It does NOT trigger a data build. Use POST /builds to materialise data.
    """
    try:
        return await service.deploy_pipeline(api_name, request)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/{api_name}/builds", response_model=BuildResponse, status_code=202, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def trigger_build(
    api_name: str,
    request: BuildRequest | None = None,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> BuildResponse:
    """Trigger a pipeline build (data materialisation).

    Returns 202 Accepted with the build ID. Use GET /builds/{id} to poll status.
    """
    try:
        return await service.trigger_build(api_name, request)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/{api_name}/deprecate", response_model=DeployResponse, openapi_extra={"x-release-stage": "beta"})
async def deprecate_pipeline(
    api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> DeployResponse:
    """Deprecate a pipeline (PUBLISHED → DEPRECATED + Kestra undeploy)."""
    try:
        return await service.deprecate_pipeline(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════════
# Build Monitoring
# ═══════════════════════════════════════════════════════════════════


@router.get("/{api_name}/builds", response_model=list[BuildResponse], openapi_extra={"x-release-stage": "beta"})
async def list_builds(
    api_name: str,
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> list[BuildResponse]:
    """List builds (executions) for a pipeline."""
    try:
        items, _ = await service.list_builds(api_name, status=status, offset=offset, limit=limit)
        return items
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{api_name}/builds/{build_id}", response_model=BuildDetailResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def get_build(
    api_name: str,
    build_id: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> BuildDetailResponse:
    """Get build details with node runs and state history."""
    try:
        return await service.get_build_detail(api_name, build_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{api_name}/builds/{build_id}/cancel", response_model=BuildResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def cancel_build(
    api_name: str,
    build_id: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> BuildResponse:
    """Cancel a running or pending build."""
    try:
        return await service.cancel_build(api_name, build_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/{api_name}/builds/{build_id}/rollback", response_model=dict[str, Any], openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def data_rollback(
    api_name: str,
    build_id: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> dict[str, Any]:
    """Data rollback: switch the output dataset's ``current_snapshot_id``.

    Switches the sink dataset to the snapshot produced by this build.
    This is a metadata-only operation — data is NOT re-processed.
    """
    try:
        return await service.data_rollback(api_name, build_id)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════════
# Schedules
# ═══════════════════════════════════════════════════════════════════


@router.post("/{api_name}/schedules", response_model=ScheduleResponse, status_code=201, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def create_schedule(
    api_name: str,
    data: ScheduleCreate,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> ScheduleResponse:
    """Create a schedule for a pipeline (e.g. cron trigger)."""
    try:
        return await service.create_schedule(api_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/{api_name}/schedules", response_model=list[ScheduleResponse], openapi_extra={"x-release-stage": "beta"})
async def list_schedules(
    api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> list[ScheduleResponse]:
    """List schedules for a pipeline."""
    try:
        return await service.list_schedules(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/{api_name}/schedules/{schedule_api_name}", response_model=ScheduleResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def update_schedule(
    api_name: str,
    schedule_api_name: str,
    data: ScheduleUpdate,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> ScheduleResponse:
    """Update a schedule."""
    try:
        return await service.update_schedule(api_name, schedule_api_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/{api_name}/schedules/{schedule_api_name}", status_code=204, openapi_extra={"x-release-stage": "beta"})
async def delete_schedule(
    api_name: str,
    schedule_api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> None:
    """Delete a schedule."""
    try:
        await service.delete_schedule(api_name, schedule_api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{api_name}/schedules/{schedule_api_name}/enable", response_model=ScheduleResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def enable_schedule(
    api_name: str,
    schedule_api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> ScheduleResponse:
    """Enable a schedule."""
    try:
        return await service.toggle_schedule(api_name, schedule_api_name, enabled=True)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/{api_name}/schedules/{schedule_api_name}/disable", response_model=ScheduleResponse, openapi_extra={"x-release-stage": "beta"})  # noqa: E501
async def disable_schedule(
    api_name: str,
    schedule_api_name: str,
    service: PipelineBuilderService = Depends(get_pipeline_service),
) -> ScheduleResponse:
    """Disable a schedule."""
    try:
        return await service.toggle_schedule(api_name, schedule_api_name, enabled=False)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


