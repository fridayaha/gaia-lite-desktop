"""PostgresMetaStore — business ontology metadata persistence layer.

All domain entities are stored in PostgreSQL via SQLAlchemy 2.0 async ORM.
No raw SQL strings — all queries use select() / insert() style.

This is the authoritative source for Ontology, ObjectType, PropertyDef,
LinkType, ActionType, and all other business metadata entities.

Transaction model:
- Standard CRUD methods auto-commit via _flush_and_commit()
- Action execution methods (create_execution_log, upsert_object_state,
  create_outbox_record) do NOT auto-commit — the caller (ActionService)
  manages the transaction for atomic multi-operation commits.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

# 跨方言说明（B1）：`postgresql.insert(...).on_conflict_do_nothing(...)`、
# `.with_for_update(skip_locked=True)`、`update().returning(...)`、以及 JSONB
# `.properties[key].as_string()` 经实测在 SQLite 上原样工作——PG 与 SQLite 都支持
# `ON CONFLICT DO NOTHING` 语法，SQLite 3.35+ 支持 `RETURNING`，SQLAlchemy 把 JSON
# 访问自动方言化为 `json_extract`，`with_for_update` 在 SQLite 上被静默忽略（单进程
# 桌面版无需行锁）。故 lite 桌面版复用本 MetaStore 无需 dialect dispatch，也不要改成
# `sqlite.insert`——那只会增加间接而无功能收益，且可能破坏现有 2124 单测（本就跑在
# SQLite db_session fixture 上）。新增方法若用到 PG-only 构造，先在 SQLite 上验证。
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ontology.core.exceptions import ConflictError, NotFoundError, OntologyError, ValidationError
from ontology.core.models.datasource import (
    CredentialModel,
    DatasetGovernanceModel,
    DataSourceModel,
    SyncTaskModel,
)
from ontology.core.models.defaults import new_uuid, utcnow
from ontology.core.models.ontology import (
    ActionExecutionLogModel,
    ActionTypeModel,
    ActionTypeVersionModel,
    BranchModel,
    InterfaceTypeModel,
    LinkTypeModel,
    ObjectLinkModel,
    ObjectStateModel,
    ObjectTypeGroupModel,
    ObjectTypeInterfaceModel,
    ObjectTypeModel,
    OntologyModel,
    OutboxModel,
    PropertyDefModel,
    SharedPropertyModel,
    StructModel,
    ValueTypeModel,
)
from ontology.core.schemas.datasource import (
    Credential,
    CredentialCreate,
    DatasetGovernance,
    DatasetGovernanceCreate,
    DataSource,
    DataSourceCreate,
    SyncTask,
    SyncTaskCreate,
)
from ontology.core.schemas.ontology import (
    ActionType,
    BackingColumnRef,
    Branch,
    InterfaceType,
    LinkTypeDef,
    ObjectType,
    ObjectTypeCapabilities,
    ObjectTypeGroup,
    Ontology,
    PropertyDef,
    SharedProperty,
    Struct,
    ValueType,
)
from ontology.core.schemas.permission import Principal, ResourceOwnership

if TYPE_CHECKING:
    from ontology.core.models.permission import (
        AccessRequestModel,
        GroupModel,
        MarkingCategoryModel,
        MarkingModel,
        OrganizationModel,
        ProjectModel,
        RoleAssignmentModel,
        RowSecurityPolicyModel,
        SpaceModel,
        UserModel,
    )


def _json_safe(obj: Any) -> Any:
    """Recursively convert a value to a JSON-serializable structure.

    PG JSON columns (via SQLAlchemy) serialize with the stdlib json module,
    which can't handle datetime/Decimal/UUID. Object-state snapshots read
    from the DB carry datetimes, so before_snapshot/after_snapshot must be
    normalized before INSERT or the commit fails with
    "Object of type datetime is not JSON serializable".
    """
    import json

    return json.loads(json.dumps(obj, default=str))


class PostgresMetaStore:
    """Business ontology metadata persistence layer.

    All queries use SQLAlchemy 2.0 async ORM. Each public method maps
    to a single domain entity operation.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def close(self) -> None:
        """Close the underlying session and return connection to pool."""
        await self._session.close()

    @property
    def session(self) -> AsyncSession:
        """DEPRECATED: direct session access leaks the Layer boundary.

        Services should call PostgresMetaStore methods, not operate on the
        session directly (architecture §1: layers do not call each other;
        services orchestrate via layer methods). Retained temporarily for
        the batch-atomic paths (define_object_type_batch / update_object_type /
        ConflictDetector audit) that have not yet been migrated to dedicated
        layer methods — see V1 follow-up. Accessing this property logs a
        deprecation warning.
        """
        import warnings

        warnings.warn(
            "PostgresMetaStore.session is deprecated; use a dedicated layer method instead (architecture V1).",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._session

    # ── Ontology ──

    async def create_ontology(self, ontology: Ontology) -> Ontology:
        """Create a new Ontology."""
        model = OntologyModel(
            id=new_uuid(),
            api_name=ontology.api_name,
            display_name=ontology.display_name,
            description=ontology.description,
            rid=ontology.rid,
            status="ACTIVE",
            deleted_at=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return Ontology.model_validate(model)

    async def get_ontology(self, api_name: str, *, include_non_active: bool = False) -> Ontology:
        """Get an Ontology by api_name, raising NotFoundError if missing.

        v5.2: by default a soft-deleted or DEPRECATED ontology is treated as
        not found (404) — this is the MCP/REST default so external agents and
        the UI don't see tombstoned resources. Pass ``include_non_active=True``
        for admin/restore flows that need to see them (design §八.3).
        """
        stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Ontology", api_name)
        if not include_non_active and (model.deleted_at is not None or model.status == "DEPRECATED"):
            raise NotFoundError("Ontology", api_name)
        return Ontology.model_validate(model)

    async def list_ontologies(
        self, *, include_non_active: bool = False, include_deprecated: bool = False
    ) -> list[Ontology]:
        """List all Ontologies.

        Visibility tiers (design §八):
          - default: only ACTIVE, non-soft-deleted.
          - ``include_deprecated=True``: also show DEPRECATED (greyed-out in the
            sidebar), but still hide soft-deleted. Use this for the sidebar's
            default view so users can find/restore deprecated ontologies.
          - ``include_non_active=True``: show everything (DEPRECATED + soft-
            deleted) — the admin/recycle-bin view.
        """
        stmt = select(OntologyModel).order_by(OntologyModel.created_at)
        if not include_non_active:
            # Always hide soft-deleted unless the admin view is requested.
            stmt = stmt.where(OntologyModel.deleted_at.is_(None))
            if not include_deprecated:
                # Sidebar default hides DEPRECATED; include_deprecated shows them.
                stmt = stmt.where(OntologyModel.status != "DEPRECATED")
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [Ontology.model_validate(m) for m in models]

    async def get_ontology_api_names_by_ids(
        self, ontology_ids: list[str]
    ) -> dict[str, str]:
        """Batch-resolve ontology_id → api_name.

        Used by services that receive ObjectType models (which carry
        ``ontology_id``) but need the api_name for routing/projection
        (e.g. ObjectIndexFunnel.project_for_dataset). Avoids N+1 lookups
        and avoids callers reaching into ``_session`` directly.
        """
        if not ontology_ids:
            return {}
        stmt = select(OntologyModel.id, OntologyModel.api_name).where(
            OntologyModel.id.in_(ontology_ids)
        )
        result = await self._session.execute(stmt)
        return {str(row[0]): str(row[1]) for row in result.fetchall()}

    async def list_ontologies_with_counts(
        self, *, include_non_active: bool = False, include_deprecated: bool = False
    ) -> list[tuple[OntologyModel, int]]:
        """List all Ontologies with their ObjectType counts.

        Returns (model, object_types_count) tuples so the caller can build
        the response schema without touching the session.

        Visibility tiers — see :meth:`list_ontologies`.
        """
        stmt = (
            select(
                OntologyModel,
                func.count(ObjectTypeModel.id.distinct()).label("object_types_count"),
            )
            .outerjoin(ObjectTypeModel, ObjectTypeModel.ontology_id == OntologyModel.id)
            .group_by(OntologyModel.id)
            .order_by(OntologyModel.created_at)
        )
        if not include_non_active:
            stmt = stmt.where(OntologyModel.deleted_at.is_(None))
            if not include_deprecated:
                stmt = stmt.where(OntologyModel.status != "DEPRECATED")
        result = await self._session.execute(stmt)
        return [(row[0], row.object_types_count) for row in result.all()]

    async def update_ontology(
        self,
        api_name: str,
        display_name: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> Ontology:
        """Update an existing Ontology (partial update).

        v5.2: ``status`` is mutable (ACTIVE → DEPRECATED) as the Deprecate
        precondition for soft-delete (design §5.5).
        """
        stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Ontology", api_name)
        if display_name is not None:
            model.display_name = display_name
        if description is not None:
            model.description = description
        if status is not None:
            model.status = status
        model.updated_at = utcnow()
        await self._flush_and_commit()
        return Ontology.model_validate(model)

    async def delete_ontology(self, api_name: str) -> None:
        """Soft-delete an Ontology and cascade-mark all its children.

        v5.2: sets ``deleted_at`` on the ontology row AND on every
        ObjectType / LinkType / ActionType under it (single transaction),
        so a later restore recovers the whole subtree atomically. The PG
        rows are NOT removed — the cleanup script reaps them after the
        cooldown window (design §五.3, §七). ``datasets`` rows are untouched
        (decision 10: Dataset is independent).

        Caller MUST have already Deprecate'd the ontology (status=DEPRECATED);
        the service layer enforces that precondition.
        """
        now = utcnow()
        stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Ontology", api_name)

        # Cascade-mark children (deleted_at). Properties inherit via their
        # ObjectType's deleted_at and are not touched individually here —
        # they ride along when the ObjectType is restored/reaped.
        await self._session.execute(
            update(ObjectTypeModel)
            .where(
                ObjectTypeModel.ontology_id == model.id,
                ObjectTypeModel.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        await self._session.execute(
            update(LinkTypeModel)
            .where(
                LinkTypeModel.ontology_id == model.id,
                LinkTypeModel.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        await self._session.execute(
            update(ActionTypeModel)
            .where(
                ActionTypeModel.ontology_id == model.id,
                ActionTypeModel.deleted_at.is_(None),
            )
            .values(deleted_at=now)
        )
        # Mark the ontology itself.
        model.deleted_at = now
        await self._flush_and_commit()

    async def restore_ontology(self, api_name: str) -> Ontology:
        """Reverse a soft-delete: clear ``deleted_at`` on the ontology + children.

        v5.2 (design §七.3): restores PG metadata only. Physical resources
        (Doris idx tables, INDEX pipelines) were dropped at delete time and
        are NOT re-provisioned — the caller/UI must signal that a re-sync is
        needed. ``status`` stays DEPRECATED; flipping back to ACTIVE is a
        separate explicit PATCH (so a restore isn't silently re-activated).
        """
        stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None or model.deleted_at is None:
            raise NotFoundError("Ontology (soft-deleted)", api_name)

        # Clear children first, then the ontology itself.
        await self._session.execute(
            update(ObjectTypeModel).where(ObjectTypeModel.ontology_id == model.id).values(deleted_at=None)
        )
        await self._session.execute(
            update(LinkTypeModel).where(LinkTypeModel.ontology_id == model.id).values(deleted_at=None)
        )
        await self._session.execute(
            update(ActionTypeModel).where(ActionTypeModel.ontology_id == model.id).values(deleted_at=None)
        )
        model.deleted_at = None
        await self._flush_and_commit()
        return Ontology.model_validate(model)

    async def get_ontology_impact(self, api_name: str) -> dict[str, Any]:
        """Build the cascade-impact report for ``GET /ontologies/{api_name}/impact``.

        v5.2 (design §六.2): counts every child resource type + object/link
        instances that the ontology owns, in a single round of cheap COUNT
        queries. The service layer wraps this into an ImpactReport and decides
        ``can_delete`` based on status.
        """
        stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Ontology", api_name)

        ont_id = model.id
        # Count active (non-deleted) children + instance tables. Counts are
        # raw ints; the service formats the labels.
        ot_count = await self._session.scalar(
            select(func.count())
            .select_from(ObjectTypeModel)
            .where(ObjectTypeModel.ontology_id == ont_id, ObjectTypeModel.deleted_at.is_(None))
        )
        prop_count = await self._session.scalar(
            select(func.count())
            .select_from(PropertyDefModel)
            .join(ObjectTypeModel, PropertyDefModel.object_type_id == ObjectTypeModel.id)
            .where(ObjectTypeModel.ontology_id == ont_id, PropertyDefModel.deleted_at.is_(None))
        )
        link_count = await self._session.scalar(
            select(func.count())
            .select_from(LinkTypeModel)
            .where(LinkTypeModel.ontology_id == ont_id, LinkTypeModel.deleted_at.is_(None))
        )
        action_count = await self._session.scalar(
            select(func.count())
            .select_from(ActionTypeModel)
            .where(ActionTypeModel.ontology_id == ont_id, ActionTypeModel.deleted_at.is_(None))
        )
        obj_instance_count = await self._session.scalar(
            select(func.count()).select_from(ObjectStateModel).where(ObjectStateModel.ontology_id == ont_id)
        )
        link_instance_count = await self._session.scalar(
            select(func.count()).select_from(ObjectLinkModel).where(ObjectLinkModel.ontology_id == ont_id)
        )
        managed_ot_count = await self._session.scalar(
            select(func.count())
            .select_from(ObjectTypeModel)
            .where(
                ObjectTypeModel.ontology_id == ont_id,
                ObjectTypeModel.deleted_at.is_(None),
                ObjectTypeModel.storage_type == "MANAGED",
            )
        )
        return {
            "api_name": api_name,
            "status": model.status,
            "deleted_at": model.deleted_at,
            "object_type_count": int(ot_count or 0),
            "property_count": int(prop_count or 0),
            "link_type_count": int(link_count or 0),
            "action_type_count": int(action_count or 0),
            "object_instance_count": int(obj_instance_count or 0),
            "link_instance_count": int(link_instance_count or 0),
            "managed_object_type_count": int(managed_ot_count or 0),
        }

    # ── ObjectType ──

    async def create_object_type(self, object_type: ObjectType) -> ObjectType:
        """Create a new ObjectType under an Ontology."""
        model = ObjectTypeModel(
            id=new_uuid(),
            ontology_id=object_type.ontology_id,
            api_name=object_type.api_name,
            display_name=object_type.display_name,
            description=object_type.description,
            primary_key=object_type.primary_key,
            title_property=object_type.title_property,
            storage_type=object_type.storage_type,
            visibility=object_type.visibility,
            status=object_type.status,
            project_id=object_type.project_id or "00000000000000000000000000000001",
            capabilities=object_type.capabilities.model_dump(),
            deleted_at=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return ObjectType(
            id=model.id,
            ontology_id=model.ontology_id,
            api_name=model.api_name,
            display_name=model.display_name,
            description=model.description or "",
            primary_key=model.primary_key,
            title_property=model.title_property,
            storage_type=cast(Literal["MANAGED", "VIRTUAL"], model.storage_type),
            visibility=cast(Literal["NORMAL", "PROMINENT", "HIDDEN"], model.visibility),
            status=cast(Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"], model.status),
            deleted_at=None,
            capabilities=ObjectTypeCapabilities.model_validate(model.capabilities or {}),
            properties=[],
            links=[],
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def get_object_type(
        self, ontology_api_name: str, api_name: str, *, include_non_active: bool = False
    ) -> ObjectType:
        """Get an ObjectType by Ontology api_name + ObjectType api_name.

        v5.2: a soft-deleted ObjectType is NotFound by default; pass
        ``include_non_active=True`` to see it (design §八).
        """
        onto_stmt = select(OntologyModel).where(OntologyModel.api_name == ontology_api_name)
        onto_result = await self._session.execute(onto_stmt)
        onto = onto_result.scalar_one_or_none()
        if onto is None:
            raise NotFoundError("Ontology", ontology_api_name)

        stmt = (
            select(ObjectTypeModel)
            .where(
                ObjectTypeModel.ontology_id == onto.id,
                ObjectTypeModel.api_name == api_name,
            )
            .options(selectinload(ObjectTypeModel.properties))
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"ObjectType in ontology {ontology_api_name}", api_name)
        if not include_non_active and (model.deleted_at is not None or model.status == "DEPRECATED"):
            raise NotFoundError(f"ObjectType in ontology {ontology_api_name}", api_name)
        return ObjectType.model_validate(model)

    async def get_object_type_by_api_name(self, ontology_id: str, api_name: str) -> ObjectType:
        """Get an ObjectType by ontology UUID + api_name (no ontology lookup needed)."""
        stmt = (
            select(ObjectTypeModel)
            .where(
                ObjectTypeModel.ontology_id == ontology_id,
                ObjectTypeModel.api_name == api_name,
            )
            .options(selectinload(ObjectTypeModel.properties))
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("ObjectType", api_name)
        return ObjectType.model_validate(model)

    async def list_object_types(self, ontology_api_name: str, *, include_non_active: bool = False) -> list[ObjectType]:
        """List all ObjectTypes within an Ontology.

        v5.2: excludes soft-deleted and DEPRECATED ObjectTypes unless
        ``include_non_active=True`` (design §八). Note: EXPERIMENTAL
        ObjectTypes are NOT excluded (decision 13).
        """
        onto_stmt = select(OntologyModel).where(OntologyModel.api_name == ontology_api_name)
        onto_result = await self._session.execute(onto_stmt)
        onto = onto_result.scalar_one_or_none()
        if onto is None:
            raise NotFoundError("Ontology", ontology_api_name)

        stmt = (
            select(ObjectTypeModel)
            .where(ObjectTypeModel.ontology_id == onto.id)
            .options(selectinload(ObjectTypeModel.properties))
            .order_by(ObjectTypeModel.created_at)
        )
        if not include_non_active:
            stmt = stmt.where(
                ObjectTypeModel.deleted_at.is_(None),
                ObjectTypeModel.status != "DEPRECATED",
            )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [ObjectType.model_validate(m) for m in models]

    async def get_virtual_object_types_by_dataset(
        self, dataset_api_name: str
    ) -> list[tuple[str, str]]:
        """查绑定某 dataset 的 VIRTUAL ObjectType（ADR-021 §3.1 触发链路用）。

        register_virtual_table 后异步查：哪些 VIRTUAL ObjectType 的属性引用了
        该 dataset（PropertyDefModel.backing_dataset_api_name）。返回
        (ontology_api_name, object_type_api_name) 元组列表，供投影触发器逐个
        调 project_for_virtual_object_type。

        dataset → OT 映射通过 PropertyDefModel 反查（OT 本身不存 dataset 字段，
        绑定关系在属性级的 backing_dataset_api_name 上）。
        """
        stmt = (
            select(OntologyModel.api_name, ObjectTypeModel.api_name)
            .join(ObjectTypeModel, ObjectTypeModel.ontology_id == OntologyModel.id)
            .join(
                PropertyDefModel,
                PropertyDefModel.object_type_id == ObjectTypeModel.id,
            )
            .where(
                PropertyDefModel.backing_dataset_api_name == dataset_api_name,
                ObjectTypeModel.storage_type == "VIRTUAL",
                ObjectTypeModel.deleted_at.is_(None),
                ObjectTypeModel.status != "DEPRECATED",
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def list_object_type_summaries(self, ontology_id: str) -> list[tuple[ObjectTypeModel, int, int, int]]:
        """List ObjectType summaries (model, properties_count, links_count, actions_count).

        Lightweight aggregation for sidebar/table/canvas views — avoids
        loading full property/link/action details.
        """
        stmt = (
            select(
                ObjectTypeModel,
                func.count(PropertyDefModel.id.distinct()).label("properties_count"),
                func.count(LinkTypeModel.id.distinct()).label("links_count"),
                func.count(ActionTypeModel.id.distinct()).label("actions_count"),
            )
            .outerjoin(PropertyDefModel, PropertyDefModel.object_type_id == ObjectTypeModel.id)
            .outerjoin(
                LinkTypeModel,
                (LinkTypeModel.source_object_type_id == ObjectTypeModel.id)
                | (LinkTypeModel.target_object_type_id == ObjectTypeModel.id),
            )
            .outerjoin(
                ActionTypeModel,
                ActionTypeModel.affected_object_type_id == ObjectTypeModel.id,
            )
            .where(ObjectTypeModel.ontology_id == ontology_id)
            .group_by(ObjectTypeModel.id)
            .order_by(ObjectTypeModel.display_name)
        )
        result = await self._session.execute(stmt)
        return [(row[0], row.properties_count, row.links_count, row.actions_count) for row in result.all()]

    async def update_object_type(self, id: str, updates: dict[str, Any]) -> ObjectType:
        """Update an ObjectType by ID (partial update)."""
        stmt = select(ObjectTypeModel).where(ObjectTypeModel.id == id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("ObjectType", id)

        allowed_fields = {
            "display_name",
            "description",
            "visibility",
            "status",
            "title_property",
            "backing_dataset_api_name",
        }
        for key, value in updates.items():
            if key in allowed_fields:
                setattr(model, key, value)
        # Capabilities update: accepts ObjectTypeCapabilities or dict.
        if "capabilities" in updates:
            caps = updates["capabilities"]
            if isinstance(caps, ObjectTypeCapabilities):
                model.capabilities = caps.model_dump()
            elif isinstance(caps, dict):
                model.capabilities = ObjectTypeCapabilities.model_validate(caps).model_dump()
            else:
                raise ValidationError(
                    f"capabilities must be ObjectTypeCapabilities or dict, got {type(caps)}"
                )
        model.updated_at = utcnow()
        await self._flush_and_commit()
        return ObjectType.model_validate(model)

    async def set_object_type_backing_dataset(
        self, object_type_id: str, dataset_api_name: str | None
    ) -> None:
        """Set or clear the OT-level primary backing dataset (Palantir "backing
        datasource" convenience ref).

        Unlike ``update_object_type``, this writes a single field and does NOT
        return a refreshed ``ObjectType`` — avoiding the ``model_validate(orm)``
        path that triggers ``properties`` lazy-load (MissingGreenlet) after
        commit (error pattern #2). Callers that need the refreshed object
        should re-fetch via ``get_object_type`` (which ``selectinload``s
        properties in a fresh session).
        """
        stmt = select(ObjectTypeModel).where(ObjectTypeModel.id == object_type_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("ObjectType", object_type_id)
        model.backing_dataset_api_name = dataset_api_name
        model.updated_at = utcnow()
        await self._flush_and_commit()

    async def delete_object_type(self, id: str) -> None:
        """Delete an ObjectType by ID (cascades to properties/links via ORM)."""
        stmt = select(ObjectTypeModel).where(ObjectTypeModel.id == id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("ObjectType", id)
        await self._session.delete(model)
        await self._flush_and_commit()

    # ── Property ──

    async def add_property(self, object_type_id: str, prop: PropertyDef) -> PropertyDef:
        """Add a property to an ObjectType."""
        model = PropertyDefModel(
            id=new_uuid(),
            object_type_id=object_type_id,
            api_name=prop.api_name,
            display_name=prop.display_name,
            description=prop.description,
            data_type=prop.data_type,
            project_id=prop.project_id or "00000000000000000000000000000001",
            is_primary_key=prop.is_primary_key,
            is_title_property=prop.is_title_property,
            nullable=prop.nullable,
            indexed=prop.indexed,
            backing_catalog=prop.backing_mapping.backing_catalog if prop.backing_mapping else None,
            backing_schema=prop.backing_mapping.backing_schema if prop.backing_mapping else None,
            backing_table=prop.backing_mapping.backing_table if prop.backing_mapping else None,
            backing_column=prop.backing_mapping.backing_column if prop.backing_mapping else None,
            # §14.4: VECTOR 属性配置序列化进 constraints JSONB。
            constraints=(prop.vector_config.model_dump() if prop.vector_config else {}),
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return PropertyDef.model_validate(model)

    async def update_property_backing_mapping(
        self,
        property_id: str,
        mapping: BackingColumnRef | None,
    ) -> PropertyDef:
        """Set or clear a property's physical column mapping (dataset link).

        A1 dataset-link API target. Writes the flat backing_* columns on
        PropertyDefModel and returns the refreshed PropertyDef.
        """
        stmt = select(PropertyDefModel).where(PropertyDefModel.id == property_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Property", property_id)
        if mapping is None:
            model.backing_dataset_api_name = None
            model.backing_catalog = None
            model.backing_schema = None
            model.backing_table = None
            model.backing_column = None
        else:
            model.backing_dataset_api_name = mapping.dataset_api_name or None
            model.backing_catalog = mapping.backing_catalog
            model.backing_schema = mapping.backing_schema
            model.backing_table = mapping.backing_table
            model.backing_column = mapping.backing_column
        model.updated_at = utcnow()
        await self._flush_and_commit()
        return PropertyDef.model_validate(model)

    async def get_properties(self, object_type_id: str) -> list[PropertyDef]:
        """Get all properties for an ObjectType."""
        stmt = (
            select(PropertyDefModel)
            .where(PropertyDefModel.object_type_id == object_type_id)
            .order_by(PropertyDefModel.created_at)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [PropertyDef.model_validate(m) for m in models]

    async def delete_property(self, property_id: str) -> None:
        """Delete a PropertyDef by ID."""
        stmt = select(PropertyDefModel).where(PropertyDefModel.id == property_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Property", property_id)
        await self._session.delete(model)
        await self._flush_and_commit()

    # ── SharedProperty ──

    async def create_shared_property(self, prop: SharedProperty) -> SharedProperty:
        """Create a globally reusable shared property."""
        model = SharedPropertyModel(
            id=new_uuid(),
            api_name=prop.api_name,
            display_name=prop.display_name,
            description=prop.description,
            data_type=prop.data_type,
            project_id=prop.project_id or "00000000000000000000000000000001",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return SharedProperty.model_validate(model)

    async def list_shared_properties(self) -> list[SharedProperty]:
        """List all globally reusable shared properties (for api_name uniqueness)."""
        from sqlalchemy import select as sa_select

        stmt = sa_select(SharedPropertyModel).order_by(SharedPropertyModel.created_at)
        result = await self._session.execute(stmt)
        return [SharedProperty.model_validate(m) for m in result.scalars().all()]

    async def link_shared_property(self, object_type_id: str, shared_property_id: str) -> None:
        """Link a shared property to an ObjectType (M:N relationship)."""
        from ontology.core.models.ontology import ObjectTypeSharedPropertyModel

        model = ObjectTypeSharedPropertyModel(
            object_type_id=object_type_id,
            shared_property_id=shared_property_id,
        )
        self._session.add(model)
        try:
            await self._flush_and_commit()
        except Exception:
            raise ConflictError(f"Shared property {shared_property_id} already linked to object type {object_type_id}")

    # ── LinkType ──

    async def create_link_type(self, link: LinkTypeDef) -> LinkTypeDef:
        """Define a relationship type between two ObjectTypes."""
        model = LinkTypeModel(
            id=new_uuid(),
            ontology_id=link.ontology_id,
            api_name=link.api_name,
            display_name=link.display_name,
            description=link.description,
            source_object_type_id=link.source_object_type_id,
            target_object_type_id=link.target_object_type_id,
            foreign_key_property_api_name=link.foreign_key_property_api_name,
            cardinality=link.cardinality,
            direction=link.direction,
            weight_property=link.weight_property,
            temporal=link.temporal,
            status="ACTIVE",
            project_id=link.project_id or "00000000000000000000000000000001",
            deleted_at=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return LinkTypeDef.model_validate(model)

    async def get_link_types(self, ontology_api_name: str, *, include_non_active: bool = False) -> list[LinkTypeDef]:
        """Get all link types within an Ontology.

        v5.2: excludes soft-deleted and DEPRECATED link types unless
        ``include_non_active=True`` (design §八).
        """
        onto_stmt = select(OntologyModel).where(OntologyModel.api_name == ontology_api_name)
        onto_result = await self._session.execute(onto_stmt)
        onto = onto_result.scalar_one_or_none()
        if onto is None:
            raise NotFoundError("Ontology", ontology_api_name)

        stmt = select(LinkTypeModel).where(LinkTypeModel.ontology_id == onto.id).order_by(LinkTypeModel.created_at)
        if not include_non_active:
            stmt = stmt.where(
                LinkTypeModel.deleted_at.is_(None),
                LinkTypeModel.status != "DEPRECATED",
            )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [LinkTypeDef.model_validate(m) for m in models]

    async def delete_link_type(self, link_id: str) -> None:
        """Delete a LinkType by ID."""
        stmt = select(LinkTypeModel).where(LinkTypeModel.id == link_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("LinkType", link_id)
        await self._session.delete(model)
        await self._flush_and_commit()

    # ── ActionType ──

    async def create_action_type(self, action: ActionType, auto_commit: bool = True) -> ActionType:
        """Define an action type.

        Args:
            auto_commit: If True (default), flush + commit. Set to False when
                the caller wraps multiple operations in a Service-level
                transaction unit (e.g. define + publish_version_snapshot).
        """
        model = ActionTypeModel(
            id=new_uuid(),
            ontology_id=action.ontology_id,
            api_name=action.api_name,
            display_name=action.display_name,
            description=action.description,
            affected_object_type_id=action.affected_object_type_id,
            parameters=action.parameters,
            rules=action.rules,
            submission_criteria=action.submission_criteria,
            status=action.status,
            risk_level=action.risk_level,
            version=action.version,
            operation_kind=action.operation_kind,
            batch_enabled=action.batch_enabled,
            project_id=action.project_id or "00000000000000000000000000000001",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        if auto_commit:
            await self._flush_and_commit()
        else:
            await self._session.flush()
        return ActionType.model_validate(model)

    async def update_action_type(
        self,
        ontology_api_name: str,
        api_name: str,
        updates: dict[str, Any],
        auto_commit: bool = True,
    ) -> ActionType:
        """Update mutable fields of an ActionType (P1, ADR-011).

        Bumps ``version`` and returns the updated ActionType. Caller is
        responsible for publishing a version snapshot if desired.

        Args:
            auto_commit: If True (default), flush + commit. Set to False when
                the caller wraps this in a Service-level transaction unit
                (e.g. update + publish_version_snapshot).
        """
        at = await self.get_action_type(ontology_api_name, api_name)
        model_stmt = select(ActionTypeModel).where(ActionTypeModel.id == at.id)
        model = (await self._session.execute(model_stmt)).scalar_one()
        for field in (
            "display_name",
            "description",
            "parameters",
            "rules",
            "submission_criteria",
            "status",
            "risk_level",
            "operation_kind",
            "batch_enabled",
        ):
            if field in updates:
                setattr(model, field, updates[field])
        model.version = at.version + 1
        model.updated_at = utcnow()
        if auto_commit:
            await self._flush_and_commit()
        else:
            await self._session.flush()
        return ActionType.model_validate(model)

    async def get_action_type(self, ontology_api_name: str, api_name: str) -> ActionType:
        """Get an action type by Ontology and api_name."""
        onto_stmt = select(OntologyModel).where(OntologyModel.api_name == ontology_api_name)
        onto_result = await self._session.execute(onto_stmt)
        onto = onto_result.scalar_one_or_none()
        if onto is None:
            raise NotFoundError("Ontology", ontology_api_name)

        stmt = select(ActionTypeModel).where(
            ActionTypeModel.ontology_id == onto.id,
            ActionTypeModel.api_name == api_name,
        )
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError(f"ActionType in ontology {ontology_api_name}", api_name)
        return ActionType.model_validate(model)

    async def list_action_types(self, ontology_api_name: str) -> list[ActionType]:
        """List all action types within an Ontology."""
        onto_stmt = select(OntologyModel).where(OntologyModel.api_name == ontology_api_name)
        onto_result = await self._session.execute(onto_stmt)
        onto = onto_result.scalar_one_or_none()
        if onto is None:
            raise NotFoundError("Ontology", ontology_api_name)

        stmt = (
            select(ActionTypeModel)
            .where(
                ActionTypeModel.ontology_id == onto.id,
                ActionTypeModel.deleted_at.is_(None),
                ActionTypeModel.status != "DEPRECATED",
            )
            .order_by(ActionTypeModel.created_at)
        )
        result = await self._session.execute(stmt)
        return [ActionType.model_validate(m) for m in result.scalars().all()]

    # ── InterfaceType ──

    async def create_interface_type(self, iface: InterfaceType) -> InterfaceType:
        """Define an interface type (preview feature)."""
        model = InterfaceTypeModel(
            id=new_uuid(),
            ontology_id=iface.ontology_id,
            api_name=iface.api_name,
            display_name=iface.display_name,
            description=iface.description,
            extends_interface_ids=iface.extends_interface_ids,
            status=iface.status,
            project_id=iface.project_id or "00000000000000000000000000000001",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return InterfaceType.model_validate(model)

    async def get_interface_types(self, ontology_api_name: str) -> list[InterfaceType]:
        """List all interface types for an ontology."""
        ont = await self.get_ontology(ontology_api_name)
        if ont is None:
            raise NotFoundError("Ontology", ontology_api_name)
        stmt = (
            select(InterfaceTypeModel)
            .where(InterfaceTypeModel.ontology_id == ont.id)
            .options(selectinload(InterfaceTypeModel.properties))
        )
        result = await self._session.execute(stmt)
        return [InterfaceType.model_validate(m) for m in result.scalars().all()]

    async def get_interface_type(
        self, ontology_api_name: str, interface_api_name: str
    ) -> InterfaceType | None:
        """Get a single interface type by api_name."""
        ont = await self.get_ontology(ontology_api_name)
        if ont is None:
            return None
        stmt = (
            select(InterfaceTypeModel)
            .where(
                InterfaceTypeModel.ontology_id == ont.id,
                InterfaceTypeModel.api_name == interface_api_name,
            )
            .options(selectinload(InterfaceTypeModel.properties))
        )
        result = await self._session.execute(stmt)
        m = result.scalar_one_or_none()
        return InterfaceType.model_validate(m) if m else None

    async def get_object_types_by_interface(
        self, ontology_api_name: str, interface_api_name: str
    ) -> list[str]:
        """返回实现了某 Interface 的所有 ObjectType api_name 列表。

        ObjectType.extends_interface_ids 存的是 InterfaceType id（JSONB），
        需先查 InterfaceType 拿 id，再查 extends_interface_ids 含此 id 的 ObjectType。
        用于 interfaceBase IR（跨类型起始集）。
        """
        iface = await self.get_interface_type(ontology_api_name, interface_api_name)
        if iface is None:
            return []
        iface_id = iface.id
        # 用 object_type_interfaces 关联表查实现该 Interface 的 ObjectType
        stmt = (
            select(ObjectTypeModel.api_name)
            .join(
                ObjectTypeInterfaceModel,
                ObjectTypeInterfaceModel.object_type_id == ObjectTypeModel.id,
            )
            .where(ObjectTypeInterfaceModel.interface_type_id == iface_id)
        )
        result = await self._session.execute(stmt)
        return [str(row[0]) for row in result.all()]

    async def get_rids_by_interface(
        self, ontology_api_name: str, interface_api_name: str, limit: int = 1_000_000
    ) -> list[str]:
        """返回实现了某 Interface 的所有对象 rid（跨 ObjectType 合并）。

        用于 interfaceBase IR 起始集。
        """
        ot_names = await self.get_object_types_by_interface(ontology_api_name, interface_api_name)
        if not ot_names:
            return []
        all_rids: list[str] = []
        for ot in ot_names:
            rids = await self.get_rids_by_type(ot, limit=limit)
            all_rids.extend(rids)
        return all_rids

    async def add_interface_to_object_type(
        self, object_type_id: str, interface_type_id: str
    ) -> bool:
        """绑定 ObjectType implements InterfaceType（幂等）。"""
        insert_stmt = (
            insert(ObjectTypeInterfaceModel)
            .values(object_type_id=object_type_id, interface_type_id=interface_type_id)
            .on_conflict_do_nothing(
                index_elements=["object_type_id", "interface_type_id"]
            )
        )
        result = await self._session.execute(insert_stmt)
        rc: int = getattr(result, "rowcount", 0) or 0
        return rc > 0

    async def remove_interface_from_object_type(
        self, object_type_id: str, interface_type_id: str
    ) -> bool:
        """解绑 ObjectType implements InterfaceType。"""
        stmt = delete(ObjectTypeInterfaceModel).where(
            ObjectTypeInterfaceModel.object_type_id == object_type_id,
            ObjectTypeInterfaceModel.interface_type_id == interface_type_id,
        )
        result = await self._session.execute(stmt)
        rc: int = getattr(result, "rowcount", 0) or 0
        return rc > 0

    # ── ValueType ──

    async def create_value_type(self, vt: ValueType) -> ValueType:
        """Define a value type (domain semantics wrapper)."""
        model = ValueTypeModel(
            id=new_uuid(),
            ontology_id=vt.ontology_id,
            api_name=vt.api_name,
            display_name=vt.display_name,
            description=vt.description,
            base_type=vt.base_type,
            constraints=vt.constraints,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return ValueType.model_validate(model)

    # ── Struct ──

    async def create_struct(self, struct: Struct) -> Struct:
        """Define a global structured type."""
        model = StructModel(
            id=new_uuid(),
            api_name=struct.api_name,
            display_name=struct.display_name,
            description=struct.description,
            fields=[f.model_dump() for f in struct.fields],
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return Struct.model_validate(model)

    # ── ObjectTypeGroup ──

    async def create_group(self, group: ObjectTypeGroup) -> ObjectTypeGroup:
        """Create an ObjectType group."""
        model = ObjectTypeGroupModel(
            id=new_uuid(),
            ontology_id=group.ontology_id,
            api_name=group.api_name,
            display_name=group.display_name,
            description=group.description,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return ObjectTypeGroup.model_validate(model)

    # ── Branch ──

    async def create_branch(self, branch: Branch) -> Branch:
        """Create an Ontology branch."""
        model = BranchModel(
            id=new_uuid(),
            ontology_id=branch.ontology_id,
            name=branch.name,
            is_main=branch.is_main,
            status=branch.status,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return Branch.model_validate(model)

    # ── Action Execution ──

    async def create_execution_log(
        self,
        action_type_api_name: str,
        object_type_api_name: str,
        ontology_id: str,
        idempotency_key: str,
        parameters: dict[str, Any],
        mutations: list[dict[str, Any]],
        action_id: str | None = None,
        status: str = "COMPLETED",
        performed_by: str = "system",
        before_snapshot: dict[str, Any] | None = None,
        after_snapshot: dict[str, Any] | None = None,
    ) -> ActionExecutionLogModel:
        """Record an action execution. Returns the ORM model.

        Does NOT auto-commit — caller (ActionService) manages the transaction
        for atomic multi-operation commits (object_state + log + outbox).

        P1 (ADR-011): before_snapshot/after_snapshot store the CDL change-data-
        log (full field state before/after) for audit traceability.
        """
        model = ActionExecutionLogModel(
            id=new_uuid(),
            action_id=action_id or new_uuid(),
            action_type_api_name=action_type_api_name,
            object_type_api_name=object_type_api_name,
            ontology_id=ontology_id,
            idempotency_key=idempotency_key,
            parameters=parameters,
            mutations=mutations,
            status=status,
            performed_by=performed_by,
            before_snapshot=_json_safe(before_snapshot or {}),
            after_snapshot=_json_safe(after_snapshot or {}),
            created_at=utcnow(),
        )
        self._session.add(model)
        return model

    async def get_execution_by_idempotency_key(self, idempotency_key: str) -> ActionExecutionLogModel | None:
        """Look up an existing execution by idempotency key."""
        stmt = select(ActionExecutionLogModel).where(ActionExecutionLogModel.idempotency_key == idempotency_key)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_outbox_record(
        self,
        action_execution_id: str,
        effect_type: str,
        effect_config: dict[str, Any],
        payload: dict[str, Any] | None = None,
        target_ontology: str | None = None,
    ) -> OutboxModel:
        """Insert an outbox record. Does NOT auto-commit — caller manages transaction.

        Args:
            target_ontology: ARCHIVE 分桶键 (ontology api_name)。
                action-sync-outbox-design.md §8.1: INDEX 不分桶 (逐条近实时),
                留空; ARCHIVE 按 ontology 分桶供 SyncFlushScheduler 微批拉取。
        """
        model = OutboxModel(
            id=new_uuid(),
            action_execution_id=action_execution_id,
            effect_type=effect_type,
            effect_config=effect_config,
            payload=payload or {},
            target_ontology=target_ontology,
            status="PENDING",
            retry_count=0,
            max_retries=3,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        return model

    async def fetch_pending_outbox(
        self,
        batch_size: int = 100,
        *,
        effect_type: str | None = None,
        exclude_effect_types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch pending outbox records ready for processing.

        action-sync-outbox-design.md §8.2:
        - effect_type: 只拉某一种 effect_type (如 'ARCHIVE')。
        - exclude_effect_types: 排除某些 effect_type (如 OutboxExecutor
          排除 'ARCHIVE',把 ARCHIVE 留给 SyncFlushScheduler 消费)。
        两者可组合。effect_type 比较不区分大小写 (统一 upper)。
        """
        now = utcnow()
        stmt = (
            select(OutboxModel)
            .where(
                OutboxModel.status == "PENDING",
                (OutboxModel.next_retry_at.is_(None)) | (OutboxModel.next_retry_at <= now),
            )
            .limit(batch_size)
            .order_by(OutboxModel.created_at)
        )
        if effect_type is not None:
            stmt = stmt.where(func.upper(OutboxModel.effect_type) == effect_type.upper())
        if exclude_effect_types:
            excluded = [t.upper() for t in exclude_effect_types]
            stmt = stmt.where(func.upper(OutboxModel.effect_type).notin_(excluded))
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "id": m.id,
                "action_execution_id": m.action_execution_id,
                "effect_type": m.effect_type,
                "effect_config": m.effect_config,
                "payload": m.payload,
                "status": m.status,
                "retry_count": m.retry_count,
                "max_retries": m.max_retries,
                "last_error": m.last_error,
                "target_ontology": m.target_ontology,
            }
            for m in models
        ]

    async def mark_outbox_completed(self, outbox_id: str) -> None:
        """Mark an outbox record as completed."""
        stmt = select(OutboxModel).where(OutboxModel.id == outbox_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            model.status = "COMPLETED"
            model.updated_at = utcnow()
            await self._flush_and_commit()

    async def retry_outbox(self, outbox_id: str, retry_count: int, error: str, next_retry_at: datetime) -> None:
        """Schedule an outbox retry with exponential backoff."""
        stmt = select(OutboxModel).where(OutboxModel.id == outbox_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            model.status = "PENDING"
            model.retry_count = retry_count
            model.last_error = error
            model.next_retry_at = next_retry_at
            model.updated_at = utcnow()
            await self._flush_and_commit()

    async def move_outbox_to_dlq(self, outbox_id: str, error: str) -> None:
        """Move a permanently failed outbox record to the dead letter queue (DLQ)."""
        stmt = select(OutboxModel).where(OutboxModel.id == outbox_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            model.status = "DLQ"
            model.last_error = error
            model.updated_at = utcnow()
            await self._flush_and_commit()

    # ── Sync Outbox (action-sync-outbox-design.md §8.2) ──

    async def count_pending_by_ontology(self, effect_type: str) -> list[tuple[str | None, int]]:
        """Count PENDING outbox records grouped by target_ontology.

        SyncFlushScheduler 用此判断每个本体是否达到微批触发阈值
        (design §5.1)。返回 [(target_ontology, count), ...],target_ontology
        可能为 None (历史/未分桶记录)。effect_type 不区分大小写。
        """
        stmt = (
            select(OutboxModel.target_ontology, func.count())
            .where(
                OutboxModel.status == "PENDING",
                func.upper(OutboxModel.effect_type) == effect_type.upper(),
                (OutboxModel.next_retry_at.is_(None)) | (OutboxModel.next_retry_at <= utcnow()),
            )
            .group_by(OutboxModel.target_ontology)
        )
        result = await self._session.execute(stmt)
        return [(row[0], int(row[1])) for row in result.all()]

    async def claim_pending_by_ontology(
        self,
        effect_type: str,
        ontology: str,
        batch_size: int = 1000,
    ) -> list[dict[str, Any]]:
        """Claim a batch of PENDING outbox records for one ontology.

        action-sync-outbox-design.md §3.7: 多实例 HA 部署时用
        ``FOR UPDATE SKIP LOCKED`` 规避并发 claim 重复 (outbox 模式标准做法)。
        单实例无额外开销。Auto-commits the claim's row locks immediately so the
        SKIP LOCKED windows across instances don't serialize.

        Returns the same record dict shape as :meth:`fetch_pending_outbox`.
        """
        now = utcnow()
        stmt = (
            select(OutboxModel)
            .where(
                func.upper(OutboxModel.effect_type) == effect_type.upper(),
                OutboxModel.status == "PENDING",
                OutboxModel.target_ontology == ontology,
                (OutboxModel.next_retry_at.is_(None)) | (OutboxModel.next_retry_at <= now),
            )
            .order_by(OutboxModel.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "id": m.id,
                "action_execution_id": m.action_execution_id,
                "effect_type": m.effect_type,
                "effect_config": m.effect_config,
                "payload": m.payload,
                "status": m.status,
                "retry_count": m.retry_count,
                "max_retries": m.max_retries,
                "last_error": m.last_error,
                "target_ontology": m.target_ontology,
            }
            for m in models
        ]

    async def mark_outbox_batch_completed(self, outbox_ids: list[str]) -> int:
        """Mark a batch of outbox records as COMPLETED (ARCHIVE flush 成功后调用).\n
        Returns the number of rows updated. Auto-commits.
        """
        if not outbox_ids:
            return 0
        stmt = (
            update(OutboxModel)
            .where(OutboxModel.id.in_(outbox_ids), OutboxModel.status == "PENDING")
            .values(status="COMPLETED", updated_at=utcnow())
        )
        result = await self._session.execute(stmt)
        await self._flush_and_commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def retry_outbox_batch(
        self, outbox_ids: list[str], error: str, *, retry_count: int | None = None
    ) -> int:
        """Schedule a batch retry: keep PENDING, set last_error + backoff.

        action-sync-outbox-design.md §4.2: ARCHIVE flush 失败时批量回退到 PENDING
        等待下个 tick 重试 (retry_count 沿用记录自身的值,若超过 max_retries 由
        逐条消费者 OutboxExecutor 的 _handle_failure 转 DLQ; 这里只重置状态).
        Auto-commits. Returns rows updated.
        """
        if not outbox_ids:
            return 0
        now = utcnow()
        # 指数退避: 60s 起步 (ARCHIVE 失败通常是 Iceberg/Trino 抖动,稍长退避)
        values: dict[str, Any] = {
            "status": "PENDING",
            "last_error": error,
            "next_retry_at": now,
            "updated_at": now,
        }
        if retry_count is not None:
            values["retry_count"] = retry_count
        stmt = (
            update(OutboxModel)
            .where(OutboxModel.id.in_(outbox_ids), OutboxModel.status == "PENDING")
            .values(**values)
        )
        result = await self._session.execute(stmt)
        await self._flush_and_commit()
        return int(getattr(result, "rowcount", 0) or 0)

    async def delete_old_completed_outbox(self, retention_days: int = 7) -> int:
        """Delete COMPLETED/FAILED outbox records older than retention window.

        action-sync-outbox-design.md §4.3: outbox 清理机制。
        - PENDING: 不删 (等消费/重试)
        - COMPLETED/FAILED: 保留 7 天后删
        - DLQ: 不自动删 (人工审查)
        Auto-commits. Returns rows deleted.
        """
        cutoff = utcnow() - timedelta(days=retention_days)
        stmt = delete(OutboxModel).where(
            OutboxModel.status.in_(["COMPLETED", "FAILED"]),
            OutboxModel.updated_at < cutoff,
        )
        result = await self._session.execute(stmt)
        await self._flush_and_commit()
        return int(getattr(result, "rowcount", 0) or 0)

    # ── Object State (Operational Write Target for Actions) ──

    async def upsert_object_state(
        self,
        rid: str,
        object_type_api_name: str,
        ontology_id: str,
        properties: dict[str, Any],
        expected_version: int,
        modified_by: str = "system",
        ontology_api_name: str = "",
    ) -> int:
        """UPSERT object state with row-level OCC.

        For CREATE (expected_version=0): INSERT ON CONFLICT DO NOTHING.
        For UPDATE (expected_version>0): UPDATE WHERE version = expected.

        Returns:
            New version on success, 0 if conflict (affected_rows=0 for UPDATE,
            or duplicate key for CREATE).

        Does NOT auto-commit — caller manages the transaction.

        P1 (ADR-011): modified_by records who applied the change (system
        audit field, mirrors Palantir modifiedBy).
        """
        now = utcnow()

        if expected_version == 0:
            insert_stmt = (
                insert(ObjectStateModel)
                .values(
                    rid=rid,
                    object_type_api_name=object_type_api_name,
                    ontology_id=ontology_id,
                    ontology_api_name=ontology_api_name,
                    version=1,
                    properties=properties,
                    modified_by=modified_by,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing()
            )
            insert_result = await self._session.execute(insert_stmt)
            rc: int = getattr(insert_result, "rowcount", 0) or 0
            return 1 if rc > 0 else 0
        else:
            new_version = expected_version + 1
            update_stmt = (
                update(ObjectStateModel)
                .where(
                    ObjectStateModel.rid == rid,
                    ObjectStateModel.version == expected_version,
                )
                .values(
                    properties=properties,
                    version=new_version,
                    modified_by=modified_by,
                    updated_at=now,
                )
                .returning(ObjectStateModel.version)
            )
            update_result = await self._session.execute(update_stmt)
            row = update_result.fetchone()
            return row[0] if row else 0

    async def get_object_state(self, rid: str) -> dict[str, Any] | None:
        """Read current object state (for read-your-writes point queries)."""
        stmt = select(ObjectStateModel).where(ObjectStateModel.rid == rid)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return {
            "rid": model.rid,
            "object_type_api_name": model.object_type_api_name,
            "version": model.version,
            "properties": model.properties,
            "ontology_id": model.ontology_id,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    async def get_object_states_by_rids(
        self, rids: list[str], limit: int = 10_000
    ) -> list[dict[str, Any]]:
        """Batch read object states by rid (graph-reasoning 水合用, §7.3 C12)。

        图遍历/过滤返回 rid 集（= object_state.rid），此方法批量取全量属性。
        分批查询避免超大 IN（R2: PG IN >3000 性能劣化）。limit 对齐 Palantir
        水合上限（C9 防线二）。
        """
        if not rids:
            return []
        rids = list(dict.fromkeys(rids))[:limit]  # 去重 + 上限
        results: list[dict[str, Any]] = []
        batch_size = 5000
        for i in range(0, len(rids), batch_size):
            batch = rids[i : i + batch_size]
            stmt = select(ObjectStateModel).where(ObjectStateModel.rid.in_(batch))
            res = await self._session.execute(stmt)
            for m in res.scalars().all():
                results.append({
                    "rid": m.rid,
                    "object_type_api_name": m.object_type_api_name,
                    "version": m.version,
                    "properties": m.properties,
                    "ontology_id": m.ontology_id,
                    "created_at": m.created_at,
                    "updated_at": m.updated_at,
                })
        return results

    async def get_object_states_by_pks(
        self,
        ontology_api_name: str,
        object_type_api_name: str,
        pk_backing_column: str,
        pk_values: list[str],
        *,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        """按业务 PK 批量查 object_state（MANAGED 端 PK→rid 反查，ADR-021 §2.6）。

        PK 存在 object_state.properties JSONB 里（按 backing_column key），
        不是独立列。用 properties[key].as_string() IN (:pks) 查询。

        分批 5000 避免 PG IN 劣化（对齐 get_object_states_by_rids 的分批策略）。
        pk_backing_column 走白名单校验防注入（同 IcebergStore._validate_identifier）。

        Returns: object_state dict 列表（含 rid + properties）。悬空的 PK
            （MANAGED 端不存在）不会出现在结果里，调用方据此跳过悬空边。
        """
        import re

        if not pk_values:
            return []
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pk_backing_column):
            raise OntologyError(f"Invalid SQL identifier: {pk_backing_column!r}")

        pk_values = list(dict.fromkeys(str(v) for v in pk_values))[:limit]
        results: list[dict[str, Any]] = []
        batch_size = 5000
        for i in range(0, len(pk_values), batch_size):
            batch = pk_values[i : i + batch_size]
            pk_expr = ObjectStateModel.properties[pk_backing_column].as_string()
            stmt = (
                select(ObjectStateModel)
                .where(ObjectStateModel.ontology_api_name == ontology_api_name)
                .where(ObjectStateModel.object_type_api_name == object_type_api_name)
                .where(pk_expr.in_(batch))
            )
            res = await self._session.execute(stmt)
            for m in res.scalars().all():
                results.append({
                    "rid": m.rid,
                    "object_type_api_name": m.object_type_api_name,
                    "version": m.version,
                    "properties": m.properties,
                    "ontology_id": m.ontology_id,
                    "ontology_api_name": m.ontology_api_name,
                    "created_at": m.created_at,
                    "updated_at": m.updated_at,
                })
        return results

    async def get_object_states_by_type(
        self, object_type_api_name: str, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List objects of a given type, ordered by updated_at DESC."""
        stmt = (
            select(ObjectStateModel)
            .where(ObjectStateModel.object_type_api_name == object_type_api_name)
            .order_by(ObjectStateModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "rid": m.rid,
                "object_type_api_name": m.object_type_api_name,
                "version": m.version,
                "properties": m.properties,
                "ontology_id": m.ontology_id,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for m in models
        ]

    async def get_rids_by_type(
        self, object_type_api_name: str, limit: int = 1_000_000
    ) -> list[str]:
        """只取 rid，不拉 properties JSONB（_eval_object_type 用）。

        区别于 get_object_states_by_type（拉全量属性），此方法只 SELECT rid，
        大规模对象集时避免拉无用 JSONB 到内存。
        """
        stmt = (
            select(ObjectStateModel.rid)
            .where(ObjectStateModel.object_type_api_name == object_type_api_name)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return [str(row[0]) for row in result.all()]

    async def delete_object_state(self, rid: str) -> None:
        """Delete an object from operational state. Does NOT auto-commit."""
        stmt = select(ObjectStateModel).where(ObjectStateModel.rid == rid)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            await self._session.delete(model)

    async def query_object_states(
        self,
        object_type_api_name: str,
        filters: list[dict[str, Any]] | None = None,
        rids: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query object_state for read-your-writes.

        Supports simple equality filters on JSONB ``properties`` (field/value)
        and direct ID lookup. Used by ObjectQueryService as a read-your-writes
        fallback so that objects just mutated by an Action (and not yet CDC-
        synced to Iceberg) are still visible to queries.

        Args:
            object_type_api_name: restrict to this object type.
            filters: list of {field, value} equality filters against properties.
            rids: if given, restrict to these rids (point lookup).
            limit/offset: pagination.
        """
        from sqlalchemy import select as sa_select

        stmt = sa_select(ObjectStateModel).where(ObjectStateModel.object_type_api_name == object_type_api_name)
        if rids:
            stmt = stmt.where(ObjectStateModel.rid.in_(rids))
        if filters:
            # JSONB equality: properties->>'<field>' = '<value>'. Cast value
            # to text for the comparison (covers str/int/float/bool). Only eq
            # is supported — complex filters defer to Iceberg/Doris.
            for f in filters:
                field = f.get("field")
                value = f.get("value")
                if field is None:
                    continue
                stmt = stmt.where(ObjectStateModel.properties[field].as_string() == str(value))
        stmt = stmt.order_by(ObjectStateModel.updated_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [
            {
                "rid": m.rid,
                "object_type_api_name": m.object_type_api_name,
                "version": m.version,
                "properties": m.properties,
                "ontology_id": m.ontology_id,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
            }
            for m in models
        ]

    # ══════════════════════════════════════════════════════════════
    # Object Links (P1, ADR-011) — Link relationship instances
    # ══════════════════════════════════════════════════════════════

    async def add_object_link(
        self,
        ontology_id: str,
        link_type_api_name: str,
        source_rid: str,
        target_rid: str,
    ) -> bool:
        """Add a Link between two objects. Idempotent (ON CONFLICT DO NOTHING).

        Returns True if a new link was created, False if it already existed.
        Does NOT auto-commit — caller manages the transaction.
        """
        insert_stmt = (
            insert(ObjectLinkModel)
            .values(
                id=new_uuid(),
                ontology_id=ontology_id,
                link_type_api_name=link_type_api_name,
                source_rid=source_rid,
                target_rid=target_rid,
                created_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=["link_type_api_name", "source_rid", "target_rid"])
        )
        result = await self._session.execute(insert_stmt)
        rc: int = getattr(result, "rowcount", 0) or 0
        return rc > 0

    async def remove_object_link(
        self,
        ontology_id: str,
        link_type_api_name: str,
        source_rid: str,
        target_rid: str,
    ) -> bool:
        """Remove a single Link. Returns True if a row was deleted."""
        stmt = delete(ObjectLinkModel).where(
            ObjectLinkModel.ontology_id == ontology_id,
            ObjectLinkModel.link_type_api_name == link_type_api_name,
            ObjectLinkModel.source_rid == source_rid,
            ObjectLinkModel.target_rid == target_rid,
        )
        result = await self._session.execute(stmt)
        rc: int = getattr(result, "rowcount", 0) or 0
        return rc > 0

    async def clear_object_links(
        self,
        ontology_id: str,
        link_type_api_name: str,
        source_rid: str,
    ) -> int:
        """Remove all Links of a given link_type from a source object.

        Returns the number of links removed. Does NOT auto-commit.
        """
        stmt = delete(ObjectLinkModel).where(
            ObjectLinkModel.ontology_id == ontology_id,
            ObjectLinkModel.link_type_api_name == link_type_api_name,
            ObjectLinkModel.source_rid == source_rid,
        )
        result = await self._session.execute(stmt)
        rc: int = getattr(result, "rowcount", 0) or 0
        return rc

    async def get_object_links(
        self,
        source_rid: str,
        link_type_api_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read Links from a source object (for read-your-writes link queries)."""
        stmt = select(ObjectLinkModel).where(ObjectLinkModel.source_rid == source_rid)
        if link_type_api_name is not None:
            stmt = stmt.where(ObjectLinkModel.link_type_api_name == link_type_api_name)
        result = await self._session.execute(stmt)
        return [
            {
                "link_type_api_name": m.link_type_api_name,
                "source_rid": m.source_rid,
                "target_rid": m.target_rid,
            }
            for m in result.scalars().all()
        ]

    async def query_object_links_batch(
        self,
        ontology_id: str,
        link_type_api_name: str,
        source_rids: list[str],
        direction: str = "forward",
    ) -> dict[str, list[str]]:
        """批量查 object_links，返回 source_rid → [target_rid] 映射。

        forward: source_rid 在 source_rids 中，返回 target_rid 列表。
        reverse: target_rid 在 source_rids 中，返回 source_rid 列表。
        用于 traverse_link 的 PG 降级路径（Neo4j 不可用时）。
        """
        if not source_rids:
            return {}
        if direction == "reverse":
            stmt = (
                select(ObjectLinkModel.target_rid, ObjectLinkModel.source_rid)
                .where(
                    ObjectLinkModel.ontology_id == ontology_id,
                    ObjectLinkModel.link_type_api_name == link_type_api_name,
                    ObjectLinkModel.target_rid.in_(source_rids),
                )
            )
            result = await self._session.execute(stmt)
            mapping: dict[str, list[str]] = {sv: [] for sv in source_rids}
            for tgt, src in result.all():
                mapping.setdefault(str(tgt), []).append(str(src))
            return mapping
        else:
            stmt = (
                select(ObjectLinkModel.source_rid, ObjectLinkModel.target_rid)
                .where(
                    ObjectLinkModel.ontology_id == ontology_id,
                    ObjectLinkModel.link_type_api_name == link_type_api_name,
                    ObjectLinkModel.source_rid.in_(source_rids),
                )
            )
            result = await self._session.execute(stmt)
            mapping = {sv: [] for sv in source_rids}
            for src, tgt in result.all():
                mapping.setdefault(str(src), []).append(str(tgt))
            return mapping

    # ══════════════════════════════════════════════════════════════
    # ActionType Versions (P1, ADR-011) — history snapshots for rollback
    # ══════════════════════════════════════════════════════════════

    async def publish_action_type_version(
        self,
        action_type_id: str,
        version: int,
        snapshot: dict[str, Any],
        published_by: str = "system",
        auto_commit: bool = True,
    ) -> ActionTypeVersionModel:
        """Publish a version snapshot of an ActionType.

        Idempotent: if a snapshot for (action_type_id, version) already
        exists, it is returned as-is without overwriting.

        Args:
            auto_commit: If True (default), flush + commit so the snapshot is
                persisted immediately (fixes the bug where snapshots were
                lost because no caller committed). Set to False only when the
                caller wraps this in a Service-level ``transaction()`` unit.
        """
        existing_stmt = select(ActionTypeVersionModel).where(
            ActionTypeVersionModel.action_type_id == action_type_id,
            ActionTypeVersionModel.version == version,
        )
        existing = (await self._session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return existing
        model = ActionTypeVersionModel(
            id=new_uuid(),
            action_type_id=action_type_id,
            version=version,
            snapshot=snapshot,
            published_by=published_by,
            created_at=utcnow(),
        )
        self._session.add(model)
        if auto_commit:
            await self._flush_and_commit()
        else:
            await self._session.flush()
        return model

    async def list_action_type_versions(self, action_type_id: str) -> list[ActionTypeVersionModel]:
        """List all historical versions of an ActionType, newest first."""
        stmt = (
            select(ActionTypeVersionModel)
            .where(ActionTypeVersionModel.action_type_id == action_type_id)
            .order_by(ActionTypeVersionModel.version.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_action_type_version(self, action_type_id: str, version: int) -> ActionTypeVersionModel | None:
        """Get a specific historical version snapshot."""
        stmt = select(ActionTypeVersionModel).where(
            ActionTypeVersionModel.action_type_id == action_type_id,
            ActionTypeVersionModel.version == version,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ══════════════════════════════════════════════════════════════
    # DataSource / Credential / SyncTask / Dataset CRUD
    # ══════════════════════════════════════════════════════════════

    # ── Credential ──

    async def create_credential(self, cred: CredentialCreate) -> Credential:
        """Create a new credential."""
        model = CredentialModel(
            id=new_uuid(),
            api_name=cred.api_name,
            credential_type=cred.credential_type,
            secret_data=cred.secret_data,
            created_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return Credential.model_validate(model)

    async def get_credential(self, api_name: str) -> Credential:
        """Get a credential by api_name."""
        stmt = select(CredentialModel).where(CredentialModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Credential", api_name)
        return Credential.model_validate(model)

    async def get_credential_by_id(self, credential_id: str) -> Credential:
        """Get a credential by id (used internally to resolve a DataSource's
        credential without exposing the secret via the API)."""
        stmt = select(CredentialModel).where(CredentialModel.id == credential_id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Credential", credential_id)
        return Credential.model_validate(model)

    async def list_credentials(self) -> list[Credential]:
        """List all credentials."""
        stmt = select(CredentialModel).order_by(CredentialModel.created_at)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [Credential.model_validate(m) for m in models]

    async def delete_credential(self, api_name: str) -> None:
        """Delete a credential by api_name."""
        stmt = select(CredentialModel).where(CredentialModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Credential", api_name)
        await self._session.delete(model)
        await self._flush_and_commit()

    # ── DataSource ──

    async def create_datasource(self, ds: DataSourceCreate) -> DataSource:
        """Create a new data source."""
        model = DataSourceModel(
            id=new_uuid(),
            api_name=ds.api_name,
            display_name=ds.display_name,
            description=ds.description,
            connector_type=ds.connector_type,
            connector_config=ds.connector_config,
            credential_id=ds.credential_id,
            status="CONNECTED",
            gravitino_catalog_name=ds.api_name,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return DataSource.model_validate(model)

    async def get_datasource(self, api_name: str) -> DataSource:
        """Get a data source by api_name."""
        stmt = select(DataSourceModel).where(DataSourceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("DataSource", api_name)
        return DataSource.model_validate(model)

    async def get_datasource_by_id(self, id: str) -> DataSource:
        """Get a data source by UUID id."""
        stmt = select(DataSourceModel).where(DataSourceModel.id == id)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("DataSource", id)
        return DataSource.model_validate(model)

    async def list_datasources(self) -> list[DataSource]:
        """List all data sources."""
        stmt = select(DataSourceModel).order_by(DataSourceModel.created_at)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [DataSource.model_validate(m) for m in models]

    async def update_datasource(self, api_name: str, updates: dict[str, Any]) -> DataSource:
        """Update a data source (partial update)."""
        stmt = select(DataSourceModel).where(DataSourceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("DataSource", api_name)
        allowed_fields = {"display_name", "description", "connector_config", "credential_id", "status"}
        for key, value in updates.items():
            if key in allowed_fields:
                setattr(model, key, value)
        model.updated_at = utcnow()
        await self._flush_and_commit()
        return DataSource.model_validate(model)

    async def delete_datasource(self, api_name: str, auto_commit: bool = True) -> None:
        """Delete a data source and cascade to its sync tasks.

        Args:
            api_name: Data source api_name
            auto_commit: If True, flush + commit. Set to False when the
                         caller needs to batch multiple operations in one
                         transaction.
        """
        stmt = select(DataSourceModel).where(DataSourceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("DataSource", api_name)
        await self._session.delete(model)
        if auto_commit:
            await self._flush_and_commit()

    # ── SyncTask ──

    async def create_sync_task(self, task: SyncTaskCreate) -> SyncTask:
        """Create a new sync task."""
        model = SyncTaskModel(
            id=new_uuid(),
            api_name=task.api_name,
            data_source_id=task.data_source_id,
            sync_type=task.sync_type,
            source_config=task.source_config,
            target_dataset_api_name=task.target_dataset_api_name,
            sync_mode=task.sync_mode,
            transaction_type=task.transaction_type,
            allow_schema_changes=task.allow_schema_changes,
            max_duration_minutes=task.max_duration_minutes,
            file_filters=task.file_filters,
            schedule=task.schedule,
            status="DRAFT",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return SyncTask.model_validate(model)

    async def get_sync_task(self, api_name: str) -> SyncTask:
        """Get a sync task by api_name."""
        stmt = select(SyncTaskModel).where(SyncTaskModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("SyncTask", api_name)
        return SyncTask.model_validate(model)

    async def list_sync_tasks_for_datasource(self, datasource_id: str) -> list[SyncTask]:
        """List all sync tasks for a data source."""
        stmt = (
            select(SyncTaskModel)
            .where(SyncTaskModel.data_source_id == datasource_id)
            .order_by(SyncTaskModel.created_at)
        )
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [SyncTask.model_validate(m) for m in models]

    async def update_sync_task(self, api_name: str, updates: dict[str, Any]) -> SyncTask:
        """Update a sync task (partial update)."""
        stmt = select(SyncTaskModel).where(SyncTaskModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("SyncTask", api_name)
        allowed_fields = {
            "source_config",
            "sync_mode",
            "transaction_type",
            "schedule",
            "status",
            "pipeline_name",
            "last_run_at",
            "allow_schema_changes",
            "max_duration_minutes",
            "file_filters",
        }
        for key, value in updates.items():
            if key in allowed_fields:
                setattr(model, key, value)
        model.updated_at = utcnow()
        await self._flush_and_commit()
        return SyncTask.model_validate(model)

    async def delete_sync_task(self, api_name: str, auto_commit: bool = True) -> SyncTaskModel:
        """Delete a sync task and return the deleted model.

        Args:
            api_name: Sync task api_name
            auto_commit: If True, flush + commit. Set to False when the
                         caller needs to batch multiple operations in one
                         transaction (e.g. delete_sync_task + delete_dataset).

        Returns:
            The deleted SyncTaskModel (before deletion), so callers can
            access fields like target_dataset_api_name.
        """
        stmt = select(SyncTaskModel).where(SyncTaskModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("SyncTask", api_name)
        await self._session.delete(model)
        if auto_commit:
            await self._flush_and_commit()
        return model

    # ── Dataset (Governance Metadata) ──

    async def create_dataset(self, ds: DatasetGovernanceCreate) -> DatasetGovernance:
        """Register dataset governance metadata in PG.

        Idempotent: if a dataset with the same api_name already exists,
        returns the existing record instead of raising ConflictError.
        This prevents 409 errors when the frontend retries sync task
        creation with dataset registration.
        """
        # Check before insert — UNIQUE(api_name) constraint would 409 otherwise
        try:
            existing = await self.get_dataset(ds.api_name)
            return existing
        except NotFoundError:
            pass

        model = DatasetGovernanceModel(
            id=new_uuid(),
            api_name=ds.api_name,
            display_name=ds.display_name,
            storage_location=ds.storage_location,
            partition_config=ds.partition_config,
            source_dataset_api_name=ds.source_dataset_api_name,
            data_source_api_name=ds.data_source_api_name,
            kind=ds.kind,
            is_view=ds.is_view,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        return DatasetGovernance.model_validate(model)

    async def get_dataset(self, api_name: str) -> DatasetGovernance:
        """Get dataset governance metadata by api_name."""
        stmt = select(DatasetGovernanceModel).where(DatasetGovernanceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Dataset", api_name)
        return DatasetGovernance.model_validate(model)

    async def list_datasets(self) -> list[DatasetGovernance]:
        """List all datasets."""
        stmt = select(DatasetGovernanceModel).order_by(DatasetGovernanceModel.created_at)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [DatasetGovernance.model_validate(m) for m in models]

    async def list_datasets_paginated(
        self,
        *,
        page: int,
        page_size: int,
        search: str = "",
        type_filter: str = "",
        ontology_api_name: str = "",
    ) -> tuple[list[DatasetGovernance], int]:
        """Paginated, filtered dataset list.

        Returns (page_items, total). Filters:
          - search: case-insensitive substring on api_name / display_name
          - type_filter: ``managed`` | ``virtual`` | ``transform``
            (transform = has source_dataset_api_name; virtual = kind=VIRTUAL
             and no source_dataset; managed = kind=MANAGED and no source_dataset)
          - ontology_api_name: only datasets whose backing is referenced by
            an ObjectType under this ontology (EXISTS on properties join)

        Ordering by created_at keeps paging stable across requests.
        """
        from sqlalchemy import and_, exists, func, or_

        from ontology.core.models.ontology import (
            ObjectTypeModel,
            OntologyModel,
            PropertyDefModel,
        )

        conditions = []
        if search:
            like = f"%{search}%"
            conditions.append(
                or_(
                    DatasetGovernanceModel.api_name.ilike(like),
                    DatasetGovernanceModel.display_name.ilike(like),
                )
            )
        if type_filter == "transform":
            conditions.append(DatasetGovernanceModel.source_dataset_api_name.is_not(None))
        elif type_filter == "virtual":
            conditions.append(
                and_(
                    DatasetGovernanceModel.kind == "VIRTUAL",
                    DatasetGovernanceModel.source_dataset_api_name.is_(None),
                )
            )
        elif type_filter == "managed":
            conditions.append(
                and_(
                    DatasetGovernanceModel.kind == "MANAGED",
                    DatasetGovernanceModel.source_dataset_api_name.is_(None),
                )
            )
        if ontology_api_name:
            # EXISTS: 该 dataset 被 PropertyDef 引用为 backing，且其 OT 属于指定本体
            ont_subq = (
                select(PropertyDefModel.id)
                .join(
                    ObjectTypeModel, PropertyDefModel.object_type_id == ObjectTypeModel.id
                )
                .join(OntologyModel, ObjectTypeModel.ontology_id == OntologyModel.id)
                .where(
                    and_(
                        PropertyDefModel.backing_dataset_api_name
                        == DatasetGovernanceModel.api_name,
                        OntologyModel.api_name == ontology_api_name,
                    )
                )
            )
            conditions.append(exists(ont_subq))

        where_clause = and_(*conditions) if conditions else None

        count_stmt = select(func.count()).select_from(DatasetGovernanceModel)
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total = (await self._session.execute(count_stmt)).scalar_one()

        offset = max(0, (page - 1) * page_size)
        stmt = (
            select(DatasetGovernanceModel)
            .order_by(DatasetGovernanceModel.created_at)
            .limit(page_size)
            .offset(offset)
        )
        if where_clause is not None:
            stmt = stmt.where(where_clause)
        result = await self._session.execute(stmt)
        models = result.scalars().all()
        return [DatasetGovernance.model_validate(m) for m in models], total

    async def get_dataset_ontology_map(self) -> dict[str, list[dict[str, str]]]:
        """Reverse-lookup: dataset api_name -> referencing ontologies.

        Aggregates ``backing_dataset_api_name`` from PropertyDef (per-column
        backing) joined to ObjectType + Ontology. ObjectType has no top-level
        backing column (it's derived in the service layer from its properties'
        backing), so PropertyDef is the single source of truth.
        """
        stmt = (
            select(
                PropertyDefModel.backing_dataset_api_name,
                OntologyModel.id.label("ontology_id"),
                OntologyModel.api_name.label("ontology_api_name"),
                OntologyModel.display_name.label("ontology_display_name"),
                ObjectTypeModel.api_name.label("ot_api_name"),
            )
            .join(ObjectTypeModel, PropertyDefModel.object_type_id == ObjectTypeModel.id)
            .join(OntologyModel, ObjectTypeModel.ontology_id == OntologyModel.id)
            .where(PropertyDefModel.backing_dataset_api_name.is_not(None))
        )
        rows = (await self._session.execute(stmt)).all()

        result: dict[str, list[dict[str, str]]] = {}
        seen: set[tuple[str, str, str]] = set()
        for ds_name, ont_id, ont_api, ont_display, ot_api in rows:
            if not ds_name:
                continue
            key = (ds_name, ont_id, ot_api)
            if key in seen:
                continue
            seen.add(key)
            result.setdefault(ds_name, []).append(
                {
                    "ontology_id": ont_id,
                    "ontology_api_name": ont_api,
                    "ontology_display_name": ont_display or "",
                    "object_type_api_name": ot_api,
                }
            )
        return result

    async def update_dataset(self, api_name: str, updates: dict[str, Any]) -> DatasetGovernance:
        """Update dataset governance metadata fields.

        Args:
            api_name: Dataset api_name
            updates: Dict of field_name → new_value (only allowed fields)

        Raises:
            NotFoundError: If dataset does not exist
        """
        stmt = select(DatasetGovernanceModel).where(DatasetGovernanceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Dataset", api_name)

        allowed_fields = {
            "display_name",
            "storage_location",
            "partition_config",
            "data_source_api_name",
            "source_dataset_api_name",
            "kind",
            "is_view",
            "row_count_estimate",
        }
        for key, value in updates.items():
            if key in allowed_fields:
                setattr(model, key, value)
        model.updated_at = utcnow()
        await self._flush_and_commit()
        return DatasetGovernance.model_validate(model)

    async def update_dataset_stats(self, api_name: str, row_count: int) -> None:
        """Update row count estimate for a dataset."""
        stmt = select(DatasetGovernanceModel).where(DatasetGovernanceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is not None:
            model.row_count_estimate = row_count
            model.updated_at = utcnow()
            await self._flush_and_commit()

    async def get_object_types_for_dataset(self, dataset_api_name: str) -> list[ObjectType]:
        """Query which ObjectTypes map to a given Dataset's columns."""
        stmt = (
            select(PropertyDefModel)
            .where(PropertyDefModel.backing_dataset_api_name == dataset_api_name)
            .distinct(PropertyDefModel.object_type_id)
        )
        result = await self._session.execute(stmt)
        prop_models = result.scalars().all()
        if not prop_models:
            return []
        ot_ids = list({p.object_type_id for p in prop_models})
        stmt2 = select(ObjectTypeModel).where(ObjectTypeModel.id.in_(ot_ids))
        result2 = await self._session.execute(stmt2)
        return [ObjectType.model_validate(m) for m in result2.scalars().all()]

    async def delete_dataset(self, api_name: str, auto_commit: bool = True) -> None:
        """Delete dataset governance metadata by api_name.

        Args:
            api_name: Dataset api_name
            auto_commit: If True, flush + commit. Set to False when the
                         caller needs to batch multiple operations in one
                         transaction.
        """
        stmt = select(DatasetGovernanceModel).where(DatasetGovernanceModel.api_name == api_name)
        result = await self._session.execute(stmt)
        model = result.scalar_one_or_none()
        if model is None:
            raise NotFoundError("Dataset", api_name)
        await self._session.delete(model)
        if auto_commit:
            await self._flush_and_commit()

    # ── Transaction Control ──

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Service-level unit-of-work transaction boundary.

        对标 SQLAlchemy 2.0 / FastAPI 最佳实践（item 10 "Keep Transaction
        Boundaries Explicit"）：use-case 在 service 层用一个事务单元包裹
        多个低层操作，正常退出 commit、异常 rollback。事务内的低层 metadata
        方法应传 ``auto_commit=False``（只 flush 不 commit），由本单元统一提交。

        实现说明：AsyncSession 默认 autobegin，service 在进入事务前常会先做
        只读查询（如 get_ontology / get_object_type）触发隐式事务，因此不能用
        ``session.begin()``（它要求进入时无活动事务，否则报 InvalidRequestError）。
        改用 try/commit/except/rollback 模式：无论 session 是否已 autobegin，
        正常退出统一 commit、异常统一 rollback，与现有 commit_transaction /
        rollback_transaction 语义一致。

        IntegrityError 被包装为 ConflictError（HTTP 409）以与现有错误映射一致。
        """
        try:
            yield
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Resource already exists (unique constraint violation)") from exc
        except Exception:
            await self._session.rollback()
            raise

    async def commit_transaction(self) -> None:
        """Commit the current transaction (used for multi-operation atomicity)."""
        await self._session.commit()

    async def rollback_transaction(self) -> None:
        """Rollback the current transaction."""
        await self._session.rollback()

    async def _flush_and_commit(self) -> None:
        """Flush pending changes and commit the transaction.

        Wraps IntegrityError (e.g. unique constraint violations)
        as ConflictError for proper HTTP 409 mapping.
        """
        try:
            await self._session.flush()
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Resource already exists (unique constraint violation)") from exc

    # ═══════════════════════════════════════════════════════════════════
    # Permission governance — PIP queries (ADR-016, Phase 1)
    # ═══════════════════════════════════════════════════════════════════

    async def resolve_resource_ownership(
        self, resource_type: str, resource_id: str
    ) -> ResourceOwnership | None:
        """Resolve the ownership chain (org/space/project) for a resource.

        This is the PIP query for Layers 2-4. Returns None when the resource
        doesn't exist or has no ownership chain (e.g. a container resource
        itself like Organization/Space/Project/Role).

        Option B fallback (design §0.5): definition-class resources
        (ObjectType/ActionType/...) with project_id NULL fall back to the
        Ontology's owning Space's default Project.
        """
        from ontology.core.models.datasource import DataSourceModel as DSModel
        from ontology.core.models.ontology import ObjectTypeModel, OntologyModel
        from ontology.core.models.permission import (
            ProjectModel,
            SpaceOrganizationModel,
        )
        # Try to find the resource's direct project_id + space_id.
        project_id: str | None = None
        space_id: str | None = None

        if resource_type in ("OBJECT_TYPE", "ACTION_TYPE", "LINK_TYPE", "INTERFACE_TYPE"):
            # Definition-class resources: look up via ObjectType (they share
            # the ontology_id + project_id pattern). For ACTION_TYPE etc.
            # we resolve via api_name → ObjectType is not directly applicable;
            # fall back to searching all ontologies for a matching resource.
            # Phase 1 simplification: OBJECT_TYPE is the primary case.
            if resource_type == "OBJECT_TYPE":
                stmt = select(ObjectTypeModel.project_id, ObjectTypeModel.ontology_id).where(
                    ObjectTypeModel.api_name == resource_id
                )
                row = (await self._session.execute(stmt)).first()
                if row is None:
                    return None
                project_id = row[0]
                ont_id = row[1]
                # Resolve space_id from the ontology.
                space_id = (
                    await self._session.execute(
                        select(OntologyModel.space_id).where(OntologyModel.id == ont_id)
                    )
                ).scalar_one_or_none()
                if space_id is None:
                    return None
            else:
                # ACTION_TYPE/LINK_TYPE: resolve via api_name on their tables.
                # Phase 1: treat as ontology-scoped — find the ontology that
                # contains this resource, then its Space.
                # Simplified: return None (no ownership chain) — platform admin
                # still manages these; non-admins deny. Full impl in Phase 2.
                return None
        elif resource_type == "ONTOLOGY":
            stmt = select(OntologyModel.id, OntologyModel.space_id).where(
                OntologyModel.api_name == resource_id
            )
            row = (await self._session.execute(stmt)).first()
            if row is None:
                return None
            space_id = row[1]
            if space_id is None:
                return None  # orphan ontology (pre-Phase-0)
        elif resource_type in ("DATASOURCE", "DATASET"):
            stmt = select(DSModel.project_id).where(DSModel.api_name == resource_id)
            project_id = (await self._session.execute(stmt)).scalar_one_or_none()
            if project_id is None:
                return None
        else:
            # Unknown resource type — no ownership chain.
            return None

        # Resolve space_id if we only have project_id.
        if space_id is None and project_id is not None:
            stmt = select(ProjectModel.space_id).where(ProjectModel.id == project_id)
            space_id = (await self._session.execute(stmt)).scalar_one_or_none()
            if space_id is None:
                return None

        # Option B fallback: project_id NULL → Space's default Project.
        if project_id is None and space_id is not None:
            project_id = await self._resolve_default_project_for_space(space_id)

        # Resolve the Space's org whitelist.
        org_ids: list[str] = []
        if space_id is not None:
            stmt = select(SpaceOrganizationModel.organization_id).where(
                SpaceOrganizationModel.space_id == space_id
            )
            org_ids = list((await self._session.execute(stmt)).scalars().all())

        if space_id is None:
            return None
        return ResourceOwnership(
            resource_type=resource_type, resource_id=resource_id,
            organization_ids=org_ids, space_id=space_id, project_id=project_id,
        )

    async def _resolve_default_project_for_space(
        self, space_id: str | None
    ) -> str | None:
        """Option A/B: resolve the default Project for a Space.

        - space_id given: the Space's default Project (api_name='default'),
          else the first Project under that Space.
        - space_id None: the first Project in the system (test compat).
        """
        from ontology.core.models.permission import ProjectModel

        if space_id is not None:
            stmt = (
                select(ProjectModel.id)
                .where(ProjectModel.space_id == space_id, ProjectModel.api_name == "default")
                .limit(1)
            )
            pid = (await self._session.execute(stmt)).scalar_one_or_none()
            if pid is not None:
                return pid
            stmt = select(ProjectModel.id).where(ProjectModel.space_id == space_id).limit(1)
            return (await self._session.execute(stmt)).scalar_one_or_none()
        stmt = select(ProjectModel.id).limit(1)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def resolve_effective_role_scopes(
        self, principal: Principal
    ) -> list[tuple[str, str]]:
        """Return (scope_type, scope_id) pairs for the principal's role assignments."""
        from ontology.core.models.permission import RoleAssignmentModel

        principal_ids = [principal.id] + list(principal.groups)
        if not principal_ids:
            return []
        stmt = (
            select(RoleAssignmentModel.scope_type, RoleAssignmentModel.scope_id)
            .where(RoleAssignmentModel.principal_id.in_(principal_ids))
            .where(RoleAssignmentModel.scope_id.isnot(None))
        )
        rows = (await self._session.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows if r[1] is not None]

    async def resolve_effective_roles_for_scope(
        self, principal: Principal, scope_id: str
    ) -> list[str]:
        """Return role names the principal holds at a given scope."""
        from ontology.core.models.permission import (
            ProjectModel,
            RoleAssignmentModel,
            RoleModel,
        )

        principal_ids = [principal.id] + list(principal.groups)
        if not principal_ids:
            return []
        space_id = (
            await self._session.execute(
                select(ProjectModel.space_id).where(ProjectModel.id == scope_id)
            )
        ).scalar_one_or_none()
        scope_ids = [scope_id]
        if space_id is not None:
            scope_ids.append(space_id)
        stmt = (
            select(RoleModel.name)
            .join(RoleAssignmentModel, RoleAssignmentModel.role_id == RoleModel.id)
            .where(RoleAssignmentModel.principal_id.in_(principal_ids))
            .where(RoleAssignmentModel.scope_id.in_(scope_ids))
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_role_permissions(self, role_name: str) -> list[str] | None:
        from ontology.core.models.permission import RoleModel

        stmt = select(RoleModel.permissions).where(RoleModel.name == role_name)
        result = (await self._session.execute(stmt)).scalar_one_or_none()
        return list(result) if result is not None else None

    async def count_organizations(self) -> int:
        from sqlalchemy import func

        from ontology.core.models.permission import OrganizationModel

        stmt = select(func.count()).select_from(OrganizationModel)
        return int((await self._session.execute(stmt)).scalar_one())

    # ── Role Assignment CRUD ──

    async def create_role_assignment(
        self, *, group_id: str, role_name: str, scope_type: str,
        scope_id: str | None, expires_at: datetime | None = None,
    ) -> "RoleAssignmentModel":
        from ontology.core.models.permission import (
            GroupModel,
            RoleAssignmentModel,
        )

        role = await self.get_role_by_name(role_name)
        if role is None:
            raise NotFoundError(f"Role '{role_name}' not found")
        group = (
            await self._session.execute(
                select(GroupModel).where(GroupModel.id == group_id)
            )
        ).scalar_one_or_none()
        if group is None:
            raise NotFoundError(f"Group '{group_id}' not found")
        assignment = RoleAssignmentModel(
            id=new_uuid(), principal_id=group_id, role_id=role.id,
            scope_type=scope_type, scope_id=scope_id, expires_at=expires_at,
        )
        self._session.add(assignment)
        await self._flush_and_commit()
        return assignment

    async def list_role_assignments(
        self, scope_id: str | None = None, group_id: str | None = None
    ) -> list[tuple["RoleAssignmentModel", str]]:
        from ontology.core.models.permission import RoleAssignmentModel, RoleModel

        stmt = (
            select(RoleAssignmentModel, RoleModel.name)
            .join(RoleModel, RoleModel.id == RoleAssignmentModel.role_id)
        )
        if scope_id is not None:
            stmt = stmt.where(RoleAssignmentModel.scope_id == scope_id)
        if group_id is not None:
            stmt = stmt.where(RoleAssignmentModel.principal_id == group_id)
        rows = (await self._session.execute(stmt)).all()
        return [(r[0], r[1]) for r in rows]

    async def delete_role_assignment(self, assignment_id: str) -> None:
        from ontology.core.models.permission import RoleAssignmentModel

        result = await self._session.execute(
            select(RoleAssignmentModel).where(RoleAssignmentModel.id == assignment_id)
        )
        assignment = result.scalar_one_or_none()
        if assignment is None:
            raise NotFoundError(f"Role assignment '{assignment_id}' not found")
        await self._session.delete(assignment)
        await self._flush_and_commit()

    # ── Marking (Phase 2) ──

    async def get_resource_markings(
        self, resource_type: str, resource_id: str
    ) -> list[str]:
        from ontology.core.models.permission import MarkingAssignmentModel, MarkingModel

        stmt = (
            select(MarkingModel.name)
            .join(
                MarkingAssignmentModel,
                MarkingAssignmentModel.marking_id == MarkingModel.id,
            )
            .where(
                MarkingAssignmentModel.resource_type == resource_type,
                MarkingAssignmentModel.resource_id == resource_id,
            )
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def resolve_principal_markings(self, principal: Principal) -> list[str]:
        from ontology.core.models.permission import MarkingGrantModel, MarkingModel

        principal_ids = [principal.id] + list(principal.groups)
        if not principal_ids:
            return []
        stmt = (
            select(MarkingModel.name)
            .join(MarkingGrantModel, MarkingGrantModel.marking_id == MarkingModel.id)
            .where(MarkingGrantModel.group_id.in_(principal_ids))
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_marking_category(
        self, name: str, description: str = ""
    ) -> "MarkingCategoryModel":
        from ontology.core.models.permission import MarkingCategoryModel

        cat = MarkingCategoryModel(
            id=new_uuid(), name=name, description=description, is_system=False,
        )
        self._session.add(cat)
        await self._flush_and_commit()
        return cat

    async def list_marking_categories(self) -> list:
        from ontology.core.models.permission import MarkingCategoryModel

        stmt = select(MarkingCategoryModel).order_by(MarkingCategoryModel.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_marking(
        self, category_id: str, name: str, display_name: str = "",
        description: str = "",
    ) -> "MarkingModel":
        from ontology.core.models.permission import MarkingModel

        marking = MarkingModel(
            id=new_uuid(), category_id=category_id, name=name,
            display_name=display_name or name, description=description,
            is_system=False,
        )
        self._session.add(marking)
        await self._flush_and_commit()
        return marking

    async def list_markings(self, category_id: str | None = None) -> list:
        from ontology.core.models.permission import MarkingModel

        stmt = select(MarkingModel)
        if category_id:
            stmt = stmt.where(MarkingModel.category_id == category_id)
        stmt = stmt.order_by(MarkingModel.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def grant_marking(
        self, marking_id: str, group_id: str,
        expires_at: datetime | None = None,
    ) -> None:
        from sqlalchemy import insert as sa_insert

        from ontology.core.models.permission import MarkingGrantModel

        try:
            await self._session.execute(
                sa_insert(MarkingGrantModel).values(
                    group_id=group_id, marking_id=marking_id, expires_at=expires_at,
                )
            )
            await self._flush_and_commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Marking already granted to this group") from exc

    async def assign_marking(
        self, resource_type: str, resource_id: str, marking_id: str,
    ) -> None:
        from sqlalchemy import insert as sa_insert

        from ontology.core.models.permission import MarkingAssignmentModel

        try:
            await self._session.execute(
                sa_insert(MarkingAssignmentModel).values(
                    resource_type=resource_type, resource_id=resource_id,
                    marking_id=marking_id, is_directly_applied=True,
                )
            )
            await self._flush_and_commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise ConflictError("Marking already applied to this resource") from exc

    async def revoke_marking_assignment(
        self, resource_type: str, resource_id: str, marking_id: str
    ) -> None:
        from ontology.core.models.permission import MarkingAssignmentModel

        await self._session.execute(
            delete(MarkingAssignmentModel).where(
                MarkingAssignmentModel.resource_type == resource_type,
                MarkingAssignmentModel.resource_id == resource_id,
                MarkingAssignmentModel.marking_id == marking_id,
            )
        )
        await self._flush_and_commit()

    async def get_row_security_policy(self, object_type_api_name: str) -> str | None:
        from ontology.core.models.ontology import ObjectTypeModel
        from ontology.core.models.permission import RowSecurityPolicyModel

        stmt = (
            select(RowSecurityPolicyModel.expression)
            .join(ObjectTypeModel, ObjectTypeModel.id == RowSecurityPolicyModel.object_type_id)
            .where(ObjectTypeModel.api_name == object_type_api_name)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_object_type_by_id(
        self, object_type_id: str
    ) -> tuple["ObjectTypeModel", str, str] | None:
        """Get an ObjectType by id, returning (model, space_id, project_id).

        Used by the option B→A migration API to resolve the current ownership
        chain (ObjectType → Ontology → Space → Project) and the target Project.
        Returns None when the ObjectType doesn't exist.
        """
        from ontology.core.models.ontology import ObjectTypeModel, OntologyModel

        stmt = (
            select(ObjectTypeModel, OntologyModel.space_id)
            .join(OntologyModel, OntologyModel.id == ObjectTypeModel.ontology_id)
            .where(ObjectTypeModel.id == object_type_id)
        )
        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        ot_model, space_id = row[0], row[1]
        return (ot_model, space_id, ot_model.project_id or "")

    async def update_object_type_project(
        self, object_type_id: str, target_project_id: str
    ) -> None:
        """Migrate an ObjectType to a different Project (option B→A, ADR-016 D3).

        Updates ``object_types.project_id``. The caller must verify the
        target Project exists and the caller has OWNER on both the current
        and target Projects (Palantir: "once migrated, cannot revert").
        """
        from sqlalchemy import update

        from ontology.core.models.ontology import ObjectTypeModel

        await self._session.execute(
            update(ObjectTypeModel)
            .where(ObjectTypeModel.id == object_type_id)
            .values(project_id=target_project_id, updated_at=utcnow())
        )
        await self._flush_and_commit()

    async def get_project(self, project_id: str) -> "ProjectModel | None":
        """Get a Project by id."""
        from ontology.core.models.permission import ProjectModel

        stmt = select(ProjectModel).where(ProjectModel.id == project_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_default_project_for_space(self, space_id: str) -> "ProjectModel | None":
        """Get the default Project for a Space (option B fallback target)."""
        from ontology.core.models.permission import ProjectModel

        stmt = (
            select(ProjectModel)
            .where(ProjectModel.space_id == space_id, ProjectModel.api_name == "default")
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_row_security_policy(
        self,
        *,
        object_type_id: str,
        expression: str,
        description: str = "",
        generated_by: str = "manual",
        generation_meta: dict[str, Any] | None = None,
    ) -> "RowSecurityPolicyModel":
        """Create a row security policy for an ObjectType.

        Raises ConflictError if an ACTIVE policy already exists for the
        ObjectType (one-active-policy-per-ObjectType constraint).
        """
        from ontology.core.models.permission import RowSecurityPolicyModel

        # Check for existing active policy (unique constraint).
        existing = await self._session.execute(
            select(RowSecurityPolicyModel).where(
                RowSecurityPolicyModel.object_type_id == object_type_id,
                RowSecurityPolicyModel.status == "ACTIVE",
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictError(
                "RowSecurityPolicy", f"object_type_id={object_type_id}"
            )
        model = RowSecurityPolicyModel(
            id=new_uuid(),
            object_type_id=object_type_id,
            expression=expression,
            description=description,
            status="ACTIVE",
            generated_by=generated_by,
            generation_meta=generation_meta or {},
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        self._session.add(model)
        await self._flush_and_commit()
        await self._session.refresh(model)
        return model

    async def list_row_security_policies(
        self, object_type_id: str | None = None
    ) -> list["RowSecurityPolicyModel"]:
        """List row security policies, optionally filtered by ObjectType."""
        from ontology.core.models.permission import RowSecurityPolicyModel

        stmt = select(RowSecurityPolicyModel).order_by(
            RowSecurityPolicyModel.created_at.desc()
        )
        if object_type_id is not None:
            stmt = stmt.where(RowSecurityPolicyModel.object_type_id == object_type_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_row_security_policy_by_id(self, policy_id: str) -> "RowSecurityPolicyModel | None":
        """Get a row security policy by its primary key."""
        from ontology.core.models.permission import RowSecurityPolicyModel

        stmt = select(RowSecurityPolicyModel).where(RowSecurityPolicyModel.id == policy_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def delete_row_security_policy(self, policy_id: str) -> None:
        """Delete a row security policy by id."""
        from ontology.core.models.permission import RowSecurityPolicyModel

        stmt = select(RowSecurityPolicyModel).where(RowSecurityPolicyModel.id == policy_id)
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            raise NotFoundError("RowSecurityPolicy", policy_id)
        await self._session.delete(model)
        await self._flush_and_commit()

    async def get_property_masking_policies(
        self, object_type_api_name: str
    ) -> list[tuple[str, str]]:
        """Return (property_api_name, expression) pairs for masked properties."""
        from ontology.core.models.ontology import ObjectTypeModel, PropertyDefModel
        from ontology.core.models.permission import PropertyMaskingPolicyModel

        stmt = (
            select(PropertyDefModel.api_name, PropertyMaskingPolicyModel.expression)
            .join(
                PropertyMaskingPolicyModel,
                PropertyMaskingPolicyModel.property_id == PropertyDefModel.id,
            )
            .join(ObjectTypeModel, ObjectTypeModel.id == PropertyDefModel.object_type_id)
            .where(ObjectTypeModel.api_name == object_type_api_name)
        )
        return list((await self._session.execute(stmt)).all())

    async def append_audit_log(
        self, *, principal_id: str | None, resource_type: str, resource_id: str,
        action: str, result: str, reason: str = "", layer: str | None = None,
        request_id: str | None = None,
    ) -> None:
        from ontology.core.models.permission import AuditLogModel

        log = AuditLogModel(
            id=new_uuid(), principal_id=principal_id, resource_type=resource_type,
            resource_id=resource_id, action=action, result=result, reason=reason,
            layer=layer, request_id=request_id,
        )
        self._session.add(log)
        await self._session.flush()  # best-effort — don't commit (caller decides)
        return log.id

    async def list_audit_logs(
        self, *, principal_id: str | None = None, resource_type: str | None = None,
        result: str | None = None, layer: str | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list:
        from ontology.core.models.permission import AuditLogModel

        stmt = select(AuditLogModel)
        if principal_id is not None:
            stmt = stmt.where(AuditLogModel.principal_id == principal_id)
        if resource_type is not None:
            stmt = stmt.where(AuditLogModel.resource_type == resource_type)
        if result is not None:
            stmt = stmt.where(AuditLogModel.result == result)
        if layer is not None:
            stmt = stmt.where(AuditLogModel.layer == layer)
        stmt = stmt.order_by(AuditLogModel.timestamp.desc()).limit(limit).offset(offset)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_access_request(self, request_id: str):
        from ontology.core.models.permission import AccessRequestModel

        stmt = select(AccessRequestModel).where(AccessRequestModel.id == request_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_access_requests(
        self, *, requester_id: str | None = None, status: str | None = None,
    ) -> list:
        from ontology.core.models.permission import AccessRequestModel

        stmt = select(AccessRequestModel)
        if requester_id is not None:
            stmt = stmt.where(AccessRequestModel.requester_id == requester_id)
        if status is not None:
            stmt = stmt.where(AccessRequestModel.status == status)
        stmt = stmt.order_by(AccessRequestModel.created_at.desc())
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_access_request(
        self, *, requester_id: str, request_type: str, requested_item: str,
        justification: str, scope_type: str | None = None, scope_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> "AccessRequestModel":
        from ontology.core.models.permission import AccessRequestModel

        req = AccessRequestModel(
            id=new_uuid(), requester_id=requester_id, request_type=request_type,
            requested_item=requested_item, justification=justification,
            scope_type=scope_type, scope_id=scope_id, status="PENDING",
            expires_at=expires_at,
        )
        self._session.add(req)
        await self._flush_and_commit()
        return req

    async def update_access_request_status(
        self, request_id: str, status: str, reviewer_id: str, review_comment: str = "",
    ) -> None:
        from ontology.core.models.permission import AccessRequestModel

        req = (
            await self._session.execute(
                select(AccessRequestModel).where(AccessRequestModel.id == request_id)
            )
        ).scalar_one_or_none()
        if req is None:
            raise NotFoundError(f"Access request '{request_id}' not found")
        req.status = status
        req.reviewer_id = reviewer_id
        req.review_comment = review_comment
        from ontology.core.models.defaults import utcnow
        req.reviewed_at = utcnow()
        await self._flush_and_commit()
        return req

    # ═══════════════════════════════════════════════════════════════════
    # Identity & Container CRUD (ADR-016 Phase 0/1 — design §7.2/§7.3)
    # ═══════════════════════════════════════════════════════════════════

    async def create_user(
        self, *, email: str, subject: str, attributes: dict[str, Any] | None = None,
        home_organization: str | None = None,
    ) -> "UserModel":
        """Create a User record (maps a Better Auth / OIDC user to Gaia)."""
        from ontology.core.models.permission import PrincipalModel, UserModel

        principal = PrincipalModel(
            id=new_uuid(), principal_type="USER", display_name=email, status="ACTIVE"
        )
        self._session.add(principal)
        await self._session.flush()
        user = UserModel(
            id=principal.id, email=email, subject=subject,
            attributes=attributes or {}, home_organization=home_organization,
        )
        self._session.add(user)
        await self._flush_and_commit()
        return user

    async def list_users(self) -> list["UserModel"]:
        from ontology.core.models.permission import UserModel

        stmt = select(UserModel).order_by(UserModel.created_at)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_user_by_subject(self, subject: str) -> "UserModel | None":
        from ontology.core.models.permission import UserModel

        stmt = select(UserModel).where(UserModel.subject == subject)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_user_by_email(self, email: str) -> "UserModel | None":
        from ontology.core.models.permission import UserModel

        stmt = select(UserModel).where(UserModel.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_identity_group(
        self, *, name: str, organization_id: str, description: str = "",
        parent_group_id: str | None = None,
    ) -> "GroupModel":
        """Create a Group (the sole permission carrier, 组授权铁律)."""
        from ontology.core.models.permission import GroupModel, PrincipalModel

        principal = PrincipalModel(
            id=new_uuid(), principal_type="GROUP", display_name=name, status="ACTIVE"
        )
        self._session.add(principal)
        await self._session.flush()
        group = GroupModel(
            id=principal.id, name=name, description=description,
            organization_id=organization_id, parent_group_id=parent_group_id,
        )
        self._session.add(group)
        await self._flush_and_commit()
        return group

    async def list_groups(self, organization_id: str | None = None) -> list["GroupModel"]:
        from ontology.core.models.permission import GroupModel

        stmt = select(GroupModel)
        if organization_id:
            stmt = stmt.where(GroupModel.organization_id == organization_id)
        stmt = stmt.order_by(GroupModel.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_group_by_name(
        self, name: str, organization_id: str
    ) -> "GroupModel | None":
        from ontology.core.models.permission import GroupModel

        stmt = select(GroupModel).where(
            GroupModel.name == name, GroupModel.organization_id == organization_id
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_group(self, group_id: str) -> "GroupModel | None":
        """Get a Group by id."""
        from ontology.core.models.permission import GroupModel

        stmt = select(GroupModel).where(GroupModel.id == group_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def add_group_member(self, *, group_id: str, user_id: str) -> None:
        """Add a User to a Group (idempotent — duplicate is a no-op)."""
        from sqlalchemy import insert as sa_insert

        from ontology.core.models.permission import GroupMembershipModel

        try:
            await self._session.execute(
                sa_insert(GroupMembershipModel).values(group_id=group_id, user_id=user_id)
            )
            await self._flush_and_commit()
        except IntegrityError as exc:
            await self._session.rollback()
            if "unique" in str(exc).lower() or "primary" in str(exc).lower():
                return  # already a member — idempotent
            raise

    async def remove_group_member(self, *, group_id: str, user_id: str) -> None:
        """Remove a User from a Group (personnel change → only touches membership)."""
        from ontology.core.models.permission import GroupMembershipModel

        await self._session.execute(
            delete(GroupMembershipModel).where(
                GroupMembershipModel.group_id == group_id,
                GroupMembershipModel.user_id == user_id,
            )
        )
        await self._flush_and_commit()

    async def list_group_members(self, group_id: str) -> list["UserModel"]:
        from ontology.core.models.permission import GroupMembershipModel, UserModel

        stmt = (
            select(UserModel)
            .join(GroupMembershipModel, GroupMembershipModel.user_id == UserModel.id)
            .where(GroupMembershipModel.group_id == group_id)
            .order_by(UserModel.email)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_user_groups(self, user_id: str) -> list["GroupModel"]:
        """Return all Groups a User belongs to."""
        from ontology.core.models.permission import GroupMembershipModel, GroupModel

        stmt = (
            select(GroupModel)
            .join(GroupMembershipModel, GroupMembershipModel.group_id == GroupModel.id)
            .where(GroupMembershipModel.user_id == user_id)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_organization(
        self, *, api_name: str, display_name: str, description: str = "",
        org_type: str = "INTERNAL",
    ) -> "OrganizationModel":
        from ontology.core.models.permission import OrganizationModel

        org = OrganizationModel(
            id=new_uuid(), api_name=api_name, display_name=display_name,
            description=description, org_type=org_type, status="ACTIVE",
        )
        self._session.add(org)
        await self._flush_and_commit()
        return org

    async def list_organizations(self) -> list["OrganizationModel"]:
        from ontology.core.models.permission import OrganizationModel

        stmt = select(OrganizationModel).order_by(OrganizationModel.api_name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_space(
        self, *, api_name: str, display_name: str, ontology_id: str,
        description: str = "",
    ) -> "SpaceModel":
        from ontology.core.models.permission import SpaceModel

        space = SpaceModel(
            id=new_uuid(), api_name=api_name, display_name=display_name,
            description=description, ontology_id=ontology_id, status="ACTIVE",
        )
        self._session.add(space)
        await self._flush_and_commit()
        return space

    async def list_spaces(self) -> list["SpaceModel"]:
        from ontology.core.models.permission import SpaceModel

        stmt = select(SpaceModel).order_by(SpaceModel.api_name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def create_project(
        self, *, api_name: str, display_name: str, space_id: str,
        description: str = "",
    ) -> "ProjectModel":
        from ontology.core.models.permission import ProjectModel

        project = ProjectModel(
            id=new_uuid(), api_name=api_name, display_name=display_name,
            description=description, space_id=space_id, status="ACTIVE",
        )
        self._session.add(project)
        await self._flush_and_commit()
        return project

    async def list_projects(self, space_id: str | None = None) -> list["ProjectModel"]:
        from ontology.core.models.permission import ProjectModel

        stmt = select(ProjectModel)
        if space_id:
            stmt = stmt.where(ProjectModel.space_id == space_id)
        stmt = stmt.order_by(ProjectModel.api_name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_space_org_whitelist(self, space_id: str) -> list[str]:
        from ontology.core.models.permission import SpaceOrganizationModel

        stmt = select(SpaceOrganizationModel.organization_id).where(
            SpaceOrganizationModel.space_id == space_id
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def add_space_org_whitelist(self, *, space_id: str, organization_id: str) -> None:
        from sqlalchemy import insert as sa_insert

        from ontology.core.models.permission import SpaceOrganizationModel

        try:
            await self._session.execute(
                sa_insert(SpaceOrganizationModel).values(
                    space_id=space_id, organization_id=organization_id
                )
            )
            await self._flush_and_commit()
        except IntegrityError:
            await self._session.rollback()  # already whitelisted — idempotent

    async def list_roles(self) -> list:
        from ontology.core.models.permission import RoleModel

        stmt = select(RoleModel).order_by(RoleModel.name)
        return list((await self._session.execute(stmt)).scalars().all())

    async def get_role_by_name(self, name: str):
        from ontology.core.models.permission import RoleModel

        stmt = select(RoleModel).where(RoleModel.name == name)
        return (await self._session.execute(stmt)).scalar_one_or_none()
