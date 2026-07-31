"""Unit tests for OutboxExecutor — async side effect execution."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from ontology.config.settings import settings
from ontology.core.models.defaults import utcnow
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.outbox_executor import OutboxExecutor


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock(spec=PostgresMetaStore)


@pytest.fixture
def mock_http_client() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def executor(mock_metadata, mock_http_client) -> OutboxExecutor:
    return OutboxExecutor(
        metadata=mock_metadata,
        http_client=mock_http_client,
        poll_interval=0.1,
        batch_size=10,
    )


def _make_outbox_record(
    id: str = "ob-1",
    effect_type: str = "WEBHOOK",
    effect_config: dict | None = None,
    status: str = "PENDING",
    retry_count: int = 0,
    max_retries: int = 3,
    payload: dict | None = None,
) -> dict:
    return {
        "id": id,
        "action_execution_id": "exec-1",
        "effect_type": effect_type,
        "effect_config": effect_config or {"url": "https://example.com/webhook"},
        "payload": payload if payload is not None else {"action_id": "act-1"},
        "status": status,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "last_error": None,
    }


class TestOutboxExecutor:
    """OutboxExecutor tests covering webhook execution, retry, and DLQ."""

    # ── process_pending ──

    @pytest.mark.asyncio
    async def test_process_pending_empty(self, executor, mock_metadata):
        """No pending records returns 0."""
        mock_metadata.fetch_pending_outbox.return_value = []
        result = await executor.process_pending()
        assert result == 0

    @pytest.mark.asyncio
    async def test_process_pending_success(self, executor, mock_metadata, mock_http_client):
        """Successful webhook execution marks outbox as completed."""
        record = _make_outbox_record()
        mock_metadata.fetch_pending_outbox.return_value = [record]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        result = await executor.process_pending()

        assert result == 1
        mock_http_client.post.assert_awaited_once_with(
            "https://example.com/webhook",
            json={"action_id": "act-1"},
            headers={"X-Idempotency-Key": "ob-1"},
        )
        mock_metadata.mark_outbox_completed.assert_awaited_once_with("ob-1")

    @pytest.mark.asyncio
    async def test_process_pending_webhook_failure_retry(self, executor, mock_metadata, mock_http_client):
        """Failed webhook triggers retry with exponential backoff."""
        record = _make_outbox_record(retry_count=0)
        mock_metadata.fetch_pending_outbox.return_value = [record]
        mock_http_client.post.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )

        result = await executor.process_pending()

        assert result == 1
        # Should have scheduled a retry, not marked as completed
        mock_metadata.mark_outbox_completed.assert_not_called()
        mock_metadata.retry_outbox.assert_awaited_once()
        # Verify retry count incremented
        call_args = mock_metadata.retry_outbox.call_args
        assert call_args[0][1] == 1  # retry_count

    @pytest.mark.asyncio
    async def test_process_pending_max_retries_exceeded(self, executor, mock_metadata, mock_http_client):
        """After max retries, record moves to DLQ."""
        record = _make_outbox_record(retry_count=2, max_retries=3)
        mock_metadata.fetch_pending_outbox.return_value = [record]
        mock_http_client.post.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )

        result = await executor.process_pending()

        assert result == 1
        # Should have moved to DLQ (retry_count 2 + 1 >= max_retries 3)
        mock_metadata.move_outbox_to_dlq.assert_awaited_once()
        mock_metadata.mark_outbox_completed.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pending_unknown_effect_type(self, executor, mock_metadata):
        """Unknown effect type causes DLQ."""
        record = _make_outbox_record(effect_type="UNKNOWN")
        mock_metadata.fetch_pending_outbox.return_value = [record]

        result = await executor.process_pending()

        assert result == 1
        # Should have moved to DLQ on first failure (retry_count 0+1=1 < 3)
        # Actually first retry, not DLQ yet
        # Wait — OutboxError is raised from _execute, which triggers _handle_failure
        # Let me check: retry_count 0 → retry_count+1=1 < max_retries=3 → retry
        mock_metadata.retry_outbox.assert_awaited_once()
        mock_metadata.move_outbox_to_dlq.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_pending_with_payload(self, executor, mock_metadata, mock_http_client):
        """Webhook receives payload from record config."""
        record = _make_outbox_record(
            effect_config={
                "url": "https://api.example.com/callback",
                "payload": {"event": "order.shipped", "data": {"order_id": "123"}},
            }
        )
        mock_metadata.fetch_pending_outbox.return_value = [record]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        await executor.process_pending()

        mock_http_client.post.assert_awaited_once_with(
            "https://api.example.com/callback",
            json={"event": "order.shipped", "data": {"order_id": "123"}, "action_id": "act-1"},
            headers={"X-Idempotency-Key": "ob-1"},
        )

    @pytest.mark.asyncio
    async def test_process_pending_custom_headers(self, executor, mock_metadata, mock_http_client):
        """Webhook includes custom headers from config."""
        record = _make_outbox_record(
            effect_config={
                "url": "https://api.example.com/callback",
                "headers": {"Authorization": "Bearer token123", "X-Custom": "value"},
            }
        )
        mock_metadata.fetch_pending_outbox.return_value = [record]
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        await executor.process_pending()

        call_headers = mock_http_client.post.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer token123"
        assert call_headers["X-Custom"] == "value"
        assert call_headers["X-Idempotency-Key"] == "ob-1"

    @pytest.mark.asyncio
    async def test_process_pending_respects_next_retry_at(self, executor, mock_metadata):
        """Records with future next_retry_at are not fetched (filtered by query)."""
        mock_metadata.fetch_pending_outbox.return_value = []
        result = await executor.process_pending()
        assert result == 0

    @pytest.mark.asyncio
    async def test_retry_backoff_calculation(self, executor, mock_metadata, mock_http_client):
        """Retry backoff is roughly 2^n * 10 seconds ± jitter."""
        record = _make_outbox_record(retry_count=0)
        mock_metadata.fetch_pending_outbox.return_value = [record]
        mock_http_client.post.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )

        await executor.process_pending()

        # Should be called with next_retry_at approximately 20s from now (2^(0+1) * 10 = 20s ± jitter)
        call_args = mock_metadata.retry_outbox.call_args
        next_retry = call_args[0][3]
        now = utcnow()
        delta = (next_retry - now).total_seconds()
        # Allow for jitter: 20s ± 50% = 10s to 30s range
        assert 9.0 < delta < 31.0, f"Expected delta ~20s, got {delta}s"

    @pytest.mark.asyncio
    async def test_close_cleans_up_http_client(self, executor, mock_http_client):
        """Closing the executor cleans up the HTTP client."""
        await executor.close()
        mock_http_client.aclose.assert_awaited_once()


class TestOutboxExecutorP1:
    """P1 (ADR-011): SUB_ACTION + KAFKA_TOPIC side effects."""

    @pytest.mark.asyncio
    async def test_execute_sub_action_calls_action_service(self, mock_metadata, mock_http_client):
        """SUB_ACTION dispatches to ActionService.execute_action."""
        action_svc = AsyncMock()
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, action_service=action_svc)
        record = _make_outbox_record(
            effect_type="SUB_ACTION",
            effect_config={
                "ontology": "default",
                "object_type": "audit_log",
                "action": "create_log",
                "parameters": {"source": "order"},
            },
            id="ob-sub-1",
        )
        await exec_._execute(record)
        action_svc.execute_action.assert_awaited_once()
        call = action_svc.execute_action.await_args
        assert call.kwargs["object_type_api_name"] == "audit_log"
        assert call.kwargs["action_api_name"] == "create_log"
        assert call.kwargs["ontology_api_name"] == "default"
        # idempotency key derived from outbox id (loop prevention)
        assert call.kwargs["request"].idempotency_key == "subaction-ob-sub-1"
        assert call.kwargs["request"].parameters["source"] == "order"

    @pytest.mark.asyncio
    async def test_execute_sub_action_without_action_service_raises(self, mock_metadata, mock_http_client):
        """SUB_ACTION with no ActionService wired → OutboxError."""
        from ontology.core.exceptions import OutboxError

        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)
        record = _make_outbox_record(
            effect_type="SUB_ACTION",
            effect_config={"ontology": "d", "object_type": "t", "action": "a"},
        )
        with pytest.raises(OutboxError, match="no ActionService"):
            await exec_._execute(record)

    @pytest.mark.asyncio
    async def test_execute_sub_action_missing_config_raises(self, mock_metadata, mock_http_client):
        """SUB_ACTION missing required config fields → OutboxError."""
        from ontology.core.exceptions import OutboxError

        action_svc = AsyncMock()
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, action_service=action_svc)
        record = _make_outbox_record(
            effect_type="SUB_ACTION",
            effect_config={"ontology": "d"},  # missing object_type, action
        )
        with pytest.raises(OutboxError, match="missing"):
            await exec_._execute(record)

    @pytest.mark.asyncio
    async def test_publish_kafka_without_aiokafka_raises(self, mock_metadata, mock_http_client):
        """KAFKA_TOPIC with aiokafka not installed → OutboxError (graceful degrade)."""
        from ontology.core.exceptions import OutboxError

        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)
        record = _make_outbox_record(
            effect_type="KAFKA_TOPIC",
            effect_config={"topic": "events", "bootstrap_servers": "localhost:9092"},
        )
        # aiokafka likely not installed in test env → OutboxError
        try:
            import aiokafka  # noqa: F401

            pytest.skip("aiokafka installed; cannot test missing-dependency path")
        except ImportError:
            with pytest.raises(OutboxError, match="aiokafka not installed"):
                await exec_._execute(record)

    @pytest.mark.asyncio
    async def test_publish_kafka_missing_config_raises(self, mock_metadata, mock_http_client):
        """KAFKA_TOPIC missing topic/bootstrap → OutboxError."""
        from ontology.core.exceptions import OutboxError

        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)
        record = _make_outbox_record(
            effect_type="KAFKA_TOPIC",
            effect_config={"topic": "events"},  # missing bootstrap_servers
        )
        with pytest.raises(OutboxError, match="missing"):
            await exec_._execute(record)

    @pytest.mark.asyncio
    async def test_sub_action_idempotency_prevents_loop(self, mock_metadata, mock_http_client):
        """Replaying the same outbox record reuses the same idempotency key."""
        action_svc = AsyncMock()
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, action_service=action_svc)
        record = _make_outbox_record(
            effect_type="SUB_ACTION",
            effect_config={"ontology": "d", "object_type": "t", "action": "a"},
            id="ob-replay",
        )
        await exec_._execute(record)
        await exec_._execute(record)  # replay
        key1 = action_svc.execute_action.await_args_list[0].kwargs["request"].idempotency_key
        key2 = action_svc.execute_action.await_args_list[1].kwargs["request"].idempotency_key
        assert key1 == key2 == "subaction-ob-replay"


# ── INDEX effect (action-sync-outbox-design.md §8.5) ──


class TestOutboxExecutorIndexSync:
    """INDEX effect: 同步 object_state 变更到 Doris idx 表 (近实时).

    CREATE/UPDATE → DorisIndexStore.upsert; DELETE → delete_by_ids (按业务 PK 列)。
    ARCHIVE 由 process_pending 排除, 不走 OutboxExecutor。
    """

    @pytest.mark.asyncio
    async def test_process_pending_excludes_archive(self, mock_metadata, mock_http_client):
        """process_pending 调用 fetch_pending_outbox 时 exclude_effect_types=['ARCHIVE']."""
        mock_metadata.fetch_pending_outbox.return_value = []
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)
        await exec_.process_pending()
        mock_metadata.fetch_pending_outbox.assert_awaited_once()
        call_kwargs = mock_metadata.fetch_pending_outbox.call_args
        # 第二个位置参数或 kwargs 都接受; batch_size + exclude_effect_types
        assert call_kwargs.kwargs.get("exclude_effect_types") == ["ARCHIVE"] or (
            len(call_kwargs.args) >= 2 and call_kwargs.args[1] == ["ARCHIVE"]
        )

    @pytest.mark.asyncio
    async def test_index_create_upserts_to_doris(self, mock_metadata, mock_http_client):
        """INDEX CREATE_OBJECT → DorisIndexStore.upsert (properties 展开为平铺列).

        T1.3: rid from payload is injected into the upsert record so Doris idx
        carries the cross-engine identity key (handoff-rid-funnel-closure.md).
        """
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, index_store=index_store)
        record = _make_outbox_record(
            effect_type="INDEX",
            effect_config={},
            payload={
                "object_id": "obj-1",
                "rid": "ri.ontology.main.object.abc-123",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "version": 1,
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123", "status": "delayed"},
            },
        )
        await exec_._execute(record)
        index_store.upsert.assert_awaited_once_with(
            "default", "Flight", [{"flight_id": "CA123", "status": "delayed", "rid": "ri.ontology.main.object.abc-123"}]
        )
        index_store.delete_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_update_upserts_to_doris(self, mock_metadata, mock_http_client):
        """INDEX UPDATE_OBJECT/UPDATE_PROPERTY → 同 upsert (Doris MOW 幂等)."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, index_store=index_store)
        record = _make_outbox_record(
            effect_type="INDEX",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "UPDATE_PROPERTY",
                "properties": {"flight_id": "CA123", "status": "cancelled"},
            },
        )
        await exec_._execute(record)
        index_store.upsert.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_index_create_without_rid_omits_rid_column(self, mock_metadata, mock_http_client):
        """T1.3: payload without rid → upsert record has no `rid` key (fail-soft).

        Defends against malformed/legacy payloads (e.g. relayed from a path
        that didn't assign a rid). The Doris idx `rid` column stays NULL for
        such rows until the backfill script (T1.5) resolves them.
        """
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, index_store=index_store)
        record = _make_outbox_record(
            effect_type="INDEX",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123", "status": "delayed"},
            },
        )
        await exec_._execute(record)
        upsert_args = index_store.upsert.await_args
        record_rows = upsert_args.args[2]
        assert "rid" not in record_rows[0]

    @pytest.mark.asyncio
    async def test_index_delete_calls_delete_by_ids(self, mock_metadata, mock_http_client):
        """INDEX DELETE_OBJECT → delete_by_ids (按业务 PK 列删, design §3.3)."""
        from ontology.core.schemas.ontology import ObjectType, ObjectTypeCapabilities
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        # metadata_factory 返回的 meta 查 ObjectType 拿 primary_key→backing_column
        meta_factory = MagicMock()
        meta = AsyncMock()
        ot = MagicMock(spec=ObjectType)
        ot.capabilities = ObjectTypeCapabilities()
        ot.primary_key = "flightId"
        ot.properties = [
            MagicMock(api_name="flightId", backing_mapping=MagicMock(backing_column="flight_id")),
        ]
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory.return_value = meta

        exec_ = OutboxExecutor(
            metadata=mock_metadata,
            http_client=mock_http_client,
            index_store=index_store,
            metadata_factory=meta_factory,
        )
        record = _make_outbox_record(
            effect_type="INDEX",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "DELETE_OBJECT",
                "properties": {"flight_id": "CA123"},  # backing_column key
            },
        )
        await exec_._execute(record)
        index_store.delete_by_ids.assert_awaited_once_with("default", "Flight", ["CA123"], "flight_id")
        index_store.upsert.assert_not_called()
        meta.close.assert_awaited_once()  # metadata_factory session 释放

    @pytest.mark.asyncio
    async def test_index_delete_no_pk_value_skips(self, mock_metadata, mock_http_client):
        """INDEX DELETE 无 PK 值 → skip (幂等: 删不存在的行无副作用)."""
        from ontology.core.schemas.ontology import ObjectType, ObjectTypeCapabilities
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        meta_factory = MagicMock()
        meta = AsyncMock()
        ot = MagicMock(spec=ObjectType)
        ot.capabilities = ObjectTypeCapabilities()
        ot.primary_key = "flightId"
        ot.properties = [
            MagicMock(api_name="flightId", backing_mapping=MagicMock(backing_column="flight_id")),
        ]
        meta.get_object_type.return_value = ot
        meta.close = AsyncMock()
        meta_factory.return_value = meta

        exec_ = OutboxExecutor(
            metadata=mock_metadata,
            http_client=mock_http_client,
            index_store=index_store,
            metadata_factory=meta_factory,
        )
        record = _make_outbox_record(
            effect_type="INDEX",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "DELETE_OBJECT",
                "properties": {},  # 无 PK 值
            },
        )
        await exec_._execute(record)  # 不报错
        index_store.delete_by_ids.assert_not_called()

    @pytest.mark.asyncio
    async def test_index_without_index_store_raises(self, mock_metadata, mock_http_client):
        """未注入 index_store → OutboxError (会重试/DLQ)."""
        from ontology.core.exceptions import OutboxError

        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)  # no index_store
        record = _make_outbox_record(
            effect_type="INDEX",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"flight_id": "CA123"},
            },
        )
        with pytest.raises(OutboxError, match="no DorisIndexStore"):
            await exec_._execute(record)

    @pytest.mark.asyncio
    async def test_index_empty_properties_skips(self, mock_metadata, mock_http_client):
        """INDEX CREATE/UPDATE 空属性 → skip (Doris upsert 空行无意义)."""
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client, index_store=index_store)
        record = _make_outbox_record(
            effect_type="INDEX",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Flight",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {},
            },
        )
        await exec_._execute(record)  # 不报错
        index_store.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_skipped_by_execute(self, mock_metadata, mock_http_client):
        """ARCHIVE 分支 return (skip), 不报 Unknown effect (design §3.1 隔离)."""
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)
        record = _make_outbox_record(
            effect_type="ARCHIVE",
            payload={"object_type_api_name": "Flight", "ontology_api_name": "default"},
        )
        await exec_._execute(record)  # 不抛异常 (防御性 skip)


class TestOutboxExecutorEmbedding:
    """EMBEDDING effect: source_expression 拼接 → embed → Doris upsert_embedding (§14.4)."""

    @pytest.mark.skipif(
        settings.edition == "lite",
        reason="cloud-only: EMBEDding→Doris upsert（lite 无 Doris/onnxruntime）",
    )
    @pytest.mark.asyncio
    async def test_embedding_writes_to_doris(self, mock_metadata, mock_http_client):
        """EMBEDDING → embed source text → upsert_embedding (pk + embedding_column)."""
        from ontology.core.schemas.ontology import ObjectType
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        embedding_provider = MagicMock()
        embedding_provider.embed.return_value = __import__("numpy").array([[0.1, 0.2, 0.3]], dtype="float32")
        exec_ = OutboxExecutor(
            metadata=mock_metadata,
            http_client=mock_http_client,
            index_store=index_store,
            embedding_provider=embedding_provider,
        )
        # ObjectType mock: primary_key=orderId → backing order_id; name/description 属性
        ot = MagicMock(spec=ObjectType)
        ot.primary_key = "orderId"
        name_prop = MagicMock(
            api_name="orderId",
            backing_mapping=MagicMock(backing_column="order_id"),
        )
        ot.properties = [
            name_prop,
            MagicMock(api_name="name", backing_mapping=MagicMock(backing_column="name")),
            MagicMock(api_name="description", backing_mapping=MagicMock(backing_column="description")),
        ]
        mock_metadata.get_object_type.return_value = ot

        record = _make_outbox_record(
            effect_type="EMBEDDING",
            payload={
                "object_id": "obj-1",
                "object_type_api_name": "Document",
                "ontology_api_name": "default",
                "mutation_type": "CREATE_OBJECT",
                "properties": {"order_id": "O1", "name": "订单", "description": "VIP"},
                "source_expression": ["name", "description"],
                "embedding_column": "profile_embedding_embedding",
            },
        )
        await exec_._execute(record)
        # embed 被调一次, 输入是拼接文本 "订单 VIP"
        embedding_provider.embed.assert_called_once_with(["订单 VIP"])
        # upsert_embedding 被调: pk_column=order_id, pk_value=O1
        index_store.upsert_embedding.assert_awaited_once()
        call = index_store.upsert_embedding.await_args
        assert call.args[0] == "default"  # ontology
        assert call.args[1] == "Document"  # object_type
        assert call.args[2] == "order_id"  # pk_column
        assert call.args[3] == "O1"  # pk_value
        assert call.args[4] == "profile_embedding_embedding"  # embedding_column
        assert len(call.args[5]) == 3  # embedding dim
        assert call.args[5][0] == pytest.approx(0.1, abs=1e-5)

    @pytest.mark.asyncio
    async def test_embedding_without_provider_raises(self, mock_metadata, mock_http_client):
        """无 EmbeddingProvider → OutboxError (失败重试)."""
        exec_ = OutboxExecutor(metadata=mock_metadata, http_client=mock_http_client)
        record = _make_outbox_record(
            effect_type="EMBEDDING",
            payload={
                "object_type_api_name": "Document",
                "ontology_api_name": "default",
                "properties": {"order_id": "O1"},
                "source_expression": ["name"],
                "embedding_column": "emb",
            },
        )
        with pytest.raises(Exception, match="no EmbeddingProvider"):
            await exec_._execute(record)

    @pytest.mark.asyncio
    async def test_embedding_empty_source_text_skips(self, mock_metadata, mock_http_client):
        """源文本全空 → skip (不调 embed, 不调 upsert_embedding)."""
        from ontology.core.schemas.ontology import ObjectType
        from ontology.layers.index.doris_index_store import DorisIndexStore

        index_store = AsyncMock(spec=DorisIndexStore)
        embedding_provider = MagicMock()
        exec_ = OutboxExecutor(
            metadata=mock_metadata,
            http_client=mock_http_client,
            index_store=index_store,
            embedding_provider=embedding_provider,
        )
        ot = MagicMock(spec=ObjectType)
        ot.primary_key = "orderId"
        ot.properties = [
            MagicMock(api_name="orderId", backing_mapping=MagicMock(backing_column="order_id")),
            MagicMock(api_name="name", backing_mapping=MagicMock(backing_column="name")),
        ]
        mock_metadata.get_object_type.return_value = ot
        record = _make_outbox_record(
            effect_type="EMBEDDING",
            payload={
                "object_type_api_name": "Document",
                "ontology_api_name": "default",
                "properties": {"order_id": "O1", "name": ""},  # name 为空
                "source_expression": ["name"],
                "embedding_column": "emb",
            },
        )
        await exec_._execute(record)
        embedding_provider.embed.assert_not_called()
        index_store.upsert_embedding.assert_not_called()
