-- 014: users 表加 avatar_url 列（账户设置-头像上传功能）
-- 头像存 MinIO 公开 bucket，DB 只存 URL 字符串
ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512);
