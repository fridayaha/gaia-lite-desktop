-- 010: alert_rules 加 category 列（5 大类：tracing/resource/service_health/usage/call_analysis）
-- 已有 3 条 tracing 类规则保留默认 category='tracing'。
-- 新增 13 条规则由 seed_alert_rules 幂等插入（per-rule_type 去重）。

ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS category VARCHAR(32) NOT NULL DEFAULT 'tracing';

UPDATE alert_rules SET category = 'tracing'
WHERE rule_type IN ('error_trace', 'high_latency', 'high_tokens') AND category = 'tracing';

CREATE INDEX IF NOT EXISTS ix_alert_rules_category ON alert_rules(category);
