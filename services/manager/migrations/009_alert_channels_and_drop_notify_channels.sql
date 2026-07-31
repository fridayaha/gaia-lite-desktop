-- 009_alert_channels_and_drop_notify_channels.sql
-- 渠道独立化重构：从「规则持有渠道列表（alert_rules.notify_channels JSON）」
-- 改为「渠道实体订阅规则（alert_channels + channel_rule_subscriptions 关联表）」。
--
-- 原因：运营直觉是「渠道订阅规则」，旧模型逼用户在每条规则里重复选渠道；
-- 加上「全部规则」需求后旧模型无法表达「一个渠道收所有告警」。
--
-- subscribed_all=true 时关联表对该渠道不写行（运行时短路），避免「全部」与显式订阅重复。
-- alert_rules.notify_channels 直接 DROP（fresh start，不保留兼容数据；008 加的列，本期未上线即弃）。
-- create_all 只建新表不改旧表（参考 006/008 migration），老部署必须手动跑此 SQL。

CREATE TABLE IF NOT EXISTS alert_channels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(100) NOT NULL,
    channel_type    VARCHAR(20) NOT NULL CHECK (channel_type IN ('feishu','dingtalk','wecom','email')),
    config          JSON NOT NULL DEFAULT '{}'::json,
    subscribed_all  BOOLEAN NOT NULL DEFAULT FALSE,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channel_rule_subscriptions (
    channel_id  UUID NOT NULL REFERENCES alert_channels(id) ON DELETE CASCADE,
    rule_id     UUID NOT NULL REFERENCES alert_rules(id) ON DELETE CASCADE,
    PRIMARY KEY (channel_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_crs_rule_id ON channel_rule_subscriptions(rule_id);

-- 008 加的 notify_channels 列废弃，DROP（fresh start，本期未上线即弃，无数据需迁移）
ALTER TABLE alert_rules DROP COLUMN IF EXISTS notify_channels;
