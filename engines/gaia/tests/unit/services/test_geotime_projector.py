"""Unit tests for GeoTimeProjector (graph-reasoning-design.md §6.2)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ontology.core.schemas.ontology import DataType, ObjectType, PropertyDef
from ontology.services.geotime_projector import GeoTimeProjector


@pytest.fixture
def mock_geotime_store() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def projector(mock_metadata, mock_geotime_store) -> GeoTimeProjector:
    return GeoTimeProjector(metadata=mock_metadata, geotime_store=mock_geotime_store)


_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _make_ot(properties: list[PropertyDef], api_name: str = "Supplier") -> ObjectType:
    return ObjectType(
        id="ot1", ontology_id="o1", api_name=api_name, display_name=api_name,
        primary_key="id", title_property="name", storage_type="MANAGED",
        properties=properties, created_at=_TS, updated_at=_TS,
    )


def _prop(name: str, dt: DataType, indexed: bool = False) -> PropertyDef:
    return PropertyDef(
        id=f"p_{name}", object_type_id="ot1", api_name=name, display_name=name,
        data_type=dt, indexed=indexed, created_at=_TS, updated_at=_TS,
    )


class TestProjectObject:
    async def test_non_spatial_object_skipped(self, projector, mock_metadata, mock_geotime_store):
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot([_prop("status", DataType.STRING)])
        )
        await projector.project_object("SC", "Supplier", {"rid": "v1", "properties": {}})
        mock_geotime_store.upsert_geo.assert_not_awaited()

    async def test_geopoint_projected(self, projector, mock_metadata, mock_geotime_store):
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot([
                _prop("location", DataType.GEOPOINT),
                _prop("status", DataType.STRING, indexed=True),
            ])
        )
        await projector.project_object(
            "SC", "Supplier",
            {"rid": "v1", "properties": {"location": [116.4, 39.9], "status": "ACTIVE", "id": "S1"}},
        )
        mock_geotime_store.upsert_geo.assert_awaited_once()
        args = mock_geotime_store.upsert_geo.call_args.args
        table, rid, api_name, pk_value, wkt, props, geom_column = args
        assert table == "geo_sc__supplier"
        assert rid == "v1"
        assert "POINT(116.4 39.9)" in wkt
        assert geom_column == "location"
        assert props == {"status": "ACTIVE"}

    async def test_no_geometry_value_skips(self, projector, mock_metadata, mock_geotime_store):
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot([_prop("location", DataType.GEOPOINT)])
        )
        await projector.project_object("SC", "Supplier", {"rid": "v1", "properties": {}})
        mock_geotime_store.upsert_geo.assert_not_awaited()

    async def test_geojson_point_converted(self, projector, mock_metadata, mock_geotime_store):
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot([_prop("location", DataType.GEOPOINT)])
        )
        await projector.project_object(
            "SC", "Supplier",
            {"rid": "v1", "properties": {"location": {"type": "Point", "coordinates": [121.5, 31.2]}}},
        )
        wkt = mock_geotime_store.upsert_geo.call_args.args[4]
        assert "POINT(121.5 31.2)" in wkt

    async def test_geoshape_uses_geometry_column(self, projector, mock_metadata, mock_geotime_store):
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot([_prop("boundary", DataType.GEOSHAPE)])
        )
        await projector.project_object(
            "SC", "Region",
            {
                "rid": "v1",
                "properties": {
                    "boundary": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}
                },
            },
        )
        geom_column = mock_geotime_store.upsert_geo.call_args.args[6]
        assert geom_column == "geometry"


class TestToWkt:
    def test_list_coords(self):
        assert GeoTimeProjector._to_wkt([116.4, 39.9], _prop("x", DataType.GEOPOINT)) == "POINT(116.4 39.9)"

    def test_wkt_string_passthrough(self):
        assert GeoTimeProjector._to_wkt("POINT(0 0)", _prop("x", DataType.GEOPOINT)) == "POINT(0 0)"

    def test_unsupported_returns_none(self):
        assert GeoTimeProjector._to_wkt(123, _prop("x", DataType.GEOPOINT)) is None

    def test_lon_lat_dict(self):
        """{"lon": x, "lat": y} format (SeaTunnel-written Iceberg data)."""
        assert GeoTimeProjector._to_wkt(
            {"lon": 116.4, "lat": 39.9}, _prop("loc", DataType.GEOPOINT)
        ) == "POINT(116.4 39.9)"

    def test_longitude_latitude_dict(self):
        """{"longitude": x, "latitude": y} variant."""
        assert GeoTimeProjector._to_wkt(
            {"longitude": 121.5, "latitude": 31.2}, _prop("loc", DataType.GEOPOINT)
        ) == "POINT(121.5 31.2)"

    def test_lon_lat_json_string(self):
        """JSON-encoded '{"lon":...,"lat":...}' string (Iceberg string column)."""
        assert GeoTimeProjector._to_wkt(
            '{"lon": 114.06, "lat": 22.27}', _prop("loc", DataType.GEOPOINT)
        ) == "POINT(114.06 22.27)"

    def test_geojson_point_still_works(self):
        """GeoJSON Point format (regression — pre-existing path)."""
        assert GeoTimeProjector._to_wkt(
            {"type": "Point", "coordinates": [116.4, 39.9]}, _prop("loc", DataType.GEOPOINT)
        ) == "POINT(116.4 39.9)"

    def test_geojson_polygon_still_works(self):
        """GeoJSON Polygon format (regression)."""
        wkt = GeoTimeProjector._to_wkt(
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
            _prop("b", DataType.GEOSHAPE),
        )
        assert wkt is not None
        assert wkt.startswith("POLYGON((")
        assert wkt.endswith("))")

    def test_geojson_polygon_unclosed_ring_gets_closed(self):
        """未闭合环（首 != 末）补首点闭合。"""
        wkt = GeoTimeProjector._to_wkt(
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]},
            _prop("b", DataType.GEOSHAPE),
        )
        assert wkt == "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"

    def test_geojson_polygon_closed_ring_not_double_closed(self):
        """RFC 7946 已闭合环（首 == 末）不再追加重复首点，避免退化零长末段被 PostGIS 拒收。"""
        wkt = GeoTimeProjector._to_wkt(
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            _prop("b", DataType.GEOSHAPE),
        )
        assert wkt == "POLYGON((0 0, 1 0, 1 1, 0 0))"

    def test_invalid_json_string_falls_back_to_raw(self):
        """A string starting with '{' but not valid JSON is returned as-is (WKT)."""
        # Not valid JSON, not WKT, but should pass through unchanged.
        assert GeoTimeProjector._to_wkt(
            "{not json", _prop("loc", DataType.GEOPOINT)
        ) == "{not json"

    def test_empty_dict_returns_none(self):
        assert GeoTimeProjector._to_wkt({}, _prop("loc", DataType.GEOPOINT)) is None
