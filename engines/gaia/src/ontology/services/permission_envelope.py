"""PermissionEnvelope — the ship-the-decision response layer (design §8.2).

This is the **single place** where backend permission decisions get attached
to API responses. Resource routes declare which actions apply to their
resource type via :data:`action_registry` (one line, declarative); the
envelope batch-resolves them via
:meth:`AuthorizationService.check_access_batch` and returns
``allowedActions`` + ``disabledReasons`` for the frontend to render
(``PermissionGate`` / ``useAllowedActions``) — without re-deriving any rule.

Why a centralized envelope (not per-route ad-hoc checks)?
    - **No scattering**: every resource route calls the same ``envelope()``
      / ``wrap_list()`` helpers. The action list lives in one registry, not
      duplicated across routes.
    - **No N+1**: list responses resolve all items in a single
      ``check_access_batch`` call (which reuses the per-entry cache).
    - **Frontend simplicity**: the frontend never calls ``/authz/check`` to
      decide what to show — it consumes the shipped decision
      (``allowedActions.includes("EDIT")``). This is the Palantir/Databricks
      UX model and the explicit choice of design §8.2.
    - **Data Gate**: :meth:`PermissionEnvelope.filter_visible` drops
      unauthorized resources from list responses at the backend (不可见即安全) —
      data never leaves the server for principals who can't see it.

The envelope output is a plain ``dict`` (not a pydantic model) so it can wrap
**any** resource schema without coupling. Routes return it directly; FastAPI
serializes the nested pydantic ``data`` field normally.

Usage (detail)::

    @router.get("/datasources/{api_name}")
    async def get_datasource(api_name: str, ...) -> dict:
        ds = await service.get_datasource(api_name)
        return await envelope(authz, principal, "DATASOURCE", api_name, ds)

Usage (list)::

    @router.get("/datasources")
    async def list_datasources(...) -> list[dict]:
        items = [(ds.api_name, ds) for ds in await service.list_datasources()]
        return await PermissionEnvelope.wrap_list(authz, principal, "DATASOURCE", items)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from ontology.core.permission_roles import (
    OP_ACTION_TYPE_EDIT,
    OP_ACTION_TYPE_EXECUTE,
    OP_ACTION_TYPE_VIEW,
    OP_DATASET_DELETE,
    OP_DATASET_EDIT,
    OP_DATASET_VIEW,
    OP_DATASOURCE_DELETE,
    OP_DATASOURCE_EDIT,
    OP_DATASOURCE_VIEW,
    OP_LINK_TYPE_EDIT,
    OP_LINK_TYPE_VIEW,
    OP_OBJECT_TYPE_DELETE,
    OP_OBJECT_TYPE_EDIT,
    OP_OBJECT_TYPE_VIEW,
    OP_OBJECT_VIEW,
    OP_OBJECT_WRITE,
    OP_ONTOLOGY_DELETE,
    OP_ONTOLOGY_EDIT,
    OP_ONTOLOGY_VIEW,
    OP_ROLE_MANAGE,
)

if TYPE_CHECKING:
    from ontology.core.schemas.permission import Principal
    from ontology.services.authorization_service import AuthorizationService

# ── Action registry (declarative, one place) ──────────────────────────


class _ActionRegistry:
    """Maps resource_type → the actions that apply to it.

    Resource routes consult this registry instead of hardcoding action lists
    (which would scatter the policy across the codebase). Adding a new
    resource type or action is a single ``register()`` call here.
    """

    def __init__(self) -> None:
        self._actions: dict[str, list[str]] = {}

    def register(self, resource_type: str, actions: list[str]) -> None:
        """Declare the actions applicable to a resource type.

        Idempotent: re-registering replaces the list (last wins), so tests
        can override without accumulating duplicates.
        """
        self._actions[resource_type] = list(actions)

    def actions_for(self, resource_type: str) -> list[str]:
        """Return the registered actions for a resource type (empty if unknown)."""
        return list(self._actions.get(resource_type, []))


# The singleton registry. Seeded with the built-in resource types below.
action_registry = _ActionRegistry()

# ── Built-in resource type → action mappings (centralized) ────────────
# These mirror the operation constants in permission_roles.py. Keeping them
# here (not in each route) is what prevents scattering.
action_registry.register("ONTOLOGY", [
    OP_ONTOLOGY_VIEW, OP_ONTOLOGY_EDIT, OP_ONTOLOGY_DELETE,
])
action_registry.register("OBJECT_TYPE", [
    OP_OBJECT_TYPE_VIEW, OP_OBJECT_TYPE_EDIT, OP_OBJECT_TYPE_DELETE,
    OP_OBJECT_VIEW, OP_OBJECT_WRITE,
])
action_registry.register("ACTION_TYPE", [
    OP_ACTION_TYPE_VIEW, OP_ACTION_TYPE_EDIT, OP_ACTION_TYPE_EXECUTE,
])
action_registry.register("LINK_TYPE", [
    OP_LINK_TYPE_VIEW, OP_LINK_TYPE_EDIT,
])
action_registry.register("DATASET", [
    OP_DATASET_VIEW, OP_DATASET_EDIT, OP_DATASET_DELETE,
])
action_registry.register("DATASOURCE", [
    OP_DATASOURCE_VIEW, OP_DATASOURCE_EDIT, OP_DATASOURCE_DELETE,
])
action_registry.register("ROLE", [
    OP_ROLE_MANAGE,
])


# ── Envelope output shape ─────────────────────────────────────────────


class _ResourceLike(Protocol):
    """Anything with an identity we can key decisions on."""

    api_name: str


def _envelope_dict(
    data: Any,
    allowed: list[str],
    disabled: dict[str, str],
) -> dict[str, Any]:
    """Build the envelope dict shape consumed by the frontend."""
    return {
        "data": data,
        "allowedActions": allowed,
        "disabledReasons": disabled,
    }


# ── Public API ────────────────────────────────────────────────────────


async def envelope(
    authz: AuthorizationService,
    principal: Principal,
    resource_type: str,
    resource_id: str,
    data: Any,
) -> dict[str, Any]:
    """Wrap a single resource with its permission decisions (detail response).

    Resolves all registered actions for ``resource_type`` in one batch call
    and returns ``{data, allowedActions, disabledReasons}``. The frontend
    reads ``allowedActions`` to render/enable controls and ``disabledReasons``
    for tooltips on disabled controls.
    """
    actions = action_registry.actions_for(resource_type)
    if not actions:
        # Unknown resource type — no actions to decide. Return data bare
        # (still in envelope shape for frontend consistency).
        return _envelope_dict(data, [], {})
    requests = [(resource_type, resource_id, action) for action in actions]
    results = await authz.check_access_batch(principal, requests)
    allowed: list[str] = []
    disabled: dict[str, str] = {}
    for action in actions:
        result = results[(resource_type, resource_id, action)]
        if result.allowed:
            allowed.append(action)
        else:
            disabled[action] = result.reason or "无权限"
    return _envelope_dict(data, allowed, disabled)


class PermissionEnvelope:
    """Batch envelope helpers for list responses (no N+1)."""

    @staticmethod
    async def wrap_list(
        authz: AuthorizationService,
        principal: Principal,
        resource_type: str,
        items: list[tuple[str, Any]],
    ) -> list[dict[str, Any]]:
        """Wrap a list of resources with per-item permission decisions.

        Issues a **single** ``check_access_batch`` call covering all items ×
        all registered actions (no N+1). ``items`` is a list of
        ``(resource_id, data)`` tuples.

        Does NOT filter — every item is returned (with its ``allowedActions``).
        Use :meth:`filter_visible` for Data Gate (不可见即安全) semantics.
        """
        actions = action_registry.actions_for(resource_type)
        if not actions or not items:
            return [_envelope_dict(data, [], {}) for _, data in items]
        # Build the full batch request set: every item × every action.
        batch: list[tuple[str, str, str]] = []
        for resource_id, _ in items:
            for action in actions:
                batch.append((resource_type, resource_id, action))
        results = await authz.check_access_batch(principal, batch)
        envs: list[dict[str, Any]] = []
        for resource_id, data in items:
            allowed: list[str] = []
            disabled: dict[str, str] = {}
            for action in actions:
                result = results[(resource_type, resource_id, action)]
                if result.allowed:
                    allowed.append(action)
                else:
                    disabled[action] = result.reason or "无权限"
            envs.append(_envelope_dict(data, allowed, disabled))
        return envs

    @staticmethod
    async def filter_visible(
        authz: AuthorizationService,
        principal: Principal,
        resource_type: str,
        items: list[tuple[str, Any]],
        view_action: str,
    ) -> list[tuple[str, Any]]:
        """Data Gate — return only items the principal can ``view_action``.

        Implements 不可见即安全: unauthorized resources are dropped at the
        backend (data never leaves the server). Uses a single batch call for
        the ``view_action`` across all items.
        """
        if not items:
            return []
        batch = [(resource_type, resource_id, view_action) for resource_id, _ in items]
        results = await authz.check_access_batch(principal, batch)
        return [
            (resource_id, data)
            for resource_id, data in items
            if results[(resource_type, resource_id, view_action)].allowed
        ]
