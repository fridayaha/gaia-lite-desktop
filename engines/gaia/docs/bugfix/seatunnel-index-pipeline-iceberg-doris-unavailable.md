# 遗留问题：SeaTunnel INDEX Pipeline (Iceberg → Doris) 不可用

> **⚠️ 2026-07 去 SeaTunnel 化后状态：整个 INDEX pipeline 方案已废弃。** 本文档记录的「SeaTunnel INDEX pipeline（Iceberg→Doris）」于 2026-07 T1.10 整体删除——Doris 写入统一收口到 `ObjectIndexFunnel`（从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`，Python 侧直连），不再走 SeaTunnel。下方「已根洽 / 架构目标态」等描述均为**历史记录**，不代表当前架构。当前架构见 [ADR-008](../architecture/adr-008-iceberg-doris-sync-path.md) 顶部横幅 + [graph-reasoning-design.md](../architecture/graph-reasoning-design.md) §6。本文档保留作事故复盘与溯源。

**记录时间**: 2026-06-25  
**影响版本**: SeaTunnel 2.3.13 (Zeta engine)  
**严重程度**: 中等 — 非阻塞，RUNBOOK 已有降级方案

> **2026-06-25 更新：已根洽。** 原根因判断（“SeaTunnel 2.3.13 不支持 REST Catalog / Iceberg source+Doris sink 组合 crash”）被实测证伪。真实卡点是 **Gravitino 1.2.0 独立 Iceberg REST server 的 `GET /v1/config?warehouse=...` 返回 404**（把 warehouse 串当 catalog 名查，Issue #10486），而 SeaTunnel 原生 Java `RESTCatalog` 初始化时无条件带 warehouse 调 `fetchConfig` 撞上这个 404。修复 = INDEX pipeline 模板里 **去掉 `warehouse`，改用 `catalog-impl = org.apache.iceberg.rest.RESTCatalog`**（不用被 SeaTunnel 枚举拒绝的 `type=rest`），并改为 BATCH 全量。实测 7/7 表全部由 SeaTunnel 直接写入 Doris。见下方“根因（2026-06-25 修订）”。
>
> **2026-07-06 二次修订**：上述“改为 BATCH 全量（避免 STREAMING+incremental 的 worker crash）”中的 crash 判断**再次被实测证伪**。当前环境下 STREAMING + `stream_scan_strategy = FROM_LATEST_SNAPSHOT`（带或不带 `incremental=true`）均稳定 RUNNING 不 crash，增量同步正常工作（单行 10s 内、连续 5 行 burst 全部同步）。保留 BATCH 是因为它适合首次 backfill / 容灾补数，而非“为规避 crash”。详见 [ADR-008 修订记录（2026-07-06）](../architecture/adr-008-iceberg-doris-sync-path.md)。

---

## 现象

SeaTunnel `INDEX` pipeline（Iceberg source → Doris sink）提交后执行阶段失败，
导致 SeaTunnel worker 进程崩溃，Doris 索引表始终为空。

具体表现：
- job 能提交到 Hazelcast cluster（返回 jobId，状态 RUNNING）
- task 分配到 worker 后执行失败
- worker 弹出 `EventService NPE: Target cannot be null` → 整个 worker 进程 crash
- worker 自动重启后，旧 slot 残留导致新 job 拿到零资源 slot (`resourceProfile{core=0, heapMemory=0}`)
- 重启 master+worker 双节点可清 slot 状态，但重新提交 job 仍重复上述流程

## 调查过程

1. 手动提交最小 MySQL→Console job 能成功（排除 SeaTunnel 整体不可用）
2. 手动提交 MySQL→Iceberg (SYNC) job 能成功（排除 Iceberg 源不可用）
3. 手动提交 Iceberg→Doris (INDEX) job 提交成功但执行失败
4. 搜索 SeaTunnel 社区：相关修复（WrongTargetSlotException #6135 / slot 资源泄漏 #6763）已在 2.3.4/2.3.5 合入，
   但 Iceberg source + Doris sink 组合在 2.3.13 上没有公开的可用性修复
5. finished-jobs NPE (#10700) 在 2.3.14 修复，当前 2.3.13 不包含

## 根因（2026-06-25 修订）

**原始判断（已证伪）**：认为 SeaTunnel 2.3.13 的 Iceberg source 连接器不支持 REST Catalog、且 Iceberg+Doris 组合会让 worker crash。

**实测真相**：
1. `connector-iceberg-2.3.13.jar` 里 **包含** `org/apache/iceberg/rest/RESTCatalog.class`，能被 iceberg 原生 `CatalogUtil.buildIcebergCatalog` 加载并初始化。
2. SeaTunnel 的 `IcebergCatalogLoader.loadCatalog` 会把 `iceberg.catalog.config`（含 `catalog-impl`）原样透传给 `CatalogUtil.buildIcebergCatalog`，所以 `catalog-impl = org.apache.iceberg.rest.RESTCatalog` 是可用的。
3. 真实失败在 `RESTSessionCatalog.fetchConfig`：`GET /v1/config?warehouse=s3://ontology-warehouse` → Gravitino 返回 404 `NoSuchCatalogException`（`IcebergCatalogWrapperManager.createCatalogWrapper` 把 warehouse 串当 catalog 名查，缓存里只有 `ontology`）。
4. pyiceberg 后端能用，是因为 `IcebergStore.GravitinoRestCatalog` 专门 override 了 `_fetch_config` 跳过这个请求；SeaTunnel 的 Java 实现没法跳过。
5. STREAMING + `incremental=true` 的 worker crash 是独立问题（EventService NPE），改为 BATCH 全量即可避开；全量快照正是 benchmark / 首次 backfill 所需。**（2026-07-06 证伪：当前环境无法复现此 crash，STREAMING 增量实测可用，见 ADR-008 修订记录）**

**关键证据**：
- `GET /iceberg/v1/config`（不带 warehouse）→ 200
- `GET /iceberg/v1/config?warehouse=s3://ontology-warehouse` → 404 NoSuchCatalogException
- 去掉 warehouse 提交 Iceberg→Console job → FINISHED
- 去掉 warehouse 提交 Iceberg→Doris job → FINISHED，idx_airline__aircraft 写入 500 行

## 根因（原记录，已过时）

SeaTunnel 2.3.13 Zeta engine 上 Iceberg source + Doris sink 组合存在内部兼容性问题。
worker 执行 task 时抛出的真实异常被 EventService 的 NPE 掩盖，
无法从日志中获取具体底层错误。猜测与 Iceberg 和 Doris 之间的类型转换、
字段映射、或 REST catalog 初始化有关。

## 解决方案

### 方案 0：修正 INDEX pipeline 模板（当前已采用，根洽）
修该 `PIPELINE_INDEX_TEMPLATE`：
- `iceberg.catalog.config` 里去掉 `warehouse`，`type="rest"` 改为 `catalog-impl = "org.apache.iceberg.rest.RESTCatalog"`
- `job.mode` 改 `BATCH`、去掉 `incremental=true`（原判断为避免 STREAMING+incremental 的 worker crash；**2026-07-06 证伪此 crash 不复现**，保留 BATCH 是因全量快照适合首次 backfill）
- `source_table` 传小写（Iceberg 表名小写，驼峰会 NoSuchTableException）

实测 7/7 OT 的 INDEX pipeline 全部 FINISHED，Doris idx 表全部填充。
**这是架构目标态**：SeaTunnel 直接 Iceberg→Doris，不经 Trino，符合红线 #6。

### 方案 A：绕过 SeaTunnel，用 Python 脚本直接同步 (历史降级方案)
从 Trino 读 Iceberg 表，通过 `pymysql` bulk INSERT 写入 Doris。
已验证 7 张表全部同步成功（500 行 × 7 表 = 行数全部匹配）。

命令参考：
```bash
.venv/bin/python /tmp/sync_doris.py   # 或等价的脚本
```

**优点**: 立即可用，不依赖 SeaTunnel 升级  
**缺点**: 不是声明式的数据管道，无增量/CDC 支持

### 方案 B：升级 SeaTunnel 到 2.3.14-dev 或更新发布版
待 SeaTunnel 社区发布 2.3.14+（包含 finished-jobs NPE 修复 + 可能的 Iceberg/Doris 组合修复），
替换 docker-compose 中的镜像版本重新测试。

**优点**: 根洽，获得完整 INDEX pipeline 能力  
**缺点**: 需要等待社区发布 + 兼容性验证

### 方案 C：降级处理 (RUNBOOK 已有)
RUNBOOK 步骤 6 注释："若 Doris 同步始终不成功，可跳过此步，
后续 read benchmark 会走 Trino 降级"。

读 benchmark 的 Tier 1 基线用例中，单对象点查和 L2 聚合走 Trino 查询 Iceberg，
不依赖 Doris 索引加速。只有性能压测中 Tax% 指标会受影响（无 Doris 加速 → P95 偏大）。

**优点**: 最简单  
**缺点**: 性能指标失真（无 Doris 索引加速）

---

## 受影响的组件

- `src/ontology/services/index_sync_service.py` — provision 时调 `create_index_pipeline` → 失败会被 `IndexProvisionError` 捕获
- `src/ontology/layers/index/doris_index_store.py` — 建空表逻辑正常，但 INDEX pipeline 无法填充数据
- `src/ontology/services/ontology_service.py` — `_provision_index` 回退路径只建空表，不启 pipeline

## 临时规避

### 状态同步修复 (已实施)
以下修复已合入，使 SeaTunnel 的 finished-jobs NPE 不再导致状态僵死：
- `src/ontology/layers/pipeline/sea_tunnel_engine.py` — `get_job_status` 检测 finished-jobs 500 body `{"status":"fail"}` 降级为 UNKNOWN
- `src/ontology/services/datasource_service.py` — `start_sync` 提交后查 SeaTunnel 真实状态而非盲目标 RUNNING

### Doris 数据填充脚本 (已实施)
见 `/tmp/sync_doris.py`（临时脚本，7 张表同步已验证）

---

## 后续行动

| 优先级 | 行动 | 预期版本 |
|--------|------|----------|
| P2 | 关注 SeaTunnel 2.3.14 发布，验证 Iceberg→Doris 修复 | TBD |
| P2 | 如升级后仍失败，向 SeaTunnel 社区提 Issue | — |
| P3 | 完善 INDEX pipeline 的 Doris 表结构生成逻辑（字段对齐 Iceberg schema） | — |
| P3 | 如长期不可用，考虑用 Trino INSERT INTO Doris jdbc 写入替代 SeaTunnel INDEX | — |
