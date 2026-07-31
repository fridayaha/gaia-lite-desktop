-- 016_sms_configs.sql
-- 短信服务商配置表（全局单条）。AK/SK 用 Fernet 加密存（app/core/crypto.py）。
-- v1 只存配置，不实际调 SDK；v2 接入 aliyun/tencent/huawei SDK 时再下发码逻辑。
-- create_all 会在新部署自动建表；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

CREATE TABLE IF NOT EXISTS sms_configs (
    id UUID PRIMARY KEY,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('aliyun', 'tencent', 'huawei')),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    sign_name VARCHAR(64) NOT NULL,
    template_code VARCHAR(64) NOT NULL,
    access_key_id_encrypted TEXT NOT NULL,
    access_key_secret_encrypted TEXT NOT NULL,
    sdk_app_id VARCHAR(128),
    region VARCHAR(64),
    daily_limit INTEGER NOT NULL DEFAULT 1000,
    interval_seconds INTEGER NOT NULL DEFAULT 60,
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_sms_configs_enabled
    ON sms_configs (enabled);
