# Handoff —— VIRTUAL 对象图投影（折中方案落地指导）

> **⚠️ 状态更新（2026-07-16）**：本文档的待确认点已全部闭合，决策已落地为正式架构文档。本文保留作为决策过程的交接记录，**实现者请以以下文档为准**：
> - 决策权威源：[`adr-021-virtual-graph-projection.md`](./adr-021-virtual-graph-projection.md)（ADR，记录"为什么这样决策"）
> - 工程落地权威源：[`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md)（组件契约/数据流/难点决策记录/PR 拆解/测试矩阵）
> - 架构基线：[`graph-reasoning-design.md`](./graph-reasoning-design.md) §6.5（已新增 VIRTUAL 联邦投影设计）
>
> 本文与上述文档的差异（已在新文档中修正）：
> - §4.2 "ObjectType 是否有 title_property" → **已有**（`ObjectTypeModel.title_property` + `PropertyDefModel.is_title_property`）
> - §4.3 "FK 属性归属哪一端" → **文档明确"源或目标端"**，两端容错查找
> - §4.3 "两端 VIRTUAL 边" → **提至 MVP**（成本不更高，少一步 PG 反查）
> - §4.4 "触发链路" → **`asyncio.create_task`**（不走 outbox，outbox 是 Action 语义）
> - §4.4 "孤儿清理" → **新增 watermark+cleanup 机制**（本文遗漏，cartography 范式）
> - §7.2 "ADR 归属" → **新建 ADR-021**
> - §4.2/§2.5 "分页/批量" → **游标分页 + CALL {} IN TRANSACTIONS**（弃 OFFSET/逐条 MERGE）
>
> 完整闭合状态见本文末「附录 C：待确认点闭合状态」。
>
> ---
> **原始用途**：本文是给后续开发人员的输入文档。它把 [`three-scenarios-ontology-graph-federation.md`](../research/three-scenarios-ontology-graph-federation.md) 的折中方案结论，转化为可直接进入 PR 拆解的工程指导：目标、原则、设计、取舍、边界、验收。
> **不写代码**——只给足够的约束和判据，让实现者知道"该做什么、不该做什么、为什么、卡住时怎么决策"。
> **关联文档**：
> - 调研全文：[`../research/three-scenarios-ontology-graph-federation.md`](../research/three-scenarios-ontology-graph-federation.md)（三方案场景模拟，本文是其结论的工程化）
> - 架构基线：[`graph-reasoning-design.md`](./graph-reasoning-design.md) §6（本文要新增 §6.5「触发模式 D：VIRTUAL 联邦投影」+ 修订 §6.3/§6.4）
> - RID 体系：[`handoff-rid-migration.md`](./handoff-rid-migration.md) + [`src/ontology/core/rid.py`](../../src/ontology/core/rid.py)（VIRTUAL rid 合成已落地，本文复用）
> - 外部参考：[`../research/ontop-source-analysis.md`](../research/ontop-source-analysis.md) · [`../research/palantir-neo4j-mapping-proposal-comparison.md`](../research/palantir-neo4j-mapping-proposal-comparison.md) · [`../research/virtual-table-neo4j-projection-feasibility.md`](../research/virtual-table-neo4j-projection-feasibility.md)
> **日期**：2026-07-15

---

## 〇、TL;DR（实现者先读这段）

**要解决的问题**：图关联推理（`search_around` / `find_paths` / `exists_link`）跨 VIRTUAL 对象时断链——VIRTUAL 节点根本不在 Neo4j 里（`ProjectSyncService` Gate 1 硬 skip + 红线 9 禁止 VIRTUAL 写入），图遍历走到 VIRTUAL ObjectType 返回空。

**采用方案**：折中方案——把 VIRTUAL 对象的**身份骨架**（rid + label + PK + title + indexed 属性 + `_virtual:true` + `_source_ref`）投影进 Neo4j，全量属性不走投影，水合时走 Trino 联邦查外部源（零拷贝，永远最新）。

**已经落地的部分**（不要重复造）：
- ✅ `core/rid.py`：`generate_virtual_rid(ont, ot, pk)` / `parse_virtual_rid_pk(rid)` / `is_virtual_rid(rid)` —— rid 合成与解析
- ✅ `DataFrameQueryService._hydrate`：已按 rid type 段分流，MANAGED→PG object_state（MVP，未来切 Doris），VIRTUAL→`hydrate_by_pk` 走 Trino 联邦
- ✅ `LinkTypeModel`：已有 `foreign_key_property_api_name` + `cardinality` 字段
- ✅ `ObjectQueryService.hydrate_by_pk` + `_virtual_table_ref`：VIRTUAL 联邦水合链路

**还差的部分**（本文档指导实现）：
- 🔴 `ProjectSyncService.project_for_virtual_object_type`：VIRTUAL 身份骨架投影入口（旁路 Gate 1）
- 🔴 FK→边的投影：VIRTUAL 对象的边来源（外部源 FK 推导，非 Action）
- 🔴 触发链路：`register_virtual_table` 后自动投影 + admin rebuild API
- 🔴 节点 schema：`_virtual` / `_source_ref` 元属性进 Neo4j 节点
- 🟡 权限治理补齐：`DataFrameQueryService` 注入 `AuthorizationService`
- 🟡 partial/omitted 降级标记
- 🟡 `ConflictDetector` 排除 VIRTUAL 节点（不对账）

**核心约束**：Neo4j 仍是**派生索引**，不是主存。VIRTUAL 投影是**模式 C 的扩展**（身份骨架复制），不是 ETL 落地。违反这一点的任何"为图方便把全量属性也塞进 Neo4j"的诱惑都要拒绝——那是被否决的纯 Palantir 方案的反面。

---

## 一、目标

### 1.1 业务目标

让 Gaia 的图关联推理能**跨 VIRTUAL 节点不断链**。具体表现为：当本体里存在 `Customer(MANAGED) -[placedOrder]-> Order(VIRTUAL) -[suppliedBy]-> Supplier(MANAGED)` 这样的混合链路时，Agent 发起的 `searchAround` 能从 Customer 一路遍历到 Supplier，中间穿过 VIRTUAL 的 Order 节点。

### 1.2 技术目标（可验收）

| 编号 | 目标 | 验收方式 |
|------|------|---------|
| G1 | VIRTUAL ObjectType 投影后，Neo4j 里有对应节点（带 `_virtual:true`） | Cypher `MATCH (n:FooOrder {_virtual:true}) RETURN count(n)` > 0 |
| G2 | 跨 VIRTUAL 节点的 `searchAround` 返回非空结果 | 端到端测试：混合链路 searchAround 返回目标 rid 集 |
| G3 | 图遍历返回的 VIRTUAL rid 能水合到全量属性（Trino 联邦） | `_hydrate` 对 virtual rid 返回 `props` 非空 |
| G4 | indexed 属性可在图遍历时剪枝 | `NodeFilter` 对 VIRTUAL 节点的 indexed 字段生效 |
| G5 | 外部源不可用时查询不整体失败 | 返回 `_partial:true` 标记，而非抛异常 |

### 1.3 非目标（明确不做）

- ❌ **不改 VIRTUAL 的存储语义**：VIRTUAL 仍是"不落地的联邦代理指针"，不引入 Iceberg 副本。把 VIRTUAL 改成可落地走 MANAGED 路径 = 红线 9 / C3 违反，否决（见可行性调研 §5.3）。
- ❌ **不引入 CDC 通道同步 VIRTUAL**：外部源 CDC 是独立工程（ADR-014 外部 CDC→Iceberg，那是把 VIRTUAL 升级成 MANAGED 的路径），不在此范围。
- ❌ **不用 Neo4j Virtual Graph**：核实结论是它只在 Aura 云提供，社区版不可用（可行性调研 §3.3）。
- ❌ **不集成 Ontop**：范式阻抗（RDF vs 属性图）+ 工程成本，否决（三场景 §1.5）。
- ❌ **不做实时图拓扑**：拓扑是投影态（分钟级延迟），实时需求等远期路径 ③'（自研查询时联邦）。

---

## 二、原则（设计不变量，实现时反复对照）

### P1. Neo4j 是派生索引，不是主存

这是最高原则，所有取舍的仲裁基准。派生索引的含义（对齐 `graph-reasoning-design.md §6.3` C8）：
- **单向**：只能从源投影到 Neo4j，禁止反写
- **可重建**：删光 Neo4j 所有节点，能从 PG object_state + Trino 重新投影重建
- **同源可信源**：MANAGED 的源是 Doris（在线读主源）/Iceberg（归档）+ PG object_state（Action 写入态）；VIRTUAL 的源是外部源（经 Trino 联邦）

**违反信号**：任何让 Neo4j 成为"数据真相来源"的设计（如让查询绕过 Trino 直接信 Neo4j 的全量属性、让 Neo4j 的节点属性成为对账基准）都是违反 P1。

### P2. 身份骨架最小化（C1 图节点轻量）

Neo4j 节点只存**图遍历必需**的字段，不多存一个字节：

| 字段 | 用途 | 必存 |
|------|------|------|
| `rid` | 节点主键 / 水合寻址 | ✅ |
| `api_name`（label 已含，但属性也存一份便于查询返回） | 水合分流 | ✅ |
| 主键业务值（PK，如 `orderId`） | 水合时 `parse_virtual_rid_pk` 已能解析，但存一份避免重复解析 | ✅ |
| `title`（描述字段，如 `orderNo`） | 画布渲染节点标题（不查全量也能显示） | ✅ |
| `indexed` 属性 | 图遍历剪枝 | ✅（按 ObjectType 元数据） |
| `_virtual:true` | 标记这是 VIRTUAL 节点（水合分流、远期 ③' 探测） | ✅ |
| `_source_ref`（Trino table ref，如 `mysql_orders.orders`） | 远期 ③' 回查水合最新属性 | ✅ |
| 全量业务属性 | — | ❌ 违反 C1，全量在 Doris/Trino |
| 系统元属性（`_source`/`_dataset_id`/`_valid_start`...） | — | ❌ Palantir 方案的反模式（对照分析 §1.2） |
| 安全标记（`_mark_conjunct` 等） | — | ❌ 标记在 PG，不进 Neo4j（对照分析 §2.2） |
| SCD2 时序（`_is_deleted`/`_valid_start`/`_valid_end`） | — | ❌ Iceberg time travel 已覆盖（对照分析 §4） |

**判据**：要往 Neo4j 节点加字段时，问自己"这个字段是图遍历/剪枝必需的吗？"。不是 → 拒绝。

### P3. VIRTUAL 投影旁路 Gate 1，不污染 MANAGED 路径

现有 `ProjectSyncService.project_for_object_type` 的 Gate 1 是刻意的（VIRTUAL 无 Iceberg 表，扫不到数据）。折中方案**不改 Gate 1**，而是新增 `project_for_virtual_object_type` 方法单独走 Trino 数据源。

理由（可行性调研 §6.3）：
- MANAGED 路径数据源是 `IcebergStore.scan_latest`；VIRTUAL 路径数据源是 `TrinoQueryEngine.query`。混在一个方法里会让 ProjectSyncService 同时依赖两个 Layer，职责膨胀。
- VIRTUAL 的触发时机、一致性模型都和 MANAGED 不同（VIRTUAL 不接 outbox INDEX effect，刷新靠手动/定时），混在一起会污染现有清晰的设计。
- 符合 CLAUDE.md「不做侵入式扩展，只基于已有扩展能力扩展」原则。

### P4. 全量属性永远走 Trino 联邦（零拷贝）

VIRTUAL 的全量属性**永远**在查询时经 Trino 联邦查外部源，不投影、不缓存、不落地。这是折中方案相对纯 Palantir 方案的核心优势（一致性维度，三场景 §4.1）：

| 一致性维度 | 纯 Palantir | 折中方案 |
|-----------|------------|---------|
| 全量属性 | ❌ ETL 延迟 | ✅ 实时（Trino 联邦，零拷贝） |
| 拓扑（边） | ❌ ETL 延迟 | 🟡 投影延迟（可接受） |
| 剪枝属性（indexed） | ❌ ETL 延迟 | 🟡 投影延迟（可接受） |

**判据**：如果某个改动让 VIRTUAL 全量属性开始"缓存进 Neo4j"，它就越界成了纯 Palantir 方案——拒绝。

### P5. 边来源：外部源 FK 推导，不是 Action

VIRTUAL 对象的边**不能来自 Action**（红线 9：VIRTUAL 禁止写入，Action 不会对 VIRTUAL 产生 RELATE mutation）。边必须从**外部源的 FK 关系推导**：

- 外部源表 `orders` 有列 `customer_id` 指向 `customers.id` → 推导出 `Customer -[placedOrder]-> Order` 边
- Gaia 本体的 `LinkType` 已有 `foreign_key_property_api_name` 字段（存外部源 FK 列名）+ `cardinality`（ONE/MANY）—— 这就是 FK→LinkType 映射的元数据锚点

**这是折中方案最大的设计挑战**（三场景 §3.4.1 L2）。详见 §四.3。

### P6. 渐进演进，为路径 ③' 留接口

折中方案是 MVP，不是终态。远期路径 ③'（自研查询时联邦）会让 `DataFrameQueryService` 检测到 `_virtual:true` 节点时，用 `_source_ref` 经 Trino 回查水合最新属性（对齐 Neo4j Virtual Graph 的"查时下推"）。

**留接口的方式**：VIRTUAL 节点的 `_virtual` + `_source_ref` 标记是路径 ③' 的探测点。实现折中方案时，这两个字段必须存进 Neo4j 节点，且 `Neo4jGraphStore` 的查询返回要带上它们。这样 ③' 不需要重新设计 rid 体系，复用折中方案的 rid 合成 + 分流水合。

---

## 三、架构定位（在 Gaia 分层里的位置）

### 3.1 折中方案 = 模式 C 的扩展

Neo4j 官方 polyglot persistence 三模式（可行性调研 §2）：A 全量迁移 / B 子集迁移 / C 子集复制。Gaia 当前 MANAGED 走模式 C（Doris 主源 + Neo4j 派生索引）。

折中方案让 VIRTUAL 也走模式 C，但"源"不同：
- MANAGED 的源：Doris（在线读主源）/ Iceberg（归档）/ PG object_state（Action 写入态）
- VIRTUAL 的源：外部源（经 Trino 联邦，零拷贝）

两者都是"主源 + Neo4j 派生索引"，只是主源形态不同。Neo4j 在两种情况下都是**派生副本**，定位不变。

### 3.2 新增触发模式 D

`graph-reasoning-design.md §6.1` 现有三种写入触发模式（A=Action / B=SeaTunnel 批量 / C=时序流式）。折中方案新增 **模式 D：VIRTUAL 联邦投影**：

| 模式 | 触发 | 数据源 | 写入目标 | 链路 |
|------|------|--------|---------|------|
| **D. VIRTUAL 联邦投影** | `register_virtual_table` 成功 / admin rebuild API | Trino 联邦查外部源（`SELECT pk, title, indexed_cols FROM <virtual_table_ref>`） | Neo4j 节点（身份骨架）+ Neo4j 边（FK 推导） | TrinoQueryEngine.query → 构造合成 object_state → GraphProjector.project_object + project_link |

**与 A/B/C 的区别**：
- 不经 Iceberg（VIRTUAL 无 Iceberg 表）
- 不经 outbox（VIRTUAL 不产生 Action 写入，无 INDEX effect）
- 不经 SeaTunnel（不落地，无主流水线）
- 数据源是 Trino，不是 IcebergStore

### 3.3 一致性语义：best-effort + 不可对账

VIRTUAL 节点的一致性模型与 MANAGED 不同（三场景 §3.4.4 L8）：

| 维度 | MANAGED 节点 | VIRTUAL 节点 |
|------|-------------|-------------|
| 一致性语义 | 最终一致（秒级），C8 对账（update_time + data_version） | best-effort，**不可对账**（外部源无 data_version） |
| 重建方式 | `rebuild_for_object_type`（从 object_state 重投影） | `project_for_virtual_object_type`（重新查 Trino） |
| 对账参与 | ✅ ConflictDetector 审计 Doris 存在性 | ❌ 排除（外部源无版本号，对账无意义） |
| 全量属性新鲜度 | Doris 主源（秒级） | Trino 联邦（实时，零拷贝） |
| 拓扑/剪枝新鲜度 | object_state 驱动（秒级） | 投影态（分钟级，刷新策略决定） |

**要在 `graph-reasoning-design.md §6.4` 明确声明**：VIRTUAL 节点是 best-effort + 不可对账，不参与 ConflictDetector。

---

## 四、设计（按组件拆解）

### 4.1 rid 合成（已落地，复用）

**现状**：`core/rid.py` 已实现：
- `generate_virtual_rid(ont, ot, pk)` → `ri.ontology.main.virtual-object.{ont}.{ot}.{safe_pk}`
- `parse_virtual_rid_pk(rid)` → `(ont, ot, pk)`（`split(".", 2)`，pk 内部允许点）
- `is_virtual_rid(rid)` / `is_managed_rid(rid)` → type 段判别

**实现者须知**：
- `safe_pk` 会把非 `[a-zA-Z0-9\-\._]` 字符替换为 `_`。若业务 PK 含中文/空格，`parse_virtual_rid_pk` 返回的 pk 与原始 pk 不一致。MVP 假设 PK 是字母数字；若实际业务 PK 含特殊字符，调用方需先编码（如 base64）再传入。**这是已知限制，文档要记，不要在 rid 模块里静默修正。**
- VIRTUAL rid 不保证稳定（外部源 PK 改了就变）。这是 VIRTUAL 的固有特性，与 MANAGED 的"rid 稳定不变"不同。`core/rid.py` 的 docstring 已说明。
- 水合分流已由 `_hydrate` 实现者完成（按 `is_managed_rid` / `is_virtual_rid` 分流）。新代码不要重复实现分流逻辑。

### 4.2 VIRTUAL 身份骨架投影（核心新增）

**入口**：`ProjectSyncService.project_for_virtual_object_type(ontology_api_name, object_type_api_name)`

**职责**：
1. 旁路 Gate 1（Gate 1 仍对 `project_for_object_type` 生效，新方法不经过它）
2. 从 `ObjectQueryService._virtual_table_ref(ot)` 拿 Trino table ref
3. 查 ObjectType 元数据，拿 PK 列 + title 列 + indexed 列列表
4. 从 Trino 拉数据：`SELECT {pk_col}, {title_col}, {indexed_cols...} FROM {table_ref}`
5. 逐行构造合成 object_state dict，调 `GraphProjector.project_object`
6. （边投影，见 §4.3）

**合成 object_state 的形状**（传给 `project_object` 的 dict）：
```python
{
    "id": generate_virtual_rid(ont, ot, row[pk_col]),  # 合成 rid
    "object_type_api_name": ot,
    "properties": {
        pk_api_name: row[pk_col],
        title_api_name: row[title_col],
        **{p.api_name: row[col] for p in indexed_props},  # indexed 属性
    },
    # 元标记（GraphProjector 要识别并写入 Neo4j 节点）
    "_virtual": True,
    "_source_ref": table_ref,  # 如 "mysql_orders.orders"
}
```

**GraphProjector 的改动**：`project_object` 当前只读 `ot.properties` 的 indexed 字段。需要扩展：
- 检测 `object_state.get("_virtual")` 为 True 时，额外写入 `_virtual:true` + `_source_ref` + PK 业务值到节点属性
- title 字段：ObjectType 元数据需要能标识哪个 property 是 title（当前是否有 `is_title`/`title_property` 字段？需确认；若无，先用 PK 列兜底，title 字段作为后续增强）

**数据源约束**（P3）：只依赖 `TrinoQueryEngine.query` + `ObjectQueryService._virtual_table_ref`，**不要**给 ProjectSyncService 新引入直连外部源的 JDBC 依赖。保持"Trino 是唯一联邦查询入口"的架构约束。

**分批**：大表全量扫描有压力。参考 Palantir 方案的批量提交优化（对照分析 §5），`project_for_virtual_object_type` 内部分批拉取 + 分批 MERGE（如 1000 行/批），用 Trino 的 `LIMIT/OFFSET` 或游标分页。不要一次性 `SELECT *` 全量进内存。

### 4.3 FK → 边投影（最大设计挑战）

**问题**：VIRTUAL 对象的边从哪来？不能来自 Action（红线 9），必须从外部源 FK 推导。

**现状核对**：
- `LinkTypeModel.foreign_key_property_api_name`（String 255，可空）：存外部源 FK 列名
- `LinkTypeModel.cardinality`（String 10，ONE/MANY）：关系基数
- `LinkType.source_object_type_id` / `target_object_type_id`：两端 ObjectType

**边投影逻辑**（在 `project_for_virtual_object_type` 内，节点投影后）：
1. 查该 VIRTUAL ObjectType 作为 source 或 target 的所有 LinkType
2. 对每条 LinkType：
   - 若 LinkType 的**两端都是 VIRTUAL**：跳过（两边都要查 Trino，复杂度高，MVP 不做，留二期）
   - 若 LinkType 的**一端 MANAGED、一端 VIRTUAL**：
     - 从 Trino 拉 FK 关系：`SELECT {fk_col}, {pk_col} FROM {virtual_table_ref}`
     - 对每行：MANAGED 端的 rid 从 PG object_state 按 PK 查（`get_object_state` 或批量）；VIRTUAL 端的 rid 用 `generate_virtual_rid` 合成
     - 调 `GraphProjector.project_link(source_rid, target_rid, rel_type)`
3. 边的 `rel_type` 用 `naming.graph_relationship_type(ontology, link)` 生成（与 MANAGED 边一致）

**关键约束**：
- **LinkType.foreign_key_property_api_name 必须已填**。如果用户建 LinkType 时没绑定外部源 FK 列，边投影无法进行——这是 `register_virtual_table` 时的校验点（见 §4.4）。
- **MANAGED 端的 rid 解析**：MANAGED 对象的 rid 是 `object_state.rid`（系统分配的 UUID rid），不是 PK。从外部源 FK 拿到的是 MANAGED 对象的 PK 值，要查 PG object_state 按 PK 反查 rid。注意 object_state 的 PK 存在 `properties` JSONB 里（按 `backing_column` key），不是独立列——查时要按 properties 里的 PK 字段过滤。
- **批量优化**：不要逐行查 object_state。先从 Trino 拉全部 FK 对，收集所有 MANAGED PK 值，批量 `get_object_states_by_pks`（若此方法不存在，需在 PostgresMetaStore 补一个按 properties PK 批量查的方法），再批量 project_link。

**待确认的设计点**（实现前与团队对齐）：
1. ObjectType 是否有 `title_property` / `is_title` 标识？若无，title 列怎么确定（PK 兜底？ObjectType 元数据加字段？）
2. `LinkType.foreign_key_property_api_name` 当前是否在 `register_virtual_table` 时强制要求填写？若否，是否要在 VIRTUAL 场景下强制？
3. MANAGED 端 PK → rid 的批量反查，PostgresMetaStore 是否已有合适方法？

### 4.4 触发链路

**自动触发**：`register_virtual_table` 成功后，调 `project_for_virtual_object_type`。
- 当前 `register_virtual_table` 在哪里？（DataSourceService，需确认）
- 触发是同步还是异步？建议**异步**（投影可能慢，不该阻塞 register 返回）。可复用现有 outbox 机制投一个新 effect 类型 `VIRTUAL_PROJECT`，或简单 `asyncio.create_task`（best-effort，失败记日志不阻塞）。

**手动触发**：admin API `POST /admin/project/rebuild-for-virtual/{ont}/{ot}`。
- 复用现有 admin 路由模式（参考 `rebuild_for_object_type` 的路由）
- 幂等：重复调用 = 重新拉 Trino + MERGE（Neo4j MERGE 天然幂等）

**定时刷新**（可选，二期）：分钟级定时任务重新投影 indexed 属性。MVP 不做，先靠手动 rebuild。

### 4.5 权限治理补齐（🟡 待补）

**现状**：`DataFrameQueryService` 不注入 `AuthorizationService`（三场景附录 A 已确认）。三个方案都有这个断层。

**折中方案的最小补齐**：
1. **ObjectType 级权限**：图遍历前校验用户对目标 ObjectType 的访问权限（`AuthorizationService.check_object_type_access`）
2. **行级权限**：VIRTUAL 水合时，行级权限（Cedar TPE → SqlGlot WHERE 注入）作用在 Trino 查询上。`ObjectQueryService.hydrate_by_pk` 是否已注入权限？需确认；若否，这是独立的权限治理工作（ADR-016 Phase 6+），不阻塞折中方案 MVP

**MVP 边界**：折中方案的 MVP 可以先不补行级权限（标注为已知限制），但 ObjectType 级权限建议同步补——否则图遍历会泄露用户无权访问的 ObjectType 的存在性。

**判据**：权限治理是横切关注点，不该让折中方案的 PR 变成"顺便重写权限层"。 ObjectType 级权限作为 P0 随折中方案补；行级权限作为 P1 独立 PR。

### 4.6 partial / omitted 降级

**场景**：外部源不可用（MySQL 挂了），图遍历返回了 VIRTUAL rid 但水合失败。

**处理**（对齐 ADR-020 best-effort 模式 + C9 包容式防线）：
- `_hydrate_virtual` 当前是 `except Exception: continue`（已实现，静默跳过）。改为返回 `_partial:true` 标记：
  ```python
  {"rid": rid, "api_name": ot, "props": {}, "_partial": True, "_error": "source unavailable"}
  ```
- 不让整个查询失败（C9：包容式，不拒绝用户）
- 前端/Agent 看到 `_partial:true` 时显示"部分数据不可用"

**投影侧也要降级**：`project_for_virtual_object_type` 时 Trino 查询失败，记日志 + 返回 partial 结果（已投影的节点保留，未投影的标 omitted），不抛异常阻塞 `register_virtual_table`。

### 4.7 ConflictDetector 排除 VIRTUAL

**现状**：`ConflictDetector` 审计 PG object_state vs Doris（INDEX outbox 漏写检测）。

**改动**：VIRTUAL 节点不参与对账（P：不可对账）。在 ConflictDetector 的审计循环里，跳过 `storage_type == VIRTUAL` 的 ObjectType。理由：外部源无 data_version，对账无基准；VIRTUAL 节点的"重建"= 重新查 Trino，不是对账。

---

## 五、取舍记录（为什么这样选，为什么不那样选）

### 5.1 为什么投影身份骨架，而不是纯查询时联邦（路径 ③'）？

| 维度 | 折中方案（骨架投影） | 路径 ③'（查询时联邦） |
|------|---------------------|---------------------|
| 拓扑延迟 | 🟡 分钟级（投影态） | ✅ 实时（查时 JOIN） |
| 工程成本 | 🟡 中（投影 + 水合分流） | ❌ 大（自研查询翻译器，参考 Ontop IQ） |
| MVP 适配 | ✅ 改动小，复用现有 GraphProjector | ❌ 要重写查询引擎 |

**决策**：MVP 走折中方案。AG-UI Agent 图探索是 ReAct 多步，每步秒级延迟可接受，查询时联邦不是 MVP 刚需（可行性调研 §5.2）。折中方案为 ③' 留 `_virtual` + `_source_ref` 接口，未来可演进。

### 5.2 为什么不用 Neo4j Virtual Graph？

核实结论（可行性调研 §3.3）：Virtual Graph 只在 Aura 云提供，**社区版和企业版自托管都不包含**，且仍是 public preview。Gaia 用 `neo4j:5-community` 自托管（私有化部署原则），不可用。要实现"查询时联邦"体验必须自研（路径 ③'）。

### 5.3 为什么不集成 Ontop？

三场景 §1.5 已详述。核心障碍：
- 范式阻抗：Ontop 是 RDF/SPARQL，Gaia 是属性图/Cypher。要调 Ontop 得把 Cypher 图遍历翻译成 SPARQL BGP——这是 Ontop 不提供的新工程（IR→SPARQL 翻译器）
- MANAGED+VIRTUAL 混合查询的双引擎问题（Neo4j 存 MANAGED，Ontop 存 VIRTUAL，一次查询跨两引擎）
- 权限注入断层：Ontop 生成的 SQL 无法注入 Gaia 的 Cedar TPE 权限 residual predicate
- Java sidecar 进 Python 栈的运维负担

Ontop 的价值在设计思想参考（mapping 声明式映射 + IQ 统一中间表示），不是直接集成。路径 ③' 自研时可借鉴。

### 5.4 为什么不用纯 Palantir 方案（Neo4j 作主存）？

三场景 §2.4 已详述。违背 5 条架构红线：
- ADR-001（Doris 是在线读主源，方案让 Neo4j 也存全量属性 = 双主源）
- C1（图节点轻量，方案塞 6 系统元属性 + 4 安全标记 + 全量业务属性）
- C8（派生副本，方案让 Neo4j 成主存失去可重建能力）
- C3（VIRTUAL 不落地，方案 ETL 进 Neo4j = 落地）
- 红线 9（VIRTUAL 禁止写入，ETL 导入是写入）

且一致性灾难（无 CDC 通道，对账失效）。Palantir 方案的价值在局部技术借鉴（基数约束应用层预查询、Marking lattice、批量提交优化），不是架构采纳。

### 5.5 为什么 VIRTUAL 节点不参与对账？

外部源无 `data_version`，对账无基准。C8 的"投影表保留 update_time + data_version 定时对账"对 VIRTUAL 失效。VIRTUAL 节点的"一致性保障"= 重新查 Trino 重建，不是对账。强行对账会产生误报（外部源每次查都可能变，对账会一直报"不一致"）。

### 5.6 为什么 indexed 属性新鲜度可接受分钟级延迟？

图推理的交互模型是 AG-UI Agent ReAct 多步探索，每步秒级延迟。indexed 属性用于图遍历剪枝（`WHERE o.status="PAID"`），分钟级延迟意味着可能漏掉"上一秒刚变成 PAID 的订单"。但：
- 图遍历的目的是"定位"，不是"精确计数"。漏掉秒级新增可接受
- 全量属性水合走 Trino 联邦（实时），用户看详情时数据是最新的
- 需要实时剪枝的场景，路径 ③' 留了接口（检测 `_virtual` 回查 Trino）

**缓解策略**（二期）：定时刷新 indexed 属性（分钟级）；或图遍历不剪枝 VIRTUAL 属性，先拿全 rid 再 Trino 查最新属性过滤（牺牲剪枝换新鲜度）。

---

## 六、边界与风险

### 6.1 已知限制（文档要记，不掩盖）

| 限制 | 影响 | 缓解 |
|------|------|------|
| PK 含特殊字符时 rid 解析失真 | `parse_virtual_rid_pk` 返回的 pk 与原始不一致，水合查不到 | MVP 假设 PK 字母数字；含特殊字符时调用方先 base64 编码 |
| VIRTUAL rid 不稳定 | 外部源 PK 改了 rid 就变，不能跨查询缓存 rid→属性 | 每次查询重新水合，不缓存 |
| indexed 属性分钟级延迟 | 图遍历剪枝可能漏掉秒级新增 | 全量属性实时（水合走 Trino）；二期定时刷新 |
| 两端都 VIRTUAL 的 LinkType 边 | MVP 不投影 | 二期补（两边都查 Trino） |
| 行级权限未注入水合 | VIRTUAL 水合可能泄露无权访问的行 | P1 独立 PR 补（ADR-016 Phase 6+） |
| 大表全量投影压力 | `project_for_virtual_object_type` 拉全表可能慢/占内存 | 分批（1000/批）+ Trino 分页 |

### 6.2 风险与对策

| 风险 | 概率 | 对策 |
|------|------|------|
| FK→LinkType 映射缺失（用户建 LinkType 没填 `foreign_key_property_api_name`） | 高 | `register_virtual_table` 时校验 + 文档引导；缺失时边不投影但节点仍投影（降级） |
| MANAGED 端 PK→rid 批量反查性能 | 中 | 补 `PostgresMetaStore.get_object_states_by_pks`（按 properties JSONB PK 批量查） |
| Trino 联邦查询外部源慢 | 中 | 水合分批 + 超时 + partial 降级 |
| Neo4j 节点膨胀（VIRTUAL 表大） | 中 | 骨架最小化（P2）+ 分批 MERGE + 监控节点数 |
| 投影与查询并发竞争 | 低 | Neo4j MERGE 幂等；投影是 best-effort，不阻塞查询 |

### 6.3 反模式（明确禁止）

- ❌ 把 VIRTUAL 全量属性投影进 Neo4j（违反 P1/P2，滑向纯 Palantir 方案）
- ❌ 让 VIRTUAL 节点参与 ConflictDetector 对账（违反 P，外部源无版本号）
- ❌ 给 ProjectSyncService 引入直连外部源 JDBC 依赖（违反 P3，破坏 Trino 唯一联邦入口）
- ❌ 改 Gate 1 让 `project_for_object_type` 放行 VIRTUAL（违反 P3，污染 MANAGED 路径）
- ❌ 把安全标记/SCD2/系统元属性塞进 Neo4j 节点（Palantir 方案反模式，对照分析 §1.2/§2.2/§4）
- ❌ 把 ActionType 投影成 Neo4j 节点（元数据非业务数据，对照分析 §6）

---

## 七、文档同步（动代码前/后必做）

按 CLAUDE.md「设计意图变更先记 ADR/设计文档」：

### 7.1 `graph-reasoning-design.md` 修订

1. **§6.1**：新增"触发模式 D：VIRTUAL 联邦投影"行（见本文 §3.2 表格）
2. **§6.3**：扩展"同源分发"说明——MANAGED 源是 Doris/Iceberg/PG object_state，VIRTUAL 源是外部源（经 Trino 联邦），两者都投影到 Neo4j 作派生索引
3. **§6.4**：新增"VIRTUAL 节点一致性语义"——best-effort + 不可对账，不参与 ConflictDetector
4. 新增 **§6.5「VIRTUAL 联邦投影设计」**：指向本 handoff 文档作为实现权威

### 7.2 ADR

- **ADR-015 补充**或**新 ADR-021**：记录"VIRTUAL 对象图投影"决策，引用三份调研文档 + 本 handoff 作为依据。决策内容：
  - 采用折中方案（身份骨架投影 + Trino 联邦水合）
  - 否决纯 Ontop / 纯 Palantir / Neo4j Virtual Graph 的理由
  - rid 合成规则（复用 `core/rid.py`）
  - 为路径 ③' 留接口（`_virtual` + `_source_ref`）

### 7.3 CLAUDE.md

- 红线 9（VIRTUAL 禁止写入）补充说明：**图投影例外**——VIRTUAL 身份骨架投影进 Neo4j 不是"业务写入"，是派生索引构建，不违反红线 9 的精神（红线 9 禁止的是 Action 对 VIRTUAL 的业务 mutation，不是只读投影）
- 若引入新红线（如"VIRTUAL 节点不参与对账"），同步更新红线列表

---

## 八、PR 拆解建议（实现顺序）

按依赖关系排序，每个 PR 独立可验证：

### PR 1：`project_for_virtual_object_type` 节点投影（MVP 核心）
- 新增 `ProjectSyncService.project_for_virtual_object_type`
- 扩展 `GraphProjector.project_object` 识别 `_virtual` / `_source_ref` 元标记
- 确认/补 ObjectType 的 title 字段标识
- 分批拉取 + MERGE
- 单测：mock Trino 返回，验证 Neo4j 节点写入正确（rid/label/indexed/_virtual/_source_ref）
- 集成测试：testcontainers Trino + Neo4j，端到端投影

### PR 2：FK → 边投影
- `project_for_virtual_object_type` 内补边投影逻辑
- 补 `PostgresMetaStore.get_object_states_by_pks`（若不存在）
- 单测：一端 MANAGED 一端 VIRTUAL 的 LinkType 边投影
- 集成测试：混合链路 searchAround 返回非空

### PR 3：触发链路 + admin API
- `register_virtual_table` 后异步触发投影
- `POST /admin/project/rebuild-for-virtual/{ont}/{ot}` 路由
- partial 降级（Trino 失败不阻塞）

### PR 4：一致性 + 治理
- `ConflictDetector` 排除 VIRTUAL ObjectType
- `_hydrate_virtual` 返回 `_partial` 标记（替代静默跳过）
- ObjectType 级权限注入 `DataFrameQueryService`（P0）

### PR 5（二期）：定时刷新 + 双 VIRTUAL 边 + 行级权限
- indexed 属性定时刷新任务
- 两端都 VIRTUAL 的 LinkType 边投影
- VIRTUAL 水合行级权限（Cedar TPE → Trino WHERE）

---

## 九、验收清单（提交前对照）

### 功能
- [ ] VIRTUAL ObjectType 投影后 Neo4j 有节点（G1）
- [ ] 跨 VIRTUAL 的 searchAround 返回非空（G2）
- [ ] VIRTUAL rid 水合返回全量属性（G3，已落地，回归验证）
- [ ] indexed 属性剪枝生效（G4）
- [ ] 外部源不可用时返回 `_partial`（G5）

### 架构合规
- [ ] Neo4j 节点只存骨架字段（P2 清单），无全量属性/系统元属性/标记/SCD2
- [ ] `project_for_virtual_object_type` 旁路 Gate 1，未改 `project_for_object_type`（P3）
- [ ] 数据源只依赖 TrinoQueryEngine，无直连外部源 JDBC（P3）
- [ ] VIRTUAL 全量属性走 Trino 联邦，未缓存进 Neo4j（P4）
- [ ] 边来源是 FK 推导，非 Action（P5）
- [ ] VIRTUAL 节点带 `_virtual` + `_source_ref`（P6，为 ③' 留接口）
- [ ] ConflictDetector 排除 VIRTUAL（§4.7）

### 文档
- [ ] `graph-reasoning-design.md` §6 修订完成（§7.1）
- [ ] ADR-015 补充或 ADR-021 起草（§7.2）
- [ ] CLAUDE.md 红线 9 补充图投影例外说明（§7.3）

### 测试
- [ ] 单测覆盖：节点投影 / 边投影 / rid 解析 / partial 降级 / Gate 1 旁路
- [ ] 集成测试：testcontainers Trino + Neo4j 端到端
- [ ] 异常路径：Trino 不可用 / FK 缺失 / PK 含特殊字符 / 大表分批

---

## 附录 A：代码事实核对（起草时已验证）

| 事实 | 位置 | 状态 |
|------|------|------|
| `generate_virtual_rid` / `parse_virtual_rid_pk` / `is_virtual_rid` | `src/ontology/core/rid.py` | ✅ 已实现 |
| `_hydrate` 按 rid type 分流（MANAGED→PG / VIRTUAL→Trino） | `src/ontology/services/object_set_executor.py:1416` | ✅ 已实现 |
| `_hydrate_virtual` 调 `hydrate_by_pk`（Trino 联邦） | `object_set_executor.py:1490` | ✅ 已实现（静默跳过，待改 partial） |
| `LinkTypeModel.foreign_key_property_api_name` + `cardinality` | `src/ontology/core/models/ontology.py:169-170` | ✅ 字段已存在 |
| `ObjectQueryService.hydrate_by_pk` + `_virtual_table_ref` | `src/ontology/services/object_query_service.py:381,608` | ✅ 已实现 |
| `ProjectSyncService` Gate 1 skip VIRTUAL | `src/ontology/services/project_sync_service.py:108-111` | ✅ 现状（折中方案旁路，不改） |
| `GraphProjector.project_object` 仅投影 indexed | `src/ontology/services/graph_projector.py:48-79` | ✅ 现状（待扩展 `_virtual`/`_source_ref`） |
| `DataFrameQueryService` 不注入 AuthorizationService | `object_set_executor.py` 全文 | ✅ 现状（断层，待补） |
| `ConflictDetector` 审计 Doris，未审计 Neo4j | `src/ontology/services/conflict_detector.py` | ✅ 现状（待排除 VIRTUAL） |
| `naming.graph_label` / `graph_relationship_type` | `src/ontology/core/naming.py:228,251` | ✅ 已实现（VIRTUAL 复用） |

## 附录 B：术语对照

| 术语 | 含义 | 出处 |
|------|------|------|
| 身份骨架 | rid + label + PK + title + indexed + `_virtual` + `_source_ref` | 本文 P2 |
| 模式 C | 子集复制（主源 + 派生索引） | Neo4j polyglot persistence |
| 触发模式 D | VIRTUAL 联邦投影（Trino→Neo4j 骨架） | 本文 §3.2 |
| best-effort + 不可对账 | VIRTUAL 节点一致性语义 | 本文 §3.3 |
| 路径 ③' | 自研查询时联邦（远期） | 可行性调研 §5.1 |

## 附录 C：待确认点闭合状态（2026-07-16 更新）

本文档起草时标记的"待确认设计点"已全部闭合，决策落地于 ADR-021 + virtual-graph-projection-design.md。实现者无需再确认：

| 本文档待确认点 | 闭合状态 | 闭合依据 | 新文档位置 |
|------------------|:---:|---------|-----------|
| §4.2 ObjectType 是否有 title_property 标识 | ✅ 已有 | `ObjectTypeModel.title_property`（String 255，非空）+ `PropertyDefModel.is_title_property`（Boolean）| design §2.2 + 附录 B |
| §4.3 FK 属性归属 source/target 哪一端 | ✅ 文档明确 | `ontology-tool-layer.md`："存储在源或目标端属性上"，两端容错查找（source 优先 target 兜底）| design §2.3 难点 1 |
| §4.3 `foreign_key_property_api_name` 是否强制要求 | ✅ 不强制，缺失降级 | FK 缺失时边不投影，节点仍投影 | design §2.3 |
| §4.3 MANAGED 端 PK→rid 批量反查方法 | ✅ 不存在，需新增 | `PostgresMetaStore.get_object_states_by_pks` | design §2.6 |
| §4.3 两端 VIRTUAL 边是否二期 | ✅ 提至 MVP | 成本不更高（少一步 PG 反查）| design §4 难点 5 |
| §4.4 触发链路同步/异步/outbox | ✅ asyncio.create_task | outbox 是 Action 语义，VIRTUAL 投影不是 Action | ADR-021 D9 + design §3.1 |
| §4.4 孤儿清理（本文遗漏） | ✅ 新增 watermark+cleanup | cartography 范式，`_sync_tag` 标记 | design §2.4 难点 2 |
| §4.5 AuthorizationService 是否已实现 | ✅ 已实现 | `authorization_service.py`，方法 `check_access`/`check_access_batch` | design 附录 B |
| §4.5 ObjectType 级权限 P0 归属 | ✅ 独立 PR 0 前置 | 横切关注点，与 VIRTUAL 投影正交 | design §5 PR 0 |
| §4.2/§2.5 分页方式（OFFSET vs 游标） | ✅ 游标分页 | OFFSET 深翻性能差 | design §2.1 难点 3 |
| §2.5 批量写入（逐条 MERGE） | ✅ CIT+UNWIND | `CALL {} IN TRANSACTIONS`（Neo4j 5 原生）| design §2.5 难点 6 |
| §7.2 ADR-015 补充 vs 新建 | ✅ 新建 ADR-021 | 独立架构决策 | ADR-021 |
