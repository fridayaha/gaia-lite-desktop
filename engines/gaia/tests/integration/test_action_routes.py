"""Integration tests for Action type definition and execution routes."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ontology.config.container import Container
from ontology.main import app


@pytest.fixture
def container() -> Container:
    c = Container()
    c._metadata = AsyncMock()
    c._catalog = AsyncMock()
    c._dataset = AsyncMock()
    c._index = AsyncMock()
    c._pipeline = AsyncMock()
    c._engine = AsyncMock()
    return c


@pytest.fixture
def client(container) -> TestClient:
    with patch("ontology.routes.ontology.container", container):
        with patch("ontology.routes.query.container", container):
            with patch("ontology.routes.action.container", container):
                with patch("ontology.routes.datasource.container", container):
                    yield TestClient(app)


class TestExecuteAction:
    def test_execute_action_success(self, client, container):
        """Use a real ActionExecutionResult for pydantic validation."""
        from ontology.core.schemas.action import ActionExecutionResult

        container.service_overrides["action_service"] = AsyncMock()
        result = ActionExecutionResult(
            status="applied",
            action_id="act-1",
            affected_objects={"obj-1": 2},
            mutations=[{"type": "UPDATE_OBJECT", "object_id": "obj-1"}],
        )
        container.service_overrides["action_service"].execute_action = AsyncMock(return_value=result)

        resp = client.post(
            "/actions/execute/hr/employee/update_status",
            json={
                "parameters": {"status": "active"},
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "applied"

    def test_execute_action_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["action_service"] = AsyncMock()
        container.service_overrides["action_service"].execute_action = AsyncMock(
            side_effect=NotFoundError("ActionType", "ghost"),
        )
        resp = client.post(
            "/actions/execute/hr/employee/ghost_action",
            json={
                "parameters": {},
            },
        )
        assert resp.status_code == 404

    def test_execute_action_validation_error(self, client, container):
        from ontology.core.exceptions import ValidationError

        container.service_overrides["action_service"] = AsyncMock()
        container.service_overrides["action_service"].execute_action = AsyncMock(
            side_effect=ValidationError("Missing required param"),
        )
        resp = client.post(
            "/actions/execute/hr/employee/bad_action",
            json={
                "parameters": {},
            },
        )
        assert resp.status_code == 422

    def test_execute_action_conflict(self, client, container):
        from ontology.core.exceptions import ConflictError

        container.service_overrides["action_service"] = AsyncMock()
        container.service_overrides["action_service"].execute_action = AsyncMock(
            side_effect=ConflictError("OCC conflict"),
        )
        resp = client.post(
            "/actions/execute/hr/employee/update_status",
            json={
                "parameters": {},
            },
        )
        assert resp.status_code == 409

    def test_execute_action_forbidden(self, client, container):
        from ontology.core.exceptions import ForbiddenError

        container.service_overrides["action_service"] = AsyncMock()
        container.service_overrides["action_service"].execute_action = AsyncMock(
            side_effect=ForbiddenError("Write access denied"),
        )
        resp = client.post(
            "/actions/execute/hr/employee/update_status",
            json={
                "parameters": {},
            },
        )
        assert resp.status_code == 403


class TestDefineActionType:
    def test_define_action_type_success(self, client, container):
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        container.service_overrides["action_service"] = AsyncMock()
        result = ActionType(
            id="at1",
            ontology_id="onto1",
            api_name="approve",
            display_name="Approve",
            description="",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        container.service_overrides["action_service"].define_action_type = AsyncMock(return_value=result)

        resp = client.post(
            "/actions/definitions/hr/approve",
            json={
                "api_name": "approve",
                "display_name": "Approve Order",
                "affected_object_type_api_name": "order",
                "parameters": [{"api_name": "x", "display_name": "X", "data_type": "STRING"}],
                "rules": [],
                "effects": [],
            },
        )
        assert resp.status_code == 201

    def test_define_action_type_not_found(self, client, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["action_service"] = AsyncMock()
        container.service_overrides["action_service"].define_action_type = AsyncMock(
            side_effect=NotFoundError("Ontology", "ghost"),
        )
        resp = client.post(
            "/actions/definitions/ghost/approve",
            json={
                "api_name": "approve",
                "display_name": "Approve",
                "affected_object_type_api_name": "order",
                "parameters": [{"api_name": "x", "display_name": "X", "data_type": "STRING"}],
                "rules": [],
                "effects": [],
            },
        )
        assert resp.status_code == 404


class TestActionTypeVersioningRoutes:
    """P1 (ADR-011): update / versions / rollback routes."""

    def test_update_action_type_route(self, client, container):
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        container.service_overrides["action_service"] = AsyncMock()
        updated = ActionType(
            id="at1",
            ontology_id="o1",
            api_name="approve",
            display_name="Approve",
            description="new",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            risk_level="low",
            version=2,
            operation_kind="mixed",
            batch_enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        container.service_overrides["action_service"].update_action_type = AsyncMock(return_value=updated)

        resp = client.patch(
            "/actions/definitions/hr/approve",
            json={"description": "new"},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == 2

    def test_list_action_type_versions_route(self, client, container):
        from datetime import UTC, datetime

        from ontology.core.models.ontology import ActionTypeVersionModel

        container.service_overrides["action_service"] = AsyncMock()
        v1 = ActionTypeVersionModel(
            id="v1",
            action_type_id="at1",
            version=1,
            snapshot={"display_name": "old"},
            published_by="system",
            created_at=datetime.now(UTC),
        )
        container.service_overrides["action_service"].list_action_type_versions = AsyncMock(return_value=[v1])

        resp = client.get("/actions/definitions/hr/approve/versions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["version"] == 1

    def test_rollback_action_type_route(self, client, container):
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        container.service_overrides["action_service"] = AsyncMock()
        rolled = ActionType(
            id="at1",
            ontology_id="o1",
            api_name="approve",
            display_name="Approve",
            description="restored",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            risk_level="low",
            version=3,
            operation_kind="mixed",
            batch_enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        container.service_overrides["action_service"].rollback_action_type = AsyncMock(return_value=rolled)

        resp = client.post("/actions/definitions/hr/approve/rollback/1")
        assert resp.status_code == 200
        assert resp.json()["version"] == 3

    def test_preview_action_route(self, client, container):
        from ontology.core.schemas.action import ActionPreviewResult

        container.service_overrides["action_service"] = AsyncMock()
        preview = ActionPreviewResult(
            valid=True,
            mutations=[{"type": "UPDATE_OBJECT", "object_id": "o1"}],
            before_snapshots={"o1": {"status": "open"}},
        )
        container.service_overrides["action_service"].preview_action = AsyncMock(return_value=preview)

        resp = client.post(
            "/actions/preview/hr/order/ship",
            json={"parameters": {"status": "shipped"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert len(data["mutations"]) == 1

    def test_execute_action_passes_context_from_headers(self, client, container):
        """X-User-Id / X-User-Roles headers populate ActionContext."""
        from ontology.core.schemas.action import ActionExecutionResult

        container.service_overrides["action_service"] = AsyncMock()
        result = ActionExecutionResult(status="applied", action_id="a1")
        captured: dict = {}

        async def capture(*args, **kwargs):
            captured["context"] = kwargs.get("context")
            return result

        container.service_overrides["action_service"].execute_action = AsyncMock(side_effect=capture)

        resp = client.post(
            "/actions/execute/hr/order/ship",
            json={"parameters": {"status": "shipped"}},
            headers={"X-User-Id": "alice", "X-User-Roles": "manager,approver"},
        )
        assert resp.status_code == 200
        ctx = captured["context"]
        assert ctx.current_user == "alice"
        assert "manager" in ctx.user_roles and "approver" in ctx.user_roles


class TestExecuteBatchAction:
    """P2: Batch Action route — POST /actions/execute-batch/{ont}/{ot}/{action}."""

    def test_execute_batch_success(self, client, container):
        """Batch route returns the BatchActionResult from the service."""
        from ontology.core.schemas.action import BatchActionResult

        container.service_overrides["action_service"] = AsyncMock()
        result = BatchActionResult(
            status="applied",
            total=2,
            applied=2,
            failed=0,
            shards_total=1,
            shards_committed=1,
        )
        container.service_overrides["action_service"].execute_batch_action = AsyncMock(return_value=result)

        resp = client.post(
            "/actions/execute-batch/hr/ticket/bulk_update",
            json={
                "items": [
                    {"object_id": "t1", "parameters": {"status": "closed"}},
                    {"object_id": "t2", "parameters": {"status": "closed"}},
                ],
                "default_parameters": {"status": "open"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "applied"
        assert data["total"] == 2
        assert data["applied"] == 2

    def test_execute_batch_passes_context_from_headers(self, client, container):
        """Batch route builds ActionContext from headers, same as single execute."""
        from ontology.core.schemas.action import BatchActionResult

        container.service_overrides["action_service"] = AsyncMock()
        result = BatchActionResult(status="applied", total=1, applied=1, failed=0)
        captured: dict = {}

        async def capture(*args, **kwargs):
            captured["context"] = kwargs.get("context")
            return result

        container.service_overrides["action_service"].execute_batch_action = AsyncMock(side_effect=capture)

        resp = client.post(
            "/actions/execute-batch/hr/ticket/bulk_update",
            json={"items": [{"object_id": "t1"}]},
            headers={"X-User-Id": "batch-user", "X-User-Roles": "operator"},
        )
        assert resp.status_code == 200
        assert captured["context"].current_user == "batch-user"
        assert "operator" in captured["context"].user_roles

    def test_execute_batch_rejects_empty_items(self, client, container):
        """min_length=1 on items → empty batch rejected at the schema layer (422)."""
        container.service_overrides["action_service"] = AsyncMock()

        resp = client.post(
            "/actions/execute-batch/hr/ticket/bulk_update",
            json={"items": []},
        )
        assert resp.status_code == 422

    def test_execute_batch_partial_result(self, client, container):
        """Partial-success result passes through unchanged."""
        from ontology.core.schemas.action import BatchActionResult, BatchItemResult

        container.service_overrides["action_service"] = AsyncMock()
        result = BatchActionResult(
            status="partial",
            total=2,
            applied=1,
            failed=1,
            item_results=[
                BatchItemResult(object_id="t1", status="applied", action_id="a1", new_version=3),
                BatchItemResult(object_id="t2", status="conflict", error="OCC"),
            ],
            first_error="t2: OCC",
        )
        container.service_overrides["action_service"].execute_batch_action = AsyncMock(return_value=result)

        resp = client.post(
            "/actions/execute-batch/hr/ticket/bulk_update",
            json={
                "items": [
                    {"object_id": "t1"},
                    {"object_id": "t2"},
                ],
                "fail_fast": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "partial"
        assert data["failed"] == 1
        assert data["first_error"] == "t2: OCC"
        assert len(data["item_results"]) == 2
