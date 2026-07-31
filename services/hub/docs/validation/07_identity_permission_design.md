# 身份权限边界设计

> 文档编号：validation/07
> 版本：v0.1
> 日期：2026-05-15
> 用途：定义 Hub 能力市场的身份角色、权限边界、Agent/Runtime 权限管控策略，以及 Hub 与 IAM/Gateway/Runtime Policy Engine 的职责划分

---

## 1. 核心原则

**Hub 不做完整 IAM，Hub 不实现认证鉴权。**

Hub 的身份权限设计遵循以下边界：

| Hub 负责 | Hub 不负责 |
|----------|-----------|
| 定义角色语义（Developer / Approver / Admin 等） | 用户密码存储、Token 签发、Session 管理 |
| 能力准入过滤（published + discoverable + non-blocking） | 用户认证与身份验证 |
| 传递权限信息（permission_json + aggregated_permissions） | 动态权限授予与撤销 |
| Agent 上下文过滤（agent_id / workspace_id → 可见能力范围） | Agent 的沙箱执行权限判定 |
| 接收 Gateway 注入的 context（user_id / agent_id / roles） | 独立实现 OAuth / OIDC / SAML |

**一句话：Hub 消费身份，不生产身份。**

---

## 2. 人类角色

### 2.1 角色定义

| 角色 | 标识 | 职责 | 典型操作 | 当前状态 |
|------|------|------|----------|:---:|
| **开发者** (Developer) | `developer` | 能力的创建者和维护者 | 创建/编辑 HubItem；创建版本；上传包；发起扫描；提交审批 | ⬜ |
| **维护者** (Maintainer) | `maintainer` | Developer + 管理与被依赖能力的关系 | Developer 全部权限 + 管理关系 + 管理标签/分类 | ⬜ |
| **审批人** (Approver) | `approver` | 能力发布的审核决策者 | 审批通过 / 驳回 / 要求修改；blocking 风险拦截 | ⬜ |
| **安全审核人** (Security Reviewer) | `security_reviewer` | 安全扫描结果的审查者 | 查看扫描报告；标记 blocking；给出安全建议 | ⬜ |
| **管理员** (Admin) | `admin` | Hub 平台的超级管理者 | 禁用/归档/回滚；管理预置能力；管理 Category；配置管理 | ⬜ |
| **普通使用者** (Viewer) | `viewer` | 能力的浏览者 | 搜索、浏览、下载已发布能力；查看详情 | ⬜ |

### 2.2 角色与操作矩阵

| 操作 | Developer | Maintainer | Approver | Security Reviewer | Admin | Viewer |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| 创建/编辑能力 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| 创建版本 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| 上传/导入能力包 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| 发起扫描 | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| 提交审批 | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |
| 审批（approve/reject/request_change） | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ |
| 查看所有扫描报告 | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| 标记 blocking | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ |
| 发布/禁用/归档 | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 回滚 | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| 管理 Category / Tag | ❌ | ✅ | ❌ | ❌ | ✅ | ❌ |
| 管理关系 | ❌ | ✅ | ❌ | ❌ | ✅ | ❌ |
| 搜索/浏览已发布能力 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 下载 manifest / 能力包 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

### 2.3 审批权限约束

| 规则 | 说明 |
|------|------|
| 创建者不能审批自己提交的能力 | 防止自审自批 |
| blocking 能力审批人只能驳回或要求修改 | 不能 approve（与当前 blocking 拦截规则一致） |
| 安全审核人可以覆盖审批人的安全判断 | 安全审核人标记 blocking → 审批人无法 approve |

---

## 3. Agent / Runtime 权限

### 3.1 Agent 权限声明

每个 Agent 在注册时声明其**被授予的权限范围**：

```json
{
  "agent_id": "uuid",
  "granted_permissions": {
    "network": true,
    "file_read": true,
    "file_write": false,
    "shell_exec": false,
    "database": true
  },
  "visible_workspaces": ["ws-001", "ws-002"],
  "max_risk_level": "high"
}
```

### 3.2 Hub 对 Agent 请求的处理

| Agent 操作 | Hub 处理 | 权限校验 |
|-----------|----------|----------|
| Discover 搜索能力 | 返回类型过滤 + 硬过滤（published + discoverable + non-blocking）| agent_id 过滤可见范围 |
| Resolve 解析能力 | 返回完整配置 + 依赖树 | 同上 + `max_risk_level` 过滤 |
| 下载 manifest | 返回 manifest_json | 能力可见 + 不是 blocking |
| 下载能力包 | 返回 ZIP | 能力可见 + 不是 blocking |

### 3.3 Agent 不可做的事

| # | 禁止操作 | 原因 |
|---|----------|------|
| 1 | 在 Discover 中绕过 agent_id 过滤 | Hub 在服务端强过滤 |
| 2 | 查看不属于其 workspace 的能力 | workspace_id 隔离 |
| 3 | 使用 blocking 能力 | Discover 直接不返回 |
| 4 | 获得新权限 | permission_json 只是信息传递，Agent 实际权限由 Runtime Policy Engine 判定 |
| 5 | 执行能力变更操作 | 管理态 API 仅人类角色可访问 |

---

## 4. Hub 不做完整 IAM

### 4.1 Hub 不实现的功能

| 功能 | 归属 | 说明 |
|------|------|------|
| 用户注册/登录 | IAM | Hub 只消费身份，不管理用户 |
| Token 签发/验证 | API Gateway / IAM | JWT/OAuth Token 由 Gateway 验证后注入 context |
| 密码管理 | IAM | 密码存储、重置、过期等 |
| MFA | IAM | 多因素认证 |
| OAuth / OIDC / SAML | IAM | 单点登录集成 |
| 角色生命周期管理 | IAM | 角色分配/变更/撤销 |

### 4.2 Hub 消费身份的方式

```
请求进入 → API Gateway
  │
  ├── ① 验证 JWT/OAuth Token
  ├── ② 解析 user_id / agent_id / workspace_id / roles
  │
  └── ③ 注入 HTTP Header 后转发到 Hub
        │
        ├── X-User-ID: user-xxx
        ├── X-User-Roles: developer,maintainer
        ├── X-Agent-ID: agent-xxx          (仅 Agent 请求携带)
        ├── X-Workspace-ID: ws-xxx         (仅 Agent 请求携带)
        └── X-Agent-Granted-Permissions: {"network":true,...}
              │
              ▼
        Hub API 层读取 Header → 注入 Service 层作为权限上下文
```

### 4.3 PoC 阶段的降级处理

在接入 IAM/Gateway 之前：

| 方式 | 说明 |
|------|------|
| **硬编码默认角色** | 所有请求以 `admin` 角色处理（当前 PoC 无权限区分） |
| **Header 直传** | 测试时手动设置 X-User-Roles / X-Agent-ID 等 Header |
| **不实现 Token 验证** | PoC 阶段跳过 Token 校验 |

---

## 5. IAM / Gateway / Runtime Policy Engine 的职责

### 5.1 职责总览

```
┌─────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────────────────┐
│  Client  │────▶│ API Gateway  │────▶│   Hub    │────▶│ Runtime Policy Engine│
│ (人/Agent)│     │              │     │ (内部)    │     │ (Runtime 独立)        │
└─────────┘     └──────┬───────┘     └──────────┘     └──────────────────────┘
                       │
                       ▼
               ┌──────────┐
               │   IAM    │
               │ (认证中心) │
               └──────────┘
```

| 组件 | 职责 | 对 Hub 的输入 | 当前状态 |
|------|------|-------------|:---:|
| **IAM** | 用户/Agent 身份管理、Token 签发、角色分配 | user_id + roles | 阶段 7 |
| **API Gateway** | Token 验证、context 注入、限流、路由 | HTTP Headers（见 §4.2） | 准生产 |
| **Hub** | 能力准入过滤 + 权限信息传递 + 角色校验 | — | 部分实现 |
| **Runtime Policy Engine** | Agent 实际权限判定 + Sandbox 执行边界 + 动态权限 | 消费 Hub 返回的 permission_json | Runtime 负责 |

### 5.2 分阶段接入路径

| 阶段 | Hub 行为 | Gateway/IAM |
|------|----------|-------------|
| PoC（当前） | 无身份校验，所有请求视为 admin | 无 |
| 阶段 7（准生产） | 读取 Header 中的身份信息，按角色限制操作 | API Gateway 注入身份 Header |
| 生产 | 严格的身份校验 + Agent 上下文过滤 + per-workspace 隔离 | 完整的 IAM + Gateway + Runtime Policy Engine |

---

## 6. Hub 内部仍负责的权限相关职责

虽然鉴权不在 Hub，但以下权限相关职责**必须保留在 Hub 内部**：

| # | 职责 | 说明 |
|---|------|------|
| 1 | **状态准入** | published / disabled / archived 状态的过滤逻辑 |
| 2 | **风险准入** | blocking 能力的禁止发现/使用规则 |
| 3 | **可发现性判定** | discoverable 字段的过滤 |
| 4 | **版本准入** | 只有 published/deprecated 版本可 resolve |
| 5 | **审批权限规则** | 创建者不能自审、blocking 只能驳回/要求修改 |
| 6 | **角色操作矩阵** | 哪些角色可以执行哪些管理操作（§2.2） |
| 7 | **Agent 上下文过滤** | 如果传入了 agent_id / workspace_id，按策略过滤 Discover/Resolve 结果 |
| 8 | **权限信息传递** | permission_json + aggregated_permissions 的准确传递 |
| 9 | **关系可见性** | management 关系对人可见、runtime 关系对 Runtime 可见 |
| 10 | **审计日志** | 所有权限相关操作的记录（谁在什么时间以什么角色做了什么操作） |

---

## 7. P0 / P1 实现建议

### 7.1 P0（阶段 7，准生产阶段）

| # | 任务 | 说明 |
|---|------|------|
| 1 | 定义角色枚举（Developer / Maintainer / Approver / Security Reviewer / Admin / Viewer） | 枚举 + API 文档 |
| 2 | Hub 读取 X-User-Roles / X-User-ID HTTP Header | 从 Gateway 注入的 context 中提取身份 |
| 3 | 管理态 API 按角色限制操作（见 §2.2 矩阵） | 根据角色拒绝未授权操作 |
| 4 | 审批约束：创建者不能审批自己提交的能力 | 审批 API 中校验 |
| 5 | Agent 请求识别：X-Agent-ID 头存在时切换为 runtime context | Discover/Resolve 按 agent 上下文过滤 |
| 6 | Agent workspace 隔离：X-Workspace-ID 过滤可见能力 | Discover 查询中应用 workspace 过滤 |
| 7 | tests: 角色操作矩阵 + 审批约束 + Agent 过滤 |

### 7.2 P1（生产阶段）

| # | 任务 | 说明 |
|---|------|------|
| 1 | 接入 IAM 完整身份体系（OAuth / OIDC） | Gateway 统一处理，Hub 消费 |
| 2 | 细粒度 per-workspace 权限策略 | 能力可见范围按 workspace 隔离 |
| 3 | Agent 权限上下文完整过滤（permission matching） | Agent 被授予的权限必须覆盖能力声明的权限 |
| 4 | 审批路径自定义（多级审批 / 会签） | 当前只有单级审批，生产可能需要更复杂流程 |
| 5 | 审计日志持久化 + 查询 | LifecycleEvent 增强 + 审计 API |

---

## 8. 与其他验证文档的关系

```
02_unified_vs_separate_management.md
  └── 统一治理面 → 统一角色和权限模型

04_runtime_discover_design.md
  └── Agent Discover/Resolve 接口 → 权限上下文过滤 (§2.2 Agent 自主搜索边界)

05_download_export_design.md
  └── 下载接口 → 访问控制 (published/deprecated 可下载)

06_manifest_spec_design.md
  └── permission_json 规范 → Hub 传递权限信息给 Runtime

07_identity_permission_design.md (本文档)
  └── 所有以上权限边界的总定义
```

---

> 配套文档：
> - `docs/validation/04_runtime_discover_design.md` — Runtime Discover 中的 Agent 权限边界
> - `docs/validation/08_final_recommendation.md` — 最终推荐路线中的阶段 7 权限接入
