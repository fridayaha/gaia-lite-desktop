"""AuthorizationService — the permission decision point (PDP, ADR-016/017 Phase 1).

Centralizes all permission decisions. Services call ``check_access`` /
``evaluate_query_scope`` / ``check_action_permission`` instead of
implementing ad-hoc checks — this guarantees consistency, cacheability,
and auditability (XACML PDP/PEP separation, design §0.4 / §2.1).

Five-layer check (串行, 任一层拒即终止, design §2.1):
    Layer 1: identity (Principal validity — anonymous denies non-public)
    Layer 2: Organization (subject isolation, MAC — principal.home_org ∈ resource.space.orgs)
    Layer 3: Space (business domain — principal has Space admission role)
    Layer 4: Project RBAC (principal has Owner/Editor/Viewer/Discoverer)
             ↑ option B fallback: definition-class resource project_id NULL → Ontology's Space's default Project
    Layer 5: Marking MAC (合取 AND — resource markings ⊆ principal.markings) [Phase 2]
    Row/Column: Cedar TPE residual → SqlGlot injection [Phase 3]

Phase 1 implements Layers 1-4; Layer 5 and row/column are stubs (allow).
The stubs are clearly marked so Phase 2/3 can fill them without touching
the Layer 1-4 logic.

Cache (cashews, ADR-017 D2): three tiers — principal attributes, resource
attributes, authorization results. Tag-based invalidation on role/grant
changes. High-sensitivity operations (grant/revoke/delete) bypass the
cache (fail-closed, design §2.1).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from cashews import Cache

from ontology.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from ontology.core.permission_roles import OP_PLATFORM_ADMIN
from ontology.core.schemas.permission import (
    AccessResult,
    MigrationImpact,
    Principal,
    QueryScope,
    ResourceOwnership,
)

if TYPE_CHECKING:
    from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

_log = logging.getLogger(__name__)

# Cache TTLs (design §2.1: short TTL + tag invalidation).
_CACHE_TTL_AUTHZ = "5m"      # authorization result
_CACHE_TTL_PRINCIPAL = "10m"  # principal effective roles
_CACHE_TTL_RESOURCE = "30m"   # resource ownership chain


class AuthorizationService:
    """The permission decision point (PDP).

    Dependencies:
      - ``metadata``: PostgresMetaStore — queries the ownership chain and
        role assignments (PIP role, design §0.4).
      - ``cache``: cashews Cache — three-tier permission cache. ``mem://``
        for dev, ``redis://`` for production (URL-driven, ADR-017 D2).
    """

    def __init__(self, metadata: PostgresMetaStore, cache: Cache) -> None:
        self._metadata = metadata
        self._cache = cache

    # ── Core: single-resource access decision ──────────────────────────

    async def check_access_batch(
        self,
        principal: Principal,
        requests: list[tuple[str, str, str]],
    ) -> dict[tuple[str, str, str], AccessResult]:
        """Batch access decision — the engine behind ship-the-decision.

        Resolves N ``(resource_type, resource_id, action)`` tuples in one
        call, reusing the per-entry cache and the five-layer logic. This is
        what ``PermissionEnvelope`` calls to populate ``allowedActions`` on
        list/detail responses without N+1 round-trips (design §8.2
        ship-the-decision).

        Semantics mirror ``check_access`` exactly — same cache keys, same
        five layers, same high-sensitivity bypass. Duplicate requests are
        deduplicated (resolved once). Order of the returned dict is not
        guaranteed; callers key by the request tuple.
        """
        if not requests:
            return {}
        # Dedup while preserving the request set (dict.fromkeys keeps first
        # occurrence order, but callers key by tuple so order doesn't matter).
        unique = list(dict.fromkeys(requests))
        results: dict[tuple[str, str, str], AccessResult] = {}
        for resource_type, resource_id, action in unique:
            results[(resource_type, resource_id, action)] = await self.check_access(
                principal, resource_type, resource_id, action
            )
        return results

    async def check_access(
        self,
        principal: Principal,
        resource_type: str,
        resource_id: str,
        action: str,
    ) -> AccessResult:
        """Five-layer check for a single resource + action.

        Returns ``AccessResult(allowed=True)`` if all layers pass, else
        ``AccessResult(allowed=False, layer=..., reason=...)``. The caller
        decides whether to raise 403 or return empty (不可见即安全).
        """
        # High-sensitivity actions bypass the cache (fail-closed).
        if _is_high_sensitivity(action):
            return await self._do_check(principal, resource_type, resource_id, action)

        cache_key = _authz_cache_key(principal.id, resource_type, resource_id, action)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            return AccessResult.model_validate_json(cached)

        result = await self._do_check(principal, resource_type, resource_id, action)
        # Phase 4: record the decision in the append-only audit log.
        # Audit failure must NOT fail the authorization decision (best-effort).
        await self._audit(principal, resource_type, resource_id, action, result)
        await self._cache.set(
            cache_key,
            result.model_dump_json(),
            expire=_CACHE_TTL_AUTHZ,
            tags=[
                f"authz:user:{principal.id}",
                f"authz:{resource_type}:{resource_id}",
            ],
        )
        return result

    async def _do_check(
        self,
        principal: Principal,
        resource_type: str,
        resource_id: str,
        action: str,
    ) -> AccessResult:
        """The actual five-layer evaluation (no cache)."""
        # Layer 1: identity. Anonymous denies everything except public reads
        # (none exist yet — even view requires a principal). Fail-closed.
        if principal.is_anonymous:
            return AccessResult.deny("IDENTITY", "Anonymous principal — authentication required")

        # PLATFORM_ADMIN wildcard: bypasses Layers 2-4 (but NOT Layer 5
        # Marking — separation of duties, design §0.1 principle 6). The
        # platform admin manages permissions but still respects data
        # classification once Marking lands (Phase 2).
        if "PLATFORM_ADMIN" in principal.roles:
            # Layer 5 (Phase 2) and row-level (Phase 3) would still apply.
            return await self._check_marking_and_row(principal, resource_type, resource_id, action)

        # Resolve the resource's ownership chain (org/space/project).
        # Layer 2-4 need it. Returns None if the resource doesn't exist or
        # has no ownership chain (e.g. a container resource itself).
        chain = await self._metadata.resolve_resource_ownership(resource_type, resource_id)
        if chain is None:
            # No ownership chain → treat as a governance/identity resource
            # (Organization/Space/Project/Role). Only platform admins manage
            # these (already checked above); non-admins deny.
            return AccessResult.deny("PROJECT", f"No ownership chain for {resource_type}:{resource_id}")

        # Layer 2: Organization (subject isolation, MAC).
        # principal.home_organization must be in the resource's Space org whitelist.
        if not await self._check_organization(principal, chain):
            return AccessResult.deny(
                "ORG",
                "Principal's organization not in Space whitelist",
            )

        # Layer 3: Space admission (principal has a Space-level role OR a
        # Project role under this Space → implied admission).
        if not await self._check_space_admission(principal, chain):
            return AccessResult.deny("SPACE", f"No admission role for Space '{chain.space_id}'")

        # Layer 4: Project RBAC (option B fallback).
        if not await self._check_project_rbac(principal, chain, action):
            return AccessResult.deny(
                "PROJECT",
                f"Principal lacks '{action}' permission in Project '{chain.project_id}'",
                missing=[action],
            )

        # Layers 5 + row/column (Phase 2/3 stubs — allow for now).
        return await self._check_marking_and_row(principal, resource_type, resource_id, action)

    # ── Layer 2: Organization ──────────────────────────────────────────

    async def _check_organization(self, principal: Principal, chain: ResourceOwnership) -> bool:
        """Layer 2: principal's home org ∈ resource's Space org whitelist.

        Single-tenant fallback: when the resource's Space has no org
        whitelist (empty), the default org is implied (progressive
        disclosure — single-tenant deployments don't configure whitelists).
        """
        if principal.home_organization is None:
            # No home org set → in single-tenant mode, allow if the Space
            # has no explicit whitelist (the default Space). In multi-tenant
            # mode this should deny, but Phase 1 is single-tenant-focused.
            return len(chain.organization_ids) == 0 or _DEFAULT_ORG_API_NAME_FALLBACK in chain.organization_ids
        return principal.home_organization in chain.organization_ids or len(chain.organization_ids) == 0

    # ── Layer 3: Space admission ───────────────────────────────────────

    async def _check_space_admission(self, principal: Principal, chain: ResourceOwnership) -> bool:
        """Layer 3: principal has any role scoped to this Space or a Project under it.

        A Project role implies Space admission (you can't have a Project
        role without being admitted to the Space). Space-level roles
        (SPACE_OWNER etc.) also count.
        """
        # Effective roles are pre-resolved on the Principal (by PrincipalService
        # in Phase 5; in dev mode from the X-User-Roles header). Phase 1
        # checks role assignments via the metadata layer for precision.
        assignments = await self._metadata.resolve_effective_role_scopes(principal)
        # Any assignment scoped to this Space (scope_type=SPACE, scope_id=chain.space_id)
        # OR any Project under this Space (scope_type=PROJECT, scope_id in chain's project subtree).
        # Phase 1 simplification: check direct Space scope + the resource's Project.
        for scope_type, scope_id in assignments:
            if scope_type == "SPACE" and scope_id == chain.space_id:
                return True
            if scope_type == "PROJECT" and scope_id == chain.project_id:
                return True
        # Fallback: dev-mode roles (X-User-Roles header) with SPACE_* / platform roles.
        if any(r.startswith("SPACE_") or r in ("PLATFORM_ADMIN",) for r in principal.roles):
            return True
        # Dev-mode PROJECT roles (OWNER/EDITOR/VIEWER/DISCOVERER) imply Space
        # admission — you can't have a Project role without being admitted to
        # the Space. This lets dev-mode X-User-Roles testing work without DB
        # group membership.
        if any(r in ("OWNER", "EDITOR", "VIEWER", "DISCOVERER") for r in principal.roles):
            return True
        return False

    # ── Layer 4: Project RBAC (option B fallback) ──────────────────────

    async def _check_project_rbac(
        self, principal: Principal, chain: ResourceOwnership, action: str
    ) -> bool:
        """Layer 4: principal holds a role granting ``action`` in the Project.

        Option B fallback (design §0.5): definition-class resources
        (ObjectType/ActionType/...) have ``project_id`` NULL in Phase 0.
        The fallback resolves to the Ontology's owning Space's default
        Project — ``resolve_resource_ownership`` already did this, so
        ``chain.project_id`` is always populated here.
        """
        # Effective role names for this principal in this Project scope.
        # ``chain.project_id`` is None only when the resource has no owning
        # Project (orphan) — in that case no Project role can apply, deny.
        if chain.project_id is None:
            return False
        role_names = await self._metadata.resolve_effective_roles_for_scope(
            principal, chain.project_id
        )
        # Check if any held role grants the action.
        for role_name in role_names:
            if await self._role_grants_action(role_name, action):
                return True
        # Dev-mode fallback: X-User-Roles header roles (PLATFORM_ADMIN already
        # handled above; SPACE_* roles grant via Layer 3; PROJECT roles here).
        if any(r in ("OWNER", "EDITOR", "VIEWER", "DISCOVERER") for r in principal.roles):
            # Coarse: a dev-mode PROJECT role grants if the action is a read
            # (VIEWER/DISCOVERER) or any (OWNER/EDITOR). Precise per-action
            # checks happen via resolve_effective_roles_for_scope above.
            return _dev_role_grants(principal.roles, action)
        return False

    async def _role_grants_action(self, role_name: str, action: str) -> bool:
        """Does the named role's permission list include ``action``?"""
        perms = await self._metadata.get_role_permissions(role_name)
        if perms is None:
            return False
        if OP_PLATFORM_ADMIN in perms:
            return True
        return action in perms

    # ── Layer 5 + row/column (Phase 2/3 stubs) ────────────────────────

    async def _check_marking_and_row(
        self, principal: Principal, resource_type: str, resource_id: str, action: str
    ) -> AccessResult:
        """Layer 5 (Marking MAC) + row/column (Phase 3 stub).

        合取校验 (design §1.4): collects all markings on the resource and
        checks the principal holds EVERY one (boolean AND, not OR). Even a
        Project Owner missing a marking is denied — Marking is a hard MAC
        gate above RBAC.

        Row/column (Phase 3): Cedar TPE residual → SqlGlot injection. For now
        permissive (the Layer 1-4 RBAC + Layer 5 Marking gates are the active
        security boundary).
        """
        resource_markings = await self._metadata.get_resource_markings(resource_type, resource_id)
        if not resource_markings:
            # No markings on the resource → MAC layer passes (no restriction).
            return AccessResult.allow()
        # Resolve the principal's held markings (via Group grants).
        principal_markings = set(principal.markings)
        if not principal_markings and not principal.is_anonymous:
            # Principal.markings not pre-populated (dev mode) — resolve from DB.
            principal_markings = set(await self._metadata.resolve_principal_markings(principal))
        missing = set(resource_markings) - principal_markings
        if missing:
            return AccessResult.deny(
                "MARKING",
                f"Principal lacks required markings: {sorted(missing)}",
                missing=sorted(missing),
            )
        return AccessResult.allow()

    # ── Query scope (Phase 3 entry point, Phase 1 stub) ────────────────

    async def evaluate_query_scope(
        self, principal: Principal, ontology_api_name: str, object_type_api_name: str
    ) -> QueryScope:
        """Return the visible-object scope for an ObjectType query.

        Runs Layers 1-4 (RBAC) + Layer 5 (Marking) on the ObjectType. If any
        denies, returns ``QueryScope(forbidden=True)`` (caller returns empty,
        不可见即安全). Otherwise evaluates the row-security policy (Cedar TPE
        → residual) + property-masking policies → returns the SQL residual
        predicate + masked property list for the query layer to inject.
        """
        result = await self.check_access(
            principal, "OBJECT_TYPE", object_type_api_name, "object:view"
        )
        if not result.allowed:
            return QueryScope(forbidden=True)
        # Resolve the project scope for the caller (option B fallback).
        chain = await self._metadata.resolve_resource_ownership(
            "OBJECT_TYPE", object_type_api_name
        )
        project_scope = chain.project_id if chain else None

        # Row-level: evaluate the RowSecurityPolicy (Cedar TPE → residual).
        residual_predicate = await self._evaluate_row_policy(
            principal, object_type_api_name
        )
        # If the row policy hard-denies (e.g. principal lacks a required attr),
        # the ObjectType is forbidden (no rows visible).
        if residual_predicate == "__DENY__":
            return QueryScope(forbidden=True, project_scope=project_scope)

        # Column-level: evaluate PropertyMaskingPolicies.
        masked_properties = await self._evaluate_masking_policies(
            principal, object_type_api_name
        )
        return QueryScope(
            forbidden=False,
            residual=residual_predicate,
            masked_properties=masked_properties,
            project_scope=project_scope,
        )

    async def _evaluate_row_policy(
        self, principal: Principal, object_type_api_name: str
    ) -> str | None:
        """Evaluate the ObjectType's row-security policy → SQL predicate.

        Returns:
          - None: no row policy (all rows visible)
          - "__DENY__": policy hard-denies (no rows visible)
          - SQL predicate string: the Cedar TPE residual translated to SQL
        """
        from ontology.services.cedar_engine import evaluate_row_policy_partial
        from ontology.services.sql_injector import translate_residual_to_predicate

        expression = await self._metadata.get_row_security_policy(object_type_api_name)
        if expression is None:
            return None  # no row policy → all rows visible
        residual = evaluate_row_policy_partial(
            policy_expression=expression,
            principal_id=principal.id,
            principal_attributes=principal.attributes,
            principal_markings=principal.markings,
            resource_attributes={},  # Phase 3: schema-typed attrs are future work
        )
        if residual.decision == "Deny":
            return "__DENY__"
        if residual.decision == "Allow":
            return None  # all rows visible
        return translate_residual_to_predicate(residual.residual_ast)

    async def _evaluate_masking_policies(
        self, principal: Principal, object_type_api_name: str
    ) -> list[str]:
        """Return the list of property api_names to mask (return as null).

        Evaluates each PropertyMaskingPolicy against the principal; properties
        whose policy evaluates false are masked.
        """
        from ontology.services.cedar_engine import evaluate_masking_policy

        # get_property_masking_policies returns list[tuple[str, str]] — NOT a dict.
        # The explicit annotation guards against the ``.items()`` regression:
        # mypy --strict flags ``policies.items()`` as an attribute error on a
        # list, but only when the local binding carries the inferred type.
        policies: list[tuple[str, str]] = (
            await self._metadata.get_property_masking_policies(object_type_api_name)
        )
        masked: list[str] = []
        for prop_api_name, expression in policies:
            visible = evaluate_masking_policy(
                policy_expression=expression,
                principal_id=principal.id,
                principal_attributes=principal.attributes,
                principal_markings=principal.markings,
            )
            if not visible:
                masked.append(prop_api_name)
        return masked

    # ── Action permission (ADR-011 contract, Phase 1 internals) ────────

    async def check_action_permission(
        self,
        principal: Principal,
        object_type_api_name: str,
        object_ids: list[str],
        action: str,
    ) -> set[str]:
        """Return the set of object_ids the principal CANNOT act on.

        ADR-011 contract: returns the forbidden set (caller filters them
        out). Phase 1: if the principal can't access the ObjectType at all
        (Layer 1-4 fail), all objects are forbidden. Per-object row-level
        filtering lands in Phase 3 (Cedar TPE).
        """
        result = await self.check_access(
            principal, "OBJECT_TYPE", object_type_api_name, action
        )
        if not result.allowed:
            return set(object_ids)
        # Phase 3: per-object row-level check via Cedar TPE residual.
        return set()

    # ── Cache invalidation ─────────────────────────────────────────────

    async def invalidate_principal(self, principal_id: str) -> None:
        """Invalidate all cached decisions for a principal (role/membership change)."""
        await self._cache.delete_tags(f"authz:user:{principal_id}")

    async def invalidate_resource(self, resource_type: str, resource_id: str) -> None:
        """Invalidate all cached decisions for a resource (permission/marking change)."""
        await self._cache.delete_tags(f"authz:{resource_type}:{resource_id}")

    # ── Option B→A migration (ADR-016 D3, Phase 7) ──

    async def preview_migration_impact(
        self, object_type_id: str, target_project_id: str
    ) -> MigrationImpact:
        """Preview the permission impact of migrating an ObjectType to a new Project.

        Compares role assignments on the current Project vs the target
        Project, returning a per-group diff (gain/lose/unchanged). Lets the
        admin verify no users unexpectedly lose access before committing
        (Palantir: "once migrated, cannot revert to ontology roles").
        """
        from ontology.core.schemas.permission import MigrationImpactEntry

        info = await self._metadata.get_object_type_by_id(object_type_id)
        if info is None:
            raise NotFoundError("ObjectType", object_type_id)
        ot_model, _space_id, current_project_id = info
        current_project_id = current_project_id or ""
        if not current_project_id:
            # Option B fallback: resolve via Ontology → Space → default Project.
            # (After the NOT NULL migration this shouldn't happen, but we handle it.)
            raise ValidationError(
                "ObjectType has no project_id and no fallback could be resolved"
            )
        target_project = await self._metadata.get_project(target_project_id)
        if target_project is None:
            raise NotFoundError("Project", target_project_id)
        current_project = await self._metadata.get_project(current_project_id)
        if current_project is None:
            raise NotFoundError("Project", current_project_id)

        # List role assignments on both Projects (scope_type=PROJECT, scope_id=project_id).
        current_assignments = await self._metadata.list_role_assignments(
            scope_id=current_project_id
        )
        target_assignments = await self._metadata.list_role_assignments(
            scope_id=target_project_id
        )
        # Build {group_id: role_name} maps.
        current_roles: dict[str, str] = {
            a[0].principal_id: a[1] for a in current_assignments
        }
        target_roles: dict[str, str] = {
            a[0].principal_id: a[1] for a in target_assignments
        }

        all_group_ids = set(current_roles) | set(target_roles)
        # Resolve group names.
        group_names: dict[str, str] = {}
        for gid in all_group_ids:
            g = await self._metadata.get_group(gid)
            group_names[gid] = g.name if g else gid

        entries: list[MigrationImpactEntry] = []
        for gid in all_group_ids:
            cur_role = current_roles.get(gid)
            tgt_role = target_roles.get(gid)
            if cur_role and not tgt_role:
                status = "lose"
            elif tgt_role and not cur_role:
                status = "gain"
            elif cur_role == tgt_role:
                status = "unchanged"
            else:
                # Both have roles but different — treat as lose+gain (role changed).
                status = "lose"
            entries.append(
                MigrationImpactEntry(
                    group_id=gid,
                    group_name=group_names.get(gid, gid),
                    current_role=cur_role,
                    target_role=tgt_role,
                    status=status,
                )
            )
        summary = {
            "gain": sum(1 for e in entries if e.status == "gain"),
            "lose": sum(1 for e in entries if e.status == "lose"),
            "unchanged": sum(1 for e in entries if e.status == "unchanged"),
        }
        return MigrationImpact(
            object_type_id=object_type_id,
            object_type_api_name=ot_model.api_name,
            current_project_id=current_project_id,
            current_project_name=current_project.display_name,
            target_project_id=target_project_id,
            target_project_name=target_project.display_name,
            entries=entries,
            summary=summary,
        )

    async def migrate_object_type_to_project(
        self, object_type_id: str, target_project_id: str, principal: Any
    ) -> MigrationImpact:
        """Migrate an ObjectType to a different Project (option B→A, ADR-016 D3).

        Requires OWNER on BOTH the current and target Projects (Palantir:
        the caller must have edit permission on the backing resources of
        both the source and target). Invalidates caches + audits the action.
        """
        impact = await self.preview_migration_impact(object_type_id, target_project_id)
        # Permission check: OWNER on both current and target Projects.
        for pid in (impact.current_project_id, impact.target_project_id):
            gate = await self.check_access(principal, "PROJECT", pid, "project:manage")
            if not gate.allowed:
                raise ForbiddenError(
                    f"Cannot migrate: lacking project:manage on Project {pid}: {gate.reason}"
                )
        await self._metadata.update_object_type_project(object_type_id, target_project_id)
        # Invalidate caches: the ObjectType's ownership changed.
        await self.invalidate_resource("ObjectType", object_type_id)
        await self.invalidate_resource("OBJECT_TYPE", object_type_id)
        # Audit.
        await self._audit(
            principal,
            "ObjectType",
            object_type_id,
            "migrate_to_project",
            AccessResult.allow(),
        )
        return impact

    # ── Audit (Phase 4) ──

    async def _audit(
        self,
        principal: Principal,
        resource_type: str,
        resource_id: str,
        action: str,
        result: AccessResult,
    ) -> None:
        """Record a decision in the append-only audit log (best-effort).

        Audit failure must NOT fail the authorization decision — the log is
        for traceability, not a gate. Errors are logged and swallowed.
        """
        try:
            await self._metadata.append_audit_log(
                principal_id=None if principal.is_anonymous else principal.id,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                result="ALLOW" if result.allowed else "DENY",
                reason=result.reason,
                layer=result.layer,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort audit
            _log.warning("Audit log write failed (non-fatal): %s", exc)

    # ── Check Access (Phase 4, explainability) ──

    async def check_access_explained(
        self,
        principal: Principal,
        resource_type: str,
        resource_id: str,
        action: str,
    ) -> Any:
        """Explainable access check — returns per-layer status + provenance.

        Unlike ``check_access`` (which returns a flat allow/deny), this method
        returns the full ``CheckAccessResult`` with per-layer status (for the
        stepper UI) + missing permissions (for the "request access" CTA) +
        permission provenance (which Group → Role granted access, when ALLOW).
        Does NOT use the cache (always fresh for debugging) and does NOT audit
        (it's a read-only probe, not an access attempt).
        """
        from ontology.core.schemas.permission import CheckAccessResult

        result = await self._do_check(principal, resource_type, resource_id, action)
        # Per-layer status for the stepper: a layer "passed" if the decision
        # didn't stop there. When allowed, all layers up to the decision passed.
        deny_order = ["IDENTITY", "ORG", "SPACE", "PROJECT", "MARKING", "ROW"]
        if result.allowed:
            layers_status = {layer.lower(): True for layer in deny_order}
        elif result.layer in deny_order:
            idx = deny_order.index(result.layer)
            layers_status = {
                layer.lower(): i < idx for i, layer in enumerate(deny_order)
            }
            layers_status[result.layer.lower()] = False
        else:
            layers_status = {layer.lower(): True for layer in deny_order}
        provenance: list[str] = []
        if result.allowed:
            # Resolve which roles granted access (provenance for the UI).
            chain = await self._metadata.resolve_resource_ownership(resource_type, resource_id)
            if chain and chain.project_id:
                role_names = await self._metadata.resolve_effective_roles_for_scope(
                    principal, chain.project_id
                )
                provenance = [f"Group → {r}" for r in role_names]
        return CheckAccessResult(
            principal_id=principal.id,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            decision="ALLOW" if result.allowed else "DENY",
            layer=result.layer,
            reason=result.reason,
            layers=layers_status,
            missing=result.missing,
            provenance=provenance,
        )


# ── Helpers ────────────────────────────────────────────────────────────

_DEFAULT_ORG_API_NAME_FALLBACK = "00000000000000000000000000000001"  # default org id


def _is_high_sensitivity(action: str) -> bool:
    """Actions that bypass the cache (fail-closed, design §2.1).

    Permission grant/revoke, role changes, marking removal, and deletes
    always hit the DB — a stale cache must never allow a revoked permission.
    """
    return action in (
        "role:manage",
        "marking:manage",
        "marking:assign",
        "ontology:delete",
        "object_type:delete",
        "dataset:delete",
        "datasource:delete",
    )


def _authz_cache_key(principal_id: str, resource_type: str, resource_id: str, action: str) -> str:
    return f"authz:result:{principal_id}:{resource_type}:{resource_id}:{action}"


def _dev_role_grants(dev_roles: list[str], action: str) -> bool:
    """Coarse dev-mode (X-User-Roles header) action grant.

    Precise per-action checks use ``resolve_effective_roles_for_scope``;
    this is a fallback for when no DB role assignments exist (pure header
    dev mode). OWNER/EDITOR grant all; VIEWER grants reads; DISCOVERER
    grants name-only views.
    """
    role_set = set(dev_roles)
    if {"OWNER", "EDITOR"} & role_set:
        return True
    if "VIEWER" in role_set and action.endswith((":view", ":read")):
        return True
    if "DISCOVERER" in role_set and action in ("ontology:view", "object_type:view"):
        return True
    return False
