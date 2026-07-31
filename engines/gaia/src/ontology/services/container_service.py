"""ContainerService — manage Organizations, Spaces, Projects (ADR-016 Phase 0/1).

SpaceService.create_space is the "atomic create" entry point (design §2.2):
creating a Space atomically creates a same-named Ontology (1:1 binding) +
a default Project + grants the creator three-tier Owner roles. This is the
"从动作推断意图" principle — the user creates a Space to use it, so the
system auto-provisions everything needed (少弹窗).

Organization and Project management are simpler CRUD (with permission gates).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ontology.core.exceptions import ForbiddenError
from ontology.core.models.defaults import new_uuid
from ontology.core.models.ontology import OntologyModel
from ontology.core.models.permission import ProjectModel, SpaceModel
from ontology.core.schemas.permission import Principal

if TYPE_CHECKING:
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
    from ontology.services.authorization_service import AuthorizationService

_log = logging.getLogger(__name__)


class ContainerService:
    """Manages the three-tier container hierarchy: Organization → Space → Project.

    Permission gates:
      - Creating an Organization requires PLATFORM_ADMIN (platform-level).
      - Creating a Space requires PLATFORM_ADMIN (or space:admin on an
        existing Space — but creating a new Space is a platform action).
      - Creating a Project under a Space requires space:admin on that Space.
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        authorization_service: AuthorizationService,
    ) -> None:
        self._metadata = metadata
        self._authz = authorization_service

    # ── Organization ───────────────────────────────────────────────────

    async def list_organizations(self) -> list[Any]:
        return await self._metadata.list_organizations()

    async def create_organization(
        self, *, api_name: str, display_name: str, description: str = "",
        org_type: str = "INTERNAL", admin: Principal,
    ) -> str:
        """Create an Organization (requires PLATFORM_ADMIN)."""
        gate = await self._authz.check_access(admin, "ROLE", "*", "role:manage")
        if not gate.allowed:
            raise ForbiddenError(f"Cannot create organization: {gate.reason}")
        org = await self._metadata.create_organization(
            api_name=api_name, display_name=display_name, description=description,
            org_type=org_type,
        )
        _log.info("Created organization '%s' by %s", api_name, admin.id)
        return org.id

    # ── Space ──────────────────────────────────────────────────────────

    async def list_spaces(self) -> list[SpaceModel]:
        return await self._metadata.list_spaces()

    async def create_space(
        self, *, api_name: str, display_name: str, description: str = "",
        creator: Principal,
    ) -> str:
        """Create a Space atomically: Space + Ontology (1:1) + default Project.

        Design §2.2: creating a Space auto-creates a same-named Ontology +
        a default Project + grants the creator Owner roles (从动作推断意图).
        This method does steps 1-3 (Space + Ontology + Project); the role
        grant is done by the caller via the role-assignment API (or the
        route that orchestrates both).

        Requires space:admin (PLATFORM_ADMIN has it via wildcard).
        """
        gate = await self._authz.check_access(creator, "SPACE", "*", "space:admin")
        if not gate.allowed:
            raise ForbiddenError(f"Cannot create space: {gate.reason}")

        # 1:1 rule: create the Ontology and Space together.
        ont = OntologyModel(
            id=new_uuid(), api_name=display_name, display_name=display_name,
            status="ACTIVE",
        )
        self._metadata._session.add(ont)  # noqa: SLF001
        await self._metadata._session.flush()  # noqa: SLF001

        space = SpaceModel(
            id=new_uuid(), api_name=api_name, display_name=display_name,
            description=description, ontology_id=ont.id, status="ACTIVE",
        )
        self._metadata._session.add(space)  # noqa: SLF001
        await self._metadata._session.flush()  # noqa: SLF001
        ont.space_id = space.id
        await self._metadata._session.flush()  # noqa: SLF001

        # Default Project under the new Space.
        project = ProjectModel(
            id=new_uuid(), api_name="default", display_name="Default Project",
            space_id=space.id, status="ACTIVE",
        )
        self._metadata._session.add(project)  # noqa: SLF001
        await self._metadata._flush_and_commit()

        _log.info(
            "Created space '%s' (ontology + default project) by %s",
            api_name, creator.id,
        )
        return space.id

    # ── Project ────────────────────────────────────────────────────────

    async def list_projects(self, space_id: str | None = None) -> list[ProjectModel]:
        return await self._metadata.list_projects(space_id)

    async def create_project(
        self, *, api_name: str, display_name: str, space_id: str,
        description: str = "", admin: Principal,
    ) -> str:
        """Create a Project under a Space (requires space:admin on the Space)."""
        gate = await self._authz.check_access(admin, "SPACE", space_id, "space:admin")
        if not gate.allowed:
            raise ForbiddenError(f"Cannot create project: {gate.reason}")
        project = await self._metadata.create_project(
            api_name=api_name, display_name=display_name, space_id=space_id,
            description=description,
        )
        _log.info("Created project '%s' in space %s by %s", api_name, space_id, admin.id)
        return project.id
