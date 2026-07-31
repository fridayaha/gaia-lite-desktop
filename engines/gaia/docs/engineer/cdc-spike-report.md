# CDC Spike 验证报告 — 外部数据源 CDC → Iceberg → Doris

> **文档性质**: Spike 验证报告（multi-source-data-fusion-design.md §7.3 阶段 0 前置任务）
> **日期**: 2026-07-02
> **状态**: ✅ **live 验证通过**（路径 a 接入主线）
> **结论**: `SeaTunnel MySQL-CDC → Iceberg sink (REST Catalog)` 链路在 Gaia 现有环境（SeaTunnel 2.3.13 + Gravitino 1.3.0）**端到端跑通**。全量快照、增量 CDC（INSERT/UPDATE/DELETE upsert）、worker 稳定性、schema 演进均已 live 验证。`start_cdc_sync` 可接入主线。数据进 Iceberg 后由 `ObjectIndexFunnel` 同步到 Doris（不经 SeaTunnel，去 SeaTunnel 化后订正）。

---

## 一、Spike 目标

验证 `SeaTunnel CDC source（MySQL-CDC/PG-CDC）→ Iceberg sink（REST Catalog）` 链路在 Gaia 现有环境下可用（§7.3.1）。数据进 Iceberg 后由 ObjectIndexFunnel 同步到 Doris。

## 二、Live 验证环境

| 组件 | 版本/实例 | 角色 |
|------|----------|------|
| SeaTunnel | 2.3.13（ontology-seatunnel-master/worker） | CDC source + Iceberg sink |
| Gravitino Iceberg REST Catalog | 1.3.0（9001） | Iceberg 元数据 |
| Trino | 478（ontology-trino） | Iceberg 查询验证 |
| RustFS | latest（S3 兼容） | Iceberg 数据存储 |
| 外部 MySQL | 8.0.46（marketing-mysql 容器） | CDC 源库（log_bin=ON, binlog_format=ROW, binlog_row_image=FULL, server_id=1） |

## 三、Live 验证步骤与结果（§7.3.2）

### Step 1: 准备外部 MySQL 测试库 ✅

```sql
CREATE TABLE test_cdc_source (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  amount DECIMAL(10,2) DEFAULT 0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;
INSERT INTO test_cdc_source (name, amount) VALUES ('alpha', 100.50), ('beta', 200.75), ('gamma', 300.00);
```
binlog 已开启（ROW + FULL），server_id=1。✅

### Step 2: 提交 SeaTunnel CDC → Iceberg job ✅

**关键修正（联网调研发现）**：SeaTunnel 2.3.13 MySQL-CDC source 的配置字段名与设计文档初版不同：
- ❌ 设计文档初版（错）：`hostname` / `port` / `database-name` / `table-name`
- ✅ SeaTunnel 2.3.13 实际（对）：`base-url`（完整 JDBC URL）+ `table-names`（复数 list）

> ⚠️ **代码修正**：`PIPELINE_EXTERNAL_CDC_TEMPLATE` 已更新为 `base-url` + `table-names`，并在 source 配置里支持 `schema-changes.enabled`。

提交配置（HOCON）：
```hocon
env { job.mode = "STREAMING", checkpoint.interval = 10000 }
source {
  MySQL-CDC {
    base-url = "jdbc:mysql://marketing-mysql:3306/marketing_benchmark"
    username = "root"; password = "marketing123"
    table-names = ["marketing_benchmark.test_cdc_source"]
    server-time-zone = "Asia/Shanghai"
  }
}
sink {
  Iceberg {
    iceberg.catalog.config = {
      catalog-impl = "org.apache.iceberg.rest.RESTCatalog"  # postmortem: 非 type=rest
      uri = "http://gravitino:9001/iceberg"                 # postmortem: 不带 warehouse
      "s3.endpoint" = "http://rustfs:9000"; "s3.access-key-id" = "minioadmin"; ...
    }
    namespace = "ontology"; table = "test_cdc_target"
    iceberg.table.primary-keys = "id"                       # #10747 规避
    iceberg.table.upsert-mode-enabled = "true"              # CDC upsert
  }
}
```
Job 提交成功，`jobId` 返回，state=RUNNING，无错误。✅

### Step 3: 验证全量快照写入 ✅

```sql
-- Trino 查询
SELECT * FROM iceberg.ontology.test_cdc_target ORDER BY id;
"1","alpha","100.50","2026-07-01 21:46:28.000000 UTC"
"2","beta","200.75","2026-07-01 21:46:28.000000 UTC"
"3","gamma","300.00","2026-07-01 21:46:28.000000 UTC"
```
3 条初始数据正确写入 Iceberg。✅

### Step 4: 验证增量 CDC（INSERT/UPDATE/DELETE）✅ **核心验证**

MySQL DML：
```sql
INSERT INTO test_cdc_source (name, amount) VALUES ('delta', 400.00);   -- INSERT
UPDATE test_cdc_source SET amount = 999.99 WHERE name = 'alpha';       -- UPDATE
DELETE FROM test_cdc_source WHERE name = 'beta';                       -- DELETE
```
等 checkpoint（~10s）后查 Iceberg：
```
"1","alpha","999.99",...   ← UPDATE 生效（upsert，非 append 重复行）
"3","gamma","300.00",...
"4","delta","400.00",...   ← INSERT 生效
-- beta(id=2) 已删除        ← DELETE 生效（positional-delete/upsert）
```
**三项 DML 全部正确反映**，延迟 ~15s（受 checkpoint.interval=10s 限制，调小可达 ≤5s）。✅

> **#10747 规避验证（关键）**：UPDATE 把 alpha 的 amount 从 100.50 改成 999.99，Iceberg 表里是**覆盖**而非新增重复行——证明显式 `primary-keys` + `upsert-mode-enabled` 成功规避了 SeaTunnel issue #10747（PK 继承导致 append-only CDC 数据丢失）。

### Step 5: Iceberg → Doris 同步 🟡 复用既有路径

Doris 同步复用现有 `IndexSyncService.sync_now`（Trino 读 Iceberg → Doris upsert，ADR-008 已验证小表可用）。CDC spike 的核心是 CDC→Iceberg 段（已 ✅），Doris 段是下游既有路径，不阻塞 spike 结论。target 表非 object_type 索引表，sync_now 需适配（后续接入主线时按 object_type 走）。

### Step 6: schema 演进 ✅（需显式开启）

MySQL 加列 `ALTER TABLE test_cdc_source ADD COLUMN status VARCHAR(20)`：
- 默认配置：Iceberg 表 schema **未自动加列**（SeaTunnel Iceberg sink `schema-evolution-enabled` 默认 false）。
- 开启 `iceberg.table.schema-evolution-enabled = "true"` + source `schema-changes.enabled = true` 后：✅ schema 演进生效。

> **代码已支持**：`PIPELINE_EXTERNAL_CDC_TEMPLATE` 渲染 `schema-evolution-enabled`，`create_external_cdc_pipeline` 的 source_config 支持透传。

### Worker 稳定性 ✅

STREAMING job 持续运行 ~4 分钟无 crash。metrics：`TableSourceReceivedCount=9`，`SinkCommittedCount=9`（全部提交成功）。设计文档 §7.3.4 坑 #4「STREAMING worker crash（EventService NPE）」**未复现**。✅

## 四、已知坑规避状态（§7.3.4）

| # | 坑 | 规避状态 | 证据 |
|---|----|---------|------|
| 1 | Iceberg sink 带 warehouse 导致 404 | ✅ 模板不带 warehouse | Step 2 job 成功 |
| 2 | type="rest" 被枚举拒 | ✅ 用 catalog-impl=RESTCatalog | Step 2 job 成功 |
| 3 | 表名大小写敏感 | ✅ table 小写 | Step 3 数据正确 |
| 4 | STREAMING worker crash | ✅ 未复现 | 4 分钟稳定运行 |
| 5 | PK 继承 append-only 数据丢失（#10747） | ✅ 显式 PK + upsert-mode | Step 4 UPDATE 覆盖非重复 |
| 6 | exactly-once 默认关闭 | ⚠️ 生产按需开启 | 非阻塞 |
| 7 | PG CDC 需 wal_level=logical | ⚠️ PG 源库配置要求 | MySQL 已验，PG 待源库 |
| 8 | timestamptz 类型映射 | ⚠️ 待 PG 源验 | MySQL 未涉 |

## 五、国产库 JDBC 驱动部署（§6.1.2，已 live 验证）

设计文档要求 openGauss/GaussDB/Kingbase/OceanBase 驱动就位。已下载并部署：

| 驱动 | jar | Maven 坐标 | Driver 类名 | SeaTunnel | Gravitino |
|------|-----|-----------|------------|-----------|-----------|
| openGauss/GaussDB | opengaussjdbc-506.0.T35.jar | `com.huaweicloud.gaussdb:opengaussjdbc:506.0.T35` | `com.huawei.opengauss.jdbc.Driver` | ✅ | ✅ |
| Kingbase | kingbase8-8.6.1.jar | `cn.com.kingbase:kingbase8:8.6.1` | `com.kingbase8.Driver` | ✅ | ✅ |
| OceanBase | oceanbase-client-2.4.14.jar | `com.oceanbase:oceanbase-client:2.4.14` | `com.oceanbase.jdbc.Driver` | ✅ | ✅ |
| 达梦 DM | DmJdbcDriver18-8.1.2.141.jar | 镜像内置 | `dm.jdbc.driver.DmDriver` | ✅ | — |

**驱动加载 live 验证**（指向假主机，确认类加载而非连接）：
- SeaTunnel：提交 openGauss/Kingbase JDBC source job，堆栈显示 `com.huawei.opengauss.jdbc.Driver.connect` / `com.kingbase8.Driver.connect` 已执行（失败在 DNS 解析，非 ClassNotFoundException）。✅
- Gravitino：注册 openGauss/Kingbase/OceanBase JDBC catalog，`code:0, in-use:true`（driver 找不到会返回错误）。✅

**关键避坑验证**：华为官方 `opengaussjdbc-506`（driver `com.huawei.opengauss.jdbc.Driver`）**不含** `org/postgresql/Driver.class`，与标准 `postgresql-42.4.3.jar` 共存无同名类冲突。`infra/seatunnel-entrypoint.sh`（飞线 1）仍保留——处理镜像自带的社区版 `opengauss-jdbc-5.1.0.jar`（含同名 `org.postgresql.Driver.class`，会冲突），将其 park 到 `lib.parked/`。华为官方版文件名 `opengaussjdbc-*`（无连字符）不匹配 entrypoint 的 glob `opengauss-jdbc-*.jar`，不受影响。

> **代码修正**：设计文档 §6.1.2 原写 GaussDB 用 `gsjdbc200.jar`（`com.huawei.gauss200.jdbc.Driver`），但 gsjdbc200 不在公网 Maven。改用 `opengaussjdbc`（华为官方公开版，同 driver 类 `com.huawei.opengauss.jdbc.Driver`，同时支持 openGauss 和 GaussDB）。`_JDBC_DRIVER_MAP` 的 gaussdb/gaussdb_dws 已改为 `com.huawei.opengauss.jdbc.Driver`。

**持久化**：jar 放 `infra/jars/`，通过 docker-compose bind-mount 到 SeaTunnel `lib/` 和 Gravitino 各 catalog `libs/`。容器 `--force-recreate` 重建后驱动仍在（已验证）。

## 六、结论

| 项 | 结论 |
|----|------|
| 路径 a 技术可行性 | ✅ **live 验证通过** |
| 全量快照 | ✅ 数据正确 |
| 增量 CDC（INSERT/UPDATE/DELETE） | ✅ upsert 生效，#10747 规避成功 |
| worker 稳定性 | ✅ STREAMING 无 crash |
| schema 演进 | ✅ 需显式开启（代码已支持） |
| 国产库驱动 | ✅ 双侧加载验证通过，持久化 |
| **接入主线** | ✅ `start_cdc_sync` + `POST /cdc-sync` 可用 |

**CDC spike 成功，接入主线。** SyncTask 已预留 cdc 模式，`DataSourceService.start_cdc_sync` 已实现。CDC（外部源→Iceberg）作为外部数据实时增量接入；数据进 Iceberg 后由 ObjectIndexFunnel 同步到 Doris（不经 SeaTunnel）。`sync_now` 维持为不依赖 SeaTunnel 的容灾兜底。

> **注**：原文此处提及的「路径 A（sync_now）/ 路径 B（PG→Kafka→Doris）」二分法中，路径 B 已于 2026-07-08 去 SeaTunnel 化删除（object_state 同步改 outbox），不再适用。CDC spike 本身（外部源→Iceberg）仍为当前真相。
