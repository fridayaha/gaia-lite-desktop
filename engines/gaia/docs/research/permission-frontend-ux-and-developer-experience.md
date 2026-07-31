# 权限治理 —— 前端交互与开发者体验设计研究

> **用途**：本文研究权限治理特性的前端界面与交互设计——如何让用户（含开发者）最简单地用起来，达到「自然，本来就应该是这个样子」的体验，而非给用户增加负担。涵盖业界最佳实践、设计原则、Gaia 界面方案、开发者架构可理解性。
> **研究方法**：以 Palantir/Databricks/Snowflake/AWS 官方文档 + NN/g 可用性研究 + Chromium 权限 UX 研究 + HP 可用安全论文为来源，结合 Gaia 现有前端技术栈（React 19 + Tailwind + React Aria + Cytoscape）。
> **研究日期**：2026-07-08
> **关联**：[ADR-016](../architecture/adr-016-permission-governance.md) · [设计文档 §八](../design/permission-governance-design.md#八前端交互设计) · [评估报告 §八](../architecture/permission-governance-landing-assessment.md#八简化设计落地复杂留给自己简单留给用户) · [可用安全研究](./palantir-permission-review-and-industry-comparison.md#三复杂留给自己简单留给用户设计哲学)

---

## 目录

- [一、业界权限 UI 最佳实践](#一业界权限-ui-最佳实践)
- [二、"自然感"设计哲学](#二自然感设计哲学)
- [三、Gaia 前端界面设计](#三gaia-前端界面设计)
- [四、开发者体验与架构可理解性](#四开发者体验与架构可理解性)
- [五、设计原则汇总与反模式](#五设计原则汇总与反模式)

---

## 一、业界权限 UI 最佳实践

### 1.1 Palantir Foundry：资源详情面板的 Access tab（就近管理）

[Compass Use Project details panel](https://palantir.com/docs/foundry/compass/use-project-details-panel/)：

Palantir 不把权限管理孤立到一个单独的"权限管理页面"，而是**就近集成在资源详情面板**里——用户在浏览 Project/Dataset/Ontology 时，右侧详情面板有 Access tab，直接管理该资源的角色授予。

**关键设计**：
- 资源详情面板（右侧抽屉）含多个 tab：Overview / Documentation / **Access** / Lineage
- Access tab 显示：当前用户角色、组织/标记要求、可管理的 group/user 角色
- **就近原则**：在资源上下文里管理权限，不跳转，不打断浏览流
- Owner 角色才能看到完整管理界面；Viewer 只看到自己的角色

**对 Gaia 启示**：权限管理应集成在 OntologyWorkspace / DataSourceDetail / ObjectType 详情面板的 Access tab，而非单独的"权限管理中心"页面。

### 1.2 Databricks Catalog Explorer：中央 Grants 管理 UI

[Catalog Explorer](https://medium.com/@infinitylearnings1201/databricks-de-associate-day-21-catalog-explorer-454dfdf42b20)：

Databricks 的 Catalog Explorer 是**中央 UI**，管理所有数据资产的 grants/revokes/ownership/审计。

**关键设计**：
- 浏览所有 catalog/schema/table 资产
- 每个资源详情页有三 tab：Details / **Permissions** / Lineage
- Permissions tab：显示所有 principals（user/group/service principal）的 grants，可 grant/revoke
- **所有权（Ownership）**：明确显示资源 owner，强化"谁负责"
- **审计视图**：who has access to which data

**对 Gaia 启示**：需要中央视图看"谁能访问什么"（审计/治理用），但日常授权就近在资源页。

### 1.3 AWS IAM Console 重新设计：流线型、消除 tab 切换

[AWS Redesigned IAM Console](https://aws.amazon.com/blogs/security/introducing-the-redesigned-iam-console/)：

AWS 2024 重新设计 IAM Console，核心改进：
- **流线型外观**：管理大量资源列表（几百 user/group/role）更方便
- **消除 tab 切换**：资源详情页重构，减少上下文切换
- **移动端优化**
- **采纳推荐**：更易采纳 AWS 推荐的最佳实践（如最小权限）

**对 Gaia 启示**：大量资源列表（几十个 Ontology/上百个 Group）要考虑分页/搜索/筛选；减少 tab 切换。

### 1.4 通用最佳实践总结

| 实践 | 来源 | Gaia 应用 |
|------|------|----------|
| 就近管理（资源详情面板 Access tab） | Palantir Compass | OntologyWorkspace/DataSourceDetail 加 Access tab |
| 中央视图（who has access to what） | Databricks Catalog Explorer | 独立的 AuditLogViewer + CheckAccessPanel |
| 渐进式披露 | [NN/g](https://www.nngroup.com/articles/progressive-disclosure/) | 默认视图日常用例，高级面板明确进入 |
| Contextual permissions（就近而非中断） | [web.dev](https://web.dev/articles/permissions-best-practices) | 在资源上下文授权，不弹全局对话框 |
| 所有权明确显示 | Databricks | 每个资源显示 Owner |
| 大列表分页/搜索/筛选 | AWS IAM | Group/Role 列表 |
| 减少上下文切换 | AWS IAM | 详情页重构，少 tab |

---

## 二、"自然感"设计哲学

> 目标：让权限体系对用户「自然，本来就应该是这个样子」，而非增加负担。用户包括业务用户和开发者。

### 2.1 核心论点：安全与可用不是对立的

[HP 论文](https://shiftleft.com/mirrors/www.hpl.hp.com/techreports/2009/HPL-2009-341.pdf)（Karp & Stiegler, CHI 2010）核心论点：

> 「There is an inevitable tension between security and usability.」——**这个说法是错误的**。紧张源于我们**无法准确判断用户意图**，而非安全本身。

[IAM UX Design Principles](https://startwithidentity.com/articles/iam-user-experience-design-principles/) 进一步指出：

> 「For decades, identity and access management has been designed by security engineers for security engineers... Users do not interact with your IAM system because they want to—they interact with it because it stands between them and the thing they actually want to do.」

**设计目标**：让权限体系成为**透明的基础设施**——用户感知不到它的存在（除非主动需要），它自然地融入工作流，不打断、不增加认知负担。

### 2.2 "自然感"的五个维度

基于 HP 论文三大维度（信息/表达力/控制）+ 业界研究，提炼"自然感"的五个维度：

#### 维度 1：从动作推断意图（不中断询问）

[Chromium: You Probably Don't Need a Permission Prompt](https://chromium.googlesource.com/chromium/src.git/+/main/docs/security/no-prompts-please.md)：

> 「The tension between functionality and security occurs when we cannot accurately determine user intent.」

**自然做法**：从用户的指代动作推断授权意图，而非弹窗询问。
- 用户「把对象加入看板」= 授权查看，不弹"是否授权"
- 用户「创建 Space」= 自动成三层 Owner，不弹"请配置权限"
- 用户「分享 Scenario 给同事」= 授权该同事，不弹"选择权限级别"

**反自然**：每个操作都弹窗确认（Just-Say-Yes 条件反射，安全形同虚设）。

#### 维度 2：就近原则（在上下文中管理）

[web.dev permissions best practices](https://web.dev/articles/permissions-best-practices)：

> 「Ask for permission after a user interaction, not on page load.」

**自然做法**：权限管理在资源上下文里（Access tab），不跳转到独立的权限管理中心。
- 在 Ontology 详情面板授权，不跳"权限管理页"
- 被拒绝时，就在被拒处显示原因 + 申请入口

**反自然**：权限管理是孤立页面，用户要来回切换。

#### 维度 3：渐进式披露（默认最简）

[NN/g Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/)：

> 「Initially, show users only a few of the most important options. Offer a larger set of specialized options upon request.」

**自然做法**：
- 默认视图：日常用例（查看数据、查询、执行已授权 Action）
- 高级面板：标记/行级策略/三层容器管理（明确进入"管理"模式）
- 单租户默认 Organization 不暴露三层管理（只有一个默认值，用户无感）

**反自然**：一上来展示所有配置项（标记分类、角色集、行级策略、组织白名单），吓退用户。

#### 维度 4：可预测 + 可理解（知道为什么）

[Authorization UX 2026](https://authorize.live/authorization-ux-2026)：

> 「Predictable behaviors: users should understand why access is denied. Graceful degradation: provide helpful fallbacks when access is restricted.」

**自然做法**：
- 无权限资源默认隐藏（不可见即安全，防枚举）
- 但用户主动尝试访问被拒时，显示**可读的拒绝原因** + **申请权限入口**
- Check Access 工具：任意用户+资源，展示五层校验状态 + 权限来源

**反自然**：拒绝只返回 403 Forbidden，不解释；或无权限资源报错暴露存在性。

#### 维度 5：JIT 授权（需时才问）

[Authorization UX 2026](https://authorize.live/authorization-ux-2026)：

> 「Just-in-time authorization: request permission at the moment of need with clear context.」

**自然做法**：
- 用户遇到无权限资源时，**就在当前上下文**申请权限（不跳转申请门户）
- 自动审批（低风险）或流转 Owner（高风险）
- 到期自动回收

**反自然**：必须提前到权限管理中心预授所有权限，或走冗长工单流程。

### 2.3 前端授权的三道门（反"只藏按钮"）

[Hiding the Button Isn't Authorization](https://dev.to/nwosaemeka/hiding-the-button-isnt-authorization-why-you-must-gate-the-request-156k)：

> 「Most teams think authorization means hiding UI elements... It isn't. Open the network tab and you'll see the real story.」

前端授权不是只藏按钮，而是三道门：

| 门 | 作用 | Gaia 实现 |
|----|------|----------|
| **Render Gate** | 用户能看到这个按钮/页面/菜单吗？ | 后端返回 `allowedActions`，前端只渲染允许的 |
| **Data Gate** | 应该 fetch 这个数据吗？ | 无权限资源不调 API（避免 403 噪音） |
| **Guard Gate** | 后端最终校验（防绕过） | AuthorizationService 五层校验 |

**关键**：前端三道门都要做，但**后端 Guard Gate 是唯一可信来源**（前端可被绕过）。前端隐藏按钮是为了 UX（不展示无权操作），不是为了安全。

---

## 三、Gaia 前端界面设计

> 基于 Gaia 现有技术栈（React 19 + Tailwind + React Aria Components + Cytoscape）+ 上述业界实践，设计权限治理的前端界面。

### 3.1 界面总体原则

1. **权限管理就近集成**（非孤立页面）——资源详情面板 Access tab
2. **中央治理视图独立**——AuditLogViewer / CheckAccessPanel / UserGroupManagement（管理员用）
3. **渐进式披露**——默认视图不暴露三层容器（单租户默认 Organization 隐藏）
4. **三道门**——前端 allowedActions 渲染 + Data Gate + 后端 Guard
5. **JIT 授权**——被拒处申请，不跳门户
6. **自然感**——从动作推断意图，少弹窗

### 3.2 关键界面设计

#### 3.2.1 资源详情面板 Access tab（就近管理）

集成在现有 OntologyWorkspace / DataSourceDetail / ObjectType 详情面板：

```
┌─ OntologyWorkspace: Airline ─────────────────────────┐
│ [Overview] [Objects] [Actions] [Access] [Lineage]    │  ← 新增 Access tab
├───────────────────────────────────────────────────────┤
│ Access tab                                            │
│                                                       │
│ 你在此本体的角色: Editor                              │
│ 所属 Space: airline-core (Space Owner: 你)            │
│ 所属 Project: default (Project Owner: 你)             │
│                                                       │
│ ── 角色授予 ──                                        │
│ [+ 授予角色]                                          │
│ ┌ Group            │ Role    │ Scope   │ Expires ──┐ │
│ │ 航空分析组        │ Viewer  │ Project │ 永久      │ │
│ │ 运营组            │ Editor  │ Project │ 2026-12  │ │
│ └──────────────────┴─────────┴─────────┴──────────┘ │
│                                                       │
│ ── 标记 ──                                            │
│ 此本体无标记                                          │
│                                                       │
│ ── 行级策略 ──                                        │
│ 无                                                    │
└───────────────────────────────────────────────────────┘
```

**设计要点**：
- 显示当前用户角色（上下文感知）
- 显示资源归属（Space/Project）
- 角色授予列表（Group + Role + Scope + Expires）
- 标记 / 行级策略就近查看（折叠，点击展开）
- `[+ 授予角色]` 就地操作，不跳转

#### 3.2.2 被拒绝时的 JIT 申请（不跳门户）

用户尝试访问无权限资源时：

```
┌─ 对象类型: VIP客户 ───────────────────────────────────┐
│                                                       │
│        🔒 你没有权限查看此对象类型                    │
│                                                       │
│   原因: 缺少标记 'VIP'                                │
│   此对象类型带有 VIP 标记，你的账户未持有该标记权限   │
│                                                       │
│   [申请 VIP 标记权限]  [我知道了]                     │
│                                                       │
└───────────────────────────────────────────────────────┘
```

**设计要点**：
- 可读的拒绝原因（哪一层拦截、缺什么）
- 就地申请入口（不跳"权限申请门户"）
- 申请带上下文（自动填资源 + 申请的标记/角色）

#### 3.2.3 Check Access 调试面板（可解释性）

```
┌─ Check Access ────────────────────────────────────────┐
│ 用户: [张三        ▼]   资源: [ObjectType: VIP客户 ▼]│
│ 操作: [view        ▼]                                 │
│ [检查]                                                │
├───────────────────────────────────────────────────────┤
│ 校验结果: ❌ DENY                                     │
│                                                       │
│ Layer 1 身份认证      ✅ PASS  (张三, ACTIVE)         │
│ Layer 2 Organization  ✅ PASS  (org-internal)         │
│ Layer 3 Space         ✅ PASS  (airline-core 准入)    │
│ Layer 4 Project RBAC  ✅ PASS  (Viewer, default proj) │
│ Layer 5 Marking       ❌ DENY  (缺 'VIP' 标记)  ← 拦截│
│                                                       │
│ 权限来源:                                             │
│   Viewer 角色 ← 航空分析组 ← 张三 (组成员)            │
│   缺失: VIP 标记 (Marking Admin 管理)                 │
│                                                       │
│ [模拟: 如果授予 VIP 标记]  [申请 VIP 标记]            │
└───────────────────────────────────────────────────────┘
```

**设计要点**：
- 五层校验可视化（每层 PASS/DENY）
- 权限来源追溯（哪个 Group → 哪个 Role → 用户）
- 缺失权限明确
- 模拟授权（"如果授予 X，会怎样"）
- 申请入口

#### 3.2.4 中央治理视图（管理员用，独立页面）

```
┌─ 治理中心 ────────────────────────────────────────────┐
│ [用户组] [角色] [标记] [审计日志] [访问申请]          │
├───────────────────────────────────────────────────────┤
│ 用户组管理                                            │
│ [+ 新建组]  搜索: [________]  筛选: [组织▼]           │
│ ┌ 组名           │ 成员数 │ 归属组织 │ 角色 ────────┐│
│ │ 航空分析组      │ 12     │ internal │ Viewer(多)  ││
│ │ 运营组          │ 8      │ internal │ Editor(多)  ││
│ └────────────────┴────────┴──────────┴──────────────┘│
└───────────────────────────────────────────────────────┘
```

**设计要点**：
- 独立页面（管理员日常用）
- 大列表分页/搜索/筛选
- 仅管理员可见（渐进式披露，普通用户看不到入口）

#### 3.2.5 渐进式披露：三层容器默认隐藏

单租户部署，默认 Organization/Space/Project 不在主导航暴露：

```
普通用户主导航:
[本体] [数据源] [图探索] [操作面板]

管理员额外可见（点击头像 → 管理）:
[治理中心] [组织] [空间] [项目]  ← 三层容器在此
```

**设计要点**：
- 单租户默认 Org/Space/Project 是"幕后"概念（用户无感）
- 管理员从"管理"入口进入三层容器管理
- 多租户场景才在主导航暴露组织切换

### 3.3 前端技术实现要点

#### 3.3.1 allowedActions 模式（Ship the Policy）

后端在每个资源响应里返回 `allowedActions`，前端只渲染允许的操作：

```typescript
// 后端响应
{
  "id": "obj-type-1",
  "apiName": "VIPCustomer",
  "allowedActions": ["view", "edit", "delete"],  // 当前用户能做的
  "disabledReasons": {}  // 无 disabled
}

// 无权限资源
{
  "id": "obj-type-2",
  "allowedActions": [],
  "disabledReasons": {
    "view": "缺少标记 'VIP'"
  }
}
```

```tsx
// 前端只渲染 allowedActions
{resource.allowedActions.includes("delete") && (
  <Button onClick={handleDelete}>删除</Button>
)}
```

**关键**：前端不自己判断权限，只渲染后端给的 allowedActions（避免 drift）。

#### 3.3.2 React Aria Components（无障碍 + 一致性）

Gaia 已用 React Aria Components（ADR-013）。权限 UI 复用现有原语：
- `Modal`（JIT 申请弹窗）
- `Select`（角色/资源选择）
- `DataTable`（角色授予列表）
- `Disclosure`（标记/策略折叠）

#### 3.3.3 不可见即安全的实现

无权限资源**不在列表返回**（后端过滤），前端不显示。但用户直接访问 URL 时显示 JIT 申请界面（非 404/403）。

---

## 四、开发者体验与架构可理解性

> 目标：让开发者（含外部集成者）快速理解 Gaia 权限架构与设计，能快速上手集成。

### 4.1 架构文档可理解性：C4 模型渐进式 zoom

[C4 Model](https://archman.dev/docs/documentation-and-modeling/views-and-viewpoints/c4-model-context-container-component-code) 提供四级渐进式架构图，不同受众看不同层级：

#### Level 1: Context（给非技术干系人）

```
[业务用户] ──→ [Gaia 平台] ←── [外部 Agent (MCP)]
                     ↑
              [OIDC 身份提供商]
```

一句话：Gaia 是本体驱动的数据平台，通过 OIDC 认证用户，Agent 也可接入。

#### Level 2: Container（给技术决策者）

```
[OIDC IDP] ─OIDC/SCIM→ [Gaia 后端 (FastAPI)]
                           ├─ AuthMiddleware (认证)
                           ├─ AuthorizationService (PDP 五层校验)
                           ├─ 各引擎 (Doris/PG/Trino/Neo4j) ← 权限下推
                           └─ [前端 (React)]
[SCIM 推送] ─────────────→ User/Group 同步
```

#### Level 3: Component（给开发者）

```
AuthorizationService (PDP)
├─ Layer 1: PrincipalService (身份解析)
├─ Layer 2-3: Organization/Space 校验
├─ Layer 4: RoleAssignment 查询 (Project RBAC)
├─ Layer 5: MarkingService (合取校验)
└─ QueryScope 求值 (行/列级)

各引擎 PEP:
├─ DorisIndexStore (Row Policy 下推)
├─ PostgresMetaStore (RLS USING + WITH CHECK)
├─ TrinoQueryEngine (应用层补偿/OPA)
└─ Neo4jGraphStore (Cypher WHERE)
```

#### Level 4: Code（给实现者）

类/方法级，由代码本身 + docstring 承载（不单独画图）。

### 4.2 开发者快速上手路径

设计文档应提供「5 分钟理解」路径，按角色分：

#### 业务开发者（用 Gaia 建本体）

```
5 分钟理解:
1. 你的本体在某个 Space（业务域）
2. 你是 Ontology Owner（创建时自动）
3. 邀请同事：加 Group → 授 Project 角色（在 Access tab）
4. 敏感数据：打 Marking（PII/机密）→ 无标记权限的人看不到
5. 行级隔离：配 RowSecurityPolicy（销售只看本区域）
```

#### API 集成者（调 Gaia API）

```
5 分钟理解:
1. OIDC 登录获取 token
2. 每请求带 Authorization: Bearer <token>
3. 响应含 allowedActions（前端据此渲染）
4. 403 时看 reason 字段（哪层拦截 + 缺什么）
5. Service User: 创建 scoped 账号调 API
```

#### Agent 开发者（MCP/AG-UI 接入）

```
5 分钟理解:
1. Agent 以 Service User 身份执行（scoped）
2. 22 工具每个声明所需权限
3. 工具调用前 ToolExecutor 校验
4. FORBIDDEN 返回 reason（给 Agent 解释为何不能）
5. Check Access API 可查询权限（Agent 主动探测）
```

### 4.3 开发者可调试性

#### Check Access API（程序化权限查询）

```bash
# 查询权限（Agent/脚本/前端调试用）
GET /authz/check?principal_id=u1&resource_type=OBJECT_TYPE&resource_id=ot1&action=view

# 响应
{
  "result": "DENY",
  "layer": "MARKING",
  "reason": "缺少标记 'VIP'",
  "permissions": {
    "role": {"source": "航空分析组", "role": "Viewer"},
    "missing_markings": ["VIP"]
  },
  "simulate": "/authz/check?...&simulate_marking=VIP"
}
```

#### 本地开发模式（无需 OIDC）

```bash
# 开发模式：X-User-Id 请求头模拟身份
curl -H "X-User-Id: alice" -H "X-User-Roles: editor" http://localhost:8000/ontologies

# 生产模式：OIDC token
curl -H "Authorization: Bearer <jwt>" http://localhost:8000/ontologies
```

### 4.4 架构决策可追溯

每个设计决策有 ADR 记录「为什么」。开发者遇到疑问时：
- 查 [ADR-016](../architecture/adr-016-permission-governance.md) D1-D10 决策点
- 查 [设计文档 §〇 设计哲学与核心概念](../design/permission-governance-design.md#〇设计哲学与核心概念) 原则
- 查 [评估报告](../architecture/permission-governance-landing-assessment.md) 评估依据

### 4.5 文档分层（按读者）

| 文档 | 读者 | 内容 |
|------|------|------|
| [评估报告](../architecture/permission-governance-landing-assessment.md) | 架构评审者 | 现状/裁剪/决策依据/风险 |
| [ADR-016](../architecture/adr-016-permission-governance.md) | 架构评审者 | 决策固化 |
| [设计文档 §〇](../design/permission-governance-design.md#〇设计哲学与核心概念) | 所有开发者 | 设计哲学与核心概念（为什么） |
| [设计文档 §一-§十](../design/permission-governance-design.md) | 实现者 | 数据模型/Service/API/迁移/测试（怎么做） |
| [本研究](.) | 前端/体验设计者 | 业界实践/自然感/开发者体验 |
| README + Quick Start | 新用户 | 5 分钟上手 |

---

## 五、设计原则汇总与反模式

### 5.1 前端交互设计原则（10 条）

1. **从动作推断意图**——少弹窗，多推断（HP 原则）
2. **就近管理**——资源详情面板 Access tab，不孤立页面
3. **渐进式披露**——默认最简，高级面板明确进入；单租户默认 Org/Space 隐藏
4. **三道门**——Render Gate（allowedActions）+ Data Gate + 后端 Guard
5. **不可见即安全 + 可读拒绝**——无权资源隐藏；主动访问被拒时显示原因 + 申请入口
6. **JIT 授权**——需时才问，就地申请，不跳门户
7. **可解释性**——Check Access 五层可视化 + 权限来源追溯
8. **策略即数据**——后端返回 allowedActions，前端只渲染不推导
9. **所有权明确**——每个资源显示 Owner
10. **大列表友好**——分页/搜索/筛选（Group/Role 多时）

### 5.2 开发者体验原则（5 条）

1. **C4 渐进式架构图**——Context/Container/Component/Code 四级，不同受众
2. **5 分钟上手路径**——按角色（业务/API/Agent）分
3. **可调试**——Check Access API + 本地开发模式（X-User-Id）
4. **决策可追溯**——ADR + 设计哲学章节
5. **文档分层**——按读者分（评审者/实现者/新用户）

### 5.3 反模式（避免）

| 反模式 | 后果 | 正确做法 |
|--------|------|---------|
| 每个操作弹窗确认 | Just-Say-Yes 条件反射，安全形同虚设 | 从动作推断意图 |
| 权限管理孤立页面 | 来回切换，打断浏览流 | 就近 Access tab |
| 一上来展示所有配置 | 吓退用户 | 渐进式披露 |
| 只藏按钮不做后端校验 | 抓包绕过，虚假安全 | 三道门 + 后端 Guard |
| 拒绝只返回 403 | 用户不知道为什么 | 可读原因 + 申请入口 |
| 无权资源报 404/403 | 暴露存在性，可枚举 | 不可见即安全（列表不返回） |
| 前端自己判断权限 | 与后端 drift | allowedActions 后端给 |
| 单租户暴露三层管理 | 增加认知负担 | 渐进式披露，默认隐藏 |
| 无 Check Access 工具 | 权限黑盒，排查困难 | 五层可视化 |
| 架构图只有一张大图 | 不同受众看不懂 | C4 四级渐进 |

---

## 附录：研究来源

### 业界权限 UI
- Palantir Compass Access tab: https://palantir.com/docs/foundry/compass/use-project-details-panel/
- Palantir Projects and roles: https://palantir.com/docs/foundry/security/projects-and-roles/
- Databricks Catalog Explorer: https://medium.com/@infinitylearnings1201/databricks-de-associate-day-21-catalog-explorer-454dfdf42b20
- Databricks Manage privileges: https://docs.databricks.com/aws/en/data-governance/unity-catalog/manage-privileges/
- Snowflake Access Control: https://docs.snowflake.com/en/user-guide/security-access-control-overview
- AWS Redesigned IAM Console: https://aws.amazon.com/blogs/security/introducing-the-redesigned-iam-console/

### 自然感设计哲学
- HP Making Policy Decisions Disappear: https://shiftleft.com/mirrors/www.hpl.hp.com/techreports/2009/HPL-2009-341.pdf
- IAM UX Design Principles: https://startwithidentity.com/articles/iam-user-experience-design-principles/
- Chromium No Prompts Please: https://chromium.googlesource.com/chromium/src.git/+/main/docs/security/no-prompts-please.md
- web.dev permissions best practices: https://web.dev/articles/permissions-best-practices
- NN/g Progressive Disclosure: https://www.nngroup.com/articles/progressive-disclosure/
- Authorization UX 2026: https://authorize.live/authorization-ux-2026
- Hiding the Button Isn't Authorization: https://dev.to/nwosaemeka/hiding-the-button-isnt-authorization-why-you-must-gate-the-request-156k
- Secure Data Sharing Interfaces: https://developerux.com/2026/03/25/how-to-design-secure-data-sharing-interfaces/
- Architecting Access by Design (UACP): https://kie.ie/docs/Architecting%20Access%20by%20Design.pdf

### 开发者文档
- C4 Model: https://archman.dev/docs/documentation-and-modeling/views-and-viewpoints/c4-model-context-container-component-code
- Architecture as Code: https://docs.spryker.com/docs/dg/dev/architecture/architecture-as-code
- Architecture Diagram Best Practices: https://infrasketch.net/blog/architecture-diagram-best-practices
