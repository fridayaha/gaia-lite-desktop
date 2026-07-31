# Action 同步链路改造方案：PG→Doris 近实时 + PG→Iceberg→Doris 最终一致（去 SeaTunnel 化）

> **状态**：已对齐，待实现（2026-07-07）
> **相关文档**：[action-loop-design.md](../architecture/action-loop-design.md)、[adr-008-iceberg-doris-sync-path.md](../architecture/adr-008-iceberg-doris-sync-path.md)、[path-b-kafka-doris-schema-mismatch.md](../bugfix/path-b-kafka-doris-schema-mismatch.md)
> **修订**：本方案替代 ADR-008 中 PG→Kafka→Doris（路径 B）和 object_state→Iceberg 的 SeaTunnel CDC 链路；ADR-008 的 Iceberg→Doris（路径 A，服务外部数据接入）保留不变。
>
> **⚠️ 2026-07 T1.10 后续更新**：上方「修订」中「路径 A 保留不变」**已被推翻**。路径 A 的 SeaTunnel INDEX backfill（`PIPELINE_INDEX_BACKFILL_TEMPLATE` / `create_index_pipeline`）于 T1.10 整体删除——Doris 写入统一收口到 `ObjectIndexFunnel`（从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`，统一 rid 分配/复用 + 四引擎扇出）。本方案描述的 outbox INDEX/ARCHIVE effect 仍为当前真相（Action 写入路径）；但外部接入数据的 Doris 同步不再走「路径 A SeaTunnel backfill」，改走 ObjectIndexFunnel。SeaTunnel 现仅承担「外部源→Iceberg」搬运。

> **⚠️ 实现前必读 — PK 区分**：object_state 的 PK 是 `rid`（Gaia 内部 UUID），但 Doris idx 表和 Iceberg 业务表的 PK 是**业务主键的 `backing_column`**（如 `flight_id`）。MERGE INTO 的 `ON` 条件、Doris `delete_by_ids` 都必须用业务 PK 列，不能用 `rid`。详见 §3.3、§3.4。

---

## 一、背景与问题

### 1.1 现状（改造前）

Action 写入 PG `object_state` 后，设计上有两条同步链路，但**均未真正接线**：

- **路径 B（近实时）**：`PG object_state → SeaTunnel CDC → Kafka → SeaTunnel → Doris`。代码完整（`gaia_pg_to_kafka` + `gaia_kafka_to_doris` 两个 STREAMING job，119 topic live 验证通过），但 `ActionSyncService.ensure_cdc_pipelines` 是孤儿方法，**无任何调用方**，job 不会被启动。
- **object_state → Iceberg**：`create_action_cdc_pipeline` 同步的是 `action_execution_logs`（审计日志），**不是 object_state 本身**。object_state 的变更**完全没有落 Iceberg**。容灾恢复时 Iceberg 里没有 Action 数据。
- **路径 A（最终一致）**：`run_backfill_loop` 在 `main.py` lifespan 中被注释禁用；`sync_now`（Iceberg→Doris）只能兜 Iceberg 里的外部接入数据，对 Action 数据无效。

### 1.2 问题

1. **资源占用**：SeaTunnel 每 ObjectType 常驻 1-2 个 STREAMING job，100 个 ObjectType = 100+ job，远超 SeaTunnel 容器 512m/256m 内存限制。
2. **object_state 无 Iceberg 归档**：容灾恢复时数据丢失（Doris 重建无源、object_state 重建无源）。
3. **接线断裂**：`ensure_cdc_pipelines` 无调用方，路径 B 名存实亡。

### 1.3 调研结论

- object_state 是 Gaia 自管的表（同库 PG），用 PG-CDC 逻辑复制槽 + Kafka + SeaTunnel 同步到自己的 Doris，是"杀鸡用牛刀"——三层基础设施只为同步一张表。
- outbox 模式已是事务安全的 CDC 替代（PG-CDC 解决的是"第三方不写 outbox"的场景，而 Action 自己写 outbox）。
- SeaTunnel 应专注于"外部数据源接入"（ADR-014 本职），不应承担自管数据流的同步。

---

## 二、核心架构

### 2.1 数据流总览

```
Action → PG 事务 (object_state + execution_log + outbox[INDEX|ARCHIVE]) ─commit─→ 返回 "applied"
                    │
                    ▼ outbox 表（PENDING，事务安全，PG 持久化）
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
   OutboxExecutor          SyncFlushScheduler
   (INDEX, 1s 轮询)        (ARCHIVE, 5min/1000条 微批)
   按 effect_type 过滤       按 ontology 分桶
   (排除 ARCHIVE)           双触发：count≥N 或 等待≥T
         │                     │
   按 mutation_type 分流:    按 ObjectType 拆分,
   CREATE/UPDATE→upsert     按 mutation_type 分流:
   DELETE→delete_by_ids     CREATE/UPDATE→merge(delete=False)
         │                  DELETE→merge(delete=True)
         ▼                     │
   DorisIndexStore         IcebergStore.merge
   (aiomysql 直连 9030)     (Trino MERGE INTO, 按业务 PK)
         │                     │
         ▼                     ▼
   Doris idx 表             Iceberg 业务表
   (近实时, ≤1s)           (最终一致, ≤5min)
                                │
                   容灾恢复: sync_now (Iceberg→Doris, 已有)
                   容灾恢复: object_state 重建 ← Iceberg 业务表最新态
```

### 2.2 两条路径的调度机制分离

| 路径 | 调度机制 | 延迟 | 理由 |
|------|---------|------|------|
| **INDEX（→Doris）** | OutboxExecutor 高频轮询（1s） | ≤1s | Doris 是在线读主源，必须近实时；Doris Unique 模型 INSERT 幂等，不怕频繁写 |
| **ARCHIVE（→Iceberg）** | SyncFlushScheduler 微批（5min/1000条） | ≤5min | Iceberg 小文件敏感，控 commit 频率；配合已有 IcebergMaintenanceService.optimize 治理小文件 |

### 2.3 去SeaTunnel化的边界

- **去掉**：PG→Kafka→Doris（路径 B，object_state 同步）、object_state→Iceberg 的 SeaTunnel CDC
- **保留**：ADR-008 路径 A（Iceberg→Doris，服务**外部数据源接入**的批量数据，非 object_state）。SeaTunnel 仍承担 ADR-014 的外部数据源接入职责。
  - **⚠️ 2026-07 T1.10 更新**：路径 A 的 SeaTunnel backfill 已删除，外部接入数据的 Doris 同步改走 `ObjectIndexFunnel`（Python 侧直连 DorisIndexStore.upsert）。SeaTunnel 仅保留「外部源→Iceberg」搬运。

---

## 三、关键设计决策

### 3.1 复用 outbox 表，按 effect_type 隔离（不冲突）

outbox 表已服务多种 effect_type（WEBHOOK/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC/NOTIFICATION），新增 INDEX/ARCHIVE 复用同一张表，靠 `effect_type` 字段区分。

> **effect_type 大小写**：ActionEffectConfig.type 用小写（如 `write_back`），但 OutboxExecutor._execute 统一 `.upper()` 比较（历史存大写）。新增 INDEX/ARCHIVE 创建时存大写，消费时也大写比较，保持一致。

| effect_type | 用途 | 谁创建 | 谁消费 |
|------------|------|--------|--------|
| WEBHOOK/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC/NOTIFICATION | Action 完成后的业务副作用（用户配置） | ActionType 的 `effects` 配置 | OutboxExecutor 逐条 |
| **INDEX** 🆕 | 同步 object_state 变更到 Doris | ActionService 自动追加 | OutboxExecutor（近实时，1s）|
| **ARCHIVE** 🆕 | 归档 object_state 变更到 Iceberg | ActionService 自动追加 | SyncFlushScheduler（微批，5min）|

**不冲突的原因**：
- 写入：同一 `create_outbox_record`，effect_type 不同，一个 Action 可同时写多条不同 type 的记录，各自独立
- 消费：OutboxExecutor 拉取时排除 ARCHIVE（`exclude_effect_types=["ARCHIVE"]`），SyncFlushScheduler 只拉 ARCHIVE，两者用 effect_type 过滤各取各的
- 清理：按 status + updated_at 删，所有 effect_type 一视同仁

### 3.2 按 ontology 分桶调度，按 ObjectType 分写（两个正交维度）

| 维度 | 含义 | 设计 |
|------|------|------|
| **调度/分桶维度** | flusher 按什么键攒批、决定"什么时候触发" | 按 **ontology**（一个 Action 涉及多个 ObjectType 的变更尽量同批 flush，保证事务完整性）|
| **物理写入维度** | flush 时实际写到哪些表 | 按 **ObjectType**（每个 ObjectType 的 Doris idx 表 / Iceberg 业务表各自独立写，数据层隔离不变）|

**"尽量同时写"的精确含义**：一个本体批次在一次 flush 调用内完成所有 ObjectType 的写入，不跨 tick。但每个 ObjectType 仍是各自独立的 upsert/merge（Doris/Iceberg 跨表无法原子 commit，"尽量同时"是能做到的最好一致性）。

> **注意**：按 ontology 分桶只用于 ARCHIVE（SyncFlushScheduler）。INDEX 走 OutboxExecutor 逐条近实时消费，不攒批。

### 3.3 复用业务 Iceberg 表，用 MERGE INTO 实现覆盖

**决策**：Action 变更直接 merge 到业务 Iceberg 表（`ontology.<snake_type>`），不新建归档表。外部读 Iceberg 时看到的是最新态（旧记录被 PK 覆盖）。

**技术依据**（调研确认）：
- Trino 的普通 `INSERT` 对 Iceberg v2 upsert 表**不会自动去重**，即使表配了 `primary-keys` + `write.upsert.enabled`，`INSERT` 仍追加新行产生重复 PK。upsert 语义只在 Flink 写入时生效。
- Trino 要实现"按 PK 覆盖"，必须用 **`MERGE INTO`**（参考 Trino 482 官方文档、AWS Prescriptive Guidance、Starburst 文档）。
- 当前 `IcebergStore.append`（`iceberg_store.py:438`）用 `INSERT INTO ... VALUES`，在业务表上会产生重复行，**不能实现覆盖**。

**⚠️ 关键：MERGE 的 PK 是业务主键，不是 rid**

三层存储的 PK 各不相同，实现时必须区分：

| 存储 | PK | 来源 |
|------|-----|------|
| object_state (PG) | `rid`（Gaia 内部 UUID） | ObjectStateModel 主键 |
| Doris idx 表 | **业务 primary_key 的 backing_column**（如 `flight_id`） | IndexFieldExtractor 把 ObjectType.primary_key 对应属性分类为 PRIMARY_KEY |
| Iceberg 业务表 | **业务 primary_key 的 backing_column** | SeaTunnel sink `iceberg.table.primary-keys = source.primary_keys` |

所以 MERGE INTO 的 `ON` 条件必须用**业务 PK 列**（从 ObjectType 配置查 `primary_key` api_name → PropertyDef `backing_column`），不能用 `rid`。

**因此新增 `IcebergStore.merge` 方法**，用 Trino `MERGE INTO`：
```sql
-- CREATE/UPDATE：WHEN MATCHED THEN UPDATE + WHEN NOT MATCHED THEN INSERT
MERGE INTO iceberg.ontology.flight AS target
USING (VALUES ('CA123', 'delayed', ...)) AS source (flight_id, status, ...)
ON target.flight_id = source.flight_id
WHEN MATCHED THEN UPDATE SET status = source.status, ...
WHEN NOT MATCHED THEN INSERT (flight_id, status, ...) VALUES (source.flight_id, source.status, ...)

-- DELETE_OBJECT：WHEN MATCHED THEN DELETE
MERGE INTO iceberg.ontology.flight AS target
USING (VALUES ('CA123')) AS source (flight_id)
ON target.flight_id = source.flight_id
WHEN MATCHED THEN DELETE
```

**Doris 侧 DELETE**：调已有 `DorisIndexStore.delete_by_ids(ont, type, ids, pk_column)`（按业务 PK 列删）。

### 3.4 三层物理列对齐（backing_column）

object_state（PG JSONB）、Doris idx 表、Iceberg 业务表的物理列名都是 `backing_column`，完全对齐：

| 存储 | 结构 | PK | 列名 |
|------|------|-----|------|
| object_state (PG) | `properties` JSONB（全量属性），key = backing_column | rid（内部 UUID） | backing_column |
| Doris idx 表 | 平铺列（每属性一列，PRIMARY_KEY/INVERTED/RANGE/STORED_ONLY）| 业务 PK 的 backing_column | backing_column |
| Iceberg 业务表 | 平铺列（外部源表列）| 业务 PK 的 backing_column | backing_column |

> ⚠️ 修正认知：Doris idx 表**不是** `rid+version+properties(JSON)` 4 列结构（那是 `DORIS_INDEX_TABLE_DDL` 模板里的旧定义，实际建表走 `DorisIndexStore.create_index_table`，是平铺列）。object_state 在 PG 里用 JSONB 存，但写入 Iceberg/Doris 时**展开成平铺列**（record 的 key = 列名 = backing_column）。

> ⚠️ **写入时的列展开**：outbox payload 的 `properties` 是 `{backing_column: value}` 的 dict，写入 Doris/Iceberg 时直接作为 row（`{col: val}`），key 即列名。CREATE/UPDATE 写全量属性列；DELETE 只需 PK 列。

### 3.5 关系（object_links）不同步

- **外键关系**（多数）：通过对象的属性表达（如工单的 `customer_id`），随对象属性一起同步，无需单独处理。
- **多对多关系**（object_links 表，边缘场景）：**本期不归档**。理由：(1) 边缘场景；(2) object_links 数据量小，PG 本身即持久化；(3) 关系重建可从 action_execution_logs 审计日志回放。

### 3.6 事务安全性与"半成品读取"规避

**Action 写入有事务**：
- 单条 `execute_action`：object_state + execution_log + outbox 在**同一 PG 事务**，`commit_transaction()` 一次提交（`action_service.py:717`）
- 批量 `execute_batch_action`：逐 item 独立事务（每 item 调 execute_action，独立 commit）。Batch 期间 flusher 可能拿到"部分 batch"（如 10000 条 batch 在第 3000 条 commit 后 tick），但 batch 设计为幂等可重入（每 item 独立幂等键），部分同步是安全的。

**PG READ COMMITTED 隔离保证**：flusher 用独立 session，`SELECT ... FROM outbox WHERE status='PENDING'` 只能看到已 commit 的行。Action 事务内的 outbox 记录在 commit 前对 flusher 不可见。**不会读到"写了一半的 Action"**。

**outbox 与 object_state 原子提交**：outbox 记录存在的充要条件 = object_state 已提交。flusher 从 outbox payload 拿到的是 Action 算好的 after_snapshot（Step 8.5 从 `get_object_state` 读的全量快照，含所有属性），不是去读 object_state 中间态。

### 3.7 并发安全：FOR UPDATE SKIP LOCKED

多实例 HA 部署时，两个 flusher 可能拉到同一批 outbox。claim 时用行锁规避（outbox 模式标准做法）：
```sql
SELECT ... FROM outbox
WHERE effect_type='ARCHIVE' AND status='PENDING' AND target_ontology=:t
FOR UPDATE SKIP LOCKED
LIMIT 1000
```
单实例时无额外开销，多实例时自动分片。

> **INDEX 侧不需要 SKIP LOCKED**：OutboxExecutor 现有 `fetch_pending_outbox` 无行锁，单实例运行。若未来 HA 部署多实例，INDEX 侧也需加 SKIP LOCKED（改造 `fetch_pending_outbox`）。本期单实例，不改。

---

## 四、outbox 表的用途与清理

### 4.1 outbox 解决"双写问题"

Action 写完 object_state 后要同步到 Doris/Iceberg，如果直接在 Action 事务里调外部系统，PG 和 Doris/Iceberg 不在同一原子事务，会出现幽灵数据或数据丢失。outbox 模式把"要同步"这件事本身当成数据，和 object_state 写在同一个 PG 事务里，保证原子一致；消费方异步拉取执行，失败可重试。

### 4.2 状态流转（已有机制，方案沿用）

```
PENDING ──消费成功──→ COMPLETED ──(7天后)──→ 清理删除
   │
   ├──消费失败──→ PENDING (retry_count+1, next_retry_at=指数退避)
   │                  │
   │                  └──retry_count ≥ max_retries──→ DLQ (人工审查，不自动删)
```

- INDEX：OutboxExecutor 消费成功 → `mark_outbox_completed` → COMPLETED
- ARCHIVE：SyncFlushScheduler 消费成功 → `mark_outbox_batch_completed`（批量版）→ COMPLETED
- 失败：`_handle_failure` → retry_outbox 或 move_outbox_to_dlq

### 4.3 清理机制（新增，补当前缺口）

当前 outbox 表**无任何清理机制**，COMPLETED/FAILED/DLQ 记录永久留存。本方案写入量增加（每 Action 改 N 对象 = 2N 条 outbox），必须补清理。

**业界最佳实践**（调研确认）：
- 不要消费后立即删除（保留用于重放/审计/去重）
- 基于时间的保留窗口（7-30 天）
- 独立清理任务，和发布解耦
- DLQ 记录需人工审查，不自动删

**方案**：SyncFlushScheduler 加 `run_cleanup_loop`（1h 间隔），删除超过保留期的 COMPLETED/FAILED 记录：

| 状态 | 处理 |
|------|------|
| PENDING | 不删（等消费/重试）|
| COMPLETED | 保留 7 天后删 |
| FAILED | 保留 7 天后删 |
| DLQ | **不自动删**（人工审查）|

```sql
DELETE FROM outbox WHERE status IN ('COMPLETED','FAILED') AND updated_at < NOW() - INTERVAL '7 days'
```

---

## 五、微批策略（ARCHIVE 专用）

### 5.1 双触发机制

每张目标表（按 ontology 分桶）独立判断，先到先 flush：

```
定时任务（每 1 分钟 tick 一次）:
    for 每个 ontology:
        count = SELECT COUNT(*) FROM outbox 
                WHERE effect_type='ARCHIVE' AND status='PENDING' AND target_ontology=:ont
        if count >= 1000  OR  距该 ontology 上次 flush >= 5 分钟:
            拉取这批 → 按 ObjectType 拆分 → 各自 IcebergStore.merge → mark COMPLETED
        else:
            跳过，下个 tick 再看
```

### 5.2 commit 频率账

| 场景 | commit 频率 |
|------|------------|
| 低频表（5min 内 < 1000 变更）| 每 5min 1 次 = 288 次/天/表 |
| 高频表（5min 内 ≥ 1000 变更）| 攒满 1000 条 1 次 |

对比"每事务直写"（864000 次/天/表），微批降 600 倍。配合 `IcebergMaintenanceService.optimize`（目标 128MB/文件，已有），小文件完全可治理。

### 5.3 Iceberg 官方建议

Iceberg 官方对 streaming 写入建议 trigger interval ≥ 1min（避免高频 commit 产生小文件）。本方案 5min 窗口远超下限。Palantir Foundry 也有"限制增量输入批次大小"的官方建议（避免 continuous rebuilding）。

---

## 六、幂等性

### 6.1 Doris 侧（INDEX）
Doris Unique 模型 + Merge-on-Write，`INSERT` 按**业务 PK**（backing_column）幂等覆盖。重试安全：同一条 outbox 重试多次，Doris 只保留最新。DELETE 按 PK 列删，幂等（删不存在的行无副作用）。

### 6.2 Iceberg 侧（ARCHIVE）
`MERGE INTO` 天然幂等（按业务 PK 匹配，重复执行结果一致），重试安全。DELETE 的 `WHEN MATCHED THEN DELETE` 也幂等（匹配不到无副作用）。比 append 方案更优（append 需要去重键，MERGE 不需要）。

---

## 七、容灾恢复路径

| 故障 | 恢复方式 |
|------|---------|
| Doris 挂了 | outbox 积压在 PG（PENDING），Doris 恢复后 OutboxExecutor 追上；期间读降级 object_state（PG，read-your-writes）|
| Doris 数据丢失/重建 | `sync_now`（已有，Iceberg→Doris upsert）：业务表已是最新态（MERGE 覆盖），直接读 |
| PG object_state 丢失 | 从 Iceberg 业务表读最新态回填（业务表 = 最新视图，MERGE 覆盖了旧记录）|
| Iceberg 挂了 | outbox 积压，Iceberg 恢复后从 outbox 补档（outbox 持久化在 PG）|
| 单条 outbox 处理失败 | retry_count/backoff/DLQ（已有机制）|

---

## 八、改动清单

### 8.1 outbox schema 增强（Alembic migration）

新增 `target_ontology` 列（ARCHIVE 分桶键）+ 联合索引：
```python
op.add_column('outbox', sa.Column('target_ontology', sa.String(255), nullable=True))
op.create_index('ix_outbox_sync_claim', 'outbox',
                ['effect_type', 'status', 'target_ontology', 'created_at'])
```

ORM（`core/models/ontology.py` OutboxModel）加 `target_ontology` 字段，更新 effect_type 注释。

### 8.2 metadata 层新增方法（`postgres_meta_store.py`）

- `fetch_pending_outbox(batch_size, effect_type=None, exclude_effect_types=None)` — 加 effect_type 过滤/排除
- `count_pending_by_ontology(effect_type)` — GROUP BY target_ontology
- `claim_pending_by_ontology(effect_type, ontology, batch_size)` — FOR UPDATE SKIP LOCKED
- `mark_outbox_batch_completed(ids)` — 批量标记
- `delete_old_completed_outbox(retention)` — 清理旧记录

### 8.3 ActionService 新增 `_create_sync_outbox_records`（`action_service.py`）

在 Step 9（commit 前），为每个 CREATE/UPDATE/DELETE mutation 生成 INDEX + ARCHIVE 两条 outbox 记录。

**outbox payload 结构**（INDEX 和 ARCHIVE 共用）：
```python
{
    "rid": "<Gaia内部UUID>",          # object_state 的 PK
    "object_type_api_name": "Flight",        # 用于查 ObjectType 配置（PK api_name）
    "ontology_api_name": "default",          # 分桶键
    "version": 3,                             # object_state 新版本号
    "mutation_type": "CREATE_OBJECT",         # CREATE_OBJECT/UPDATE_OBJECT/UPDATE_PROPERTY/DELETE_OBJECT
    "properties": {"flight_id": "CA123", "status": "delayed", ...}  # 全量快照，key=backing_column
}
```

**关键**：
- `properties` 是全量快照（从 `after_snapshot` 取，含业务 PK 值）。CREATE/UPDATE 时写全量列；DELETE 时只用 PK 列。
- `object_type_api_name` 让 flusher 能查 ObjectType 拿 `primary_key` api_name → PropertyDef `backing_column`（MERGE/DELETE 的 PK 列名）。
- RELATE/UNRELATE/CLEAR_LINKS mutation 跳过（关系不同步，见 3.5）。

**target_ontology 字段**：在 `create_outbox_record` 新增参数，存 `ontology_api_name`，供 ARCHIVE 分桶。

### 8.4 IcebergStore 新增 `merge` 方法（`iceberg_store.py`）

```python
async def merge(
    self, dataset: str, rows: list[dict[str, Any]], pk_columns: list[str],
    *, delete: bool = False,
) -> WriteResult:
    """MERGE INTO — 按 PK 覆盖旧记录（upsert）或删除（delete=True）。
    
    Trino INSERT 不去重（即使 v2 upsert 表），必须用 MERGE INTO。
    
    Args:
        dataset: 业务表名（如 ontology.flight）
        rows: 行数据（backing_column key）。delete=True 时只需 PK 列。
        pk_columns: 业务主键列名（backing_column，用于 ON 匹配）
        delete: True=WHEN MATCHED THEN DELETE；False=UPDATE+INSERT
    """
    # delete=True:  MERGE INTO ... WHEN MATCHED THEN DELETE
    # delete=False: MERGE INTO ... WHEN MATCHED THEN UPDATE SET ... WHEN NOT MATCHED THEN INSERT ...
```

**pk_columns 来源**：flusher 从 ObjectType 查 `primary_key`（api_name）→ PropertyDef `backing_column`。需要 metadata 查询，缓存 per ObjectType。

### 8.5 OutboxExecutor 扩展（`outbox_executor.py`）

- 构造函数加 `index_store: DorisIndexStore | None = None`
- `_execute` 加 INDEX 分支：CREATE/UPDATE → `DorisIndexStore.upsert`（properties 展开为平铺列）；DELETE → `DorisIndexStore.delete_by_ids`（按业务 PK 列删）。ARCHIVE 分支 return（skip）。
- `process_pending` 改为排除 ARCHIVE（`exclude_effect_types=["ARCHIVE"]`）
- **业务 PK 列名获取**：INDEX 处理时需查 ObjectType 拿 `primary_key` api_name → backing_column（DELETE 用）。可注入 container 或缓存。

### 8.6 SyncFlushScheduler（新文件 `services/sync_flush_scheduler.py`）

- `run_flush_loop(container)` — 每 60s tick，按 ontology 分桶，双触发（1000条/5min）
- `run_cleanup_loop(container)` — 每 1h 清理 7 天前 COMPLETED/FAILED 记录
- `_flush_ontology(container, ont)` — 认领批次 → 按 ObjectType 拆分 → 各自处理

**ARCHIVE flush 逻辑**：
```
for 每个 ObjectType (从批次按 object_type_api_name 拆分):
    1. 查 ObjectType 配置拿 primary_key api_name
    2. 查 PropertyDef 拿 primary_key 的 backing_column（= Iceberg/Doris PK 列名）
    3. 按 mutation_type 分流:
       - CREATE/UPDATE: rows = [payload.properties 展开] → IcebergStore.merge(table, rows, [pk_col], delete=False)
       - DELETE:        rows = [{pk_col: payload.properties[pk_col]}] → IcebergStore.merge(table, rows, [pk_col], delete=True)
    4. 成功 → mark_outbox_batch_completed；失败 → retry_outbox
```

**ObjectType 配置查询缓存**：primary_key api_name → backing_column 的映射 per ObjectType 缓存（避免每条记录查一次），ObjectType define/update 时失效。

### 8.7 container 注入调整（`config/container.py`）

- `outbox_executor` 注入 `index_store=self.index_store`
- 新增 `sync_flush_scheduler` property

### 8.8 lifespan 调整（`main.py`）

启动 `sync_flush_task`（run_flush_loop）+ `cleanup_task`（run_cleanup_loop）。

### 8.9 阶段 2（验证后）：删除 SeaTunnel 同步代码

- 删除 `ActionSyncService`（孤儿方法）
- 删除 `create_pg_to_kafka_pipeline` / `create_kafka_to_doris_pipeline` / PIPELINE_PG_TO_KAFKA_TEMPLATE / PIPELINE_KAFKA_TO_DORIS_TEMPLATE
- 删除注释掉的 `run_backfill_loop`
- **保留**：~~`create_action_cdc_pipeline`（PG→Iceberg 审计日志，若仍需要）~~ **2026-07-10 删除**（无调用方，审计日志 PG append-only 已足够）、~~路径 A 的 PIPELINE_INDEX_BACKFILL/STREAM（服务外部数据接入）~~ **2026-07 T1.10 删除**（Doris 写入统一收口到 ObjectIndexFunnel）

---

## 九、测试计划

| 测试 | 覆盖点 |
|------|--------|
| `test_create_sync_outbox_records` | 每 mutation 生成 INDEX+ARCHIVE 两条，target_ontology 正确 |
| `test_outbox_atomic_with_object_state` | outbox 与 object_state 同事务（真 DB，rollback 两者都不落库）|
| `test_fetch_pending_excludes_archive` | OutboxExecutor 不拉 ARCHIVE |
| `test_outbox_executor_index_to_doris` | INDEX → DorisIndexStore.upsert（近实时）|
| `test_outbox_executor_skips_archive` | ARCHIVE 分支 return，不消费 |
| `test_count_pending_by_ontology` | GROUP BY 分组正确 |
| `test_claim_pending_skip_locked` | 并发 claim 不重叠 |
| `test_flush_archive_count_threshold` | 攒够 1000 条触发 MERGE |
| `test_flush_archive_time_threshold` | 不足 1000 但满 5min 触发 |
| `test_flush_archive_per_type_split` | 一个本体批次内多 type 各自 MERGE |
| `test_iceberg_merge_upsert` | MERGE INTO 按 PK 覆盖旧记录（不产生重复行）|
| `test_iceberg_merge_idempotent` | 重试 MERGE 结果一致 |
| `test_flush_failure_retry` | MERGE 失败 → retry，不影响其他 type |
| `test_doris_index_idempotent` | INDEX CREATE/UPDATE 重试，Doris 只保留最新；DELETE 重试幂等 |
| `test_iceberg_merge_delete` | DELETE_OBJECT → MERGE WHEN MATCHED THEN DELETE，旧记录被删 |
| `test_delete_old_completed_outbox` | 清理 7 天前 COMPLETED/FAILED，不删 PENDING/DLQ |
| 本地冒烟 | Action → 1s 内 Doris 可查；5min 后 Iceberg 可查（MERGE 覆盖）|

---

## 十、遗留任务

> **强制 primary_key 约束**：当前外部接入 Iceberg 表如果 `source.primary_keys` 为空（`sea_tunnel_engine.py:796` 条件跳过），表无 PK，MERGE 无法按 PK 匹配。需后续加限制：所有 ObjectType 必须有 primary_key，且外部接入时必须配置 source primary_keys。本期先假设所有表都有 PK，不加强制校验。

> **外部数据接入路径的图/时空投影**（2026-07-10）：Action 写入路径的节点投影（OutboxExecutor INDEX effect 侧）和边投影（ActionService Step 11）已接线，但外部数据接入路径（SeaTunnel Iceberg→Doris backfill）尚未接线图/时空投影。需新增 SeaTunnel Iceberg→PG（PostGIS）pipeline（Jdbc sink，同 Kafka→TimescaleDB 模式），让外部接入的空间数据直接写 PostGIS。Neo4j 图投影需在 backfill 完成后触发全量重建（`rebuild_for_object_type`，数据源从 object_state 改为 SeaTunnel 读 Iceberg）。

---

## 十-A、图/时空投影接线（2026-07-10）

### 背景

图（Neo4j）和时空（PostGIS/TimescaleDB）投影的 schema provision 已在 `OntologyService.define_object_type` 实现，但**数据投影写入**在 2026-07-10 前未接线——`GraphProjector.project_object` / `GeoTimeProjector.project_object` 无调用方，Neo4j 和 PostGIS 只有空 schema 无数据。本次接线解决了此问题。

### Capabilities 门控（四道门）

投影受四道门控制，全部通过才写（对齐 Palantir Foundry Ontology Manager Capabilities tab）：

| 门 | 条件 | 实现 |
|----|------|------|
| 门 1 | `storage_type == MANAGED` | VIRTUAL 拒绝启用（ValidationError） |
| 门 2 | `data_type` 匹配 | GEOPOINT/GEOSHAPE→PostGIS, indexed→Neo4j |
| 门 3 | 关系存在（仅图） | 启用图索引时检查 LinkType，无关系则警告 |
| 门 4 | 用户显式启用 | `ObjectTypeCapabilities`（graph_indexing_enabled / geotime_indexing_enabled） |

Doris 基础索引不受门控——始终为 MANAGED 类型启用（在线读主源，红线 #4）。

### 接线点

**① 对象节点投影（Action 写入路径）** — OutboxExecutor 侧

`_sync_index_to_doris` 处理完 Doris upsert/delete 后，用同一份 outbox payload（`rid` + `properties`）调 projector：
- CREATE/UPDATE → `_project_object_upsert` → `graph_projector.project_object` + `geotime_projector.project_object`
- DELETE → `_project_object_delete` → `graph_projector.delete_object` + `geotime_projector.delete_object`

数据源是 outbox payload 自带的 properties（不读 Doris、不读 object_state）。fail-tolerant：投影失败不影响 Doris 同步（已完成）。

**② 边投影（Action 写入路径）** — ActionService 侧

新增 Step 11（Step 10 commit 后）：`_project_link_mutations` 遍历 RELATE/UNRELATE：
- RELATE → `graph_projector.project_link`
- UNRELATE → `graph_projector.delete_link`（新增方法）

门控：查源 ObjectType 的 `capabilities.graph_indexing_enabled`，未启用则跳过。fail-tolerant。

**③ 外部数据接入路径** — 待接线

需新增 SeaTunnel Iceberg→PG（PostGIS）pipeline（Jdbc sink），让外部接入的空间数据直接写 PostGIS。Neo4j 图投影需在 backfill 完成后触发全量重建。

### 代码改动

| 文件 | 改动 |
|------|------|
| `core/models/ontology.py` | `ObjectTypeModel` 加 `capabilities` JSONB 列 |
| `core/schemas/ontology.py` | 新增 `ObjectTypeCapabilities` pydantic 模型；`ObjectType`/`ObjectTypeCreate` 加 `capabilities` 字段 |
| `layers/metadata/postgres_meta_store.py` | `create_object_type` / `update_object_type` 支持 capabilities |
| `services/ontology_service.py` | provision 受 capabilities 门控；`update_object_type_fields` 支持能力切换（Gate 1/3 校验 + 触发 provision） |
| `services/outbox_executor.py` | 构造函数加 projectors；`_sync_index_to_doris` 后调投影；新增 `_project_object_upsert`/`_project_object_delete` |
| `services/action_service.py` | 构造函数加 `graph_projector`；新增 Step 11 `_project_link_mutations` |
| `services/graph_projector.py` | 新增 `delete_link` 方法 |
| `config/container.py` | OutboxExecutor + ActionService 注入 projectors |
| `alembic/versions/...capabilities...py` | 加 `capabilities` JSONB 列 migration |
| 前端 `ObjectDetailPanel.tsx` | 新增「能力」tab + `CapabilitiesTab` 组件（图/时空开关 + GateCheck 门控可视化） |
| 前端 `types/index.ts` + `api/client.ts` | `ObjectTypeCapabilities` 类型 + `updateObjectTypeCapabilities` 函数 |

---

## 十一、决策记录（讨论过程）

| # | 议题 | 最终决策 | 关键依据 |
|---|------|---------|---------|
| 1 | PG→Doris 是否去 SeaTunnel | **是** | object_state 是自管表，用 PG-CDC+Kafka+SeaTunnel 同步是杀鸡用牛刀；outbox 模式已是事务安全替代 |
| 2 | object_state→Iceberg 如何保证不丢 | outbox ARCHIVE effect + MERGE | 之前完全缺失，需补；append 直写有小文件问题 |
| 3 | Iceberg 小文件如何治理 | 微批（5min/1000条）+ 已有 optimize | Iceberg 官方建议 trigger ≥ 1min；Palantir 建议限流批处理 |
| 4 | 多目标 Iceberg 表如何处理 | 按 ontology 分桶调度，按 ObjectType 分写 | 两个维度正交：调度按本体（事务完整），物理按 type（数据隔离）|
| 5 | 归档表结构 | 复用业务表，MERGE 覆盖 | 业务表/idx/object_state 三层列对齐（backing_column）；Trino INSERT 不去重须用 MERGE；**PK 是业务主键 backing_column，不是 rid** |
| 6 | outbox 粒度 | 每 mutation 一条 INDEX + 一条 ARCHIVE | 简单；payload 小（单对象快照）|
| 7 | 事务安全（半成品读取） | PG READ COMMITTED + outbox 原子提交保证 | Action 事务内 outbox 对 flusher 不可见，commit 后才可见 |
| 8 | INDEX 调度延迟 | OutboxExecutor 1s 轮询（近实时）| Doris 在线读主源必须近实时；不能等 1min |
| 9 | ARCHIVE 调度策略 | 双触发（1000条/5min），每 1min tick | 控 commit 频率，自适配高低频 |
| 10 | 关系是否同步 | 本期不同步 object_links | 外键关系随对象属性同步；多对多边缘场景从审计日志回放 |
| 11 | outbox 清理 | 7 天保留 + 1h 清理，DLQ 不自动删 | 业界共识：保留重放窗口，DLQ 人工审查 |
| 12 | 是否复用 outbox 表 | 是，按 effect_type 隔离 | 不冲突；outbox 模式标准设计 |
| 13 | 跨本体同名 ObjectType | 行里带 ontology_api_name，恢复时按本体过滤 | Iceberg 表名不带本体前缀（已有命名，不改）|
