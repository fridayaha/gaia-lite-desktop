"""Unit tests for OutboxExecutor WRITE_BACK path (WriteBackManager integration)."""

from unittest.mock import AsyncMock, patch

import pytest

from ontology.config.settings import settings
from ontology.services.outbox_executor import OutboxExecutor

_full_only = pytest.mark.skipif(
    settings.edition == "lite",
    reason="cloud-only: 真 write-back 执行（lite 无 asyncpg/aiomysql 驱动）",
)


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def executor(mock_metadata) -> OutboxExecutor:
    return OutboxExecutor(metadata=mock_metadata)


def _write_back_record(changes: dict | None = None) -> dict:
    return {
        "id": "ob-wb-1",
        "action_execution_id": "exec-1",
        "effect_type": "WRITE_BACK",
        "effect_config": {
            "jdbc_url": "postgres://u:p@localhost:5432/ontology",
            "table": "source_orders",
            "primary_key": "order_id",
            "changes": changes or {"order_id": "O-1", "status": "shipped"},
        },
        "payload": {},
        "status": "PENDING",
        "retry_count": 0,
        "max_retries": 3,
        "last_error": None,
    }


class TestWriteBackDispatch:
    @pytest.mark.asyncio
    async def test_missing_config_raises(self, executor, mock_metadata):
        """Missing jdbc_url/table/primary_key/changes → OutboxError → retry path."""
        record = _write_back_record()
        record["effect_config"] = {"jdbc_url": "postgres://x"}  # missing fields
        mock_metadata.fetch_pending_outbox.return_value = [record]
        await executor.process_pending()
        # Failed → retry_outbox called (not completed)
        mock_metadata.mark_outbox_completed.assert_not_called()
        mock_metadata.retry_outbox.assert_awaited_once()

    @pytest.mark.asyncio
    @_full_only
    async def test_postgres_write_back_executes_sql(self, executor, mock_metadata):
        """WRITE_BACK to postgres builds UPSERT SQL and executes via asyncpg."""
        record = _write_back_record()
        mock_metadata.fetch_pending_outbox.return_value = [record]

        fake_conn = AsyncMock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)) as mock_connect:
            await executor.process_pending()

        mock_connect.assert_awaited_once()
        # execute called with the UPSERT SQL + param values
        assert fake_conn.execute.await_count == 1
        sql_arg = fake_conn.execute.call_args[0][0]
        assert "INSERT INTO source_orders" in sql_arg
        assert "ON CONFLICT (order_id) DO UPDATE" in sql_arg
        # Feedback-loop metadata injected
        assert "gaia_sync_tx" in sql_arg
        assert "gaia_sync_user" in sql_arg
        mock_metadata.mark_outbox_completed.assert_awaited_once_with("ob-wb-1")

    @pytest.mark.asyncio
    @_full_only
    async def test_mysql_write_back_translates_placeholders(self, executor, mock_metadata):
        """WRITE_BACK to mysql translates :name → %s placeholders."""
        record = _write_back_record()
        record["effect_config"]["jdbc_url"] = "mysql://root:@localhost:3306/ontology"
        mock_metadata.fetch_pending_outbox.return_value = [record]

        fake_conn = AsyncMock()
        fake_cursor = AsyncMock()
        fake_conn.cursor.return_value = fake_cursor
        with patch("aiomysql.connect", new=AsyncMock(return_value=fake_conn)):
            await executor.process_pending()

        sql_arg = fake_cursor.execute.call_args[0][0]
        # No :name placeholders remain after translation
        assert ":order_id" not in sql_arg
        assert "%s" in sql_arg
        mock_metadata.mark_outbox_completed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unsupported_scheme_retries(self, executor, mock_metadata):
        """Unsupported jdbc scheme → failure → retry."""
        record = _write_back_record()
        record["effect_config"]["jdbc_url"] = "oracle://x"
        mock_metadata.fetch_pending_outbox.return_value = [record]
        await executor.process_pending()
        mock_metadata.mark_outbox_completed.assert_not_called()
        mock_metadata.retry_outbox.assert_awaited_once()

    @pytest.mark.asyncio
    @_full_only
    async def test_write_back_payload_merged_from_record(self, executor, mock_metadata):
        """Record payload.changes is merged into changes (Action dynamic data)."""
        record = _write_back_record(changes={"order_id": "O-1"})
        # ADR Action Mutation Mapping: payload carries {changes: {...}, ...}
        record["payload"] = {"changes": {"status": "shipped"}}
        mock_metadata.fetch_pending_outbox.return_value = [record]

        fake_conn = AsyncMock()
        with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)):
            await executor.process_pending()

        sql_arg = fake_conn.execute.call_args[0][0]
        # Both the config change and the record payload.changes land in the SQL
        assert "order_id" in sql_arg
        assert "status" in sql_arg
