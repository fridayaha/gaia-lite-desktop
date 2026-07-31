"""pydantic v2 schemas for Index layer (Doris)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class IndexField(BaseModel):
    """Field definition in a Doris object-store table."""

    name: str
    index_type: Literal["PRIMARY_KEY", "INVERTED", "VECTOR", "RANGE", "STORED_ONLY"]
    vector_config: dict[str, Any] | None = None
    data_type: str | None = None


class IndexTable(BaseModel):
    """Doris index table definition."""

    object_type_api_name: str
    source_dataset: str
    fields: list[IndexField] = Field(default_factory=list)
    partition_by: list[str] = Field(default_factory=list)


# NOTE: IndexFilter / IndexQuery / IndexResult (legacy filter-DSL schemas)
# were removed 2026-07-13. DorisIndexStore.query / load_by_filter / aggregate
# (the only consumers) were also removed — they had no production callers and
# used non-parameterized SQL assembly (_build_filter_clause + _escape_val),
# contradicting the parameterized-query red line. Doris reads now go solely
# through execute_sql (TextQL compiler path, fully parameterized) and
# load_by_ids (point lookup). See icd-04-doris-index-store.md §3.3.
