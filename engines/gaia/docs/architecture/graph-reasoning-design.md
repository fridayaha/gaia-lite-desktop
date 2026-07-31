# 图关联推理与时空多维分析 —— 特性设计文档

> **状态**：设计定稿（评审中）
> **版本**：v2.4（2026-07-17 四轮订正：**引入 ObjectIndexFunnel（对齐 Palantir Object Data Funnel）统一索引编排**——ProjectSyncService 升级重命名，统一负责 rid 分配/复用 + 四引擎（Doris/Neo4j/PostGIS/TimescaleDB）扇出写入，保证 rid 跨引擎一致 / rid 权威改为 Doris idx 表（object database），不单独建映射表，靠 PK UNIQUE KEY + upsert 复用 / Iceberg 保持纯净无 rid（backing dataset）/ 废弃 SeaTunnel 直写 Doris/超表路径（SeaTunnel 回归纯搬运） / TimescaleDB 超表加 rid 列，时序写入经 ObjectIndexFunnel（原“不经 Iceberg”改为 Kafka→Iceberg append→Funnel） / 时序明细不进 Doris（Doris 只存 series_id 引用） / object_state 降级为 Action 写入暂存态（非 rid 权威）/ rebuild 从 Doris 读 / §6 整章重写为 ObjectIndexFunnel 编排）
> **代号线索**：Gotham 基因落地 —— 平台标志性决策分析能力
> **关联文档**：
> - [reference-graph-reasoning.md](../reference-graph-reasoning.md)（参考资料汇编，Palantir 范式 + 业界调研，本特性的概念源头）
> - [implementation-status.md](./implementation-status.md)（实现状态路标，本特性新增章节 §十二）
> - [adr-012-textql-ontology-driven-nl-query.md](./adr-012-textql-ontology-driven-nl-query.md)（TextQL，本特性扩展其 ObjectSet IR 产出）
> - [adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md)（本体工具层，本特性扩展工具族）
> - [adr-015-agent-driven-graph-explore.md](./adr-015-agent-driven-graph-explore.md)（NL→IR 由 AG-UI ReAct Agent 完成，已废弃 object_set_parser/explore-plan）
> - [adr-021-virtual-graph-projection.md](./adr-021-virtual-graph-projection.md)（VIRTUAL 对象图投影，模式 D）
> - [architecture_plan.md](./architecture_plan.md)（5+1 分层架构，本特性新增 Graph / GeoTime 两个 Layer）
>
> **⚠️ ADR 编号说明**：早期设计稿曾预留 `adr-015-graph-engine-neo4j` / `adr-016-geotime` / `adr-017-ibis` 三个待补 ADR，但后续 ADR 编号被前端探索（ADR-015）与权限治理（ADR-016/017）占用。本特性的核心决策（图引擎选型 / 时空层 / ObjectSet IR 执行载体）目前以本设计文档为权威源，尚未独立立项 ADR；VIRTUAL 投影部分已立项 ADR-021。
>
> **设计原则对齐**：CLAUDE.md 第一原则"把复杂留给自己，把简单留给用户"——多引擎流转的全部复杂度收在编排层，用户只感知业务语义。

---

## 〇、设计契约（动笔前对齐结论）

本特性在设计启动前完成多轮约束对齐，以下为已拍板的核心契约：

| # | 契约 | 决策来源 |
|---|------|----------|
| C1 | 图引擎：本期 Neo4j，不抽象 GraphStore/GraphDialect，但留四条迁移口子（rid 稳定主键 / Cypher 收口 Neo4jGraphStore / 强 schema / 边轻量） | 用户决策 + 附录 A 调研 |
| C2 | 时空存储：PostGIS + TimescaleDB 合并为 `GeoTimeStore`（同 PG 实例，用 `ngosang/timescaledb-postgis` 镜像），静态空间属性 + 动态 GTS 时空序列二分 | 用户材料 + 附录 E 调研 |
| C3 | 动态时序数据走流式独立链路（Kafka→Iceberg append→ObjectIndexFunnel→TimescaleDB 超表），不经 Action / 不经 object_state；时序超表带 rid 列与四引擎一致 | 用户强调（2026-07-17 订正：原“SeaTunnel 直写超表”改为经 ObjectIndexFunnel 统一 rid） |
| C4 | 本体元数据驱动存储分发：Property 的 DataType（GEOPOINT/GEOSHAPE/GEOTEMPORAL_SERIES/TIME_SERIES）决定路由，`indexed` 复用决定索引 | 用户材料（Palantir 范式）+ 附录 D |
| C5 | 查询抽象：`query_with_dataframe`（推理线）的引擎分工为 图遍历→Neo4j / 空间→PostGIS / 时序→TimescaleDB / **属性过滤→Trino→Doris idx 表（与水合同源口径）**；与 `query_with_sql`（SQL 线）独立并行，水合点交汇 | 用户决策 + 附录 D 调研；**早期调研拟用 Ibis，但实测 Ibis PostGIS backend 有 bug（ibis#1786/#12007），改用原生 SQL；属性过滤与水合同走 Trino→Doris，不走 PG object_state（object_state 是 Action 写入路径暂存态，与查询无关）** |
| C6 | LLM/Agent 产 ObjectSet IR（pydantic JSON，白名单护栏）→ DataFrameQueryService 翻译为原生 SQL 片段执行；两层 IR 分离（传输层 ObjectSet IR / 执行层原生 SQL） | 用户材料 + 附录 F |
| C7 | ObjectSet IR 用 Palantir 真实结构（searchAround 顶层 type，非 transform），已实现 13/15 type（objectType/static/filter/searchAround/union/intersect/subtract/aggregate/select/interfaceBase/interfaceLinkSearchAround + 占位 withProperties/reference），nearestNeighbors/asType/methodInput 列二期 | 附录 C 调研（Palantir SDK，2026-07-04 更新对齐 87%） |
| C8 | 一致性模型：best-effort 最终一致（秒级），删 `sync=true` 承诺；不对账（派生索引层，由同步任务保障最终一致） | 用户决策（2026-07-16 订正） |
| C9 | 防线包容式：不拒绝用户，超限转分页/续算/排队；阈值对齐 Palantir 实测值（3 跳 / 100 万 / 1 万水合）；多跳用原生 Cypher 不用 APOC path.expand | 用户纠正 + 附录 C 调研 |
| C10 | 本体模型克制度：Alert/RiskScore/Function 不造新模型，用 ObjectType+Property+Action 表达 | 用户确认 |
| C11 | 本期范围：`query_with_dataframe` + ObjectSet IR + 图遍历(searchAround) + 时空联动 + 证据链快照 + 风险评分(轻量)；前端 Vertex 式探索 / 全链路血缘审计 / 实体对齐 / Function 抽象 / union/KNN 均留二期 | 用户确认 |
| C12 | 两条线在水合点交汇：推理线最后用 ObjectQueryService 走 Trino→Doris（MANAGED）/ Trino 联邦外部源（VIRTUAL）取全量属性；属性过滤同源走 Trino→Doris，图遍历/空间/时序分别走 Neo4j/PostGIS/TimescaleDB | 用户确认（2026-07-17 订正：属性过滤也走 Trino→Doris，非 PG object_state） |
| C13 | 容错：MVP 整体失败 + 保留证据（不降级），降级列二期 | 用户确认 |
| C14 | docker-compose：Neo4j 独立服务按需启停，PostGIS+TimescaleDB 复用现有 PG 实例（换一体镜像） | 用户确认 |

---

## 一、价值定位与特性蓝图

### 1.1 这是什么

Gaia 的图关联推理与时空多维分析特性，是平台的**标志性决策分析能力**，对标 Palantir Gotham/Foundry 的核心壁垒。本质是：

> **以本体为语义中枢，将多源异构数据转化为可推理、可溯源、可执行的数字孪生网络，在亿级实体规模下支持自由探索式分析，同时满足强合规场景的全链路审计要求。**

这不是孤立的"图查询模块"，而是把 Gaia 现有的本体（语义大脑）+ Action（安全手脚）+ 多源融合（数据连接器）三个已建成能力，用图引擎 + 时空引擎 + 编排中枢连接起来，形成"看见关系 → 推理路径 → 评估风险 → 采取行动 → 留存证据"的完整决策闭环。

### 1.2 为什么是标志性能力（区别于普通图数据库/BI）

| 普通图数据库 | Gaia 本特性 |
|---|---|
| 图谱与业务数据脱节，分析结果是孤立节点 | **分析结果绑定本体实体**，可一键溯源原始数据 |
| 单一图存储，时序/空间分析短板 | **多模型混合**：图(Neo4j) + 全量(Doris) + 空间(PostGIS) + 时序(TimescaleDB) |
| 需手写图查询语言 | **本体驱动 + 自然语言**：用户描述意图，LLM 产 ObjectSet IR，系统自动跨引擎 |
| 分析与行动割裂 | **分析与 Action 闭环**：图分析发现风险 → 触发告警对象 → 绑定处置 Action |
| 无合规审计 | **证据链快照**：每次分析留存 ObjectSet IR + 命中 rid（血缘指针后续实现），可基本追溯 |

### 1.3 特性蓝图

```
┌─────────────────────────────────────────────────────────────────┐
│  用户触点（全部屏蔽多引擎）                                       │
│  自然语言(TextQL扩展)  │  图探索UI(Vertex式,二期)  │  API/Agent工具 │
├─────────────────────────────────────────────────────────────────┤
│  两条独立查询线                                                  │
│  ┌─ SQL 线: query_with_sql ── NL→SQL→Doris(主)/Trino(降级)     │
│  └─ 推理线: query_with_dataframe ── ObjectSet IR→原生SQL+Neo4j │
│     (图遍历→Neo4j; 空间→PostGIS; 时序→TimescaleDB; 属性过滤+水合→Trino→Doris) │
├─────────────────────────────────────────────────────────────────┤
│  编排中枢（推理线核心）                                           │
│  DataFrameQueryService ── IR 翻译 + 多引擎编排 + 防线 + 证据链       │
├──────────────┬──────────────┬──────────────┬────────────────────┤
│  Graph Layer │  Index Layer │ GeoTime Layer│  现有层不变          │
│  (新,Neo4j)  │  (现有Doris) │ (新,PG扩展)  │  Catalog/Dataset/   │
│  图遍历/路径  │  全量属性+水合│ PostGIS+TSDB │  Pipeline/Engine/   │
│              │              │ 空间+时序     │  Metadata           │
└──────────────┴──────────────┴──────────────┴────────────────────┘
数据写入：SeaTunnel→Iceberg（backing dataset）+ ObjectIndexFunnel（rid 分配+四引擎扇出）+ Action→object_state→Funnel
```

### 1.4 目标场景（通用支持）

通用支持情报/金融风控/供应链/军工四类场景。各场景能力侧重：

| 场景 | 核心能力侧重 | 典型查询 |
|---|---|---|
| 情报/执法 | 开放式线索探索、隐藏链路 | "从已知嫌疑人出发，3跳内关联的所有通讯/资金/同行记录" |
| 金融风控 | 隐藏链路、UBO、壳公司网络 | "两账户无直接转账但同向汇款至同一空壳公司" |
| 供应链 | 中断传导、替代方案、时空联动 | "供应商停产，3跳内受影响订单 + 300km内替代供应商" |
| 军工/态势 | 轨迹追踪、时空共现、区域碰撞 | "48h内进入敏感区域的装备及其关联人员" |

---

## 二、架构总览

### 2.1 两条独立查询线（核心架构决策，C5+C12）

本特性的关键架构决策是**两条查询线独立并行，仅在水合点交汇**：

| 线 | 工具 | 载体 | 引擎 | 职责 |
|---|---|---|---|---|
| **SQL 线** | `query_with_sql`（现有） | SQL | Doris（主）/ Trino（降级） | 属性过滤/聚合/单表多表查询 |
| **推理线** | `query_with_dataframe`（新） | DataFrame (原生 SQL) | Neo4j(图) + PostGIS(空间) + TimescaleDB(时序)；属性过滤+水合走 Trino→Doris | 图遍历/路径/空间过滤/时序联动 |

**职责隔离红线**：
- 推理线执行层的引擎分工：**图遍历→Neo4j、空间过滤→PostGIS、时序过滤→TimescaleDB、属性过滤→Trino（背后 Doris idx 表）**。属性过滤与水合同源同口径走 Trino→Doris，与 SQL 线 `query_with_sql` 一致，保证推理线拿到的属性是经过治理的权威数据
- Doris 不被推理线绕开：属性过滤和水合都经 Trino 走 Doris idx 表（MANAGED）/ 联邦外部源（VIRTUAL）。所谓"推理线不碰 Doris"是早期 Ibis 方案的过时表述，已废弃
- 两条线不交织：推理线不做图遍历以外的 SQL 聚合，SQL 线不掺和图遍历

**水合衔接（C12）**：推理线执行完（图遍历+空间+时序+属性过滤后得到 rid 集），调用现有 `ObjectQueryService` 加载全量属性返回用户。这是两条线唯一交汇点。

> **水合架构说明（设计契约，非待办）**：水合**必须通过 Trino 查询**——MANAGED 对象走 Trino→Doris idx 表（ADR-001 在线读主源），VIRTUAL 对象走 Trino 联邦查外部源表（ADR-014）。与 `query_with_sql` 同源同口径，保证推理线拿到的"全量属性"是经过治理的权威数据。
>
> - **MANAGED 对象**：Trino 查 Doris idx 表，按 rid / 主键批量取全量属性。**不走 PG `object_state`**——`object_state` 是 Action 写入路径的暂存态（记录 Action 刚写入、Doris 未同步的瞬间状态），与查询路径完全无关，不是全量属性源。把 `object_state` 当查询数据源是设计错误。
> - **VIRTUAL 对象**：不落地，水合分流到 Trino 联邦查外部源表（ADR-014），零拷贝。
> - **属性过滤同源**：推理线的属性 filter 同样下推 Trino→Doris（不是 PG object_state JSONB），与水合同一数据口径。详见 §7.4。
> - **批量水合**：参考 Palantir `loadObjects`（static objectSet 一次 POST 取一批 rid）与 OBDA 批量取数范式，**禁止逐个查询**（详见 §7.7）。
>
> **阻塞项（独立架构任务，推理线上线前必须完成）**：
> 1. ✅ **Doris idx 表加 rid 列**（rid 作为普通 STRING 列 + 倒排索引，PK 仍是 UNIQUE KEY）。rid 是索引层字段非数据源字段，对齐 Palantir object database（§6.3）。**已落地**（handoff-rid-funnel-closure.md T1.1，2026-07-27）
> 2. ✅ **ObjectIndexFunnel 改造**（§6.1）：ProjectSyncService 升级重命名完成（PR-2），加 DorisIndexStore 注入 + rid 分配/复用（按 PK 查 Doris）+ 四引擎扇出。废弃 SeaTunnel Iceberg→Doris backfill 直写路径（T1.4/T1.10/PR-2 已落地）
> 3. ✅ **ObjectQueryService 补 `hydrate_by_rids`**：按 rid 批量查 Doris idx 表取全量属性（MANAGED）/ 解析 rid 走 Trino 联邦（VIRTUAL），不走 object_state。**已落地**（handoff T1.6，MANAGED 完成；VIRTUAL 留二期）
> 4. ⏳ **TimescaleDB 超表加 rid 列**（§6.5）：时序写入带 rid，推理线时空联动直接按 rid 查超表。**未落地**（时序链路整体推后，见 §13.1 边界）
>
> **rid 一致性方案（对齐 Palantir）**：rid 权威是 Doris idx 表（object database），不单独建映射表。ObjectIndexFunnel 按 PK 查 Doris 已有 rid 复用、无则新分配，再用同一 rid 扇出写 Neo4j/PostGIS/TimescaleDB。Iceberg（backing dataset）保持纯净无 rid。详见 §6.3。

### 2.2 分层架构演进（5+1 → 5+3）

现有 5 Layer（Catalog/Metadata/Dataset/Index/Pipeline）+ Engine。本特性新增 2 个 Layer：

```
Routes（HTTP 薄层）+ 本体工具层（20→21 工具）
    ↓ 依赖注入
Services（业务编排层）
    新增：DataFrameQueryService（编排中枢）/ GeoTimeQueryService
    扩展：ObjectQueryService（水合复用）/ TextQL（产 ObjectSet IR）
    实现：LinkTraversalService（路标#3，本期落地）
    ↓ 构造函数注入
Layer Implementations（层实现）
    ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │ Catalog  │ │ Metadata │ │ Dataset  │ │  Index   │ │ Pipeline │
    │Gravitino │ │PostgreSQL│ │ Iceberg  │ │  Doris   │ │SeaTunnel │
    └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘
    ┌──────────────┐  ┌────────────────────┐  ┌──────────┐
    │ Graph (新)   │  │ GeoTime (新)       │  │  Engine  │
    │ Neo4jGraph   │  │ PostGIS+TimescaleDB│  │  Trino   │
    │ Store        │  │ (同 PG 实例)       │  │          │
    └──────────────┘  └────────────────────┘  └──────────┘
    ↓
Core Models（领域模型）
    扩展：LinkType(权重/时效) / Property(DataType 扩展 GEOPOINT/GEOSHAPE/GEOTEMPORAL_SERIES/TIME_SERIES)
    新增表：analysis_records（证据链快照）
```

### 2.3 新增 Layer 职责边界

| Layer | 职责 | 存储 | 不做的事 |
|---|---|---|---|
| **Graph Layer** (`layers/graph/`) | 实体关系存储、多跳遍历、路径推理 | Neo4j | 不存全量业务属性（仅剪枝必需）、不做空间/时序 |
| **GeoTime Layer** (`layers/geotime/`) | 静态空间属性(GiST) + 动态GTS时空序列(超表) + 空间/时序查询 | PG+PostGIS+TimescaleDB | 不存图结构、不做全量业务属性 |

**红线**：
- Graph/GeoTime 的数据是 Doris/Iceberg 的**投影**（派生副本），可全量重建。Action 写入态另有从 outbox payload snapshot 的 fast-path 投影（§6.3）
- GeoTime 时序超表由 **ObjectIndexFunnel 写入**（带 rid，§6.5），不经 object_state
- 三引擎间**禁止跨库 Join**，联动通过 DataFrameQueryService 应用层编排 + rid 集传递

---

## 三、本体模型扩展（克制原则，C10）

### 3.1 设计立场

Alert（告警）、RiskScore（风险评分）、Function（计算逻辑）等 Palantir 概念，**全部用 Gaia 现有 ObjectType + Property + ActionType 表达**，不新增一等公民模型。Alert 是 ObjectType，RiskScore 是数值 Property（由 Action 计算），Function 用 Action+submission_criteria 替代（Function 抽象留二期）。

### 3.2 LinkType 扩展（图遍历必需）

| 新增字段 | 类型 | 用途 |
|---|---|---|
| `weight_property` | `str \| None` | 权重属性名（指向边属性），路径推理加权 |
| `temporal` | `bool` | 是否时态关系（含有效期），默认 False |

时态边的 `start_time`/`end_time` 作为边属性（Neo4j 关系属性 / object_links JSONB），不作为 LinkType 固定列。`temporal=True` 标记此 LinkType 有时态语义，查询时由执行器注入时间窗口过滤。

**不加**：多态、`security_marking` 独立列（复用现有 `visibility`）。

### 3.3 Property 扩展（类型驱动路由，C4 核心）

**核心机制：Property 的 DataType 决定存储路由，不靠额外标注字段。**

DataType 枚举扩展（激活现有空枚举 + 新增时序类型）：

| DataType | 含义 | 对齐 Palantir | 存储 | 写入路径 |
|---|---|---|---|---|
| `GEOPOINT`（现有，激活） | 静态单点坐标 | Palantir Geopoint | PostGIS 点表（GiST） | Action 写 object_state 后投影 |
| `GEOSHAPE`（现有，激活） | 静态线/多边形 | Palantir Geoshape | PostGIS 几何表（GiST） | Action 写 object_state 后投影 |
| `GEOTEMPORAL_SERIES`（新） | 动态时空序列引用 | Palantir GTS | TimescaleDB 超表（含 position） | ObjectIndexFunnel（§6.5） |
| `TIME_SERIES`（新） | 纯时序引用（无空间） | Palantir Time Series Property | TimescaleDB 超表（无 position） | ObjectIndexFunnel（§6.5） |

**`indexed` 字段复用**（现有，不新增）：`indexed=True` 时各引擎按类型自动建索引（GEOPOINT→GiST，普通字段→Doris倒排/Neo4j B-tree）。

**约束**：
- `GEOTEMPORAL_SERIES` / `TIME_SERIES` 类型的属性值 = Series ID（指向超表），不存点位/指标本身
- `indexed=True` 的属性数量建议 ≤ 5（避免节点膨胀），定义时校验告警
- **时序类型属性的 `indexed` 语义**：`GEOTEMPORAL_SERIES`/`TIME_SERIES` 的 `indexed=True` 当前无额外效果——超表索引由 `(series_id, timestamp DESC)` 复合索引覆盖（§5.3），Series ID 本身在 object_state/Doris 上建 B-tree 意义不大（要的是超表内按 series_id 查，不是反查对象）。如未来需"按 Series ID 反查拥有该轨迹的对象"，再考虑在 object_state 上对 Series ID 建 B-tree

**示例**：
```python
Property(api_name="location", data_type="GEOPOINT", indexed=True)        # 静态空间
Property(api_name="track", data_type="GEOTEMPORAL_SERIES")               # 动态轨迹引用
Property(api_name="inventoryHistory", data_type="TIME_SERIES")           # 纯时序引用
Property(api_name="status", data_type="STRING", indexed=True)            # 图剪枝字段
```

投影器读 `data_type` 决定路由：GEOPOINT/GEOSHAPE → PostGIS；GEOTEMPORAL_SERIES/TIME_SERIES → 超表引用；`indexed=True` → 同步到 Neo4j 节点做剪枝。

### 3.4 新增表：analysis_records（证据链快照）

```python
class AnalysisRecordModel(Base):
    """图分析查询的证据链快照（C11，合规溯源轻量版）。

    每次推理查询生成一条记录，含 ObjectSet IR + 各步引擎结果摘要 +
    命中对象的血缘指针。不做全链路数据血缘反查（留二期）。
    """
    __tablename__ = "analysis_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    ontology_id: Mapped[str] = mapped_column(String(32), ForeignKey("ontologies.id", ondelete="CASCADE"), nullable=False)
    principal: Mapped[str] = mapped_column(String(255), default="anonymous")
    object_set_ir: Mapped[dict] = mapped_column(JSONB, nullable=False)    # ObjectSet IR 快照
    result_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)   # 各步引擎耗时 + 命中数 + truncated
    evidence_pointers: Mapped[dict] = mapped_column(JSONB, nullable=False) # 当前存 {"rids": [...], "lineage": null}；lineage 字段为后续真实血缘指针占位（§10.2）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
```

### 3.5 Schema 变更走 Alembic

LinkType 扩展 + Property DataType 枚举扩展 + analysis_records 新表，均通过 Alembic migration。仅加枚举值/列，不改存量数据。

---

## 四、图引擎层设计（Graph Layer）

> 配套 ADR：核心决策以本设计文档为权威源（图引擎选型 + 四条迁移口子 + 原生 Cypher），尚未独立立项 ADR；VIRTUAL 投影部分见 [adr-021-virtual-graph-projection.md](./adr-021-virtual-graph-projection.md)。

### 4.1 本期不抽象，但留四条迁移口子（C1）

| 口子 | 约束 | 迁移时价值 |
|---|---|---|
| **rid 稳定主键** | Neo4j 节点用 Palantir RID（`ri.ontology.main.object.{uuid}`）作 `rid` 属性，不用 Neo4j 内部 id | NebulaGraph 强制 vertex id，数据 1:1 搬移 |
| **Cypher 收口** | 所有 Cypher 收口在 `Neo4jGraphStore`（`layers/graph/`），Service/工具层只调方法 | 迁移时只改一个类；未来提 Protocol 即抽象层 |
| **强 schema 建模** | 标签/边类型先 `CREATE` 再写入，不用 schema-less 动态加属性 | NebulaGraph 强 schema，不会遇到兼容问题 |
| **边模型轻量** | 边仅存 `weight + start_time/end_time + visibility`，不存业务详情 | NebulaGraph 边语义不同（rank），轻量边迁移最简 |

> **身份模型说明（2026-07-15）**：图节点主键采用 Palantir [Resource Identifier](https://github.com/palantir/resource-identifier) 规范，格式 `ri.<service>.<instance>.<type>.<locator>`。Gaia 对象 RID = `ri.ontology.main.object.{uuid}`：
> - `service=ontology`、`instance=main`（单实例部署）、`type=object`（固定，不嵌具体 ObjectType；ObjectType 是独立资源 `ri.ontology.main.object-type.{api_name}`）
> - `locator=UUID`（系统分配，稳定不变）——**不用 primary key 当 locator**，这是 Palantir 核心设计：RID（系统身份）与 primary key（业务身份）正交分离
> - 命名统一为 `rid`（通用概念，所有资源都用，靠 type 段区分；与 `ontology.rid` 同名不冲突）
> - 应用层判等用 `(typeId, primaryKey)`，不用 rid（对齐 Palantir，新创建未持久化对象 rid 为 undefined）
> - Iceberg 不用 rid（用业务主键列，与 Palantir backing dataset 一致）
> - 废弃原 `vid`（Vertex ID 缩写，无全称且与 VIRTUAL 的 V 混淆）和 `object_id`（中间过渡命名）
>
> **PK 变更语义（2026-07-16 订正，对齐 Palantir）**：原表述“primary key 改了 RID 也不变”过度理想化。Palantir 实际行为：改 ObjectType 的 primary key 属于需要 **unregister/reregister backing datasource 的变更**（会触发 reindex，对象在 reindex 期间不可用）。Gaia 同样：rid 本身不变，但 **PK 变更需重建 rid↔PK 映射 + 重新投影派生索引**（Neo4j/PostGIS 的 PK 业务值字段需重投影）。不支持在线无缝改 PK。
>
> **rid 分配权威与存储（2026-07-17 订正，对齐 Palantir Object Data Funnel）**：
> - **rid 权威源是 Doris idx 表**（object database），不是 PG object_state、不是 Iceberg、不是单独映射表。对齐 Palantir：rid 是 object database 的内部字段（`__rid`），不是 backing dataset 的字段
> - **rid 分配时机**：① Action 写入时，Action 执行分配 rid（写 object_state.id，outbox 同步 Doris）；② 外部接入时，ObjectIndexFunnel（§6.1）从 Iceberg 读数据，按 PK 查 Doris 已有 rid 复用、无则新分配
> - **rid 幂等性**：靠 Doris PK UNIQUE KEY + upsert 语义——同 PK 复用同 rid，不单独存映射表。全量 reindex 按 PK 复用 Doris 已有 rid，不重新分配（对齐 Palantir 全量 reindex 不改 rid）
> - **Iceberg 不存 rid**：backing dataset 保持纯净（只有业务 PK + 全量属性），rid 是索引层字段，由 ObjectIndexFunnel 分配后写进 Doris/Neo4j/PostGIS/TimescaleDB 四引擎
> - **object_state 降级**：object_state 是 Action 写入路径暂存态（outbox 消费前 fast-path），**不再是 rid 分配权威**。rid 权威是 Doris idx 表

### 4.2 Neo4jGraphStore 接口

```python
# layers/graph/neo4j_graph_store.py

class Neo4jGraphStore:
    """Graph Layer。所有 Cypher 收口于此，上层只调方法（C1）。"""

    def __init__(self, driver: AsyncGraphDatabase) -> None: ...

    # ── Schema（define_object_type / define_link_type 触发） ──
    async def create_label(self, object_type_api_name: str, indexed_props: list[str]) -> None: ...
    async def create_relationship_type(self, link_type_api_name: str, temporal: bool) -> None: ...
    async def create_constraints(self, object_type_api_name: str, pk_property: str) -> None: ...

    # ── 写入（GraphProjector 调用） ──
    async def upsert_node(self, rid: str, label: str, props: dict) -> None: ...
    async def upsert_edge(self, link_type: str, source_rid: str, target_rid: str, props: dict) -> None: ...
    async def delete_node(self, rid: str) -> None: ...
    async def delete_edge(self, link_type: str, source_rid: str, target_rid: str) -> None: ...

    # ── 查询（DataFrameQueryService 的 searchAround 步骤调用） ──
    async def search_around(
        self,
        source_rids: list[str],
        rel_types: list[str] | None,    # None=任意关系
        min_hops: int,                  # 默认 1
        max_hops: int,                  # 默认 3
        direction: Literal["out", "in", "both"],
        node_filter: NodeFilter | None,  # 终点谓词（仅过滤终点 m，不过滤中间节点；详见 §4.5）
        limit: int,                      # 去重 (start,m) 边对数上限（100 万，C9；rids 自动去重保序，matched_count=边对数）
    ) -> GraphTraversalResult: ...

    async def find_paths(
        self,
        source_rid: str,
        target_rid: str,
        rel_types: list[str] | None = None,
        max_depth: int = 5,
        limit: int = 10,
    ) -> list[list[str]]: ...  # 返回 rid 序列列表，每条路径 [source, ..., target]

    async def exists_link(self, rel_type: str, source_rid: str, target_rid: str | None) -> bool: ...
```

### 4.3 图数据模型

- **节点**：每个 ObjectType = 一个 Label。属性 = `rid + api_name + 主键值 + indexed属性 + visibility`，不存全量
- **边**：每个 LinkType = 一个 Relationship Type。属性 = `weight(可选) + start_time/end_time(时态) + visibility`
- **索引**：主键唯一约束；indexed 属性建 B-tree（剪枝）
- **反向遍历**：Neo4j 原生双向，不存反向边

### 4.4 多跳用原生 Cypher，不用 APOC path.expand（C9）

APOC `apoc.path.expand` 不被内存追踪器检测，可能 OOM（附录 C）。本期多跳只用原生 Cypher `MATCH (n)-[*1..3]->(m)` + LIMIT。APOC 仅用于辅助（如 `apoc.coll`）。

> **⚠️ 高扇出爆炸风险**：Neo4j 的 `*1..3` 是**先展开所有路径再 LIMIT**——若某起点 3 跳内可达节点达 10^8 量级（高扇出图，如社交网络平均度数 > 50），中间态在 LIMIT 生效前就可能 OOM。防线二的"1000万截断"截的是返回值，不是中间计算。原生 `MATCH *1..N` 与 APOC `path.expand` 同样不被内存追踪器检测——"不用 APOC 避免 OOM"的论证仅对配置可控性成立，对中间态保护两者都无解。
>
> **前置约束（高扇出图必须遵守）**：
> - searchAround 必须指定 `rel_types`（不允许任意关系全图遍历）
> - 起点集必须经前置 filter 缩减（不拿"全部供应商"当起点）
> - 高扇出图（ObjectType 平均度数 > 50）须配置更小的 `max_hops`（默认 2 而非 3）
> - 未来如需更强中间态保护，评估 `apoc.path.expandConfig` 的 `limit`/`maxNodes` 剪枝参数（虽弃用 path.expand，expandConfig 的剪枝是原生 MATCH 做不到的）

### 4.5 searchAround 语义明确（对齐 Palantir）

> **调研结论**（Palantir Foundry ObjectSet）：searchAround 返回的是**去重后的终点对象集**（ObjectSet），不是路径集；上限 1000 万**对象**（非路径）；最多 3 跳；filter 作用于**结果对象集**（终点），不作用于路径中间节点。Palantir 不在图引擎层暴露 node_filter 下推，而是把 searchAround 抽象成 ObjectSet 操作，filter 是后续独立步骤。

Gaia 语义明确如下（消除原设计 §4.2 `node_filter` + `limit` 的歧义）：

| 语义点 | 定义 | 理由 |
|---|---|---|
| **返回内容** | 去重后的终点 rid 集（非路径、非 (start,end) 对） | 对齐 Palantir ObjectSet 语义；用户要的是“哪些对象被关联到”，不是“走了哪条路” |
| **LIMIT 作用对象** | 去重后的**终点 rid 数** | 不是 (start,m) 对数（否则多起点时 LIMIT 被起点数稀释）；Cypher 实现应为 `WITH DISTINCT m ... LIMIT` 而非 `WITH DISTINCT start, m ... LIMIT` |
| **node_filter 作用层** | **终点谓词**（只过滤终点 m，不过滤路径中间节点） | 对齐 Palantir：中间节点不满足 filter 时路径**断开**（不跳过绕行）；若需中间节点过滤，应拆为多步 searchAround |
| **嵌套深度** | searchAround ≤ 3 层 | 对齐 Palantir 硬限（`.all()` 最多 3 个 searchAround） |
| **强制索引** | source rid 必须命中 rid 唯一约束 / B-tree | 防止无索引全图扫导致 30s 超时 |

**实现约束**：Cypher 形如 `UNWIND $rids AS src MATCH (start{rid:src})-[*1..3]->(m) WHERE m.xxx WITH DISTINCT m LIMIT $limit`；node_filter 仅渲染到终点 `m` 的 WHERE。路径中间节点过滤不支持（设计上禁止，避免图引擎语义复杂化）。

> **✅ 已收敛（方向 B 精细化，handoff-rid-funnel-closure.md T1.7/D3，2026-07-27）**：LIMIT 作用对象 = **去重 (start, m) 边对数上限**（Cypher `WITH DISTINCT start, m LIMIT $limit`）。理由：`edges` 字段独立承载 (start→target) 边三元组供前端画布渲染探索轨迹箭头（ADR-015），与 LIMIT 作用对象解耦。`rids` 返回**去重终点 rid 集**（保序，同 m 被多 start 命中不重复）；`matched_count` = 边对数；`truncated` 基于边对数判定。下游去重 rid 集自然 ≤ 边对数，不单独截断。一次 Cypher 查询零额外往返（优于方向 A 的两次查询）。

**node_filter 与 ObjectSet IR 的关系**：ObjectSet IR 的 `searchAround` type **不直接暴露 node_filter 参数**（对齐 Palantir，filter 是后续独立步骤）。`NodeFilter` 是 Graph Layer 的内部执行参数，DataFrameQueryService 在求值 `searchAround` 时**可选**把同层后续 `filter` 操作优化下推为 Neo4j node_filter（减少回 Python 的 rid 集大小）。当前实现未做此优化——IR 的 filter 总是先 searchAround 取 rid 集回 Python，再走 §7.4 的属性过滤（Trino→Doris）。node_filter 下推优化列为二期性能改进。

---

## 五、时空层设计（GeoTime Layer）

> 配套 ADR：核心决策以本设计文档为权威源（时空层选型 + 静态/动态二分 + ObjectIndexFunnel 统一写入 + 精简双存），尚未独立立项 ADR。

### 5.1 静态空间属性 vs 动态 GTS（C2 核心）

| 数据形态 | 判断标准 | DataType | 存储 | 写入路径 |
|---|---|---|---|---|
| **静态空间属性** | 位置固定、低频变更、仅筛选/距离 | `GEOPOINT`/`GEOSHAPE` | PostGIS 空间表（GiST） | Action 写 object_state 后投影 |
| **动态 GTS 时空序列** | 高频、需轨迹回放、体量大 | `GEOTEMPORAL_SERIES` | TimescaleDB 超表（含 position） | ObjectIndexFunnel（C3/§6.5） |
| **纯时序指标** | 高频、连续、无空间 | `TIME_SERIES` | TimescaleDB 超表（无 position） | ObjectIndexFunnel（C3/§6.5） |

### 5.2 静态空间属性存储

```sql
-- OntologyService.define_object_type 触发自动创建（命名走 core/naming.py snake_case）
CREATE TABLE geo_<ont>__<type> (
    rid VARCHAR(128) PRIMARY KEY,       -- Palantir RID: ri.ontology.main.object.{uuid}
    api_name VARCHAR(255) NOT NULL,
    pk_value VARCHAR(255) NOT NULL,
    location GEOGRAPHY(POINT, 4326),   -- GEOPOINT
    geometry GEOGRAPHY(POLYGON, 4326), -- GEOSHAPE（二选一）
    -- indexed 同步过来的剪枝字段
    status VARCHAR(64),
    region VARCHAR(64),
    update_time TIMESTAMPTZ DEFAULT NOW(),
    data_version BIGINT DEFAULT 0
);
CREATE INDEX idx_geo_<ont>__<type>_geom ON geo_<ont>__<type> USING GIST (location);
CREATE INDEX idx_geo_<ont>__<type>_geom_poly ON geo_<ont>__<type> USING GIST (geometry);
```

精简双存：PostGIS 只存 `rid + 主键 + 几何 + 剪枝字段`，不存全量（全量在 Doris）。

### 5.3 动态 GTS 时空序列存储

```sql
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE timeseries_<ont>__<type>__<series> (
    rid         VARCHAR(128) NOT NULL,   -- 🆕 宿主对象 rid（与 Doris/Neo4j/PostGIS 一致，§6.5）
    series_id   VARCHAR(64) NOT NULL,    -- 时序身份（与对象 GEOTEMPORAL_SERIES 属性一一对应）
    timestamp   TIMESTAMPTZ NOT NULL,    -- 强制：时序分片依据
    location    GEOGRAPHY(POINT, 4326),  -- 强制：空间索引基础（GEOTEMPORAL_SERIES 必有，TIME_SERIES 无此列）
    speed       DOUBLE PRECISION,        -- 业务指标列
    status      VARCHAR(32),
    payload     JSONB
);
SELECT create_hypertable('timeseries_<ont>__<type>__<series>', 'timestamp',
    chunk_time_interval => INTERVAL '1 day');
CREATE INDEX idx_timeseries_<...>_location ON timeseries_<...> USING GIST (location);
CREATE INDEX idx_timeseries_<...>_series_time ON timeseries_<...> (series_id, timestamp DESC);
CREATE INDEX idx_timeseries_<...>_rid_time ON timeseries_<...> (rid, timestamp DESC);  -- 🆕 按 rid 查轨迹
```

**写入链路（ObjectIndexFunnel 统一，§6.5）**：

```
Kafka topic / 文件流
    ↓ SeaTunnel（搬运工，无状态）→ Iceberg（append，backing dataset）
    ↓ ObjectIndexFunnel 增量消费 Iceberg snapshot
    ↓   按 series_id 查 Doris 复用宿主对象 rid
    ↓   写 TimescaleDB 超表（带 rid 列）
    ↓
TimescaleDB 超表（带 rid，与 Doris/Neo4j/PostGIS 一致）
```

> **写入链路订正（2026-07-17）**：原设计“SeaTunnel 直写 TimescaleDB（不经 Iceberg）”**已废弃**——SeaTunnel 无法在写超表前"按 series_id 查 Doris 复用 rid"，导致 rid 缺失。改为 SeaTunnel 只搬进 Iceberg，ObjectIndexFunnel 统一负责 rid 分配 + 写超表（§6.5）。时序明细不进 Doris（Doris 只存 series_id 属性引用）。

**不经 Iceberg 的理由（修订）**：原表述“时序高频写入不经 Iceberg”已过时。新链路：高频流式数据先落 Iceberg（append 模式，Iceberg 支持高频 append），ObjectIndexFunnel 增量消费。代价是延迟（轮询间隔，秒级~分钟级）。若需亚秒级延迟，二期评估“SeaTunnel 直写超表（rid 暂空）+ ObjectIndexFunnel 异步回填 rid”优化路径。

> **遗留任务**：时序链路的端到端验证与超表运维留独立任务跟踪：
> - ObjectIndexFunnel 增量消费 Iceberg snapshot 写 TimescaleDB 的 live 验证未做
> - 超表运维（chunk_time_interval / 压缩策略 / retention / 连续聚合）未规划
> - 本次仅保留架构设计与接口定义，真实链路落地排期后续任务

**object_state 角色**：对象上 `GEOTEMPORAL_SERIES` 属性只存 Series ID（指向超表），不存点位。rid 权威是 Doris idx 表，超表的 rid 列由 ObjectIndexFunnel 从 Doris 复用写入。

### 5.4 GeoTimeStore 接口

```python
# layers/geotime/geotime_store.py

class GeoTimeStore:
    """时空层。PostGIS（静态空间）+ TimescaleDB（动态序列）同 PG 实例，合并封装。"""

    def __init__(self, pg_pool: AsyncConnectionPool) -> None: ...

    # ── Schema（define_object_type 触发） ──
    async def create_geo_table(self, ontology: str, object_type: str, geo_type: str, indexed_fields: list[str]) -> None: ...
    async def create_timeseries_hypertable(self, ontology: str, object_type: str, series_name: str, has_position: bool, metric_fields: list[str]) -> None: ...

    # ── 静态空间写入（GeoTimeProjector 调用） ──
    async def upsert_geo(self, table: str, rid: str, geometry, props: dict) -> None: ...

    # ── 动态序列写入（SeaTunnel sink / 流式链路） ──
    async def append_series(self, hypertable: str, rows: list[dict]) -> None: ...

    # ── 空间查询（DataFrameQueryService 的空间 filter 步骤） ──
    async def spatial_filter(
        self, table: str, candidate_rids: list[str],
        spatial_op: Literal["withinDistance", "withinPolygon", "intersects"],
        geometry, max_distance: float | None,
    ) -> list[str]: ...  # 返回命中 rid（GiST + ID IN 过滤，毫秒级）
    # 候选 rid 集非空时：GiST + rid IN（缩减扫描范围）
    # 候选 rid 集为空时（如起始集就要空间过滤）：退化为全表 GiST 扫描（仍走索引，不顺序扫）

    # ── 时序查询（DataFrameQueryService 的时序 filter 步骤） ──
    async def series_query(
        self, hypertable: str, rids: list[str],   # 🆕 按 rid 查（超表带 rid 列，§6.5）
        time_range: tuple[datetime, datetime] | None,
        spatial_filter: SpatialFilter | None,
        aggregations: list[AggSpec] | None,
    ) -> list[dict]: ...  # 返回轨迹点（带 rid，可回溯对象，无需中转 Doris 查 series_id）
```

### 5.5 docker-compose 变更（C2+C14）

```yaml
# PG 换一体镜像（PostGIS + TimescaleDB 共存，附录 E 验证可行）
postgres:
  image: ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6
  container_name: ontology-postgres
  environment:
    POSTGRES_DB: ontology
  volumes:
    - ./infra/init-pg-extensions.sql:/docker-entrypoint-initdb.d/01-extensions.sql

# Neo4j 独立服务（按需启停）
neo4j:
  image: neo4j:5-community
  container_name: gaia-neo4j
  environment:
    NEO4J_AUTH: neo4j/${NEOJ_PASSWORD:-change-me}
    NEO4J_PLUGINS: '["apoc"]'
    NEO4J_dbms_memory_transaction_database_max_size: 512MB
  ports: ["7474:7474", "7687:7687"]
  volumes: [neo4j-data:/data]
  profiles: ["graph"]  # docker compose --profile graph up
```

> **Neo4j 社区版 HA 限制（2026-07-16 确认）**：`neo4j:5-community` 是单实例部署，**无 clustering / failover / read replica**（causal cluster 是企业版独占能力）。社区版只能靠 OS 级定时备份（`neo4j-admin dump`）做容灾，无自动故障切换。生产部署需评估：
> - 单点宕机 → 推理线全挂（SQL 线 `query_with_sql` 不受影响，符合 F10）
> - 如需 HA，选项：① 升级 Neo4j 企业版（商业授权）；② 换开源支持集群的图库（如 NebulaGraph，C1 已留迁移口子）；③ 推理线降级（MVP 不支持，列二期）
> - MVP 接受单点，但需配套定时备份 + 监控告警
> - **HA 触发门槛**（超过任一即评估升级 HA）：① 图节点数 > 1000 万；② 推理线 QPS > 50；③ SLA 要求可用性 > 99.5%；④ 业务场景为情报/军工等不可中断类（§1.4）。社区版冷备（`neo4j-admin dump`）RPO/RTO 以分钟~小时计，不满足实时性要求

```sql
-- infra/init-pg-extensions.sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
```

---

## 六、数据写入与索引编排（ObjectIndexFunnel）

> **架构对齐 Palantir**：Palantir 的 Object Data Funnel（"Funnel"）是 object database 的唯一写入者，统一负责从 backing dataset + user edits 读取数据、分配/复用 rid、写入 object database。Gaia 对应 **`ObjectIndexFunnel`**（`services/object_index_funnel.py`）——统一负责从 Iceberg（backing dataset）+ Action 写入，分配/复用 rid，扇出写入 Doris idx + Neo4j + PostGIS + TimescaleDB 四引擎。
>
> **为什么需要统一漏斗**：rid 要在四引擎间一致。若各引擎各自写入、各自分配 rid，必然不一致。ObjectIndexFunnel 是 rid 分配的唯一权威点——rid 在这里确定后，用同一个 rid 扇出写所有引擎。对齐 Palantir Funnel 的"Merge changes（按 PK join）+ Indexing（分配 rid）+ Hydration（写 object database）"四步。

### 6.1 ObjectIndexFunnel 职责

```python
# services/object_index_funnel.py

class ObjectIndexFunnel:
    """统一索引编排：backing dataset + Action 写入 → rid 分配 → 四引擎扇出。

    对齐 Palantir Object Data Funnel。是 Doris idx / Neo4j / PostGIS /
    TimescaleDB 四引擎的唯一写入者，保证 rid 跨引擎一致。

    rid 权威源是 Doris idx 表（PK UNIQUE KEY + rid 列）。ObjectIndexFunnel
    按 PK 查 Doris 已有 rid 复用，无则新分配（uuid4 →
    ri.ontology.main.object.{uuid}），再用同一 rid 扇出写四引擎。
    """

    def __init__(
        self,
        dataset: IcebergStore,           # 读 backing dataset
        index_store: DorisIndexStore,    # rid 权威 + 对象层写入
        graph_projector: GraphProjector | None,       # Neo4j 扇出
        geotime_projector: GeoTimeProjector | None,   # PostGIS 扇出
        geotime_store: GeoTimeStore | None,           # TimescaleDB 扇出（时序）
        metadata: PostgresMetaStore,
    ) -> None: ...

    async def index_from_backing_dataset(
        self, ontology: str, object_type: str, limit: int = 0,
    ) -> dict[str, int]:
        """外部接入路径：读 Iceberg → rid 分配/复用 → 扇出写四引擎。

        触发：SeaTunnel backfill 完成后 / admin rebuild / provision 后首轮。
        """
        rows = await self._dataset.scan_latest(...)
        for row in rows:
            pk = row[pk_column]
            # rid 复用：按 PK 查 Doris 已有 rid
            rid = await self._index_store.get_rid_by_pk(ont, ot, pk)
            if rid is None:
                rid = f"ri.ontology.main.object.{uuid.uuid4().hex}"  # 新分配
            # 先写 Doris（rid 权威，必须成功）
            await self._index_store.upsert(ont, ot, [{"rid": rid, **row}])
            # 扇出写派生引擎（同一 rid，fail-tolerant）
            await self._graph_projector.project_object(ont, ot, {"id": rid, "properties": row})
            await self._geotime_projector.project_object(ont, ot, {"id": rid, "properties": row})
            # 时序数据：按 series_id 查 Doris 复用 rid，写超表（见 §6.5）

    async def index_from_action(self, ontology: str, object_state: dict) -> None:
        """Action 写入路径：用 Action 已分配的 rid 扇出写四引擎。

        由 OutboxExecutor INDEX effect 委托调用。rid 来自 object_state.id
        （Action 执行时分配），不再重新查 Doris（Action 是 rid 的首次分配点）。
        """
        rid = object_state["id"]
        await self._index_store.upsert(ont, ot, [{"rid": rid, **props}])
        await self._graph_projector.project_object(ont, ot, object_state)
        await self._geotime_projector.project_object(ont, ot, object_state)

    async def rebuild_for_object_type(self, ontology: str, object_type: str) -> int:
        """全量重建：从 Doris idx 读全量 rid + 属性 → 重投影 Neo4j/PostGIS。

        rebuild 不重新分配 rid（按 PK 复用 Doris 已有 rid，对齐 Palantir
        全量 reindex 不改 rid）。Doris 是权威，Neo4j/PostGIS 是派生可重建。
        """
        ...
```

### 6.2 四种写入触发模式（统一经 ObjectIndexFunnel）

| 模式 | 触发 | 数据来源 | ObjectIndexFunnel 动作 |
|---|---|---|---|
| **A. Action 写入** | 用户操作产生对象/关系变更 | object_state（Action 分配 rid） | 用 Action 已分配的 rid 扇出写四引擎（OutboxExecutor INDEX effect 委托） |
| **B. 外部批量接入** | SeaTunnel backfill 完成后 / admin rebuild | Iceberg scan_latest（无 rid） | 按 PK 查 Doris 复用 rid or 新分配 → 先写 Doris → 扇出 Neo4j/PostGIS/TimescaleDB |
| **C. 外部 CDC 增量** | Iceberg 新 snapshot（轮询） | Iceberg 增量 append | 同 B，增量处理新 snapshot |
| **D. VIRTUAL 联邦投影**（ADR-021） | `register_virtual_table` 成功 / admin rebuild | TrinoQueryEngine.query（合成 object_state） | 合成 rid（`ri.ontology.main.virtual-object.*`）→ 写 Neo4j 身份骨架（不经 Doris，VIRTUAL 无表） |

> **模式 D 与 A/B/C 区别**：不经 Iceberg（VIRTUAL 无表）/ 不经 outbox（VIRTUAL 不产生 Action）/ 不经 SeaTunnel（不落地）。数据源是 TrinoQueryEngine，rid 是合成的（非系统分配）。详见 [`adr-021-virtual-graph-projection.md`](./adr-021-virtual-graph-projection.md) + [`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md)。

> **废弃路径**：原 SeaTunnel Iceberg→Doris backfill（`PIPELINE_INDEX_BACKFILL_TEMPLATE`）**已废弃**。SeaTunnel 回归纯搬运职责（外部源→Iceberg），不再直写 Doris。原因：SeaTunnel 是无状态搬运工，无法在写 Doris 前"按 PK 查已有 rid 复用"，导致 rid 缺失或不一致。Doris 写入统一归 ObjectIndexFunnel（用 DorisIndexStore.upsert，Python 侧批量）。

### 6.3 同源分发与 rid 一致性（ObjectIndexFunnel 统一编排）

```
外部数据源 → SeaTunnel（搬运工，无状态）→ Iceberg（backing dataset，无 rid）
                                              ↓
                                    ObjectIndexFunnel（统一索引编排）
                                    ① 读 backing dataset / object_state
                                    ② rid 分配/复用（按 PK 查 Doris）
                                    ③ 先写 Doris idx（rid 权威）
                                    ④ 扇出写 Neo4j + PostGIS + TimescaleDB（同一 rid）
                                              ↓        ↓        ↓        ↓
                                           Doris idx  Neo4j   PostGIS  TimescaleDB
                                           (权威)    (派生)   (派生)   (派生)

Action 写入 → object_state（Action 分配 rid）→ ObjectIndexFunnel（同上②③④）

VIRTUAL（不落地）→ TrinoQueryEngine → ObjectIndexFunnel（合成 rid，仅写 Neo4j 身份骨架）
```

**四引擎 rid 一致性**：rid 由 ObjectIndexFunnel 在唯一权威点分配/复用，用同一 rid 扇出写四引擎。不存在各引擎各自分配 rid 的情况。

| 引擎 | rid 角色 | 数据形态 | 写入顺序 | 一致性 |
|---|---|---|---|---|
| **Doris idx** | rid 权威源（PK UNIQUE KEY + rid 列） | 每对象 1 行（rid + PK + 全量属性） | 先写（rid 锁定） | **必须成功** |
| **Neo4j** | rid 作节点属性 + 唯一约束 | 节点（rid + indexed 属性） | Doris 后扇出 | 派生，可重建 |
| **PostGIS** | rid 作空间表主键 | 空间行（rid + 几何 + 剪枝字段） | Doris 后扇出 | 派生，可重建 |
| **TimescaleDB** | rid 作超表列 | 时序行（rid + series_id + timestamp + 明细，每对象 N 行） | Doris 后扇出 | 派生，可重建 |

> **TimescaleDB 不复制进 Doris**：时序明细只存 TimescaleDB（亿级行，按 timestamp 分片），Doris 只存对象的 series_id 属性引用（1 行）。两者数据形态不同，不互相复制。rid 是跨引擎关联身份——推理线从图遍历拿 rid 集，直接查 TimescaleDB `WHERE rid IN (...)`，无需中转 Doris 查 series_id。

**rid 分配权威是 Doris idx 表**（对齐 Palantir object database）：
- **幂等机制**：ObjectIndexFunnel 写 Doris 时按 PK 查已有 rid——存在则复用，不存在则新分配（uuid4 → `ri.ontology.main.object.{uuid}`）。靠 Doris PK UNIQUE KEY + upsert 语义，**不需要单独映射表**
- **全量 reindex 稳定**：rebuild 时按 PK 从 Doris 已有 rid 复用，不重新分配（对齐 Palantir 全量 reindex 不改 rid）
- **object_state 降级**：object_state 是 Action 写入路径暂存态（outbox 消费前的 fast-path），**不再是 rid 分配权威**。rid 权威是 Doris idx 表。Action 执行时分配 rid 写 object_state，outbox 同步到 Doris 后以 Doris 为准

**rebuild 数据源**：`rebuild_for_object_type`（现为 ObjectIndexFunnel.rebuild_for_object_type）从 **Doris idx 表**读全量 rid + 属性重投影到 Neo4j/PostGIS。**不从 PG object_state 读**——object_state 只存 Action 写入态、不含外部接入数据，把它当 rebuild 数据源会导致外部接入的对象在图里缺失。Doris 是全量真相源（Action + 外部接入都写进了 Doris）。

**现有服务改造**：
- **ProjectSyncService → 升级重命名为 ObjectIndexFunnel**（PR-2 已完成）：加 DorisIndexStore 注入 + rid 分配/复用 + 四引擎扇出
- **OutboxExecutor INDEX 分支**：保留 outbox 消费框架，INDEX 处理逻辑委托给 ObjectIndexFunnel.index_from_action
- **IndexSyncService**：职责收缩为只管 Doris 表 schema（provision/rebuild/deprovision 建表删表），**废弃 SeaTunnel backfill 数据同步**（原 `PIPELINE_INDEX_BACKFILL_TEMPLATE`），数据同步归 ObjectIndexFunnel

### 6.4 一致性模型（C8，订正：删 sync=true）

- **best-effort 最终一致**（秒级）：Neo4j/PostGIS/TimescaleDB 是派生副本，Doris idx 更新后异步扇出，查询看到的是“上一秒”的状态
- **不提供 sync=true / 强制等投影**：早期设计拟提供 `sync=true` 选项强制等投影完成再查，但该机制依赖 outbox 同步等待，与 best-effort 异步投影架构冲突，**已删除该承诺**
- **read-your-writes 不保证**：Action 写入后立刻查询可能看不到刚写入的关系（ObjectIndexFunnel 未消费 outbox）；如需保证，调用方自行查询源（Trino→Doris）而非派生索引
- **扇出写入部分失败处理**：Doris idx 写成功 = rid 已锁定（权威）；Neo4j/PostGIS/TimescaleDB 扇出失败只记日志（fail-tolerant），可从 Doris 全量重建（rebuild_for_object_type）。设计上可扩展为 outbox 重试（GRAPH_UPSERT/SPATIAL_UPSERT/TIMESERIES_APPEND 副作用类型），当前不做
- **不对账**（订正）：Doris / Neo4j / PostGIS / TimescaleDB 都是从 backing dataset（Iceberg）或数据源（虚拟表）经 ObjectIndexFunnel 来的派生索引层，源更新时由 Funnel 保障最终一致性即可，**不需要单独的对账机制**。一致性跟踪由独立任务负责（不在本特性范围）

**VIRTUAL 节点一致性例外（ADR-021 D7）**：VIRTUAL 节点是 best-effort + **不可对账**——外部源无 `data_version`，对账无基准。VIRTUAL 节点**不参与 ConflictDetector 审计**（`_audit_iteration` 排除 `storage_type == VIRTUAL`）。VIRTUAL 全量属性实时（Trino 联邦水合零拷贝），拓扑/剪枝投影态延迟（MVP 手动 rebuild，二期定时刷新）。VIRTUAL 节点孤儿清理走 watermark + cleanup 模式（`_sync_tag` 标记，见 §6.6）。

**架构红线声明**：图/空间/时序是派生数据，秒级最终一致，不适合强实时场景。

**运维约束（Neo4j 启停切换）**：Neo4j 用 `profiles: ["graph"]` 按需启停（§5.5）。Neo4j 关闭期间：① `OntologyService.define` 的 `_provision_graph_schema` 受 `capabilities.graph_indexing_enabled` 门控跳过（best-effort，不阻塞）；② ObjectIndexFunnel 扇出写 Neo4j 失败（fail-tolerant 记日志，不阻塞 Doris/Action）。**Neo4j 重新启动后，关闭期间写入的数据在图里缺失，必须手动调 `rebuild_for_object_type` 全量重投影**（从 Doris 读，§6.3）。这是启停切换的强制运维步骤。

### 6.5 时序数据写入（ObjectIndexFunnel 管 TimescaleDB）

时序数据（`GEOTEMPORAL_SERIES` / `TIME_SERIES` 属性）由 ObjectIndexFunnel 统一写入 TimescaleDB 超表，**带 rid 列**，与 Doris/Neo4j/PostGIS 保持 rid 一致。

**超表 schema 加 rid 列**（§5.3 订正）：
```sql
CREATE TABLE timeseries_<ont>__<type>__<series> (
    rid         VARCHAR(128) NOT NULL,   -- 🆕 宿主对象 rid（与 Doris/Neo4j 一致）
    series_id   VARCHAR(64) NOT NULL,    -- 时序身份（指向同一序列）
    timestamp   TIMESTAMPTZ NOT NULL,    -- 时序分片依据
    location    GEOGRAPHY(POINT, 4326),  -- GEOTEMPORAL_SERIES 必有，TIME_SERIES 无
    speed       DOUBLE PRECISION,        -- 业务指标列
    payload     JSONB
);
-- rid + series_id + timestamp 复合索引（按 rid 查轨迹 + 时序分片）
CREATE INDEX idx_timeseries_<...>_rid_time ON timeseries_<...> (rid, timestamp DESC);
```

**写入链路（ObjectIndexFunnel 统一）**：

| 场景 | 数据来源 | rid 分配 | 写入 |
|---|---|---|---|
| **静态/批量时序**（历史轨迹导入） | Iceberg scan_latest | 按 series_id 查 Doris 复用宿主对象 rid | ObjectIndexFunnel → TimescaleDB（带 rid）+ Doris（series_id 属性） |
| **流式实时时序**（Kafka 高频） | Kafka → Iceberg(append) → ObjectIndexFunnel 增量消费 | 同上 | ObjectIndexFunnel 增量写 TimescaleDB |

**rid 分配逻辑**：时序数据写入时，按 `series_id` 查 Doris idx 表拿宿主对象的 rid（`SELECT rid FROM idx WHERE <series_property> = ?`），复用该 rid 写超表。若宿主对象未建（边缘情况），rid 暂空，待宿主对象建好后由 ObjectIndexFunnel 回填（`UPDATE hypertable SET rid=? WHERE series_id=?`）。

**查询路径（无需中转 Doris）**：推理线从图遍历拿 rid 集，直接查 TimescaleDB `WHERE rid IN (...) AND timestamp BETWEEN ...`，返回轨迹点。rid 是跨引擎统一身份，不用先查 Doris 拿 series_id 再查超表。

**时序明细不进 Doris**：TimescaleDB 存时序明细（亿级行，按 timestamp 分片），Doris 只存对象的 series_id 属性引用（1 行）。两者数据形态不同，不互相复制。T+1 归档到 Iceberg/Doris 做冷存储（二期）。

### 6.6 VIRTUAL 联邦投影设计（ADR-021）

VIRTUAL 对象（不落地的外部源联邦代理）的图投影是模式 D 的核心。完整设计见 [`adr-021-virtual-graph-projection.md`](./adr-021-virtual-graph-projection.md)（决策权威源）+ [`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md)（工程落地权威源）。要点：

- **投影内容**：VIRTUAL 对象的**身份骨架**（rid + label + PK + title + indexed + `_virtual` + `_source_ref` + `_sync_tag`）进 Neo4j，全量属性永远走 Trino 联邦水合（零拷贝，不投影/不缓存/不落地）
- **rid 合成**：`ri.ontology.main.virtual-object.{ont}.{ot}.{pk}`（复用 `core/rid.py`，水合按 type 段分流已落地）
- **边来源**：外部源 FK 推导（`LinkType.foreign_key_property_api_name` → `Property.backing_column`，source 端优先 target 端兜底），非 Action（红线 9）
- **孤儿清理**：watermark + cleanup（cartography 范式），`_sync_tag` 标记本次投影，结束后删 `_sync_tag <> current` 的 VIRTUAL 节点（先建后删，无窗口期断链）
- **批量写入**：`UNWIND` + `CALL {} IN TRANSACTIONS`（Neo4j 5 原生，替代 deprecated 的 `apoc.periodic.iterate`）
- **触发**：`register_virtual_table` 异步 best-effort + admin rebuild API（不走 outbox，outbox 是 Action 语义）
- **为路径 ③' 留接口**：`_virtual` + `_source_ref` 是远期查询时联邦的探测点

**红线 9 例外声明**：VIRTUAL 身份骨架投影进 Neo4j 不是"业务写入"，是派生索引构建（只读投影），不违反红线 9 的精神（红线 9 禁止的是 Action 对 VIRTUAL 的业务 mutation）。详见 CLAUDE.md 红线 9。

---

## 七、查询编排设计（推理线核心）

> 配套 ADR：核心决策以本设计文档为权威源（ObjectSet IR 两层分离 + 推理线/SQL 线独立），尚未独立立项 ADR。

### 7.1 两层 IR 分离（C6）

| 层 | 载体 | 产出者 | 用途 |
|---|---|---|---|
| **传输层** | ObjectSet IR（pydantic JSON） | AG-UI Agent（ADR-015）/ MCP 工具调用方 / API 调用方 | 产出、传输、白名单护栏 |
| **执行层** | 原生 SQL 片段 | DataFrameQueryService 翻译 | 属性过滤编译 Trino SQL（查 Doris idx 表）；空间编译 PostGIS SQL；时序编译 TimescaleDB SQL |

调用方不直接接触原生 SQL，只产受控 ObjectSet IR。两层分离：安全（白名单护栏）+ 可靠（属性过滤走 Trino→Doris、空间走 PostGIS、时序走 TimescaleDB，避免 Ibis PostGIS backend bug）。

> **为何不用 Ibis**：早期调研拟用 Ibis TableExpr 作执行载体，但实测 Ibis PostgreSQL backend 对 PostGIS 支持不完整且有 bug（原生 point 列被误判 geospatial → `ST_AsEWKB` 编译失败，ibis#1786 / ibis#12007）。最终改用原生 SQL + 参数化下推，属性过滤走 Trino→Doris idx 表、空间走 PostGIS、时序走 TimescaleDB，详见 §7.4。

### 7.2 ObjectSet IR（对齐 Palantir 真实结构，C7）

```python
# core/schemas/object_set.py

class ObjectSetIR(BaseModel):
    """判别联合，对齐 Palantir ObjectSet。type 区分操作。LLM 产此 JSON。
    searchAround 是顶层 type（非 transform），对齐 Palantir SDK 真实结构。"""
    # 已实现 13 种 type（对齐 Palantir 15 种的 87%）
    type: Literal[
        "objectType", "static", "filter", "searchAround",
        "union", "intersect", "subtract",
        "aggregate", "select",
        "interfaceBase", "interfaceLinkSearchAround",
        "withProperties", "reference",  # 占位，NotImplementedError
    ]
    # objectType: {objectType: str, filters?: list[Filter], where?: WhereClause}
    # static: {objects: list[str]}
    # filter: {object_set: ObjectSetIR, filters?: list[Filter], where?: WhereClause}
    # searchAround: {link: str, object_set: ObjectSetIR, hops?: [min,max], direction?: "out|in|both"}
    # union/intersect/subtract: {object_sets: list[ObjectSetIR]}  # ≥2 个
    # aggregate: {object_set: ObjectSetIR, group_by?: list[str], aggregations: [{func,field,alias}]}
    # select: {object_set: ObjectSetIR, select_fields: list[str]}
    # interfaceBase: {interface: str}  # 跨类型起始集
    # interfaceLinkSearchAround: {interface, link, object_set, hops?, direction?}
    # 可选 order_by: [{field, desc}]  # 保证 cursor 分页稳定

class Filter(BaseModel):
    field: str       # 白名单校验（必须在本体 properties 内）
    op: Literal[  # 16 种属性算子（执行层按 op 分流：属性→Trino→Doris，空间→PostGIS，时序→TimescaleDB）
        "exactMatch", "notEqual", "in", "notIn", "range",
        "greaterThan", "lessThan", "contains", "startsWith", "endsWith",
        "withinDistance", "withinPolygon", "withinBoundingBox",
        "timeRange", "isNull", "isNotNull",
    ]
    value: Any | None = None
    coords: list[list[float]] | None = None  # 空间算子用
    center: list[float] | None = None  # withinDistance
    max_distance: float | None = None

# where 嵌套逻辑组合（对齐 Palantir SearchJsonQueryV2 and/or/not）
WhereClause = Union[Filter, AndClause, OrClause, NotClause]
```

**已实现的 type**（13/15，87%）：`objectType` / `static` / `filter` / `searchAround` /
`union` / `intersect` / `subtract` / `aggregate` / `select` /
`interfaceBase` / `interfaceLinkSearchAround` + 占位 `withProperties` / `reference`。

**未实现**（二期，需独立基础设施）：`nearestNeighbors`（向量索引）、
`asType`/`asBaseObjectTypes`（类型转换）、`methodInput`（Action 方法输入）。

**嵌套深度**：searchAround ≤ 3 层（对齐 Palantir 硬限）。

### 7.3 DataFrameQueryService（编排中枢）

```python
# services/object_set_executor.py

class DataFrameQueryService:
    """编排中枢。ObjectSet IR → 多引擎执行 + 防线 + 证据链。"""

    def __init__(
        self,
        graph_store: Neo4jGraphStore,
        geotime_store: GeoTimeStore,
        object_query_service: ObjectQueryService,  # 水合+属性过滤走 Trino→Doris
        metadata: PostgresMetaStore,
    ) -> None: ...

    async def execute(self, ir: ObjectSetIR, ontology: str) -> ReasoningResult:
        # 1. 递归解析 ObjectSet IR 树，逐步转换 rid 集
        rids = await self._eval_object_set(ir, ontology, EvidenceChain())

        # 2. 防线检查（包容式，C9）
        rids = self._apply_safeguards(rids)

        # 3. 水合全量属性（C12，走 Trino→Doris idx 表 / VIRTUAL 联邦外部源；不走 object_state）
        objects = await self.object_query_service.hydrate_by_rids(ontology, rids[:LIMIT_HYDRATE])

        # 4. 证据链快照
        record = await self._save_analysis_record(ir, evidence, objects)
        return ReasoningResult(objects=objects, evidence=record, ...)

    async def _eval_object_set(self, ir, ontology, evidence) -> list[str]:
        if ir.type == "objectType":
            # 起始对象集：从 Doris idx 表取该类型 rid（可带 filter）
            return await self._eval_object_type(ir, evidence)
        elif ir.type == "static":
            return ir.objects  # 直接给定 pk
        elif ir.type == "filter":
            # 过滤：属性走 Trino→Doris idx 表 / 空间走 PostGIS / 时序走 TimescaleDB
            base_rids = await self._eval_object_set(ir.object_set, ontology, evidence)
            return await self._eval_filter(ir.filters, base_rids, evidence)
        elif ir.type == "searchAround":
            # 图遍历：走 Neo4jGraphStore
            base_rids = await self._eval_object_set(ir.objectSet, ontology, evidence)
            return await self.graph_store.search_around(base_rids, ...)
```

### 7.4 执行层的下推策略（C5）—— 属性走 Trino→Doris，空间走 PostGIS，时序走 TimescaleDB

**实现**：`_eval_filter` 按 op 分流到不同引擎——属性算子下推 Trino 查 Doris idx 表、空间算子下推 PostGIS、时序算子下推 TimescaleDB。三类 filter **不在同一引擎**，无法合并成一条 SQL，而是按 rid 集在引擎间链式传递（应用层编排 + rid 集传递，红线 12）。

> **为何不用 Ibis**：早期调研拟用 Ibis TableExpr 作执行载体，后实测 Ibis PostgreSQL backend 对 PostGIS
> 支持有 bug（ibis#1786/#12007，原生 point 列被误判 geospatial → `ST_AsEWKB` 编译失败）。最终改用原生 SQL + 参数化。
> 代码中已无 `ibis` import，`pyproject.toml` 无 ibis 依赖。

```python
async def _eval_filter(self, filters, base_rids, evidence) -> list[str]:
    """filter 按 op 分流到不同引擎，rid 集在引擎间链式传递。"""
    candidate_rids = base_rids
    for f in filters:
        if f.op in SPATIAL_OPS:
            # 空间 → PostGIS：geo 表 LEFT JOIN + ST_DWithin/ST_Covers
            candidate_rids = await self._spatial_filter(f, candidate_rids, evidence)
        elif f.op == "timeRange":
            # 时序 → TimescaleDB：超表分片查询
            candidate_rids = await self._time_range_filter(f, candidate_rids, evidence)
        else:
            # 属性 → Trino→Doris idx 表（不走 PG object_state）
            candidate_rids = await self._attr_filter_trino(f, candidate_rids, evidence)
    return candidate_rids

async def _attr_filter_trino(self, f, candidate_rids, evidence) -> list[str]:
    """属性过滤下推 Trino 查 Doris idx 表（与水合同源同口径）。"""
    # 候选 rid 集注册 Trino 会话临时表或 WHERE pk IN (...) 分批
    # Doris idx 表主键是业务 PK，需 rid↔PK 映射（阻塞项，§2.1）
    pred, params = self._compile_attr_pred(f)  # 白名单字段 + 参数化值
    sql = f"""
        SELECT rid FROM {doris_idx_table}
        WHERE rid IN ({rid_placeholders}) AND {pred}
    """
    # Trino 执行（背后联邦到 Doris）
    return await self._engine.query(sql, params)
```

**关键设计点**：
- **属性 filter 与水合同源**：都走 Trino→Doris idx 表，保证过滤口径与返回的全量属性一致（不会出现"过滤命中但水合取不到"或反过来的错位）
- **三类 filter 不合并成一条 SQL**：属性(Trino/Doris)、空间(PostGIS)、时序(TimescaleDB)分属不同引擎，按 rid 集链式传递（红线 12：跨引擎禁止 Join，应用层编排）
- **属性/空间/时序 filter 编译为纯函数**（`_compile_attr_pred`/`_compile_spatial_pred`/`_compile_time_pred`），可单测
- **白名单护栏**：field 名必须在本体 properties 内（红线 8：元数据白名单校验），值参数化绑定

**searchAround 不走 SQL 下推**，走 Neo4jGraphStore（`UNWIND $rids` 参数化），
结果 rid 集回 Python 后链式传入下一个 filter 步骤。

**where 嵌套逻辑**（and/or/not）：`_compile_where` 递归编译。**注意 NOT 的 NULL 三值逻辑**——跨引擎 NOT 下推时，对可空字段用 `IS DISTINCT FROM` / `IS NOT DISTINCT FROM` 处理 NULL 语义，避免 `NOT (field = X)` 在 field 为 NULL 时返回 NULL 而非 true。

**Neo4j ↔ Trino/PG 正交边界**：Neo4j ↔ Python 传一次 rid 集（不可避免），
Trino/PG 侧各自内部全走参数化下推，不回 Python 逐条查询。

### 7.5 多引擎联动示例（供应链中断传导）

用户自然语言："供应商 S001 停产，3跳内受影响订单，且找出 300km 内替代供应商"

TextQL LLM 产 ObjectSet IR：
```json
{
  "type": "searchAround",
  "link": "supplies",
  "objectSet": {
    "type": "filter",
    "objectSet": {
      "type": "searchAround",
      "link": "supplies",
      "objectSet": {"type": "static", "objects": ["S001"]}
    },
    "filters": [{"field": "status", "op": "exactMatch", "value": "unfulfilled"}]
  }
}
```
（300km 内替代供应商由 Agent 第二轮 query_with_dataframe 调用，带 withinPolygon filter）

DataFrameQueryService 执行：
1. `static` → rid 集 [S001]
2. `searchAround(supplies)` → Neo4j 多跳受影响订单 rid 集
3. `filter(status=unfulfilled)` → Trino→Doris idx 表属性过滤（与水合同源口径）
4. `searchAround(supplies, reverse)` → Neo4j 反向找替代候选
5. 水合 → ObjectQueryService 走 Trino 查 Doris（MANAGED）/ 联邦外部源（VIRTUAL）取全量属性

### 7.6 流式处理

引擎间 rid 集用异步流传递，不一次性载入内存：
- **anyio 异步生成器**：每步产出 `AsyncIterator[str]`，下游异步消费
- **Polars LazyFrame**：ID 集合运算（去重/交集）用 Polars，比纯 Python 快
- **分批传递**：memtable/IN 子句分批 1000 一批

### 7.7 批量水合（禁止逐个查询）

> **调研结论**：Palantir `loadObjects` 用 **static objectSet（rid 数组）一次 POST 批量取数**，OSDK 文档明确“by rid 用 static objectSet”。OBDA（Ontop）的教训是逐列/逐行查询会产生 N-1 self join（性能灾难，ontop#800）。逐个查外部源是 N+1 查询反模式。

**Gaia 批量水合约束**：

| 场景 | 批量策略 | 禁止 |
|---|---|---|
| MANAGED（Trino→Doris） | rid 集注册临时表或 `WHERE pk IN (...)`，一次 SQL 取一批（≤ 1万/批） | 逐 rid 查 Doris |
| VIRTUAL（Trino 联邦外部源） | 同一批 rid 解析出 (ont,ot,pk) 后，按外部源表构造 `WHERE pk IN (...)` 批量查；多源则按源分组批量 | 逐 rid 查 Trino（N+1 灾难） |
| 超水合上限（1万） | 强制游标分页，cursor 续取 | 一次性载入 |

**实现待办**：`ObjectQueryService` 需补 `hydrate_by_rids(rids)`（MANAGED 走 Trino→Doris 批量）与 `hydrate_by_pks(ont, ot, pks)`（VIRTUAL 走 Trino 联邦批量）。早期实现 `_hydrate_virtual` 逐个查 Trino 为 N+1 反模式，须重构为批量。

### 7.8 下推方案的性能边界（§7.4 补充）

rid 集在引擎间传递的性能不是线性安全的：

| rid 集规模 | 属性过滤（Trino→Doris） | 空间过滤（PostGIS GiST） | 风险 |
|---|---|---|---|
| ≤ 1 万 | 毫秒~百毫秒 | 毫秒级 | 安全 |
| 1万~10万 | 百毫秒~秒级 | 百毫秒 | `rid IN (...)` 分批；Trino 联邦计划优化 |
| 10万~100万 | 秒级~十秒级 | 秒级 | 可能超防线三超时；须分批 |
| > 100万 | 不可接受 | 不可接受 | 防线二上限 100万 截断 + truncated |

**约束**：filter 步骤候选 rid 集超 1万 须分批下推（1万/批多次执行后取并集），不一次性灌 10万+ 进 `IN` 子句。**需压测验证** §13.2 性能目标（多引擎联动 < 5s）在 1万/10万/100万 rid 下的真实 P95，当前未压测（§13.2 性能目标标为待压测确认）。

---

## 八、性能与可靠性防线（C9）

### 8.1 防线哲学：包容式，不拒绝用户

用户总能得到结果（哪怕是部分），系统内部消化性能压力：

| 场景 | 拒绝式（弃） | 包容式（采用） |
|---|---|---|
| 无前置过滤 | 返回 400 | 自动注入默认边界（最近时间窗口 + 默认 3 跳），返回 + 提示 |
| 结果集超限 | 截断 | 截断 + `truncated=true` + 自动转游标分页 |
| 大起始集（>10万） | 转离线 | 在线返首批（1万）+ 截断 + truncated，**异步续算列二期**（MVP 不做后台续算任务） |
| 资源耗尽 | 503 | 排队等待（图遍历信号量限流，§8.4），超时报错+保留证据（C13） |

### 8.2 四道防线

**防线一：查询计划级——前置过滤 + 谓词下推 + 步骤剪枝**
- 无过滤自动注入默认边界（不拒绝）
- 谓词下推：属性下推 Trino→Doris、空间下推 PostGIS、时序下推 TimescaleDB；图过滤下推 Neo4j WHERE
- 步骤剪枝：不需要空间的步骤跳过 PostGIS

**防线二：数据量级（阈值对齐 Palantir，C9；2026-07-27 拆两行 + 下调 100 万，handoff D4/D7）**

| 步骤 | 传递内容 | 单步上限 | 超限处理 |
|---|---|---|---|
| 图遍历探索边对数 | 去重 (start, m) 边对数 | 100 万（C9，MVP 下调自 Palantir 1000 万） | 截断 + truncated + 游标 |
| 图遍历 → 下游 | 去重 rid 集 | 100 万（自然 ≤ 边对数，不单独截断） | 同上 |
| 属性过滤（Trino→Doris） → 下游 | rid 集 | 100 万 | 截断 + 告警 |
| 水合 → 用户 | 全量对象 | 1 万（Palantir 实践） | 强制游标分页 |

**防线三：超时与降级（C13，MVP 不降级）**

| 引擎 | 超时（只计本引擎执行） | MVP 超时处理 |
|---|---|---|
| Neo4j 图遍历 | 30s（可配） | 报错 + 保留已执行步骤证据 |
| 属性过滤（Trino→Doris） | 15s | 报错 + 保留证据 |
| PostGIS / TimescaleDB | 15s | 报错 + 保留证据 |

MVP 整体失败 + 保留证据（C13）。降级（Doris/Trino 切换、跳过空间过滤等）列二期。

**防线四：资源隔离与背压**
- 图遍历独立信号量限流（同时 ≤ 5 个多跳，可配），超出排队
- 内存监控：超阈值背压（排队不拒绝）
- 大起始集截断 + truncated（异步续算列二期，MVP 不做后台续算任务调度）

### 8.3 阈值依据（写进后续图推理 ADR）

| 防线项 | 阈值 | 依据 |
|---|---|---|
| 图遍历边对数上限 | 100 万 | MVP 下调自 Palantir 1000 万（handoff D4，控制风险） |
| 单次水合上限 | 1 万 | Palantir Functions 实践 |
| 多跳用原生 Cypher | 不用 APOC path.expand | Neo4j issue #56（APOC 内存缺陷） |
| 游标分页 | cursor + truncated | Neo4j GraphQL + Azure Resource Graph |

---

## 九、本体工具层扩展

### 9.1 工具族（C11）

| 工具 | 能力线 | 状态 |
|---|---|---|
| `query_with_sql` | SQL 线（现有） | 现有 |
| **`query_with_dataframe`**（新） | 推理线统一入口（ObjectSet IR → Trino→Doris + Neo4j + PostGIS + TimescaleDB） | 新增 |
| `traverse_link` / `exists_link` | 单跳精确控制 | 已落地（路标#3） |
| `find_paths` | 路径推理（源→目标最短路径，allShortestPaths Cypher） | 已落地（Phase 2d，提前实现） |

工具总数 20→22（+query_with_dataframe +find_paths）。`search_around` 不单独暴露（ObjectSet IR 的 searchAround 操作即是）；`find_paths` 因路径推理语义独立（需指定 source/target），单独暴露为工具 + REST 路由。

> **红线 11 对齐**：`query_with_dataframe` / `traverse_link` / `exists_link` / `find_paths` / `spatial-filter` / `series-query` 等 `/objects/*` 路由**只接受结构化 ObjectSet IR / 参数，不接受自然语言**（CLAUDE.md 红线 11）。NL→ObjectSet IR 的转换由 `/ai/agent`（AG-UI ReAct Agent，ADR-015）完成，Agent 内部调用这些工具。

### 9.2 query_with_dataframe 签名

```python
async def query_with_dataframe(
    ontology: str,
    object_set_ir: ObjectSetIR,          # 结构化 ObjectSet IR（白名单护栏）
    cursor: str | None = None,           # 分页游标（上页 next_cursor）
) -> dict:
    """图关联推理与时空分析的统一入口（对标 query_with_sql，推理线）。

    接收结构化 ObjectSet IR（由调用方或 AG-UI Agent 产出），
    DataFrameQueryService 执行（图遍历→空间/时序过滤→水合），
    返回统一对象集 + 证据链。

    与 query_with_sql 独立并行：SQL 线走 Doris/Trino，
    推理线走 Neo4j+PostGIS+TimescaleDB，属性过滤+水合走 Trino→Doris。

    **不接受自然语言**（红线 11）：NL→IR 转换由 /ai/agent（AG-UI ReAct Agent，ADR-015）完成。

    Returns {objects[], evidence_id, stats{steps, engines_used, timings}, truncated, next_cursor?}
    """
```

### 9.3 NL → ObjectSet IR 的转换入口

> **架构转向（ADR-015，2026-07-04）**：早期设计拟由 TextQL `intent_parser` 产 ObjectSet IR（含关键词路由 `object_set_parser`）。已废弃。当前架构：NL→ObjectSet IR 转换由 `/ai/agent`（AG-UI ReAct Agent）完成，Agent 每轮读画布 state 决策（0 对象自然止损），内部调用 `query_with_dataframe` / `traverse_link` / `find_paths` 等结构化工具。详见 [adr-015-agent-driven-graph-explore.md](./adr-015-agent-driven-graph-explore.md)。

现有 TextQL 五步流水线**不复用于推理线**（TextQL 只服务于 SQL 线 `query_with_sql`）。推理线的 schema 注入由 AG-UI Agent 的 prompt 构造完成：注入图结构（LinkType + 权重 + 时态）+ 空间属性 + 时序超表元数据 + ObjectSet IR 输出格式约束（§9.4 四层保障适用）。

**已删除组件**（不再存在）：`object_set_parser.py`、`explore_plan_parser.py`、`POST /query-nl`、`POST /explore-plan`、`should_route_to_object_set` 关键词路由、`usePlanExecutor` 一次性编排。

### 9.4 LLM 稳定输出四层保障（C6，附录 F）

| 层 | 机制 |
|---|---|
| Prompt 标准化 | 强制规则 + Few-Shot 样例 + 本体元数据上下文注入 |
| 输出清洗 | 正则截取 ```json``` + 去末尾逗号 |
| Pydantic 强校验 | 判别联合 + 枚举约束 + 白名单字段 + 嵌套深度 ≤ 3 |
| 自动纠错闭环 | 结构化报错 + 原始 JSON 回灌 LLM 重试（≤ 2 次） |

### 9.5 三入口暴露

遵循现有本体工具层范式（ADR-009）：`build_reasoning_toolset` 暴露 AG-UI，MCP 侧 `@mcp.tool` 包装 `_logic`，REST 路由薄包装。HITL：推理查询只读不审批。

---

## 十、风险评分与证据链（C10+C11 轻量版）

### 10.1 风险评分（不造新模型）

风险评分 = 对象的数值属性，由**定期 Action** 计算写入：
- 用户建 `RiskScore` 属性（DOUBLE）
- 用户建计算 Action（如 `recalculateRisk`），`submission_criteria` 定义规则（关联风险=跳数×权重、行为风险=偏离基线、外部风险=制裁名单）
- Action 定期执行，结果写 object_state 的 RiskScore
- `indexed=True` 时同步到 Neo4j，供遍历按风险剪枝

### 10.2 证据链快照（C11 轻量版）—— 当前仅存基本字段，血缘指针后续实现

每次推理查询生成 `AnalysisRecord`（§3.4）：
- `object_set_ir`：ObjectSet IR 快照（已实现）
- `result_summary`：各步引擎耗时 + 命中数 + truncated（已实现）
- `evidence_pointers`：**当前暂不实现血缘指针，仅存命中 rid 列表**。原设计拟存 `backing_mapping` 血缘指针，但 MANAGED 水合走 Trino→Doris（无持久化 backing_mapping）、VIRTUAL 走 Trino 联邦（更无），血缘指针指向的东西当前不存在。

**当前状态**：证据链快照能记录“查了什么 IR、用了哪些引擎、命中哪些 rid”，但**不能“展开看每跳关系来源、每对象原始数据定位符”**（原 §10.2 用户视角表述为远期目标，当前不实现）。

**后续实现（独立任务跟踪）**：全链路数据血缘反查、审计包导出、Principal 真实身份、evidence_pointers 真实血缘指针。当前暂不考虑。

---

## 十一、API 设计

### 11.1 新增路由（以实际 `routes/query/__init__.py` 为准）

```
# 推理线（结构化 ObjectSet IR / 参数，不吃 NL，红线 11）
POST /objects/{ontology}/query-dataframe    # query_with_dataframe：ObjectSet IR → 多引擎执行
POST /objects/{ontology}/traverse           # traverse_link（单跳精确控制）
POST /objects/{ontology}/exists-link        # exists_link（关系存在性检查）
POST /objects/{ontology}/find-paths         # find_paths（源→目标最短路径）
POST /objects/{ontology}/spatial-filter    # 独立空间过滤（PostGIS GiST）
POST /objects/{ontology}/series-query      # 独立时序查询（TimescaleDB 分片）
GET  /objects/{ontology}/analysis/{id}      # 查证据链快照

# Admin 重建投影（实际路由在 routes/admin.py，非 /admin/graph/*）
POST /admin/project/rebuild/{ont}/{ot}              # 按 ObjectType 重建 Neo4j+PostGIS 投影
POST /admin/project/rebuild-for-dataset/{dataset}   # 按 Dataset 重建投影
```

> **已移除**：`POST /object-set`（曾与 query-dataframe 重复，已合并为单一入口）。`/admin/graph/rebuild`、`/admin/geotime/rebuild-geo` 为设计初稿路径，实际未采用此命名。

### 11.2 请求/响应示例

```http
POST /objects/SupplyChain/query-dataframe
{
  "type": "searchAround",
  "link": "supplies",
  "objectSet": {
    "type": "filter",
    "objectSet": {
      "type": "searchAround",
      "link": "supplies",
      "objectSet": {"type": "static", "objects": ["S001"]}
    },
    "filters": [{"field": "status", "op": "exactMatch", "value": "unfulfilled"}]
  }
}

200 OK
{
  "objects": [{"rid":"...","api_name":"Order","props":{...}}, ...],
  "truncated": false,
  "next_cursor": null,
  "stats": {"steps": 2, "engines_used": ["graph","doris"], "timings": {"graph": 1.2, "hydrate": 0.8}},
  "evidence_id": "abc123"
}
```

> **NL 版本**：若用自然语言查询，调用 `/ai/agent`（AG-UI ReAct Agent），Agent 内部将 NL 转为上述 ObjectSet IR 后调用 `query_with_dataframe`。`/objects/*/query-dataframe` 本身不吃 NL（红线 11）。

**find_paths 请求/响应示例**：

```http
POST /objects/SupplyChain/find-paths
{
  "source_rid": "ri.ontology.main.object.aaa",
  "target_rid": "ri.ontology.main.object.bbb",
  "rel_types": ["supplies"],     // 可选，None=任意关系
  "max_depth": 5,              // 默认 5，避免爆炸
  "limit": 10                  // 返回路径上限，默认 10
}

200 OK
{
  "paths": [
    ["ri.ontology.main.object.aaa", "ri.ontology.main.object.ccc", "ri.ontology.main.object.bbb"]
  ],
  "truncated": false
}
```

> **find_paths 防线**：`max_depth` 默认 5、`limit` 默认 10，用 Neo4j `allShortestPaths` 只返回最短路径集（非全路径枚举）。全路径枚举 / 权重路径列为二期（§12.2）。返回 `list[list[str]]`（rid 序列），不含边属性；如需边详情，调用方按路径 rid 对再查 `traverse_link`。无分页游标（路径集本身有 limit 截断 + truncated 标记）。

---

## 十二、分期路线图

### 12.1 本期（MVP）— 落地状态

> 落地详情以 [`graph-reasoning-progress.md`](./graph-reasoning-progress.md) + [`implementation-status.md §十二`](./implementation-status.md) 为准。下表为设计侧里程碑。M0-M7 后端已基本完成，M5 架构转向（见 M5 行）。

| 里程碑 | 内容 | 验收 | 状态 |
|---|---|---|---|
| **M0 基础设施** | docker-compose（Neo4j + 一体 PG 镜像）+ Alembic 迁移 | Neo4j 可连；PostGIS/TimescaleDB 扩展可用 | ✅ |
| **M1 Graph Layer** | Neo4jGraphStore + GraphProjector + object_state/links 投影 | define 自动建图 schema；Action 写入同步投影 | ✅ |
| **M2 GeoTime Layer** | GeoTimeStore + PostGIS 空间表 + TimescaleDB 超表 | 静态空间投影；⚠️超表 rid 列待加（§6.5） | ✅ |
| **M3 ObjectSet IR + 编排中枢** | ObjectSet IR schema + DataFrameQueryService + **属性过滤/水合走 Trino→Doris** + 防线 | 多引擎联动查询跑通（供应链示例）；⚠️ 阻塞项未完成（§2.1）：ObjectIndexFunnel 改造 + Doris idx 加 rid 列 + hydrate_by_rids，未完成前水合/属性过滤不可上线 | ✅设计 / ⚠️阻塞项未完成 |
| **M4 工具与 API** | query_with_dataframe + traverse_link/exists_link/find_paths + 三入口 + 路由 | 工具可用；curl 冒烟通过 | ✅ |
| **M5 NL 入口** | ~~intent_parser 产 ObjectSet IR~~ → 架构转向：`/ai/agent` AG-UI ReAct Agent（ADR-015） | NL 经 Agent 调结构化工具 | ✅（转向） |
| **M6 证据链** | analysis_records + 证据链快照 + 查询接口 | 分析后可查证据链 | ✅ |
| **M7 风险评分** | 定期 Action 计算写 RiskScore | 风险评分可算可查 | 🟡 设计就绪 |

### 12.2 二期任务（架构预留，C11）

| 任务 | 本期预留 | 二期实现 |
|---|---|---|
| 前端 Vertex 式图探索 UI | API 支持完整（返回 paths） | 画布右键扩展/着色/模板/What-if |
| 地图组件 + 轨迹回放 | TimescaleDB 超表 + series_query 接口 | 前端地图叠加/轨迹回放/时空共现 |
| 全链路血缘审计 | AnalysisRecord 当前仅存 rid 列表，**血缘指针后续实现**（§10.2） | lineage 体系 + evidence_pointers 真实血缘 + 审计包导出 |
| 实体对齐 | object_state.id 主键稳定 | 手动合并 + 自动对齐(ML) |
| Function 抽象 | Action+submission_criteria 替代 | Palantir Function 一等公民 |
| ObjectSet 集合运算 | union/intersect/subtract **已实现**；nearestNeighbors 未实现（需向量索引） | nearestNeighbors + asType/methodInput 二期 |
| 图引擎抽象层 | 四条口子预留 | GraphStore Protocol + NebulaGraph |
| 降级策略 | MVP 整体失败+证据 | 多引擎降级 + 部分结果返回 |
| find_paths（路径推理） | **已实现**（Phase 2d，allShortestPaths Cypher + POST /find-paths，§9.1） | 权重路径 / 全路径枚举二期 |
| 时序链路落地（C3） | 架构与接口已定义（§5.3/§6.5），**真实链路未 live 验证** | ObjectIndexFunnel 增量消费 Iceberg→TimescaleDB（带 rid）+ 超表运维（压缩/retention/连续聚合），独立任务跟踪 |
| 水合+属性过滤走 Trino→Doris | **设计契约已定（§2.1/§7.4）**：水合与属性过滤都走 Trino→Doris idx 表（MANAGED）/ 联邦外部源（VIRTUAL），不走 PG object_state | **阻塞项**（§2.1）：① Doris idx 表加 rid 列 ② ObjectIndexFunnel 改造（§6.1）③ hydrate_by_rids ④ TimescaleDB 超表加 rid 列，推理线上线前必须完成 |
| VIRTUAL 批量水合 | 当前逐个查 Trino（N+1 反模式），**须改为批量**（§7.7） | hydrate_by_pks 批量化，独立任务跟踪 |
| Neo4j HA | 社区版单点（§5.5），**无 clustering/failover** | 企业版 / NebulaGraph / 推理线降级，远期评估 |

---

## 十三、验收指标

### 13.1 功能验收

| # | 场景 | 验收标准 |
|---|---|---|
| F1 | 多源数据建图 | define 含 LinkType 的本体 → Neo4j 自动建标签/边/约束 |
| F2 | Action 写入投影 | 创建对象+关系 → Neo4j 节点/边可见；PostGIS 空间对象可见 |
| F3 | 静态空间投影 | GEOPOINT 属性对象 → PostGIS 空间表有记录 + GiST 可用 |
| F4 | 时序流式接入 | Kafka 消息 → Iceberg(append) → ObjectIndexFunnel → TimescaleDB 超表有记录（带 rid）⚠️架构已定，待 ObjectIndexFunnel 改造后 live 验证（§5.3/§6.5） |
| F5 | query_with_dataframe | ObjectSet IR→多引擎执行，返回对象集+证据链（NL→IR 由 /ai/agent 完成，不在本端点）⚠️阻塞项未完成前水合走 Trino→Doris 不可上线（§2.1） |
| F6 | 多引擎联动 | 供应链中断传导示例：searchAround→属性过滤(Trino→Doris)→水合 完整跑通 |
| F7 | 时空联动 | "48h内进入区域X的实体" 查询正确（TimescaleDB 分片 + PostGIS GiST） |
| F8 | 防线包容 | 1000万+结果 → truncated + 游标续取（不拒绝） |
| F9 | 证据链 | GET analysis/{id} 返回 ObjectSet IR + 各步耗时 + 命中 rid 列表（**血缘指针后续实现，当前不返回**） |
| F10 | 两条线独立 | query_with_sql 走 Doris；query_with_dataframe 走 Neo4j+PostGIS+TimescaleDB，属性过滤+水合走 Trino→Doris |
| F11 | 风险评分 | 定期 Action 执行后 RiskScore 更新 |

### 13.2 性能验收

| 指标 | 目标 | 状态 |
|---|---|---|
| 3 跳图遍历 P95 | < 2s（百万节点级） | 待压测 |
| 多引擎联动查询 P95 | < 5s | 待压测（§7.8，阻塞项未完成前口径未定） |
| 空间范围查询 P95 | < 100ms（PostGIS GiST，候选 ≤ 10万） | 待压测 |
| 时序窗口查询 P95 | < 200ms（TimescaleDB 分片，百万点） | 待压测 |
| 投影写入延迟 | < 1s（Action 提交后可见，异步） | ✅ |

### 13.3 可靠性验收

| 指标 | 目标 | 当前状态 |
|---|---|---|
| Neo4j 宕机 | 推理线报错+保留证据；SQL 线（query_with_sql）不受影响 | ✅ 两线独立 |
| PostGIS 宕机 | 推理线报错；SQL 线不受影响 | ✅ 两线独立 |
| 投影器写入失败 | fail-tolerant 记日志，不阻塞 Action | ✅ fail-tolerant（不对账，由同步任务保障最终一致） |
| 数据重建 | rebuild_for_object_type 全量重建（ObjectIndexFunnel 从 Doris idx 读全量 rid + 属性重投影 Neo4j/PostGIS，不从 object_state） | ⚠️ 设计要求从 Doris 读；当前实现仍从 object_state，须随 ObjectIndexFunnel 改造（§6.1/§6.3） |
| Neo4j 社区版单点 | 单实例无 HA，宕机即推理线全挂 | ⚠️ 接受单点，靠定时备份 + 监控告警（详见 §5.5） |

---

## 十四、风险清单与反模式审查

### 14.1 技术风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| ~~Ibis PG 编译与 PostGIS/TimescaleDB 兼容~~ | ~~低~~ | **已消除**：弃用 Ibis，改原生 SQL（ibis#1786/#12007 bug） |
| 临时表大 rid 集性能 | 中 | 分批 1 万/批 + 上限截断（原 Ibis memtable 顾虑不再适用） |
| 阻塞项：ObjectIndexFunnel 改造 + Doris idx 加 rid 列 | 高 | rid 权威是 Doris idx 表，ObjectIndexFunnel 统一 rid 分配+四引擎扇出。未完成前推理线不可上线（§2.1/§6.1） |
| object_state 当查询源 | 高 | 已在设计层面纠正：查询/水合/属性过滤/rebuild 全走 Trino→Doris，object_state 仅 Action 写入路径暂存态（§2.1/§7.4/§6.3） |
| SeaTunnel 直写 Doris/超表（rid 缺失） | 高 | 已废弃：SeaTunnel 回归纯搬运（外部源→Iceberg），Doris/TimescaleDB 写入归 ObjectIndexFunnel（§6.2/§6.5） |
| LLM/Agent 产复杂嵌套 IR 准确率 | 中 | MVP 限单跳+属性+空间，多跳走 Agent 链式（每轮读画布 state） |
| Neo4j APOC 内存缺陷 | 中 | 不用 APOC 遍历，用原生 Cypher（C9） |
| 投影器一致性漂移 | 中 | fail-tolerant 记日志 + 重建工具（outbox 重试为远期目标） |
| LLM/Agent 产 ObjectSet IR 语义错误 | 中 | 白名单护栏 + 四层保障 + 纠错闭环 |

### 14.2 反模式审查清单

- [ ] 未使用单一 RELATED_TO 万能边（按业务语义拆分 LinkType）
- [ ] 边未存储大量业务属性（仅权重+时效+标记）
- [ ] 图遍历有前置过滤（不无过滤全图遍历）
- [ ] 实体状态变迁未新建节点
- [ ] 静态空间属性未建成 GTS（工厂地址用 GEOPOINT）
- [ ] 动态轨迹未存对象普通属性（车辆轨迹用 GEOTEMPORAL_SERIES）
- [ ] 跨引擎未写复杂 Join（应用层编排 + ID 集传递）
- [ ] Cypher 未散落到 Service 层（收口 Neo4jGraphStore）
- [ ] 时序数据经 ObjectIndexFunnel 写入（非 SeaTunnel 直写超表，§6.5）
- [ ] rid 未用 Neo4j 内部 id（用 Palantir RID 格式 `ri.ontology.main.object.{uuid}`）
- [ ] 推理线属性过滤未走 PG object_state（走 Trino→Doris idx 表，与水合同源口径，G6）
- [ ] 水合未走 PG object_state（走 Trino→Doris / Trino 联邦外部源，G6）
- [ ] rebuild 未从 object_state 读（从 Doris/Iceberg 全量读，§6.3）
- [ ] `/objects/*` 推理线路由未接受自然语言（G7 / CLAUDE.md 红线 11，NL 走 /ai/agent）

---

## 十五、与现有架构红线的关系

> **编号说明**：以下 G1~G7 是本特性新增约束的**文档内部编号**（G = Graph-reasoning 专属），与 CLAUDE.md 主红线表编号**完全独立**，避免与 CLAUDE.md 红线 11/12（语义不同）混淆。若要将这些决策提升为全局红线，需重新分配编号并入 CLAUDE.md。当前这些约束以本设计文档 + ADR-021 为权威源。

**本特性新增约束**（文档内部编号 G1~G7）：
- G1. 图/空间/时序数据均为派生副本，**Doris idx 表（object database）是 rid 权威源**（rid + PK + 全量属性同一行），Neo4j/PostGIS/TimescaleDB 是派生投影，可全量重建。PG object_state 仅 Action 写入路径暂存态，与查询无关
- G2. **跨引擎禁止 Join**，联动通过 DataFrameQueryService 应用层编排 + rid 集传递
- G3. **动态时序数据经 ObjectIndexFunnel 写 TimescaleDB**（Kafka→Iceberg append→Funnel→超表，带 rid 列）；不经 Action / 不经 object_state；时序明细不进 Doris（Doris 只存 series_id 属性引用）
- G4. **图遍历用原生 Cypher**，不用 APOC path.expand
- G5. **rid 采用 Palantir RID 格式**（`ri.ontology.main.object.{uuid}`），为迁移铺路
- G6. **两条查询线独立**：推理线执行层的引擎分工为 图遍历→Neo4j / 空间→PostGIS / 时序→TimescaleDB / **属性过滤+水合→Trino→Doris idx 表（MANAGED）或 Trino 联邦外部源（VIRTUAL）**。属性过滤与水合同源同口径走 Trino→Doris，不走 PG object_state
- G7. **`/objects/*` 推理线路由只吃结构化 ObjectSet IR，不吃自然语言**（对齐 CLAUDE.md 红线 11，NL→IR 走 /ai/agent）

**复用现有红线**：CLAUDE.md #1-#12 不变。Doris 仍是在线读主源；Iceberg 仍是全量写入入口（时序例外）；物理命名走 snake_case（`core/naming.py` 扩展 `graph_label`/`geo_table`/`timeseries_hypertable`）。

---

## 附录：决策对齐记录

本特性经多轮设计前对齐，核心决策契约见 §〇。关键决策点：

1. 图引擎：Neo4j（本期）+ 四条迁移口子（C1）
2. 时空存储：PostGIS+TimescaleDB 合并 GeoTimeStore（C2），静态/动态二分
3. 动态时序：Kafka→Iceberg(append)→ObjectIndexFunnel→TimescaleDB（带 rid，§6.5），不经 Action/object_state（C3）
4. 本体驱动分发：DataType 决定路由（C4）
5. 两条查询线独立：query_with_sql(Doris) / query_with_dataframe(Neo4j+PostGIS+TimescaleDB+Trino→Doris)，属性过滤+水合交汇于 Trino→Doris（C5+C12；原 Ibis 已弃用；属性过滤走 Trino→Doris 非 PG object_state）
6. 两层 IR：ObjectSet IR(Agent 产) → 原生 SQL 片段(执行)（C6；原 Ibis TableExpr 已弃用）
7. ObjectSet IR 顶层结构对齐 Palantir（C7）
8. 一致性：秒级最终一致 + sync 选项（C8）
9. 防线包容式 + Palantir 阈值（C9）
10. 模型克制度（C10）
11. 本期范围 + 二期任务（C11）
12. 容错：MVP 整体失败+证据（C13）
13. docker-compose：Neo4j 按需 + 一体 PG 镜像（C14）

## 附录：ADR 立项状态

本特性核心决策（图引擎选型 / 时空层 / ObjectSet IR 执行载体）目前以本设计文档为权威源，**尚未独立立项 ADR**。早期设计稿预留的 `adr-015/016/017` 编号已被其他特性占用：

- `adr-015` = agent-driven-graph-explore（前端 ReAct 探索，且废弃了本特性原 M3c 的 explore-plan）
- `adr-016/017` = 权限治理（Cedar + Better Auth）

已立项的相关 ADR：
- [`adr-021-virtual-graph-projection.md`](./adr-021-virtual-graph-projection.md)：VIRTUAL 对象图投影（模式 D）
- [`adr-015-agent-driven-graph-explore.md`](./adr-015-agent-driven-graph-explore.md)：NL→IR 由 AG-UI Agent 完成

**待办**：若需将图引擎/时空层/ObjectSet IR 三组核心决策正式立项为独立 ADR，建议新开 `adr-022-graph-reasoning-design`（合并三组决策，避免再与已占用编号冲突）。
