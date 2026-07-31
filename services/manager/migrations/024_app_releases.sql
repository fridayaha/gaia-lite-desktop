-- 024_app_releases.sql
-- 0.8.123 APP 管理：APK 发布记录（base APK + patched APK + 元数据）
-- 本地 DB 用 create_all 自动建表（不会改旧表）；ECS DB 必须手动跑 024。
-- manager 启动期 bootstrap 扫描 /app/base-apks/*.apk，按 versionName 幂等注册 draft 记录。

CREATE TABLE IF NOT EXISTS app_releases (
    id UUID PRIMARY KEY,
    version VARCHAR(64) NOT NULL,
    base_apk_object_key VARCHAR(512) NOT NULL,
    patched_apk_object_key VARCHAR(512),
    display_name VARCHAR(128) NOT NULL DEFAULT '知行',
    description TEXT NOT NULL DEFAULT '',
    icon_object_key VARCHAR(512),
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    manager_url VARCHAR(512),
    gateway_url VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

-- version 唯一：相同 versionName 的 base APK 不重复注册（bootstrap 幂等）
CREATE UNIQUE INDEX IF NOT EXISTS uq_app_releases_version
    ON app_releases (version);

-- status 索引：列表过滤 published 记录走此索引
CREATE INDEX IF NOT EXISTS ix_app_releases_status
    ON app_releases (status);
