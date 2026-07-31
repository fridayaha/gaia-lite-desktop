-- 004_instance_dify_config.sql
-- 把 Dify 应用绑定从 definition.model_config.dify 迁移到 instance.dify_config 新列
-- 让每个实例独立绑定 Dify 应用（dev/staging/prod 可指向不同 app）
-- create_all 在新部署会自动加列；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

ALTER TABLE agent_instances
    ADD COLUMN IF NOT EXISTS dify_config JSON NOT NULL DEFAULT '{}'::json;

-- Backfill: 从 definition.model_config.dify 拷贝到所有 DIFY 引擎实例
-- model_config 是 JSON（非 JSONB），不能用 ? 操作符，用 ->'dify' IS NOT NULL 判空
UPDATE agent_instances i
SET dify_config = d.model_config->'dify'
FROM agent_definitions d
WHERE d.id = i.definition_id
  AND d.engine_type = 'DIFY'
  AND d.model_config->'dify' IS NOT NULL;

-- 注：不清理 definition.model_config 里的 'dify' key
-- 读侧 fallback 让历史快照继续可用，cleanup 留给下个 PR
