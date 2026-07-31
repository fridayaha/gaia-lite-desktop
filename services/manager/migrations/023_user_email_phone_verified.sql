-- 023_user_email_phone_verified.sql
-- 0.8.110 用户邮箱/手机「未认证 / 已认证」两态模型
-- 本地 DB 用 create_all 自动建表（不会改旧表）；ECS DB 必须手动跑 023。
-- 上线前需先查重，避免 partial unique index 因重复值失败：
--   SELECT email, count(*) FROM users WHERE email IS NOT NULL GROUP BY email HAVING count(*) > 1;
--   SELECT phone, count(*) FROM users WHERE phone IS NOT NULL GROUP BY phone HAVING count(*) > 1;

-- 1. 先 DROP 旧的全局唯一约束（依赖 email 列的 unique constraint）
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;
DROP INDEX IF EXISTS ux_users_email;
DROP INDEX IF EXISTS ix_users_email;
DROP INDEX IF EXISTS ux_users_phone;
DROP INDEX IF EXISTS ix_users_phone;

-- 2. email 列改 nullable（之前 NOT NULL）
ALTER TABLE users ALTER COLUMN email DROP NOT NULL;

-- 3. 新增 verified 字段（默认 FALSE，存量用户全部视为未认证）
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified
    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified
    BOOLEAN NOT NULL DEFAULT FALSE;

-- 4. partial unique index：只在 verified=TRUE 时保证全局唯一
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_verified
    ON users (email) WHERE email_verified = TRUE AND email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_phone_verified
    ON users (phone) WHERE phone_verified = TRUE AND phone IS NOT NULL;
