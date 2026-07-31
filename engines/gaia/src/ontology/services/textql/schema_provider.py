"""OntologySchemaProvider — loads ontology schema for the SQL compiler.

Bridges PostgresMetaStore ( ObjectType / Property / LinkType with UUID ids )
to the OntologySqlCompiler's OntologySchemaProvider protocol ( api_name-keyed
maps ). Loaded per-query from the metadata layer; cheap because object counts
per ontology are small (tens to low hundreds).
"""

from __future__ import annotations

import logging
from typing import Any

from ontology.core.schemas.ontology import ObjectType
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore

logger = logging.getLogger(__name__)


class MetaStoreSchemaProvider:
    """OntologySchemaProvider impl backed by PostgresMetaStore.

    Loads all ObjectTypes + Properties + LinkTypes for one ontology and
    exposes them in the api_name-keyed shape the compiler expects.
    """

    def __init__(self, metadata: PostgresMetaStore) -> None:
        self._metadata = metadata
        self._object_types: dict[str, str] = {}
        self._properties: dict[str, dict[str, str]] = {}
        self._links: set[tuple[str, str]] = set()
        self._physical_to_ot: dict[str, str] = {}
        self._storage_types: dict[str, str] = {}
        self._trino_refs: dict[str, str] = {}
        self._duckdb_refs: dict[str, str] = {}
        self._loaded_ontology: str | None = None

    async def load(self, ontology_api_name: str) -> None:
        """Load the full schema for one ontology (idempotent per ontology)."""
        if self._loaded_ontology == ontology_api_name and self._object_types:
            return
        self._object_types = {}
        self._properties = {}
        self._links = set()
        self._physical_to_ot = {}
        self._storage_types = {}
        self._trino_refs = {}
        self._duckdb_refs = {}

        # Build ObjectType id → api_name index first (LinkType refs by UUID).
        object_types = await self._metadata.list_object_types(ontology_api_name)
        ot_id_to_api: dict[str, str] = {}

        for ot in object_types:
            ot_id_to_api[ot.id] = ot.api_name
            storage = getattr(ot, "storage_type", "MANAGED") or "MANAGED"
            self._storage_types[ot.api_name] = storage
            physical = self._physical_table_ref(ontology_api_name, ot)
            self._object_types[ot.api_name] = physical
            self._physical_to_ot[physical] = ot.api_name
            # Trino physical name: MANAGED → iceberg.ontology.<snake_type>;
            # VIRTUAL → same three-part external locator as Doris (Trino
            # federation resolves it via the registered catalog).
            trino_ref = self._trino_table_ref(ontology_api_name, ot, storage, physical)
            self._trino_refs[ot.api_name] = trino_ref
            # DuckDB physical name (lite 桌面版, B3): VIRTUAL → src_<ds>.<schema>.
            # <table>; MANAGED 不做（lite 红线下砍托管表，查询层 guard 拦截）。
            duckdb_ref = self._duckdb_table_ref(ot, storage)
            self._duckdb_refs[ot.api_name] = duckdb_ref
            # Register BOTH physical names (Doris + Trino) in the reverse map
            # so the compiler's column-owner resolution (which runs AFTER
            # table rewrite) can map a rewritten physical table name back to
            # its ObjectType regardless of dialect.
            self._physical_to_ot[trino_ref] = ot.api_name
            # VIRTUAL tables carry a catalog.schema.table locator; SqlGlot's
            # Table.name returns only the innermost identifier (e.g.
            # 't_project_base'), so also register that inner name → ot for
            # the compiler's reverse lookup (_resolve_owner no-prefix path).
            # Table names are globally unique per benchmark ontology, so the
            # collision risk is acceptable; MANAGED names are already unique.
            if "." in physical:
                inner = physical.rsplit(".", 1)[-1]
                if inner not in self._physical_to_ot:
                    self._physical_to_ot[inner] = ot.api_name
            if "." in trino_ref and trino_ref != physical:
                inner = trino_ref.rsplit(".", 1)[-1]
                if inner not in self._physical_to_ot:
                    self._physical_to_ot[inner] = ot.api_name
            # Properties: api_name → backing_column (fallback to api_name).
            props: dict[str, str] = {}
            for p in ot.properties or []:
                pm = getattr(p, "backing_mapping", None)
                col = str(pm.backing_column) if pm and getattr(pm, "backing_column", None) else str(p.api_name)
                props[p.api_name] = col
            self._properties[ot.api_name] = props

        # LinkTypes: resolve source/target UUID → api_name pairs.
        link_types = await self._metadata.get_link_types(ontology_api_name)
        for lt in link_types:
            src = ot_id_to_api.get(lt.source_object_type_id)
            tgt = ot_id_to_api.get(lt.target_object_type_id)
            if src and tgt:
                self._links.add((src, tgt))
                self._links.add((tgt, src))  # bidirectional for join validation

        self._loaded_ontology = ontology_api_name
        logger.debug(
            "Loaded schema for %s: %d object types, %d links",
            ontology_api_name,
            len(self._object_types),
            len(self._links) // 2,
        )

    @staticmethod
    def _physical_table_ref(ontology_api_name: str, ot: ObjectType) -> str:
        """Resolve an ObjectType's physical table reference for the compiler.

        - MANAGED: Doris index table name ``idx_<ont>__<type>`` (data lives
          in Doris/Iceberg, the compiler emits this as a single identifier).
        - VIRTUAL: the backing_mapping's three-part locator
          ``catalog.schema.table`` (data lives in the external source, e.g.
          MySQL via Trino federation; the compiler emits this as a
          catalog-qualified table so Trino resolves it correctly).

        The value is a plain string either way; the compiler splits on '.'
        to decide single-identifier vs catalog-qualified ``exp.Table``.
        Falls back to the Doris index table name when no backing_mapping is
        present (legacy/unsynced OTs).
        """
        from ontology.core.naming import doris_index_table

        if getattr(ot, "storage_type", None) == "VIRTUAL":
            for p in ot.properties or []:
                pm = getattr(p, "backing_mapping", None)
                if pm and getattr(pm, "backing_catalog", None) and getattr(pm, "backing_table", None):
                    catalog = str(pm.backing_catalog)
                    schema = str(getattr(pm, "backing_schema", "") or "")
                    table = str(pm.backing_table)
                    # Trino lower-cases catalog names on registration; the
                    # backing_catalog stored in the ontology is the DataSource
                    # api_name (camelCase), which Trino registers as all-lower.
                    # Emit lower-case to match the registered catalog name.
                    catalog = catalog.lower()
                    return f"{catalog}.{schema}.{table}" if schema else f"{catalog}.{table}"
            # VIRTUAL without backing_mapping — fall through to Doris name
            # (will fail downstream, but keeps the schema loadable).
        return doris_index_table(ontology_api_name, ot.api_name)

    @staticmethod
    def _trino_table_ref(ontology_api_name: str, ot: ObjectType, storage: str, doris_ref: str) -> str:
        """Resolve an ObjectType's Trino physical table reference.

        - MANAGED: ``iceberg.ontology.<snake_type>`` — the Iceberg table
          visible via Trino's ``iceberg`` catalog (REST Catalog backed by
          Gravitino 9001), namespace ``ontology`` (the metalake name). The
          Iceberg table name is the snake_case ObjectType api_name (see
          ``core.naming.managed_dataset_api_name``). This is what lets a
          MANAGED table participate in a Trino federation JOIN alongside
          VIRTUAL tables from other catalogs.
        - VIRTUAL: the same three-part external locator as Doris
          (``<catalog>.<schema>.<table>``) — Trino resolves it via the
          registered catalog. Reusing ``doris_ref`` avoids recomputing.
        """
        from ontology.core.naming import managed_dataset_api_name

        if storage == "VIRTUAL":
            return doris_ref
        # MANAGED: prefer the backing_mapping recorded at link_dataset time.
        # The ontology's dataset api_name / backing_table is the source of
        # truth for where the data physically lives (a MANAGED OT may be
        # bound to any dataset api_name, not necessarily
        # managed_dataset_api_name(ot)). Only fall back to the derived name
        # when no backing_mapping is present (legacy/unsynced OTs).
        for p in ot.properties or []:
            pm = getattr(p, "backing_mapping", None)
            if pm and getattr(pm, "backing_table", None):
                catalog = str(getattr(pm, "backing_catalog", "") or "iceberg")
                schema = str(getattr(pm, "backing_schema", "") or "ontology")
                table = str(pm.backing_table)
                return f"{catalog}.{schema}.{table}"
        table = managed_dataset_api_name(ot.api_name)
        return f"iceberg.ontology.{table}"

    @staticmethod
    def _duckdb_table_ref(ot: ObjectType, storage: str) -> str:
        """Resolve an ObjectType's DuckDB physical table reference (lite 桌面版, B3).

        - VIRTUAL: ``src_<ds>.<schema>.<table>`` — catalog = DuckDB ATTACH 别名
          ``src_<datasource api_name>``（B4 连接器 ``to_duckdb_attach`` 生成）。
          DataSource api_name 取自 property.backing_mapping.backing_catalog
          （link_dataset 时存入，即 DataSource api_name）。schema/table 同 Trino
          路径，沿用 backing_mapping 的值。
        - MANAGED: 返回空串——lite 红线下不做托管表，查询层 guard 会拦截 MANAGED
          查询，此值不会被使用（占位保证 map 完整，不抛破坏 schema load）。
        """
        if storage == "VIRTUAL":
            for p in ot.properties or []:
                pm = getattr(p, "backing_mapping", None)
                if pm and getattr(pm, "backing_catalog", None) and getattr(pm, "backing_table", None):
                    catalog = f"src_{str(pm.backing_catalog).lower()}"
                    schema = str(getattr(pm, "backing_schema", "") or "")
                    table = str(pm.backing_table)
                    return f"{catalog}.{schema}.{table}" if schema else f"{catalog}.{table}"
        return ""

    # ── OntologySchemaProvider protocol ───────────────────────────────────

    def object_types(self) -> dict[str, str]:
        return self._object_types

    def properties(self) -> dict[str, dict[str, str]]:
        return self._properties

    def links(self) -> set[tuple[str, str]]:
        return self._links

    def physical_to_object_type(self) -> dict[str, str]:
        return self._physical_to_ot

    def storage_types(self) -> dict[str, str]:
        return self._storage_types

    def trino_table_refs(self) -> dict[str, str]:
        return self._trino_refs

    def duckdb_table_refs(self) -> dict[str, str]:
        return self._duckdb_refs


def schema_provider_from_object_types(ontology_api_name: str, object_types: list[ObjectType]) -> dict[str, Any]:
    """Build a plain schema dict from a list of ObjectType schemas.

    Convenience for callers that already hold ObjectType objects (e.g. the
    schema injector) and want the compiler's input shape without a round-trip
    to the metadata layer.
    """
    object_types_map: dict[str, str] = {}
    properties_map: dict[str, dict[str, str]] = {}
    physical_to_ot: dict[str, str] = {}
    for ot in object_types:
        physical = MetaStoreSchemaProvider._physical_table_ref(ontology_api_name, ot)
        object_types_map[ot.api_name] = physical
        physical_to_ot[physical] = ot.api_name
        if "." in physical:
            inner = physical.rsplit(".", 1)[-1]
            if inner not in physical_to_ot:
                physical_to_ot[inner] = ot.api_name
        props: dict[str, str] = {}
        for p in ot.properties or []:
            pm = getattr(p, "backing_mapping", None)
            col = str(pm.backing_column) if pm and getattr(pm, "backing_column", None) else str(p.api_name)
            props[p.api_name] = col
        properties_map[ot.api_name] = props
    return {
        "object_types": object_types_map,
        "properties": properties_map,
        "links": set(),  # links need UUID resolution; inject separately if needed
        "physical_to_object_type": physical_to_ot,
    }
