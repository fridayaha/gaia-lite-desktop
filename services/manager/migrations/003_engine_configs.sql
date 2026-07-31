-- 003_engine_configs.sql
-- 引擎级系统配置表（v1: Dify 引擎，全局单条配置）。
-- admin_password / cached_access_token 用 Fernet 加密存（app/core/crypto.py）。
-- create_all 会在新部署自动建表；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

CREATE TABLE IF NOT EXISTS engine_configs (
    id UUID PRIMARY KEY,
    group_id UUID REFERENCES user_groups(id) ON DELETE CASCADE,
    engine_type VARCHAR(32) NOT NULL,
    mode VARCHAR(16) NOT NULL DEFAULT 'EXTERNAL',
    base_url VARCHAR(512),
    admin_email VARCHAR(255),
    admin_password_encrypted TEXT,
    cached_access_token_encrypted TEXT,
    cached_token_expires_at TIMESTAMPTZ,
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_engine_configs_group_id
    ON engine_configs (group_id);
CREATE INDEX IF NOT EXISTS ix_engine_configs_engine_type
    ON engine_configs (engine_type);

-- 全局唯一约束：每个 engine_type 只允许一个 group_id IS NULL 的全局配置
CREATE UNIQUE INDEX IF NOT EXISTS uq_engine_config_global_engine_type
    ON engine_configs (engine_type)
    WHERE group_id IS NULL;
