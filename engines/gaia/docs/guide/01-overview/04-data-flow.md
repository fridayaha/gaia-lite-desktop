# 数据流场景

> **读者**：架构师 / 集成开发者 / 需要理解数据怎么流转的读者
> **预计阅读**：12 min

开源版 Palantir 有六种核心数据流，覆盖了数据从外部系统进来、被查询、被修改、被追溯、被自然语言问、被图推理分析的全过程。每条流不是简单的"从 A 到 B"——背后都有关于"为什么要这样走而不是那样走"的权衡。

## 场景总览

<figure>

| # | 场景 | 核心组件 | 关键特征 |
|---|------|---------|---------|
| 1 | 新数据接入 | SeaTunnel→Iceberg→ObjectIndexFunnel→Doris/Neo4j/PostGIS | 唯一写入入口，物理到语义的升级 |
| 2 | 托管对象查询 | Doris 直出 | 核心读链路，Trino 降级 |
| 3 | 虚拟对象查询 | Trino 联邦 | 不落地，不写，没降级 |
| 4 | 时间旅行 | Trino+Iceberg 快照 | 不需要历史表，自带版本 |
| 5 | 自然语言查询 | TextQL 五步流水线 | LLM 写意图，系统写 SQL |
| 6 | 图关联推理 | ObjectSet IR 多引擎 | 自动分流，证据链可追溯 |

</figure>

## 场景 1：新数据接入

数据从外部系统进入开源版 Palantir，变成可以被业务语义查询的对象。

```
源端（数据库/Kafka/文件/时序库）
  → SeaTunnel 管道 → Iceberg（唯一写入入口）
  → Gravitino 注册物理资产
  → ObjectIndexFunnel 统一索引编排 → Doris（索引列）
  → 可选图/时空投影（Neo4j/PostGIS，按 capabilities 门控扇出）
```

**几个关键设计理由**：

- 为什么 Iceberg 是唯一写入入口？因为它提供 ACID。Doris 不提供。先写 Iceberg 再同步到 Doris，写操作是原子可见的。写入过程中查询不受影响。

- 为什么 Doris 同步不走 SeaTunnel？SeaTunnel 是无状态搬运工，无法在写 Doris 前「按业务主键查已有 rid 复用」，会导致 rid 缺失或不一致。所以 SeaTunnel 只负责把外部源数据搬进 Iceberg（外部源→Iceberg），从 Iceberg 往 Doris/Neo4j/PostGIS 的写入统一交给 `ObjectIndexFunnel`（Python 侧批量直连各引擎：rid 分配/复用 + 四引擎扇出）。

- 外部数据接入不经过 Action，不产生 outbox。所以图（Neo4j）和时空（PostGIS）的投影目前需要手动触发（`POST /admin/project/rebuild*`）。不是 bug——是设计：自动投影意味着每次导入都触发一次重建，对批量场景是灾难。手动触发给运维侧主动权。（SeaTunnel backfill 完成自动触发 ObjectIndexFunnel 的链路待接。）

## 场景 2：托管对象查询

最常见的读操作——查已经落地开源版 Palantir 的数据。

```
查询 → ObjectQueryService 判断 storage_type
  → MANAGED → Doris 直出（主路径）
      └─ Doris 挂了 → Trino 扫 Iceberg（降级）
  → VIRTUAL → 走场景 3
```

Doris、Iceberg、Trino 三者的分工：

<figure>

| | Doris | Iceberg | Trino |
|---|-------|---------|-------|
| 内容 | 主键+索引+热点属性 | 全量+历史 | 无存储 |
| 读 | 过滤直出 | 按 ID 返 | 联邦路由 |
| 写 | 只收同步 | **唯一入口** | 无 |
| 一致性 | 最终一致(秒级) | ACID | 读时态 |

</figure>

这个分离的回报：读性能不需要和数据完整性互相妥协。Doris 只存搜索需要的字段，轻、快。Iceberg 储存全量所有版本，但只参与降级和审计路径。

## 场景 3：虚拟对象查询

有些数据不适合搬进来——更新极高频（毫秒级传感器）、需实时一致性、或旧系统即将退役不值得迁移。`storage_type=VIRTUAL` 就是这些场景的答案：

```
查询 → ObjectQueryService（VIRTUAL）
  → Trino 直接查询外部数据源
  → 不落地开源版 Palantir，不缓存，不备份
```

代价：查询速度取决于外部源。无降级路径——外部源挂了直接 fail。VIRTUAL 对象禁止写入——写入不走开源版 Palantir 审计链路，不可追责。

## 场景 4：时间旅行

Iceberg 的 MVCC 让任意时刻的数据快照可查：

```
查询（指定时间/snapshot_id）→ Trino FOR VERSION AS OF → Iceberg snapshot
```

不需要额外维护历史表。常用于审计（"这个订单什么时候变成风险状态的"）、回溯（"上季度数据全貌"）、恢复误操作。

## 场景 5：自然语言查询（TextQL）

大模型和开源版 Palantir 协作最密切的路径：

```
自然语言问题
  → AI Agent 解析意图（LLM 把 NL 转为结构化 QueryIR）
  → 语义召回（在本体定义中匹配对象名和属性名）
  → Schema 注入（把匹配的本体定义精确注入 LLM context）
  → SQL 编译（SqlGlot 生成目标引擎 SQL——Doris/Trino/跨 catalog）
  → 执行 → 用 displayName 渲染结果给人看
```

为什么不让 LLM 直出 SQL？因为 LLM 不知道你的 Doris 表名、不知道哪个字段在哪个引擎、不知道跨 catalog 怎么写。给 LLM 的是"想清楚意图 + 系统帮你编译 SQL"——LLM 做语义理解（它擅长），系统做物理执行（它不该碰）。

关键：本体定义中的 displayName/apiName 双命名体系在这里发挥价值——displayName 让 AI"听懂"人话（用中文匹配"供应商名称"），apiName 让 AI"写出"机器可执行的查询（`supplierName`）。

## 场景 6：图关联推理与多维分析

基于 ObjectSet IR 的多引擎联动——这是开源版 Palantir 对齐 Palantir ObjectSet 的核心：

```
用户问题 → ObjectSet IR（结构化推理计划）
  → DataFrameQueryService 拆解 IR：
      属性过滤 → PG
      图遍历(searchAround/find_paths) → Neo4j
      空间过滤 → PostGIS
      时序查询 → TimescaleDB
      跨类型聚合/投影 → 关联表/Ibis
  → 水合（分批从 object_state 填属性，每批 5000）
  → 证据链记录 → 返回结果 + evidence_id
```

精妙之处：IR 树的每个节点根据类型自动选最合适的引擎。一个查询可能同时触发 4 个引擎，调用者只看一份结果。`evidence_id` 记录了每步的来源和中间数据——事后可以完整追溯"为什么是这个结果"。

## 深入

- 三层架构怎么配合：[架构总览](./01-palantir-and-gaia)
- 本体建模中 apiName/displayName 的设计逻辑：[本体体系](./03-ontology-system)
- Action 写入的 outbox 同步链路：[Action 闭环](../04-concepts/03-action-loop)
