"""Integration tests for GeoTimeStore against real PostgreSQL (PostGIS + TimescaleDB).

Requires the one-image PG (ngosang/timescaledb-postgis) running with both
extensions activated. These tests exercise real PostGIS spatial functions
and TimescaleDB hypertable creation — the highest-value verification per
the project's "use real DB to test writes" rule.

Uses a session-scoped event loop + dedicated engine to avoid the asyncpg
"Event loop is closed" issue when reusing a module-level engine across
pytest-asyncio function-scoped loops.

Run: .venv/bin/python -m pytest tests/integration/test_geotime_store.py -v
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from ontology.config.settings import settings
from ontology.core.schemas.geotime import SpatialFilter
from ontology.layers.geotime.geotime_store import GeoTimeStore

# Session-scoped loop so the dedicated engine's connection pool isn't torn
# down between function-scoped tests (avoids asyncpg Event loop is closed).
pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def pg_engine():
    """Session-scoped dedicated PG engine for GeoTimeStore tests."""
    eng = create_async_engine(settings.pg_dsn, echo=False, pool_pre_ping=True)
    yield eng
    await eng.dispose()


@pytest.fixture
def store(pg_engine) -> GeoTimeStore:
    return GeoTimeStore(engine=pg_engine)


class TestCreateGeoTable:
    async def test_creates_table_with_gist_index(self, store: GeoTimeStore):
        table = await store.create_geo_table("GeoTest", "Supplier", "GEOPOINT", indexed_fields=["status"])
        try:
            assert await store.table_exists(table)
            # GiST index exists.
            from sqlalchemy import text

            from ontology.config.database import engine

            async with engine.connect() as conn:
                res = await conn.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE tablename = :t "
                        "AND indexname LIKE '%_gist'"
                    ),
                    {"t": table},
                )
                indexes = [r[0] for r in res]
            assert any("gist" in i for i in indexes)
        finally:
            await store.drop_table(table)

    async def test_geoshape_uses_geometry_column(self, store: GeoTimeStore):
        table = await store.create_geo_table("GeoTest", "Region", "GEOSHAPE")
        try:
            assert await store.table_exists(table)
        finally:
            await store.drop_table(table)


class TestUpsertGeo:
    async def test_upsert_and_spatial_filter(self, store: GeoTimeStore):
        table = await store.create_geo_table("GeoTest", "Supplier", "GEOPOINT", indexed_fields=["status"])
        try:
            # 写入 3 个点：北京、上海、广州。
            await store.upsert_geo(table, "v1", "Supplier", "S1", "POINT(116.4 39.9)", {"status": "ACTIVE"})
            await store.upsert_geo(table, "v2", "Supplier", "S2", "POINT(121.5 31.2)", {"status": "ACTIVE"})
            await store.upsert_geo(table, "v3", "Supplier", "S3", "POINT(113.3 23.1)", {"status": "INACTIVE"})

            # withinDistance: 北京 1000km 内（应含北京、上海约 1067km 略外、广州远）。
            # 1000km 内只有北京本身附近。用 1200km 应含北京+上海。
            hits = await store.spatial_filter(
                table, ["v1", "v2", "v3"],
                SpatialFilter(op="withinDistance", center=[116.4, 39.9], max_distance=1_200_000),
            )
            assert "v1" in hits  # 北京本身
            assert "v2" in hits  # 上海 ~1067km < 1200km
            assert "v3" not in hits  # 广州 ~1880km

            # withinPolygon: 多边形包含北京+上海，不含广州。
            polygon = [
                [115.0, 38.0], [122.0, 38.0], [122.0, 40.0], [115.0, 40.0]
            ]
            hits_poly = await store.spatial_filter(
                table, ["v1", "v2", "v3"],
                SpatialFilter(op="withinPolygon", coords=polygon),
            )
            assert "v1" in hits_poly  # 北京在多边形内
            assert "v3" not in hits_poly  # 广州不在
        finally:
            await store.drop_table(table)

    async def test_upsert_idempotent(self, store: GeoTimeStore):
        table = await store.create_geo_table("GeoTest", "Item", "GEOPOINT")
        try:
            await store.upsert_geo(table, "v1", "Item", "I1", "POINT(0 0)")
            await store.upsert_geo(table, "v1", "Item", "I1", "POINT(0 0)")  # 重复
            # 仍只有 1 行。
            from sqlalchemy import text

            from ontology.config.database import engine

            async with engine.connect() as conn:
                res = await conn.execute(text(f"SELECT count(*) FROM {table}"))
                assert res.scalar() == 1
        finally:
            await store.drop_table(table)


class TestTimeseriesHypertable:
    async def test_creates_hypertable_with_indices(self, store: GeoTimeStore):
        table = await store.create_timeseries_hypertable(
            "GeoTest", "Vehicle", "track", has_position=True, metric_fields=["speed"]
        )
        try:
            assert await store.table_exists(table)
            # 是超表（timescaledb_information.hypertables 有记录）。
            from sqlalchemy import text

            from ontology.config.database import engine

            async with engine.connect() as conn:
                res = await conn.execute(
                    text(
                        "SELECT count(*) FROM timescaledb_information.hypertables "
                        "WHERE hypertable_name = :t"
                    ),
                    {"t": table},
                )
                assert res.scalar() == 1
        finally:
            await store.drop_table(table)

    async def test_append_series_and_query(self, store: GeoTimeStore):
        from datetime import UTC, datetime

        table = await store.create_timeseries_hypertable(
            "GeoTest", "Vehicle", "track", has_position=True, metric_fields=["speed"]
        )
        try:
            rows = [
                {
                    "series_id": "veh-1",
                    "timestamp": datetime(2026, 7, 1, 10, tzinfo=UTC),
                    "location": [116.4, 39.9],
                    "speed": 60.0,
                },
                {
                    "series_id": "veh-1",
                    "timestamp": datetime(2026, 7, 1, 11, tzinfo=UTC),
                    "location": [116.5, 39.9],
                    "speed": 65.0,
                },
            ]
            count = await store.append_series(table, rows)
            assert count == 2

            # 时序查询：series_id + 时间窗口。
            results = await store.series_query(
                table, ["veh-1"],
                time_range=(datetime(2026, 7, 1, 9, tzinfo=UTC), datetime(2026, 7, 1, 12, tzinfo=UTC)),
            )
            assert len(results) == 2
        finally:
            await store.drop_table(table)


class TestAttrFilterPg:
    """Integration test: DataFrameQueryService 属性过滤走 PG JSONB SQL（M3 优化）。"""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_attr_filter_exact_match_pg(self, pg_engine):
        from unittest.mock import AsyncMock, MagicMock

        # 灌测试数据：自包含建临时 ontology + object_state，测试后清理。
        from sqlalchemy import text

        from ontology.core.schemas.object_set import Filter
        from ontology.services.object_set_executor import DataFrameQueryService
        ont_id = "attrfilterontid"
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM ontologies WHERE id = :oid"), {"oid": ont_id})
            await conn.execute(text(
                "INSERT INTO ontologies (id, api_name, display_name, description, rid, status, created_at, updated_at) "
                "VALUES (:oid, 'AttrFilterTest', 't', '', '', 'ACTIVE', NOW(), NOW())"
            ), {"oid": ont_id})
            await conn.execute(text("DELETE FROM object_state WHERE object_id LIKE 'attrtest%'"))
            await conn.execute(
                text(
                    "INSERT INTO object_state "
                    "(object_id, object_type_api_name, ontology_id, version, properties, "
                    "modified_by, created_at, updated_at) "
                    "VALUES (:id1, 'T', :ont, 1, :p1, 'system', NOW(), NOW()),"
                    "(:id2, 'T', :ont, 1, :p2, 'system', NOW(), NOW())"
                ),
                {"id1": "attrtest1", "id2": "attrtest2", "ont": ont_id,
                 "p1": '{"status":"ACTIVE","amt":50}', "p2": '{"status":"INACTIVE","amt":150}'},
            )

        meta = AsyncMock()
        meta.get_ontology = AsyncMock(return_value=MagicMock(id=ont_id))
        meta.session = MagicMock()
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=meta, attr_engine=pg_engine
        )
        from ontology.services.object_set_executor import EvidenceChain
        ev = EvidenceChain()
        # exactMatch status=ACTIVE → attrtest1
        vids = await svc._attr_filter_pg(
            Filter(field="status", op="exactMatch", value="ACTIVE"),
            ["attrtest1", "attrtest2"], ev,
        )
        assert vids == ["attrtest1"]
        # range amt 100-200 → attrtest2
        vids = await svc._attr_filter_pg(
            Filter(field="amt", op="range", value={"min": 100, "max": 200}),
            ["attrtest1", "attrtest2"], ev,
        )
        assert vids == ["attrtest2"]

        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM object_state WHERE object_id LIKE 'attrtest%'"))
            await conn.execute(text("DELETE FROM ontologies WHERE id = :oid"), {"oid": ont_id})
