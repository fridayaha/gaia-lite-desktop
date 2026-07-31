# Hub RBAC / 身份认证对接方案

版本：v0.7 | 日期：2026-05-29 | 状态：RBAC-4 已实现。RBAC-5 Gateway/OIDC 已设计。MT-0 多租户设计已完成（`docs/24_multi_tenancy_design.md`），workspace-level role binding 待 MT-5。

---

## 一、当前身份 / 权限现状分析

### 1.1 已实现（截止 RBAC-3C-0）

| 能力 | 状态 | 位置 |
|------|:---:|------|
| **AuthContext 数据类**（14 字段） | ✅ | `backend/app/core/auth_context.py` |
| **AuthMiddleware**（dev/header/none 三模式） | ✅ | `backend/app/core/auth_middleware.py` |
| **contextvars**（跨模块取 AuthContext） | ✅ | `backend/app/core/auth_middleware.py` |
| **RBAC 权限系统**（8 角色 × 24 权限） | ✅ | `backend/app/core/rbac.py` |
| **管理态 RBAC 门**（26 个 Management API 已加 `require_permission`） | ✅ | 全部 `backend/app/api/*.py` |
| **CapabilityAccessPolicy Protocol** | ✅ | `backend/app/policies/capability_access.py` |
| **AllowAllCapabilityAccessPolicy**（默认） | ✅ | `backend/app/policies/capability_access.py` |
| **ApprovalPolicy Protocol** | ✅ | `backend/app/policies/approval_policy.py` |
| **AllowAllApprovalPolicy**（默认） | ✅ | `backend/app/policies/approval_policy.py` |
| **operator → actor_id 迁移** | ✅ | `backend/app/core/operator.py` |
| **operator_mismatch 事件日志** | ✅ | `backend/app/core/operator.py` |
| **actor_id 记录到 access log** | ✅ | `backend/app/core/logging.py` |
| **actor_id 记录到 event log** | ✅ | `backend/app/core/event_log.py` |
| **get_runtime_auth_context**（Runtime API 身份解析） | ✅ | `backend/app/core/auth_dependencies.py` |

### 1.2 未实现

| 能力 | 状态 | 计划 |
|------|:---:|------|
| Runtime Consumer RBAC 权限 | ✅（RBAC-4） | 已完成 |
| 对象级 ownership（own/other 区分） | ✅（RBAC-3D-2） | 已实现 |
| 四眼原则（提交者不能审批自己） | ✅（RBAC-3C） | 已实现，默认关闭 |
| Runtime 资产级可见性策略 | ✅（RBAC-4） | ScopedCapabilityAccessPolicy |
| workspace DB 过滤 | 📋 MT-0 设计完成，代码未实现（`docs/24_multi_tenancy_design.md`） |
| 真实 IAM / OIDC / JWT 校验 | ❌ | RBAC-5 |
| workspace-level role binding | 📋 MT-0 设计完成，待 MT-5 | 当前 RBAC role 仍是请求上下文全局角色，尚未实现 per-workspace role binding |
| waiver 机制 | ❌ | P3 |

### 1.3 当前结论

**当前没有真实身份认证（OIDC/JWT），也没有完整 RBAC 强制。Hub 已实现 RBAC 基础架构（角色/权限矩阵/中间件/策略接口）和管理态 RBAC。管理态默认使用 `dev` 模式——AuthMiddleware 自动注入 `platform_admin` 角色，所有 API 可用。Runtime API 已增加入口级 role/scope 检查（`require_runtime_permission`）和资产级 `ScopedCapabilityAccessPolicy` 过滤。**

简言之：基础设施已就绪，默认宽松。对接真实身份系统时将收紧。

---

## 二、RBAC 角色设计

### 2.1 默认角色定义

| # | 角色 | 标识 | 职责范围 |
|:---:|------|------|----------|
| 1 | **Platform Admin** | `platform_admin` | 全局配置、归档、回滚、紧急禁用、管理角色映射 |
| 2 | **Asset Owner** | `asset_owner` | 负责自己资产的维护、版本管理、提交审核、下架申请 |
| 3 | **Contributor** | `contributor` | 创建和编辑 draft，不能发布 |
| 4 | **Security Reviewer** | `security_reviewer` | 查看扫描报告、确认 high/blocking 风险、安全审批 |
| 5 | **Business Approver** | `business_approver` | 业务合规、可用性、归属角度审批 |
| 6 | **Publisher** | `publisher` | 将 approved 版本发布为当前生效版本、执行下架和回滚 |
| 7 | **Runtime Consumer** | `runtime_consumer` | Runtime/Agent/OpenClaw 运行态调用，仅 Discover/Resolve/Tool Definition |
| 8 | **Auditor** | `auditor` | 只读审计：审批/生命周期/扫描/导入/发布 |

### 2.2 角色灵活性说明

- **Security Reviewer + Business Approver → Approver**：小团队可将两者合并为单一 Approver 角色，由 IAM 同时映射两组权限。
- **Publisher → Owner 或 Admin 兼任**：小团队中发布人可由 Asset Owner 或 Platform Admin 兼任，无需独立角色。
- **Runtime Consumer 是 service account**：不应映射为真人用户，而是服务账号或 Gateway 注入的角色。
- **角色是默认建议**：实际部署时由外部 IAM 按企业组织映射到这些 Hub 角色，可裁剪或扩展。

### 2.3 角色规范

- 标识全部小写 + 下划线分隔：`platform_admin`、`asset_owner` 等。
- `from_headers()` 自动做 normalize：trim / lower / `-`→`_` / ` `→`_`。
- 兼容外部 IAM 传递含连字符/大写/空格的原始角色名。

---

## 三、动作权限矩阵

### 3.1 权限定义（`backend/app/core/rbac.py`）

| 权限标识 | 说明 | 分类 |
|----------|------|------|
| `asset:create` | 创建能力资产 | 管理态 |
| `asset:read` | 查看资产和版本 | 管理态 |
| `asset:update` | 更新资产元信息 | 管理态 |
| `asset:delete_draft` | 删除 draft 资产 | 管理态 |
| `asset:import` | 导入能力包 | 管理态 |
| `version:create` | 创建新版本 | 管理态 |
| `version:edit` | 编辑 draft 版本 | 管理态 |
| `version:delete` | 删除 draft 版本 | 管理态 |
| `scan:run` | 手动触发扫描 | 管理态 |
| `scan:read` | 查看扫描报告 | 管理态 |
| `review:submit` | 提交审核 | 管理态 |
| `review:approve` | 审批通过 | 管理态 |
| `review:reject` | 驳回 | 管理态 |
| `review:request_change` | 请求修改 | 管理态 |
| `lifecycle:publish` | 发布版本 | 管理态 |
| `lifecycle:disable` | 禁用/下架 | 管理态 |
| `lifecycle:archive` | 归档 | 管理态 |
| `lifecycle:rollback` | 回滚到历史版本 | 管理态 |
| `relation:create` | 创建关系 | 管理态 |
| `relation:delete` | 删除关系 | 管理态 |
| `export:download` | 下载版本包/管理态导出 | 管理态 |
| `audit:read` | 查看审计记录 | 管理态 |
| `admin:configure` | 系统配置（预置/标签/角色映射等） | 管理态 |

Runtime API 权限由 CapabilityAccessPolicy 独立控制，不在 RBAC 权限矩阵中。

### 3.2 角色 × 权限矩阵（已实现）

| 权限 | Admin | Owner | Contributor | SecReviewer | Approver | Publisher | RuntimeConsumer | Auditor |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `asset:create` | ✅ | ✅ | ✅ | — | — | — | — | — |
| `asset:read` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| `asset:update` | ✅ | ✅ | — | — | — | — | — | — |
| `asset:delete_draft` | ✅ | — | — | — | — | — | — | — |
| `asset:import` | ✅ | ✅ | ✅ | — | — | — | — | — |
| `version:create` | ✅ | ✅ | ✅ | — | — | — | — | — |
| `version:edit` | ✅ | ✅ | ✅ | — | — | — | — | — |
| `version:delete` | ✅ | ✅ | — | — | — | — | — | — |
| `scan:run` | ✅ | ✅ | ✅ | ✅ | — | — | — | — |
| `scan:read` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| `review:submit` | ✅ | ✅ | ✅ | — | — | — | — | — |
| `review:approve` | ✅ | — | — | ✅ | ✅ | — | — | — |
| `review:reject` | ✅ | — | — | ✅ | ✅ | — | — | — |
| `review:request_change` | ✅ | — | — | ✅ | ✅ | — | — | — |
| `lifecycle:publish` | ✅ | — | — | — | — | ✅ | — | — |
| `lifecycle:disable` | ✅ | — | — | — | — | ✅ | — | — |
| `lifecycle:archive` | ✅ | — | — | — | — | — | — | — |
| `lifecycle:rollback` | ✅ | — | — | — | — | ✅ | — | — |
| `relation:create` | ✅ | ✅ | ✅ | — | — | — | — | — |
| `relation:delete` | ✅ | ✅ | — | — | — | — | — | — |
| `export:download` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| `audit:read` | ✅ | — | — | — | — | — | — | ✅ |
| `admin:configure` | ✅ | — | — | — | — | — | — | — |

### 3.3 Runtime API 权限矩阵（已实现 RBAC-4）

| 动作 | Runtime Consumer | 管理态角色 | scope |
|------|:---:|:---:|------|
| `discover` | ✅（需 `capability:discover` scope） | ✅（platform_admin 豁免 scope） | `capability:discover` |
| `resolve` | ✅（需 `capability:resolve` scope） | ✅（platform_admin 豁免 scope） | `capability:resolve` |
| `tool-definition` | ✅（需 `capability:tool_definition` OR `capability:resolve`） | ✅ | `capability:tool_definition`（可兼容 `capability:resolve`） |
| `manifest` | ✅（需 `capability:manifest` OR `capability:resolve`） | ✅ | `capability:manifest`（可兼容 `capability:resolve`） |
| 管理态 API | ❌ | 按角色矩阵 | — |

> **注意**：`manifest` 和 `tool-definition` 当前兼容 `capability:resolve` scope，未来可能收紧为独立 scope。

### 3.4 当前矩阵的已知限制

| 限制 | 说明 | 计划 |
|------|------|:---:|
| Owner 权限是全局角色级 | 不区分 own/other 资产 | ✅ 已实现（RBAC-3D-2） |
| `asset:delete_draft` 仅 Admin | Owner 在全局矩阵中无此权限 | ✅ 已实现（RBAC-3D-2 own 资产可删） |
| `lifecycle:publish` 仅 Publisher + Admin | Owner 需兼任 Publisher 或 Admin 才能发布 | 如下注 |
| `export:download` 偏宽 | 当前给了几乎所有管理角色，后续可收紧 | P2 |
| `runtime_consumer` 权限空集 | 当前 Runtime API 已有 role/scope 入口检查 + ScopedCapabilityAccessPolicy | ✅ RBAC-4 |

> **注**：`lifecycle:publish` 当前仅 Publisher 和 Admin 拥有。方案允许按场景调整：若希望 Owner 可直接发布，将 `lifecycle:publish` 加入 Owner 权限集即可。

---

## 四、审批流程与 RBAC 绑定

### 4.1 审批流

```
创建/导入
    │
    ▼
  draft ──── Contributor/Owner 编辑
    │
    │ (Contributor/Owner/Admin 提交审核)
    │ submit-review 自动触发扫描
    │   ├── blocking → 400 阻断
    │   ├── high → 允许进入 review，需 Security Reviewer 关注
    │   └── medium/low → 仅记录
    ▼
  pending_review
    │
    ├── Security Reviewer 审核扫描结果 (review:approve)
    ├── Business Approver 审核业务合理性 (review:approve)
    │
    ├── request_change → change_required → draft (review:request_change)
    ├── reject → rejected (review:reject)
    └── approve → approved (review:approve)
            │
            │ (Publisher/Admin 发布)
            ▼
          published
            │
            ├── disable → disabled (lifecycle:disable)
            ├── archive → archived (lifecycle:archive)
            └── rollback → back to previous version (lifecycle:rollback)
```

### 4.2 风险级别处理

| 风险 | 提交审核时 | 审批时 | 发布时 |
|------|----------|--------|--------|
| `blocking` | 400 直接阻断 | N/A（不会到达此状态） | N/A |
| `high` | 允许提交，标记需关注 | Security Reviewer 已查看才可 approve | 无额外限制 |
| `medium` | 允许提交 | 仅展示 | 无额外限制 |
| `low` | 允许提交 | 仅展示 | 无额外限制 |

### 4.3 审批动作角色绑定

| 动作 | 所需权限 | 所需角色 | 业务规则 |
|------|----------|----------|----------|
| `submit_item` | `review:submit` | Contributor / Owner / Admin | item 必须是 draft |
| `submit_version` | `review:submit` | Contributor / Owner / Admin | 自动扫描 |
| `approve` | `review:approve` | Security Reviewer / Approver / Admin | 必须先扫描；high 风险需 SecReviewer |
| `reject` | `review:reject` | Security Reviewer / Approver / Admin | 状态必须是 pending_review |
| `request_change` | `review:request_change` | Security Reviewer / Approver / Admin | 状态必须是 pending_review |
| `publish` | `lifecycle:publish` | Publisher / Admin | 状态必须是 approved |
| `disable` | `lifecycle:disable` | Publisher / Admin | 状态必须是 published |
| `archive` | `lifecycle:archive` | Admin | 状态必须是 published 或 disabled |
| `rollback` | `lifecycle:rollback` | Publisher / Admin | 目标版本必须是 historical published |

### 4.4 四眼原则

- **当前未开启**。`HUB_FOUR_EYES_REQUIRED` 默认 `false`。
- 开启后：ApprovalPolicy 拒绝提交者审批自己提交的版本。
- 只对 `approve` 生效（`reject` / `request-change` 不受影响）。
- submitter 优先使用可信 operator（ctx.actor_id）。
- Admin 和 dev mode 豁免。
- 找不到 submitter 时 fail-open（不阻断）。

### 4.5 简化部署建议

**小团队 / PoC / 演示环境推荐：**

| 场景 | 角色配置 |
|------|----------|
| 小团队安全评估 | Actor 持 Contributor + Approver（Security + Business 合并） |
| 小团队发布 | Owner 兼任 Publisher 或 Admin |
| PoC 演示 | `HUB_AUTH_MODE=dev`（自动 platform_admin） |
| 审核加速 | 单人 Owner 持 Admin 等效权限 |
| 多人审批（P3） | AND 逻辑：SecReviewer AND Approver 都 approve 后进入 approved |

**推荐 P1 先做**：角色 × 动作定义（已完成） + 四眼原则（可选配置）。
**P2 再做**：Security Reviewer 与 Business Approver 分离（AND 逻辑）。
**P3 再做**：waiver 机制、审批链配置、多人审批策略。

---

## 五、身份上下文设计

### 5.1 AuthContext 字段（已实现）

```python
@dataclass
class AuthContext:
    actor_id: str | None          # 调用者唯一标识
    actor_type: str | None        # user / service / agent
    display_name: str | None      # 显示名称
    email: str | None             # 邮箱
    agent_id: str | None          # Agent ID（Agent 调用方标识）
    roles: list[str]             # 角色列表
    scopes: list[str]            # 权限范围
    organization_id: str | None   # 组织
    workspace_id: str | None      # 工作空间
    groups: list[str]            # 组
    service_name: str | None      # 服务账号名
    raw: dict                    # 原始 claims（透传，不做校验）
    is_authenticated: bool        # 是否已认证
    auth_mode: str                # dev / header / none
```

### 5.2 身份来源

| 来源 | 模式 | 字段映射 |
|------|------|----------|
| **API Gateway**（推荐） | `header` | `X-Actor-ID` → `actor_id`、`X-Roles` → `roles`、... |
| **OIDC / JWT** | `oidc`（P2） | 从 Token claims 解析 |
| **Dev Mode** | `dev` | 硬编码 `platform_admin` |
| **None Mode** | `none` | 无认证，所有字段为空 |

### 5.3 设计原则（已贯彻）

- Hub **不生产身份**：身份由上游 Gateway / IAM / OIDC 注入，Hub 只消费。
- Hub **不存储密码**：不存储任何用户凭证。
- Hub **不做登录页面**：没有 login/logout/register 端点。
- Hub **消费上游可信身份上下文**：dev mode 仅用于本地开发。
- **不信任前端直接传 actor_id**：只有 Gateway 注入的 Header 才是可信来源；query 参数仅作为补充。
- 后续由 Gateway 负责校验 token 并注入可信 header 或 claims。

---

## 六、鉴权模式设计

### 6.1 三种模式对比

| 维度 | A：dev 模式 | B：header 模式 | C：OIDC 模式 |
|------|------------|----------------|--------------|
| 适用场景 | 本地开发、演示 | 企业内网 + 统一网关 | 独立部署、无 Gateway |
| 认证方式 | 自动 admin | Gateway 已认证、Header 注入 | Hub 校验 JWT |
| 身份来源 | 硬编码 | `X-Actor-ID` / `X-Roles` 等 | `Authorization: Bearer <jwt>` |
| Hub 职责 | 无认证 | 解析 Header、执行 RBAC | 校验 JWT、解析 claims、执行 RBAC |
| 安全性 | 仅 localhost | 依赖可信 Gateway | Hub 自主校验 |
| 实现复杂度 | 零 | 低 | 中高 |
| 当前状态 | ✅ 已实现 | ✅ 已实现（无 JWT 校验） | ❌ 未实现 |

### 6.2 推荐路线

- **P1-B**：Header 注入模式已实现。适合企业内网 + API Gateway（如 Kong/Nginx/Apigee）场景。
- **P2-C**：OIDC Token 校验模式。适合无统一 Gateway 的独立部署。
- **dev 模式保留**：默认本地可用，生产环境通过 `HUB_AUTH_MODE` 切换。

### 6.3 Header 模式安全约束

```
客户端 ──→ Gateway（认证 + Token 校验）
             │
             ├── 校验 JWT / OAuth2 token
             ├── 提取 actor_id / roles / scopes / workspace_id
             ├── 注入 header
             │     X-Actor-ID: user-456
             │     X-Roles: contributor,business_approver
             │     X-Workspace-ID: ws-789
             ├── **必须覆盖/过滤客户端自行传递的同名 header**
             └── 转发请求到 Hub
```

- Gateway **必须过滤或覆盖**客户端直接发送的 `X-Actor-ID` / `X-Roles` 等 Header。
- Hub 默认信任 Header。若 Gateway 未正确过滤，存在身份伪造风险。
- OIDC 模式可避免此风险：Hub 直接校验 JWT，不信任任何 Header。

### 6.4 模式切换

环境变量 `HUB_AUTH_MODE`：

```
dev    → 默认，本地开发，自动 admin
header → 从 Header 解析 AuthContext，无 Header → is_authenticated=false → RBAC 拒绝
none   → 紧急关闭所有鉴权（运维用途）
```

生产部署必须设为 `header` 或 `oidc`（P2），禁止使用 `dev`。

---

## 七、API 权限分层

### 7.1 API 分类

| 类别 | 路径前缀 | 鉴权要求 | 说明 |
|------|----------|----------|------|
| **Public / Health** | `GET /api/health` | 无 | 健康检查，Gateway / Hermes 使用 |
| **Management API** | `/api/hub/*` | RBAC + AuthContext | 全部 26 个端点已加 `require_permission` |
| **Runtime API** | `/api/runtime/*` | ScopedCapabilityAccessPolicy + `require_runtime_permission` | 已完成 RBAC-4 |
| **Admin API** | 预置/标签/配置 | `admin:configure` | 仅 platform_admin |

### 7.2 各类 API 鉴权详情

#### Public / Health

- `GET /api/health` — 无鉴权。
- 仅返回 `{"status": "ok"}`。
- 不记录 access log。

#### Management API（已实现 RBAC-2）

| 路径 | 方法 | 所需权限 |
|------|------|----------|
| `/api/hub/items` | POST | `asset:create` |
| `/api/hub/items` | GET | `asset:read` |
| `/api/hub/items/{id}` | GET | `asset:read` |
| `/api/hub/items/{id}` | PUT | `asset:update` |
| `/api/hub/items/{id}/relations` | GET | `asset:read` |
| `/api/hub/items/{id}/versions` | POST | `version:create` |
| `/api/hub/items/{id}/versions` | GET | `asset:read` |
| `/api/hub/items/{id}/versions/{vid}` | GET | `asset:read` |
| `/api/hub/items/{id}/submit` | POST | `review:submit` |
| `/api/hub/items/{id}/disable` | POST | `lifecycle:disable` |
| `/api/hub/items/{id}/archive` | POST | `lifecycle:archive` |
| `/api/hub/items/{id}/rollback` | POST | `lifecycle:rollback` |
| `/api/hub/versions/{id}/submit-review` | POST | `review:submit` |
| `/api/hub/versions/{id}/approve` | POST | `review:approve` |
| `/api/hub/versions/{id}/reject` | POST | `review:reject` |
| `/api/hub/versions/{id}/request-change` | POST | `review:request_change` |
| `/api/hub/versions/{id}/publish` | POST | `lifecycle:publish` |
| `/api/hub/versions/{id}/scan` | POST | `scan:run` |
| `/api/hub/versions/{id}/scan-report` | GET | `scan:read` |
| `/api/hub/relations` | POST | `relation:create` |
| `/api/hub/relations/{id}` | GET | `asset:read` |
| `/api/hub/relations/{id}` | DELETE | `relation:delete` |
| `/api/hub/imports/package` | POST | `asset:import` |
| `/api/hub/imports/openapi` | POST | `asset:import` |
| `/api/hub/presets/init` | POST | `admin:configure` |
| `/api/hub/exports/items/{id}` | GET | `export:download` |
| `/api/hub/exports/items/{id}/versions/{vid}/package` | GET | `export:download` |

#### Runtime API（已实现 RBAC-4）

| 路径 | 方法 | 鉴权 |
|------|------|------|
| `/api/runtime/capabilities/discover` | GET | `require_runtime_permission("capability:discover")` + ScopedCapabilityAccessPolicy |
| `/api/runtime/capabilities/{id}/resolve` | GET | `require_runtime_permission("capability:resolve")` + ScopedCapabilityAccessPolicy |
| `/api/runtime/capabilities/{id}/manifest` | GET | `require_runtime_permission("capability:manifest", fallback=["capability:resolve"])` + ScopedCapabilityAccessPolicy |
| `/api/runtime/capabilities/{id}/tool-definition` | GET | `require_runtime_permission("capability:tool_definition", fallback=["capability:resolve"])` + ScopedCapabilityAccessPolicy |

- **入口级检查**：`require_runtime_permission` 验证调用者具备 `runtime_consumer` 或 `platform_admin` 角色，以及对应 scope。
- **资产级过滤**：`ScopedCapabilityAccessPolicy` 进一步按角色过滤资产可见性（P1 仅区分 runtime_consumer/platform_admin，不做 workspace 过滤）。
- **platform_admin 豁免 scope**，但需具备 role。
- **dev mode 无 Header** 自动获得 dev-admin（platform_admin），兼容本地测试。
- **none mode** 完全绕过 Runtime Auth（运维用途）。
- 不检查管理态 RBAC 权限。
- Policy deny 时 discover 静默排除，resolve/manifest/tool-definition 返回 404。

#### Runtime API Header 使用示例

**Runtime Discover（runtime_consumer + scope）**：
```
GET /api/runtime/capabilities/discover
X-Actor-ID: runtime-service-1
X-Actor-Type: service
X-Roles: runtime_consumer
X-Scopes: capability:discover
```

**Runtime Resolve（runtime_consumer + scope）**：
```
GET /api/runtime/capabilities/{id}/resolve
X-Actor-ID: runtime-service-1
X-Actor-Type: service
X-Roles: runtime_consumer
X-Scopes: capability:resolve
```

**Runtime Manifest（runtime_consumer + manifest scope 或 resolve scope fallback）**：
```
GET /api/runtime/capabilities/{id}/manifest
X-Actor-ID: runtime-service-1
X-Actor-Type: service
X-Roles: runtime_consumer
X-Scopes: capability:manifest    # 或 capability:resolve（fallback）
```

**Runtime Tool Definition（runtime_consumer + tool_definition scope 或 resolve scope fallback）**：
```
GET /api/runtime/capabilities/{id}/tool-definition
X-Actor-ID: runtime-service-1
X-Actor-Type: service
X-Roles: runtime_consumer
X-Scopes: capability:tool_definition   # 或 capability:resolve（fallback）
```

**Platform Admin（豁免 scope）**：
```
GET /api/runtime/capabilities/discover
X-Actor-ID: admin-1
X-Roles: platform_admin
```

> **重要约束**：
> - Runtime Consumer 不拥有管理态 API 权限（`runtime_consumer` 在 RBAC 矩阵中权限为空集）；
> - 生产环境 Header 应由 Gateway 注入，不可由客户端直接传递；
> - query 身份参数（`actor_id`、`roles`、`scopes` 等）仅作 dev/backward compatibility，不是生产可信身份来源。

---

## 八、策略模型设计

### 8.1 策略分层

```
请求进入
    │
    ├── AuthMiddleware → AuthContext (dev/header/none)
    │
    ├── require_permission() → RBAC 角色级权限检查（管理态 API）
    │    └── ROLE_PERMISSIONS dict 查表
    │
    ├── ApprovalPolicy → 审批业务规则检查（approve/reject/publish/...）
    │    └── 四眼原则 / high 风险确认 / 状态流转约束
    │
    └── CapabilityAccessPolicy → Runtime 可见性过滤（discover/resolve）
         └── 基于 workspace/scope/risk-level 过滤
```

### 8.2 各策略接口

#### RBAC 策略（`backend/app/core/rbac.py`，已实现）

```python
def require_permission(permission: str) -> Depends
def has_permission(ctx: AuthContext, permission: str) -> bool
```

- 8 角色 × 24 权限硬编码矩阵。
- 通过 FastAPI Depends 在 handler 层执行。
- 不散落在 Service 层。
- dev mode：`platform_admin` 通过所有检查。
- header mode：无有效角色 → 403。
- none mode：无认证 → 403。

#### CapabilityAccessPolicy（`backend/app/policies/capability_access.py`，接口已实现）

```python
class CapabilityAccessPolicy(Protocol):
    def can_discover(item, version, context) -> bool
    def can_resolve(item, version, context) -> bool
```

- 当前 `AllowAllCapabilityAccessPolicy` 作为默认实现（测试用）。
- `ScopedCapabilityAccessPolicy` 作为生产默认实现（RBAC-4 已完成）。
- Runtime Discover Service 已接入 policy 调用（`runtime_discover_service.py:307,334,361,449`）。

#### ApprovalPolicy（`backend/app/policies/approval_policy.py`，接口已实现）

```python
class ApprovalPolicy(Protocol):
    def can_submit_review(ctx, item, version, operator, reason) -> ApprovalPolicyDecision
    def can_approve(ctx, item, version, operator, comment) -> ApprovalPolicyDecision
    def can_reject(ctx, item, version, operator, comment) -> ApprovalPolicyDecision
    def can_request_change(ctx, item, version, operator, comment) -> ApprovalPolicyDecision
    def can_publish(ctx, item, version, operator, reason) -> ApprovalPolicyDecision
```

- 当前 `AllowAllApprovalPolicy` 作为默认实现。
- Service 层已接入 policy + ctx（`approval_service.py` / `lifecycle_service.py`）。
- API 层捕获 `ApprovalPolicyDeniedError` 返回 403。
- RBAC-3C 时替换为含四眼原则 + high 风险确认的真实实现。

### 8.3 策略选择机制

当前通过依赖注入 / 默认参数实现：

```python
class RuntimeDiscoverService:
    def __init__(self, db, policy=None):
        self.policy = policy or AllowAllCapabilityAccessPolicy()

class ApprovalService:
    def __init__(self, db, policy=None):
        self.policy = policy or AllowAllApprovalPolicy()
```

P2 时可通过 FastAPI Depends / Settings 注入真实策略实现，无需修改 Service 代码。

---

## 九、分阶段实现计划

### 当前进度总览

| 阶段 | 内容 | 状态 | 测试基线 |
|:---:|------|:---:|:---:|
| RBAC-0 | 文档设计（`07_rbac_approval_design.md`） | ✅ | — |
| RBAC-1 | AuthContext 标准化 + Header 注入 + actor_id 日志 | ✅ | 313 passed |
| RBAC-2 | 管理态 RBAC 中间件 / Depends | ✅ | 407 passed |
| RBAC-3B | ApprovalPolicy 接口 + AllowAll | ✅ | 424 passed |
| RBAC-3C-0 | operator → actor_id 迁移 | ✅ | 438 passed |
| RBAC 决策 | 策略决策收敛（`docs/14_rbac_decision_record.md`） | ✅ | — |
| RBAC-3C | 四眼原则实现 | ✅ | 445 passed |
| RBAC-3D-1 | created_by 写入端修复 | ✅ | 458 passed |
| RBAC-3D-2 | 对象级 ownership 实现 | ✅ | 471 passed |
| RBAC-4 | Runtime Consumer 角色/scope 入口权限 + ScopedCapabilityAccessPolicy | ✅ | 509 passed |

### 后续阶段

#### Stage RBAC-3C：四眼原则 ✅ 已实现

**注：决策已收敛，详见 `docs/14_rbac_decision_record.md`。**

**目标**：实现提交者不能审批自己版本的约束（可配置，默认关闭）。

**内容**：
- 新增 `HUB_FOUR_EYES_REQUIRED` 环境变量（默认 `false`）。
- 实现 `FourEyesApprovalPolicy`，在 `can_approve` 中检查 submitter ≠ approver。
- submitter 优先用 operator 字段记录，长期迁移到 actor_id。
- Admin 和 dev mode 豁免。
- 找不到 submitter 时 fail-open（记录警告，不阻断）。
- 不影响 reject / request-change。

**涉及文件**：
- `backend/app/policies/approval_policy.py` — 新增 `FourEyesApprovalPolicy`
- `backend/app/core/config.py` — 新增 `HUB_FOUR_EYES_REQUIRED`
- `backend/app/services/approval_service.py` — 可能需要记录 submitter

**测试要求**：
- dev mode 下不受影响。
- 四眼关闭时行为不变。
- 四眼开启时提交者无法审批自己版本。
- Admin 豁免。

#### Stage RBAC-3D：对象级 Ownership

**目标**：`asset_owner` 角色仅能操作自己拥有的资产。

**内容**：
- HubItem / HubItemVersion 模型是否新增 `owner_id` 字段。
- 或复用 `created_by` 作为 owner。
- `require_permission` 增加资源级检查。
- 区分 own/other：Owner 可删除 own draft；不可操作 other 资产。

**涉及文件**：
- `backend/app/models/hub_item.py` — 新增 `owner_id` 字段
- `backend/app/core/rbac.py` — 扩展 `require_permission`
- `backend/app/api/*.py` — 修改相关 endpoint

**测试要求**：
- Owner 可操作自己资产。
- Owner 不可操作他人资产。
- Admin 可操作所有资产。

#### Stage RBAC-4：Runtime Consumer 权限 ✅ 已实现

**目标**：Runtime API 只允许 Runtime Consumer 角色，非该角色返回 403。实现 scope 级别的入口权限检查和资产级可见性策略。

**内容**：
- 新增 `backend/app/core/runtime_auth.py` — 入口级 role/scope 检查（`require_runtime_permission`）。
- 新增 `ScopedCapabilityAccessPolicy` — 基于角色的资产级可见性过滤。
- Runtime API 4 个端点增加入口权限检查：
  - `discover` → `capability:discover`
  - `resolve` → `capability:resolve`
  - `manifest` → `capability:manifest`（兼容 `capability:resolve`）
  - `tool-definition` → `capability:tool_definition`（兼容 `capability:resolve`）
- `platform_admin` 豁免 scope 检查。
- dev mode 无 Header 时 dev-admin 为 platform_admin，继续兼容本地测试。
- none mode 完全绕过 Runtime Auth。
- Policy deny 时 discover 静默排除，resolve/manifest/tool-definition 返回 404。
- workspace 过滤暂不做（需后续 DB migration）。
- OIDC / API Key 仍未实现。

**涉及文件**：
- `backend/app/core/runtime_auth.py` — 新增 Runtime Auth 检查函数
- `backend/app/policies/capability_access.py` — 新增 `ScopedCapabilityAccessPolicy`
- `backend/app/api/runtime.py` — 增加 `require_runtime_permission` + 切换默认 policy
- `backend/tests/test_runtime_access_policy.py` — 新增 24 个 RBAC-4 测试

**测试要求**：
- Runtime Consumer + scope → 200 ✅
- 无 runtime role → 403 ✅
- Contributor → 403 ✅
- Contributor + runtime_consumer → 200 ✅
- platform_admin → 200 ✅
- manifest/tool-definition scope fallback ✅
- Policy deny → 404 ✅
- Policy deny discover → 静默排除 ✅

#### Stage RBAC-5：OIDC / Gateway 真实对接

**目标**：对接真实企业 IAM，不再依赖 dev mode。

**内容**：
- Header 模式生产化（确保 Gateway 正确过滤/注入 Header）。
- OIDC Token 校验模式（可选）。
- 外部 IAM 角色映射配置。
- 审计日志增强（记录 roles / claims）。

**涉及文件**：
- `backend/app/core/auth_context.py` — JWT claims 解析
- `backend/app/core/config.py` — OIDC 配置
- `backend/app/core/auth_middleware.py` — OIDC 模式
- 新增 `backend/app/core/oidc.py`

**测试要求**：
- Header 模式：有效 Header → 通过；无效/缺失 → 403。
- OIDC 模式：有效 JWT → 通过；过期/无效 → 401。
- 角色映射：外部角色 `cn=admin` → Hub `platform_admin`。
- 审计日志包含 claims 摘要（不记录 token）。

### 优先级排序

| 优先级 | 阶段 | 说明 |
|:---:|------|------|
| **P0（已完成）** | RBAC-0/1/2/3B/3C-0/3C/3D-1/3D-2/4 | 基础 RBAC + 管理态 + 四眼 + ownership + Runtime Consumer |
| **P1（当前）** | RBAC-5 | OIDC/Gateway 对接 |
| **P2** | RBAC-5 | 真实 IAM / Gateway 对接 |
| **P3** | waiver / 多人审批 / 审批链 | 高级审批策略 |

---

## 十、测试计划

### 10.1 RBAC-3C 四眼原则测试

| # | 测试场景 | 预期结果 |
|:---:|------|------|
| 1 | dev mode 下提交者审批自己版本 | ✅ 通过（Admin 豁免） |
| 2 | 四眼关闭（`HUB_FOUR_EYES_REQUIRED=false`）时提交者审批自己版本 | ✅ 通过 |
| 3 | 四眼开启时提交者审批自己版本 | ❌ 403 denied |
| 4 | 四眼开启时其他人审批版本 | ✅ 通过 |
| 5 | 四眼开启时 Admin 审批自己版本 | ✅ 通过（豁免） |
| 6 | 找不到 submitter 时审批 | ✅ 通过（fail-open） |
| 7 | reject 不受四眼限制 | ✅ 通过 |
| 8 | request-change 不受四眼限制 | ✅ 通过 |

### 10.2 RBAC-3D 对象级 Ownership 测试

| # | 测试场景 | 预期结果 |
|:---:|------|------|
| 1 | Owner（own 资产）编辑 draft | ✅ 通过 |
| 2 | Owner（own 资产）提交审核 | ✅ 通过 |
| 3 | Owner（own 资产）删除 draft | ✅ 通过 |
| 4 | Owner（other 资产）编辑 draft | ❌ 403 |
| 5 | Owner（other 资产）删除 draft | ❌ 403 |
| 6 | Admin 操作 any 资产 | ✅ 通过 |
| 7 | Contributor（own 资产）可创建和编辑 | ✅ 通过 |
| 8 | Contributor（other 资产）不可操作 | ❌ 403（按设计，Contributor 不区分 own/other，保持全局角色级） |

### 10.3 RBAC-4 Runtime Consumer 测试 ✅ 已实现

| # | 测试场景 | 预期结果 |
|:---:|------|------|
| 1 | Runtime Consumer + `capability:discover` 调 discover | ✅ 200 |
| 2 | 无 Runtime Consumer 调 discover | ✅ 403 |
| 3 | Contributor 调 discover | ✅ 403 |
| 4 | Contributor + Runtime Consumer 调 discover | ✅ 200 |
| 5 | Runtime Consumer 缺 `capability:resolve` 调 resolve | ✅ 403 |
| 6 | Runtime Consumer + `capability:resolve` 调 resolve | ✅ 200 |
| 7 | `platform_admin` 调 Runtime API | ✅ 200 |
| 8 | `manifest` 需 `capability:manifest` 或 `capability:resolve` | ✅ |
| 9 | `tool-definition` 需 `capability:tool_definition` 或 `capability:resolve` | ✅ |
| 10 | discover 资产级 policy deny 静默排除 | ✅ |
| 11 | resolve 资产级 policy deny → 404 | ✅ |
| 12 | manifest 资产级 policy deny → 404 | ✅ |
| 13 | tool-definition 资产级 policy deny → 404 | ✅ |
| 14 | Runtime API 不受管理态 ownership 影响 | ✅ |
| 15 | 现有 471 tests 继续通过 | ✅ (509 passed) |

### 10.4 RBAC-5 Gateway/OIDC 测试

| # | 测试场景 | 预期结果 |
|:---:|------|------|
| 1 | header 模式 + 有效 Header | ✅ 构造正确 AuthContext |
| 2 | header 模式 + 无 Header | ❌ 403（is_authenticated=false） |
| 3 | OIDC 模式 + 有效 JWT | ✅ 构造正确 AuthContext |
| 4 | OIDC 模式 + 过期 JWT | ❌ 401 |
| 5 | OIDC 模式 + 无效签名 | ❌ 401 |
| 6 | role 映射：`IAM:admin` → `platform_admin` | ✅ has_permission 通过 |
| 7 | dev mode 下旧测试不受影响 | ✅ 438 passed 保持不变 |

### 10.5 回归测试要求

- **dev mode 下所有现有测试不受影响**（当前 438 passed 无变化）。
- 新增测试不破坏现有测试。
- 权限拒绝返回 403 + `{"detail": "permission denied"}` 保持一致。
- 日志中 actor_id 正确记录。

---

## 十一、文档更新计划

### 新增

| 文档 | 说明 |
|------|------|
| `docs/13_rbac_auth_integration_plan.md` | **本文档** — RBAC / 身份认证对接实施方案 |

### 更新

| 文档 | 更新内容 |
|------|----------|
| `docs/00_docs_index.md` | 确认 `13_rbac_auth_integration_plan.md` 已标记 |
| `docs/02_solution_design.md` | 更新状态表：RBAC-3C-0 已完成，新增后续阶段 |
| `docs/03_platform_integration.md` | 补充 Runtime 鉴权模式说明 |
| `docs/07_rbac_approval_design.md` | 更新 RBAC-3C-0 完成状态，补充 13 文档引用 |
| `docs/08_roadmap_workload.md` | 更新 RBAC 阶段完成状态，调整后续计划 |
| `docs/12_observability_logging_design.md` | 确认 actor_id 日志已实现 |
| `README.md` | 更新 RBAC 状态描述和 roadmap |

---

## 十二、需要人工确认的问题

**已解决**：以下 10 个问题已于 2026-05-27 通过 `docs/14_rbac_decision_record.md` 完成决策收敛。问题原文保留供参考，最终策略以决策记录为准。

| # | 问题 | 决策结论 | 详见 |
|:---:|------|------|------|
| 1 | **HubItem 是否新增 `owner_id` 字段？** | P1 复用 `created_by`，长期新增 `owner_id` | `14_rbac_decision_record.md` §3.5 |
| 2 | **Owner 是否可以删除自己资产的 draft？** | 待 RBAC-3D 确认（建议可以） | `14_rbac_decision_record.md` §3.5 |
| 3 | **Owner 是否可以发布自己资产？** | 待 RBAC-3D 确认（建议按场景） | `14_rbac_decision_record.md` §3.5 |
| 4 | **Security Reviewer + Business Approver 分离还是合并？** | P1 合并为 OR（两者共享 review:approve） | `14_rbac_decision_record.md` §3.4 |
| 5 | **四眼原则是否默认开启？** | 默认关闭（`HUB_FOUR_EYES_REQUIRED=false`） | `14_rbac_decision_record.md` §3.1 |
| 6 | **Runtime Consumer 是否检查角色？** | RBAC-3C 不处理，RBAC-4 单独处理 | `14_rbac_decision_record.md` §3.8 |
| 7 | **OIDC 模式优先级？** | P2，Header 优先（`header` 模式已就绪） | `14_rbac_decision_record.md` §3.7 |
| 8 | **外部 IAM 角色映射配置方式？** | 环境变量优先，P3 再接入 DB 配置 | `14_rbac_decision_record.md`，后续阶段确认 |
| 9 | **Runtime API 无认证时行为？** | 由 CapabilityAccessPolicy 决定（保持当前行为） | `14_rbac_decision_record.md` §3.8 |
| 10 | **`body.operator` 兼容字段何时移除？** | 保留过渡期，P2 optional，P3 废弃 | `14_rbac_decision_record.md` §3.6 |

---

## 十三、不做事项

本阶段不做：

- 不实现登录页面 / logout / register
- 不存储密码
- 不实现完整 IAM
- 不实现多租户隔离
- 不实现 OIDC 校验（`HUB_AUTH_MODE=oidc`）
- 不修改业务 API 签名（仅增加 Depends）
- 不修改前端
- 不新增依赖
- 不破坏当前 438 tests
- 不将 `actor_id` query 参数视为可信身份

---

## 十四、文档角色约定

| 文档 | 定位 |
|------|------|
| `docs/07_rbac_approval_design.md` | **设计规格**：角色定义、权限矩阵、审批流程、策略接口 |
| `docs/13_rbac_auth_integration_plan.md`（本文档） | **实施计划**：当前状态、分阶段实现、测试计划 |
| `docs/14_rbac_decision_record.md` | **决策记录**：10 项决策收敛、RBAC-3C 实现边界、默认策略 |

三者互补：07 关注"是什么"，13 关注"怎么做"，14 关注"为何这样决定"。
