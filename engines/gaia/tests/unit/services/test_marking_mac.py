"""Tests for Phase 2 MAC: Marking models + 合取校验 + separation of duties.

Covers:
  - Marking ORM tables (categories, markings, grants, assignments) + constraints
  - Organization↔Marking linkage (bootstrap derives system marking per org)
  - AuthorizationService Layer 5: 合 conjunctive check (resource markings ⊆ principal.markings)
  - MarkingService separation of duties:
      MARKING_ADMIN: create/grant (denied for PROJECT_OWNER)
      PROJECT_OWNER: assign (denied for MARKING_ADMIN without project access)
  - 治理红线 (≤ 3 categories, ≤ 20 markings)
"""


import pytest
import pytest_asyncio
from cashews import Cache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.core.exceptions import ForbiddenError, ValidationError
from ontology.core.models import Base
from ontology.core.models.ontology import ObjectTypeModel, OntologyModel
from ontology.core.models.permission import (
    GroupModel,
    MarkingAssignmentModel,
    MarkingCategoryModel,
    MarkingGrantModel,
    MarkingModel,
    OrganizationModel,
    ProjectModel,
    RoleAssignmentModel,
    RoleModel,
    SpaceModel,
)
from ontology.core.schemas.permission import Principal
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.authorization_service import AuthorizationService
from ontology.services.marking_service import MarkingService
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
        await bootstrap_default_containers(session)
        yield session


@pytest_asyncio.fixture
def cache() -> Cache:
    c = Cache(name="test-mac")
    c.setup("mem://")
    return c


def _meta(session: AsyncSession) -> PostgresMetaStore:
    return PostgresMetaStore(session)


class TestMarkingTables:
    """ORM tables + constraints."""

    @pytest.mark.asyncio
    async def test_tables_exist(self, db_engine):
        from sqlalchemy import inspect

        def _get_names(conn):
            return set(inspect(conn).get_table_names())

        async with db_engine.connect() as conn:
            names = await conn.run_sync(_get_names)
        for t in ("marking_categories", "markings", "marking_grants", "marking_assignments"):
            assert t in names

    @pytest.mark.asyncio
    async def test_marking_name_unique_per_category(self, db_session):
        """A marking name is unique within its category."""
        from sqlalchemy.exc import IntegrityError

        cat = MarkingCategoryModel(name="Sensitivity")
        db_session.add(cat)
        await db_session.flush()
        db_session.add(MarkingModel(category_id=cat.id, name="PII"))
        await db_session.commit()
        db_session.add(MarkingModel(category_id=cat.id, name="PII"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    @pytest.mark.asyncio
    async def test_marking_assignment_unique_per_resource(self, db_session):
        """A marking is applied at most once per (resource, marking)."""
        from sqlalchemy.exc import IntegrityError

        cat = MarkingCategoryModel(name="Sens2")
        db_session.add(cat)
        await db_session.flush()
        m = MarkingModel(category_id=cat.id, name="PHI")
        db_session.add(m)
        await db_session.flush()
        db_session.add(MarkingAssignmentModel(
            resource_type="OBJECT_TYPE", resource_id="ot1", marking_id=m.id
        ))
        await db_session.commit()
        db_session.add(MarkingAssignmentModel(
            resource_type="OBJECT_TYPE", resource_id="ot1", marking_id=m.id
        ))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()


class TestSystemMarkingBootstrap:
    """Organization↔Marking linkage: bootstrap derives a system marking per org."""

    @pytest.mark.asyncio
    async def test_default_org_has_system_marking(self, db_session):
        # The default org should have a derived system marking.
        org = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
        )).scalar_one()
        markings = (await db_session.execute(
            select(MarkingModel).where(MarkingModel.source_organization_id == org.id)
        )).scalars().all()
        assert len(markings) == 1
        assert markings[0].is_system is True
        assert markings[0].name == "org:org-default"

    @pytest.mark.asyncio
    async def test_idempotent_bootstrap(self, db_session):
        """Re-running bootstrap doesn't duplicate system markings."""
        org = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
        )).scalar_one()
        count_before = len((await db_session.execute(
            select(MarkingModel).where(MarkingModel.source_organization_id == org.id)
        )).scalars().all())
        await bootstrap_default_containers(db_session)
        count_after = len((await db_session.execute(
            select(MarkingModel).where(MarkingModel.source_organization_id == org.id)
        )).scalars().all())
        assert count_before == count_after == 1


class TestLayer5ConjunctiveCheck:
    """AuthorizationService Layer 5: resource markings ⊆ principal.markings (AND)."""

    @pytest.mark.asyncio
    async def test_no_markings_passes(self, db_session, cache):
        """A resource with no markings passes Layer 5 (no restriction)."""
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        principal = Principal(id="u1", is_anonymous=False, roles=["PLATFORM_ADMIN"])
        result = await authz.check_access(principal, "OBJECT_TYPE", "unmarked", "object:view")
        # PLATFORM_ADMIN bypasses 2-4; no markings → Layer 5 passes.
        assert result.allowed

    @pytest.mark.asyncio
    async def test_missing_marking_denies(self, db_session, cache):
        """Principal missing a required marking is denied at Layer 5."""
        # Seed a resource + apply a marking.
        space = (await db_session.execute(
            select(SpaceModel).where(SpaceModel.api_name == "default")
        )).scalar_one()
        project = (await db_session.execute(
            select(ProjectModel).where(ProjectModel.api_name == "default")
        )).scalar_one()
        ont = (await db_session.execute(
            select(OntologyModel).where(OntologyModel.space_id == space.id)
        )).scalar_one()
        ot = ObjectTypeModel(
            ontology_id=ont.id, api_name="Secret", display_name="Secret",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.flush()

        cat = MarkingCategoryModel(name="Class")
        db_session.add(cat)
        await db_session.flush()
        marking = MarkingModel(category_id=cat.id, name="Confidential")
        db_session.add(marking)
        await db_session.flush()
        db_session.add(MarkingAssignmentModel(
            resource_type="OBJECT_TYPE", resource_id="Secret", marking_id=marking.id
        ))
        # Grant the principal a Viewer role so Layer 4 passes (to reach Layer 5).
        org = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
        )).scalar_one()
        group = GroupModel(name="viewers-mac", organization_id=org.id)
        db_session.add(group)
        await db_session.flush()
        role = (await db_session.execute(
            select(RoleModel).where(RoleModel.name == "VIEWER")
        )).scalar_one()
        db_session.add(RoleAssignmentModel(
            principal_id=group.id, role_id=role.id, scope_type="PROJECT", scope_id=project.id
        ))
        await db_session.commit()

        # Principal WITHOUT the Confidential marking → denied at Layer 5.
        principal = Principal(
            id="u2", display_name="u2", is_anonymous=False, groups=[group.id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(principal, "OBJECT_TYPE", "Secret", "object:view")
        assert not result.allowed
        assert result.layer == "MARKING"
        assert "Confidential" in str(result.missing) or result.missing

    @pytest.mark.asyncio
    async def test_held_marking_passes(self, db_session, cache):
        """Principal holding all required markings passes Layer 5."""
        space = (await db_session.execute(
            select(SpaceModel).where(SpaceModel.api_name == "default")
        )).scalar_one()
        project = (await db_session.execute(
            select(ProjectModel).where(ProjectModel.api_name == "default")
        )).scalar_one()
        ont = (await db_session.execute(
            select(OntologyModel).where(OntologyModel.space_id == space.id)
        )).scalar_one()
        ot = ObjectTypeModel(
            ontology_id=ont.id, api_name="Open", display_name="Open",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.flush()

        cat = MarkingCategoryModel(name="Class2")
        db_session.add(cat)
        await db_session.flush()
        marking = MarkingModel(category_id=cat.id, name="Public")
        db_session.add(marking)
        await db_session.flush()
        db_session.add(MarkingAssignmentModel(
            resource_type="OBJECT_TYPE", resource_id="Open", marking_id=marking.id
        ))
        org = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
        )).scalar_one()
        group = GroupModel(name="viewers-open", organization_id=org.id)
        db_session.add(group)
        await db_session.flush()
        db_session.add(MarkingGrantModel(group_id=group.id, marking_id=marking.id))
        role = (await db_session.execute(
            select(RoleModel).where(RoleModel.name == "VIEWER")
        )).scalar_one()
        db_session.add(RoleAssignmentModel(
            principal_id=group.id, role_id=role.id, scope_type="PROJECT", scope_id=project.id
        ))
        await db_session.commit()

        # Principal WITH the Public marking (via group grant) → passes.
        principal = Principal(
            id="u3", display_name="u3", is_anonymous=False, groups=[group.id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(principal, "OBJECT_TYPE", "Open", "object:view")
        assert result.allowed, f"Should pass: {result.reason}"


class TestSeparationOfDuties:
    """MarkingService: MARKING_ADMIN vs PROJECT_OWNER split."""

    @pytest.mark.asyncio
    async def test_anonymous_cannot_manage_markings(self, db_session, cache):
        svc = MarkingService(metadata=_meta(db_session))
        with pytest.raises(ForbiddenError):
            await svc.create_marking_category("C", "", admin=Principal.anonymous_principal())

    @pytest.mark.asyncio
    async def test_project_owner_cannot_create_marking(self, db_session, cache):
        """PROJECT_OWNER lacks MARKING_ADMIN → cannot define markings."""
        svc = MarkingService(metadata=_meta(db_session))
        principal = Principal(id="u", is_anonymous=False, roles=["OWNER"])
        with pytest.raises(ForbiddenError, match="MARKING_ADMIN"):
            await svc.create_marking_category("C", "", admin=principal)

    @pytest.mark.asyncio
    async def test_marking_admin_can_create_marking(self, db_session, cache):
        svc = MarkingService(metadata=_meta(db_session))
        principal = Principal(id="u", is_anonymous=False, roles=["MARKING_ADMIN"])
        cat_id = await svc.create_marking_category("Sensitivity", "Data sensitivity", admin=principal)
        assert cat_id

    @pytest.mark.asyncio
    async def test_marking_admin_without_project_cannot_assign(self, db_session, cache):
        """MARKING_ADMIN without project access cannot apply markings to resources."""
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        svc = MarkingService(metadata=_meta(db_session), authorization_service=authz)
        # MARKING_ADMIN has no PROJECT role → assign_marking should deny.
        principal = Principal(id="u", is_anonymous=False, roles=["MARKING_ADMIN"])
        with pytest.raises(ForbiddenError):
            await svc.assign_marking("OBJECT_TYPE", "ot1", "m1", principal=principal)


class TestGovernanceRedLines:
    """治理红线: ≤ 3 categories, ≤ 20 markings."""

    @pytest.mark.asyncio
    async def test_category_limit(self, db_session, cache):
        svc = MarkingService(metadata=_meta(db_session))
        admin = Principal(id="u", is_anonymous=False, roles=["MARKING_ADMIN"])
        # Bootstrap already created 1 system category → can add 2 more (total 3).
        await svc.create_marking_category("C1", "", admin=admin)
        await svc.create_marking_category("C2", "", admin=admin)
        # 4th should fail (bootstrap's system category + C1 + C2 = 3, +1 = 4 > 3).
        with pytest.raises(ValidationError, match="category limit"):
            await svc.create_marking_category("C3", "", admin=admin)
