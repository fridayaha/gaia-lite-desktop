"""MySQL 连接器（lite 桌面版, B4）。

DuckDB mysql_scanner 扩展 ATTACH 外部 MySQL。ATTACH 串格式：
``ATTACH 'host=... port=... user=... password=... database=...' AS src_<alias> (TYPE mysql_scanner)``
"""

from __future__ import annotations

from typing import Any

from ontology.core.exceptions import OntologyError
from ontology.plugins.connectors.base import DataSourceConnector


def _build_mysql_conn_str(config: dict[str, Any], credentials: tuple[str, str]) -> str:
    host = config.get("host", "localhost")
    port = config.get("port", 3306)
    database = config.get("database", "")
    if not database:
        raise OntologyError("MySQL data source requires 'database' in connector_config")
    username, password = credentials
    parts = [f"host={host}", f"port={port}", f"database={database}"]
    if username:
        parts.append(f"user={username}")
    if password:
        parts.append(f"password={password}")
    extra = config.get("extra_params", "")
    if extra:
        parts.append(str(extra))
    return " ".join(parts)


class MySQLConnector(DataSourceConnector):
    connector_type = "mysql"

    def to_duckdb_attach(self, alias: str) -> str:
        conn_str = _build_mysql_conn_str(self.config, self.credentials)
        conn_str_escaped = conn_str.replace("'", "\\'")
        return f"ATTACH '{conn_str_escaped}' AS {self.attach_alias(alias)} (TYPE mysql_scanner)"

    async def test_connection(self) -> bool:
        return True  # ATTACH 即探活，service 经 engine.attach 实测

    def default_schema(self) -> str:
        # MySQL 的 schema = database；用户填的 database 即探索起始库。
        return str(self.config.get("database", "") or "")
