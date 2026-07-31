-- 017_email_configs.sql
-- 邮件服务商配置表（全局单条）。v1 只支持 SMTP，密码用 Fernet 加密存（app/core/crypto.py）。
-- v1 只做配置 CRUD + SMTP login 探活（不实际发邮件），v2 接入发码 endpoint 走 auth.py。
-- create_all 会在新部署自动建表；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

CREATE TABLE IF NOT EXISTS email_configs (
    id UUID PRIMARY KEY,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('smtp')),  -- v1 只允许 smtp
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    smtp_host VARCHAR(255) NOT NULL,
    smtp_port INTEGER NOT NULL DEFAULT 465,
    encryption VARCHAR(16) NOT NULL DEFAULT 'ssl' CHECK (encryption IN ('none', 'ssl', 'starttls')),
    username VARCHAR(255) NOT NULL,
    password_encrypted TEXT NOT NULL,
    from_name VARCHAR(128),
    daily_limit INTEGER NOT NULL DEFAULT 200,
    interval_seconds INTEGER NOT NULL DEFAULT 60,
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_email_configs_enabled
    ON email_configs (enabled);
