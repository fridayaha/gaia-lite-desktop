"""QueryEngine — 查询引擎结构化契约（Protocol）。

full 版用 ``TrinoQueryEngine``，lite 版用 ``DuckDBEngine``（B2）。两者实现同一组
联邦查询方法，本 Protocol 让 Service 层按契约而非具体类型依赖引擎，便于 edition
切换（B3 ObjectQueryService dialect 改造的基础）。

仅声明两引擎共有的方法：``query`` / ``list_tables`` / ``describe_table`` /
``sample_data`` / ``sample_data_columns`` / ``test_connection``。引擎专属能力
（DuckDB 的 ``attach``/``detach``/``execute``/``close``、Trino 的 ``connection``）
不在此契约，由各自调用方按具体类型访问。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class QueryEngine(Protocol):
    """结构化查询引擎契约（Trino / DuckDB 共同实现）。"""

    async def query(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """执行 SQL 查询，返回 list[dict]（列名 → 值）。"""
        ...

    async def list_tables(self, catalog: str, schema: str = "") -> list[str]:
        """列出 catalog 下的表。"""
        ...

    async def describe_table(self, catalog: str, schema: str, table: str) -> list[dict[str, Any]]:
        """描述表的列。"""
        ...

    async def sample_data(self, catalog: str, schema: str, table: str, limit: int = 10) -> list[dict[str, Any]]:
        """采样表数据。"""
        ...

    async def sample_data_columns(
        self,
        catalog: str,
        schema: str,
        table: str,
        columns: list[str],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """采样表数据（仅指定列）。"""
        ...

    async def test_connection(self, catalog: str) -> bool:
        """检查 catalog 是否可达。"""
        ...
