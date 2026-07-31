# ADR-018: Pipeline Builder 数据管道编排（基于 Apache Kestra）

| 字段 | 值 |
|------|-----|
| 状态 | Proposed |
| 日期 | 2026-07-14 |
| 决策者 | 开发者 + 评审（待评审） |
| 影响 | 新增 `layers/pipeline/kestra_engine.py` / `services/pipeline_builder_service.py` / `core/models/pipeline.py` / `core/schemas/pipeline.py` / `routes/pipeline/`；扩展现有 `DatasetGovernanceModel`（snapshot 感知）；docker-compose 新增 Kestra 服务 |
| 关联文档 | [pipeline-builder-design.md](../design/pipeline-builder-design.md)、[ADR-014 多源融合](adr-014-multi-source-data-fusion-connectors.md)、[ADR-008 Iceberg→Doris 同步](adr-008-iceberg-doris-sync-path.md)、[dataset-ontology-binding.md](../design/dataset-ontology-binding.md)、[data-flow-diagrams.md](../design/data-flow-diagrams.md) |
| 对标 | Palantir Foundry Pipeline Builder |

## 背景

Gaia 当前的「数据管道」能力由 `layers/pipeline/sea_tunnel_engine.py` 承担，本质是**数据搬运管道**：源 → Iceberg（main/cdc/file_sync/kafka/external_cdc/timeseries 6 种模板），由 `DataSourceService` 编排，`IndexSyncService` 负责 Iceberg→Doris 同步，`OutboxExecutor` 负责 Action 写回。

这套能力对标的是 Palantir Foundry 的 **Data Connection**（数据接入层），而非 **Pipeline Builder**（数据转换编排层）。缺失的能力是：

1. **数据转换编排**：清洗 / Join / 聚合 / 计算 / 质量校验的可视化 DAG，对应 Palantir Pipeline Builder 的核心定位
2. **契约式校验**：Schema 推演引擎（编译期检查，错误实时暴露，不等运行时）
3. **管道与本体绑定**：管道输出直接映射到 ObjectType（路线 A，分两步：先 Dataset→Dataset，再挂载/新建本体对象）
4. **管道版本化与生命周期**：分支、部署、回滚、调度、监控

引入 Pipeline Builder 的目标：把 Gaia 从「数据接入 + 本体查询」平台，升级为「数据接入 → 转换加工 → 本体落地」的完整数据价值交付平台，对齐 Palantir Foundry 的核心链路。

## 决策

### D1: 执行层四引擎分工 —— Kestra（编排）+ DuckDB（转换）+ SeaTunnel（搬运）+ Trino（只读联邦）

Pipeline Builder 的执行层采用 **四引擎分工**架构，每个引擎职责正交、不互相替代：

| 引擎 | 角色 | 部署形态 | 读写 | 对应 Palantir |
|------|------|---------|------|--------------|
| **Apache Kestra** | 编排引擎（DAG 调度/状态机/触发器/重试） | 独立服务（docker-compose 新增） | 不执行数据读写 | 编排层（非 Palantir 对应物，Palantir 自研） |
| **DuckDB** | 转换执行引擎（SQL 转换，读 Iceberg 写 Iceberg） | **嵌入式库**（Kestra Worker 进程内，零新增服务） | ✅ 完整读写 | Faster Pipeline（DataFusion） |
| **SeaTunnel** | 数据搬运引擎（source→Iceberg，25 连接器+CDC） | 独立服务（已有，保留） | 写 Iceberg | Data Connection |
| **Trino** | 只读联邦查询引擎（跨源 JOIN，VIRTUAL 表） | 独立服务（已有，保留） | ❌ 只读（保持现有定位） | 联邦查询（非 Palantir 对应物） |

**核心原则：每个引擎只做它擅长的事，不越界**
- Kestra 编排，不自研调度器（Kestra 有完整的 Scheduler/Executor/Worker/状态机）
- DuckDB 做转换（嵌入式、向量化、能读能写、单节点 GB~十GB 级），**不扩展 Trino 写入**（Trino 定位是查询引擎，Gaia 现状纯只读，扩展写入是逆定位）
- SeaTunnel 做搬运（被 Kestra 编排，不替换，25 连接器是已有资产）
- Trino 做联邦查询（只读，VIRTUAL 表联邦红线不可替代），**不承担管道转换**

**为什么需要 DuckDB（关键决策）**：
- Kestra 自带的 `plugin-transform-records`（Filter/Select/Aggregate/Map）是配置驱动的轻量算子，但数据以 Ion 格式在 Kestra 内部存储流转，**执行上下文有 ~1MB 硬限制**，处理 1GB 文件会 OOM，只适合 MB~几十MB 级
- Trino 在 Gaia 是**纯只读**（TrinoQueryEngine 只有 query/list/describe/sample，零写入方法），且其定位是查询引擎非 ETL 引擎，扩展写入逆定位
- **DuckDB 填补这个空白**：嵌入式（零部署，`pip install duckdb` 或 Kestra plugin-jdbc-duckdb 自带）、完整读写（CREATE TABLE/INSERT/CTAS）、向量化单节点、读 Iceberg/Parquet/S3、能 spill to disk 处理超内存数据、单机实测 1TB Parquet 可跑
- DuckDB 对应 Palantir Faster Pipeline（DataFusion/Rust），是开源等价物

**Kestra 与 SeaTunnel 的协作（避免割裂）**：
- Kestra 的 Flow 中可以包含「SeaTunnel 数据搬运 Task」（通过 `io.kestra.plugin.core.http.Request` 调 SeaTunnel REST API），实现「先搬运、再转换」的组合管道
- 用户视角是**一条管道**，不感知两个引擎。这印证了 Kestra 官方对「Kestra vs Airbyte/Fivetran」的定位：专用工具负责搬运，Kestra 负责编排

**替代方案**（已否决）：
- 自研 DAG 调度器：放弃，重复造轮子，调度/重试/状态机/触发器都要自己实现
- 用 SeaTunnel SQL Transform 做转换：放弃，SeaTunnel Transform 能力有限，无 Schema 推演/版本管理/调度，强行扩展等于把它改造成编排平台，违反「不侵入修改」原则
- 扩展 Trino 写入做转换：放弃，Trino 定位是查询引擎，Gaia 现状纯只读，扩展写入逆定位且不擅长大规模 ETL 写入
- 引入 Airflow/Dagster：放弃，Airflow 偏 Python 代码编排 YAML 弱，Dagster Asset-centric 与 Gaia Dataset 模型重叠且 Python-heavy，都不如 Kestra 适合可视化拖拽

**部署附注：DuckDB Iceberg 扩展的分发**

DuckDB 读 Iceberg 表依赖 `iceberg` 扩展。该扩展是预编译二进制（`.duckdb_extension.gz` ~18MB，解压 49MB），**超过 gitcode 仓库 10MB 文件大小限制，不入 git**（历史 commit `cacc67e` 曾误入被远端 hook 拒绝，已从历史彻底移除）。分发方案：

| 路径 | 方式 | 适用场景 |
|------|------|----------|
| **主路径（推荐）** | 自定义 Kestra 镜像预装扩展 → ACR 分发，`docker pull` 即自带 | 云端/生产部署 |
| 备路径 | 挂载 `./infra/extensions/` 卷，本地手动放置 `.gz` | 本地开发 |
| 兜底 | DuckDB 首次 `LOAD iceberg` 时在线下载（需外网 egress 到 duckdb.org） | 仅联网环境 |

- 镜像构建：`infra/Dockerfile.kestra`（基于 `kestra/kestra:latest`，COPY 扩展进 `/opt/kestra/extensions/`）
- 扩展来源：https://github.com/duckdb/duckdb_iceberg/releases （选 duckdb 1.5.3 linux_amd64 版本）
- 安装逻辑：`infra/kestra-entrypoint.sh` §2 检测 `.gz` 并解压到 DuckDB 扩展缓存目录，目录为空时安全跳过
- `.gitignore` 已加 `engines/gaia/infra/extensions/*.duckdb_extension` 防再次误提交

### D2: 三层架构 —— Pipeline IR（自研）+ Schema 引擎（自研）+ Kestra 编排 + DuckDB 转换

完全对齐 Palantir 三层架构，但实现方式基于开源栈四引擎分工：

| 层 | Palantir | Gaia 实现 | 自研/复用 |
|----|----------|----------|----------|
| 可视化逻辑层 | Graph UI | React 19 + React Flow 画布 | 自研前端 |
| 统一转换抽象层（IR） | Pipeline IR（自研） | **Pipeline IR（Gaia 自研）** —— DAG + 节点契约 + 执行属性 | **自研** |
| Schema 计算引擎 | Schema Computation Engine | **SchemaInferenceEngine** | **自研**（Kestra 无此能力） |
| 多引擎执行层 | Spark/Flink/DataFusion | **Kestra 编排 + DuckDB 转换 + SeaTunnel 搬运 + Trino 只读联邦** | 复用开源 |

**关键边界**：Pipeline IR 是 Gaia 自研的引擎无关中间表示，Kestra Flow 是 IR 在执行层的物理投影。IR → Kestra Flow 是单向翻译（类似编译器 IR → 目标代码），Kestra Flow 不反向生成 IR（避免 Palantir 也未完全解决的代码反向解析难题）。Kestra Flow 内部的 Task 调用 DuckDB（转换）/ SeaTunnel（搬运）/ Trino（联邦查询），由 KestraEngine 翻译时决定路由。

**为什么 IR 不直接用 Kestra Flow YAML**：
- Kestra Flow 是执行导向的（task list + 控制流），缺少 Schema 契约、数据质量规则、输出契约、血缘元数据等治理属性
- IR 需要支持 Schema 推演（D3），Kestra Flow 没有 schema 概念
- IR 是 Gaia 治理体系（血缘/权限/版本）的载体，必须自研以对接 ADR-016 权限体系与未来血缘
- 保持 IR 引擎无关，未来若引入 dbt（用户提及的远期选项），只需新增 IR→dbt model 翻译器，IR 层不变

### D3: Schema 推演引擎自研（Gaia 核心壁垒，Kestra 无此能力）

Kestra 没有 Schema 推演能力（它的 inputs 是强类型校验，但不是数据流字段的编译期推演）。Pipeline Builder 的「Schema 与计算解耦」是核心差异化，必须自研。

**实现方式**：基于 pydantic v2 + 算子注册表，每个转换算子注册：
- `input_contract`：输入 Schema 约束（字段数、类型、主键要求）
- `infer_output_schema(input_schema, config) -> output_schema`：输出 Schema 推导函数
- `validate_config(config, input_schema) -> list[ValidationIssue]`：配置校验

Schema 推演引擎增量计算（仅重算受影响下游），毫秒级返回，完全不触碰真实数据。第一阶段覆盖核心算子（Filter/Select/Rename/TypeCast/Join/Aggregate/Union/Expression/QualityCheck），后续按需扩展。

**详细设计见** [pipeline-builder-design.md §4 Schema 计算引擎](../design/pipeline-builder-design.md#4-schema-计算引擎自研核心壁垒)。

### D4: Ontology 输出采用路线 A，分两步实施

路线 A（管道感知 Ontology），但分两阶段：

- **阶段 1（MVP）**：管道输出节点配置「映射到已有 ObjectType」—— 用户选择已存在的 ObjectType + 字段映射，管道执行时写入 ObjectType 绑定的 Iceberg 表（通过现有 `link_dataset` 机制解析物理表名）。**不写 Doris idx 表**（那是 IndexSyncService 的独立链路，ADR-008）。
- **阶段 2**：管道输出节点支持「新建 ObjectType」—— 管道根据输出 Schema + 用户配置，调用 OntologyService 创建 ObjectType + link_dataset，实现 Palantir 式的「管道生成对象」。

**关键约束（与用户确认）**：
- 管道写入只落地 Iceberg（通过 IcebergStore），Doris 同步由 IndexSyncService 独立触发（可在管道配置「触发索引同步」选项，调用 IndexSyncService.sync_now）
- Dataset 抽象只关联 Iceberg snapshot，不关联 Doris 表 / 外邦表（VIRTUAL）

### D5: 版本化 Dataset —— 复用现有 DatasetGovernanceModel + 激活 Iceberg snapshot

不新增 Dataset 抽象层。现有 `DatasetGovernanceModel`（治理记录）+ Iceberg snapshot（物理版本）已足够，只需激活：

- `DatasetGovernanceModel` 新增字段 `current_snapshot_id`（当前对外可见版本）+ `snapshot_retention`（版本保留策略）
- 管道写入 Iceberg 后，`commit` 操作原子更新 `current_snapshot_id`，下游读取走 TimeTravelService 按 snapshot 读取
- 版本回滚 = 切换 `current_snapshot_id`，秒级完成（元数据操作，不重跑数据）

**Dataset 读写边界（与用户确认）**：
- **只关联 Iceberg snapshot**：MANAGED Dataset 的读写都走 IcebergStore，版本管理用 Iceberg snapshot
- **不管 Doris**：Doris 是在线读主源（ADR-001），由 IndexSyncService 从 Iceberg 同步，不属于 Dataset 抽象
- **不管外邦表**：VIRTUAL Dataset 是 Trino 联邦代理指针（不落地），无版本概念，管道不可写入（红线 9）

### D6: MVP 范围 —— DuckDB 转换 + Dataset→Dataset 闭环

第一阶段（MVP）聚焦最小可用闭环，执行层用 Kestra 编排 + DuckDB 转换：

**做**：
- Pipeline IR + DAG 模型（节点/连线/契约/执行属性）
- Schema 推演引擎（核心算子）
- Kestra 引擎适配 + IR→Kestra Flow 翻译器（Task 路由：转换→DuckDB，搬运→SeaTunnel，联邦→Trino）
- DuckDB 嵌入（Kestra plugin-jdbc-duckdb，零新增服务）
- 核心转换算子（Filter/Select/Rename/TypeCast/Join/Aggregate/Union/Expression/QualityCheck）
- 输出到 Dataset（Iceberg，通过 DuckDB CTAS/INSERT）+ 阶段 1 的 ObjectType 映射
- 手动触发 + 定时调度（Schedule trigger）
- 全量重建 + 增量追加两种写入模式
- Schema 校验 + 执行状态监控

**不做**（后续阶段）：
- Streaming Pipeline（需引入 Flink，新引擎依赖）
- 大规模分布式 Batch（需引入 Spark 或扩展 Trino 写入，TB+ 级场景）
- CDC 合并模式（管道级 MERGE INTO，调度链路复杂）
- Job Group 分组（先单输出/单 Flow）
- 分支评审 / 灰度发布（先主干直接部署）
- AI/LLM 节点 / Custom Function / 代码双向导出
- Marketplace 分发 / 多租户

**注**：DuckDB 单节点能覆盖 GB~十GB 级转换（spill to disk 可处理更大），MVP 场景足够。TB+ 级大规模分布式转换是 Phase 3 考虑项（引入 Spark 或扩展 Trino 写入）。

### D7: UI 分层覆盖路线 C —— Gaia UI 只做独有价值层，Kestra UI 透出给高级用户

Kestra 自带完整 UI（Flow 编辑器/No-Code/Topology/执行监控/Dashboard/1700+ 插件表单），且 No-Code 编辑器基于插件 JSON Schema 自动生成表单，新增插件零映射代码。若 Gaia UI 完全覆盖 Kestra，会陷入"永远追不上插件迭代"的死锁，且大量重写 Kestra 已有能力（执行监控/调度/日志），投入产出比极低。

**决策**：路线 C —— 分层覆盖。

**Gaia UI 自研层**（独有价值，必须自研）：
- Pipeline 画布编辑器（React Flow，拖拽 Source/Transform/Sink + 连线）——DAG 编辑器，Kestra topology 是只读视图不是编辑器
- 实时 Schema 推演校验提示——**核心壁垒，Kestra 完全没有**
- 核心算子表单（~10 个固定集合：Filter/Select/Rename/TypeCast/Join/Aggregate/Union/Expression/QualityCheck）
- Ontology 绑定配置（阶段 2）——Gaia 独有
- 管道列表 + 轻量执行状态（成功/失败/耗时）
- 版本管理 + 回滚——Gaia 独有

**Kestra UI 透出层**（iframe 嵌入，给高级用户/运维）：
- 执行详情（TaskRun 级日志/metrics/拓扑）——复杂场景才看
- 1700+ 插件 No-Code 配置——超出核心 10 个的高级算子
- 调度/触发器高级配置——超出 Gaia 简化配置的
- Dashboard 监控大盘——运维场景

**算子分层策略**（解决可扩展性死锁）：
- **核心算子自研**（~10 个，固定集合）：Gaia UI 表单 + Schema 推演 + 翻译为 DuckDB SQL，覆盖 80% 场景（二八原则）
- **扩展算子透传**（1700+ Kestra 插件）：IR 的 `GenericKestraTask` 节点，Gaia UI 提供"插入 Kestra Task"入口 + iframe 跳转 Kestra No-Code 配置，Schema 用户声明，翻译时原样透传。**不背映射包袱**

**需解决的工程问题**：SSO/认证打通（Gaia 登录免登 Kestra）、URL 定位（Gaia 构造 iframe URL）、视觉割裂（接受，因为是"高级模式"）

**替代方案**（已否决）：
- 路线 A（完全自研 UI 覆盖 Kestra）：算子可扩展性死锁，永远追不上 1700+ 插件迭代；大量重写 Kestra 已有能力
- 路线 B（完全用 Kestra 原生 UI）：失去 Schema 推演核心壁垒；体验割裂；无法做 Ontology 绑定可视化

**详细设计见** [pipeline-builder-design.md §7.0 算子分层策略 + §16.7 Kestra UI 分层透出策略](../design/pipeline-builder-design.md#70-算子分层策略路线-c-核心算子自研--扩展算子透传)

### D8: 数据模型 —— Airflow 3 独立版本表 + Prefect 状态历史 + JSONB 整存 IR

Pipeline Builder 的物理表结构设计，参考 Airflow 3 / Prefect / DolphinScheduler / Kestra 四家开源编排器的成熟模式，规避其踩过的坑。

**七项子决策**（详见 [pipeline-builder-design.md §12.1](../design/pipeline-builder-design.md#121-设计决策dfx-属性)）：

| 子决策 | 选择 | 对标开源 |
|--------|------|---------|
| D-1 版本化模式 | Airflow 3 独立版本表（pipelines + pipeline_versions） | Airflow 3 `dag`+`dag_version`（AIP-65） |
| D-2 节点存储 | JSONB 整存（pipeline_versions.graph） | Airflow serialized_dag / Kestra Flow YAML |
| D-3 状态历史 | 冗余当前 + 独立 state_history 表 | Prefect flow_run+flow_run_state（PR #7138 教训） |
| D-4 Kestra 映射 | executions 表加字段（非独立映射表） | — |
| D-5 节点观测字段 | MVP 建字段，采集延后 | Prefect task_run |
| D-6 执行记录 TTL | MVP 建索引，清理延后 | Prefect/Airflow vacuum |
| D-7 节点血缘表 | Phase 2 再建 | DolphinScheduler 边表（反例） |

**避开的坑**：
- FK 无索引致级联删除慢（Airflow PR #39638）→ 所有 FK 列显式加 INDEX
- 状态无历史（Airflow 2.x）→ 独立 state_history 表
- 历史查询慢（Prefect PR #7138）→ executions 表冗余 current_state/state_started_at/duration_ms/error_message
- 误删有执行引用的版本（Airflow 3.1.0）→ 版本→执行用 RESTRICT 而非 CASCADE
- 节点过早关系化（DolphinScheduler 边表）→ MVP 用 JSONB graph

**6 张新表 + datasets 扩展 3 字段**：pipelines / pipeline_versions / pipeline_schedules / pipeline_executions / pipeline_node_runs / pipeline_state_history + datasets.{current_snapshot_id, snapshot_retention, write_lock}。ORM 模型放 `src/ontology/core/models/pipeline.py`，走 Alembic migration。

**替代方案**（已否决）：
- DolphinScheduler 双表模式（当前表+日志表）：schema 要同步维护，易漂移
- 节点独立表（方案 A）：IR 整体性被破坏，MVP 无跨版本节点 diff 需求
- 状态只存当前靠日志补：审计和失败诊断不便
- 独立 Kestra 映射表：1:1 关系过度设计

**详细表结构见** [pipeline-builder-design.md §12 数据模型（ORM）](../design/pipeline-builder-design.md#12-数据模型orm)


### D9: API 设计 —— Palantir Deploy/Build 分离 + Kestra 执行控制 + 开源避坑

Pipeline Builder REST API 设计，参考 Palantir Foundry API（Deploy/Build 分离、Schedule 独立资源、Release Stage）+ Kestra API（执行控制、SSE、插件发现）+ Prefect（idempotency）+ Airflow（批量查询）。

**15 项子决策**（详见 [pipeline-builder-design.md §13.1](../design/pipeline-builder-design.md#131-设计决策dfx-属性)）：

| 子决策 | 选择 | 对标 |
|--------|------|------|
| A-1 版本前缀 | `/api/v1/pipelines` 显式 v1 | Kestra / Airflow |
| A-2 资源标识 | URL 用 api_name（不用 Palantir RID） | Gaia 现有规范 |
| A-3 Deploy/Build 分离 | deploy（逻辑生效）+ build（数据物化）独立端点 | **Palantir 核心概念** |
| A-4 Schedule 独立资源 | `/schedules` 独立 CRUD | Palantir `/v2/orchestration/schedules` |
| A-5 触发嵌套 | trigger JSONB 预留 AND/OR 嵌套 | Palantir Trigger 嵌套 |
| A-6 Build 参数 | force_build/retry_count/timeout/abort_on_failure | Palantir CreateBuildRequest |
| A-7 异步执行 | 202 + SSE，预留 `?wait=true` | Kestra / AIP-151 |
| A-8 幂等触发 | `Idempotency-Key` header | Prefect / Stripe |
| A-9 Kestra 透传 | `?fetch_from_kestra=true` | 路线 C |
| A-10 算子目录 | `/pipeline-operators` + `/kestra-plugins` | 路线 C 支撑 |
| A-11 错误响应 | 沿用 Gaia 现有 `{detail, error_type, code}` | 一致性优先 |
| A-12 Release Stage | OpenAPI `x-release-stage` | Palantir Release Stage |
| A-13 术语 | `builds` 替代 `executions` | Palantir 术语 |
| A-14 批量 | MVP 不做批量触发，列表支持多管道筛选 | Airflow batch list |
| A-15 分页 | MVP offset，预留 cursor | 2026 业界共识 |

**避开的坑**：
- DolphinScheduler Issue #18132（version 不回填）→ POST/PATCH 响应必含 version_number
- Airflow offset 深翻页慢 → 预留 cursor
- Airflow GET URL 超长 → 批量查询用 POST body
- 无幂等重复触发 → Idempotency-Key
- 阻塞 HTTP 等执行 → 202 + SSE

**关键设计**：Deploy/Build 分离是吸收 Palantir 的核心概念——deploy 更新逻辑（不触发执行），build 物化数据。用户可只 deploy 不 build（defer cost），schedule 触发的是 build。

**替代方案**（已否决）：
- RFC 9457 结构化错误：与 Gaia 现有 `{detail}` 风格不一致，放弃
- Palantir RID 标识：对用户不友好，沿用 api_name
- 同步等待端点：管道执行可能数小时，MVP 只异步
- 批量触发/取消：MVP 单管道场景，Phase 2 多管道编排

**详细端点清单见** [pipeline-builder-design.md §13 API 设计](../design/pipeline-builder-design.md#13-api-设计)


### D10: 前端设计 —— React Flow + AG-UI Agent（复用图探索实践）+ NodeRegistry + 表单/IR 双模

Pipeline Builder 前端架构，参考 Palantir Pipeline Builder UI（四区布局 + Preview pane）+ React Flow 生产级实践（Zustand + zundo + dagre）+ Gaia 现有 AG-UI 实践（图探索 ADR-015）。

**15 项子决策**（详见 [pipeline-builder-design.md §14.1](../design/pipeline-builder-design.md#141-设计决策dfx-属性)）：

| 子决策 | 选择 | 对标 |
|--------|------|------|
| F-1 画布引擎 | React Flow | DAG 编辑器事实标准 |
| F-2 状态管理 | Zustand + TanStack Query | React Flow 内部用 Zustand |
| F-3 undo/redo | zundo | Zustand temporal 中间件 |
| F-4 AI FDE 架构 | 复用 AG-UI Agent + STATE_SNAPSHOT | **图探索 ADR-015 完全一致** |
| F-5 AI 交互 | 对话式（多轮 ReAct），AssistantUiChat | 复用图探索组件 |
| F-6 流式渲染 | 工具逐个发 STATE_SNAPSHOT | AG-UI 协议天然契合 |
| F-7 布局 | dagre（MVP）+ elkjs（Phase 2） | graph 真相源，布局纯函数派生 |
| F-8 节点管理 | NodeRegistry 注册表 | 避免switch-case膨胀 |
| F-9 配置双模 | 表单 + IR/JSON 双向同步 | 单一真相源+派生视图；GenericKestraTask 必需 |
| F-10 自动保存 | debounce + sendBeacon | 防丢失，跨设备 |
| F-11 协同编辑 | MVP 不做，Phase 2 Yjs | React Flow + Yjs 成熟 |
| F-12 Parameters | MVP 预留，Phase 2 UI | Palantir Parameters |
| F-13 HITL | deploy/build 走 MetadataApprovalToolset | 图探索 write/action 一致 |
| F-14 Schema 预览 | MVP 推演（同步），Phase 2 增量执行+数据样本 | Palantir Preview |
| F-15 iframe 透出 | 独立路由，不嵌入画布 | 路线 C |

**核心设计：AI FDE 复用图探索实践**（不另起炉灶）：
- 复用 `/ai/agent` AG-UI 端点 + AssistantUiChat 组件
- 新增 `pipeline_builder` toolset（8 工具：list_datasets/add_source/add_transform/add_sink/modify_node/remove_node/connect/get_dataset_schema）
- 工具发 `state.pipeline_canvas` 的 STATE_SNAPSHOT 驱动 React Flow（与图探索 `state.canvas` 驱动 Cytoscape 同模式）
- PipelineBuilderAgent（HttpAgent 子类）tap SSE + 三层指纹去重（复用图探索死循环防护）
- deploy/build 走 MetadataApprovalToolset HITL（与图探索 write/action 一致）

**核心架构原则：单一真相源 + 派生视图**：
- graph（nodes+edges）存在 Zustand store（唯一真相）
- 表单/JSON/画布布局都是派生视图，改一个→store更新→其他自动同步
- 布局是纯函数（dagre 从 graph 派生 positions，可替换为 elkjs）

**与图探索的一致性**：AI 架构完全一致（AG-UI + STATE_SNAPSHOT + AssistantUiChat + HITL + 死循环防护），仅画布引擎（Cytoscape→React Flow，场景不同）和 AI 面板位置（左侧→底部，主交互不同）调整。

**替代方案**（已否决）：
- AIGenerate 单次结构化生成：无法多轮微调，用 AG-UI ReAct Agent
- useState 管 nodes/edges：生产级必崩，用 Zustand
- MVP 只表单：GenericKestraTask 必需 JSON 模式，MVP 就做双模
- AI 面板嵌画布：干扰编辑，放底部可收起

**详细设计见** [pipeline-builder-design.md §14 前端设计](../design/pipeline-builder-design.md#14-前端设计)

## 替代方案

### A1: 直接用 SeaTunnel SQL Transform 做转换（不引入 Kestra）
放弃。SeaTunnel 的 Transform 能力有限（SQL Transform 为主），无 DAG 可视化、无 Schema 推演、无版本管理、无调度触发器、无执行状态机。强行扩展 SeaTunnel 等于把它改造成编排平台，违反「不侵入修改开源软件」原则。

### A2: 用 dbt 做转换层
用户明确提到「以后如果有需要再引入 dbt，当前先不做」。dbt 是 SQL 转换专用，无可视化拖拽、无 Schema 推演引擎、无数据搬运能力（需配合 Kestra/Airflow 编排）。未来若引入，定位是「Kestra Flow 内的 SQL 转换 Task 类型之一」，不替代 Pipeline IR。

### A3: 自研全套（DAG 调度 + Schema 引擎 + 执行引擎）
放弃。调度/状态机/触发器/重试/Worker 都是成熟问题，Kestra 已解决。自研投入产出比极低，且难以达到生产级稳定性。自研聚焦在 Gaia 独有价值：Pipeline IR + Schema 推演 + Ontology 绑定。

## 影响

### 新增代码
- `src/ontology/core/models/pipeline.py` —— ORM（Pipeline / PipelineNode / PipelineVersion / PipelineExecution）
- `src/ontology/core/schemas/pipeline.py` —— pydantic Schema（含 IR 定义）
- `src/ontology/layers/pipeline/kestra_engine.py` —— Kestra REST 客户端 + IR→Flow 翻译器（含 Task 路由：转换→DuckDB，搬运→SeaTunnel，联邦→Trino）
- `src/ontology/services/pipeline_builder_service.py` —— 业务编排（CRUD / Schema 推演 / 部署 / 执行监控）
- `src/ontology/services/schema_inference_engine.py` —— Schema 推演引擎
- `src/ontology/routes/pipeline_builder.py` —— REST 路由

### 修改代码
- `src/ontology/core/models/datasource.py` —— DatasetGovernanceModel 新增 `current_snapshot_id` / `snapshot_retention`
- `src/ontology/config/container.py` —— 注入 KestraEngine / PipelineBuilderService / SchemaInferenceEngine
- `docker-compose.yml` —— 新增 Kestra 服务（JDBC 后端复用 PostgreSQL）；Kestra Worker 镜像需含 DuckDB + plugin-jdbc-duckdb（通过自定义 Dockerfile 或 init script 安装）
- `pyproject.toml` —— 新增 `duckdb` 依赖（Gaia 后端若需直接调 DuckDB 做预览/校验，否则仅 Kestra Worker 用）

### 不修改的代码（保持现有定位）
- `src/ontology/layers/engine/trino_query_engine.py` —— **保持只读**，不扩展写入能力（Trino 定位是查询引擎）
- `src/ontology/layers/pipeline/sea_tunnel_engine.py` —— 保留，被 Kestra 编排，不替换
- `src/ontology/services/datasource_service.py` —— 保留，非管道场景仍用 DataSourceService 编排搬运

### Alembic migration
- 业务表 schema 变更走 Alembic（新增 pipeline_* 表 + datasets 表加列）

### 文档同步
- `docs/architecture/implementation-status.md` 新增 §十五 Pipeline Builder
- `docs/design/data-flow-diagrams.md` 新增「流 G：管道转换加工」

## 待评审问题

1. Kestra 部署形态：standalone（单 JVM，开发）vs Kubernetes Helm（生产）—— MVP 用 standalone docker-compose，生产再切 Helm？
2. Kestra 与 SeaTunnel/Trino/DuckDB 的网络拓扑：同一 docker network，Kestra 通过 HTTP 调 SeaTunnel REST API（`http://seatunnel-master:5801`）、通过 JDBC 调 Trino（`jdbc:trino://trino:8080`）、通过 JDBC 调 DuckDB（嵌入式，Worker 进程内）？
3. 管道写入 Iceberg 的并发控制：多管道同时写同一 Dataset 如何避免冲突？（建议：Dataset 级写锁 / 串行化）
4. DuckDB 与 Iceberg REST Catalog 的集成方式：DuckDB iceberg extension 读 Gravitino 9001 REST Catalog（与 Trino 共用），还是直接读 Iceberg metadata 文件？（需 spike 验证，DuckDB iceberg extension 对 REST Catalog 的支持成熟度）
5. DuckDB 写 Iceberg 的方式：DuckDB 原生写 Iceberg 表（需 iceberg extension 写支持，目前可能不完整）vs DuckDB 写 Parquet 后由 IcebergStore 注册为 snapshot？（需 spike 验证）
6. 大规模转换（TB+）的路由：MVP 不做，Phase 3 引入 Spark 还是扩展 Trino 写入？（倾向引入 Spark，保持 Trino 只读定位）
