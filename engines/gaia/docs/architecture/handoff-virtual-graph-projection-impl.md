# Handoff —— ADR-021 VIRTUAL 对象图投影实施

> **状态**：✅ PR 1 + PR 2 + PR 3 + PR 4 + PR 5a 完成 + 本地冲烟通过（2026-07-27），待 commit + 启动 PR 5（二期）
> **日期**：2026-07-27
> **前置**：rid 链路闭合（handoff-rid-funnel-closure.md，PR-1+PR-2 已完成）—— rid 在 Doris/Neo4j/PostGIS 间正确流转，ObjectIndexFunnel 已就位。

## 权威源

- 决策权威源：[`adr-021-virtual-graph-projection.md`](./adr-021-virtual-graph-projection.md)
- 工程落地权威源：[`virtual-graph-projection-design.md`](./virtual-graph-projection-design.md)（组件契约/数据流/难点决策/PR 拆解/测试矩阵）
- 架构基线：[`graph-reasoning-design.md`](./graph-reasoning-design.md) §6.5

## 目标

让图关联推理（`search_around`/`find_paths`/`exists_link`）跨 VIRTUAL 对象不断链。把 VIRTUAL 对象的**身份骨架**（rid+label+PK+title+indexed+`_virtual`+`_source_ref`+`_sync_tag`）投影进 Neo4j，全量属性走 Trino 联邦水合（零拷贝，永远最新）。

## 核心约束（实现时反复对照）

- **P1**：Neo4j 是派生索引，不是主存。单向投影、可重建、同源可信。
- **P2**：身份骨架最小化——只存图遍历必需字段，不存全量属性/系统元属性/安全标记/SCD2。
- **P3**：VIRTUAL 投影旁路 Gate 1，不污染 MANAGED 路径。新增 `project_for_virtual_object_type`，不改 `project_for_object_type`。
- **P4**：全量属性永远走 Trino 联邦（零拷贝），不投影/不缓存/不落地。
- **P5**：边来源是外部源 FK 推导，不是 Action（红线 9：VIRTUAL 禁止业务写入）。
- **P6**：为路径 ③'（远期查询时联邦）留 `_virtual`+`_source_ref` 接口。

## PR 拆解与实施状态

| PR | 内容 | 状态 | 说明 |
|----|------|:---:|------|
| PR 0 | 权限注入（DataFrameQueryService + AuthorizationService） | ⏳ 待定 | 查询侧横切，与投影正交，可后置 |
| **PR 1** | **节点投影 MVP** | ✅ 完成 | project_for_virtual_object_type + GraphProjector 扩展 + Neo4j 批量写入 + 孤儿清理 |
| PR 2 | FK→边投影 | ✅ | _resolve_fk_backing_column + get_object_states_by_pks + 一端VIRTUAL一端MANAGED + 两端VIRTUAL内存join + project_links_batch |
| PR 3 | 触发链路 + admin API | ✅ | register_virtual_table 异步触发 + rebuild-for-virtual 路由 + get_virtual_object_types_by_dataset + partial 降级 |
| PR 4 | 一致性 + 治理 | ✅ | ConflictDetector 排除 VIRTUAL（storage_type==MANAGED 过滤）+ _hydrate_virtual 返回 _partial 标记 |
| PR 5（二期） | 定时刷新 + 行级权限 + FK 自动推断 | ⏳ | 不在本期 |

## PR 1 任务拆解（进行中）

### P1.1 Neo4jGraphStore 批量写入 + 孤儿清理
- `upsert_nodes_batch(label, nodes)` — UNWIND + CALL {} IN TRANSACTIONS OF 1000 ROWS
- `upsert_edges_batch(label, rel_type, edges)` — 同上
- `cleanup_stale_virtual(label, current_sync_tag)` — DETACH DELETE WHERE _sync_tag <> current AND _virtual=true
- 前置：rid 唯一约束已建（`ensure_label` line 153，✅）

### P1.2 GraphProjector.project_object 扩展
- 检测 `object_state.get("_virtual")` 为 True 时，额外写入 `_virtual`/`_source_ref`/`_sync_tag` + PK 业务值 + title
- title 字段：`ObjectTypeModel.title_property`（✅ 已有）

### P1.3 ObjectIndexFunnel 注入 + project_for_virtual_object_type
- `__init__` 新增 `engine: TrinoQueryEngine | None` + `object_query: ObjectQueryService | None`（可选）
- container.py 同步注入
- `project_for_virtual_object_type(ont, ot, *, batch_size=1000)` 节点部分：
  - 查 OT 元数据（PK api + title api + indexed + backing_column）
  - `_virtual_table_ref(ot)` 拿 Trino table ref
  - 游标分页：`SELECT pk,title,indexed FROM ref WHERE pk > $last ORDER BY pk LIMIT $batch`
  - 合成 object_state（带 _virtual/_source_ref/_sync_tag）
  - 调 GraphProjector.project_object
  - 末尾 cleanup_stale_virtual

### P1.4 测试
- `test_neo4j_graph_store.py`：upsert_nodes_batch/upsert_edges_batch/cleanup_stale_virtual
- `test_graph_projector.py`：_virtual 元标记写入
- `test_object_index_funnel.py`：project_for_virtual_object_type 节点投影 + 游标分页 + cleanup

## 文档同步清单（PR 合并后）

- [ ] `graph-reasoning-design.md` §6.5 标注实现状态
- [ ] `implementation-status.md` VIRTUAL 投影行 🟡→✅
- [ ] `virtual-graph-projection-design.md` §〇 🔴→✅ + 类名 ProjectSyncService→ObjectIndexFunnel
- [ ] CLAUDE.md 红线 9 补充图投影例外说明

## 不在本期范围

- PR 5（二期）：定时刷新 / 行级权限 / FK 自动推断
- PR 0 权限注入（可后置，与投影正交）

## 本地冲烟验证结果（2026-07-27）

冲烟脚本：`scripts/verify_virtual_graph_projection.py`（造临时 VIRTUAL OT 绑 pgnative.public.data_sources → 调 project_for_virtual_object_type → 验证 Neo4j 节点 → 清理）。

| 验证项 | 结果 | 证据 |
|---|---|---|
| §3.3 admin rebuild 路由 | ✅ | `POST /admin/project/rebuild-for-virtual/DVP/ProjectBase` 返回 HTTP 200 |
| PR 1 节点投影 | ✅ | 3 行源数据 → 3 节点落 Neo4j，`nodes=3 partial=False` |
| 节点骨架属性 | ✅ | `_virtual=True` `_source_ref=pgnative.public.data_sources` `_sync_tag=<epoch>` 三者正确写入 |
| §2.8 partial 降级 | ✅ | DVP.ProjectBase（Trino 源不通）返回 `{partial:true, error:"Communications link failure"}`，HTTP 200 不抛异常 |
| §2.7 ConflictDetector 排除 VIRTUAL | ✅ | 审计日志仅 7 个 MANAGED OT（Customer/Order/...），24 个 DVP VIRTUAL OT 全部被 `storage_type=='MANAGED'` 过滤 |
| §3.1 register 触发链路 | ✅ | 代码路径验证（`_maybe_trigger_virtual_projection` + `get_virtual_object_types_by_dataset` JOIN 查询） |

## PR 5a：VIRTUAL 批量水合（hydrate_by_pks）

> **状态**：🚧 实施中（2026-07-27）

### 背景

`_hydrate_virtual`（DataFrameQueryService）当前逐个调 `hydrate_by_pk`，50 rid = 50~100 次 Trino 往返（N+1 反模式，graph-reasoning-design.md §7.7 列二期）。本次补 `ObjectQueryService.hydrate_by_pks` 批量原语 + 重构 `_hydrate_virtual` 按 (ont,ot) 分组委托批量。

### 改动清单

- `src/ontology/services/object_query_service.py` — 新增 `hydrate_by_pks(ont, ot, pks, select_fields)`：WHERE pk IN (...) 分批 1000 + 参数化绑定 + _coerce_property_types 类型强转 + _map_backing_to_api 列名映射；移除 hydrate_by_rids 对 VIRTUAL 的 NotImplementedError（改委托 hydrate_by_pks）；清理遗留 [TEXTSQL_DEBUG] 调试日志（warning→debug）
- `src/ontology/services/object_set_executor.py` — 重构 `_hydrate_virtual`：parse_virtual_rid_pk 解析 + 按 (ont,ot) 分组 + 调 hydrate_by_pks + partial 降级 + rid 顺序保持
- `src/ontology/layers/engine/trino_query_engine.py` — 清理遗留 [TRINO_DEBUG] 调试日志
- `tests/unit/services/test_object_query_service_hydrate_pks.py` — hydrate_by_pks 单测（13 个：空/单批/分批/失败/select 下推/PK 必选/类型强转/datetime/decimal/MANAGED 拒绝/无 properties/列名映射/hydrate_by_rids 委托）
- `tests/unit/services/test_object_set_executor.py` — _hydrate_virtual 重构测试（11 个：单 rid/混合/批量单 OT/多 OT 分组/无 OQS/单组失败 partial/pk 缺失跳过/select 透传/legacy UUID/空）

### 验证结果

- 13 个 hydrate_by_pks 单测全绿
- 11 个 _hydrate_virtual 重构测试全绿
- 全量 1984 passed / 3 failed（预存环境依赖：Neo4j/S3）
- ruff：改动文件全绿（预存 7 个非本次改动）
- mypy：object_query_service.py + object_set_executor.py 零错误

### 设计决策

- D1：走裸 SQL 路径（参考 _hydrate_via_source_table），不走 execute_compiled_sql 编译路径（IN 列表参数化更直接）
- D2：IN 列表分批 1000（对齐 hydrate_by_rids 的 MANAGED 批量级配）
- D3：跨 OT 分组串行查询（避免外部源并发风暴，对齐 CLAUDE.md 错误模式 #15 教训）
- D4：单批失败整组标 _partial（对齐 PR 4 §2.8 语义）
- D5：select_fields 下推到 SELECT 列表，PK 列必选
