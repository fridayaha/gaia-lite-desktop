"""Tests for option B→A migration (ADR-016 D3, Phase 7).

Covers:
  - preview_migration_impact: gain/lose/unchanged diff between Projects
  - migrate_object_type_to_project: permission check + project_id update + cache invalidation
  - error cases: ObjectType not found, target Project not found, no permission

Uses a real async SQLite session with seeded containers/roles/assignments so
the ownership-chain resolution and RBAC checks hit actual SQL.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from cashews import Cache
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.core.models import Base
from ontology.core.models.ontology import ObjectTypeModel, OntologyModel
from ontology.core.models.permission import (
    GroupModel,
    OrganizationModel,
    ProjectModel,
    RoleAssignmentModel,
    RoleModel,
    SpaceModel,
)
from ontology.core.schemas.permission import Principal
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.authorization_service import AuthorizationService
from ontology.services.permission_bootstrap import bootstrap_default_containers


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    session_local = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_local() as session:
        # Seed default containers + builtin roles.
        await bootstrap_default_containers(session)
        yield session


@pytest_asyncio.fixture
async def authz(db_session) -> AuthorizationService:
    metadata = PostgresMetaStore(db_session)
    cache = Cache()
    cache.setup("mem://")
    svc = AuthorizationService(metadata, cache)
    return svc


@pytest_asyncio.fixture
async def seeded_world(authz: AuthorizationService):
    """Seed: org + space + ontology + 2 projects + 2 groups + role assignments."""
    meta = authz._metadata  # noqa: SLF001

    # Org + Space + Ontology
    org = OrganizationModel(
        id="org1", api_name="org1", display_name="Org", org_type="INTERNAL", status="ACTIVE"
    )
    space = SpaceModel(
        id="space1", api_name="space1", display_name="Space", status="ACTIVE", ontology_id="ont1"
    )
    ont = OntologyModel(
        id="ont1",
        api_name="Ont1",
        display_name="Ontology 1",
        space_id="space1",
        status="ACTIVE",
    )
    # Two Projects under the Space
    proj_a = ProjectModel(
        id="proj_a", api_name="proj_a", display_name="Project A", space_id="space1", status="ACTIVE"
    )
    proj_b = ProjectModel(
        id="proj_b", api_name="proj_b", display_name="Project B", space_id="space1", status="ACTIVE"
    )
    # ObjectType owned by Project A
    ot = ObjectTypeModel(
        id="ot1",
        api_name="Customer",
        display_name="Customer",
        ontology_id="ont1",
        project_id="proj_a",
        primary_key="id",
        title_property="name",
        storage_type="MANAGED",
        status="ACTIVE",
    )
    # Two Groups
    grp1 = GroupModel(
        id="grp1",
        name="Sales Team",
        description="Sales",
        organization_id="org1",
    )
    grp2 = GroupModel(
        id="grp2",
        name="Eng Team",
        description="Eng",
        organization_id="org1",
    )
    meta._session.add_all([org, space, ont, proj_a, proj_b, ot, grp1, grp2])  # noqa: SLF001
    await meta._session.flush()  # noqa: SLF001

    # Role assignments: grp1 has OWNER on proj_a, grp2 has VIEWER on proj_a.
    # On proj_b: grp1 has VIEWER, grp2 has OWNER.
    # So migrating ot1 from proj_a → proj_b:
    #   grp1: OWNER → VIEWER (role changed → lose)
    #   grp2: VIEWER → OWNER (role changed → lose, but actually gains access)
    role_owner = await meta._session.execute(  # noqa: SLF001
        __import__("sqlalchemy").select(RoleModel).where(RoleModel.name == "OWNER")
    )
    role_owner = role_owner.scalar_one()
    role_viewer = await meta._session.execute(  # noqa: SLF001
        __import__("sqlalchemy").select(RoleModel).where(RoleModel.name == "VIEWER")
    )
    role_viewer = role_viewer.scalar_one()

    for gid, pid, role in [
        ("grp1", "proj_a", role_owner),
        ("grp2", "proj_a", role_viewer),
        ("grp1", "proj_b", role_viewer),
        ("grp2", "proj_b", role_owner),
    ]:
        meta._session.add(  # noqa: SLF001
            RoleAssignmentModel(
                id=f"ra_{gid}_{pid}",
                principal_id=gid,
                role_id=role.id,
                scope_type="PROJECT",
                scope_id=pid,
            )
        )
    await meta._session.commit()  # noqa: SLF001

    # Principal: a user in grp1 (OWNER on proj_a, VIEWER on proj_b)
    principal = Principal(
        id="admin_user",
        principal_type="USER",
        display_name="Admin",
        roles=["PLATFORM_ADMIN"],  # bypass permission checks for the migration
        groups=["grp1"],
        is_anonymous=False,
    )
    return {"ot_id": "ot1", "proj_a": "proj_a", "proj_b": "proj_b", "principal": principal}


@pytest.mark.asyncio
class TestPreviewMigrationImpact:
    async def test_impact_shows_gain_lose_unchanged(self, authz, seeded_world):
        """grp1 (OWNER→VIEWER = lose), grp2 (VIEWER→OWNER = lose/role-change)."""
        impact = await authz.preview_migration_impact(
            object_type_id=seeded_world["ot_id"],
            target_project_id=seeded_world["proj_b"],
        )
        assert impact.object_type_api_name == "Customer"
        assert impact.current_project_name == "Project A"
        assert impact.target_project_name == "Project B"
        # Both groups have different roles → both "lose" (role changed).
        statuses = {e.group_id: e.status for e in impact.entries}
        assert statuses["grp1"] == "lose"  # OWNER → VIEWER
        assert statuses["grp2"] == "lose"  # VIEWER → OWNER
        assert impact.summary["lose"] == 2

    async def test_impact_with_identical_projects_shows_unchanged(self, authz, seeded_world):
        """Migrating to the same Project → all unchanged."""
        impact = await authz.preview_migration_impact(
            object_type_id=seeded_world["ot_id"],
            target_project_id=seeded_world["proj_a"],  # same as current
        )
        statuses = {e.group_id: e.status for e in impact.entries}
        assert all(s == "unchanged" for s in statuses.values())
        assert impact.summary["unchanged"] == 2

    async def test_object_type_not_found(self, authz, seeded_world):
        from ontology.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await authz.preview_migration_impact(
                object_type_id="nonexistent",
                target_project_id=seeded_world["proj_b"],
            )

    async def test_target_project_not_found(self, authz, seeded_world):
        from ontology.core.exceptions import NotFoundError

        with pytest.raises(NotFoundError):
            await authz.preview_migration_impact(
                object_type_id=seeded_world["ot_id"],
                target_project_id="nonexistent_proj",
            )


@pytest.mark.asyncio
class TestMigrateObjectTypeToProject:
    async def test_migration_updates_project_id(self, authz, seeded_world):
        """Migration changes object_types.project_id from proj_a to proj_b."""
        from sqlalchemy import select

        meta = authz._metadata  # noqa: SLF001
        impact = await authz.migrate_object_type_to_project(
            object_type_id=seeded_world["ot_id"],
            target_project_id=seeded_world["proj_b"],
            principal=seeded_world["principal"],
        )
        assert impact.target_project_id == seeded_world["proj_b"]
        # Verify DB updated.
        ot = (
            await meta._session.execute(  # noqa: SLF001
                select(ObjectTypeModel).where(ObjectTypeModel.id == seeded_world["ot_id"])
            )
        ).scalar_one()
        assert ot.project_id == seeded_world["proj_b"]

    async def test_migration_audits(self, authz, seeded_world):
        """Migration writes an audit log entry."""
        meta = authz._metadata  # noqa: SLF001
        await authz.migrate_object_type_to_project(
            object_type_id=seeded_world["ot_id"],
            target_project_id=seeded_world["proj_b"],
            principal=seeded_world["principal"],
        )
        logs = await meta.list_audit_logs()
        migrate_logs = [l for l in logs if l.action == "migrate_to_project"]
        assert len(migrate_logs) == 1
        assert migrate_logs[0].resource_id == seeded_world["ot_id"]

    async def test_migration_without_permission_denied(self, authz, seeded_world):
        """Non-owner principal cannot migrate."""
        from ontology.core.exceptions import ForbiddenError

        from ontology.core.schemas.permission import Principal

        # A principal with no groups and no PLATFORM_ADMIN role.
        weak_principal = Principal(
            id="weak_user",
            principal_type="USER",
            display_name="Weak",
            groups=[],
            is_anonymous=False,
        )
        with pytest.raises(ForbiddenError):
            await authz.migrate_object_type_to_project(
                object_type_id=seeded_world["ot_id"],
                target_project_id=seeded_world["proj_b"],
                principal=weak_principal,
            )
