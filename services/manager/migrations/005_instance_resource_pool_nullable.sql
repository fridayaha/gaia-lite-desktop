-- 005_instance_resource_pool_nullable.sql
-- Dify 外接模式实例直接调外部 Dify 平台 API，不依赖 k8s 资源池。
-- resource_pool_id 改可空，MANAGED 模式（K8s 部署 Dify Pod）和 Hermes/OpenClaw 引擎仍需资源池。
-- create_all 在新部署会自动建 nullable 列；此脚本供已有部署手动执行（CLAUDE.md:53 规范）。

ALTER TABLE agent_instances
    ALTER COLUMN resource_pool_id DROP NOT NULL;
