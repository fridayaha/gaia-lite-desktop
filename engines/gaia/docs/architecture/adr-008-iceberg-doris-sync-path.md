# ADR-008：Iceberg→Doris 索引同步路径（sync_now 取代 SeaTunnel 流式）

> **⚠️ 当前状态（2026-07 去 SeaTunnel 化后，请先读此条）**：本 ADR 主体（背景/决策/红线合规/回归条件/模式选择评估/第二次修订）描述的「SeaTunnel INDEX pipeline 承担 Iceberg→Doris 同步」方案**已整体废弃**，不再反映当前架构。当前真实架构：
> - **Doris 写入不走 SeaTunnel**。外部接入数据由 `ObjectIndexFunnel` 从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`（统一 rid 分配/复用 + 四引擎扇出，见 [graph-reasoning-design.md](./graph-reasoning-design.md) §6 + [handoff-rid-funnel-closure.md](./handoff-rid-funnel-closure.md)）；Action 业务写入由 outbox `INDEX` effect → `OutboxExecutor` ≤1s → `DorisIndexStore.upsert`。
> - `IndexSyncService` 现仅负责 Doris 索引表 DDL（provision/rebuild/deprovision），不再提交任何 SeaTunnel pipeline。`create_index_pipeline` / `stop_index_pipelines` / `PIPELINE_INDEX_BACKFILL_TEMPLATE` / `PIPELINE_INDEX_STREAM_TEMPLATE` 均已删除（T1.10）。
> - `sync_now` 保留为容灾兑底（Trino 读 + Doris upsert）。
> - SeaTunnel 现只承担「外部源 → Iceberg」搬运（MAIN pipeline + FILE_SYNC/KAFKA_*/EXTERNAL_CDC），详见 [architecture_overview.md](./architecture_overview.md) §5.6。
>
> 下方主体内容保留作为**历史决策记录**（含三次修订演进轨迹），不代表当前实现。当前实现以本横幅 + 末尾「修订记录（2026-07-08，第三次）」+「遗留」段 + [implementation-status.md](./implementation-status.md) 为准。

| 字段     | 内容 |
| -------- | ---- |
| **状态** | 已采纳，后于 2026-07 去 SeaTunnel 化整体废弃（见顶部横幅 + 末尾第三次修订）。主体保留作历史记录 |
| **审批日期** | 2026-06-19 |
| **影响层** | `services/IndexSyncService`、`layers/dataset/IcebergStore`、`layers/pipeline/SeaTunnelEngine` |
| **相关 ICD** | ICD-04 DorisIndexStore、ICD-03 IcebergStore |
| **相关红线** | #4 Doris 严格作为索引加速层；#6 SeaTunnel 承担 PipelineBuilder（已收窄为「外部源→Iceberg 搬运」） |

---

## 背景

架构红线 #4 要求 Doris 仅存主键 + 索引列 + 热点属性，由 Iceberg 同步而来。原设计
（见 `architecture_overview.md` §5.6、`index-acceleration-design.md`）由 SeaTunnel 承担
这条 `INDEX_SYNC` 流水线（Iceberg 增量 → Doris，~30s 延迟）。

落地时 SeaTunnel 2.3.13 的 Iceberg source 连接器**无法读取 Gaia 写出的 Iceberg 表**，
两条通道均失败：

| 通道 | 失败点 | 根因 |
| ---- | ------ | ---- |
| ① REST Catalog | `iceberg.catalog.config = { catalog-impl = "org.apache.iceberg.rest.RESTCatalog", ... }` → `Factory initialize failed` | SeaTunnel 2.3.13 的 Iceberg 连接器**不支持 REST Catalog**（REST 支持在 PR #9654 引入，未进入 2.3.13 release） |
| ② Hadoop Catalog (S3A) | `NoSuchTableException: Table ontology.<t> does not exist` → `version-hint.text` FileNotFoundException | pyiceberg REST Catalog 写出的表**不生成 `version-hint.text`**，而 SeaTunnel 的 Hadoop Catalog 读取依赖该文件——两类 catalog 的元数据布局不互通 |

SeaTunnel 侧的错误均为 `ErrorCode:[API-06] Factory initialize failed - Unable to create a
source for identifier 'Iceberg'`，根因落在上述两类 catalog 互操作缺口，**与连接器 JAR
是否安装无关**（`connector-iceberg-2.3.13.jar` 已在 `/opt/seatunnel/connectors/`）。

此外，SeaTunnel 2.3.13 的 REST submit-job V1 端点
`POST /hazelcast/rest/maps/submit-job` 请求体必须为 **JSON**，原实现以 HOCON 文本直发
返回 `400 Invalid JSON format in request body`；需带 `?format=hocon` 才接受 HOCON。

## 决策

1. **`IndexSyncService.sync_now` 作为当前可用的 Iceberg→Doris 同步路径**：
   通过 `IcebergStore.scan_latest`（Trino 读取 Iceberg 最新快照）取出索引列，投影后
   `DorisIndexStore.upsert` 写入 Doris 索引表。一次调用完成全量同步，无延迟、无 mock。
   `IndexSyncService.provision/rebuild` 仍创建 Doris 索引表 DDL；其 SeaTunnel 流式
   pipeline 提交保持 best-effort（失败仅记日志，不阻断 ObjectType CRUD）。

2. **SeaTunnel 流式 `INDEX_SYNC` pipeline 暂缓**，直到 SeaTunnel 升级到支持 Iceberg
   REST Catalog 的版本（≥ 含 PR #9654 的发行版）。升级后：
   - 将 `PIPELINE_SYNC_TEMPLATE` 的 source 切回 REST Catalog 配置；
   - `sync_now` 降级为「首次 backfill / 容灾补数」角色，流式增量回归 SeaTunnel。

3. **SeaTunnel submit-job 调用修正**：`_submit_job` URL 固定带 `&format=hocon`，使 HOCON
   模板可被 V1 端点接受（此前 400 的根因）。

4. **Doris sink `fenodes` 端口修正**：SeaTunnel Doris sink 的 `fenodes` 期望 FE HTTP
   （stream load）端口 **8030**，非 MySQL 协议端口 9030。新增 `settings.doris_fe_http_port`
   （默认 8030，compose 注入），`PIPELINE_SYNC_TEMPLATE` 与 Kafka→Doris 模板统一改用它。

## 红线合规

- **红线 #4（Doris 严格作为索引加速层）**：`sync_now` 仅投影 `IndexFieldExtractor` 产出的
  索引列 + 主键， STRUCT/ARRAY/ATTACHMENT 等被红线拒绝的类型不会进入 Doris。✅
- **红线 #6（SeaTunnel 承担 PipelineBuilder）**：主数据写入（MAIN pipeline, JDBC→Iceberg）
  仍由 SeaTunnel 承担；本 ADR 仅调整**索引同步**这一内部机制为进程内路径，因 SeaTunnel
  2.3.13 客观不可用。升级 SeaTunnel 后即回归 SeaTunnel。此为受控的临时偏离，已在此 ADR
  记录并设回归条件。⚠️ 受控偏离

## 替代方案（否决）

- **升级 SeaTunnel 到 2.3.14+/dev**：引入 REST Catalog 支持，但需重新验证整个 Zeta 集群
  稳定性与 connector 兼容矩阵，超出当前迭代范围，列为后续回归项。
- **改用 pyiceberg 直接写 Doris**：等同 sync_now，但绕过 Trino 读取；Trino 路径已验证可用
  且与 `load_by_ids`/`scan_as_of` 复用，无需新通道。
- **Iceberg 表强制生成 `version-hint.text`**：需改 pyiceberg 写路径或后置补写，脆弱且
  违背 REST Catalog 规范，否决。

## 回归条件

满足以下任一即可回归 SeaTunnel 流式 INDEX_SYNC：
- SeaTunnel 升级到支持 Iceberg REST Catalog 的版本（PR #9654 合入后的发行版）；
- 或 Iceberg 表改由 SeaTunnel Hadoop-Catalog sink 写入（生成 version-hint.text），使
  Hadoop-Catalog source 可读——但此举与红线 #3（Iceberg 是主数据唯一写入入口）的当前
  pyiceberg/REST 写入链路冲突，需整体评估。

## 验证

`scripts/verify_e2e_full.py` 步骤 D 真实验证：写 Iceberg → A1 数据集关联 →
`provision`（建 Doris 索引表）→ `sync_now`（25 条 upsert）→ Doris 查询 `status=ok`
返回 3 个真实 ID。全链路真实服务、无 mock。

---

## 修订记录（2026-06-25）

本 ADR 的根因判断被实测证伪，方案 0（SeaTunnel INDEX pipeline）已根洽。

### 证伪过程

1. 反编译 `connector-iceberg-2.3.13.jar`：`org/apache/iceberg/rest/RESTCatalog.class` **存在**于 2.3.13。原判断“PR #9654 未进 2.3.13”错误。
2. `IcebergCatalogType` 枚举确实只有 HADOOP/HIVE（这是原判断对的部分），但 `IcebergCatalogLoader` 会把 `iceberg.catalog.config` 原样透传给 iceberg 原生 `CatalogUtil.buildIcebergCatalog`，后者读 `catalog-impl` key——所以 `catalog-impl = org.apache.iceberg.rest.RESTCatalog` 可用，不需要 SeaTunnel 枚举支持 `rest`。
3. 实测提交 Iceberg→Console job：`catalog-impl=RESTCatalog` + `warehouse` → `RESTException: Couldn't find Iceberg configuration for catalog s3://ontology-warehouse/`（**不是** Factory initialize failed 的“不支持”，而是 Gravitino REST server 的 404）。
4. `GET /iceberg/v1/config`（不带 warehouse）→ 200；`GET /iceberg/v1/config?warehouse=...` → 404。Gravitino 1.2.0 的 `IcebergCatalogWrapperManager` 把 warehouse 串当 catalog 名查，缓存里只有 `ontology`。
5. pyiceberg 能用是因为 `IcebergStore.GravitinoRestCatalog` override 了 `_fetch_config` 跳过这个请求；SeaTunnel Java 实现跳不过，但**去掉 warehouse 参数**后 Gravitino 返回 200，全链路通。
6. 去掉 warehouse 提交 Iceberg→Doris job → FINISHED，idx_airline__aircraft 写入 500 行。

### 方案 0（根洽）

修改 `PIPELINE_INDEX_TEMPLATE`（`src/ontology/layers/pipeline/sea_tunnel_engine.py`）：
- `iceberg.catalog.config` 去掉 `warehouse`；`type="rest"` → `catalog-impl = "org.apache.iceberg.rest.RESTCatalog"`
- `job.mode` STREAMING→BATCH、去掉 `incremental=true`（当时判断为规避 STREAMING+incremental 的 worker crash；**此判断已于 2026-07-06 再次实测证伪**，见末尾「修订记录（2026-07-06）」——STREAMING+`stream_scan_strategy` 不但不 crash，且增量同步正常工作。当时选择 BATCH 仍合理：全量快照是 benchmark / 首次 backfill 所需模式）
- `source_table` 传小写（Iceberg 表名小写）

实测 7/7 OT INDEX pipeline 全部 FINISHED，`03_wait_sync` 首轮 poll 即收敛。

### 红线回归

方案 0 使红线 #6（SeaTunnel 承担 PipelineBuilder）从“受控偏离”回归“完全合规”。`sync_now` 从“唯一可用路径”降级为“容灾 / 首次 backfill 补充”。

> **2026-07-06 修订**：原“增量同步（STREAMING）的 worker crash 问题待 SeaTunnel 修复”的表述**被实测证伪**。在当前 SeaTunnel 2.3.13 + 现有 Gravitino/Doris/Iceberg 环境下，STREAMING + `stream_scan_strategy = FROM_LATEST_SNAPSHOT`（带或不带 `incremental=true`）均稳定 RUNNING 不 crash，且 Iceberg 追加写入后 ~checkpoint 周期内 Doris 即出现新行（实测单行 10s 内、连续 5 行 burst 全部同步）。worker 进程测试期间未重启、日志零异常。详见末尾「修订记录（2026-07-06）」。因此 STREAMING 增量模式的启用**不再阻塞于 SeaTunnel 升级**，回归条件已满足；是否切换为 STREAMING 由架构评估决定（见末尾「模式选择评估」）。

---

## 修订记录（2026-07-06）：STREAMING 增量模式实测可用

### 证伪对象

2026-06-25 修订记录里的这条判断：

> "STREAMING + `incremental=true` 的 worker crash 是独立问题（EventService NPE），改为 BATCH 全量即可避开"

### 实测过程

在当前环境（SeaTunnel 2.3.13 + Gravitino 1.3.0 + Doris 4.0.5 + Iceberg 1.11，worker 已连续运行 11h+）下，对 `ontology.lead_source` 表（30 行，对应 Doris 索引表 `idx_marketing__lead_source`）提交三种 Iceberg→Doris job：

| # | job 配置 | 结果 |
|---|---------|------|
| 1 | `job.mode=BATCH`（基线） | ✅ FINISHED，无 error |
| 2 | `job.mode=STREAMING` + `stream_scan_strategy=FROM_LATEST_SNAPSHOT` | ✅ 稳定 RUNNING 90s+，无 crash；Iceberg 追加 1 行 → 10s 内 Doris 出现该行 |
| 3 | `job.mode=STREAMING` + `FROM_LATEST_SNAPSHOT` + `incremental=true` | ✅ 稳定 RUNNING 90s+，**无 NPE/crash**；增量同步成功（Iceberg 追加 1 行 → Doris 出现） |
| 4 | 配置 2 + 连续 burst 插入 5 行 | ✅ 5/5 全部同步到 Doris；worker 内存稳定（488→492MB） |

测试期间：
- worker 进程（PID 10）从未重启，RSS 稳定在 ~490MB
- worker/master 日志**零** `error`/`exception`/`NPE`/`EventService` 异常（master 仅有测试中故意提交的错误 HOCON 解析失败 + 容器时钟回拨的 checkpoint WARN，均无害）
- 数据一致性：Iceberg 行数与 Doris 行数始终一致

### 证伪结论

**STREAMING + incremental 不会 crash**。2026-06-25 记录的 EventService NPE crash 现象在当前环境无法复现——可能是当时 worker 的 slot 资源泄漏残留（见 `bugfix/seatunnel-index-pipeline-iceberg-doris-unavailable.md` 描述的 `WrongTargetSlotException` / 零资源 slot 问题）所致，而非 STREAMING+incremental 组合本身的固有缺陷。随着 worker 重启清 slot + 配置修正（去 warehouse），该问题已不存在。

### 对回归条件的影响

原回归条件「SeaTunnel 升级到支持 Iceberg REST Catalog 的版本」**早已满足**（2.3.13 即可用，见 2026-06-25 修订）。本次进一步确认：STREAMING 增量模式也**不再阻塞于 SeaTunnel 升级**。是否切换为 STREAMING 是架构选择，不是技术阻塞。

---

## 模式选择评估：BATCH vs STREAMING

STREAMING 增量已实测可用后，需评估当前 INDEX pipeline 应采用哪种模式。以下从架构红线、数据流特征、运维成本三方面评估。

### 评估维度

| 维度 | BATCH 全量重灌（当前） | STREAMING 增量 |
|------|----------------------|---------------|
| **延迟** | 高：每次重灌全表，provision/rebuild 后才同步；两次同步间数据滞后 | 低：~checkpoint 周期（实测 10s 级）近实时 |
| **资源开销** | 每次全表扫描 + 全量 upsert，O(N) 重复劳动；大表（Flight/Booking）每次重灌代价大 | 只读增量 snapshot 的 append/delete files，O(Δ) |
| **SeaTunnel job 生命周期** | 一次性：FINISHED 后退出，无常驻资源 | 长驻：RUNNING 状态占用 worker slot + 内存 |
| **故障恢复** | job 失败重提即可，无状态 | 需 checkpoint 恢复；checkpoint 失败/时钟回拨会告警（实测已见 WARN，不影响数据） |
| **与红线 #4 的契合** | ✅ Doris 严格索引层：每次重灌投影索引列，schema 漂移由 IndexFieldExtractor 把关 | ✅ 同样投影索引列；但 schema 变更（加索引列）需停旧 job+建新 job |
| **ObjectType 变更处理** | rebuild = drop+建表+全量重灌，天然幂等 | rebuild 需 stop 旧 streaming job + 建新表 + 启新 streaming job，多一步 |
| **首次 backfill** | 天然支持（首次就是全量） | 需 `TABLE_SCAN_THEN_INCREMENTAL` 策略先全量再转增量，或配合 sync_now |
| **大表可行性** | ❌ 大表全量重灌不可行（已踩坑：Doris BE OOM，见 `bugfix/seatunnel-worker-oom-and-doris-be-mem-limit.md`） | ✅ 增量只处理 Δ，大表友好 |
| **运维复杂度** | 低：job 即起即灭 | 中：需监控长驻 job 健康、checkpoint、worker 资源 |

### 结论：STREAMING 更合适，BATCH 作为补充

**推荐主路径采用 STREAMING 增量**，BATCH 降级为两个补充角色：

1. **首次 backfill**：ObjectType 新建 provision 后，先跑一次 BATCH 全量灌满 Doris 索引表，再切 STREAMING 跟增量。理由：STREAMING 的 `FROM_LATEST_SNAPSHOT` 只跟新快照，不补历史；`TABLE_SCAN_THEN_INCREMENTAL` 虽可一次搞定但首扫同样有全量开销和 OOM 风险，拆成独立的 BATCH backfill 更可控（可限流、可重试）。

2. **容灾补数**：当 STREAMING job 长时间故障、Doris 数据滞后过多时，用 BATCH（或现有 `sync_now`）做一次全量对齐。`sync_now`（Trino 读 + Doris upsert）保留作为不依赖 SeaTunnel 的最后兜底。

**核心理由**：
- Gaia 的 Doris 是**在线读主源**（红线 #4），读路径对延迟敏感；BATCH 全量重灌的滞后与 Gaia 的"近实时索引"目标冲突
- 大表（Flight/Booking 等）全量重灌已被实测证明会触发 Doris BE OOM，BATCH 作为常态同步路径不可行
- STREAMING 的 O(Δ) 开销符合"1000 Pod 规模"的架构约束（红线相关：不设定期轮询全量备份）

**保留 BATCH 而非全切 STREAMING 的理由**：ObjectType rebuild（schema 变更）时，DROP+CREATE 索引表后必须有一次全量灌数据，STREAMING 无法独立完成这个"从零填满"动作。

### 落地路径（后续工作项）

1. `PIPELINE_INDEX_TEMPLATE` 拆成两个模板：`PIPELINE_INDEX_BACKFILL_TEMPLATE`（BATCH，首次/容灾）+ `PIPELINE_INDEX_STREAM_TEMPLATE`（STREAMING+`FROM_LATEST_SNAPSHOT`，常态增量）
2. `IndexSyncService.provision`：建表 → 提交 backfill job（等 FINISHED）→ 提交 stream job
3. `IndexSyncService.rebuild`：stop stream job → drop+建表 → backfill → 新 stream job
4. `IndexSyncService.deprovision`：stop stream job → drop 表
5. `sync_now` 保留为“不依赖 SeaTunnel 的容灾兜底”（Trino 读 + Doris upsert），文档已如此定位

---

## 修订记录（2026-07-06，第二次）：STREAMING 常驻 → 改回 BATCH 低频批式 + Iceberg maintenance

### 架构转向

本次修订**推翻**上面「模式选择评估」中“推荐主路径采用 STREAMING 增量”的结论。改为：**路径 A（Iceberg→Doris）去掉常驻 STREAMING job，回退到低频 BATCH 批式 + 配套 Iceberg maintenance**。

### 原因：规模化下的常驻 job 爆炸

「模式选择评估」当时只从单表维度看 BATCH vs STREAMING 的延迟/资源权衡，**漏看了规模化维度**——STREAMING 常驻 job 是 per-ObjectType 的，每个 ObjectType 1 个常驻 stream job：

- 1000 个 ObjectType = 1000 个常驻 stream job
- SeaTunnel Zeta 的 `runningJobInfoIMap` 每 job ~200KB，1000 jobs ≈ 400MB（含 backup replica），线性增长（apache/seatunnel#10856）
- dev 环境实测几个 job 就偶发 Hazelcast operation-heartbeat-timeout，规模化下不可行

「模式选择评估」里的“STREAMING O(Δ) 开销符合 1000 Pod 规模”判断是错的——它只算了数据开销，没算**常驻 job 元数据开销**。1000 个常驻 job 的元数据压力远超 Δ 数据处理的节省。

### 新架构：路径 A 与路径 B 分工

路径 A（Iceberg→Doris）和路径 B（Kafka→Doris）重新分工，不再都用 per-type 常驻 job：

| 维度 | 路径 A（Iceberg 中转） | 路径 B（Kafka 中转） |
|------|----------------------|---------------------|
| 中转件 | Iceberg（主数据落盘，列存 parquet） | Kafka（内存消息流，不落盘） |
| 写频率约束 | **低频批式**（Iceberg commit 重，频繁写产生小文件 + snapshot 膨胀） | 高频流式（Kafka 天然支持高频，无文件压力） |
| 延迟 | ~分钟级（批攒 + parquet commit） | 3-5s（流式） |
| 定位 | 主数据持久化 + 历史快照 + 容灾恢复 | 低延迟实时索引（查询加速） |
| job 模式 | **BATCH 一次性**（provision/rebuild 触发） + 周期性 BATCH 调度 | **1 个常驻 multi-table job**（SeaTunnel multi-table 特性） |
| 常驻 job 数 | **0** | **1**（与 ObjectType 数无关，O(1)） |

**关键认知**：
1. 路径 A 的 Iceberg 写不该频繁（小文件 + snapshot 治理负担），所以批式低频是必须的，不是妥协
2. 路径 B 用 Kafka 解耦高频写与 Iceberg 低频写的矛盾——高频变更走 Kafka（无文件成本）低延迟到 Doris，Iceberg 仍按批节奏做主数据持久化
3. 两条路径互补不冗余：路径 A 保证主数据在 Iceberg（红线 #3），路径 B 保证 Doris 索引低延迟可查

### 落地变更

1. `create_index_pipeline`：**去掉 stream job 提交**，只留 backfill（BATCH 一次性）
2. `stop_index_pipelines`/`update_index_pipeline`：去掉 stream 的 stop 逻辑
3. 新增周期性 backfill 调度（lifespan 后台任务，遍历有 Doris 索引表的 ObjectType 周期触发 backfill；Doris MOW 幂等，重复 upsert 不出错）
4. 新增 `IcebergMaintenanceService`：用 Trino 原生 Iceberg connector 的 `ALTER TABLE ... EXECUTE optimize/expire_snapshots/remove_orphan_files` 定期治理小文件 + snapshot（Gravitino REST Catalog 本身不提供 maintenance，但 Gaia 已有的 Trino 原生 iceberg connector 完整支持，已 live 验证）
5. 路径 B 重写 `kafka_to_doris` 为单 job multi-table 动态路由（`table="${target_table}"` + `schema_save_mode=CREATE_SCHEMA_WHEN_NOT_EXIST` + `sink.enable-delete=true`），见 action-loop-design.md

### 对「模式选择评估」的修正

上面「模式选择评估」表中“STREAMING 延迟低/大表友好”的优势**交给路径 B 承担**（Kafka→Doris 3-5s 低延迟）。路径 A 退化为低频批式，不再承担实时索引职责。「落地路径（后续工作项）」中的“拆 backfill + stream 两模板 + 常驻 stream”**作废**，改为“只留 backfill BATCH + 周期调度 + maintenance”。

---

## 修订记录（2026-07-08，第三次）：object_state 同步去 SeaTunnel 化，改 outbox 驱动

### 架构再次转向

本次修订**推翻**第二次修订中「路径 B (Kafka→Doris) 承担 object_state 实时索引」的结论。**object_state 同步链路整体去 SeaTunnel 化**，改为 outbox 驱动：

- **删除**：`ActionSyncService` + `create_pg_to_kafka_pipeline`/`create_kafka_to_doris_pipeline`/`create_dual_sink_pipeline` + PIPELINE_PG_TO_KAFKA/KAFKA_TO_DORIS/DUAL_TEMPLATE + TableSchema* 类 + scripts/verify_action_cdc_live.py
- **新增**：outbox 表的 INDEX/ARCHIVE effect，复用同表按 effect_type 隔离

### 原因

第二次修订虽然解决了路径 A 的 per-type 常驻 job 爆炸（改 BATCH 低频），但路径 B 仍有三个未解决问题：
1. `ensure_cdc_pipelines` 是**孤儿方法无调用方**，kafka_to_doris job 永远不会被启动，路径 B 名存实亡
2. object_state 变更**完全没有落 Iceberg**（create_action_cdc_pipeline 同步的是 action_execution_logs 审计日志，非 object_state），容灾恢复数据丢失
3. object_state 是 Gaia 自管表（同库 PG），用 PG-CDC 逻辑复制槽 + Kafka + SeaTunnel 同步到自己的 Doris，是"杀鸡用牛刀"——三层基础设施只为同步一张表。outbox 模式已是事务安全的 CDC 替代（Action 自己写 outbox，PG-CDC 解决的是"第三方不写 outbox"的场景）

### 新架构：outbox 驱动（替代路径 B + object_state→Iceberg）

```
Action → PG 事务 (object_state + execution_log + outbox[INDEX|ARCHIVE])
                │
        ┌───────┴────────┐
        ▼                ▼
  OutboxExecutor     SyncFlushScheduler
  (INDEX, 1s 轮询)   (ARCHIVE, 5min/1000条 微批)
        │                │
   Doris upsert     IcebergStore.merge
   (近实时 ≤1s)      (Trino MERGE INTO, 最终一致 ≤5min)
```

| 路径 | 旧方案 (已废) | 新方案 (2026-07-08) |
|------|--------------|---------------------|
| object_state → Doris (实时索引) | ~~PG→Kafka→Doris (路径 B, SeaTunnel, 3-5s)~~ | outbox INDEX effect → OutboxExecutor 1s 轮询 → DorisIndexStore upsert/delete (≤1s) |
| object_state → Iceberg (主数据) | ~~SeaTunnel PG-CDC (未接线, 数据丢失)~~ | outbox ARCHIVE effect → SyncFlushScheduler 5min 微批 → IcebergStore.merge (MERGE INTO 按业务 PK) |

### 路径 A 的角色 (不变)

路径 A（Iceberg→Doris backfill）**保留不变**，但职责收窄为服务**外部数据源接入**（ADR-014 本职）：
- 外部数据经 SeaTunnel 写入 Iceberg → 路径 A backfill (BATCH) 同步到 Doris idx 表
- IndexSyncScheduler (周期 backfill 调度) + IcebergMaintenanceService (小文件治理) 作为路径 A 配套
- **object_state 不再走路径 A**（改走 outbox）

### 关键技术点

- **PK 区分**：object_state PK 是 rid (内部 UUID); Doris idx 表 / Iceberg 业务表 PK 是业务主键的 backing_column (如 flight_id)。MERGE INTO 的 ON 条件 + Doris delete_by_ids 都用业务 PK
- **Trino INSERT 不去重**：即使 v2 upsert 表配了 primary-keys + write.upsert.enabled，INSERT 仍追加新行产生重复 PK。必须用 MERGE INTO 实现"按 PK 覆盖"。新增 `IcebergStore.merge` 方法
- **三层物理列对齐**：object_state.properties (JSONB, key=backing_column) / Doris idx 表 (平铺列) / Iceberg 业务表 (平铺列) 列名完全对齐
- **事务安全**：outbox 与 object_state 同 PG 事务原子提交; PG READ COMMITTED 保证 flusher 不会读到"写了一半的 Action"
- **幂等**：Doris Unique MOW INSERT 按业务 PK 幂等覆盖; MERGE INTO 天然幂等; 重试安全

### 容灾恢复

| 故障 | 恢复方式 |
|------|---------|
| Doris 挂了 | outbox 积压在 PG (PENDING), 恢复后 OutboxExecutor 追上; 期间读降级 object_state |
| Doris 数据丢失 | sync_now (Iceberg→Doris upsert, 已有): 业务表已是最新态 (MERGE 覆盖) |
| PG object_state 丢失 | 从 Iceberg 业务表读最新态回填 (业务表 = 最新视图) |
| Iceberg 挂了 | outbox 积压, 恢复后从 outbox 补档 (outbox 持久化在 PG) |

详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) + [action-loop-design.md](./action-loop-design.md) §四.4。

### 遗留

- ~~`create_action_cdc_pipeline` (PG action_execution_logs → Iceberg 审计日志)~~ — **2026-07-10 删除** (无调用方, 审计日志 PG append-only 已足够, 审计归档无合规需求)
- ~~IndexSyncScheduler (路径 A 周期 backfill)~~ — **2026-07-10 删除** (周期轮询全量 OT 不合理; 外部接入数据改方案 A: provision/sync_now 事件驱动触发 Doris 同步)
- ConflictDetector 审计目标从 Iceberg 改为 Doris (2026-07-10): 存在性检测 INDEX outbox 漏写, 删除 placeholder audit_snapshot_diff
