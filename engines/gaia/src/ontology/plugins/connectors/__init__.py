"""ConnectorRegistry — connector_type → DataSourceConnector 类（lite 桌面版, B4）。

lite 版内置四类连接器（postgres/mysql/csv_file/sqlite）。未注册的 connector_type
抛 OntologyError（桌面版不支持该源类型，可后续插件补）。

后续扩展点：改用 Python entry_points 动态发现第三方插件（路标，B4 先 dict）。
"""

from __future__ import annotations

from typing import Any

from ontology.core.exceptions import OntologyError
from ontology.plugins.connectors.base import DataSourceConnector
from ontology.plugins.connectors.csv_file import CsvFileConnector
from ontology.plugins.connectors.mysql import MySQLConnector
from ontology.plugins.connectors.postgres import PostgresConnector
from ontology.plugins.connectors.sqlite import SQLiteConnector

# connector_type → connector 类。type 别名（postgres/postgresql）映射到同一类。
_BUILTIN_CONNECTORS: dict[str, type[DataSourceConnector]] = {
    "postgresql": PostgresConnector,
    "postgres": PostgresConnector,
    "mysql": MySQLConnector,
    "mariadb": MySQLConnector,
    "csv": CsvFileConnector,
    "csv_file": CsvFileConnector,
    "sqlite": SQLiteConnector,
}


class ConnectorRegistry:
    """连接器注册表。lite 版用内置 dict；后续可扩展为 entry_points 动态发现。"""

    def __init__(self, extra: dict[str, type[DataSourceConnector]] | None = None) -> None:
        self._registry: dict[str, type[DataSourceConnector]] = dict(_BUILTIN_CONNECTORS)
        if extra:
            self._registry.update(extra)

    def is_supported(self, connector_type: str) -> bool:
        return connector_type.lower() in self._registry

    def get(self, connector_type: str) -> type[DataSourceConnector]:
        ct = connector_type.lower()
        cls = self._registry.get(ct)
        if cls is None:
            raise OntologyError(
                f"桌面版不支持数据源类型 {connector_type!r}（支持：postgres/mysql/csv/sqlite）",
                code="EDITION_UNAVAILABLE",
            )
        return cls

    def supported_types(self) -> list[str]:
        return sorted(self._registry.keys())

    def create(
        self,
        connector_type: str,
        config: dict[str, Any],
        credentials: tuple[str, str] = ("", ""),
    ) -> DataSourceConnector:
        """实例化一个连接器。"""
        cls = self.get(connector_type)
        return cls(config=config, credentials=credentials)
