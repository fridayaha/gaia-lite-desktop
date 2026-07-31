# Changelog

本项目所有重要变更记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。
源仓库 `git tag` 加 `v` 前缀（`v0.1.0`）；`VERSION` 文件与镜像 tag 用裸版本号（`0.1.0`）。

> 本 CHANGELOG 以 Gaia 引擎（`engines/gaia/`，Python 包名 `ontology`）为范围。版本号源头为 `src/ontology/__init__.py` 的 `__version__` + `pyproject.toml`。

---

## [Unreleased]

### Added — 文档治理
- **ADR-002~006 实体文件补全**：SeaTunnel vs Flink、RustFS vs MinIO、PG 存元数据、properties JSONB、Python+FastAPI（此前仅有索引行无实体）
- **ICD-01~05 实体文件补全**：PostgresMetaStore / GravitinoRegistry / IcebergStore / DorisIndexStore / TrinoQueryEngine 接口契约（此前只有索引表无契约文档）
- **CHANGELOG.md 创建**：本项目变更记录基线确立

---

## [0.1.0] — 2026-07-10

> Gaia 首个完整里程碑版本。8 层架构 + 22 Service + 本体工具层 + TextQL + 图关联推理 + 多源融合 + 权限治理全部落地。后端 1268 测试函数，前端 169 用例。

### Added — 架构基线
- **8 层分层架构**：Catalog(Gravitino) / Metadata(PG) / Dataset(Iceberg) / Index(Doris) / Pipeline(SeaTunnel) / Engine(Trino) + Graph(Neo4j) / GeoTime(PostGIS+TimescaleDB)
- **Docker Compose 11 服务**：含可选 Neo4j (profile=graph) + migrate init 容器（Alembic 一次性迁移）
- **领域模型**：Ontology / ObjectType / PropertyDef / LinkTypeDef / ActionType / InterfaceType / ValueType / Struct / ObjectTypeGroup / Branch + 运行态（object_state / outbox / action_execution_logs / datasets）
- **异常层级树**：OntologyError 基类 + NotFound/Conflict/Validation/Forbidden/DorisUnavailable/IcebergUnavailable/GravitinoUnavailable/TrinoUnavailable/OutboxError 等

### Added — ADR（架构决策记录）
- ADR-001: Doris 作为在线读主源（存全量属性，2026-06-25 修订，POC 数据支撑 qps 1.8→552）
- ADR-007: Iceberg REST Catalog 访问通道（pyiceberg 子类化 + Trino 双通道，绕过 Gravitino memory backend s3-token 401）
- ADR-008: Iceberg→Doris 索引同步路径（backfill BATCH + stream STREAMING 双模板落地，2026-07-06）
- ADR-009: 本体工具层（22 工具 / 8 toolset + MCP/AG-UI/REST 三入口）
- ADR-010: 本体 HITL 审批机制（ToolExecutor.execute_gated + ApprovalStore + AG-UI interrupt/resume + MCP elicit）
- ADR-011: Action P1（上下文注入 / 三层权限 ActionAuthorizer / CDL / Link mutation / 版本管控 / preview）
- ADR-012: 本体驱动自然语言查询 TextQL（五步流水线 + 双引擎召回 + Schema 注入 + SqlGlot 编译器，端到端验证）
- ADR-013: 前端采用 React Aria Components 作为 headless 行为层（IME-safe TextInput/TextAreaInput + ui/ 原语）
- ADR-014: 多源异构数据融合连接器体系（连接器 2→25 种 + 国产库独立类名驱动 + CDC live 验证）
- ADR-015: AG-UI Agent 驱动图探索画布（Controlled Gen UI + Shared State，废弃 explore-plan 一次性编排）
- ADR-016: 权限治理体系（Organization + Space + Project 三层 + RBAC×MAC + 多引擎下推）
- ADR-017: 权限治理技术选型（Cedar + cashews + Better Auth + fastapi-betterauth + SqlGlot）
- adr-action-mutation-mapping: Action 变更映射

### Added — Layer 实现（8 层）
- **PostgresMetaStore**：30+ 方法，业务本体元数据 + object_state(OCC) + outbox + 审计 + datasets 治理记录 + interface 关联表
- **GravitinoRegistry**：物理资产注册 + Virtual Table 联邦 + RBAC + 表路由 + 多源 catalog 管理（ADR-014）
- **IcebergStore**：双通道（pyiceberg 元数据 + Trino 数据），load_by_ids / scan_latest / merge(MERGE INTO) / 时间旅行
- **DorisIndexStore**：在线读主源（全量属性，ADR-001）+ 连接池 + load_by_ids/load_by_filter/aggregate + IVF ANN 语义表（ADR-012）
- **SeaTunnelEngine**：7 种流水线（MAIN/INDEX_BACKFILL/ACTION_CDC/FILE_SYNC/KAFKA_INGESTION/KAFKA_TIMESERIES/EXTERNAL_CDC）
- **TrinoQueryEngine**：联邦查询 + 探索辅助（list_tables/describe_table/sample_data）+ 动态 catalog 注册
- **Neo4jGraphStore**：图遍历（search_around/find_paths/exists_link）+ indexed 属性投影（ADR-015 M1）
- **GeoTimeStore**：PostGIS 空间过滤 + TimescaleDB 超表时序查询（ADR-015 M2）

### Added — Service 编排（22 个）
- OntologyService / ObjectQueryService / TimeTravelService / ActionService
- ActionRuleEngine / ActionValidator / ActionAuthorizer（三层权限）
- DataSourceService / IndexSyncService / IndexFieldExtractor
- OutboxExecutor / WriteBackManager / ConflictDetector / IngestionFilter
- SyncFlushScheduler / IcebergMaintenanceService（去 SeaTunnel 化配套）
- AIAgent（AG-UI pydantic-ai Agent）/ AIGenerate（LLM 原语 + scaffold）
- TextQL 子包（五步流水线）
- GraphProjector / GeoTimeProjector / DataFrameQueryService / AnalysisRecordStore（ADR-015）

### Added — 本体工具层（ADR-009/010）
- 22 工具 / 8 toolset 模块：metadata / object_query / write / action / link_traversal / reasoning / canvas_control / approval
- HITL 审批切面：ToolExecutor.execute_gated + ApprovalStore + AG-UI NEED_APPROVAL + MCP elicit
- 三入口：MCP（FastMCP 19 工具）/ AG-UI（pydantic-ai）/ REST

### Added — 图关联推理与时空多维分析（ADR-015）
- M0：Neo4j + PostGIS/TimescaleDB 基础设施 + 命名扩展
- M1-M2：Graph + GeoTime Layer + 投影器
- M3-M6：ObjectSet IR（对齐 Palantir 15 type 中的 13 type）+ DataFrameQueryService 执行 + TextQL + 证据链 + 工具/API
- M7：前端 Phase 2a-2h（图谱/地图/图层/分布/路径推理/决策分析）+ Phase 3a-3e（对话式 AI 自动编排 + 场景模板 + URL 预填充）

### Added — 多源异构数据融合（ADR-014）
- 连接器从 2 种扩展到 25 种（6 大品类 + generic_jdbc + StarRocks）
- 国产库独立类名驱动避冲突（openGauss/Kingbase/OceanBase）
- CDC live 验证：MySQL-CDC→Iceberg 通过
- 文件存储 / Kafka live 验证
- 前端连接器目录

### Added — 权限治理（ADR-016/017）
- Phase 0：容器 + 身份 + 归属列
- 完整实现：角色 / 用户 / 用户组 / 容器 / 标记 / 审计 / 前端
- JIT 自动 provisioning + 移除成员 + 用户详情面板
- 身份管理页面（用户/用户组 CRUD + 角色授予可视化）

### Added — Action 同步去 SeaTunnel 化（2026-07-08）
- object_state 同步改 outbox 驱动：INDEX effect→Doris ≤1s / ARCHIVE effect→Iceberg ≤5min（SyncFlushScheduler 微批 MERGE INTO）
- 不再走 SeaTunnel CDC 同步 object_state
- 主数据写入 / 外部 CDC / Iceberg→Doris 同步仍走 SeaTunnel（ADR-002 不变）

### Added — 前端
- React 19 + Vite 8 + Tailwind v4.3 + Cytoscape + React Aria Components
- 本体管理（Ontology / ObjectType / Property / Link / Action / Interface）
- 数据源管理 + 数据集列表 + 连接器目录
- 图探索画布（图谱/地图/图层/分布/路径推理/决策分析/对话式 AI 编排）
- Dashboard + 权限管理（角色/用户/用户组/容器/标记/审计）
- ObjectPicker 服务端搜索 + react-aria async combobox 修复（动态集合模式）

### Added — 工程基础设施
- Alembic 接入（业务表 schema 单一真相源，2026-07-01）
- Gravitino 1.2.0 → 1.3.0 升级 + Trino 升级配套
- uv 包管理 + uv.lock 可复现构建
- ruff + mypy --strict + pre-commit
- CI 流水线（lint → {test, audit}）
- Prometheus /metrics + trace_id 追踪

### Fixed
- Doris 索引表名跨本体数据互盖（补本体前缀 `idx_{ont}__{type}`，红线 #8）
- `get_object_type` MultipleResultsFound（查重 + DISTINCT ON，错误模式 #6）
- react-aria combobox async items popover 不打开（动态集合模式，错误模式 #8）
- SeaTunnel `stop()` bug（`cancel-job` 失效 → `stop-job` + jobId）
- JWT 模式下从 DB 加载 groups + 角色视图权限缺失

---

## 历史前置（UnionAgents 知行平台）

Gaia 引擎作为 UnionAgents（知行）平台的自研 KG 引擎引入。以下为平台级里程碑（非 Gaia 范围，仅作上下文）：

- **0.8.0 — 2026-06-25**：Step 0 基座落地（Repo2 V3 基座 + Repo1 hub/ragflow）+ 三引擎协议归一化（Hermes/OpenClaw/Dify）+ controller 并入 manager + 版本号规则入 CLAUDE.md
- **0.8.1 — 2026-06-26**：构建修复（Dockerfile.deps esbuild/vue-demi）+ hub 反向代理 + 实例版本增量热更新 + 真 DB 测试独立库守卫
- **0.8.2 — 2026-06-27**：channel profile_type 独占模式 + 用户信息注入智能体对话上下文（USER.md）+ heal 同步 profile API_SERVER_PORT

---

## 版本号规则备忘

- **版本格式**：SemVer `MAJOR.MINOR.PATCH`（如 `0.1.0`），可选预发布后缀 `-<prerelease>`
- **版本号源头**：`src/ontology/__init__.py` 的 `__version__` + `pyproject.toml`
- **改版本号**：手动同步两处（`__init__.py` + `pyproject.toml`）；如需全局 bump 走仓库根 `scripts/bump-version.sh`
- **commit / tag**：`chore: bump version to <version>` → `git tag v<version>`
- **MAJOR**：ICD 基线不兼容变更
- **MINOR**：向下兼容新功能（新增 ADR / 新 Layer / 新 Service）
- **PATCH**：Bug 修复
