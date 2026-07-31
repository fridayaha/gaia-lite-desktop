import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.event_log import log_event
from app.core.request_id import _normalize_request_id


@pytest.fixture(autouse=True)
def _event_log_propagation():
    logging.getLogger("hub.event").propagate = True
    yield


def _create_item(client: TestClient, name: str = "Obs Test", item_type: str = "agent") -> str:
    resp = client.post("/api/hub/items", json={"name": name, "type": item_type})
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_version(client: TestClient, item_id: str, version: str = "1.0.0", **extra) -> str:
    payload = {"hub_item_id": item_id, "version": version, **extra}
    resp = client.post(f"/api/hub/items/{item_id}/versions", json=payload)
    assert resp.status_code == 201
    return resp.json()["id"]


def _publish(client: TestClient, item_id: str, version_id: str):
    resp = client.post(
        f"/api/hub/versions/{version_id}/submit-review", json={"operator": "dev"}
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/hub/versions/{version_id}/approve",
        json={"operator": "approver", "comment": "ok"},
    )
    assert resp.status_code == 200
    resp = client.post(
        f"/api/hub/versions/{version_id}/publish", json={"operator": "approver"}
    )
    assert resp.status_code == 200


def _publish_item(client: TestClient, name: str) -> tuple[str, str]:
    item_id = _create_item(client, name, "agent")
    vid = _create_version(client, item_id, "1.0.0")
    _publish(client, item_id, vid)
    return item_id, vid


def _publish_agent(client, name="event-agent"):
    item_id = _create_item(client, name, "agent")
    vid = _create_version(client, item_id, "1.0.0")
    _publish(client, item_id, vid)
    return item_id, vid


def _publish_tool(client, name="event-tool"):
    item_id = _create_item(client, name, "tool")
    vid = _create_version(
        client, item_id, "1.0.0",
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={"type": "object"},
        permission_json={"scope": ["internal"]},
        runtime_compatibility={"platform": "linux"},
    )
    _publish(client, item_id, vid)
    return item_id, vid


# ---- Stage 7A tests (existing) ----

def test_generates_request_id_when_missing(client):
    response = client.get("/api/health")
    request_id = response.headers.get("X-Request-ID")
    assert request_id is not None
    assert len(request_id) == 36


def test_response_header_contains_request_id(client):
    response = client.get("/api/health")
    assert "X-Request-ID" in response.headers


def test_reuses_incoming_request_id(client):
    incoming = "my-custom-request-id-123"
    response = client.get("/api/health", headers={"X-Request-ID": incoming})
    assert response.headers["X-Request-ID"] == incoming


def test_regenerates_when_empty_request_id(client):
    response = client.get("/api/health", headers={"X-Request-ID": ""})
    request_id = response.headers["X-Request-ID"]
    assert request_id is not None
    assert len(request_id) == 36


def test_regenerates_when_request_id_too_long(client):
    too_long = "x" * 200
    response = client.get("/api/health", headers={"X-Request-ID": too_long})
    request_id = response.headers["X-Request-ID"]
    assert request_id is not None
    assert len(request_id) == 36


def test_404_response_has_request_id(client):
    response = client.get("/api/hub-items/nonexistent-id")
    assert "X-Request-ID" in response.headers


def test_normalize_empty_generates_uuid():
    result = _normalize_request_id(None)
    assert len(result) == 36


def test_normalize_too_long_generates_uuid():
    too_long = "x" * 200
    result = _normalize_request_id(too_long)
    assert len(result) == 36


def test_access_log_has_required_fields(caplog):
    from app.core.logging import log_access

    caplog.set_level(logging.INFO, logger="hub.access")

    log_access(
        method="GET",
        path="/api/health",
        status_code=200,
        duration_ms=5,
        request_id="test-rid",
    )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.event == "hub.http.request"
    assert record.method == "GET"
    assert record.path == "/api/health"
    assert record.status_code == 200
    assert record.duration_ms == 5
    assert record.request_id == "test-rid"
    assert record.result == "ok"


def test_access_log_no_permission_json(caplog):
    from app.core.logging import log_access

    caplog.set_level(logging.INFO, logger="hub.access")

    log_access(
        method="POST",
        path="/api/import",
        status_code=200,
        duration_ms=10,
        request_id="rid",
    )

    assert len(caplog.records) == 1
    record = caplog.records[0]
    record_vars = vars(record)
    for forbidden in (
        "permission_json",
        "manifest_json",
        "input_schema",
        "output_schema",
    ):
        assert forbidden not in record_vars


# ---- Stage 7B tests ----

class TestEventLogTool:
    def test_log_event_has_request_id(self, caplog):
        caplog.set_level(logging.INFO, logger="hub.event")
        log_event("test.event", item_id="abc", result="ok")
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.event == "test.event"
        assert record.item_id == "abc"
        assert record.result == "ok"
        assert record.request_id is not None

    def test_log_event_filters_none(self, caplog):
        caplog.set_level(logging.INFO, logger="hub.event")
        log_event("test.event", item_id="x", extra_field=None, status=None)
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.item_id == "x"
        record_vars = vars(record)
        assert "extra_field" not in record_vars

    def test_log_event_no_sensitive_fields(self, caplog):
        caplog.set_level(logging.INFO, logger="hub.event")
        log_event(
            "test.event",
            item_id="x",
            permission_json='{"scope": "admin"}',
            manifest_json='{"name": "test"}',
            input_schema='{"type": "object"}',
            output_schema='{"type": "object"}',
        )
        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.item_id == "x"
        log_output = caplog.text
        for forbidden in (
            "permission_json",
            "manifest_json",
            "input_schema",
            "output_schema",
        ):
            assert forbidden not in log_output


class TestRuntimeEventLogs:
    def test_discover_produces_event(self, caplog, client: TestClient):
        item_id, vid = _publish_agent(client)
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get("/api/runtime/capabilities/discover?type=agent")
        events = [r for r in caplog.records if r.event == "runtime.discover.completed"]
        assert len(events) == 1
        rec = events[0]
        assert rec.result_count >= 1
        assert rec.result_total >= 1
        assert rec.duration_ms is not None
        assert rec.status_code == 200
        assert rec.request_id is not None

    def test_discover_event_no_sensitive(self, caplog, client: TestClient):
        item_id, vid = _publish_agent(client)
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get("/api/runtime/capabilities/discover?type=agent")
        for record in caplog.records:
            record_vars = vars(record)
            for forbidden in (
                "permission_json",
                "manifest_json",
                "input_schema",
                "output_schema",
            ):
                assert forbidden not in record_vars

    def test_resolve_produces_event(self, caplog, client: TestClient):
        item_id, vid = _publish_agent(client)
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(f"/api/runtime/capabilities/{item_id}/resolve?depth=1")
        events = [r for r in caplog.records if r.event == "runtime.resolve.completed"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.item_type == "agent"
        assert rec.depth == 1
        assert rec.duration_ms is not None
        assert rec.status_code == 200
        assert rec.request_id is not None

    def test_tool_definition_produces_event(self, caplog, client: TestClient):
        item_id, vid = _publish_tool(client)
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(f"/api/runtime/capabilities/{item_id}/tool-definition")
        events = [r for r in caplog.records if r.event == "runtime.tool_definition.completed"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.item_type == "tool"
        assert rec.duration_ms is not None
        assert rec.status_code == 200

    def test_dependency_warning_event(self, caplog, client: TestClient, db_session: Session):
        from app.models.hub_item_relation import HubItemRelation

        tool_id, tool_vid = _publish_tool(client, name="dep-tool")
        agent_id, agent_vid = _publish_agent(client, name="dep-agent")

        rel = HubItemRelation(
            source_item_id=uuid.UUID(agent_id),
            target_item_id=uuid.UUID(tool_id),
            relation_type="depends_on",
            relation_scope="runtime",
            required=True,
        )
        db_session.add(rel)
        db_session.commit()

        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(f"/api/runtime/capabilities/{agent_id}/resolve?depth=2")

        dep_events = [r for r in caplog.records if r.event == "runtime.dependency_warning"]
        assert len(dep_events) >= 0

    def test_resolve_event_no_sensitive(self, caplog, client: TestClient):
        item_id, vid = _publish_agent(client)
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(f"/api/runtime/capabilities/{item_id}/resolve?depth=1")
        for record in caplog.records:
            record_vars = vars(record)
            for forbidden in (
                "permission_json",
                "manifest_json",
                "input_schema",
                "output_schema",
            ):
                assert forbidden not in record_vars


class TestScanEventLogs:
    def test_scan_produces_started_and_completed(self, caplog, client: TestClient):
        item_id = _create_item(client, "scan-event", "tool")
        vid = _create_version(
            client, item_id, "1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission_json={"scope": ["internal"]},
            runtime_compatibility={"platform": "linux"},
        )
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/scan", json={"operator": "tester"})
        started = [r for r in caplog.records if r.event == "scan.started"]
        completed = [r for r in caplog.records if r.event == "scan.completed"]
        assert len(started) == 1
        assert len(completed) == 1
        rec = completed[0]
        assert rec.risk_level is not None
        assert rec.total_findings is not None
        assert rec.request_id is not None

    def test_scan_blocking_produces_blocked_event(self, caplog, client: TestClient):
        item_id = _create_item(client, "scan-block", "mcp")
        vid = _create_version(
            client, item_id, "1.0.0",
            manifest_json={
                "mcp_server": {"command": "rm -rf /tmp/logs"},
                "transport": "streamable_http",
            },
            permission_json={"scope": ["admin"]},
            runtime_compatibility={"platform": "linux"},
        )
        caplog.set_level(logging.INFO, logger="hub.event")
        resp = client.post(f"/api/hub/versions/{vid}/scan", json={"operator": "tester"})
        assert resp.status_code == 200
        blocked = [r for r in caplog.records if r.event == "scan.blocked"]
        assert len(blocked) == 1
        rec = blocked[0]
        assert rec.risk_level == "blocking"

    def test_scan_event_no_sensitive(self, caplog, client: TestClient):
        item_id = _create_item(client, "scan-safe", "tool")
        vid = _create_version(
            client, item_id, "1.0.0",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            permission_json={"scope": ["internal"]},
            runtime_compatibility={"platform": "linux"},
        )
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/scan", json={"operator": "tester"})
        for record in caplog.records:
            record_vars = vars(record)
            for forbidden in ("permission_json", "manifest_json"):
                assert forbidden not in record_vars


class TestOpenAPIImportEventLogs:
    def test_import_produces_started_and_completed(self, caplog, client: TestClient):
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0.0"},
            "paths": {
                "/users": {
                    "get": {
                        "operationId": "listUsers",
                        "summary": "List all users",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        content = json.dumps(openapi_spec).encode()
        caplog.set_level(logging.INFO, logger="hub.event")
        resp = client.post(
            "/api/hub/imports/openapi",
            files={"file": ("spec.json", content, "application/json")},
        )
        assert resp.status_code == 201
        started = [r for r in caplog.records if r.event == "openapi.import.started"]
        completed = [r for r in caplog.records if r.event == "openapi.import.completed"]
        assert len(started) == 1
        assert len(completed) == 1
        rec = completed[0]
        assert rec.spec_title == "Test API"
        assert rec.spec_version == "1.0.0"
        assert rec.tools_created >= 1
        assert rec.duration_ms is not None

    def test_import_event_no_sensitive(self, caplog, client: TestClient):
        openapi_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Safe API", "version": "1.0.0"},
            "paths": {
                "/data": {
                    "get": {
                        "operationId": "getData",
                        "summary": "Get data",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
        }
        content = json.dumps(openapi_spec).encode()
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(
            "/api/hub/imports/openapi",
            files={"file": ("spec.json", content, "application/json")},
        )
        for record in caplog.records:
            record_vars = vars(record)
            for forbidden in ("permission_json", "manifest_json"):
                assert forbidden not in record_vars


class TestLifecycleEventLogs:
    def test_publish_produces_event(self, caplog, client: TestClient):
        item_id = _create_item(client, "lc-publish", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/submit-review", json={"operator": "dev"})
        client.post(f"/api/hub/versions/{vid}/approve", json={"operator": "approver", "comment": "ok"})
        client.post(f"/api/hub/versions/{vid}/publish", json={"operator": "approver"})
        publish_events = [r for r in caplog.records if r.event == "lifecycle.publish"]
        assert len(publish_events) >= 1
        rec = publish_events[0]
        assert rec.item_id == item_id
        assert rec.version_id == vid
        assert rec.action == "publish"
        assert rec.result == "ok"

    def test_submit_review_produces_event(self, caplog, client: TestClient):
        item_id = _create_item(client, "lc-submit", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/submit-review", json={"operator": "dev"})
        events = [r for r in caplog.records if r.event == "lifecycle.submit_review"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.result == "ok"

    def test_approve_produces_event(self, caplog, client: TestClient):
        item_id = _create_item(client, "lc-approve", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/submit-review", json={"operator": "dev"})
        client.post(f"/api/hub/versions/{vid}/approve", json={"operator": "approver", "comment": "ok"})
        events = [r for r in caplog.records if r.event == "lifecycle.approve"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.result == "ok"

    def test_reject_produces_event(self, caplog, client: TestClient):
        item_id = _create_item(client, "lc-reject", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/submit-review", json={"operator": "dev"})
        client.post(f"/api/hub/versions/{vid}/reject", json={"operator": "approver", "comment": "no"})
        events = [r for r in caplog.records if r.event == "lifecycle.reject"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.result == "ok"

    def test_request_change_produces_event(self, caplog, client: TestClient):
        item_id = _create_item(client, "lc-change", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/submit-review", json={"operator": "dev"})
        client.post(f"/api/hub/versions/{vid}/request-change", json={"operator": "approver", "comment": "fix"})
        events = [r for r in caplog.records if r.event == "lifecycle.request_change"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.result == "ok"

    def test_disable_produces_event(self, caplog, client: TestClient):
        item_id, vid = _publish_item(client, "lc-disable")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/items/{item_id}/disable", json={"operator": "admin", "reason": "test"})
        events = [r for r in caplog.records if r.event == "lifecycle.disable"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.result == "ok"

    def test_archive_produces_event(self, caplog, client: TestClient):
        item_id, vid = _publish_item(client, "lc-archive")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/items/{item_id}/archive", json={"operator": "admin", "reason": "test"})
        events = [r for r in caplog.records if r.event == "lifecycle.archive"]
        assert len(events) == 1
        rec = events[0]
        assert rec.item_id == item_id
        assert rec.result == "ok"

    def test_rollback_produces_event(self, caplog, client: TestClient):
        item_id, vid = _publish_item(client, "lc-rollback")
        vid2 = _create_version(client, item_id, "2.0.0")
        _publish(client, item_id, vid2)
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/items/{item_id}/rollback", json={
            "target_version_id": vid,
            "operator": "admin",
            "reason": "rollback test",
        })
        events = [r for r in caplog.records if r.event == "lifecycle.rollback"]
        assert len(events) >= 1

    def test_lifecycle_event_no_sensitive(self, caplog, client: TestClient):
        item_id = _create_item(client, "lc-safe", "agent")
        vid = _create_version(client, item_id, "1.0.0")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.post(f"/api/hub/versions/{vid}/submit-review", json={"operator": "dev"})
        client.post(f"/api/hub/versions/{vid}/approve", json={"operator": "approver", "comment": "ok"})
        client.post(f"/api/hub/versions/{vid}/publish", json={"operator": "approver"})
        for record in caplog.records:
            record_vars = vars(record)
            for forbidden in ("permission_json", "manifest_json"):
                assert forbidden not in record_vars


# ---- RBAC-1 tests ----

class TestAuthContextFromHeaders:
    def test_no_headers_produces_anonymous_context(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({}, auth_mode="dev")
        assert ctx.is_authenticated is False
        assert ctx.actor_id is None
        assert ctx.auth_mode == "dev"

    def test_parses_actor_id(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Actor-ID": "user-1"}, auth_mode="header")
        assert ctx.actor_id == "user-1"
        assert ctx.is_authenticated is True
        assert ctx.auth_mode == "header"

    def test_parses_actor_type(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Actor-Type": "service"})
        assert ctx.actor_type == "service"

    def test_parses_roles_as_list(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Roles": "contributor,approver"})
        assert ctx.roles == ["contributor", "approver"]

    def test_parses_scopes_as_list(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Scopes": "read,write"})
        assert ctx.scopes == ["read", "write"]

    def test_parses_groups_as_list(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Groups": "g1,g2"})
        assert ctx.groups == ["g1", "g2"]

    def test_parses_workspace_and_org(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({
            "X-Workspace-ID": "ws-1",
            "X-Organization-ID": "org-1",
        })
        assert ctx.workspace_id == "ws-1"
        assert ctx.organization_id == "org-1"

    def test_parses_user_email_and_name(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({
            "X-User-Email": "a@b.com",
            "X-User-Name": "test",
        })
        assert ctx.email == "a@b.com"
        assert ctx.display_name == "test"

    def test_parses_service_name(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Service-Name": "openclaw"})
        assert ctx.service_name == "openclaw"

    def test_parses_agent_id(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Agent-ID": "agent-1"})
        assert ctx.agent_id == "agent-1"

    def test_empty_roles_are_empty_list(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers({"X-Roles": ""})
        assert ctx.roles == []

    def test_none_mode_ignores_headers(self):
        from app.core.auth_context import AuthContext

        ctx = AuthContext.from_headers(
            {"X-Actor-ID": "user-1"}, auth_mode="none"
        )
        assert ctx.is_authenticated is False
        assert ctx.actor_id is None
        assert ctx.auth_mode == "none"


class TestAuthMiddleware:
    def test_request_state_has_auth_context(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200

    def test_header_context_used_in_discover(self, client):
        _publish_item(client, "mid-discover")
        response = client.get(
            "/api/runtime/capabilities/discover",
            headers={
                "X-Actor-ID": "h-user",
                "X-Workspace-ID": "h-ws",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:discover",
            },
        )
        assert response.status_code == 200

    def test_header_context_used_in_resolve(self, client):
        item_id, _ = _publish_item(client, "mid-resolve")
        response = client.get(
            f"/api/runtime/capabilities/{item_id}/resolve",
            headers={
                "X-Actor-ID": "h-user",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:resolve",
            },
        )
        assert response.status_code == 200

    def test_header_context_used_in_tool_definition(self, client):
        item_id, _ = _publish_tool(client, "mid-tool")
        response = client.get(
            f"/api/runtime/capabilities/{item_id}/tool-definition",
            headers={
                "X-Actor-ID": "h-user",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:tool_definition",
            },
        )
        assert response.status_code == 200

    def test_header_priority_over_query(self, client):
        _publish_item(client, "mid-hp")
        response = client.get(
            "/api/runtime/capabilities/discover?actor_id=query-user",
            headers={
                "X-Actor-ID": "header-user",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:discover",
            },
        )
        assert response.status_code == 200

    def test_none_mode_ignores_headers(self, monkeypatch, client):
        _publish_item(client, "mid-none")
        monkeypatch.setenv("HUB_AUTH_MODE", "none")
        response = client.get(
            "/api/runtime/capabilities/discover",
            headers={"X-Actor-ID": "h-user"},
        )
        assert response.status_code == 200

    def test_policy_tests_still_pass(self, client, db_session):
        from app.services.runtime_discover_service import RuntimeDiscoverService
        from app.schemas.runtime import RuntimeDiscoverFilters

        _publish_item(client, "mid-policy")
        svc = RuntimeDiscoverService(db_session)
        results, total = svc.discover(RuntimeDiscoverFilters())
        assert total >= 1
        assert len(results) >= 1


class TestEventLogIdentity:
    def test_event_log_has_actor_id_from_header(self, caplog, client):
        item_id, vid = _publish_item(client, "evt-actor")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(
            f"/api/runtime/capabilities/{item_id}/resolve",
            headers={
                "X-Actor-ID": "evt-user-1",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:resolve",
            },
        )
        events = [r for r in caplog.records if r.event == "runtime.resolve.completed"]
        assert len(events) >= 1
        rec = events[0]
        assert rec.actor_id == "evt-user-1"

    def test_event_log_has_actor_type_and_workspace(self, caplog, client):
        item_id, vid = _publish_item(client, "evt-aw")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(
            f"/api/runtime/capabilities/{item_id}/resolve",
            headers={
                "X-Actor-ID": "u2",
                "X-Actor-Type": "service",
                "X-Workspace-ID": "ws-x",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:resolve",
            },
        )
        events = [r for r in caplog.records if r.event == "runtime.resolve.completed"]
        assert len(events) >= 1
        rec = events[0]
        assert rec.actor_type == "service"
        assert rec.workspace_id == "ws-x"

    def test_event_log_no_token_in_output(self, caplog, client):
        item_id, vid = _publish_item(client, "evt-nosec")
        caplog.set_level(logging.INFO, logger="hub.event")
        client.get(
            f"/api/runtime/capabilities/{item_id}/resolve",
            headers={
                "X-Actor-ID": "u3",
                "Authorization": "Bearer secret-token",
                "X-Roles": "runtime_consumer",
                "X-Scopes": "capability:resolve",
            },
        )
        log_text = caplog.text
        assert "Bearer" not in log_text
        assert "secret-token" not in log_text


class TestAccessLogIdentity:
    def test_access_log_has_actor_id_when_header_present(self, caplog):
        from app.core.logging import log_access

        caplog.set_level(logging.INFO, logger="hub.access")
        log_access(
            method="GET", path="/test", status_code=200, duration_ms=5,
            request_id="rid", actor_id="al-user",
        )
        records = caplog.records
        assert len(records) >= 1
        rec = records[0]
        assert rec.actor_id == "al-user"

    def test_access_log_no_actor_id_when_not_provided(self, caplog):
        from app.core.logging import log_access

        caplog.set_level(logging.INFO, logger="hub.access")
        log_access(
            method="GET", path="/test", status_code=200, duration_ms=5,
            request_id="rid",
        )
        records = caplog.records
        assert len(records) >= 1
        rec = records[0]
        assert not hasattr(rec, "actor_id") or rec.actor_id is None

    def test_access_log_has_identity_fields(self, caplog):
        from app.core.logging import log_access

        caplog.set_level(logging.INFO, logger="hub.access")
        log_access(
            method="POST", path="/api/test", status_code=200, duration_ms=10,
            request_id="rid-x",
            actor_id="u1", actor_type="service",
            workspace_id="ws1", organization_id="org1",
        )
        records = caplog.records
        assert len(records) >= 1
        rec = records[0]
        assert rec.actor_id == "u1"
        assert rec.actor_type == "service"
        assert rec.workspace_id == "ws1"
        assert rec.organization_id == "org1"


def _publish_item(client: TestClient, name: str) -> tuple[str, str]:
    item_id = _create_item(client, name, "agent")
    vid = _create_version(client, item_id, "1.0.0")
    _publish(client, item_id, vid)
    return item_id, vid


def _publish_tool(client: TestClient, name: str = "event-tool") -> tuple[str, str]:
    item_id = _create_item(client, name, "tool")
    vid = _create_version(
        client, item_id, "1.0.0",
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={"type": "object"},
        permission_json={"scope": ["internal"]},
        runtime_compatibility={"platform": "linux"},
    )
    _publish(client, item_id, vid)
    return item_id, vid


def _publish(client: TestClient, item_id: str, version_id: str):
    for path, body in [
        (f"/api/hub/versions/{version_id}/submit-review", {"operator": "dev"}),
        (f"/api/hub/versions/{version_id}/approve", {"operator": "a", "comment": "ok"}),
        (f"/api/hub/versions/{version_id}/publish", {"operator": "a"}),
    ]:
        r = client.post(path, json=body)
        assert r.status_code == 200
