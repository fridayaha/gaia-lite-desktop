"""Unit tests for ObjectIndexFunnel (external Iceberg → graph/geotime projection)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.ontology import (
    DataType,
    ObjectType,
    ObjectTypeCapabilities,
    PropertyDef,
)
from ontology.services.object_index_funnel import ObjectIndexFunnel

_TS = datetime(2026, 1, 1, tzinfo=UTC)


# ── fixtures ──


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_dataset() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_graph_projector() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_geotime_projector() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_index_store() -> AsyncMock:
    from ontology.layers.index.doris_index_store import DorisIndexStore

    store = AsyncMock(spec=DorisIndexStore)
    # Default: no existing rids (all PKs absent → fresh allocation).
    store.get_rids_by_pks = AsyncMock(return_value={})
    return store

@pytest.fixture
def svc(mock_metadata, mock_dataset, mock_graph_projector, mock_geotime_projector, mock_index_store):
    return ObjectIndexFunnel(
        metadata=mock_metadata,
        dataset=mock_dataset,
        graph_projector=mock_graph_projector,
        geotime_projector=mock_geotime_projector,
        index_store=mock_index_store,
    )


@pytest.fixture
def mock_engine() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_object_query() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def svc_virtual(
    mock_metadata, mock_dataset, mock_graph_projector, mock_geotime_projector,
    mock_index_store, mock_engine, mock_object_query,
):
    """ObjectIndexFunnel with Trino engine + object_query injected (VIRTUAL projection)."""
    return ObjectIndexFunnel(
        metadata=mock_metadata,
        dataset=mock_dataset,
        graph_projector=mock_graph_projector,
        geotime_projector=mock_geotime_projector,
        index_store=mock_index_store,
        engine=mock_engine,
        object_query=mock_object_query,
    )


def _make_ot(
    api_name: str = "Supplier",
    storage_type: str = "MANAGED",
    caps: ObjectTypeCapabilities | None = None,
    props: list[PropertyDef] | None = None,
) -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        primary_key="id",
        title_property="name",
        storage_type=storage_type,  # type: ignore[arg-type]
        capabilities=caps or ObjectTypeCapabilities(),
        properties=props or [],
        created_at=_TS,
        updated_at=_TS,
    )


def _prop(name: str, dt: DataType, indexed: bool = False) -> PropertyDef:
    return PropertyDef(
        id=f"p_{name}",
        object_type_id="ot1",
        api_name=name,
        display_name=name,
        data_type=dt,
        indexed=indexed,
        created_at=_TS,
        updated_at=_TS,
    )


# ── project_for_object_type ──


class TestProjectForObjectType:
    async def test_virtual_skipped(self, svc, mock_metadata, mock_dataset):
        """VIRTUAL type: no data to project, skip entirely (Gate 1)."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(storage_type="VIRTUAL")
        )
        result = await svc.project_for_object_type("SC", "VirtualOT")
        assert result == {"graph": 0, "geotime": 0}
        mock_dataset.scan_latest.assert_not_awaited()

    async def test_no_capabilities_skipped(self, svc, mock_metadata, mock_dataset):
        """Neither graph nor geotime enabled: skip Iceberg read (Gate 4)."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(
                    graph_indexing_enabled=False,
                    geotime_indexing_enabled=False,
                )
            )
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 0, "geotime": 0}
        mock_dataset.scan_latest.assert_not_awaited()

    async def test_graph_only_projection(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector, mock_geotime_projector,
    ):
        """graph_indexing_enabled=True → only graph projector called."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
                props=[_prop("name", DataType.STRING, indexed=True)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[
                {"object_id": "v1", "name": "Acme"},
                {"object_id": "v2", "name": "Beta"},
            ]
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 2, "geotime": 0}
        assert mock_graph_projector.project_object.await_count == 2
        mock_geotime_projector.project_object.assert_not_awaited()

    async def test_geotime_only_projection(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector, mock_geotime_projector,
    ):
        """geotime_indexing_enabled=True → only geotime projector called."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(geotime_indexing_enabled=True),
                props=[_prop("loc", DataType.GEOPOINT)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[
                {"object_id": "v1", "loc": [116.4, 39.9]},
            ]
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 0, "geotime": 1}
        mock_graph_projector.project_object.assert_not_awaited()
        assert mock_geotime_projector.project_object.await_count == 1

    async def test_both_projections_called(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector, mock_geotime_projector,
    ):
        """Both capabilities enabled → both projectors called for each row."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(
                    graph_indexing_enabled=True,
                    geotime_indexing_enabled=True,
                ),
                props=[
                    _prop("name", DataType.STRING, indexed=True),
                    _prop("loc", DataType.GEOPOINT),
                ],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[
                {"object_id": "v1", "name": "Acme", "loc": [116.4, 39.9]},
                {"object_id": "v2", "name": "Beta", "loc": [121.5, 31.2]},
            ]
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 2, "geotime": 2}
        assert mock_graph_projector.project_object.await_count == 2
        assert mock_geotime_projector.project_object.await_count == 2

    async def test_empty_iceberg(self, svc, mock_metadata, mock_dataset, mock_graph_projector):
        """Empty Iceberg table: no projection, log info."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
            )
        )
        mock_dataset.scan_latest = AsyncMock(return_value=[])
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 0, "geotime": 0}
        mock_graph_projector.project_object.assert_not_awaited()

    async def test_fail_tolerant_per_row(self, svc, mock_metadata, mock_dataset, mock_graph_projector):
        """Single row projection failure doesn't block the rest."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
                props=[_prop("name", DataType.STRING, indexed=True)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[
                {"object_id": "v1", "name": "Good"},
                {"object_id": "v2", "name": "Bad"},
                {"object_id": "v3", "name": "Good2"},
            ]
        )
        # Row 2 (v2) fails, others succeed.
        mock_graph_projector.project_object = AsyncMock(
            side_effect=[None, RuntimeError("Neo4j timeout"), None]
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 2, "geotime": 0}  # v2 skipped
        assert mock_graph_projector.project_object.await_count == 3

    async def test_ot_not_found_graceful(self, svc, mock_metadata, mock_dataset):
        """If get_object_type raises, return zero gracefully."""
        mock_metadata.get_object_type = AsyncMock(
            side_effect=ValueError("OT not found")
        )
        result = await svc.project_for_object_type("SC", "Nonexistent")
        assert result == {"graph": 0, "geotime": 0}
        mock_dataset.scan_latest.assert_not_awaited()

    async def test_iceberg_scan_failure_graceful(self, svc, mock_metadata, mock_dataset):
        """If Iceberg scan fails, return zero gracefully."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            side_effect=RuntimeError("Trino connection refused")
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 0, "geotime": 0}

    async def test_projectors_none_skips(self, mock_metadata, mock_dataset):
        """When projectors are None, skip even if capabilities enabled."""
        svc_no_proj = ObjectIndexFunnel(
            metadata=mock_metadata,
            dataset=mock_dataset,
            graph_projector=None,
            geotime_projector=None,
        )
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(
                    graph_indexing_enabled=True,
                    geotime_indexing_enabled=True,
                ),
            )
        )
        result = await svc_no_proj.project_for_object_type("SC", "Supplier")
        assert result == {"graph": 0, "geotime": 0}
        mock_dataset.scan_latest.assert_not_awaited()

    async def test_custom_dataset_api_name(self, svc, mock_metadata, mock_dataset, mock_graph_projector):
        """Explicit dataset_api_name overrides the default snake_case."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
            )
        )
        mock_dataset.scan_latest = AsyncMock(return_value=[])
        await svc.project_for_object_type(
            "SC", "FlightStatus",
            dataset_api_name="custom_flight_log",
        )
        # Should query Iceberg with the custom name, not "flight_status"
        call_args = mock_dataset.scan_latest.call_args.args
        assert "custom_flight_log" in str(call_args)


# ── rid assignment / reuse (T1.4, handoff-rid-funnel-closure.md) ──


class TestRidAssignment:
    """rid reuse-or-generate + Doris upsert + correct projector object_state."""

    async def test_rid_allocated_for_new_rows(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector, mock_index_store,
    ):
        """PK absent in Doris → fresh rid allocated + written to Doris idx."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
                props=[_prop("name", DataType.STRING, indexed=True)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[{"id": "s1", "name": "Acme"}]
        )
        # mock_index_store.get_rids_by_pks defaults to {} (all absent).
        await svc.project_for_object_type("SC", "Supplier")
        # Doris upsert called with a record that includes a freshly-allocated rid.
        mock_index_store.upsert.assert_awaited_once()
        records = mock_index_store.upsert.await_args.args[2]
        assert records[0]["rid"].startswith("ri.ontology.main.object.")
        # projector received the rid in object_state (NOT the old {"id": ...} bug).
        call = mock_graph_projector.project_object.await_args
        object_state = call.args[2]
        assert object_state["rid"] == records[0]["rid"]
        assert "id" not in object_state  # regression: old bug passed {"id": pk}

    async def test_rid_reused_for_existing_rows(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector, mock_index_store,
    ):
        """PK already in Doris → existing rid reused (no new allocation)."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
                props=[_prop("name", DataType.STRING, indexed=True)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[{"id": "s1", "name": "Acme"}]
        )
        existing_rid = "ri.ontology.main.object.reuse-me"
        mock_index_store.get_rids_by_pks = AsyncMock(return_value={"s1": existing_rid})
        await svc.project_for_object_type("SC", "Supplier")
        records = mock_index_store.upsert.await_args.args[2]
        assert records[0]["rid"] == existing_rid  # reused, not new

    async def test_rid_without_index_store_fails_soft(
        self, mock_metadata, mock_dataset, mock_graph_projector, mock_geotime_projector,
    ):
        """No index_store wired → still projects (allocates fresh rids, no Doris write)."""
        svc = ObjectIndexFunnel(
            metadata=mock_metadata, dataset=mock_dataset,
            graph_projector=mock_graph_projector, geotime_projector=mock_geotime_projector,
            # index_store=None
        )
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
                props=[_prop("name", DataType.STRING, indexed=True)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[{"id": "s1", "name": "Acme"}]
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        assert result["graph"] == 1  # projection happened despite no Doris write
        mock_graph_projector.project_object.assert_awaited_once()

    async def test_doris_get_rids_failure_fails_soft(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector, mock_index_store,
    ):
        """Doris get_rids_by_pks raises → fail-soft (allocate fresh rids, continue)."""
        from ontology.core.exceptions import DorisUnavailableError

        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
                props=[_prop("name", DataType.STRING, indexed=True)],
            )
        )
        mock_dataset.scan_latest = AsyncMock(
            return_value=[{"id": "s1", "name": "Acme"}]
        )
        mock_index_store.get_rids_by_pks = AsyncMock(
            side_effect=DorisUnavailableError("Doris down")
        )
        result = await svc.project_for_object_type("SC", "Supplier")
        # Projection continued with a fresh rid despite Doris being unavailable.
        assert result["graph"] == 1


# ── project_for_dataset ──


class TestProjectForDataset:
    async def test_no_object_types_returns_empty(self, svc, mock_metadata):
        """No ObjectType references the dataset → empty result."""
        mock_metadata.get_object_types_for_dataset = AsyncMock(return_value=[])
        result = await svc.project_for_dataset("some_dataset")
        assert result == {}

    async def test_calls_project_for_each_object_type(
        self, svc, mock_metadata, mock_dataset, mock_graph_projector,
    ):
        """Each referenced ObjectType triggers project_for_object_type.

        Mocks get_ontology_api_names_by_ids (the public metadata method) so
        the full path runs without a real DB.
        """
        ot1 = _make_ot(api_name="Supplier")
        ot2 = _make_ot(api_name="Product")
        mock_metadata.get_object_types_for_dataset = AsyncMock(return_value=[ot1, ot2])
        mock_metadata.get_ontology_api_names_by_ids = AsyncMock(
            return_value={"o1": "SC"}
        )
        mock_dataset.scan_latest = AsyncMock(return_value=[])
        result = await svc.project_for_dataset("ds1")
        # Both OTs processed (empty Iceberg → 0 projections each).
        assert result == {"Supplier": {"graph": 0, "geotime": 0},
                          "Product": {"graph": 0, "geotime": 0}}

    async def test_skips_ot_when_ontology_unresolvable(
        self, svc, mock_metadata, mock_dataset,
    ):
        """OT whose ontology_id can't be resolved is skipped with a warning."""
        ot1 = _make_ot(api_name="Orphan")
        mock_metadata.get_object_types_for_dataset = AsyncMock(return_value=[ot1])
        mock_metadata.get_ontology_api_names_by_ids = AsyncMock(return_value={})
        result = await svc.project_for_dataset("ds1")
        assert result == {}


# ── _extract_pk (helper) ──


class TestExtractPk:
    """Tests for the PK extraction helper (None/0/False/case-insensitive)."""

    def test_exact_match(self):
        assert ObjectIndexFunnel._extract_pk({"id": "S001"}, "id") == "S001"

    def test_object_id_fallback(self):
        assert ObjectIndexFunnel._extract_pk({"object_id": "O1"}, "id") == "O1"

    def test_case_insensitive_match(self):
        """Iceberg snake_case vs OT camelCase."""
        assert ObjectIndexFunnel._extract_pk(
            {"supplierid": "S001"}, "supplierId"
        ) == "S001"

    def test_none_value_returns_none(self):
        """None PK should not become string 'None'."""
        assert ObjectIndexFunnel._extract_pk({"id": None}, "id") is None

    def test_zero_value_preserved(self):
        """Numeric 0 is a valid PK — must not be treated as falsy."""
        assert ObjectIndexFunnel._extract_pk({"id": 0}, "id") == "0"

    def test_false_value_preserved(self):
        """False is a valid (if unusual) PK — must not be skipped."""
        assert ObjectIndexFunnel._extract_pk({"id": False}, "id") == "False"

    def test_empty_string_returns_none(self):
        """Empty string is treated as no PK."""
        assert ObjectIndexFunnel._extract_pk({"id": ""}, "id") is None

    def test_missing_key_returns_none(self):
        assert ObjectIndexFunnel._extract_pk({"other": "x"}, "id") is None


# ── limit clamping ──


class TestLimitClamping:
    async def test_limit_clamped_to_max(self, svc, mock_metadata, mock_dataset):
        """limit > _MAX_LIMIT is clamped down."""
        from ontology.services.object_index_funnel import _MAX_LIMIT

        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
            )
        )
        mock_dataset.scan_latest = AsyncMock(return_value=[])
        await svc.project_for_object_type("SC", "Supplier", limit=10_000_000)
        # scan_latest should have been called with the clamped limit.
        call_kwargs = mock_dataset.scan_latest.call_args.kwargs
        assert call_kwargs["limit"] == _MAX_LIMIT

    async def test_limit_floor_one(self, svc, mock_metadata, mock_dataset):
        """limit < 1 is clamped up to 1."""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot(
                caps=ObjectTypeCapabilities(graph_indexing_enabled=True),
            )
        )
        mock_dataset.scan_latest = AsyncMock(return_value=[])
        await svc.project_for_object_type("SC", "Supplier", limit=0)
        call_kwargs = mock_dataset.scan_latest.call_args.kwargs
        assert call_kwargs["limit"] == 1


# ── project_for_virtual_object_type（ADR-021 PR 1） ──


class TestProjectForVirtualObjectType:
    """ADR-021 §2.1：VIRTUAL 身份骨架投影入口（旁路 Gate 1）。"""

    async def test_skipped_when_engine_not_injected(self, svc, mock_metadata):
        """无 Trino engine 注入时返回 partial + error（best-effort 不报错）。"""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot("Order", storage_type="VIRTUAL")
        )
        result = await svc.project_for_virtual_object_type("Shop", "Order")
        assert result["partial"] is True
        assert "not injected" in result["error"]
        assert result["nodes"] == 0

    async def test_skipped_when_graph_projector_none(
        self, mock_metadata, mock_dataset, mock_engine, mock_object_query
    ):
        """graph_projector 为 None 时返回 partial（Neo4j 未启动）。"""
        svc_no_proj = ObjectIndexFunnel(
            metadata=mock_metadata, dataset=mock_dataset,
            graph_projector=None, geotime_projector=None,
            index_store=None, engine=mock_engine, object_query=mock_object_query,
        )
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot("Order", storage_type="VIRTUAL")
        )
        result = await svc_no_proj.project_for_virtual_object_type("Shop", "Order")
        assert result["partial"] is True
        assert "GraphProjector" in result["error"]

    async def test_skipped_when_not_virtual(self, svc_virtual, mock_metadata):
        """非 VIRTUAL ObjectType 返回 partial + error。"""
        mock_metadata.get_object_type = AsyncMock(
            return_value=_make_ot("Supplier", storage_type="MANAGED")
        )
        result = await svc_virtual.project_for_virtual_object_type("SC", "Supplier")
        assert result["partial"] is True
        assert "not VIRTUAL" in result["error"]

    async def test_skipped_when_no_primary_key(self, svc_virtual, mock_metadata):
        """无 primary_key 返回 partial + error。"""
        ot = _make_ot("Order", storage_type="VIRTUAL")
        ot.primary_key = ""
        mock_metadata.get_object_type = AsyncMock(return_value=ot)
        result = await svc_virtual.project_for_virtual_object_type("Shop", "Order")
        assert result["partial"] is True
        assert "primary_key" in result["error"]

    async def test_projects_nodes_via_trino_cursor_pagination(
        self, svc_virtual, mock_metadata, mock_engine, mock_object_query,
        mock_graph_projector,
    ):
        """正常路径：游标分页拉 Trino + 合成 object_state + project_object + cleanup。"""
        ot = _make_ot(
            "Order", storage_type="VIRTUAL",
            props=[_prop("id", DataType.STRING), _prop("status", DataType.STRING, indexed=True),
                   _prop("name", DataType.STRING)],
        )
        mock_metadata.get_object_type = AsyncMock(return_value=ot)
        mock_object_query._virtual_table_ref = AsyncMock(return_value="mysql_shop.orders")
        # 无 LinkType（边投影返回 0）
        mock_metadata.get_link_types = AsyncMock(return_value=[])

        # Trino 游标分页：首批返回 batch_size 行（触发翻页），次批返回空（结束）。
        batch = [{"id": f"O{i}", "status": "PAID", "name": f"Order-{i}"} for i in range(1, 3)]
        mock_engine.query = AsyncMock(side_effect=[batch, []])
        # cleanup 返回删除 0（通过 graph_projector 薄包装调用）
        mock_graph_projector.cleanup_stale_virtual = AsyncMock(return_value=0)

        result = await svc_virtual.project_for_virtual_object_type("Shop", "Order", batch_size=2)

        assert result["nodes"] == 2
        assert result["cleaned"] == 0
        assert result["partial"] is False
        # project_object 被调 2 次（每行一次）
        assert mock_graph_projector.project_object.await_count == 2
        # 第一次调用的 object_state 含 VIRTUAL 元标记
        first_call = mock_graph_projector.project_object.call_args_list[0]
        state = first_call.args[2]
        assert state["_virtual"] is True
        assert state["_source_ref"] == "mysql_shop.orders"
        assert "_sync_tag" in state
        assert state["rid"].startswith("ri.ontology.main.virtual-object.Shop.Order.")
        # Trino 游标分页：第二次 query 带 WHERE pk > $last（首批满 batch 触发翻页）
        assert mock_engine.query.await_count == 2
        second_sql = mock_engine.query.call_args_list[1].args[0]
        assert 'WHERE "id" > ?' in second_sql

    async def test_trino_failure_returns_partial(
        self, svc_virtual, mock_metadata, mock_engine, mock_object_query,
    ):
        """Trino 查询失败时返回 partial + error（best-effort，已投影节点保留）。"""
        ot = _make_ot(
            "Order", storage_type="VIRTUAL",
            props=[_prop("id", DataType.STRING)],
        )
        mock_metadata.get_object_type = AsyncMock(return_value=ot)
        mock_object_query._virtual_table_ref = AsyncMock(return_value="mysql_shop.orders")
        mock_engine.query = AsyncMock(side_effect=RuntimeError("Trino connection refused"))

        result = await svc_virtual.project_for_virtual_object_type("Shop", "Order")
        assert result["partial"] is True
        assert "Trino connection refused" in result["error"]
        assert result["nodes"] == 0

    async def test_uses_backing_column_for_select(
        self, svc_virtual, mock_metadata, mock_engine, mock_object_query,
        mock_graph_projector,
    ):
        """属性有 backing_column 时，SELECT 用物理列名而非 api_name。"""
        ot = _make_ot(
            "Order", storage_type="VIRTUAL",
            props=[
                PropertyDef(
                    id="p_id", object_type_id="ot1", api_name="orderId",
                    display_name="orderId", data_type=DataType.STRING,
                    backing_column="order_id", created_at=_TS, updated_at=_TS,
                ),
            ],
        )
        ot.primary_key = "orderId"
        mock_metadata.get_object_type = AsyncMock(return_value=ot)
        mock_object_query._virtual_table_ref = AsyncMock(return_value="mysql.orders")
        mock_engine.query = AsyncMock(return_value=[])

        await svc_virtual.project_for_virtual_object_type("Shop", "Order")
        sql = mock_engine.query.call_args.args[0]
        # backing_column "order_id" 用于 SELECT，不是 api_name "orderId"
        assert '"order_id"' in sql
        assert '"orderId"' not in sql


# ── FK→边投影（ADR-021 PR 2） ──


def _make_link(
    api_name: str = "supplierOrders",
    src_ot_id: str = "src_ot",
    tgt_ot_id: str = "tgt_ot",
    fk_api: str | None = "supplierId",
) -> object:
    from ontology.core.schemas.ontology import LinkTypeDef
    return LinkTypeDef(
        id="lk1",
        ontology_id="o1",
        api_name=api_name,
        display_name=api_name,
        source_object_type_id=src_ot_id,
        target_object_type_id=tgt_ot_id,
        foreign_key_property_api_name=fk_api,
        cardinality="MANY",
        direction="OUTGOING",
        created_at=_TS,
        updated_at=_TS,
    )


def _make_ot_model(api_name: str, storage_type: str, primary_key: str, props: list) -> object:
    """Mock ObjectTypeModel（ORM 层，用于 get_object_type_by_id 返回）。"""
    from datetime import UTC, datetime
    m = AsyncMock()
    m.id = f"ot_{api_name}"
    m.api_name = api_name
    m.storage_type = storage_type
    m.primary_key = primary_key
    m.title_property = "name"
    m.properties = props
    m.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    m.updated_at = datetime(2026, 1, 1, tzinfo=UTC)
    return m


def _make_prop_model(api_name: str, backing_column: str) -> object:
    m = MagicMock()
    m.api_name = api_name
    m.backing_column = backing_column
    return m


class TestResolveFkBackingColumn:
    """ADR-021 §2.3 难点 1：FK 物理列名解析。"""

    def test_fk_in_source(self):
        src = _make_ot_model("Order", "VIRTUAL", "orderId",
                             [_make_prop_model("supplierId", "supplier_id")])
        tgt = _make_ot_model("Supplier", "MANAGED", "id", [])
        link = _make_link(fk_api="supplierId")
        result = ObjectIndexFunnel._resolve_fk_backing_column(link, src, tgt)
        assert result == ("supplier_id", src)

    def test_fk_in_target_fallback(self):
        """source 端无 FK 属性时，target 端兜底。"""
        src = _make_ot_model("Order", "VIRTUAL", "orderId", [])
        tgt = _make_ot_model("Supplier", "MANAGED", "id",
                             [_make_prop_model("supplierId", "supplier_id")])
        link = _make_link(fk_api="supplierId")
        result = ObjectIndexFunnel._resolve_fk_backing_column(link, src, tgt)
        assert result == ("supplier_id", tgt)

    def test_fk_api_none_returns_none(self):
        src = _make_ot_model("Order", "VIRTUAL", "orderId", [])
        tgt = _make_ot_model("Supplier", "MANAGED", "id", [])
        link = _make_link(fk_api=None)
        assert ObjectIndexFunnel._resolve_fk_backing_column(link, src, tgt) is None

    def test_fk_property_no_backing_column_returns_none(self):
        """FK 属性存在但未绑 backing_column → None（降级）。"""
        src = _make_ot_model("Order", "VIRTUAL", "orderId",
                             [_make_prop_model("supplierId", "")])
        tgt = _make_ot_model("Supplier", "MANAGED", "id", [])
        link = _make_link(fk_api="supplierId")
        assert ObjectIndexFunnel._resolve_fk_backing_column(link, src, tgt) is None


class TestProjectVirtualEdges:
    """ADR-021 §2.3：边投影三种形态。"""

    async def test_both_managed_skipped(self, svc_virtual, mock_metadata, mock_graph_projector):
        """两端都 MANAGED → 跳过（边由 Action Step 11 投影）。"""
        ot = _make_ot("Order", storage_type="VIRTUAL",
                      props=[_prop("id", DataType.STRING)])
        ot.id = "ot_order"
        mock_metadata.get_link_types = AsyncMock(return_value=[
            _make_link(src_ot_id="ot_a", tgt_ot_id="ot_b"),
        ])
        # 两端都返回 MANAGED model
        mock_metadata.get_object_type_by_id = AsyncMock(
            return_value=(_make_ot_model("A", "MANAGED", "id", []), "s1", "p1")
        )
        count = await svc_virtual._project_virtual_edges("SC", ot, sync_tag=100)
        assert count == 0
        mock_graph_projector.project_links_batch.assert_not_awaited()

    async def test_fk_missing_degraded(self, svc_virtual, mock_metadata, mock_graph_projector):
        """FK 缺失 → 边不投影，不报错（降级）。"""
        ot = _make_ot("Order", storage_type="VIRTUAL",
                      props=[_prop("id", DataType.STRING)])
        ot.id = "ot_order"
        mock_metadata.get_link_types = AsyncMock(return_value=[
            _make_link(fk_api=None),
        ])
        mock_metadata.get_object_type_by_id = AsyncMock(
            return_value=(_make_ot_model("Order", "VIRTUAL", "id", []), "s1", "p1")
        )
        count = await svc_virtual._project_virtual_edges("SC", ot, sync_tag=100)
        assert count == 0
        mock_graph_projector.project_links_batch.assert_not_awaited()

    async def test_virtual_managed_edges(
        self, svc_virtual, mock_metadata, mock_engine, mock_object_query,
        mock_graph_projector,
    ):
        """情况 3：一端 VIRTUAL 一端 MANAGED。

        Order(VIRTUAL) -[:supplierOrders]-> Supplier(MANAGED)
        FK supplierId 在 Order 端，指向 Supplier.id
        """
        order_ot = _make_ot("Order", storage_type="VIRTUAL",
                            props=[_prop("orderId", DataType.STRING),
                                   _prop("supplierId", DataType.STRING)])
        order_ot.id = "ot_order"
        order_ot.primary_key = "orderId"
        mock_metadata.get_object_type = AsyncMock(return_value=order_ot)

        # LinkType: source=Order(VIRTUAL), target=Supplier(MANAGED)
        link = _make_link(api_name="supplierOrders", src_ot_id="ot_order", tgt_ot_id="ot_supplier")

        # source OT model（VIRTUAL，含 FK 属性）
        src_model = _make_ot_model("Order", "VIRTUAL", "orderId",
                                   [_make_prop_model("orderId", "order_id"),
                                    _make_prop_model("supplierId", "supplier_id")])
        # target OT model（MANAGED）
        tgt_model = _make_ot_model("Supplier", "MANAGED", "id",
                                   [_make_prop_model("id", "id")])
        mock_metadata.get_link_types = AsyncMock(return_value=[link])
        mock_metadata.get_object_type_by_id = AsyncMock(
            side_effect=[(src_model, "s1", "p1"), (tgt_model, "s1", "p1")]
        )
        mock_object_query._virtual_table_ref = AsyncMock(return_value="mysql.orders")

        # Trino 返回 Order 行：(order_id, supplier_id)
        mock_engine.query = AsyncMock(return_value=[
            {"order_id": "O1", "supplier_id": "S1"},
            {"order_id": "O2", "supplier_id": "S2"},
        ])

        # MANAGED 端 PK→rid 反查：S1 存在，S2 悬空
        mock_metadata.get_object_states_by_pks = AsyncMock(return_value=[
            {"rid": "ri.ontology.main.object.S1-rid", "properties": {"id": "S1"}},
        ])

        # cleanup mock（_project_virtual_edges 不调 cleanup，但 project_for_virtual 会）
        mock_graph_projector.project_links_batch = AsyncMock(return_value=1)

        count = await svc_virtual._project_virtual_edges("SC", order_ot, sync_tag=100)

        # 悬空 FK S2 跳过，只 1 条边
        assert count == 1
        mock_graph_projector.project_links_batch.assert_awaited_once()
        call = mock_graph_projector.project_links_batch.call_args
        edges = call.args[4]
        assert len(edges) == 1
        # source 是 VIRTUAL rid，target 是 MANAGED rid
        assert edges[0][0].startswith("ri.ontology.main.virtual-object.SC.Order.O1")
        assert edges[0][1] == "ri.ontology.main.object.S1-rid"

    async def test_virtual_virtual_edges(
        self, svc_virtual, mock_metadata, mock_engine, mock_object_query,
        mock_graph_projector,
    ):
        """情况 2：两端都 VIRTUAL（内存 join）。

        Order(VIRTUAL) -[:refs]-> Note(VIRTUAL)
        FK noteId 在 Order 端，指向 Note.id
        """
        order_ot = _make_ot("Order", storage_type="VIRTUAL",
                            props=[_prop("orderId", DataType.STRING),
                                   _prop("noteId", DataType.STRING)])
        order_ot.id = "ot_order"
        order_ot.primary_key = "orderId"

        link = _make_link(api_name="orderNotes", src_ot_id="ot_order", tgt_ot_id="ot_note",
                           fk_api="noteId")

        src_model = _make_ot_model("Order", "VIRTUAL", "orderId",
                                   [_make_prop_model("orderId", "order_id"),
                                    _make_prop_model("noteId", "note_id")])
        tgt_model = _make_ot_model("Note", "VIRTUAL", "id",
                                   [_make_prop_model("id", "id")])
        mock_metadata.get_link_types = AsyncMock(return_value=[link])
        mock_metadata.get_object_type_by_id = AsyncMock(
            side_effect=[(src_model, "s1", "p1"), (tgt_model, "s1", "p1")]
        )
        # 两个不同 table ref
        mock_object_query._virtual_table_ref = AsyncMock(
            side_effect=["mysql.orders", "mysql.notes"]
        )

        # source Trino 返回 (order_id, note_id)
        # target Trino 返回 (id,)
        mock_engine.query = AsyncMock(side_effect=[
            [{"order_id": "O1", "note_id": "N1"},
             {"order_id": "O2", "note_id": "N3"}],  # N3 悬空
            [{"id": "N1"}, {"id": "N2"}],  # target 只有 N1, N2
        ])

        mock_graph_projector.project_links_batch = AsyncMock(return_value=1)

        count = await svc_virtual._project_virtual_edges("SC", order_ot, sync_tag=100)

        # 内存 join：O1->N1 匹配，O2->N3 不匹配（悬空）
        assert count == 1
        call = mock_graph_projector.project_links_batch.call_args
        edges = call.args[4]
        assert len(edges) == 1
        assert edges[0][0].startswith("ri.ontology.main.virtual-object.SC.Order.O1")
        assert edges[0][1].startswith("ri.ontology.main.virtual-object.SC.Note.N1")

    async def test_edge_projection_failure_does_not_crash(
        self, svc_virtual, mock_metadata, mock_graph_projector,
    ):
        """单个 LinkType 边投影失败不阻塞其他 link（best-effort）。"""
        ot = _make_ot("Order", storage_type="VIRTUAL",
                      props=[_prop("id", DataType.STRING)])
        ot.id = "ot_order"
        mock_metadata.get_link_types = AsyncMock(return_value=[
            _make_link(api_name="bad_link", src_ot_id="x", tgt_ot_id="y"),
        ])
        # get_object_type_by_id 抛异常
        mock_metadata.get_object_type_by_id = AsyncMock(side_effect=RuntimeError("db down"))
        count = await svc_virtual._project_virtual_edges("SC", ot, sync_tag=100)
        # 异常被 except 捕获，返回 0
        assert count == 0
