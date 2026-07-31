"""OntologyService — ontology CRUD orchestration.

Coordinates between Metadata (PostgreSQL), Catalog (Gravitino),
and Index (Doris) layers to manage the full ontology lifecycle.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from ontology.core.exceptions import ConflictError, IndexProvisionError, NotFoundError, ValidationError
from ontology.core.models.defaults import utcnow
from ontology.core.schemas.action import ActionTypeCreate
from ontology.core.schemas.ontology import (
    ActionType,
    ActionTypeSummary,
    BackingColumnRef,
    DataType,
    ImpactItem,
    ImpactReport,
    InterfaceType,
    LinkTypeDef,
    LinkTypeDefCreate,
    ObjectType,
    ObjectTypeBatchCreate,
    ObjectTypeCapabilities,
    ObjectTypeCreate,
    ObjectTypeFullMetadata,
    Ontology,
    OntologyCreate,
    OntologyFullMetadata,
    OntologyUpdate,
    PropertyDef,
    PropertyDefCreate,
    SharedProperty,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.index.doris_index_store import DorisIndexStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services._metadata_owner import MetadataOwnerMixin
from ontology.services.index_sync_service import IndexSyncService

if TYPE_CHECKING:
    from ontology.config.container import Container
    from ontology.layers.dataset.iceberg_store import IcebergStore


@dataclass
class _DerivedProp:
    """Intermediate property representation carrying the service-derived
    api_name plus the caller's flags, used during batch create/update so
    primary_key/title_property resolution and ORM construction can read the
    derived api_name alongside the flags (Q2).
    """

    api_name: str
    display_name: str
    description: str
    data_type: str
    searchable: bool
    is_primary_key: bool | None
    is_title_property: bool | None
    backing_mapping: Any  # BackingColumnRef | None
    # §14.4: VECTOR 属性的配置 (透传 PropertyInput.vector_config)。
    vector_config: Any = None  # VectorPropertyConfig | None


class OntologyService(MetadataOwnerMixin):
    """Ontology lifecycle management."""

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry | None,
        index: DorisIndexStore | None,
        index_sync: IndexSyncService | None = None,
        *,
        container: "Container | None" = None,
        dataset: "IcebergStore | None" = None,
    ) -> None:
        self._metadata = metadata
        # catalog/index lite 装配传 None（lite 只做 VIRTUAL，define_object_type
        # 入口已 guard 拦截 MANAGED，不触达 _provision_index 等物理注册路径）。
        # 存到 _val，下方 property 窄化回非 None 让 full 路径调用点 mypy 不报
        # union-attr；lite 下访问即走错路径，assert 兜底。
        self._catalog_val = catalog
        self._index_val = index
        # IndexSyncService orchestrates Doris index table DDL + the SeaTunnel
        # INDEX_SYNC pipeline. Optional for backward compat / isolated tests;
        # when None, define/update fall back to the legacy best-effort
        # create_index_table(fields=[]) call so the ObjectType still creates.
        self._index_sync = index_sync
        # Optional container reference so define_action_type_full can resolve
        # the shared ActionService (M4: avoid `new ActionService(dataset=None)`
        # + type: ignore inside a method body). Null in tests/legacy paths.
        self._container = container
        # Catalog First: IcebergStore for managed-table provisioning. Optional
        # for tests; when None, lazily resolved from the DI container.
        self._dataset_override = dataset

    @property
    def _catalog(self) -> GravitinoRegistry:
        """Gravitino catalog。lite 装配为 None——访问即说明走了 MANAGED 物理
        注册路径（lite define_object_type 入口已 guard 拦截），assert 兜底。"""
        assert self._catalog_val is not None, "catalog 未装配（lite 版不应触达 Gravitino 路径）"
        return self._catalog_val

    @property
    def _index(self) -> DorisIndexStore:
        """Doris index store。lite 装配为 None——访问即走错路径，assert 兜底。"""
        assert self._index_val is not None, "index 未装配（lite 版不应触达 Doris 路径）"
        return self._index_val

    @property
    def _dataset(self) -> "IcebergStore":
        """Lazy IcebergStore for Catalog First managed-table provisioning.

        OntologyService historically only needed the Gravitino catalog
        (``self._catalog``) for table registration. Catalog First moves
        managed-table creation to ``IcebergStore.create_managed_table``
        (pyiceberg path — supports PK identifiers, column comments, NULL),
        so we lazily resolve the shared IcebergStore from the DI container.
        """
        if self._dataset_override is not None:
            return self._dataset_override
        if self._container is None:
            from ontology.config.container import Container

            self._container = Container()
        return self._container.dataset

    # ── Catalog First: managed Iceberg table provisioning ──

    async def _provision_managed_table_for_object_type(
        self,
        ot_api_name: str,
        *,
        display_name: str,
        description: str,
        properties: Sequence[object],
        primary_key: str,
    ) -> None:
        """Create/reconcile the managed Iceberg table for a MANAGED ObjectType.

        Catalog First: the physical Iceberg table is created by Gaia via the
        Gravitino/Iceberg catalog (pyiceberg), carrying the ObjectType's
        primary-key identifier, per-column comments (property description),
        and NOT-NULL constraints. Replaces the legacy
        ``GravitinoRegistry.register_dataset`` call that only wrote a bare
        PK column with no comments/properties.

        ``properties`` may be empty (single-type ``define_object_type`` —
        properties arrive later via ``add_property``, which calls
        :meth:`IcebergStore.ensure_schema` to evolve). In that case only
        the PK column is registered now; the table comment carries the
        ObjectType description so downstream readers see provenance.

        Best-effort: failures are logged and swallowed so an Iceberg/Gravitino
        outage does not block ontology modeling (queries degrade to Trino
        once the table materializes on retry).
        """
        import logging

        from ontology.core.naming import managed_dataset_api_name
        from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

        _log = logging.getLogger(__name__)
        dataset_api_name = managed_dataset_api_name(ot_api_name)

        columns: list[ManagedColumnDef] = []
        for prop in properties:
            # Physical Iceberg column = backing_column (snake_case) when the
            # property has an explicit backing mapping, else the property
            # api_name (callers without backing_mapping rely on the default
            # identity mapping; define_object_type_batch always sets one).
            backing = getattr(prop, "backing_mapping", None)
            col_name = backing.backing_column if backing and backing.backing_column else getattr(prop, "api_name", "")
            if not col_name:
                continue
            columns.append(
                ManagedColumnDef(
                    name=col_name,
                    type=str(getattr(prop, "data_type", "string")).lower(),
                    nullable=getattr(prop, "nullable", True),
                    comment=getattr(prop, "description", "") or "",
                    is_primary_key=bool(getattr(prop, "is_primary_key", False)),
                )
            )

        # Single-type define path: no properties yet, register just the PK.
        if not columns:
            columns.append(
                ManagedColumnDef(
                    name=primary_key,
                    type="string",
                    nullable=False,
                    comment=f"{display_name} 主键",
                    is_primary_key=True,
                )
            )

        schema = ManagedTableSchema(
            columns=columns,
            table_comment=description or display_name,
        )
        properties_dict = {
            "gaia.object-type": ot_api_name,
            "gaia.kind": "object-type",
        }
        try:
            await self._dataset.create_managed_table(dataset_api_name, schema, properties=properties_dict)
        except Exception as exc:
            _log.warning(
                "Managed table provisioning deferred for ObjectType '%s': %s",
                ot_api_name,
                exc,
            )

    # ── Index provisioning helper ──

    async def _provision_index(
        self,
        ontology_api_name: str,
        api_name: str,
        properties: Sequence[object],
        *,
        rebuild: bool = False,
        primary_key: str | None = None,
    ) -> None:
        """Best-effort Doris index provisioning for a MANAGED ObjectType.

        Delegates to IndexSyncService when wired; otherwise falls back to the
        legacy create_index_table(fields=[]) call. Never raises — provisioning
        failures are logged and the ObjectType CRUD still succeeds, so a Doris
        outage cannot block ontology modeling. Queries degrade to Trino until
        a retry succeeds.

        ``ontology_api_name`` drives the namespaced Doris table
        (``idx_{ontology}__{type}``) and INDEX pipeline name
        (``index_{ontology}__{type}``) for cross-ontology isolation (v5.2).
        """
        import logging

        _log = logging.getLogger(__name__)
        try:
            if self._index_sync is not None:
                if rebuild:
                    await self._index_sync.rebuild(ontology_api_name, api_name, properties, primary_key=primary_key)
                else:
                    await self._index_sync.provision(ontology_api_name, api_name, properties, primary_key=primary_key)
            else:
                await self._index.create_index_table(
                    ontology_api_name=ontology_api_name,
                    object_type_api_name=api_name,
                    fields=[],
                )
        except IndexProvisionError as e:
            _log.warning(f"Doris index provisioning deferred for {ontology_api_name}.{api_name}: {e}")
        except Exception as e:
            _log.warning(f"Doris index creation deferred for {ontology_api_name}.{api_name}: {e}")

    async def _deprovision_index(self, ontology_api_name: str, api_name: str) -> None:
        """Best-effort teardown of the Doris index table + INDEX pipeline."""
        import logging

        _log = logging.getLogger(__name__)
        if self._index_sync is None:
            return
        try:
            await self._index_sync.deprovision(ontology_api_name, api_name)
        except Exception as e:
            _log.warning(f"Doris index deprovision deferred for {ontology_api_name}.{api_name}: {e}")

    async def _provision_graph_schema(
        self,
        ontology_api_name: str,
        api_name: str,
        properties: Sequence[object],
    ) -> None:
        """Best-effort Neo4j graph schema provisioning (graph-reasoning-design.md §4.2).

        为 ObjectType 创建节点标签（vid 唯一约束）+ indexed 属性 B-tree 索引。
        Never raises — graph schema 失败不阻塞本体建模（Neo4j profile=graph
        可能未启动，两条线独立 C5/C12）。投影数据后续由 GraphProjector 写入。
        """
        import logging

        _log = logging.getLogger(__name__)
        if self._container is None:
            return
        try:
            store = self._container.graph_store
            await store.create_label(ontology_api_name, api_name)
            for p in properties:
                # _DerivedProp 用 searchable，PropertyDef 用 indexed（同一语义）。
                is_indexed = getattr(p, "indexed", None) or getattr(p, "searchable", False)
                if is_indexed:
                    await store.create_indexed_property_index(ontology_api_name, api_name, p.api_name)
        except Exception as e:
            _log.warning(f"Neo4j graph schema provisioning deferred for {ontology_api_name}.{api_name}: {e}")

    async def _provision_geotime_schema(
        self,
        ontology_api_name: str,
        api_name: str,
        properties: Sequence[object],
    ) -> None:
        """Best-effort PostGIS/TimescaleDB schema provisioning (§5.2, §5.3).

        为含空间属性（GEOPOINT/GEOSHAPE）的 ObjectType 创建 PostGIS 空间表；
        为含时序属性（GEOTEMPORAL_SERIES/TIME_SERIES）的 ObjectType 创建
        TimescaleDB 超表。Never raises — 失败不阻塞本体建模。
        """
        import logging

        _log = logging.getLogger(__name__)
        if self._container is None:
            return
        try:
            from ontology.core.schemas.ontology import SPATIAL_DATA_TYPES, TIMESERIES_DATA_TYPES

            store = self._container.geotime_store
            # indexed 剪枝字段：排除空间属性本身（几何列不重复加，避免 DuplicateColumn）。
            indexed_fields = [
                p.api_name
                for p in properties
                if (getattr(p, "indexed", None) or getattr(p, "searchable", False))
                and getattr(p, "data_type", None) not in SPATIAL_DATA_TYPES
            ]
            for p in properties:
                dt = getattr(p, "data_type", None)
                if dt in SPATIAL_DATA_TYPES:
                    await store.create_geo_table(ontology_api_name, api_name, str(dt), indexed_fields)
                    break  # 一个空间表（MVP 单几何）；多几何留二期
            for p in properties:
                dt = getattr(p, "data_type", None)
                if dt in TIMESERIES_DATA_TYPES:
                    has_position = dt == "GEOTEMPORAL_SERIES"
                    await store.create_timeseries_hypertable(ontology_api_name, api_name, p.api_name, has_position)
        except Exception as e:
            _log.warning(f"GeoTime schema provisioning deferred for {ontology_api_name}.{api_name}: {e}")

    @staticmethod
    def _managed_backing_ref(ot_api_name: str) -> BackingColumnRef:
        """Build the backing-column ref for a MANAGED ObjectType's own dataset.

        A MANAGED ObjectType owns a dataset whose api_name is the lower-cased
        form of the ObjectType's PascalCase api_name (``Flight`` → ``flight``)
        and whose physical locator is ``iceberg.<iceberg_namespace>.<dataset>``
        (per ADR-007 / dataset-ontology-binding.md §4.6). The dataset api_name
        doubles as the physical Iceberg table name, which is stored lower-cased
        so it round-trips through Trino's iceberg REST client. The per-property
        ``backing_column`` is the property's own api_name (the logical column
        name on the managed table) and is filled in by the caller.
        """
        from ontology.config.settings import settings
        from ontology.core.naming import managed_dataset_api_name

        dataset_api_name = managed_dataset_api_name(ot_api_name)
        return BackingColumnRef(
            dataset_api_name=dataset_api_name,
            backing_catalog="iceberg",
            backing_schema=settings.iceberg_namespace,
            backing_table=dataset_api_name,
            backing_column="",  # filled in per-property by the caller
        )

    async def _register_managed_dataset_governance(self, *, ot_api_name: str, display_name: str) -> None:
        """Write the PG datasets governance record for a MANAGED ObjectType.

        Mirrors ``datasource_service.register_virtual_table`` for the VIRTUAL
        side: a MANAGED ObjectType definition owns a dataset whose api_name is
        the lower-cased form of the ObjectType api_name, so we persist a
        ``DatasetGovernance(kind="MANAGED")`` row alongside the Gravitino/Doris
        registration. ``create_dataset`` is idempotent (returns the existing
        record on collision), so this is safe to call on retries and
        re-definitions.
        """
        from ontology.core.naming import managed_dataset_api_name
        from ontology.core.schemas.datasource import DatasetGovernanceCreate

        dataset_api_name = managed_dataset_api_name(ot_api_name)
        await self._metadata.create_dataset(
            DatasetGovernanceCreate(
                api_name=dataset_api_name,
                display_name=display_name,
                storage_location=f"s3://ontology-warehouse/{dataset_api_name}",
                kind="MANAGED",
                is_view=False,
            )
        )

    @staticmethod
    def _resolve_pk_title_flags(
        prop_api_name: str,
        primary_key: str,
        title_property: str,
        explicit_pk: bool | None,
        explicit_title: bool | None,
    ) -> tuple[bool, bool]:
        """Resolve is_primary_key / is_title_property for a property.

        Explicit caller values win; otherwise derive by matching the
        property's ``api_name`` against the ObjectType's ``primary_key`` /
        ``title_property``. Ensures the PK/title semantics are persisted on
        the property rows even when the API client omits the flags (the
        common case for the batch create/update payload).
        """
        is_pk = explicit_pk if explicit_pk is not None else (prop_api_name == primary_key)
        is_title = explicit_title if explicit_title is not None else (prop_api_name == title_property)
        return is_pk, is_title

    async def _derive_unique_api_name(
        self,
        display_name: str,
        *,
        backing_column: str | None = None,
        fallback_prefix: str,
        pascal: bool,
        existing_api_names: list[str],
    ) -> str:
        """Derive an api_name from display_name/backing_column, guaranteeing uniqueness.

        Wraps ``core.naming.derive_api_name``. Uniqueness is enforced by
        appending an incrementing numeric suffix when the derived name
        collides with an existing one (``Model`` → ``Model1`` → ``Model2``),
        per the Palantir apiName spec (重名兜底: 末尾自增数字后缀). This is
        the derivation path only — a *user-typed* duplicate api_name on an
        ObjectType/Action is a ConflictError raised by the caller.
        """
        from ontology.core.naming import derive_api_name

        existing_count = sum(
            1 for n in existing_api_names if n.startswith(fallback_prefix) and n[len(fallback_prefix) :].isdigit()
        )
        candidate = derive_api_name(
            display_name,
            backing_column=backing_column,
            fallback_prefix=fallback_prefix,
            existing_count=existing_count,
            pascal=pascal,
        )
        # Guarantee uniqueness: append an incrementing numeric suffix when the
        # derived name collides (covers both real derived names and fallback
        # prefixN). Per Palantir spec — derivation collisions are auto-suffixed,
        # not surfaced as errors.
        if candidate in existing_api_names:
            base = candidate
            n = 1
            while candidate in existing_api_names:
                candidate = f"{base}{n}"
                n += 1
        return candidate

    async def _resolve_link_api_name(
        self,
        submitted: str | None,
        display_name: str,
        existing_api_names: list[str],
    ) -> str:
        """Resolve a Link api_name that may be caller-supplied or derived.

        Mirrors the ObjectType/Action pattern (caller-supplied + AI assist):
        - If ``submitted`` is given, validate it against the camelCase pattern
          and check uniqueness; a duplicate is a ConflictError (NOT
          auto-suffixed — a user-typed name colliding is a real conflict).
          A pattern violation is a ValidationError.
        - If ``submitted`` is None, fall back to derivation via
          ``_derive_unique_api_name`` (auto-suffixed on collision).
        """
        import re

        from ontology.core.exceptions import ValidationError
        from ontology.core.naming import PROPERTY_API_NAME_PATTERN

        if submitted is not None:
            if not re.match(PROPERTY_API_NAME_PATTERN, submitted):
                raise ValidationError(f"Link api_name '{submitted}' does not match pattern {PROPERTY_API_NAME_PATTERN}")
            if submitted in existing_api_names:
                raise ConflictError(f"Link api_name '{submitted}' already exists")
            return submitted
        return await self._derive_unique_api_name(
            display_name,
            fallback_prefix="linkType",
            pascal=False,
            existing_api_names=existing_api_names,
        )

    async def _resolve_property_api_name(
        self,
        submitted: str | None,
        display_name: str,
        backing_column: str | None,
        existing_api_names: list[str],
    ) -> str:
        """Resolve a Property api_name that may be caller-supplied or derived.

        Same caller-supplied-vs-derived contract as ``_resolve_link_api_name``
        (and ObjectType/Action): a submitted api_name is validated against the
        camelCase pattern and checked for uniqueness (duplicate → ConflictError,
        NOT auto-suffixed); pattern violation → ValidationError. When omitted,
        derivation uses ``display_name`` → ``backing_column`` → ``propertyN``
        fallback (auto-suffixed on collision). The ``backing_column`` path is
        what lets Chinese-display-name properties with a physical column still
        derive a meaningful camelCase api_name (e.g. "门店ID" + store_code →
        storeCode); caller-supplied api_name covers the no-backing case (MVP
        master data like product_capability whose displayName is Chinese and
        has no physical column).
        """
        import re

        from ontology.core.exceptions import ValidationError
        from ontology.core.naming import PROPERTY_API_NAME_PATTERN

        if submitted is not None:
            if not re.match(PROPERTY_API_NAME_PATTERN, submitted):
                raise ValidationError(
                    f"Property api_name '{submitted}' does not match pattern {PROPERTY_API_NAME_PATTERN}"
                )
            if submitted in existing_api_names:
                raise ConflictError(f"Property api_name '{submitted}' already exists")
            return submitted
        return await self._derive_unique_api_name(
            display_name,
            backing_column=backing_column,
            fallback_prefix="property",
            pascal=False,
            existing_api_names=existing_api_names,
        )

    @staticmethod
    def _resolve_pk_title_from_properties(
        properties: Sequence[object],
        primary_key: str | None,
        title_property: str | None,
    ) -> tuple[str, str]:
        """Resolve ObjectType primary_key / title_property (Q2).

        Authority order:
          1. Explicit ``primary_key`` / ``title_property`` on the ObjectType —
             treated as a reference (api_name or display_name) and matched
             against the derived property api_names.
          2. Property ``is_primary_key`` / ``is_title_property`` flags — the
             authoritative source.
          3. First property's api_name as a last-resort default.

        Callers pass ``properties`` as a sequence of objects exposing
        ``api_name``, ``display_name``, ``is_primary_key``, ``is_title_property``.
        """
        props = list(properties)
        if not props:
            # No properties yet (single-type define without batch); caller
            # must supply primary_key explicitly — validated upstream.
            return primary_key or "", title_property or ""

        def _match(ref: str | None) -> str:
            if not ref:
                return ""
            for p in props:
                if getattr(p, "api_name", None) == ref or getattr(p, "display_name", None) == ref:
                    return str(getattr(p, "api_name"))
            return ""

        pk = _match(primary_key)
        if not pk:
            pk = next((str(getattr(p, "api_name")) for p in props if getattr(p, "is_primary_key", False)), "")
        if not pk:
            pk = str(getattr(props[0], "api_name", ""))

        title = _match(title_property)
        if not title:
            title = next((str(getattr(p, "api_name")) for p in props if getattr(p, "is_title_property", False)), pk)
        if not title:
            title = pk
        return pk, title

    # ── Ontology ──

    async def create_ontology(self, data: OntologyCreate) -> Ontology:
        """Create a new Ontology container."""
        now = utcnow()
        return await self._metadata.create_ontology(
            Ontology(
                id="",
                api_name=data.api_name,
                display_name=data.display_name,
                description=data.description,
                rid="",
                created_at=now,
                updated_at=now,
            )
        )

    async def get_ontology(self, api_name: str, *, include_non_active: bool = False) -> Ontology:
        """Get an Ontology by api_name."""
        return await self._metadata.get_ontology(api_name, include_non_active=include_non_active)

    async def list_ontologies(
        self, *, include_non_active: bool = False, include_deprecated: bool = False
    ) -> list[Ontology]:
        """List all ontologies. See MetaStore.list_ontologies for visibility tiers."""
        result: list[Ontology] = await self._metadata.list_ontologies(
            include_non_active=include_non_active, include_deprecated=include_deprecated
        )
        return result

    async def list_ontologies_with_counts(
        self, *, include_non_active: bool = False, include_deprecated: bool = False
    ) -> list[tuple[Any, int]]:
        """List ontologies with object-type counts (for the sidebar/list route)."""
        return await self._metadata.list_ontologies_with_counts(
            include_non_active=include_non_active, include_deprecated=include_deprecated
        )

    async def update_ontology(self, api_name: str, data: OntologyUpdate) -> Ontology:
        """Update an Ontology.

        v5.2: ``status`` is mutable here — PATCH {"status":"DEPRECATED"} is the
        Deprecate precondition for soft-delete (design §5.5).
        """
        return await self._metadata.update_ontology(
            api_name,
            display_name=data.display_name,
            description=data.description,
            status=data.status,
        )

    async def delete_ontology(self, api_name: str) -> None:
        """Soft-delete an Ontology and all its children (v5.2).

        Precondition: the ontology must be DEPRECATED (design §5.3 / §6.3).
        Within one PG transaction, sets ``deleted_at`` on the ontology and
        every ObjectType / LinkType / ActionType under it. After the commit,
        best-effort tears down the Doris idx tables + INDEX pipelines for each
        MANAGED ObjectType (decision 10: Iceberg tables and ``datasets`` rows
        are NOT touched). Physical cleanup failure is logged, not raised — the
        soft-delete has already succeeded in PG.
        """
        import logging

        _log = logging.getLogger(__name__)
        # Fetch including non-active so we can validate status on a DEPRECATED
        # ontology (which the default filter would hide as 404).
        onto = await self._metadata.get_ontology(api_name, include_non_active=True)
        if onto.status != "DEPRECATED":
            raise ConflictError(f"Ontology {api_name} is {onto.status}; Deprecate it before deleting.")
        if onto.deleted_at is not None:
            raise ConflictError(f"Ontology {api_name} is already soft-deleted.")

        # Best-effort physical teardown BEFORE marking deleted — we still need
        # the ObjectType api_names to target. Use include_non_active so we see
        # the soon-to-be-deleted types. (Design §九.1 step 3.)
        try:
            ots = await self.list_object_types(api_name, include_non_active=True)
        except NotFoundError:
            ots = []
        for ot in ots:
            if ot.storage_type == "MANAGED":
                try:
                    await self._deprovision_index(api_name, ot.api_name)
                except Exception as exc:
                    _log.warning(
                        "Doris index deprovision deferred for %s.%s during ontology delete: %s",
                        api_name,
                        ot.api_name,
                        exc,
                    )

        # PG soft-delete (cascades to children in one transaction).
        await self._metadata.delete_ontology(api_name)

    async def restore_ontology(self, api_name: str) -> Ontology:
        """Reverse a soft-delete (v5.2, design §七.3).

        Clears ``deleted_at`` on the ontology and all children. ``status``
        stays DEPRECATED — re-activation is a separate PATCH. Physical
        resources (Doris idx tables, INDEX pipelines) are NOT re-provisioned.
        """
        return await self._metadata.restore_ontology(api_name)

    async def get_ontology_impact(self, api_name: str) -> ImpactReport:
        """Build the cascade-impact report for the delete confirm dialog (v5.2 §六)."""
        data = await self._metadata.get_ontology_impact(api_name)
        status = data["status"]
        managed = data["managed_object_type_count"]
        impacts = [
            ImpactItem(
                resource_type="object_type",
                count=data["object_type_count"],
                label=f"{data['object_type_count']} 个对象类型（含 {data['property_count']} 个属性）",
            ),
            ImpactItem(
                resource_type="link_type",
                count=data["link_type_count"],
                label=f"{data['link_type_count']} 个关系类型",
            ),
            ImpactItem(
                resource_type="action_type",
                count=data["action_type_count"],
                label=f"{data['action_type_count']} 个动作定义",
            ),
            ImpactItem(
                resource_type="object_instance",
                count=data["object_instance_count"],
                label=f"{data['object_instance_count']} 条对象实例数据",
            ),
            ImpactItem(
                resource_type="link_instance",
                count=data["link_instance_count"],
                label=f"{data['link_instance_count']} 条关系实例数据",
            ),
            ImpactItem(
                resource_type="doris_index_table",
                count=managed,
                label=f"{managed} 张 Doris 索引表（将 drop）",
            ),
            ImpactItem(
                resource_type="index_pipeline",
                count=managed,
                label=f"{managed} 条 INDEX 同步管道（将停止）",
            ),
        ]
        can_delete = status == "DEPRECATED"
        blocked_reason = None if can_delete else "本体状态为 ACTIVE，请先弃用（Deprecate）"
        return ImpactReport(
            api_name=api_name,
            status=status,
            impacts=impacts,
            can_delete=can_delete,
            blocked_reason=blocked_reason,
        )

    # ── ObjectType ──

    async def define_object_type(self, ontology_api_name: str, data: ObjectTypeCreate) -> ObjectType:
        """Define a new ObjectType.

        ``api_name`` is derived from ``display_name`` (PascalCase). Single-type
        create carries no properties (they arrive later via ``add_property``),
        so ``primary_key`` / ``title_property`` MUST be supplied explicitly —
        there are no property flags to derive them from yet. Use
        ``define_object_type_batch`` for the property-flag-driven path.

        MANAGED types also register in Gravitino and create Doris index.
        VIRTUAL types skip physical registration.
        """
        # lite 版不做托管表（MANAGED 需 Gravitino+Iceberg+Doris，红线下砍）。
        # 在入口拦截，避免后续 MANAGED 分支触达 self._catalog/_index/_dataset
        # （lite 装配这些为 None）。VIRTUAL 是 lite 唯一支持的 storage_type。
        from ontology.config.settings import settings
        from ontology.core.exceptions import EditionUnavailableError

        if settings.edition == "lite" and data.storage_type == "MANAGED":
            raise EditionUnavailableError(
                "lite 版不支持托管表（MANAGED），请使用虚拟表（VIRTUAL）",
                code="EDITION_UNAVAILABLE",
            )
        if not data.primary_key:
            raise ValidationError(
                "define_object_type requires primary_key (use define_object_type_batch "
                "to derive it from property flags)"
            )
        onto = await self._metadata.get_ontology(ontology_api_name)

        # ADR-016 option A: resolve project_id (caller-supplied or Space default).
        project_id = data.project_id
        if project_id is None:
            project_id = await self._metadata._resolve_default_project_for_space(onto.space_id)  # noqa: SLF001
        if project_id is None:
            raise ValidationError(
                f"Cannot resolve a Project for Ontology '{ontology_api_name}' "
                "(option A: Ontology must be bound to a Space with a Project)"
            )

        # Prevent duplicate api_name within same ontology (user-typed duplicate → Conflict).
        try:
            await self._metadata.get_object_type(ontology_api_name, data.api_name)
            raise ConflictError(f"ObjectType '{data.api_name}' already exists in this ontology")
        except NotFoundError:
            pass

        title_property = data.title_property or data.primary_key
        now = utcnow()
        ot = await self._metadata.create_object_type(
            ObjectType(
                id="",
                ontology_id=onto.id,
                api_name=data.api_name,
                display_name=data.display_name,
                description=data.description,
                primary_key=data.primary_key,
                title_property=title_property,
                storage_type=data.storage_type,
                visibility=data.visibility,
                status=data.status,
                project_id=project_id,
                capabilities=data.capabilities,
                properties=[],
                links=[],
                created_at=now,
                updated_at=now,
            )
        )
        if data.storage_type == "MANAGED":
            import logging

            _log = logging.getLogger(__name__)
            # Catalog First: create the managed Iceberg table via Gaia
            # (IcebergStore.create_managed_table) with full physical metadata.
            # Legacy path called GravitinoRegistry.register_dataset (bare HTTP,
            # PK column only, no comments/properties).
            await self._provision_managed_table_for_object_type(
                data.api_name,
                display_name=data.display_name,
                description=data.description,
                properties=[],
                primary_key=data.primary_key,
            )
            await self._provision_index(ontology_api_name, data.api_name, [], primary_key=data.primary_key)
            # Graph-reasoning: Neo4j 图 schema 只在用户显式启用 graph_indexing_enabled
            # 时创建（Gate 4，ADR-015 §capabilities）。未启用则跳过，避免无谓 schema 开销。
            # best-effort：Neo4j 未启动不阻塞本体建模（两条线独立 C5/C12）。
            if data.capabilities.graph_indexing_enabled:
                await self._provision_graph_schema(ontology_api_name, data.api_name, [])
            # Graph-reasoning GeoTime: PostGIS/TimescaleDB schema 只在用户显式启用
            # geotime_indexing_enabled 时创建（Gate 4）。best-effort。
            if data.capabilities.geotime_indexing_enabled:
                await self._provision_geotime_schema(ontology_api_name, data.api_name, [])
            # Write the PG datasets governance record so the dataset is visible
            # on the data-connections page and link_dataset can resolve it.
            # create_dataset is idempotent (returns existing record on collision).
            await self._register_managed_dataset_governance(
                ot_api_name=data.api_name,
                display_name=data.display_name,
            )
        return ot

    async def define_object_type_batch(self, ontology_api_name: str, data: ObjectTypeBatchCreate) -> ObjectType:
        """Create an ObjectType with properties and links in a single transaction."""
        import logging

        from ontology.core.models.defaults import new_uuid, utcnow
        from ontology.core.models.ontology import (
            LinkTypeModel,
            ObjectTypeModel,
            PropertyDefModel,
        )

        _log = logging.getLogger(__name__)

        onto = await self._metadata.get_ontology(ontology_api_name)

        # ADR-016 option A: resolve project_id (Space default fallback).
        project_id = await self._metadata._resolve_default_project_for_space(onto.space_id)  # noqa: SLF001
        if project_id is None:
            raise ValidationError(
                f"Cannot resolve a Project for Ontology '{ontology_api_name}' "
                "(option A: Ontology must be bound to a Space with a Project)"
            )

        # ── apiName: ObjectType api_name is caller-supplied (PascalCase). ──
        ot_api_name = data.api_name
        # Prevent duplicate api_name (user-typed duplicate → Conflict).
        try:
            await self._metadata.get_object_type(ontology_api_name, ot_api_name)
            raise ConflictError(f"ObjectType '{ot_api_name}' already exists")
        except NotFoundError:
            pass

        now = utcnow()
        ot_id = new_uuid()

        # Property api_names: caller-supplied (camelCase) or derived from
        # display_name/backing_column. Build lightweight intermediate objects
        # carrying the resolved api_name + the caller's flags so
        # _resolve_pk_title_from_properties can read them.
        derived_props: list[_DerivedProp] = []
        existing_prop_names: list[str] = []
        for prop in data.properties:
            prop_api_name = await self._resolve_property_api_name(
                prop.api_name,
                prop.display_name,
                backing_column=prop.backing_mapping.backing_column if prop.backing_mapping else None,
                existing_api_names=existing_prop_names,
            )
            existing_prop_names.append(prop_api_name)
            derived_props.append(
                _DerivedProp(
                    api_name=prop_api_name,
                    display_name=prop.display_name,
                    description=prop.description,
                    data_type=prop.data_type,
                    searchable=prop.searchable,
                    is_primary_key=prop.is_primary_key,
                    is_title_property=prop.is_title_property,
                    backing_mapping=prop.backing_mapping,
                    vector_config=prop.vector_config,
                )
            )

        # Resolve primary_key / title_property (Q2): property flags are
        # authoritative; explicit ObjectType fields act as api_name/display_name
        # references matched against the derived property api_names.
        primary_key, title_property = self._resolve_pk_title_from_properties(
            derived_props, data.primary_key, data.title_property
        )
        if not primary_key:
            raise ValidationError(
                f"ObjectType '{ot_api_name}' has no primary_key: set is_primary_key=true on a "
                "property or pass primary_key explicitly"
            )

        # Build all ORM models without committing
        ot_model = ObjectTypeModel(
            id=ot_id,
            ontology_id=onto.id,
            api_name=ot_api_name,
            display_name=data.display_name,
            description=data.description,
            primary_key=primary_key,
            title_property=title_property or primary_key,
            storage_type=data.storage_type,
            visibility="NORMAL",
            status="ACTIVE",
            project_id=project_id,
            created_at=now,
            updated_at=now,
        )
        self._metadata.session.add(ot_model)

        # For MANAGED ObjectTypes, the object owns a dataset whose api_name
        # is the lower-cased form of the ObjectType api_name
        # (iceberg.<iceberg_namespace>.<dataset>). Properties that arrive
        # without an explicit backing_mapping are auto-bound to that dataset's
        # matching column (column = property api_name). Explicit caller-supplied
        # mappings are preserved untouched.
        managed_backing = self._managed_backing_ref(ot_api_name) if data.storage_type == "MANAGED" else None

        for dprop in derived_props:
            is_pk = dprop.api_name == primary_key
            is_title = dprop.api_name == (title_property or primary_key)
            # Explicit caller flags win when set (covers the case where the
            # caller marks a property PK/title but doesn't pass primary_key).
            if dprop.is_primary_key is not None:
                is_pk = dprop.is_primary_key
            if dprop.is_title_property is not None:
                is_title = dprop.is_title_property
            bm = dprop.backing_mapping
            if bm is None and managed_backing is not None:
                bm = BackingColumnRef(
                    dataset_api_name=managed_backing.dataset_api_name,
                    backing_catalog=managed_backing.backing_catalog,
                    backing_schema=managed_backing.backing_schema,
                    backing_table=managed_backing.backing_table,
                    backing_column=dprop.api_name,
                )
            pm = PropertyDefModel(
                id=new_uuid(),
                object_type_id=ot_id,
                api_name=dprop.api_name,
                display_name=dprop.display_name,
                description=dprop.description,
                data_type=dprop.data_type,
                is_primary_key=is_pk,
                is_title_property=is_title,
                indexed=dprop.searchable,
                backing_dataset_api_name=bm.dataset_api_name if bm else None,
                backing_catalog=bm.backing_catalog if bm else None,
                backing_schema=bm.backing_schema if bm else None,
                backing_table=bm.backing_table if bm else None,
                backing_column=bm.backing_column if bm else None,
                # §14.4: VECTOR 属性配置序列化进 constraints JSONB。
                constraints=(dprop.vector_config.model_dump() if dprop.vector_config else {}),
                project_id=project_id,
                created_at=now,
                updated_at=now,
            )
            self._metadata.session.add(pm)

        # Link api_names: caller-supplied (camelCase) or derived from display_name.
        existing_link_names = [lt.api_name for lt in await self._metadata.get_link_types(ontology_api_name)]
        for link in data.links:
            link_api_name = await self._resolve_link_api_name(
                link.api_name,
                link.display_name,
                existing_api_names=existing_link_names,
            )
            existing_link_names.append(link_api_name)
            lm = LinkTypeModel(
                id=new_uuid(),
                ontology_id=onto.id,
                api_name=link_api_name,
                display_name=link.display_name,
                source_object_type_id=ot_id,
                target_object_type_id=link.target_object_type_id,
                cardinality=link.cardinality,
                direction=link.direction,
                weight_property=link.weight_property,
                temporal=link.temporal,
                description="",
                project_id=project_id,
                created_at=now,
                updated_at=now,
            )
            self._metadata.session.add(lm)

        # Single atomic commit
        await self._metadata._flush_and_commit()

        # Non-blocking Gravitino/Doris registration
        if data.storage_type == "MANAGED":

            async def _register_gravitino() -> None:
                # Catalog First: create the managed Iceberg table via Gaia
                # (IcebergStore.create_managed_table) with full physical
                # metadata (PK identifier, column comments, NULL). Legacy
                # path called GravitinoRegistry.register_dataset (bare HTTP,
                # columns name+type only).
                await self._provision_managed_table_for_object_type(
                    ot_api_name,
                    display_name=data.display_name,
                    description=data.description,
                    properties=cast("Sequence[object]", derived_props),
                    primary_key=primary_key,
                )

            async def _register_doris() -> None:
                # Provision with real indexed fields (was fields=[]).
                await self._provision_index(ontology_api_name, ot_api_name, derived_props, primary_key=primary_key)

            async def _register_graph() -> None:
                # Graph-reasoning: 创建 Neo4j 图 schema（best-effort）。
                await self._provision_graph_schema(ontology_api_name, ot_api_name, derived_props)
                # GeoTime: PostGIS/TimescaleDB schema。
                await self._provision_geotime_schema(ontology_api_name, ot_api_name, derived_props)

            for fn, name in [
                (_register_gravitino, "Gravitino"),
                (_register_doris, "Doris"),
                (_register_graph, "Neo4j"),
            ]:
                try:
                    await fn()
                except Exception as e:
                    _log.warning(f"{name} registration deferred: {e}")

            # Write the PG datasets governance record (kind=MANAGED) so the
            # dataset shows up on the data-connections page and link_dataset
            # can resolve it. Idempotent: returns the existing record on
            # collision instead of raising 409.
            await self._register_managed_dataset_governance(
                ot_api_name=ot_api_name,
                display_name=data.display_name,
            )

        return ObjectType(
            id=ot_id,
            ontology_id=onto.id,
            api_name=ot_api_name,
            display_name=data.display_name,
            description=data.description,
            primary_key=primary_key,
            title_property=title_property or primary_key,
            storage_type=cast(Literal["MANAGED", "VIRTUAL"], data.storage_type),
            visibility="NORMAL",
            status="ACTIVE",
            properties=[],
            links=[],
            created_at=now,
            updated_at=now,
        )

    async def update_object_type_batch(
        self, ontology_api_name: str, type_name: str, data: ObjectTypeBatchCreate
    ) -> ObjectType:
        """Update an ObjectType's metadata and properties in a single transaction."""
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import select as sa_select

        from ontology.core.models.defaults import new_uuid, utcnow
        from ontology.core.models.ontology import ObjectTypeModel as OTModel
        from ontology.core.models.ontology import PropertyDefModel

        # Get existing
        onto = await self._metadata.get_ontology(ontology_api_name)
        stmt = sa_select(OTModel).where(
            OTModel.ontology_id == onto.id,
            OTModel.api_name == type_name,
        )
        r = await self._metadata.session.execute(stmt)
        ot_model = r.scalar_one_or_none()
        if ot_model is None:
            raise NotFoundError("ObjectType", type_name)

        now = utcnow()

        # Update metadata
        ot_model.display_name = data.display_name
        ot_model.description = data.description
        ot_model.updated_at = now

        # Delete old properties and recreate. Property api_names are re-derived
        # from display_name/backing_column (Q: apiName auto-derivation).
        await self._metadata.session.execute(
            sa_delete(PropertyDefModel).where(PropertyDefModel.object_type_id == ot_model.id)
        )
        derived_props: list[_DerivedProp] = []
        existing_prop_names: list[str] = []
        for prop in data.properties:
            prop_api_name = await self._resolve_property_api_name(
                prop.api_name,
                prop.display_name,
                backing_column=prop.backing_mapping.backing_column if prop.backing_mapping else None,
                existing_api_names=existing_prop_names,
            )
            existing_prop_names.append(prop_api_name)
            derived_props.append(
                _DerivedProp(
                    api_name=prop_api_name,
                    display_name=prop.display_name,
                    description=prop.description,
                    data_type=prop.data_type,
                    searchable=prop.searchable,
                    is_primary_key=prop.is_primary_key,
                    is_title_property=prop.is_title_property,
                    backing_mapping=prop.backing_mapping,
                    vector_config=prop.vector_config,
                )
            )

        # Resolve primary_key / title_property (Q2). On update, preserve the
        # existing primary_key when the caller omits it and no property flags
        # it (the common case — editing display_name shouldn't drop the PK).
        primary_key, title_property = self._resolve_pk_title_from_properties(
            derived_props, data.primary_key, data.title_property
        )
        if not primary_key:
            primary_key = ot_model.primary_key
        if not title_property:
            title_property = ot_model.title_property or primary_key
        ot_model.primary_key = primary_key
        ot_model.title_property = title_property

        for dprop in derived_props:
            is_pk = dprop.api_name == primary_key
            is_title = dprop.api_name == title_property
            if dprop.is_primary_key is not None:
                is_pk = dprop.is_primary_key
            if dprop.is_title_property is not None:
                is_title = dprop.is_title_property
            pm = PropertyDefModel(
                id=new_uuid(),
                object_type_id=ot_model.id,
                api_name=dprop.api_name,
                display_name=dprop.display_name,
                description=dprop.description,
                data_type=dprop.data_type,
                is_primary_key=is_pk,
                is_title_property=is_title,
                indexed=dprop.searchable,
                backing_dataset_api_name=dprop.backing_mapping.dataset_api_name if dprop.backing_mapping else None,
                backing_catalog=dprop.backing_mapping.backing_catalog if dprop.backing_mapping else None,
                backing_schema=dprop.backing_mapping.backing_schema if dprop.backing_mapping else None,
                backing_table=dprop.backing_mapping.backing_table if dprop.backing_mapping else None,
                backing_column=dprop.backing_mapping.backing_column if dprop.backing_mapping else None,
                # §14.4: VECTOR 属性配置序列化进 constraints JSONB。
                constraints=(dprop.vector_config.model_dump() if dprop.vector_config else {}),
                project_id=ot_model.project_id or "00000000000000000000000000000001",
                created_at=now,
                updated_at=now,
            )
            self._metadata.session.add(pm)

        await self._metadata._flush_and_commit()

        # The indexed field set may have changed — rebuild the Doris index
        # table + sync pipeline so filters reflect the new property set.
        if cast(Literal["MANAGED", "VIRTUAL"], ot_model.storage_type) == "MANAGED":
            await self._provision_index(
                ontology_api_name,
                ot_model.api_name,
                derived_props,
                rebuild=True,
                primary_key=ot_model.primary_key,
            )
            # Graph-reasoning: update 时重建图 schema（新增 indexed 属性建索引）。
            # 仅在用户显式启用 graph_indexing_enabled 时执行（Gate 4）。
            caps = ObjectTypeCapabilities.model_validate(ot_model.capabilities or {})
            if caps.graph_indexing_enabled:
                await self._provision_graph_schema(ontology_api_name, ot_model.api_name, derived_props)
            if caps.geotime_indexing_enabled:
                await self._provision_geotime_schema(ontology_api_name, ot_model.api_name, derived_props)

        # Return manually to avoid greenlet error
        return ObjectType(
            id=ot_model.id,
            ontology_id=ot_model.ontology_id,
            api_name=ot_model.api_name,
            display_name=ot_model.display_name,
            description=ot_model.description or "",
            primary_key=ot_model.primary_key,
            title_property=ot_model.title_property,
            storage_type=cast(Literal["MANAGED", "VIRTUAL"], ot_model.storage_type),
            visibility=cast(Literal["NORMAL", "PROMINENT", "HIDDEN"], ot_model.visibility),
            status=cast(Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"], ot_model.status),
            properties=[],
            links=[],
            created_at=ot_model.created_at,
            updated_at=now,
        )

    async def list_link_types(self, ontology_api_name: str, *, include_non_active: bool = False) -> list[LinkTypeDef]:
        """List all link types in an Ontology."""
        return await self._metadata.get_link_types(ontology_api_name, include_non_active=include_non_active)

    async def get_object_type(
        self, ontology_api_name: str, api_name: str, *, include_non_active: bool = False
    ) -> ObjectType:
        return await self._metadata.get_object_type(ontology_api_name, api_name, include_non_active=include_non_active)

    async def list_object_types(self, ontology_api_name: str, *, include_non_active: bool = False) -> list[ObjectType]:
        result: list[ObjectType] = await self._metadata.list_object_types(
            ontology_api_name, include_non_active=include_non_active
        )
        return result

    async def delete_object_type(self, ontology_api_name: str, type_name: str) -> None:
        """Delete an ObjectType by api_name (cascades to properties/links)."""
        ot = await self._metadata.get_object_type(ontology_api_name, type_name)
        # Tear down the Doris index table + sync pipeline before deleting the
        # PG row, so we still have the api_name to target. Best-effort.
        if ot.storage_type == "MANAGED":
            await self._deprovision_index(ontology_api_name, type_name)
        await self._metadata.delete_object_type(ot.id)

    async def update_object_type_fields(
        self, ontology_api_name: str, type_name: str, updates: dict[str, Any]
    ) -> ObjectType:
        """Partially update an ObjectType's display fields.

        Supports ``capabilities`` key: when present, validates and persists
        the ObjectTypeCapabilities (graph/geotime opt-in switches). Enabling
        a capability triggers the corresponding schema provisioning (Neo4j
        label/index, PostGIS table, TimescaleDB hypertable) best-effort.
        """
        ot = await self._metadata.get_object_type(ontology_api_name, type_name)

        # If capabilities are being updated, validate + trigger provisioning.
        if "capabilities" in updates:
            caps_data = updates["capabilities"]
            if isinstance(caps_data, ObjectTypeCapabilities):
                new_caps = caps_data
            elif isinstance(caps_data, dict):
                new_caps = ObjectTypeCapabilities.model_validate(caps_data)
            else:
                raise ValidationError(f"capabilities must be ObjectTypeCapabilities or dict, got {type(caps_data)}")

            # Gate 1: VIRTUAL types cannot enable graph/geotime (no data to project).
            if ot.storage_type == "VIRTUAL" and (new_caps.graph_indexing_enabled or new_caps.geotime_indexing_enabled):
                raise ValidationError(
                    f"VIRTUAL ObjectType '{type_name}' cannot enable graph/geotime "
                    "indexing (no managed data to project)"
                )

            # Gate 3 (graph only): check that at least one LinkType connects
            # this ObjectType before allowing graph_indexing_enabled. We warn
            # rather than block — the user may create links after enabling.
            # The provisioning itself is best-effort and will simply create
            # an empty label (no edges until links exist).
            import logging

            _log = logging.getLogger(__name__)
            if new_caps.graph_indexing_enabled and not getattr(ot, "links", None):
                links = await self._metadata.get_link_types(ontology_api_name)
                connected = any(lt.source_object_type_id == ot.id or lt.target_object_type_id == ot.id for lt in links)
                if not connected:
                    _log.warning(
                        "graph_indexing_enabled for '%s' but no LinkType connects "
                        "it — graph projection will have no edges until links are "
                        "created",
                        type_name,
                    )

            # Persist the capabilities update.
            result = await self._metadata.update_object_type(ot.id, {"capabilities": new_caps.model_dump()})

            # Trigger schema provisioning for newly-enabled capabilities.
            # Only provision if the capability was just turned on (compare
            # against the previous state).
            old_caps = ot.capabilities
            props = await self._metadata.get_properties(ot.id)
            if new_caps.graph_indexing_enabled and not old_caps.graph_indexing_enabled:
                await self._provision_graph_schema(ontology_api_name, type_name, props)
            if new_caps.geotime_indexing_enabled and not old_caps.geotime_indexing_enabled:
                await self._provision_geotime_schema(ontology_api_name, type_name, props)

            return result

        return await self._metadata.update_object_type(ot.id, updates)

    async def list_object_type_summaries(self, ontology_api_name: str) -> list[tuple[Any, int, int, int]]:
        """Return (ObjectTypeModel, properties_count, links_count, actions_count) tuples."""
        onto = await self._metadata.get_ontology(ontology_api_name)
        return await self._metadata.list_object_type_summaries(onto.id)

    async def add_property_to_object_type(
        self, ontology_api_name: str, type_name: str, prop: PropertyDefCreate
    ) -> PropertyDef:
        """Add a property to an ObjectType.

        ``api_name`` is caller-supplied (camelCase) or derived from
        ``display_name`` / ``backing_column``; kept unique within the ObjectType.
        """
        ot = await self._metadata.get_object_type(ontology_api_name, type_name)
        existing_props = await self._metadata.get_properties(ot.id)
        api_name = await self._resolve_property_api_name(
            prop.api_name,
            prop.display_name,
            backing_column=prop.backing_mapping.backing_column if prop.backing_mapping else None,
            existing_api_names=[p.api_name for p in existing_props],
        )
        now = utcnow()
        created = await self._metadata.add_property(
            ot.id,
            PropertyDef(
                id="",
                object_type_id=ot.id,
                api_name=api_name,
                display_name=prop.display_name,
                description=prop.description,
                data_type=prop.data_type,
                is_primary_key=prop.is_primary_key,
                is_title_property=prop.is_title_property,
                nullable=prop.nullable,
                indexed=prop.indexed,
                backing_mapping=prop.backing_mapping,
                vector_config=prop.vector_config,
                project_id=prop.project_id or ot.project_id or "00000000000000000000000000000001",
                created_at=now,
                updated_at=now,
            ),
        )

        # Catalog First: evolve the Iceberg table schema so the new property's
        # physical column (with comment + NOT-NULL) materializes in the catalog.
        # Best-effort — a failure is logged so PG metadata (source of truth for
        # the ObjectType definition) still wins; the column reconciles on retry
        # or next sync. Only MANAGED ObjectTypes own an Iceberg table.
        if ot.storage_type == "MANAGED":
            import logging

            from ontology.core.naming import managed_dataset_api_name
            from ontology.core.schemas.dataset import ManagedColumnDef, ManagedTableSchema

            _log = logging.getLogger(__name__)
            col_name = (
                prop.backing_mapping.backing_column
                if prop.backing_mapping and prop.backing_mapping.backing_column
                else api_name
            )
            try:
                await self._dataset.ensure_schema(
                    managed_dataset_api_name(type_name),
                    ManagedTableSchema(
                        columns=[
                            ManagedColumnDef(
                                name=col_name,
                                type=str(prop.data_type).lower(),
                                nullable=prop.nullable,
                                comment=prop.description or "",
                                is_primary_key=prop.is_primary_key,
                            )
                        ],
                    ),
                )
            except Exception as exc:
                _log.warning(
                    "Iceberg schema evolution deferred for %s.%s: %s",
                    type_name,
                    api_name,
                    exc,
                )
        return created

    async def link_dataset(
        self,
        ontology_api_name: str,
        type_name: str,
        dataset_api_name: str,
        column_mappings: list[dict[str, str]],
    ) -> ObjectType:
        """A1 — bind an ObjectType's properties to a Dataset's columns.

        Per dataset-ontology-binding.md §4.6 / architecture red lines:
          - storage_type must match dataset kind (MANAGED↔MANAGED,
            VIRTUAL↔VIRTUAL); mismatch is a ValidationError (422).
          - The physical locator (catalog.schema.table) is resolved from
            the DatasetGovernance record: VIRTUAL parses storage_location
            (three-part locator); MANAGED uses the Iceberg default
            (catalog=iceberg, schema=ICEBERG_NAMESPACE, table=dataset_api_name).
          - EVERY property must be mapped: ``column_mappings`` must cover
            all properties of the ObjectType. Unmapped properties are
            rejected (ValidationError) — the user must delete unwanted
            properties before binding, not leave them dangling. This makes
            partial-binding / dangling-old-dataset refs impossible, so no
            ``clear_unmapped`` flag is needed.
          - Every ``column_name`` must exist in the target dataset's schema
            (verified best-effort; skipped if the schema can't be loaded).
          - ``column_mappings`` is ``[{"property_api_name": str,
            "column_name": str}]``.
        """
        from ontology.config.settings import settings

        ot = await self._metadata.get_object_type(ontology_api_name, type_name)
        dataset = await self._metadata.get_dataset(dataset_api_name)

        if ot.storage_type != dataset.kind:
            raise ValidationError(
                f"storage_type mismatch: ObjectType {type_name} is "
                f"{ot.storage_type} but dataset {dataset_api_name} is "
                f"{dataset.kind}"
            )

        # Resolve physical locator.
        if dataset.kind == "VIRTUAL":
            parts = dataset.storage_location.split(".")
            if len(parts) != 3:
                raise ValidationError(
                    f"VIRTUAL dataset {dataset_api_name} has invalid "
                    f"storage_location (expected catalog.schema.table): "
                    f"{dataset.storage_location!r}"
                )
            catalog_name, schema_name, table_name = parts
        else:  # MANAGED → Iceberg
            catalog_name = "iceberg"
            schema_name = settings.iceberg_namespace
            table_name = dataset_api_name

        props = await self._metadata.get_properties(ot.id)
        prop_by_name = {p.api_name: p for p in props}

        # Strong invariant: every property MUST be mapped. A partial mapping
        # would leave some properties without a data source (silent data loss
        # on query). If the user no longer needs a property they must delete
        # it first, not leave it unmapped. This applies to both first-binding
        # and migration.
        if len(column_mappings) != len(props):
            mapped_apis = {e.get("property_api_name") for e in column_mappings}
            missing = [p.api_name for p in props if p.api_name not in mapped_apis]
            raise ValidationError(f"每个属性必须映射到源列；{len(missing)} 个属性未映射: {missing}")

        # Strong invariant: every backing_column must exist in the target
        # dataset. Fetched best-effort: if the schema can't be loaded
        # (Iceberg/Gravitino down) we skip the check and trust the caller
        # (the frontend Select only offers real columns), logging a warning.
        valid_columns: set[str] | None = None
        if self._container is not None:
            import logging

            _log = logging.getLogger(__name__)
            try:
                from ontology.services.datasource_service import DataSourceService

                ds_svc: DataSourceService = self._container.datasource_service
                schema = await ds_svc.get_dataset_schema(dataset_api_name)
                valid_columns = {c.name for c in schema.columns}
            except Exception as exc:  # noqa: BLE001 — schema fetch is best-effort
                _log.warning(
                    "Could not verify column names against dataset '%s' schema "
                    "during link_dataset (skipping check): %s",
                    dataset_api_name,
                    exc,
                )

        # Two-phase: validate everything first, then write. Avoids leaving
        # partial mappings behind when a later entry fails validation.
        resolved: list[tuple[str, BackingColumnRef]] = []
        for entry in column_mappings:
            prop_api = entry.get("property_api_name")
            col_name = entry.get("column_name")
            if not prop_api or not col_name:
                raise ValidationError("每个映射需要 property_api_name 和 column_name")
            if valid_columns is not None and col_name not in valid_columns:
                raise ValidationError(
                    f"属性 '{prop_api}' 映射的源列 '{col_name}' 在数据集 '{dataset_api_name}' 中不存在"
                )
            target = prop_by_name.get(prop_api)
            if target is None:
                raise NotFoundError(f"Property {prop_api} on ObjectType {type_name}", prop_api)
            mapping = BackingColumnRef(
                dataset_api_name=dataset_api_name,
                backing_catalog=catalog_name,
                backing_schema=schema_name,
                backing_table=table_name,
                backing_column=col_name,
            )
            resolved.append((target.id, mapping))

        for prop_id, mapping in resolved:
            await self._metadata.update_property_backing_mapping(prop_id, mapping)

        # Anchor the OT's primary backing dataset on first bind (Palantir
        # "backing datasource" semantics). Subsequent binds to a *different*
        # dataset do NOT overwrite — the first bound dataset stays the primary
        # source (MDO: additional datasets are secondary, surfaced only via
        # per-property ``backing_mapping``). This keeps the OT-level field a
        # stable default reference for list badges / detail pages / future
        # permission anchoring, while property-level mapping remains the
        # authoritative physical binding.
        if ot.backing_dataset_api_name is None:
            await self._metadata.set_object_type_backing_dataset(ot.id, dataset_api_name)

        return await self._metadata.get_object_type(ontology_api_name, type_name)

    async def unlink_dataset(
        self, ontology_api_name: str, type_name: str, property_api_names: list[str] | None = None
    ) -> ObjectType:
        """A1 — clear dataset links on an ObjectType.

        If ``property_api_names`` is None, clear all physical mappings on the
        ObjectType; otherwise clear only the listed properties.
        """
        ot = await self._metadata.get_object_type(ontology_api_name, type_name)
        props = await self._metadata.get_properties(ot.id)
        targets = (
            [p for p in props if p.api_name in property_api_names]
            if property_api_names is not None
            else [p for p in props if p.backing_mapping is not None]
        )
        for p in targets:
            await self._metadata.update_property_backing_mapping(p.id, None)
        return await self._metadata.get_object_type(ontology_api_name, type_name)

    async def list_properties(self, ontology_api_name: str, type_name: str) -> list[PropertyDef]:
        """List properties of an ObjectType."""
        ot = await self._metadata.get_object_type(ontology_api_name, type_name)
        return await self._metadata.get_properties(ot.id)

    async def delete_property(self, ontology_api_name: str, type_name: str, property_name: str) -> None:
        """Delete a property by api_name from an ObjectType."""
        ot = await self._metadata.get_object_type(ontology_api_name, type_name)
        props = await self._metadata.get_properties(ot.id)
        target = next((p for p in props if p.api_name == property_name), None)
        if target is None:
            raise NotFoundError("Property", property_name)
        await self._metadata.delete_property(target.id)

    async def delete_link_type(self, ontology_api_name: str, link_name: str) -> None:
        """Delete a link type by api_name from an Ontology."""
        links = await self._metadata.get_link_types(ontology_api_name)
        target = next((lt for lt in links if lt.api_name == link_name), None)
        if target is None:
            raise NotFoundError("LinkType", link_name)
        await self._metadata.delete_link_type(target.id)

    async def list_action_types(self, ontology_api_name: str) -> list[ActionType]:
        """List all action types in an Ontology."""
        return await self._metadata.list_action_types(ontology_api_name)

    async def assemble_ontology_metadata(self, ontology_api_name: str) -> OntologyFullMetadata:
        """Assemble the full ontology metadata in one payload (ADR-020).

        Single source of truth shared by:
          - ``describe_ontology`` tool (MCP + REST) — returns the structured payload
          - ``build_ontology_summary`` (AG-UI text injection) — renders it to markdown

        Loads objects + links + actions + interfaces, attaches inbound/outbound
        links and applicable actions to each ObjectType, and keys everything by
        api_name. Best-effort: a failing entity-type query is recorded in
        ``omitted`` (``partial=True``) rather than raising — matching Palantir
        ``/fullMetadata``'s "may omit rather than fail" contract. A missing
        ontology itself is a real NotFoundError (404), not best-effort.
        """
        ontology = await self._metadata.get_ontology(ontology_api_name)  # raises NotFoundError

        ots = await self._metadata.list_object_types(ontology_api_name)

        links: list[LinkTypeDef] = []
        actions: list[ActionType] = []
        interfaces: list[InterfaceType] = []
        omitted: list[str] = []

        try:
            links = await self._metadata.get_link_types(ontology_api_name)
        except Exception:  # noqa: BLE001 — best-effort, see ADR-020
            omitted.append("link_types")
        try:
            actions = await self._metadata.list_action_types(ontology_api_name)
        except Exception:  # noqa: BLE001 — best-effort
            omitted.append("action_types")
        try:
            interfaces = await self._metadata.get_interface_types(ontology_api_name)
        except Exception:  # noqa: BLE001 — best-effort
            omitted.append("interfaces")

        # id → api_name index for resolving LinkType source/target UUIDs and
        # ActionType.affected_object_type_id onto business names.
        ot_name_by_id: dict[str, str] = {ot.id: ot.api_name for ot in ots}

        # Pre-group links by endpoint so each ObjectTypeFullMetadata carries its
        # inbound/outbound link api_names without a second pass over all links.
        outbound_by_ot: dict[str, list[str]] = {}
        inbound_by_ot: dict[str, list[str]] = {}
        for lt in links:
            outbound_by_ot.setdefault(lt.source_object_type_id, []).append(lt.api_name)
            inbound_by_ot.setdefault(lt.target_object_type_id, []).append(lt.api_name)

        # Pre-group actions by affected OT. Actions with no affected OT (cross-
        # type / global) stay in the top-level map but attach to no ObjectType.
        actions_by_ot: dict[str, list[str]] = {}
        action_summaries: dict[str, ActionTypeSummary] = {}
        for at in actions:
            action_summaries[at.api_name] = ActionTypeSummary(
                api_name=at.api_name,
                display_name=at.display_name,
                description=at.description,
                affected_object_type_api_name=(
                    ot_name_by_id.get(at.affected_object_type_id) if at.affected_object_type_id else None
                ),
                risk_level=at.risk_level,
                operation_kind=at.operation_kind,
            )
            if at.affected_object_type_id:
                actions_by_ot.setdefault(at.affected_object_type_id, []).append(at.api_name)

        object_types: dict[str, ObjectTypeFullMetadata] = {
            ot.api_name: ObjectTypeFullMetadata(
                id=ot.id,
                api_name=ot.api_name,
                display_name=ot.display_name,
                description=ot.description,
                primary_key=ot.primary_key,
                title_property=ot.title_property,
                storage_type=ot.storage_type,
                visibility=ot.visibility,
                status=ot.status,
                properties=ot.properties,
                inbound_links=inbound_by_ot.get(ot.id, []),
                outbound_links=outbound_by_ot.get(ot.id, []),
                actions=actions_by_ot.get(ot.id, []),
            )
            for ot in ots
        }

        return OntologyFullMetadata(
            ontology=ontology,
            object_types=object_types,
            link_types={lt.api_name: lt for lt in links},
            action_types=action_summaries,
            interfaces=interfaces,
            partial=bool(omitted),
            omitted=omitted,
        )

    # ── SharedProperty ──

    async def add_shared_property(
        self,
        display_name: str,
        data_type: DataType,
        description: str = "",
    ) -> SharedProperty:
        """Create a globally reusable shared property.

        ``api_name`` is derived from ``display_name`` (camelCase) and kept
        unique across all shared properties.
        """
        existing = await self._metadata.list_shared_properties()
        api_name = await self._derive_unique_api_name(
            display_name,
            fallback_prefix="property",
            pascal=False,
            existing_api_names=[sp.api_name for sp in existing],
        )
        now = utcnow()
        return await self._metadata.create_shared_property(
            SharedProperty(
                id="",
                api_name=api_name,
                display_name=display_name,
                description=description,
                data_type=data_type,
                created_at=now,
                updated_at=now,
            )
        )

    async def link_shared_property(self, object_type_id: str, shared_property_id: str) -> None:
        await self._metadata.link_shared_property(object_type_id, shared_property_id)

    # ── LinkType ──

    async def define_link_type(self, ontology_api_name: str, data: LinkTypeDefCreate) -> LinkTypeDef:
        onto = await self._metadata.get_ontology(ontology_api_name)
        project_id = await self._metadata._resolve_default_project_for_space(onto.space_id)  # noqa: SLF001
        if project_id is None:
            raise ValidationError(
                f"Cannot resolve a Project for Ontology '{ontology_api_name}' "
                "(option A: Ontology must be bound to a Space with a Project)"
            )
        existing_links = await self._metadata.get_link_types(ontology_api_name)
        api_name = await self._resolve_link_api_name(
            data.api_name,
            data.display_name,
            existing_api_names=[lt.api_name for lt in existing_links],
        )
        now = utcnow()
        return await self._metadata.create_link_type(
            LinkTypeDef(
                id="",
                ontology_id=onto.id,
                api_name=api_name,
                display_name=data.display_name,
                description=data.description,
                source_object_type_id=data.source_object_type_id,
                target_object_type_id=data.target_object_type_id,
                foreign_key_property_api_name=data.foreign_key_property_api_name,
                cardinality=data.cardinality,
                direction=data.direction,
                weight_property=data.weight_property,
                temporal=data.temporal,
                project_id=project_id,
                created_at=now,
                updated_at=now,
            )
        )

    # ── ActionType ──

    async def define_action_type(
        self,
        ontology_api_name: str,
        api_name: str,
        display_name: str,
        parameters: dict[str, Any] | None = None,
        rules: dict[str, Any] | None = None,
        description: str = "",
    ) -> ActionType:
        """Define an ActionType (legacy simplified API).

        For the full ActionTypeCreate workflow (with typed parameters,
        rules, and effects), use ActionService.define_action_type().
        """
        onto = await self._metadata.get_ontology(ontology_api_name)
        now = utcnow()
        return await self._metadata.create_action_type(
            ActionType(
                id="",
                ontology_id=onto.id,
                api_name=api_name,
                display_name=display_name,
                description=description,
                parameters=parameters or {},
                rules=rules or {},
                submission_criteria={},
                status="ACTIVE",
                created_at=now,
                updated_at=now,
            )
        )

    async def define_action_type_full(
        self,
        ontology_api_name: str,
        action_type_def: "ActionTypeCreate",
    ) -> ActionType:
        """Define an ActionType with full typed parameter/rule/effect definitions.

        This is the preferred API for ActionType creation with structured parameters.
        Delegates to the enriched ActionService.define_action_type().
        """
        if self._container is None:
            raise RuntimeError(
                "define_action_type_full requires a container reference (M4); "
                "construct OntologyService with container=... to use this method."
            )
        service = self._container.action_service
        return await service.define_action_type(ontology_api_name, action_type_def)
