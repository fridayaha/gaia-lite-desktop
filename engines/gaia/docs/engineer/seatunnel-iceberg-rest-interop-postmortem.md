# 踩坑复盘：SeaTunnel 2.3.13 + Gravitino 1.2.0 Iceberg REST Catalog 互操作

> **⚠️ 2026-07 去 SeaTunnel 化后说明**：本文档调查的「SeaTunnel INDEX pipeline（Iceberg→Doris）」链路已于 T1.10 整体删除——Doris 写入改 ObjectIndexFunnel（Python 侧直连），不再走 SeaTunnel。文档主体（红线 #6 要求所有数据流动经 SeaTunnel / sync_now 是当前唯一可用路径等）均为**历史调查记录**。但文档核心价值——「误判根因 → 错误结论沉淀进 ADR/代码注释 → 长期误导」的复盘方法论——仍然有效。当前架构见 [ADR-008](../architecture/adr-008-iceberg-doris-sync-path.md) 顶部横幅。

> 本文档记录 2026-06-25 调通 SeaTunnel Iceberg→Doris 索引同步链路时的一段调查过程，目的是把背景和技术细节固化为工程经验，**避免后续重蹈"误判根因 → 错误结论沉淀进 ADR/代码注释 → 长期误导"的覆辙**。
>
> 关联文档：
> - [ADR-008 Iceberg→Doris 索引同步路径](../architecture/adr-008-iceberg-doris-sync-path.md)
> - [bugfix: SeaTunnel INDEX Pipeline 不可用](../bugfix/seatunnel-index-pipeline-iceberg-doris-unavailable.md)
> - 代码：`src/ontology/layers/pipeline/sea_tunnel_engine.py` (`PIPELINE_INDEX_TEMPLATE`)、`src/ontology/layers/dataset/iceberg_store.py` (`GravitinoRestCatalog`)

---

## 一、问题背景

架构红线 #6 要求 **SeaTunnel 承担 PipelineBuilder**，所有数据流动（含 Iceberg→Doris 索引同步）都应经 SeaTunnel 声明式管道。但端到端落地时，SeaTunnel 的 `INDEX` pipeline（Iceberg source → Doris sink）一直跑不通，被迫用 `IndexSyncService.sync_now`（Trino 读 Iceberg + pymysql 写 Doris）绕路。

当时（ADR-008、`index_sync_service.py` 注释）记录的根因是：

> "SeaTunnel 2.3.13 的 Iceberg source 连接器**无法读取** REST Catalog 写出的 Iceberg 表"
> "REST 支持在 PR #9654 引入，未进入 2.3.13 release"
> "Iceberg source + Doris sink 组合会让 worker 进程 crash"
>
> **2026-07-06 再次证伪**：这条里的 "STREAMING+incremental 会 crash" 判断也被实测推翻。当前环境下 STREAMING 增量稳定可用，详见 ADR-008 末尾「修订记录（2026-07-06）」。本文档保留下方原始调查过程作为历史记录，但「crash」相关结论已作废。

由此得出结论：SeaTunnel 2.3.13 客观上不可用，`sync_now` 是当前唯一可用路径，流式 INDEX pipeline 暂缓到 SeaTunnel 升级。

**这个判断是错的。** 2026-06-25 实测证伪了它。

---

## 二、为什么当初会误判

三个因素叠加，让一个**配置层**问题被误诊为**版本兼容性**问题：

| 因素 | 表现 | 误导点 |
|------|------|--------|
| SeaTunnel 报错信息模糊 | `ErrorCode:[API-06] Factory initialize failed - Unable to create a source for identifier 'Iceberg'` | API-06 字面像"连接器不支持"，实则是"连接器初始化时内部抛异常" |
| SeaTunnel IcebergCatalogType 枚举只有 `HADOOP`/`HIVE` | 用 `type = "rest"` 配置会被拒 | 误以为"SeaTunnel 2.3.13 根本不认 REST Catalog" |
| worker crash 现象真实存在 | STREAMING + incremental 组合下 worker 抛 EventService NPE 后重启 | 把"streaming 模式 crash"和"REST catalog 不支持"混为一谈。**（2026-07-06 证伪：当前环境无法复现此 crash，见 ADR-008 修订记录）** |

**教训**：API-06 是 SeaTunnel 的通用"连接器初始化失败"错误码，**不等于**"连接器不支持某能力"。要看 master 日志里 `Caused by:` 真实异常栈，不能止步于 SeaTunnel 返回的 fail message。

---

## 三、实测证伪过程

### 3.1 RESTCatalog 类到底在不在 2.3.13

反编译 `connector-iceberg-2.3.13.jar`：

```
$ unzip -l connector-iceberg-2.3.13.jar | grep -E "rest/RESTCatalog\.class$|CatalogUtil\.class$"
    15249  org/apache/iceberg/rest/RESTCatalog.class       ← 存在
    21748  org/apache/iceberg/CatalogUtil.class            ← 存在
```

`RESTCatalog.class` **确实存在**于 2.3.13。原判断"PR #9654 未进 2.3.13"错误——PR #9654 改的是 SeaTunnel 侧的 `IcebergCatalogType` 枚举暴露，而 iceberg 原生的 `RESTCatalog` 实现类本就在依赖里。

### 3.2 catalog-impl 透传链路

反编译 SeaTunnel 的 `IcebergCatalogLoader.loadCatalog`，关键调用：

```java
// IcebergCatalogLoader.java
CatalogUtil.buildIcebergCatalog(catalogName, catalogProps, hadoopConf)
```

`catalogProps` 来自配置里的 `iceberg.catalog.config` Map，**原样透传**给 iceberg 原生的 `CatalogUtil.buildIcebergCatalog`。后者读 `catalog-impl` key 来决定加载哪个 catalog 实现——这是 iceberg 规范的标准机制，**不经** SeaTunnel 的 `IcebergCatalogType` 枚举校验。

所以：
- `type = "rest"` → SeaTunnel 枚举只认 hadoop/hive → ❌ 被拒
- `catalog-impl = "org.apache.iceberg.rest.RESTCatalog"` → 透传给 iceberg → ✅ 加载 RESTCatalog

### 3.3 真实卡点：Gravitino REST server 的 `/v1/config?warehouse=...` 返回 404

SeaTunnel 提交带 `warehouse` 的 Iceberg job，master 日志真实异常栈：

```
FactoryException: API-06 ... Unable to create a source for identifier 'Iceberg'.
Caused by: org.apache.iceberg.exceptions.RESTException:
  Unable to process: Couldn't find Iceberg configuration for catalog s3://ontology-warehouse/
    at RESTSessionCatalog.fetchConfig(RESTSessionCatalog.java:980)
    at RESTSessionCatalog.initialize(RESTSessionCatalog.java:223)
    at RESTCatalog.initialize(RESTCatalog.java:78)
    at CatalogUtil.loadCatalog(CatalogUtil.java:256)
    at CatalogUtil.buildIcebergCatalog(CatalogUtil.java:310)
    at IcebergCatalogLoader.loadCatalog(IcebergCatalogLoader.java:61)
```

RESTCatalog **被成功加载并初始化了**，失败在 `fetchConfig`。直接 curl Gravitino REST server 复现：

```bash
$ curl -o /dev/null -w "%{http_code}" "http://localhost:9001/iceberg/v1/config"          # 200
$ curl -o /dev/null -w "%{http_code}" "http://localhost:9001/iceberg/v1/config?warehouse=s3://ontology-warehouse"  # 404
```

带 `warehouse` 返回 404 `NoSuchCatalogException`，栈指向 Gravitino 的 `IcebergCatalogWrapperManager.createCatalogWrapper`——**它把 warehouse 串当 catalog 名去缓存里查**，而缓存里只有 `catalog-backend-name = ontology` 这一个 catalog，找不到 `s3://ontology-warehouse`。（对应 Gravitino Issue #10486。）

> 补充：pyiceberg 后端的 `GravitinoRestCatalog._fetch_config` 注释里写的是 401（s3-token credential provider 场景）。同一端点带 warehouse 时，**带 s3-token 凭证分发返回 401，不带凭证返回 404**——都是 Gravitino REST server 对 `warehouse` 参数处理有缺陷，表现因凭证链路不同而异。文档/注释要据实测场景如实记录，不能一概而论。

### 3.4 为什么 pyiceberg 能用、SeaTunnel 不能

| 客户端 | 是否调 `/v1/config` | 结果 |
|--------|---------------------|------|
| pyiceberg（`GravitinoRestCatalog`） | **跳过**（override `_fetch_config`，直接设空 defaults） | ✅ 可用 |
| SeaTunnel（原生 Java `RESTCatalog`） | **无条件调**（`fetchConfig` 在 `initialize` 里硬编码，带 warehouse） | ❌ 撞 404 |

`IcebergStore.GravitinoRestCatalog` 专门 override 了 `_fetch_config` 和 `_create_session`（去掉 `X-Iceberg-Access-Delegation` 头和 Bearer auth）来绕过 Gravitino 的这些缺陷。SeaTunnel 用的是 iceberg 原生 Java 实现，没法 override，只能从配置层规避。

### 3.5 解法：配置层去掉 `warehouse`

去掉 `warehouse` 后，Gravitino `/v1/config` 返回 200（空 defaults），RESTCatalog 初始化通过，整条链路打通。实测：

```
去掉 warehouse 提交 Iceberg→Console job  → FINISHED
去掉 warehouse 提交 Iceberg→Doris job     → FINISHED，idx_airline__aircraft 写入 500 行
7/7 OT 的 INDEX pipeline 全部 FINISHED，benchmark 03_wait_sync 首轮 poll 即收敛
```

---

## 四、最终修复（`PIPELINE_INDEX_TEMPLATE`）

```hocon
source {
  Iceberg {
    catalog_name = "ontology"
    namespace = "ontology"
    table = "{{ source_table }}"          # ① 小写
    case_sensitive = true
    iceberg.catalog.config = {
      catalog-impl = "org.apache.iceberg.rest.RESTCatalog"   # ② 不用 type="rest"
      uri = "{{ iceberg_rest_uri }}"
      # ③ 不带 warehouse —— 规避 Gravitino /v1/config 404
      "s3.endpoint" = "..."
      "s3.region" = "..."
      "s3.access-key-id" = "..."
      "s3.secret-access-key" = "..."
    }
  }
}
```

三处关键改动：

| # | 改动 | 原因 |
|---|------|------|
| ① | `table` 传小写（`object_type_api_name.lower()`） | Iceberg 表名由 SYNC sink 强制小写写入，REST catalog 查表大小写敏感，驼峰 OT（如 `airportStand`）会 `NoSuchTableException` |
| ② | `catalog-impl = "RESTCatalog"` 取代 `type = "rest"` | SeaTunnel `IcebergCatalogType` 枚举只认 hadoop/hive；`catalog-impl` 透传给 iceberg `CatalogUtil` 才能加载 REST |
| ③ | `iceberg.catalog.config` 不带 `warehouse` | Gravitino 1.2.0 REST server 把 warehouse 串当 catalog 名查 → 404；不带 warehouse 时返回 200 空 defaults |
| ④ | `job.mode = "BATCH"` + 去掉 `incremental=true` | 原判断：STREAMING+incremental 组合触发 worker EventService NPE crash。**此判断已于 2026-07-06 被实测证伪**（STREAMING+`stream_scan_strategy` 稳定 RUNNING 不 crash，增量同步正常）。当时选 BATCH 仍合理：全量快照是 benchmark / 首次 backfill 所需；但「为规避 crash 而选 BATCH」这一理由不成立。详见 ADR-008「模式选择评估」 |

---

## 五、工程教训（防重蹈覆辙）

### 教训 1：API-06 ≠ 不支持，要看真实异常栈

SeaTunnel 的 `Factory initialize failed (API-06)` 是"连接器初始化阶段抛了任何异常"的通用码。**永远要追到 master 日志的 `Caused by:`**，不能凭 fail message 字面意思下"版本不支持"的结论。

### 教训 2：判断"某版本是否支持某能力"要验证依赖内部，不止看入口枚举

`IcebergCatalogType` 只有 HADOOP/HIVE，是 SeaTunnel **自己**的配置入口枚举；但底层 `iceberg-core` 的 `RESTCatalog` 类、`CatalogUtil.buildIcebergCatalog` 透传机制都在。入口没暴露 ≠ 底层不支持。判断版本能力时：
- 反编译 jar 看类是否存在（`unzip -l | grep`）
- 看加载链路是否有透传 escape hatch（如 `catalog-impl`）

### 教训 3：跨组件互操作问题，要分别隔离复现两端

本例是 SeaTunnel（客户端）+ Gravitino（服务端）+ iceberg（协议）三方。直接看 SeaTunnel 日志只能拿到 `RESTException`，curl Gravitino 端点才暴露 404 的真实成因（warehouse 当 catalog 名查）。**每个组件单独最小复现**，才能定位责任方。

### 教训 4：不同客户端走同一协议，行为差异要在代码里显式记录

pyiceberg 和 SeaTunnel 都用 Iceberg REST 协议，但 pyiceberg 能 override `_fetch_config` 跳过缺陷端点，SeaTunnel 不能。`GravitinoRestCatalog` 的 override 注释里**如实记录了 401 现象**，但当时没意识到同一端点还有 404 表现——导致 SeaTunnel 侧的 ADR-008 写了错误的根因。**协议层缺陷要记录"所有客户端表现"，不能只记自己遇到的那一种。**

### 教训 5：错误根因一旦沉淀进 ADR/注释，会长期误导

ADR-008 是"已采纳"决策文档，`index_sync_service.py` 的 docstring 把错误根因当事实写进去后，后续开发者（包括 AI）会默认采信，不再质疑。**ADR 的根因判断要标注"基于当时认知"，并保留修订记录**；发现证伪时必须追加修订（本次已在 ADR-008 末尾加"修订记录（2026-06-25）"）。

### 教训 6：配置绕路优先于版本升级

原 ADR-008 把"升级 SeaTunnel"列为回归条件，但实测发现**改三行配置**就能打通，根本不需要升级。遇到"版本不支持 X"的判断时，先穷举配置层的 escape hatch（`catalog-impl`、去掉 `warehouse`、换 BATCH 模式），再考虑升级。

### 教训 7：SeaTunnel connector 字段名跨版本变动，文档初版不可全信（ADR-014 多源融合补充）

2026-07 多源融合实现时发现：SeaTunnel 2.3.13 各 connector 的配置字段名与文档初版/设计文档推测多处不符，**必须 live dry-run 验证**（提交配置指向假主机，看 dialect/factory/字段识别，不能只靠文档）：

| connector | 文档初版（错） | 实测（对） |
|-----------|--------------|----------|
| MySQL-CDC source | `hostname`/`port`/`database-name`/`table-name` | `base-url`（完整 JDBC URL）+ `table-names`（复数 list） |
| S3File source | `endpoint` | `fs.s3a.endpoint` + `hadoop_s3_properties { fs.s3a.path.style.access=true }`（RustFS/MinIO 必需） |
| Kafka source | `pattern`（数据格式） | `format`（`pattern` 是 topic 正则匹配，语义不同） |
| Iceberg sink | `type = "rest"` | `catalog-impl = org.apache.iceberg.rest.RESTCatalog`（本文档教训 2） |

字段名以 **jar 反编译 + 实测报错**为准。详见 [cdc-spike-report.md](cdc-spike-report.md)、[starrocks-seatunnel-dryrun.md](starrocks-seatunnel-dryrun.md)。

### 教训 8：国产库 JDBC 驱动同名类冲突（ADR-014 D4）

openGauss/GaussDB 旧驱动 `gsjdbc4.jar` 内含完整 `org.postgresql.Driver.class`，与官方 `postgresql-42.x.jar` 同名注册，SeaTunnel `AbstractJdbcCatalog`（PR #8986 只对异名类生效）无法区分，回退 `DriverManager` 顺序加载，国产库先加载获胜但 SCRAM/SHA-256 与标准 PG 不兼容，报 `Protocol error. Session setup failed`。

解法：用**独立类名驱动包**（`opengaussjdbc` `com.huawei.opengauss.jdbc.Driver` / `kingbase8` `com.kingbase8.Driver` / `oceanbase` `com.oceanbase.jdbc.Driver`），不含同名 `org.postgresql.Driver.class`。`infra/seatunnel-entrypoint.sh`（飞线 1）仍需保留——park 镜像自带的冲突社区版 `opengauss-jdbc-5.1.0.jar`；新放的华为官方版 `opengaussjdbc-506`（文件名无连字符）不匹配 entrypoint 的 glob，可共存。

---

## 六、快速自查清单

下次遇到 SeaTunnel Iceberg source 报 API-06 时，按此顺序排查：

- [ ] master 日志 `Caused by:` 是什么？（不要止步于 fail message）
- [ ] 如果是 `RESTException: Couldn't find Iceberg configuration for catalog ...` → Gravitino `/v1/config?warehouse=...` 的 404，**去掉 `warehouse`**
- [ ] 如果是 `Factory initialize failed` 且用了 `type = "rest"` → 改用 `catalog-impl = "org.apache.iceberg.rest.RESTCatalog"`
- [ ] 表名是否小写？（驼峰会 `NoSuchTableException`）
- [ ] STREAMING 模式是否 worker crash？→ **2026-07-06 实测：不 crash**（原判断已作废，见 ADR-008 修订记录）。当前 BATCH 仍保留用于首次 backfill / 容灾补数
- [ ] CDC source 报 `API-02 url required`？→ 字段名错了，用 `base-url`+`table-names`（教训 7）
- [ ] S3File 报 `UnknownHostException: <bucket>.<host>`？→ 缺 `fs.s3a.path.style.access=true`（教训 7）
- [ ] 国产库报 `Protocol error. Session setup failed`？→ 驱动同名冲突，换独立类名驱动（教训 8）
- [ ] curl 验证 Gravitino REST server 两端点：不带 warehouse 应 200，带 warehouse 看是否 404/401

---

## 七、相关代码与文档索引

| 位置 | 内容 |
|------|------|
| `src/ontology/layers/pipeline/sea_tunnel_engine.py` | `PIPELINE_INDEX_TEMPLATE`（修复后的模板）+ 模板上方注释（完整背景） |
| `src/ontology/layers/dataset/iceberg_store.py` | `GravitinoRestCatalog`（pyiceberg 侧的 `_fetch_config` override） |
| `src/ontology/services/index_sync_service.py` | `sync_now`（降级为容灾路径）的 docstring |
| `docs/architecture/adr-008-iceberg-doris-sync-path.md` | 原决策 + 2026-06-25 修订记录（证伪过程） |
| `docs/bugfix/seatunnel-index-pipeline-iceberg-doris-unavailable.md` | bugfix 记录 + 方案 0（根洽） |
