# 方案对照分析 —— Palantir 动态本体映射 Neo4j 企业级方案 vs Gaia 当前实现 vs Ontop 调研结论

> **用途**：本文对用户提供的《Palantir 动态本体映射 Neo4j 企业级完整方案》（以下简称「方案」）做逐节对照分析，指出其与 Gaia 当前实现的重合/冲突/可借鉴之处，并整合 Ontop 源码调研的结论，给出 Gaia 的取舍建议。
> **输入方案**：用户提供（八个章节，覆盖映射规则、安全标记、嵌套结构、SCD2、批量同步、Action/Function、工程分层、投产规范）
> **对照基准**：Gaia ADR-015/016/017 + `graph-reasoning-design.md` §6 + `graph_projector.py` / `marking_service.py` / `authorization_service.py` 实际代码
> **关联文档**：[`ontop-source-analysis.md`](./ontop-source-analysis.md) · [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) · [`graph-reasoning-design.md`](../architecture/graph-reasoning-design.md)
> **分析日期**：2026-07-14

---

## 〇、一句话总览

| 维度 | 方案立场 | Gaia 现状 | 判断 |
|------|---------|----------|------|
| **数据流向** | Neo4j 是**主数据存储**（Palantir 数据集直接落地） | Neo4j 是**派生投影副本**（Doris 在线读主源 + Iceberg 归档是主源；PG object_state 仅是 Action 写入态） | ❌ 根本性分歧 |
| **映射方向** | Palantir 本体 → Neo4j（Neo4j 持有全量属性） | object_state → Neo4j 仅投影 indexed 属性 | ❌ 设计哲学不同 |
| **安全标记** | 标记存 Neo4j 节点属性 + RLS | 标记存 PG（`marking_service` + `authorization_service`）+ 查询时注入 | 🟡 部分可借鉴，存 Neo4j 是反模式 |
| **批量同步** | `neo4j-admin import` 离线 + APOC 增量 | OutboxExecutor 异步 fan-out | ❌ 不可照搬（数据源不同） |
| **技术栈** | Java/Neo4j 企业版 | Python + Neo4j 社区版 | ❌ 部分特性不可用 |

**核心结论**：方案是「**Neo4j 作主存的 Foundry-on-Neo4j 私有化复刻**」，而 Gaia 是「**Neo4j 作派生索引的多引擎联邦**」（Doris 在线读主源 + Iceberg 归档 + Neo4j 图遍历 + PostGIS 空间）。两者**架构前提不同，方案不能整体照搬**，但其若干具体技术（安全标记的 4 字段编码、SCD2 时序元属性、嵌套结构两套存储模式）有局部参考价值。这与 Ontop 调研的结论形成互补：Ontop 教的是"查询时联邦"的算法，方案教的是"派生投影"的细节，两者都指向 Gaia 应坚持自己的派生索引路线。

---

## 一、§1 核心映射规则对照

### 1.1 ObjectType → Node Label（方案 §1.1）

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| Label 生成 | `{ObjectType}` 单标签 | `graph_label(ontology, ot) = {Ontology}{ObjectType}` PascalCase | ✅ Gaia 更优（防跨本体同名冲突，见 naming.py 注释） |
| 继承 | `extends` → 多标签 `:Person:Employee` | **不支持 extends**，用 InterfaceType + `object_type_interfaces` 关联表（implements） | ⚠️ 设计差异（见下） |
| 抽象类型 | 仅作父标签，不生成节点 | 无抽象 ObjectType 概念 | — |

**关于继承**：方案用 `extends`（单继承 + 多标签），Gaia 用 InterfaceType（implements，可多实现）。这是 Palantir 本体两种不同的建模能力：
- Palantir 实际**同时支持** `extends`（类型继承）和 `implements`（接口实现）
- Gaia 当前只实现了 `implements`（InterfaceType），`extends` 未建模
- **图投影层面的差异**：方案的多标签让 `MATCH (n:Person)` 能查到所有 Employee；Gaia 的 InterfaceType 要靠 `interfaceBase` 关联表做跨类型查询，图层面没有"父标签"

**建议**：**不照搬方案的多标签继承**。理由：(1) Gaia 当前没有 ObjectType 继承模型，引入多标签需要先在元数据层加 extends；(2) InterfaceType + interfaceBase 已能解决"跨类型查询"需求（见 `DataFrameQueryService` 的 interface 关联表分流）；(3) 多标签在 Neo4j 社区版会让 label scan 索引选择复杂化。如果未来真需要 extends，再统一设计，而不是在图投影层临时打补丁。

### 1.2 PropertyType → 属性（方案 §1.2）

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| 类型对齐表 | 7 类基础映射 | 类似（data_type 字段 + Doris/Iceberg 各自映射） | ✅ 一致 |
| `@unique` | Neo4j 唯一约束 | `is_primary_key` 标记，但**图层面不创建约束** | 🟡 见下 |
| `@required` | Neo4j 存在约束 | `nullable` 字段，图层面不创建约束 | 🟡 |
| `@fulltext` / 普通索引 | 全文/Range 索引 | `indexed` 字段 → 仅决定是否投影到 Neo4j | ❌ 语义不同（见下） |
| 系统元属性（`_source`/`_dataset_id`/`_valid_start`/...） | 节点存 6 个下划线前缀元属性 | **图节点不存这些**（仅存 rid + indexed 属性 + visibility） | ❌ 反模式（见下） |

**关键分歧 1：`indexed` 的语义**。方案的 `@fulltext`/普通索引是"**在 Neo4j 内建索引**"，Gaia 的 `indexed` 是"**是否投影到 Neo4j**"。这是架构前提不同：
- 方案：Neo4j 持全量属性，索引加速 Neo4j 内查询
- Gaia：Neo4j 只存 indexed 属性做图遍历剪枝，全量属性在 Doris（ADR-001，Doris 在线读主源）

Gaia 不能照搬"在 Neo4j 建全量索引"——那会让 Neo4j 退化成第二个 Doris，违背 C1（图节点轻量）和 ADR-001。

**关键分歧 2：系统元属性存 Neo4j**。方案把 `_source`/`_dataset_id`/`_valid_start`/`_valid_end`/`_tx_version`/`_is_deleted` 都塞进 Neo4j 节点。这在 Gaia 是**反模式**：
- Gaia 的谱系/治理记录在 PG（`datasets` 治理记录 + object_state 的 backing_dataset 字段）；**全量业务属性在 Doris**（ADR-001 在线读主源）+ Iceberg（归档）
- 时序在 Iceberg（time travel，ADR 已有 TimeTravelService）+ TimescaleDB（GTS）
- 软删除在 PG object_state（Action 写入态）
- 把这些冗余复制到 Neo4j = 6 个额外字段 × N 节点 = 存储膨胀 + 一致性维护成本，且违反 C1（图节点轻量，仅 indexed 属性 + rid）

**建议**：**拒绝方案的系统元属性进 Neo4j**。Neo4j 只存图遍历必需的剪枝字段。全量业务属性查询走 Doris（主源）；谱系/时序/软删除等治理元数据走 PG/Iceberg。唯一例外是 `visibility`（已投影，用于图遍历前的可见性过滤），这个 Gaia 已经做了。

### 1.3 LinkType → Relationship（方案 §1.3）

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| 关系类型命名 | 大写 | `graph_relationship_type(ontology, link) = {Ontology}{LinkType}` PascalCase | ✅ Gaia 更优（带本体前缀） |
| 反向 Link | 不创建反向边，查询时反向遍历 | 同（C1，单向上行关系） | ✅ 一致 |
| 源/目标类型约束 | 应用层校验 | 应用层校验（ActionService） | ✅ 一致 |
| **基数约束** | 三层：应用层预查询 + 节点冗余计数 + 企业版触发器 | **无基数约束实现** | 🟡 见下 |

**基数约束是方案最有价值的点之一**。Gaia 当前 LinkType 模型有没有基数字段？需要确认，但即便有，执行层也没有强制。方案的三层方案里：
- **第 1 层（应用层预查询）**：Gaia 可直接借鉴，在 ActionService RELATE 操作前查现有关联数
- **第 2 层（节点冗余计数）**：❌ 违反 C1（图节点轻量），且 Gaia 的图是派生副本，计数应该在 PG object_state 层做，不是 Neo4j
- **第 3 层（企业版触发器）**：❌ Gaia 用社区版，不可用

**建议**：**采纳第 1 层**（应用层预查询校验基数），在 `ActionService` 的 RELATE 分支加基数检查。**拒绝第 2/3 层**。这需要先在 LinkType 模型补 `cardinality` 字段（min/max），是 ADR-011 Action P1 的延伸。

---

## 二、§2 安全标记映射对照（核心冲突区）

这是方案和 Gaia **冲突最大**的部分。方案要把安全标记存进 Neo4j 节点属性，Gaia 的权限治理（ADR-016/017）是另一套架构。

### 2.1 语义模型对照

| 语义 | 方案 | Gaia（ADR-016） | 评价 |
|------|------|----------------|------|
| 合取 AND | `_mark_conjunct` 数组 | ✅ 已实现（`authorization_service._check_marking_and_row`，missing = resource - principal） | ✅ 一致 |
| 分级格支配 | `_mark_class_level` 数值 | 🟡 Marking 有 category，但**无 lattice 层级**（Phase 7 待开发） | 🟡 方案更完整 |
| 组织析取 OR | `_mark_org` 数组 | ✅ Layer 2 Organization（home_org ∈ space org whitelist） | ✅ 一致（不同实现） |
| 标记传播 | `_mark_inherit_propagate` | ❌ 未实现（Phase 7 血缘传播） | 🟡 方案有，Gaia 待开发 |

**关键洞察**：方案的 4 字段编码（`_mark_conjunct`/`_mark_org`/`_mark_class_level`/`_mark_inherit_propagate`）是**针对 Neo4j 节点属性存储优化的编码**。Gaia 的 Marking 存 PG（`markings` + `resource_markings` 关联表），查询时 `get_resource_markings` 拉取再校验，**不需要这种编码**。

### 2.2 存储位置的根本分歧

| 维度 | 方案 | Gaia | 谁对 |
|------|------|------|------|
| 标记存哪 | Neo4j 节点属性 | PG（markings 表 + resource_markings 关联表） | **Gaia 对** |
| 校验在哪 | Cypher WHERE 注入 / 企业版 RLS | Python AuthorizationService（Layer 5） | Gaia 适合社区版 |
| 索引 | 全文索引/TEXT索引/独立标记节点 | PG 索引（resource_markings） | Gaia 更简单 |

**为什么 Gaia 不把标记存 Neo4j**：
1. **Neo4j 是派生副本**（C8），标记是治理元数据，属于"权威源"范畴，应在 PG
2. **社区版无 RLS**（方案 §2.5 也承认社区版要"应用层封装"），那存 Neo4j 节点纯属冗余
3. **图遍历的 ACL 过滤**：方案给的标准 Cypher（`all(mark IN n._mark_conjunct WHERE mark IN $user_acl_conjunct)`）确实能在图遍历时过滤，但 Gaia 的图遍历（search_around/find_paths）是**先查图拿 rid，再回 Doris 水合属性**（MANAGED 主源 Doris，VIRTUAL 联邦 Trino）——标记校验放在水合后的属性层做，和图拓扑分离，更干净
4. **一致性**：标记变更（assign/revoke）是治理操作，频率低；如果存 Neo4j，每次变更要同步投影，增加 outbox 负担

**方案的合理内核**：**标记传播**（derived data 继承上游标记）和 **lattice 支配**是 Gaia Phase 7 待开发的能力。方案给了具体的字段设计（`_mark_inherit_propagate` boolean + `_mark_class_level` int），可以**在 PG 层借鉴**这个数据模型（不是 Neo4j 层）：
- Marking category 加 `lattice_order` 字段（int，支配关系）
- Marking 加 `propagate` 字段（是否向下游血缘传播）
- 派生数据的标记继承在 OutboxExecutor / 派生计算时自动赋值

### 2.3 索引策略对照

方案给的三种索引（全文/TEXT/独立标记节点）都是**为 Neo4j 节点属性存储设计的**。Gaia 不存 Neo4j，这三种都不适用。Gaia 的标记查询走 PG `resource_markings` 表的索引，已足够（标记数量远小于实体数量）。

**结论**：§2 整体**不照搬**，但 lattice + 传播两个语义模型在 PG 层借鉴。

---

## 三、§3 嵌套结构体对照

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| 嵌套结构体支持 | 两套模式：JSON 序列化 / 扁平化子实体节点 | PropertyDefModel `data_type` 字段，**无原生 struct 类型** | 🟡 见下 |
| 查询转译层 | `apoc.convert.fromJsonMap` 自动封装 | 无 | — |

**Gaia 现状**：`PropertyDefModel.data_type` 是 String（如 "string"/"integer"/"VECTOR"/"GEOPOINT"），`constraints` 是 JSONB（VECTOR 存 dimension/similarity）。**没有原生的嵌套 struct 类型**——嵌套数据只能存 JSONB（在 object_state 的 properties dict 里），或拆成独立 ObjectType 用 Link 关联。

**方案的启发**：
- **模式 1（JSON 序列化）**：Gaia 的 object_state properties 是 JSONB，天然支持嵌套。但图投影（GraphProjector）只取 indexed 的顶层属性，嵌套字段不进 Neo4j——这其实和方案模式 1 一致（JSON 存节点，查询时解析），只是 Gaia 连解析层都没做（因为图不查嵌套属性）
- **模式 2（扁平化子实体）**：Gaia 用 LinkType + 独立 ObjectType 表达"主实体-子结构"关系，比方案的"独立子实体节点 + 一对一关系"更规范（因为子结构在 Gaia 是一等公民 ObjectType，有完整的属性/索引/权限）

**结论**：Gaia 的 LinkType + 独立 ObjectType 已经覆盖了方案模式 2 的能力，且更规范。方案模式 1（JSON 存节点）违反 C1（图节点轻量），不采纳。**§3 整体不照搬**。

---

## 四、§4 SCD2 时序对照

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| 历史版本管理 | `_is_deleted` + `_valid_start` + `_valid_end` 在 Neo4j 节点 | **Iceberg time travel**（TimeTravelService，snapshot 版本）+ PG object_state OCC | ❌ 不同范式 |
| 指定时间窗查询 | `WHERE _valid_start <= t < _valid_end` | `TimeTravelService` → Trino `FOR VERSION AS OF {snapshot}` | ❌ 不同实现 |

**根本分歧**：方案用 SCD2（缓慢变化维度，数仓经典模式）在 Neo4j 节点存多版本；Gaia 用 Iceberg 的 ACID snapshot 做时间旅行。两者解决同一问题（历史版本查询），但：
- Iceberg snapshot 是**表级快照**，天然支持全表时间旅行，不需要每行加 valid_start/valid_end
- SCD2 是**行级版本**，需要业务字段标记，查询时要过滤，且只适用于维度表

**为什么 Gaia 不用 SCD2 存 Neo4j**：
1. Neo4j 是派生副本，历史版本的主源是 Iceberg，Neo4j 只存"当前"图拓扑
2. 图遍历（search_around）查的是**当前关系状态**，历史关系查询走 Iceberg time travel + Trino，不需要 Neo4j 存历史
3. SCD2 的 `_is_deleted`/`_valid_end` 进 Neo4j = 每次软删除都在 Neo4j 留一个旧节点 + 新节点，图膨胀

**唯一可借鉴**：如果未来 Gaia 要在**图层面**支持"某时刻的关系拓扑"查询（如"上周三时 A 和 B 是否关联"），可以考虑在 Neo4j 边上加 `valid_start`/`valid_end`（不是节点）。但这属于 C8 一致性模型的扩展，当前 MVP 不做。

**结论**：§4 **不照搬**，Gaia 的 Iceberg time travel 是更优的方案。

---

## 五、§5 批量同步对照

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| 离线全量 | `neo4j-admin import`（CSV） | `rebuild_graph` / `rebuild_for_object_type`（从 object_state 全量重投影） | ❌ 不同链路 |
| 增量流式 | APOC `periodic.iterate` | OutboxExecutor fan-out（INDEX effect 侧调 GraphProjector） | ❌ 不同链路 |
| 事务控制 | APOC batchSize 5000 | outbox 单条 fail-tolerant | 🟡 Gaia 可补批量优化 |

**根本分歧**：方案的批量同步是"**Palantir 数据集 → Neo4j**"（Neo4j 是目标存储），Gaia 是"**PG object_state/Iceberg → Neo4j**"（Neo4j 是派生副本）。链路完全不同。

**可借鉴点**：方案的 `apoc.periodic.iterate` 用 `batchSize:5000, parallelism:8` 控制事务。Gaia 的 `rebuild_for_object_type` 当前是**逐条** `project_object`（见 graph_projector.py），没有批量。对于大规模重建（百万级 object_state），可以借鉴批量提交：
- 在 `Neo4jGraphStore.upsert_node` 上层加 `upsert_nodes_batch(label, items: list)`，用 UNWIND + MERGE 一次提交
- `rebuild_for_object_type` 改成分批（如 1000/批）

**结论**：§5 链路**不照搬**，但**批量提交优化可借鉴**（提升 rebuild 性能）。

---

## 六、§6 Function/Action 对照

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| Function 衍生属性 | 写入预计算 / 查询实时计算 | ❌ Functions 远期规划（implementation-status §十四.2） | 🟡 远期参考 |
| ActionType | 独立 `OntologyAction` 节点 + `[:EXECUTE_ACTION]` 关系 | ActionType 在 PG（`action_types` 表），**图层面不投影** | ❌ 反模式（见下） |
| 执行引擎 | 应用层读 Action 节点配置动态生成 Cypher | ActionService（PG 原子提交 + outbox） | ❌ 不同架构 |

**为什么 ActionType 不进 Neo4j**：ActionType 是**操作定义**（元数据），不是**业务数据**。把操作定义存成 Neo4j 节点，意味着每次查"这个对象能执行什么 Action"都要查图——而 Action 的可用性依赖权限、上下文、submission_criteria，这些都在 PG 层判断（ActionAuthorizer 三层权限）。存 Neo4j 纯属冗余，且违反 C4（图只存图遍历必需的拓扑 + 剪枝字段）。

**方案 Action 节点设计的合理内核**：把 Action 元数据建模为图节点，好处是能查"哪些对象类型共享某个 Action"。但这个需求 Gaia 用 PG 的 `action_types.affected_object_type_id` 外键就能解决，不需要图。

**Function 的参考价值**：方案的两个模式（写入预计算 / 查询实时计算）对 Gaia 远期 Functions 设计有参考。Gaia 的 implementation-status §十四.2 提到"声明式 DSL 优先，Python 沙箱后续"，方案的"写入预计算衍生属性"对应 DSL 模式，"查询实时计算"对应 Python 沙箱。但这是远期，当前不落地。

**结论**：§6 **不照搬**，Function 远期参考。

---

## 七、§7 工程分层架构对照

方案的 10 层架构 vs Gaia 现状：

| 方案层 | Gaia 对应 | 状态 |
|--------|----------|------|
| 1. 本体元数据解析层 | OntologyService + ObjectType/Property/LinkType 元模型 | ✅ 已有 |
| 2. Schema 自动生成层 | naming.py（graph_label/relationship_type）+ Alembic | ✅ 已有（但**不自动建 Neo4j 约束/索引**，见下） |
| 3. 安全标记语义层 | MarkingService + AuthorizationService Layer 5 | ✅ 已有（PG 层，非 Neo4j） |
| 4. 实例转换映射层 | GraphProjector（object_state → Neo4j 节点） | ✅ 已有 |
| 5. 嵌套结构转译层 | 无（用 LinkType + 独立 ObjectType 替代） | — |
| 6. 基数约束校验层 | 无 | 🟡 待补（见 §1.3） |
| 7. 批量同步引擎层 | OutboxExecutor + rebuild_graph | ✅ 已有（非 neo4j-admin） |
| 8. Action 执行层 | ActionService + ActionAuthorizer | ✅ 已有（PG 层，非 Neo4j） |
| 9. 时序 SCD2 管理层 | TimeTravelService（Iceberg） | ✅ 已有（非 SCD2） |
| 10. 查询适配封装层 | DataFrameQueryService + ObjectSet IR | ✅ 已有 |

**方案缺失的、Gaia 已有的关键层**：
- **Doris 在线读主源层**（ADR-001）——方案完全没提，因为方案假设 Neo4j 持全量属性
- **Iceberg 归档层**——方案没有，因为方案没有"主数据落 Iceberg"的概念
- **PostGIS 空间层**——方案没有
- **TimescaleDB 时序层**——方案用 SCD2 替代

**Schema 自动生成层的差异**：方案 §7.2 说"自动生成约束、索引、基数校验 Cypher 脚本"。Gaia 当前 `naming.py` 只生成 label/rel_type 名称，**不自动建 Neo4j 约束/索引**。这是个可借鉴的点：
- Gaia 可以在 `Neo4jGraphStore` 加 `ensure_schema(ot)` 方法，根据 ObjectType 的 `is_primary_key` 在 Neo4j 建 `rid` 唯一约束（如果还没有）
- 但**不建全量属性索引**（违反 ADR-001，Doris 才是在线读主源）

**结论**：§7 的分层思路 Gaia 基本都有，且 Gaia 的分层更完整（多引擎联邦）。Schema 自动生成可局部借鉴（仅 rid 唯一约束）。

---

## 八、§8 投产规范对照

| 项 | 方案 | Gaia 现状 | 评价 |
|----|------|----------|------|
| 映射元数据表 | 独立表记录 ObjectType-Label 对应 | naming.py 纯函数推导（无独立表） | ✅ Gaia 更优（无冗余） |
| 审计节点 | Neo4j 审计节点记录写入/标记变更 | 审计在 PG（outbox + analysis_records） | ✅ Gaia 已有（非 Neo4j） |
| 一致性校验工具 | 定时比对本体元模型 vs Neo4j Schema | `rebuild_graph` + ConflictDetector（审计 Doris） | 🟡 可补 Neo4j 侧校验 |

**可借鉴**：方案 §8.3 的“映射一致性定时校验工具”——定期比对本体元模型与 Neo4j 库内 Schema。Gaia 的 ConflictDetector 当前只审计 Doris 存在性（检测 INDEX outbox 漏写），**不审计 Neo4j**。可以扩展 ConflictDetector 加一轮 Neo4j 节点数 vs Doris 行数（主源）的对账（采样，非全量）；PG object_state 作为 Action 写入态也可作为辅助计数源（每个 MANAGED 对象必有 object_state 记录），但权威计数以 Doris 为准。

**结论**：§8 一致性校验**可借鉴**（扩展 ConflictDetector）。

---

## 九、与 Ontop 调研结论的整合

三份文档现在形成完整的决策三角：

```
                    Gaia 架构前提
                   （Neo4j = 派生索引）
                    /            \
                   /              \
    Ontop 调研结论                  方案对照分析
   （查询时联邦范式）              （派生投影细节）
   - 不直接集成（RDF）             - 不整体照搬（主存分歧）
   - 借鉴 mapping + IQ 设计        - 借鉴基数校验 + lattice + 批量优化
   - 路径 ③' 自研联邦参考          - 坚持派生索引路线
```

### 9.1 三者对"Neo4j 定位"的一致判断

| 来源 | Neo4j 定位 | 数据流方向 |
|------|-----------|-----------|
| **Ontop 调研** | 零拷贝虚拟图（查询时翻译） | 查询时联邦（不存数据） |
| **方案对照** | 主数据存储（Palantir 数据集落地） | ETL 导入（存全量） |
| **Gaia 现状** | 派生索引（仅 indexed 属性） | 异步 fan-out 投影 |

Gaia 的定位（派生索引）**介于两者之间**：比 Ontop 重（存了数据），比方案轻（只存剪枝字段）。这是 Gaia 的架构选择（ADR-015），**两个外部参考都验证了这个选择的合理性**：
- Ontop 证明"全虚拟"在属性图范式下需要自研查询翻译器（成本高）
- 方案证明"全落地"会让 Neo4j 退化为第二个 Doris（冗余 + 一致性负担）
- Gaia 的"轻量派生索引"是两者的中间最优解

### 9.2 各自的借鉴价值分层

| 借鉴价值 | 来源 | 具体内容 | 落地优先级 |
|---------|------|---------|-----------|
| ★★★ 高 | 方案 §1.3 | LinkType 基数约束（应用层预查询校验） | P1（ActionService RELATE 前置校验） |
| ★★★ 高 | Ontop 参考 1/2 | IQ 统一中间表示 + Mapping 声明式映射 | P1（路径 ③' 设计阶段） |
| ★★ 中 | 方案 §2.1 | Marking lattice 支配 + 标记传播（PG 层借鉴） | P2（ADR-016 Phase 7） |
| ★★ 中 | 方案 §5 | 批量提交优化（UNWIND + MERGE） | P2（rebuild_graph 性能） |
| ★★ 中 | Ontop 参考 3/4 | Lens 约束声明 + Direct Mapping 自动建模 | P2（路径 ③' + BuildWith） |
| ★ 低 | 方案 §8.3 | Neo4j 一致性校验（扩展 ConflictDetector） | P3 |
| ★ 低 | Ontop 参考 5/6 | 查询缓存 + explain 调试 | P3 |
| ❌ 拒绝 | 方案 §1.2 | 系统元属性进 Neo4j（违反 C1） | — |
| ❌ 拒绝 | 方案 §2.2-2.4 | 安全标记存 Neo4j 节点（违反派生副本定位） | — |
| ❌ 拒绝 | 方案 §4 | SCD2 存 Neo4j（Iceberg time travel 更优） | — |
| ❌ 拒绝 | 方案 §6 | ActionType 进 Neo4j（元数据非业务数据） | — |

### 9.3 对虚拟表填充 Neo4j 决策的影响

之前的 [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) 推荐 ①→②→③' 演进路线。方案对照后**不改变这个结论**，反而强化：
- 方案把"全量属性 + 系统元属性 + 标记 + 时序"都塞 Neo4j 的做法，正是 Gaia 要**避免**的反面教材
- 方案的反例说明：**Neo4j 越轻（只存图拓扑 + 剪枝字段），派生副本的维护成本越低，一致性越容易保证**
- 这进一步支持路径 ①（VIRTUAL 只投身份骨架），而不是路径 ②（投 indexed 属性）——因为投得越多，和方案的"Neo4j 主存"陷阱越近

---

## 十、最终建议

### 10.1 整体判断

**方案不能整体照搬**。其架构前提（Neo4j 作主存、Palantir 数据集直接落地）与 Gaia（Neo4j 作派生索引、Doris+Iceberg 是主源；PG object_state 仅是 Action 写入态）根本不同。强行照搬会：
1. 让 Neo4j 退化为第二个 Doris（全量属性冗余）
2. 破坏 C1（图节点轻量）/ C8（派生副本一致性）
3. 引入社区版不支持的企业版特性（RLS / 触发器）

### 10.2 可落地的具体改进（按优先级）

1. **P1 — LinkType 基数约束**（方案 §1.3 第 1 层）
   - LinkType 模型补 `min_cardinality` / `max_cardinality` 字段
   - ActionService RELATE 分支前置校验
   - 配套 Alembic migration + 测试

2. **P2 — Marking lattice + 传播**（方案 §2.1，PG 层）
   - MarkingCategory 加 `lattice_order`（int，支配关系）
   - Marking 加 `propagate`（bool，是否向下游血缘传播）
   - 派生数据自动继承标记（OutboxExecutor 侧）
   - 属于 ADR-016 Phase 7

3. **P2 — rebuild 批量优化**（方案 §5）
   - Neo4jGraphStore 加 `upsert_nodes_batch`
   - rebuild_for_object_type 改分批提交

4. **P3 — Neo4j Schema 自动生成**（方案 §7.2，仅 rid 约束）
   - Neo4jGraphStore 加 `ensure_vid_constraint(label)`
   - rebuild 时自动确保约束存在

5. **P3 — Neo4j 一致性校验**（方案 §8.3）
   - ConflictDetector 扩展一轮 Neo4j 节点数采样对账

### 10.3 需要先做的设计决策（动代码前）

在落地 P1 之前，建议先更新设计文档：
- **`graph-reasoning-design.md` §6**：明确 Neo4j 是"派生索引"（非主存非虚拟），拒绝方案的系统元属性/标记/SCD2 进 Neo4j，记录这个设计立场
- **ADR-015 补充或新 ADR**：记录"VIRTUAL 对象图投影"决策（路径 ①），引用三份调研文档（本方案对照 + Ontop + 可行性）作为依据
- **ADR-016 Phase 7 设计**：把 lattice + 传播的数据模型（借鉴方案 §2.1）纳入规划

### 10.4 文档存档建议

本对照分析 + Ontop 源码分析 + 虚拟表可行性调研，三份文档共同构成"Gaia 图推理层外部参考调研全集"，建议在 `docs/research/index.md` 的"图推理与虚拟表联邦"分类下统一索引（已登记），供后续 ADR-015 修订和 Phase 7 设计时引用。

---

## 附录：方案各章节采纳决策速查表

| 方案章节 | 标题 | 决策 | 理由 |
|---------|------|------|------|
| §1.1 | ObjectType → Label | ❌ 不照搬多标签继承 | Gaia 用 InterfaceType，无 extends 模型 |
| §1.2 | Property → 属性 | ❌ 拒绝系统元属性进 Neo4j | 违反 C1，谱系/时序在 PG/Iceberg |
| §1.2 | 约束映射 | 🟡 仅借鉴 rid 唯一约束 | 全量索引违反 ADR-001 |
| §1.3 | Link → Relationship | ✅ 命名一致；🟡 借鉴基数第 1 层 | 拒绝第 2/3 层 |
| §2 | 安全标记 | ❌ 不存 Neo4j；🟡 PG 层借鉴 lattice + 传播 | Neo4j 是派生副本，社区版无 RLS |
| §3 | 嵌套结构 | ❌ 不照搬 | LinkType + 独立 ObjectType 已覆盖 |
| §4 | SCD2 时序 | ❌ 不照搬 | Iceberg time travel 更优 |
| §5 | 批量同步 | ❌ 链路不照搬；🟡 借鉴批量提交 | 数据源不同（PG vs Palantir 数据集） |
| §6 | Function/Action | ❌ Action 不进 Neo4j；🟡 Function 远期参考 | 元数据非业务数据 |
| §7 | 工程分层 | ✅ Gaia 已有且更完整 | 多引擎联邦是 Gaia 优势 |
| §8 | 投产规范 | 🟡 借鉴一致性校验 | 扩展 ConflictDetector |
