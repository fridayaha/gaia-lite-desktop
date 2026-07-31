"""Default-container bootstrap (ADR-016 Phase 0, design §9.2).

Seeds the single-tenant default Space + Project on application startup.
The default Organization (``org-default``) is inserted by the Alembic
migration (it must exist before any FK reference); the default Space and
Project are bootstrapped here because creating a Space atomically creates
a paired Ontology (1:1 binding) + a default Project — that's Service logic,
not raw DDL, so it belongs in the lifespan, not the migration.

Idempotent: re-runs are no-ops if the defaults already exist. Safe to call
on every startup.

Design §9.5 (Ontology 领养): existing Ontologies created before Phase 0 have
``space_id IS NULL``. Rather than stuffing them all into the default Space
(which would violate the 1:1 constraint — the default Space already owns its
own Ontology), each pre-existing Ontology gets its own Space named after the
Ontology. This preserves the 1:1 invariant. Single-Ontology deployments just
use the default Space.
"""

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ontology.core.models.defaults import new_uuid
from ontology.core.models.ontology import OntologyModel
from ontology.core.models.permission import (
    MarkingCategoryModel,
    MarkingModel,
    OrganizationModel,
    ProjectModel,
    RoleModel,
    SpaceModel,
)
from ontology.core.permission_roles import BUILTIN_ROLES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

# Fixed ids for the default containers so bootstrap is deterministic across
# restarts (avoids creating duplicate defaults if a previous run was interrupted).
DEFAULT_ORG_API_NAME = "org-default"
DEFAULT_SPACE_API_NAME = "default"
DEFAULT_PROJECT_API_NAME = "default"


async def bootstrap_default_containers(session: "AsyncSession") -> None:
    """Ensure the single-tenant default Space + Project + builtin roles exist.

    Creates:
      1. A default Space bound 1:1 to a default Ontology (creates the
         Ontology too if missing — they're created together per the 1:1 rule).
      2. A default Project under the default Space.
      3. Adopts any pre-existing orphan Ontologies (space_id IS NULL) by
         giving each its own Space (design §9.5).
      4. Seeds the builtin roles (Phase 1, RBAC).

    Idempotent — safe to call on every startup. Commits its own transaction.
    """
    # 1. Ensure the default Organization exists (migration seeds it, but be
    #    defensive in case the migration was skipped / DB was reset manually).
    org = await _get_or_create_default_org(session)

    # 2. Ensure the default Space + its paired Ontology exist (1:1).
    await _get_or_create_default_space(session, org.id)

    # 3. Ensure the default Project exists under the default Space.
    await _get_or_create_default_project(session)

    # 4. Adopt orphan Ontologies (created before Phase 0, space_id IS NULL).
    await _adopt_orphan_ontologies(session)

    # 5. Seed builtin roles (Phase 1 RBAC).
    await _seed_builtin_roles(session)

    # 6. Derive system markings for Organizations (Phase 2 MAC).
    await _derive_system_markings(session)

    await session.commit()


async def _get_or_create_default_org(session: "AsyncSession") -> OrganizationModel:
    stmt = select(OrganizationModel).where(OrganizationModel.api_name == DEFAULT_ORG_API_NAME)
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is not None:
        return org
    _log.info("Creating default Organization '%s'", DEFAULT_ORG_API_NAME)
    org = OrganizationModel(
        id=new_uuid(),
        api_name=DEFAULT_ORG_API_NAME,
        display_name="Default Organization",
        description="Default organization for single-tenant deployments",
        org_type="INTERNAL",
        status="ACTIVE",
    )
    session.add(org)
    try:
        await session.flush()
    except IntegrityError:
        # Concurrent bootstrap race — re-read.
        await session.rollback()
        org = (await session.execute(stmt)).scalar_one()
    return org


async def _get_or_create_default_space(
    session: "AsyncSession", org_id: str
) -> SpaceModel:
    stmt = select(SpaceModel).where(SpaceModel.api_name == DEFAULT_SPACE_API_NAME)
    space = (await session.execute(stmt)).scalar_one_or_none()
    if space is not None:
        return space

    _log.info("Creating default Space '%s' (with paired Ontology)", DEFAULT_SPACE_API_NAME)
    # 1:1 rule: create the Ontology and Space together.
    ont = OntologyModel(
        id=new_uuid(),
        api_name="Default",
        display_name="Default Ontology",
        status="ACTIVE",
    )
    session.add(ont)
    await session.flush()  # get ont.id

    space = SpaceModel(
        id=new_uuid(),
        api_name=DEFAULT_SPACE_API_NAME,
        display_name="Default Space",
        description="Default space for single-tenant deployments",
        ontology_id=ont.id,
        status="ACTIVE",
    )
    session.add(space)
    await session.flush()  # get ont.id
    # Backfill the Ontology's space_id (the 1:1 reverse pointer).
    ont.space_id = space.id
    await session.flush()
    return space


async def _get_or_create_default_project(session: "AsyncSession") -> ProjectModel:
    stmt = (
        select(ProjectModel)
        .where(ProjectModel.api_name == DEFAULT_PROJECT_API_NAME)
        .join(SpaceModel, SpaceModel.id == ProjectModel.space_id)
        .where(SpaceModel.api_name == DEFAULT_SPACE_API_NAME)
    )
    project = (await session.execute(stmt)).scalar_one_or_none()
    if project is not None:
        return project

    space_stmt = select(SpaceModel).where(SpaceModel.api_name == DEFAULT_SPACE_API_NAME)
    space = (await session.execute(space_stmt)).scalar_one()
    _log.info("Creating default Project '%s'", DEFAULT_PROJECT_API_NAME)
    project = ProjectModel(
        id=new_uuid(),
        api_name=DEFAULT_PROJECT_API_NAME,
        display_name="Default Project",
        space_id=space.id,
        status="ACTIVE",
    )
    session.add(project)
    await session.flush()
    return project


async def _adopt_orphan_ontologies(session: "AsyncSession") -> None:
    """Give each pre-existing orphan Ontology (space_id IS NULL) its own Space.

    Design §9.5: we can't stuff all orphans into the default Space (that
    would break the 1:1 — the default Space already owns its Ontology).
    Instead each orphan gets a Space named after the Ontology. This preserves
    the 1:1 invariant and keeps existing Ontologies accessible.
    """
    stmt = select(OntologyModel).where(OntologyModel.space_id.is_(None))
    orphans = (await session.execute(stmt)).scalars().all()
    if not orphans:
        return
    _log.info("Adopting %d orphan Ontology(ies) into dedicated Spaces", len(orphans))
    for ont in orphans:
        space = SpaceModel(
            id=new_uuid(),
            api_name=ont.api_name.lower(),
            display_name=f"{ont.display_name} Space",
            ontology_id=ont.id,
            status="ACTIVE",
        )
        session.add(space)
        await session.flush()
        ont.space_id = space.id
    await session.flush()


async def _seed_builtin_roles(session: "AsyncSession") -> None:
    """Seed the builtin roles (Phase 1 RBAC, design §1.3).

    Idempotent: roles that already exist (by name) are skipped. Builtin roles
    are marked is_builtin=True and cannot be deleted by users.
    """
    existing = {
        r.name for r in (await session.execute(select(RoleModel))).scalars().all()
    }
    to_create = [r for r in BUILTIN_ROLES if r["name"] not in existing]
    if not to_create:
        return
    _log.info("Seeding %d builtin roles", len(to_create))
    for role_def in to_create:
        session.add(RoleModel(
            id=new_uuid(),
            name=role_def["name"],
            scope_type=role_def["scope_type"],
            permissions=role_def["permissions"],
            description=role_def["description"],
            is_builtin=True,
        ))
    await session.flush()


async def _derive_system_markings(session: "AsyncSession") -> None:
    """Derive a system marking for each Organization (Phase 2 MAC, design §1.4).

    Each Organization gets a system marking category ("OrgIsolation:<org>")
    + a system marking ("org:<api_name>"). The org's users auto-hold it (via
    the org's default group), and the org's resources auto-carry it. This is
    the底层 implementation of subject isolation (MAC).

    Idempotent: organizations that already have a derived system marking are
    skipped. System markings cannot be manually removed.
    """
    # Find organizations without a derived system marking.
    stmt = select(OrganizationModel).where(
        ~OrganizationModel.id.in_(
            select(MarkingModel.source_organization_id).where(
                MarkingModel.source_organization_id.isnot(None)
            )
        )
    )
    orgs = (await session.execute(stmt)).scalars().all()
    if not orgs:
        return
    _log.info("Deriving system markings for %d organization(s)", len(orgs))
    for org in orgs:
        # System category for this org's subject isolation.
        category = MarkingCategoryModel(
            id=new_uuid(),
            name=f"OrgIsolation:{org.api_name}",
            description=f"System marking category for organization '{org.api_name}' (subject isolation)",
            is_system=True,
        )
        session.add(category)
        await session.flush()
        marking = MarkingModel(
            id=new_uuid(),
            category_id=category.id,
            name=f"org:{org.api_name}",
            display_name=f"Org: {org.display_name}",
            description=(
                f"System marking for organization '{org.api_name}' — "
                "auto-held by org users, auto-applied to org resources"
            ),
            is_system=True,
            source_organization_id=org.id,
        )
        session.add(marking)
    await session.flush()
