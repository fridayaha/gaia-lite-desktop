"""DataSourceConnector — 数据源连接器抽象基类（lite 桌面版, B4）。

每个连接器封装一种外部源的接入逻辑：
- ``to_duckdb_attach(alias)``：生成 DuckDB ATTACH 语句（DataSourceService 创建时调，
  经 DuckDBEngine.attach 注册为 src_<alias> catalog）。
- ``test_connection()``：直连源探活（不依赖 ATTACH，快速失败反馈）。
- ``default_schema()``：该源类型的默认探索 schema（PG=public、MySQL=配置 database、
  CSV/SQLite 无 schema 概念用 main）。

explore/describe/sample 不在 connector 内实现——统一经 DuckDBEngine 查 ATTACH 后的
catalog（B2 的 list_tables/describe_table/sample_data 已支持三段式引用），避免重复
造轮子且保持引擎层单一入口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataSourceConnector(ABC):
    """数据源连接器抽象基类。

    Args:
        config: connector_config（host/port/database/path 等，不含敏感凭据）。
        credentials: (username, password) 或 (access_key, secret_key)，由
            DataSourceService._resolve_credentials 解析后传入。CSV/SQLite 忽略。
    """

    #: 该连接器处理的 connector_type（小写，如 "postgresql"）。
    connector_type: str = ""

    def __init__(self, config: dict[str, Any], credentials: tuple[str, str] = ("", "")) -> None:
        self.config = config
        self.credentials = credentials

    @abstractmethod
    def to_duckdb_attach(self, alias: str) -> str:
        """生成 DuckDB ATTACH 语句。

        返回形如 ``ATTACH 'postgres:dbname=...' AS src_<alias> (TYPE postgres_scanner)``
        的完整语句。alias 即 DataSource api_name（小写），ATTACH 别名约定 src_<alias>。
        DataSourceService 调 DuckDBEngine.attach(alias, attach_sql) 注册。
        """
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """直连源探活（不经 ATTACH）。返回 True/False，不抛异常。"""
        ...

    def default_schema(self) -> str:
        """该源类型的默认探索 schema。子类可覆盖。"""
        return ""

    @staticmethod
    def attach_alias(alias: str) -> str:
        """ATTACH 别名约定：src_<alias lower>。"""
        return f"src_{alias.lower()}"
