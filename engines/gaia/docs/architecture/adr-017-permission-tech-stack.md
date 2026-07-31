# ADR-017: 权限治理技术选型（Cedar + cashews + Better Auth + SqlGlot）

| 字段 | 值 |
|------|-----|
| 状态 | Accepted（2026-07-08 评审） |
| 日期 | 2026-07-08 |
| 决策者 | 开发者 + 评审 |
| 影响 | 新增依赖 `cedarpy`、`cashews`、`authlib`；引入 Better Auth（TypeScript/Node.js）独立认证服务；策略表达式从 simpleeval 切换到 Cedar；Doris/Trino 行级下推统一走 SqlGlot AST 注入；Principal 注入层从零搭建 |
| 关联文档 | [adr-016-permission-governance.md](./adr-016-permission-governance.md)（权限治理架构决策，本 ADR 细化其技术实现）、[permission-governance-design.md](../design/permission-governance-design.md)（详细设计）、[permission-tech-stack-deep-dive.md](../research/permission-tech-stack-deep-dive.md)（深度选型研究，本 ADR 的依据） |
| 取代 | ADR-016 D6（策略引擎 simpleeval 自建 → Cedar）、ADR-016 D8（Doris 原生 Row Policy → SqlGlot AST 注入）、ADR-016 D9（OIDC Keycloak/Authelia → Better Auth 双场景）的技术实现部分。ADR-016 的架构决策（五层模型/RBAC×MAC/资源归属）不变 |

## 背景

ADR-016 确立了权限治理的**架构决策**（Organization+Space+Project 三层、RBAC×MAC 混合、五层校验、多引擎下推），但其 D6/D8/D9 对**技术实现**留了过时假设：

- D6 假设「一期 simpleeval 够用，二期评估 OPA/Cerbos」——但 simpleeval 有五项根本缺陷（黑名单 AST 过滤、无类型系统、无法下推、无 partial evaluation、无安全审计），不应用于安全策略
- D8 假设「Doris 原生 Row Policy + Group→Role 映射 + `current_user_region()`」——但 Doris Row Policy 是静态谓词不支持运行时上下文，且 Gaia 单用户连接池下 root/admin 不受 Row Policy 约束
- D9 假设「OIDC 对接 Keycloak/Authelia」——但 Python 生态无 Spring Security 等价物，且 Keycloak 无法原生满足「本地用户管理 + 企业联邦」双场景共存

[深度选型研究](../research/permission-tech-stack-deep-dive.md)基于一手证据（官方文档/源码/benchmark/学术论文/独立安全基准）重新选型，本 ADR 沉淀最终结论。

## 决策

四项核心技术选型，均为开源成熟方案，覆盖权限治理全部技术栈。

### D1: 策略求值与表达式引擎 —— Cedar（cedarpy）

**决策**：采用 [Cedar](https://github.com/cedar-policy/cedar)（AWS 开源策略语言）的 Python 绑定 [cedarpy](https://github.com/k9securityio/cedar-py) 作为策略求值引擎（PDP）与行/列级表达式引擎。**放弃 simpleeval**。

**Cedar 同时解决两个问题**：
1. **策略求值引擎**（AuthorizationService PDP）：`is_authorized(request, policies, entities)` 做五层校验决策
2. **行/列级表达式引擎**：RowSecurityPolicy / PropertyMaskingPolicy 的表达式用 Cedar 策略语言编写，而非 simpleeval Python 表达式

**理由**：
- **语言级安全**：Cedar 是非图灵完备专用策略语言，从设计层面排除任意函数调用/属性反射/循环/递归。Trail of Bits + Teleport SPEF 独立安全基准（27 测试用例）证实 Cedar 安全确定性远超 Rego（[SPEF 结果](https://goteleport.com/blog/benchmarking-policy-languages/)）
- **类型系统 + schema 验证**：策略加载时验证类型，错误部署前暴露，非运行时抛异常
- **Partial Evaluation（TPE）**：资源未知时部分求值产生类型安全残差，可可靠翻译为各引擎 SQL WHERE——这是行级下推的正确路径，simpleeval 做不到
- **进程内嵌**：cedarpy 预编译 manylinux wheel，`pip install` 即可，无需 Rust 工具链，无额外服务（对比 OPA 必须 sidecar）
- **生产成熟**：cedarpy v4.8.6，PyPI Trusted Publishing + SLSA 构建证明 + zizmor CI 审计；Cedar 有 OOPSLA 论文、AWS Verified Permissions 生产规模部署
- **性能**：PolicySet 句柄复用后单次授权 ~120-168µs（大策略 60 规则）

**对 simpleeval 的处理**：现有 ActionRuleEngine（业务规则引擎，5 文件引用）一期保留不换——它是 Action 参数推导/校验，非安全策略，关注点不同。权限策略必须用 Cedar。二期可选迁移 ActionRuleEngine 的 validation 规则到 Cedar。

**Cedar 落地**：
- Gaia 权限模型（Organization/Space/Project/Group/Marking/ObjectType）映射为 Cedar schema + entities
- 五层校验映射为 Cedar policies（permit/forbid/when/unless/in）
- `PolicySet` 启动时编译一次进程内复用；`Entities` principal slice 会话级缓存
- 行级下推：`is_authorized_partial` 产生残差 → 翻译为 SQL 谓词（见 D4）

### D2: 缓存层 —— cashews

**决策**：采用 [cashews](https://github.com/Krukov/cashews)（async-first 多 backend 缓存框架）作为权限缓存。**不自建缓存抽象层**。

**理由**：
- **async-first**：与 Gaia FastAPI + SQLAlchemy async 技术栈契合
- **URL 驱动 backend 切换**：`cache.setup("mem://")` ↔ `cache.setup("redis://host:6379")`，**同一套代码，改 URL 切换单机/分布式**——满足「切换依赖不改代码」需求
- **tag 失效**（权限缓存最关键能力）：`cache.set(key, value, tags=[...])` + `cache.delete_tags("tag")`，精准批量失效
- **分布式锁**：`set_lock` 防 stampede
- **client-side caching**（Redis 6+ tracking）：多实例跨进程缓存一致性，无需手写 Pub/Sub
- **生产成熟**：v7.5.0，PandaDoc 生产使用，580+ stars

**实测验证**（Gaia .venv Python 3.12.3）：基本 get/set/delete、pattern 批量删除、分布式锁、tag 失效、backend 切换全部端到端通过。

**集成**：`AuthorizationService` 直接依赖 `cashews.Cache`，三级缓存（用户属性/资源属性/授权结果）用 key 前缀 + tag 区分，切换 backend 只改 `settings.cache.url`。

### D3: 身份认证 —— Better Auth（双场景）+ Authlib（应用层 JWT 验证）

**决策**：采用 [Better Auth](https://github.com/better-auth/better-auth)（27.5k stars，Vercel 2026-07 收购）作为独立认证服务（TypeScript/Node.js），Gaia FastAPI 用 [Authlib](https://github.com/authlib/authlib) 验证其签发的 JWT。**满足双场景需求**：
1. **简单场景**：自己管理用户/角色/分组（Better Auth Admin + Organization + emailAndPassword 插件）
2. **企业场景**：对接已有企业用户系统（Better Auth SSO 插件 OIDC/SAML + SCIM 自动同步）

**为什么 Better Auth**：
- **Spring Security 的现代等价物**——应用内嵌、plugin-based、composable，是 2026 年最先进的认证框架（非协议库、非 IDP only）
- **双场景原生共存**——account linking 默认开启，本地用户与企业 SSO 身份通过邮箱自动关联，同一用户可有多重身份
- **企业联邦最完整**——SSO 插件支持 OIDC + OAuth2 + SAML 2.0（Okta/Azure AD/任意 SAML IdP），SCIM 插件让企业 IdP 自动同步用户，内置 SAML 安全防护（InResponseTo/replay/timestamp/签名/加密）
- **联邦运行时动态注册**——`registerSSOProvider` API 运行时配置企业 IdP（存数据库，不用改代码重启），OIDC 自动 discovery，邮箱域名匹配自动路由
- **应用层控制用户**——Admin 插件 REST API，Gaia 前端直接调用做用户管理，不像 Keycloak 要跳外部系统

**为什么不用 Python 原生方案**：Python 生态没有 Spring Security 等价物（[SSOJet](https://ssojet.com/blog/enterprise-sso-in-fastapi-how-to-add-saml-and-oidc-auth-to-python-apis-in-2026) 证实「Python's enterprise SSO ecosystem is thinner than Java/.NET」）。AuthX 协议覆盖不足，python-social-auth 无 async 无 FastAPI adapter，fastapi-oidc 依赖停摆的 python-jose。

**Node.js 运行时成本（已确认可接受）**：
- 能力门槛不存在：Gaia web-ui 已是 TypeScript，团队有 TS 能力
- 运维同构：Gaia 已是多引擎容器架构（PG/Gravitino/Doris/Trino/Neo4j/SeaTunnel/Kafka），再加 Node 认证服务同构
- 资源轻量：Better Auth Server 用 Hono（轻量），有开源 starter 模板（[oil-auth](https://github.com/savioruz/oil-auth) 等），核心代码 ~20 行配置 + ~10 行 provisionUser 回调
- 不用从头写：登录/注册/SSO/SAML/SCIM/session/JWT 签发/密码哈希/2FA 全由 Better Auth 内置

**Gaia 应用层**：Authlib 验证 Better Auth 签发的 JWT（async 原生、无 python-jose 技术债），自补 ~80 行 FastAPI Depends 适配（Authlib 无 FastAPI Resource Server 集成，有 ResourceProtector 原语）。`PrincipalService` 做 claims→Gaia Principal 映射（业务逻辑，约 50 行）。

**JWT 签名与验证（Phase 5 实现细化）**：Better Auth 的 `jwt()` 插件用**非对称密钥**（默认 EdDSA/Ed25519）签发 JWT，公钥通过 `/api/auth/jwks` 端点暴露（JWKS）。Gaia `PrincipalService` 用 Authlib 的 `JsonWebKey`/`KeySet` 拉取 JWKS（进程级缓存 10 分钟，未知 `kid` 触发重拉）验签 + 校验 `iss`/`aud`（默认 Better Auth `baseURL`）。**不使用 HS256 共享密钥验签**——`BETTER_AUTH_SECRET` 仅用于 Better Auth 会话 cookie 签名，不参与 JWT 验证。这是生产级路径（非对称签名 + 可轮换密钥 + 公钥可独立分发）。

**部署**：Better Auth Server 独立 Docker 容器，共享 Gaia PostgreSQL（独立 `better_auth` schema）。首启后需运行 `npx @better-auth/cli migrate`（在 `auth-server/` 目录）创建 9 张表（user/session/account/verification/organization/member/invitation/ssoProvider/jwks）。认证（你是谁）归 Better Auth，授权（你能做什么）归 Gaia Cedar，通过 JWT 解耦。

**开发模式 fallback**：一期可不部署 Better Auth，用 `X-User-Id` 请求头 fallback。

> **实现注记（2026-07-10）**：实际实现中 JWT 验证用 [`fastapi-betterauth`](https://pypi.org/project/fastapi-betterauth/) 替代了手写 Authlib 适配。`fastapi-betterauth` 封装了 PyJWKClient（内置 JWKS 缓存 + key rotation + EdDSA 支持 + iss/aud 校验），比手写 Authlib JWKS fetch + KeySet cache 更成熟且代码量更少。`PrincipalService._verify_token` 调 `BetterAuth.fetch_token(token)` 验证，`_claims_to_principal` 做 claims→Principal 映射。此外，JWT 验证后还需用 `sub` 查 Gaia `users` 表加载 group memberships（Better Auth JWT 不带 groups，组授权铁律要求 groups 来自 Gaia DB）。前端 JWT 注入用独立 `jwt-store.ts`（同步 localStorage + 内存），不用 effect 注册 token provider（消除竞态）。详见 [`docs/engineer/permission-roadmap-and-principles.md`](../engineer/permission-roadmap-and-principles.md) §三 设计原则 6/7。

### D4: 行级下推 —— SqlGlot AST 注入（统一机制，放弃 Doris 原生 Row Policy）

**决策**：Doris/Trino/任何 SQL 引擎的行级权限下推**统一走 SqlGlot AST 注入**。**放弃 Doris 原生 Row Policy**。

**理由**：
- **Doris Row Policy 是静态谓词**——USING 不能引用 session 变量/UDF 运行时求值（与 PG RLS 的 `current_setting` 本质不同），过滤值必须创建策略时写死
- **Gaia 单用户连接池不兼容**——Doris root/admin 不受 Row Policy 约束；改 per-user/per-group 连接池会连接池爆炸 + 违反组授权铁律
- **SqlGlot AST 注入安全等价**——谓词在 SQL 发给 Doris 前注入，Doris 在 scan 节点执行 WHERE 过滤无权行，无权数据不离开引擎。这是「应用层构造谓词，引擎执行过滤」，非「应用层后过滤」（后过滤是查完再 Python 过滤，可绕过）
- **架构统一**——Doris/Trino/PG 任何 SQL 引擎同一套下推机制，方言切换即可
- **避免 Doris DDL 同步开销**——不用 CREATE ROLE/ROW POLICY/GRANT，不用 outbox 同步，不用处理 Group 生命周期与 Doris 一致性
- **Gaia 已依赖 sqlglot>=30.0**，且有生产级参考实现（[AskTable SQL Permission Guard](https://www.asktable.com/en-US/blog/2026-03-05/asktable-sql-permission-guard-sqlglot)，递归 Scope 处理子查询/CTE/UNION/JOIN，<10ms；Apache Superset 也有 sqlglot RLS）

**下推链路**：
```
Cedar is_authorized_partial（resource 未知）
  → TPE 产生残差（求值掉 principal 部分，只剩 resource 属性条件）
  → 残差翻译为 SQL 谓词（确定性映射表：== → =, in → IN, && → AND，符合红线 8）
  → SqlGlot AST 注入（递归处理所有 Scope：子查询/CTE/UNION/JOIN）
  → Doris/Trino 引擎执行 WHERE 过滤
```

**保留**：
- **PG RLS** 保留（object_state 写入校验 WITH CHECK，这是 PG 独特能力，SqlGlot 注入只管读路径）
- **Doris 列脱敏**保留原生 MASK 函数 + VIEW（存储层脱敏，与行级下推是独立机制）
- **Neo4j** 用 Cypher WHERE 属性驱动过滤（Community 无 FGAC，不变）
- **Doris 原生 Row Policy** 保留为二期可选纵深防御层（若未来 Doris 对外暴露直连再评估）

### D5: 工具层权限声明 —— pydantic-ai RunContext 原生 DI

**决策**：工具层权限声明用代码内 `ToolPermission` 注册表 + pydantic-ai `RunContext[GaiaDeps]` 原生依赖注入。**不用额外框架**。

**理由**：Gaia AG-UI Agent 基于 pydantic-ai（`pydantic-ai-slim[ag-ui]==2.0.0`），其 `RunContext[Deps]` 类型化依赖注入天然解决 Principal 透传。`@agent.tool` 装饰器自动注入 context，Principal 作为 Dep。

**声明格式**：代码内 `ToolPermission` dataclass + 注册表（类型安全 + IDE 可追溯，不用 YAML 避免漂移）。动态 resource_id 从工具参数取值（声明记 `resource_id_param` 参数名）。

### D6: Cedar 策略 LLM 辅助生成（二期）—— 生态完整

**决策**：二期实现自然语言→Cedar 策略生成，复用 Gaia `/ai/generate` + cedarpy `validate_policies` + VS Code Cedar 扩展工具链。

**生态依据**：
- [AutoCedar](https://github.com/neselab/cedar-synthesis-engine)（学术+开源，verifier-guided 合成，CVC5 SMT 验证）
- AWS Bedrock AgentCore 用 Cedar 保护 agentic workflows（生产实践）
- [vscode-cedar](https://github.com/cedar-policy/vscode-cedar)（官方，语法高亮/校验/IntelliSense）

## 四项选型总览

| 关注点 | 选型 | 角色 |
|--------|------|------|
| 策略求值 + 表达式引擎 | **Cedar（cedarpy）** | PDP 决策 + 行/列级表达式 + TPE 残差下推 |
| 缓存 | **cashews** | 三级权限缓存，URL 驱动切换单机/分布式 |
| 认证（双场景） | **Better Auth（Hono 独立服务）** | 本地用户管理 + 企业 SSO 联邦 + account linking |
| 应用层 JWT 验证 | **Authlib**（自补 FastAPI 适配） | 验证 Better Auth 签发的 JWT |
| 行级下推 | **SqlGlot AST 注入** | Doris/Trino/PG 统一下推机制 |
| 工具层权限 | **pydantic-ai RunContext** | Principal 依赖注入 + 工具权限声明 |

## 与 ADR-016 的关系

ADR-016 的**架构决策不变**（D1-D5 组织模型、D7 标记、D10 Scenario 协同）。本 ADR 细化其**技术实现**：
- ADR-016 D6（策略引擎）→ 本 ADR D1（Cedar 替代 simpleeval）
- ADR-016 D8（下推分层）→ 本 ADR D4（SqlGlot 统一注入替代 Doris Row Policy）
- ADR-016 D9（身份对接）→ 本 ADR D3（Better Auth 替代 Keycloak/Authelia）

## 未决问题

- **LDAP 直连**：Better Auth 无原生 LDAP 插件（靠 SSO 联邦 LDAP IdP 转 OIDC）。纯 LDAP 直连场景若必需，补 ldap3（模式 C）。一期不处理
- **Doris 纵深防御**：若未来 Doris 对外暴露直连，需评估 per-group Doris Role + Row Policy 作为应用层注入之外的二级防护

## 参考

- [深度选型研究](../research/permission-tech-stack-deep-dive.md)（本 ADR 完整依据）
- [Cedar OOPSLA 论文](https://arxiv.org/pdf/2403.04651)
- [Trail of Bits / Teleport SPEF 安全基准](https://goteleport.com/blog/benchmarking-policy-languages/)
- [cedarpy benchmark](https://github.com/k9securityio/cedar-py/blob/main/BENCHMARKS.md)
- [Cedar TPE RFC](https://github.com/cedar-policy/rfcs/blob/main/text/0095-type-aware-partial-evaluation.md)
- [cashews 文档](https://github.com/Krukov/cashews)
- [Better Auth SSO 插件](https://better-auth.com/docs/plugins/sso)
- [Vercel 收购 Better Auth](https://vercel.com/blog/vercel-acquires-better-auth)
- [AskTable SQL Permission Guard（SqlGlot 参考）](https://www.asktable.com/en-US/blog/2026-03-05/asktable-sql-permission-guard-sqlglot)
- [pydantic-ai Dependencies](https://pydantic.dev/docs/ai/core-concepts/dependencies/)
