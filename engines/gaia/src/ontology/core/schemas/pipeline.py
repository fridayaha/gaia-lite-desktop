"""pydantic v2 schemas for Pipeline layer (SeaTunnel)."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class PipelineSource(BaseModel):
    """Data source configuration for a pipeline."""

    type: Literal[
        "jdbc",
        "mysql-cdc",
        "kafka",
        "postgres-cdc",
        "postgresql-cdc",
        "opengauss-cdc",
        "tidb-cdc",
        "file",
        "s3file",
        "iceberg-incremental",
    ]
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineTransform(BaseModel):
    """Transform step in a pipeline."""

    type: Literal["field-mapping", "filter", "mask", "projection", "sql"]
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineSink(BaseModel):
    """Data sink configuration for a pipeline."""

    type: Literal["iceberg", "doris", "kafka", "jdbc"]
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineDef(BaseModel):
    """Pipeline definition (SeaTunnel job config)."""

    name: str
    type: Literal[
        "SYNC",
        "INDEX",
        "FILE_SYNC",
        "KAFKA_INGESTION",
        "EXTERNAL_CDC",
        "KAFKA_TIMESERIES",
    ]
    source: PipelineSource
    transforms: list[PipelineTransform] = Field(default_factory=list)
    sink: PipelineSink
    # SeaTunnel job id assigned on successful submit. Populated by
    # SeaTunnelEngine._submit_job; None means "not yet submitted".
    job_id: str | None = None


class PipelineStatus(BaseModel):
    """Pipeline runtime status.

    ``state`` is normalized from SeaTunnel Zeta's job lifecycle:
      - RUNNING  — job currently executing
      - FINISHED — job completed successfully (terminal)
      - CANCELED — job cancelled by user (terminal)
      - FAILED   — job failed (terminal, ``error_msg`` populated)
      - UNKNOWN  — job not found in SeaTunnel (never submitted, or
                   evicted from the finished-jobs history window)

    The service layer maps terminal/UNKNOWN states back onto the
    SyncTask status column so the UI reflects SeaTunnel truth rather
    than a blindly-written "RUNNING".
    """

    name: str
    # SeaTunnel job lifecycle state, normalized by SeaTunnelEngine.
    # Common values: RUNNING / FINISHED / CANCELED / FAILED / STOPPED /
    # UNKNOWN. Kept as str (not Literal) because SeaTunnel may introduce
    # new terminal states and we don't want a schema migration each time.
    state: str
    job_id: str | None = None
    last_checkpoint: int | None = None
    records_processed: int = 0
    error_msg: str | None = None
