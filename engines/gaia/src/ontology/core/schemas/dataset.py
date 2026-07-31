"""pydantic v2 schemas for Dataset layer (Iceberg)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ColumnDef(BaseModel):
    """Physical column definition in an Iceberg table."""

    name: str
    type: str
    nullable: bool = True


class ManagedColumnDef(BaseModel):
    """Column definition for Gaia-managed Iceberg table creation.

    Carries the full physical metadata that Gaia registers into the
    Gravitino/Iceberg catalog when it creates a managed table (Catalog
    First: the physical schema — column comment, NOT-NULL, primary-key —
    is owned by Iceberg, not duplicated in PG).

    Used by both managed-table creation paths:
      - data-source sync (``describe_table`` → ``TableInfo`` → this)
      - ObjectType define (ObjectType properties → this)
    """

    name: str
    type: str
    nullable: bool = True
    comment: str = ""
    is_primary_key: bool = False


class ManagedTableSchema(BaseModel):
    """Schema spec for Gaia-managed Iceberg table creation.

    ``columns`` carry per-column metadata (comment/nullable/PK);
    ``table_comment`` becomes the Iceberg table ``properties.comment``.
    """

    columns: list[ManagedColumnDef] = Field(default_factory=list)
    table_comment: str = ""


class DatasetSchema(BaseModel):
    """Iceberg table schema."""

    columns: list[ColumnDef] = Field(default_factory=list)


class PartitionField(BaseModel):
    """Iceberg partition specification."""

    source_column: str
    transform: Literal["identity", "year", "month", "day", "hour", "bucket"]
    transform_param: int | None = None


class Dataset(BaseModel):
    """Dataset (Iceberg table) metadata."""

    name: str
    schema_: DatasetSchema = Field(alias="schema")
    storage_location: str
    partition_spec: list[PartitionField] = Field(default_factory=list)


class DatasetSnapshot(BaseModel):
    """Immutable Iceberg snapshot."""

    snapshot_id: int
    timestamp: int
    operation: Literal["append", "overwrite", "delete"] = "append"
    summary: dict[str, Any] = Field(default_factory=dict)


class WriteResult(BaseModel):
    """Result of a write operation to Iceberg."""

    snapshot: DatasetSnapshot
    rows_written: int
