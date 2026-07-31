# Gaia 文档工程总纲

> **状态**：v1.0（已评审定稿）
> **日期**：2026-07-13
> **作者**：文档工程组
> **依据**：[`tech-doc-writing-research.md`](./tech-doc-writing-research.md)（方法论研究报告）
> **目的**：定义 Gaia 系统性技术文档的目录结构、每篇定位、写作规范、执行计划，作为后续逐篇落地的骨架。
>
> **评审决策（v1.0 落实）**：
> 1. 新建 `guide/` 目录（不改造现有结构）
> 2. 首篇 E1《什么是 Gaia》单写定调，review 风格后再批量
> 3. 未实现特性**边开发边写文档**（每天一篇），文档只描述已实现的东西——根除状态漂移
> 4. 引入 VitePress 建文档站点
> 5. `reference.md`（430KB Palantir 参考）原文保留不动，guide 只引用
> 6. 统一中文（专有名词/API/Schema 名保留英文）

---

## 〇、为什么做这件事（SCQA 开篇）

- **背景（S）**：Gaia 已是一个 38k 行后端 + 166 文件前端 + 11 服务 + 80 篇散落文档的成熟工程，覆盖本体建模、8 层数据引擎、Action 闭环、图关联推理、多源融合、权限治理等 9 大特性。
- **冲突（C）**：现有文档按"开发时序/事故复盘"组织，适合维护者不适合新读者。三大痛点：① 缺面向新读者的"需求→架构→选型→特性→使用"叙事主线；② 严重偏 Explanation+Reference，缺 Tutorials+How-to（PostgreSQL 文档病）；③ 状态标注漂移（如"外部数据路径待接线"实已实现），已设计未实现特性（scenario）未显式标注。
- **疑问（Q）**：如何用一套系统文章，让决策者看懂价值、集成者能用起来、贡献者能深入改造？
- **回答（A）**：**本总纲**。采用 Diátaxis 四模式 + 金字塔叙事 + arc42 骨架 + 渐进式披露，建一套三层阅读路径、四象限分仓的文档体系。文档**只描述已实现的东西**，未实现特性边开发边补（每天一篇），从源头根除状态漂移。

---

## 一、设计原则（9 条，源自研究结论）

| # | 原则 | 落地要求 |
|---|------|---------|
| 1 | **四模式分仓** | 每篇先定象限（Tutorial/How-to/Reference/Explanation）再动笔。重点补 Tutorials+How-to |
| 2 | **金字塔叙事** | 结论先行（BLUF）+ SCQA 开篇 + 纵向疑问-回答链 + 同层 MECE |
| 3 | **任务导向主线** | 使用指导按"我想做 X"组织，不按"组件支持 X"组织 |
| 4 | **渐进式披露** | 三层阅读路径（5min→30min→深度）。每页统一"TL;DR→概念→操作→深入"结构 |
| 5 | **arc42 骨架对齐** | 补齐 §1 引言/§6 运行时视图/§10-11 质量场景与风险；ADR/ICD 已对齐 §9 |
| 6 | **Palantir 叙事母题借鉴** | 复刻"四重整合+场景具象+完整走查"手法，去 Palantir 黑话，用"语义大脑+安全手脚" |
| 7 | **只写已实现** | 文档只描述已落地的东西；未实现特性边开发边写（每天一篇），从源头根除状态漂移。不设"已设计未实现"专区 |
| 8 | **Docs-as-Code** | 文档模板 + 导航索引 + 链接/lint 校验入 pre-commit；与代码同 PR 审查 |
| 9 | **受众分层** | 决策者（价值与权衡）/ 集成开发者（API 与 how-to）/ 贡献者（架构与 ADR）三入口 |

---

## 二、文档目录结构（四象限分仓）

> 在现有 `docs/` 基础上**重组导航**，不搬迁已有文件（保留 git 历史）。新建 `docs/guide/` 作为读者主入口，现有 `architecture/` `design/` `engineer/` `bugfix/` `research/` 退为深度参考层。

```
docs/
├── guide/                      【🆕 读者主入口 — Diátaxis 四象限】
│   ├── README.md               # 导航首页（三层阅读路径 + 受众入口）
│   │
│   ├── 01-overview/            【Explanation 象限 — 理解层】
│   │   ├── 01-what-is-gaia.md          # 项目定位与价值（arc42 §1）
│   │   ├── 02-architecture.md          # 8 分层架构总览（arc42 §4/§5 + C4 L1/L2）
│   │   ├── 03-ontology-system.md     # 本体体系：语义大脑+安全手脚（Palantir 母题）
│   │   ├── 04-data-flow.md             # 六种数据流场景（arc42 §6 运行时视图）
│   │   └── 05-design-principles.md     # 四大设计哲学与权衡
│   │
│   ├── 02-tutorials/          【Tutorials 象限 — 学习层，从零到一】
│   │   ├── 01-quickstart.md            # 30 分钟跑通：docker compose + 建第一个本体
│   │   ├── 02-model-ontology.md        # 对话式建模教程（AG-UI Thread）
│   │   ├── 03-connect-data.md          # 连接数据源教程（JDBC/CDC）
│   │   └── 04-explore-graph.md         # 图探索决策分析教程
│   │
│   ├── 03-how-to/             【How-to 象限 — 操作层，解决具体问题】
│   │   ├── README.md                   # 操作索引（按"我想做 X"）
│   │   ├── modeling/                   # 建模操作
│   │   ├── data/                       # 数据接入操作（含 25 连接器）
│   │   ├── actions/                    # Action 定义与执行操作
│   │   ├── query/                      # 查询操作（NL/图/时空）
│   │   ├── permissions/                # 权限治理操作
│   │   └── ops/                        # 运维操作（部署/监控/重建投影）
│   │
│   ├── 04-concepts/           【Explanation 象限 — 概念层，每特性深入】
│   │   ├── 01-ontology-modeling.md     # 本体建模深度
│   │   ├── 02-data-layers.md           # 8 层数据引擎深度
│   │   ├── 03-action-loop.md           # Action 闭环与 outbox 同步
│   │   ├── 04-tool-layer.md            # 本体工具层 + HITL
│   │   ├── 05-textql.md                # 本体驱动 NL 查询
│   │   ├── 06-graph-reasoning.md       # 图关联推理与时空分析
│   │   ├── 07-multi-source.md          # 多源数据融合
│   │   ├── 08-permission.md            # 权限治理体系
│   │   └── 09-ai-agent.md              # AG-UI Agent 与对话式建模
│   │
│   ├── 05-reference/          【Reference 象限 — 查询层，去叙事化】
│   │   ├── api-index.md                # API 总览（10 路由组）
│   │   ├── config-reference.md         # 配置项与环境变量
│   │   ├── schema-reference.md         # ORM 表与 pydantic schema
│   │   ├── cli-reference.md            # 命令与脚本
│   │   └── glossary.md                 # 术语表（arc42 §12）
│   │
│   └── 06-roadmap/            【🆕 状态与路线图 — 渐进式披露的状态层】
│       ├── implementation-status.md    # 软链/索引到 ../architecture/implementation-status.md
│       ├── designed-not-implemented.md # 🆕 已设计未实现特性清单（scenario 等）
│       └── changelog.md                # 版本演进
│
├── architecture/              【深度参考层 — 保留】ADR/ICD/implementation-status/architecture_plan
├── design/                    【深度参考层 — 保留】设计文档
├── engineer/                  【深度参考层 — 保留】工程规范/验证/事故复盘
├── bugfix/                    【深度参考层 — 保留】事故复盘
├── research/                  【深度参考层 — 保留】研究文档（含本总纲+方法论报告）
└── web-ui/                    【深度参考层 — 保留】前端集成
```

---

## 三、三层阅读路径（渐进式披露）

| 层级 | 时长 | 读者 | 内容 | 对应文件 |
|------|------|------|------|---------|
| **L1 电梯演讲** | 5 min | 决策者/初次评估者 | Gaia 是什么、解决什么、为什么开源 | `guide/01-overview/01-what-is-gaia.md` |
| **L2 架构与范式** | 30 min | 架构师/技术选型者 | 8 分层 + 本体体系 + 数据流 + 设计哲学 | `guide/01-overview/02~05` |
| **L3 深度章节** | 按需 | 集成者/贡献者 | 9 大特性深度 + API reference + ADR/ICD | `guide/04-concepts/*` + `guide/05-reference/*` + `architecture/` |

**每篇文档统一结构**（金字塔 + 渐进式披露）：
1. **TL;DR**（3 行，结论先行）：这是什么 / 解决什么 / 何时用
2. **概念**（Why，链接到深度 Explanation）
3. **操作**（How，可执行步骤或 API 调用）
4. **深入**（折叠/链接到 ADR/ICD/源码）

---

## 四、文章清单与定位

### 4.1 Explanation 象限 — 概念理解（guide/01 + guide/04）

| # | 文件 | 一句话定位 | 状态锚点 | 主要素材来源 |
|---|------|-----------|---------|-------------|
| E1 | `01-overview/01-what-is-gaia.md` | 项目定位、价值主张、与 Palantir 的关系 | ✅ 全部已实现 | CLAUDE.md 首段 + reference.md 母题 |
| E2 | `01-overview/02-architecture.md` | 8 分层架构总览 + C4 L1/L2 图 | ✅ | architecture_overview.md |
| E3 | `01-overview/03-ontology-system.md` | 本体体系：四重整合(data+logic+action+security)、语义大脑+安全手脚 | ✅ | reference.md + Palantir ontology-system |
| E4 | `01-overview/04-data-flow.md` | 六种数据流场景走查 | ✅ | CLAUDE.md §五种数据流 + data-flow-diagrams.md |
| E5 | `01-overview/05-design-principles.md` | 四大设计哲学（复杂留给自己/组件复用/先走通/质量守则）+ 架构红线 | ✅ | CLAUDE.md 核心设计哲学 + 红线 1-11 |
| E6 | `04-concepts/01-ontology-modeling.md` | 本体建模深度：ObjectType/Property/Link/Action/Interface + 命名规范 | ✅ | ontology-modeling-spec + naming.py |
| E7 | `04-concepts/02-data-layers.md` | 8 层数据引擎：每层职责、组件版本、降级策略 | ✅ | architecture_overview §5 + ICD-01~05 |
| E8 | `04-concepts/03-action-loop.md` | Action 闭环：outbox 驱动同步、三层权限、版本管控 | ✅ | action-architecture + action-sync-outbox-design |
| E9 | `04-concepts/04-tool-layer.md` | 本体工具层：22 工具/8 toolset + HITL 审批 + 三入口 | ✅ | ontology-tool-layer + ADR-009/010 |
| E10 | `04-concepts/05-textql.md` | TextQL 五步流水线：NL→QueryIR→编译 | ✅ Phase 1-2 | textql-design + ADR-012 |
| E11 | `04-concepts/06-graph-reasoning.md` | 图关联推理：ObjectSet IR + 多引擎联动 + 证据链 | ✅ M0-M7 | graph-reasoning-design + ADR-015 |
| E12 | `04-concepts/07-multi-source.md` | 多源融合：25 连接器 + CDC + 国产库驱动 | ✅ | multi-source-data-fusion-design + ADR-014 |
| E13 | `04-concepts/08-permission.md` | 权限治理：三层+RBAC×MAC+Cedar+行列下推+JIT+审计 | ✅ Phase 0-5 | permission-governance-design + ADR-016/017 |
| E14 | `04-concepts/09-ai-agent.md` | AG-UI Agent + 对话式建模 + BuildWith 脚手架 | ✅ | ai-integration-guide + ADR-015 |

### 4.2 Tutorials 象限 — 从零到一（guide/02）

| # | 文件 | 读者产出 | 状态锚点 |
|---|------|---------|---------|
| T1 | `02-tutorials/01-quickstart.md` | docker compose 起全栈 + 建第一个本体 + 查到数据 | ✅ |
| T2 | `02-tutorials/02-model-ontology.md` | 用对话式建模建一个供应链本体（客户/订单/物流） | ✅ |
| T3 | `02-tutorials/03-connect-data.md` | 连一个 MySQL 数据源 + CDC 同步到 Iceberg | ✅ |
| T4 | `02-tutorials/04-explore-graph.md` | 在图探索画布做一次供应链中断传导分析 | ✅ |

### 4.3 How-to 象限 — 操作手册（guide/03）

按"我想做 X"组织，每篇短小可执行。锚点为前端 15 页面 + 10 个 API 路由组：

- **modeling/**：建对象类型 / 建关系 / 建 Action / 建接口 / 软删除与恢复
- **data/**：连 JDBC 源 / 连 Kafka / 连文件存储 / 配 CDC / 配时序同步 / 25 连接器速查表
- **actions/**：定义 Action / 执行单条 / 批量执行 / 预览 / 回滚版本
- **query/**：NL 查询 / 图遍历 / 时空过滤 / 时序查询 / 调 REST / 调 MCP / 调 AG-UI
- **permissions/**：建角色 / 打标记 / JIT 申请 / 查审计 / 五层权限检查
- **ops/**：部署 k3s / 监控 Prometheus / 重建图投影 / 重建时空投影 / Doris 降级排查

### 4.4 Reference 象限 — 查询层（guide/05）

去叙事化，纯事实：API 总览（10 路由组端点表）/ 配置项 / ORM 38 表 / 命令脚本 / 术语表。

### 4.5 状态与路线图（guide/06-roadmap/）

**核心原则：文档只写已实现的东西。** 未实现特性（scenario 沙箱、决策飞轮、Functions、实体对齐、全链路血缘、ObjectSet IR 二期 type、权限 Phase 6-7 等）**当前不写**，等开发到那天再写（每天一篇的节奏）。这样 guide 里出现的每个特性都是可用的，根除状态漂移。

`06-roadmap/` 只放：
- `implementation-status.md`：索引到 `../architecture/implementation-status.md`（唯一真相源，保留）
- `changelog.md`：版本演进
- （未来）当某特性落地时，其概念文档进 `04-concepts/`，状态同步更新

---

## 五、写作规范

### 5.1 每篇文档模板

```markdown
# <标题>

> **象限**：Explanation / Tutorial / How-to / Reference
> **读者**：决策者 / 集成开发者 / 贡献者
> **状态**：✅ 已实现 / 🟡 部分 / 🔴 待开发（注明代码核实日期）
> **预计阅读**：X min

## TL;DR
<3 行结论：这是什么 / 解决什么 / 何时用>

## <主体 — 按象限写法>

## 深入
<链接到 ADR/ICD/源码/事故复盘>
```

### 5.2 四象限写法差异

| 象限 | 开头 | 主体风格 | 禁止 |
|------|------|---------|------|
| Tutorial | "本教程你将完成 X" | 手把手步骤，可复制命令/代码 | 解释为什么（放概念链接） |
| How-to | "如何实现 X" | 步骤导向，假设已会基础 | 铺垫概念、讲设计权衡 |
| Reference | 端点/配置名 | 纯事实表格，无叙事 | 讲故事、讲为什么 |
| Explanation | "为什么这样设计" | 有观点、有取舍、有 alternatives | 罗列 API、step-by-step |

### 5.3 状态标注规范

文档只写已实现的特性，故 guide 内文档默认状态为 ✅ 已实现或 🟡 部分（注明缺口）。不写 🔴 待开发 / ⚫ 未规划（那些等实现后再写）。

- **✅ 已实现**：代码交叉验证过（不只是 implementation-status 写了）
- **🟡 部分**：核心通但有缺口（注明缺口 + 代码核实日期）
- 每篇文档 header 注明代码核实日期（如"2026-07-13 核实"）
- 与 `implementation-status.md` 冲突时以代码为准，并同步修 `implementation-status.md`

### 5.4 去实现细节文案

遵循 CLAUDE.md 既有约束：用户可见文案不暴露实现细节（不写"per-rule_type 幂等""ondelete=SET NULL"等）。文档面向开发者时可适度暴露，但面向使用者的 how-to 仍用行为语言。

---

## 六、与现有文档的关系

| 现有文档 | 处理方式 |
|---------|---------|
| `architecture/architecture_overview.md` | 作为 E2 的素材源，E2 是其读者友好版 |
| `architecture/implementation-status.md` | 唯一真相源保留；guide/06 建索引 + 补 designed-not-implemented |
| `architecture/adr-*.md`（17 篇） | 保留，作为深度层；E6-E14 引用 |
| `architecture/icd-*.md`（5 篇） | 保留，E7/E8 引用 |
| `architecture/reference.md`（430KB） | **不整篇呈现**，拆解引用到 E3/E6 等；保留原文供贡献者 |
| `design/scenario-*.md`（3 篇） | 保留，在 designed-not-implemented 显式索引 |
| `bugfix/*` `engineer/*` | 保留，深度层，how-to/ops 章节按需引用 |
| `docs/DEMO_*.md` | 作为 T1-T4 教程的素材 |

---

## 七、执行计划

### 7.1 阶段划分

| 阶段 | 产出 | 预计 |
|------|------|------|
| **P0 总纲定稿** | ✅ 本文件评审通过（v1.0） | 已完成 |
| **P1 骨架搭建** | guide/ 目录 + VitePress 站点骨架（首页/侧边栏/导航）+ 文档模板 + 空文件占位 | 1 天 |
| **P2 E1 试写定调** | E1《什么是 Gaia》单篇，review 风格 | 先行 |
| **P3 L1-L2 批量** | E2-E5（决策者路径） | P2 通过后 |
| **P4 Tutorials** | T1-T4（学习者路径） | 并行 |
| **P5 概念深度** | E6-E14（9 篇，对应已实现的 9 大特性） | 主力 |
| **P6 How-to** | 按 modeling/data/actions/query/permissions/ops 分批 | 滚动 |
| **P7 Reference** | API/配置/schema/术语 | 可由代码生成辅助 |
| **P8 边开发边写** | 后续每天一篇，新特性落地即补文档 | 持续 |

### 7.2 优先级建议

**P1 骨架 → P2 E1 试写**。E1《什么是 Gaia》是整套文章的"塔尖"，定调最重要，单篇评审风格后再批量。

### 7.3 质量门禁

每篇文档提交前：
- [ ] 象限/读者/状态 header 完整
- [ ] TL;DR 3 行结论先行
- [ ] 状态标注经代码核实（附日期）
- [ ] 链接有效（pre-commit 校验）
- [ ] 与 implementation-status 不冲突（冲突则以代码为准并同步修 implementation-status）

---

## 八、已评审决策（v1.0 定稿）

| # | 决策点 | 结论 |
|---|--------|------|
| 1 | 目录方案 | 新建 `guide/`，不改造现有结构（保留 git 历史） |
| 2 | 首篇风格 | E1《什么是 Gaia》单篇试写定调，review 后再批量 |
| 3 | 未实现特性 | **边开发边写**（每天一篇），文档只描述已实现的东西，不设"已设计未实现"专区 |
| 4 | 文档站点 | 引入 VitePress 建站点 |
| 5 | reference.md | 原文保留不动，guide 只引用 |
| 6 | 受众语言 | 统一中文（API/Schema 等专有名词保留英文） |

### 8.1 站点技术约定（P1 落地补充）

- **VitePress 位置**：`docs/.vitepress/config.ts`，依赖独立于前端（`docs/package.json`，`npm run dev/build/preview`）
- **srcDir = `guide`**：只编译 `guide/` 下文档。老文档（`architecture/` `design/` `engineer/` `bugfix/` `research/` 及根级 `reference.md` 等）作为深度参考层，**不进站点编译**（避免老文档尖括号被当 Vue 模板报错），通过仓库直接查看。导航"深度参考"指向仓库源。
- **首页**：`guide/index.md`（VitePress home layout，hero + 6 feature 卡片）
- **链接路径**：srcDir=guide 下，guide 内文档间链接不带 `/guide` 前缀（如 `/01-overview/01-what-is-gaia`）
- **模板**：`docs/_template.md`（放 srcDir 外，不编译，供复制）
- **搜索**：VitePress 本地搜索（中文）

---

## 附：方法论依据映射

本总纲每条原则的出处见 [`tech-doc-writing-research.md`](./tech-doc-writing-research.md)：
- 四象限分仓 → Diátaxis（§一）
- 金字塔叙事 + SCQA → Minto Pyramid（§二）
- arc42 骨架 → §三
- 任务导向 → Concept vs Task + Meng 2019（§四）
- 渐进式披露三层路径 → §五
- Palantir 叙事母题 → §六
- Docs-as-Code → §七
- 状态以代码为准 → 本次"外部数据路径"漂移事件教训
