"""Marking routes — MAC marking management with separation of duties (ADR-016 Phase 2).

Two route groups, called by different roles (design §7.4):

  /marking-categories, /markings, /markings/{id}/grants — MARKING_ADMIN
    (define markings + grant to Groups)

  /resources/{type}/{id}/markings — PROJECT_OWNER/EDITOR
    (apply EXISTING markings to resources)

The separation-of-duties role checks are enforced by MarkingService, not the
routes — the routes are thin HTTP adapters. The Principal comes from
request.state.principal (set by AuthMiddleware).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, ForbiddenError, ValidationError
from ontology.core.schemas.permission import (
    Marking,
    MarkingAssignment,
    MarkingAssignmentCreate,
    MarkingCategory,
    MarkingCategoryCreate,
    MarkingCreate,
    MarkingGrantCreate,
    Principal,
)
from ontology.services.marking_service import MarkingService

router = APIRouter(tags=["marking"])


async def get_marking_service() -> AsyncIterator[MarkingService]:
    service = container.marking_service
    try:
        yield service
    finally:
        await service._metadata.close()  # noqa: SLF001 — close the request-scoped session


def _principal(request: Request) -> Principal:
    principal: Principal = request.state.principal
    return principal


# ── MARKING_ADMIN: define + grant ──


@router.post("/marking-categories", response_model=MarkingCategory, status_code=201)
async def create_marking_category(
    body: MarkingCategoryCreate,
    service: MarkingService = Depends(get_marking_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        cat_id = await service.create_marking_category(
            body.name, body.description, admin=principal
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (IntegrityError, ConflictError) as e:
        raise HTTPException(status_code=409, detail=f"Marking category '{body.name}' already exists") from e
    # Return a minimal category object (full read path comes later).
    return MarkingCategory(
        id=cat_id, name=body.name, description=body.description,
        is_system=False, created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


@router.get("/marking-categories", response_model=list[MarkingCategory])
async def list_marking_categories(
    service: MarkingService = Depends(get_marking_service),
) -> Any:
    cats = await service.list_marking_categories()
    return [MarkingCategory.model_validate(c) for c in cats]


@router.post("/markings", response_model=Marking, status_code=201)
async def create_marking(
    body: MarkingCreate,
    service: MarkingService = Depends(get_marking_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        marking_id = await service.create_marking(
            body.category_id, body.name, body.display_name, body.description,
            admin=principal,
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (IntegrityError, ConflictError) as e:
        raise HTTPException(status_code=409, detail=f"Marking '{body.name}' already exists") from e
    from datetime import UTC, datetime

    return Marking(
        id=marking_id, category_id=body.category_id, name=body.name,
        display_name=body.display_name or body.name, description=body.description,
        is_system=False, source_organization_id=None,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )


@router.get("/markings", response_model=list[Marking])
async def list_markings(
    category_id: str | None = None,
    service: MarkingService = Depends(get_marking_service),
) -> Any:
    markings = await service.list_markings(category_id)
    return [Marking.model_validate(m) for m in markings]


@router.post("/markings/{marking_id}/grants", status_code=201)
async def grant_marking(
    marking_id: str,
    body: MarkingGrantCreate,
    service: MarkingService = Depends(get_marking_service),
    principal: Principal = Depends(_principal),
) -> dict[str, str]:
    try:
        await service.grant_marking(
            marking_id, body.group_id, admin=principal, expires_at=body.expires_at
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"status": "granted", "marking_id": marking_id, "group_id": body.group_id}


# ── PROJECT_OWNER/EDITOR: apply markings to resources ──


@router.post(
    "/resources/{resource_type}/{resource_id}/markings",
    response_model=MarkingAssignment,
    status_code=201,
)
async def assign_marking(
    resource_type: str,
    resource_id: str,
    body: MarkingAssignmentCreate,
    service: MarkingService = Depends(get_marking_service),
    principal: Principal = Depends(_principal),
) -> Any:
    try:
        await service.assign_marking(
            resource_type, resource_id, body.marking_id, principal=principal
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (IntegrityError, ConflictError) as e:
        raise HTTPException(status_code=409, detail="Marking already applied to this resource") from e
    from datetime import UTC, datetime

    return MarkingAssignment(
        id=f"{resource_type}:{resource_id}:{body.marking_id}",
        resource_type=resource_type, resource_id=resource_id,
        marking_id=body.marking_id, is_directly_applied=True,
        created_at=datetime.now(UTC),
    )


@router.delete("/resources/{resource_type}/{resource_id}/markings/{marking_id}")
async def revoke_marking(
    resource_type: str,
    resource_id: str,
    marking_id: str,
    service: MarkingService = Depends(get_marking_service),
    principal: Principal = Depends(_principal),
) -> dict[str, str]:
    try:
        await service.revoke_marking_assignment(
            resource_type, resource_id, marking_id, principal=principal
        )
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"status": "revoked", "marking_id": marking_id}
