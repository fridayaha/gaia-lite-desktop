"""Unit tests for SyncFlushScheduler — ARCHIVE outbox 微批归档到 Iceberg + 清理。

action-sync-outbox-design.md §5/§8.6:
- run_flush_loop: 按 ontology 分桶, 双触发 (1000条/5min)
- _flush_ontology: claim → 按 ObjectType 拆分 → IcebergStore.merge
- run_cleanup_loop: 7 天保留期清理 COMPLETED/FAILED (DLQ 不删)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.services.sync_flush_scheduler import SyncFlushScheduler


def _make_outbox_record(
    *,
    id: str = "ob-1",
    effect_type: str = "ARCHIVE",
    target_ontology: str = "hr",
    object_type: str = "Flight",
    mutation_type: str = "CREATE_OBJECT",
    properties: dict | None = None,
) -> dict:
    return {
        "id": id,
        "action_execution_id": "exec-1",
        "effect_type": effect_type,
        "effect_config": {},
        "payload": {
            "object_id": "obj-1",
            "object_type_api_name": object_type,
            "ontology_api_name": target_ontology,
            "version": 1,
            "mutation_type": mutation_type,
            "properties": properties if properties is not None else {"flight_id": "CA123", "status": "delayed"},
        },
        "status": "PENDING",
        "retry_count": 0,
        "max_retries": 3,
        "last_error": None,
        "target_ontology": target_ontology,
    }


def _make_ot_mock(pk_api: str = "flightId", pk_backing: str = "flight_id") -> MagicMock:
    """ObjectType mock with primary_key → backing_column mapping."""
    from ontology.core.schemas.ontology import ObjectType

    ot = MagicMock(spec=ObjectType)
    ot.primary_key = pk_api
    ot.properties = [
        MagicMock(api_name=pk_api, backing_mapping=MagicMock(backing_column=pk_backing)),
    ]
    return ot


@pytest.fixture
def mock_dataset() -> AsyncMock:
    ds = AsyncMock()
    ds.merge = AsyncMock()
    return ds


@pytest.fixture
def mock_meta() -> AsyncMock:
    meta = AsyncMock()
    meta.close = AsyncMock()
    return meta


@pytest.fixture
def scheduler(mock_dataset, mock_meta) -> SyncFlushScheduler:
    meta_factory = MagicMock(return_value=mock_meta)
    return SyncFlushScheduler(
        dataset=mock_dataset,
        metadata_factory=meta_factory,
        flush_count_threshold=1000,
        flush_time_threshold=300.0,
    )


class TestFlushTick:
    """_flush_tick: 双触发 (count≥N 或 age≥T)。"""

    @pytest.mark.asyncio
    async def test_count_threshold_triggers_flush(self, scheduler, mock_meta, mock_dataset):
        """攒满 1000 条立即 flush (不等 5min)。"""
        mock_meta.count_pending_by_ontology.return_value = [("hr", 1500)]
        mock_meta.claim_pending_by_ontology.return_value = [_make_outbox_record()]
        mock_meta.get_object_type.return_value = _make_ot_mock()

        triggered = await scheduler._flush_tick()
        assert triggered == 1
        mock_meta.claim_pending_by_ontology.assert_awaited_once_with("ARCHIVE", "hr", batch_size=1000)
        mock_dataset.merge.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_time_threshold_triggers_flush(self, scheduler, mock_meta, mock_dataset):
        """不足 1000 但满 5min → flush (首次 tick age=∞)。"""
        mock_meta.count_pending_by_ontology.return_value = [("hr", 5)]
        mock_meta.claim_pending_by_ontology.return_value = [_make_outbox_record()]
        mock_meta.get_object_type.return_value = _make_ot_mock()

        triggered = await scheduler._flush_tick()
        assert triggered == 1  # 首次 tick 立即触发 (last_flush_at 未设)

    @pytest.mark.asyncio
    async def test_no_trigger_when_below_threshold_and_recent(self, scheduler, mock_meta):
        """不足 1000 且刚 flush 过 → 跳过。"""
        from ontology.core.models.defaults import utcnow

        scheduler._last_flush_at["hr"] = utcnow()  # 刚 flush
        mock_meta.count_pending_by_ontology.return_value = [("hr", 5)]

        triggered = await scheduler._flush_tick()
        assert triggered == 0
        mock_meta.claim_pending_by_ontology.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_null_ontology(self, scheduler, mock_meta):
        """target_ontology=None (历史/异常记录) 跳过, 不归档。"""
        mock_meta.count_pending_by_ontology.return_value = [(None, 100)]
        triggered = await scheduler._flush_tick()
        assert triggered == 0
        mock_meta.claim_pending_by_ontology.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_ontologies_independent(self, scheduler, mock_meta, mock_dataset):
        """多个 ontology 各自独立判断阈值 + flush。"""
        mock_meta.count_pending_by_ontology.return_value = [("hr", 2000), ("sales", 2000)]
        mock_meta.claim_pending_by_ontology.side_effect = [
            [_make_outbox_record(id="ob-1", target_ontology="hr")],
            [_make_outbox_record(id="ob-2", target_ontology="sales")],
        ]
        mock_meta.get_object_type.return_value = _make_ot_mock()

        triggered = await scheduler._flush_tick()
        assert triggered == 2


class TestFlushOntology:
    """_flush_ontology: claim → 按 ObjectType 拆分 → MERGE。"""

    @pytest.mark.asyncio
    async def test_create_update_merges_upsert(self, scheduler, mock_meta, mock_dataset):
        """CREATE/UPDATE → IcebergStore.merge(delete=False)。"""
        mock_meta.claim_pending_by_ontology.return_value = [
            _make_outbox_record(id="ob-1", mutation_type="CREATE_OBJECT", properties={"flight_id": "CA123"}),
            _make_outbox_record(id="ob-2", mutation_type="UPDATE_OBJECT", properties={"flight_id": "CA456"}),
        ]
        mock_meta.get_object_type.return_value = _make_ot_mock()

        await scheduler._flush_ontology("hr")

        mock_dataset.merge.assert_awaited_once()
        call = mock_dataset.merge.call_args
        args, kwargs = call
        # merge(dataset, rows, pk_columns, *, delete=False) — 前三个位置参数
        assert kwargs["delete"] is False
        assert args[2] == ["flight_id"]  # pk_columns
        # 两行 upsert
        assert len(args[1]) == 2  # rows
        # 成功的标记 COMPLETED
        mock_meta.mark_outbox_batch_completed.assert_awaited_once()
        completed_ids = mock_meta.mark_outbox_batch_completed.call_args.args[0]
        assert set(completed_ids) == {"ob-1", "ob-2"}
        mock_meta.retry_outbox_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_merges_delete(self, scheduler, mock_meta, mock_dataset):
        """DELETE_OBJECT → IcebergStore.merge(delete=True), source 只需 PK 列。"""
        mock_meta.claim_pending_by_ontology.return_value = [
            _make_outbox_record(id="ob-1", mutation_type="DELETE_OBJECT", properties={"flight_id": "CA123"}),
        ]
        mock_meta.get_object_type.return_value = _make_ot_mock()

        await scheduler._flush_ontology("hr")

        # merge 调一次 (delete=True)
        mock_dataset.merge.assert_awaited_once()
        args = mock_dataset.merge.call_args.args
        kwargs = mock_dataset.merge.call_args.kwargs
        assert kwargs["delete"] is True
        assert args[1] == [{"flight_id": "CA123"}]  # rows
        assert args[2] == ["flight_id"]  # pk_columns

    @pytest.mark.asyncio
    async def test_mixed_mutations_split(self, scheduler, mock_meta, mock_dataset):
        """一个批次内 CREATE/UPDATE/DELETE 各自 MERGE (design §3.2)。"""
        mock_meta.claim_pending_by_ontology.return_value = [
            _make_outbox_record(
                id="ob-1", mutation_type="CREATE_OBJECT", properties={"flight_id": "CA123", "status": "ok"}
            ),
            _make_outbox_record(id="ob-2", mutation_type="DELETE_OBJECT", properties={"flight_id": "CA456"}),
        ]
        mock_meta.get_object_type.return_value = _make_ot_mock()

        await scheduler._flush_ontology("hr")

        # merge 调两次: upsert + delete
        assert mock_dataset.merge.await_count == 2
        delete_calls = [c for c in mock_dataset.merge.call_args_list if c.kwargs.get("delete")]
        upsert_calls = [c for c in mock_dataset.merge.call_args_list if not c.kwargs.get("delete")]
        assert len(delete_calls) == 1
        assert len(upsert_calls) == 1

    @pytest.mark.asyncio
    async def test_per_object_type_split(self, scheduler, mock_meta, mock_dataset):
        """一个 ontology 批次内多 ObjectType 各自 MERGE (design §3.2 物理写入维度)。"""
        mock_meta.claim_pending_by_ontology.return_value = [
            _make_outbox_record(id="ob-1", object_type="Flight", properties={"flight_id": "CA123"}),
            _make_outbox_record(id="ob-2", object_type="Order", properties={"order_id": "O1"}),
        ]
        # 两个 ObjectType 返回不同 PK
        mock_meta.get_object_type.side_effect = [
            _make_ot_mock(pk_api="flightId", pk_backing="flight_id"),
            _make_ot_mock(pk_api="orderId", pk_backing="order_id"),
        ]

        await scheduler._flush_ontology("hr")

        assert mock_dataset.merge.await_count == 2
        pk_cols_used = {c.args[2][0] for c in mock_dataset.merge.call_args_list}
        assert pk_cols_used == {"flight_id", "order_id"}

    @pytest.mark.asyncio
    async def test_failure_retries_not_completes(self, scheduler, mock_meta, mock_dataset):
        """单 type MERGE 失败 → 失败记录回退 PENDING (retry), 不标记 COMPLETED。"""
        mock_meta.claim_pending_by_ontology.return_value = [
            _make_outbox_record(id="ob-1", properties={"flight_id": "CA123"}),
        ]
        mock_meta.get_object_type.return_value = _make_ot_mock()
        mock_dataset.merge.side_effect = RuntimeError("iceberg down")

        await scheduler._flush_ontology("hr")

        mock_meta.mark_outbox_batch_completed.assert_not_called()
        mock_meta.retry_outbox_batch.assert_awaited_once_with(["ob-1"], "ARCHIVE flush failed (see logs)")

    @pytest.mark.asyncio
    async def test_one_type_failure_doesnt_block_others(self, scheduler, mock_meta, mock_dataset):
        """单 type 失败不影响同批其他 type (design §3.2 各自独立写)。"""
        mock_meta.claim_pending_by_ontology.return_value = [
            _make_outbox_record(id="ob-1", object_type="Flight", properties={"flight_id": "CA123"}),
            _make_outbox_record(id="ob-2", object_type="Order", properties={"order_id": "O1"}),
        ]

        # Flight 第一次失败, Order 成功
        flight_ot = _make_ot_mock(pk_api="flightId", pk_backing="flight_id")
        order_ot = _make_ot_mock(pk_api="orderId", pk_backing="order_id")

        # get_object_type 调用顺序不确定 (dict 拆分), 用 side_effect 容错
        def get_ot_side_effect(ont, ot_api):
            return flight_ot if ot_api == "Flight" else order_ot

        mock_meta.get_object_type.side_effect = get_ot_side_effect

        # merge: Flight 失败, Order 成功 (按表名区分)
        async def merge_side_effect(dataset, rows, pk_cols, *, delete=False):
            if "flight" in dataset:
                raise RuntimeError("flight table down")

        mock_dataset.merge.side_effect = merge_side_effect
        await scheduler._flush_ontology("hr")

        # Order 成功 → mark_completed 含 ob-2; Flight 失败 → retry 含 ob-1
        mock_meta.mark_outbox_batch_completed.assert_awaited_once()
        completed = mock_meta.mark_outbox_batch_completed.call_args.args[0]
        assert "ob-2" in completed
        assert "ob-1" not in completed
        mock_meta.retry_outbox_batch.assert_awaited_once()
        retried = mock_meta.retry_outbox_batch.call_args.args[0]
        assert "ob-1" in retried

    @pytest.mark.asyncio
    async def test_empty_claim_noop(self, scheduler, mock_meta):
        """claim 返回空 → 不发 MERGE, 不标记。"""
        mock_meta.claim_pending_by_ontology.return_value = []
        await scheduler._flush_ontology("hr")
        mock_dataset_merge = scheduler._dataset.merge
        mock_dataset_merge.assert_not_called()
        mock_meta.mark_outbox_batch_completed.assert_not_called()


class TestPkResolution:
    """_resolve_pk_columns: ObjectType.primary_key → backing_column (带缓存)."""

    @pytest.mark.asyncio
    async def test_pk_resolution_caches(self, scheduler, mock_meta):
        """同 ObjectType 第二次查走缓存 (不重复查 DB)。"""
        mock_meta.get_object_type.return_value = _make_ot_mock()

        await scheduler._resolve_pk_columns("hr", "Flight")
        await scheduler._resolve_pk_columns("hr", "Flight")

        # 缓存命中 → 只查一次 DB
        assert mock_meta.get_object_type.await_count == 1

    @pytest.mark.asyncio
    async def test_invalidate_pk_cache(self, scheduler, mock_meta):
        """invalidate 后重新查 DB (ObjectType define/update 时调用)。"""
        mock_meta.get_object_type.return_value = _make_ot_mock()
        await scheduler._resolve_pk_columns("hr", "Flight")
        scheduler.invalidate_pk_cache("hr", "Flight")
        await scheduler._resolve_pk_columns("hr", "Flight")
        assert mock_meta.get_object_type.await_count == 2

    @pytest.mark.asyncio
    async def test_pk_no_backing_mapping_falls_back_to_api_name(self, scheduler, mock_meta):
        """primary_key 属性无 backing_mapping → 回退 api_name 作列名。"""
        from ontology.core.schemas.ontology import ObjectType

        ot = MagicMock(spec=ObjectType)
        ot.primary_key = "flightId"
        ot.properties = [MagicMock(api_name="flightId", backing_mapping=None)]
        mock_meta.get_object_type.return_value = ot

        pk_cols = await scheduler._resolve_pk_columns("hr", "Flight")
        assert pk_cols == ["flightId"]

    @pytest.mark.asyncio
    async def test_no_primary_key_returns_empty(self, scheduler, mock_meta):
        """ObjectType 无 primary_key → 返回空 (flush 会报 OntologyError, design §10)。"""
        from ontology.core.schemas.ontology import ObjectType

        ot = MagicMock(spec=ObjectType)
        ot.primary_key = None
        ot.properties = []
        mock_meta.get_object_type.return_value = ot

        pk_cols = await scheduler._resolve_pk_columns("hr", "Flight")
        assert pk_cols == []


class TestCleanup:
    """_cleanup_once: 删除 7 天前 COMPLETED/FAILED (DLQ 不删)。"""

    @pytest.mark.asyncio
    async def test_cleanup_calls_delete_old(self, scheduler, mock_meta):
        mock_meta.delete_old_completed_outbox.return_value = 5
        deleted = await scheduler._cleanup_once()
        assert deleted == 5
        mock_meta.delete_old_completed_outbox.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_cleanup_zero_deletes(self, scheduler, mock_meta):
        mock_meta.delete_old_completed_outbox.return_value = 0
        deleted = await scheduler._cleanup_once()
        assert deleted == 0


class TestTableName:
    """_iceberg_table_name: ObjectType → ontology.<snake> 业务表名。"""

    def test_pascalcase_to_snake(self):
        assert SyncFlushScheduler._iceberg_table_name("Flight") == "ontology.flight"
        assert SyncFlushScheduler._iceberg_table_name("FlightStatusLog") == "ontology.flight_status_log"
        assert SyncFlushScheduler._iceberg_table_name("CustomerOrder") == "ontology.customer_order"
