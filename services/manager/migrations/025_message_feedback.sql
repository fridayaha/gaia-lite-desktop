-- 025_message_feedback.sql
-- 0.8.x 消息级用户反馈（赞/踩）+ 消息收藏（业务库为 source of truth）
-- 本地 DB 用 create_all 自动建表（不会改旧表）；ECS DB 必须手动跑 025。
-- 锚点 message_ref："mid:{引擎消息id}" 优先，"hash:{sha256[:16]}" 兜底；
-- run_id 仅作 Langfuse 镜像元数据。收藏不镜像。

CREATE TABLE IF NOT EXISTS message_feedbacks (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    message_ref VARCHAR(128) NOT NULL,
    run_id VARCHAR(64),
    value VARCHAR(8) NOT NULL,
    reason VARCHAR(32),
    comment TEXT,
    content_snapshot TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 一人对一会话内一条消息仅一条反馈（重复提交即更新）
CREATE UNIQUE INDEX IF NOT EXISTS uq_message_feedbacks_user_msg
    ON message_feedbacks (user_id, session_id, message_ref);

CREATE INDEX IF NOT EXISTS ix_message_feedbacks_user_id
    ON message_feedbacks (user_id);

CREATE INDEX IF NOT EXISTS ix_message_feedbacks_session_id
    ON message_feedbacks (session_id);

CREATE TABLE IF NOT EXISTS message_favorites (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    message_ref VARCHAR(128) NOT NULL,
    run_id VARCHAR(64),
    content_snapshot TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_message_favorites_user_msg
    ON message_favorites (user_id, session_id, message_ref);

-- 「我的收藏」列表按用户 + 时间倒序
CREATE INDEX IF NOT EXISTS ix_message_favorites_user_created
    ON message_favorites (user_id, created_at);
