-- 015: operation_logs 表加 operator_user_agent 列（安全审计追溯客户端类型/版本）
ALTER TABLE operation_logs ADD COLUMN IF NOT EXISTS operator_user_agent VARCHAR(512);
