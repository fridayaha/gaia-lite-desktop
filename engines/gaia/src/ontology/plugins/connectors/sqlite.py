"""SQLite 连接器（lite 桌面版, B4）。

DuckDB sqlite_scanner 扩展 ATTACH 外部 SQLite 文件。ATTACH 串格式：
``ATTACH '<path>' AS src_<alias> (TYPE sqlite_scanner)``

外部 SQLite 文件库的 schema 通常是 main（业务表所在）。
"""

from __future__ import annotations

import os
from typing import Any

from ontology.core.exceptions import OntologyError
from ontology.plugins.connectors.base import DataSourceConnector


class SQLiteConnector(DataSourceConnector):
    connector_type = "sqlite"

    def __init__(self, config: dict[str, Any], credentials: tuple[str, str] = ("", "")) -> None:
        super().__init__(config, credentials)
        path = config.get("path", "")
        if not path:
            raise OntologyError("SQLite data source requires 'path' in connector_config")
        self.path = os.path.expanduser(str(path))

    def to_duckdb_attach(self, alias: str) -> str:
        path_escaped = self.path.replace("'", "\\'")
        return f"ATTACH '{path_escaped}' AS {self.attach_alias(alias)} (TYPE sqlite_scanner)"

    async def test_connection(self) -> bool:
        return os.path.exists(self.path)

    def default_schema(self) -> str:
        return "main"
