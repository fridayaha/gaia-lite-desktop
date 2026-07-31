# Pipeline Builder 设计文档

> **版本**：v0.1（Proposed） | **日期**：2026-07-14
> **ADR**：[ADR-018](../architecture/adr-018-pipeline-builder.md)
> **对标**：Palantir Foundry Pipeline Builder
> **状态**：设计阶段，未开始实现

---

## 目录

- [0. 文档定位与阅读路径](#0-文档定位与阅读路径)
- [1. Palantir Pipeline Builder 设计参考（提炼）](#1-palantir-pipeline-builder-设计参考提炼)
- [2. Gaia 的现实约束](#2-gaia-的现实约束)
- [3. 设计原则：对齐与调整](#3-设计原则对齐与调整)
- [4. 整体架构](#4-整体架构)
- [5. Pipeline IR 中间表示](#5-pipeline-ir-中间表示自研核心)
- [6. Schema 计算引擎](#6-schema-计算引擎自研核心壁垒)
- [7. 转换算子体系](#7-转换算子体系)
- [8. 执行层：Kestra 引擎适配](#8-执行层kestra-引擎适配)
- [9. 输出层：Dataset 与 Ontology 绑定](#9-输出层dataset-与-ontology-绑定)
- [10. 调度与触发](#10-调度与触发)
- [11. 版本化与生命周期](#11-版本化与生命周期)
- [12. 与现有架构的集成边界](#12-与现有架构的集成边界)
- [13. 约束、边界与不做的事](#13-约束边界与不做的事)
- [14. 分期实施路线](#14-分期实施路线)
- [15. 待评审问题](#15-待评审问题)

---

## 0. 文档定位与阅读路径

本文档是 Gaia Pipeline Builder 的**设计文档**，不是实现文档。它回答四个问题：

1. **Palantir Pipeline Builder 是什么、好在哪里**（§1，来自 10 轮参考资料的系统提炼）
2. **Gaia 现在有什么、缺什么、能复用什么**（§2，Gaia 现有架构盘点）
3. **Gaia 要做一个什么样的 Pipeline Builder**（§3 原则 + §4~§12 设计）
4. **不做什么、边界在哪**（§13）

读者路径建议：
- **决策评审**：读 §1 → §2 → §3 → §13，理解定位与边界
- **架构设计**：读 §4 → §5 → §6 → §8，理解三层架构核心
- **后续实现**：读 §7 → §9 → §10 → §11 → §14，理解能力与分期

---

## 1. Palantir Pipeline Builder 设计参考（提炼）

> 本节是对 Palantir Foundry Pipeline Builder 10 轮深度研究资料的系统性提炼，作为 Gaia 设计的对标基准。Palantir 的能力是「全栈自研 + 深度治理」的产物，Gaia 不可能也不应该全盘照搬，但核心设计思想必须吃透。

### 1.1 本质定位

Pipeline Builder 不是「更好用的 ETL 工具」，而是 Palantir Foundry **本体驱动架构下，企业数据资产的生产与治理枢纽**。

- 传统 ETL：以数据表为中心，目标是把原始数据加工成数仓表
- Pipeline Builder：以业务本体为中心，目标是把原始数据变成可直接支撑业务决策的标准化资产，并在生产过程中自动完成治理、权限、质量、审计

它的竞争力从来不是「拖拽界面更友好」，而是把软件工程几十年积累的成熟方法论——**编译期检查、声明式编程、契约式设计、版本控制、模块化复用、分层治理**——系统性平移到了数据工程领域，并通过 Ontology 把管道锚定到业务价值上。

### 1.2 六大核心设计原则

| 原则 | 内涵 | Gaia 适配度 |
|------|------|------------|
| **Schema 与计算解耦** | 独立的 Schema 计算引擎，拖拽时只推演字段/类型，不碰数据，错误实时标红，校验通过才执行。调试周期从分钟级压缩到秒级 | ✅ 核心壁垒，必须自研 |
| **声明式设计** | 用户描述「目标」（输出契约），系统决定「路径」（自动路径裁剪、公共子表达式消除、算子重排） | ✅ 采纳，IR 优化器分阶段实现 |
| **契约式设计** | 每个节点有输入/输出契约，强类型安全，输出严格检查阻断构建，上游变更前向兼容性可预测 | ✅ 采纳，IR 节点契约 |
| **本体优先** | 管道终点不只是表，直接生成 Object Type / Link Type / Time Series，跳过「数仓→再建模→再应用」中间损耗 | ⚠️ 部分采纳，路线 A 分两步（见 §3.4） |
| **结构显性化** | 依赖关系是可见连线，转换逻辑是表单化配置，血缘自动生成。「认知从人的脑子迁移到系统本身」 | ✅ 采纳 |
| **全角色统一协作** | 零代码（拖拽）→ 低代码（表达式/自定义函数）→ 专业代码（导出 Spark/Flink 代码），同一份逻辑同一套治理 | ⚠️ 分阶段，MVP 只做零代码 + 表达式 |

### 1.3 三层内核架构

```
┌─ 可视化逻辑层（Graph UI）─────────────────────────────────┐
│  结构化 DAG 编辑器：节点元数据 + 连线元数据 + 管道全局元数据 │
│  增量 Schema 推演（仅重算受影响下游，毫秒级）               │
│  实时数据预览（采样 1000 行，轻量引擎执行局部 IR）          │
└────────────────────────────┬─────────────────────────────┘
                             ↓ 序列化
┌─ 统一转换抽象层（Pipeline IR）────────────────────────────┐
│  引擎无关的逻辑中间表示（增强版 DAG）：                     │
│  • 逻辑 DAG 拓扑（与画布一一对应）                         │
│  • 每节点完整契约（输入/输出 Schema + 参数 + 质量规则）     │
│  • 执行属性（Job Group / 缓存优先级 / 事务级别）            │
│  • 治理元数据（负责人 / 标签 / 业务域 / 数据等级）          │
│  四大职责：语义归一化 / 全局静态校验 / 全局执行优化 / 血缘生成 │
└────────────────────────────┬─────────────────────────────┘
                             ↓ 翻译
┌─ 多引擎执行层 ────────────────────────────────────────────┐
│  Spark（批，最全算子）/ Flink（流，有状态）/ DataFusion（极速）│
│  引擎自动路由 + 手动指定 + 一键转换（兼容性校验）          │
└───────────────────────────────────────────────────────────┘
```

**IR 是整个架构的灵魂**：它把业务逻辑从执行引擎抽离，变成可校验、可优化、可移植、可治理的标准化逻辑资产。这是 Pipeline Builder 从「玩具级可视化工具」升级为「企业级生产管道平台」的根本。

### 1.4 三种管道形态

| 形态 | 引擎 | 延迟 | 规模 | 状态 | 一致性 |
|------|------|------|------|------|--------|
| **Batch** | Spark | 分钟~小时 | GB~PB | 无（周期快照） | 快照级强一致 |
| **Streaming** | Flink | 秒~亚秒 | GB~TB 流 | 有状态+Checkpoint | 最终一致+Exactly-Once |
| **Faster** | DataFusion | 秒~分钟 | MB~十GB | 无 | 快照级强一致 |

三者共享同一套 IR、同一套 Schema 校验、同一套治理体系，仅执行层不同。增量模式有三种：全量重建 / 增量追加 / CDC 合并（MERGE INTO）。

### 1.5 转换算子体系（六大类）

1. **行级清洗**：Filter / Rename / Drop / TypeCast / 去重 / 空值处理（谓词下推、列裁剪的核心优化对象）
2. **多表合并**：Join（7 种）/ Union / Split（单输入多输出）
3. **聚合窗口**：GroupBy / Pivot / 窗口函数（排名/累计/移动平均）
4. **结构解析**：JSON/XML 提取 / 数组展开 / 结构体打平
5. **数据质量**：非空 / 唯一主键 / 取值范围 / 正则 / 参照完整性，违规可警告/阻断/分流
6. **安全脱敏**：字段级脱敏（掩码/哈希/加密/替换）/ 行级权限过滤

外加 **AI/模型节点**（Trained Model 批量推理 + Use LLM 文本处理）和 **Custom Function**（可复用逻辑封装，行级/聚合/表级三档）。

### 1.6 输出能力（核心差异化）

管道输出不止数据表，直接对接 Foundry 本体：

- **Dataset**：版本化数据表（分区/分桶/压缩/版本保留）
- **Ontology Object / Link Type**：直接生成业务实体与关联（核心价值）
- **Time Series**：IoT/监控时序
- **外部导出**：JDBC / Kafka / 对象存储 / API

输出具备原子性（所有输出要么全成功要么全回滚）、一致性（基于同一份上游快照）、可见性（成功前下游不可见，成功后原子切换）。

### 1.7 配置体系（四层 + 参数化）

- **全局配置**：管道类型 / 引擎模式 / 分支策略 / 全局参数
- **Job Group 配置**：调度 / 资源 / 重试 / 优先级 / 告警
- **节点级配置**：算子参数 / 质量规则 / 脱敏策略 / 输出映射
- **部署运行时配置**：环境变量 / 资源队列 / 告警规则

**Pipeline Parameters（全局参数）**是模板化的核心：IR 中的强类型符号占位符，延迟绑定，支持基础类型 + 平台对象引用 + 复合类型，支撑多业务线复用、多环境隔离、Marketplace 分发。

### 1.8 部署运维与治理

- **类 Git 分支**：Master/Develop/Feature，逻辑+数据双重隔离，可视化差异对比，合并原子性
- **变更评审**：系统自动生成影响分析报告（下游影响/Schema变更/资源预估/质量变更）+ 人工审核
- **原子发布 + 秒级回滚**：逻辑版本 + 数据版本双重管控，数据回滚是元数据级操作（切 snapshot），秒级完成
- **四层可观测**：任务运行 / 数据质量 / 资源成本 / 端到端 SLA
- **安全合规**：RBAC+ABAC 五层权限、字段级脱敏/行级过滤、全链路审计、多租户四层隔离

### 1.9 性能优化方法论（四层递减收益）

1. **架构层**（收益最高）：管道类型选型 / 增量策略 / 分组设计 / 数仓分层
2. **逻辑层**：过滤前置 / 列裁剪 / Join 优化 / 聚合优化 / 算子选型
3. **引擎层**：分区管理 / 缓存策略 / 数据倾斜处理 / 流状态优化
4. **资源层**（收益最低）：Compute Profile / 资源队列 / GPU

绝大多数性能问题源于设计阶段偷懒，靠加资源永远无法根本解决，只会让成本随数据量线性增长。

### 1.10 Palantir 的关键启示（对 Gaia 的指导）

1. **IR 是壁垒，不是 UI**：可视化拖拽不是核心竞争力，引擎无关的标准化逻辑资产才是。Gaia 必须自研 Pipeline IR。
2. **Schema 引擎是第二壁垒**：编译期检查把调试周期压缩到秒级，这是零代码体验的基础。Kestra 没有这个能力，必须自研。
3. **Ontology 绑定是差异化**：管道终点是业务实体而非数据表，这是跳过中间损耗的关键。Gaia 必须做路线 A。
4. **治理是副产品不是项目**：血缘/权限/质量在开发过程中自动生成。Gaia 的 IR 要预留治理元数据位。
5. **不追求全栈自研**：执行引擎层（Spark/Flink/DataFusion）Palantir 自研是因为它是全栈平台，Gaia 基于开源栈，应复用 Kestra/SeaTunnel/Trino，自研聚焦在前两层（IR + Schema 引擎）+ Ontology 绑定。

---

## 2. Gaia 的现实约束

> 本节盘点 Gaia 现有架构，明确 Pipeline Builder 设计的起点边界。设计必须在这些约束内进行，不能脱离现有架构另起炉灶。

### 2.1 技术栈约束

| 维度 | Gaia 现状 | 对 Pipeline Builder 的影响 |
|------|----------|---------------------------|
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0 async | Pipeline IR / Schema 引擎 / Service 用 Python 实现 |
| 前端 | React 19 + Vite + Tailwind v4.3 + React Aria Components（ADR-013） | 画布用 React Flow（与 ADR-015 图探索画布范式统一） |
| 编排引擎 | **Apache Kestra**（新增） | 管道 DAG 调度 / 状态机 / 触发器 / 重试，docker-compose 新增独立服务 |
| 转换引擎 | **DuckDB**（新增，嵌入式） | 管道转换的 SQL 执行引擎（读 Iceberg 写 Iceberg），嵌入 Kestra Worker 进程，零新增服务 |
| 数据搬运 | SeaTunnel 2.3.13（6 种 pipeline 模板） | 保留，被 Kestra 编排（HTTP Task 调用），不替换 |
| 联邦查询 | Trino 478（Gravitino Connector，**纯只读**） | 只用于跨源联邦查询（VIRTUAL 表 JOIN），**不承担管道转换写入**（保持只读定位） |
| 主数据 | Iceberg 1.11.0（REST Catalog via Gravitino 9001） | 管道写入的唯一入口（通过 DuckDB CTAS/INSERT），snapshot 做版本管理 |
| 在线读 | Doris 4.0.5（ADR-001 在线读主源） | **管道不直接写 Doris**，由 IndexSyncService 独立同步 |
| 元数据 | PostgreSQL 16（业务本体 + object_state + outbox + datasets 治理） | Pipeline IR / 版本 / 执行记录存 PG，Kestra JDBC 后端也复用 PG（独立 schema） |
| 对象存储 | RustFS（S3 兼容） | Iceberg 底层存储，Kestra 内部存储也可用 |

### 2.2 现有「管道」能力盘点

Gaia 当前的「数据管道」由 `layers/pipeline/sea_tunnel_engine.py`（1319 行）承担，本质是**数据搬运管道**，对标 Palantir 的 **Data Connection（数据接入层）**，不是 Pipeline Builder（转换编排层）：

| 现有 pipeline 模板 | 能力 | 对标 Palantir |
|-------------------|------|--------------|
| `create_sync_pipeline`（main） | JDBC 源 → Iceberg 全量/增量搬运 | Data Connection |
| `create_file_sync_pipeline` | 文件 → Iceberg | Data Connection |
| `create_kafka_ingestion_pipeline` | Kafka → Iceberg | Data Connection |
| `create_kafka_timeseries_pipeline` | Kafka → TimescaleDB | Data Connection |
| `create_external_cdc_pipeline` | 外部 CDC → Iceberg | Data Connection |
| ~~`create_index_pipeline`（backfill/stream）~~ | ~~Iceberg → Doris 同步~~ | **2026-07 T1.10 删除**。Doris 写入统一收口到 ObjectIndexFunnel（Python 侧直连），不再走 SeaTunnel |

**缺失的正是 Pipeline Builder 的核心**：
1. 数据转换编排（清洗/Join/聚合/计算的可视化 DAG）
2. 契约式校验（Schema 推演引擎）
3. 管道与本体绑定（输出映射到 ObjectType）
4. 管道版本化与生命周期（分支/部署/回滚/调度/监控）

### 2.3 现有 Dataset 抽象

`DatasetGovernanceModel`（`core/models/datasource.py`）是**治理记录**，不是可版本化读写的统一接口：

```
DatasetGovernanceModel:
  api_name, display_name, storage_location, partition_config
  source_dataset_api_name, data_source_api_name  # 血缘
  kind (MANAGED / VIRTUAL), is_view
  row_count_estimate
  project_id  # ADR-016 资产归属
  # ❌ 缺少 current_snapshot_id（当前对外可见版本）
  # ❌ 缺少 snapshot_retention（版本保留策略）
```

- `MANAGED` Dataset：数据落地 Iceberg，有 snapshot（TimeTravelService 已用），但未激活版本管理
- `VIRTUAL` Dataset：Trino 联邦代理指针（不落地），**无版本概念，管道不可写入**（红线 9）

### 2.4 现有 Ontology 绑定机制

`OntologyService.link_dataset`（`services/ontology_service.py:1236`）已实现 ObjectType ↔ Dataset 绑定：

- 输入：`ontology_api_name` + `type_name` + `dataset_api_name` + `column_mappings`（每个 property 必须映射）
- 校验：`storage_type` 必须匹配 `dataset.kind`（MANAGED↔MANAGED / VIRTUAL↔VIRTUAL）
- 物理定位：MANAGED 用 Iceberg 默认（catalog=iceberg, schema=ICEBERG_NAMESPACE, table=dataset_api_name）；VIRTUAL 解析 storage_location 三段定位符

**这意味着**：管道输出到 Dataset（Iceberg）后，阶段 2 只需调用 `link_dataset` 即可完成本体绑定，**机制已就绪**，不需要新发明。

### 2.5 现有 IR：ObjectSet IR（ADR-015）

Gaia 已有 ObjectSet IR（13/15 type 对齐 Palantir），用于**只读查询推理线**（DataFrameQueryService.execute）。它与 Pipeline Builder 的关系：

| | ObjectSet IR（ADR-015） | Pipeline IR（本文档） |
|---|---|---|
| 语义 | 描述「查询什么数据」 | 描述「如何加工数据」 |
| 方向 | 只读查询 | 写入加工 |
| 结构 | filter/join/aggregate/traverse/searchAround... | source→transform→sink 的 DAG + 契约 + 执行属性 |
| 输出 | ReasoningResult（结果集 + 证据链） | Dataset（新 snapshot）/ ObjectType 绑定 |

**结论**：两者正交，不强行统一。Pipeline IR 的转换节点内部可按需复用 ObjectSet IR 描述查询语义（如 Join 节点的数据选取），但 Pipeline IR 本身是独立的 DAG + 契约 + 执行属性模型。避免过度抽象。

### 2.6 红线约束（必须遵守）

Pipeline Builder 设计必须遵守 Gaia 现有红线（见 CLAUDE.md）：

| 红线 | 对 Pipeline Builder 的约束 |
|------|---------------------------|
| **#3 Iceberg 是主数据唯一写入入口** | 管道写入只落地 Iceberg（通过 DuckDB CTAS/INSERT 或 SeaTunnel sink），不直接写 Doris/PG 业务表 |
| **#4 Doris 是在线读主源** | 管道不直接写 Doris，Doris 同步由 IndexSyncService 独立触发（管道可配置「触发索引同步」选项） |
| **#9 VIRTUAL 目标禁止写入** | 管道输出节点拒绝 VIRTUAL Dataset 作为目标（ValidationError） |
| **#10 物理命名走 snake_case** | 管道中间表 / 输出 Dataset api_name / Iceberg 表名走 `core/naming.py`，不用业务 api_name 泄漏 |
| **#11 Ontology API 不吃 NL** | Pipeline Builder 配置是结构化 IR，管道构建/编辑不引入自然语言（AI 辅助建模走 `/ai/*` 路由） |
| **基于开源不侵入修改** | Kestra/SeaTunnel/Trino 通过原生 HTTP API 调用，不修改其源码 |
| **Schema 变更走 Alembic** | 新增 pipeline_* 表 + datasets 表加列，必须配 migration |

### 2.7 与 Palantir 的根本差异（决定设计取舍）

| 维度 | Palantir | Gaia | 设计影响 |
|------|----------|------|----------|
| 执行引擎 | 全栈自研（Spark/Flink/DataFusion 适配） | 基于开源四引擎分工（Kestra 编排 + DuckDB 转换 + SeaTunnel 搬运 + Trino 只读联邦） | 执行层不自研，复用开源 |
| Versioned Dataset | 自研完整抽象 | Iceberg snapshot + DatasetGovernance 治理记录 | 复用 + 激活 snapshot，不新建抽象层 |
| Ontology 生成 | 管道直接生成 Object Type schema | ObjectType 用户定义 + 管道映射/建议 | 路线 A 分两步，不「生成」schema |
| 多引擎 | 批/流/极速三引擎统一 IR | MVP 用 DuckDB（对应 Faster），批（Spark）/流（Flink）延后 | IR 预留引擎无关性 |
| 治理底座 | Foundry 全栈原生 | Gaia 治理体系在建（ADR-016/017） | 治理元数据预留位，对接现有权限体系 |
| 代码双向互通 | 完整支持（导出 Spark/Flink 代码） | MVP 不做 | 延后，先保证零代码闭环 |


---

## 3. 设计原则：对齐与调整

> 基于 §1 的 Palantir 参考与 §2 的 Gaia 约束，明确 Gaia Pipeline Builder 的设计原则：哪些对齐 Palantir、哪些因约束而调整、哪些不做。

### 3.1 坚定对齐的原则（不动摇）

**P1. Schema 与计算解耦 —— 自研 Schema 推演引擎**
这是 Pipeline Builder 区别于传统 ETL 的根本，也是零代码体验的基础。无论执行引擎用什么，Schema 推演必须自研，且必须做到毫秒级增量推演、完全不碰真实数据。Kestra 没有这个能力，不能因为引入 Kestra 就放弃。

**P2. 契约式设计 —— IR 节点强类型契约**
每个节点有明确的输入契约和输出契约，强类型校验，错误实时标记，输出严格检查阻断构建。这是「编译期检查」思想在数据工程的落地，不能因为 MVP 而放松。

**P3. 本体优先 —— 路线 A（管道感知 Ontology）**
管道终点必须能映射到 ObjectType，不能只输出数据表（否则就和传统 ETL 无异，浪费 Gaia 本体驱动架构的核心价值）。分两步实施（见 §3.4），但方向不变。

**P4. 结构显性化 —— 可视化 DAG + 表单化配置 + 自动血缘**
依赖关系是可见连线，转换逻辑是表单化配置（不是藏代码里），血缘自动生成。认知沉淀在系统里，不依赖关键人员。

**P5. 基于开源不侵入 —— 执行层四引擎分工，每个引擎只做擅长的事**
执行引擎层不自研，复用开源四引擎：Kestra（编排）+ DuckDB（转换）+ SeaTunnel（搬运）+ Trino（只读联邦）。每个引擎职责正交不越界：Trino 保持只读不扩展写入，DuckDB 填补转换执行空白，SeaTunnel 保留被编排。自研聚焦在 Gaia 独有价值：Pipeline IR + Schema 引擎 + Ontology 绑定。

### 3.2 因约束而调整的原则

**A1. 全角色统一协作 —— MVP 只做零代码 + 表达式**
Palantir 的零代码→低代码→专业代码三档梯度，Gaia MVP 只做前两档（拖拽内置算子 + 表达式），专业代码（Python 脚本任务 / 代码双向导出）延后。理由：MVP 聚焦最小闭环，专业代码依赖 Kestra plugin-script-python 深度集成，复杂度高。

**A2. 声明式优化 —— IR 优化器分阶段实现**
Palantir 的谓词下推、列裁剪、公共子表达式消除是 IR 优化器自动完成。Gaia MVP 先做「Schema 校验 + 拓扑校验」，全局执行优化（路径裁剪、公共节点合并）延后到 Phase 2。理由：DuckDB 自身有查询优化器（向量化+谓词下推），管道级优化可先依赖 DuckDB，Gaia 层优化后续叠加。

**A3. 多引擎统一 —— MVP 用 DuckDB（对应 Faster），批/流延后**
Palantir 批/流/极速三引擎统一 IR，Gaia MVP 用 DuckDB 做转换执行（对应 Palantir Faster Pipeline，单节点向量化，GB~十GB 级）。大规模分布式批（Spark）和流（Flink）延后，但 IR 设计预留引擎无关性，未来新增引擎只需新增 IR→执行翻译器，IR 层不变。

**A4. 治理原生内嵌 —— 治理元数据预留位，对接现有体系**
Palantir 的权限/血缘/质量是 Foundry 全栈原生。Gaia 的治理体系在建（ADR-016/017 权限、血缘在 implementation-status §14.1 待实现）。Pipeline IR 预留治理元数据位（负责人/标签/业务域/数据等级），Phase 1 先做血缘自动生成（管道节点→Dataset→ObjectType 的字段级映射记录），权限/质量规则对接现有体系。

### 3.3 明确不做的原则（MVP 边界）

**N1. 不做 Streaming Pipeline / 大规模分布式 Batch** —— Streaming 需引入 Flink，大规模 Batch 需引入 Spark 或扩展 Trino 写入（逆定位），MVP 用 DuckDB 覆盖 GB~十GB 级足够
**N2. 不做 CDC 合并模式（管道级 MERGE）** —— IcebergStore.merge 已有能力，但管道级 CDC 调度链路复杂，延后
**N3. 不做 Job Group 分组** —— MVP 单输出/单 Flow，多输出分组延后
**N4. 不做分支评审 / 灰度发布** —— MVP 主干直接部署，分支评审延后（依赖 ADR-016 权限体系完善）
**N5. 不做 AI/LLM 节点** —— 延后到 AI 原生管道阶段
**N6. 不做 Custom Function** —— MVP 用内置算子 + 表达式，自定义函数延后
**N7. 不做代码双向导出** —— 延后
**N8. 不做 Marketplace 分发 / 多租户** —— 延后

### 3.4 Ontology 路线 A 的两步实施（关键决策）

**背景**：Palantir 的管道直接生成 Object Type schema；Gaia 的 ObjectType 是用户定义的（元数据驱动），管道不应「生成」schema 定义，而应「映射到」已存在的 ObjectType 或「建议新建」。

**阶段 1（MVP）：映射到已有 ObjectType**
- 管道输出节点配置：用户选择已存在的 ObjectType + 字段映射（property → 输出字段）
- 管道执行时：写入 ObjectType 绑定的 Iceberg 表（通过 `link_dataset` 已解析的物理表名）
- **不写 Doris idx 表**（那是 IndexSyncService 的独立链路，ADR-008）
- 管道可配置「触发索引同步」选项，调用 `IndexSyncService.sync_now`（容灾兜底路径，非主链路）

**阶段 2：新建 ObjectType（AI 辅助）**
- 管道输出节点支持「新建 ObjectType」：根据输出 Schema + 用户配置，调用 OntologyService 创建 ObjectType + link_dataset
- AI 辅助：基于输出 Schema 建议 ObjectType 的 property 划分、api_name、display_name（走 `/ai/*` 路由，符合红线 11）
- 实现 Palantir 式的「管道生成对象」体验，但保留 Gaia「用户确认 + 元数据驱动」的底线

**关键约束（与用户确认）**：
- 管道写入只落地 Iceberg（通过 DuckDB CTAS/INSERT），Doris 同步是独立链路
- Dataset 抽象只关联 Iceberg snapshot，不关联 Doris / 外邦表

---

## 4. 整体架构

### 4.1 架构总览

核心：**四引擎分工**——Kestra 编排、DuckDB 转换、SeaTunnel 搬运、Trino 只读联邦。每个引擎职责正交不越界。

```
┌─────────────────────────────────────────────────────────────────────┐
│  前端：Pipeline Builder 画布（React 19 + React Flow）                  │
│  拖拽节点 / 连线 / 配置参数 / 实时 Schema 校验提示 / 预览              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST API（结构化 IR，不吃 NL，红线 11）
┌──────────────────────────────▼──────────────────────────────────────┐
│  Routes（/pipelines，薄层）                                           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│  PipelineBuilderService（业务编排层）                                  │
│  ├─ CRUD（管道 / 节点 / 版本）                                         │
│  ├─ Schema 推演调度（调用 SchemaInferenceEngine，毫秒级）              │
│  ├─ 部署（IR → Kestra Flow 翻译 → 提交 Kestra）                        │
│  └─ 执行监控（轮询 Kestra Execution 状态）                             │
└──────┬───────────────────────┬──────────────────────────┬───────────┘
       │                       │                          │
┌──────▼──────────────┐ ┌──────▼──────────────┐ ┌────────▼────────────┐
│ SchemaInferenceEngine│ │ KestraEngine        │ │ OntologyService     │
│ (自研核心壁垒)        │ │ (IR→Flow 翻译+REST) │ │ (复用，阶段2绑定)    │
│ 增量推演 + 契约校验   │ │ 含 Task 路由决策     │ │ link_dataset 已就绪  │
└─────────────────────┘ └──────────┬──────────┘ └─────────────────────┘
                                   │ HTTP（原生 API）
┌──────────────────────────────────▼──────────────────────────────────┐
│  Apache Kestra（编排引擎，docker-compose 新增，JDBC 后端复用 PG）      │
│  ├─ Flow YAML（IR 的物理投影，单向翻译，不反向生成 IR）                │
│  ├─ Scheduler / Executor / Worker / Webserver                         │
│  └─ Task 路由（KestraEngine 翻译时决定）：                             │
│     ├─ 转换算子 → io.kestra.plugin.jdbc.duckdb.Query（SQL 转换）      │
│     ├─ 数据搬运 → io.kestra.plugin.core.http.Request（调 SeaTunnel）  │
│     ├─ 跨源联邦 → io.kestra.plugin.jdbc.trino.Query（只读查询）       │
│     ├─ 质量校验 → io.kestra.plugin.core.flow.If + DuckDB Query        │
│     └─ 控制流 → io.kestra.plugin.core.flow.Sequential/Parallel/If     │
└───┬───────────────────┬───────────────────────┬───────────────────┘
    │ JDBC（嵌入式）      │ HTTP                  │ JDBC（只读）
    ▼                    ▼                       ▼
┌──────────────┐  ┌──────────────┐  ┌───────────────────────────────┐
│ DuckDB       │  │ SeaTunnel    │  │ Trino（现有，保持只读）         │
│ 转换执行引擎  │  │ 数据搬运引擎  │  │ 跨源联邦查询引擎                │
│ (嵌入式，     │  │ (独立服务，   │  │ (独立服务，已有，不扩展写入)     │
│  零新增服务)  │  │  已有，保留)  │  │ VIRTUAL 表联邦红线不可替代      │
│ 读Iceberg写  │  │ 25连接器+CDC │  │                                │
│ Iceberg      │  │ →Iceberg     │  │ 只读查询，不参与管道转换写入     │
│ =Faster引擎  │  │ =Data Conn.  │  │                                │
└──────┬───────┘  └──────┬───────┘  └───────────────────────────────┘
       │                 │
       ▼                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Iceberg（Dataset 物理存储 + snapshot 版本管理）                   │
│  管道写入的唯一入口（红线 3，DuckDB CTAS/INSERT），                │
│  Doris 同步是独立链路（红线 4，IndexSyncService）                  │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 四引擎分工与三层架构对应

**四引擎分工**（执行层，每个引擎职责正交）：

| 引擎 | 角色 | 部署 | 读写 | 对应 Palantir | MVP |
|------|------|------|------|--------------|-----|
| Kestra | 编排（DAG调度/状态机/触发器/重试） | 独立服务（新增） | 不执行数据 IO | Palantir 自研编排层 | ✅ |
| DuckDB | 转换执行（SQL 转换，读Iceberg写Iceberg） | 嵌入式（Kestra Worker 内） | ✅ 完整读写 | Faster Pipeline（DataFusion） | ✅ |
| SeaTunnel | 数据搬运（source→Iceberg，25连接器+CDC） | 独立服务（已有） | 写 Iceberg | Data Connection | ✅ 被编排 |
| Trino | 只读联邦查询（跨源JOIN，VIRTUAL表） | 独立服务（已有） | ❌ 只读 | 联邦查询（非对应物） | ✅ 不扩展 |

**三层架构对应**：

| Palantir 层 | Gaia 实现 | 自研/复用 | 说明 |
|-------------|----------|----------|------|
| Graph UI | React 19 + React Flow 画布 | 自研前端 | 复用 ADR-015 图探索画布范式 |
| Pipeline IR | Pipeline IR（DAG + 契约 + 执行属性） | **自研** | 引擎无关的逻辑中间表示 |
| Schema 计算引擎 | SchemaInferenceEngine | **自研** | Kestra 无此能力，核心壁垒 |
| Spark 批引擎 | （Phase 3，引入 Spark） | — | TB+ 级大规模分布式转换，MVP 不做 |
| Flink 流引擎 | （Phase 3，引入 Flink） | — | 流式转换，MVP 不做 |
| DataFusion 极速 | **DuckDB**（MVP） | 复用 | 嵌入式向量化，GB~十GB 级 |
| Versioned Dataset | DatasetGovernanceModel + Iceberg snapshot | 复用 + 激活 | 不新建抽象层 |
| Ontology 输出 | OntologyService.link_dataset（阶段2） | 复用 | 机制已就绪 |
| 血缘自动生成 | IR 节点→Dataset→ObjectType 映射记录 | 自研 | 预留位，Phase 1 先记录 |
### 4.3 与现有数据流的衔接（新增「流 G」）

现有 6 条主干流（见 [data-flow-diagrams.md](data-flow-diagrams.md)）新增「流 G：管道转换加工」：

```
流 G：管道转换加工
  外部数据源 ─[SeaTunnel 搬运，流 A]→ Iceberg(Dataset A，MANAGED)
                                       │
                  Pipeline Builder 画布：Dataset A → Filter → Join Dataset B
                                       │                         → Aggregate → 输出
                                       ↓ IR 翻译（单向）
                  Kestra Flow 执行（DuckDB SQL 转换算子）
                                       ↓
                  Iceberg(Dataset C，新 snapshot，原子提交)
                                       │
                  ┌────────────────────┼────────────────────┐
                  ↓ （可选）            ↓ （阶段2）           ↓ （Phase 2 血缘）
        IndexSyncService          OntologyService        血缘记录
        .sync_now（容灾兜底）      .link_dataset          字段级映射
                  ↓                       ↓
        Doris idx 表（独立链路）    ObjectType 绑定 Dataset C
        （流 B 查询主源）           （流 B/C 查询）
```

**关键边界**：
- 管道写入只到 Iceberg（红线 3）
- Doris 同步是独立链路（红线 4），管道可触发但不直接写
- Dataset 只关联 Iceberg snapshot，不管 Doris / 外邦表

### 4.4 核心组件清单

| 组件 | 职责 | 自研/复用 | 状态 |
|------|------|----------|------|
| `PipelineBuilderService` | 管道 CRUD / Schema 推演调度 / 部署 / 轻量执行状态 | 自研 | 新增 |
| `SchemaInferenceEngine` | 增量 Schema 推演 + 契约校验（核心算子） | 自研 | 新增 |
| `KestraEngine` | IR→Kestra Flow 翻译 + Kestra REST 客户端 + Task 路由 | 自研（适配层） | 新增 |
| `Pipeline IR`（schemas/pipeline.py） | 引擎无关的逻辑中间表示（含 GenericKestraTask 节点） | 自研 | 新增 |
| `pipeline.py`（models） | ORM：Pipeline/Node/Version/Execution | 自研 | 新增 |
| **Gaia UI 画布**（React Flow） | 拖拽编辑 + 核心算子表单 + Schema 提示 + Ontology 绑定 | 自研 | 新增 |
| **Kestra UI iframe 透出层** | 执行详情/日志/metrics/高级算子/Dashboard（iframe 嵌入） | 复用 Kestra | 新增（集成层） |
| `OntologyService.link_dataset` | ObjectType↔Dataset 绑定 | 复用 | 已就绪 |
| `IcebergStore` | Iceberg 读写 + snapshot | 复用 | 已就绪 |
| `DuckDBEngine`（新增适配层） | DuckDB 转换执行（读Iceberg写Iceberg） | 复用（嵌入式） | 新增（Kestra plugin-jdbc-duckdb） |
| `TrinoQueryEngine` | 跨源联邦查询（只读，VIRTUAL 表 JOIN） | 复用 | 已就绪，**不扩展写入** |
| `SeaTunnelEngine` | 数据搬运 | 复用 | 已就绪，被 Kestra 编排 |
| `IndexSyncService` | Iceberg→Doris 同步 | 复用 | 已就绪 |
| Apache Kestra | 编排引擎（调度/状态机/触发器/1700+插件/No-Code编辑器/Dashboard） | 复用 | docker-compose 新增 |

**职责分层**（路线 C）：
- **Gaia UI 自研层**（独有价值）：画布编辑 + 核心算子表单 + Schema 推演 + Ontology 绑定 + 轻量执行状态
- **Kestra UI 透出层**（复用）：执行详情/日志/metrics + 1700+ 插件 No-Code 配置 + Dashboard + 高级调度配置
- **不做**：不自研执行监控 UI、不逐个映射 1700 插件、不自研 Dashboard（详见 §7.0 算子分层策略 + §16.7）

---

## 5. Pipeline IR 中间表示（自研核心）

> IR 是 Gaia Pipeline Builder 的灵魂，引擎无关的标准化逻辑资产。本节定义 IR 的结构、契约、执行属性、治理元数据。

### 5.1 IR 的设计目标

1. **引擎无关**：不包含任何 Kestra/Trino/SeaTunnel 特有语法，只描述「做什么」
2. **可校验**：支持 Schema 推演引擎做全链路静态校验（见 §6）
3. **可优化**：预留优化器接入点（路径裁剪/公共子表达式消除，Phase 2）
4. **可治理**：承载血缘、权限、质量、审计元数据
5. **可移植**：同一套 IR 可在不同环境（开发/测试/生产）运行，未来可切换引擎

### 5.2 IR 的核心结构

IR 是一个增强版 DAG 对象，包含四类元数据：

**（1）逻辑 DAG 拓扑**
- 节点列表（每个节点有唯一 ID、类型、配置）
- 连线列表（上游输出端口 → 下游输入端口）
- 与画布一一对应

**（2）节点契约（每个节点）**
- `input_contract`：输入 Schema 约束（字段名/类型/可空性/主键要求/字段数）
- `output_schema`：输出 Schema 定义（推演生成，见 §6）
- `config`：节点配置参数（算子特定）
- `quality_rules`：数据质量规则（可选，附属于节点）

**（3）执行属性**
- `write_mode`：写入模式（FULL_REFRESH / APPEND / MERGE，MVP 只做前两种）
- `trigger_index_sync`：是否触发 Doris 同步（bool，调用 IndexSyncService）
- `compute_profile`：计算资源配置（MVP 用 Kestra 默认，Phase 2 扩展）
- `job_group`：作业分组（MVP 不做，单输出）

**（4）治理元数据（预留位）**
- `owner`：负责人
- `tags`：标签列表
- `business_domain`：业务域
- `data_classification`：数据等级
- `lineage`：血缘记录（字段级映射，Phase 1 自动生成）

### 5.3 节点类型

| 类型 | 说明 | 输入端口 | 输出端口 |
|------|------|---------|---------|
| `Source` | 读取 Dataset（MANAGED Iceberg 表） | 0 | 1 |
| `Transform` | Gaia 原生转换算子（Filter/Join/Aggregate...，核心 10 个，有 Schema 推演） | 1~2 | 1 |
| `GenericKestraTask` | Kestra 透出算子（封装任意 Kestra 插件，用户声明 Schema，见 §7.0） | 1~N | 1 |
| `QualityCheck` | 数据质量校验（可附属于 Transform，也可独立节点） | 1 | 1（正常）+ 1（异常分流，可选） |
| `Sink` | 输出到 Dataset（Iceberg 新 snapshot） | 1 | 0 |
| `OntologyMapping` | 映射到 ObjectType（阶段 2，附属于 Sink） | 1 | 0 |

**节点分层**（对应路线 C）：
- **Gaia 原生节点**（Source/Transform/QualityCheck/Sink/OntologyMapping）：IR 一等公民，有完整 Schema 推演 + Gaia UI 表单 + 精确翻译
- **透出节点**（GenericKestraTask）：封装任意 Kestra 插件，用户声明 Schema，iframe 跳转 Kestra No-Code 配置，翻译时原样透传

**MVP 不做的节点**：AI/LLM 节点（Phase 3）、Custom Function 节点（Phase 2）、外部导出节点（Phase 2）、Time Series 输出节点（Phase 2）。

### 5.4 IR 与 Kestra Flow 的关系（单向翻译）

```
Pipeline IR（引擎无关）  ──翻译──→  Kestra Flow YAML（执行投影）
     ↑                                  ↓
     │                              Kestra 执行
     │                                  ↓
     └──── 执行结果回写（状态/日志/指标） ──┘
     
     ❌ 不反向生成 IR（避免代码反向解析难题）
```

IR → Kestra Flow 的翻译规则（§8 详述）：
- `Source` 节点 → Kestra Task（DuckDB Query 读取 Iceberg 表，输出到 Kestra 内部存储）
- `Transform` 节点 → Kestra Task（DuckDB Query，SQL 由算子配置生成）
- `Sink` 节点 → Kestra Task（DuckDB CTAS/INSERT 写 Iceberg + 原子提交 snapshot）
- `QualityCheck` 节点 → Kestra If Task（DuckDB 校验 SQL，不通过则分流/阻断）
- 跨源联邦节点 → Kestra Task（Trino Query，只读，仅当涉及 VIRTUAL 表 JOIN 时路由）
- 节点连线 → Kestra Task 顺序 + outputs 传递

### 5.5 IR 与 ObjectSet IR 的关系（正交）

- **ObjectSet IR**（ADR-015）：只读查询推理线，描述「查询什么数据」
- **Pipeline IR**（本文档）：写入加工线，描述「如何加工数据」

两者不强行统一。Pipeline IR 的 Transform 节点内部可按需复用 ObjectSet IR 描述查询语义（如 Join 节点的数据选取条件），但 Pipeline IR 本身是独立的 DAG + 契约 + 执行属性模型。避免过度抽象。

---

## 6. Schema 计算引擎（自研核心壁垒）

> Schema 推演引擎是 Pipeline Builder 区别于传统 ETL 的根本，也是零代码体验的基础。Kestra 没有这个能力，必须自研。本节定义其推演链路、契约校验、增量计算、与执行引擎的解耦边界。

### 6.1 为什么必须自研（Kestra 的局限）

Kestra 的 `inputs` 是强类型校验（STRING/INT/FLOAT/SELECT/DATE...），但这是**执行入口参数校验**，不是**数据流字段的编译期推演**：

- Kestra 不知道「Task A 的输出表有哪些字段、什么类型」
- Kestra 不知道「Task B 的 Join 条件字段在 Task A 输出中是否存在」
- Kestra 无法在编辑时（未执行）就告诉你「字段名拼错」「类型不兼容」

这些正是 Palantir Schema 引擎的核心能力，是「调试周期从分钟级压缩到秒级」的实现关键。Gaia 必须自研。

### 6.2 推演链路（三步固定）

对任意一个转换节点，Schema 推演遵循固定三步：

1. **输入契约校验**：检查上游传入的 Schema 是否满足该节点的输入要求
   - 例：Join 节点需要左右表都有关联字段，且类型兼容
   - 例：Aggregate 节点的 group_by 字段必须存在于输入 Schema

2. **内部逻辑推演**：根据节点配置，计算输出 Schema
   - 字段列表（哪些字段保留/新增/删除）
   - 字段类型（Filter 不变；TypeCast 按配置；Join 合并两表；Aggregate 只剩分组字段+聚合结果）
   - 可空性（Left Join 右表字段自动可空；Aggregate 的 sum 可空）
   - 主键属性（Union 后主键可能不唯一）

3. **输出契约生成**：生成标准化的输出 Schema，传递给下游所有后继节点

### 6.3 增量推演机制（性能核心）

当用户修改某个节点时，**仅从该节点出发，向下游级联重新推演**：

- 上游节点及不相关分支的 Schema 结果完全复用，不重算
- 整个推演过程在后端内存中完成，不触碰任何真实数据
- 单节点变更的反馈延迟控制在毫秒级
- 上百节点的超大管线，修改一个字段名也能立刻看到下游报错

实现方式：维护每个节点的 `computed_schema` 缓存，变更时标记下游为 dirty，按拓扑序重算 dirty 节点。

### 6.4 多级契约校验体系

Schema 校验不是「对/错」二元结果，而是分级管控：

| 等级 | 含义 | 处理 |
|------|------|------|
| **ERROR** | 字段不存在、类型完全不兼容、主键重复 | 阻断构建，画布标红 |
| **WARNING** | 字段名大小写不一致、精度可能丢失、空值风险 | 提示但不阻断，画布标黄 |
| **INFO** | 字段被裁剪、新增衍生字段 | 仅记录，画布标灰 |

### 6.5 算子注册表（Schema 推演的可扩展基础）

每个转换算子注册三个函数：

```
class OperatorSpec:
    input_contract: InputContract       # 输入约束（字段数/类型/主键要求）
    infer_output_schema: Callable       # (input_schema, config) -> output_schema
    validate_config: Callable           # (config, input_schema) -> list[ValidationIssue]
```

新增算子 = 注册一个新的 OperatorSpec，Schema 引擎自动接入全链路推演。MVP 覆盖核心算子（见 §7），后续按需扩展。

### 6.6 与执行引擎的解耦边界

| | Schema 引擎 | 执行引擎（Kestra+Trino） |
|---|---|---|
| 状态 | 无状态 | 有状态（Kestra Execution） |
| 数据 | 不碰真实数据 | 加载全量数据 |
| 耗时 | 毫秒级 | 秒~分钟~小时级 |
| 作用 | 编译期校验 + 输出 Schema 推导 | 运行时执行 + 结果落地 |
| 交互 | Schema 校验通过的 IR 才提交执行 | 执行时若真实数据与 Schema 不符，抛运行时错误 |

绝大多数逻辑错误在 Schema 阶段就被拦截，不会浪费执行资源。

### 6.7 实时数据预览（Phase 2，MVP 不做）

Palantir 的「预览」是独立采样执行链路（抽 1000 行，轻量引擎执行局部 IR）。Gaia MVP 不做预览，Phase 2 用 Trino `LIMIT 1000` 采样 + 局部 IR 执行实现。理由：MVP 聚焦 Schema 校验闭环，预览依赖局部 IR 执行器，复杂度高。


---

## 7. 转换算子体系

### 7.0 算子分层策略（路线 C 核心：核心算子自研 + 扩展算子透传）

> 这是 Pipeline Builder 可扩展性的核心设计决策。解决"Kestra 有 1700+ 插件，Gaia IR 不可能逐个映射"的死锁问题。

**设计原则**：二八原则——核心算子自研（覆盖 80% 场景），扩展算子透传 Kestra（覆盖 20% 高级场景）。

```
┌─ Gaia 原生算子（IR 一等公民，自研 Schema 推演）─────────┐
│  Filter / Select / Rename / TypeCast / Join /            │
│  Aggregate / Union / Expression / QualityCheck           │
│  → Gaia UI 表单配置 + Schema 推演 + 翻译为 DuckDB SQL    │
│  → 固定 ~10 个，覆盖 80% 场景                              │
└──────────────────────────────────────────────────────────┘
          │ 用户需要更复杂算子时
          ▼
┌─ Kestra 透出算子（IR 的 GenericKestraTask，不做 Schema 推演）──┐
│  1700+ Kestra 插件（Python/Spark/dbt/HTTP/云服务...）           │
│  → IR 中表示为 GenericKestraTask 节点（type + raw config）      │
│  → Gaia UI 只提供"插入 Kestra Task"入口 + iframe 跳转 Kestra    │
│    No-Code 编辑器配置（基于插件 JSON Schema 自动生成表单）       │
│  → Schema 推演：输入/输出 Schema 用户声明（不做自动推演）        │
│  → 翻译时原样透传给 Kestra Flow                                 │
└──────────────────────────────────────────────────────────┘
```

**两类算子的对比**：

| 维度 | Gaia 原生算子（核心 10 个） | Kestra 透出算子（GenericKestraTask） |
|------|---------------------------|-------------------------------------|
| 数量 | 固定 ~10 个 | 1700+，随 Kestra 迭代自动增加 |
| Schema 推演 | ✅ 自动推演（核心壁垒） | ❌ 用户声明输入/输出 Schema |
| UI 配置 | Gaia UI 表单（自研） | iframe 跳转 Kestra No-Code（自动生成） |
| 翻译 | IR → DuckDB SQL（精确映射） | 原样透传 Kestra Flow（raw config） |
| 适用用户 | 业务分析师（零代码） | 高级用户/数据工程师（懂 Kestra） |
| 覆盖场景 | 80%（常规清洗/关联/聚合） | 20%（Python 脚本/Spark/dbt/云服务） |
| Gaia 维护成本 | 中（每个算子写 Schema 推演规则） | 零（透传，不维护） |

**为什么这样设计**：
1. **核心算子有完整体验**：Schema 推演 + 表单 + 翻译，Palantir 式零代码体验，这是 Gaia 独有价值
2. **扩展算子不堵死**：任何 Kestra 插件都能作为 GenericKestraTask 插入，用户在 Kestra No-Code 配置，Schema 用户声明
3. **Gaia 不背 1700 插件的映射包袱**：核心 10 个自研，其余透传，永远跟得上 Kestra 迭代
4. **代码量聚焦独有价值**：不重写 Kestra 已有的算子表单生成（Kestra 基于插件 JSON Schema 自动生成）

**GenericKestraTask 节点的 IR 表示**：

GenericKestraTask 是 IR 的一种特殊节点类型，封装任意 Kestra Task：
- `task_type`：Kestra 插件全限定名（如 `io.kestra.plugin.scripts.python.Script`）
- `task_config`：原始 Kestra Task 配置（YAML/JSON，由 Kestra No-Code 编辑器生成）
- `declared_input_schema`：用户声明的输入 Schema（用于上游契约校验）
- `declared_output_schema`：用户声明的输出 Schema（用于下游契约校验）
- 翻译时：原样作为 Kestra Flow 的一个 Task，不做任何转换

**Schema 推演引擎对 GenericKestraTask 的处理**：
- 输入契约校验：用用户声明的 input_schema 校验上游输出是否兼容
- 输出 Schema：直接采用用户声明的 output_schema（不做自动推演）
- 不深入 task_config 内部做字段级推演（那是 Kestra No-Code 的职责）

这样设计保证了：核心算子有 Palantir 级别的 Schema 推演体验，扩展算子虽然没自动推演但能用（用户声明 Schema + Kestra 配置），两不耽误。


> 参考 Palantir 六大类算子，结合 Gaia MVP 范围（Trino SQL 执行），定义 Gaia 的算子体系。MVP 覆盖核心算子，后续按需扩展。

### 7.1 算子分类与 MVP 范围

| 类别 | Palantir 算子 | Gaia MVP | 执行映射（DuckDB SQL） | 说明 |
|------|--------------|---------|----------------------|------|
| **行级清洗** | Filter / Rename / Drop / TypeCast / 去重 / 空值处理 | ✅ 全做 | `WHERE` / `SELECT AS` / `SELECT` 列裁剪 / `CAST` / `DISTINCT` / `COALESCE` | 谓词下推、列裁剪核心对象 |
| **多表合并** | Join（7种）/ Union / Split | ✅ Join（Inner/Left/Right/Full）/ Union；❌ Split 延后 | `JOIN` / `UNION ALL` | MVP 4 种 Join，交叉/半/反连接延后 |
| **聚合窗口** | GroupBy / Pivot / 窗口函数 | ✅ GroupBy + 常用聚合函数；⚠️ 窗口函数延后；❌ Pivot 延后 | `GROUP BY` + `SUM/COUNT/AVG/MIN/MAX` | 窗口函数（排名/累计/移动平均）Phase 2 |
| **结构解析** | JSON/XML 提取 / 数组展开 / 结构体打平 | ⚠️ JSON 提取（DuckDB `json_extract`）；❌ XML/复杂嵌套延后 | `json_extract` / `UNNEST` | MVP 只做 JSON，DuckDB 原生支持 |
| **数据质量** | 非空 / 唯一主键 / 取值范围 / 正则 / 参照完整性 | ✅ 非空 / 唯一 / 取值范围 / 正则；❌ 参照完整性延后 | `CHECK` 语义（Kestra If Task + 校验 SQL） | 违规可警告/阻断/分流 |
| **安全脱敏** | 字段级脱敏 / 行级权限过滤 | ❌ 延后 | — | 依赖 ADR-016 权限体系完善 |
| **AI/模型** | Trained Model / Use LLM | ❌ 延后 | — | AI 原生管道阶段 |
| **表达式** | 算术/字符串/日期/条件/正则/类型转换 | ✅ MVP 做 | DuckDB 函数 | 嵌入节点的强类型计算语言 |

### 7.2 算子的 Schema 推演规则（示例）

**Filter 节点**：
- 输入契约：1 个输入，表达式引用的字段必须存在于输入 Schema
- 输出 Schema：与输入相同（字段不变，行数变）
- 推演：校验表达式字段引用 → 输出 = 输入

**Join 节点**：
- 输入契约：2 个输入，关联字段在两表都存在且类型兼容
- 输出 Schema：左表字段 + 右表字段（重名自动加前缀），Left/Right Join 的可空性传递
- 推演：合并两表字段列表，标记可空性，检测重名

**Aggregate 节点**：
- 输入契约：1 个输入，group_by 字段存在，聚合表达式引用的字段存在
- 输出 Schema：group_by 字段 + 聚合结果字段（类型按聚合函数推导）
- 推演：缩减字段列表，推导聚合结果类型与可空性

**TypeCast 节点**：
- 输入契约：1 个输入，待转换字段存在
- 输出 Schema：字段列表不变，指定字段类型变更
- 推演：校验类型兼容性（STRING→INT 可能 WARNING），更新字段类型

### 7.3 表达式系统（嵌入节点的强类型计算语言）

参考 Palantir 表达式引擎，Gaia MVP 实现轻量版：

- **能力**：算术运算 / 字符串处理 / 日期时间 / 条件分支（CASE WHEN）/ 类型转换 / 嵌套函数
- **语法**：贴近 DuckDB SQL 表达式（降低学习门槛，且直接映射到 DuckDB 执行）
- **与 Schema 联动**：编写时参与 Schema 推演，实时类型校验，结果类型自动成为输出字段类型
- **MVP 不做**：自定义函数调用（延后到 Custom Function）

**关键决策**：表达式语法直接用 DuckDB SQL 表达式子集，不做自创 DSL。理由：
1. DuckDB 是 MVP 转换执行引擎，表达式可直接执行，无需翻译
2. SQL 表达式用户熟悉，学习成本低
3. 避免自创 DSL 的维护负担和翻译损耗
4. DuckDB SQL 与 Trino/PostgreSQL SQL 高度兼容，未来切换引擎表达式可复用

### 7.4 数据质量校验算子

数据质量是嵌入管道全链路的原生能力，不是独立后置工具：

**规则类型（MVP）**：
- 非空校验（`field IS NOT NULL`）
- 唯一主键（`COUNT(*) = COUNT(DISTINCT field)`）
- 取值范围（`field BETWEEN x AND y`）
- 正则格式（`regexp_like(field, pattern)`）

**违规处理策略**：
- **警告**：仅记录，不阻断，画布标黄
- **阻断**：校验不通过则构建失败（Kestra If Task + FAIL）
- **分流**：违规数据单独输出到异常 Dataset，正常数据继续（Kestra If + 分支）

**实现**：质量规则附属于 Transform 节点（也可独立 QualityCheck 节点），翻译为 Kestra 的 `io.kestra.plugin.core.flow.If` Task + 校验 SQL。

---

## 8. 执行层：Kestra 引擎适配

> 本节定义 Pipeline IR → Kestra Flow 的单向翻译规则、Kestra 的部署形态、与 SeaTunnel/Trino 的协作模式。核心原则：不修改 Kestra 源码，通过原生 HTTP API + 插件能力集成。

### 8.1 为什么选 Kestra（决策依据）

| 维度 | Kestra | 对 Gaia 的价值 |
|------|--------|---------------|
| 声明式 YAML | Flow = id + namespace + tasks + triggers | 天然适配管道 DAG |
| 编排能力 | Sequential/Parallel/ForEach/If/Switch + Subflow | 覆盖管道控制流需求 |
| 触发器 | Schedule/Webhook/Kafka/Flow completion | 覆盖调度场景 |
| 状态机 | Execution/TaskRun 完整状态机 + 重试/超时/熔断 | 生产级容错 |
| 数据传递 | 内部存储 + KV Store + outputs | Task 间数据传递 |
| 插件生态 | 1700+ 插件（JDBC/Trino/Python/Spark/dbt/HTTP） | 复用，不自研 |
| 部署 | Docker / K8s Helm / Standalone | JDBC 后端复用 PG |
| 开源协议 | Apache 2.0 | 可商用 |

**与 SeaTunnel 的职责分工（避免割裂）**：
- **SeaTunnel**：数据搬运引擎（source→sink 数据移动），保留现有 6 种 pipeline 模板，继续由 `DataSourceService` 编排
- **Kestra**：转换编排引擎（DAG + 转换算子 + 调度 + 生命周期），新增 `PipelineBuilderService` 编排
- **协作**：Kestra Flow 中可包含「SeaTunnel 数据搬运 Task」（通过 Kestra `io.kestra.plugin.core.http.Request` 调 SeaTunnel REST API），实现「先搬运、再转换」的组合管道。**用户视角是一条管道，不感知两个引擎**

### 8.2 Kestra 部署形态

**MVP（开发/测试）**：docker-compose 新增 Kestra 服务，JDBC 后端复用现有 PostgreSQL（独立 schema，不混用业务表）。

```
docker-compose.yml 新增：
  kestra:
    image: kestra/kestra:latest
    environment:
      KESTRA_CONFIGURATION: |
        kestra:
          server:
            basic-auth:
              enabled: true
              username: admin
              password: ${KESTRA_PASSWORD}
          repository:
            type: postgres
          storage:
            type: local
            local:
              base-path: /app/storage
          queue:
            type: postgres
          jdbc:
            url: jdbc:postgresql://postgres:5432/kestra
            username: ${POSTGRES_USER}
            password: ${POSTGRES_PASSWORD}
    ports: ["8080:8080"]
    depends_on: [postgres]
```

**生产**：Kubernetes Helm（Kestra 官方 Helm chart），独立 PG 或共享 PG 独立 schema。MVP 先 standalone，生产再切。

### 8.3 IR → Kestra Flow 翻译规则

**翻译原则**：
- 一个 Pipeline → 一个 Kestra Flow（MVP 单输出，Phase 2 多输出再考虑 Job Group）
- 一个 IR 节点 → 一个或多个 Kestra Task
- 节点连线 → Kestra Task 顺序 + outputs 传递
- 不反向生成 IR（避免代码反向解析难题）

**节点翻译映射**：

| IR 节点 | Kestra Task 类型 | 说明 |
|---------|-----------------|------|
| `Source`（读 Dataset） | `io.kestra.plugin.jdbc.duckdb.Query` | `SELECT * FROM iceberg.{dataset_api_name}`（DuckDB 读 Iceberg via iceberg extension），结果存 Kestra 内部存储 |
| `Transform`（Filter） | `io.kestra.plugin.jdbc.duckdb.Query` | `SELECT * FROM {{ upstream_output }} WHERE {expression}` |
| `Transform`（Join） | `io.kestra.plugin.jdbc.duckdb.Query` | `SELECT ... FROM {{ left }} JOIN {{ right }} ON ...` |
| `Transform`（Aggregate） | `io.kestra.plugin.jdbc.duckdb.Query` | `SELECT group_by, agg_func FROM {{ upstream }} GROUP BY ...` |
| `QualityCheck` | `io.kestra.plugin.core.flow.If` + DuckDB Query | 条件不满足则 FAIL（阻断）或分流 |
| `Sink`（写 Dataset） | `io.kestra.plugin.jdbc.duckdb.Query` | DuckDB `CREATE TABLE iceberg.{target} AS SELECT ...` 或 `INSERT INTO` + 原子提交 snapshot |
| `跨源联邦`（VIRTUAL 表 JOIN） | `io.kestra.plugin.jdbc.trino.Query` | **只读**，仅当涉及 VIRTUAL 表时路由，不做写入 |
| `GenericKestraTask`（透出算子） | 原样透传（task_type + task_config） | 封装任意 Kestra 插件，不做转换，Schema 用户声明（见 §7.0） |

**关键设计：中间数据传递**
- Trino 的中间结果用临时表或 CTE 承载，避免 Kestra 内部存储搬运大数据
- 方案 A（MVP）：每个 Transform 节点的 SQL 用 CTE 串联，最终合成一条大 SQL（DuckDB 优化器自动优化）
- 方案 B（备选）：每个节点写 Iceberg 临时表，下游读取（适合长链路，但有 IO 开销）
- MVP 选方案 A（CTE 串联），性能更好；Phase 2 长链路再考虑方案 B

### 8.4 Kestra 与 SeaTunnel 的协作（组合管道）

当管道需要「先搬运外部数据、再转换」时，Kestra Flow 内嵌 SeaTunnel 搬运 Task：

```yaml
# Kestra Flow 示例（IR 翻译产物）
id: pipeline_customer_etl
namespace: gaia.pipelines
tasks:
  - id: sync_source_data
    type: io.kestra.plugin.core.http.Request
    uri: http://seatunnel-master:5801/api/v1/submit-job
    method: POST
    body: |
      { "pipeline_name": "sync_mysql_customers", ... }
    # 调 SeaTunnel REST API 触发数据搬运
  
  - id: transform_filter
    type: io.kestra.plugin.jdbc.duckdb.Query
    sql: "SELECT * FROM iceberg.customer_raw WHERE status = 'active'"
    # DuckDB 通过 iceberg extension 读 Iceberg 表（REST Catalog via Gravitino 9001）
    
  - id: transform_join
    type: io.kestra.plugin.jdbc.duckdb.Query
    sql: "SELECT ... FROM {{ outputs.transform_filter.uri }} JOIN iceberg.orders ..."
    
  - id: sink_dataset
    type: io.kestra.plugin.jdbc.duckdb.Query
    sql: "CREATE OR REPLACE TABLE iceberg.customer_enriched AS SELECT * FROM {{ outputs.transform_join.uri }}"
    # DuckDB CTAS 写 Iceberg（或 INSERT INTO 追加模式）+ 原子提交 snapshot
```

**用户视角**：在画布上看到的是一个 Source 节点（标注「外部数据源，需先搬运」）+ 转换节点 + Sink 节点。不感知 SeaTunnel 和 Kestra 的协作。

### 8.5 执行状态监控

`PipelineBuilderService` 轮询 Kestra Execution 状态（通过 Kestra REST API），映射为 Gaia 的执行记录：

| Kestra Execution State | Gaia Execution State | 说明 |
|------------------------|---------------------|------|
| CREATED / RUNNING | RUNNING | 执行中 |
| SUCCESS | SUCCESS | 成功 |
| FAILED / KILLED | FAILED | 失败 |
| WARNING | SUCCESS_WITH_WARNING | 部分警告 |
| PAUSED | PAUSED | 暂停（HITL，未来） |

执行记录存 PG（`pipeline_executions` 表），包含：开始/结束时间、状态、节点级 TaskRun 状态、数据行数、错误日志。

### 8.6 不修改 Kestra 的边界

- ❌ 不修改 Kestra 源码
- ❌ 不自研 Kestra 插件（MVP 用原生插件足够）
- ❌ 不绕过 Kestra 直接调 DuckDB/Trino（所有执行经 Kestra 编排，保证状态机/重试/血缘统一）
- ❌ 不扩展 Trino 写入能力（保持其只读联邦查询定位，转换写入走 DuckDB）
- ✅ 通过 Kestra REST API + Flow YAML 提交/查询/控制
- ✅ Phase 2 若需自定义算子，再考虑自研 Kestra 插件（io.kestra.plugin.gaia.*）

---

## 9. 输出层：Dataset 与 Ontology 绑定

> 本节定义管道输出到 Dataset 的写入模式、版本管理、Ontology 绑定（阶段 2）。核心约束：只写 Iceberg，不写 Doris；Dataset 只关联 Iceberg snapshot。

### 9.1 输出目标类型（MVP）

| 输出类型 | MVP | 说明 |
|---------|-----|------|
| **Dataset（Iceberg 新 snapshot）** | ✅ | 核心输出，原子提交 |
| **映射到已有 ObjectType** | ✅ 阶段1 | 写入 ObjectType 绑定的 Iceberg 表 |
| **新建 ObjectType** | ❌ 阶段2 | AI 辅助，延后 |
| **Time Series** | ❌ | 延后 |
| **外部导出（JDBC/Kafka/文件）** | ❌ | 延后 |

### 9.2 写入模式（MVP 两种）

| 模式 | 说明 | 实现 | 适用场景 |
|------|------|------|---------|
| **FULL_REFRESH** | 全量重建，覆盖输出表 | DuckDB `CREATE OR REPLACE TABLE`（CTAS） | 小表/维度表/逻辑频繁迭代 |
| **APPEND** | 增量追加，不修改历史 | DuckDB `INSERT INTO` | 日志/流水/仅追加数据 |

**MVP 不做**：MERGE 合并模式（管道级 CDC，需主键匹配 + 变更识别，调度链路复杂，延后；注意 IcebergStore.merge 已有能力，是 Action 写回用的，不是管道级）。

### 9.3 版本化 Dataset（复用 + 激活 snapshot）

**不新建 Dataset 抽象层**，复用现有 `DatasetGovernanceModel` + 激活 Iceberg snapshot：

**DatasetGovernanceModel 新增字段**：
- `current_snapshot_id`：当前对外可见的 Iceberg snapshot ID（原子切换实现秒级回滚）
- `snapshot_retention`：版本保留策略（保留最近 N 个 snapshot，超期清理）

**写入与版本提交流程**：
1. 管道执行时，DuckDB `CREATE TABLE AS` / `INSERT INTO` 写入 Iceberg 表（产生新 snapshot）
2. 写入完成后，`PipelineBuilderService` 查询 Iceberg 最新 snapshot ID
3. 原子更新 `DatasetGovernanceModel.current_snapshot_id`（事务）
4. 下游读取走 TimeTravelService，按 `current_snapshot_id` 读取（默认读最新可见版本）

**版本回滚**：
- 切换 `current_snapshot_id` 到历史 snapshot，秒级完成（元数据操作，不重跑数据）
- 这是 Palantir 式「数据回滚秒级完成」的 Gaia 实现

**边界（与用户确认）**：
- Dataset 只关联 Iceberg snapshot，**不管 Doris**（Doris 是在线读主源，由 IndexSyncService 独立同步）
- Dataset **不管外邦表**（VIRTUAL 是 Trino 联邦代理，无版本概念，管道不可写入）

### 9.4 Ontology 绑定（阶段 1：映射到已有 ObjectType）

**流程**：
1. 用户在 Sink 节点配置「映射到 ObjectType」：选择已存在的 ObjectType + 字段映射（property → 输出字段）
2. 管道执行时，通过 `OntologyService` 查询 ObjectType 绑定的 Dataset（`link_dataset` 已记录）
3. 写入该 Dataset 的 Iceberg 表（即 ObjectType 的物理数据源）
4. 更新 `current_snapshot_id`，ObjectType 查询自动看到新数据

**关键约束**：
- 管道**不创建 ObjectType schema**（ObjectType 是用户定义的）
- 管道**不写 Doris idx 表**（Doris 同步是 IndexSyncService 独立链路）
- 管道可配置「触发索引同步」选项，调用 `IndexSyncService.sync_now`（容灾兜底，非主链路；主链路是 IndexSyncService 的 stream pipeline 自动同步）

**字段映射校验**：
- 输出 Schema 的字段必须覆盖 ObjectType 的所有 property（参考 `link_dataset` 的「EVERY property must be mapped」约束）
- 类型兼容性校验（输出字段类型 vs property 类型）

### 9.5 Ontology 绑定（阶段 2：新建 ObjectType，AI 辅助）

**流程**：
1. 用户在 Sink 节点选择「新建 ObjectType」
2. 系统根据输出 Schema + AI 建议（走 `/ai/*` 路由，符合红线 11）：
   - 建议 property 划分（哪些字段是 property）
   - 建议 api_name / display_name
   - 建议主键
3. 用户确认后，调用 `OntologyService.create_object_type` 创建 ObjectType
4. 自动创建目标 Dataset（Iceberg 表）+ `link_dataset` 绑定
5. 管道写入该 Dataset

**实现 Palantir 式「管道生成对象」体验，但保留 Gaia「用户确认 + 元数据驱动」底线**。

### 9.6 输出事务与一致性

- **原子性**：单次构建的所有输出要么全部成功（新 snapshot + current_snapshot_id 更新），要么全部回滚（不更新 current_snapshot_id，下游看不到新数据）
- **一致性**：多输出基于同一份上游 snapshot 计算（MVP 单输出，Phase 2 多输出再考虑一致性）
- **可见性**：构建成功前下游读旧 snapshot，成功后原子切换到新 snapshot


---

## 10. 调度与触发

> 参考 Palantir 四大触发模式，结合 Kestra 原生触发器能力，定义 Gaia MVP 的调度策略。

### 10.1 触发模式（MVP 两种）

| 触发模式 | Palantir | Gaia MVP | 实现（Kestra） |
|---------|----------|---------|---------------|
| **定时调度** | Cron | ✅ | `io.kestra.plugin.core.trigger.Schedule` |
| **手动触发** | 页面/API | ✅ | Kestra REST API 触发 Execution |
| **上游依赖触发** | Dataset 版本事件 | ❌ Phase 2 | Kestra Flow trigger（监听上游 Flow 完成） |
| **API/Webhook 触发** | 外部系统 | ❌ Phase 2 | `io.kestra.plugin.core.trigger.Webhook` |

**MVP 选型理由**：定时 + 手动覆盖核心场景（离线数仓周期刷新、临时重跑）。上游依赖触发（数据落地后自动级联）是 Phase 2 重点，依赖 Dataset 版本事件机制完善。

### 10.2 调度配置

每个管道可配置：
- **触发方式**：手动 / 定时（Cron 表达式）
- **并发控制**：禁止并发（上一次没跑完则跳过）/ 排队（Phase 2）
- **重试策略**：最大重试次数 / 重试间隔 / 指数退避（Kestra 原生支持）
- **超时时间**：构建级超时
- **时区**：Cron 时区配置（避免跨时区偏移）

### 10.3 失败重试与容错

- **全量重试**：Kestra 原生支持（`retry` 配置）
- **分区级重试**：❌ MVP 不做（依赖 Iceberg 分区 + Trino 分区级执行，复杂）
- **熔断机制**：连续失败达阈值暂停调度（Phase 2，依赖告警体系）
- **幂等性**：FULL_REFRESH 天然幂等（覆盖）；APPEND 需用户保证（同一批数据重复触发会重复追加，Phase 2 考虑去重）

### 10.4 调度与执行的解耦

- **调度**：Kestra Scheduler 负责（Gaia 不自研调度器）
- **执行**：Kestra Worker 执行 Task（Trino Query / HTTP Request）
- **监控**：`PipelineBuilderService` 轮询 Kestra Execution 状态，映射到 Gaia 执行记录
- **触发入口**：Gaia REST API → Kestra REST API（用户在 Gaia UI 触发，不直接操作 Kestra UI）

**用户视角**：在 Gaia 的管道管理页面触发/查看执行，不感知 Kestra。Kestra UI 仅作开发调试辅助（管理员可见）。

---

## 11. 版本化与生命周期

> 参考 Palantir 的类 Git 分支、变更评审、原子发布、秒级回滚，定义 Gaia 的管道生命周期管理。MVP 做轻量版，分支评审延后。

### 11.1 版本模型（逻辑版本 + 数据版本）

完全对齐 Palantir 的双重版本管控：

- **逻辑版本**：Pipeline IR 的版本（PipelineVersionModel），每次保存生成新版本，支持历史对比
- **数据版本**：输出 Dataset 的 Iceberg snapshot，每次构建生成新 snapshot

两者关系：逻辑版本决定计算规则，构建执行后生成对应的数据版本。

### 11.2 生命周期阶段（MVP 轻量版）

| 阶段 | Palantir | Gaia MVP | Gaia Phase 2 |
|------|----------|---------|--------------|
| **开发** | 特性分支 | 主干直接编辑（单分支） | 特性分支 + 差异对比 |
| **评审** | Propose & Review + 影响分析 | ❌ 不做 | 变更评审 + 影响分析 |
| **部署** | 灰度/直接发布/回滚 | 直接部署（保存即部署） | 灰度发布 + 审批 |
| **运行** | 四层可观测 | 执行状态监控（成功率/耗时/日志） | 数据质量 + 资源成本 + SLA |
| **故障修复** | 分区级重试 + 数据回滚 | 全量重试 + 逻辑回滚 | 分区级重试 + 数据回滚 |
| **下线** | 依赖检查 + 归档 | 软删除（标记 deleted） | 依赖检查 + 数据归档 |

### 11.3 回滚能力（MVP 核心价值）

**逻辑回滚**：切换到历史逻辑版本，重新部署执行（Kestra Flow 切换）
**数据回滚**：切换 Dataset 的 `current_snapshot_id` 到历史 snapshot，秒级完成（元数据操作，不重跑数据）

数据回滚是 Palantir 式「秒级恢复」的 Gaia 实现，是版本化架构的核心价值。传统 ETL 数据已覆盖，要恢复必须全量重跑（小时~天）；Gaia 切 snapshot 秒级完成。

### 11.4 血缘自动生成（Phase 1 预留，Phase 2 实现）

Pipeline IR 的每个节点都有明确的输入输出字段映射，系统可在执行前生成完整字段级血缘：

- **管道内血缘**：Source → Transform → Sink 的字段映射链
- **跨管道血缘**：Dataset A（管道1输出）→ Dataset B（管道2输入）
- **本体血缘**：Dataset → ObjectType（通过 link_dataset）

**MVP**：预留血缘元数据位（IR 的 lineage 字段），记录节点级映射。
**Phase 2**：对接 Gaia 全局血缘体系（implementation-status §14.1 待实现），生成字段级血缘图谱，支持影响分析（上游 Schema 变更，下游影响范围自动计算）。

### 11.5 治理元数据（预留位）

Pipeline IR 预留治理元数据位，对接 ADR-016/017 权限体系：
- `owner`：负责人
- `tags`：标签列表
- `business_domain`：业务域
- `data_classification`：数据等级
- `project_id`：项目归属（复用 ADR-016 Project 模型）

**MVP**：记录但不强制（可选字段）。
**Phase 2**：对接权限体系，管道按 Project/业务域隔离，部署需审批。

---


## 12. 数据模型（ORM）

> 本节定义 Pipeline Builder 的物理表结构。设计参考 Airflow 3（DagVersion 独立版本表）、Prefect（状态冗余 + 独立历史表）、DolphinScheduler（定义/实例分离）四家开源编排器的成熟模式，规避其踩过的坑（FK 无索引致级联删除慢、状态无历史、JSONB 频繁更新）。

### 12.1 设计决策（DFX 属性）

基于开源调研，确认以下设计决策（详见 ADR-018 D8）：

| 决策 | 选择 | 理由 | 对标开源 |
|------|------|------|---------|
| D-1 版本化模式 | **Airflow 3 独立版本表** | `pipelines` 存元信息 + `pipeline_versions` 存每版本完整 IR；回滚=切换 current_version_id；历史查询直接查版本表 | Airflow 3 `dag`+`dag_version`（AIP-65） |
| D-2 节点存储 | **JSONB 整存**（`pipeline_versions.graph`） | IR 是整体逻辑资产，节点间有强契约；拆表破坏整体性且 MVP 无跨版本节点 diff 需求 | Airflow `serialized_dag`、Kestra Flow YAML |
| D-3 状态历史 | **冗余当前 + 独立历史表** | `executions.current_state` 冗余加速列表查询；`state_history` 独立表记录每次变更（审计+诊断） | Prefect `flow_run`+`flow_run_state`（PR #7138 教训） |
| D-4 Kestra 映射 | **executions 表加字段**（非独立映射表） | 1:1 关系，独立表过度设计 | — |
| D-5 节点观测字段 | **MVP 建字段，采集延后** | rows_in/out、bytes_processed 建字段先 NULL，Phase 2 补 DuckDB/SeaTunnel 采集 | Prefect `task_run` |
| D-6 执行记录 TTL | **MVP 建索引，清理延后** | `created_at` 加 index，清理逻辑（保留 N 天）Phase 2 | Prefect/Airflow vacuum 服务 |
| D-7 节点血缘表 | **Phase 2 再建** | MVP 用 JSONB graph 承载节点关系；字段级血缘 Phase 2 加 `pipeline_lineage` 表 | DolphinScheduler 边表（反例：过早关系化） |

### 12.2 表结构总览

```
pipelines                 管道定义（当前生效版本的元信息）
pipeline_versions         管道版本（完整历史，每次编辑一行，graph JSONB 整存 IR）
pipeline_schedules        调度（独立资源，对标 Palantir Schedule；一个 pipeline 可多个 schedule）
pipeline_executions       构建执行记录（一次 build=一行，冗余当前状态 + Kestra 映射）
pipeline_node_runs        节点执行记录（构建内节点级状态/耗时/观测）
pipeline_state_history    执行状态变更历史（审计 + 失败诊断）
datasets 表扩展           current_snapshot_id / snapshot_retention / write_lock（D5）
```

**关系图**：

```
pipelines (1) ──< pipeline_versions (N)    每次编辑一行版本
    │                    │
    │                    └──< pipeline_executions (N)   用某版本执行（build）
    │                              │
    │                              ├──< pipeline_node_runs (N)      节点级执行
    │                              └──< pipeline_state_history (N)  状态变更历史
    │
    ├── current_version_id → pipeline_versions.id   当前生效版本（RESTRICT 删除）
    │
    └──< pipeline_schedules (N)    调度（独立资源，触发 build）
```

### 12.3 表定义

#### 12.3.1 `pipelines`（管道定义，当前生效元信息）

只存管道的元信息和"当前生效版本"指针，不存 IR 内容（IR 在版本表）。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | String(32) | PK | UUID v4 hex |
| `api_name` | String(255) | UNIQUE, INDEX | 业务标识（`_to_snake` 保词界，见 naming.py） |
| `display_name` | String(255) | NOT NULL | 展示名 |
| `description` | Text | default "" | 描述 |
| `status` | String(20) | default "DRAFT" | DRAFT / PUBLISHED / DEPRECATED / ARCHIVED |
| `current_version_id` | String(32) | FK→pipeline_versions.id, ON DELETE RESTRICT | 当前生效版本（RESTRICT：有执行引用时防误删） |
| `write_mode` | String(20) | default "FULL_REFRESH" | FULL_REFRESH / APPEND（构建写入模式） |
| `sink_dataset_api_name` | String(255) | NOT NULL | 输出 Dataset（VIRTUAL 禁止，红线 9） |
| `ontology_mapping_config` | JSONB | nullable | 阶段2 Ontology 绑定（MVP NULL） |
| `owner_id` | String(32) | nullable | 创建者 |
| `project_id` | String(32) | FK→projects.id, ON DELETE SET NULL, INDEX | 项目归属（ADR-016） |
| `deleted_at` | DateTime | nullable | 软删除（保留执行审计） |
| `created_at` | DateTime | default utcnow | — |
| `updated_at` | DateTime | default utcnow, onupdate utcnow | — |

**避坑**：`current_version_id` 用 RESTRICT 而非 CASCADE——参照 Airflow 3.1.0 把 `task_instance.dag_version_id` 从 CASCADE 改 RESTRICT 的教训（防止误删还有执行引用的版本）。

#### 12.3.2 `pipeline_versions`（版本历史，每次编辑一行）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | String(32) | PK | UUID v4 hex |
| `pipeline_id` | String(32) | FK→pipelines.id, ON DELETE CASCADE, INDEX | 父管道（CASCADE：删管道清版本） |
| `version_number` | Integer | NOT NULL | 同 pipeline 内自增（1, 2, 3...） |
| `graph` | JSONB | NOT NULL | 完整 IR：nodes + edges + 节点配置（见 §5） |
| `inferred_schema` | JSONB | nullable | 推演出的输出 Schema 快照（见 §6，版本固化） |
| `change_summary` | Text | default "" | 本次变更说明（用户填写或自动生成） |
| `created_by` | String(32) | nullable | 编辑者 |
| `created_at` | DateTime | default utcnow | — |
| | | UNIQUE(pipeline_id, version_number) | 版本号同管道内唯一 |

**设计要点**：
- `graph` JSONB 整存 IR（决策 D-2），包含 nodes 列表 + edges 拓扑 + 每个节点的 config
- `inferred_schema` 固化该版本的推演结果——执行时用此快照，不受后续编辑影响（保证可复现）
- OCC 乐观锁：编辑时 `version_number` 由 DB 层 `MAX(version_number)+1` 保证，应用层校验并发

#### 12.3.3 `pipeline_executions`（执行记录）

一次触发（手动/cron/事件）= 一行。冗余当前状态加速列表查询，Kestra 映射字段解耦编排引擎。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | String(32) | PK | UUID v4 hex |
| `pipeline_id` | String(32) | FK→pipelines.id, ON DELETE RESTRICT, INDEX | 父管道（RESTRICT：保留执行审计，不随管道删除） |
| `version_id` | String(32) | FK→pipeline_versions.id, ON DELETE RESTRICT, INDEX | 用哪个版本执行（RESTRICT：版本有执行引用时防删） |
| `trigger_type` | String(20) | NOT NULL | MANUAL / SCHEDULE / UPSTREAM_EVENT |
| `triggered_by` | String(32) | nullable | 用户ID 或 "scheduler" |
| `current_state` | String(20) | INDEX | PENDING / RUNNING / SUCCESS / FAILED / CANCELLED（冗余，加速列表） |
| `state_started_at` | DateTime | nullable | 当前状态进入时间（冗余，算停留时长） |
| `started_at` | DateTime | nullable | 执行开始 |
| `finished_at` | DateTime | nullable | 执行结束 |
| `duration_ms` | BigInteger | nullable | 总耗时（冗余，排序用） |
| `kestra_execution_id` | String(255) | nullable, INDEX | Kestra 侧执行ID（解耦映射，决策 D-4） |
| `kestra_flow_id` | String(255) | nullable | Kestra Flow ID |
| `kestra_namespace` | String(255) | nullable | Kestra Namespace |
| `error_message` | Text | nullable | 失败原因（冗余，列表展示不 JOIN state_history） |
| `output_snapshot_id` | String(64) | nullable | 输出 Iceberg snapshot ID（数据回滚锚点） |
| `execution_meta` | JSONB | default {} | 扩展元数据（rows_total / bytes_total / cost_estimate） |
| `created_at` | DateTime | default utcnow | — |

**避坑**（Prefect PR #7138 教训）：`current_state`/`state_started_at`/`duration_ms`/`error_message` 全部冗余在 executions 表——列表查询"最近 100 次执行及状态"不需要 JOIN state_history，历史查询性能 10x 提升。

**可靠性**：Gaia 先 INSERT execution 行（state=PENDING）→ 调 Kestra 触发 → Kestra 返回 executionId 后 UPDATE 回填。Kestra 调用失败时 execution 行保留（state=FAILED, error_message 记录），可补偿重试。

#### 12.3.4 `pipeline_node_runs`（节点级执行状态）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | String(32) | PK | UUID v4 hex |
| `execution_id` | String(32) | FK→pipeline_executions.id, ON DELETE CASCADE, INDEX | 父执行（CASCADE：删执行清节点） |
| `node_id` | String(255) | NOT NULL | IR 内节点 ID（对应 graph.nodes[].id） |
| `node_type` | String(50) | NOT NULL | Source / Transform / GenericKestraTask / QualityCheck / Sink |
| `engine` | String(20) | nullable | duckdb / seatunnel / trino / kestra_plugin（执行引擎，可扩展） |
| `current_state` | String(20) | INDEX | PENDING / RUNNING / SUCCESS / FAILED / SKIPPED |
| `started_at` | DateTime | nullable | — |
| `finished_at` | DateTime | nullable | — |
| `duration_ms` | BigInteger | nullable | 节点耗时 |
| `error_message` | Text | nullable | 节点级错误 |
| `attempt` | Integer | default 1 | 重试次数 |
| `kestra_taskrun_id` | String(255) | nullable, INDEX | Kestra 侧 TaskRun ID |
| `rows_in` | BigInteger | nullable | 输入行数（决策 D-5：MVP 建字段，采集延后） |
| `rows_out` | BigInteger | nullable | 输出行数 |
| `bytes_processed` | BigInteger | nullable | 处理字节数 |
| `node_run_meta` | JSONB | default {} | 扩展元数据 |

**可观测性**：`rows_in`/`rows_out`/`bytes_processed`/`duration_ms` 支持"哪个节点慢/数据量异常"诊断。MVP 字段先 NULL（DuckDB/SeaTunnel 采集逻辑 Phase 2 补），建表时就预留避免后续 ALTER。

#### 12.3.5 `pipeline_state_history`（执行状态变更历史）

审计 + 失败诊断。每次 execution 状态变更 INSERT 一行。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | String(32) | PK | UUID v4 hex |
| `execution_id` | String(32) | FK→pipeline_executions.id, ON DELETE CASCADE, INDEX | 父执行 |
| `from_state` | String(20) | nullable | 前状态（NULL=初始） |
| `to_state` | String(20) | NOT NULL | PENDING / RUNNING / SUCCESS / FAILED / CANCELLED |
| `reason` | String(255) | nullable | 变更原因（kestra_task_completed / user_cancelled / timeout / ...） |
| `changed_by` | String(32) | nullable | 触发者（user_id / scheduler / kestra_callback） |
| `changed_at` | DateTime | default utcnow, INDEX | 变更时间 |

**设计要点**（Prefect 模式）：状态历史独立表，不污染 executions 表。executions 表只存当前状态（冗余），完整变迁链在此表。诊断"何时失败、失败前停了多久、谁取消的"靠此表。

#### 12.3.6 `pipeline_schedules`（调度，独立资源）

对标 Palantir `/v2/orchestration/schedules` 独立资源设计。一个 pipeline 可有多个 schedule（如工作日增量 + 月末全量），可独立启用/禁用。

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `id` | String(32) | PK | UUID v4 hex |
| `pipeline_id` | String(32) | FK→pipelines.id, ON DELETE CASCADE, INDEX | 父管道 |
| `api_name` | String(255) | NOT NULL, INDEX | 调度业务标识（同 pipeline 内唯一） |
| `display_name` | String(255) | default "" | 展示名 |
| `trigger` | JSONB | NOT NULL | 触发配置（MVP 单 trigger；JSONB 预留 AND/OR 嵌套，见 §13.4） |
| `action_config` | JSONB | default {} | 构建动作配置（force_build / retry_count / timeout_minutes / abort_on_failure） |
| `enabled` | Boolean | default True | 启用/禁用（独立于 pipeline 状态） |
| `kestra_trigger_id` | String(255) | nullable | Kestra 侧 trigger 标识（部署时回填） |
| `created_by` | String(32) | nullable | 创建者 |
| `project_id` | String(32) | FK→projects.id, ON DELETE SET NULL, INDEX | 项目归属 |
| `created_at` | DateTime | default utcnow | — |
| `updated_at` | DateTime | default utcnow, onupdate utcnow | — |
| | | UNIQUE(pipeline_id, api_name) | 调度名同管道内唯一 |

**trigger JSONB 结构**（对标 Palantir Trigger 嵌套）：

```json
// MVP：单触发器
{"type": "time", "cron": "0 9 * * 1-5", "tz": "Asia/Shanghai"}
{"type": "webhook", "key": "orders-arrived"}
// Phase 2：AND/OR 嵌套（预留，MVP 不实现）
{"type": "OR", "triggers": [
  {"type": "time", "cron": "0 9 * * 1-5"},
  {"type": "dataset_event", "dataset": "orders_raw", "condition": "new_snapshot"}
]}}
```

**设计要点**：
- 调度独立于 pipeline（Palantir 模式）：可独立增删启停，不影响 pipeline 定义
- `trigger` JSONB 预留嵌套结构（MVP 只实现 time/webhook 单触发，Phase 2 加 AND/OR + dataset_event）
- `action_config` 存构建参数（对标 Palantir CreateBuildRequest 的 force_build/retry_count/abort_on_failure）
- 部署时翻译为 Kestra Flow 的 trigger 段，`kestra_trigger_id` 回填解耦

#### 12.3.7 `datasets` 表扩展（D5，加 3 字段）

复用现有 `DatasetGovernanceModel`（见 `src/ontology/core/models/datasource.py`），新增 3 字段：

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `current_snapshot_id` | String(64) | nullable | 当前生效 Iceberg snapshot ID（数据回滚=切换此值） |
| `snapshot_retention` | Integer | nullable | 保留多少个历史 snapshot（回滚窗口，默认 10） |
| `write_lock` | String(32) | nullable | 持锁 pipeline_id（并发写保护，NULL=可写） |

**并发控制**：管道执行前对 sink_dataset 加锁（`write_lock = pipeline_id`），完成后释放。同一 Dataset 同时只允许一个管道写入（决策 §16.4）。实现用 PG advisory lock + `write_lock` 字段双重保护（advisory lock 防并发，字段防崩溃残留锁）。

### 12.4 索引设计（避坑：FK 必须加 index）

> Airflow PR #39638 教训：FK 约束不自动建索引，大表级联删除慢。所有 FK 列显式加 INDEX。

| 表 | 索引列 | 用途 |
|----|--------|------|
| pipelines | api_name (UNIQUE) | 业务查询 |
| pipelines | project_id | 按项目筛选 |
| pipeline_versions | pipeline_id | 查管道的版本列表 |
| pipeline_versions | (pipeline_id, version_number) UNIQUE | 版本号唯一 |
| pipeline_executions | pipeline_id | 查管道的执行历史 |
| pipeline_executions | version_id | 查版本的执行记录 |
| pipeline_executions | current_state | 按状态筛选（RUNNING/FAILED） |
| pipeline_executions | kestra_execution_id | Kestra 回调映射 |
| pipeline_executions | created_at | TTL 清理排序（决策 D-6） |
| pipeline_node_runs | execution_id | 查执行的节点详情 |
| pipeline_node_runs | current_state | 找失败节点 |
| pipeline_node_runs | kestra_taskrun_id | Kestra 回调映射 |
| pipeline_state_history | execution_id | 查执行的状态变迁 |
| pipeline_state_history | changed_at | 时间范围审计 |
| datasets | current_snapshot_id | snapshot 查询 |
| pipeline_schedules | pipeline_id | 查管道的调度列表 |
| pipeline_schedules | (pipeline_id, api_name) UNIQUE | 调度名唯一 |
| pipeline_schedules | project_id | 按项目筛选 |

### 12.5 级联删除策略

| 关系 | 策略 | 理由 |
|------|------|------|
| pipelines → pipeline_versions | CASCADE | 删管道清所有版本 |
| pipelines.current_version_id → pipeline_versions | RESTRICT | 有生效版本引用时防删版本 |
| pipeline_versions → pipeline_executions | RESTRICT | 版本有执行引用时防删（保留审计） |
| pipelines → pipeline_schedules | CASCADE | 删管道清调度 |
| pipelines → pipeline_executions | RESTRICT | 管道有执行记录时保留（审计不丢） |
| pipeline_executions → pipeline_node_runs | CASCADE | 删执行清节点 |
| pipeline_executions → pipeline_state_history | CASCADE | 删执行清状态历史 |
| pipelines → datasets（sink） | 无 FK（弱关联） | Dataset 独立生命周期，管道删后 Dataset 保留 |

**软删除**：`pipelines.deleted_at` 标记软删除，不物理删（保留执行审计）。物理删除仅管理员操作，且受 RESTRICT 保护（有执行引用时拒绝）。

### 12.6 状态机定义

#### 12.6.1 管道生命周期状态（pipelines.status）

```
DRAFT ──publish──> PUBLISHED ──deprecate──> DEPRECATED ──archive──> ARCHIVED
                       │                        │
                       └──<──edit──<────────────┘  （DEPRECATED 可重新发布）
```

#### 12.6.2 执行状态（pipeline_executions.current_state）

```
PENDING ──start──> RUNNING ──success──> SUCCESS
                      │
                      ├──failed──> FAILED
                      ├──timeout──> FAILED
                      └──cancel──> CANCELLED
```

每次状态变更同步写 `pipeline_state_history` 一行 + 更新 `pipeline_executions.current_state`（事务内）。

### 12.7 Alembic Migration 要求

- 新增 5 张表（pipelines / pipeline_versions / pipeline_executions / pipeline_node_runs / pipeline_state_history）必须走 Alembic（CLAUDE.md 红线：禁止手写 SQL / 禁止手改 init-pg-schema.sql）
- `datasets` 表加 3 字段（current_snapshot_id / snapshot_retention / write_lock）单独一个 migration revision
- migration 必须包含所有 INDEX 定义（FK 不自动建索引）
- ORM 模型放 `src/ontology/core/models/pipeline.py`（新文件），与现有 `ontology.py`/`datasource.py` 同级
- `pipeline.py` 导入 `Base` from `ontology.py`，遵循现有 ORM 规范（String(32) UUID 主键 / JSONB 灵活字段 / created_at+updated_at / 软删除 deleted_at）

### 12.8 开源对标速查

| 设计点 | Gaia 方案 | 对标开源 | 避开的坑 |
|--------|----------|---------|---------|
| 定义/实例分离 | pipelines + pipeline_executions | Airflow dag+dag_run / Prefect flow+flow_run / DolphinScheduler definition+instance | — |
| 版本化 | 独立 pipeline_versions 表 | Airflow 3 dag_version（AIP-65） | DolphinScheduler 双表 schema 漂移 |
| 节点存储 | JSONB 整存 graph | Airflow serialized_dag / Kestra Flow YAML | DolphinScheduler 边表过早关系化 |
| 状态历史 | 冗余当前 + 独立 state_history | Prefect flow_run+flow_run_state | Airflow 2.x 无历史靠日志 / Prefect PR #7138 无冗余致查询慢 |
| Kestra 映射 | executions 表加字段 | — | 独立映射表过度设计 |
| FK 索引 | 所有 FK 加 INDEX | Airflow PR #39638 | 级联删除慢 |
| 级联策略 | 定义→版本 CASCADE，版本→执行 RESTRICT | Airflow 3.1.0 RESTRICT | 误删有引用的版本 |
| 软删除 | pipelines.deleted_at | DolphinScheduler | 物理删丢审计 |



## 13. API 设计

> 本节定义 Pipeline Builder 的 REST API 契约。设计参考 Palantir Foundry API（Deploy/Build 分离、Schedule 独立资源、Release Stage）+ Kestra API（执行控制、SSE、插件发现）+ Prefect（idempotency）+ Airflow（批量查询）四家开源模式，规避其踩过的坑（DolphinScheduler version 不回填、Airflow offset 深翻页慢）。

### 13.1 设计决策（DFX 属性）

基于 Palantir + Kestra + 开源调研，确认以下决策（详见 ADR-018 D9）：

| 决策 | 选择 | 对标 |
|------|------|------|
| A-1 版本前缀 | `/api/v1/pipelines` 显式 v1 | Kestra `/api/v1/`、Airflow `/api/v2/` |
| A-2 资源标识 | URL 用 api_name，build 用 id | Gaia 现有规范（不用 Palantir RID） |
| A-3 Deploy/Build 分离 | deploy（逻辑生效）+ build（数据物化）独立端点 | Palantir Deploy/Build 核心概念 |
| A-4 Schedule 独立资源 | `/pipelines/{api_name}/schedules` 独立 CRUD | Palantir `/v2/orchestration/schedules` |
| A-5 触发嵌套 | trigger JSONB 预留 AND/OR 嵌套，MVP 单 trigger | Palantir Trigger 嵌套 |
| A-6 Build 参数 | force_build/retry_count/timeout/abort_on_failure | Palantir CreateBuildRequest |
| A-7 异步执行 | 202 + build_id + SSE，预留 `?wait=true` | Kestra `?wait=true`、AIP-151 |
| A-8 幂等触发 | `Idempotency-Key` header | Prefect idempotency_key、Stripe |
| A-9 Kestra 透传 | `?fetch_from_kestra=true` 透传原始详情 | 路线 C |
| A-10 算子目录 | `/pipeline-operators` + `/kestra-plugins` | 路线 C 支撑 |
| A-11 错误响应 | 沿用 Gaia 现有 `{detail, error_type, code}` | 一致性优先（非 RFC 9457） |
| A-12 Release Stage | OpenAPI `x-release-stage` 标注 stable/beta/experimental | Palantir Release Stage |
| A-13 术语 | 用 `builds` 替代 `executions`（"构建"表达"物化数据"） | Palantir 术语 |
| A-14 批量 | MVP 不做批量触发，列表支持多管道筛选 | Airflow batch list（避 GET URL 超长） |
| A-15 分页 | MVP offset，预留 cursor | 2026 业界共识 cursor |

### 13.2 API 总览

```
/api/v1/pipelines                              管道定义 CRUD
/api/v1/pipelines/{api_name}/versions          版本管理（历史/对比/回滚）
/api/v1/pipelines/{api_name}/deploy            部署逻辑（Deploy）
/api/v1/pipelines/{api_name}/builds            构建执行（Build）
/api/v1/pipelines/{api_name}/schedules         调度管理（独立资源）
/api/v1/pipelines/{api_name}/validate          校验 + Schema 推演（不保存）
/api/v1/pipelines/{api_name}/deprecate         废弃
/api/v1/pipelines/{api_name}/builds/{id}/...   构建监控/控制/节点/状态历史
/api/v1/pipeline-operators                     算子目录（核心 + Kestra 透出）
/api/v1/datasets/{api_name}/snapshots          数据版本（snapshot 管理）
```

### 13.3 端点清单

#### 13.3.1 管道定义 CRUD

| 方法 | 路径 | 说明 | 状态码 | Release |
|------|------|------|--------|---------|
| POST | `/api/v1/pipelines` | 创建（含初始版本，状态 DRAFT） | 201 | beta |
| GET | `/api/v1/pipelines` | 列表（分页+过滤：project/status） | 200 | beta |
| GET | `/api/v1/pipelines/{api_name}` | 详情（含当前版本 IR） | 200 | beta |
| PATCH | `/api/v1/pipelines/{api_name}` | 更新（生成新版本，**不部署**） | 200 | beta |
| DELETE | `/api/v1/pipelines/{api_name}` | 软删除（deleted_at） | 204 | beta |

**避坑**（DolphinScheduler Issue #18132）：POST/PATCH 响应必须含 `current_version_id` 和 `version_number`，service 层 commit 后重新查最新对象返回，不能返回 null。

**请求体**（POST/PATCH）：

```json
{
  "api_name": "customer_etl",
  "display_name": "客户 ETL 管道",
  "description": "清洗客户数据并关联订单",
  "write_mode": "FULL_REFRESH",
  "sink_dataset_api_name": "customer_enriched",
  "graph": {
    "nodes": [...],
    "edges": [...]
  },
  "change_summary": "新增订单关联节点"
}
```

**响应体**：

```json
{
  "api_name": "customer_etl",
  "display_name": "客户 ETL 管道",
  "status": "DRAFT",
  "current_version_id": "abc123...",
  "current_version_number": 3,
  "write_mode": "FULL_REFRESH",
  "sink_dataset_api_name": "customer_enriched",
  "project_id": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

#### 13.3.2 版本管理

| 方法 | 路径 | 说明 | Release |
|------|------|------|---------|
| GET | `/pipelines/{api_name}/versions` | 版本列表 | beta |
| GET | `/pipelines/{api_name}/versions/{version_number}` | 版本详情（含 graph IR + inferred_schema） | beta |
| POST | `/pipelines/{api_name}/versions/{version_number}/rollback` | 回滚（切换 current_version_id + 重新 deploy） | beta |

**对标**：Kestra `GET /flows/{ns}/{id}/revisions`、Palantir Branch 指向 transaction。

**rollback 语义**：切换 `current_version_id` 到指定版本 + 重新 deploy（翻译该版本 IR → Kestra Flow PUT）。下次 schedule 触发自动用回滚版本。**不自动 build**（用户决定是否立即数据回滚）。

#### 13.3.3 部署与构建（Deploy/Build 分离，对标 Palantir）

| 方法 | 路径 | 说明 | 状态码 | 对标 Palantir |
|------|------|------|--------|--------------|
| POST | `/pipelines/{api_name}/deploy` | 部署逻辑（DRAFT→PUBLISHED，翻译 IR→Kestra Flow，**不触发 build**） | 200 | Deploy |
| POST | `/pipelines/{api_name}/builds` | 触发构建（执行逻辑，物化数据，产生 snapshot） | 202 | Build |
| POST | `/pipelines/{api_name}/validate` | 校验 IR + Schema 推演（不保存，返回推演结果+错误） | 200 | Gaia 独有 |
| POST | `/pipelines/{api_name}/deprecate` | 废弃（PUBLISHED→DEPRECATED + Kestra disable） | 200 | — |

**deploy 请求体**（可选）：

```json
{
  "version_id": "abc123...",  // 可选，默认用 current_version_id
  "force": false  // 强制重新部署（即使逻辑未变）
}
```

**deploy 响应**：

```json
{
  "api_name": "customer_etl",
  "status": "PUBLISHED",
  "deployed_version_id": "abc123...",
  "deployed_version_number": 3,
  "kestra_flow_id": "pipeline_customer_etl",
  "kestra_namespace": "gaia.project_xxx",
  "deployed_at": "..."
}
```

**build 请求体**（对标 Palantir CreateBuildRequest）：

```json
{
  "version_id": "abc123...",  // 可选，默认用 current_version_id
  "force_build": false,       // 强制重建，忽略缓存
  "retry_count": 3,           // 失败重试次数
  "retry_backoff_seconds": 60,// 重试退避
  "timeout_minutes": 120,     // 超时
  "abort_on_failure": true,   // 多输出时一个失败终止其他（Phase 2 Job Group）
  "idempotency_key": "daily-run-20260715"  // 幂等键（也可用 header）
}
```

**build 响应**（202 Accepted）：

```json
{
  "build_id": "def456...",
  "pipeline_api_name": "customer_etl",
  "version_id": "abc123...",
  "version_number": 3,
  "status": "PENDING",
  "trigger_type": "MANUAL",
  "triggered_by": "user_xxx",
  "created_at": "...",
  "stream_url": "/api/v1/pipelines/customer_etl/builds/def456.../stream"
}
```

**validate 请求体**：

```json
{
  "graph": { "nodes": [...], "edges": [...] },
  "write_mode": "FULL_REFRESH",
  "sink_dataset_api_name": "customer_enriched"
}
```

**validate 响应**：

```json
{
  "valid": true,
  "inferred_schema": { "fields": [...] },
  "contracts": [
    {"node_id": "join_1", "valid": true, "message": ""},
    {"node_id": "filter_1", "valid": false, "message": "字段 'status' 不存在于上游 Schema"}
  ],
  "warnings": ["输出 Dataset 已有 5 个 snapshot，建议清理"]
}
```

#### 13.3.4 调度管理（独立资源，对标 Palantir Schedule）

| 方法 | 路径 | 说明 | Release |
|------|------|------|---------|
| POST | `/pipelines/{api_name}/schedules` | 创建调度 | beta |
| GET | `/pipelines/{api_name}/schedules` | 调度列表 | beta |
| GET | `/pipelines/{api_name}/schedules/{schedule_api_name}` | 调度详情 | beta |
| PATCH | `/pipelines/{api_name}/schedules/{schedule_api_name}` | 更新调度 | beta |
| DELETE | `/pipelines/{api_name}/schedules/{schedule_api_name}` | 删除调度 | beta |
| POST | `/pipelines/{api_name}/schedules/{schedule_api_name}/enable` | 启用 | beta |
| POST | `/pipelines/{api_name}/schedules/{schedule_api_name}/disable` | 禁用 | beta |

**创建调度请求体**：

```json
{
  "api_name": "weekday_daily",
  "display_name": "工作日每日构建",
  "trigger": {
    "type": "time",
    "cron": "0 9 * * 1-5",
    "tz": "Asia/Shanghai"
  },
  "action_config": {
    "force_build": false,
    "retry_count": 3,
    "timeout_minutes": 120,
    "abort_on_failure": true
  },
  "enabled": true
}
```

**Webhook 触发调度**：

```json
{
  "api_name": "orders_arrived_webhook",
  "trigger": {
    "type": "webhook",
    "key": "orders-arrived"
  },
  "action_config": {...},
  "enabled": true
}
```

Webhook 触发 URL：`POST /api/v1/pipelines/{api_name}/webhooks/{webhook_key}`（对标 Kestra `/executions/webhook/`）。

#### 13.3.5 构建监控与控制

| 方法 | 路径 | 说明 | 状态码 | 对标 Kestra |
|------|------|------|--------|------------|
| GET | `/pipelines/{api_name}/builds` | 构建列表（分页+状态过滤+多管道筛选） | 200 | `GET /executions` |
| GET | `/pipelines/{api_name}/builds/{build_id}` | 构建详情（含 node_runs + state_history） | 200 | `GET /executions/{id}` |
| GET | `/pipelines/{api_name}/builds/{build_id}?fetch_from_kestra=true` | 透传 Kestra 原始详情（含 taskRunList） | 200 | 路线 C 透出 |
| GET | `/pipelines/{api_name}/builds/{build_id}/stream` | SSE 实时状态推送 | 200 (SSE) | `GET /executions/{id}/follow` |
| POST | `/pipelines/{api_name}/builds/{build_id}/cancel` | 取消构建 | 200 | `DELETE /executions/{id}/kill` |
| POST | `/pipelines/{api_name}/builds/{build_id}/retry` | 重试（整个或指定节点） | 202 | `POST /executions/{id}/restart` |
| POST | `/pipelines/{api_name}/builds/{build_id}/rollback` | 数据回滚（切换 dataset snapshot） | 200 | Gaia 独有 |

**构建列表查询参数**：

```
GET /api/v1/pipelines/customer_etl/builds
  ?status=FAILED              # 状态过滤（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）
  &trigger_type=MANUAL        # 触发类型
  &start_time_gte=2026-07-01  # 时间范围
  &offset=0&limit=20          # 分页（MVP offset，预留 cursor）
  &order_by=-created_at       # 排序
```

**多管道筛选**（看板场景，对标 Airflow batch list）：

```
POST /api/v1/pipelines/builds/search
Content-Type: application/json

{
  "pipeline_api_names": ["customer_etl", "order_etl", "inventory_etl"],
  "status": ["FAILED", "RUNNING"],
  "start_time_gte": "2026-07-01"
}
```

#### 13.3.6 节点级构建详情

| 方法 | 路径 | 说明 | 对标 Kestra |
|------|------|------|------------|
| GET | `/pipelines/{api_name}/builds/{build_id}/nodes` | 节点执行列表 | taskRunList |
| GET | `/pipelines/{api_name}/builds/{build_id}/nodes/{node_id}` | 单节点详情（含 error/logs 指向） | TaskRun |
| GET | `/pipelines/{api_name}/builds/{build_id}/state-history` | 状态变更历史 | state.histories |

**节点详情响应**：

```json
{
  "node_id": "join_1",
  "node_type": "Transform",
  "engine": "duckdb",
  "status": "SUCCESS",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 12345,
  "rows_in": 100000,
  "rows_out": 95000,
  "bytes_processed": 52428800,
  "kestra_taskrun_id": "...",
  "logs_url": "/ui/kestra/executions/.../logs"  // iframe 透出（路线 C）
}
```

#### 13.3.7 算子目录（路线 C 支撑）

| 方法 | 路径 | 说明 | Release |
|------|------|------|---------|
| GET | `/api/v1/pipeline-operators` | 核心算子目录（10 个，含 input/output/config schema） | beta |
| GET | `/api/v1/pipeline-operators/kestra-plugins` | Kestra 插件发现（透出，路线 C） | beta |
| GET | `/api/v1/pipeline-operators/kestra-plugins/{plugin_type}` | 单插件详情（含 No-Code schema） | beta |

**核心算子响应**：

```json
{
  "operators": [
    {
      "type": "Filter",
      "category": "transform",
      "display_name": "过滤",
      "description": "按条件过滤行",
      "input_ports": 1,
      "output_ports": 1,
      "config_schema": { "properties": { "condition": {"type": "string"} } },
      "output_schema_rule": "同输入 Schema（行数减少）"
    },
    ...
  ]
}
```

**Kestra 插件响应**（代理 Kestra `/plugins`）：

```json
{
  "plugins": [
    {
      "type": "io.kestra.plugin.scripts.python.Script",
      "display_name": "Python 脚本",
      "category": "script",
      "no_code_schema": {...},  // Kestra 原生 JSON Schema，前端渲染表单
      "gaia_note": "作为 GenericKestraTask 插入，Schema 需用户声明"
    },
    ...
  ]
}
```

#### 13.3.8 数据版本管理（对标 Palantir Transaction）

复用现有 `/api/datasets`，新增 snapshot 端点：

| 方法 | 路径 | 说明 | 对标 Palantir |
|------|------|------|--------------|
| GET | `/api/datasets/{api_name}/snapshots` | Iceberg snapshot 列表 | `GET /v2/datasets/{rid}/transactions` |
| GET | `/api/datasets/{api_name}/snapshots/{snapshot_id}` | snapshot 详情（含 schema） | `getSchema?endTransactionRid=` |
| POST | `/api/datasets/{api_name}/snapshots/{snapshot_id}/activate` | 切换当前 snapshot（数据回滚） | Branch 指向 transaction |

### 13.4 Kestra API 对照

| Gaia 端点 | 对应 Kestra | Gaia 封装逻辑 |
|-----------|------------|--------------|
| `POST /pipelines` | `POST /flows`（YAML） | Gaia 收 IR（JSON）→ 存版本 → deploy 时翻译 YAML |
| `PATCH /pipelines/{api_name}` | `PUT /flows/{ns}/{id}`（revision+1） | Gaia 存新版本 → deploy 时同步 Kestra |
| `POST /pipelines/{api_name}/deploy` | Kestra 无（保存即生效） | Gaia 独有：翻译 IR→Kestra Flow + 注册 trigger |
| `POST /pipelines/{api_name}/builds` | `POST /executions/{ns}/{flowId}` | Gaia 先建 build 行 + 加锁 + 调 Kestra 触发 |
| `POST /builds/{id}?wait=true`（预留） | `POST /executions/...?wait=true` | MVP 不实现，Phase 2 |
| `GET /builds/{id}` | `GET /executions/{id}` | Gaia 查 PG（轻量），`?fetch_from_kestra=true` 透传 |
| `GET /builds/{id}/stream` | `GET /executions/{id}/follow`（SSE） | Gaia 包装 + 同步状态 |
| `POST /builds/{id}/cancel` | `DELETE /executions/{id}/kill` | Gaia 更新状态 + 调 Kestra kill |
| `POST /builds/{id}/retry` | `POST /executions/{id}/restart` | Gaia 新建 build 行 + 调 Kestra restart |
| `POST /builds/{id}/rollback` | Kestra 无 | Gaia 独有：数据回滚（切换 snapshot） |
| `GET /pipeline-operators/kestra-plugins` | `GET /plugins` | Gaia 代理透出（路线 C） |

**Kestra namespace 映射**（决策 A-2）：
- Gaia pipeline api_name → Kestra `{namespace}/{flowId}`
- namespace 规则：`gaia.{project_api_name}`（按项目隔离）
- flowId 规则：`pipeline_{api_name}`（前缀避免冲突）
- tenant：固定 `main`（OSS 单租户）

### 13.5 Palantir API 对照

| Palantir 概念 | Gaia 对应 | 对标方式 |
|--------------|----------|---------|
| Deploy（逻辑生效） | `POST /deploy` | 吸收：Deploy/Build 分离 |
| Build（数据物化） | `POST /builds` | 吸收：独立 build 端点 |
| Schedule（独立资源） | `/schedules` | 吸收：独立 CRUD |
| Trigger AND/OR 嵌套 | trigger JSONB 预留 | 吸收：MVP 单 trigger，结构预留嵌套 |
| CreateBuildRequest 参数 | build 请求体 force_build/retry/timeout | 吸收 |
| Transaction（数据版本） | Iceberg snapshot | 吸收：snapshot 端点 |
| Release Stage | `x-release-stage` | 吸收：OpenAPI 标注 |
| RID 标识 | api_name | **不吸收**（Gaia 用业务名） |
| Branch（git 分支） | MVP 单分支 | **不吸收**（Phase 2） |
| JobSpec 独立 | 合并到 Pipeline | **不吸收**（MVP 单输出） |
| Code Repos | 不做 | **不吸收**（Gaia 用 IR + 画布） |

### 13.6 DFX 属性

**可扩展性**：
- 算子扩展：`/pipeline-operators` 动态返回，新增算子不改路由
- 触发器扩展：trigger JSONB 预留嵌套，新触发类型不改 schema
- 执行引擎扩展：build 响应 `engine` 字段可扩展（duckdb/seatunnel/trino/kestra_plugin）
- API 版本化：`/v1/` 前缀，破坏性变更走 `/v2/`

**可维护性**：
- 资源分层：定义/版本/部署/构建/调度五类资源
- 复用 Gaia 现有规范：prefix/tags/response_model 风格一致
- schema 与 ORM 分离：pydantic 在 `schemas/pipeline_builder.py`（避免与现有 `pipeline.py` 冲突）

**可观测性**：
- 构建状态冗余在 builds 响应（不依赖 JOIN）
- 节点级详情独立端点（诊断"哪个节点失败"）
- SSE 实时推送（`/builds/{id}/stream`）
- `?fetch_from_kestra=true` 深查 Kestra 原始详情

**可靠性**：
- 幂等触发：`Idempotency-Key` header + 请求体 `idempotency_key`
- 异步执行：202 + build_id，绝不阻塞
- 写锁保护：build 前检查 sink_dataset.write_lock，冲突 409
- 乐观并发：PATCH 用 `If-Match: {version}` header（ETag），冲突 409

**可演进性**：
- cursor 分页预留（MVP offset，响应含 `next_cursor` 字段）
- `?wait=true` 预留（Phase 2 实现同步等待）
- Release Stage 标注（beta→stable 演进）

### 13.7 错误响应

沿用 Gaia 现有错误格式（一致性优先，不采用 RFC 9457）：

```json
{
  "detail": "Pipeline 'customer_etl' not found",
  "error_type": "NotFoundError",
  "code": "PIPELINE_NOT_FOUND"
}
```

**关键错误码**：

| HTTP | code | 场景 |
|------|------|------|
| 404 | PIPELINE_NOT_FOUND | 管道不存在 |
| 409 | PIPELINE_CONFLICT | api_name 冲突 / ETag 版本冲突 |
| 409 | DATASET_WRITE_LOCKED | sink_dataset 被其他 build 持锁 |
| 409 | VERSION_MISMATCH | If-Match ETag 不匹配 |
| 422 | SCHEMA_CONTRACT_VIOLATION | IR 契约校验失败（字段不匹配） |
| 422 | VIRTUAL_SINK_FORBIDDEN | sink_dataset 是 VIRTUAL（红线 9） |
| 422 | VALIDATION_ERROR | IR 结构错误 |
| 503 | KESTRA_UNAVAILABLE | Kestra 不可达（build 触发失败） |

### 13.8 OpenAPI 规范

- 所有端点用 FastAPI 自动生成 OpenAPI 3.1 spec
- `x-release-stage` 扩展字段标注成熟度（stable/beta/experimental）
- `tags` 分组：`Pipelines` / `Pipeline Versions` / `Deploy` / `Builds` / `Schedules` / `Operators` / `Dataset Snapshots`
- pydantic schema 在 `src/ontology/core/schemas/pipeline_builder.py`（新文件，避免与现有 `pipeline.py` SeaTunnel schema 冲突）
- 路由在 `src/ontology/routes/pipeline_builder.py`（新文件），`main.py` 注册 `pipeline_builder_router`



## 14. 前端设计

> 本节定义 Pipeline Builder 的前端架构与交互设计。核心原则："把复杂留给自己，把简单留给用户"——用户零代码拖拽 + AI 对话几句话完成管道，技术复杂度（四引擎/Schema 推演/版本化）全部封装。AI FDE 复用 Gaia 现有 AG-UI Agent 实践（图探索页面 ADR-015），不另起炉灶。

### 14.1 设计决策（DFX 属性）

基于 Palantir Pipeline Builder UI + React Flow 生产级实践 + Gaia 现有 AG-UI 实践，确认以下决策（详见 ADR-018 D10）：

| 决策 | 选择 | 对标/理由 |
|------|------|----------|
| F-1 画布引擎 | React Flow（@xyflow/react v12） | DAG 编辑器事实标准，headless 可定制 |
| F-2 状态管理 | Zustand store（graph 真相源）+ TanStack Query（server state） | React Flow 内部用 Zustand；与图探索 useGraphExplore 模式一致 |
| F-3 undo/redo | zundo（Zustand temporal 中间件） | 成熟方案；图探索以后也可迁移 |
| F-4 AI FDE 架构 | 复用 AG-UI Agent（/ai/agent）+ pipeline_builder toolset + STATE_SNAPSHOT 驱动 | 与图探索页面 ADR-015 完全一致 |
| F-5 AI 交互模式 | 对话式（多轮 ReAct），AssistantUiChat 组件 | 复用图探索的 AssistantUiChat |
| F-6 流式渲染 | 工具调用逐个发 STATE_SNAPSHOT，节点逐个出现 | 与 AG-UI 协议天然契合 |
| F-7 布局 | ELK layered（2026-07 从 dagre 升级；两遍 measure + 真实尺寸 + 正交边） | graph 真相源，布局纯函数派生，可替换 |
| F-8 节点管理 | NodeRegistry 注册表（类型→组件+schema+推演规则） | 避免switch-case膨胀，算子可扩展 |
| F-9 配置双模 | 表单（React Aria）+ IR/JSON 双向同步 | 单一真相源+派生视图；GenericKestraTask 必需 JSON |
| F-10 自动保存 | debounce 2s 存后端 + sendBeacon 兜底 | 防丢失，跨设备 |
| F-11 协同编辑 | MVP 不做，Phase 2 加 Yjs | React Flow + Yjs 成熟方案 |
| F-12 Parameters | MVP 预留 IR 字段，Phase 2 加 UI | 对标 Palantir Parameters |
| F-13 HITL | deploy/build 走 MetadataApprovalToolset 审批 | 与图探索 write/action HITL 一致 |
| F-14 Schema 预览 | MVP 推演 Schema（同步），Phase 2 加增量执行+数据样本 | 对标 Palantir Preview |
| F-15 iframe 透出 | 独立路由（执行详情/No-Code），不嵌入画布 | 路线 C |

### 14.2 整体布局（landing + editing 双模式）

对齐图探索页面的双模式设计：

#### 14.2.1 landing 模式（新建管道）

```
┌─────────────────────────────────────────────────┐
│ 极简顶栏（项目选择）+ PipelineBuilderLanding       │
│   ┌─────────────────────────────────────────┐   │
│   │  描述你想要的管道...                       │   │
│   │  ┌─────────────────────────────────────┐│   │
│   │  │ 清洗客户数据，过滤 inactive，关联订单  ││   │
│   │  └─────────────────────────────────────┘│   │
│   │  [开始构建]                               │   │
│   │                                          │   │
│   │  示例：                                   │   │
│   │  · 清洗客户数据并关联订单算总消费           │   │
│   │  · 每日同步订单数据到分析表                │   │
│   │  · 过滤异常订单并按地区聚合                │   │
│   └─────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

用户输入描述 → 切到 editing 模式 → AG-UI Agent 接管，工具调用逐个生成节点到画布。

#### 14.2.2 editing 模式（编辑中）

```
┌──────────────────────────────────────────────────────────────────┐
│ 顶栏：[← 返回] 管道名 [Save] [Deploy] [Build▾] [⚙ 设置] [⋮ 更多]  │
├────────┬────────────────────────────────────┬───────────────────┤
│        │                                    │                   │
│ 左侧栏  │         画布（React Flow）          │  右侧配置面板      │
│        │                                    │                   │
│ 算子    │   ┌──────┐   ┌──────┐   ┌──────┐ │  [📋 表单] [{} JSON]│
│ 面板    │   │Source│──▶│Filter│──▶│Join  │ │  ┌───────────────┐│
│        │   │customer│  │status│  │orders│ │  │ 条件:         ││
│ 核心    │   └──────┘   └──────┘   └──┬───┘ │  │ [status=active]││
│ 10个   │                          ▼     │  │ 类型: [WHERE ▾] ││
│        │                    ┌──────┐    │  └───────────────┘│
│ Kestra │                    │Sink  │    │  Schema 预览:      │
│ 透出    │                    │enriched│   │  ┌──────────────┐│
│        │                    └──────┘    │  │字段 类型 可空  ││
│        │                                │  │id   STRING  ✗  ││
│        │                                │  │name STRING  ✗  ││
│        │                                │  │total DECIMAL ✓ ││
│        │                                │  └──────────────┘│
├────────┴────────────────────────────────┴───────────────────┤
│ AI 助手面板（可收起，AssistantUiChat）                          │
│ > 清洗客户数据，过滤 inactive，关联订单算总消费                  │
│ [AI] 正在添加 Source 节点 customer_raw ●                       │
│ [AI] 正在添加 Filter 节点 ●●                                   │
│ [AI] 正在添加 Join 节点 ●●●                                    │
│ [用户] 把 Filter 条件改成 status='active'                      │
└──────────────────────────────────────────────────────────────────┘
```

**五区**：
1. **顶部工具栏**：返回 / Save / Deploy / Build / 设置 / 视图切换
2. **左侧算子面板**：NodeRegistry 驱动（核心 10 个 + Kestra 透出入口）
3. **中间画布**：React Flow + ELK 自动布局
4. **右侧配置面板**：表单/IR 双模 + Schema 预览
5. **底部 AI 助手**：AssistantUiChat（可收起，与图探索的 showConversation toggle 一致）

> **⚠️ 2026-07 重构（范式变更）**：
> 上述「右侧配置面板」已**移除**，改为「画布最大化 + 弹窗」范式：
> - **节点卡片摘要**：每个节点在画布上直接显示 1-3 行核心配置（由 `nodeConfigSummary.getConfigSummary` 纯函数推导），如 Join 显示 `INNER · order_id = order_id`、Filter 显示 `status = "active"`、多条件降级为 `共 3 个条件`。用户从画布即可理解流程逻辑，无需打开配置。
> - **双击节点 → NodeConfigModal**：配置表单较重（多条件/多键），右侧 320px 抽屉放不下且与 Schema/历史/JSON 抢位置，改为居中弹窗（min 560 / max 720 px），参考 n8n NDV 范式。
> - **辅助视图弹窗**：原右侧抽屉的 Schema/执行历史/JSON 改为工具栏按钮（`onOpenAux`）触发的 `PipelineAuxModal`，画布获得最大留白。
> - 交互：单击节点仅选中（高亮，用于连线/删除），双击打开配置弹窗（`zoomOnDoubleClick={false}`）。
> - 动机：参考 n8n（双击 NDV + 节点摘要）/ Dify（NodeBody 摘要）/ Coze 调研结论，配置走弹窗给表单更大空间，画布留白便于操作和查看。

**与图探索布局的差异**（主交互不同）：
- 图探索：对话流在左侧大面板（NL 查询是主操作）
- Pipeline Builder：AI 助手在底部可收起（画布编辑是主操作，AI 是辅助）

### 14.3 状态管理架构

**核心原则：单一真相源 + 派生视图**。graph 数据（nodes+edges）是唯一真相，表单/JSON/画布布局都是派生视图。

```
┌─ Server State（TanStack Query）──────────────────────┐
│  GET /pipelines/{api_name}    → 加载管道 graph          │
│  PATCH /pipelines/{api_name}  → debounce 自动保存       │
│  POST /ai/agent (SSE)         → AG-UI Agent 流式        │
│  POST /pipelines/.../validate → Schema 推演校验        │
└──────────────────────────────────────────────────────┘
          │ hydrate（加载时）/ debounce 2s（编辑时）
          ▼
┌─ Client State（Zustand + zundo）──────────────────────┐
│  pipelineCanvasStore: {                                │
│    graph: { nodes, edges },     // 真相源（IR）         │
│    selectedNodeId,              // 当前选中             │
│    viewMode,                    // 'form' | 'ir-json'  │
│    dirty,                       // 未保存标记           │
│  } + temporal(zundo)            // undo/redo 历史      │
└──────────────────────────────────────────────────────┘
          │
    ┌─────┼─────────┬──────────────┐
    ▼     ▼         ▼              ▼
┌─ 画布 ─┐ ┌─ 表单 ─┐ ┌─ JSON ─┐ ┌─ 布局 ─┐
│React   │ │React   │ │Json    │ │ ELK    │
│Flow    │ │Aria    │ │Editor  │ │派生    │
│nodes/  │ │config  │ │整个    │ │positions│
│edges   │ │schema  │ │graph   │ │        │
└────────┘ └────────┘ └────────┘ └────────┘
  所有视图读写同一个 Zustand graph，改一个 → store 更新 → 其他自动同步
```

**自动保存**（决策 F-10）：
- 编辑时 debounce 2s 调 `PATCH /pipelines/{api_name}` 存草稿版本
- `window.beforeunload` 时 `navigator.sendBeacon` 兜底保存（防页面关闭丢失）
- dirty 标记追踪未保存状态（顶栏显示 ●）

### 14.4 NodeRegistry 注册表模式

避免 switch-case 膨胀（核心 10 算子 + GenericKestraTask + 未来扩展），用注册表驱动算子面板 + 配置表单 + Schema 推演。

```typescript
interface NodeDefinition {
  type: string;                    // "Filter" | "Join" | "GenericKestraTask" | ...
  category: "source" | "transform" | "sink" | "quality" | "kestra";
  displayName: string;
  icon: ReactComponent;
  inputPorts: number;              // 端口数
  outputPorts: number;
  configSchema: JSONSchema;        // 驱动表单渲染（React Aria）
  defaultConfig: object;
  NodeComponent: ReactComponent;   // 画布上的节点视觉
  ConfigFormComponent: ReactComponent;  // 右侧配置表单
  schemaInferenceRule: (input: Schema, config: object) => Schema;
  validateConnection: (from: Node, to: Node) => ValidationResult;
}

// 注册（src/web-ui/src/lib/nodeRegistry.ts）
NodeRegistry.register(filterDefinition);
NodeRegistry.register(joinDefinition);
NodeRegistry.register(genericKestraTaskDefinition);

// 算子面板从 Registry 渲染；配置表单从 configSchema 渲染
```

**新算子扩展**：只需注册一个 NodeDefinition，算子面板自动出现该算子 + 配置表单自动渲染。不需要改路由或 switch-case。

### 14.5 AI FDE 架构（复用 AG-UI Agent）

**核心：复用图探索页面 ADR-015 的 AG-UI Agent + STATE_SNAPSHOT 驱动画布模式，不另起炉灶。**

#### 14.5.1 架构总览

```
用户在 AssistantUiChat 输入："清洗客户数据，过滤 inactive，关联订单算总消费"
    │
    ▼
PipelineBuilderAgent（HttpAgent 子类）→ POST /ai/agent（SSE 流）
    │  - 注入 context（ontology/project/现有 graph）到 forwardedProps
    │  - tap SSE 流拦截 STATE_SNAPSHOT
    │
    ▼
后端 AIAgent（pydantic-ai ReAct 循环）:
    │  - 挂载 pipeline_builder toolset（新增）
    │  - 每轮决策调哪个工具
    │  - list_datasets（了解数据）→ add_source → add_transform → add_sink
    │  - 每个工具执行后发 STATE_SNAPSHOT，snapshot={"pipeline_canvas": {...}}
    │
    ▼
SSE 事件流 → 前端 PipelineBuilderAgent 拦截:
    │  - tap STATE_SNAPSHOT 事件
    │  - 解析 state.pipeline_canvas → 调 onPipelineCanvasState 回调
    │  - 回调更新 Zustand store（nodes/edges）→ React Flow 渲染
    │
    ▼
画布更新：节点逐个出现（AI "在画管道"的可视反馈）
    │
    ▼
用户多轮对话微调：
    │  "把 Filter 条件改成 status='active'"
    │  → AI 调 modify_node → STATE_SNAPSHOT → 节点配置更新
```

#### 14.5.2 PipelineCanvasState 共享状态

对标图探索的 `GraphExploreState`，新增 `PipelineCanvasState` 作为 Agent 的 AppState.deps：

```python
@dataclass
class PipelineCanvasState:
    nodes: list[NodeSnapshot]      # 画布节点（id/type/config）
    edges: list[EdgeSnapshot]      # 连线
    selected_node_id: str | None

    def with_added_node(self, node): ...        # 不可变更新
    def with_modified_node(self, node_id, config): ...
    def with_removed_node(self, node_id): ...
    def with_connection(self, from_id, to_id): ...
```

#### 14.5.3 pipeline_builder toolset（AI 工具集）

对标图探索的 toolsets（metadata/object_query/canvas_control），新增 `pipeline_builder` toolset（8 个工具，决策 5）：

| 工具 | 用途 | 发 STATE_SNAPSHOT | 对标图探索 |
|------|------|:-:|-----------|
| `list_datasets()` | 查可用 Dataset（帮 AI 选 Source） | 否（纯查询） | list_object_types |
| `get_dataset_schema(dataset)` | 查 Dataset 字段（帮 AI 推断） | 否（纯查询） | get_object_type |
| `add_source(dataset)` | 加 Source 节点 | ✅ | query_with_dataframe |
| `add_transform(type, config)` | 加转换节点（Filter/Join/...） | ✅ | traverse_link |
| `add_sink(dataset)` | 加 Sink 节点 | ✅ | — |
| `modify_node(node_id, config)` | 修改节点配置 | ✅ | color_by |
| `remove_node(node_id)` | 删除节点 | ✅ | — |
| `connect(from_id, to_id)` | 连接节点 | ✅ | — |

每个工具执行后发 `StateSnapshotEvent`（与 `canvas_control.py` 的 `_snapshot_event` 模式一致）：

```python
def _pipeline_snapshot_event(canvas_state):
    return StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={"pipeline_canvas": canvas_state.model_dump(mode="json")},
    )
```

#### 14.5.4 deploy/build 的 HITL 审批（决策 F-13）

对标图探索的 write/action 工具用 `MetadataApprovalToolset`：

- `deploy_pipeline()` / `build_pipeline()` 工具用 `MetadataApprovalToolset` 包装，`metadata={"risk_level": "high"}`
- AI 说"我要部署管道"→ 触发 AG-UI interrupt → 前端 `BatchApprovalPanel` 弹确认 → 用户确认才执行
- 与图探索的 HITL 模式完全一致（ADR-010）

#### 14.5.5 system_prompt 前端管理（决策 F-6）

与图探索一致，前端 `AssistantUiChat` 的 `systemPrompt` prop 注入：

```tsx
<AssistantUiChat
  agent={pipelineAgent}
  systemPrompt={`你是管道构建助手。用户用自然语言描述数据管道，你调用工具在画布上构建。
可用工具：list_datasets（查数据源）、add_source（加数据源节点）、add_transform（加转换：Filter/Join/Aggregate/...）、add_sink（加输出）、modify_node（改配置）、connect（连线）。
决策原则：先 list_datasets 了解可用数据，再逐步 add_source → add_transform → add_sink 构建管道。用户要求修改时调 modify_node。不要编造不存在的 Dataset。`}
/>
```

#### 14.5.6 前端 Agent 实现（PipelineBuilderAgent）

对标 `useGraphExploreAgent`，新增 `usePipelineBuilderAgent` hook：

```typescript
// src/web-ui/src/hooks/usePipelineBuilderAgent.ts
class PipelineBuilderAgent extends HttpAgent {
  // 注入 ontology/project/当前 graph 到 forwardedProps
  // tap STATE_SNAPSHOT → 解析 state.pipeline_canvas → onPipelineCanvasState
}

export function usePipelineBuilderAgent({ ontology, onPipelineCanvasState }) {
  // useMemo 依赖 ontology（上下文切换=新会话）
  // 指纹去重（防 runtime re-render 重放死循环，与图探索三层防护一致）
}
```

**死循环防护**（复用图探索三层）：
1. SSE tap 指纹去重
2. applyingRef 防并发
3. lastAppliedFingerprint 兜底

### 14.6 配置面板：表单/IR 双模（决策 F-9）

**单一真相源 + 派生视图**：表单和 JSON 编辑同一个 Zustand graph，改一边另一边自动更新。

```
右侧配置面板（ConfigPanel）:
┌────────────────────────────────────┐
│ [📋 表单] [{} JSON]  ← 模式切换      │
├────────────────────────────────────┤
│  表单模式（选中节点）：              │
│  ┌──────────────────────────────┐  │
│  │ 条件: [status='active'      ]│  │  ← React Aria TextField
│  │ 类型: [WHERE ▾]              │  │  ← React Aria Select
│  └──────────────────────────────┘  │
│                                    │
│  JSON 模式（整个 graph IR）：        │

> **⚠️ 2026-07 重构**：ConfigPanel 不再常驻右侧，改为「双击节点 → NodeConfigModal」弹窗。
> JSON 模式移到工具栏「JSON」按钮触发的 PipelineAuxModal。详见 §14.2 布局重构说明。
│  ┌──────────────────────────────┐  │
│  │ {                             │  │
│  │   "nodes": [                  │  │  ← JsonEditor
│  │     {"id":"n1","type":"Filter",│  │     (Monaco/CodeMirror)
│  │      "config":{"condition":   │  │
│  │       "status='active'"}}     │  │
│  │   ],                          │  │
│  │   "edges": [...]              │  │
│  │ }                             │  │
│  └──────────────────────────────┘  │
└────────────────────────────────────┘
```

**双向同步机制**：
- **表单 → store**：字段 onChange → `store.graph.nodes[selectedId].config[field]`（Zustand immutable update）
- **store → JSON**：JSON 内容由 `JSON.stringify(graph, null, 2)` 派生
- **JSON → store**：JSON onChange → debounce → `JSON.parse` → Zod 校验 → 覆盖 store.graph；parse 失败只标红不覆盖
- **防循环**：JSON 编辑器 debounce + 受控（避免每次按键 parse 覆盖导致光标跳动）
- **校验共享**：表单和 JSON 最终都过同一个 `validateGraph(graph)` → Schema 推演 → 契约错误标红

**GenericKestraTask 必需 JSON 模式**：1700+ Kestra 插件的 `task_config` 无法表单覆盖，JSON 模式是用户粘贴 Kestra No-Code 生成配置的入口。

**复用 Gaia 现有**：`components/ui/`（React Aria 原语：TextField/Select/ComboBox）+ Tailwind v4.3 样式。

### 14.7 布局：ELK layered（2026-07 从 dagre 升级，决策 F-7）

**graph 真相源，布局纯函数派生，可替换。**

```
┌─ 真相源 ─┐     ┌─ 派生 ─┐     ┌─ 渲染 ─┐
│  graph   │ ──▶ │  ELK   │ ──▶ │ React  │
│ (nodes,  │     │ (x,y)  │     │ Flow   │
│  edges)  │     │ +尺寸  │     │        │
└──────────┘     └────────┘     └────────┘
   Zustand        elkjs          画布
   store          layered
```

- `useElkLayout` hook（`src/hooks/useElkLayout.ts`）监听 nodes/edges **结构**变化 → 调 ELK `layered`（`direction: RIGHT`）→ 写回 node.position
- **两遍 measure 模式**（关键）：ELK 需要节点尺寸，但真实尺寸来自 DOM。React Flow v12 在节点挂载后经 `dimensions` change 把尺寸写入 `node.measured`。hook 等所有参与布局的节点都有 `measured` 后才调 ELK，避免用固定尺寸导致间距错乱（dagre 旧实现的硬伤）
- **结构签名防循环**：只在「节点 id 集合 + 边拓扑」变化时重算，position 变化（拖拽、布局自身回写）不触发。否则会形成 布局写 position → onNodesChange → 触发重算 → 无限循环（React Flow 社区高频坑）
- 用户手动拖拽后位置写入 node.position（`markManuallyPositioned`，覆盖 ELK 值），graph 结构不变
- 孤立节点（无连边）不参与布局，保留用户落点
- **ELK 配置**：`edgeRouting: ORTHOGONAL`（正交折线边）+ `nodePlacement.strategy: NETWORK_SIMPLEX`（整体最优放置）+ `spacing.nodeNode: 40` / `nodeNodeBetweenLayers: 60`
- **fitView 延后一帧**：setNodes 后立即 fitView 在 RF v12 有已知 bug（xyflow#3946，只 fit 部分节点），用 `requestAnimationFrame` 延后
- **为什么从 dagre 换 ELK**：dagre 用固定 `NODE_WIDTH=200` 布局，而节点实际宽度 61~144px 不等，导致同 rank 节点重叠 + 间距忽大忽小 + 整体挤顶部。ELK 用真实尺寸 + NETWORK_SIMPLEX 放置，同 rank 多 source 自动上下分散并对齐中点
- **性能**：elkjs bundled 同步引擎，管道 ≤50 节点无压力；超 50 节点可切 `elkjs/lib/elk-api` + worker（参考 Stately cookbook）
- 参考：[Stately React Flow + ELK cookbook](https://stately.ai/docs/packages/graph/react-flow-elk-pipeline)、[ELK Layered reference](https://eclipse.dev/elk/reference/algorithms/org-eclipse-elk-layered.html)

> **历史**：2026-07 前用 dagre（`useAutoLayout.ts`，已删除），固定 `NODE_WIDTH=200` + `rankdir: 'LR'`。升级后 dagre 依赖移除（cytoscape-dagre 自带 dagre 打包，图探索不受影响）。

#### 14.7.1 画布交互能力（2026-07 新增，对标 n8n/Dify/Zapier）

布局能力从「仅结构变化自动触发」开放为完整工具集：

| 能力 | 触发方式 | 实现 |
|------|---------|------|
| **手动整理布局** | 画布 Controls 面板「整理」按钮 / `Cmd+Ctrl+O` | `runLayout({ force: true })` 清除手动定位标记，强制 ELK 重排所有连边节点（图标用 lucide `align-horizontal-distribute-center`） |
| **缩放适配** | `1` 键 / Controls FitView 按钮 | `reactFlowInstance.fitView()` |
| **重置缩放** | `0` 键 | `setViewport({ x:0, y:0, zoom:1 })` |
| **手型平移** | 按住 `Space` 拖拽 | `panActivationKeyCode="Space"`（RF 原生） |
| **框选多选** | 拖拽空白区框选 | `selectionOnDrag` + `selectionMode=Partial` |
| **Shift 多选** | `Shift`+点击节点 | `multiSelectionKeyCode="Shift"` |
| **对齐**（左/中/右/顶/中/底） | 多选 ≥2 节点 → 浮动工具条 | `nodeAlignment.ts` 纯函数几何计算 |
| **分布**（水平/垂直等距） | 多选 ≥3 节点 → 浮动工具条 | `distributeNodes` 纯函数 |
| **多选复制** | 浮动工具条复制按钮 | `duplicateNodes` 生成副本 + 位移 40px 错开 |
| **多选删除** | 浮动工具条删除按钮 / `Delete` | `removeNode` 批量 |
| **小地图开关** | 画布 Controls 面板「小地图」按钮 | `ControlButton` 切换 `showMiniMap` |

**多选状态管理**：
- 单选 `selectedNodeId`（配置弹窗用）与多选 `selectedNodeIds`（批量操作用）分离
- 单击节点 → 设单选、清多选；Shift/框选 → 写多选、清单选（避免高亮并存）
- `onNodesChange` 处理 select change 写入 `selectedNodeIds`，不动 `selectedNodeId`（避 xyflow #2405 虚假 select change 闪退）
- 对齐/分布后对涉及节点调 `markManuallyPositioned`，防止 ELK 自动布局覆盖用户对齐结果

**浮动工具条**（`SelectionToolbar.tsx`）：选中 ≥2 节点时经 `<Panel position="top-center">` 渲染在画布顶部中央，含对齐图标组 + 分布图标组 + 复制/删除。

**Controls 面板**：`<Controls orientation="horizontal">` 水平排列，画布左下角；内置 Zoom In/Out/FitView + 自定义「整理」「小地图」两个 `<ControlButton>`（继承 RF 统一样式）。

### 14.8 Schema 预览（决策 F-14）

对标 Palantir Preview pane（选中节点 → 增量执行到该节点 → 看数据）：

- **MVP**：选中节点 → 右侧面板显示推演出的输出 Schema（字段名/类型/可空）+ 契约错误标红（如"字段 status 不存在于上游"）。推演是同步的（SchemaInferenceEngine，不需执行），MVP 立即给反馈。
- **Phase 2**：加增量执行（局部 IR 执行器执行到该节点）+ 数据样本（前 100 行）+ 列统计（histogram）

### 14.9 iframe 透出层（决策 F-15，路线 C）

Kestra UI 透出不嵌入画布（画布保持纯净），用独立路由/面板：

| 场景 | 实现位置 | 说明 |
|------|---------|------|
| 执行详情（日志/metrics/拓扑） | `/pipelines/{api_name}/builds/{build_id}` 页面内嵌 iframe | 复杂场景才看 |
| GenericKestraTask 配置 | 节点配置面板"在 Kestra 中配置"按钮 → 新窗口/iframe | Kestra No-Code 编辑器 |
| Dashboard 监控大盘 | `/operations` 页面 iframe | 运维场景 |

**工程问题**：SSO 认证打通（Gaia 登录免登 Kestra）+ URL 定位（Gaia 构造 iframe URL）+ 视觉割裂（接受，"高级模式"）。

### 14.10 组件清单（自研 vs 复用）

| 组件 | 自研/复用 | 说明 |
|------|----------|------|
| 画布（React Flow） | 复用（`@xyflow/react`） | DAG 编辑器核心 |
| 自定义节点（SourceNode/TransformNode/SinkNode） | 自研 | NodeRegistry 驱动，固定大小+状态色+图标 |
| 自定义边（SchemaEdge） | 自研 | 数据流方向 + 契约状态色 |
| 算子面板（OperatorPanel） | 自研 | NodeRegistry 驱动，核心算子 + Kestra 入口 |
| 配置面板（ConfigPanel） | 自研 | 表单/IR 双模 + Schema 预览 |
| 算子配置表单（FilterForm/JoinForm...） | 自研 | React Aria 表单（复用 `components/ui/`） |
| JsonEditor | 复用（Monaco/CodeMirror） | IR/JSON 模式编辑 |
| Schema 预览（SchemaPreview） | 自研 | 字段表格 + 契约错误 |
| **AssistantUiChat** | **复用图探索组件** | AG-UI 聊天（不改动） |
| **usePipelineBuilderAgent** | 自研（对标 useGraphExploreAgent） | HttpAgent 子类 + STATE_SNAPSHOT tap |
| PipelineCanvasState | 自研（对标 GraphExploreState） | Agent 共享状态 |
| pipeline_builder toolset | 自研（后端） | 8 个工具，发 STATE_SNAPSHOT |
| PipelineBuilderLanding | 自研（对标 ExploreLanding） | landing 对话框 + 示例 |
| NodeRegistry | 自研 | 注册表模式 |
| useElkLayout | 自研 | ELK layered 布局 hook（两遍 measure + 结构签名防循环 + force 整理） |
| SelectionToolbar | 自研 | 多选浮动工具条（对齐/分布/复制/删除） |
| nodeAlignment | 自研 | 对齐与分布几何纯函数（6 对齐 + 2 分布） |
| KestraEmbed（iframe 透出） | 自研 | 执行详情/No-Code 透出 |
| Preview pane（数据预览） | Phase 2 | 增量执行 + 数据样本 |
| 工具栏（PipelineToolbar） | 自研 | Save/Deploy/Build/Validate |

**复用 Gaia 现有**：
- `components/ui/`（React Aria 原语）
- `components/AssistantUiChat`（AG-UI 聊天组件，不改动）
- `components/assistant-ui/`（Thread/BatchApprovalPanel 等）
- Tailwind v4.3 样式体系
- `api/` 客户端（调用 §13 REST API）
- 路由/认证（App 框架）

### 14.11 DFX 属性

**可用性（"把简单留给用户"）**：
- 零代码：拖拽 + 表单配置
- AI FDE：几句话生成草稿，多轮微调
- 实时反馈：Schema 推演同步校验，错误标红
- 键盘可达：Tab/Enter/Esc/Delete
- 渐进式披露：节点固定大小，配置在右侧面板

**可扩展性**：
- 算子扩展：NodeRegistry 注册，自动出现在面板 + 表单
- GenericKestraTask：JSON 模式 + iframe Kestra No-Code
- 节点组件复用：注册表驱动

**性能**：
- React Flow 虚拟视口（节点多时只渲染可见区域）
- Schema 推演增量计算（配置变更只重算下游）
- AI 流式渲染（STATE_SNAPSHOT 逐个节点出现）

**可观测性**：
- 节点状态色（PENDING/RUNNING/SUCCESS/FAILED）
- Schema 预览 + 契约错误
- iframe 透出 Kestra 执行详情

**可靠性**：
- 本地草稿 debounce 自动保存 + sendBeacon 兜底
- 连接校验（isValidConnection：端口类型 + Schema 契约）
- AI 生成结果用户确认（HITL deploy/build）
- 死循环防护（三层指纹去重，复用图探索模式）

**可演进性**：
- 布局可替换（ELK→其他引擎，只改派生函数）
- 双模（表单/JSON）支持高级用户
- 协同编辑预留（Phase 2 Yjs）
- Release Stage 标注（API 层）

### 14.12 与图探索页面的一致性对照

| 维度 | 图探索（ADR-015） | Pipeline Builder | 一致性 |
|------|------------------|-----------------|--------|
| AI 架构 | AG-UI Agent + STATE_SNAPSHOT | AG-UI Agent + STATE_SNAPSHOT | ✅ 完全一致 |
| 聊天组件 | AssistantUiChat | AssistantUiChat（复用） | ✅ 复用 |
| HttpAgent 子类 | GraphExploreAgent | PipelineBuilderAgent（对标） | ✅ 同模式 |
| 共享状态 | GraphExploreState | PipelineCanvasState（对标） | ✅ 同模式 |
| 工具发 snapshot | canvas_control toolset | pipeline_builder toolset | ✅ 同模式 |
| 死循环防护 | 三层指纹去重 | 三层指纹去重（复用） | ✅ 完全一致 |
| HITL | MetadataApprovalToolset（write/action） | MetadataApprovalToolset（deploy/build） | ✅ 同模式 |
| system_prompt | 前端注入 | 前端注入 | ✅ 一致 |
| 布局 | landing + exploring 双模式 | landing + editing 双模式 | ✅ 同模式 |
| 画布引擎 | Cytoscape（图可视化） | React Flow（DAG 编辑器） | ⚠️ 不同（场景不同，§15.5 已决策） |
| AI 面板位置 | 左侧大面板（NL 是主操作） | 底部可收起（画布编辑是主操作） | ⚠️ 差异（主交互不同） |

**结论**：AI FDE 的架构与图探索完全一致（复用 AG-UI + STATE_SNAPSHOT + AssistantUiChat + HITL），仅画布引擎（Cytoscape→React Flow）和 AI 面板位置（左侧→底部）因场景不同而调整。


## 15. 与现有架构的集成边界

> 本节明确 Pipeline Builder 与 Gaia 现有组件的集成关系、复用边界、不重复造轮子的原则。

### 15.1 集成关系图

```
┌─ Pipeline Builder（新增）──────────────────────────────────────┐
│ PipelineBuilderService                                          │
│   ├─ SchemaInferenceEngine（自研）                              │
│   ├─ KestraEngine（适配层，调 Kestra REST）                     │
│   └─ IR 管理（CRUD + 版本）                                     │
└──────┬──────────────────┬──────────────────┬──────────────────┘
       │ 复用              │ 复用              │ 复用
       ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌────────────────────┐
│ OntologySvc  │  │ IcebergStore │  │ IndexSyncService   │
│ .link_dataset│  │ (写入+snapshot)│  │ .sync_now (可选触发)│
│ (阶段2绑定)   │  │              │  │                    │
└──────────────┘  └──────────────┘  └────────────────────┘
       │                ▲                  ▲
       │                │                  │
       ▼                │                  │
┌──────────────┐  ┌─────┴───────┐  ┌───────┴────────────┐
│ ObjectType   │  │ TrinoEngine │  │ DorisIndexStore     │
│ (查询本体)    │  │ (SQL 转换)   │  │ (在线读，独立链路)   │
└──────────────┘  └─────────────┘  └────────────────────┘
                         ▲
                         │ Kestra HTTP Task 调用
                  ┌──────┴───────┐
                  │ SeaTunnel    │
                  │ (数据搬运，现有)│
                  └──────────────┘
```

### 15.2 复用清单（不重复造轮子）

| 现有组件 | 复用方式 | 不做的事 |
|---------|---------|---------|
| `OntologyService.link_dataset` | 阶段2 绑定 ObjectType↔Dataset | 不重写绑定逻辑 |
| `IcebergStore` | 管道写入 Iceberg + snapshot 管理 | 不直接操作 pyiceberg，走 IcebergStore |
| `TrinoQueryEngine` | 转换算子 SQL 执行 | 不自研 SQL 引擎 |
| `SeaTunnelEngine` | 数据搬运（Kestra HTTP Task 调用） | 不替换为 Kestra 原生搬运 |
| `IndexSyncService` | 可选触发 Doris 同步 | 不直接写 Doris |
| `TimeTravelService` | 下游按 snapshot 读取 | 不自研版本读取 |
| `core/naming.py` | 物理命名（表名/Dataset api_name） | 不手拼命名 |
| ADR-016 Project 模型 | 管道归属 Project | 不新建归属模型 |

### 15.3 不集成的边界（明确隔离）

- **不绕过 Kestra 直接调 DuckDB/Trino**：所有执行经 Kestra 编排，保证状态机/重试/血缘统一
- **不扩展 Trino 写入**：保持 Trino 只读联邦查询定位，转换写入走 DuckDB
- **不修改 Kestra/SeaTunnel/Trino 源码**：通过原生 HTTP API + 插件能力集成
- **不让管道直接写 Doris**：Doris 同步是 IndexSyncService 独立链路（红线 4）
- **不让管道写 VIRTUAL Dataset**：VIRTUAL 是联邦代理，无版本，禁止写入（红线 9）
- **不在管道配置里引入自然语言**：管道构建/编辑是结构化 IR，AI 辅助走 `/ai/*`（红线 11）

### 15.4 与 ADR-014 多源融合的关系

ADR-014 解决「数据从哪来」（25 种连接器 + CDC），Pipeline Builder 解决「数据怎么加工」：

- ADR-014 的 `DataSourceService` 负责数据接入（SeaTunnel 搬运到 Iceberg）
- Pipeline Builder 的 `PipelineBuilderService` 负责数据转换（Iceberg→转换→Iceberg）
- 两者通过 Dataset（Iceberg 表）衔接：DataSourceService 产出 Dataset，Pipeline Builder 消费 Dataset

**协作场景**：用户在 Pipeline Builder 画布上，Source 节点可以选择「外部数据源（需先搬运）」，系统自动调用 DataSourceService 触发搬运，再执行转换。用户视角是一条管道，不感知两个 Service。

### 15.5 与 ADR-008 Iceberg→Doris 同步的关系

> **⚠️ 2026-07 去 SeaTunnel 化后订正**：ADR-008 描述的「backfill BATCH + stream STREAMING 双模板」**已整体删除**（T1.10）。当前 Iceberg→Doris 同步不走 SeaTunnel，改由 `ObjectIndexFunnel`（从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`）完成。下方原描述保留作历史记录。

~~ADR-008 解决「Iceberg 数据如何同步到 Doris」（backfill BATCH + stream STREAMING 双模板），Pipeline Builder 解决「数据如何加工」：~~

- ~~管道写入 Iceberg 后，IndexSyncService 的 stream pipeline 自动同步到 Doris（主链路）~~
- ~~管道可配置「触发 sync_now」选项，调用 `IndexSyncService.sync_now`（容灾兜底，非主链路）~~
- **管道不直接写 Doris**，Doris 同步是独立链路（红线 4）——当前由 ObjectIndexFunnel 承担（不经 SeaTunnel）

---

## 16. 约束、边界与不做的事

> 本节明确 Gaia Pipeline Builder 的能力边界，避免过度承诺。所有「不做」都有明确理由和延后计划。

### 16.1 能力边界（MVP）

**能做**：
- ✅ 可视化 DAG 拖拽编辑（React Flow 画布）
- ✅ 实时 Schema 推演校验（毫秒级，不碰数据）
- ✅ 核心转换算子（Filter/Select/Rename/TypeCast/Join/Aggregate/Union/Expression/QualityCheck）
- ✅ 输出到 Dataset（Iceberg 新 snapshot，原子提交）
- ✅ 映射到已有 ObjectType（阶段1，写入绑定 Iceberg 表）
- ✅ 全量重建 + 增量追加两种写入模式
- ✅ 手动触发 + 定时调度（Cron）
- ✅ 执行状态监控（成功率/耗时/日志）
- ✅ 逻辑版本 + 数据版本双重管理
- ✅ 逻辑回滚 + 数据回滚（秒级，切 snapshot）
- ✅ 「先搬运、再转换」组合管道（Kestra 调 SeaTunnel）

### 16.2 不做的事（MVP 不做，有明确延后计划）

| 不做项 | 理由 | 延后到 |
|--------|------|--------|
| Streaming Pipeline | 需引入 Flink，新引擎依赖 | Phase 3+ |
| 大规模分布式 Batch（Spark） | TB+ 级场景，需引入 Spark 或扩展 Trino 写入（逆定位） | Phase 3+ |
| CDC 合并模式（管道级 MERGE） | 调度链路复杂，需主键匹配+变更识别 | Phase 2 |
| Job Group 分组 | MVP 单输出，多输出分组复杂 | Phase 2 |
| 分支评审 / 灰度发布 | 依赖 ADR-016 权限体系完善 | Phase 2 |
| AI/LLM 节点 | 依赖 AI 原生管道设计 | Phase 3 |
| Custom Function | MVP 用内置算子+表达式 | Phase 2 |
| 代码双向导出 | 复杂度高，依赖 Kestra 插件深度集成 | Phase 3 |
| Marketplace 分发 / 多租户 | 依赖治理体系成熟 | Phase 3+ |
| 实时数据预览 | 依赖局部 IR 执行器 | Phase 2 |
| 数据质量参痏完整性 | 依赖外键约束机制 | Phase 2 |
| 字段级脱敏 / 行级权限 | 依赖 ADR-016 权限体系 | Phase 2 |
| 分区级重试 | 依赖 Iceberg 分区+DuckDB 分区执行 | Phase 2 |
| 上游依赖触发 | 依赖 Dataset 版本事件机制 | Phase 2 |
| **自研执行监控 UI**（TaskRun 级日志/metrics/拓扑） | Kestra 已有完整能力，iframe 透出即可（路线 C） | 不做（透出 Kestra） |
| **逐个映射 1700+ Kestra 插件为 Gaia 算子** | 死路，永远追不上迭代；用 GenericKestraTask 透传（路线 C） | 不做（透传） |
| **自研 Dashboard 监控大盘** | Kestra 已有，iframe 透出即可（路线 C） | 不做（透出 Kestra） |
| **自研调度/触发器高级配置 UI** | Kestra 已有，iframe 透出即可（路线 C） | 不做（透出 Kestra） |
| 全局执行优化（路径裁剪/公共子表达式消除） | DuckDB 自身有优化器（向量化+谓词下推），Gaia 层优化后续叠加 | Phase 2 |

### 16.3 与 Palantir 的根本差异（不追求对齐）

| 维度 | Palantir | Gaia | 不对齐的理由 |
|------|----------|------|-------------|
| 执行引擎 | 全栈自研三引擎 | 复用 Kestra+Trino（MVP 只批） | 基于开源栈，不自研执行引擎 |
| Versioned Dataset | 自研完整抽象 | 复用 Iceberg snapshot + 治理记录 | 不重复造抽象层 |
| Ontology 生成 | 管道直接生成 Object Type schema | 管道映射/建议，用户确认 | 元数据驱动，用户定义 ObjectType |
| 代码双向互通 | 完整支持 | MVP 不做 | 复杂度高，延后 |
| 全栈治理原生 | Foundry 全栈 | 对接现有治理体系（在建） | 治理体系分阶段建设 |

**核心立场**：Gaia 不追求「复刻 Palantir」，而是「在开源栈上实现 Palantir 的核心设计思想」（IR + Schema 引擎 + Ontology 绑定），执行层复用开源，自研聚焦在独有价值。

### 16.4 红线遵守清单

Pipeline Builder 设计严格遵守 Gaia 现有红线：

- [x] 红线 3：管道写入只落地 Iceberg（通过 IcebergStore/Trino），不直接写 Doris/PG 业务表
- [x] 红线 4：管道不直接写 Doris，Doris 同步由 IndexSyncService 独立触发
- [x] 红线 9：管道输出节点拒绝 VIRTUAL Dataset 作为目标
- [x] 红线 10：物理命名走 `core/naming.py`（管道中间表/输出 Dataset api_name/Iceberg 表名）
- [x] 红线 11：管道配置是结构化 IR，不引入自然语言（AI 辅助走 `/ai/*`）
- [x] 基于开源不侵入：Kestra/SeaTunnel/Trino 通过原生 HTTP API 调用，不修改源码
- [x] Schema 变更走 Alembic：新增 pipeline_* 表 + datasets 表加列，配 migration

---

## 17. 分期实施路线

> 参考 Palantir 企业落地的三阶段路线，结合 Gaia MVP 范围，定义分期实施计划。每阶段有明确交付物和验收标准。

### 17.1 Phase 1：MVP 闭环（预计 4-6 周）

**目标**：跑通「Dataset→Dataset 转换 + 映射到已有 ObjectType」最小闭环，验证 IR + Schema 引擎 + Kestra+DuckDB 集成的核心架构。

**交付物**：
1. Pipeline IR 数据模型（ORM + pydantic Schema）
2. SchemaInferenceEngine（核心算子推演）
3. KestraEngine（IR→Flow 翻译 + REST 客户端 + Task 路由：转换→DuckDB，搬运→SeaTunnel，联邦→Trino）
4. DuckDB 嵌入（Kestra plugin-jdbc-duckdb + iceberg extension，spike 验证 REST Catalog 读写）
5. PipelineBuilderService（CRUD + 推演调度 + 部署 + 监控）
6. 核心转换算子（Filter/Select/Rename/TypeCast/Join/Aggregate/Union/Expression/QualityCheck）
7. 输出到 Dataset（Iceberg 新 snapshot + current_snapshot_id 原子切换，通过 DuckDB CTAS/INSERT）
8. 映射到已有 ObjectType（阶段1）
9. 手动触发 + 定时调度
10. 执行状态监控
11. 逻辑回滚 + 数据回滚
12. docker-compose 新增 Kestra 服务 + Kestra Worker 镜像含 DuckDB
13. REST API（`/pipelines`）
14. 前端画布 MVP（React Flow，拖拽+连线+配置+校验提示）

**验收标准**：
- 能在画布上拖拽搭建一条「Source(Filter→Join→Aggregate→Sink)」管道
- Schema 推演实时校验，错误标红
- 部署后定时触发，DuckDB 写入 Iceberg 新 snapshot
- 数据回滚秒级完成
- 映射到 ObjectType，本体查询能看到新数据

### 17.2 Phase 2：生产化（预计 6-8 周）

**目标**：补齐生产级能力，支持复杂场景与治理。

**交付物**：
- 上游依赖触发（Dataset 版本事件）
- CDC 合并模式（管道级 MERGE INTO）
- Job Group 分组（多输出）
- 分支评审 + 变更影响分析
- 实时数据预览（采样执行）
- 字段级血缘自动生成（对接全局血缘体系）
- 字段级脱敏 / 行级权限（对接 ADR-016）
- 全局执行优化（路径裁剪 / 公共子表达式消除）
- Custom Function（可复用逻辑封装）
- 数据质量参照完整性
- 分区级重试
- 灰度发布

### 17.3 Phase 3：智能化与多引擎（预计 8-12 周）

**目标**：AI 原生管道 + 多引擎支持 + 资产化。

**交付物**：
- AI/LLM 节点（Use LLM 文本处理 + Trained Model 推理）
- 新建 ObjectType（阶段2，AI 辅助建议）
- Streaming Pipeline（引入 Flink）
- Faster Pipeline（引入 DuckDB via Kestra）
- 代码双向导出（IR→Python/SQL 代码 + 反向导入）
- Marketplace 分发 / 多租户
- 成本治理与资源优化
- 全角色协作（零代码→低代码→专业代码完整梯度）

### 17.4 不在路线图中的（远期备忘）

- 自研 Kestra 插件（io.kestra.plugin.gaia.*，当原生插件不够用时）
- 引入 dbt（作为 Kestra Flow 内的 SQL 转换 Task 类型之一，用户提及的远期选项）
- 实时流管道的状态重放规则（依赖 Flink Checkpoint 深度集成）
- 多租户四层隔离（资产/数据/计算/安全，依赖 ADR-016 完整落地）

---

## 18. 待评审问题

> 以下问题需要评审决策，影响最终设计。

### 18.1 Kestra 部署形态
- **问题**：MVP 用 standalone docker-compose，生产切 Kubernetes Helm？
- **建议**：是。MVP 用 standalone 快速验证，生产用 Helm 保证高可用。
- **影响**：docker-compose.yml 配置、生产部署文档

### 18.2 Kestra 与 SeaTunnel/Trino/DuckDB 的网络拓扑
- **问题**：Kestra 如何连接三个执行引擎？同 docker network？
- **建议**：同一 docker network。SeaTunnel 走 HTTP（`http://seatunnel-master:5801`，`io.kestra.plugin.core.http.Request`）；Trino 走 JDBC（`jdbc:trino://trino:8080`，`io.kestra.plugin.jdbc.trino.Query`，只读）；DuckDB 嵌入式（Kestra Worker 进程内，`io.kestra.plugin.jdbc.duckdb.Query`，无网络）。
- **影响**：docker-compose network 配置、Kestra Task 翻译规则

### 18.3 DuckDB 与 Iceberg REST Catalog 的集成（spike 验证）
- **问题**：DuckDB iceberg extension 读 Gravitino 9001 REST Catalog 是否成熟？写 Iceberg 表是否完整支持？
- **建议**：Phase 1 开始前必须 spike 验证。备选方案：DuckDB 写 Parquet 后由 IcebergStore 注册为 snapshot（走 pyiceberg）。这是 MVP 最大技术风险点。
- **影响**：KestraEngine 翻译规则、DuckDBEngine 适配层设计、可能需要 IcebergStore 提供「注册 Parquet 为 snapshot」能力

### 18.4 管道写入 Iceberg 的并发控制
- **问题**：多管道同时写同一 Dataset 如何避免冲突？
- **建议**：Dataset 级写锁（PG advisory lock 或 DatasetGovernanceModel 加 `write_lock` 字段），同一 Dataset 同时只允许一个管道写入。
- **影响**：PipelineBuilderService 部署逻辑、并发场景测试

### 18.5 前端画布技术选型
- **问题**：React Flow vs Cytoscape（ADR-015 图探索用 Cytoscape）？
- **建议**：React Flow。理由：管道画布是 DAG 编辑器（节点拖拽+连线+配置面板），React Flow 更适合；Cytoscape 偏图可视化（节点多时的布局算法）。两者在前端共存，不复用同一组件。
- **影响**：前端架构、组件复用策略

### 18.6 中间数据传递方案
- **问题**：MVP 用 CTE 串联（方案 A）还是临时表（方案 B）？
- **建议**：MVP 用方案 A（CTE 串联），性能更好；Phase 2 长链路再考虑方案 B。
- **影响**：KestraEngine 翻译规则、DuckDB SQL 生成逻辑

### 18.7 Kestra UI 的分层透出策略（路线 C）
- **问题**：Gaia UI 能否完全覆盖 Kestra 所有能力？算子可扩展性如何处理？
- **决策**：**路线 C —— 分层覆盖**。Gaia UI 只做独有价值层（IR + Schema 推演 + 画布 + 核心算子 + Ontology 绑定），Kestra 的通用编排能力（执行详情/日志/metrics/高级算子/Dashboard）用 iframe 透出给高级用户。
- **核心原则**：
  - **核心算子自研**（~10 个，固定集合）：Filter/Select/Rename/TypeCast/Join/Aggregate/Union/Expression/QualityCheck —— Gaia UI 表单配置 + Schema 推演 + 翻译为 DuckDB SQL，覆盖 80% 场景（二八原则）
  - **扩展算子透传**：1700+ Kestra 插件作为 IR 的 `GenericKestraTask` 节点，Gaia UI 只提供"插入 Kestra Task"入口 + iframe 跳转 Kestra No-Code 编辑器配置，Schema 用户声明（不做自动推演），翻译时原样透传给 Kestra Flow。**不背 1700 插件的映射包袱**
  - **执行监控透出**：Gaia UI 只显示轻量执行状态（成功/失败/耗时），详情（TaskRun 级日志/metrics/拓扑）iframe 透出 Kestra 执行详情页
  - **高级配置透出**：调度/触发器高级配置、Dashboard 监控大盘，iframe 透出 Kestra
- **需解决的工程问题**：
  - SSO/认证打通：Gaia 登录后免登 Kestra（共享 token 或 service account）
  - URL 定位：Gaia 知道管道对应的 Kestra flowId/executionId，构造 iframe URL
  - 视觉割裂：iframe 内外风格不一致，接受（因为是"高级模式"）
- **不做的事**：
  - ❌ 不自研执行监控 UI（TaskRun 级日志/metrics，iframe 透出 Kestra）
  - ❌ 不逐个映射 1700+ Kestra 插件为 Gaia 算子（死路，永远追不上迭代）
  - ❌ 不自研调度/触发器高级配置 UI（iframe 透出 Kestra）
  - ❌ 不自研 Dashboard 监控大盘（iframe 透出 Kestra）
- **影响**：前端架构（iframe 集成层）、认证打通、算子分层策略（见 §7.0）

---

## 附录 A：术语对照表

| 术语 | Palantir | Gaia | 说明 |
|------|----------|------|------|
| 管道 | Pipeline | Pipeline | 同 |
| 管道类型 | Batch/Streaming/Faster | MVP 只 Batch | 流/极速延后 |
| 中间表示 | Pipeline IR | Pipeline IR | 自研，引擎无关 |
| Schema 引擎 | Schema Computation Engine | SchemaInferenceEngine | 自研，Kestra 无此能力 |
| 执行引擎 | Spark/Flink/DataFusion | Kestra编排+DuckDB转换+SeaTunnel搬运+Trino只读联邦 | 复用开源四引擎分工 |
| 转换引擎 | Spark/Flink/DataFusion | DuckDB（MVP，对应 Faster） | 嵌入式，GB~十GB 级 |
| 版本化数据集 | Versioned Dataset | DatasetGovernance + Iceberg snapshot | 复用+激活 |
| 本体输出 | Ontology Object/Link | 映射到 ObjectType（阶段1）/新建（阶段2） | 路线 A 分两步 |
| 作业分组 | Job Group | MVP 不做 | Phase 2 |
| 全局参数 | Pipeline Parameters | Phase 2 | MVP 不做 |
| 自定义函数 | Custom Function | Phase 2 | MVP 不做 |
| 代码双向互通 | Code Export/Import | Phase 3 | MVP 不做 |
| 血缘 | Lineage | Phase 1 预留，Phase 2 实现 | 字段级 |
| 多租户 | Multi-tenant | Phase 3+ | MVP 不做 |

## 附录 B：参考文档

- [ADR-018: Pipeline Builder 数据管道编排](../architecture/adr-018-pipeline-builder.md)
- [ADR-014: 多源异构数据融合连接器体系](../architecture/adr-014-multi-source-data-fusion-connectors.md)
- [ADR-008: Iceberg→Doris 索引同步路径](../architecture/adr-008-iceberg-doris-sync-path.md)
- [ADR-001: Doris 作在线读主源](../architecture/adr-001-doris-as-online-read-source.md)
- [ADR-015: AG-UI Agent 驱动图探索画布](../architecture/adr-015-agent-driven-graph-explore.md)（前端画布范式参考）
- [ADR-016: 权限治理体系](../architecture/adr-016-permission-governance.md)
- [dataset-ontology-binding.md](dataset-ontology-binding.md)
- [data-flow-diagrams.md](data-flow-diagrams.md)
- [implementation-status.md §14.1 数据血缘与来源追踪](../architecture/implementation-status.md)
- Apache Kestra 官方文档：https://kestra.io/docs
- Palantir Foundry Pipeline Builder（对标参考）

---

**文档结束**

> 本文档为设计阶段产物，未开始实现。评审通过后，将按 §14 分期路线推进，每阶段交付前更新 implementation-status.md 对应章节。
