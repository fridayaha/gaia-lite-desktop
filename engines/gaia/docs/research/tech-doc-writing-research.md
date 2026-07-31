# 技术文档写作方法论研究报告

> **目的**：为 Gaia 文档工程（系统讲解需求目标 / 架构设计 / 选型 / 特性 / 使用指导）提供方法论依据。
> 本报告汇总 2026-07-13 对业界文档写作规范、组织框架、Palantir 范式、优秀案例的深度研究结论。
> 所有结论均带出处，便于复核。

---

## 一、核心框架：四模式分类法（Diátaxis / Divio）

### 1.1 是什么

**Diátaxis**（前身 Divio Documentation System，作者 Daniele Procida，在 Divio 公司时创立，后独立为 diataxis.fr）是当前业界最受推崇的文档信息架构框架。Canonical（Ubuntu 母公司）已将其作为全部文档的基础。核心论断：

> 文档不是一种东西，而是四种——**tutorials / how-to guides / reference / explanation**。每种对应读者在不同阶段的不同需求，必须用不同方式写，且彼此严格分离。

### 1.2 四模式定义（务必分清）

| 模式 | 读者需求 | 读者状态 | 写作取向 | 类比 |
|------|---------|---------|---------|------|
| **Tutorials（教程）** | 学习（learning） | 新手，需要被牵着走 | **做**——在指导下完成一个学习体验 | 烹饪课 |
| **How-to guides（操作指南）** | 解决具体问题（doing） | 有基础，要达成某个目标 | **步骤**——导向实际结果 | 菜谱 |
| **Reference（参考）** | 查信息（information） | 工作/排障中，要快速查准 | **描述**——准确、完备、无废话 | 百科词条 |
| **Explanation（解释/讨论）** | 理解（understanding） | 想弄清为什么、背后的权衡 | **讨论**——背景、设计选择、 alternatives | 文学评论 |

### 1.3 关键区分（最容易混的两对）

- **Tutorial vs How-to**：Tutorial 是"学"（带新人走一遍，不求实用求建立信心）；How-to 是"用"（解决真实问题，假设读者已会基础）。Tutorial 永远是"在实践中学习"，How-to 永远是"为达成目标"。
- **Reference vs Explanation**：Reference 描述系统"是什么"（枯燥、结构化、不教学）；Explanation 讨论"为什么"（有观点、有背景、有取舍）。把设计理由塞进 Reference 会污染它；把 API 细节塞进 Explanation 会稀释它。

### 1.4 四象限的位置关系

Diátaxis 把四模式放在两个轴上：
- **纵轴**：action（做）↔ knowledge（知）→ Tutorials/How-to 在"做"，Reference/Explanation 在"知"
- **横轴**：work（实际工作）↔ study（学习研究）→ How-to/Reference 在"工作"，Tutorials/Explanation 在"学习"

这套关系决定了文档该放哪一象限，不是凭感觉。

### 1.5 对 Gaia 的直接启示

Gaia 现有 80 篇文档几乎全是 **Explanation + Reference**（architecture/design/ADR/ICD），严重缺 **Tutorials** 和 **How-to**——这正是 PostgreSQL 文档被诟病的同一种病（HN 共识："解释了各部件是干什么的，但没解释如何用它们达成特定目标"）。文档工程必须补齐这两个象限。

> 出处：diataxis.fr（四模式定义）；canonical.com/blog/diataxis-a-new-foundation-for-canonical-documentation（Canonical 落地）；newton.cx/~peter/2023/divio-documentation-system/（Diátaxis=Divio 考证）

---

## 二、叙事逻辑：金字塔原理（Minto Pyramid）

### 2.1 是什么 vs 不是什么（重要澄清）

技术文档圈常把两个东西混为一谈，必须分清：

- **金字塔原理（Minto Pyramid Principle）**：麦肯锡 Barbara Minto 提出。**结论先行 + MECE 纵向横向逻辑**。是一套完整的结构化思维/表达体系，源自咨询业。
- **倒金字塔（Inverted Pyramid）**：新闻业传统。"最重要的放最前面，读者随时可停止阅读仍获关键信息"。只是一种信息排序原则，比金字塔原理轻得多。

二者都主张"结论先行"，但金字塔原理多了 **MECE（互斥穷尽）+ 纵向疑问-回答链 + 横向同一层逻辑递进** 的完整骨架。

### 2.2 金字塔原理四要素

1. **结论先行（BLUF, Bottom Line Up Front）**：塔尖放中心结论，不铺垫。
2. **以上统下**：上层结论 = 下层论点的总结；下层论点必须回答上层引出的疑问（纵向疑问-回答链）。
3. **归类分组（MECE）**：同层论点互斥且穷尽（Mutually Exclusive, Collectively Exhaustive）。MECE 也是 Minto 发明的。
4. **逻辑递进**：同层论点按时间/结构/重要性三种顺序之一排列。

### 2.3 SCQA 序言框架

金字塔原理用 **SCQA** 展开序言，引出塔尖结论：
- **S (Situation) 背景**：读者认同的现状
- **C (Complication) 冲突**：现状中出现的矛盾（预期未达 / 流程不畅 / 隐患存在）
- **Q (Question) 疑问**：由背景+冲突自然引出的问题（该做什么 / 该怎么做 / 是否该做 / 为什么发生）
- **A (Answer) 回答**：就是金字塔的中心结论

### 2.4 对 Gaia 的直接启示

- 每篇文档**第一段就给结论**（这个组件是什么、解决什么问题、读者该关注什么），背景和细节后置。避免当前文档常见的"先铺三页架构再说到正题"。
- 用 SCQA 组织开篇：例如 Action 章节可写"业务需要原子地改对象状态（S）→ 但直接写多引擎会不一致（C）→ 如何保证一致？（Q）→ outbox 驱动同步（A）"。
- 同层小标题做到 MECE，避免"既有按组件分、又有按流程分"的混杂。

> 出处：mckinsey.com（Minto MECE）；strategyu.co/pyramid-principle-partone（SCQA+纵向横向详解）；daily.jovis.ai/technical-writing/conquering-complexity-the-pyramid-principle（技术文档落地）

---

## 三、架构文档专项：arc42 + C4 模型

### 3.1 arc42 模板（12 节）

arc42 是业界最通用的软件架构文档模板（Gernot Starke & Peter Hruschka，2005，开源），回答两个问题：**该文档化什么 / 该怎么文档化**。12 节：

1. Introduction & Goals（引言与目标，含质量目标）
2. Constraints（约束，法规/外部）
3. Context & Scope（上下文与范围，外部系统与接口）
4. Solution Strategy（解决方案策略，核心思想）
5. Building Block View（构建块视图，模块分层）
6. Runtime View（运行时视图，场景/时序）
7. Deployment View（部署视图）
8. Cross-cutting Concepts（横切概念，安全/日志/事务等）
9. **Architecture Decisions（架构决策，ADR 汇总）**
10. Quality Scenarios（质量场景）
11. Risks & Technical Debt（风险与技术债）
12. Glossary（术语表）

### 3.2 C4 模型（四层缩放视图）

C4（Context / Container / Component / Code）用 4 层抽象级别画架构图，从粗到细像 Google Maps 缩放：
- **L1 Context**：系统与外部角色/系统的关系（一页纸）
- **L2 Container**：系统内的部署单元（服务、DB、消息队列）
- **L3 Component**：每个 Container 内的组件/模块
- **L4 Code**：类/接口级（通常不画，靠代码自证）

### 3.3 arc42 + C4 + ADR 三者配合（业界共识）

- **arc42** 提供"该写哪些章节"的骨架
- **C4** 提供"架构图怎么分层画"的规范
- **ADR**（Architecture Decision Records）在 arc42 §9 汇总，单独成文件记录每条决策的 Context/Decision/Consequences
- 三者可无缝结合（C4 官方 FAQ 明确兼容 arc42；bitsmuggler/arc42-c4-... 是公认范例仓库）

### 3.4 对 Gaia 的直接启示

**Gaia 现状已经高度对齐这套体系**：ADR-001~017 + ICD-01~05 + architecture_overview/plan = arc42 §4/§5/§9 + C4 L1/L2 的雏形。缺口在：
- **arc42 §1 Introduction & Goals**：缺一篇讲清"为什么做 Gaia、解决什么业务问题、质量目标"的文档（这正是文档工程第一篇要补的）。
- **arc42 §6 Runtime View**：现有 data-flow-diagrams 部分覆盖，但缺以"场景时序"为主线的叙述。
- **arc42 §10/§11 Quality Scenarios / Risks**：降级策略矩阵有了，但质量场景（如"1000 Pod 并发查询 P99"）和风险/技术债清单未单独成文。
- **C4 规范化**：现有架构图是 ASCII，可保留但建议关键图用 C4 层级明确标注"这是 L1 还是 L2"。

> 出处：arc42.org/overview（12 节）；c4model.com/faq（C4↔arc42 兼容）；bitsmuggler.github.io/arc42-c4-software-architecture-documentation-example（范例）；docs.arc42.org/section-9（ADR 节）

---

## 四、概念先行 vs 任务先行（Concept vs Task）

### 4.1 两套并存的分类法

- **DITA/Atlassian/GitLab 三分类**：Concept（是什么/为什么）/ Task（怎么做）/ Reference（查什么）。比 Diátaxis 粗，没有把 Explanation 独立出来。
- **Diátaxis 四分类**：如上，多了 Explanation 且把 Tutorial 和 How-to 分开。
- 二者本质同源，Diátaxis 是更精细的演进。

### 4.2 研究证据：任务导向对开发者更有效

Meng et al. (2019) 的开发者 API 文档观察研究结论：**按任务（task）组织的文档比按信号类型（concepts/integrations/samples/cookbooks/api-reference）组织的更有效**，因为后者分类主观、因人而异。

### 4.3 但概念不能省

PostgreSQL 文档的教训反面证明：纯 reference + explanation（"各部件是干什么的"）而缺 how-to（"如何用它们达成目标"）会让读者迷路。**概念先行是基础，任务导向是主线，二者不可偏废**。

### 4.4 对 Gaia 的直接启示

- **主线走任务导向**：Use Cases / 操作指南按"我想做 X"组织，不按"这个组件支持 X/Y/Z"组织。
- **概念作为前置铺垫**：每篇 how-to 开头用 1-2 段 + 链接交代必要概念，不在 how-to 里展开概念（概念进 Explanation 象限）。
- **Reference 严格去叙事化**：API reference / 配置项 / schema 只列事实，不解释为什么（为什么进 ADR/Explanation）。

> 出处：docs.docmd.io/07/guides/content-ux/task-vs-concept（concept vs task）；atlassian.com/blog/it-teams/writing-great-docs-for-your-app（三分类）；docsgeek.io/blog/posts/task-based-api-docs.html（Meng 2019 研究）；news.ycombinator.com/item?id=36328466（PG 文档局限）

---

## 五、渐进式披露（Progressive Disclosure）

### 5.1 原理

源自 UX 设计（NN/G 经典定义）：**只呈现用户当下需要的信息，复杂度按需揭示**。对抗"vertical bloat"——文档随产品演进膨胀成一堵文字墙，读者被淹没。

### 5.2 在文档中的落地手段

- **分层导航**：主层只放最常用信息，次级/专家层通过折叠/跳转/二级导航触达。
- **章节内"先结论后细节"**：开头给 TL;DR，细节用 `<details>`、子页、附录承载。
- **按读者画像裁剪**：新人看快速上手，架构师看设计权衡，运维看部署/告警——同一主题多入口。
- **何时避免**：专家级读者需要全貌时，不要强行藏信息（NN/G 提示）。

### 5.3 与金字塔原理的关系

二者互补：金字塔原理是"纵向信息排序"（结论→支撑→证据），渐进式披露是"横向信息分层"（主→次→专家）。**金字塔管"一篇文章内怎么排"，渐进式披露管"一个主题跨页面怎么分层"**。

### 5.4 对 Gaia 的直接启示

Gaia 文档体量大（reference.md 430KB）、组件多（8 层 + 22 service + 25 连接器），必须用渐进式披露，否则读者一进来就溺亡：
- **三层阅读路径**：① 5 分钟概览（电梯演讲）→ ② 30 分钟架构与核心概念 → ③ 深度章节（按特性展开，链接到 ADR/ICD 原文）。
- **每个特性页统一结构**：开头"这是什么/为什么/何时用"（TL;DR）→ 概念 → 操作 → 深入（折叠/链接）。
- **绝不把 reference.md 430KB 整篇呈现**，拆成可导航的子页 + 交叉链接。

> 出处：nngroup.com/articles/progressive-disclosure（经典定义）；daily.jovis.ai/technical-writing/unlocking-clarity-...（文档落地）；developers.google.com/tech-writing/two/large-docs（Google 大文档组织）；quality.arc42.org/approaches/progressive-disclosure（arc42 视角）

---

## 六、Palantir 的叙事范式（一手研究）

### 6.1 Palantir 怎么讲 Ontology（架构中心页原文手法）

抓取了 `palantir.com/docs/foundry/architecture-center/ontology-system/` 全文，其叙事手法可提炼为五步：

1. **一句话定位 + 反常识立论**：开头 "The Ontology is the system at the heart of Palantir's architecture"，紧接着反常识声明 "**designed to represent the complex decisions of an enterprise, not simply the data**"（强调不是普通数据层）。
2. **三个行业场景立刻具象**：航空（flights/aircraft/crew）、医疗（patients/nurses/supplies）、军事（multinational forces）——用读者能共鸣的业务名词，不堆技术栈。
3. **四要素整合框架**：data + logic + action + security 四重整合，配两张图（本体全景图 + 四要素缩放图 + 读写回路图）。核心比喻："nouns (data) + verbs (actions)"。
4. **一个完整走查案例**：medical manufacturing 供应链，从供应商→产线→物流→客户，展示不同角色（生产团队/仓储/分析师）的安全 scope 差异，以及 action/logic 各自的权限粒度。
5. **三层分解收尾**：Language（建模语义+动作+逻辑）/ Engine（读写架构、事务、CDC）/ Toolchain（OSDK + DevOps），配一张 Language×{Data,Logic,Action,Security} 矩阵表。

### 6.2 Palantir 文档站的信息架构

- **foundry docs**（palantir.com/docs/foundry/）：按产品能力分区的 reference + how-to 混合
- **architecture center**：架构级 explanation（如 Ontology system 页）
- **build.palantir.com**：纯 tutorials（"Building with AIP" 系列，step-by-step）
- **learn.palantir.com**：培训课程（如 "ONTOLOGY 01: Understanding and Exploring Your Ontology"）
- 这正是 Diátaxis 四模式的工业级落地：docs=reference+how-to，architecture center=explanation，build=tutorials，learn=系统化教程。

### 6.3 Palantir 的核心叙事母题（值得复刻）

> **"Ontology = 组织的 API / 数字孪生，通过 data+logic+action+security 四重整合，让人类与 AI Agent 在运营决策上协作。"**

关键修辞：
- "not simply the data"（用否定句立异）
- "nouns and verbs"（用语法隐喻让技术概念可被业务人理解）
- "digital twin / cybernetic enterprise"（用一个愿景词收束）

### 6.4 对 Gaia 的直接启示

Gaia 自称"开源 Palantir Foundry 风格"，应**直接借鉴这套叙事母题**，但要做开源化改写：
- 保留"四重整合 data+logic+action+security"框架（Gaia 已对齐）。
- "nouns and verbs"隐喻可用。
- 场景具象手法必须复刻——Gaia 当前文档缺"航空/医疗/供应链"这类业务场景走查，通篇是技术组件。
- **但要去 Palantir 黑话**："cybernetic enterprise / kinetic / operational world"这类词在开源文档里会劝退读者，换成"语义大脑 + 安全手脚"这类已有表述。

> 出处：palantir.com/docs/foundry/architecture-center/ontology-system/（一手全文）；palantir.com/docs/foundry/ontology/core-concepts/；blog.palantir.com/connecting-ai-to-decisions-with-the-palantir-ontology

---

## 七、Docs-as-Code 工程实践

### 7.1 核心主张

文档像代码一样：用 Git 版本控制、Markdown 纯文本、PR 审查、CI/CD 发布。文档与代码同库、同审、同部署，解决"文档漂移"（doc drift）。

### 7.2 为什么对 Gaia 重要

Gaia 已天然满足 docs-as-code 前提：文档在 `docs/` 与代码同库、用 Markdown、有 git 历史。但当前缺：
- **文档审查流程**：改代码不一定触发对应文档 review（CLAUDE.md 已有"资料中心同步"规范但偏 admin 前端，未覆盖 gaia engine）。
- **文档 CI 校验**：链接有效性、Markdown lint、术语一致性可入 pre-commit。
- **发布站点**：当前 docs 是散 Markdown，无统一导航站点（VitePress/Docusaurus）。

### 7.3 对 Gaia 的直接启示

文档工程应顺带建立：
- 一套文档模板（每类文档的标准结构）。
- 一个文档索引/导航（至少一个 README/INDEX 起到站点 sidebar 作用）。
- 状态标注规范（✅/🟡/🔴 + 代码交叉验证，避免如本次"外部数据路径待接线"那种漂移）。

> 出处：docsio.co/blog/docs-as-code；engineering.squarespace.com/blog/2025/making-documentation-simpler-and-practical-our-docs-as-code-journey

---

## 八、优秀案例的共同特征（横评）

综合 HN "best OSS docs" 讨论 + Rust/Kafka/PostgreSQL/Vue/Laravel 等公认好文档：

| 特征 | 说明 | 代表 |
|------|------|------|
| **可上手性（approachability）** | 新人周末能跑通一个 demo | Vue, Laravel |
| **设计哲学可见（philosophy）** | 讲清"为什么这么设计" | Kafka Design 章节, Rust |
| **可发现性（discoverability）** | 容易探索到新主题 | Rust docs (docs.rs) |
| **完备性（comprehensiveness）** | 主题覆盖有实质深度 | PostgreSQL |
| **代码即文档** | 文档从代码注释生成，零漂移 | Rust rustdoc |
| **任务主线存在** | 有"如何达成 X"的引导，不只罗列能力 | Django, Laravel |

PostgreSQL 的反面教训值得重申：它 reference/explanation 极强（完备性天花板），但**任务导向弱**——读者知道每个部件是什么，却不知道怎么组合它们解决问题。

---

## 九、中文社区方法论沉淀（补充视角）

- **Code2Life《如何写出高质量的技术文档》**：系统介绍 Diátaxis + 7 条写作原则 + 工具箱，中文圈对 Diátaxis 最完整的解读。
- **腾讯云《如何写一份高可读性的软件工程设计文档》**：基于 Google 设计文档经验，强调设计文档三要素（受众/目的/结构），主张"非正式但系统"。
- **《技术写作的工程化》**（腾讯云）：把技术写作当流水线（选题→素材→生成→审稿→排版→发布→复盘），文章超 60 篇后瓶颈从"写作能力"转"内容资产管理"——对 Gaia 80 篇文档正切题。
- **金字塔原理 + SCQA 在技术场景的落地**（CSDN/腾讯云）：技术方案评审、故障复盘、架构设计文档的 SCQA 实战模板。

共识与英文方法论一致，额外强调：**受众优先 + 目的单一 + 简洁准确 + 可落地**四原则。

---

## 十、综合结论：对 Gaia 文档工程的 9 条指导原则

基于以上研究，提炼 Gaia 文档工程应遵循的原则：

1. **四模式分仓**：明确区分 Tutorials / How-to / Reference / Explanation，每篇文档先定象限再动笔。Gaia 当前严重偏 Explanation+Reference，必须补 Tutorials+How-to。

2. **金字塔叙事**：每篇文档结论先行（BLUF），用 SCQA 开篇，纵向疑问-回答链推进，同层 MECE。杜绝"铺三页背景才到正题"。

3. **任务导向主线**：使用指导按"我想做 X"组织，不按"组件支持 X"组织。概念作为前置铺垫（链接到 Explanation），不在 how-to 里展开。

4. **渐进式披露**：三层阅读路径（5 分钟概览 → 30 分钟架构 → 深度章节）。每特性页统一"TL;DR→概念→操作→深入"结构。大文档（如 reference.md 430KB）必须拆分+导航，不整篇呈现。

5. **arc42 骨架对齐**：补齐 §1 引言与目标 / §6 运行时视图 / §10-11 质量场景与风险。ADR/ICD 已对齐 §9，保持。C4 层级标注架构图。

6. **Palantir 叙事母题借鉴**：复刻"四重整合 + 场景具象 + 完整走查案例"手法，但去 Palantir 黑话，用"语义大脑+安全手脚"等开源化表述。必须补业务场景走查（供应链/制造/医疗），不能通篇技术组件。

7. **状态标注规范**：✅已实现 / 🟡部分 / 🔴待开发 / ⚫未规划，且**每条标注以代码交叉验证为准**（implementation-status 会漂移，如本次"外部数据路径"事件）。已设计未实现的特性（如 scenario/decision-exhaust）必须显式标注，不与已实现混排。

8. **Docs-as-Code 落地**：文档模板 + 导航索引 + 状态校验入 pre-commit。文档与代码同 PR 审查。

9. **受众分层**：至少区分三类读者——决策者（看价值与权衡）、集成开发者（看 API 与 how-to）、贡献者（看架构与 ADR）。同一主题为不同读者提供不同入口（渐进式披露的受众裁剪）。

---

## 附：关键参考出处索引

### 框架与原则
- Diátaxis: https://diataxis.fr/ | https://canonical.com/blog/diataxis-a-new-foundation-for-canonical-documentation
- Divio 考证: https://newton.cx/~peter/2023/divio-documentation-system/
- 金字塔原理: https://strategyu.co/pyramid-principle-partone/ | https://www.mckinsey.com/alumni/news-and-events/global-news/alumni-news/barbara-minto-mece-i-invented-it-so-i-get-to-say-how-to-pronounce-it
- 技术文档落地金字塔: https://daily.jovis.ai/technical-writing/conquering-complexity-the-pyramid-principle-for-crystal-clear-technical-documentation/
- arc42: https://arc42.org/overview | https://docs.arc42.org/section-9/
- C4: https://c4model.com/faq
- arc42+C4 范例: https://bitsmuggler.github.io/arc42-c4-software-architecture-documentation-example/
- Progressive Disclosure: https://www.nngroup.com/articles/progressive-disclosure/ | https://quality.arc42.org/approaches/progressive-disclosure
- Concept vs Task: https://docs.docmd.io/07/guides/content-ux/task-vs-concept/ | https://www.atlassian.com/blog/it-teams/writing-great-docs-for-your-app
- Task-based 研究: https://docsgeek.io/blog/posts/task-based-api-docs.html
- Google 大文档组织: https://developers.google.com/tech-writing/two/large-docs

### Palantir 一手
- Ontology system: https://palantir.com/docs/foundry/architecture-center/ontology-system/
- Ontology core concepts: https://palantir.com/docs/foundry/ontology/core-concepts/
- Action types: https://palantir.com/docs/foundry/action-types/overview/
- OSDK: https://palantir.com/docs/foundry/ontology-sdk/overview/
- Build with AIP (tutorials): https://build.palantir.com/
- Foundry whitepaper: https://www.palantir.com/assets/.../Whitepaper_-_Foundry_2022.pdf

### Docs-as-Code
- https://docsio.co/blog/docs-as-code
- https://engineering.squarespace.com/blog/2025/making-documentation-simpler-and-practical-our-docs-as-code-journey

### 中文沉淀
- Diátaxis 中文详解: https://code2life.top/blog/0078-how-to-write-good-tech-docs
- 设计文档: https://cloud.tencent.com/developer/article/2083710
- 技术写作工程化: https://developer.cloud.tencent.com/article/2701468
- 金字塔+SCQA 技术落地: https://blog.csdn.net/RickyIT/article/details/161936746

### 案例与反思
- HN best OSS docs: https://news.ycombinator.com/item?id=32131918
- PostgreSQL 文档局限: https://news.ycombinator.com/item?id=36328466
- Rust docs 评测: https://www.harudagondi.space/blog/rust-documentation-ecosystem-review/
