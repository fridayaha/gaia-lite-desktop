"""Unit tests for Neo4jGraphStore (Graph Layer, graph-reasoning-design.md §4).

Neo4j driver is mocked. Tests validate:
1. Cypher generation correctness (label/relationship naming, MERGE/DELETE)
2. search_around multi-hop Cypher + direction + limit + truncation (C9)
3. exists_link ANY_TARGET vs SINGLE_TARGET modes
4. Node filter pushdown rendering
5. Error path (GraphUnavailableError on connection failure)
6. rid 稳定主键（C1）：所有 Cypher 以 rid 为锚

Integration tests (real Neo4j) live in tests/integration/.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ontology.config.settings import settings
from ontology.core.exceptions import GraphUnavailableError
from ontology.core.schemas.graph import EdgeProps, NodeFilter
from ontology.layers.graph import neo4j_graph_store
from ontology.layers.graph.neo4j_graph_store import Neo4jGraphStore


@pytest.fixture
def mock_driver() -> AsyncMock:
    """Mock neo4j AsyncDriver. execute_query returns a result with .records."""
    driver = AsyncMock()
    result = MagicMock()
    result.records = []
    driver.execute_query = AsyncMock(return_value=result)
    driver.verify_connectivity = AsyncMock()
    driver.close = AsyncMock()
    return driver


@pytest.fixture
def store(mock_driver: AsyncMock) -> Neo4jGraphStore:
    """Neo4jGraphStore with mocked driver singleton."""
    with patch.object(neo4j_graph_store, "_driver", mock_driver):
        yield Neo4jGraphStore()


def _result_with(records: list[dict]) -> MagicMock:
    """Build a fake execute_query result yielding the given records."""
    result = MagicMock()
    result.records = [MagicMock(spec=list, **r) for r in records]
    # MagicMock(spec=list) won't subscript; use a dict-backed record instead.
    result.records = [_Record(r) for r in records]
    return result


class _Record:
    """Minimal record proxy supporting record['key'] + .get(key) (Neo4j Record API)."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def get(self, key: str, default: object = None) -> object:
        return self._data.get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def keys(self) -> list[str]:
        return list(self._data.keys())


class TestCreateLabel:
    async def test_creates_unique_constraint_on_rid(self, store, mock_driver):
        label = await store.create_label("SupplyChain", "Supplier")
        assert label == "SupplyChainSupplier"
        mock_driver.execute_query.assert_awaited_once()
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "SupplyChainSupplier" in cypher
        assert "REQUIRE n.rid IS UNIQUE" in cypher
        assert "IF NOT EXISTS" in cypher

    async def test_idempotent_constraint_name(self, store, mock_driver):
        # 同一 label 二次调用用同一约束名（IF NOT EXISTS 幂等）。
        await store.create_label("SC", "Order")
        await store.create_label("SC", "Order")
        assert mock_driver.execute_query.await_count == 2


class TestUpsertNode:
    async def test_merge_on_rid_and_set_props(self, store, mock_driver):
        await store.upsert_node("SupplyChainSupplier", "rid-123", {"rid": "rid-123", "status": "ACTIVE"})
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "MERGE (n:SupplyChainSupplier {rid: $rid})" in cypher
        assert "n.status = $status" in cypher
        params = mock_driver.execute_query.call_args.kwargs["parameters_"]
        assert params["rid"] == "rid-123"
        assert params["status"] == "ACTIVE"

    async def test_empty_props_only_sets_rid(self, store, mock_driver):
        await store.upsert_node("Label", "rid-1", {})
        cypher = mock_driver.execute_query.call_args.args[0]
        # 空 props 时至少有 rid。
        assert "MERGE (n:Label {rid: $rid})" in cypher


class TestUpsertEdge:
    async def test_merge_edge_with_props(self, store, mock_driver):
        await store.upsert_edge(
            "SupplyChainSupplies",
            "SupplyChainSupplier",
            "s1",
            "SupplyChainOrder",
            "o1",
            EdgeProps(weight=0.8, visibility="NORMAL"),
        )
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "MERGE (s)-[r:SupplyChainSupplies]->(t)" in cypher
        assert "r.weight = $weight" in cypher
        assert "r.visibility = $visibility" in cypher
        params = mock_driver.execute_query.call_args.kwargs["parameters_"]
        assert params["weight"] == 0.8
        assert params["source_rid"] == "s1"
        assert params["target_rid"] == "o1"

    async def test_edge_without_props_no_set_clause(self, store, mock_driver):
        await store.upsert_edge("Rel", "Src", "s1", "Tgt", "t1", None)
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "MERGE (s)-[r:Rel]->(t)" in cypher
        assert " SET " not in cypher

    async def test_temporal_edge_props(self, store, mock_driver):
        await store.upsert_edge(
            "Rel",
            "Src",
            "s1",
            "Tgt",
            "t1",
            EdgeProps(start_time="2026-01-01T00:00:00Z", end_time="2026-12-31T23:59:59Z"),
        )
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "r.start_time = $start_time" in cypher
        assert "r.end_time = $end_time" in cypher


class TestDelete:
    async def test_delete_node_detach(self, store, mock_driver):
        await store.delete_node("Label", "rid-1")
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "DETACH DELETE n" in cypher

    async def test_delete_edge(self, store, mock_driver):
        await store.delete_edge("Rel", "Src", "s1", "Tgt", "t1")
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "DELETE r" in cypher
        assert "[r:Rel]" in cypher


class TestSearchAround:
    async def test_empty_source_rids_returns_empty(self, store):
        result = await store.search_around("Label", [], (1, 3))
        assert result.rids == []
        assert result.matched_count == 0

    async def test_outgoing_multihop_cypher(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with(
            [{"rid": "t1", "src_rid": "s1"}, {"rid": "t2", "src_rid": "s1"}]
        )
        result = await store.search_around("SupplyChainOrder", ["s1"], (1, 3), direction="out", limit=100)
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "[*1..3]->" in cypher  # outgoing direction
        assert "m:SupplyChainOrder" in cypher
        assert "UNWIND $rids" in cypher
        assert "LIMIT $limit" in cypher
        assert result.rids == ["t1", "t2"]
        assert result.matched_count == 2  # 2 distinct (start, m) edge pairs
        assert result.hops == 3

    async def test_incoming_direction(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([])
        await store.search_around("Label", ["s1"], (1, 2), direction="in")
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "<-[*1..2]-" in cypher

    async def test_both_direction(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([])
        await store.search_around("Label", ["s1"], (1, 2), direction="both")
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "-[*1..2]-" in cypher

    async def test_rel_types_filter(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([])
        await store.search_around(
            "Label",
            ["s1"],
            (1, 3),
            rel_types=["SCSupplies", "SCHasItems"],
            direction="out",
        )
        cypher = mock_driver.execute_query.call_args.args[0]
        assert ":SCSupplies|:SCHasItems" in cypher

    async def test_truncation_when_limit_reached(self, store, mock_driver):
        # 返回刚好等于 limit 的 (start, m) 边对数 → truncated=True。
        # 方向 B 精细化 (T1.7): matched_count = 边对数, rids 去重。
        mock_driver.execute_query.return_value = _result_with([{"rid": f"v{i}", "src_rid": "s1"} for i in range(100)])
        result = await store.search_around("Label", ["s1"], (1, 3), limit=100)
        assert result.truncated is True
        assert len(result.rids) == 100  # 100 distinct target rids (all unique)
        assert result.matched_count == 100  # 100 edge pairs

    async def test_no_truncation_below_limit(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([{"rid": "v1"}])
        result = await store.search_around("Label", ["s1"], (1, 3), limit=100)
        assert result.truncated is False

    async def test_default_limit_from_settings(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([])
        await store.search_around("Label", ["s1"], (1, 3))
        params = mock_driver.execute_query.call_args.kwargs["parameters_"]
        # 默认 limit 来自 settings.graph_traversal_result_limit (1_000_000, D4)。
        assert params["limit"] == 1_000_000

    async def test_node_filter_pushdown_eq(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([])
        await store.search_around(
            "Label",
            ["s1"],
            (1, 3),
            node_filter=NodeFilter(field="status", op="eq", value="ACTIVE"),
        )
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "m.status = $nf_value" in cypher
        params = mock_driver.execute_query.call_args.kwargs["parameters_"]
        assert params["nf_value"] == "ACTIVE"

    async def test_node_filter_pushdown_in(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([])
        await store.search_around(
            "Label",
            ["s1"],
            (1, 3),
            node_filter=NodeFilter(field="region", op="in", values=["east", "west"]),
        )
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "m.region IN $nf_values" in cypher

    async def test_returns_edge_pairs_for_traversal(self, store, mock_driver):
        """searchAround 返回 start.rid → m.rid 配对，填入 result.edges（ADR-015 探索轨迹）。"""
        mock_driver.execute_query.return_value = _result_with(
            [
                {"rid": "m1", "src_rid": "s1"},
                {"rid": "m2", "src_rid": "s1"},
                {"rid": "m3", "src_rid": "s2"},
            ]
        )
        result = await store.search_around(
            "Material",
            ["s1", "s2"],
            (1, 1),
            rel_types=["SCSupplies"],
            direction="out",
            limit=100,
        )
        # rids 仍保留
        assert result.rids == ["m1", "m2", "m3"]
        # 边配对去重后保留
        edge_pairs = {(e.source_rid, e.target_rid) for e in result.edges}
        assert edge_pairs == {("s1", "m1"), ("s1", "m2"), ("s2", "m3")}
        # rel_type / direction 透传
        assert all(e.rel_type == "SCSupplies" for e in result.edges)
        assert all(e.direction == "out" for e in result.edges)
        # Cypher 应同时 RETURN start.rid
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "start.rid AS src_rid" in cypher

    async def test_edges_empty_when_source_missing(self, store, mock_driver):
        """旧格式 record（无 src_rid 字段）不报错，edges 为空（向后兼容）。"""
        mock_driver.execute_query.return_value = _result_with([{"rid": "t1"}, {"rid": "t2"}])
        result = await store.search_around("Label", ["s1"], (1, 3), limit=100)
        assert result.rids == ["t1", "t2"]
        assert result.edges == []

    async def test_rids_deduped_when_multiple_starts_hit_same_target(self, store, mock_driver):
        """T1.7 (方向 B 精细化): 同一 m 被多 start 命中 → rids 去重, 但 edges 保留全部配对。"""
        mock_driver.execute_query.return_value = _result_with(
            [{"rid": "m1", "src_rid": "s1"}, {"rid": "m1", "src_rid": "s2"}, {"rid": "m2", "src_rid": "s1"}]
        )
        result = await store.search_around("Label", ["s1", "s2"], (1, 3), limit=100)
        # m1 被两个 start 命中, 但 rids 只出现一次 (保序去重)
        assert result.rids == ["m1", "m2"]
        # edges 保留两个 (s1→m1) (s2→m1) 配对
        assert len(result.edges) == 3
        assert result.matched_count == 3  # 3 distinct edge pairs


class TestExistsLink:
    async def test_any_target_mode(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([{"exists": True}])
        result = await store.exists_link("Rel", "Src", "s1", "Tgt", None, "out")
        assert result is True
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "RETURN count(r) > 0 AS exists" in cypher
        assert "()" in cypher  # 任意目标

    async def test_single_target_mode(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([{"exists": False}])
        result = await store.exists_link("Rel", "Src", "s1", "Tgt", "t1", "out")
        assert result is False
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "rid: $target_rid" in cypher

    async def test_reverse_direction(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([{"exists": True}])
        await store.exists_link("Rel", "Src", "s1", "Tgt", None, "in")
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "<-[r:Rel]-" in cypher


class TestErrorPath:
    async def test_connection_failure_raises_graph_unavailable(self, store, mock_driver):
        mock_driver.execute_query.side_effect = ConnectionError("connection refused")
        with pytest.raises(GraphUnavailableError):
            await store.create_label("SC", "Order")

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: 触发真 neo4j driver init（lite 未装 neo4j）",
    )
    async def test_driver_init_failure_raises_graph_unavailable(self):
        # _driver=None 且 _get_driver verify 失败 → GraphUnavailableError。
        with (
            patch.object(neo4j_graph_store, "_driver", None),
            patch("neo4j.AsyncGraphDatabase.driver", side_effect=ConnectionError("refused")),
        ):
            with pytest.raises(GraphUnavailableError):
                await neo4j_graph_store._get_driver()


class TestEscapeStringLiteral:
    def test_escapes_single_quote(self):
        from ontology.layers.graph.neo4j_graph_store import _escape_string_literal

        assert _escape_string_literal("it's") == "it\\'s"

    def test_escapes_backslash(self):
        from ontology.layers.graph.neo4j_graph_store import _escape_string_literal

        assert _escape_string_literal("a\\b") == "a\\\\b"


class TestBatchUpsert:
    """ADR-021 §2.5：批量写入（UNWIND + CALL {} IN TRANSACTIONS）。"""

    async def test_upsert_nodes_batch_empty_returns_zero(self, store):
        result = await store.upsert_nodes_batch("TestLabel", [])
        assert result == 0

    async def test_upsert_nodes_batch_writes_all(self, store, mock_driver):
        nodes = [
            {"rid": "r1", "api_name": "Foo", "name": "a"},
            {"rid": "r2", "api_name": "Foo", "name": "b"},
        ]
        result = await store.upsert_nodes_batch("Foo", nodes)
        assert result == 2
        mock_driver.execute_query.assert_awaited_once()
        call_args = mock_driver.execute_query.call_args
        cypher = call_args.args[0]
        params = call_args.kwargs["parameters_"]
        assert "UNWIND $rows AS row" in cypher
        assert "CALL {" in cypher
        assert "IN TRANSACTIONS OF 1000 ROWS" in cypher
        assert "MERGE (n:Foo {rid: row.rid})" in cypher
        assert params["rows"] == nodes

    async def test_upsert_nodes_batch_single_prop_key(self, store, mock_driver):
        """节点只有 rid 时 SET 子句只有 n.rid。"""
        nodes = [{"rid": "r1"}]
        result = await store.upsert_nodes_batch("Solo", nodes)
        assert result == 1
        cypher = mock_driver.execute_query.call_args.args[0]
        assert "SET n.rid = row.rid" in cypher
        # 无额外 prop_keys 时不应有逗号
        assert "row.rid," not in cypher

    async def test_upsert_edges_batch_empty_returns_zero(self, store):
        result = await store.upsert_edges_batch("REL", "A", "B", [])
        assert result == 0

    async def test_upsert_edges_batch_writes_all(self, store, mock_driver):
        edges = [("s1", "t1"), ("s2", "t2")]
        result = await store.upsert_edges_batch("LINKED", "Src", "Tgt", edges)
        assert result == 2
        cypher = mock_driver.execute_query.call_args.args[0]
        params = mock_driver.execute_query.call_args.kwargs["parameters_"]
        assert "UNWIND $rows AS row" in cypher
        assert "MATCH (s:Src {rid: row.s})" in cypher
        assert "(t:Tgt {rid: row.t})" in cypher
        assert "MERGE (s)-[r:LINKED]->(t)" in cypher
        assert len(params["rows"]) == 2
        assert params["rows"][0] == {"s": "s1", "t": "t1"}


class TestCleanupStaleVirtual:
    """ADR-021 §2.4：watermark + cleanup 孤儿清理。"""

    async def test_cleanup_deletes_stale_virtual_nodes(self, store, mock_driver):
        deleted_count = 5
        mock_driver.execute_query.return_value = _result_with([{"deleted": deleted_count}])
        result = await store.cleanup_stale_virtual("Foo", 1700000000)
        assert result == deleted_count
        cypher = mock_driver.execute_query.call_args.args[0]
        params = mock_driver.execute_query.call_args.kwargs["parameters_"]
        assert "MATCH (n:Foo {_virtual: true})" in cypher
        assert "WHERE n._sync_tag <> $current_tag" in cypher
        assert "DETACH DELETE n" in cypher
        assert params["current_tag"] == 1700000000

    async def test_cleanup_no_stale_returns_zero(self, store, mock_driver):
        mock_driver.execute_query.return_value = _result_with([{"deleted": 0}])
        result = await store.cleanup_stale_virtual("Foo", 1700000000)
        assert result == 0

    async def test_cleanup_empty_result_returns_zero(self, store, mock_driver):
        """execute_query 返回空 records 时返回 0（不报错）。"""
        mock_driver.execute_query.return_value = _result_with([])
        result = await store.cleanup_stale_virtual("Foo", 1700000000)
        assert result == 0
