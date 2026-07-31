"""OutboxExecutor — asynchronous side effect execution.

Consumes PENDING outbox records from PostgreSQL and executes configured side effects:
    - WEBHOOK: Send HTTP request to external API
    - WRITE_BACK: Write changes back to source system via WriteBackManager
    - SUB_ACTION: Trigger another ActionType (chain orchestration, P1 ADR-011)
    - KAFKA_TOPIC: Publish change event to a Kafka topic (P1 ADR-011)

Uses exponential backoff with jitter for retries.
Dead letter queue (DLQ) for permanently failed records.

Aligns with Palantir's Transactional Outbox pattern:
    - Side effects are atomically persisted alongside data changes
    - Asynchronous execution decouples the Action hot path from external calls
    - At-least-once delivery with idempotency keys
"""

import asyncio
import logging
import random
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

import httpx

from ontology.core.exceptions import OutboxError
from ontology.core.models.defaults import utcnow
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.write_back_manager import WriteBackManager

if TYPE_CHECKING:
    from ontology.layers.index.doris_index_store import DorisIndexStore
    from ontology.services.action_service import ActionService
    from ontology.services.geotime_projector import GeoTimeProjector
    from ontology.services.graph_projector import GraphProjector
    from ontology.services.textql.embedding import EmbeddingProvider

_log = logging.getLogger(__name__)


class OutboxExecutor:
    """Asynchronously consume outbox records and execute side effects.

    Design:
    - Polls PENDING records from PostgreSQL at regular intervals
    - Executes webhooks with idempotency keys for at-least-once delivery
    - Executes write-backs via WriteBackManager (builds UPSERT SQL, runs against
      the target DB described in effect_config)
    - Retries failed deliveries with exponential backoff (2^n * 10s ± jitter)
    - Moves permanently failed records to DLQ after max_retries exhausted
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        http_client: httpx.AsyncClient | None = None,
        poll_interval: float = 1.0,
        batch_size: int = 100,
        write_back_manager: WriteBackManager | None = None,
        action_service: "ActionService | None" = None,
        index_store: "DorisIndexStore | None" = None,
        metadata_factory: "Callable[[], PostgresMetaStore] | None" = None,
        graph_projector: "GraphProjector | None" = None,
        geotime_projector: "GeoTimeProjector | None" = None,
        # §14.4 语义检索: EMBEDDING effect 调 EmbeddingProvider 计算向量
        # → DorisIndexStore.upsert_embedding 写入 embedding 列。为 None 时
        # EMBEDDING 记录会失败重试 (同 INDEX 未注入 index_store 的策略)。
        embedding_provider: "EmbeddingProvider | None" = None,
    ) -> None:
        self._metadata = metadata
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._write_back = write_back_manager or WriteBackManager()
        # P1 (ADR-011): optional ActionService ref for SUB_ACTION side effects.
        # Late-bound to avoid a circular dependency (ActionService does not
        # depend on OutboxExecutor, but both live in the services package).
        self._action_service = action_service
        # action-sync-outbox-design.md §8.5: INDEX effect 同步 object_state 变更到
        # Doris (近实时)。注入 DorisIndexStore; 为 None 时 INDEX 记录会失败重试。
        self._index_store = index_store
        # INDEX 处理 DELETE 需查 ObjectType 拿 primary_key→backing_column (业务
        # PK 列名)。OUTBOX_EXECUTOR 走后台轮询, 不能复用请求级 metadata session
        # (会被请求生命周期关闭)。metadata_factory 每次返回独立 session 的
        # PostgresMetaStore, 供 INDEX 分支查 ObjectType。未注入时回退 self._metadata
        # (单实例仍可工作, session 复用, HA 时需注入)。
        self._metadata_factory = metadata_factory
        # ADR-015 §capabilities: 图/时空投影器。INDEX effect 处理完 Doris upsert 后,
        # 用同一份 outbox payload 投影到 Neo4j (图节点) / PostGIS (空间表)。
        # 投影受 ObjectType.capabilities 门控 (Gate 4: 用户显式启用才投影)。
        # 为 None 时跳过投影 (Neo4j/PostGIS 未配置或未启用)。
        self._graph_projector = graph_projector
        self._geotime_projector = geotime_projector
        # §14.4: EMBEDDING effect 的 embedding 推理器 (本地 ONNX, 无 API 成本)。
        self._embedding_provider = embedding_provider

    async def process_pending(self) -> int:
        """Poll and process all pending outbox records.

        action-sync-outbox-design.md §8.5: 排除 ARCHIVE (由 SyncFlushScheduler
        消费), 只处理 WEBHOOK/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC/NOTIFICATION/INDEX。

        ⚠️ 事务释放: OutboxExecutor 用单一常驻 metadata session 轮询。SELECT 在
        READ COMMITTED 下会开启事务并持有 outbox 表的 AccessShareLock 直到 commit;
        若不显式提交, 事务长期 idle-in-transction 会阻塞 DDL (如 ALTER TABLE
        outbox ADD COLUMN, 2026-07-07 alembic 迁移被阻塞 570s 的事故)。故每次 poll
        末尾显式 commit 释放事务锁 (mark_outbox_completed/retry_outbox 自带 commit,
        但 records 为空或纯读路径不会 commit, 必须补上)。

        Returns:
            Number of records processed (including failed and completed).
        """
        records = await self._metadata.fetch_pending_outbox(self._batch_size, exclude_effect_types=["ARCHIVE"])
        if not records:
            # 释放 SELECT 开启的事务, 避免 idle-in-transaction 阻塞 DDL。
            await self._metadata.commit_transaction()
            return 0

        for record in records:
            try:
                await self._execute(record)
                await self._metadata.mark_outbox_completed(record["id"])
            except Exception as exc:
                await self._handle_failure(record, str(exc))

        # 末尾再 commit 一次兜底 (mark/retry 自带 commit, 但防御性确保事务释放)。
        await self._metadata.commit_transaction()
        return len(records)

    async def run_forever(self) -> None:
        """Run the executor loop continuously (blocking).

        Intended to be run as a background task in the FastAPI lifespan.
        """
        _log.info("OutboxExecutor started, polling every %.1fs", self._poll_interval)
        while True:
            try:
                await self.process_pending()
            except Exception:
                _log.exception("OutboxExecutor loop error")
            await asyncio.sleep(self._poll_interval)

    async def _execute(self, record: dict[str, Any]) -> None:
        """Execute a single outbox record based on its effect_type."""
        # 统一转大写比较(ActionEffectConfig.type 用小写如 "write_back",历史存大写)。
        effect_type = record["effect_type"].upper()
        config = record["effect_config"]

        if effect_type == "WEBHOOK":
            await self._call_webhook(config, record)
        elif effect_type == "WRITE_BACK":
            await self._do_write_back(config, record)
        elif effect_type == "SUB_ACTION":
            await self._execute_sub_action(config, record)
        elif effect_type == "KAFKA_TOPIC":
            await self._publish_kafka(config, record)
        elif effect_type == "NOTIFICATION":
            # ADR Action Mutation Mapping: notification effect 本期仅记日志。
            _log.info("notification effect (no-op): %s", config)
        elif effect_type == "INDEX":
            # action-sync-outbox-design.md §8.5: INDEX 同步 object_state 变更到
            # Doris (近实时, ≤1s)。ARCHIVE 不走这里 (process_pending 已排除)。
            await self._sync_index_to_doris(record)
        elif effect_type == "EMBEDDING":
            # §14.4 语义检索: 按 source_expression 拼 source 文本 → embed →
            # Doris upsert_embedding 写入 ARRAY<FLOAT> 列。INDEX effect 之后
            # 异步执行 (embedding 计算不阻塞 Action 返回, 也不阻塞 Doris 主索引)。
            await self._do_embedding(record)
        elif effect_type == "ARCHIVE":
            # ARCHIVE 由 SyncFlushScheduler 消费, 不应到达这里 (process_pending
            # 排除 ARCHIVE)。防御性 skip, 避免误走 Unknown effect 报错。
            _log.debug("ARCHIVE record %s skipped by OutboxExecutor", record["id"])
        else:
            raise OutboxError(record["id"], f"Unknown effect type: {effect_type}")

    async def _call_webhook(self, config: dict[str, Any], record: dict[str, Any]) -> None:
        """Execute a webhook side effect.

        Sends an HTTP POST with the configured payload, including
        X-Idempotency-Key header for at-least-once delivery guarantees.
        """
        url = config["url"]
        payload = {**config.get("payload", {}), **record.get("payload", {})}
        headers = config.get("headers", {})
        idempotency_key = record["id"]  # Use outbox ID as idempotency key

        response = await self._http.post(
            url,
            json=payload,
            headers={**headers, "X-Idempotency-Key": idempotency_key},
        )
        response.raise_for_status()

    async def _do_write_back(self, config: dict[str, Any], record: dict[str, Any]) -> None:
        """Execute a write-back to an external RDBMS via WriteBackManager.

        effect_config shape:
            jdbc_url:   target DB JDBC-style url (postgres://user:pass@host:port/db)
            table:      target table name
            primary_key: PK column for ON CONFLICT
            changes:    column-value pairs to upsert

        The WriteBackManager builds a parameterized UPSERT SQL with feedback-loop
        sync metadata (gaia_sync_tx / gaia_sync_user); this method executes it
        against the target DB using an async driver chosen by the url scheme.
        """
        jdbc_url = config.get("jdbc_url")
        table = config.get("table")
        primary_key = config.get("primary_key")
        # ADR Action Mutation Mapping: payload 是 {changes: {...}, ...}; 旧式
        # config 可能直接带 changes。优先取 payload.changes,回退 config.changes。
        payload = record.get("payload", {}) or {}
        changes = {**config.get("changes", {}), **(payload.get("changes", {}) if isinstance(payload, dict) else {})}
        if not (jdbc_url and table and changes):
            raise OutboxError(record["id"], "WRITE_BACK missing jdbc_url/table/changes")

        sync_tx_id = record.get("action_execution_id") or str(uuid.uuid4())
        scheme = jdbc_url.split("://", 1)[0].lower() if "://" in jdbc_url else jdbc_url.split(":", 1)[0].lower()
        dialect: Literal["postgres", "mysql"] = "mysql" if scheme in ("mysql", "mariadb", "doris") else "postgres"
        # ADR Action Mutation Mapping §3.9: op=insert 走 build_insert_sql
        # (CreateObject 回写,auto_increment 主键表);默认 upsert。
        op = config.get("op", "upsert")
        if op == "insert":
            sql, params = self._write_back.build_insert_sql(table, changes, sync_tx_id, dialect=dialect)
        else:
            sql, params = self._write_back.build_upsert_sql(
                table, primary_key or "id", changes, sync_tx_id, dialect=dialect
            )
        await self._execute_sql(jdbc_url, sql, params)

    async def _execute_sub_action(self, config: dict[str, Any], record: dict[str, Any]) -> None:
        """Execute a nested sub-action side effect (P1, ADR-011).

        Triggers another ActionType after the parent Action commits — the
        core of Palantir's chain orchestration (e.g. approval → auto-update).

        effect_config shape:
            ontology:    target ontology api_name
            object_type: target object type api_name
            action:      target ActionType api_name
            parameters:  optional dict of parameters to pass (merged with
                         record.payload.mutations)

        Loop prevention: the sub-action is invoked with an idempotency_key
        derived from the parent outbox record id, so a replay of the parent
        outbox record will not re-execute the sub-action (idempotency replay
        returns "accepted").
        """
        if self._action_service is None:
            raise OutboxError(record["id"], "SUB_ACTION configured but no ActionService wired")
        from ontology.core.schemas.action import ActionExecutionRequest

        ontology = config.get("ontology")
        object_type = config.get("object_type")
        action = config.get("action")
        if not (ontology and object_type and action):
            raise OutboxError(record["id"], "SUB_ACTION missing ontology/object_type/action")
        parameters: dict[str, Any] = dict(config.get("parameters", {}))
        # Carry parent mutations so the sub-action can see what changed.
        parent_payload = record.get("payload", {})
        if "mutations" in parent_payload and "mutations" not in parameters:
            parameters["mutations"] = parent_payload["mutations"]
        # Idempotency key derived from outbox id → replay-safe.
        idem_key = f"subaction-{record['id']}"
        await self._action_service.execute_action(
            object_type_api_name=object_type,
            action_api_name=action,
            request=ActionExecutionRequest(parameters=parameters, idempotency_key=idem_key),
            ontology_api_name=ontology,
        )

    async def _publish_kafka(self, config: dict[str, Any], record: dict[str, Any]) -> None:
        """Publish a change event to a Kafka topic (P1, ADR-011).

        effect_config shape:
            topic:             Kafka topic name
            bootstrap_servers: comma-separated host:port list
            key_field:         optional payload field to use as message key

        Graceful degradation: if ``aiokafka`` is not installed, the record is
        marked failed (so it retries / goes to DLQ) with a clear message —
        matching the simpleeval fallback pattern in ActionRuleEngine.
        """
        topic = config.get("topic")
        bootstrap = config.get("bootstrap_servers")
        if not (topic and bootstrap):
            raise OutboxError(record["id"], "KAFKA_TOPIC missing topic/bootstrap_servers")
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise OutboxError(record["id"], "aiokafka not installed") from exc
        payload = record.get("payload", {})
        key = str(payload.get(config["key_field"])) if config.get("key_field") else None
        producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
        await producer.start()
        try:
            import json

            await producer.send_and_wait(
                topic,
                value=json.dumps(payload, default=str).encode("utf-8"),
                key=key.encode("utf-8") if key else None,
            )
        finally:
            await producer.stop()

    async def _sync_index_to_doris(self, record: dict[str, Any]) -> None:
        """INDEX effect: 同步 object_state 变更到 Doris idx 表 (近实时).

        action-sync-outbox-design.md §8.5/§3.3:
        - CREATE_OBJECT/UPDATE_OBJECT/UPDATE_PROPERTY → DorisIndexStore.upsert
          (properties 展开为平铺列, Doris Unique MOW INSERT 幂等覆盖)
        - DELETE_OBJECT → DorisIndexStore.delete_by_ids (按业务 PK 列删)

        ⚠️ Doris idx 表的 PK 是业务 primary_key 的 backing_column, 不是
        rid (design §3.3)。DELETE 需查 ObjectType 拿 primary_key
        api_name → PropertyDef backing_column 作 pk_column。
        """
        if self._index_store is None:
            raise OutboxError(record["id"], "INDEX effect configured but no DorisIndexStore wired")
        payload = record.get("payload", {}) or {}
        ont = payload.get("ontology_api_name", "")
        ot_api = payload.get("object_type_api_name", "")
        mut_type = payload.get("mutation_type", "")
        props = payload.get("properties", {}) or {}

        if not (ont and ot_api):
            raise OutboxError(record["id"], "INDEX payload missing ontology/object_type api_name")

        if mut_type == "DELETE_OBJECT":
            # 按业务 PK 列删。pk_column = ObjectType.primary_key 的 backing_column。
            pk_column, pk_value = await self._resolve_pk_for_delete(ont, ot_api, props, record["id"])
            if pk_value is None:
                # 无 PK 值 → 无法定位行, 当作成功 (幂等: 删不存在的行无副作用)。
                _log.warning("INDEX DELETE %s/%s: no PK value in payload, skip", ont, ot_api)
                return
            await self._index_store.delete_by_ids(ont, ot_api, [str(pk_value)], pk_column)
            # ADR-015 §capabilities: 删除对象时同步删除图/时空投影 (受 capabilities 门控)。
            await self._project_object_delete(ont, ot_api, payload)
        elif mut_type in ("CREATE_OBJECT", "UPDATE_OBJECT", "UPDATE_PROPERTY"):
            if not props:
                # 无属性 (空 CREATE/UPDATE) → Doris upsert 空行无意义, skip。
                _log.warning("INDEX %s %s/%s: empty properties, skip", mut_type, ont, ot_api)
                return
            # T1.3 (handoff-rid-funnel-closure.md): 注入 rid 到 Doris idx 表 rid 列。
            # rid 是跨引擎身份主键 (Neo4j/PostGIS/TimescaleDB), Doris idx 是权威源
            # (design §4.4)。outbox payload 携带 rid (ActionService 分配, 见
            # _create_sync_outbox_records), 这里写入 Doris 与 Neo4j 对齐。
            # 复制一份避免污染 payload 原对象 (projector 下游还读 payload)。
            props_with_rid = dict(props)
            rid = payload.get("rid")
            if rid:
                props_with_rid["rid"] = rid
            await self._index_store.upsert(ont, ot_api, [props_with_rid])
            # ADR-015 §capabilities: 投影对象到图/时空 (受 capabilities 门控, fail-tolerant)。
            await self._project_object_upsert(ont, ot_api, payload)
        else:
            # 未知 mutation_type (RELATE/...不应出现, _create_sync_outbox_records
            # 已过滤)。防御性 skip 避免误报。
            _log.warning("INDEX record %s: unsupported mutation_type %s, skip", record["id"], mut_type)

    async def _do_embedding(self, record: dict[str, Any]) -> None:
        """EMBEDDING effect: 计算对象 embedding → 写 Doris embedding 列 (§14.4).

        流程:
        1. 从 payload 取 source_expression (api_name 列表) + properties (backing_column key)
        2. 查 ObjectType 拿 backing_to_api 映射 → 把 properties 转成 api_name key
        3. 按 source_expression 顺序取属性值拼接成 source 文本
        4. EmbeddingProvider.embed([text]) → L2-normalized 向量
        5. DorisIndexStore.upsert_embedding (UPDATE ... SET embedding_col WHERE pk)

        content_hash 增量: payload 带 source_hash 时与计算结果比对, 未变则 skip
        (避免重复 embed)。当前 ActionService 未传 source_hash, 全量计算 (后续增强)。
        """
        if self._embedding_provider is None:
            raise OutboxError(record["id"], "EMBEDDING effect configured but no EmbeddingProvider wired")
        if self._index_store is None:
            raise OutboxError(record["id"], "EMBEDDING effect configured but no DorisIndexStore wired")
        payload = record.get("payload", {}) or {}
        ont = payload.get("ontology_api_name", "")
        ot_api = payload.get("object_type_api_name", "")
        props = payload.get("properties", {}) or {}
        source_expr = payload.get("source_expression", []) or []
        embedding_column = payload.get("embedding_column", "")
        if not (ont and ot_api and source_expr and embedding_column):
            raise OutboxError(record["id"], "EMBEDDING payload missing required fields")
        # 查 ObjectType: backing_column key → api_name key (source_expression 是 api_name)。
        meta = self._metadata_factory() if self._metadata_factory is not None else self._metadata
        try:
            ot = await meta.get_object_type(ont, ot_api)
        finally:
            if self._metadata_factory is not None:
                await meta.close()
        from ontology.core.property_mapping import backing_to_api

        api_props = backing_to_api(ot, props)
        # 按 source_expression 顺序拼接源文本。
        text_parts = [str(api_props.get(name, "")) for name in source_expr]
        source_text = " ".join(p for p in text_parts if p)
        if not source_text.strip():
            _log.warning("EMBEDDING record %s: empty source text, skip", record["id"])
            return
        # embed (batch=1, query-time cost only)。
        vectors = self._embedding_provider.embed([source_text])
        embedding = vectors[0].tolist()
        # 解析 PK 列 + 值 (同 _resolve_pk_for_delete 逻辑)。
        pk_api = ot.primary_key
        pk_column = pk_api
        for prop in ot.properties:
            if prop.api_name == pk_api:
                pk_column = prop.backing_mapping.backing_column if prop.backing_mapping else pk_api
                break
        pk_value = props.get(pk_column)
        if pk_value is None:
            _log.warning("EMBEDDING record %s: no PK value, skip", record["id"])
            return
        await self._index_store.upsert_embedding(ont, ot_api, pk_column, str(pk_value), embedding_column, embedding)

    async def _resolve_pk_for_delete(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        props: dict[str, Any],
        record_id: str,
    ) -> tuple[str, Any]:
        """查 ObjectType.primary_key → PropertyDef backing_column.

        返回 (pk_column, pk_value)。pk_column 是 Doris idx 表的业务 PK 列名;
        pk_value 从 props 里按 pk_column 取 (props 是 backing_column key)。
        ObjectType 查不到或无 primary_key 时报错 (design §10 遗留任务: 强制 PK)。
        """
        meta = self._metadata_factory() if self._metadata_factory is not None else self._metadata
        try:
            ot = await meta.get_object_type(ontology_api_name, object_type_api_name)
        finally:
            if self._metadata_factory is not None:
                await meta.close()
        pk_api = ot.primary_key
        if not pk_api:
            raise OutboxError(record_id, f"ObjectType {object_type_api_name} has no primary_key")
        pk_column = pk_api
        for prop in ot.properties:
            if prop.api_name == pk_api:
                pk_column = prop.backing_mapping.backing_column if prop.backing_mapping else pk_api
                break
        return pk_column, props.get(pk_column)

    async def _project_object_upsert(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        payload: dict[str, Any],
    ) -> None:
        """ADR-015 §capabilities: 投影 CREATE/UPDATE 对象到 Neo4j + PostGIS。

        受 ObjectType.capabilities 门控 (Gate 4): 只有用户显式启用了
        graph_indexing_enabled / geotime_indexing_enabled 才投影。
        fail-tolerant: 投影失败不影响 Doris 同步 (已完成), 只记日志。
        数据源是 outbox payload 自带的 properties (不读 Doris / 不读 object_state)。
        """
        # Early exit: no projectors configured → skip entirely (no OT lookup)
        if self._graph_projector is None and self._geotime_projector is None:
            return
        obj_id = payload.get("rid", "")
        props = payload.get("properties", {}) or {}
        # 构造 projector 期望的 object_state 形态: {rid, properties}
        object_state = {"rid": obj_id, "properties": props}

        # 查 ObjectType 拿 capabilities (复用 metadata_factory 避免 session 复用问题)
        meta = self._metadata_factory() if self._metadata_factory is not None else self._metadata
        try:
            ot = await meta.get_object_type(ontology_api_name, object_type_api_name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("project_object: failed to load OT %s/%s: %s", ontology_api_name, object_type_api_name, exc)
            return
        finally:
            if self._metadata_factory is not None:
                await meta.close()

        caps = ot.capabilities

        # Gate 4: graph_indexing_enabled
        if caps.graph_indexing_enabled and self._graph_projector is not None:
            try:
                await self._graph_projector.project_object(ontology_api_name, object_type_api_name, object_state)
            except Exception as exc:  # noqa: BLE001 — fail-tolerant
                _log.warning(
                    "graph project_object failed for %s/%s (rid=%s): %s",
                    ontology_api_name,
                    object_type_api_name,
                    obj_id,
                    exc,
                )

        # Gate 4: geotime_indexing_enabled
        if caps.geotime_indexing_enabled and self._geotime_projector is not None:
            try:
                await self._geotime_projector.project_object(ontology_api_name, object_type_api_name, object_state)
            except Exception as exc:  # noqa: BLE001 — fail-tolerant
                _log.warning(
                    "geotime project_object failed for %s/%s (rid=%s): %s",
                    ontology_api_name,
                    object_type_api_name,
                    obj_id,
                    exc,
                )

    async def _project_object_delete(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        payload: dict[str, Any],
    ) -> None:
        """ADR-015 §capabilities: 删除 Neo4j + PostGIS 投影 (对象删除时)。

        受 capabilities 门控, fail-tolerant。用 outbox payload 的 rid。
        """
        # Early exit: no projectors configured → skip entirely (no OT lookup)
        if self._graph_projector is None and self._geotime_projector is None:
            return
        obj_id = payload.get("rid", "")
        if not obj_id:
            return

        meta = self._metadata_factory() if self._metadata_factory is not None else self._metadata
        try:
            ot = await meta.get_object_type(ontology_api_name, object_type_api_name)
        except Exception as exc:  # noqa: BLE001
            _log.warning("project_delete: failed to load OT %s/%s: %s", ontology_api_name, object_type_api_name, exc)
            return
        finally:
            if self._metadata_factory is not None:
                await meta.close()

        caps = ot.capabilities

        if caps.graph_indexing_enabled and self._graph_projector is not None:
            try:
                await self._graph_projector.delete_object(ontology_api_name, object_type_api_name, str(obj_id))
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "graph delete_object failed for %s/%s (rid=%s): %s",
                    ontology_api_name,
                    object_type_api_name,
                    obj_id,
                    exc,
                )

        if caps.geotime_indexing_enabled and self._geotime_projector is not None:
            try:
                await self._geotime_projector.delete_object(ontology_api_name, object_type_api_name, str(obj_id))
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "geotime delete_object failed for %s/%s (rid=%s): %s",
                    ontology_api_name,
                    object_type_api_name,
                    obj_id,
                    exc,
                )

    async def _execute_sql(self, jdbc_url: str, sql: str, params: list[Any]) -> None:
        """Execute a parameterized SQL statement against the target DB.

        Dispatches by url scheme. Currently supports postgres (asyncpg) and
        mysql/doris (aiomysql). ``sql`` already carries driver-native
        placeholders ($N / %s) and ``params`` is positional — no caller-side
        placeholder translation (V2 fix). Errors propagate to the caller
        (retried by _handle_failure).
        """
        scheme = jdbc_url.split("://", 1)[0].lower() if "://" in jdbc_url else jdbc_url.split(":", 1)[0].lower()
        if scheme in ("postgres", "postgresql"):
            await self._execute_postgres(jdbc_url, sql, params)
        elif scheme in ("mysql", "mariadb", "doris"):
            await self._execute_mysql(jdbc_url, sql, params)
        else:
            raise OutboxError("", f"Unsupported write-back scheme: {scheme}")

    async def _execute_postgres(self, jdbc_url: str, sql: str, params: list[Any]) -> None:
        import asyncpg

        conn = await asyncpg.connect(_pg_dsn(jdbc_url))
        try:
            await conn.execute(sql, *params)
        finally:
            await conn.close()

    async def _execute_mysql(self, jdbc_url: str, sql: str, params: list[Any]) -> None:
        import aiomysql

        parts = _parse_mysql_url(jdbc_url)
        parts["autocommit"] = True
        conn = await aiomysql.connect(**parts)
        try:
            cur = await conn.cursor()
            try:
                await cur.execute(sql, params)
            finally:
                await cur.close()
        finally:
            conn.close()

    async def _handle_failure(self, record: dict[str, Any], error: str) -> None:
        """Handle execution failure with retry or DLQ.

        Retry strategy: exponential backoff with jitter
            delay = 2^retry_count * 10 seconds
            jitter = ±50% of delay
        """
        retry_count = record["retry_count"] + 1
        max_retries = record["max_retries"]

        if retry_count >= max_retries:
            _log.warning("Outbox %s moved to DLQ after %d retries: %s", record["id"], retry_count, error)
            await self._metadata.move_outbox_to_dlq(record["id"], error)
        else:
            delay = (2**retry_count) * 10.0
            jitter = delay * 0.5 * (2 * random.random() - 1)
            next_retry = utcnow() + timedelta(seconds=delay + jitter)
            _log.info(
                "Outbox %s retry %d/%d in %.1fs: %s",
                record["id"],
                retry_count,
                max_retries,
                delay + jitter,
                error,
            )
            await self._metadata.retry_outbox(record["id"], retry_count, error, next_retry)

    async def close(self) -> None:
        """Clean up the HTTP client and metadata session."""
        await self._http.aclose()
        await self._metadata.close()


def _pg_dsn(jdbc_url: str) -> str:
    """Convert a postgres:// user:pass@host:port/db url to an asyncpg DSN."""
    # asyncpg accepts postgresql:// URLs directly; normalize scheme.
    if jdbc_url.startswith("postgres://"):
        return "postgresql://" + jdbc_url[len("postgres://") :]
    return jdbc_url


def _parse_mysql_url(jdbc_url: str) -> dict[str, Any]:
    """Parse a mysql://user:pass@host:port/db url into aiomysql.connect kwargs."""
    from urllib.parse import urlparse

    p = urlparse(jdbc_url)
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 3306,
        "user": p.username or "root",
        "password": p.password or "",
        "db": (p.path or "/").lstrip("/") or "ontology",
    }
