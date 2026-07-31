"""traverse_link_logic 测试：Neo4j 主路径 + PG 降级 + source_to_target_map 多源正确性。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.tools.toolsets.link_traversal import traverse_link_logic


@asynccontextmanager
async def _fake_metadata_session(meta: MagicMock):
    """模拟 container.metadata_session() async context manager。"""
    try:
        yield meta
    finally:
        pass


@pytest.fixture
def executor() -> MagicMock:
    """构造带 mock container 的 ToolExecutor。"""
    exe = MagicMock()
    exe.container = MagicMock()
    exe.container.graph_store = AsyncMock()
    exe.container.dataframe_query_service = MagicMock()

    # _resolve_target_label / _resolve_source_label 返回有效 label
    exe.container.dataframe_query_service._resolve_target_label = AsyncMock(return_value="SC__Supplier")
    exe.container.dataframe_query_service._resolve_source_label = AsyncMock(return_value="SC__Supplier")
    # _resolve_link_endpoint_ot 返回源端 ObjectType（带 api_name + primary_key）
    src_ot = MagicMock()
    src_ot.api_name = "Supplier"
    src_ot.primary_key = "supplier_id"
    exe.container.dataframe_query_service._resolve_link_endpoint_ot = AsyncMock(return_value=src_ot)
    # _resolve_rids_by_pk：pk → rid 翻译（测试中 pk "S001" → vid "rid-S001"）
    async def _fake_resolve_rids(_ont: str, _ot: str, pks: list[str]) -> list[str]:
        return [f"rid-{pk}" for pk in pks]
    exe.container.dataframe_query_service._resolve_rids_by_pk = _fake_resolve_rids

    # mock metadata store（通过 metadata_session() async context manager 暴露）
    from ontology.core.models.ontology import OntologyModel

    meta = AsyncMock()
    onto = MagicMock(spec=OntologyModel)
    onto.id = "ont-1"
    meta.get_ontology = AsyncMock(return_value=onto)
    exe.container._mock_meta = meta  # 保留引用供各测试配置
    exe.container.metadata_session = lambda: _fake_metadata_session(meta)

    # audit_call 透传 awaitable
    async def audit_call(_name: str, _params: dict, coro):
        return await coro

    exe.audit_call = audit_call
    return exe


@pytest.mark.asyncio
async def test_traverse_neo4j_success(executor: MagicMock) -> None:
    """Neo4j 成功时返回 target_objects + source_to_target_map（查 PG 补全映射）。"""
    from ontology.layers.graph.neo4j_graph_store import GraphTraversalResult

    executor.container.graph_store.search_around = AsyncMock(
        return_value=GraphTraversalResult(rids=["t1", "t2"], matched_count=2)
    )
    executor.container._mock_meta.query_object_links_batch = AsyncMock(
        return_value={"rid-s1": ["t1", "t2"]}
    )
    executor.container._mock_meta.get_object_states_by_rids = AsyncMock(
        return_value=[
            {"rid": "t1", "object_type_api_name": "Order", "properties": {"amt": 100}},
            {"rid": "t2", "object_type_api_name": "Order", "properties": {"amt": 200}},
        ]
    )

    result = await traverse_link_logic(
        executor, "SC", "supplies", ["s1"], "forward", include_source_mapping=True
    )
    assert len(result["target_objects"]) == 2
    assert result["source_to_target_map"]["s1"] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_traverse_pg_fallback_on_neo4j_failure(executor: MagicMock) -> None:
    """Neo4j 抛异常 → 降级 PG object_links，仍返回正确结果。"""
    executor.container.graph_store.search_around = AsyncMock(side_effect=ConnectionError("Neo4j down"))
    executor.container._mock_meta.query_object_links_batch = AsyncMock(
        return_value={"rid-s1": ["t1"], "rid-s2": ["t2", "t3"]}
    )
    executor.container._mock_meta.get_object_states_by_rids = AsyncMock(
        return_value=[{"rid": "t1", "object_type_api_name": "Order", "properties": {}}]
    )

    result = await traverse_link_logic(
        executor, "SC", "supplies", ["s1", "s2"], "forward", include_source_mapping=True
    )
    # 降级后仍返回 target_objects（去重后的 t1/t2/t3，但只 mock 了 t1 的 state）
    assert len(result["target_objects"]) >= 1
    # 多源映射正确（每个 source 各自的 target，不再是统一列表）
    assert result["source_to_target_map"]["s1"] == ["t1"]
    assert result["source_to_target_map"]["s2"] == ["t2", "t3"]


@pytest.mark.asyncio
async def test_traverse_source_to_target_map_not_unified(executor: MagicMock) -> None:
    """多源时 source_to_target_map 各自独立（修复 MVP bug：所有源映射到同一批 target）。"""
    from ontology.layers.graph.neo4j_graph_store import GraphTraversalResult

    executor.container.graph_store.search_around = AsyncMock(
        return_value=GraphTraversalResult(rids=["t1", "t2"], matched_count=2)
    )
    # PG 返回不同 source 的不同 target
    executor.container._mock_meta.query_object_links_batch = AsyncMock(
        return_value={"rid-s1": ["t1"], "rid-s2": ["t2"]}
    )
    executor.container._mock_meta.get_object_states_by_rids = AsyncMock(return_value=[])

    result = await traverse_link_logic(
        executor, "SC", "supplies", ["s1", "s2"], "forward", include_source_mapping=True
    )
    assert result["source_to_target_map"]["s1"] == ["t1"]
    assert result["source_to_target_map"]["s2"] == ["t2"]
    # 关键：s1 不应包含 t2，s2 不应包含 t1
    assert "t2" not in result["source_to_target_map"]["s1"]
    assert "t1" not in result["source_to_target_map"]["s2"]


@pytest.mark.asyncio
async def test_traverse_reverse_direction(executor: MagicMock) -> None:
    """reverse 方向：target_rid 在 source_rids 中。"""
    from ontology.layers.graph.neo4j_graph_store import GraphTraversalResult

    executor.container.graph_store.search_around = AsyncMock(
        return_value=GraphTraversalResult(rids=["src1"], matched_count=1)
    )
    executor.container._mock_meta.query_object_links_batch = AsyncMock(
        return_value={"rid-tgt1": ["src1"]}
    )
    executor.container._mock_meta.get_object_states_by_rids = AsyncMock(
        return_value=[{"rid": "src1", "object_type_api_name": "Supplier", "properties": {}}]
    )

    result = await traverse_link_logic(
        executor, "SC", "supplies", ["tgt1"], "reverse", include_source_mapping=True
    )
    assert result["source_to_target_map"]["tgt1"] == ["src1"]
