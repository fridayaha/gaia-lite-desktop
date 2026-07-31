# 开源方案复用矩阵

> 文档编号：validation/01
> 版本：v0.1
> 日期：2026-05-15
> 依赖文档：`validation/00_technical_validation_plan.md`、`docs/13_open_source_tech_selection_evaluation.md`（数据来源）

---

## 评估说明

本矩阵对 10 个候选开源方案进行**逐能力**对照评估。与 `docs/13` 的综合九维评分不同，本矩阵聚焦于：

1. **每个候选方案是否满足 Hub 的具体能力需求**（Yes / No / Partial）
2. **复用方式**（直接使用 / Adapter / Portal / Spec Reference / 组件替换 / 参考设计）
3. **Spike 验证决策**（Yes：需要实际验证 / No：不进入验证 / Optional：视资源决定）

**GitHub 数据**：引用 `docs/13_open_source_tech_selection_evaluation.md`，采集于 2026-05-15。

> **动态数据说明**：Stars、主语言、License、Open Issues、最新版本等数据以 GitHub API 采集时的结果为准。本报告中的社区数据具有时效性，后续提交前应重新运行 `scripts/collect_github_metrics.py` 刷新。

---

## 候选方案列表

| # | 方案 | 仓库 | 主语言 | License | 定位 |
|---|------|------|--------|---------|------|
| 1 | AgentRegistry | agentregistry-dev/agentregistry | Go | Apache-2.0 | 多资产类型 Registry |
| 2 | SkillHub | iflytek/skillhub | Java | Apache-2.0 | Skill 管理与执行框架 |
| 3 | MCP Registry | modelcontextprotocol/registry | Go | Other | MCP 官方 Registry |
| 4 | Backstage | backstage/backstage | TypeScript | Apache-2.0 | 开发者门户 / Catalog |
| 5 | Artifact Hub | artifacthub/hub | TypeScript | Apache-2.0 | 云原生包市场 |
| 6 | DataHub | datahub-project/datahub | Python | Apache-2.0 | 元数据管理与数据血缘 |
| 7 | OpenMetadata | open-metadata/OpenMetadata | TypeScript | Apache-2.0 | 元数据管理平台 |
| 8 | CKAN | ckan/ckan | Python | AGPL | 开放数据门户 |
| 9 | 当前 FastAPI Hub | —（内部） | Python | 内部 | 能力资产治理中心 |
| 10 | Django + DRF | django/django | Python | BSD-3 | 通用全栈 Web 框架 |

---

## 复用方式定义

本矩阵中使用的复用方式术语定义如下：

| 复用方式 | 含义 | 代码耦合 | 适用场景 | 示例（本矩阵中） |
|----------|------|:---:|------|------|
| **Adapter** | 在 Hub 侧编写轻量协议转换层（≤ 500 行），将外部项目的接口语义映射为 Hub 内部调用，不引入外部项目运行时 | 极低 | 外部项目有成熟的协议规范，Hub 需要与之互通但不需要引入其代码 | AgentRegistry Discover 协议 |
| **Portal** | 外部项目作为独立的展示层部署，消费 Hub API 提供能力目录浏览/搜索/详情展示，治理逻辑仍在 Hub | 低 | 外部项目有优秀的 UI/Catalog 能力，可作为 Hub 的上层门户 | Backstage 作为能力 Portal |
| **Spec Reference** | 不引入代码，仅参考外部项目的规范文档、Schema 定义、API 语义来设计 Hub 的对应模块 | 无 | 外部项目的协议规范是领域标准或最佳实践 | MCP Registry 的 Config Schema、SkillHub 的 Skill Spec |
| **Component** | 引入外部项目的 SDK/CLI/库作为 Hub 的依赖组件，替代或增强 Hub 的某一子功能 | 低 | 外部项目提供成熟的独立组件（如迁移工具、存储 SDK） | Alembic、MinIO SDK |
| **Reference** | 仅阅读外部项目的设计文档、架构、数据模型作为灵感来源，不引入代码也不复制规范 | 无 | 外部项目在某一维度有优秀设计但不适合直接复用 | DataHub 的元数据图谱、CKAN 的 Dataset 模型 |
| **None** | 不推荐任何形式的复用 | — | 方案不适合 Hub 场景或违反技术约束 | Django+DRF |

---

## 评估维度说明

| 维度 | 评估问题 | 判定标准 |
|------|----------|----------|
| 可直接使用 | 是否可零修改直接部署为 Hub 能力市场 | Yes = 功能全覆盖；Partial = 可覆盖子集；No = 不匹配 |
| 可低侵入集成 | 是否可通过 Adapter/Wrapper/Plugin 模式接入 | Yes = ≤ 500 行适配；Partial = 需要中度适配；No = 需侵入 |
| 需要 fork | 是否需要 fork 源码才能满足需求 | Yes = 必须 fork；No = 不需要 |
| 商用 License | License 是否允许商业闭源使用 | Yes = Apache2/MIT/BSD；No = AGPL/Other 限制 |
| 覆盖 Agent 类型 | 支持 Agent 类型能力管理 | Yes / Partial / No |
| 覆盖 Skill 类型 | 支持 Skill 类型能力管理 | Yes / Partial / No |
| 覆盖 Tool 类型 | 支持 Tool 类型能力管理 | Yes / Partial / No |
| 覆盖 MCP 类型 | 支持 MCP 类型能力管理 | Yes / Partial / No |
| 版本管理 | 多版本并存 + 语义化版本号 | Yes / Partial / No |
| 审批流程 | 完整提交→审核→发布→驳回 | Yes / Partial / No |
| 安全扫描 | 内置扫描器或可对接 | Yes / Partial / No |
| Runtime Discover | 运行时能力发现接口 | Yes / Partial / No |
| 下载/导出 | Manifest / 包 / 全量导出 | Yes / Partial / No |
| 关系管理 | 资产间依赖关系建模 | Yes / Partial / No |
| Category / Tag | 树形分类 + 扁平标签体系 | Yes / Partial / No |
| 身份权限 | 内置用户角色 + 权限控制 | Yes / Partial / No |
| 不推荐直接采用的原因 | 具体技术/业务障碍 | 文本 |
| 推荐使用方式 | Adapter / Portal / Spec Ref / Component / Reference / None | 枚举 |
| 是否进入 Spike | Yes / No / Optional | 决策 |

---

## 逐方案矩阵

### 1. AgentRegistry（agentregistry-dev/agentregistry）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 缺少审批/扫描/回滚/下架/关系管理 |
| 可低侵入集成 | **Partial** | Adapter 模式可实现 Discover/Resolve 协议兼容，约 300-500 行 |
| 需要 fork | **No** | Adapter 层不触及上游源码 |
| 商用 License | **Yes** | Apache-2.0 |
| 覆盖 Agent 类型 | **Yes** | 原生支持 |
| 覆盖 Skill 类型 | **Yes** | 原生支持 |
| 覆盖 Tool 类型 | **Yes** | 原生支持 |
| 覆盖 MCP 类型 | **Yes** | 原生支持 |
| 版本管理 | **Yes** | 支持多版本 |
| 审批流程 | **No** | 无审批流程 |
| 安全扫描 | **No** | 无内置扫描器 |
| Runtime Discover | **Yes** | 核心能力，协议设计优秀 |
| 下载/导出 | **Partial** | 支持能力发现但无标准下载格式 |
| 关系管理 | **Partial** | 基本依赖声明，无版本策略/作用域 |
| Category / Tag | **No** | 无分类/标签体系 |
| 身份权限 | **No** | 无内置角色/权限控制 |
| 不推荐直接采用的原因 | Go 语言实现；缺少企业治理能力（审批/扫描/回滚/下架/权限）；社区规模小（307 Stars）；v0.3.3 仍早期 |
| 推荐使用方式 | **Spec Reference** — 参考其 Discover/Resolve 接口语义 |
| **是否进入 Spike** | **Yes** — 阶段 B 验证 Discover/Resolve 协议兼容性 |

**深度评估**

- **改造侵入程度**：如果将其作为主系统，需要全量补充审批、扫描、回滚、下架、权限等治理能力，改造量等同于重写一个能力市场。如果仅作为 Adapter 的协议参考，零侵入。
- **上游维护风险**：项目处于 v0.3.3 早期，API 原型化，频繁 breaking change 可能性高。Adapter 只做协议映射，上游变更影响可控（修改映射规则即可）。
- **综合复用定位**：不适合作为主系统或 Portal。最适合作为 **Spec Reference** — 其多资产 Registry 和 Discover/Resolve 协议设计是 Hub 阶段 5 的核心参考。Hub 自研 Runtime Discover 时推荐保持与 AgentRegistry 的接口语义可映射关系，以备后续 Adapter 互通。

---

### 2. SkillHub（iflytek/skillhub）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 仅管理 Skill 单一类型 |
| 可低侵入集成 | **No** | Java 语言；核心定位包含执行层，与 Hub 不执行原则冲突 |
| 需要 fork | **—** | 不适合集成 |
| 商用 License | **Yes** | Apache-2.0 |
| 覆盖 Agent 类型 | **No** | 不支持 |
| 覆盖 Skill 类型 | **Yes** | 核心类型 |
| 覆盖 Tool 类型 | **No** | 不支持 |
| 覆盖 MCP 类型 | **No** | 不支持 |
| 版本管理 | **Partial** | 有版本概念但非多版本并存 |
| 审批流程 | **Partial** | 有审核流程但不可定制 |
| 安全扫描 | **No** | 无扫描器 |
| Runtime Discover | **No** | 面向人管理，无 Runtime API |
| 下载/导出 | **No** | 无标准导出格式 |
| 关系管理 | **No** | 无关系模型 |
| Category / Tag | **Partial** | 可能有分类功能，但面向 Skill 单一类型 |
| 身份权限 | **Partial** | 可能有发布者概念，但无审批/角色体系 |
| 不推荐直接采用的原因 | Java 语言违反 AGENTS.md 约束；仅管理 Skill 单类型；SkillHub 以 Skill 管理为核心，可能包含安装、运行或执行相关能力；即使其部分能力可借鉴，也无法直接覆盖 Agent / Tool / MCP / Skill 的统一治理需求；v0.2.8 早期 |
| 推荐使用方式 | **Spec Reference** — 参考 Skill 包规范和权限声明模型 |
| **是否进入 Spike** | **No** — 作为 Spec 参考，不进行运行时验证 |

**深度评估**

- **改造侵入程度**：Java 语言与当前 Python 技术栈不兼容，不存在"低侵入改造"可能。即使将其思路迁移为 Python 实现，也需要全量重写四类资产管理、审批、扫描、关系等能力。从实际效果看，改造量等同于自研。
- **上游维护风险**：如 fork 其 Java 源码进行修改，团队需要维护两套语言栈。上游 v0.2.8 处于频繁迭代期，fork 的分支将快速落后于上游。
- **综合复用定位**：不适合任何形式的代码级复用。最适合作为 **Spec Reference** — Skill 包结构（manifest + code + config + deps）、权限声明模型（network / file_read / file_write / shell_exec 等维度）、治理流程（注册→审核→发布→下架）是其核心参考价值。这些参考已在 Hub 的 Manifest Spec 和 Skill 专项增强（阶段 6）规划中体现。

---

### 3. MCP Registry（modelcontextprotocol/registry）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 仅管理 MCP 单一类型；License 为 Other |
| 可低侵入集成 | **No** | Go 语言；协议耦合 MCP 生态 |
| 需要 fork | **—** | 不适合集成 |
| 商用 License | **No** | License 为 "Other"，需法务审核方可使用 |
| 覆盖 Agent 类型 | **No** | 不支持 |
| 覆盖 Skill 类型 | **No** | 不支持 |
| 覆盖 Tool 类型 | **No** | 不支持 |
| 覆盖 MCP 类型 | **Yes** | 核心类型，规范定义最权威 |
| 版本管理 | **Partial** | 协议版本管理，非能力版本管理 |
| 审批流程 | **No** | 无审批 |
| 安全扫描 | **No** | 无扫描器 |
| Runtime Discover | **Yes** | MCP 能力发现协议 |
| 下载/导出 | **No** | 无导出功能 |
| 关系管理 | **No** | 无关系模型 |
| Category / Tag | **No** | 无分类/标签体系 |
| 身份权限 | **No** | 无内置角色/权限控制 |
| 不推荐直接采用的原因 | 仅管 MCP 单类型；License 限制不明；Go 语言；无企业治理能力 |
| 推荐使用方式 | **Spec Reference** — MCP 类型 manifest schema 严格对齐 MCP Registry |
| **是否进入 Spike** | **Optional** — 不引用代码，仅做 MCP manifest / config schema 对齐验证；License 未确认前不作为运行时依赖 |

**深度评估**

- **改造侵入程度**：MCP Registry 作为 MCP 协议的附属组件，设计上与 MCP 生态深度耦合。若要改造为通用能力市场，需要解耦其 MCP 协议层并扩展 Agent/Skill/Tool 类型支持，改造深度远超"低侵入"。作为 Spec Reference 则零侵入。
- **上游维护风险**：License 为 Other（非标准开源协议），法务合规风险不确定。即使 License 确认可用，Go 语言实现意味着团队需要跨语言维护。MCP 协议本身的迭代会驱动 Registry 持续变更，上游更新频繁（v1.7.9，最近推送 2026-05-14）。
- **综合复用定位**：不适合任何代码级复用。最适合作为 **Spec Reference** — 其 MCP Server 配置的标准 schema 定义是 Hub 中 MCP 类型 manifest/config 规范的最高优先级对齐目标。Hub 在实施 MCP 类型资产时，manifest 的 config_json 结构应尽可能 1:1 映射 MCP Registry 的配置规范，以便未来 MCP 生态的 MCP Server 配置可直接通过 Hub 分发。

---

### 4. Backstage（backstage/backstage）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 通用开发者门户，非 AI 能力市场；需大量自定义开发 |
| 可低侵入集成 | **Partial** | 可作为 Portal 层，消费 Hub API；Entity Kind 需自定义 |
| 需要 fork | **No** | Plugin / Entity Kind 为扩展机制 |
| 商用 License | **Yes** | Apache-2.0 |
| 覆盖 Agent 类型 | **No** | 无预设 |
| 覆盖 Skill 类型 | **No** | 无预设 |
| 覆盖 Tool 类型 | **No** | 无预设 |
| 覆盖 MCP 类型 | **No** | 无预设 |
| 版本管理 | **No** | 无能力版本管理概念 |
| 审批流程 | **No** | 无审批流程 |
| 安全扫描 | **No** | 无内置扫描器 |
| Runtime Discover | **No** | 无运行时发现 API |
| 下载/导出 | **No** | 无能力包下载 |
| 关系管理 | **Yes** | EntityRelation 模型极为成熟 |
| Category / Tag | **Yes** | Kind / Type / Tag / Owner / System 元数据体系完善 |
| 身份权限 | **Partial** | 有 Backstage 内置认证，但非 Hub 所需的 Agent 权限模型 |
| 不推荐直接采用的原因 | TypeScript 后端；定位为开发者门户非能力市场；缺少所有治理能力（审批/扫描/回滚/下架）；需大量自定义 Entity Kind 和 Plugin 开发 |
| 推荐使用方式 | **Portal** — 作为上层展示门户 + **Spec Reference** — 核心借鉴 Catalog/Relation/Tag 模型 |
| **是否进入 Spike** | **Yes** — 阶段 C 验证 Backstage 作为 Portal 的可行性 |

**深度评估**

- **改造侵入程度**：Backstage 的 Plugin 和 Entity Kind 是原生扩展机制，不需要 fork 源码。作为 Portal 时，只需编写一个 Catalog Entity Provider 插件消费 Hub API，开发量约 300-800 行 TypeScript。Backstage 自身不承载治理逻辑（审批、扫描、回滚等），这些能力仍在 Hub 中，符合"治理面保留在 Hub"原则。
- **上游维护风险**：Backstage 社区极其活跃（33k Stars，CNCF 孵化），Entity Provider 插件接口相对稳定。即使上游大版本升级，插件多数情况下只需兼容性调整。主要风险在于 Backstage 自身部署运维复杂度（PostgreSQL + Backstage Backend + Frontend + Plugin 依赖），但作为独立 Portal 层，其可用性不耦合 Hub 的核心链路。
- **综合复用定位**：最适合作为 **Portal + Spec Reference**。Portal 方面，Backstage 的能力目录 UI、搜索、Tag 体系可作为 Hub 的上层展示门户，替代或增强当前 Vue 3 前端。Spec Reference 方面，其 Entity / Kind / Metadata / Spec / Relations 五层模型是 Hub 的 HubItemRelation 和分类标签体系设计的最高优先级参考。

---

### 5. Artifact Hub（artifacthub/hub）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 定位云原生制品市场，非 AI 能力市场 |
| 可低侵入集成 | **No** | 概念模型不匹配；无 Adapter 接口 |
| 需要 fork | **—** | 不适合集成 |
| 商用 License | **Yes** | Apache-2.0 |
| 覆盖 Agent 类型 | **No** | 无此类 Kind |
| 覆盖 Skill 类型 | **No** | 无此类 Kind |
| 覆盖 Tool 类型 | **No** | 无此类 Kind |
| 覆盖 MCP 类型 | **No** | 无此类 Kind |
| 版本管理 | **Yes** | Package → Version 模型成熟 |
| 审批流程 | **No** | 无审批 |
| 安全扫描 | **Partial** | 展示 Security Report 但不内置扫描 |
| Runtime Discover | **No** | 无运行时发现 |
| 下载/导出 | **Yes** | 完善的包下载和导出 |
| 关系管理 | **Partial** | 包依赖声明 |
| Category / Tag | **Yes** | Category + Tag + Badge 体系成熟 |
| 身份权限 | **Partial** | 有 Publisher/Repository 概念，无审批角色 |
| 不推荐直接采用的原因 | TypeScript 实现；定位为 Helm/OLM 等云原生制品市场，非 Agent/Skill 能力；概念模型（Chart/Operator/Policy）与 AI 能力不对应；无治理能力；最近更新距今半年以上 |
| 推荐使用方式 | **Spec Reference** — 参考包市场 UX、安全报告展示、Badge 体系 |
| **是否进入 Spike** | **Optional** — 前端 UX 设计阶段可参考 |

**深度评估**

- **改造侵入程度**：Artifact Hub 的业务模型（Publisher → Repository → Package → Version）与 Hub 的能力模型（HubItem → HubItemVersion）在概念上有相似之处，但其 Kind 体系（Helm/OLM/Falco Rules 等）与 Agent/Skill/Tool/MCP 完全不匹配。要接入需要 fork 源码添加自定义 Kind，并同时改造前端和后端，改造量大且侵入深。作为 Spec Reference 则零侵入。
- **上游维护风险**：v1.22.0 发布至今已超半年无新 release（2025-10-21），pushed_at 为 2026-05-12 表明仓库仍有活动但 release 节奏慢。CNCF Sandbox 项目，维护保障度一般。如果 fork 后深度定制，上游停滞会降低合并价值，但也意味着变更减少。
- **综合复用定位**：最适合作为 **Spec Reference**。主要参考价值集中在前端 UX 和包市场体验设计：多类型制品的统一展示、Security Report 可视化（风险徽章、发现详情列表）、Package Badge 体系（成熟度/安全等级/社区评分）、新版本通知机制。这些设计在 Hub 前端增强时可作为 UX 模式参考，但不涉及代码级复用。

---

### 6. DataHub（datahub-project/datahub）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 数据目录，非 AI 能力市场 |
| 可低侵入集成 | **No** | 10+ 微服务依赖，侵入性过高 |
| 需要 fork | **—** | 不适合集成 |
| 商用 License | **Yes** | Apache-2.0 |
| 覆盖 Agent 类型 | **No** | 不支持 |
| 覆盖 Skill 类型 | **No** | 不支持 |
| 覆盖 Tool 类型 | **No** | 不支持 |
| 覆盖 MCP 类型 | **No** | 不支持 |
| 版本管理 | **Partial** | 数据版本化，非能力版本 |
| 审批流程 | **No** | 无 |
| 安全扫描 | **No** | 无 |
| Runtime Discover | **No** | 无 |
| 下载/导出 | **No** | 无包导出 |
| 关系管理 | **Yes** | Metadata Graph + Lineage 极为成熟 |
| Category / Tag | **Yes** | Tag / Glossary / Domain 三层标签体系 |
| 身份权限 | **Partial** | 有 RBAC 权限体系但面向数据治理场景 |
| 不推荐直接采用的原因 | 强绑 Elasticsearch + Kafka + Neo4j 等 10+ 微服务依赖；部署复杂度极高（远超 PoC/准生产可接受范围）；定位为数据目录（Table/Dataset/Dashboard/Pipeline），非 AI 能力市场；893 Issues 维护压力大 |
| 推荐使用方式 | **Reference** — 参考元数据图谱和 Aspect 扩展模型 |
| **是否进入 Spike** | **No** — 基础设施依赖过重，仅作设计参考 |

**深度评估**

- **改造侵入程度**：DataHub 的架构是 10+ 微服务 + Elasticsearch + Kafka + Neo4j + MySQL/PostgreSQL，要在其上构建 AI 能力市场，本质上是在其外围搭建一个新的业务层，同时还要处理其基础设施与当前 Hub 环境的重叠与冲突。这不是"改造"，而是"在 DataHub 之上重新开发 Hub"。侵入程度极高。
- **上游维护风险**：893 个 Open Issues、活跃的社区意味着上游 API 可能频繁变更。如果深度依赖 DataHub 的 Metadata Graph 或 Aspect 模型，每次上游升级都可能破坏集成的 API 契约。维护成本持续且不可预测。
- **综合复用定位**：不适合任何代码级复用或部署依赖。适合作为 **Reference** — 主要借鉴其设计思路：元数据图谱（Metadata Graph）的跨类型关系建模方式（对 HubItemRelation 设计有启发）、Aspect 面相扩展模型（对 Hub 按 type 扩展 config schema 有启发）、Tag / Glossary / Domain 三层标签治理体系（对 Category + Tag 分级治理有启发）。这些都是设计层面的参考，不涉及任何代码或运行时依赖。

---

### 7. OpenMetadata（open-metadata/OpenMetadata）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 数据治理平台，非 AI 能力市场 |
| 可低侵入集成 | **No** | Elasticsearch + Airflow 等依赖过重 |
| 需要 fork | **—** | 不适合集成 |
| 商用 License | **Yes** | Apache-2.0 |
| 覆盖 Agent 类型 | **No** | 不支持 |
| 覆盖 Skill 类型 | **No** | 不支持 |
| 覆盖 Tool 类型 | **No** | 不支持 |
| 覆盖 MCP 类型 | **No** | 不支持 |
| 版本管理 | **Partial** | 元数据版本化 |
| 审批流程 | **No** | 无 |
| 安全扫描 | **No** | 无 |
| Runtime Discover | **No** | 无 |
| 下载/导出 | **No** | 无 |
| 关系管理 | **No** | 数据血缘为主 |
| Category / Tag | **Partial** | 有 Glossary/Classification 但不面向能力资产 |
| 身份权限 | **Partial** | 有角色体系但面向数据治理 |
| 不推荐直接采用的原因 | TypeScript 实现；定位为数据治理平台（非 AI 能力市场）；Elasticsearch + Airflow 等依赖过重；790 Issues 维护压力大 |
| 推荐使用方式 | **Reference** — 参考元数据版本管理和 Glossary 体系 |
| **是否进入 Spike** | **No** — 仅作设计参考 |

**深度评估**

- **改造侵入程度**：与 DataHub 类似，OpenMetadata 的基础设施依赖（Elasticsearch + Airflow + MySQL/PostgreSQL）远超 Hub PoC/准生产可接受范围。其数据治理的业务模型与 AI 能力市场完全不匹配，改造本质上是抛弃其核心业务逻辑只复用框架。侵入程度极高。
- **上游维护风险**：1.12.8-release 表明项目已进入稳定迭代，但 790 Issues + TypeScript 技术栈意味着团队需跨语言维护。Elasticsearch 依赖是 AGENTS.md 明确禁止的技术。
- **综合复用定位**：不适合任何代码级复用。适合作为 **Reference** — 仅参考其元数据版本化管理的思路（与 Hub 的 HubItemVersion 设计概念相似），以及 Glossary 业务术语表的分层标签治理模型。这些是概念层面的参考，不涉及代码。

---

### 8. CKAN（ckan/ckan）

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 开放数据门户，非能力市场 |
| 可低侵入集成 | **No** | Pylons 老架构，WSGI 同步模型 |
| 需要 fork | **—** | 不适合集成 |
| 商用 License | **No** | AGPL — 商业闭源/SaaS 部署有严格限制 |
| 覆盖 Agent 类型 | **No** | 不支持 |
| 覆盖 Skill 类型 | **No** | 不支持 |
| 覆盖 Tool 类型 | **No** | 不支持 |
| 覆盖 MCP 类型 | **No** | 不支持 |
| 版本管理 | **No** | Dataset 无版本概念 |
| 审批流程 | **No** | 无 |
| 安全扫描 | **No** | 无 |
| Runtime Discover | **No** | 无 |
| 下载/导出 | **Yes** | Dataset + Resource 结构化下载 |
| 关系管理 | **No** | 无关系模型 |
| Category / Tag | **Yes** | 有 Organization / Group / Tag 体系 |
| 身份权限 | **Partial** | 有组织/用户角色，但 AGPL 限制商业灵活性 |
| 不推荐直接采用的原因 | AGPL License 商业闭源/SaaS 部署有严格限制（根本性淘汰）；Pylons 老架构、WSGI 同步模型与 FastAPI 异步模型不兼容；数据集概念（Dataset/Resource）与能力资产（HubItem/Version）不对应；847 Issues |
| 推荐使用方式 | **Reference** — 参考 Dataset→Resource 两级模型 |
| **是否进入 Spike** | **No** — License 不合规直接淘汰 |

**深度评估**

- **改造侵入程度**：CKAN 的 License（AGPL）从根本上排除了商业化使用的可能性 — 这是单一最硬的门槛，不存在绕过方式。即使忽略 License，Pylons 架构（同步 WSGI）与当前 FastAPI 异步栈完全不兼容，技术栈改造等同推倒重写。
- **上游维护风险**：AGPL License 意味着如果通过任何方式集成了 CKAN 代码，Hub 的代码也必须以 AGPL 协议开源 — 对企业内部平台不可接受。即使作为参考，也不能引用其代码。
- **综合复用定位**：仅适合作为 **Reference** — 并且仅限于概念层面的结构参考。CKAN 的 Dataset → Resource 两级管理与 Hub 的 HubItem → HubItemVersion 两级模型在结构上有形式相似性。其 Harvester 自动导入机制对 Hub 的外部源自动同步功能设计有概念启发。**注意**：参考仅限于阅读设计思路，任何代码引用都构成 AGPL 传染。

---

### 9. 当前 FastAPI Hub

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **Yes** | 当前系统 |
| 可低侵入集成 | **—** | 自身即基础 |
| 需要 fork | **No** | 自有代码 |
| 商用 License | **—** | 内部项目 |
| 覆盖 Agent 类型 | **Yes** | ✅ 已实现 |
| 覆盖 Skill 类型 | **Yes** | ✅ 已实现 |
| 覆盖 Tool 类型 | **Yes** | ✅ 已实现 |
| 覆盖 MCP 类型 | **Yes** | ✅ 已实现 |
| 版本管理 | **Yes** | ✅ 已实现（HubItemVersion + 语义化版本 + 去重） |
| 审批流程 | **Yes** | ✅ 已实现（approve/reject/request_change + blocking 拦截） |
| 安全扫描 | **Yes** | ✅ 已实现（5 类规则 + ScanReport/ScanFinding） |
| Runtime Discover | **No** | ⬜ 未实现（阶段 5 规划） |
| 下载/导出 | **No** | ⬜ 未实现（阶段 4 规划） |
| 关系管理 | **No** | ⬜ 未实现（阶段 1 规划） |
| Category / Tag | **Partial** | ✅ Category/Tag 模型已定义，前端筛选和编辑待实现（阶段 2） |
| 身份权限 | **No** | ⬜ 未实现（阶段 7 规划），当前无认证 |
| 不推荐直接采用的原因 | — |
| 推荐使用方式 | **主系统** — 保留为能力治理核心 |
| **是否进入 Spike** | **No** — 非外部方案，为主线自研基础 |

---

### 10. Django + DRF

| 维度 | 判定 | 说明 |
|------|:---:|------|
| 可直接使用 | **No** | 通用 Web 框架，不含任何业务能力 |
| 可低侵入集成 | **—** | 全栈框架，不存在"低侵入引入部分"的可能 |
| 需要 fork | **No** | 框架本身不 fork |
| 商用 License | **Yes** | BSD-3 |
| 覆盖 Agent 类型 | **No** | 不包含业务语义 |
| 覆盖 Skill 类型 | **No** | 不包含业务语义 |
| 覆盖 Tool 类型 | **No** | 不包含业务语义 |
| 覆盖 MCP 类型 | **No** | 不包含业务语义 |
| 版本管理 | **No** | 不包含业务语义 |
| 审批流程 | **No** | 不包含业务语义 |
| 安全扫描 | **No** | 不包含业务语义 |
| Runtime Discover | **No** | 不包含业务语义 |
| 下载/导出 | **No** | 不包含业务语义 |
| 关系管理 | **No** | 不包含业务语义 |
| Category / Tag | **No** | 不包含业务语义 |
| 身份权限 | **Partial** | Django 内置 Admin/Auth 但与 Hub 的业务权限模型不匹配 |
| 不推荐直接采用的原因 | 违反 AGENTS.md 禁止 Django 的约束；Django + DRF 是通用 Web 框架，无法减少能力市场核心业务开发量；即使切换框架，审批、扫描、关系、Runtime Discover 等业务仍需自研，因此不具备替换收益；Admin 不是必需（已有 Vue 3 前端） |
| 推荐使用方式 | **None** — 不作为替换方案 |
| **是否进入 Spike** | **No** — 违反技术栈约束，无验证价值 |

---

## Spike 验证决策汇总

| 候选方案 | Spike | 说明 |
|----------|:---:|------|
| AgentRegistry | **Yes** | 阶段 B：验证 Discover/Resolve 协议兼容性 |
| SkillHub | **No** | Java 语言栈冲突；仅参考 Skill Spec |
| MCP Registry | **Optional** | 不引用代码，仅做 MCP manifest / config schema 对齐验证；License 未确认前不作为运行时依赖 |
| Backstage | **Yes** | 阶段 C：验证作为上层 Portal 的可行性 |
| Artifact Hub | **Optional** | 前端 UX 设计时参考，不进行运行时验证 |
| DataHub | **No** | 基础设施依赖过重 |
| OpenMetadata | **No** | 仅设计参考 |
| CKAN | **No** | AGPL License 直接淘汰 |
| 当前 FastAPI Hub | **No** | 主线自研基础 |
| Django + DRF | **No** | 违反技术栈约束 |

**Spike 决策归类**：

| 类别 | 方案 | 说明 |
|------|------|------|
| **代码级 Spike**（需实际拉取/运行/编写 Adapter） | AgentRegistry、Backstage | 验证 Discover 协议兼容（阶段 B）；验证 Portal 可行性（阶段 C） |
| **规范对齐验证**（不引用代码，仅文档对照） | MCP Registry | MCP manifest / config schema 对齐，License 确认前无运行时依赖 |
| **Optional UX/设计参考**（不进行运行时验证） | Artifact Hub | 前端 UX 阶段参考包市场、安全报告、Badge 体系设计 |
| **仅设计/规范参考**（不进入 Spike） | SkillHub、DataHub、OpenMetadata、CKAN、当前 Hub、Django+DRF | 语言/领域/License/依赖等限制，仅作 Spec/设计参考 |

---

> 上一文档：`docs/validation/00_technical_validation_plan.md`
> 下一文档：`docs/validation/08_final_recommendation.md`
