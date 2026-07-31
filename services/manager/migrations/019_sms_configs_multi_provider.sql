-- 019_sms_configs_multi_provider.sql
-- 扩展 sms_configs 表支持 multi-config + 真实 SDK 探活。
-- 原 016 表是 singleton + 字段校验探活；019 改 nullable + 加 is_active 列 + partial unique index。
-- 本地 DB + 云 DB 同步执行。

-- 1. sign_name/template_code/AK/SK 字段改 nullable（按 provider 切换必填在 schema 层校验）
ALTER TABLE sms_configs ALTER COLUMN sign_name DROP NOT NULL;
ALTER TABLE sms_configs ALTER COLUMN template_code DROP NOT NULL;
ALTER TABLE sms_configs ALTER COLUMN access_key_id_encrypted DROP NOT NULL;
ALTER TABLE sms_configs ALTER COLUMN access_key_secret_encrypted DROP NOT NULL;

-- 2. 加 is_active 列
ALTER TABLE sms_configs ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;

-- 3. provider CHECK 约束保持 3 个 provider（016 已有，但显式 DROP + ADD 兜底）
ALTER TABLE sms_configs DROP CONSTRAINT IF EXISTS sms_configs_provider_check;
ALTER TABLE sms_configs ADD CONSTRAINT sms_configs_provider_check
    CHECK (provider IN ('aliyun', 'tencent', 'huawei'));

-- 4. 现有行迁移：把最新一行标为 active（singleton → multi 迁移）
UPDATE sms_configs SET is_active = TRUE
WHERE id IN (SELECT id FROM sms_configs ORDER BY updated_at DESC LIMIT 1);

-- 5. 全局仅一行 is_active=true 的 partial unique index（PG 14+ 特性）
CREATE UNIQUE INDEX IF NOT EXISTS ix_sms_configs_active
    ON sms_configs (is_active) WHERE is_active = TRUE;
