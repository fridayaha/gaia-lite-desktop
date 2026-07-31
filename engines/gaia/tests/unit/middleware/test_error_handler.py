"""Unit tests for ontology error handler middleware."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ontology.core.exceptions import (
    ConflictError,
    DataSourceUnreachableError,
    ForbiddenError,
    NotFoundError,
    OntologyError,
    TrinoUnavailableError,
    ValidationError,
)
from ontology.middleware.error_handler import generic_error_handler, ontology_error_handler


@pytest.fixture
def app() -> FastAPI:
    """A minimal FastAPI app with the error handlers registered."""
    app = FastAPI()
    app.add_exception_handler(OntologyError, ontology_error_handler)
    app.add_exception_handler(Exception, generic_error_handler)
    return app


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


class TestOntologyErrorHandler:
    """Each domain exception maps to correct HTTP status and format."""

    @pytest.mark.parametrize(
        "exc_cls,expected_status,expected_type",
        [
            (NotFoundError, 404, "NotFoundError"),
            (ForbiddenError, 403, "ForbiddenError"),
            (ConflictError, 409, "ConflictError"),
            (ValidationError, 422, "ValidationError"),
        ],
    )
    def test_domain_exception_maps_correctly(self, app, client, exc_cls, expected_status, expected_type):
        """Domain exceptions return matching HTTP status + error_type."""

        @app.get(f"/test_{exc_cls.__name__}")
        async def raise_error():
            raise exc_cls(exc_cls.__name__, "test")

        resp = client.get(f"/test_{exc_cls.__name__}")
        assert resp.status_code == expected_status
        body = resp.json()
        assert body["error_type"] == expected_type
        assert "test" in body["detail"]

    def test_generic_ontology_error(self, app, client):
        """Unclassified OntologyError returns 500."""

        class _TestError(OntologyError):
            pass

        @app.get("/test_generic")
        async def raise_generic():
            raise _TestError("Generic error")

        resp = client.get("/test_generic")
        assert resp.status_code == 500
        assert resp.json()["detail"] == "Internal server error"

    def test_unhandled_exception(self):
        """Non-domain exceptions: verify generic handler logic directly."""
        import types

        request = types.SimpleNamespace()
        request.url = types.SimpleNamespace(path="/test")
        request.method = "GET"

        async def test():
            resp = await generic_error_handler(request, RuntimeError("crash"))
            assert resp.status_code == 500

        import anyio

        anyio.run(test)

    def test_datasource_unreachable_maps_502(self, app, client):
        """DataSourceUnreachableError → 502 + DATASOURCE_UNREACHABLE code.

        Regression: a stopped external DB must surface as 502 (bad gateway:
        upstream down), not a generic 500, and carry the stable code so the
        UI can show a data-source-specific hint instead of blaming Trino.
        """

        @app.get("/test_unreachable")
        async def raise_unreachable():
            raise DataSourceUnreachableError(
                "无法连接到数据源 mysql@localhost:3306",
                code="DATASOURCE_UNREACHABLE",
            )

        resp = client.get("/test_unreachable")
        assert resp.status_code == 502
        body = resp.json()
        assert body["error_type"] == "DataSourceUnreachableError"
        assert body["code"] == "DATASOURCE_UNREACHABLE"
        assert "localhost:3306" in body["detail"]

    def test_trino_unavailable_maps_503(self, app, client):
        """TrinoUnavailableError → 503 + TRINO_UNAVAILABLE code."""

        @app.get("/test_trino_down")
        async def raise_trino_down():
            raise TrinoUnavailableError(
                "Trino server unreachable",
                code="TRINO_UNAVAILABLE",
            )

        resp = client.get("/test_trino_down")
        assert resp.status_code == 503
        body = resp.json()
        assert body["error_type"] == "TrinoUnavailableError"
        assert body["code"] == "TRINO_UNAVAILABLE"
