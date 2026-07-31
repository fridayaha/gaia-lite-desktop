"""B4 tests — 数据源连接器插件系统 + DataSourceService lite 路径。

两部分：
1. 连接器单测：to_duckdb_attach 生成正确 ATTACH 语句；CSV/SQLite test_connection
   文件存在性；registry 注册/查询/不支持类型抛错。
2. 端到端（DuckDBEngine + 本地文件）：CSV/SQLite connector ATTACH/导入 →
   DataSourceService.create_datasource → explore → describe_table → sample_data →
   register_virtual_table 全链路真实数据。

不依赖外部 PG/MySQL（PG/MySQL connector 只测 ATTACH 语句生成正确性，实际连接
env-gated）。跨 edition：编译/逻辑层两版都跑；DataSourceService lite 路径用
monkeypatch 强制 edition='lite'。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ontology.core.exceptions import OntologyError
from ontology.plugins.connectors import ConnectorRegistry
from ontology.plugins.connectors.csv_file import CsvFileConnector
from ontology.plugins.connectors.mysql import MySQLConnector
from ontology.plugins.connectors.postgres import PostgresConnector
from ontology.plugins.connectors.sqlite import SQLiteConnector

# 异步测试靠 conftest 的 asyncio_mode=auto。


# ── 连接器单测 ──────────────────────────────────────────────────────────


class TestConnectorRegistry:
    def test_supported_types_includes_four(self):
        reg = ConnectorRegistry()
        for t in ("postgresql", "postgres", "mysql", "csv", "csv_file", "sqlite"):
            assert reg.is_supported(t)

    def test_unsupported_type_raises(self):
        reg = ConnectorRegistry()
        with pytest.raises(OntologyError):
            reg.get("opengauss")

    def test_postgres_alias_maps_same_class(self):
        reg = ConnectorRegistry()
        assert reg.get("postgres") is PostgresConnector
        assert reg.get("postgresql") is PostgresConnector

    def test_create_instantiates_connector(self):
        reg = ConnectorRegistry()
        conn = reg.create("postgresql", {"host": "h", "database": "db"}, ("u", "p"))
        assert isinstance(conn, PostgresConnector)
        assert conn.credentials == ("u", "p")


class TestPostgresConnector:
    def test_to_duckdb_attach(self):
        conn = PostgresConnector(
            config={"host": "localhost", "port": 5432, "database": "mydb"},
            credentials=("alice", "secret"),
        )
        sql = conn.to_duckdb_attach("Erp")
        assert sql.startswith("ATTACH '")
        assert "src_erp" in sql  # alias lower-cased + src_ prefix
        assert "TYPE postgres_scanner" in sql
        assert "dbname=mydb" in sql
        assert "host=localhost" in sql
        assert "port=5432" in sql
        assert "user=alice" in sql
        assert "password=secret" in sql

    def test_missing_database_raises(self):
        conn = PostgresConnector(config={"host": "h"}, credentials=("u", "p"))
        with pytest.raises(OntologyError):
            conn.to_duckdb_attach("erp")

    def test_default_schema_public(self):
        conn = PostgresConnector(config={"host": "h", "database": "d"}, credentials=("", ""))
        assert conn.default_schema() == "public"

    def test_default_schema_override(self):
        conn = PostgresConnector(config={"host": "h", "database": "d", "schema": "myschema"}, credentials=("", ""))
        assert conn.default_schema() == "myschema"


class TestMysqlConnector:
    def test_to_duckdb_attach(self):
        conn = MySQLConnector(
            config={"host": "h", "port": 3306, "database": "db"},
            credentials=("u", "p"),
        )
        sql = conn.to_duckdb_attach("Crm")
        assert "src_crm" in sql
        assert "TYPE mysql_scanner" in sql
        assert "host=h" in sql
        assert "port=3306" in sql
        assert "database=db" in sql

    def test_default_schema_is_database(self):
        conn = MySQLConnector(config={"host": "h", "database": "shop"}, credentials=("", ""))
        assert conn.default_schema() == "shop"


class TestCsvConnector:
    def test_to_duckdb_attach_creates_table(self):
        conn = CsvFileConnector(config={"path": "/tmp/x.csv"}, credentials=("", ""))
        sql = conn.to_duckdb_attach("Sales")
        # CSV 走主库表（CREATE TABLE AS SELECT），无 ATTACH/src_ 前缀。
        assert sql.startswith("CREATE OR REPLACE TABLE sales AS")
        assert "read_csv_auto('/tmp/x.csv')" in sql

    def test_missing_path_raises(self):
        with pytest.raises(OntologyError):
            CsvFileConnector(config={}, credentials=("", ""))

    def test_attach_alias_no_src_prefix(self):
        conn = CsvFileConnector(config={"path": "/tmp/x.csv"}, credentials=("", ""))
        assert conn.attach_alias("Sales") == "sales"

    async def test_test_connection_file_exists(self, tmp_path):
        f = tmp_path / "x.csv"
        f.write_text("a,b\n1,2\n")
        conn = CsvFileConnector(config={"path": str(f)}, credentials=("", ""))
        assert await conn.test_connection() is True

    async def test_test_connection_file_missing(self):
        conn = CsvFileConnector(config={"path": "/nonexistent/path.csv"}, credentials=("", ""))
        assert await conn.test_connection() is False


class TestSqliteConnector:
    def test_to_duckdb_attach(self):
        conn = SQLiteConnector(config={"path": "/tmp/x.db"}, credentials=("", ""))
        sql = conn.to_duckdb_attach("Local")
        assert "src_local" in sql
        assert "TYPE sqlite_scanner" in sql
        assert "/tmp/x.db" in sql

    def test_default_schema_main(self):
        conn = SQLiteConnector(config={"path": "/tmp/x.db"}, credentials=("", ""))
        assert conn.default_schema() == "main"

    async def test_test_connection_file_exists(self, tmp_path):
        f = tmp_path / "x.db"
        f.write_bytes(b"")  # SQLite header not needed for existence check
        conn = SQLiteConnector(config={"path": str(f)}, credentials=("", ""))
        assert await conn.test_connection() is True


# ── 端到端：DataSourceService lite 全链路 ──────────────────────────────


class TestDataSourceServiceLiteE2E:
    """create_datasource → explore → describe → sample → register_virtual_table.

    用真实 DuckDBEngine + 本地 SQLite 文件模拟外部源（不依赖外部 PG/MySQL）。
    """

    @pytest.fixture
    async def sqlite_source(self, tmp_path, monkeypatch):
        """建一个外部 SQLite 文件库（带一张表），返回 (path, DuckDBEngine, svc)。"""
        from ontology.config.settings import settings
        from ontology.layers.engine.duckdb_engine import DuckDBEngine
        from ontology.services.datasource_service import DataSourceService

        monkeypatch.setattr(settings, "edition", "lite")

        ext_path = tmp_path / "ext.db"
        # 用 sqlite_scanner 需要真 SQLite 文件——用 Python sqlite3 建（duckdb 写出
        # 的 .db 是 DuckDB 格式，sqlite_scanner 读不了）。
        import sqlite3

        con = sqlite3.connect(str(ext_path))
        con.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, city TEXT)")
        con.execute("INSERT INTO customers VALUES (1, 'Alice', 'Shanghai'), (2, 'Bob', 'Beijing')")
        con.commit()
        con.close()

        engine = DuckDBEngine(db_path=str(tmp_path / "warehouse.duckdb"))
        # DataSourceService 只用到 engine + metadata（explore/describe/sample/test）。
        # metadata 用真 SQLite MetaStore（B1 已验），但这里只需 get_datasource 返回
        # 一个 DataSource record——用 AsyncMock 起步，避免拉起全 MetaStore。
        from datetime import UTC, datetime

        from ontology.core.schemas.datasource import DataSource

        ds_record = DataSource(
            id="ds1",
            api_name="ext",
            display_name="External",
            connector_type="sqlite",
            connector_config={"path": str(ext_path)},
            credential_id=None,
            status="CONNECTED",
            gravitino_catalog_name="",
            capabilities=["explore", "virtual_table"],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        metadata = MagicMock()
        metadata.get_datasource = AsyncMock(return_value=ds_record)
        metadata.update_datasource = AsyncMock(return_value=ds_record)

        svc = DataSourceService.__new__(DataSourceService)
        svc.engine = engine
        svc.metadata = metadata
        svc.catalog = None
        svc.pipeline = None
        svc.dataset = None
        svc._ingestion_filter = MagicMock()
        svc._object_index_funnel = None

        yield ext_path, engine, svc, ds_record
        await engine.close()

    async def test_create_datasource_attaches_sqlite(self, sqlite_source, monkeypatch):
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")
        ext_path, engine, svc, ds_record = sqlite_source

        # create_datasource 调 _register_lite_datasource → connector.to_duckdb_attach
        # → engine.attach。metadata.create_datasource 返回 record。
        from ontology.core.schemas.datasource import DataSourceCreate

        ds_create = DataSourceCreate(
            api_name="ext",
            display_name="External",
            connector_type="sqlite",
            connector_config={"path": str(ext_path)},
        )
        svc.metadata.create_datasource = AsyncMock(return_value=ds_record)
        record = await svc.create_datasource(ds_create)
        assert record.status == "CONNECTED"

        # ATTACH 后 catalog src_ext 可达。
        assert await engine.test_connection("src_ext") is True

    async def test_explore_lists_tables(self, sqlite_source, monkeypatch):
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")
        ext_path, engine, svc, ds_record = sqlite_source
        # 先 ATTACH（create_datasource 路径）。
        await engine.attach("src_ext", f"ATTACH '{ext_path}' AS src_ext (TYPE sqlite_scanner)")

        result = await svc.explore("ext")
        assert result.database == "main"
        table_names = {t.name for t in result.tables}
        assert "customers" in table_names

    async def test_describe_table_returns_columns(self, sqlite_source, monkeypatch):
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")
        ext_path, engine, svc, ds_record = sqlite_source
        await engine.attach("src_ext", f"ATTACH '{ext_path}' AS src_ext (TYPE sqlite_scanner)")

        info = await svc.describe_table("ext", "main", "customers")
        col_names = {c.name for c in info.columns}
        assert col_names == {"id", "name", "city"}
        id_col = next(c for c in info.columns if c.name == "id")
        assert id_col.data_type  # 非空类型串

    async def test_sample_data_returns_rows(self, sqlite_source, monkeypatch):
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")
        ext_path, engine, svc, ds_record = sqlite_source
        await engine.attach("src_ext", f"ATTACH '{ext_path}' AS src_ext (TYPE sqlite_scanner)")

        rows = await svc.sample_data("ext", "main", "customers", limit=10)
        assert len(rows) == 2
        names = {r["name"] for r in rows}
        assert names == {"Alice", "Bob"}

    async def test_test_connection_success(self, sqlite_source, monkeypatch):
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")
        ext_path, engine, svc, ds_record = sqlite_source
        await engine.attach("src_ext", f"ATTACH '{ext_path}' AS src_ext (TYPE sqlite_scanner)")

        result = await svc.test_connection("ext")
        assert result.success is True

    async def test_register_virtual_table_stores_src_locator(self, sqlite_source, monkeypatch):
        from ontology.config.settings import settings

        monkeypatch.setattr(settings, "edition", "lite")
        ext_path, engine, svc, ds_record = sqlite_source
        await engine.attach("src_ext", f"ATTACH '{ext_path}' AS src_ext (TYPE sqlite_scanner)")

        # register_virtual_table 调 describe_table（已验）+ create_dataset + refresh_row_count。
        # mock metadata.create_dataset 捕获 locator；refresh_row_count 走 engine.sample_data
        # （经 _lite_catalog_and_schema），需 mock 或让它跑（会查 src_ext.main.customers）。
        captured: dict = {}

        from datetime import UTC, datetime

        from ontology.core.schemas.datasource import DatasetGovernance

        async def _fake_create_dataset(create):
            captured["locator"] = create.storage_location
            captured["api_name"] = create.api_name
            return DatasetGovernance(
                id="d1",
                api_name=create.api_name,
                display_name=create.display_name,
                storage_location=create.storage_location,
                kind="VIRTUAL",
                is_view=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

        svc.metadata.create_dataset = AsyncMock(side_effect=_fake_create_dataset)
        svc._maybe_trigger_virtual_projection = lambda *a, **k: None

        # refresh_row_count 内部会 sample_data 查行数——让它真跑（src_ext 已 ATTACH）。
        # 它调 metadata.get_dataset + update_dataset_stats，mock 之。
        svc.metadata.get_dataset = AsyncMock(
            return_value=DatasetGovernance(
                id="d1",
                api_name="customers",
                display_name="customers",
                storage_location="src_ext.main.customers",
                kind="VIRTUAL",
                is_view=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        svc.metadata.update_dataset_stats = AsyncMock(return_value=None)

        result = await svc.register_virtual_table("ext", "main", "customers")
        assert captured["locator"] == "src_ext.main.customers"
        assert result.kind == "VIRTUAL"
