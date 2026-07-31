"""LLM-assisted Cedar policy generation (ADR-017 D6, Phase 7).

Turns natural-language access-control requirements (e.g. "sales reps can
only see customers in their own region") into validated Cedar row-security
policy expressions, using a verifier-guided loop:

  1. Build a Cedar schema from the target ObjectType's properties + the
     principal's attribute keys (deterministic injection, NOT LLM inference).
  2. Prompt the LLM with the schema + the NL requirement + few-shot examples.
  3. Validate the generated expression with cedarpy ``validate_policies``
     (syntax + type check against the schema). On failure, feed the error
     back to the LLM for repair (up to ``max_retries`` rounds).
  4. Dry-run preview: run ``is_authorized`` on sample principal/resource
     pairs (Floor: should allow; Ceiling: should deny) so the user can
     confirm semantic correctness before HITL approval.

The LLM NEVER directly executes — its output is always a *draft* that must
pass validation + human review before being saved as a RowSecurityPolicy.
This is the AutoCedar verifier-guided paradigm (arXiv:2607.03656): "a model
can produce a syntactically valid, plausible-looking policy that is
semantically wrong, with no internal signal that anything is off."

See ``docs/engineer/permission-phase2-landing-guide.md`` §二 for the
full design rationale and references.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ontology.services.ai_generate import generate_text

if TYPE_CHECKING:
    from ontology.core.schemas.permission import (
        PolicyGenerationRequest,
        PolicyGenerationResult,
        PolicyPreviewResult,
    )
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)

# Maximum LLM repair rounds (CEGIS-style: validate → feed error → regenerate).
_MAX_RETRIES = 3

# Few-shot examples anchor the LLM on the exact Cedar expression shape Gaia
# expects (a bare ``when`` condition, NOT a full ``permit(...)`` statement —
# the wrapper is added by cedar_engine.evaluate_row_policy_partial).
_SYSTEM_PROMPT = """\
You are a Cedar policy authoring assistant for the Gaia data platform.

Given a natural-language access-control requirement and a Cedar schema
(entity types + attributes), produce a SINGLE Cedar condition expression
that will be used in a `permit(principal, action, resource) when { <EXPR> }`
policy for row-level security.

Rules:
1. Output ONLY the condition expression — no `permit(...)`, no trailing `;`.
2. Reference principal attributes as `principal.attributes["<name>"]`.
3. Reference resource (ObjectType row) attributes as `resource.<name>`.
4. Reference principal markings as `principal.markings` (a Set of String).
5. Use only attribute names that appear in the provided schema.
6. For marking checks, use: `principal.markings.contains("<MARKING>")`
   (NOT `"<MARKING>" in principal.markings` — the `in` operator is for
   entity group membership, not string-set membership).
7. Combine conditions with `&&` (and) / `||` (or) / `!` (not).
8. Keep it minimal — express exactly the requirement, nothing more.

Examples:
  Requirement: "sales reps can only see customers in their own region"
  Expression: principal.attributes["region"] == resource.region

  Requirement: "only users with the PII marking can see rows"
  Expression: principal.markings.contains("PII")

  Requirement: "managers can see all rows, others only their department"
  Expression: principal.attributes["role"] == "manager" || principal.attributes["department"] == resource.department

  Requirement: "users can see rows where status is active and region matches"
  Expression: resource.status == "active" && principal.attributes["region"] == resource.region
"""


class _LLMOutput(BaseModel):
    """Structured LLM output (parsed from the model's text response)."""

    expression: str
    explanation: str = ""
    confidence: float = 0.0


def _build_prompt(
    nl_requirement: str,
    schema: dict[str, Any],
    resource_attributes: dict[str, str],
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
) -> str:
    """Build the user prompt: schema + NL requirement → Cedar expression."""
    # Compact schema summary (only the entity types + attributes the LLM needs).
    entity_types = schema.get("", {}).get("entityTypes", {})
    user_attrs = list(entity_types.get("User", {}).get("shape", {}).get("attributes", {}).keys())
    resource_attrs = list(entity_types.get("ObjectType", {}).get("shape", {}).get("attributes", {}).keys())

    return json.dumps(
        {
            "requirement": nl_requirement,
            "principal_attributes_available": user_attrs,
            "principal_markings_available": principal_markings,
            "resource_attributes_available": resource_attrs,
            "resource_attribute_types": resource_attributes,
            "principal_sample_attributes": principal_attributes,
            "instruction": "Produce the Cedar condition expression. Reply as JSON: "
            '{"expression": "...", "explanation": "...", "confidence": 0.0-1.0}',
        },
        ensure_ascii=False,
        indent=2,
    )


def _parse_llm_output(raw: str) -> _LLMOutput:
    """Parse the LLM's JSON response into a structured output.

    Tolerates trailing/leading whitespace and partial JSON (extracts the
    first {...} block). Raises ValueError on unparseable output.
    """
    text = raw.strip()
    # Strip markdown code fences if present.
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first (```json or ```) and last (```) lines.
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    # Find the first { and last } to extract a JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        # Fallback: treat the whole output as a bare expression.
        return _LLMOutput(expression=text, explanation="raw fallback", confidence=0.3)
    json_str = text[start : end + 1]
    data = json.loads(json_str)
    return _LLMOutput(
        expression=data.get("expression", "").strip(),
        explanation=data.get("explanation", "").strip(),
        confidence=float(data.get("confidence", 0.0)),
    )


def _build_validation_schema(
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
    resource_attributes: dict[str, str],
) -> dict[str, Any]:
    """Build a Cedar schema for cedarpy.validate_policies.

    Unlike ``cedar_engine.build_cedar_schema`` (which flattens principal
    attributes to the top level for ``is_authorized_partial``), this schema
    nests principal attributes under an ``attributes`` Record — matching
    the Cedar policy syntax ``principal.attributes["<name>"]``. Resource
    attributes stay at the top level (``resource.<name>``).

    All attributes are ``required: True`` to avoid Cedar's optional-attribute
    safety guard (which requires ``has`` checks). LLM-generated policies are
    simple equality/membership checks where the attribute is always accessed.
    """
    # Principal: { attributes: { region: String, ... }, markings: Set<String> }
    # principal_attributes may come in as {key: value} (sample attrs like
    # {"region": "east"}) — we only need the KEYS, all typed as String
    # (the common case for region/department/level filters). Richer typing
    # is Phase 6+.
    attr_fields = {
        name: {"type": "String", "required": True}
        for name in principal_attributes
    }
    principal_shape = {
        "type": "Record",
        "attributes": {
            "attributes": {"type": "Record", "attributes": attr_fields, "required": True},
            "markings": {
                "type": "Set",
                "element": {"type": "String"},
                "required": True,
            },
        },
    }
    resource_shape = {
        "type": "Record",
        "attributes": {
            name: {"type": cedar_type, "required": True}
            for name, cedar_type in resource_attributes.items()
        },
    }
    return {
        "": {
            "entityTypes": {
                "User": {"shape": principal_shape},
                "Resource": {"shape": resource_shape},
            },
            "actions": {
                "view": {
                    "appliesTo": {
                        "principalTypes": ["User"],
                        "resourceTypes": ["Resource"],
                        "context": {"type": "Record", "attributes": {}},
                    }
                }
            },
        }
    }


def _validate_expression(
    expression: str,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
    resource_attributes: dict[str, str],
) -> tuple[bool, list[str]]:
    """Validate a Cedar expression against the schema via cedarpy.

    Returns (passed, errors). Wraps the expression in a full permit policy
    (the shape cedar_engine.evaluate_row_policy_partial expects) and runs
    cedarpy.validate_policies for syntax + type checking.

    Builds a validation-specific schema (principal attributes nested under
    ``attributes`` Record) — see ``_build_validation_schema``.
    """
    import cedarpy  # local import — cedarpy is a heavy dep

    schema = _build_validation_schema(
        principal_attributes=principal_attributes,
        principal_markings=principal_markings,
        resource_attributes=resource_attributes,
    )
    policy_text = (
        f'permit(principal, action == Action::"view", resource) '
        f'when {{ {expression} }};'
    )
    try:
        result = cedarpy.validate_policies(
            policies=policy_text,
            schema=schema,
        )
        errors = list(getattr(result, "errors", []) or [])
        passed = bool(getattr(result, "validation_passed", len(errors) == 0))
        return (passed, [str(e) for e in errors])
    except Exception as exc:  # noqa: BLE001 — cedarpy raises various errors
        return (False, [f"validation_exception: {exc}"])


def _dry_run_preview(
    expression: str,
    principal_id: str,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
    resource_attributes: dict[str, str],
    floor_resources: list[dict[str, Any]],
    ceiling_resources: list[dict[str, Any]],
) -> list[PolicyPreviewResult]:
    """Run is_authorized on sample resources to produce floor/ceiling preview.

    Floor resources: the principal SHOULD be allowed (expected="allow").
    Ceiling resources: the principal should NOT be allowed (expected="deny").

    Uses a validation-consistent schema (principal attributes nested under
    ``attributes`` Record) so the entity structure matches the policy syntax
    ``principal.attributes["<name>"]``.
    """
    import cedarpy  # local import

    from ontology.core.schemas.permission import PolicyPreviewResult

    schema = _build_validation_schema(
        principal_attributes=principal_attributes,
        principal_markings=principal_markings,
        resource_attributes=resource_attributes,
    )
    policy_text = (
        f'permit(principal, action == Action::"view", resource) '
        f'when {{ {expression} }};'
    )
    # Principal entity: { attributes: {...}, markings: [...] } (nested shape).
    principal_entity = {
        "uid": {"__entity": {"type": "User", "id": principal_id}},
        "attrs": {
            "attributes": dict(principal_attributes),
            "markings": list(principal_markings),
        },
        "parents": [],
    }
    action_entity = {
        "uid": {"__entity": {"type": "Action", "id": "view"}},
        "attrs": {},
        "parents": [],
    }
    previews: list[PolicyPreviewResult] = []

    for i, sample in enumerate(floor_resources):
        resource_entity = {
            "uid": {"__entity": {"type": "Resource", "id": f"floor_{i}"}},
            "attrs": {k: v for k, v in sample.items() if k in resource_attributes},
            "parents": [],
        }
        try:
            result = cedarpy.is_authorized(
                request={
                    "principal": f'User::"{principal_id}"',
                    "action": 'Action::"view"',
                    "resource": f'Resource::"floor_{i}"',
                    "context": {},
                },
                policies=cedarpy.PolicySet.from_str(policy_text),
                entities=[principal_entity, action_entity, resource_entity],
                schema=schema,
            )
            actual = str(result.decision).split(".")[-1]  # "Allow"/"Deny"/"NoDecision"
        except Exception as exc:  # noqa: BLE001
            actual = f"error: {exc}"
        passed = actual == "Allow"
        previews.append(
            PolicyPreviewResult(
                resource_attributes=sample,
                expected="allow",
                actual=actual,
                passed=passed,
            )
        )

    for i, sample in enumerate(ceiling_resources):
        resource_entity = {
            "uid": {"__entity": {"type": "Resource", "id": f"ceiling_{i}"}},
            "attrs": {k: v for k, v in sample.items() if k in resource_attributes},
            "parents": [],
        }
        try:
            result = cedarpy.is_authorized(
                request={
                    "principal": f'User::"{principal_id}"',
                    "action": 'Action::"view"',
                    "resource": f'Resource::"ceiling_{i}"',
                    "context": {},
                },
                policies=cedarpy.PolicySet.from_str(policy_text),
                entities=[principal_entity, action_entity, resource_entity],
                schema=schema,
            )
            actual = str(result.decision).split(".")[-1]
        except Exception as exc:  # noqa: BLE001
            actual = f"error: {exc}"
        passed = actual == "Deny"
        previews.append(
            PolicyPreviewResult(
                resource_attributes=sample,
                expected="deny",
                actual=actual,
                passed=passed,
            )
        )

    return previews


async def generate_policy(
    request: PolicyGenerationRequest,
    metadata: PostgresMetaStore,
) -> PolicyGenerationResult:
    """Generate a validated Cedar row-security policy from natural language.

    Verifier-guided loop (ADR-017 D6):
      1. Load ObjectType properties → build Cedar schema (deterministic).
      2. LLM generates expression (schema-injected prompt).
      3. cedarpy validate_policies (syntax + type gate).
      4. On failure → feed error to LLM → regenerate (up to _MAX_RETRIES).
      5. Dry-run preview on floor/ceiling samples.
      6. Return draft (NOT saved — caller must HITL-approve + POST to save).

    The result is always returned (even if validation failed) so the caller
    can show the user what went wrong. ``validation_passed=False`` signals
    the draft is unsafe to use.
    """
    from ontology.core.schemas.permission import PolicyGenerationResult

    # 1. Load the ObjectType's properties to build the Cedar schema.
    props = await metadata.get_properties(request.object_type_id)
    resource_attributes: dict[str, str] = {}
    for p in props:
        # Map Gaia data_type → Cedar type. Default String (common case).
        cedar_type = "String"
        dt = str(p.data_type).upper()
        if dt in ("INTEGER", "LONG", "INT", "BIGINT"):
            cedar_type = "Long"
        elif dt in ("BOOLEAN", "BOOL"):
            cedar_type = "Bool"
        resource_attributes[p.api_name] = cedar_type

    schema = _build_validation_schema(
        principal_attributes=request.sample_principal_attributes,
        principal_markings=request.sample_principal_markings,
        resource_attributes=resource_attributes,
    )

    prompt = _build_prompt(
        nl_requirement=request.natural_language,
        schema=schema,
        resource_attributes=resource_attributes,
        principal_attributes=request.sample_principal_attributes,
        principal_markings=request.sample_principal_markings,
    )

    # 2-4. Verifier-guided loop: generate → validate → repair.
    last_errors: list[str] = []
    llm_output: _LLMOutput | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        repair_hint = ""
        if last_errors:
            repair_hint = (
                f"\n\nYour previous expression failed validation:\n"
                f"{'; '.join(last_errors)}\n"
                f"Please fix and regenerate."
            )
        try:
            raw = await generate_text(_SYSTEM_PROMPT, prompt + repair_hint)
            llm_output = _parse_llm_output(raw)
        except Exception as exc:  # noqa: BLE001 — LLM call failed
            _log.warning("LLM policy generation attempt %d failed: %s", attempt, exc)
            last_errors = [f"llm_error: {exc}"]
            continue

        if not llm_output.expression:
            last_errors = ["empty_expression"]
            continue

        passed, errors = _validate_expression(
            llm_output.expression,
            principal_attributes=request.sample_principal_attributes,
            principal_markings=request.sample_principal_markings,
            resource_attributes=resource_attributes,
        )
        if passed:
            last_errors = []
            break
        last_errors = errors
        _log.info("Policy validation attempt %d failed: %s", attempt, errors)

    # 5. Dry-run preview (only if validation passed + samples provided).
    previews: list[PolicyPreviewResult] = []
    validation_passed = not last_errors and llm_output is not None and bool(llm_output.expression)
    if validation_passed and llm_output and (request.floor_resources or request.ceiling_resources):
        try:
            previews = _dry_run_preview(
                expression=llm_output.expression,
                principal_id=request.sample_principal_id,
                principal_attributes=request.sample_principal_attributes,
                principal_markings=request.sample_principal_markings,
                resource_attributes=resource_attributes,
                floor_resources=request.floor_resources,
                ceiling_resources=request.ceiling_resources,
            )
        except Exception as exc:  # noqa: BLE001 — preview is best-effort
            _log.warning("Dry-run preview failed: %s", exc)

    return PolicyGenerationResult(
        expression=llm_output.expression if llm_output else "",
        explanation=llm_output.explanation if llm_output else "",
        confidence=llm_output.confidence if llm_output else 0.0,
        validation_passed=validation_passed,
        validation_errors=last_errors,
        previews=previews,
        schema_used=schema,
    )
