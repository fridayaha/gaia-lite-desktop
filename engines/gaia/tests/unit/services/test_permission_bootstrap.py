"""Tests for the default-container bootstrap (ADR-016 Phase 0, design §9.2).

Uses a real async SQLite session (per the existing conftest pattern) so the
1:1 Space↔Ontology binding and FK constraints are exercised against actual
SQL — mocking the session would hide schema/constraint bugs.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.core.models import Base
from ontology.core.models.ontology import OntologyModel
from ontology.core.models.permission import OrganizationModel, ProjectModel, SpaceModel
from ontology.services.permission_bootstrap import (
    DEFAULT_ORG_API_NAME,
    DEFAULT_PROJECT_API_NAME,
    DEFAULT_SPACE_API_NAME,
    bootstrap_default_containers,
)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Real in-memory SQLite async session with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    session_local = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_local() as session:
        yield session
    await engine.dispose()


class TestBootstrapDefaultContainers:
    @pytest.mark.asyncio
    async def test_creates_defaults_when_absent(self, db_session):
        """First run creates org + space + project + paired ontology."""
        await bootstrap_default_containers(db_session)

        org = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == DEFAULT_ORG_API_NAME)
        )).scalar_one()
        assert org.org_type == "INTERNAL"

        space = (await db_session.execute(
            select(SpaceModel).where(SpaceModel.api_name == DEFAULT_SPACE_API_NAME)
        )).scalar_one()
        assert space.ontology_id is not None

        project = (await db_session.execute(
            select(ProjectModel).where(ProjectModel.api_name == DEFAULT_PROJECT_API_NAME)
        )).scalar_one()
        assert project.space_id == space.id

        # The default Space's Ontology must backreference the Space (1:1).
        ont = (await db_session.execute(
            select(OntologyModel).where(OntologyModel.id == space.ontology_id)
        )).scalar_one()
        assert ont.space_id == space.id

    @pytest.mark.asyncio
    async def test_idempotent(self, db_session):
        """Second run is a no-op (doesn't duplicate)."""
        await bootstrap_default_containers(db_session)
        await bootstrap_default_containers(db_session)  # re-run

        orgs = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == DEFAULT_ORG_API_NAME)
        )).scalars().all()
        assert len(orgs) == 1

        spaces = (await db_session.execute(
            select(SpaceModel).where(SpaceModel.api_name == DEFAULT_SPACE_API_NAME)
        )).scalars().all()
        assert len(spaces) == 1

        projects = (await db_session.execute(
            select(ProjectModel).where(ProjectModel.api_name == DEFAULT_PROJECT_API_NAME)
        )).scalars().all()
        assert len(projects) == 1

    @pytest.mark.asyncio
    async def test_adopts_orphan_ontologies(self, db_session):
        """Pre-existing Ontologies (space_id NULL) get their own Space (§9.5)."""
        # Create an orphan Ontology (as if created before Phase 0).
        orphan = OntologyModel(api_name="LegacyOnt", display_name="Legacy")
        db_session.add(orphan)
        await db_session.commit()

        await bootstrap_default_containers(db_session)

        # Refresh the orphan — it should now have a space_id.
        await db_session.refresh(orphan)
        assert orphan.space_id is not None
        # The adopting Space must be 1:1 with this Ontology.
        space = (await db_session.execute(
            select(SpaceModel).where(SpaceModel.id == orphan.space_id)
        )).scalar_one()
        assert space.ontology_id == orphan.id

    @pytest.mark.asyncio
    async def test_orphan_adoption_preserves_1to1(self, db_session):
        """Multiple orphans each get distinct Spaces (1:1 preserved)."""
        o1 = OntologyModel(api_name="OntA", display_name="A")
        o2 = OntologyModel(api_name="OntB", display_name="B")
        db_session.add_all([o1, o2])
        await db_session.commit()

        await bootstrap_default_containers(db_session)

        await db_session.refresh(o1)
        await db_session.refresh(o2)
        assert o1.space_id is not None
        assert o2.space_id is not None
        assert o1.space_id != o2.space_id  # distinct spaces
