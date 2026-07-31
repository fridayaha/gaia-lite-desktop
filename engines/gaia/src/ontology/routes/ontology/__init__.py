"""Ontology CRUD routes — Ontology, ObjectType, Property, LinkType, ActionType, DataSource."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.models.defaults import utcnow
from ontology.core.schemas.ontology import (
    ActionType,
    ImpactReport,
    LinkTypeDef,
    LinkTypeDefCreate,
    ObjectType,
    ObjectTypeBatchCreate,
    ObjectTypeCreate,
    ObjectTypeSummary,
    Ontology,
    OntologyCreate,
    OntologyFullMetadata,
    OntologyUpdate,
    PropertyDef,
    PropertyDefCreate,
)
from ontology.services.ontology_service import OntologyService

router = APIRouter(prefix="/ontologies", tags=["ontologies"])


async def get_ontology_service() -> AsyncIterator[OntologyService]:
    """Yield a request-scoped OntologyService and close its session after."""
    service = container.ontology_service
    try:
        yield service
    finally:
        await service.aclose()


# ══════════════════════════════════════════════════════
# Ontology CRUD
# ══════════════════════════════════════════════════════


@router.post("", response_model=Ontology, status_code=201)
async def create_ontology(
    data: OntologyCreate,
    service: OntologyService = Depends(get_ontology_service),
) -> Ontology:
    return await service.create_ontology(data)


@router.get("", response_model=list[Ontology])
async def list_ontologies(
    include_deleted: bool = Query(default=False),
    include_deprecated: bool = Query(default=False),
    service: OntologyService = Depends(get_ontology_service),
) -> list[Ontology]:
    """List all Ontologies with object type counts.

    Visibility tiers:
      - default: only ACTIVE, non-soft-deleted.
      - ``?include_deprecated=true``: also show DEPRECATED (sidebar default —
        lets users find/restore deprecated ontologies), still hide soft-deleted.
      - ``?include_deleted=true``: show everything (DEPRECATED + soft-deleted) —
        the admin/recycle-bin view (design §5.5).
    """
    rows = await service.list_ontologies_with_counts(
        include_non_active=include_deleted, include_deprecated=include_deprecated
    )

    ontologies: list[Ontology] = []
    for model, count in rows:
        ontologies.append(
            Ontology(
                id=model.id,
                api_name=model.api_name,
                display_name=model.display_name,
                description=model.description or "",
                rid=model.rid,
                object_types_count=count,
                status=cast(Literal["ACTIVE", "DEPRECATED"], model.status),
                deleted_at=model.deleted_at,
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        )
    return ontologies


@router.get("/{api_name}", response_model=Ontology)
async def get_ontology(
    api_name: str,
    include_deleted: bool = Query(default=False),
    service: OntologyService = Depends(get_ontology_service),
) -> Ontology:
    return await service.get_ontology(api_name, include_non_active=include_deleted)


@router.get("/{api_name}/fullMetadata", response_model=OntologyFullMetadata)
async def get_ontology_full_metadata(
    api_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> OntologyFullMetadata:
    """Get the full ontology metadata in a single payload (ADR-020).

    Returns objects + links + actions + interfaces in one call — mirrors
    Palantir Foundry ``/v2/ontologies/{ont}/fullMetadata``. Best-effort: a
    failing entity type is recorded in ``omitted`` (``partial=true``) rather
    than failing the request.
    """
    return await service.assemble_ontology_metadata(api_name)


@router.patch("/{api_name}", response_model=Ontology)
async def update_ontology(
    api_name: str,
    data: OntologyUpdate,
    service: OntologyService = Depends(get_ontology_service),
) -> Ontology:
    return await service.update_ontology(api_name, data)


@router.delete("/{api_name}", status_code=204)
async def delete_ontology(
    api_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> None:
    try:
        await service.delete_ontology(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/{api_name}/restore", response_model=Ontology)
async def restore_ontology(
    api_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> Ontology:
    """Reverse a soft-delete (v5.2, design §七.3).

    Clears ``deleted_at`` on the ontology and all children. Physical resources
    (Doris idx tables, INDEX pipelines) are NOT re-provisioned.
    """
    try:
        return await service.restore_ontology(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{api_name}/impact", response_model=ImpactReport)
async def get_ontology_impact(
    api_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> ImpactReport:
    """Cascade-impact report for the delete confirm dialog (v5.2 §六)."""
    try:
        return await service.get_ontology_impact(api_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# ObjectType CRUD
# ══════════════════════════════════════════════════════


@router.post("/{ontology_name}/object-types", response_model=ObjectType, status_code=201)
async def create_object_type(
    ontology_name: str,
    data: ObjectTypeCreate,
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    # Service raises NotFoundError (404) / ConflictError (409) — caught by
    # the global ontology_error_handler. Routes stay thin (M3: no per-route
    # try/except → HTTPException; error format unified across MCP/AG-UI/REST).
    return await service.define_object_type(ontology_name, data)


@router.get("/{ontology_name}/object-types/summary", response_model=list[ObjectTypeSummary])
async def list_object_types_summary(
    ontology_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> list[ObjectTypeSummary]:
    """Lightweight list — only id/name/status/counts, no property details.

    Suitable for sidebars, tables, card grids, and graph views where
    thousands of objects may be loaded.  Full property details are
    loaded on-demand via GET .../object-types/{type_name}.
    """
    try:
        rows = await service.list_object_type_summaries(ontology_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    summaries: list[ObjectTypeSummary] = []
    for model, properties_count, links_count, actions_count in rows:
        summaries.append(
            ObjectTypeSummary(
                id=model.id,
                ontology_id=model.ontology_id,
                api_name=model.api_name,
                display_name=model.display_name,
                description=model.description or "",
                storage_type=cast(Literal["MANAGED", "VIRTUAL"], model.storage_type),
                visibility=cast(Literal["NORMAL", "PROMINENT", "HIDDEN"], model.visibility),
                status=cast(Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"], model.status),
                properties_count=properties_count,
                links_count=links_count,
                actions_count=actions_count,
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        )
    return summaries


@router.get("/{ontology_name}/object-types", response_model=list[ObjectType])
async def list_object_types(
    ontology_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> list[ObjectType]:
    try:
        result: list[ObjectType] = await service.list_object_types(ontology_name)
        return result
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/{ontology_name}/object-types/{type_name}", response_model=ObjectType)
async def get_object_type(
    ontology_name: str,
    type_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    try:
        return await service.get_object_type(ontology_name, type_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.patch("/{ontology_name}/object-types/{type_name}", response_model=ObjectType)
async def update_object_type(
    ontology_name: str,
    type_name: str,
    updates: dict[str, Any],
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    try:
        return await service.update_object_type_fields(ontology_name, type_name, updates)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/{ontology_name}/object-types/{type_name}", status_code=204)
async def delete_object_type(
    ontology_name: str,
    type_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> None:
    try:
        await service.delete_object_type(ontology_name, type_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# A1: Dataset link management
# ══════════════════════════════════════════════════════


class ColumnMapping(BaseModel):
    property_api_name: str
    column_name: str


class DatasetLinkRequest(BaseModel):
    """A1 — bind an ObjectType's properties to a Dataset's columns.

    Every property must be mapped (the service rejects partial mappings),
    and every column_name must exist in the target dataset.
    """

    dataset_api_name: str
    column_mappings: list[ColumnMapping]


@router.patch(
    "/{ontology_name}/object-types/{type_name}/dataset-link",
    response_model=ObjectType,
)
async def link_dataset(
    ontology_name: str,
    type_name: str,
    data: DatasetLinkRequest,
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    try:
        return await service.link_dataset(
            ontology_name,
            type_name,
            data.dataset_api_name,
            [m.model_dump() for m in data.column_mappings],
        )
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete(
    "/{ontology_name}/object-types/{type_name}/dataset-link",
    response_model=ObjectType,
)
async def unlink_dataset(
    ontology_name: str,
    type_name: str,
    property_api_names: list[str] | None = Query(default=None),
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    """Clear dataset links. Query param ``property_api_names`` (repeatable)
    restricts the clear to those properties; omit to clear all links."""
    try:
        return await service.unlink_dataset(ontology_name, type_name, property_api_names)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# Property CRUD
# ══════════════════════════════════════════════════════


@router.post(
    "/{ontology_name}/object-types/{type_name}/properties",
    response_model=PropertyDef,
    status_code=201,
)
async def add_property(
    ontology_name: str,
    type_name: str,
    data: PropertyDefCreate,
    service: OntologyService = Depends(get_ontology_service),
) -> PropertyDef:
    try:
        # api_name is derived by the service from display_name/backing_column.
        return await service.add_property_to_object_type(ontology_name, type_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get(
    "/{ontology_name}/object-types/{type_name}/properties",
    response_model=list[PropertyDef],
)
async def list_properties(
    ontology_name: str,
    type_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> list[PropertyDef]:
    try:
        return await service.list_properties(ontology_name, type_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete(
    "/{ontology_name}/object-types/{type_name}/properties/{property_name}",
    status_code=204,
)
async def delete_property(
    ontology_name: str,
    type_name: str,
    property_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> None:
    try:
        await service.delete_property(ontology_name, type_name, property_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# LinkType CRUD
# ══════════════════════════════════════════════════════


@router.post("/{ontology_name}/link-types", response_model=LinkTypeDef, status_code=201)
async def create_link_type(
    ontology_name: str,
    data: LinkTypeDefCreate,
    service: OntologyService = Depends(get_ontology_service),
) -> LinkTypeDef:
    try:
        return await service.define_link_type(ontology_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/{ontology_name}/link-types", response_model=list[LinkTypeDef])
async def list_link_types(
    ontology_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> list[LinkTypeDef]:
    try:
        return await service.list_link_types(ontology_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/{ontology_name}/link-types/{link_name}", status_code=204)
async def delete_link_type(
    ontology_name: str,
    link_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> None:
    try:
        await service.delete_link_type(ontology_name, link_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# ActionType list
# ══════════════════════════════════════════════════════


@router.get("/{ontology_name}/action-types", response_model=list[ActionType])
async def list_action_types(
    ontology_name: str,
    service: OntologyService = Depends(get_ontology_service),
) -> list[ActionType]:
    try:
        return await service.list_action_types(ontology_name)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ══════════════════════════════════════════════════════
# DataSource (stub — for frontend data connection flow)
# ══════════════════════════════════════════════════════


class DataSourceDef(BaseModel):
    id: str
    name: str
    source_type: str
    config: dict[str, Any]
    created_at: datetime


class DataSourceCreate(BaseModel):
    name: str
    source_type: str
    config: dict[str, Any] = {}


@router.post("/{ontology_name}/data-sources", response_model=DataSourceDef, status_code=201)
async def create_data_source(
    ontology_name: str,
    data: DataSourceCreate,
) -> DataSourceDef:
    import uuid

    now = utcnow()
    return DataSourceDef(
        id=uuid.uuid4().hex,
        name=data.name,
        source_type=data.source_type,
        config=data.config,
        created_at=now,
    )


@router.get("/{ontology_name}/data-sources", response_model=list[DataSourceDef])
async def list_data_sources() -> list[DataSourceDef]:
    return []


# ══════════════════════════════════════════════════════
# Batch ObjectType creation (atomic transaction)
# ══════════════════════════════════════════════════════


@router.post("/{ontology_name}/object-types/create", response_model=ObjectType, status_code=201)
async def create_object_type_batch(
    ontology_name: str,
    data: ObjectTypeBatchCreate,
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    """Create an ObjectType with properties and links in a single atomic transaction.

    The entire creation (object type + all properties + all links)
    executes in one PostgreSQL transaction. If any part fails,
    everything rolls back.
    """
    try:
        return await service.define_object_type_batch(ontology_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/{ontology_name}/object-types/{type_name}/batch", response_model=ObjectType)
async def update_object_type_batch(
    ontology_name: str,
    type_name: str,
    data: ObjectTypeBatchCreate,
    service: OntologyService = Depends(get_ontology_service),
) -> ObjectType:
    """Update an ObjectType with its properties (delete & recreate)."""
    try:
        return await service.update_object_type_batch(ontology_name, type_name, data)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
