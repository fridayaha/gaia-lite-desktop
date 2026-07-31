# Hub 平台集成部署设计

版本：v0.4 | 日期：2026-05-29 | 状态：RBAC-4 已实现（Runtime Consumer role/scope + ScopedCapabilityAccessPolicy）。MT-0 多租户设计已完成（`docs/24_multi_tenancy_design.md`）。

---

## 一、部署形态

Hub 支持两种部署形态：

| 形态 | 说明 |
|------|------|
| 单独部署 | Hub 独立使用 PostgreSQL / 对象存储 / Gateway |
| 平台集成部署 | Hub 作为能力中心组件，使用统一底座的存储、日志、监控、鉴权、网关 |

---

## 二、平台集成架构

```
客户端/Runtime
       │
       ▼
 API Gateway（统一入口）
  ├── 注入 X-Request-ID
  ├── 注入 actor_id / workspace_id / organization_id
  ├── 鉴权 / 限流
  └── 路由到 Hub
       │
       ▼
 Hub（能力资产治理中心 + Runtime Discover）
  ├── 复用 Gateway 注入的 X-Request-ID
  ├── 消费 actor_id / workspace_id（通过 AuthContext）
  ├── stdout JSON log → 统一日志平台
  ├── /metrics → Prometheus（后续）
  ├── /health → Hermes 健康检查
  └── PostgreSQL（统一底座提供）
```

## 2A. 身份注入

### 2A.1 Header 注入模式（推荐 P1）

```
Gateway → 认证/鉴权 → Header 注入
  X-Request-ID: uuid4
  X-Actor-ID: user-123
  X-Roles: contributor,approver
  X-Workspace-ID: ws-456
  X-Organization-ID: org-789
```

- Gateway 负责 token 校验 / OIDC 认证；
- Hub 从 Header 解析 AuthContext，不自行校验；
- Header 不可从前端直接发出（由 Gateway 覆盖或过滤）；
- Dev Mode 下 Hub 跳过校验。

### 2A.2 OIDC Token 模式（P2）

```
Client → Authorization: Bearer <jwt>
       → Hub 校验 JWT → 解析 claims → AuthContext
```

- 适合无统一 Gateway 的独立部署；
- Hub 需集成 JWT 校验库；
- 实现复杂度高于 Header 模式。

### 2A.3 Dev Mode

```
HUB_AUTH_MODE=dev → admin 角色 → 所有 API 可用
```

- 仅限 localhost 或开发环境；
- 生产环境必须切换到 header 或 oidc 模式。

### 2A.4 Runtime API 鉴权（已实现 RBAC-4）

Runtime API 采用独立的鉴权策略分层：

| 层级 | 机制 | 状态 |
|------|------|:---:|
| 入口角色检查 | `require_runtime_permission` — 验证 `runtime_consumer` / `platform_admin` 角色 + scope | ✅ RBAC-4 |
| 资产级可见性过滤 | `ScopedCapabilityAccessPolicy` — 按角色过滤，P1 不做 workspace 过滤 | ✅ RBAC-4 |
| Policy deny 行为 | discover 静默排除；resolve/manifest/tool-definition 返回 404 隐藏资产存在性 | ✅ 已实现 |

- Runtime API 不受管理态 RBAC 约束，不使用管理态 `require_permission`；
- `platform_admin` 豁免 scope 检查；
- `manifest` 和 `tool-definition` 当前兼容 `capability:resolve` scope，未来可能收紧；
- workspace DB 过滤未实现，需后续 migration；
- OIDC / API Key 仍未实现。

---

## 三、日志串联

### 3.1 request_id 透传

```
Gateway/Hermes → [X-Request-ID] → Hub → [X-Request-ID 响应头]
```

- Hub 检测 X-Request-ID header，优先复用
- 如上游未传入，Hub 自动生成 uuid4
- 响应头始终返回 X-Request-ID
- contextvars 保存 request_id，供业务日志使用

### 3.2 全链路追踪

```
Gateway → Hub → Runtime
  [X-Request-ID 串联全部日志]
```

- 统一日志平台通过 X-Request-ID 跨服务搜索
- actor_id 由 Gateway 注入 header，后续在业务日志中记录

### 3.3 平台组件职责

| 组件 | 职责 |
|------|------|
| Gateway | 注入 X-Request-ID / actor_id，鉴权，路由 |
| Hermes | 部署管理，健康检查（消费 /health） |
| Hub | 能力治理 + Discover + stdout JSON log |
| 统一日志平台 | 采集 stdout，聚合，搜索，告警 |
| Prometheus | 抓取 /metrics（后续） |

---

## 四、部署约束

### 4.1 Hub 内部约束

- 不引入 Prometheus / Grafana / OpenTelemetry（Stage 7A 阶段）
- 日志只输出 stdout JSON，不写文件
- 不引入 Loki / ELK（由统一底座负责）
- 不内部实现 tracing

### 4.2 底座要求（平台集成模式）

| 底座能力 | Hub 接口 | 状态 |
|----------|----------|:---:|
| X-Request-ID 注入 | 复用 header | ✅ P1 |
| actor_id / roles 注入 | AuthContext | ✅ P1（Header 模式设计完成） |
| 日志采集 | stdout JSON | ✅ P1 |
| Prometheus | /metrics | 🔜 后续 |
| PostgreSQL | DATABASE_URL | 📋 待测 |
| 对象存储 | StorageAdapter 预留 | 📋 设计已完成（`docs/19_storage_adapter_design.md`），P1 LocalStorageAdapter 待实现 |
| 健康检查 | /health | ✅ |

---

## 五、多租户与数据隔离

### 当前状态

Hub 当前为单租户模式。`AuthContext.organization_id` / `workspace_id` 已通过 Header 注入、日志已捕获，但未在任何查询中使用。

### 设计路线

详见 `docs/24_multi_tenancy_design.md`。

| 阶段 | 内容 | 状态 |
|:---:|------|:---:|
| MT-0 | 多租户设计 | ✅ 已完成 |
| MT-1 | DB 新增 tenant 列 + migration | 待实现 |
| MT-2 | 管理态 workspace 过滤 | 待实现 |
| MT-3 | Runtime workspace + visibility_scope 过滤 | 待实现 |
| MT-4 | Storage tenant prefix | 待实现 |

### 与平台集成的关系

- 组织/工作空间标识应由 Gateway / IAM 注入 Header（`X-Organization-ID` / `X-Workspace-ID`）
- Hub 仅消费注入值，不自行推导或解析 tenant 拓扑
- 平台集成时应明确 tenant / workspace 模型，确保 Header 注入链路可靠
- Dev mode 下 `workspace_id` 可留空，不过滤

---

## 六、文档参考

| 文档 | 说明 |
|------|------|
| `docs/13_rbac_auth_integration_plan.md` | RBAC 身份认证对接方案 |
| `docs/07_rbac_approval_design.md` | RBAC 与审批设计 |
| `docs/24_multi_tenancy_design.md` | 多租户设计与数据模型影响分析 |
| `docs/02_solution_design.md` | 整体方案设计 |
