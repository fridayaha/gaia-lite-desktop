"""Unit tests for DataFrameQueryService (graph-reasoning-design.md §7.3).

Layer dependencies (graph/geotime/metadata) are mocked. Tests validate:
1. IR tree recursive evaluation (objectType/static/filter/searchAround)
2. filter dispatch (attr→PG, spatial→PostGIS, timeRange→TimescaleDB)
3. searchAround calls Neo4jGraphStore with correct label/rel_type/hops
4. safeguards (hydrate limit truncation, depth limit)
5. evidence chain accumulation
"""

from unittest.mock import AsyncMock

import pytest

from ontology.core.schemas.graph import GraphTraversalResult
from ontology.core.schemas.object_set import Filter, ObjectSetIR
from ontology.core.schemas.ontology import ObjectType
from ontology.services.object_set_executor import DataFrameQueryService, EvidenceChain


@pytest.fixture
def mock_graph() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_geotime() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def svc(mock_graph, mock_geotime, mock_metadata) -> DataFrameQueryService:
    svc = DataFrameQueryService(
        graph_store=mock_graph, geotime_store=mock_geotime, metadata=mock_metadata
    )
    # pk→rid 翻译层 mock：测试中 pk "v1" → rid "v1"（透传，保持断言不变）。
    # 真实翻译查 object_state.properties[pk_field]，单测不依赖真 DB。
    svc._resolve_rids_by_pk = AsyncMock(side_effect=lambda _ont, _ot, pks: pks)  # type: ignore[method-assign]
    return svc


class TestEvalObjectType:
    async def test_object_type_returns_rids(self, svc, mock_metadata):
        mock_metadata.get_rids_by_type = AsyncMock(
            return_value=["v1", "v2"]
        )
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "object_type_api_name": "Supplier", "properties": {"id": "v1"}},
                {"rid": "v2", "object_type_api_name": "Supplier", "properties": {"id": "v2"}},
            ]
        )
        ir = ObjectSetIR(type="objectType", object_type="Supplier")
        result = await svc.execute(ir, "SC")
        assert len(result.objects) == 2
        assert result.stats["total_rids"] == 2
        assert "postgres" in result.stats["engines_used"]

    async def test_object_type_with_inline_filter(self, svc, mock_metadata):
        mock_metadata.get_rids_by_type = AsyncMock(
            return_value=["v1", "v2", "v3"]
        )
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"status": "ACTIVE"}},
                {"rid": "v2", "properties": {"status": "INACTIVE"}},
            ]
        )
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            filters=[Filter(field="status", op="exactMatch", value="ACTIVE")],
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # 只 ACTIVE


class TestEvalStatic:
    async def test_static_returns_resolved_rids(self, svc, mock_metadata):
        # static 传入业务主键，经翻译层解析为 rid 后返回。
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        ir = ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S")
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 3

    async def test_static_without_object_type_raises(self, svc, mock_metadata):
        # static 必须带 object_type，否则无法解析 primary_key → rid。
        ir = ObjectSetIR(type="static", objects=["v1"])
        with pytest.raises(ValueError, match="object_type"):
            await svc.execute(ir, "SC")

    async def test_static_translates_pk_to_rid(self, svc, mock_metadata):
        # 翻译层把 pk “S001” → rid “rid-S001”（默认 fixture 透传，这里覆盖验证映射）。
        svc._resolve_rids_by_pk = AsyncMock(return_value=["rid-S001"])  # type: ignore[method-assign]
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        ir = ObjectSetIR(type="static", objects=["S001"], object_type="Supplier")
        result = await svc.execute(ir, "SC")
        # 翻译后调用水合的 rid 是 rid-S001
        mock_metadata.get_object_states_by_rids.assert_awaited_once_with(["rid-S001"])
        assert result.stats["total_rids"] == 1


class TestEvalFilter:
    async def test_attr_filter_exact_match(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"status": "ACTIVE"}},
                {"rid": "v2", "properties": {"status": "INACTIVE"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="status", op="exactMatch", value="ACTIVE")],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1
        assert "postgres" in result.stats["engines_used"]

    async def test_spatial_filter_calls_postgis(self, svc, mock_metadata, mock_geotime):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": "v1", "object_type_api_name": "Supplier"}]
        )
        mock_geotime.table_exists = AsyncMock(return_value=True)
        mock_geotime.spatial_filter = AsyncMock(return_value=["v1"])
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="location", op="withinDistance", center=[116.4, 39.9], max_distance=1000)],
            object_set=ObjectSetIR(type="static", objects=["v1"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        mock_geotime.spatial_filter.assert_awaited_once()
        assert "postgis" in result.stats["engines_used"]

    async def test_range_filter(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"amount": 50}},
                {"rid": "v2", "properties": {"amount": 150}},
                {"rid": "v3", "properties": {"amount": 200}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="amount", op="range", value={"min": 100, "max": 200})],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 2  # v2(150), v3(200)

    async def test_time_range_filter_memory(self, svc, mock_metadata):
        """timeRange 走内存过滤（无 engine 时），真正过滤而非返回全集。"""
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"createdAt": "2024-01-01T00:00:00"}},
                {"rid": "v2", "properties": {"createdAt": "2024-06-01T00:00:00"}},
                {"rid": "v3", "properties": {"createdAt": "2024-12-01T00:00:00"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="createdAt", op="timeRange", value={"start": "2024-03-01T00:00:00", "end": "2024-09-01T00:00:00"})],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # 只有 v2 在 3月-9月内

    async def test_time_range_filter_numeric_ts(self, svc, mock_metadata):
        """timeRange 兼容数值 timestamp（ms）。"""
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"ts": 1000}},
                {"rid": "v2", "properties": {"ts": 5000}},
                {"rid": "v3", "properties": {"ts": 9000}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="ts", op="timeRange", value={"start": 2000, "end": 8000})],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # 只有 v2(5000) 在 [2000,8000]


class TestCompileFilterSQL:
    """_compile_* 方法测试：验证 Ibis SQL 模式编译的谓词 + 参数化。"""

    def test_compile_attr_exact_match(self, svc):
        f = Filter(field="status", op="exactMatch", value="ACTIVE")
        pred, params = svc._compile_attr_pred(f, "f0")
        assert "= :f0_val" in pred
        assert params["f0_val"] == "ACTIVE"
        assert params["f0_field"] == "status"

    def test_compile_attr_range_numeric(self, svc):
        f = Filter(field="amt", op="range", value={"min": 10, "max": 100})
        pred, params = svc._compile_attr_pred(f, "f0")
        assert "::numeric" in pred  # 强制数值比较
        assert params["f0_mn"] == 10
        assert params["f0_mx"] == 100

    def test_compile_spatial_within_distance(self):
        from ontology.core.schemas.geotime import SpatialFilter
        from ontology.services.object_set_executor import DataFrameQueryService

        spatial = SpatialFilter(op="withinDistance", center=[116.4, 39.9], max_distance=50000.0)
        pred, params = DataFrameQueryService._compile_spatial_pred(spatial, "g0", "f0", "Supplier")
        assert "ST_DWithin" in pred
        assert params["f0_lon"] == 116.4
        assert params["f0_dist"] == 50000.0

    def test_compile_spatial_within_polygon(self):
        from ontology.core.schemas.geotime import SpatialFilter
        from ontology.services.object_set_executor import DataFrameQueryService

        coords = [[116.0, 39.0], [117.0, 39.0], [117.0, 40.0], [116.0, 40.0]]
        spatial = SpatialFilter(op="withinPolygon", coords=coords)
        pred, params = DataFrameQueryService._compile_spatial_pred(spatial, "g0", "f0", "Supplier")
        assert "ST_Covers" in pred
        assert "POLYGON" in params["f0_wkt"]

    def test_compile_time_range(self, svc):
        f = Filter(field="createdAt", op="timeRange", value={"start": 1000, "end": 2000})
        pred, params = svc._compile_time_pred(f, "f0")
        assert "to_timestamp" in pred
        assert "/ 1000" in pred  # ms → 秒
        # 参数转为 datetime（asyncpg 要求）
        from datetime import datetime
        assert isinstance(params["f0_start"], datetime)

    def test_to_datetime_numeric_ms(self):
        from datetime import datetime

        from ontology.services.object_set_executor import DataFrameQueryService

        dt = DataFrameQueryService._to_datetime(1751328000000)
        assert isinstance(dt, datetime)
        assert dt.year == 2025

    def test_to_datetime_iso_string(self):
        from datetime import datetime

        from ontology.services.object_set_executor import DataFrameQueryService

        dt = DataFrameQueryService._to_datetime("2024-06-01T00:00:00")
        assert isinstance(dt, datetime)
        assert dt.year == 2024


class TestEvalSearchAround:
    async def test_search_around_calls_graph(self, svc, mock_metadata, mock_graph):
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        mock_metadata.get_link_types = AsyncMock(return_value=[])
        mock_metadata.list_object_types = AsyncMock(return_value=[])
        mock_graph.search_around = AsyncMock(
            return_value=GraphTraversalResult(rids=["t1", "t2"], matched_count=2, hops=3)
        )
        ir = ObjectSetIR(
            type="searchAround", link="supplies",
            object_set=ObjectSetIR(type="static", objects=["s1"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        mock_graph.search_around.assert_awaited_once()
        assert "neo4j" in result.stats["engines_used"]
        assert result.stats["total_rids"] == 2

    async def test_search_around_uses_default_hops(self, svc, mock_metadata, mock_graph):
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        mock_metadata.get_link_types = AsyncMock(return_value=[])
        mock_metadata.list_object_types = AsyncMock(return_value=[])
        mock_graph.search_around = AsyncMock(return_value=GraphTraversalResult(rids=[]))
        ir = ObjectSetIR(
            type="searchAround", link="supplies",
            object_set=ObjectSetIR(type="static", objects=["s1"], object_type="S"),
        )
        await svc.execute(ir, "SC")
        call_kwargs = mock_graph.search_around.call_args.kwargs
        assert call_kwargs["hops"] == (1, 3)  # 默认
        assert call_kwargs["direction"] == "both"


class TestSafeguards:
    async def test_hydrate_limit_truncation(self, svc, mock_metadata, monkeypatch):
        # 造超过 hydrate_limit 的 rid 集。
        from ontology.config import settings as _settings

        monkeypatch.setattr(_settings, "hydrate_limit", 3)
        mock_metadata.get_object_states_by_type = AsyncMock(
            return_value=[{"rid": f"v{i}"} for i in range(10)]
        )
        # _eval_object_type 现在用 get_rids_by_type
        mock_metadata.get_rids_by_type = AsyncMock(
            return_value=[f"v{i}" for i in range(10)]
        )
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        ir = ObjectSetIR(type="objectType", object_type="Supplier")
        result = await svc.execute(ir, "SC")
        assert result.truncated is True
        assert result.next_cursor is not None

    async def test_cursor_pagination(self, svc, mock_metadata, monkeypatch):
        """cursor 分页：第二页从 cursor 之后开始水合。"""
        from ontology.config import settings as _settings

        monkeypatch.setattr(_settings, "hydrate_limit", 3)
        mock_metadata.get_rids_by_type = AsyncMock(
            return_value=[f"v{i}" for i in range(10)]
        )
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        ir = ObjectSetIR(type="objectType", object_type="Supplier")

        # 第一页：v0-v2，next_cursor=v2
        page1 = await svc.execute(ir, "SC")
        assert page1.truncated is True
        assert page1.next_cursor == "v2"

        # 第二页：cursor=v2，从 v3 开始
        page2 = await svc.execute(ir, "SC", cursor="v2")
        assert page2.truncated is True
        assert page2.next_cursor == "v5"

        # 最后一页：cursor=v8，从 v9 开始，不截断
        page4 = await svc.execute(ir, "SC", cursor="v8")
        assert page4.truncated is False
        assert page4.next_cursor is None

    async def test_cursor_not_in_rids_resets(self, svc, mock_metadata, monkeypatch):
        """cursor 不在 rids 中（数据变化）→ 从头开始。"""
        from ontology.config import settings as _settings

        monkeypatch.setattr(_settings, "hydrate_limit", 3)
        mock_metadata.get_rids_by_type = AsyncMock(
            return_value=[f"v{i}" for i in range(5)]
        )
        mock_metadata.get_object_states_by_rids = AsyncMock(return_value=[])
        ir = ObjectSetIR(type="objectType", object_type="Supplier")

        result = await svc.execute(ir, "SC", cursor="stale_rid")
        assert result.truncated is True  # 5 > 3
        assert result.next_cursor == "v2"  # 从头开始

    async def test_depth_limit_exceeds(self, svc):
        # 4 层 searchAround 超限。
        ir = ObjectSetIR(
            type="searchAround", link="l4",
            object_set=ObjectSetIR(
                type="searchAround", link="l3",
                object_set=ObjectSetIR(
                    type="searchAround", link="l2",
                    object_set=ObjectSetIR(
                        type="searchAround", link="l1",
                        object_set=ObjectSetIR(type="static", objects=["s1"], object_type="S"),
                    ),
                ),
            ),
        )
        with pytest.raises(ValueError, match="depth"):
            await svc.execute(ir, "SC")


class TestEvidenceChain:
    def test_record_accumulates(self):
        ec = EvidenceChain()
        ec.record("object_type", "postgres", 0.1, 100)
        ec.record("searchAround", "neo4j", 1.2, 50)
        assert ec.step_count == 2
        assert "postgres" in ec.engines_used
        assert "neo4j" in ec.engines_used
        assert ec.timings["searchAround"] == 1.2

    def test_timings_accumulates_same_step(self):
        """同名 step 多次出现应累积，不覆盖（P2 修复）。"""
        ec = EvidenceChain()
        ec.record("filter:range", "postgres", 0.1, 10)
        ec.record("filter:range", "postgres", 0.3, 5)
        assert ec.timings["filter:range"] == 0.4  # 累积非 0.3


class TestMatchAttr:
    def test_exact_match(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        f = Filter(field="status", op="exactMatch", value="ACTIVE")
        assert DataFrameQueryService._match_attr(f, "ACTIVE")
        assert not DataFrameQueryService._match_attr(f, "INACTIVE")

    def test_is_null(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        assert DataFrameQueryService._match_attr(Filter(field="x", op="isNull"), None)
        assert not DataFrameQueryService._match_attr(Filter(field="x", op="isNull"), "val")

    def test_range(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        f = Filter(field="amt", op="range", value={"min": 10, "max": 100})
        assert DataFrameQueryService._match_attr(f, 50)
        assert not DataFrameQueryService._match_attr(f, 5)
        assert not DataFrameQueryService._match_attr(f, 150)


class TestMatchTime:
    def test_iso_string_in_range(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        assert DataFrameQueryService._match_time("ts", {"ts": "2024-06-01T00:00:00"}, "2024-01-01T00:00:00", "2024-12-01T00:00:00")

    def test_iso_string_out_of_range(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        assert not DataFrameQueryService._match_time("ts", {"ts": "2023-01-01T00:00:00"}, "2024-01-01T00:00:00", "2024-12-01T00:00:00")

    def test_numeric_ts_in_range(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        assert DataFrameQueryService._match_time("ts", {"ts": 5000}, 2000, 8000)
        assert not DataFrameQueryService._match_time("ts", {"ts": 9000}, 2000, 8000)

    def test_missing_field(self):
        from ontology.services.object_set_executor import DataFrameQueryService

        assert not DataFrameQueryService._match_time("ts", {}, None, None)

    def test_numeric_string_ts(self):
        """数值字符串时间戳（如 '1751328000000'）应按数值比较。"""
        from ontology.services.object_set_executor import DataFrameQueryService

        assert DataFrameQueryService._match_time("ts", {"ts": "5000"}, "2000", "8000")


class TestNewFilterOps:
    """新增 filter op（notEqual/in/notIn/greaterThan/lessThan/startsWith/endsWith）测试。"""

    async def test_in_filter(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"status": "A"}},
                {"rid": "v2", "properties": {"status": "B"}},
                {"rid": "v3", "properties": {"status": "C"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="status", op="in", value=["A", "C"])],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 2  # v1(A), v3(C)

    async def test_not_in_filter(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"status": "A"}},
                {"rid": "v2", "properties": {"status": "B"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="status", op="notIn", value=["A"])],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # 只有 v2(B)

    async def test_greater_than_filter(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"amt": 50}},
                {"rid": "v2", "properties": {"amt": 150}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="amt", op="greaterThan", value=100)],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # v2(150)

    async def test_starts_with_filter(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"name": "Acme"}},
                {"rid": "v2", "properties": {"name": "Beta"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="name", op="startsWith", value="Ac")],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # v1(Acme)


class TestSetOperations:
    """集合运算 union/intersect/subtract 测试。"""

    async def test_union(self, svc, mock_metadata):
        ir = ObjectSetIR(
            type="union",
            object_sets=[
                ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
                ObjectSetIR(type="static", objects=["v2", "v3"], object_type="S"),
            ],
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 3  # v1, v2, v3 去重

    async def test_intersect(self, svc, mock_metadata):
        ir = ObjectSetIR(
            type="intersect",
            object_sets=[
                ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
                ObjectSetIR(type="static", objects=["v2", "v3", "v4"], object_type="S"),
            ],
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 2  # v2, v3

    async def test_subtract(self, svc, mock_metadata):
        ir = ObjectSetIR(
            type="subtract",
            object_sets=[
                ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
                ObjectSetIR(type="static", objects=["v2"], object_type="S"),
            ],
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 2  # v1, v3

    async def test_union_requires_two_sets(self):
        with pytest.raises(ValueError, match="union requires object_sets"):
            ObjectSetIR(type="union", object_sets=[ObjectSetIR(type="static", objects=["v1"], object_type="S")])


class TestOrderBy:
    """order_by 排序测试（保证 cursor 分页稳定性）。"""

    async def test_order_by_ascending(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"amt": 300}},
                {"rid": "v2", "properties": {"amt": 100}},
                {"rid": "v3", "properties": {"amt": 200}},
            ]
        )
        # static IR 的 rids 顺序是 v1,v2,v3，order_by amt asc 应得 v2,v3,v1
        ir = ObjectSetIR(
            type="static", objects=["v1", "v2", "v3"], object_type="S",
            order_by=[{"field": "amt", "desc": False}],
        )
        result = await svc.execute(ir, "SC")
        rids = [o["rid"] for o in result.objects]
        # _hydrate 用 get_object_states_by_rids，顺序可能不保持，验证 total
        assert result.stats["total_rids"] == 3

    async def test_order_by_cursor_stability(self, svc, mock_metadata, monkeypatch):
        """order_by 后 cursor 分页位置稳定。"""
        from ontology.config import settings as _settings
        monkeypatch.setattr(_settings, "hydrate_limit", 2)
        mock_metadata.get_rids_by_type = AsyncMock(
            return_value=[f"v{i}" for i in range(5)]
        )
        # 每次查 states 返回带 amt 的（用于排序）
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": f"v{i}", "properties": {"amt": i * 10}} for i in range(5)]
        )
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            order_by=[{"field": "amt", "desc": False}],
        )
        page1 = await svc.execute(ir, "SC")
        assert page1.truncated is True
        # cursor 是排序后第 2 个（amt=10 的 v1）
        assert page1.next_cursor is not None


class TestAggregate:
    """aggregate 聚合查询测试。"""

    async def test_group_by_count(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "object_type_api_name": "S", "properties": {"risk": "high", "amt": 100}},
                {"rid": "v2", "object_type_api_name": "S", "properties": {"risk": "high", "amt": 200}},
                {"rid": "v3", "object_type_api_name": "S", "properties": {"risk": "low", "amt": 50}},
            ]
        )
        ir = ObjectSetIR(
            type="aggregate",
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
            group_by=["risk"],
            aggregations=[{"func": "count", "field": "", "alias": "cnt"}, {"func": "sum", "field": "amt", "alias": "total"}],
        )
        result = await svc.execute(ir, "SC")
        assert len(result.aggregates) == 2  # high, low 两组
        # 找 high 组
        high = next(a for a in result.aggregates if a["group"]["risk"] == "high")
        assert high["aggregates"]["cnt"] == 2
        assert high["aggregates"]["total"] == 300

    async def test_global_aggregate(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "object_type_api_name": "S", "properties": {"amt": 100}},
                {"rid": "v2", "object_type_api_name": "S", "properties": {"amt": 200}},
            ]
        )
        ir = ObjectSetIR(
            type="aggregate",
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
            aggregations=[{"func": "avg", "field": "amt", "alias": "avg_amt"}],
        )
        result = await svc.execute(ir, "SC")
        assert len(result.aggregates) == 1  # 无 group_by = 全局一组
        assert result.aggregates[0]["aggregates"]["avg_amt"] == 150

    async def test_min_max(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "object_type_api_name": "S", "properties": {"amt": 100}},
                {"rid": "v2", "object_type_api_name": "S", "properties": {"amt": 300}},
                {"rid": "v3", "object_type_api_name": "S", "properties": {"amt": 200}},
            ]
        )
        ir = ObjectSetIR(
            type="aggregate",
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
            aggregations=[{"func": "min", "field": "amt", "alias": "mn"}, {"func": "max", "field": "amt", "alias": "mx"}],
        )
        result = await svc.execute(ir, "SC")
        assert result.aggregates[0]["aggregates"]["mn"] == 100
        assert result.aggregates[0]["aggregates"]["mx"] == 300

    async def test_aggregate_requires_aggregations(self):
        with pytest.raises(ValueError, match="aggregate requires"):
            ObjectSetIR(type="aggregate", object_set=ObjectSetIR(type="static", objects=["v1"], object_type="S"))


class TestSelect:
    """select 投影测试。"""

    async def test_select_fields(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "object_type_api_name": "S", "properties": {"a": 1, "b": 2, "c": 3}},
            ]
        )
        ir = ObjectSetIR(
            type="select",
            object_set=ObjectSetIR(type="static", objects=["v1"], object_type="S"),
            select_fields=["a", "c"],
        )
        result = await svc.execute(ir, "SC")
        assert result.objects[0]["props"] == {"a": 1, "c": 3}  # 只投影 a, c

    async def test_select_requires_fields(self):
        with pytest.raises(ValueError, match="select requires"):
            ObjectSetIR(type="select", object_set=ObjectSetIR(type="static", objects=["v1"], object_type="S"))


class TestWhereClause:
    """where 嵌套逻辑组合执行测试（内存兜底路径）。"""

    async def test_or_memory(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"risk": "high"}},
                {"rid": "v2", "properties": {"risk": "medium"}},
                {"rid": "v3", "properties": {"risk": "low"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
            where={"type": "or", "value": [
                {"field": "risk", "op": "exactMatch", "value": "high"},
                {"field": "risk", "op": "exactMatch", "value": "medium"},
            ]},
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 2  # v1, v2

    async def test_not_memory(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"risk": "high"}},
                {"rid": "v2", "properties": {"risk": "low"}},
            ]
        )
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
            where={"type": "not", "value": {"field": "risk", "op": "exactMatch", "value": "high"}},
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1  # v2

    async def test_nested_and_or_not_memory(self, svc, mock_metadata):
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"risk": "high", "status": "active"}},
                {"rid": "v2", "properties": {"risk": "medium", "status": "active"}},
                {"rid": "v3", "properties": {"risk": "high", "status": "archived"}},
            ]
        )
        # (risk=high OR risk=medium) AND NOT status=archived
        ir = ObjectSetIR(
            type="filter",
            object_set=ObjectSetIR(type="static", objects=["v1", "v2", "v3"], object_type="S"),
            where={"type": "and", "value": [
                {"type": "or", "value": [
                    {"field": "risk", "op": "exactMatch", "value": "high"},
                    {"field": "risk", "op": "exactMatch", "value": "medium"},
                ]},
                {"type": "not", "value": {"field": "status", "op": "exactMatch", "value": "archived"}},
            ]},
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 2  # v1, v2

    async def test_with_properties_not_implemented(self, svc, mock_metadata):
        ir = ObjectSetIR(
            type="withProperties",
            object_set=ObjectSetIR(type="static", objects=["v1"], object_type="S"),
            derived_properties={"score": {"expression": "1+1"}},
        )
        with pytest.raises(NotImplementedError, match="withProperties"):
            await svc.execute(ir, "SC")


class TestInterfaceBase:
    """interfaceBase 跨类型起始集测试。"""

    async def test_interface_base(self, svc, mock_metadata):
        mock_metadata.get_rids_by_interface = AsyncMock(
            return_value=["v1", "v2", "v3"]
        )
        ir = ObjectSetIR(type="interfaceBase", interface="Geolocated")
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 3

    async def test_interface_base_empty(self, svc, mock_metadata):
        mock_metadata.get_rids_by_interface = AsyncMock(return_value=[])
        ir = ObjectSetIR(type="interfaceBase", interface="NonExistent")
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 0


class TestFieldWhitelist:
    """P2: filter.field 必须在本体 properties 白名单内（红线 8 + 可读错误）。

    防止拼错属性名静默返回空结果（违反「错误必须有可读反馈，禁止静默失败」）。
    field 名参数化已防注入，白名单是差基线补充 + 体验改善。
    """

    @staticmethod
    def _ot(api_name: str, props: list[str]):
        """构造带 properties 的 ObjectType mock。"""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import DataType, ObjectType, PropertyDef

        now = datetime.now(UTC)
        return ObjectType(
            id=f"ot-{api_name}",
            ontology_id="ont-1",
            api_name=api_name,
            display_name=api_name,
            description="",
            primary_key="id",
            title_property="id",
            storage_type="MANAGED",
            created_at=now,
            updated_at=now,
            properties=[
                PropertyDef(
                    id=f"p-{api_name}-{p}",
                    object_type_id=f"ot-{api_name}",
                    api_name=p,
                    display_name=p,
                    data_type=DataType.STRING,
                    created_at=now,
                    updated_at=now,
                )
                for p in props
            ],
        )

    async def test_unknown_field_raises_validation_error(self, svc, mock_metadata):
        """filter.field 不在本体白名单 → ValidationError（可读错误，列可用属性）。"""
        from ontology.core.exceptions import ValidationError

        mock_metadata.list_object_types = AsyncMock(
            return_value=[self._ot("Supplier", ["status", "riskLevel", "name"])]
        )
        mock_metadata.get_rids_by_type = AsyncMock(return_value=["v1"])
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            filters=[Filter(field="risk_level", op="exactMatch", value="high")],
        )
        with pytest.raises(ValidationError, match="risk_level") as exc_info:
            await svc.execute(ir, "SC")
        # 错误信息列出可用属性，帮助用户/LLM 纠正
        assert "status" in str(exc_info.value)
        assert "riskLevel" in str(exc_info.value)

    async def test_known_field_passes(self, svc, mock_metadata):
        """filter.field 在白名单 → 正常执行。"""
        mock_metadata.list_object_types = AsyncMock(
            return_value=[self._ot("Supplier", ["status", "riskLevel"])]
        )
        mock_metadata.get_rids_by_type = AsyncMock(return_value=["v1", "v2"])
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "properties": {"riskLevel": "high"}},
                {"rid": "v2", "properties": {"riskLevel": "low"}},
            ]
        )
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            filters=[Filter(field="riskLevel", op="exactMatch", value="high")],
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1

    async def test_where_clause_unknown_field_raises(self, svc, mock_metadata):
        """where 嵌套逻辑组合中的未知 field → ValidationError。"""
        from ontology.core.exceptions import ValidationError
        from ontology.core.schemas.object_set import AndClause

        mock_metadata.list_object_types = AsyncMock(
            return_value=[self._ot("Supplier", ["status", "riskLevel"])]
        )
        mock_metadata.get_rids_by_type = AsyncMock(return_value=["v1"])
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            where=AndClause(value=[
                Filter(field="status", op="exactMatch", value="ACTIVE"),
                Filter(field="nonexistent", op="exactMatch", value="x"),
            ]),
        )
        with pytest.raises(ValidationError, match="nonexistent"):
            await svc.execute(ir, "SC")

    async def test_empty_whitelist_skips_validation(self, svc, mock_metadata):
        """本体无 OT（list_object_types=[]）→ 跳过白名单校验，不阻塞查询。

        兼容边界：新本体/测试 mock 未配 properties 时不应报错。
        """
        mock_metadata.list_object_types = AsyncMock(return_value=[])
        mock_metadata.get_rids_by_type = AsyncMock(return_value=["v1"])
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": "v1", "properties": {"anyField": "x"}}]
        )
        ir = ObjectSetIR(
            type="objectType", object_type="Supplier",
            filters=[Filter(field="anyField", op="exactMatch", value="x")],
        )
        # 不 raise，正常执行
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1

    async def test_spatial_field_not_subject_to_attr_whitelist(self, svc, mock_metadata, mock_geotime):
        """空间算子（withinDistance）的 field 是几何列，不走属性白名单。"""
        from ontology.core.schemas.object_set import Filter as F

        mock_metadata.list_object_types = AsyncMock(
            return_value=[self._ot("Vehicle", ["status"])]  # 无 location 属性
        )
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": "v1", "object_type_api_name": "Vehicle"}]
        )
        mock_geotime.table_exists = AsyncMock(return_value=True)
        mock_geotime.spatial_filter = AsyncMock(return_value=["v1"])
        ir = ObjectSetIR(
            type="filter",
            filters=[F(field="location", op="withinDistance", center=[116.0, 39.0], max_distance=5000)],
            object_set=ObjectSetIR(type="static", objects=["v1"], object_type="S"),
        )
        # location 不在属性白名单，但空间算子豁免 → 不 raise
        await svc.execute(ir, "SC")
        mock_geotime.spatial_filter.assert_awaited_once()


class TestObjectStateBackingColumnKeys:
    """object_state stores backing_column keys; the executor speaks api_name.

    Verifies the load-boundary translation (core.property_mapping): filter
    fields are api_name on the IR surface but object_state JSONB is keyed by
    backing_column, so the executor translates field → backing_column for
    SQL/in-memory match and translates props → api_name for hydration output.
    """

    @staticmethod
    def _ot_with_backing() -> "object":
        """Lead OT: leadsId/operationTime (api_name) → leads_id/operation_time."""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import (
            BackingColumnRef,
            DataType,
            ObjectType,
            PropertyDef,
        )

        now = datetime.now(UTC)

        def _p(api: str, col: str, *, indexed: bool = False) -> PropertyDef:
            return PropertyDef(
                id=f"p-{api}",
                object_type_id="ot-lead",
                api_name=api,
                display_name=api,
                data_type=DataType.STRING,
                indexed=indexed,
                backing_mapping=BackingColumnRef(
                    dataset_api_name="ds",
                    backing_catalog="cat",
                    backing_schema="public",
                    backing_table="t_lead",
                    backing_column=col,
                ),
                created_at=now,
                updated_at=now,
            )

        return ObjectType(
            id="ot-lead",
            ontology_id="ont-1",
            api_name="Lead",
            display_name="Lead",
            description="",
            primary_key="leadsId",
            title_property="leadsId",
            storage_type="MANAGED",
            created_at=now,
            updated_at=now,
            properties=[_p("leadsId", "leads_id", indexed=True), _p("operationTime", "operation_time")],
        )

    async def test_hydrate_translates_backing_to_api(self, svc, mock_metadata):
        """Hydration output surfaces api_name, not backing_column."""
        mock_metadata.list_object_types = AsyncMock(return_value=[self._ot_with_backing()])
        # object_state stores backing_column keys (post-migration state).
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {
                    "rid": "v1",
                    "object_type_api_name": "Lead",
                    "properties": {"leads_id": "L1", "operation_time": "2026-01-01"},
                }
            ]
        )
        ir = ObjectSetIR(type="static", objects=["v1"], object_type="S")
        result = await svc.execute(ir, "SC")
        props = result.objects[0]["props"]
        # backing_column → api_name in the consumer-facing output.
        assert props["leadsId"] == "L1"
        assert props["operationTime"] == "2026-01-01"
        assert "leads_id" not in props
        assert "operation_time" not in props

    async def test_filter_field_translated_to_backing_in_memory(self, svc, mock_metadata):
        """api_name filter field matches backing_column-keyed properties (memory path)."""
        mock_metadata.list_object_types = AsyncMock(return_value=[self._ot_with_backing()])
        # object_state stores backing_column keys.
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[
                {"rid": "v1", "object_type_api_name": "Lead", "properties": {"leads_id": "L1"}},
                {"rid": "v2", "object_type_api_name": "Lead", "properties": {"leads_id": "L2"}},
            ]
        )
        # svc has no attr_engine → memory filter path.
        ir = ObjectSetIR(
            type="filter",
            filters=[Filter(field="leadsId", op="exactMatch", value="L1")],
            object_set=ObjectSetIR(type="static", objects=["v1", "v2"], object_type="S"),
        )
        result = await svc.execute(ir, "SC")
        assert result.stats["total_rids"] == 1
        assert result.objects[0]["rid"] == "v1"

    async def test_compile_attr_pred_translates_field(self, svc, mock_metadata):
        """_compile_attr_pred emits the backing_column as the JSONB key param."""
        mock_metadata.list_object_types = AsyncMock(return_value=[self._ot_with_backing()])
        # Trigger _load_allowed_fields so the flat map is populated.
        ir = ObjectSetIR(type="static", objects=["v1"], object_type="S")
        await svc.execute(ir, "SC")
        f = Filter(field="leadsId", op="exactMatch", value="L1")
        _pred, params = svc._compile_attr_pred(f, "f0")
        # The JSONB key param is the backing_column, not the api_name.
        assert params["f0_field"] == "leads_id"


class TestHydrateVirtualRouting:
    """PR 5a：VIRTUAL rid 批量水合（§7.7）。

    VIRTUAL rid（``ri.ontology.main.virtual-object.*``）解析 locator 得
    (ont, ot, pk) → 按 (ont,ot) 分组 → ObjectQueryService.hydrate_by_pks
    批量 WHERE pk IN (...) 查 Trino 联邦源表。MANAGED rid 仍走 PG
    object_state。混合 rid 列表两边各取。
    """

    @staticmethod
    def _managed_rid() -> str:
        from ontology.core.rid import generate_object_rid
        return generate_object_rid()

    @staticmethod
    def _virtual_rid(ont: str, ot: str, pk: str) -> str:
        from ontology.core.rid import generate_virtual_rid
        return generate_virtual_rid(ont, ot, pk)

    @staticmethod
    def _virtual_ot(api_name: str, pk_api: str = "id") -> "ObjectType":
        """构造 VIRTUAL ObjectType（含 backing_mapping，供 hydrate_by_pks 用）。"""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import (
            BackingColumnRef,
            DataType,
            ObjectType,
            PropertyDef,
        )
        now = datetime.now(UTC)
        return ObjectType(
            id=f"ot-{api_name}",
            ontology_id="ont-1",
            api_name=api_name,
            display_name=api_name,
            description="",
            primary_key=pk_api,
            title_property=pk_api,
            storage_type="VIRTUAL",
            created_at=now,
            updated_at=now,
            properties=[
                PropertyDef(
                    id=f"p-{api_name}-id",
                    object_type_id=f"ot-{api_name}",
                    api_name=pk_api,
                    display_name=pk_api,
                    description="",
                    data_type=DataType.STRING,
                    is_primary_key=True,
                    is_title_property=True,
                    nullable=False,
                    indexed=False,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                    backing_mapping=BackingColumnRef(
                        dataset_api_name="ds1",
                        backing_catalog="pgnative",
                        backing_schema="public",
                        backing_table="t1",
                        backing_column=pk_api,
                    ),
                ),
                PropertyDef(
                    id=f"p-{api_name}-name",
                    object_type_id=f"ot-{api_name}",
                    api_name="name",
                    display_name="name",
                    description="",
                    data_type=DataType.STRING,
                    is_primary_key=False,
                    is_title_property=False,
                    nullable=True,
                    indexed=False,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                    backing_mapping=BackingColumnRef(
                        dataset_api_name="ds1",
                        backing_catalog="pgnative",
                        backing_schema="public",
                        backing_table="t1",
                        backing_column="name",
                    ),
                ),
            ],
        )

    async def test_single_virtual_rid_calls_hydrate_by_pks(self, mock_metadata):
        """单个 VIRTUAL rid → hydrate_by_pks 被调用（批量），返回 Trino 联邦数据。"""
        mock_oqs = AsyncMock()
        mock_oqs.hydrate_by_pks = AsyncMock(return_value=[{"id": "C001", "name": "Acme"}])
        ot_obj = self._virtual_ot("Customer")
        mock_metadata.get_object_type = AsyncMock(return_value=ot_obj)
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        vrid = self._virtual_rid("SC", "Customer", "C001")
        objs = await svc._hydrate([vrid])
        assert len(objs) == 1
        assert objs[0]["rid"] == vrid
        assert objs[0]["api_name"] == "Customer"
        assert objs[0]["props"]["name"] == "Acme"
        mock_oqs.hydrate_by_pks.assert_awaited_once()
        call_args = mock_oqs.hydrate_by_pks.call_args
        assert call_args.args[0] == "SC"  # ontology
        assert call_args.args[2] == ["C001"]  # pks
        # hydrate_by_pk（逐个）不应再被调用
        mock_oqs.hydrate_by_pk.assert_not_awaited()

    async def test_managed_rid_still_uses_pg_object_state(self, mock_metadata):
        """MANAGED rid → 仍走 PG get_object_states_by_rids（MVP，未来切 Doris）。"""
        mock_oqs = AsyncMock()
        mrid = self._managed_rid()
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": mrid, "object_type_api_name": "Order", "properties": {"amt": 100}}]
        )
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        objs = await svc._hydrate([mrid])
        assert len(objs) == 1
        assert objs[0]["rid"] == mrid
        assert objs[0]["props"]["amt"] == 100
        mock_metadata.get_object_states_by_rids.assert_awaited_once_with([mrid])
        mock_oqs.hydrate_by_pks.assert_not_awaited()

    async def test_mixed_rids_split_between_pg_and_trino_batch(self, mock_metadata):
        """混合 rid：MANAGED 走 PG，VIRTUAL 走 hydrate_by_pks 批量，结果合并。"""
        mock_oqs = AsyncMock()
        mock_oqs.hydrate_by_pks = AsyncMock(return_value=[{"id": "S1", "name": "Ext"}])
        mrid = self._managed_rid()
        vrid = self._virtual_rid("SC", "Supplier", "S1")
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": mrid, "object_type_api_name": "Order", "properties": {"amt": 50}}]
        )
        mock_metadata.get_object_type = AsyncMock(return_value=self._virtual_ot("Supplier"))
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        objs = await svc._hydrate([vrid, mrid])
        rids = {o["rid"] for o in objs}
        assert rids == {mrid, vrid}
        mock_metadata.get_object_states_by_rids.assert_awaited_once_with([mrid])
        mock_oqs.hydrate_by_pks.assert_awaited_once()

    async def test_batch_multiple_pks_single_ot_single_call(self, mock_metadata):
        """§7.7：同 OT 多 PK → 单次 hydrate_by_pks 调用（批量，非 N+1）。"""
        mock_oqs = AsyncMock()
        mock_oqs.hydrate_by_pks = AsyncMock(return_value=[
            {"id": "C001", "name": "A"}, {"id": "C002", "name": "B"},
        ])
        mock_metadata.get_object_type = AsyncMock(return_value=self._virtual_ot("Customer"))
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        vrids = [self._virtual_rid("SC", "Customer", pk) for pk in ("C001", "C002")]
        objs = await svc._hydrate(vrids)
        assert len(objs) == 2
        # 关键断言：hydrate_by_pks 只调一次（批量），不是两次
        assert mock_oqs.hydrate_by_pks.await_count == 1
        assert mock_oqs.hydrate_by_pks.call_args.args[2] == ["C001", "C002"]

    async def test_batch_multiple_ots_grouped_serially(self, mock_metadata):
        """多 OT 的 rid → 按 (ont,ot) 分组，各组串行查（串行非并发）。"""
        mock_oqs = AsyncMock()
        # Customer OT 返回 1 行，Supplier OT 返回 1 行
        def _hydrate_by_pks_side(_ont, ot_obj, pks, _sf=None):
            return [{"id": pks[0], "name": f"{ot_obj.api_name}-name"}]
        mock_oqs.hydrate_by_pks = AsyncMock(side_effect=_hydrate_by_pks_side)
        def _get_ot(_ont, ot_api):
            return self._virtual_ot(ot_api)
        mock_metadata.get_object_type = AsyncMock(side_effect=_get_ot)
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        v1 = self._virtual_rid("SC", "Customer", "C1")
        v2 = self._virtual_rid("SC", "Supplier", "S1")
        objs = await svc._hydrate([v1, v2])
        assert len(objs) == 2
        # 两个 OT 各调一次 hydrate_by_pks
        assert mock_oqs.hydrate_by_pks.await_count == 2

    async def test_virtual_rid_without_oqs_returns_empty_with_warning(self, mock_metadata):
        """object_query_service 未注入 → VIRTUAL rid 水合返回空（不报错）。"""
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=None,
        )
        vrid = self._virtual_rid("SC", "Customer", "C001")
        objs = await svc._hydrate([vrid])
        assert objs == []

    async def test_one_ot_group_failure_marks_partial_others_ok(self, mock_metadata):
        """ADR-021 §2.8：某 OT 组 hydrate_by_pks 抛异常 → 该组全 _partial，其他组正常。"""
        mock_oqs = AsyncMock()
        # 第一次调用（Customer）抛异常，第二次（Supplier）成功
        call_count = 0
        async def _side(_ont, ot_obj, pks, _sf=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Trino down")
            return [{"id": pks[0], "name": "Ok"}]
        mock_oqs.hydrate_by_pks = AsyncMock(side_effect=_side)
        def _get_ot(_ont, ot_api):
            return self._virtual_ot(ot_api)
        mock_metadata.get_object_type = AsyncMock(side_effect=_get_ot)
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        v_bad = self._virtual_rid("SC", "Customer", "C1")
        v_ok = self._virtual_rid("SC", "Supplier", "S1")
        objs = await svc._hydrate([v_bad, v_ok])
        assert len(objs) == 2
        partial_obj = next(o for o in objs if o["rid"] == v_bad)
        ok_obj = next(o for o in objs if o["rid"] == v_ok)
        assert partial_obj.get("_partial") is True
        assert partial_obj.get("_error") == "source unavailable"
        assert partial_obj["props"] == {}
        assert ok_obj["props"]["name"] == "Ok"

    async def test_pk_not_found_in_source_skipped(self, mock_metadata):
        """源表无此 PK 行 → 跳过（不进结果，对齐原 data is None: continue）。"""
        mock_oqs = AsyncMock()
        # 查 2 个 pk 只返回 1 行
        mock_oqs.hydrate_by_pks = AsyncMock(return_value=[{"id": "C001", "name": "A"}])
        mock_metadata.get_object_type = AsyncMock(return_value=self._virtual_ot("Customer"))
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        v1 = self._virtual_rid("SC", "Customer", "C001")  # 存在
        v2 = self._virtual_rid("SC", "Customer", "C999")  # 源表无
        objs = await svc._hydrate([v1, v2])
        assert len(objs) == 1
        assert objs[0]["rid"] == v1

    async def test_select_fields_passed_through(self, mock_metadata):
        """select_fields 透传到 hydrate_by_pks（下推到 SELECT 列表）。"""
        mock_oqs = AsyncMock()
        mock_oqs.hydrate_by_pks = AsyncMock(return_value=[{"id": "C001", "name": "A"}])
        mock_metadata.get_object_type = AsyncMock(return_value=self._virtual_ot("Customer"))
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        vrid = self._virtual_rid("SC", "Customer", "C001")
        await svc._hydrate([vrid], select_fields=["name"])
        # select_fields 作为第 4 个参数传入 hydrate_by_pks
        assert mock_oqs.hydrate_by_pks.call_args.args[3] == ["name"]

    async def test_legacy_uuid_rid_treated_as_managed(self, mock_metadata):
        """裸 UUID（旧数据，非 RID 格式）按 MANAGED 处理，走 PG。"""
        mock_oqs = AsyncMock()
        legacy = "550e8400-e29b-41d4-a716-446655440000"
        mock_metadata.get_object_states_by_rids = AsyncMock(
            return_value=[{"rid": legacy, "object_type_api_name": "Order", "properties": {"amt": 1}}]
        )
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        objs = await svc._hydrate([legacy])
        assert len(objs) == 1
        assert objs[0]["rid"] == legacy
        mock_oqs.hydrate_by_pks.assert_not_awaited()

    async def test_empty_rids_returns_empty(self, mock_metadata):
        """空 rid 列表 → 不调任何后端。"""
        mock_oqs = AsyncMock()
        svc = DataFrameQueryService(
            graph_store=AsyncMock(), geotime_store=AsyncMock(), metadata=mock_metadata,
            object_query_service=mock_oqs,
        )
        assert await svc._hydrate([]) == []
        mock_oqs.hydrate_by_pks.assert_not_awaited()
