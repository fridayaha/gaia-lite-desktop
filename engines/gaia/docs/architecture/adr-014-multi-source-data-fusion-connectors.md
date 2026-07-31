# ADR-014: 多源异构数据融合连接器体系

| 字段 | 值 |
|------|-----|
| 状态 | Accepted |
| 日期 | 2026-07-02 |
| 决策者 | 开发者 + 评审（2026-07-02 评审通过 7 决策点） |
| 影响 | DataSourceService / SeaTunnelEngine / GravitinoRegistry / 前端连接器目录 |
| 关联文档 | [multi-source-data-fusion-design.md](../design/multi-source-data-fusion-design.md)、[cdc-spike-report.md](../engineer/cdc-spike-report.md)、[starrocks-seatunnel-dryrun.md](../engineer/starrocks-seatunnel-dryrun.md) |

## 背景

Gaia 本体建模依赖数据融合——ObjectType 属性必须映射到真实数据列才有业务意义。接入前 Gaia 仅支持 MySQL/PostgreSQL 两种关系库（`_JDBC_CONNECTOR_MAP` 硬编码 4 条目），无法满足「多源异构数据全域融合」的平台定位。

需在现有 6 层架构（Gravitino + SeaTunnel + Iceberg + Doris + Trino + PostgreSQL）上扩展连接器覆盖，对标 Palantir Foundry Data Connection 的能力。

## 决策

### D1: 不自研连接器框架，配置驱动 + 复用开源原生能力（G4）

不建 `ConnectorRegistry` / `ConnectorPlugin` / SPI 框架。用配置驱动的映射表（`_JDBC_CONNECTOR_MAP` / `_JDBC_DRIVER_MAP` / `_JDBC_URL_SCHEME` / `_FILESET_PROVIDER_MAP` / `_LAKEHOUSE_PROVIDER_MAP` / `CAPABILITY_MAP` / 前端 `connectorCatalog.ts`）+ 直接复用 SeaTunnel/Gravitino/Trino 原生 connector。

**理由**：开源三引擎已覆盖目标品类（关系库/湖仓/文件/Kafka/NoSQL/云数仓），自建抽象是重复造轮子，违反 G4。新连接器接入 = 加映射表条目 + 前端 catalog 条目，零抽象成本。

### D2: 品类一刀切 VIRTUAL 边界（决策点 4）

VIRTUAL 联邦（不搬迁）仅对 Gravitino 纳管的品类开放：关系库 JDBC / 湖仓格式 / Kafka。其余（文件存储 / ES / 时序 / SaaS / 达梦 / MaxCompute）一律 MANAGED 落地，**不解耦 Trino 直连绕过 Gravitino**。

**理由**：解耦会破坏 Catalog 层统一性（G4）；NoSQL 联邦发挥不出 NoSQL 自身优势（ES 全文检索、Mongo 文档查询）；Palantir 也是「关系库+湖仓+Kafka 走 VIRTUAL，其余落地」。

### D3: CDC 走 SeaTunnel CDC source → Iceberg（路径 a，spike 验证通过）

外部业务库 CDC 用 SeaTunnel CDC source（MySQL-CDC / PostgreSQL-CDC / Opengauss-CDC / TiDB-CDC）→ Iceberg sink（REST Catalog）。数据进 Iceberg 后，由 `ObjectIndexFunnel`（从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`，统一 rid 分配/复用 + 四引擎扇出）同步到 Doris / Neo4j / PostGIS——**这段不走 SeaTunnel**（2026-07 去 SeaTunnel 化）。`sync_now`（Trino 读 + Doris upsert）维持为不依赖 SeaTunnel 的容灾兜底。

**关键避坑（live 验证）**：
- Iceberg sink 显式 `primary-keys` + `upsert-mode-enabled`，规避 SeaTunnel #10747（PK 继承导致 append-only CDC 数据丢失）
- Iceberg sink config 不带 `warehouse`（Gravitino REST /v1/config 404，见 postmortem）
- 用 `catalog-impl = org.apache.iceberg.rest.RESTCatalog`（非 `type=rest`，枚举拒）

### D4: 国产库用独立类名驱动，规避同名类冲突（§6.1.2）

openGauss / GaussDB / Kingbase / OceanBase / 达梦 用独立类名驱动包，避免与官方 PG/MySQL 驱动同名 `org.postgresql.Driver` / `com.mysql.cj.jdbc.Driver` 冲突（SeaTunnel #10229/#10242，`AbstractJdbcCatalog` 无法区分同名 driver）。

| 数据源 | 驱动包 | Driver 类名 |
|--------|--------|------------|
| openGauss/GaussDB | opengaussjdbc-506（华为官方，Maven Central） | `com.huawei.opengauss.jdbc.Driver` |
| Kingbase | kingbase8-8.6.1 | `com.kingbase8.Driver` |
| OceanBase | oceanbase-client-2.4.14 | `com.oceanbase.jdbc.Driver` |
| 达梦 DM | DmJdbcDriver18（镜像内置） | `dm.jdbc.driver.DmDriver` |

> **设计文档初版修正**：原写 GaussDB 用 `gsjdbc200.jar`（`com.huawei.gauss200.jdbc.Driver`），但 gsjdbc200 不在公网 Maven。改用 `opengaussjdbc`（华为官方公开版，同 driver 类，同时支持 openGauss/GaussDB，且不含 `org/postgresql/Driver.class`，与标准 PG 驱动共存无冲突）。

驱动放 `infra/jars/`，docker-compose bind-mount 到 SeaTunnel `lib/` + Gravitino 各 catalog `libs/`，容器重建后持久化。

### D5: StarRocks 复用 jdbc-starrocks 通路，JDBC 路径优先

StarRocks 与 Doris 同构（MySQL 协议 OLAP），走 Gravitino `jdbc-starrocks` 原生 provider + SeaTunnel JDBC source（`catalog { factory = "StarRocks" }` dialect）。专用 StarRocks connector（BE 直读）为可选性能优化，按 G3 二八原则后续按需触发。

## 替代方案

| 方案 | 否决理由 |
|------|---------|
| 自建 ConnectorRegistry / SPI 框架 | 违反 G4（不过度抽象），重复造轮子 |
| 复刻 Palantir 200+ 连接器 | 违反 G1（不复刻），开源三引擎已覆盖目标品类 |
| NoSQL/时序/SaaS 解耦 Trino 直连 | 违反 G4，破坏 Catalog 层统一性（D2） |
| 自研 CDC 中间件 | SeaTunnel CDC 已成熟，spike 验证通过 |
| 用 gsjdbc200 for GaussDB | 不在公网 Maven，改用 opengaussjdbc（D4） |

## 后果

**正面**：
- 连接器从 2 种扩展到 25 种（6 大品类 + generic_jdbc + StarRocks）
- 国产库驱动双侧加载验证通过，docker-compose 持久化
- CDC spike live 验证通过（全量+增量 upsert+#10747 规避+worker 稳定+schema 演进）
- 文件存储（RustFS + Parquet/CSV）、Kafka（实时+earliest）live 验证通过
- 新连接器接入成本极低（加映射表 + 前端 catalog 条目）

**负面/遗留**：
- ES / 湖仓格式 live 验证待外部源容器
- 现有 `PIPELINE_SYNC_TEMPLATE`（JDBC sync 通用模板）Iceberg sink 仍用 `type=rest`，未迁移到 postmortem 的 `catalog-impl`（独立工作项）
- SeaTunnel 2.3.13 各 connector 字段名与文档初版多处不符（CDC `base-url`/`table-names`、S3File `fs.s3a.endpoint`/`hadoop_s3_properties`、Kafka `format` 非 `pattern`），依赖 live 验证发现

## 验证证据

- CDC：MySQL-CDC → Iceberg 全量+增量 upsert（#10747 规避确认）+ `POST /cdc-sync` 端到端
- 文件存储：RustFS + Parquet/CSV → Iceberg，`create_file_sync_pipeline` 端到端
- Kafka：实时流式落地 + earliest 历史消费 + Gravitino messaging catalog 注册
- 国产库驱动：Gravitino + SeaTunnel 双侧 catalog 注册/类加载验证
- StarRocks：Gravitino `jdbc-starrocks` catalog 注册 + JDBC dialect dry-run

详见 [cdc-spike-report.md](../engineer/cdc-spike-report.md) 和 [starrocks-seatunnel-dryrun.md](../engineer/starrocks-seatunnel-dryrun.md)。
