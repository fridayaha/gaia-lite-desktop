# RBAC-5 Gateway / OIDC 对接设计方案

版本：v0.1 | 日期：2026-05-27 | 状态：设计阶段，未实现

---

## 一、当前 Auth 模式分析

### 1.1 `HUB_AUTH_MODE` 三种模式行为

| 模式 | 行为 | 生产就绪 | 测试基线 |
|------|------|:---:|:---:|
| `dev` | 无 Header → 自动注入 `dev-admin`（platform_admin 角色，无 scope） | ❌ | ✅ 509 passed |
| `header` | 从 Gateway 注入的 Header 解析 AuthContext；无 Header → `is_authenticated=false` → RBAC 拒绝 | ✅（需 Gateway 配合） | ✅ |
| `none` | 完全绕过所有鉴权，Runtime Auth 也绕过 | ❌（仅运维/测试） | ✅ |

### 1.2 Header 模式信任的 Header 字段

```
X-Actor-ID          → actor_id
X-Actor-Type        → actor_type
X-Roles             → roles (comma-separated, auto-normalized)
X-Scopes            → scopes (comma-separated)
X-Groups            → groups
X-Workspace-ID      → workspace_id
X-Organization-ID   → organization_id
X-User-Name         → display_name
X-User-Email        → email
X-Service-Name      → service_name
X-Agent-ID          → agent_id
```

### 1.3 关键现状

| 问题 | 答案 |
|------|------|
| 是否校验 JWT？ | ❌ 不校验，Hub 完全信任 Header |
| 是否校验 Header 来源？ | ❌ 不校验，任何请求可直接写 Header |
| 能区分 user/service/agent？ | ⚠️ 通过 X-Actor-Type / X-Service-Name / X-Agent-ID 区分，但由调用方自行声明 |
| 是否支持 roles/scopes？ | ✅ 支持，Header / query 注入 |
| 日志是否带 actor_id？ | ✅ access log + event log 均已记录 |
| Dev mode 是否适合生产？ | ❌ 禁止 |
| None mode 是否适合生产？ | ❌ 禁止 |

### 1.4 当前生产风险

| 风险 | 等级 | 说明 |
|------|:---:|------|
| **Header 伪造** | high | header mode 下，客户端可直接写 X-Actor-ID / X-Roles，Hub 无任何校验 |
| **无 JWT 校验** | high | 无法验证调用方身份真实性 |
| **无 API Key 校验** | medium | Runtime Consumer 没有独立的认证凭据 |
| **Dev mode 可用于生产** | high | 默认 `dev` 模式自动签发 platform_admin |
| **Query 参数补充** | low | `get_runtime_auth_context` 可补充 query 参数，但 Header 优先 |

---

## 二、Gateway Header 注入模式设计（推荐 P1）

### 2.1 架构

```
Client / Runtime
    │
    │ Authorization: Bearer <jwt>  (或 API Key / Session)
    ▼
Gateway (Kong / Nginx / Apigee)
    ├── 校验 JWT / API Key / Session
    ├── 验证签名 / expiry / issuer / audience
    ├── 提取 identity → actor_id
    ├── 查询 IAM → roles, scopes, groups
    ├── 提取 workspace_id / organization_id
    │
    ├── ***** STRIP/OVERWRITE 客户端所有身份 Header *****
    │
    ├── 注入可信 Header:
    │     X-Actor-ID: user-456
    │     X-Actor-Type: user
    │     X-Roles: contributor,runtime_consumer
    │     X-Scopes: capability:discover,capability:resolve
    │     X-Workspace-ID: ws-789
    │     X-Organization-ID: org-001
    │     X-Groups: team-a,engineering
    │     X-User-Name: Alice
    │     X-User-Email: alice@example.com
    │     X-Request-ID: uuid4
    │
    └── 转发到 Hub
            │
            ▼
        Hub (HUB_AUTH_MODE=header)
            ├── AuthMiddleware: from_headers() → AuthContext
            ├── require_permission() → RBAC
            ├── require_runtime_permission() → Runtime Auth
            ├── ScopedCapabilityAccessPolicy → 资产过滤
            └── 执行请求
```

### 2.2 Gateway 必须遵守的安全规则

1. **STRIP 或 OVERWRITE** 客户端传入的所有 `X-*` 身份 Header
2. 新请求必须先通过 Gateway 认证才注入 Header
3. Hub 部署在 Gateway 后方，不接受外部直连
4. `HUB_AUTH_MODE=header` 不得直接暴露到公网

### 2.3 Hub 侧职责

- 只消费 Header，不校验 JWT
- 构造 AuthContext
- 执行 RBAC / CapabilityAccessPolicy
- 不存储密码 / token
- 不提供登录/登出页面
- 记录 actor_id 到所有日志

### 2.4 Header 规范（生产）

| Header | 类型 | 必填 | 说明 |
|--------|------|:---:|------|
| `X-Actor-ID` | string | ✅ | 调用方唯一标识 |
| `X-Actor-Type` | `user` / `service` / `agent` | ✅ | 调用方类型 |
| `X-Roles` | comma-separated | ✅ | Hub 角色列表 |
| `X-Scopes` | comma-separated | — | 细粒度 scope |
| `X-Workspace-ID` | string | — | 工作空间 |
| `X-Organization-ID` | string | — | 组织 |
| `X-Groups` | comma-separated | — | 组 |
| `X-User-Name` | string | — | 显示名 |
| `X-User-Email` | string | — | 邮箱 |
| `X-Service-Name` | string | — | 服务名（service account 时） |
| `X-Agent-ID` | string | — | Agent ID（agent 调用时） |

---

## 三、OIDC / JWT 校验模式设计（P2 备选）

### 3.1 两种方案对比

| 维度 | 方案 A：Gateway 校验 JWT | 方案 B：Hub 自行校验 JWT |
|------|--------------------------|---------------------------|
| 适用场景 | 平台集成部署（有统一 Gateway） | 独立部署（无 Gateway） |
| 认证逻辑位置 | Gateway | Hub |
| Hub 复杂度 | 低（只消费 Header） | 中高（需 JWKS、签名校验、expiry） |
| 依赖性 | 依赖 Gateway 正确配置 | 独立 |
| 安全性 | Gateway 撤防 = Hub 可被绕过 | Hub 自主校验 |
| 推荐优先级 | **P1** | P2 |

### 3.2 方案 B：Hub JWT 校验模式设计

新增 `HUB_AUTH_MODE=jwt`：

```
HUB_AUTH_MODE=jwt
HUB_OIDC_ISSUER="https://auth.example.com"
HUB_OIDC_AUDIENCE="hub-api"
HUB_OIDC_JWKS_URL="https://auth.example.com/.well-known/jwks.json"
HUB_OIDC_ROLE_CLAIM="roles"
HUB_OIDC_SCOPE_CLAIM="scope"
```

行为：
1. 从 `Authorization: Bearer <jwt>` 提取 token
2. 请求 JWKS endpoint 获取公钥（带缓存）
3. 校验签名、issuer、audience、expiry
4. 从 claims 提取 actor_id（sub）、roles、scopes
5. 构造 AuthContext
6. 校验失败 → 401 Unauthorized

**本阶段只设计，不实现。**

### 3.3 推荐路线

| 阶段 | 内容 |
|:---:|------|
| P1 | Gateway Header 注入模式（依赖平台 Gateway） |
| P2 | Hub JWT 校验模式（独立部署备选） |

---

## 四、Service Account / Runtime Consumer 认证

### 4.1 认证方式选项

| 方式 | 适用场景 | 复杂度 | 推荐 |
|------|----------|:---:|:---:|
| Gateway-issued service account token | 平台集成 | 低 | ✅ P1 |
| OIDC Client Credentials | 独立部署 / OIDC 环境 | 中 | ✅ P1 |
| API Key | 简化方案 | 低 | — 可考虑 |
| mTLS | 内部高安全部署 | 高 | P3 |

### 4.2 推荐策略

**P1（平台集成）**：Gateway 签发 service account token，注入 `X-Actor-Type: service` + `X-Service-Name: openclaw-runtime` + `X-Roles: runtime_consumer` + 对应 scopes。

**P1（独立部署）**：OIDC Client Credentials flow → 获取 access token → Hub JWT 模式校验。

**P2**：API Key 方案 — 生成 API Key → 轮换 → 审计。需额外设计 API Key 生成/验证/轮换机制。

### 4.3 Runtime Consumer 认证流程（P1 Gateway 模式）

```
Runtime Service
    │
    │ Authorization: Bearer <service-account-token>
    ▼
Gateway
    ├── 校验 service account token
    ├── 提取 service identity
    ├── 注入 Header:
    │     X-Actor-ID: svc-runtime-1
    │     X-Actor-Type: service
    │     X-Service-Name: openclaw
    │     X-Roles: runtime_consumer
    │     X-Scopes: capability:discover,capability:resolve
    │
    └── 转发到 Hub → Runtime API
```

---

## 五、角色 / Scope 映射

### 5.1 外部 IAM → Hub 角色映射

| 外部角色 | Hub 角色 | 说明 |
|----------|----------|------|
| `hub-admin` | `platform_admin` | 全局管理员 |
| `hub-owner` | `asset_owner` | 资产所有者 |
| `hub-contributor` | `contributor` | 贡献者 |
| `hub-sec-reviewer` | `security_reviewer` | 安全审核者 |
| `hub-biz-approver` | `business_approver` | 业务审批者 |
| `hub-publisher` | `publisher` | 发布者 |
| `hub-runtime` | `runtime_consumer` | 运行时调用方 |
| `hub-auditor` | `auditor` | 审计者 |

映射由 Gateway 或 IAM 配置维护。Hub 提供固定 role/permission 词表（`ROLE_PERMISSIONS` dict）。

### 5.2 外部 Scope → Hub Scope 映射

| 外部 scope | Hub scope | 说明 |
|------------|-----------|------|
| `hub.capability.discover` | `capability:discover` | 发现能力 |
| `hub.capability.resolve` | `capability:resolve` | 解析能力 |
| `hub.capability.manifest` | `capability:manifest` | 导出 manifest |
| `hub.capability.tool-definition` | `capability:tool_definition` | 导出 tool definition |

### 5.3 冲突处理

- Gateway/IAM 角色优先级高于 Hub 默认；
- 同名字段冲突时 Gateway 注入为准（因为 Hub 不吃客户端 Header）；
- role 映射失败时 Gateway 不注入该角色，Hub 视为无此角色。

### 5.4 Workspace / Organization 注入

- 由 Gateway 从 IAM token/claim 中提取并注入 Header；
- Hub 消费但不校验（P1 不做 workspace DB 过滤）。

---

## 六、生产安全要求

| # | 要求 | 优先级 |
|:---:|------|:---:|
| 1 | **禁止客户端直连 Hub 并伪造 Header** | blocking |
| 2 | **Gateway 必须 strip/overwrite 身份 Header** | blocking |
| 3 | **`HUB_AUTH_MODE=header` 仅部署在可信 Gateway 后方** | blocking |
| 4 | **`HUB_AUTH_MODE=none` 不得用于生产** | blocking |
| 5 | **`HUB_AUTH_MODE=dev` 不得用于生产** | blocking |
| 6 | **request_id 应由 Gateway 生成或透传** | high |
| 7 | **日志不得记录 Authorization / token / raw JWT** | blocking |
| 8 | **Hub 不存储 token / API Key / 密码** | blocking |

---

## 七、配置项设计

### 7.1 现有配置

```
HUB_AUTH_MODE=dev|header|none
```

### 7.2 Header 模式配置（当前无需新配置）

Hub 默认信任 Gateway 注入的 Header，Gateways 责任落实即可。

### 7.3 JWT 模式配置（P2 设计，不实现）

```
HUB_AUTH_MODE=jwt
HUB_OIDC_ISSUER="https://auth.example.com"
HUB_OIDC_AUDIENCE="hub-api"
HUB_OIDC_JWKS_URL="https://auth.example.com/.well-known/jwks.json"
HUB_OIDC_ROLE_CLAIM="roles"
HUB_OIDC_SCOPE_CLAIM="scope"
HUB_OIDC_JWKS_CACHE_TTL=300
HUB_OIDC_TOKEN_LEEWAY=30
```

---

## 八、测试计划（后续实现）

### 8.1 Gateway Header 模式测试

| # | 场景 | 预期 |
|:---:|------|------|
| 1 | header mode + actor_id + roles → 管理态 API | 200 |
| 2 | header mode + runtime_consumer + scope → Runtime API | 200 |
| 3 | header mode + 无 actor_id → 任意 API | 403 |
| 4 | header mode + 无 roles → 任意 API | 403 |
| 5 | header mode + 错误 role → 管理态 API | 403 |
| 6 | role mapping 生效（含 hyphen/space normalize） | ✅ |
| 7 | scope mapping 生效 | ✅ |
| 8 | Gateway strip 后 Header 不可伪造（文档说明） | — |

### 8.2 JWT 模式测试（P2）

| # | 场景 | 预期 |
|:---:|------|------|
| 1 | 有效 JWT + correct claims → API | 200 |
| 2 | 无效签名 → 拒绝 | 401 |
| 3 | 错误 issuer → 拒绝 | 401 |
| 4 | 错误 audience → 拒绝 | 401 |
| 5 | 过期 token → 拒绝 | 401 |
| 6 | roles/scopes 从 claims 正确映射 | ✅ |
| 7 | JWKS 缓存生效 | ✅ |

### 8.3 Runtime Service Account 测试

| # | 场景 | 预期 |
|:---:|------|------|
| 1 | runtime service token → discover | 200 |
| 2 | runtime service token 缺 scope → 403 |
| 3 | runtime service token → policy deny → 404 |
| 4 | audit log 记录 service_name | ✅ |
| 5 | service account 不能访问管理态 API | 403 |

---

## 九、文档更新计划

| 文档 | 更新内容 |
|------|----------|
| `docs/13_rbac_auth_integration_plan.md` | 补充 RBAC-5 设计，更新当前状态 |
| `docs/03_platform_integration.md` | 补充 Gateway Header 注入规范、安全要求 |
| `docs/08_roadmap_workload.md` | 更新 RBAC-5 状态为"已设计" |
| `docs/02_solution_design.md` | 补充 RBAC-5 设计完成 |
| `docs/12_observability_logging_design.md` | 补充 token/log 安全要求 |
| `docs/16_gateway_oidc_integration_design.md`（本文档） | 新增 |

---

## 十、不做事项

| 项 | 状态 |
|----|:---:|
| 写代码实现 JWT/OIDC 校验 | ❌ |
| 新增依赖（PyJWT / python-jose / oauthlib） | ❌ |
| 对接真实 OIDC Provider | ❌ |
| 实现 API Key 生成/验证 | ❌ |
| 实现 mTLS | ❌ |
| 修改前端 | ❌ |
| 修改 demo worktree | ❌ |
| 修改数据库 schema | ❌ |
| 修改管理态 RBAC | ❌ |
| 修改 Runtime API 鉴权 | ❌ |
