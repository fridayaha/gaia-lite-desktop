-- 001_skill_credentials.sql
-- skill 外部 API 凭证表（per-skill + scope 预留）。
-- 凭证加密存储（credentials_encrypted = Fernet token），明文绝不落库。
-- create_all 会在新部署自动建表；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

CREATE TABLE IF NOT EXISTS skill_credentials (
    id UUID PRIMARY KEY,
    definition_id UUID NOT NULL REFERENCES agent_definitions(id) ON DELETE CASCADE,
    skill_name VARCHAR(128) NOT NULL,
    scope_type VARCHAR(16) NOT NULL DEFAULT 'ALL',
    scope_target_id UUID,
    credentials_encrypted TEXT NOT NULL,
    target_base_url VARCHAR(512),
    created_by UUID NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_credential_scope
    ON skill_credentials (definition_id, skill_name, scope_type, scope_target_id);
CREATE INDEX IF NOT EXISTS ix_skill_credentials_definition_id
    ON skill_credentials (definition_id);
CREATE INDEX IF NOT EXISTS ix_skill_credentials_skill_name
    ON skill_credentials (skill_name);
