"""Property key mapping between api_name and backing_column.

The operational state store (``object_state.properties`` JSONB) keys its
entries by **backing_column** (the snake_case physical column name shared
with Iceberg / Doris), not by the business-facing **api_name**
(camelCase). This keeps the CDC chain (PG → Kafka → Doris) consistent end
to end: the Kafka message JSON keys, the Doris idx table columns, and the
object_state JSONB keys are all the *same* name, so Doris stream-load can
match columns by name without per-table ``jsonpaths`` mapping (the
multi-table ``tables_configs`` blocker, see
``docs/bugfix/path-b-kafka-doris-schema-mismatch.md``).

Actions expose **api_name** to the outside world (REST / AG-UI / rules).
The conversion happens at the ActionService *write* boundary (api_name →
backing_column before ``upsert_object_state``) and at the read boundaries
(backing_column → api_name when object_state is surfaced to consumers or
merged with api_name rule outputs).

These helpers are the single source of truth for that conversion. They
are tolerant of:

  - properties with no ``backing_mapping`` (synthetic / test OTs) —
    backing_column falls back to api_name, so the key is unchanged;
  - keys not present on the ObjectType (extra fields like ``visibility``,
    legacy data) — passed through unchanged;
  - missing / None ObjectType — passthrough (no rename).

Design rule (architecture redline 9 — physical naming is snake_case): the
backing_column is authoritative; api_name is the business alias. These
helpers never *invent* a mapping — they only translate using the
ObjectType's declared ``backing_mapping``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ontology.core.schemas.ontology import ObjectType, PropertyDef


def _ot_props(ot: ObjectType) -> list[Any]:
    """Safely get an ObjectType's properties list.

    Defensive against mocks / partial objects whose ``properties`` attribute
    is not a real list (returns [] so the conversion becomes a passthrough).
    """
    props = getattr(ot, "properties", None)
    if isinstance(props, list | tuple):
        return list(props)
    return []


def _backing_column_of(prop: PropertyDef) -> str:
    """Resolve the physical column name for a property.

    Falls back to ``api_name`` when no ``backing_mapping`` is declared
    (synthetic / test ObjectTypes, or properties with no dataset binding).
    Defensive against mocks whose ``backing_mapping.backing_column`` is not
    a real string (returns the api_name verbatim in that case).
    """
    pm = getattr(prop, "backing_mapping", None)
    col = getattr(pm, "backing_column", None) if pm is not None else None
    if isinstance(col, str) and col:
        return col
    return str(prop.api_name)


def api_to_backing_map(ot: ObjectType) -> dict[str, str]:
    """Build ``{api_name: backing_column}`` for an ObjectType's properties.

    When api_name and backing_column are identical (no backing_mapping, or
    a mapping whose backing_column equals the api_name), the entry is
    omitted — the caller can treat the dict as "only the keys that need
    renaming", keeping no-op translations out of the hot path.
    """
    result: dict[str, str] = {}
    for p in _ot_props(ot):
        api = str(p.api_name)
        col = _backing_column_of(p)
        if api != col:
            result[api] = col
    return result


def backing_to_api_map(ot: ObjectType) -> dict[str, str]:
    """Build ``{backing_column: api_name}`` (inverse of :func:`api_to_backing_map`)."""
    result: dict[str, str] = {}
    for p in _ot_props(ot):
        api = str(p.api_name)
        col = _backing_column_of(p)
        if api != col:
            result[col] = api
    return result


def api_to_backing(ot: ObjectType | None, props: dict[str, Any]) -> dict[str, Any]:
    """Rename property dict keys from api_name → backing_column.

    Keys not declared on the ObjectType pass through unchanged (extra
    fields like ``visibility``, legacy data). ``ot`` None → passthrough.
    """
    if ot is None:
        return dict(props)
    mapping = api_to_backing_map(ot)
    if not mapping:
        return dict(props)
    return {mapping.get(k, k): v for k, v in props.items()}


def backing_to_api(ot: ObjectType | None, props: dict[str, Any]) -> dict[str, Any]:
    """Rename property dict keys from backing_column → api_name.

    Inverse of :func:`api_to_backing`. Keys not declared on the ObjectType
    pass through unchanged (so aggregation aliases like ``count`` /
    ``sum_amount`` and extras survive). ``ot`` None → passthrough.
    """
    if ot is None:
        return dict(props)
    mapping = backing_to_api_map(ot)
    if not mapping:
        return dict(props)
    return {mapping.get(k, k): v for k, v in props.items()}


def api_field_to_backing(ot: ObjectType | None, field: str) -> str:
    """Translate a single api_name field → backing_column (passthrough if unknown)."""
    if ot is None:
        return field
    mapping = api_to_backing_map(ot)
    return mapping.get(field, field)
