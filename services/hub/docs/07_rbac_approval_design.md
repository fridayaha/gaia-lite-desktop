# Hub RBAC 与审批设计

版本：v0.10 | 日期：2026-05-27 | 状态：RBAC-4 已实现（Runtime Consumer role/scope + ScopedCapabilityAccessPolicy）

---

## 一、设计原则

Hub 的 RBAC 和审批设计遵循以下原则：

1. **Hub 不生产身份**：身份由上游 Gateway / IAM / OIDC 注入，Hub 只消费；
2. **Hub 不存储密码**：不存储任何用户凭证；
3. **管理态与运行态分离**：Management API 需要用户身份；Runtime API 可由 service account 调用；
4. **身份仅从可信来源获取**：不信任前端直接传 actor_id；
5. **策略层与业务逻辑分离**：权限判断通过 Policy 接口执行，不散落在 API handler 中；
6. **默认拒绝**：未明确授权的动作不可执行；
7. **不可见 = 不存在**：Runtime API 对无权访问的资产返回 404。

---

## 二、审批流程

### 2.1 完整审批流

```
创建/导入
    │
    ▼
  draft ──── 编辑 / 删除 / 重新编辑
    │
    │ (Contributor/Owner 提交审核)
    ▼
  submit-review
    │
    ├── 自动扫描 (auto scan)
    │     ├── blocking → 400 阻断
    │     └── 通过 → 继续
    ▼
  pending_review
    │
    ├── Security Reviewer 审核风险
    ├── Business Approver 审核业务
    │
    ├── request_change → change_required → draft
    ├── reject → rejected
    └── approve → approved
            │
            │ (Publisher 发布)
            ▼
          published
            │
            ├── disable → disabled
            ├── archive → archived
            └── rollback → new version published
```

### 2.2 状态流转表

| 当前状态 | 可流转到 | 执行角色 |
|----------|----------|----------|
| draft | pending_review（submit） | Contributor, Owner, Admin |
| draft | —（delete） | Admin, Owner |
| pending_review | approved | SecReviewer + Approver, Admin |
| pending_review | rejected | SecReviewer, Approver, Admin |
| pending_review | change_required | SecReviewer, Approver, Admin |
| change_required | draft（重新编辑） | Contributor, Owner, Admin |
| change_required | pending_review（重新提交） | Contributor, Owner, Admin |
| approved | published | Publisher, Admin |
| published | disabled | Admin, Owner |
| published | archived | Admin |
| published | rolled_back | Admin, Owner |
| disabled | archived | Admin |
| rejected | —（终态） | — |

### 2.3 自动扫描

- `submit-review` 时自动触发扫描；
- `blocking` 风险 → 400 阻断提交；
- `high` 风险 → 需 Security Reviewer 确认后放行；
- `low` / `medium` → 仅展示在扫描报告，不阻断。

### 2.4 四眼原则

- 默认不强制（P1 阶段可通过 `self_approve_allowed` 配置）；
- 开启后：提交者不能审批自己提交的版本；
- P3 再做完整四眼原则 + waiver + 审批链配置。

---

## 三、角色体系

### 3.1 角色定义

| 角色 | 标识 | 职责范围 |
|------|------|----------|
| Platform Admin | `platform_admin` | 系统管理、紧急操作 |
| Asset Owner | `asset_owner` | 自己资产的全生命周期 |
| Contributor | `contributor` | 创建和编辑（不能发布） |
| Security Reviewer | `security_reviewer` | 安全审核 |
| Business Approver | `business_approver` | 业务审核 |
| Publisher | `publisher` | 发布管理 |
| Runtime Consumer | `runtime_consumer` | 运行态调用 |
| Auditor | `auditor` | 只读审计 |

### 3.2 简化模式

小团队可使用以下角色映射：

| 标准角色 | 简化映射 |
|----------|----------|
| Security Reviewer | → Approver |
| Business Approver | → Approver |
| Publisher | → Asset Owner 或 Admin |
| Auditor | → Admin（只读权限） |

---

## 四、动作权限

### 4.1 管理态权限矩阵

| 动作 | Admin | Owner（own） | Contributor（own） | Approver | Publisher | Auditor |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| 创建 item | ✅ | ✅ | ✅ | — | — | — |
| 导入 | ✅ | ✅ | ✅ | — | — | — |
| 创建 version | ✅ | ✅ | ✅ | — | — | — |
| 编辑 version | ✅ | ✅ | ✅ | — | — | — |
| 删除 version | ✅ | ✅ | — | — | — | — |
| 提交审核 | ✅ | ✅ | ✅ | — | — | — |
| 手动扫描 | ✅ | ✅ | ✅ | — | — | — |
| 查看扫描 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 审批 | ✅ | — | — | ✅ | — | — |
| 驳回 | ✅ | — | — | ✅ | — | — |
| 请求修改 | ✅ | — | — | ✅ | — | — |
| 发布 | ✅ | ✅ | — | — | ✅ | — |
| 禁用 | ✅ | ✅ | — | — | — | — |
| 归档 | ✅ | — | — | — | — | — |
| 回滚 | ✅ | ✅ | — | — | — | — |
| 管理关系 | ✅ | ✅ | — | — | — | — |
| 审计查看 | ✅ | — | — | — | — | ✅ |

### 4.2 运行态权限矩阵

| 动作 | Runtime Consumer | 其他角色 |
|------|:---:|:---:|
| discover | ✅ | ✅ |
| resolve | ✅ | ✅ |
| tool-definition | ✅ | ✅ |
| manifest | ✅ | ✅ |
| 管理态 API | ❌ | 按角色 |

---

## 五、身份上下文

### 5.1 AuthContext

Hub 通过 `AuthContext` 消费上游身份：

```python
@dataclass
class AuthContext:
    actor_id: str | None          # 调用者唯一标识
    actor_type: str | None        # user / service / agent
    display_name: str | None      # 显示名称
    roles: list[str]             # 角色列表
    workspace_id: str | None      # 工作空间
    organization_id: str | None   # 组织
    scopes: list[str]            # 权限范围
    groups: list[str]            # 组
    raw: dict                    # 原始 claims
```

### 5.2 身份来源

```
Gateway / IAM → Header 注入
  X-Actor-ID: user-123
  X-Roles: contributor,approver
  X-Workspace-ID: ws-456
```

### 5.3 Dev Mode

```
HUB_AUTH_MODE=dev → 自动 admin，用于本地开发
HUB_AUTH_MODE=header → 从 Header 解析，无 Header → 403
```

---

## 六、策略接口

### 6.1 RBACPolicy

```python
class RBACPolicy(Protocol):
    def can_perform(action: str, context: AuthContext,
                    resource: dict | None = None) -> bool
```

### 6.2 CapabilityAccessPolicy（已有）

```python
class CapabilityAccessPolicy(Protocol):
    def can_discover(item, version, context) -> bool
    def can_resolve(item, version, context) -> bool
```

### 6.3 ApprovalPolicy

```python
class ApprovalPolicy(Protocol):
    def can_approve(version_id, context) -> bool
    def can_reject(version_id, context) -> bool
    def can_request_change(version_id, context) -> bool
    def can_publish(version_id, context) -> bool
```

---

## 七、与 13_rbac_auth_integration_plan 的关系

| 文档 | 定位 |
|------|------|
| `docs/07_rbac_approval_design.md`（本文档） | 设计规格：角色、动作、审批流、策略接口 |
| `docs/13_rbac_auth_integration_plan.md` | 实施计划：分阶段实现、技术方案、测试计划 |

---

## 八、RBAC-3 审批流角色绑定（下一阶段）

### 8.1 核心问题

RBAC-2 已实现角色级权限门，但以下问题待 RBAC-3 解决：

- `asset_owner` 当前是全局角色权限，不区分 own/other 资产；
- 审批动作（approve/reject/publish）未绑定到具体角色流转约束；
- 提交者可以审批自己提交的版本；
- high 风险未强制 Security Reviewer 确认。

### 8.2 决策已收敛

RBAC-3 关键决策已在 `docs/14_rbac_decision_record.md` 中确认，详见该文档。

本文档保留设计规格，不再重复决策点。

### 8.3 新增策略接口 ✅ 已实现

RBAC-3B 已实现 `backend/app/policies/approval_policy.py`：

- `ApprovalPolicyDecision`：`allow()` / `deny(reason, reason_code)`
- `ApprovalPolicy` Protocol：
  - `can_submit_review(ctx, item, version, operator, reason)`
  - `can_approve(ctx, item, version, operator, comment)`
  - `can_reject(ctx, item, version, operator, comment)`
  - `can_request_change(ctx, item, version, operator, comment)`
  - `can_publish(ctx, item, version, operator, reason)`
- `AllowAllApprovalPolicy`：默认实现，全部 allow
- Service 层：`ApprovalService` / `LifecycleService` 已接入 policy + ctx
- API 层：`approvals.py` / `lifecycle.py` 注入 ctx，捕获 403

相比 RBAC-2 的 `require_permission(review:approve)`：
- RBAC-2 解"谁有 approve 权限"（角色级）
- RBAC-3 解"在这个具体版本/资产/上下文下是否允许"（业务策略级）

### 8.4 disable/archive/rollback 不纳入 ApprovalPolicy

disable/archive/rollback 属于生命周期治理，不属于审批流程。当前 `require_permission` 已提供角色级保护。后续如需细化，应新增独立的 `LifecyclePolicy`，不继续扩大 ApprovalPolicy。

---

## 九、operator → actor_id 可信审计身份迁移 ✅ 已完成

`backend/app/core/operator.py` 已实现：

- `resolve_effective_operator(ctx, body_operator)`：优先 ctx.actor_id，fallback body.operator，最终 "unknown"
- `log_operator_mismatch(ctx, body_operator, action, ...)`：不一致时记录 `auth.operator_mismatch` 事件
- `resolve_and_log_operator(...)`：组合函数

全部 10 个管理态端点（approve/reject/request-change/submit-item/submit-review/publish/disable/archive/rollback/scan）已使用 effective_operator。

详见 `docs/13_rbac_auth_integration_plan.md` 第五节"身份上下文设计"。

---

## 十、四眼原则 ✅ 已实现

`backend/app/policies/approval_policy.py` 中 `DefaultApprovalPolicy` 已实现四眼原则。

关键行为：
- `HUB_FOUR_EYES_REQUIRED` 默认 false，生产可开启
- 只对 approve 生效（reject / request-change 不受影响）
- `platform_admin` 豁免 + 记录 `auth.four_eyes.admin_exempted` 日志
- submitter 优先从 `LifecycleEvent` 按 `version_id + EventType.submitted` 查询
- 找不到 submitter 时 fail-open + 记录 `auth.four_eyes.submitter_unknown` 日志
- operator 已通过 RBAC-3C-0 迁移为可信 actor_id

详见 `docs/14_rbac_decision_record.md` 第四节。
