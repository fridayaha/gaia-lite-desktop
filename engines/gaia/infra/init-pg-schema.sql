-- PostgreSQL initialization script for Ontology metadata
-- Auto-executed on first container start (docker-entrypoint-initdb.d)
--
-- 本脚本只负责基础设施：建 gravitino_store schema + pgcrypto 扩展。
-- Gaia 业务表（public schema）的建表 DDL 不再写在这里 —— 由 Alembic 统一管理：
--   docker compose 的 migrate 容器 / 本地 `make dev-backend` 会执行
--   `alembic upgrade head`，从 alembic/versions/ 的 revision 链建出全部业务表。
--
-- 这样 ORM 模型（src/ontology/core/models/）+ Alembic revision 链 = schema 单一真相源，
-- 避免之前「init 脚本 vs migration 脚本」分裂导致的 schema 漂移。
-- Gravitino 的 gravitino_store schema 仍由 02-gravitino-schema.sql 初始化（Gravitino 自管）。

-- ============================================================
-- Gravitino Entity Store Schema
-- Gravitino 把元数据存到 gravitino_store schema（与 Gaia 业务表物理隔离）。
-- 表结构由 02-gravitino-schema.sql 建，这里只建空 schema。
-- ============================================================
CREATE SCHEMA IF NOT EXISTS gravitino_store;

-- ============================================================
-- pgcrypto 扩展
-- 提供 gen_random_bytes() / gen_random_uuid()，Alembic migration 与运行时均依赖。
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- Gaia 业务表（public schema）
-- 不在此建表 —— 由 Alembic `upgrade head` 从 alembic/versions/ 建出。
-- 全新环境启动顺序：
--   1. docker compose up（postgres 容器跑本脚本建 gravitino schema + pgcrypto）
--   2. migrate 容器跑 `alembic upgrade head` 建 public 业务表
--   3. api 容器启动（depends_on migrate）
-- 本地开发：make dev-backend 自动先跑 `alembic upgrade head`。
-- ============================================================
