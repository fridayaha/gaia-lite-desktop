import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.auth_context import AuthContext
from app.models.hub_item import HubItem
from app.models.hub_item_version import HubItemVersion
from app.policies.capability_access import CapabilityAccessPolicy


class FakeDenyPolicy(CapabilityAccessPolicy):
    version = "fake-deny"

    def can_discover(self, item, version, context):
        return False

    def can_resolve(self, item, version, context):
        return False


class TargetedDenyPolicy(CapabilityAccessPolicy):
    version = "targeted-deny"

    def __init__(self, denied_ids: set[str] | None = None):
        self.denied_ids = denied_ids or set()

    def can_discover(self, item, version, context):
        return str(item.id) not in self.denied_ids

    def can_resolve(self, item, version, context):
        return str(item.id) not in self.denied_ids


def _create_item(client, name, item_type="agent"):
    resp = client.post("/api/hub/items", json={"name": name, "type": item_type})
    assert resp.status_code == 201
    return resp.json()["id"]


def _publish(client, item_id, version_id):
    for path, body in [
        (f"/api/hub/versions/{version_id}/submit-review", {"operator": "dev"}),
        (f"/api/hub/versions/{version_id}/approve", {"operator": "a", "comment": "ok"}),
        (f"/api/hub/versions/{version_id}/publish", {"operator": "a"}),
    ]:
        r = client.post(path, json=body)
        assert r.status_code == 200


def _setup_published(client, name, item_type="agent"):
    item_id = _create_item(client, name, item_type)
    r = client.post(
        f"/api/hub/items/{item_id}/versions",
        json={"hub_item_id": item_id, "version": "1.0.0"},
    )
    assert r.status_code == 201
    vid = r.json()["id"]
    _publish(client, item_id, vid)
    return item_id, vid


class TestAllowAllDefault:
    def test_discover_with_context_returns_same(self, client):
        _setup_published(client, "AA1")
        r1 = client.get("/api/runtime/capabilities/discover")
        r2 = client.get(
            "/api/runtime/capabilities/discover",
            params={"agent_id": "a1", "workspace_id": "w1"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["total"] == r2.json()["total"]

    def test_discover_without_context_no_error(self, client):
        _setup_published(client, "AA2")
        r = client.get("/api/runtime/capabilities/discover")
        assert r.status_code == 200

    def test_resolve_with_context_returns_same(self, client):
        item_id, _ = _setup_published(client, "AA3")
        r1 = client.get(f"/api/runtime/capabilities/{item_id}/resolve")
        r2 = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve",
            params={"agent_id": "a2"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 200

    def test_resolve_without_context_no_error(self, client):
        item_id, _ = _setup_published(client, "AA4")
        r = client.get(f"/api/runtime/capabilities/{item_id}/resolve")
        assert r.status_code == 200


class TestApiContextParams:
    def test_accepts_all_context_params(self, client):
        _setup_published(client, "CP1")
        r = client.get(
            "/api/runtime/capabilities/discover",
            params={
                "agent_id": "ag-1",
                "workspace_id": "ws-1",
                "organization_id": "org-1",
                "actor_id": "user-1",
                "actor_type": "user",
                "scopes": "read,write,admin",
            },
        )
        assert r.status_code == 200

    def test_scopes_parsed_correctly(self):
        from app.core.auth_dependencies import _parse_list

        assert _parse_list("read,write") == ["read", "write"]
        assert _parse_list("  read ,  write  ") == ["read", "write"]
        assert _parse_list("") == []
        assert _parse_list(None) == []

    def test_filters_no_longer_carry_auth_fields(self):
        from app.schemas.runtime import RuntimeDiscoverFilters

        f = RuntimeDiscoverFilters(
            type="agent",
            keyword="test",
            risk_level_max="high",
            limit=10,
            offset=0,
        )
        assert not hasattr(f, "agent_id")
        assert not hasattr(f, "workspace_id")


class TestDenyPolicy:
    def test_discover_deny_policy_filters_all(self, client, db_session):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.schemas.runtime import RuntimeDiscoverFilters

        _setup_published(client, "DP1")
        _setup_published(client, "DP2")

        svc = RuntimeDiscoverService(db_session, policy=FakeDenyPolicy())
        _, total = svc.discover(RuntimeDiscoverFilters(), AuthContext())
        assert total == 0

    def test_discover_deny_policy_total_accurate(self, client, db_session):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.schemas.runtime import RuntimeDiscoverFilters

        _setup_published(client, "DP3")

        svc = RuntimeDiscoverService(db_session, policy=FakeDenyPolicy())
        results, total = svc.discover(
            RuntimeDiscoverFilters(limit=5), AuthContext()
        )
        assert total == 0
        assert len(results) == 0

    def test_resolve_deny_policy_returns_404(self, client, db_session):
        from app.services.runtime_discover_service import (
            RuntimeDiscoverService,
            CapabilityNotAvailableError,
        )

        item_id, _ = _setup_published(client, "DP4")

        svc = RuntimeDiscoverService(db_session, policy=FakeDenyPolicy())
        try:
            svc.resolve(uuid.UUID(item_id), AuthContext())
            assert False, "should have raised"
        except CapabilityNotAvailableError:
            pass

    def test_deny_policy_rejects_required_dependency(self, client, db_session):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.services.exceptions import RequiredDependencyUnavailableError

        agent_id, _ = _setup_published(client, "DP5Agent")
        skill_id, _ = _setup_published(client, "DP5Skill", "skill")

        r = client.post(
            "/api/hub/relations",
            json={
                "source_item_id": agent_id,
                "target_item_id": skill_id,
                "relation_type": "uses",
                "relation_scope": "runtime",
                "required": True,
            },
        )
        assert r.status_code == 201

        policy = TargetedDenyPolicy(denied_ids={skill_id})
        svc = RuntimeDiscoverService(db_session, policy=policy)
        try:
            svc.resolve(uuid.UUID(agent_id), AuthContext())
            assert False
        except RequiredDependencyUnavailableError as e:
            assert skill_id in e.target_item_id

    def test_deny_policy_skips_optional_dependency(self, client, db_session):
        from app.services.runtime_discover_service import RuntimeDiscoverService

        agent_id, _ = _setup_published(client, "DP6Agent")
        skill_id, _ = _setup_published(client, "DP6Skill", "skill")

        r = client.post(
            "/api/hub/relations",
            json={
                "source_item_id": agent_id,
                "target_item_id": skill_id,
                "relation_type": "uses",
                "relation_scope": "runtime",
                "required": False,
            },
        )
        assert r.status_code == 201

        policy = TargetedDenyPolicy(denied_ids={skill_id})
        svc = RuntimeDiscoverService(db_session, policy=policy)
        result = svc.resolve(uuid.UUID(agent_id), AuthContext())
        assert result["relations"] == []

    def test_deny_policy_optional_warning_at_depth_2(
        self, client, db_session
    ):
        from app.services.runtime_discover_service import RuntimeDiscoverService

        agent_id, _ = _setup_published(client, "DP7Agent")
        skill_id, _ = _setup_published(client, "DP7Skill", "skill")

        r = client.post(
            "/api/hub/relations",
            json={
                "source_item_id": agent_id,
                "target_item_id": skill_id,
                "relation_type": "uses",
                "relation_scope": "runtime",
                "required": False,
            },
        )
        assert r.status_code == 201

        policy = TargetedDenyPolicy(denied_ids={skill_id})
        svc = RuntimeDiscoverService(db_session, policy=policy)
        result = svc.resolve(
            uuid.UUID(agent_id), AuthContext(), depth=2
        )

        warnings = result["dependency_warnings"]
        assert len(warnings) >= 1
        assert warnings[0]["warning_type"] == "optional_policy_denied"
        assert result["dependencies"] == []

    def test_deny_policy_required_still_409(
        self, client, db_session
    ):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.services.exceptions import RequiredDependencyUnavailableError

        agent_id, _ = _setup_published(client, "DP8Agent")
        skill_id, _ = _setup_published(client, "DP8Skill", "skill")

        r = client.post(
            "/api/hub/relations",
            json={
                "source_item_id": agent_id,
                "target_item_id": skill_id,
                "relation_type": "uses",
                "relation_scope": "runtime",
                "required": True,
            },
        )
        assert r.status_code == 201

        policy = TargetedDenyPolicy(denied_ids={skill_id})
        svc = RuntimeDiscoverService(db_session, policy=policy)
        try:
            svc.resolve(uuid.UUID(agent_id), AuthContext(), depth=2)
            assert False
        except RequiredDependencyUnavailableError as e:
            assert skill_id in e.target_item_id


# ─── RBAC-4 Runtime Consumer Role Tests ───


def _consumer_headers(extra_scopes: str = "") -> dict:
    headers: dict = {
        "X-Actor-ID": "svc-consumer",
        "X-Roles": "runtime_consumer",
    }
    if extra_scopes:
        headers["X-Scopes"] = extra_scopes
    return headers


def _admin_headers() -> dict:
    return {"X-Actor-ID": "admin", "X-Roles": "platform_admin"}


def _contributor_headers() -> dict:
    return {"X-Actor-ID": "contrib1", "X-Roles": "contributor"}


class TestEntryLevelRole:
    def test_runtime_consumer_discover_200(self, client: TestClient):
        _setup_published(client, "R4D1")
        headers = _consumer_headers("capability:discover")
        resp = client.get("/api/runtime/capabilities/discover", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    def test_no_runtime_role_discover_403(self, client: TestClient):
        _setup_published(client, "R4D2")
        resp = client.get(
            "/api/runtime/capabilities/discover",
            headers={"X-Actor-ID": "nobody"},
        )
        assert resp.status_code == 403

    def test_contributor_discover_403(self, client: TestClient):
        _setup_published(client, "R4D3")
        resp = client.get(
            "/api/runtime/capabilities/discover",
            headers=_contributor_headers(),
        )
        assert resp.status_code == 403

    def test_contributor_plus_consumer_discover_200(self, client: TestClient):
        _setup_published(client, "R4D4")
        headers = {
            "X-Actor-ID": "dual-role",
            "X-Roles": "contributor,runtime_consumer",
            "X-Scopes": "capability:discover",
        }
        resp = client.get("/api/runtime/capabilities/discover", headers=headers)
        assert resp.status_code == 200

    def test_runtime_consumer_missing_resolve_scope_403(self, client: TestClient):
        item_id, _ = _setup_published(client, "R4D5")
        headers = _consumer_headers("capability:discover")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve", headers=headers
        )
        assert resp.status_code == 403

    def test_runtime_consumer_resolve_200(self, client: TestClient):
        item_id, _ = _setup_published(client, "R4D6")
        headers = _consumer_headers("capability:resolve")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve", headers=headers
        )
        assert resp.status_code == 200

    def test_platform_admin_discover_200(self, client: TestClient):
        _setup_published(client, "R4D7")
        resp = client.get(
            "/api/runtime/capabilities/discover", headers=_admin_headers()
        )
        assert resp.status_code == 200

    def test_platform_admin_no_header_dev_200(self, client: TestClient):
        _setup_published(client, "R4D7b")
        resp = client.get("/api/runtime/capabilities/discover")
        assert resp.status_code == 200

    def test_runtime_api_unaffected_by_ownership(self, client: TestClient):
        item_id, _ = _setup_published(client, "R4Own")
        headers = _contributor_headers()
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve", headers=headers
        )
        assert resp.status_code == 403


class TestManifestScope:
    def test_manifest_requires_capability_manifest(self, client: TestClient):
        item_id, _ = _setup_published(client, "R4Man1")
        headers = _consumer_headers("capability:manifest")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/manifest", headers=headers
        )
        assert resp.status_code == 200

    def test_manifest_fallback_resolve_scope(self, client: TestClient):
        item_id, _ = _setup_published(client, "R4Man2")
        headers = _consumer_headers("capability:resolve")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/manifest", headers=headers
        )
        assert resp.status_code == 200

    def test_manifest_missing_scope_403(self, client: TestClient):
        item_id, _ = _setup_published(client, "R4Man3")
        headers = _consumer_headers("capability:discover")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/manifest", headers=headers
        )
        assert resp.status_code == 403


class TestToolDefinitionScope:
    def _setup_published_tool(self, client: TestClient, name: str) -> str:
        item_id = _create_item(client, name, "tool")
        r = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={
                "hub_item_id": item_id,
                "version": "1.0.0",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
        vid = r.json()["id"]
        _publish(client, item_id, vid)
        return item_id

    def test_tool_def_requires_capability_tool_definition(self, client: TestClient):
        item_id = self._setup_published_tool(client, "R4TD1")
        headers = _consumer_headers("capability:tool_definition")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition", headers=headers
        )
        assert resp.status_code == 200

    def test_tool_def_fallback_resolve_scope(self, client: TestClient):
        item_id = self._setup_published_tool(client, "R4TD2")
        headers = _consumer_headers("capability:resolve")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition", headers=headers
        )
        assert resp.status_code == 200

    def test_tool_def_missing_scope_403(self, client: TestClient):
        item_id = self._setup_published_tool(client, "R4TD3")
        headers = _consumer_headers("capability:discover")
        resp = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition", headers=headers
        )
        assert resp.status_code == 403


class TestAssetLevelPolicyDeny:
    def _setup_published_tool(self, client: TestClient, name: str) -> str:
        item_id = _create_item(client, name, "tool")
        r = client.post(
            f"/api/hub/items/{item_id}/versions",
            json={
                "hub_item_id": item_id,
                "version": "1.0.0",
                "input_schema": {"type": "object", "properties": {}},
            },
        )
        vid = r.json()["id"]
        _publish(client, item_id, vid)
        return item_id

    def test_discover_policy_deny_silent_exclusion(
        self, client: TestClient, db_session: Session
    ):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.schemas.runtime import RuntimeDiscoverFilters

        item_id_a, _ = _setup_published(client, "ALDP1-A")
        item_id_b, _ = _setup_published(client, "ALDP1-B")

        svc = RuntimeDiscoverService(
            db_session, policy=TargetedDenyPolicy(denied_ids={item_id_b})
        )
        ctx = AuthContext(
            actor_id="svc",
            roles=["runtime_consumer"],
            is_authenticated=True,
        )
        results, total = svc.discover(RuntimeDiscoverFilters(), ctx)
        names = {item.name for item, _ in results}
        assert "ALDP1-A" in names
        assert "ALDP1-B" not in names
        assert total >= 1

    def test_resolve_policy_deny_404(self, client: TestClient, db_session: Session):
        from app.services.runtime_discover_service import (
            RuntimeDiscoverService,
            CapabilityNotAvailableError,
        )

        item_id, _ = _setup_published(client, "ALDP2")
        svc = RuntimeDiscoverService(db_session, policy=FakeDenyPolicy())
        ctx = AuthContext(
            actor_id="svc",
            roles=["runtime_consumer"],
            is_authenticated=True,
        )
        try:
            svc.resolve(uuid.UUID(item_id), ctx)
            assert False, "should have raised"
        except CapabilityNotAvailableError:
            pass

    def test_manifest_policy_deny_404(self, client: TestClient, db_session: Session):
        from app.services.runtime_discover_service import (
            RuntimeDiscoverService,
            CapabilityNotAvailableError,
        )

        item_id, _ = _setup_published(client, "ALDP3")
        svc = RuntimeDiscoverService(db_session, policy=FakeDenyPolicy())
        ctx = AuthContext(
            actor_id="svc",
            roles=["runtime_consumer"],
            is_authenticated=True,
        )
        try:
            svc.resolve(uuid.UUID(item_id), ctx, depth=1)
            assert False
        except CapabilityNotAvailableError:
            pass

    def test_tool_def_policy_deny_404(self, client: TestClient, db_session: Session):
        from app.services.runtime_discover_service import (
            RuntimeDiscoverService,
            CapabilityNotAvailableError,
        )

        item_id = self._setup_published_tool(client, "ALDP4")
        svc = RuntimeDiscoverService(db_session, policy=FakeDenyPolicy())
        ctx = AuthContext(
            actor_id="svc",
            roles=["runtime_consumer"],
            is_authenticated=True,
        )
        try:
            svc.build_tool_definition(uuid.UUID(item_id), ctx)
            assert False
        except CapabilityNotAvailableError:
            pass


class TestScopedPolicyDirect:
    def test_scoped_policy_platform_admin_allow(self):
        from app.policies.capability_access import ScopedCapabilityAccessPolicy

        policy = ScopedCapabilityAccessPolicy()
        ctx = AuthContext(
            actor_id="admin",
            roles=["platform_admin"],
            is_authenticated=True,
        )
        assert policy.can_discover(None, None, ctx) is True
        assert policy.can_resolve(None, None, ctx) is True

    def test_scoped_policy_runtime_consumer_allow(self):
        from app.policies.capability_access import ScopedCapabilityAccessPolicy

        policy = ScopedCapabilityAccessPolicy()
        ctx = AuthContext(
            actor_id="svc",
            roles=["runtime_consumer"],
            is_authenticated=True,
        )
        assert policy.can_discover(None, None, ctx) is True
        assert policy.can_resolve(None, None, ctx) is True

    def test_scoped_policy_contributor_deny(self):
        from app.policies.capability_access import ScopedCapabilityAccessPolicy

        policy = ScopedCapabilityAccessPolicy()
        ctx = AuthContext(
            actor_id="contrib1",
            roles=["contributor"],
            is_authenticated=True,
        )
        assert policy.can_discover(None, None, ctx) is False
        assert policy.can_resolve(None, None, ctx) is False

    def test_scoped_policy_unauthenticated_deny(self):
        from app.policies.capability_access import ScopedCapabilityAccessPolicy

        policy = ScopedCapabilityAccessPolicy()
        ctx = AuthContext()
        assert policy.can_discover(None, None, ctx) is False
        assert policy.can_resolve(None, None, ctx) is False


class TestExistingTestsUnaffected:
    def test_dev_mode_discover_still_works(self, client: TestClient):
        _setup_published(client, "ETU1")
        resp = client.get("/api/runtime/capabilities/discover")
        assert resp.status_code == 200
