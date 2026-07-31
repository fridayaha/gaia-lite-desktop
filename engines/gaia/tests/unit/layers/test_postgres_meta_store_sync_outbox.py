"""Unit tests for PostgresMetaStore sync-outbox methods.

action-sync-outbox-design.md §8.2: outbox 表复用承载 INDEX (→Doris 近实时) /
ARCHIVE (→Iceberg 微批) 同步。新增方法:
- fetch_pending_outbox (effect_type 过滤/排除)
- count_pending_by_ontology (GROUP BY target_ontology)
- claim_pending_by_ontology (FOR UPDATE SKIP LOCKED)
- mark_outbox_batch_completed / retry_outbox_batch (批量标记)
- delete_old_completed_outbox (7 天保留期清理)
- create_outbox_record (target_ontology 参数)

All DB calls mocked — validates SQLAlchemy query construction + effect_type
隔离逻辑 (INDEX/ARCHIVE 互不干扰, design §3.1)。
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from ontology.core.models.defaults import utcnow
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore


def _pg_sql(stmt) -> str:
    """Compile a SQLAlchemy stmt to PG dialect SQL (literal_binds) for assertions.

    SKIP LOCKED / FOR UPDATE 等 PG 方言特性只在 postgresql dialect 下才出现在
    compiled SQL 里 (默认 dialect 不输出)。
    """
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def _make_outbox_orm(
    *,
    id: str = "ob-1",
    effect_type: str = "INDEX",
    target_ontology: str | None = None,
    status: str = "PENDING",
    payload: dict | None = None,
    next_retry_at: datetime | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = id
    m.action_execution_id = "exec-1"
    m.effect_type = effect_type
    m.target_ontology = target_ontology
    m.effect_config = {}
    m.payload = payload or {}
    m.status = status
    m.retry_count = 0
    m.max_retries = 3
    m.last_error = None
    m.next_retry_at = next_retry_at
    m.created_at = utcnow()
    m.updated_at = utcnow()
    return m


@pytest.fixture
def store(mock_session) -> PostgresMetaStore:
    return PostgresMetaStore(mock_session)


class TestCreateOutboxRecord:
    """create_outbox_record 接受 target_ontology 参数 (design §8.1)。"""

    @pytest.mark.asyncio
    async def test_create_with_target_ontology(self, store, mock_session):
        record = await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="ARCHIVE",
            effect_config={},
            payload={"k": "v"},
            target_ontology="hr",
        )
        mock_session.add.assert_called_once()
        added = mock_session.add.call_args.args[0]
        assert added.effect_type == "ARCHIVE"
        assert added.target_ontology == "hr"
        assert added.payload == {"k": "v"}
        assert added.status == "PENDING"
        # Does NOT auto-commit (caller manages transaction)
        mock_session.flush.assert_not_called()
        mock_session.commit.assert_not_called()
        assert record is added

    @pytest.mark.asyncio
    async def test_create_index_has_no_target_ontology(self, store, mock_session):
        """INDEX 不分桶 (逐条近实时), target_ontology=None (design §3.1)。"""
        await store.create_outbox_record(
            action_execution_id="exec-1",
            effect_type="INDEX",
            effect_config={},
            payload={},
            # target_ontology 默认 None
        )
        added = mock_session.add.call_args.args[0]
        assert added.target_ontology is None


class TestFetchPendingOutbox:
    """fetch_pending_outbox 支持 effect_type 过滤 / exclude (design §8.5)。"""

    @pytest.mark.asyncio
    async def test_fetch_excludes_archive(self, store, mock_session, mock_execute_result):
        """OutboxExecutor 调用: 排除 ARCHIVE, 只拿副作用 + INDEX。"""
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        await store.fetch_pending_outbox(100, exclude_effect_types=["ARCHIVE"])

        # 验证 SQL 构造: WHERE status='PENDING' AND effect_type NOT IN ('ARCHIVE')
        stmt = mock_session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ARCHIVE" in compiled
        # NOT IN 子句存在 (upper 比较)
        assert "NOT IN" in compiled or "notin_" in str(stmt)

    @pytest.mark.asyncio
    async def test_fetch_filter_single_effect_type(self, store, mock_session, mock_execute_result):
        """SyncFlushScheduler 用 effect_type='ARCHIVE' 单取 (但实际用 claim, 这里测 fetch)。"""
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        await store.fetch_pending_outbox(100, effect_type="ARCHIVE")

        stmt = mock_session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ARCHIVE" in compiled

    @pytest.mark.asyncio
    async def test_fetch_returns_target_ontology(self, store, mock_session, mock_execute_result):
        """返回 dict 带 target_ontology 字段 (供 claim/flush 使用)。"""
        mock_execute_result.scalars.return_value.all.return_value = [
            _make_outbox_orm(id="ob-1", target_ontology="hr"),
        ]
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        records = await store.fetch_pending_outbox(100)
        assert records[0]["target_ontology"] == "hr"
        assert records[0]["effect_type"] == "INDEX"

    @pytest.mark.asyncio
    async def test_fetch_effect_type_case_insensitive(self, store, mock_session, mock_execute_result):
        """effect_type 比较不区分大小写 (历史存大写, 新存小写)。"""
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        # 传小写 'archive', 库里可能存 'ARCHIVE' (func.upper 统一)
        await store.fetch_pending_outbox(100, effect_type="archive")
        # 不报错即通过 (upper 比较在 SQL 层)


class TestCountPendingByOntology:
    """count_pending_by_ontology: GROUP BY target_ontology (design §5.1)。"""

    @pytest.mark.asyncio
    async def test_count_groups_by_ontology(self, store, mock_session, mock_execute_result):
        mock_execute_result.all.return_value = [("hr", 500), ("sales", 200), (None, 5)]
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        result = await store.count_pending_by_ontology("ARCHIVE")

        assert result == [("hr", 500), ("sales", 200), (None, 5)]
        stmt = mock_session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "ARCHIVE" in compiled
        assert "GROUP BY" in compiled.upper() or "group_by" in str(stmt).lower()


class TestClaimPendingByOntology:
    """claim_pending_by_ontology: FOR UPDATE SKIP LOCKED (design §3.7)。"""

    @pytest.mark.asyncio
    async def test_claim_returns_records(self, store, mock_session, mock_execute_result):
        mock_execute_result.scalars.return_value.all.return_value = [
            _make_outbox_orm(id="ob-1", effect_type="ARCHIVE", target_ontology="hr"),
            _make_outbox_orm(id="ob-2", effect_type="ARCHIVE", target_ontology="hr"),
        ]
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        records = await store.claim_pending_by_ontology("ARCHIVE", "hr", batch_size=1000)
        assert len(records) == 2
        assert records[0]["id"] == "ob-1"
        assert records[0]["target_ontology"] == "hr"

    @pytest.mark.asyncio
    async def test_claim_uses_skip_locked(self, store, mock_session, mock_execute_result):
        """验证 SQL 带 FOR UPDATE SKIP LOCKED (多实例 HA 安全)."""
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        await store.claim_pending_by_ontology("ARCHIVE", "hr", batch_size=100)

        stmt = mock_session.execute.call_args.args[0]
        # with_for_update(skip_locked=True) 在 PG dialect 下输出 FOR UPDATE SKIP LOCKED
        compiled = _pg_sql(stmt)
        assert "SKIP LOCKED" in compiled

    @pytest.mark.asyncio
    async def test_claim_filters_ontology_and_effect_type(self, store, mock_session, mock_execute_result):
        mock_execute_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_execute_result)

        await store.claim_pending_by_ontology("ARCHIVE", "hr", batch_size=100)

        stmt = mock_session.execute.call_args.args[0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "'hr'" in compiled  # target_ontology = 'hr'
        assert "ARCHIVE" in compiled


class TestMarkOutboxBatchCompleted:
    """mark_outbox_batch_completed: 批量标记 COMPLETED (design §4.2)。"""

    @pytest.mark.asyncio
    async def test_mark_batch_completed(self, store, mock_session):
        mock_result = MagicMock()
        mock_result.rowcount = 3
        mock_session.execute = AsyncMock(return_value=mock_result)

        count = await store.mark_outbox_batch_completed(["ob-1", "ob-2", "ob-3"])

        assert count == 3
        mock_session.flush.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_batch_empty(self, store, mock_session):
        """空列表 → no-op, 不发 SQL。"""
        count = await store.mark_outbox_batch_completed([])
        assert count == 0
        mock_session.execute.assert_not_called()


class TestRetryOutboxBatch:
    """retry_outbox_batch: 批量回退 PENDING (design §4.2)。"""

    @pytest.mark.asyncio
    async def test_retry_batch(self, store, mock_session):
        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_session.execute = AsyncMock(return_value=mock_result)

        count = await store.retry_outbox_batch(["ob-1", "ob-2"], "flush failed")

        assert count == 2
        mock_session.flush.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retry_batch_empty(self, store, mock_session):
        count = await store.retry_outbox_batch([], "x")
        assert count == 0
        mock_session.execute.assert_not_called()


class TestDeleteOldCompletedOutbox:
    """delete_old_completed_outbox: 7 天保留期清理 (design §4.3)。"""

    @pytest.mark.asyncio
    async def test_delete_old_records(self, store, mock_session):
        mock_result = MagicMock()
        mock_result.rowcount = 42
        mock_session.execute = AsyncMock(return_value=mock_result)

        deleted = await store.delete_old_completed_outbox(retention_days=7)

        assert deleted == 42
        stmt = mock_session.execute.call_args.args[0]
        compiled = _pg_sql(stmt)
        # COMPLETED 和 FAILED 都删
        assert "COMPLETED" in compiled
        assert "FAILED" in compiled
        # 7 天间隔: updated_at < (now - 7 days)。compiled 里是计算后的时间戳,
        # 验证 cutoff 在过去 (now - 7d) 而非字面量 '7'。
        assert "updated_at <" in compiled
        mock_session.flush.assert_awaited_once()
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_default_retention_7_days(self, store, mock_session):
        """默认保留 7 天: cutoff = now - 7d。"""
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        before = utcnow()
        await store.delete_old_completed_outbox()  # 不传 retention_days
        after = utcnow()

        stmt = mock_session.execute.call_args.args[0]
        compiled = _pg_sql(stmt)
        # cutoff 落在 (now-7d-ε, now-7d+ε) 之间 → 默认 7 天保留期生效。
        # 提取 SQL 里的时间戳字面量验证近似 7 天前。
        import re

        ts_match = re.search(r"updated_at < '([^']+)'", compiled)
        assert ts_match is not None, compiled
        from datetime import datetime as _dt

        cutoff = _dt.fromisoformat(ts_match.group(1))
        expected_low = before - timedelta(days=7, seconds=5)
        expected_high = after - timedelta(days=7, seconds=-5)
        assert expected_low <= cutoff <= expected_high
