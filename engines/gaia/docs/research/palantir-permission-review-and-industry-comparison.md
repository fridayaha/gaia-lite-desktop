# Palantir Foundry 权限与隔离体系 —— 深度评审与业界对照研究报告

> **用途**：本文是对用户提供的 Palantir Foundry 权限与隔离体系参考材料（10 轮 + 总览，已归档于 [`palantir-permission-isolation-reference.md`](./palantir-permission-isolation-reference.md)）的**深度评审**，并扩展为**业界通用实践对照**与**"复杂留给自己，简单留给用户"设计哲学**三部分研究，作为 Gaia 项目权限治理特性落地评估的依据。
> **研究方法**：以 Palantir 官方文档（palantir.com/docs）、官方博客（blog.palantir.com）、官方白皮书为第一手来源核对材料准确性；以 NIST SP 800-162（ABAC 标准）、Google Zanzibar 论文、Apache Ranger/Databricks Unity Catalog/Snowflake Horizon/Immuta 官方文档为业界对照；以 HP 实验室《Making Policy Decisions Disappear into the User's Workflow》(Karp & Stiegler, 2009) 等可用安全研究为简化哲学理论基础。
> **研究日期**：2026-07-08
> **关联文档**：[`palantir-permission-isolation-reference.md`](./palantir-permission-isolation-reference.md)（材料归档）· [`palantir-capability-gap-analysis.md`](./palantir-capability-gap-analysis.md) §三 P0-B（差距分析）· Gaia 现状见 [`docs/architecture/implementation-status.md`](../architecture/implementation-status.md) 路标 #4
>
> ⚠️ **本轮研究不含 Gaia 落地方案**（用户明确要求"当前这一轮设计，你不用考虑 Gaia 如何落地"）。本文输出的是评估依据，Gaia 落地设计另立 ADR + 设计文档。

---

## 目录

- [第一部分：Palantir 材料评审（准确性 / 完整性 / 详细度核对）](#第一部分palantir-材料评审)
- [第二部分：业界通用实践对照](#第二部分业界通用实践对照)
- [第三部分："复杂留给自己，简单留给用户"设计哲学](#第三部分复杂留给自己简单留给用户设计哲学)
- [第四部分：综合结论与 Gaia 落地评估锚点](#第四部分综合结论与-gaia-落地评估锚点)

---

# 第一部分：Palantir 材料评审

> **评审结论先行**：用户提供的材料**整体准确度约 85%**，五层隔离模型、双轨控制（RBAC×MAC）、组为核心、代码数据同栖、审计治理等核心机制描述正确，与 Palantir 官方文档高度一致。但存在 **3 处事实性错误、5 处过时描述、4 处过度演绎**，需修正后才能作为可靠的设计参考。材料在**完整性**上覆盖全面（10 轮六维度展开），在**详细度**上达到生产级落地水准，反模式与避坑指南尤其有价值。

## 1.1 事实性错误（必须修正）

### 错误 1：❌「Organization 标记是『或』关系，普通 Marking 是『且』关系」——过度演绎，官方无此区分

**材料原文**（第1轮）：
> 「组织标记之间是『或』的关系（用户只要拥有其中一个即可），这与普通业务标记的『与』关系不同，是组织标记的特殊属性。」

**官方文档核对**（[Organizations and spaces](https://palantir.com/docs/foundry/security/orgs-and-spaces/)）：
> 「Organizations are **access requirements** applied to Projects that enforce strict silos... To meet access requirements, users must be a member or guest member of **at least one** organization applied to a Project. Organizations are inherited via the file hierarchy and direct dependencies.」
> 「Like markings, organizations are a **mandatory access control**. However, organizations differ from markings in a few key ways...」

**评审结论**：
- 官方明确说 Organizations 和 Markings **都是 MAC**，机制本质相同（都是 access requirements）
- 「at least one」描述的是 **Project 上挂多个 Organization 时的并集语义**（用户持有任一即可），这是「多 access requirement 叠加」的自然结果，**不是 Organization 独有的特殊属性**
- 材料把 Organization 等同于「系统级内置标记」且强调「或逻辑」是**过度演绎**。官方没有「组织标记是或、业务标记是与」这种二元区分的表述
- 真相：**多个 access requirement（无论 Org 还是 Marking）的叠加都是「且」逻辑**（用户须满足全部 requirement）；「至少持有一个 Org」是因为 Project 上的多 Org 是「同一 requirement 的多个候选值」而非「多个独立 requirement」

**修正建议**：删除「组织标记是或、业务标记是与」的区分表述。统一描述为：access requirements（Org + Marking + Classification）叠加时，用户须满足**全部** requirement；每个 requirement 内部若是多值（如多 Org），用户持其一即可。

---

### 错误 2：❌「Restricted View 是行级安全的唯一/核心方案」——已过时，Object/Property Security Policies 是推荐方案

**材料原文**（第5轮、总览）：
> 「行级安全通过 Restricted View（限制视图）实现... Restricted View 是 Foundry 行级安全的原生标准实现... 本体对象级安全直接继承底层数据源权限」

**官方文档核对**（[Object security policies](https://palantir.com/docs/foundry/object-permissioning/object-security-policies/) + [managing-object-security](https://palantir.com/docs/foundry/object-permissioning/managing-object-security/)）：
> 「Object security policies are **recommended over restricted views** for most use cases built on the Ontology. They provide **unified cell-level security, near-instantaneous policy updates, and support for streaming and branching**.」
> 「Object security policies allow you to configure view permissions on an object instance by configuring security policies on the object type, **independently of the permissions on the backing data source**.」
> Palantir 提供了专门的「Migrate from restricted views to object security policies」迁移工具。

**评审结论**：
- Restricted Views **仍存在**但已是**过渡/遗留方案**，官方明确推荐迁移到 Object/Property Security Policies
- 材料把 Restricted View 当唯一行级方案、未提及 Object Security Policies，是**重大遗漏**（材料里只在「列级安全」提到 MDO，没讲 Object Security Policy 这个主推方案）
- Object Security Policy 的关键能力材料完全没覆盖：
  - **独立于底层数据源**配置对象实例可见性（不依赖 Restricted View 的视图层过滤）
  - **统一 cell 级安全**（Object Policy = 行级 + Property Policy = 列级，两者组合）
  - **支持 streaming 和 branching**（Restricted View 对 branching 支持是实验性的，社区帖证实）
  - **近实时策略更新**（Restricted View 需重建视图，较慢）
  - **Mandatory Control Property**（把 Marking 作为对象属性，按行控制——这是行级 MAC 的现代实现）
  - **stop inheriting markings**（对象层可阻断标记继承，**直接推翻材料「项目内只能叠加不能阻断」的说法**）

**修正建议**：重写细粒度安全章节，以 Object/Property Security Policies 为主方案，Restricted View 降级为「遗留方案，正在迁移」。补充 Mandatory Control Property 机制。

---

### 错误 3：❌「项目内权限全量继承，子文件夹只能叠加标记不能阻断」——在 Ontology 对象层不成立

**材料原文**（第3轮、总览）：
> 「项目内不支持阻断继承：子文件夹、单数据集无法移除或降级项目级授予的角色权限... 项目内只能通过叠加安全标记来收紧权限，不能放宽」

**官方文档核对**（[Object security policies](https://palantir.com/docs/foundry/object-permissioning/object-security-policies/)）：
> 「In the **Markings** configuration, **stop inheriting** the `PII` and `VIP` markings so that users without those markings can see object instances.」
> 「The object security policy can then be further customized to **add new mandatory controls and remove inherited mandatory controls** that are no longer necessary.」

**评审结论**：
- 在**数据集/文件层**，材料说法基本成立（Project 权限全继承，文件夹只能加严）
- 但在 **Ontology 对象层**，Object Security Policy 明确支持「stop inheriting markings」和「remove inherited mandatory controls」——**可以阻断继承**
- 材料把这个约束绝对化，混淆了「文件系统层」和「Ontology 对象层」的权限模型差异

**修正建议**：区分两个层次。文件系统层（数据集/代码）权限全继承不可阻断；Ontology 对象层（Object Security Policy）支持阻断标记继承、自定义 mandatory controls。

### 错误 4：❌「自定义 Role Set 冻结，平台新增 operation 不会自动同步」——与官方文档相反

**材料原文**（归档 §3.2）：
> 「冻结机制：自定义角色集发布后冻结，平台新增原子权限不会自动同步，须手动添加（易踩坑：长期不维护导致功能缺失/安全漏洞）」

**官方文档核对**（[Manage roles](https://palantir.com/docs/foundry/platform-security-management/manage-roles/)）：
> 「when copying the Project default role set or another role set that depends on the Project default role set, the newly copied role set will be **automatically updated with any role updates to the Project default role set**. As Foundry development continues, new roles may be added by Palantir; **receiving these permission updates automatically can reduce future administrative work**.」

**评审结论**：
- 材料说法与官方**完全相反**——官方明确说复制自 Project default 的 role set **会自动同步**平台更新
- 正确机制：
  - 复制自 Project default（或依赖它的）role set → **自动同步**平台新增角色/操作
  - 完全独立（非复制自 Project default）的 role set → 不同步（但这不是"冻结"，是设计选择）
- 材料的"冻结""易踩坑""长期不维护导致功能缺失"是**过度演绎**，夸大了维护成本
- 此错误影响了 Gaia 评估报告（"自定义角色集舍弃理由：Palantir 冻结机制维护成本高"），已同步修正

**修正建议**：删除"冻结机制"表述。自定义 role set 的正确取舍依据是"一期无需自定义，默认四角色够用"，而非"冻结维护成本高"。

---

## 1.2 过时描述（需更新）

| # | 材料描述 | 官方现状 | 修正 |
|---|---------|---------|------|
| 1 | Restricted View 是行级安全主方案 | Object/Property Security Policies 是推荐方案，RV 在迁移 | 见错误 2 |
| 2 | 列级安全三种方案：列级标记 / MDO 属性权限 / 转换脱敏 | 官方主推 Property Security Policy（统一 cell 级），MDO 是补充，列级标记通过 Mandatory Control Property 实现 | 补充 Property Security Policy 为列级主方案 |
| 3 | Ontology Roles 是本体权限模型 | 官方标注「legacy authorization model」，已迁移到 Compass filesystem 权限 | 标注 Ontology Roles 为遗留，主推 Compass filesystem 权限 |
| 4 | 「空间前身名为 Namespace」 | 官方确认 Spaces rebranded from namespaces，但材料多处仍混用 | 统一用 Space，注明历史名 |
| 5 | Marking 管理仅 Marking Admin 全局集中 | 官方支持「标记分级授权管理」（不同分类由不同团队管），材料第4轮扩展能力提了但正文仍说「仅全局 Marking Admin」 | 正文与扩展能力对齐，支持分级管理 |

## 1.3 过度演绎 / 待核实（标注存疑）

| # | 材料说法 | 核实情况 | 处理 |
|---|---------|---------|------|
| 1 | Organization = 系统级内置标记，自动绑定用户与资源 | 官方说 Org 和 Marking 「都是 MAC」但「differ in key ways」：Org 保护范围包括 spaces/ontologies/projects/users/groups/tag categories/collections，且「individual resources cannot be tied to an organization」（与 Marking 相反）；未明确说「Org = 系统标记」 | 标注为「推断模型」，官方未明确说 Org 是系统标记的实现。实际 Org 是 access requirement 的一种，与 Marking 并列 |
| 2 | 主组织「永久不可变更」 | 官方未明确说「永久不可变」，只说「every user is a member of only one organization」 | 标注为「强约束」，但「永久不可变」是材料演绎 |
| 3 | Service User「不能被授予平台级管理角色」 | 官方未明确此限制，材料第6轮自行加入 | 标注为「最佳实践建议」而非「内核约束」 |
| 4 | 组嵌套「建议 ≤ 2 层」 | 官方未给出具体层数建议，材料自行建议 | 标注为「实践建议」而非「官方约束」 |

## 1.4 准确且高价值的部分（确认无误）

以下材料内容经官方文档核对**完全准确**，且对 Gaia 设计有高参考价值：

1. ✅ **五层隔离模型**（Org → Space → Project → Marking → 行/列）—— 官方 [Security overview](https://palantir.com/docs/foundry/security/overview/) + [Security glossary](https://palantir.com/docs/foundry/security/security-glossary/) 确认
2. ✅ **Project 是 primary security boundary** —— 官方原文「Projects are the primary security boundary in Foundry」
3. ✅ **代码数据同栖（Code-Data Colocation）** —— 官方 [Recommended Project structure](https://palantir.com/docs/foundry/building-pipelines/recommended-project-structure/) 确认
4. ✅ **Marking 布尔合取（AND）逻辑** —— 官方原文「a user must be a member of all Markings applied to a resource to access it」「Access to a Marking is binary (all-or-nothing)」
5. ✅ **Marking 血缘自动传播 + stop_propagating** —— 官方 [Markings](https://palantir.com/docs/foundry/security/markings/) 确认
6. ✅ **四级角色体系**（全局/空间本体/项目/应用）—— 官方 [Projects and roles](https://palantir.com/docs/foundry/security/projects-and-roles/) 确认
7. ✅ **组授权铁律**（权限授组不授人）—— 官方权限管理贯穿此原则
8. ✅ **默认拒绝 / 不可见即安全** —— 官方 [Security glossary](https://palantir.com/docs/foundry/security/security-glossary/)「If a user does not have access, they will not know the existence of the resource」
9. ✅ **PBAC（Purpose-Based Access Control）** —— 官方博客 [Purpose-based access controls at Palantir](https://blog.palantir.com/purpose-based-access-controls-at-palantir-f419faa400b3) 详述，三个设计目标（结构化清晰、治理集成、可审计）均确认
10. ✅ **CBAC（Classification-Based Access Control）** —— 官方 [CBAC](https://palantir.com/docs/foundry/security/classification-based-access-controls/) 确认「not enabled by default，需 Palantir 参与，用于政府敏感数据」
11. ✅ **Mandatory Control Property** —— 官方 [mandatory-control-properties](https://palantir.com/docs/foundry/object-link-types/mandatory-control-properties/) 确认（材料在错误2对应处遗漏，但概念本身存在）
12. ✅ **审计追加写入不可篡改 / 权责分离 / Check Access** —— 官方 [Data protection and governance](https://palantir.com/docs/foundry/security/data-protection-and-governance/) 确认

## 1.5 材料完整性与详细度评价

### 完整性：★★★★★（优秀）
- 10 轮覆盖：Org / Space / Project / Marking / 细粒度 / 身份 / 角色 / 授权引擎 / 审计治理 / 落地方法论，**无重大遗漏**
- 每轮六维度（底层原理 / 设计约束 / 标准配置 / 使用指导 / 反模式 / 扩展能力）结构完整
- 唯一遗漏：Object/Property Security Policies（见错误2），这是 2024+ 的主推方案

### 详细度：★★★★☆（优秀，有少量过时）
- 反模式与避坑指南（30 条）**极具价值**，均为生产级经验
- 数据四层项目架构、四维分组体系、集团级模板可直接套用
- 降分点：Restricted View / Ontology Roles 等过时内容未标注版本

### 准确性：★★★★☆（85%，需修正 4 处错误 + 5 处过时）
- 核心机制准确，错误集中在「Object Security Policy 未覆盖」「Org 标记逻辑过度演绎」「Role Set 冻结机制与官方相反」
- 修正后可作为可靠设计参考

---

# 第二部分：业界通用实践对照

> **研究目的**：Palantir 是闭源商业产品，其模型未必是唯一或最优解。本部分对照业界主流数据平台权限方案（Apache Ranger / Databricks Unity Catalog / Snowflake Horizon / Immuta / Google Zanzibar / OpenFGA / OPA Cedar / AWS ABAC），识别**通用共识**与**Palantir 独有选择**，为 Gaia 选型提供业界基准。

## 2.1 访问控制模型谱系（理论基础）

业界访问控制模型经 NIST 标准化（[NIST SP 800-162](https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-162.pdf)），形成如下谱系：

| 模型 | 全称 | 核心机制 | 优势 | 劣势 | 典型实现 |
|------|------|---------|------|------|---------|
| **DAC** | Discretionary Access Control | 资源所有者自主授权 | 灵活 | 依赖人的管理水平，易误授权 | Linux 文件权限、Palantir Project |
| **MAC** | Mandatory Access Control | 集中强制管控，标签驱动，用户不可绕过 | 安全底线高，合规 | 不灵活，管理成本高 | 军用系统、Palantir Marking、SELinux |
| **RBAC** | Role-Based Access Control | 权限打包成角色，用户通过角色获权 | 易理解，管理集中 | **Role Explosion**（角色数随维度组合爆炸） | Palantir Project 角色、传统企业系统 |
| **ABAC** | Attribute-Based Access Control | 基于用户/资源/环境属性动态求值 | 细粒度，可扩展，无角色爆炸 | 策略复杂，求值开销 | AWS IAM ABAC、Databricks Unity Catalog、Immuta、Apache Ranger Tag-based |
| **PBAC** | Purpose-Based Access Control | 按使用目的授权，须声明 rationale | 合规（GDPR 最小必要） | 流程重，体验有损 | Palantir PBAC |
| **ReBAC** | Relationship-Based Access Control | 权限 = 关系图遍历（用户↔资源↔资源） | 表达层级/共享/继承天然 | 关系图维护成本，复杂查询 | Google Zanzibar、OpenFGA、SpiceDB、GitHub 权限 |
| **CBAC** | Classification-Based Access Control | 按数据密级分类标签强制管控 | 政府/军用合规 | 需定制，非通用 | Palantir CBAC（政府场景） |

### 关键共识（业界趋势）
1. **ABAC 是规模化方向**：GigaOm 报告（虽有争议）显示 ABAC 比 RBAC 减少 **75x** 策略变更（[Immuta vs Ranger](https://www.immuta.com/resources/gigaom-report-immuta-vs-apache-ranger/)）。Apache Ranger PMC [反驳](https://news.apache.org/foundation/entry/apache-ranger-response-to-incorrect)称对比不公，但 **ABAC 解决 role explosion 是业界共识**（[Axiomatics](https://axiomatics.com/blog/three-rbac-challenges-solved-with-abac)、[NIST SP 800-162](https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-162.pdf)）
2. **实际系统多为混合模型**：「RBAC 管粗粒度协作授权 + ABAC/Tag-based 管细粒度数据访问」是 2026 年 B2B SaaS 主流（[CIAM Compass](https://guptadeepak.com/ciam-compass/guides/rbac-vs-abac-vs-rebac/)）
3. **ReBAC 适合层级/共享场景**：Google Zanzibar 在 2 万亿 ACL、10ms 内求值（[论文](https://www.usenix.org/system/files/atc19-pang.pdf)），适合文件系统/文档协作的权限继承，但数据平台的行/列级安全仍以 ABAC 为主
4. **Policy-as-Code 外部化**：把授权逻辑从业务代码抽离到独立策略引擎（OPA/Cerbos/Cedar），是工程最佳实践（[OpenFGA](https://openfga.dev/docs/learn/policy-engine)）

## 2.2 主流数据平台权限方案横向对照

| 维度 | Palantir Foundry | Apache Ranger | Databricks Unity Catalog | Snowflake Horizon | Immuta | Google Zanzibar |
|------|------------------|---------------|-------------------------|-------------------|--------|-----------------|
| **定位** | 闭源企业数据平台 | 开源 Hadoop 生态安全 | 闭源 Lakehouse 治理 | 闭源云数仓治理 | 闭源数据治理层 | 通用授权服务（非数据平台） |
| **主模型** | RBAC(Project角色) + MAC(Marking) + PBAC | RBAC + Tag-based(OT-RBAC) | RBAC + **ABAC(tag+policy)** | RBAC + Tag-based masking/RLS | **纯 ABAC**(tag+attribute) | **ReBAC**(关系图) |
| **多租户隔离** | Organization（MAC） | 无原生（靠 policy） | Catalog 层级 | 账户/角色 | 无原生 | namespace |
| **行级安全** | Object Security Policy / Restricted View | Row Filter policy | **ABAC row filter policy**（UDF） | Row Access Policy | Data Policy (row filter) | N/A（关系层） |
| **列级安全** | Property Security Policy / MDO / 列级标记 | Column Masking policy | **ABAC column mask policy**（UDF） | Masking Policy（tag-based） | Data Policy (column mask) | N/A |
| **标签传播** | Marking 血缘自动传播 | Tag（Atlas 同步） | Governed Tag（手动/自动） | Object Tagging（手动/classification） | Tag（Data Source 关联） | N/A |
| **策略下推** | 视图层 / 对象层 | 插件拦截各引擎 | **UDF 运行时求值** | 策略编译进查询 | 运行时改写 SQL | 关系图遍历 |
| **外部化** | 中心化授权引擎 | 中心化 Policy Server | Unity Catalog 服务 | Horizon 服务 | 独立治理层 | 独立 Zanzibar 服务 |
| **开源** | ❌ | ✅ Apache 2.0 | ❌ | ❌ | ❌ | ❌（论文开源，实现闭源；OpenFGA/SpiceDB 开源） |
| **典型适用** | 企业级全栈 | Hadoop/Trino/Spark 生态 | Databricks Lakehouse | Snowflake 数仓 | 跨平台数据治理 | 通用授权（Drive/Docs/Cloud） |

### 各方案深度点评

#### Apache Ranger（开源，Hadoop 生态事实标准）
- **核心**：中心化 Policy Server + 各组件插件（Hive/HDFS/HBase/Trino/Kafka...），策略含 RBAC + Tag-based（TBAC）+ Row Filter + Column Masking
- **Tag-based 关键创新**（[Ranger Tag-based policies](https://ranger.apache.org/blogs/policy_model.html)）：**分离「资源分类」与「访问授权」**——数据管理员打 PII 标签，安全管理员写 PII 策略，权责分离
- **与 Trino 集成**：Ranger 有 Trino 插件，行级过滤/列脱敏在 Trino 查询时拦截（[Zenodo 案例](https://zenodo.org/records/19473036)）
- **劣势**：RBAC 为主，role explosion；策略管理重（GigaOm 报告，Ranger PMC 反驳）；Tag 同步依赖 Atlas，链路复杂
- **对 Gaia 启示**：Gaia 用 Trino 联邦查询，Ranger Trino 插件是现成的行级/列级方案，但开源 Ranger 部署重

#### Databricks Unity Catalog（ABAC 主推，2024+ 趋势）
- **核心**：RBAC（GRANT）+ **ABAC（governed tag + 3 类 policy：row filter / column mask / GRANT）**
- **ABAC 求值两阶段**（[policy-evaluation](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/policy-evaluation)）：① Unity Catalog 策略求值 ② Databricks Runtime 运行时执行（UDF）
- **Catalog 层级级联**：catalog → schema → table，策略自动下钻，无需逐表配置（[How to scale](https://www.databricks.com/blog/how-scale-data-governance-attribute-based-access-control-unity-catalog)）
- **关键约束**：ABAC policy **不授予权限，只增加限制**——基础表访问仍须 GRANT（[abac-vs-rls-cm](https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/abac/abac-vs-rls-cm)）
- **对 Gaia 启示**：ABAC tag-driven 是规模化方向；「policy 只收紧不放宽」与 Palantir Marking 一致；Catalog 层级级联减少配置量

#### Snowflake Horizon（云数仓治理）
- **核心**：RBAC（角色层级）+ Tag-based Masking Policy + Row Access Policy + Object Tagging + AI Classification
- **Tag-based Masking**（[tag-based-masking-policies](https://docs.snowflake.com/en/user-guide/tag-based-masking-policies)）：tag 挂列上，masking policy 挂 tag 上，**新增同 tag 列自动生效**——规模化关键
- **动态脱敏**：查询时求值，不修改原数据（[security-column-intro](https://docs.snowflake.com/en/user-guide/security-column-intro)）
- **ACCESS_HISTORY**：审计数据访问，精确到列
- **对 Gaia 启示**：tag-based masking 的「打标自动生效」模式比逐列配置高效；动态脱敏（不落盘）是最佳实践

#### Immuta（独立数据治理层，纯 ABAC）
- **核心理念**：**decouple access decisions from user and data identities**（解耦，用属性+标签驱动）
- **三种 data policy**（[data-policy-overview](https://documentation.immuta.com/2025.1/governance/author-policies-for-data-access-control/authoring-policies-in-secure/data-policies/data-policy-overview.md)）：row-level / column masking / **cell masking**（按行内其他值决定 cell 脱敏）
- **Global Subscription Policy**：基于用户元数据 + 数据元数据自动求值，**非角色驱动**
- **least privilege 强制**：policy 对所有人生效，无例外默认
- **对 Gaia 启示**：纯 ABAC 的「属性驱动」比 RBAC 角色管理轻；cell-level masking（行×列交叉）是高级能力

#### Google Zanzibar / OpenFGA / SpiceDB（ReBAC，关系图授权）
- **核心**：权限 = 关系图遍历。`(object, relation, user)` tuple 存储，Check 请求遍历关系图求值
- **Zookie / new-enough token**：一致性保证（[Zanzibar 论文](https://www.usenix.org/system/files/atc19-pang.pdf) §5）——内容版本绑定 ACL 版本，避免用旧 ACL 求新内容
- **Leopard 索引**：深度嵌套关系（如组套组）的反向索引，避免递归遍历爆炸
- **2 万亿 ACL / 10ms 求值**：外部一致性（Spanner 支持）+ 多级缓存
- **适用场景**：层级继承（文件夹→文件）、共享（doc→user）、团队协作——**数据平台的行/列级安全不是 ReBAC 强项**，但 ObjectType 间的 Link 关系权限可考虑
- **对 Gaia 启示**：Gaia 有 Neo4j 图数据库 + object_links 关系表，ReBAC 模型天然适合表达「对象间关系的可见性传递」，但行/列级安全仍应走 ABAC/tag-based

#### OPA / Cedar / Cerbos（Policy-as-Code 策略引擎）
- **核心**：策略用 DSL 表达（Rego / Cedar / YAML），引擎无状态求值，外部化于业务代码
- **Cerbos**：YAML 策略，**非工程师可读写**（[案例](https://www.cerbos.dev/blog/can-non-engineers-manage-authorization-policies-with-cerbos)），Policy-as-Code 入 Git
- **OPA**：Rego 语言，生态广，CNCF 毕业
- **Cedar**：AWS 推出，类型安全，验证友好
- **对 Gaia 启示**：策略外部化是工程最佳实践；YAML/可读 DSL 让安全团队直接维护策略，降低工程师介入

## 2.3 业界共识与 Palantir 独有选择

### 业界共识（Gaia 应遵循）
1. **RBAC + ABAC 混合**：RBAC 管协作授权（谁能进项目/操作），ABAC/Tag-based 管数据访问（能看哪些行/列）——所有主流平台都是此模式
2. **Tag-based 是规模化关键**：Databricks/Snowflake/Immuta/Ranger 都用 tag 驱动，避免逐资源配置
3. **策略只收紧不放宽**：ABAC policy 不授予权限只增加限制（Databricks 明确），与 Palantir Marking 一致
4. **动态脱敏不落盘**：Snowflake/Databricks/Immuta 都在查询时求值，不修改原数据
5. **行级 + 列级 = cell 级**：Immuta/Databricks/Palantir 都支持 cell 级（行×列交叉）
6. **策略外部化（Policy-as-Code）**：OPA/Cerbos/Cedar 趋势，授权逻辑独立于业务代码
7. **默认拒绝 + 不可见即安全**：业界安全基线
8. **审计强制 + 不可篡改**：合规底线

### Palantir 独有选择（Gaia 需评估是否跟随）
1. **Organization 作为顶层 MAC 边界**——多数数据平台无此层（用 catalog/账户隔离代替），Palantir 因面向多主体企业协作才需要
2. **Space ↔ Ontology 一一强绑定**——多数平台 ontology 概念弱或无，此约束是 Palantir 特有
3. **代码数据同栖（Code-Data Colocation）**——依赖 Spark Transform 体系，非 Spark 平台不适用
4. **PBAC（Purpose-Based）**——Palantir 独创，面向强合规（GDPR），多数平台不内置
5. **CBAC（Classification-Based）**——政府/军用，需 Palantir 参与，非通用
6. **Project 权限全继承不可阻断（文件层）**——Palantir 特有设计，多数平台支持更灵活的子资源权限

## 2.4 权限引擎架构对照（PDP/PIP/PEP）

业界授权引擎标准化为三角色（[XACML](https://docs.oasis-open.org/xacml/3.0/xacml-3.0-core-spec-os-en.html) 模型）：

| 角色 | 全称 | 职责 | Palantir | Ranger | Databricks | OPA/Cerbos |
|------|------|------|----------|--------|------------|------------|
| **PDP** | Policy Decision Point | 求值策略，返回允许/拒绝 | 中心化授权引擎 | Policy Server | Unity Catalog 服务 | OPA/Cerbos 引擎 |
| **PIP** | Policy Information Point | 提供求值所需数据（用户属性/资源属性） | 属性解析模块 | Atlas/UserStore | Catalog 元数据 | 调用方传入 |
| **PEP** | Policy Enforcement Point | 执行点，拦截请求调用 PDP | 各 Service/引擎 | 各组件插件 | Runtime UDF | 应用中间件 |

**关键架构选择**：
- **有状态（ReBAC）vs 无状态（Policy Engine）**（[OpenFGA 分析](https://openfga.dev/docs/learn/policy-engine)）：
  - 无状态（OPA/Cedar）：引擎不存数据，调用方传入所有求值所需数据，适合属性驱动的 ABAC
  - 有状态（OpenFGA/SpiceDB/Zanzibar）：引擎即数据库，存储关系图，适合关系驱动的 ReBAC
  - **Palantir 是有状态**（缓存用户/资源属性 + 授权结果），因为有血缘传播和复杂属性解析

---

# 第三部分："复杂留给自己，简单留给用户"设计哲学

> **研究目的**：Palantir 的权限体系以**复杂著称**（五层 + 双轨 + 四级角色 + PBAC...），用户配置成本高。Gaia 的第一原则是「把复杂留给自己，把简单留给用户」。本部分研究如何在保证安全底线的前提下，**最大化降低用户的权限配置与认知负担**，避免重蹈「安全干扰工作」覆辙。

## 3.1 理论基础：可用安全（Usable Security）

### 3.1.1 HP 论文：让策略决策消失在工作流中

HP 实验室 Karp & Stiegler《Making Policy Decisions Disappear into the User's Workflow》(CHI 2010, [HPL-2009-341](https://shiftleft.com/mirrors/www.hpl.hp.com/techreports/2009/HPL-2009-341.pdf)) 是可用安全的经典论文，核心论点：

> 「Complaints of security interfering with getting work done are commonplace. They often arise when users are distracted from their tasks to make policy decisions.」

**三大维度**（避免安全与可用性对立）：

| 维度 | 含义 | 失败后果 |
|------|------|---------|
| **Information（信息）** | 给用户足够信息做出明智策略决策 | 用户不理解决策后果，盲目选择 |
| **Expressiveness（表达力）** | 支持用户工作所需的分享模式 | 用户找不到合法表达，绕道走 |
| **Control（控制）** | 让用户在工作流内表达决策，不打断任务 | 用户被打断，Just-Say-Yes 条件反射 |

**分享的六个方面**（物理世界有，在线系统常缺）：
1. Dynamic（动态，无需第三方批准）
2. Cross-domain（跨域，无单方主导）
3. Attenuated（衰减，能授权子集而非全部）
4. Chained（链式，可再授权）
5. Composable（可组合，多源权利合并）
6. Accountable（可追溯，谁授谁用）

**四原则**（让策略决策消失）：
1. 每个用户可单独控制的对象，在 UI 中用唯一可区分的 capability 表示
2. 每个可能的策略决策，在 UI 中表现为唯一的 affordance
3. 每个已做的策略决策，在 UI 中用唯一可区分的 capability 表示
4. 每个对已做决策的修改，在 UI 中表现为唯一的 affordance

**核心洞察**：「从用户的**指代动作**（acts of designation）**推断**其授权意图，而非打断询问」。例：用户把文件图标拖到编辑器图标 = 授权编辑器读该文件，无需弹窗询问。

### 3.1.2 其他可用安全原则

- **Yee《Guidelines for Secure Interaction Design》**：对象与动作用「可区分、真实」的外观呈现
- **Garfinkel《Simultaneously Secure and Usable》**：安全与可用无内在冲突，问题在实现方式
- **Eight Lightweight Usable Security Principles**（IEEE 2022）：给开发者的轻量框架
- **UACP 框架**（[Architecting Access by Design](https://kie.ie/docs/Architecting%20Access%20by%20Design.pdf)）：避免「安全难用」的伪二元对立

## 3.2 业界简化模式研究

### 3.2.1 策略外部化 + 可读 DSL（Cerbos 模式）

[Cerbos](https://www.cerbos.dev/blog/can-non-engineers-manage-authorization-policies-with-cerbos) 的实践证明：**YAML 策略让产品经理和安全团队也能读写**，非工程师可独立维护、审计授权策略。

```yaml
# Cerbos 策略示例（非工程师可读）
apiVersion: "api.cerbos.dev/v1"
resourcePolicy:
  resource: "order"
  rules:
    - actions: ["cancel"]
      effect: EFFECT_ALLOW
      roles: ["customer"]
      condition:
        match:
          expr: request.resource.attr.status == "PENDING"
```

**简化价值**：
- 策略即数据，入 Git 版本管理，可 diff/审查/回滚
- 非工程师直接维护，减少工程介入
- 策略一次定义，前后端共享（[Ship the policy, not the code](https://www.jayfreestone.com/writing/share-the-policy-not-the-code/)）

### 3.2.2 Ship the Policy, not the Code（前后端策略共享）

[Jay Freestone](https://www.jayfreestone.com/writing/share-the-policy-not-the-code/) 的核心论点：**不要在前后端各写一遍权限逻辑**，会 drift。三种共享方式：

1. **Ship the decision**：后端求值后返回 `allowedActions: ["CANCEL"]`，前端只渲染状态（HATEOAS 思想）
2. **Ship the policy**：序列化策略本身，前后端用共享 evaluator 求值（如 CASL/JSON Schema）
3. **Ship the reason**：返回 `{disabled: true, reason: "Orders can't be canceled once shipped"}`，前端展示原因

**简化价值**：前端不再重复推导权限，减少 bug；权限变更一处生效。

### 3.2.3 渐进式披露（Progressive Disclosure）

[Admin 工具的渐进式披露](https://koder.ai/blog/progressive-disclosure-admin-tools)：
- 默认视图匹配日常工作（快速查询、常规更新、清晰状态）
- 高级设置在明确需要时才出现（「Advanced」面板、「Edit」模式、需确认的独立流程）
- 最安全、最常用的控件先显示，强大/危险的控件后揭示

**简化价值**：避免一上来吓退用户；按需展开复杂度。

### 3.2.4 ABAC 替代 RBAC 减少 75x 策略变更

[Immuta/GigaOm 报告](https://www.immuta.com/resources/gigaom-report-immuta-vs-apache-ranger/)：ABAC 比 RBAC 减少 **75x** 策略变更（Ranger PMC [反驳](https://news.apache.org/foundation/entry/apache-ranger-response-to-incorrect)对比不公，但 ABAC 减负是共识）。

**根因**：RBAC 的 role explosion——「50 项目 × 3 环境 = 150 角色组合」（[LinkedIn 案例](https://www.linkedin.com/pulse/rbac-obsolete-learn-abac-tag-based-policy-ngoc-thien-nguyen-odwzc)）。ABAC 用属性驱动，一条策略覆盖海量场景。

**简化价值**：策略数量从「N 角色 × M 资源」降到「少量属性规则」，管理成本骤降。

### 3.2.5 标签驱动 + 自动生效（Snowflake/Databricks 模式）

[Snowflake tag-based masking](https://docs.snowflake.com/en/user-guide/tag-based-masking-policies) + [Databricks ABAC](https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/)：
- masking policy 挂在 tag 上，新增同 tag 列**自动生效**，无需逐列配置
- Catalog 层级策略自动下钻到 schema/table

**简化价值**：打标即保护，零增量配置。

### 3.2.6 Just-in-Time 权限（Apono/P0 模式）

[Apono](https://www.apono.io/) / [P0](https://p0.dev/technology/authz-control-plane/)：
- 替换 standing privilege（常驻权限）为运行时动态授权
- 按需申请、自动审批、到期自动回收
- 「just-enough privilege, just-in-time access」

**简化价值**：用户无需常驻高权限，按需获取；管理员无需预授权海量场景。

### 3.2.7 默认安全 + 自动推导（Encore 模式）

[Encore](https://encore.dev/features/iam)：
- 分析代码自动生成最小权限 IAM 策略
- 每个服务恰好获得所需权限，无多余
- 「Least-privilege by default」

**简化价值**：用户不手动配置权限，系统自动推导。

## 3.3 Gaia 应用的简化设计原则（提炼）

基于 HP 论文 + 业界实践，提炼 Gaia 权限特性的简化设计原则（**供后续设计参考，非本轮落地决策**）：

### 原则 1：从用户动作推断授权意图，而非打断询问（HP 核心）
- 用户「把对象加入看板」= 授权查看该对象，无需弹窗
- 用户「分享场景给同事」= 授权该同事访问场景，无需单独配权限
- 避免「Just-Say-Yes」条件反射——少弹窗，多推断

### 原则 2：策略即数据，前后端共享（Ship the Policy）
- 权限规则序列化（YAML/JSON），一处定义处处可用
- 后端返回 `allowedActions` + `disabledReasons`，前端只渲染不推导
- 避免前后端各写一遍权限逻辑导致 drift

### 原则 3：ABAC/Tag 优先，RBAC 兜底（避免 role explosion）
- 数据访问（行/列级）用 tag/属性驱动，一条策略覆盖海量资源
- 协作授权（谁能进项目）用 RBAC，角色数严格控制
- 避免「N 部门 × M 角色」的组合爆炸

### 原则 4：打标即保护，自动传播（Snowflake/Databricks 模式）
- 给数据打 PII 标记 → 所有衍生数据自动继承保护
- 新增同标记列/对象自动生效，零增量配置
- 血缘传播自动化，用户无需逐资源配置

### 原则 5：渐进式披露，默认最简（Progressive Disclosure）
- 默认视图：日常用例（查看、查询、执行已授权 Action）
- 高级面板：标记管理、行级策略、角色配置（需明确进入）
- 默认安全 + 默认最简：新用户默认最小权限，按需升级

### 原则 6：Just-in-Time 权限，减少常驻授权
- 临时需求走自助申请 + 自动审批 + 到期回收
- 避免为临时场景预授常驻高权限
- 与 HR/身份源联动，入职自动授权、离职自动失效

### 原则 7：默认安全 + 不可见即安全，但提供「为什么」
- 无权限资源默认隐藏（不提示「无权限」，防枚举）
- 但当用户主动尝试访问被拒时，提供**可读的拒绝原因** + **申请权限的入口**
- 平衡「不可见即安全」与「用户能理解为什么」

### 原则 8：系统承担复杂，用户只表达意图
- 血缘传播、策略下推、缓存一致性、多引擎适配——系统内部复杂
- 用户侧只表达业务意图：「这是 PII 数据」「销售只能看本区域客户」
- 用 LLM 辅助策略生成：用户说自然语言，系统转成结构化策略（对齐 Gaia AI 原生定位）

### 原则 9：可解释性工具（Check Access 模式）
- 任何权限拒绝都可解释：哪一层拦截、缺什么权限、如何获取
- 权限模拟：给用户加某组后能看到什么，预判授权效果
- 避免权限成为黑盒

### 原则 10：分权治理，但流程自动化
- 标记管理员/项目管理员/审计员三权分立（安全底线）
- 但申请-审批-授权-回收全流程自动化，减少人工瓶颈
- 治理左移：在数据接入时自动打标，而非事后补标

---

# 第四部分：综合结论与 Gaia 落地评估锚点

> ⚠️ 本部分仅给出**评估锚点**，不含 Gaia 落地方案（用户明确要求本轮不涉及）。

## 4.1 材料可信度结论

| 项 | 评级 | 说明 |
|----|:---:|------|
| Palantir 机制描述准确性 | ★★★★☆ | 85%，4 处错误 + 5 处过时需修正 |
| 完整性 | ★★★★★ | 10 轮覆盖全面，仅遗漏 Object Security Policy |
| 详细度 | ★★★★☆ | 生产级，反模式尤有价值 |
| **作为设计参考可靠性** | **★★★★☆** | **修正错误后可作可靠参考** |

## 4.2 业界基准结论

Gaia 落地权限特性时，业界基准如下：

1. **模型选型**：RBAC（协作授权）+ ABAC/Tag-based（数据访问）混合，是所有主流平台共识。Palantir 的 PBAC/CBAC 是强合规场景的可选项，非必需。
2. **行/列级安全**：tag-driven + 动态脱敏（不落盘）+ cell 级（行×列交叉）是业界标准。Databricks ABAC policy + Snowflake tag-based masking 是最佳参考。
3. **多租户隔离**：是否需要 Organization 层取决于 Gaia 定位。若开源本地优先（单租户为主），可降级；若面向 SaaS 多主体，需 Organization 级 MAC。
4. **策略引擎**：外部化（Policy-as-Code）是工程最佳实践。可参考 OPA/Cerbos，或自建轻量策略层。
5. **标签传播**：血缘自动传播是高价值能力（Palantir Marking / Ranger Tag），但实现成本高（需血缘引擎）。可分期：先手动打标，后自动传播。

## 4.3 简化设计结论

Gaia 权限特性的简化方向（业界验证）：

1. **ABAC/Tag 优先于 RBAC**：避免 role explosion，一条策略覆盖海量场景
2. **打标即保护 + 自动传播**：零增量配置，但需血缘引擎支撑
3. **策略即数据 + 前后端共享**：避免 drift，一处定义处处可用
4. **Just-in-Time 权限**：减少常驻授权，按需获取
5. **从动作推断意图**（HP 核心）：少弹窗，多推断
6. **LLM 辅助策略生成**：自然语言 → 结构化策略（对齐 Gaia AI 原生定位，业界尚无成熟实践，是 Gaia 差异化机会）

## 4.4 Gaia 现状的关键约束（评估时需考虑）

以下约束来自 Gaia 现有架构（见 [implementation-status.md](../architecture/implementation-status.md)），影响权限特性落地选型：

1. **多引擎**：PG（元数据+object_state）/ Doris（在线读主源）/ Iceberg（全量明细）/ Trino（联邦）/ Neo4j（图）—— 权限下推需分别适配各引擎能力（Doris 行级策略、PG RLS、Trino Ranger 插件、Neo4j 权限）
2. **ActionAuthorizer 已有 Action 三层权限**（ADR-011，存 JSON）—— 可复用为权限体系起点
3. **principal=anonymous** —— 身份体系需从零搭建
4. **object_state OCC + outbox** —— 权限变更可与 outbox 联动异步生效
5. **Scenario 设计已就绪**（scenario-*.md）—— 权限需与 Scenario overlay 读写语义协同
6. **开源本地优先定位** —— Organization 多租户层需求程度待评估
7. **无 Spark/Transform 体系** —— 代码数据同栖、Reference 等 Palantir 特有概念需重新映射或舍弃

## 4.5 待决策的关键问题（供下一轮设计输入）

1. **是否引入 Organization 多租户层？** 取决于 Gaia 是否面向多主体 SaaS。开源本地优先可降级为单租户 + 项目级隔离。
2. **行/列级安全如何下推多引擎？** Doris 有 [Row Policy/Column Permission/Data Masking](https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/)，PG 有 RLS，Trino 可挂 Ranger，Neo4j 有权限模型。需统一抽象。
3. **标签传播是否一期做？** 血缘自动传播价值高但成本高。可分期：一期手动打标 + 标记校验，二期血缘传播。
4. **策略引擎自建还是引入 OPA/Cerbos？** 自建轻量可控但重复造轮子；引入 OPA 生态好但增加部署复杂度。
5. **PBAC 是否纳入？** 面向强合规场景的可选项，非必需。可按需迭代。
6. **身份体系如何对接？** 对接 OIDC/LDAP 还是自建用户系统？影响 Group/Role 模型设计。
7. **LLM 辅助策略生成的边界？** 哪些策略用 LLM 生成（自然语言→结构化），哪些保持确定性配置？需平衡灵活性与可靠性。

---

## 附录：研究来源索引

### Palantir 官方文档（第一手，已核对）
- Security overview: https://palantir.com/docs/foundry/security/overview/
- Organizations and spaces: https://palantir.com/docs/foundry/security/orgs-and-spaces/
- Projects and roles: https://palantir.com/docs/foundry/security/projects-and-roles/
- Markings: https://palantir.com/docs/foundry/security/markings/
- Restricted views: https://palantir.com/docs/foundry/security/restricted-views/
- Object security policies: https://palantir.com/docs/foundry/object-permissioning/object-security-policies/
- Managing object security: https://palantir.com/docs/foundry/object-permissioning/managing-object-security/
- Mandatory control properties: https://palantir.com/docs/foundry/object-link-types/mandatory-control-properties/
- Granular policies: https://palantir.com/docs/foundry/platform-security-management/manage-granular-policies/
- Classification-based Access Controls: https://palantir.com/docs/foundry/security/classification-based-access-controls/
- Security glossary: https://palantir.com/docs/foundry/security/security-glossary/
- Data protection and governance: https://palantir.com/docs/foundry/security/data-protection-and-governance/
- Recommended Project structure: https://palantir.com/docs/foundry/building-pipelines/recommended-project-structure/

### Palantir 官方博客/白皮书
- Purpose-based access controls: https://blog.palantir.com/purpose-based-access-controls-at-palantir-f419faa400b3
- Foundry Technical Overview v4: https://www.palantir.com/assets/.../FfB_Technical_Overview_v4.pdf
- Foundry 2022 Whitepaper: https://www.palantir.com/assets/.../Whitepaper_-_Foundry_2022.pdf

### 业界方案（官方文档）
- NIST SP 800-162 (ABAC 标准): https://nvlpubs.nist.gov/nistpubs/specialpublications/nist.sp.800-162.pdf
- Google Zanzibar 论文: https://www.usenix.org/system/files/atc19-pang.pdf
- Apache Ranger Policy Model: https://ranger.apache.org/blogs/policy_model.html
- Apache Ranger Tag-based: https://ranger.apache.org/blogs/adventures_in_abac_1.html
- Databricks Unity Catalog ABAC: https://docs.databricks.com/aws/en/data-governance/unity-catalog/abac/
- Databricks ABAC vs RLS/CM: https://learn.microsoft.com/en-us/azure/databricks/data-governance/unity-catalog/abac/abac-vs-rls-cm
- Snowflake tag-based masking: https://docs.snowflake.com/en/user-guide/tag-based-masking-policies
- Snowflake row access policies: https://docs.snowflake.com/en/user-guide/security-row-intro
- Immuta data policy overview: https://documentation.immuta.com/2025.1/governance/author-policies-for-data-access-control/authoring-policies-in-secure/data-policies/data-policy-overview.md
- OpenFGA ReBAC: https://openfga.dev/docs/learn/rebac
- OpenFGA Policy vs Relationship engine: https://openfga.dev/docs/learn/policy-engine
- AWS ABAC: https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction_attribute-based-access-control.html
- Apache Doris Data Access Control: https://doris.apache.org/docs/4.x/admin-manual/auth/authorization/data/

### 可用安全与简化哲学研究
- HP Making Policy Decisions Disappear (Karp & Stiegler 2009): https://shiftleft.com/mirrors/www.hpl.hp.com/techreports/2009/HPL-2009-341.pdf
- Ship the policy, not the code (Jay Freestone): https://www.jayfreestone.com/writing/share-the-policy-not-the-code/
- Cerbos non-engineer policy authoring: https://www.cerbos.dev/blog/can-non-engineers-manage-authorization-policies-with-cerbos
- Cerbos evaluation framework: https://www.cerbos.dev/blog/framework-evaluating-authorization-providers-solutions
- Progressive disclosure admin tools: https://koder.ai/blog/progressive-disclosure-admin-tools
- Eight Lightweight Usable Security Principles (IEEE 2022): https://doi.org/10.1109/msec.2022.3205484
- Architecting Access by Design (UACP): https://kie.ie/docs/Architecting%20Access%20by%20Design.pdf
- Yee Guidelines for Secure Interaction Design: https://digitalassets.lib.berkeley.edu/techreports/ucb/text/CSD-02-1184.pdf

### 对比分析报告
- GigaOm Immuta vs Ranger: https://www.immuta.com/resources/gigaom-report-immuta-vs-apache-ranger/
- Apache Ranger PMC response: https://news.apache.org/foundation/entry/apache-ranger-response-to-incorrect
- CIAM Compass RBAC vs ABAC vs ReBAC: https://guptadeepak.com/ciam-compass/guides/rbac-vs-abac-vs-rebac/
- Zanzibar deep dive: https://dev.to/kanywst/google-zanzibar-deep-dive-handling-2-trillion-acls-in-under-10ms-f06
- Ranger + Trino engineering: https://zenodo.org/records/19473036
