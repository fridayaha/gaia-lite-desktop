# union-agent-hub service

## 来源

本服务由旧项目 `UnionAgent-Hub` 迁移而来，当前作为 `union_agent` 新项目 `services/` 下的独立服务组件存在。

## 当前接入状态

* 已完成部署验证 (2026-06-15)；
* 核心链路 (Import/Review/Publish/Discover) 烟雾测试通过；
* 端口 8003 验证可用；
* 暂未接入新项目根目录构建；
* 暂未接入 K8s manifests；
* 暂未接入 gateway route；
* 暂未接入统一 CI。

## API

* 当前保持原 Hub API prefix：`/api/hub`；
* Runtime API 保持原路径；
* 后续如需适配新项目 `/api/<service>` 规范，需要单独评估。

## 端口

* 建议预留端口：8003；
* 当前不修改新项目根部署文件；
* 后续部署阶段再确认端口分配。

## 健康检查

* 当前健康检查以 Hub 现有实现为准；
* 新项目现有服务规范为 `GET /health`；
* 如 Hub 尚未提供 `/health`，后续单独适配；
* 本阶段不改业务代码强行适配。

## 数据库

* 当前建议 Hub 使用独立 `DATABASE_URL`；
* Alembic migration 暂由 Hub 自管；
* 是否接入平台统一数据库，由部署阶段确认。

## 存储

* 当前 LocalStorageAdapter 仍为开发/单实例默认；
* `.hub_storage/` 不得入库；
* S3/MinIO/CommonStorage 后续单独评估；
* MT-4 Storage tenant prefix 仍未完成。

## 鉴权与租户

* 当前 Hub 支持 Header-based AuthContext；
* Runtime Consumer header、organization_id、workspace_id 后续由 gateway 或调用方注入；
* MT-5 scoped role binding 仍未完成。

## 安全扫描

* Betterleaks / Gitleaks adapter 已存在；
* 是否将 Betterleaks / Gitleaks 内置到服务镜像，部署阶段确认；
* scanner report 不得入库。

## 后续对接事项

* 端口 8003 确认；
* `/health` 适配；
* K8s manifests；
* gateway route；
* `DATABASE_URL`；
* object storage；
* scanner binary；
* Runtime header 注入；
* CI tests；
* secret scanning gate；
* Harness / OfficeClaw schema。
