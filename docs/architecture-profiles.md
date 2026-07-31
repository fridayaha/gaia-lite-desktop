> ⚠️ **SUPERSEDED（已过时）**：本文档描述 V2 多 Profile 隔离方案。V3 已将顶层模型重构为「定义/资源/实例」三层，Profile 改挂 `agent_instances`。当前架构以 [`architecture-v3.md`](./architecture-v3.md) 为准（2026-06-23）。本文仅作历史参考。

# 多 Profile 动态创建与用户隔离方案

## 1. 概述

### 1.1 什么是 Profile

Profile 是 Hermes 引擎中的一个**独立运行时单元**，包含：

- 一个独立的 Hermes Agent 进程（占用一个端口）
- 独立的会话存储（独立的 SQLite `state.db`）
- 独立的记忆/知识库/技能目录
- 独立的配置环境（`.env`、`config.yaml`）

每个 Profile 本质上就是一个**用户专属的 AI Agent 沙箱**。

### 1.2 解决的问题

| 场景 | 无 Profile 隔离 | 有 Profile 隔离 |
|---|---|---|
| 用户 A 和 B 同时对话 | 共享上下文，互相干扰 | 各自独立上下文 |
| 运营数据统计 | 无法区分用户 | 按 Profile 粒度统计 |
| 引擎重启 | 所有数据丢失 | Profile 目录持久化，恢复后重建 |
| 多租户 | 无法实现 | 天然隔离 |

---

## 2. 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        Gateway (8010)                        │
│  ┌───────────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │  Webhook      │  │  Profile      │  │  Dispatcher      │  │
│  │  Router       │→→→│  Resolver     │→→→│  (Worker Pool)  │  │
│  └───────────────┘  └───────┬───────┘  └────────┬─────────┘  │
│                             │                    │            │
└─────────────────────────────┼────────────────────┼────────────┘
                              │                    │
              ┌───────────────▼──────┐   ┌─────────▼──────────┐
              │   Controller (8001)  │   │  Manager (8002)    │
              │  ┌─────────────────┐ │   │  ┌──────────────┐  │
              │  │ K8sManager      │ │   │  │ CRUD API     │  │
              │  │ ProfileScheduler│ │   │  │ IM Bindings  │  │
              │  │ RecycleScheduler│ │   │  │ Channels     │  │
              │  └─────────────────┘ │   │  └──────────────┘  │
              └───────────┬─────────┘   └─────────────────────┘
                          │ K8s API
              ┌───────────▼──────────────────────────────┐
              │          Engine Pod (K8s)                 │
              │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
              │  │Profile A │ │Profile B │ │Profile C │  │
              │  │port 8644 │ │port 8645 │ │port 8646 │  │
              │  │state.db  │ │state.db  │ │state.db  │  │
              │  └──────────┘ └──────────┘ └──────────┘  │
              │  ┌──────────────────────────────────────┐ │
              │  │  Nginx 反向代理 (按 port 路由)        │ │
              │  └──────────────────────────────────────┘ │
              └──────────────────────────────────────────┘
```

### 2.1 核心组件职责

| 组件 | 职责 | 端口 |
|---|---|---|
| **Manager** | 管理面 CRUD（Agent/EngineInstance/用户/IM绑定/渠道配置） | 8002 |
| **Controller** | K8s 引擎 Pod 生命周期 + Profile 调度 | 8001 |
| **Gateway** | IM 渠道回调接收、Profile 解析、消息分发 | 8010 |
| **Hermes 引擎** | AI 推理、会话管理、记忆存储（Profile 内） | 8642+ |

---

## 3. Profile 数据模型

### 3.1 agent_profiles 表

```sql
CREATE TABLE agent_profiles (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_id UUID NOT NULL REFERENCES agent_deployments(id),
    agent_id      UUID NOT NULL REFERENCES agents(id),

    -- Profile 标识
    profile_name  VARCHAR(64) NOT NULL,  -- 确定性命名
    profile_type  VARCHAR(16) NOT NULL,  -- INDEPENDENT / SHARED

    -- 引擎内路径
    hermes_home   VARCHAR(256),           -- Profile 数据目录
    internal_port INTEGER,               -- 该 Profile 占用的引擎端口

    -- 归属
    user_id  UUID REFERENCES users(id),   -- INDEPENDENT 时绑定用户
    group_id UUID REFERENCES user_groups(id),  -- SHARED 时绑定组

    UNIQUE(deployment_id, profile_name),
    UNIQUE(agent_id, engine_instance_id, user_id)
);
```

### 3.2 Profile 命名规范

Profile 名称采用**确定性哈希**，确保相同用户的相同请求命中同一 Profile：

```
{agent_id_short8}-{scope_hash6}-{user_id_short8}
```

- `agent_id_short8`: Agent UUID 取前 8 位（去掉横线）
- `scope_hash6`: `SHA256("SCOPE_TYPE:SCOPE_TARGET_ID")` 前 6 位
- `user_id_short8`: 用户 UUID 取前 8 位

**示例**：`d38e436e-f664d6-39c9e118`

### 3.3 Profile 目录结构（引擎 Pod 内）

```
/opt/data/profiles/
├── d38e436e-f664d6-39c9e118/       ← MengLiang 的 Profile
│   ├── .env                         ← 引擎环境变量
│   ├── config.yaml                  ← 引擎配置
│   ├── state.db                     ← SQLite 会话数据库
│   ├── response_store.db            ← 响应缓存
│   ├── sessions/                    ← 会话元数据
│   ├── memories/                    ← 长期记忆
│   ├── skills/                      ← 技能插件
│   ├── logs/                        ← 引擎运行时日志
│   └── cache/                       ← 模型缓存
│
├── d38e436e-f664d6-21d72d5b/       ← LiaoQiWang 的 Profile
│   └── ...
│
└── base/                            ← 基础 Profile（模板）
    ├── .env
    ├── config.yaml
    └── ...
```

---

## 4. 多 Profile 隔离机制

### 4.1 存储隔离

| 维度 | 隔离策略 | 实现方式 |
|---|---|---|
| **会话数据** | 每个 Profile 独立 SQLite | 各自 `state.db`，互不访问 |
| **记忆** | Profile 级目录隔离 | `memories/` 目录各自独立 |
| **配置** | Profile 级 `.env` | 每个 Profile 有自己的环境变量 |
| **模型缓存** | 共享（只读） | `models_dev_cache.json` 可跨 Profile 共享 |

### 4.2 端口隔离

每个 Profile 在引擎 Pod 内启动一个独立的 Hermes Gateway 进程，占用一个端口：

```
Profile A → port 8644 → process hermes -p profile_a gateway start
Profile B → port 8645 → process hermes -p profile_b gateway start
Profile C → port 8646 → process hermes -p profile_c gateway start
```

Nginx 容器根据 `X-Hermes-Profile` 头部将请求路由到对应端口。

### 4.3 IM 用户映射

企业微信/飞书/钉钉的 `user_id` 通过 `im_user_bindings` 表映射到内部用户 UUID：

```sql
-- im_user_bindings 记录
channel_type='wecom', im_user_id='LiaoQiWang' → user_id='21d72d5b-...'
```

Gateway 的 ProfileResolver 在收到 IM 消息时：
1. 查 `im_user_bindings` 将平台用户 ID 转为内部 UUID
2. 用内部 UUID 生成 Profile 名称
3. 路由到对应 Profile

---

## 5. 动态创建流程

### 5.1 完整链路（以企业微信消息为例）

```
用户发消息 → WeCom 服务器 → Gateway Webhook
                                     │
                                     ▼
                          Channel 路由 (router.py)
                          验证签名 + 解密 XML
                                     │
                                     ▼
                          Dispatcher (dispatcher.py)
                          去重 + 入队
                                     │
                                     ▼
                          ProfileResolver.resolve()
                          1. IM 用户映射 (im_user_bindings)
                          2. Agent 访问权限检查
                          3. 渠道配置匹配
                          4. 生成 Profile 名称
                          5. 查 Deployment 记录
                          6. Profile 存在？→ 返回。不存在 → 继续
                                     │
                                     ▼
                          Controller /profiles/ensure
                          1. 查 agent_profiles 表
                          2. 不存在 → 分配端口
                          3. 调用引擎 hermes profile create
                          4. 复制基础 Profile 配置
                          5. 写入 .env（含 API Key）
                          6. 更新 nginx 配置
                          7. 返回 Profile 信息
                                     │
                                     ▼
                          Dispatcher 转发消息到引擎
                          POST /v1/chat/completions
                          Header: X-Hermes-Profile={profile_name}
                                     │
                                     ▼
                          Nginx → 对应端口的 Hermes Gateway
                                     │
                                     ▼
                          AI 模型推理 → 返回回复
```

### 5.2 时序图

```
User                Gateway          Controller        Engine Pod
 │                    │                  │                 │
 │── WeCom 消息 ─────→│                  │                 │
 │                    │── GET /health ───→│                 │ (引擎存活检查)
 │                    │── POST /profiles/ensure ─→│        │ (Profile 不存在则创建)
 │                    │                  │── hermes profile create ─→│
 │                    │                  │── nginx reload  ────────→│
 │                    │                  │←── port: 8644 ──────────│
 │                    │── POST /v1/chat/completions ──────────────→│
 │                    │                  │                 │── AI 推理
 │                    │←── response ───────────────────────│
 │                    │── WeCom 回复消息（MP.news）──→│
 │←── 用户收到回复 ───│                  │                 │
```

### 5.3 幂等性保证

`/profiles/ensure` 端点具有幂等性：

```python
# 先差
existing = db.query(AgentProfile).where(
    profile_name=req.profile_name,
    agent_id=req.agent_id
).first()

# 存在则直接返回
if existing and existing.internal_port:
    return existing

# 不存在则创建
port = allocate_port(deployment)
create_hermes_profile(agent_id, profile_name, port)
return {profile_name, port, created=true}
```

---

## 6. Profile 类型

### 6.1 INDEPENDENT（独立 Profile）

| 属性 | 值 |
|---|---|
| 适用范围 | Agent `access_scope` = ALL 或 USER |
| 用户关系 | 1 个 Profile = 1 个用户 |
| Profile 名称 | 含 user_id 哈希 |
| 数据隔离 | 完全独立 |
| 典型场景 | 全员通用 Agent，每个用户独立上下文 |

### 6.2 SHARED（共享 Profile）

| 属性 | 值 |
|---|---|
| 适用范围 | Agent `access_scope` = USER_GROUP |
| 用户关系 | 1 个 Profile = 1 个用户组 |
| Profile 名称 | 含 group_id 哈希 |
| 数据隔离 | 组内共享 |
| 典型场景 | 门店智能体，同一门店的店员共享 Profile |

### 6.3 Agent 访问范围 → Profile 派生产出规则

Channel 的 `scope_type` 和 `profile_type` 不再由用户手动设置，而是由后端从 Agent 的 `access_scope` 自动派生：

```python
def derive_scope_and_profile(agent):
    if agent.access_scope in (ALL, USER):
        return scope="ALL", profile="INDEPENDENT"
    if agent.access_scope == USER_GROUP:
        return scope="USER_GROUP", profile="SHARED"
```

| Agent.access_scope | Channel 派生结果 | 说明 |
|---|---|---|
| ALL | scope=ALL, profile=INDEPENDENT | 全员可见，每人独立 Profile |
| USER | scope=ALL, profile=INDEPENDENT | Agent 层已做用户过滤 |
| USER_GROUP | scope=USER_GROUP, profile=SHARED | 用户组内共享 Profile |

---

## 7. 端口分配与管理

### 7.1 端口池

每个引擎 Pod 预分配端口范围：**8642 ~ 8661**（最多 20 个 Profile/Pod，由 `max_profiles_per_pod` 配置）

### 7.2 端口分配策略

```python
def allocate_port(deployment):
    port_map = deployment.internal_port_map or {"profiles": {}, "next_port": 8644}
    port = port_map["next_port"]
    port_map["next_port"] = port + 1
    port_map["profiles"][profile_name] = port
    # 持久化到 agent_deployments.internal_port_map
    update_deployment(deployment, port_map)
    return port
```

- Port 8642：Hermes 引擎主端口（API Server）
- Port 8643：预留（基础 Profile default）
- Port 8644+：动态分配的 Profile

### 7.3 端口回收

Profile 销毁时释放端口：

```python
def release_port(deployment, profile_name):
    port_map = deployment.internal_port_map
    port_map["profiles"].pop(profile_name, None)
    # 不减小 next_port（端口号只增不降，避免冲突）
    update_deployment(deployment, port_map)
```

---

## 8. 生命周期管理

### 8.1 状态机

```
                    ┌──────────┐
                    │  PENDING  │  ← 首次创建 Agent，未部署
                    └─────┬────┘
                          │ Controller.deploy()
                          ▼
                    ┌──────────┐
              ┌────→│ DEPLOYING│  ← K8s 创建 Pod 中
              │     └─────┬────┘
              │           │ Pod Ready
              │           ▼
              │     ┌──────────┐
              │     │  RUNNING  │  ← 正常运行，可接收消息
              │     └────┬─────┘
              │           │ 30min 无活动
              │           ▼
              │     ┌──────────┐
              │     │ SUSPENDED│  ← 存档到 MinIO，scale=0
              │     └────┬─────┘
              │           │ 24h 后
              │           ▼
              │     ┌──────────┐
              │     │ ARCHIVED  │  ← K8s 资源删除，MinIO 保留
              │     └──────────┘
              │
              └── 重新部署（从 SUSPENDED 恢复）
```

### 8.2 Profile 在生命周期中的变化

| 部署状态 | Profile 状态 | Pod 状态 | 数据位置 |
|---|---|---|---|
| PENDING | — | 无 | 无 |
| DEPLOYING | — | 创建中 | — |
| RUNNING | 活跃 | Running | Pod 内 `/opt/data/profiles/` |
| SUSPENDED | 已存档 | Scale=0 | MinIO 备份 |
| ARCHIVED | 已归档 | 已删除 | MinIO 归档 |

### 8.3 RecycleScheduler

Controller 内部运行循环：

```python
# 每 5 分钟
suspend_loop():
    for dep in RUNNING deployments:
        if now - dep.last_active_at > idle_suspend_minutes:
            scale_to_zero(dep)
            save_to_minio(dep)
            dep.status = SUSPENDED

# 每小时
cleanup_loop():
    for dep in SUSPENDED deployments:
        if now - dep.suspended_at > idle_destroy_hours:
            delete_k8s_resources(dep)
            dep.status = ARCHIVED
```

---

## 9. 关键配置参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `idle_suspend_minutes` | 30 | 空闲多少分钟后暂停（存档 + scale=0） |
| `idle_destroy_hours` | 24 | 暂停后多少小时销毁 K8s 资源 |
| `max_profiles_per_pod` | 20 | 单个 Pod 最大 Profile 数 |
| `min_replicas` / `max_replicas` | 1 / 5 | 自动扩缩容范围 |

---

## 10. 多 Profile 相关 API

| 端点 | 方法 | 说明 | 调用方 |
|---|---|---|---|
| `/api/controller/profiles` | POST | 创建 Profile | Gateway |
| `/api/controller/profiles/ensure` | POST | 幂等创建 Profile | Gateway |
| `/api/controller/profiles/{id}` | DELETE | 删除 Profile | Gateway |
| `/api/controller/agents/{id}/deploy` | POST | 部署 Agent（创建引擎 Pod） | Admin |
| `/api/controller/agents/{id}/suspend` | POST | 暂停（存档 + scale=0） | Admin |
| `/api/controller/agents/{id}/destroy` | POST | 销毁 K8s 资源 | Admin |
| `/api/controller/agents/{id}/config/sync` | POST | 同步配置到 MinIO | Admin |
| `/api/controller/agents/{id}/config/apply` | POST | 应用配置 + 重启引擎 | Admin |
| `/api/controller/engine-instances/{id}/pods` | GET | 列出 Pod（含 Profile 信息） | Admin 前端 |
| `/api/controller/engine-instances/{id}/pods/{name}/logs` | GET | 获取 Pod 日志 | Admin 前端 |
| `/api/manager/users/{id}/im-bindings` | GET/POST/DELETE | IM 用户绑定管理 | Admin 前端 |

---

## 11. 数据流示例：完整的多用户对话

### 场景

企业微信中，门店助手 Agent 被 3 个用户访问：

| 用户 | WeCom ID | 绑定内部用户 | Profile |
|---|---|---|---|
| 店长 郭然 | GuoRan | `staff_a` | `d38e436e-f664d6-83c83c77` |
| 店员 孟亮 | MengLiang | `staff_b` | `d38e436e-f664d6-c79cfbd0` |
| 管理员 廖启旺 | LiaoQiWang | `admin` | `d38e436e-f664d6-c246cea4` |

### 流程

```
1. GuoRan 在企微发消息 → WeCom → Gateway webhook
   Gateway decrypt XML, verify signature
   → Dispatcher dispatch
   → ProfileResolver.resolve("GuoRan", agent_id, "wecom")
     → im_user_bindings: "GuoRan" → staff_a UUID
     → agent.access_scope = USER_GROUP → SHARED profile
     → profile_name = "d38e436e-f664d6-83c83c77"
     → Controller /profiles/ensure
       → DB 已有 → 直接返回 port=8645
   → Forward to engine:8645 (GuoRan 的 Profile)

2. MengLiang 同时发消息
   → profile_name = "d38e436e-f664d6-c79cfbd0"
   → port=8646 (不同 Profile，不同端口)

3. LiaoQiWang 同时发消息
   → profile_name = "d38e436e-f664d6-c246cea4"
   → port=8644 (不同 Profile，不同端口)
```

**三个用户并行对话，互不干扰。**

---

## 12. 验证方法

### 12.1 数据库验证

```sql
-- 查 Profile 是否创建
SELECT profile_name, internal_port, user_id
FROM agent_profiles
WHERE agent_id = 'd38e436e-...';

-- 查 IM 绑定
SELECT channel_type, im_user_id, u.username
FROM im_user_bindings b
JOIN users u ON u.id = b.user_id;
```

### 12.2 引擎 Pod 验证

```bash
# 查看所有 Profile 目录
kubectl exec deploy/engine-hermes-xxxx -- ls /opt/data/profiles/

# 查看 Profile 的会话数据
kubectl exec deploy/engine-hermes-xxxx -- \
  sqlite3 /opt/data/profiles/xxx/state.db \
  "SELECT COUNT(*) FROM sessions"

# 查看各 Profile 端口
kubectl exec deploy/engine-hermes-xxxx -- \
  cat /opt/data/profiles/xxx/.env | grep PORT
```

### 12.3 Admin 前端验证

1. 资源池详情 → Pod 管理 Tab → 查看 Pod 列表
2. Pod 日志 Tab → 查看引擎实时日志
3. 智能体详情 → 概览 Tab → 部署状态卡片
4. 用户管理 → 编辑用户 → IM 平台绑定

### 12.4 模拟器验证

使用 `scripts/im_test_simulator.py`（本地开发工具）：
1. 用不同 `user_id` 发送消息 → 创建不同 Profile
2. 查看 `agent_profiles` 表确认每条消息对应独立 Profile
