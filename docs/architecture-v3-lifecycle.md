# V3 生命周期 — main→develop 等价映射

main 分支的 Manager 生命周期管理能力（9 commit）在 develop V3 架构中
已由 worker 模块完整吸收。本文档记录等价关系，说明为何 Group E 跳过移植。

## 等价映射

| main 能力 | main 位置 | develop 等价位置 | 说明 |
|---|---|---|---|
| Agent CRUD + lifecycle API | `manager/api/agents.py` | `services/manager/app/api/agent_instances.py` | V3 三层模型：AgentInstance 替代 Agent，含 publish/offline 状态机 |
| 部署编排服务 | `manager/services/deployment_service.py` | `services/manager/app/worker/router.py` + `k8s_manager.py` | deploy_flow / suspend / destroy / wakeup / hot_reload / apply_config 全覆盖 |
| K8s STS 管理 | `manager/infra/k8s_manager.py`（StatefulSet） | `services/manager/app/worker/k8s_manager.py`（Deployment） | V3 升级为 Deployment 模式，支持 replicas 缩放 + pod_manager 轮询 |
| Pod 状态查询 | `manager/infra/k8s_manager.py` get_sts_status | `services/manager/app/worker/pod_manager.py` | 独立模块，4 档轮询频率（deploying→running→suspended→destroyed） |
| idle 自动回收 | `manager/services/deployment_service.py` _check_idle | `services/manager/app/worker/recycle_scheduler.py` | RecycleScheduler 5min 检测 → 30min idle SUSPEND；CleanupScheduler 1h → 24h DESTROY |
| metric 周期采样 | `manager/services/deployment_service.py` _sample | `services/manager/app/worker/metric_sampler.py` | MetricSampler 60s 采样 Pod CPU/内存 → resource_metric_samples 表，7d 保留 |
| 外键级联删除 | `migrate_fk_cascade.sql` | `services/manager/app/models/__init__.py` | Base.metadata 已用 `ondelete="CASCADE"`（22 处），无需独立 migration |
| Deploy 事件面板 | admin `DeployConfigTab.vue` | `apps/admin/.../DeployEventsPanel.vue` | 4 档轮询频率 + 实时事件流 |
| 健康检查巡检 | `manager/services/deployment_service.py` _monitor | `services/manager/app/worker/router.py` _update_active_loop | 60s 更新 last_active_at + profile 一致性巡检 |

## V3 新增能力（main 无）

- **AgentDefinition / AgentVersion / AgentInstance 三层模型** — 草稿/发布/实例分离
- **ResourcePool 资源池** — 多租户资源隔离
- **AgentInstanceChannel** — 实例↔渠道绑定（独立于 deployment）
- **profile_resolver** — gateway 层 Profile 路由解析（60s 正缓存 + 10s 负缓存）
- **user_md_sync** — USER.md 注入引擎 Pod（per-user 上下文）
- **minio_archiver** — SUSPEND 时 PVC→MinIO 归档（零开销存档策略）

## 结论

develop V3 worker 已完整吸收 main 的生命周期能力，并在此基础上增加了
三层模型、资源池、Profile 路由等 V3 特性。Group E 跳过 cherry-pick，
仅文档化等价关系。

## 相关文件

- `services/manager/app/worker/` — background.py / k8s_manager.py / pod_manager.py / recycle_scheduler.py / metric_sampler.py / minio_archiver.py / user_md_sync.py / router.py
- `services/manager/app/api/agent_instances.py` — V3 实例 API
- `services/manager/app/models/__init__.py` — V3 三层模型 + ondelete CASCADE

## 相关记忆

- [[monitoring-wiring-status]] — 监控栈部署状态
