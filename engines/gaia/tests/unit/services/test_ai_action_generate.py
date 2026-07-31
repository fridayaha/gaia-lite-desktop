"""Tests for LLM-assisted ActionType scaffolding (AI-powered Action creation).

Covers:
  - _sanitize_draft: anti-hallucination (clamp enums, drop rules referencing
    non-existent parameters/object types, reconcile operation_kind)
  - _validate_draft: api_name pattern, simpleeval syntax on rules/criteria/
    conditions, ontology_rules structural consistency
  - _simpleeval_syntax_check: expression parse + eval against sample names
  - stream_action_type_draft: end-to-end loop (LLM mock + sanitize + validate
    + CEGIS repair round)
  - draft_to_create: shape translation to ActionTypeCreate

The LLM call (stream_structured) is mocked — we test the verifier-guided loop
logic, not the model. simpleeval validation uses the real engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ontology.services.ai_action_generate import (
    ActionTypeDraft,
    _DraftEffect,
    _DraftOntologyRule,
    _DraftParameter,
    _DraftSubmissionCriterion,
    _DraftValueSource,
    _load_object_type_info,
    _ObjectTypeInfo,
    _sanitize_draft,
    _simpleeval_syntax_check,
    _validate_draft,
    draft_to_create,
    stream_action_type_draft,
)

# ── Fixtures ────────────────────────────────────────────────────────────


def _ticket_object_type() -> _ObjectTypeInfo:
    """A sample affected ObjectType: Ticket with priority/status/owner."""
    return _ObjectTypeInfo(
        api_name="Ticket",
        display_name="工单",
        primary_key="ticketId",
        title_property="title",
        storage_type="MANAGED",
        properties=[
            {
                "api_name": "ticketId",
                "data_type": "STRING",
                "nullable": False,
                "is_primary_key": True,
                "is_title_property": False,
                "description": "工单唯一标识",
            },
            {
                "api_name": "title",
                "data_type": "STRING",
                "nullable": False,
                "is_primary_key": False,
                "is_title_property": True,
                "description": "工单标题",
            },
            {
                "api_name": "priority",
                "data_type": "STRING",
                "nullable": True,
                "is_primary_key": False,
                "is_title_property": False,
                "description": "优先级 P0/P1/P2",
            },
            {
                "api_name": "status",
                "data_type": "STRING",
                "nullable": True,
                "is_primary_key": False,
                "is_title_property": False,
                "description": "工单状态",
            },
        ],
    )


def _valid_draft() -> ActionTypeDraft:
    """A draft that should pass all validation."""
    return ActionTypeDraft(
        api_name="changePriority",
        display_name="修改优先级",
        description="修改工单优先级，仅当状态为 Open 时允许",
        affected_object_type_api_name="Ticket",
        parameters=[
            _DraftParameter(
                api_name="ticketRef",
                display_name="工单",
                data_type="STRING",
                required=True,
                object_type_ref="Ticket",
            ),
            _DraftParameter(
                api_name="newPriority",
                display_name="新优先级",
                data_type="STRING",
                required=True,
                enum_values=["P0", "P1", "P2"],
            ),
        ],
        rules=[],
        submission_criteria=[
            _DraftSubmissionCriterion(
                expression="newPriority in ['P0', 'P1', 'P2']",
                error_message="优先级必须是 P0/P1/P2",
            ),
        ],
        ontology_rules=[
            _DraftOntologyRule(
                type="ModifyObject",
                target_parameter="ticketRef",
                properties={
                    "priority": _DraftValueSource(source="PARAMETER", value="newPriority"),
                },
            ),
        ],
        effects=[
            _DraftEffect(type="notification", config={}),
        ],
        risk_level="low",
        operation_kind="update",
        batch_enabled=False,
        confidence=0.9,
        pending_confirmations=[],
    )


# ── _simpleeval_syntax_check ────────────────────────────────────────────


class TestSimpleevalSyntaxCheck:
    def test_valid_expression_passes(self):
        ok, msg = _simpleeval_syntax_check("quantity > 0", {"quantity": 1})
        assert ok, f"Expected pass, got: {msg}"
        assert msg == ""

    def test_valid_in_expression_passes(self):
        ok, msg = _simpleeval_syntax_check(
            "newPriority in ['P0', 'P1', 'P2']", {"newPriority": "P0"}
        )
        assert ok, f"Expected pass, got: {msg}"

    def test_valid_compound_expression_passes(self):
        ok, msg = _simpleeval_syntax_check(
            "quantity > 0 and status == 'open'",
            {"quantity": 1, "status": "open"},
        )
        assert ok, f"Expected pass, got: {msg}"

    def test_empty_expression_fails(self):
        ok, msg = _simpleeval_syntax_check("", {})
        assert not ok
        assert "空" in msg

    def test_whitespace_expression_fails(self):
        ok, msg = _simpleeval_syntax_check("   ", {})
        assert not ok

    def test_syntax_error_fails(self):
        ok, msg = _simpleeval_syntax_check("quantity >>>", {"quantity": 1})
        assert not ok
        assert msg  # non-empty error


# ── _sanitize_draft ────────────────────────────────────────────────────


class TestSanitizeDraft:
    def test_clamps_invalid_data_type_to_string(self, _ticket_obj=None):
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            parameters=[_DraftParameter(api_name="p", data_type="BIGDECIMAL")],  # not a valid DataType
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.parameters[0].data_type == "STRING"

    def test_clamps_invalid_risk_level(self):
        draft = ActionTypeDraft(api_name="x", display_name="x", risk_level="critical")
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.risk_level == "low"

    def test_drops_ontology_rule_referencing_nonexistent_parameter(self):
        """Anti-hallucination: rule target_parameter must reference a real param."""
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            parameters=[_DraftParameter(api_name="realParam", data_type="STRING")],
            ontology_rules=[
                _DraftOntologyRule(
                    type="ModifyObject",
                    target_parameter="hallucinatedParam",  # does not exist
                    properties={"priority": _DraftValueSource(source="PARAMETER", value="realParam")},
                ),
                _DraftOntologyRule(
                    type="ModifyObject",
                    target_parameter="realParam",
                    properties={"priority": _DraftValueSource(source="PARAMETER", value="realParam")},
                ),
            ],
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        # Only the rule with the real param survives.
        assert len(sanitized.ontology_rules) == 1
        assert sanitized.ontology_rules[0].target_parameter == "realParam"

    def test_drops_createobject_with_unknown_target_object_type(self):
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            ontology_rules=[
                _DraftOntologyRule(
                    type="CreateObject",
                    target_object_type="HallucinatedObject",  # not the affected OT
                    properties={},
                ),
                _DraftOntologyRule(
                    type="CreateObject",
                    target_object_type="Ticket",  # valid (the affected OT)
                    properties={},
                ),
            ],
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert len(sanitized.ontology_rules) == 1
        assert sanitized.ontology_rules[0].target_object_type == "Ticket"

    def test_reconciles_operation_kind_from_rules(self):
        """operation_kind is inferred from the sanitized ontology_rules."""
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            operation_kind="delete",  # wrong, will be reconciled
            parameters=[_DraftParameter(api_name="ref", data_type="STRING", object_type_ref="Ticket")],
            ontology_rules=[
                _DraftOntologyRule(
                    type="ModifyObject",
                    target_parameter="ref",
                    properties={"priority": _DraftValueSource(source="PARAMETER", value="ref")},
                ),
            ],
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.operation_kind == "update"  # inferred from ModifyObject

    def test_mixed_operation_kind_when_multiple_rule_types(self):
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            parameters=[_DraftParameter(api_name="ref", data_type="STRING", object_type_ref="Ticket")],
            ontology_rules=[
                _DraftOntologyRule(
                    type="ModifyObject",
                    target_parameter="ref",
                    properties={"priority": _DraftValueSource(source="PARAMETER", value="ref")},
                ),
                _DraftOntologyRule(
                    type="CreateObject",
                    target_object_type="Ticket",
                    properties={},
                ),
            ],
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.operation_kind == "mixed"

    def test_forces_affected_object_type_to_real_target(self):
        """LLM might echo a wrong affected_object_type; sanitize overrides it."""
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            affected_object_type_api_name="WrongObject",
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.affected_object_type_api_name == "Ticket"

    def test_clamps_invalid_value_source(self):
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            parameters=[_DraftParameter(api_name="ref", data_type="STRING", object_type_ref="Ticket")],
            ontology_rules=[
                _DraftOntologyRule(
                    type="ModifyObject",
                    target_parameter="ref",
                    properties={
                        "priority": _DraftValueSource(source="INVALID_SOURCE", value="ref"),
                    },
                ),
            ],
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.ontology_rules[0].properties["priority"].source == "PARAMETER"

    def test_drops_system_context_with_invalid_value(self):
        draft = ActionTypeDraft(
            api_name="x",
            display_name="x",
            parameters=[_DraftParameter(api_name="ref", data_type="STRING", object_type_ref="Ticket")],
            ontology_rules=[
                _DraftOntologyRule(
                    type="ModifyObject",
                    target_parameter="ref",
                    properties={
                        "owner": _DraftValueSource(source="SYSTEM_CONTEXT", value="BOGUS_VALUE"),
                    },
                ),
            ],
        )
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        # Source kept, but invalid value dropped to None.
        vs = sanitized.ontology_rules[0].properties["owner"]
        assert vs.source == "SYSTEM_CONTEXT"
        assert vs.value is None

    def test_clamps_confidence_to_unit_interval(self):
        draft = ActionTypeDraft(api_name="x", display_name="x", confidence=1.5)
        sanitized = _sanitize_draft(draft, _ticket_object_type())
        assert sanitized.confidence == 1.0

        draft2 = ActionTypeDraft(api_name="x", display_name="x", confidence=-0.3)
        sanitized2 = _sanitize_draft(draft2, _ticket_object_type())
        assert sanitized2.confidence == 0.0


# ── _validate_draft ────────────────────────────────────────────────────


class TestValidateDraft:
    def test_valid_draft_passes(self):
        errors = _validate_draft(_valid_draft(), _ticket_object_type())
        assert errors == [], f"Expected no errors, got: {errors}"

    def test_empty_api_name_fails(self):
        draft = _valid_draft()
        draft.api_name = ""
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("api_name" in e for e in errors)

    def test_invalid_api_name_pattern_fails(self):
        draft = _valid_draft()
        draft.api_name = "ChangePriority"  # uppercase first letter — not camelCase
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("camelCase" in e for e in errors)

    def test_empty_display_name_fails(self):
        draft = _valid_draft()
        draft.display_name = "  "
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("display_name" in e for e in errors)

    def test_duplicate_parameter_api_name_fails(self):
        draft = _valid_draft()
        draft.parameters.append(_DraftParameter(api_name="newPriority", data_type="STRING"))
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("重复" in e for e in errors)

    def test_invalid_simpleeval_in_submission_criteria_fails(self):
        draft = _valid_draft()
        draft.submission_criteria[0].expression = "newPriority >>>"
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("submission_criteria" in e and "无效" in e for e in errors)

    def test_modify_without_target_parameter_fails(self):
        draft = _valid_draft()
        draft.ontology_rules[0].target_parameter = None
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("target_parameter" in e for e in errors)

    def test_modify_touching_primary_key_fails(self):
        draft = _valid_draft()
        draft.ontology_rules[0].properties = {
            "ticketId": _DraftValueSource(source="PARAMETER", value="newPriority"),
        }
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("主键" in e for e in errors)

    def test_createobject_without_target_object_type_fails(self):
        draft = _valid_draft()
        draft.ontology_rules[0] = _DraftOntologyRule(
            type="CreateObject",
            target_object_type=None,
            properties={},
        )
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("target_object_type" in e for e in errors)

    def test_create_link_without_link_type_fails(self):
        draft = _valid_draft()
        draft.ontology_rules[0] = _DraftOntologyRule(
            type="CreateLink",
            source_parameter="ref",
            target_link_parameter="ref",
        )
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("link_type" in e for e in errors)

    def test_invalid_condition_expression_fails(self):
        draft = _valid_draft()
        draft.ontology_rules[0].condition = "$isUrgent >>>"
        errors = _validate_draft(draft, _ticket_object_type())
        assert any("condition" in e and "无效" in e for e in errors)


# ── draft_to_create ────────────────────────────────────────────────────


class TestDraftToCreate:
    def test_translates_valid_draft_to_action_type_create(self):
        draft = _valid_draft()
        create = draft_to_create(draft)
        assert create.api_name == "changePriority"
        assert create.display_name == "修改优先级"
        assert create.affected_object_type_api_name == "Ticket"
        assert len(create.parameters) == 2
        assert create.parameters[0].api_name == "ticketRef"
        assert create.parameters[0].object_type_ref == "Ticket"
        assert len(create.ontology_rules) == 1
        assert create.ontology_rules[0].type == "ModifyObject"
        assert len(create.effects) == 1
        assert create.effects[0].type == "notification"
        assert create.risk_level == "low"
        assert create.operation_kind == "update"


# ── _load_object_type_info (deterministic schema loading) ─────────────


class TestLoadObjectTypeInfo:
    @pytest.mark.asyncio
    async def test_loads_full_schema_from_metadata(self):
        """_load_object_type_info deterministically loads the OT + properties."""
        metadata = AsyncMock()

        ontology_mock = MagicMock()
        ontology_mock.id = "ont-1"
        metadata.get_ontology.return_value = ontology_mock

        obj_type_mock = MagicMock()
        obj_type_mock.api_name = "Ticket"
        obj_type_mock.display_name = "工单"
        obj_type_mock.primary_key = "ticketId"
        obj_type_mock.title_property = "title"
        obj_type_mock.storage_type = "MANAGED"
        metadata.get_object_type_by_api_name.return_value = obj_type_mock

        prop_mocks = []
        for name, dt, pk in [("ticketId", "STRING", True), ("priority", "STRING", False)]:
            p = MagicMock()
            p.api_name = name
            p.data_type = dt
            p.nullable = not pk
            p.is_primary_key = pk
            p.is_title_property = False
            p.description = ""
            prop_mocks.append(p)
        metadata.get_properties.return_value = prop_mocks

        info = await _load_object_type_info(metadata, "default", "Ticket")
        assert info.api_name == "Ticket"
        assert info.primary_key == "ticketId"
        assert info.storage_type == "MANAGED"
        assert len(info.properties) == 2
        assert info.properties[0]["api_name"] == "ticketId"
        assert info.properties[0]["is_primary_key"] is True


# ── stream_action_type_draft (end-to-end with mocked LLM) ──────────────


def _ticket_obj_type() -> _ObjectTypeInfo:
    """Build the _ObjectTypeInfo that _load_object_type_info would return."""
    return _ObjectTypeInfo(
        api_name="Ticket",
        display_name="工单",
        primary_key="ticketId",
        title_property="title",
        storage_type="MANAGED",
        properties=[
            {"api_name": "ticketId", "data_type": "STRING", "nullable": False,
             "is_primary_key": True, "is_title_property": False, "description": "工单唯一标识"},
            {"api_name": "title", "data_type": "STRING", "nullable": False,
             "is_primary_key": False, "is_title_property": True, "description": "工单标题"},
            {"api_name": "priority", "data_type": "STRING", "nullable": True,
             "is_primary_key": False, "is_title_property": False, "description": "优先级 P0/P1/P2"},
            {"api_name": "status", "data_type": "STRING", "nullable": True,
             "is_primary_key": False, "is_title_property": False, "description": "工单状态"},
        ],
    )


class TestStreamActionTypeDraft:
    @pytest.mark.asyncio
    async def test_valid_draft_streamed_first_try(self):
        """LLM produces a valid draft on first attempt — no repair round."""
        valid_partial = _valid_draft()

        async def fake_stream(*args, **kwargs):
            yield valid_partial

        with patch(
            "ontology.services.ai_action_generate.stream_structured",
            new=fake_stream,
        ):
            results = []
            async for partial in stream_action_type_draft(
                obj_type=_ticket_obj_type(),
                natural_language="修改工单优先级",
            ):
                results.append(partial)

        assert len(results) >= 1
        final = results[-1]
        assert final.api_name == "changePriority"
        assert final.affected_object_type_api_name == "Ticket"
        # No validation-error sentinel in pending_confirmations.
        assert not any("校验未通过" in pc for pc in final.pending_confirmations)

    @pytest.mark.asyncio
    async def test_repair_round_after_validation_failure(self):
        """First attempt has an invalid simpleeval expression; second is valid."""
        bad_draft = _valid_draft()
        bad_draft.submission_criteria[0].expression = "newPriority >>>"  # syntax error
        good_draft = _valid_draft()

        call_count = {"n": 0}

        async def fake_stream(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                yield bad_draft
            else:
                yield good_draft

        with patch(
            "ontology.services.ai_action_generate.stream_structured",
            new=fake_stream,
        ):
            results = []
            async for partial in stream_action_type_draft(
                obj_type=_ticket_obj_type(),
                natural_language="修改工单优先级",
            ):
                results.append(partial)

        assert call_count["n"] == 2  # one bad + one good
        final = results[-1]
        assert final.api_name == "changePriority"
        assert not any("校验未通过" in pc for pc in final.pending_confirmations)

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_yields_tagged_draft(self):
        """Every attempt fails validation — final frame is tagged with errors."""
        bad_draft = _valid_draft()
        bad_draft.api_name = "NotCamelCase"  # pattern violation, never fixable

        async def fake_stream(*args, **kwargs):
            yield bad_draft

        with patch(
            "ontology.services.ai_action_generate.stream_structured",
            new=fake_stream,
        ):
            results = []
            async for partial in stream_action_type_draft(
                obj_type=_ticket_obj_type(),
                natural_language="修改工单优先级",
            ):
                results.append(partial)

        # 3 attempts (MAX_RETRIES), each yielding one frame; the last frame
        # is tagged with the validation-failure sentinel.
        assert len(results) >= 1
        final = results[-1]
        assert any("校验未通过" in pc for pc in final.pending_confirmations)

    @pytest.mark.asyncio
    async def test_llm_stream_exception_retries(self):
        """If stream_structured raises, the loop retries (not a hard failure)."""
        good_draft = _valid_draft()
        call_count = {"n": 0}

        async def fake_stream(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("LLM transient error")
            yield good_draft

        with patch(
            "ontology.services.ai_action_generate.stream_structured",
            new=fake_stream,
        ):
            results = []
            async for partial in stream_action_type_draft(
                obj_type=_ticket_obj_type(),
                natural_language="修改工单优先级",
            ):
                results.append(partial)

        assert call_count["n"] == 2  # failed first, succeeded second
        assert len(results) >= 1
        final = results[-1]
        assert final.api_name == "changePriority"
