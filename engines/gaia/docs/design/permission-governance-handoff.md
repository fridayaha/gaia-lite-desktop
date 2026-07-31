# 权限治理体系 —— 开发交接输入

> **⚠️ 状态：已完成实现**（2026-07-10）。本文档是开发前的交接输入，保留作为历史参考。当前实现状态、后续待办、设计原则见 [`docs/engineer/permission-roadmap-and-principles.md`](../engineer/permission-roadmap-and-principles.md)。
>
> **实现与设计的差异**（开发中调整）：
> - JWT 验证用 `fastapi-betterauth` 替代手写 Authlib（更成熟，内置 JWKS 缓存）
> - 前端 JWT 注入用 `jwt-store.ts` 单一真相源（同步 localStorage），不用 effect 注册 token provider
> - JIT auto-provisioning 用 Better Auth `databaseHooks.user.create.after`（非 SSO provisionUser）
> - 身份管理 API 新增 `/identity/*` 和 `/containers/*` 路由（设计文档 §七 未列出）
> - OWNER/EDITOR 角色补齐了 view 权限（设计文档的初始角色定义漏了）
>
> **原文档内容**（开发前交接输入，保留不动）：

> **用途**：本文件是权限治理体系从「架构设计 + 技术选型」阶段交接给「开发实现」阶段的完整输入。新会话的 AI 助手读取本文件 + 下列文档后，即可开始编码实现。
> **日期**：2026-07-08
> **前置状态**：架构决策（ADR-016）+ 技术选型（ADR-017）+ 详细设计（设计文档）+ 深度研究（研究文档）全部完成并一致，无遗留未决问题。

---

## 一、必读文档（按优先级）

开发前**必须完整阅读**以下文档，按顺序：

| 顺序 | 文档 | 行数 | 作用 |
|:---:|------|:---:|------|
| 1 | [ADR-016: 权限治理体系](../architecture/adr-016-permission-governance.md) | 212 | **架构决策**——Organization+Space+Project 三层、RBAC×MAC、五层校验、9 项核心决策（D1-D10） |
| 2 | [ADR-017: 权限治理技术选型](../architecture/adr-017-permission-tech-stack.md) | 171 | **技术选型**——Cedar + cashews + Better Auth + SqlGlot，6 项决策（D1-D6），取代 ADR-016 的 D6/D8/D9 技术实现 |
| 3 | [权限治理详细设计文档](../design/permission-governance-design.md) | 1681 | **实现蓝图**——数据模型/Service 层/AuthMiddleware/查询下推/Action 改造/工具层/API 路由/前端/迁移/测试，十大章节 |
| 4 | [技术选型深度研究](../research/permission-tech-stack-deep-dive.md) | 1489 | **选型依据 + 参考实现**——四项选型的一手证据 + 四个待定点验证 + **§八 自建代码的参考实现**（开源参考，不从头写） |
| 5 | [前端交互与开发者体验研究](../research/permission-frontend-ux-and-developer-experience.md) | 570 | **前端 UX 依据**——业界最佳实践（Palantir 就近管理 / Databricks 中央视图 / AWS IAM）+ 自然感五维度 + 三道门反「藏按钮」+ Gaia 界面方案 + 开发者体验（C4 模型/快速上手/可调试性），设计文档 §八 的研究依据 |
| 6 | [评估报告](../architecture/permission-governance-landing-assessment.md) | 1081 | **现状基线 + 分期路线**——Gaia 现状核查、Phase 0-5 实施路线 |
| 7 | [前期研究（数据下推+组件选型）](../research/permission-data-pushdown-and-python-components.md) | 457 | 各引擎下推机制 + OIDC/SCIM 组件（部分已被 ADR-017 勘误，见研究文档 §六勘误表） |

**关键**：研究文档 §六有勘误表，指明前期研究哪些结论已被 ADR-017 取代。读前期研究时对照勘误表。

---

## 二、技术栈选型结论（四项核心 + 补充）

### 四项核心技术选型（均为开源成熟方案）

| 关注点 | 选型 | 角色 | 关键依据 |
|--------|------|------|---------|
| **策略求值 + 表达式引擎** | [Cedar（cedarpy）](https://github.com/k9securityio/cedar-py) | PDP 决策 + 行/列级表达式 + TPE 残差下推 | 非图灵完备、类型安全、Trail of Bits 安全背书、进程内嵌、<168µs |
| **缓存** | [cashews](https://github.com/Krukov/cashews) | 三级权限缓存，URL 驱动切换单机/分布式 | async-first、tag 失效、分布式锁、client-side caching、v7.5 |
| **认证（双场景）** | [Better Auth](https://github.com/better-auth/better-auth)（Hono 独立服务） | 本地用户管理 + 企业 SSO 联邦 + account linking | 27.5k stars、Vercel 收购、Spring Security 等价物、SSO/SAML/SCIM 插件 |
| **应用层 JWT 验证** | [Authlib](https://github.com/authlib/authlib)（自补 ~80 行 FastAPI 适配） | 验证 Better Auth 签发的 JWT | async 原生、无 python-jose 技术债、ResourceProtector 原语 |

### 补充选型

| 关注点 | 选型 | 说明 |
|--------|------|------|
| 行级下推 | **SqlGlot AST 注入**（Doris/Trino/PG 统一机制） | 放弃 Doris 原生 Row Policy；Cedar TPE 残差→SQL 谓词→SqlGlot 递归注入 WHERE |
| 工具层权限 | **pydantic-ai RunContext** 原生 DI | Principal 作为 Dep 注入，ToolPermission 注册表 |
| Cedar LLM 辅助（二期） | /ai/generate + cedarpy validate + vscode-cedar | AutoCedar 学术参考 |

### 明确放弃的方案（不要走回头路）

| 方案 | 放弃理由 |
|------|---------|
| **simpleeval** | 五项根本缺陷（黑名单 AST/无类型/无法下推/无 partial eval/无安全审计），不用于安全策略 |
| **Doris 原生 Row Policy** | 静态谓词不支持运行时上下文 + root/admin 不受约束 + 单用户连接池不兼容 |
| **OPA sidecar** | 必须 sidecar 部署，SqlGlot 注入更轻更可控 |
| **Casbin** | 10k 规则 500ms + 缓存 bug + 不支持 SQL 下推 |
| **Keycloak/Authelia 做 IDP** | 无法原生满足双场景（本地用户+企业联邦）共存；Better Auth 是 Spring Security 等价物 |
| **fastapi-oidc** | 依赖停摆的 python-jose + 同步 requests |
| **自建缓存抽象层** | cashews 已覆盖全部需求 |

---

## 三、架构关键决策摘要

### 五层权限校验（串行，任一层拒即终止）

```
请求 → AuthMiddleware 提取 Principal（Better Auth JWT → Authlib 验证 → claims→Principal）
     → AuthorizationService.check_access(principal, resource, action)
         Layer 1: 身份认证（Principal 有效性）
         Layer 2: Organization 校验（主体强隔离，MAC）
         Layer 3: Space 校验（业务域，组织白名单）
         Layer 4: Project RBAC（角色授予 Group，选项 B fallback）
         Layer 5: Marking MAC（合取校验，AND）
     → 行/列级下推（Cedar TPE 残差 → SqlGlot AST 注入）
```

### 数据模型四组表

```
第一组：三层容器    Organization → Space(↔Ontology 1:1) → Project
第二组：身份层      Principal ← User/Group/ServiceUser，Group↔User via GroupMembership
第三组：权限规则    Role+RoleAssignment(RBAC) / Marking+Grant+Assignment(MAC) / RowSecurityPolicy+PropertyMaskingPolicy(ABAC)
第四组：治理凭证    AuditLog（追加写入）/ AccessRequest（JIT）
```

### 行级下推链路（统一机制）

```
Cedar is_authorized_partial（resource 未知）
  → TPE 产生残差（求值掉 principal，只剩 resource 属性条件）
  → 残差翻译为 SQL 谓词（确定性映射表：== → =, in → IN, && → AND）
  → SqlGlot AST 递归注入 WHERE（子查询/CTE/UNION/JOIN 全覆盖）
  → Doris/Trino/PG 引擎执行过滤（数据不离开引擎）
```

### 双场景认证

```
场景 1（本地用户）：Better Auth emailAndPassword + Admin 插件 + Organization 插件
场景 2（企业联邦）：Better Auth SSO 插件（OIDC/SAML）+ SCIM 插件 + provisionUser 属性同步
共存机制：account linking（邮箱匹配自动关联）
```

---

## 四、自建代码的参考实现（不从头写）

**关键**：研究文档 §八 详细列出了每个自建模块的开源参考实现。开发时务必先读参考再写代码。

| Gaia 自建模块 | 参考实现 | 借鉴要点 |
|-------------|---------|---------|
| Cedar 集成层 | [atlas9 博文](https://atlas9.dev/blog/access-with-cedar.html) + [cedarpy-example](https://github.com/k9securityio/cedarpy-example-hello-photos) | 组嵌套（`in` 运算符）、引用资源校验（应用层循环）、list 查询（partial eval 转 SQL）、policy templates |
| PG RLS 生成 + SET LOCAL | [ulfblk-multitenant](https://pypi.org/project/ulfblk-multitenant/) + [django-rls-tenants](https://dvoraj75.github.io/django-rls-tenants/advanced/architecture/) | `before_cursor_execute` 事件注入 SET LOCAL；RLSConstraint 声明式生成 CREATE POLICY |
| **SqlGlot AST 注入器** | [**AskTable SQL Permission Guard**](https://www.asktable.com/en-US/blog/2026-03-05/asktable-sql-permission-guard-sqlglot) | **架构直接借鉴**——Scope 递归 + 条件注入 + 别名处理 + 去重，<10ms |
| FastAPI 认证中间件 | [FastAPI 官方](https://fastapi.tiangolo.com/tutorial/security/get-current-user/) + [safeguard 最佳实践](https://safeguard.sh/resources/blog/fastapi-authentication-best-practices-2026) | 分层依赖链（verify_jwt→get_current_user→require_permission），request.state 传 Principal |
| **审计日志** | [**immutable audit trail (FastAPI+async SA)**](https://dev.to/codemalasartes/an-immutable-audit-trail-for-ai-agent-actions-fastapi-async-sqlalchemy-4m4c) | **技术栈一致直接借鉴**——append-only 强制 + DB 角色权限 + 哈希链（二期） |
| 权限缓存失效 | [cashews 文档](https://github.com/Krukov/cashews) | tag 失效 + set_lock + client_side caching，原语直接用 |

---

## 五、分期实施路线

按 [评估报告 §四](../architecture/permission-governance-landing-assessment.md) Phase 0-5 分期，每期 TDD：

| Phase | 内容 | 关键产出 |
|:---:|------|---------|
| **0** | 三层容器 + 身份层 + 现有模型加归属字段 | Organization/Space/Project/Principal/User/Group ORM + Alembic migration + 默认 org 兜底 |
| **1** | 角色层（RBAC） | Role + RoleAssignment + AuthorizationService 骨架（Layer 1-4） + Cedar 集成 |
| **2** | 标记层（MAC） | Marking + Grant + Assignment + Layer 5 合取校验 + 权责分离 |
| **3** | 行/列级 + 下推 | RowSecurityPolicy + PropertyMaskingPolicy + Cedar TPE + SqlGlot 注入器 + PG RLS + object_state RLS |
| **4** | 审计 + JIT | AuditLog（append-only）+ AccessRequest + Check Access API |
| **5** | 前端 + Better Auth | 权限管理 UI（**就近管理**：资源详情面板 Access tab，非孤立权限页；**三道闸门**而非藏按钮；**Ship the decision**：后端返回 `allowedActions`+`disabledReasons`，前端渲染不推导；详见设计文档 §八 + 前端研究文档 §二/§三）+ Better Auth Server 部署 + Authlib JWT 验证 + PrincipalService |

**一期开发模式 fallback**：可不部署 Better Auth，用 `X-User-Id` 请求头开发测试。

---

## 六、开发约束与避坑（必读）

### 技术约束

1. **Python 3.12.3**（`.python-version`），async 优先，ruff line-length=120，mypy --strict
2. **SQLAlchemy 2.0 async ORM**，禁止裸 SQL；pydantic v2 与 ORM 分离；`datetime.now(UTC)`；`uuid.uuid4().hex` 主键
3. **Alembic 单一真相源**——schema 变更必须走 Alembic migration，autogenerate + 人工 review + `alembic check` 无漂移
4. **Gaia 已依赖 sqlglot>=30.0**——SqlGlot 注入器无需加依赖；cedarpy/cashews/authlib 需加 pyproject.toml
5. **Better Auth Server 是 Node.js 服务**——独立 Docker 容器，共享 Gaia PostgreSQL（独立 `better_auth` schema），有开源 starter（[oil-auth](https://github.com/savioruz/oil-auth)）

### 关键避坑（详见设计文档各章节「避坑」段）

| 坑 | 避坑 |
|----|------|
| PG RLS + PgBouncer | 用 `SET LOCAL`（事务级）非 `SET`（session 级），防跨请求上下文泄露 |
| PG RLS + superuser | 应用连接不用 superuser（BYPASSRLS），用普通角色 + FORCE RLS |
| Doris 列脱敏 | 原生 MASK 函数 + VIEW（存储层脱敏，无需 Ranger） |
| 前端当安全边界 | 前端只是体验优化，后端 Cedar 五层校验是唯一安全边界（三道闸门） |
| 前端镜像后端规则 | Ship the decision——后端返回 allowedActions，前端渲染不推导 |
| SSO 组织 scope | SSO 登录自动设置 activeOrganizationId（Better Auth PR #9024） |
| 缓存高敏操作 | 权限授予/角色变更/标记移除/数据删除强制实时校验，不走缓存 |
| 审计日志可篡改 | append-only（只暴露 append()）+ DB 角色只授 INSERT/SELECT |

### 测试规范（CLAUDE.md 强制）

- 每次新增/修改代码，commit 前必须：补充单元测试 + `make test` 全绿 + 本地冒烟
- DB 写入逻辑用真 DB 不全 mock（不能只断言 `commit.assert_awaited()`）
- 前端改动 `pnpm run typecheck` + `pnpm run build`
- 验证脚本：`scripts/verify_permission_live.py` + `scripts/verify_rls_live.py`

---

## 七、新会话启动指令

新会话开始时，给 AI 助手的输入：

```
我们要开始实现 Gaia 权限治理体系。请先完整阅读以下文档（按顺序）：

1. docs/architecture/adr-016-permission-governance.md（架构决策）
2. docs/architecture/adr-017-permission-tech-stack.md（技术选型）
3. docs/design/permission-governance-design.md（详细设计，十大章节）
4. docs/research/permission-tech-stack-deep-dive.md（选型依据 + §八自建代码参考实现）
5. docs/architecture/permission-governance-landing-assessment.md（现状基线 + Phase 0-5 路线）

重点注意：
- 技术栈：Cedar（cedarpy）+ cashews + Better Auth + Authlib + SqlGlot，不用 simpleeval/OPA/Casbin/Doris Row Policy
- 自建代码有开源参考实现（研究文档 §八），先读参考再写代码
- 按 Phase 0-5 分期实施，每期 TDD
- 遵循 CLAUDE.md 开发规范（async/ruff/mypy strict/Alembic/测试规范）

读完后告诉我你理解的实施路线，我们从 Phase 0 开始。
```

---

## 八、文档一致性状态

所有文档已更新一致，无遗留矛盾：

- ADR-016 D6/D8/D9 → 指向 ADR-017（架构决策不变，技术实现细化）
- 设计文档 §0.7/§1.5/§2.1/§2.3/§3.1/§4.0-4.7/§6.1-6.2/§8.1-8.7/§10.1 → 全部反映最终方案
- 设计文档无过时残留（simpleeval/current_user_region/gaia_group_/Keycloak/Authelia/fastapi-oidc 全部清除）
- CLAUDE.md ADR 索引已补 ADR-016/ADR-017
- 研究文档 §六勘误表指明前期研究的过时结论
