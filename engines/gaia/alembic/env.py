"""Alembic migration environment for Gaia business tables (public schema).

设计要点：
- 单一真相源：ORM 模型（src/ontology/core/models/）的 Base.metadata
- 只管 public schema 的 Gaia 业务表，不碰 gravitino_store（Gravitino 自管）
- DSN 从 settings.pg_sync_dsn 动态读取，不写进 alembic.ini（避免凭据入库）
- 异步项目用 asyncpg，但 Alembic 本身同步，故用 psycopg 同步驱动
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 把 src/ 加入 sys.path，确保能 import ontology 包
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from ontology.config.settings import settings  # noqa: E402
from ontology.core.models import Base  # noqa: E402

# Alembic 配置对象
config = context.config

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 动态注入 DSN（覆盖 alembic.ini 里的空值）
config.set_main_option("sqlalchemy.url", settings.pg_sync_dsn)

# 目标 metadata — Alembic autogenerate 据此对比 DB
target_metadata = Base.metadata

# 只管 public schema，排除 gravitino_store（Gravitino 自管）
# include_schemas=False 时 Alembic 默认只看连接的默认 schema（public），
# 但显式设置 include_object 做双重保险，防止误碰 gravitino_store。
INCLUDE_SCHEMA = "public"

# 部分索引（带 WHERE 子句）无法在 ORM 层表达，手动维护在 migration 里。
# 这些索引名需排除在 autogenerate 对比之外，否则 Alembic 会误报“DB 有 ORM 无”要求删除。
# 新增部分索引时，在此追加索引名。
IGNORED_PARTIAL_INDEXES = {
    "ix_ontologies_active",
    "ix_object_types_active",
    "ix_link_types_active",
    "ix_action_types_active",
}

# Iceberg REST Catalog（JDBC backend）自动建在 public schema 的元数据表，
# 不归 Gaia ORM 管（由 Iceberg REST 服务自己维护）。autogenerate 时需忽略，
# 否则 Alembic 会误报要求删除。
IGNORED_ICEBERG_TABLES = {
    "iceberg_tables",
    "iceberg_namespace_properties",
}

# PostGIS 激活后自动创建的空间参考系统表（CREATE EXTENSION postgis 副产物），
# 不归 Gaia ORM 管。autogenerate 时需忽略，否则误报要求删除。
IGNORED_POSTGIS_TABLES = {
    "spatial_ref_sys",
}

# GeoTime Layer 动态创建的物理表（graph-reasoning-design.md §5）。PostGIS 空间表
# (geo_<ont>__<type>) 和 TimescaleDB 超表 (timeseries_<ont>__<type>__<series>) 由
# GeoTimeStore 在 define_object_type 时按需创建，不归 ORM/Alembic 管（物理资源，
# 随本体 lifecycle 创建/删除）。autogenerate 时按前缀忽略。
IGNORED_GEOTIME_TABLE_PREFIXES = ("geo_", "timeseries_")


def include_object(object, name, type_, reflected, compare_to):
    """过滤掉非 Gaia 管理的对象：gravitino_store schema + 部分索引 + Iceberg REST 元数据表。"""
    if type_ == "schema":
        return name == INCLUDE_SCHEMA
    # 跳过 ORM 无法表达的部分索引（手动维护在 migration 里）
    if type_ == "index" and name in IGNORED_PARTIAL_INDEXES:
        return False
    # 跳过 Iceberg REST Catalog 自动建的元数据表
    if type_ == "table" and name in IGNORED_ICEBERG_TABLES:
        return False
    # 跳过 PostGIS 自动建的空间参考系统表
    if type_ == "table" and name in IGNORED_POSTGIS_TABLES:
        return False
    # 跳过 GeoTime Layer 动态建的物理表（geo_/timeseries_ 前缀）
    if type_ == "table" and any(name.startswith(p) for p in IGNORED_GEOTIME_TABLE_PREFIXES):
        return False
    # 表/列等：检查所属 schema
    schema = getattr(object, "schema", None)
    if schema is not None and schema != INCLUDE_SCHEMA:
        return False
    return True


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=False,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=False,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
