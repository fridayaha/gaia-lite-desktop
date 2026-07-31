# UnionAgents（知行）V3 架构图

> 基于 2026-06-23 完成的 V3 三层重构（智能体开发 / 运行资源管理 / 智能体实例 三层分离）整理。

事实来源：
- 数据模型：`services/manager/app/models`（`agent_definitions` / `agent_versions` / `resource_pools` / `agent_instances` + `agent_instance_channels` / `agent_instance_user_access` / `agent_instance_group_access` / `agent_deployments` / `agent_profiles`）
- 引擎运行时映射：`pkg/common/config.py` 的 `ENGINE_RUNTIMES`
- 编排：`services/controller/app/main.py`（`_load_instance_config` / `_load_resource_spec` / `deploy` / `suspend` / `resume` / `restart` / `destroy`）
- 网关：`services/gateway` 的 `profile_resolver` + dispatcher + wecom 适配器
- 终端门户：`apps/enduser/src`（`useChat` composable）

---

## 1. 系统整体架构图

```mermaid
flowchart TB
    subgraph 接入[用户接入层]
        Portal[Enduser Portal<br/>Vue3 + Tailwind · Chat 直渲染]
        Admin[管理台 Admin<br/>Vue3 + Element Plus]
        IM[IM 平台<br/>企业微信 / 飞书]
    end

    subgraph 服务[应用服务层]
        Manager[Manager · FastAPI<br/>JWT/RBAC · V3 三层 CRUD + 运行时代理]
        Gateway[Gateway<br/>路由/鉴权/Profile 解析/SSE]
    end

    subgraph 编排[运行编排层]
        Controller[Controller<br/>deploy/suspend/resume/restart/destroy<br/>metric_sampler 1min]
    end

    subgraph 引擎[引擎运行层 · K8s]
        Engine["engine-hermes-{id} Pod<br/>Hermes · 多 Profile · PVC"]
    end

    subgraph 模型[模型网关层]
        LiteLLM[LiteLLM Proxy<br/>per-instance key · 计费 Team=UserGroup]
        Up[上游 LLM<br/>DeepSeek / GLM]
    end

    subgraph 基础[数据与基础设施]
        PG[(PostgreSQL 16)]
        MinIO[(MinIO)]
        K8s[(K8s/k3s API + metrics-server)]
        LiteDB[(LiteLLM DB)]
    end

    Portal -->|X-Agent-ID / SSE| Gateway
    IM -->|webhook 回调| Gateway
    Admin -->|REST| Manager

    Manager -->|controller_client 代理运行时| Controller
    Manager --> PG
    Manager -->|Admin API 生成 key| LiteLLM
    Manager --> MinIO

    Gateway -->|ensure_profile / DNS 命名| Engine
    Gateway --> PG
    Gateway -->|ensure_engine_ready| Controller

    Controller -->|apply/scale/delete| K8s
    Controller --> PG
    Controller --> MinIO

    Engine -->|/v1/chat/completions| LiteLLM
    Engine -->|PVC + SUSPEND 归档| MinIO
    Engine -->|会话/记忆| PG

    LiteLLM --> Up
    LiteLLM --> LiteDB
    K8s -. 调度 .-> Engine
```

**要点**：Gateway 反向依赖解耦——不查 Controller 取 upstream，靠 `X-Agent-ID` + DNS 命名规范（`{pod_name}.{ns}.svc.cluster.local:port`）直连引擎；Controller 只管 Pod 生命周期与采样。引擎全部经 LiteLLM 调上游，per-instance key 保证用量精确归因到计费 Team（Team = UserGroup）。

---

## 2. 逻辑架构（V3 三层 + 横切）

```mermaid
flowchart LR
    subgraph 开发层[① 智能体开发层]
        Def["AgentDefinition<br/>name / engine_type 枚举<br/>persona·model·skill·memory config(草稿)<br/>status: DRAFT/PUBLISHED"]
        Ver["AgentVersion<br/>不可变快照 · version_no"]
        Def -->|发布生成 1:N| Ver
        Ver -. current_version_id .-> Def
    end

    subgraph 资源层[② 运行资源管理层]
        Pool["ResourcePool<br/>min/max cpu·mem · replicas<br/>max_sessions_per_pod<br/>idle_suspend_minutes / idle_destroy_hours"]
    end

    subgraph 实例层[③ 智能体实例层]
        Inst["AgentInstance<br/>definition × version × resource_pool<br/>access_scope: ALL/USER/USER_GROUP<br/>status: DRAFT/PUBLISHED/OFFLINE<br/>litellm_config (per-instance key)"]
        Chan["AgentInstanceChannel<br/>http / wecom / feishu"]
        Dep["AgentDeployment<br/>引擎部署状态 · scope"]
        Prof["AgentProfile<br/>单用户/组 Profile 映射"]
        Inst --> Chan
        Inst --> Dep
        Inst --> Prof
    end

    Def -->|实例化 1:N| Inst
    Pool -->|供给规格 1:N| Inst
    Ver -->|绑定可回滚 1:N| Inst

    subgraph 横切[横切关注点]
        RBAC[RBAC<br/>definition / pool / instance 三类资源]
        Bill["LiteLLM 计费<br/>Team = UserGroup"]
        Met["Metrics<br/>spend_logs + metrics-server"]
    end

    Inst -. 计费 .- Bill
    Inst -. 权限 .- RBAC
    Inst -. 监控 .- Met
```

**要点**：
- 引擎类型**不建表**——`engine_type` 作枚举放 definition，镜像/端口走 `ENGINE_RUNTIMES` 常量（`HERMES`→`unionagents/engine-hermes:latest`/8642，`OPENCLAW`→同端口）。理由：加引擎必须改代码，是强契约，建表会误导。
- 「发布」语义拆分：定义「发布版本」(生成快照) ≠ 实例「上线」(DRAFT→PUBLISHED)。实例绑定 `version_id` 支持回滚。
- 技能挂定义层，install/sync/uninstall fan-out 到定义各实例；access_scope 在实例层决定谁能用，渠道层不再重复控制权限。
- 运行时操作全归实例详情页：deploy / suspend / resume / restart / destroy；定义层只管 Manager 操作（发布/下线/编辑）。

---

## 3. 顶层模型关系图（ER + 企业微信引用链）

### 3a. ER 关系（1:N / N:N / 引用）

```mermaid
erDiagram
    AgentDefinition ||--o{ AgentVersion : "发布快照 1:N"
    AgentDefinition ||--o{ AgentInstance : "实例化 1:N"
    AgentVersion    ||--o{ AgentInstance : "绑定(可回滚) 1:N"
    ResourcePool    ||--o{ AgentInstance : "供给规格 1:N"
    ResourcePool    ||--o{ AgentDeployment : "1:N"
    ResourcePool    ||--o{ AgentProfile : "1:N"

    AgentInstance ||--o{ AgentInstanceChannel : "渠道 1:N"
    AgentInstance ||--o{ AgentDeployment : "部署 1:N"
    AgentInstance ||--o{ AgentProfile : "Profile 1:N"
    AgentDeployment ||--o{ AgentProfile : "1:N"

    AgentInstance }o--o{ User : "agent_instance_user_access  N:N"
    AgentInstance }o--o{ UserGroup : "agent_instance_group_access N:N"
    User }o--o{ UserGroup : "成员 N:N"

    AgentDefinition {
        uuid id PK
        string name
        enum engine_type "HERMES OPENCLAW"
        uuid current_version_id FK
        json persona_config
        json model_config
        json skill_config
    }
    AgentInstance {
        uuid id PK
        uuid definition_id FK
        uuid version_id FK
        uuid resource_pool_id FK
        enum access_scope "ALL USER USER_GROUP"
        enum status "DRAFT PUBLISHED OFFLINE"
        json litellm_config "per-instance key"
    }
    ResourcePool {
        uuid id PK
        string max_cpu
        int max_replicas
        int max_sessions_per_pod
        int idle_suspend_minutes
    }
    AgentInstanceChannel {
        uuid id PK
        uuid instance_id FK
        string channel_type "http wecom feishu"
        json config
    }
    AgentProfile {
        uuid id PK
        uuid instance_id FK
        uuid deployment_id FK
        uuid user_id FK
        uuid group_id FK
        string profile_name
        string hermes_home
    }
```

### 3b. 以企业微信为例的引用链

> 题目要求的链路：企业微信应用 → 智能体实例 → 智能体定义 → 运行资源 → 单用户 Profile

```mermaid
flowchart LR
    WeCom["企业微信应用<br/>AgentInstanceChannel<br/>channel_type=wecom<br/>corp_id / agent_id / secret"]
    Inst["智能体实例<br/>AgentInstance<br/>access_scope"]
    Def["智能体定义<br/>AgentDefinition<br/>engine_type=HERMES"]
    Ver["版本快照<br/>AgentVersion"]
    Pool["运行资源<br/>ResourcePool"]
    Dep["部署<br/>AgentDeployment<br/>pod_name / engine_url"]
    Prof["单用户 Profile<br/>AgentProfile<br/>user_id → hermes_home"]
    User[(User)]

    WeCom -->|instance_id FK| Inst
    Inst -->|definition_id FK| Def
    Inst -->|version_id FK| Ver
    Def -. current_version_id .-> Ver
    Inst -->|resource_pool_id FK| Pool
    Inst -->|1:N scope 部署| Dep
    Dep -->|1:N| Prof
    Prof -->|user_id FK| User
```

**引用链语义**：企业微信应用 → 实例（渠道 FK）→ 定义（实例化来源）+ 版本（运行配置快照，可回滚）→ 资源池（规格供给）→ 部署（引擎 Pod 实例）→ 单用户 Profile（Pod 内隔离会话空间）。`access_scope` 在实例层决定谁能用，渠道层只做存在性检查、不再重复控制权限。

---

## 4. 运行时序图（最终用户视角入口）

### 4a. Web Portal 聊天入口（主路径）

```mermaid
sequenceDiagram
    autonumber
    actor U as 终端用户
    participant Portal as Enduser Portal
    participant GW as Gateway
    participant DB as PostgreSQL
    participant Ctrl as Controller
    participant K8s as K8s API
    participant Engine as Engine Pod
    participant Lite as LiteLLM

    U->>Portal: 选择智能体(实例)进入 Chat
    Portal->>GW: GET /api/agents/{instanceId}<br/>X-Agent-ID
    GW->>DB: list_accessible_instances(access_scope 过滤)
    DB-->>GW: 实例信息
    GW-->>Portal: 实例列表/详情

    U->>Portal: 发送消息
    Portal->>GW: POST /api/sessions (X-Agent-ID)
    GW->>DB: ProfileResolver.resolve<br/>①access 校验 ②派生scope ③profile_name
    GW->>Ctrl: ensure_profile(部署就绪?)
    alt Pod 未运行
        Ctrl->>K8s: deploy / scale=1
        K8s-->>Ctrl: Pod Ready
    end
    Ctrl-->>GW: engine_url (pod DNS)
    GW->>Engine: 创建会话
    Engine-->>GW: session_id
    GW-->>Portal: session_id

    Portal->>GW: POST /v1/chat/completions (stream)<br/>X-Agent-ID + X-Session-Id
    GW->>Engine: 透传(SSE, 去 Origin/Referer 头)
    Engine->>Lite: /v1/chat/completions (per-instance key)
    Lite->>Lite: 按 key 归因 Team 计费
    Lite-->>Engine: 流式 token
    Engine-->>GW: SSE 流
    GW-->>Portal: SSE (ReadableStream 解析)
    Portal-->>U: 逐字渲染
```

### 4b. IM 渠道入口（企业微信 inbound）

> 📌 **完整 IM/Channel 对接文档**已拆分到 [`docs/architecture-im-channels.md`](./architecture-im-channels.md)（HTML 版 [`architecture-im-channels.html`](./architecture-im-channels.html)，含模型字段表、入站/出站时序图、adapter 差异表、v0.8.19 端口单源真相）。下面保留简化时序图作总览，细节以那份为准。
>
> 关键更新（vs 下方旧图）：webhook 路径为 `/api/gateway/channel/{type}/{agent_id}/callback`；`ensure_engine_ready` 走 DNS 命名规范（不查 Controller）；profile 解析含 IM 用户绑定转换（`im_user_bindings`）；引擎路由经 Pod 内 nginx `X-Hermes-Profile` 头 → per-profile 端口（`port_map.json` 唯一真相，v0.8.19）。

```mermaid
sequenceDiagram
    autonumber
    participant WeCom as 企业微信
    participant GW as Gateway dispatcher
    participant DB as PostgreSQL
    participant Ctrl as Controller
    participant Engine as Engine Pod
    participant Lite as LiteLLM

    WeCom->>GW: POST /webhook/wecom (加密 XML)
    GW->>GW: WeComAdapter.parse_incoming<br/>解密 → MessageEvent
    GW->>DB: 消息去重 (MsgId)
    GW->>DB: _check_im_access 权限闸门<br/>用户绑定 → instance access_scope
    GW->>Ctrl: ensure_engine_ready(实例运行?)
    Ctrl-->>GW: engine_url
    GW->>Engine: 创建/恢复 session (Profile)
    GW->>DB: 加载 litellm_config (per-instance key)
    GW->>Engine: 转发消息
    Engine->>Lite: per-instance key 调用
    Lite-->>Engine: 回复
    Engine-->>GW: 响应
    GW->>WeCom: WeComAdapter.send_message<br/>分段 ≤ 2048 字节
```

**要点对比**：Web 入口由 Portal 主动建会话、Gateway 透传 SSE（nginx 必须 `proxy_buffering off`，且去掉 `Origin`/`Referer` 头避免引擎 403）；IM 入口由 dispatcher 被动收回调，做去重 + 权限闸门 + ensure 引擎就绪后转发，响应需按企业微信消息长度（≤2048 字节）分段回发。两条路径最终都落到「Gateway → 引擎 Pod → LiteLLM → 上游」同一模型调用链，per-instance key 保证计费归因。

---

## 5. k3s 部署拓扑图

单 namespace `unionagents`，所有组件同 ns。Ingress 双域名分流管理台与聊天门户；引擎 Pod 由 Controller 动态创建为 Deployment + 同名 Service + PVC。

```mermaid
flowchart TB
    subgraph Ingress[Ingress · 双域名]
        AdminIng["admin.__DOMAIN__"]
        ChatIng["chat.__DOMAIN__"]
    end

    subgraph NS["namespace: unionagents"]
        subgraph 前端[前端静态站]
            AdminWeb["console-admin :80"]
            PortalWeb["enduser-portal :80"]
        end

        subgraph 后端[微服务]
            Manager["manager :8002<br/>unionagents/manager"]
            Gateway["gateway :8010<br/>unionagents/gateway"]
            Controller["controller :8001<br/>unionagents/controller"]
            LiteLLM["litellm :4000<br/>litellm-database"]
        end

        subgraph 引擎[引擎 · 动态 Deployment]
            Eng1["engine-hermes-{id8}[-scope]<br/>:8642 · Hermes"]
            EngN["engine-hermes-...<br/>多实例"]
        end

        subgraph 基础设施[基础设施]
            PG["postgres :5432<br/>StatefulSet + PVC 10Gi"]
            MinIO["minio :9000/:9001<br/>StatefulSet + PVC 20Gi"]
        end

        Secret["Secret: unionagents-secret<br/>pg/litellm/minio/jwt/api-server-key"]
        CM["ConfigMap: litellm-config<br/>上游模型供应商"]
    end

    AdminIng -->|/api/controller| Controller
    AdminIng -->|/api| Manager
    AdminIng -->|/| AdminWeb
    ChatIng -->|/api/gateway| Gateway
    ChatIng -->|/api| Manager
    ChatIng -->|/| PortalWeb

    Manager --> Controller
    Manager --> LiteLLM
    Gateway --> Controller
    Controller -->|apply Deployment+Service+PVC| Eng1
    Controller --> EngN
    Gateway -->|DNS 命名直连| Eng1
    Gateway -->|DNS 命名直连| EngN
    Eng1 --> LiteLLM
    EngN --> LiteLLM

    Manager --> PG
    Controller --> PG
    Gateway --> PG
    LiteLLM --> PG
    Controller --> MinIO
    Eng1 --> MinIO
    Manager --> MinIO

    Secret -. env 注入 .-> Manager
    Secret -. env 注入 .-> Controller
    Secret -. env 注入 .-> Gateway
    Secret -. env 注入 .-> LiteLLM
    Secret -. env 注入 .-> PG
    Secret -. env 注入 .-> MinIO
    CM -. 模型配置 .-> LiteLLM

    EngPVC["PVC: engine-data-{id8}<br/>/opt/data · RWO"]
    Eng1 -. 挂载 .-> EngPVC
```

**要点**：
- **单 namespace**：`unionagents`，无多 ns 隔离；引擎与平台服务同 ns，靠 DNS 命名（`engine-hermes-{id[:8]}[-{scope}].unionagents.svc:8642`）路由。
- **引擎动态创建**：Controller `k8s_manager.py` 按 instance 创建 Deployment + 同名 Service + PVC `engine-data-{id[:8]}`（挂 `/opt/data`，多 Profile 布局 `/opt/data/profiles/{name}`）。
- **Ingress 双域名**：`admin.__DOMAIN__`（管理台）、`chat.__DOMAIN__`（终端门户），前端静态站走 `/`。`/api` 拆分：管理台只路由到 manager + controller（运行时代理），**不接 gateway**；终端门户额外路由 `/api/gateway` 到 gateway（聊天 SSE）。
- **Secret/ConfigMap 集中**：`unionagents-secret` 统一管所有凭据；`litellm-config` ConfigMap 存上游模型供应商（敏感，不入库）。
- **持久化**：PG（10Gi，含 `unionagents` + `litellm` 两个库）、MinIO（20Gi）、引擎 PVC（RWO，每实例一块）。

---

## 6. RBAC 权限矩阵图

权限模型：`Role` ⟂ `Permission`（N:N，经 `role_permissions`），`User` ⟂ `Role`（N:N，经 `user_roles`）。权限粒度 = `resource_type` + `code`。管理台走 RBAC；终端用户走 `access_scope`（与 RBAC 分离）。

### 6a. 权限模型与角色

```mermaid
erDiagram
    USER }o--o{ ROLE : "user_roles"
    ROLE }o--o{ PERMISSION : "role_permissions"
    USER {
        uuid id PK
    }
    ROLE {
        uuid id PK
        string name
    }
    PERMISSION {
        uuid id PK
        string code
        string resource_type
    }
```

> 终端用户访问**不走 RBAC**，由 `AgentInstance.access_scope`（ALL / USER / USER_GROUP）决定可见性，与管理台 RBAC 分离（详见 6c）。

### 6b. 预置角色 × 权限矩阵

| resource_type | 动作 code | 平台管理员 | 组管理员 | 说明 |
|---|---|:---:|:---:|---|
| **litellm** | `model:manage` | ✅ | ❌ | 全局上游模型/供应商，不受组范围限制 |
| | `key:manage` | ✅ | ✅ | 所属 UserGroup 对应 Team 的虚拟 key |
| | `spend:view` | ✅ | ✅ | 所属 UserGroup 用量与成本 |
| **agent_definition** | `view` | ✅ | ✅ | |
| | `create` | ✅ | ✅ | |
| | `update` | ✅ | ✅ | 编辑草稿配置 |
| | `delete` | ✅ | ❌ | 组管理员不可删定义 |
| | `publish` | ✅ | ✅ | 发布版本快照 |
| | `manage_skills` | ✅ | ✅ | 安装/开关/排序/卸载技能 |
| **resource_pool** | `view` | ✅ | ✅ | |
| | `create` | ✅ | ❌ | 资源池仅平台管理员维护 |
| | `update` | ✅ | ❌ | |
| | `delete` | ✅ | ❌ | |
| | `clone` | ✅ | ❌ | |
| **agent_instance** | `view` | ✅ | ✅ | |
| | `create` | ✅ | ✅ | |
| | `update` | ✅ | ✅ | |
| | `delete` | ✅ | ✅ | |
| | `clone` | ✅ | ✅ | |
| | `publish` | ✅ | ✅ | 上线（对终端可见） |
| | `offline` | ✅ | ✅ | 停用 |
| | `switch_version` | ✅ | ✅ | 切换绑定版本（升级/回滚） |
| | `manage_channel` | ✅ | ✅ | 管理 IM 渠道 |
| | `deploy` | ✅ | ✅ | 运行时：部署引擎 |
| | `suspend` | ✅ | ✅ | 运行时：scale 0 + 存档 |
| | `resume` | ✅ | ✅ | 运行时：恢复 |
| | `restart` | ✅ | ✅ | 运行时：滚动重启 |
| | `destroy` | ✅ | ✅ | 运行时：销毁引擎 + 归档 |
| | `view_overview` | ✅ | ✅ | 概览 |
| | `view_metrics` | ✅ | ✅ | 监控 |
| | `view_memory` | ✅ | ✅ | 记忆 |
| | `view_logs` | ✅ | ✅ | Pod 日志 |

> 平台管理员 = 全部权限（含 `resource_pool` 写与 `litellm:model:manage`）；组管理员 = 实例全生命周期 + 定义开发 + 资源池只读 + LiteLLM key/spend，不可删定义、不可改资源池、不可管全局模型。

### 6c. 终端用户 access_scope 与计费 Team 派生

终端用户**不走 RBAC**，由 `AgentInstance.access_scope` 决定可见性；计费 Team 由 access_scope 派生（谁能用谁出钱）：

```mermaid
flowchart LR
    AS{access_scope}
    AS -->|ALL| AllUser["所有登录用户可见<br/>Team=平台默认 default"]
    AS -->|USER| UList["指定用户列表可见<br/>Team=平台默认 default"]
    AS -->|USER_GROUP| GList["指定用户组成员可见<br/>Team=access_groups[0] 对应 Team"]
```

`_derive_team`：`USER_GROUP` → 首个组对应 Team；`ALL`/`USER` → 平台默认 Team（`team_id="default"`）。access 变更触发 per-instance key 重生成。
