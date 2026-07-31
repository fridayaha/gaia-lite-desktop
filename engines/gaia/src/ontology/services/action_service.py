"""ActionService — data write operations with full transaction control.

All data mutations are applied to PostgreSQL `object_state` within the same
transaction as the audit log (`execution_log`) and side effect queue (`outbox`).
Iceberg remains the analytical persistence layer, updated asynchronously via
SeaTunnel CDC from the PG WAL — the architecture redline ("Iceberg is the single
write entry point for analytical data") is preserved because CDC is a derived
copy, not a direct write.

Flow:
    1. Idempotency check → reject duplicates
    2. Parameter validation → reject invalid input
    3. Rule evaluation → compute derived values
    4. Mutation building → generate change intents with expected_version
    5. Row-level OCC → UPSERT object_state WHERE version = :expected
       (affected_rows = 0 → ConflictError, rollback entire tx)
    6. PG atomic commit (object_state + execution_log + outbox)
    7. Return "applied" (data immediately readable via object_state)
    8. Async: SeaTunnel CDC (PG WAL → Iceberg + Kafka → Doris) + Outbox Executor

Palantir alignment:
    This is the Gaia equivalent of Foundry's Action execution pipeline (OSv2).
    The PG transaction + ObjectState approach mirrors OSv2's Transaction
    Coordinator with lock-free MVCC at a much lighter weight.
"""

import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

from ontology.config.settings import settings
from ontology.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    OntologyError,
    ValidationError,
)
from ontology.core.models.defaults import utcnow
from ontology.core.permission_roles import OP_OBJECT_WRITE
from ontology.core.property_mapping import api_to_backing, backing_to_api
from ontology.core.rid import generate_object_rid
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
    BatchActionRequest,
    BatchActionResult,
    BatchItemResult,
    OntologyRule,
    SubmissionCriterion,
    ValueSource,
)
from ontology.core.schemas.ontology import ActionType, ObjectType
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services._metadata_owner import MetadataOwnerMixin
from ontology.services.action_auth import ActionAuthorizer
from ontology.services.action_rule_engine import ActionRuleEngine
from ontology.services.action_validator import ParameterValidator

if TYPE_CHECKING:
    # IcebergStore 仅类型注解；移入 TYPE_CHECKING 避免 lite 版拉 pyiceberg 重依赖（A3）。
    from ontology.layers.dataset.iceberg_store import IcebergStore
    from ontology.services.authorization_service import AuthorizationService
    from ontology.services.graph_projector import GraphProjector
    from ontology.services.object_query_service import ObjectQueryService


class ActionService(MetadataOwnerMixin):
    """Data write orchestration with full Action lifecycle.

    Depends on:
        - PostgresMetaStore: Metadata + Action execution logs + Outbox
        - GravitinoRegistry: Permission checks (write access)
        - IcebergStore: Analytical persistence (async via CDC)

    Optional:
        - ActionRuleEngine: Declarative rule evaluation (P1)
    """

    _logger = logging.getLogger("ontology.action_service")

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry | None,
        dataset: "IcebergStore | None",
        rule_engine: ActionRuleEngine | None = None,
        authorizer: ActionAuthorizer | None = None,
        object_query_service: "ObjectQueryService | None" = None,
        authorization_service: "AuthorizationService | None" = None,
        graph_projector: "GraphProjector | None" = None,
    ) -> None:
        self._metadata = metadata
        self._catalog = catalog
        self._dataset = dataset
        self._validator = ParameterValidator()
        self._rule_engine = rule_engine or ActionRuleEngine()
        # P1 (ADR-011): three-layer authorization. When None, an internal
        # default ActionAuthorizer is constructed lazily (requires
        # authorization_service — the PDP is mandatory, not optional).
        self._authorizer = authorizer
        self._authz = authorization_service
        # ADR Action Mutation Mapping: ObjectQueryService for hydrate (决策 C) —
        # Modify/Upsert 时 object_state 缺失从读路径(Doris,含 Trino 降级)读
        # 全量当前值补建。Optional for backward compat / isolated tests。
        self._object_query = object_query_service
        # ADR-015 §capabilities: GraphProjector for edge projection (RELATE/
        # UNRELATE). Node projection is handled by OutboxExecutor (INDEX effect
        # side); edge projection has no outbox, so it's done directly after
        # Step 10 commit (fail-tolerant). None = skip (graph not configured).
        self._graph_projector = graph_projector
        # Per-execution ObjectType cache for api↔backing_column translation.
        # (Re)initialized at the top of each execute_action call.
        self._ot_cache: dict[tuple[str, str], ObjectType | None] = {}

    async def _resolve_ot_cached(self, ontology_api_name: str, object_type_api_name: str) -> ObjectType | None:
        """Resolve an ObjectType with per-execution caching (api↔backing mapping).

        Mutations may target multiple object types within one Action (e.g.
        flightStatusLog vs flight). The ObjectType is needed to translate
        property keys between api_name (Action surface) and backing_column
        (object_state storage, see core.property_mapping). Cached per
        ``execute_action`` call so repeated mutations on the same type don't
        re-query. Returns None on lookup failure (caller falls back to
        passthrough — keys unchanged).
        """
        cache = self._ot_cache
        key = (ontology_api_name, object_type_api_name)
        if key in cache:
            return cache[key]
        try:
            ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        except Exception:
            ot = None
        cache[key] = ot
        return ot

    async def _props_to_backing(
        self, props: dict[str, Any], ontology_api_name: str, object_type_api_name: str
    ) -> dict[str, Any]:
        """Translate mutation properties api_name → backing_column for storage.

        Mutations carry properties in api_name form (the Action surface).
        object_state stores them keyed by backing_column (core.property_mapping),
        keeping the CDC chain (PG → Kafka → Doris) name-consistent end to end.
        OT lookup failure → passthrough (keys unchanged, safe for synthetic OTs).
        """
        ot = await self._resolve_ot_cached(ontology_api_name, object_type_api_name)
        return api_to_backing(ot, props)

    async def _snapshot_to_api(
        self,
        state: dict[str, Any] | None,
        ontology_api_name: str,
        fallback_type: str,
    ) -> dict[str, Any]:
        """Translate an object_state snapshot backing_column → api_name for audit.

        The execution log (before/after snapshots) is read by humans / OMA in
        the business vocabulary, so properties are surfaced as api_name.
        Uses the snapshot's own ``object_type_api_name`` when present (handles
        cross-type mutations), falling back to ``fallback_type``.
        """
        if not state:
            return state or {}
        ot_api = state.get("object_type_api_name") or fallback_type
        ot = await self._resolve_ot_cached(ontology_api_name, ot_api)
        if ot is None or "properties" not in state:
            return state
        return {**state, "properties": backing_to_api(ot, state["properties"])}

    def _get_authorizer(self) -> ActionAuthorizer:
        """Return the configured authorizer, or lazily construct a default.

        Requires authorization_service (the PDP is mandatory). Callers that
        don't inject an authorizer get a default ActionAuthorizer bound to
        this service's PDP.
        """
        if self._authorizer is None:
            if self._authz is None:
                raise OntologyError("ActionService requires AuthorizationService — wire it via the container")
            self._authorizer = ActionAuthorizer(
                metadata=self._metadata,
                catalog=self._catalog,
                rule_engine=self._rule_engine,
                authorization_service=self._authz,
            )
        return self._authorizer

    # ── Action Type Definition ──

    async def define_action_type(
        self,
        ontology_api_name: str,
        action_type_def: ActionTypeCreate,
    ) -> ActionType:
        """Register a new ActionType definition.

        Args:
            ontology_api_name: The ontology scope.
            action_type_def: Full action type definition with parameters, rules, effects.

        Returns:
            The created ActionType.

        Raises:
            NotFoundError: If ontology or affected object type not found.
        """
        # Resolve ontology
        onto = await self._metadata.get_ontology(ontology_api_name)

        # Resolve affected object type
        obj_type = await self._metadata.get_object_type_by_api_name(
            onto.id, action_type_def.affected_object_type_api_name
        )

        # Build parameters dict with structured definitions
        parameters_dict: dict[str, Any] = {
            "parameters": [p.model_dump() for p in action_type_def.parameters],
            "rules": [r.model_dump() for r in action_type_def.rules],
            "effects": [e.model_dump() for e in action_type_def.effects],
            # ADR Action Mutation Mapping: ontology_rules 与 effects/rules 同处
            # parameters JSON,执行期由 _build_mutations_from_rules 读取。
            "ontology_rules": [r.model_dump() for r in action_type_def.ontology_rules],
        }
        rules_dict: dict[str, Any] = {
            "rules": [r.model_dump() for r in action_type_def.rules],
        }
        # P1 (ADR-011): normalize submission_criteria to list[dict] for storage.
        # Accepts list[SubmissionCriterion] or legacy dict {expr: msg}.
        submission_criteria = [
            c.model_dump() for c in self._normalize_submission_criteria(action_type_def.submission_criteria)
        ]

        now = utcnow()
        action_type = ActionType(
            id="",
            ontology_id=onto.id,
            api_name=action_type_def.api_name,
            display_name=action_type_def.display_name,
            description=action_type_def.description,
            affected_object_type_id=obj_type.id,
            parameters=parameters_dict,
            rules=rules_dict,
            submission_criteria=submission_criteria,
            status="ACTIVE",
            risk_level=action_type_def.risk_level,
            version=1,
            operation_kind=action_type_def.operation_kind,
            batch_enabled=action_type_def.batch_enabled,
            created_at=now,
            updated_at=now,
        )
        # 事务单元：ActionType 写入 + 版本快照原子提交（修复快照丢失 bug）。
        async with self.transaction():
            created = await self._metadata.create_action_type(action_type, auto_commit=False)
            # P1 (ADR-011): publish v1 version snapshot for audit/rollback.
            await self._publish_version_snapshot(created, published_by="system")
        return created

    async def update_action_type(
        self,
        ontology_api_name: str,
        api_name: str,
        updates: dict[str, Any],
        published_by: str = "system",
    ) -> ActionType:
        """Update an ActionType and publish a new version snapshot (P1, ADR-011).

        Bumps the ActionType version, applies mutable field updates, and
        records a historical snapshot for rollback.
        """
        # 事务单元：更新 + 版本快照原子提交。
        async with self.transaction():
            updated = await self._metadata.update_action_type(ontology_api_name, api_name, updates, auto_commit=False)
            await self._publish_version_snapshot(updated, published_by=published_by)
        return updated

    async def rollback_action_type(
        self,
        ontology_api_name: str,
        api_name: str,
        target_version: int,
        published_by: str = "system",
    ) -> ActionType:
        """Roll back an ActionType to a prior version (P1, ADR-011).

        Loads the historical snapshot for ``target_version``, applies it as a
        new version (version = current + 1), so the rollback itself is audited
        and reversible. The live ActionTypeModel row is overwritten with the
        snapshot's mutable fields.
        """
        current = await self._metadata.get_action_type(ontology_api_name, api_name)
        snapshot_model = await self._metadata.get_action_type_version(current.id, target_version)
        if snapshot_model is None:
            raise NotFoundError(f"ActionType version {target_version}", api_name)
        snapshot = snapshot_model.snapshot
        updates = {
            "display_name": snapshot.get("display_name", current.display_name),
            "description": snapshot.get("description", current.description),
            "parameters": snapshot.get("parameters", current.parameters),
            "rules": snapshot.get("rules", current.rules),
            "submission_criteria": snapshot.get("submission_criteria", current.submission_criteria),
            "risk_level": snapshot.get("risk_level", current.risk_level),
            "operation_kind": snapshot.get("operation_kind", current.operation_kind),
            "batch_enabled": snapshot.get("batch_enabled", current.batch_enabled),
        }
        # 事务单元：回滚写入 + 新版本快照原子提交（回滚本身可审计）。
        async with self.transaction():
            rolled_back = await self._metadata.update_action_type(
                ontology_api_name, api_name, updates, auto_commit=False
            )
            await self._publish_version_snapshot(rolled_back, published_by=published_by)
        return rolled_back

    async def list_action_type_versions(self, ontology_api_name: str, api_name: str) -> list[Any]:
        """List historical versions of an ActionType (P1, ADR-011)."""
        at = await self._metadata.get_action_type(ontology_api_name, api_name)
        return await self._metadata.list_action_type_versions(at.id)

    async def get_action_type(self, ontology_api_name: str, api_name: str) -> ActionType:
        """Fetch a single ActionType by api_name (ADR Action Mutation Mapping).

        Thin wrapper over the metadata layer so the action routes have a
        single service entry point for all ActionType operations.
        """
        return await self._metadata.get_action_type(ontology_api_name, api_name)

    async def delete_action_type(self, ontology_api_name: str, api_name: str) -> None:
        """Soft-delete an ActionType by marking it DEPRECATED (ADR Action Mutation Mapping).

        Hard delete would lose audit history; soft-delete keeps the row for
        version snapshots / rollback while hiding it from the active list
        (list_action_types filters status != DEPRECATED).
        """
        await self._metadata.update_action_type(ontology_api_name, api_name, {"status": "DEPRECATED"})

    async def preview_action(
        self,
        object_type_api_name: str,
        action_api_name: str,
        request: ActionExecutionRequest,
        ontology_api_name: str | None = None,
        context: ActionContext | None = None,
    ) -> ActionPreviewResult:
        """Dry-run an action without persisting (P1, ADR-011 — OMA debug panel).

        Runs the full pipeline up to mutation building and before_snapshot
        collection, but skips object_state/outbox writes. Lets the OMA debug
        panel show what *would* happen.
        """
        if ontology_api_name is None:
            ontology_api_name = "default"
        ctx = context or ActionContext()
        action_type = await self._metadata.get_action_type(ontology_api_name, action_api_name)

        # Idempotency / permission checks still run (preview should surface
        # permission failures too), but no writes.
        authorizer = self._get_authorizer()
        await authorizer.check_execute_permission(action_type, ctx)
        request.parameters = authorizer.filter_sensitive_parameters(action_type, request.parameters, ctx)

        param_defs = [ActionTypeParameter(**p) for p in action_type.parameters.get("parameters", [])]
        self._validator.resolve_defaults(param_defs, request.parameters, ctx)
        try:
            self._validator.validate(param_defs, request.parameters)
        except ValidationError as e:
            return ActionPreviewResult(valid=False, validation_errors=[str(e)])

        rule_defs = [ActionRule(**r) for r in action_type.parameters.get("rules", [])]
        derived, rule_errors = self._rule_engine.evaluate(rule_defs, request.parameters, ctx)
        if rule_errors:
            return ActionPreviewResult(valid=False, validation_errors=rule_errors)

        criteria = self._normalize_submission_criteria(action_type.submission_criteria)
        criterion_errors = self._rule_engine.evaluate_submission_criteria(criteria, request.parameters, ctx)
        if criterion_errors:
            return ActionPreviewResult(valid=False, validation_errors=criterion_errors)

        mutations = self._build_mutations(action_type, request.parameters)
        before_snapshots: dict[str, Any] = {}
        for mutation in mutations:
            if mutation["type"] in ("UPDATE_OBJECT", "UPDATE_PROPERTY", "DELETE_OBJECT"):
                current_state = await self._metadata.get_object_state(mutation["rid"])
                before_snapshots[mutation["rid"]] = current_state or {}

        return ActionPreviewResult(
            valid=True,
            mutations=mutations,
            before_snapshots=before_snapshots,
            derived_parameters=derived,
        )

    @staticmethod
    def _normalize_submission_criteria(
        criteria: list[Any] | dict[str, Any],
    ) -> list[SubmissionCriterion]:
        """Normalize submission_criteria to list[SubmissionCriterion].

        - list[SubmissionCriterion] → passthrough
        - dict {expression: error_message} (legacy) → single-element list
        - list[dict] (already normalized from storage) → validated
        """
        if isinstance(criteria, dict):
            return [
                SubmissionCriterion(expression=expr, error_message=msg)
                for expr, msg in criteria.items()
                if isinstance(msg, str) and msg
            ]
        result: list[SubmissionCriterion] = []
        for item in criteria:
            if isinstance(item, SubmissionCriterion):
                result.append(item)
            elif hasattr(item, "model_dump"):
                result.append(SubmissionCriterion(**item.model_dump()))
            elif isinstance(item, dict):
                result.append(SubmissionCriterion(**item))
        return result

    async def _publish_version_snapshot(self, action_type: ActionType, published_by: str) -> None:
        """Publish a version snapshot of the ActionType for audit/rollback.

        必须在调用方的事务单元内调用（auto_commit=False），与 ActionType 写入
        原子提交。快照失败会让整个事务回滚（对标最佳实践 item 10：多步操作
        作为原子单元），不再静默吞异常——此前 ``except Exception: pass`` 导致
        快照静默丢失且不可观测（见 bugfix 文档）。
        """
        snapshot = action_type.model_dump(mode="json")
        try:
            await self._metadata.publish_action_type_version(
                action_type_id=action_type.id,
                version=action_type.version,
                snapshot=snapshot,
                published_by=published_by,
                auto_commit=False,
            )
        except Exception:
            # 可观测性：记录日志后重新 raise，让外层事务回滚。
            self._logger.warning(
                "Failed to publish ActionType version snapshot",
                extra={"action_type_id": action_type.id, "version": action_type.version},
                exc_info=True,
            )
            raise

    # ── Action Execution ──

    async def execute_action(
        self,
        object_type_api_name: str,
        action_api_name: str,
        request: ActionExecutionRequest,
        ontology_api_name: str | None = None,
        context: ActionContext | None = None,
    ) -> ActionExecutionResult:
        """Execute an action with full lifecycle.

        Args:
            object_type_api_name: Target object type.
            action_api_name: Action type to execute.
            request: Execution parameters + optional idempotency key.
            ontology_api_name: Optional ontology scope.

        Returns:
            ActionExecutionResult with status and details.

        Raises:
            NotFoundError: If action type or object type not found.
            ValidationError: If parameter validation fails.
            ConflictError: If optimistic lock conflict detected.
            ForbiddenError: If write access denied.
        """
        # Step 1: Resolve ActionType definition
        if ontology_api_name is None:
            # Derive ontology from object_type
            ontology_api_name = "default"  # Fallback — caller should provide

        # Per-execution ObjectType cache for api↔backing_column property key
        # translation (core.property_mapping). Reset at the top of every
        # execute_action call so it never leaks across requests.
        self._ot_cache = {}

        ctx = context or ActionContext()
        action_type = await self._metadata.get_action_type(ontology_api_name, action_api_name)

        # Step 1.5 (P1, ADR-011): Layer 1 — Action execution permission.
        authorizer = self._get_authorizer()
        await authorizer.check_execute_permission(action_type, ctx)
        # Layer 3 — strip sensitive parameters the caller cannot see.
        request.parameters = authorizer.filter_sensitive_parameters(action_type, request.parameters, ctx)

        # Step 2: Idempotency check
        if request.idempotency_key:
            existing = await self._metadata.get_execution_by_idempotency_key(request.idempotency_key)
            if existing is not None:
                return ActionExecutionResult(
                    status="accepted",
                    action_id=existing.action_id,
                    mutations=list(existing.mutations) if existing.mutations else [],
                )

        # Step 3: Parse parameter definitions
        param_defs = [ActionTypeParameter(**p) for p in action_type.parameters.get("parameters", [])]

        # Step 3.5 (P1, ADR-011): Resolve dynamic default values from context
        # (currentUser, currentTimestamp, workspaceId, selectedObject fields).
        self._validator.resolve_defaults(param_defs, request.parameters, ctx)

        # Step 4: Validate parameters. Validation failures are contract
        # errors (HTTP 422 via the global OntologyError handler), not a
        # 200-with-body status — see ADR (REST error mode for Actions).
        try:
            self._validator.validate(param_defs, request.parameters)
        except ValidationError as e:
            raise ValidationError(str(e), code="VALIDATION_FAILED") from e

        # Step 4.5 (ADR Action Mutation Mapping 决策 7): hydrate ObjectReference
        # 参数引用的对象属性,注入参数命名空间,使 validation 规则可读引用对象属性
        # (如 ReassignAircraft 校验 newAircraft.status != 'Maintenance')。仅读用户
        # 直接传入的对象属性,不做关系链遍历。object_query 缺失时跳过(降级)。
        if self._object_query is not None:
            await self._hydrate_reference_params(param_defs, request.parameters, ctx, ontology_api_name)

        # Step 5: Evaluate rules (derivations + constraints)
        rule_defs = [ActionRule(**r) for r in action_type.parameters.get("rules", [])]
        derived, rule_errors = self._rule_engine.evaluate(rule_defs, request.parameters, ctx)
        if rule_errors:
            raise ValidationError("; ".join(rule_errors), code="VALIDATION_FAILED")

        # Step 5.1 (P1, ADR-011): Evaluate global submission criteria.
        # Normalizes legacy dict form to list[SubmissionCriterion] first.
        criteria = self._normalize_submission_criteria(action_type.submission_criteria)
        criterion_errors = self._rule_engine.evaluate_submission_criteria(criteria, request.parameters, ctx)
        if criterion_errors:
            raise ValidationError("; ".join(criterion_errors), code="VALIDATION_FAILED")

        # Step 5b: VIRTUAL write guard — architecture red line: VIRTUAL
        # ObjectTypes are read-only external proxies; Actions must target
        # MANAGED objects (the only Iceberg write entry point). Mirrors the
        # frontend F5 guard on ActionsOverview.
        target_ot = await self._metadata.get_object_type(ontology_api_name, object_type_api_name)
        if target_ot.storage_type == "VIRTUAL":
            raise ValidationError(
                f"ObjectType {object_type_api_name} is VIRTUAL (read-only external "
                f"proxy); Actions cannot write to virtual objects"
            )

        # Step 6: Permission check (type-level write, fail-closed via PDP).
        if self._authz is None:
            raise OntologyError("ActionService requires AuthorizationService — wire it via the container")
        write_result = await self._authz.check_access(
            ctx.principal, "OBJECT_TYPE", object_type_api_name, OP_OBJECT_WRITE
        )
        if not write_result.allowed:
            raise ForbiddenError(f"Write access denied for {object_type_api_name}: {write_result.reason}")

        # Step 7: Build mutations (resolved change intents with expected_version)
        # ADR Action Mutation Mapping: 若声明了 ontology_rules,走声明式构建
        # (含 hydrate 决策 C / 主键校验 / on_missing→404 / OCC 衔接);
        # 否则回退旧 _build_mutations 硬编码行为(向后兼容)。
        ontology_rules_raw = action_type.parameters.get("ontology_rules", [])
        if ontology_rules_raw:
            mutations = await self._build_mutations_from_rules(
                action_type, ontology_rules_raw, request.parameters, ctx, ontology_api_name
            )
        else:
            mutations = self._build_mutations(action_type, request.parameters)

        # Step 7.1 (P1, ADR-011): Layer 2 — object row-level write permission.
        # Filter out mutations whose target object the caller may not write.
        # Link mutations carry rid too (the source object), so they are
        # covered by the same check. Pure-create mutations (rid freshly
        # generated, not yet existing) are always allowed at this layer.
        candidate_ids = [m["rid"] for m in mutations if m.get("rid") and m["type"] != "CREATE_OBJECT"]
        forbidden_ids: set[str] = set()
        if candidate_ids:
            forbidden_ids = await authorizer.check_row_write_permission(object_type_api_name, candidate_ids, ctx)
        forbidden_list: list[str] = sorted(forbidden_ids)
        if forbidden_ids:
            mutations = [m for m in mutations if m.get("rid") not in forbidden_ids]
            if not mutations:
                # Every target forbidden — reject outright.
                raise ForbiddenError(f"Row-level write denied for all target objects: {forbidden_list}")

        # Step 7.5 (P1, ADR-011): Collect before_snapshots for CDL audit.
        # For UPDATE/DELETE mutations, read current object_state so the
        # execution log records the full before→after transition.
        # object_state stores properties keyed by backing_column (core.property_mapping);
        # the audit snapshot surfaces them as api_name so the execution log is
        # readable in the business vocabulary.
        before_snapshots: dict[str, Any] = {}
        # action-sync-outbox-design.md §8.3: raw (backing_column key) 前态,
        # DELETE 后 object_state 已删, 需在删之前抓取业务 PK 值。
        raw_before_states: dict[str, dict[str, Any] | None] = {}
        for mutation in mutations:
            if mutation["type"] in ("UPDATE_OBJECT", "UPDATE_PROPERTY", "DELETE_OBJECT"):
                current_state = await self._metadata.get_object_state(mutation["rid"])
                before_snapshots[mutation["rid"]] = await self._snapshot_to_api(
                    current_state, ontology_api_name, mutation.get("object_type") or object_type_api_name
                )
                # action-sync-outbox-design.md §8.3: 保留 raw (backing_column key)
                # 前态,供 DELETE 的 ARCHIVE/INDEX outbox 取业务 PK 值 (DELETE 后
                # object_state 已删, get_object_state 返回 None)。
                if mutation["type"] == "DELETE_OBJECT":
                    raw_before_states[mutation["rid"]] = current_state

        # Step 8: Row-level OCC + apply mutations to object_state (in same tx)
        affected_objects: dict[str, int] = {}
        after_snapshots: dict[str, Any] = {}
        # action-sync-outbox-design.md §8.3: raw (backing_column key) 后态,
        # 供 CREATE/UPDATE 的 ARCHIVE/INDEX outbox 直接取全量属性 (object_state
        # 已是 backing_column key, 无需再翻译)。DELETE 用 raw_before_states。
        raw_after_states: dict[str, dict[str, Any] | None] = {}
        for mutation in mutations:
            obj_id = mutation.get("rid", generate_object_rid())
            expected_version = mutation.get("expected_version", 0)
            # ADR Action Mutation Mapping: ontology_rules 可针对不同对象类型
            # (如 flightStatusLog);mutation 携带的 object_type 优先。
            mut_obj_type = mutation.get("object_type") or object_type_api_name

            if mutation["type"] == "CREATE_OBJECT":
                new_version = await self._metadata.upsert_object_state(
                    rid=obj_id,
                    object_type_api_name=mut_obj_type,
                    ontology_id=action_type.ontology_id,
                    ontology_api_name=ontology_api_name,
                    properties=await self._props_to_backing(
                        mutation.get("properties", {}), ontology_api_name, mut_obj_type
                    ),
                    expected_version=0,
                    modified_by=ctx.current_user,
                )
            elif mutation["type"] in ("UPDATE_PROPERTY", "UPDATE_OBJECT"):
                new_version = await self._metadata.upsert_object_state(
                    rid=obj_id,
                    object_type_api_name=mut_obj_type,
                    ontology_id=action_type.ontology_id,
                    ontology_api_name=ontology_api_name,
                    properties=await self._props_to_backing(
                        mutation.get("properties", {}), ontology_api_name, mut_obj_type
                    ),
                    expected_version=expected_version,
                    modified_by=ctx.current_user,
                )
            elif mutation["type"] == "DELETE_OBJECT":
                await self._metadata.delete_object_state(obj_id)
                new_version = -1
            elif mutation["type"] == "RELATE":
                # P1 (ADR-011): Link mutation — add a relationship.
                link_type = mutation.get("link_type_api_name")
                target_id = mutation.get("target_rid")
                if link_type and target_id:
                    await self._metadata.add_object_link(
                        ontology_id=action_type.ontology_id,
                        link_type_api_name=link_type,
                        source_rid=obj_id,
                        target_rid=target_id,
                    )
                continue  # Link ops don't bump object version
            elif mutation["type"] == "UNRELATE":
                link_type = mutation.get("link_type_api_name")
                target_id = mutation.get("target_rid")
                if link_type and target_id:
                    await self._metadata.remove_object_link(
                        ontology_id=action_type.ontology_id,
                        link_type_api_name=link_type,
                        source_rid=obj_id,
                        target_rid=target_id,
                    )
                continue
            elif mutation["type"] == "CLEAR_LINKS":
                link_type = mutation.get("link_type_api_name")
                if link_type:
                    await self._metadata.clear_object_links(
                        ontology_id=action_type.ontology_id,
                        link_type_api_name=link_type,
                        source_rid=obj_id,
                    )
                continue
            else:
                continue

            # new_version=0 means affected_rows=0 → version mismatch (conflict).
            # Per REST error mode this raises ConflictError (HTTP 409) rather
            # than returning a 200-with-conflict-status body.
            if new_version == 0 and mutation["type"] not in ("DELETE_OBJECT",):
                await self._metadata.rollback_transaction()
                raise ConflictError(
                    f"Object {obj_id} modified by another action "
                    f"(expected_version={expected_version}); refresh and retry",
                    code="OCC_CONFLICT",
                )
            affected_objects[obj_id] = new_version

        # Step 8.5 (P1, ADR-011): Collect after_snapshots for CDL audit.
        for obj_id in affected_objects:
            after = await self._metadata.get_object_state(obj_id)
            after_snapshots[obj_id] = await self._snapshot_to_api(after, ontology_api_name, object_type_api_name)
            # action-sync-outbox-design.md §8.3: 保留 raw (backing_column key)
            # 后态, CREATE/UPDATE 的 outbox 直接用它写 Doris/Iceberg (key=列名)。
            raw_after_states[obj_id] = after

        # Step 9: Record execution log + outbox (in same PG transaction)
        idempotency_key = request.idempotency_key or uuid.uuid4().hex
        action_id = uuid.uuid4().hex
        execution = await self._metadata.create_execution_log(
            action_type_api_name=action_api_name,
            object_type_api_name=object_type_api_name,
            ontology_id=action_type.ontology_id,
            idempotency_key=idempotency_key,
            parameters=request.parameters,
            mutations=mutations,
            action_id=action_id,
            status="COMPLETED",
            performed_by=ctx.current_user,
            before_snapshot=before_snapshots,
            after_snapshot=after_snapshots,
        )

        # Create outbox records for configured side effects
        effects = action_type.parameters.get("effects", [])
        # B5: lite 桌面版只保留 webhook/notification/sub_action effect（用户可选配置），
        # 跳过 write_back（无回写源）/kafka_topic（无 Kafka）——桌面版边界外。
        _lite_skip_effects = {"write_back", "kafka_topic"} if settings.edition == "lite" else set()
        for effect_config_dict in effects:
            try:
                effect = ActionEffectConfig(**effect_config_dict)
                if effect.type in _lite_skip_effects:
                    continue
                # ADR Action Mutation Mapping: write_back effect 需从 ObjectType
                # backing_mapping 推导 table/primary_key,并填入对应 mutation
                # 的最终 properties 作 changes。未配 target_object_type/op 的旧
                # write_back 配置保持原样(payload 仅带 mutations,由消费方解析)。
                outbox_config, outbox_payload = await self._build_outbox_effect(effect, mutations, ontology_api_name)
                await self._metadata.create_outbox_record(
                    action_execution_id=execution.id,
                    effect_type=effect.type,
                    effect_config=outbox_config,
                    payload=outbox_payload,
                )
            except Exception:
                # Effect config parse failure shouldn't block the action
                pass

        # action-sync-outbox-design.md §8.3: 为每个 CREATE/UPDATE/DELETE mutation
        # 自动追加 INDEX (→Doris 近实时) + ARCHIVE (→Iceberg 微批) 两条 outbox
        # 记录, 复用同一 PG 事务保证原子性 (outbox 与 object_state 同提交)。
        # RELATE/UNRELATE/CLEAR_LINKS 跳过 (关系不同步, design §3.5)。失败不阻塞
        # Action (best-effort, 同上面 effect 处理一致)。
        await self._create_sync_outbox_records(
            execution_id=execution.id,
            mutations=mutations,
            ontology_api_name=ontology_api_name,
            object_type_api_name=object_type_api_name,
            affected_objects=affected_objects,
            raw_before_states=raw_before_states,
            raw_after_states=raw_after_states,
        )

        # Step 10: PG atomic commit (object_state + execution_log + outbox)
        await self._metadata.commit_transaction()

        # Step 11 (ADR-015 §capabilities): 图边投影 (RELATE/UNRELATE).
        # 节点投影由 OutboxExecutor INDEX effect 处理; 边投影没有 outbox,
        # 在 commit 后直接调 (fail-tolerant, 不影响已提交的 Action)。
        # 受 capabilities.graph_indexing_enabled 门控 (Gate 4)。
        await self._project_link_mutations(ontology_api_name, mutations, action_type.ontology_id)

        # NOTE: 异步副作用已从请求路径移除。Action 的核心契约是「写 PG → 返回
        # applied」（read-your-writes）, 同步到 Doris/Iceberg 与图/时空投影是
        # 派生链路, 由后台服务消费 (action-sync-outbox-design.md):
        #   - OutboxExecutor.run_forever (lifespan 启动) 消费 outbox 的
        #     WRITE_BACK / WEBHOOK / SUB_ACTION / KAFKA_TOPIC / INDEX effect
        #     (INDEX 近实时同步 object_state 变更到 Doris idx 表);
        #   - SyncFlushScheduler.run_flush_loop 消费 ARCHIVE outbox 微批归档到
        #     Iceberg 业务表 (MERGE INTO 按业务 PK 覆盖);
        #   - 路径 A (Iceberg→Doris 外部数据接入 backfill) 由 IndexSyncService
        #     provision/sync_now 事件驱动触发, 无常驻周期调度;
        #   - 图/时空投影是派生链路, 后续可改为 outbox effect 异步执行。

        return ActionExecutionResult(
            status="applied",
            action_id=action_id,
            affected_objects=affected_objects,
            mutations=mutations,
            forbidden_objects=forbidden_list,
        )

    # ── P2: Batch Action (ADR-011 follow-up) ──

    async def execute_batch_action(
        self,
        object_type_api_name: str,
        action_api_name: str,
        request: BatchActionRequest,
        ontology_api_name: str | None = None,
        context: ActionContext | None = None,
    ) -> BatchActionResult:
        """Apply one ActionType to a large set of target objects (P2).

        Each item is executed as its own atomic unit (own PG transaction +
        own idempotency key + own execution_log), so:
          - a single item's OCC conflict / validation error does NOT abort
            the whole batch — partial success is reported per item;
          - lock duration is bounded to one item (short WAL segment pressure);
          - the batch is safely re-runnable via derived per-item keys.

        Sharding: the item list is split into shards of ``shard_size`` for
        *observability* (shards_committed / shards_total in the result) and
        to bound the in-memory item_results list growth. Each shard is a
        sequential sweep of its items (parallelism within a shard would
        contend on the same PG connection; cross-shard parallelism is a
        follow-up once a connection pool is wired).

        fail_fast: when True, aborts at the first failing item WITHOUT
        rolling back items already committed in earlier shards (true
        cross-shard rollback is not supported — each shard's commits are
        durable). Use fail_fast only when partial success is unacceptable
        AND the caller is prepared to reconcile the committed prefix.

        Args:
            object_type_api_name: Target object type (same for all items).
            action_api_name: ActionType to execute (must be batch_enabled).
            request: items + shared defaults + shard_size + fail_fast.
            ontology_api_name: Optional ontology scope.
            context: Execution context (currentUser, roles, ...).

        Returns:
            BatchActionResult with aggregate status + per-item detail.
        """
        if ontology_api_name is None:
            ontology_api_name = "default"
        ctx = context or ActionContext()

        # ── Reject early before touching any item ──
        if len(request.items) > BATCH_MAX_ITEMS:
            return BatchActionResult(
                status="rejected",
                total=len(request.items),
                applied=0,
                failed=0,
                first_error=f"batch has {len(request.items)} items; max is {BATCH_MAX_ITEMS}",
            )

        action_type = await self._metadata.get_action_type(ontology_api_name, action_api_name)
        if not action_type.batch_enabled:
            return BatchActionResult(
                status="rejected",
                total=len(request.items),
                applied=0,
                failed=0,
                first_error=(
                    f"ActionType {action_api_name} has batch_enabled=False; "
                    "enable it in the definition before submitting a batch"
                ),
            )

        # ── Resolve shard size (clamp to [1, MAX]) ──
        shard_size = BATCH_DEFAULT_SHARD_SIZE if request.shard_size is None else request.shard_size
        shard_size = max(1, min(shard_size, BATCH_MAX_SHARD_SIZE))

        # ── Derive per-item idempotency keys (batch-key + index) ──
        batch_key = request.idempotency_key or uuid.uuid4().hex

        items = request.items
        n = len(items)
        # Build shards.
        shards: list[list[int]] = [list(range(start, min(start + shard_size, n))) for start in range(0, n, shard_size)]
        shards_total = len(shards)
        shards_committed = 0

        item_results: list[BatchItemResult] = [
            BatchItemResult(  # placeholder
                rid=it.rid, status="error", error="not run"
            )
            for it in items
        ]
        applied = 0
        accepted = 0
        failed = 0
        first_error: str | None = None

        # ── Sweep shards sequentially ──
        aborted = False
        for shard_idx, shard_indices in enumerate(shards):
            if aborted:
                break
            for idx in shard_indices:
                item = items[idx]
                # Merge shared defaults ← item parameters (item wins).
                merged_params = {**request.default_parameters, **item.parameters}
                # Ensure the target rid + expected_version reach the
                # ActionType (so UPDATE_OBJECT mutations resolve correctly).
                merged_params.setdefault("rid", item.rid)
                if item.expected_version:
                    merged_params.setdefault("expected_version", item.expected_version)

                item_idem = item.idempotency_key or f"{batch_key}#{idx}"
                req = ActionExecutionRequest(
                    parameters=merged_params,
                    idempotency_key=item_idem,
                )
                try:
                    result = await self.execute_action(
                        object_type_api_name=object_type_api_name,
                        action_api_name=action_api_name,
                        request=req,
                        ontology_api_name=ontology_api_name,
                        context=ctx,
                    )
                except NotFoundError as exc:
                    item_results[idx] = BatchItemResult(rid=item.rid, status="not_found", error=str(exc))
                    failed += 1
                    if first_error is None:
                        first_error = f"{item.rid}: {exc}"
                    if request.fail_fast:
                        aborted = True
                        break
                    continue
                except ForbiddenError as exc:
                    item_results[idx] = BatchItemResult(rid=item.rid, status="forbidden", error=str(exc))
                    failed += 1
                    if first_error is None:
                        first_error = f"{item.rid}: {exc}"
                    if request.fail_fast:
                        aborted = True
                        break
                    continue
                except ValidationError as exc:
                    item_results[idx] = BatchItemResult(rid=item.rid, status="validation_failed", error=str(exc))
                    failed += 1
                    if first_error is None:
                        first_error = f"{item.rid}: {exc}"
                    if request.fail_fast:
                        aborted = True
                        break
                    continue
                except ConflictError as exc:
                    item_results[idx] = BatchItemResult(rid=item.rid, status="conflict", error=str(exc))
                    failed += 1
                    if first_error is None:
                        first_error = f"{item.rid}: {exc}"
                    if request.fail_fast:
                        aborted = True
                        break
                    continue
                except Exception as exc:  # noqa: BLE001 — batch must survive item errors
                    self._logger.warning(
                        "Batch item %s (%s) errored: %s",
                        idx,
                        item.rid,
                        exc,
                        exc_info=True,
                    )
                    item_results[idx] = BatchItemResult(rid=item.rid, status="error", error=str(exc))
                    failed += 1
                    if first_error is None:
                        first_error = f"{item.rid}: {exc}"
                    if request.fail_fast:
                        aborted = True
                        break
                    continue

                # Success path.
                if result.status == "applied":
                    # new_version is the first affected object's version (the
                    # target rid when present).
                    new_ver = result.affected_objects.get(item.rid)
                    item_results[idx] = BatchItemResult(
                        rid=item.rid,
                        status="applied",
                        action_id=result.action_id,
                        new_version=new_ver,
                    )
                    applied += 1
                elif result.status == "accepted":
                    item_results[idx] = BatchItemResult(
                        rid=item.rid,
                        status="accepted",
                        action_id=result.action_id,
                    )
                    accepted += 1
                else:
                    # conflict / validation_failed surfaced as a result status
                    # (defensive — the except branches above already cover the
                    # raised forms).
                    item_results[idx] = BatchItemResult(
                        rid=item.rid,
                        status=result.status,
                        action_id=result.action_id,
                        error="; ".join(result.validation_errors),
                    )
                    failed += 1
                    if first_error is None:
                        first_error = f"{item.rid}: {result.status}"
                    if request.fail_fast:
                        aborted = True
            # A shard is "committed" once its items have been attempted (each
            # item is its own transaction; this counts shards that ran to
            # completion rather than being aborted mid-way).
            shards_committed = shard_idx + 1 if not aborted else shards_committed

        # ── Aggregate status ──
        if applied == 0 and accepted == 0:
            status: Literal["applied", "partial", "failed", "rejected"] = "failed"
        elif failed == 0:
            status = "applied"
        else:
            status = "partial"

        return BatchActionResult(
            status=status,
            total=n,
            applied=applied,
            accepted=accepted,
            failed=failed,
            item_results=item_results,
            shards_committed=shards_committed,
            shards_total=shards_total,
            first_error=first_error,
        )

    def _build_mutations(
        self,
        action_type: ActionType,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build resolved mutation intents from action type and parameters.

        Each mutation carries:
            - type: CREATE_OBJECT, UPDATE_PROPERTY, DELETE_OBJECT, etc.
            - rid: Target object identifier
            - expected_version: Version at client read time (for row-level OCC)
            - properties: Resolved property values (post rule evaluation)

        For simple actions, parameters are mapped directly to mutations.
        For complex actions, rules define the mapping logic.

        Note: Does NOT mutate the input `parameters` dict — uses `.get()` instead of `.pop()`.
        """
        mutations: list[dict[str, Any]] = []

        # If parameters contain explicit mutation definitions, use those
        if "mutations" in parameters:
            return list(parameters["mutations"])

        # If parameters contain a rid, treat as UPDATE
        if "rid" in parameters:
            obj_id = parameters["rid"]
            expected_version = parameters.get("expected_version", 0)
            # Build properties excluding control fields
            props = {k: v for k, v in parameters.items() if k not in ("rid", "expected_version")}
            mutations.append(
                {
                    "type": "UPDATE_OBJECT",
                    "rid": obj_id,
                    "expected_version": expected_version,
                    "properties": props,
                }
            )
            return mutations

        # Default: treat all parameters as CREATE_OBJECT properties
        mutations.append(
            {
                "type": "CREATE_OBJECT",
                "rid": generate_object_rid(),
                "expected_version": 0,
                "properties": parameters,
            }
        )
        return mutations

    # ── ADR Action Mutation Mapping: 声明式 Ontology Rules 执行 ──

    async def _hydrate_reference_params(
        self,
        param_defs: list[ActionTypeParameter],
        parameters: dict[str, Any],
        ctx: ActionContext,
        ontology_api_name: str,
    ) -> None:
        """决策 7: hydrate ObjectReference 参数引用的对象属性,注入参数命名空间。

        对每个 object_type_ref 非 None 的参数,读其主键值,从读路径(Doris,含
        Trino 降级)load 该对象全量属性,注入 parameters[param_name] 为属性 dict。
        这使 validation 规则可写 `newAircraft.status != 'Maintenance'`。

        仅读用户直接传入的对象属性,不做关系链遍历(决策 7)。
        hydrate 失败(对象不存在)时:留裸值,让后续 ModifyObject 的
        on_missing=raise_not_found 触发 404。仅读对象 → 不报错,降级。
        """
        for pd in param_defs:
            if pd.object_type_ref is None:
                continue
            pk_value = parameters.get(pd.api_name)
            if pk_value is None:
                continue
            try:
                obj = await self._hydrate_object(str(pk_value), pd.object_type_ref, ontology_api_name)
                if obj is not None:
                    # 覆盖裸值为属性 dict,供规则表达式 `param.prop` 访问。
                    parameters[pd.api_name] = obj
            except Exception:
                # hydrate 失败不阻断校验阶段:留裸值,ModifyObject 阶段再判 404。
                pass

    async def _hydrate_object(
        self,
        rid: str,
        object_type_api_name: str,
        ontology_api_name: str,
    ) -> dict[str, Any] | None:
        """决策 C: 从读路径读对象全量当前值。

        走 ObjectQueryService.execute_compiled_sql（TextQL/SqlGlot 编译路径，
        ADR-012 Step 4 path B）：拼点查 logical SQL
        ``SELECT * FROM <OT> WHERE <pk_api> = '<id>'``，编译器做列名映射、
        参数化绑定、方言分叉（MANAGED→Doris / VIRTUAL→Trino 联邦）。
        返回 None 表示对象不存在。

        字面量由 OntologySqlCompiler 自动提取为 ``?`` 占位符 + params（参数化
        绑定，注入安全），无需手写转义。
        """
        if self._object_query is None:
            return None
        return await self._object_query.hydrate_by_pk(f"{ontology_api_name}.{object_type_api_name}", rid)

    async def _build_mutations_from_rules(
        self,
        action_type: ActionType,
        ontology_rules_raw: list[Any],
        parameters: dict[str, Any],
        ctx: ActionContext,
        ontology_api_name: str,
    ) -> list[dict[str, Any]]:
        """声明式 Ontology Rules → Mutation 列表(ADR Action Mutation Mapping)。

        对每条 rule:
          1. condition 求值(假则 skip)
          2. 解析 target(主键值=parameters[target_parameter])
          3. 解析 properties(每个 ValueSource 求值)
          4. 按规则类型生成 mutation:
             - ModifyObject: hydrate(决策 C)+ OCC 衔接 + on_missing→404
             - UpsertObject: 同 Modify;0 行且 on_missing=create → CREATE
             - CreateObject: CREATE_OBJECT(SYSTEM_GENERATED 主键)
             - DeleteObject: 先 hydrate 校验存在 → DELETE_OBJECT
             - CreateLink/DeleteLink → RELATE/UNRELATE
        5. 主键不可出现在 Modify 的 properties(定义期+执行期双重校验)
        """
        rules = [OntologyRule(**r) if isinstance(r, dict) else r for r in ontology_rules_raw]
        # 执行期主键不可改校验(防绕过定义期校验)。
        await self._validate_rules_execution(rules, parameters, ontology_api_name)

        mutations: list[dict[str, Any]] = []
        # 缓存已 hydrate 的引用对象属性,供 OBJECT_PROPERTY / EXPRESSION 复用。
        # key=参数名,value=属性 dict(parameters 已被 _hydrate_reference_params 注入)。
        ref_cache: dict[str, dict[str, Any]] = {name: val for name, val in parameters.items() if isinstance(val, dict)}

        for rule in rules:
            # 条件执行(simpleeval,作用于参数命名空间)
            if rule.condition:
                try:
                    cond_result = self._rule_engine._safe_eval(rule.condition, parameters)
                    if not cond_result:
                        continue
                except Exception:
                    # 条件求值失败 → 视为不满足,跳过该规则(保守策略)。
                    continue

            mut: dict[str, Any] | None = None
            if rule.type == "CreateObject":
                mut = await self._build_create_mutation(rule, parameters, ctx, ontology_api_name)
            elif rule.type == "ModifyObject":
                mut = await self._build_modify_mutation(
                    rule,
                    parameters,
                    ctx,
                    ontology_api_name,
                    ref_cache,
                    upsert=False,
                    ontology_id=action_type.ontology_id,
                )
            elif rule.type == "UpsertObject":
                mut = await self._build_modify_mutation(
                    rule,
                    parameters,
                    ctx,
                    ontology_api_name,
                    ref_cache,
                    upsert=True,
                    ontology_id=action_type.ontology_id,
                )
            elif rule.type == "DeleteObject":
                mut = await self._build_delete_mutation(rule, parameters, ontology_api_name)
            elif rule.type == "CreateLink":
                mut = self._build_link_mutation(rule, parameters, link_op="RELATE")
            elif rule.type == "DeleteLink":
                mut = self._build_link_mutation(rule, parameters, link_op="UNRELATE")
            else:
                continue
            if mut is not None:
                mutations.append(mut)

        # 规则编译: 同对象多规则合并为单 mutation (对齐 Palantir rules 文档
        # "compile rules to generate a single edit per object" — 多条规则
        # 改同一对象时, 后者属性覆盖前者, 顺序敏感)。
        # 合并后: 每个对象只有一条 object mutation, 避免多次 upsert + 多条
        # INDEX/ARCHIVE outbox 的二义性。Link mutation 不参与合并 (多个 link
        # 操作语义不同, 不能合并)。
        return self._compile_mutations(mutations)

    @staticmethod
    def _compile_mutations(mutations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并同一对象的 object mutation 为单条 (Palantir 规则编译语义)。

        对齐 Palantir rules 文档: "if the result of one rule updates a property
        to A, but another rule in the same action type updates the same object's
        property to B, the resulting edit would just update the property to B.
        The order of rules affects the final object edit."

        合并规则 (按 mutation 声明顺序处理, 后者覆盖前者):
        - 同 rid 的 CREATE_OBJECT + CREATE_OBJECT → 后者的 override 覆盖
          (理论上 _validate_rules_execution 已拦重复 Create, 此处防御)
        - 同 rid 的 UPDATE_OBJECT + UPDATE_OBJECT → 用 _override 增量合并
          (后者 override 胜), expected_version 取后者 (后者 hydrate 读到的
          版本更新)。**关键: 用 _override 而非全量 properties 合并**, 否则
          后者的全量 properties (含 base 旧值) 会覆盖前者的改动。
        - 同 rid 的 CREATE + UPDATE → 合并为 CREATE (override 合并并入 Create
          的 properties, expected_version=0; 即 "先建后改" 归约为带全属性的建)
        - DELETE 不参与合并 (一个对象只能 Delete 一次, 重复由校验拦)
        - Link mutation (RELATE/UNRELATE/CLEAR_LINKS) 原样保留, 不合并
        - UPDATE_PROPERTY 归一为 UPDATE_OBJECT 处理 (二者存储语义一致)

        未合并前的重复 object mutation 会产生多条 INDEX/ARCHIVE outbox,
        导致同一对象被同步多次 (额外开销 + 中间状态泄漏到 Doris)。
        合并后每个对象一条 outbox, 语义干净。

        _override 内部字段: 仅携带 rule 声明的增量属性 (不含 base), 由
        _build_modify_mutation / _build_create_mutation 注入。合并后删除,
        不进入 outbox / execution_log / object_state。
        """
        if not mutations:
            return mutations

        compiled: list[dict[str, Any]] = []
        # rid → 在 compiled 中的索引 (仅跟踪 object mutation)。
        obj_index: dict[str, int] = {}

        for mut in mutations:
            mut_type = mut["type"]
            rid = mut.get("rid", "")

            # Link mutation: 不合并, 原样保留。
            if mut_type in ("RELATE", "UNRELATE", "CLEAR_LINKS"):
                compiled.append(mut)
                continue

            # DELETE_OBJECT: 不合并 (校验已保证唯一), 原样保留。
            if mut_type == "DELETE_OBJECT":
                compiled.append(mut)
                # 标记该 rid 已删除, 后续若再有 object mutation 属于
                # invalid combination (校验应已拦), 防御性跳过索引复用。
                if rid:
                    obj_index[rid] = len(compiled) - 1
                continue

            # 归一 UPDATE_PROPERTY → UPDATE_OBJECT (存储语义一致, 合并逻辑统一)。
            norm_type = "UPDATE_OBJECT" if mut_type == "UPDATE_PROPERTY" else mut_type
            # _override: 仅 rule 声明的增量。回退到全量 properties (兼容
            # 旧路径 _build_mutations / 外部直接构造的 mutation 无 _override)。
            override = dict(mut.get("_override", mut.get("properties", {})))

            if rid and rid in obj_index:
                # 同对象已有 mutation → 合并 (后者 override 覆盖前者 override)。
                prev = compiled[obj_index[rid]]
                prev_type = prev["type"]
                prev_override = dict(prev.get("_override", prev.get("properties", {})))
                merged_override = {**prev_override, **override}

                if prev_type == "CREATE_OBJECT":
                    # CREATE + (CREATE|UPDATE) → 合并为 CREATE, override 合并,
                    # properties = merged_override (Create 无 base), expected_version=0。
                    prev["_override"] = merged_override
                    prev["properties"] = merged_override
                    if mut.get("object_type"):
                        prev["object_type"] = mut["object_type"]
                elif prev_type in ("UPDATE_OBJECT", "UPDATE_PROPERTY"):
                    # UPDATE + UPDATE → override 合并 (后者胜),
                    # properties = base + merged_override (base 取 prev, 因 prev 与
                    # latter 同对象同 base), expected_version 取后者。
                    prev["_override"] = merged_override
                    # prev["properties"] 已是 prev 的 base + prev_override;
                    # 重算为 base + merged_override: 先剥离 prev_override 再加 merged_override。
                    base_props = {k: v for k, v in prev.get("properties", {}).items() if k not in prev_override}
                    prev["properties"] = {**base_props, **merged_override}
                    prev["expected_version"] = mut.get("expected_version", prev.get("expected_version", 0))
                    if mut.get("object_type"):
                        prev["object_type"] = mut["object_type"]
                    prev["type"] = "UPDATE_OBJECT"
                else:
                    # prev 是 DELETE (不应发生, 校验已拦) → 防御性追加。
                    compiled.append(mut)
                    obj_index[rid] = len(compiled) - 1
            else:
                # 新对象 mutation → 归一 type 后追加。
                new_mut = {**mut, "type": norm_type}
                compiled.append(new_mut)
                if rid:
                    obj_index[rid] = len(compiled) - 1

        # 删除内部字段 _override (不进入 outbox / execution_log / object_state)。
        for m in compiled:
            m.pop("_override", None)
        return compiled

    async def _build_create_mutation(
        self,
        rule: OntologyRule,
        parameters: dict[str, Any],
        ctx: ActionContext,
        ontology_api_name: str,
    ) -> dict[str, Any]:
        """CreateObject 规则 → CREATE_OBJECT mutation。

        主键:SYSTEM_GENERATED uuid,或显式 PARAMETER/STATIC 值(若声明在 properties)。
        """
        obj_type = rule.target_object_type or ""
        props = await self._resolve_rule_properties(rule, parameters, ctx)
        # 主键生成:若 properties 未含主键,且无 SYSTEM_GENERATED 声明,
        # 则自动生成 uuid 主键(仅当对象类型主键已知为字符串时;数值主键
        # 需用户显式声明 SYSTEM_GENERATED/STATIC)。这里统一用声明的 props。
        rid: str | None = None
        for src_name, vs in rule.properties.items():
            if vs.source == "SYSTEM_GENERATED" and vs.value == "uuid":
                rid = str(props.get(src_name))
                break
        if rid is None:
            # 未显式声明主键来源 → 生成 RID (ri.ontology.main.object.{uuid})。
            rid = generate_object_rid()
        return {
            "type": "CREATE_OBJECT",
            "rid": rid,
            "object_type": obj_type,
            "expected_version": 0,
            "properties": props,
            # CreateObject 无 base, _override = 全部 properties (用于合并语义统一)。
            "_override": props,
        }

    async def _build_modify_mutation(
        self,
        rule: OntologyRule,
        parameters: dict[str, Any],
        ctx: ActionContext,
        ontology_api_name: str,
        ref_cache: dict[str, dict[str, Any]],
        *,
        upsert: bool,
        ontology_id: str,
    ) -> dict[str, Any] | None:
        """ModifyObject/UpsertObject 规则 → UPDATE_OBJECT / CREATE_OBJECT mutation。

        决策 C hydrate: object_state 缺失时从读路径读全量补建,再 apply Modify。
        OCC 衔接: hydrate 写入 v1 后,并发 Modify 用读出的 version 做 expected_version。
        on_missing=raise_not_found 且 Doris 也不存在 → NotFoundError(404)。
        on_missing=create 且 upsert=True 且对象不存在 → CREATE_OBJECT。
        """
        target_param = rule.target_parameter
        if target_param is None:
            raise ValidationError(f"{rule.type} rule requires target_parameter", code="VALIDATION_FAILED")
        # 主键值:ObjectReference 参数可能已被 hydrate 成属性 dict(决策 7),
        # 需取其原始裸值(parameters 里已是 dict 时,主键 = 该对象的 primary_key)。
        pk_value = parameters.get(target_param)
        if isinstance(pk_value, dict):
            # hydrate 注入的属性 dict;主键值需从对象类型 primary_key 取。
            # 此处退化:取 dict 中的主键字段(benchmark flight_id 在 dict 里)。
            # 由于不知 primary_key 字段名,回退取 dict 第一个标量主键。
            pk_value = self._extract_pk_from_dict(pk_value)
        if pk_value is None:
            raise ValidationError(
                f"{rule.type} target_parameter '{target_param}' has no value",
                code="VALIDATION_FAILED",
            )
        rid = str(pk_value)
        obj_type = rule.target_object_type or ""

        # 决策 C: 先查 object_state,缺失则 hydrate。
        existing = await self._metadata.get_object_state(rid)
        if existing is not None:
            base_props = dict(existing.get("properties", {}))
            expected_version = int(existing.get("version", 1))
        else:
            # hydrate: 从读路径(Doris,含 Trino 降级)读全量当前值。
            hydrated = await self._hydrate_object(rid, obj_type, ontology_api_name)
            if hydrated is None:
                # Doris 也不存在该对象。
                if upsert and rule.on_missing == "create":
                    # UpsertObject on_missing=create → CREATE_OBJECT(主键用参数值)。
                    props = await self._resolve_rule_properties(rule, parameters, ctx)
                    return {
                        "type": "CREATE_OBJECT",
                        "rid": rid,
                        "object_type": obj_type,
                        "expected_version": 0,
                        "properties": props,
                    }
                # on_missing=raise_not_found(默认) → 404(write_004/012)。
                raise NotFoundError("Object", rid)
            # 写入 object_state(全量快照, version=1)作为 hydrate 基底。
            base_props = dict(hydrated)
            expected_version = 0  # 触发 upsert_object_state 的 CREATE 路径(ON CONFLICT DO NOTHING)
            # 预先 upsert hydrate 基底(写 v1),使后续并发 Modify 用 v1 做 OCC。
            # base_props is in api_name (from hydrate); object_state stores
            # backing_column keys, so translate before the pre-upsert.
            backing_props = await self._props_to_backing(base_props, ontology_api_name, obj_type)
            await self._metadata.upsert_object_state(
                rid=rid,
                object_type_api_name=obj_type,
                ontology_id=ontology_id,
                ontology_api_name=ontology_api_name,
                properties=backing_props,
                expected_version=0,
                modified_by=ctx.current_user,
            )
            expected_version = 1

        # apply Modify: 合并 base_props + rule.properties 覆盖(局部增量)。
        override_props = await self._resolve_rule_properties(rule, parameters, ctx)
        merged = {**base_props, **override_props}
        return {
            "type": "UPDATE_OBJECT",
            "rid": rid,
            "object_type": obj_type,
            "expected_version": expected_version,
            "properties": merged,
            # _override: 仅 rule 声明的增量 (不含 base), 供 _compile_mutations
            # 正确合并同对象多条 Modify (全量 properties 合并会丢失前者的改动)。
            # 合并后由 _compile_mutations 删除该内部字段。
            "_override": override_props,
        }

    async def _build_delete_mutation(
        self,
        rule: OntologyRule,
        parameters: dict[str, Any],
        ontology_api_name: str,
    ) -> dict[str, Any] | None:
        """DeleteObject 规则 → DELETE_OBJECT mutation(先 hydrate 校验存在)。"""
        target_param = rule.target_parameter
        if target_param is None:
            raise ValidationError("DeleteObject rule requires target_parameter", code="VALIDATION_FAILED")
        pk_value = parameters.get(target_param)
        if isinstance(pk_value, dict):
            pk_value = self._extract_pk_from_dict(pk_value)
        if pk_value is None:
            raise ValidationError(
                f"DeleteObject target_parameter '{target_param}' has no value",
                code="VALIDATION_FAILED",
            )
        rid = str(pk_value)
        obj_type = rule.target_object_type or ""
        existing = await self._metadata.get_object_state(rid)
        if existing is None:
            hydrated = await self._hydrate_object(rid, obj_type, ontology_api_name)
            if hydrated is None:
                # 对象不存在 → 404(与 Modify on_missing 一致)。
                raise NotFoundError("Object", rid)
        return {
            "type": "DELETE_OBJECT",
            "rid": rid,
            "object_type": obj_type,
            "expected_version": 0,
            "properties": {},
        }

    def _build_link_mutation(
        self,
        rule: OntologyRule,
        parameters: dict[str, Any],
        *,
        link_op: str,
    ) -> dict[str, Any] | None:
        """CreateLink/DeleteLink 规则 → RELATE/UNRELATE mutation。"""
        source_param = rule.source_parameter
        target_param = rule.target_link_parameter
        link_type = rule.link_type
        if not (source_param and target_param and link_type):
            return None
        source_id = parameters.get(source_param)
        target_id = parameters.get(target_param)
        if source_id is None or target_id is None:
            return None
        return {
            "type": link_op,
            "rid": str(source_id),
            "link_type_api_name": link_type,
            "target_rid": str(target_id),
            "expected_version": 0,
            "properties": {},
        }

    async def _resolve_rule_properties(
        self,
        rule: OntologyRule,
        parameters: dict[str, Any],
        ctx: ActionContext,
    ) -> dict[str, Any]:
        """解析 rule.properties 的每个 ValueSource,求值为具体属性值。"""
        resolved: dict[str, Any] = {}
        for prop_name, vs in rule.properties.items():
            resolved[prop_name] = self._resolve_value_source(vs, parameters, ctx)
        return resolved

    def _resolve_value_source(
        self,
        vs: ValueSource,
        parameters: dict[str, Any],
        ctx: ActionContext,
    ) -> Any:
        """按 ValueSource.source 求值(ADR §3.3 值来源规则)。"""
        if vs.source == "PARAMETER":
            if vs.value is None:
                raise ValidationError("PARAMETER ValueSource missing value", code="VALIDATION_FAILED")
            val = parameters.get(vs.value)
            # ObjectReference 参数可能被 hydrate 成 dict;取主键裸值。
            if isinstance(val, dict):
                return self._extract_pk_from_dict(val)
            return val
        if vs.source == "OBJECT_PROPERTY":
            # value = "参数名.属性名",读引用对象属性(决策 7)。
            if vs.value is None or "." not in vs.value:
                raise ValidationError(
                    f"OBJECT_PROPERTY ValueSource requires 'param.prop' format, got {vs.value!r}",
                    code="VALIDATION_FAILED",
                )
            param_name, prop_name = vs.value.split(".", 1)
            obj = parameters.get(param_name)
            if not isinstance(obj, dict):
                raise ValidationError(
                    f"OBJECT_PROPERTY references non-object parameter '{param_name}'",
                    code="VALIDATION_FAILED",
                )
            return obj.get(prop_name)
        if vs.source == "STATIC_VALUE":
            return vs.value
        if vs.source == "SYSTEM_CONTEXT":
            if vs.value == "CURRENT_USER_ID":
                return ctx.current_user
            if vs.value == "CURRENT_TIMESTAMP":
                return ctx.current_timestamp.isoformat()
            raise ValidationError(f"Unknown SYSTEM_CONTEXT value: {vs.value}", code="VALIDATION_FAILED")
        if vs.source == "SYSTEM_GENERATED":
            if vs.value == "uuid":
                return uuid.uuid4().hex
            raise ValidationError(f"Unknown SYSTEM_GENERATED value: {vs.value}", code="VALIDATION_FAILED")
        if vs.source == "EXPRESSION":
            if vs.value is None:
                raise ValidationError("EXPRESSION ValueSource missing value", code="VALIDATION_FAILED")
            try:
                return self._rule_engine._safe_eval(vs.value, parameters)
            except Exception as e:
                raise ValidationError(f"EXPRESSION evaluation failed: {e}", code="VALIDATION_FAILED") from e
        raise ValidationError(f"Unknown ValueSource source: {vs.source}", code="VALIDATION_FAILED")

    @staticmethod
    def _extract_pk_from_dict(obj: dict[str, Any]) -> Any:
        """从 hydrate 的属性 dict 中提取主键裸值。

        优先尝试常见主键命名(flightId/aircraftId/...Id/id),回退取第一个标量值。
        benchmark 的 flight/aircraft 主键为驼峰 *Id 字段。
        """
        for key in ("flightId", "aircraftId", "crewId", "taskId", "passengerId", "bookingId", "standId", "logId", "id"):
            if key in obj and not isinstance(obj[key], dict | list):
                return obj[key]
        # 回退:第一个标量值。
        for v in obj.values():
            if not isinstance(v, dict | list):
                return v
        return None

    async def _validate_rules_execution(
        self,
        rules: list[OntologyRule],
        parameters: dict[str, Any],
        ontology_api_name: str,
    ) -> None:
        """执行期校验(防绕过定义期)。

        两类校验:
        1. 主键不可出现在 Modify/Upsert 的 properties
           (查 ObjectType.primary_key 拿真实主键属性名,仅拒绝主键属性
           如 flight 的 flightId;外键属性如 aircraftId 可改)
        2. Invalid combinations (对齐 Palantir rules 文档 "Invalid combinations"):
           - 同一对象不可被多次 Create (重复创建)
           - 对象不可被 Modify/Upsert 后再 Create (先改后建)
           - 对象不可被 Delete 后再 Create/Modify (先删后建/先删后改)
           声明顺序即执行顺序,违反 → ValidationError(422)。
           依赖 condition 求值:跳过 condition 为假的规则后再判组合合法性
           (条件分支可能让两条看似冲突的规则实际只执行一条)。
        """
        # ── 校验 1: 主键不可改 ──
        for rule in rules:
            if rule.type not in ("ModifyObject", "UpsertObject"):
                continue
            target_ot = rule.target_object_type
            if not target_ot:
                continue
            try:
                ot = await self._metadata.get_object_type(ontology_api_name, target_ot)
            except Exception:
                continue
            pk = ot.primary_key
            if pk and pk in rule.properties:
                raise ValidationError(
                    f"Primary key '{pk}' cannot be modified in {rule.type} rule",
                    code="VALIDATION_FAILED",
                )

        # ── 校验 2: Invalid combinations (按声明顺序,跳过条件为假的规则) ──
        # 对齐 Palantir rules 文档 "Invalid combinations"。Palantir 语义是
        # "多条规则编译成单 edit"(同对象多条 Modify 会合并, 见 _compile_mutations),
        # invalid combinations 指的是**语义矛盾**的操作序列, 不是 "一个 op per object":
        #   - delete before add/modify: 先删后建/先删后改 (删了就没了, 后续 op 无意义)
        #   - modify before add: 先改后建 (对象尚不存在, 改无意义; Gaia 有 hydrate 兜底
        #     不会 crash, 但仍按 Palantir 语义拒绝以避免依赖时序的脆弱行为)
        #   - create twice: 重复创建同一对象
        # 同对象多条 ModifyObject/UpsertObject **不拦** (由 _compile_mutations 合并)。
        # 依赖 condition 求值: 跳过 condition 为假的规则后再判组合合法性
        # (条件分支可能让两条看似冲突的规则实际只执行一条)。
        seen_ops: dict[tuple[str, str], str] = {}
        for rule in rules:
            # 条件为假的规则跳过 (不参与组合校验)。
            if rule.condition:
                try:
                    if not self._rule_engine._safe_eval(rule.condition, parameters):
                        continue
                except Exception:
                    # 条件求值失败 → 保守视为不执行,跳过。
                    continue

            # 解析规则的目标对象标识 (object_type, pk_value)。
            obj_key = self._resolve_rule_object_key(rule, parameters)
            if obj_key is None:
                # 无目标对象 (如 CreateObject 无主键声明) 或主键值缺失 → 无法判组合,跳过。
                # CreateObject 的主键在 _build_create_mutation 才生成,这里无法预判重复,
                # 交给 _compile_mutations 的 create set 去重。
                continue

            op = rule.type
            prev_op = seen_ops.get(obj_key)
            if prev_op is None:
                seen_ops[obj_key] = op
                continue
            # 已有操作, 判断是否为 invalid combination。
            invalid = _is_invalid_combination(prev_op, op)
            if invalid:
                raise ValidationError(
                    f"Invalid rule combination: object {obj_key[0]} '{obj_key[1]}' "
                    f"is targeted by {prev_op} then {op}; an action cannot "
                    f"{_invalid_combination_reason(prev_op, op)}",
                    code="VALIDATION_FAILED",
                )
            # 合法组合 (如 Modify + Modify): 记录后者, 交给 _compile_mutations 合并。
            seen_ops[obj_key] = op

    @staticmethod
    def _resolve_rule_object_key(
        rule: OntologyRule,
        parameters: dict[str, Any],
    ) -> tuple[str, str] | None:
        """解析规则的目标对象标识 (object_type, pk_value) 用于 invalid combination 校验。

        - CreateObject: object_type + 主键 (从 properties 的 SYSTEM_GENERATED/STATIC/PARAMETER 取);
          无主键声明时返回 None (主键运行时生成,无法预判)。
        - ModifyObject/UpsertObject/DeleteObject: target_object_type + target_parameter 的值。
        - CreateLink/DeleteLink: 无单对象目标,返回 None (link 组合不限制)。
        """
        obj_type = rule.target_object_type or ""
        if rule.type == "CreateObject":
            if not obj_type:
                return None
            # CreateObject 主键可能声明在 properties (SYSTEM_GENERATED uuid 运行时生成,
            # 无法预判;仅 PARAMETER/STATIC_VALUE 可预判)。
            pk_value: Any = None
            for vs in rule.properties.values():
                if vs.source in ("PARAMETER", "STATIC_VALUE") and vs.value is not None:
                    pk_value = parameters.get(vs.value) if vs.source == "PARAMETER" else vs.value
                    if pk_value is not None:
                        break
            if pk_value is None:
                return None
            return (obj_type, str(pk_value))
        if rule.type in ("ModifyObject", "UpsertObject", "DeleteObject"):
            target_param = rule.target_parameter
            if not target_param or not obj_type:
                return None
            pk_value = parameters.get(target_param)
            if isinstance(pk_value, dict):
                pk_value = ActionService._extract_pk_from_dict(pk_value)
            if pk_value is None:
                return None
            return (obj_type, str(pk_value))
        return None

    async def _build_outbox_effect(
        self,
        effect: ActionEffectConfig,
        mutations: list[dict[str, Any]],
        ontology_api_name: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """构造 outbox effect_config + payload。

        write_back effect(ADR §3.9):从 ObjectType.backing_mapping 推导
        table/primary_key,匹配对应 mutation 的最终 properties 作 changes。
        其它 effect 保持原 config + 标准 payload。
        """
        action_id_prefix = {"action_id": "", "action": "", "mutations": mutations}
        if effect.type != "write_back":
            return effect.config, {"object_type": "", **action_id_prefix}
        # write_back: 优先用 WriteBackEffectConfig(target_object_type + op)。
        from ontology.core.schemas.action import WriteBackEffectConfig

        try:
            wb = WriteBackEffectConfig(**effect.config)
        except Exception:
            # 旧式 write_back config(jdbc_url/table/primary_key/changes) → 原样透传。
            return effect.config, {"object_type": "", **action_id_prefix}
        # 从 ObjectType.backing_mapping 推导 table + primary_key 列名。
        ot = await self._metadata.get_object_type(ontology_api_name, wb.target_object_type)
        table_name = ot.api_name  # 回退
        primary_key_col = ot.primary_key
        # 取首个物理表的表名 + 主键列的物理列名。
        for prop in ot.properties:
            if prop.backing_mapping and prop.backing_mapping.backing_table:
                table_name = prop.backing_mapping.backing_table
                if prop.api_name == ot.primary_key:
                    primary_key_col = prop.backing_mapping.backing_column or ot.primary_key
                break
        # 匹配该 object_type 的 mutation 的最终 properties 作 changes。
        changes: dict[str, Any] = {}
        for mut in mutations:
            if mut.get("object_type") == wb.target_object_type or (
                not mut.get("object_type") and wb.target_object_type == ot.api_name
            ):
                props = mut.get("properties", {})
                # 把属性 api_name 映射到物理列名。
                for prop in ot.properties:
                    if prop.api_name in props:
                        col = prop.backing_mapping.backing_column if prop.backing_mapping else prop.api_name
                        changes[col] = props[prop.api_name]
                break
        outbox_config = {
            **effect.config,
            "table": table_name,
            "primary_key": primary_key_col,
            "op": wb.op,
        }
        # ADR §3.9: 从 ObjectType.backing_mapping.backing_catalog 反查 datasource,
        # 拼 jdbc_url(含 credential)。未找到 datasource 时 jdbc_url 留空,
        # OutboxExecutor 会报错(可由调用方在 config 预填 jdbc_url 覆盖)。
        if not outbox_config.get("jdbc_url"):
            jdbc_url = await self._derive_jdbc_url(ot)
            if jdbc_url:
                outbox_config["jdbc_url"] = jdbc_url
        payload = {"object_type": wb.target_object_type, "changes": changes, **action_id_prefix}
        return outbox_config, payload

    async def _derive_jdbc_url(self, ot: ObjectType) -> str | None:
        """从 ObjectType 的 backing_mapping.backing_catalog 反查 datasource,
        拼 jdbc_url(mysql://user:pass@host:port/db)。返回 None 表示未找到。
        """
        # 取首个属性的 backing_mapping.backing_catalog 作 datasource api_name。
        catalog_name: str | None = None
        for prop in ot.properties:
            if prop.backing_mapping and prop.backing_mapping.backing_catalog:
                catalog_name = prop.backing_mapping.backing_catalog
                break
        if not catalog_name:
            return None
        try:
            ds = await self._metadata.get_datasource(catalog_name)
        except Exception:
            return None
        cfg = ds.connector_config or {}
        host = cfg.get("host", "localhost")
        port = cfg.get("port", 3306)
        database = cfg.get("database", "")
        # 查 credential 拿 user/password。
        user = cfg.get("username", "root")
        password = cfg.get("password", "")
        if ds.credential_id:
            try:
                cred = await self._metadata.get_credential_by_id(ds.credential_id)
                secret = cred.secret_data or {}
                user = secret.get("username", user)
                password = secret.get("password", password)
            except Exception:
                pass
        scheme = "mysql" if ds.connector_type.lower() in ("mysql", "mariadb") else ds.connector_type.lower()
        auth = f"{user}:{password}@" if password else (f"{user}@" if user else "")
        return f"{scheme}://{auth}{host}:{port}/{database}"

    # ── Action 同步链路 outbox (action-sync-outbox-design.md §8.3) ──

    async def _create_sync_outbox_records(
        self,
        *,
        execution_id: str,
        mutations: list[dict[str, Any]],
        ontology_api_name: str,
        object_type_api_name: str,
        affected_objects: dict[str, int],
        raw_before_states: dict[str, dict[str, Any] | None],
        raw_after_states: dict[str, dict[str, Any] | None],
    ) -> None:
        """为每个 CREATE/UPDATE/DELETE mutation 追加 INDEX + ARCHIVE outbox 记录。

        action-sync-outbox-design.md §3.1/§8.3:
        - INDEX (→Doris 近实时): OutboxExecutor 1s 轮询消费, 按 mutation_type
          分流 CREATE/UPDATE→upsert / DELETE→delete_by_ids。
        - ARCHIVE (→Iceberg 微批): SyncFlushScheduler 5min/1000 条微批, 走
          IcebergStore.merge (MERGE INTO, 按业务 PK 覆盖)。
        两者复用同一 outbox 表, 靠 effect_type 隔离消费方。RELATE/UNRELATE/
        CLEAR_LINKS 跳过 (关系不同步, design §3.5)。

        outbox payload (INDEX/ARCHIVE 共用):
        - rid: Gaia 内部 UUID (object_state PK)
        - object_type_api_name: flusher 据此查 ObjectType 拿 primary_key
          api_name → PropertyDef backing_column (MERGE/DELETE 的 PK 列名)
        - ontology_api_name: 分桶键
        - version: object_state 新版本号
        - mutation_type: CREATE_OBJECT/UPDATE_OBJECT/UPDATE_PROPERTY/DELETE_OBJECT
        - properties: 全量快照 (backing_column key); DELETE 时只需 PK 列

        事务原子性 (transaction-management-best-practices.md §5.2 + design §3.6):
        本方法在 Action 主事务内 (Step 9, commit 前), outbox 与 object_state
        必须原子提交。故**不吞异常** — 任一 outbox 写入失败 → raise → 阻止
        commit_transaction() → object_state 也回滚 (session close 隐式 rollback),
        保证"outbox 记录存在 ⟺ object_state 已提交"的不变式。outbox 是派生链路,
        但其与 object_state 的原子性是 outbox 模式的核心契约, 不能为"不阻塞
        Action"而破坏 (那会导致幽灵数据: object_state 提交了但 Doris/Iceberg
        永远收不到同步)。
        """
        # B5: lite 桌面版无 Doris/Iceberg，不产 INDEX/ARCHIVE/EMBEDDING outbox
        # （A4 已砍 OutboxExecutor/SyncFlushScheduler 后台消费，产了也永远 PENDING
        # 堆积）。object_state 写 SQLite（B1 OCC 已适配）+ execution_log + 用户 effect
        # outbox（WEBHOOK/NOTIFICATION/SUB_ACTION）仍正常产生。
        if settings.edition == "lite":
            return
        for mutation in mutations:
            mut_type = mutation["type"]
            if mut_type not in ("CREATE_OBJECT", "UPDATE_OBJECT", "UPDATE_PROPERTY", "DELETE_OBJECT"):
                continue  # RELATE/UNRELATE/CLEAR_LINKS 跳过
            obj_id = mutation["rid"]
            mut_obj_type = mutation.get("object_type") or object_type_api_name
            # VIRTUAL 目标已在 Step 5b 拒绝, 这里双重保险: VIRTUAL 不落
            # Doris idx / Iceberg 业务表 (design §3.5, 架构红线 9)。
            ot = await self._resolve_ot_cached(ontology_api_name, mut_obj_type)
            if ot is not None and ot.storage_type == "VIRTUAL":
                continue
            version = affected_objects.get(obj_id, 0)
            is_delete = mut_type == "DELETE_OBJECT"
            # payload.properties: backing_column key (直接作 Doris/Iceberg 列)。
            # CREATE/UPDATE → 全量后态; DELETE → 前态里取 PK (删后无后态)。
            if is_delete:
                raw = raw_before_states.get(obj_id)
                props = (raw or {}).get("properties", {}) if raw else {}
            else:
                raw = raw_after_states.get(obj_id)
                props = (raw or {}).get("properties", {}) if raw else {}
            payload = {
                "rid": obj_id,
                "object_type_api_name": mut_obj_type,
                "ontology_api_name": ontology_api_name,
                "version": version,
                "mutation_type": mut_type,
                "properties": props,
            }
            # INDEX 不分桶 (逐条近实时, target_ontology=None);
            # ARCHIVE 按 ontology 分桶 (SyncFlushScheduler 微批拉取键)。
            # create_outbox_record 是 auto_commit=False (只 add), 失败时 raise
            # 让 Action 主事务回滚 (见 docstring 事务原子性说明)。
            await self._metadata.create_outbox_record(
                action_execution_id=execution_id,
                effect_type="INDEX",
                effect_config={},
                payload=payload,
                target_ontology=None,
            )
            await self._metadata.create_outbox_record(
                action_execution_id=execution_id,
                effect_type="ARCHIVE",
                effect_config={},
                payload=payload,
                target_ontology=ontology_api_name,
            )
            # §14.4 语义检索: CREATE/UPDATE 且 OT 有 VECTOR 属性时, 追加 EMBEDDING
            # outbox (异步调 EmbeddingProvider → Doris embedding 列)。
            # DELETE 不需要 (行已删, embedding 随之消失)。RELATE/UNRELATE 跳过
            # (已在上面 continue)。best-effort: VECTOR 属性查询失败不阻塞主链路
            # (同 INDEX/ARCHIVE 的 fail-tolerant 策略一致)。
            if not is_delete and ot is not None:
                try:
                    vector_props = [
                        p
                        for p in ot.properties
                        if str(getattr(p.data_type, "value", p.data_type)).upper() == "VECTOR"
                        and getattr(p, "vector_config", None) is not None
                        and p.vector_config.source_expression  # 需有源表达式
                    ]
                except Exception:
                    vector_props = []
                for vp in vector_props:
                    emb_col_base = vp.backing_mapping.backing_column if vp.backing_mapping else vp.api_name
                    emb_payload = {
                        **payload,
                        "vector_property_api_name": vp.api_name,
                        "source_expression": vp.vector_config.source_expression,
                        "embedding_column": f"{emb_col_base}_embedding",
                    }
                    await self._metadata.create_outbox_record(
                        action_execution_id=execution_id,
                        effect_type="EMBEDDING",
                        effect_config={},
                        payload=emb_payload,
                        target_ontology=None,
                    )

    async def _project_link_mutations(
        self,
        ontology_api_name: str,
        mutations: list[dict[str, Any]],
        ontology_id: str,
    ) -> None:
        """ADR-015 §capabilities: 投影 RELATE/UNRELATE 边到 Neo4j (Step 11).

        节点投影由 OutboxExecutor INDEX effect 处理 (用 outbox payload);
        边投影没有 outbox, 在 commit 后直接调。fail-tolerant: 失败不影响
        Action 结果 (已 commit), 只记日志。

        受 capabilities.graph_indexing_enabled 门控 (Gate 4): 只有源 ObjectType
        显式启用了图索引才投影边。
        """
        if self._graph_projector is None:
            return

        # 收集所有 RELATE/UNRELATE mutation
        link_muts = [m for m in mutations if m["type"] in ("RELATE", "UNRELATE")]
        if not link_muts:
            return

        # 查 LinkType 元数据: link_type_api_name → (source_ot_api, target_ot_api)
        try:
            link_types = await self._metadata.get_link_types(ontology_api_name)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("project_link: failed to load link types: %s", exc)
            return
        link_map: dict[str, tuple[str, str]] = {}
        ots = await self._metadata.list_object_types(ontology_api_name)
        ot_id_to_api = {ot.id: ot.api_name for ot in ots}
        for lt in link_types:
            source_api = ot_id_to_api.get(lt.source_object_type_id, "")
            target_api = ot_id_to_api.get(lt.target_object_type_id, "")
            if source_api and target_api:
                link_map[lt.api_name] = (source_api, target_api)

        for mut in link_muts:
            link_type = mut.get("link_type_api_name", "")
            source_rid = str(mut.get("rid", ""))
            target_rid = str(mut.get("target_rid", ""))
            if not (link_type and source_rid and target_rid):
                continue

            ot_pair = link_map.get(link_type)
            if ot_pair is None:
                self._logger.warning(
                    "project_link: link type '%s' not found in ontology '%s', skip",
                    link_type,
                    ontology_api_name,
                )
                continue
            source_ot_api, target_ot_api = ot_pair

            # Gate 4: 检查源 ObjectType 的 graph_indexing_enabled
            try:
                source_ot = await self._resolve_ot_cached(ontology_api_name, source_ot_api)
            except Exception:  # noqa: BLE001
                source_ot = None
            if source_ot is None or not source_ot.capabilities.graph_indexing_enabled:
                continue

            try:
                if mut["type"] == "RELATE":
                    await self._graph_projector.project_link(
                        ontology_api_name=ontology_api_name,
                        link_type_api_name=link_type,
                        source_object_type_api_name=source_ot_api,
                        source_rid=source_rid,
                        target_object_type_api_name=target_ot_api,
                        target_rid=target_rid,
                    )
                elif mut["type"] == "UNRELATE":
                    await self._graph_projector.delete_link(
                        ontology_api_name=ontology_api_name,
                        link_type_api_name=link_type,
                        source_object_type_api_name=source_ot_api,
                        source_rid=source_rid,
                        target_object_type_api_name=target_ot_api,
                        target_rid=target_rid,
                    )
            except Exception as exc:  # noqa: BLE001 — fail-tolerant
                self._logger.warning(
                    "project_link %s %s→%s failed: %s",
                    mut["type"],
                    link_type,
                    target_rid,
                    exc,
                )


# ── Invalid combination 判定 (模块级纯函数, 便于单测) ──

# 对齐 Palantir rules 文档 "Invalid combinations":
#   - Objects cannot be deleted before they are added or modified.
#   - Objects cannot be modified before they are added.
#   - Objects cannot be created twice in one form submission.
# 同对象多条 ModifyObject/UpsertObject **不是** invalid (由 _compile_mutations 合并)。
# 返回 True 表示该 (prev_op, op) 序列为非法组合。
_INVALID_COMBINATIONS: set[tuple[str, str]] = {
    # (prev_op, op) → 非法
    # delete before add/modify (先删后建/先删后改)
    ("DeleteObject", "CreateObject"),
    ("DeleteObject", "ModifyObject"),
    ("DeleteObject", "UpsertObject"),
    # modify before add (先改后建) — Gaia 有 hydrate 兜底不会 crash,
    # 但 Palantir 语义明确禁止, 保持一致避免依赖时序的脆弱行为。
    ("ModifyObject", "CreateObject"),
    ("UpsertObject", "CreateObject"),
    # create twice (重复创建)
    ("CreateObject", "CreateObject"),
    # create then modify/delete — Palantir: 新建对象不可被同 Action 后续规则引用修改/删除
    # (尚无有效主键)。Gaia 主键运行时生成, 仍按 Palantir 语义拒绝。
    ("CreateObject", "ModifyObject"),
    ("CreateObject", "UpsertObject"),
    ("CreateObject", "DeleteObject"),
}


def _is_invalid_combination(prev_op: str, op: str) -> bool:
    """判断 (prev_op, op) 是否为 Palantir invalid combination。"""
    return (prev_op, op) in _INVALID_COMBINATIONS


def _invalid_combination_reason(prev_op: str, op: str) -> str:
    """返回 invalid combination 的人类可读原因 (用于错误消息)。"""
    if prev_op == "DeleteObject":
        return f"{op} an object after deleting it"
    if op == "CreateObject" and prev_op in ("ModifyObject", "UpsertObject"):
        return "create an object after modifying it"
    if prev_op == "CreateObject" and op == "CreateObject":
        return "create the same object twice"
    if prev_op == "CreateObject":
        return f"{op} an object created earlier in the same action"
    return f"perform {op} after {prev_op} on the same object"
