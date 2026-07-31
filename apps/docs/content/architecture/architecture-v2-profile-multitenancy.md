> ⚠️ **SUPERSEDED（已过时）**：本文档为 V2 多租户架构重构实施计划。V3 已在其上进一步重构为「定义/资源/实例」三层（`agent_definitions` / `agent_versions` / `resource_pools` / `agent_instances`）。当前架构以 [`architecture-v3.md`](./architecture-v3.md) 为准（2026-06-23）。本文仅作历史参考。

# UnionAgents V2 架构重构 - 实施计划（最终版）

## Context

当前「单 Pod = 单用户」→ 重构为「Engine Instance（多租户） + 多 Agent + Hermes 原生多 Profile」架构。

核心约束：
- **复用 Hermes 原生能力**：`hermes profile create`、`hermes -p <name> gateway start/stop`、s6-overlay 进程管理
- **Hermes 官方 Docker 镜像包含 s6-overlay**：创建 Profile 时自动注册 s6 服务(`/run/service/gateway-<name>/`)，通过 `s6-svc` 管理启停
- **Hermes 不支持多进程并发写同一数据目录**："Never run two Hermes gateway containers against the same data directory simultaneously"

---

## 一、s6-overlay 原理

Hermes 官方 Docker 镜像使用 s6-overlay 作为容器 PID 1，管理所有 Hermes 进程。理解其原理是架构设计的前提。

### 1.1 三层监管结构

```
s6-svscan (PID 1)                          ← 根监管进程
  │
  └── 扫描 /run/service/ 目录下的所有服务
       │
       ├── s6-supervise → main-hermes/run       ← 每服务一个监管进程
       │   └── 执行: hermes gateway run         ← 默认 profile
       │
       ├── s6-supervise → gateway-coder/run
       │   └── 执行: hermes -p coder gateway run
       │
       └── s6-supervise → gateway-assistant/run
           └── 执行: hermes -p assistant gateway run
```

- **`s6-svscan`**：根监管进程，监控 `/run/service/` 下所有服务目录。服务目录出现/消失会被自动感知，无需显式 reload。
- **`s6-supervise`**：每服务一个监管进程。负责启动 `run` 脚本、监控子进程存活、崩溃后自动重启（带退避）。
- **`s6-svc`**：命令行控制工具。`s6-svc -u <dir>` 启动服务，`s6-svc -d <dir>` 停止服务，`s6-svc -r <dir>` 重启服务。

### 1.2 Hermes 如何集成 s6

**创建 Profile 时自动注册服务：**
```bash
hermes profile create coder
```
Hermes 内部逻辑：
1. 创建 Profile 目录 `/opt/data/profiles/coder/`
2. 在 `/run/service/gateway-coder/` 创建 s6 服务目录
3. 写入 `run` 脚本（内容是 `exec hermes -p coder gateway run`）
4. s6-svscan 自动发现新服务目录 → s6-supervise 启动进程

**启停控制透明委托给 s6-svc：**
```bash
hermes -p coder gateway start   # → s6-svc -u /run/service/gateway-coder/
hermes -p coder gateway stop    # → s6-svc -d /run/service/gateway-coder/
hermes -p coder gateway restart # → s6-svc -r /run/service/gateway-coder/
hermes -p coder gateway status  # → s6-svstat /run/service/gateway-coder/
                                #    输出: "Manager: s6 (container supervisor)"
```

**容器重启时自动恢复：**
1. 容器启动 → s6-overlay `/init` 成为 PID 1
2. 运行 `/etc/cont-init.d/02-reconcile-profiles`
3. 遍历 `/opt/data/profiles/*/gateway_state.json` 读取上次运行状态
4. 为状态为 `running` 的 Profile 重建 s6 服务目录
5. s6 自动启动这些 gateway 进程

### 1.3 对我们架构的影响

- **Profile 创建 = 写文件 + 创建 s6 服务**：Controller 通过 `kubectl exec` 调用 `hermes profile create`，s6 自动接管后续管理
- **崩溃自愈**：某个 Profile 的 gateway 进程崩溃，s6 自动重启，无需 Controller 介入
- **容器重启**：之前 running 的 Profile 自动恢复，之前 stop 的保持停止
- **我们只需维护 nginx 路由层**：因为 Hermes 不提供 API 层的 profile 路由，需要在 Pod 内加一层 nginx 将 `X-Hermes-Profile` 头映射到对应端口

---

## 二、核心架构决策

### 2.1 关键关系

```
EngineInstance (资源池) ←→ Agent: 一对多
  ├── 一个 EngineInstance 可运行多个 Agent
  └── 一个 Agent 只绑定到一个 EngineInstance（用 clone 跨资源池复用）

Agent ←→ AgentChannel: 一对多
  └── 按 channel_type + scope 分

EngineInstance ←→ AgentDeployment (Pod): 一对多
  └── 1~N 个对等 Pod
```

**Agent 与 EngineInstance 是多对一（N:1）**，不是多对多。光谷店的销售智能体和1号店的销售智能体是两个**独立的** Agent 记录（从模板 clone 而来），各自绑定到自己的 EngineInstance。

### 2.2 整体部署拓扑

```
Engine Instance "光谷" (Store-level K8s resource pool)
│
├── Pod A (s6-overlay, port 8642)
│   ├── gateway (default profile, port 8642)    ← s6 管理
│   ├── gateway-emp001 (port 8643)              ← s6 管理
│   ├── gateway-emp002 (port 8644)              ← s6 管理
│   └── nginx sidecar (port 8642 → 路由到各 profile port)
│       └── X-Hermes-Profile → upstream port 映射
│
├── Pod B (s6-overlay, port 8642)
│   ├── gateway (default) + gateway-emp003 + gateway-emp004
│   └── nginx sidecar
│
└── Pod C (s6-overlay, port 8642)
    ├── gateway (default) + gateway-shared-ops
    └── nginx sidecar

每个 Profile 的 Hermes 进程只在一个 Pod 中运行（避免并发写冲突）
Profile 分配由 Controller 调度（选择 Profile 数最少的 Pod）
Pod 对外统一端口 8642，内部 nginx 根据 X-Hermes-Profile 路由
```

### 2.3 Pod 数量计算公式

**问题**：光谷门店有 100 名销售人员，每人独立 Profile，需要多少个引擎 Pod？

**核心约束**：每个 Pod 的 Hermes gateway 进程数受内存和端口范围限制。

**公式**：

```
Pods_needed = max(
    ceil(Total_Profiles / Max_Profiles_Per_Pod),   ← 内存约束
    ceil(Peak_Concurrent_Requests / Max_Requests_Per_Pod), ← CPU约束
    min_replicas,                                   ← 保底
)

其中:
  Max_Profiles_Per_Pod = floor(
    (Pod_Memory_MB × 0.7 - Base_Overhead_MB)       ← 70%可用内存，预留default/nginx开销
    / Memory_Per_Profile_MB
  )

  Memory_Per_Profile_MB:
    - Idle gateway:     ~80MB  (Hermes 进程空载)
    - Active gateway:   ~250MB (处理请求中，含模型上下文缓存)
    - 计算时取加权:      Active_Pct × 250 + (1-Active_Pct) × 80
```

**实例演算（光谷门店，100 销售人员）**：

```
假设:
  - EngineInstance: max_memory = 4Gi (4096MB)
  - 预计同时活跃用户: 20% (20人)
  - Base overhead (default profile + nginx): 200MB
  - Memory_Per_Profile = 0.2 × 250 + 0.8 × 80 = 114MB (加权平均)
  
计算:
  Max_Profiles_Per_Pod = floor((4096 × 0.7 - 200) / 114) 
                        = floor(2667 / 114) = 23 个 Profile/Pod
  
  Pods_needed = ceil(100 / 23) = 5 个 Pod
  
  ─── 加上店长、运营等 ───
  Total_Profiles = 100(销售) + 1(店长) + 1(运营共享) = 102
  Pods_needed = ceil(102 / 23) = 5 个 Pod

验证并发:
  5 Pods × 23 Profiles = 115 Profile slots > 102 ✓
  每 Pod 承担: 102/5 ≈ 20 Profiles
  每 Pod 同时活跃: 20 × 20% ≈ 4 个活跃 Profile
  每 Pod 活跃内存: 4 × 250 + 16 × 80 ≈ 2280MB < 4096MB × 0.7 ✓
```

**不同规模的 Pod 数量参考**：

| 门店规模 | 销售人员 | 总计 Profiles | Pod 数量 (4Gi/Pod) | Pod 数量 (8Gi/Pod) |
|---|---|---|---|---|
| 小型 | 20 | 22 | 1 | 1 |
| 中型 | 50 | 52 | 2 | 1 |
| 大型 | 100 | 102 | 5 | 3 |
| 超大型 | 200 | 202 | 9 | 5 |
| 旗舰 | 500 | 502 | 22 | 11 |

**扩容逻辑**：当 Profile 数接近 `Pods × Max_Profiles_Per_Pod × 0.8` 时（80% 水位线），Controller 自动触发扩容（增加 replica）。

### 2.4 Profile 到 Pod 的调度策略

**核心原则：每个 Profile 的 Hermes gateway 进程只在唯一一个 Pod 中运行。**

```
┌─────────────────────────────────────────────────────────┐
│ Controller: ProfileScheduler                            │
│                                                         │
│  assign(pod, profile):                                  │
│    1. kubectl exec {pod} -- hermes profile create {name}│
│       --clone --clone-from base                         │
│    2. kubectl exec {pod} -- hermes -p {name} config set │
│       api_server.port {port}                            │
│    3. kubectl exec {pod} -- hermes -p {name} gateway    │
│       start                                             │
│    4. 更新 AgentProfile.deployment_id → pod             │
│    5. 更新 pod 的 nginx upstream 配置                    │
│                                                         │
│  on_pod_failure(pod):                                   │
│    1. 查询 pod 上的所有 Profile                          │
│    2. 对每个 Profile，选择新的 Pod（最少 Profile 数）    │
│    3. 在新 Pod 上重建 Profile + 启动 gateway            │
│    4. 更新 AgentProfile.deployment_id                   │
└─────────────────────────────────────────────────────────┘
```

**Pod 内 nginx sidecar**：动态维护 `X-Hermes-Profile → 127.0.0.1:{port}` 映射。当 Controller 在 Pod 上创建新 Profile 时，同步更新 nginx 配置并 reload。

### 2.5 数据存储

- **Pod 本地存储（emptyDir）**：Hermes 数据目录（`/opt/data/`），包括 sessions、state.db、memories
- **MinIO**：备份/恢复（现有机制，`exec tar` → MinIO，SUSPEND/RESUME 时使用）
- **不共享 PVC**：因为 Hermes 不支持并发写，每个 Pod 的 `/opt/data` 是独立的

### 2.6 基础镜像选择

```
FROM nousresearch/hermes-agent:latest  # 官方镜像（含 s6-overlay）
# 官方镜像基于 debian:13.4，已包含 s6-overlay、Python 3、Node.js 等
# 在此基础上添加我们的 nginx sidecar 和配置
```

或者，如果我们现有的 `python:3.11-slim` 基础镜像需要保留，可以单独安装 s6-overlay。但推荐直接 Extend 官方镜像以获得完整的 Hermes 原生体验。

---

## 三、EngineInstance（引擎实例 = 多租户资源池）

### 3.1 模型

```sql
CREATE TABLE engine_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(128) NOT NULL,           -- "光谷引擎实例"
    description TEXT DEFAULT '',
    engine_type VARCHAR(32) NOT NULL DEFAULT 'HERMES',
    engine_image VARCHAR(256) DEFAULT 'unionagents/engine-hermes:latest',

    -- K8s 资源规格（每个 Pod 的资源）
    min_cpu VARCHAR(16) DEFAULT '100m',
    max_cpu VARCHAR(16) DEFAULT '2',      -- 多 Profile 需要更多资源
    min_memory VARCHAR(16) DEFAULT '256Mi',
    max_memory VARCHAR(16) DEFAULT '2Gi',

    -- Pod 副本数（对等 Pod 数量，用于负载均衡）
    min_replicas INTEGER DEFAULT 1,
    max_replicas INTEGER DEFAULT 5,

    -- 自动回收
    auto_recycle BOOLEAN DEFAULT TRUE,
    idle_suspend_minutes INTEGER DEFAULT 30,
    idle_destroy_hours INTEGER DEFAULT 24,

    -- 每个 Pod 最多承载的 Profile 数（用于调度）
    max_profiles_per_pod INTEGER DEFAULT 20,

    is_builtin BOOLEAN DEFAULT FALSE,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 API

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/manager/engine-instances` | 列表 |
| POST | `/api/manager/engine-instances` | 创建 |
| GET | `/api/manager/engine-instances/:id` | 详情 |
| PUT | `/api/manager/engine-instances/:id` | 编辑 |
| DELETE | `/api/manager/engine-instances/:id` | 删除（仅非内置） |
| POST | `/api/manager/engine-instances/:id/clone` | 克隆快捷新建 |

---

## 四、Agent（智能体定义）

### 4.1 模型

```sql
ALTER TABLE agents
    DROP COLUMN engine_type,
    DROP COLUMN config,
    ADD COLUMN model_config JSONB DEFAULT '{}',
    ADD COLUMN skill_config JSONB DEFAULT '{}',
    ADD COLUMN memory_config JSONB DEFAULT '{}',
    ADD COLUMN avatar_color VARCHAR(7) DEFAULT '#6366f1',
    -- 多对一: 一个 Agent 只绑定到一个 EngineInstance
    ADD COLUMN engine_instance_id UUID REFERENCES engine_instances(id);
```

**关键关系**：Agent → EngineInstance 是**多对一（N:1）**。
- 光谷店的「猛士销售智能体」和1号店的「猛士销售智能体」是**两个独立的 Agent 记录**
- 通过 **Clone API**（`POST /api/manager/agents/{id}/clone`）快速复制 Agent 定义 + 绑定到不同 EngineInstance
- 一个 EngineInstance 下可以有多个 Agent

`model_config` 结构：
```json
{
  "system_prompt": "你是猛士销售智能体...",
  "model_providers": [
    {"type": "anthropic", "api_key": "sk-...", "model_name": "claude-sonnet-4-6"}
  ],
  "parameters": {"temperature": 0.7, "max_tokens": 4096}
}
```

### 4.2 关键关系

- Agent → EngineInstance：**多对一（N:1）**。一个 Agent 只绑定到一个 EngineInstance，一个 EngineInstance 可运行多个 Agent。跨资源池复用通过 Clone API 创建独立 Agent 记录实现。
- Agent → AgentChannel：**一对多**（按 scope 分）
- EngineInstance → AgentDeployment（Pod）：**一对多**（1~N 个对等 Pod）

---

## 五、AgentChannel（渠道 + Profile 策略）

### 5.1 模型

```sql
-- 修改现有表
ALTER TABLE agent_channels DROP CONSTRAINT uq_agent_channel;

ALTER TABLE agent_channels
    ADD COLUMN scope_type VARCHAR(16) NOT NULL DEFAULT 'ALL'
        CHECK (scope_type IN ('ALL', 'USER_GROUP', 'USER')),
    ADD COLUMN scope_target_id UUID,
    ADD COLUMN profile_type VARCHAR(16) NOT NULL DEFAULT 'INDEPENDENT'
        CHECK (profile_type IN ('INDEPENDENT', 'SHARED')),
    ADD COLUMN engine_instance_id UUID REFERENCES engine_instances(id);

ALTER TABLE agent_channels
    ADD CONSTRAINT uq_agent_channel_scope
    UNIQUE (agent_id, channel_type, engine_instance_id, scope_type, scope_target_id);
```

### 5.2 Profile 策略映射

| Agent.access_scope | Channel.scope_type | Channel.profile_type | Profile 命名 |
|---|---|---|---|
| ALL | ALL | INDEPENDENT | `{agent_short}-{instance_short}-{user_short}` |
| USER_GROUP | USER_GROUP | INDEPENDENT | `{agent_short}-{instance_short}-{user_short}` |
| USER_GROUP | USER_GROUP | SHARED | `{agent_short}-{instance_short}-shared` |
| USER | USER | INDEPENDENT | `{agent_short}-{instance_short}-{user_short}` |

### 5.3 Channel 创建规则

- `access_scope=ALL`：1 个 Channel（scope_type=ALL），**每 Engine Instance 一个**
- `access_scope=USER_GROUP`：每组 1 个 Channel × 每 Engine Instance
- `access_scope=USER`：每用户 1 个 Channel × 每 Engine Instance

---

## 六、AgentDeployment 与 AgentProfile

### 6.1 AgentDeployment（Pod）

```sql
ALTER TABLE agent_deployments DROP CONSTRAINT IF EXISTS agent_deployments_agent_id_key;

ALTER TABLE agent_deployments
    ADD COLUMN engine_instance_id UUID REFERENCES engine_instances(id),
    ADD COLUMN scope_type VARCHAR(16) NOT NULL DEFAULT 'ALL',
    ADD COLUMN scope_target_id UUID,
    ADD COLUMN internal_port_map JSONB;

ALTER TABLE agent_deployments
    ADD CONSTRAINT uq_agent_deployment_scope
    UNIQUE (agent_id, engine_instance_id, scope_type, scope_target_id);
```

`internal_port_map` 结构：
```json
{
  "profiles": {
    "a1b2-gg-emp001": 8643,
    "a1b2-gg-emp002": 8644,
    "a1b2-gg-shared": 8645
  },
  "next_port": 8646
}
```

### 6.2 AgentProfile（Profile 映射）

```sql
CREATE TABLE agent_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    engine_instance_id UUID NOT NULL REFERENCES engine_instances(id),
    deployment_id UUID NOT NULL REFERENCES agent_deployments(id),
    
    profile_name VARCHAR(256) NOT NULL,
    profile_type VARCHAR(16) NOT NULL CHECK (profile_type IN ('INDEPENDENT','SHARED')),
    
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    group_id UUID REFERENCES user_groups(id) ON DELETE SET NULL,
    
    hermes_home VARCHAR(512) NOT NULL,     -- /opt/data/profiles/{profile_name}
    internal_port INTEGER,                  -- Pod 内端口
    
    is_active BOOLEAN DEFAULT TRUE,
    config_synced_at TIMESTAMPTZ,
    
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE (deployment_id, profile_name),
    UNIQUE (agent_id, engine_instance_id, user_id)  -- 每用户在每个(Agent, Instance)下唯一
);
```

---

## 七、命名规范

### 7.1 K8s 资源

```
# Deployment: engine-{engine_type}-{instance_id_short}
# 示例:
engine-hermes-3f7a2e1b    # 光谷引擎实例

# replica pods:
engine-hermes-3f7a2e1b-0  # pod-0
engine-hermes-3f7a2e1b-1  # pod-1
engine-hermes-3f7a2e1b-2  # pod-2
```

### 7.2 Hermes Profile

```
# agent_id_short-instance_id_short-user_id_short
# 示例:
a1b2c3d4-3f7a2e-emp001     # 销售智能体@光谷×员工A（INDEPENDENT）
a1b2c3d4-3f7a2e-shared     # 销售智能体@光谷×组共享（SHARED）
e5f6a7b8-3f7a2e-mgr001     # 店长智能体@光谷×店长A（INDEPENDENT）
```

---

## 八、Gateway 重构

### 8.1 ProfileResolver（核心新增模块）

```python
# services/gateway/app/profile_resolver.py

@dataclass
class ResolvedTarget:
    profile_name: str
    profile_type: str
    pod_name: str          # 目标 pod 的 K8s 名称
    pod_address: str       # pod IP:port 或 service DNS
    internal_port: int     # Pod 内 Hermes 进程端口

class ProfileResolver:
    """
    解析 (user_id, agent_id, engine_instance_id, channel_type) → ResolvedTarget
    
    路由链:
      用户请求 → Gateway
        → 确定 engine_instance_id（从 channel 配置）
        → 查找 Profile（agent_profiles 表）
        → 若不存在 → 调 Controller 创建 Profile
        → 查找目标 Pod（deployment_id → pod_name）
        → 返回 ResolvedTarget
    """
    
    async def resolve(
        self, user_id: str, agent_id: str, 
        engine_instance_id: str, channel_type: str = "http"
    ) -> ResolvedTarget:
        # 1. 验证用户对 Agent 的访问权限
        # 2. 匹配 Channel（按 engine_instance_id + scope_type + scope_target_id）
        # 3. 确定 profile_name（命名规则见 §6.2）
        # 4. 查找或创建 AgentProfile（无则调 Controller）
        # 5. 返回目标 pod + port
```

### 8.2 路由流程

```
User Request → Gateway (JWT auth)
  │
  ├── 1. Extract: user_id (JWT sub), agent_id (X-Agent-ID header)
  ├── 2. Query agent_channels: 找到匹配的 channel
  │     └── Match: engine_instance_id + scope_type + scope_target_id vs user's groups
  ├── 3. Determine profile_name:
  │     ├── INDEPENDENT: {agent_short}-{instance_short}-{user_short}
  │     └── SHARED: {agent_short}-{instance_short}-shared
  ├── 4. Query agent_profiles: 查找已有 profile
  │     └── Not found → POST /api/controller/profiles (创建 Profile + 分配到 Pod)
  ├── 5. Query agent_deployments: 获取 pod_name + internal_port_map
  ├── 6. Construct upstream: http://{pod_ip}:8642
  │     └── Inject X-Hermes-Profile: {profile_name}
  └── 7. Proxy request → nginx in pod → Hermes gateway on {internal_port}
```

### 8.3 安全：忽略客户端 Header

```python
# Gateway 强制忽略客户端传入的 X-Hermes-Profile
# Profile 名由服务端计算，不可被客户端篡改
if request.headers.get("X-Hermes-Profile"):
    logger.warning("Client X-Hermes-Profile header IGNORED (server-computed)")
    del request.headers["X-Hermes-Profile"]

# 注入服务端计算的值
headers["x-hermes-profile"] = target.profile_name
```

### 8.4 Pod 内 nginx 路由

```nginx
# 每个 Pod 内的 nginx sidecar
# 根据 X-Hermes-Profile 头路由到对应 Hermes 进程

upstream profile_emp001 { server 127.0.0.1:8643; }
upstream profile_emp002 { server 127.0.0.1:8644; }
upstream profile_shared { server 127.0.0.1:8645; }

server {
    listen 8642;
    
    location / {
        set $backend "127.0.0.1:8643";  # default
        if ($http_x_hermes_profile = "a1b2c3d4-3f7a2e-emp001") {
            set $backend "127.0.0.1:8643";
        }
        if ($http_x_hermes_profile = "a1b2c3d4-3f7a2e-emp002") {
            set $backend "127.0.0.1:8644";
        }
        # ... 动态生成
        
        proxy_pass http://$backend;
        proxy_buffering off;     # SSE 必须
        proxy_set_header Origin "";
        proxy_set_header Referer "";
    }
}
```

Controller 通过 `kubectl exec` 更新 nginx 配置并执行 `nginx -s reload`。

---

## 九、Controller 重构

### 9.1 新 API：Profile 生命周期管理

```python
# services/controller/app/main.py（新增）

class CreateProfileRequest(BaseModel):
    agent_id: str
    engine_instance_id: str
    user_id: str                          # INDEPENDENT profile 需要
    group_id: str | None = None           # SHARED profile 需要
    profile_type: str                     # INDEPENDENT / SHARED
    profile_name: str                     # 由 Gateway 预计算

@app.post("/api/controller/profiles")
async def create_profile(req: CreateProfileRequest, db: AsyncSession):
    """创建 Hermes Profile 并分配到某个 Engine Pod"""
    
    # 1. 找到 Engine Instance 下所有 Pod（agent_deployments where engine_instance_id）
    pods = await _get_instance_pods(req.engine_instance_id)
    
    # 2. 选择 Profile 数最少的 Pod
    target_pod = await _select_pod_by_load(pods)
    
    # 3. 分配端口（从 internal_port_map 中取下一个可用端口）
    port = await _allocate_port(target_pod)
    
    # 4. kubectl exec → hermes profile create + gateway start（Hermes 原生）
    await k8s_manager.exec_hermes_command(
        pod_name=target_pod.pod_name,
        commands=[
            f"hermes profile create {req.profile_name} --clone --clone-from base",
            f"hermes -p {req.profile_name} config set api_server.port {port}",
            f"hermes -p {req.profile_name} gateway start",
        ]
    )
    
    # 5. 更新 Pod 的 nginx upstream 配置
    await k8s_manager.update_nginx_config(target_pod, req.profile_name, port)
    
    # 6. 创建 AgentProfile 记录
    profile = AgentProfile(
        agent_id=req.agent_id,
        engine_instance_id=req.engine_instance_id,
        deployment_id=target_pod.id,
        profile_name=req.profile_name,
        profile_type=req.profile_type,
        user_id=req.user_id,
        group_id=req.group_id,
        hermes_home=f"/opt/data/profiles/{req.profile_name}",
        internal_port=port,
    )
    db.add(profile)
    await db.commit()
    
    return {"profile_name": req.profile_name, "pod_name": target_pod.pod_name, "port": port}

@app.delete("/api/controller/profiles/{profile_id}")
async def delete_profile(profile_id: str):
    """停止 Profile gateway 并清理"""
    # kubectl exec → hermes -p {name} gateway stop
    # kubectl exec → hermes profile delete {name} --yes
    # 更新 internal_port_map（释放端口）
    # 删除 AgentProfile 记录
```

### 9.2 K8s Manager 新增方法

```python
# services/controller/app/k8s_manager.py（新增）

async def exec_hermes_command(self, pod_name: str, commands: list[str]):
    """在 Pod 内执行 Hermes CLI 命令（复用 Hermes 原生能力）"""
    for cmd in commands:
        exec_cmd = ["/bin/bash", "-c", cmd]
        # 使用现有 exec 机制（exec_write_file 同款）
        # ...

async def update_nginx_config(self, pod: dict, profile_name: str, port: int):
    """更新 Pod 内 nginx 的 upstream 配置并 reload"""
    # 读取现有 internal_port_map
    # 添加新映射: profile_name → port
    # 生成 nginx upstream + location 配置
    # kubectl exec → write nginx config
    # kubectl exec → nginx -s reload
```

### 9.3 闲置回收策略（两层）

**层 1 — Profile 级（Controller 主动管理）：**
- Controller 定期检查所有 Profile 的最后活跃时间
- 单个 Profile 空闲超过阈值（如 2h）→ `hermes -p {name} gateway stop`（s6 停止进程）
- 保留 Profile 目录数据
- 下次请求时 Gateway 触发 Profile 恢复：`hermes -p {name} gateway start`（秒级恢复）

**层 2 — Pod 级（现有逻辑）：**
- 当某个 Engine Instance 的所有 Pod 中所有 Profile 都空闲 → SUSPEND
- tar 所有 Pod 的数据 → MinIO → scale Deployment to 0

---

## 十、Pod 内架构（详细）

### 10.1 容器组成

基于 `nousresearch/hermes-agent:latest` 官方镜像，增加：

```
Container:
├── s6-overlay (PID 1)              ← Hermes 原生
│   ├── main-hermes (port 8642)     ← default profile
│   ├── gateway-emp001 (port 8643)  ← s6 动态注册
│   ├── gateway-emp002 (port 8644)
│   └── gateway-{profile} (port {n})
│
├── nginx sidecar (port 8642)       ← 我们添加的
│   └── X-Hermes-Profile → 内部端口路由
│
└── /opt/data/                      ← emptyDir
    ├── profiles/
    │   ├── base/                   ← 共享配置模板
    │   ├── {profile_name}/         ← 独立 Profile 目录
    │   │   ├── config.yaml
    │   │   ├── .env
    │   │   ├── SOUL.md
    │   │   ├── sessions/
    │   │   ├── memory/
    │   │   ├── state.db
    │   │   └── gateway_state.json
    │   └── ...
    └── logs/gateways/{profile}/current
```

### 10.2 Pod 启动流程

```
1. s6-overlay /init 启动
2. 运行 /etc/cont-init.d/01-hermes-setup:
   - 初始化 /opt/data/.env（API_SERVER_KEY 等）
   - 创建 base profile
   - 写入 MODEL_PROVIDERS_JSON → base profile config
3. 运行 /etc/cont-init.d/02-reconcile-profiles:
   - 扫描 /opt/data/profiles/*/gateway_state.json
   - 重建 s6 service slots
   - 恢复之前 running 的 profile gateways
4. 启动 nginx sidecar（带初始路由表）
5. s6 启动 main-hermes gateway（default profile, port 8643）
6. nginx 监听 8642 对外
```

### 10.3 动态 Profile 创建（运行时）

```
Controller:
  1. 选择目标 Pod（load-based selection）
  2. kubectl exec {pod} -- hermes profile create {name} --clone --clone-from base
     → s6 自动注册 /run/service/gateway-{name}/
  3. kubectl exec {pod} -- hermes -p {name} config set api_server.port {port}
  4. kubectl exec {pod} -- hermes -p {name} gateway start
     → s6-svc 启动 gateway 进程
  5. kubectl exec {pod} -- 写入 nginx upstream 配置
  6. kubectl exec {pod} -- nginx -s reload
```

---

## 十一、Engine Instance 扩容/缩容

### 11.1 扩容（增加对等 Pod）

```
1. Admin 在管理台将 EngineInstance.max_replicas 调大
2. Controller 检测到变更 → kubectl scale deployment {instance_name}
3. 新 Pod 启动（s6 + nginx）
4. Controller 将新 Pod 注册到 agent_deployments 表
5. 后续新 Profile 可能被调度到新 Pod
6. 已有 Profile 不迁移（避免中断）
```

### 11.2 缩容（减少 Pod）

```
1. Admin 调小 max_replicas
2. Controller 选择要下线的 Pod（Profile 最少的）
3. 将该 Pod 上所有 Profile 迁移到其他 Pod:
   a. 在新 Pod 上创建同名 Profile（hermes profile create --clone）
   b. 启动 gateway（hermes gateway start）
   c. 更新 AgentProfile.deployment_id
   d. 在旧 Pod 上停止 gateway（hermes gateway stop）
4. 缩容 Deployment
```

---

## 十二、测试用例：东风猛士门店（完整走查）

### 12.1 初始配置

```
1. 创建 3 个 UserGroup:
   - 武汉光谷店 (gg-001)、武汉1号店 (no1-001)、武汉汉口店 (hk-001)
   - 每个 Group 添加相应员工

2. 创建 3 个 EngineInstance:
   - 光谷引擎实例 (ei-gg), 1号店引擎实例 (ei-no1), 汉口引擎实例 (ei-hk)
   - 每个: min_replicas=1, max_replicas=3, engine_type=HERMES

3. 通过 Clone 创建 3×3=9 个 Agent（每个 Agent 一个门店）:
   - 先创建模板: 猛士销售智能体(模板), 猛士店长智能体(模板), 猛士运营智能体(模板)
   - Clone 到各门店:
     - 猛士销售-光谷 (engine_instance=ei-gg), 猛士销售-1号店 (ei-no1), 猛士销售-汉口 (ei-hk)
     - 猛士店长-光谷 (ei-gg), 猛士店长-1号店 (ei-no1), 猛士店长-汉口 (ei-hk)
     - 猛士运营-光谷 (ei-gg), 猛士运营-1号店 (ei-no1), 猛士运营-汉口 (ei-hk)
   - 每个 access_scope=USER_GROUP, 关联对应的一个 UserGroup

4. 系统自动创建 Channels:
   - 每个 Agent 按 scope 自动创建 Channel
   - 例如: 猛士销售-光谷 → 1 个 feishu channel (scope=gg-001, profile_type=INDEPENDENT)
   - 总计 9 个 Channels（每 Agent 至少 1 个）
```

### 12.2 运行时行为

```
用户A（光谷店销售员工）首次发消息:
  1. Gateway 收到请求
  2. JWT 提取: user_id=A, X-Agent-ID=猛士销售
  3. 查询 Channels: agent=销售 + user belongs to gg-001
     → 匹配到 Channel: engine_instance=ei-gg, scope=gg-001
  4. 确定 profile_name: {sales_short}-{gg_short}-{A_short}
  5. 查询 agent_profiles: 不存在
  6. 调 Controller POST /api/controller/profiles
  7. Controller:
     a. 找到 ei-gg 的 Pod（目前 1 个）
     b. kubectl exec → hermes profile create A_profile
     c. kubectl exec → hermes -p A_profile gateway start (port 8643)
     d. 更新 nginx config + reload
     e. 创建 AgentProfile 记录
     f. 返回 {pod_name, port}
  8. Gateway 构造 upstream: http://{pod_ip}:8642
  9. 注入 X-Hermes-Profile header
  10. 转发 → nginx → 127.0.0.1:8643 (Hermes for profile A)

用户A 第二次发消息:
  1-4. 同上
  5. 查询 agent_profiles: 已存在 → 直接获取 pod_name + port
  6. Gateway 缓存命中 → 直连目标 Pod
  7-9. 同上
```

### 12.3 Pod 分布（光谷门店，100 名销售）

```
光谷引擎实例 (ei-gg):
  部署: engine-hermes-3f7a2e (replicas=5，根据 §2.3 公式)
  
  Pod-0 (26 profiles): gateway-[emp001..emp020, mgr-gg, ...]
  Pod-1 (26 profiles): gateway-[emp021..emp040, ...]  
  Pod-2 (26 profiles): gateway-[emp041..emp060, ...]
  Pod-3 (24 profiles): gateway-[emp061..emp080, ...]
  Pod-4 (24 profiles): gateway-[emp081..emp100, shared-ops, shared-sales]
  
  总计: 102 Profiles / 5 Pods ≈ 20 Profiles/Pod

1号店引擎实例 (ei-no1, 50名销售):
  Pod-0..Pod-1 (2 Pods): 分配 52 Profiles

汉口引擎实例 (ei-hk, 30名销售):
  Pod-0..Pod-1 (2 Pods): 分配 32 Profiles
```

---

## 十三、迁移策略

### Phase 1: EngineInstance 基础设施（1 周）
- EngineInstance 模型 + CRUD API + Admin 页面
- 内置 "Hermes-标准版" EngineInstance
- 现有 Agent 自动关联到默认 EngineInstance

### Phase 2: Agent + Channel 模型重构（1 周）
- Agent 模型改造（DROP engine_type/config, ADD model_config/skill_config/memory_config）
- Agent-EngineInstance 多对一关联（FK）
- Channel 模型改造（ADD scope_type/profile_type/engine_instance_id）
- 数据迁移脚本 + 验证

### Phase 3: 引擎容器改造（1-2 周）
- Dockerfile：Extend Hermes 官方镜像 + nginx
- entrypoint：初始化 base profile + 启动 nginx
- Pod 内 nginx 动态配置模板

### Phase 4: Controller 重构（1-2 周）
- Profile 生命周期 API（create/delete/suspend/resume）
- ProfileScheduler（Pod 选择 + 迁移）
- K8sManager：exec hermes CLI + nginx 配置管理
- 两层闲置回收

### Phase 5: Gateway 重构（1-2 周）
- ProfileResolver 模块
- Proxy + Channel Dispatcher 改造
- Profile 缓存 + 安全校验

### Phase 6: 前端 + 测试（1-2 周）
- EngineInstance 管理页
- Agent 表单改为选择 EngineInstance（多选）+ 模型/技能配置
- Channel 管理增强（scope + profile_type）
- 东风猛士门店 E2E 测试

---

## 十四、验证方案

1. **单元测试**：ProfileResolver 路由匹配、ProfileScheduler 调度算法
2. **集成测试**：
   - 创建 Agent → 绑定 EngineInstance → 用户访问 → Profile 自动创建
   - 同一 Pod 内多个 Profile 的 session/memory 隔离验证
   - Pod 故障 → Profile 自动迁移
3. **E2E 测试**：东风猛士门店场景（3 stores × 5 users = 15 profiles）
4. **性能测试**：单 Pod 内 N 个 Hermes gateway 进程的资源消耗曲线
5. **安全测试**：X-Hermes-Profile 注入攻击、跨 Profile 访问

---

## 附录：实施状态（2026-06-16）

### 已完成 ✅
- [x] V2 Docker 镜像（`Dockerfile.v2` + `entrypoint-v2.sh`）构建并部署
- [x] `entrypoint-v2.sh` 目录权限修复（`chown hermes:hermes`）
- [x] `im_user_bindings` 表 — 企微用户 ID → 平台 UUID 映射
- [x] `profile_resolver` IM 用户查询 + `resolved_user_id` 字段
- [x] Controller `ensure_profile` API（幂等创建）
- [x] Pod 调度：`_get_instance_pods` / `_select_pod_by_load`
- [x] nginx 配置动态更新（`update_nginx_config`）
- [x] port_map 持久化（JSON 列新 dict 引用，避免 SQLAlchemy 不追踪原地修改）
- [x] Gateway 转发带 `X-Hermes-Profile` 头
- [x] 确定性 Session ID（SHA256，跨重启不变）
- [x] V2 镜像 CI 构建（手动触发）
- [x] 企业微信门店助手 3 用户 Profile 隔离验证通过

### 未完成 ❌
- [ ] 管理后台 IM 渠道绑定界面（用户编辑页加 IM 绑定区域）
- [ ] Profile 动态销毁 / 端口回收（LRU 策略）
- [ ] 超 `max_profiles_per_pod` 自动扩 Pod
- [ ] Pod 重启后已有 Profile 自动重建
- [ ] `UA_API_SERVER_KEY` 注入 Controller 部署
- [ ] 已有测试中 2 个 `TestStreaming` 用例修复
- [ ] 性能基准测试（单 Pod N 个 gateway 的资源消耗）
