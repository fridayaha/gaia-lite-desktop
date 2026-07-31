-- 011: alert_events 加状态机字段（firing/resolved/acknowledged）+ 自动恢复检测
-- 0.8.66：AlertEvent 从「只追加」表升级为状态机，支持 A 类规则自动恢复 + 人工确认。
--
-- status 取值：
--   firing       默认状态，告警仍在触发
--   resolved     已恢复（A 类规则指标降下来后后台轮询自动标记）
--   acknowledged 人已确认（正交状态，不影响 firing/resolved，但不再重复发通知）
--
-- 字段说明：
--   acknowledged_by/at  谁在何时确认
--   last_seen_at        最近一次仍触发的轮询时间（A 类每次轮询看到仍触发就更新）
--   resolved_at         恢复时间（status=resolved 时填）

ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'firing';
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS acknowledged_by VARCHAR(64);
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMPTZ;
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
ALTER TABLE alert_events ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_alert_events_status ON alert_events(status);

-- 已有事件回填 status='firing'（DEFAULT 已处理，但显式 UPDATE 兜底 NULL/空串）
UPDATE alert_events SET status = 'firing' WHERE status IS NULL OR status = '';
