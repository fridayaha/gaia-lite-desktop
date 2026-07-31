-- 014_engine_rollout.sql
-- 引擎镜像滚动发布（A6）：记录「把所有引擎 Deployment 的 image 批量滚到目标镜像」的操作。
--
-- 背景：发版后存量引擎 Deployment 的 image 字段是创建时烘入的旧值，不随 manager 的
-- UA_ENGINE_IMAGE 更新，只有新建引擎才拿新镜像。本表支撑后台分批 patch image + 等 ready，
-- 替代手动 kubectl set image。状态分类：RUNNING→patch+等ready；SUSPENDED→只 patch 不等
-- ready（下次 resume 拉新镜像）；ARCHIVED/DIFY 外部→跳过。
--
-- 本地 DB + 云 DB 同步执行（CLAUDE.md:53 规范）。

CREATE TABLE IF NOT EXISTS engine_rollouts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    engine_type   VARCHAR(16),                       -- HERMES/OPENCLAW；NULL=全部
    target_image  VARCHAR(512) NOT NULL,
    status        VARCHAR(16) NOT NULL DEFAULT 'RUNNING',  -- RUNNING/FINISHED/FAILED
    batch_size    INTEGER NOT NULL DEFAULT 5,
    force_repull  BOOLEAN NOT NULL DEFAULT FALSE,
    dry_run       BOOLEAN NOT NULL DEFAULT FALSE,
    summary       JSON NOT NULL DEFAULT '{}'::json,
    triggered_by  UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_engine_rollouts_status ON engine_rollouts(status);

CREATE TABLE IF NOT EXISTS engine_rollout_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rollout_id      UUID NOT NULL REFERENCES engine_rollouts(id) ON DELETE CASCADE,
    agent_id        VARCHAR(64) NOT NULL,            -- = str(instance_id)，用于 _engine_name
    deployment_name VARCHAR(128) NOT NULL,
    prev_image      VARCHAR(512),
    engine_status   VARCHAR(16),                     -- 处理时该引擎 DB 状态
    status          VARCHAR(16) NOT NULL DEFAULT 'PENDING',  -- PENDING/PATCHED/READY/FAILED/SKIPPED
    error           TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_engine_rollout_items_rollout_id ON engine_rollout_items(rollout_id);
CREATE INDEX IF NOT EXISTS ix_engine_rollout_items_status     ON engine_rollout_items(status);
