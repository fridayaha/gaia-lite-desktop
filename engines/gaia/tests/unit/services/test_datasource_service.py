"""Unit tests for DataSourceService.

Tests the data source orchestration layer: credential management,
data source lifecycle, schema exploration, sync tasks, dataset
governance, and impact analysis. All layer dependencies are mocked.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import OntologyError
from ontology.core.schemas.datasource import (
    ColumnInfo,
    Credential,
    CredentialCreate,
    DatasetGovernanceCreate,
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    ImpactAnalysisRequest,
    SyncTaskCreate,
    TableInfo,
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
    # describe_table 现在会 best-effort 调 get_table_comment 取表注释；
    # 默认返回空串，避免 AsyncMock 对象污染 TableInfo.comment 校验。
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
def service(mock_metadata, mock_catalog, mock_engine, mock_pipeline, mock_dataset) -> DataSourceService:
    return DataSourceService(
        metadata=mock_metadata,
        catalog=mock_catalog,
        engine=mock_engine,
        pipeline=mock_pipeline,
        dataset=mock_dataset,
    )


def _make_ds(api_name: str = "erp_mysql", connector_type: str = "mysql") -> DataSource:
    return DataSource(
        id="ds1",
        api_name=api_name,
        display_name="ERP MySQL",
        description="",
        connector_type=connector_type,
        connector_config={"host": "localhost", "port": "3306", "database": "erp"},
        credential_id=None,
        status="CONNECTED",
        gravitino_catalog_name=api_name,
        capabilities=["explore", "sync", "sample"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


class TestCredentialManagement:
    @pytest.mark.asyncio
    async def test_create_credential(self, service, mock_metadata):
        mock_metadata.create_credential.return_value = MagicMock(
            id="c1",
            api_name="erp_cred",
            credential_type="BASIC_AUTH",
            secret_data="***",
            created_at=MagicMock(),
        )
        cred = CredentialCreate(
            api_name="erp_cred", credential_type="BASIC_AUTH", secret_data={"password": "secret123"}
        )
        result = await service.create_credential(cred)
        assert result.id == "c1"
        assert result.secret_data == "***"  # masked

    @pytest.mark.asyncio
    async def test_list_credentials(self, service, mock_metadata):
        mock_metadata.list_credentials.return_value = [
            MagicMock(id="c1", api_name="erp", credential_type="BASIC_AUTH", secret_data="***", created_at=MagicMock()),
        ]
        result = await service.list_credentials()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_credential(self, service, mock_metadata):
        mock_metadata.get_credential.return_value = MagicMock(api_name="erp")
        result = await service.get_credential("erp")
        assert result is not None

    @pytest.mark.asyncio
    async def test_delete_credential(self, service, mock_metadata):
        await service.delete_credential("erp")
        mock_metadata.delete_credential.assert_awaited_once_with("erp")


class TestDataSourceLifecycle:
    @pytest.mark.asyncio
    async def test_create_datasource_jdbc(self, service, mock_metadata, mock_catalog, monkeypatch):
        """JDBC data source registers in Gravitino via REST API."""
        # Isolate from the host .env: this test asserts the no-override base path.
        monkeypatch.setattr("ontology.services.datasource_service.settings.catalog_jdbc_host_override", "")
        ds = _make_ds()
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="erp_mysql",
            display_name="ERP MySQL",
            connector_type="mysql",
            connector_config={"host": "localhost", "port": "3306", "database": "erp"},
        )
        result = await service.create_datasource(ds_create)
        assert result.api_name == "erp_mysql"
        # 注册成功后 status 应刷新为 CONNECTED（修复创建后误显 ERROR/DISCONNECTED）
        mock_metadata.update_datasource.assert_awaited_with("erp_mysql", {"status": "CONNECTED"})
        mock_catalog.register_jdbc_catalog.assert_awaited_once()
        # 验证 provider 与 properties
        call = mock_catalog.register_jdbc_catalog.await_args
        assert call.kwargs["catalog_name"] == "erp_mysql"
        assert call.kwargs["provider"] == "jdbc-mysql"
        assert call.kwargs["jdbc_url"] == "jdbc:mysql://localhost:3306"
        assert call.kwargs["jdbc_database"] == "erp"
        # No credential linked → user/password empty (not pulled from connector_config).
        assert call.kwargs["jdbc_user"] == ""
        assert call.kwargs["jdbc_password"] == ""

    @pytest.mark.asyncio
    async def test_create_datasource_resolves_credential_and_overrides_host(
        self, service, mock_metadata, mock_catalog, monkeypatch
    ):
        """Catalog user/password come from the linked Credential (not
        connector_config), and jdbc-url host is rewritten by the override so the
        Gravitino/Trino container can reach the source DB."""
        monkeypatch.setattr(
            "ontology.services.datasource_service.settings.catalog_jdbc_host_override",
            "benchmark-mysql",
        )
        ds = _make_ds()
        ds.credential_id = "cred1"
        ds.connector_config = {"host": "localhost", "port": "3306", "database": "airline_benchmark"}
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        mock_metadata.get_credential_by_id.return_value = Credential(
            id="cred1",
            api_name="airline_mysql_cred",
            credential_type="basic",
            secret_data={"username": "root", "password": "root"},
            created_at=datetime.now(UTC),
        )
        ds_create = DataSourceCreate(
            api_name="airline_mysql",
            display_name="Airline MySQL",
            connector_type="mysql",
            connector_config={"host": "localhost", "port": 3306, "database": "airline_benchmark"},
            credential_id="cred1",
        )
        await service.create_datasource(ds_create)
        call = mock_catalog.register_jdbc_catalog.await_args
        # host rewritten for the Gravitino container view
        assert call.kwargs["jdbc_url"] == "jdbc:mysql://benchmark-mysql:3306"
        assert call.kwargs["jdbc_database"] == "airline_benchmark"
        # secrets resolved from the linked Credential, NOT connector_config
        assert call.kwargs["jdbc_user"] == "root"
        assert call.kwargs["jdbc_password"] == "root"
        mock_metadata.get_credential_by_id.assert_awaited_once_with("cred1")

    @pytest.mark.asyncio
    async def test_create_datasource_non_jdbc(self, service, mock_metadata, mock_catalog):
        """Non-JDBC source skips catalog registration."""
        ds = _make_ds(api_name="s3_data", connector_type="s3")
        mock_metadata.create_datasource.return_value = ds
        mock_metadata.update_datasource.return_value = ds
        ds_create = DataSourceCreate(
            api_name="s3_data",
            display_name="S3 Data",
            connector_type="s3",
            connector_config={"bucket": "data"},
        )
        result = await service.create_datasource(ds_create)
        assert result.api_name == "s3_data"
        mock_catalog.register_jdbc_catalog.assert_not_called()
        # 非 JDBC 跳过 catalog 注册，但仍标记 CONNECTED（PG 记录已创建）
        mock_metadata.update_datasource.assert_awaited_with("s3_data", {"status": "CONNECTED"})

    @pytest.mark.asyncio
    async def test_create_datasource_registration_failure(self, service, mock_metadata, mock_catalog):
        """Catalog registration failure updates status to ERROR."""
        ds = _make_ds()
        mock_metadata.create_datasource.return_value = ds
        mock_catalog.register_jdbc_catalog.side_effect = OntologyError("Gravitino down")
        ds_create = DataSourceCreate(
            api_name="erp_mysql",
            display_name="ERP MySQL",
            connector_type="mysql",
            connector_config={"host": "localhost", "port": "3306", "database": "erp"},
        )
        with pytest.raises(OntologyError, match="Failed to register"):
            await service.create_datasource(ds_create)
        mock_metadata.update_datasource.assert_awaited_with("erp_mysql", {"status": "ERROR"})

    @pytest.mark.asyncio
    async def test_get_datasource(self, service, mock_metadata):
        mock_metadata.get_datasource.return_value = _make_ds()
        result = await service.get_datasource("erp_mysql")
        assert result.api_name == "erp_mysql"
        assert "explore" in result.capabilities

    @pytest.mark.asyncio
    async def test_list_datasources(self, service, mock_metadata):
        mock_metadata.list_datasources.return_value = [_make_ds(), _make_ds("pg_hr", "postgresql")]
        result = await service.list_datasources()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update_datasource(self, service, mock_metadata):
        mock_metadata.update_datasource.return_value = _make_ds()
        updates = DataSourceUpdate(display_name="Updated ERP")
        result = await service.update_datasource("erp_mysql", updates)
        assert result.api_name == "erp_mysql"

    @pytest.mark.asyncio
    async def test_delete_datasource(self, service, mock_metadata, mock_catalog):
        mock_metadata.list_datasets.return_value = []
        await service.delete_datasource("erp_mysql")
        mock_catalog.remove_catalog.assert_awaited_once_with("erp_mysql")
        mock_metadata.delete_datasource.assert_awaited_once_with("erp_mysql", auto_commit=False)
        mock_metadata.commit_transaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_datasource_catalog_already_removed(self, service, mock_metadata, mock_catalog):
        """Even if Gravitino 404s, PG delete still proceeds."""
        mock_metadata.list_datasets.return_value = []
        mock_catalog.remove_catalog.side_effect = Exception("Not found")
        await service.delete_datasource("erp_mysql")
        mock_metadata.delete_datasource.assert_awaited_once_with("erp_mysql", auto_commit=False)
        mock_metadata.commit_transaction.assert_awaited_once()


class TestConnectionTest:
    @pytest.mark.asyncio
    async def test_test_connection_success(self, service, mock_metadata, mock_engine):
        mock_metadata.get_datasource.return_value = _make_ds()
        mock_engine.test_connection.return_value = True
        result = await service.test_connection("erp_mysql")
        assert result.success is True
        mock_metadata.update_datasource.assert_awaited_with("erp_mysql", {"status": "CONNECTED"})

    @pytest.mark.asyncio
    async def test_test_connection_failure(self, service, mock_metadata, mock_engine):
        mock_metadata.get_datasource.return_value = _make_ds()
        mock_engine.test_connection.return_value = False
        result = await service.test_connection("erp_mysql")
        assert result.success is False
        mock_metadata.update_datasource.assert_awaited_with("erp_mysql", {"status": "ERROR"})

    @pytest.mark.asyncio
    async def test_test_connection_not_supported(self, service, mock_metadata):
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="s3")
        result = await service.test_connection("s3_data")
        assert result.success is False
        assert "not supported" in result.message


class TestHealthCheckLoop:
    @pytest.mark.asyncio
    async def test_recovers_error_to_connected(self, service, mock_metadata, mock_engine):
        """Loop 探活 ERROR 数据源，成功后刷新为 CONNECTED（自愈）。"""
        ds = _make_ds()
        ds.status = "ERROR"  # 模拟创建时瞬时失败残留的 ERROR
        mock_metadata.list_datasources.return_value = [ds]
        mock_metadata.get_datasource.return_value = ds  # test_connection 内部会重新查
        mock_engine.test_connection.return_value = True

        # 只跑一轮：mock sleep 抛 CancelledError 中断循环
        import asyncio as _asyncio

        async def _fake_sleep(_seconds: float) -> None:
            raise _asyncio.CancelledError

        from unittest.mock import patch as _patch

        with _patch("ontology.services.datasource_service.asyncio.sleep", _fake_sleep):
            with pytest.raises(_asyncio.CancelledError):
                await service.run_health_check_loop(interval=1)

        # ERROR 数据源被探活，且 status 刷新为 CONNECTED
        mock_engine.test_connection.assert_awaited_once_with("erp_mysql")
        mock_metadata.update_datasource.assert_awaited_with("erp_mysql", {"status": "CONNECTED"})

    @pytest.mark.asyncio
    async def test_skips_connected_sources(self, service, mock_metadata, mock_engine):
        """CONNECTED 数据源不重复探活（省资源）。"""
        ds = _make_ds()
        ds.status = "CONNECTED"
        mock_metadata.list_datasources.return_value = [ds]

        import asyncio as _asyncio

        async def _fake_sleep(_seconds: float) -> None:
            raise _asyncio.CancelledError

        from unittest.mock import patch as _patch

        with _patch("ontology.services.datasource_service.asyncio.sleep", _fake_sleep):
            with pytest.raises(_asyncio.CancelledError):
                await service.run_health_check_loop(interval=1)

        mock_engine.test_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_non_jdbc_sources(self, service, mock_metadata, mock_engine):
        """非 JDBC（无 Trino catalog）数据源跳过探活。"""
        ds = _make_ds(api_name="s3_data", connector_type="s3")
        ds.status = "ERROR"
        mock_metadata.list_datasources.return_value = [ds]

        import asyncio as _asyncio

        async def _fake_sleep(_seconds: float) -> None:
            raise _asyncio.CancelledError

        from unittest.mock import patch as _patch

        with _patch("ontology.services.datasource_service.asyncio.sleep", _fake_sleep):
            with pytest.raises(_asyncio.CancelledError):
                await service.run_health_check_loop(interval=1)

        mock_engine.test_connection.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_probe_exception_does_not_crash_loop(self, service, mock_metadata, mock_engine):
        """单次探活并常不 crash 循环（只 log，下一 tick 重试）。"""
        ds = _make_ds()
        ds.status = "ERROR"
        mock_metadata.list_datasources.return_value = [ds]
        mock_metadata.get_datasource.return_value = ds
        mock_engine.test_connection.side_effect = RuntimeError("Trino down")

        import asyncio as _asyncio

        sleep_count = 0

        async def _fake_sleep(_seconds: float) -> None:
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                raise _asyncio.CancelledError

        from unittest.mock import patch as _patch

        with _patch("ontology.services.datasource_service.asyncio.sleep", _fake_sleep):
            with pytest.raises(_asyncio.CancelledError):
                await service.run_health_check_loop(interval=1)

        # 跑了两轮，都没有 crash（CancelledError 只是我们主动中断）
        assert mock_engine.test_connection.await_count == 2


class TestExplore:
    @pytest.mark.asyncio
    async def test_explore_schema(self, service, mock_metadata, mock_engine):
        """explore returns table names only (no columns) — lazy loading."""
        mock_metadata.get_datasource.return_value = _make_ds()
        mock_engine.list_tables.return_value = ["dbo.employees", "dbo.departments"]
        result = await service.explore("erp_mysql", "dbo")
        assert len(result.tables) == 2
        # Columns are empty — loaded via describe_table()
        assert result.tables[0].columns == []
        assert result.tables[0].schema_ == "dbo"
        assert result.tables[0].name == "employees"

    @pytest.mark.asyncio
    async def test_explore_empty(self, service, mock_metadata, mock_engine):
        mock_metadata.get_datasource.return_value = _make_ds()
        mock_engine.list_tables.return_value = None
        result = await service.explore("erp_mysql")
        assert result.tables == []

    @pytest.mark.asyncio
    async def test_explore_default_schema_pg(self, service, mock_metadata, mock_engine):
        """PostgreSQL 默认 schema 为 public（未配置 schema 时）。"""
        ds = _make_ds("erpPg", "postgresql")
        ds.connector_config = {"host": "h", "database": "erp", "schema": ""}
        mock_metadata.get_datasource.return_value = ds
        mock_engine.list_tables.return_value = ["employees"]
        result = await service.explore("erpPg")
        assert result.database == "public"
        assert len(result.tables) == 1
        assert result.tables[0].columns == []

    @pytest.mark.asyncio
    async def test_explore_pg_custom_schema(self, service, mock_metadata, mock_engine):
        """PostgreSQL 可通过 connector_config.schema 指定探索起始命名空间。"""
        ds = _make_ds("erpPg", "postgresql")
        ds.connector_config = {"host": "h", "database": "erp", "schema": "biz"}
        mock_metadata.get_datasource.return_value = ds
        mock_engine.list_tables.return_value = ["orders"]
        result = await service.explore("erpPg")
        assert result.database == "biz"

    @pytest.mark.asyncio
    async def test_explore_default_schema_mysql(self, service, mock_metadata, mock_engine):
        """MySQL 默认 schema 为 connector_config.database（mysql schema=database）。"""
        mock_metadata.get_datasource.return_value = _make_ds("erp_mysql", "mysql")
        mock_engine.list_tables.return_value = ["employees"]
        result = await service.explore("erp_mysql")
        # _make_ds 的 connector_config.database = "erp"
        assert result.database == "erp"
        assert len(result.tables) == 1

    @pytest.mark.asyncio
    async def test_explore_many_tables(self, service, mock_metadata, mock_engine):
        """explore should be instant regardless of table count (no DESCRIBE calls)."""
        mock_metadata.get_datasource.return_value = _make_ds()
        table_count = 55
        table_names = [f"schema_{i}.table_{i}" for i in range(table_count)]
        mock_engine.list_tables.return_value = table_names

        result = await service.explore("erp_mysql")

        assert len(result.tables) == table_count
        # describe_table should NOT be called during explore
        mock_engine.describe_table.assert_not_called()
        # All columns should be empty
        for tbl in result.tables:
            assert tbl.columns == []

    @pytest.mark.asyncio
    async def test_explore_csv_uses_current_database_as_catalog(
        self, service, mock_metadata, mock_engine, monkeypatch
    ):
        """lite CSV explore 用 current_database() 作 catalog（非硬编码 'main'）。

        回归：CSV 经 CREATE TABLE AS SELECT 导入 DuckDB 主库，duckdb_tables()
        的 database_name 是文件 stem（如 'warehouse'），硬编码 'main' 会致
        list_tables('main', ...) 查空。explore 应调 engine.current_database()
        拿正确 catalog 再 list_tables。
        """
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")

        ds = _make_ds("sample_csv", "csv")
        ds.connector_config = {"path": "/tmp/sample.csv"}
        mock_metadata.get_datasource.return_value = ds
        # CSV 主库 database_name 由 current_database() 查得
        mock_engine.current_database = AsyncMock(return_value="warehouse")
        mock_engine.list_tables.return_value = ["sample_csv"]

        result = await service.explore("sample_csv")

        # current_database 必须被调（CSV 路径）
        mock_engine.current_database.assert_awaited_once()
        # list_tables 用的 catalog 是 current_database() 返回值，不是 'main'
        mock_engine.list_tables.assert_awaited_once()
        catalog_arg = mock_engine.list_tables.call_args.args[0]
        assert catalog_arg == "warehouse"
        assert len(result.tables) == 1
        assert result.tables[0].name == "sample_csv"

    @pytest.mark.asyncio
    async def test_describe_table_mysql_prefers_gravitino_rest(self, service, mock_metadata, mock_catalog):
        """JDBC sources (MySQL) prefer Gravitino REST — preserves column casing + PK."""
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="mysql")
        # Gravitino REST 一次返回 columns + indexes + comment（保留原始大小写 + PK）
        mock_catalog.get_table_metadata.return_value = {
            "columns": [
                {"name": "id", "type": "integer", "nullable": False, "comment": "ID"},
                {"name": "customerName", "type": "varchar(255)", "nullable": True, "comment": ""},
            ],
            "indexes": [
                {"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["id"]]},
            ],
            "comment": "员工表",
        }

        result = await service.describe_table("erp_mysql", "dbo", "employees")
        assert result.name == "employees"
        assert result.schema_ == "dbo"
        assert result.comment == "员工表"
        assert len(result.columns) == 2
        # 列名保留原始大小写（REST 不折叠）
        assert result.columns[0].name == "id"
        assert result.columns[0].is_primary_key is True
        assert result.columns[0].data_type == "integer"
        assert result.columns[1].name == "customerName"  # camelCase 保留
        assert result.columns[1].is_primary_key is False
        assert result.columns[1].data_type == "varchar(255)"

        # JDBC 源优先 REST，不应调 Trino DESCRIBE
        mock_catalog.get_table_metadata.assert_awaited_once_with("erp_mysql", "dbo", "employees")

    @pytest.mark.asyncio
    async def test_describe_table_pg_preserves_camelcase_and_pk(self, service, mock_metadata, mock_catalog):
        """PG 源走 REST 保留 modelId/SpecialFeatures 原始大小写 + PK 标记 modelId。

        回归测试：Trino DESCRIBE 会把列名折叠成全小写（modelId→modelid），
        Gravitino REST 保留原样。见 CLAUDE.md 通用错误模式 #13 + 列名大小写调研。
        """
        mock_metadata.get_datasource.return_value = _make_ds(api_name="xiaoling", connector_type="postgresql")
        mock_catalog.get_table_metadata.return_value = {
            "columns": [
                {"name": "modelId", "type": "string", "nullable": False, "comment": "HF repo ID"},
                {"name": "SpecialFeatures", "type": "string", "nullable": True, "comment": "MLA/MTP"},
                {"name": "downloads", "type": "long", "nullable": True, "comment": ""},
            ],
            "indexes": [
                {"indexType": "PRIMARY_KEY", "name": "model_instance_pkey", "fieldNames": [["modelId"]]},
            ],
            "comment": "模型实例",
        }

        result = await service.describe_table("xiaoling", "public", "model_instance")
        assert result.columns[0].name == "modelId"  # 不是 modelid
        assert result.columns[0].is_primary_key is True
        assert result.columns[1].name == "SpecialFeatures"  # 不是 specialfeatures
        assert result.columns[1].is_primary_key is False
        assert result.columns[2].name == "downloads"

    @pytest.mark.asyncio
    async def test_describe_table_composite_pk(self, service, mock_metadata, mock_catalog):
        """复合主键：fieldNames=[['a'],['b']] → a/b 都标记 PK。"""
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="postgresql")
        mock_catalog.get_table_metadata.return_value = {
            "columns": [
                {"name": "a", "type": "integer", "nullable": False, "comment": ""},
                {"name": "b", "type": "integer", "nullable": False, "comment": ""},
                {"name": "c", "type": "varchar", "nullable": True, "comment": ""},
            ],
            "indexes": [
                {"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["a"], ["b"]]},
            ],
            "comment": "",
        }

        result = await service.describe_table("pg_ds", "public", "t")
        assert result.columns[0].is_primary_key is True
        assert result.columns[1].is_primary_key is True
        assert result.columns[2].is_primary_key is False

    @pytest.mark.asyncio
    async def test_describe_table_rest_failure_falls_back_to_trino(
        self, service, mock_metadata, mock_catalog, mock_engine
    ):
        """Gravitino REST 失败（非 NotFound）→ 降级 Trino DESCRIBE（列名折叠但能返回）。"""
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="mysql")
        mock_catalog.get_table_metadata.side_effect = RuntimeError("gravitino down")
        mock_engine.describe_table.return_value = [
            {"Column": "id", "Type": "integer", "Null": "NO", "Key": "PRI", "Comment": ""},
            {"Column": "name", "Type": "varchar", "Null": "YES", "Key": "", "Comment": ""},
        ]

        result = await service.describe_table("erp_mysql", "dbo", "employees")
        assert len(result.columns) == 2
        assert result.columns[0].name == "id"
        assert result.columns[0].is_primary_key is True
        # 降级路径调了 Trino DESCRIBE
        mock_engine.describe_table.assert_awaited_once_with("erp_mysql", "dbo", "employees")

    @pytest.mark.asyncio
    async def test_describe_table_rest_not_found_returns_empty(self, service, mock_metadata, mock_catalog, mock_engine):
        """Gravitino REST 返回 NotFoundError → 不降级 Trino，返回空 columns（表不存在）。"""
        from ontology.core.exceptions import NotFoundError

        mock_metadata.get_datasource.return_value = _make_ds(connector_type="postgresql")
        mock_catalog.get_table_metadata.side_effect = NotFoundError("Table", "xiaoling.public.missing")

        result = await service.describe_table("xiaoling", "public", "missing")
        assert result.columns == []
        # 表不存在时不降级到 Trino（Trino 也查不到，且会掩盖 NotFoundError）
        mock_engine.describe_table.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_describe_table_data_source_unreachable_no_fallback(self, service, mock_metadata, mock_catalog):
        """数据源连不上（DataSourceUnreachableError）→ 不降级，直接报可读错误。"""
        from ontology.core.exceptions import DataSourceUnreachableError

        mock_metadata.get_datasource.return_value = _make_ds(connector_type="mysql")
        mock_catalog.get_table_metadata.side_effect = DataSourceUnreachableError("refused")

        with pytest.raises(DataSourceUnreachableError):
            await service.describe_table("erp_mysql", "dbo", "employees")

    @pytest.mark.asyncio
    async def test_describe_table_external_type_via_rest(self, service, mock_metadata, mock_catalog):
        """列含 external(jsonb) 类型时 REST 路径返回结构化 type（不崩溃）。"""
        mock_metadata.get_datasource.return_value = _make_ds(connector_type="postgresql")
        mock_catalog.get_table_metadata.return_value = {
            "columns": [
                {"name": "id", "type": "long", "nullable": False, "comment": ""},
                # Gravitino 把 PG jsonb 映射为 external dict
                {
                    "name": "meta",
                    "type": {"type": "external", "catalogString": "jsonb"},
                    "nullable": True,
                    "comment": "",
                },
            ],
            "indexes": [],
            "comment": "",
        }

        result = await service.describe_table("pg_ds", "public", "t")
        assert len(result.columns) == 2
        assert result.columns[1].name == "meta"
        assert result.columns[1].data_type == "jsonb"  # _format_gravitino_column_type 提取 catalogString

    @pytest.mark.asyncio
    async def test_sample_data(self, service, mock_metadata, mock_engine):
        mock_metadata.get_datasource.return_value = _make_ds()
        mock_engine.sample_data.return_value = [{"id": 1, "name": "Alice"}]
        result = await service.sample_data("erp_mysql", "dbo", "employees", limit=5)
        assert len(result) == 1


class TestSyncTask:
    @pytest.mark.asyncio
    async def test_create_sync_task(self, service, mock_metadata, mock_pipeline):
        ds = _make_ds()
        mock_metadata.create_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            id="st1",
            data_source_id=ds.id,
            status="DRAFT",
        )
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            id="st1",
            data_source_id=ds.id,
            status="DRAFT",
        )
        mock_metadata.get_datasource_by_id.return_value = ds
        mock_pipeline.create_sync_pipeline.return_value = MagicMock(name="pipeline-sync_orders")

        task = SyncTaskCreate(
            api_name="sync_orders",
            data_source_id=ds.id,
            sync_type="FULL_SYNC",
            source_config={"table": "orders"},
            target_dataset_api_name="orders_dataset",
        )
        result = await service.create_sync_task(task)
        assert result.api_name == "sync_orders"
        mock_metadata.update_sync_task.assert_awaited()

    @pytest.mark.asyncio
    async def test_create_sync_task_provisions_managed_table(self, service, mock_metadata, mock_pipeline, mock_dataset):
        """Catalog First: create_sync_task registers the managed Iceberg table
        with full physical metadata (PK, comments, NULL) via IcebergStore before
        submitting the SeaTunnel pipeline. SeaTunnel only writes data."""
        ds = _make_ds()
        mock_metadata.create_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            id="st1",
            data_source_id=ds.id,
            status="DRAFT",
            source_config={"table": "orders"},
            target_dataset_api_name="orders_dataset",
            transaction_type="snapshot",
        )
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders", id="st1", data_source_id=ds.id, status="DRAFT"
        )
        mock_metadata.get_datasource_by_id.return_value = ds
        mock_pipeline.create_sync_pipeline.return_value = MagicMock(name="pipeline-sync_orders")
        # describe_table returns source schema with PK + comment + nullable.
        service.describe_table = AsyncMock(
            return_value=TableInfo(
                name="orders",
                schema="dbo",
                columns=[
                    ColumnInfo(
                        name="order_id",
                        data_type="bigint",
                        nullable=False,
                        is_primary_key=True,
                        comment="订单ID",
                    ),
                    ColumnInfo(
                        name="customer_id",
                        data_type="bigint",
                        nullable=True,
                        is_primary_key=False,
                        comment="客户ID",
                    ),
                ],
                comment="订单表",
            )
        )

        task = SyncTaskCreate(
            api_name="sync_orders",
            data_source_id=ds.id,
            sync_type="FULL_SYNC",
            source_config={"table": "orders"},
            target_dataset_api_name="orders_dataset",
        )
        await service.create_sync_task(task)

        # IcebergStore.create_managed_table called with snake_case table name,
        # full schema (PK marker + comments + nullable), and provenance props.
        mock_dataset.create_managed_table.assert_awaited_once()
        call = mock_dataset.create_managed_table.await_args
        assert call.args[0] == "orders_dataset"  # _to_snake(bare api_name)
        schema = call.args[1]
        assert schema.table_comment == "订单表"
        assert len(schema.columns) == 2
        assert schema.columns[0].name == "order_id"
        assert schema.columns[0].is_primary_key is True
        assert schema.columns[0].nullable is False
        assert schema.columns[0].comment == "订单ID"
        assert schema.columns[1].nullable is True
        props = call.kwargs.get("properties", {})
        assert props.get("gaia.source-datasource") == ds.api_name
        assert props.get("gaia.source-table") == "orders"

    @pytest.mark.asyncio
    async def test_create_sync_task_source_schema_key_used_as_database(
        self, service, mock_metadata, mock_pipeline, mock_dataset
    ):
        """source_config may store the schema under "schema" (PG) or
        "database" (MySQL). _provision_managed_table_for_sync must pass the
        right value to describe_table — regression: previously only read
        "database", so PG sources (which use "schema") passed "" and
        describe_table returned empty columns → Iceberg table created with 0 fields.
        """
        ds = _make_ds(connector_type="postgresql")
        mock_metadata.create_sync_task.return_value = MagicMock(
            api_name="sync_cann_op",
            id="st2",
            data_source_id=ds.id,
            status="DRAFT",
            source_config={"table": "cann_op", "schema": "public"},
            target_dataset_api_name="cann_op_smoke",
            transaction_type="snapshot",
        )
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_cann_op", id="st2", data_source_id=ds.id, status="DRAFT"
        )
        mock_metadata.get_datasource_by_id.return_value = ds
        mock_pipeline.create_sync_pipeline.return_value = MagicMock(name="p")
        service.describe_table = AsyncMock(
            return_value=TableInfo(
                name="cann_op",
                schema="public",
                columns=[
                    ColumnInfo(name="opId", data_type="string", nullable=False, is_primary_key=True, comment="主键"),
                ],
                comment="CANN算子",
            )
        )

        task = SyncTaskCreate(
            api_name="sync_cann_op",
            data_source_id=ds.id,
            sync_type="FULL_SYNC",
            source_config={"table": "cann_op", "schema": "public"},
            target_dataset_api_name="cann_op_smoke",
        )
        await service.create_sync_task(task)

        # describe_table must receive database="public" (from the "schema" key),
        # not the empty string default.
        desc_call = service.describe_table.await_args
        assert desc_call.args[1] == "public"
        assert desc_call.args[2] == "cann_op"

    @pytest.mark.asyncio
    async def test_get_sync_task(self, service, mock_metadata):
        mock_metadata.get_sync_task.return_value = MagicMock(api_name="sync_orders")
        result = await service.get_sync_task("sync_orders")
        assert result.api_name == "sync_orders"

    @pytest.mark.asyncio
    async def test_list_sync_tasks(self, service, mock_metadata):
        mock_metadata.get_datasource.return_value = _make_ds()
        mock_metadata.list_sync_tasks_for_datasource.return_value = [
            MagicMock(api_name="sync_orders"),
        ]
        result = await service.list_sync_tasks("erp_mysql")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_start_sync_re_submits_when_not_running(self, service, mock_metadata, mock_pipeline):
        """start_sync re-submits the pipeline when SeaTunnel reports it not running.

        This is the core fix for the phantom-RUNNING bug: previously
        start_sync was a no-op that just wrote status=RUNNING to the DB
        without ever talking to SeaTunnel.
        """
        ds = _make_ds()
        task = MagicMock(
            api_name="sync_orders",
            id="st1",
            data_source_id=ds.id,
            pipeline_name="pipeline-sync_orders",
            source_config={"table": "orders"},
            target_dataset_api_name="orders_dataset",
        )
        mock_metadata.get_sync_task.return_value = task
        mock_metadata.get_datasource_by_id.return_value = ds
        # SeaTunnel says the job is NOT running (e.g. finished/unknown).
        mock_pipeline.get_job_status.return_value = MagicMock(state="UNKNOWN")
        mock_pipeline.create_sync_pipeline.return_value = MagicMock(name="pipeline-sync_orders")
        mock_metadata.update_sync_task.return_value = MagicMock(status="RUNNING")

        result = await service.start_sync("sync_orders")

        assert result.status == "RUNNING"
        mock_pipeline.create_sync_pipeline.assert_awaited_once()
        mock_metadata.update_sync_task.assert_awaited()

    @pytest.mark.asyncio
    async def test_start_sync_short_circuits_when_running(self, service, mock_metadata, mock_pipeline):
        """If the job is already RUNNING in SeaTunnel, start_sync does not re-submit."""
        task = MagicMock(
            api_name="sync_orders",
            pipeline_name="pipeline-sync_orders",
            data_source_id="ds1",
            source_config={"table": "orders"},
        )
        mock_metadata.get_sync_task.return_value = task
        mock_pipeline.get_job_status.return_value = MagicMock(state="RUNNING")
        mock_metadata.update_sync_task.return_value = MagicMock(status="RUNNING")

        result = await service.start_sync("sync_orders")

        assert result.status == "RUNNING"
        mock_pipeline.create_sync_pipeline.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_start_sync_marks_failed_on_submit_error(self, service, mock_metadata, mock_pipeline):
        """When SeaTunnel rejects the re-submit, the task is marked FAILED and re-raised.

        This ensures the UI never shows a phantom RUNNING for a task whose
        job SeaTunnel refused to run.
        """
        ds = _make_ds()
        task = MagicMock(
            api_name="sync_orders",
            data_source_id=ds.id,
            pipeline_name=None,
            source_config={"table": "orders"},
            target_dataset_api_name="orders_dataset",
        )
        mock_metadata.get_sync_task.return_value = task
        mock_metadata.get_datasource_by_id.return_value = ds
        mock_pipeline.create_sync_pipeline.side_effect = OntologyError("SeaTunnel rejected job sync_orders: bad config")
        mock_metadata.update_sync_task.return_value = MagicMock(status="FAILED")

        with pytest.raises(OntologyError, match="SeaTunnel rejected job"):
            await service.start_sync("sync_orders")

        # FAILED must be persisted so the UI reflects the real outcome.
        mock_metadata.update_sync_task.assert_awaited()
        args = mock_metadata.update_sync_task.await_args
        assert args.args[0] == "sync_orders"
        assert args.args[1]["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_stop_sync(self, service, mock_metadata, mock_pipeline):
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            pipeline_name="pipeline-sync_orders",
        )
        mock_metadata.update_sync_task.return_value = MagicMock(status="STOPPED")
        result = await service.stop_sync("sync_orders")
        assert result.status == "STOPPED"

    @pytest.mark.asyncio
    async def test_refresh_sync_status_maps_finished_to_stopped(self, service, mock_metadata, mock_pipeline):
        """refresh_sync_status reconciles PG state with SeaTunnel truth.

        A FINISHED SeaTunnel job maps to STOPPED locally (one-shot full
        snapshot ran to completion). This is what lets the UI show the
        real outcome instead of a stale RUNNING.
        """
        task = MagicMock(api_name="sync_orders", pipeline_name="pipeline-sync_orders")
        mock_metadata.get_sync_task.return_value = task
        mock_pipeline.get_job_status.return_value = MagicMock(state="FINISHED")
        mock_metadata.update_sync_task.return_value = MagicMock(status="STOPPED")

        result = await service.refresh_sync_status("sync_orders")

        assert result.status == "STOPPED"
        mock_metadata.update_sync_task.assert_awaited()

    @pytest.mark.asyncio
    async def test_refresh_sync_status_maps_failed(self, service, mock_metadata, mock_pipeline):
        task = MagicMock(api_name="sync_orders", pipeline_name="pipeline-sync_orders")
        mock_metadata.get_sync_task.return_value = task
        mock_pipeline.get_job_status.return_value = MagicMock(state="FAILED")
        mock_metadata.update_sync_task.return_value = MagicMock(status="FAILED")

        result = await service.refresh_sync_status("sync_orders")

        assert result.status == "FAILED"

    @pytest.mark.asyncio
    async def test_refresh_sync_status_skips_unknown(self, service, mock_metadata, mock_pipeline):
        """UNKNOWN (aged out of history) must not overwrite a real stored status."""
        task = MagicMock(api_name="sync_orders", pipeline_name="pipeline-sync_orders")
        mock_metadata.get_sync_task.return_value = task
        mock_pipeline.get_job_status.return_value = MagicMock(state="UNKNOWN")

        result = await service.refresh_sync_status("sync_orders")

        # No update written — the stored state is preserved.
        mock_metadata.update_sync_task.assert_not_awaited()
        assert result is task

    @pytest.mark.asyncio
    async def test_refresh_sync_status_no_pipeline_name(self, service, mock_metadata, mock_pipeline):
        """A task with no pipeline_name has nothing to reconcile."""
        task = MagicMock(api_name="sync_orders", pipeline_name=None)
        mock_metadata.get_sync_task.return_value = task

        result = await service.refresh_sync_status("sync_orders")

        mock_pipeline.get_job_status.assert_not_awaited()
        assert result is task


class TestRefreshAllSyncStatus:
    """Batch reconcile — 2 SeaTunnel calls for N tasks (not 2N).

    Validates the four-layer defence: immediate PG render, only reconcile
    non-terminal, per-task failure isolation, SeaTunnel-unreachable safety.
    """

    @pytest.mark.asyncio
    async def test_empty_tasks_returns_empty_no_api_calls(self, service, mock_metadata, mock_pipeline):
        """No tasks → no SeaTunnel calls."""
        mock_metadata.get_datasource.return_value = MagicMock(id="ds1")
        mock_metadata.list_sync_tasks_for_datasource.return_value = []

        result = await service.refresh_all_sync_status("erp_mysql")

        assert result == []
        mock_pipeline.get_jobs_status_batch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_reconciles_mixed_states_in_one_pass(self, service, mock_metadata, mock_pipeline):
        """3 tasks (RUNNING + FINISHED + UNKNOWN) → 1 batch call, terminal updated, UNKNOWN skipped."""
        from ontology.core.schemas.pipeline import PipelineStatus

        mock_metadata.get_datasource.return_value = MagicMock(id="ds1")
        tasks = [
            MagicMock(api_name="t_running", pipeline_name="p_running", status="RUNNING"),
            MagicMock(api_name="t_done", pipeline_name="p_done", status="RUNNING"),
            MagicMock(api_name="t_unknown", pipeline_name="p_unknown", status="RUNNING"),
        ]
        mock_metadata.list_sync_tasks_for_datasource.return_value = tasks
        mock_pipeline.get_jobs_status_batch.return_value = {
            "p_running": PipelineStatus(name="p_running", state="RUNNING"),
            "p_done": PipelineStatus(name="p_done", state="FINISHED"),
            "p_unknown": PipelineStatus(name="p_unknown", state="UNKNOWN"),
        }
        # update_sync_task returns a MagicMock; final list returns the same tasks.
        mock_metadata.update_sync_task.return_value = MagicMock()

        await service.refresh_all_sync_status("erp_mysql")

        # Exactly ONE batch call, not 3 separate get_job_status calls.
        mock_pipeline.get_jobs_status_batch.assert_awaited_once()
        # FINISHED task updated with status + last_run_at; UNKNOWN skipped.
        updated_names = {c.args[0] for c in mock_metadata.update_sync_task.await_args_list}
        assert "t_running" in updated_names  # RUNNING → RUNNING (update written)
        assert "t_done" in updated_names  # FINISHED → FINISHED (update + last_run_at)
        assert "t_unknown" not in updated_names  # UNKNOWN → skipped (no update)
        # Verify the FINISHED update carries last_run_at (terminal timestamp).
        done_call = next(c for c in mock_metadata.update_sync_task.await_args_list if c.args[0] == "t_done")
        assert done_call.args[1]["status"] == "FINISHED"
        assert "last_run_at" in done_call.args[1]

    @pytest.mark.asyncio
    async def test_batch_skips_tasks_without_pipeline_name(self, service, mock_metadata, mock_pipeline):
        """Tasks with no pipeline_name are not passed to the batch lookup."""
        mock_metadata.get_datasource.return_value = MagicMock(id="ds1")
        tasks = [
            MagicMock(api_name="t_draft", pipeline_name=None, status="DRAFT"),
            MagicMock(api_name="t_live", pipeline_name="p_live", status="RUNNING"),
        ]
        mock_metadata.list_sync_tasks_for_datasource.return_value = tasks
        mock_pipeline.get_jobs_status_batch.return_value = {}

        await service.refresh_all_sync_status("erp_mysql")

        # Only the task with a pipeline_name is looked up.
        called_names = mock_pipeline.get_jobs_status_batch.await_args.args[0]
        assert called_names == {"p_live"}

    @pytest.mark.asyncio
    async def test_batch_seatunnel_unreachable_returns_pg_tasks_untouched(self, service, mock_metadata, mock_pipeline):
        """SeaTunnel unreachable → no updates written, PG-stored tasks returned."""
        mock_metadata.get_datasource.return_value = MagicMock(id="ds1")
        tasks = [MagicMock(api_name="t1", pipeline_name="p1", status="RUNNING")]
        mock_metadata.list_sync_tasks_for_datasource.return_value = tasks
        mock_pipeline.get_jobs_status_batch.side_effect = Exception("SeaTunnel down")

        result = await service.refresh_all_sync_status("erp_mysql")

        mock_metadata.update_sync_task.assert_not_awaited()
        assert result == tasks  # PG-stored tasks returned as-is

    @pytest.mark.asyncio
    async def test_batch_per_task_update_failure_does_not_abort_others(self, service, mock_metadata, mock_pipeline):
        """One task's update failing must not block the others."""
        from ontology.core.schemas.pipeline import PipelineStatus

        mock_metadata.get_datasource.return_value = MagicMock(id="ds1")
        tasks = [
            MagicMock(api_name="t_ok", pipeline_name="p_ok", status="RUNNING"),
            MagicMock(api_name="t_bad", pipeline_name="p_bad", status="RUNNING"),
        ]
        mock_metadata.list_sync_tasks_for_datasource.return_value = tasks
        mock_pipeline.get_jobs_status_batch.return_value = {
            "p_ok": PipelineStatus(name="p_ok", state="FINISHED"),
            "p_bad": PipelineStatus(name="p_bad", state="FAILED"),
        }
        # First update (t_ok) succeeds; second (t_bad) raises.
        mock_metadata.update_sync_task.side_effect = [MagicMock(), Exception("DB write failed")]

        # Must not raise despite the per-task failure.
        await service.refresh_all_sync_status("erp_mysql")

        assert mock_metadata.update_sync_task.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_returns_fresh_pg_truth_after_reconcile(self, service, mock_metadata, mock_pipeline):
        """Returns the re-listed tasks (PG-truth post-reconcile), not the pre-update objects."""
        from ontology.core.schemas.pipeline import PipelineStatus

        mock_metadata.get_datasource.return_value = MagicMock(id="ds1")
        pre_update = [MagicMock(api_name="t1", pipeline_name="p1", status="RUNNING")]
        post_update = [MagicMock(api_name="t1", pipeline_name="p1", status="FINISHED")]
        # list_sync_tasks_for_datasource is called twice: once to load, once to return.
        mock_metadata.list_sync_tasks_for_datasource.side_effect = [pre_update, post_update]
        mock_pipeline.get_jobs_status_batch.return_value = {
            "p1": PipelineStatus(name="p1", state="FINISHED"),
        }
        mock_metadata.update_sync_task.return_value = MagicMock()

        result = await service.refresh_all_sync_status("erp_mysql")

        assert result == post_update  # fresh PG-truth, not the pre-update list

    @pytest.mark.asyncio
    async def test_submit_sync_pipeline_full_snapshot_query_is_none(self, service, monkeypatch):
        """full_snapshot syncs pass query=None so the HOCON template uses its
        default `SELECT * FROM <table>` (PG expands ``*`` with real column
        names, avoiding the camelCase-folding bug that a hand-written
        ``SELECT opId, ...`` would trigger)."""
        from ontology.core.schemas.datasource import SyncTask

        ds = _make_ds(api_name="xiaoling", connector_type="postgresql")
        ds.connector_config = {
            "host": "gaia-postgres",
            "port": "5432",
            "database": "xiaoling",
            "username": "ontology",
            "password": "ontology",
        }
        task = SyncTask(
            id="t1",
            api_name="sync_cann_op",
            data_source_id=ds.id,
            target_dataset_api_name="ontology.cann_op_raw",
            sync_mode="full_snapshot",
            source_config={"table": "cann_op"},
            status="DRAFT",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        service.metadata.get_datasource_by_id = AsyncMock(return_value=ds)
        service._resolve_credentials = AsyncMock(return_value=("ontology", "ontology"))
        service.dataset.ensure_namespace = AsyncMock()
        service.dataset.drop_table_if_exists = AsyncMock()
        service._build_jdbc_url = lambda ct, cfg: "jdbc:postgresql://gaia-postgres:5432/xiaoling"
        service._resolve_driver = lambda ct, cfg: "org.postgresql.Driver"
        service._rewrite_source_host_for_seatunnel = lambda cfg, ct: cfg
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="sync_cann_op_raw")

        service.pipeline.create_sync_pipeline = fake_create

        await service._submit_sync_pipeline(task)

        source_config = captured["source_config"]
        assert source_config["query"] is None, "full_snapshot must pass query=None so HOCON defaults to SELECT *"
        assert source_config["table"] == "cann_op"

    @pytest.mark.asyncio
    async def test_submit_sync_pipeline_incremental_query_filters_feedback_loop(self, service, monkeypatch):
        """incremental syncs build `SELECT * FROM <table>` then append the
        gaia_sync_tx feedback-loop filter clause (no ::text cast, no
        hand-written column list)."""
        from ontology.core.schemas.datasource import SyncTask

        ds = _make_ds(api_name="xiaoling", connector_type="postgresql")
        ds.connector_config = {
            "host": "gaia-postgres",
            "port": "5432",
            "database": "xiaoling",
            "username": "ontology",
            "password": "ontology",
        }
        task = SyncTask(
            id="t2",
            api_name="sync_cann_op_inc",
            data_source_id=ds.id,
            target_dataset_api_name="ontology.cann_op_raw",
            sync_mode="incremental",
            source_config={"table": "cann_op", "last_sync_tx": "tx-abc-123"},
            status="DRAFT",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        service.metadata.get_datasource_by_id = AsyncMock(return_value=ds)
        service._resolve_credentials = AsyncMock(return_value=("ontology", "ontology"))
        service.dataset.ensure_namespace = AsyncMock()
        service.dataset.drop_table_if_exists = AsyncMock()
        service._build_jdbc_url = lambda ct, cfg: "jdbc:postgresql://gaia-postgres:5432/xiaoling"
        service._resolve_driver = lambda ct, cfg: "org.postgresql.Driver"
        service._rewrite_source_host_for_seatunnel = lambda cfg, ct: cfg
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="sync_cann_op_raw")

        service.pipeline.create_sync_pipeline = fake_create

        await service._submit_sync_pipeline(task)

        source_config = captured["source_config"]
        query = source_config["query"]
        assert query is not None
        assert query.startswith("SELECT * FROM cann_op")
        assert "gaia_sync_tx" in query
        assert "tx-abc-123" in query
        # No ::text cast — the timestamptz workaround is gone
        assert "::text" not in query

    @pytest.mark.asyncio
    async def test_delete_sync_task(self, service, mock_metadata, mock_pipeline):
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            pipeline_name="pipeline-sync_orders",
            target_dataset_api_name="orders_raw",
        )
        mock_metadata.delete_sync_task.return_value = MagicMock(
            target_dataset_api_name="orders_raw",
        )
        await service.delete_sync_task("sync_orders")
        mock_pipeline.stop.assert_awaited_once()
        mock_metadata.delete_sync_task.assert_awaited_once_with("sync_orders", auto_commit=False)
        mock_metadata.delete_dataset.assert_awaited_once_with("orders_raw", auto_commit=False)
        mock_metadata.commit_transaction.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_sync_task_stop_errors_ignored(self, service, mock_metadata, mock_pipeline):
        mock_metadata.get_sync_task.return_value = MagicMock(
            api_name="sync_orders",
            pipeline_name="pipeline-sync_orders",
            target_dataset_api_name="orders_raw",
        )
        mock_metadata.delete_sync_task.return_value = MagicMock(
            target_dataset_api_name="orders_raw",
        )
        mock_pipeline.stop.side_effect = Exception("Pipeline not found")
        await service.delete_sync_task("sync_orders")
        mock_metadata.delete_sync_task.assert_awaited_once_with("sync_orders", auto_commit=False)
        mock_metadata.delete_dataset.assert_awaited_once_with("orders_raw", auto_commit=False)
        mock_metadata.commit_transaction.assert_awaited_once()


class TestDatasetGovernance:
    @pytest.mark.asyncio
    async def test_register_dataset(self, service, mock_metadata):
        mock_metadata.create_dataset.return_value = MagicMock(api_name="orders_dataset")
        ds = DatasetGovernanceCreate(api_name="orders_dataset", display_name="Orders Dataset")
        result = await service.register_dataset(ds)
        assert result.api_name == "orders_dataset"

    @pytest.mark.asyncio
    async def test_get_dataset(self, service, mock_metadata):
        mock_metadata.get_dataset.return_value = MagicMock(api_name="orders_dataset")
        result = await service.get_dataset("orders_dataset")
        assert result.api_name == "orders_dataset"

    @pytest.mark.asyncio
    async def test_list_datasets(self, service, mock_metadata):
        mock_metadata.list_datasets.return_value = [MagicMock(api_name="ds1")]
        result = await service.list_datasets()
        assert len(result) == 1


class TestImpactAnalysis:
    @pytest.mark.asyncio
    async def test_delete_datasource_impact(self, service, mock_metadata):
        ds = _make_ds()
        mock_metadata.get_datasource.return_value = ds
        mock_metadata.list_sync_tasks_for_datasource.return_value = [
            MagicMock(api_name="sync_orders"),
            MagicMock(api_name="sync_products"),
        ]
        request = ImpactAnalysisRequest(
            target_type="datasource",
            target_api_name="erp_mysql",
            action="delete",
        )
        result = await service.analyze_impact(request)
        assert len(result.impacts) == 2
        assert result.impacts[0].effect == "CASCADE_DELETE"

    @pytest.mark.asyncio
    async def test_delete_dataset_with_referencing_object_types(self, service, mock_metadata):
        mock_metadata.get_object_types_for_dataset.return_value = [
            MagicMock(api_name="order"),
            MagicMock(api_name="customer"),
        ]
        request = ImpactAnalysisRequest(
            target_type="dataset",
            target_api_name="orders_ds",
            action="delete",
        )
        result = await service.analyze_impact(request)
        assert len(result.impacts) == 2
        assert result.impacts[0].effect == "ORPHANED"

    @pytest.mark.asyncio
    async def test_severity_low(self, service, mock_metadata):
        ds = _make_ds()
        mock_metadata.get_datasource.return_value = ds
        mock_metadata.list_sync_tasks_for_datasource.return_value = []
        request = ImpactAnalysisRequest(
            target_type="datasource",
            target_api_name="erp_mysql",
            action="delete",
        )
        result = await service.analyze_impact(request)
        assert result.severity == "LOW"

    @pytest.mark.asyncio
    async def test_severity_high_with_orphaned(self, service, mock_metadata):
        mock_metadata.get_object_types_for_dataset.return_value = [
            MagicMock(api_name="o1"),
            MagicMock(api_name="o2"),
            MagicMock(api_name="o3"),
            MagicMock(api_name="o4"),
        ]
        request = ImpactAnalysisRequest(
            target_type="dataset",
            target_api_name="orders_ds",
            action="delete",
        )
        result = await service.analyze_impact(request)
        assert result.severity == "HIGH"


class TestHelpers:
    def test_compute_capabilities_mysql(self):
        caps = DataSourceService._compute_capabilities("mysql")
        assert "explore" in caps
        assert "batch_sync" in caps
        assert "cdc" in caps

    def test_compute_capabilities_postgresql(self):
        caps = DataSourceService._compute_capabilities("postgresql")
        assert "explore" in caps
        assert "batch_sync" in caps
        assert "cdc" in caps

    def test_compute_capabilities_unknown(self):
        caps = DataSourceService._compute_capabilities("unknownDb")
        assert caps == ["explore"]

    # ── _extract_pk_column_names：Gravitino indexes → PK 列名集合 ──
    def test_extract_pk_single_column(self):
        indexes = [{"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["modelId"]]}]
        assert DataSourceService._extract_pk_column_names(indexes) == {"modelId"}

    def test_extract_pk_composite(self):
        indexes = [{"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["a"], ["b"]]}]
        assert DataSourceService._extract_pk_column_names(indexes) == {"a", "b"}

    def test_extract_pk_ignores_non_primary_indexes(self):
        indexes = [
            {"indexType": "UNIQUE_KEY", "name": "uq", "fieldNames": [["email"]]},
            {"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": [["id"]]},
        ]
        assert DataSourceService._extract_pk_column_names(indexes) == {"id"}

    def test_extract_pk_empty_indexes(self):
        assert DataSourceService._extract_pk_column_names([]) == set()

    def test_extract_pk_no_primary_key_entry(self):
        indexes = [{"indexType": "UNIQUE_KEY", "name": "uq", "fieldNames": [["email"]]}]
        assert DataSourceService._extract_pk_column_names(indexes) == set()

    def test_extract_pk_case_insensitive_index_type(self):
        """indexType 大小写不敏感（Gravitino 返回 'PRIMARY_KEY'，防御性处理小写/混合）。"""
        indexes = [{"indexType": "primary_key", "name": "pk", "fieldNames": [["id"]]}]
        assert DataSourceService._extract_pk_column_names(indexes) == {"id"}

    def test_extract_pk_malformed_field_names_skipped(self):
        """fieldNames 非 list 或 path 为空 → 跳过不崩。"""
        indexes = [
            {"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": "not-a-list"},
            {"indexType": "PRIMARY_KEY", "name": "pk2", "fieldNames": [[]]},  # 空路径
            {"indexType": "PRIMARY_KEY", "name": "pk3", "fieldNames": [["ok"]]},
        ]
        assert DataSourceService._extract_pk_column_names(indexes) == {"ok"}

    def test_extract_pk_string_path(self):
        """path 是纯字符串（非 list）→ 直接加入。"""
        indexes = [{"indexType": "PRIMARY_KEY", "name": "pk", "fieldNames": ["id"]}]
        assert DataSourceService._extract_pk_column_names(indexes) == {"id"}

    def test_build_jdbc_url_mysql(self):
        url = DataSourceService._build_jdbc_url(
            "mysql",
            {
                "host": "db.example.com",
                "port": "3307",
                "database": "erp",
            },
        )
        # 默认 include_database=True（SeaTunnel Jdbc source 需要带库），
        # Gravitino catalog 注册时传 include_database=False。
        assert url == "jdbc:mysql://db.example.com:3307/erp"

    def test_build_jdbc_url_mysql_no_database(self):
        """Gravitino Trino connector 注册 mysql catalog 时 URL 不带 database。"""
        url = DataSourceService._build_jdbc_url(
            "mysql",
            {"host": "db.example.com", "port": "3307", "database": "erp"},
            include_database=False,
        )
        assert url == "jdbc:mysql://db.example.com:3307"

    def test_build_jdbc_url_postgresql(self):
        url = DataSourceService._build_jdbc_url(
            "postgresql",
            {
                "host": "pg.example.com",
                "database": "analytics",
            },
        )
        assert url == "jdbc:postgresql://pg.example.com:5432/analytics"

    def test_build_jdbc_url_with_extra_params(self):
        url = DataSourceService._build_jdbc_url(
            "mysql",
            {
                "host": "db.example.com",
                "port": "3306",
                "database": "erp",
                "extra_params": "useSSL=false&serverTimezone=UTC",
            },
        )
        assert "useSSL=false" in url
        assert "serverTimezone=UTC" in url

    def test_build_jdbc_url_unknown_type(self):
        url = DataSourceService._build_jdbc_url(
            "clickhouse",
            {
                "host": "ch.example.com",
                "port": "8123",
                "database": "logs",
            },
        )
        assert url == "jdbc:clickhouse://ch.example.com:8123/logs"


class TestRefreshRowCount:
    """refresh_row_count back-fills DatasetGovernance.row_count_estimate via Trino.

    MANAGED → SELECT COUNT(*) FROM iceberg.<namespace>.<dataset>;
    VIRTUAL → SELECT COUNT(*) FROM <storage_location> (Trino three-part locator).
    Best-effort: Trino/PG failures return None without raising.
    """

    @pytest.mark.asyncio
    async def test_managed_queries_iceberg_table(self, service, mock_metadata, mock_engine):
        from ontology.core.schemas.datasource import DatasetGovernance

        mock_metadata.get_dataset = AsyncMock(
            return_value=DatasetGovernance(
                id="d1",
                api_name="dealership",
                kind="MANAGED",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        mock_engine.query = AsyncMock(return_value=[{"c": 20}])

        count = await service.refresh_row_count("dealership")

        assert count == 20
        # SQL targets the Iceberg table iceberg.<namespace>.dealership.
        sql = mock_engine.query.await_args.args[0]
        assert "iceberg." in sql and "dealership" in sql
        mock_metadata.update_dataset_stats.assert_awaited_once_with("dealership", 20)

    @pytest.mark.asyncio
    async def test_virtual_queries_storage_location(self, service, mock_metadata, mock_engine):
        from ontology.core.schemas.datasource import DatasetGovernance

        locator = "marketingMysql.marketing_benchmark.t_ods_leads_info"
        mock_metadata.get_dataset = AsyncMock(
            return_value=DatasetGovernance(
                id="d2",
                api_name="leads_virtual",
                storage_location=locator,
                kind="VIRTUAL",
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        mock_engine.query = AsyncMock(return_value=[{"c": 1000}])

        count = await service.refresh_row_count("leads_virtual")

        assert count == 1000
        # SQL uses the storage_location three-part locator directly (no iceberg. prefix).
        sql = mock_engine.query.await_args.args[0]
        assert sql == f"SELECT COUNT(*) AS c FROM {locator}"
        mock_metadata.update_dataset_stats.assert_awaited_once_with("leads_virtual", 1000)

    @pytest.mark.asyncio
    async def test_trino_failure_returns_none(self, service, mock_metadata, mock_engine):
        from ontology.core.schemas.datasource import DatasetGovernance

        mock_metadata.get_dataset = AsyncMock(
            return_value=DatasetGovernance(
                id="d3", api_name="x", kind="MANAGED", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
            )
        )
        mock_engine.query = AsyncMock(side_effect=Exception("trino down"))

        count = await service.refresh_row_count("x")

        assert count is None
        mock_metadata.update_dataset_stats.assert_not_awaited()
