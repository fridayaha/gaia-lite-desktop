# 权限治理体系 — 后续路线图与设计原则备忘

> **本文档**记录权限治理体系（ADR-016/017）当前实现状态、后续待办、以及过程中沉淀的设计原则和避坑要点。供后续开发者参考，避免重复踩坑。

---

## 一、当前实现状态（截至 2026-07-10）

### ✅ 已完成

| 模块 | 能力 | 关键文件 |
|------|------|----------|
| **AuthorizationService** | 五层校验 PDP（身份→Org→Space→Project RBAC→Marking MAC） | `services/authorization_service.py` |
| **PrincipalService** | 双模式：dev（X-User-Id + DB group 加载）/ JWT（fastapi-betterauth JWKS + DB group enrichment） | `services/principal_service.py` |
| **9 个内置角色** | bootstrap 自动 seed，OWNER/EDITOR 含 view 权限（高权限⊇低权限） | `core/permission_roles.py` |
| **IdentityService** | User/Group/GroupMembership CRUD + 权限门控 | `services/identity_service.py` |
| **ContainerService** | Org/Space/Project CRUD（Space 创建自动建 Ontology + default Project） | `services/container_service.py` |
| **MarkingService** | MAC 标记合取校验 + 权责分离（MARKING_ADMIN 管定义，PROJECT_OWNER 管打标） | `services/marking_service.py` |
| **AccessRequestService** | JIT 临时权限申请 + 审批 + 到期回收 | `services/access_request_service.py` |
| **PermissionEnvelope** | ship-the-decision（allowedActions + disabledReasons 批量返回） | `services/permission_envelope.py` |
| **行/列级下推** | SqlGlot AST 注入 + 属性脱敏 | `services/sql_injector.py` |
| **审计日志** | append-only，记录五层决策 | `models/permission.py` AuditLogModel |
| **JIT Auto-Provisioning** | Better Auth 注册 → 自动创建 Gaia user（databaseHooks） | `auth-server/auth.ts` |
| **前端三道闸门** | PermissionGate / PermissionedRoute / ForbiddenPage | `components/permission/` |
| **前端 jwt-store** | 同步 localStorage + 内存，消除竞态 | `lib/jwt-store.ts` |
| **前端身份管理页** | 用户组/用户双 tab + 成员管理 + 角色授予可视化 + 用户详情 | `pages/IdentityManagementPage.tsx` |
| **前端 Access tab** | 资源详情（本体/数据源）的访问控制面板 | `ObjectDetailPanel.tsx` / `DataSourceDetail.tsx` |
| **Better Auth 服务端** | Hono + Better Auth（emailAndPassword + admin + organization + jwt + sso） | `auth-server/auth.ts` |

### 🟡 已实现但需完善

| 模块 | 现状 | 待完善 |
|------|------|--------|
| 角色→权限映射 | 9 个内置角色硬编码 | 自定义角色 UI（二期） |
| provisionUser 钩子 | 只打 log | 同步 OIDC claims → Gaia attributes（region/department） |
| 行级安全策略 | Cedar 引擎 + SqlGlot 注入已实现 | 前端策略编辑器（LLM 辅助，二期） |
| PG RLS | 未启用 | Phase 3 启用 object_state RLS（需 PG session context 注入） |
| SCIM | Better Auth 有插件未激活 | 二期企业 SSO 场景激活 |

---

## 二、后续待办

### Phase 5 收尾（近期）✅ 已落地 (2026-07-10)

1. **Better Auth 容器配置固化** — `auth-server/auth.ts` 加 key rotation（`rotationInterval: 90d` + `gracePeriod: 30d`，> JWT 1h + JWKS cache 5min，防 4 分钟窗口破产）；docker-compose 补 `GAIA_API_URL`/`GAIA_PROVISION_TOKEN` env（JIT 桥接）；`.env.example` 补 secret 固定化说明。验证脚本：`scripts/verify_better_auth_e2e.py`（注册→登录→换 JWT→调 Gaia→验 JWKS）
2. **databaseHooks 事务陷阱验证** — 确认 better-auth ≥ PR #7345 版本（`user.create.after` 在事务提交后执行）。`auth.ts` JIT 已是非阻塞 best-effort（try/catch + 409 幂等）✓
3. **端到端验证脚本** — `scripts/verify_better_auth_e2e.py`（7 步：health/JWKS/注册/登录/JWT/Gaia 调用/principal 解析）
4. **角色授予 UI 通用化** — 仍待前端补 IdentityManagementPage 组详情「授予角色」表单
5. **用户停用** — 仍待前端补用户详情「停用」按钮

### Phase 7 LLM 辅助策略生成 ✅ 已落地 (2026-07-10)

- **数据模型**：`RowSecurityPolicyModel` 加 `generated_by` + `generation_meta`（Alembic migration `8549c04f46b9`）
- **Service**：`services/ai_policy_generate.py` — verifier-guided 闭环（LLM 提议 → `cedarpy.validate_policies` 语法+类型闸门 → repair loop 3 轮 → `is_authorized` floor/ceiling 干跑预览）。LLM 永远不直接执行，输出是 draft，必须 HITL 审批后 POST 落库
- **路由**：`POST /authz/generate-policy`（生成 draft）+ `POST /authz/row-security-policies`（HITL 落库，带 defense-in-depth re-validate）+ `GET/DELETE` CRUD
- **测试**：`tests/unit/services/test_ai_policy_generate.py`（19 测试：validate/parse/dry_run/generate_policy 完整闭环）
- **关键设计**：Cedar schema 用 `_build_validation_schema`（principal attributes 嵌套 `attributes` Record），与 `cedar_engine.build_cedar_schema`（partial eval 用，扁平）不同——validate_policies 要求 schema 与 `principal.attributes["X"]` 语法一致。markings 用 `.contains()` 非 `in`（`in` 是 entity group membership）

### Phase 7 选项 B→A 迁移运行时 ✅ 已落地 (2026-07-10)

- **DB 迁移**：已完成（`d4a1b2c3e5f7` backfill + `e5b2c3d4f6a8` NOT NULL + FK CASCADE）
- **运行时能力**：`AuthorizationService.preview_migration_impact`（gain/lose/unchanged diff）+ `migrate_object_type_to_project`（OWNER on both Projects 权限门 + project_id 更新 + 缓存失效 + 审计）
- **meta_store**：`get_object_type_by_id` / `update_object_type_project` / `get_project` / `get_default_project_for_space` / `get_group`
- **路由**：`GET /authz/migration-impact/{ot_id}?target_project_id=` + `POST /authz/migrate-object-type?object_type_id=&target_project_id=`
- **测试**：`tests/unit/services/test_migration_impact.py`（7 测试：impact diff/identical/not_found/permission_denied + migration updates DB/audits）
- **Palantir 对齐**：迁移不可逆（project_id NOT NULL），但可再迁到另一个 Project；影响分析 API 让 admin 预览哪些组失权

### Phase 6 企业联邦（中期，待开发）

1. **SCIM 插件激活** — `npm install @better-auth/scim` + `scim()` 插件 + 配置 IdP（Okta/Entra ID）
2. **provisionUser 属性同步** — SSO 登录时把 OIDC claims（region/department/level）写入 Gaia user.attributes（当前只打 log）
3. **rule-based group** — Better Auth group assignment rules
4. **多租户** — Organization 管理界面

### Phase 7 治理增强（远期，待开发）

1. **自定义角色 UI** — 角色模型已支持，缺创建 UI
2. **血缘传播** — 标记自动传播到派生资源（依赖血缘引擎）
3. **PG RLS 启用** — object_state 行级安全纵深防御
4. **审计日志增强** — SHA-256 hash chain + SIEM 导出

---

## 三、核心设计原则（过程中沉淀）

### 1. 认证-授权分离，JIT 桥接

> Better Auth 管认证（注册/登录/会话），Gaia 管授权（角色/组/属性）。两者通过 JWT 的 `sub` claim 关联。JIT auto-provisioning（`databaseHooks.user.create.after`）自动在 Gaia 创建 user 记录，消除手动复制 uid 的断裂。

**避坑**：
- `databaseHooks.user.create.after` 在事务提交后运行，适合调外部 API（官方 stripe customer 示例）
- `before` 钩子在事务内，调外部 API 会导致死锁/超时
- JIT 失败不能阻塞 Better Auth 注册（非阻塞，best-effort）

### 2. 组授权铁律

> 100% 权限授 Group，零直接个人授权。人员异动只调 GroupMembership，不改资源权限。

**实现要点**：
- `RoleAssignment.principal_id` 指向 Group，不指向 User
- `resolve_effective_role_scopes` 用 `[principal.id] + principal.groups` 查 — principal.id 不会匹配任何 role_assignment（因为授权在 group），只有 groups 会匹配
- 前端身份管理页以「组」为主 tab，「用户」为辅 tab（对齐 Palantir Platform Settings）

### 3. Ship the Decision，不 Ship the Policy

> 后端返回 `allowedActions` + `disabledReasons`，前端只渲染不推导。避免前后端各写一遍权限逻辑导致 drift。

**实现要点**：
- `action_registry` 是声明式的单一真相源（每个 resource_type 注册它的 actions）
- `PermissionEnvelope.check_access_batch` 批量求值，避免 N+1
- 前端 `useAllowedActions` hook 一次请求获取整页所有资源的权限决策
- 前端 `PermissionGate` 组件声明式门控，不写 `if (user.role === 'admin')`

### 4. 高权限角色 ⊇ 低权限角色

> OWNER 必须包含 VIEWER 的所有权限。能编辑却不能查看是角色定义 bug。

**避坑**：初始角色定义里 OWNER/EDITOR 只有 `edit` 没有 `view`，导致 OWNER 登录后 allowedActions 不含 `ontology:view`，被 Layer 4 拒绝。所有写角色必须同时包含对应的读权限。

### 5. Principal 是 frozen 的

> pydantic v2 的 `Principal` model 默认 frozen。不能直接 `principal.groups = [...]`，必须用 `principal.model_copy(update={...})` 返回新实例。

**避坑**：`_resolve_jwt` 里直接赋值 `principal.groups` 会抛 `frozen_instance` validation error。

### 6. JWT 模式必须从 DB 加载 groups

> Better Auth 的 JWT 只带 `sub/email/roles`，不带 groups。`_resolve_jwt` 验证完 JWT 后必须用 `sub` 查 Gaia `users` 表，再查该 user 的 groups，填入 `principal.groups`。否则 `resolve_effective_role_scopes` 找不到任何 role assignment，所有非 PLATFORM_ADMIN 用户被全拒。

**实现要点**：
- `PrincipalService.__init__` 接受 `metadata` 参数（DB session）
- `AuthMiddleware` 每次请求创建带新 DB session 的 PrincipalService（避免单例 session 泄漏）
- dev mode `_resolve_dev` 也在 `X-User-Roles` 为空时从 DB 加载 groups

### 7. jwt-store 单一真相源

> 前端 JWT 存储用独立的 `jwt-store.ts`（无 React 依赖，同步 localStorage + 内存）。不散落在 client.ts / auth-client.ts / useAuth.ts。`request()` 直接调 `getJwt()`（同步），消除 effect 注册竞态和 HMR 模块实例不一致。

**避坑**：
- 不要用 `useEffect` 注册 token provider — 子组件的 API 请求在 effect 跑之前就发出了
- 不要用模块级 `_tokenGetter` 变量 — HMR 会导致多个模块实例，注册到错误的实例
- Better Auth Bearer plugin 文档 §5 的 `token: () => localStorage.getItem(...)` 是正确模式

### 8. 权责分离

> MARKING_ADMIN 管数据密级定义和授权，不管项目；PROJECT_OWNER 管协作和打标，不管密级定义。防止单一角色即可完全放开数据权限。

**实现要点**：
- `MarkingService.create_marking` 要求 MARKING_ADMIN
- `MarkingService.assign_marking` 要求 PROJECT_OWNER/EDITOR
- `MarkingService.grant_marking` 要求 MARKING_ADMIN
- PLATFORM_ADMIN 默认无数据访问权限（管权限不看数据）

### 9. 渐进式披露

> 单租户默认 Organization 不暴露三层容器管理。高级面板（标记/策略/角色/三层容器）需明确进入「设置」模式。

**实现要点**：
- `GET /auth/deployment-info` 返回 `is_multi_tenant` 信号
- 前端 Layout 根据 `is_multi_tenant` 决定是否显示 Organization 管理入口
- 权限管理降级为「设置」导航组的二级入口

### 10. 不可见即安全

> 无权限资源在前端/搜索/API/SQL 完全隐藏，不提示「无权限」，防枚举探测。但用户主动尝试访问被拒时，须提供可读的拒绝原因 + 申请权限入口。

**实现要点**：
- `PermissionEnvelope.filter_visible` 在后端列表响应中丢弃无权资源
- `allowedActions` 为空时前端隐藏按钮（不置灰）
- 用户主动访问被拒时 `disabledReasons` 提供原因 + Check Access 面板

---

## 四、避坑速查表

| # | 坑 | 根因 | 解法 |
|---|-----|------|------|
| 1 | OWNER 能编辑但不能查看 | 角色定义缺 view 权限 | 高权限角色必须包含低权限的所有操作 |
| 2 | JWT 登录后所有非 admin 用户全拒 | `_resolve_jwt` 没从 DB 加载 groups | 验证 JWT 后用 sub 查 Gaia users → 加载 groups |
| 3 | `principal.groups = [...]` 报错 | pydantic v2 frozen | 用 `model_copy(update={...})` |
| 4 | 前端首次请求没带 JWT | effect 注册 token provider 太晚 | `jwt-store.ts` 同步读 localStorage，不用 effect |
| 5 | HMR 后 JWT 注入失效 | 模块多实例 | jwt-store 单一真相源，不依赖模块级变量 |
| 6 | properties 表 NOT NULL project_id 报错 | ORM 加了列但 DB 没 migration | Alembic migration: nullable → backfill → NOT NULL → FK |
| 7 | Better Auth `/api/auth/token` 404 | vite proxy 没代理 `/api/auth` 到 3000 | vite.config.ts 加 `'/api/auth': 'http://localhost:3000'` |
| 8 | Better Auth 登录 403 Invalid origin | TRUSTED_ORIGINS 缺前端端口 | 容器 env 加上前端 origin（每个端口都要加） |
| 9 | JWKS 私钥解密失败 | 容器重启后 BETTER_AUTH_SECRET 变了 | 用固定 secret（.env 管理），不要用默认值 |
| 10 | role_assignment scope 对不上 | 角色授在 default Space 的 project，但本体属于 marketing Space | 授角色时选对 Project（本体所属 Space 的 default Project） |

---

## 五、关键架构决策记录

### 5.1 为什么 Gaia 有自己的 users 表（而不是直接用 Better Auth 的）

对齐 Palantir Foundry：Better Auth（IdP）管认证，Gaia 管授权属性。Gaia 的 `users` 表存 `attributes`（部门/区域/职级）——这是行级安全策略的数据源（`principal.attributes['region'] == row['region']`）。Better Auth 的 user 表不存这些业务属性。两者通过 `subject`（= JWT `sub` = Better Auth uid）关联。

### 5.2 为什么用 JIT 而不是 SCIM

- JIT 实现成本低（Better Auth `databaseHooks`，几行代码），适合当前阶段
- SCIM 实现成本高（需要 IdP 配置 + SCIM 协议适配），适合企业 SSO 场景
- 业界共识：「JIT for onboarding, SCIM for governance」，最终两者都支持
- JIT 的离职残留风险通过 admin 手动停用兜底（一期），SCIM 实时停用（二期）

### 5.3 为什么 AuthMiddleware 每次请求创建新 PrincipalService

PrincipalService 需要 DB session 来加载 groups/attributes。如果用单例，DB session 会在请求间共享，导致：
- 跨请求 session 污染
- Greenlet finalization 错误
- session 关闭后后续请求失败

每次请求创建新 PrincipalService + 新 DB session，请求结束后关闭。dev mode 不需要 DB（header 模式），用单例无开销。

### 5.4 为什么 ROLE 资源类型注册到 action_registry

前端 `useAllowedActions('ROLE', ['*'])` 需要获取 `role:manage` 权限决策来门控身份管理页的创建/修改按钮。如果 ROLE 不注册到 action_registry，`allowed-actions` 端点返回空 actions 列表，前端永远拿不到 `role:manage` 决策，PermissionGate 永远隐藏按钮。
