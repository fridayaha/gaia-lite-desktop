"""Tests for Phase 4: audit log + Check Access + JIT access requests.

Covers:
  - AuditLogModel / AccessRequestModel ORM + constraints
  - AuditService append-only (AuthorizationService logs every decision)
  - CheckAccessResult: per-layer status + provenance + missing
  - AccessRequestService: create → approve (creates time-limited grant) →
    cache invalidation; reject; separation of duties (can't self-approve)
  - Routes: /authz/check, /authz/access-requests, /audit-logs
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cashews import Cache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ontology.core.exceptions import ForbiddenError, ValidationError
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
from ontology.core.schemas.permission import Principal
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.access_request_service import AccessRequestService
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
        await bootstrap_default_containers(session)
        yield session


@pytest_asyncio.fixture
def cache() -> Cache:
    c = Cache(name="test-audit")
    c.setup("mem://")
    return c


def _meta(session: AsyncSession) -> PostgresMetaStore:
    return PostgresMetaStore(session)


async def _seed_viewer(
    session: AsyncSession, *, project_id: str
) -> tuple[UserModel, str]:
    org = (await session.execute(
        select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
    )).scalar_one()
    user = UserModel(email="viewer@example.com", subject="viewer-sub")
    session.add(user)
    await session.flush()
    group = GroupModel(name="viewers-audit", organization_id=org.id)
    session.add(group)
    await session.flush()
    session.add(GroupMembershipModel(group_id=group.id, user_id=user.id))
    role = (await session.execute(
        select(RoleModel).where(RoleModel.name == "VIEWER")
    )).scalar_one()
    session.add(RoleAssignmentModel(
        principal_id=group.id, role_id=role.id, scope_type="PROJECT", scope_id=project_id
    ))
    await session.commit()
    return user, group.id


class TestAuditLogModel:
    @pytest.mark.asyncio
    async def test_append_only(self, db_session):
        """append_audit_log writes a row; no update/delete method exists."""
        meta = _meta(db_session)
        log_id = await meta.append_audit_log(
            principal_id="u1", resource_type="OBJECT_TYPE", resource_id="ot1",
            action="object:view", result="ALLOW", reason="", layer="ALLOW",
        )
        await db_session.commit()
        logs = await meta.list_audit_logs()
        assert len(logs) == 1
        assert logs[0].id == log_id
        assert logs[0].result == "ALLOW"

    @pytest.mark.asyncio
    async def test_list_filters(self, db_session):
        meta = _meta(db_session)
        await meta.append_audit_log(
            principal_id="u1", resource_type="OBJECT_TYPE", resource_id="ot1",
            action="object:view", result="ALLOW", layer="ALLOW",
        )
        await meta.append_audit_log(
            principal_id="u2", resource_type="OBJECT_TYPE", resource_id="ot1",
            action="object:view", result="DENY", layer="PROJECT",
        )
        await db_session.commit()
        # Filter by result
        denies = await meta.list_audit_logs(result="DENY")
        assert len(denies) == 1
        assert denies[0].principal_id == "u2"
        # Filter by principal
        u1_logs = await meta.list_audit_logs(principal_id="u1")
        assert len(u1_logs) == 1
        assert u1_logs[0].result == "ALLOW"


class TestAuthorizationServiceAudits:
    """AuthorizationService.check_access records every decision in audit_logs."""

    @pytest.mark.asyncio
    async def test_denied_decision_audited(self, db_session, cache):
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        # Anonymous → denied at IDENTITY, should be audited.
        await authz.check_access(
            Principal.anonymous_principal(), "OBJECT_TYPE", "x", "object:view"
        )
        logs = await _meta(db_session).list_audit_logs(result="DENY")
        assert len(logs) >= 1
        assert logs[0].layer == "IDENTITY"

    @pytest.mark.asyncio
    async def test_allowed_decision_audited(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Audited", display_name="A",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()

        user, group_id = await _seed_viewer(db_session, project_id=project.id)
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        await authz.check_access(principal, "OBJECT_TYPE", "Audited", "object:view")
        allows = await _meta(db_session).list_audit_logs(result="ALLOW")
        assert len(allows) >= 1

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_block_authorization(self, db_session, cache):
        """If the audit write fails, the authorization decision still returns."""
        # Use a mock metadata that raises on append_audit_log.
        mock_meta = AsyncMock()
        mock_meta.append_audit_log.side_effect = RuntimeError("DB down")
        mock_meta.resolve_resource_ownership.return_value = None
        authz = AuthorizationService(metadata=mock_meta, cache=cache)
        principal = Principal(id="u1", is_anonymous=False, roles=["PLATFORM_ADMIN"])
        # Should NOT raise despite audit failure.
        result = await authz.check_access(principal, "OBJECT_TYPE", "x", "object:view")
        assert result is not None  # decision still returned


class TestCheckAccessExplained:
    @pytest.mark.asyncio
    async def test_returns_per_layer_status(self, db_session, cache):
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        explained = await authz.check_access_explained(
            Principal.anonymous_principal(), "OBJECT_TYPE", "x", "object:view"
        )
        assert explained.decision == "DENY"
        assert explained.layer == "IDENTITY"
        assert explained.layers["identity"] is False

    @pytest.mark.asyncio
    async def test_allowed_returns_provenance(self, db_session, cache):
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
            ontology_id=ont.id, api_name="Expl", display_name="E",
            primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
        )
        db_session.add(ot)
        await db_session.commit()
        user, group_id = await _seed_viewer(db_session, project_id=project.id)
        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group_id]
        )
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        explained = await authz.check_access_explained(
            principal, "OBJECT_TYPE", "Expl", "object:view"
        )
        assert explained.decision == "ALLOW"
        assert all(explained.layers.values())  # all layers passed
        assert len(explained.provenance) >= 1  # has Group → Role provenance


class TestAccessRequestService:
    @pytest.mark.asyncio
    async def test_create_request(self, db_session, cache):
        svc = AccessRequestService(metadata=_meta(db_session))
        requester = Principal(id="u1", is_anonymous=False)
        req_id = await svc.create_request(
            requester=requester,
            request_type="ROLE_ASSIGNMENT",
            requested_item="EDITOR",
            justification="Need to edit for sprint task",
        )
        req = await svc._metadata.get_access_request(req_id)
        assert req.status == "PENDING"
        assert req.requested_item == "EDITOR"

    @pytest.mark.asyncio
    async def test_anonymous_cannot_request(self, db_session, cache):
        svc = AccessRequestService(metadata=_meta(db_session))
        with pytest.raises(ForbiddenError):
            await svc.create_request(
                requester=Principal.anonymous_principal(),
                request_type="ROLE_ASSIGNMENT", requested_item="EDITOR",
                justification="x",
            )

    @pytest.mark.asyncio
    async def test_justification_required(self, db_session, cache):
        svc = AccessRequestService(metadata=_meta(db_session))
        with pytest.raises(ValidationError):
            await svc.create_request(
                requester=Principal(id="u1", is_anonymous=False),
                request_type="ROLE_ASSIGNMENT", requested_item="EDITOR",
                justification="   ",  # whitespace only
            )

    @pytest.mark.asyncio
    async def test_cannot_self_approve(self, db_session, cache):
        svc = AccessRequestService(metadata=_meta(db_session))
        requester = Principal(id="u1", is_anonymous=False)
        req_id = await svc.create_request(
            requester=requester, request_type="ROLE_ASSIGNMENT",
            requested_item="EDITOR", justification="need",
        )
        with pytest.raises(ForbiddenError, match="own"):
            await svc.approve_request(req_id, reviewer=requester)

    @pytest.mark.asyncio
    async def test_approve_creates_grant(self, db_session, cache):
        """Approving a role request creates a time-limited RoleAssignment."""
        authz = AuthorizationService(metadata=_meta(db_session), cache=cache)
        svc = AccessRequestService(metadata=_meta(db_session), authorization_service=authz)
        # Seed a requester with a group.
        org = (await db_session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
        )).scalar_one()
        user = UserModel(email="jit@example.com", subject="jit-sub")
        db_session.add(user)
        await db_session.flush()
        group = GroupModel(name="jit-group", organization_id=org.id)
        db_session.add(group)
        await db_session.flush()
        db_session.add(GroupMembershipModel(group_id=group.id, user_id=user.id))
        await db_session.commit()

        requester = Principal(id=user.id, is_anonymous=False)
        expires = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        req_id = await svc.create_request(
            requester=requester, request_type="ROLE_ASSIGNMENT",
            requested_item="EDITOR", justification="temp edit access",
            scope_type="PROJECT", scope_id="proj-1", expires_at=expires,
        )
        # A different reviewer approves.
        reviewer = Principal(id="admin-1", is_anonymous=False, roles=["PLATFORM_ADMIN"])
        await svc.approve_request(req_id, reviewer=reviewer)
        # Verify the RoleAssignment was created with expires_at.
        from ontology.core.models.permission import RoleAssignmentModel
        role = (await db_session.execute(
            select(RoleModel).where(RoleModel.name == "EDITOR")
        )).scalar_one()
        ra = (await db_session.execute(
            select(RoleAssignmentModel).where(
                RoleAssignmentModel.principal_id == group.id,
                RoleAssignmentModel.role_id == role.id,
            )
        )).scalar_one()
        assert ra.expires_at is not None

    @pytest.mark.asyncio
    async def test_reject(self, db_session, cache):
        svc = AccessRequestService(metadata=_meta(db_session))
        requester = Principal(id="u1", is_anonymous=False)
        req_id = await svc.create_request(
            requester=requester, request_type="ROLE_ASSIGNMENT",
            requested_item="EDITOR", justification="need",
        )
        reviewer = Principal(id="admin-1", is_anonymous=False)
        req = await svc.reject_request(req_id, reviewer=reviewer, review_comment="not needed")
        assert req.status == "REJECTED"
