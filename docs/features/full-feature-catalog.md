# UnionAgents（知行）— 产品特性全量清单

> 版本：v0.7.0（V3 三层重构，2026-06-23 线上发布）
> 用途：作为「版本能力全量复刻」基线，用于迁移到其它语言/框架时逐项核对。
> 维护：每次新增能力请同步更新本清单。

---

## 0. 产品定位与整体架构

**UnionAgents（知行）** 是面向企业客户的多智能体平台，核心价值：
- **智能体开发层**：定义/版本/人设/技能的可视化开发与版本化管理
- **运行资源管理层**：K8s 资源池规格、配额、回收策略
- **智能体实例层**：定义 × 版本 × 资源池的运行实例，完整生命周期
- **统一模型网关**：LiteLLM 作为全系统唯一模型出口，per-instance 精确计费
- **企业 IM 集成**：飞书/企业微信/钉钉渠道接入，流式响应
- **终端门户**：浏览器端 Chat 界面，复刻 hermes-webui 体验

**两大后端微服务**（同 namespace `unionagents` 部署，REST 解耦）：
| 服务 | 端口 | 职责 |
|------|------|------|
| Manager | 8002 | 业务后台 API：定义/资源池/实例/权限/计费/仪表盘；**含原 Controller worker**（`/api/controller/*` 引擎 Pod 生命周期：K8s 资源、Profile、存档恢复、回收调度，已并入 manager，无独立 :8001 服务） |
| Gateway | 8010 | 反向代理 + IM 渠道分发 + SSE 流式 + 权限闸门 |

**两大前端**：
| 前端 | 技术栈 | 端口 | 面向 |
|------|--------|------|------|
| Admin Console | Vue3 + Element Plus + TS（vue-pure-admin） | 8848 | 平台/组管理员 |
| Enduser Portal | Vue3 + Tailwind + Vite + Pinia | 3000 | 终端用户 |

**版本演进**：
- V1：单体，Agent CRUD 直管引擎
- V2：引入 Hermes Profile 多租户、PVC 持久化、UserGroup 隔离、IM 初步集成
- V3（当前）：定义/资源/实例三层分离、LiteLLM 唯一模型网关、完整生命周期、技能下沉定义层、RBAC + access_scope 分离

---

## 一、Manager Backend（services/manager）

### 1.1 智能体定义层（V3 定义层）

#### F-MGR-001 智能体定义 CRUD
- **描述**：管理智能体元数据（名称、描述、头像色、引擎类型、状态、所属组），支持草稿编辑。
- **组件**：Manager Backend — AgentDefinitions API
- **API**：`POST/GET/PUT/DELETE /api/manager/agent-definitions`
- **实现**：`AgentDefinition` 模型（name, description, avatar_color, engine_type, status, group_id）；组隔离，跨组返回 404；配置字段 persona_config / model_config / skill_config / memory_config（JSON）。

#### F-MGR-002 版本快照与发布
- **描述**：发布定义时生成不可变版本快照，支持版本回滚与语义化版本号。
- **组件**：Manager Backend — AgentDefinitions API
- **API**：`POST /api/manager/agent-definitions/{definition_id}/publish`
- **实现**：`AgentVersion` 模型（version_no, 配置快照, change_log）；发布时草稿配置拷贝为快照并置为 current_version；定义详情返回 current_version_no 与 instance_count。

#### F-MGR-003 人设配置（SOUL.md）
- **描述**：Markdown 格式人设，写文件即生效，由 controller fan-out 到所有引擎 Pod。
- **组件**：Manager Backend — AgentDefinitions API（配置存储）+ Controller（同步）
- **实现**：存于 persona_config；自适应 V1/V2 目录结构；Hermes 按会话读取。

#### F-MGR-004 技能管理（定义层）
- **描述**：技能挂定义层，支持安装/卸载/开关/列表，热生效不重启。
- **组件**：Manager Backend — Agent Skills API
- **API**：
  - `GET /api/manager/agent-definitions/{definition_id}/skills`（列表）
  - `POST .../skills/install`（安装，zip 包上传）
  - `PUT .../skills/{skill_id}`（开关）
  - `DELETE .../skills/{skill_id}`（卸载）
- **实现**：技能配置存 skill_config JSON；内置技能扫描 + 自定义 zip 上传；开关操作重写 config.yaml 的 skills.disabled 热生效；zip→tar.gz 转换并剥离顶层目录；路径安全过滤。

### 1.2 资源池层（V3 资源层）

#### F-MGR-005 资源池 CRUD
- **描述**：管理 K8s 资源规格（CPU/内存 min-max、副本数、会话数、max_profiles_per_pod）与回收策略。
- **组件**：Manager Backend — ResourcePools API
- **API**：`POST/GET/PUT/DELETE /api/manager/resource-pools`
- **实现**：`ResourcePool` 模型；支持平台共享池（group_id=NULL）与组私有池；回收策略字段 idle_suspend_minutes / idle_destroy_hours；支持克隆。

#### F-MGR-006 资源池实时监控
- **描述**：监控资源池下所有 Pod 的 CPU/内存用量与状态。
- **组件**：Manager Backend — ResourcePools API
- **API**：`GET /api/manager/resource-pools/{pool_id}/metrics`
- **实现**：代理 controller 拉取 metrics-server 数据；返回运行/停止/异常状态统计 + 实时用量。

### 1.3 实例层（V3 实例层）

#### F-MGR-007 实例 CRUD
- **描述**：定义 × 版本 × 资源池的实例关联，每实例分配 LiteLLM key 归属 UserGroup Team。
- **组件**：Manager Backend — AgentInstances API
- **API**：`POST/GET/PUT/DELETE /api/manager/agent-instances`
- **实现**：`AgentInstance` 模型（definition_id, version_id, resource_pool_id, status, litellm_config）；业务状态 DRAFT/PUBLISHED/OFFLINE。

#### F-MGR-008 实例业务生命周期
- **描述**：上线/下架/版本切换/克隆。
- **组件**：Manager Backend — AgentInstances API
- **API**：
  - `POST .../publish`（上线）
  - `POST .../offline`（下架）
  - `POST .../switch-version`（版本切换，触发 controller 重启）
  - `POST .../clone`（克隆）

#### F-MGR-009 实例运行时生命周期
- **描述**：通过 controller 管理部署/暂停/恢复/重启/销毁。
- **组件**：Manager Backend — AgentInstances API（代理 Controller）
- **API**：`POST /api/manager/agent-instances/{instance_id}/{deploy|suspend|resume|restart|destroy}`
- **实现**：SUSPEND 时存档数据；DESTROY 时归档到 MinIO（ARCHIVED）；统一 ControllerError 错误处理。

#### F-MGR-010 实例运行时详情
- **描述**：部署状态、Pod 列表、日志、指标、概览、SSE 部署事件流。
- **组件**：Manager Backend — AgentInstances API
- **API**：
  - `GET .../deployment-status`
  - `GET .../pods`、`GET .../pods/{pod_name}/logs`
  - `GET .../metrics`、`GET .../overview`
  - `GET .../deploy/events`（SSE 流式部署进度）

#### F-MGR-011 实例 IM 渠道绑定
- **描述**：为实例绑定企业微信/飞书/钉钉渠道，支持独立/共享 Profile。
- **组件**：Manager Backend — AgentInstances API
- **API**：`GET/POST/PUT/DELETE /api/manager/agent-instances/{instance_id}/channels`
- **实现**：`AgentInstanceChannel`（channel_type, scope_type, config, enabled）；敏感信息脱敏显示。

#### F-MGR-012 实例 LiteLLM Key reprovision
- **描述**：补实例 LiteLLM key 重新签发接口。
- **组件**：Manager Backend — AgentInstances API
- **API**：`POST /api/manager/agent-instances/{instance_id}/litellm-key/reprovision`

#### F-MGR-013 终端门户可访问实例
- **描述**：为终端门户提供用户有权访问的实例列表。
- **组件**：Manager Backend — AgentInstances API
- **API**：`GET /api/manager/agent-instances/accessible`
- **实现**：组用户仅见本组已上线实例；平台管理员跨组可见；返回简化信息（id, name, description, engine_type）。

### 1.4 LiteLLM 模型网关集成

#### F-MGR-020 模型组管理
- **描述**：管理上游供应商（OpenAI/Anthropic/DeepSeek 等）部署与参数。
- **组件**：Manager Backend — LiteLLM API
- **API**：`GET/POST/PUT/DELETE /api/manager/litellm/models`
- **实现**：模型组名供 Agent 表单选择，对应 litellm model 参数；权限 `litellm:model:manage`（平台管理员）。

#### F-MGR-021 虚拟 Key 管理
- **描述**：每实例一虚拟 Key，归属 UserGroup 对应 Team，支持预算/速率限制。
- **组件**：Manager Backend — LiteLLM API
- **API**：`GET/POST/PUT/DELETE /api/manager/litellm/keys`
- **实现**：max_budget + budget_duration；rpm/tpm 限制；平台管理员不限范围，组管理员仅限本组；启用/禁用；通过 metadata.agent_id 或 key_alias 智能关联解析。

#### F-MGR-022 Team 同步
- **描述**：UserGroup ↔ LiteLLM Team 1:1 映射同步。
- **组件**：Manager Backend — LiteLLM API
- **API**：`GET /api/manager/litellm/teams`、`POST /api/manager/litellm/teams/sync`
- **实现**：创建 UserGroup 时自动 ensure Team；平台默认 Team ID `settings.litellm_default_team_id`。

#### F-MGR-023 用量计费统计
- **描述**：按组/模型/时间维度的 token 用量与费用统计。
- **组件**：Manager Backend — LiteLLM API
- **API**：
  - `GET /api/manager/litellm/spend`（明细）
  - `GET .../spend/summary`（组维度汇总）
  - `GET .../spend/by-model`（模型维度聚合）
  - `GET .../spend/trend`（趋势，裁剪 end+1 的「明天」0 点）
- **实现**：USD→CNY 汇率转换；组管理员仅见本组，平台管理员全平台。

### 1.5 IM 渠道接入

#### F-MGR-030 IM 用户绑定
- **描述**：企业微信/飞书/钉钉用户 ID 映射到平台用户。
- **组件**：Manager Backend — IM Bindings API
- **API**：`GET/POST/DELETE /api/manager/users/{user_id}/im-bindings`
- **实现**：`ImUserBinding`（channel_type, im_user_id, im_user_name）；平台管理员可管任意用户，组用户仅限共同组用户。

### 1.6 权限与隔离

#### F-MGR-040 用户组管理（最小隔离单元）
- **描述**：UserGroup 作为最小隔离单元（不引入 tenant 概念），所有资源按 group_id 归属。
- **组件**：Manager Backend — User Groups API
- **API**：`GET/POST/PUT/DELETE /api/manager/user-groups`
- **实现**：`UserGroup`（name, code, description, litellm_team_id）；自动生成机器码用于 MinIO 前缀与 Pod label；跨组不可见。

#### F-MGR-041 RBAC 角色权限
- **描述**：基于角色的访问控制，细粒度权限（menu/api/button 三类）。
- **组件**：Manager Backend — Roles API
- **API**：`GET/POST/PUT/DELETE /api/manager/roles`
- **实现**：Role-Permission 多对多；V3 三类资源权限种子（definitions/instances/resource-pools）；平台管理员专属 `litellm:model:manage`、用户组管理等。

#### F-MGR-042 用户管理
- **描述**：用户账户 CRUD、角色分配、状态管理。
- **组件**：Manager Backend — Users API
- **API**：`GET/POST/PUT/DELETE /api/manager/users`
- **实现**：JWT 双 Token（access 30min + refresh 7d）；密码 bcrypt。

#### F-MGR-043 管理员旁路
- **描述**：平台管理员可绕过组隔离管理任意资源。
- **组件**：Manager Backend — 核心权限逻辑
- **实现**：`is_platform_admin()` 判断；`group_ids=None` 旁路组隔离；IM 绑定、资源池等支持跨组操作。

#### F-MGR-044 access_scope 访问控制
- **描述**：实例访问范围 ALL/USER/USER_GROUP，与 RBAC 分离（终端用户不走 RBAC）。
- **组件**：Manager Backend — 实例访问逻辑
- **实现**：计费 Team 由 access_scope 派生（USER_GROUP→对应 Team）。

### 1.7 监控仪表盘

#### F-MGR-050 系统仪表盘
- **描述**：平台整体运行状态与关键指标。
- **组件**：Manager Backend — Dashboard API
- **API**：
  - `GET /api/manager/dashboard/activities`（最近活动）
  - `GET .../group`（组管理员概览）
  - `GET .../health`（系统健康检查）
  - `GET .../resources`（资源消耗）
  - `GET .../instance-status`（实例状态分布）
  - `GET .../billing`（计费概览）
  - `GET .../top-agents`（热门 Agent 排行）

#### F-MGR-051 指标采样服务
- **描述**：定期采集 Pod 资源用量，时序存储。
- **组件**：Manager Backend — Metrics Service
- **实现**：`ResourceMetricSample`（cpu_m, memory_mi, 按分钟采样）；保留 7 天；按 instance_id 或 resource_pool_id 聚合；时间范围 1h/6h/24h/7d。

### 1.8 备份迁移与初始化

#### F-MGR-060 V3 数据迁移脚本
- **描述**：V2 → V3 三层模型迁移。
- **组件**：scripts/ — `migrate_to_v3.py`、`migrate_to_v3_data.py`
- **实现**：Agent→Definition+Version+Instance、EngineInstance→ResourcePool；分阶段：建表 → 迁数据 → 列重命名 + FK 改指 → DROP 老 V2 表。

#### F-MGR-061 种子数据初始化
- **描述**：启动时自动创建默认角色/权限/用户组/管理员。
- **组件**：Manager Backend — Seed Service
- **实现**：幂等；V3 三类资源权限种子；修复 startup seed greenlet bug；默认管理员 admin@unionagents.io / admin123。

### 1.9 系统集成

#### F-MGR-070 Controller 代理客户端
- **描述**：统一代理 controller 的 deploy/status/pods 等接口。
- **组件**：Manager Backend — Controller Client
- **实现**：统一 ControllerError；SSE 部署事件透传。

#### F-MGR-071 数据库架构（V3 三层模型）
- **描述**：定义/资源/实例三层分离的数据库设计。
- **组件**：Manager Backend — 数据模型
- **实现**：AgentDefinition / AgentVersion / ResourcePool / AgentInstance / AgentInstanceChannel / AgentDeployment / AgentProfile / ResourceMetricSample；支持组隔离与权限控制。

---

## 二、Controller Backend（已并入 services/manager/app/worker）

> **融合说明**：原 Repo2 独立 `services/controller` 服务（:8001）已并入 manager（`services/manager/app/worker/`，`worker_router` 在 `/api/controller/*` 字面路径提供服务）。下文 F-CTL-* 特性的实现均位于 `services/manager/app/worker/router.py` + `k8s_manager.py` + `client.py` + `background.py`；原 `services/controller/` 死代码目录已删除。

### 2.1 引擎 Pod 生命周期

#### F-CTL-001 Agent Deploy
- **描述**：创建/恢复引擎 Pod，支持 SUSPENDED/FAILED 状态恢复与 scope 维度部署。
- **组件**：Controller Backend
- **API**：`POST /api/controller/agents/{agent_id}/deploy`
- **实现**：创建 K8s Deployment + Service + PVC；自动扩容（现有 Pod 全满时新建）；preferred_node 节点亲和性优化镜像缓存。

#### F-CTL-002 Agent Status
- **描述**：查询引擎部署状态，含 K8s Pod 实际存活状态纠错。
- **API**：`GET /api/controller/agents/{agent_id}/status`
- **实现**：按需 reconciliation；自动修复陈旧状态（FAILED/PENDING/SUSPENDED→RUNNING）。

#### F-CTL-003 SUSPEND 空闲存档
- **描述**：30 分钟空闲自动存档到 MinIO，scale=0 释放资源。
- **API**：`POST /api/controller/agents/{agent_id}/suspend`
- **实现**：`exec tar → MinIO → scale=0 → SUSPENDED`；PVC 跳过机制 `pvc_skip_backup_on_suspend`；UserGroup 隔离路径 `groups/{group_code}/backups/`；不设定期轮询备份（大规模不可行）。

#### F-CTL-004 RESUME 恢复
- **描述**：SUSPENDED → RUNNING，从 MinIO 恢复数据。
- **API**：`POST /api/controller/agents/{agent_id}/resume`
- **实现**：Deployment scale 0→1；`exec untar` 恢复 backup；清理 stale gateway.lock 避免启动冲突。

#### F-CTL-005 DESTROY 归档销毁
- **描述**：确认 SUSPEND 存档 → 复制到 archives → 清理 K8s 资源。
- **API**：`POST /api/controller/agents/{agent_id}/destroy`
- **实现**：`archive_backup → delete_all_k8s → ARCHIVED`；PVC 回收控制 `pvc_reclaim_on_destroy`；原子清理 AgentProfile 记录。

#### F-CTL-006 RESTART 滚动重启
- **描述**：配置/技能/人设变更生效，不改变副本数。
- **API**：`POST /api/controller/agents/{agent_id}/restart`
- **实现**：修改 Deployment template annotations 触发滚动更新。

#### F-CTL-007 部署进度 SSE
- **描述**：SSE 流式返回部署进度。
- **API**：`GET /api/controller/agents/{agent_id}/deploy/events`
- **实现**：内存事件存储 `_deploy_events`；流式 JSON 事件推送。

### 2.2 数据持久化与存储

#### F-CTL-010 PVC 持久化
- **描述**：引擎数据 PVC 持久化，确保不丢失。
- **实现**：PVC 命名 `engine-data-{short_id[-scope_hash]}`；挂载 `/opt/data`（V2 多 profile 布局）；ReadWriteOnce；StorageClass 可配置；实时写零开销。

#### F-CTL-011 MinIO 存档管理
- **描述**：UserGroup 隔离的 MinIO 存档读写。
- **实现**：路径前缀 `groups/{group_code}/`；备份 `backups/{agent_id}/latest.tar.gz`；归档 `archives/{agent_id}/{timestamp}.tar.gz`；服务端复制优化。

#### F-CTL-012 数据备份/恢复（WebSocket exec）
- **描述**：通过 WebSocket 二进制通道备份/恢复。
- **实现**：`exec_tar_data`（tar→WebSocket→MinIO）；`exec_untar_data`（WebSocket→untar→Pod）；临时文件机制避免 stderr 混流。

### 2.3 多 Profile 隔离架构

#### F-CTL-020 Profile 生命周期
- **描述**：Hermes Profile 创建/删除/端口分配。
- **API**：`POST /api/controller/profiles`、`POST .../profiles/ensure`、`DELETE .../profiles/{profile_id}`
- **实现**：`hermes profile create --clone --clone-from base`；端口分配 `internal_port_map` JSON；`update_nginx_config` 动态生成并 reload。

#### F-CTL-021 Profile 修复（_heal_profile_runtime_config）
- **描述**：修复 PVC 持久化的 stale profile 配置（绕过 LiteLLM 直连问题）。
- **实现**：修复 provider auto→openai-api；清理 DEEPSEEK_API_KEY，注入 OPENAI_*；对齐当前 LiteLLM 配置。

#### F-CTL-022 Pod 共享调度（fan-out）
- **描述**：多 Agent 共享同一 Pod，按负载 fan-out 端口分配。
- **实现**：`_select_pod_by_load` 按负载选最空闲 Pod；`_ensure_pod_exists` 自动扩容；scope 维度隔离（scope_type + scope_target_id）；跨 agent 共享 Pod 走 `deployment.pod_name`。

#### F-CTL-023 V2 Profile 目录结构
- **描述**：`/opt/data/profiles/{name}/` 多 profile 布局。
- **实现**：base 目录 entrypoint 创建；新 profile 自动继承 base 配置。

#### F-CTL-024 Pod 启动注册
- **描述**：Pod 启动后主动上报 profile 列表。
- **API**：`POST /api/controller/profiles/register`
- **实现**：识别并删除 stale DB 记录；Engine entrypoint-v2.sh 调用。

### 2.4 K8s 交互

#### F-CTL-030 K8s 资源全生命周期
- **描述**：Deployment/Service/PVC 创建/删除/查询。
- **实现**：资源标签 `agent.unionagents/agent-id`；组隔离标签 `agent.unionagents/group-code`；节点亲和性调度。

#### F-CTL-031 Pod 状态监控
- **描述**：实时 Pod 状态查询与等待。
- **实现**：`get_pod_status` / `wait_pod_ready` / `wait_engine_ready`（等待引擎 HTTP 就绪）。

#### F-CTL-032 Pod Exec 权限（WebSocket）
- **描述**：二进制 WebSocket exec 通道。
- **实现**：`_ws_exec_sync` 同步执行；支持二进制传输；RBAC 需 get+create 两个 verb（Python SDK 限制，kubectl 不受限）。

#### F-CTL-033 Metrics 采样
- **描述**：周期性采样 CPU/内存用量。
- **实现**：`MetricSampler` 类，每 60s 采样；写入 `resource_metric_samples`；7 天保留；集成 metrics-server。

### 2.5 配置与人设/技能同步

#### F-CTL-040 引擎配置同步
- **描述**：配置同步到 MinIO 与运行中 Pod。
- **API**：`POST .../config/sync`、`POST .../config/apply`
- **实现**：MinIO 路径 `groups/{group_code}/engine-config/`；统一生成 config.yaml 避免 skills.disabled 被覆盖。

#### F-CTL-041 V3 三层配置读取
- **描述**：按 instance_id 读取三层配置。
- **实现**：`_load_instance_config` JOIN agent_instances + agent_versions + agent_definitions；per-instance litellm_config 覆盖版本快照。

#### F-CTL-042 人设同步（SOUL.md fan-out）
- **描述**：人设文件 fan-out 到所有引擎 Pod。
- **API**：`POST /api/controller/agents/{agent_id}/persona/sync`
- **实现**：自适应 V1/V2 目录；Hermes 按会话读取，写文件即生效。

#### F-CTL-043 技能安装/卸载/列表
- **描述**：技能文件管理，热生效不重启。
- **API**：`POST .../skills/install`、`DELETE .../skills/{skill_name}`、`POST .../skills/config/sync`、`GET .../skills/list`
- **实现**：zip→tar.gz 转换剥离顶层目录；路径安全过滤；重生成 config.yaml 更新 skills.disabled；递归查找 `**/SKILL.md` 解析 YAML frontmatter；统一扫描脚本 `/tmp/ua_scan_skills.py`；技能按 agent 隔离 + 软链接。

### 2.6 配额与后台调度

#### F-CTL-050 资源配额控制
- **描述**：CPU/内存资源限制与配额。
- **实现**：ResourcePool min/max_cpu、min/max_memory；K8s ResourceRequirements；max_profiles_per_pod 控制并发。

#### F-CTL-051 空闲回收调度器（RecycleScheduler）
- **描述**：定时检测空闲引擎并 SUSPEND/DESTROY。
- **实现**：每 5min 检查 RUNNING，30min 空闲 SUSPEND；每小时检查 SUSPENDED，24h 空闲 DESTROY；回调模式解耦。

#### F-CTL-052 状态巡检更新
- **描述**：更新 last_active_at，修正异常状态。
- **实现**：每 60s 执行；区分正常 SUSPEND 与外部误删；Profile 一致性检查。

### 2.7 其他

#### F-CTL-060 模型权限查询
- **描述**：按 Agent 虚拟 Key 返回可用模型。
- **API**：`GET /api/controller/agents/{agent_id}/models`
- **实现**：调用 LiteLLM `/v1/models`，返回 agent 有权限的模型组。

#### F-CTL-061 聊天仪表盘配置端点
- **描述**：前端探活配置。
- **API**：`GET /api/controller/chat/dashboard/config`、`.../status`、`GET /api/controller/chat/settings`、`GET /api/controller/chat/models`

#### F-CTL-062 服务解耦设计
- **描述**：原 Controller 与 Manager/Gateway 独立部署；融合后 Controller 已并入 manager（`services/manager/app/worker/`，进程内直调 facade 替代 HTTP 封装，见 `worker/__init__.py`）。无外键约束的同表不同约束模型、Controller 只写不读关联关系、K8s Service DNS 通信等设计仍沿用。

---

## 三、Gateway Backend（services/gateway）

### 3.1 反向代理与路由

#### F-GW-001 DNS-based Agent 智能路由
- **描述**：通过 `X-Agent-ID` 头 + DNS 命名规范构造 upstream URL，不查询 Controller。
- **组件**：Gateway Backend — Proxy 模块
- **实现**：URL `engine-hermes-{agent_id[:8]}-{scope_hash[:6]}.{namespace}.svc.cluster.local:8642`；`build_engine_url()` 传统 DNS 路由；`resolve_engine_url()` 支持 scope_hash pod_name 路由；Pod 重启检测缓存失效。

#### F-GW-002 Profile 感知路由
- **描述**：基于用户身份与 Agent 配置动态解析目标 Profile。
- **实现**：Profile 名 `{short_agent}-{scope_hash[:6]}-{short_user}`；INDEPENDENT/SHARED 两种类型；60s 成功缓存 + 10s 负缓存；IM 用户 ID 映射（`im_user_bindings`）；组隔离验证 + 平台管理员特权。

#### F-GW-003 安全头部过滤
- **描述**：过滤 Origin/Referer 头部（Hermes 收到 Origin 返回 403）。
- **实现**：忽略客户端 `X-Hermes-Profile` 头（服务端计算）；过滤 host/origin/referer/x-hermes-profile；注入 `X-Hermes-Profile` 与 `authorization: Bearer {api_server_key}`。

#### F-GW-004 Profile 路由 6 层问题链路修复
- **描述**：经多轮迭代的 Profile 路由系统。
- **实现**：①避免 Controller 查询直 DNS ②统一 Profile 名构造 ③缓存 Pod 重启检测 ④权限闸门前置无副作用 ⑤IM 用户 ID 统一映射 ⑥降级策略完善。

### 3.2 SSE 流式代理

#### F-GW-010 SSE 流式响应代理
- **描述**：服务器发送事件流式传输，实时 AI 响应。
- **实现**：`proxy_buffering off`；Content-Type `text/event-stream` 检测；`_stream()` 流式转发；OpenAI 兼容 SSE 解析；nginx 不得缓冲或修改 SSE 内容；`Connection upgrade` 会干扰 SSE 需避免。

#### F-GW-011 企业微信 chunk-flush
- **描述**：针对 2048 字节限制智能分段传输。
- **实现**：`_split_by_bytes()` UTF-8 字节级分段；优先换行处切分避免切断多字节字符；满 2048 字节立即 flush；`_stream_sent` 字符偏移跟踪。

#### F-GW-012 飞书流式编辑
- **描述**：PATCH 卡片消息实时编辑更新。
- **实现**：`send_initial_response()` 独立回复卡；`update_streaming_card()` 增量更新；双元素策略修复布局残留；启动状态卡 + 回复卡分离。

### 3.3 IM 渠道分发

#### F-GW-020 统一消息分发器
- **描述**：队列化处理 IM 消息，支持去重、生命周期管理。
- **实现**：消息去重 60s TTL + `(agent_id, platform_message_id)`；Per-agent 队列避免乱序；Session 30min TTL + 确定性 session ID；引擎重启清理 session 缓存。

#### F-GW-021 权限闸门（AccessDenied）
- **描述**：消息转发前权限验证，不可吞 AccessDenied 当 V1 fallback（越权）。
- **实现**：`check_access()` 轻量无副作用验证；类型 NotBound/AccessDenied/ProfileNotFound；IM 用户 ID 映射 + 组隔离；拒绝时返回明确 IM 提示。

#### F-GW-022 飞书适配器
- **描述**：飞书回调协议完整支持。
- **实现**：AES-256-CBC 加密消息；交互式卡片；PATCH API 实时编辑；Markdown 格式；HMAC-SHA256 签名验证。

#### F-GW-023 企业微信适配器
- **描述**：企业微信回调协议与加密。
- **实现**：SHA1 签名验证；AES-256-CBC 解密；2048 字节分段；Markdown 消息。

#### F-GW-024 钉钉适配器
- **描述**：钉钉回调协议。
- **实现**：URL 验证 checkUrl；HMAC-SHA256 签名；OAuth 2.0；无消息编辑支持。

### 3.4 引擎生命周期与 UX

#### F-GW-030 健康检查与自动恢复
- **描述**：自动检测引擎状态，支持冷启动恢复。
- **实现**：`check_engine_health()` HTTP GET /health；`trigger_deploy()` 30s 超时；`ensure_engine_ready()` 最长 300s 轮询；热/冷启动识别。

#### F-GW-031 启动进度 UX
- **描述**：智能体启动时发送状态提示。
- **实现**：冷启动发 "🤖 正在启动..." 占位；就绪后更新 "✅ 引擎已就绪"；飞书独立卡片 + 状态更新；企业微信仅发最终响应。

#### F-GW-032 重试与降级
- **描述**：消息转发失败重试与降级。
- **实现**：指数退避 3 次 [1s,2s,4s]；基础设施异常降级 legacy 路由；Profile 创建失败降级 V1；用户友好错误提示。

### 3.5 API 代理与会话

#### F-GW-040 模型 API 代理
- **描述**：OpenAI 兼容模型 API 代理到 Hermes 引擎。
- **实现**：`/v1/chat/completions`；模型配置从 `agent_instances.litellm_config` 读取；支持 stream 参数；`X-Hermes-Session-Id` 头转发。

#### F-GW-041 会话上下文管理
- **描述**：跨消息会话状态，连续对话体验。
- **实现**：确定性 session ID `SHA256(agent_id+channel_type+chat_id)[:24]`；POST /api/sessions（含 origin 元数据）；30min TTL + 引擎重启清理；409 视为正常重复。

### 3.6 配置与监控

#### F-GW-050 数据库配置缓存
- **描述**：DB 配置内存缓存减少查询。
- **实现**：60s TTL；`_invalidate_channel_config_cache()` 主动失效；渠道配置读 `agent_instance_channels`；Agent 模型配置读 `agent_instances.litellm_config`。

#### F-GW-051 安全配置
- **描述**：JWT 认证与 CORS。
- **实现**：JWT HS256；生产环境密钥强制验证；CORS 白名单；API Server 密钥认证。

#### F-GW-052 健康检查端点
- **描述**：`/health` 端点服务状态监控。
- **实现**：返回状态 + 版本；异步启动验证 DB 连接；日志输出 stderr（k8s 收集）毫秒级时间戳。

---

## 四、Admin Console（apps/admin，Vue3 + Element Plus）

### 4.1 V3 三层前端

#### F-ADM-001 智能体定义列表
- **描述**：网格卡片展示定义，搜索/状态筛选/引擎筛选/分页。
- **路由**：`/agent-definitions`
- **实现**：响应式网格（xs:24,sm:12,md:6,lg:6）；统计卡片（已发布/草稿）；引擎筛选 Hermes/OpenClaw。

#### F-ADM-002 智能体定义详情
- **描述**：3 Tab（人设 SOUL.md / 技能管理 / 版本管理），编辑/发布/删除。
- **路由**：`/agent-definitions/detail/:id`
- **实现**：头部卡片 + 引擎类型图标；下拉菜单更多操作；跳转关联实例/资源池；多步骤编辑表单。

#### F-ADM-003 智能体实例列表
- **描述**：实例生命周期管理，创建/克隆/发布/停用/删除。
- **路由**：`/agent-instances`
- **实现**：三状态统计（已上线/草稿/已停用）；引擎+状态双重筛选；状态 DRAFT→PUBLISHED→OFFLINE。

#### F-ADM-004 智能体实例详情（5 Tab）
- **描述**：概览/实例/监控/记忆/技能 5 Tab，运行时生命周期操作。
- **路由**：`/agent-instances/detail/:id`
- **实现**：双层状态（Manager 业务态 + Controller 部署态）；部署/暂停/恢复/重启/销毁操作；15s 轮询部署状态；Pod 重建跟踪。

#### F-ADM-005 资源池管理
- **描述**：资源池配置管理，克隆/删除。
- **路由**：`/resource-pools`
- **实现**：三维统计（总数/自动回收/手动管理）；卡片网格；搜索分页。

### 4.2 LiteLLM 模型网关管理

#### F-ADM-010 模型配置管理
- **描述**：配置 LLM 上游连接参数。
- **路由**：`/litellm/models`
- **实现**：多供应商（OpenAI/Anthropic/Azure/Gemini）；API Key 编辑可留空保持不变；自定义提供商。

#### F-ADM-011 API Key 管理
- **描述**：Key 权限/预算/速率限制管理。
- **路由**：`/litellm/keys`
- **实现**：Key 状态（正常/封禁）；智能关联解析（metadata.agent_id 或 key_alias）；用户组隔离；预算 max_budget+budget_duration；rpm/tpm；封禁/解封；用量统计。

#### F-ADM-012 用量统计
- **描述**：Token 用量与成本趋势。
- **路由**：`/litellm/spend`
- **实现**：ECharts 折线（趋势）/饼（用户组）/柱（模型）/柱（实例）；时间范围 + 用户组筛选；成本计算。

### 4.3 Dashboard 仪表盘

#### F-ADM-020 多角色仪表盘
- **描述**：根据角色展示不同视角运营数据。
- **路由**：`/welcome`
- **实现**：`<div class="main"><div class="welcome">` 双层容器，max-width 1400px；管理员左右分栏 md:17/md:7（73%/27%）；`.chart-card`+`.chart-fill` 自适应高度；ECharts 选项 `as any` 断言。
- **管理员视角**：概览数字卡片、系统健康监控、三大分布饼图（实例状态/引擎类型/运行状态）、6 快捷入口、底部四维监控（资源消耗/Token计费/热门Top5/最近动态时间线）。
- **组管理员视角**：组专属统计、实例状态进度条、快捷入口。
- **普通用户视角**：可访问实例数、个人对话统计、我的实例网格、7 天对话趋势。

### 4.4 系统管理

#### F-ADM-030 用户管理
- **描述**：用户 CRUD、角色分配、密码重置。
- **路由**：`/system/user/index`
- **实现**：表格 + 批量删除 + 密码重置 + 角色分配弹窗 + 状态筛选。

#### F-ADM-031 角色权限管理
- **描述**：角色 CRUD 与权限树配置。
- **路由**：`/system/role/index`
- **实现**：权限树形结构 + 搜索过滤 + 全选/展开联动；响应式可折叠。

#### F-ADM-032 用户组管理
- **描述**：用户组 CRUD 与成员管理。
- **路由**：`/system/user-group/index`
- **实现**：弹窗式成员编辑。

### 4.5 国际化与配置

#### F-ADM-040 i18n 国际化
- **描述**：中英文双语界面。
- **实现**：Vue i18n + Element Plus 本地化；YAML 语言文件；`import.meta.glob` 服务器启动缓存（改 yaml 需重启 Vite）；`$t` 为占位符（i18n Ally 提示），真实翻译在 `transformI18n`；`flatI18n` 缓存有 bug 已绕过。

#### F-ADM-041 版本检测
- **描述**：构建时生成 version.json 消除 version-rocket 轮询报错。

### 4.6 样式与技术约束

#### F-ADM-050 图标渲染约束
- **描述**：禁止将图标字符串直传 `IconifyIconOffline`，必须 `import Chat1Line from "~icons/ri/chat-1-line"`；JSX 中用 `{...({width:"18"} as any)}`。

#### F-ADM-051 页面布局约束
- **描述**：列表/内容页用 `<div class="main">` 容器；按钮左筛选右；搜索框 width 260px + suffix 图标 v-show 控制；筛选下拉在前搜索在后。

#### F-ADM-052 技术栈
- **实现**：Vue3 + TS + Element Plus + Vite + Pinia + Vue Router 4 + ECharts；RePureTableBar/ReIcon/ReDialog/ReCountTo/ReECharts 组件库；Tailwind + SCSS + 暗色主题 + 响应式；RBAC 动态路由 + 按钮权限 + 用户组隔离。

---

## 五、Enduser Portal（apps/enduser，Vue3 + Tailwind）

### 5.1 认证与智能体发现

#### F-END-001 JWT 认证
- **描述**：双 Token（access/refresh）+ LocalStorage 持久化 + 自动会话恢复 + 401 跳登录。
- **组件**：Auth Store `/stores/auth.ts`

#### F-END-002 路由守卫
- **描述**：`meta.requiresAuth` 权限控制，未认证重定向 `/login`。
- **组件**：Router Guard

#### F-END-003 可访问智能体列表
- **描述**：获取用户有权访问的实例列表。
- **API**：`GET /api/manager/agent-instances/accessible`
- **实现**：支持 HERMES/OPENCLAW 引擎类型；含名称/描述/引擎信息。

#### F-END-004 智能体部署与进度
- **描述**：自动部署引擎，SSE 进度追踪。
- **实现**：EventSource 监控；步骤 准备→创建Pod→配置→等待就绪→验证→完成；防误判（防 EventSource 默认 error 误判）；支持重试。

#### F-END-005 引擎健康监控
- **描述**：503 自动检测，不可用提示横幅，自动触发重新部署。

### 5.2 会话管理

#### F-END-010 多会话管理
- **描述**：创建/切换/删除多个对话会话。
- **实现**：会话存引擎本地（不入 Manager DB）；`POST /api/gateway/api/sessions`；按时间倒序；搜索 + 日期分组（今天/昨天/本周/上周）。

#### F-END-011 智能标题生成
- **描述**：启发式从首条用户消息截取标题，避免 LLM 生成多余记录；支持内联重命名。
- **API**：`PATCH /api/gateway/api/sessions/{id}`

#### F-END-012 会话持久化
- **描述**：LocalStorage 缓存 + 消息懒加载 + JSON 导入导出。

### 5.3 消息处理

#### F-END-020 SSE 流式消息
- **描述**：ReadableStream + TextDecoder 解析，AbortController 中断，实时渲染。
- **API**：`POST /api/gateway/v1/chat/completions`

#### F-END-021 Markdown 渲染
- **描述**：`streaming-markdown` 库，表格/代码块/链接，自动+手动滚动控制，时间戳。
- **组件**：ChatMessages.vue

#### F-END-022 工具调用追踪
- **描述**：实时显示工具状态（waiting/running/done）+ 活动事件分类 + 工具标签智能识别（搜索/读取/写入/命令）+ 可折叠面板。
- **组件**：ToolCard + Activity Events

### 5.4 工作区

#### F-END-030 文件系统浏览器
- **描述**：树形文件结构 + 大小格式化 + 展开折叠。
- **API**：`GET /api/gateway/v1/files`
- **组件**：ChatFileBrowser.vue

#### F-END-031 多工作区切换
- **描述**：工作区列表动态获取 + 状态保持 + 切换刷新。

#### F-END-032 文件附件上传
- **描述**：多文件选择 + 附件标签 + 移除。
- **组件**：ChatComposer.vue

### 5.5 用户界面

#### F-END-040 Rail 导航系统
- **描述**：左侧 rail 导航 9 面板（对话/任务/看板/技能/记忆/工作区/配置/任务/洞察），响应式。
- **组件**：ChatPage.vue

#### F-END-041 会话列表界面
- **描述**：日期分组 + 搜索 + 批量选择删除 + 右键菜单 + 内联重命名。
- **组件**：ChatSessionList.vue

#### F-END-042 智能输入框
- **描述**：自动高度 + Enter/Shift+Enter 发送 + 模型选择下拉 + 附件按钮 + 发送/停止状态。
- **组件**：ChatComposer.vue

### 5.6 模型管理

#### F-END-050 动态模型加载
- **描述**：优先从 Controller 获取 Agent 配置模型，回退引擎 `/v1/models`。
- **实现**：模型选择切换 + 默认模型同步；`bareModel` 提取 provider/model_name 中的纯模型名。

### 5.7 网络通信

#### F-END-060 API 客户端
- **描述**：统一 HTTP 客户端，自动 JWT 头 + 401 处理跳转 + 统一错误。
- **组件**：`api/client.ts`

#### F-END-061 Gateway 代理通信
- **描述**：通过 Gateway 转发到引擎，base `/api/gateway`，自动 `X-Agent-ID` + `X-Engine-Type` + `X-Session-ID` 头。

#### F-END-062 Nginx 代理配置
- **描述**：`/api/manager/`→manager:8002、`/api/controller/`→manager:8002（controller 已并入 manager，worker_router 在 `/api/controller/*` 字面路径提供服务）、`/api/gateway/`→gateway:8010（剥离前缀）；SSE 长连接优化；`/api/manager/` 通配修复 k3s 直连 pod 时 /accessible 404。

### 5.8 设置与体验

#### F-END-070 主题与字体
- **描述**：浅色/深色/系统主题 + 四档字体大小 + LocalStorage 持久化 + 实时预览。

#### F-END-071 面板记忆
- **描述**：LocalStorage 记忆工作区面板开关 + 可调大小。

#### F-END-072 会话导出导入
- **描述**：Markdown/JSON 导出 + JSON 导入验证。

#### F-END-073 滚动优化
- **描述**：距底部 150px 内自动滚动 + 阅读时暂停 + 手动回到底部按钮 + 平滑动画。

#### F-END-074 移动端适配
- **描述**：移动端专用侧边栏 + 触摸友好 + 自适应布局。

---

## 六、Engine Integration（引擎集成）

#### F-ENG-001 Hermes 引擎容器化
- **描述**：Docker 基础镜像 + nginx 多 Profile 路由，容器化部署于 k3s Pod，通过原生 HTTP API 调用，不侵入式修改源码。
- **实现**：暴露 OpenAI 兼容接口 `/v1/chat/completions`；多 Profile 每实例一个；PVC 持久化 `/opt/data/profiles/{name}`；端口 8642。

#### F-ENG-002 引擎运行时强契约
- **描述**：`ENGINE_RUNTIMES` 常量定义引擎类型（HERMES/OPENCLAW），新增引擎必须改代码（非数据驱动）；统一端口 8642；镜像环境变量可覆盖。

#### F-ENG-003 引擎生命周期状态机
- **描述**：`PENDING → DEPLOYING → RUNNING ↔ SUSPENDED → ARCHIVED`，含 FAILED 分支。

#### F-ENG-004 DNS 命名规范路由
- **描述**：`engine-hermes-{instance_id[:8]}.{namespace}.svc.cluster.local:8642`，Gateway 与 Controller 通过命名约定解耦，无运行时依赖。

---

## 七、Deploy（部署架构）

#### F-DEP-001 k3s 部署
- **描述**：单 namespace `unionagents`；双域名 Ingress（admin/chat）；Controller 按实例动态创建 Deployment+Service+PVC；本地用 colima + k3s。
- **路径**：`deploy/k8s/`、`deploy/k8s/infra/`

#### F-DEP-002 基础设施组件
- **描述**：PostgreSQL 16（StatefulSet+PVC，unionagents+litellm 两库）、MinIO（对象存储归档）、LiteLLM Proxy（模型网关）、Traefik Ingress（TLS+Let's Encrypt）。

#### F-DEP-003 服务端口规划
- **描述**：见整体架构表（PostgreSQL 5432 / MinIO 9000-9001 / LiteLLM 4000 / Hermes 8642 / Manager 8002（含 controller worker）/ Gateway 8010 / Admin 8848 / Portal 3000）。

#### F-DEP-004 容器镜像构建
- **描述**：Gitee Go → 容器镜像仓库；生产 Always / 开发 IfNotPresent；amd64；Dockerfile.local 宿主预构建 dist 绕过 pnpm 11 容器内 build 硬错。

#### F-DEP-005 安全配置
- **描述**：`unionagents-secret` 统一凭据（仅本地 k3s，占位符）；ServiceAccount+Role+RoleBinding RBAC；TLS 自动签发；敏感信息只走 env/k8s Secret/.env.local（已 gitignore）。

---

## 八、Scripts（运维脚本）

#### F-SCR-001 V3 数据迁移脚本
- **描述**：建表 + 数据迁移 + 列重命名 + DROP 老 V2 表。
- **路径**：`scripts/migrate_to_v3.py`、`migrate_to_v3_data.py`

#### F-SCR-002 端口转发脚本
- **描述**：一键转发本地开发所有 k8s 服务（3001→portal / 8010→gateway / 8002→manager（含 controller worker））。
- **路径**：`scripts/port-forwards.sh`

#### F-SCR-003 版本管理脚本
- **描述**：`bump-version.sh` 语义化版本更新 + `VERSION` 文件。

#### F-SCR-004 种子数据脚本
- **描述**：`seed.py` 管理员初始化 + `seed_test_users.py` 测试用户 + `migrate_im_user_bindings.sql` IM 绑定迁移。

#### F-SCR-005 调试测试脚本
- **描述**：`im_test_simulator.py` IM 模拟器；testcontainers 集成测试；E2E 端到端生命周期测试。

---

## 九、公共包（pkg/）

#### F-PKG-001 统一配置
- **描述**：`pkg/common/config.py` 全局配置 + ENGINE_RUNTIMES 常量；dev/prod 区分；环境变量覆盖。

#### F-PKG-002 共享数据模型
- **描述**：`pkg/models/` 跨服务共享模型；Controller 用无外键约束版本避免循环依赖；AgentDeployment / AgentProfile / ResourceMetricSample 等。

#### F-PKG-003 异步数据库连接
- **描述**：`pkg/common/database.py` SQLAlchemy async 引擎 + 连接池。

---

## 十、文档体系（docs/）

#### F-DOC-001 架构文档
- **描述**：`docs/architecture-v3-src/` V3 架构图（md + 交互式 HTML + .mmd + gen.py）；ER 关系图；运行时序图；RBAC 权限矩阵；旧 V2 文档标注 superseded。

#### F-DOC-002 功能特性文档
- **描述**：`docs/features/` overview / hermes-engine / enduser-portal / gateway / im-channels。

#### F-DOC-003 部署与变更
- **描述**：`docs/deployment/` 部署指南；`docs/changelog/` 变更日志；`docs/brand/` 品牌视觉规范。

---

## 十一、关键技术约束（复刻必须遵守）

| 约束 | 说明 |
|------|------|
| Gateway 反向依赖禁止 | Gateway 不得查询 Controller 获取 upstream，仅靠 `X-Agent-ID` + DNS 命名 |
| SSE 与 nginx | `proxy_buffering off`；`Connection upgrade` 会干扰 SSE；浏览器 ReadableStream+TextDecoder 解析；nginx 不得缓冲/修改 SSE |
| iframe 禁止 | 终端门户不 iframe 嵌 hermes-webui，直接渲染 Vue3 组件 |
| Gateway Origin 过滤 | 转发前去掉 Origin/Referer（Hermes 收 Origin 返 403） |
| 存档策略 | 存档提前到 SUSPEND（30min 空闲）；不定期轮询备份；PVC 实时写；DESTROY 仅清 K8s 资源 |
| 会话不入 Manager DB | 聊天会话由引擎自身管理 |
| UserGroup 隔离 | 最小隔离单元，不引入 tenant；资源表 group_id；管理员旁路 |
| LiteLLM 唯一出口 | 引擎只走 LiteLLM；UserGroup=Team；每 Agent 一 key；计费 Team 由 access 派生 |
| 开源软件不侵入 | 只用扩展能力 + 云化加固，引擎容器化通过原生 HTTP API 调用 |

---

## 复刻核对清单（按组件统计）

| 组件 | 特性数 |
|------|--------|
| Manager Backend | 30（F-MGR-001 ~ F-MGR-071） |
| Controller Backend | 28（F-CTL-001 ~ F-CTL-062） |
| Gateway Backend | 20（F-GW-001 ~ F-GW-052） |
| Admin Console | 22（F-ADM-001 ~ F-ADM-052） |
| Enduser Portal | 27（F-END-001 ~ F-END-074） |
| Engine Integration | 4（F-ENG-001 ~ F-ENG-004） |
| Deploy | 5（F-DEP-001 ~ F-DEP-005） |
| Scripts | 5（F-SCR-001 ~ F-SCR-005） |
| 公共包 | 3（F-PKG-001 ~ F-PKG-003） |
| 文档 | 3（F-DOC-001 ~ F-DOC-003） |
| **合计** | **147 项特性** |

> 复刻时建议按「定义层 → 资源层 → 实例层 → 引擎生命周期 → 模型网关 → IM 渠道 → 权限隔离 → 仪表盘 → 终端门户 → 部署运维」顺序推进，每完成一项核对编号打勾。
