# 三场景模拟分析 —— 纯 Ontop / 纯 Palantir 方案 / Gaia 折中（PK+描述+索引列入 Neo4j，其余 Trino 联邦）

> **用途**：对三种图推理架构方案做**端到端场景模拟**（设计层面推演，不动代码），逐一验证每个方案在 Gaia 真实查询路径上能走到哪、卡在哪、限制是什么。参考 Palantir Foundry 范式与 Ontop VKG 实现。
> **对照基准**：Gaia 当前实现（`DataFrameQueryService` / `GraphProjector` / `ObjectQueryService` / `AuthorizationService`）
> **关联文档**：[`ontop-source-analysis.md`](./ontop-source-analysis.md) · [`palantir-neo4j-mapping-proposal-comparison.md`](./palantir-neo4j-mapping-proposal-comparison.md) · [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) · [`graph-reasoning-design.md`](../architecture/graph-reasoning-design.md) §6-7
> **分析日期**：2026-07-14
> **身份模型决策（2026-07-15 修订）**：经调研 Palantir Foundry 官方文档（[object-identifiers](https://palantir.com/docs/foundry/functions/object-identifiers/) + [resource-identifier 开源 spec](https://github.com/palantir/resource-identifier)）确认 Gaia 应采用 Palantir 的 Resource Identifier 体系，取代原裸 UUID 主键。要点：
> - **RID 格式**：`ri.<service>.<instance>.<type>.<locator>`，四段点分隔。Gaia 对象实例的 RID 为 `ri.ontology.main.object.{uuid}`。
> - **locator 是 UUID，不是 primary key**：这是 Palantir 的核心设计——RID 是系统身份（创建时分配，稳定不变，即使 primary key 改了也不变），与 primary key（业务身份，用户提供）正交分离。用 primary key 当 locator 会破坏这个分离保证。实际例子：`ri.phonograph2-objects.main.object.48971f8a-fdff-4157-9cf4-aa3e98163be4`。
> - **type 段固定 `object`**：不嵌具体 ObjectType（ObjectType 是独立资源，有自己的 RID `ri.ontology.main.object-type.{api_name}`）。从 RID 解析不出 ObjectType，要查元数据——这与 Palantir 一致。
> - **instance = `main`**：单实例部署，未来多租户再区分。
> - **命名统一为 `rid`**：RID 是通用概念，所有资源（Ontology / Object / ObjectType / LinkType）的标识字段都叫 `rid`，靠 type 段区分（如 `ontology.rid` = `ri.ontology.main.ontology.{uuid}`，`object.rid` = `ri.ontology.main.object.{uuid}`）。这是 Palantir 的做法，同名不冲突。废弃原 `vid`（Vertex ID 缩写，无全称且与 VIRTUAL 的 V 混淆）和 `object_id`（中间过渡命名）。
> - **应用层判等用 `(typeId, primaryKey)`，不用 RID**：对齐 Palantir（官方推荐判等方式，因为新创建未持久化对象 RID 为 undefined）。
> - **Iceberg 不用 RID**：Iceberg 表的 PK 是业务主键列（`backing_column`，如 `flight_id`），与 Palantir backing dataset 一致。RID 是 ontology 层身份，不在存储层。
> - **存量索引可清空重建**：PG `object_state` / `object_links` 的 `object_id` 字段、Doris idx 表、Neo4j 均无生产数据，可全量迁移或清空重建。
> - **本决策为架构级变更**：影响 PG schema（`object_id` 字段从 String(64) UUID hex 扩为 RID 串）、object_id 生成点（`action_service.py` 改为 RID 生成器）、Neo4j 节点属性、Doris idx 表主键列。本文档已全文应用 `rid` 命名（从 `vid` / `object_id` 统一改）。

---

## 〇、模拟基准：Gaia 当前查询路径全链路

在做方案对比前，先固定"待优化的现状"，三个方案都要在这个链路上被检验。

### 0.1 一次 `searchAround` 查询的完整执行链

以 **"找出客户 C001 的所有订单，再找这些订单关联的供应商"** 为例（跨 2 跳图遍历）：

```
Agent 发起 ObjectSet IR:
  searchAround(
    objectSet: static([Customer:C001]),
    link: "placedOrder",        ← Customer → Order
    hops: (1,1),
    then: searchAround(_, link: "suppliedBy", hops: (1,1))  ← Order → Supplier
  )

DataFrameQueryService.execute(ir):
  ① _eval_object_set(static) → _eval_static → 查 PG object_state by pk → [vid_C001]
  ② _eval_search_around(placedOrder):
     - _resolve_target_label(link="placedOrder") → 查 LinkType 元数据 → graph_label("Order")
     - Neo4jGraphStore.search_around(label=Order, source_rids=[rid_C001], hops=(1,1))
     - 返回 [vid_Order1, vid_Order2, ...]
  ③ _eval_search_around(suppliedBy):
     - Neo4jGraphStore.search_around(label=Supplier, source_rids=[rid_Order1,...], hops=(1,1))
     - 返回 [vid_Supp_A, vid_Supp_B, ...]
  ④ _hydrate([vid_C001, vid_Order1, ..., vid_Supp_A, ...]):
     - 当前 MVP：get_object_states_by_rids(rids) → 查 PG object_state 批量取
       （架构上应走 Doris 主源 MANAGED / Trino 联邦 VIRTUAL，见 D2 注）
     - 返回 [{rid, api_name, props}, ...]
```

### 0.2 当前链路的三个断裂点（三个方案要解决的痛点）

| 断裂点 | 位置 | 现象 | 根因 |
|--------|------|------|------|
| **D1. VIRTUAL 节点图遍历断链** | 步骤②③ | `search_around` 返回空 | VIRTUAL 对象无 object_state → GraphProjector Gate 1 skip → Neo4j 里没有 VIRTUAL 节点 → 图遍历到 VIRTUAL ObjectType 时返回空 |
| **D2. VIRTUAL 节点水合断链** | 步骤④ | `vid_Supp_X` 在 `state_map` 里 None → skip | 当前 `_hydrate` MVP 走 PG `get_object_states_by_rids`，VIRTUAL 无 object_state 记录。**即使水合对齐架构走 Doris 主源**，VIRTUAL 数据也不在 Doris（不落地），仍会断——需分流到 Trino 联邦查外部源 |
| **D3. 图遍历无属性剪枝** | 步骤②③ | `search_around` 只能按拓扑遍历，不能按属性过滤 | `NodeFilter` 只支持 indexed 属性，且 VIRTUAL 节点无属性在 Neo4j |

> **注**：D1 是"VIRTUAL 节点根本不在图里"，D2 是"即便在图里，水合也拿不到属性"。两个断裂点性质不同，方案对不同断裂点的解决力度是评判标准。

### 0.3 评判维度（统一打分框架）

每个方案按以下 5 个维度评估，每项 ✅ 可行 / 🟡 受限 / ❌ 不可行：

| 维度 | 含义 |
|------|------|
| **拓扑连通性** | 跨 VIRTUAL 节点的多跳遍历能否走通（解决 D1） |
| **属性水合** | 图遍历返回的 VIRTUAL rid 能否取到全量属性（解决 D2） |
| **属性剪枝** | 图遍历能否按 VIRTUAL 对象的属性过滤（解决 D3） |
| **一致性** | 外部源变更后，图推理结果多久能反映（延迟 + 对账） |
| **工程成本** | 改动量、引入依赖、运维复杂度 |

---

## 一、场景 1：纯 Ontop 方案

### 1.1 方案描述

部署 Ontop 作为独立 sidecar 服务（Spring Boot，Docker），指向 Trino，暴露 SPARQL endpoint。Gaia 的图推理引擎在遇到图遍历时，不查 Neo4j，而是把图遍历请求翻译成 SPARQL，调 Ontop endpoint，Ontop 内部按 mapping 把 SPARQL 翻译成 SQL 下推到 Trino → 外部源。

```
Agent → DataFrameQueryService → [SPARQL 翻译器] → Ontop sidecar
                                                    ↓ mapping 展开
                                                  Trino SQL
                                                    ↓
                                                  Trino → 外部源（MySQL/PG/...）
```

### 1.2 场景模拟：跨 VIRTUAL 节点的 searchAround

以 §0.1 的查询为例，假设 **Order 是 VIRTUAL**（来自外部 MySQL），Customer 和 Supplier 是 MANAGED：

```
searchAround(static([Customer:C001]), link="placedOrder", hops=(1,1))
  then searchAround(_, link="suppliedBy", hops=(1,1))
```

**纯 Ontop 方案的执行**：

① 把 ObjectSet IR 翻译成 SPARQL：
```sparql
SELECT ?order ?supplier WHERE {
  <gaia/Customer/C001> <gaia/placedOrder> ?order .
  ?order <gaia/suppliedBy> ?supplier .
}
```
（这里已经需要 Gaia 自研 IR→SPARQL 翻译器）

② Ontop sidecar 收到 SPARQL，按 mapping 展开：
- mapping 声明：`<gaia/placedOrder> ?o` ← `SELECT customer_id, order_id FROM iceberg.customer_order_links`（MANAGED，Iceberg 表）
- mapping 声明：`?order <gaia/suppliedBy> ?s` ← `SELECT order_id, supplier_id FROM mysql_orders.orders_suppliers`（VIRTUAL，外部 MySQL 经 Trino 联邦）

③ Ontop 生成跨 catalog SQL：
```sql
SELECT o.order_id, s.supplier_id
FROM iceberg.customer_order_links l
JOIN mysql_orders.orders o ON l.order_id = o.order_id   -- 跨 catalog JOIN
JOIN mysql_orders.orders_suppliers s ON o.order_id = s.order_id
WHERE l.customer_id = 'C001'
```

④ Trino 执行跨 catalog JOIN，返回结果。

### 1.3 能走到哪 / 卡在哪

| 维度 | 评估 | 详细 |
|------|------|------|
| **拓扑连通性** | ✅ | Ontop 的 mapping 声明"图关系 ↔ SQL JOIN"，跨 VIRTUAL 节点的多跳遍历天然走通（Trino 跨 catalog JOIN）。这是 Ontop 范式的强项 |
| **属性水合** | ✅ | 结果直接从外部源 SQL 取，不需要二次水合（零拷贝，数据永远最新） |
| **属性剪枝** | ✅ | SPARQL FILTER 翻译成 SQL WHERE，下推到外部源。VIRTUAL 对象的属性过滤原生支持 |
| **一致性** | ✅ 最强 | 零拷贝 = 查时下推 = 永远最新，无延迟、无对账问题 |
| **工程成本** | ❌ 最高 | 见下 |

### 1.4 致命限制

#### 限制 1：范式翻译器工程量巨大（❌ 决定性障碍）

Gaia 的查询入口是 **ObjectSet IR**（对齐 Palantir，13/15 type），不是 SPARQL。纯 Ontop 方案要求：

1. **IR → SPARQL 翻译器**：把 ObjectSet IR 的 `searchAround`/`filter`/`union`/`intersect` 翻译成 SPARQL BGP + FILTER + UNION。这是 Gaia 当前没有的组件，且 ObjectSet IR 有 13 种 type（searchAround/filter/union/intersect/subtract/aggregate/select/static/objectType/interfaceBase/withProperties/reference/interfaceLinkSearchAround），每种都要翻译
2. **SPARQL 结果 → ReasoningResult 反翻译**：Ontop 返回 RDF 三元组（或 JSON-LD），要转回 Gaia 的 `{rid, api_name, props}` 格式
3. **rid 语义对齐**：Gaia 的 rid = object_state.rid（UUID hex），Ontop 的节点 IRI 是 `{base}/{table}/{pk}={value}`。两套主键体系要映射——MANAGED 对象的 IRI 要映射到 Gaia rid，VIRTUAL 对象的 IRI 要映射到外部源 PK

**对照 Ontop 源码**（见 [`ontop-source-analysis.md`](./ontop-source-analysis.md) §3.1）：Ontop 的 `BasicQueryUnfolder` 是把 SPARQL 三元组模式按 mapping 展开，这个展开器 Ontop 已经写好了。**但 Gaia 缺的是反向的 IR→SPARQL**，这是 Ontop 不提供的新工程。

#### 限制 2：MANAGED + VIRTUAL 混合查询的双引擎问题（❌）

如果 Order 是 MANAGED（在 Neo4j 有投影），Supplier 是 VIRTUAL（走 Ontop），一次 searchAround 跨两者：
- Neo4j 里有 `Customer → Order` 的边（MANAGED 投影）
- Ontop 里有 `Order → Supplier` 的 mapping（VIRTUAL 联邦）

**纯 Ontop 方案要么**：(a) MANAGED 也走 Ontop（放弃 Neo4j 投影，全部虚拟化）→ 违背 ADR-001（Doris 在线读主源）+ 丧失 Neo4j 的图遍历性能优势；(b) MANAGED 走 Neo4j、VIRTUAL 走 Ontop → 一次查询要**双引擎联合**，中间结果在 Gaia 层 join，复杂度爆炸（且 Neo4j Cypher 和 SPARQL 语义不完全对齐）

#### 限制 3：Trino 无法提取约束（🟡，Ontop 已知限制）

见 [`ontop-source-analysis.md`](./ontop-source-analysis.md) §4.2：`TrinoDBMetadataProvider.insertIntegrityConstraints()` 为空。纯 Ontop 方案下，VIRTUAL 对象的 PK/FK 要么：
- 用 Ontop Lens 手工声明（额外维护一份约束元数据，和 Gaia 本体元数据重复）
- 不声明（查询优化失效，可能产生笛卡尔积）

Gaia 的本体元数据（ObjectType PK + LinkType）天然是约束声明，但要喂给 Ontop 的 Lens JSON 格式，需要额外的元数据同步层。

#### 限制 4：权限治理断层（❌）

`DataFrameQueryService` 当前不注入 `AuthorizationService`（见代码确认）。纯 Ontop 方案下，SPARQL 查询直接下推 Trino，**行级权限（Cedar TPE → SqlGlot 注入）无法作用**——因为 SQL 是 Ontop 生成的，不是 Gaia 的 `OntologySqlCompiler` 生成的。要在 Ontop 生成的 SQL 里注入 Gaia 的权限 residual predicate，需要 hook Ontop 的 SQL 生成层，极其侵入。

#### 限制 5：Java sidecar 进 Python 栈（🟡 运维）

Ontop 是 Spring Boot/Java，Gaia 是 Python/FastAPI。引入 Java sidecar 增加：镜像体积、JVM 内存、两套监控、跨语言调试。私有化部署场景（k3s / 国产化）还要保证 JRE 可用。

### 1.5 结论

**纯 Ontop 方案不可行**。虽然拓扑连通性/水合/剪枝/一致性都是 ✅（理论最强），但工程成本 ❌（IR→SPARQL 翻译器 + 双引擎联合 + 权限注入断层 + Java sidecar）远超收益。**Ontop 的价值在于其设计思想参考**（mapping 声明式映射 + IQ 中间表示 + 查询时联邦范式），不在于直接集成。这与 [`ontop-source-analysis.md`](./ontop-source-analysis.md) §8.1 的结论一致。

**Ontop 能走到哪**：单跳 VIRTUAL 查询（如"查 Order 的属性"）能走通；但多跳跨 MANAGED+VIRTUAL 的图推理走不通（双引擎问题 + 权限断层）。

---

## 二、场景 2：纯 Palantir 方案（Neo4j 作主存）

### 2.1 方案描述

按用户提供的《Palantir 动态本体映射 Neo4j 企业级方案》：Neo4j 持全量属性 + 系统元属性 + 安全标记 + SCD2 时序。图遍历、属性过滤、水合全部在 Neo4j 内完成。外部数据通过 ETL（`neo4j-admin import` 离线 + APOC 增量）导入 Neo4j。

```
外部源 → SeaTunnel → Iceberg → [全量 ETL 导入] → Neo4j（主存，全量属性+标记+时序）
                                              ↓
Agent → DataFrameQueryService → Neo4j Cypher（图遍历+属性过滤+水合一站式）
```

### 2.2 场景模拟：跨 VIRTUAL 节点的 searchAround

同样以 §0.1 查询为例，Order 是 VIRTUAL（外部 MySQL）：

**纯 Palantir 方案的执行**：

① VIRTUAL 数据先 ETL 进 Neo4j：
- `neo4j-admin import` 把 MySQL orders 表全量导入，每个 Order 变成 `(:SupplyChainOrder {orderId, orderNo, amount, status, _source:"mysql", _valid_start:..., _is_deleted:false, _mark_conjunct:[...]})` 节点
- 关系 `[:ORDER_BELONG_CUSTOMER]` 也导入

② Agent 查询时，DataFrameQueryService 直接发 Cypher：
```cypher
MATCH (c:Customer {rid:"C001"})-[:PLACED_ORDER]->(o:Order)-[:SUPPLIED_BY]->(s:Supplier)
WHERE o.status = "PAID" AND o._mark_class_level <= $user_max_class
RETURN c, o, s
```
（图遍历 + 属性过滤 + 权限过滤 + 水合一站式）

### 2.3 能走到哪 / 卡在哪

| 维度 | 评估 | 详细 |
|------|------|------|
| **拓扑连通性** | ✅ | VIRTUAL 数据已 ETL 进 Neo4j，图遍历天然走通 |
| **属性水合** | ✅ | 全量属性在 Neo4j 节点，无需二次水合 |
| **属性剪枝** | ✅ | 全量属性在 Neo4j，`WHERE o.status="PAID"` 直接过滤 |
| **一致性** | ❌ 最差 | 见下 |
| **工程成本** | ❌ 高（且违背 Gaia 架构） | 见下 |

### 2.4 致命限制

#### 限制 1：违背 Gaia 架构红线（❌ 决定性）

| 红线 | 冲突 |
|------|------|
| **ADR-001** | Doris 是在线读主源，存全量属性。方案让 Neo4j 也存全量属性 → 双主源，一致性维护爆炸 |
| **C1（图节点轻量）** | 方案节点存 6 个系统元属性 + 4 个安全标记字段 + 全量业务属性 → 图节点膨胀，违背"仅 indexed 属性做剪枝" |
| **C8（派生副本）** | 方案让 Neo4j 成主存，不再是派生副本 → 失去"可全量重建"能力（重建要从 Neo4j 反向导出？） |
| **C3（VIRTUAL 不落地）** | 方案把 VIRTUAL 数据 ETL 进 Neo4j = 落地存储 → 直接违反 C3 |
| **红线 9（VIRTUAL 禁止写入）** | ETL 导入是写入操作 → 违反 |

**核心矛盾**：方案的设计前提是"Neo4j 是 Foundry 数据集的目标存储"，而 Gaia 的设计前提是"Neo4j 是 PG+Iceberg 的派生索引"。两者不可调和。

#### 限制 2：一致性灾难（❌）

方案把 VIRTUAL 数据（外部源）ETL 进 Neo4j，但外部源会变：
- **没有 CDC 通道**：方案的"增量流式同步"是 APOC `periodic.iterate`，但数据来源是"Palantir 数据集"（已落地的快照），不是外部源的实时 CDC
- **延迟不可控**：从外部源变更 → Gaia SeaTunnel → Iceberg → Neo4j ETL，链路长，分钟~小时级延迟
- **对账失效**：C8 的 `update_time + data_version` 对账对外部源无效（外部源无 data_version）

对比当前 Gaia 的 VIRTUAL 查询（Trino 联邦，零拷贝，永远最新），方案是**一致性倒退**。

#### 限制 3：安全标记存 Neo4j 的反模式（❌）

见 [`palantir-neo4j-mapping-proposal-comparison.md`](./palantir-neo4j-mapping-proposal-comparison.md) §2.2。Gaia 的 Marking 在 PG（`MarkingService` + `resource_markings`），方案要求存 Neo4j 节点（`_mark_conjunct` 数组）。这意味着：
- 标记变更（assign/revoke）要同步投影到 Neo4j → 增加 outbox 负担
- 社区版无 RLS，方案 §2.5 的"企业版 RLS"不可用 → 退化到"应用层注入 ACL"，但这时存 Neo4j 纯属冗余（PG 校验已足够）

#### 限制 4：SCD2 与 Iceberg time travel 重复（❌）

方案用 `_is_deleted` + `_valid_start` + `_valid_end` 在 Neo4j 存历史版本。Gaia 已有 Iceberg time travel（`TimeTravelService`，snapshot 级）。两套时序机制并存 = 数据冗余 + 一致性维护。且 SCD2 是行级版本（查询要过滤），Iceberg snapshot 是表级快照（查询用 `FOR VERSION AS OF`），后者更优。

#### 限制 5：ETL 链路重建（❌）

方案的批量同步（`neo4j-admin import` + APOC）要重建 Gaia 的整个数据接入链路：
- 当前：SeaTunnel → Iceberg（主流水线）+ fan-out 投影（OutboxExecutor → GraphProjector）
- 方案：SeaTunnel → Iceberg → [新增 ETL] → Neo4j（全量属性）

新增 ETL 层 = 新增故障点 + 新增延迟 + 新增运维。而当前 fan-out 投影是异步、fail-tolerant 的，不需要额外 ETL。

### 2.5 结论

**纯 Palantir 方案不可行**。拓扑/水合/剪枝都 ✅（因为数据全在 Neo4j），但**以违背 Gaia 5 条架构红线为代价**（ADR-001/C1/C8/C3/红线9），且一致性灾难（无 CDC + 对账失效）。**这个方案的本质是"把 Gaia 改造成 Foundry-on-Neo4j"**，不是给 Gaia 加图推理能力。

**纯 Palantir 方案能走到哪**：如果 Gaia 愿意放弃 Doris/Iceberg/Trino 多引擎联邦架构，退回到"Neo4j 单存储"，方案能走通。但这等于推翻 ADR-001~005，不在可接受范围内。

**唯一价值**：方案的若干**局部技术**可借鉴（见 [`palantir-neo4j-mapping-proposal-comparison.md`](./palantir-neo4j-mapping-proposal-comparison.md) §10.2）：LinkType 基数约束（应用层预查询）、Marking lattice 支配模型（PG 层）、rebuild 批量提交优化。这些是**细节借鉴**，不是架构采纳。

---

## 三、场景 3：Gaia 折中方案（PK + 描述 + 索引列入 Neo4j，其余 Trino 联邦）

### 3.1 方案描述

这是用户提出的中间路线，也是三个方案里**最贴近 Gaia 架构**的。核心思想：

- **Neo4j 存什么**：VIRTUAL 对象的**身份骨架** = `rid` + `label` + **主键（PK）** + **描述字段（title property）** + **indexed 属性** + `_virtual:true` + `_source_ref`（Trino table ref）
- **全量属性在哪**：仍在 Trino 联邦查询外部源（VIRTUAL 不落地）
- **图遍历**：走 Neo4j（拓扑 + 剪枝字段在 Neo4j）
- **水合**：图遍历返回 rid 后，按 storage_type 分流——MANAGED → Trino（读 Iceberg 表，生产走 Doris 主源点查；Doris 不可用降级 Trino）；VIRTUAL → Trino（跨 catalog 联邦查外部源表）→ 外部数据源。**两条路都经 Trino，但分叉到不同 catalog**

```
外部数据源 ──────────────── Trino（外部源 catalog，如 mysql_xxx）─┐
                                                                   │
Iceberg 表（MANAGED 全量）── Trino（iceberg catalog）─────────────┤
                                          ↑ Doris 主源点查（Doris 不可用降级 Trino）
                                                                   │
VIRTUAL 身份骨架 → Neo4j（rid + PK + title + indexed + _source_ref）─┤
              ↑ 图遍历                                              │
Agent → DataFrameQueryService → Neo4j（拓扑+剪枝）→ 分流水合（Trino→Doris / Trino→外部源）
```

> **注**：当前 `DataFrameQueryService._hydrate` 是 MVP 简化实现，走 PG `object_state` 批量取（代码注释 "MVP：object_state 批量取；Doris 水合留优化期"）。但 Gaia 的现有架构中 **MANAGED 数据的在线读主源就是 Doris**（ADR-001）——PG object_state 只是 Action 的同步写入目标 + read-your-writes 保障，数据会经 outbox INDEX effect 最终落入 Doris。因此 `_hydrate` 走 PG 是实现未对齐架构的 MVP 越暂，不是架构要变。折中方案的水合改造应直接对齐既有架构：MANAGED 走 Doris（读主源，Trino Iceberg 降级）；VIRTUAL 走 Trino 联邦（→ 外部源），与 `ObjectQueryService` 的标准查询路由一致。

### 3.2 场景模拟：跨 VIRTUAL 节点的 searchAround

以 §0.1 查询为例，Order 是 VIRTUAL（外部 MySQL），Customer/Supplier 是 MANAGED：

**折中方案的执行**：

① **投影阶段**（`register_virtual_table` 后触发）：
```python
# ProjectSyncService.project_for_virtual_object_type(ont, ot="Order")
# 1. 从 Trino 拉数据：SELECT order_id, order_no, status FROM mysql_orders.orders
trino_rows = await trino.query(f"SELECT {pk_col}, {title_col}, {indexed_cols} FROM {table_ref}")
# 2. 构造最小 object_state，复用 GraphProjector.project_object
for row in trino_rows:
    object_state = {
        "id": f"virtual:{ont}:{ot}:{row[pk_col]}",  # 合成 rid
        "properties": {pk: row[pk], title: row[title], indexed...},
        "object_type_api_name": "Order",
    }
    await graph_projector.project_object(ont, "Order", object_state)
    # Neo4j 节点：(:SupplyChainOrder {rid, orderId, orderNo, status, _virtual:true, _source_ref:"mysql_orders.orders"})
```

② **查询阶段**（Agent 发起 searchAround）：
```
DataFrameQueryService.execute(ir):
  ① _eval_static([Customer:C001]) → 查 PG object_state 拿 rid（rid 是 object_state.rid，起始集定位用）→ [rid_C001]
     *注：起始集定位走 PG 拿 rid 是合理的（rid 是 object_state 主键）；
      真正的全量属性水合在步骤④，走 Doris/Trino，不走 PG*
  ② _eval_search_around(placedOrder):
     - Neo4j: MATCH (c:Customer {rid:"rid_C001"})-[:PLACED_ORDER]->(o:Order) RETURN o.rid
     - Neo4j 里有 Order 节点（身份骨架）→ 返回 [vid_Order1, vid_Order2]
     - 可选剪枝：WHERE o.status = "PAID"（status 是 indexed，在 Neo4j）
  ③ _eval_search_around(suppliedBy):
     - Neo4j: MATCH (o)-[:SUPPLIED_BY]->(s:Supplier) RETURN s.rid
     - 返回 [vid_Supp_A, vid_Supp_B]
  ④ _hydrate([vid_C001, vid_Order1, ..., vid_Supp_A, ...]):
     - 分流：MANAGED rid → Trino（iceberg catalog，生产走 Doris 主源点查；Doris 不可用降级 Trino）
     - VIRTUAL rid → Trino（外部源 catalog，跨 catalog 联邦查外部源表）
       SELECT * FROM mysql_orders.orders WHERE order_id IN (vid_Order1 解析出 PK, ...)
     *两条路都经 Trino，分叉到不同 catalog（iceberg.* vs 外部源 catalog）*
```

### 3.3 能走到哪 / 卡在哪

| 维度 | 评估 | 详细 |
|------|------|------|
| **拓扑连通性** | ✅ | VIRTUAL 身份骨架在 Neo4j，图遍历走通（解决 D1） |
| **属性水合** | ✅ | 分流：MANAGED rid → Doris 主源点查（Trino Iceberg 降级）；VIRTUAL rid → Trino 跨 catalog 联邦查外部源（解决 D2，需新增分流，复用 ObjectQueryService 标准路由） |
| **属性剪枝** | 🟡 | 仅 indexed 属性可剪枝（在 Neo4j）；非 indexed 属性过滤要回 Trino（解决 D3 部分） |
| **一致性** | 🟡 | 见下，介于纯 Ontop（最强）和纯 Palantir（最差）之间 |
| **工程成本** | 🟡 中等 | 见下，可控 |

### 3.4 详细分析：每个维度的限制

#### 3.4.1 拓扑连通性（✅，但有 rid 合成问题）

**能走到哪**：VIRTUAL 对象的节点（rid + PK + title + indexed）在 Neo4j，跨 VIRTUAL 节点的多跳遍历走通。

**限制 L1：rid 合成策略**。VIRTUAL 对象没有 object_state，没有 Gaia 生成的 UUID rid。需要**合成 rid**：
- 方案 a：`virtual:{ont}:{ot}:{pk_value}`（字符串拼接，可解析回 PK）
- 方案 b：用 Ontop 的 IRI 模板规则（`{base}/{table}/{pk}={value}`，见 [`ontop-source-analysis.md`](./ontop-source-analysis.md) §3.2）

**选 a 还是 b**？Gaia 的 MANAGED rid 采用 Palantir RID 规范 `ri.ontology.main.object.{uuid}`（系统分配，稳定不变）。VIRTUAL 对象没有 object_state，没有系统分配的 RID，需要**合成伪 rid**。合成格式要与 MANAGED rid **可区分**（否则水合分流无法判断走 Doris/Iceberg 还是外部源 catalog）：
- **方案 a（推荐）**：`ri.ontology.main.virtual-object.{ont}.{ot}.{pk_value}`——复用 RID 规范外壳，但 type 段用 `virtual-object` 区分，locator 嵌入 ont/ot/pk 以便水合解析。符合 RID 规范、可逆解析、与 MANAGED rid 格式区分清晰。
- 方案 b：`virtual:{ont}:{ot}:{pk_value}`——非规范格式，简单但不符合 RID 体系。

水合时 `_hydrate` 检测 type 段分流：`object` → MANAGED 走 Doris 主源点查（降级 Trino iceberg catalog）；`virtual-object` → VIRTUAL 走 Trino 外部源 catalog。

> **区分两个 PG 用途**：PG object_state 在折中方案里仍有两个正当用途——(1) **起始集 rid 定位**（`_eval_static` 按业务主键查 object_state 拿 rid，因为 rid = object_state.rid）；(2) **read-your-writes**（Action 刚写入的 object_state，秒内查 Doris 可能未同步，走 PG 拿最新）。这两个用途与"全量属性水合走 Doris/Trino"不矛盾——前者是拿 rid（主键），后者是拿属性（全量数据）。当前 MVP 的 `_hydrate` 走 PG 取全量属性是实现未对齐既有架构（MANAGED 数据本来就在 Doris，ADR-001），折中方案应将水合迁回 Doris/Trino 标准路由。

**限制 L2：跨 VIRTUAL-MANAGED 的边**。图遍历 `Customer(MANAGED) → Order(VIRTUAL)`，这条边在 Neo4j 里怎么来？
- Customer 的 rid 是 `ri.ontology.main.object.{uuid}`，Order 的 rid 是 `ri.ontology.main.virtual-object.{ont}.{ot}.{pk}`
- 边 `[:PLACED_ORDER]` 要连接这两个 rid
- **边的来源**：如果是 Action 创建的关系，Action 写入 PG object_state 的 links 表 → OutboxExecutor 投影边（但 VIRTUAL 不能被 Action 写入，红线 9）
- **边的来源（替代）**：从外部源的 FK 关系推导。如 MySQL orders 表有 `customer_id` 外键 → 推导出 `Customer → Order` 边。这需要 **FK → LinkType 映射**（类似 Ontop Direct Mapping，见 [`ontop-source-analysis.md`](./ontop-source-analysis.md) §3.2）

**这是折中方案的最大设计挑战**：VIRTUAL 对象的边不能来自 Action（红线 9），必须来自外部源 FK 推导。需要在 `register_virtual_table` 时：
1. 扫描外部源的 FK（Trino 无法提取，见 Ontop §4.2 限制）
2. 或手工声明（Gaia 本体的 LinkType 已有，但要绑定到外部源 FK 列）

#### 3.4.2 属性水合（✅，但有 N+1 查询风险）

**能走到哪**：图遍历返回 VIRTUAL rid → 解析出 PK → Trino 批量查全量属性。

**限制 L3：rid → PK 解析**。水合时要从 `ri.ontology.main.virtual-object.{ont}.{ot}.{pk_value}` 解析出 `pk_value`，再用 PK 查 Trino。这要求 rid 编码可逆（方案 a/b 均可逆）。

**限制 L4：批量水合的 IN 查询**。当前 `_hydrate` 是 `get_object_states_by_rids(rids)`（PG 批量）。VIRTUAL 水合要改成：
```python
# 分流
managed_rids = [v for v in rids if parse_rid(v).type == "object"]
virtual_rids = [v for v in rids if parse_rid(v).type == "virtual-object"]
# MANAGED：Doris 主源点查（降级 Trino iceberg catalog）
#   复用 ObjectQueryService 的 load_by_ids / execute_compiled_sql 路径
managed_objs = await object_query_service.load_by_ids(managed_rids)  # Doris 主，Trino 降级
# VIRTUAL：按 OT 分组，每组一次 Trino 跨 catalog 查询（→ 外部源）
virtual_by_ot = group_by_ot(virtual_rids)  # {("ont","Order"): [pk1, pk2]}
for (ont, ot), pks in virtual_by_ot.items():
    table_ref = await object_query_service._virtual_table_ref(ot)  # 外部源 catalog.schema.table
    rows = await trino.query(f"SELECT * FROM {table_ref} WHERE {pk_col} IN (?, ...)", pks)
```
**N+1 风险**：如果图遍历返回的 VIRTUAL rid 跨多个 OT，每个 OT 一次 Trino 查询。对于"找 100 个 Order 的供应商"这种，Order 是一个 OT，1 次查询即可。但如果跨 5 个 VIRTUAL OT，5 次 Trino 查询。可接受（远好于逐条）。

**限制 L5：外部源不可用时降级**。VIRTUAL 水合依赖外部源可达。如果 MySQL 挂了，图遍历返回了 rid 但水合失败。需要：
- partial/omitted 标记（类似 ADR-020 best-effort）：返回 `[{rid, api_name, props:{}, _partial:true, _error:"source unavailable"}]`
- 不能让整个查询失败（C9 包容式防线）

#### 3.4.3 属性剪枝（🟡，indexed 内 Neo4j，其余回 Trino）

**能走到哪**：indexed 属性在 Neo4j，`NodeFilter` 可下推（现有 `_render_node_filter` 支持 eq/neq/gt/lt/in）。

**限制 L6：非 indexed 属性过滤**。如果 Agent 要 `WHERE o.amount > 1000`，但 `amount` 不是 indexed（不在 Neo4j），怎么办？
- 方案 a：先图遍历拿所有 rid，再 Trino 查全量属性，内存过滤 → 大结果集时低效
- 方案 b：把过滤下推到 Trino（`SELECT order_id FROM mysql_orders.orders WHERE amount > 1000`），拿 PK 集，再回 Neo4j 做图遍历 → 双向往返
- 方案 c：让用户把常用过滤字段标记为 indexed → 运营约定

**这是折中方案 vs 纯 Ontop 的关键差距**：纯 Ontop 任意属性都能下推（因为查时翻译 SQL），折中方案只有 indexed 能剪枝。**但这个差距是可接受的**——Gaia 的 indexed 机制本就是为剪枝设计的，用户可以把高频过滤字段标记为 indexed。

**限制 L7：VIRTUAL 节点的 indexed 属性新鲜度**。VIRTUAL 的 indexed 属性是从 Trino 拉来投影的，不是实时的。如果外部源的 `status` 从 PAID 变成 CANCELLED，Neo4j 里的 `status` 还是旧的（直到下次重新投影）。图遍历 `WHERE o.status="PAID"` 可能漏掉最新数据。

**缓解**：
- 定时刷新（方案 ②，分钟级延迟）
- 或图遍历不剪枝 VIRTUAL 属性，先拿全 rid，再 Trino 查最新属性过滤（牺牲剪枝换新鲜度）

#### 3.4.4 一致性（🟡，三档里居中）

| 一致性维度 | 纯 Ontop | 纯 Palantir | 折中方案 |
|-----------|---------|------------|---------|
| 拓扑（边） | ✅ 实时（查时 JOIN） | ❌ ETL 延迟 | 🟡 投影延迟（边来自 FK 推导，FK 变更要重新投影） |
| 剪枝属性 | ✅ 实时 | ❌ ETL 延迟 | 🟡 投影延迟（indexed 属性非实时） |
| 全量属性 | ✅ 实时 | ❌ ETL 延迟 | ✅ 实时（水合走 Trino 联邦） |

**折中方案的独特优势**：**全量属性永远最新**（水合走 Trino 零拷贝），只有拓扑和剪枝属性有投影延迟。对于图推理场景（先图遍历定位，再水合看详情），这个一致性折衷是合理的——图遍历的"上一秒拓扑"可接受（C8 秒级最终一致），水合的"最新全量属性"是必须的。

**限制 L8：对账失效**。C8 的 `update_time + data_version` 对账对 VIRTUAL 节点失效（外部源无 data_version）。VIRTUAL 节点的"重建"= 重新从 Trino 拉，不是对账。需要：
- VIRTUAL 节点不参与 ConflictDetector 对账（排除）
- VIRTUAL 节点的重建走 `project_for_virtual_object_type`（重新拉 Trino），不走 `rebuild_for_object_type`（从 object_state）

#### 3.4.5 工程成本（🟡，可控）

| 改动项 | 工作量 | 依赖 |
|--------|--------|------|
| `ProjectSyncService.project_for_virtual_object_type` | 中 | TrinoQueryEngine.query + GraphProjector.project_object |
| rid 合成规则（`virtual:` 前缀） | 小 | naming.py |
| `_hydrate` 分流（MANAGED→Doris 主/Trino 降级；VIRTUAL→Trino 外部源 catalog） | 中 | ObjectQueryService.load_by_ids / _virtual_table_ref |
| FK → LinkType 映射（边来源） | 大 | 外部源 FK 探索（Trino 无法提取，需手工声明或 JDBC 直连） |
| 触发链路（register_virtual_table 后 + admin API） | 小 | 现有路由 |
| partial/omitted 降级标记 | 小 | 现有 ADR-020 模式 |
| ConflictDetector 排除 VIRTUAL | 小 | 现有 ConflictDetector |
| 测试（单测 + 集成） | 中 | testcontainers Trino + Neo4j |

**最大工作量在 FK → LinkType 映射**（L2）。这是折中方案能不能真正解决"跨 VIRTUAL 边"的关键。如果 Gaia 本体的 LinkType 已经绑定了外部源 FK 列（通过 `backing_column`），则边投影可以：
```python
# 从 Trino 拉 FK 关系：SELECT customer_id, order_id FROM mysql_orders.orders
# 按 LinkType.backing_column 解析，投影边 (customer_rid)-[:PLACED_ORDER]->(order_rid)
```
但如果 LinkType 没绑定 FK 列（当前可能没有），需要先补这个元数据。

### 3.5 折中方案的演进性（为路径 ③' 留接口）

折中方案的 `_virtual:true` + `_source_ref` 标记，为远期路径 ③'（自研查询时联邦）留了接口：
- 路径 ③' 可以在 `DataFrameQueryService` 层检测 `_virtual` 节点，不查 Neo4j，直接用 `_source_ref` 回查 Trino 水合最新属性
- 这样路径 ③' 不需要重新设计 rid 体系，复用折中方案的 rid 合成 + 分流水合

**这是折中方案相对纯 Ontop 的工程优势**：渐进式演进，每一步都可投产，不需要一次性建翻译器。

### 3.6 结论

**折中方案可行，推荐作为 Gaia 的 MVP 路径**。拓扑/水合/剪枝都 ✅ 或 🟡，一致性居中（全量属性实时，拓扑/剪枝秒级延迟），工程成本可控（最大挑战是 FK→LinkType 映射）。

**与之前 [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) 的路径 ① 的关系**：折中方案 = 路径 ①（身份骨架）+ 路径 ②（indexed 属性）的合并。之前的路径 ① 只投 rid+label，折中方案多投了 PK + title + indexed，代价是投影时要从 Trino 拉更多列，但收益是图遍历可剪枝 + 水合可解析 PK。**折中方案是路径 ①② 的更优合并**。

---

## 四、三方案横向对比

### 4.1 评判矩阵

| 维度 | 纯 Ontop | 纯 Palantir | 折中方案 |
|------|---------|------------|---------|
| **拓扑连通性** | ✅ | ✅ | ✅ |
| **属性水合** | ✅ | ✅ | ✅ |
| **属性剪枝** | ✅（任意属性） | ✅（任意属性） | 🟡（仅 indexed） |
| **一致性-全量属性** | ✅ 实时 | ❌ ETL 延迟 | ✅ 实时 |
| **一致性-拓扑** | ✅ 实时 | ❌ ETL 延迟 | 🟡 投影延迟 |
| **架构合规** | ❌ 双引擎+权限断层 | ❌ 违背 5 条红线 | ✅ 符合 ADR/C1/C8 |
| **工程成本** | ❌ 最高（翻译器） | ❌ 高（ETL 重建） | 🟡 中（FK 映射） |
| **演进性** | ❌ 一步到位 | ❌ 锁死 Neo4j 主存 | ✅ 渐进（留 ③' 接口） |
| **私有化部署** | 🟡 Java sidecar | ✅ 纯 Neo4j | ✅ 纯 Python+Neo4j |

### 4.2 各方案的"能走到哪"总结

| 方案 | 单跳 VIRTUAL 查询 | 多跳跨 VIRTUAL 图推理 | 跨 MANAGED+VIRTUAL 混合 | 权限治理 |
|------|------------------|---------------------|----------------------|---------|
| 纯 Ontop | ✅ 走通 | ✅ 走通（全虚拟） | ❌ 双引擎联合失败 | ❌ SQL 注入断层 |
| 纯 Palantir | ✅ 走通 | ✅ 走通（全在 Neo4j） | ✅ 走通 | 🟡 标记存 Neo4j（社区版退化） |
| 折中方案 | ✅ 走通 | ✅ 走通（骨架在 Neo4j） | ✅ 走通（分流） | 🟡 待补（见下） |

### 4.3 各方案的"卡在哪"总结

| 方案 | 决定性限制 |
|------|-----------|
| 纯 Ontop | IR→SPARQL 翻译器工程量 + MANAGED/VIRTUAL 双引擎联合 + 权限注入断层 |
| 纯 Palantir | 违背 ADR-001/C1/C8/C3/红线9 + 一致性灾难（无 CDC） |
| 折中方案 | FK→LinkType 映射（边来源）+ indexed 属性新鲜度（投影延迟）+ 权限治理待补 |

---

## 五、综合建议

### 5.1 推荐折中方案

**理由**：
1. **唯一架构合规**：不违背任何 Gaia 红线（ADR-001/C1/C8/C3/红线9）
2. **一致性最优平衡**：全量属性实时（Trino 联邦水合），拓扑/剪枝秒级延迟（可接受，C8 已声明）
3. **工程成本可控**：最大挑战 FK 映射有解（Gaia 本体 LinkType + backing_column）
4. **演进性最好**：`_virtual` + `_source_ref` 为路径 ③' 留接口，渐进式增强
5. **私有化友好**：纯 Python + Neo4j 社区版，无 Java sidecar

### 5.2 折中方案的待解决问题（动代码前必须设计）

1. **rid 合成规则**：`virtual:{ont}:{ot}:{pk_value}` 格式 + 可逆解析（水合分流用）
2. **FK → LinkType 映射**：VIRTUAL 对象的边来源。需确认 Gaia LinkType 是否已绑定外部源 FK 列（`backing_column`）；若否，补元数据
3. **权限治理补齐**：`DataFrameQueryService` 注入 `AuthorizationService`，图遍历前校验 ObjectType 级权限；VIRTUAL 水合时注入行级权限（Cedar TPE → Trino SQL WHERE）
4. **partial/omitted 降级**：外部源不可用时返回 `_partial:true`，不失败整个查询
5. **刷新策略**：indexed 属性的定时刷新（分钟级）+ 手动 rebuild admin API

### 5.3 三个方案的借鉴关系

```
纯 Ontop（查时联邦范式）
  └─ 借鉴：mapping 声明式映射思想 → 折中方案的 _source_ref + Trino 水合
  └─ 借鉴：Direct Mapping IRI 模板 → 折中方案的 rid 合成规则
  └─ 不借鉴：SPARQL/RDF 范式、Java sidecar

纯 Palantir（全落地主存）
  └─ 借鉴：基数约束（应用层预查询）→ 折中方案的 ActionService RELATE 校验
  └─ 借鉴：批量提交优化 → 折中方案的 rebuild 批量
  └─ 不借鉴：Neo4j 作主存、系统元属性进 Neo4j、SCD2、标记存 Neo4j

折中方案（Gaia 路径 ①②合并 + Trino 联邦水合）
  └─ 架构：Neo4j 派生索引（骨架）+ Trino 联邦（VIRTUAL 全量，零拷贝）+ Doris（MANAGED 全量，在线读主源）/ Iceberg（MANAGED 归档）
  └─ 演进：留 _virtual + _source_ref 接口 → 路径 ③' 自研查询时联邦
```

### 5.4 与之前调研结论的一致性

本三场景模拟**强化了之前两份调研的结论**：
- [`ontop-source-analysis.md`](./ontop-source-analysis.md) 说"Ontop 不直接集成，借鉴设计思想"→ 场景 1 验证了直接集成的工程障碍（翻译器 + 双引擎 + 权限）
- [`palantir-neo4j-mapping-proposal-comparison.md`](./palantir-neo4j-mapping-proposal-comparison.md) 说"方案不整体照搬，局部借鉴"→ 场景 2 验证了整体照搬的架构违规（5 条红线）
- [`virtual-table-neo4j-projection-feasibility.md`](./virtual-table-neo4j-projection-feasibility.md) 说"推荐 ①→②→③' 演进"→ 场景 3 验证了折中方案（①②合并）的可行性，并细化了 rid 合成、FK 映射、权限补齐等待解决问题

**下一步**：将本分析的折中方案待解决问题（§5.2）转化为 ADR-015 补充 + `graph-reasoning-design.md §6` 修订，再进入实现 PR 拆解。

---

## 附录 A：场景模拟用到的 Gaia 代码事实

| 事实 | 位置 | 对分析的影响 |
|------|------|------------|
| `_hydrate` 当前 MVP 走 PG `get_object_states_by_rids` | `object_set_executor.py:1415`（注释 "MVP：object_state 批量取；Doris 水合留优化期"） | D2 断裂点：VIRTUAL 无 object_state → 水合返回 None。**注：MANAGED 数据本就在 Doris（ADR-001 在线读主源），PG object_state 只是 Action 同步写入态；`_hydrate` 走 PG 是实现未对齐既有架构，折中方案应迁回 Doris/Trino 标准路由** |
| `ObjectQueryService` 标准路由：MANAGED→Doris 主（Trino 降级）；VIRTUAL→Trino 联邦 | `object_query_service.py:4,198,240` | 折中方案水合应复用此既有路由 |
| `get_rids_by_type` 查 object_state | `postgres_meta_store.py:1522` | D1 断裂点：VIRTUAL 起始集为空 |
| `NodeFilter` 仅支持 indexed 属性 | `schemas/graph.py:19` + `neo4j_graph_store.py:89` | D3：剪枝仅限 indexed |
| `_resolve_query_target` VIRTUAL→Trino | `object_query_service.py:561` | 折中方案水合可复用 `_virtual_table_ref` |
| `hydrate_by_pk` 已支持 VIRTUAL（Trino 降级） | `object_query_service.py:381` | 折中方案水合可复用此路径 |
| `DataFrameQueryService` 不注入 AuthorizationService | `object_set_executor.py` 全文 | 权限治理断层（三个方案都有） |
| `TrinoQueryEngine.query(sql, params)` | `trino_query_engine.py:142` | 折中方案投影/水合的 Trino 调用入口 |
| `GraphProjector.project_object` 接受任意 object_state dict | `graph_projector.py:42` | 折中方案可复用，传合成 object_state |
| `ProjectSyncService` Gate 1 skip VIRTUAL | `project_sync_service.py:108` | 折中方案要旁路 Gate 1（新方法） |

## 附录 B：Palantir Foundry 真实范式参考

Palantir Foundry 的本体查询分两层（见 [`reference.md`](../reference.md)）：
- **OSDK `loadObjectSet`**：吃结构化 ObjectSet IR，返回对象集。MANAGED 对象从 Foundry 数据集（Iceberg-like）加载，VIRTUAL 对象从外部源联邦加载
- **AIP Agent**：吃 NL，通过 tool calling 调 OSDK 工具

Foundry **不把 VIRTUAL 对象的图拓扑存进图引擎**——它的"图"是查询时按 LinkType JOIN 算的（类似 Ontop 的查时联邦）。Palantir 的 `searchAround` 实际是 ObjectSet IR 的 `searchAround` type，执行时按 LinkType 定义做 SQL JOIN，不是查图数据库。

**这对 Gaia 的启示**：Gaia 用 Neo4j 做图遍历（性能优势），但 VIRTUAL 对象的图拓扑本质上是"查询时联邦"问题。折中方案用"身份骨架入 Neo4j + 全量 Trino 联邦"是 Foundry 范式 + Neo4j 性能的合理折中——比纯 Foundry（全查时 JOIN）快（拓扑在 Neo4j），比纯 Palantir 方案（全落地）轻（只存骨架）。
