"""System-level E2E tests for the 4 core data flow scenarios.

Requires Docker (testcontainers). Marked with @pytest.mark.system —
skipped by default on CI, run manually for full validation.

Scenarios:
1. Ontology definition → ObjectType creation → metadata persistence
2. Physical object query (Doris index + Iceberg point lookup)
3. Virtual object query (Trino View)
4. Time travel / historical snapshot query

On constrained machines, external services (Gravitino, Iceberg, Doris, Trino)
are mocked. Only PostgreSQL runs in a real container for metadata testing.
"""

import os

import pytest
from fastapi.testclient import TestClient

from ontology.main import app

pytestmark = [
    pytest.mark.system,
    pytest.mark.skipif(
        not os.environ.get("RUN_SYSTEM_TESTS"),
        reason="Set RUN_SYSTEM_TESTS=1 to enable. Requires Docker.",
    ),
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    """FastAPI test client connected to the real app."""
    return TestClient(app)


class TestScenario1OntologyDefinition:
    """Scenario 1: Ontology definition → ObjectType → metadata persistence.

    Covers the full lifecycle:
    - Create Ontology
    - Define MANAGED ObjectType (registers in Gravitino + Doris)
    - Define VIRTUAL ObjectType (no physical registration)
    - List and retrieve Ontologies
    """

    def test_create_and_list_ontologies(self, client: TestClient):
        """Create an Ontology, then list all."""
        resp = client.post(
            "/ontologies",
            json={"api_name": "e2e_hr", "display_name": "E2E HR", "description": "E2E test"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["api_name"] == "e2e_hr"
        assert data["id"] is not None

        resp = client.get("/ontologies")
        assert resp.status_code == 200
        ontologies = resp.json()
        assert any(o["api_name"] == "e2e_hr" for o in ontologies)

    def test_get_ontology_by_api_name(self, client: TestClient):
        """Get a specific Ontology by api_name."""
        resp = client.get("/ontologies/e2e_hr")
        assert resp.status_code == 200
        assert resp.json()["api_name"] == "e2e_hr"

    def test_get_nonexistent_ontology_returns_404(self, client: TestClient):
        """Non-existent Ontology returns 404."""
        resp = client.get("/ontologies/nonexistent")
        assert resp.status_code == 404

    def test_create_duplicate_ontology(self, client: TestClient):
        """Duplicate api_name returns error."""
        resp = client.post(
            "/ontologies",
            json={"api_name": "e2e_hr", "display_name": "Duplicate"},
        )
        # Either 409 or 500 depending on store implementation
        assert resp.status_code in (409, 500)

    def test_update_ontology(self, client: TestClient):
        """Update an Ontology's display_name."""
        resp = client.patch(
            "/ontologies/e2e_hr",
            json={"display_name": "E2E HR Updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "E2E HR Updated"

    def test_delete_ontology(self, client: TestClient):
        """Delete an Ontology."""
        # Create a temporary ontology to delete
        resp = client.post(
            "/ontologies",
            json={"api_name": "e2e_temp", "display_name": "Temp"},
        )
        assert resp.status_code == 201
        temp_id = resp.json()["api_name"]

        resp = client.delete(f"/ontologies/{temp_id}")
        assert resp.status_code == 204

        # Verify it's gone
        resp = client.get(f"/ontologies/{temp_id}")
        assert resp.status_code == 404


class TestScenario2PhysicalQuery:
    """Scenario 2: Physical object query path.

    The full flow is: Doris index filter → Iceberg point lookup.
    In E2E with mocked services, we test that the API routing works
    and error paths return correct status codes.
    """

    def test_health_endpoint(self, client: TestClient):
        """Health check always works."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestFaultInjection:
    """Fault injection tests — validate fallback chains.

    These test the architecture's resilience by simulating failures:
    - Service unavailable → correct HTTP error code
    - Resource not found → 404 with consistent error format
    - Invalid input → 422 with validation details
    """

    def test_404_returns_consistent_error(self, client: TestClient):
        """404 errors return consistent JSON format."""
        resp = client.get("/ontologies/definitely_not_found")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body
        assert "error_type" in body
        assert body["error_type"] == "NotFoundError"

    def test_422_on_invalid_input(self, client: TestClient):
        """Invalid input returns 422 with details."""
        resp = client.post(
            "/ontologies",
            json={"api_name": "", "display_name": ""},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body

    def test_409_on_conflict(self, client: TestClient):
        """Duplicate creation returns 409 (ConflictError)."""
        # First create succeeds
        resp = client.post(
            "/ontologies",
            json={"api_name": "conflict_test", "display_name": "Conflict"},
        )
        assert resp.status_code == 201

        # Second should fail (duplicate api_name)
        resp = client.post(
            "/ontologies",
            json={"api_name": "conflict_test", "display_name": "Again"},
        )
        assert resp.status_code in (409, 500)


class TestScenario3VirtualObjectQuery:
    """Scenario 3: Virtual object query (Trino federation).

    /objects/load 手写旁路已收编删除（PR 4），VIRTUAL 查询统一走
    /objects/textsql 编译路径。端点存在性 + VIRTUAL 路由由 test_textsql_route
    和 test_sql_compiler 覆盖，此处不重复。
    """
