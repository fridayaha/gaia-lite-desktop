# 统一管理 vs 分散治理分析

> 文档编号：validation/02
> 版本：v0.1
> 日期：2026-05-15
> 用途：论证 Agent / Skill / Tool / MCP 四类能力资产是否应在 Hub 中统一管理

---

## 1. 什么是统一管理

统一管理是指：Agent / Skill / Tool / MCP 四类能力资产在 Hub 中**共享同一套治理体系**。

具体表现为：

| 共享层 | 内容 |
|--------|------|
| **数据模型** | 共用 HubItem（主表）+ HubItemVersion（版本表），通过 `type` 字段区分资产类型 |
| **生命周期** | 统一的状态机：draft → pending_review → published → disabled → archived |
| **审批流程** | 统一的 approve / reject / request_change + blocking 风险拦截 |
| **版本管理** | 统一的语义化版本号 + 多版本并存 + 回滚策略 |
| **安全扫描** | 统一的规则引擎 + ScanReport/ScanFinding 模型 |
| **搜索发现** | 统一的能力列表、关键字搜索、类型/状态/风险筛选 |
| **导入导出** | 统一的 manifest / yaml / zip 导入管道 |

**核心思路**：治理逻辑一次实现，四类资产复用；资产差异通过类型化字段（manifest_json、config_json、permission_json、runtime_compatibility）承载。

---

## 2. 什么是不合理的强行统一

统一管理不是无差别的抹平。以下情况属于**不合理的强行统一**，应当避免：

| 反模式 | 说明 | 后果 |
|--------|------|------|
| **用同一个 manifest schema 校验所有类型** | Agent 的行业/场景、MCP 的连接配置、Skill 的任务定义、Tool 的参数 Schema 差异巨大，共用 schema 会导致字段既不完整又不精确 | 校验形同虚设，良品和劣品无法区分 |
| **用同一个 Validator 校验所有类型** | 各类资产需要不同类型的业务规则，如 Skill 需要校验任务定义，MCP 需要校验连接协议 | 审批无法发现类型特有的问题 |
| **共用同一个生命周期状态机但不允许类型差异** | 各类资产可能存在不同的发布节奏和治理策略，一刀切的状态机可能过于僵化或不适用于特定资产 | 流程不适配导致操作混乱 |
| **在 Hub 中统一执行各类能力** | Hub 管理的是资产定义，不应执行 Agent/Skill/Tool/MCP | 职责越界，整体架构失稳 |
| **用同一张表存所有类型数据而不利用 JSON 字段的灵活性** | 将所有类型特有字段做成枚举列而非 JSON，导致表结构膨胀 | 表结构难以演进，新增类型需 DDL |

**判断标准**：如果统一导致某个类型的核心需求被弱化、某个类型被强加不需要的约束、或者治理流程对某类型完全不适用，则为不合理统一。

---

## 3. 统一管理的优点

| # | 优点 | 对 Hub 的价值 |
|---|------|-------------|
| 1 | **治理逻辑零重复** | 生命周期、审批、回滚、下架对四类资产完全一致。统一实现=避免 4 套代码维护 |
| 2 | **搜索发现一致性** | 用户和 Agent 在同一界面、同一 API 搜索所有类型能力，无需跨多个 Registry |
| 3 | **跨类型关系建模** | Agent uses Skill、Agent depends_on MCP、Skill invokes Tool — 统一模型使跨类型关系天然可表达 |
| 4 | **统一安全准入** | RiskLevel(low/medium/high/blocking) 和 blocking 禁止发布规则在四类资产上一视同仁 |
| 5 | **审批流程统一** | 审核/驳回/要求修改的流程不因资产类型而不同，审批人学习成本降低 |
| 6 | **版本与回滚统一** | 语义化版本号 + 多版本并存 + 回滚到历史版本在四类资产上完全一致 |
| 7 | **审计一致性** | ApprovalRecord、LifecycleEvent 面向统一的 HubItem 模型，跨类型审计无死角 |
| 8 | **开发和维护成本** | 新增一种资产类型只需增加 type 枚举值 + 类型化 manifest schema，不需要新建整套 CRUD/审批/版本服务 |
| 9 | **AGENTS.md 约束** | PoC 阶段明确要求不拆分四套主表，避免 CRUD/审批/搜索/生命周期在各类型间重复实现 |
| 10 | **开闭原则** | 对扩展开放（新增 type + 类型化 manifest）、对修改封闭（治理层不需要改动） |

---

## 4. 统一管理的缺点

| # | 缺点 | 影响 | 缓解措施 |
|---|------|------|----------|
| 1 | **HubItem 表承载所有类型** | 表的 JSON 字段缺少结构约束（manifest_json 对 Agent 和 MCP 内容完全不同，但数据库无强制类型约束） | Manifest Spec v0.1 定义每种类型的 manifest schema；Service 层按 type 执行类型化校验 |
| 2 | **类型特有字段难以用关系型表达** | Skill 的 task_definition、MCP 的 transport_config 等高度结构化数据全存在 JSON 列中，无法做 SQL 级查询过滤 | 在应用层缓存常见查询字段；准生产阶段按需建立类型扩展表 |
| 3 | **审批人面对不同类型的审批内容** | Agent 的审批关注点（行业/场景）与 MCP（连接/协议安全）差异大，审批人难以统一判断 | 审批时按类型展示不同的清单项；安全审核人专项审查 |
| 4 | **安全扫描规则需要类型化** | Prompt 风险对 Agent 和 Skill 有意义，对纯 Tool 无意义；MCP 需检测连接风险 | 扫描规则分通用规则和类型专项规则（已规划阶段 6） |
| 5 | **索引膨胀** | 所有类型共用同一表可能导致索引覆盖不均匀 | PostgreSQL 支持 partial index，可按 type 分别建索引 |
| 6 | **类型爆炸时 HubItem 可能成为瓶颈** | 若未来支持 20+ 类型，单表通用模型可能不够灵活 | 当前仅 4 种类型，PoC 到准生产阶段无需过早优化 |

**结论**：统一管理的缺点几乎全部可以通过"类型化扩展"解决 — Manifest Spec、类型化 Validator、类型专项扫描规则、类型化审批清单。统一不意味着放弃类型差异，而是让类型差异在统一的治理框架内各自表达。

---

## 5. 分散治理的优点

分散治理指 Agent / Skill / Tool / MCP 分别使用独立的数据模型、独立的生命周期、独立的审批和独立的搜索。

| # | 优点 | 说明 |
|---|------|------|
| 1 | **类型语义极度精确** | 每类资产有专属的表结构、字段、约束，数据库层保证数据完整性 |
| 2 | **审批高度适配** | 每类资产有独立审批流程和审批清单，审批人面对的永远是该类型的内容 |
| 3 | **独立演进** | 某类资产（如 Skill）变更生命周期不影响其他类型 |
| 4 | **查询性能可控** | 每类资产独立表独立索引，无通用模型性能顾虑 |
| 5 | **职责清晰** | 每类资产团队自行迭代不相互耦合 |

---

## 6. 分散治理的缺点

| # | 缺点 | 说明 |
|---|------|------|
| 1 | **治理逻辑重复 4×** | 生命周期状态机、审批流程、回滚策略、下架策略需要对每类资产独立实现和维护 |
| 2 | **跨类型关系天然困难** | Agent uses Skill 的关系必须在 Agent 和 Skill 两个独立系统间同步，一致性和事务性都需要额外保障 |
| 3 | **搜索发现分裂** | 人和 Agent 需要在 4 个独立 Registry 中分别搜索，无法"一键搜全平台" |
| 4 | **安全准入不一致** | 4 套系统 4 套扫描，风险等级定义和 blocking 拦截规则可能不一致，可能导致"同样危险的能力在不同类型里有不同的准入结果" |
| 5 | **审计分裂** | 审批记录和生命周期事件分布在 4 个系统，跨类型追踪需要聚合层 |
| 6 | **开发成本 4×** | 新增通用治理能力（如下架策略增强）需要 4 个系统分别改动 |
| 7 | **违反当前 PoC 已验证方向** | 当前 94 个测试覆盖四类资产统一管理，拆分会立即使测试体系分裂 |
| 8 | **不符合 AGENTS.md 设计约束** | 约束文档明确要求 PoC 阶段统一使用 HubItem 建模 |

---

## 7. 当前 Hub 场景为什么推荐统一 Catalog / Governance

当前 Hub 面对的是 **企业内部 Agent 平台的能力资产市场**，四类资产具有以下共同特征：

| 共同特征 | 说明 |
|----------|------|
| **都需要注册** | 所有能力在进入平台前需要一个统一的注册入口 |
| **都需要版本** | 所有能力随迭代产生新版本，旧版本保留 |
| **都需要审批** | 所有能力发布前需要人工确认质量和安全 |
| **都需要扫描** | 所有能力可能包含风险（prompt 注入、危险命令、密钥泄露），需要安全准入 |
| **都需要发布/下架** | 所有能力有上线和退役的完整生命周期 |
| **都需要可发现** | 人和 Agent 都需要能搜索到已发布能力 |
| **都被引用/依赖** | Agent 依赖 Skill、Skill 调用 Tool、Agent 连接 MCP — 跨类型依赖是天然存在的 |

如果四类资产各自管理，上述需求需要在 4 个系统中各自实现，且跨类型依赖同步将极为复杂。

**关键判断**：四类资产的**治理需求**高度同构，**内容差异**完全可以通过类型化扩展承载。统一治理层 + 类型化内容层是最优分割。

---

## 8. 为什么不推荐完全分散治理

| 不接受分散治理的原因 | 说明 |
|----------------------|------|
| **治理同构性** | 四类资产的治理需求（注册/版本/审批/扫描/发布/下架/回滚）100% 相同，分散治理是 4× 重复 |
| **关系网络的统一性** | Agent/Skill/Tool/MCP 的关系不是孤立的 — 查询一个 Agent 需要同时看到它依赖的 Skill、调用的 Tool、连接的 MCP。统一模型下这是一个 join 查询，分散模型下需要跨系统聚合 |
| **企业治理的一致性** | 安全团队只关心"这个能力风险高不高、能不能发布"，不管它是什么类型。统一的 RiskLevel 让安全团队面对一个词汇表，降低沟通成本 |
| **当前 PoC 已验证** | 94 个测试已经证明：统一的 HubItem + HubItemVersion 模型可以承载四类资产的管理闭环。拆分的改造成本远大于收益 |
| **未来能力类型扩展** | 如果后续出现第 5 种类型（如 Plugin、Connector），统一模型只需增加 type 枚举值，分散模型需要新建第 5 套完整系统 |

---

## 9. 推荐原则

### 原则一：统一治理面

```
  ┌────────────────────────────────────────┐
  │           统一治理面（Hub 自研）          │
  │                                        │
  │  · HubItem / HubItemVersion            │
  │  · 生命周期状态机                       │
  │  · 发布审批                            │
  │  · 风险准入（RiskLevel）                │
  │  · 版本回滚                            │
  │  · 下架策略                            │
  │  · 搜索发现                            │
  │  · HubItemRelation（跨类型依赖）        │
  └────────────────────────────────────────┘
```

- 所有类型共享同一治理流程和治理规则
- 治理层不感知类型差异（或仅通过 type 字段做路由）

### 原则二：类型化 Manifest

```
  ┌─────────────────────────────────────────────────────┐
  │              类型化 Manifest（Hub 制定）              │
  │                                                     │
  │  AgentManifest   SkillManifest   ToolManifest   MCPManifest
  │  ────────────   ────────────   ────────────   ───────────
  │  · industry     · task_def     · params       · transport
  │  · scenario     · capability   · returns      · protocol
  │  · system_prompt· constraints  · examples     · auth_config
  │  · model_config · skill_spec   · timeout      · server_config
  └─────────────────────────────────────────────────────┘
```

- 每类资产有独立的 manifest schema 定义
- Manifest Spec v0.1 中通过 `type` 字段路由到对应 schema
- 导入时按类型执行对应的 schema 校验

### 原则三：类型化 Validator

- 审批阶段，按 `type` 展示不同的清单项
- 扫描阶段，通用规则 + 类型专项规则叠加
- 创建阶段，按 `type` 校验必填字段差异

```
                  ┌─────────────┐
                  │  通用校验    │  ← 所有类型共用（name/version/description）
                  └──────┬──────┘
                         │
         ┌───────────────┼───────────────┐
         │               │               │
  ┌──────▼──────┐ ┌──────▼──────┐ ┌──────▼──────┐
  │ Agent 校验   │ │ Skill 校验   │ │ MCP 校验    │
  │ · industry  │ │ · task_def  │ │ · transport │
  │ · scenario  │ │ · capability│ │ · protocol  │
  │ · prompts   │ │ · skill_spec│ │ · auth      │
  └─────────────┘ └─────────────┘ └─────────────┘
```

### 原则四：独立 Runtime

- Hub 不执行任何能力
- Hub 不托管 MCP Server
- Hub 不参与 Agent/Skill/Tool 的运行装配
- Runtime 负责：执行、编排、沙箱、权限评估、资源分配
- Hub 负责：可信发现、版本管理、风险告知、依赖解析

---

## 10. 对后续模型的影响

### 10.1 HubItem（已实现，方向不变）

```
HubItem
├── 统一字段（name, type, status, risk_level, ...）
├── type 枚举 → agent / mcp / skill / tool
├── JSON 承载类型差异 → manifest_json / config_json / permission_json
└── 后续演进方向 → 类型扩展表一对一关联（阶段 6+，当前不做）
```

**当前不做拆分**，因为四类资产的差异量尚不足以证明扩展表的必要性。如果某类型需要 10+ 专有字段做高频查询，可创建类型扩展表（如 `agent_profiles`）通过 hub_item_id 一对一关联。

### 10.2 HubItemVersion（已实现，方向不变）

```
HubItemVersion
├── 统一字段（version, status, risk_level, ...）
├── JSON 承载版本级类型差异 → manifest_json / config_json / input_schema / output_schema
├── permission_json → 按类型校验权限声明完整性
├── runtime_compatibility → 按类型校验兼容性字段
└── 后续 → 类型化 Config Schema 校验（阶段 3 Manifest Spec v0.1）
```

### 10.3 HubItemRelation（阶段 1 规划）

```
HubItemRelation
├── 统一的关系模型
├── relation_type → uses / invokes / depends_on / provides
├── relation_scope → management / runtime
├── version_policy → current / fixed / compatible
└── 类型规则 → Agent 可依赖 MCP/Skill/Tool/Agent，MCP 仅 provides Tool（不依赖）
```

统一的关系模型是统一治理面的关键优势 — 跨类型关系天然可表达，无需跨系统同步。

### 10.4 类型化 Manifest Schema（阶段 3 规划）

```
Manifest Spec v0.1
├── 顶层通用字段（name, type, version, description, ...）
├── type 路由 → 加载对应类型的 schema
│
├── AgentManifest
│   ├── industry, scenario
│   ├── system_prompt (JSON)
│   ├── model_config (JSON)
│   └── agent_relations (uses/DependsOn)
│
├── SkillManifest
│   ├── category, tags
│   ├── task_definition (JSON)
│   ├── capability_declaration (JSON)
│   ├── constraints (JSON)
│   └── skill_relations (invokes/depends_on)
│
├── ToolManifest
│   ├── category, tags
│   ├── parameters (JSON Schema)
│   ├── returns (JSON Schema)
│   ├── examples (JSON)
│   └── timeout_seconds
│
└── MCPManifest
    ├── transport (stdio/sse/http)
    ├── protocol_version
    ├── auth_config (JSON)
    ├── server_config (JSON)
    └── provided_tools (Array)
```

**重要**：类型化 Manifest 不破坏统一治理面。Manifest 只在导入/版本创建/下载时按类型校验，治理面（生命周期/审批/回滚）不感知 Manifest 的内部结构。

---

## 11. 最终结论

### 核心结论

**Agent / Skill / Tool / MCP 应该在 Hub 中统一治理，但不应强行统一其内部结构。**

| 层 | 策略 | 理由 |
|----|------|------|
| **治理面** | **统一** | 生命周期、审批、扫描、回滚、下架对四类资产完全同构，统一避免 4× 重复 |
| **数据模型** | **统一主表 + JSON 承载差异** | HubItem/HubItemVersion 提供共同骨架，manifest_json/config_json 承载类型差异 |
| **关系模型** | **统一** | 跨类型关系天然存在于统一模型中，拆分将带来分布式一致性问题 |
| **Manifest Schema** | **按类型独立** | Agent/Skill/Tool/MCP 各有专属 schema，不混用不强制 |
| **校验/扫描** | **通用 + 类型专项叠加** | 通用规则覆盖所有类型，类型专项规则补充 |
| **Runtime** | **完全独立** | Hub 不执行任何能力，不托管 MCP Server |

### 不推荐的方案

| 方案 | 不推荐原因 |
|------|-----------|
| 四类资产完全独立 | 治理逻辑 4× 重复；跨类型关系需分布式同步；搜索发现分裂 |
| 四类资产完全一致（无类型差异） | 不同类型核心字段不同，强求统一会导致 schema 退化（All fields optional, no type constraint） |
| 在 HubItem 上加类型特有列 | 表结构膨胀，新增类型需 DDL；当前 4 类可管理，未来 10+ 类型不可持续 |
| Hub 按类型拆分成 4 个微服务 | PoC 阶段过度设计；治理逻辑重复；网络开销和事务一致性复杂度远超收益 |

### 演化路径

```
PoC 当前（已实现）:
  HubItem(type=agent|mcp|skill|tool) + JSON 承载差异

阶段 1-3（短期）:
  统一模型 + HubItemRelation + Manifest Spec v0.1 + 类型化校验/扫描

阶段 6+（中期，如某类型差异显著扩大）:
  保留统一治理面 + 按需创建类型扩展表（agent_profiles / mcp_configs 等）

生产（长期，类型数量 > 10）:
  评估统一治理面是否需拆分 → 大概率仍保留统一治理面，扩展表 + 类型化 schema 已足够
```

---

> 配套文档：
> - `docs/validation/00_technical_validation_plan.md` — 验证计划
> - `docs/validation/01_open_source_reuse_matrix.md` — 开源复用矩阵
> - `docs/validation/08_final_recommendation.md` — 最终推荐路线
> - `docs/14_hub_capability_market_solution_design.md` — 整体方案设计（第 4 节：能力资产模型）
