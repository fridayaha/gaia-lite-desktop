"""Integration tests for HTTP routes — FastAPI TestClient with mocked services."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ontology.config.container import Container
from ontology.config.settings import settings
from ontology.main import app


@pytest.fixture
def container() -> Container:
    """Override the global container with mocked services."""
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
    """TestClient with mocked container injected into all route modules."""
    with patch("ontology.routes.ontology.container", container):
        with patch("ontology.routes.query.container", container):
            with patch("ontology.routes.action.container", container):
                yield TestClient(app)


class TestHealth:
    def test_health(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_metrics(self, client: TestClient):
        resp = client.get("/metrics")
        assert resp.status_code == 200


class TestOntologyRoutes:
    def test_create_ontology(self, client: TestClient, container):
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import Ontology

        container.service_overrides["ontology_service"] = AsyncMock()
        container.service_overrides["ontology_service"].create_ontology = AsyncMock(
            return_value=Ontology(
                id="id1",
                api_name="sys_hr",
                display_name="HR",
                description="",
                rid="",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

        resp = client.post(
            "/ontologies",
            json={
                "api_name": "sys_hr",
                "display_name": "HR",
                "description": "HR department",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["api_name"] == "sys_hr"

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: 路由依赖触发 Gravitino catalog（lite 抛 EditionUnavailableError）",
    )
    def test_create_ontology_invalid(self, client: TestClient):
        resp = client.post(
            "/ontologies",
            json={
                "api_name": "",
                "display_name": "",
            },
        )
        assert resp.status_code == 422

    def test_list_ontologies(self, client: TestClient, container):
        from unittest.mock import MagicMock

        svc = AsyncMock()
        svc._metadata = MagicMock()
        svc._metadata.session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])
        svc._metadata.session.execute = AsyncMock(return_value=mock_result)
        container.service_overrides["ontology_service"] = svc

        resp = client.get("/ontologies")
        assert resp.status_code == 200

    def test_get_ontology_not_found(self, client: TestClient, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["ontology_service"] = AsyncMock()
        container.service_overrides["ontology_service"].get_ontology = AsyncMock(
            side_effect=NotFoundError("Ontology", "ghost")
        )

        resp = client.get("/ontologies/ghost")
        assert resp.status_code == 404
        assert resp.json()["error_type"] == "NotFoundError"

    def test_delete_ontology_not_found(self, client: TestClient, container):
        from ontology.core.exceptions import NotFoundError

        container.service_overrides["ontology_service"] = AsyncMock()
        container.service_overrides["ontology_service"].delete_ontology = AsyncMock(
            side_effect=NotFoundError("Ontology", "ghost")
        )

        resp = client.delete("/ontologies/ghost")
        assert resp.status_code == 404

    def test_delete_ontology_success(self, client: TestClient, container):
        container.service_overrides["ontology_service"] = AsyncMock()
        container.service_overrides["ontology_service"].delete_ontology = AsyncMock(return_value=None)

        resp = client.delete("/ontologies/sys_hr")
        assert resp.status_code == 204
