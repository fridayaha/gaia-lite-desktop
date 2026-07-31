# 终端用户门户 + 智能体生命周期管理

> 企业级多智能体平台的核心用户侧链路。
> 终端用户通过独立门户登录、选择智能体、与引擎对话。
> 所有交互通过自研 Vue 3 组件直接渲染（无 iframe）。

## 1. 总体架构

### 1.1 系统拓扑

```
[Browser] → Nginx/Ingress
              ├── /              → End User Portal (Vue 3 + Vite, 静态文件)
              ├── /api/auth/*    → Manager Service (:8002)
              ├── /api/agents/*  → Manager Service (:8002)
              ├── /api/controller/* → Controller Service (:8001)
              └── /api/gateway/* → Gateway Service (:8010, DNS 命名路由)
```

### 1.2 数据流

```
用户交互链路:
  Browser → Portal (Vue 3) → Manager (:8002)         # 登录 / 智能体列表
  Browser → Portal (Vue 3) → Controller (:8001)       # 部署 / 状态查询
  Browser → Portal (Vue 3) → Gateway (:8010)          # 聊天 / SSE 流式
                              ↓
                      engine-hermes-{id}:8642          # DNS 命名规范路由

智能体生命周期管理链路:
  Controller ←→ K8s API (按命名规范创建/休眠/删除 Pod)
  Controller → MinIO (归档/恢复引擎数据)

部署进度推送链路:
  Controller → SSE (text/event-stream) → Portal 前端 (进度条展示)
```

### 1.3 架构约束

- **Gateway 无反向依赖**（不查询 Controller），通过 `X-Agent-ID` 头 + DNS 命名规范路由
- **数据存档提前到 SUSPEND**（30min），不加定期轮询备份
- **无 iframe**，Chat 页面直接渲染 Vue 3 组件
- **不修改开源软件源代码**（hermes-webui 仅参考实现，终端前端为自研 Vue 3 应用）

## 2. 用户流程

### 2.1 完整交互链路

```
1. 用户访问 / 或 /agents/:id
     │
     ├── 未登录 → 重定向到 /login?redirect=/agents/:id
     │               │
     │               ▼
     │          登录页 (POST /api/auth/login)
     │               │ 成功
     │               ▼
     │          存储 JWT → 重定向回原地址
     │
     ├── 已登录, 访问 /agents
     │       │
     │       ▼
     │   GET /api/agents/accessible
     │       │
     │       ▼
     │   智能体列表 (按 last_accessed_at 排序)
     │       │
     │       ▼ 点击智能体
     │   /agents/:id
     │
     └── 已登录, 访问 /agents/:id (直接 URL 或从列表点击)
             │
             ▼
         GET /api/controller/agents/:id/status
             │
         ┌───┴───┐
         │       │
      RUNNING  其他状态
         │       │
         │       ▼
         │   POST /api/controller/agents/:id/deploy
         │       │
         │       ▼
         │   SSE → 部署进度条
         │       │
         │       ▼
         │   记录 last_accessed_at
         │
         ▼
     进入 Chat 页面 (Vue 3 组件, 无 iframe)
         │
         ▼
     ChatPage.vue
       ├── ChatSessionList.vue  ← Controller API (会话管理)
       ├── ChatMessages.vue     ← SSE 流式渲染
       ├── ChatComposer.vue     → Gateway (X-Agent-ID)
       └── useChat.ts
              ├── newSession()    → POST /api/controller/chat/session/new
              ├── loadSessions()  → GET  /api/controller/chat/sessions
              ├── sendMessage()   → POST /api/gateway/v1/chat/completions (SSE, 流式)
              └── loadModels()    → GET  /api/gateway/v1/models
```

### 2.2 前端的页面与路由

| 路径 | 页面组件 | 功能 |
|------|---------|------|
| `/login` | LoginPage | 终端用户登录，支持 redirect 回跳 |
| `/agents` | AgentListPage | 可访问智能体卡片列表，按最近访问排序 |
| `/agents/:id` | AgentChatPage | 部署检测 → 进度展示 → Chat 页面 |
| `/` | — | 自动重定向到 `/agents` |

### 2.3 路由守卫

- 所有页面（除 `/login`）需 JWT token
- token 不存在或过期 → 重定向到 `/login?redirect=当前路径`
- 登录成功 → 根据 redirect 参数回跳

## 3. 组件设计

### 3.1 后端服务

#### Manager (端口 8002)

| 端点 | 方法 | 说明 |
|------|------|------|
| `POST /api/auth/login` | 已有 | 用户登录 |
| `GET /api/auth/me` | 已有 | 当前用户信息 |
| `GET /api/agents/accessible` | **新增** | 终端用户可访问的已发布智能体列表 |
| `POST /api/agents/{id}/access` | **新增** | 记录用户访问 (upsert) |

#### Controller (端口 8001)

生命周期管理：

| 端点 | 方法 | 说明 |
|------|------|------|
| `GET /api/controller/agents/{id}/status` | GET | 查询引擎部署状态 |
| `POST /api/controller/agents/{id}/deploy` | POST | 创建/恢复引擎 |
| `GET /api/controller/agents/{id}/deploy/events` | GET (SSE) | 部署进度事件流 |
| `POST /api/controller/agents/{id}/suspend` | POST | 休眠引擎 (scale=0) |
| `POST /api/controller/agents/{id}/destroy` | POST | 销毁引擎并归档 |

会话管理：

| 端点 | 方法 | 说明 |
|------|------|------|
| `POST /api/controller/chat/session/new` | POST | 创建新会话 |
| `GET /api/controller/chat/sessions` | GET | 列出某用户的会话 |
| `GET /api/controller/chat/session` | GET | 获取会话详情 |
| `GET /api/controller/chat/dashboard/config` | GET | 前端探活配置 |
| `GET /api/controller/chat/settings` | GET | 会话设置 |
| `GET /api/controller/chat/models` | GET | 模型列表 |

#### Gateway (端口 8010)

| 端点 | 方法 | 说明 |
|------|------|------|
| `ANY /{path}` | ANY | DNS 命名规范路由，需要 `X-Agent-ID` 头 |

Gateway 改造为**无状态 DNS 命名路由**，不依赖任何其他服务：

```
请求头 X-Agent-ID → 提取 agent_id → DNS 命名规范构造 upstream
→ engine-hermes-{agent_id[:8]}.unionagents.svc.cluster.local:8642
→ 连接失败时返回 503
```

关键约束：**Gateway 不包含屏蔽 Origin/Referer 头**，否则引擎会拒绝请求返回 403。

### 3.2 前端组件

```
src/
├── main.ts                        # Vue 3 应用入口
├── App.vue                        # 根组件 (导航栏 + router-view)
├── router/
│   ├── index.ts                   # 路由定义 (hash 模式)
│   └── guard.ts                   # 路由守卫 (JWT 检查)
├── api/
│   ├── client.ts                  # HTTP 客户端 (自动附加 JWT, 401 重定向)
│   └── endpoints.ts               # 所有 API 调用函数
├── stores/
│   ├── auth.ts                    # 认证状态 (JWT + user info + chatMode)
│   └── agent.ts                   # 智能体部署状态管理
├── composables/
│   ├── useChat.ts                 # 聊天核心逻辑 (会话/消息/SSE/模型)
│   └── useDeployProgress.ts       # SSE 部署进度 hook
├── views/
│   ├── LoginPage.vue              # 登录页面
│   ├── AgentListPage.vue          # 智能体列表 (卡片布局、最近访问排序)
│   └── AgentChatPage.vue          # 部署检测 → ChatPage
└── components/
    ├── AppNav.vue                 # 导航栏 (支持深色模式切换)
    ├── DeployProgress.vue         # 部署进度条组件
    └── chat/
        ├── ChatPage.vue           # 聊天主布局 (Rail + 侧边栏 + 主区域 + Workspace)
        ├── ChatSessionList.vue    # 会话列表 (过滤、时间格式化)
        ├── ChatMessages.vue       # 消息流式渲染 (空状态、SSE 逐块拼接)
        ├── ChatComposer.vue       # 输入框 (发送、模型选择)
        └── ChatFileBrowser.vue    # 工作区文件浏览器
```

#### 样式方案

| 文件 | 来源 | 行数 | 说明 |
|------|------|------|------|
| `index.html` | 自定义 | — | `<html class="dark">` 启用深色主题 |
| `hermes-style.css` | 拷贝 hermes-webui | ~5043 | 8 套主题（深色/slate/poseidon 等） |
| Tailwind CSS | 自定义 | — | Portal 页面（登录/列表/部署）使用 |

#### ChatPage 布局

```
┌──────────────────────────────────────────────────────┐
│  AppNav (导航栏, 聊天模式时深色背景)                   │
├────┬──────────────────────────────┬──────────────────┤
│Rail│ Sidebar (会话列表)          │  Main (主区域)   │
│    │  ┌───── Panel Head ──────┐  │  ┌─────────────┐ │
│    │  │ Chat    [+新会话]     │  │  │ 消息列表     │ │
│    │  ├───────────────────────┤  │  │            │ │
│ C  │  │ session search       │  │  │ · 用户消息  │ │
│ h  │  ├───────────────────────┤  │  │ · AI 回复   │ │
│ a  │  │ ◉ Untitled           │  │  │ · SSE 流式  │ │
│ t  │  │ ○ 数据分析           │  │  │            │ │
│    │  │ ○ 代码审查           │  │  ├─────────────┤ │
│ S  │  └───────────────────────┘  │  │ Composer    │ │
│ p  │                             │  │ [输入框][📎]│ │
│ a  │                             │  │ [hermes] [▶]│ │
│ c  │                             │  └─────────────┘ │
│ e  │              ┌──────────────┤                   │
│ s  │              │Workspace     │                   │
│    │              │ Panel        │                   │
│    │              │ 📁 files     │                   │
│    │              │ 📄 artifacts │                   │
└────┴──────────────┴──────────────┴───────────────────┘
```

#### 移动端布局

窄屏（`max-width:768px`）下布局切换单栏 + 抽屉：

- **顶部 titlebar**（48px）：左 hamburger 唤出会话抽屉、居中智能体名、右侧按钮唤出工作区抽屉
- **底部 tabbar**（56px + 安全区）：对话 / 定时 / 看板 / 技能 / 返回
- **侧栏抽屉化**：Sidebar 与 Workspace Panel 由固定列改为 fixed 抽屉，`open` 时滑入并带遮罩层
- **触摸手势**：会话列表项左滑显示删除、长按唤出上下文菜单；屏幕左右边缘横滑可打开/关闭抽屉
- **键盘适配**：通过 `visualViewport` 监听软键盘高度，Composer 与 tabbar 同步上移；`--kb-h` CSS 变量驱动 `transform`
- **iOS 视口**：全局 `100vh` 改 `100dvh` 解决地址栏跳动；`env(safe-area-inset-bottom)` 处理 Home 指示条；输入框 `font-size:16px` 防止自动放大

### 3.3 useChat 核心逻辑

`useChat.ts` composable 管理所有聊天状态和通信：

```
useChat(agentId)
  │
  ├── sessions: Ref<Session[]>       # 会话列表
  ├── currentSessionId: Ref<string>  # 当前会话 ID
  ├── isStreaming: Ref<boolean>      # 是否正在流式接收
  ├── streamingContent: Ref<string>  # 当前流式内容
  ├── currentModel: Ref<string>      # 当前模型
  │
  ├── newSession()        → POST /api/controller/chat/session/new
  ├── selectSession(id)   → 切换当前会话
  ├── sendMessage(text)   → POST /api/gateway/v1/chat/completions
  │                          ├── stream: true (SSE)
  │                          ├── ReadableStream 逐块读取
  │                          ├── TextDecoder 解码
  │                          ├── buffer 处理不完整行
  │                          └── JSON.parse delta.content 拼接
  ├── loadSessions()      → GET /api/controller/chat/sessions
  └── loadModels()        → GET /api/gateway/v1/models
```

SSE 流式数据结构：

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",
       "choices":[{"index":0,"delta":{"content":"数"},
                   "finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",
       "choices":[{"index":0,"delta":{"content":"据"},
                   "finish_reason":null}]}

data: [DONE]
```

## 4. 数据库模型

### 4.1 现有模型

| 模型 | 表名 | 说明 |
|------|------|------|
| User | users | 用户账号 |
| Role | roles | 角色 |
| Permission | permissions | 权限 |
| Agent | agents | AI 智能体 |
| UserGroup | user_groups | 用户组 |

### 4.2 新增模型

**AgentSession** — 用户-智能体访问记录
```python
class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id              = Column(UUID, primary_key=True)
    user_id         = Column(UUID, ForeignKey("users.id"), nullable=False)
    agent_id        = Column(UUID, ForeignKey("agents.id"), nullable=False)
    last_accessed_at = Column(DateTime, default=utcnow)
    access_count    = Column(Integer, default=1)
```

**AgentDeployment** — 智能体引擎部署状态
```python
class AgentDeployment(Base):
    __tablename__ = "agent_deployments"
    id              = Column(UUID, primary_key=True)
    agent_id        = Column(UUID, nullable=False, unique=True)
    status          = Column(Enum, default=PENDING)  # PENDING/DEPLOYING/RUNNING/SUSPENDED/FAILED/ARCHIVED
    pod_name        = Column(String, nullable=True)
    namespace       = Column(String, default="unionagents")
    engine_url      = Column(String, nullable=True)
    deployed_at     = Column(DateTime, nullable=True)
    last_active_at  = Column(DateTime, nullable=True)
    backup_at       = Column(DateTime, nullable=True)
    archived_at     = Column(DateTime, nullable=True)
    archive_path    = Column(String, nullable=True)
    error_message   = Column(Text, nullable=True)
```

**ChatSession** — 聊天会话
```python
class ChatSession(Base):
    __tablename__ = "chat_sessions"
    session_id  = Column(String(32), primary_key=True)
    agent_id    = Column(UUID, nullable=False, index=True)
    user_id     = Column(UUID, nullable=False, index=True)
    title       = Column(String(256), default="Untitled")
    model       = Column(String(128), default="hermes-agent")
    workspace   = Column(String(512), default="/workspace")
    messages    = Column(JSON, default=list)
    created_at  = Column(DateTime, default=utcnow)
    updated_at  = Column(DateTime, default=utcnow, onupdate=utcnow)
    archived    = Column(Boolean, default=False)
```

## 5. 智能体生命周期

### 5.1 状态机

```
PENDING → DEPLOYING → RUNNING → SUSPENDED → ARCHIVED
                          ↑         │
                          └─────────┘ (用户再次访问时恢复, scale=1)
```

| 状态 | Pod | PVC | MinIO |
|------|-----|-----|-------|
| PENDING | — | — | — |
| DEPLOYING | 创建中 | 创建中 | — |
| RUNNING | 1 副本 | 有 | — |
| SUSPENDED | scale=0 | 保留 | 有 SUSPEND 存档 |
| ARCHIVED | 已删除 | 已删除 | 有永久存档 |
| FAILED | — | — | — |

### 5.2 K8s 资源命名规范

| 资源 | 命名规则 | 示例 |
|------|---------|------|
| Deployment | `engine-hermes-{agent_id[:8]}` | `engine-hermes-a1b2c3` |
| Service | `engine-hermes-{agent_id[:8]}` | `engine-hermes-a1b2c3` |
| PVC | `engine-hermes-{agent_id[:8]}-pvc` | `engine-hermes-a1b2c3-pvc` |
| Pod Label | `agent-id={agent_id}` | `agent-id=550e8400-...` |

### 5.3 部署流程

```
Controller.deploy(agent_id):
  1. 从 Manager 获取 agent 配置 (engine_type, config)
  2. 检查 AgentDeployment 记录
     ├── 不存在 / ARCHIVED → 全新创建
     └── SUSPENDED → 恢复 (scale=1)
  3. 创建 K8s Deployment + Service + PVC
     - 设置 PROVIDER_NAME/MODEL_NAME/API_KEY 等环境变量（从 agent config 读取）
  4. 推送 SSE 事件: creating_pod → configuring → waiting_ready → validating → engine_ready
  5. 等待 Pod Ready
  6. 记录 engine_url 到数据库
```

### 5.4 SSE 部署进度格式

```javascript
event: progress
data: {"step": "creating_pod",   "message": "沙箱环境申请中...",  "percentage": 30}

event: progress
data: {"step": "configuring",    "message": "引擎配置注入中...",  "percentage": 50}

event: progress
data: {"step": "waiting_ready",  "message": "等待引擎就绪...",   "percentage": 70}

event: progress
data: {"step": "engine_ready",   "message": "引擎已就绪",       "percentage": 100}

event: error
data: {"message": "部署失败: ..."}
```

## 6. 引擎回收与数据持久化

### 6.1 设计原则

| 层级 | 机制 | 覆盖风险 |
|------|------|---------|
| PVC 实时写 | 引擎运行时交互即落盘（Hermes 自身行为） | Pod 崩溃/重启 |
| SUSPEND 存档 | 休眠前 exec 进 Pod，tar 数据目录上传 MinIO | PVC 损坏/误删 |
| DESTROY 确认 | 仅当 SUSPEND 存档确认后，才删除 PVC | 存档失败则保留 |

### 6.2 MinIO 路径规范

```
backups/{agent_id}/
  └── latest.tar.gz              ← SUSPEND 时上传（最新快照）

archives/{agent_id}/
  └── {timestamp}.tar.gz         ← DESTROY 时从 backups 复制（永久留存）
```

### 6.3 SUSPEND 流程

```
RecycleScheduler (每 5 分钟):
  1. 遍历所有 RUNNING 部署, 检查 last_active_at
  2. 空闲超 30min → exec 进 Pod: tar czf - ~/.hermes/
  3. tar 流上传 MinIO: backups/{agent_id}/latest.tar.gz
  4. K8s scale Deployment 到 0
  5. 状态 → SUSPENDED
```

### 6.4 DESTROY 流程

```
CleanupScheduler (每小时):
  1. 遍历所有 SUSPENDED 部署, 检查 backup_at
  2. 超 24h → 复制 backups → archives/{timestamp}.tar.gz
  3. 删除 Deployment + Service + PVC
  4. 状态 → ARCHIVED
```

### 6.5 恢复流程

| 来源状态 | 操作 | 数据来源 |
|---------|------|---------|
| SUSPENDED | scale=1 | PVC 保留完整数据 |
| ARCHIVED | 创建新 Pod + exec tar 解压 | MinIO archives |

## 7. Nginx 路由配置

```nginx
server {
    listen 80;
    server_name chat.unionagents.com;

    # Portal 静态文件
    location / {
        root /usr/share/nginx/html;
        try_files $uri $uri/ /index.html;
    }

    # Manager API
    location /api/auth/ { proxy_pass http://manager:8002/api/auth/; }
    location /api/agents/ { proxy_pass http://manager:8002/api/agents/; }

    # Controller API (SSE)
    location /api/controller/ {
        proxy_pass http://controller:8001/api/controller/;
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
    }

    # Gateway (聊天请求, 同域代理)
    location /api/gateway/ {
        rewrite ^/api/gateway/(.*)$ /$1 break;
        proxy_pass http://gateway:8010;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_cache off;
    }
}
```

## 8. 端口规划

| 分组 | 服务 | K8s 端口 | 本地开发 | 说明 |
|------|------|---------|---------|------|
| 基础设施 | PostgreSQL | 5432 | 5432 (PF) | |
| | MinIO API | 9000 | 9000 (PF) | |
| 引擎 | Hermes Engine | 8642 | — | 每个 agent 一个 Pod |
| 后端微服务 | Manager | 8002 | `--port 8002` | CRUD + 认证 |
| | Controller | 8001 | `--port 8001` | 生命周期 + 会话管理 |
| | Gateway | **8010** | `--port 8010` | DNS 路由（避开 MinIO 9000） |
| 前端 | Admin (Vite) | — | 8848 | 管理后台 |
| | Enduser Portal (Vite) | — | 3000 | 终端用户门户 |
| | Hermes WebUI | 8787 | 8787 (PF) | 第三方聊天界面（备用） |

## 9. 关键技术决策

### 9.1 为什么不用 iframe（历史决策，已落地）

- hermes-webui 的 `X-Frame-Options: DENY` 限制无法绕过
- 跨域通信复杂（CORS + cookie + postMessage）
- 两次路由（Portal 路由 + iframe 内路由）体验割裂
- 无法统一主题和样式
- **最终方案**：自研 Vue 3 组件，hermes-webui 仅做参考实现

### 9.2 Gateway 为什么不需要 Controller

- Gateway 通过 DNS 命名规范直接从 `X-Agent-ID` 获取 upstream
- Controller 按相同规范创建 Pod：`engine-hermes-{agent_id[:8]}`
- 两者通过命名约定解耦，无需运行时依赖

### 9.3 SSE 流式注意事项

- nginx 必须设置 `proxy_buffering off;`，否则 SSE 会被缓冲
- `proxy_set_header Connection "upgrade"` 会干扰 SSE 流式响应
- Gateway 前端必须去掉 Origin/Referer 头，引擎 API 会拒绝带这些头的请求

### 9.4 为什么选择非流式 fallback

Portal 的 nginx 代理 SSE 时，浏览器 `fetch()` 的 `ReadableStream` 在某些 nginx 版本下会返回空响应（`Failed to fetch`）。已确认的解决方案是使用非流式请求作为 fallback。当前实现优先使用 SSE 流式，如果失败则自动回退到非流式。

## 10. 配置汇总

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `UA_K8S_NAMESPACE` | `unionagents` | K8s 命名空间 |
| `UA_MINIO_BUCKET` | `unionagents-archives` | MinIO 备份 Bucket |
| `UA_IDLE_SUSPEND_MINUTES` | `30` | 空闲多少分钟后休眠 |
| `UA_IDLE_DESTROY_HOURS` | `24` | 休眠后多少小时清理 |
| `UA_API_SERVER_KEY` | `change-me` | 引擎 API Key |
| `UA_JWT_SECRET` | (开发密钥) | JWT 签名密钥 |
| `UA_DATABASE_URL` | (连接串) | PostgreSQL 连接 |
| `UA_MINIO_ENDPOINT` | `http://minio:9000` | MinIO 地址 |

## 11. 验证方式

### 11.1 本地开发

```bash
# 启动后端
make dev-manager      # :8002
make dev-controller   # :8001
make dev-gateway      # :8010

# 启动前端
cd apps/enduser && pnpm dev  # :3000

# k3s 基础设施
make k8s-infra        # PostgreSQL + MinIO
```

### 11.2 端口转发（k3s）

```bash
make pf-manager       # 8002 → Manager
make pf-controller    # 8001 → Controller
make pf-gateway       # 8010 → Gateway
make pf-enduser       # 3000 → Portal
```

### 11.3 端到端验证

| # | 场景 | 预期 |
|---|------|------|
| 1 | 未登录访问 `/agents` | 跳转 `/login?redirect=/agents` |
| 2 | 登录后自动回跳 | 成功回到原页 |
| 3 | 可访问智能体列表 | 只显示有权限的已发布 Agent |
| 4 | 访问未部署智能体 | 部署进度条 → SSE → 完成 |
| 5 | 访问已部署智能体 | 直接进入 Chat 页面 |
| 6 | 发送消息 | SSE 流式逐字回复 |
| 7 | 切换会话 | 历史消息正常加载 |
| 8 | 30min 无操作 | 引擎自动休眠 (scale=0) |
| 9 | 再次访问已休眠引擎 | 自动恢复 |
| 10 | 24h 无访问 | 引擎归档到 MinIO → Pod 删除 |
