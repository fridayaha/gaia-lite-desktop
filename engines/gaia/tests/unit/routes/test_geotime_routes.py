"""Phase 2b 路由测试：spatial-filter + series-query（geotime REST 入口）。"""

from unittest.mock import AsyncMock

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
    container.service_overrides.pop("geotime_store", None)


def test_spatial_filter_returns_matched_rids(client: TestClient) -> None:
    """框选过滤：GeoTimeStore.spatial_filter 返回命中 rid 子集。"""
    mock_store = AsyncMock()
    mock_store.table_exists = AsyncMock(return_value=True)
    mock_store.spatial_filter = AsyncMock(return_value=["S001", "S003"])
    container.service_overrides["geotime_store"] = mock_store  # type: ignore[index]

    resp = client.post(
        "/objects/ChainSmoke/spatial-filter",
        json={
            "object_type": "Supplier",
            "candidate_rids": ["S001", "S002", "S003", "S004"],
            "op": "withinBoundingBox",
            "bbox": [[120, 30], [121, 31]],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == ["S001", "S003"]
    mock_store.spatial_filter.assert_awaited_once()
    args = mock_store.spatial_filter.call_args
    assert args.args[1] == ["S001", "S002", "S003", "S004"]


def test_spatial_filter_table_missing_returns_empty(client: TestClient) -> None:
    """对象类型未投影空间表时返回空列表（不报错）。"""
    mock_store = AsyncMock()
    mock_store.table_exists = AsyncMock(return_value=False)
    container.service_overrides["geotime_store"] = mock_store  # type: ignore[index]

    resp = client.post(
        "/objects/ChainSmoke/spatial-filter",
        json={
            "object_type": "Supplier",
            "candidate_rids": ["S001"],
            "op": "withinDistance",
            "center": [120, 30],
            "max_distance": 5000,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
    mock_store.spatial_filter.assert_not_awaited()


def test_series_query_returns_rows(client: TestClient) -> None:
    """轨迹回放：series_query 返回时序点列表。"""
    mock_store = AsyncMock()
    mock_store.table_exists = AsyncMock(return_value=True)
    mock_store.series_query = AsyncMock(
        return_value=[
            {"series_id": "O1", "timestamp": "2026-07-02T10:00:00", "location": "(120,30)"},
            {"series_id": "O1", "timestamp": "2026-07-02T10:05:00", "location": "(121,31)"},
        ]
    )
    container.service_overrides["geotime_store"] = mock_store  # type: ignore[index]

    resp = client.post(
        "/objects/ChainSmoke/series-query",
        json={
            "object_type": "Order",
            "series_property": "track",
            "series_ids": ["O1"],
            "time_start": "2026-07-02T00:00:00",
            "time_end": "2026-07-02T23:59:59",
            "limit": 100,
        },
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]["series_id"] == "O1"


def test_series_query_table_missing_returns_empty(client: TestClient) -> None:
    """无时序表时返回空列表。"""
    mock_store = AsyncMock()
    mock_store.table_exists = AsyncMock(return_value=False)
    container.service_overrides["geotime_store"] = mock_store  # type: ignore[index]

    resp = client.post(
        "/objects/ChainSmoke/series-query",
        json={"object_type": "Order", "series_property": "track", "series_ids": ["O1"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
