-- 002_add_dify_engine_type.sql
-- 扩展 agent_definitions.engine_type / agent_versions.engine_type 枚举新增 DIFY 值。
-- 配合 Dify v2 引擎集成（外部实例 + Pod 双模）。
-- create_all 在新部署自动建枚举；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

-- PostgreSQL 枚举类型新增值（无需 DROP COLUMN，APPEND 是非阻塞操作）。
-- 类型名是 enginetype（SQLAlchemy Enum(EngineType) 自动生成，无下划线），不是 engine_type。
ALTER TYPE enginetype ADD VALUE IF NOT EXISTS 'DIFY';
