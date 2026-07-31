"""Unit tests for GraphProjector (graph-reasoning-design.md §6.2)."""

from unittest.mock import AsyncMock

import pytest

from ontology.core.schemas.graph import EdgeProps
from ontology.core.schemas.ontology import ObjectType, PropertyDef
from ontology.services.graph_projector import GraphProjector


@pytest.fixture
def mock_graph_store() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def projector(mock_metadata, mock_graph_store) -> GraphProjector:
    return GraphProjector(metadata=mock_metadata, graph_store=mock_graph_store)


def _make_ot(
    api_name: str = "Supplier",
    indexed: list[str] | None = None,
    storage_type: str = "MANAGED",
) -> ObjectType:
    from datetime import UTC, datetime
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    indexed = indexed or []
    props = []
    for name in ["status", "region", "name"]:
        props.append(
            PropertyDef(
                id=f"p_{name}",
                object_type_id="ot1",
                api_name=name,
                display_name=name,
                data_type="STRING",
                indexed=name in indexed,
                created_at=ts,
                updated_at=ts,
            )
        )
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        primary_key="id",
        title_property="name",
        storage_type=storage_type,
        properties=props,
        created_at=ts,
        updated_at=ts,
    )


class TestProjectObject:
    async def test_projects_only_indexed_props(self, projector, mock_metadata, mock_graph_store):
        mock_metadata.get_object_type = AsyncMock(return_value=_make_ot(indexed=["status", "region"]))
        await projector.project_object(
            "SupplyChain", "Supplier",
            {"rid": "vid-1", "properties": {"status": "ACTIVE", "region": "east", "name": "Acme"}},
        )
        mock_graph_store.upsert_node.assert_awaited_once()
        label, rid, props = mock_graph_store.upsert_node.call_args.args
        assert label == "SupplyChainSupplier"
        assert rid == "vid-1"
        # indexed 属性被投影。
        assert props["status"] == "ACTIVE"
        assert props["region"] == "east"
        # 非 indexed 属性不投影。
        assert "name" not in props
        # rid + api_name + visibility 总是存在。
        assert props["rid"] == "vid-1"
        assert props["api_name"] == "Supplier"
        assert props["visibility"] == "NORMAL"

    async def test_reads_flat_or_nested_properties(self, projector, mock_metadata, mock_graph_store):
        """object_state 可能是扁平（属性在顶层）或嵌套（在 properties 里）。"""
        mock_metadata.get_object_type = AsyncMock(return_value=_make_ot(indexed=["status"]))
        # 扁平形式
        await projector.project_object(
            "SC", "Supplier", {"rid": "v1", "status": "ACTIVE"}
        )
        _, _, props = mock_graph_store.upsert_node.call_args.args
        assert props["status"] == "ACTIVE"

    async def test_visibility_from_state(self, projector, mock_metadata, mock_graph_store):
        mock_metadata.get_object_type = AsyncMock(return_value=_make_ot(indexed=[]))
        await projector.project_object(
            "SC", "Supplier", {"rid": "v1", "visibility": "HIDDEN", "properties": {}}
        )
        _, _, props = mock_graph_store.upsert_node.call_args.args
        assert props["visibility"] == "HIDDEN"


class TestProjectLink:
    async def test_projects_edge_with_naming(self, projector, mock_graph_store):
        await projector.project_link(
            "SupplyChain", "supplies", "Supplier", "s1", "Order", "o1",
            EdgeProps(weight=0.5),
        )
        mock_graph_store.upsert_edge.assert_awaited_once()
        args = mock_graph_store.upsert_edge.call_args.args
        rel_type, src_label, src_rid, tgt_label, tgt_rid, edge_props = args
        assert rel_type == "SupplyChainSupplies"
        assert src_label == "SupplyChainSupplier"
        assert tgt_label == "SupplyChainOrder"
        assert src_rid == "s1"
        assert tgt_rid == "o1"
        assert edge_props.weight == 0.5


class TestDeleteObject:
    async def test_delete_node_detach(self, projector, mock_graph_store):
        await projector.delete_object("SC", "Supplier", "v1")
        mock_graph_store.delete_node.assert_awaited_once_with("SCSupplier", "v1")


class TestRebuild:
    async def test_rebuild_projects_all_states(self, projector, mock_metadata, mock_graph_store):
        mock_metadata.get_object_type = AsyncMock(return_value=_make_ot(indexed=["status"]))
        states = [
            {"rid": "v1", "status": "ACTIVE"},
            {"rid": "v2", "status": "INACTIVE"},
            {"rid": "v3", "status": "ACTIVE"},
        ]
        count = await projector.rebuild_for_object_type("SC", "Supplier", states)
        assert count == 3
        assert mock_graph_store.upsert_node.await_count == 3

    async def test_rebuild_tolerates_single_failure(self, projector, mock_metadata, mock_graph_store):
        mock_metadata.get_object_type = AsyncMock(return_value=_make_ot(indexed=["status"]))
        mock_graph_store.upsert_node.side_effect = [
            None,  # v1 ok
            RuntimeError("boom"),  # v2 fails
            None,  # v3 ok
        ]
        states = [{"rid": f"v{i}", "status": "X"} for i in range(1, 4)]
        count = await projector.rebuild_for_object_type("SC", "Supplier", states)
        # v2 失败但 v3 继续，count=2（成功数）。
        assert count == 2


class TestVirtualProjection:
    """ADR-021 §2.2：VIRTUAL 节点身份骨架元标记投影。"""

    async def test_virtual_writes_meta_markers(self, projector, mock_metadata, mock_graph_store):
        """VIRTUAL object_state 触发 _virtual/_source_ref/_sync_tag 写入。"""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot("Order", indexed=["status"], storage_type="VIRTUAL")
        )
        await projector.project_object(
            "Shop", "Order",
            {
                "rid": "ri.ontology.main.virtual-object.Shop.Order.42",
                "properties": {"id": "42", "status": "PAID", "name": "Order-42"},
                "_virtual": True,
                "_source_ref": "mysql_shop.orders",
                "_sync_tag": 1700000000,
            },
        )
        mock_graph_store.upsert_node.assert_awaited_once()
        _, _, props = mock_graph_store.upsert_node.call_args.args
        assert props["_virtual"] is True
        assert props["_source_ref"] == "mysql_shop.orders"
        assert props["_sync_tag"] == 1700000000
        # title 字段（name）也写入
        assert props["name"] == "Order-42"
        # PK 值强制写入（D5）
        assert props["id"] == "42"
        # indexed 属性照常
        assert props["status"] == "PAID"

    async def test_managed_does_not_write_virtual_markers(self, projector, mock_metadata, mock_graph_store):
        """MANAGED object_state 不写 _virtual 元标记（回归测试）。"""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot("Supplier", indexed=["status"])
        )
        await projector.project_object(
            "SC", "Supplier",
            {"rid": "r1", "properties": {"id": "S1", "status": "ACTIVE", "name": "Acme"}},
        )
        _, _, props = mock_graph_store.upsert_node.call_args.args
        assert "_virtual" not in props
        assert "_source_ref" not in props
        assert "_sync_tag" not in props

    async def test_virtual_without_title_property(self, projector, mock_metadata, mock_graph_store):
        """title_property 为空时不写 title（不报错）。"""
        ot = _make_ot("Note", indexed=[], storage_type="VIRTUAL")
        ot.title_property = ""
        mock_metadata.get_object_type = AsyncMock(return_value=ot)
        await projector.project_object(
            "App", "Note",
            {
                "rid": "ri.ontology.main.virtual-object.App.Note.1",
                "properties": {"id": "1"},
                "_virtual": True,
                "_source_ref": "pg.notes",
                "_sync_tag": 100,
            },
        )
        _, _, props = mock_graph_store.upsert_node.call_args.args
        assert props["_virtual"] is True
        # 无 title_property 时不写 title 键
        assert "name" not in props


class TestProjectLinksBatch:
    """ADR-021 §2.3：批量边投影薄包装。"""

    async def test_empty_edges_returns_zero(self, projector, mock_graph_store):
        result = await projector.project_links_batch("SC", "link", "A", "B", [])
        assert result == 0
        mock_graph_store.upsert_edges_batch.assert_not_awaited()

    async def test_delegates_to_upsert_edges_batch(self, projector, mock_graph_store):
        edges = [("r1", "r2"), ("r3", "r4")]
        mock_graph_store.upsert_edges_batch = AsyncMock(return_value=2)
        result = await projector.project_links_batch("SC", "orderLinks", "Order", "Supplier", edges)
        assert result == 2
        mock_graph_store.upsert_edges_batch.assert_awaited_once()
        call = mock_graph_store.upsert_edges_batch.call_args
        rel_type, src_label, tgt_label, passed_edges = call.args
        assert rel_type == "SCOrderLinks"
        assert src_label == "SCOrder"
        assert tgt_label == "SCSupplier"
        assert passed_edges == edges
