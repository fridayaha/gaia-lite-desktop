"""Tests for PermissionEnvelope — the ship-the-decision response layer.

The envelope is the single place where backend permission decisions get
attached to API responses (design §8.2 "Ship the decision"). Resource
routes declare which actions apply to their resource type via a registry;
the envelope batch-resolves them via AuthorizationService.check_access_batch
and returns ``allowedActions`` + ``disabledReasons`` for the frontend to
render (PermissionGate / useAllowedActions) — without re-deriving any rule.

These tests use a real seeded ObjectType (with an ownership chain) so the
five-layer check actually resolves, plus a plain pydantic object for the
registry/shape tests.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from cashews import Cache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.core.models import Base
from ontology.core.models.ontology import ObjectTypeModel, OntologyModel
from ontology.core.models.permission import (
    GroupMembershipModel,
    GroupModel,
    OrganizationModel,
    ProjectModel,
    RoleAssignmentModel,
    RoleModel,
    SpaceModel,
    UserModel,
)
from ontology.core.permission_roles import (
    OP_DATASOURCE_DELETE,
    OP_DATASOURCE_VIEW,
    OP_OBJECT_TYPE_EDIT,
    OP_OBJECT_TYPE_VIEW,
    OP_OBJECT_VIEW,
    OP_OBJECT_WRITE,
)
from ontology.core.schemas.permission import Principal
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.authorization_service import AuthorizationService
from ontology.services.permission_bootstrap import bootstrap_default_containers
from ontology.services.permission_envelope import (
    PermissionEnvelope,
    action_registry,
    envelope,
)


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
    c = Cache(name="test-env")
    c.setup("mem://")
    return c


def _meta(session: AsyncSession) -> PostgresMetaStore:
    return PostgresMetaStore(session)


async def _seed_object_type(db_session: AsyncSession, api_name: str) -> str:
    """Seed an ObjectType under the default Space/Ontology/Project. Return api_name."""
    space = (await db_session.execute(
        select(SpaceModel).where(SpaceModel.api_name == "default")
    )).scalar_one()
    ont = (await db_session.execute(
        select(OntologyModel).where(OntologyModel.space_id == space.id)
    )).scalar_one()
    project = (await db_session.execute(
        select(ProjectModel).where(ProjectModel.api_name == "default")
    )).scalar_one()
    ot = ObjectTypeModel(
        ontology_id=ont.id, api_name=api_name, display_name=api_name,
        primary_key="id", title_property="id", storage_type="MANAGED",
        project_id=project.id,
    )
    db_session.add(ot)
    await db_session.commit()
    return api_name


async def _seed_viewer(db_session: AsyncSession) -> Principal:
    """Seed a VIEWER-scoped principal under the default Project."""
    project = (await db_session.execute(
        select(ProjectModel).where(ProjectModel.api_name == "default")
    )).scalar_one()
    org = (await db_session.execute(
        select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
    )).scalar_one()

    user = UserModel(email="viewer@example.com", subject="sub-viewer")
    user.home_organization = org.id
    db_session.add(user)
    await db_session.flush()

    group = GroupModel(name="viewers", organization_id=org.id)
    db_session.add(group)
    await db_session.flush()

    role = (await db_session.execute(
        select(RoleModel).where(RoleModel.name == "VIEWER")
    )).scalar_one()
    db_session.add(RoleAssignmentModel(
        principal_id=group.id, role_id=role.id,
        scope_type="PROJECT", scope_id=project.id,
    ))
    db_session.add(GroupMembershipModel(group_id=group.id, user_id=user.id))
    await db_session.commit()

    return Principal(
        id=user.id, display_name=user.email, is_anonymous=False, groups=[group.id]
    )


class TestActionRegistry:
    """The registry maps resource_type → actions (declarative, one place)."""

    def test_datasource_actions_registered(self):
        actions = action_registry.actions_for("DATASOURCE")
        assert OP_DATASOURCE_VIEW in actions
        assert OP_DATASOURCE_DELETE in actions

    def test_object_type_actions_registered(self):
        actions = action_registry.actions_for("OBJECT_TYPE")
        assert OP_OBJECT_TYPE_VIEW in actions
        assert OP_OBJECT_VIEW in actions
        assert OP_OBJECT_WRITE in actions

    def test_unknown_resource_type_returns_empty(self):
        assert action_registry.actions_for("UNKNOWN") == []

    def test_register_adds_actions(self):
        action_registry.register("CUSTOM", ["custom:read", "custom:write"])
        assert set(action_registry.actions_for("CUSTOM")) == {"custom:read", "custom:write"}


class TestEnvelopeSingle:
    """envelope() wraps a single resource with allowedActions + disabledReasons."""

    @pytest.mark.asyncio
    async def test_detail_includes_allowed_actions(self, db_session, cache):
        principal = await _seed_viewer(db_session)
        api_name = await _seed_object_type(db_session, "Invoice")
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        # A plain data payload (stands in for the ObjectType schema).
        data = {"api_name": api_name, "display_name": "Invoice"}
        env = await envelope(authz, principal, "OBJECT_TYPE", api_name, data)
        # Viewer has object_type:view + object:view → allowed.
        assert OP_OBJECT_TYPE_VIEW in env["allowedActions"]
        assert OP_OBJECT_VIEW in env["allowedActions"]
        # Viewer lacks object_type:edit + object:write → disabled with reason.
        assert OP_OBJECT_TYPE_EDIT in env["disabledReasons"]
        assert OP_OBJECT_WRITE in env["disabledReasons"]
        # Original data preserved.
        assert env["data"]["api_name"] == api_name

    @pytest.mark.asyncio
    async def test_detail_anonymous_denies_all(self, db_session, cache):
        api_name = await _seed_object_type(db_session, "Secret")
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        env = await envelope(
            authz, Principal.anonymous_principal(), "OBJECT_TYPE", api_name, {"api_name": api_name}
        )
        assert env["allowedActions"] == []
        # Every registered action should be in disabledReasons.
        assert len(env["disabledReasons"]) == len(action_registry.actions_for("OBJECT_TYPE"))


class TestEnvelopeList:
    """envelope_list() batch-wraps without N+1 (one check_access_batch call)."""

    @pytest.mark.asyncio
    async def test_list_wraps_each_item(self, db_session, cache):
        principal = await _seed_viewer(db_session)
        names = [await _seed_object_type(db_session, f"OT{i}") for i in range(3)]
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        items = [(n, {"api_name": n}) for n in names]
        envs = await PermissionEnvelope.wrap_list(authz, principal, "OBJECT_TYPE", items)
        assert len(envs) == 3
        for name, env in zip(names, envs, strict=True):
            assert env["data"]["api_name"] == name
            assert OP_OBJECT_TYPE_VIEW in env["allowedActions"]

    @pytest.mark.asyncio
    async def test_list_single_batch_call(self, db_session, cache):
        """wrap_list must call check_access_batch exactly once (no N+1)."""
        principal = await _seed_viewer(db_session)
        names = [await _seed_object_type(db_session, f"X{i}") for i in range(5)]
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        calls = {"n": 0}
        original = authz.check_access_batch

        async def counting_batch(p, reqs):
            calls["n"] += 1
            return await original(p, reqs)

        authz.check_access_batch = counting_batch  # type: ignore[assignment]

        items = [(n, {"api_name": n}) for n in names]
        await PermissionEnvelope.wrap_list(authz, principal, "OBJECT_TYPE", items)
        assert calls["n"] == 1


class TestDataGate:
    """Data Gate: unauthorized resources are excluded (不可见即安全)."""

    @pytest.mark.asyncio
    async def test_filter_visible_excludes_denied(self, db_session, cache):
        """Anonymous sees nothing (all denied at Layer 1)."""
        names = [await _seed_object_type(db_session, f"H{i}") for i in range(3)]
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        anon = Principal.anonymous_principal()
        items = [(n, {"api_name": n}) for n in names]
        visible = await PermissionEnvelope.filter_visible(
            authz, anon, "OBJECT_TYPE", items, OP_OBJECT_TYPE_VIEW,
        )
        assert visible == []

    @pytest.mark.asyncio
    async def test_filter_visible_includes_allowed(self, db_session, cache):
        principal = await _seed_viewer(db_session)
        name = await _seed_object_type(db_session, "Visible")
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        items = [(name, {"api_name": name})]
        visible = await PermissionEnvelope.filter_visible(
            authz, principal, "OBJECT_TYPE", items, OP_OBJECT_TYPE_VIEW,
        )
        assert len(visible) == 1
        assert visible[0][0] == name
