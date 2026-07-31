"""PostgreSQL 连接器（lite 桌面版, B4）。

DuckDB postgres_scanner 扩展 ATTACH 外部 PG。ATTACH 串格式：
``ATTACH 'dbname=... user=... password=... host=... port=...' AS src_<alias> (TYPE postgres_scanner)``

psycopg/psycopg2 不直接用——探活也走 DuckDB postgres_scanner（ATTACH 即探活），
避免 lite 版多装一个 PG 驱动链路。凭据从 DataSource 的 Credential 解析后传入。
"""

from __future__ import annotations

from typing import Any

from ontology.core.exceptions import OntologyError
from ontology.plugins.connectors.base import DataSourceConnector


def _build_pg_conn_str(config: dict[str, Any], credentials: tuple[str, str]) -> str:
    """Build a libpq-style connection string (key=value pairs, space-separated)."""
    host = config.get("host", "localhost")
    port = config.get("port", 5432)
    database = config.get("database", "")
    if not database:
        raise OntologyError("PostgreSQL data source requires 'database' in connector_config")
    username, password = credentials
    parts = [f"dbname={database}", f"host={host}", f"port={port}"]
    if username:
        parts.append(f"user={username}")
    if password:
        parts.append(f"password={password}")
    # 允许 extra_params 附加 libpq 选项（如 sslmode=require）。
    extra = config.get("extra_params", "")
    if extra:
        parts.append(str(extra))
    return " ".join(parts)


class PostgresConnector(DataSourceConnector):
    connector_type = "postgresql"

    def to_duckdb_attach(self, alias: str) -> str:
        conn_str = _build_pg_conn_str(self.config, self.credentials)
        # 单引号转义（密码/extra_params 可能含 '）。
        conn_str_escaped = conn_str.replace("'", "\\'")
        return f"ATTACH '{conn_str_escaped}' AS {self.attach_alias(alias)} (TYPE postgres_scanner)"

    async def test_connection(self) -> bool:
        # ATTACH 即探活——尝试 ATTACH 到临时别名，成功即源可达。同步执行经 DuckDB
        # 的话需要 engine；此处由 DataSourceService 经 engine.attach 试跑（见
        # DataSourceService.test_connection lite 分支）。这里返回 True 让 service
        # 走 ATTACH 路径实测。
        return True

    def default_schema(self) -> str:
        # PG 默认 schema public（业务表所在）；用户可在 connector_config.schema 覆盖。
        return str(self.config.get("schema", "") or "public")
