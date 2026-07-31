"""ActionService._create_sync_outbox_records — INDEX + ARCHIVE outbox 自动追加。

action-sync-outbox-design.md §8.3: Action 写 PG object_state 时同步追加
INDEX (→Doris 近实时) + ARCHIVE (→Iceberg 微批) 两条 outbox 记录, 复用同一
PG 事务保证原子性。RELATE/UNRELATE/CLEAR_LINKS 跳过 (关系不同步, design §3.5)。
VIRTUAL 目标跳过 (架构红线 9)。

验证:
- 每个 CREATE/UPDATE/DELETE mutation 生成 1 INDEX + 1 ARCHIVE (target_ontology)
- INDEX 不分桶 (target_ontology=None), ARCHIVE 按 ontology 分桶
- payload 含 object_id/object_type/ontology/version/mutation_type/properties
- properties 是 backing_column key (从 object_state raw 取, 不再翻译)
- RELATE/UNRELATE/CLEAR_LINKS 不生成 sync outbox
- VIRTUAL 目标跳过
- outbox 与 object_state 同事务 (commit 前 outbox 已 add, commit 后才可见)
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.config.settings import settings
from ontology.core.schemas.action import ActionExecutionRequest, ActionTypeParameter
from ontology.core.schemas.ontology import DataType, ObjectType
from ontology.services.action_service import ActionService

# 本模块断言 full 版 INDEX/ARCHIVE/EMBEDDING outbox 自动追加（→Doris/Iceberg）。
# B5：lite 桌面版不产这些 outbox（无 Doris/Iceberg，A4 已砍后台消费），lite 下
# _create_sync_outbox_records 直接 return。这些断言在 lite 不适用——lite 的等价
# 覆盖在 test_action_service_lite.py（断言 INDEX/ARCHIVE 0 条）。模块级 skipif
# 避免逐个测试标记（A5/B3 惯例）。
pytestmark = pytest.mark.skipif(
    settings.edition == "lite",
    reason="lite 版不产 INDEX/ARCHIVE/EMBEDDING outbox（B5）；lite 覆盖见 test_action_service_lite.py",
)


def _make_ot(
    *,
    api_name: str = "order",
    primary_key: str = "orderId",
    pk_backing: str = "order_id",
    storage_type: str = "MANAGED",
    vector_props: list[dict] | None = None,
) -> ObjectType:
    """ObjectType with primary_key → backing_column mapping.

    vector_props: optional list of VECTOR property dicts, each
        {api_name, backing_column, source_expression}. Adds a VECTOR-typed
        property with vector_config for EMBEDDING outbox testing (§14.4).
    """
    ot = MagicMock(spec=ObjectType)
    ot.id = "ot1"
    ot.ontology_id = "onto1"
    ot.api_name = api_name
    ot.display_name = api_name
    ot.description = ""
    ot.primary_key = primary_key
    ot.title_property = primary_key
    ot.storage_type = storage_type
    ot.visibility = "NORMAL"
    ot.status = "ACTIVE"
    ot.deleted_at = None
    props = [
        MagicMock(
            api_name=primary_key,
            data_type=DataType.STRING,
            backing_mapping=MagicMock(backing_column=pk_backing) if pk_backing else None,
            vector_config=None,
        ),
    ]
    for vp in vector_props or []:
        vc = MagicMock()
        vc.source_expression = vp["source_expression"]
        props.append(
            MagicMock(
                api_name=vp["api_name"],
                data_type=DataType.VECTOR,
                backing_mapping=MagicMock(backing_column=vp["backing_column"]),
                vector_config=vc,
            )
        )
    ot.properties = props
    return ot


def _make_action_type():
    from ontology.core.schemas.ontology import ActionType

    return ActionType(
        id="at1",
        ontology_id="onto1",
        api_name="shipOrder",
        display_name="Ship",
        description="",
        affected_object_type_id="ot1",
        parameters={
            "parameters": [
                ActionTypeParameter(api_name="status", display_name="Status", data_type=DataType.STRING).model_dump(),
            ],
            "rules": [],
            "effects": [],
        },
        rules={"rules": []},
        submission_criteria={},
        status="ACTIVE",
        created_at=MagicMock(),
        updated_at=MagicMock(),
    )


@pytest.fixture
def mock_metadata() -> AsyncMock:
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

    meta = AsyncMock(spec=PostgresMetaStore)

    @asynccontextmanager
    async def _noop_transaction():
        yield

    meta.transaction = _noop_transaction
    return meta


@pytest.fixture
def mock_authz() -> AsyncMock:
    """Permissive AuthorizationService mock (PDP allows by default)."""
    az = AsyncMock()
    az.check_access.return_value = MagicMock(allowed=True, reason="")
    az.check_action_permission.return_value = set()
    return az


@pytest.fixture
def service(mock_metadata, mock_authz) -> ActionService:
    rule_engine = MagicMock()
    rule_engine.evaluate = MagicMock(return_value=({}, []))
    rule_engine.evaluate_submission_criteria = MagicMock(return_value=[])
    return ActionService(
        metadata=mock_metadata,
        catalog=AsyncMock(),
        dataset=AsyncMock(),
        rule_engine=rule_engine,
        authorization_service=mock_authz,
    )


def _setup_create_action(service, mock_metadata, *, mutations_payload=None):
    """Wire mocks for a CREATE_OBJECT action returning applied."""
    at = _make_action_type()
    mock_metadata.get_action_type.return_value = at
    mock_metadata.get_execution_by_idempotency_key.return_value = None
    mock_metadata.upsert_object_state.return_value = 1
    mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
    # object_state raw 后态 (backing_column key) — 模拟 Step 8.5 读回的快照
    mock_metadata.get_object_state.return_value = {
        "rid": "obj-1",
        "object_type_api_name": "order",
        "version": 1,
        "properties": {"order_id": "O1", "status": "shipped"},
        "ontology_id": "onto1",
    }
    return at


class TestCreateSyncOutboxRecords:
    """_create_sync_outbox_records: INDEX + ARCHIVE 自动追加 (design §8.3)。"""

    @pytest.mark.asyncio
    async def test_create_object_generates_index_and_archive(
        self, service, mock_metadata
    ):
        """CREATE_OBJECT → 1 INDEX + 1 ARCHIVE (target_ontology=本体)."""
        _setup_create_action(service, mock_metadata)
        await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )
        calls = mock_metadata.create_outbox_record.await_args_list
        index_calls = [c for c in calls if c.kwargs.get("effect_type") == "INDEX"]
        archive_calls = [c for c in calls if c.kwargs.get("effect_type") == "ARCHIVE"]
        assert len(index_calls) == 1
        assert len(archive_calls) == 1

        # INDEX 不分桶 (target_ontology=None)
        assert index_calls[0].kwargs["target_ontology"] is None
        # ARCHIVE 按 ontology 分桶
        assert archive_calls[0].kwargs["target_ontology"] == "hr"

    @pytest.mark.asyncio
    async def test_payload_structure(self, service, mock_metadata):
        """payload 含 object_id/object_type/ontology/version/mutation_type/properties."""
        _setup_create_action(service, mock_metadata)
        await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )
        archive_call = next(
            c for c in mock_metadata.create_outbox_record.await_args_list if c.kwargs["effect_type"] == "ARCHIVE"
        )
        payload = archive_call.kwargs["payload"]
        assert payload["object_type_api_name"] == "order"
        assert payload["ontology_api_name"] == "hr"
        assert payload["version"] == 1
        assert payload["mutation_type"] == "CREATE_OBJECT"
        # properties 是 backing_column key (从 object_state raw 取, 不再翻译)
        assert payload["properties"] == {"order_id": "O1", "status": "shipped"}

    @pytest.mark.asyncio
    async def test_relate_unrelate_skipped(self, service, mock_metadata):
        """RELATE/UNRELATE/CLEAR_LINKS 不生成 sync outbox (关系不同步, design §3.5)."""
        at = _make_action_type()
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        # 无 CREATE/UPDATE/DELETE mutation, 只有 RELATE
        await service.execute_action(
            object_type_api_name="order",
            action_api_name="linkOrder",
            request=ActionExecutionRequest(
                parameters={
                    "status": "x",  # 满足必填参数校验
                    "mutations": [
                        {
                            "type": "RELATE",
                            "rid": "obj-1",
                            "link_type_api_name": "lt",
                            "target_rid": "obj-2",
                        },
                    ],
                }
            ),
            ontology_api_name="hr",
        )
        calls = mock_metadata.create_outbox_record.await_args_list
        sync_calls = [c for c in calls if c.kwargs.get("effect_type") in ("INDEX", "ARCHIVE")]
        assert sync_calls == []

    @pytest.mark.asyncio
    async def test_virtual_target_skipped_direct_call(self, service, mock_metadata):
        """VIRTUAL 目标跳过 sync outbox (架构红线 9).

        Step 5b 会在 execute_action 主流程拒 VIRTUAL (ValidationError), 所以这里
        直接调 _create_sync_outbox_records 验证双重保险分支。
        """
        mock_metadata.get_object_type.return_value = _make_ot(
            api_name="orderVirtual", storage_type="VIRTUAL"
        )
        await service._create_sync_outbox_records(
            execution_id="exec-1",
            mutations=[
                {"type": "CREATE_OBJECT", "rid": "obj-1", "properties": {"order_id": "O1"}},
            ],
            ontology_api_name="hr",
            object_type_api_name="orderVirtual",
            affected_objects={"obj-1": 1},
            raw_before_states={},
            raw_after_states={"obj-1": {"properties": {"order_id": "O1"}}},
        )
        sync_calls = [
            c for c in mock_metadata.create_outbox_record.await_args_list
            if c.kwargs.get("effect_type") in ("INDEX", "ARCHIVE")
        ]
        assert sync_calls == []

    @pytest.mark.asyncio
    async def test_delete_object_uses_before_state_pk(self, service, mock_metadata):
        """DELETE_OBJECT 的 properties 从 raw_before_states 取 PK (删后无后态)."""
        at = _make_action_type()
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        # Step 7.5 读 before (backing_column key), Step 8.5 读 after (None, 已删)
        mock_metadata.get_object_state.return_value = {
            "rid": "obj-1",
            "object_type_api_name": "order",
            "version": 2,
            "properties": {"order_id": "O1", "status": "shipped"},
        }
        await service.execute_action(
            object_type_api_name="order",
            action_api_name="deleteOrder",
            request=ActionExecutionRequest(
                parameters={
                    "status": "x",  # 满足必填参数校验
                    "mutations": [
                        {"type": "DELETE_OBJECT", "rid": "obj-1", "expected_version": 2},
                    ],
                }
            ),
            ontology_api_name="hr",
        )
        archive_call = next(
            c for c in mock_metadata.create_outbox_record.await_args_list if c.kwargs["effect_type"] == "ARCHIVE"
        )
        payload = archive_call.kwargs["payload"]
        assert payload["mutation_type"] == "DELETE_OBJECT"
        # properties 来自 before_state (含 PK)
        assert payload["properties"]["order_id"] == "O1"

    @pytest.mark.asyncio
    async def test_outbox_created_before_commit(self, service, mock_metadata):
        """outbox 在 commit 之前 add (同事务原子性, design §3.6)."""
        _setup_create_action(service, mock_metadata)
        await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )
        # create_outbox_record 在 commit 之前调用 (同事务)
        # (create_outbox_record 是 sync 方法返回 model, 不 await, 但 _session.add 已发生)
        assert mock_metadata.create_outbox_record.called
        assert mock_metadata.commit_transaction.await_count == 1

    @pytest.mark.asyncio
    async def test_outbox_failure_aborts_action_atomic(
        self, service, mock_metadata
    ):
        """outbox 写入失败 → Action 不 commit (原子回滚, transaction-management §5.2).

        transaction-management-best-practices.md §5.2: 事务内 best-effort 操作不吞
        异常, raise 让事务回滚。outbox 与 object_state 原子提交 (design §3.6),
        outbox 写失败时 object_state 也不能提交 (避免幽灵数据)。
        """
        _setup_create_action(service, mock_metadata)
        # create_outbox_record 第一次 (INDEX) 成功, 第二次 (ARCHIVE) 失败
        mock_metadata.create_outbox_record.side_effect = [
            MagicMock(),  # INDEX
            RuntimeError("DB connection lost"),  # ARCHIVE
        ]

        with pytest.raises(RuntimeError, match="DB connection lost"):
            await service.execute_action(
                object_type_api_name="order",
                action_api_name="shipOrder",
                request=ActionExecutionRequest(parameters={"status": "shipped"}),
                ontology_api_name="hr",
            )

        # 关键: commit_transaction 未被调用 → object_state + outbox 都未提交 (原子回滚)
        mock_metadata.commit_transaction.assert_not_called()


class TestEmbeddingOutboxRecords:
    """_create_sync_outbox_records: VECTOR 属性 → EMBEDDING outbox (§14.4).

    CREATE/UPDATE 含 VECTOR 属性时, 除 INDEX + ARCHIVE 外追加 EMBEDDING outbox。
    DELETE 不追加 (行已删)。无 VECTOR 属性的 OT 不追加。
    """

    @pytest.mark.asyncio
    async def test_create_with_vector_generates_embedding_outbox(
        self, service, mock_metadata
    ):
        """CREATE_OBJECT + VECTOR 属性 → 1 INDEX + 1 ARCHIVE + 1 EMBEDDING."""
        mock_metadata.get_object_type.return_value = _make_ot(
            vector_props=[
                {
                    "api_name": "profileEmbedding",
                    "backing_column": "profile_embedding",
                    "source_expression": ["name", "description"],
                }
            ]
        )
        await service._create_sync_outbox_records(
            execution_id="exec-1",
            mutations=[
                {"type": "CREATE_OBJECT", "rid": "obj-1", "properties": {"order_id": "O1"}},
            ],
            ontology_api_name="hr",
            object_type_api_name="order",
            affected_objects={"obj-1": 1},
            raw_before_states={},
            raw_after_states={"obj-1": {"properties": {"order_id": "O1"}}},
        )
        calls = mock_metadata.create_outbox_record.await_args_list
        emb_calls = [c for c in calls if c.kwargs.get("effect_type") == "EMBEDDING"]
        assert len(emb_calls) == 1
        payload = emb_calls[0].kwargs["payload"]
        assert payload["vector_property_api_name"] == "profileEmbedding"
        assert payload["source_expression"] == ["name", "description"]
        assert payload["embedding_column"] == "profile_embedding_embedding"
        assert payload["rid"] == "obj-1"

    @pytest.mark.asyncio
    async def test_delete_with_vector_no_embedding_outbox(
        self, service, mock_metadata
    ):
        """DELETE_OBJECT + VECTOR 属性 → 无 EMBEDDING (行已删)."""
        mock_metadata.get_object_type.return_value = _make_ot(
            vector_props=[
                {
                    "api_name": "profileEmbedding",
                    "backing_column": "profile_embedding",
                    "source_expression": ["name"],
                }
            ]
        )
        await service._create_sync_outbox_records(
            execution_id="exec-1",
            mutations=[
                {"type": "DELETE_OBJECT", "rid": "obj-1", "properties": {}},
            ],
            ontology_api_name="hr",
            object_type_api_name="order",
            affected_objects={"obj-1": -1},
            raw_before_states={"obj-1": {"properties": {"order_id": "O1"}}},
            raw_after_states={},
        )
        calls = mock_metadata.create_outbox_record.await_args_list
        emb_calls = [c for c in calls if c.kwargs.get("effect_type") == "EMBEDDING"]
        assert emb_calls == []

    @pytest.mark.asyncio
    async def test_no_vector_property_no_embedding_outbox(
        self, service, mock_metadata
    ):
        """无 VECTOR 属性的 OT → 无 EMBEDDING outbox."""
        mock_metadata.get_object_type.return_value = _make_ot()
        await service._create_sync_outbox_records(
            execution_id="exec-1",
            mutations=[
                {"type": "CREATE_OBJECT", "rid": "obj-1", "properties": {"order_id": "O1"}},
            ],
            ontology_api_name="hr",
            object_type_api_name="order",
            affected_objects={"obj-1": 1},
            raw_before_states={},
            raw_after_states={"obj-1": {"properties": {"order_id": "O1"}}},
        )
        calls = mock_metadata.create_outbox_record.await_args_list
        emb_calls = [c for c in calls if c.kwargs.get("effect_type") == "EMBEDDING"]
        assert emb_calls == []
