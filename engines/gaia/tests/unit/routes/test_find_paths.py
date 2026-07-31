"""Phase 2d find_paths 测试：Neo4jGraphStore.find_paths + logic + route。"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from ontology.config.container import container
from ontology.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def reset_overrides() -> None:
    yield
    container.service_overrides.pop("graph_store", None)
    container.service_overrides.pop("dataframe_query_service", None)


async def test_find_paths_store_returns_vid_sequences() -> None:
    """Neo4jGraphStore.find_paths 调 allShortestPaths Cypher，返回 vid 路径。"""
    from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore

    store = Neo4jGraphStore()
    # mock _run 返回 records
    fake_result = AsyncMock()
    fake_result.records = [
        {"path": ["S001", "O1", "C001"]},
        {"path": ["S001", "O2", "C001"]},
    ]
    mock_run = AsyncMock(return_value=fake_result)
    with patch.object(store, "_run", mock_run):
        paths = await store.find_paths("S001", "C001", max_depth=3, limit=10)

    assert paths == [["S001", "O1", "C001"], ["S001", "O2", "C001"]]
    # 验证 Cypher 含 allShortestPaths
    cypher = mock_run.call_args.args[0]
    assert "allShortestPaths" in cypher
    assert "1..3" in cypher


async def test_find_paths_with_rel_types() -> None:
    """link_types 限定时 Cypher 含关系类型。"""
    from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore

    store = Neo4jGraphStore()
    fake_result = AsyncMock()
    fake_result.records = [{"path": ["A", "B"]}]
    mock_run = AsyncMock(return_value=fake_result)
    with patch.object(store, "_run", mock_run):
        await store.find_paths("A", "B", rel_types=["ChainSmokeSupplies"])

    cypher = mock_run.call_args.args[0]
    assert ":ChainSmokeSupplies" in cypher


async def test_find_paths_no_connection_returns_empty() -> None:
    """无连接时返回空列表。"""
    from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore

    store = Neo4jGraphStore()
    fake_result = AsyncMock()
    fake_result.records = []
    with patch.object(store, "_run", AsyncMock(return_value=fake_result)):
        paths = await store.find_paths("X", "Y")

    assert paths == []


def test_find_paths_route(client: TestClient) -> None:
    """POST /objects/{ont}/find-paths 路由调 find_paths_logic。

    find_paths_logic 在调 graph_store 之前会先用 dataframe_query_service 把
    业务主键解析成 vid（查 PG object_state）。这里同时 mock 两个服务，避免
    route 测试依赖真实 PG/Neo4j。
    """
    mock_store = AsyncMock()
    mock_store.find_paths = AsyncMock(return_value=[["S001", "O1", "C001"]])
    container.service_overrides["graph_store"] = mock_store  # type: ignore[index]

    # 请求未传 link_types → find_paths_logic 走 _resolve_vid_by_pk_any_type
    # 跨类型扫描分支，mock 成固定 vid 即可绕过 PG。
    mock_dfs = AsyncMock()
    mock_dfs._resolve_vid_by_pk_any_type = AsyncMock(side_effect=lambda ont, pk: pk)
    container.service_overrides["dataframe_query_service"] = mock_dfs  # type: ignore[index]

    resp = client.post(
        "/objects/ChainSmoke/find-paths",
        json={"source_key": "S001", "target_key": "C001", "max_depth": 4},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 1
    assert data["paths"] == [["S001", "O1", "C001"]]
    assert data["source"] == "S001"
    mock_store.find_paths.assert_awaited_once()


def test_find_paths_route_rejects_unknown_field_with_422(client: TestClient) -> None:
    """The route uses a pydantic FindPathsRequest model, so a typo in a field
    name (e.g. ``source`` instead of ``source_key``) must surface as a 422
    validation error — NOT a 500 KeyError from a bare ``dict["source_key"]``
    access. This is the contract that lets external integrators trust the
    OpenAPI schema."""
    resp = client.post(
        "/objects/ChainSmoke/find-paths",
        json={"source": "S001", "target_key": "C001"},  # typo: source→source_key
    )
    assert resp.status_code == 422


def test_traverse_route_rejects_unknown_field_with_422(client: TestClient) -> None:
    """Same contract for /traverse: bare-dict era returned 500 on a missing
    ``link_type``; the TraverseRequest model now returns 422 with a clear
    location pointer."""
    resp = client.post(
        "/objects/ChainSmoke/traverse",
        json={"link": "hasOrder", "source_keys": ["S001"]},  # typo: link→link_type
    )
    assert resp.status_code == 422


def test_action_validate_route_returns_valid_result(client: TestClient) -> None:
    """POST /actions/validate/{ont}/{ot}/{action} is the REST parity of the
    MCP/AG-UI validate_action tool. It reuses validate_action_logic so all
    three entry points agree on what "valid" means, and returns
    {"valid": bool, "errors": [...]} (not the heavier ActionPreviewResult
    that /preview returns)."""
    from unittest.mock import AsyncMock, patch

    with patch(
        "ontology.tools.toolsets.action.validate_action_logic",
        new=AsyncMock(return_value={"valid": True, "errors": []}),
    ) as mock_logic:
        resp = client.post(
            "/actions/validate/Marketing/Order/cancel_order",
            json={"parameters": {"order_no": "PO-001"}},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data == {"valid": True, "errors": []}
    mock_logic.assert_awaited_once()
    # Shares the same logic fn as the MCP/AG-UI tool — single source of truth.
    assert mock_logic.call_args.args[1:4] == ("Marketing", "Order", "cancel_order")
    assert mock_logic.call_args.args[4] == {"order_no": "PO-001"}
