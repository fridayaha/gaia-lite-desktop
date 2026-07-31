-- 021_verification_codes.sql
-- Phase 1 验证码发码能力：verification_codes + verification_tickets 表 + phone 唯一约束
-- 本地 DB 用 create_all 自动建表；云 DB 必须手动跑 021。
-- 注意：上线前需先查重 users.phone，避免 partial unique index 因重复值失败：
--   SELECT phone, count(*) FROM users WHERE phone IS NOT NULL GROUP BY phone HAVING count(*) > 1;

-- 1. 验证码表（6 位数字 OTP，bcrypt hash 存，10min 有效，5 次错误失效）
CREATE TABLE IF NOT EXISTS verification_codes (
    id UUID PRIMARY KEY,
    channel VARCHAR(8) NOT NULL,
    target VARCHAR(256) NOT NULL,
    purpose VARCHAR(32) NOT NULL,
    code_hash VARCHAR(256) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    ip VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_verification_codes_lookup
    ON verification_codes (target, purpose, created_at);

-- 2. ticket 表（验证码校验通过后的临时凭证，单次使用，10min 有效）
CREATE TABLE IF NOT EXISTS verification_tickets (
    id UUID PRIMARY KEY,
    code_id UUID NOT NULL REFERENCES verification_codes(id) ON DELETE CASCADE,
    purpose VARCHAR(32) NOT NULL,
    target VARCHAR(256) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_verification_tickets_lookup
    ON verification_tickets (target, purpose, created_at);

-- 3. users.phone 加 partial unique index（同一 phone 不能绑两个账号）
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_phone
    ON users (phone) WHERE phone IS NOT NULL;
