"""Action three-layer authorization (P1, ADR-011; ADR-016 internals).

Mirrors Palantir Foundry's three permission layers:
    1. Action execution permission — can the caller invoke this ActionType at all?
    2. Object row-level write permission — can the caller modify these specific objects?
    3. Parameter-level permission — are sensitive parameters hidden from this caller?

Permission decisions are unified through the AuthorizationService (PDP,
five-layer check, fail-closed). The legacy fail-open ``catalog.check_access``
path has been removed — the PDP is the single source of truth.

    - Layer 1: AuthorizationService five-layer check on the ActionType
      (action_type:execute) **stacked with** the ADR-011 declarative
      ``parameters.permissions`` config (role allowlist + dynamic condition).
      Both must pass. The JSON config is an additional restriction, not a
      fallback.
    - Layer 2: ``AuthorizationService.check_action_permission`` (fail-closed,
      returns the forbidden set).
    - Layer 3: unchanged (``sensitive_params`` role whitelist).

The contract (return forbidden set / raise ForbiddenError) is fixed so
ActionService and all 22 tool consumers don't change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ontology.core.exceptions import ForbiddenError
from ontology.core.schemas.action import ActionContext
from ontology.core.schemas.ontology import ActionType
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services._metadata_owner import MetadataOwnerMixin
from ontology.services.action_rule_engine import ActionRuleEngine

if TYPE_CHECKING:
    from ontology.services.authorization_service import AuthorizationService


class ActionAuthorizer(MetadataOwnerMixin):
    """Three-layer Action authorization (P1, ADR-011 + ADR-016).

    Layer 1 (execute) and Layer 2 (row-write) both delegate to the
    AuthorizationService (PDP, five-layer check, fail-closed). The legacy
    fail-open ``catalog.check_access`` path has been removed — the PDP is now
    the single source of truth for permission decisions.

    Layer 1 *additionally* evaluates the ADR-011 declarative ``permissions``
    config (roles/condition/sensitive_params) on the ActionType itself. These
    are stacked on top of the PDP decision (both must pass), not a substitute.
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry | None,
        rule_engine: ActionRuleEngine | None = None,
        *,
        authorization_service: AuthorizationService,
    ) -> None:
        self._metadata = metadata
        self._catalog = catalog
        self._rule_engine = rule_engine or ActionRuleEngine()
        self._authz = authorization_service

    # ── Layer 1: Action execution permission ──────────────────────────

    async def check_execute_permission(
        self,
        action_type: ActionType,
        context: ActionContext,
    ) -> None:
        """Layer 1: can the caller invoke this ActionType?

        Two stacked checks (both must pass):
          1. PDP five-layer check on the ActionType (action_type:execute) —
             fail-closed (anonymous / no-role principals denied).
          2. ADR-011 declarative ``parameters.permissions`` config on the
             ActionType itself (role allowlist + dynamic condition). This is
             an additional restriction layered on top of RBAC, not a substitute.

        Raises ForbiddenError if either check fails.
        """
        # 1. PDP five-layer check (fail-closed).
        result = await self._authz.check_access(
            context.principal,
            "ACTION_TYPE",
            action_type.api_name,
            "action_type:execute",
        )
        if not result.allowed:
            raise ForbiddenError(f"Action '{action_type.api_name}' execution denied: {result.reason}")

        # 2. ADR-011 declarative permissions (additional restriction).
        perms = _extract_permissions(action_type)
        if perms is None:
            return  # no extra restriction

        # Role allowlist
        roles = perms.get("roles") or []
        if roles:
            if not _has_any_role(context.user_roles, roles):
                raise ForbiddenError(f"Action '{action_type.api_name}' requires one of roles: {roles}")

        # Dynamic condition
        condition = perms.get("condition")
        if condition:
            from ontology.core.schemas.action import SubmissionCriterion

            errors = self._rule_engine.evaluate_submission_criteria(
                # Reuse the criterion evaluator: expression must be truthy,
                # otherwise the error_message is surfaced as Forbidden.
                [
                    SubmissionCriterion(
                        expression=condition,
                        error_message=f"Permission condition failed: {condition}",
                    )
                ],
                {},
                context,
            )
            if errors:
                raise ForbiddenError(errors[0])

    # ── Layer 2: Object row-level write permission ────────────────────

    async def check_row_write_permission(
        self,
        object_type_api_name: str,
        object_ids: list[str],
        context: ActionContext,
    ) -> set[str]:
        """Layer 2: which of these objects can the caller NOT write?

        Returns the set of forbidden object_ids (caller filters them out of
        mutations). Delegates to ``AuthorizationService.check_action_permission``
        (fail-closed). The contract (return forbidden set) is fixed so callers
        do not change.
        """
        return await self._authz.check_action_permission(
            context.principal,
            object_type_api_name,
            object_ids,
            "object:write",
        )

    # ── Layer 3: Parameter-level permission ───────────────────────────

    def filter_sensitive_parameters(
        self,
        action_type: ActionType,
        parameters: dict[str, Any],
        context: ActionContext,
    ) -> dict[str, Any]:
        """Layer 3: strip sensitive parameters the caller cannot see.

        Reads ``parameters.permissions.sensitive_params`` (list of api_names).
        Callers without the ``admin`` role get these removed from the payload
        before validation/execution. Admins see everything.
        """
        perms = _extract_permissions(action_type)
        if perms is None:
            return parameters
        sensitive = perms.get("sensitive_params") or []
        if not sensitive:
            return parameters
        if "admin" in context.user_roles:
            return parameters  # admins see all
        return {k: v for k, v in parameters.items() if k not in sensitive}


def _extract_permissions(action_type: ActionType) -> dict[str, Any] | None:
    """Pull the ``permissions`` sub-config from ActionType.parameters.

    Returns None when no permissions are declared (open access).
    """
    params = action_type.parameters or {}
    perms = params.get("permissions")
    if isinstance(perms, dict) and perms:
        return perms
    return None


def _has_any_role(user_roles: list[str], required: list[str]) -> bool:
    """True if the caller holds at least one of the required roles."""
    required_set = {r for r in required if isinstance(r, str)}
    return bool(required_set & set(user_roles))
