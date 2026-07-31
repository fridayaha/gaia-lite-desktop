# ADR-021：VIRTUAL 对象图投影（折中方案）

> **状态**：已采纳（2026-07-16）
> **决策日期**：2026-07-16
> **关联文档**：
> - 调研全文：[`../research/three-scenarios-ontology-graph-federation.md`](../research/three-scenarios-ontology-graph-federation.md)（三方案场景模拟）
> - 调研附件：[`../research/ontop-source-analysis.md`](../research/ontop-source-analysis.md) · [`../research/palantir-neo4j-mapping-proposal-comparison.md`](../research/palantir-neo4j-mapping-proposal-comparison.md) · [`../research/virtual-table-neo4j-projection-feasibility.md`](../research/virtual-table-neo4j-projection-feasibility.md)
> - 实现设计：[`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md)（**工程落地权威源**，含组件契约/PR 拆解/测试矩阵）
> - 架构基线：[`graph-reasoning-design.md`](./graph-reasoning-design.md) §6（本 ADR 新增 §6.5 触发模式 D + 修订 §6.3/§6.4）
> - RID 体系：[`handoff-rid-migration.md`](./handoff-rid-migration.md) + [`core/rid.py`](../../src/ontology/core/rid.py)
> - 交接输入：[`handoff-virtual-graph-projection.md`](./handoff-virtual-graph-projection.md)（本文档的前身，部分待确认点已在实现设计中闭合）
> **取代**：无（首次决策）

---

## 背景

### 问题：图关联推理跨 VIRTUAL 对象断链

Gaia 的图关联推理（`search_around` / `find_paths` / `exists_link`）在跨 VIRTUAL 对象时断链。根因：

- **D1 拓扑断链**：VIRTUAL 对象不在 Neo4j 里。`ProjectSyncService.project_for_object_type` 的 Gate 1 硬 skip VIRTUAL（VIRTUAL 无 Iceberg 表，`scan_latest` 扫不到），`OutboxExecutor` 的 INDEX effect 只消费 Action 产生的 outbox（VIRTUAL 禁止 Action 写入，红线 9）。图遍历走到 VIRTUAL ObjectType 返回空。
- **D2 水合断链**：图遍历即便返回 VIRTUAL rid，`_hydrate` 走 PG `object_state` 批量取，VIRTUAL 无 object_state 记录。**注**：此 D2 已由 RID 迁移（`handoff-rid-migration.md`）解决——`_hydrate` 已按 rid type 段分流（MANAGED→PG/Doris，VIRTUAL→Trino 联邦），见 `object_set_executor.py:1416`。
- **D3 剪枝缺失**：VIRTUAL 节点无属性在 Neo4j，`NodeFilter` 无法剪枝。

本 ADR 聚焦解决 **D1（拓扑断链）+ D3（剪枝缺失）**。D2 已闭合。

### 三方案调研结论

经三方案端到端场景模拟（[`three-scenarios-ontology-graph-federation.md`](../research/three-scenarios-ontology-graph-federation.md)）：

| 方案 | 拓扑 | 水合 | 剪枝 | 一致性 | 架构合规 | 工程成本 | 结论 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|------|
| 纯 Ontop（查时联邦，RDF/SPARQL） | ✅ | ✅ | ✅任意 | ✅实时 | ❌双引擎+权限断层 | ❌最高（IR→SPARQL 翻译器） | **否决** |
| 纯 Palantir（Neo4j 作主存，全量 ETL） | ✅ | ✅ | ✅任意 | ❌ETL延迟 | ❌违5条红线 | ❌高（ETL重建） | **否决** |
| 折中方案（身份骨架投影 + Trino 联邦水合） | ✅ | ✅ | 🟡仅indexed | 🟡拓扑延迟/全量实时 | ✅ | 🟡中 | **采纳** |

否决详情见各方案调研文档。核心否决理由：
- **Ontop**：ObjectSet IR→SPARQL 翻译器是新工程（Ontop 不提供）；MANAGED+VIRTUAL 混合查询需双引擎联合；Ontop 生成的 SQL 无法注入 Cedar TPE 行级权限；Java sidecar 进 Python 栈。
- **Palantir**：违背 ADR-001（Doris 在线读主源，方案让 Neo4j 也存全量属性=双主源）、C1（图节点轻量）、C8（派生副本）、C3（VIRTUAL 不落地）、红线 9（VIRTUAL 禁止写入，ETL 导入是写入）；无 CDC 通道致一致性灾难。
- **Neo4j Virtual Graph**：核实结论是只在 Aura 云提供（private preview），社区版/企业版自托管均不可用（[`../research/virtual-table-neo4j-projection-feasibility.md`](../research/virtual-table-neo4j-projection-feasibility.md) §3.3）。其设计思想（Cypher→SQL 翻译器下推）是远期路径 ③' 的参考，非本期集成。

---

## 决策

### D1：采用折中方案——VIRTUAL 身份骨架投影进 Neo4j，全量属性走 Trino 联邦水合

**Neo4j 存什么**（VIRTUAL 节点的"身份骨架"）：

| 字段 | 用途 | 必存 |
|------|------|:---:|
| `rid`（`ri.ontology.main.virtual-object.{ont}.{ot}.{pk}`） | 节点主键 / 水合寻址 | ✅ |
| `api_name` | 水合分流 + 返回 | ✅ |
| 主键业务值（PK property api_name） | 避免重复解析 rid locator | ✅ |
| title property | 画布渲染节点标题（不查全量也能显示） | ✅ |
| `indexed` 属性 | 图遍历剪枝（`NodeFilter` 下推） | ✅ |
| `_virtual: true` | 标记 VIRTUAL 节点（水合分流、远期 ③' 探测、cleanup 范围） | ✅ |
| `_source_ref`（Trino table ref，如 `mysql_orders.orders`） | 远期 ③' 回查水合最新属性 | ✅ |
| `_sync_tag`（int 时间戳） | 投影刷新的孤儿清理（watermark + cleanup 模式，见 D6） | ✅ VIRTUAL 专有 |

**全量属性在哪**：永远在查询时经 Trino 联邦查外部源（零拷贝，永远最新）。**不投影、不缓存、不落地进 Neo4j**。

**图遍历**：走 Neo4j（拓扑 + 剪枝字段在 Neo4j）。
**水合**：图遍历返回 rid 后，`_hydrate` 按 rid type 段分流——MANAGED→Doris 主源点查（PG object_state MVP，降级 Trino Iceberg）；VIRTUAL→Trino 联邦查外部源表（复用 `ObjectQueryService.hydrate_by_pk`，已落地）。

### D2：Neo4j 仍是派生索引，VIRTUAL 投影是模式 C 的扩展

Neo4j 官方 polyglot persistence 三模式：A 全量迁移 / B 子集迁移 / C 子集复制。Gaia MANAGED 走模式 C（Doris 主源 + Neo4j 派生索引）。折中方案让 VIRTUAL 也走模式 C，但"源"不同：

- MANAGED 的源：Doris（在线读主源）/ Iceberg（归档）/ PG object_state（Action 写入态）
- VIRTUAL 的源：外部源（经 Trino 联邦，零拷贝）

两者都是"主源 + Neo4j 派生索引"，Neo4j 在两种情况下都是**派生副本**，定位不变。**派生索引三性质保持**（对齐 graph-reasoning-design.md §6.3 C8）：
1. **单向**：只能从源投影到 Neo4j，禁止反写
2. **可重建**：删光 Neo4j 所有 VIRTUAL 节点，能从 Trino 重新投影重建
3. **同源可信源**：VIRTUAL 的源是外部源（经 Trino 联邦），不是 Neo4j 自身

### D3：新增触发模式 D——VIRTUAL 联邦投影

graph-reasoning-design.md §6.1 现有三种写入触发模式（A=Action / B=SeaTunnel 批量 / C=时序流式）。新增 **模式 D：VIRTUAL 联邦投影**：

| 模式 | 触发 | 数据源 | 写入目标 | 链路 |
|------|------|--------|---------|------|
| **D. VIRTUAL 联邦投影** | `register_virtual_table` 成功 / admin rebuild API | Trino 联邦查外部源（`SELECT pk, title, indexed_cols FROM <virtual_table_ref>`） | Neo4j 节点（身份骨架）+ Neo4j 边（FK 推导） | TrinoQueryEngine.query → 构造合成 object_state → GraphProjector.project_object + project_link |

**与 A/B/C 的区别**：不经 Iceberg（VIRTUAL 无表）/ 不经 outbox（VIRTUAL 不产生 Action）/ 不经 SeaTunnel（不落地）。数据源是 TrinoQueryEngine，不是 IcebergStore。

### D4：rid 合成复用 `core/rid.py`（已落地）

- `generate_virtual_rid(ont, ot, pk)` → `ri.ontology.main.virtual-object.{ont}.{ot}.{safe_pk}`
- `parse_virtual_rid_pk(rid)` → `(ont, ot, pk)`
- `is_virtual_rid(rid)` / `is_managed_rid(rid)` → type 段判别

水合分流已由 `DataFrameQueryService._hydrate` 实现（按 `is_managed_rid`/`is_virtual_rid` 分流，见 `object_set_executor.py:1416`）。**本 ADR 不改 rid 体系**。

### D5：边来源——外部源 FK 推导，非 Action

VIRTUAL 对象的边**不能来自 Action**（红线 9：VIRTUAL 禁止写入，Action 不产生 RELATE mutation）。边从**外部源的 FK 关系推导**：

- `LinkType.foreign_key_property_api_name`（属性 api_name，可空）+ `Property.backing_column`（物理列名）是 FK→边映射的元数据锚点
- FK 属性归属：文档明确"存储在源或目标端属性上"（[`ontology-tool-layer.md`](ontology-tool-layer.md) §`define_link_type`），实现按 **source 端优先 → target 端兜底** 两端容错查找 backing_column
- 默认语义：FK 在 source 端持向 target PK（经典 N:1，如 `orders.customer_id → customers.id`）
- 缺失 `foreign_key_property_api_name` 时：边不投影，节点仍投影（降级，不阻塞）

### D6：孤儿清理——watermark + cleanup 模式（cartography 范式）

外部源会删除对象，投影态会残留孤儿节点（rid 仍在 Neo4j 但源里已删）。采用 cartography（CNCF）的 watermark + cleanup 模式：

1. 每次 `project_for_virtual_object_type` 生成单调递增的 `_sync_tag`（int 时间戳）
2. MERGE 节点时 `SET n._sync_tag = $sync_tag`
3. **先建后删**（"MERGE first, then clean up"）：节点投影完成后，执行 cleanup：`MATCH (n:Label {_virtual: true}) WHERE n._sync_tag <> $sync_tag DETACH DELETE n`
4. cleanup 必须带 label + `_virtual: true` 维度，**绝不误删 MANAGED 节点**（MANAGED 不带 `_virtual`/`_sync_tag`）

**为什么不用全量重建（drop + re-import）**：重建窗口期查询断链，违反 C9 包容式防线。watermark 模式无窗口期（先建后删）。

### D7：一致性语义——best-effort + 不可对账

VIRTUAL 节点的一致性模型与 MANAGED 不同：

| 维度 | MANAGED 节点 | VIRTUAL 节点 |
|------|-------------|-------------|
| 一致性语义 | 最终一致（秒级），C8 对账（update_time + data_version） | best-effort，**不可对账**（外部源无 data_version） |
| 重建方式 | `rebuild_for_object_type`（从 object_state 重投影） | `project_for_virtual_object_type`（重新查 Trino） |
| 对账参与 | ✅ ConflictDetector 审计 Doris 存在性 | ❌ 排除（外部源无版本号，对账无意义） |
| 全量属性新鲜度 | Doris 主源（秒级） | Trino 联邦（实时，零拷贝） |
| 拓扑/剪枝新鲜度 | object_state 驱动（秒级） | 投影态（刷新策略决定，MVP 手动 rebuild） |

`ConflictDetector._audit_iteration` 遍历 ObjectType 时排除 `storage_type == VIRTUAL`。

### D8：为远期路径 ③' 留接口

折中方案是 MVP，不是终态。远期路径 ③'（自研查询时联邦，对齐 Neo4j Virtual Graph 的 Cypher→SQL 翻译器思路，但自研实现因 Aura 私有预览不可用）会让 `DataFrameQueryService` 检测到 `_virtual: true` 节点时，用 `_source_ref` 经 Trino 回查水合最新属性。

**留接口**：VIRTUAL 节点的 `_virtual` + `_source_ref` 标记是路径 ③' 的探测点。这两个字段必须存进 Neo4j 节点，且 `Neo4jGraphStore` 查询返回要带上。路径 ③' 复用折中方案的 rid 合成 + 分流水合，不重新设计身份体系。

### D9：触发链路——异步 best-effort，不走 outbox

- **自动触发**：`register_virtual_table` 成功后，`asyncio.create_task` 异步触发投影（best-effort，失败记日志不阻塞注册返回）
- **手动触发**：admin API `POST /admin/project/rebuild-for-virtual/{ont}/{ot}`（幂等，重复调用=重新拉 Trino + MERGE + cleanup）
- **不走 outbox**：outbox 是 Action 写入语义（每个 effect 对应一个 Action mutation 的派生副作用，见 `outbox_executor.py:148-171` 的 WEBHOOK/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC/INDEX/EMBEDDING/ARCHIVE 全部源自 Action）。VIRTUAL 投影的触发源是"外部数据接入"不是 Action，塞进 outbox 语义错位。VIRTUAL 节点 best-effort + 不可对账，丢失后手动 rebuild 兜底，不需要 outbox 可靠重试。

### D10：权限补齐——ObjectType 级 P0 随折中方案（独立 PR），行级 P1 独立

- **P0（随折中方案，独立 PR 0 前置）**：`DataFrameQueryService` 注入 `AuthorizationService`，图遍历入口调 `check_access`（ObjectType 级，防泄露无权 ObjectType 的存在性）。`AuthorizationService` 已实现（`authorization_service.py`），只是接线。
- **P1（独立 PR，ADR-016 Phase 6+）**：VIRTUAL 水合行级权限（Cedar TPE → SqlGlot WHERE 注入 Trino 查询）。

---

## 替代方案（已否决）

### 否决 1：纯 Ontop（查时联邦）

见 [`three-scenarios-ontology-graph-federation.md`](../research/three-scenarios-ontology-graph-federation.md) §1.5。决定性障碍：
1. ObjectSet IR→SPARQL 翻译器（13 type）是新工程，Ontop 不提供
2. MANAGED+VIRTUAL 混合查询双引擎联合（Neo4j 存 MANAGED，Ontop 存 VIRTUAL）
3. Ontop 生成 SQL 无法注入 Cedar TPE 行级权限
4. Java sidecar 进 Python 栈

**借鉴**：mapping 声明式映射思想（→ `_source_ref`）、Direct Mapping IRI 模板（→ rid 合成）、查询时联邦范式（→ 路径 ③'）。

### 否决 2：纯 Palantir（Neo4j 作主存）

见 [`three-scenarios-ontology-graph-federation.md`](../research/three-scenarios-ontology-graph-federation.md) §2.4。违背 5 条架构红线（ADR-001/C1/C8/C3/红线9）+ 一致性灾难（无 CDC）。

**借鉴**：基数约束应用层预查询（→ ActionService RELATE 校验）、批量提交优化（→ rebuild 批量）、Marking lattice（PG 层）。

### 否决 3：Neo4j Virtual Graph

见 [`../research/virtual-table-neo4j-projection-feasibility.md`](../research/virtual-table-neo4j-projection-feasibility.md) §3.3。只在 Aura 云 private preview，社区版/企业版自托管不可用。其"查询时联邦"设计是路径 ③' 的参考，非本期集成。

### 否决 4：全量重建（drop + re-import）做孤儿清理

重建窗口期查询断链违反 C9。改用 watermark + cleanup（D6）。

### 否决 5：outbox 投递 VIRTUAL 投影任务

outbox 是 Action 写入语义，VIRTUAL 投影触发源不是 Action，语义错位（D9）。

---

## 实现范围

完整工程落地指导见 [`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md)，含：
- 组件契约（`ProjectSyncService.project_for_virtual_object_type` / `GraphProjector` 扩展 / `Neo4jGraphStore` 批量方法 / `PostgresMetaStore.get_object_states_by_pks`）
- 数据流时序（投影/查询/刷新）
- 难点决策记录（FK 解析 / 孤儿清理 / 大表分页 / 批量写入 / 跨 catalog JOIN）
- PR 拆解（6 个 PR，按依赖排序）
- 测试矩阵（单测/集成/异常路径）
- 文档同步清单

**MVP 范围**：
- ✅ VIRTUAL 身份骨架节点投影（rid + label + PK + title + indexed + `_virtual` + `_source_ref` + `_sync_tag`）
- ✅ FK→边投影（一端 VIRTUAL 一端 MANAGED + 两端 VIRTUAL，决策 6 已确认两端 VIRTUAL 成本不更高，MVP 一并做）
- ✅ 触发链路（register_virtual_table 异步 + admin rebuild API）
- ✅ 孤儿清理（watermark + cleanup）
- ✅ partial 降级（外部源不可用返回 `_partial`，不失败整个查询）
- ✅ ConflictDetector 排除 VIRTUAL

**二期范围**：
- 🟡 indexed 属性定时刷新（分钟级）
- 🟡 VIRTUAL 水合行级权限（Cedar TPE → Trino WHERE）
- 🟡 `foreign_key_property_api_name` 缺失时从外部源 schema FK 自动推断回填（借鉴 Neo4j Virtual Graph 的 AI model 生成）
- 🟡 路径 ③' 自研查询时联邦

---

## 后续工作

1. 实现设计文档 [`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md) 的 PR 拆解落地
2. graph-reasoning-design.md §6 修订（新增 §6.5 + 修订 §6.3/§6.4）—— 见本文档 D2/D3/D7
3. CLAUDE.md 红线 9 补充图投影例外说明 + ADR 索引表加 ADR-021
4. 权限 P0 独立 PR（`DataFrameQueryService` 注入 `AuthorizationService`）

---

## 参考

- [Palantir Foundry · Object identifiers](https://palantir.com/docs/foundry/functions/object-identifiers/) — RID 体系源头
- [Palantir resource-identifier spec](https://github.com/palantir/resource-identifier) — RID regex 规范
- [Neo4j · Introducing Virtual Graph](https://neo4j.com/blog/graph-database/introducing-neo4j-virtual-graph-graph-reasoning-on-the-data-you-already-have/) — 路径 ③' 设计参考（Aura private preview，不可直接集成）
- [Neo4j · CALL {} IN TRANSACTIONS](https://neo4j.com/docs/cypher-manual/current/subqueries/subqueries-in-transactions/) — 批量写入（替代 deprecated 的 `apoc.periodic.iterate`）
- [cartography (CNCF) · Sync](https://cartography-cncf.github.io/cartography/references/sync.html) — watermark + cleanup 孤儿清理范式
- [Ontop · Role of foreign keys](https://ontop-vkg.org/tutorial/mapping/foreign-keys.html) — FK 在 VKG 查询优化中的作用
- [Trino · Join pushdown](https://trino.io/docs/current/optimizer/pushdown.html) — 跨 catalog JOIN 的下推条件
