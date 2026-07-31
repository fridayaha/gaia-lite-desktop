# 交接文档：rid 链路闭合 + ObjectIndexFunnel + searchAround LIMIT 收敛

> **文档性质**：交接文档（handoff），供开发人员执行
> **创建日期**：2026-07-27
> **决策来源**：graph-reasoning-design.md 落地前疑问澄清（2026-07-27 评审）
> **状态**：✅ PR-1 + PR-2 实施完成（2026-07-27），待本地冒烟 + 合入
> **关联文档**：
> - [graph-reasoning-design.md](graph-reasoning-design.md)（特性设计权威源，§2.1 阻塞项 / §4.5 searchAround / §6 ObjectIndexFunnel / §8.2 防线）
> - [handoff-rid-migration.md](handoff-rid-migration.md)（rid 命名统一，已完成，本任务在其之上补 rid 落库）

---

## 一、背景与问题

graph-reasoning-design.md 落地前评审发现 4 个阻塞项 + 1 个语义未收敛：

1. **Doris idx 表无 rid 列**：rid 是 Neo4j/PostGIS 的节点主键，但 Doris idx 表（object database）没有 rid 列——Action 路径 outbox INDEX 分支 `upsert([props])` 时 props 不含 rid，rid 没写进 Doris。文档 §4.4 声明"rid 权威源是 Doris idx 表"但代码未落地。
2. **ProjectSyncService（外部接入路径）rid 缺失**：传给 projector 的是 `{"id": obj_id}`（业务主键值），而 `GraphProjector.project_object` 读 `object_state["rid"]`——KeyError/None bug。且 ProjectSyncService 不写 Doris，外部接入的对象在 Doris 里没 rid。
3. **ObjectQueryService 无 hydrate_by_rids**：推理线水合只能逐个 `hydrate_by_pk`（N+1 反模式），无按 rid 批量查 Doris 的方法。
4. **searchAround LIMIT 语义未收敛**：文档 §4.5 自标"待对齐"——文档要求 LIMIT 作用"去重终点 rid 数"，代码实现是 `WITH DISTINCT start, m LIMIT`（保留 start 给前端画箭头），多起点时 LIMIT 被起点数稀释，且 rids 列表含重复 m。
5. **图遍历阈值 1000 万过大**：MVP 风险控制，下调到 100 万。

**额外纳入本次**：废弃 IndexSyncService 的 SeaTunnel backfill 路径（`PIPELINE_INDEX_BACKFILL_TEMPLATE` + `PIPELINE_INDEX_STREAM_TEMPLATE` + `backfill()` + `sync_now()` + `create/update/stop_index_pipeline`），Doris 写入统一归 ObjectIndexFunnel。文档 §6.2 已声明废弃，本次落地。

---

## 二、已确认的决策（2026-07-27 评审）

| # | 决策 | 说明 |
|---|------|------|
| D1 | rid 链路闭合拆 PR-1（功能）+ PR-2（纯重命名），分开合入 | PR-1 不改类名，先把 rid 跑通；PR-2 纯 rename |
| D2 | 存量 rid 回填走 Neo4j 优先 | rid 权威最终是 Doris，但首次回填时 Neo4j 是唯一可信 rid 来源（Action 路径已正确分配）。Neo4j 也没有的新分配 |
| D3 | searchAround LIMIT 收敛走"方向 B 精细化" | LIMIT 作用"去重 (start,m) 边对数"；rids 去重保序；matched_count 语义改为边对数。一次 Cypher 查询零额外往返 |
| D4 | 图遍历阈值 1000 万 → 100 万 | settings + metadata 默认值 + 文档多处 |
| D5 | GraphProjector 强制投影 PK 值 | 不管 indexed 与否，Neo4j 节点总有业务 PK 可反查（D2 存量回填依赖 + 调试/未来功能受益） |
| D6 | IndexSyncService 废弃 SeaTunnel backfill 纳入本次 | ProjectSyncService 写 Doris 成唯一路径，backfill 同步废弃，无重叠。这回答了 T1.4 疑问 |
| D7 | 防线二拆两行 | 探索边对数 ≤ 100 万（LIMIT 截断）/ 下游去重 rid 集 ≤ 100 万（自然 ≤ 边对数） |

---

## 三、PR-1：rid 链路闭合 + searchAround LIMIT 收敛 + 阈值下调 + backfill 废弃

**目标**：rid 在 Doris/Neo4j/PostGIS 间正确流转；searchAround LIMIT 语义收敛；阈值降 100 万；废弃 SeaTunnel backfill。**不改类名**（类名重命名归 PR-2）。

### T1.1 Doris idx 表加 rid 列
- **文件**：`src/ontology/layers/index/doris_index_store.py`
- **改动**：`create_index_table` 在用户 fields 之外自动注入系统列 `rid`（`VARCHAR(128)` + INVERTED 索引，不进 UNIQUE KEY）
- **要点**：rid 是系统注入列，不来自 IndexFieldExtractor；UNIQUE KEY 仍只用业务 PK
- **迁移**：Alembic migration——存量 idx 表 `ALTER TABLE ... ADD COLUMN rid VARCHAR(128)`（rid 暂空，由 T1.5 回填）

### T1.2 DorisIndexStore 新增 get_rid_by_pk
- **文件**：`src/ontology/layers/index/doris_index_store.py`
- **改动**：`async def get_rid_by_pk(self, ont, ot, pk_column, pk_value) -> str | None`（单条）+ 批量版 `get_rids_by_pks(self, ont, ot, pk_column, pk_values) -> dict[pk, rid]`

### T1.3 outbox INDEX 分支注入 rid 到 Doris upsert
- **文件**：`src/ontology/services/outbox_executor.py`
- **改动**：`_sync_index_to_doris` CREATE/UPDATE 分支，调 upsert 前注入 `props["rid"] = payload["rid"]`
- **DELETE 分支**：无需改

### T1.4 ProjectSyncService（外部接入路径）修 rid 分配 + 写 Doris
- **文件**：`src/ontology/services/project_sync_service.py` + `src/ontology/config/container.py`
- **改动**：
  - `__init__` 新增 `index_store: DorisIndexStore` 参数
  - `project_for_object_type` 循环内：`get_rid_by_pk` 复用 or `generate_object_rid()` 新分配 → Doris upsert（注入 rid）→ projector 用 `{"rid": rid, "properties": row}`
  - container `project_sync_service` property 注入 `self.index`
- **修复 bug**：当前传 `{"id": obj_id}` 导致 projector KeyError

### T1.5 存量 rid 回填（Neo4j 优先，一次性迁移）
- **文件**：新增 `scripts/backfill_rids_to_doris.py`
- **逻辑**：遍历 MANAGED ObjectType → Doris `WHERE rid IS NULL` → Neo4j 按 PK 反查 rid（依赖 D5）→ 有则复用、无则新分配 + 同步 Neo4j → Doris UPDATE
- **依赖**：T1.1（rid 列）+ T1.2（get_rid_by_pk）+ D5（Neo4j 节点有 PK）

### T1.6 ObjectQueryService 新增 hydrate_by_rids
- **文件**：`src/ontology/services/object_query_service.py`
- **改动**：`async def hydrate_by_rids(self, ontology, rids, ot) -> list[dict]`——MANAGED 走 Doris `WHERE rid IN (...)` 分批 1000；VIRTUAL 留二期（NotImplementedError 或简单实现）
- **DataFrameQueryService 水合点切换**：`object_set_executor.py` execute() 末尾切到 hydrate_by_rids

### T1.7 searchAround LIMIT 收敛（方向 B 精细化，D3）
- **文件**：`src/ontology/layers/graph/neo4j_graph_store.py`
- **改动**：
  - Cypher 不变（保留 `WITH DISTINCT start, m LIMIT $limit`，LIMIT 作用边对数）
  - rids 去重保序（`dict.fromkeys`）
  - `matched_count` 改为 `len(edge_pairs)`（边对数）
  - `truncated` 基于边对数判定
- **下游**：`object_set_executor.py` 两处 `evidence.record(..., result.matched_count)` 字段名不变，语义自动跟随

### T1.8 GraphProjector 强制投影 PK（D5）
- **文件**：`src/ontology/services/graph_projector.py`
- **改动**：`project_object` 在 indexed 属性循环之外，强制投影 `pk_value`（`props["pk_value"] = flat.get(ot.primary_key)` 或 backing_column 对应值）
- **Neo4j 节点属性**：`rid + api_name + pk_value + indexed属性 + visibility`

### T1.9 图遍历阈值 1000 万 → 100 万（D4）
- **文件**：
  - `src/ontology/config/settings.py:158`：`graph_traversal_result_limit: int = 1_000_000`
  - `src/ontology/layers/metadata/postgres_meta_store.py` 两处默认值 `10_000_000` → `1_000_000`
  - `src/ontology/layers/graph/neo4j_graph_store.py` 注释（2 处）

### T1.10 废弃 SeaTunnel backfill（D6）
- **文件**：`src/ontology/services/index_sync_service.py` + `src/ontology/layers/pipeline/sea_tunnel_engine.py`
- **改动**：
  - IndexSyncService 删除 `backfill()` + `sync_now()` 方法（无外部调用方，已核查）
  - IndexSyncService `provision`/`rebuild` 删除 `create_index_pipeline`/`update_index_pipeline` 调用（只保留 Doris DDL 建表）
  - IndexSyncService `deprovision` 删除 `stop_index_pipelines` 调用（只保留 drop table）
  - SeaTunnelEngine 删除 `PIPELINE_INDEX_BACKFILL_TEMPLATE` + `PIPELINE_INDEX_STREAM_TEMPLATE` + `create_index_pipeline`/`update_index_pipeline`/`stop_index_pipelines` 方法
  - IndexSyncService 注入去掉 `pipeline`（SeaTunnelEngine）依赖
- **职责收窄**：IndexSyncService 只管 Doris 表 schema（provision/rebuild/deprovision 建表删表），数据同步归 ObjectIndexFunnel（PR-2 后的类名）

### T1.11 文档同步
- **文件**：`docs/architecture/graph-reasoning-design.md`
  - §4.5 "待对齐"段落 → "已收敛（方向 B 精细化）"
  - §8.2 防线二拆两行（D7）+ 阈值 100 万
  - §2.1 阻塞项标记完成状态（①②③ 完成，④ TimescaleDB rid 列留独立任务——时序链路推后）
  - §6.2 "废弃路径"标记"已落地"
  - 阈值 1000 万 → 100 万（多处）

### T1.12 测试补充
- `doris_index_store`：rid 列注入、get_rid_by_pk 复用
- `outbox_executor`：INDEX 分支 props 含 rid
- `project_sync_service`：rid 分配/复用、projector 收到正确 rid（回归 KeyError bug）
- `object_query_service`：hydrate_by_rids 批量、分批
- `neo4j_graph_store`：search_around 多 start 命中同 m 的去重 + matched_count 语义
- `graph_projector`：强制投影 pk_value
- `index_sync_service`：删除 backfill/sync_now 后 provision/rebuild/deprovision 只建表
- 真实 DB（testcontainers）：rid 列 ALTER 迁移

---

## 四、PR-2：ProjectSyncService → ObjectIndexFunnel 重命名（纯重构）

**前置**：PR-1 合入后。零功能改动，纯 rename。

### T2.1 类与文件重命名
- `src/ontology/services/project_sync_service.py` → `object_index_funnel.py`
- `class ProjectSyncService` → `class ObjectIndexFunnel`
- docstring 更新为 ObjectIndexFunnel 职责（统一索引编排 + rid 分配 + 四引擎扇出）

### T2.2 DI 容器改名
- `src/ontology/config/container.py`：import + property `project_sync_service` → `object_index_funnel` + override key

### T2.3 路由调用点改名
- `src/ontology/routes/admin.py`：2 处 `container.project_sync_service` → `container.object_index_funnel`

### T2.4 测试改名
- `grep -rl "project_sync_service\|ProjectSyncService" tests/` 全部替换

### T2.5 文档同步
- `graph-reasoning-design.md` §6.1/§6.3 "升级重命名" → "已完成"
- `implementation-status.md` Service 清单表更新

---

## 五、不在本期范围（独立任务跟踪）

| 项 | 原因 |
|---|---|
| TimescaleDB 超表加 rid 列（阻塞项④） | 时序链路整体推后（边界待定） |
| ObjectIndexFunnel 增量消费 Iceberg snapshot（模式 C） | 依赖时序链路落地 |
| VIRTUAL 批量水合 hydrate_by_pks | 文档 §7.7 标二期，本 PR 只做 MANAGED |
| 性能压测 | 待阻塞项完成后单独排期 |

---

## 六、实施顺序（依赖图）

```
T1.1 (rid 列) ──┬── T1.2 (get_rid_by_pk) ── T1.4 (ProjectSync 修 rid) ──┐
                │                                                        ├── T1.5 (存量回填)
                └── T1.3 (outbox 注入 rid)                              │
                                                                         │
T1.8 (GraphProjector 强制 PK) ─────────────────────────────────────────┘
                                                                         │
T1.6 (hydrate_by_rids) ── T1.7 (searchAround 收敛) ── T1.9 (阈值)      │
                                  │                                      │
                            T1.10 (废弃 backfill)                       │
                                  │                                      │
                            T1.11 (文档) ── T1.12 (测试) ──────────────┘
                                                       │
                                                  PR-1 完成
                                                       │
                                                  T2.x (重命名) ── PR-2 完成
```

无强依赖的可并行：T1.6/T1.7/T1.8/T1.9/T1.10 互相独立，T1.1-T1.5 是 rid 链路主线。
