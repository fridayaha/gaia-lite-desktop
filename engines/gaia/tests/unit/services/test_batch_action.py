"""Unit tests for Batch Action (P2 — ADR-011 follow-up, Batch Action 分片调度).

Validates execute_batch_action:
  1. Happy path: all items applied → status="applied", per-item versions
  2. Partial success: one item conflicts → status="partial", others applied
  3. fail_fast=True aborts at first failure (committed prefix stays)
  4. Rejected when ActionType.batch_enabled=False
  5. Rejected when item count exceeds BATCH_MAX_ITEMS
  6. Idempotency: derived per-item keys; replayed item → "accepted"
  7. Shard boundary correctness (shards_total + shards_committed)
  8. Validation error per item doesn't abort batch (non-fail_fast)
  9. Shared default_parameters merged (item wins on conflict)
  10. shard_size clamping (None→default, >MAX→MAX, <1→1)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import ConflictError, NotFoundError, ValidationError
from ontology.core.schemas.action import (
    BATCH_DEFAULT_SHARD_SIZE,
    BATCH_MAX_ITEMS,
    BATCH_MAX_SHARD_SIZE,
    ActionExecutionResult,
    ActionTypeParameter,
    BatchActionItem,
    BatchActionRequest,
)
from ontology.core.schemas.ontology import ActionType, DataType, ObjectType
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.action_rule_engine import ActionRuleEngine
from ontology.services.action_service import ActionService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    from contextlib import asynccontextmanager

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
def mock_rule_engine() -> AsyncMock:
    engine = AsyncMock(spec=ActionRuleEngine)
    engine.evaluate = MagicMock(return_value=({}, []))
    engine.evaluate_submission_criteria = MagicMock(return_value=[])
    return engine


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_dataset, mock_rule_engine) -> ActionService:
    return ActionService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        dataset=mock_dataset,
        rule_engine=mock_rule_engine,
    )


def _make_batch_action_type(batch_enabled: bool = True) -> ActionType:
    """ActionType with batch_enabled set (default True for batch tests)."""
    at = ActionType(
        id="at-batch",
        ontology_id="onto1",
        api_name="bulkUpdateStatus",
        display_name="Bulk Update Status",
        description="Bulk update object status",
        affected_object_type_id="ot1",
        parameters={
            "parameters": [
                ActionTypeParameter(
                    api_name="status", display_name="Status", data_type=DataType.STRING
                ).model_dump(),
            ],
            "rules": [],
            "effects": [],
        },
        rules={"rules": []},
        submission_criteria={},
        status="ACTIVE",
        batch_enabled=batch_enabled,
        created_at=MagicMock(),
        updated_at=MagicMock(),
    )
    return at


def _make_managed_object_type() -> ObjectType:
    return ObjectType(
        id="ot1",
        ontology_id="onto1",
        api_name="ticket",
        display_name="Ticket",
        primary_key="id",
        title_property="name",
        storage_type="MANAGED",
        created_at=MagicMock(),
        updated_at=MagicMock(),
    )


def _stub_execute(
    service: ActionService,
    outcomes: dict[str, ActionExecutionResult | Exception],
) -> AsyncMock:
    """Stub ActionService.execute_action to return per-rid outcomes.

    ``outcomes`` maps rid → either an ActionExecutionResult (success)
    or an Exception instance (raised). Lets each test declaratively script
    per-item behavior without wiring the full execute_action mock graph.
    """
    async def _fake_execute(*, object_type_api_name, action_api_name, request, ontology_api_name, context):
        oid = request.parameters.get("rid")
        outcome = outcomes.get(oid)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is not None:
            return outcome
        # Default: a generic applied result.
        return ActionExecutionResult(
            status="applied",
            action_id=f"act-{oid}",
            affected_objects={oid: 2},
        )

    service.execute_action = AsyncMock(side_effect=_fake_execute)  # type: ignore[method-assign]
    return service.execute_action


class TestBatchActionRequestValidation:
    """Pre-execution rejection paths (status='rejected')."""

    @pytest.mark.asyncio
    async def test_rejected_when_batch_disabled(self, service, mock_metadata):
        """ActionType.batch_enabled=False → rejected, no items executed."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type(batch_enabled=False)
        req = BatchActionRequest(items=[BatchActionItem(rid="o1", parameters={"status": "x"})])

        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "rejected"
        assert result.applied == 0
        assert "batch_enabled" in (result.first_error or "")
        # No writes happened — the batch was rejected before any item ran.
        mock_metadata.upsert_object_state.assert_not_called()
        mock_metadata.commit_transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_when_too_many_items(self, service, mock_metadata):
        """Item count > BATCH_MAX_ITEMS → rejected before lookup."""
        # Don't even configure get_action_type — the size guard runs first.
        too_many = BATCH_MAX_ITEMS + 1
        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(too_many)]
        )

        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "rejected"
        assert result.total == too_many
        assert "max" in (result.first_error or "")
        mock_metadata.get_action_type.assert_not_called()

    @pytest.mark.asyncio
    async def test_action_type_not_found_raises(self, service, mock_metadata):
        """ActionType lookup failure propagates (NotFoundError, not 'rejected')."""
        mock_metadata.get_action_type.side_effect = NotFoundError("ActionType", "missing")
        req = BatchActionRequest(items=[BatchActionItem(rid="o1")])

        with pytest.raises(NotFoundError):
            await service.execute_batch_action("ticket", "missing", req, "hr")


class TestBatchActionHappyPath:
    """All items applied → status='applied'."""

    @pytest.mark.asyncio
    async def test_all_applied(self, service, mock_metadata, mock_catalog):
        """3 items all applied → status='applied', applied=3, failed=0."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(service, {})  # default applied for all

        req = BatchActionRequest(
            items=[
                BatchActionItem(rid=f"o{i}", parameters={"status": "closed"})
                for i in range(3)
            ],
            default_parameters={"status": "open"},
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "applied"
        assert result.total == 3
        assert result.applied == 3
        assert result.failed == 0
        assert result.first_error is None
        # Per-item results: all applied with new_version + action_id.
        assert all(r.status == "applied" for r in result.item_results)
        assert all(r.action_id is not None for r in result.item_results)
        assert all(r.new_version == 2 for r in result.item_results)

    @pytest.mark.asyncio
    async def test_default_parameters_merged_item_wins(self, service, mock_metadata):
        """Item parameters override shared default_parameters."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(service, {})

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1", parameters={"status": "custom"})],
            default_parameters={"status": "default", "extra": "keep"},
        )
        await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        # The merged params passed to execute_action: item.status wins, but
        # default's `extra` survives.
        _, kwargs = spy.call_args
        assert kwargs["request"].parameters["status"] == "custom"
        assert kwargs["request"].parameters["extra"] == "keep"
        # rid is injected from the item.
        assert kwargs["request"].parameters["rid"] == "o1"

    @pytest.mark.asyncio
    async def test_rid_injected_into_params(self, service, mock_metadata):
        """Each item's rid reaches execute_action as parameters['rid']."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(service, {})

        req = BatchActionRequest(
            items=[BatchActionItem(rid="obj-xyz", parameters={"status": "x"})]
        )
        await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        _, kwargs = spy.call_args
        assert kwargs["request"].parameters["rid"] == "obj-xyz"

    @pytest.mark.asyncio
    async def test_expected_version_propagated(self, service, mock_metadata):
        """Item.expected_version reaches the merged parameters."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(service, {})

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1", parameters={"status": "x"}, expected_version=7)]
        )
        await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        _, kwargs = spy.call_args
        assert kwargs["request"].parameters["expected_version"] == 7


class TestBatchActionPartialSuccess:
    """Some items fail → status='partial', failures isolated per item."""

    @pytest.mark.asyncio
    async def test_one_conflict_others_applied(self, service, mock_metadata):
        """Middle item OCC-conflicts → status='partial', 2 applied / 1 conflict."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(
            service,
            {
                "o1": ActionExecutionResult(
                    status="applied", action_id="a1", affected_objects={"o1": 2}
                ),
                "o2": ConflictError("OCC conflict", code="OCC_CONFLICT"),
                "o3": ActionExecutionResult(
                    status="applied", action_id="a3", affected_objects={"o3": 2}
                ),
            },
        )

        req = BatchActionRequest(
            items=[
                BatchActionItem(rid="o1"),
                BatchActionItem(rid="o2"),
                BatchActionItem(rid="o3"),
            ]
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "partial"
        assert result.applied == 2
        assert result.failed == 1
        assert result.first_error is not None
        assert "o2" in result.first_error
        # The conflict item's result records the conflict.
        conflict_item = next(r for r in result.item_results if r.rid == "o2")
        assert conflict_item.status == "conflict"

    @pytest.mark.asyncio
    async def test_validation_error_per_item(self, service, mock_metadata):
        """A validation error on one item doesn't abort the batch."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(
            service,
            {
                "o1": ValidationError("bad status", code="VALIDATION_FAILED"),
                "o2": ActionExecutionResult(
                    status="applied", action_id="a2", affected_objects={"o2": 2}
                ),
            },
        )

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1"), BatchActionItem(rid="o2")]
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "partial"
        assert result.applied == 1
        assert result.failed == 1
        bad = next(r for r in result.item_results if r.rid == "o1")
        assert bad.status == "validation_failed"

    @pytest.mark.asyncio
    async def test_all_fail_status_failed(self, service, mock_metadata):
        """Every item fails → status='failed', applied=0."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(
            service,
            {"o1": ConflictError("c"), "o2": ConflictError("c")},
        )

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1"), BatchActionItem(rid="o2")]
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "failed"
        assert result.applied == 0
        assert result.failed == 2

    @pytest.mark.asyncio
    async def test_generic_exception_isolated(self, service, mock_metadata):
        """An unexpected exception is caught + recorded as 'error', batch continues."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(
            service,
            {
                "o1": RuntimeError("kaboom"),
                "o2": ActionExecutionResult(
                    status="applied", action_id="a2", affected_objects={"o2": 2}
                ),
            },
        )

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1"), BatchActionItem(rid="o2")]
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "partial"
        err_item = next(r for r in result.item_results if r.rid == "o1")
        assert err_item.status == "error"
        assert "kaboom" in (err_item.error or "")


class TestBatchActionFailFast:
    """fail_fast=True aborts at first failure."""

    @pytest.mark.asyncio
    async def test_fail_fast_aborts_at_first_failure(self, service, mock_metadata):
        """fail_fast: o1 fails → o2 (later) is NOT attempted."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(
            service,
            {
                "o1": ConflictError("c"),
                "o2": ActionExecutionResult(
                    status="applied", action_id="a2", affected_objects={"o2": 2}
                ),
            },
        )

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1"), BatchActionItem(rid="o2")],
            fail_fast=True,
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        # o1 failed, o2 never attempted.
        assert result.status == "failed"
        assert result.applied == 0
        # execute_action called exactly once (for o1), never for o2.
        assert spy.await_count == 1
        # o2 still has its placeholder 'not run' status.
        o2_result = next(r for r in result.item_results if r.rid == "o2")
        assert o2_result.status == "error"

    @pytest.mark.asyncio
    async def test_fail_fast_committed_prefix_stays(self, service, mock_metadata):
        """fail_fast: o1 applied, o2 fails → o3 not run; o1's commit is durable."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(
            service,
            {
                "o1": ActionExecutionResult(
                    status="applied", action_id="a1", affected_objects={"o1": 2}
                ),
                "o2": ValidationError("bad"),
                "o3": ActionExecutionResult(
                    status="applied", action_id="a3", affected_objects={"o3": 2}
                ),
            },
        )

        req = BatchActionRequest(
            items=[
                BatchActionItem(rid="o1"),
                BatchActionItem(rid="o2"),
                BatchActionItem(rid="o3"),
            ],
            fail_fast=True,
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.status == "partial"
        assert result.applied == 1  # o1 committed before the abort
        assert spy.await_count == 2  # o1 + o2, not o3


class TestBatchActionSharding:
    """Shard boundary correctness + shard_size clamping."""

    @pytest.mark.asyncio
    async def test_shard_count_default_size(self, service, mock_metadata):
        """5 items, default shard_size → 1 shard (5 ≤ BATCH_DEFAULT_SHARD_SIZE)."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(service, {})

        n = 5
        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(n)]
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.shards_total == 1
        assert result.shards_committed == 1
        assert result.applied == n

    @pytest.mark.asyncio
    async def test_shard_count_custom_size(self, service, mock_metadata):
        """7 items, shard_size=3 → 3 shards (3+3+1)."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(service, {})

        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(7)],
            shard_size=3,
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.shards_total == 3
        assert result.shards_committed == 3
        assert result.applied == 7

    @pytest.mark.asyncio
    async def test_shard_size_clamped_to_max(self, service, mock_metadata):
        """shard_size > BATCH_MAX_SHARD_SIZE is clamped down."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(service, {})

        n = 3
        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(n)],
            shard_size=BATCH_MAX_SHARD_SIZE + 500,
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        # Clamped to BATCH_MAX_SHARD_SIZE, so 3 items fit in 1 shard.
        assert result.shards_total == 1
        assert result.applied == n

    @pytest.mark.asyncio
    async def test_shard_size_zero_clamped_to_one(self, service, mock_metadata):
        """shard_size=0 is clamped to 1 → each item its own shard."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(service, {})

        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(3)],
            shard_size=0,
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.shards_total == 3
        assert result.shards_committed == 3

    @pytest.mark.asyncio
    async def test_shard_committed_count_on_fail_fast_abort(self, service, mock_metadata):
        """fail_fast abort mid-shard → shards_committed < shards_total."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        # All items conflict so the first item aborts immediately.
        _stub_execute(
            service,
            {f"o{i}": ConflictError("c") for i in range(4)},
        )

        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(4)],
            shard_size=2,
            fail_fast=True,
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        assert result.shards_total == 2  # 4 items / shard_size 2
        # Aborted on the very first item of the first shard → 0 shards ran
        # to completion. shards_committed reflects the last fully-iterated
        # shard index +1; since shard 0 aborted, shards_committed stays 0.
        assert result.shards_committed == 0


class TestBatchActionIdempotency:
    """Per-item idempotency keys derived from the batch key."""

    @pytest.mark.asyncio
    async def test_derived_idempotency_keys_unique_per_item(self, service, mock_metadata):
        """Without explicit per-item keys, each gets batch_key#index."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(service, {})

        batch_key = "batch-abc"
        req = BatchActionRequest(
            items=[BatchActionItem(rid=f"o{i}") for i in range(3)],
            idempotency_key=batch_key,
        )
        await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        keys = [c.kwargs["request"].idempotency_key for c in spy.call_args_list]
        assert keys == [f"{batch_key}#0", f"{batch_key}#1", f"{batch_key}#2"]

    @pytest.mark.asyncio
    async def test_explicit_item_key_used_over_derived(self, service, mock_metadata):
        """An item with its own idempotency_key is used as-is."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        spy = _stub_execute(service, {})

        req = BatchActionRequest(
            items=[
                BatchActionItem(rid="o1", idempotency_key="custom-key"),
                BatchActionItem(rid="o2"),
            ],
            idempotency_key="batch-key",
        )
        await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        keys = [c.kwargs["request"].idempotency_key for c in spy.call_args_list]
        assert keys[0] == "custom-key"
        assert keys[1] == "batch-key#1"

    @pytest.mark.asyncio
    async def test_accepted_item_counted(self, service, mock_metadata):
        """An item returning 'accepted' (idempotent replay) is counted separately."""
        mock_metadata.get_action_type.return_value = _make_batch_action_type()
        _stub_execute(
            service,
            {
                "o1": ActionExecutionResult(
                    status="accepted", action_id="a1", mutations=[]
                ),
                "o2": ActionExecutionResult(
                    status="applied", action_id="a2", affected_objects={"o2": 2}
                ),
            },
        )

        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1"), BatchActionItem(rid="o2")]
        )
        result = await service.execute_batch_action("ticket", "bulkUpdateStatus", req, "hr")

        # accepted + applied, no failures → 'applied' aggregate.
        assert result.status == "applied"
        assert result.accepted == 1
        assert result.applied == 1
        assert result.failed == 0


class TestBatchActionSchemas:
    """Schema-level validation (no service needed)."""

    def test_batch_request_requires_at_least_one_item(self):
        """min_length=1 on items rejects empty batches."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            BatchActionRequest(items=[])

    def test_batch_item_defaults(self):
        """BatchActionItem has sensible defaults."""
        item = BatchActionItem(rid="o1")
        assert item.parameters == {}
        assert item.idempotency_key is None
        assert item.expected_version == 0

    def test_default_shard_size_constants(self):
        """Constants exported for clamping + tests."""
        assert BATCH_DEFAULT_SHARD_SIZE == 100
        assert BATCH_MAX_SHARD_SIZE == 1000
        assert BATCH_MAX_ITEMS == 10_000
