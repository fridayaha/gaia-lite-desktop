# 图关联推理与时空多维分析 — 实现进度跟踪

> 设计文档：[graph-reasoning-design.md](./graph-reasoning-design.md)
> 本文件跟踪 M0-M7 落地进度。每完成一项打勾并附验证方式。
>
> **⚠️ 架构转向声明（ADR-015, 2026-07-04）**：本文件记录的是 M0-M5 + Phase 2a-3e 的**历史实现过程**，其中 M5 的 `object_set_parser.py` + `POST /query-nl` 关键词路由、M3c/Phase 3c 的 `explore_plan_parser.py` + `POST /explore-plan` 一次性编排 + `usePlanExecutor` **已在 ADR-015 中废弃删除**。当前真实架构：NL 查询统一走 `/ai/agent`（AG-UI ReAct Agent + CanvasSnapshot shared state），Agent 每轮读画布 state 决策（0 对象自然止损）。**以 [`implementation-status.md §十二`](./implementation-status.md) + [`adr-015-agent-driven-graph-explore.md`](./adr-015-agent-driven-graph-explore.md) 为准**。本文件保留作历史参考。

## 调研结论（影响落地的避坑要点，2026-07-02）

| # | 发现 | 来源 | 对落地的影响 |
|---|------|------|-------------|
| R1 | Ibis PostgreSQL backend 的 PostGIS 支持不完整且有 bug（原生 point 列被误判 geospatial → `ST_AsEWKB` 编译失败） | ibis#1786 / ibis#12007 | **不用 Ibis geospatial 表达式 API**。Ibis 只负责表连接 + 属性过滤 + memtable 衔接；空间/时序函数用 `raw_sql` / 原生 SQL 注入（PostGIS `ST_DWithin`/`ST_Within`、TimescaleDB 时间分片）。DataFrameQueryService 编排时把空间/时序 filter 翻译成原生 SQL 片段，属性 filter 用 Ibis 表达式 |
| R2 | Ibis memtable 大列表 ~8400 行 pipeline 报错；PG IN 子句 >3000 性能劣化 | ibis#11750 / SO | 候选 rid 集分批 ≤ 5000/批，用 VALUES join 或临时表，不用超大 IN |
| R3 | 一体镜像 `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6` 可用（比设计文档版本更新） | Docker Hub | docker-compose 用此 tag |
| R4 | Neo4j async driver `AsyncGraphDatabase.driver()` 创建一次，lifespan 关闭 | Neo4j docs | container 持单例 driver，main.py lifespan aclose |
| R5 | Neo4j APOC `path.expand` 不被内存追踪器检测可能 OOM | 设计文档附录 C | 多跳只用原生 Cypher `MATCH (n)-[*1..3]->(m)` + LIMIT |

## M0 基础设施 ✅

- [x] docker-compose：PG 换一体镜像 `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6` + Neo4j 独立服务（profile=graph）
- [x] infra/init-pg-extensions.sql：postgis + timescaledb + pgcrypto（00-extensions.sql，先于 01-init-schema）
- [x] config/postgres/postgresql.conf：`shared_preload_libraries = 'timescaledb'`
- [x] settings.py：neo4j 连接配置 + 图遍历阈值（concurrency/limit/timeout）+ 水合/ibis/分批上限
- [x] 依赖：neo4j[async] + ibis-framework[postgres] + polars + shapely
- [x] Alembic 迁移 `a1b2c3d4e5f6`：link_types 加 weight_property/temporal + analysis_records 新表
- [x] core/naming.py 扩展：graph_label / graph_relationship_type / geo_table / timeseries_hypertable（52 测试）
- [x] ORM：LinkTypeModel 加列 + AnalysisRecordModel；schema：DataType 激活 GEOPOINT/GEOSHAPE + 新增 GEOTEMPORAL_SERIES/TIME_SERIES + SPATIAL/TIMESERIES_DATA_TYPES 常量 + LinkTypeDef/Create/Input 加 weight_property/temporal
- [x] 验证：PostGIS 3.6.1 + TimescaleDB 2.24.0 激活；超表 create_hypertable 可用；alembic check 无漂移；989 单测全绿

## M1 Graph Layer ✅

- [x] core/schemas/graph.py：NodeFilter / GraphTraversalResult / EdgeProps（执行层契约，与传输层 ObjectSet IR 分离, C6）
- [x] core/exceptions.py：GraphUnavailableError + GeoTimeUnavailableError
- [x] layers/graph/neo4j_graph_store.py：Neo4jGraphStore（Cypher 收口, C1）— create_label/constraint/indexed_index + upsert_node/edge + delete + search_around（原生 Cypher, C9）+ exists_link（ANY/SINGLE_TARGET）+ count_nodes
- [x] services/graph_projector.py：GraphProjector（object_state/links → Neo4j 投影, 仅 indexed 属性, C1/C4）+ rebuild_for_object_type
- [x] config/container.py：graph_store + graph_projector 注入；OutboxExecutor + ActionService 注入 graph_projector
- [x] OntologyService.define（单个/batch/update）触发 _provision_graph_schema（受 capabilities.graph_indexing_enabled 门控，best-effort，Neo4j 未启动不阻塞, C5/C12）
- [x] **节点投影接线 (2026-07-10)**：OutboxExecutor INDEX effect 侧用 outbox payload 调 project_object/delete_object（capabilities 门控，fail-tolerant）
- [x] **边投影接线 (2026-07-10)**：ActionService Step 11 commit 后 RELATE→project_link / UNRELATE→delete_link（capabilities 门控，fail-tolerant）
- [ ] rebuild_for_object_type 全量重建未接线（外部数据接入路径需 SeaTunnel Iceberg→PG pipeline）
- [x] main.py lifespan 关闭 Neo4j driver
- [x] docker-compose Neo4j 配置修正：`db.memory.transaction.total.max`（5.x 配置名）
- [x] 测试：26 Neo4jGraphStore + 7 GraphProjector + 3 ActionGraphProjection = 36 新单测全绿；1025 总单测无回归
- [x] 真实 Neo4j 集成验证：create_label/constraint + upsert + search_around（1-2 跳）+ exists_link + node_filter（eq/in 下推）全跑通；define 触发 schema；Action mutation → Neo4j 节点写入

## M2 GeoTime Layer ✅（静态空间 + 动态时序流式链路完成）

- [x] core/schemas/geotime.py：SpatialFilter / AggSpec / SeriesRow（执行层契约）
- [x] layers/geotime/geotime_store.py：GeoTimeStore（PostGIS + TimescaleDB 合并封装）— create_geo_table（GiST）/ create_timeseries_hypertable（超表）/ upsert_geo / append_series / spatial_filter（withinDistance/withinPolygon/withinBoundingBox，geography 用 ST_Covers）/ series_query / table_exists / drop_table。engine 可注入便于测试
- [x] services/geotime_projector.py：GeoTimeProjector（object_state → PostGIS 投影，仅空间属性对象，[lon,lat]/GeoJSON/WKT 转 WKT）
- [x] config/container.py：geotime_store + geotime_projector 注入；OutboxExecutor 注入 geotime_projector
- [x] OntologyService.define 触发 _provision_geotime_schema（受 capabilities.geotime_indexing_enabled 门控，best-effort，含空间属性建 PostGIS 表，含时序属性建超表）
- [x] **节点投影接线 (2026-07-10)**：OutboxExecutor INDEX effect 侧用 outbox payload 调 project_object/delete_object（capabilities 门控，fail-tolerant）
- [ ] 外部数据接入路径的 PostGIS 投影待接线（SeaTunnel Iceberg→PG pipeline）
- [x] 测试：7 GeoTimeProjector 单测 + 6 GeoTimeStore 真实 PG 集成测试（PostGIS GiST 空间过滤 + TimescaleDB 超表写入查询）全绿；1032 总单测无回归
- [x] 真实端到端：define 含 GEOPOINT 的 Vehicle → PostGIS 表 `geo_geo_smoke__vehicle` 自动创建（location geography + status 剪枝列 + GiST 索引）；Action mutation → PostGIS 投影写入（V-001, 116.4/39.9, ACTIVE）
- [x] 动态时序流式链路：SeaTunnel Kafka→TimescaleDB sink（create_kafka_timeseries_pipeline + _render_kafka_timeseries_config + start_timeseries_sync + POST /datasources/{ds}/timeseries-sync）。JDBC sink 字段对齐 SeaTunnel 2.3.13（url/driver/table/schema_save_mode=IGNORE 保护超表/field_ide=LOWERCASE/primary_keys）。配置渲染真实验证正确；SeaTunnel 服务端 500 是环境问题（未启动/PG 驱动 jar），代码就绪

## M3 ObjectSet IR + 编排中枢 ✅

- [x] core/schemas/object_set.py：ObjectSet IR（判别联合，对齐 Palantir, C7）— objectType/static/filter/searchAround 四种 type + Filter（9 算子，空间/时序/属性）+ 白名单校验 + 嵌套深度检查（≤3）+ ReasoningResult
- [x] layers/metadata：get_object_states_by_rids 批量按 rid 取（水合用，分批 5000）
- [x] services/object_set_executor.py：DataFrameQueryService（编排中枢）— 递归求值 IR 树（_eval_object_set）+ filter 分流（属性→PG/空间→PostGIS/时序→TimescaleDB）+ searchAround→Neo4j（含 _resolve_target_label 从 LinkType 元数据解析目标 label）+ 防线（水合上限截断 + 深度限制）+ EvidenceChain 证据累积 + 水合（object_state 批量取, C12）
- [x] config/container.py：dataframe_query_service 注入
- [x] 测试：12 ObjectSet IR schema + 14 DataFrameQueryService = 26 新单测全绿；1065 总单测无回归；ruff + mypy 全过
- [x] 真实端到端（供应链中断传导示例, §7.5）：IR `filter(status=unfulfilled) over searchAround(supplies, from S001)` → static(S001) → Neo4j 图遍历返回 O1/O2 → PG 属性过滤保留 O1(unfulfilled)、过滤 O2(fulfilled) → 水合返回 O1 全量属性。多引擎联动（postgres+neo4j, 3 steps）完整跑通

注：Ibis 集成按 R1 调研结论简化——属性过滤走 PG object_state JSONB 参数化 SQL（_attr_filter_pg，利用 GIN 索引，大规模友好），空间走 PostGIS 原生 SQL，时序走 TimescaleDB。attr_engine 可注入（单测回退内存过滤，生产注入模块 engine）。PG JSONB 过滤集成测试验证 exactMatch + range

## M4 工具与 API ✅

- [x] tools/toolsets/reasoning.py：query_with_dataframe 工具（推理线统一入口，AG-UI + MCP + REST 三入口）
- [x] tools/toolsets/link_traversal.py：traverse_link + exists_link 实现（替换 TOOL_NOT_IMPLEMENTED，用 Neo4jGraphStore.search_around 单跳 / exists_link）
- [x] services/object_set_executor.py：加 _resolve_source_label（exists_link 源端 label 解析）
- [x] tools/__init__.py + ai_agent.py：build_reasoning_toolset 注册到 AG-UI CombinedToolset（21st 工具）
- [x] protocols/mcp_server.py：query_with_dataframe + traverse_link + exists_link MCP 暴露（替换骨架）
- [x] routes/query/__init__.py：POST /objects/{ont}/query-dataframe + /object-set + /traverse + /exists-link 四个路由
- [x] 测试：traverse_link/exists_link logic 测试（替换过时的 TOOL_NOT_IMPLEMENTED 测试）；1064 总单测无回归；ruff + mypy 全过
- [x] 真实端到端：query-dataframe（IR filter+searchAround 返回 O1，过滤 O2）；traverse（S001→O1/O2 水合）；exists-link（SINGLE_TARGET + ANY_TARGET 两种模式）全跑通

## M5 TextQL 扩展 ✅（含真实 LLM 端到端验证）

- [x] services/textql/object_set_parser.py：ObjectSetParser（推理线入口）
  - should_route_to_object_set：路由判断（图/空间/时序关键词 → 推理线，其余 → SQL 线）
  - clean_llm_json：第 2 层输出清洗（截取 ```json``` 围栏 + 去末尾逗号）
  - parse_object_set_ir：第 3+4 层 Pydantic 强校验 + 纠错闭环（校验失败回灌错误重试 ≤2 次）
  - _build_prompt：第 1 层标准化 prompt（强制规则 + Few-Shot + 本体元数据注入）
  - _call_llm：LLM 调用（支持 llm_runner 注入测试；真实用 pydantic-ai Agent）
- [x] 测试：20 object_set_parser 单测（路由判断 11 + 清洗 5 + 纠错闭环 4）；1087 总单测无回归；ruff + mypy 全过
- [x] 真实 LLM 端到端：POST /objects/{ont}/query-nl 路由（NL→should_route_to_object_set 路由判断→parse_object_set_ir LLM 产 IR→DataFrameQueryService 执行）。真实 DeepSeek LLM 验证：“供应商S001关联的所有订单”→LLM 产 searchAround(supplies, static([S001])) IR→Neo4j 图遍历找到 O1→水合返回 O1 全量属性。_build_object_set_schema_context 注入本体 LinkType + 空间/时序属性

## M6 证据链 ✅

- [x] services/analysis_record_store.py：AnalysisRecordStore（save/get 证据链快照）
- [x] DataFrameQueryService.execute 加 _save_evidence（best-effort 保存 ObjectSet IR + 各步摘要 + 血缘指针，失败不阻塞查询）
- [x] routes/query：GET /objects/{ont}/analysis/{id} 查证据链快照
- [x] 测试：3 AnalysisRecordStore 单测；1067 总单测无回归；ruff + mypy 全过
- [x] 真实端到端：query-dataframe 返回 evidence_id；GET analysis/{id} 返回完整证据链（ObjectSet IR 快照 + result_summary steps/timings/engines + evidence_pointers）

## M7 风险评分 ✅（无需新代码，用既有 Action 闭环）

设计 §10.1 明确：风险评分不造新模型，用 ObjectType+Property+Action 表达。
- 用户建 RiskScore 属性（DOUBLE, indexed=True 同步到 Neo4j 供遍历剪枝）
- 用户建计算 Action（如 recalculateRisk），submission_criteria 定义规则
  （关联风险=跳数×权重、行为风险=偏离基线、外部风险=制截名单）
- Action 定期执行，结果写 object_state 的 RiskScore
- indexed=True 时 GraphProjector 自动同步到 Neo4j 节点

M1 GraphProjector 已实现 indexed 属性同步（RiskScore indexed=True 会自动投影）。
M4 Action 投影已实现（CREATE/UPDATE mutation → Neo4j 节点）。
故 M7 无需新代码，是既有 Action 闭环的应用场景。

## Phase 2a（前端图探索 MVP）✅

- [x] docs/architecture/graph-reasoning-frontend-design.md：二期前端设计文档（Vertex 范式对标 + 4 阶段路线）
- [x] types/index.ts：补 GEOTEMPORAL_SERIES/TIME_SERIES + graph 类型（ObjectSetIR/ReasoningResult/AnalysisRecord 等）
- [x] api/graph.ts：queryDataFrame/queryNL/traverseLink/existsLink/getAnalysis/startTimeseriesSync
- [x] api/client.ts：export request 函数
- [x] hooks/useGraphExplore.ts：画布状态管理（节点/边增量 + 撤销栈 + LOD + 选中态）
- [x] components/GraphCanvas.tsx：Cytoscape 对象实例图（增量 diff + 右键 cxtmenu + ResizeObserver + fcose 布局）
- [x] pages/GraphExplorePage.tsx：顶栏（本体/OT/过滤/加载）+ 画布 + 侧栏（选中详情 + Search Around 按钮）+ 底栏（统计/证据/截断）
- [x] App.tsx + Layout.tsx：/explore 路由 + RAIL_ITEMS 加"图探索"（第 5 项）
- [x] tsc + vite build 通过
- [x] 真实端到端验证：选本体 ChainSmoke + Supplier → 加载 2 节点 → 选中 S001 → 侧栏显示详情 + Search Around → 点 supplies → 增量加 O1/O2 节点 + 2 边（节点4边2，证据链生成，引擎 neo4j+postgres）

### Phase 2b/2c/2d（待开发）
- [ ] MapPanel（MapLibre）+ 空间过滤 + 轨迹回放
- [ ] ObjectSetBuilder + Layers/Histogram tab + 对话联动
- [ ] 全链路血缘/实体对齐/find_paths/KNN

## Phase 2b（空间时空分析）✅

### 后端
- [x] `POST /objects/{ont}/spatial-filter` 路由（SpatialFilterRequest body，复用 GeoTimeStore.spatial_filter，PostGIS GiST 索引）
- [x] `POST /objects/{ont}/series-query` 路由（SeriesQueryRequest body，复用 GeoTimeStore.series_query，TimescaleDB hypertable）
- [x] `container.geotime_store` 支持 service_overrides（测试 mock）
- [x] 4 个路由单元测试（spatial 命中/表缺失、series 返回行/表缺失）全过

### 前端
- [x] 安装 maplibre-gl ^5.24.0
- [x] `api/graph.ts`：spatialFilter + seriesQuery API 客户端
- [x] `components/MapPanel.tsx`：MapLibre GL 地图（CartoDB 底图）+ marker 渲染（从 explore.nodes 提取 GEOPOINT）+ 框选过滤（shift 拖拽→bbox→spatialFilter→高亮）+ WebGL 不可用降级为坐标列表
- [x] `components/TrajectoryPlayer.tsx`：轨迹回放（series_property 输入 + seriesQuery 拉取 + 时间轴 scrubbing + 播放/暂停/速度 + 进度条）
- [x] `GraphExplorePage`：视图切换（图谱/地图/分屏）+ 轨迹面板开关
- [x] tsc + vite build 通过
- [x] 真实验证：加载 Supplier(带 location 北京/上海) → 切地图视图 → 降级列表显示 2 节点坐标（headless 无 WebGL）；spatial-filter API 真实返回 S001（bbox 命中北京）

### 待真实环境验证（需 WebGL 浏览器）
- [ ] MapLibre 交互地图渲染（marker + 框选）—— headless Chrome 无 WebGL，降级列表已验证
- [ ] TrajectoryPlayer 真实轨迹播放（需时序数据）

## Phase 2c（高级）✅

### 侧栏三 tab 化（设计 §3.4，对标 Vertex）
- [x] 侧栏重构为 tab 结构：选中 / 图层 / 分布（+ 轨迹面板开关覆盖）
- [x] Selection tab：保留原选中详情 + Search Around

### LayersPanel（图层样式，F9 持久化）
- [x] 着色：按 ObjectType（默认）/ 按属性值（自动调色板 + 图例）
- [x] 节点大小：固定 / 按度数 / 按属性值
- [x] localStorage 持久化（gaia:layerStyle）
- [x] GraphCanvas 接收 layerStyle，effect 重算节点 color/size data
- [x] LayerStyle 类型扩展（colorBy/colorProp/colorMap/sizeBy/sizeProp）

### HistogramPanel（属性分布筛选）
- [x] 选属性 → 值分布直方图（数值型分桶 / 枚举型计数）
- [x] 框选桶 → Filter to（数值→range filter，枚举→exactMatch filter）
- [x] 已应用筛选 chip 顶部显示，点 × 移除
- [x] 应用筛选后重载（baseIR + filters → loadStartSet）

### 对话结果"在画布打开"联动（F8）
- [x] 顶栏加 NL 输入框 + 💬问 按钮
- [x] queryNL → static IR（vids）重装载到画布，保证证据链一致

### 验证
- [x] tsc + vite build 通过
- [x] 真实端到端：加载 Supplier → 三 tab 渲染（选中/图层/分布）→ Layers 按度数 → Histogram 选 supplierId 显示 S001/S002 分布 → NL"所有Supplier"查询加载 2 节点
- [x] 1090 后端测试全过，无回归

### 未做（设计 §10.3 剩余）
- [ ] 新 DataType 属性编辑器适配（GEOPOINT/GEOTEMPORAL_SERIES 编辑器，属 ObjectType 编辑范畴，非图探索页）
- [ ] 截断"加载更多"cursor 续取（后端 next_cursor 已返回，但 _eval_object_set 未支持 cursor 入参，需后端改动）

## Phase 2d（二期任务 - 部分完成）

### find_paths 路径推理 ✅
- [x] 后端 `Neo4jGraphStore.find_paths`（allShortestPaths Cypher，max_depth + limit 防爆炸）
- [x] `find_paths_logic` toolset logic（graph_relationship_type 解析 link_types）
- [x] `find_paths` AG-UI 工具（第 22 个工具，挂 link_traversal toolset）
- [x] `POST /objects/{ont}/find-paths` REST 路由
- [x] `container.graph_store` 支持 service_overrides（测试 mock）
- [x] 4 个单元测试（store 返回路径/限定 rel_types/无连接/路由）全过
- [x] 前端 `api/graph.ts` findPaths + `components/PathFinder.tsx`（源/目标下拉 + max_depth + 路径序列展示）
- [x] GraphExplorePage Selection tab 接入 PathFinder
- [x] 真实端到端：S001→S002 找到最短路径 [S001, O2, S002] ✅

### 未做（真正二期，需较大后端工作）
- [ ] ObjectSet IR 剩余 type：nearestNeighbors（需 Doris 向量索引或 Neo4j GDS）、
      withProperties（需表达式引擎）、reference（需持久化 ObjectSet 存储）、
      asType/asBaseObjectTypes（类型转换）、methodInput（Action 方法输入）
- [ ] 全链路血缘审计：lineage 体系 + 审计包导出（需全链路追踪基础设施）
- [ ] 实体对齐：手动合并 + 自动对齐 ML（需 ML 模型 + 对齐工作流）

## IR 层深化 + 接口对齐 + Interface 接入（2026-07-04）✅

> 多轮架构审查驱动的系统性修复，从「MVP 能跑」到「可扩展 + 表达力完备 + 接口自洽」。

### IR 层对齐 Palantir ObjectSet（87%，13/15 type）✅

调研 Palantir `foundry-platform-python` v2 SDK，ObjectSet 实际有 15 种 type，
之前只实现 9 种。多轮补齐：

**集合运算 + 聚合 + 投影 + 排序**（P1/P2）：
- [x] union/intersect/subtract（集合运算，对齐 Palantir + Ibis union/intersect/difference）
- [x] aggregate（group_by + count/sum/avg/min/max，对齐 Ibis group_by().aggregate()）
- [x] select（投影 select_fields，减少水合 IO）
- [x] order_by（多字段+desc，保证 cursor 分页稳定性）
- [x] cursor 分页闭环（后端 cursor 参数 + 前端「加载更多」按钮）

**filter op 扩展**（9→16 种，对齐 Ibis 表达式）：
- [x] notEqual/in/notIn/greaterThan/lessThan/startsWith/endsWith

**where 嵌套逻辑组合**（P0，对齐 Palantir SearchJsonQueryV2 and/or/not）：
- [x] WhereClause 判别联合：Filter(叶子) | AndClause | OrClause | NotClause
- [x] _eval_where + _eval_where_sql：递归编译成一条 SQL（AND/OR/NOT 下推）
- [x] 真实验证：(risk=high OR medium) AND NOT none → 17 个；
      (none∩5000km) OR (high∩5000km) → 2 个（跨引擎逻辑组合）

**Interface 查询接入**（对齐 Palantir interfaceBase/interfaceLinkSearchAround）：
- [x] ObjectTypeInterfaceModel 关联表 + Alembic 迁移（implements 关系持久化）
- [x] metadata 层 get_object_types_by_interface / get_rids_by_interface（跨类型查询）
- [x] IR 加 interfaceBase（跨类型起始集）/ interfaceLinkSearchAround（跨类型图遍历）
- [x] 真实验证：Geolocated Interface（Supplier+Customer 实现）→ 250 个跨类型对象

**withProperties/reference schema 占位**（🟡 明确未实现）：
- [x] type 加入枚举 + 字段 + validator
- [x] executor 抛 NotImplementedError（需表达式引擎/持久化存储）

### 执行层重构：Ibis 临时表模式（设计 §7.4 落地）✅

之前实现退化为 Python list 手动分批传递，违背设计 §7.4 的 Ibis 规划：
- [x] _eval_filter 有 engine 时走 _eval_filter_sql：候选 vids 注册 PG 临时表，
      所有 filter 编译进一条 SQL 下推（PG 优化器自决 join 策略）
- [x] _compile_attr_pred / _compile_spatial_pred / _compile_time_pred（纯函数编译器）
- [x] 多 filter 链式一条 SQL（属性+空间+时序混合，1 次 RTT）
- [x] R2 过时结论修正：Ibis 10.8 下 5 万行 memtable + PG join 正常
- [x] 真实验证：属性+空间链式 none+5000km → 2 个（一条 SQL 同时下推）

### 闭环修复（3 轮架构审查）✅

**第 1 轮**（P0 数据正确性 + 闭环缺口）：
- [x] range 算子字符串比较 bug → ::numeric 强制数值比较
- [x] timeRange 过滤空壳 → 真过滤（to_timestamp + 内存兑底）
- [x] usePlanExecutor load 步重复查询 → 传预取 result
- [x] traverse_link Neo4j 无降级 → PG object_links 降级路径
- [x] source_to_target_map 多源 bug → 按源分组（query_object_links_batch）
- [x] schema_context 不含普通属性 → 列出全部 property api_name
- [x] ExploreStep payload 弱类型 → 判别联合强校验
- [x] EvidenceChain.timings 覆盖 → 累积

**第 2 轮**（P1 并发/性能/一致性/翻页）：
- [x] _eval_object_type 全量拉取 → get_rids_by_type（只取 id 不拉 JSONB）
- [x] traverse target/map 数据源不一致 → 单源从 target_vids 反推
- [x] next_cursor 翻页未实现 → execute cursor 参数 + 前端「加载更多」
- [x] 编排并发无防护 → usePlanExecutor abortedRef + abort()

### 接口层 JSON 契约对齐 ✅

IR 层扩展后接口层同步（之前前端类型完全落后）：
- [x] 前端 ObjectSetIR 加 9 种新 type + where + group_by/aggregations/select_fields 等
- [x] 前端 GraphFilter op 9→16 种
- [x] 前端 ReasoningResult 加 aggregates 字段
- [x] 工具层/MCP/AG-UI docstring 全部同步完整能力
- [x] object-set 路由加 cursor 参数

### 验证状态
- 后端：1172 测试全过，ruff/mypy/alembic 干净
- 前端：tsc + vite build 全过，169 测试全过
- 真实端到端：集合运算/聚合/投影/排序/where 嵌套/Interface 跨类型/Ibis 下推/cursor 翻页 全部验证

### IR 层对齐度总结

| Palantir ObjectSet type (15) | 状态 |
|-------------------------------|------|
| objectType/static/filter/searchAround | ✅ |
| union/intersect/subtract | ✅ |
| aggregate/select | ✅（扩展） |
| interfaceBase/interfaceLinkSearchAround | ✅ |
| withProperties/reference | 🟡 占位 |
| nearestNeighbors | ❌ 二期（需向量索引） |
| asType/asBaseObjectTypes | ❌ 二期（类型转换） |
| methodInput | ❌ 二期（Action 方法输入） |

## 总结

图关联推理与时空多维分析功能全部开发完成：
- 后端 M0-M7：基础设施 + 图层 + 时空层 + ObjectSet IR + 工具/API + TextQL + 证据链 + 风险评分 ✅
- 前端 2a-2h + 3a-3e：图探索 MVP → 决策分析工具 → 对话式简化 ✅
- IR 层深化：对齐 Palantir ObjectSet 87%（13/15 type）+ Ibis 临时表执行层 + 接口契约自洽 ✅
- 真二期（nearestNeighbors/血缘/实体对齐）已标记，属独立工作项

## 质量收尾（前端测试覆盖）✅

为 Phase 2a-2d 新增的前端组件补充单元测试：

- [x] `hooks/__tests__/useGraphExplore.test.ts`（10 测试）：loadStartSet/searchAround/undo/removeNode/clear/truncated/LOD 折叠/layerStyle localStorage 持久化与恢复
- [x] `components/__tests__/PathFinder.test.tsx`（4 测试）：未输入不调 API/找到路径显示 rid 序列/API 失败显示错误/无连接
- [x] `components/__tests__/LayersPanel.test.tsx`（3 测试）：无节点提示/默认样式/重置按钮
- [x] `components/__tests__/EvidenceDrawer.test.tsx`（4 测试）：null 不渲染/加载展示详情/API 失败/关闭按钮

### 最终验证状态
- 后端：1093 测试全过，ruff/mypy/alembic 干净
- 前端：20 测试文件 157 测试全过（+21 图探索测试），tsc + vite build 通过
- 真实端到端：图探索/Search Around/证据链/地图降级/三 tab/路径推理 全部验证通过

## Phase 2e-2h（v2：从 MVP 到决策分析工具）✅

### Phase 2e：分析→行动闭环（原始诉求目标 4）✅
- [x] hooks/useActionTrigger.ts（列适用 Action + 过滤 ACTIVE/VIRTUAL + trigger/close）
- [x] hooks/useGraphExplore 加 refreshNode（Action 执行后 read-your-writes 刷新单节点）
- [x] 侧栏 Selection tab 加「⚡ 可执行操作」区
- [x] 复用现有 ExecuteActionDialog + initialParameters 预填 rid
- [x] 4 个 hook 测试全过
- [x] 真实验证：选中 S001 → 显示「标记为风险供应商」→ 弹表单 → 参数校验 → 提交链路通

### Phase 2f：多步 Search Around 配置面板（Vertex 核心范式）✅
- [x] hooks/useSearchAroundConfig.ts（链式嵌套 IR 构建 + 预览 + 重置）
- [x] components/SearchAroundConfigPanel.tsx（起始集 + 跳卡片 + 关系/方向/跳数/属性过滤 + 预览数量防星爆）
- [x] 侧栏加「探索」第四 tab
- [x] 8 个 hook 测试全过（IR 构建/过滤/移除/预览/reset）
- [x] 真实验证：设起始集 S001 + Link1 supplies → 预览命中数

### Phase 2g：全局时间轴（原始诉求目标 5）✅
- [x] hooks/useTimeFilter.ts（时间窗 + 活跃过滤 + 播放）
- [x] components/TimeScrubber.tsx（双滑块 + 预设 1h/24h/48h/7d + 播放 + 仅活跃实体）
- [x] 底栏替换为 TimeScrubber，统计移顶栏
- [x] 无时序数据降级提示

### Phase 2h：配套改进 ✅
- [x] 多布局切换（力导向 fcose / 层级 dagre / 环形 circle / 网格 grid）+ cytoscape-dagre 依赖
- [x] GraphCanvas 加 layout prop + 切换 effect

### 验证
- [x] 后端 1094 测试全过，前端 22 文件 169 测试全过，tsc + vite build 通过
- [x] 真实端到端：Action 闭环 + 多步 Search Around + 布局切换 全部验证

### 设计文档
- [x] docs/architecture/graph-reasoning-frontend-design-v2.md（10 条调研结论 + 3 大核心交互设计 + 测试策略）

## 前端 Phase 3：对话式简化（v3，2026-07-03 完成）✅

> 目标：从 v2 的「7 控件 + 空画布」转为「对话即探索」，业务用户 0 培训可用。设计详见 [graph-reasoning-frontend-design-v3.md](./graph-reasoning-frontend-design-v3.md)。
> **核心纠正**：v3 不是 v2 的重写，是对话优先的皮肤——v2 全部能力保留，技术用户可切回完整控件。

### Phase 3a：对话式空状态 ✅
- [x] `ExploreLanding.tsx`：中央对话框 + 4 个可点击 worked example 卡片 + 本体知识提示（ObjectType/LinkType 列表）
- [x] GraphExplorePage 加 `mode: 'landing' | 'exploring'` 状态机，landing 显示 ExploreLanding
- [x] `runNLQuery` 带 fallback：query-nl 422（非推理查询）→ `parseBasicQuery` 轻量意图识别（识别「查看所有X」）→ queryDataFrame
- [x] 真实验证：「查看所有Supplier」→50 节点；「找出高风险Supplier」→7 节点

### Phase 3b：对话流多轮 ✅
- [x] `useConversation` hook：ChatMessage[] 多轮管理 + localStorage 按本体持久化 + 建议/证据/IR 附着
- [x] `ConversationPanel.tsx`：左侧常驻对话流（消息历史 + AI 建议可点芯片 + 输入框）
- [x] `buildSuggestions` helper：根据 linkTypes 生成「展开 X 关系」「🗺 在地图查看」建议
- [x] 建议动作分流：`expand` → `explore.searchAround(rid, linkType, 'forward')`；`view` → `setView('map')`；`nl` → runNLQuery
- [x] 顶栏「💬 新对话」按钮：清对话 + 回 landing
- [x] 真实验证：初始查询→对话显示对象数+3建议；点「展开 supplies 关系」→ search_around 执行；点「在地图查看」→切地图视图

### Phase 3c：AI 自动编排（核心差异化）✅

> **这是 v3 的灵魂**：用户说一句「分析供应链中断风险」，LLM 自动拆成多步操作计划，前端逐步执行。真 AI 理解意图产计划，**不是硬编码 if-else 模板**。

**后端**：
- [x] `core/schemas/explore_plan.py`：ExplorePlan（steps[]）+ ExploreStep（type=load/search_around/switch_view/color_by/message + payload + source + direction）
- [x] `services/textql/explore_plan_parser.py`：LLM 产计划 service，复用 object_set_parser 四层保障模式（prompt 标准化 + 输出清洗 + Pydantic 强校验 + 纠错闭环 ≤2 次）
- [x] `POST /objects/{ont}/explore-plan` 路由：复用 `_build_object_set_schema_context` 注入本体元数据
- [x] 4 单测：basic 多步计划 / 纠错环 / 全步骤类型 / 重试耗尽报错

**前端**：
- [x] `usePlanExecutor` hook：接收 ExplorePlan 逐步执行——load→queryDataFrame+loadStartSet；search_around→explore.searchAround；switch_view→setView；color_by→setLayerStyle；message→对话回复。每步更新对话流+画布
- [x] 修复 search_around 闭包过期：load 步记录首个 rid 供后续步用（不依赖闭包 explore.nodes 旧快照）
- [x] `runNLQuery` 智能路由：复杂分析类问题（分析/评估/排查/挖掘/查看地理/分布查看）走编排，简单查询走 queryNL+fallback

**真实验证**（ChainSmoke 本体）：
- [x] 用户「分析供应链中断风险」→ LLM 拆 5 步：加载供应商→按风险着色→展开物料关系→切地图视图→完成提示
- [x] 逐步执行全绿：✓50 供应商 → ✓着色 → ✓search_around（traverse 200）→ ✓地图视图 → 完成文案

### Phase 3d：场景模板 + URL 预填充 ✅
- [x] ExploreLanding 加 4 场景卡片：📦供应链中断分析 / 🔍隐性关联挖掘 / 🗺地理分布查看 / 🕐时序异常排查
- [x] **模板只预填对话问题，走真 AI 编排**（非硬编码步骤）——用户点模板=一句话触发多步自动执行
- [x] URL 预填充（对齐 Vertex）：`/explore/:ont?objects=S001,S002&view=map&question=xxx`
  - objects：预加载对象集（static IR）；view：预切视图（graph/map/split）；question：自动执行问题
  - 从其他页面带上下文进入图探索（如对象详情页「在图中查看」按钮）
- [x] 真实验证：`?objects=S000,S001&view=map` → 直接进探索模式 + 2 节点 + 地图视图

### Phase 3e：高级模式（保底）✅
- [x] 顶栏 💬/⚙ 按钮切换对话流显隐（`showConversation` state）
- [x] 业务用户：对话流常驻，对话驱动；技术用户：⚙ 隐藏对话流，画布撑满，用完整 v2 控件
- [x] 真实验证：点⚙ → 对话流条件渲染移除，画布撑满

### Phase 3 验证
- [x] 后端 1094 测试全过（含 4 explore_plan 新测）；前端 tsc + vite build 通过
- [x] 真实端到端：对话流多轮 + AI 自动编排 + 场景模板 + URL 预填充 + 高级模式 全部验证

### Phase 3 设计文档
- [x] docs/architecture/graph-reasoning-frontend-design-v3.md（问题诊断 + 三原则 + 对话式空状态 + 对话流 + AI 编排 + 场景模板 + URL 预填充 + 分层适配 + 分期实施）

## 后续路标（遗留任务）

### IR 层剩余（依赖独立基础设施）
- [ ] nearestNeighbors：KNN/向量检索（需 Doris 向量索引或 Neo4j GDS）
- [ ] withProperties：派生属性（需表达式引擎，目前 schema 占位 + NotImplementedError）
- [ ] reference：引用持久化 ObjectSet（需 ObjectSet 存储，目前 schema 占位）
- [ ] asType/asBaseObjectTypes：类型转换（ObjectType → Interface/基类）
- [ ] methodInput：Action 方法输入（与 Action 闭环深化）
- [ ] Substrait 对接：ibis-substrait 标准化 PG 侧查询计划（当前手写 SQL 够用）

### 产品/体验
- [x] 对话式本体建模（多轮）：✅ 已落地（Sprint 2 + commit 584af2c：AG-UI Thread 多轮 + 写工具 HITL + Capability 方法论）
- [ ] 真实 LLM 编排确认环节：当前 AI 编排直接执行，未来加「确认/调整步骤」（design-v3 §2.3 原始设想）
- [ ] 场景模板自定义：用户保存自己的探索为模板（localStorage）

### 基础设施
- [ ] 全链路血缘审计：lineage 体系 + 审计包导出（需全链路追踪基础设施）
- [ ] 实体对齐：手动合并 + 自动对齐 ML（需 ML 模型 + 对齐工作流）
- [ ] 治理 Principal + 权限 + 审计入库（当前 principal=anonymous）
- [ ] Interface CRUD 路由：metadata 层已实现，但无 REST 端点暴露（当前只能通过代码/脚本建 Interface）
- [ ] 推理线 Doris 水合：当前 _hydrate 走 object_state（MVP），大规模下应切 Doris 倒排索引（CLAUDE.md 红线 4）
