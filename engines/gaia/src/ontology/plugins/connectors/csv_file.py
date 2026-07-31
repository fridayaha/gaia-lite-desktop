"""CSV 文件连接器（lite 桌面版, B4）。

CSV/Parquet/JSON 等本地文件不走 ATTACH——DuckDB 用 ``read_csv_auto()`` 直接读。
故 to_duckdb_attach 不生成 ATTACH 语句，而是登记文件路径供 DataSourceService 在
查询时用 ``read_csv_auto('<path>')`` 替代表引用。

为保持与 ATTACH 模型一致，CSV connector 把文件**导入到 DuckDB 主库的一张表**
（CREATE TABLE AS SELECT * FROM read_csv_auto(path)），别名即表名，catalog=主库
（无 src_ 前缀）。这样 explore/describe/sample 经 DuckDBEngine 查 main.<table> 即可。
"""

from __future__ import annotations

import os
from typing import Any

from ontology.core.exceptions import OntologyError
from ontology.plugins.connectors.base import DataSourceConnector


class CsvFileConnector(DataSourceConnector):
    connector_type = "csv"

    def __init__(self, config: dict[str, Any], credentials: tuple[str, str] = ("", "")) -> None:
        super().__init__(config, credentials)
        path = config.get("path", "")
        if not path:
            raise OntologyError("CSV data source requires 'path' in connector_config")
        self.path = os.path.expanduser(str(path))

    def to_duckdb_attach(self, alias: str) -> str:
        """把 CSV 导入主库一张表（alias 为表名），而非 ATTACH。

        返回 ``CREATE OR REPLACE TABLE <alias> AS SELECT * FROM read_csv_auto('<path>')``。
        DataSourceService 经 DuckDBEngine.execute 执行。catalog=主库（main），后续
        explore 查 main.<alias>。
        """
        path_escaped = self.path.replace("'", "\\'")
        # 表名用 alias lower（与 DuckDB 标识符约定一致），不前缀 src_（主库表非外部 catalog）。
        table = alias.lower()
        return f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto('{path_escaped}')"

    def attach_alias(self, alias: str) -> str:  # type: ignore[override]
        """CSV 走主库表，alias 即表名（无 src_ 前缀）。"""
        return alias.lower()

    async def test_connection(self) -> bool:
        return os.path.exists(self.path)

    def default_schema(self) -> str:
        return "main"
