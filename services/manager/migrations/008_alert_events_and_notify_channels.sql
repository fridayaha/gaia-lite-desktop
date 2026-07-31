-- 008_alert_events_and_notify_channels.sql
-- 1) alert_rules 加 notify_channels JSON 字段（4 渠道：feishu/dingtalk/wecom/email 单向推送）。
--    独立于 AgentInstance 的 IM ChannelType 枚举（场景不同：IM 双向通信 vs 告警单向推送）。
--    webhook URL/邮箱地址敏感，仅平台管理员可见（list_alert_rules 走 require_platform_admin）。
-- 2) 新表 alert_events：后台 _alert_check_loop 触发告警时写入，
--    用于去重（同 rule+trace 1h 内不重复）+ 历史查询。rule_id ondelete=SET NULL 保留历史。
-- create_all 只建新表不改旧表（参考 006 migration），老部署必须手动跑此 ALTER。
-- 本地 DB + 云 DB 同步执行（CLAUDE.md:53 规范）。

ALTER TABLE alert_rules
    ADD COLUMN IF NOT EXISTS notify_channels JSON NOT NULL DEFAULT '[]'::json;

CREATE TABLE IF NOT EXISTS alert_events (
    id              UUID PRIMARY KEY,
    rule_id         UUID REFERENCES alert_rules(id) ON DELETE SET NULL,
    rule_name       VARCHAR(64) NOT NULL,
    rule_type       VARCHAR(32) NOT NULL,
    trace_id        VARCHAR(64),
    agent_id        VARCHAR(64),
    severity        VARCHAR(16) NOT NULL,
    message         TEXT NOT NULL,
    notified_channels JSON NOT NULL DEFAULT '[]'::json,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_alert_events_rule_id    ON alert_events(rule_id);
CREATE INDEX IF NOT EXISTS ix_alert_events_trace_id   ON alert_events(trace_id);
CREATE INDEX IF NOT EXISTS ix_alert_events_created_at ON alert_events(created_at);
