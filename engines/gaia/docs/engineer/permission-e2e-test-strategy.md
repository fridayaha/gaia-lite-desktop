# 权限治理端到端测试策略

> **状态：已实现**（2026-07-10）。E2E 测试脚本 `scripts/verify_permission_e2e.py` 已完成，46 用例全绿。本文档保留作为测试策略参考。

## 目标

验证 Gaia 权限治理体系从「零部署」到「复杂企业权限管理」的完整链路，覆盖：
1. 默认角色/用户/容器的创建与初始化
2. 各角色（PLATFORM_ADMIN / SPACE_OWNER / EDITOR / VIEWER / DISCOVERER / MARKING_ADMIN / AUDIT_ADMIN）的权限边界
3. 页面级权限（前端 PermissionGate 三道闸门）
4. 数据级权限（行级下推、列级脱敏、Marking MAC 合取）
5. 端到端场景（从创建用户到受限访问的完整流程）

## 测试架构

### 场景设计：模拟一个企业「汽车门店营销」业务

```
Organization: org-default (单租户)
  └─ Space: Marketing (1:1 Ontology: Marketing)
       └─ Project: marketing-ops (协作单元)
            ├─ Group: marketing-admins → OWNER 角色
            ├─ Group: marketing-editors → EDITOR 角色
            ├─ Group: marketing-viewers → VIEWER 角色
            └─ Group: marketing-discoverers → DISCOVERER 角色

全局角色:
  ├─ Group: platform-admins → PLATFORM_ADMIN
  ├─ Group: marking-admins → MARKING_ADMIN
  └─ Group: audit-admins → AUDIT_ADMIN

用户:
  ├─ admin@gaiatest.com (Better Auth, role=admin → PLATFORM_ADMIN) — 平台管理员
  ├─ alice (OWNER 组) — 本体负责人
  ├─ bob (EDITOR 组) — 本体编辑者
  ├─ carol (VIEWER 组) — 只读用户
  ├─ dave (DISCOVERER 组) — 仅发现者
  ├─ eve (MARKING_ADMIN 组) — 标记管理员
  └─ frank (AUDIT_ADMIN 组) — 审计管理员
```

### 测试维度矩阵

| # | 场景 | 主体 | 期望结果 | 验证方式 |
|---|------|------|----------|----------|
| 1 | admin 查看/编辑/删除本体 | admin (PLATFORM_ADMIN) | 全部允许 | allowedActions 含 view/edit/delete |
| 2 | OWNER 编辑本体 | alice | 允许 edit | allowedActions 含 edit |
| 3 | VIEWER 编辑本体 | carol | 禁止 edit | allowedActions 不含 edit, disabledReasons 有 |
| 4 | DISCOVERER 查看数据 | dave | 禁止 object:view | allowedActions 不含 object:view |
| 5 | 匿名访问 | anonymous | 全部禁止 | 所有 allowedActions 为空 |
| 6 | MARKING_ADMIN 管理标记 | eve | 允许 marking:manage | 可创建标记分类/标记 |
| 7 | MARKING_ADMIN 管理项目 | eve | 禁止 project:admin | separation of duties |
| 8 | AUDIT_ADMIN 看审计 | frank | 允许 audit:read | 可查询 audit-logs |
| 9 | AUDIT_ADMIN 操作数据 | frank | 禁止 object:write | separation of duties |
| 10 | Marking 合取校验 | carol (无 PII 标记) | 数据不可见 | 资源带 PII 标记 → carol 看不到 |
| 11 | 角色授予 | admin | 可授予角色 | role:manage 通过 |
| 12 | 非管理员授予角色 | carol | 禁止 | role:manage 拒绝 |

## 实现状态（已完成）

### 后端管理 API ✅
- `POST /identity/users` / `GET /identity/users` — User CRUD（含 JIT auto-provisioning via X-Provision-Token）
- `POST /identity/groups` / `GET /identity/groups` — Group CRUD
- `POST /identity/groups/{id}/members` / `DELETE /identity/groups/{id}/members/{uid}` — 成员管理
- `GET /identity/users/{id}/groups` — 用户所属组
- `POST /containers/organizations` / `GET /containers/organizations` — Organization
- `POST /containers/spaces` / `GET /containers/spaces` — Space（自动建 Ontology + default Project）
- `POST /containers/projects` / `GET /containers/projects` — Project
- `GET /containers/roles` — 角色列表

### E2E 测试脚本 ✅
`scripts/verify_permission_e2e.py` — 46 用例，4 个阶段：
- Phase 0: Bootstrap defaults（默认 org/space/project/roles 存在性验证）
- Phase 1: 创建测试 identity（groups/users/role assignments，幂等）
- Phase 2: 权限矩阵验证（10 个 allowed-actions 场景）
- Phase 3: 权责分离（MARKING_ADMIN/VIEWER/anonymous 边界）
- Phase 4: 审计可观测性（audit logs + check-access explainability）

### 前端测试 ✅
- `src/pages/__tests__/IdentityManagementPage.test.tsx` — 页面渲染 + tab 切换 + 权限门控
- `src/components/permission/__tests__/` — PermissionGate/PermissionedRoute/AccessDecisionPanel
- `src/hooks/__tests__/useAllowedActions.test.ts` — ship-the-decision hook
- `src/api/__tests__/client-auth.test.ts` — JWT 注入
- `src/lib/__tests__/auth-client.test.ts` — JWT 生命周期

### 运行方式
```bash
# 后端启动后（dev mode 或 JWT mode）
.venv/bin/python scripts/verify_permission_e2e.py --base-url http://127.0.0.1:46094

# 生产模式（带 admin JWT）
.venv/bin/python scripts/verify_permission_e2e.py --base-url http://... --admin-jwt <JWT>
```

## 原始实现计划（保留参考）

### ~~Phase 1: 后端管理 API~~ ✅ 已完成
路由路径调整为 `/identity/*` 和 `/containers/*`（非设计文档的 `/authz/groups`）

### ~~Phase 2: 测试数据初始化~~ ✅ 已完成
内联在 E2E 脚本 Phase 1，幂等（409 时自动查已有记录）

### ~~Phase 3: 端到端测试脚本~~ ✅ 已完成
`scripts/verify_permission_e2e.py`，46 用例

### ~~Phase 4: HTML 测试报告~~ ✅ 已完成
脚本自动生成 HTML + JSON（运行时产物，不入 Git）
