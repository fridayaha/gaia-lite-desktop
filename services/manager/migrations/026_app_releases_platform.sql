-- 026_app_releases_platform.sql
-- APP 管理支持鸿蒙：app_releases 加 platform 列（android / harmony），
-- version 唯一约束升级为 (platform, version) 复合唯一——同版本号可分别发 android 与 harmony 包。
-- 本地 DB 用 create_all 自动建表（不会改旧表）；ECS DB 必须手动跑 026。

ALTER TABLE app_releases
    ADD COLUMN IF NOT EXISTS platform VARCHAR(16) NOT NULL DEFAULT 'android';

-- 旧 version 单例唯一约束/索引进程下线，换 (platform, version) 复合唯一。
-- 两种形态都处理：create_all 建出来的是 CONSTRAINT（带同名 backing index），
-- 024 SQL 建出来的是裸 UNIQUE INDEX；两条语句各管一种，IF EXISTS 保证幂等。
ALTER TABLE app_releases DROP CONSTRAINT IF EXISTS uq_app_releases_version;
DROP INDEX IF EXISTS uq_app_releases_version;

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_releases_platform_version
    ON app_releases (platform, version);

-- platform 索引：列表按平台筛选走此索引
CREATE INDEX IF NOT EXISTS ix_app_releases_platform
    ON app_releases (platform);
