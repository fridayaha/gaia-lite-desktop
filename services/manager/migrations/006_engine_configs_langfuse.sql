-- 006_engine_configs_langfuse.sql
-- 给 engine_configs 加 Langfuse 集成配置字段（Dify 外接模式 per-EngineConfig）。
-- 用户在 Dify workspace 后台配 Langfuse 集成后，把同一组 host + public_key + secret_key 填到这里，
-- manager 调 Langfuse API 按 metadata[app_id] 反查 per-app 用量。
-- secret_key 用 Fernet 加密存（langfuse_secret_key_encrypted），跟 admin_password_encrypted 同机制。
-- create_all 只建新表不改旧表（CLAUDE.md memory v2_to_v3_schema_gaps），所以老部署要手动跑这个 ALTER。

ALTER TABLE engine_configs
    ADD COLUMN IF NOT EXISTS langfuse_host VARCHAR(512),
    ADD COLUMN IF NOT EXISTS langfuse_public_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS langfuse_secret_key_encrypted TEXT;
