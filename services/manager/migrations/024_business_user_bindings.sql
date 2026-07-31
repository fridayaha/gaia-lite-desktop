-- 024_business_user_bindings.sql
-- 业务系统用户身份绑定（1:1）：UA user ↔ 业务系统用户（用户名/手机号/邮箱）
-- 与 im_user_bindings（1:N，IM 渠道身份）并列，构成「平台用户 + IM 用户 + 业务用户」三方身份。
-- 本地 DB 用 create_all 自动建表（不会改旧表）；ECS DB 必须手动跑 024。

CREATE TABLE IF NOT EXISTS business_user_bindings (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_username VARCHAR(128) NOT NULL,
    business_phone VARCHAR(64),
    business_email VARCHAR(256),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 1:1：一个 UA user 只能绑一个业务身份
CREATE UNIQUE INDEX IF NOT EXISTS uq_business_binding_per_user
    ON business_user_bindings (user_id);
CREATE INDEX IF NOT EXISTS ix_business_user_bindings_user_id
    ON business_user_bindings (user_id);
