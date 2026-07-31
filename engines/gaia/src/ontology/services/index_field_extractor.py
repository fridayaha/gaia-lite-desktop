"""IndexFieldExtractor — derive Doris IndexField[] from an ObjectType's properties.

Bridges the Ontology model (PropertyDef.indexed + backing_mapping) and the
Doris object-store table (IndexField with index_type). This is the single
source of truth for *which* columns land in the Doris table and *how* they
are indexed.

Design rules (ADR-001 revision, 2026-06-25 — Doris is the online read
primary source, storing full structured attributes):
  - is_primary_key      → PRIMARY_KEY
  - indexed + numeric/temporal → RANGE   (Doris range/minmax, efficient for >/</between)
  - indexed + string-ish       → INVERTED (Doris inverted index, efficient for eq/in/contains)
  - indexed + VECTOR datatype  → VECTOR   (embedding, ANN search)
  - non-indexed property       → STORED_ONLY (full-detail column, no index)
  - Only properties with a backing_mapping (real Iceberg column) are eligible;
    a logical-only property has nothing to mirror into Doris.
  - All datatypes (including former redline STRUCT/ARRAY/binary) are stored —
    they map to Doris types via DorisIndexStore._DORIS_TYPE_MAP. The old
    "no full-detail/large/binary in Doris" red line was lifted by ADR-001
    revision; large binaries store only their reference (URL/id), not the blob.

The extractor never raises on a single bad property — it skips it and records
the reason, so one misconfigured property cannot block ObjectType provisioning.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ontology.core.schemas.index import IndexField

_log = logging.getLogger(__name__)

# Datatypes that formerly were refused in Doris (old red line #4). With the
# ADR-001 revision (2026-06-25) these are now STORED_ONLY columns — Doris
# stores their serialized form (JSON for STRUCT/ARRAY, reference id for
# MEDIA_REFERENCE/ATTACHMENT/GEOSHAPE). Kept as a set only to give them a
# consistent STORED_ONLY classification regardless of the `indexed` flag.
_FORMER_REDLINE_DATA_TYPES: frozenset[str] = frozenset(
    {
        "STRUCT",
        "ARRAY",
        "MEDIA_REFERENCE",
        "ATTACHMENT",
        "GEOSHAPE",
    }
)

# Numeric/temporal → RANGE index. Doris minmax/range index is most effective
# for ordered types where range predicates (>, <, between) dominate.
_RANGE_DATA_TYPES: frozenset[str] = frozenset(
    {"INTEGER", "SHORT", "LONG", "FLOAT", "DOUBLE", "DECIMAL", "DATE", "TIMESTAMP"}
)

# String-ish → INVERTED index (tokenized, efficient for eq / in / contains).
_INVERTED_DATA_TYPES: frozenset[str] = frozenset({"STRING"})

# VECTOR datatype → vector index (embedding column).
_VECTOR_DATA_TYPES: frozenset[str] = frozenset({"VECTOR", "GEOPOINT"})


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of index field extraction.

    Attributes:
        fields: The IndexField list to pass to DorisIndexStore.create_index_table.
            Includes all storable properties — indexed columns (PRIMARY_KEY/
            INVERTED/RANGE/VECTOR) plus STORED_ONLY full-detail columns.
        stored_columns: Physical column names of ALL fields (indexed + stored),
            used by IndexSyncService.sync_now to read the full column set from
            Iceberg when backfilling Doris.
        skipped: Properties skipped (api_name, reason) — for logging/metrics.
    """

    fields: list[IndexField]
    stored_columns: list[str]
    skipped: list[tuple[str, str]]


class IndexFieldExtractor:
    """Derive Doris IndexField[] from an ObjectType's properties.

    Stateless and side-effect free — safe to call from any service. All I/O
    (logging) is best-effort.
    """

    def extract(self, properties: Sequence[object], primary_key: str | None = None) -> ExtractionResult:
        """Extract index fields from property definitions.

        Args:
            properties: PropertyDefModel (ORM) or PropertyDef (schema) instances.
                Both expose ``api_name``, ``data_type`` (enum or str),
                ``is_primary_key``, ``indexed``, and a physical column reference
                (``backing_column`` on the ORM, ``backing_mapping.backing_column``
                on the schema). Duck-typing keeps the extractor decoupled from
                the exact type.
            primary_key: The ObjectType's primary_key field name. A property
                whose api_name matches is treated as PRIMARY_KEY even if its
                ``is_primary_key`` flag is not set (callers like the API batch
                create set primary_key at the ObjectType level, not per-property).

        Returns:
            ExtractionResult with fields + skipped reasons.
        """
        fields: list[IndexField] = []
        skipped: list[tuple[str, str]] = []
        stored_columns: list[str] = []

        for p in properties:
            api_name = getattr(p, "api_name", None)
            if not api_name:
                continue

            # Primary key always participates (as the Doris UNIQUE KEY /
            # distribution key). It is PK if either the per-property flag is
            # set OR its api_name matches the ObjectType's primary_key.
            is_pk = getattr(p, "is_primary_key", False) or (primary_key is not None and str(api_name) == primary_key)
            if is_pk:
                # Use the physical column name if available, else the api_name.
                col = _resolve_backing_column(p) or str(api_name)
                fields.append(IndexField(name=col, index_type="PRIMARY_KEY"))
                stored_columns.append(col)
                continue

            # Must map to a real physical column — a logical-only property
            # has no column in Iceberg to mirror into Doris.
            column = _resolve_backing_column(p)
            if column is None:
                skipped.append((str(api_name), "no backing_mapping"))
                continue

            data_type = _str_datatype(getattr(p, "data_type", "STRING"))
            indexed = getattr(p, "indexed", False)

            if indexed and data_type not in _FORMER_REDLINE_DATA_TYPES:
                # Indexed scalar/vector property → give it a real Doris index.
                index_type = self._classify(data_type)
                fields.append(IndexField(name=column, index_type=index_type, data_type=data_type))
            else:
                # Non-indexed property, or a former-redline type that Doris
                # stores serialized (STRUCT/ARRAY/binary-reference). Store the
                # full column without an index so point/filter queries on other
                # columns still return these attributes in one round-trip.
                fields.append(IndexField(name=column, index_type="STORED_ONLY", data_type=data_type))
            stored_columns.append(column)

        if skipped:
            _log.info(
                "IndexFieldExtractor: skipped %d property(ies): %s",
                len(skipped),
                skipped,
            )
        # Fallback: ``define_object_type`` (the single-type create path) calls
        # provision with ``properties=[]`` because properties arrive later via
        # ``add_property``. In that case the loop above produced no fields, but
        # ``primary_key`` is known — synthesize a PRIMARY_KEY field so the Doris
        # index table is created with at least the PK column (otherwise the DDL
        # degenerates to ``CREATE TABLE (...)`` with an empty column list, which
        # Doris rejects with ``no viable alternative at input '(\n\n)'``).
        # Properties added later via ``add_property`` → ``rebuild`` will expand
        # the table with the full indexed column set.
        if not fields and primary_key:
            pk_col = str(primary_key)
            fields.append(IndexField(name=pk_col, index_type="PRIMARY_KEY"))
            stored_columns.append(pk_col)
        # Doris (and most OLAP engines) require key columns to be an ordered
        # prefix of the schema. PRIMARY_KEY fields must therefore come first
        # in `fields` so create_index_table emits them as the leading columns.
        # The extractor iterates properties in definition order, so a PK that
        # isn't the first property would otherwise land mid-schema and break
        # CREATE TABLE ("Key columns should be a ordered prefix of the schema").
        pk_fields = [f for f in fields if f.index_type == "PRIMARY_KEY"]
        if pk_fields:
            non_pk = [f for f in fields if f.index_type != "PRIMARY_KEY"]
            fields = pk_fields + non_pk
        return ExtractionResult(fields=fields, stored_columns=stored_columns, skipped=skipped)

    @staticmethod
    def _classify(data_type: str) -> Literal["PRIMARY_KEY", "INVERTED", "VECTOR", "RANGE", "STORED_ONLY"]:
        """Map a datatype to a Doris index_type."""
        if data_type in _VECTOR_DATA_TYPES:
            return "VECTOR"
        if data_type in _RANGE_DATA_TYPES:
            return "RANGE"
        # Default to INVERTED for string and any unknown scalar type —
        # inverted is the safest general-purpose index for equality/membership.
        return "INVERTED"


def _resolve_backing_column(prop: object) -> str | None:
    """Resolve the physical Iceberg column name for a property.

    Handles both ORM (``backing_column``) and schema (``backing_mapping.backing_column``).
    Returns None if the property has no physical mapping.
    """
    # ORM: PropertyDefModel.backing_column
    col = getattr(prop, "backing_column", None)
    if col:
        return str(col)
    # Schema: PropertyDef.backing_mapping.backing_column
    pm = getattr(prop, "backing_mapping", None)
    if pm is not None:
        col = getattr(pm, "backing_column", None)
        if col:
            return str(col)
    return None


def _str_datatype(data_type: object) -> str:
    """Normalize a datatype (enum member or str) to an upper-case string."""
    # Enum members expose .value; raw strings are returned as-is.
    value = getattr(data_type, "value", data_type)
    return str(value).upper()
