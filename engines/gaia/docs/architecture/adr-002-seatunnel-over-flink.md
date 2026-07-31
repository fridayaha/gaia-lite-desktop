# ADR-002: 使用 SeaTunnel 而非 Flink 作为 Pipeline 层

| 字段 | 内容 |
| ---- | ---- |
| **状态** | 已采纳 |
| **决策日期** | 2026-05（架构 v5 终稿） |
| **影响层** | `layers/pipeline/SeaTunnelEngine` |
| **相关 ICD** | —（Pipeline 层无独立 ICD，方法签名见 `architecture_plan.md` §十六 ICD-06） |
| **关联文档** | `architecture_plan.md` §1.4（Gravitino↔SeaTunnel 集成）、`docs/engineer/cdc-spike-report.md`、ADR-008（Iceberg→Doris 同步路径） |

---

## 背景

Pipeline 层负责数据的采集、清洗、写入与 CDC。这一层需要承担两类职责：

1. **主数据写入流水线**：外部数据源（MySQL/Kafka/文件/...）→ Iceberg（主数据唯一写入入口）
2. **衍生搬运链路**：外部 CDC → Iceberg、Kafka → Iceberg/TimescaleDB、S3File → Iceberg 等（均「外部源 → Iceberg/TimescaleDB」方向）。**Iceberg → Doris / Neo4j / PostGIS 的写入不走 SeaTunnel**（2026-07 去 SeaTunnel 化，改 Python 侧 `ObjectIndexFunnel` / `OutboxExecutor` 直连，见 ADR-008 修订记录 + graph-reasoning-design §6）

候选方案有三类：

| 方案 | 定位 | 代表项目 |
| ---- | ---- | -------- |
| 流批一体计算引擎 | 功能最强，复杂流处理（多流 JOIN、CEP、窗口） | Apache Flink |
| 微批流处理 | 批处理思维，吞吐高但延迟大 | Spark Streaming |
| 数据集成 / 配置驱动 ETL | 声明式 source→sink，无需写计算代码 | Apache SeaTunnel |

## 决策

**采用 Apache SeaTunnel 2.3.13 作为 Pipeline 层引擎**，理由如下：

### 1. 与本架构的契合度最高

Gaia 的数据流是**配置驱动的 source→sink 管道**（Iceberg 写入、Doris 索引同步、CDC 落地），不需要复杂的事件时间窗口、状态计算或 CEP。SeaTunnel 的声明式 conf（Jinja2 模板生成）天然匹配这种形态：

```
source(JDBC-CDC / Kafka / S3File) → transform(field-mapping/filter) → sink(Iceberg / Doris / TimescaleDB)
```

Flink 的 DataStream API / Table API 在本场景属于杀鸡用牛刀——它的核心价值（有状态流计算）在本架构里没有用武之地，反而引入不必要的复杂度。

### 2. 与 Gravitino 生态深度集成

- SeaTunnel dev 分支（PR #10402）支持 `schema_url` 自动从 Gravitino REST API 拉取表结构，减少配置重复
- Gravitino 官方把 SeaTunnel 列为首选集成对象，预计 SeaTunnel 3.0.0 正式发布深度集成
- 当前（2.3.13 稳定版）手动配置 schema，待 3.0.0 升级后自动拉取

### 3. 部署轻量、运维简单

- SeaTunnel Zeta 集群自愈，单容器即可起步（开发环境），生产扩到 4-6 节点
- 配置即代码（Git 管理 conf），无需编译打包作业 JAR
- Flink 需要维护 JobManager/TaskManager、Checkpoint 状态后端、作业打包部署，运维成本显著更高

### 4. 连接器生态覆盖数据集成场景

SeaTunnel 内置 250+ 连接器，覆盖本项目所需的 MySQL-CDC / PostgreSQL-CDC / Kafka / S3File / Iceberg / JDBC-StarRocks / 国产库（openGauss/Kingbase/OceanBase，见 ADR-014）等。CDC 能力经 live dry-run 验证可用（`cdc-spike-report.md`）。

## 后果

### 正面

- **配置驱动，开发效率高**：新流水线 = 写一个 Jinja2 模板 + 调 `SeaTunnelEngine.create_*_pipeline()`，无需写 Flink 作业代码
- **与架构红线一致**：SeaTunnel 承担 PipelineBuilder（红线 #6，收窄为「外部源→Iceberg/TimescaleDB 搬运」），Zeta 任务组只跑 MAIN / FILE_SYNC / KAFKA_* / EXTERNAL_CDC（Iceberg→Doris 同步已于 2026-07 去 SeaTunnel 化，改 ObjectIndexFunnel）
- **升级路径清晰**：2.3.13 → 3.0.0 后获得 Gravitino `schema_url` 自动拉取

### 负面 / 已知限制

- **复杂流处理能力弱于 Flink**：不支持多流 JOIN、CEP、复杂窗口。当前架构无此需求；若未来出现（如实时风控多流关联），需重新评估
- **~~SeaTunnel 2.3.13 的 Iceberg source 不支持 REST Catalog~~**（该判断已于 2026-06-25 证伪，2.3.13 实际支持）：Iceberg→Doris 索引同步曾走 `sync_now` 兜底，后落地 backfill(BATCH)+stream(STREAMING) 双模板（ADR-008），**最终于 2026-07 T1.10 整体删除**，改 `ObjectIndexFunnel`（Python 侧 `DorisIndexStore.upsert`，统一 rid 分配/复用）。SeaTunnel 不再参与 Iceberg→Doris 同步
- **Gravitino 深度集成仍在 dev 分支**：当前手动配 schema，有配置重复风险
- **字段名跨版本变动**：SeaTunnel 2.3.13 各 connector 字段名与文档初版不符（CDC 要 `base-url`+`table-names` 非 `hostname`/`port`；S3File 要 `fs.s3a.endpoint`+`hadoop_s3_properties`），新连接器接入必须 live dry-run（见 CLAUDE.md 通用错误模式 #9）

## 替代方案（否决）

| 方案 | 否决原因 |
| ---- | -------- |
| **Apache Flink** | 功能更强但运维复杂（JobManager/TaskManager、状态后端、作业打包）；本项目无复杂流处理需求，核心价值用不上；连接器生态偏计算而非数据集成 |
| **Spark Streaming** | 微批模型延迟大；批处理思维与实时 CDC 场景不匹配；部署重 |
| **自研轻量 Pipeline** | 重新发明 SeaTunnel 已有的连接器、容错、Exactly-Once 语义，维护成本高 |

## 回归条件

出现以下任一情况，需重新评估是否引入 Flink：

1. 业务出现**多流 JOIN / CEP / 复杂事件时间窗口**需求，SeaTunnel 无法表达
2. SeaTunnel CDC 在高吞吐（>50K rows/s）场景下出现不可接受的延迟或数据丢失
3. SeaTunnel 社区停滞（3.0.0 长期不发），连接器生态无法覆盖新数据源

## 修订记录

- **2026-05 初始决策**：架构 v5 终稿选定 SeaTunnel
- **2026-07-02**：ADR-014 多源融合扩展 SeaTunnel 连接器到 25 种，CDC live 验证通过，本决策适用范围得到验证
- **2026-07-08**：object_state 同步去 SeaTunnel 化（改 outbox 驱动）。
- **2026-07 T1.10**：Iceberg→Doris 同步去 SeaTunnel 化（删除 `create_index_pipeline` / backfill / stream 模板，改 `ObjectIndexFunnel`）。至此 SeaTunnel 职责完全收窄为「外部源→Iceberg/TimescaleDB 搬运」，**不再参与任何 Doris/Neo4j/PostGIS 写入**。本决策（SeaTunnel over Flink）仍成立——搬运职责仍由 SeaTunnel 承担。
