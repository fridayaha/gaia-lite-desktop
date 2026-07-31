"""Gaia data-source connector plugin system (lite 桌面版, B4).

lite 版数据源接入抽象：每个连接器封装「如何连外部源 + 如何生成 DuckDB ATTACH
语句」。DataSourceService 创建数据源时调 connector.to_duckdb_attach(api_name)
经 DuckDBEngine.attach 注册外部源为 src_<api_name> catalog，之后 explore/describe/
sample 统一经 DuckDBEngine（B2 已实现）查询 ATTACH 后的 catalog。

四类内置连接器：postgres / mysql / csv_file / sqlite。第三方扩展走 entry_points
（后续路标，B4 先内置 + registry dict）。
"""
