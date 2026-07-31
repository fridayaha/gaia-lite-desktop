-- 012_agent_api_keys.sql
-- 智能体实例的 OpenAI 兼容 API Key（sk- 前缀，HMAC-SHA256 hash 存储）
-- 每实例最多 10 个（service 层 enforce，DB 不加 CHECK 因 per-instance count 复杂）
-- key_hash: HMAC-SHA256 hex（不可逆，明文仅创建时返回一次）
-- key_prefix: 前 14 字符，用于列表展示 + Gateway 前缀索引查询
-- create_all 在新部署会自动建表；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。
-- 本地 DB + 云 DB 同步执行。

CREATE TABLE IF NOT EXISTS agent_instance_api_keys (
    id              UUID PRIMARY KEY,
    instance_id     UUID NOT NULL REFERENCES agent_instances(id) ON DELETE CASCADE,
    group_id        UUID NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
    name            VARCHAR(128) NOT NULL,
    key_hash        VARCHAR(128) NOT NULL,
    key_prefix      VARCHAR(20) NOT NULL,
    last_used_at    TIMESTAMPTZ,
    last_used_ip    VARCHAR(64),
    created_by      UUID NOT NULL REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_instance_apikey_name UNIQUE (instance_id, name)
);
CREATE INDEX IF NOT EXISTS ix_api_keys_instance_id ON agent_instance_api_keys(instance_id);
CREATE INDEX IF NOT EXISTS ix_api_keys_key_prefix ON agent_instance_api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS ix_api_keys_group_id   ON agent_instance_api_keys(group_id);
