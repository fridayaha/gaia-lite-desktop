"""object_state properties key = backing_column (CDC path B end-to-end unblock).

Verifies the architecture decision (HANDOFF.md §四): object_state.properties
JSONB is keyed by backing_column (snake_case physical column name), while the
Action surface (REST / rules / audit snapshots) speaks api_name. The
conversion happens at the ActionService write boundary (api_name →
backing_column before upsert_object_state) and the read boundaries
(backing_column → api_name in audit snapshots).

This is what unblocks path B (Kafka → Doris): the Kafka message JSON key ==
Doris idx column name == backing_column, so Doris stream-load matches columns
by name without per-table jsonpaths (docs/bugfix/path-b-kafka-doris-schema-mismatch.md).
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.schemas.action import ActionExecutionRequest, ActionTypeParameter
from ontology.core.schemas.ontology import (
    ActionType,
    BackingColumnRef,
    DataType,
    ObjectType,
    PropertyDef,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.action_rule_engine import ActionRuleEngine
from ontology.services.action_service import ActionService

_TS = datetime(2026, 1, 1, tzinfo=UTC)


def _prop(api_name: str, backing_column: str, *, indexed: bool = False, pk: bool = False) -> PropertyDef:
    return PropertyDef(
        id=f"p_{api_name}",
        object_type_id="ot1",
        api_name=api_name,
        display_name=api_name,
        data_type=DataType.STRING,
        indexed=indexed,
        is_primary_key=pk,
        backing_mapping=BackingColumnRef(
            dataset_api_name="ds",
            backing_catalog="cat",
            backing_schema="public",
            backing_table="t_orders",
            backing_column=backing_column,
        ),
        created_at=_TS,
        updated_at=_TS,
    )


def _ot() -> ObjectType:
    """Order OT: api_name (camelCase) ≠ backing_column (snake_case)."""
    return ObjectType(
        id="ot1",
        ontology_id="onto1",
        api_name="Order",
        display_name="Order",
        primary_key="orderId",
        title_property="orderId",
        storage_type="MANAGED",
        properties=[
            _prop("orderId", "order_id", pk=True, indexed=True),
            _prop("customerName", "customer_name", indexed=True),
            _prop("status", "status", indexed=True),  # api_name == backing_column (passthrough)
        ],
        created_at=_TS,
        updated_at=_TS,
    )


def _action_type() -> ActionType:
    return ActionType(
        id="at1",
        ontology_id="onto1",
        api_name="shipOrder",
        display_name="Ship",
        description="",
        affected_object_type_id="ot1",
        parameters={
            "parameters": [
                ActionTypeParameter(
                    api_name="orderId", display_name="Order ID", data_type=DataType.STRING, required=False
                ).model_dump(),
                ActionTypeParameter(
                    api_name="customerName", display_name="Customer", data_type=DataType.STRING, required=False
                ).model_dump(),
                ActionTypeParameter(
                    api_name="status", display_name="Status", data_type=DataType.STRING, required=False
                ).model_dump(),
            ],
            "rules": [],
            "effects": [],
            "ontology_rules": [],
        },
        rules={"rules": []},
        submission_criteria={},
        status="ACTIVE",
        created_at=_TS,
        updated_at=_TS,
    )


@pytest.fixture
def mock_metadata() -> AsyncMock:
    meta = AsyncMock(spec=PostgresMetaStore)

    @asynccontextmanager
    async def _noop_transaction():
        yield

    meta.transaction = _noop_transaction
    return meta


@pytest.fixture
def mock_catalog() -> AsyncMock:
    return AsyncMock(spec=GravitinoRegistry)


@pytest.fixture
def mock_dataset() -> AsyncMock:
    return AsyncMock(spec=IcebergStore)


@pytest.fixture
def mock_authz() -> AsyncMock:
    """Permissive AuthorizationService mock (PDP allows by default)."""
    az = AsyncMock()
    az.check_access.return_value = MagicMock(allowed=True, reason="")
    az.check_action_permission.return_value = set()
    return az


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_dataset, mock_authz) -> ActionService:
    rule_engine = MagicMock(spec=ActionRuleEngine)
    rule_engine.evaluate = MagicMock(return_value=({}, []))
    rule_engine.evaluate_submission_criteria = MagicMock(return_value=[])
    return ActionService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        dataset=mock_dataset,
        rule_engine=rule_engine,
        authorization_service=mock_authz,
    )


class TestObjectStateBackingColumnKeys:
    """Action writes object_state with backing_column keys; reads return api_name."""

    @pytest.mark.asyncio
    async def test_create_writes_backing_column_keys(self, service, mock_metadata, mock_catalog):
        """CREATE_OBJECT mutation persists properties keyed by backing_column."""
        mock_metadata.get_action_type.return_value = _action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.get_object_type.return_value = _ot()
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")

        # parameters use api_name (Action surface)
        await service.execute_action(
            object_type_api_name="Order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"orderId": "O1", "customerName": "Acme", "status": "open"}),
            ontology_api_name="hr",
        )

        _, kwargs = mock_metadata.upsert_object_state.call_args
        props = kwargs["properties"]
        # api_name → backing_column rename for differing pairs.
        assert props["order_id"] == "O1"
        assert props["customer_name"] == "Acme"
        # api_name == backing_column → unchanged.
        assert props["status"] == "open"
        # Original camelCase keys must NOT be present.
        assert "orderId" not in props
        assert "customerName" not in props

    @pytest.mark.asyncio
    async def test_update_snapshot_reads_back_as_api_name(self, service, mock_metadata, mock_catalog):
        """UPDATE_OBJECT: before/after snapshots surface properties as api_name."""
        at = _action_type()
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.get_object_type.return_value = _ot()
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")

        # existing object_state stores backing_column keys (post-migration state).
        existing = {
            "rid": "O1",
            "object_type_api_name": "Order",
            "version": 1,
            "properties": {"order_id": "O1", "customer_name": "Acme", "status": "open"},
        }
        # before_snapshot read returns existing; after_snapshot read returns
        # the same dict (mock) — both go through _snapshot_to_api.
        mock_metadata.get_object_state.return_value = existing

        await service.execute_action(
            object_type_api_name="Order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"rid": "O1", "status": "shipped"}),
            ontology_api_name="hr",
        )

        _, kwargs = mock_metadata.create_execution_log.call_args
        before = kwargs["before_snapshot"]["O1"]
        # backing_column → api_name in the audit snapshot.
        assert before["properties"]["orderId"] == "O1"
        assert before["properties"]["customerName"] == "Acme"
        assert before["properties"]["status"] == "open"
        # backing_column keys must NOT leak into the audit snapshot.
        assert "order_id" not in before["properties"]
        assert "customer_name" not in before["properties"]

    @pytest.mark.asyncio
    async def test_update_upsert_uses_backing_column_keys(self, service, mock_metadata, mock_catalog):
        """UPDATE_OBJECT merges existing (backing) + override (api) and writes backing."""
        at = _action_type()
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.get_object_type.return_value = _ot()
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")

        existing = {
            "rid": "O1",
            "object_type_api_name": "Order",
            "version": 1,
            "properties": {"order_id": "O1", "customer_name": "Acme", "status": "open"},
        }
        mock_metadata.get_object_state.return_value = existing

        await service.execute_action(
            object_type_api_name="Order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"rid": "O1", "status": "shipped"}),
            ontology_api_name="hr",
        )

        _, kwargs = mock_metadata.upsert_object_state.call_args
        props = kwargs["properties"]
        # Legacy _build_mutations path sends only the override (no merge with
        # existing); the merge happens in object_state via UPSERT. The override
        # is translated api_name → backing_column.
        assert props["status"] == "shipped"
        assert "status" not in props or props["status"] == "shipped"
        # No camelCase keys leak.
        assert "orderId" not in props
        assert "customerName" not in props
        # expected_version defaults to 0 (caller didn't pass expected_version).
        assert kwargs["expected_version"] == 0
