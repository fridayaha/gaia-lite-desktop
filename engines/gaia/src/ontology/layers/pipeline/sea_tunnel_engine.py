"""SeaTunnelEngine — data pipeline management via Apache SeaTunnel.

Generates SeaTunnel configuration files using Jinja2 templates and
submits them to the SeaTunnel Zeta cluster via REST API.

Pipeline types (per architecture):
1. SYNC: Data source → Iceberg (single write entry point, external data)
2. INDEX: Iceberg full-snapshot → Doris (one-shot BATCH backfill, path A,
   serves external data ingestion; object_state sync uses outbox INDEX effect)
3. FILE_SYNC: S3File → Iceberg (ADR-014)
4. KAFKA_INGESTION: Kafka → Iceberg (ADR-014)
5. KAFKA_TIMESERIES: Kafka → TimescaleDB (ADR-015)
6. EXTERNAL_CDC: external MySQL/PG/... → Iceberg (ADR-014)

2026-07-08 (去 SeaTunnel 化): ACTION_CDC (PG action_execution_logs → Iceberg
audit trail) + PG_TO_KAFKA + KAFKA_TO_DORIS + DUAL_SINK (object_state 同步)
have been removed. object_state sync now uses outbox INDEX/ARCHIVE effect
(OutboxExecutor + SyncFlushScheduler). SeaTunnel retains only external data
source ingestion duties (ADR-014) + path A backfill (external Iceberg→Doris).
See docs/design/action-sync-outbox-design.md.

Per architecture: SeaTunnel is the PipelineBuilder for external data ingestion.
object_state (Gaia self-managed) sync does NOT go through SeaTunnel.
"""

import logging
from typing import Any

import httpx
from jinja2 import Environment

from ontology.config.settings import settings
from ontology.core import naming
from ontology.core.exceptions import OntologyError
from ontology.core.schemas.pipeline import (
    PipelineDef,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    PipelineTransform,
)

_env = Environment()

_log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# Pipeline Configuration Templates (HOCON format, Jinja2 rendered)
# ═══════════════════════════════════════════════════════════════════

PIPELINE_SYNC_TEMPLATE = """env {
  parallelism = {{ parallelism | default(1) }}
  job.mode = "{{ job_mode | default('BATCH') }}"
  checkpoint.interval = 30000
}

source {
  Jdbc {
    driver = "{{ source.driver }}"
    url = "{{ source.url }}"
    user = "{{ source.user }}"
    password = "{{ source.password }}"
    query = "{{ source.query | default('SELECT * FROM ' ~ source.table, true) }}"
    connection_check_timeout_sec = 100
    {#- SeaTunnel 2.3.13's Jdbc source initializes an AbstractJdbcCatalog
        even in query-only mode; AbstractJdbcCatalog.<init> requires a
        non-blank catalog name (Preconditions.checkArgument), so an inline
        catalog block must be present. The factory identifier is the JDBC
        dialect: "MySQL" / "PostgreSQL". Without this the job submit fails
        with FactoryException API-06 (Unable to create a source for 'Jdbc'). -#}
    {% set factory = source.catalog_factory or "MySQL" %}
    catalog {
      factory = "{{ factory }}"
    }
  }
}
{% if transforms %}
transform {
{% for t in transforms %}
  {{ t.type | replace("-", "_") | capitalize }} {
    {% for key, value in t.config.items() %}
    {{ key }} = "{{ value }}"
    {% endfor %}
  }{% if not loop.last %},{% endif %}
{% endfor %}
}
{% endif %}
sink {
  Iceberg {
    catalog_name = "default_catalog"
    iceberg.catalog.config = {
      type = "rest"
      uri = "{{ iceberg_rest_uri }}"
      "s3.endpoint" = "{{ s3_endpoint }}"
      "s3.access-key-id" = "{{ s3_access_key_id }}"
      "s3.secret-access-key" = "{{ s3_secret_access_key }}"
      "s3.path-style-access" = "{{ s3_path_style_access | lower }}"
      "s3.region" = "{{ s3_region }}"
    }
    namespace = "ontology"
    table = "{{ target_table }}"
    # Catalog First: Gaia creates the managed Iceberg table (with PK,
    # comments, NULL) via the Gravitino/Iceberg catalog before this job
    # runs. SeaTunnel must NOT recreate the schema — IGNORE keeps the
    # registered metadata intact. Data-save mode follows the sync mode:
    #   snapshot (full_snapshot) → DROP_DATA (overwrite, keeps schema)
    #   append   (incremental)   → APPEND_DATA
    schema_save_mode = "IGNORE"
    data_save_mode = "{{ 'DROP_DATA' if transaction_type == 'snapshot' else 'APPEND_DATA' }}"
    iceberg.table.write-props = {
      "write.format.default" = "parquet"
    }
    case_sensitive = true
  }
}
"""

# ── Multi-source pipelines (multi-source-data-fusion-design.md §六/§七) ──
#
# These templates follow the postmortem-verified Iceberg sink config:
#   - catalog-impl = org.apache.iceberg.rest.RESTCatalog (not type=rest)
#   - NO warehouse in iceberg.catalog.config (Gravitino /v1/config 404)
#   - table name lowercased (postmortem §4)
# See docs/engineer/seatunnel-iceberg-rest-interop-postmortem.md.

PIPELINE_FILE_SYNC_TEMPLATE = """env {
  parallelism = {{ parallelism | default(2) }}
  job.mode = "{{ job_mode | default('BATCH') }}"
  checkpoint.interval = 30000
}

source {
  S3File {
    path = "{{ source.path }}"
    bucket = "s3a://{{ source.bucket }}"
    fs.s3a.endpoint = "{{ source.endpoint }}"
    access_key = "{{ source.access_key }}"
    secret_key = "{{ source.secret_key }}"
    fs.s3a.aws.credentials.provider = "org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider"
    file_format_type = "{{ source.file_format_type | default('parquet') }}"
    {% if source.schema_fields %}
    schema = {
      fields = {
        {% for name, typ in source.schema_fields.items() %}
        {{ name }} = "{{ typ }}"{% if not loop.last %},{% endif %}
        {% endfor %}
      }
    }
    {% endif %}
    {% if source.delimiter %}
    delimiter = "{{ source.delimiter }}"
    {% endif %}
    {% if source.skip_header_row_number %}
    skip_header_row_number = {{ source.skip_header_row_number }}
    {% endif %}
    {% if source.read_columns %}
    read_columns = [{{ source.read_columns | join('", "') | safe }}]
    {% endif %}
    # path-style access for S3-compatible stores (MinIO/RustFS) — virtual-host
    # style would resolve <bucket>.<host> and fail with UnknownHostException.
    hadoop_s3_properties {
      fs.s3a.path.style.access = true
      fs.s3a.connection.ssl.enabled = false
    }
  }
}

sink {
  Iceberg {
    catalog_name = "default_catalog"
    iceberg.catalog.config = {
      catalog-impl = "org.apache.iceberg.rest.RESTCatalog"
      uri = "{{ iceberg_rest_uri }}"
      "s3.endpoint" = "{{ s3_endpoint }}"
      "s3.access-key-id" = "{{ s3_access_key_id }}"
      "s3.secret-access-key" = "{{ s3_secret_access_key }}"
      "s3.path-style-access" = "{{ s3_path_style_access | lower }}"
      "s3.region" = "{{ s3_region }}"
    }
    namespace = "ontology"
    table = "{{ target_table }}"
    iceberg.table.write-props = {
      "write.format.default" = "parquet"
    }
    case_sensitive = true
  }
}
"""

PIPELINE_KAFKA_INGESTION_TEMPLATE = """env {
  parallelism = {{ parallelism | default(2) }}
  job.mode = "STREAMING"
  checkpoint.interval = 30000
}

source {
  Kafka {
    topic = "{{ source.topic }}"
    bootstrap.servers = "{{ source.bootstrap_servers }}"
    consumer.group = "{{ source.consumer_group | default('gaia_ingest') }}"
    format = "{{ source.format | default('json') }}"
    {% if source.start_mode %}
    start.mode = "{{ source.start_mode }}"
    {% endif %}
    {% if source.kafka_config %}
    kafka.config = {
      {% for k, v in source.kafka_config.items() %}
      {{ k }} = "{{ v }}"
      {% endfor %}
    }
    {% endif %}
    {% if source.schema_fields %}
    schema = {
      fields = {
        {% for name, typ in source.schema_fields.items() %}
        {{ name }} = "{{ typ }}"{% if not loop.last %},{% endif %}
        {% endfor %}
      }
    }
    {% endif %}
    commit_on_checkpoint = true
  }
}

sink {
  Iceberg {
    catalog_name = "default_catalog"
    iceberg.catalog.config = {
      catalog-impl = "org.apache.iceberg.rest.RESTCatalog"
      uri = "{{ iceberg_rest_uri }}"
      "s3.endpoint" = "{{ s3_endpoint }}"
      "s3.access-key-id" = "{{ s3_access_key_id }}"
      "s3.secret-access-key" = "{{ s3_secret_access_key }}"
      "s3.path-style-access" = "{{ s3_path_style_access | lower }}"
      "s3.region" = "{{ s3_region }}"
    }
    namespace = "ontology"
    table = "{{ target_table }}"
    # CDC/streaming mode: upsert on primary key for change events
    {% if source.primary_keys %}
    iceberg.table.primary-keys = "{{ source.primary_keys | join(',') }}"
    {% endif %}
    iceberg.table.write-props = {
      "write.format.default" = "parquet"
    }
    case_sensitive = true
  }
}
"""


# Kafka → TimescaleDB hypertable (graph-reasoning-design.md §5.3, C3 流式独立链路).
# 时序数据走流式独立链路，不经 Iceberg/Action/object_state。SeaTunnel Kafka
# source → JDBC sink (PG/TimescaleDB)。超表由 GeoTimeStore 预建（含 hypertable
# + GiST + 复合索引），sink 用 schema_save_mode=IGNORE 不重建（保护 hypertable）。
# JDBC sink 字段名对齐 SeaTunnel 2.3.13（url/driver/user/table/primary_keys）。
PIPELINE_KAFKA_TIMESERIES_TEMPLATE = """env {
  parallelism = {{ parallelism | default(2) }}
  job.mode = "STREAMING"
  checkpoint.interval = 30000
}

source {
  Kafka {
    topic = "{{ source.topic }}"
    bootstrap.servers = "{{ source.bootstrap_servers }}"
    consumer.group = "{{ source.consumer_group | default('gaia_timeseries_ingest') }}"
    format = "{{ source.format | default('json') }}"
    {% if source.start_mode %}
    start.mode = "{{ source.start_mode }}"
    {% endif %}
    {% if source.schema_fields %}
    schema = {
      fields = {
        {% for name, typ in source.schema_fields.items() %}
        {{ name }} = "{{ typ }}"{% if not loop.last %},{% endif %}
        {% endfor %}
      }
    }
    {% endif %}
    commit_on_checkpoint = true
  }
}

sink {
  Jdbc {
    url = "jdbc:postgresql://{{ pg_host }}:{{ pg_port }}/{{ pg_database }}"
    driver = "org.postgresql.Driver"
    user = "{{ pg_user }}"
    password = "{{ pg_password }}"
    table = "{{ target_table }}"
    # 超表已由 GeoTimeStore 预建（含 hypertable 属性），sink 不重建 schema
    # （重建会破坏 hypertable。IGNORE = 表已存在则直接写）。
    schema_save_mode = "IGNORE"
    data_save_mode = "APPEND_DATA"
    # 时序超表无传统主键（按 timestamp 分片），用 series_id + timestamp 标识。
    {% if source.primary_keys %}
    primary_keys = [{% for pk in source.primary_keys %}"{{ pk }}"{% if not loop.last %}, {% endif %}{% endfor %}]
    {% endif %}
    # 字段名小写对齐超表列名（TimescaleDB 列名 snake_case）。
    field_ide = "LOWERCASE"
    batch_size = 1000
    max_retries = 3
  }
}
"""

# External-source CDC → Iceberg (spike path a, §7.3).
# The source is a user business DB (MySQL-CDC / PostgreSQL-CDC /
# Opengauss-CDC / TiDB-CDC), not Gaia's own PG object_state. Must be
# STREAMING (CDC requires streaming).
PIPELINE_EXTERNAL_CDC_TEMPLATE = """env {
  parallelism = {{ parallelism | default(2) }}
  job.mode = "STREAMING"
  checkpoint.interval = {{ checkpoint_interval | default(10000) }}
}

source {
  {{ source.cdc_connector | default('MySQL-CDC') }} {
    # SeaTunnel 2.3.13 CDC source field names differ per connector family
    # (verified via official docs + live spike, cdc-spike-report.md):
    #   - MySQL-CDC / TiDB-CDC / OpenGauss-CDC / SqlServer-CDC / Oracle-CDC:
    #     `base-url` (full JDBC URL) + `table-names`
    #   - Postgres-CDC: `url` (NOT base-url) + `database-names` +
    #     `schema-names` + `table-names` (3-part db.schema.table) +
    #     `decoding.plugin.name` (NOT plugin.name)
    # The source identifier for PG is `Postgres-CDC` (plugin-mapping.properties),
    # NOT `PostgreSQL-CDC` (API-06 factory-not-found).
    {% if source.cdc_connector == 'Postgres-CDC' or source.cdc_connector == 'PostgreSQL-CDC' %}
    url = "{{ source.base_url }}"
    username = "{{ source.username }}"
    password = "{{ source.password }}"
    database-names = ["{{ source.database_name }}"]
    schema-names = ["{{ source.schema_name | default('public') }}"]
    table-names = ["{{ source.database_name }}.{{ source.schema_name | default('public') }}.{{ source.table_name }}"]
    decoding.plugin.name = "pgoutput"
    slot.name = "{{ source.slot_name | default('gaia_external_slot') }}"
    server-time-zone = "{{ source.server_time_zone | default('Asia/Shanghai') }}"
    {% else %}
    base-url = "{{ source.base_url }}"
    username = "{{ source.username }}"
    password = "{{ source.password }}"
    table-names = ["{{ source.database_name }}.{{ source.table_name }}"]
    server-time-zone = "{{ source.server_time_zone | default('Asia/Shanghai') }}"
    {% if source.cdc_connector == 'TiDB-CDC' and source.pd_addresses %}
    pd-addresses = "{{ source.pd_addresses }}"
    {% endif %}
    {% endif %}
    {% if source.schema_changes_enabled %}
    schema-changes.enabled = true
    {% endif %}
  }
}

sink {
  Iceberg {
    catalog_name = "default_catalog"
    iceberg.catalog.config = {
      catalog-impl = "org.apache.iceberg.rest.RESTCatalog"
      uri = "{{ iceberg_rest_uri }}"
      "s3.endpoint" = "{{ s3_endpoint }}"
      "s3.access-key-id" = "{{ s3_access_key_id }}"
      "s3.secret-access-key" = "{{ s3_secret_access_key }}"
      "s3.path-style-access" = "{{ s3_path_style_access | lower }}"
      "s3.region" = "{{ s3_region }}"
    }
    namespace = "ontology"
    table = "{{ target_table }}"
    # Explicit PK to avoid append-only CDC data loss (SeaTunnel #10747):
    # without primary-keys the sink is append-only and UPDATE/DELETE events
    # produce duplicate rows instead of upserts.
    {% if source.primary_keys %}
    iceberg.table.primary-keys = "{{ source.primary_keys | join(',') }}"
    iceberg.table.upsert-mode-enabled = "true"
    {% endif %}
    {% if source.schema_evolution_enabled %}
    iceberg.table.schema-evolution-enabled = "true"
    {% endif %}
    iceberg.table.write-props = {
      "write.format.default" = "parquet"
    }
    case_sensitive = true
  }
}
"""


def _render_file_sync_config(
    source: dict[str, Any],
    target_table: str,
    job_mode: str = "BATCH",
) -> str:
    template = _env.from_string(PIPELINE_FILE_SYNC_TEMPLATE)
    return template.render(
        source=source,
        target_table=target_table,
        job_mode=job_mode,
        iceberg_rest_uri=settings.seatunnel_iceberg_rest_uri,
        s3_endpoint=settings.seatunnel_s3_endpoint,
        s3_access_key_id=settings.s3_access_key_id,
        s3_secret_access_key=settings.s3_secret_access_key,
        s3_path_style_access=settings.s3_path_style_access,
        s3_region=settings.s3_region,
    )


def _render_kafka_ingestion_config(
    source: dict[str, Any],
    target_table: str,
) -> str:
    template = _env.from_string(PIPELINE_KAFKA_INGESTION_TEMPLATE)
    return template.render(
        source=source,
        target_table=target_table,
        iceberg_rest_uri=settings.seatunnel_iceberg_rest_uri,
        s3_endpoint=settings.seatunnel_s3_endpoint,
        s3_access_key_id=settings.s3_access_key_id,
        s3_secret_access_key=settings.s3_secret_access_key,
        s3_path_style_access=settings.s3_path_style_access,
        s3_region=settings.s3_region,
    )


def _render_kafka_timeseries_config(
    source: dict[str, Any],
    target_table: str,
) -> str:
    """渲染 Kafka→TimescaleDB 超表 sink 配置（graph-reasoning §5.3）。

    JDBC sink 字段对齐 SeaTunnel 2.3.13。超表由 GeoTimeStore 预建，sink
    用 schema_save_mode=IGNORE 不重建（保护 hypertable 属性）。
    """
    template = _env.from_string(PIPELINE_KAFKA_TIMESERIES_TEMPLATE)
    return template.render(
        source=source,
        target_table=target_table,
        pg_host=settings.seatunnel_pg_host,
        pg_port=settings.pg_port,
        pg_database=settings.pg_database,
        pg_user=settings.pg_user,
        pg_password=settings.pg_password,
    )


def _render_external_cdc_config(
    source: dict[str, Any],
    target_table: str,
) -> str:
    template = _env.from_string(PIPELINE_EXTERNAL_CDC_TEMPLATE)
    return template.render(
        source=source,
        target_table=target_table,
        iceberg_rest_uri=settings.seatunnel_iceberg_rest_uri,
        s3_endpoint=settings.seatunnel_s3_endpoint,
        s3_access_key_id=settings.s3_access_key_id,
        s3_secret_access_key=settings.s3_secret_access_key,
        s3_path_style_access=settings.s3_path_style_access,
        s3_region=settings.s3_region,
    )


class SeaTunnelEngine:
    """Data pipeline management via Apache SeaTunnel Zeta API.

    Manages all pipeline lifecycles: MAIN ingestion, INDEX backfill
    (Iceberg→Doris, path A for external data), FILE_SYNC, KAFKA_INGESTION,
    KAFKA_TIMESERIES, EXTERNAL_CDC (ADR-014).

    object_state sync does NOT go through SeaTunnel (uses outbox INDEX/ARCHIVE
    effect since 2026-07-08). See docs/design/action-sync-outbox-design.md.

    Production deployment requires:
        - SeaTunnel plugins: connector-cdc-postgresql, connector-kafka, connector-doris
        - Kafka auto.create.topics.enable = true
        - Doris Unique Key Merge-on-Write tables (DorisIndexStore.create_index_table)

    Args:
        client: Optional pre-configured httpx AsyncClient. If None,
                creates one using settings.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    @property
    def client(self) -> Any:
        """Lazy-initialized httpx client pointing to SeaTunnel REST API."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                base_url=f"http://{settings.seatunnel_host}:{settings.seatunnel_rest_port}",
                timeout=30.0,
            )
        return self._client

    # ══════════════════════════════════════════════════════════════
    # SYNC Pipeline (data source → Iceberg)
    # ══════════════════════════════════════════════════════════════

    async def _build_sync_pipeline(
        self,
        source: dict[str, Any],
        target_dataset: str,
        transforms: list[dict[str, Any]] | None = None,
        job_mode: str = "BATCH",
        transaction_type: str = "snapshot",
    ) -> PipelineDef:
        """Build & submit a data ingestion pipeline (source → Iceberg).

        Internal implementation behind :meth:`create_sync_pipeline` (which
        routes by connector type). If SeaTunnel rejects the config (e.g.
        source connection failure), ``OntologyError`` is raised — the caller
        MUST surface this rather than swallowing it, otherwise the SyncTask
        ends up in a phantom "running" state with no real job behind it.

        The pipeline name is ``sync_{dataset}`` (via
        :func:`naming.sync_pipeline`): the SYNC consumer is a Dataset, whose
        api_name is globally unique, so no ontology prefix is needed for
        isolation (v5.2 §4.3).

        Args:
            job_mode: ``BATCH`` (run once, terminate) or ``STREAMING``
                (run continuously). Defaults to BATCH — a full-snapshot
                sync is a finite job and should terminate when the source
                is exhausted.
        """
        # target_dataset may be "ontology.<dataset_api_name>" or bare; the
        # pipeline name uses the bare dataset api_name (naming validates it).
        dataset_api_name = target_dataset.split(".")[-1]
        pipeline_name = naming.sync_pipeline(dataset_api_name)
        transform_objs = [PipelineTransform(type=t["type"], config=t["config"]) for t in (transforms or [])]

        # Iceberg table names are case-insensitive but Trino's iceberg REST
        # client lower-cases identifiers on lookup, while the REST server
        # preserves the declared case. A table created as "maintenanceTask"
        # is thus unreachable as either "maintenanceTask" or "maintenancetask"
        # from Trino. Convert the Iceberg sink table name to snake_case
        # (all-lower, word boundaries preserved) so it round-trips cleanly
        # through Trino and stays readable. The pipeline name keeps the
        # original api_name for readability (it is not a table identifier).
        from ontology.core.naming import _to_snake

        iceberg_table_name = _to_snake(dataset_api_name)
        config = _render_sync_config_v2(
            source=source,
            target_table=iceberg_table_name,
            transforms=transforms or [],
            job_mode=job_mode,
            transaction_type=transaction_type,
        )

        job_id = await self._submit_job(pipeline_name, config)

        return PipelineDef(
            name=pipeline_name,
            type="SYNC",
            source=PipelineSource(type="jdbc", config=source),
            transforms=transform_objs,
            sink=PipelineSink(type="iceberg", config={"table": iceberg_table_name}),
            job_id=job_id,
        )

    # Pipeline Lifecycle
    # ══════════════════════════════════════════════════════════════

    async def start(self, name: str, config: str | None = None) -> PipelineStatus:
        """Start (or re-start) a pipeline by name.

        SeaTunnel Zeta has no explicit "start" endpoint — a job runs as
        soon as it is submitted, and there is no way to resume a terminal
        job by name alone. Therefore starting really means one of:

          1. If a job with this ``name`` is already RUNNING in SeaTunnel,
             do nothing and return its current status.
          2. Otherwise, re-submit the job from ``config``. The caller
             (DataSourceService) is responsible for providing the config;
             without it we cannot restart, and we raise ``OntologyError``
             so the UI shows a real failure instead of a silent no-op.

        Args:
            name: SeaTunnel job name (== SyncTask.pipeline_name).
            config: Rendered SeaTunnel job config. Required when the job
                is not already running; ignored when it is.

        Returns:
            PipelineStatus reflecting SeaTunnel's view after the call.
        """
        current = await self.get_job_status(name)
        if current.state == "RUNNING":
            return current
        if config is None:
            raise OntologyError(
                f"Cannot start pipeline '{name}': not running in SeaTunnel and no config provided to re-submit."
            )
        await self._submit_job(name, config)
        return await self.get_job_status(name)

    async def stop(self, name: str) -> PipelineStatus:
        """Stop a pipeline by name (stop the SeaTunnel Zeta job).

        SeaTunnel 2.3.13's REST API has **two** job-termination endpoints,
        and only one actually works:
          - ``POST /hazelcast/rest/maps/cancel-job`` — returns
            ``400 {"status":"fail","message":"Missing map name"}`` for
            *any* input in 2.3.13 (the endpoint exists in the Hazelcast
            REST surface but is not wired to a job-cancel handler in the
            V1 API). The original implementation used this and silently
            treated the fail body as success, so **``stop()`` never actually
            stopped any job** — a latent resource leak where every
            ``deprovision``/``rebuild`` left the old STREAMING job running.
          - ``POST /hazelcast/rest/maps/stop-job`` with JSON body
            ``{"jobId": "...", "isStopWithSavePoint": false}`` — the
            correct V1 endpoint, verified working against 2.3.13.

        ``stop-job`` is keyed by *jobId* (not jobName), so we first resolve
        the name → jobId via :meth:`get_job_status`. If the job is already
        terminal (not in running-jobs) there is nothing to stop — return
        its terminal status. This makes ``stop`` idempotent.
        """
        # Resolve name → jobId + current state. If the job is already
        # terminal, get_job_status returns that terminal state and we have
        # nothing to stop.
        current = await self.get_job_status(name)
        if current.state not in ("RUNNING", "UNKNOWN"):
            # Already terminal (FINISHED/CANCELED/FAILED/...) — nothing to do.
            return current
        if not current.job_id:
            # No jobId resolved (job never ran, or evicted from history).
            # Desired end state ("not running") already holds.
            return current

        url = "/hazelcast/rest/maps/stop-job"
        try:
            resp = await self.client.post(
                url,
                json={"jobId": current.job_id, "isStopWithSavePoint": False},
            )
            # stop-job returns 200 + {"jobId": "..."} on success, or a
            # fail body if the job is already terminal/unknown. A fail body
            # here is not an error from our POV (desired state holds).
            if resp.status_code != 200:
                _log.warning(
                    "SeaTunnelEngine.stop: stop-job for %s (jobId=%s) returned "
                    "HTTP %s: %s; treating as already-stopped",
                    name,
                    current.job_id,
                    resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:
            raise OntologyError(f"Failed to stop pipeline {name}: {exc}") from exc
        return await self.get_job_status(name)

    async def get_status(self, name: str) -> PipelineStatus:
        """Get the current status of a pipeline.

        Returns ``state="UNKNOWN"`` (not an exception) when the job is
        absent from SeaTunnel — this happens when the job was never
        submitted, or was evicted from the finished-jobs history window.
        Callers can distinguish "never ran" from "ran and finished" via
        the state field.
        """
        return await self.get_job_status(name)

    async def _fetch_running_jobs(self) -> list[dict[str, Any]]:
        """Fetch the full running-jobs list from SeaTunnel.

        Returns ``[]`` on the known 2.3.13 NPE shape (HTTP 500 body
        ``{"status":"fail"}``) so callers can treat it as "no running jobs".
        Raises ``OntologyError`` on genuine transport/HTTP errors.
        """
        try:
            resp = await self.client.get("/hazelcast/rest/maps/running-jobs")
            resp.raise_for_status()
            return resp.json() or []
        except Exception as exc:
            raise OntologyError(f"SeaTunnel API error (running-jobs): {exc}") from exc

    async def _fetch_finished_jobs(self) -> list[dict[str, Any]]:
        """Fetch the full finished-jobs list from SeaTunnel.

        Returns ``[]`` on the known 2.3.13 NPE shape (HTTP 500 body
        ``{"status":"fail"}``) so callers can treat it as "no finished jobs".
        Raises ``OntologyError`` on genuine transport/HTTP errors.
        """
        try:
            resp = await self.client.get("/hazelcast/rest/maps/finished-jobs")
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as http_exc:
                body_text = resp.text if hasattr(resp, "text") else ""
                if '"status":"fail"' in body_text or '"status": "fail"' in body_text:
                    return []
                raise OntologyError(f"SeaTunnel API error (finished-jobs): {http_exc}") from http_exc
            return resp.json() or []
        except httpx.HTTPStatusError:
            raise  # re-raise genuine HTTP errors
        except OntologyError:
            raise
        except Exception as exc:
            raise OntologyError(f"SeaTunnel API error (finished-jobs): {exc}") from exc

    @staticmethod
    def _status_from_job(job: dict[str, Any], name: str) -> PipelineStatus:
        """Build a PipelineStatus from a SeaTunnel job dict."""
        return PipelineStatus(
            name=name,
            state=_normalize_state(job.get("jobStatus", "UNKNOWN")),
            job_id=str(job.get("jobId")) if job.get("jobId") is not None else None,
            records_processed=_extract_records(job),
            error_msg=job.get("errorMsg"),
        )

    async def get_job_status(self, name: str) -> PipelineStatus:
        """Resolve a pipeline's true status by name from SeaTunnel.

        SeaTunnel Zeta exposes two endpoints we need:
          - ``GET /running-jobs``  -> list of active jobs (jobId/jobName/jobStatus)
          - ``GET /finished-jobs`` -> list of terminal jobs incl. errorMsg
        Neither is keyed by name, so we fetch both and match on jobName.
        A finished job's status is one of FINISHED / CANCELED / FAILED /
        IN_CANCELED etc.; we normalize to the PipelineStatus.state union.

        The old implementation called ``/job-info?jobName=...`` which is
        wrong — ``job-info`` is keyed by *jobId*, and returns
        ``{"status":"fail"}`` for any name input. That made every status
        lookup look like an API error, which the service layer then
        swallowed, leaving stale "RUNNING" in the DB forever.
        """
        # Check running jobs first.
        running = await self._fetch_running_jobs()
        for job in running:
            if str(job.get("jobName", "")) == name:
                return self._status_from_job(job, name)

        # Not running — check finished jobs.
        # finished-jobs is ordered newest-first by finishTime in practice,
        # but we sort defensively to pick the latest finish for this name.
        finished = await self._fetch_finished_jobs()
        matches = [j for j in finished if str(j.get("jobName", "")) == name]
        if matches:
            job = max(matches, key=lambda j: j.get("finishTime", ""))
            return self._status_from_job(job, name)

        # Genuinely absent — never submitted, or aged out of history.
        return PipelineStatus(name=name, state="UNKNOWN")

    async def get_jobs_status_batch(self, names: set[str]) -> dict[str, PipelineStatus]:
        """Resolve the status of many pipelines in **2 SeaTunnel calls**.

        Replaces the N+1 anti-pattern where ``get_job_status`` is called
        once per task (each call re-fetching the full running + finished
        lists). This method fetches both lists once and matches all names
        locally.

        Args:
            names: pipeline names to look up. Empty set returns ``{}``.

        Returns:
            Mapping ``pipeline_name -> PipelineStatus``. Names absent from
            both SeaTunnel lists map to ``state="UNKNOWN"`` (never
            submitted / aged out of history). Never raises — SeaTunnel
            errors degrade to UNKNOWN for every name so callers can still
            render PG-stored status.
        """
        if not names:
            return {}

        try:
            running = await self._fetch_running_jobs()
        except Exception:
            # SeaTunnel unreachable — degrade every name to UNKNOWN.
            return {name: PipelineStatus(name=name, state="UNKNOWN") for name in names}

        running_by_name: dict[str, dict[str, Any]] = {}
        for job in running:
            jname = str(job.get("jobName", ""))
            if jname in names:
                running_by_name[jname] = job

        unresolved = names - running_by_name.keys()
        if not unresolved:
            return {n: self._status_from_job(running_by_name[n], n) for n in names}

        try:
            finished = await self._fetch_finished_jobs()
        except Exception:
            # finished-jobs unavailable — unresolved names get UNKNOWN.
            partial: dict[str, PipelineStatus] = {
                n: self._status_from_job(running_by_name[n], n) for n in running_by_name
            }
            partial.update({n: PipelineStatus(name=n, state="UNKNOWN") for n in unresolved})
            return partial

        # finished-jobs may contain multiple entries per name (re-runs);
        # keep the latest by finishTime.
        finished_by_name: dict[str, dict[str, Any]] = {}
        for job in finished:
            jname = str(job.get("jobName", ""))
            if jname not in unresolved:
                continue
            prev = finished_by_name.get(jname)
            if prev is None or job.get("finishTime", "") > prev.get("finishTime", ""):
                finished_by_name[jname] = job

        result: dict[str, PipelineStatus] = {}
        for n in running_by_name:
            result[n] = self._status_from_job(running_by_name[n], n)
        for n in unresolved:
            if n in finished_by_name:
                result[n] = self._status_from_job(finished_by_name[n], n)
            else:
                result[n] = PipelineStatus(name=n, state="UNKNOWN")
        return result

    async def create_sync_pipeline(
        self,
        connector_type: str,
        source_config: dict[str, Any],
        target_dataset: str,
        transforms: list[dict[str, Any]] | None = None,
        job_mode: str = "BATCH",
        transaction_type: str = "snapshot",
    ) -> PipelineDef:
        """Create a sync pipeline based on connector type.

        Routes to the correct SeaTunnel Source template based on
        connector_type. Currently supports JDBC sources (mysql, postgresql).
        Future: S3, Kafka, etc.

        source_config is passed directly to the Jinja2 template.
        It must contain keys: driver, url, user, password, table.
        ``query`` is optional and overrides the default ``SELECT *``.
        """
        return await self._build_sync_pipeline(
            source=source_config,
            target_dataset=target_dataset,
            transforms=transforms,
            job_mode=job_mode,
            transaction_type=transaction_type,
        )

    # ══════════════════════════════════════════════════════════════
    # Multi-source pipelines (multi-source-data-fusion-design.md §6/§7)
    # ══════════════════════════════════════════════════════════════

    async def create_file_sync_pipeline(
        self,
        source_config: dict[str, Any],
        target_dataset: str,
        job_mode: str = "BATCH",
    ) -> PipelineDef:
        """Create a file/object-storage → Iceberg sync pipeline (§6.3).

        Reads files (Parquet/CSV/JSON/ORC/Avro) from S3/MinIO/OSS/HDFS via
        SeaTunnel S3File and lands them into an Iceberg managed table.
        File/object storage has no VIRTUAL federation path — landing is the
        only option (§8.1).

        Args:
            source_config: SeaTunnel S3File source config. Must contain:
                path, bucket, access_key, secret_key, endpoint.
                Optional: file_format_type (default parquet), schema_fields
                (required for CSV/JSON), delimiter, read_columns.
            target_dataset: Target Iceberg table name (lowercased).
            job_mode: "BATCH" (full snapshot) or "STREAMING" (incremental).
        """
        pipeline_name = f"file_sync_{target_dataset}"
        config = _render_file_sync_config(source_config, target_dataset, job_mode)
        await self._submit_job(pipeline_name, config)
        return PipelineDef(
            name=pipeline_name,
            type="FILE_SYNC",
            source=PipelineSource(type="s3file", config=source_config),
            transforms=[],
            sink=PipelineSink(type="iceberg", config={"table": target_dataset}),
        )

    async def create_kafka_ingestion_pipeline(
        self,
        source_config: dict[str, Any],
        target_dataset: str,
    ) -> PipelineDef:
        """Create a Kafka → Iceberg streaming ingestion pipeline (§6.4 path B).

        Persists Kafka messages into Iceberg for time-travel / ontology
        binding. SeaTunnel handles schema, Exactly-once, and consumer-group
        management. STREAMING mode (Kafka is a streaming source).

        Args:
            source_config: SeaTunnel Kafka source config. Must contain:
                topic, bootstrap_servers. Optional: consumer_group
                (default gaia_ingest — use distinct groups per pipeline to
                avoid collision with internal Action CDC consumers),
                format (default json), schema_fields, primary_keys.
            target_dataset: Target Iceberg table name (lowercased).
        """
        pipeline_name = f"kafka_ingest_{target_dataset}"
        config = _render_kafka_ingestion_config(source_config, target_dataset)
        await self._submit_job(pipeline_name, config)
        return PipelineDef(
            name=pipeline_name,
            type="KAFKA_INGESTION",
            source=PipelineSource(type="kafka", config=source_config),
            transforms=[],
            sink=PipelineSink(type="iceberg", config={"table": target_dataset}),
        )

    async def create_kafka_timeseries_pipeline(
        self,
        source_config: dict[str, Any],
        target_hypertable: str,
    ) -> PipelineDef:
        """Create a Kafka → TimescaleDB hypertable streaming pipeline (§5.3, C3).

        动态时序数据走流式独立链路（不经 Iceberg/Action/object_state）。
        SeaTunnel Kafka source → JDBC sink (PG/TimescaleDB 超表)。超表必须由
        GeoTimeStore.create_timeseries_hypertable 预建（sink 用 IGNORE 不重建）。

        Args:
            source_config: SeaTunnel Kafka source config. Must contain:
                topic, bootstrap_servers. Optional: consumer_group
                (default gaia_timeseries_ingest), format (default json),
                schema_fields (列名/类型，对齐超表列), primary_keys
                (时序超表通常 series_id+timestamp)。
            target_hypertable: Target TimescaleDB hypertable name
                (naming.timeseries_hypertable 生成，snake_case 三段式)。
        """
        pipeline_name = f"kafka_ts_{target_hypertable}"
        config = _render_kafka_timeseries_config(source_config, target_hypertable)
        await self._submit_job(pipeline_name, config)
        return PipelineDef(
            name=pipeline_name,
            type="KAFKA_TIMESERIES",
            source=PipelineSource(type="kafka", config=source_config),
            transforms=[],
            sink=PipelineSink(type="jdbc", config={"table": target_hypertable}),
        )

    async def create_external_cdc_pipeline(
        self,
        source_config: dict[str, Any],
        target_dataset: str,
    ) -> PipelineDef:
        """Create an external-source CDC → Iceberg pipeline (§7.3 spike path a).

        Streams changes from an external business DB (MySQL-CDC /
        PostgreSQL-CDC / Opengauss-CDC / TiDB-CDC) into an Iceberg managed
        table. STREAMING mode (CDC requires it).

        Prerequisites (see §7.3.4):
          - MySQL: binlog row mode enabled
          - PostgreSQL/OpenGauss: wal_level=logical + replication slot privs
          - TiDB: PD addresses configured
          - explicit primary_keys to avoid append-only CDC data loss (#10747)

        Args:
            source_config: CDC source config. Must contain: cdc_connector
                ("MySQL-CDC" | "PostgreSQL-CDC" | "Opengauss-CDC" |
                "TiDB-CDC"), and either:
                  - base_url (full JDBC URL, e.g. jdbc:mysql://host:3306/db), OR
                  - hostname, port, database_name (base_url auto-built).
                Plus username, password, table_name. Optional: server_time_zone,
                primary_keys (strongly recommended), pd_addresses (TiDB),
                slot_name (PG), schema_changes_enabled / schema_evolution_enabled
                (schema evolution, default off).
            target_dataset: Target Iceberg table name (lowercased).
        """
        # SeaTunnel 2.3.13 CDC source needs base-url (full JDBC URL).
        # Auto-build from hostname/port/database_name if base_url not given.
        cfg = dict(source_config)
        if not cfg.get("base_url"):
            cdc_connector = cfg.get("cdc_connector", "MySQL-CDC")
            host = cfg.get("hostname", "")
            port = cfg.get("port", "")
            db = cfg.get("database_name", "")
            # Determine JDBC URL scheme from cdc_connector type
            if "MySQL" in cdc_connector or "TiDB" in cdc_connector:
                scheme = "mysql"
            elif "Postgres" in cdc_connector or "Opengauss" in cdc_connector:
                scheme = "postgresql"
            else:
                scheme = cdc_connector.lower().replace("-cdc", "")
            cfg["base_url"] = f"jdbc:{scheme}://{host}:{port}/{db}"
        pipeline_name = f"ext_cdc_{target_dataset}"
        config = _render_external_cdc_config(cfg, target_dataset)
        await self._submit_job(pipeline_name, config)
        cdc_type = cfg.get("cdc_connector", "MySQL-CDC").lower()
        return PipelineDef(
            name=pipeline_name,
            type="EXTERNAL_CDC",
            source=PipelineSource(type=cdc_type, config=cfg),
            transforms=[],
            sink=PipelineSink(type="iceberg", config={"table": target_dataset}),
        )

    async def _submit_job(self, name: str, config: str) -> str:
        """Submit a job config to SeaTunnel REST API.

        POST /hazelcast/rest/maps/submit-job?jobName=<name>
        Body is the rendered config string from a Jinja2 template. SeaTunnel
        accepts both JSON and HOCON config formats, so the rendered text is
        sent as-is (text/plain) rather than parsed/forced into JSON.

        CRITICAL: SeaTunnel returns HTTP 200 for BOTH success and failure.
          success: {"jobId": "...", "jobName": "..."}
          failure: {"status": "fail", "message": "..."}
        So ``raise_for_status()`` alone is insufficient — we must parse the
        body and raise on ``status == "fail"``. The previous implementation
        only checked the HTTP status, so failed submits were silently
        treated as success and the SyncTask was marked RUNNING with no
        real job behind it.

        Returns:
            The jobId assigned by SeaTunnel on success.

        Raises:
            OntologyError: on transport error or SeaTunnel-reported failure.
        """
        url = f"/hazelcast/rest/maps/submit-job?jobName={name}&format=hocon"
        try:
            response = await self.client.post(url, content=config.encode("utf-8"))
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise OntologyError(f"Failed to submit SeaTunnel job {name}: {exc}") from exc

        if isinstance(data, dict) and data.get("status") == "fail":
            message = data.get("message") or "SeaTunnel rejected the job config"
            raise OntologyError(
                f"SeaTunnel rejected job {name}: {message}. Check seatunnel-master logs for the underlying exception."
            )

        job_id = data.get("jobId") if isinstance(data, dict) else None
        return str(job_id) if job_id is not None else ""


# ═══════════════════════════════════════════════════════════════════
# SeaTunnel status normalization helpers
# ═══════════════════════════════════════════════════════════════════

# SeaTunnel Zeta JobStatus enum values we care about. Reference:
# apache/seatunnel engine/common/JobStatus.java. Anything we don't
# explicitly map is surfaced as UNKNOWN so the UI shows a real (if
# unfamiliar) string rather than silently downgrading to a green state.
_STATE_MAP: dict[str, str] = {
    "RUNNING": "RUNNING",
    "FINISHED": "FINISHED",
    "CANCELED": "CANCELED",
    "CANCELLED": "CANCELED",
    "FAILED": "FAILED",
    "FAILING": "FAILED",
    # SeaTunnel reports "STOPPED" for some terminal paths; keep as-is.
    "STOPPED": "STOPPED",
}


def _normalize_state(raw: str | None) -> str:
    """Map a raw SeaTunnel jobStatus string onto the PipelineStatus union."""
    if not raw:
        return "UNKNOWN"
    return _STATE_MAP.get(str(raw).upper(), "UNKNOWN")


def _extract_records(job: dict[str, Any]) -> int:
    """Best-effort extraction of total records processed from a job dict.

    SeaTunnel nests per-source/per-sink counts under
    ``metrics.TableSinkWriteCount`` (finished jobs) or
    ``metrics.TableSourceReceivedCount`` (running jobs). We sum the sink
    write counts when present, else the source received counts. Returns 0
    when the shape is unexpected — this field is informational only.
    """
    metrics = job.get("metrics")
    if not isinstance(metrics, dict):
        return 0
    for key in ("TableSinkWriteCount", "TableSourceReceivedCount"):
        bucket = metrics.get(key)
        if isinstance(bucket, dict) and bucket:
            total = 0
            for value in bucket.values():
                try:
                    total += int(float(value))
                except (TypeError, ValueError):
                    continue
            return total
    return 0


# ═══════════════════════════════════════════════════════════════════
# Template Rendering Helpers
# ═══════════════════════════════════════════════════════════════════


# Map connector_type → SeaTunnel Jdbc catalog factory identifier.
# SeaTunnel 2.3.13's Jdbc source initializes an AbstractJdbcCatalog even in
# query-only mode; AbstractJdbcCatalog.<init> requires a non-blank catalog
# name, so the inline catalog block must declare the right factory.
_JDBC_CATALOG_FACTORY: dict[str, str] = {
    "mysql": "MySQL",
    "mariadb": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "starrocks": "StarRocks",  # SeaTunnel 2.3.8+ starrocks jdbc dialect (#7294)
    "tidb": "MySQL",  # MySQL 协议
    "oceanbase": "MySQL",  # MySQL 模式
}


def _render_sync_config_v2(
    source: dict[str, Any],
    target_table: str,
    transforms: list[dict[str, Any]],
    job_mode: str = "BATCH",
    transaction_type: str = "snapshot",
) -> str:
    # Attach the resolved catalog factory so the template can render the
    # inline catalog block (see _JDBC_CATALOG_FACTORY above).
    source = dict(source)
    source.setdefault(
        "catalog_factory",
        _JDBC_CATALOG_FACTORY.get(str(source.get("connector_type", "")).lower(), "MySQL"),
    )
    template = _env.from_string(PIPELINE_SYNC_TEMPLATE)
    return template.render(
        source=source,
        target_table=target_table,
        transforms=transforms,
        job_mode=job_mode,
        transaction_type=transaction_type,
        iceberg_rest_uri=settings.seatunnel_iceberg_rest_uri,
        s3_endpoint=settings.seatunnel_s3_endpoint,
        s3_access_key_id=settings.s3_access_key_id,
        s3_secret_access_key=settings.s3_secret_access_key,
        s3_path_style_access=settings.s3_path_style_access,
        s3_region=settings.s3_region,
    )

