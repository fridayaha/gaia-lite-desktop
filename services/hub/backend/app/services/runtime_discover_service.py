import re
import uuid

from sqlalchemy.orm import Session, joinedload

from app.core.auth_context import AuthContext
from app.core.enums import HubItemStatus, HubItemType, HubItemVersionStatus, RelationScope, RiskLevel
from app.core.event_log import log_event
from app.models.hub_item import HubItem
from app.models.hub_item_relation import HubItemRelation
from app.models.hub_item_version import HubItemVersion
from app.policies.capability_access import AllowAllCapabilityAccessPolicy, CapabilityAccessPolicy
from app.policies.tenant_policy import can_runtime_access_item
from app.schemas.runtime import RuntimeDiscoverFilters
from app.services.exceptions import RequiredDependencyUnavailableError

_RISK_LEVELS = ["low", "medium", "high"]

_WARNING_DETAILS = {
    "optional_unavailable": "optional dependency unavailable",
    "optional_policy_denied": "optional dependency denied by policy",
    "cycle_detected": "cycle detected, expansion stopped",
    "max_depth_reached": "max depth reached, expansion stopped",
    "dependency_skipped": "dependency skipped",
}


class CapabilityNotAvailableError(Exception):
    def __init__(self, item_id: str):
        super().__init__(f"capability not available: {item_id}")


class RuntimeDiscoverService:
    def __init__(
        self,
        db: Session,
        policy: CapabilityAccessPolicy | None = None,
    ):
        self.db = db
        self.policy = policy or AllowAllCapabilityAccessPolicy()

    def _get_discoverable_item_with_version(
        self, item_id: uuid.UUID
    ) -> tuple[HubItem, HubItemVersion] | None:
        item = self.db.get(HubItem, item_id)
        if item is None:
            return None
        if (
            item.status != HubItemStatus.published
            or not item.discoverable
            or item.force_disabled
            or item.risk_level == RiskLevel.blocking
            or item.current_version_id is None
        ):
            return None
        version = self.db.get(HubItemVersion, item.current_version_id)
        if (
            version is None
            or version.status != HubItemVersionStatus.published
            or version.risk_level == RiskLevel.blocking
        ):
            return None
        return item, version

    def _is_discoverable_with_version(
        self,
        item: HubItem,
        version: HubItemVersion | None,
    ) -> bool:
        if (
            item.status != HubItemStatus.published
            or not item.discoverable
            or item.force_disabled
            or item.risk_level == RiskLevel.blocking
            or item.current_version_id is None
        ):
            return False
        if (
            version is None
            or version.status != HubItemVersionStatus.published
            or version.risk_level == RiskLevel.blocking
        ):
            return False
        return True

    def _load_version_for_item(
        self, item: HubItem
    ) -> HubItemVersion | None:
        if item.current_version_id is None:
            return None
        return self.db.get(HubItemVersion, item.current_version_id)

    def _load_runtime_relations(
        self, item_id: uuid.UUID
    ) -> list[HubItemRelation]:
        return (
            self.db.query(HubItemRelation)
            .options(joinedload(HubItemRelation.target_item))
            .filter(HubItemRelation.source_item_id == item_id)
            .filter(HubItemRelation.relation_scope == RelationScope.runtime)
            .all()
        )

    def _check_dependency_available(
        self,
        target_item_id: uuid.UUID,
        context: AuthContext | None,
    ) -> tuple[bool, tuple[HubItem, HubItemVersion] | None, str | None]:
        discovered = self._get_discoverable_item_with_version(target_item_id)
        if discovered is None:
            return False, None, "optional_unavailable"

        item, version = discovered

        if context is not None and not self.policy.can_resolve(
            item, version, context
        ):
            return False, None, "optional_policy_denied"

        return True, discovered, None

    def _build_dependency_node(
        self,
        rel: HubItemRelation,
        target_item: HubItem,
        target_version: HubItemVersion,
        depth: int,
        source_item_id: uuid.UUID,
        warnings: list[str] | None = None,
    ) -> dict:
        return {
            "item": {
                "id": target_item.id,
                "name": target_item.name,
                "type": target_item.type.value,
            },
            "relation_type": rel.relation_type,
            "required": rel.required,
            "depth": depth,
            "source_item_id": source_item_id,
            "available": True,
            "warnings": warnings or [],
        }

    def _build_dependency_warning(
        self,
        warning_type: str,
        detail: str,
        source_item_id: uuid.UUID | None = None,
        target_item_id: uuid.UUID | None = None,
        relation_type: str | None = None,
        required: bool | None = None,
        depth: int | None = None,
    ) -> dict:
        return {
            "source_item_id": source_item_id,
            "target_item_id": target_item_id,
            "relation_type": relation_type,
            "required": required,
            "depth": depth,
            "warning_type": warning_type,
            "detail": detail,
        }

    def _resolve_dependencies_recursive(
        self,
        root_id: uuid.UUID,
        current_id: uuid.UUID,
        context: AuthContext | None,
        max_depth: int,
        current_depth: int,
        visited_path: set[str],
    ) -> tuple[list[dict], list[dict]]:
        deps: list[dict] = []
        warnings: list[dict] = []

        relations = self._load_runtime_relations(current_id)

        for rel in relations:
            target_id = rel.target_item_id

            available, discovered, reason = self._check_dependency_available(
                target_id, context
            )

            if not available:
                if rel.required:
                    raise RequiredDependencyUnavailableError(str(target_id))

                warnings.append(
                    self._build_dependency_warning(
                        warning_type=reason or "dependency_skipped",
                        detail=_WARNING_DETAILS.get(
                            reason or "dependency_skipped",
                            "dependency skipped",
                        ),
                        source_item_id=current_id,
                        target_item_id=target_id,
                        relation_type=rel.relation_type,
                        required=rel.required,
                        depth=current_depth,
                    )
                )
                log_event(
                    "runtime.dependency_warning",
                    item_id=str(target_id),
                    warning_type=reason or "dependency_skipped",
                    warning_count=1,
                )
                continue

            target_item, target_version = discovered

            node_warnings: list[str] = []

            if current_depth >= max_depth:
                remaining = any(
                    not r.required
                    for r in self._load_runtime_relations(target_id)
                )
                if remaining:
                    node_warnings.append("max_depth_reached")
                    warnings.append(
                        self._build_dependency_warning(
                            warning_type="max_depth_reached",
                            detail=_WARNING_DETAILS["max_depth_reached"],
                            source_item_id=target_id,
                            depth=current_depth,
                        )
                    )

            node = self._build_dependency_node(
                rel=rel,
                target_item=target_item,
                target_version=target_version,
                depth=current_depth,
                source_item_id=current_id,
                warnings=node_warnings,
            )
            deps.append(node)

            if current_depth < max_depth:
                target_id_str = str(target_id)
                if target_id_str in visited_path:
                    warnings.append(
                        self._build_dependency_warning(
                            warning_type="cycle_detected",
                            detail=_WARNING_DETAILS["cycle_detected"],
                            source_item_id=current_id,
                            target_item_id=target_id,
                            depth=current_depth,
                        )
                    )
                    continue

                next_visited = visited_path | {target_id_str}
                child_deps, child_warnings = self._resolve_dependencies_recursive(
                    root_id=root_id,
                    current_id=target_id,
                    context=context,
                    max_depth=max_depth,
                    current_depth=current_depth + 1,
                    visited_path=next_visited,
                )
                deps.extend(child_deps)
                warnings.extend(child_warnings)

        return deps, warnings

    def discover(
        self,
        filters: RuntimeDiscoverFilters,
        context: AuthContext | None = None,
    ) -> tuple[list[tuple[HubItem, HubItemVersion]], int]:
        query = (
            self.db.query(HubItem, HubItemVersion)
            .join(
                HubItemVersion,
                HubItem.current_version_id == HubItemVersion.id,
            )
            .filter(HubItem.status == HubItemStatus.published)
            .filter(HubItem.discoverable == True)
            .filter(HubItem.force_disabled == False)
            .filter(HubItem.risk_level != RiskLevel.blocking)
            .filter(HubItemVersion.status == HubItemVersionStatus.published)
            .filter(HubItemVersion.risk_level != RiskLevel.blocking)
        )

        max_idx = _RISK_LEVELS.index(filters.risk_level_max)
        allowed = _RISK_LEVELS[: max_idx + 1]
        query = query.filter(HubItem.risk_level.in_(allowed))
        query = query.filter(HubItemVersion.risk_level.in_(allowed))

        if filters.type:
            query = query.filter(HubItem.type == filters.type)

        if filters.keyword:
            kw = f"%{filters.keyword}%"
            query = query.filter(
                HubItem.name.ilike(kw) | HubItem.description.ilike(kw)
            )

        all_results = query.order_by(HubItem.updated_at.desc()).all()

        if context is not None:
            tenant_filtered = []
            for item, version in all_results:
                if can_runtime_access_item(context, item, action="discover"):
                    tenant_filtered.append((item, version))
        else:
            tenant_filtered = list(all_results)

        if context is not None:
            filtered = []
            for item, version in tenant_filtered:
                if self.policy.can_discover(item, version, context):
                    filtered.append((item, version))
            del tenant_filtered
        else:
            filtered = list(tenant_filtered)
            del tenant_filtered

        total = len(filtered)
        paged = filtered[filters.offset : filters.offset + filters.limit]

        return paged, total

    def resolve(
        self,
        item_id: uuid.UUID,
        context: AuthContext | None = None,
        depth: int = 1,
    ) -> dict:
        if depth < 1 or depth > 3:
            raise ValueError("depth must be between 1 and 3")

        discovered = self._get_discoverable_item_with_version(item_id)
        if discovered is None:
            raise CapabilityNotAvailableError(str(item_id))

        item, version = discovered

        if context is not None and not self.policy.can_resolve(
            item, version, context
        ):
            raise CapabilityNotAvailableError(str(item_id))

        relations = self._load_runtime_relations(item_id)

        resolved_relations = []
        for rel in relations:
            target_item = rel.target_item
            if target_item is None:
                if rel.required:
                    raise RequiredDependencyUnavailableError(
                        str(rel.target_item_id)
                    )
                continue

            target_version = self._load_version_for_item(target_item)
            hard_available = self._is_discoverable_with_version(
                target_item, target_version
            )

            if not hard_available:
                if rel.required:
                    raise RequiredDependencyUnavailableError(
                        str(rel.target_item_id)
                    )
                continue

            if context is not None and not self.policy.can_resolve(
                target_item, target_version, context
            ):
                if rel.required:
                    raise RequiredDependencyUnavailableError(
                        str(rel.target_item_id)
                    )
                continue

            resolved_relations.append(rel)

        dependencies: list[dict] = []
        dep_warnings: list[dict] = []

        if depth >= 1:
            visited_path = {str(item_id)}
            dependencies, dep_warnings = self._resolve_dependencies_recursive(
                root_id=item_id,
                current_id=item_id,
                context=context,
                max_depth=depth,
                current_depth=1,
                visited_path=visited_path,
            )

        return {
            "id": str(item.id),
            "name": item.name,
            "type": item.type.value,
            "description": item.description,
            "version": version.version,
            "status": item.status.value,
            "risk_level": item.risk_level.value,
            "manifest_json": version.manifest_json,
            "config_json": version.config_json,
            "input_schema": version.input_schema,
            "output_schema": version.output_schema,
            "permission_json": version.permission_json,
            "runtime_compatibility": version.runtime_compatibility,
            "relations": resolved_relations,
            "dependencies": dependencies,
            "dependency_warnings": dep_warnings,
        }

    def _sanitize_function_name(self, name: str, item_id: uuid.UUID) -> str:
        result = re.sub(r"[^a-zA-Z0-9_]", "_", name)
        result = re.sub(r"_+", "_", result)
        result = result.strip("_").lower()
        if not result:
            result = f"tool_{item_id.hex[:8]}"
        if result and result[0].isdigit():
            result = f"tool_{result}"
        return result or f"tool_{item_id.hex[:8]}"

    def _normalize_parameters_schema(
        self, input_schema: dict | None
    ) -> dict:
        if input_schema is None:
            raise ValueError("tool contract incomplete: missing input_schema")
        if not isinstance(input_schema, dict):
            raise ValueError("tool contract incomplete: input_schema must be object")
        schema_type = input_schema.get("type")
        has_properties = "properties" in input_schema
        if schema_type and schema_type != "object":
            raise ValueError(
                "tool contract incomplete: input_schema must be object"
            )
        if not schema_type and has_properties:
            input_schema = dict(input_schema, type="object")
        if "required" not in input_schema:
            input_schema = dict(input_schema, required=[])
        return input_schema

    def build_tool_definition(
        self,
        item_id: uuid.UUID,
        context: AuthContext | None = None,
    ) -> dict:
        discovered = self._get_discoverable_item_with_version(item_id)
        if discovered is None:
            raise CapabilityNotAvailableError(str(item_id))

        item, version = discovered

        if item.type != HubItemType.tool:
            raise CapabilityNotAvailableError(str(item_id))

        if context is not None and not self.policy.can_resolve(
            item, version, context
        ):
            raise CapabilityNotAvailableError(str(item_id))

        input_schema = self._normalize_parameters_schema(version.input_schema)
        func_name = self._sanitize_function_name(item.name, item_id)
        description = item.description or item.name

        return {
            "type": "function",
            "function": {
                "name": func_name,
                "description": description,
                "parameters": input_schema,
            },
        }
