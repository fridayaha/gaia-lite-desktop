-- IM 用户 ID 映射表
-- 将企业微信/飞书/钉钉等 IM 平台用户 ID 映射到 UnionAgents 内部用户 UUID
--
-- 使用方式:
--   psql -h <host> -U unionagents -d unionagents -f migrate_im_user_bindings.sql

CREATE TABLE IF NOT EXISTS im_user_bindings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id),
    channel_type VARCHAR(32) NOT NULL,
    im_user_id VARCHAR(256) NOT NULL,
    im_user_name VARCHAR(256),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE(channel_type, im_user_id)
);

CREATE INDEX IF NOT EXISTS idx_im_user_bindings_user_id ON im_user_bindings(user_id);
CREATE INDEX IF NOT EXISTS idx_im_user_bindings_lookup ON im_user_bindings(channel_type, im_user_id);

COMMENT ON TABLE im_user_bindings IS 'IM 平台用户 ID ↔ 平台用户 UUID 映射';
COMMENT ON COLUMN im_user_bindings.user_id IS 'UnionAgents 内部用户 ID (users.id)';
COMMENT ON COLUMN im_user_bindings.channel_type IS 'IM 平台类型: wecom / feishu / dingtalk';
COMMENT ON COLUMN im_user_bindings.im_user_id IS 'IM 平台用户 ID（如企业微信 FromUserName）';
