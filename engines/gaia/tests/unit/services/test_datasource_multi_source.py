"""Unit tests for multi-source data fusion connector expansion.

Covers the new connector_type entries added by
docs/design/multi-source-data-fusion-design.md §六:

  - 国产库 JDBC 适配（OpenGauss/GaussDB/TiDB + OceanBase/Kingbase/达梦）
  - 通用 JDBC 兜底（generic_jdbc）
  - 文件/对象存储、Kafka、ES、湖仓格式、云数仓的 capability 映射
  - JDBC URL scheme 独立化（避免驱动同名类冲突，§6.1.2）
  - provider=None 品类（达梦/generic_jdbc）跳过 Gravitino catalog 注册
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from ontology.core.schemas.datasource import (
    CAPABILITY_MAP,
    DataSource,
    DataSourceCreate,
)
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.engine.trino_query_engine import TrinoQueryEngine
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.layers.pipeline.sea_tunnel_engine import SeaTunnelEngine
from ontology.services.datasource_service import DataSourceService


@pytest.fixture
def mock_metadata() -> AsyncMock:
    return AsyncMock(spec=PostgresMetaStore)


@pytest.fixture
def mock_catalog() -> AsyncMock:
    catalog = AsyncMock(spec=GravitinoRegistry)
    catalog.get_table_comment.return_value = ""
    return catalog


@pytest.fixture
def mock_engine() -> AsyncMock:
    return AsyncMock(spec=TrinoQueryEngine)


@pytest.fixture
def mock_pipeline() -> AsyncMock:
    return AsyncMock(spec=SeaTunnelEngine)


@pytest.fixture
def mock_dataset() -> AsyncMock:
    return AsyncMock(spec=IcebergStore)


@pytest.fixture
def service(mock_metadata, mock_catalog, mock_engine, mock_pipeline, mock_dataset):
    return DataSourceService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        engine=mock_engine,
        pipeline=mock_pipeline,
        dataset=mock_dataset,
    )


def _make_ds(api_name: str = "testDs", connector_type: str = "mysql") -> DataSource:
    return DataSource(
        id="ds1",
        api_name=api_name,
        display_name=api_name,
        connector_type=connector_type,
        connector_config={},
        status="DISCONNECTED",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_sync_task(api_name: str = "task1"):
    from ontology.core.schemas.datasource import SyncTask

    return SyncTask(
        id="t1",
        api_name=api_name,
        data_source_id="ds1",
        sync_type="table",
        source_config={},
        target_dataset_api_name="target",
        sync_mode="incremental",
        transaction_type="append",
        status="DRAFT",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


# ─────────────────────────────────────────────────────────────
# CAPABILITY_MAP 覆盖度
# ─────────────────────────────────────────────────────────────


class TestCapabilityMap:
    def test_relational_native_dbs_have_cdc_and_virtual_table(self):
        for ct in ("mysql", "postgresql", "postgres", "mariadb"):
            caps = CAPABILITY_MAP[ct]
            assert "cdc" in caps
            assert "virtual_table" in caps

    def test_domestic_dbs_with_native_cdc(self):
        # G2: OpenGauss / GaussDB / TiDB 都有 SeaTunnel 原生 CDC
        for ct in ("opengauss", "gaussdb", "tidb"):
            assert "cdc" in CAPABILITY_MAP[ct]
            assert "virtual_table" in CAPABILITY_MAP[ct]

    def test_oceanbase_no_seatumnel_cdc(self):
        # OceanBase CDC 走 OMS，非 SeaTunnel 原生
        caps = CAPABILITY_MAP["oceanbase"]
        assert "cdc" not in caps
        assert "virtual_table" in caps

    def test_dameng_no_virtual_table(self):
        # 达梦无 Gravitino provider，仅落地
        caps = CAPABILITY_MAP["dameng"]
        assert "virtual_table" not in caps
        assert "cdc" not in caps
        assert "batch_sync" in caps

    def test_generic_jdbc_fallback(self):
        caps = CAPABILITY_MAP["generic_jdbc"]
        assert "explore" in caps
        assert "batch_sync" in caps
        assert "virtual_table" not in caps  # 无 Gravitino catalog
        assert "cdc" not in caps

    def test_file_object_storage_only_file_sync(self):
        for ct in ("s3", "minio", "oss", "hdfs"):
            caps = CAPABILITY_MAP[ct]
            assert "file_sync" in caps
            assert "virtual_table" not in caps  # 裸文件无联邦价值（§8.1）

    def test_lakehouse_formats_virtual_table(self):
        for ct in ("iceberg", "hive", "delta", "hudi", "paimon"):
            assert "virtual_table" in CAPABILITY_MAP[ct]

    def test_kafka_streaming_and_virtual(self):
        caps = CAPABILITY_MAP["kafka"]
        assert "streaming_sync" in caps
        assert "virtual_table" in caps

    def test_elasticsearch_landing_only(self):
        # 决策点 4：严格一刀切，ES 一律落地，不开 Trino 联邦口子
        caps = CAPABILITY_MAP["elasticsearch"]
        assert "virtual_table" not in caps
        assert "batch_sync" in caps

    def test_cloud_warehouse_pg_kernel(self):
        for ct in ("analyticdb_pg", "gaussdb_dws"):
            assert "virtual_table" in CAPABILITY_MAP[ct]
        assert "virtual_table" not in CAPABILITY_MAP["maxcompute"]

    def test_all_connectors_have_explore(self):
        # explore 是所有连接器的基础能力
        for ct, caps in CAPABILITY_MAP.items():
            assert "explore" in caps, f"{ct} missing explore capability"


# ─────────────────────────────────────────────────────────────
# JDBC URL scheme 独立化（§6.1.2 避坑）
# ─────────────────────────────────────────────────────────────


class TestJdbcUrlScheme:
    def test_starrocks_uses_mysql_protocol(self):
        # StarRocks 走 MySQL 协议（FE 9030 兼容），可 VIRTUAL 联邦 + 落地
        caps = CAPABILITY_MAP["starrocks"]
        assert "virtual_table" in caps
        assert "batch_sync" in caps
        url = DataSourceService._build_jdbc_url(
            "starrocks",
            {"host": "sr.internal", "port": "9030", "database": "app"},
        )
        assert url == "jdbc:mysql://sr.internal:9030/app"
        # catalog 注册时 URL 不带 database（mysql 协议）
        url_no_db = DataSourceService._build_jdbc_url(
            "starrocks",
            {"host": "sr.internal", "port": "9030", "database": "app"},
            include_database=False,
        )
        assert url_no_db == "jdbc:mysql://sr.internal:9030"
        assert DataSourceService._resolve_driver("starrocks", {}) == "com.mysql.cj.jdbc.Driver"
        assert DataSourceService._default_port("starrocks") == "9030"

    def test_starrocks_jdbc_factory_dialect(self):
        # SeaTunnel 2.3.8+ starrocks jdbc dialect (#7294)，sync pipeline 应渲染
        # catalog { factory = "StarRocks" } 而非兜底 MySQL
        from ontology.layers.pipeline.sea_tunnel_engine import _render_sync_config_v2
        config = _render_sync_config_v2(
            source={
                "connector_type": "starrocks",
                "driver": "com.mysql.cj.jdbc.Driver",
                "url": "jdbc:mysql://sr:9030/app",
                "user": "root",
                "password": "",
                "table": "orders",
            },
            target_table="sr_orders",
            transforms=[],
        )
        assert 'factory = "StarRocks"' in config
        assert 'factory = "MySQL"' not in config

    def test_opengauss_uses_independent_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "opengauss",
            {"host": "og.internal", "port": "5432", "database": "app"},
        )
        assert url == "jdbc:opengauss://og.internal:5432/app"

    def test_gaussdb_uses_independent_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "gaussdb",
            {"host": "g.internal", "port": "25308", "database": "app"},
        )
        assert url == "jdbc:gaussdb://g.internal:25308/app"

    def test_kingbase_uses_kingbase8_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "kingbase",
            {"host": "kb.internal", "database": "app"},
        )
        assert url == "jdbc:kingbase8://kb.internal:54321/app"

    def test_tidb_uses_mysql_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "tidb",
            {"host": "tidb.internal", "port": "4000", "database": "app"},
        )
        assert url == "jdbc:mysql://tidb.internal:4000/app"

    def test_tidb_no_database_for_catalog(self):
        # TiDB 走 jdbc-mysql provider，注册 catalog 时 URL 不带 database
        url = DataSourceService._build_jdbc_url(
            "tidb",
            {"host": "tidb.internal", "port": "4000", "database": "app"},
            include_database=False,
        )
        assert url == "jdbc:mysql://tidb.internal:4000"

    def test_oceanbase_uses_oceanbase_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "oceanbase",
            {"host": "ob.internal", "port": "2883", "database": "app"},
        )
        assert url == "jdbc:oceanbase://ob.internal:2883/app"

    def test_dameng_uses_dm_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "dameng",
            {"host": "dm.internal", "port": "5236", "database": "app"},
        )
        assert url == "jdbc:dm://dm.internal:5236/app"

    def test_analyticdb_pg_uses_postgresql_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "analyticdb_pg",
            {"host": "adb.internal", "database": "app"},
        )
        assert url == "jdbc:postgresql://adb.internal:5432/app"

    def test_gaussdb_dws_uses_gaussdb_scheme(self):
        url = DataSourceService._build_jdbc_url(
            "gaussdb_dws",
            {"host": "dws.internal", "port": "8000", "database": "app"},
        )
        assert url == "jdbc:gaussdb://dws.internal:8000/app"

    def test_generic_jdbc_uses_user_url(self):
        url = DataSourceService._build_jdbc_url(
            "generic_jdbc",
            {"url": "jdbc:custom://host:1234/db"},
        )
        assert url == "jdbc:custom://host:1234/db"

    def test_generic_jdbc_missing_url_raises(self):
        from ontology.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            DataSourceService._build_jdbc_url("generic_jdbc", {})

    def test_default_ports_applied(self):
        assert DataSourceService._default_port("mysql") == "3306"
        assert DataSourceService._default_port("tidb") == "4000"
        assert DataSourceService._default_port("postgresql") == "5432"
        assert DataSourceService._default_port("kingbase") == "54321"
        assert DataSourceService._default_port("gaussdb_dws") == "8000"
        assert DataSourceService._default_port("dameng") == "5236"
        assert DataSourceService._default_port("unknown") == ""


# ─────────────────────────────────────────────────────────────
# Driver 解析（§6.1.2 独立类名驱动）
# ─────────────────────────────────────────────────────────────


class TestDriverResolution:
    def test_opengauss_uses_independent_driver_class(self):
        # 关键避坑：用 com.huawei.opengauss.jdbc.Driver，避免与 org.postgresql.Driver 同名冲突
        d = DataSourceService._resolve_driver("opengauss", {})
        assert d == "com.huawei.opengauss.jdbc.Driver"

    def test_gaussdb_uses_huawei_opengauss_driver(self):
        # opengaussjdbc 统一驱动（gsjdbc200 不在公网 Maven，用公开的 opengaussjdbc 替代）
        d = DataSourceService._resolve_driver("gaussdb", {})
        assert d == "com.huawei.opengauss.jdbc.Driver"

    def test_gaussdb_dws_uses_huawei_opengauss_driver(self):
        d = DataSourceService._resolve_driver("gaussdb_dws", {})
        assert d == "com.huawei.opengauss.jdbc.Driver"

    def test_kingbase_uses_kingbase8_driver(self):
        d = DataSourceService._resolve_driver("kingbase", {})
        assert d == "com.kingbase8.Driver"

    def test_dameng_uses_dm_driver(self):
        d = DataSourceService._resolve_driver("dameng", {})
        assert d == "dm.jdbc.driver.DmDriver"

    def test_oceanbase_uses_oceanbase_driver(self):
        d = DataSourceService._resolve_driver("oceanbase", {})
        assert d == "com.oceanbase.jdbc.Driver"

    def test_tidb_uses_mysql_driver(self):
        d = DataSourceService._resolve_driver("tidb", {})
        assert d == "com.mysql.cj.jdbc.Driver"

    def test_analyticdb_pg_uses_postgresql_driver(self):
        d = DataSourceService._resolve_driver("analyticdb_pg", {})
        assert d == "org.postgresql.Driver"

    def test_generic_jdbc_driver_from_config(self):
        d = DataSourceService._resolve_driver("generic_jdbc", {"driver": "com.example.MyDriver"})
        assert d == "com.example.MyDriver"

    def test_generic_jdbc_no_driver_returns_empty(self):
        d = DataSourceService._resolve_driver("generic_jdbc", {})
        assert d == ""


# ─────────────────────────────────────────────────────────────
# provider=None 品类跳过 Gravitino catalog 注册
# ─────────────────────────────────────────────────────────────


class TestProviderNoneRouting:
    @pytest.mark.asyncio
    async def test_dameng_skips_catalog_registration(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="dameng_ds", connector_type="dameng")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="dameng_ds",
            display_name="达梦库",
            connector_type="dameng",
            connector_config={"host": "dm.internal", "port": "5236", "database": "app"},
        )
        result = await service.create_datasource(ds_create)
        assert result.api_name == "dameng_ds"
        mock_catalog.register_jdbc_catalog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generic_jdbc_skips_catalog_registration(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="custom_ds", connector_type="generic_jdbc")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="custom_ds",
            display_name="Custom JDBC",
            connector_type="generic_jdbc",
            connector_config={
                "url": "jdbc:custom://host:1234/db",
                "driver": "com.example.Driver",
            },
        )
        result = await service.create_datasource(ds_create)
        assert result.api_name == "custom_ds"
        mock_catalog.register_jdbc_catalog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_opengauss_registers_with_pg_provider(self, service, mock_metadata, mock_catalog, monkeypatch):
        monkeypatch.setattr("ontology.services.datasource_service.settings.catalog_jdbc_host_override", "")
        ds = _make_ds(api_name="og_ds", connector_type="opengauss")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="og_ds",
            display_name="OpenGauss",
            connector_type="opengauss",
            connector_config={"host": "og.internal", "port": "5432", "database": "app"},
        )
        await service.create_datasource(ds_create)
        call = mock_catalog.register_jdbc_catalog.await_args
        assert call.kwargs["provider"] == "jdbc-postgresql"
        # 独立 URL scheme（opengauss://）而非 postgresql://
        # PG 系 URL 始终带 database
        assert call.kwargs["jdbc_url"] == "jdbc:opengauss://og.internal:5432/app"
        # 独立 driver 类名
        assert call.kwargs["jdbc_driver"] == "com.huawei.opengauss.jdbc.Driver"

    @pytest.mark.asyncio
    async def test_tidb_registers_with_mysql_provider(self, service, mock_metadata, mock_catalog, monkeypatch):
        monkeypatch.setattr("ontology.services.datasource_service.settings.catalog_jdbc_host_override", "")
        ds = _make_ds(api_name="tidb_ds", connector_type="tidb")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="tidb_ds",
            display_name="TiDB",
            connector_type="tidb",
            connector_config={"host": "tidb.internal", "port": "4000", "database": "app"},
        )
        await service.create_datasource(ds_create)
        call = mock_catalog.register_jdbc_catalog.await_args
        assert call.kwargs["provider"] == "jdbc-mysql"
        # URL 不带 database（mysql 协议，Gravitino jdbc-url 要求）
        assert call.kwargs["jdbc_url"] == "jdbc:mysql://tidb.internal:4000"
        assert call.kwargs["jdbc_driver"] == "com.mysql.cj.jdbc.Driver"


# ─────────────────────────────────────────────────────────────
# test_connection 对 provider=None 品类的处理
# ─────────────────────────────────────────────────────────────


class TestConnectionTestProviderNone:
    @pytest.mark.asyncio
    async def test_dameng_connection_test_not_supported(self, service, mock_metadata):
        from ontology.core.schemas.datasource import ConnectionTestResult

        mock_metadata.get_datasource.return_value = _make_ds(connector_type="dameng")
        result = await service.test_connection("dameng_ds")
        assert isinstance(result, ConnectionTestResult)
        assert result.success is False
        assert "dameng" in result.message

    @pytest.mark.asyncio
    async def test_generic_jdbc_connection_test_not_supported(self, service, mock_metadata):
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="generic_jdbc")
        result = await service.test_connection("custom_ds")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_opengauss_connection_test_via_trino(self, service, mock_metadata, mock_engine):
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="opengauss")
        mock_engine.test_connection.return_value = True
        result = await service.test_connection("og_ds")
        assert result.success is True
        mock_engine.test_connection.assert_awaited_once()


# ─────────────────────────────────────────────────────────────
# File/Kafka/Lakehouse catalog registration routing (§6.2/§6.3/§6.4)
# ─────────────────────────────────────────────────────────────


class TestExternalCatalogRouting:
    @pytest.mark.asyncio
    async def test_s3_registers_fileset_catalog(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="s3_ds", connector_type="s3")
        ds.connector_config = {
            "endpoint": "http://rustfs:9000",
            "bucket": "data",
            "access_key": "ak",
            "secret_key": "sk",
        }
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="s3_ds",
            display_name="S3 Data",
            connector_type="s3",
            connector_config={
                "endpoint": "http://rustfs:9000",
                "bucket": "data",
                "access_key": "ak",
                "secret_key": "sk",
            },
        )
        await service.create_datasource(ds_create)
        mock_catalog.register_fileset_catalog.assert_awaited_once()
        call = mock_catalog.register_fileset_catalog.await_args
        assert call.kwargs["provider"] == "fileset"
        assert call.kwargs["catalog_name"] == "s3_ds"
        props = call.kwargs["properties"]
        assert "s3://data" in props["location"]
        assert props["s3-access-key-id"] == "ak"

    @pytest.mark.asyncio
    async def test_kafka_registers_kafka_catalog(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="kafka_ds", connector_type="kafka")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="kafka_ds",
            display_name="Kafka",
            connector_type="kafka",
            connector_config={"bootstrap_servers": "kafka:9092"},
        )
        await service.create_datasource(ds_create)
        mock_catalog.register_kafka_catalog.assert_awaited_once_with(
            catalog_name="kafka_ds",
            bootstrap_servers="kafka:9092",
        )

    @pytest.mark.asyncio
    async def test_hive_registers_lakehouse_catalog(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="hive_ds", connector_type="hive")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="hive_ds",
            display_name="Hive",
            connector_type="hive",
            connector_config={"metastore-uri": "thrift://hms:9083"},
        )
        await service.create_datasource(ds_create)
        mock_catalog.register_lakehouse_catalog.assert_awaited_once()
        call = mock_catalog.register_lakehouse_catalog.await_args
        assert call.kwargs["provider"] == "hive"
        assert call.kwargs["properties"]["metastore-uri"] == "thrift://hms:9083"

    @pytest.mark.asyncio
    async def test_delta_registers_generic_lakehouse(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="delta_ds", connector_type="delta")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="delta_ds",
            display_name="Delta",
            connector_type="delta",
            connector_config={"catalog-backend": "hive", "warehouse": "s3://delta/wh"},
        )
        await service.create_datasource(ds_create)
        call = mock_catalog.register_lakehouse_catalog.await_args
        assert call.kwargs["provider"] == "lakehouse-delta"

    @pytest.mark.asyncio
    async def test_elasticsearch_skips_catalog(self, service, mock_metadata, mock_catalog):
        # ES 一律落地，不注册 Gravitino catalog（决策点 4）
        ds = _make_ds(api_name="es_ds", connector_type="elasticsearch")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="es_ds",
            display_name="ES",
            connector_type="elasticsearch",
            connector_config={"hosts": "es:9200"},
        )
        await service.create_datasource(ds_create)
        mock_catalog.register_fileset_catalog.assert_not_awaited()
        mock_catalog.register_kafka_catalog.assert_not_awaited()
        mock_catalog.register_lakehouse_catalog.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_catalog_registration_failure_marks_error(self, service, mock_metadata, mock_catalog):
        ds = _make_ds(api_name="kafka_ds", connector_type="kafka")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        mock_catalog.register_kafka_catalog.side_effect = RuntimeError("Gravitino down")
        ds_create = DataSourceCreate(
            api_name="kafka_ds",
            display_name="Kafka",
            connector_type="kafka",
            connector_config={"bootstrap_servers": "kafka:9092"},
        )
        from ontology.core.exceptions import OntologyError

        with pytest.raises(OntologyError, match="Failed to register"):
            await service.create_datasource(ds_create)
        mock_metadata.update_datasource.assert_awaited_with("kafka_ds", {"status": "ERROR"})


# ─────────────────────────────────────────────────────────────
# start_cdc_sync (§7.3 post-spike interface)
# ─────────────────────────────────────────────────────────────


class TestStartCdcSync:
    @pytest.mark.asyncio
    async def test_start_cdc_sync_submits_pipeline_and_marks_running(
        self, service, mock_metadata, mock_pipeline
    ):
        ds = _make_ds(api_name="mysql_ds", connector_type="mysql")
        mock_metadata.get_datasource.return_value = ds
        mock_metadata.create_sync_task.return_value = _make_sync_task("ordersCdc")
        result = await service.start_cdc_sync(
            datasource_api_name="mysql_ds",
            source_table="erp.orders",
            target_dataset_api_name="erp_orders",
            cdc_config={
                "cdc_connector": "MySQL-CDC",
                "hostname": "mysql.internal",
                "port": "3306",
                "username": "u",
                "password": "p",
            },
            primary_keys=["id"],
            task_api_name="ordersCdc",
        )
        # pipeline submitted
        mock_pipeline.create_external_cdc_pipeline.assert_awaited_once()
        call = mock_pipeline.create_external_cdc_pipeline.await_args
        source_config = call.kwargs["source_config"]
        assert source_config["table_name"] == "orders"
        assert source_config["database_name"] == "erp"
        assert source_config["primary_keys"] == ["id"]
        # task created + marked RUNNING
        mock_metadata.create_sync_task.assert_awaited_once()
        update_call = mock_metadata.update_sync_task.await_args
        assert update_call.args[0] == "ordersCdc"
        assert update_call.args[1]["status"] == "RUNNING"
        assert update_call.args[1]["pipeline_name"] == "ext_cdc_erp_orders"
        assert result is not None


class TestStartTimeseriesSync:
    @pytest.mark.asyncio
    async def test_start_timeseries_sync_submits_pipeline(
        self, service, mock_metadata, mock_pipeline
    ):
        ds = _make_ds(api_name="kafka_ds", connector_type="kafka")
        mock_metadata.get_datasource.return_value = ds
        mock_metadata.create_sync_task.return_value = _make_sync_task("vehicleTrackTs")
        result = await service.start_timeseries_sync(
            datasource_api_name="kafka_ds",
            kafka_topic="vehicle_track",
            target_hypertable="timeseries_logistics__vehicle__track",
            schema_fields={"series_id": "string", "timestamp": "timestamp", "speed": "double"},
            primary_keys=["series_id", "timestamp"],
            task_api_name="vehicleTrackTs",
        )
        # Kafka→TimescaleDB pipeline submitted
        mock_pipeline.create_kafka_timeseries_pipeline.assert_awaited_once()
        call = mock_pipeline.create_kafka_timeseries_pipeline.await_args
        assert call.kwargs["target_hypertable"] == "timeseries_logistics__vehicle__track"
        source_config = call.kwargs["source_config"]
        assert source_config["topic"] == "vehicle_track"
        assert source_config["schema_fields"]["speed"] == "double"
        assert source_config["primary_keys"] == ["series_id", "timestamp"]
        # task created + RUNNING
        mock_metadata.create_sync_task.assert_awaited_once()
        update_call = mock_metadata.update_sync_task.await_args
        assert update_call.args[1]["status"] == "RUNNING"
        assert update_call.args[1]["pipeline_name"] == "kafka_ts_timeseries_logistics__vehicle__track"
        assert result is not None
