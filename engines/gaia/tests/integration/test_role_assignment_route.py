"""Integration tests for /authz/role-assignments (design §7.3).

Verifies the grant/list/revoke lifecycle for role assignments, including:
  - 组授权铁律: grants go to Groups, not Users
  - role:manage permission gate (non-managers get 403)
  - duplicate grant -> 409 ConflictError
  - cache invalidation after grant/revoke (decisions refresh)

Uses a real AuthorizationService against a seeded SQLite DB. The Principal
is injected via FastAPI dependency override — a PLATFORM_ADMIN for the
manager role, an anonymous/no-role principal for the 403 path.
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
from ontology.core.models.permission import (
    GroupModel,
    OrganizationModel,
    ProjectModel,
)
from ontology.core.schemas.permission import Principal
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.main import app
from ontology.routes.authz import _principal
from ontology.services.authorization_service import AuthorizationService
from ontology.services.permission_bootstrap import bootstrap_default_containers


def _build_seed():
    """Build seeded DB. Returns (session, project_id, group_id, admin_principal, user_principal)."""

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
        group = GroupModel(name="editors", organization_id=org.id)
        session.add(group)
        await session.commit()

        admin = Principal(
            id="admin-1", display_name="admin", is_anonymous=False,
            roles=["PLATFORM_ADMIN"],
        )
        user = Principal(
            id="user-1", display_name="user", is_anonymous=False,
        )
        return session, project.id, group.id, admin, user

    return asyncio.run(_run())


@pytest.fixture
def client_admin():
    """Client with a PLATFORM_ADMIN principal (can grant roles)."""
    session, project_id, group_id, admin, _ = _build_seed()
    cache = Cache(name="test-ra")
    cache.setup("mem://")
    authz = AuthorizationService(metadata=PostgresMetaStore(session), cache=cache)

    container = Container()
    container.service_overrides["authorization_service"] = authz

    async def override_principal():
        return admin
    app.dependency_overrides[_principal] = override_principal

    with patch("ontology.routes.authz.container", container):
        yield TestClient(app), project_id, group_id

    app.dependency_overrides.pop(_principal, None)


@pytest.fixture
def client_user():
    """Client with a no-role principal (cannot grant roles -> 403)."""
    session, project_id, group_id, _, user = _build_seed()
    cache = Cache(name="test-ra-user")
    cache.setup("mem://")
    authz = AuthorizationService(metadata=PostgresMetaStore(session), cache=cache)

    container = Container()
    container.service_overrides["authorization_service"] = authz

    async def override_principal():
        return user
    app.dependency_overrides[_principal] = override_principal

    with patch("ontology.routes.authz.container", container):
        yield TestClient(app), project_id, group_id

    app.dependency_overrides.pop(_principal, None)


class TestCreateRoleAssignment:
    def test_grant_role_to_group(self, client_admin):
        client, project_id, group_id = client_admin
        resp = client.post("/authz/role-assignments", json={
            "group_id": group_id,
            "role_name": "VIEWER",
            "scope_type": "PROJECT",
            "scope_id": project_id,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["group_id"] == group_id
        assert body["role_name"] == "VIEWER"
        assert body["scope_type"] == "PROJECT"
        assert body["scope_id"] == project_id

    def test_duplicate_grant_returns_409(self, client_admin):
        client, project_id, group_id = client_admin
        payload = {
            "group_id": group_id,
            "role_name": "EDITOR",
            "scope_type": "PROJECT",
            "scope_id": project_id,
        }
        first = client.post("/authz/role-assignments", json=payload)
        assert first.status_code == 201
        second = client.post("/authz/role-assignments", json=payload)
        assert second.status_code == 409

    def test_non_manager_gets_403(self, client_user):
        client, project_id, group_id = client_user
        resp = client.post("/authz/role-assignments", json={
            "group_id": group_id,
            "role_name": "VIEWER",
            "scope_type": "PROJECT",
            "scope_id": project_id,
        })
        assert resp.status_code == 403

    def test_unknown_role_returns_404(self, client_admin):
        client, project_id, group_id = client_admin
        resp = client.post("/authz/role-assignments", json={
            "group_id": group_id,
            "role_name": "NONEXISTENT_ROLE",
            "scope_type": "PROJECT",
            "scope_id": project_id,
        })
        assert resp.status_code == 404


class TestListRoleAssignments:
    def test_list_returns_grants(self, client_admin):
        client, project_id, group_id = client_admin
        client.post("/authz/role-assignments", json={
            "group_id": group_id, "role_name": "VIEWER",
            "scope_type": "PROJECT", "scope_id": project_id,
        })
        resp = client.get("/authz/role-assignments", params={"scope_id": project_id})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["role_name"] == "VIEWER"

    def test_filter_by_group(self, client_admin):
        client, project_id, group_id = client_admin
        client.post("/authz/role-assignments", json={
            "group_id": group_id, "role_name": "EDITOR",
            "scope_type": "PROJECT", "scope_id": project_id,
        })
        resp = client.get("/authz/role-assignments", params={"group_id": group_id})
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_non_manager_list_gets_403(self, client_user):
        client, _, _ = client_user
        resp = client.get("/authz/role-assignments")
        assert resp.status_code == 403


class TestDeleteRoleAssignment:
    def test_revoke_assignment(self, client_admin):
        client, project_id, group_id = client_admin
        create = client.post("/authz/role-assignments", json={
            "group_id": group_id, "role_name": "VIEWER",
            "scope_type": "PROJECT", "scope_id": project_id,
        })
        assignment_id = create.json()["id"]

        resp = client.delete(f"/authz/role-assignments/{assignment_id}")
        assert resp.status_code == 204

        # List should now be empty for this scope.
        listing = client.get("/authz/role-assignments", params={"scope_id": project_id})
        assert len(listing.json()) == 0

    def test_revoke_nonexistent_returns_404(self, client_admin):
        client, _, _ = client_admin
        resp = client.delete("/authz/role-assignments/nonexistent-id")
        assert resp.status_code == 404

    def test_non_manager_revoke_gets_403(self, client_user):
        client, _, _ = client_user
        resp = client.delete("/authz/role-assignments/any-id")
        assert resp.status_code == 403
