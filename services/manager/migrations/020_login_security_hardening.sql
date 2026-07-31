-- 020_login_security_hardening.sql
-- Phase 0 登录安全加固：失败计数 + 锁定 + last_login
-- 本地 DB 用 create_all 自动建新列；云 DB 必须手动跑 020。
-- 注意：audit_logs.actor_id 已在 models/__init__.py:736 设为 nullable=True，
--       observability.py:2493 已用 isouter=True LEFT JOIN，本 migration 不再 ALTER audit_logs.actor_id。

-- 1. users 表加失败计数 + 锁定字段
ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

-- 2. users 表加 last_login 字段
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_ip VARCHAR(64);
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_user_agent VARCHAR(256);

-- 3. 索引（按 locked_until 查询当前被锁用户，partial index 跳过 NULL 行）
CREATE INDEX IF NOT EXISTS ix_users_locked_until
    ON users (locked_until) WHERE locked_until IS NOT NULL;
