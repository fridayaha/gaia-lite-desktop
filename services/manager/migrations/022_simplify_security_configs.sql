-- 022_simplify_security_configs.sql
-- 0.8.107 安全配置简化：删 enabled 字段 + provider 唯一约束
-- 本地 DB 用 create_all 自动建表；云 DB 必须手动跑 022。
-- 上线前需先查重 provider，避免 unique index 因重复值失败：
--   SELECT provider, count(*) FROM sms_configs GROUP BY provider HAVING count(*) > 1;
--   SELECT provider, count(*) FROM email_configs GROUP BY provider HAVING count(*) > 1;

-- 1. 先 DROP 依赖 enabled 列的旧索引（016/017 创建）
DROP INDEX IF EXISTS ix_sms_configs_enabled;
DROP INDEX IF EXISTS ix_email_configs_enabled;

-- 2. 删除 enabled 字段
ALTER TABLE sms_configs DROP COLUMN IF EXISTS enabled;
ALTER TABLE email_configs DROP COLUMN IF EXISTS enabled;

-- 3. provider 唯一约束（一个 provider 只能建一条记录）
CREATE UNIQUE INDEX IF NOT EXISTS ux_sms_configs_provider ON sms_configs (provider);
CREATE UNIQUE INDEX IF NOT EXISTS ux_email_configs_provider ON email_configs (provider);
