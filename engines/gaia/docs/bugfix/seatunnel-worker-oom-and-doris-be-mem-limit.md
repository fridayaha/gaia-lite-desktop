# 待修复：SeaTunnel worker 并发 OOM + Doris BE 写入内存超限

> **⚠️ 2026-07 去 SeaTunnel 化后说明**：本文档涉及的「INDEX Iceberg→Doris」SeaTunnel pipeline 链路已于 T1.10 删除（Doris 写入改 ObjectIndexFunnel Python 侧直连）。但文档中的并发限流、容器内存与 JVM 堆匹配、Doris BE `mem_limit` 调优等**通用教训仍然有效**（适用任何并发写 Doris 的场景）。事故复盘保留作溯源。

**记录时间**: 2026-06-25
**状态**: 🟡 已定位根因 + 临时规避，待调参
**关联**: benchmark 数据链路（SYNC MySQL→Iceberg、INDEX Iceberg→Doris）、`docs/bugfix/eval-doris-full-data-replace-dataset-lookup.md`

---

## 背景

Doris 全量化实施后重建数据链路时，两个**预先存在的基础设施内存问题**暴露出来，阻塞了全量数据同步。这两个问题与 Doris 全量化改动无关（之前同样存在），只是全量化后同步的数据量从「索引列」变成「全量列」，更容易触发。

---

## 问题 1：SeaTunnel worker 并发 OOM 被 Kill

### 现象

`02_setup_pipeline` 同时启动 7 个 SYNC job（MySQL→Iceberg），全部 FAILED：

```
worker node [localhost]:5802 offline
java.io.IOException: Connection reset by peer
/opt/seatunnel/bin/seatunnel-cluster.sh: line 201: 10 Killed   ← OOM Killer
```

`ps` 显示 worker 进程消失，master 日志 `MemberLeftException`。

### 根因

| 因素 | 值 | 问题 |
|------|----|----|
| docker-compose `mem_limit` | `512m`（`*dev-resources`） | SeaTunnel worker 容器物理内存上限仅 512MB |
| `JAVA_TOOL_OPTIONS` | `-Xms128m -Xmx256m` | JVM 堆 256MB |
| 启动脚本叠加 | `-XX:MaxMetaspaceSize=2g` | metaspace 上限 2g（远超容器 512m） |
| 并发 job 数 | 7（一次启动全部 SYNC） | 7 个 Iceberg sink job 同时跑，堆/metaspace 撑爆 |

`02_setup_pipeline` 在 for 循环里依次 `POST /sync-tasks/{name}/start`，但 start 是 fire-and-forget（不等完成），导致 7 个 job 几乎同时提交。256m 堆 + 7 并发 Iceberg sink → OOM Killer 杀进程。

### 临时规避

串行启动 sync task，每个等 Iceberg 出现行数后再启动下一个：

```bash
for task in sync_aircraft sync_crew ...; do
  curl -X POST ".../sync-tasks/${task}/start"
  # 轮询 Iceberg 行数，非 0 后再启动下一个
  for i in $(seq 1 20); do
    sleep 10
    cnt=$(trino --execute "SELECT count(*) FROM iceberg.ontology.${ot}")
    [ "$cnt" != "0" ] && break
  done
done
```

### 待修复

- [ ] **提升 SeaTunnel worker 内存**：`mem_limit` 从 `512m` 提到 `2g`+，`JAVA_TOOL_OPTIONS` 的 `-Xmx` 提到 `1g`+，`MaxMetaspaceSize` 降到 `512m`（与堆匹配，不能超容器限制）
- [ ] **`02_setup_pipeline` 改串行**：每个 sync task start 后轮询等待完成（或限流并发数 ≤ 2），避免 worker 同时承载 7 个 job
- [ ] **`IndexSyncService.provision` 同理**：define 多个 ObjectType 时会并发建 INDEX pipeline，同样需要限流
- [ ] **SeaTunnel seatunnel-env.sh 显式设堆**：当前 `JAVA_TOOL_OPTIONS` 被 JVM picked up 但脚本可能叠加参数，应在 `seatunnel-env.sh` 显式 `JAVA_OPTS="-Xms512m -Xmx1g"` 确保生效

---

## 问题 2：Doris BE 写入内存超限（booking 50 万行）

### 现象

`IndexSyncService.sync_now` 灌 booking（501481 行）到 Doris，失败：

```
Doris unavailable: (1105, 'errCode = 2, detailMessage = ... MEM_LIMIT_EXCEEDED
Cancel Top Memory task: Memory(Used=0, Limit=921.60 MB, Peak=0).
because process memory used exceed limit. in backend 172.18.0.20,
os physical memory 1.00 GB. process memory used 921.84 MB, limit 921.60 MB.')
```

其余 6 个 OT（最大 1 万行）都成功，只有 booking（50 万行）失败。

### 根因

| 因素 | 值 | 问题 |
|------|----|----|
| docker-compose `mem_limit` | `1g`（`*heavy-resources`） | Doris BE 容器物理内存上限 1GB |
| cgroup `memory.max` | `1073741824`（1GB） | 确认容器内存上限 |
| Doris BE `mem_limit` | 自动推导为 921.60MB（容器内存的 ~90%） | BE 自身内存上限接近容器上限 |
| 写入方式 | `INSERT INTO ... VALUES` 多行（1000 行/批） | Doris Unique model INSERT 在 BE 侧聚合，50 万行累计占内存超限 |

`DorisIndexStore.upsert` 已分批 1000 行/批，但 booking 50 万行 × 多批 INSERT 在 BE 侧累积，BE 进程内存触顶被 cancel。本质是 Doris BE 容器内存（1GB）对 50 万行 INSERT 太小。

### 临时规避

用 Doris stream load（BE 直接读文件，内存友好）替代 INSERT：

```bash
docker exec benchmark-mysql mysql ... -e "SELECT ... FROM booking" --batch --raw > /tmp/booking.tsv
docker exec ontology-doris-fe curl -sL --location-trusted -u root:'' \
  -X PUT "http://127.0.0.1:8030/api/ontology/idx_airline__booking/_stream_load" \
  -H "column_separator:\t" -H "columns:..." -T /tmp/booking.tsv
# → Status: Success Loaded: 501481
```

### 待修复

- [ ] **提升 Doris BE 内存**：`mem_limit` 从 `1g` 提到 `4g`+（生产建议 8g+），让 50 万行+ INSERT 不触顶
- [ ] **`DorisIndexStore` 大批量写入改 stream load**：当前 `upsert` 用 `INSERT ... VALUES`，适合小批量；大批量（>1万行）应走 stream load HTTP 接口（BE 直接读文件，内存恒定）。可在 `upsert` 内按数据量自动选路径，或新增 `bulk_upsert` 方法
- [ ] **`IndexSyncService.sync_now` 流式同步**：当前一次性 `scan_latest` 读全表到内存再 upsert，大表（booking 50万）应分页流式读 + 流式写，避免单端内存峰值
- [ ] **生产容量规划**：Doris BE 内存按「最大单表行数 × 行宽 × 副本数」估算，全量化后 Doris 存全量，需重新评估 BE 内存（POC 用 1g 跑 50 万行 booking 已触顶）

---

## 教训

1. **「索引列」→「全量列」放大了既有的内存瓶颈**：两个问题之前就存在（索引列数据量小，没触发），Doris 全量化后同步数据量增加，把 SeaTunnel worker 和 Doris BE 的内存配置不足暴露出来。架构升级时要重新评估基础设施容量。
2. **fire-and-forget 并发是隐性 OOM 源**：`02_setup_pipeline` 的 `start` 不等完成，看似串行实则并发 7 个 job。任何「启动后台任务」的 API 都要明确并发度，否则下游资源会被打爆。
3. **INSERT 和 stream load 的内存特性不同**：Doris `INSERT ... VALUES` 在 BE 侧聚合占内存，stream load 流式读文件内存恒定。大批量写入应优先 stream load，`upsert` 适合小批量/点更新。
4. **容器内存限制和 JVM 堆要匹配**：SeaTunnel worker 容器 512m 但 metaspace 设 2g，Doris BE 容器 1g 但写 50 万行——都是「容器内存 < 工作负载需求」。`mem_limit` 和 `-Xmx`/`mem_limit`（BE 配置）要协同调整，不能只设一端。

---

## 关联代码索引

| 位置 | 内容 |
|------|------|
| `docker-compose.yml` | `x-resources`（512m）/ `x-resources-heavy`（1g）锚点；SeaTunnel worker `*dev-resources`；Doris BE `*heavy-resources` |
| `benchmark/scripts/02_setup_pipeline.py` | 并发启动 sync task（fire-and-forget） |
| `src/ontology/services/index_sync_service.py` | `sync_now` 一次性读全表 + upsert |
| `src/ontology/layers/index/doris_index_store.py` | `upsert` 用 INSERT VALUES 分批 1000 行 |
