"""B5 tests — Action 执行 lite 简化：不产 INDEX/ARCHIVE outbox。

lite 桌面版 ActionService.execute_action 写 SQLite object_state（B1 OCC 已适配）
+ execution_log + 用户 effect outbox（WEBHOOK/NOTIFICATION/SUB_ACTION），但不自动
追加 INDEX（→Doris）/ARCHIVE（→Iceberg）outbox（lite 无 Doris/Iceberg，A4 已砍后台
消费，产了也堆积）。用户 effect 过滤 write_back/kafka_topic（桌面版边界外）。

复用 test_action_service.py 的 mock 模式（ActionService + mock metadata/catalog/
dataset/rule_engine/authz），但强制 settings.edition='lite' 跑 execute_action，
断言 create_outbox_record 调用的 effect_type 集合。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.action import (
    ActionExecutionRequest,
    ActionTypeParameter,
)
from ontology.core.schemas.ontology import ActionType, DataType
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.action_rule_engine import ActionRuleEngine
from ontology.services.action_service import ActionService

# 异步测试靠 conftest 的 asyncio_mode=auto。
# dataset mock 用 MagicMock() 而非 spec=IcebergStore——纯 lite venv 无 pyiceberg，
# import IcebergStore 会炸。


def _make_action_type(effects: list[dict] | None = None) -> ActionType:
    return ActionType(
        id="at1",
        ontology_id="onto1",
        api_name="shipOrder",
        display_name="Ship Order",
        description="Ship an order",
        affected_object_type_id="ot1",
        parameters={
            "parameters": [
                ActionTypeParameter(api_name="status", display_name="Status", data_type=DataType.STRING).model_dump(),
            ],
            "rules": [],
            "effects": effects or [],
        },
        rules={"rules": []},
        submission_criteria={},
        status="ACTIVE",
        risk_level="low",
        version=1,
        operation_kind="mixed",
        batch_enabled=False,
        created_at=MagicMock(),
        updated_at=MagicMock(),
    )


@pytest.fixture
def lite_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    from ontology.config.settings import settings

    monkeypatch.setattr(settings, "edition", "lite")


@pytest.fixture
def mock_metadata() -> AsyncMock:
    meta = AsyncMock(spec=PostgresMetaStore)
    meta.get_ontology.return_value = MagicMock(id="onto1", api_name="hr")
    meta.get_object_type_by_api_name.return_value = MagicMock(
        id="ot1",
        api_name="order",
        ontology_id="onto1",
        storage_type="MANAGED",
        primary_key="orderId",
        title_property="orderId",
        properties=[],
        capabilities=MagicMock(graph_indexing_enabled=False, geotime_indexing_enabled=False),
    )

    @asynccontextmanager
    async def _noop_transaction():
        yield

    meta.transaction = _noop_transaction
    return meta


@pytest.fixture
def service(mock_metadata) -> ActionService:
    catalog = AsyncMock(spec=GravitinoRegistry)
    # dataset mock 不用 spec=IcebergStore（纯 lite venv 无 pyiceberg，import 会炸）。
    dataset = MagicMock()
    rule_engine = AsyncMock(spec=ActionRuleEngine)
    rule_engine.evaluate = MagicMock(return_value=({}, []))
    rule_engine.evaluate_submission_criteria = MagicMock(return_value=[])
    az = AsyncMock()
    az.check_access.return_value = MagicMock(allowed=True, reason="")
    az.check_action_permission.return_value = set()
    return ActionService(
        metadata=mock_metadata,
        catalog=catalog,
        dataset=dataset,
        rule_engine=rule_engine,
        authorization_service=az,
    )


class TestExecuteActionLite:
    async def test_lite_produces_no_index_archive_outbox(self, lite_edition, service, mock_metadata):
        """lite 下 execute_action 不自动追加 INDEX/ARCHIVE outbox（B5）。

        仍产生用户配置的 webhook effect。object_state + execution_log 正常写。
        """
        at = _make_action_type(effects=[{"type": "webhook", "config": {"url": "https://example.com/wh"}}])
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )

        assert result.status == "applied"
        # object_state OCC 写入 + execution_log + commit 都发生了。
        mock_metadata.upsert_object_state.assert_awaited()
        mock_metadata.create_execution_log.assert_awaited()
        mock_metadata.commit_transaction.assert_awaited_once()

        # outbox：webhook 产生，INDEX/ARCHIVE 0 条（lite 砍掉）。
        calls = mock_metadata.create_outbox_record.await_args_list
        effect_types = [c.kwargs["effect_type"] for c in calls]
        assert "webhook" in effect_types
        assert "INDEX" not in effect_types
        assert "ARCHIVE" not in effect_types
        assert "EMBEDDING" not in effect_types

    async def test_lite_skips_write_back_kafka_effects(self, lite_edition, service, mock_metadata):
        """lite 跳过 write_back/kafka_topic effect（桌面版边界外，B5）。"""
        at = _make_action_type(
            effects=[
                {"type": "webhook", "config": {"url": "https://x"}},
                {"type": "notification", "config": {"message": "done"}},
                {"type": "write_back", "config": {"target_object_type": "order", "op": "upsert"}},
                {"type": "kafka_topic", "config": {"topic": "t"}},
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")

        await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )

        calls = mock_metadata.create_outbox_record.await_args_list
        effect_types = [c.kwargs["effect_type"] for c in calls]
        assert "webhook" in effect_types
        assert "notification" in effect_types
        assert "write_back" not in effect_types
        assert "kafka_topic" not in effect_types

    async def test_lite_no_effects_no_outbox(self, lite_edition, service, mock_metadata):
        """lite 下无用户 effect 且无 INDEX/ARCHIVE → 0 条 outbox。"""
        at = _make_action_type(effects=[])
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )
        assert result.status == "applied"
        mock_metadata.create_outbox_record.assert_not_awaited()
