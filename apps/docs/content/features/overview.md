# UnionAgents (知行) — 功能概述

> 企业级多智能体平台。

## 已实现功能

### 1. RBAC 权限系统
- **用户-角色-权限** 三层模型（多对多关系）
- 预置三个角色：`system_admin`、`operator`、`end_user`
- 14 个预置权限（页面级 + 操作级）
- JWT 双 Token 认证（access 30min + refresh 7d）
- 默认管理员账号：`admin@unionagents.io` / `admin123`

### 2. Agent 管理 (Manager API)
- Agent CRUD（创建、编辑、删除、查询）
- 引擎类型选择：Hermes（可扩展 OpenClaw 等）
- 状态流转：DRAFT → PUBLISHED → OFFLINE
- 访问范围控制：ALL / USER / USER_GROUP
- 终端用户可用智能体列表 (`GET /api/agents/accessible`)
- 访问记录追踪 (`POST /api/agents/{id}/access`)

### 3. 终端用户门户 (Enduser Portal)
- **技术栈**：Vue 3 + TypeScript + Vite + Tailwind CSS + Pinia
- **页面**：登录、智能体列表（按最近访问排序）、聊天（部署进度 + 对话）
- **聊天组件**：直接渲染 Vue 3 组件（无 iframe）
- **对话流程**：
  1. 用户选择智能体 → Controller 检测引擎状态
  2. 未部署 → 创建 K8s Pod + SSE 进度推送 → 就绪
  3. 已休眠 → 恢复 (scale=1)
  4. 就绪 → 进入 Chat 页面
  5. 发送消息 → Gateway → engine Pod → SSE 流式回复
- **会话管理**：Controller API + PostgreSQL (`chat_sessions` 表)
- **Gateway 通信**：`/api/gateway/` 同域代理，`X-Agent-ID` 头路由

### 4. 智能体生命周期 (Controller API)
- **K8s 引擎管理**：通过 kubernetes Python client 创建/管理 Pod
  - 命名规范：`engine-hermes-{agent_id[:8]}`（Gateway DNS 路由依据）
- **部署状态**：PENDING → DEPLOYING → RUNNING → SUSPENDED → ARCHIVED
- **SSE 部署进度**：实时推送创建 Pod → 等待就绪 → 完成
- **数据存档**：SUSPEND 时 exec tar 引擎数据 → MinIO 备份
- **空闲回收**：30min 空闲 → 存档+休眠 (scale=0)；24h 空闲 → 清理 K8s 资源
- **后端 API 会话管理**：创建/列表/详情

### 5. Hermes 引擎容器化
- Hermes Agent API Server 模式
- DeepSeek v4 Flash 模型集成
- 支持流式 (SSE) 和非流式响应
- 容器内自动配置 provider/model/api_key（从 Agent config 读取）
- Gateway 模式：去掉透传的 Origin/Referer 头

### 6. Gateway 动态路由
- DNS 命名规范路由：`engine-hermes-{agent_id[:8]}.unionagents.svc.cluster.local:8642`
- 从 `X-Agent-ID` 请求头提取 agent_id，无需调用外部服务
- SSE 流式透传
- 503 引擎不可用处理
- 无反向依赖（Gateway 不依赖 Controller）

### 7. k3s 基础设施
- PostgreSQL 16 (StatefulSet + PVC)
- MinIO 对象存储 (StatefulSet + PVC)
- Controller RBAC：ServiceAccount + Role + RoleBinding
- 所有服务 ClusterIP 内网通信
- `imagePullPolicy: IfNotPresent`（本地镜像优先）

## 端口规划

| 分组 | 服务 | K8s 端口 | 本地开发 |
|------|------|---------|---------|
| 基础设施 | PostgreSQL | 5432 | 5432 |
| | MinIO API | 9000 | 9000 |
| 引擎 | Hermes Engine | 8642 | — |
| 后端 | Manager（含 controller worker） | 8002 | 8002 |
| | Gateway | 8010 | 8010 |
| 前端 | Admin (Vite) | — | 8848 |
| | Enduser Portal (Vite) | — | 3000 |

## 数据库模型

| 模型 | 表名 | 说明 |
|------|------|------|
| User | users | 用户账号 |
| Role | roles | 角色 |
| Permission | permissions | 权限 |
| Agent | agents | AI 智能体 |
| UserGroup | user_groups | 用户组 |
| AgentSession | agent_sessions | 用户访问记录 |
| AgentDeployment | agent_deployments | 部署状态 |
| ChatSession | chat_sessions | 聊天会话 |

## 架构约束

### Gateway 反向依赖
- Gateway **不允许**查询 Controller 或其他服务获取 upstream
- 路由信息通过请求头 `X-Agent-ID` + DNS 命名规范传递
- 引擎 Pod 命名规范是 Controller 和 Gateway 之间的约定契约

### 开源软件修改策略
- 不对开源软件做侵入式修改
- hermes-webui 仅作为参考实现，终端用户前端为自研 Vue 3 应用

### 数据存档策略
- 存档时机提前到 SUSPEND（30min），不在 DESTROY 时
- 不加定期轮询备份（避免大规模下的资源消耗）
- PVC 实时写（引擎自身行为，零开销）
- SUSPEND 存档 → DESTROY 仅清理 K8s 资源
