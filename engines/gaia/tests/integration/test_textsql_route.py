"""Integration tests for POST /objects/textsql (ADR-012 Step 4 path B).

Verifies the route is a thin forwarder: it only passes
``(ontology_api_name, logical_sql)`` to
``ObjectQueryService.execute_compiled_sql``. No ``object_type`` is supplied
by the caller — every ObjectType referenced in the SQL is auto-inferred by
the compiler for access check / routing / remap (design decision C). The
apiName→physical rewrite happens inside the service/compiler, never in the
route. Literal values travel as a logical SQL string (the compiler
re-extracts them into bound params internally).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ontology.config.container import Container
from ontology.config.settings import settings
from ontology.main import app

_full_only = pytest.mark.skipif(
    settings.edition == "lite",
    reason="cloud-only: 路由依赖触发 Gravitino catalog（lite 抛 EditionUnavailableError）",
)


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
    with patch("ontology.routes.query.container", container):
        yield TestClient(app)


def test_textsql_forwards_logical_sql_to_service(client, container) -> None:
    """Route forwards (ontology, logical_sql) verbatim — no object_type arg."""
    from ontology.routes.query import get_query_service

    svc = AsyncMock()
    svc.execute_compiled_sql = AsyncMock(return_value=[{"count": 7}])
    svc.aclose = AsyncMock()
    app.dependency_overrides[get_query_service] = _yield_service(svc)
    try:
        resp = client.post(
            "/objects/textsql",
            json={
                "ontology_api_name": "Marketing",
                "logical_sql": (
                    "SELECT COUNT(*) AS count FROM ManualOutboundCall moc "
                    "JOIN Lead l ON l.leadId = moc.leadId "
                    "WHERE sc.phone = '17838371975'"
                ),
            },
        )
    finally:
        app.dependency_overrides.pop(get_query_service, None)
    assert resp.status_code == 200, resp.text
    assert resp.json() == [{"count": 7}]
    # The route forwards the logical SQL unchanged; no object_type arg is
    # passed (the service infers all OTs from the SQL). Two positional args.
    svc.execute_compiled_sql.assert_awaited_once()
    args = svc.execute_compiled_sql.call_args.args
    assert args[0] == "Marketing"
    assert "ManualOutboundCall" in args[1]
    assert "leadId" in args[1]


@_full_only
def test_textsql_requires_ontology_field(client) -> None:
    """Missing ontology_api_name → pydantic 422, service not called."""
    resp = client.post(
        "/objects/textsql",
        json={"logical_sql": "SELECT 1"},
    )
    assert resp.status_code == 422


@_full_only
def test_textsql_requires_logical_sql_field(client) -> None:
    """Missing logical_sql → pydantic 422."""
    resp = client.post(
        "/objects/textsql",
        json={"ontology_api_name": "Marketing"},
    )
    assert resp.status_code == 422


# ── helpers ──────────────────────────────────────────────────────────────


def _yield_service(svc):
    """Build an async generator dependency that yields ``svc`` then closes it."""

    async def _dep():
        try:
            yield svc
        finally:
            await svc.aclose()

    return _dep
