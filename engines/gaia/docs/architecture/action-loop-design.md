# Action 闭环设计 (Action Closed Loop)

> **用途**：Action 闭环的完整设计参考，覆盖意图、架构、组件契约、数据流、失败语义、read-your-writes、可观测性与测试标准。供后续设计与开发对齐。
>
> **关联文档**：[action-architecture.md](./action-architecture.md) · [index-acceleration-design.md](./index-acceleration-design.md) · [implementation-status.md](./implementation-status.md)
>
> **状态**：核心闭环已接通（2026-06-18），真实 PG 环境验证通过。**2026-07-08 去 SeaTunnel 化**：object_state 同步改 outbox 驱动 (INDEX/ARCHIVE effect), 删除 SeaTunnel CDC 链路。详见文末"实现状态" + [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

---

## 一、设计意图

### 1.1 要解决的问题

Action（动作）是"从数据洞察到业务执行"的闭环写回能力——用户/AI 修改业务对象后，变更要能被查询路径看到、副作用要可靠执行、主数据要持久化到 Iceberg、且能回写外部系统。

### 1.2 解法：Transactional Outbox + Read-your-writes + Outbox 驱动同步

对标 Palantir Foundry OSv2 的 Transactional Outbox 模式：

```
Action execute（同步热路径）
  → PG 原子提交：object_state + execution_log + outbox[INDEX|ARCHIVE|WEBHOOK|WRITE_BACK|...]
  → 返回 "applied"（read-your-writes，毫秒级）
  ─────────────────────────────────────────────
  → 异步：OutboxExecutor 消费 outbox
      - INDEX   → DorisIndexStore upsert/delete（近实时 ≤1s，在线读主源）
      - WEBHOOK/WRITE_BACK/SUB_ACTION/KAFKA_TOPIC → 业务副作用
  → 异步：SyncFlushScheduler 消费 ARCHIVE outbox → IcebergStore.merge（微批 ≤5min，主数据持久化）
  → 异步：ConflictDetector 事后审计（object_state vs Iceberg）
```

> **2026-07-08 架构演进**：原 SeaTunnel CDC 链路（PG WAL → Iceberg + Kafka→Doris）已废弃删除。object_state 是 Gaia 自管表，用 PG-CDC + Kafka + SeaTunnel 同步到自己的 Doris 是"杀鸡用牛刀"；outbox 模式已是事务安全的 CDC 替代（Action 自己写 outbox）。SeaTunnel 现只承担外部数据源接入（ADR-014 本职）。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

### 1.3 三个关键原则

1. **Transactional Outbox**：副作用与数据变更同 PG 事务落盘。对象改成功⇒outbox 必有任务；失败⇒绝无任务。
2. **Read-your-writes**：Action 返回 "applied" 后，object_state 立即可读，不依赖 CDC 同步完成。
3. **反馈环防御**：写回注入 `gaia_sync_tx`/`gaia_sync_user` 元数据，IngestionFilter 过滤自身回写数据。

---

## 二、架构定位

### 2.1 闭环链路在分层架构中的位置

```
Routes  /actions/execute  /actions/definitions
   │
Services 编排层
   ├─ ActionService          （同步：execute_action 热路径 + 主事务内追加 INDEX/ARCHIVE outbox）
   ├─ OutboxExecutor         （异步：消费 outbox — INDEX→Doris / WEBHOOK / WRITE_BACK / SUB_ACTION / KAFKA_TOPIC）
   ├─ SyncFlushScheduler     （异步：消费 ARCHIVE outbox 微批 → IcebergStore.merge + outbox 7 天清理）
   ├─ WriteBackManager       （写回 SQL 构建 + 反馈环标记）
   ├─ ConflictDetector       （事后审计）
   └─ ObjectQueryService     （read-your-writes 查询兜底）
   │
Layers
   ├─ Metadata (PG)          object_state / outbox / execution_log
   ├─ Dataset (Iceberg)      主数据持久化（ARCHIVE outbox MERGE 目标）
   └─ Index (Doris)          在线索引（INDEX outbox upsert 目标）
```

> **注意**：Pipeline (SeaTunnel) 不再出现在 Action 闭环链路中。SeaTunnel 退回外部数据源接入职责（ADR-014）。

### 2.2 职责边界

| 组件 | 职责 | 不做 |
|------|------|------|
| ActionService | execute 热路径：校验→规则→OCC→写 PG→主事务内追加 INDEX/ARCHIVE outbox→返回 applied | 不直接执行副作用、不直接同步 Doris/Iceberg（靠 outbox 异步） |
| ~~ActionSyncService~~ | **已删除（2026-07-08 去 SeaTunnel 化）** | — |
| OutboxExecutor | 消费 outbox：INDEX→Doris / WEBHOOK / WRITE_BACK / SUB_ACTION / KAFKA_TOPIC | 不写 object_state、不消费 ARCHIVE（交 SyncFlushScheduler）、不决策 retry 策略（用固定退避） |
| SyncFlushScheduler | 消费 ARCHIVE outbox 微批 → IcebergStore.merge；清理 7 天前 outbox | 不写 object_state、不处理 INDEX/WEBHOOK 等 |
| WriteBackManager | 构建 UPSERT/MERGE SQL + 注入反馈环标记 | 不执行 SQL（由 OutboxExecutor 执行） |
| ConflictDetector | object_state vs Iceberg 版本审计 | 不阻塞写入（事后告警） |
| ObjectQueryService | read-your-writes：先查 object_state 兜底 | 不写 object_state |

---

## 三、组件契约

### 3.1 ActionService (`services/action_service.py`)

`execute_action(object_type_api_name, action_api_name, request, ontology_api_name) -> ActionExecutionResult`

11 步链路：
1. 解析 ActionType 定义
2. 幂等检查（idempotency_key）
3. 解析参数定义
4. 校验参数（ActionValidator）
5. 评估规则（ActionRuleEngine：derivations + constraints）
6. 权限检查（catalog.check_access write）
7. 构建 mutations（含 expected_version）
8. 行级 OCC + 写 object_state（INSERT ON CONFLICT / UPDATE WHERE version=expected）
9. 写 execution_log + outbox（同事务）
10. PG 原子提交
11. 主事务内追加 INDEX+ARCHIVE outbox（`_create_sync_outbox_records`，每个 CREATE/UPDATE/DELETE mutation 两条）

返回 `ActionExecutionResult`：
- `applied`：mutations 已提交 object_state（read-your-writes）
- `accepted`：重复请求（幂等键命中）
- `conflict`：行级版本 OCC 失败（affected_rows=0），caller 应刷新
- `validation_failed`：参数/规则校验错误

### 3.2 OutboxExecutor (`services/outbox_executor.py`)

| 方法 | 职责 |
|------|------|
| `run_forever()` | 后台轮询循环（lifespan 启动），poll_interval=1s |
| `process_pending()` | 拉取 PENDING outbox 批量执行（**排除 ARCHIVE**，交 SyncFlushScheduler） |
| `_execute(record)` | 按 effect_type 分发：WEBHOOK / WRITE_BACK / **INDEX** / SUB_ACTION / KAFKA_TOPIC（ARCHIVE 防御性 skip） |
| `_execute_index(record)` | **INDEX effect**：CREATE/UPDATE→DorisIndexStore.upsert / DELETE→delete_by_ids（近实时 ≤1s） |
| `_call_webhook` | HTTP POST + X-Idempotency-Key，合并 record.payload |
| `_do_write_back` | 调 WriteBackManager.build_upsert_sql + 执行 |
| `_execute_sql` | 按 jdbc scheme 分发：postgres(asyncpg) / mysql(aiomysql) |
| `_handle_failure` | 指数退避重试（2^n*10s ± jitter），超 max_retries 进 DLQ |

**副作用类型**：
- `INDEX` 🆕（2026-07-08）：effect_config 无；record.payload 含 object_type/ontology/mutation_type/properties(全量快照, key=backing_column)。OutboxExecutor 直调 DorisIndexStore。替代原 SeaTunnel Kafka→Doris 链路
- `WEBHOOK`：effect_config = `{url, payload?, headers?}`；record.payload 合并进 body
- `WRITE_BACK`：effect_config = `{jdbc_url, table, primary_key, changes}`；WriteBackManager 注入 `gaia_sync_tx`/`gaia_sync_user`，构建 `INSERT ... ON CONFLICT DO UPDATE`
- `SUB_ACTION` / `KAFKA_TOPIC` / `NOTIFICATION`：P1 (ADR-011) 扩展
- `ARCHIVE` 🆕（2026-07-08）：**不由 OutboxExecutor 消费**（process_pending 排除），交 SyncFlushScheduler 微批归档到 Iceberg

### 3.3 WriteBackManager (`services/write_back_manager.py`)

无状态 SQL 构建器 + 反馈环标记。
- `build_write_back_payload(changes, sync_tx_id)`：注入 `gaia_sync_tx`/`gaia_sync_user`
- `build_upsert_sql(table, pk, changes, sync_tx_id)`：`INSERT ... ON CONFLICT DO UPDATE`（:name 占位符）
- `build_merge_sql`：Oracle/SQL Server 的 MERGE 变体
- `extract_sync_metadata_from_row`：IngestionFilter 用来识别自身回写

### 3.4 ~~ActionSyncService~~ → outbox 驱动同步（2026-07-08 去 SeaTunnel 化）

**`services/action_sync_service.py` 已删除**。原 `ensure_cdc_pipelines` 编排的三条 SeaTunnel CDC pipeline 已废弃：
- ~~`cdc_<ontology>_actions`：PG action_execution_logs → Iceberg（审计）~~ — `create_action_cdc_pipeline` 已于 2026-07-10 删除（无调用方，审计日志 PG append-only 已足够）
- ~~`pg_to_kafka`：PG object_state → Kafka（实时索引源）~~ — 已删
- ~~`kafka_to_doris`：Kafka → Doris idx 表（实时索引写）~~ — 已删

**替代方案（outbox 驱动）**：ActionService 在主事务内为每个 mutation 追加 INDEX + ARCHIVE 两条 outbox，异步消费：
- **INDEX（→Doris，近实时 ≤1s）**：OutboxExecutor 1s 轮询消费，CREATE/UPDATE→upsert / DELETE→delete_by_ids
- **ARCHIVE（→Iceberg，微批 ≤5min）**：SyncFlushScheduler 按 ontology 分桶微批，调 IcebergStore.merge（Trino MERGE INTO 按业务 PK backing_column）

详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

### 3.5 ObjectQueryService read-your-writes (`services/object_query_service.py`)

`_load_physical` 路径前置 `_read_your_writes`：
- 点查（rids）：查 object_state，命中即投影返回
- filter 查询（top-level eq）：查 object_state，命中返回
- 复合/非 eq filter：跳过 object_state，走 Doris→Iceberg
- 无 filter 无 IDs：跳过（list-everything 走 Iceberg/Trino）
- object_state 异常：log warning，fall through 到原路径

未命中则 fall through 到 Doris 索引 → Iceberg 点查 → Trino 降级（见 [index-acceleration-design.md](./index-acceleration-design.md)）。

### 3.6 ConflictDetector (`services/conflict_detector.py`)

事后审计（不阻塞写入）：
- ~~`audit_snapshot_diff`：Iceberg snapshot diff 审计~~ — **2026-07-10 删除**（placeholder，且审计目标已从 Iceberg 改为 Doris）
- `verify_object_state_consistency(dataset, rids, pg_versions)`：object_state 版本 vs Iceberg 最新，返回不一致 ID 列表

---

## 四、数据流

### 4.1 同步热路径（execute → applied）

```
POST /actions/execute/{ontology}/{object_type}/{action}
  → ActionService.execute_action
      ├─ 校验 + 规则 + 权限
      ├─ 行级 OCC: upsert_object_state (INSERT ON CONFLICT / UPDATE WHERE version=expected)
      ├─ create_execution_log + create_outbox_record (WEBHOOK/WRITE_BACK/...) (同事务)
      ├─ _create_sync_outbox_records: 每个 mutation 追加 INDEX + ARCHIVE 两条 outbox (同事务)
      └─ PG commit
  → 返回 {status: "applied", action_id, affected_objects}
```

### 4.2 Read-your-writes（查询立即可见）

```
GET /query/objects {rids: [刚创建的ID]}
  → ObjectQueryService._load_physical
      ├─ _read_your_writes: query_object_states(rids=[...])
      │   → 命中 → 投影 properties → 返回  ✅ 立即可见
      └─ 未命中 → Doris 索引 → Iceberg 点查 → Trino 降级
```

### 4.3 异步副作用 + 近实时索引（OutboxExecutor）

```
OutboxExecutor.run_forever (lifespan 后台任务, 1s 轮询, 排除 ARCHIVE)
  → fetch_pending_outbox(exclude_effect_types=["ARCHIVE"])
  → 对每条 record:
      INDEX      → DorisIndexStore.upsert (CREATE/UPDATE) / delete_by_ids (DELETE)  ← 近实时索引 ≤1s
      WEBHOOK    → httpx.post(url, payload, X-Idempotency-Key)
      WRITE_BACK → WriteBackManager.build_upsert_sql → asyncpg/aiomysql 执行
      SUB_ACTION / KAFKA_TOPIC / NOTIFICATION → 各自处理
  → 成功: mark_outbox_completed
  → 失败: retry_outbox (指数退避) 或 move_outbox_to_dlq (超 max_retries)
```

### 4.3b 异步主数据归档（SyncFlushScheduler）

```
SyncFlushScheduler.run_flush_loop (lifespan 后台任务, 60s tick)
  → 按 ontology 分桶: count_pending_by_ontology("ARCHIVE")
  → 双触发: count ≥ 1000 或 距上次 flush ≥ 5min
  → claim_pending_by_ontology (FOR UPDATE SKIP LOCKED, 多实例 HA 安全)
  → 按 ObjectType 拆分 + 按 mutation_type 分流:
      CREATE/UPDATE → IcebergStore.merge(table, rows, [pk_col], delete=False)  ← Trino MERGE INTO 覆盖
      DELETE        → IcebergStore.merge(table, rows, [pk_col], delete=True)   ← WHEN MATCHED THEN DELETE
  → 成功: mark_outbox_batch_completed
  → 失败: retry_outbox_batch

SyncFlushScheduler.run_cleanup_loop (lifespan 后台任务, 1h)
  → delete_old_completed_outbox (7 天前 COMPLETED/FAILED, DLQ 不删)
```

### 4.4 异步主数据持久化 + 近实时索引（outbox 驱动，去 SeaTunnel 化）

> **2026-07-08 重构**：原 SeaTunnel CDC 链路（PG WAL → Iceberg + Kafka→Doris）已删除。object_state 同步改 outbox 驱动：

```
ActionService 主事务内追加 outbox (原子提交):
  INDEX   outbox → OutboxExecutor 1s 轮询 → DorisIndexStore upsert/delete  (近实时索引 ≤1s)
  ARCHIVE outbox → SyncFlushScheduler 5min 微批 → IcebergStore.merge        (主数据归档 ≤5min)
```

**不再有 SeaTunnel CDC**。SeaTunnel 退回外部数据源接入职责（ADR-014）。容灾恢复：Doris 重建走 sync_now (Iceberg→Doris upsert, 已有)；object_state 重建从 Iceberg 业务表读最新态（MERGE 覆盖了旧记录）。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) §七。

---

## 五、失败语义与可观测性

### 5.1 失败处理原则

| 场景 | 处理 |
|------|------|
| Action 参数/规则校验失败 | 返回 validation_failed，不写 PG |
| 行级 OCC 冲突（version 不匹配） | 返回 conflict，caller 刷新重试 |
| Outbox webhook 失败 | 指数退避重试，超 max_retries 进 DLQ |
| Outbox write-back 失败 | 同上 |
| Outbox INDEX 同步失败 | 指数退避重试，超 max_retries 进 DLQ；期间读降级 object_state（PG，read-your-writes） |
| SyncFlushScheduler ARCHIVE 失败 | retry_outbox_batch（指数退避）；outbox 持久化在 PG，恢复后补档 |
| object_state 查询失败 | log warning，fall through 到 Iceberg |

**Action 热路径永不阻塞**：所有异步链路（outbox/CDC）失败都不影响 "applied" 返回。

### 5.2 异常层级

| 异常 | 含义 |
|------|------|
| `ConflictError` | OCC 版本冲突（HTTP 409） |
| `ValidationError` | 参数/规则校验失败 |
| `ForbiddenError` | 写权限拒绝 |
| `OutboxError` | outbox 执行失败（含 outbox_id + error） |

### 5.3 可观测性

- OutboxExecutor：`_log.info` 启动/消费，`_log.warning` DLQ，`_log.info` 重试（含 INDEX 分支）
- SyncFlushScheduler：`_log.info` flush 批次（ontology + 行数 + MERGE 结果），`_log.warning` claim/merge 失败
- ObjectQueryService：复用 `object_query_index_hit_total`（read-your-writes 命中也计 hit）

### 5.4 SLO

| 指标 | 目标 |
|------|------|
| Action execute 延迟（同步） | < 100ms（PG 事务） |
| Read-your-writes 可见性 | 立即（同一事务） |
| Outbox INDEX→Doris 延迟 | ≤1s（poll_interval） |
| SyncFlushScheduler ARCHIVE→Iceberg 延迟 | ≤5min（微批双触发） |

---

## 六、前端契约

### 6.1 API

- `POST /actions/execute/{ontology}/{object_type}/{action}` — 执行
- `POST /actions/definitions/{ontology}/{action_type}` — 定义
- `GET /{ontology}/action-types` — 列举

### 6.2 前端组件

- `ExecuteActionDialog`：参数表单 + 执行 + 结果展示（applied/conflict/validation_failed）
- `ActionsOverview`：动作总览 + 执行入口（③ 能力赋予）
- 类型：`ActionTypeRecord` / `ActionParameterDef` / `ActionExecutionRequest` / `ActionExecutionResult`

### 6.3 Read-your-writes 体验

执行后返回 applied，用户刷新对象详情/列表立即看到变更（object_state 兜底），无需等 CDC。

---

## 七、测试标准

### 7.1 测试矩阵

| 层级 | 文件 | 覆盖 |
|------|------|------|
| 单测 | test_action_service.py | execute 热路径各分支 |
| 单测 | test_outbox_executor.py | webhook/retry/DLQ/INDEX 分支 |
| 单测 | test_outbox_write_back.py | WRITE_BACK 路径 + postgres/mysql 执行 + 反馈环标记 |
| 单测 | test_action_sync_outbox.py | INDEX/ARCHIVE outbox 生成 + target_ontology + 原子提交 |
| 单测 | test_sync_flush_scheduler.py | 微批双触发 + 按 ontology 分桶 + FOR UPDATE SKIP LOCKED + cleanup |
| 单测 | test_iceberg_store.py | merge (upsert/delete) + 幂等 |
| 单测 | test_read_your_writes.py | 点查/filter/复合 filter 跳过/投影 |
| 集成 | test_action_routes.py | HTTP 端点 |
| Live | （旧 `verify_action_loop_live.py` 已删） | 冒烟由 commit 73b1c7f 完成 |

### 7.2 Live 验证要点（冒烟由 commit 73b1c7f 完成）

1. execute action → status=applied + object_state 写入 + INDEX/ARCHIVE outbox 追加
2. read-your-writes 点查立即可见
3. read-your-writes filter 查询立即可见
4. object_state PG 直查验证
5. OutboxExecutor 消费 webhook outbox → COMPLETED
6. 后台 OutboxExecutor（lifespan）真实运行验证

---

## 八、实现状态（2026-07-08，outbox 驱动重构后）

### 冒烟验证（commit 73b1c7f）

1. ✅ execute action → applied + object_state 写入 + INDEX/ARCHIVE outbox 追加
2. ✅ read-your-writes 点查立即可见
3. ✅ OutboxExecutor 消费 INDEX outbox → Doris upsert COMPLETED（1s 内）
4. ✅ SyncFlushScheduler 消费 ARCHIVE outbox → Iceberg MERGE INTO COMPLETED（微批）
5. ✅ OutboxExecutor 消费 webhook outbox → COMPLETED
6. ✅ lifespan 后台任务真实运行（OutboxExecutor + SyncFlushScheduler + ConflictDetector + IcebergMaintenanceService）

### 组件状态

| 组件 | 状态 | 说明 |
|------|------|------|
| ActionService.execute_action | ✅ | 热路径完整，主事务内追加 INDEX/ARCHIVE outbox |
| OutboxExecutor | ✅ | webhook + write-back + INDEX→Doris + retry/DLQ，lifespan 启动 |
| SyncFlushScheduler | ✅ | ARCHIVE→Iceberg 微批 + outbox 清理，lifespan 启动 |
| WriteBackManager | ✅ | UPSERT/MERGE SQL + 反馈环标记，被 OutboxExecutor 调用 |
| ~~ActionSyncService~~ | ⚫ 已删 | 2026-07-08 去 SeaTunnel 化 |
| IcebergMaintenanceService | ✅ | 路径 A 小文件治理，lifespan 启动 |
| ObjectQueryService read-your-writes | ✅ | object_state 兜底（点查 + filter） |
| ConflictDetector | ✅ | 2026-07-10 重构：审计目标改为 Doris（存在性检测 INDEX outbox 漏写），删除 placeholder audit_snapshot_diff，lifespan 启动 run_audit_loop |
| ~~IndexSyncScheduler~~ | ⚫ 已删 | 2026-07-10 删除（周期轮询全量 OT 不合理，外部接入数据改方案 A 事件驱动 sync_now） |
| 前端 ExecuteActionDialog | ✅ | 参数表单 + 执行 + 结果展示 |
| 前端 ActionsOverview 执行入口 | ✅ | 执行按钮 + Dialog |

### 真实环境验证中发现的 bug（已修复）

| Bug | 修复 |
|-----|------|
| ActionValidator 拒绝 `mutations` 系统参数 | 加入 `_SYSTEM_PARAMS` |
| 存量 object_types `storage_type='PHYSICAL'` 与新 schema MANAGED 冲突 | 迁移到 MANAGED |

### P0 outbox 驱动重构（2026-07-08, 去 SeaTunnel 化, 冒烟通过）

**背景**：原 SeaTunnel CDC 链路（PG→Kafka→Doris 路径 B + object_state→Iceberg）存在三个问题：(1) `ensure_cdc_pipelines` 是孤儿无调用方，链路名存实亡；(2) object_state 变更**完全没有落 Iceberg**，容灾恢复数据丢失；(3) per-type 常驻 SeaTunnel job 规模化爆炸（500-10000 ObjectType）。object_state 是 Gaia 自管表，用 PG-CDC+Kafka+SeaTunnel 同步是杀鸡用牛刀，outbox 模式已是事务安全替代。

**新架构**（详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)）：
- ✅ outbox 表复用 + effect_type 隔离：新增 INDEX(→Doris) + ARCHIVE(→Iceberg) 两种 effect，与历史 WEBHOOK/WRITE_BACK 等共表
- ✅ `ActionService._create_sync_outbox_records`：每个 CREATE/UPDATE/DELETE mutation 在主事务内追加 INDEX+ARCHIVE 两条 outbox（原子提交）
- ✅ `OutboxExecutor` INDEX 分支：CREATE/UPDATE→upsert / DELETE→delete_by_ids，近实时 ≤1s
- ✅ `SyncFlushScheduler`：run_flush_loop（60s tick，按 ontology 分桶，双触发 1000条/5min，FOR UPDATE SKIP LOCKED）+ run_cleanup_loop（1h 清理 7 天前）
- ✅ `IcebergStore.merge`：Trino MERGE INTO 按业务 PK backing_column 覆盖旧记录（Trino INSERT 不去重，必须 MERGE）
- ✅ `property_mapping.py`：object_state.properties 以 backing_column 为 key（三层物理列对齐）
- ✅ Alembic migrations：timezone 列修复 + object_state.ontology_api_name + properties keys backfill + outbox.target_ontology

### 去 SeaTunnel 化变更清单（已完成）

- ✅ 删除 `ActionSyncService` + `create_pg_to_kafka_pipeline`/`create_kafka_to_doris_pipeline`/`create_dual_sink_pipeline` + 对应模板 + TableSchema* 类
- ✅ 删除注释禁用的 run_backfill_loop
- ✅ 删除 scripts/verify_action_loop_live.py + scripts/verify_action_cdc_live.py
- [x] ~~保留 `create_action_cdc_pipeline`~~ — **2026-07-10 删除**（无调用方，审计日志 PG append-only 已足够）
- [x] ~~保留路径 A PIPELINE_INDEX_BACKFILL 模板~~ — **2026-07 T1.10 删除**（去 SeaTunnel 化）。外部接入数据的 Iceberg→Doris 同步改由 ObjectIndexFunnel 承担，不再走 SeaTunnel

> **历史背景**（已被取代，仅作记录）：2026-07-06 曾修正 SeaTunnel PG-CDC 模板字段名（Postgres-CDC + url + decoding.plugin.name=pgoutput + 三段式 table-names + 独立 replication slot + primary-keys/upsert-mode 规避 #10747）并修 timestamptz blocker（migration 0e2239a90155）。但该链路 ensure_cdc_pipelines 孤儿无调用方 + per-type 常驻 job 规模化爆炸，故 2026-07-08 整体废弃改 outbox 驱动。详见 docs/bugfix/seatunnel-pg-cdc-timestamptz-blocker.md + docs/bugfix/path-b-kafka-doris-schema-mismatch.md

### 已完成收尾（2026-07-10）

- [x] ConflictDetector 审计目标改为 Doris（存在性检测，检测 INDEX outbox 漏写），删除 placeholder `audit_snapshot_diff`
- [x] `create_action_cdc_pipeline`（审计日志→Iceberg）删除（无调用方，无合规需求）
- [x] IndexSyncScheduler 删除（周期轮询全量 OT 不合理，外部接入数据改方案 A：provision/sync_now 事件驱动触发 Doris 同步）

### P1 补全（ADR-011，2026-06-22）

除上述 CDC 联调项外，Action 闭环的 P1 能力补全已完成：

- ✅ 上下文注入（`ActionContext`：currentUser/currentTimestamp/workspaceId/selectedObject）贯穿规则引擎与权限
- ✅ CDL 变更前后快照（`execution_log.before_snapshot`/`after_snapshot`）
- ✅ Link mutation（RELATE/UNRELATE/CLEAR_LINKS，独立 `object_links` 表）
- ✅ 三层权限（`ActionAuthorizer`：执行权限/行级写权限/参数级权限）
- ✅ submission_criteria 接入规则引擎（从死字段到结构化 `SubmissionCriterion`）
- ✅ 副作用扩展（SUB_ACTION 链式编排 + KAFKA_TOPIC 事件流）
- ✅ ActionType 版本管控（`action_type_versions` 历史快照 + rollback）
- ✅ preview dry-run（OMA 调试面板后端）
- ✅ `object_state.modified_by` 审计字段
- ✅ 前端：类型化参数控件 + onApplied 回调 + ObjectDetailPanel 执行入口 + Vitest 测试体系

详见 [adr-011-action-p1.md](./adr-011-action-p1.md)。

### P2 Batch Action 分片调度（2026-07-06）

ADR-011 路标中的 P2 Batch Action 已落地:

- ✅ `execute_batch_action` 方法: 将同一 ActionType 应用到大批目标对象，分片调度（shard_size 默100/最大1000/项上限10000）
- ✅ 逐项原子事务: 每个 item 独立 PG 事务 + 独立 idempotency_key + 独立 execution_log（单项 OCC 冲突/校验失败不中断整批→status=`partial`）
- ✅ `fail_fast` 选项: True 时遇首项失败即中止（已提交的前缀不可回滚，调用方需对账）
- ✅ 派生逐项幂等键: `batch_key#index`（未显式提供时），整批安全重跑
- ✅ 共享 `default_parameters` 合并（item 参数胜出）+ `rid`/`expected_version` 注入
- ✅ `POST /actions/execute-batch/{ontology}/{object_type}/{action}` 路由
- ✅ schema: `BatchActionRequest`/`BatchActionResult`/`BatchItemResult` + 常量 `BATCH_DEFAULT_SHARD_SIZE=100`/`BATCH_MAX_SHARD_SIZE=1000`/`BATCH_MAX_ITEMS=10000`
- ✅ ActionType 定义期 `batch_enabled` 闸门（False 时 batch 请求被拒为 `rejected`）

未覆盖（后续 P3+）: 跨 shard 并行执行（需连接池）、Scenario 沙箱事务、Function-Backed Action。

---

## 九、与索引加速层的联动

Action 闭环的实时索引同步正是索引加速层的**阶段 5（P1）**。Action 闭环接通后：
- Action 写 object_state → 主事务内追加 INDEX outbox → OutboxExecutor 1s 轮询 → Doris upsert（近实时 ≤1s）
- Action 写 object_state → 主事务内追加 ARCHIVE outbox → SyncFlushScheduler 5min 微批 → IcebergStore.merge（主数据归档）
- 索引加速层从"批同步 30s"升级到"近实时 ≤1s"

**两个 P0 闭环在此交汇**：Action 闭环提供数据源（object_state 变更），索引加速层提供查询加速（Doris 近实时索引）。

> **2026-07-08 变更**：原"PG→Kafka→Doris (路径 B) 3-5s 实时索引"已改为 outbox INDEX effect ≤1s，更快且去 SeaTunnel 化。

---

## 九.一、路径 A/B 分工与规模化设计（2026-07-06 重构）

> **⚠️ 2026-07-08 去 SeaTunnel 化（路径 B）+ 2026-07 T1.10 去 SeaTunnel 化（路径 A）**。本节描述的路径 B (Kafka→Doris 承担 object_state 实时索引) 与路径 A (Iceberg→Doris backfill 承担外部接入同步) **均已被删除**。object_state 同步改 outbox INDEX effect → OutboxExecutor ≤1s；外部接入数据的 Iceberg→Doris 同步改 `ObjectIndexFunnel`（Python 侧直连 DorisIndexStore.upsert）。SeaTunnel 现仅承担「外部源→Iceberg」搬运。本节保留作为**历史决策记录**，其中 Iceberg 小文件治理 + IcebergMaintenanceService 的设计仍适用（外部接入数据进 Iceberg 后仍需维护）。当前真实架构以 §三.4 + §四.4 + [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) 为准。

### 问题：per-type 常驻 job 的规模化爆炸

原设计中路径 A（Iceberg→Doris）每个 ObjectType 提交一个常驻 STREAMING job tail 增量。这在规模化下不可行：

- Gaia 作为企业级本体平台（对标 Palantir Foundry），生产规模是 **10-50 本体 × 每 50-200 ObjectType = 500-10000 ObjectType**，不是 dev 环境的 67 个
- 每 ObjectType 1 个常驻 stream job → 1000-10000 个常驻 SeaTunnel job
- SeaTunnel Zeta 的 `runningJobInfoIMap` 每 job ~200KB 元数据，1000 jobs ≈ 400MB，线性增长（apache/seatunnel#10856）；dev 环境几个 job 就偶发 Hazelcast heartbeat-timeout

### 根因：Iceberg 写频率约束 + job 模式错配

两条候选路径从 object_state 到 Doris idx 表：

```
路径 A（Iceberg 中转）：object_state →[CDC]→ Iceberg →[STREAM/BATCH]→ Doris
路径 B（Kafka 中转） ：object_state →[CDC]→ Kafka  →[multi-table]→ Doris
```

关键认知：**Iceberg 是列存 parquet，commit 重（写文件 + snapshot metadata），不该频繁写**。高频 Action 写 object_state 如果直走路径 A，每个 checkpoint（10s） commit 一次 parquet，会产生海量小文件 + snapshot 膨胀，是 Iceberg 的经典性能杀手。Kafka 是内存消息流，天然支持高频写无文件压力。

### 新分工：路径 A 低频批式（0 常驻 job），路径 B 单 job multi-table（1 常驻）

| 维度 | 路径 A（Iceberg 中转） | 路径 B（Kafka 中转） |
|------|----------------------|---------------------|
| 定位 | 主数据持久化 + 历史快照 + 容灾恢复 | 低延迟实时索引（查询加速） |
| 写频率 | **低频批式**（避免 Iceberg 小文件） | 高频流式（Kafka 天然适合） |
| 延迟 | ~分钟级 | 3-5s |
| job 模式 | BATCH 一次性（provision/rebuild） + 周期性 BATCH 调度 | **1 个常驻 multi-table job** |
| 常驻 job 数 | **0** | **1**（O(1)，与 ObjectType 数无关） |

**总常驻 SeaTunnel job = 1**（路径 B），与 ObjectType 数无关。backfill 是一次性脉冲（跑完 FINISHED 退出，不占常驻槽）。

### 路径 B 为何选 SeaTunnel multi-table（方案 B1）而非 Doris Routine Load（方案 B2）

调研了 Doris 原生 Kafka 消费三条路径（Routine Load / Flink Connector / Doris Kafka Connector）：
- **Doris Routine Load** 最短路径（Doris FE 自当 consumer，一个 SQL 语句），但**一个 Routine Load job 只能写一张 Doris 表**，不支持按消息字段动态路由到不同表 → N 个 ObjectType = N 个 Routine Load job，没解决规模化的 O(N) 问题
- **SeaTunnel multi-table 特性**（官方主推，Single Job Multiple Tables）支持 `table="${target_table}"` 动态路由 + `schema_save_mode=CREATE_SCHEMA_WHEN_NOT_EXIST` 自动建表 + `sink.enable-delete` CDC 删除同步 → **1 个 job 写 N 张表，O(1)**

选 B1 的另一理由：**PG 时空数据路径后续要补充**（PostGIS/TimescaleDB 的 Kafka→超表同步也是同类多表路由场景），统一用 SeaTunnel multi-table 模式更一致。

### Iceberg maintenance（路径 A 配套硬要求）

Iceberg 批式写仍会累积小文件 + snapshot（每次 backfill commit 产生 parquet + snapshot），必须定期治理。Gravitino 内置 Iceberg REST Catalog 本身**不提供** maintenance（它是 catalog proxy，只管元数据注册），但 Gaia 已有的 **Trino 原生 Iceberg connector**（`iceberg.properties`，直连 Gravitino 9001）完整支持标准 SQL maintenance（已 live 验证）：

| 操作 | Trino 语法 | 作用 |
|------|-----------|------|
| compaction | `ALTER TABLE iceberg.ontology.<t> EXECUTE optimize(file_size_threshold => '128MB')` | 合并小 parquet 文件 |
| expire_snapshots | `ALTER TABLE ... EXECUTE expire_snapshots(retention_threshold => '7d')` | 过期旧快照，释放 S3 + 控制 metadata |
| remove_orphan_files | `ALTER TABLE ... EXECUTE remove_orphan_files(retention_threshold => '7d')` | 清理孤儿文件 |

新增 `IcebergMaintenanceService` 封装这些调用 + lifespan 后台定时调度（频率跟 backfill 节奏匹配，如每天一次）。

### 落地变更清单

**路径 A**：
1. `create_index_pipeline` 去掉 stream job 提交（只留 backfill BATCH）
2. `stop_index_pipelines`/`update_index_pipeline` 去掉 stream stop
3. ~~新增 `IndexSyncScheduler`~~ — **2026-07-10 删除**（周期轮询全量 OT 不合理；外部接入数据改方案 A：provision/sync_now 事件驱动触发 Doris 同步）
4. 新增 `IcebergMaintenanceService`（Trino ALTER TABLE EXECUTE）+ lifespan 调度

**路径 B**：
1. ~~重写 `PIPELINE_KAFKA_TO_DORIS_TEMPLATE` 为单 job multi-table 动态路由~~
2. ~~`ActionSyncService` 编排调整（kafka_to_doris 不再 per-type）~~

> **2026-07-08 路径 B 已整体废弃**：PIPELINE_KAFKA_TO_DORIS_TEMPLATE + ActionSyncService + pg_to_kafka/kafka_to_doris pipeline 全部删除。object_state 实时索引改 outbox INDEX effect。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

详见 ADR-008「修订记录（2026-07-06，第二次）」+「修订记录（2026-07-08，第三次）」。

---

## 十、关键决策记录

| 决策 | 理由 |
|------|------|
| read-your-writes 用 object_state 而非等同步 | 同步有延迟，用户执行后立即查询不应等待 |
| read-your-writes 只支持点查 + top-level eq filter | 复合 filter 难以翻译到 JSONB，留给 Iceberg/Doris |
| OutboxExecutor 后台任务用独立 session | 避免与请求 session 冲突 |
| object_state 同步去 SeaTunnel 化, 改 outbox 驱动 (2026-07-08) | object_state 是自管表, 用 PG-CDC+Kafka+SeaTunnel 同步是杀鸡用牛刀; outbox 模式已是事务安全 CDC 替代; per-type 常驻 SeaTunnel job 规模化爆炸 |
| INDEX(→Doris) 用 OutboxExecutor 1s 轮询, ARCHIVE(→Iceberg) 用 SyncFlushScheduler 5min 微批 | Doris 在线读主源须近实时; Iceberg 小文件敏感须控 commit 频率; 两条路径延迟需求不同, 调度机制分离 |
| Iceberg 归档用 MERGE INTO 而非 INSERT | Trino INSERT 不去重 (即使 v2 upsert 表), 必须用 MERGE INTO 按业务 PK backing_column 覆盖 |
| write-back 用 asyncpg/aiomysql 直连而非 SeaTunnel | 一次性写回，直连更简单可靠；SeaTunnel 留给外部数据源接入 (ADR-014) |
| ConflictDetector 事后审计 | 不阻塞写入，作为后台定时任务 |
