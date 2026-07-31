"""Integration tests for the /authz/allowed-actions ship-the-decision endpoint.

Verifies the batch endpoint that powers the frontend's PermissionGate /
useAllowedActions (design §8.2). The endpoint resolves N resources × M
registered actions in one AuthorizationService.check_access_batch call and
returns per-resource allowedActions + disabledReasons.

Uses a real AuthorizationService against a seeded SQLite DB so the
five-layer check actually resolves (mocking would hide the ownership-chain
logic). The DB is built once per module (sync, via asyncio.run) and the
session/principal are reused across tests; the AuthorizationService is
request-scoped but bound to that same session.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from cashews import Cache
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ontology.config.container import Container
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
from ontology.main import app
from ontology.routes.authz import _principal
from ontology.services.authorization_service import AuthorizationService
from ontology.services.permission_bootstrap import bootstrap_default_containers


def _build_seed():
    """Build the seeded DB + principal. Returns (session, principal)."""

    async def _run():
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_local = async_sessionmaker(engine, expire_on_commit=False)
        session = await session_local().__aenter__()
        await bootstrap_default_containers(session)

        project = (await session.execute(
            select(ProjectModel).where(ProjectModel.api_name == "default")
        )).scalar_one()
        org = (await session.execute(
            select(OrganizationModel).where(OrganizationModel.api_name == "org-default")
        )).scalar_one()
        space = (await session.execute(
            select(SpaceModel).where(SpaceModel.api_name == "default")
        )).scalar_one()
        ont = (await session.execute(
            select(OntologyModel).where(OntologyModel.space_id == space.id)
        )).scalar_one()

        for name in ("Invoice", "Order"):
            session.add(ObjectTypeModel(
                ontology_id=ont.id, api_name=name, display_name=name,
                primary_key="id", title_property="id", storage_type="MANAGED",
            project_id=project.id,
            ))

        user = UserModel(email="viewer@example.com", subject="sub-v")
        user.home_organization = org.id
        session.add(user)
        await session.flush()
        group = GroupModel(name="viewers", organization_id=org.id)
        session.add(group)
        await session.flush()
        role = (await session.execute(
            select(RoleModel).where(RoleModel.name == "VIEWER")
        )).scalar_one()
        session.add(RoleAssignmentModel(
            principal_id=group.id, role_id=role.id,
            scope_type="PROJECT", scope_id=project.id,
        ))
        session.add(GroupMembershipModel(group_id=group.id, user_id=user.id))
        await session.commit()

        principal = Principal(
            id=user.id, display_name=user.email, is_anonymous=False, groups=[group.id]
        )
        return session, principal

    return asyncio.run(_run())


@pytest.fixture
def client():
    session, principal = _build_seed()
    cache = Cache(name="test-aa")
    cache.setup("mem://")
    authz = AuthorizationService(metadata=PostgresMetaStore(session), cache=cache)

    container = Container()
    container.service_overrides["authorization_service"] = authz

    async def override_principal():
        return principal

    app.dependency_overrides[_principal] = override_principal

    with patch("ontology.routes.authz.container", container):
        yield TestClient(app)

    app.dependency_overrides.pop(_principal, None)


class TestAllowedActionsEndpoint:
    def test_batch_returns_decisions_per_resource(self, client):
        """Two ObjectTypes → decisions keyed by resource_id, each with actions."""
        resp = client.post("/authz/allowed-actions", json={
            "resource_type": "OBJECT_TYPE",
            "resource_ids": ["Invoice", "Order"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["resource_type"] == "OBJECT_TYPE"
        assert set(body["decisions"].keys()) == {"Invoice", "Order"}
        # Viewer: view allowed, write/edit denied.
        inv = body["decisions"]["Invoice"]
        assert "object_type:view" in inv["allowedActions"]
        assert "object:view" in inv["allowedActions"]
        assert "object_type:edit" in inv["disabledReasons"]
        assert "object:write" in inv["disabledReasons"]

    def test_empty_resource_ids_returns_empty(self, client):
        resp = client.post("/authz/allowed-actions", json={
            "resource_type": "OBJECT_TYPE",
            "resource_ids": [],
        })
        assert resp.status_code == 200
        assert resp.json()["decisions"] == {}

    def test_unknown_resource_type_returns_empty(self, client):
        resp = client.post("/authz/allowed-actions", json={
            "resource_type": "NOPE",
            "resource_ids": ["x"],
        })
        assert resp.status_code == 200
        assert resp.json()["decisions"] == {}
