# Hub RBAC 策略决策记录

版本：v1.4 | 日期：2026-05-27 | 状态：**RBAC-3D-2 已实现**（对象级 ownership）

---

## 一、当前 RBAC 已完成能力确认

| 阶段 | 内容 | 测试基线 | 说明 |
|:---:|------|:---:|------|
| RBAC-0 | 文档设计（`07_rbac_approval_design.md`） | — | 角色/权限/审批流/策略接口设计 |
| RBAC-1 | AuthContext 标准化 + Header 注入 + actor_id 日志 | 313 passed | 14 字段 AuthContext，dev/header/none 三模式 |
| RBAC-2 | 管理态 RBAC 中间件 / Depends | 407 passed | 8 角色 × 24 权限矩阵，26 个 API 已加 `require_permission` |
| RBAC-3B | ApprovalPolicy 接口 + AllowAll + Service 接入 | 424 passed | Protocol + Decision，Service & API 层已接入 |
| RBAC-3C-0 | operator → actor_id 可信审计身份迁移 | 438 passed | effective_operator + mismatch 事件日志 |
| RBAC-3D-1 | `created_by` 写入端修复 | ✅ 458 passed | `resolve_effective_created_by`，4 个入口已修复 |

**当前状态**：基础设施就绪，默认宽松（dev mode = `platform_admin`，AllowAllApprovalPolicy = 全部放行）。**不等于完整 IAM。**

---

## 二、待决策问题及默认策略

### 问题列表（提取自 `docs/13_rbac_auth_integration_plan.md` 第十二节）

| # | 问题 | 推荐默认策略 | 原因 | 实现阶段 | 可配置 |
|:---:|------|------|------|:---:|:---:|
| 1 | **四眼原则默认开启？** | `HUB_FOUR_EYES_REQUIRED=false` | dev/demo 兼容，渐进式收紧 | RBAC-3C | ✅ 环境变量 |
| 2 | **Admin 是否豁免四眼原则？** | 默认豁免 + 记录日志 | 紧急操作通道 | RBAC-3C | ✅ P2 可配 |
| 3 | **找不到 submitter 时行为？** | fail-open + 记录 warning 事件 | 历史数据可能没有可信 submitter | RBAC-3C | ✅ P2 fail-closed |
| 4 | **四眼原则作用范围？** | 仅 `approve` | reject/request-change 是风险降低动作；publish 依赖 approved 状态 | RBAC-3C | — |
| 5 | **Security Reviewer + Business Approver 分离还是合并？** | P1 合并为 OR（任一 review:approve 即通过） | 当前无 ApprovalStage/Task 数据结构 | RBAC-3C（不做），P2 AND | ✅ P2 |
| 6 | **对象级 ownership 何时实现？** | RBAC-3C 不实现，RBAC-3D 单独处理 | 需要 owner_id 字段扩展和对象级策略 | RBAC-3D | ✅ |
| 7 | **Owner 是否新增 `owner_id` 字段？** | P1 复用 `created_by`；长期新增 `owner_id` | 避免 RBAC-3C 引入 schema 变更 | RBAC-3D | — |
| 8 | **body.operator 何时废弃？** | 保留兼容，审计优先用 actor_id | 避免破坏现有客户端 | P2 optional，P3 废弃 | ✅ |
| 9 | **Runtime Consumer 何时收紧？** | RBAC-3C 不处理 | 独立阶段，需 CapabilityAccessPolicy 替换 | RBAC-4 | ✅ |
| 10 | **export:download 何时收紧？** | 保留现状 | 结合 ownership 一起收敛 | RBAC-3D+ | ✅ |

---

## 三、推荐默认策略详述

### 3.1 四眼原则

**默认策略：关闭。**

```
HUB_FOUR_EYES_REQUIRED=false  # 默认
```

| 维度 | 策略 |
|------|------|
| 作用动作 | 仅 `approve` |
| 不受限的动作 | `reject` / `request-change` / `publish` |
| 默认值 | `false`（关闭） |
| 生产建议 | 开启（`true`） |
| Admin | 默认豁免 + 记录日志 |
| submitter 不可用 | fail-open + 记录 `four_eyes.submitter_unknown` warning |

**原因：**
- 保持 dev/demo 完全兼容，零破坏行变更；
- `publish` 已依赖 `approved` 状态，间接受四眼保护；
- `reject` 和 `request-change` 是风险降低动作，不应该被四眼原则卡住。

**判定逻辑：**

```
can_approve(operator, version):
  if dev mode or admin:
    return allow (+ log exemption)
  if not HUB_FOUR_EYES_REQUIRED:
    return allow
  submitter = get_submitter(version)
  if submitter is None:
    log warning → fail-open → allow
  if operator == submitter:
    return deny("four_eyes: submitter cannot approve own version")
  return allow
```

### 3.2 Admin 豁免

**默认策略：Admin 豁免，但记录日志。**

| 维度 | 策略 |
|------|------|
| 条件 | `platform_admin` 角色 |
| 行为 | 允许通过，记录 `auth.four_eyes.admin_exempted` 日志 |
| 可配置 | P2 增加 `HUB_FOUR_EYES_ADMIN_EXEMPT=true` 开关 |

**原因：**
- 便于紧急操作（故障需要立即发布修复版本）；
- 当前无多级管理员模型，不存在 Admin 滥用监管。

### 3.3 找不到 submitter

**默认策略：fail-open。**

| 维度 | 策略 |
|------|------|
| 行为 | 允许通过，记录 `auth.four_eyes.submitter_unknown` warning |
| 查询来源 | `LifecycleEvent` 表 `action="submitted"` 的 `operator` 字段 |
| fail-close | P2 通过 `HUB_FOUR_EYES_UNKNOWN_SUBMITTER=block` 切换 |

**原因：**
- 历史数据（RBAC-3C-0 之前）可能没有可信 actor_id；
- 避免因历史记录缺失导致存量版本无法审批。

### 3.4 Security Reviewer 与 Business Approver

**默认策略：P1 合并为 OR。**

两个角色共享 `review:approve` / `review:reject` / `review:request_change` 权限，当前 RBAC-2 矩阵已为两者分配相同权限集。任一角色 approve 即可进入 `approved` 状态。

| 维度 | 策略 |
|------|------|
| P1（当前） | OR：任一 review:approve 角色 approve 即通过 |
| P2 | AND：双阶段审批（需先有 ApprovalStage 数据结构） |
| 实现代价 | P1 零代价（当前矩阵已是 OR），P2 需数据模型扩展 |

### 3.5 对象级 ownership

**默认策略：RBAC-3C 不实现，RBAC-3D 单独处理。**

| 维度 | 策略 |
|------|------|
| RBAC-3C | 不涉及 ownership |
| RBAC-3D | 正式处理 |
| 临时 owner | 复用 `created_by` 字段 |
| 长期 owner | 新增 `owner_id` 字段（model + migration） |
| 粒度 | own/other 区分 |

**原因：**
- RBAC-3C 聚焦审批流程内部约束（四眼原则），不扩大范围；
- ownership 需要对象级策略判断和可能的数据模型扩展（schema change）；
- 分阶段处理避免耦合。

### 3.6 body.operator 兼容字段

**默认策略：保留兼容，审计优先用 actor_id。**

| 维度 | 策略 |
|------|------|
| P1（当前） | `body.operator` 保留，不参与鉴权 |
| 审计身份 | RBAC-3C-0 已实现优先 `ctx.actor_id`，fallback `body.operator` |
| P2 | `body.operator` 改为 `Optional`，允许不传 |
| P3 | 废弃 `body.operator`，仅接受 Header 身份 |

### 3.7 Header 模式

**默认策略：RBAC-3C 不改变 Header 模式行为。**

Header 模式当前行为：从 Gateway 注入的 `X-Actor-ID` / `X-Roles` 等 Header 解析 AuthContext。RBAC-3C 在上述上下文下执行四眼原则。

OIDC / JWT 在 RBAC-5 实现，不在 RBAC-3C 中实现。

### 3.8 Runtime Consumer

**默认策略：RBAC-4 已完成。**

Runtime API 已接入 `require_runtime_permission`（入口级 role/scope 检查）+ `ScopedCapabilityAccessPolicy`（资产级可见性过滤）。Runtime Consumer 角色权限集在管理态 RBAC 中保持为空，Runtime 权限独立管理。

| 维度 | 策略 |
|------|------|
| RBAC-3C | 不涉及 Runtime |
| RBAC-4 | 角色检查 + ScopedCapabilityAccessPolicy |

### 3.9 export:download

**默认策略：保留当前配置。**

当前 `export:download` 权限由以下角色持有：Admin / Owner / Contributor / SecReviewer / Approver / Publisher / Auditor。此配置偏宽，但结合 ownership（RBAC-3D）和敏感字段过滤一起收敛。

### 3.10 operator_mismatch 事件

**默认策略：不采样，全部记录。**

`auth.operator_mismatch` 事件已实现（RBAC-3C-0）。默认不采样，全部记录。如日志量过高再考虑采样。

---

## 四、RBAC-3C 实现边界

### 4.1 RBAC-3C 实现范围（已完成）

| 实现项 | 说明 | 状态 |
|--------|------|:---:|
| `DefaultApprovalPolicy` | 替换 `AllowAllApprovalPolicy`，实现完整 `ApprovalPolicy` Protocol | ✅ |
| `ApprovalPolicyContext` | 数据类，携带 submitted_by 等上下文 | ✅ |
| 四眼原则检查 | `can_approve` 中检查 `operator == submitted_by` | ✅ |
| submitter 查询 | 从 `LifecycleEvent` 查询 `event_type=submitted` + `version_id` | ✅ |
| `HUB_FOUR_EYES_REQUIRED` | 环境变量，默认 `false` | ✅ |
| Admin 豁免 | `platform_admin` 角色跳过四眼检查 + 记录日志 | ✅ |
| submitter 不可用 | fail-open + 记录 `auth.four_eyes.submitter_unknown` | ✅ |
| policy deny | 返回 `ApprovalPolicyDecision.deny(four_eyes_violation)` → 403 | ✅ |
| policy deny 日志 | `approval.policy_denied` 事件 | ✅ |
| dev mode | 不变（`platform_admin`，连带豁免） | ✅ |
| Protocol 兼容 | `policy_context: ApprovalPolicyContext \| None = None` 向后兼容 | ✅ |
| 测试 | 11 新增测试（unit + integration） | ✅ 445 passed |

### 4.2 RBAC-3C 不实现

| 不实现项 | 原因 | 计划 |
|----------|------|:---:|
| 对象级 ownership | 需 model 扩展 + 对象级策略 | RBAC-3D |
| 双阶段审批（AND） | 需 ApprovalStage/Task 数据结构 | P2/RBAC-5 |
| OIDC / JWT 校验 | 独立认证机制 | RBAC-5 |
| Runtime Consumer 权限 | 独立权限域 | RBAC-4 |
| body.operator 废弃 | 过渡期兼容 | P2/P3 |
| 多租户隔离 | 超出当前范围 | P3 |
| waiver 机制 | 高级审批策略 | P3 |
| DB schema 变更 | 保持零 schema change | — |
| 新依赖引入 | 全部使用现有库 | — |

---

## 五、RBAC-3C 涉及的决策子问题

以下问题在 RBAC-3B 已解决（本节仅记录确认项）：

| 子问题 | 已有实现 | 状态 |
|--------|----------|:---:|
| ApprovalPolicy 接口定义 | `Protocol` 5 个方法 | ✅ |
| Decision 模式 | `allow()` / `deny(reason, reason_code)` | ✅ |
| Service 层接入 | `approval_service.py` / `lifecycle_service.py` | ✅ |
| API 层 403 处理 | `ApprovalPolicyDeniedError` → 403 | ✅ |
| 策略注入 | 构造函数默认参数 `policy=None` → AllowAll | ✅ |
| 权限粒度 | RBAC 角色级 + ApprovalPolicy 业务级 两层 | ✅ |
| allow 优先级 | RBAC 先于 ApprovalPolicy | ✅ |

---

## 六、RBAC-3D 对象级 Ownership 方案设计

### 6.0 当前数据模型分析

| 字段 | HubItem | HubItemVersion | 来源 |
|------|:---:|:---:|------|
| `created_by` | ✅ `str \| None`（nullable） | ✅ `str \| None`（nullable） | 请求体传入 |
| `owner_id` | ❌ | ❌ | — |
| 创建时已知 ctx.actor_id | ❌（API handler 未注入 AuthContext） | ❌ | — |

**关键发现：**

1. `HubItem.created_by` 存在，但来自请求体 `HubItemCreate.created_by`，**不是可信身份**（前端可任意设置）；
2. `HubItemService.create` 直接使用 `data.created_by`，不经过 `AuthContext`；
3. `ImportService.import_package` **不设置 created_by**（Line 170-183）；
4. `OpenapiImportService` **不设置 created_by**；
5. `LivecycleEvent` 可以推断创建人，但仅在 `submit_version` 时才记录（迟于创建）；
6. 不改 DB 的"复用 created_by"方案不够可靠：**created_by 当前不可信，需要先修复写入端**。

### 6.1 三种方案对比

| 维度 | 方案 A：新增 owner_id | 方案 B：复用/修复 created_by | 方案 C：暂不实现 |
|------|------|------|------|
| DB 变更 | 需 migration | 不需（字段已在） | 不需 |
| 代码变更 | model + migration + API | API handler 注入 + service 调整 | 无 |
| 可信度 | 高（独立字段，服务端控制） | 中（需修复写入端为可信） | N/A |
| 测试成本 | 中 | 低 | 无 |
| owner 转移 | 天然支持 | 不支持 | N/A |
| 长久生产 | 推荐 | 可临时代替 | 不推荐 |
| 本轮交付 | RBAC-3D-1（先设计） | RBAC-3D-1（先修复 created_by） | 本轮 |

### 6.2 推荐方案：B→A 渐进

**分两步走：**

1. **RBAC-3D-1（本轮）**：修复 `created_by` 写入端，使其来自 `AuthContext.actor_id`，不再信任客户端传值；
2. **RBAC-3D-2（稍后）**：基于可信 `created_by` 实现对象级 `OwnershipPolicy`；
3. **RBAC-3D-3（长期）**：新增 `owner_id` 字段，支持所有权转移。

**理由**：HubItem 已有 `created_by` 字段，修复写入端（从请求体改为 AuthContext）是**最小的代码变更**，无需 migration，测试成本最低。待对象级 ownership 在生产中验证后，再引入 `owner_id` 独立字段。

### 6.3 created_by 修复策略

当前问题：

```
请求体 HubItemCreate.created_by → Service → HubItem.created_by
  ↑ 不可信：前端/Curl 可任意设置
```

修复后：

```
AuthContext.actor_id → API handler 覆盖 → HubItem.created_by
  ↑ 可信来源（dev: "dev-admin", header: Gateway 注入）
```

**具体修改**（设计阶段，不写代码）：

1. `backend/app/api/hub_items.py:create_item` —— 注入 `ctx: AuthContext = Depends(get_auth_context)`，设置 `data.created_by = ctx.actor_id`；
2. `backend/app/api/imports.py:import_package` —— 同上；
3. `backend/app/api/openapi_imports.py:import_openapi` —— 同上；
4. `backend/app/api/versions.py:create_version` —— 同上。

**历史数据**：已存在的 `created_by` 可能为空或不可信。对象级 ownership 首次运行时 log warning，不阻断操作。

### 6.4 对象级权限模型设计

#### 规则表

| 角色 | 动作 | 条件 |
|------|------|------|
| `platform_admin` | 全部管理态动作 | 无限制 |
| `asset_owner` | `asset:update` / `version:edit` / `scan:run` / `review:submit` / `relation:create` / `export:download` | `item.created_by == ctx.actor_id` |
| `asset_owner` | `asset:update` 等对 other 资产 | ❌ 403 |
| `contributor` | `asset:create` / `version:create` | 无限制（创建即 Owner） |
| `contributor` | `version:edit` / `scan:run` / `review:submit` / `relation:create` | `item.created_by == ctx.actor_id` |
| `contributor` | 对 other 资产操作 | ❌ 403 |
| `publisher` | publish/disable/rollback | 当前不做 ownership 过滤（publisher 角色级权限，但后续可收紧） |
| `security_reviewer` / `business_approver` | approve/reject/request_change | 不依赖 ownership |
| `auditor` | audit:read / asset:read | 只读，无 ownership 限制 |
| `runtime_consumer` | 无管理态权限 | N/A |

#### 决策子问题

| # | 问题 | 默认策略 |
|:---:|------|------|
| 1 | `asset_owner` 能否 publish 自己资产？ | **不能**，publish 仍由 `publisher` 或 `admin` |
| 2 | `contributor` 能否 `relation:create` 到别人资产？ | **只能**对自己资产创建 outgoing relation |
| 3 | `export:download` 是否按 ownership 收紧？ | **是**：owner/admin/auditor/publisher 可下载；contributor 仅下载自己资产 |
| 4 | `contributor` 能否创建 relation 指向别人资产？ | 暂不做拦截（outgoing 方向已限制，incoming 由对方控制） |

### 6.5 OwnershipPolicy 设计

**新增**：`backend/app/policies/ownership_policy.py`

```python
# 设计原型
class OwnershipPolicy:
    def can_manage(self, ctx: AuthContext, item: HubItem, permission: str) -> bool
    def get_owner_id(self, item: HubItem) -> str | None

def is_asset_owner(ctx: AuthContext, item: HubItem) -> bool:
    return item.created_by is not None and item.created_by == ctx.actor_id

def require_asset_permission(permission: str, item_loader):
    """FastAPI Depends: 角色检查 + ownership 检查"""
```

**与现有 RBAC 的集成**：

```
请求进入
  ├── require_permission(permission) → 角色级检查（已有 RBAC-2）
  ├── require_asset_permission(permission, item_loader) → 对象级检查（新增 RBAC-3D）
  └── ApprovalPolicy → 业务策略检查（已有 RBAC-3C）
```

**item_loader 设计**：
- 从 `request.path_params` 提取 `item_id`
- 在 FastAPI Depends 中查询 HubItem
- 避免重复查询：利用 SQLAlchemy identity map

### 6.6 实现阶段拆分

| 阶段 | 内容 | 修改文件 | DB 变更 |
|:---:|------|------|:---:|
| RBAC-3D-0 | 方案设计（本轮） | `docs/14_rbac_decision_record.md` | 无 |
| RBAC-3D-1 | 修复 `created_by` 写入端为 AuthContext | `hub_items.py` / `imports.py` / `openapi_imports.py` / `versions.py` | 无 |
| RBAC-3D-2 | 实现 `OwnershipPolicy` + 6 个管理态 API 加对象级检查 | `ownership_policy.py` / 受影响的 API handlers | 无 |
| RBAC-3D-3 | `owner_id` 独立字段 + owner 转移 | model + migration + service | 有 |
| RBAC-3D-4 | Runtime Consumer（RBAC-4） | 独立阶段 | 待定 |

### 6.7 测试计划（RBAC-3D-2 时实现）

| # | 场景 | 预期 |
|:---:|------|------|
| 1 | `platform_admin` 可 update 任意资产 | ✅ |
| 2 | `asset_owner`（created_by="u1"）update 自己的 item（created_by="u1"） | ✅ |
| 3 | `asset_owner`（created_by="u1"）update 他人 item（created_by="u2"） | ❌ 403 |
| 4 | `contributor` 创建 asset → created_by 自动设为 actor_id | ✅ |
| 5 | `contributor` 编辑自己 draft | ✅ |
| 6 | `contributor` 编辑他人 draft | ❌ 403 |
| 7 | `publisher` 可 publish（不依赖 ownership） | ✅ |
| 8 | `auditor` 可读不可写 | ✅ |
| 9 | `export:download` 他人资产被拒 | ❌ 403 |
| 10 | `relation:create` 仅允许自己资产 outgoing | ❌ 403 若 source 非 own |
| 11 | Runtime API 不受 ownership 影响 | ✅ |
| 12 | dev mode 兼容现有测试 | ✅ |
| 13 | import 时 created_by 自动设为 actor_id | ✅ |

---

## 七、文档索引

| 文档 | 与本决策记录的关系 |
|------|------|
| `docs/07_rbac_approval_design.md` | 设计规格（"是什么"） |
| `docs/13_rbac_auth_integration_plan.md` | 实施方案（"怎么做"） |
| `docs/14_rbac_decision_record.md`（本文档） | 决策记录（"为何这样决定"） |

---

## 八、决策生效

本文档中所有决策自 2026-05-27 起生效。后续阶段如需要变更决策，应在本文档末尾追加变更记录：

| 日期 | 变更项 | 原策略 | 新策略 | 原因 |
|------|--------|--------|--------|------|
| 2026-05-27 | RBAC-3C 实现 | 待实现 | 已完成（DefaultApprovalPolicy + 四眼原则） | 决策已确认后实施 |
| 2026-05-27 | RBAC-3D-2 ownership 策略 | 待实施 | ✅ 已完成 | 12 个端点 + 13 测试 |
