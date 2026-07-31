"""Unit tests for action schemas — ActionTypeCreate, ActionExecutionRequest, etc."""

import pytest

from ontology.core.schemas.action import (
    BATCH_DEFAULT_SHARD_SIZE,
    BATCH_MAX_ITEMS,
    BATCH_MAX_SHARD_SIZE,
    ActionContext,
    ActionEffectConfig,
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionPreviewResult,
    ActionRule,
    ActionTypeCreate,
    ActionTypeParameter,
    BatchActionItem,
    BatchActionRequest,
    BatchActionResult,
    BatchItemResult,
    Mutation,
    SubmissionCriterion,
)
from ontology.core.schemas.ontology import DataType


class TestActionTypeParameter:
    def test_create_required_parameter(self):
        """A required parameter with no default."""
        p = ActionTypeParameter(
            api_name="status",
            display_name="Status",
            data_type=DataType.STRING,
            required=True,
        )
        assert p.api_name == "status"
        assert p.required is True
        assert p.default is None

    def test_create_optional_parameter_with_default(self):
        """An optional parameter with a default value."""
        p = ActionTypeParameter(
            api_name="quantity",
            display_name="Quantity",
            data_type=DataType.INTEGER,
            required=False,
            default=1,
        )
        assert p.required is False
        assert p.default == 1

    def test_api_name_must_match_pattern(self):
        """api_name must start with lowercase and contain only alphanumeric/underscore."""
        with pytest.raises(ValueError):
            ActionTypeParameter(
                api_name="InvalidName",  # starts with uppercase
                display_name="Invalid",
                data_type=DataType.STRING,
            )


class TestActionRule:
    def test_create_derivation_rule(self):
        """A derivation rule computes new values."""
        r = ActionRule(
            type="derivation",
            target="total",
            expression="quantity * unit_price",
            description="Compute total cost",
        )
        assert r.type == "derivation"
        assert r.target == "total"

    def test_create_constraint_rule(self):
        """A constraint rule validates combinations."""
        r = ActionRule(
            type="constraint",
            target="quantity",
            expression="quantity > 0",
            description="Quantity must be positive",
        )
        assert r.type == "constraint"


class TestActionEffectConfig:
    def test_webhook_effect(self):
        """Webhook side effect configuration."""
        effect = ActionEffectConfig(
            type="webhook",
            config={"url": "https://example.com/webhook", "headers": {"Authorization": "Bearer token"}},
        )
        assert effect.type == "webhook"
        assert effect.config["url"] == "https://example.com/webhook"

    def test_default_config(self):
        """Effect config defaults to empty dict."""
        effect = ActionEffectConfig(type="webhook")
        assert effect.config == {}


class TestActionTypeCreate:
    def test_minimal_action_type(self):
        """Minimal ActionType with required fields."""
        at = ActionTypeCreate(
            api_name="approveOrder",
            display_name="Approve Order",
            affected_object_type_api_name="Order",
        )
        assert at.api_name == "approveOrder"
        assert at.parameters == []
        assert at.rules == []
        assert at.effects == []

    def test_full_action_type(self):
        """Full ActionType with parameters, rules, and effects."""
        at = ActionTypeCreate(
            api_name="shipOrder",
            display_name="Ship Order",
            description="Ship an order to the customer",
            affected_object_type_api_name="Order",
            parameters=[
                ActionTypeParameter(api_name="orderId", display_name="Order ID", data_type=DataType.STRING),
                ActionTypeParameter(
                    api_name="trackingNumber",
                    display_name="Tracking #",
                    data_type=DataType.STRING,
                    required=False,
                ),
            ],
            rules=[
                ActionRule(
                    type="constraint",
                    target="orderId",
                    expression='orderId != ""',
                    description="Order ID required",
                ),
                ActionRule(type="derivation", target="status", expression='"shipped"'),
            ],
            effects=[
                ActionEffectConfig(type="webhook", config={"url": "https://erp.example.com/shipments"}),
            ],
        )
        assert len(at.parameters) == 2
        assert len(at.rules) == 2
        assert at.rules[0].type == "constraint"
        assert at.rules[1].type == "derivation"
        assert len(at.effects) == 1

    def test_api_name_must_match_pattern(self):
        """ActionType api_name must match naming convention."""
        with pytest.raises(ValueError):
            ActionTypeCreate(
                api_name="Invalid-Name",  # contains hyphen
                display_name="Invalid",
                affected_object_type_api_name="order",
            )


class TestActionTypeParameterP1:
    """P1 extensions: dynamic default source, readonly/hidden, pattern, enum, object_ref."""

    def test_default_source_current_user(self):
        p = ActionTypeParameter(
            api_name="createdBy",
            display_name="Created By",
            data_type=DataType.STRING,
            default_source="current_user",
        )
        assert p.default_source == "current_user"
        assert p.default_source_field is None

    def test_default_source_selected_object_field_requires_field(self):
        p = ActionTypeParameter(
            api_name="owner",
            display_name="Owner",
            data_type=DataType.STRING,
            default_source="selected_object_field",
            default_source_field="owner",
        )
        assert p.default_source == "selected_object_field"
        assert p.default_source_field == "owner"

    def test_readonly_hidden_flags(self):
        p = ActionTypeParameter(
            api_name="status",
            display_name="Status",
            data_type=DataType.STRING,
            readonly=True,
            hidden=False,
        )
        assert p.readonly is True
        assert p.hidden is False

    def test_pattern_and_error_message(self):
        p = ActionTypeParameter(
            api_name="phone",
            display_name="Phone",
            data_type=DataType.STRING,
            pattern=r"^\d{11}$",
            error_message="Phone must be 11 digits",
        )
        assert p.pattern == r"^\d{11}$"
        assert p.error_message == "Phone must be 11 digits"

    def test_enum_values(self):
        p = ActionTypeParameter(
            api_name="priority",
            display_name="Priority",
            data_type=DataType.STRING,
            enum_values=["low", "medium", "high"],
        )
        assert p.enum_values == ["low", "medium", "high"]

    def test_object_ref_single(self):
        p = ActionTypeParameter(
            api_name="customerId",
            display_name="Customer",
            data_type=DataType.STRING,
            object_type_ref="customer",
            is_object_set=False,
        )
        assert p.object_type_ref == "customer"
        assert p.is_object_set is False

    def test_object_ref_set(self):
        p = ActionTypeParameter(
            api_name="selectedOrders",
            display_name="Selected Orders",
            data_type=DataType.STRING,
            object_type_ref="order",
            is_object_set=True,
        )
        assert p.is_object_set is True


class TestSubmissionCriterion:
    def test_create_criterion(self):
        c = SubmissionCriterion(
            expression="quantity > 0 and status == 'open'",
            error_message="Quantity must be positive and order must be open",
        )
        assert c.expression == "quantity > 0 and status == 'open'"
        assert "positive" in c.error_message

    def test_criterion_requires_error_message(self):
        with pytest.raises(ValueError):
            SubmissionCriterion(expression="quantity > 0", error_message="")


class TestActionTypeCreateP1:
    def test_operation_kind_default_mixed(self):
        at = ActionTypeCreate(
            api_name="approve",
            display_name="Approve",
            affected_object_type_api_name="order",
        )
        assert at.operation_kind == "mixed"
        assert at.batch_enabled is False

    def test_operation_kind_create(self):
        at = ActionTypeCreate(
            api_name="createOrder",
            display_name="Create Order",
            affected_object_type_api_name="order",
            operation_kind="create",
        )
        assert at.operation_kind == "create"

    def test_submission_criteria_structured_list(self):
        at = ActionTypeCreate(
            api_name="approve",
            display_name="Approve",
            affected_object_type_api_name="order",
            submission_criteria=[
                SubmissionCriterion(expression="amount > 0", error_message="Amount must be positive"),
            ],
        )
        assert isinstance(at.submission_criteria, list)
        assert len(at.submission_criteria) == 1

    def test_submission_criteria_legacy_dict_backward_compat(self):
        """Bare dict is accepted for backward compatibility."""
        at = ActionTypeCreate(
            api_name="approve",
            display_name="Approve",
            affected_object_type_api_name="order",
            submission_criteria={"amount > 0": "Amount must be positive"},
        )
        # Stored as-is; ActionService normalizes to list.
        assert isinstance(at.submission_criteria, dict)

    def test_batch_enabled_flag(self):
        at = ActionTypeCreate(
            api_name="bulkClose",
            display_name="Bulk Close",
            affected_object_type_api_name="order",
            batch_enabled=True,
        )
        assert at.batch_enabled is True


class TestMutation:
    def test_create_object_mutation(self):
        m = Mutation(type="CREATE_OBJECT", rid="order-1", properties={"status": "open"})
        assert m.type == "CREATE_OBJECT"
        assert m.expected_version == 0
        assert m.properties == {"status": "open"}

    def test_relate_mutation(self):
        m = Mutation(
            type="RELATE",
            rid="order-1",
            link_type_api_name="order_customer",
            target_rid="customer-1",
        )
        assert m.type == "RELATE"
        assert m.link_type_api_name == "order_customer"
        assert m.target_rid == "customer-1"

    def test_clear_links_mutation(self):
        m = Mutation(
            type="CLEAR_LINKS",
            rid="order-1",
            link_type_api_name="order_items",
        )
        assert m.type == "CLEAR_LINKS"
        assert m.target_rids is None  # clear all

    def test_conditional_mutation(self):
        m = Mutation(
            type="UPDATE_OBJECT",
            rid="order-1",
            properties={"status": "shipped"},
            condition="status == 'paid'",
        )
        assert m.condition == "status == 'paid'"


class TestActionExecutionResultP1:
    def test_forbidden_objects_field(self):
        result = ActionExecutionResult(
            status="applied",
            action_id="abc",
            forbidden_objects=["order-5", "order-6"],
        )
        assert result.forbidden_objects == ["order-5", "order-6"]

    def test_forbidden_objects_default_empty(self):
        result = ActionExecutionResult(status="applied", action_id="abc")
        assert result.forbidden_objects == []


class TestActionContext:
    def test_default_context(self):
        ctx = ActionContext()
        assert ctx.current_user == "anonymous"
        assert ctx.workspace_id == ""
        assert ctx.user_roles == []
        assert ctx.selected_object is None

    def test_context_with_user_and_roles(self):
        ctx = ActionContext(current_user="alice", user_roles=["manager", "approver"])
        assert ctx.current_user == "alice"
        assert "manager" in ctx.user_roles


class TestActionPreviewResult:
    def test_preview_result(self):
        r = ActionPreviewResult(
            valid=True,
            mutations=[{"type": "UPDATE_OBJECT", "rid": "order-1"}],
            before_snapshots={"order-1": {"status": "open"}},
        )
        assert r.valid is True
        assert r.before_snapshots["order-1"]["status"] == "open"


class TestActionEffectConfigP1:
    def test_sub_action_effect(self):
        effect = ActionEffectConfig(
            type="sub_action",
            config={"ontology": "default", "object_type": "audit_log", "action": "create_log"},
        )
        assert effect.type == "sub_action"

    def test_kafka_topic_effect(self):
        effect = ActionEffectConfig(
            type="kafka_topic",
            config={"topic": "action_events", "bootstrap_servers": "localhost:9092"},
        )
        assert effect.type == "kafka_topic"


class TestActionExecutionRequest:
    def test_minimal_request(self):
        """Minimal request with just parameters."""
        req = ActionExecutionRequest(parameters={"status": "shipped"})
        assert req.parameters == {"status": "shipped"}
        assert req.idempotency_key is None

    def test_request_with_idempotency_key(self):
        """Request with client-provided idempotency key."""
        req = ActionExecutionRequest(
            parameters={"status": "shipped"},
            idempotency_key="client-key-12345",
        )
        assert req.idempotency_key == "client-key-12345"

    def test_empty_parameters_default(self):
        """Empty parameters default to empty dict."""
        req = ActionExecutionRequest()
        assert req.parameters == {}


class TestActionExecutionResult:
    def test_applied_result(self):
        """Successful action execution."""
        result = ActionExecutionResult(
            status="applied",
            action_id="abc123",
            affected_objects={"order-1": 2, "order-2": 5},
            mutations=[{"type": "UPDATE_OBJECT", "rid": "order-1"}],
        )
        assert result.status == "applied"
        assert len(result.affected_objects) == 2
        assert result.affected_objects["order-1"] == 2

    def test_conflict_result(self):
        """Conflict result with details."""
        result = ActionExecutionResult(
            status="conflict",
            action_id="",
            conflict_details={"rid": "order-1", "expected_version": 3},
        )
        assert result.status == "conflict"
        assert result.conflict_details["expected_version"] == 3

    def test_validation_failed_result(self):
        """Validation failure with errors."""
        result = ActionExecutionResult(
            status="validation_failed",
            action_id="",
            validation_errors=["Missing required parameter: 'status'"],
        )
        assert result.status == "validation_failed"
        assert len(result.validation_errors) == 1

    def test_accepted_result(self):
        """Idempotent replay — request already processed."""
        result = ActionExecutionResult(
            status="accepted",
            action_id="existing-action-123",
        )
        assert result.status == "accepted"


class TestBatchActionSchemas:
    """P2: Batch Action request/result schemas."""

    def test_batch_request_minimal(self):
        """A batch request with one item + defaults."""
        req = BatchActionRequest(
            items=[BatchActionItem(rid="o1", parameters={"status": "x"})],
            default_parameters={"status": "default"},
        )
        assert len(req.items) == 1
        assert req.shard_size is None
        assert req.fail_fast is False

    def test_batch_request_rejects_empty_items(self):
        """min_length=1 on items."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            BatchActionRequest(items=[])

    def test_batch_item_defaults(self):
        """BatchItemItem has sensible defaults."""
        item = BatchActionItem(rid="o1")
        assert item.parameters == {}
        assert item.idempotency_key is None
        assert item.expected_version == 0

    def test_batch_result_applied(self):
        """All-applied aggregate result."""
        r = BatchActionResult(
            status="applied", total=3, applied=3, failed=0,
            shards_total=1, shards_committed=1,
        )
        assert r.status == "applied"
        assert r.accepted == 0
        assert r.item_results == []

    def test_batch_result_partial_with_items(self):
        """Partial result carries per-item detail."""
        r = BatchActionResult(
            status="partial", total=2, applied=1, failed=1,
            item_results=[
                BatchItemResult(rid="o1", status="applied", new_version=4),
                BatchItemResult(rid="o2", status="conflict", error="OCC"),
            ],
            first_error="o2: OCC",
        )
        assert r.status == "partial"
        assert len(r.item_results) == 2
        assert r.item_results[1].status == "conflict"

    def test_batch_result_invalid_status_rejected(self):
        """status is a Literal — bogus value rejected."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            BatchActionResult(status="bogus", total=0, applied=0, failed=0)

    def test_batch_constants_present(self):
        """Clamping constants are exported."""
        assert BATCH_DEFAULT_SHARD_SIZE > 0
        assert BATCH_MAX_SHARD_SIZE > BATCH_DEFAULT_SHARD_SIZE
        assert BATCH_MAX_ITEMS > BATCH_MAX_SHARD_SIZE

    def test_batch_item_result_invalid_status_rejected(self):
        """BatchItemResult.status is a Literal."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            BatchItemResult(rid="o1", status="bogus")
