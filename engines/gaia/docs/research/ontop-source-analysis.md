# Ontop 源码分析 —— 虚拟知识图谱（VKG）实现剖析与 Gaia 可参考点

> **用途**：本文是对 Ontop v5（`github.com/ontop/ontop`，分支 `version5`，commit `5ec0757`）源码的深度剖析，识别其"虚拟知识图谱/查询时联邦"范式的核心实现机制，并逐项对照 Gaia 当前架构（Neo4j 图投影 + Trino 联邦查询），提炼可参考的设计与不可照搬的差异。
> **源码位置**：`/tmp/pi-github-repos/ontop/ontop`（已克隆，2311 个 Java 文件，191MB）
> **分析日期**：2026-07-14
> **关联文档**：[`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md)（虚拟表填充 Neo4j 调研，本文是其 §4.2 Ontop 部分的展开）· [`graph-reasoning-design.md`](../architecture/graph-reasoning-design.md) §6（投影机制）

---

## 目录

- [第一部分：Ontop 是什么](#第一部分ontop-是什么)
- [第二部分：核心架构 —— 五阶段查询翻译流水线](#第二部分核心架构--五阶段查询翻译流水线)
- [第三部分：关键子系统剖析](#第三部分关键子系统剖析)
- [第四部分：Trino 支持现状](#第四部分trino-支持现状)
- [第五部分：与 Gaia 的对照分析](#第五部分与-gaia-的对照分析)
- [第六部分：可参考点清单](#第六部分可参考点清单)
- [第七部分：不可照搬的差异](#第七部分不可照搬的差异)
- [第八部分：结论与建议](#第八部分结论与建议)

---

## 第一部分：Ontop 是什么

| 维度 | 说明 |
|------|------|
| 定位 | Virtual Knowledge Graph (VKG) 系统，把关系数据库内容暴露为虚拟知识图谱，数据不落地 |
| 核心能力 | 把 SPARQL 查询**翻译**成 SQL，下推到关系源执行（OBDA — Ontology-Based Data Access） |
| 语言栈 | 输入 SPARQL 1.1；映射用 R2RML（W3C 标准）或 Ontop Native；输出 SQL |
| 协议 | 暴露为 SPARQL 1.1 endpoint（Spring Boot 应用，官方 Docker 镜像，amd64/arm64） |
| 范式本质 | **零拷贝查询时联邦** —— 和 Neo4j Virtual Graph 是同一范式，但开源（Apache 2.0） |
| 版本 | v5（本文分析基准），4 层架构（inputs / Quest core / high-level API / clients） |
| 维护方 | Free University of Bozen-Bolzano + Ontopic s.r.l.（商业公司） + Birkbeck + Southeast University |
| 规模 | 2311 个 Java 文件，Maven 多模块，16k+ commits，20+ 年学术积累 |

**与 Neo4j Virtual Graph 的关键区别**：Ontop 是 **RDF/SPARQL 范式**（三元组 + W3C 标准），不是属性图/Cypher。这是它和 Gaia（Neo4j 属性图）的核心阻抗。

---

## 第二部分：核心架构 —— 五阶段查询翻译流水线

### 2.1 流水线总览

核心入口：`engine/reformulation/core/.../impl/QuestQueryProcessor.java` 的 `reformulateIntoNativeQuery()` 方法。

```
SPARQL 查询
   │
   ▼ ① translate                    KGQueryTranslator
SPARQL → IQ（Intermediate Query，统一中间表示）
   │
   ▼ ② rewrite                      QueryRewriter（TreeWitness 重写）
IQ → IQ（本体推理展开，把 intensional 节点替换为 extensional 的并集）
   │
   ▼ ③ unfold                       QueryUnfolder
IQ → IQ（把 RDF 三元组模式按 mapping 展开成 SQL 子查询，变量替换）
   │
   ▼ ④ optimize + plan              GeneralStructuralAndSemanticIQOptimizer + QueryPlanner
IQ → IQ（连接消除、投影下推、冗余去重、子查询合并等关系代数优化）
   │
   ▼ ⑤ generateSourceQuery          NativeQueryGenerator → SQLGeneratorImpl
IQ → SQL 字符串（按方言序列化，带 CTE/子查询/UNNEST 等）
   │
   ▼ 执行
JDBC → 源数据库（Trino/PG/MySQL/Snowflake/...）→ 结果集 → 转回 RDF
```

**关键设计**：五个阶段全部在 **IQ（Intermediate Query）** 这个统一数据结构上操作，IQ 同时承载 SPARQL 语义和关系代数语义。这是 Ontop 20 年学术积累的精华。

### 2.2 IQ（Intermediate Query）数据结构

位置：`core/model/.../iq/`

IQ 是一棵树，节点类型（`iq/node/` 目录）覆盖了 SPARQL 和关系代数的所有算子：

| 节点类型 | 对应语义 |
|---------|---------|
| `ExtensionalDataNode` | 表扫描（物理表，最终的 SQL FROM 子句来源） |
| `IntensionalDataNode` | 本体概念节点（需被 rewrite/unfold 展开） |
| `ConstructionNode` | 投影 + IRI 构造（把列值拼成 IRI/字面量） |
| `InnerJoinNode` / `LeftJoinNode` | 连接（INNER / LEFT OUTER） |
| `FilterNode` | 过滤（WHERE） |
| `UnionNode` | 并集（UNION ALL，rewrite 产生多分支） |
| `DistinctNode` | 去重 |
| `OrderByNode` | 排序 |
| `SliceNode` | LIMIT/OFFSET |
| `AggregationNode` | 聚合（GROUP BY） |
| `FlattenNode` | 数组展开（UNNEST） |
| `NativeNode` | 原生 SQL 片段（最终输出） |
| `EmptyNode` | 空结果（优化短路） |

**这个设计的价值**：SPARQL 的图模式匹配（BGP）、可选匹配（OPTIONAL）、并集（UNION）、过滤（FILTER）都被统一翻译成关系代数算子树，使得所有优化（连接顺序、投影下推、冗余消除）都能在统一框架下进行，且与目标 SQL 方言解耦。

### 2.3 缓存层

`QuestQueryProcessor` 在入口处先查 `QueryCache`（`GuiceBasedQueryCache`）——相同 SPARQL + QueryContext 直接返回缓存的 IQ，跳过整个重写流水线。这对 Gaia 有参考价值：图查询模式往往高度重复（Agent 多轮探索同一子图）。

---

## 第三部分：关键子系统剖析

### 3.1 映射（Mapping）—— 图三元组 ↔ SQL 的桥梁

位置：`mapping/sql/`（R2RML + Native 两种格式）

**核心抽象**：`SQLPPTriplesMap`（SQL Pre-Processed Triples Map）= 一条映射规则：
- **源查询（Source Query）**：一段 SQL，如 `SELECT * FROM orders`
- **目标查询（Target Query）**：RDF 三元组模板，如 `<{base}/orders/{id}> <{base}/orders#customer> <{base}/customers/{customer_id}>`

展开时（`BasicQueryUnfolder`），遇到一个 RDF 三元组模式 `(s, p, o)`，去 mapping 里找 predicate `p` 匹配的规则，把源查询的 SQL 子树替换进来，并把变量绑定到模板的占位符。

**这正是"图查询→SQL"的核心展开机制**：图模式不是在图引擎里执行，而是被**重写**成对源表的 SQL 查询。

### 3.2 直接映射（Direct Mapping）—— 自动生成图模型

位置：`mapping/sql/owlapi/.../bootstrap/impl/DirectMappingEngine.java` + `DirectMappingAxiomProducer.java`

这是 Ontop 的"自动建模"能力——连接数据库后，根据 W3C Direct Mapping 标准**自动生成映射规则和本体**。规则（`DirectMappingAxiomProducer` 实现）：

| 关系库对象 | 生成的图元素 | IRI 模板 |
|-----------|------------|---------|
| 表 `T` | RDF 类 | `{baseIRI}/T` |
| 行（有 PK） | 节点（IRI） | `{baseIRI}/T/{pkcol}={value}[;{pk2}={val2}]` |
| 行（无 PK） | 节点（空白节点 bnode） | 自动生成唯一 bnode |
| 列 `C` | 字面量属性 | `{baseIRI}/T#C` |
| 外键 `FK(T→U)` | 对象属性（边） | `{baseIRI}/T#ref-{fkcol}` |

源查询生成（`getSQL` / `getRefSQL`）：
- 表映射：`SELECT * FROM {table}`
- 外键映射：`SELECT {source_pk}, {target_pk} FROM {table} t, {ref_table} r WHERE {fk_cols JOIN conditions}`

**关键细节**：
- 无 PK 的表用 bnode，且 bnode 模板会跨外键复用（`bnodeTemplateMap` 缓存），保证同一行在不同查询里映射到同一 bnode
- 外键自引用（表外键指向自身）会加别名 `T_FK` / `T_FKR` 区分两端
- 列名做 IRI-safe 编码（`R2RMLIRISafeEncoder`）

**这正是 Neo4j Virtual Graph"AI 自动生成图模型"对应的开源实现**，只是规则是确定性的（W3C 标准），不是 LLM 驱动。

### 3.3 Lens（透镜）—— 虚拟视图 + 手工约束声明

位置：`db/rdb/.../dbschema/impl/json/JsonLens.java` + 子类

Ontop v5 的重要特性：在源数据库之外声明**虚拟视图**（不修改源库），类型有：
- `BasicLens`：投影/过滤视图
- `SQLLens`：任意 SQL 视图
- `JoinLens`：多表连接视图
- `UnionLens`：并集视图
- `FlattenLens`：数组展开视图

**最关键的设计**：Lens 允许**手工声明完整性约束**（源库不暴露或不存在时）：
- `UniqueConstraints`（含 `isPrimaryKey` 标记）
- `ForeignKeys`（声明跨表外键，含 `from` 列和 `to.relation`+`to.columns`）
- `OtherFunctionalDependencies`（函数依赖）
- `NonNullConstraints`（非空约束）
- `IRISafeConstraints`（IRI 安全编码声明）

**这个设计对 Gaia 极有价值**：Trino 联邦查询无法提取源库的 PK/FK（见 §4），Lens 机制允许在 Ontop 侧**补声明**这些约束，使查询优化（连接消除、冗余去重）能正常工作。

### 3.4 SQL 生成与方言适配

位置：`engine/reformulation/sql/.../impl/SQLGeneratorImpl.java` + `db/rdb/.../generation/serializer/impl/`

`SQLGeneratorImpl.generateSourceQuery()` 流水线：
1. `rdfTypeLifter` —— 把 RDF type 提升到 ConstructionNode
2. `functionLifter` —— 把可后处理的函数符号提升
3. `projectionSplitter` —— 拆分投影（哪些在源库算，哪些在后处理算）
4. `normalizeSubTree` —— lift Slice / OrderBy / Distinct
5. `defaultIQTree2NativeNodeGenerator` —— IQ 树 → `NativeNode`（SQL 字符串）

**方言序列化器**（`*SelectFromWhereSerializer`）按 DB 类型生成不同 SQL：
- `TrinoSelectFromWhereSerializer`：`OFFSET x LIMIT y` 顺序、`TIMESTAMP '...'` 字面量、`CROSS JOIN UNNEST(...) WITH ORDINALITY`
- `PostgresDialectExtraNormalizer`：PG 特有的类型规范化
- 还有 MySQL / SQLServer / Oracle / DB2 / Snowflake / SparkSQL / DuckDB / Dremio / MonetDB 等

### 3.5 Endpoint —— 独立服务部署

位置：`client/endpoint/`

- `OntopEndpointApplication`：Spring Boot 应用，默认 8080 端口
- `SparqlQueryController`：SPARQL 查询端点
- `PortalController` / `PortalConfigController`：预定义查询门户
- `OntologyFetcherController`：本体下载
- `ReformulateController`：直接获取重写后的 SQL（调试用）
- 官方 Docker 镜像（`ontop/ontop`），多平台，基于 Eclipse Temurin JRE 11，用 `jlink` 裁剪 JRE 减小体积

**部署形态**：独立 sidecar 服务，配置文件（mapping + 数据源连接）挂载即可，**不修改源数据库**。

---

## 第四部分：Trino 支持现状

Ontop 原生支持 Trino，这对 Gaia 极其有价值。但有一个**关键限制**。

### 4.1 支持的部分

- `TrinoDBMetadataProvider`：从 Trino JDBC 提取表/列 schema
- `TrinoQuotedIDFactory`：Trino 的标识符引用规则（双引号）
- `TrinoSelectFromWhereSerializer`：Trino 方言 SQL 序列化
- 过滤 `information_schema` 和 `system` catalog

### 4.2 关键限制：Trino 无法提取完整性约束

`TrinoDBMetadataProvider.insertIntegrityConstraints()` 源码注释明确写道：

> Trino does not support the extraction of integrity constraints.
> Furthermore, running the method `insertUniqueConstraints` throws an exception, because it accesses
> the method `getIndex` on the Trino MetaData which is not supported by the Trino JDBC.
> Therefore, we skip this method.

**影响**：
- 通过 Trino 联邦查询外部源时，**Ontop 无法自动发现 PK / FK / UK**
- 没有 PK → 直接映射无法生成行节点 IRI（退化为 bnode）
- 没有 FK → 无法自动生成外键关系边
- 查询优化器无法做连接消除（不知道哪些列是唯一的）

**Ontop 的解法**：用 **Lens 机制**（§3.3）手工声明这些约束。即在 Ontop 侧维护一份"约束元数据"，独立于 Trino。

**对 Gaia 的启示**：Gaia 已经有 ObjectType 的 `properties`（含 PK 标记）和 LinkType（关系定义）——这本身就是一份"约束元数据"。如果走 Ontop 路径，可以用 Gaia 的本体元数据**反向喂给 Ontop 的 Lens 声明**，绕过 Trino 的限制。

---

## 第五部分：与 Gaia 的对照分析

### 5.1 范式对照

| 维度 | Ontop | Gaia 当前 |
|------|-------|----------|
| 图模型 | RDF 三元组（W3C 标准） | Neo4j 属性图 |
| 查询语言 | SPARQL | Cypher（经 GraphProjector 派生） |
| 数据落地 | **不落地**（查询时翻译成 SQL 下推） | MANAGED 落地 Iceberg（归档）+ Doris（在线读主源）；VIRTUAL 不落地（Trino 联邦） |
| 图的来源 | 全部虚拟（mapping 声明） | MANAGED 实投影；VIRTUAL 当前不入图 |
| 写入 | 只读（不写源库） | MANAGED 可写（Action→object_state→投影）；VIRTUAL 只读 |
| 元数据 | mapping 文件（R2RML/Native）+ Lens 约束 | PostgreSQL 本体表（ObjectType/Property/LinkType） |
| 联邦引擎 | 内置 JDBC → 多种 DB | Trino（已部署） |

### 5.2 架构角色对照

Ontop 在 Gaia 里能扮演的角色，取决于 Gaia 选择哪条路径（见 [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) §5）：

| 路径 | Ontop 的角色 | 可行性 |
|------|------------|--------|
| ① 身份骨架复制 | 不相关（Gaia 自己从 Trino 拉 PK 即可） | — |
| ② indexed 属性复制 | 不相关（Gaia 自己从 Trino 拉 PK+indexed） | — |
| ③' 自研查询时联邦 | **参考对象**（学其 IQ + mapping + unfold 设计） | ✅ 参考价值高 |
| ④ Ontop 作为联邦查询 sidecar | **直接集成**（Ontop 暴露 SPARQL endpoint，Gaia 调用） | 🟡 见 §5.3 |

### 5.3 "Ontop 作为 sidecar" 方案的可行性评估

设想：部署 Ontop 作为独立服务，指向 Trino，暴露 SPARQL endpoint；Gaia 的图推理引擎在遇到 VIRTUAL 对象时，调 Ontop 的 SPARQL endpoint 查询（而不是查 Neo4j 缓存）。

**优点**：
- ✅ 零拷贝，数据永远最新（查时下推到 Trino→外部源）
- ✅ 不需要给 Neo4j 填 VIRTUAL 数据，绕过 C8 一致性模型冲击
- ✅ Ontop 原生支持 Trino，有生产级 Docker 镜像
- ✅ 自动 direct mapping 减少手工建模（虽然 Gaia 已有本体，可复用）

**致命问题**：
- ❌ **范式不匹配**：Ontop 是 RDF/SPARQL，Gaia 是属性图/Cypher。Gaia 的 `search_around`/`find_paths` 返回的是属性图节点，如果要调 Ontop，需要把 Cypher 图遍历翻译成 SPARQL BGP——这本身就是另一个"查询翻译器"工程
- ❌ **跨两套图模型**：MANAGED 对象在 Neo4j（属性图），VIRTUAL 对象在 Ontop（RDF）。一条 `find_paths` 跨两者需要**双引擎联合查询**，复杂度爆炸
- ❌ **Trino 无法提取约束**（§4.2）：要么手工维护 Lens 约束（额外元数据负担），要么查询优化失效
- ❌ **额外服务**：增加一个 Spring Boot/Java 服务到 Gaia 的 Python 栈，运维复杂度上升
- ❌ **SPARQL 学习曲线**：Gaia 团队是 Python/属性图背景，SPARQL/RDF 是另一套知识体系

**结论**：直接集成 Ontop 作为 sidecar **不推荐**——范式阻抗（RDF vs 属性图）带来的翻译成本，抵消了零拷贝的好处。

---

## 第六部分：可参考点清单

虽然不直接集成，但 Ontop 的**设计思想**有多个点值得 Gaia 借鉴：

### 参考 1：IQ 统一中间表示（★★★ 高价值）

**Ontop 做法**：SPARQL 和关系代数都翻译成统一的 IQ 树，所有优化在 IQ 上做，最后再序列化成方言 SQL。

**Gaia 可参考**：Gaia 已有 ObjectSet IR（ADR-015，对齐 Palantir 13/15 type）作为查询中间表示。但当前 ObjectSet IR 的执行器（`DataFrameQueryService`）是**按 type 分流**（属性→PG、空间→PostGIS、时序→TimescaleDB、searchAround→Neo4j），缺少一个**统一的查询优化层**。

可借鉴 Ontop 的思路：在 ObjectSet IR 上加一层**关系代数优化**（连接顺序、投影下推、冗余消除），特别是当 IR 跨多个数据源时（MANAGED 属性在 Doris + VIRTUAL 属性在 Trino + 图关系在 Neo4j），优化器决定哪些操作下推、哪些在内存合并。

**落地建议**：不需要照搬 IQ 的完整节点体系，但可以参考其"join/filter/union/projection"四个核心算子的优化规则，给 `DataFrameQueryService` 加一个 `optimize()` 阶段。

### 参考 2：Mapping 声明式映射（★★★ 高价值）

**Ontop 做法**：用 mapping 文件声明"图三元组模式 ↔ SQL 查询"的映射，查询时按 mapping 展开。mapping 是声明式的，与查询解耦。

**Gaia 可参考**：Gaia 当前的 VIRTUAL 对象查询是**命令式**的——`ObjectQueryService._resolve_query_target` 里 `if storage_type == VIRTUAL: return _virtual_table_ref(ot)`，直接拼 Trino table ref。这缺乏一层"映射抽象"。

如果未来要做路径 ③'（自研查询时联邦），可以借鉴 Ontop 的 mapping 思路：为 VIRTUAL ObjectType 声明一份"属性 ↔ SQL 列"的映射（实际上 Gaia 的 `ObjectType.properties` + `link_dataset` 已经是这份映射的数据源），查询引擎按映射展开成 Trino SQL。这比硬编码 `_virtual_table_ref` 更灵活，且能支持属性重命名、计算列、多表 join 等复杂映射。

### 参考 3：Lens 手工约束声明（★★ 中价值）

**Ontop 做法**：源库不暴露 PK/FK 时（如 Trino），在 Ontop 侧用 Lens 声明约束，让查询优化器正常工作。

**Gaia 可参考**：Gaia 的本体元数据（ObjectType 的 PK、LinkType 的关系）天然就是一份"约束声明"。当通过 Trino 联邦查询外部源时，可以把 Gaia 本体里的约束**下推**给 Trino 查询优化器（虽然 Trino 本身的优化器用不上，但 Gaia 自己的查询编排层可以用）。

特别是：知道一个列是 PK → 查询结果天然去重，不需要 `DISTINCT`；知道外键关系 → join 可以转 semi-join。这些优化在 Gaia 的 `DataFrameQueryService` 层面可以做。

### 参考 4：Direct Mapping 自动建模（★★ 中价值）

**Ontop 做法**：连数据库后自动按 W3C 标准生成图模型（表→类、行→节点、列→属性、外键→边）。

**Gaia 可参考**：Gaia 的 BuildWith 脚手架（`/ai/scaffold`）已经在做"NL→ObjectType 生成"。但对外部数据源的**自动 schema → 本体**推导还比较弱（当前是 `register_virtual_table` 手动登记）。

可借鉴 Ontop 的 Direct Mapping 规则：连接外部源后，自动扫描 schema，按规则推导出 ObjectType 草稿（表→ObjectType、PK→identity property、FK→LinkType），让用户在草稿基础上修改。这比从零建模快。

### 参考 5：查询缓存（★ 低价值但易实现）

**Ontop 做法**：`QueryCache` 缓存 SPARQL→IQ 的重写结果，相同查询直接命中。

**Gaia 可参考**：图探索画布是 AG-UI Agent 驱动，多轮 ReAct 会反复查相似子图。可以在 `DataFrameQueryService` 加一层查询结果缓存（按 ObjectSet IR 的哈希做 key），避免重复下推 Trino。这个改动很小但收益明显。

### 参考 6：端点调试接口（★ 低价值）

**Ontop 做法**：`ReformulateController` 暴露"获取重写后 SQL"的接口，方便调试。

**Gaia 可参考**：Gaia 的 `/objects/{ont}/query-dataframe` 可以加一个 `?explain=true` 参数，返回实际下推到 Doris/Trino 的 SQL，方便排查查询性能问题。当前调试需要看日志，不够直观。

---

## 第七部分：不可照搬的差异

### 差异 1：RDF vs 属性图（根本性）

Ontop 基于 RDF 三元组（s, p, o），所有数据都是"主语-谓词-宾语"。Gaia 基于 Neo4j 属性图，节点有 label + 多属性，边有 type + 多属性。

- RDF 的"属性"是独立的三元组（`s hasName "张三"`），属性图是节点的 property
- RDF 的"关系"也是三元组（`s knows o`），属性图是 edge
- RDF 用 IRI 全局标识，属性图用内部 id

**不能照搬**：Ontop 的 mapping 格式（R2RML/三元组模板）直接套到 Gaia 会很别扭。Gaia 如果借鉴 mapping 思路，应该设计**属性图版**的映射（ObjectType→label、Property→node property、LinkType→edge type），而不是套 R2RML。

### 差异 2：SPARQL vs Cypher 查询语义

SPARQL 的 BGP（基本图模式）和 Cypher 的 MATCH 在语义上有差异：
- SPARQL 是**集合语义**（无序、去重需 DISTINCT）
- Cypher 是**路径语义**（MATCH 可以返回路径，关系可以重复遍历）
- SPARQL 的 OPTIONAL ≈ Cypher 的 OPTIONAL MATCH，但变量绑定规则不同
- SPARQL 的 UNION ≈ Cypher 的 UNION，但类型推断不同

**不能照搬**：Ontop 的 TreeWitness 重写算法（针对 OWL 2 QL 本体的查询重写）对 Gaia 无用——Gaia 没有基于描述逻辑的本体推理需求。Gaia 的图推理是 AG-UI Agent 驱动的多跳遍历，不需要本体一致性推理。

### 差异 3：只读 vs 读写

Ontop 是**纯只读**系统（OBDA 的 A 是 Access，但实际只做查询，不写回源库）。Gaia 的 MANAGED 对象是**可写**的（Action→object_state→投影）。

**不能照搬**：Ontop 的架构里没有"写入路径"，没有 outbox、没有投影一致性。Gaia 如果参考 Ontop 的查询翻译，**只能用于 VIRTUAL 对象的只读联邦查询**，不能用于 MANAGED 的写入投影链路。

### 差异 4：Java vs Python 技术栈

Ontop 是 Java + Guice（依赖注入）+ Spring Boot。Gaia 是 Python + FastAPI。

**不能照搬**：不能直接引入 Ontop 的 Java 库（除非走 sidecar HTTP 调用，但见 §5.3 的范式问题）。如果要借鉴其算法，需要用 Python 重新实现核心逻辑。

### 差异 5：学术标准 vs 工程实用

Ontop 严格遵循 W3C 标准（R2RML、SPARQL、OWL 2 QL、Direct Mapping），有大量学术正确性证明。Gaia 是工程驱动，优先实用而非标准合规。

**不能照搬**：Ontop 的很多复杂度（TreeWitness 重写、T-mappings、本体分类）是为了处理 OWL 本体推理的边界情况，Gaia 不需要这些。借鉴时应该**只取工程实用的部分**（mapping 展开、SQL 生成、方言适配、查询优化），跳过学术理论部分。

---

## 第八部分：结论与建议

### 8.1 核心判断

**Ontop 不适合作为 Gaia 的直接集成组件**（范式阻抗：RDF/SPARQL vs 属性图/Cypher，且 Trino 无法自动提取约束），但其**设计思想**对 Gaia 的路径 ③'（自研查询时联邦）有明确的参考价值。

### 8.2 建议的参考优先级

| 优先级 | 参考点 | 落地时机 | 工作量 |
|--------|--------|---------|--------|
| P1 | 参考 2：Mapping 声明式映射 | 路径 ③' 设计阶段 | 中（设计属性图版映射抽象） |
| P1 | 参考 1：IQ 统一中间表示 | `DataFrameQueryService` 优化器迭代 | 中（给 ObjectSet IR 加优化层） |
| P2 | 参考 3：Lens 手工约束声明 | 路径 ③' 联邦查询优化 | 小（复用 Gaia 本体元数据） |
| P2 | 参考 4：Direct Mapping 自动建模 | BuildWith 脚手架增强 | 中（schema→ObjectType 草稿推导） |
| P3 | 参考 5：查询缓存 | `DataFrameQueryService` 性能优化 | 小 |
| P3 | 参考 6：explain 调试接口 | 查询性能排查工具 | 小 |

### 8.3 对路径选择的最终建议

结合 [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) 的结论和本次 Ontop 分析：

1. **MVP 走路径 ①**（身份骨架复制到 Neo4j）——Ontop 在此阶段无关，Gaia 自己从 Trino 拉 PK 即可
2. **按需走路径 ②**（indexed 属性复制）——同样不涉及 Ontop
3. **远期路径 ③'**（自研查询时联邦）——**参考 Ontop 的 mapping + IQ 设计**，但用 Python 实现属性图版，不集成 Ontop 本身

**关键决策依据**：Ontop 证明了"查询时联邦"范式在学术和工程上都成熟，但其 RDF/SPARQL 栈与 Gaia 的属性图栈不兼容。Gaia 应该吸收其**声明式映射 + 统一中间表示 + 查询优化**的设计思想，在 Python/属性图栈里自研轻量版，而不是引入一个重量级 Java sidecar。

### 8.4 Ontop 源码的保留价值

克隆的源码（`/tmp/pi-github-repos/ontop/ontop`）建议保留至路径 ③' 设计完成，重点参考的具体文件：

| 关注点 | 文件 |
|--------|------|
| 流水线编排 | `engine/reformulation/core/.../impl/QuestQueryProcessor.java` |
| IQ 数据结构 | `core/model/.../iq/node/` 全部节点类型 |
| 映射展开 | `core/kg-query/.../query/unfolding/impl/BasicQueryUnfolder.java` |
| 自动建模规则 | `mapping/sql/owlapi/.../bootstrap/impl/DirectMappingAxiomProducer.java` |
| Lens 约束声明 | `db/rdb/.../dbschema/impl/json/JsonLens.java` |
| SQL 方言序列化 | `db/rdb/.../generation/serializer/impl/TrinoSelectFromWhereSerializer.java` |
| Trino 元数据限制 | `db/rdb/.../dbschema/impl/TrinoDBMetadataProvider.java`（§4.2 关键限制） |
| 查询优化器 | `core/optimization/.../iq/optimizer/` 目录 |

---

## 附录：Ontop 模块结构速查

```
ontop/                          (version5 分支, 2311 Java 文件)
├── core/                       核心抽象（与具体 DB/协议无关）
│   ├── model/                  IQ 数据结构 + term/atom/type 体系 + dbschema 抽象
│   ├── kg-query/               SPARQL 解析 + KGQuery 抽象 + QueryUnfolder
│   ├── obda/                   Mapping + Ontology 规范（OBDA spec）
│   └── optimization/           IQ 优化器（join 消除、投影下推、冗余去重、lens 展开）
├── engine/                     查询执行引擎（Quest）
│   ├── reformulation/core/     ★ 流水线入口 QuestQueryProcessor + rewriting + generation 抽象
│   ├── reformulation/sql/      ★ SQL 生成器 SQLGeneratorImpl
│   └── system/                 系统级装配（OWLAPI + SQL 配置）
├── mapping/                    映射格式
│   ├── sql/owlapi/             ★ Direct Mapping 自动建模 DirectMappingEngine
│   ├── sql/native/             Ontop Native 映射格式（解析+序列化）
│   ├── sql/r2rml/              W3C R2RML 标准映射格式
│   └── sql/all/                聚合
├── db/rdb/                     关系数据库适配
│   ├── dbschema/               ★ MetadataProvider + Lens 机制（JsonLens 系列）
│   ├── dbschema/impl/          ★ 各 DB 元数据提取（Trino/PG/MySQL/Oracle/SQLServer...）
│   └── generation/             ★ SQL 序列化器（Trino/PG/MySQL/Snowflake/SparkSQL/DuckDB...）
├── binding/                    RDF 框架绑定（owlapi / rdf4j）
├── client/                     部署形态
│   ├── endpoint/               ★ Spring Boot SPARQL endpoint 服务
│   ├── endpoint-core/          查询执行器
│   ├── cli/                    命令行工具
│   └── docker/                 ★ 官方 Docker 镜像（多平台）
├── ontology/                   本体处理（OWL 2 QL 分类、T-mappings）
├── protege/                    Protégé 插件（桌面 GUI，Gaia 不需要）
└── test/                       测试套件
```
