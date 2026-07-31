-- PostgreSQL extension initialization for Graph-reasoning feature.
-- Auto-executed on first container start (docker-entrypoint-initdb.d).
--
-- 此脚本在 01-init-schema.sql 之前执行（按文件名字母序 00 < 01），负责激活
-- 图关联推理与时空多维分析特性（graph-reasoning-design.md §5.5）所需的 PG 扩展：
--   - postgis: 静态空间属性存储（GEOPOINT/GEOSHAPE，GiST 索引）
--   - timescaledb: 动态 GTS 时空序列超表（GEOTEMPORAL_SERIES/TIME_SERIES）
--   - pgcrypto: gen_random_bytes/gen_random_uuid（Alembic 与运行时依赖）
--
-- 这些扩展随一体镜像 ngosang/timescaledb-postgis 预装，本脚本仅 CREATE EXTENSION
-- 激活到 ontology 数据库。pgcrypto 在原 01-init-schema.sql 也有 CREATE，此处提前
-- 激活（IF NOT EXISTS 幂等，重复无害）。
--
-- 注意：TimescaleDB 需要在 postgresql.conf 里加 shared_preload_libraries='timescaledb'
-- 才能完全启用（见 config/postgres/postgresql.conf）。CREATE EXTENSION 在未预加载时
-- 也能成功，但超表功能在预加载后才可用。

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
