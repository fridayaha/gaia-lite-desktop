# Palantir 核心能力差距分析：Gaia 当前缺失与不足

> **研究主题**：Palantir Foundry / AIP / Apollo 有哪些核心能力是 Gaia 当前还没有或不足的，按用户可见价值优先级排列。
> **研究方法**：以 Palantir 官方文档（palantir.com/docs）、官方博客（blog.palantir.com）、官方白皮书（assets.palantir.com）为第一手来源，对每个能力点下钻到机制层（不是"知道有"，而是"知道怎么实现、为什么这么实现、用户如何感知"）。
> **对照基线**：Gaia 当前实现状态以 [`docs/architecture/implementation-status.md`](../architecture/implementation-status.md) 为准；本体工具层对齐情况见 [`docs/reference.md`](../reference.md)（22 工具已对齐 Palantir 范式）。
> **研究日期**：2026-07-06
> **关联文档**：[`docs/reference.md`](../reference.md)（工具层范式）· [`docs/reference-palantir-ontology.md`](../reference-palantir-ontology.md)（本体建模规范）· [`docs/reference-graph-reasoning.md`](../reference-graph-reasoning.md)（图推理范式）

---

## 〇、研究方法论与价值评估框架

### 为什么这份研究要"深度优先"

表面研究只能给出"Palantir 有 Scenario / Workshop / 权限"这种清单，无法回答两个对 Gaia 真正重要的问题：
1. **这个能力的本质机制是什么**（决定 Gaia 该不该做、怎么做、能不能用更轻的方式复现）
2. **用户如何感知这个能力**（决定优先级——有些能力技术上很难但用户无感，有些能力技术简单但直接决定产品成败）

因此本研究对每个能力点都要求挖到：(a) Palantir 官方怎么描述它；(b) 它的底层机制/数据结构/执行链路；(c) 用户操作路径与价值感知；(d) Gaia 当前现状（已有 / 部分 / 完全缺失）；(e) 差距的本质与可选路径。

### 价值优先级评估维度

按"对用户可见的价值"排序，权重从高到低：

| 维度 | 权重 | 说明 |
|------|:---:|------|
| **决策闭环度** | ★★★★★ | 是否让用户从"看数据"走到"做决策→执行→看到结果→改进"。这是 Palantir 与 BI 工具的根本分野 |
| **用户日常触达频率** | ★★★★★ | 用户每天/每周是否都用，还是边缘场景 |
| **不可替代性** | ★★★★ | 没有这个能力，用户是否能用其他方式绕过（绕过成本多高） |
| **安全/合规底线** | ★★★★ | 缺失是否直接阻挡企业落地（尤其 toB/toG 场景） |
| **AI 时代杠杆** | ★★★ | 是否是 AI Agent 可靠落地的前置条件 |
| **实现成本** | ★★ | 反向权重：成本越低、价值越高的越优先（仅作参考，不主导排序） |

> **关键判断**：Gaia 已经对齐了 Palantir 的"感知层"（22 工具 + 图推理 + ObjectSet IR），但 **Palantir 真正的护城河不在感知层，而在"决策闭环 + 治理 + 反馈飞轮"**。这三者构成的价值飞轮是本研究识别出的最高优先级差距。

---

## 一、价值优先级总览（结论先行）

按"对用户可见价值"从高到低排列，Gaia 当前缺失/不足的 Palantir 核心能力：

| 优先级 | 能力 | 用户可见价值 | Gaia 现状 | 本质差距 |
|:---:|------|------------|:---:|--------|
| **P0** | **决策捕获与反馈飞轮（Decision Exhaust）** | 让"做决策"本身成为可积累的组织资产 | 🟡 雏形 | 有 action_execution_logs 但无"决策→结果→学习"闭环 |
| **P0** | **细粒度权限治理体系（Marking + PBAC + Object/Property Policy）** | 企业能否把系统交给一线员工和 Agent | 🔴 缺失 | principal=anonymous，无行/列/cell 级控制 |
| **P1** | **Scenario 沙箱与 What-if 推演** | 决策前先"演习"，低风险验证方案 | 🔴 缺失 | 完全没有本体数据 fork 机制 |
| **P1** | **运营应用构建器（Workshop / Slate 范式）** | 业务人员自助搭建"能操作"的应用 | 🟡 部分 | 有图探索/详情面板，但无 Variables/Events/Actions 应用框架 |
| **P1** | **共享逻辑层（Foundry Functions + AIP Logic）** | 业务逻辑一处定义、处处可用（Action/应用/Agent/管道） | 🔴 缺失 | 逻辑散落在 Action 规则和 Agent toolset，无独立 Function 资产 |
| **P2** | **OSDK（本体 SDK 代码生成）** | 把本体变成开发者后端，外部应用即用 | 🔴 缺失 | 有 MCP/AG-UI/REST 三入口，但无强类型 SDK 生成 |
| **P2** | **全链路动态血缘（data↔logic↔action↔application）** | 改一处知道影响范围；删除知道扩散到哪 | 🔴 缺失 | 只有 Gravitino 物理血缘，无跨层血缘 |
| **P2** | **实时订阅（Ontology 变更推送）** | 数据变了应用立刻变，运营实时性 | 🔴 缺失 | 仅轮询/CDC，无 WebSocket/SSE 推送 |
| **P3** | **MLOps / Model 集成（Modeling Objective + 部署）** | 模型绑定本体，推理结果进决策 | 🔴 缺失 | 仅 LLM 原语，无传统 ML 训练/部署/特征 |
| **P3** | **AIP Evals（LLM 函数评估套件）** | AI 上线前可测、上线后可回归 | 🔴 缺失 | 无 eval harness，Agent 改动靠人工验证 |
| **P3** | **Global Branching（环境级 Git fork）** | 大改动端到端隔离测试再合并 | 🔴 缺失 | 无分支概念 |
| **P3** | **Apollo（边缘/多环境持续部署）** | 同一套软件部署到云/边/气隙 | ⚫ 不适用 | Gaia 定位开源本地优先，可降级为容器编排 |

下面逐项深度展开。每项包含：**Palantir 机制深描 → 用户价值路径 → Gaia 现状 → 差距本质 → 可选路径**。

---

## 二、P0-A：决策捕获与反馈飞轮（Decision Exhaust）

> **一句话**：Palantir 把"每一次决策"（谁、何时、基于哪版数据、通过哪个应用、为什么、结果如何）都捕获成本体对象，形成"决策废气→组织学习"的闭环。这是 Palantir 最重要的差异化，也是 Gaia 最该补的能力。

### 2.1 Palantir 机制深描

**核心概念：Decision Exhaust（决策废气）**

Palantir 官方博客《Connecting AI to Decisions with the Palantir Ontology》明确表述：

> "the full expanse of the enterprise comes to life in [decisions]... decision was made, atop which version of enterprise data, and through which application"
> "Closing the action loop as decisions are made in real-time is what distinguishes an operational system from an analytical system."

这不是一句口号，而是一套机制：

1. **Action 是决策的载体**。每个 Action Type 不只是"改数据"，它的每次执行都记录：调用者（principal）、时间戳、入参、修改前快照（CDL = Change Description Log）、修改后状态、版本号。Gaia 的 `action_execution_logs` 已经有这个雏形。

2. **决策被物化成本体对象**。这是关键差异——Palantir 不是把决策日志写在一张孤立的 audit 表里，而是把"决策"本身建模为 ObjectType（如 `Decision`、`AllocationDecision`、`MaintenanceOrder`），与被决策的业务对象通过 Link 关联。这样决策就能被查询、聚合、与结果对照。

3. **结果回流形成反馈环**。官方白皮书《Connecting AI and ML with Operations》：
   > "As operators, business processes, and systems make decisions, [the Ontology] forms a feedback loop between operational actions and the models that informed them."

   即：决策（Action）→ 写回业务系统（Writeback webhook）→ 业务结果产生新数据 → 数据回流本体 → 与原决策对照 → 评估决策质量 → 改进模型/规则。这是 Palantir 反复强调的 "software-defined feedback loops" / "local and global organizational learning"。

4. **决策可追溯到具体数据版本**。Agentic Runtime 博客：
   > "Every data query can be tied to a full version history for the given data source — including the transformation logic that was used to produce that particular version of the data."

   即每次决策不仅记录"用了哪些对象"，还记录"用的是这些对象的哪个版本、哪个数据管道产出的版本"。这让"复盘为什么当时做了这个决策"成为可能。

**Writeback 机制（决策落地的关键）**

Palantir Action 有两种 webhook 配置（官方文档《Action types → Webhooks》）：

| 类型 | 执行时机 | 失败是否阻断 | 语义 |
|------|---------|:---:|------|
| **Writeback** | 本体修改**之前** | 是 | 保证"外部系统没写成功就不动本体"——事务性 |
| **Side effect** | 本体修改**之后** | 否 | best-effort 通知/多系统同步 |

- Writeback webhook 的输出参数可被后续规则使用（如外部系统返回的新订单号写入本体）
- Side effect webhook 可接收 list payload 触发多次执行
- OAuth 2.0 由 Foundry 托管，开发者不碰 token

这套机制让"决策→执行到真实业务系统"变得安全且可组合。**这是"operational"与"analytical"的分水岭**——BI 工具止步于"洞察"，Foundry 走到"执行+反馈"。

### 2.2 用户价值路径

- **运营主管**：看到"上周做了 47 个补货决策，其中 12 个结果低于预期（实际销量 < 预测 20%+），这 12 个都用了某供应商的货"→ 直接定位决策质量问题
- **数据科学家**：模型上线后，每个模型推荐都关联到"是否被采纳 + 采纳后结果"→ 自动算模型真实业务价值，而不是离线指标
- **审计/合规**：任何一笔业务状态变化都能回答"谁、何时、基于什么信息、通过什么操作改的"→ 满足监管
- **Agent 自我改进**：Agent 的每次动作及其结果都是本体数据，可被后续 Agent 调用学习（"上次类似情况采取了 A 动作，结果不好，这次换 B"）

### 2.3 Gaia 现状

- ✅ `ActionExecutionLogModel`（`action_execution_logs` 表）记录了 action 执行的 principal/time/params/CDL 前后快照/outbox
- ✅ `AnalysisRecordStore`（ADR-015 M6）有"证据链快照"（evidence_id），记录推理过程的 ObjectSet IR + 结果
- 🟡 **但决策没有被物化成本体对象**——execution_log 是审计表，不是可查询的 ObjectType，无法被聚合/关联/对照结果
- 🟡 **Writeback 只有 SQL UPSERT/MERGE**（`WriteBackManager`），没有 webhook writeback/side effect 的事务性区分，没有 OAuth 托管
- 🔴 **没有"决策→结果→学习"闭环**——action 执行完就结束，没有机制把后续业务结果回流对照原决策
- 🔴 **决策不绑定数据版本**——execution_log 不记录"用的是哪个 snapshot 的对象"

### 2.4 差距本质

Gaia 有"动作执行记录"的骨架，但缺三块：
1. **决策即对象**：决策应该是一等公民的本体对象（可建模、可查询、可关联结果），而不是审计日志里的一行
2. **结果回流管道**：需要一种机制把"决策后的业务结果"（来自 CDC/外部系统回流）与原决策关联，计算决策质量指标
3. **版本锚定**：execution_log 应记录决策时刻的数据版本（Iceberg snapshot_id / object_state version），让复盘可重现"当时的视图"

### 2.5 可选路径（Gaia 落地方向）

1. **第一步（低成本高价值）**：把 `ActionExecutionLog` 升级为可选的本体对象——用户可声明某 ActionType 的执行应物化为 `Decision` 对象，自动 link 到 affected objects。这样决策立刻进入本体可查询层
2. **第二步**：execution_log 增加 `data_version_anchor` 字段（Iceberg snapshot_id + object_state version），决策可时间旅行回放
3. **第三步**：Writeback webhook 支持 writeback/side effect 双模式 + 事务性保证（已有 outbox 基础，扩展 effect type）
4. **第四步**：决策结果回流——定义 `DecisionOutcome` 对象类型 + 定时/事件驱动的对照任务（CDC 检测被决策对象的状态变化 → 计算 outcome 指标 → 写回 Decision）

> **优先级判断**：这是 Gaia 从"本体查询工具"升级为"运营决策平台"的关键跃迁。没有它，Gaia 永远是"分析师的工具"；有了它，Gaia 才是"运营的操作系统"。**P0**。

---

## 三、P0-B：细粒度权限治理体系

> **一句话**：Palantir 的权限是"三层范式叠加（Role + Marking + Purpose）× 三层粒度（对象类型/对象实例/属性）× 贯穿 data/logic/action/application"。Gaia 当前 principal=anonymous，是企业落地的硬阻断。

### 3.1 Palantir 机制深描

**三种正交的访问控制范式（可混合）**

官方白皮书《Foundry Technical》明确：
> "04. SECURITY: Role-, Classification-, and Purpose-based paradigms; Integration with existing authorization models; Propagation by default; extreme configurability"

| 范式 | 全称 | 控制什么 | 典型场景 |
|------|------|---------|---------|
| **RBAC** | Role-Based | 用户能做什么操作（按角色） | "分析师"角色能查询，"运营"角色能执行 Action |
| **CBAC / Marking** | Classification-Based | 数据的密级标记，用户需持有对应 marking 才能看 | VIP 客户数据标 `VIP` marking，只有持 VIP 的人能看 |
| **PBAC** | Purpose-Based | 按"使用目的"授权，访问需申请 Purpose 并记录 rationale | GDPR 合规：访问 PII 需声明"用于客户服务"目的 |

PBAC 是 Palantir 最独特的（来源博客《Purpose-based access controls at Palantir》）：
- 用户申请的不是"某个数据集"，而是"一个 Purpose"（目的）
- Purpose 由数据治理团队定义，精确圈定该目的可访问的数据范围（no more, no less）
- 授权时治理方和数据所有方都要记录 rationale（理由）
- 所有访问决策被持久化，可随时回溯"谁、为何、何时获得了什么访问权"
- 这是为了满足隐私法规（GDPR 的"目的限制""最小化"原则）的工程化落地

**对象级权限实现（行/列/cell 级）**

官方文档《Object permissioning → Object security policies》：

- **Object security policy**（对象安全策略）= 行级安全：在 ObjectType 上配置策略，决定哪些对象实例对谁可见。独立于底层数据源权限
- **Property security policy**（属性安全策略）= 列级安全：对选定属性集配置策略
- **两者叠加 = cell 级安全**：用户必须同时通过对象策略和属性策略才能看到属性值；通过对象策略但不过属性策略 → 看到 null（不是报错，是静默脱敏）
- **Granular policy**（细粒度策略）= 表达式引擎：`用户属性 OP 列/属性 OP 值` 的规则组合，如 `user.region == object.region`

关键工程细节：
- 对象安全策略**推荐优于 restricted views**，因为：策略更新近乎瞬时、支持流式和分支、统一 cell 级安全
- 策略默认继承底层数据源的 mandatory controls（markings/organizations/classifications）
- **权限下推到存储层**（reference.md 第1迭代已记录）：无权限对象在索引层就被过滤，不会返回上层，从物理上杜绝"存在性泄露"（用户无法区分"不存在"和"无权限"）

**权限贯穿四层（data/logic/action/security 是 Ontology 的"四fold整合"）**

官方《The Ontology system》：
> "the Ontology's security system has to reconcile all of these granular policies, at the time of interaction, across tens of thousands of humans and agents"

即同一个权限策略要同时作用于：
- 数据层：能看到哪些对象/属性
- 逻辑层：能调用哪些 Function
- 动作层：能执行哪些 Action、能改哪些对象行
- 应用层：能在 Workshop 里看到哪些 widget

Agent 的权限要么继承人类用户，要么继承 Project 的权限结构。

### 3.2 用户价值路径

- **企业 IT/安全**：能把系统真的交给一线员工（销售只能看自己区域的客户；运营只能改自己工厂的工单）——这是 SaaS 化的前提
- **合规官**：GDPR/等保场景下，能证明"每个访问都有合法目的和记录"——没有这个根本不能上某些行业
- **Agent 治理**：Agent 用人类用户权限执行，不会越权——AI 落地的安全底线
- **多租户/SaaS**：权限是 multi-tenant 隔离的基础

### 3.3 Gaia 现状

- 🟡 `ActionAuthorizer`（ADR-011）实现了 Action 的三层权限（执行/行级写/参数级），但**配置存在 ActionType.parameters.permissions JSON 里，没有独立 ORM 表，principal=anonymous**
- 🟡 Gravitino 自带 RBAC（`check_access`），但只作用于物理资产层，不到 ObjectType/Property 层
- 🔴 **没有 Marking 体系**（无密级标记）
- 🔴 **没有 PBAC**（无 Purpose 概念）
- 🔴 **没有 Object/Property security policy**（无行/列/cell 级对象权限）
- 🔴 **principal 全局 anonymous**（`tools/executor.py` 明确注释 "until Sprint 3"）
- 🔴 **查询层无权限下推**——ObjectQueryService 的 filter 不感知权限，无权限对象可能被查出（靠业务层兜底）

### 3.4 差距本质

权限不是"加个 middleware"的事，而是**贯穿整个技术栈的架构性能力**：
1. **身份层**：需要 Principal 模型（用户/服务账号/Agent）+ 认证
2. **策略层**：需要 Role/Marking/Purpose 三套正交体系 + Granular policy 表达式引擎
3. **执行层**：权限要下推到 Doris 查询（行级过滤）、Property 序列化（列级脱敏）、Action 校验（动作权限）、Function 调用（逻辑权限）
4. **审计层**：每次访问决策要可追溯

Gaia 当前只有 Action 层的雏形，其余全缺。这是 toB 落地的硬阻断——没有权限，企业不敢把真实数据接进来，更不敢让 Agent 操作。

### 3.5 可选路径

1. **第一步（解锁 SaaS）**：引入 Principal（用户/Agent）+ 认证中间件 + ObjectType 级 RBAC（谁能看这个 ObjectType）。成本低，解锁多用户场景
2. **第二步（行级隔离）**：ObjectType 配置 `row_security_policy`（表达式，引用 principal 属性）→ ObjectQueryService 的 filter 自动注入。这是多租户/区域隔离的核心
3. **第三步（列级 + cell 级）**：Property 配置 `property_security_policy` → 序列化层对无权限属性返回 null
4. **第四步（Marking）**：Property 可绑 marking，principal 持有 marking 集合，查询时下推过滤
5. **第五步（PBAC，可选）**：若面向强合规行业，引入 Purpose 概念 + 授权理由记录

> **优先级判断**：权限是 Gaia 从"个人/小团队工具"走向"企业级平台"的门槛。即便只做第一步+第二步，价值也极大（解锁多用户 + 行级隔离）。**P0**（与决策闭环并列，因为缺权限企业根本不敢用）。

---

## 四、P1-A：Scenario 沙箱与 What-if 推演

> **一句话**：Palantir 让用户在不影响生产数据的前提下，"fork"一份本体数据，应用一组 Action + 评估一组 Model，对比多个"如果……会怎样"的方案。这是运营决策的高价值能力。

### 4.1 Palantir 机制深描

**注意区分两个概念**（这是深度研究的关键发现，表层研究常混淆）：

| 概念 | 层次 | 作用 | 对标 |
|------|------|------|------|
| **Scenario** | 本体数据层 | fork 本体对象，应用 Action + Model 评估，对比方案 | "如果给这 3 个客户降价 10%，利润会怎样" |
| **Global Branching** | 环境/平台层 | fork 整个 Foundry 环境（数据管道+本体+应用+权限），端到端隔离开发 | "我要重构数据管道，先在分支上测，不影响生产" |

**Scenario 机制（官方《Workshop → Scenarios → Core concepts》）**

> "A Scenario is fork of the data in the Ontology created by applying a set of Actions and evaluating a set of Models. The fork contains only the edits or changes from the base Ontology including modified Object properties, created Objects, deleted Objects, created link types, and deleted link types."

关键机制：
1. **Scenario 是本体的增量 fork**——只存"相对 base 的 edits"，不是全量复制（copy-on-write 语义）
2. **Scenario 不可变**——创建后不可修改，要改就新建一个（类似 git commit 不可变）。可 duplicate
3. **Scenario = Actions + Models**：用户在 Scenario 里应用一组 Action（改属性/建对象/删对象/建关系）+ 评估一组 Model（Function 包装的预测/优化模型）→ 得到一组对象的新状态
4. **Scenario-aware widget**：Object Table、Chart XY、Metric Card 等 widget 可绑定 Scenario 数组变量，**多 Scenario 并排对比**（只在差异列显示）。聚合/Group By 也尊重 Scenario
5. **限制**：单 Scenario ≤30000 edits、≤50 Actions、加载 ≤10000 对象

**Model 在 Scenario 中的角色**：
- Model = "给定对象属性估算其他属性"的函数（预测/预报/优化）
- 必须包装成 Function，通过 Function-backed Action 在 Scenario 中评估
- Domain = Model 可评估的对象集（必须独立可分，子集评估 = 全集评估）

**Global Branching 机制（官方《Global Branching → Core concepts》）**

> "branching allows you to fork your existing environment and work on components of your end-to-end workflow in a contained branch"

- Git-like：fork main → 在 branch 上改资源（数据集/管道/本体/Workshop 模块）→ rebase（拉 main 更新）→ proposal → review → merge
- 自动解决非冲突变更，真正冲突（同资源同属性两边都改）需手动解决
- Checks：rebase 检查 + 审批检查，全过才能 merge
- **资源级分支**：每种资源（Workshop 模块、数据集、本体）有自己的分支集成

### 4.2 用户价值路径

- **供应链计划员**："如果把这批货改走西线，成本和时效如何？" → 建 3 个 Scenario 对比，选最优再 apply 到生产
- **产能规划**："如果新增 2 条产线，下季度产能如何？" → Scenario 评估产能模型
- **应急响应**："如果部署 A 方案 vs B 方案，影响范围各多少？" → 并排对比
- **AI Agent 决策验证**：Agent 提议一个 Action 前，先在 Scenario 里跑一遍看结果（这是 Agent 安全落地的关键——验证再执行）

### 4.3 Gaia 现状

- 🔴 **完全无 Scenario 机制**——object_state 是单一线性状态，无 fork/branch
- 🔴 **无 Global Branching**——整个系统单 main，无环境级分支
- 🟡 有 Iceberg snapshot（时间旅行），但那是"历史回放"不是"假设推演"
- 🟡 有图推理的 EvidenceChain，但那是"查询证据"不是"假设模拟"

### 4.4 差距本质

Scenario 的技术本质是**对象状态的 copy-on-write 分支 + Action 在分支上重放 + Model 在分支上评估**。Gaia 的 object_state（PG）+ Iceberg（明细）结构可以支持，但需要：
1. object_state 增加分支维度（`branch_id` / `scenario_id`），base 分支 = NULL
2. Action 执行时支持"目标分支"参数，写入指定分支而非 main
3. 查询时支持"在分支 X 上求值"，把分支 edits 叠加到 base
4. 多分支并排对比的查询语义

Global Branching 成本远高于 Scenario（要 fork 整个环境），对 Gaia 当前阶段优先级低。

### 4.5 可选路径

1. **第一步（Scenario MVP）**：object_state 加 `scenario_id` 列；Action 支持 `scenario_id` 入参；ObjectQueryService 支持"base + scenario overlay"求值。先支持单 Scenario（不做并排对比）
2. **第二步（多 Scenario 对比）**：查询返回多 Scenario 的属性值数组；前端 ObjectTable 支持差异列并排
3. **第三步（Model 评估）**：依赖 P3 的 Function/Model 集成，Scenario 可调用 Function-backed Model
4. **Global Branching 暂缓**——成本高、ROI 低，等 Gaia 成熟后再考虑

> **优先级判断**：Scenario 是"决策前验证"的核心，对运营用户价值极高，且技术路径清晰（基于现有 object_state 扩展）。但依赖决策闭环（P0）和权限（P0）先到位。**P1**。

---

## 五、P1-B：运营应用构建器（Workshop / Slate 范式）

> **一句话**：Palantir 让业务人员（非开发者）用拖拽方式搭建"能看能操作"的运营应用，应用直接读写本体。Workshop 是"on rails"的本体应用构建器，Slate 是更自由的拖拽构建器。

### 5.1 Palantir 机制深描

**Workshop 四要素（官方《Workshop → Concepts》）**

| 要素 | 作用 | 类比 |
|------|------|------|
| **Widgets** | UI 组件（Object Table / Chart XY / Metric Card / Button Group / Map / Scenario Manager / Iframe 等） | React 组件库 |
| **Variables** | 模块状态（ObjectSet / Object / String / Number / Filter / Scenario 数组等），驱动 widget | 受控状态 |
| **Events** | 用户操作触发（按钮点击/行选择/Tab 切换），顺序执行 | 事件处理 |
| **Actions** | 嵌入 Action 表单，用户提交数据回写本体 | 表单提交 |

核心架构特点：
- **本体优先**：widget 直接消费 ObjectType/LinkType，不需要"集成数据源"——"You do not integrate data sources. You select object types from a dropdown."
- **Variables 是数据流核心**：变量有依赖图（variable dependency graph），支持派生变量、函数回填变量
- **Module Interface = 模块的 API**：定义模块可被父模块嵌入时映射的变量 + 可从 URL 初始化的变量。模块可嵌套（主模块嵌入子模块）
- **State Saving**：用户可保存当前变量状态，分享给他人（保存的是变量值 + 当前页）
- **Scenario-aware widget**：widget 可绑定 Scenario 数组做多方案并排（见 P1-A）
- **双向 iframe 嵌入**：外部 React 应用可通过 iframe 嵌入 Workshop，双向读写变量（`workshop-iframe-custom-widget`）
- **Global Branching 集成**：Workshop 模块可在分支上开发，rebase/merge

**Workshop vs Slate vs OSDK（官方+社区共识）**

| 工具 | 定位 | 自由度 | 维护性 | 适用 |
|------|------|:---:|:---:|------|
| **Workshop** | 本体优先的应用构建器，"on rails" | 低 | 高 | 运营应用/仪表盘（首选） |
| **Slate** | 拖拽式自由应用构建器 | 高 | 中 | 高度定制 UI/落地页 |
| **OSDK** | 代码生成 SDK，外部开发 | 最高 | 取决于团队 | 完全自定义应用 |
| **Compute Modules** | 容器化函数，任意语言 | — | — | 在 Workshop/Slate 中调用任意代码 |

社区反馈："your first choice should be to reach for workshop"——Workshop 的"on rails"特性让应用可维护、易定制。

### 5.2 用户价值路径

- **运营主管**：自助搭建"今日工单处理台"——表格筛选我的工单、点击按钮分配/转单/关闭、地图看现场位置、Scenario 试排方案。不需要找开发
- **业务分析师**：搭"供应链监控大屏"——多源数据在一个界面、异常自动标红、点击钻取
- **一线员工**：在移动端用 Workshop 应用录入巡检结果、拍照上传、触发工单

**这是 Palantir "operational" 定位的载体**——没有 Workshop，本体只是后端；有了 Workshop，本体变成一线员工每天用的工具。

### 5.3 Gaia 现状

- 🟡 前端有 `OntologyWorkspace` / `GraphExplorePage` / `OperationsDashboard` / `ObjectDetailPanel` / `ActionsOverview` 等页面，但都是**固定的预设页面**，用户不能自定义
- 🟡 有 React Aria 组件库（`components/ui/`）和图探索组件（`GraphCanvas`），但不是"业务人员可拖拽"
- 🔴 **无 Variables/Events 应用框架**——没有"变量驱动 widget"的运行时
- 🔴 **无模块嵌套/嵌入**——页面是扁平的
- 🔴 **无 State Saving**——用户不能保存/分享工作状态

### 5.4 差距本质

Workshop 的本质是一个**本体优先的低代码应用运行时**：
1. 数据层：widget 直接绑定 ObjectSet（不是 SQL/API）
2. 状态层：Variables 框架（变量依赖图 + 派生 + 持久化）
3. 交互层：Events 顺序执行 + Actions 嵌入
4. 组合层：模块嵌套 + iframe 双向 + State Saving

Gaia 当前是"开发者写的固定页面"，不是"业务人员可配置的应用"。这是产品形态的根本差异。

### 5.5 可选路径

1. **第一步（固定模板+变量）**：定义几个运营场景的固定布局（工单台/监控大屏/详情面板），但布局内的 widget 绑定可配置（选 ObjectType + Filter）。这是"半低代码"
2. **第二步（Variables 框架）**：引入 Workshop 式的变量系统（ObjectSet/Object/String 变量 + 依赖图），widget 受控于变量。让交互可配置
3. **第三步（模块嵌套 + State Saving）**：页面可嵌套、状态可保存分享
4. **全自由拖拽（Slate 式）暂缓**——成本极高，ROI 不如 Workshop 式 on rails

> **优先级判断**：Workshop 是用户日常触达最频繁的载体（每天用），但 Gaia 当前阶段"半低代码固定模板"已能覆盖核心场景。完整 Workshop 是长期目标。**P1**。

---

## 六、P1-C：共享逻辑层（Foundry Functions + AIP Logic）

> **一句话**：Palantir 把"业务逻辑"从应用/Action/管道中抽离出来，变成独立的、版本化的、可被任何消费者调用的 Function 资产。Gaia 当前逻辑散落在各处，无法复用。

### 6.1 Palantir 机制深描

**Foundry Functions（官方《Functions → Overview》）**

> "Functions enable code authors to write logic that can be executed quickly in operational contexts, such as dashboards and applications... first-class support for authoring logic based on the Ontology. This includes support for reading the properties of various object types, traversing links, and flexibly making Ontology edits."

- **语言**：TypeScript（v1/v2）、Python
- **运行时**：服务端隔离环境执行
- **本体优先**：Function 可直接读对象属性、遍历关系、做 Ontology 编辑
- ** centrally-managed, reusable**：一处定义、处处可用

**Function 的消费场景（官方列表）**：
- Workshop：返回 ObjectSet/变量值、function-backed 表格列、chart 聚合
- Function-backed Action：表达复杂的多对象编辑
- Slate：后端逻辑返回前端
- Quiver：自定义指标/聚合
- External Functions：查外部系统丰富本体对象
- Pipeline Builder：Python 函数作为 sidecar

**AIP Logic（官方《AIP Logic → Overview》）**

> "AIP Logic is a no-code development environment for building, testing, and releasing functions powered by LLMs."

- AIP Logic 是**生成 LLM 驱动 Function 的无代码环境**
- 和 Foundry Functions 的关系：**逻辑层的两半**——Foundry Functions 是代码定义的逻辑，AIP Logic 是 LLM 驱动的逻辑。两者都产出"可被调用的 Function"
- AIP Logic 的 Call function tool 让 LLM 调用任意 Function（代码定义的或 Logic 定义的）

**Compute Modules（容器化函数）**

- 把任意语言代码作为 serverless Docker 镜像运行，水平扩展
- Function 执行模式：注册函数，API name 作为 locator（`com.<...>.computemodules.<name>`）
- 可在 Workshop/Slate 中调用

**逻辑层的整体定位（第三方分析 Valliance）**：
> "Business rules: ... held in ... AIP Logic and Foundry Functions (no-code LLM-driven and code-based logic over the Ontology, respectively) at the Ontology layer. All are versioned in the platform and monitored at scale through Data Health."

即：逻辑层是 Ontology 的独立一层，版本化、可监控、可被所有消费者复用。

### 6.2 用户价值路径

- **业务规则一处定义**：库存预警阈值规则写一次，Workshop 仪表盘、Action 校验、Agent tool、管道计算都能用，改一处全生效
- **复杂 Action 逻辑**：Function-backed Action 表达"修改 A 同时根据 A 的关系链更新 B/C/D"这类复杂编辑
- **LLM + 确定性逻辑混合**：AIP Logic 让 LLM 调用确定性 Function（预测/优化/校验），弥补 LLM 不擅长计算的问题（官方《Logic Tools for RAG/OAG》）
- **模型推理即 Function**：ML 模型包装成 Function，在任何场景调用推理

### 6.3 Gaia 现状

- 🟡 Action 有声明式规则（`ActionRuleEngine` + `ActionValidator`）+ Expression ValueSource，但**规则绑定在具体 ActionType 上，不是独立可复用的 Function 资产**
- 🟡 AI Agent 的 22 工具是逻辑的一种形式，但**只对 Agent 暴露，不对应用/管道暴露**
- 🟡 有 `ai_generate.py` 的 LLM 原语，但**不是可编排的 Function 资产**
- 🔴 **无独立 Function 资产**——逻辑不能被 Workshop/Action/Agent/管道共享调用
- 🔴 **无 AIP Logic 式的无代码 LLM 函数构建器**
- 🔴 **无 Compute Module 式的容器化函数**

### 6.4 差距本质

Gaia 的逻辑是"嵌入式"的（嵌在 Action 规则里、Agent toolset 里），Palantir 的逻辑是"资产化"的（独立 Function，多消费者）。差异：
1. **资产化**：Function 是版本化的可发现资源（Ontology Manager 有 Functions tab），不是散落代码
2. **多消费者**：同一 Function 被 Workshop/Action/Agent/管道调用
3. **类型契约**：Function 有强类型输入输出，消费者按契约调用
4. **LLM 友好**：AIP Logic 让 LLM 通过 tool calling 编排确定性逻辑

### 6.5 可选路径

1. **第一步（Function 资产化）**：定义 `FunctionType`（API name + 强类型签名 + 实现：Python 代码 or 声明式规则 or LLM）。OntologyManager 增 Function 管理
2. **第二步（多消费者）**：Action 的 Expression ValueSource 可引用 Function；Agent toolset 增 `call_function` 工具；查询层支持 function-backed 派生属性
3. **第三步（AIP Logic 式构建器）**：无代码编排 LLM + Function + Ontology 数据，产出新的 LLM Function
4. **Compute Module（容器化）可选**——让用户跑任意语言代码

> **优先级判断**：Function 资产化是"逻辑复用"的基础，对开发者体验和一致性价值大。但 Gaia 当前 Action 规则已能覆盖部分场景，紧迫性低于 P0。**P1**。

---

## 七、P2-A：OSDK（本体 SDK 代码生成）

> **一句话**：Palantir 从本体自动生成 TypeScript/Python/Java 强类型 SDK，开发者把 Foundry 当后端，像用 ORM 一样用本体。Gaia 有 API 但无 SDK 生成。

### 7.1 Palantir 机制深描

**OSDK（官方《Ontology SDK → Overview》）**

> "The Ontology Software Development Kit (OSDK) allows you to access the full power of the Ontology directly from your development environment."

- **代码生成**：从本体 Schema 自动生成 ObjectType/ActionType/Function 的强类型客户端代码
- **多语言**：TypeScript（NPM）、Python（pip/conda）、Java（Maven）、任意语言（OpenAPI spec）
- **本体优先**："Ontology-first, not table-first"——开发者不写 SQL，操作业务对象
- **@osdk/react**：React hooks（`useOsdkObjects`/`useOsdkObject`/`useLinks`/`useOsdkAction`/`useObjectSet`），含乐观更新、缓存管理
- **实时订阅**：`.subscribe()` 通过 WebSocket 流式推送对象变更
- **Ontology as Code（OAC）**：YAML 定义本体，可代码化（`@osdk/maker`）

**OSDK 的核心价值（官方博客《Building with AIP: the OSDK》）**：
> "harnessing the full power of the Palantir Ontology directly from your development environment. With the OSDK, you can quickly and seamlessly integrate the data, logic, and actions that define your business — along with LLMs — into existing applications, create net new applications, integrate with back office systems"

### 7.2 用户价值路径

- **外部应用开发**：开发者用熟悉的语言和 IDE 构建应用，后端是本体，不需要学 Foundry 特有 API
- **集成后端系统**：OSDK 让本体数据/动作集成进现有企业系统
- **类型安全**：本体改了，SDK 重新生成，编译时发现不兼容
- **AI 应用**：OSDK + LLM 构建 AI 应用，本体作为后端

### 7.3 Gaia 现状

- ✅ 有 MCP（19 工具）/ AG-UI / REST 三入口
- 🔴 **无代码生成 SDK**——外部开发者要手写 API 调用
- 🔴 **无 React hooks**——前端开发者不能用 `useObjects` 式 hook
- 🔴 **无 OpenAPI spec 自动生成**

### 7.4 差距本质

OSDK 是"开发者体验"层的差距。Gaia 的 API 已完备，缺的是"让外部开发者好用"的封装层。技术路径清晰：从 ObjectType/ActionType Schema 生成代码。

### 7.5 可选路径

1. **第一步（OpenAPI spec 生成）**：从本体 Schema 自动生成 OpenAPI spec，任意语言可生成客户端
2. **第二步（Python/TypeScript SDK 生成器）**：生成强类型 ObjectType/ActionType 客户端类
3. **第三步（React hooks 库）**：`@gaia/react` 提供 `useObjects`/`useObject`/`useAction` hooks
4. **第四步（实时订阅）**：依赖 P2-B 的 WebSocket

> **优先级判断**：OSDK 对"把 Gaia 推给外部开发者"价值大，但 Gaia 当前主要用户是内置前端 + Agent，外部开发场景未成主流。**P2**。

---

## 八、P2-B：全链路动态血缘

> **一句话**：Palantir 的血缘贯穿 data↔logic↔action↔application 四层，改一处知道影响范围，删除知道扩散到哪（Lineage-Aware Deletion）。Gaia 只有物理血缘。

### 8.1 Palantir 机制深描

**血缘的范围（官方《Data Lineage》）**

Data Lineage 是交互式工具，展示数据如何在 Foundry 流动。但关键是血缘**不止于数据管道**：

Agentic Runtime 博客明确：
> "dynamic lineage that flows across data, logic, action, and application artifacts"

即一个 ObjectType 的变更，血缘能追溯到：
- 数据层：哪个 dataset/管道/源系统产出它
- 逻辑层：哪些 Function 依赖它
- 动作层：哪些 Action 修改它
- 应用层：哪些 Workshop 模块/Slate 应用消费它

**Lineage-Aware Deletion（官方白皮书）**

> "A dependable deletion solution must find instances of sensitive data across multiple transformations and combinations of the data."

GDPR"被遗忘权"要求删除某人的数据时，必须找到这些数据经过所有变换/组合后产生的派生数据一并删除。Palantir 通过血缘追踪实现：删一个源数据 → 沿血缘图找到所有派生 → 重新计算/删除派生。

**Data Lifetime**：在 namespace 级定义"血缘感知"的保留策略，给 dataset 的所有 transaction 分配删除日期（固定日期 / 仅最新视图）。

**血缘与安全交织**（白皮书）：
> "05. LINEAGE: Interwoven with security paradigm; provides immutable tracking; Allows for impact analysis"

血缘不可变（审计用），与安全策略交织（影响分析）。

### 8.2 用户价值路径

- **数据工程师**：改一个管道字段，立刻知道下游哪些本体/应用/Action 受影响（影响分析）
- **合规官**：删除某客户数据时，自动找到所有派生数据（聚合/JOIN 结果）一并清理，满足 GDPR
- **审计**：任何业务状态可追溯到源数据和变换逻辑的版本
- **Agent 可观测**：Agent 的每个动作可追溯到用了哪些数据版本（Agentic Runtime）

### 8.3 Gaia 现状

- 🟡 Gravitino 自带物理资产血缘（dataset ↔ pipeline）
- 🟡 `AnalysisRecordStore` 有证据链（查询 → ObjectSet IR → 结果），但是查询级的，不是数据级的
- 🔴 **无跨层血缘**——ObjectType 变更不追溯到管道/源系统
- 🔴 **无 Lineage-Aware Deletion**——删除对象不清理派生
- 🔴 **无 Data Lifetime**——无保留策略

### 8.4 差距本质

血缘是"治理"的基础设施。Gaia 当前是"知道有哪些资源"（Gravitino catalog），但不知道"资源间如何相互依赖"。跨层血缘需要：
1. 元数据层记录"谁依赖谁"（ObjectType ↔ dataset ↔ pipeline ↔ Function ↔ Action ↔ Workshop 模块）
2. 变更事件传播 + 影响图计算
3. 删除时沿图回溯

### 8.5 可选路径

1. **第一步（数据↔本体血缘）**：记录 ObjectType.property ↔ dataset.column ↔ pipeline 的映射（已有 physical_mapping，扩展为血缘图）
2. **第二步（逻辑↔动作↔应用血缘）**：Function/Action/前端页面注册"我依赖哪些 ObjectType"
3. **第三步（影响分析）**：变更 ObjectType 时查询影响图，提示下游
4. **第四步（Lineage-Aware Deletion）**：删除对象时沿血缘清理派生

> **优先级判断**：血缘是治理基础设施，企业规模化后必需，但 Gaia 当前规模未到痛点。**P2**。

---

## 九、P2-C：实时订阅（Ontology 变更推送）

> **一句话**：Palantir 通过 WebSocket 把本体对象变更实时推给客户端，应用看到的数据是"活的"。Gaia 只有轮询/CDC。

### 9.1 Palantir 机制深描

**OSDK 实时订阅（官方《Subscribe to changes via WebSocket》）**

> "The TypeScript OSDK's .subscribe method uses the WebSocket protocol to stream object updates to the client."

- 客户端订阅一个 ObjectSet，对象创建/编辑/删除时实时推送
- Object Storage V2：编辑立即可见（V1 是最终一致）
- 应用场景：运营大屏实时刷新、多用户协作看到彼此编辑、异常实时告警

**Streaming（官方白皮书）**

> "automated state monitoring... automatically generate alerts and escalate them to human operators or algorithms for analysis and action in near real-time"

流式数据进本体 → 订阅推送 → 实时告警/动作。

### 9.2 用户价值路径

- **运营大屏**：工单状态变了立刻刷新，不用 F5
- **协作**：多人编辑同一对象，看到彼此变更
- **实时告警**：异常指标超阈值立刻推送
- **Agent 实时反应**：Agent 订阅事件，异常发生时自动响应

### 9.3 Gaia 现状

- 🔴 **无 WebSocket/SSE 推送**——前端轮询或手动刷新
- 🟡 有 CDC（SeaTunnel pg_to_kafka），但那是后端同步，不到前端
- 🟡 AG-UI 是 SSE 流式（Agent 响应），但不是数据变更推送

### 9.4 差距本质

实时订阅需要：对象变更事件总线（PG notify / Kafka）+ WebSocket 网关 + 客户端订阅协议。Gaia 的 outbox + CDC 已有事件源基础，可扩展到前端推送。

### 9.5 可选路径

1. **第一步（PG LISTEN/NOTIFY → SSE）**：object_state 变更触发 PG notify，SSE 网关转发给订阅客户端
2. **第二步（WebSocket + ObjectSet 订阅协议）**：客户端订阅 ObjectSet，服务端过滤推送
3. **第三步（Kafka 事件总线）**：大规模下用 Kafka 做事件总线

> **优先级判断**：实时性对运营场景价值大，但 Gaia 当前轮询能兜底。**P2**。

---

## 十、P3 能力群（优先级较低，简述）

### 10.1 MLOps / Model 集成（P3）

**Palantir 机制**：Modeling Objective 管理模型全生命周期（训练/评估/部署/治理）。部署模式：live deployment（实时推理 endpoint）、batch deployment（批推理写回本体）、streaming（流式推理）。模型包装成 Function 在任何场景调用。模型推理历史可追溯。Feature 与本体绑定。

**Gaia 现状**：仅有 LLM 原语（`ai_generate.py`），无传统 ML 训练/部署/特征存储。

**差距本质**：Gaia 定位是"本体 + AI Agent"，传统 ML 不是核心。但 Scenario 的 Model 评估（P1-A）需要 Function-backed Model，因此**轻量 Model 注册（包装外部模型为 Function）**有价值。

**可选路径**：定义 `ModelFunction`（包装外部模型 API 为 Function），不建完整 MLOps。**P3**。

### 10.2 AIP Evals（LLM 函数评估套件）（P3）

**Palantir 机制**：AIP Evals 是测试环境，针对 AIP Logic/Chatbot/代码 Function 创建评估套件（test cases + evaluation functions）。特点：
- 处理 LLM 非确定性
- 可从决策历史（Decision Exhaust，见 P0-A）生成 test case（"这个真实决策，Agent 是否能正确处理"）
- 评估函数可对比历史版本
- Evals suite 自动跟踪到 Agentic Runtime 的 trace

**Gaia 现状**：无 eval harness，Agent 改动靠人工验证 + 单元测试（确定性部分）。

**差距本质**：LLM 驱动的逻辑需要专门的评估基础设施。Gaia 的 TextQL/Agent 都依赖 LLM，缺 eval 是质量隐患。

**可选路径**：建 `evals/` 目录 + eval runner（golden cases + LLM-as-judge）+ CI 集成。**P3**（但随 Agent 复杂度增长会升优先级）。

### 10.3 Global Branching（环境级 Git fork）（P3）

**Palantir 机制**：fork 整个 Foundry 环境，端到端隔离开发，rebase/merge/proposal。资源级分支集成。

**Gaia 现状**：无分支概念。

**差距本质**：环境级分支成本极高（要 fork 所有层），对 Gaia 当前小团队阶段 ROI 低。Scenario（P1-A）已覆盖"数据假设推演"的核心价值。

**可选路径**：暂缓。若未来需要"大改动隔离测试"，可考虑 Alembic migration 分支 + 本体 schema 版本化。**P3**。

### 10.4 Apollo（边缘/多环境持续部署）（P3，对 Gaia 不完全适用）

**Palantir 机制**：Apollo 是"软件部署的操作系统"，把同一套软件持续部署到云/边/气隙/分类网络。Single source of truth 管理整个 fleet。

**Gaia 现状**：Gaia 定位开源本地优先（k3s/colima），不需要 Palantir 式的全球 fleet 管理。

**差距本质**：Apollo 是 Palantir 商业模式的产物（多客户多环境 SaaS），Gaia 开源定位不需要。

**可选路径**：降级为"容器编排 + Helm chart + 多环境配置"，不建独立 Apollo。**P3 / 不适用**。

---

## 十一、关键洞察汇总（便于后续参考）

### 洞察 1：Palantir 的护城河不在感知层，在闭环与治理

Gaia 已对齐 Palantir 的感知层（22 工具 + 图推理 + ObjectSet IR，对齐 Palantir 范式 87%）。但 Palantir 真正的差异化在：
- **决策闭环**（Decision Exhaust，P0-A）：让决策可积累、可对照结果、可学习
- **治理体系**（权限 + 血缘，P0-B + P2-B）：让企业敢用、敢让 Agent 操作
- **反馈飞轮**：决策→执行→结果→改进

感知层是"能看到"，闭环与治理是"能落地、能改进"。**Gaia 下阶段重心应从感知层转向闭环与治理**。

### 洞察 2："Operational"与"Analytical"的分水岭是 Writeback + 决策捕获

Palantir 反复强调 operational ≠ analytical：
- Analytical：止步于"洞察"（BI 报表）
- Operational：走到"执行 + 反馈"（Action + Writeback + 决策回流）

Gaia 当前偏向 analytical（查询 + 图推理），离 operational 还差：决策物化（P0-A）、Writeback webhook 事务性（P0-A）、Scenario 验证（P1-A）、运营应用（P1-B）。

### 洞察 3：逻辑层资产化是复用的基础

Palantir 的 Function 是独立资产（versioned + discoverable + multi-consumer），Gaia 的逻辑是嵌入式（Action 规则 + Agent toolset）。这个差异导致：
- Palantir：业务规则改一处全平台生效
- Gaia：同一规则可能在 Action、Agent、前端各写一遍

Function 资产化（P1-C）是 Gaia 逻辑复用的基础设施。

### 洞察 4：Scenario ≠ Global Branching（常被混淆）

- Scenario = 本体数据 fork（运营决策推演，P1-A，高价值）
- Global Branching = 环境 fork（开发隔离，P3，低 ROI）

Gaia 应优先做 Scenario（基于 object_state 扩展，技术路径清晰），暂缓 Global Branching。

### 洞察 5：权限是 toB 落地的硬门槛

principal=anonymous 意味着 Gaia 现在只能给个人/小团队用。企业落地（多用户、行级隔离、Agent 越权防护）必需权限体系（P0-B）。即便只做 Principal + RBAC + 行级 policy，也能解锁大量场景。

### 洞察 6：Agentic Runtime = 安全 + 可观测 + 血缘的统一

Palantir 的 Agentic Runtime 不是"Agent 运行时"那么简单，而是：
- 安全：marking/purpose/role 混合策略贯穿 Agent 所有动作
- 可观测：data-to-decision 分布式 trace，每次 LLM 调用/Function 调用/变换都可追溯
- 血缘：每次查询绑定数据版本，每次 Function 调用绑定语义版本，Evals suite 自动跟踪

这对 Gaia 的启示：Agent 的可观测性（trace_id 已有雏形）需要升级到"决策可追溯"级别，与决策捕获（P0-A）、血缘（P2-B）、Evals（P3）联动。

### 洞察 7：Workshop 的"on rails"哲学值得借鉴

Workshop 比 Slate 更受推荐，因为"on rails"（轨道式）：widget 直接绑定 ObjectType，不让用户碰底层。这降低了构建难度、提升了可维护性。

对 Gaia 的启示：运营应用构建器（P1-B）应优先做 Workshop 式的"本体优先 + 固定 widget + 变量驱动"，而不是 Slate 式的全自由拖拽。

### 洞察 8：PBAC 是 Palantir 的隐私工程独创

Purpose-Based Access Control 把 GDPR 的"目的限制""最小化"原则工程化：访问需申请 Purpose、记录 rationale、可回溯。这是 Palantir 在政府/医疗/金融场景的杀手锏。

Gaia 若面向强合规行业，PBAC 是差异化能力。但成本高，建议作为权限体系（P0-B）的第五步可选。

---

## 十二、推荐的实施路线图

按"价值优先级 × 依赖关系 × 成本"排序的渐进路线：

### 阶段 1：决策闭环与权限基石（解锁企业落地）

| 序号 | 能力 | 依赖 | 价值 |
|:---:|------|------|------|
| 1.1 | Principal + 认证中间件 + ObjectType RBAC | 无 | 解锁多用户 |
| 1.2 | ObjectType 行级 security policy（表达式 + 查询下推） | 1.1 | 多租户/区域隔离 |
| 1.3 | 决策物化为对象（ActionExecutionLog → 可选 Decision 对象） | 无 | 决策进入本体可查询 |
| 1.4 | execution_log 增加数据版本锚定 | 1.3 | 决策可回放 |
| 1.5 | Writeback webhook（writeback/side effect 双模式） | 1.3 | 决策落地外部系统 |

### 阶段 2：逻辑资产化与推演（提升复用与决策质量）

| 序号 | 能力 | 依赖 | 价值 |
|:---:|------|------|------|
| 2.1 | Function 资产化（FunctionType + 强类型签名） | 无 | 逻辑一处定义处处可用 |
| 2.2 | Function 多消费者（Action Expression / Agent tool / 派生属性） | 2.1 | 复用落地 |
| 2.3 | Scenario MVP（object_state 分支 + Action 重放） | 1.3 | 决策前推演 |
| 2.4 | 多 Scenario 并排对比 | 2.3 | 多方案选优 |
| 2.5 | Property 列级 security policy | 1.2 | cell 级安全 |

### 阶段 3：运营载体与开发者生态（扩大触达）

| 序号 | 能力 | 依赖 | 价值 |
|:---:|------|------|------|
| 3.1 | Workshop 半低代码（固定模板 + 可配置 widget 绑定） | 2.1 | 业务人员自助搭应用 |
| 3.2 | Variables 框架（变量依赖图 + 派生） | 3.1 | 交互可配置 |
| 3.3 | OpenAPI spec 自动生成 | 无 | 任意语言客户端 |
| 3.4 | Python/TypeScript OSDK 生成器 | 3.3 | 外部开发者体验 |
| 3.5 | 实时订阅（PG notify → SSE → WebSocket） | 1.3 | 运营实时性 |

### 阶段 4：治理深化与 AI 质量保障（规模化）

| 序号 | 能力 | 依赖 | 价值 |
|:---:|------|------|------|
| 4.1 | 跨层血缘（data↔logic↔action↔application） | 2.1 | 影响分析 |
| 4.2 | Lineage-Aware Deletion | 4.1 | GDPR 合规 |
| 4.3 | Marking 体系 | 1.2 | 密级控制 |
| 4.4 | AIP Evals（golden cases + LLM-as-judge） | 1.3 | AI 质量回归 |
| 4.5 | 决策结果回流（DecisionOutcome + 对照任务） | 1.3, 2.1 | 反馈飞轮 |
| 4.6 | AIP Logic 式 LLM 函数构建器 | 2.2 | 无代码 AI 逻辑 |
| 4.7 | ModelFunction（外部模型包装） | 2.1 | Scenario Model 评估 |
| 4.8 | PBAC（Purpose + rationale，面向合规行业） | 1.2 | 隐私工程 |

---

## 十三、与 Gaia 现有架构的契合度评估

| 能力 | 与 Gaia 架构契合度 | 备注 |
|------|:---:|------|
| 决策捕获（P0-A） | ✅ 高 | 已有 execution_log + outbox + object_state，扩展即可 |
| 权限（P0-B） | 🟡 中 | ActionAuthorizer 有雏形，但查询层下推需改 ObjectQueryService |
| Scenario（P1-A） | ✅ 高 | object_state 加 scenario_id，技术路径清晰 |
| Workshop（P1-B） | 🟡 中 | 前端已有组件库，缺 Variables 运行时 |
| Functions（P1-C） | 🟡 中 | Action 规则可抽离，但需要新的 FunctionType 模型 |
| OSDK（P2-A） | ✅ 高 | Schema 已完备，代码生成是纯增量 |
| 血缘（P2-B） | 🟡 中 | physical_mapping 已有，跨层扩展需元数据增强 |
| 实时订阅（P2-C） | ✅ 高 | outbox + PG notify，技术成熟 |
| MLOps（P3） | 🔴 低 | 需新建训练/部署基础设施，偏离核心 |
| AIP Evals（P3） | ✅ 高 | 纯增量，不破坏现有架构 |
| Global Branching（P3） | 🔴 低 | 需 fork 整个环境，成本极高 |
| Apollo（P3） | ⚫ 不适用 | 与 Gaia 开源定位不符 |

> **结论**：Gaia 现有架构（object_state + outbox + Action + 图推理 + 多入口）对决策闭环、Scenario、OSDK、实时订阅、Evals 的支撑度高，这些应优先。权限、Workshop、Functions、血缘需要中等改造。MLOps、Global Branching、Apollo 契合度低，暂缓或不做。

---

## 十四、参考来源索引

### Palantir 官方文档（第一手）
- The Ontology system: https://palantir.com/docs/foundry/architecture-center/ontology-system/
- AIP overview: https://palantir.com/docs/foundry/aip/overview/
- AIP Logic: https://palantir.com/docs/foundry/logic/overview/
- AIP Evals: https://palantir.com/docs/foundry/aip-evals/overview/
- Functions: https://palantir.com/docs/foundry/functions/overview/
- Workshop: https://palantir.com/docs/foundry/workshop/overview/
- Workshop Variables: https://palantir.com/docs/foundry/workshop/concepts-variables/
- Workshop Events: https://palantir.com/docs/foundry/workshop/concepts-events/
- Workshop Scenarios: https://palantir.com/docs/foundry/workshop/scenarios-overview/
- Workshop Scenarios concepts: https://palantir.com/docs/foundry/workshop/scenarios-concepts/
- Workshop Scenarios getting started: https://palantir.com/docs/foundry/workshop/scenarios-getting-started/
- Scenario Manager widget: https://palantir.com/docs/foundry/workshop/widgets-scenario-manager/
- Global Branching: https://palantir.com/docs/foundry/global-branching/core-concepts/
- Object permissioning: https://palantir.com/docs/foundry/object-permissioning/managing-object-security/
- Object security policies: https://palantir.com/docs/foundry/object-permissioning/object-security-policies/
- Restricted views: https://palantir.com/docs/foundry/security/restricted-views/
- CBAC: https://palantir.com/docs/foundry/security/classification-based-access-controls/
- Action types Webhooks: https://palantir.com/docs/foundry/action-types/webhooks/
- Operational apps: https://palantir.com/docs/foundry/app-building/operational-apps/
- OSDK: https://palantir.com/docs/foundry/ontology-sdk/overview/
- OSDK WebSocket subscriptions: https://palantir.com/docs/foundry/ontology-sdk/websocket-subscriptions/
- Model integration: https://palantir.com/docs/foundry/model-integration/models/
- Data Lineage: https://palantir.com/docs/foundry/data-lineage/overview/
- Data Lifetime: https://palantir.com/docs/foundry/data-lifetime/core-concepts-data-lifetime/
- Pipeline Builder: https://palantir.com/docs/foundry/pipeline-builder/overview/
- Streaming: https://palantir.com/docs/foundry/building-pipelines/streaming-overview/
- Slate: https://palantir.com/docs/foundry/slate/overview/
- AIP Chatbot Studio: https://palantir.com/docs/foundry/chatbot-studio/overview/
- Compute modules: https://palantir.com/docs/foundry/compute-modules/functions/

### Palantir 官方博客（第一手）
- Connecting AI to Decisions with the Ontology: https://blog.palantir.com/connecting-ai-to-decisions-with-the-palantir-ontology-c73f7b0a1a72
- Connecting Agents to Decisions: https://blog.palantir.com/connecting-agents-to-decisions-277dee8ddb40
- Purpose-based access controls: https://blog.palantir.com/purpose-based-access-controls-at-palantir-f419faa400b3
- Securing Agents in Production (Agentic Runtime): https://blog.palantir.com/securing-agents-in-production-agentic-runtime-1-5191a0715240
- Taking Data Science Models to the Next Level: https://blog.palantir.com/taking-your-data-science-models-to-the-next-level-149d9c4269ec
- How Ontology Deploys Data Science to the Front Line: https://blog.palantir.com/how-palantir-foundrys-ontology-deploys-data-science-to-the-front-line-7a9679bdfd01
- Engineering Responsible AI (AIP Evals): https://blog.palantir.com/from-prototype-to-production-engineering-responsible-ai-3-ea18818cd222
- Designing for deletion: https://blog.palantir.com/designing-for-deletion-palantir-explained-6-adfe25fda810
- On dataset versioning: https://blog.palantir.com/on-dataset-versioning-in-palantir-foundry-8f23de22cc4c
- Building with AIP: the OSDK: https://blog.palantir.com/building-with-palantir-aip-the-ontology-software-development-kit-823fe5ac7aae
- Building with AIP: Logic Tools for RAG/OAG: https://blog.palantir.com/building-with-palantir-aip-logic-tools-for-rag-oag-fdaf8938d02e
- Apollo: https://blog.palantir.com/palantir-apollo-powering-saas-where-no-saas-has-gone-before-7be3e565c379

### Palantir 官方白皮书（第一手）
- Foundry Technical Overview v4: https://www.palantir.com/assets/.../FfB_Technical_Overview_v4.pdf
- Foundry 2022 Whitepaper: https://www.palantir.com/assets/.../Whitepaper_-_Foundry_2022.pdf
- Connecting AI and ML with Operations v4: https://www.palantir.com/assets/.../Connecting_AI___Machine_Learning_with_Operations.pdf
- Granular Lineage-Aware Deletion: https://www.palantir.com/assets/.../Palantir-lineage-aware-data-deletion_whitepaper.pdf
- Foundry Streaming: https://www.palantir.com/assets/.../Foundry_Streaming_White_Paper_vF.pdf
- Apollo for the Edge: https://www.palantir.com/assets/.../ApolloForEdge.pdf
- Privacy and Governance: https://www.palantir.com/assets/.../Palantir_Privacy_and_Governance_Whitepaper.pdf

### Gaia 内部对照文档
- 实现状态：`docs/architecture/implementation-status.md`
- 工具层范式：`docs/reference.md`（22 工具对齐）
- 本体建模规范：`docs/reference-palantir-ontology.md`
- 图推理范式：`docs/reference-graph-reasoning.md`
- Action 架构：`docs/architecture/action-architecture.md`
- ADR-011 Action P1（含权限雏形）：`docs/architecture/adr-011-action-p1.md`
