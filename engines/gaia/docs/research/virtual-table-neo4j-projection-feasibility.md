# 虚拟表（VIRTUAL）对象填充 Neo4j 可行性调研

> **用途**：评估 Gaia 虚拟表（`storage_type=VIRTUAL`）对象能否填充 Neo4j 图数据库，以及业界在"让图数据库看到外部已有数据"这一场景下的主流范式与选型判据。本文输出的是评估依据与设计选项，Gaia 落地方案另立 ADR + 设计文档修订。
> **研究方法**：以 Neo4j 官方文档（neo4j.com/docs）、官方博客（neo4j.com/blog）、产品页与定价页为第一手来源核对特性可用性与版本门槛；以 Neo4j polyglot persistence 官方博客、Virtual Graph 产品发布博客、Layers appview 文档、Ontop VKG 文档为业界实践对照；以 Gaia 源码（`project_sync_service.py` / `outbox_executor.py` / `graph_projector.py` / `object_query_service.py`）为现状核对基准。
> **研究日期**：2026-07-14
> **关联文档**：[`graph-reasoning-design.md`](../architecture/graph-reasoning-design.md) §6（投影机制设计）· [`dataset-ontology-binding.md`](../design/dataset-ontology-binding.md)（VIRTUAL 术语体系）· [`adr-015-agent-driven-graph-explore.md`](../architecture/adr-015-agent-driven-graph-explore.md)（图探索画布）· [`implementation-status.md`](../architecture/implementation-status.md) §十二（图推理实施状态）

---

## 目录

- [第一部分：问题背景与现状结论](#第一部分问题背景与现状结论)
- [第二部分：业界三种主流模式](#第二部分业界三种主流模式)
- [第三部分：Neo4j Virtual Graph 深度核实](#第三部分neo4j-virtual-graph-深度核实)
- [第四部分：第三方实证](#第四部分第三方实证)
- [第五部分：方案选型与推荐](#第五部分方案选型与推荐)
- [第六部分：落地设计锚点](#第六部分落地设计锚点)
- [第七部分：待决策点](#第七部分待决策点)

---

## 第一部分：问题背景与现状结论

### 1.1 问题陈述

Gaia 本体对象分两种 `storage_type`：

- **MANAGED（托管）**：数据落地 Iceberg，Doris 作在线读主源。Action 写入触发 `object_state` 变更 → OutboxExecutor fan-out → GraphProjector 投影到 Neo4j。**完整投影链路已实现**。
- **VIRTUAL（虚拟）**：外部源表的联邦代理指针，数据不落地。查询时经 `ObjectQueryService._resolve_query_target` 路由到 Trino 联邦查询外部源表。**对齐 Palantir Foundry 虚拟表语义**（见 [`dataset-ontology-binding.md`](../design/dataset-ontology-binding.md) §一）。

**核心问题**：虚拟表对象能否填充 Neo4j？即 VIRTUAL 对象在创建/更新时是否触发 GraphProjector 将数据投影到 Neo4j，使得图关联推理（`search_around` / `find_paths` / `exists_link`）能跨过 VIRTUAL 节点不断链。

### 1.2 现状结论：当前架构明确拒绝了虚拟表填充 Neo4j

当前实现里有**双重保险**阻断 VIRTUAL 对象进入图投影：

**保险 1：ProjectSyncService 的 Gate 1 硬门控**

[`src/ontology/services/project_sync_service.py:108-111`](../../src/ontology/services/project_sync_service.py)
```python
# Gate 1: VIRTUAL 类型无数据落地。
if ot.storage_type == "VIRTUAL":
    _log.debug("project_for_object_type: %s/%s is VIRTUAL, skip", ontology_api_name, object_type_api_name)
    return result
```

ProjectSyncService 是 Iceberg→Neo4j/PostGIS 的桥梁（SeaTunnel 批量接入触发路径）。Gate 1 在入口处直接 skip VIRTUAL 类型，根本不读 Iceberg 数据。

**保险 2：Action 写入路径天然走不到 VIRTUAL**

红线 9（CLAUDE.md）规定 `VIRTUAL 目标禁止写入`：`ActionService.execute_action` 对 `storage_type=VIRTUAL` 目标直接 `ValidationError` 拒绝。因此 VIRTUAL 对象不会产生 outbox INDEX event，OutboxExecutor 的 `_project_object_upsert`（[`outbox_executor.py:448`](../../src/ontology/services/outbox_executor.py)）不会被触发。

**结论**：两条投影触发路径（Action 写入 / SeaTunnel→Iceberg backfill）都到不了 VIRTUAL 对象。设计上是刻意的。

### 1.3 拒绝背后的三个真实约束

| 约束 | 含义 | 对"填充 Neo4j"的影响 |
|------|------|---------------------|
| **C3 红线：VIRTUAL 不落地** | 虚拟表是 Trino 联邦查询外部源的代理指针，Gaia 不持有数据副本 | ProjectSyncService 的数据源是 `IcebergStore.scan_latest`——VIRTUAL 没有 Iceberg 表，扫不到数据 |
| **一致性模型 C8：投影是同源派生副本** | Neo4j 是 `object_state/Iceberg` 的投影（`graph-reasoning-design.md §6.3`），单向、可重建、禁止反写 | VIRTUAL 的"源"在外部系统，且 Gaia 不写——投影的"同源可信源"这个前提塌了 |
| **写入触发模式 A/B 都依赖落地** | A=Action 写 PG object_state；B=SeaTunnel 批量灌 Iceberg（见 §6.1） | VIRTUAL 既无 object_state 也无 Iceberg 行，两条触发链都断 |

**核心矛盾**：Neo4j 在 Gaia 里不是"另一个数据源"，而是 Gaia 自己写入数据的**派生索引**。VIRTUAL 的数据 Gaia 根本不写，所以"派生"无从谈起。

---

## 第二部分：业界三种主流模式

Neo4j 官方在 polyglot persistence 文档中把"如何让 Neo4j 看到关系库/数仓里的数据"归纳为三条路径，是业界最权威的归纳。

> 来源：[NoSQL polyglot persistence: Tools and integrations with Neo4j](https://neo4j.com/blog/cypher-and-gql/nosql-polyglot-persistence-tools-integrations/)（GraphConnect Europe 2016，William Lyon）

### 2.1 模式 A：全量迁移（Migrate All）

把所有数据搬到 Neo4j。

- **一致性**：强一致（单源）
- **延迟**：毫秒
- **适用**：数据天然图形态、需 ACID 写
- **业界代表**：知识图谱、客户 360、Agent memory graph

### 2.2 模式 B：子集迁移（Migrate Subset）

把图相关数据搬到 Neo4j，非图数据留原库。图查询走 Neo4j，非图查询走原库。

- **一致性**：各自独立
- **延迟**：毫秒（图）/ 原库（非图）
- **适用**：图查询和非图查询职责分治
- **业界代表**：推荐系统（关系库存商品，Neo4j 存用户-商品交互）

### 2.3 模式 C：子集复制（Duplicate Subset）★ Gaia 当前走的就是这条

主库为单一真相源，图相关数据**复制**一份到 Neo4j 作派生索引。主库写入 → 同步到 Neo4j。

- **一致性**：最终一致
- **延迟**：毫秒（图查询，因为数据已在 Neo4j）
- **适用**：主库 + 图索引并存，图是只读派生
- **业界代表**：
  - **Neo4j Doc Manager**：tail MongoDB OPLOG → 转换为 Cypher → 流式写入 Neo4j（[文档](https://neo4j.com/developer/perl/)）
  - **Neo4j Cassandra Data Import Tool**：检查 Cassandra schema → 应用翻译规则（列族→节点）→ 生成 LOAD CSV Cypher 导入
  - **Layers appview**（见第四部分）

**关键洞察**：Gaia 当前的 `GraphProjector` 走的就是**模式 C**（Doris 在线读主源 + Iceberg 归档为单一真相源；PG object_state 是 Action 写入态，经 OutboxExecutor fan-out 触发投影；Neo4j 是同源分发派生副本）。这和 Neo4j 官方推荐的“主库 + 图索引”范式完全对齐。

---

## 第三部分：Neo4j Virtual Graph 深度核实

2025 年 Neo4j 推出 **Virtual Graph** 产品，定位是"零拷贝联邦查询"——让 Cypher 直接查数仓里的数据，不搬数据。这几乎是为虚拟表场景量身定做的范式。

> 来源：
> - [Introducing Neo4j Virtual Graph](https://neo4j.com/blog/graph-database/introducing-neo4j-virtual-graph-graph-reasoning-on-the-data-you-already-have/)（首发博客）
> - [Neo4j Virtual Graph is now in public preview](https://neo4j.com/blog/auradb/neo4j-virtual-graph-is-now-in-public-preview/)（public preview 博客）
> - [Graph intelligence on your existing data](https://neo4j.com/product/virtual-graph/)（产品页）
> - [Zero-copy graph reasoning on Snowflake](https://neo4j.com/blog/developer/zero-copy-graph-reasoning-on-snowflake-getting-started-with-neo4j-virtual-graph/)（实战博客）
> - [Cloud & self-hosted graph database platform pricing](https://neo4j.com/pricing/)（定价页）

### 3.1 工作原理

Virtual Graph 的三个组件协同工作：

1. **数据模型**：AI 从源表 schema 自动生成图模型（哪些表变节点、哪些外键变关系、哪些列变属性），用户可编辑
2. **查询翻译器**：把 Cypher 模式编译成优化的 SQL，下推到数仓执行。**编译是确定性的（非 LLM 驱动）**，每次产生相同 SQL，性能与成本可预测
3. **图计算层**：处理 SQL 无法高效表达的图特定操作（模式匹配、遍历、算法）

### 3.2 选型判据（Neo4j 官方明确给出）

| 维度 | Virtual Graph（零拷贝） | 原生 Neo4j 存储 |
|------|----------------------|----------------|
| 延迟容忍度 | 秒~分钟级（GraphRAG、批量富化、分析探索、Agent 多步推理） | 毫秒级（实时决策、在线欺诈评分、会话内推荐） |
| 写需求 | 只读（数据在源系统） | ACID 写 |
| 数据规模/治理 | 太大/太受管控/太操作化，不能搬 | 天然图形态 |
| 一致性 | 实时（查时下推，永远最新） | 最终一致（投影滞后） |
| 数据形态 | "agents that need to **think** in seconds" | "agents that need to **act** in milliseconds" |

**Neo4j 官方最推荐的模式是 "A common pattern: run both"**——参考数据走 Virtual Graph，操作图走原生存储，单条 Cypher 跨两者查询（composite query，路线图重点）。

### 3.3 版本门槛核实（★ 关键确认点）

**核实结论：Virtual Graph 只在 Aura 云托管服务上提供，社区版（Community Edition）和企业版自托管（Enterprise Edition）都不包含，且目前仍是 public preview 阶段。**

证据链：

| 来源 | 关键表述 |
|------|---------|
| [产品页](https://neo4j.com/product/virtual-graph/) | Virtual Graph 列在 Aura 云服务产品线下 |
| [public preview 博客](https://neo4j.com/blog/auradb/neo4j-virtual-graph-is-now-in-public-preview/) | "Virtual Graph moves to public preview, open to **every Aura customer**" |
| [首发博客](https://neo4j.com/blog/graph-database/introducing-neo4j-virtual-graph-graph-reasoning-on-the-data-you-already-have/) | "It runs natively in **Neo4j Aura**, behind the same surface you already use for AuraDB" |
| [Snowflake 实战博客](https://neo4j.com/blog/developer/zero-copy-graph-reasoning-on-snowflake-getting-started-with-neo4j-virtual-graph/) | "It carries the full Neo4j toolset on top: Bloom, GDS, agents, Browser"——是 Aura 托管服务的组成部分 |
| [定价页](https://neo4j.com/pricing/) | 自托管版（Community / Enterprise）的 feature 列表里**完全没有** Virtual Graph；Virtual Graph 只在 Aura 产品线下 |
| [AuraDB FAQ](https://neo4j.com/cloud/platform/aura-graph-database/faq/) | AuraDB Free tier 只提 200k 节点/400k 关系，未提 Virtual Graph——大概率 Professional 及以上 |

**对 Gaia 的直接影响**：

Gaia 当前部署用的是 `neo4j:5-community`（社区版自托管，见 `docker-compose.yml:525` 和 `deploy/k8s/infra/optional/neo4j.yaml:35`）。因此：

> **Neo4j 原生 Virtual Graph（零拷贝联邦查询）在 Gaia 当前技术栈下根本不可用** —— 除非把图数据迁移到 Aura 云服务，这违背 Gaia 的私有化部署原则（本地 k3s / 国产化场景）。

如果要在 Gaia 实现"查询时联邦"的体验，必须**自研**，不能依赖 Neo4j Virtual Graph。

### 3.4 Virtual Graph 路线图（供参考）

Neo4j 官方公布的后续方向：
- **更多数据源**：任何 JDBC/SQL 接口的系统都在范围内
- **Adaptive caching**：热子图物化，降低重复 Agent 工作负载的延迟，减少外部计算开销
- **Composite queries across Aura and Virtual Graph**：单条 Cypher 跨原生图 + 虚拟图（self-managed 已支持，Aura 路线图）
- **Deeper agent integration**：原生 GraphRAG 原语、语义层 hooks、工具定义
- **Cypher 和 GQL parity**：AuraDB 上写的查询在 Virtual Graph 上不改即可用

---

## 第四部分：第三方实证

### 4.1 Layers appview —— "四后端派生索引"架构（★ 与 Gaia 高度同构）

> 来源：[Database Design | Layers Documentation](https://docs.layers.pub/appview/database-design)

Layers（ATProto 生态）的 `appview` 是一个非常接近 Gaia 的实战案例。它把数据持久化到四个后端：

> "PostgreSQL is the authoritative source of truth, Elasticsearch and Neo4j are **derived indexes optimized for specific query patterns**... Every piece of data in Elasticsearch and Neo4j can be reconstructed from PostgreSQL."

**实现要点**：
- 所有写入经 firehose → PG → fan-out 到 ES/Neo4j/Redis（`RecordSink` trait，可选 feature flag）
- **Neo4j sink 只做 `MERGE` 节点 + 引用关系**（`MEMBER_OF` / `CITES_CORPUS` 等关系类型），不拆属性到节点上
- 节点只存 `uri + did + nsid + body`（body 是完整 JSON），全量属性查询回 PG
- 删除用 `MATCH (n) DETACH DELETE n`

**对 Gaia 的启示**：
- 这印证了"模式 C 派生索引"在业界是被验证过的合理设计，不是偷懒
- 他们的 Neo4j sink 不拆属性到节点——和 Gaia 的 GraphProjector"仅投影 indexed 属性"思路一致，但更激进（Gaia 至少保留了 indexed 属性用于剪枝）
- fan-out 模式与 Gaia 的 OutboxExecutor 几乎一模一样

### 4.2 Ontop Virtual Knowledge Graph —— 开源的零拷贝替代（参考价值有限）

> 来源：[Introduction | Ontop](https://ontop-vkg.org/guide/) · [Flexible Enterprise Knowledge Graphs with Virtualization and Apache Iceberg](https://ontopic.ai/en/tech-notes/create-virtual-knowledge-graphs-on-apache-iceberg/)

Ontop 是开源（Apache 2）的 Virtual Knowledge Graph 系统，把关系库内容暴露为知识图谱，数据不落地。明确支持 Trino 作为联邦查询引擎，且与 Apache Iceberg 结合有专门方案。

**但 Ontop 是 SPARQL/RDF 范式，不是 Cypher/属性图**——和 Gaia 的 Neo4j 属性图模型不直接兼容，接入成本高。

**结论**：不推荐作为 Gaia 的主路径，仅作为"查询时联邦"自研路径（路径 ③'）的设计参考——看它怎么把图查询编译成 SQL 下推。

### 4.3 Memgraph MemGQL —— 联邦 GQL（参考）

> 来源：[Federated GQL Across Heterogeneous Backends](https://memgraph.com/docs/memgraph-zero/memgql/use-cases/federated-gql)

Memgraph 的 MemGQL 把任意注册后端暴露为图，单一 GQL 端点（ISO/IEC 39075）。同样是"查询时联邦"范式，但属于另一款图数据库产品，不在 Gaia 选型范围内。

### 4.4 Neo4j CDC —— 反向同步（不适用本场景）

> 来源：[Neo4j CDC 文档](https://neo4j.com/docs/cdc/current/) · [Neo4j Connector for Kafka](https://neo4j.com/docs/kafka/5.0/architecture/sinkconsume/)

Neo4j 自身提供 CDC（Change Data Capture），用于把 Neo4j 的变更**流出到**其他系统（如 Kafka）。这是**反向**——从 Neo4j 同步出去，不是把外部数据同步进来。

与本场景（外部数据 → Neo4j）方向相反，不适用。但记录备查：如果未来 Gaia 需要"Neo4j 变更反向同步回 object_state"（目前设计禁止反写，C8），可参考此机制。

---

## 第五部分：方案选型与推荐

### 5.1 可选路径全景

```
                        Gaia 当前
                        ─────────
MANAGED 对象 ──→ Doris(在线读主源)/Iceberg(归档) ──→ GraphProjector ──→ Neo4j
                 (单一真相源)            (模式 C: 派生索引)    ✅ 已实现
  *注：Action 写入 PG object_state(写入态) → outbox INDEX effect 同步 Doris → GraphProjector 投影*

                        Gaia 虚拟表场景（待决策）
                        ──────────────────────
VIRTUAL 对象 ──→ Trino 联邦查询外部源（不落地）
                        │
            ┌───────────┼───────────┐
            ▼           ▼           ▼
         路径①        路径②       路径③'
      身份骨架复制   全量属性复制   查询时联邦（自研）
      (模式C轻量)   (模式C完整)   (Virtual Graph范式自实现)
```

| 路径 | 对应业界模式 | 机制 | Neo4j 版本要求 | 一致性 | 延迟 | 改动量 | Gaia 可行性 |
|------|------------|------|---------------|--------|------|--------|------------|
| **① 身份骨架复制** | 模式 C（子集复制，只复制 rid+label） | Trino 查 PK 列表 → `project_object`（空属性） | 社区版即可（纯 MERGE） | 最终一致（需定时刷新） | 图查询毫秒 | 小 | ✅ 可用 |
| **② indexed 属性复制** | 模式 C（子集复制，复制 indexed 属性） | Trino 查 PK+indexed 列 → `project_object` | 社区版即可（纯 MERGE） | 最终一致（定时刷新） | 图查询毫秒 | 中 | ✅ 可用 |
| ~~③ Neo4j Virtual Graph~~ | Virtual Graph（零拷贝） | Cypher 编译成 SQL 下推 | ❌ 仅 Aura 云 | — | — | — | ❌ 不可用（见 §3.3） |
| **③' 自研查询时联邦** | Virtual Graph 范式自实现 | `Neo4jGraphStore` 查到 VIRTUAL 节点 → 回查 Trino 水合 | 社区版即可 | 实时（查时下推） | 秒级 | 大（需改查询引擎） | ✅ 可行但工作量大 |

### 5.2 推荐路线：路径 ① → ② → ③'

**阶段 1：路径 ①（MVP，必做）**

VIRTUAL 节点只存 `rid + label + _virtual:true + _source_ref`，让 `search_around` / `find_paths` 跨虚拟表节点不断链。社区版 Neo4j 完全支持，改动量小。

**理由**：
- 图关联推理最痛的断点是"跨 VIRTUAL 节点跳转断链"，路径 ① 直接解决这个痛点，投入产出比最高
- Neo4j 官方对 Virtual Graph 的定位是"秒~分钟级延迟容忍"场景——而 Gaia 的图探索画布是 AG-UI Agent 驱动（ReAct 多步），每步秒级延迟完全可接受。这意味着查询时联邦（路径 ③'）在 Gaia 场景下**不是 MVP 刚需**，路径 ①/② 的"图查询毫秒 + 数据滞后"反而更匹配交互模型
- Layers appview 的实证印证了"只存身份骨架不拆属性"在业界是合理设计（见 §4.1）

**阶段 2：路径 ②（按需）**

补 indexed 属性，让属性过滤可用。与路径 ① 共用同一个 `project_for_virtual_object_type` 入口，只是 `SELECT` 的列不同。是 ① 的增量升级，不是独立路径。

**阶段 3：路径 ③'（远期，自研）**

如果未来 VIRTUAL 对象需要实时属性，不依赖 Neo4j Virtual Graph（不可用），而是在 `DataFrameQueryService` 层自研——检测到 VIRTUAL 节点时，用 `_source_ref` 经 `TrinoQueryEngine` 回查水合最新属性。这本质是把 Virtual Graph 的"查时下推"逻辑搬到 Gaia 的 service 层自己实现。

**路径 ① 的实现要为 ③' 留接口**：VIRTUAL 节点在 Neo4j 里带 `_virtual: true` 标记 + `_source_ref`（Trino table ref），未来查询引擎检测到这个标记时可以决定是"用缓存的 indexed 属性"还是"回查 Trino"。

### 5.3 否决的方案

**方案 3（把 VIRTUAL 改成可落地，走 MANAGED 路径）**：即让虚拟表也同步一份到 Iceberg，然后走现有 ProjectSyncService。这等于把 VIRTUAL 降级成 MANAGED，违背红线 9（VIRTUAL 仅可读）和 C3（不落地）。**否决**。

---

## 第六部分：落地设计锚点

> ⚠️ 本节为设计锚点，非最终方案。落地需先更新设计文档（见第七部分），再实现。

### 6.1 数据流（对齐模式 C + Neo4j Doc Manager 的 "tail log → Cypher" 范式）

```
register_virtual_table 成功
  → ProjectSyncService.project_for_virtual_object_type(ont, ot)   [新增方法，旁路 Gate 1]
    → TrinoQueryEngine.query("SELECT <pk> [, <indexed_cols>] FROM <virtual_table_ref>")
    → 逐行调 GraphProjector.project_object(ont, ot,
          {id, properties:{...}, _virtual:true, _source_ref:<trino_table_ref>})
    → Neo4j MERGE 节点（带 _virtual 标记）
```

### 6.2 数据源

- 复用 `ObjectQueryService._virtual_table_ref(ot)` 拿 Trino table ref
- 经 `TrinoQueryEngine.query` 拉数据
- **不要**给 ProjectSyncService 新引入直连外部源的依赖（保持"Trino 是唯一联邦查询入口"的架构约束）

### 6.3 Gate 1 处理策略：旁路而非改掉

两个选择：
- **改 Gate 1**：把 VIRTUAL 也放行，但数据源从 Iceberg 换成 Trino——会让 ProjectSyncService 同时依赖 IcebergStore + TrinoQueryEngine，职责膨胀
- **旁路 Gate 1**（推荐）：新增 `ProjectSyncService.project_for_virtual_object_type` 方法，单独走 Trino 数据源，与现有 MANAGED 路径解耦

**推荐旁路**：符合"不做侵入式扩展"原则，且 VIRTUAL 投影的触发时机、一致性模型都和 MANAGED 不同，混在一起会污染现有清晰的设计。

### 6.4 一致性模型（对齐 Neo4j 官方对 Virtual Graph 的定位）

- **不是"派生副本"，而是"联邦图索引的缓存层"**——这点要更新 `graph-reasoning-design.md §6.3`，VIRTUAL 节点的 Neo4j 定位从"同源派生"改为"联邦缓存"，freshness 由刷新策略决定
- **刷新策略**：手动 `POST /admin/project/rebuild-for-virtual/{ont}/{ot}` + 可选定时任务（对齐 Neo4j 社区 "lastUpdate property + 定时 MERGE" 的做法）
- **不接 CDC**：VIRTUAL 不落地，没有 SeaTunnel backfill，不要硬接 CDC 通道。外部源 CDC 是另一个独立工程（见 ADR-014 的外部 CDC→Iceberg，那是把 VIRTUAL 升级成 MANAGED 的路径，不属于此场景）

### 6.5 查询侧演进（为路径 ③' 留口）

`Neo4jGraphStore` 的 VIRTUAL 节点查询时，返回结果带 `_virtual` 标记，由上层 `DataFrameQueryService` 决定：
- **MVP（路径 ②）**：直接用缓存的 indexed 属性
- **远期（路径 ③'）**：检测到 `_virtual:true` 时，用 `_source_ref` 回查 Trino 水合最新属性（对齐 Virtual Graph 的"查时下推"）

### 6.6 代价与权衡

**路径 ① 的代价**：
- ❌ indexed 属性剪枝失效（图上 VIRTUAL 节点没有可过滤的属性，只能按 label/rid 查）
- ❌ 外部源数据变更不会同步到 Neo4j（没有 CDC 通道，除非额外给 VIRTUAL 配 SeaTunnel CDC→Iceberg，但那就变成 MANAGED 了）
- ✅ 改动小，只新增一个 `project_for_virtual_object_type`，复用现有 projector
- ✅ 边关系完整保留，`search_around` / `find_paths` 能跨 VIRTUAL 节点跳转

**路径 ② 的代价**：
- ❌ 外部源数据新鲜度滞后（分钟级，和 Palantir Virtual Table 的"实时联邦"承诺有差距，但 Neo4j 本就是最终一致派生副本 C8，可接受）
- ❌ 大表全量扫描有压力（需分批 + 增量 watermark，复杂度上升）
- ✅ indexed 属性剪枝可用，图推理质量接近 MANAGED
- ⚠️ 需在 `GraphProjector` 加一个 VIRTUAL 专用入口或给 ProjectSyncService 加 Trino 数据源依赖（当前它只依赖 IcebergStore）

---

## 第七部分：待决策点

在动手前，以下问题比"怎么填"更重要，需要先决策：

### 决策点 1：Neo4j 的定位是否要从"派生投影"扩展为"联邦图索引"？

当前 `graph-reasoning-design.md §6.3` 明确：Neo4j 是 `object_state/Iceberg` 的**同源分发派生副本**，可全量重建、禁止反写。如果允许填 VIRTUAL 数据，Neo4j 就变成了**跨数据源的联邦图索引**——它的"源"不再单一（一部分来自 Iceberg，一部分来自外部 MySQL/PG via Trino）。

这会冲击：
- **rebuild 语义**：重建 VIRTUAL 节点要重新查外部源，外部源不可用时图会缺数据（需要 partial/omitted 标记，类似 ADR-020 的 best-effort）
- **一致性对账**：C8 的"投影表保留 update_time + data_version 对账"对 VIRTUAL 节点失效（外部源没有 data_version）

**建议**：如果走路径 ①/②，需要更新 `graph-reasoning-design.md §6.1/§6.3`，新增"触发模式 C：VIRTUAL 联邦投影"，并明确其一致性语义为 **best-effort + 不可对账**。

### 决策点 2：先更新设计文档还是先写代码？

按 CLAUDE.md 红线"设计意图变更先记 ADR/设计文档"，应该：
1. 先起草 `graph-reasoning-design.md §6` 修订（新增触发模式 C + 一致性说明）
2. 新增 ADR（或并入 ADR-015 补充）记录"VIRTUAL 对象图投影"决策，引用本文档作为调研依据
3. 再实现路径 ①

### 决策点 3：是否真的需要填 Neo4j？

在做路径 ① 之前，应先确认图关联推理的真实业务场景中，VIRTUAL 对象是否频繁出现在 `search_around` / `find_paths` 的路径上。如果绝大多数图推理只涉及 MANAGED 对象，VIRTUAL 只是偶尔被查询（走 Trino 联邦即可），那么填 Neo4j 的投入产出比可能不高——这个判断需要结合实际本体设计（哪些对象是 VIRTUAL、它们和 MANAGED 对象的关系密度）。

---

## 附录：调研来源索引

### Neo4j 官方（第一手）
| 来源 | URL |
|------|-----|
| Introducing Neo4j Virtual Graph（首发） | https://neo4j.com/blog/graph-database/introducing-neo4j-virtual-graph-graph-reasoning-on-the-data-you-already-have/ |
| Virtual Graph public preview | https://neo4j.com/blog/auradb/neo4j-virtual-graph-is-now-in-public-preview/ |
| Virtual Graph 产品页 | https://neo4j.com/product/virtual-graph/ |
| Zero-copy on Snowflake 实战 | https://neo4j.com/blog/developer/zero-copy-graph-reasoning-on-snowflake-getting-started-with-neo4j-virtual-graph/ |
| Neo4j 定价页 | https://neo4j.com/pricing/ |
| NoSQL polyglot persistence（三种模式） | https://neo4j.com/blog/cypher-and-gql/nosql-polyglot-persistence-tools-integrations/ |
| Neo4j CDC 文档 | https://neo4j.com/docs/cdc/current/ |
| Neo4j Connector for Kafka | https://neo4j.com/docs/kafka/5.0/architecture/sinkconsume/ |
| Composite databases（联邦/分片） | https://neo4j.com/docs/operations-manual/current/tutorial/tutorial-composite-database/ |
| Import from relational database | https://neo4j.com/docs/getting-started/data-import/relational-to-graph-import/ |
| Community Edition | https://neo4j.com/product/community-edition/ |
| AuraDB FAQ | https://neo4j.com/cloud/platform/aura-graph-database/faq/ |

### 第三方实证
| 来源 | URL |
|------|-----|
| Layers appview Database Design | https://docs.layers.pub/appview/database-design |
| Ontop Virtual Knowledge Graph | https://ontop-vkg.org/guide/ |
| Ontop + Apache Iceberg | https://ontopic.ai/en/tech-notes/create-virtual-knowledge-graphs-on-apache-iceberg/ |
| Memgraph MemGQL 联邦 GQL | https://memgraph.com/docs/memgraph-zero/memgql/use-cases/federated-gql |
| Starburst + PuppyGraph（Iceberg 图分析） | https://www.starburst.io/blog/starburst-and-puppygraph/ |
| Neo4j Virtual Graph Playground（社区 sandbox） | https://github.com/ikwattro/neo4j-virtual-graph-playground |

### Gaia 内部代码（现状基准）
| 文件 | 关键位置 |
|------|---------|
| `src/ontology/services/project_sync_service.py` | L108-111 Gate 1（VIRTUAL skip） |
| `src/ontology/services/outbox_executor.py` | L448 `_project_object_upsert`（投影入口） |
| `src/ontology/services/graph_projector.py` | `project_object` / `project_link`（仅投影 indexed 属性） |
| `src/ontology/services/object_query_service.py` | `_resolve_query_target`（VIRTUAL→Trino 路由） |
| `docker-compose.yml` / `deploy/k8s/infra/optional/neo4j.yaml` | `neo4j:5-community`（社区版自托管） |
