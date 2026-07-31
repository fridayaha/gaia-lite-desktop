"""Unit tests for ActionService — full Action execution lifecycle.

All Layer dependencies are mocked. Tests validate:
1. ActionType definition
2. Action execution (success path with idempotency, commit, outbox)
3. Error paths (not found, forbidden, validation, conflict)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ontology.core.schemas.action import (
    ActionContext,
    ActionEffectConfig,
    ActionExecutionRequest,
    ActionRule,
    ActionTypeCreate,
    ActionTypeParameter,
)
from ontology.core.schemas.ontology import ActionType, DataType
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.action_rule_engine import ActionRuleEngine
from ontology.services.action_service import ActionService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    meta = AsyncMock(spec=PostgresMetaStore)
    # transaction() 是 asynccontextmanager；让 mock 返回一个真正的 noop async
    # context manager，使 service 的事务单元在单测中不触碰真实 session。
    from contextlib import asynccontextmanager

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
def mock_rule_engine() -> AsyncMock:
    engine = AsyncMock(spec=ActionRuleEngine)
    engine.evaluate = MagicMock(return_value=({}, []))
    # P1 (ADR-011): default submission-criteria evaluation passes.
    engine.evaluate_submission_criteria = MagicMock(return_value=[])
    return engine


@pytest.fixture
def mock_authz() -> AsyncMock:
    """Permissive AuthorizationService mock (PDP allows by default)."""
    from unittest.mock import MagicMock

    az = AsyncMock()
    az.check_access.return_value = MagicMock(allowed=True, reason="")
    az.check_action_permission.return_value = set()
    return az


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_dataset, mock_rule_engine, mock_authz) -> ActionService:
    return ActionService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        dataset=mock_dataset,
        rule_engine=mock_rule_engine,
        authorization_service=mock_authz,
    )


def _make_action_type(ontology_id: str = "onto1", api_name: str = "shipOrder") -> ActionType:
    """Factory helper: create a test ActionType schema."""
    return ActionType(
        id="at1",
        ontology_id=ontology_id,
        api_name=api_name,
        display_name="Ship Order",
        description="Ship an order",
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


class TestDefineActionType:
    """ActionType definition through the full ActionService."""

    @pytest.mark.asyncio
    async def test_define_action_type_success(self, service, mock_metadata):
        """Define an ActionType with full typed parameters."""
        onto = MagicMock(id="onto1", api_name="hr")
        obj_type = MagicMock(id="ot1", api_name="order")
        mock_metadata.get_ontology.return_value = onto
        mock_metadata.get_object_type_by_api_name.return_value = obj_type
        mock_metadata.create_action_type.return_value = MagicMock()

        at_def = ActionTypeCreate(
            api_name="approveOrder",
            display_name="Approve Order",
            affected_object_type_api_name="order",
            parameters=[
                ActionTypeParameter(api_name="orderId", display_name="Order ID", data_type=DataType.STRING),
            ],
            rules=[
                ActionRule(type="constraint", target="orderId", expression='orderId != ""'),
            ],
            effects=[
                ActionEffectConfig(type="webhook", config={"url": "https://example.com"}),
            ],
        )
        result = await service.define_action_type("hr", at_def)

        assert result is not None
        mock_metadata.get_ontology.assert_awaited_once_with("hr")
        mock_metadata.get_object_type_by_api_name.assert_awaited_once_with("onto1", "order")
        mock_metadata.create_action_type.assert_awaited_once()


class TestExecuteAction:
    """Action execution tests covering the full lifecycle."""

    @pytest.mark.asyncio
    async def test_execute_action_success(self, service, mock_metadata, mock_catalog):
        """Successful action execution returns 'applied' status."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")

        # No effects on this action type
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )

        assert result.status == "applied"
        assert result.action_id != ""
        assert "status" in str(result.mutations)
        mock_metadata.commit_transaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_action_with_idempotency_key_new(self, service, mock_metadata, mock_catalog):
        """First request with an idempotency key executes normally."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None  # Not found
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")

        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}, idempotency_key="key-123"),
            ontology_api_name="hr",
        )

        assert result.status == "applied"

    @pytest.mark.asyncio
    async def test_execute_action_idempotency_replay(self, service, mock_metadata, mock_authz):
        """Repeated request with same idempotency key returns cached result."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        existing_exec = MagicMock(
            id="exec-old",
            action_id="act-old",
            mutations=[{"type": "UPDATE_OBJECT", "rid": "obj-1"}],
        )
        mock_metadata.get_execution_by_idempotency_key.return_value = existing_exec

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}, idempotency_key="key-123"),
            ontology_api_name="hr",
        )

        assert result.status == "accepted"
        assert result.action_id == "act-old"
        # Should NOT have committed (no new transaction)
        mock_metadata.commit_transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_action_not_found(self, service, mock_metadata):
        """Non-existent ActionType raises NotFoundError."""
        mock_metadata.get_action_type.side_effect = NotFoundError("ActionType", "ghost")

        with pytest.raises(NotFoundError, match="ActionType not found"):
            await service.execute_action(
                object_type_api_name="order",
                action_api_name="ghostAction",
                request=ActionExecutionRequest(parameters={}),
                ontology_api_name="hr",
            )

    @pytest.mark.asyncio
    async def test_execute_action_forbidden(self, service, mock_metadata, mock_authz):
        """Write access denied raises ForbiddenError."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_authz.check_access.return_value = MagicMock(allowed=False, reason="denied")

        with pytest.raises(ForbiddenError):
            await service.execute_action(
                object_type_api_name="order",
                action_api_name="shipOrder",
                request=ActionExecutionRequest(parameters={"status": "shipped"}),
                ontology_api_name="hr",
            )

    @pytest.mark.asyncio
    async def test_execute_action_validation_failed(self, service, mock_metadata, mock_catalog):
        """Invalid parameters raise ValidationError (HTTP 422 via global handler)."""
        at = _make_action_type()
        at.parameters["parameters"] = [
            ActionTypeParameter(
                api_name="status",
                display_name="Status",
                data_type=DataType.STRING,
                required=True,
            ).model_dump(),
            ActionTypeParameter(
                api_name="priority",
                display_name="Priority",
                data_type=DataType.INTEGER,
                required=True,
            ).model_dump(),
        ]
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None

        with pytest.raises(ValidationError) as exc_info:
            await service.execute_action(
                object_type_api_name="order",
                action_api_name="shipOrder",
                request=ActionExecutionRequest(parameters={"status": "shipped", "extra_unknown": "bad"}),
                ontology_api_name="hr",
            )
        # Required param 'priority' is missing
        assert "priority" in str(exc_info.value).lower()
        assert exc_info.value.code == "VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_execute_action_rule_validation_failed(self, service, mock_metadata, mock_catalog, mock_rule_engine):
        """Rule evaluation failures raise ValidationError (HTTP 422)."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_rule_engine.evaluate = MagicMock(return_value=({}, ["Rule violation: status must be valid"]))

        with pytest.raises(ValidationError) as exc_info:
            await service.execute_action(
                object_type_api_name="order",
                action_api_name="shipOrder",
                request=ActionExecutionRequest(parameters={"status": "invalid"}),
                ontology_api_name="hr",
            )
        assert "status must be valid" in str(exc_info.value)
        assert exc_info.value.code == "VALIDATION_FAILED"

    @pytest.mark.asyncio
    async def test_execute_action_conflict(self, service, mock_metadata, mock_catalog):
        """OCC conflict raises ConflictError (HTTP 409 via global handler)."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 0  # version mismatch

        with pytest.raises(ConflictError) as exc_info:
            await service.execute_action(
                object_type_api_name="order",
                action_api_name="shipOrder",
                request=ActionExecutionRequest(parameters={"rid": "obj-1", "expected_version": 3, "status": "shipped"}),
                ontology_api_name="hr",
            )

        assert exc_info.value.code == "OCC_CONFLICT"
        mock_metadata.rollback_transaction.assert_awaited_once()
        mock_metadata.commit_transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_action_with_outbox_effects(self, service, mock_metadata, mock_catalog):
        """Action with webhook effects creates outbox records."""
        at = _make_action_type()
        at.parameters["effects"] = [
            {"type": "webhook", "config": {"url": "https://example.com/webhook"}},
        ]
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
        # action-sync-outbox-design.md: Action 自动追加 INDEX + ARCHIVE outbox
        # 记录 (每个 CREATE/UPDATE/DELETE mutation 两条), 加上用户配置的 webhook
        # effect 一条 = 3 条。验证 webhook 仍被创建 + sync 记录被创建。
        calls = mock_metadata.create_outbox_record.await_args_list
        effect_types = [c.kwargs["effect_type"] for c in calls]
        assert "webhook" in effect_types
        assert effect_types.count("INDEX") == 1  # 1 mutation → 1 INDEX
        assert effect_types.count("ARCHIVE") == 1  # 1 mutation → 1 ARCHIVE

    @pytest.mark.asyncio
    async def test_execute_action_create_object(self, service, mock_metadata, mock_catalog):
        """CREATE_OBJECT mutation uses expected_version=0."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1  # New version
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-new")

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "created"}),
            ontology_api_name="hr",
        )

        assert result.status == "applied"
        # upsert_object_state was called with expected_version=0 (because no rid in params)
        call_args = mock_metadata.upsert_object_state.call_args
        assert call_args[1]["expected_version"] == 0


class TestVirtualWriteGuard:
    """F5/A1 backend guard — Actions must not write to VIRTUAL objects."""

    @pytest.mark.asyncio
    async def test_execute_action_rejects_virtual_target(self, service, mock_metadata, mock_catalog):
        from ontology.core.exceptions import ValidationError
        from ontology.core.schemas.ontology import ObjectType

        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        virtual_ot = ObjectType(
            id="ot1",
            ontology_id="onto1",
            api_name="orderVirtual",
            display_name="Order Virtual",
            primary_key="id",
            title_property="name",
            storage_type="VIRTUAL",
            created_at=MagicMock(),
            updated_at=MagicMock(),
        )
        mock_metadata.get_object_type.return_value = virtual_ot

        with pytest.raises(ValidationError, match="VIRTUAL"):
            await service.execute_action(
                object_type_api_name="orderVirtual",
                action_api_name="shipOrder",
                request=ActionExecutionRequest(parameters={"status": "shipped"}),
                ontology_api_name="hr",
            )
        # Must NOT have written anything.
        mock_metadata.upsert_object_state.assert_not_called()
        mock_metadata.commit_transaction.assert_not_called()


class TestExecuteActionP1:
    """P1 (ADR-011): context injection, CDL snapshots, Link mutations, modified_by."""

    @pytest.mark.asyncio
    async def test_execute_action_injects_context_to_rules(
        self, service, mock_metadata, mock_catalog, mock_rule_engine
    ):
        """Context is passed through to the rule engine."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.get_object_state.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        ctx = ActionContext(current_user="alice")
        await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(parameters={"status": "shipped"}),
            "hr",
            context=ctx,
        )
        # rule_engine.evaluate must have received the context as 3rd positional arg
        args, _ = mock_rule_engine.evaluate.call_args
        assert args[2] is ctx

    @pytest.mark.asyncio
    async def test_execute_action_records_before_after_snapshot(self, service, mock_metadata, mock_catalog):
        """CDL before/after snapshots are persisted to the execution log."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 2
        # before_snapshot: existing object state; after_snapshot: updated
        mock_metadata.get_object_state.side_effect = lambda oid: {"status": "open"} if oid == "order-1" else None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        # Force an UPDATE mutation by passing rid
        await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(parameters={"rid": "order-1", "status": "shipped"}),
            "hr",
        )
        # create_execution_log called with before/after snapshots
        _, kwargs = mock_metadata.create_execution_log.call_args
        assert kwargs["before_snapshot"]["order-1"] == {"status": "open"}
        assert kwargs["after_snapshot"]["order-1"] == {"status": "open"}  # side_effect returns same

    @pytest.mark.asyncio
    async def test_execute_action_records_modified_by(self, service, mock_metadata, mock_catalog):
        """modified_by from context is passed to upsert_object_state."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.get_object_state.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(parameters={"status": "open"}),
            "hr",
            context=ActionContext(current_user="bob"),
        )
        _, kwargs = mock_metadata.upsert_object_state.call_args
        assert kwargs["modified_by"] == "bob"
        # performed_by on execution log also from context
        _, log_kwargs = mock_metadata.create_execution_log.call_args
        assert log_kwargs["performed_by"] == "bob"

    @pytest.mark.asyncio
    async def test_execute_action_relate_link(self, service, mock_metadata, mock_catalog):
        """RELATE mutation calls add_object_link."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        # Pass explicit mutations with a RELATE entry
        await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(
                parameters={
                    "status": "shipped",
                    "mutations": [
                        {
                            "type": "RELATE",
                            "rid": "order-1",
                            "link_type_api_name": "order_customer",
                            "target_rid": "cust-1",
                        },
                    ],
                }
            ),
            "hr",
        )
        mock_metadata.add_object_link.assert_awaited_once()
        _, kwargs = mock_metadata.add_object_link.call_args
        assert kwargs["link_type_api_name"] == "order_customer"
        assert kwargs["source_rid"] == "order-1"
        assert kwargs["target_rid"] == "cust-1"
        # Link ops must not call upsert_object_state
        mock_metadata.upsert_object_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_action_clear_links(self, service, mock_metadata, mock_catalog):
        """CLEAR_LINKS mutation calls clear_object_links."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(
                parameters={
                    "status": "shipped",
                    "mutations": [
                        {"type": "CLEAR_LINKS", "rid": "order-1", "link_type_api_name": "orderItems"},
                    ],
                }
            ),
            "hr",
        )
        mock_metadata.clear_object_links.assert_awaited_once_with(
            ontology_id=at.ontology_id,
            link_type_api_name="orderItems",
            source_rid="order-1",
        )

    @pytest.mark.asyncio
    async def test_execute_action_submission_criteria_fail(
        self, service, mock_metadata, mock_catalog, mock_rule_engine
    ):
        """Failing submission criterion raises ValidationError (HTTP 422)."""
        at = _make_action_type()
        at.parameters["effects"] = []
        at.submission_criteria = [{"expression": "amount > 0", "error_message": "Amount must be positive"}]
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_rule_engine.evaluate_submission_criteria.return_value = ["Amount must be positive"]

        with pytest.raises(ValidationError) as exc_info:
            await service.execute_action(
                "order",
                "ship_order",
                ActionExecutionRequest(parameters={"status": "shipped"}),
                "hr",
            )
        assert "Amount must be positive" in str(exc_info.value)
        assert exc_info.value.code == "VALIDATION_FAILED"
        mock_metadata.upsert_object_state.assert_not_called()


class TestExecuteActionPermissions:
    """P1 (ADR-011): three-layer permission integration in execute_action."""

    @pytest.mark.asyncio
    async def test_execute_action_rejected_by_execute_permission(self, service, mock_metadata, mock_catalog):
        """Layer 1 failure → ForbiddenError before any write."""
        from ontology.core.exceptions import ForbiddenError

        at = _make_action_type()
        at.parameters["permissions"] = {"roles": ["manager"]}
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None

        ctx = ActionContext(current_user="intern", user_roles=["intern"])
        with pytest.raises(ForbiddenError, match="roles"):
            await service.execute_action(
                "order",
                "ship_order",
                ActionExecutionRequest(parameters={"status": "shipped"}),
                "hr",
                context=ctx,
            )
        mock_metadata.upsert_object_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_action_strips_sensitive_params(self, service, mock_metadata, mock_catalog):
        """Layer 3 strips sensitive params before validation."""
        at = _make_action_type()
        at.parameters["permissions"] = {"sensitive_params": ["credit_limit"]}
        at.parameters["effects"] = []
        # Add credit_limit as an optional declared param so stripping is visible
        at.parameters["parameters"].append(
            ActionTypeParameter(
                api_name="creditLimit", display_name="Credit", data_type=DataType.INTEGER, required=False
            ).model_dump()
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.get_object_state.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        ctx = ActionContext(current_user="user", user_roles=["user"])
        await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(parameters={"status": "open", "credit_limit": 5000}),
            "hr",
            context=ctx,
        )
        # The mutation persisted to object_state must NOT contain credit_limit
        _, kwargs = mock_metadata.upsert_object_state.call_args
        assert "credit_limit" not in kwargs["properties"]
        assert kwargs["properties"]["status"] == "open"

    @pytest.mark.asyncio
    async def test_execute_action_partial_row_permission(self, service, mock_metadata, mock_catalog):
        """Layer 2 partial: some objects forbidden → filtered, rest applied, forbidden listed."""
        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        # Type-level write allowed, but we simulate per-object denial by
        # making check_access return False only on a second call — simpler:
        # patch the authorizer to return a forbidden set.
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.get_object_state.return_value = None
        mock_metadata.create_execution_log.return_value = MagicMock(id="e1", action_id="a1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        # Inject an authorizer that forbids order-2
        from ontology.services.action_auth import ActionAuthorizer

        forbidding_auth = MagicMock(spec=ActionAuthorizer)
        forbidding_auth.check_execute_permission = AsyncMock(return_value=None)
        forbidding_auth.filter_sensitive_parameters = MagicMock(side_effect=lambda at, p, c: p)
        forbidding_auth.check_row_write_permission = AsyncMock(return_value={"order-2"})
        service._authorizer = forbidding_auth

        result = await service.execute_action(
            "order",
            "ship_order",
            ActionExecutionRequest(
                parameters={
                    "status": "shipped",
                    "mutations": [
                        {
                            "type": "UPDATE_OBJECT",
                            "rid": "order-1",
                            "expected_version": 1,
                            "properties": {"status": "shipped"},
                        },
                        {
                            "type": "UPDATE_OBJECT",
                            "rid": "order-2",
                            "expected_version": 1,
                            "properties": {"status": "shipped"},
                        },
                    ],
                }
            ),
            "hr",
        )
        assert result.status == "applied"
        assert "order-2" in result.forbidden_objects
        assert "order-1" not in result.forbidden_objects
        # Only order-1 written
        assert "order-1" in result.affected_objects
        assert "order-2" not in result.affected_objects

    @pytest.mark.asyncio
    async def test_execute_action_all_rows_forbidden_raises(self, service, mock_metadata, mock_catalog):
        """Layer 2 full: all targets forbidden → ForbiddenError."""
        from ontology.core.exceptions import ForbiddenError

        at = _make_action_type()
        at.parameters["effects"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = None
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        from ontology.services.action_auth import ActionAuthorizer

        forbidding_auth = MagicMock(spec=ActionAuthorizer)
        forbidding_auth.check_execute_permission = AsyncMock(return_value=None)
        forbidding_auth.filter_sensitive_parameters = MagicMock(side_effect=lambda at, p, c: p)
        forbidding_auth.check_row_write_permission = AsyncMock(return_value={"order-1"})
        service._authorizer = forbidding_auth

        with pytest.raises(ForbiddenError, match="all target objects"):
            await service.execute_action(
                "order",
                "ship_order",
                ActionExecutionRequest(
                    parameters={
                        "status": "shipped",
                        "mutations": [
                            {
                                "type": "UPDATE_OBJECT",
                                "rid": "order-1",
                                "expected_version": 1,
                                "properties": {"status": "shipped"},
                            },
                        ],
                    }
                ),
                "hr",
            )
        mock_metadata.upsert_object_state.assert_not_called()


class TestActionTypeVersioning:
    """P1 (ADR-011): update / rollback / list versions."""

    @pytest.mark.asyncio
    async def test_update_action_type_bumps_version_and_snapshots(self, service, mock_metadata):
        """update_action_type bumps version and publishes a snapshot."""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        original = ActionType(
            id="at1",
            ontology_id="ont1",
            api_name="approve",
            display_name="Approve",
            description="old",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            risk_level="low",
            version=1,
            operation_kind="mixed",
            batch_enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        updated = original.model_copy(update={"version": 2, "description": "new"})
        mock_metadata.get_action_type.return_value = original
        mock_metadata.update_action_type.return_value = updated

        result = await service.update_action_type("hr", "approve", {"description": "new"})
        assert result.version == 2
        mock_metadata.update_action_type.assert_awaited_once_with(
            "hr", "approve", {"description": "new"}, auto_commit=False
        )
        # 版本快照在事务内与 update 原子提交（不再 best-effort 吞异常）
        mock_metadata.publish_action_type_version.assert_awaited_once()
        _snap_kwargs = mock_metadata.publish_action_type_version.await_args.kwargs
        assert _snap_kwargs["action_type_id"] == "at1"
        assert _snap_kwargs["version"] == 2
        assert _snap_kwargs["auto_commit"] is False

    @pytest.mark.asyncio
    async def test_define_action_type_publishes_v1_snapshot(self, service, mock_metadata):
        """define_action_type 在事务内写入 ActionType + v1 快照（修复快照丢失 bug）。"""
        onto = MagicMock(id="onto1", api_name="hr")
        obj_type = MagicMock(id="ot1", api_name="order")
        mock_metadata.get_ontology.return_value = onto
        mock_metadata.get_object_type_by_api_name.return_value = obj_type
        created = MagicMock(id="at1", version=1)
        mock_metadata.create_action_type.return_value = created

        at_def = ActionTypeCreate(
            api_name="approveOrder",
            display_name="Approve Order",
            affected_object_type_api_name="order",
        )
        await service.define_action_type("hr", at_def)

        # create 在事务内 auto_commit=False
        _create_kwargs = mock_metadata.create_action_type.await_args.kwargs
        assert _create_kwargs["auto_commit"] is False
        # v1 快照发布，auto_commit=False（由事务单元统一提交）
        mock_metadata.publish_action_type_version.assert_awaited_once()
        _snap_kwargs = mock_metadata.publish_action_type_version.await_args.kwargs
        assert _snap_kwargs["action_type_id"] == "at1"
        assert _snap_kwargs["version"] == 1
        assert _snap_kwargs["auto_commit"] is False

    @pytest.mark.asyncio
    async def test_update_snapshot_failure_rolls_back_whole_transaction(self, service, mock_metadata):
        """快照写入失败时整个 update 事务回滚（原子性，不再 best-effort 吞异常）。"""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        original = ActionType(
            id="at1",
            ontology_id="ont1",
            api_name="approve",
            display_name="Approve",
            description="old",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            risk_level="low",
            version=1,
            operation_kind="mixed",
            batch_enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_metadata.get_action_type.return_value = original
        mock_metadata.update_action_type.return_value = original.model_copy(update={"version": 2, "description": "new"})
        # 快照写入失败
        mock_metadata.publish_action_type_version.side_effect = RuntimeError("snapshot boom")

        with pytest.raises(RuntimeError, match="snapshot boom"):
            await service.update_action_type("hr", "approve", {"description": "new"})
        # update 被调过（但在事务内 auto_commit=False，未真正提交；异常由事务回滚）
        mock_metadata.update_action_type.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_action_type_applies_historical_snapshot(self, service, mock_metadata):
        """rollback restores fields from a prior version snapshot."""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        current = ActionType(
            id="at1",
            ontology_id="ont1",
            api_name="approve",
            display_name="Approve",
            description="new",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            risk_level="low",
            version=3,
            operation_kind="mixed",
            batch_enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        snapshot_model = MagicMock()
        snapshot_model.snapshot = {
            "display_name": "Approve (old)",
            "description": "old desc",
            "parameters": {"old": True},
            "rules": {},
            "submission_criteria": {},
            "risk_level": "medium",
            "operation_kind": "update",
            "batch_enabled": False,
        }
        rolled_back = current.model_copy(update={"version": 4, "description": "old desc"})
        mock_metadata.get_action_type.return_value = current
        mock_metadata.get_action_type_version.return_value = snapshot_model
        mock_metadata.update_action_type.return_value = rolled_back

        result = await service.rollback_action_type("hr", "approve", target_version=1)
        assert result.version == 4
        # update called with fields from snapshot
        args, _ = mock_metadata.update_action_type.call_args
        assert args[0] == "hr"
        assert args[1] == "approve"
        assert args[2]["description"] == "old desc"
        assert args[2]["risk_level"] == "medium"

    @pytest.mark.asyncio
    async def test_rollback_unknown_version_raises(self, service, mock_metadata):
        """Rolling back to a non-existent version raises NotFoundError."""
        from datetime import UTC, datetime

        from ontology.core.schemas.ontology import ActionType

        current = ActionType(
            id="at1",
            ontology_id="ont1",
            api_name="approve",
            display_name="Approve",
            description="",
            affected_object_type_id="ot1",
            parameters={},
            rules={},
            submission_criteria={},
            status="ACTIVE",
            risk_level="low",
            version=2,
            operation_kind="mixed",
            batch_enabled=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        mock_metadata.get_action_type.return_value = current
        mock_metadata.get_action_type_version.return_value = None

        with pytest.raises(NotFoundError):
            await service.rollback_action_type("hr", "approve", target_version=99)


class TestPreviewAction:
    """P1 (ADR-011): dry-run preview does not persist."""

    @pytest.mark.asyncio
    async def test_preview_action_does_not_persist(self, service, mock_metadata, mock_catalog):
        """preview returns mutations but writes nothing."""
        mock_metadata.get_action_type.return_value = _make_action_type()
        mock_metadata.get_object_state.return_value = None
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        result = await service.preview_action(
            "order",
            "ship_order",
            ActionExecutionRequest(parameters={"status": "shipped"}),
            "hr",
        )
        assert result.valid is True
        assert len(result.mutations) > 0
        mock_metadata.upsert_object_state.assert_not_called()
        mock_metadata.create_execution_log.assert_not_called()
        mock_metadata.create_outbox_record.assert_not_called()
        mock_metadata.commit_transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_preview_action_validation_failed(self, service, mock_metadata):
        """preview surfaces validation failures without persisting."""
        at = _make_action_type()
        # status is required; omit it
        mock_metadata.get_action_type.return_value = at

        result = await service.preview_action(
            "order",
            "ship_order",
            ActionExecutionRequest(parameters={}),  # missing required status
            "hr",
        )
        assert result.valid is False
        assert any("status" in e for e in result.validation_errors)
        mock_metadata.upsert_object_state.assert_not_called()


# ── ADR Action Mutation Mapping: 声明式 Ontology Rules 测试 ──


@pytest.fixture
def mock_object_query() -> AsyncMock:
    from ontology.services.object_query_service import ObjectQueryService

    return AsyncMock(spec=ObjectQueryService)


@pytest.fixture
def service_with_query(
    mock_metadata, mock_catalog, mock_dataset, mock_rule_engine, mock_object_query, mock_authz
) -> ActionService:
    return ActionService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        dataset=mock_dataset,
        rule_engine=mock_rule_engine,
        object_query_service=mock_object_query,
        authorization_service=mock_authz,
    )


def _make_action_type_with_rules(
    ontology_id: str = "onto1",
    api_name: str = "delayFlight",
    ontology_rules: list[dict] | None = None,
    effects: list[dict] | None = None,
    parameters: list[dict] | None = None,
) -> ActionType:
    """Factory: ActionType 带声明式 Ontology Rules。"""
    if parameters is None:
        parameters = [
            {"api_name": "flight", "display_name": "Flight", "data_type": "LONG", "required": True},
            {"api_name": "delayMinutes", "display_name": "Delay", "data_type": "INTEGER", "required": False},
            {"api_name": "operator", "display_name": "Operator", "data_type": "STRING", "required": False},
        ]
    return ActionType(
        id="at1",
        ontology_id=ontology_id,
        api_name=api_name,
        display_name="Delay Flight",
        description="",
        affected_object_type_id="ot1",
        parameters={
            "parameters": parameters,
            "rules": [],
            "effects": effects or [],
            "ontology_rules": ontology_rules or [],
        },
        rules={"rules": []},
        submission_criteria={},
        status="ACTIVE",
        created_at=MagicMock(),
        updated_at=MagicMock(),
    )


class TestOntologyRulesModifyObject:
    """ADR Action Mutation Mapping: ModifyObject 规则。"""

    @pytest.mark.asyncio
    async def test_modify_object_hydrate_then_update(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """object_state 缺失 → hydrate 全量 → apply Modify → UPDATE_OBJECT v1."""
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {
                        "status": {"source": "STATIC_VALUE", "value": "Delayed"},
                        "delayMinutes": {"source": "PARAMETER", "value": "delayMinutes"},
                    },
                    "on_missing": "raise_not_found",
                }
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = None  # object_state 缺失
        # hydrate: object_query 返回全量 flight
        mock_object_query.hydrate_by_pk.return_value = {"flightId": 100, "status": "Scheduled", "delayMinutes": 0}
        # hydrate upsert (v1) → 1; apply Modify (v1→v2) → 2
        mock_metadata.upsert_object_state.side_effect = [1, 2]
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(
                parameters={"flight": 100, "delayMinutes": 60},
                idempotency_key="k1",
            ),
            ontology_api_name="airline",
        )

        assert result.status == "applied"
        # mutation 应是 UPDATE_OBJECT,合并 hydrate 全量 + rule 覆盖
        mut = result.mutations[0]
        assert mut["type"] == "UPDATE_OBJECT"
        assert mut["rid"] == "100"
        assert mut["properties"]["status"] == "Delayed"
        assert mut["properties"]["delayMinutes"] == 60
        # hydrate 基底字段保留
        assert mut["properties"]["flightId"] == 100

    @pytest.mark.asyncio
    async def test_modify_object_not_found_raises_404(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """object_state 缺失 + Doris 也不存在 → NotFoundError (write_004/012)。"""
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                    "on_missing": "raise_not_found",
                }
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = None
        mock_object_query.hydrate_by_pk.return_value = None  # Doris/源表都没有
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        with pytest.raises(NotFoundError):
            await service_with_query.execute_action(
                object_type_api_name="flight",
                action_api_name="delayFlight",
                request=ActionExecutionRequest(parameters={"flight": 99999999}),
                ontology_api_name="airline",
            )

    @pytest.mark.asyncio
    async def test_modify_object_existing_state_no_hydrate(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """object_state 已存在 → 直接用其 version 做 OCC,不 hydrate。"""
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                }
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = {
            "properties": {"flightId": 100, "status": "Scheduled"},
            "version": 5,
        }
        mock_metadata.upsert_object_state.return_value = 6
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100}, idempotency_key="k2"),
            ontology_api_name="airline",
        )

        assert result.status == "applied"
        mut = result.mutations[0]
        assert mut["expected_version"] == 5  # OCC 衔接:用读出的 version
        # 不应 hydrate
        mock_object_query.hydrate_by_pk.assert_not_awaited()


class TestOntologyRulesCreateObject:
    """ADR Action Mutation Mapping: CreateObject 规则(副表日志 write_audit)。"""

    @pytest.mark.asyncio
    async def test_create_object_with_system_generated_pk(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """CreateObject + SYSTEM_GENERATED uuid 主键 → CREATE_OBJECT。"""
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "CreateObject",
                    "target_object_type": "flightStatusLog",
                    "properties": {
                        "logId": {"source": "SYSTEM_GENERATED", "value": "uuid"},
                        "flightId": {"source": "PARAMETER", "value": "flight"},
                        "newStatus": {"source": "STATIC_VALUE", "value": "Delayed"},
                    },
                }
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100}, idempotency_key="k3"),
            ontology_api_name="airline",
        )

        assert result.status == "applied"
        create_muts = [m for m in result.mutations if m["type"] == "CREATE_OBJECT"]
        assert len(create_muts) == 1
        assert create_muts[0]["object_type"] == "flightStatusLog"
        assert create_muts[0]["properties"]["flightId"] == 100
        assert create_muts[0]["properties"]["newStatus"] == "Delayed"
        assert create_muts[0]["properties"]["logId"]  # uuid 已生成


class TestOntologyRulesValidation:
    """ADR Action Mutation Mapping: 主键不可改 + 值来源校验。"""

    @pytest.mark.asyncio
    async def test_modify_object_primary_key_in_properties_rejected(
        self, service_with_query, mock_metadata, mock_catalog
    ):
        """执行期校验:主键(flightId)不可出现在 Modify 的 properties → 422。"""
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {
                        "flightId": {"source": "PARAMETER", "value": "flight"},  # 主键不可改
                    },
                }
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        mock_metadata.get_object_type.return_value = ot

        with pytest.raises(ValidationError, match="Primary key"):
            await service_with_query.execute_action(
                object_type_api_name="flight",
                action_api_name="delayFlight",
                request=ActionExecutionRequest(parameters={"flight": 100}),
                ontology_api_name="airline",
            )

    def test_resolve_value_source_static(self, mock_metadata, mock_catalog, mock_dataset):
        """STATIC_VALUE 字面量直出。"""
        from ontology.core.schemas.action import ValueSource

        svc = ActionService(
            metadata=mock_metadata,
            catalog=mock_catalog,
            dataset=mock_dataset,
        )
        vs = ValueSource(source="STATIC_VALUE", value="Delayed")
        assert svc._resolve_value_source(vs, {}, ActionContext()) == "Delayed"

    def test_resolve_value_source_system_context_timestamp(self, mock_metadata, mock_catalog, mock_dataset):
        """SYSTEM_CONTEXT CURRENT_TIMESTAMP 取 ctx.current_timestamp。"""
        from ontology.core.schemas.action import ValueSource

        svc = ActionService(
            metadata=mock_metadata,
            catalog=mock_catalog,
            dataset=mock_dataset,
        )
        ctx = ActionContext()
        vs = ValueSource(source="SYSTEM_CONTEXT", value="CURRENT_TIMESTAMP")
        assert svc._resolve_value_source(vs, {}, ctx) == ctx.current_timestamp.isoformat()

    def test_resolve_value_source_expression(self, mock_metadata, mock_catalog, mock_dataset):
        """EXPRESSION 用 simpleeval 求值,可引用参数。"""
        from ontology.core.schemas.action import ValueSource
        from ontology.services.action_rule_engine import ActionRuleEngine

        svc = ActionService(
            metadata=mock_metadata,
            catalog=mock_catalog,
            dataset=mock_dataset,
            rule_engine=ActionRuleEngine(),
        )
        vs = ValueSource(source="EXPRESSION", value="delayMinutes * 2")
        result = svc._resolve_value_source(vs, {"delayMinutes": 30}, ActionContext())
        assert result == 60


class TestRuleCompileMerge:
    """断裂 2 修复: 同对象多规则编译合并 (对齐 Palantir "compile rules to
    generate a single edit per object")。_compile_mutations 是纯函数, 直接单测。"""

    def test_merge_two_updates_same_object_properties_latter_wins(self, mock_metadata, mock_catalog, mock_dataset):
        """同 rid 两条 UPDATE_OBJECT → 合并为一条, 属性后者覆盖, version 取后者。"""
        svc = ActionService(metadata=mock_metadata, catalog=mock_catalog, dataset=mock_dataset)
        mutations = [
            {
                "type": "UPDATE_OBJECT",
                "rid": "r1",
                "object_type": "flight",
                "expected_version": 1,
                "properties": {"status": "Delayed", "note": "a"},
            },
            {
                "type": "UPDATE_OBJECT",
                "rid": "r1",
                "object_type": "flight",
                "expected_version": 2,
                "properties": {"status": "Cancelled"},
            },
        ]
        result = svc._compile_mutations(mutations)
        assert len(result) == 1
        assert result[0]["type"] == "UPDATE_OBJECT"
        assert result[0]["rid"] == "r1"
        # note 保留 (前者), status 后者胜
        assert result[0]["properties"] == {"status": "Cancelled", "note": "a"}
        assert result[0]["expected_version"] == 2  # 取后者

    def test_merge_create_then_update_becomes_create_with_merged_props(self, mock_metadata, mock_catalog, mock_dataset):
        """CREATE + UPDATE 同 rid → 合并为 CREATE, 属性合并, expected_version=0。"""
        svc = ActionService(metadata=mock_metadata, catalog=mock_catalog, dataset=mock_dataset)
        mutations = [
            {
                "type": "CREATE_OBJECT",
                "rid": "r2",
                "object_type": "log",
                "expected_version": 0,
                "properties": {"flightId": 100, "op": "delay"},
            },
            {
                "type": "UPDATE_OBJECT",
                "rid": "r2",
                "object_type": "log",
                "expected_version": 1,
                "properties": {"newStatus": "Delayed"},
            },
        ]
        result = svc._compile_mutations(mutations)
        assert len(result) == 1
        assert result[0]["type"] == "CREATE_OBJECT"
        assert result[0]["expected_version"] == 0
        assert result[0]["properties"] == {"flightId": 100, "op": "delay", "newStatus": "Delayed"}

    def test_link_mutations_not_merged(self, mock_metadata, mock_catalog, mock_dataset):
        """RELATE/UNRELATE 不参与合并 (多个 link 操作语义不同)。"""
        svc = ActionService(metadata=mock_metadata, catalog=mock_catalog, dataset=mock_dataset)
        mutations = [
            {
                "type": "RELATE",
                "rid": "r1",
                "link_type_api_name": "assigned",
                "target_rid": "r2",
                "expected_version": 0,
                "properties": {},
            },
            {
                "type": "RELATE",
                "rid": "r1",
                "link_type_api_name": "assigned",
                "target_rid": "r3",
                "expected_version": 0,
                "properties": {},
            },
            {
                "type": "UPDATE_OBJECT",
                "rid": "r1",
                "object_type": "flight",
                "expected_version": 1,
                "properties": {"status": "Delayed"},
            },
        ]
        result = svc._compile_mutations(mutations)
        # 2 RELATE 保留 + 1 UPDATE_OBJECT, 共 3 条
        assert len(result) == 3
        relates = [m for m in result if m["type"] == "RELATE"]
        assert len(relates) == 2
        assert {r["target_rid"] for r in relates} == {"r2", "r3"}

    def test_different_objects_not_merged(self, mock_metadata, mock_catalog, mock_dataset):
        """不同 rid 的 mutation 各自保留, 不合并。"""
        svc = ActionService(metadata=mock_metadata, catalog=mock_catalog, dataset=mock_dataset)
        mutations = [
            {
                "type": "UPDATE_OBJECT",
                "rid": "r1",
                "object_type": "flight",
                "expected_version": 1,
                "properties": {"status": "Delayed"},
            },
            {
                "type": "UPDATE_OBJECT",
                "rid": "r2",
                "object_type": "flight",
                "expected_version": 1,
                "properties": {"status": "Cancelled"},
            },
        ]
        result = svc._compile_mutations(mutations)
        assert len(result) == 2
        assert {m["rid"] for m in result} == {"r1", "r2"}

    def test_update_property_normalized_to_update_object(self, mock_metadata, mock_catalog, mock_dataset):
        """UPDATE_PROPERTY 归一为 UPDATE_OBJECT 参与合并。"""
        svc = ActionService(metadata=mock_metadata, catalog=mock_catalog, dataset=mock_dataset)
        mutations = [
            {
                "type": "UPDATE_PROPERTY",
                "rid": "r1",
                "object_type": "flight",
                "expected_version": 1,
                "properties": {"status": "Delayed"},
            },
            {
                "type": "UPDATE_OBJECT",
                "rid": "r1",
                "object_type": "flight",
                "expected_version": 2,
                "properties": {"note": "weather"},
            },
        ]
        result = svc._compile_mutations(mutations)
        assert len(result) == 1
        assert result[0]["type"] == "UPDATE_OBJECT"
        assert result[0]["properties"] == {"status": "Delayed", "note": "weather"}

    def test_empty_and_single_passthrough(self, mock_metadata, mock_catalog, mock_dataset):
        """空列表 / 单条 mutation 原样返回。"""
        svc = ActionService(metadata=mock_metadata, catalog=mock_catalog, dataset=mock_dataset)
        assert svc._compile_mutations([]) == []
        single = [{"type": "DELETE_OBJECT", "rid": "r1", "expected_version": 0, "properties": {}}]
        assert svc._compile_mutations(single) == single

    @pytest.mark.asyncio
    async def test_compile_reduces_outbox_records_for_same_object(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """集成: 同对象两条 ModifyObject 规则 → 合并为单 mutation → 单条 INDEX/ARCHIVE outbox。

        验证合并后不再为同一对象生成多条同步 outbox (断裂 2 的核心收益)。
        """
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                },
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"note": {"source": "STATIC_VALUE", "value": "weather"}},
                },
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = {
            "rid": "r1",
            "version": 1,
            "properties": {"flightId": 100, "status": "Scheduled"},
            "object_type_api_name": "flight",
        }
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        ot.properties = []
        mock_metadata.get_object_type.return_value = ot

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100}, idempotency_key="k1"),
            ontology_api_name="airline",
        )
        assert result.status == "applied"
        # 合并后只有 1 条 object mutation
        assert len(result.mutations) == 1
        mut = result.mutations[0]
        assert mut["properties"]["status"] == "Delayed"
        assert mut["properties"]["note"] == "weather"
        # upsert_object_state 只被调一次 (合并后单 mutation),
        # 而非两次 (合并前两条 mutation 各调一次)。
        assert mock_metadata.upsert_object_state.await_count == 1


class TestInvalidCombinations:
    """断裂 3 修复: 执行期 invalid combinations 校验
    (对齐 Palantir rules 文档 "Invalid combinations")。"""

    @pytest.mark.asyncio
    async def test_modify_same_object_twice_merged(self, service_with_query, mock_metadata, mock_catalog):
        """同一对象两条 ModifyObject 规则 → 不报冲突, 由 _compile_mutations 合并为单 mutation。

        Palantir 语义: 多条规则编译成单 edit (属性后者胜), 不是 "一个 op per object"。
        Invalid combinations 只拦语义矛盾 (先删后改/重复创建等), 不拦同 op 多条。
        """
        at = _make_action_type_with_rules(
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                },
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"note": {"source": "STATIC_VALUE", "value": "weather"}},
                },
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = {
            "rid": "r1",
            "version": 1,
            "properties": {"flightId": 100, "status": "Scheduled"},
            "object_type_api_name": "flight",
        }
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        ot.properties = []
        mock_metadata.get_object_type.return_value = ot

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100}, idempotency_key="k1"),
            ontology_api_name="airline",
        )
        assert result.status == "applied"
        # 合并为单 mutation, 属性后者胜
        assert len(result.mutations) == 1
        assert result.mutations[0]["properties"]["status"] == "Delayed"
        assert result.mutations[0]["properties"]["note"] == "weather"

    @pytest.mark.asyncio
    async def test_delete_then_modify_same_object_rejected(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """先删后改同一对象 → 422 (Palantir: delete before add/modify 禁止)。"""
        at = _make_action_type_with_rules(
            ontology_rules=[
                {"type": "DeleteObject", "target_parameter": "flight", "target_object_type": "flight"},
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                },
            ]
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        mock_metadata.get_object_type.return_value = ot
        mock_object_query.hydrate_by_pk.return_value = {"flightId": 100, "status": "Scheduled"}

        with pytest.raises(ValidationError, match="Invalid rule combination"):
            await service_with_query.execute_action(
                object_type_api_name="flight",
                action_api_name="delayFlight",
                request=ActionExecutionRequest(parameters={"flight": 100}),
                ontology_api_name="airline",
            )

    @pytest.mark.asyncio
    async def test_conditional_rules_both_execute_merged(self, service_with_query, mock_metadata, mock_catalog):
        """两条条件规则条件都为真且命中同对象 → 合并通过, 不报冲突 (同 op 多条合法)。"""
        at = _make_action_type_with_rules(
            parameters=[
                {"api_name": "flight", "display_name": "Flight", "data_type": "LONG", "required": True},
                {"api_name": "isUrgent", "display_name": "Urgent", "data_type": "BOOLEAN", "required": False},
            ],
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "condition": "isUrgent == True",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                },
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "condition": "isUrgent == True",
                    "properties": {"note": {"source": "STATIC_VALUE", "value": "urgent"}},
                },
            ],
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = {
            "rid": "r1",
            "version": 1,
            "properties": {"flightId": 100, "status": "Scheduled"},
            "object_type_api_name": "flight",
        }
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        ot.properties = []
        mock_metadata.get_object_type.return_value = ot
        service_with_query._rule_engine = ActionRuleEngine()

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100, "isUrgent": True}, idempotency_key="k1"),
            ontology_api_name="airline",
        )
        assert result.status == "applied"
        assert len(result.mutations) == 1
        assert result.mutations[0]["properties"]["status"] == "Delayed"
        assert result.mutations[0]["properties"]["note"] == "urgent"

    @pytest.mark.asyncio
    async def test_conditional_rules_one_skipped_no_conflict(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """两条互斥条件规则, 只一条执行 → 不报冲突 (条件分支合法)。"""
        at = _make_action_type_with_rules(
            parameters=[
                {"api_name": "flight", "display_name": "Flight", "data_type": "LONG", "required": True},
                {"api_name": "isUrgent", "display_name": "Urgent", "data_type": "BOOLEAN", "required": False},
            ],
            ontology_rules=[
                # condition 为假 → 跳过, 不参与组合校验
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "condition": "isUrgent == True",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                },
                # condition 为真 → 执行
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "condition": "isUrgent == False",
                    "properties": {"note": {"source": "STATIC_VALUE", "value": "normal"}},
                },
            ],
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = {
            "rid": "r1",
            "version": 1,
            "properties": {"flightId": 100, "status": "Scheduled"},
            "object_type_api_name": "flight",
        }
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        ot.properties = []
        mock_metadata.get_object_type.return_value = ot
        service_with_query._rule_engine = ActionRuleEngine()

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100, "isUrgent": False}, idempotency_key="k1"),
            ontology_api_name="airline",
        )
        # 只执行了第二条 → applied, 不报冲突
        assert result.status == "applied"

    @pytest.mark.asyncio
    async def test_different_objects_no_conflict(
        self, service_with_query, mock_metadata, mock_catalog, mock_object_query
    ):
        """两条规则改不同对象 → 不报冲突。"""
        at = _make_action_type_with_rules(
            parameters=[
                {"api_name": "flight", "display_name": "Flight", "data_type": "LONG", "required": True},
                {"api_name": "aircraft", "display_name": "Aircraft", "data_type": "LONG", "required": False},
            ],
            ontology_rules=[
                {
                    "type": "ModifyObject",
                    "target_parameter": "flight",
                    "target_object_type": "flight",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "Delayed"}},
                },
                {
                    "type": "ModifyObject",
                    "target_parameter": "aircraft",
                    "target_object_type": "aircraft",
                    "properties": {"status": {"source": "STATIC_VALUE", "value": "InUse"}},
                },
            ],
        )
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.get_object_state.return_value = {
            "rid": "r1",
            "version": 1,
            "properties": {"id": 100, "status": "x"},
            "object_type_api_name": "flight",
        }
        mock_metadata.upsert_object_state.return_value = 2
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        ot.primary_key = "flightId"
        ot.properties = []
        mock_metadata.get_object_type.return_value = ot

        result = await service_with_query.execute_action(
            object_type_api_name="flight",
            action_api_name="delayFlight",
            request=ActionExecutionRequest(parameters={"flight": 100, "aircraft": 200}, idempotency_key="k1"),
            ontology_api_name="airline",
        )
        assert result.status == "applied"
        # 两个不同对象的 mutation 都保留
        assert len(result.mutations) == 2


class TestOntologyRulesBackwardCompat:
    """ADR Action Mutation Mapping: 无 ontology_rules 时回退旧行为。"""

    @pytest.mark.asyncio
    async def test_no_ontology_rules_falls_back_to_legacy(self, service, mock_metadata, mock_catalog):
        """无 ontology_rules → 走旧 _build_mutations(CREATE_OBJECT 默认)。"""
        at = _make_action_type()  # 无 ontology_rules
        at.parameters["ontology_rules"] = []
        mock_metadata.get_action_type.return_value = at
        mock_metadata.get_execution_by_idempotency_key.return_value = None
        mock_metadata.upsert_object_state.return_value = 1
        mock_metadata.create_execution_log.return_value = MagicMock(id="exec-1", action_id="act-1")
        ot = MagicMock()
        ot.storage_type = "MANAGED"
        mock_metadata.get_object_type.return_value = ot

        result = await service.execute_action(
            object_type_api_name="order",
            action_api_name="shipOrder",
            request=ActionExecutionRequest(parameters={"status": "shipped"}),
            ontology_api_name="hr",
        )

        assert result.status == "applied"
        # 旧默认 CREATE_OBJECT
        assert result.mutations[0]["type"] == "CREATE_OBJECT"
