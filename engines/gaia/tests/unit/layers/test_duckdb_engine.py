"""B2 tests — DuckDBEngine 契约 + ATTACH 联邦 + 线程安全。

不依赖外部服务（除可选的 PG ATTACH 集成测试，env-gated）。用 DuckDB 原生能力
验证 query/execute/list_tables/describe_table/sample_data/sample_data_columns/
test_connection/attach/detach/close，以及 asyncio.Lock 串行化下的并发安全。

跑在 full / lite 两 edition（自起临时 DuckDB 文件，不依赖 settings）。
"""

import asyncio
import os

import pytest

from ontology.core.exceptions import OntologyError
from ontology.layers.engine.duckdb_engine import DuckDBEngine

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def engine(tmp_path):
    """Fresh DuckDBEngine backed by a temp file."""
    eng = DuckDBEngine(db_path=str(tmp_path / "test.duckdb"))
    yield eng
    await eng.close()


@pytest.fixture
async def engine_with_table(engine):
    """Engine with a table t(id INT, name VARCHAR) and 2 rows."""
    await engine.execute("CREATE TABLE t (id INTEGER, name VARCHAR)")
    await engine.execute("INSERT INTO t VALUES (?, ?), (?, ?)", [1, "a", 2, "b"])
    return engine


class TestQueryExecute:
    async def test_query_returns_list_of_dicts(self, engine_with_table):
        rows = await engine_with_table.query("SELECT * FROM t ORDER BY id")
        assert rows == [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]

    async def test_query_with_params(self, engine_with_table):
        rows = await engine_with_table.query("SELECT * FROM t WHERE id > ?", [1])
        assert rows == [{"id": 2, "name": "b"}]

    async def test_query_no_rows(self, engine_with_table):
        rows = await engine_with_table.query("SELECT * FROM t WHERE id > ?", [99])
        assert rows == []

    async def test_execute_ddl_no_return(self, engine):
        await engine.execute("CREATE TABLE x (v INTEGER)")
        await engine.execute("INSERT INTO x VALUES (42)")
        rows = await engine.query("SELECT * FROM x")
        assert rows == [{"v": 42}]

    async def test_query_error_maps_to_ontology_error(self, engine):
        with pytest.raises(OntologyError):
            await engine.query("SELECT * FROM nonexistent_table")

    async def test_execute_error_maps_to_ontology_error(self, engine):
        with pytest.raises(OntologyError):
            await engine.execute("CREATE TABLE bad syntax (((")


class TestAttachDetach:
    """ATTACH 一个 DuckDB 文件为外部 catalog —— 联邦查询的核心机制。"""

    @pytest.fixture
    async def attached_src(self, engine, tmp_path):
        import duckdb

        ext = tmp_path / "ext.duckdb"
        ext_con = duckdb.connect(str(ext))
        ext_con.execute("CREATE TABLE remote_t (x INTEGER, label VARCHAR)")
        ext_con.execute("INSERT INTO remote_t VALUES (10, 'ten'), (20, 'twenty')")
        ext_con.close()
        await engine.attach("src", f"ATTACH '{ext}' AS src")
        return engine

    async def test_cross_catalog_query(self, attached_src):
        rows = await attached_src.query("SELECT * FROM src.main.remote_t ORDER BY x")
        assert rows == [{"x": 10, "label": "ten"}, {"x": 20, "label": "twenty"}]

    async def test_current_database_returns_file_stem(self, engine):
        """current_database() 返回主库 database_name（DuckDB 文件名 stem）。

        CSV explore 用它作 catalog_name——duckdb_tables() 的 database_name
        对主库表是文件名 stem（warehouse.duckdb → 'warehouse'），非 'main'。
        回归：lite CSV 数据源 explore 返 tables=[] 是因为 catalog 硬编码 'main'
        与实际 database_name 不符。
        """
        db = await engine.current_database()
        assert db == "test"  # tmp_path/test.duckdb 的 stem

    async def test_list_tables_with_schema(self, attached_src):
        tables = await attached_src.list_tables("src", "main")
        assert tables == ["remote_t"]

    async def test_list_tables_all_schemas(self, attached_src):
        tables = await attached_src.list_tables("src")
        assert "main.remote_t" in tables

    async def test_describe_table(self, attached_src):
        desc = await attached_src.describe_table("src", "main", "remote_t")
        names = [d["column_name"] for d in desc]
        assert names == ["x", "label"]
        assert desc[0]["column_type"] == "INTEGER"

    async def test_sample_data(self, attached_src):
        rows = await attached_src.sample_data("src", "main", "remote_t", limit=1)
        assert len(rows) == 1
        assert set(rows[0].keys()) == {"x", "label"}

    async def test_sample_data_columns(self, attached_src):
        rows = await attached_src.sample_data_columns("src", "main", "remote_t", ["label"], limit=10)
        assert rows == [{"label": "ten"}, {"label": "twenty"}]

    async def test_test_connection_attached(self, attached_src):
        assert await attached_src.test_connection("src") is True

    async def test_detach_makes_catalog_unavailable(self, attached_src):
        await attached_src.detach("src")
        assert await attached_src.test_connection("src") is False
        with pytest.raises(OntologyError):
            await attached_src.query("SELECT * FROM src.main.remote_t")

    async def test_detach_is_idempotent(self, attached_src):
        await attached_src.detach("src")
        # second detach must not raise
        await attached_src.detach("src")


class TestConcurrency:
    """asyncio.Lock 串行化：并发写不丢行、并发读一致。"""

    async def test_concurrent_inserts_no_lost_rows(self, engine):
        await engine.execute("CREATE TABLE c (id INTEGER)")
        # 20 个并发 INSERT，每个经 execute（持锁串行）。
        await asyncio.gather(*(engine.execute("INSERT INTO c VALUES (?)", [i]) for i in range(20)))
        rows = await engine.query("SELECT COUNT(*) AS n FROM c")
        assert rows == [{"n": 20}]

    async def test_concurrent_queries_all_succeed(self, engine_with_table):
        results = await asyncio.gather(*(engine_with_table.query("SELECT COUNT(*) AS n FROM t") for _ in range(15)))
        assert all(r == [{"n": 2}] for r in results)


class TestClose:
    async def test_close_then_reopen_via_new_engine(self, tmp_path):
        path = tmp_path / "persist.duckdb"
        eng = DuckDBEngine(db_path=str(path))
        await eng.execute("CREATE TABLE p (v INTEGER)")
        await eng.execute("INSERT INTO p VALUES (7)")
        await eng.close()
        # Reopen same file — data persists.
        eng2 = DuckDBEngine(db_path=str(path))
        rows = await eng2.query("SELECT * FROM p")
        assert rows == [{"v": 7}]
        await eng2.close()


@pytest.mark.skipif(
    not os.getenv("GAIA_LITE_PG_TEST_DSN"),
    reason="手动集成测试：需 GAIA_LITE_PG_TEST_DSN 指向一个可连的 PG（B2 验收 ATTACH 外部 PG 能查）",
)
class TestAttachPostgresIntegration:
    """B2 验收：ATTACH 外部 PG + 联邦查询。

    手动跑：GAIA_LITE_PG_TEST_DSN='postgres://user:pass@host:5432/db' pytest \
    tests/unit/layers/test_duckdb_engine.py::TestAttachPostgresIntegration -v

    首次运行需联网下载 postgres_scanner 扩展（INSTALL）；后续走缓存。
    """

    async def test_attach_pg_and_query(self, engine, tmp_path):
        dsn = os.environ["GAIA_LITE_PG_TEST_DSN"]
        # postgres_scanner 扩展（首次联网下载，后续缓存）。
        try:
            await engine.execute("INSTALL postgres_scanner")
        except OntologyError:
            pytest.skip("postgres_scanner 扩展下载失败（离线环境）")
        await engine.execute("LOAD postgres_scanner")

        await engine.attach("src_pg", f"ATTACH '{dsn}' AS src_pg (TYPE postgres_scanner)")
        assert await engine.test_connection("src_pg") is True

        tables = await engine.list_tables("src_pg")
        # 能列出表即证明联邦链路通（具体表取决于 DSN 指向的库）。
        assert isinstance(tables, list)

        await engine.detach("src_pg")
        assert await engine.test_connection("src_pg") is False
