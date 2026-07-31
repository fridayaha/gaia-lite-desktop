"""Cedar integration layer — schema/entities/partial-evaluation (ADR-017 D1, Phase 3).

This module bridges Gaia's permission model (Principal + RowSecurityPolicy)
and Cedar (cedarpy). It is the ONLY place that talks to cedarpy directly —
AuthorizationService and the SqlGlot injector consume its outputs.

Three responsibilities (design §4, research §3):

1. **Schema generation**: build a Cedar schema from the ObjectType's
   properties + the Principal's attribute keys. This gives Cedar type-safety
   (policy load-time validation catches unknown attributes before deployment).

2. **Entities construction**: build the Cedar entity JSON for the principal
   (known) — the resource is left unknown so partial evaluation produces a
   residual.

3. **Partial evaluation (TPE)**: run ``is_authorized_partial`` with the
   principal known + resource unknown → produce a **residual** describing
   which resource-attribute conditions decide visibility. The residual is
   a structured Cedar AST that the ``ResidualTranslator`` turns into a SQL
   predicate (deterministic mapping table, CLAUDE.md red line 8).

Key cedarpy quirk (verified): entity uids must use the dict form
``{"type": "User", "id": "alice"}``, NOT the string form ``'User::"alice"'``
— the latter triggers a JSON-escaping bug in cedarpy's entity parser.

This module is deliberately framework-agnostic — it doesn't import any
Service or Layer, only cedarpy + the pydantic Principal. It's testable in
isolation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import cedarpy

_log = logging.getLogger(__name__)

# Cedar entity type names (must match the schema).
_PRINCIPAL_TYPE = "User"
_ACTION_VIEW = "Action"
_RESOURCE_TYPE = "Resource"


@dataclass(frozen=True)
class CedarResidual:
    """The result of Cedar partial evaluation (TPE).

    ``decision``: Allow (no residual — principal sees all rows) / Deny
    (no residual — principal sees no rows) / NoDecision (residual present —
    visibility depends on resource attributes described in ``residual_ast``).

    ``residual_ast``: the structured Cedar AST of the residual conditions
    (None when decision is Allow/Deny). The ResidualTranslator converts this
    to a SQL WHERE predicate.

    ``unknown_entities``: entity uids Cedar says it still needs (for future
    entity-slicing optimization; Phase 3 loads resource attributes lazily).
    """

    decision: str  # "Allow" | "Deny" | "NoDecision"
    residual_ast: dict[str, Any] | None
    unknown_entities: list[str]


def build_cedar_schema(
    *,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
    resource_attributes: dict[str, str],
) -> dict[str, Any]:
    """Build a Cedar schema for the principal + resource attribute shape.

    The schema declares the ``User`` entity with the principal's attribute
    keys (as Strings) + a ``Resource`` entity with the resource's attribute
    keys. This lets Cedar validate policy expressions at load time
    (unknown-attribute errors surface before deployment).

    ``resource_attributes`` maps attribute name → Cedar type
    (``"String"`` / ``"Long"`` / ``"Bool"``). Phase 3 infers String for all
    (the common case for region/department filters); richer typing is Phase 6.
    """
    user_attrs = {
        key: {"type": "String", "required": False}
        for key in principal_attributes
    }
    # ``markings`` is always present on the principal (list of String).
    user_attrs["markings"] = {"type": "Set", "element": {"type": "String"}, "required": False}

    resource_attrs = {
        name: {"type": cedar_type, "required": False}
        for name, cedar_type in resource_attributes.items()
    }
    return {
        "": {
            "entityTypes": {
                _PRINCIPAL_TYPE: {"shape": {"type": "Record", "attributes": user_attrs}},
                _RESOURCE_TYPE: {"shape": {"type": "Record", "attributes": resource_attrs}},
            },
            "actions": {
                "view": {
                    "appliesTo": {
                        "principalTypes": [_PRINCIPAL_TYPE],
                        "resourceTypes": [_RESOURCE_TYPE],
                        "context": {},
                    }
                }
            },
        }
    }


def build_principal_entity(
    principal_id: str,
    attributes: dict[str, Any],
    markings: list[str],
    *,
    string_uid: bool = False,
) -> dict[str, Any]:
    """Build the Cedar entity JSON for the principal (the known entity).

    cedarpy has two entity-uid requirements (verified):
      - ``is_authorized`` (full eval): accepts the dict form
        ``{"type": "User", "id": "alice"}``.
      - ``is_authorized_partial``: requires the string form ``'User::"alice"'``
        (the dict form triggers a JSON-escaping bug in the entity parser).

    ``string_uid=True`` selects the string form (for partial eval).
    """
    attrs: dict[str, Any] = {k: str(v) for k, v in attributes.items()}
    attrs["markings"] = list(markings)
    uid = (
        f'{_PRINCIPAL_TYPE}::"{principal_id}"'
        if string_uid
        else {"type": _PRINCIPAL_TYPE, "id": principal_id}
    )
    return {"uid": uid, "attrs": attrs, "parents": []}


def build_action_entity(*, string_uid: bool = False) -> dict[str, Any]:
    uid = (
        f'{_ACTION_VIEW}::"view"'
        if string_uid
        else {"type": _ACTION_VIEW, "id": "view"}
    )
    return {"uid": uid, "attrs": {}, "parents": []}


def evaluate_row_policy_partial(
    *,
    policy_expression: str,
    principal_id: str,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
    resource_attributes: dict[str, str],
) -> CedarResidual:
    """Run Cedar partial evaluation on a RowSecurityPolicy expression.

    The ``policy_expression`` is a Cedar condition (e.g.
    ``principal.attributes.region == resource.region``). We wrap it in a
    ``permit ... when { <expression> }`` policy, run
    ``is_authorized_partial`` with the principal known + resource unknown,
    and return the residual.

    Returns:
      - ``Allow``: the policy is satisfied for ALL resources (principal sees
        every row) — no residual, no SQL predicate needed.
      - ``Deny``: the policy is unsatisfiable (principal sees no rows) —
        AuthorizationService returns forbidden/empty.
      - ``NoDecision``: visibility depends on resource attributes — the
        ``residual_ast`` describes the condition (translated to SQL WHERE).

    .. note::
        cedarpy 4.8.6's ``is_authorized_partial`` does not expose structured
        residuals for cross-entity (``resource.attr``) references in all
        cases. When TPE yields no usable residual, this function falls back
        to ``_parse_expression_to_residual`` — a deterministic application-
        layer parser that handles the common Cedar policy subset
        (``principal.attributes.X == resource.Y``) and produces the same
        residual shape. This is still "application-constructs-predicate,
        engine-executes-filter" (design §4.0), not post-filtering.
    """
    # First try Cedar TPE (the ideal path — type-safe partial evaluation).
    schema = build_cedar_schema(
        principal_attributes=principal_attributes,
        principal_markings=principal_markings,
        resource_attributes=resource_attributes,
    )
    policy_text = (
        f'permit(principal, action == Action::"view", resource) '
        f'when {{ {policy_expression} }};'
    )
    entities = [
        build_principal_entity(principal_id, principal_attributes, principal_markings,
                               string_uid=True),
        build_action_entity(string_uid=True),
    ]
    try:
        result = cedarpy.is_authorized_partial(
            request={
                "principal": f'{_PRINCIPAL_TYPE}::"{principal_id}"',
                "action": f'{_ACTION_VIEW}::"view"',
            },
            policies=cedarpy.PolicySet.from_str(policy_text),
            entities=entities,
            schema=schema,
        )
        decision_str = str(result.decision).split(".")[-1]
        # Check for a usable structured residual.
        residual_ast = None
        nontrivial = getattr(result.diagnostics, "nontrivial_residuals", []) or []
        if nontrivial:
            try:
                residual_ast = _extract_residual_condition(nontrivial[0])
            except Exception:  # noqa: BLE001
                residual_ast = None
        if residual_ast is not None:
            unknown = list(getattr(result.diagnostics, "unknown_entities", []) or [])
            return CedarResidual(decision=decision_str, residual_ast=residual_ast,
                                  unknown_entities=unknown)
        # TPE yielded no structured residual — fall back to app-layer parse.
    except Exception as exc:  # noqa: BLE001 — cedarpy raises various errors
        _log.warning("Cedar partial evaluation failed: %s (policy: %s)", exc, policy_expression)
    # Application-layer fallback: parse the Cedar expression subset directly.
    return _parse_expression_to_residual(
        policy_expression, principal_attributes, principal_markings
    )


def _parse_expression_to_residual(
    expression: str,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
) -> CedarResidual:
    """Application-layer fallback: parse a Cedar policy subset to a residual.

    Handles the common RowSecurityPolicy patterns (design §1.5):
      - ``principal.attributes.X == resource.Y`` → ``Y = '<principal.X value>'``
      - ``"M" in principal.markings`` → no row filter (marking is Layer 5)
      - ``resource.X == "literal"`` → ``X = 'literal'``
      - ``expr && expr`` → AND of the above

    Produces a residual_ast in the same shape as Cedar TPE would, so the
    ``ResidualTranslator`` handles it uniformly. Unrecognized expressions
    fail-closed (Deny — no rows visible).
    """
    import re

    expr = expression.strip()
    # Split top-level && (Phase 3 handles AND; OR is Phase 6).
    # Naive split — doesn't handle nested parens, sufficient for Phase 3 subset.
    conjuncts = re.split(r"\s*&&\s*", expr)
    conditions: list[dict[str, Any]] = []
    for conj in conjuncts:
        cond = _parse_single_condition(conj.strip(), principal_attributes, principal_markings)
        if cond is None:
            # Unparseable condition → fail-closed.
            return CedarResidual(decision="Deny", residual_ast=None, unknown_entities=[])
        if not isinstance(cond, dict):
            # "PASS" (skip) or None (fail-closed, already returned).
            continue
        conditions.append(cond)
    if not conditions:
        # No row-level conditions → all rows visible.
        return CedarResidual(decision="Allow", residual_ast=None, unknown_entities=[])
    # Build a residual AST: combine conditions with && ("and").
    if len(conditions) == 1:
        residual = conditions[0]
    else:
        residual = conditions[0]
        for c in conditions[1:]:
            residual = {"and": {"left": residual, "right": c}}
    return CedarResidual(
        decision="NoDecision",
        residual_ast={"conditions": [{"kind": "when", "body": residual}]},
        unknown_entities=[],
    )


def _parse_single_condition(
    conj: str,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
) -> dict[str, Any] | str | None:
    """Parse a single Cedar comparison to a residual AST node.

    Returns:
      - dict: a residual AST node (e.g. ``{"==": {...}}``)
      - "PASS": the condition doesn't constrain rows (skip)
      - None: unparseable (fail-closed)
    """
    import re

    # principal.attributes.X == resource.Y
    m = re.match(
        r'principal\.attributes\.(\w+)\s*==\s*resource\.(\w+)$', conj
    )
    if m:
        attr_key, col = m.group(1), m.group(2)
        if attr_key not in principal_attributes:
            # Principal lacks the attribute → condition can't be satisfied → Deny.
            return None
        value = str(principal_attributes[attr_key])
        return {
            "==": {
                "left": {".": {"left": {"unknown": "resource"}, "attr": col}},
                "right": {"Value": value},
            }
        }
    # resource.X == "literal" (or 'literal')
    m = re.match(r'resource\.(\w+)\s*==\s*["\']([^"\']+)["\']$', conj)
    if m:
        col, value = m.group(1), m.group(2)
        return {
            "==": {
                "left": {".": {"left": {"unknown": "resource"}, "attr": col}},
                "right": {"Value": value},
            }
        }
    # "M" in principal.markings — marking constraint, handled by Layer 5, not row-level.
    if re.match(r'["\']\w+["\']\s+in\s+principal\.markings$', conj):
        return "PASS"
    # principal.markings contains "M" — same, Layer 5.
    if "principal.markings" in conj:
        return "PASS"
    # principal.attributes.X == "literal" — principal-side, fully evaluated.
    m = re.match(r'principal\.attributes\.(\w+)\s*==\s*["\']([^"\']+)["\']$', conj)
    if m:
        attr_key, value = m.group(1), m.group(2)
        if principal_attributes.get(attr_key) == value:
            return "PASS"  # satisfied → no row constraint
        return None  # not satisfied → Deny
    # Unrecognized pattern → fail-closed.
    return None


def _extract_residual_condition(residual: Any) -> dict[str, Any]:
    """Extract the condition AST from a residual policy object.

    cedarpy returns residuals as objects whose ``to_json()`` (or dict form)
    exposes the policy AST. The exact shape varies by cedarpy version, so we
    try a few access paths defensively.
    """
    if isinstance(residual, dict):
        return dict(residual)
    if hasattr(residual, "to_json"):
        data: dict[str, Any] = json.loads(residual.to_json())
        return data
    if hasattr(residual, "__dict__"):
        d: dict[str, Any] = json.loads(json.dumps(residual.__dict__, default=str))
        return d
    return {"raw": str(residual)}


def evaluate_masking_policy(
    *,
    policy_expression: str,
    principal_id: str,
    principal_attributes: dict[str, Any],
    principal_markings: list[str],
) -> bool:
    """Evaluate a PropertyMaskingPolicy against the principal.

    Returns True if the principal can see the property (condition satisfied),
    False if it should be masked. Unlike row policies, masking is a full
    evaluation (no resource — the condition only references principal attrs
    and markings), so there's no residual.

    Example: ``principal.markings.contains("PII")`` → True if the principal holds PII.
    """
    # For masking, the condition must be true to SEE the column. We model this
    # as a permit policy: permit when { condition } → Allow = visible.
    # No schema — Cedar infers types from entities (avoiding schema-format
    # quirks; masking expressions only reference principal attrs/markings).
    policy_text = (
        f'permit(principal, action == Action::"view", resource) '
        f'when {{ {policy_expression} }};'
    )
    entities = [
        build_principal_entity(principal_id, principal_attributes, principal_markings),
        build_action_entity(),
        # Resource is irrelevant for masking (condition doesn't reference it),
        # but Cedar requires a resource entity in the request. Use a dummy.
        {"uid": {"type": _RESOURCE_TYPE, "id": "dummy"}, "attrs": {}, "parents": []},
    ]
    try:
        result = cedarpy.is_authorized(
            request={
                "principal": {"type": _PRINCIPAL_TYPE, "id": principal_id},
                "action": {"type": _ACTION_VIEW, "id": "view"},
                "resource": {"type": _RESOURCE_TYPE, "id": "dummy"},
            },
            policies=cedarpy.PolicySet.from_str(policy_text),
            entities=entities,
        )
        return result.decision == cedarpy.Decision.Allow
    except Exception as exc:  # noqa: BLE001 — fail-closed (mask on error)
        _log.warning("Cedar masking evaluation failed: %s (policy: %s)", exc, policy_expression)
        return False
