# 权限治理二期落地指导 — (一) Better Auth 部署 + RS256/JWKS · (二) LLM 辅助策略生成 · (四) 选项 B→A 迁移

> **用途**:本文档为权限治理二期三项优先落地工作提供基于一手调研的落地指导原则、实践参考与避坑指南。所有结论均有出处(链接/源码/Gaia 现状代码),供实现时直接参照。
>
> **调研日期**:2026-07-10
> **调研覆盖**:Better Auth JWT/JWKS 官方文档 + 源码(fastapi-betterauth)、RFC 8725 JWT BCP、JWT alg-confusion CVE-2026 系列、JWKS key rotation 运维 runbook、Cedar/AutoCedar 论文 + cedarpy API、AWS AgentCore NL2Cedar、Palantir Foundry 项目权限迁移官方文档、零停机 DB 迁移 expand/contract 模式、Better Auth databaseHooks 事务陷阱。
> **关联**:ADR-016(架构)/ ADR-017(技术选型)/ `permission-roadmap-and-principles.md`(现状与原则)/ `permission-governance-design.md`(详细设计)/ `permission-governance-handoff.md`(交接输入)。
> **现状基线**:Phase 0-5 已落地(1502 单测 + 13 E2E),PrincipalService 双模式(dev/JWT)已实现,fastapi-betterauth 已接入,auth-server/auth.ts 含 JIT databaseHooks + sso provisionUser。本文档针对三项的**生产化闭环 / 能力深化 / 架构演进**。

---

## 总体优先级与依赖关系

```
(一) Better Auth 部署 + RS256/JWKS  ←─ P0,阻塞 (二) 的安全基座
        │
        │ 认证生产化后,principal.attributes 才可靠
        ▼
(二) LLM 辅助策略生成  ←─ P1,依赖 Cedar schema 注入 + validate_policies 闸门
        │
        │ 生成的 RowSecurityPolicy 落库后参与权限校验
        ▼
(四) 选项 B→A 迁移  ←─ P2,独立架构演进,触发于多团队需求,不依赖前两项
        │
        │ project_id NULL→填充,权限查询 fallback 自动跳过
```

**关键结论(一句话)**:
- **(一)** 当前是"代码就绪,工程化未闭环"。容器重建 + 固定 secret + key rotation 配置 + 端到端验签验证即可闭环。最大陷阱是 `databaseHooks.after` 的事务时机(Better Auth #7260/#7345)和 JWKS key rotation 的 overlap window。
- **(二)** LLM 生成策略**永远不能直接执行**,必须经 cedarpy `validate_policies` + schema 类型校验闸门。AutoCedar 论文证明:纯 LLM 直出策略语义等价率仅 45.8%(非推理模型)/93.7%(推理模型),**必须 verifier-guided 闭环**。Gaia 复用 `/ai/generate` + `cedar_engine.build_cedar_schema` + `is_authorized_partial` 干跑预览。
- **(四)** Palantir 自己也是从 Ontology Roles 演进到 Project-based 的(有官方迁移工具),且**迁移后不可回退**。Gaia 用 expand/contract 零停机迁移:`project_id` 已 nullable(无需加列)→ backfill → 切权限查询逻辑 → (可选)NOT NULL。最大陷阱是权限语义变化(用户可能失去/获得权限)需**迁移前影响分析**。

---

# (一) Better Auth Server Docker 部署 + RS256/JWKS

## 1.1 现状基线(代码已就绪的部分)

| 组件 | 现状 | 出处 |
|------|------|------|
| `auth-server/auth.ts` | Hono + Better Auth,含 emailAndPassword + admin + organization + jwt + sso 插件,JIT databaseHooks(`user.create.after` 调 Gaia `/identity/users`),sso provisionUser(只打 log) | `auth-server/auth.ts` |
| docker-compose `better-auth` 服务 | 已定义,端口 3000,共享 PG(`better_auth` schema),`BETTER_AUTH_SECRET` 默认 `change-me-in-production`,`TRUSTED_ORIGINS` 含 5173/5174 | `docker-compose.yml:365-391` |
| `PrincipalService` | 双模式:dev(`X-User-Id`+DB groups)/ JWT(`fastapi-betterauth.BetterAuth` 验签 + DB group enrichment),`model_copy(update={"groups":...})` | `services/principal_service.py:155-243` |
| `fastapi-betterauth` | 已接入,`BetterAuth(base_url, audience, issuer, auto_error=False)`,默认 `algorithms=("EdDSA",)`,`lifespan=300`(JWKS 缓存 5min) | `principal_service.py:215-225` + fastapi-betterauth 源码 |
| JWT 签名 | Better Auth `jwt()` 插件默认 EdDSA/Ed25519(非对称),公钥暴露 `/api/auth/jwks` | `auth.ts` + Better Auth JWT 文档 |
| `definePayload` | 已配置 `sub/email/roles`,过期 1h | `auth.ts:jwt.jwt.definePayload` |

## 1.2 落地指导原则

### 原则 1:认证-授权分离,JWT 为唯一解耦边界

> Better Auth 管认证(你是谁),Gaia Cedar 管授权(你能做什么),通过 JWT 的 `sub` claim 解耦。**JIT databaseHooks 桥接两边**。

**实践**(已在 `auth.ts` 落地,需验证闭环):
- Better Auth 注册 → `databaseHooks.user.create.after` → `POST /identity/users`(带 `X-Provision-Token`)→ Gaia user 创建(subject = Better Auth uid)
- Gaia user 初始无 groups(无权限),admin 加组后获权
- **JWT 不带 groups**——验签后必须用 `sub` 查 Gaia `users` 表加载 group memberships(设计原则 6,`principal_service.py:178-198` 已实现)

### 原则 2:非对称签名(EdDSA/RS256),禁用 HS256 共享密钥验签

> RFC 8725 §3.1(Algorithm Verification):验证方**必须**显式白名单算法,不得信任 JWT header 的 `alg` 字段。这是防御 algorithm confusion 攻击的根本措施。

**现状**:`fastapi-betterauth` 默认 `algorithms=("EdDSA",)`,Better Auth `jwt()` 默认 EdDSA/Ed25519。**已对齐**。

**避坑——algorithm confusion 攻击(2026 Q1 CVE 簇发)**:
- CVE-2026-22817 / CVE-2026-27804 / CVE-2026-23552:根因都是"信任 attacker-controlled `alg` header 选验证算法"
- 攻击路径:RS256 JWT 改 `alg: HS256` + 用 RSA 公钥当 HMAC secret → 服务端用公钥当 HMAC 密钥验证 → 伪造通过
- **防御**:`fastapi-betterauth` 的 `jwt.decode(..., algorithms=self.algorithms)` 已硬编码 `("EdDSA",)`,不接受 token 声明的 alg。**保持不变,不要为了"兼容"加 HS256**。
- 若未来需要 RS256(Better Auth 支持 `keyPairConfig.alg: "RSA256"`),`BetterAuth(algorithms=("RS256",))` 同步切换,**单算法白名单**最安全(参考 pentesterlab 评审:只支持一种算法可彻底消除 alg-confusion)。

**参考**:[RFC 8725](https://www.rfc-editor.org/rfc/rfc8725.html) §3.1-3.4 / [PortSwigger alg-confusion](https://portswigger.net/web-security/jwt/algorithm-confusion) / [WorkOS alg-confusion](https://workos.com/blog/jwt-algorithm-confusion-attacks) / [IAMDevBox CVE-2026 分析](https://www.iamdevbox.com/posts/jwt-algorithm-confusion-attack-cve-2026-developer-guide/)

### 原则 3:JWKS 缓存 + key rotation overlap window

> Better Auth JWT 文档:"Since this key is not subject to frequent changes, it can be cached indefinitely. The key ID (`kid`) that was used to sign a JWT is included in the header. In case a JWT with a different `kid` is received, it is recommended to fetch the JWKS again."

**现状**:`fastapi-betterauth` 用 `PyJWKClient(cache_jwk_set=True, lifespan=300)`——JWKS 整体缓存 5min,未知 `kid` 触发重拉(PyJWKClient 内置行为)。

**落地——启用 Better Auth key rotation**(`auth.ts` 当前未配置 `rotationInterval`,默认禁用):
```ts
jwt({
  jwks: {
    keyPairConfig: { alg: "EdDSA", crv: "Ed25519" },
    rotationInterval: 60 * 60 * 24 * 90,  // 90 天轮换
    gracePeriod: 60 * 60 * 24 * 30,       // 旧 key 保留 30 天(grace period)
  },
  ...
})
```

**避坑——key rotation 破产 4 分钟窗口**(真实事故):
- 根因:新 key 签发 token,但验证方缓存还是旧 JWKS → 全量 401
- **铁律**:`gracePeriod` 必须 **> 验证方 JWKS 缓存 TTL + token 最大生命周期**。Gaia:PyJWKClient `lifespan=300`(5min)+ JWT `expirationTime=1h` → `gracePeriod` 至少 1h+buffer,推荐 30 天(覆盖 token 生命周期 + 缓存 + 运维缓冲)
- **rollout 顺序**(零停机轮换):① 生成新 keypair → ② 新 key 加入 JWKS(此时仍用旧 key 签)→ ③ 等 `gracePeriod`(验证方缓存刷新)→ ④ 切换 signer 用新 key → ⑤ 旧 key 在 `gracePeriod` 内仍可验签 → ⑥ `gracePeriod` 后移除旧 key
- Better Auth `rotationInterval` + `gracePeriod` 自动完成 ①-⑥,**但要确保 `BETTER_AUTH_SECRET` 固定**(见原则 4)

**参考**:[How to Rotate JWT Signing Keys with JWKS Without Downtime](https://how2.sh/posts/how-to-rotate-jwt-signing-keys-with-jwks-without-downtime/) / [We rotated our JWKS without overlap 事故复盘](https://dev.to/bluehills/we-rotated-our-jwks-without-overlap-here-is-the-4-minute-window-that-broke-prod-11i3) / [Operational Runbook for JWKS Key Rotation](https://mustafaerbay.com.tr/en/blog/tutorials/jwks-anahtar-rotasyonu-icin-operasyonel-runbook/)

### 原则 4:`BETTER_AUTH_SECRET` 固定化 + 私钥加密

> 避坑速查 #9:容器重启后 `BETTER_AUTH_SECRET` 变化 → JWKS 私钥解密失败(AES256-GCM 解密 key 变了)。

**现状**:`docker-compose.yml` 默认 `BETTER_AUTH_SECRET: ${BETTER_AUTH_SECRET:-change-me-in-production}`——开发够用,生产必须改。

**落地**:
- `.env`(不入 Git)生成固定 secret:`openssl rand -base64 32`,通过环境变量注入容器
- `BETTER_AUTH_SECRET` 用于:① 会话 cookie 签名 ② JWKS 私钥加密(Better Auth 默认 `disablePrivateKeyEncryption: false`,AES256-GCM)——**secret 变了 = 历史 JWKS 私钥不可解 = 旧 token 全部验证失败**
- Better Auth JWT 文档明确:"For security reasons, it's recommended to keep the private key encrypted"——**不要** `disablePrivateKeyEncryption: true`
- 私钥存在 `better_auth.jwks` 表(publicKey + encryptedPrivateKey),`BETTER_AUTH_SECRET` 是解密钥匙

**参考**:[Better Auth Installation](https://www.better-auth.com/docs/installation) / [Better Auth JWT §Disable private key encryption](https://better-auth.com/docs/plugins/jwt)

### 原则 5:iss/aud 强校验(防 cross-service replay)

> RFC 8725 §3.8-3.9:验证方 MUST 校验 iss(issuer)和 aud(audience)。OWASP ASVS V9.1.3:验证密钥属于声明 issuer。Vulnetix VNX-JWT-006:无 aud/iss 校验 = token cross-service replay(CWE-287)。

**现状**:`principal_service.py:217-223` `BetterAuth(base_url=..., audience=issuer_or_none, issuer=issuer_or_none)`,默认 iss/aud = Better Auth `baseURL`。`fastapi-betterauth` 源码 `jwt.decode(token, ..., issuer=self.issuer, audience=self.audience)`——**已校验**。

**落地确认**:
- `settings.better_auth_jwt_issuer` / `better_auth_jwt_audience` 未显式配置时 fallback 到 `better_auth_url`(= Better Auth baseURL),与 Better Auth 签发时的 iss/aud 一致
- **不要**为了"多服务共享"把 aud 设成通配——每个资源服务器应有独立 aud(虽 Gaia 当前单后端,但保留意识)

**参考**:[RFC 8725 §3.8-3.9](https://www.rfc-editor.org/rfc/rfc8725.html) / [OWASP ASVS V9](https://github.com/OWASP/ASVS/blob/master/5.0/en/0x18-V9-Self-contained-Tokens.md) / [WorkOS JWT best practices](https://workos.com/blog/jwt-best-practices)

## 1.3 关键避坑:databaseHooks 事务陷阱(P0,必须验证)

> **这是 Better Auth 已知的最危险陷阱**。Gaia `auth.ts` 的 `databaseHooks.user.create.after` 调用 Gaia API,如果时机不对会导致 FK 约束违反或死锁。

**Better Auth Issue #7260**:`databaseHooks.user.create.after` 在 social login + db transaction 开启时,在事务提交**前**执行 → 外部 Prisma client 看不到未提交行 → FK 约束违反。

**PR #7345 修复**:将 after hook 延迟到事务提交**后**执行(`runWithTransaction` queue after hooks)。

**PR #10231 新增 `afterTransaction` hook**:在 `runWithTransaction` callback 内 await,失败则回滚——用于"app-owned database writes 必须与 model creation 原子提交"场景。

**Gaia 现状分析**(`auth.ts:databaseHooks.user.create.after`):
- 当前调 `fetch(${gaiaApiUrl}/identity/users)`——**跨服务 HTTP 调用**,不是同 DB 事务
- 按 Better Auth 官方 stripe customer 示例,`after`(提交后)适合调外部 API ✓
- **但需确认 Better Auth 版本**:PR #7345 合入后 `after` 才在提交后执行。若 `package.json` 锁定旧版本,`after` 可能在事务内 → 调外部 HTTP 会导致死锁/超时

**落地检查清单**:
1. `auth-server/package.json` 的 `better-auth` 版本 ≥ PR #7345 合入版本(检查 changelog)
2. JIT 调用是**非阻塞 best-effort**(`auth.ts` 已 try/catch + console.error,不抛出)✓
3. JIT 失败不阻塞 Better Auth 注册(`auth.ts` 已实现)✓
4. **幂等性**:`POST /identity/users` 返回 409 时 `auth.ts` 视为"已存在"跳过 ✓(重复注册场景)
5. **GAIA_PROVISION_TOKEN 未设置时静默跳过**(`auth.ts` 已实现,dev 模式 Gaia 可能没起)✓

**参考**:[Better Auth #7260](https://github.com/better-auth/better-auth/issues/7260) / [PR #7345](https://github.com/better-auth/better-auth/pull/7345) / [PR #10231](https://github.com/better-auth/better-auth/pull/10231) / [Issue #10202](https://github.com/better-auth/better-auth/issues/10202) / [Better Auth Database Hooks](https://www.better-auth.com/docs/concepts/database)

## 1.4 关键避坑:TRUSTED_ORIGINS / CORS(P0,常见 403)

> 避坑速查 #7-8:`/api/auth/token` 404(vite proxy 没代理)+ 登录 403 Invalid origin(TRUSTED_ORIGINS 缺前端端口)。

**Better Auth 安全模型**:
- `trustedOrigins` 是**主 CSRF 保护机制**,验证每个请求的 `Origin` header
- 默认信任 `baseURL`,额外 origin 需显式配置
- Docker 化部署常见问题:`baseURL` 设成 `http://localhost:3000`,但前端从 `http://localhost:5173` 访问 → CORS 失败(Issue #3874)
- **社交登录 redirect 异常**(Issue #796):docker compose 内 `baseURL` 推断成 `http://<container_id>:3000` → redirect 到容器内部地址

**落地**:
- `auth.ts` 已用 `trustedOrigins: [...]` 显式列表 + `TRUSTED_ORIGINS` env 扩展 ✓
- 生产部署:**每个**前端 origin(含端口、协议、域名)都要加入 `TRUSTED_ORIGINS`
- **推荐用 `baseURL.allowedHosts`**(Better Auth dynamic base URL,支持通配):
  ```ts
  baseURL: {
    allowedHosts: ["localhost:5173", "localhost:5174", "gaia.example.com", "*.preview.gaia.example.com"],
    protocol: process.env.NODE_ENV === "production" ? "https" : "http",
  }
  ```
  这比静态 `baseURL` 更适合多环境(preview 部署/分支环境)
- 反向代理(nginx)场景:`advanced.trustedProxyHeaders` + `x-forwarded-host`/`x-forwarded-proto`(PR #7835),**否则 Better Auth 推断出容器内部地址**

**参考**:[Better Auth Security](https://better-auth.com/docs/reference/security) / [Better Auth Dynamic Base URL](https://github.com/better-auth/better-auth/blob/main/docs/content/docs/guides/dynamic-base-url.mdx) / [Issue #3874 CORS](https://github.com/better-auth/better-auth/issues/3874) / [Issue #796 redirect](https://github.com/better-auth/better-auth/issues/796) / [PR #7835 trustedProxyHeaders](https://github.com/better-auth/better-auth/pull/7835)

## 1.5 落地步骤(Phase 5 收尾)

### 步骤 1:容器重建 + 配置固化
```bash
# 1. 生成固定 secret(生产)
openssl rand -base64 32  # 写入 .env: BETTER_AUTH_SECRET=<输出>

# 2. auth.ts 加 key rotation(原则 3)
# 3. 重建镜像(databaseHooks 代码变更后必须)
docker compose build better-auth
docker compose up -d better-auth

# 4. 首次迁移(创建 9 张 better_auth 表)
docker compose exec better-auth npx @better-auth/cli migrate
```

### 步骤 2:端到端验签验证
```bash
# 1. 注册用户(Better Auth)
curl -X POST http://localhost:3000/api/auth/sign-up/email \
  -H "Content-Type: application/json" \
  -d '{"email":"test@gaia.dev","password":"Test1234!","name":"Test"}'

# 2. 登录拿 session
# 3. 换 JWT(/token 端点或 set-auth-jwt header)
curl http://localhost:3000/api/auth/token -H "Authorization: Bearer <session>"

# 4. 用 JWT 调 Gaia API(验证 JWKS 验签 + DB group 加载)
curl http://localhost:8000/health -H "Authorization: Bearer <jwt>"

# 5. 验证 JWKS 端点
curl http://localhost:3000/api/auth/jwks  # 应返回 {"keys":[{"crv":"Ed25519",...,"kid":"..."}]}
```

### 步骤 3:JIT 流程验证
```bash
# 1. 注册新用户 → 检查 Gaia 身份管理页是否自动出现(设置 GAIA_PROVISION_TOKEN)
# 2. 加组 → 登录验证权限
# 3. 更新 verify_permission_live.py 加 JIT 场景
```

### 步骤 4:key rotation 演练
```bash
# 1. 配置 rotationInterval=30s(临时测试)+ gracePeriod=3600
# 2. 触发轮换 → 验证旧 token 在 gracePeriod 内仍可验签
# 3. 验证新 token 用新 kid,PyJWKClient 自动重拉 JWKS
# 4. 恢复生产值(90 天轮换 + 30 天 grace)
```

## 1.6 测试矩阵

| 场景 | 预期 | 验证点 |
|------|------|--------|
| 正确 JWT | 200 + principal 加载 | JWKS 验签 + DB group enrichment |
| 过期 JWT | 401 | `exp` 校验 |
| 篡改 alg=HS256 | 401 | algorithm 白名单(EdDSA only) |
| 篡改 aud | 401 | audience 校验 |
| 未知 kid | 自动重拉 JWKS + 验签成功 | PyJWKClient `kid` miss 重拉 |
| key rotation | 旧 token gracePeriod 内有效 | overlap window |
| JIT 注册 | Gaia user 自动创建 | databaseHooks.after |
| JIT 失败 | Better Auth 注册仍成功 | 非阻塞 best-effort |
| 无 token | anonymous principal | `auto_error=False` |
| BETTER_AUTH_SECRET 变更 | 旧 token 全失效 | 私钥解密失败(预期) |

---

# (二) LLM 辅助 Cedar 策略生成

## 2.1 问题本质:LLM 生成策略的安全悖论

> **AutoCedar 论文核心论点**(arXiv:2607.03656):"A model can produce a syntactically valid, plausible-looking policy that is semantically wrong, with no internal signal that anything is off."

**关键数据**(Vatsa et al.):
- 非推理模型:策略语义等价率 **45.8%**(过半数生成策略语义错误)
- 推理模型:语义等价率 **93.7%**(仍有 6.3% 错误)
- **结论**:LLM 能写出"看起来对"的 Cedar 语法,但无法保证语义正确性。**语法正确 ≠ 安全正确**。

**OWASP Top 10 2025**:Broken Access Control 排第一,出现在**每个**被测应用中。权限配置是安全失败的最常见源头。

**为什么 access control 是 LLM 最危险的领域**(AutoCedar §I):"a memory bug carries its own ground truth(sanitizer 崩溃),while 'user A can read user B's record' is a defect only relative to a policy the code never states"——权限错误的"正确性"是相对于未言明的意图,LLM 自己无法判断。

## 2.2 落地指导原则

### 原则 1:Verifier-guided,LLM 只提议不决策

> AutoCedar 范式:**LLM 提议候选策略,verifier 拥有 target/failure history/accept-reject 决策**。LLM 永远不直接执行,只作为策略草案来源。

**Gaia 落地架构**(复用现有组件):
```
用户说 NL("销售只能看本区域客户")
  → /ai/generate(现有 LLM 原语,AI_MODEL)
  → LLM 生成 Cedar 表达式草案
  → cedarpy validate_policies(schema, [draft])  ← 闸门 1:语法+类型校验
  → 失败 → 反馈错误,LLM 修正(可多轮)
  → 通过 → is_authorized_partial 干跑预览  ← 闸门 2:语义预览
  → 用户确认"此策略下销售 A 能看到哪些客户"
  → 落库 RowSecurityPolicy.expression(generated_by: "llm")
  → 审计日志记录 prompt + 输出 + reviewer
```

**关键**:Gaia 已有 `cedar_engine.build_cedar_schema()` + `evaluate_row_policy_partial()`(Phase 3 实现),**无需新建 verifier**——复用 Cedar TPE 做干跑预览。

### 原则 2:Schema 注入约束 LLM 输出边界

> Cedar Policy Validation 文档:"To validate a policy, Cedar needs information about the application. It needs to know the correct names of entity types, the attributes they possess."

**AutoCedar 的 schema atom 机制**:先从需求提取 schema atoms(实体/属性/动作),review 后编译成 Cedar schema,**schema 固定后 LLM 只能在 schema 范围内生成策略**。

**Gaia 落地**:
- `cedar_engine.build_cedar_schema()` 已从 ObjectType properties + principal attributes 构建 Cedar schema
- LLM prompt 注入:**当前 ObjectType 的 Cedar schema**(entity types + attributes + actions)+ principal attributes schema(region/department/level)
- 这复用 TextQL(ADR-012)`schema_injector` 的"确定性注入,非 LLM 推断"思路——schema 是事实,不是 LLM 要猜的东西
- **约束效果**:LLM 不会生成引用不存在属性的策略(如把 `region` 写成 `area`),因为 schema 里没有 `area`

### 原则 3:Floor/Ceiling/Liveness 三层语义边界

> AutoCedar 的核心创新——不是让 LLM 生成"一个策略",而是让 verifier 检查策略是否落在**审批过的语义边界**内:
> - **Floor(下界)**:必须允许的请求(销售 A 能看本区域客户)
> - **Ceiling(上界)**:必须拒绝的请求(销售 A 不能看其他区域客户)
> - **Liveness(活跃性)**:不能被清空的请求类(不能 deny-all)

**Gaia 务实子集**(不全照搬 AutoCedar 的 CVC5 SMT):
- **Floor 预览**:用 `is_authorized_partial` 对样本 principal(销售 A)+ 样本 resource(本区域客户)求值 → 展示"此策略下能看到什么"
- **Ceiling 预览**:对样本 principal(销售 A)+ 样本 resource(其他区域客户)求值 → 展示"此策略下看不到什么"
- **Liveness 检查**:策略不能 `forbid`(全拒),至少有一个 `permit` 路径
- **用户确认**:UI 展示 floor/ceiling 预览结果,用户确认语义后落库(HITL,对齐 ADR-010)

### 原则 4:HITL 审批,LLM 生成物标记 provenance

> AutoCedar §III-C:"Reviewers are not asked to certify an unchecked generated policy. They approve the target that the verifier will later enforce."——人审批的是**边界/预览结果**,不是 Cedar 语法。

**Gaia 落地**:
- `RowSecurityPolicyModel` 加 `generated_by: str`(null=人工,"llm"=LLM 生成)+ `generation_meta: JSONB`(prompt/模型/时间/reviewer)
- LLM 生成策略**必须经 HITL 审批**(对齐 ADR-010 工具层审批切面),不允许"生成即生效"
- 审计日志记录完整链路:NL 输入 → LLM 草案 → validate 结果 → floor/ceiling 预览 → 审批人 → 落库
- **provenance 可追溯**:事后发现策略错误,可定位是 LLM 生成还是人工编写

### 原则 5:复用 `/ai/generate`,不新建端点

> ADR-017 D6:"复用 Gaia `/ai/generate` + cedarpy `validate_policies` + VS Code Cedar 扩展工具链。"

**Gaia 现状**:`/ai/generate`(非流式)+ `/ai/stream`(SSE 流式)+ `/ai/scaffold`(结构化流式)已成熟。LLM 策略生成是新的 prompt + 后处理,**不新建 `/ai/policy-generate` 端点**。

**实现模式**(对齐 TextQL 范式):
- 新增 `services/ai_policy_generate.py`(类似 `ai_generate.py`),调 `Agent(model, system_prompt=...)` + `result_type=CedarPolicyDraft`(pydantic 结构化输出)
- prompt = Cedar schema 注入 + few-shot 示例 + NL 需求
- 后处理 = `cedarpy.validate_policies` + `is_authorized_partial` 干跑
- 暴露为 `/ai/generate-policy`(或复用 `/ai/generate` 加 `mode=policy` 参数)

## 2.3 cedarpy API 实践(基于源码 + 测试)

### 2.3.1 validate_policies(闸门 1:语法+类型)

```python
import cedarpy

# Gaia 已有 build_cedar_schema() 生成 schema(dict)
schema = build_cedar_schema(principal_attrs=["region","department"], 
                            resource_attrs=["region","department","level"])

# LLM 生成的草案
draft_policy = '''
permit(
  principal is User,
  action == Action::"view",
  resource is ObjectType
) when {
  principal.attributes["region"] == resource.attributes["region"]
};
'''

# validate:语法 + 类型 + schema 一致性
result = cedarpy.validate_policies(
    policies=cedarpy.PolicySet.from_str(draft_policy),
    schema=schema,
)
# result.errors: 类型错误/未声明实体/属性缺失等
# 通过后才能进入干跑
```

**参考**:[cedarpy v4.8.6](https://pypi.org/project/cedarpy/) / [Cedar Policy Validation](https://docs.cedarpolicy.com/policies/validation.html) / [Cedar Schema](https://docs.cedarpolicy.com/schema/schema.html)

### 2.3.2 is_authorized_partial(闸门 2:语义预览)

Gaia `cedar_engine.evaluate_row_policy_partial()` 已实现(Phase 3):
```python
from ontology.services.cedar_engine import evaluate_row_policy_partial

# principal 已知(销售 A),resource 未知 → TPE 产生 residual
residual = evaluate_row_policy_partial(
    policy_expression=draft_policy,
    principal_entity=build_principal_entity(sales_a, string_uid=True),
    resource_attrs=["region", "department"],  # ObjectType 属性
)
# residual.decision: "Allow"/"Deny"
# residual.residual_ast: 剩余条件(只剩 resource 属性)
# residual.unknown_entities: Cedar 还需要的实体

# Floor 预览:销售 A + 本区域客户 → 应 Allow
# Ceiling 预览:销售 A + 其他区域客户 → 应 Deny
```

**cedarpy 测试用例参考**([test_authorize_partial.py](https://github.com/k9securityio/cedar-py/blob/main/tests/unit/test_authorize_partial.py)):
- `test_unknown_principal_produces_residuals`:principal 未知时产生 residual
- Gaia 用法相反:principal 已知 + resource 未知 → residual 描述 resource 条件

### 2.3.3 is_authorized(全量求值,干跑预览)

```python
import cedarpy
from cedarpy import is_authorized, Decision

# 完整求值(principal + resource 都已知)→ 干跑预览
result = is_authorized(
    request={
        "principal": 'User::"sales_a"',
        "action": 'Action::"view"',
        "resource": 'ObjectType::"customer_123"',
        "context": {},
    },
    policies=cedarpy.PolicySet.from_str(draft_policy),
    entities=[
        {"uid": {"__entity": {"type": "User", "id": "sales_a"}},
         "attrs": {"region": "east"}, "parents": []},
        {"uid": {"__entity": {"type": "ObjectType", "id": "customer_123"}},
         "attrs": {"region": "east"}, "parents": []},
    ],
)
# result.decision == Decision.Allow → 销售 A 能看本区域客户 ✓ (Floor)
# 换 resource region="west" → Decision.Deny → 不能看其他区域 ✓ (Ceiling)
```

## 2.4 AutoCedar 借鉴 vs Gaia 务实子集

| AutoCedar 特性 | Gaia 是否采用 | 理由 |
|---------------|:---:|------|
| Verifier-guided 闭环(LLM 提议 + verifier 判定) | ✅ | 核心范式,用 cedarpy validate + partial eval |
| Schema atom 人工 review | 🟡 简化 | Gaia schema 来自 ObjectType(已有),不需从 NL 提取 schema |
| Floor/Ceiling/Liveness 边界 | 🟡 简化 | 用 partial eval 干跑预览替代 CVC5 SMT 全量验证 |
| CEGIS 迭代修复(verifier 失败 → repair packet) | ✅ 轻量 | validate 失败 → 错误反馈 LLM 修正(限 N 轮) |
| Signal layer(方向:tighten/loosen/expand) | 🔴 不做 | Gaia 策略简单(单 ObjectType 行级),不需 SMT 级方向分析 |
| CVC5 SMT solver | 🔴 不做 | 重依赖,Gaia 用 cedarpy 内置验证够用 |
| cedar symcc 符号比较 | 🔴 不做 | 同上 |
| HITL 审批 | ✅ | 对齐 ADR-010,LLM 策略必须人工确认 |
| Provenance 追踪 | ✅ | generated_by + generation_meta |

**关键差异**:AutoCedar 解决"从零生成完整 policy store"的难题(221 task benchmark),Gaia 场景更窄——**用户已知道想表达什么**(销售看本区域),LLM 只是把 NL 翻译成 Cedar 语法,verifier 负责保证翻译正确。因此 Gaia 不需要 AutoCedar 的完整 CEGIS + SMT,但**必须保留 verifier-guided 闭环**(validate + 干跑预览 + HITL)。

## 2.5 AWS NL2Cedar 参考实践

> AWS Bedrock AgentCore 的 NL2Cedar 是业界唯一生产级 NL→Cedar 服务(2026)。

**关键实践**(AWS 官方):
1. **schema 驱动生成**:"The service uses the AgentCore Gateway schema to generate valid Cedar policies"——**先注入 schema,再生成**,与 AutoCedar/Gaia 一致
2. **生成仍需 review**:"generated policies still need review"——AWS 明确不信任 LLM 直出
3. **多语句生成**:支持多行 NL 生成多个策略
4. **运行时 PDP**:生成策略存 policy engine,AgentCore Gateway 拦截每个 tool call 求值

**Gaia 借鉴**:NL2Cedar 的 schema 驱动 + review-required 模式与 Gaia 原则 1/2/4 完全一致。Gaia 差异化在于**复用自建 Cedar 集成层**(cedar_engine.py)而非依赖 AWS 托管服务,保持开源/私有部署能力。

**参考**:[Writing policies in natural language](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-natural-language.html) / [NL2Cedar Demo Notebook](https://github.com/awslabs/agentcore-samples/blob/main/06-workshops/08-AgentCore-policy/02-Natural-Language-Policy-Authoring/NL-Authoring-Policy.ipynb) / [Secure AI agents with AgentCore Policy](https://aws.amazon.com/blogs/machine-learning/secure-ai-agents-with-policy-in-amazon-bedrock-agentcore/) / [AWS Security Blog: least-privilege Cedar in multi-agent chains](https://aws.amazon.com/blogs/security/enforce-least-privilege-authorization-in-multi-agent-ai-chains-using-cedar/)

## 2.6 落地步骤

### 步骤 1:数据模型扩展(Alembic migration)
```python
# RowSecurityPolicyModel 加字段
generated_by: Mapped[str] = mapped_column(String(20), default="manual")  # manual | llm
generation_meta: Mapped[dict] = mapped_column(JSONB, default=dict)  # {prompt, model, ts, reviewer}
```

### 步骤 2:策略生成 Service
```python
# services/ai_policy_generate.py
class CedarPolicyDraft(BaseModel):
    expression: str  # Cedar 条件表达式
    explanation: str  # LLM 解释
    confidence: float  # 0-1

async def generate_policy_draft(
    nl_requirement: str,
    object_type_id: str,
    principal_attrs: dict,
) -> CedarPolicyDraft:
    # 1. 加载 ObjectType → build_cedar_schema()
    # 2. prompt = schema + few-shot + nl_requirement
    # 3. /ai/generate → LLM 输出结构化草案
    # 4. cedarpy.validate_policies 闸门
    # 5. 失败 → 反馈错误,重试(限 3 轮)
    # 6. is_authorized_partial 干跑预览数据
    return draft
```

### 步骤 3:HITL 审批 UI
- 策略编辑器加"AI 辅助生成"按钮
- 生成后展示:Cedar 草案 + floor/ceiling 预览(样本 principal × 样本 resource 矩阵)
- 用户确认 → `POST /object-types/{id}/row-security-policy`(带 `generated_by: "llm"`)
- 落库前再过一次 `validate_policies`(防 UI 篡改)

## 2.7 测试矩阵

| 场景 | 预期 | 验证点 |
|------|------|--------|
| NL → 正确 Cedar | 草案通过 validate | schema 注入约束 |
| NL 引用不存在属性 | validate 失败 + 反馈 | schema 闸门 |
| 草案语义过宽(ceiling 违反) | 干跑预览暴露 | partial eval |
| 草案语义过窄(floor 违反) | 干跑预览暴露 | partial eval |
| 草案 forbid-all(liveness 违反) | 检测并拒绝 | liveness check |
| LLM 幻觉生成非法语法 | validate 失败 | 语法闸门 |
| 多轮修复收敛 | 限 N 轮后成功/失败 | CEGIS 轻量版 |
| HITL 拒绝 | 策略不落库 | 审批切面 |
| provenance 记录 | generation_meta 完整 | 审计 |

---

# (四) 选项 B→A 迁移(资源归属模型演进)

## 4.1 Palantir 实践参照(权威范式)

> Palantir Foundry 自己也是从 Ontology Roles 演进到 Project-based Permissions 的,有[官方迁移工具](https://palantir.com/docs/foundry/ontology-manager/migrate-to-project-based-permissions/)。这是 Gaia 选项 B→A 迁移的直接参照。

### 4.1.1 Palantir 的三层权限演进

| 阶段 | 模型 | 说明 |
|------|------|------|
| 1. Datasource-derived | 对象权限 = backing dataset 权限 | 1:1 依赖,最早期 |
| 2. Ontology Roles(legacy) | 直接给 ObjectType/LinkType/ActionType 打角色 | 替代 datasource-derived,但粒度粗 |
| 3. **Project-based(当前默认)** | Ontology 资源存入 Project,继承 Project 角色 | 经 Compass 文件系统管理,**新 Ontology 默认此模式** |

**关键事实**(Palantir 官方):
- "All new ontologies will default to project-based permissions"(2025 更新)
- "Once a resource has been migrated to project-based permissions, **it cannot be reverted** to ontology roles or datasource-derived permissions"——**迁移不可逆**
- "Migrating to projects does not change who has access to the backing datasource. To see objects, users continue to need permissions on both the object type and the backing datasource"——**对象实例权限跟 backing dataset,不跟定义的 Project**

### 4.1.2 Palantir 迁移的关键设计决策

**决策 1:Ontology 资源 vs 对象实例权限分离**
- Ontology 资源(ObjectType/ActionType/LinkType/Interface/SharedProperty)→ Project-based(定义权限)
- Object/Link 实例权限 → 跟 backing datasource location(数据权限)
- **Gaia 对齐**:ADR-016 D3 "实例权限跟 backing dataset"(已实现)

**决策 2:Project 是原子权限单位**
- "Projects are the atomic units of permissioning in Foundry. People who have access to any resource in a Project should have access to all resources. If you find yourself trying to block off areas of the Project, you should consider splitting it into multiple Projects instead."
- **Gaia 对齐**:ADR-016 D1 Project 是协作权限边界

**决策 3:Ontology owner 手动启用 + 渐进迁移**
- "For existing ontologies, an ontology owner must enable the capability manually, and existing ontology resources require migration"
- 不是一次性全切,可逐资源迁移
- **Gaia 对齐**:选项 B 预留 `project_id`(nullable),可逐 ObjectType 填充

**参考**:[Migrate to project-based permissions](https://palantir.com/docs/foundry/ontology-manager/migrate-to-project-based-permissions/) / [Ontology permissions](https://palantir.com/docs/foundry/object-permissioning/ontology-permissions/) / [Ontology roles migration [Legacy]](https://palantir.com/docs/foundry/ontology-manager/ontology-roles-migration/) / [Projects and roles](https://palantir.com/docs/foundry/security/projects-and-roles/) / [Ontology and Pipeline Design Principles](https://community.palantir.com/t/ontology-and-pipeline-design-principles/5481/1)

## 4.2 落地指导原则

### 原则 1:Expand/Contract 零停机迁移

> 选项 B→A 不是一次 ALTER,而是**多阶段 campaign**。核心是 expand/contract 模式:每个阶段独立可部署、可回滚。

**Gaia 现状优势**:`project_id` 已 nullable(Phase 0 已预留),**无需加列**(expand 阶段已完成)。

**迁移五阶段**:

| 阶段 | 操作 | 回滚 | 风险 |
|------|------|------|------|
| **1. Expand(已完成)** | `project_id` nullable 列已存在 | N/A | 无 |
| **2. Backfill** | 逐 ObjectType 填充 `project_id`(默认 = Ontology 所在 Space 的 default Project) | `UPDATE ... SET project_id = NULL` | 低(只写不读) |
| **3. 切权限查询(双模式)** | AuthorizationService Layer 4:`project_id` 非空 → 查 Project 角色;空 → fallback Ontology(已实现) | 回滚 backfill(置 NULL) | 中(权限语义可能变) |
| **4. 验证 + 观察** | 影响分析对比 + 灰度 + 监控 | 回滚阶段 3 | 中 |
| **5. Contract(可选)** | `ALTER TABLE ... ALTER COLUMN project_id SET NOT NULL` + FK | 需重新 nullable | 高(不可逆) |

**关键**:阶段 3 的双模式 fallback **已在 `AuthorizationService` 实现**(`project_id` 优先 + Ontology fallback),调用方无感知。迁移时填充 `project_id` 后 fallback 自动跳过。

**参考**:[Zero-Downtime Schema Migrations: Expand-Contract](https://abstractalgorithms.dev/zero-downtime-schema-migrations-expand-contract) / [Prisma Expand and Contract](https://www.prisma.io/dataguide/types/relational/expand-and-contract-pattern) / [Zero-Downtime Postgres Migrations 2026](https://agentscamp.com/guides/database/zero-downtime-postgres-migrations)

### 原则 2:迁移前影响分析(权限语义变化)

> **最大风险**:迁移前 ObjectType 权限查 Ontology 所属 Project;迁移后查新 Project。如果新 Project 的角色授予不同,**用户权限会变化**(失去或获得)。

**Palantir 实践**:"Migrating to projects does not change who has access to the backing datasource"——Palantir 保证数据权限不变,但**定义权限会变**(从 Ontology Roles 切到 Project Roles)。

**Gaia 影响分析**(迁移前必须输出):
```
对每个待迁移 ObjectType:
  - 当前(选项 B):权限 = Ontology 所属 Space 的 default Project 角色
  - 迁移后(选项 A):权限 = 新 project_id 的 Project 角色
  - diff:哪些用户/组会失去权限?哪些会获得?
```

**实现**:
- `AuthorizationService` 加 `preview_migration_impact(object_type_id, target_project_id)` 方法
- 对比两个 Project 的 `RoleAssignment`,输出 user/group 级别的权限变化矩阵
- **迁移前必须人工 review 此矩阵**,确认无意外权限丢失

### 原则 3:渐进迁移,逐 ObjectType 切换

> Palantir:"existing ontology resources require migration"(逐资源),不是一次性全切。

**Gaia 落地**:
- 不需要一次性 `UPDATE ALL object_types SET project_id = ...`
- 提供 `POST /object-types/{id}/migrate-to-project` API(逐个迁移)
- 每个 ObjectType 迁移后**独立验证**(影响分析 + 灰度 + 用户反馈)
- **触发时机**:出现真实多团队需求时(某 ObjectType 需归到非 default Project),不是"为了迁移而迁移"

**反模式**(明确禁止):
- ❌ 一次性全量迁移(风险集中,回滚困难)
- ❌ 无影响分析直接迁移(可能意外剥夺用户权限)
- ❌ 迁移后立即 SET NOT NULL(不可逆,阻断回滚)

### 原则 4:实例权限跟 backing dataset(不变)

> ADR-016 D3 + Palantir:"Object and link instance permissions remain dependent on the backing datasource location."

**迁移边界**:
- 选项 B→A 只迁移**定义类资源**(ObjectType/ActionType/LinkType)的归属
- **对象实例权限(object_state)不动**——它跟 backing dataset(归 Project,Phase 0 已实现 `project_id`)
- 这与 Palantir "migrating to projects does not change backing datasource access" 一致

### 原则 5:缓存失效策略

> cashews 三级缓存(用户属性/资源属性/授权结果)。迁移改变 resource 归属 → 必须失效相关缓存。

**落地**:
- `AuthorizationService.invalidate_resource(resource_type, resource_id)` 已实现(Phase 1)
- 迁移某 ObjectType 后,调 `invalidate_resource("ObjectType", ot_id)` + `invalidate_principal` 对所有受影响 principal
- cashews `cache.delete_tags("resource:ObjectType:{id}")` 批量失效
- **避坑**:高敏操作(权限授予/角色变更/归属迁移)强制实时校验,不走缓存(ADR-016 避坑表)

## 4.3 现状代码对齐(已实现的平滑迁移保障)

| 保障 | 现状实现 | 出处 |
|------|---------|------|
| `project_id` nullable 预留 | 所有定义类资源表已加 `project_id: Mapped[str \| None]` | `core/models/permission.py` + 评估报告 §5.3 |
| 权限查询双模式 | `AuthorizationService` Layer 4:`project_id` 非空→查 Project;空→fallback Ontology 所属 default Project | 设计文档 §2.1 + `authorization_service.py` |
| 调用方无感知 | fallback 逻辑集中在 AuthorizationService,OntologyService/ObjectQueryService/ActionService 不变 | ADR-016 D3 |
| 缓存失效 API | `invalidate_principal` / `invalidate_resource` | `authorization_service.py:438-443` |

**结论**:Gaia 的平滑迁移保障**已在 Phase 0-1 实现**,二期只需:
1. Backfill 脚本(填充 `project_id`)
2. 影响分析 API(`preview_migration_impact`)
3. 迁移 API + UI(`migrate-to-project`)
4. (可选)Contract 阶段 NOT NULL 约束

## 4.4 落地步骤

### 步骤 1:影响分析 API
```python
# AuthorizationService
async def preview_migration_impact(
    self, object_type_id: str, target_project_id: str
) -> MigrationImpact:
    """对比当前(选项 B fallback)vs 迁移后(选项 A)的权限差异。"""
    current_project = await self._resolve_ontology_default_project(ot.ontology_id)
    current_assignments = await self._list_role_assignments(current_project)
    target_assignments = await self._list_role_assignments(target_project_id)
    # 输出:gain_groups(新获权)/ lose_groups(失权)/ unchanged
```

### 步骤 2:迁移 API
```python
# POST /object-types/{id}/migrate-to-project
async def migrate_to_project(ot_id: str, target_project_id: str, principal):
    # 1. 权限校验:principal 必须是两个 Project 的 OWNER
    # 2. 影响分析 → 返回给调用方确认
    # 3. 填充 project_id
    # 4. 缓存失效
    # 5. 审计日志
```

### 步骤 3:Backfill 脚本(默认迁移)
```python
# scripts/migrate_to_project_based.py
# 对每个 project_id=NULL 的 ObjectType:
#   target = ot.ontology.space.default_project
#   影响分析(应无变化,因为 default Project = 原 fallback 目标)
#   填充 project_id
# 这相当于"显式化选项 B 的隐式行为",权限语义不变
```

### 步骤 4:Contract(可选,高谨慎)
```python
# Alembic migration(仅在所有 project_id 都已填充后)
def upgrade():
    op.alter_column('object_types', 'project_id', nullable=False)
    # + FK constraint
# ⚠️ 不可逆,必须确认所有行已 backfill
```

## 4.5 测试矩阵

| 场景 | 预期 | 验证点 |
|------|------|--------|
| backfill default Project | 权限语义不变 | 影响分析 diff 为空 |
| 迁移到非 default Project | 权限按新 Project 角色 | 影响分析 diff 正确 |
| 迁移后 fallback 跳过 | project_id 非空→直接查 Project | AuthorizationService Layer 4 |
| 缓存失效 | 迁移后立即生效 | invalidate_resource |
| 回滚(置 NULL) | 恢复 fallback 行为 | 权限恢复 |
| 影响分析 expose 失权用户 | 提前预警 | preview_migration_impact |
| Contract NOT NULL(有 NULL 行) | 失败 | 约束保护 |

---

## 附录:关键参考文档索引

### (一) Better Auth + JWT/JWKS
- [Better Auth JWT Plugin 官方文档](https://better-auth.com/docs/plugins/jwt)(key rotation / gracePeriod / definePayload / 算法选项 / custom adapter)
- [fastapi-betterauth 源码](https://github.com/lukonik/fastapi-betterauth)(PyJWKClient 封装,EdDSA 默认,iss/aud 校验)
- [Better Auth + JWKS 完整指南](https://shahriyar.dev/blog/better-auth-jwks-jwt-verification-a-complete-guide)
- [RFC 8725 JWT Best Current Practices](https://www.rfc-editor.org/rfc/rfc8725.html)(algorithm verification / iss/aud / key binding)
- [JWT alg-confusion 攻击 CVE-2026 分析](https://www.iamdevbox.com/posts/jwt-algorithm-confusion-attack-cve-2026-developer-guide/)
- [JWKS key rotation 零停机](https://how2.sh/posts/how-to-rotate-jwt-signing-keys-with-jwks-without-downtime/)
- [JWKS rotation 事故复盘(4 分钟窗口)](https://dev.to/bluehills/we-rotated-our-jwks-without-overlap-here-is-the-4-minute-window-that-broke-prod-11i3)
- [JWKS rotation 运维 runbook](https://mustafaerbay.com.tr/en/blog/tutorials/jwks-anahtar-rotasyonu-icin-operasyonel-runbook/)
- [Better Auth databaseHooks 事务陷阱 #7260](https://github.com/better-auth/better-auth/issues/7260) / [PR #7345 修复](https://github.com/better-auth/better-auth/pull/7345) / [PR #10231 afterTransaction](https://github.com/better-auth/better-auth/pull/10231)
- [Better Auth Security(trustedOrigins/proxy)](https://better-auth.com/docs/reference/security)
- [Better Auth Dynamic Base URL](https://github.com/better-auth/better-auth/blob/main/docs/content/docs/guides/dynamic-base-url.mdx)
- [Better Auth Database 概念(hooks)](https://www.better-auth.com/docs/concepts/database)

### (二) LLM 辅助 Cedar 策略生成
- [AutoCedar 论文(arXiv:2607.03656)](https://arxiv.org/html/2607.03656v1)(verifier-guided / floor-ceiling-liveness / CEGIS / signal layer)
- [AutoCedar 开源实现](https://github.com/neselab/cedar-synthesis-engine)
- [cedarpy v4.8.6 API](https://pypi.org/project/cedarpy/)(is_authorized / is_authorized_partial / PolicySet)
- [cedarpy partial eval 测试](https://github.com/k9securityio/cedar-py/blob/main/tests/unit/test_authorize_partial.py)
- [Cedar Policy Validation](https://docs.cedarpolicy.com/policies/validation.html)
- [Cedar Schema](https://docs.cedarpolicy.com/schema/schema.html)
- [AWS AgentCore NL2Cedar](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-natural-language.html) / [Demo](https://github.com/awslabs/agentcore-samples/blob/main/06-workshops/08-AgentCore-policy/02-Natural-Language-Policy-Authoring/NL-Authoring-Policy.ipynb)
- [AWS: Secure AI agents with AgentCore Policy](https://aws.amazon.com/blogs/machine-learning/secure-ai-agents-with-policy-in-amazon-bedrock-agentcore/)
- [AWS Security: least-privilege Cedar in multi-agent chains](https://aws.amazon.com/blogs/security/enforce-least-privilege-authorization-in-multi-agent-ai-chains-using-cedar/)
- [Can AI Generate Authorization Policy Safely?(permit.io)](https://www.permit.io/blog/can-ai-generate-authorization-policy-safely)
- [Cedar OOPSLA 论文](https://assets.amazon.science/96/a8/1b427993481cbdf0ef2c8ca6db85/cedar-a-new-language-for-expressive-fast-safe-and-analyzable-authorization.pdf)

### (四) 选项 B→A 迁移
- [Palantir: Migrate to project-based permissions](https://palantir.com/docs/foundry/ontology-manager/migrate-to-project-based-permissions/)
- [Palantir: Ontology permissions](https://palantir.com/docs/foundry/object-permissioning/ontology-permissions/)
- [Palantir: Ontology roles migration [Legacy]](https://palantir.com/docs/foundry/ontology-manager/ontology-roles-migration/)
- [Palantir: Projects and roles](https://palantir.com/docs/foundry/security/projects-and-roles/)
- [Palantir: Ontology and Pipeline Design Principles](https://community.palantir.com/t/ontology-and-pipeline-design-principles/5481/1)
- [Expand-Contract 零停机迁移](https://abstractalgorithms.dev/zero-downtime-schema-migrations-expand-contract)
- [Prisma: Expand and Contract Pattern](https://www.prisma.io/dataguide/types/relational/expand-and-contract-pattern)
- [Zero-Downtime Postgres Migrations 2026](https://agentscamp.com/guides/database/zero-downtime-postgres-migrations)

### Gaia 内部现状(代码基线)
- `auth-server/auth.ts` — Better Auth 配置(JIT + sso + jwt)
- `services/principal_service.py` — 双模式 Principal 解析(JWKS 验签 + DB group enrichment)
- `services/cedar_engine.py` — Cedar 集成层(schema/partial eval/masking)
- `services/sql_injector.py` — Cedar residual → SQL 注入
- `services/authorization_service.py` — 五层校验 PDP + 缓存
- `core/models/permission.py` — RowSecurityPolicy/PropertyMaskingPolicy/RoleAssignment 模型
- `docs/engineer/permission-roadmap-and-principles.md` — 现状 + 10 设计原则 + 避坑速查
- `docs/design/permission-governance-design.md` — 详细设计(§0.5 B→A 迁移 / §1.5 行列级 / §2.1 Layer 4 fallback)
