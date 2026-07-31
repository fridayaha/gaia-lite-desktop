# 多源异构数据融合设计 - 连接器体系与实时数据管线

> **版本**: v1.1（评审决策已固化，进入实现阶段）
> **日期**: 2026-07-02
> **评审记录**: 2026-07-02 评审通过 7 个决策点，见 §附「评审决策固化」
> **对标系统**: Palantir Foundry Data Connection（连接器目录 + Capability 模型 + Virtual Table 联邦）
> **核心目标**: 在 Gaia 现有 6 层架构（Gravitino + SeaTunnel + Iceberg + Doris + Trino + PostgreSQL）上，发挥开源组件组合优势，实现多源异构数据的全域融合与实时管线，支撑本体建模的数据前置依赖。
>
> **前置文档**:
> - [数据层设计](./data-layer-design.md) — DataSource / Dataset / ObjectType 完整方案（v2.0）
> - [数据集与本体关联设计](./dataset-ontology-binding.md) — Managed / Virtual 术语体系与绑定链路（v1.0）
> - [实现状态路标](../architecture/implementation-status.md) — 各组件真实状态
> - [SeaTunnel Iceberg REST 互操作踩坑复盘](../engineer/seatunnel-iceberg-rest-interop-postmortem.md)
> - [Gravitino 1.3.0 升级记录](../bugfix/gravitino-1.3.0-upgrade.md)
>
> **工程原则**: 遵循 [CLAUDE.md](../../CLAUDE.md) 四条核心设计哲学 + 本文档 §二的设计准则。
>
> **本文件性质**: 设计基准（v1.1，评审决策已固化）。实现阶段遵循 G5：多检索官方文档 + 避坑 + Palantir 交互参照。

---

## 目录

- [〇、背景与问题](#〇背景与问题)
- [一、设计目标与非目标](#一设计目标与非目标)
- [二、设计准则（G1-G5，不可违反）](#二设计准则g1-g5不可违反)
- [三、三引擎能力对照与品类最优路径矩阵](#三三引擎能力对照与品类最优路径矩阵)
- [四、范围与优先级](#四范围与优先级)
- [五、连接器目录 UX 设计](#五连接器目录-ux-设计)
- [六、各品类接入方案](#六各品类接入方案)
  - [6.1 关系型数据库（MySQL/PG + 国产库 + 通用 JDBC 兜底）](#61-关系型数据库mysqlpg--国产库--通用-jdbc-兜底)
  - [6.2 湖仓格式（Hive/Delta/Hudi/Paimon 联邦源）](#62-湖仓格式hivedeltahudipaimon-联邦源)
  - [6.3 文件与对象存储（S3/MinIO/OSS/HDFS）](#63-文件与对象存储s3minioosshdfs)
  - [6.4 消息队列（Kafka）](#64-消息队列kafka)
  - [6.5 NoSQL（Elasticsearch）](#65-nosqlelasticsearch)
  - [6.6 中国云数仓（MaxCompute/ADB/GaussDB-DWS 等）](#66-中国云数仓maxcomputeadbgaussdb-dws-等)
- [七、实时管线与 CDC](#七实时管线与-cdc)
- [八、VIRTUAL 联邦扩展边界](#八virtual-联邦扩展边界)
- [九、与 Gaia 6 层架构的契合](#九与-gaia-6-层架构的契合)
- [十、安全与涉密场景适配](#十安全与涉密场景适配)
- [十一、验收标准](#十一验收标准)
- [十二、路标（三档明确不做项与未来扩展点）](#十二路标三档明确不做项与未来扩展点)
- [十三、调研参考索引](#十三调研参考索引)

---

## 〇、背景与问题

### 0.1 为什么需要这份设计

本体（Ontology）是 Gaia 的核心锚点，但**本体没有数据融合就无内容可用**——ObjectType 的属性必须映射到真实数据列才有业务意义。当前 Gaia 的数据接入能力仅覆盖 MySQL / PostgreSQL 两种关系库（`_JDBC_CONNECTOR_MAP` 硬编码 4 个条目：mysql/mariadb/postgresql/postgres），无法满足"多源异构数据全域融合"的平台定位。

用户期望对标 Palantir Foundry 的数据连接能力：
- 覆盖结构化、半结构化、非结构化、IoT、地理空间、业务系统等全品类数据源
- 批流一体实时管道、数据清洗/版本/质量治理
- **物理数据不迁移、逻辑统一视图**（联邦式融合，高安全场景适配性强）
- 动态增量更新，新数据秒级映射进本体

### 0.2 本设计要解决什么

1. **明确范围与优先级** —— 不复刻 Palantir 的 200+ 连接器，而是基于 SeaTunnel+Gravitino+Iceberg+Trino 的组合优势，按"应用广 + 成熟稳定 + 通用（一支撑类）+ 有潜力"筛选本期落地的品类（§四）。
2. **连接器目录 UX** —— 连接器多了之后必须有目录，参考 Palantir 的分品类卡片 + 能力标签 + 图标简介，给用户简单（§五）。
3. **各品类接入方案** —— 每个入选数据源的三引擎原生支持情况、最佳实践、配置指南、避坑（§六）。
4. **实时管线与 CDC 破局** —— CDC 走 SeaTunnel CDC source → Iceberg（spike 已 live 验证通过，§七）；数据进 Iceberg 后由 ObjectIndexFunnel 同步到 Doris（不经 SeaTunnel）。
5. **VIRTUAL 联邦扩展边界** —— 明确哪些品类能走联邦不落地、哪些只能落地，按品类一刀切（§八）。

### 0.3 本设计不解决什么

- **对话式本体建模**（AIAssistant 多轮对话）—— 独立工作项，见 implementation-status §五。
- **Doris 索引加速深化**（向量检索、IVF ANN 调优）—— 已有 ADR-012 TextQL 覆盖。
- **Action 闭环的内部 CDC**（PG object_state → Iceberg/Doris）—— 原内部 CDC 模板（`create_pg_to_kafka_pipeline` / `create_kafka_to_doris_pipeline` / `create_action_cdc_pipeline`）**已于 2026-07-08/10 删除（去 SeaTunnel 化）**，object_state 同步改 outbox 驱动（INDEX/ARCHIVE effect）。本文档聚焦**外部数据源 CDC**。
- **涉密定制连接器** —— 走开源组合 + 权限下推 + VIRTUAL 不搬迁天然适配，不做专用连接器（§十）。

---

## 一、设计目标与非目标

### 1.1 目标

| # | 目标 | 衡量标准 |
|---|------|---------|
| O1 | 连接器覆盖从 2 种扩展到覆盖 6 大品类 | 关系库（含国产）/ 湖仓格式 / 文件对象存储 / 消息队列 / NoSQL / 中国云数仓 均有可用路径 |
| O2 | 每个连接器在目录页有图标、简介、能力标签 | 用户无需看文档即可判断该连接器能做什么 |
| O3 | VIRTUAL 联邦路径明确（关系库/湖仓/Kafka） | 不搬迁即可查询，Trino 计算下推 |
| O4 | CDC spike 方案可验证 | 路径 a（CDC → Iceberg → Doris）有明确的验证步骤与成功/失败判定 |
| O5 | 与现有 6 层架构零侵入契合 | 不新增重型抽象层，复用 GravitinoRegistry / SeaTunnelEngine / TrinoQueryEngine |

### 1.2 非目标（明确不做）

- ❌ 自研连接器框架 / SPI 插件体系（违反 G4）
- ❌ 复刻 Palantir 200+ 连接器（违反 G1）
- ❌ Oracle / SQL Server 专用适配（用户明确不做，聚焦中国头部）
- ❌ IoT 工业协议（OPC-UA / OSI PI）—— 三引擎全不覆盖，违反 G2
- ❌ 时序数据库（InfluxDB / IoTDB）—— 场景特定，等真实需求触发
- ❌ SaaS 专用连接器（Salesforce / SAP / Jira）—— HTTP 兜底已能覆盖大部分
- ❌ 地理空间影像专用连接器 —— 走 PG-JDBC 子路径 + 自定义空间函数
- ❌ Snowflake / BigQuery / Redshift —— 全球场景，聚焦中国头部云数仓

---

## 二、设计准则（G1-G5，不可违反）

这五条准则由用户在讨论中明确，所有后续决策必须过这五条筛子。

| # | 准则 | 落地为约束 |
|---|------|-----------|
| **G1** | 不复刻 200+ 连接器 | 不自研连接器框架、不建重型 Connector Registry 抽象；目标是"用好 SeaTunnel+Gravitino+Iceberg+Trino 的组合优势"，覆盖度由开源原生能力决定，而非自己堆连接器数量 |
| **G2** | 范围三筛子：应用广 + 成熟稳定 + 通用（一支撑类）+ 有潜力 | 每个入选品类必须同时满足"覆盖一类数据形态 + 引擎原生支持 + 生产就绪（GA/Beta）"。淘汰小众/Alpha/单一厂商协议 |
| **G3** | 二八原则定优先级 | 20% 品类覆盖 80% 真实场景，先做这 20% 做深做透，其余进路标 |
| **G4** | 不过度抽象、直接组合开源成熟方案 | 禁止：重型 SPI 框架、连接器插件体系、自研联邦中间层。允许：配置驱动 + 适度的 provider 映射表（如现有 `_JDBC_CONNECTOR_MAP`）+ 直接复用 SeaTunnel/Gravitino/Trino 原生能力 |
| **G5** | 实现时多检索 + 多看本项目开源方案最佳实践/避坑 + Palantir 交互 | 落地阶段必须先查 SeaTunnel/Gravitino/Trino 官方文档与已知坑、读本项目 postmortem（`docs/bugfix/`、`seatunnel-iceberg-rest-interop-postmortem.md`）、参考 Palantir Foundry 数据连接 UI |

**准则之间的优先级**：G2（范围筛子）是入门门槛，G3（二八）是排序依据，G4（不过度抽象）是实现约束，G1（不复刻）是规模天花板，G5（多检索）是过程要求。当冲突时，G2 > G3 > G4 > G1。

---

## 三、三引擎能力对照与品类最优路径矩阵

### 3.1 三个引擎的分工

| 引擎 | 定位 | 在数据融合中的角色 |
|------|------|------------------|
| **Gravitino 1.3.0** | 联邦元数据湖（Metadata Lake） | 元数据纳管：关系库 JDBC Catalog、湖仓格式 Catalog、Kafka Topic Catalog、Fileset Catalog。**不存数据，只管元数据与访问入口**。权限下推到源系统。 |
| **SeaTunnel 2.3.13** | 批流一体数据集成框架 | 数据搬运：100+ 连接器 Source/Sink，全量/增量/CDC 三模式。**把外部数据搬进 Iceberg 成 MANAGED 托管表**。 |
| **Trino 478** | 分布式 MPP 联邦 SQL 查询引擎 | 联邦查询：30+ 连接器，计算下推到源端，**不搬迁数据实时查询**（VIRTUAL 路径）。跨源 Join。 |

### 3.2 关键约束：VIRTUAL 联邦强依赖 Gravitino 纳管

当前 Gaia 的 VIRTUAL 虚拟表实现 = `Gravitino JDBC Catalog + Trino Gravitino Connector`（见 `dataset-ontology-binding.md` §3.2）。要让某品类走 VIRTUAL 联邦，**前提是 Gravitino 能纳管该数据源的元数据**。

但 Gravitino 1.3.0 原生纳管范围有限：
- ✅ 关系库 JDBC（mysql/postgresql/clickhouse/doris/starrocks/oceanbase + 通用 JDBC 扩展）
- ✅ 湖仓格式（hive/iceberg/hudi/paimon/delta，1.2.0 起 Generic Lakehouse Catalog 统一纳管）
- ✅ Kafka（Topic 元数据）
- ✅ Fileset（S3/GCS/OSS/Azure/MinIO/HDFS/本地）
- ❌ NoSQL（Mongo/Redis/ES/Cassandra）—— 不纳管
- ❌ 时序（InfluxDB/IoTDB）—— 不纳管
- ❌ SaaS / HTTP —— 不纳管

### 3.3 品类最优路径矩阵

把三引擎能力叠加，每个品类的最优落地路径如下（✅=原生支持，⚠️=部分/有限制，❌=不支持）：

| 数据品类 | Gravitino 纳管 | SeaTunnel 搬运 | Trino 联邦下推 | **最优路径** | **落地形态** |
|---|---|---|---|---|---|
| 关系库 JDBC（MySQL/PG/国产） | ✅ jdbc-* + 通用 | ✅ 30+ + CDC | ✅ 完善下推 | **VIRTUAL 联邦** 或 **MANAGED+CDC** 任选 | 双形态 |
| 湖仓格式（Hive/Iceberg/Delta/Hudi/Paimon） | ✅ 5 Catalog | ✅ Source+Sink | ✅ 时间旅行 | 双引擎对齐，Iceberg 已是主存储 | 联邦源 / 主存储 |
| 文件/对象存储（S3/MinIO/OSS/HDFS） | ✅ Fileset | ✅ 多介质多格式 | ✅（湖仓底层） | **MANAGED 落地**（Fileset 管位置 + SeaTunnel 读内容 → Iceberg） | 落地 |
| Kafka | ✅ Topic 元数据 | ✅ Source+Sink | ✅ Topic→表 | **VIRTUAL**（Trino 实时消费）或 **MANAGED 流式落地** | 双形态 |
| 其他 MQ（Rocket/Pulsar/Rabbit） | ❌ | ✅ | ❌ | **只能 MANAGED 落地** | 落地 |
| NoSQL（ES） | ❌ | ✅ | ⚠️ 限制多 | **MANAGED 落地为主**，Trino 联邦仅兜底 | 落地 |
| 时序（Influx/IoTDB） | ❌ | ✅ | ❌ | **只能 MANAGED 落地** | 落地（路标） |
| SaaS / HTTP | ❌ | ✅ HTTP 兜底 | ❌ | **MANAGED 落地** | 落地（路标） |
| IoT 工业协议（OPC-UA/OSI PI） | ❌ | ❌ | ❌ | **三者都不覆盖** | 不做（路标） |
| 中国云数仓（MaxCompute/ADB/GaussDB-DWS） | ⚠️ PG 内核的可纳管 | ✅ JDBC | ✅ PG 兼容 | **VIRTUAL 联邦**（PG 内核的）或 **MANAGED 落地** | 双形态/落地 |

### 3.4 矩阵揭示的设计张力（已在讨论中决策）

**张力 1：NoSQL/时序/SaaS 的 VIRTUAL 联邦路径**
- Trino 能联邦查询 ES/Mongo/Redis/Cassandra，但**这些不在 Gravitino 纳管范围**。
- **决策（Q2 已确认）**：按品类一刀切——NoSQL/时序/SaaS 一律 MANAGED 落地，不为它们解耦 Trino 直连绕过 Gravitino。理由：G4（不过度抽象），解耦会破坏 6 层架构纯洁度，且 NoSQL 联邦查询发挥不出 NoSQL 自身优势（如 ES 全文检索）。

**张力 2：IoT 工业协议三引擎全不覆盖**
- OPC-UA/OSI PI 三个引擎都没有原生连接器。
- **决策**：明确不做，违反 G2（成熟稳定）。留 SPI 扩展点说明（§十二）。

**张力 3：实时增量"秒级映射进本体"**
- SeaTunnel CDC 原生支持 MySQL/PG/Oracle/SQLServer/MongoDB/TiDB/OpenGauss，秒级延迟。
- 但当前 Gaia 受 SeaTunnel 2.3.13 限制（postmortem 记录的 Iceberg REST Catalog 互操作问题，已证伪为配置层问题）。
- **决策（Q3/Q4 已确认）**：本期维持 `sync_now` 批量同步为生产路径，同期并行 CDC spike（路径 a：CDC → Iceberg → Doris）。spike 成功 → 接入主线；失败 → 回落路标。

---

## 四、范围与优先级

### 4.1 一档（二八里的"二"，本期做深）

| # | 品类 | 入选理由（过 G2 三筛子） | 本期具体数据源 |
|---|------|------------------------|--------------|
| 1 | **关系库 JDBC** | 应用最广 + 三引擎全原生 GA + 通用 JDBC 覆盖一类 | MySQL、PostgreSQL（已通）+ **国产库（本期）：OpenGauss / GaussDB / TiDB**（均有 SeaTunnel 原生 CDC）+ 通用 JDBC 兜底。OceanBase/达梦/金仓进路标 |
| 2 | **湖仓格式** | 湖仓生态核心 + 三引擎全原生 + Iceberg 已是主存储 | Iceberg（主存储，已通）+ Hive / Delta / Hudi / Paimon 作为可接入联邦源 |
| 3 | **文件与对象存储** | 数据落地主力 + 三引擎原生 + Fileset+SeaTunnel 组合成熟 | S3、MinIO（RustFS）、云对象存储 OSS、HDFS + Parquet/CSV/JSON/Avro/ORC |

### 4.2 二档（二八里的"八"前段，本期做但投入小）

| # | 品类 | 入选理由 | 本期具体数据源 |
|---|------|--------|--------------|
| 4 | **消息队列** | 实时场景高频 + Kafka 三引擎全原生 | **Kafka**（VIRTUAL 联邦 + 落地双通道）。RocketMQ/Pulsar/Rabbit 进路标 |
| 5 | **NoSQL** | 搜索类代表 + SeaTunnel 原生 + 企业高频 | **Elasticsearch**（落地为主，Trino 联邦兜底）。Mongo/Redis/Cassandra/HBase 进路标 |
| 6 | **中国云数仓** | 中国头部企业高频 + JDBC/PG 兼容可复用现有通道 | **AnalyticDB PostgreSQL（ADB-PG）、GaussDB DWS**（PG 内核，复用 PG 通道）+ **MaxCompute**（独立 JDBC，路标）+ TDSQL / CDW（路标） |

### 4.3 三档（路标 / 明确不做）

| # | 品类 | 状态 | 理由 |
|---|------|------|------|
| 7 | 时序数据库（InfluxDB/IoTDB/OpenTSDB） | 路标 | 场景特定（IoT/监控），等真实需求触发。SeaTunnel 原生支持，接入成本低 |
| 8 | SaaS 专用（Salesforce/SAP/Jira/Slack） | 路标 | SeaTunnel 通用 HTTP 连接器已能覆盖大部分 REST API 场景，不做专用连接器 |
| 9 | IoT 工业协议（OPC-UA/OSI PI/AWS IoT Core） | 不做 | 三引擎全不覆盖，违反 G2。留 SPI 扩展点说明 |
| 10 | 地理空间影像（ArcGIS/PostGIS/卫星影像） | 不做专用 | 走 PG-JDBC 子路径（PostGIS）+ 自定义空间函数；卫星影像走文件落地 |
| 11 | 涉密定制 | 不做专用 | 靠 G4 开源组合 + 权限下推 + VIRTUAL 不搬迁天然适配（§十） |
| 12 | Oracle / SQL Server | 不做 | 用户明确不做，聚焦中国头部国产库 |
| 13 | Snowflake / BigQuery / Redshift | 不做 | 全球场景，聚焦中国头部云数仓 |
| 14 | 其他 MQ（Rocket/Pulsar/Rabbit） | 路标 | SeaTunnel 原生支持，等真实需求触发 |
| 15 | 其他 NoSQL（Mongo/Redis/Cassandra/HBase） | 路标 | SeaTunnel 原生支持，等真实需求触发。ES 验证模式后扩展成本低 |

### 4.4 优先级排序（按 G3 二八原则）

```
P0（本期必做，做深做透）:
  1. 国产库 JDBC 适配（OpenGauss/GaussDB/TiDB — 均有 SeaTunnel 原生 CDC）
  2. 通用 JDBC 兜底（任意 JDBC 兼容库，含未列出的国产库/小众库）
  3. 连接器目录 UX（分品类卡片 + simple-icons/官网 favicon 图标 + 能力标签）
  4. 文件/对象存储接入（S3/MinIO/OSS + 多格式）
  5. CDC spike（独立前置任务，见 §七）

P1（本期做，投入小）:
  5. 湖仓格式联邦源（Hive/Delta/Hudi/Paimon 经 Gravitino 纳管）
  6. Kafka 接入（VIRTUAL 联邦 + 落地双通道）
  7. Elasticsearch 接入（落地为主）
  8. 中国云数仓（ADB-PG / GaussDB-DWS，复用 PG 通道）

P2（本期 spike，成功则接入）:
  9. CDC 路径 a 验证（外部数据源 CDC → Iceberg → Doris）

路标（不在本期）:
  时序 / SaaS / 其他 MQ / 其他 NoSQL / IoT 工业协议 / MaxCompute 专用
```

---

## 五、连接器目录 UX 设计

### 5.1 设计参照：Palantir Foundry Data Connection

调研 Palantir 官方文档（`palantir.com/docs/foundry/data-connection/`、`available-connectors/`）确认的交互模式：

1. **新建 Source 页面**：右上角 "+ New Source" → 进入连接器选择页
2. **连接器卡片网格**：每个连接器一张卡片，含图标 + 名称 + 支持的能力标签
3. **双向搜索**：可按连接器名称搜索，也可按能力搜索（如搜 "virtual" 列出所有支持虚拟表的连接器）
4. **每个连接器独立文档页**（`/docs/foundry/available-connectors/<connector>/`）结构：
   - 顶部一句话简介
   - **Supported capabilities** 表格（每项 🟢 Generally available / 🟡 Sunset / 状态）
   - Setup 步骤
   - Connection details 表格（Option / Required / Description）
   - Authentication 方式表格
   - Networking 说明
   - Virtual tables 子能力表格（如适用）
   - Privileges 表格
   - Data model 注意事项（类型映射陷阱）

5. **三层连接器成熟度**（Palantir Design Patterns 总结）：
   - **Native** —— 专用连接器，能力最全（如 SAP/Salesforce/Kafka）
   - **Generic JDBC** —— 通用 JDBC 驱动覆盖所有兼容 JDBC 的系统（Palantir 一次性新增 150+ JDBC 源）
   - **REST API / External transforms** —— 兜底，对接无 JDBC 的系统

### 5.2 Gaia 的连接器目录形态

**复用现有 `CONNECTOR_META` 扩展**（G4：不过度抽象）。当前前端 `DataSourceForm.tsx` 已有 `CONNECTOR_META: Record<string, {icon, label, port}>`，Step 1 已是卡片网格。扩展方案：

#### 5.2.1 扩展 `CONNECTOR_META` 结构

```typescript
// src/web-ui/src/components/DataSourceForm.tsx（或抽取到 constants/connectorCatalog.ts）

interface ConnectorMeta {
  // ── 基础展示（给用户简单）──
  icon: string;              // simple-icons 品牌图标 SVG（国产库/无品牌图标的用官网 favicon 补齐）
  label: string;             // 显示名 "MySQL" / "OpenGauss"
  description: string;       // 一句话简介 "开源关系型数据库，支持 CDC 实时同步"
  category: ConnectorCategory;  // 品类分组
  maturity: 'GA' | 'Beta' | 'Alpha';  // 成熟度标签

  // ── 能力标签（对标 Palantir capability）──
  capabilities: Capability[];  // ['explore', 'batch_sync', 'cdc', 'virtual_table']

  // ── 配置表单元数据 ──
  defaultPort: string;
  configSchema: ConfigField[];  // 该连接器的配置字段定义
  jdbc?: {
    provider: string;          // Gravitino provider "jdbc-mysql" / "jdbc-postgresql"
    driver: string;            // JDBC driver 类名
    urlScheme: string;         // "mysql" / "postgresql" / "opengauss"
  };
}

type ConnectorCategory =
  | 'relational'      // 关系型数据库
  | 'lakehouse'       // 湖仓格式
  | 'file_object'     // 文件与对象存储
  | 'messaging'       // 消息队列
  | 'nosql'           // NoSQL
  | 'cloud_warehouse'; // 云数仓

type Capability =
  | 'explore'         // 探索 schema
  | 'batch_sync'      // 批量同步
  | 'cdc'             // 增量 CDC
  | 'virtual_table'   // VIRTUAL 联邦不落地
  | 'streaming_sync'; // 流式同步
```

#### 5.2.2 目录页布局（分品类 + 搜索 + 能力过滤）

```
┌─ 数据源目录 ──────────────────────────────────────────────────────┐
│  🔍 [搜索连接器名称或能力...]   能力: [☐ 虚拟表] [☐ CDC] [☐ 批量] │
│                                                                    │
│  ── 关系型数据库 ──────────────────────────────────────────────  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ 🐬 MySQL  │ │ 🐘 PG    │ │ 🔵 OG    │ │ 🟢 Gauss │ ...        │
│  │ 关系库    │ │ 关系库    │ │ 国产库    │ │ 国产库    │            │
│  │ [探索][批]│ │ [探索][批]│ │ [探索][批]│ │ [探索][批]│            │
│  │ [CDC][VT]│ │ [CDC][VT]│ │ [CDC]    │ │ [CDC]    │            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│                                                                    │
│  ── 湖仓格式 ──────────────────────────────────────────────────  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ 🧊 Iceberg│ │ 🐝 Hive  │ │ 🔺 Delta │ │ 🪶 Hudi  │            │
│  │ 主存储    │ │ 联邦源    │ │ 联邦源    │ │ 联邦源    │            │
│  │ [VT]     │ │ [VT]     │ │ [VT]     │ │ [VT]     │            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│                                                                    │
│  ── 文件与对象存储 ────────────────────────────────────────────  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ 🪣 S3     │ │ 🟠 MinIO │ │ ☁️ OSS   │ │ 📁 HDFS  │            │
│  │ 对象存储  │ │ S3兼容   │ │ 云对象存储│ │ 文件系统  │            │
│  │ [探索][批]│ │ [探索][批]│ │ [探索][批]│ │ [探索][批]│            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
│                                                                    │
│  ── 消息队列 ── ── NoSQL ── ── 云数仓 ──（折叠/展开）─────────  │
└────────────────────────────────────────────────────────────────────┘
```

#### 5.2.3 连接器详情面板（选中卡片后）

参考 Palantir Snowflake 连接器页结构，每个连接器选中后展示：
- 图标 + 名称 + 一句话简介 + 成熟度徽章
- **Supported capabilities** 表格（每项 🟢 GA / 🟡 Beta / 状态）
- **Connection details** 表格（配置字段：host/port/database/credentials 等）
- **避坑提示**（该连接器的已知坑，如 openGauss 驱动冲突、ES multi-fields 不可查）
- **数据类型映射**（源类型 → Gaia DataType，参考 Palantir Snowflake 的 Data model 段）

### 5.3 后端 `CAPABILITY_MAP` 同步扩展

当前 `core/schemas/datasource.py` 的 `CAPABILITY_MAP` 只覆盖 8 种 connector_type。需同步扩展到所有入选品类，并增加 `virtual_table` 能力标记：

```python
# 现有（保留）
CAPABILITY_MAP = {
    "mysql": ["explore", "batch_sync", "cdc", "virtual_table"],
    "mariadb": ["explore", "batch_sync", "cdc", "virtual_table"],
    "postgresql": ["explore", "batch_sync", "cdc", "virtual_table"],
    "postgres": ["explore", "batch_sync", "cdc", "virtual_table"],
    # 国产库（新增，CDC 取决于 SeaTunnel 是否原生支持）
    "opengauss": ["explore", "batch_sync", "cdc", "virtual_table"],
    "gaussdb": ["explore", "batch_sync", "cdc", "virtual_table"],
    "tidb": ["explore", "batch_sync", "cdc", "virtual_table"],
    "oceanbase": ["explore", "batch_sync", "virtual_table"],  # CDC 走 OMS，非 SeaTunnel 原生
    "dameng": ["explore", "batch_sync", "virtual_table"],
    "kingbase": ["explore", "batch_sync", "virtual_table"],
    # 湖仓格式（新增，VIRTUAL 联邦为主）
    "iceberg": ["explore", "virtual_table"],
    "hive": ["explore", "batch_sync", "virtual_table"],
    "delta": ["explore", "virtual_table"],
    "hudi": ["explore", "virtual_table"],
    "paimon": ["explore", "virtual_table"],
    # 文件/对象存储（新增）
    "s3": ["explore", "file_sync"],
    "minio": ["explore", "file_sync"],
    "oss": ["explore", "file_sync"],
    "hdfs": ["explore", "file_sync"],
    # 消息队列
    "kafka": ["explore", "streaming_sync", "virtual_table"],
    # NoSQL
    "elasticsearch": ["explore", "batch_sync"],  # 落地为主，无 virtual_table
    # 中国云数仓
    "analyticdb_pg": ["explore", "batch_sync", "virtual_table"],  # PG 内核
    "gaussdb_dws": ["explore", "batch_sync", "virtual_table"],    # PG 内核
    "maxcompute": ["explore", "batch_sync"],  # 独立 JDBC，无 VIRTUAL（路标）
}
```

> **注意**：`virtual_table` 能力标记表示"该品类技术上可走 VIRTUAL 联邦"，但不强制——用户创建对象时仍可选 MANAGED 落地。详见 §八。

---

## 六、各品类接入方案

> 每个品类包含：原生支持情况、成熟度、最佳实践、配置指南、避坑。信息均经联网调研核实（§十三）。


### 6.1 关系型数据库（MySQL/PG + 国产库 + 通用 JDBC 兜底）

#### 6.1.1 原生支持情况

| 数据源 | SeaTunnel 2.3.13 | SeaTunnel CDC | Gravitino 1.3.0 | Trino 478 | 成熟度 |
|--------|------------------|---------------|-----------------|-----------|--------|
| MySQL | ✅ Source+Sink | ✅ Binlog CDC | ✅ jdbc-mysql | ✅ mysql connector | GA |
| PostgreSQL | ✅ Source+Sink | ✅ WAL CDC | ✅ jdbc-postgresql | ✅ postgresql connector | GA |
| OpenGauss | ✅ JDBC（PG 兼容） | ✅ Opengauss-CDC（2.3.8+） | ⚠️ 走 jdbc-postgresql | ✅ PG 兼容 | GA |
| GaussDB(DWS) | ✅ JDBC（PG 兼容） | ❌（走 OMS） | ⚠️ 走 jdbc-postgresql | ✅ PG 兼容 | GA |
| TiDB | ✅ JDBC（MySQL 兼容） | ✅ TiDB-CDC | ⚠️ 走 jdbc-mysql | ✅ mysql connector | GA |
| OceanBase | ✅ Source+Sink | ❌（走 OMS） | ⚠️ 走 jdbc-mysql（MySQL 模式） | ✅ mysql connector | Beta→GA |
| 达梦 DM | ✅ JDBC（独立 dialect） | ❌ | ❌（通用 JDBC 扩展） | ⚠️ 通用 JDBC | Beta |
| 人大金仓 Kingbase | ✅ JDBC（PG 兼容） | ❌（走 Kafka CDC） | ⚠️ 走 jdbc-postgresql | ✅ PG 兼容 | Beta |

**关键发现**：
- SeaTunnel JDBC connector 内置 dialect 列表已包含 **Dameng / KingBase / OceanBase / Highgo / Greenplum** 等，且有 `GenericDialect` 兜底——"通用 JDBC 覆盖一类"在 SeaTunnel 侧天然成立（G4 验证）。
- Gravitino 对国产库无专用 provider，但 PG/MySQL 兼容的国产库可走 `jdbc-postgresql` / `jdbc-mysql` provider（GaussDB/OpenGauss/Kingbase 走 PG，TiDB/OceanBase 走 MySQL）。
- SeaTunnel CDC 原生支持 **OpenGauss / TiDB**（在 CDC 列表里），OceanBase/达梦/金仓的 CDC 走各自厂商工具（OMS/DMHS/Kafka CDC），不在 SeaTunnel CDC 范围。

#### 6.1.2 国产库驱动冲突避坑（关键，已踩过）

**问题根因**（本项目飞线 1，见 `gravitino-1.3.0-upgrade.md` §SeaTunnel 飞线 1）：
- openGauss / GaussDB 的 `gsjdbc4.jar` / `opengauss-jdbc-5.1.0.jar` 内含**完整的 `org.postgresql.Driver.class`**（为 PG 兼容性）。
- 与官方 `postgresql-42.x.jar` 注册了**同名 driver** 处理 `jdbc:postgresql://` URL。
- SeaTunnel `AbstractJdbcCatalog.getConnection` 对同名类无效（PR #8986 的"按类名优先选 driver"只对异名类生效），回退到 `DriverManager` 顺序加载，国产库驱动先加载获胜，但 SCRAM/SHA-256 认证与标准 PG 不兼容，报 `Protocol error. Session setup failed`。

**正确解法**（替代现有飞线 1 的"移除 openGauss 驱动"）：
使用**独立类名**的驱动包，避免同名类冲突：

| 数据源 | 推荐驱动包 | Driver 类名 | URL 前缀 |
|--------|-----------|------------|---------|
| OpenGauss | `opengauss-jdbc-5.x.jar`（**非** postgresql.jar） | `com.huawei.opengauss.jdbc.Driver` | `jdbc:opengauss://` |
| GaussDB(DWS) | `gsjdbc200.jar`（**非** gsjdbc4.jar） | `com.huawei.gauss200.jdbc.Driver` | `jdbc:gaussdb://` |
| Kingbase | `kingbase8-8.6.0.jar` | `com.kingbase8.Driver` | `jdbc:kingbase8://` |
| 达梦 DM | `DmJdbcDriver18.jar` | `dm.jdbc.driver.DmDriver` | `jdbc:dm://` |
| TiDB | `mysql-connector-j.jar`（MySQL 协议） | `com.mysql.cj.jdbc.Driver` | `jdbc:mysql://` |
| OceanBase | `oceanbase-client.jar` | `com.oceanbase.jdbc.Driver` | `jdbc:oceanbase://` |

> **回归飞线 1**：采用独立类名驱动后，可移除 `infra/seatunnel-entrypoint.sh`（飞线 1），让 openGauss 驱动与 PG 驱动共存。这是国产库适配的副产物收益。

#### 6.1.3 配置指南

**后端改造点**（`src/ontology/services/datasource_service.py`）：

1. **扩展 `_JDBC_CONNECTOR_MAP` + `_JDBC_DRIVER_MAP`**（G4：适度映射表，非重型抽象）：

```python
_JDBC_CONNECTOR_MAP: dict[str, str] = {
    # 现有
    "mysql": "jdbc-mysql",
    "mariadb": "jdbc-mysql",
    "postgresql": "jdbc-postgresql",
    "postgres": "jdbc-postgresql",
    # 国产库：PG/MySQL 兼容的走对应 provider
    "opengauss": "jdbc-postgresql",      # Gravitino 用 PG provider
    "gaussdb": "jdbc-postgresql",
    "kingbase": "jdbc-postgresql",
    "tidb": "jdbc-mysql",
    "oceanbase": "jdbc-mysql",
    # 达梦无 Gravitino 专用 provider，走通用 JDBC（Gravitino 1.3.0 不支持，只能 SeaTunnel 落地）
    "dameng": None,  # 标记无 Gravitino provider，仅 MANAGED 落地
}

_JDBC_DRIVER_MAP: dict[str, str] = {
    # 现有
    "mysql": "com.mysql.cj.jdbc.Driver",
    "mariadb": "com.mysql.cj.jdbc.Driver",
    "postgresql": "org.postgresql.Driver",
    "postgres": "org.postgresql.Driver",
    # 国产库：用独立类名驱动，避免同名冲突
    "opengauss": "com.huawei.opengauss.jdbc.Driver",
    "gaussdb": "com.huawei.gauss200.jdbc.Driver",
    "kingbase": "com.kingbase8.Driver",
    "tidb": "com.mysql.cj.jdbc.Driver",       # MySQL 协议
    "oceanbase": "com.oceanbase.jdbc.Driver",
    "dameng": "dm.jdbc.driver.DmDriver",
}
```

2. **扩展 `_build_jdbc_url`**（现有 `else: jdbc:{type}://...` 兜底已支持，但国产库需独立 URL scheme）：

```python
_JDBC_URL_SCHEME: dict[str, str] = {
    "opengauss": "opengauss",
    "gaussdb": "gaussdb",
    "kingbase": "kingbase8",
    "tidb": "mysql",         # MySQL 协议
    "oceanbase": "oceanbase",
    "dameng": "dm",
}

@staticmethod
def _build_jdbc_url(connector_type: str, config: dict[str, Any], *, include_database: bool = True) -> str:
    type_lower = connector_type.lower()
    scheme = _JDBC_URL_SCHEME.get(type_lower, type_lower)
    # ... 其余逻辑不变，用 scheme 构造 jdbc:{scheme}://host:port/db
```

3. **Gravitino provider 为 None 的品类（达梦）**：`create_datasource` 跳过 Gravitino catalog 注册，仅存 PG 记录，只支持 SeaTunnel 落地（无 VIRTUAL 联邦）。

#### 6.1.4 最佳实践

- **CDC 优先级**：MySQL/PG/OpenGauss/TiDB 用 SeaTunnel 原生 CDC（§七 spike）；OceanBase/达梦/金仓用 `sync_now` 批量 + 厂商 CDC 工具（路标）。
- **类型映射**：国产库大多 PG/MySQL 兼容，类型映射复用现有 `_iceberg_type_from_str`。注意 Kingbase 的 `bigint identity`、达梦的 `NUMBER(p,s)` 需单独映射。
- **探索 schema**：国产库的 `information_schema` 大多 PG/MySQL 兼容，`explore()` 复用现有逻辑。达梦的 `sysobjects` 需单独处理（GenericDialect 兜底）。

#### 6.1.5 避坑清单

| # | 坑 | 规避 |
|---|----|------|
| 1 | openGauss/GaussDB 驱动与 PG 驱动同名类冲突 | 用独立类名驱动包（§6.1.2） |
| 2 | Gravitino 1.3.0 jsonb 仍映射为 ExternalType（PG 类国产库同理） | 维持 pgnative workaround（`gravitino-1.3.0-upgrade.md`），国产库的 jsonb 列走 `pgnative` catalog |
| 3 | SeaTunnel `timestamptz` 列类型映射缺失（飞线 2） | 国产库的 `timestamptz` 同样受影响，维持 `_build_safe_query` 的 `::text` cast 飞线 |
| 4 | OceanBase MySQL 模式 vs Oracle 模式 | 本期只支持 MySQL 模式（`jdbc-mysql` provider），Oracle 模式进路标 |
| 5 | 达梦无 Gravitino provider | 标记 `provider=None`，只支持 MANAGED 落地，不支持 VIRTUAL 联邦 |
| 6 | TiDB CDC 需要 PD 地址 | SeaTunnel TiDB-CDC 配置需 `pd-addresses`，在 `source_config` 里透传 |

---

### 6.2 湖仓格式（Hive/Delta/Hudi/Paimon 联邦源）

#### 6.2.1 原生支持情况

| 格式 | Gravitino 1.3.0 | SeaTunnel 2.3.13 | Trino 478 | 成熟度 | 本期角色 |
|------|-----------------|------------------|-----------|--------|---------|
| Iceberg | ✅ lakehouse-iceberg（主存储，已通） | ✅ Source+Sink（REST Catalog 已支持，postmortem 证伪） | ✅ iceberg connector | GA | 主存储 |
| Hive | ✅ hive catalog | ✅ Source+Sink | ✅ hive connector | GA | 联邦源 |
| Delta Lake | ✅ Generic Lakehouse Catalog（1.2.0+ #9647） | ✅ Source+Sink | ✅ delta connector | GA | 联邦源 |
| Hudi | ✅ lakehouse-hudi | ✅ Source+Sink | ✅ hudi connector | GA | 联邦源 |
| Paimon | ✅ lakehouse-paimon | ✅ Source+Sink | ✅ paimon connector | GA | 联邦源 |

**关键发现**：Gravitino 1.2.0 引入 **Generic Lakehouse Catalog**（#9647），统一纳管 Delta/Hudi/Paimon 等湖仓格式的外部表——这是"湖仓格式联邦源"的统一入口，不需要每个格式单独建 catalog。

#### 6.2.2 接入方案

湖仓格式作为**联邦源**（VIRTUAL），让 Gaia 能查询企业已有的 Hive/Delta/Hudi/Paimon 表，不搬迁：

1. **Gravitino 注册外部湖仓 catalog**：用户配置已有的 Hive Metastore / Delta 表路径 / Hudi 表路径 → Gravitino 纳管元数据。
2. **Trino 通过 Gravitino Connector 联邦查询**：跨 catalog Join（Gaia Iceberg 表 + 外部 Hive 表）。
3. **不落地**：湖仓格式数据保留在原存储，Gaia 只读。

**也可选落地**：若需要把外部 Hive/Delta 表搬进 Gaia Iceberg（统一治理），用 SeaTunnel Hive/Delta source → Iceberg sink。

#### 6.2.3 最佳实践

- **联邦优先**：企业已有 Hive/Delta/Hudi/Paimon 表优先走 VIRTUAL 联邦（不搬迁），只有需要统一治理/时间旅行时才落地。
- **跨格式 Join**：Trino 跨 catalog Join 是湖仓联邦的核心价值（Gaia Iceberg 表 + 外部 Hive 表关联分析）。
- **Generic Lakehouse Catalog 优先**：Delta/Hudi/Paimon 用 Gravitino 1.2.0+ 的统一纳管入口，不为每个格式单独建 catalog。
- **schema 推断**：湖仓格式自带 schema，Trino/Gravitino 自动识别，无需手动配置。

#### 6.2.4 配置指南

**Gravitino catalog 注册**（`GravitinoRegistry` 新增方法）：

```python
async def register_lakehouse_catalog(
    self,
    catalog_name: str,
    provider: str,  # "hive" | "lakehouse-delta" | "lakehouse-hudi" | "lakehouse-paimon"
    properties: dict[str, str],  # metastore-uri / warehouse / 表路径
) -> None:
    """注册外部湖仓 catalog 作为联邦源。

    provider="hive": properties={"metastore-uri": "thrift://..."}
    provider="lakehouse-delta": properties={"catalog-backend": "...", "warehouse": "..."}
    """
```

#### 6.2.5 避坑清单

| # | 坑 | 规避 |
|---|----|------|
| 1 | Hive Metastore 必须可达 | Gravitino hive catalog 需 Thrift 访问 HMS，网络不通则注册失败 |
| 2 | Delta/Hudi/Paimon 表的底层存储凭证 | 走 Gravitino Fileset 的 S3/OSS 凭证体系，复用现有 RustFS 配置 |
| 3 | Trino hive connector 需要配套的文件系统配置 | hive catalog 底层访问 S3/HDFS 需配 `fs.native-s3` 等（Trino 侧），与现有 iceberg catalog 配置一致 |
| 4 | 跨湖仓格式 Join 的类型映射差异 | Delta/Hudi 的 `struct`/`map` 类型映射到 Trino `ROW`/`MAP`，注意嵌套类型查询限制 |

---

### 6.3 文件与对象存储（S3/MinIO/OSS/HDFS）

#### 6.3.1 原生支持情况

| 存储介质 | Gravitino Fileset | SeaTunnel File Source | Trino（湖仓底层） | 成熟度 |
|---------|-------------------|----------------------|-------------------|--------|
| Amazon S3 | ✅ | ✅ S3File | ✅ | GA |
| MinIO（S3 兼容） | ✅ | ✅ S3File（用 S3 endpoint） | ✅ | GA |
| 云对象存储 OSS | ✅ | ✅ OssFile（独立）/ S3File（兼容） | ✅ | GA |
| HDFS | ✅ | ✅ HdfsFile | ✅ | GA |

**文件格式支持**（SeaTunnel）：CSV / JSON / Parquet / ORC / Avro / Excel / Text / XML / Binary。Parquet/ORC 自动识别 schema，CSV/JSON 需指定 schema 或自动推断。

**关键发现**：
- Gravitino Fileset catalog 管理**非结构化文件元数据 + 存储位置 + 访问权限**，不存文件内容，提供 GVFS 虚拟文件系统层。
- SeaTunnel `OssFile` 与 `S3File` 是独立连接器——MinIO 不能用 `OssFile`（issue #5835 确认），需用 `S3File` 配 MinIO endpoint。
- SeaTunnel 2.3.13 新增文件 `update` 同步模式，突破传统文件仅追加/覆盖的限制。

#### 6.3.2 接入方案

文件/对象存储**只能 MANAGED 落地**（无 VIRTUAL 联邦——Trino 不能直接对裸文件做联邦查询，必须经湖仓格式 catalog）：

1. **Gravitino Fileset 注册**：登记外部 S3/OSS/HDFS 路径作为 Fileset。
2. **SeaTunnel File source → Iceberg sink**：把文件内容读出，按格式解析，写入 Iceberg 托管表。
3. **schema 推断**：Parquet/ORC 自动；CSV/JSON 用 SeaTunnel 的 schema 配置或 `schema_save_mode=CREATE_SCHEMA_WHEN_NOT_EXIST`。

#### 6.3.3 最佳实践

- **Parquet/ORC 优先**：结构化文件优先用 Parquet/ORC（自带 schema，自动推断），避免 CSV/JSON 的 schema 配置负担。
- **S3File 统一走 S3 协议**：MinIO/OSS 都用 S3File + endpoint，不用独立的 OssFile（兼容性更好，issue #5835）。
- **Fileset 管位置，SeaTunnel 读内容**：Gravitino Fileset 只管元数据与访问入口，实际文件读取由 SeaTunnel 完成，职责分离。
- **大文件并行**：SeaTunnel 2.3.13 支持大文件并行处理，配置 `split_row` 控制并行度。
- **增量同步**：用 `modified_after` 过滤或 2.3.13 的 `update` 同步模式，避免全量重复读。

#### 6.3.4 配置指南

**SeaTunnel S3File source 模板**（`SeaTunnelEngine` 新增 `create_file_sync_pipeline`）：

```hocon
source {
  S3File {
    path = "s3://bucket/path/"
    bucket = "bucket"
    access_key = "${access_key}"
    secret_key = "${secret_key}"
    endpoint = "${endpoint}"  # MinIO: http://minio:9000
    file_format_type = "parquet"  # parquet/orc/csv/json/...
    # schema = [...]  # csv/json 需指定
  }
}
sink {
  Iceberg {
    # 复用现有 Iceberg sink 配置（catalog-impl=RESTCatalog, 不带 warehouse）
  }
}
```

#### 6.3.5 避坑清单

| # | 坑 | 规避 |
|---|----|------|
| 1 | MinIO 不能用 OssFile | MinIO 走 S3File + endpoint 指向 MinIO |
| 2 | CSV/JSON 需指定 schema | 用 SeaTunnel schema 配置或 `CREATE_SCHEMA_WHEN_NOT_EXIST` |
| 3 | 大文件并行读取 | SeaTunnel 2.3.13 支持大文件并行处理，配置 `split_row` 控制 |
| 4 | 文件增量同步 | 用 SeaTunnel 2.3.13 的 `update` 同步模式，或 `modified_after` 过滤 |
| 5 | OSS 走 S3 兼容还是专用 OssFile | 云对象存储 OSS 推荐 S3File（S3 兼容协议更稳定），OssFile 作为路标 |

---

### 6.4 消息队列（Kafka）

#### 6.4.1 原生支持情况

| 能力 | Gravitino 1.3.0 | SeaTunnel 2.3.13 | Trino 478 | 成熟度 |
|------|-----------------|------------------|-----------|--------|
| Topic 元数据纳管 | ✅ kafka catalog | ✅ Source+Sink | ✅ kafka connector | GA |
| 消息消费 | ❌（只管元数据） | ✅ Exactly-once + 消费组 | ✅ Topic→表 | GA |
| Schema Registry | ❌ | ✅ Protobuf/Avro/JSON（2.3.13 增强） | ⚠️ 需 topic description 文件 | Beta |

**关键发现**：
- Gravitino Kafka catalog **只纳管 Topic 元数据**（创建/更新/删除/list），不存消息内容，符合"元数据不动数据"理念。
- Trino Kafka connector 需要手动配置 `kafka.table-names` + **topic description JSON 文件**（定义字段映射），不是自动发现 schema——配置较重。
- SeaTunnel 2.3.13 的 Kafka source 支持 Schema Registry wire format（Protobuf 反序列化 #10183），Exactly-once + 消费组管理。

#### 6.4.2 接入方案

Kafka 是少数能走 VIRTUAL 联邦的非关系型数据源，但也支持落地：

**路径 A：VIRTUAL 联邦（Trino 实时消费）**
- 适用：实时即席查询 Topic 数据，不需要持久化。
- 限制：需手写 topic description JSON；Schema 注册需额外配置（issue #12195）。

**路径 B：MANAGED 流式落地（SeaTunnel Kafka → Iceberg）**
- 适用：消息需要持久化、时间旅行、与本体对象关联。
- 优势：SeaTunnel 自动处理 schema、Exactly-once。

**路径 C：MANAGED 流式落地（SeaTunnel Kafka → Iceberg → ObjectIndexFunnel → Doris）**——实时索引
- 适用：消息需要秒级可查（Doris 在线读主源）。
- SeaTunnel 只负责 Kafka→Iceberg 搬运；Iceberg→Doris 由 ObjectIndexFunnel 完成（不经 SeaTunnel）。原 §七 的 Kafka→Doris 直写链路已废弃（去 SeaTunnel 化）。

#### 6.4.3 最佳实践

- **落地优先**：Kafka 常规场景走路径 B（SeaTunnel → Iceberg），只有"不想持久化、只实时即席查询"才走路径 A（Trino VIRTUAL）。
- **Schema Registry 优先**：生产环境用 Avro/Protobuf + Schema Registry，避免 JSON 手动维护 schema。
- **消费组隔离**：每个 SeaTunnel Kafka ingestion pipeline 用独立消费组（`consumer.group`），避免与内部 Action CDC 的 Kafka 消费组冲突。
- **Exactly-once 权衡**：开启 `exactly_once=true` 保证不丢不重，但牺牲吞吐；对账类场景开启，日志类可关闭。
- **Topic schema 演进**：SeaTunnel `allow_schema_changes` + Iceberg sink schema evolution 支持 Topic 加字段。

#### 6.4.4 配置指南

**Gravitino Kafka catalog 注册**（`GravitinoRegistry` 新增）：

```python
async def register_kafka_catalog(
    self,
    catalog_name: str,
    bootstrap_servers: str,
) -> None:
    """注册 Kafka catalog 纳管 Topic 元数据。

    POST /api/metalakes/ontology/catalogs
    { name, type: "messaging", provider: "kafka",
      properties: {"bootstrap.servers": "..."} }
    """
```

**SeaTunnel Kafka source → Iceberg sink 模板**（`SeaTunnelEngine` 新增 `create_kafka_ingestion_pipeline`）：

```hocon
source {
  Kafka {
    topic = "events"
    bootstrap.servers = "${bootstrap}"
    consumer.group = "gaia_ingest"
    pattern = "json"  # json/avro/protobuf
    # schema = [...]  # json 需指定字段
  }
}
sink {
  Iceberg { /* 复用配置 */ }
}
```

#### 6.4.5 避坑清单

| # | 坑 | 规避 |
|---|----|------|
| 1 | Trino Kafka connector 需手写 topic description JSON | VIRTUAL 路径仅推荐给"不想搬迁"的场景，常规用落地 |
| 2 | Trino Kafka Schema Registry Basic Auth 支持（issue #12195） | 升级 schema-registry-client 到 6+，或用 SeaTunnel 落地绕过 |
| 3 | SeaTunnel Kafka 消费组 offset 管理 | 用 Exactly-once + checkpoint（2.3.13 Checkpoint API） |
| 4 | Topic schema 演进 | SeaTunnel `allow_schema_changes` 控制；Iceberg sink 支持 schema 演进 |

---

### 6.5 NoSQL（Elasticsearch）

#### 6.5.1 原生支持情况

| 能力 | Gravitino 1.3.0 | SeaTunnel 2.3.13 | Trino 478 | 成熟度 |
|------|-----------------|------------------|-----------|--------|
| 元数据纳管 | ❌ 不支持 | ✅ Source+Sink | ✅ elasticsearch connector | GA（SeaTunnel）/ 限制多（Trino） |
| 数据搬运 | — | ✅ Scroll API + 批量读写 | — | GA |
| 联邦查询 | — | — | ⚠️ 多限制 | Beta（多 bug） |

**关键发现（决定 ES 以落地为主）**：
- Gravitino **不纳管 ES** → ES 无法走 VIRTUAL 联邦（违反 §八品类一刀切原则）。
- Trino ES connector 的硬限制（issue #754/8358/29158）：
  - ❌ 不支持 ES multi-fields 子字段（`.keyword`）作为可查询列
  - ❌ 不支持 runtime fields
  - ⚠️ nested 类型映射有 bug（`UnsupportedOperationException`）
  - ❌ dense_vector 等类型不支持
- ES 的核心价值是**全文检索 + 倒排索引**，Trino 联邦只能做结构化 SQL 查询，发挥不出 ES 优势。
- SeaTunnel ES source 用 Scroll API 批量读取，2.3.13 增强 Runtime Fields 支持 + Scroll API 资源清理。

**结论**：ES 应以**落地 Iceberg 为主**（SeaTunnel ES source → Iceberg），Trino ES 联邦仅作为“不想搬迁且字段简单”的兜底，且要明确告知用户限制。

> ⚠️ **评审决策点 4 已确认**：严格一刀切，ES 一律落地，**不在产品上暴露 Trino ES 联邦口子**。上述“兜底”仅指技术可能性，本期不实现。

#### 6.5.2 接入方案（落地为主）

1. **SeaTunnel ES source → Iceberg sink**：把 ES index 的文档批量读出，写入 Iceberg 托管表。
2. **schema 推断**：SeaTunnel 从 ES index mapping 自动推断 schema（`schema_save_mode=CREATE_SCHEMA_WHEN_NOT_EXIST`）。
3. **增量同步**：ES 无原生 CDC，用 `query` 过滤时间字段（如 `@timestamp`）做增量。

#### 6.5.3 最佳实践

- **落地为主**：ES 走 SeaTunnel → Iceberg，不走 Trino 联邦（决策点 4）。
- **`text` 字段映射为 string**：ES 的 `text` 类型在 Iceberg 里存为 `string`，保留原始文本，检索能力由 Gaia 的 Doris 全文索引或独立 ES 提供。
- **`@timestamp` 增量**：ES 无原生 CDC，用时间字段过滤做增量同步，避免全量 Scroll。
- **大 index 分片**：用 SeaTunnel `max_batch_size` + Scroll API 控制，避免 OOM。
- **Runtime Fields**：ES 7.11+ 的 Runtime Fields，SeaTunnel 2.3.13 支持（#10201），落地时正常读取。

#### 6.5.4 配置指南

**SeaTunnel ES source 模板**（`SeaTunnelEngine` 新增）：

```hocon
source {
  Elasticsearch {
    hosts = ["es_host:9200"]
    index = "logs-*"
    username = "${user}"
    password = "${password}"
    schema_save_mode = "CREATE_SCHEMA_WHEN_NOT_EXIST"
    # query = "{\"range\":{\"@timestamp\":{\"gte\":\"2026-07-01\"}}}"  # 增量
  }
}
sink {
  Iceberg { /* 复用配置 */ }
}
```

#### 6.5.5 避坑清单

| # | 坑 | 规避 |
|---|----|------|
| 1 | Trino ES connector 不支持 multi-fields/nested | 落地为主，Trino 联邦仅限简单字段 |
| 2 | ES mapping 的 `text` 字段在 Trino 里不可查 | 落地时把 `text` 映射为 Iceberg `string` |
| 3 | ES Scroll API 资源泄露 | SeaTunnel 2.3.13 已增强 Scroll 资源清理（#10124） |
| 4 | ES 7.11+ Runtime Fields | SeaTunnel 2.3.13 支持（#10201），落地时正常读取 |
| 5 | 大 index 全量同步 OOM | 用 SeaTunnel 分片并行 + `max_batch_size` 控制 |

---

### 6.6 中国云数仓（MaxCompute/ADB/GaussDB-DWS 等）

#### 6.6.1 原生支持情况

| 云数仓 | 内核 | SeaTunnel 2.3.13 | Gravitino 1.3.0 | Trino 478 | 成熟度 | 本期角色 |
|--------|------|------------------|-----------------|-----------|--------|---------|
| AnalyticDB PostgreSQL（ADB-PG） | PG | ✅ JDBC（PG 兼容） | ⚠️ 走 jdbc-postgresql | ✅ postgresql connector | GA | **本期**（复用 PG 通道） |
| GaussDB DWS | PG | ✅ JDBC（PG 兼容） | ⚠️ 走 jdbc-postgresql | ✅ PG 兼容 | GA | **本期**（复用 PG 通道） |
| MaxCompute | 独立 | ✅ MaxCompute Source+Sink | ❌ 无 provider | ⚠️ 官方 Trino connector（独立） | GA | **路标**（决策点 6） |
| TDSQL / CDW | MySQL/PG | ✅ JDBC | ⚠️ 走对应 provider | ✅ 兼容 | GA | 路标 |

**关键发现**：
- **ADB-PG 和 GaussDB-DWS 都基于 PG 内核**，可走通用 PG JDBC 通道——SeaTunnel/Trino/Gravitino 都能用现有 PG 通路（pgnative workaround 同理适用），**通用性极强（G2）**。
- GaussDB DWS 提供 `gsjdbc4.jar`（PG 兼容，同名类）和 `gsjdbc200.jar`（独立类名 `com.huawei.gauss200.jdbc.Driver`）——**必须用 gsjdbc200.jar** 避免与 PG 驱动冲突（§6.1.2）。
- MaxCompute 有官方 JDBC 驱动 + 官方维护的 Trino connector（`aliyun-maxcompute-data-collectors/trino-connector`），但独立 Trino connector 集成成本高，进路标（决策点 6）。

#### 6.6.2 接入方案（ADB-PG / GaussDB-DWS）

复用 PG 通道（§6.1），只需在 `CONNECTOR_META` + `_JDBC_DRIVER_MAP` 增加条目：

```python
# ADB-PG：PG 内核，走 jdbc-postgresql provider + 独立驱动
"analyticdb_pg": {
    "provider": "jdbc-postgresql",
    "driver": "org.postgresql.Driver",  # ADB-PG 兼容标准 PG 驱动
    "url_scheme": "postgresql",
    "default_port": "5432",
}

# GaussDB DWS：PG 内核，走 jdbc-postgresql provider + 独立类名驱动
"gaussdb_dws": {
    "provider": "jdbc-postgresql",
    "driver": "com.huawei.gauss200.jdbc.Driver",  # 独立类名，避免冲突
    "url_scheme": "gaussdb",
    "default_port": "8000",
}
```

#### 6.6.3 最佳实践

- **复用 PG 通道**：ADB-PG / GaussDB-DWS 都是 PG 内核，走 `jdbc-postgresql` provider，复用现有 PG 接入逻辑（探索/同步/VIRTUAL 联邦）。
- **GaussDB 必用 gsjdbc200.jar**：独立类名 `com.huawei.gauss200.jdbc.Driver`，避免与标准 PG 驱动同名冲突（§6.1.2）。
- **VIRTUAL 联邦优先**：云数仓数据量大，优先走 VIRTUAL 联邦不搬迁，只读查询；需要本地加速才落地。
- **网络可达性**：云数仓在 VPC 内，部署时确认 Gaia 到云数仓的网络可达，Gaia 不处理网络层。

#### 6.6.4 配置指南

配置方式与 §6.1.3 关系库一致（复用 PG 通道），只需 `connector_type` 选 `analyticdb_pg` 或 `gaussdb_dws`，`connector_config` 填 host/port/database/credentials。详见 §6.1.3 的 `_JDBC_CONNECTOR_MAP` / `_JDBC_DRIVER_MAP` / `_JDBC_URL_SCHEME` 扩展。

#### 6.6.5 避坑清单

| # | 坑 | 规避 |
|---|----|------|
| 1 | GaussDB DWS 的 gsjdbc4.jar 与 PG 驱动同名冲突 | 用 gsjdbc200.jar（独立类名） |
| 2 | ADB-PG 的跨库查询 FDW 限制 | Gaia 只读 ADB-PG 的表，不做跨库 FDW，跨源 Join 由 Trino 联邦 |
| 3 | MaxCompute 独立 Trino connector 集成成本 | 进路标，本期不做 |
| 4 | 云数仓的网络隔离（VPC） | 部署时配置网络可达，Gaia 不处理网络层 |


---

## 七、实时管线与 CDC

### 7.1 目标态（Q4 已确认：A + B）

- **生产路径（A）**：维持现状 `sync_now` 批量同步 + 增量拉取（`incremental_column` + `incremental_start`）。已验证可用（benchmark 03_wait_sync 收敛）。
- **Spike 验证（B）**：并行验证 CDC 路径 a（CDC → Iceberg → Doris）。spike 成功 → 接入主线；失败 → 回落路标。
- **设计原则**：接口按"未来支持 CDC"设计，不锁死为只读批量。

### 7.2 现状澄清：内部 CDC vs 外部数据源 CDC

**关键区分**（调研中发现）：
- Gaia 曾有内部 CDC pipeline 模板（`create_pg_to_kafka_pipeline`、`create_kafka_to_doris_pipeline`、`create_dual_sink_pipeline`），针对 Action 闭环的内部 CDC（PG `object_state` → Iceberg/Kafka/Doris）。**这些模板已于 2026-07-08 删除（去 SeaTunnel 化）**，object_state 同步改 outbox 驱动（INDEX/ARCHIVE effect）。仅保留 `create_action_cdc_pipeline`（审计日志→Iceberg，当前无调用方）+ `PIPELINE_CDC_TEMPLATE`。
- 用户问的"实时增量秒级映射进本体"指的是**外部数据源（MySQL/PG 等业务库）的 CDC**——把外部业务库的变更实时同步到 Gaia Iceberg，再映射进本体。
- 两者模板结构相似（都用 SeaTunnel CDC source + Iceberg sink），但 **source 不同**：内部 CDC source 是 PG `object_state`，外部 CDC source 是用户业务库的表。

本节聚焦**外部数据源 CDC**。

### 7.3 CDC 路径 a 的 spike 方案

**目标**：验证 `SeaTunnel CDC source（MySQL-CDC/PG-CDC）→ Iceberg sink（REST Catalog）` 链路在 Gaia 现有环境（SeaTunnel 2.3.13 + Gravitino 1.3.0）下可用。（数据进 Iceberg 后由 ObjectIndexFunnel 同步到 Doris，这段不走 SeaTunnel。）

#### 7.3.1 spike 前置调研结论（已验证）

postmortem（`seatunnel-iceberg-rest-interop-postmortem.md`）已证伪"SeaTunnel 2.3.13 不支持 Iceberg REST Catalog"的判断：

| 原判断 | 证伪结论 |
|--------|---------|
| SeaTunnel 2.3.13 Iceberg source 不支持 REST Catalog | ❌ 错误。`RESTCatalog.class` 存在于 2.3.13，用 `catalog-impl=org.apache.iceberg.rest.RESTCatalog` 透传可加载 |
| PR #9654 未进 2.3.13 | ❌ 错误。#9654 的 docs PR #9686 在 2.3.12 release note 出现，2.3.13 自然包含 |
| REST Catalog 读取失败是版本问题 | ❌ 错误。是 Gravitino REST server 的 `/v1/config?warehouse=` 返回 404（warehouse 当 catalog 名查），去掉 warehouse 即可 |

**spike 可行性结论**：SeaTunnel 2.3.13 的 Iceberg **sink** 已原生支持 CDC mode + REST Catalog + auto create table + schema evolution。CDC → Iceberg sink 路径在技术上可行。

#### 7.3.2 spike 验证步骤

```
Step 1: 准备外部 MySQL 测试库
  - 创建测试表 `test_cdc_source`，插入初始数据
  - 开启 binlog（row 模式）

Step 2: 提交 SeaTunnel CDC → Iceberg job
  source {
    MySQL-CDC {
      hostname = "mysql_test"
      port = 3306
      username = "..."
      password = "..."
      database-name = "test"
      table-name = "test_cdc_source"
      server-time-zone = "Asia/Shanghai"
    }
  }
  sink {
    Iceberg {
      catalog_name = "ontology"
      namespace = "ontology"
      table = "test_cdc_target"  # 小写
      iceberg.catalog.config = {
        catalog-impl = "org.apache.iceberg.rest.RESTCatalog"
        uri = "http://gravitino:9001/iceberg"
        # ⚠️ 不带 warehouse —— 规避 Gravitino /v1/config 404
        "s3.endpoint" = "http://rustfs:9000"
        "s3.region" = "us-east-1"
        "s3.access-key-id" = "..."
        "s3.secret-access-key" = "..."
      }
    }
  }
  job.mode = "STREAMING"  # CDC 必须流式

Step 3: 验证全量快照写入
  - 检查 Iceberg 表 test_cdc_target 有初始数据
  - Trino: SELECT count(*) FROM iceberg.ontology.test_cdc_target

Step 4: 验证增量 CDC
  - 在 MySQL test_cdc_source 插入/更新/删除数据
  - 检查 Iceberg 表秒级反映变更（target ≤ 5s 延迟）

Step 5: 验证 Iceberg → Doris 同步
  - 调用 ObjectIndexFunnel.project_for_dataset（从 Iceberg scan_latest 读 → DorisIndexStore.upsert）
  - ~~或验证 SeaTunnel Iceberg→Doris pipeline~~（已于 T1.10 删除）

Step 6: 验证 schema 演进
  - 在 MySQL 加列
  - 检查 Iceberg 表自动加列（SeaTunnel Iceberg sink 支持 schema evolution）
```

#### 7.3.3 spike 成功/失败判定

| 判定项 | 成功标准 | 失败处理 |
|--------|---------|---------|
| 全量快照 | Iceberg 表有初始数据，count 正确 | 排查 REST Catalog 配置（warehouse/catalog-impl） |
| 增量 CDC | MySQL DML 后 ≤ 5s Iceberg 反映 | 排查 binlog 配置、checkpoint 间隔 |
| Doris 同步 | Doris 表有数据，查询返回正确 | 用 sync_now 兜底（现有路径） |
| schema 演进 | 加列后 Iceberg 自动加列 | 关闭 `allow_schema_changes`，手动重建 |
| worker 稳定性 | STREAMING 模式不 crash | 2026-07-06 实测可用（不 crash）；若未来复现 crash，排查 postmortem 记录的 EventService NPE |

#### 7.3.4 spike 已知坑（必须规避）

| # | 坑 | 来源 | 规避 |
|---|----|------|------|
| 1 | Iceberg sink 配置带 `warehouse` 导致 404 | postmortem §3.3 | `iceberg.catalog.config` 不带 `warehouse` |
| 2 | 用 `type="rest"` 被枚举拒 | postmortem §3.2 | 用 `catalog-impl=org.apache.iceberg.rest.RESTCatalog` |
| 3 | 表名大小写敏感 | postmortem §4 | `table` 传小写 |
| 4 | ~~STREAMING+incremental worker crash（EventService NPE）~~ | postmortem §3.4 / ADR-008 修订 | **2026-07-06 实测证伪**：当前环境 STREAMING+`stream_scan_strategy` 稳定不 crash、增量同步正常。无需回避 STREAMING；BATCH 仅用于首次 backfill/容灾（见 ADR-008 模式选择评估） |
| 5 | **PK 继承导致 append-only CDC 数据丢失** | SeaTunnel issue #10747 | `iceberg.table.primary-keys` 显式配置，或确认 source 表 PK 语义符合预期。**spike 必须验证 UPDATE/DELETE 不丢数据** |
| 6 | SeaTunnel CDC exactly-once 默认关闭 | connector-cdc-base #6244 | 生产环境按需开启 `exactly_once=true`，权衡稳定性 |
| 7 | PG CDC 需 `wal_level=logical` | 现有 ADR-008 | PG 源库配置 `wal_level=logical` + 复制槽权限 |
| 8 | timestamptz 列类型映射（飞线 2） | gravitino-1.3.0-upgrade.md | CDC 模式下复用 `_build_safe_query` 的 `::text` cast（若 SeaTunnel CDC 不走 query 则需验证） |

#### 7.3.5 spike 成功后的接入设计

spike 成功后，新增 `SeaTunnelEngine.create_external_cdc_pipeline`：

```python
async def create_external_cdc_pipeline(
    self,
    datasource_api_name: str,
    source_table: str,
    target_dataset_api_name: str,
    cdc_config: dict[str, Any],  # hostname/port/username/password/database-name/server-time-zone
) -> PipelineDef:
    """创建外部数据源 CDC → Iceberg pipeline。

    与内部 ACTION_CDC pipeline 区别：
    - source 是外部业务库（MySQL-CDC/PG-CDC/Opengauss-CDC/TiDB-CDC）
    - sink 是 Gaia Iceberg 托管表（用户指定的 target_dataset）
    - STREAMING 模式（CDC 必须流式）
    """
```

`DataSourceService` 增加 `start_cdc_sync`，与现有 `start_sync`（批量）并列。SyncTask 增加 `sync_mode = "cdc"`。

### 7.4 CDC 不成功的兜底

若 spike 失败（最可能是 PK 继承数据丢失无法规避；STREAMING worker crash 已于 2026-07-06 证伪不再是最可能原因）：
- 维持 `sync_now` 批量同步为生产路径
- CDC 进路标，等 SeaTunnel 升级（含 #10747 修复 + STREAMING 稳定性）
- 不阻塞本期其他连接器扩展工作

---

## 八、VIRTUAL 联邦扩展边界

### 8.1 品类一刀切策略（Q2 已确认）

| 品类 | VIRTUAL 联邦 | MANAGED 落地 | 理由 |
|------|-------------|-------------|------|
| 关系库 JDBC（含国产 PG/MySQL 兼容） | ✅ | ✅ | Gravitino 纳管 + Trino 下推完善 |
| 湖仓格式（Hive/Delta/Hudi/Paimon） | ✅ | ✅ | Gravitino 纳管 + Trino 联邦 |
| Kafka | ✅ | ✅ | Gravitino 纳管 Topic 元数据 + Trino kafka connector |
| 文件/对象存储 | ❌ | ✅ | 无联邦价值（裸文件不能 SQL 查询），必须落地成湖仓表 |
| Elasticsearch | ❌（仅兜底） | ✅ | Gravitino 不纳管 + Trino ES 限制多 + ES 优势在检索非 SQL |
| 达梦 / MaxCompute | ❌ | ✅ | Gravitino 无 provider / 独立 Trino connector 成本高 |
| 其他 NoSQL / 时序 / SaaS | ❌ | ✅ | Gravitino 不纳管 |

### 8.2 不解耦 Trino 直连的决策依据

**决策**：不为 NoSQL/时序/SaaS 解耦 Trino 直连（绕过 Gravitino Catalog）。

**理由**（G4 不过度抽象）：
1. 解耦 Trino 直连会破坏 6 层架构纯洁度——Catalog 层（Gravitino）失去统一纳管意义，权限下推失效。
2. NoSQL 联邦查询发挥不出 NoSQL 自身优势（ES 全文检索、Mongo 文档查询、Redis KV）——Trino SQL 只能做结构化查询。
3. Palantir 自己也是"关系库 + 湖仓 + Kafka 走 VIRTUAL，其余落地"的模式，并非所有数据源都联邦。
4. 若未来有强需求，可单独为某品类开 Trino 直连口子（路标），不预先抽象。

### 8.3 VIRTUAL 路径的现有实现复用

VIRTUAL 虚拟表已实现（`dataset-ontology-binding.md` §3.2，`DataSourceService.register_virtual_table`）：
- 外部表登记为 `DatasetGovernance(kind=VIRTUAL, storage_location="catalog.schema.table")`
- 查询时 `ObjectQueryService` 按 `storage_type=VIRTUAL` 走 Trino 联邦

**扩展点**：本期只需让更多品类的连接器能走 `register_virtual_table`——关系库（含国产）/湖仓格式/Kafka 已具备条件，其余品类明确不支持。

---

## 九、与 Gaia 6 层架构的契合

### 9.1 各层改造点（零侵入，G4）

| 层 | 改造点 | 改造性质 |
|----|--------|---------|
| **Catalog (Gravitino)** | `GravitinoRegistry` 新增 `register_lakehouse_catalog` / `register_kafka_catalog` / `register_fileset_catalog`；`register_jdbc_catalog` 已支持（现有） | 新增方法，不改现有 |
| **Metadata (PostgreSQL)** | `data_sources` 表 `connector_type` 已是 VARCHAR，无需改 schema；`CAPABILITY_MAP` 扩展 | 配置数据，无 schema 变更 |
| **Dataset (Iceberg)** | 无改造（Iceberg 已是主存储，sink 配置复用 postmortem 修复的模板） | 无 |
| **Index (Doris)** | 无改造（Doris 索引同步复用 `IndexSyncService.sync_now`） | 无 |
| **Pipeline (SeaTunnel)** | `SeaTunnelEngine` 新增 `create_file_sync_pipeline` / `create_kafka_ingestion_pipeline` / `create_external_cdc_pipeline`（spike 后）；现有 `create_sync_pipeline` 扩展 connector_type 路由 | 新增方法 + 模板 |
| **Engine (Trino)** | 无改造（Trino connector 由 Gravitino 动态加载，`register_catalog_in_trino` 已支持） | 无 |

### 9.2 Service 层改造点

| Service | 改造点 |
|---------|--------|
| `DataSourceService` | `_JDBC_CONNECTOR_MAP` / `_JDBC_DRIVER_MAP` / `_JDBC_URL_SCHEME` 扩展（§6.1.3）；`create_datasource` 按 connector_type 分流（JDBC 走 Gravitino catalog，File/Kafka 走对应 catalog，达梦等无 provider 的跳过）；新增 `start_cdc_sync`（spike 后） |
| `OntologyService` | 无改造（数据集绑定链路不变） |
| `ObjectQueryService` | 无改造（VIRTUAL/MANAGED 路由不变） |
| `IndexSyncService` | 无改造（`sync_now` 复用） |

### 9.3 前端改造点

| 文件 | 改造点 |
|------|--------|
| `src/web-ui/src/components/DataSourceForm.tsx` | `CONNECTOR_META` 扩展为含 `description/category/maturity/capabilities/configSchema` 的完整结构（§5.2.1）；Step 1 改为分品类目录页（§5.2.2）；Step 2 按 `configSchema` 动态渲染配置表单 |
| `src/web-ui/src/components/DataSourceCard.tsx` | `CONNECTOR_ICONS` 扩展；卡片展示能力标签 |
| 新增 `src/web-ui/src/constants/connectorCatalog.ts` | 抽取 `CONNECTOR_META` 到独立文件（连接器多了之后必要） |
| 新增 `src/web-ui/src/components/ConnectorDetailPanel.tsx` | 连接器详情面板（§5.2.3） |

### 9.4 不引入新抽象（G4 验证）

- ❌ 不建 `ConnectorRegistry` / `ConnectorPlugin` / `@register_connector` 装饰器
- ❌ 不建连接器 SPI 框架
- ✅ 用配置驱动的映射表（`_JDBC_CONNECTOR_MAP` 等）+ 直接复用 SeaTunnel/Gravitino/Trino 原生能力
- ✅ 前端 `CONNECTOR_META` 是数据结构，不是插件体系

---

## 十、安全与涉密场景适配

### 10.1 现状安全债

| # | 债 | 现状 | 风险 |
|---|----|------|------|
| SEC-001 | `credentials.secret_data` 明文存储 | PG `credentials` 表 `secret_data` JSONB 明文（TODO 标注） | 涉密场景不可接受 |
| SEC-002 | 无行级权限 | 治理 principal=anonymous，无行列级权限 | 多租户/涉密不可用 |
| SEC-003 | 无审计入库 | 删除本体等操作无持久化审计 | 合规不可用 |

### 10.2 涉密场景的天然适配（不动数据）

Gaia 的 VIRTUAL 联邦路径天然适配涉密场景（对标 Palantir 国防/金融卖点）：
- **数据不搬迁**：VIRTUAL 虚拟表只存指针（`catalog.schema.table`），数据留在源系统
- **权限下推**：Gravitino 权限规则下推到源系统执行（源系统保持原有 ACL）
- **计算下推**：Trino 把谓词/聚合下推到源端，最小化数据传输
- **审计可追**：Trino 查询日志 + Gravitino 访问日志（需 SEC-003 落地）

**适用场景**：涉密数据库不允许可搬迁，但可查询——走 VIRTUAL 联邦，Gaia 只看到查询结果。

### 10.3 本期安全工作范围

- **不做** SEC-001 加密、SEC-002 权限、SEC-003 审计（独立工作项，见 implementation-status 路标 #4）
- **做**：在连接器目录的连接器详情里标注安全特性（是否支持 VIRTUAL 不搬迁、是否权限下推）
- **做**：凭证管理 UX 不回显明文（现有 `secret_data="***"` 脱敏已满足）
- **文档**：在设计中明确"涉密场景需配合 SEC-001/002/003 落地后才能生产可用"

---

## 十一、验收标准

### 11.1 连接器目录 UX

- [ ] 目录页按 6 大品类分组展示连接器卡片
- [ ] 每个连接器卡片含图标 + 名称 + 简介 + 能力标签
- [ ] 支持按名称搜索 + 按能力过滤
- [ ] 选中连接器后展示详情面板（能力表 + 配置字段 + 避坑提示）

### 11.2 各品类接入

- [ ] **关系库**：OpenGauss / GaussDB / TiDB / OceanBase / 达梦 / 金财 至少 3 种可创建数据源 + 探索 schema + 同步到 Iceberg
- [ ] **通用 JDBC 兜底**：任意 JDBC 兼容库可创建数据源（无 Gravitino catalog，仅落地）
- [ ] **湖仓格式**：Hive / Delta / Hudi / Paimon 至少 1 种可注册为联邦源 + Trino 跨 catalog 查询
- [ ] **文件存储**：S3 / MinIO / OSS 至少 1 种可同步文件到 Iceberg（Parquet + CSV 各一）
- [ ] **Kafka**：可创建数据源 + 消费消息落地 Iceberg（路径 B）
- [ ] **ES**：可创建数据源 + 同步 index 到 Iceberg
- [ ] **云数仓**：ADB-PG 或 GaussDB-DWS 可创建数据源 + 探索 + VIRTUAL 联邦查询

### 11.3 VIRTUAL 联邦

- [ ] 关系库（含国产 PG/MySQL 兼容）可登记虚拟表 + Trino 联邦查询
- [ ] 湖仓格式可登记虚拟表 + 跨 catalog Join
- [ ] Kafka 可走 VIRTUAL 路径（Trino kafka connector）
- [ ] NoSQL/时序/SaaS 明确不支持 VIRTUAL（UI 置灰 + 提示）

### 11.4 CDC spike

- [ ] spike 验证报告产出（成功/失败 + 证据）
- [ ] 若成功：`create_external_cdc_pipeline` 实现 + MySQL CDC 端到端跑通（全量 + 增量 ≤ 5s）
- [ ] 若失败：明确根因 + 回落路标，不阻塞其他工作

### 11.5 避坑验证

- [ ] 国产库驱动冲突已规避（openGauss 用独立类名驱动，飞线 1 可移除）
- [ ] Iceberg sink 配置不带 warehouse（postmortem 教训）
- [ ] ES 联邦限制已在前端提示
- [ ] GaussDB 用 gsjdbc200.jar（独立类名）

### 11.6 架构红线

- [ ] 未违反任何架构红线（1~10）
- [ ] 未引入 Redis 依赖
- [ ] VIRTUAL 目标未被写入
- [ ] Doris 索引表名带本体前缀
- [ ] 物理命名走 snake_case（`core/naming.py`）

---

## 十二、路标（三档明确不做项与未来扩展点）

### 12.1 路标项（未来按需触发）

| # | 项 | 触发条件 | 依赖 |
|---|----|---------|------|
| 1 | 时序数据库（InfluxDB/IoTDB） | 真实 IoT/监控场景 | SeaTunnel 原生支持，接入成本低 |
| 2 | 其他 MQ（Rocket/Pulsar/Rabbit） | 真实流式场景 | SeaTunnel 原生支持 |
| 3 | 其他 NoSQL（Mongo/Redis/Cassandra/HBase） | ES 验证模式后扩展 | SeaTunnel 原生支持 + Trino 联邦（Mongo/Redis/Cassandra） |
| 4 | MaxCompute 专用 | 中国云数仓强需求 | 官方 Trino connector 集成 |
| 5 | SaaS 专用（Salesforce/SAP/Jira） | 业务系统强需求 | SeaTunnel HTTP 兜底已覆盖大部分 |
| 6 | CDC 接入主线 | spike 成功 | SeaTunnel #10747 修复 / STREAMING 稳定性 |
| 7 | SEC-001 凭证加密 | 生产/涉密上线前 | AES-256-GCM + KMS |
| 8 | SEC-002 行级权限 | 多租户场景 | Principal 抽象 + Gravitino RBAC |
| 9 | SEC-003 审计入库 | 合规场景 | 审计专用库 |

### 12.2 明确不做（违反 G1/G2）

- ❌ Oracle / SQL Server（用户明确不做，聚焦中国头部）
- ❌ Snowflake / BigQuery / Redshift（全球场景）
- ❌ IoT 工业协议（OPC-UA/OSI PI）—— 三引擎全不覆盖
- ❌ 地理空间影像专用连接器 —— 走 PG-JDBC 子路径
- ❌ 涉密定制连接器 —— 靠开源组合 + VIRTUAL 不搬迁

### 12.3 SPI 扩展点说明（为未来留口，不实现）

若未来需要接入三引擎都不覆盖的数据源（如 IoT 工业协议、自研系统），有三条扩展路径：
1. **Gravitino 自定义 Catalog SPI** —— 开发 Gravitino 自定义 connector，纳管元数据
2. **SeaTunnel 自定义 Source/Sink SPI** —— 开发 SeaTunnel 连接器，搬运数据
3. **Trino 自定义 Connector SPI** —— 开发 Trino connector，联邦查询

三条路径都是各自开源组件的标准扩展机制，不需要 Gaia 自建抽象层（G4）。本期不实现，仅在文档说明。

---

## 十三、调研参考索引

### 13.1 Palantir Foundry（连接器目录 UX 参照）

- [Data Connection • Core concepts](https://palantir.com/docs/foundry/data-connection/core-concepts/) — Capability 模型（Batch syncs/Streaming syncs/CDC/Virtual tables/Exploration 等）
- [Data Connection • Set up a source](https://palantir.com/docs/foundry/data-connection/set-up-source/) — Source 配置流程
- [Available connectors • Snowflake](https://palantir.com/docs/foundry/available-connectors/snowflake/) — 连接器详情页结构范本（Supported capabilities 表 + Connection details + Authentication + Networking + Virtual tables + Data model）
- [Available connectors • Other source types](https://palantir.com/docs/foundry/available-connectors/other-source-types/) — 行业特定连接器
- [Over 150 new sources in Data Connection](https://community.palantir.com/t/over-150-new-sources-are-now-available-in-data-connection/507) — JDBC 覆盖 150+ 源的策略
- [Palantir Foundry Design Patterns](https://spencerfuller.dev/projects/foundry-patterns/) — 三层连接器成熟度（Native/Generic JDBC/REST）

### 13.2 Apache SeaTunnel（数据搬运执行层）

- [SeaTunnel 2.3.13 Release](https://github.com/apache/seatunnel/releases/tag/2.3.13) — 版本特性
- [SeaTunnel JDBC connector](https://seatunnel.apache.org/docs/connectors/sink/Jdbc/) — dialect 列表（含 Dameng/KingBase/OceanBase/Highgo 等）+ GenericDialect 兜底
- [SeaTunnel connector-cdc changelog](https://seatunnel.apache.org/docs/2.3.13/connectors/changelog/connector-cdc/) — OpenGauss-CDC（2.3.8+）/ TiDB-CDC
- [SeaTunnel Iceberg sink](https://seatunnel.apache.org/docs/connectors/sink/Iceberg/) — CDC mode + auto create table + schema evolution
- [SeaTunnel PR #9654](https://github.com/apache/seatunnel/pull/9654) — Iceberg REST catalog integration（2.3.12+ 合入）
- [SeaTunnel issue #10229](https://github.com/apache/seatunnel/issues/10229) — JDBC 驱动包冲突（openGauss vs postgresql）
- [SeaTunnel issue #10242](https://github.com/apache/seatunnel/issues/10242) — "Protocol error. Session setup failed" 根因
- [SeaTunnel issue #10747](https://github.com/apache/seatunnel/issues/10747) — PK 继承导致 append-only CDC 数据丢失（spike 必规避）
- [SeaTunnel issue #9387](https://github.com/apache/seatunnel/issues/9387) — Iceberg REST Catalog OAuth2 URI 限制
- [SeaTunnel S3File source](https://seatunnel.apache.org/docs/2.3.3/connector-v2/source/S3File/) — 文件格式支持
- [SeaTunnel Elasticsearch sink](https://seatunnel.apache.org/docs/2.3.10/connector-v2/sink/Elasticsearch/) — ES 配置
- [SeaTunnel Opengauss CDC](https://seatunnel.apache.org/zh-CN/docs/connectors/source/Opengauss-CDC) — OpenGauss CDC 配置
- [SeaTunnel Kingbase source](https://seatunnel.apache.org/docs/connectors/source/Kingbase/) — 人大金仓
- [SeaTunnel Exactly-Once Semantics](https://seatunnel.apache.org/docs/architecture/fault-tolerance/exactly-once/) — CDC exactly-once
- [达梦 & 人大金仓适配实战](https://segmentfault.com/a/1190000047578410) — 信创数据平台踩坑

### 13.3 Apache Gravitino（元数据纳管）

- [Gravitino 1.2.0 Release Notes](https://gravitino.apache.org/blog/gravitino-1-2-0-release-notes/) — Generic Lakehouse Catalog（#9647，Delta/Hudi/Paimon 统一纳管）
- [Gravitino Kafka catalog](https://github.com/apache/gravitino-site/blob/main/docs/kafka-catalog.md) — Topic 元数据纳管
- [Gravitino Fileset catalog](https://github.com/apache/gravitino-site/blob/main/docs/fileset-catalog.md) — 文件元数据 + GVFS
- [Gravitino Fileset with S3](https://github.com/apache/gravitino-site/blob/main/docs/fileset-catalog-with-s3.md) — S3/MinIO 配置
- [Gravitino manage messaging metadata](https://github.com/apache/gravitino-site/blob/main/docs/manage-messaging-metadata-using-gravitino.md) — Kafka/Pulsar/RocketMQ 元数据
- [Gravitino + Trino Federation](https://dev.to/gravitino/using-apache-gravitino-with-trino-for-query-federation-4doi) — Trino Gravitino Connector 联邦查询

### 13.4 Trino（联邦查询执行层）

- [Trino Elasticsearch connector](https://trino.io/docs/current/connector/elasticsearch.html) — ES 联邦配置
- [Trino ES issue #29158](https://github.com/trinodb/trino/issues/29158) — multi-fields 子字段不可查
- [Trino ES issue #754](https://github.com/trinodb/trino/issues/754) — nested 类型 UnsupportedOperationException
- [Trino Kafka connector](https://trino.io/docs/current/connector/kafka.html) — Kafka 联邦配置
- [Trino Kafka issue #12195](https://github.com/trinodb/trino/issues/12195) — Schema Registry Basic Auth 限制
- [Trino PostgreSQL connector](https://trino.io/docs/current/connector/postgresql.html) — PG 联邦（ADB-PG/GaussDB 复用）

### 13.5 中国云数仓

- [MaxCompute JDBC](https://help.aliyun.com/zh/maxcompute/user-guide/overview-23) — MaxCompute JDBC 驱动
- [aliyun MaxCompute Trino connector](https://github.com/aliyun/aliyun-maxcompute-data-collectors/tree/master/trino-connector) — 官方 Trino connector
- [AnalyticDB PostgreSQL 联邦分析](https://www.alibabacloud.com/help/zh/analyticdb/analyticdb-for-postgresql/user-guide/use-external-tables-for-federated-analytics-of-external-sql-databases) — ADB-PG 外部表
- [GaussDB DWS JDBC 驱动](https://support.huaweicloud.com/intl/zh-cn/devg-dws/dws_04_0090.html) — gsjdbc4 vs gsjdbc200
- [GaussDB 分布式 JDBC 驱动包](https://support.huaweicloud.com/distributed-devg-v3-gaussdb/gaussdb-12-0056.html) — opengaussjdbc / gsjdbc4 / gscejdbc 区别

### 13.6 本项目避坑文档

- [SeaTunnel Iceberg REST 互操作踩坑复盘](../engineer/seatunnel-iceberg-rest-interop-postmortem.md) — API-06 ≠ 不支持 / catalog-impl 透传 / warehouse 404
- [ADR-008 Iceberg→Doris 索引同步路径](../architecture/adr-008-iceberg-doris-sync-path.md) — sync_now 降级 + 修订记录
- [Gravitino 1.3.0 升级记录](../bugfix/gravitino-1.3.0-upgrade.md) — jsonb 未解决 / 6 大飞线 / pgnative workaround
- [数据层设计](./data-layer-design.md) — DataSource/Dataset/ObjectType 完整方案
- [数据集与本体关联设计](./dataset-ontology-binding.md) — Managed/Virtual 术语 + 绑定链路
- [实现状态路标](../architecture/implementation-status.md) — 各组件真实状态

### 13.7 联邦 vs 落地方法论

- [Federated query: how it works and when to use it](https://iomete.com/resources/blog/federated-query-explained) — 联邦查询适用场景（操作型表/SaaS/法规不搬迁）
- [From Elasticsearch to dashboards with Dremio and Iceberg](https://www.dremio.com/blog/from-elasticsearch-to-dashboards-with-dremio-and-iceberg/) — ES vs Iceberg 取舍
- [Beyond Elasticsearch: Choosing an Analytics Store](https://bingcs.com/blog/2026-05-05-beyond-elasticsearch-analytics-store-selection) — ES 优势在检索非聚合

---

## 附：评审决策固化（2026-07-02 评审通过）

以下 7 个决策点已评审确认，作为实现的强制约束：

| # | 决策点 | 最终选择 | 影响 |
|---|--------|---------|------|
| 1 | 国产库范围 | **B — 先做 OpenGauss / GaussDB / TiDB** | OceanBase/达梦/金仓进路标；这 3 种均有 SeaTunnel 原生 CDC（G2 成熟稳定） |
| 2 | 连接器图标 | **B + 官网 favicon** | simple-icons 为主（MySQL/PG/Kafka/ES/Hive 等有官方品牌图标），国产库/无品牌图标的用官网 favicon 补齐 |
| 3 | CDC spike | **A — 独立前置任务** | spike 在连接器扩展之前先做，结果影响 SyncTask 接口设计（是否预留 cdc 模式）；避免返工 |
| 4 | VIRTUAL 边界 | **A — 严格一刀切** | ES 一律落地，不开 Trino 直连口子（即使字段简单）；G4 不过度抽象，避免破坏 Catalog 层统一性 |
| 5 | 安全债 | **A — 本期纯路标** | SEC-001/002/003 全部不做，涉密场景靠 VIRTUAL 不搬迁天然适配；文档明确标注"涉密生产可用需配合 SEC 落地" |
| 6 | MaxCompute | **A — 路标** | 本期只做 ADB-PG / GaussDB-DWS（复用 PG 通道，零额外成本）；MaxCompute 独立 Trino connector 违反 G4 |
| 7 | 通用 JDBC 兜底 | **A — 本期做** | `connector_type="generic_jdbc"` 覆盖任意 JDBC 兼容库，无 Gravitino catalog（仅落地）；SeaTunnel GenericDialect 原生支持，实现成本极低 |

### 实现任务规划（基于评审决策）

```
阶段 0 - CDC spike（独立前置任务，决策点 3）
  S0.1  准备外部 MySQL 测试库 + 开启 binlog
  S0.2  提交 SeaTunnel CDC → Iceberg job（按 §7.3.2 步骤）
  S0.3  验证全量快照 + 增量 CDC + Doris 同步 + schema 演进
  S0.4  规避 8 个已知坑（§7.3.4）
  S0.5  产出 spike 验证报告（成功/失败 + 证据）
  S0.6  若成功：设计 SyncTask 的 cdc 模式接口

阶段 1 - 后端连接器扩展（P0）
  S1.1  扩展 _JDBC_CONNECTOR_MAP / _JDBC_DRIVER_MAP / _JDBC_URL_SCHEME（OpenGauss/GaussDB/TiDB + generic_jdbc）
  S1.2  扩展 CAPABILITY_MAP（所有入选品类）
  S1.3  GravitinoRegistry 新增 register_lakehouse_catalog / register_kafka_catalog / register_fileset_catalog
  S1.4  SeaTunnelEngine 新增 create_file_sync_pipeline / create_kafka_ingestion_pipeline
  S1.5  SeaTunnelEngine 新增 create_external_cdc_pipeline（若 S0 spike 成功）
  S1.6  DataSourceService 按 connector_type 分流（JDBC/File/Kafka/generic_jdbc）
  S1.7  单元测试 + 本地冒烟（每种连接器 create/explore/sync）

阶段 2 - 前端连接器目录（P0）
  S2.1  抽取 constants/connectorCatalog.ts（完整 ConnectorMeta 结构）
  S2.2  引入 simple-icons + 收集官网 favicon（国产库）
  S2.3  DataSourceForm Step 1 改为分品类目录页（§5.2.2）
  S2.4  新增 ConnectorDetailPanel（§5.2.3）
  S2.5  DataSourceForm Step 2 按 configSchema 动态渲染配置表单
  S2.6  typecheck + build + 本地页面验证

阶段 3 - 文件/对象存储接入（P0）
  S3.1  SeaTunnel S3File/OssFile/HdfsFile source 模板
  S3.2  Gravitino Fileset catalog 注册
  S3.3  端到端验证（S3/MinIO + Parquet/CSV 各一）

阶段 4 - 二档品类（P1）
  S4.1  湖仓格式联邦源（Hive/Delta/Hudi/Paimon 经 Gravitino 纳管）
  S4.2  Kafka 接入（VIRTUAL 联邦 + 落地双通道）
  S4.3  Elasticsearch 接入（落地为主，严格一刀切）
  S4.4  ADB-PG / GaussDB-DWS 接入（复用 PG 通道）

阶段 5 - 验收
  S5.1  按 §十一验收标准逐项验证
  S5.2  架构红线检查
  S5.3  更新 implementation-status.md
```

---

*设计文档 v1.1 定稿。实现阶段遵循 G5：每个连接器接入前先查官方文档 + 避坑 + Palantir 交互参照。*

---

## 附：实现阶段 Live 验证修正记录（2026-07-02，v1.1 → 实现）

> 本节记录 live 验证发现的设计文档初版错误，作为后续接入者的避坑参考。代码以这些修正为准；设计文档正文保留原样以记录决策演进。详见 [ADR-014](../architecture/adr-014-multi-source-data-fusion-connectors.md)、[cdc-spike-report.md](../engineer/cdc-spike-report.md)、[starrocks-seatunnel-dryrun.md](../engineer/starrocks-seatunnel-dryrun.md)。

### 修正 1：CDC source 字段名（§7.3.2）

SeaTunnel 2.3.13 MySQL-CDC source 的配置字段名与设计文档初版不同：
- ❌ 初版（错）：`hostname` / `port` / `database-name` / `table-name`
- ✅ 实际（对）：`base-url`（完整 JDBC URL）+ `table-names`（复数 list）

代码 `PIPELINE_EXTERNAL_CDC_TEMPLATE` 已用 `base-url` + `table-names`，`create_external_cdc_pipeline` 自动从 `hostname`/`port`/`database_name` 构建 `base_url`。

### 修正 2：S3File 配置字段（§6.3.4）

SeaTunnel 2.3.13 S3File source 实际配置（live 验证）：
- `bucket = "s3a://xxx"`（带 `s3a://` 前缀，非裸 bucket 名）
- `fs.s3a.endpoint`（非 `endpoint`）
- `fs.s3a.aws.credentials.provider = org.apache.hadoop.fs.s3a.SimpleAWSCredentialsProvider`
- `hadoop_s3_properties { fs.s3a.path.style.access = true }`（RustFS/MinIO 必需，否则虚拟主机样式解析 `<bucket>.<host>` 报 `UnknownHostException`）
- CSV 需 `skip_header_row_number = 1`（否则表头被当数据，`NumberFormatException`）

代码 `PIPELINE_FILE_SYNC_TEMPLATE` 已修正。

### 修正 3：Gravitino Fileset catalog provider（§6.3.4）

设计文档初版写 `provider="s3"` 是错的——Gravitino 1.3.0 的 fileset catalog provider **统一是 `"fileset"`**（存储后端由 `location` 的 scheme 决定：`s3a://`/`hdfs://`）。`_FILESET_PROVIDER_MAP` 全部映射到 `"fileset"`。

### 修正 4：Kafka source 字段（§6.4.4）

- `format`（数据格式 json/text），非 `pattern`（`pattern` 是 topic 正则匹配）
- `start.mode=earliest` 需配合**全新消费组** + `kafka.config.auto.offset.reset=earliest` 才能消费历史消息（消费组有 committed offset 时优先用）

代码 `PIPELINE_KAFKA_INGESTION_TEMPLATE` 已修正，新增 `start.mode` + `kafka.config` 透传。

### 修正 5：GaussDB 驱动（§6.1.2）

设计文档初版写 GaussDB 用 `gsjdbc200.jar`（`com.huawei.gauss200.jdbc.Driver`），但 gsjdbc200 **不在公网 Maven**。改用 `opengaussjdbc`（`com.huaweicloud.gaussdb:opengaussjdbc:506.0.T35`，华为官方公开版，同 driver 类 `com.huawei.opengauss.jdbc.Driver`，同时支持 openGauss/GaussDB，且不含 `org/postgresql/Driver.class`，与标准 PG 驱动共存无冲突）。`_JDBC_DRIVER_MAP` 的 gaussdb/gaussdb_dws 已改为 `com.huawei.opengauss.jdbc.Driver`。

### 修正 6：StarRocks 接入（设计文档未列，实现补充）

StarRocks 与 Doris 同构（MySQL 协议 OLAP），三引擎原生支持且镜像内置（Gravitino `jdbc-starrocks` catalog + SeaTunnel `connector-starrocks`），已补充接入：
- `_JDBC_CONNECTOR_MAP`: `jdbc-starrocks` provider
- `_JDBC_DRIVER_MAP`/`_JDBC_URL_SCHEME`/`_default_port`: MySQL 协议，9030
- `_JDBC_CATALOG_FACTORY`: `StarRocks` dialect（SeaTunnel 2.3.8+ #7294）
- `CAPABILITY_MAP`: `[explore, batch_sync, virtual_table]`
- JDBC 路径 dry-run 验证通过；专用 StarRocks connector（BE 直读）为可选优化

### Live 验证状态汇总

| 品类 | live 验证 | 证据 |
|------|----------|------|
| CDC（MySQL-CDC→Iceberg） | ✅ | 全量+增量 upsert（#10747 规避）+ `POST /cdc-sync` 端到端 |
| 文件存储（RustFS + Parquet/CSV） | ✅ | `create_file_sync_pipeline` 端到端 + Gravitino fileset catalog |
| Kafka（实时+earliest） | ✅ | `create_kafka_ingestion_pipeline` + messaging catalog |
| 国产库驱动（opengauss/kingbase/oceanbase/达梦） | ✅ | Gravitino + SeaTunnel 双侧类加载验证，docker-compose 持久化 |
| StarRocks | ✅ | Gravitino `jdbc-starrocks` catalog 注册 + JDBC dialect dry-run |
| ES / 湖仓格式 | 🟡 | 代码就绪，待外部源容器 |
