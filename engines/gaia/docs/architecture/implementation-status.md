# 实现状态路标 (Implementation Status)

> **用途**:本文档记录各架构组件的"已实现 / 开发中 / 待开发"状态,作为继续开发的参考路标。
> 评审日期:2026-06-18（§一~§九）/ 2026-07-06（§十二 图关联推理整章新增 + 全文数字校准）
> 关联文档:[architecture_plan.md](./architecture_plan.md) · [action-architecture.md](./action-architecture.md) · [data-layer-design.md](../design/data-layer-design.md) · [graph-reasoning-design.md](./graph-reasoning-design.md) · [graph-reasoning-progress.md](./graph-reasoning-progress.md)
>
> **⚠️ 文档同步约定**:本文件是组件实现状态的**唯一真相源**。新增 Layer / Service / Route / 工具 / 重大特性时,必须同步更新本文件对应章节(§一~§四 + 相关特性章),并在文末「十二、重大特性增量」追加。`architecture_overview.md` 与 `CLAUDE.md` 的服务清单/分层图/数字仅作概览,以本文件为准。
>
> **状态图例**:
> - ✅ **已完成** - 代码实现 + 已接入主流程 + 有测试
> - 🟡 **已实现未接线** - 代码写好了,但没接入 container/route/后台任务,功能不可用
> - 🔴 **待开发** - 仅有设计或模板,无业务逻辑
> - ⚫ **未规划** - 设计文档未涉及

---

## 一、分层架构 (8 Layer)

> 2026-07 新增 **Graph Layer**（Neo4j，ADR-015 图关联推理）+ **GeoTime Layer**（PostGIS + TimescaleDB，时空多维分析），原 6 层扩展为 8 层。详见 §十二。

| Layer | 组件 | 状态 | 说明 |
|-------|------|------|------|
| Metadata | PostgresMetaStore | ✅ | 完整 CRUD + datasource + object_state + outbox + interface 关联表（§十二 Interface 查询） |
| Catalog | GravitinoRegistry | ✅ | 物理资产注册、View、RBAC、access check + JDBC/Lakehouse/Kafka/Fileset catalog 注册（ADR-014） |
| Dataset | IcebergStore | ✅ | load_by_ids、append、get_snapshots、**scan_latest**（sync_now 读取路径，ADR-008） |
| Index | DorisIndexStore | ✅ | 在线读主源：连接池 + create_index_table(含 STORED_ONLY 全量列)/upsert/delete_by_ids/load_by_ids/table_exists + IndexSyncService 编排接通 (ADR-001 修订 2026-06-25) + 语义表 create_semantic_table/vector_search/execute_sql（ADR-012 IVF ANN）。2026-07-13 删除遗留非参数化 query/load_by_filter/aggregate（无生产调用方，见 ICD-04 v1.1） |
| Pipeline | SeaTunnelEngine | ✅ | pipeline 创建方法完整（main/index_sync/cdc/file_sync/kafka_ingestion/external_cdc/kafka_timeseries 等），main/file_sync/kafka_ingestion/external_cdc/index_sync 已 live 验证；index_sync 已落地 backfill(BATCH)+stream(STREAMING) 双模板（2026-07-06），live 验证 backfill FINISHED + stream RUNNING 增量同步正常（见 [ADR-008 模式选择评估](./adr-008-iceberg-doris-sync-path.md)）。**Catalog First (2026-07-25)**: main sync 模板 Iceberg sink 改 `schema_save_mode=IGNORE`（表由 Gaia 经 `IcebergStore.create_managed_table` 建好，SeaTunnel 只写数据），`data_save_mode` 按 `transaction_type` 分流（snapshot→DROP_DATA / append→APPEND_DATA）；`_submit_sync_pipeline` 移除 full_snapshot 的 `drop_table_if_exists`（保 snapshot 历史） |
| Engine | TrinoQueryEngine | ✅ | query、list_tables、describe_table、sample_data、test_connection |
| **Graph** 🆕 | Neo4jGraphStore | ✅ | Cypher 收口：create_label/constraint/indexed_index + upsert_node/edge + search_around + exists_link + count_nodes + find_paths（ADR-015 §十二 M1） |
| **GeoTime** 🆕 | GeoTimeStore | ✅ | PostGIS + TimescaleDB 合并封装：create_geo_table(GiST)/create_timeseries_hypertable/upsert_geo/append_series/spatial_filter/series_query（§十二 M2） |

## 二、Service 编排层

> 2026-07 新增 4 个 Service（graph_projector / geotime_projector / object_set_executor / analysis_record_store），Service 文件总数从 18 增至 22。详见 §十二。

| Service | 注入 container | 状态 | 说明 |
|---------|---------------|------|------|
| OntologyService | ✅ | ✅ | 本体 CRUD + 批量操作 + define/update batch;define/update/delete 经 IndexSyncService 触发 Doris 建表与索引同步 pipeline + **图/时空 schema provision**（_provision_graph_schema / _provision_geotime_schema，best-effort，§十二 M1/M2） |
| ObjectQueryService | ✅ | ✅ | 路由逻辑完整,Doris 全量直出为主路径(load_by_ids 点查 / execute_sql 参数化查询),Doris 不可用降级 Trino-Iceberg;read-your-writes 临时禁用(见待办2) |
| ~~VirtualTableService~~ | ⚫ | - | 已删除(Gravitino SQL View 线路废弃)。虚拟对象查询由 ObjectQueryService 按 `storage_type=VIRTUAL` 走 Trino 联邦查询 Virtual Table。详见 [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) §3.4 |
| TimeTravelService | ✅ | ✅ | Iceberg snapshot 时间旅行 |
| ActionService | ✅ | ✅ | execute_action 写 PG object_state + execution_log + outbox 原子提交 + read-your-writes;**outbox 驱动同步 (2026-07-08, 去 SeaTunnel 化)**: 主事务内为每个 CREATE/UPDATE/DELETE mutation 追加 INDEX(→Doris 近实时) + ARCHIVE(→Iceberg 微批) 两条 outbox, 由 OutboxExecutor(1s 轮询) + SyncFlushScheduler(5min/1000条微批) 异步消费, 替代原 SeaTunnel PG→Kafka→Doris + object_state→Iceberg CDC 链路 (见下文 #1 + [action-sync-outbox-design.md](../design/action-sync-outbox-design.md));**P1 补全 (ADR-011)**: 上下文注入 / 三层权限 / CDL 前后快照 / Link mutation / submission_criteria 接入 / 版本管控 / preview;**ADR Action Mutation Mapping**: 声明式 Ontology Rules (CreateObject/ModifyObject/UpsertObject/DeleteObject/CreateLink/DeleteLink) + ValueSource (PARAMETER/OBJECT_PROPERTY/STATIC/SYSTEM_CONTEXT/SYSTEM_GENERATED/EXPRESSION) + hydrate 决策 C + OCC expected_version 衔接 + on_missing→404 + 主键不可改校验 + ObjectReference 参数 + write_back effect;**版本快照修复**: define/update/rollback 用 `async with self.transaction():` 事务单元原子提交 ActionType + 版本快照(修复快照静默丢失 bug, 见 bugfix/action-type-version-snapshot-not-persisted.md);**P2 Batch Action (2026-07-06)**: execute_batch_action 分片调度 (shard_size 默100/最大1000/上限10000项) + 逐项原子事务 (单项 OCC/校验失败不中断整批→partial) + fail_fast 选项 + 派生逐项幂等键 (batch_key#index) + 共享 default_parameters 合并 (item 胜出) + POST /actions/execute-batch 路由 + BatchActionRequest/Result/ItemResult schema |
| ~~ActionSyncService~~ | ⚫ | - | **已删除 (2026-07-08, 去 SeaTunnel 化)**。原 ensure_cdc_pipelines 编排的 pg_to_kafka / kafka_to_doris / dual_sink pipeline + 对应模板 + ActionSyncService 类已全部删除, 改为 outbox 驱动 (INDEX/ARCHIVE effect)。object_state 同步不再走 SeaTunnel。SeaTunnel 现只承担外部数据源接入 (ADR-014) + 路径 A backfill (外部接入 Iceberg→Doris)。`create_action_cdc_pipeline` (审计日志→Iceberg) 亦于 2026-07-10 删除 (无调用方, 审计日志 PG append-only 已足够)。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) §8.9 |
| DataSourceService | ✅ | ✅ | 数据源 CRUD + 探索 (走 pgnative 原生 connector) + **登记虚拟表 (B2)** + **get_dataset_schema 按 kind 分流 (B3)**。**Catalog First (2026-07-25)**: `create_sync_task` 新增 `_provision_managed_table_for_sync`——提交 SeaTunnel 前先 `describe_table` 拿源表完整 schema,调 `IcebergStore.create_managed_table` 经 Gravitino 建托管表(带主键/注释/NULL/表属性 `gaia.source-datasource`+`gaia.source-table`),SeaTunnel sink 改 `IGNORE` 只写数据 |
| IndexSyncService | ✅ | ✅ | provision/rebuild/deprovision 编排:**只做 Doris 索引表 DDL（全量列建表）**。~~Iceberg→Doris 全量同步 pipeline~~ / ~~sync_now backfill~~ **2026-07 T1.10 删除**（去 SeaTunnel 化）。Iceberg→Doris 同步改由 `ObjectIndexFunnel` 承担（从 Iceberg scan_latest 读 → DorisIndexStore.upsert，统一 rid 分配/复用 + 四引擎扇出）；object_state 的 Doris 同步走 outbox INDEX effect → OutboxExecutor ≤1s |
| IndexFieldExtractor | ✅ | ✅ | 从 property 的 indexed+physical_mapping 推导 IndexField[],含红线校验 |
| AIAssistant | ❌ | ✅ | `generate_json_stream` 已实现;route 直接 import 函数调用,未走 container(可接受,无状态) |
| OutboxExecutor | ✅ | ✅ | `process_pending`/`run_forever` 已注入 container;main.py lifespan 启动后台任务;`_do_write_back` 调 WriteBackManager;**P1 (ADR-011)**: 新增 SUB_ACTION / KAFKA_TOPIC 副作用类型;**INDEX effect (2026-07-08, 去 SeaTunnel 化)**: 注入 DorisIndexStore, 新增 INDEX 分支 (CREATE/UPDATE→upsert / DELETE→delete_by_ids, 近实时 ≤1s), process_pending 排除 ARCHIVE;**图/时空节点投影 (2026-07-10)**: INDEX effect 处理完 Doris 后用 outbox payload 调 graph_projector/geotime_projector (capabilities 门控, fail-tolerant) |
| WriteBackManager | ✅ | ✅ | SQL 构建 (UPSERT/MERGE) + 反馈环标记完整;已注入并被 OutboxExecutor 调用 |
| ConflictDetector | ✅ | ✅ | **2026-07-10 重构**: 审计目标从 Iceberg (version 对比, ARCHIVE ≤5min 延迟会误报) 改为 Doris (INDEX ≤1s, 存在性检测——object_state 有但 Doris 缺失=INDEX outbox 漏写);删除 placeholder `audit_snapshot_diff`;注入 DorisIndexStore;已注入 container,lifespan 启动 `run_audit_loop` 后台审计任务 |
| IngestionFilter | ✅ | ✅ | 反馈环过滤;已接入 DataSourceService 增量同步查询重写 |
| **SyncFlushScheduler** 🆕 | ✅ | ✅ | **outbox 驱动 (2026-07-08)**: 消费 ARCHIVE outbox 微批归档到 Iceberg (run_flush_loop 60s tick, 双触发 1000条/5min, 按 ontology 分桶, FOR UPDATE SKIP LOCKED 多实例 HA 安全) + run_cleanup_loop (1h 清理 7 天前 COMPLETED/FAILED, DLQ 不删);按 ObjectType 拆分调 IcebergStore.merge (Trino MERGE INTO 按业务 PK backing_column);lifespan 启动 |
| **IcebergMaintenanceService** 🆕 | ✅ | ✅ | 路径 A 配套: Trino ALTER TABLE EXECUTE optimize/expire_snapshots/remove_orphan_files 治理小文件 + snapshot;run_maintenance_loop lifespan 启动 |
| ActionRuleEngine | (内部) | ✅ | 被 ActionService 内部实例化使用;**P1 (ADR-011)**: 上下文注入 (currentUser/currentTimestamp/workspaceId/selectedObject) + submission_criteria 评估 |
| ActionValidator | (内部) | ✅ | 被 ActionService 内部使用;**P1 (ADR-011)**: 动态默认值解析 + enum/pattern 校验 + 自定义错误文案 |
| ActionAuthorizer | ✅ | ✅ | **P1 (ADR-011)** 新增: 三层权限 (执行/行级/参数级);已注入 container |
| **GraphProjector** 🆕 | ✅ | ✅ | §十二 M1：object_state/links → Neo4j 投影（仅 indexed 属性）+ rebuild_for_object_type（未接线）；**节点+边投影已接线 (2026-07-10)**：① 节点—OutboxExecutor INDEX effect 侧调 project_object/delete_object（capabilities 门控）② 边—ActionService Step 11 调 project_link/delete_link（capabilities 门控）；**外部数据路径已接线**：ObjectIndexFunnel 从 Iceberg scan_latest 读外部接入数据调 project_object（见下 ObjectIndexFunnel 行；手动 rebuild 路径已通，SeaTunnel backfill 完成自动触发链路待接）；**VIRTUAL 联邦投影已接线 (ADR-021, 2026-07-16)**：project_object 识别 `_virtual`/`_source_ref`/`_sync_tag` 元标记写身份骨架节点（rid+label+PK+title+indexed），cleanup_stale_virtual 走 watermark 清孤儿；FK→边由 ObjectIndexFunnel 投影（一端 VIRTUAL 走 PG PK→rid 反查 / 两端 VIRTUAL 走内存 join） |
| **GeoTimeProjector** 🆕 | ✅ | ✅ | §十二 M2：object_state → PostGIS 投影（仅空间属性对象，[lon,lat]/GeoJSON/WKT 转 WKT）；**节点投影已接线 (2026-07-10)**：OutboxExecutor INDEX effect 侧调 project_object/delete_object（capabilities 门控，fail-tolerant）；**外部数据路径已接线**：ObjectIndexFunnel 从 Iceberg scan_latest 读外部接入数据调 project_object（手动 rebuild 路径已通，SeaTunnel backfill 完成自动触发链路待接） |
| **DataFrameQueryService** 🆕 (object_set_executor.py) | ✅ | ✅ | §十二 M3：ObjectSet IR 编排中枢，递归求值 IR 树 + filter 分流（属性→PG/空间→PostGIS/时序→TimescaleDB）+ searchAround→Neo4j + EvidenceChain 证据累积 + Ibis 临时表下推（设计 §7.4） |
| **AnalysisRecordStore** 🆕 | ✅ | ✅ | §十二 M6：证据链快照 save/get；DataFrameQueryService.execute 加 _save_evidence（best-effort） |
| **ObjectIndexFunnel** 🆕 | ✅ | ✅ | **统一索引编排漏斗（PR-2 重命名，原 ProjectSyncService）**：外部数据接入路径的唯一索引编排入口——从 IcebergStore.scan_latest 读全量数据，统一完成 ① rid 分配/复用（按 PK 查 Doris idx 已有 rid 复用、无则新分配，T1.4）② Doris idx 写入（成为唯一数据同步路径，SeaTunnel backfill 已废弃 T1.10）③ 按 ADR-015 四道门控（Gate1 storage_type=MANAGED / Gate2 data_type 匹配 / Gate3 关系存在 / Gate4 capabilities 开关）扇出调 graph_projector/geotime_projector.project_object，fail-tolerant（单条失败不中断）；project_for_object_type（单 OT）+ project_for_dataset（dataset 关联全 OT，MetaStore 批量预取避免 N+1）；已注入 container；暴露 admin 路由 `POST /admin/project/rebuild/{ont}/{ot}` + `POST /admin/project/rebuild-for-dataset/{dataset}`（PLATFORM_ADMIN gate）。**待接**：SeaTunnel 同步 success 回调自动触发（当前需手动 rebuild）。**VIRTUAL 联邦投影已接线 (ADR-021, 2026-07-16)**：`project_for_virtual_object_type` 旁路 Gate 1——Trino 游标分页拉外部源表 PK/title/indexed/FK 列 → 合成 object_state（带 `_virtual`/`_source_ref`/`_sync_tag`）→ Neo4jGraphStore.upsert_nodes_batch（UNWIND+CALL{} IN TRANSACTIONS 批量 MERGE）→ FK→边投影（`_resolve_fk_backing_column` source/target 两端容错；一端 VIRTUAL 一端 MANAGED 走 `get_object_states_by_pks` PG PK→rid 反查；两端 VIRTUAL 走内存 join）→ `cleanup_stale_virtual` watermark 清孤儿；register_virtual_table 成功后 asyncio.create_task 异步触发（best-effort，Trino/Neo4j 失败仅记日志不阻塞）+ `POST /admin/project/rebuild-for-virtual/{ont}/{ot}` 手动 rebuild（幂等）；VIRTUAL 节点 best-effort + 不可对账，不参与 ConflictDetector。**未完成**：PR 0 权限注入（DataFrameQueryService 未注入 AuthorizationService，图遍历入口无 OT 级 check_access，见路标 #15）；二期 PR 5（indexed 定时刷新 / VIRTUAL 行级权限 Cedar TPE→Trino WHERE / FK 自动推断回填，见路标 #16） |

## 三、Route 层

| Route | 状态 | 说明 |
|-------|------|------|
| `/ontologies/*` | ✅ | 完整 CRUD + batch |
| `/query/*` (load objects) | ✅ | ObjectQueryService 路由 |
| `/objects/{ont}/*` 🆕 (图关联推理) | ✅ | query-dataframe / object-set / traverse / exists-link / find-paths / spatial-filter / series-query / analysis（§十二 M4-M6；⚠️ query-nl / explore-plan 已删，ADR-015） |
| `/actions/*` | ✅ | define + execute + execute-batch (P2, 2026-07-06) + preview + versions + rollback |
| `/datasources/*` | ✅ | CRUD + explore + sync-tasks + virtual-tables (B2) + cdc-sync + timeseries-sync（§十二 M2） |
| `/ai/agent` (AG-UI) | ✅ | pydantic-ai Agent 挂载统一 toolset（v4.0，ADR-009）；旧 `/ai/action/confirm` 已删 |
| `/admin/*` 🆕 | ✅ | 运维/管理端点（PLATFORM_ADMIN gate）：`POST /admin/project/rebuild/{ont}/{ot}` + `POST /admin/project/rebuild-for-dataset/{dataset}`——从 Iceberg 全量重建图/时空投影（ObjectIndexFunnel，外部数据接入路径专用）；`POST /admin/project/rebuild-for-virtual/{ont}/{ot}`——VIRTUAL ObjectType 图投影重建（ADR-021，Trino 联邦拉骨架→Neo4j，幂等 + 孤儿清理） |
| `/health`, `/metrics` | ✅ | 健康检查 + Prometheus |

---

## 三-bis、本体工具层 (ADR-009, 🆕)

> 详见 [ontology-tool-layer.md](./ontology-tool-layer.md) + [adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md)。
> Palantir 原始范式参照：[reference.md](../reference.md)。
> 本体能力经统一 toolset 暴露给三类消费者：外部 Agent (MCP) / 内置 Web UI (AG-UI) / 脚本 (REST)。

| 组件 | 状态 | 说明 |
|------|------|------|
| `tools/executor.py` | ✅ 骨架 | 治理切面，MVP 仅审计（principal=anonymous）；Sprint 2 加 HITL，Sprint 3 加权限 Principal |
| `tools/toolsets/metadata.py` (4 工具 + 1 MCP/REST-only) | ✅ | list_ontologies / list_object_types / describe_object_type / describe_link_type，薄包装 OntologyService；**ADR-020 新增 `describe_ontology`（MCP + REST only，不注册 AG-UI）**——单次返回全量元数据，薄包装 `OntologyService.assemble_ontology_metadata`，对齐 Palantir `/fullMetadata` |
| `tools/toolsets/object_query.py` (7 工具) | ✅ | get/bulk_get/exists/count/filter/aggregate/topn 全部接通；薄包装 ObjectQueryService 新方法 |
| `tools/toolsets/write.py` (4 工具) | ✅ | define_object_type（含 properties 批量创建，走 `define_object_type_batch`）/add_property/define_link_type/link_dataset，固定 medium 风险，共享 _logic + AG-UI 暴露 |
| `tools/toolsets/action.py` (2 工具) | ✅ | invoke_action(按 ActionType.risk_level)/validate_action(不审批)，共享 _logic + AG-UI 暴露 |
| `tools/state.py` (AppState) | ✅ | Sprint 2 新增，持 thread_id + 请求级 executor，避免循环 import |
| `tools/executor.py` HITL 切面 | ✅ | execute_gated 分级拦截 + ApprovalStore(thread_id 索引 + action_id 全局兜底) + confirm 恢复 + ApprovalHandler Protocol |
| `ActionType.risk_level` 字段 | ✅ | schema/ORM + Alembic migration（原 20260619 已并入初始 revision）+ ActionTypeCreate 透传 |
| `tools/toolsets/link_traversal.py` (3 工具) | 🟡 部分 | list_link_types **已通**；traverse_link（批量源签名，对齐 reference.md 第 5 迭代）+ exists_link（关系存在性，对齐第 8 迭代）**骨架已注册，返回 TOOL_NOT_IMPLEMENTED**，待 LinkTraversalService（Sprint 3+ 图数据库方案） |
| `protocols/mcp_server.py` | ✅ | FastMCP 暴露 19 工具(13 只读 + 6 写/执行)；只读 add_tool(tool.function)，写/执行专用 @mcp.tool 函数调 _logic + MCPApprovalHandler(Context.elicit)；6 端到端测试 |
| `services/ai_agent.py` v4.1 改造 | ✅ | AGUIApprovalHandler(raise NeedsApprovalError) + fresh_deps(thread_id) 构造请求级 executor；挂全部 5 toolset(19 工具) |
| `routes/ai.py` v4.1 改造 | ✅ | 恢复 POST /ai/action/confirm(真正调 ToolExecutor.confirm 执行)；/ai/agent 从 RunAgentInput 提 thread_id |
| 前端 AiSuggestPanel 改造 | ✅ | (Sprint 1) 只读对话；**(Sprint 2 已完成)** 写类恢复 + thread.tsx HITL BatchApprovalPanel 渲染 + ontology_modeling Capability 方法论注入（commit 584af2c） |
| 前端 thread.tsx NEED_APPROVAL 渲染 | ✅ | Sprint 2 加 ApprovalDialog(检测 marker + 弹窗 + 调 confirmAiAction)；client.ts 恢复 confirmAiAction；types.ts 加 NeedApprovalMarker/ActionConfirmResult |
| `ObjectQueryService` 扩展 | ✅ | filter_objects/exists_objects/count_objects/aggregate_objects/topn_objects 全部实现，按 storage_type 分叉复用 _resolve_query_target；filter SQL 生成支持全操作符(eq/neq/gt/lt/gte/lte/in/notIn/contains/startsWith/endsWith/isNull/isNotNull/and/or/not)+标识符注入校验 |
| **🚫 待重构: `_filter_dict_to_sql` 等手写 SQL 翻译** | 🟡 部分重构 | **if-elif 操作符链已消除**（改用 `_OP_COMPARE`/`_OP_NULL` 映射表查表，对齐 §7.2）。剩余：① `_sql_literal` 手写转义待改为参数化查询（`TrinoQueryEngine.query` 已支持 params，需穿透调用点）；② `_validate_identifier` 正则校验待改为 `ot.properties` 白名单（需穿透 ObjectType）。两者耦合于调用点签名变更，留作后续单独 PR |
| `LinkTraversalService` | 🔴 待开发 | Sprint 2，外键点查(MANAGED)+Trino 联邦(VIRTUAL) |
| 前端 AiSuggestPanel 改造 | ✅ | 删 ApplyBar/handleApplyAiSuggestions/doBatchCreate（依赖已删 apply_suggestions）；thread.tsx 加默认 ToolCallPart 渲染器（22 工具统一显示）；删 tool-renderers.tsx/confirmAiAction/ActionConfirmResult/PendingApproval；**(Sprint 2 已完成)** 写工具恢复（MetadataApprovalToolset HITL 批量审批 + impact_builder 自然语言影响预览）+ ontology_modeling.py Capability form A 按需注入建模方法论 |

**MVP 14 工具进度**：14/14 可用（元层 4 + 检索 query_with_sql + 聚合 2 + list_link_types + traverse_link + exists_link）。原 traverse_link / exists_link 骨架（TOOL_NOT_IMPLEMENTED）已在 §十二 M4 实现。
> **设计对齐**：[ontology-tool-layer.md](./ontology-tool-layer.md) §五 5.4 关系族 3 工具——`traverse_link`（批量源 `source_keys[]` + `target_filter`/`target_properties`/`include_source_mapping`，对齐 reference.md 第 5 迭代）、`exists_link`（关系存在性，对齐第 8 迭代）。两个工具的**签名与 docstring 已在代码中落地**（MCP 可见、契约已生成），**执行逻辑待 LinkTraversalService**。
**Sprint 2 写/执行工具**：6/6 可用（写 4 + 动作 2），双协议 HITL 闭环（AG-UI NEED_APPROVAL→confirm / MCP elicit）。对象查询统一收敛到 `query_with_sql`（2026-07 删除 get_object/bulk_get_object，点查改用 SQL）。

### 🆕 图关联推理工具增量（§十二 M4-M5，2026-07）

| 工具 | toolset | 状态 | 说明 |
|------|---------|------|------|
| `query_with_dataframe` | reasoning | ✅ | 推理线统一入口（AG-UI + MCP + REST），IR 树递归求值 |
| `traverse_link` | link_traversal | ✅ | **已实现**（替换 TOOL_NOT_IMPLEMENTED，用 Neo4jGraphStore.search_around 单跳；PG object_links 降级路径） |
| `exists_link` | link_traversal | ✅ | **已实现**（替换骨架，ANY/SINGLE_TARGET 两种模式） |
| `find_paths` | link_traversal | ✅ | 第 22 个工具，allShortestPaths Cypher（max_depth + limit 防爆炸） |

**工具总数：22**（元数据 4 + 对象查询 query_with_sql 1 + 写 4 + 动作 2 + 关系族 3 + 路径 1 + 推理线 1 + 审批/画布辅助 6）。MCP 暴露 19 工具（只读 + 写/执行 + 推理/路径）。AG-UI 挂全部 toolset。

> **设计对齐**：[ontology-tool-layer.md](./ontology-tool-layer.md) §五 5.4 关系族 3 工具原为骨架，现 traverse_link / exists_link / find_paths 已全部实现（§十二 M4）。

### 后续路标（本体工具层）

| # | 项 | 阶段 | 说明 |
|---|----|------|------|
| 1 | Claude Desktop elicitation 实测 | Sprint 2 收尾 | 代码就绪（MCPApprovalHandler + Context.elicit），需真实 Claude Desktop 环境验证 elicit 弹窗 + 是/否确认闭环 |
| 2 | `_filter_dict_to_sql` 技术债重构 | ✅ 已完成 (2026-07-13) | 三条 SQL 拼接路径现状：① TextQL 编译器（`sql_compiler.py`）literal→`?` placeholder + params list，已参数化；② ObjectSet IR（`object_set_executor.py`）`:param` 占位符 + SQLAlchemy `text` 绑定 + `_validate_filter_fields` 本体白名单（P2，2026-07-06 落地），已参数化 + 白名单；③ Doris 层遗留 `query`/`load_by_filter`/`aggregate` + `_build_filter_clause` 字面量拼接——**无生产调用方**（生产读统一走 `execute_sql` 参数化路径），2026-07-13 直接删除（连 `IndexFilter`/`IndexQuery`/`IndexResult` schema），消除「非参数化拼接现成可用」的认知陷阱。详见 ICD-04 v1.1 变更记录 |
| 3 | 图数据库方案调研 | ✅ 已选型落地 (Neo4j) | §十二 M1 已采用 Neo4j 5-community（profile=graph）。traverse_link / exists_link / find_paths 已实现（§十二 M4）。多跳遍历用原生 Cypher `MATCH (n)-[*1..3]->(m)` + LIMIT（避免 APOC path.expand OOM）。后续多跳复杂场景可评估 Neo4j GDS / NebulaGraph |
| 4 | 治理 Principal + 权限 + 审计入库 | ✅ Phase 0-5 完成 (2026-07-08) | **ADR-016/017 权限治理体系全部落地**。Phase 0：三层容器 + 身份层 + 归属字段 + bootstrap + AuthMiddleware。Phase 1：RBAC（11 角色 + Layer 1-4 + cashews 缓存 + 选项 B fallback + ActionAuthorizer internals 切换）。Phase 2：MAC（Marking + Organization↔Marking 联动 + Layer 5 合取校验 + MarkingService 权责分离 + 治理红线）。Phase 3：行/列级下推（Cedar 集成层 + 残差→SQL 翻译器 + SqlGlot AST 注入器 + evaluate_query_scope）。Phase 4：审计 + JIT（AuditLog append-only + CheckAccessResult 可解释性 + AccessRequestService + `/authz/*` + `/audit-logs` 路由）。**Phase 5 已落地**：PrincipalService JWT 验证路径（Authlib，Better Auth HS256 JWT，production mode）+ dev 模式 fallback（X-User-Id 请求头）+ build_principal_service 工厂 + 前端 4 页面（CheckAccessPage 五层 stepper + AccessRequestsPage JIT 审批 + AuditLogsPage 审计查看器 + MarkingsManagementPage 标记管理）+ 侧边栏导航 + permission API client + `scripts/verify_permission_live.py` 端到端验证脚本（13/13 通过）。验证：1502 单元测试 + 13 E2E + ruff/mypy/alembic/前端 typecheck+build 全绿。后续二期：Better Auth Server Docker 部署 + RS256/JWKS + LLM 辅助策略生成 + 标记血缘传播 + 选项 B→A 迁移。详见 `docs/design/permission-governance-handoff.md` |
| 5 | 高危输名称确认（AG-UI） | Sprint 3 | 当前高危只弹是/否；CLAUDE.md 要求高危输名称，前端补输入框 + 后端校验 |
| 6 | ApprovalStore Redis 持久化 | Sprint 3 | 当前进程内 dict，重启丢失；Redis 替换同接口（集成指南 §9.3） |
| 7 | 语义检索工具 | Sprint 3 | reference.md §14 语义对象检索，依赖 Doris 4.0.5 向量索引成熟度 |
| 8 | 函数族 / 场景族 | 远期 | reference.md 第二/四族；需先建 Ontology Function 抽象 + CoW 沙箱，按真实需求触发 |
| 9 | Doris 索引表命名空间隔离 + 删除治理 | ✅ 已完成 (v5.2) | 表名加本体前缀 `idx_{ont}__{type}`；pipeline 名 `index_{ont}__{type}` / `sync_{dataset}`；`core/naming.py` 统一生成。同时落地本体软删除治理：Deprecate 前置 + 子资源级联 soft-delete + Impact API + Restore + MCP 状态过滤。Dataset 完全独立（不随本体删除，Iceberg 表不 drop）。见 §七-bis 与 `docs/design/ontology-namespace-isolation-and-cleanup.md` |
| 10 | TextQL 本体驱动自然语言查询 (ADR-012) | 🟡 Phase 2 进行中 | 见下「TextQL Phase 1-2 实现状态」 |

### TextQL Phase 1-2 实现状态 (ADR-012, 🆕 2026-06-27/28)

> 详见 [adr-012-textql-ontology-driven-nl-query.md](./adr-012-textql-ontology-driven-nl-query.md) + [textql-design.md](./textql-design.md)。本体驱动 NL 查询五步流水线（意图解析→召回→Schema注入→Tool Use→执行），对标 Palantir 范式 B。

| 组件 | 状态 | 说明 |
|------|------|------|
| `core/schemas/textql.py` (QueryIR 一等公民) | ✅ | QueryIR + RecallResult + FilterSpec/PropertyRef/ObjectRef/LinkRef/WindowSpec；24 单测 |
| `services/textql/intent_parser.py` (Step 1) | ✅ | LLM 产出 QueryIR（pydantic-ai result_type）；验证 10/10 稳定 |
| `services/textql/semantic_recall.py` (Step 2 引擎A+B) | ✅ | 引擎A 精确匹配 + 引擎B 向量召回兜底（低置信触发）；async recall；17 单测 |
| `services/textql/embedding.py` (引擎B 推理) | ✅ | OnnxEmbeddingProvider: ONNX CPU 推理 MiniLM-L12-v2 (384维)，~15ms/句；7 单测（含语义相似度验证） |
| `services/textql/vector_indexer.py` (向量化流水线) | ✅ | define/update 钩子把本体元素 embedding 后写入 Doris 语义表；2 单测 |
| `services/textql/schema_injector.py` (Step 3) | ✅ | 确定性 Schema 注入，7 单测 |
| `services/textql/orchestrator.py` (Step 1-3 串联) | ✅ | agent 入口自动接线，引擎B 按需启用（表不存在/模型未装则禁用，非致命）；7 单测 |
| `services/textql/schema_provider.py` | ✅ | MetaStoreSchemaProvider 从 PG 加载本体给编译器；方言感知物理名（Doris `idx_<ont>__<type>` / Trino `iceberg.ontology.<snake>`）+ `storage_types()` + `trino_table_refs()` |
| `services/textql/sql_compiler.py` (Step 4 路径B) | ✅ | SqlGlot 编译器 + 三大护栏 + CTE 支持 + `involved_object_types()` 推断 + `SELECT *` 展开（同 apiName 冲突加 OT 前缀）+ 方言感知物理名 + 表别名补偿；36 单测 |
| `ObjectQueryService.execute_compiled_sql` | ✅ | **设计决策 C（2026-07）**：删除 `object_type` 参数，从 SQL 推断所有 OT 统一权限/路由/回映；全 MANAGED→Doris主/Trino降级，含 VIRTUAL→Trino 跨 catalog 联邦 JOIN（不再报 MIXED_STORAGE_JOIN）；`_map_backing_to_api_multi` 合并多 OT 列名回映 |
| `DorisIndexStore` 语义表方法 | ✅ | create_semantic_table(IVF ANN)/upsert_semantic_rows/vector_search/execute_sql |
| `tools/toolsets/object_query.py` 工具 `query_with_sql` | ✅ | AG-UI + MCP 双协议暴露；签名 `query_with_sql(ontology, sql)`（无 object_type）；get_object/bulk_get_object 已删（2026-07，点查统一用 `SELECT * FROM <OT> WHERE <pk>=?`）|
| 端到端验证 (Airline + Marketing 本体+Doris/Trino) | ✅ | 单表过滤/聚合/CTE/三大护栏拦截全过；4 表 JOIN（销售外呼统计）真实跑通；SELECT * 多表不再丢列；跨 MANAGED+VIRTUAL 联邦 JOIN 路由 Trino |
| 前端 | ✅ 无需改动 | 默认 ToolCallPart 渲染器自动支持 query_with_sql；tsc 0 非测试错误 |

**Phase 1-2 已完成**（五步流水线 + 双引擎召回 + 编译器 CTE + 白名单护栏，端到端验证通过）。

**Doris ANN 环境调优（已解决）**：Doris BE 容器内存 1GB < ANN memtable load 需 2GB → `MEM_LIMIT_EXCEEDED`。解法：`be.conf` 加 `mem_limit=80%`/`load_mem_limit=80%` + docker-compose doris-be 内存 1g→3g + 建表不带 ANN 索引改用 `ALTER ADD INDEX`（走低内存路径）。向量召回端到端已跑通（Airline 本体 78 元素索引，口语化查询精准命中）。

**Phase 3+ 未完成**：
- HyDE + Rank Fusion（引擎B 增强）
- `_validate_identifier` 全链路参数化绑定（当前白名单+escape，参数化待穿透调用点）
- Trino 降级路径表名重编译（聚合+Doris 失败场景）
- traverse_link 跨对象 JOIN（LinkTraversalService，单独做）
- 多轮对话状态管理（T14）

---

---

## 四、关键断裂链路 (开发优先级)

### ✅ #1 Action 闭环未通 (P0) - 已接通

> **2026-07-08 重大重构：去 SeaTunnel 化, 改 outbox 驱动**。原 PG→Kafka→Doris (路径 B) + object_state→Iceberg 的 SeaTunnel CDC 链路已**全部删除**, 改为 outbox 表的 INDEX/ARCHIVE effect 异步同步。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

**设计链路** (见 action-architecture.md 第一部分):
```
Action execute → PG(object_state + execution_log + outbox[INDEX|ARCHIVE] 原子提交)  ✅ 已实现
              → 返回 "applied" (read-your-writes)                              ✅ 已实现
              → OutboxExecutor 消费 INDEX outbox → Doris upsert (近实时 ≤1s)    ✅ 已实现
              → SyncFlushScheduler 消费 ARCHIVE outbox → Iceberg MERGE (微批 ≤5min) ✅ 已实现
              → OutboxExecutor 消费 WEBHOOK/WRITE_BACK outbox → 副作用执行        ✅ 已启动(lifespan 后台任务)
```

**已落地**:
- [x] container 注入 `OutboxExecutor`(含 index_store) + `SyncFlushScheduler` + `WriteBackManager` + `ConflictDetector` + `IcebergMaintenanceService`
- [x] `main.py` lifespan 启动: OutboxExecutor.run_forever + SyncFlushScheduler.run_flush_loop/run_cleanup_loop + ConflictDetector.run_audit_loop + IcebergMaintenanceService.run_maintenance_loop
- [x] `ActionService._create_sync_outbox_records`: 每个 CREATE/UPDATE/DELETE mutation 在主事务内追加 INDEX+ARCHIVE 两条 outbox (原子提交, 失败回滚保证 outbox⟺object_state 一致性)
- [x] `OutboxExecutor` INDEX 分支: CREATE/UPDATE→DorisIndexStore.upsert / DELETE→delete_by_ids; process_pending 排除 ARCHIVE
- [x] `SyncFlushScheduler`: run_flush_loop (60s tick, 按 ontology 分桶, 双触发 1000条/5min, FOR UPDATE SKIP LOCKED) + run_cleanup_loop (1h 清理 7 天前 COMPLETED/FAILED, DLQ 不删)
- [x] `IcebergStore.merge`: Trino MERGE INTO 按业务 PK (backing_column, 非 rid) 覆盖旧记录; delete=True 走 WHEN MATCHED THEN DELETE
- [x] `property_mapping.py`: object_state.properties 以 backing_column 为 key (api_name↔backing_column 转换在 ActionService 写边界)
- [x] `ObjectQueryService` read-your-writes: 先查 `object_state` 兜底(点查 + top-level eq filter)
- [x] Alembic migrations: timezone 列修复 + object_state.ontology_api_name + properties keys backfill (api_name→backing_column) + outbox.target_ontology

**真实环境已验证** (commit 73b1c7f 冒烟):
- [x] execute action → applied + object_state 写入 + INDEX/ARCHIVE outbox 追加
- [x] read-your-writes 点查/filter 立即可见
- [x] OutboxExecutor 消费 INDEX outbox → Doris upsert COMPLETED (1s 内)
- [x] SyncFlushScheduler 消费 ARCHIVE outbox → Iceberg MERGE INTO COMPLETED (微批)
- [x] OutboxExecutor 消费 webhook outbox → COMPLETED
- [x] lifespan 后台 OutboxExecutor 真实运行(插入 outbox 1s 内消费)
- [x] 修复 ActionValidator 拒绝 `mutations` 系统参数;迁移存量 PHYSICAL→MANAGED

**去 SeaTunnel 化 (设计 §8.9 阶段 2, 已完成)**:
- [x] 删除 `ActionSyncService` (孤儿方法) + `create_pg_to_kafka_pipeline` / `create_kafka_to_doris_pipeline` / `create_dual_sink_pipeline` + PIPELINE_PG_TO_KAFKA/KAFKA_TO_DORIS/DUAL_TEMPLATE + TableSchema* 类
- [x] 删除注释禁用的 run_backfill_loop
- [x] 删除 scripts/verify_action_loop_live.py + scripts/verify_action_cdc_live.py (旧 SeaTunnel CDC 验证脚本, 链路已不存在)
- [x] ~~**保留** `create_action_cdc_pipeline`~~ — **2026-07-10 删除** (无调用方, 审计日志 PG append-only 已足够, 审计归档 Iceberg 无合规需求)
- [x] ~~**保留** 路径 A 的 PIPELINE_INDEX_BACKFILL 模板~~ — **2026-07 T1.10 删除** (去 SeaTunnel 化)。外部接入数据的 Iceberg→Doris 同步改由 `ObjectIndexFunnel` 承担 (Python 侧直连 DorisIndexStore.upsert), 不再走 SeaTunnel

> **历史背景** (已被 outbox 方案取代, 仅作记录): 2026-07-06 曾用 SeaTunnel PG-CDC (Postgres-CDC + url + decoding.plugin.name + database-names/schema-names/table-names) + 独立 replication slot + Iceberg sink primary-keys/upsert-mode-enabled 规避 #10747, 并修 timestamptz blocker (migration 0e2239a90155)。但该链路 ensure_cdc_pipelines 是孤儿无调用方 + per-type 常驻 job 规模化爆炸, 故 2026-07-08 整体废弃改 outbox 驱动。详见 docs/bugfix/seatunnel-pg-cdc-timestamptz-blocker.md + docs/bugfix/path-b-kafka-doris-schema-mismatch.md

**已完成收尾 (2026-07-10)**:
- [x] ConflictDetector 审计目标改为 Doris (存在性检测, 检测 INDEX outbox 漏写), 删除 placeholder `audit_snapshot_diff`
- [x] `create_action_cdc_pipeline` (审计日志→Iceberg) 删除 (无调用方, 无合规需求)
- [x] IndexSyncScheduler 删除 (周期轮询全量 ObjectType 不合理, 外部接入数据改方案 A: provision/sync_now 事件驱动触发 Doris 同步)

详见 [action-loop-design.md](./action-loop-design.md) + [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)

### Action 架构对齐 Palantir 官方文档的修正与决策 (2026-07)

> 基于 Palantir 官方文档 (action-types/rules, object-edits/how-edits-applied, workshop/scenarios-concepts, action-types/notifications, action-types/webhooks) 一手资料交叉校验后, 纠正了几处二手资料的认知偏差, 并明确若干架构决策。

#### 已修复的架构断裂

- [x] **规则编译合并 (断裂 2, 对齐 Palantir rules 文档 "compile rules to generate a single edit per object")**: `ActionService._compile_mutations` 在 `_build_mutations_from_rules` 末尾按 rid 合并同对象的多条 object mutation。合并语义用 `_override` 内部字段 (仅 rule 声明的增量, 不含 base) 做正确增量合并, 避免「后者的全量 properties (含 base 旧值) 覆盖前者改动」的 bug。CREATE+UPDATE→合并为 CREATE; UPDATE+UPDATE→override 合并 (后者胜), expected_version 取后者; Link mutation 不合并; UPDATE_PROPERTY 归一为 UPDATE_OBJECT。合并后每个对象一条 INDEX/ARCHIVE outbox (此前重复 mutation 会产生多条同步 outbox, 导致同一对象被同步多次 + 中间状态泄漏到 Doris)。
- [x] **Invalid combinations 执行期校验 (断裂 3, 对齐 Palantir rules 文档 "Invalid combinations")**: `_validate_rules_execution` 扩展第二类校验: 按声明顺序跟踪每对象 (object_type, pk) 的首个 op, 命中 `_INVALID_COMBINATIONS` (delete-before-add/modify, modify-before-add, create-twice, create-then-modify/delete) → `ValidationError(422)`。**关键**: 同对象多条 ModifyObject/UpsertObject **不拦** (由 `_compile_mutations` 合并, Palantir 语义是「编译成单 edit」而非「一个 op per object」)。条件规则跳过 condition 为假的不参与组合校验 (条件分支让两条看似冲突的规则实际只执行一条时不算冲突)。

#### 明确延后的架构决策 (本期不做)

| 项 | 决策 | 理由 / 现状 |
|----|------|------------|
| **Read-your-writes 主查询路径接线** | ❌ 延后 | `ObjectQueryService` 主查询入口未前置 object_state 兜底 (仅 `object_set_executor` 图推理路径 + Action 内部 hydrate 用了 object_state)。`PostgresMetaStore.query_object_states`/`get_object_state` 已存在但主查询路径未调用。本期决策不做, 维持「Action 返回 applied 后, 查询侧由 OutboxExecutor 1s 轮询同步 Doris 后可见」的现状。**注意**: 本表 #1 中的 `ObjectQueryService read-your-writes: 先查 object_state 兜底` 描述与代码现状不符, 待后续修正文档或补接线。 |
| **object_state 语义定位** | ✅ **保持现有全量快照模型** | Palantir OSv2 是「数据源基线 + 用户编辑层」叠加 (用户编辑只存增量, 未编辑属性跟数据源走, 两种冲突策略 apply-user-edits / apply-most-recent-value)。Gaia 的 `object_state` 存**全量快照** (ModifyObject 时 hydrate 整对象, 合并后整体写回, 一旦写入即该对象唯一真相, 与原始数据源脱钩)。**决策: 保持全量快照**, 不改为编辑层。代价: 无法表达「只改这一个属性, 其他属性继续跟数据源走」, object_state 写入后未编辑属性会被旧值遮蔽数据源更新。适合 Action 是主要写入源的场景。 |
| **属性级 OCC / Auto-Merge / Function-level Retry** | ❌ 不做 (前提不成立) | 二手资料曾称 Palantir OSv2 有「属性级 OCC + 正交并发 + Auto-Merge + Function-level Retry」, 查官方文档后**不成立**: OSv2 实际是**削弱**版本检查 (只查参与编辑生成的对象版本, reduce StaleObject conflicts, weaker guarantees), 没有 Auto-Merge/Function-level Retry 概念。Gaia 行级 OCC (`WHERE version=expected`) 已比 OSv2 更严, 非差距。 |
| **Webhook writeback vs side-effect 失败语义区分** | ❌ 延后 | Palantir 官方区分 webhook 两种用法 (writeback 失败给终端用户看/回滚, side-effect 失败静默)。Gaia 所有 effect 统一走 outbox+重试+DLQ, 失败对用户全部不可见。是否在 Action 结果暴露「副作用待执行/失败」状态属产品语义决策, 延后。 |
| **Notification 真实现** | ❌ 延后 (保持 `_log.info` 占位) | Palantir Notification 是完整产品功能 (站内/邮件/模板/Handlebars/收件人来源/用户偏好)。Gaia 当前 `_log.info` 是合理占位。要做需最小可用 (PG 通知表 + 查询接口) 或明确拒绝 (配了报错「未实现」, 避免违反「禁止静默失败」红线)。延后。 |
| **Function-backed Action** | ❌ 延后 (EXPRESSION 过渡) | Palantir `@OntologyEditFunction` (TS 沙箱) 处理声明式 Rules 不够的复杂逻辑 (循环/跨对象级联); Function rule 存在时禁其他 Rules。Gaia 用 `EXPRESSION` ValueSource 过渡覆盖 80% 场景, 不引入函数运行时。需明确触发条件再启动。 |
| **Scenario 推演** | ❌ 延后 | Palantir Scenario = 一组 Actions + Models 评估出的**不可变 fork** (非交互式沙箱), 强依赖 Models (=Function 封装预测模型)。Gaia Model 体系/不可变 fork 存储/对比分析三样均无, 投入大, 需产品方向决策。 |
| **Actions on Interfaces** | ❌ 延后 | Palantir 支持对 Interface 类型执行 Create/Modify/Delete。Gaia Interface 仅 metadata 层, 无 REST 端点。需 Interface CRUD 路由 + Action 规则的 interface 变体。 |
| **草稿/提案审批闭环** | ❌ 延后 | AIP Action Tool 的 Manual 模式 (预填参数→用户确认) 本质是「执行前确认」, Gaia 的 `ApprovalStore` (ADR-010) 已覆盖。真正缺的是「生成可评审草稿」(草稿隔离区 + 影响范围 + 多人评审), 更偏产品, 延后。 |

### ✅ #2 Doris 索引链路空转 (P0) - 已接通

**设计链路** (见 CLAUDE.md 场景2):
```
ObjectQueryService → DorisIndexStore.query (索引过滤)  ✅ 调用了
                  → IcebergStore.load_by_ids (点查)    ✅ 调用了
                  → Trino fallback                    ✅ 调用了

IndexSyncService.provision/rebuild/deprovision  ✅ 已接通 (OntologyService define/update/delete)
IndexFieldExtractor (真实 indexed 字段)         ✅ 新增
DorisIndexStore.table_exists (降级区分)         ✅ 新增
ObjectQueryService 降级区分 (not_built/doris_down) + 指标  ✅ 新增
```

**已落地**:
- [x] 新建 `IndexSyncService` (`services/index_sync_service.py`) 编排:创建/更新/删除 ObjectType 时触发 `create_index_table` + `create_index_sync_pipeline`/`update_sync_pipeline`
- [x] 新建 `IndexFieldExtractor` (`services/index_field_extractor.py`) 从 property 的 `indexed`+`physical_mapping` 推导真实 IndexField[],含红线校验 (STRUCT/ARRAY/ATTACHMENT 等拒绝入 Doris)
- [x] `ObjectQueryService` 的 Doris 失败检测 + 降级日志区分 "索引未建" (info, not_built) vs "Doris 宕机" (warning, doris_down)
- [x] Prometheus 指标 `object_query_index_hit_total` / `object_query_fallback_total{reason}`
- [x] 集成验证 `tests/integration/test_index_acceleration.py`:extract→provision→backfill→query 端到端,确认索引表有真实数据且查询返回真实 ID

**仍待验证**(需真实组件环境):
- [x] Doris sink `fenodes` 端口修正为 8030(FE HTTP),见 ADR-008
- [x] SeaTunnel submit-job 请求体修正:V1 端点需 `?format=hocon` 才接受 HOCON(此前 400)
- [x] SeaTunnel Iceberg→Doris 同步 - **已落地双模板**（原“受 SeaTunnel 2.3.13 限制暂缓”判断于 2026-06-25 证伪：REST Catalog 配置层可通；2026-07-06 进一步证伪 STREAMING 增量不 crash）。`PIPELINE_INDEX_BACKFILL_TEMPLATE`（BATCH 全量）+ `PIPELINE_INDEX_STREAM_TEMPLATE`（STREAMING + `FROM_LATEST_SNAPSHOT`）已拆分落地，`create_index_pipeline` 提交两个 job，`stop_index_pipelines` 停两个 job；live 验证 backfill FINISHED 灌数据 + stream RUNNING 增量同步正常（Iceberg 插入 → 20s 内 Doris 出现）。`stop()` bug 同步修复（原 `cancel-job` 端点失效→ `stop-job`+jobId）。`sync_now`（Trino 读 + Doris upsert）保留为不依赖 SeaTunnel 的容灾兜底
- [x] 阶段 8 实时索引同步 - **已实现 (2026-07-08, outbox 驱动去 SeaTunnel 化)**: object_state 变更经 outbox INDEX effect → OutboxExecutor 1s 轮询 → DorisIndexStore.upsert (CREATE/UPDATE) / delete_by_ids (DELETE), 近实时 ≤1s。替代原 PG→Kafka→Doris (路径 B) SeaTunnel 链路 (已删)。object_state.properties key 改用 backing_column (json_key=doris_column=backing_column, 三层对齐)。冒烟验证 INDEX 1s 内 Doris upsert COMPLETED。详见 #1 + [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)

**真实环境已验证**(`scripts/verify_index_live.py` + `scripts/verify_e2e_full.py` 步骤D):
- [x] Doris 真实建表 + upsert + query 全链路(9 项检查)
- [x] API `POST /object-types/create` 真实触发 Doris 建表 `idx_ticket`
- [x] 修复 5 个真实环境 bug(replication_num / UNIQUE KEY / DISTRIBUTED BY / pk_column / primary_key 匹配)
- [x] `IndexSyncService.sync_now`:Iceberg→Doris 一次同步(Trino 读 + Doris upsert),25 条 upsert + 查询返回真实 ID(ADR-008)

### ✅ #3 未接线组件 (P1) - 已接通

| 组件 | 当前状态 | 说明 |
|------|---------|------|
| ConflictDetector | ✅ 已注入 container + 后台审计任务 | `main.py` lifespan 启动 `run_audit_loop`(定期 reconcile PG object_state vs Iceberg);`run_audit_once` 可单点调用 |
| IngestionFilter | ✅ 已接入 DataSourceService | `_assemble_source_config` 对 `sync_mode=incremental` 任务应用 `rewrite_incremental_query`,过滤 gaia_sync_tx 反馈环 |
| WriteBackManager | ✅ 已注入并被 OutboxExecutor 调用 | - |
| AIAssistant | route 直接 import 函数,无状态 | 可接受现状 |
| VIRTUAL 写入 guard | ✅ 后端 + 前端 | ActionService.execute_action 拒绝 VIRTUAL 目标(ValidationError);前端 ActionsOverview 卡片置灰 + 禁用执行 |

---

## 五、前端实现状态

> 前端代码在 `src/web-ui/`(React 19 + Vite 8 + Tailwind 4.3.1)。下表反映实际组件文件,不再对照 CLAUDE.md 原始 6 组件设计(设计名与实际命名不一致,以实际为准)。

| 模块 | 实际状态 | 说明 |
|------|---------|------|
| 框架 | ✅ | React 19 + Vite 8 + react-router-dom 7 |
| 样式 | ✅ | Tailwind 4.3.1 (frontend-standards.md 的 "S4 暂不引入 Tailwind" 决策已被推翻) |
| Headless 行为层 | ✅ | **ADR-013 React Aria Components** (`react-aria-components` 1.19) 作为 headless 行为层；`components/ui/` 沉淀项目级原语：`TextInput`/`TextAreaInput`(IME-safe)、`Select`/`SelectOption`、`DataTable`、`ComboBox`、`DatePicker`、`Disclosure`、`Modal`。全部受控表单输入已迁移至 IME-safe 包装；36 处原生 `<select>` 迁移至 React Aria `Select`；只读展示表用 `DataTable`（2026-07 由 React Aria `Table` 回退为原生 `<table>`，规避 RAC collection 校验竞态导致「浏览 Schema」连续切表崩溃，详见 ADR-013 R1）；Modal/ConfirmDialog 基于 React Aria `ModalOverlay`+`Dialog`(焦点陷阱/ESC/遮罩/iOS 滚动锁定) |
| 图谱渲染 | ✅ | `components/OntologyGraph.tsx` 已用 cytoscape + cxtmenu + navigator;右键菜单/鸟瞰图/周边聚焦/SVG 导出已落地(见 git log "图谱第二梯队能力") |
| 复用组件 | ✅ | ObjectTypeViews / ObjectDetailPanel / DataSourceCard / SyncTaskCard / CreateObjectWizard / RegisterVirtualTableDialog / DatasetLinkDialog 等 |
| 数据集关联 UI (F4) | ✅ | ObjectDetailPanel 数据集区块 + DatasetLinkDialog 可编辑保存(A1 已接通) |
| VIRTUAL 写入 guard (F5) | ✅ | 向导 Step 3 guard + ActionsOverview 卡片「只读」标记 + 禁用执行按钮;后端 VIRTUAL 拦截 |
| 动作创建 | ✅ | **ADR Action Mutation Mapping 全套已落地**: ActionTypeEditor(结构化编辑器: 基本信息/规则/参数/校验/预览) + RuleCard + PropertyMappingRow + ValueSourceInput(机制 B 按属性类型收窄) + ParameterList(机制 A 自动派生参数) + EffectConfigForm + ActionPreviewPanel(干跑); 对标 Palantir 三机制(属性映射自动派生参数/值来源自适应/规则+副作用统一入口); 对象详情面板编辑+新建入口 + 编辑回填; ActionsOverview 只读抽屉; 版本历史抽屉(回滚); ObjectPicker 对象搜索下拉(P1); 规则拖拽排序; CreateObjectWizard Step 3 引导式; ExecuteActionDialog 409 冲突反馈; 前端 action API URL bug 修复(execute/preview/update 等错用 /ontologies 前缀→/actions) |
| 对话式建模 | ✅ | **多轮对话式本体建模已落地**（Sprint 2 + commit 584af2c）：AG-UI Thread 维护完整对话历史（多轮上下文经 message_history 透传后端 pydantic-ai）；写工具 4 个（define_object_type/add_property/define_link_type/link_dataset）+ 动作工具 2 个已挂 Agent，经 MetadataApprovalToolset HITL 批量审批；ontology_modeling.py 以 pydantic-ai Capability（form A defer_loading）按需注入 Palantir 级建模方法论（六步流/数据类型红线/M:N 拆分/置信度标记）；impact_builder 生成自然语言影响预览；系统提示引导并行批量建模 |
| 前端测试 | ✅ | vitest 35 个测试文件（actionForm/actionDraft 纯逻辑 + ActionParameterField/OntologySidebar/ExecuteActionDialog 组件 + Select/DatePicker/Disclosure ui 原语 + 图探索 + 权限） |

**待开发**:
- [ ] 对话式建模体验打磨（P2/P3，非功能缺口）：Capability 按需加载改条件注入（避免 LLM 漏 load_capability）+ prepare_tools 按场景过滤工具（缓解 22 工具 tool selection 退化）+ 参数解析契约写入工具开发规范
- [ ] 前端测试体系持续完善
- [x] ADR-002~006 补齐 + ICD-01~05 实体文件 + CHANGELOG.md（2026-07-10 文档治理完成）

---

## 六、文档治理状态

| 文档 | CLAUDE.md 要求 | 实际状态 |
|------|---------------|---------|
| ADR | ADR-001~017 索引 | ✅ **全部已有实体文件** (2026-07-10)：ADR-001、ADR-002、ADR-003、ADR-004、ADR-005、ADR-006、ADR-007、ADR-008、ADR-009、ADR-010、ADR-011、ADR-012、ADR-013、ADR-014、ADR-015、ADR-016、ADR-017、adr-action-mutation-mapping |
| ICD | ICD-01~05 基线 | ✅ **已有实体文件** (2026-07-10)：icd-01-postgres-meta-store / icd-02-gravitino-registry / icd-03-iceberg-store / icd-04-doris-index-store / icd-05-trino-query-engine（含职责边界 / 方法签名 / 异常契约 / 降级策略 / 变更管理） |
| CHANGELOG.md | 版本管理 | ✅ **已创建** (2026-07-10)：记录 0.1.0 里程碑（8 层 + 22 Service + ADR-001~017 + 图关联推理 + 多源融合 + 权限治理）|
| CLAUDE.md 目录结构 | 反映项目结构 | 🟡 **已同步** (2026-07-06)：Layer 6→8、Service 18→22、工具 18→22、ADR 索引补 ADR-015、docker-compose 服务拓扑补 Neo4j/PostGIS/TimescaleDB |

**待开发**:
- [ ] 补充 ADR 实体文件覆盖更多关键决策（Cytoscape 图谱渲染、Tailwind v4.3 迁移、object_state 临时态、pgnative workaround 等，非阻塞）
- [ ] 更新 CLAUDE.md 目录结构与服务清单,或改为指向本文件 + architecture 目录

---

## 七、已完成的临时方案 (需后续回归)

| 方案 | 文档 | 回归条件 |
|------|------|---------|
| Trino 原生 PG connector (pgnative) 绕过 Gravitino jsonb bug | [gravitino-1.3.0-upgrade.md](../bugfix/gravitino-1.3.0-upgrade.md) | Gravitino 已升级 1.3.0，但 jsonb 仍映射为 ExternalType（1.3.0 未修复），pgnative 暂不可移除。待社区在 PG TypeConverter 增加 jsonb→JSON 映射后回归 `pg` catalog |

---

## 七-bis、本体删除级联清理验证 + Doris 表名命名空间隐患

### 本体删除级联清理（已实测 ✅）

用 `medical` 本体实测 `DELETE /ontologies/{api_name}`（见 `OntologyService.delete_ontology`）：

| 资源 | 删除前 | 删除后 | 清理路径 |
|------|-------|-------|---------|
| 本体/对象类型/属性/关系 (PG) | 1/11/63/27 | 0/0/0/0 | ORM `ON DELETE CASCADE`（`object_state.ontology_id`、`object_links.ontology_id`、`properties.object_type_id` 等全 CASCADE） |
| 对象实例 `object_state` (PG) | 0 | 0 | CASCADE |
| Doris `idx_<type>` 表 | — | dropped | `_deprovision_index` → `IndexSyncService.deprovision` → `drop_index_table` |
| Gravitino `table_meta` / SeaTunnel pipeline | — | 无残留 | — |

清理链路工作正常（另用临时本体 `zz_doris_cleanup_test` 端到端验证：provision 建 `idx_cleanup_item` → 删本体 → Doris 表被 drop）。

### ⚠️ Doris 表名命名空间隐患（待修，见路标 #9）

`DorisIndexStore._table_name` = `idx_<object_type_api_name>`，**不含本体维度**。实测 20 个 `e2e_a_*` 本体各自有叫 `asset` 的 ObjectType → 共享同一张 `idx_asset` 表（表内无 `ontology_id` 列）。后果：

- **跨本体数据误删**：删任一本体触发 `drop_index_table('asset')` → `DROP TABLE idx_asset`，把其他 19 个本体的索引/对象数据一起删掉。
- **跨本体数据互盖**：多本体同名类型写入时按 PK `id` 互相覆盖（无隔离）。

当前未触发数据丢失（这些类型暂无实例数据）。修复需穿透 provision/rebuild/query/backfill/CDC 全链路（表名加本体前缀或加 `ontology_id` 列 + 复合 PK）。

### ⚠️ 本体删除无审计日志（待修，见路标 #4）

REST 路由的 `delete_ontology` 不经 Agent 工具层 `audit_call`，仅 Doris 清理失败时 `_log.warning`，**无"谁删了哪个本体"的持久化审计**。无审计表、无访问日志中间件。归入路标 #4（治理 Principal + 权限 + 审计入库）统一整改。

---

## 八、数据集与本体关联 (dataset-ontology-binding.md)

> 详见 [dataset-ontology-binding.md](../design/dataset-ontology-binding.md)。本节跟踪其落地状态。

### 后端

| 项 | 状态 | 说明 |
|----|------|------|
| B1 `DatasetGovernance.kind` (MANAGED\|VIRTUAL) | ✅ | schema/ORM/meta_store + Alembic migration（原 `scripts/migrations/20260618` 已并入初始 revision） |
| B2 登记虚拟表接口 `POST /datasources/{ds}/virtual-tables` | ✅ | route + `DataSourceService.register_virtual_table` + 单测/集成测 |
| B3 `get_dataset_schema` 按 `kind` 分流 | ✅ | MANAGED 走 IcebergStore,VIRTUAL 解析三段式定位符走 Gravitino 联邦 |
| B4 删除 Gravitino SQL View 死代码 | ✅ | 删 `VirtualTableService`、`GravitinoRegistry.create_view`;保留 `is_view` 探测方法 |
| B5a `storage_type` PHYSICAL→MANAGED | ✅ | 7 文件 + 前端类型同步;存量数据迁移走同一 SQL 脚本 |
| **A1 独立管理关联 API** `PATCH /object-types/{type}/dataset-link` | ✅ | route + OntologyService.link_dataset/unlink_dataset + storage_type/kind 一致性校验 + 单测/集成测;解锁前端 DatasetLinkDialog「保存关联」 |
| 后端 Action 写入校验 (VIRTUAL 拦截) | ✅ | ActionService.execute_action 拒绝 VIRTUAL 目标 (ValidationError),与前端 F5 guard 互补 |

### 前端

| 项 | 状态 | 说明 |
|----|------|------|
| F-types 类型对齐 (StorageType/DatasetGovernance.kind/wizard) | ✅ | `types/index.ts`、`types/wizard.ts` |
| **BuildWith: 从数据集脚手架生成对象类型** | ✅ | `docs/design/buildwith-object-scaffolding.md`；向导精简为 3 步（数据集 → 属性与键 → 审核），关系延后/动作移出；`POST /ai/scaffold` 结构化流式（pydantic-ai Tool Output + `stream_output`）；后端 `stream_structured` 通用能力（`ai_generate.py`）；AI 推导元数据+属性+主键+标题，确定性补 data_type/nullable；失败兑底确定性骨架；单测 `tests/unit/ai/test_scaffold.py` + 前端 `CreateObjectWizard.test.tsx` |
| F0 登记虚拟表入口 + Dialog | ✅ | `RegisterVirtualTableDialog` + `SchemaTreeBrowser`/`ExplorerView`/`DataSourceDetail` 接线 |
| F1 向导 Step 0 统一数据集选择 (删 mock、按 kind 过滤、storage_type 提顶) | ✅ | `CreateObjectWizard` |
| F2 Step 1 源列映射 + "从数据集生成属性" | ✅ | `lib/typeMapping.ts` + wizard；属性 api_name 由后端 `derive_api_name` 推导（前端 `lib/deriveApiName.ts` 仅预览，不提交）；PK/标题按属性 index 选择，提交时落 `is_primary_key`/`is_title_property` flag（后端反推 primary_key/title_property）；**BuildWith 后选数据集自动调 `/ai/scaffold` 流式填充，"从数据集生成属性"退为手动兑底** |
| F3 提交 `physical_mapping` + 编辑回填修复 | ✅ | `OntologyWorkspace.handleWizardComplete`/`handleEditObject` + 后端 `PropertyInput.physical_mapping` 透传 |
| F4 对象详情数据集区块 + 列表徽章 | ✅ | ObjectDetailPanel 数据集区块 + DatasetLinkDialog 可编辑保存(A1 已接通) |
| F5 VIRTUAL 写入 guard | ✅ | 向导 Step 3 guard + ActionsOverview 卡片「只读」标记 + 禁用执行按钮;后端 ActionService VIRTUAL 拦截 |
| F6 DatasetDetail 按 `kind` 展示 | ✅ | `DatasetDetail.tsx` |

### 验收 grep (§7.2)

```bash
grep -rn "PHYSICAL" src/ --include=*.py --include=*.ts --include=*.tsx   # 无残留
grep -rn "联邦表" src/ --include=*.py --include=*.ts --include=*.tsx        # 无残留
grep -rn "VirtualTableService\|create_view\|query_view" src/ontology     # 仅剩解释性注释
```

---

## 九、建议的开发顺序

1. **P0 #1 Action 闭环** - ✅ 已接通 (OutboxExecutor + SyncFlushScheduler + read-your-writes)。**2026-07-08 去 SeaTunnel 化**: object_state 同步改 outbox 驱动 (INDEX→Doris 近实时 ≤1s / ARCHIVE→Iceberg 微批 ≤5min), 删除 ActionSyncService + pg_to_kafka/kafka_to_doris 链路。冒烟验证通过 (见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md))
2. **P0 #2 Doris 索引** - ✅ 已接通 (IndexSyncService + IndexFieldExtractor + 降级区分 + `sync_now` 真实同步)。路径 A backfill (BATCH) 服务外部接入数据; object_state 的 Doris 同步走 outbox INDEX effect (≤1s)。`stop()` bug 同步修复（ADR-008）
3. **P1 #3 接线孤儿 service** - ✅ ConflictDetector (后台审计任务) / IngestionFilter (增量查询重写) / WriteBackManager 均已接通
4. **P1 A1 独立管理关联 API** - ✅ `PATCH /object-types/{type}/dataset-link` + 前端 DatasetLinkDialog 保存已接通
5. **P1 前端 F5 VIRTUAL guard** - ✅ 后端 + 前端均完成
6. **P1 前端图谱** - ✅ Cytoscape 画布已落地(右键菜单/鸟瞰图/周边聚焦/SVG 导出)
7. **P2 文档治理** - ADR-007/008 已补;ADR-001~006、ICD、CHANGELOG 待补;CLAUDE.md 服务清单已同步
8. **后续回归** - ✅ outbox 驱动同步已落地（2026-07-08, 去 SeaTunnel 化）;孤儿收尾已完成（2026-07-10: ConflictDetector 改 Doris + 删 IndexSyncScheduler/create_action_cdc_pipeline）;对话式本体建模已落地（多轮 + 写工具 HITL + Capability 方法论）;遗留 P2/P3 体验打磨;前端测试体系
9. **Schema 迁移工具链** - ✅ Alembic 已接入（2026-06-29）。业务表 schema 单一真相源 = ORM 模型 + `alembic/versions/` revision 链；旧 `scripts/migrations/` 5 个 SQL 已并入初始 revision 删除；`infra/init-pg-schema.sql` 移除业务表 DDL，只建 gravitino schema + pgcrypto；docker-compose 加 `migrate` init 容器跑 `alembic upgrade head`；本地 `make dev-backend` 自动 migrate。Gravitino 的 `gravitino_store` schema 仍由 `infra/gravitino-pg-schema.sql` 管（Gravitino 不支持自动建表，见 GitHub issue #9013）

---

## 十、apiName 自动推导与命名规范 (v6, 2026-06-26)

对标 Palantir Foundry 三字段模型(rid / apiName / displayName),落地 apiName 推导与命名规范。

### 命名规范

| 字段 | 格式 | 决定者 | 说明 |
|------|------|--------|------|
| rid (Gaia `id`) | Palantir RID `ri.ontology.main.object.{uuid}` | 系统生成 | 永久不变,全局唯一。**2026-07-15 修订**:从裸 UUID 改为 Palantir RID 格式(对齐 Foundry 身份模型,自描述跨服务寻址)。见 [graph-reasoning-design.md §4.1](../architecture/graph-reasoning-design.md) 身份模型说明 |
| Ontology apiName | **PascalCase** (`Airline`) | 用户手填 | 命名空间,URL 可寻址 |
| ObjectType apiName | **PascalCase** (`Flight`) | 前端 LLM 推导+用户可改 | 提交必填,后端校验 pattern+唯一性 |
| Property apiName | camelCase (`flightId`) | 后端纯规则推导 | 从 displayName/backingColumn 推导 |
| Link apiName | camelCase (`assignedTo`) | 后端纯规则推导 | 从 displayName 推导 |
| Action apiName | camelCase (`delayFlight`) | 前端 LLM 推导+用户可改 | 提交必填,后端校验 pattern+唯一性 |
| displayName | 任意 | 用户手填 | 可重名,可改无风险 |

### pattern
- ObjectType/Ontology: `^[A-Z][a-zA-Z0-9]{0,99}$` (PascalCase)
- Property/Link/Action/参数: `^[a-z][a-zA-Z0-9]{0,99}$` (camelCase)
- 常量: `naming.OBJECT_TYPE_API_NAME_PATTERN` / `PROPERTY_API_NAME_PATTERN`

### 推导规则
- **Property/Link**: `core.naming.derive_api_name` 纯规则,优先级 displayName(ASCII)>backingColumn(ASCII)>兜底 prefixN;重名自增数字后缀(`Model1`)
- **ObjectType/Action**: 前端调 `/ai/generate` LLM 推导(中文翻译辅助),用户确认/修改后提交;后端不推导,只校验
- **primary_key/title_property**: 从属性 `is_primary_key`/`is_title_property` flag 反推(Q2),兼容显式 api_name/display_name 引用

### 重名处理
- 推导阶段(Property/Link)重名 → 自动加数字后缀
- 用户手填(ObjectType/Action/Ontology)重名 → 后端 raise ConflictError

### 新增接口
- `POST /ai/generate` — AI SDK `generateText` 等价,非流式,`{instructions, prompt} → {text}`
- `POST /ai/stream` — AI SDK `streamText` 等价,SSE 流式
- `POST /ai/scaffold` — BuildWith: 从数据集 schema 流式脚手架生成 ObjectType 结构（SSE 结构化流式，pydantic-ai Tool Output）；底层 `stream_structured` 通用能力可复用于后续关系推断/语义增强
- 后端不感知任务语义,纯 LLM 原语;每请求新建轻量 `Agent(model, system_prompt=instructions)`
- 前端 `api/ai.ts`: `generateText` / `streamText` / `suggestActionApiName` / `scaffoldObjectType` 便捷封装

### 改名收尾(physical → backing)
- 全链路(后端 schema/ORM/service/route + 前端 types/client/components + benchmark JSON/script)统一为 `backing_mapping`/`backing_catalog`/`backing_schema`/`backing_table`/`backing_column`
- `PhysicalColumnRef` → `BackingColumnRef`

### 验证状态
- 后端 ruff + mypy 通过;754 单测 passed(1 预存 iceberg 失败无关)
- 前端 tsc + vite build 通过(1 预存 OntologySidebar test 错误无关)
- benchmark setup 端到端通过:Ontology `Airline` + 9 ObjectType(PascalCase) + 8 LinkType + 2 ActionType 全部创建成功;Property camelCase 推导 + primary_key flag 反推正确
- `/ai/generate` 真实 LLM 验证:"航班"→`Flight`(PascalCase)、"延误航班"→`delayedFlight`(camelCase)
- `/ai/stream` SSE 流式验证通过

### benchmark 验证 (2026-06-26)
全流程跑通 (00-08)，暴露并修复了多处 apiName 相关问题：
- 02 setup_pipeline: `MANAGED_TABLES` key 改 PascalCase、补 FlightStatusLog 映射;credential/datasource/sync-task/dataset api_name 改 camelCase
- 03 wait_sync: Doris 表名映射改 snake_case (配合 `naming.doris_index_table` snake_case 化)
- `naming.doris_index_table`/`index_pipeline`/`iceberg_s3_location`/`managed_dataset_api_name` 用 `_to_snake` — 物理资源 snake_case 保词界 (架构约束, 2026-06-28 从 `.lower()` 改进)
- testcase YAML: `airline.X` → `Airline.X` (ontology apiName PascalCase);`delay_minutes` → `delayMinutes`、`flight_id` → `flight` (参数名 camelCase/对齐)
- 05_run_write_benchmark: `OBJECT_TYPE = "Flight"` (PascalCase)

**验证结果：**
- 01 setup: ✅ Ontology Airline + 9 ObjectType(PascalCase) + 8 Link + 2 Action
- 02 pipeline: ✅ 8 sync task 全部 RUNNING (FlightStatusLog 表名修复后)
- 04 读 benchmark: ✅ 跑通 (err=0)，性能数据正常 (P95 130-4500ms @10-200 并发)
- 05 写 benchmark: ✅ 12/14 PASS (2 FAIL 是数据同步/hydrate 问题，见遗留)
- 06 安全 benchmark: ⏮ 1 PASS/4 FAIL (权限未配)/5 XFAIL (行级权限未实现)
- 07 agent benchmark: ✅ text_to_ontology 92/105 (87.6%)
- 08 报告: ✅ 生成

**遗留问题（非 apiName 特性范畴，已记录）：**
1. **Doris 数据同步部分修复**: `sync_now` 增加 `dataset_api_name` 参数（Iceberg 表名=dataset api_name，非 object_type.lower()）。Aircraft(500)/Crew(2000) 小表已成功同步到 Doris，读路径验证返回正确数据（apiName camelCase 映射正确）。Flight/Booking 等大表同步失败——**Doris BE 内存不足**（process 945MB/limit 921MB，`MEM_ALLOC_FAILED`），需调大 Doris BE `mem_limit` 配置（环境调优，非代码）
2. **ObjectType-dataset 关联缺失**(✅ 已修复 2026-06-26,见 `docs/bugfix/managed-dataset-governance-record-missing.md`): `define_object_type(_batch)` MANAGED 分支现已 (a) 写入 PG `datasets` 治理记录(`kind=MANAGED`,api_name = `naming.managed_dataset_api_name(ot_api)` 的 **snake_case** 形式,= Iceberg 物理表名) + (b) ~~两个 `register_dataset` 调用点改用同一 snake_case 名注册 Iceberg 表~~ **(2026-07-25 Catalog First 改造)**: 建表改走 `IcebergStore.create_managed_table`(pyiceberg 路径,带主键 identifier/列 doc/required/表 properties),`GravitinoRegistry.register_dataset`(裸 HTTP)不再用于托管表建表;`add_property_to_object_type` 加列时走 `ensure_schema` 演进(不删表不丢 snapshot) + (c) 对未显式提供 `backing_mapping` 的属性自动回填 `dataset_api_name`/`backing_catalog`/`backing_schema`/`backing_table`/`backing_column`(指向对象自有托管数据集,column=property api_name)。显式 `backing_mapping` 原样保留。原 benchmark 01 setup 走的是另一条路径(经 sync task 产生 dataset),该链路不动;本修复针对不经 sync task 直接 define 的场景。多词对象 `FlightStatusLog` Schema 页验证通过(Iceberg 表 `flight_status_log` snake_case,Trino 可达)。> 注: 2026-06-28 命名从 `.lower()`(`flightstatuslog`)改为 `_to_snake`(`flight_status_log`),保词界。
3. **读正确性 18/30 FAIL**: 根因是 Doris 0 行 (问题 1)，读路径返回空。压测 err=0 是因为空结果不报错
4. **write_010 hydrate 失败**: ObjectReference `newAircraft` hydrate 读 Aircraft 数据失败 (Doris 0 行，hydrate 降级路径问题)
5. **sec_001/002/004 权限未配**: 期望 403 但得 200 (权限配置问题)
6. **text_to_sql 0/70**: agent benchmark SQL 生成准确率 0 (对比逻辑或 LLM 问题，待查)

---

## 十一、多源异构数据融合 (multi-source-data-fusion-design.md, 🆕 2026-07-02)

> 设计文档：[`docs/design/multi-source-data-fusion-design.md`](../design/multi-source-data-fusion-design.md)（v1.1，评审决策已固化）
> CDC spike 报告：[`docs/engineer/cdc-spike-report.md`](../engineer/cdc-spike-report.md)

### 后端 — 连接器扩展（P0/P1 已完成）

| 组件 | 状态 | 说明 |
|------|------|------|
| `_JDBC_CONNECTOR_MAP` / `_JDBC_DRIVER_MAP` / `_JDBC_URL_SCHEME` | ✅ | 扩展到国产库（OpenGauss/GaussDB/TiDB/OceanBase/达梦/金仓）+ 云数仓（ADB-PG/GaussDB-DWS）+ 通用 JDBC 兜底（generic_jdbc）。国产库用独立类名驱动 + 独立 URL scheme，规避驱动同名类冲突（§6.1.2） |
| `CAPABILITY_MAP` | ✅ | 从 8 种扩展到覆盖 6 大品类（关系库/湖仓/文件存储/消息队列/NoSQL/云数仓）+ generic，新增 `virtual_table` 能力标记。ES 严格一刀切（决策点 4），达梦/generic_jdbc/MaxCompute 无 virtual_table |
| `GravitinoRegistry._register_typed_catalog` + `register_lakehouse_catalog` / `register_kafka_catalog` / `register_fileset_catalog` | ✅ | 统一 lakehouse/messaging/fileset catalog 注册后端，复用 `register_jdbc_catalog` 模式 |
| `SeaTunnelEngine.create_file_sync_pipeline` (S3File→Iceberg, §6.3) | ✅ | postmortem-verified sink config（catalog-impl + 无 warehouse） |
| `SeaTunnelEngine.create_kafka_ingestion_pipeline` (Kafka→Iceberg, §6.4 path B) | ✅ | STREAMING + Exactly-once + 消费组隔离 |
| `SeaTunnelEngine.create_external_cdc_pipeline` (外部 CDC→Iceberg, §7.3) | ✅ | 支持 MySQL/PG/OpenGauss/TiDB-CDC，显式 PK + upsert-mode 规避 #10747 |
| `DataSourceService._register_datasource_catalog` | ✅ | 按 connector_type 分流：JDBC(provider)/Fileset/Lakehouse/Kafka catalog 注册；provider=None（达梦/generic_jdbc）+ ES 跳过 |
| `DataSourceService.start_cdc_sync` (§7.3.5, post-spike) | ✅ | 创建 sync_mode=cdc 的 SyncTask + 提交 external CDC pipeline，与 start_sync（批量）并列 |
| `POST /api/datasources/{ds}/cdc-sync` 路由 | ✅ | CDC 同步入口端点 |

### 前端 — 连接器目录 UX（P0 已完成）

| 组件 | 状态 | 说明 |
|------|------|------|
| `constants/connectorCatalog.ts` | ✅ | 完整 ConnectorMeta 结构（icon/label/description/category/maturity/capabilities/configSchema/pitfalls），23 个连接器条目，emoji 图标（无图标库依赖，G4 轻量替代） |
| `DataSourceForm` Step 1 改为分品类目录页（§5.2.2） | ✅ | 6 大品类分组 + 搜索 + 能力过滤 + 成熟度徽章 + 能力标签 |
| `DataSourceForm` Step 2 按 configSchema 动态渲染（§5.2.1） | ✅ | 配置字段按 flex 行分组，避坑提示面板 |
| `DataSourceCard` 图标从目录取 | ✅ | 复用 CONNECTOR_META |

### CDC spike（阶段 0，代码就绪，live 待执行）

| 项 | 状态 |
|----|------|
| 代码（pipeline + service + route + 测试） | ✅ 全绿 |
| 技术可行性论证（postmortem 证伪 + #10747 联网确认规避） | ✅ 路径 a 可行 |
| Live 验证（外部 MySQL + binlog 6 步骤） | ⏳ 待运行容器执行 |
| spike 成功后接入主线 | ✅ 接口已预留（SyncTask cdc 模式 + start_cdc_sync） |
| spike 失败兜底 | sync_now 批量同步维持，不阻塞其他连接器 |

### 测试覆盖

- `tests/unit/services/test_datasource_multi_source.py`（49 项）：capability map / URL scheme / driver 解析 / provider=None 路由 / File/Kafka/Lakehouse catalog 注册 / start_cdc_sync / StarRocks jdbc factory dialect
- `tests/unit/layers/test_sea_tunnel_multi_source.py`（12 项）：file/kafka/external-cdc 模板渲染 + postmortem sink config 断言 + kafka start.mode
- `tests/unit/layers/test_gravitino_registry.py`（+5 项）：lakehouse/kafka/fileset catalog 注册
- `tests/integration/test_datasource_routes.py::test_start_cdc_sync`：CDC 端点 HTTP 200
- 前端 `src/components/__tests__/DataSourceForm.test.tsx`（14 项）：目录数据完整性 + 分品类渲染 + 搜索过滤 + 步骤切换 + StarRocks 卡片

### 验收对照（设计 §十一）

- [x] 连接器目录 UX：分品类 + 搜索 + 能力过滤 + 详情（避坑提示）
- [x] 关系库：OpenGauss/GaussDB/TiDB + 通用 JDBC 兜底 + 达梦/金仓/OceanBase/**StarRocks**（catalog 注册 + URL scheme + driver，live explore 待容器；StarRocks 走 Gravitino `jdbc-starrocks` 原生 provider，MySQL 协议，已 live 验证 catalog 注册）
- [x] 湖仓格式：Hive/Delta/Hudi/Paimon 经 Gravitino Generic Lakehouse Catalog 注册
- [x] 文件存储：S3/MinIO/OSS/HDFS Fileset catalog + S3File→Iceberg pipeline ✅ **live 验证**（RustFS + Parquet + CSV，`create_file_sync_pipeline` 端到端跑通；Gravitino fileset catalog 注册 provider="fileset" 非 "s3"）
- [x] Kafka：VIRTUAL（kafka catalog）+ 落地（Kafka→Iceberg pipeline）双通道 ✅ **live 验证**（Kafka→Iceberg 实时流式落地 + earliest 历史消费 + `create_kafka_ingestion_pipeline` 端到端 + Gravitino messaging catalog 注册）
- [x] ES：落地为主（严格一刀切，无 virtual_table）
- [x] 云数仓：ADB-PG/GaussDB-DWS 复用 PG 通道
- [x] CDC spike：✅ **live 验证通过**（全量+增量 CDC upsert+#10747 规避+worker 稳定+schema 演进，`POST /cdc-sync` 端到端，见 `docs/engineer/cdc-spike-report.md`）
- [x] 国产库驱动：✅ **live 验证双侧加载**（opengaussjdbc/kingbase8/oceanbase/达梦，docker-compose 持久化挂载）
- [x] StarRocks：✅ Gravitino `jdbc-starrocks` catalog 注册 + JDBC dialect dry-run（`StarRocks` factory，见 `docs/engineer/starrocks-seatunnel-dryrun.md`）
- [x] 避坑：国产库驱动独立类名、Iceberg sink 无 warehouse、ES 联邦限制前端提示、GaussDB 用 opengaussjdbc（非 gsjdbc200）
- [x] 架构红线：未引入 Redis、VIRTUAL 目标未写入、物理命名 snake_case、未建重型抽象（G4）

> 决策记录见 [ADR-014](adr-014-multi-source-data-fusion-connectors.md)；设计文档实现修正见 [multi-source-data-fusion-design.md 附「实现阶段 Live 验证修正记录」](../design/multi-source-data-fusion-design.md)。

---

## 十二、图关联推理与时空多维分析 (ADR-015, 🆕 2026-07-06)

> 设计文档：[`graph-reasoning-design.md`](./graph-reasoning-design.md) · 进度跟踪：[`graph-reasoning-progress.md`](./graph-reasoning-progress.md) · 前端 v3：[`graph-reasoning-frontend-design-v3.md`](./graph-reasoning-frontend-design-v3.md) · **架构转向：[`adr-015-agent-driven-graph-explore.md`](./adr-015-agent-driven-graph-explore.md)**
>
> 这是 2026-06-18 评审后新增的**重大特性**（265 个提交），对标 Palantir Vertex / Foundry ObjectSet 范式。M0-M7 后端 + 前端 Phase 2a-2h + Phase 3a-3e 全部完成。IR 层对齐 Palantir ObjectSet 87%（13/15 type）。
>
> **⚠️ 架构转向说明（ADR-015, 2026-07-04）**：原 M5/M3c 的 `object_set_parser.py` + `POST /query-nl` 关键词路由 + `explore_plan_parser.py` + `POST /explore-plan` 一次性编排机制**已废弃删除**。原因：explore-plan 在空画布编 5 步计划不基于状态（0 对象空转还编结论）；should_route_to_object_set 关键词路由永远枚举不全（违反红线 8）。改为 **AG-UI ReAct Agent + CanvasSnapshot shared state**：Agent 每轮读画布 state 决策（0 对象自然止损，不编结论），通过 STATE_SNAPSHOT 驱动画布。`graph-reasoning-progress.md` 记录的是转向前的历史状态，以本节 + ADR-015 为准。

### 12.1 后端 M0-M7

| 里程碑 | 状态 | 说明 |
|--------|------|------|
| **M0 基础设施** | ✅ | docker-compose PG 换一体镜像 `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6`（PostGIS 3.6.1 + TimescaleDB 2.24.0）+ Neo4j 5-community（profile=graph）；Alembic 迁移 `a1b2c3d4e5f6`（link_types 加 weight_property/temporal + analysis_records 新表）；`core/naming.py` 扩展 graph_label/graph_relationship_type/geo_table/timeseries_hypertable（52 测试） |
| **M1 Graph Layer** | ✅ | `Neo4jGraphStore`（Cypher 收口 + `upsert_nodes_batch`/`upsert_edges_batch` UNWIND+CALL{} IN TRANSACTIONS 批量 + `cleanup_stale_virtual` watermark 清孤儿）+ `GraphProjector`（object_state/links → Neo4j，仅 indexed 属性；**ADR-021 扩展**：识别 `_virtual`/`_source_ref`/`_sync_tag` 元标记写身份骨架节点）+ `rebuild_for_object_type`（**未接线**）；OntologyService.define 触发 `_provision_graph_schema`（受 `capabilities.graph_indexing_enabled` 门控，best-effort）；**数据投影已接线**：① 节点投影—OutboxExecutor INDEX effect 侧用 outbox payload properties 调 project_object（capabilities 门控，fail-tolerant）；② 边投影—ActionService Step 11 commit 后 RELATE→project_link / UNRELATE→delete_link（capabilities 门控，fail-tolerant）；③ 外部数据路径已接线—ObjectIndexFunnel 从 Iceberg scan_latest 读外部接入数据调 project_object（手动 rebuild via `POST /admin/project/rebuild/*`；SeaTunnel backfill 完成自动触发链路待接）；④ **VIRTUAL 联邦投影已接线 (ADR-021, 2026-07-16)**—ObjectIndexFunnel.project_for_virtual_object_type 旁路 Gate 1，Trino 游标分页拉外部源表骨架列 → 合成 object_state（带 `_virtual` 元标记）→ Neo4j 批量 MERGE 节点 + FK→边投影（一端 VIRTUAL 走 PG PK→rid 反查 / 两端 VIRTUAL 走内存 join）→ watermark 清孤儿；register_virtual_table 成功后 asyncio.create_task 异步触发 + `POST /admin/project/rebuild-for-virtual/{ont}/{ot}` 手动 rebuild；VIRTUAL 节点 best-effort + 不可对账，不参与 ConflictDetector（M1 不含 PR 0 权限注入 + 二期 PR 5，见 §12.7 路标 #15/#16） | |
| **M2 GeoTime Layer** | ✅ | `GeoTimeStore`（PostGIS + TimescaleDB 合并封装）+ `GeoTimeProjector`（**节点投影已接线**）；OntologyService.define 触发 `_provision_geotime_schema`（受 `capabilities.geotime_indexing_enabled` 门控，含空间属性建 PostGIS 表，含时序属性建超表）；SeaTunnel Kafka→TimescaleDB sink（`create_kafka_timeseries_pipeline` + `start_timeseries_sync` + `POST /datasources/{ds}/timeseries-sync`）；**节点投影已接线**：OutboxExecutor INDEX effect 侧用 outbox payload properties 调 project_object（capabilities 门控，fail-tolerant）；外部数据路径已接线—ObjectIndexFunnel 从 Iceberg scan_latest 读外部接入数据调 project_object（手动 rebuild via `POST /admin/project/rebuild/*`；SeaTunnel backfill 完成自动触发链路待接） | |
| **M3 ObjectSet IR + 编排中枢** | ✅ | `core/schemas/object_set.py`（ObjectSet IR 判别联合，对齐 Palantir）+ `DataFrameQueryService`（`object_set_executor.py`，递归求值 IR 树 + filter 分流属性→PG/空间→PostGIS/时序→TimescaleDB + searchAround→Neo4j + EvidenceChain 证据累积 + 水合分批 5000 + **field 白名单校验 P2**）；26 新单测 + 真实端到端（供应链中断传导示例多引擎联动） |
| **M4 工具与 API** | ✅ | `tools/toolsets/reasoning.py`（query_with_dataframe，**ADR-015：返回 ToolReturn 双职分离 — return_value 给 Agent + StateSnapshotEvent 给画布**）+ traverse_link/exists_link 实现（替换 TOOL_NOT_IMPLEMENTED）+ `find_paths`（第 22 工具）；REST 路由（query-dataframe/object-set/traverse/exists-link/find-paths/spatial-filter/series-query/analysis）；AG-UI + MCP 双协议暴露 |
| **M5 NL 查询入口** | ✅ | **ADR-015 转向后**：`query-nl` 路由 + `object_set_parser.py` + `should_route_to_object_set` 关键词路由**已删除**。所有 NL 查询统一走 `/ai/agent`（AG-UI ReAct Agent），Agent 自行决定调 query_with_dataframe / traverse_link / switch_view / color_by。**不在 `/objects/*` 补 NL 端点**（对齐 Palantir 两层正交：Ontology API 不吃 NL，见 CLAUDE.md 红线 11 + ADR-015 D4 修订）。脚本/外部 Agent 走 MCP `query_with_dataframe` 工具 |
| **M6 证据链** | ✅ | `AnalysisRecordStore`（save/get 证据链快照）+ DataFrameQueryService.execute `_save_evidence`（best-effort）+ `GET /objects/{ont}/analysis/{id}` 路由；3 新单测 + 真实端到端 |
| **M7 风险评分** | ✅ | 无需新代码，用既有 Action 闭环（RiskScore 属性 indexed=True 自动同步 Neo4j，recalculateRisk Action 定期执行写 object_state） |

#### 12.1.1 已知 MVP 限制（P3 标注）

| 项 | 现状 | 优化方向 | 路标 |
|----|------|---------|------|
| **_hydrate 水合源** | 走 PG `object_state`（MVP，`get_object_states_by_rids` 批量取） | 大规模下应切 Doris 倒排索引（CLAUDE.md 红线 4：Doris 在线读主源） | #10 |
| **field 白名单校验** | ✅ 已实现（P2，2026-07-06）：`_eval_filter` / `_eval_where` 入口校验 field 在本体 properties 内，不在则 raise ValidationError（列可用属性）；空间算子豁免 | — | 已完成 |

### 12.2 IR 层对齐 Palantir ObjectSet（87%，13/15 type）

调研 Palantir `foundry-platform-python` v2 SDK，ObjectSet 实际有 15 种 type，多轮补齐：

| Palantir ObjectSet type | 状态 | 说明 |
|--------------------------|------|------|
| objectType / static / filter / searchAround | ✅ | 基础集 + 过滤 + 图遍历 |
| union / intersect / subtract | ✅ | 集合运算（Ibis union/intersect/difference） |
| aggregate / select | ✅ | 聚合（group_by + count/sum/avg/min/max）+ 投影 select_fields |
| interfaceBase / interfaceLinkSearchAround | ✅ | Interface 跨类型查询（ObjectTypeInterfaceModel 关联表 + Alembic 迁移） |
| order_by + cursor 分页 | ✅ | 多字段+desc + 后端 cursor + 前端「加载更多」 |
| where 嵌套逻辑组合 | ✅ | WhereClause 判别联合（Filter/And/Or/Not），递归编译一条 SQL 下推 |
| filter op 9→16 种 | ✅ | notEqual/in/notIn/greaterThan/lessThan/startsWith/endsWith |
| withProperties / reference | 🟡 | schema 占位 + executor 抛 NotImplementedError（需表达式引擎/持久化存储） |
| nearestNeighbors | ❌ 二期 | 需 Doris 向量索引或 Neo4j GDS |
| asType / asBaseObjectTypes | ❌ 二期 | 类型转换 |
| methodInput | ❌ 二期 | Action 方法输入 |

### 12.3 执行层重构：Ibis 临时表模式（设计 §7.4）

- `_eval_filter` 有 engine 时走 `_eval_filter_sql`：候选 vids 注册 PG 临时表，所有 filter 编译进一条 SQL 下推（PG 优化器自决 join 策略）
- `_compile_attr_pred` / `_compile_spatial_pred` / `_compile_time_pred`（纯函数编译器）
- 多 filter 链式一条 SQL（属性+空间+时序混合，1 次 RTT）
- R2 过时结论修正：Ibis 10.8 下 5 万行 memtable + PG join 正常
- **P2 field 白名单（2026-07-06）**：`_eval_filter` / `_eval_where` 入口校验属性/时序算子的 field 在本体 properties 白名单内，拼错属性名 raise ValidationError（列可用属性），不再静默返回空。空间算子豁免（field 是几何列）。空白名单（本体无 OT）跳过校验

### 12.4 前端 Phase 2a-2h + Phase 3a-3e

> **ADR-015 转向后**：Phase 3c 的 `explore_plan_parser.py` + `usePlanExecutor` + `POST /explore-plan` 一次性编排**已删除**，改为 AG-UI ReAct Agent + `useGraphExploreAgent` + `AssistantUiChat`。下表反映真实状态。

| Phase | 状态 | 说明 |
|-------|------|------|
| 2a 图探索 MVP | ✅ | Cytoscape 画布（增量 diff + 右键 cxtmenu + 鸟瞰图 + fcose 布局）+ useGraphExplore（撤销栈 + LOD）+ GraphExplorePage（顶栏/画布/侧栏/底栏）+ `/explore` 路由 |
| 2b 空间时空分析 | ✅ | MapLibre GL 地图 + marker + 框选过滤 + TrajectoryPlayer 轨迹回放 + 视图切换（图谱/地图/分屏）；headless 无 WebGL 降级列表已验证 |
| 2c 高级 | ✅ | 侧栏三 tab（选中/图层/分布）+ LayersPanel（着色/节点大小 + localStorage 持久化）+ HistogramPanel（属性分布筛选） |
| 2d 路径推理 | ✅ | PathFinder（源/目标下拉 + max_depth + 路径序列展示） |
| 2e 分析→行动闭环 | ✅ | useActionTrigger（列适用 Action + 过滤 ACTIVE/VIRTUAL）+ refreshNode（read-your-writes 刷新）+ ExecuteActionDialog 预填 rid |
| 2f 多步 Search Around | ✅ | useSearchAroundConfig（链式嵌套 IR 构建 + 预览防星爆）+ SearchAroundConfigPanel |
| 2g 全局时间轴 | ✅ | useTimeFilter + TimeScrubber（双滑块 + 预设 1h/24h/48h/7d + 播放 + 仅活跃实体） |
| 2h 多布局 | ✅ | fcose / dagre / circle / grid 切换 |
| **3a 对话式空状态** | ✅ | ExploreLanding（中央对话框 + 4 场景卡片 + 本体知识提示）+ mode 状态机（landing/exploring）+ handleAsk 切到 exploring 传首问给 AG-UI Thread |
| **3b 对话流（AG-UI Agent）** | ✅ | **ADR-015**：`useGraphExploreAgent`（HttpAgent 子类，拦截 STATE_SNAPSHOT 驱动画布）+ `AssistantUiChat`（复用 assistant-ui runtime）。替代旧 useConversation + usePlanExecutor 三套机制 |
| **3c AI 自动编排（AG-UI ReAct）** | ✅ | **ADR-015 灵魂**：AG-UI ReAct Agent（pydantic-ai）每轮读 `ctx.deps.state`（CanvasSnapshot）决策——调 query_with_dataframe 加载数据 → 看 object_count → 0 对象自然终止不编结论 / 有对象继续 traverse_link / switch_view / color_by。工具返回 ToolReturn 双职分离（return_value 给 Agent + StateSnapshotEvent 给画布）。**替代已删的 explore-plan 一次性编排**。真实验证"分析供应链中断风险"→Agent ReAct 多步执行全绿 |
| 3d 场景模板 + URL 预填充 | ✅ | 4 场景卡片（模板只预填对话问题，走 AG-UI Agent）+ URL `/explore/:ont?objects=&view=&question=` |
| 3e 高级模式 | ✅ | 💬/⚙ 切换对话流显隐，技术用户切回完整 v2 控件 |

### 12.5 闭环修复（3 轮架构审查 + ADR-015 转向）

- **第 1 轮**（P0 数据正确性）：range 算子字符串比较 bug → ::numeric；timeRange 过滤空壳 → 真过滤；traverse_link Neo4j 无降级 → PG object_links 降级；source_to_target_map 多源 bug → 按源分组
- **第 2 轮**（P1 并发/性能/一致性/翻页）：_eval_object_type 全量拉取 → 只取 id；next_cursor 翻页未实现 → execute cursor 参数 + 前端「加载更多」；编排并发无防护 → abortedRef + abort()
- **ADR-015 转向**（2026-07-04）：废弃 explore-plan 一次性编排 + should_route_to_object_set 关键词路由，改 AG-UI ReAct Agent + CanvasSnapshot shared state。根因：explore-plan 空画布编 5 步不基于状态（0 对象空转编结论）+ 关键词路由枚举不全（违反红线 8）
- **接口层 JSON 契约对齐**：前端 ObjectSetIR 加 9 种新 type + where + group_by/aggregations/select_fields；GraphFilter op 9→16；工具层/MCP/AG-UI docstring 全部同步

### 12.6 测试与验证

- **后端**：~1094 测试全过（含图/时空/ObjectSet + P2 field 白名单 5 新测），ruff/mypy/alembic 干净
- **前端**：22 文件 169 测试全过（+21 图探索测试），tsc + vite build 通过
- **真实端到端**：图探索 / Search Around / 证据链 / 地图降级 / 三 tab / 路径推理 / Action 闭环 / 多步 Search Around / **AG-UI ReAct Agent 自动编排** / 场景模板 / URL 预填充 全部验证通过

### 12.7 后续路标（图关联推理遗留）

| # | 项 | 阶段 | 说明 |
|---|----|------|------|
| 1 | nearestNeighbors | 二期 | KNN/向量检索（需 Doris 向量索引或 Neo4j GDS） |
| 2 | withProperties | 二期 | 派生属性（需表达式引擎，当前 schema 占位 + NotImplementedError） |
| 3 | reference | 二期 | 引用持久化 ObjectSet（需 ObjectSet 存储，当前 schema 占位） |
| 4 | asType / asBaseObjectTypes | 二期 | 类型转换（ObjectType → Interface/基类） |
| 5 | methodInput | 二期 | Action 方法输入（与 Action 闭环深化） |
| 6 | Substrait 对接 | 二期 | ibis-substrait 标准化 PG 侧查询计划（当前手写 SQL 够用） |
| 7 | 全链路血缘审计 | 二期 | lineage 体系 + 审计包导出（需全链路追踪基础设施） |
| 8 | 实体对齐 | 二期 | 手动合并 + 自动对齐 ML（需 ML 模型 + 对齐工作流） |
| 9 | Interface CRUD 路由 | 后续 | metadata 层已实现，但无 REST 端点暴露（当前只能通过代码/脚本建 Interface） |
| 10 | 推理线 Doris 水合 | 后续 | **当前 _hydrate 走 object_state（MVP 限制）**，大规模下应切 Doris 倒排索引（CLAUDE.md 红线 4）。见 §12.1.1 |
| 11 | 对话式建模体验打磨 | 后续 | **多轮对话式本体建模已落地**（Sprint 2 + commit 584af2c：AG-UI Thread 多轮 + 写工具 HITL + Capability 方法论）。遗留 P2/P3 体验打磨：Capability 按需加载改条件注入（避免 LLM 漏 load_capability）+ prepare_tools 按场景过滤（缓解 tool selection 退化）+ 参数契约规范化。见 [ontology-modeling-e2e-review.md](./ontology-modeling-e2e-review.md) |
| 12 | switch_view/color_by 迁移为 frontend-defined tool | 后续 | ADR-015 §后续工作：当前是后端 toolset（只写 state 不查数据），迁移后 MCP/REST 不暴露纯 UI 工具 |
| 13 | 场景模板自定义 | 后续 | 用户保存自己的探索为模板（localStorage） |
| 14 | ~~NL → IR → JSON 独立 REST 端点~~ **不补（范式对齐）** | ✅ 已决策 | 经 Palantir 范式调研确认：不在 `/objects/*` 加 NL 端点，对齐 Foundry 两层正交（Ontology API 不吃 NL，NL→IR 在 AIP Agent 层）。脚本/外部 Agent 走 MCP `query_with_dataframe` 工具或 `/ai/agent`。约束固化为 CLAUDE.md 红线 11 + ADR-015 D4 修订，避免重蹈「为每场景开端点」覆辙 |
| 15 | **DataFrameQueryService 权限注入 (ADR-021 PR 0)** | P0 待做 | `DataFrameQueryService.__init__` 未注入 `AuthorizationService`（container 工厂也未传），图遍历入口（`_eval_search_around`/`_eval_find_paths`/`exists_link`）未调 `check_access`。后果：无权用户可经 searchAround/findPaths 探测无权 OT 存在性 + 拿到 rid 再经水合泄露数据。MANAGED + VIRTUAL 两路径均裸奔。修复：注入 + 图遍历入口 OT 级 `check_access`（防泄露存在性）。横切关注点，与 VIRTUAL 投影正交。见 [virtual-graph-projection-design.md](./virtual-graph-projection-design.md) §5 PR 0 |
| 16 | **VIRTUAL 投影二期 (ADR-021 PR 5)** | 二期 | ① indexed 属性定时刷新（lifespan 后台任务分钟级，当前需手动 rebuild）；② VIRTUAL 水合行级权限（Cedar TPE → Trino WHERE 下推，当前仅 OT 级，无行级）；③ `foreign_key_property_api_name` 缺失时从外部源 schema FK 自动推断回填（当前 FK 缺失降级为只投影节点不投影边）。见 [virtual-graph-projection-design.md](./virtual-graph-projection-design.md) §5 PR 5 |

---

## 十三、文档同步校准记录 (2026-07-06)

本次同步修正的过时项（基于实际代码核查：101 个后端 .py 源文件 / 27,756 行；130 个测试文件 / 34,711 行 / 1268 个测试函数；79 个前端组件；22 个 Service；8 个 Layer；22 个工具；11 个 ADR 实体）：

| 文档 | 过时项 | 修正 |
|------|--------|------|
| implementation-status.md | §一 6 Layer | 改为 8 Layer（+ Graph / GeoTime） |
| implementation-status.md | §二 18 Service | 改为 22 Service（+ GraphProjector / GeoTimeProjector / DataFrameQueryService / AnalysisRecordStore） |
| implementation-status.md | §三-bis 18/19 工具 + traverse_link/exists_link 骨架 | 改为 22 工具，traverse_link/exists_link/find_paths 已实现 |
| implementation-status.md | 缺图关联推理整章 | 新增 §十二（M0-M7 + IR 对齐 + 前端 Phase 2a-3e + 闭环修复 + 路标） |
| implementation-status.md | §六 ADR 清单 | 补 ADR-015 实体 |
| architecture_overview.md | 头部统计（614 用例 / 10,807 行 / 6 Layer / 10 Service） | 改为 1268 用例 / 27,756 行 / 8 Layer / 22 Service |
| architecture_overview.md | §1 架构总览图（6 Layer） | 补 Graph / GeoTime Layer |
| architecture_overview.md | §2 组件版本矩阵 | 补 Neo4j / PostGIS / TimescaleDB；PG 镜像改 timescaledb-postgis 一体镜像 |
| architecture_overview.md | §5 Layer 实现总览 | 补 Neo4jGraphStore / GeoTimeStore 行 |
| architecture_overview.md | §6 Service 编排总览（10 个） | 改为 22 个，补图/时空/ObjectSet/Analysis |
| architecture_overview.md | §8 Docker Compose 9 服务 | 改为 11 服务（+ Neo4j profile=graph + migrate init 容器） |
| architecture_overview.md | §13.4 ADR 索引（到 008） | 补到 ADR-015 |
| CLAUDE.md | 目录结构（6 Layer / 18 Service / 18 工具） | 改为 8 Layer / 22 Service / 22 工具，补 graph/geotime/object_set_executor/analysis_record_store/canvas_control/reasoning |
| CLAUDE.md | ADR 索引（到 014） | 补 ADR-015 |
| CLAUDE.md | docker-compose 服务表（9 服务） | 补 Neo4j + timescaledb-postgis 一体镜像 + migrate |
| CLAUDE.md | 实施路线 | 补图关联推理特性行 |

### 二次校准（2026-07-06，P0/P2/P3）

query_with_dataframe 能力审查发现文档与代码不一致 + 安全/体验缺口，二次修正：

| 项 | 类型 | 修正内容 |
|----|------|---------|
| implementation-status.md §十二 M5/M3c/Phase 3c | **P0 文档修正** | 原描述的 `object_set_parser.py` + `POST /query-nl` + `explore_plan_parser.py` + `POST /explore-plan` + `usePlanExecutor` 是 ADR-015 **已废弃删除**的旧架构。改为真实架构：AG-UI ReAct Agent + CanvasSnapshot shared state + `useGraphExploreAgent` + `AssistantUiChat`。§十二 顶部加 ADR-015 转向说明 |
| graph-reasoning-progress.md | **P0 文档修正** | 顶部加 ADR-015 转向声明，标注正文为历史记录，以 implementation-status §十二 + ADR-015 为准 |
| CLAUDE.md / architecture_overview.md | **P0 文档修正** | 清理 query-nl / explore-plan / object_set_parser / explore_plan_parser / usePlanExecutor 残留描述（目录注释 / Service 清单 / 架构图路由列表 / Route 表） |
| `object_set_executor.py` field 白名单校验 | **P2 代码实现** | 新增 `_load_allowed_fields` + `_validate_filter_fields` + `_validate_where_fields` + `_check_field`。`execute` 入口一次性加载本体 properties 白名单，`_eval_filter` / `_eval_where` 入口校验属性/时序算子的 field 在白名单内，不在则 raise ValidationError（列可用属性前 20 个）。空间算子豁免（field 是几何列）。空白名单跳过校验（兼容边界 + 测试）。消除「拼错属性名静默返回空」+ 安全差基线（红线 8 标识符白名单）。5 新单测 |
| implementation-status.md §十二.1.1 + 路标 #10 | **P3 文档标注** | 明确标注 `_hydrate` 走 object_state 是 MVP 限制，大规模下应切 Doris 倒排索引（红线 4） |
| implementation-status.md §十二.3 | **P2 文档同步** | 补 P2 field 白名单实现说明 |
| implementation-status.md §十二.7 路标 #14 | **P1 范式固化** | 经 Palantir 范式调研确认**不补** query-nl 端点，对齐 Foundry 两层正交架构。约束固化为 CLAUDE.md 红线 11（Ontology API 层不吃 NL）+ ADR-015 D4 修订。避免重蹈「为每个消费者场景开端点」的功能思维覆辙 |
| 工具层 #2 技术债关闭 + 路径③ 遗留清理（2026-07-13） | **代码 + 文档同步** | 核查发现「参数化查询绑定 + ot.properties 白名单」在两条生产路径（TextQL 编译器 `sql_compiler.py` literal→`?` + params；ObjectSet IR `object_set_executor.py` `:param` + `_validate_filter_fields` 白名单）**早已落地**，文档高估了剩余工作量。真正未参数化的是 `DorisIndexStore.query`/`load_by_filter`/`aggregate` + `_build_filter_clause` 字面量拼接——但 grep 确认这组方法**生产零调用方**（生产读统一走 `execute_sql` 参数化路径），是孤儿代码。执行方案 B：删除这 4 方法 + `IndexFilter`/`IndexQuery`/`IndexResult` schema + 16 个孤儿单测 + 1 个集成测试改造（`test_full_chain` 改走 `fake_doris.tables` 直接断言）。同步更新：ICD-04 v1.1（删方法 + 变更记录）、CLAUDE.md（场景 2 流程图 + ICD-04 基线）、implementation-status §一/§二/工具层#2（标 ✅）、architecture_overview（3 处架构图）、ontology-tool-layer §7.2/§八表格（标已解决）。1573 单测全绿 |

### 三次校准（2026-07-16，ADR-021 VIRTUAL 图投影）

VIRTUAL 对象图投影（ADR-021）实现核查发现 implementation-status.md 未反映该特性真实状态（PR 1-4 + 5a 已落地，PR 0 未做，PR 5 二期未做），补齐：

| 项 | 类型 | 修正内容 |
|----|------|---------|
| implementation-status.md §二 ObjectIndexFunnel 行 | **文档同步** | 补 VIRTUAL 联邦投影链路：`project_for_virtual_object_type`（旁路 Gate 1，Trino 游标分页拉骨架→合成 object_state 带 `_virtual`/`_source_ref`/`_sync_tag`→Neo4j 批量 MERGE→FK→边投影→watermark 清孤儿）+ register_virtual_table 异步触发 + `rebuild-for-virtual` admin 路由；标注 PR 0 权限注入未完成 + 二期 PR 5 未做 |
| implementation-status.md §二 GraphProjector 行 | **文档同步** | 补 ADR-021 扩展：project_object 识别 `_virtual`/`_source_ref`/`_sync_tag` 元标记写身份骨架节点 + cleanup_stale_virtual watermark 清孤儿；FK→边由 ObjectIndexFunnel 投影 |
| implementation-status.md §三 `/admin/*` 路由行 | **文档同步** | 补 `POST /admin/project/rebuild-for-virtual/{ont}/{ot}`（ADR-021，Trino 联邦拉骨架→Neo4j，幂等 + 孤儿清理） |
| implementation-status.md §十二 M1 | **文档同步** | 补④ VIRTUAL 联邦投影链路（ADR-021）：ObjectIndexFunnel.project_for_virtual_object_type 旁路 Gate 1，Trino 游标分页→Neo4j 批量 MERGE 节点 + FK→边投影→watermark 清孤儿 + register_virtual_table 异步触发 + rebuild-for-virtual 路由；Neo4jGraphStore 补 upsert_nodes_batch/upsert_edges_batch/cleanup_stale_virtual；VIRTUAL 节点 best-effort + 不可对账不参与 ConflictDetector |
| implementation-status.md §十二.7 路标 #15 | **P0 安全债标注** | 新增：DataFrameQueryService 未注入 AuthorizationService（container 工厂也未传），图遍历入口无 OT 级 check_access，无权用户可经 searchAround/findPaths 探测无权 OT 存在性 + 泄露数据。MANAGED+VIRTUAL 两路径均裸奔 |
| implementation-status.md §十二.7 路标 #16 | **二期标注** | 新增：ADR-021 PR 5 三项（indexed 定时刷新 / VIRTUAL 行级权限 Cedar TPE→Trino WHERE / FK 自动推断回填） |

---

## 十四、待实现重大特性（设计备忘）

以下特性已在 Palantir 范式调研中识别为关键缺口，但尚未进入开发阶段。此处记录设计要点，作为后续启动时的输入。

### 14.1 数据血缘与来源追踪（Data Lineage & Provenance）

> 对应 Palantir Foundry 的 Data Lineage 与 Data Provenance 能力。
> 关联路标：§十二.7 #7「全链路血缘审计」。

**核心问题**：当前 Gaia 数据经过多段管道（外部源→SeaTunnel→Iceberg→ObjectIndexFunnel→Doris / outbox INDEX effect→Doris），但全程不记录"数据从哪来、经过哪些转换、谁拥有它、哪个系统是权威来源"。数据治理缺乏可追溯性。

**需追踪的维度**：

| 维度 | 说明 | 示例 |
|------|------|------|
| 系统来源 | 数据原始产生于哪个外部系统 | CRM、ERP、IoT 传感器、第三方 API |
| 区域归属 | 数据所属的地理区域或合规域 | cn-shanghai、eu-west-1 |
| 业务单元 | 数据归属的组织/业务部门 | 销售部、供应链、财务部 |
| 原始记录 | 数据在源系统的原始标识和值 | 源表名 + 源主键 + 原始字段值 |
| 加工映射 | 从源数据到本体对象的转换链路 | CRM.customer → Iceberg.ontology.customer → idx_ontology__customer (Doris) |
| 写回能力 | 本体对象的修改是否可以反向同步到源系统 | 只读 / 双向同步 / 需审批写回 |
| 数据所有权 | 谁对该数据负责（owner / steward） | Principal 或 Group |
| 权威来源 | 当多个源提供同类数据时，哪个是权威的 | CRM 是客户数据的权威源，ERP 是订单的权威源 |

**设计要点**：

1. **对象级来源标注**：每个 ObjectType 记录 `source_system`（来源系统标识）、`authoritative_source`（是否权威源）、`write_back_enabled`（是否支持写回）
2. **属性级映射追踪**：每个 Property 的 `physical_mapping` 应记录从哪个源表/源字段映射而来，支持多源合并时的字段级溯源
3. **管道血缘图**：记录数据从外部源→Iceberg→Doris 的完整管道链路（SeaTunnel job → Iceberg table → ObjectIndexFunnel → Doris index table → ObjectType），支持可视化
4. **对象实例来源**：每条 object_state 记录其原始来源（source_record_id、ingested_at、pipeline_run_id），支持从本体对象追溯到源系统原始记录
5. **权威来源冲突检测**：当多个源提供同一对象类型的数据时，按 authoritative_source 优先级处理冲突，非权威源的更新需标注
6. **血缘 API**：提供 `/lineage/{object_type}` 查询数据管道链路，`/lineage/{object_type}/{rid}` 查询单个对象的来源追溯

**当前状态**：`DatasetModel` 已有 `source_system` 字段雏形，但仅记录数据集级别的来源信息，未扩展到对象类型/属性/实例级别。pipeline 链路信息仅存在于 SeaTunnel job 配置中，未结构化存储。

### 14.2 函数（Functions）

> 对应 Palantir Foundry 的 Ontology Functions（Phonograph Functions）。
> 关联路标：§四 #8「函数族 / 场景族」。

**核心问题**：当前 Action 只能做简单的参数→效果映射，无法封装复杂业务规则（如"计算客户 LTV"需要聚合历史订单、应用衰减因子、结合风控评分"）。业务逻辑散落在前端 / AI Agent prompt / Action 参数中，不可复用、不可测试、不可治理。

**Functions 的定义**：

> Functions 是封装业务规则的**执行单元**。它将离散的业务规则转化为可组合、可复用的模块，是业务动作的技术实现载体。

**与 Action 的关系**：

| | Action | Function |
|------|--------|----------|
| 面向 | 用户操作（"创建订单"） | 业务计算（"计算订单总价"） |
| 输入 | 用户提供的参数 | 对象引用 + 参数 |
| 输出 | 对象状态变更 | 计算结果（值/对象/集合） |
| 副作用 | 有（写 object_state） | 无（纯计算，或仅写缓存） |
| 调用方 | 前端 / API | Action / Agent / 其他 Function |

**设计要点**：

1. **函数定义模型**：`FunctionType` 包含 `api_name`、`display_name`、`input_object_types`（输入对象类型）、`input_parameters`（静态参数）、`output_type`（返回值类型：scalar / object / object_set）
2. **函数体执行**：
   - **方案 A**：Python 沙箱（CoW 隔离 + 白名单库），用户编写 Python 函数
   - **方案 B**：声明式 DSL（类似 ObjectSet IR 的函数组合语法）
   - **方案 C**：SQL 函数（在 Doris/Trino 上注册 UDF）
   - 初期推荐 **方案 B**（声明式），与 ObjectSet IR 保持一致的表达力，避免沙箱安全风险
3. **函数注册与发现**：
   - 函数注册到 ObjectType 上（类似 Action），前端按对象类型展示可用的函数
   - Agent 可通过 tool calling 发现和调用函数
4. **函数组合**：
   - 函数可以作为 ObjectSet IR 的 `select` 字段（类似 SQL 计算列）
   - 函数可以作为 Action 的 `ValueSource.EXPRESSION` 调用
   - 函数可以嵌套调用（`fn_a(fn_b(obj), params)`）
5. **沙箱与安全**：
   - 函数执行需要沙箱隔离（CoW / gVisor / Firecracker）
   - 白名单库（禁止文件 IO、网络访问），允许 pydantic / numpy / 内置类型
   - 超时控制 + 内存限制
6. **测试与版本**：
   - 函数应有独立的测试框架（输入→预期输出）
   - 函数版本化管理（类似 ActionType.version）

**当前状态**：ActionType 已实现，但 Function 抽象未建立。Agent 调用 `execute_action` 时若需要复杂计算，只能在 prompt 中描述规则让 LLM 推理，不可靠且不可复现。

**优先级建议**：先落地声明式 DSL（方案 B），覆盖 80% 的常见场景（聚合、过滤、计算派生字段）。Python 沙箱（方案 A）作为后续增强，给高级用户自定义能力。

---

### 14.3 其他远期特性

以下特性已有路标条目但未展开设计备忘录，在此记录当前状态：

| 特性 | 路标 | 说明 |
|------|------|------|
| 实体对齐（Entity Resolution） | §十二.7 #8 | 手动合并 + 自动对齐 ML；需 ML 模型 + 对齐工作流 + 人工审核界面 |
| Interface CRUD 路由 | §十二.7 #9 | metadata 层已实现，无 REST 端点暴露 |
| 语义检索（Semantic Search） | §四 #7 | ~~依赖 Doris 4.0.5 向量索引成熟度~~（已证伪，见下）。Doris 4.x ANN 索引已生产可用（HNSW/IVF + `inner_product_approximate`），底层 `DorisIndexStore` 已有 `create_semantic_table`/`vector_search` 等 API；TextQL 引擎B 向量召回已用。真正缺口见 §14.4（管道内变换能力 + 对象实例语义检索工具/IR type） |

---

### 14.4 管道内变换能力与语义检索落地（设计备忘，🆕 2026-07-13）

> 本节记录两个耦合的遗留任务：（1）管道内变换能力（Pipeline Transform）缺失；（2）对象实例语义检索未暴露。两者关系：语义检索的 embedding 生成依赖管道内变换能力，因此（2）的正式方案依赖（1）的落地。（1）作为架构能力补齐独立成立，不仅为语义检索服务。

#### 背景：Palantir 范式与 Gaia 现状的差距

**Palantir Foundry** 的数据流是「通用数据变换管道」：
```
源数据集 → Pipeline Builder（Spark/Pandas transforms + 算子库）
           ↳ Text to Embeddings（调 embedding 模型生成向量列）
           ↳ 字段映射 / 过滤 / 拼接 / 聚合 / 自定义 UDF
         → 带 embedding 列的输出数据集
         → ObjectType backing dataset（Vector 属性映射到 embedding 列）
         → Object Storage 索引
```
Palantir 的 embedding 在**管道内部**生成（`Text to Embeddings` 是 Pipeline Builder 的一个变换算子），输出数据集直接带 embedding 列，ObjectType 只需把 Vector 属性映射到该列。

**Gaia 现状** 的数据流是「配置驱动 source→sink 搬运」：
```
外部源 → SeaTunnel pipeline（source→transform→sink 直连）
         ↳ transform 阶段已存在（FieldMapper/Sql 等内置算子）
           但仅支持声明式字段映射/SQL，不支持调外部模型推理
         → Iceberg 表（只有原始列，无 embedding 列）
         → IndexSyncService.sync_now（Python 编排层）
           ↳ 这是当前唯一能调 OnnxEmbeddingProvider 的位置
         → Doris idx 表
```

**核心差距**：SeaTunnel 的 `transform {}` 阶段（ADR-002 已选型为 source→sink 声明式 ETL）在 Gaia 当前用法里只接了声明式字段映射/SQL 变换，**未接入模型推理类变换**。这导致所有「需要计算派生列」的场景（embedding 是最典型代表，也包括 ML 打分、文本清洗、字段级 NLP 等）只能落到 Python 编排层（IndexSyncService / outbox executor）代劳，无法在管道层统一处理。

> **⚠️ 能力现状校准（2026-07-13）**：经核查，**SeaTunnel 2.3.13 已原生支持 Embedding Transform Plugin**（`[Feature][Transform-V2] Support multimodal embeddings` #9673，含 text/image/video）+ **LLM Transform Plugin**（数据清洗/标注/推断），两者均在 Transform-V2 体系下，支持 `model_provider`（OPENAI / AMAZON / QIANFAN / ZHIPU 等）+ `api_key` + `api_path` 配置，在管道内调外部模型 API 产出向量/文本列。即「管道内 Text-to-Embeddings」这个能力**上游已具备，Gaia 未接线**，不是需要从零自建。遗留任务 1 的性质因此从「能力缺失需新建」修正为「能力已存在需接入 + 适配」（见下）。

#### 遗留任务 1：管道内变换能力（Pipeline Transform）

**问题陈述**：SeaTunnel 2.3.13 已原生提供 Embedding Transform + LLM Transform（见背景校准），但 Gaia 的 `SeaTunnelEngine` 当前只生成字段映射/SQL 类 transform，**未接入这两个模型推理类 transform**，也未验证其在 Gaia 部署环境（Zeta 集群 + 国产化模型 + 本地 ONNX 推理偏好）下的可用性。这导致 ADR-002 的设计意图（SeaTunnel 承担 PipelineBuilder 核心能力，含模型推理类变换）未完全兑现，Pipeline 层在模型推理场景退化为纯搬运工。

**影响范围**（不止 embedding）：
| 场景 | 当前临时方案 | 正式方案应在哪里 |
|------|------------|----------------|
| 语义检索 embedding 生成 | IndexSyncService.sync_now 代劳 | SeaTunnel Embedding Transform（已存在，待接入） |
| LLM 数据清洗/标注/推断 | 无 | SeaTunnel LLM Transform（已存在，待接入） |
| ML 模型批量打分（风险分/异常分） | 无（待 Functions §14.2） | SeaTunnel 自定义 transform / LLM Transform prompt |
| 复杂文本清洗/分词/NER | 无 | SeaTunnel LLM Transform / 自定义 transform |
| 字段级派生计算（超出 SQL 能力） | 无 | SeaTunnel 自定义 transform（Transform SPI） |

**设计要点**（待 ADR 展开）：
1. **接入而非自建**：Embedding/LLM Transform 上游已具备，Gaia 的工作是（a）在 `SeaTunnelEngine` 模板里支持生成 Embedding/LLM transform 配置块，（b）live dry-run 验证 Zeta 集群下能正常调通模型 API，（c）把 `model_provider`/`api_key`/`api_path` 纳入 settings 配置管理（敏感信息走环境变量，遵循 CLAUDE.md 安全约束）。
2. **本地 ONNX vs 云端 API 的适配**：SeaTunnel Embedding Transform 走 `model_provider` 调外部 API（OPENAI/QIANFAN/ZHIPU 等）。Gaia 现有 `OnnxEmbeddingProvider` 是**本地 ONNX 推理**（无 API 成本/无并发限制/数据不出域，TextQL 引擎B 已用）。需评估：外部接入路径走云端 API embedding（SeaTunnel 原生）还是本地 ONNX embedding（需自建 transform 或侧路）。这是 ADR 的核心决策点，涉及成本/延迟/数据合规/国产化要求权衡。
3. **算子注册与发现**：变换算子配置应可复用（类似 SeaTunnel connector 的模板机制）。`source_expression`（Vector 属性声明的拼接输入）需映射到 Embedding Transform 的输入列配置。
4. **与 Gravitino 生态对齐**：算子产物（派生列）应纳入 Gravitino 资产管理（列级 lineage），为 §14.1 数据血缘打基础。
3. **与 Gravitino 生态对齐**：算子产物（派生列）应纳入 Gravitino 资产管理（列级 lineage），为 §14.1 数据血缘打基础。
4. **与 Functions（§14.2）的边界**：管道内变换是**批/流数据级**计算（处理整个数据集），Functions 是**对象级/请求级**计算（处理单个对象或对象集）。两者正交，不可互相替代。管道变换产出派生列，Functions 消费列做请求级计算。
5. **与 Functions（§14.2）的边界**：管道内变换是**批/流数据级**计算（处理整个数据集），Functions 是**对象级/请求级**计算（处理单个对象或对象集）。两者正交，不可互相替代。管道变换产出派生列，Functions 消费列做请求级计算。
6. **embedding 作为首个落地场景**：Embedding Transform 接入是管道内变换能力的首个高价值用例，产出 Vector 列供 ObjectType 映射。

**当前临时方案**（Embedding Transform 接入前的过渡）：
- 外部接入路径的 embedding 由 `IndexSyncService.sync_now`（Python 编排层）代劳：从 Iceberg 读全量行 → 按 Vector 属性的 `source_expression` 拼接文本 → `OnnxEmbeddingProvider.embed_batch()` → 随原始列 upsert 到 Doris idx 表的 embedding 列。
- 这**不是正式方案**，是 Embedding Transform 接入前的过渡。代价：embedding 计算与数据搬运分离（SeaTunnel 搬原始列 + IndexSync 补 embedding 列，两步非原子）、无法复用 SeaTunnel 的并行/容错/检查点机制、IndexSyncService 职责膨胀。
- **回归条件**：SeaTunnel Embedding Transform 接入 + live 验证通过后，外部接入路径的 embedding 生成迁移到管道层，IndexSyncService 退回纯索引编排职责（建表/触发同步/降级），不再代劳 embedding 计算。

#### 遗留任务 2：对象实例语义检索（Semantic Search）暴露

**问题陈述**：底层能力齐备但未对用户暴露。`DorisIndexStore` 已有向量表/ANN 索引/`vector_search` API，`OnnxEmbeddingProvider` 已有本地 embedding 推理，但当前仅服务于 TextQL 引擎B 的「本体元素召回」（把用户名词匹配到 ObjectType/Property/LinkType），**未作为「对象实例语义检索」能力暴露给 Agent/REST/前端**。

**与 Palantir 范式对齐**：
- Palantir 的 Vector 是 Property **base type**（与 String/Integer/Geopoint 平级），非 render hint。用户显式建模 Vector 属性，配置 `dimension` + `similarity_function`。
- `nearestNeighbors` ObjectSet type 锚定单个 `property_identifier`（哪个 Vector 属性做 ANN），`num_neighbors`（K 值 1~500）+ `similarity_threshold`（0~1）。
- embedding 值来自外部 pipeline（Palantir 模式 A：backing dataset 列映射）或 Action 编排（Palantir 模式 B：AIP Logic 调 Function 算 embedding → Apply Action 写回）。
- 一个 ObjectType 可有 0~N 个 Vector 属性（多向量检索场景），系统**绝不自动**给文本属性生成向量。

**设计要点**（待 ADR 展开，依赖遗留任务 1）：
1. **Property 层**：新增 VECTOR base type（而非 `semantic_searchable` 标记）。VECTOR 属性配置 `dimension` + `similarity_function` + `source_expression`（声明 embedding 输入文本从哪些属性拼接——解决「embedding 输入文本从哪来」的核心问题）。
2. **写入路径（正式方案，依赖遗留任务 1）**：
   - 外部接入：ObjectIndexFunnel 内 Text-to-Embeddings 步骤，按 `source_expression` 拼接源列 → 产出 embedding 列 → 随数据写入 Doris idx 表（原设想在 SeaTunnel 管道内做，去 SeaTunnel 化后改在 ObjectIndexFunnel）。
   - Action 写入：outbox EMBEDDING effect 异步，按 `source_expression` 从 object_state.properties 取值拼接 → embed → Doris upsert embedding 列。
3. **写入路径（临时方案，遗留任务 1 落地前）**：外部接入的 embedding 由 IndexSyncService.sync_now 代劳（见上）。Action 写入路径的 outbox EMBEDDING effect 可先行落地（不依赖管道变换）。
4. **查询路径**：Hybrid Search（对齐 Palantir `.filter().nearestNeighbors()`）—— 结构化 filter 倒排预过滤 + ANN TopN 向量排序，一条 SQL（`WHERE` + `inner_product_approximate` + `ORDER BY ... LIMIT k`）。查询时只 embed query 一次（~15ms），对象向量预计算好。
5. **IR 层**：`nearestNeighbors` ObjectSet type 落地（§十二.7 #1），字段与 Palantir SDK `ObjectSetNearestNeighborsType` 对齐（`property_identifier` / `num_neighbors` / `similarity_threshold` / `query`）。
6. **工具/REST 暴露**：`tools/toolsets/object_query.py` 新增 `search_objects(ontology, object_type, query, top_k, filter)` 工具（AG-UI + MCP 双协议）；`POST /objects/{ont}/search` 语义检索端点。
7. **增量 re-embed**：content_hash 机制避免重复 embed（源文本未变则跳过），日常增量开销可控。

**性能预算**（100 万对象 × 1 个 Vector 属性 × 384 维）：
| 环节 | 开销 |
|------|------|
| 首次全量 embedding（临时方案 IndexSync 代劳） | 100 万 × 15ms / 50(batch) ≈ 5 分钟，本地 ONNX 无 API 成本 |
| 存储 | 100 万 × 384 × 4B ≈ 1.5GB（Doris IVF 索引） |
| 日常增量 re-embed | 日更新 1% = 1 万条 ≈ 3 秒（content_hash 跳过 99%） |
| 查询 | 1 次 embed(15ms) + ANN TopN(20ms@SIFT-1M 基准) ≈ 35ms |

**设计原则**：embedding 是索引层派生物（不是源数据），不进 Iceberg 源表，随 Doris idx 表生命周期管理。Vector 属性是用户主动建模的，系统绝不自动给所有文本属性生成向量（避免 embedding 模型 + Doris 存储/计算爆炸）。

#### 落地顺序建议

1. **✅ 已完成（2026-07-13，不依赖管道变换接入）**：VECTOR base type（PropertyDefModel.constraints JSONB + VectorPropertyConfig schema + PropertyInput/PropertyDefCreate 透传 + Alembic migration b2c3d4e5f6a7）+ source_expression 建模 + Action 写入路径 outbox EMBEDDING effect（`_create_sync_outbox_records` 追加 EMBEDDING outbox + OutboxExecutor `_do_embedding` 调 OnnxEmbeddingProvider → `DorisIndexStore.upsert_embedding` UPDATE embedding 列）+ DorisIndexStore `_escape_val` 支持 ARRAY<FLOAT> 字面量 + container 注入 EmbeddingProvider。单测覆盖：`test_vector_property_schema.py`（schema 转换）+ `test_action_sync_outbox.py::TestEmbeddingOutboxRecords`（3 用例）+ `test_outbox_executor.py::TestOutboxExecutorEmbedding`（3 用例）+ `test_doris_index_store.py::TestUpsertEmbedding/TestEscapeValArray`（4 用例）。
2. **⏳ 待实现（不依赖管道变换）**：nearestNeighbors IR type（§12.7 #1）+ search_objects 工具/REST + Hybrid Search 查询路径。底层 DorisIndexStore.vector_search 已有，待暴露。
3. **后（遗留任务 1：SeaTunnel Embedding Transform 接入）**：`SeaTunnelEngine` 模板支持生成 Embedding Transform 配置块 + live dry-run 验证 + 本地 ONNX vs 云端 API 决策（ADR）后，外部接入路径的 embedding 生成从 IndexSyncService 迁移到管道层，IndexSyncService 回归纯索引编排职责。

#### 关联文档
- ADR-002（SeaTunnel over Flink，Pipeline 层定位）
- ADR-012（TextQL，引擎B 向量召回已有雏形）
- ADR-015（图关联推理，nearestNeighbors IR type 占位）
- §14.1（数据血缘，管道内变换算子的列级 lineage 依赖）
- §14.2（Functions，与管道变换的对象级 vs 数据级边界）
- SeaTunnel Embedding Transform（上游已具备，待接入）：[2.3.13 文档](https://seatunnel.apache.org/docs/2.3.13/transforms/embedding/) · [release-note #9673 multimodal embeddings](https://github.com/apache/seatunnel/blob/2.3.13/release-note.md)
- Palantir 范式参照：`docs/reference.md` + [Vector base type](https://palantir.com/docs/foundry/object-link-types/base-types/) + [ObjectSetNearestNeighborsType](https://github.com/palantir/foundry-platform-python/blob/develop/docs/v2/Ontologies/models/ObjectSetNearestNeighborsType.md)
