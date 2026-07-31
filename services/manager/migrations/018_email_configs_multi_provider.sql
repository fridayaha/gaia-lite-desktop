-- 018_email_configs_multi_provider.sql
-- 扩展 email_configs 表支持 multi-config + 4 个 provider（smtp/aliyun/tencent/huawei）。
-- 原 017 表是 singleton + smtp-only；018 改 nullable + 加 cloud 字段 + is_active 列 + partial unique index。
-- 本地 DB + 云 DB 同步执行（CLAUDE.md:53 规范）。

-- 1. smtp 字段改 nullable（cloud providers 不用）
ALTER TABLE email_configs ALTER COLUMN smtp_host DROP NOT NULL;
ALTER TABLE email_configs ALTER COLUMN smtp_port DROP NOT NULL;
ALTER TABLE email_configs ALTER COLUMN encryption DROP NOT NULL;
ALTER TABLE email_configs ALTER COLUMN username DROP NOT NULL;
ALTER TABLE email_configs ALTER COLUMN password_encrypted DROP NOT NULL;

-- 2. 加 is_active 列 + cloud 字段
ALTER TABLE email_configs ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE email_configs ADD COLUMN IF NOT EXISTS access_key_id_encrypted TEXT;
ALTER TABLE email_configs ADD COLUMN IF NOT EXISTS access_key_secret_encrypted TEXT;
ALTER TABLE email_configs ADD COLUMN IF NOT EXISTS region VARCHAR(64);
ALTER TABLE email_configs ADD COLUMN IF NOT EXISTS from_email VARCHAR(255);

-- 3. 替换 provider CHECK 约束（允许 4 个 provider）
ALTER TABLE email_configs DROP CONSTRAINT IF EXISTS email_configs_provider_check;
ALTER TABLE email_configs ADD CONSTRAINT email_configs_provider_check
    CHECK (provider IN ('smtp', 'aliyun', 'tencent', 'huawei'));

-- 4. 现有行迁移：把最新一行标为 active（singleton → multi 迁移）
UPDATE email_configs SET is_active = TRUE
WHERE id IN (SELECT id FROM email_configs ORDER BY updated_at DESC LIMIT 1);

-- 5. 全局仅一行 is_active=true 的 partial unique index（PG 14+ 特性）
CREATE UNIQUE INDEX IF NOT EXISTS ix_email_configs_active
    ON email_configs (is_active) WHERE is_active = TRUE;
