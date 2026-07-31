"""DuckDBEngine — lite 桌面版嵌入式联邦查询引擎（B2）。

替代云版的 TrinoQueryEngine。DuckDB 进程内嵌入式，持久化到本地文件
（``settings.lite_warehouse_path``），通过 ``ATTACH`` 接入外部数据源
（PG/MySQL/CSV/SQLite）做联邦查询。

实现与 TrinoQueryEngine 同构的契约（``query`` / ``list_tables`` /
``describe_table`` / ``sample_data`` / ``sample_data_columns`` /
``test_connection``），让 ObjectQueryService / DataSourceService 在引擎层零改动
调用（SQL dialect 改造留 B3）。额外提供 ``execute``（无返回行的 DDL，如 ATTACH）
与 ``attach`` / ``detach``，供 B4 数据源连接器注册外部源。

线程安全：DuckDB Python 连接**非线程安全**，并发 ``con.execute`` 会竞态。本引擎
单连接 + ``asyncio.Lock`` 串行化所有连接访问（桌面单用户可接受；DuckDB 分析查询
快，串行不构成瓶颈）。所有同步 DB 调用经 ``asyncio.to_thread`` 跑在线程池，不阻塞
事件循环。

catalog 语义：Trino 的 catalog = Gravitino catalog 名；DuckDB 对应 ATTACH 的别名
（``ATTACH 'postgres:...' AS src_<api_name>`` → catalog = ``src_<api_name>``）。
三段式引用 ``"catalog"."schema"."table"`` DuckDB 原生支持。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ontology.config.settings import settings
from ontology.core.exceptions import DataSourceUnreachableError, OntologyError

if TYPE_CHECKING:
    import duckdb

# DuckDB 错误消息中指示「外部数据源连不上」的子串（ATTACH 的源不可达）。
# 对齐 TrinoQueryEngine 的 _DATASOURCE_UNREACHABLE_MARKERS 思路。
_DATASOURCE_UNREACHABLE_MARKERS = (
    "connection refused",
    "connection timed out",
    "no route to host",
    "unknownhostexception",
    "could not connect",
    "unable to connect",
    "communications link failure",
)


def _classify_duckdb_error(exc: Exception) -> OntologyError:
    """Map a raw duckdb exception to a domain exception.

    - 外部源连不上（IOException / 消息含连接失败标记）→ DataSourceUnreachableError
    - 其余（语法/类型/catalog 不存在/绑定）→ OntologyError
    """
    msg = str(exc)
    lowered = msg.lower()
    if any(marker in lowered for marker in _DATASOURCE_UNREACHABLE_MARKERS):
        return DataSourceUnreachableError(msg, code="DATASOURCE_UNREACHABLE")
    return OntologyError(f"DuckDB query failed: {exc}")


class DuckDBEngine:
    """嵌入式 DuckDB 联邦查询引擎（lite 版 QueryEngine 实现）。

    Args:
        db_path: 可选的 DuckDB 文件路径。None 时用 settings.lite_warehouse_path。
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = db_path
        self._connection: duckdb.DuckDBPyConnection | None = None
        self._lock = asyncio.Lock()
        # 已 ATTACH 的外部源别名（供 detach / 诊断）。
        self._attached: set[str] = set()

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        """惰性初始化的持久化 DuckDB 连接。访问须持有 self._lock（串行化）。"""
        if self._connection is None:
            import duckdb

            path = Path(self._db_path or settings.lite_warehouse_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = duckdb.connect(str(path))
        return self._connection

    # ═════════════════════════════════════════════════════════════
    # Core query execution
    # ═════════════════════════════════════════════════════════════

    def _query_sync(self, sql: str, params: list[Any] | None) -> list[dict[str, Any]]:
        """同步执行查询 → list[dict]。调用方须持锁。"""
        try:
            cur = self.connection.execute(sql, params) if params else self.connection.execute(sql)
            rows = cur.fetchall()
            if not cur.description:
                return []
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in rows]
        except Exception as exc:  # noqa: BLE001 — duckdb 异常类型多样，统一分类
            raise _classify_duckdb_error(exc) from exc

    def _execute_sync(self, sql: str, params: list[Any] | None = None) -> None:
        """同步执行无返回行的 DDL（ATTACH/DETACH/CREATE/DROP）。调用方须持锁。"""
        try:
            if params:
                self.connection.execute(sql, params)
            else:
                self.connection.execute(sql)
        except Exception as exc:  # noqa: BLE001
            raise _classify_duckdb_error(exc) from exc

    async def query(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """执行 SQL 查询，返回 list[dict]（列名 → 值）。

        Args:
            sql: SQL 查询串（``?`` 占位符参数化）
            params: 可选位置参数

        Raises:
            DataSourceUnreachableError: ATTACH 的外部源连不上。
            OntologyError: 其他查询失败（语法/类型/catalog 不存在等）。
        """
        async with self._lock:
            return await asyncio.to_thread(self._query_sync, sql, params)

    async def execute(self, sql: str, params: list[Any] | None = None) -> None:
        """执行无返回行的 DDL（ATTACH/DETACH/CREATE/DROP）。

        供 B4 数据源连接器注册/注销外部源用。异常语义同 :meth:`query`。
        """
        async with self._lock:
            await asyncio.to_thread(self._execute_sync, sql, params)

    # ═════════════════════════════════════════════════════════════
    # External source attach / detach（B4 connector 用）
    # ═════════════════════════════════════════════════════════════

    async def attach(self, alias: str, attach_sql: str) -> None:
        """ATTACH 一个外部数据源为命名 catalog。

        Args:
            alias: 本地别名（catalog 名），用于后续 detach / 诊断。
            attach_sql: 完整 ATTACH 语句，由 B4 连接器的 ``to_duckdb_attach()``
                生成（如 ``ATTACH 'postgres:dbname=...' AS src_x (TYPE postgres_scanner)``）。
                别名须与语句内的 AS 别名一致。
        """
        await self.execute(attach_sql)
        self._attached.add(alias)

    async def detach(self, alias: str) -> None:
        """DETACH 一个外部数据源。不存在时静默忽略（幂等）。"""
        try:
            await self.execute(f'DETACH "{alias}"')
        except OntologyError:
            # 别名未 ATTACH 或已 detach —— 幂等。
            pass
        self._attached.discard(alias)

    # ═════════════════════════════════════════════════════════════
    # Data Source Exploration Helpers（对齐 TrinoQueryEngine 契约）
    # ═════════════════════════════════════════════════════════════

    async def list_tables(self, catalog: str, schema: str = "") -> list[str]:
        """列出 catalog（ATTACH 别名）下的表。

        DuckDB 用 ``duckdb_tables()`` 系统函数跨 catalog 列表（attached DuckDB
        文件不暴露 ``catalog.information_schema``）。schema 给定时只返回该 schema
        下的表名；不给定时返回 ``schema.table`` 形式（对齐 Trino 行为）。

        Args:
            catalog: ATTACH 别名（duckdb_tables.database_name）
            schema: 可选 schema 名过滤
        """
        if schema:
            rows = await self.query(
                "SELECT table_name FROM duckdb_tables() WHERE database_name = ? AND schema_name = ?",
                [catalog, schema],
            )
            return [r["table_name"] for r in rows if r.get("table_name")]

        rows = await self.query(
            "SELECT schema_name, table_name FROM duckdb_tables() WHERE database_name = ?",
            [catalog],
        )
        return [f"{r['schema_name']}.{r['table_name']}" for r in rows if r.get("table_name")]

    async def describe_table(self, catalog: str, schema: str, table: str) -> list[dict[str, Any]]:
        """描述表的列（column_name/column_type/null/key/default/extra）。"""
        sql = f'DESCRIBE "{catalog}"."{schema}"."{table}"'
        return await self.query(sql)

    async def sample_data(self, catalog: str, schema: str, table: str, limit: int = 10) -> list[dict[str, Any]]:
        """采样表数据。"""
        sql = f'SELECT * FROM "{catalog}"."{schema}"."{table}" LIMIT {limit}'
        return await self.query(sql)

    async def sample_data_columns(
        self,
        catalog: str,
        schema: str,
        table: str,
        columns: list[str],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """采样表数据（仅指定列）。列名来自后端元数据，双引号包裹防保留字。"""
        col_list = ", ".join(f'"{c}"' for c in columns)
        sql = f'SELECT {col_list} FROM "{catalog}"."{schema}"."{table}" LIMIT {limit}'
        return await self.query(sql)

    async def test_connection(self, catalog: str) -> bool:
        """检查 catalog（ATTACH 别名）是否已挂载且可达。

        ATTACH 成功即证明外部源可达；本方法复查别名是否在 ``duckdb_databases()``
        中（catalog 丢失返回 False，不抛异常）。
        """
        try:
            rows = await self.query(
                "SELECT 1 FROM duckdb_databases() WHERE database_name = ? LIMIT 1",
                [catalog],
            )
            return len(rows) > 0
        except Exception:  # noqa: BLE001 — 连通性探测失败统一返回 False
            return False

    # ═════════════════════════════════════════════════════════════

    async def close(self) -> None:
        """关闭连接。app shutdown 调用。"""
        async with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            self._attached.clear()
