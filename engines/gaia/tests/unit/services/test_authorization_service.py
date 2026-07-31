"""Tests for AuthorizationService (ADR-016 Phase 1: five-layer check, Layers 1-4).

Uses a real async SQLite session with seeded containers/roles/assignments so
the ownership-chain resolution and RBAC checks hit actual SQL — mocking the
session would hide the JOIN/constraint logic that's the whole point.

Layers tested (Phase 1):
  Layer 1: identity (anonymous → deny)
  Layer 2: Organization (subject isolation) — single-tenant fallback
  Layer 3: Space admission (role scoped to Space)
  Layer 4: Project RBAC (role grants action, option B fallback)
  Layer 5: stub (allow) — Phase 2

Layer 4 also covers the PLATFORM_ADMIN wildcard + the option B fallback
(definition-class resource project_id NULL → Ontology's Space's default Project).
"""

import pytest
import pytest_asyncio
from cashews import Cache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.core.models import Base
from ontology.core.models.ontology import (
    ObjectTypeModel,
    OntologyModel,
    PropertyDefModel,
)
from ontology.core.models.permission import (
    GroupModel,
    OrganizationModel,
    ProjectModel,
    PropertyMaskingPolicyModel,
    RoleAssignmentModel,
    RoleModel,
    SpaceModel,
    UserModel,
)
from ontology.core.permission_roles import (
    OP_DATASET_VIEW,
    OP_OBJECT_VIEW,
    OP_OBJECT_WRITE,
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
def cache() -> Cache:
    c = Cache(name="test-perm")
    c.setup("mem://")
    return c


def _meta(session: AsyncSession) -> PostgresMetaStore:
    return PostgresMetaStore(session)


async def _seed_user_with_role(
    session: AsyncSession,
    *,
    role_name: str,
    scope_type: str,
    scope_id: str,
    org_id: str | None = None,
) -> tuple[UserModel, str]:
    """Seed a User + Group + RoleAssignment (组授权铁律: grant to Group)."""
    user = UserModel(email="tester@example.com", subject="sub-tester")
    if org_id:
        user.home_organization = org_id
    session.add(user)
    await session.flush()

    # Default org for the group.
    org_stmt = select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
    org = (await session.execute(org_stmt)).scalar_one()
    group = GroupModel(name=f"{role_name}-group", organization_id=org.id)
    session.add(group)
    await session.flush()

    role_stmt = select(RoleModel).where(RoleModel.name == role_name)
    role = (await session.execute(role_stmt)).scalar_one()
    session.add(RoleAssignmentModel(
        principal_id=group.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
    ))
    await session.flush()
    # Link user to group.
    from ontology.core.models.permission import GroupMembershipModel
    session.add(GroupMembershipModel(group_id=group.id, user_id=user.id))
    await session.commit()
    return user, group.id


class TestLayer1Identity:
    """Layer 1: anonymous principal is denied (fail-closed)."""

    @pytest.mark.asyncio
    async def test_anonymous_denied(self, db_session, cache):
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(
            Principal.anonymous_principal(), "OBJECT_TYPE", "any", "object:view"
        )
        assert not result.allowed
        assert result.layer == "IDENTITY"

    @pytest.mark.asyncio
    async def test_authenticated_not_denied_by_layer1(self, db_session, cache):
        # An authenticated principal without roles still gets past Layer 1
        # (it fails at a later layer, not IDENTITY).
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        principal = Principal(id="u1", display_name="u1", is_anonymous=False)
        result = await authz.check_access(
            principal, "OBJECT_TYPE", "nonexistent", "object:view"
        )
        assert result.layer != "IDENTITY"


class TestLayer4ProjectRBAC:
    """Layer 4: a Viewer can read but not write; an Editor can write."""

    @pytest.mark.asyncio
    async def test_viewer_can_view(self, db_session, cache):
        # Seed an Ontology + ObjectType under the default Space/Project.
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
            ontology_id=ont.id,
            api_name="Invoice",
            display_name="Invoice",
            primary_key="id",
            title_property="id",
            storage_type="MANAGED",
            project_id=project.id,
        )
        session_ot = db_session
        session_ot.add(ot)
        await session_ot.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(
            principal, "OBJECT_TYPE", "Invoice", OP_OBJECT_VIEW
        )
        assert result.allowed, f"Viewer should view: {result.reason}"

    @pytest.mark.asyncio
    async def test_viewer_cannot_write(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Order", display_name="Order",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(
            principal, "OBJECT_TYPE", "Order", OP_OBJECT_WRITE
        )
        assert not result.allowed
        assert result.layer == "PROJECT"

    @pytest.mark.asyncio
    async def test_editor_can_write(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Ticket", display_name="Ticket",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="EDITOR", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(
            principal, "OBJECT_TYPE", "Ticket", OP_OBJECT_WRITE
        )
        assert result.allowed, f"Editor should write: {result.reason}"


class TestPlatformAdminWildcard:
    """PLATFORM_ADMIN bypasses Layers 2-4 (but not Layer 5 — Phase 2)."""

    @pytest.mark.asyncio
    async def test_platform_admin_accesses_any_resource(self, db_session, cache):
        # Seed a resource.
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
            ontology_id=ont.id, api_name="Admin", display_name="Admin",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        principal = Principal(
            id="admin-1", display_name="admin", is_anonymous=False, roles=["PLATFORM_ADMIN"]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        result = await authz.check_access(
            principal, "OBJECT_TYPE", "Admin", OP_OBJECT_VIEW
        )
        assert result.allowed


class TestOptionBFallback:
    """Option B: ObjectType with project_id NULL falls back to Space's default Project."""

    @pytest.mark.asyncio
    async def test_definition_resource_null_project_falls_back(self, db_session, cache):
        # ObjectType with project_id NULL (option B default) — Layer 4 should
        # resolve to the Ontology's Space's default Project.
        space = (await db_session.execute(
            select(SpaceModel).where(SpaceModel.api_name == "default")
        )).scalar_one()
        project = (await db_session.execute(
            select(ProjectModel).where(ProjectModel.api_name == "default")
        )).scalar_one()
        ont = (await db_session.execute(
            select(OntologyModel).where(OntologyModel.space_id == space.id)
        )).scalar_one()
        # project_id stays NULL (option B).
        ot = ObjectTypeModel(
            ontology_id=ont.id, api_name="Fallback", display_name="Fallback",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        chain = await _meta(db_session).resolve_resource_ownership("OBJECT_TYPE", "Fallback")
        assert chain is not None
        assert chain.project_id == project.id  # fallback resolved


class TestCheckActionPermission:
    """ADR-011 contract: returns the forbidden set."""

    @pytest.mark.asyncio
    async def test_allowed_returns_empty_forbidden(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Permit", display_name="Permit",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="EDITOR", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        forbidden = await authz.check_action_permission(
            principal, "Permit", ["obj-1", "obj-2"], OP_OBJECT_WRITE
        )
        assert forbidden == set()  # Editor can write all

    @pytest.mark.asyncio
    async def test_denied_returns_all_forbidden(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Deny", display_name="Deny",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        forbidden = await authz.check_action_permission(
            principal, "Deny", ["obj-1", "obj-2", "obj-3"], OP_OBJECT_WRITE
        )
        assert forbidden == {"obj-1", "obj-2", "obj-3"}  # Viewer can't write any


class TestCacheInvalidation:
    @pytest.mark.asyncio
    async def test_invalidate_principal_clears_cache(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Cached", display_name="Cached",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        # First call populates cache.
        r1 = await authz.check_access(principal, "OBJECT_TYPE", "Cached", OP_OBJECT_VIEW)
        assert r1.allowed
        # Invalidate.
        await authz.invalidate_principal(principal.id)
        # Cache should be empty for this principal.
        cached = await cache.get(f"authz:result:{principal.id}:OBJECT_TYPE:Cached:{OP_OBJECT_VIEW}")
        assert cached is None


class TestCheckAccessBatch:
    """Batch decision API — the engine behind ship-the-decision (envelope).

    A single batch call resolves N (resource_type, resource_id, action)
    tuples at once, reusing the per-entry cache and the five-layer logic.
    This is what PermissionEnvelope calls to populate ``allowedActions``
    on list/detail responses without N+1 round-trips.
    """

    @pytest.mark.asyncio
    async def test_batch_returns_one_result_per_request(self, db_session, cache):
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        principal = Principal.anonymous_principal()
        requests = [
            ("OBJECT_TYPE", "a", OP_OBJECT_VIEW),
            ("OBJECT_TYPE", "b", OP_OBJECT_WRITE),
            ("DATASET", "c", OP_DATASET_VIEW),
        ]
        results = await authz.check_access_batch(principal, requests)
        assert set(results.keys()) == set(requests)
        # Anonymous → all denied at Layer 1.
        for r in results.values():
            assert not r.allowed
            assert r.layer == "IDENTITY"

    @pytest.mark.asyncio
    async def test_batch_empty_input_returns_empty(self, db_session, cache):
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        results = await authz.check_access_batch(
            Principal.anonymous_principal(), []
        )
        assert results == {}

    @pytest.mark.asyncio
    async def test_batch_consistent_with_single(self, db_session, cache):
        """Batch results must equal single check_access results."""
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
            ontology_id=ont.id, api_name="Batch", display_name="Batch",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        single = await authz.check_access(principal, "OBJECT_TYPE", "Batch", OP_OBJECT_VIEW)
        batched = await authz.check_access_batch(
            principal, [("OBJECT_TYPE", "Batch", OP_OBJECT_VIEW)]
        )
        key = ("OBJECT_TYPE", "Batch", OP_OBJECT_VIEW)
        assert batched[key].allowed == single.allowed
        assert batched[key].layer == single.layer

    @pytest.mark.asyncio
    async def test_batch_mixed_allow_deny(self, db_session, cache):
        """Viewer can view but not write — batch returns both correctly."""
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
            ontology_id=ont.id, api_name="Mixed", display_name="Mixed",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        results = await authz.check_access_batch(principal, [
            ("OBJECT_TYPE", "Mixed", OP_OBJECT_VIEW),
            ("OBJECT_TYPE", "Mixed", OP_OBJECT_WRITE),
        ])
        assert results[("OBJECT_TYPE", "Mixed", OP_OBJECT_VIEW)].allowed
        assert not results[("OBJECT_TYPE", "Mixed", OP_OBJECT_WRITE)].allowed

    @pytest.mark.asyncio
    async def test_batch_reuses_cache(self, db_session, cache):
        """A prior single check should populate cache; batch reads from it."""
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
            ontology_id=ont.id, api_name="Reuse", display_name="Reuse",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=project.id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)

        # Prime the cache with a single call.
        await authz.check_access(principal, "OBJECT_TYPE", "Reuse", OP_OBJECT_VIEW)
        # Batch should hit cache (no new audit entries from this call).
        results = await authz.check_access_batch(
            principal, [("OBJECT_TYPE", "Reuse", OP_OBJECT_VIEW)]
        )
        assert results[("OBJECT_TYPE", "Reuse", OP_OBJECT_VIEW)].allowed

    @pytest.mark.asyncio
    async def test_batch_dedup_identical_requests(self, db_session, cache):
        """Duplicate (resource, action) tuples resolve once, return for all."""
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        principal = Principal.anonymous_principal()
        req = ("OBJECT_TYPE", "dup", OP_OBJECT_VIEW)
        results = await authz.check_access_batch(principal, [req, req, req])
        # Dict keys dedup — one entry.
        assert len(results) == 1
        assert req in results


class TestEvaluateMaskingPolicies:
    """Column-level PropertyMaskingPolicy evaluation (Phase 3, ABAC).

    Regression coverage for the ``policies.items()`` AttributeError bug:
    ``get_property_masking_policies`` returns ``list[tuple[str, str]]`` (not a
    dict), so iterating must unpack the tuple directly, not call ``.items()``.
    """

    async def _seed_ot_with_property(
        self, session: AsyncSession, ot_api_name: str, prop_api_name: str
    ) -> ObjectTypeModel:
        """Seed an Ontology + ObjectType + one PropertyDef under default Space."""
        space = (await session.execute(
            select(SpaceModel).where(SpaceModel.api_name == "default")
        )).scalar_one()
        project = (await session.execute(
            select(ProjectModel).where(ProjectModel.api_name == "default")
        )).scalar_one()
        ont = (await session.execute(
            select(OntologyModel).where(OntologyModel.space_id == space.id)
        )).scalar_one()
        ot = ObjectTypeModel(
            ontology_id=ont.id,
            api_name=ot_api_name,
            display_name=ot_api_name,
            primary_key="id",
            title_property="id",
            storage_type="MANAGED",
            project_id=project.id,
        )
        session.add(ot)
        await session.flush()
        prop = PropertyDefModel(
            object_type_id=ot.id,
            api_name=prop_api_name,
            display_name=prop_api_name,
            data_type="STRING",
            project_id=project.id,
        )
        session.add(prop)
        await session.commit()
        return ot

    @pytest.mark.asyncio
    async def test_no_masking_policies_returns_empty(self, db_session, cache):
        """No PropertyMaskingPolicy → masked_properties is empty (no crash).

        This is the path that triggered the ``.items()`` AttributeError: when
        ``get_property_masking_policies`` returns an empty list, calling
        ``.items()`` raised AttributeError and 500'd every textsql query.
        """
        ot = await self._seed_ot_with_property(db_session, "NoPolicy", "amount")
        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=ot.project_id
        )
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        scope = await authz.evaluate_query_scope(principal, "Default", "NoPolicy")
        assert not scope.forbidden
        assert scope.masked_properties == []

    @pytest.mark.asyncio
    async def test_property_masked_when_principal_lacks_marking(self, db_session, cache):
        """Principal without the PII marking → property is masked (null)."""
        ot = await self._seed_ot_with_property(db_session, "Sensitive", "ssn")
        prop = (await db_session.execute(
            select(PropertyDefModel).where(PropertyDefModel.object_type_id == ot.id)
        )).scalar_one()
        # Masking policy: visible only when principal holds the "PII" marking.
        db_session.add(PropertyMaskingPolicyModel(
            property_id=prop.id,
            expression='principal.markings.contains("PII")',
        ))
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=ot.project_id
        )
        # Principal has NO markings → condition is false → masked.
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        scope = await authz.evaluate_query_scope(principal, "Default", "Sensitive")
        assert not scope.forbidden
        assert "ssn" in scope.masked_properties

    @pytest.mark.asyncio
    async def test_property_visible_when_principal_has_marking(self, db_session, cache):
        """Principal WITH the PII marking → property is visible (not masked)."""
        ot = await self._seed_ot_with_property(db_session, "Sensitive2", "ssn")
        prop = (await db_session.execute(
            select(PropertyDefModel).where(PropertyDefModel.object_type_id == ot.id)
        )).scalar_one()
        db_session.add(PropertyMaskingPolicyModel(
            property_id=prop.id,
            expression='principal.markings.contains("PII")',
        ))
        await db_session.commit()

        user, group_id = await _seed_user_with_role(
            db_session, role_name="VIEWER", scope_type="PROJECT", scope_id=ot.project_id
        )
        # Principal holds the PII marking → condition is true → visible.
        principal = Principal(
            id=user.id,
            display_name=user.email,
            is_anonymous=False,
            groups=[group_id],
            markings=["PII"],
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        scope = await authz.evaluate_query_scope(principal, "Default", "Sensitive2")
        assert not scope.forbidden
        assert "ssn" not in scope.masked_properties
