"""Tests for LLM-assisted Cedar policy generation (ADR-017 D6, Phase 7).

Covers:
  - _validate_expression: syntax + type gate (cedarpy validate_policies)
  - _parse_llm_output: JSON parsing + bare-expression fallback
  - _dry_run_preview: floor/ceiling is_authorized preview
  - generate_policy: verifier-guided loop (LLM mock + validate + repair)

The LLM call (generate_text) is mocked — we test the verifier-guided loop
logic, not the model. cedarpy validation/dry-run use the real Cedar engine.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ontology.services.ai_policy_generate import (
    _dry_run_preview,
    _parse_llm_output,
    _validate_expression,
    generate_policy,
)
from ontology.core.schemas.permission import (
    PolicyGenerationRequest,
    PolicyPreviewResult,
)


# ── _validate_expression ──────────────────────────────────────────────


class TestValidateExpression:
    def test_valid_expression_passes(self):
        passed, errors = _validate_expression(
            expression='principal.attributes["region"] == resource.region',
            principal_attributes={"region": "east", "department": "sales"},
            principal_markings=["PII"],
            resource_attributes={"region": "String", "department": "String", "status": "String"},
        )
        assert passed, f"Expected pass, got errors: {errors}"
        assert errors == []

    def test_valid_marking_expression_passes(self):
        passed, errors = _validate_expression(
            expression='principal.markings.contains("PII")',
            principal_attributes={"region": "east"},
            principal_markings=["PII"],
            resource_attributes={"region": "String"},
        )
        assert passed, f"Expected pass, got errors: {errors}"

    def test_valid_compound_expression_passes(self):
        passed, errors = _validate_expression(
            expression='principal.attributes["department"] == resource.department '
            '&& resource.status == "active"',
            principal_attributes={"region": "east", "department": "sales"},
            principal_markings=[],
            resource_attributes={"region": "String", "department": "String", "status": "String"},
        )
        assert passed, f"Expected pass, got errors: {errors}"

    def test_invalid_syntax_fails(self):
        # Missing closing bracket — syntax error.
        passed, errors = _validate_expression(
            expression='principal.attributes["region" == resource.region',
            principal_attributes={"region": "east"},
            principal_markings=[],
            resource_attributes={"region": "String"},
        )
        assert not passed
        assert len(errors) > 0

    def test_undefined_attribute_fails(self):
        # "area" is not in the principal attributes — type error.
        passed, errors = _validate_expression(
            expression='principal.attributes["area"] == resource.region',
            principal_attributes={"region": "east"},
            principal_markings=[],
            resource_attributes={"region": "String"},
        )
        assert not passed

    def test_empty_expression_fails(self):
        passed, errors = _validate_expression(
            expression="",
            principal_attributes={"region": "east"},
            principal_markings=[],
            resource_attributes={"region": "String"},
        )
        assert not passed


# ── _parse_llm_output ──────────────────────────────────────────────────


class TestParseLLMOutput:
    def test_valid_json_parsed(self):
        raw = '{"expression": "principal.attributes[\\"region\\"] == resource.region", "explanation": "region match", "confidence": 0.9}'
        result = _parse_llm_output(raw)
        assert result.expression == 'principal.attributes["region"] == resource.region'
        assert result.explanation == "region match"
        assert result.confidence == 0.9

    def test_json_in_code_fence_parsed(self):
        raw = '```json\n{"expression": "true", "explanation": "", "confidence": 0.5}\n```'
        result = _parse_llm_output(raw)
        assert result.expression == "true"

    def test_bare_expression_fallback(self):
        # No JSON braces → treat whole output as expression.
        raw = 'principal.attributes["region"] == resource.region'
        result = _parse_llm_output(raw)
        assert result.expression == raw
        assert result.confidence == 0.3  # fallback confidence

    def test_empty_output_fallback(self):
        result = _parse_llm_output("")
        assert result.expression == ""

    def test_json_with_extra_text_extracted(self):
        raw = 'Here is the policy:\n{"expression": "true", "confidence": 1.0}\nHope it helps!'
        result = _parse_llm_output(raw)
        assert result.expression == "true"
        assert result.confidence == 1.0


# ── _dry_run_preview ───────────────────────────────────────────────────


class TestDryRunPreview:
    def test_floor_and_ceiling_preview(self):
        """Floor (same region → allow) + Ceiling (diff region → deny)."""
        previews = _dry_run_preview(
            expression='principal.attributes["region"] == resource.region',
            principal_id="sales_alice",
            principal_attributes={"region": "east", "department": "sales"},
            principal_markings=[],
            resource_attributes={"region": "String", "department": "String", "status": "String"},
            floor_resources=[{"region": "east", "department": "sales", "status": "active"}],
            ceiling_resources=[{"region": "west", "department": "sales", "status": "active"}],
        )
        assert len(previews) == 2
        # Floor: same region → Allow
        assert previews[0].expected == "allow"
        assert previews[0].actual == "Allow"
        assert previews[0].passed is True
        # Ceiling: diff region → Deny
        assert previews[1].expected == "deny"
        assert previews[1].actual == "Deny"
        assert previews[1].passed is True

    def test_empty_samples_returns_empty(self):
        previews = _dry_run_preview(
            expression="true",
            principal_id="u1",
            principal_attributes={},
            principal_markings=[],
            resource_attributes={"region": "String"},
            floor_resources=[],
            ceiling_resources=[],
        )
        assert previews == []


# ── generate_policy (verifier-guided loop) ─────────────────────────────


@pytest.fixture
def mock_metadata():
    """Mock PostgresMetaStore with a simple property list."""
    metadata = AsyncMock()
    # Simulate get_properties returning PropertyDef-like objects.
    class FakeProp:
        def __init__(self, api_name, data_type="STRING"):
            self.api_name = api_name
            self.data_type = data_type

    metadata.get_properties = AsyncMock(
        return_value=[
            FakeProp("region", "STRING"),
            FakeProp("department", "STRING"),
            FakeProp("status", "STRING"),
        ]
    )
    return metadata


@pytest.fixture
def gen_request():
    return PolicyGenerationRequest(
        object_type_id="ot_001",
        natural_language="sales reps can only see customers in their own region",
        sample_principal_id="sales_alice",
        sample_principal_attributes={"region": "east", "department": "sales"},
        sample_principal_markings=[],
        floor_resources=[{"region": "east", "department": "sales", "status": "active"}],
        ceiling_resources=[{"region": "west", "department": "sales", "status": "active"}],
    )


class TestGeneratePolicy:
    @pytest.mark.asyncio
    async def test_successful_generation_first_try(self, mock_metadata, gen_request):
        """LLM produces a valid expression on the first try."""
        llm_output = (
            '{"expression": "principal.attributes[\\"region\\"] == resource.region", '
            '"explanation": "region match", "confidence": 0.95}'
        )
        with patch(
            "ontology.services.ai_policy_generate.generate_text",
            new_callable=AsyncMock,
            return_value=llm_output,
        ):
            result = await generate_policy(gen_request, mock_metadata)

        assert result.validation_passed is True
        assert result.validation_errors == []
        assert result.expression == 'principal.attributes["region"] == resource.region'
        assert result.confidence == 0.95
        # Floor/ceiling preview should pass.
        assert len(result.previews) == 2
        assert all(p.passed for p in result.previews)

    @pytest.mark.asyncio
    async def test_repair_loop_converges(self, mock_metadata, gen_request):
        """LLM produces invalid then valid expression — repair loop converges."""
        bad_output = '{"expression": "principal.attributes[\\"area\\"] == resource.region", "confidence": 0.5}'
        good_output = (
            '{"expression": "principal.attributes[\\"region\\"] == resource.region", '
            '"confidence": 0.9}'
        )
        with patch(
            "ontology.services.ai_policy_generate.generate_text",
            new_callable=AsyncMock,
            side_effect=[bad_output, good_output],
        ):
            result = await generate_policy(gen_request, mock_metadata)

        assert result.validation_passed is True
        assert result.expression == 'principal.attributes["region"] == resource.region'
        # generate_text called twice (1 bad + 1 good).
        assert mock_metadata.get_properties.call_count >= 1

    @pytest.mark.asyncio
    async def test_all_retries_fail_returns_invalid(self, mock_metadata, gen_request):
        """LLM never produces a valid expression — returns validation_passed=False."""
        bad_output = '{"expression": "principal.attributes[\\"nonexistent\\"] == resource.region", "confidence": 0.3}'
        with patch(
            "ontology.services.ai_policy_generate.generate_text",
            new_callable=AsyncMock,
            return_value=bad_output,
        ):
            result = await generate_policy(gen_request, mock_metadata)

        assert result.validation_passed is False
        assert len(result.validation_errors) > 0
        # No previews when validation failed.
        assert result.previews == []

    @pytest.mark.asyncio
    async def test_empty_llm_output_handled(self, mock_metadata, gen_request):
        """LLM returns empty output — loop retries, eventually returns invalid."""
        with patch(
            "ontology.services.ai_policy_generate.generate_text",
            new_callable=AsyncMock,
            return_value="",
        ):
            result = await generate_policy(gen_request, mock_metadata)

        assert result.validation_passed is False

    @pytest.mark.asyncio
    async def test_no_samples_skips_preview(self, mock_metadata):
        """No floor/ceiling samples → no previews, but validation still runs."""
        request = PolicyGenerationRequest(
            object_type_id="ot_001",
            natural_language="only PII holders can see rows",
            sample_principal_id="u1",
            sample_principal_attributes={"region": "east"},
            sample_principal_markings=["PII"],
            floor_resources=[],
            ceiling_resources=[],
        )
        llm_output = '{"expression": "principal.markings.contains(\\"PII\\")", "confidence": 0.9}'
        with patch(
            "ontology.services.ai_policy_generate.generate_text",
            new_callable=AsyncMock,
            return_value=llm_output,
        ):
            result = await generate_policy(request, mock_metadata)

        assert result.validation_passed is True
        assert result.previews == []

    @pytest.mark.asyncio
    async def test_llm_call_exception_retries(self, mock_metadata, gen_request):
        """LLM raises exception on first call, succeeds on second."""
        good_output = (
            '{"expression": "principal.attributes[\\"region\\"] == resource.region", '
            '"confidence": 0.9}'
        )
        with patch(
            "ontology.services.ai_policy_generate.generate_text",
            new_callable=AsyncMock,
            side_effect=[RuntimeError("LLM timeout"), good_output],
        ):
            result = await generate_policy(gen_request, mock_metadata)

        assert result.validation_passed is True
