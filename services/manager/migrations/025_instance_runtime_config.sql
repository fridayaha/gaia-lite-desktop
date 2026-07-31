-- 025_instance_runtime_config.sql
-- agent_instances 加 runtime_config JSON 列，承载 per-instance 运行时开关。
-- 首批用途：{"browser_sandbox": {"enabled": true}} —— 启用浏览器沙箱（VNC 接管）。
-- create_all 在新部署会自动加列；此脚本供已有部署手动执行（CLAUDE.md 规范）。
-- 本地 DB + 云 DB 同步执行。

ALTER TABLE agent_instances
    ADD COLUMN IF NOT EXISTS runtime_config JSON NOT NULL DEFAULT '{}'::json;
