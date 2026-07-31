"""Unit tests for multi-source SeaTunnel pipelines.

Covers the new pipeline builders from
docs/design/multi-source-data-fusion-design.md §6.3/§6.4/§7.3:

  - create_file_sync_pipeline (S3File → Iceberg, §6.3)
  - create_kafka_ingestion_pipeline (Kafka → Iceberg, §6.4 path B)
  - create_external_cdc_pipeline (external CDC → Iceberg, §7.3 spike path a)

Verifies the postmortem-verified Iceberg sink config is rendered:
  - catalog-impl = org.apache.iceberg.rest.RESTCatalog (not type=rest)
  - NO warehouse in iceberg.catalog.config (Gravitino /v1/config 404)
  - explicit primary-keys on CDC sink (avoid #10747 append-only data loss)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.layers.pipeline.sea_tunnel_engine import (
    SeaTunnelEngine,
    _render_external_cdc_config,
    _render_file_sync_config,
    _render_kafka_ingestion_config,
)


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value=MagicMock(raise_for_status=MagicMock()))
    return client


@pytest.fixture
def engine(mock_client) -> SeaTunnelEngine:
    return SeaTunnelEngine(client=mock_client)


# ── File sync (S3File → Iceberg, §6.3) ──


class TestFileSyncPipeline:
    def test_file_sync_config_postmortem_sink(self):
        """Iceberg sink follows postmortem-verified config (no warehouse, catalog-impl)."""
        config = _render_file_sync_config(
            source={
                "path": "/data/",
                "bucket": "bucket",
                "access_key": "ak",
                "secret_key": "sk",
                "endpoint": "http://rustfs:9000",
                "file_format_type": "parquet",
            },
            target_table="flight_logs",
        )
        assert "S3File" in config
        assert "flight_logs" in config
        # SeaTunnel 2.3.13 S3File 必需配置（live 验证）
        assert "s3a://bucket" in config  # bucket 带 s3a:// 前缀
        assert "fs.s3a.endpoint" in config
        assert "SimpleAWSCredentialsProvider" in config
        assert "fs.s3a.path.style.access" in config  # RustFS/MinIO 必需
        # postmortem: catalog-impl not type=rest
        assert "catalog-impl" in config
        assert "org.apache.iceberg.rest.RESTCatalog" in config
        # postmortem: no warehouse (Gravitino /v1/config 404)
        assert "warehouse" not in config

    def test_file_sync_csv_includes_schema_and_header_skip(self):
        config = _render_file_sync_config(
            source={
                "path": "/data/orders.csv",
                "bucket": "bucket",
                "access_key": "ak",
                "secret_key": "sk",
                "endpoint": "http://rustfs:9000",
                "file_format_type": "csv",
                "delimiter": ",",
                "skip_header_row_number": 1,
                "schema_fields": {"id": "long", "name": "string"},
            },
            target_table="csv_import",
        )
        assert "csv" in config
        assert "delimiter" in config
        assert "skip_header_row_number = 1" in config
        assert "id" in config and "long" in config

    @pytest.mark.asyncio
    async def test_create_file_sync_pipeline(self, engine, mock_client):
        result = await engine.create_file_sync_pipeline(
            source_config={
                "path": "s3://bucket/data/",
                "bucket": "bucket",
                "access_key": "ak",
                "secret_key": "sk",
                "endpoint": "http://rustfs:9000",
                "file_format_type": "parquet",
            },
            target_dataset="flight_logs",
        )
        assert result.type == "FILE_SYNC"
        assert result.name == "file_sync_flight_logs"
        assert result.source.type == "s3file"
        assert result.sink.type == "iceberg"
        mock_client.post.assert_awaited()


# ── Kafka ingestion (Kafka → Iceberg, §6.4 path B) ──


class TestKafkaIngestionPipeline:
    def test_kafka_config_streaming_mode(self):
        config = _render_kafka_ingestion_config(
            source={
                "topic": "events",
                "bootstrap_servers": "kafka:9092",
                "consumer_group": "gaia_ingest_events",
                "format": "json",
            },
            target_table="event_log",
        )
        assert "Kafka" in config
        assert "events" in config
        assert "kafka:9092" in config
        assert "gaia_ingest_events" in config
        # format 字段（数据格式 json/text），非 pattern（topic 正则）
        assert 'format = "json"' in config
        assert "pattern" not in config
        # streaming source
        assert "STREAMING" in config
        # postmortem sink config
        assert "catalog-impl" in config
        assert "warehouse" not in config

    def test_kafka_config_with_start_mode_earliest(self):
        config = _render_kafka_ingestion_config(
            source={
                "topic": "events",
                "bootstrap_servers": "kafka:9092",
                "start_mode": "earliest",
                "kafka_config": {"auto.offset.reset": "earliest"},
            },
            target_table="event_log",
        )
        assert 'start.mode = "earliest"' in config
        assert "auto.offset.reset" in config

    def test_kafka_config_with_primary_keys(self):
        config = _render_kafka_ingestion_config(
            source={
                "topic": "events",
                "bootstrap_servers": "kafka:9092",
                "primary_keys": ["event_id"],
            },
            target_table="event_log",
        )
        assert "iceberg.table.primary-keys" in config
        assert "event_id" in config

    @pytest.mark.asyncio
    async def test_create_kafka_ingestion_pipeline(self, engine, mock_client):
        result = await engine.create_kafka_ingestion_pipeline(
            source_config={
                "topic": "events",
                "bootstrap_servers": "kafka:9092",
            },
            target_dataset="event_log",
        )
        assert result.type == "KAFKA_INGESTION"
        assert result.name == "kafka_ingest_event_log"
        mock_client.post.assert_awaited()


# ── External CDC (external-source CDC → Iceberg, §7.3) ──


class TestExternalCdcPipeline:
    def test_mysql_cdc_config(self):
        config = _render_external_cdc_config(
            source={
                "cdc_connector": "MySQL-CDC",
                "base_url": "jdbc:mysql://mysql.internal:3306/erp",
                "username": "user",
                "password": "pass",
                "database_name": "erp",
                "table_name": "orders",
                "primary_keys": ["id"],
            },
            target_table="erp_orders",
        )
        assert "MySQL-CDC" in config
        assert "base-url" in config
        assert "jdbc:mysql://mysql.internal:3306/erp" in config
        assert "erp.orders" in config  # table-names = ["database.table"]
        assert "STREAMING" in config
        # postmortem sink config
        assert "catalog-impl" in config
        assert "warehouse" not in config
        assert "iceberg.table.primary-keys" in config
        assert "upsert-mode-enabled" in config

    def test_postgresql_cdc_includes_slot(self):
        config = _render_external_cdc_config(
            source={
                "cdc_connector": "PostgreSQL-CDC",
                "base_url": "jdbc:postgresql://pg.internal:5432/app",
                "username": "user",
                "password": "pass",
                "database_name": "app",
                "table_name": "users",
                "slot_name": "gaia_external_slot",
                "primary_keys": ["id"],
            },
            target_table="app_users",
        )
        assert "PostgreSQL-CDC" in config
        assert "pgoutput" in config  # plugin.name
        assert "gaia_external_slot" in config

    def test_tidb_cdc_includes_pd_addresses(self):
        config = _render_external_cdc_config(
            source={
                "cdc_connector": "TiDB-CDC",
                "base_url": "jdbc:mysql://tidb.internal:4000/app",
                "username": "user",
                "password": "pass",
                "database_name": "app",
                "table_name": "orders",
                "pd_addresses": "pd:2379",
                "primary_keys": ["id"],
            },
            target_table="tidb_orders",
        )
        assert "TiDB-CDC" in config
        assert "pd-addresses" in config
        assert "pd:2379" in config

    def test_external_cdc_without_primary_keys_omits_pk_block(self):
        # PK is optional but strongly recommended; template must render without it
        config = _render_external_cdc_config(
            source={
                "cdc_connector": "MySQL-CDC",
                "base_url": "jdbc:mysql://mysql.internal:3306/erp",
                "username": "user",
                "password": "pass",
                "database_name": "erp",
                "table_name": "orders",
            },
            target_table="erp_orders",
        )
        assert "iceberg.table.primary-keys" not in config

    @pytest.mark.asyncio
    async def test_create_external_cdc_pipeline(self, engine, mock_client):
        result = await engine.create_external_cdc_pipeline(
            source_config={
                "cdc_connector": "MySQL-CDC",
                "hostname": "mysql.internal",
                "port": "3306",
                "username": "user",
                "password": "pass",
                "database_name": "erp",
                "table_name": "orders",
                "primary_keys": ["id"],
            },
            target_dataset="erp_orders",
        )
        assert result.type == "EXTERNAL_CDC"
        assert result.name == "ext_cdc_erp_orders"
        assert result.source.type == "mysql-cdc"
        # base_url auto-built from hostname/port/database_name
        assert result.source.config["base_url"] == "jdbc:mysql://mysql.internal:3306/erp"
        mock_client.post.assert_awaited()


# ── Kafka → TimescaleDB hypertable (graph-reasoning §5.3, C3 流式独立链路) ──


class TestKafkaTimeseriesPipeline:
    def test_timeseries_config_jdbc_sink_postgres(self):
        from ontology.layers.pipeline.sea_tunnel_engine import _render_kafka_timeseries_config

        config = _render_kafka_timeseries_config(
            source={
                "topic": "vehicle_track",
                "bootstrap_servers": "kafka:9092",
                "schema_fields": {
                    "series_id": "string",
                    "timestamp": "timestamp",
                    "speed": "double",
                },
                "primary_keys": ["series_id", "timestamp"],
            },
            target_table="timeseries_logistics__vehicle__track",
        )
        # Kafka source
        assert "Kafka" in config
        assert "vehicle_track" in config
        assert "STREAMING" in config
        # JDBC sink (PG/TimescaleDB)
        assert "Jdbc" in config
        assert "org.postgresql.Driver" in config
        assert "jdbc:postgresql://" in config
        assert "timeseries_logistics__vehicle__track" in config
        # 超表保护：schema_save_mode=IGNORE（不重建，保护 hypertable）
        assert 'schema_save_mode = "IGNORE"' in config
        assert 'data_save_mode = "APPEND_DATA"' in config
        # 字段名小写对齐超表列
        assert 'field_ide = "LOWERCASE"' in config
        # primary_keys 渲染
        assert "series_id" in config and "timestamp" in config

    def test_timeseries_config_no_primary_keys(self):
        from ontology.layers.pipeline.sea_tunnel_engine import _render_kafka_timeseries_config

        config = _render_kafka_timeseries_config(
            source={"topic": "t", "bootstrap_servers": "kafka:9092"},
            target_table="ts_table",
        )
        # 无 primary_keys 时不渲染 primary_keys 行
        assert "primary_keys" not in config

    async def test_create_kafka_timeseries_pipeline(self, engine, mock_client):
        result = await engine.create_kafka_timeseries_pipeline(
            source_config={
                "topic": "vehicle_track",
                "bootstrap_servers": "kafka:9092",
                "schema_fields": {"series_id": "string", "timestamp": "timestamp"},
            },
            target_hypertable="timeseries_logistics__vehicle__track",
        )
        assert result.type == "KAFKA_TIMESERIES"
        assert result.name == "kafka_ts_timeseries_logistics__vehicle__track"
        assert result.source.type == "kafka"
        assert result.sink.type == "jdbc"
        mock_client.post.assert_awaited()
