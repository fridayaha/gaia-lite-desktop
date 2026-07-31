-- 007_add_user_phone_realname.sql
-- users 表新增 phone（手机号）和 real_name（真实姓名）列。
-- create_all 会在新部署自动建表；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(32);
ALTER TABLE users ADD COLUMN IF NOT EXISTS real_name VARCHAR(128);
