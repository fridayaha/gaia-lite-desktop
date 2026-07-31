# Gaia Action 架构设计 — 评估与实现方案

> **版本**：v1.0  
> **对标系统**：Palantir Foundry Action (OSv2)  
> **核心目标**：在 Gaia 开源分层架构上，实现从"数据洞察到业务执行"（Closing the Loop）的闭环写回能力

---

## 目录

- [第一部分：Palantir Action 架构与技术原理（参考）](#第一部分palantir-action-架构与技术原理参考)
- [第二部分：Gaia 当前实现评估](#第二部分gaia-当前实现评估)
- [第三部分：分层实现方案](#第三部分分层实现方案)
- [第四部分：优先级路线图](#第四部分优先级路线图)

---

# 第一部分：Palantir Action 架构与技术原理（参考）

> 以下内容基于 Palantir Foundry Action (OSv2) 的公开架构资料整理。

## 1. 核心概念

### 1.1 Ontology（本体）

Foundry 的核心业务数据模型。它将底层海量的表格数据映射为业务人员可理解的 **Objects（对象）**（如"飞机"、"客户"、"工单"）和 **Links（关系）**（如"客户-购买-产品"）。

### 1.2 Object Storage v2 (OSv2)

支撑当前新版 Ontology 的全新分布式、事务型分布式键值/文档数据库。它是对早期 Phonograph (OSv1) 的彻底重构。OSv2 解决了 Phonograph 在海量对象、复杂高频关系（Links）更新时的性能瓶颈，其底层采用日志结构合并树（LSM-Tree）变种与优化的 Raft 共识协议，专为低时延、强一致性的行级读写和高并发事务设计。

### 1.3 Action Type（动作类型）

定义了如何修改本体。它包含：

- **Parameters（参数）**：用户输入或系统传入的变量。
- **Rules/Logic（规则/逻辑）**：验证参数的合法性，以及决定如何修改对象（如：创建对象、更新属性、删除对象、修改关系）。
- **Functions on Objects (FoO)**：当内置的规则不够用时，可以使用 TypeScript 编写复杂的业务逻辑函数来处理数据并返回修改意图（Mutations）。
- **Side Effects（副作用）**：修改数据之外的操作，例如发送邮件、触发 Webhook 调用外部 ERP 系统等。

## 2. OSv2 时代的全新技术架构

在 OSv2 架构下，Palantir 摒弃了早期过于依赖单一大型搜索引擎索引作主存储的作法，转而采用 **存储与索引分离（Storage-Index Separation）** 的现代化云原生数据库架构。

```
                                 +---------------------------------------+
                                 |          Frontend / Client            |
                                 +-------------------+-------------------+
                                                     | Action Request
                                                     v
                                 +-------------------+-------------------+
                                 |            Action Service             |
                                 |      (Validation & Rule Engine)       |
                                 +---+-------------------------------+---+
                                     |                               |
                   (If TypeScript)   |                               | (If Declarative Rules)
                                     v                               v
                       +-------------+-------------+                 |
                       | Functions-on-Objects JVM  |                 |
                       |    (Isolated Sandbox)     |                 |
                       +-------------+-------------+                 |
                                     |                               |
                                     +---------------+---------------+
                                                     | Computed Mutations (JSON Patch)
                                                     v
+----------------------------------------------------+----------------------------------------------------+
|                                    Object Storage v2 (OSv2)                                     |
|                                                                                                 |
|   +-----------------------------------------------------------------------------------------+   |
|   |                           Ontology Transaction Coordinator                              |   |
|   |                       (2-Phase Commit & Lock-free MVCC Engine)                          |   |
|   +------------------------------------+------------------------------------+---------------+   |
|                                        |                                        |               |
|                                        v Commit Log                             v Branch State  |
|   +------------------------------------+------------------------------------+---------------+   |
|   |                            OSv2 Storage Node Clustered (Raft)                           |   |
|   |         - Versioned Log Store (RocksDB / S3 Append)                                     |   |
|   |         - LSM-based Key-Value for Object Properties & Link Pointers                     |   |
|   +-----------------------------------------------------------------------------------------+   |
+----------------------------------------+------------------------------------+-------------------+
                                         |                                    |
                    Event Stream (Kafka) |               Reliable CDC         | Outbox Event
                                         v                                    v
                       +-----------------+-----------------+        +---------+---------+
                       |          Search Index             |        |  Outbox Executor  |
                       |  (Elasticsearch / OpenSearch)     |        | (Webhook Gateway) |
                       +-----------------+-----------------+        +---------+---------+
                                         |                                    |
                                         v CDC Batch Ingestion                v
                       +-----------------+-----------------+        +---------+---------+
                       |           Data Lake               |        |   External Source |
                       |    (Apache Iceberg / Parquet)     |        |    (SAP, Oracle)  |
                       +-----------------------------------+        +-------------------+
```

### 2.1 OSv2 底层核心技术组件

| 组件 | 职责 | 技术实现 |
|------|------|----------|
| **Transaction Coordinator** | 多对象、多关系更新的跨分片事务 | 非阻塞 MVCC 引擎 + 优化 2PC |
| **Raft-based Storage Group** | 分片管理，强一致性读写 | 每个分片由一个 Raft 共识组管理 |
| **Storage Engine** | 存储对象属性的 Versioned Key-Value | 定制 RocksDB / S3-EBS 混合存储 |
| **Decoupled Search Index** | 异步消费提交日志构建搜索 | 不再是事务路径的"同步参与者" |

## 3. 事务控制与 MVCC 原理

### 3.1 混合逻辑时钟 (Hybrid Logical Clocks)

OSv2 采用 HLC 为每个事务分配全局单调递增的事务时间戳（$T_{tx}$）。所有对象修改不是直接覆盖原有记录，而是作为新版本数据插入存储引擎，Key 格式为：`{Object_ID}::{Property_ID}::{Timestamp}`。

### 3.2 读写隔离与隔离级别

**Snapshot Isolation（快照隔离）**：
- Action 执行时获取全局读时间戳 $T_{read}$
- 整个计算过程中只读取 $\le T_{read}$ 的数据版本
- 即使其他并发 Action 正在修改相同数据，当前 Action 读到的也是完全一致的静态快照

**Atomic Write-sets（原子写集）**：
- 所有修改意图打包为一个 Write-set
- Transaction Coordinator 负责将 Write-set 写入相关 Raft 存储组

## 4. 冲突解决机制

### 4.1 属性级冲突检测 (Property-level OCC)

传统 OCC 是"行级"的：如果用户 A 改"状态"、B 改"备注"，后提交者被拒绝。OSv2 升级为精确到"属性"和"关系"级别的冲突检测：

**写集（Write-set）与读集（Read-set）**：
- Action 开始计算时，OSv2 记录读取了哪些对象的哪些属性版本（Read-set），以及计划修改哪些属性（Write-set）
- 提交前 Coordinator 检查每个属性的版本号是否变化

**正交并发（Orthogonal Execution）**：
- 如果并发事务修改的是不同属性（如 A 改 status、B 改 notes），只要 Read-set/Write-set 无交集，两者同时提交成功

### 4.2 冲突解决策略

| 策略 | 行为 | 适用场景 |
|------|------|----------|
| **Fail-fast（快速失败）** | 事务 Rollback，抛出 ConflictException | 默认策略 |
| **Auto-Merge（自动合并）** | 原子增量操作（如库存 -1），自动合并 | 数值累加类型操作 |
| **Function-level Retry** | 获取最新值重新运行 FoO，最多重试 3 次 | TypeScript 复杂逻辑 |

## 5. 副作用与同步

### 5.1 事务外箱模式（Transactional Outbox）

OSv2 中副作用与主数据的写入是原子绑定的：

```
[ OSv2 Client ]
       |
       | 1. Commit Action Transaction (Atomic Package)
       v
+-----------------------------------------------------------------------+
|  [ OSv2 Storage Node ]                                                |
|                                                                       |
|  +---------------------------+        +----------------------------+  |
|  |    Active Object Table    |        |        Outbox Table        |  |
|  |  (Property & Link Stores) |        |  (Serialized Side Effects) |  |
|  +-------------+-------------+        +-------------+--------------+  |
|                |                                    |                 |
|                +-----------------+------------------+                 |
|                                  |                                    |
|                                  v 2. Atomic Commit (Raft Consensus)  |
|                            [ Raft Log ]                               |
+----------------------------------+------------------------------------+
                                   |
                                   | 3. Asynchronous Streaming (CDC)
                                   v
                        +--------------------+
                        |  Outbox Executor   |
                        +----------+---------+
                                   |
                       4. Execute  | (At-Least-Once retry with Backoff)
                       Webhook     v
                        +--------------------+
                        | External ERP (SAP) |
                        +--------------------+
```

**关键设计**：
- Active Object Table 和 Outbox Table 在同一 RocksDB 实例中（**同库本地事务**）
- 两部分变更作为同一个本地事务写入 Raft Log
- 对象改成功 ⇒ Outbox 中必有对应待执行任务
- 对象修改失败 ⇒ Outbox 中绝对不会有任务

**幂等与重试**：
- Webhook 调用带全局唯一 `action_id` 作为幂等键
- 失败时指数退避自动重试（Exponential Backoff with Jitter）
- 达到最大重试上限进入死信队列（DLQ）并报警

### 5.2 归档数据湖：从高频 KV 到列式 Parquet/Iceberg

OSv2 的变更日志通过 CDC 管道实时流转到数据湖：

1. **CDC 捕获**：Raft 变更日志被管道实时捕获并汇聚到分布式消息队列
2. **微批合并**：Lake Synchronizer 每 1-5 分钟从队列拉取数据，在内存中做微批内去重与合并。例如 Object_A 被修改 10 次，只保留最新状态
3. **Iceberg 写入**：合并后的数据以 Parquet 格式写入，原子性地更新 Iceberg 清单文件

## 6. 数据源回写机制（Write-back）

### 6.1 路径 A：基于 Webhook 的实时 API 回写（SaaS / ERP）

- 在 Data Connection 中定义 Webhook，绑定运行在客户网络内的 Connection Agent
- Action 提交 → OSv2 事务落盘（含 Outbox 记录）→ Outbox Executor → 云端路由 → 本地 Agent → 外部 API
- **两阶段确认（2-Phase Acknowledgment）**：Agent 确认外部 API 成功后，Outbox 任务才算完成

### 6.2 路径 B：基于导出管道的直接数据库回写（RDBMS）

- 管理员将某些属性设为"可写回（Write-back-enabled）"
- OSv2 导出管道实时捕获变更，生成优化的 SQL（INSERT/UPDATE/MERGE）
- SQL 通过安全 TLS 通道下发给内网 Agent 执行

### 6.3 反馈环路防御（Feedback Loop Prevention）

写回 → 原始数据库变更 → Ingestion 再次拉回 → 无限循环。OSv2 通过三种机制组合解决：

1. **事务标记追踪法**：写回时注入 `foundry_sync_tx` 和 `foundry_sync_user` 元数据字段
2. **增量拉取过滤**：Ingestion SQL 自动重写，过滤掉自身回写的数据
3. **多版本快照比对**：比对源系统 Hash 与 OSv2 中的 Applied-Snapshot-Hash，一致则静默丢弃

---

# 第二部分：Gaia 当前实现评估

## 0. Gaia 现有架构概述

### 0.1 项目定位

Gaia 是一个**开源 Palantir Foundry 风格的分层数据架构**，通过目录隔离 + 明确的组件边界实现层间解耦，以轻量级方式复现 Foundry Ontology 的核心能力。

- **代号**：Gaia（盖亚）— 数据之母
- **Python 包名**：`ontology`（位于 `src/ontology/`）
- **技术栈**：Python 3.12+ / FastAPI
- **包管理**：uv
- **测试**：pytest + pytest-asyncio（TDD：先写测试，后实现）
- **代码风格**：ruff（格式 + lint 一体）
- **类型检查**：mypy --strict

### 0.2 5+1 分层架构

```
Routes（HTTP 薄层）        /ontologies  /objects/load  /actions  /metrics
    ↓ 依赖注入
Services（业务编排层）
    ↓ 构造函数注入
Layer Implementations（层实现，可并行替换）
 Catalog │ Metadata │ Dataset │ Index │ Pipeline │ Engine
    ↓
Core Models（领域模型）
    ├── models/    SQLAlchemy 2.0 ORM（表映射）
    └── schemas/   pydantic v2（API 校验/序列化）
```

### 0.3 各层职责与组件

| 层 | 职责 | 数据库/服务 | ICD |
|----|------|------------|-----|
| **Routes** | FastAPI 路由定义，参数校验 | — | — |
| **Services** | 业务编排，跨层协调 | — | — |
| **Catalog** | 物理资产注册、View、RBAC | Gravitino 1.3.0 | ICD-02 |
| **Metadata** | 业务本体元数据 | PostgreSQL 16 | ICD-01 |
| **Dataset** | 全量明细 + 历史快照 | Iceberg 1.11.0 (RustFS/S3) | ICD-03 |
| **Index** | 索引加速 | Doris 4.0.5 | ICD-04 |
| **Pipeline** | 数据采集、写入、同步 | SeaTunnel 2.3.13 | — |
| **Engine** | 联邦查询、View 执行 | Trino 478 | ICD-05 |

### 0.4 当前服务拓扑

```
┌──────────────────────────────────────────────────────────────────┐
│                        Docker Compose (9 服务)                    │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────────────┐ │
│  │ PostgreSQL│  │ RustFS   │  │   Gravitino                   │ │
│  │ :5432    │  │ :9000    │  │   :8090 (元数据) + :9001 (Iceberg)│ │
│  └────┬─────┘  └────┬─────┘  └───────────────┬───────────────┘ │
│       │              │              │                  │          │
│       └──────────────┴──────────────┴──────────────────┘          │
│                              │                                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ Doris FE │  │ Doris BE │  │  Trino   │  │   SeaTunnel      │ │
│  │ :9030    │  │ :9050    │  │ :8080    │  │   :8081          │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              API (FastAPI :8000)                         │   │
│  │  /ontologies  /objects/load  /objects/aggregate         │   │
│  │  /actions/{type}/{action}  /time-travel  /metrics       │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### 0.5 四种核心数据流场景

| 场景 | 路径 | 关键组件 |
|------|------|----------|
| **新数据接入** | 源端 → SeaTunnel 主流水线 → Iceberg → Gravitino 注册 → ObjectIndexFunnel 同步 → Doris | SeaTunnel（源→Iceberg 搬运）+ Iceberg + Gravitino + ObjectIndexFunnel（Iceberg→Doris） |
| **物理对象查询** | 客户端 → ObjectQueryService → Doris 索引过滤 → IcebergStore.load_by_ids() → 返回 | Doris + Iceberg |
| **虚拟对象查询** | 客户端 → ObjectQueryService → Trino 执行 Gravitino View | Trino + Gravitino |
| **时间旅行** | 客户端 → TimeTravelService → Trino FOR VERSION AS OF | Trino + Iceberg |

### 0.6 现有 Service 层

| Service | 职责 | 注入依赖 |
|---------|------|----------|
| **OntologyService** | 本体/对象类型/属性/关系 CRUD | Metadata + Catalog + Index |
| **ObjectQueryService** | 物理/虚拟对象查询 | Metadata + Catalog + Index + Dataset + Engine |
| ~~VirtualTableService~~ | ~~虚拟表查询~~ 已删除 | ~~Catalog + Engine~~ |
| **TimeTravelService** | 时间旅行查询 | Catalog + Engine |
| **ActionService** | ⚠️ 极简写操作 | Metadata + Catalog + Dataset |

### 0.7 架构红线

Gaia 项目有明确的架构约束，Action 实现必须遵守：

| # | 红线 | 含义 |
|---|------|------|
| 1 | Gravitino **仅**管理物理数据资产 | 不存业务本体元数据 |
| 2 | PostgreSQL **仅**存业务本体元数据 | 不存物理表元数据 |
| 3 | Iceberg 是主数据**唯一写入入口** | 所有写操作最终必须写入 Iceberg |
| 4 | Doris **严格**作为索引加速层 | 不存全量明细、大字段、二进制 |
| 5 | Trino 是主要查询引擎 | 通过 Gravitino Connector 联邦查询 |
| 6 | SeaTunnel 承担 PipelineBuilder 核心能力 | 不做元数据管理或查询路由 |
| 7 | **无 Redis** | 用 Doris 缓存 + Iceberg ACID + 分区策略替代 |

### 0.8 当前实现状态总览

| 维度 | 状态 | 代码量 |
|------|------|--------|
| 核心 ORM + pydantic 模型 | ✅ 完成 | 约 350 行 |
| 6 个 Layer 实现 | ✅ 完成 | 约 600 行 |
| 5 个 Service 编排 | ✅ 完成（ActionService 极简） | 约 400 行 |
| DI + Routes + 可观测性 | ✅ 完成 | 约 200 行 |
| Docker Compose 9 服务 | ✅ 完成 | 配置 + Dockerfile |
| 单元测试（109 个） | ✅ 完成 | 约 2000 行 |
| 系统测试（12 个） | ✅ 完成 | 约 500 行 |
| **Action 完整生命周期** | ❌ **待实现（本文档）** | — |
| 规则引擎 | ❌ 未实现 | — |
| Outbox / 副作用 | ❌ 未实现 | — |
| Write-back | ❌ 未实现 | — |

### 0.9 关键编码规范（影响 Action 实现）

| # | 规范 | Action 实现中的体现 |
|---|------|-------------------|
| 1 | SQLAlchemy 2.0 async ORM | Outbox 和 ExecutionLog 使用 ORM 而非裸 SQL |
| 2 | pydantic v2 API 校验 | ActionTypeCreate、ActionExecutionRequest 使用 pydantic |
| 3 | 类型注解全覆盖 | 所有 Service 方法带 mypy --strict 兼容注解 |
| 4 | `datetime.now(UTC)` | 时间戳使用 UTC |
| 5 | Palantir RID 主键 | object_state/object_links 使用 RID 格式主键 `ri.ontology.main.object.{uuid}`（String(128)）；元数据表仍用裸 UUID |
| 6 | `async` 全链路 | OutboxExecutor.run_forever() 异步轮询 |
| 7 | Repository 模式 | 每个 Layer 类封装对单一组件的全部操作 |
| 8 | ORM 与 Schema 分离 | models/ 放 ORM，schemas/ 放 pydantic |

## 1. 现有 Action 代码结构

```
src/ontology/
├── core/
│   ├── models/ontology.py          # ActionTypeModel (ORM)
│   └── schemas/ontology.py         # ActionType (pydantic)
├── services/
│   ├── action_service.py           # ActionService（极简）
│   └── ontology_service.py         # define_action_type()
├── routes/action/__init__.py       # POST /actions/{object_type}/{action}
└── config/container.py             # DI 注入
```

## 2. 差距分析矩阵

### 2.1 已有但需补强

| 组件 | 位置 | 当前状态 | 问题 |
|------|------|----------|------|
| `ActionTypeModel` | `models/ontology.py` | ✅ 已定义 | 字段齐全（parameters/rules/submission_criteria 均为 JSONB），但缺 `ActionTypeCreate` schema |
| `ActionType` schema | `schemas/ontology.py` | ✅ 已定义 | 只读模型，缺少 `ActionTypeParameter`、`ActionRule` 等子模型 |
| `OntologyService.define_action_type()` | `services/ontology_service.py` | ✅ 已实现 | 创建入口，但参数校验规则定义不完整 |
| `POST /actions/{object_type}/{action}` | `routes/action/__init__.py` | ✅ 已定义 | 路由存在，payload 直接透传无校验 |

### 2.2 完全缺失

| 组件 | Palantir 对标 | 实现位置 | 优先级 |
|------|---------------|----------|--------|
| 参数校验引擎 | ActionType.parameters 校验 | `ActionService` 新增 | **P0** |
| 声明式规则引擎 | Rules/Logic 评估 | `ActionService` 新增 | **P1** |
| Transactional Outbox | Outbox Table + 同库事务 | 新增 ORM + `PostgresMetaStore` | **P0** |
| Outbox Executor | CDC 消费 + Webhook 发送 | 新增服务 | **P1** |
| 属性级冲突检测 | Property-level OCC | `ActionService` 新增 | **P2** |
| Write-back 机制 | Webhook + JDBC Export | 新增服务 | **P3** |
| 反馈环路防御 | 事务标记 + 过滤 | 增量导入层新增 | **P3** |
| Functions on Objects | TypeScript 沙箱 | 待定（未来阶段） | **P4** |
| SeaTunnel CDC (PG → Iceberg) | 事件流摄取 | `SeaTunnelEngine` 新增 | **P1** |

### 2.3 现有可复用基础设施

| 组件 | 用途 | 备注 |
|------|------|------|
| **PostgreSQL** (ACID) | Outbox 同库事务、元数据持久化 | 已就绪 |
| **Iceberg** (ACID) | 主数据持久化、快照隔离 | 已就绪 |
| **SeaTunnel** | CDC 管道、索引同步 | 已就绪，需验证 PG CDC 能力 |
| **Gravitino** | 权限校验、物理表路由 | 已就绪 |
| **Doris** | 索引加速（异步同步） | 已就绪 |

## 3. 架构红线检查

对照 `CLAUDE.md` 中的红线，Action 实现方案：

| 红线 | 是否违反 | 说明 |
|------|----------|------|
| Iceberg 是唯一写入入口 | ✅ 不违反 | 数据最终写入 Iceberg |
| Doris 不存全量明细 | ✅ 不违反 | Doris 仅加速索引 |
| PostgreSQL 不存物理表元数据 | ✅ 不违反 | Outbox 是临时任务队列，非物理表元数据 |
| 无 Redis | ✅ 不违反 | Outbox 使用 PostgreSQL |
| Gravitino 不存业务本体元数据 | ✅ 不违反 | Outbox 效果配置存于 PG 非 Gravitino |

---

# 第三部分：分层实现方案

## 1. 核心架构原则

> **⚠️ 2026-07-08 重大演进：object_state 同步去 SeaTunnel 化**。本节原始架构图描述的"SeaTunnel CDC (PG WAL 双路分流 → Iceberg / Kafka→Doris)" 已**被 outbox 驱动方案取代**：object_state 变更经 outbox INDEX effect → OutboxExecutor ≤1s → Doris upsert；经 outbox ARCHIVE effect → SyncFlushScheduler ≤5min → IcebergStore.merge。SeaTunnel 退回外部数据源接入职责（ADR-014）。当前真实架构以 [action-loop-design.md](./action-loop-design.md) §四.4 + [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) 为准。下方原始架构图保留作为历史记录。

```
用户请求 → ActionService (规则+校验+行级OCC)
  → PostgreSQL 事务（object_state + execution_log + outbox[INDEX|ARCHIVE|WEBHOOK|...] 原子提交）
  → 返回 "applied"（毫秒级，数据已生效，支持 read-your-writes）
  → OutboxExecutor 消费 INDEX outbox → Doris upsert/delete（近实时索引，≤1s）
  → SyncFlushScheduler 消费 ARCHIVE outbox → IcebergStore.merge（主数据归档，≤5min 微批）
  → OutboxExecutor 消费 WEBHOOK/WRITE_BACK outbox → Webhook / Write-back
```

<details><summary>历史架构图（SeaTunnel CDC，已被 outbox 驱动取代）</summary>

```
用户请求 → ActionService (规则+校验+行级OCC)
  → PostgreSQL 事务（object_state + execution_log + outbox 原子提交）
  → 返回 "applied"（毫秒级，数据已生效，支持 read-your-writes）
  → SeaTunnel CDC (PG WAL 双路分流)              ← 已废弃删除
      ├→ Iceberg（主数据持久化，异步批次合并）
      ├→ Kafka → Doris（实时索引同步，异步 3-5s）
      └→ Doris 加速层按对象类型物理隔离
  → Outbox Executor → Webhook / Write-back
```

</details>

### 1.1 设计决策

| 决策 | 选择 | 替代方案 |
|------|------|----------|
| Outbox 存储 | PostgreSQL 同库事务（利用现有 session） | Kafka / Redis |
| 运营写入路径 | PostgreSQL `object_state` 同库事务（同步写入，行级 version OCC） | Kafka / 直接写 Iceberg |
| 规则引擎 | `simpleeval` 安全表达式求值 | Rust/WASM 沙箱 / QuickJS |
| 冲突检测 | **PG 行级 OCC 为主**：`UPDATE ... WHERE version = :expected`，`affected_rows=0` 即冲突，提交时立即检测；Iceberg snapshot diff 为事后审计 | Doris 版本号 / Trino 查询 |
| CDC 管道 | ~~SeaTunnel PG-CDC 双路分流：→ Iceberg（主数据持久化）+ → Kafka → Doris（实时索引加速）~~ **已废弃（2026-07-08）**。改 outbox 驱动：INDEX effect→Doris(≤1s) + ARCHIVE effect→Iceberg(≤5min MERGE)。SeaTunnel 保留外部数据源接入(ADR-014) | Debezium + Kafka Connect + Flink / 原 SeaTunnel CDC |
| 幂等性 | `idempotency_key`（唯一约束）+ `object_state.version` 自增防重放 | 窗口去重 |

### 1.2 最终架构图

> **⚠️ 下图描绘的 SeaTunnel CDC 双路分流（PG→Iceberg / PG→Kafka→Doris）已于 2026-07-08 被 outbox 驱动方案取代。** 保留作为历史设计参考。当前架构见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

<details><summary>历史架构图（SeaTunnel CDC，已被 outbox 驱动取代）</summary>

```
                    ┌─────────────────────────────────────┐
                    │           Client (REST)              │
                    │  POST /actions/{object_type}/{action} │
                    └───────────────┬─────────────────────┘
                                    │ payload + action_id (幂等键)
                                    v
              ┌─────────────────────┼─────────────────────┐
              │         ActionService                       │
              │                                            │
              │  ┌─────────────────────────────────────┐   │
              │  │  1. RepeatReqFilter (幂等去重)       │   │
              │  └──────────────┬──────────────────────┘   │
              │                 v                          │
              │  ┌─────────────────────────────────────┐   │
              │  │  2. ParameterValidator.validate()    │   │
              │  │     → 校验参数类型/必填/约束          │   │
              │  └──────────────┬──────────────────────┘   │
              │                 v                          │
              │  ┌─────────────────────────────────────┐   │
              │  │  3. ActionRuleEngine.evaluate()      │   │
              │  │     → 派生值计算 + 约束验证          │   │
              │  └──────────────┬──────────────────────┘   │
              │                 v                          │
              │  ┌─────────────────────────────────────┐   │
              │  │  4. MutationBuilder.build()          │   │
              │  │     → 生成修改意图 (create/update/   │   │
              │  │        delete/relate/unrelate)       │   │
              │  └──────────────┬──────────────────────┘   │
              │                 v                          │
              │  ┌─────────────────────────────────────┐   │
              │  │  5. ConflictDetector.check()         │   │
              │  │     → snapshot 版本比对              │   │
              │  └──────────────┬──────────────────────┘   │
              │                 v (无冲突或已解决)          │
              │  ┌─────────────────────────────────────┐   │
              │  │  6. PostgreSQL 事务 (同一 session):   │   │
              │  │     ├─ object_state (数据变更)        │   │
              │  │     │   UPSERT with version OCC       │   │
              │  │     ├─ ActionExecutionLog (审计)      │   │
              │  │     └─ Outbox (副作用待执行)          │   │
              │  └──────────────┬──────────────────────┘   │
              └─────────────────┼─────────────────────────┘
                                │ await session.commit()
                                v
            ┌───────────────────────────────────────────┼───────────────────────┐
            │                   │                       │                        │
            v                   v                       v                        v
   ┌──────────────────────────────────┐  ┌────────────────┐  ┌──────────────────────┐
   │        SeaTunnel CDC (统一引擎)   │  │ OutboxExecutor │  │ Gravitino (RBAC)     │
   │  ┌──────────┐  ┌──────────────┐  │  │ (异步消费)      │  │                      │
   │  │PG→Iceberg│  │PG→Kafka      │  │  └───┬────────────┘  └──────────────────────┘
   │  │(主数据)   │  │(实时索引同步) │  │      │
   │  └────┬─────┘  └──────┬───────┘  │      ├── Webhook → External SaaS
   └───────┼────────────────┼─────────┘      ├── JDBC Write-back → RDBMS
           │                │                └── 死信队列 (DLQ)
           v                v
   ┌───────────────┐  ┌─────────────────────────┐
   │ IcebergStore  │  │  Kafka (多 Topic 物理隔离) │
   │ (主数据持久化)  │  │ action_order│action_...  │
   └───────┬───────┘  └────────────┬────────────┘
           │                        │
           │  INDEX_SYNC            │ SeaTunnel 阶段二
           │  (批同步，延迟分钟级)    │ Kafka → Doris
           │                        │ (实时同步，延迟 3-5s)
           v                        v
   ┌──────────────────────────────────────────────────┐
   │              DorisIndex (加速层)                   │
   │  ┌──────────┐ ┌──────────┐ ┌──────────┐          │
   │  │  order   │ │customer  │ │ product  │  ...     │
   │  │Unique Key│ │Unique Key│ │Unique Key│          │
   │  └──────────┘ └──────────┘ └──────────┘          │
   └──────────────────────────────────────────────────┘
```

</details>

## 2. 阶段 1：Action Type 定义补全（P0）

### 2.1 新增 Schema 定义

文件：`src/ontology/core/schemas/action.py`（新增独立模块，或并入 ontology.py）

```python
"""pydantic v2 schemas for Action domain — validation/serialization."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from ontology.core.schemas.ontology import DataType


class ActionTypeParameter(BaseModel):
    """Action parameter definition (Palantir ActionType.parameters equivalent)."""
    api_name: str = Field(..., pattern=r"^[a-z][a-zA-Z0-9_]*$")
    display_name: str
    data_type: DataType
    required: bool = True
    default: Any | None = None
    description: str = ""


class ActionRule(BaseModel):
    """Declarative rule for validation or derivation."""
    type: Literal["constraint", "derivation", "validation"]
    target: str          # 目标参数名或属性名
    expression: str      # 安全表达式，如 "value > 0", "unit_price * quantity"
    description: str = ""


class ActionEffectConfig(BaseModel):
    """Side effect configuration for an Action."""
    type: Literal["webhook", "write_back", "pipeline"]
    config: dict[str, Any] = Field(default_factory=dict)


class ActionTypeCreate(BaseModel):
    """Create a new ActionType — business users define the action contract."""
    api_name: str = Field(..., pattern=r"^[a-z][a-zA-Z0-9_]*$")
    display_name: str
    description: str = ""
    affected_object_type_api_name: str           # 用 api_name 而非内部 ID
    parameters: list[ActionTypeParameter] = Field(default_factory=list)
    rules: list[ActionRule] = Field(default_factory=list)
    submission_criteria: dict[str, Any] = Field(default_factory=dict)
    effects: list[ActionEffectConfig] = Field(default_factory=list)


class ActionExecutionRequest(BaseModel):
    """Request payload for executing an action."""
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None           # 客户端提供幂等键


class ActionExecutionResult(BaseModel):
    """Result of an action execution.

    - "applied": mutations committed to object_state (read-your-writes)
    - "conflict": row-level version OCC failed (affected_rows=0), caller should refresh
    - "validation_failed": parameter or rule validation errors
    """
    status: Literal["applied", "conflict", "validation_failed"]
    action_id: str
    affected_objects: dict[str, int] = Field(default_factory=dict)  # rid → new_version
    mutations: list[dict[str, Any]] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    conflict_details: dict[str, Any] | None = None
```

### 2.2 新增 ORM 模型

文件：`src/ontology/core/models/ontology.py`（追加）

```python
class ActionExecutionLogModel(Base):
    """Audit log for action executions."""
    __tablename__ = "action_execution_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    action_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String(32), ForeignKey("ontologies.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    mutations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    error: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[str] = mapped_column(String(255), default="system")
    read_snapshot_id: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxModel(Base):
    """Transactional outbox for side effects (Webhook, Write-back).

    Co-located in PostgreSQL with metadata for atomic commits.
    """
    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    action_execution_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("action_execution_logs.id"), nullable=False, index=True
    )
    effect_type: Mapped[str] = mapped_column(String(50), nullable=False)   # WEBHOOK | WRITE_BACK
    effect_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")     # PENDING | COMPLETED | FAILED | DLQ
    retry_count: Mapped[int] = mapped_column(default=0)
    max_retries: Mapped[int] = mapped_column(default=3)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ObjectStateModel(Base):
    """Operational state for all objects — the synchronous write target for Actions.

    This table is the PG-side mirror of the "object" concept. Every Action's
    mutations are applied here within the same PG transaction as the execution
    log, guaranteeing atomicity and read-your-writes consistency.

    Key design:
    - version column enables row-level OCC: Actions include expected_version,
      and UPSERT fails if version has changed (affected_rows = 0 → conflict).
    - object_type_api_name drives CDC routing (PG → Kafka → Doris per-type tables).
    - properties stores the full object as JSONB for schema flexibility.
    """
    __tablename__ = "object_state"

    rid: Mapped[str] = mapped_column(String(128), primary_key=True)  # Palantir RID: ri.ontology.main.object.{uuid}
    object_type_api_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(default=1)
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ontology_id: Mapped[str] = mapped_column(String(32), ForeignKey("ontologies.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
```

### 2.3 修改 PostgresMetaStore

```python
# 新增方法
async def create_action_type_v2(self, action_type: ActionTypeCreate, ontology_id: str) -> ActionType:
    """Create an ActionType with full parameter/rule/effect definitions."""
    ...

async def get_action_type_with_params(self, ontology_api_name: str, api_name: str) -> ActionType:
    """Get ActionType with fully resolved parameters and rules."""
    ...

async def create_execution_log(self, log: ActionExecutionLog) -> ActionExecutionLog:
    """Record an action execution and return with generated ID."""
    ...

async def create_outbox_record(self, outbox: OutboxRecord) -> OutboxRecord:
    """Insert an outbox record in the same transaction as execution log."""
    ...

async def upsert_object_state(
    self,
    rid: str,
    object_type_api_name: str,
    ontology_id: str,
    properties: dict[str, Any],
    expected_version: int,
) -> int:
    """UPSERT object state with row-level OCC (version check).

    For CREATE (expected_version=0):
        INSERT INTO object_state (rid, ..., version=1)
        ON CONFLICT (rid) DO NOTHING
        Returns 1 on success, 0 if duplicate.

    For UPDATE:
        UPDATE object_state SET properties=..., version=version+1
        WHERE rid=:id AND version=:expected_version
        RETURNING version
        Returns new_version on success, 0 on conflict (affected_rows=0).
    """
    ...

async def delete_object_state(self, rid: str) -> None:
    """Delete an object from operational state."""
    ...

async def get_object_state(self, rid: str) -> dict[str, Any] | None:
    """Read current object state (for read-your-writes point queries)."""
    ...

async def get_object_states_by_type(
    self, object_type_api_name: str, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    """List objects of a given type (with version for client-side OCC)."""
    ...
```

### 2.4 补充 `__init__.py` 导出

文件：`src/ontology/core/schemas/__init__.py`（追加导出）

```python
from ontology.core.schemas.action import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionRule,
    ActionTypeCreate,
    ActionTypeParameter,
)
```

## 3. 阶段 2：参数校验引擎（P0）

### 3.1 实现

文件：`src/ontology/services/action_validator.py`（新增）

```python
"""Action parameter validation engine.

Validates action payload against ActionType parameter definitions.
Supports type checking, required field validation, and constraint rules.
"""

from typing import Any

from ontology.core.exceptions import ValidationError
from ontology.core.schemas.action import ActionTypeParameter


class ParameterValidator:
    """Validate action payload against parameter definitions."""

    def validate(
        self,
        parameters: list[ActionTypeParameter],
        payload: dict[str, Any],
    ) -> None:
        """Validate payload against parameter definitions.

        Args:
            parameters: Action type parameter definitions
            payload: User-supplied parameter values

        Raises:
            ValidationError: If validation fails with details.
        """
        errors: list[str] = []
        param_names = {p.api_name for p in parameters}

        # 1. Check required parameters
        for param in parameters:
            if param.api_name not in payload:
                if param.required:
                    errors.append(f"Missing required parameter: '{param.api_name}'")
                continue

            value = payload[param.api_name]

            # 2. Check for unknown parameters
            unknown = set(payload.keys()) - param_names
            for key in unknown:
                errors.append(f"Unknown parameter: '{key}'")

            # 3. Type validation
            try:
                self._validate_type(value, param.data_type, param.api_name)
            except ValidationError as e:
                errors.append(str(e))

            # 4. Default value handling
            if value is None and param.default is not None:
                payload[param.api_name] = param.default

        if errors:
            raise ValidationError("; ".join(errors))

    def _validate_type(
        self,
        value: Any,
        data_type: str,
        param_name: str,
    ) -> None:
        """Validate a single value against its declared data type."""
        type_map = {
            "STRING": str,
            "INTEGER": int,
            "LONG": int,
            "BOOLEAN": bool,
            "FLOAT": (float, int),
            "DOUBLE": (float, int),
            "DECIMAL": (float, int),
        }

        expected = type_map.get(data_type)
        if expected is None:
            return  # Complex types (STRUCT, ARRAY) validated elsewhere

        if not isinstance(value, expected):
            raise ValidationError(
                f"Parameter '{param_name}': expected {data_type}, "
                f"got {type(value).__name__}"
            )
```

## 4. 阶段 3：Outbox ORM + 同库事务（P0）

### 4.1 ActionService 重构

文件：`src/ontology/services/action_service.py`（重写）

```python
"""ActionService — data write operations with full transaction control.

All data mutations are applied to PostgreSQL `object_state` within the same
transaction as the audit log (`execution_log`) and side effect queue (`outbox`).
Iceberg remains the analytical persistence layer, updated asynchronously via
SeaTunnel CDC from the PG WAL — the architecture redline ("Iceberg is the single
write entry point for analytical data") is preserved because CDC is a derived
copy, not a direct write.

Flow:
    1. Idempotency check → reject duplicates
    2. Parameter validation → reject invalid input
    3. Rule evaluation → compute derived values
    4. Mutation building → generate change intents with expected_version
    5. Row-level OCC → UPSERT object_state WHERE version = :expected
       (affected_rows = 0 → ConflictError, rollback entire tx)
    6. PG atomic commit (object_state + execution_log + outbox)
    7. Return "applied" (data immediately readable via object_state)
    8. Async: Outbox Executor (INDEX → Doris ≤1s) + SyncFlushScheduler (ARCHIVE → Iceberg MERGE ≤5min)  # 去 SeaTunnel 化，不经 SeaTunnel
"""

from datetime import UTC, datetime
from typing import Any

from ontology.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ontology.core.schemas.action import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionTypeCreate,
    ActionTypeParameter,
    ActionRule,
)
from ontology.core.schemas.ontology import ActionType
from ontology.layers.catalog.gravitino_registry import GravitinoRegistry
from ontology.layers.dataset.iceberg_store import IcebergStore
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore
from ontology.services.action_validator import ParameterValidator


class ActionService:
    """Data write orchestration with full Action lifecycle."""

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry,
        dataset: IcebergStore,
    ) -> None:
        self._metadata = metadata
        self._catalog = catalog
        self._dataset = dataset
        self._validator = ParameterValidator()

    async def define_action_type(
        self,
        ontology_api_name: str,
        action_type_def: ActionTypeCreate,
    ) -> ActionType:
        """Register a new ActionType definition."""
        # 1. Resolve ontology
        onto = await self._metadata.get_ontology(ontology_api_name)

        # 2. Resolve affected object type
        obj_type = await self._metadata.get_object_type_by_api_name(
            onto.id, action_type_def.affected_object_type_api_name
        )

        # 3. Persist to PostgreSQL
        now = datetime.now(UTC)
        action_type = ActionType(
            id="",
            ontology_id=onto.id,
            api_name=action_type_def.api_name,
            display_name=action_type_def.display_name,
            description=action_type_def.description,
            affected_object_type_id=obj_type.id,
            parameters={
                "parameters": [p.model_dump() for p in action_type_def.parameters],
                "rules": [r.model_dump() for r in action_type_def.rules],
            },
            rules={},
            submission_criteria=action_type_def.submission_criteria,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        return await self._metadata.create_action_type(action_type)

    async def execute_action(
        self,
        object_type_api_name: str,
        action_api_name: str,
        request: ActionExecutionRequest,
        ontology_api_name: str | None = None,
    ) -> ActionExecutionResult:
        """Execute an action with full lifecycle.

        Args:
            object_type_api_name: Target object type
            action_api_name: Action type to execute
            request: Execution parameters + idempotency key
            ontology_api_name: Optional ontology scope

        Returns:
            ActionExecutionResult with status and details.

        Raises:
            NotFoundError: If action type or object type not found
            ValidationError: If parameter validation fails
            ConflictError: If optimistic lock conflict detected
            ForbiddenError: If write access denied
        """
        # Step 1: Resolve ActionType definition
        if ontology_api_name is None:
            # 从 object_type 反查 ontology
            ...
        action_type = await self._metadata.get_action_type(
            ontology_api_name, action_api_name
        )

        # Step 2: Idempotency check
        if request.idempotency_key:
            existing = await self._metadata.get_execution_by_idempotency_key(
                request.idempotency_key
            )
            if existing is not None:
                return ActionExecutionResult(
                    status="accepted",
                    action_id=existing.id,
                    mutations=existing.mutations,
                )

        # Step 3: Resolve parameter definitions
        param_defs = [
            ActionTypeParameter(**p)
            for p in action_type.parameters.get("parameters", [])
        ]

        # Step 4: Validate parameters
        self._validator.validate(param_defs, request.parameters)

        # Step 5: Evaluate rules (derivations + constraints)
        rule_defs = [
            ActionRule(**r)
            for r in action_type.parameters.get("rules", [])
        ]
        # 规则引擎执行 (见阶段 4)
        # derived = await self._rule_engine.evaluate(rule_defs, request.parameters)

        # Step 6: Permission check
        allowed = await self._catalog.check_access(object_type_api_name, "write")
        if not allowed:
            raise ForbiddenError(f"Write access denied for {object_type_api_name}")

        # Step 7: Build mutations (resolved change intents with expected_version)
        mutations = self._build_mutations(action_type, request.parameters)

        # Step 8: Row-level OCC + apply mutations to object_state (in tx)
        # Each mutation includes rid + expected_version (from client read).
        # PG UPSERT uses WHERE version = :expected — if affected_rows = 0,
        # someone else modified the object → ConflictError, rollback entire tx.
        affected_objects: dict[str, int] = {}
        for mutation in mutations:
            obj_id = mutation["rid"]
            expected_version = mutation.get("expected_version", 0)

            if mutation["type"] == "CREATE_OBJECT":
                new_version = await self._metadata.upsert_object_state(
                    rid=obj_id,
                    object_type_api_name=object_type_api_name,
                    ontology_id=action_type.ontology_id,
                    properties=mutation.get("properties", {}),
                    expected_version=0,
                )
            elif mutation["type"] in ("UPDATE_PROPERTY", "UPDATE_OBJECT"):
                new_version = await self._metadata.upsert_object_state(
                    rid=obj_id,
                    object_type_api_name=object_type_api_name,
                    ontology_id=action_type.ontology_id,
                    properties=mutation.get("properties", {}),
                    expected_version=expected_version,
                )
            elif mutation["type"] == "DELETE_OBJECT":
                await self._metadata.delete_object_state(obj_id)
                new_version = -1
            else:
                continue

            # new_version = 0 means affected_rows = 0 → version mismatch
            if new_version == 0 and mutation["type"] != "CREATE_OBJECT":
                raise ConflictError(
                    f"Object '{obj_id}' modified by another action "
                    f"(expected version {expected_version}). Please refresh."
                )
            affected_objects[obj_id] = new_version

        # Step 9: Atomic commit (object_state + execution_log + outbox)
        # All writes are in the same PG session transaction:
        #   - object_state UPSERTs already executed above
        #   - ExecutionLog + Outbox recorded below
        #   - PG session.commit() makes everything atomic
        execution = await self._metadata.create_execution_log(
            action_type_api_name=action_api_name,
            object_type_api_name=object_type_api_name,
            ontology_id=action_type.ontology_id,
            idempotency_key=request.idempotency_key or self._gen_idempotency_key(),
            parameters=request.parameters,
            mutations=mutations,
        )

        if "effects" in action_type.parameters:
            for effect in action_type.parameters.get("effects", []):
                await self._metadata.create_outbox_record(
                    action_execution_id=execution.id,
                    effect_type=effect.get("type", "WEBHOOK"),
                    effect_config=effect.get("config", {}),
                )

        # PG session.commit() — object_state + log + outbox[INDEX|ARCHIVE|...] atomically durable
        # → OutboxExecutor consumes INDEX outbox → Doris (≤1s)
        # → SyncFlushScheduler consumes ARCHIVE outbox → Iceberg (≤5min MERGE)
        #   (see action-sync-outbox-design.md; old SeaTunnel CDC path removed 2026-07-08)

        return ActionExecutionResult(
            status="applied",
            action_id=execution.id,
            affected_objects=affected_objects,
            mutations=mutations,
        )

    def _build_mutations(
        self,
        action_type: ActionType,
        parameters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Build resolved mutation intents from action type and parameters.

        Each mutation carries:
            - rid: target object
            - expected_version: version at client read time (for row-level OCC)
            - properties: resolved property values (post rule evaluation)

        Supported types: CREATE_OBJECT, UPDATE_PROPERTY, DELETE_OBJECT, ADD_LINK, REMOVE_LINK
        """
        # 基于 action_type.rules + 已求值参数生成完整变更意图
        return [
            {
                "type": "UPDATE_PROPERTY",
                "rid": "...",
                "expected_version": 5,   # 用户加载页面时的版本号
                "properties": {"status": "shipped"},
            }
        ]

    @staticmethod
    def _gen_idempotency_key() -> str:
        import uuid
        return uuid.uuid4().hex
```

## 5. 阶段 4：声明式规则引擎（P1）

### 5.1 安全表达式求值

文件：`src/ontology/services/action_rule_engine.py`（新增）

```python
"""Action rule engine — declarative rule evaluation with safe expression execution.

Supports two rule types:
    - derivation: Compute derived parameter values
    - constraint: Validate parameter combinations
    - validation: Check business rules

Security: Uses simpleeval for safe expression evaluation (no arbitrary code execution).
"""

from typing import Any

from ontology.core.exceptions import ValidationError
from ontology.core.schemas.action import ActionRule


class ActionRuleEngine:
    """Evaluate declarative rules for an Action Type."""

    # Safe built-in functions whitelist
    _SAFE_BUILTINS = {
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "abs": abs,
        "round": round,
        "min": min,
        "max": max,
        "sum": sum,
    }

    def __init__(self) -> None:
        try:
            from simpleeval import SimpleEval
            self._evaluator = SimpleEval(functions=self._SAFE_BUILTINS)
        except ImportError:
            self._evaluator = None

    def evaluate(
        self,
        rules: list[ActionRule],
        parameters: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Evaluate all rules, return derived values and validation errors.

        Args:
            rules: List of rule definitions
            parameters: Input parameters (will be mutated with derived values)

        Returns:
            Tuple of (derived_parameters, validation_errors)
        """
        errors: list[str] = []
        derived: dict[str, Any] = {}

        if self._evaluator is None:
            # Fallback: no evaluation available
            return derived, ["Rule engine not available (simpleeval not installed)"]

        # Phase 1: Execute derivation rules
        derivation_rules = [r for r in rules if r.type == "derivation"]
        for rule in derivation_rules:
            try:
                result = self._evaluator.eval(
                    rule.expression,
                    names={**parameters, **derived},
                )
                derived[rule.target] = result
                parameters[rule.target] = result  # Make available for subsequent rules
            except Exception as e:
                errors.append(f"Derivation rule '{rule.target}' failed: {e}")

        # Phase 2: Execute constraint/validation rules
        constraint_rules = [r for r in rules if r.type in ("constraint", "validation")]
        for rule in constraint_rules:
            try:
                result = self._evaluator.eval(
                    rule.expression,
                    names={**parameters, **derived},
                )
                if result is False:
                    errors.append(f"Validation failed: {rule.description or rule.expression}")
            except Exception as e:
                errors.append(f"Rule evaluation failed: {e}")

        return derived, errors
```

## 6. 阶段 5：Outbox Executor（P1）

### 6.1 实现

文件：`src/ontology/services/outbox_executor.py`（新增）

```python
"""OutboxExecutor — asynchronous side effect execution.

Consumes PENDING outbox records and executes configured side effects:
    - WEBHOOK: Send HTTP request to external API
    - WRITE_BACK: Write changes back to source system

Uses exponential backoff with jitter for retries.
Dead letter queue (DLQ) for permanently failed records.
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ontology.core.exceptions import OntologyError
from ontology.layers.metadata.postgres_meta_store import PostgresMetaStore


class OutboxExecutor:
    """Asynchronously consume outbox records and execute side effects."""

    def __init__(
        self,
        metadata: PostgresMetaStore,
        http_client: httpx.AsyncClient | None = None,
        poll_interval: float = 1.0,
        batch_size: int = 100,
    ) -> None:
        self._metadata = metadata
        self._http = http_client or httpx.AsyncClient(timeout=30.0)
        self._poll_interval = poll_interval
        self._batch_size = batch_size

    async def process_pending(self) -> int:
        """Poll and process all pending outbox records.

        Returns:
            Number of records processed.
        """
        records = await self._metadata.fetch_pending_outbox(self._batch_size)
        if not records:
            return 0

        for record in records:
            try:
                await self._execute(record)
                await self._metadata.mark_outbox_completed(record.id)
            except Exception as exc:
                await self._handle_failure(record, str(exc))

        return len(records)

    async def run_forever(self) -> None:
        """Run the executor loop continuously."""
        while True:
            try:
                await self.process_pending()
            except Exception:
                pass  # Logged externally
            await asyncio.sleep(self._poll_interval)

    async def _execute(self, record: dict[str, Any]) -> None:
        """Execute a single outbox record."""
        effect_type = record["effect_type"]
        config = record["effect_config"]

        if effect_type == "WEBHOOK":
            await self._call_webhook(config)
        elif effect_type == "WRITE_BACK":
            await self._write_back(config)
        else:
            raise OntologyError(f"Unknown effect type: {effect_type}")

    async def _call_webhook(self, config: dict[str, Any]) -> None:
        """Execute a webhook side effect."""
        url = config["url"]
        payload = config.get("payload", {})
        headers = config.get("headers", {})
        idempotency_key = config.get("idempotency_key", "")

        response = await self._http.post(
            url,
            json=payload,
            headers={**headers, "X-Idempotency-Key": idempotency_key},
        )
        response.raise_for_status()

    async def _write_back(self, config: dict[str, Any]) -> None:
        """Execute a write-back via SeaTunnel JDBC sink."""
        # 通过 SeaTunnel REST API 提交临时写回任务
        # config 包含: source_table, target_jdbc_url, column_mapping
        ...

    async def _handle_failure(self, record: dict[str, Any], error: str) -> None:
        """Handle execution failure with retry or DLQ."""
        retry_count = record["retry_count"] + 1
        max_retries = record["max_retries"]

        if retry_count >= max_retries:
            await self._metadata.move_outbox_to_dlq(record["id"], error)
        else:
            # Exponential backoff: 2^retry * 10s ± 50% jitter
            delay = (2**retry_count) * 10
            jitter = delay * 0.5 * (2 * random.random() - 1)
            next_retry = datetime.now(UTC) + timedelta(seconds=delay + jitter)
            await self._metadata.retry_outbox(record["id"], retry_count, error, next_retry)
```

## 7. 阶段 6：冲突检测（P2）

> **设计变更 (v1.1)**：主 OCC 机制已下沉到 PG 事务内的行级 version 检查（见阶段 3 的
> `execute_action` Step 8）。本阶段的 `ConflictDetector` 角色从「主冲突检测」调整为
> 「事后审计 + 跨表一致性校验」。

### 7.1 双层 OCC 架构

| 层级 | 位置 | 检测点 | 延迟 | 粒度 | 用途 |
|------|------|--------|------|------|------|
| **L1 主 OCC** | PG `object_state` 行级 | `UPDATE WHERE version = :expected` → `affected_rows=0` | 同步，提交时立即 | 行级 | 阻止并发冲突写入 |
| **L2 审计 OCC** | Iceberg snapshot diff | 快照 ID 比对 + 变更行过滤 | 异步，事后 | 表级 → 行级 | 发现绕过 L1 的异常、跨表一致性 |

**L1 工作原理（已集成在 `ActionService.execute_action` 中）**：

1. 客户端查询对象时拿到 `rid` + `version`（如 `version=5`）
2. 提交 Action 时，mutation 携带 `expected_version=5`
3. PG 事务内执行：
   ```sql
   UPDATE object_state
   SET properties = :new_props, version = version + 1, updated_at = NOW()
   WHERE rid = :id AND version = :expected_version
   RETURNING version;
   ```
4. 若他人先提交（version 已变成 6）→ `affected_rows = 0` → `ConflictError`
5. 整个 PG 事务回滚（object_state + execution_log + outbox 全部撤销）
6. 客户端收到 409，重新加载最新数据后重试

**与 Palantir 对标**：这正是 OSv2 的 Version ID 校验 —— 用户在 Workshop 中看到的版本号
随 Action 提交，若已被他人修改则拒绝。区别在于 Gaia 用 PG 的 `WHERE version = :expected`
替代了 OSv2 的 HLC + Raft 共识组。

### 7.2 实现：审计层 ConflictDetector

文件：`src/ontology/services/conflict_detector.py`（新增）

```python
"""Conflict detection — audit-layer OCC via Iceberg snapshot diff.

Primary OCC is handled inline in the PG transaction (see ActionService.execute_action
step 8: row-level `WHERE version = :expected` OCC). This module provides a secondary
audit layer for post-commit consistency checks and cross-table reconciliation.

Use cases:
    - Detecting anomalies that bypassed L1 (e.g., direct PG writes outside ActionService)
    - Cross-table consistency audits (object_state vs Iceberg)
    - Forensic replay of conflicting write sequences
"""

from typing import Any

from ontology.core.exceptions import ConflictError
from ontology.core.schemas.dataset import DatasetSnapshot
from ontology.layers.dataset.iceberg_store import IcebergStore


class ConflictDetector:
    """Audit-layer OCC: compare Iceberg snapshots for post-hoc conflict detection.

    This is NOT called during the Action hot path. It serves as a reconciliation
    and audit tool, answering questions like:
        - "Did any write bypass the PG row-level OCC?"
        - "Are object_state and Iceberg in sync?"
    """

    def __init__(self, dataset: IcebergStore) -> None:
        self._dataset = dataset

    async def audit_snapshot_diff(
        self,
        dataset: str,
        from_snapshot_id: int,
        to_snapshot_id: int | None = None,
    ) -> dict[str, Any]:
        """Compare two Iceberg snapshots and report changed rows.

        Used for post-commit audit, not for blocking concurrent writes.
        """
        to_snapshot_id = to_snapshot_id or await self._dataset.current_snapshot_id(dataset)
        if from_snapshot_id == to_snapshot_id:
            return {"conflict": False, "changed_objects": []}

        # Scan Iceberg changelog between snapshots
        changed = await self._dataset.scan_changes(
            dataset, from_snapshot_id, to_snapshot_id
        )
        return {
            "conflict": len(changed) > 0,
            "changed_objects": [r["rid"] for r in changed],
            "from_snapshot": from_snapshot_id,
            "to_snapshot": to_snapshot_id,
        }

    async def verify_object_state_consistency(
        self,
        dataset: str,
        rids: list[str],
        pg_versions: dict[str, int],
    ) -> list[str]:
        """Cross-check PG object_state versions against Iceberg latest.

        Returns list of rids where versions disagree.
        """
        mismatches: list[str] = []
        for obj_id in rids:
            iceberg_version = await self._dataset.get_object_version(dataset, obj_id)
            if iceberg_version != pg_versions.get(obj_id):
                mismatches.append(obj_id)
        return mismatches
```

### 7.3 为什么 PG 行级 OCC 优于 Iceberg 快照 OCC

| 维度 | PG 行级 OCC (L1) | Iceberg 快照 OCC (L2) |
|------|------------------|----------------------|
| **检测延迟** | 同步，SQL 执行时立即 | 异步，需查询 Iceberg REST API |
| **粒度** | 行级（精确到 rid + version） | 快照级（一次快照含数百变更） |
| **正交并发** | 天然支持（不同 rid 不冲突） | 无法区分（同一快照内有任何变更即拒绝） |
| **网络开销** | 零（已在 PG 事务内） | 每次检测需 HTTP 调用 Iceberg REST |
| **回滚成本** | PG 事务回滚，零额外成本 | 需手动补偿或重试 |
| **适用场景** | 在线热路径（每次 Action 提交） | 离线审计（定期或按需触发） |

---

## 8. 阶段 7：SeaTunnel CDC 接入（P1）

> **⚠️ 2026-07-08 已废弃（去 SeaTunnel 化）**：本节描述的 object_state→Iceberg SeaTunnel CDC 链路已被 outbox ARCHIVE effect + SyncFlushScheduler + IcebergStore.merge 取代。`create_action_cdc_pipeline` 代码保留但无调用方（孤儿）。本节保留作为历史设计记录，当前架构见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

### 8.1 方案

SeaTunnel 需要消费 PostgreSQL 的变更日志并将其写入 Iceberg。

**SeaTunnel CDC Pipeline 配置模板**（新增到 `sea_tunnel_engine.py`）：

```python
PIPELINE_CDC_TEMPLATE = """env {
  parallelism = {{ parallelism | default(1) }}
  job.mode = "STREAMING"
  job.name = "cdc_{{ ontology_name }}_actions"
}

source {
  PostgreSQL-CDC {
    host = "{{ pg_host }}"
    port = {{ pg_port }}
    username = "{{ pg_user }}"
    password = "{{ pg_password }}"
    database = "{{ pg_database }}"
    schema = "public"
    table = "action_execution_logs"
    slot.name = "gaia_cdc_{{ ontology_name }}"
    debezium.include.schema.changes = false
  }
}

transform {
  Sql {
    sql = """
      SELECT
        id as action_id,
        object_type_api_name,
        mutations::text,
        created_at
      FROM action_execution_logs
      WHERE status = 'COMPLETED'
    """
  }
}

sink {
  Iceberg {
    catalog_name = "ontology"
    namespace = "ontology"
    table = "{{ target_table }}"
    warehouse = "{{ warehouse }}"
    iceberg.table.commit-branch = "main"
  }
}
"""

# ├── Optional: Doris index sync pipeline triggered after Iceberg commit
# └── SeaTunnel 已有 INDEX_SYNC pipeline 处理此步骤
```

### 8.2 SeaTunnelEngine 新增方法

```python
async def create_action_cdc_pipeline(
    self,
    ontology_name: str,
    target_table: str,
) -> PipelineDef:
    """Create a CDC pipeline for action execution logs (PG → Iceberg)."""
    pipeline_name = f"cdc_{ontology_name}_actions"
    config = _render_cdc_config(
        ontology_name=ontology_name,
        target_table=target_table,
    )
    await self._submit_job(pipeline_name, config)
    return PipelineDef(
        name=pipeline_name,
        type="ACTION_CDC",
        source=PipelineSource(type="postgresql-cdc", config={"table": "action_execution_logs"}),
        transforms=[],
        sink=PipelineSink(type="iceberg", config={"table": target_table}),
    )
```

**⚠️ 前提条件**：需验证 SeaTunnel 2.3.13 是否支持 PostgreSQL-CDC connector。如不支持，使用 Debezium + Kafka 替代。

---

## 9. 阶段 8：实时索引同步（PG → Kafka → Doris）（P1）

> **⚠️ 2026-07-08 已废弃（去 SeaTunnel 化）**：本节描述的 PG→Kafka→Doris (路径 B) 实时索引链路已被 outbox INDEX effect + OutboxExecutor(≤1s) 取代。ActionSyncService + pg_to_kafka/kafka_to_doris pipeline + 对应模板已全部删除。本节保留作为历史设计记录，当前架构见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md) + [action-loop-design.md](./action-loop-design.md) §四.4。

### 9.1 设计理念

> 以下整节（§9）为**已废弃的历史设计**（2026-07-08 去 SeaTunnel 化），描述的 SeaTunnel INDEX_SYNC / Kafka→Doris 链路均已删除。当前 Action 后的 Doris 同步走 outbox INDEX effect → OutboxExecutor ≤1s，不走 SeaTunnel。本节保留仅作设计演进溯源。

在 Action 提交后，用户查询依赖 **Doris 索引加速层** 获取最新结果。~~如果仅走「Iceberg → SeaTunnel INDEX_SYNC → Doris」批同步路径，延迟可达 30 秒以上，影响用户体验。~~（该批同步路径已删除；当前 outbox INDEX effect 延迟 ≤1s。）

本阶段构建一条 **低延迟、按对象类型物理隔离** 的实时索引同步管道，通过 SeaTunnel 单一引擎统一驱动，端到端延迟控制在 **3-5 秒**。

**核心设计原则**：
- **统一引擎**：全程使用 SeaTunnel，避免引入 Debezium、Kafka Connect、SMT、Flink 等多个组件
- **物理隔离**：每种对象类型在 Kafka 中拥有独立 Topic，在 Doris 中拥有独立表，避免缓存污染和连接膨胀
- **动态扩展**：新增对象类型无需重启任何管道组件，系统自动识别并处理
- **Exactly-Once 语义**：SeaTunnel Checkpoint 机制 + Doris 2PC 保证端到端数据不丢不重

### 9.2 架构拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                    PostgreSQL (object_state)                    │
│                    单表存储所有对象最新状态                        │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                │ CDC (SeaTunnel 消费 WAL)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SeaTunnel 阶段一                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ PG CDC Source → Table Extract Transform → Kafka Sink     │  │
│  │     (根据 object_type_api_name 动态生成 Topic 名)          │  │
│  └───────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                │ 写入
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                          Kafka 集群                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │action_order  │  │action_customer│  │action_product│  ...     │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                │ 消费
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SeaTunnel 阶段二                              │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Kafka Source → Table Name Extract → Doris Sink           │  │
│  │        (由 Topic 名推导目标表名，动态写入 Doris)            │  │
│  └───────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                           Doris 集群                             │
│  ┌──────────┐  ┌────────────┐  ┌────────────┐                   │
│  │order     │  │customer    │  │product     │  ...              │
│  │Unique Key│  │Unique Key  │  │Unique Key  │                   │
│  │Merge-on- │  │Merge-on-   │  │Merge-on-   │                   │
│  │Write     │  │Write       │  │Write       │                   │
│  └──────────┘  └────────────┘  └────────────┘                   │
└─────────────────────────────────────────────────────────────────┘
```

### 9.3 阶段一：SeaTunnel PG CDC → Kafka（动态多 Topic）

**设计思路**：SeaTunnel 直接消费 PostgreSQL 的 WAL 逻辑复制流，在数据流中解析出 `object_type_api_name` 字段，以此作为 Kafka 的 Topic 名称。SeaTunnel Kafka Sink 支持通过 `${field_name}` 表达式动态路由，Single Job 即可完成所有对象类型的逻辑分拆。

```hocon
env {
  job.name = "gaia_pg_to_kafka"
  job.mode = "STREAMING"
  checkpoint.interval = 30000
  parallelism = 4
}

source {
  PostgreSQL-CDC {
    base-url = "jdbc:postgresql://${PG_HOST}:5432/gaia"
    username = "cdc_user"
    password = "${PG_PASSWORD}"
    database-names = ["gaia"]
    table-names = ["gaia.public.object_state"]
    plugin.name = "pgoutput"
    format = "debezium_json"
    debezium.after.field.include = ["rid", "object_type_api_name", "version", "properties", "updated_at"]
  }
}

transform {
  Sql {
    source_table_name = "postgres_cdc"
    result_table_name = "routed_data"
    query = """
      SELECT
        CONCAT('action_', LOWER(after.object_type_api_name)) AS dynamic_topic,
        after.rid,
        after.object_type_api_name,
        after.version,
        after.updated_at,
        after.properties,
        op
      FROM postgres_cdc
      WHERE op != 'd'
    """
  }
}

sink {
  Kafka {
    source_table_name = "routed_data"
    bootstrap.servers = "kafka:9092"
    topic = "${dynamic_topic}"
    topic.key = "rid"
    format = "json"
    format.field = "after"
    kafka.producer.batch.size = "16384"
    kafka.producer.linger.ms = "100"
  }
}
```

**关键技术点**：
- `PostgreSQL-CDC` 源直接消费 PG 逻辑复制流，要求 `wal_level = logical`
- SQL Transform 动态计算 `dynamic_topic` 字段（如 `action_order`），Kafka Sink 通过 `${dynamic_topic}` 表达式自动路由
- `topic.key = "rid"` 保证同一对象的变更进入同一分区，确保顺序消费
- `batch.size` / `linger.ms` 优化写入吞吐

### 9.4 阶段二：SeaTunnel Kafka → Doris（动态多表）

SeaTunnel 从 Kafka 消费所有 `action_*` Topic，从 Topic 名称中解析出目标表名，动态写入 Doris 对应物理表。

```hocon
env {
  job.name = "gaia_kafka_to_doris"
  job.mode = "STREAMING"
  checkpoint.interval = 30000
  parallelism = 4
}

source {
  Kafka {
    bootstrap.servers = "kafka:9092"
    topics_pattern = "action_.*"
    consumer.group = "seatunnel_kafka_to_doris"
    format = "json"
    start.mode = "earliest"
    metadata.include = ["topic"]
    schema = {
      fields = {
        __topic__ = "string"
        __key__ = "string"
        __value__ = "string"
      }
    }
  }
}

transform {
  Sql {
    source_table_name = "kafka_raw"
    result_table_name = "doris_data"
    query = """
      SELECT
        REGEXP_REPLACE(__topic__, '^action_', '') AS target_table,
        __value__.rid,
        __value__.version,
        __value__.updated_at,
        __value__.properties,
        __value__.op
      FROM kafka_raw
    """
  }
}

sink {
  Doris {
    source_table_name = "doris_data"
    fenodes = "doris_fe:8030"
    username = "admin"
    password = "${DORIS_PASSWORD}"
    database = "gaia_index"
    table = "${target_table}"
    doris.sink.properties = {
      format = "json"
      read_json_by_line = "true"
      merge_type = "merge"
    }
    sink.buffer-flush.max-rows = 2000
    sink.buffer-flush.interval-ms = 5000
    sink.enable-2pc = "true"
  }
}
```

**关键技术点**：
- `topics_pattern = "action_.*"` 自动匹配所有 `action_` 前缀 Topic，新增 Topic 无需重启
- `metadata.include = ["topic"]` 提取 Kafka 消息的原始 Topic 名称
- `REGEXP_REPLACE(__topic__, '^action_', '')` 将 `action_order` 转换为目标表 `order`
- Doris Sink 通过 `${target_table}` 动态路由至不同物理表
- `sink.enable-2pc = "true"` 开启两阶段提交，保证 Exactly-Once

### 9.5 Doris 端表设计

为每种对象类型创建独立的 Unique Key 表，**必须开启 Merge-on-Write** 以保证点查性能和行缓存效率。

```sql
-- 示例：order 表（customer, product 等结构相同，仅表名不同）
CREATE TABLE gaia_index.`order` (
    rid   VARCHAR(64)   NOT NULL,
    version     INT           NOT NULL,
    updated_at  DATETIME      NOT NULL,
    properties  JSON          NOT NULL
) UNIQUE KEY(rid)
DISTRIBUTED BY HASH(rid) BUCKETS AUTO
PROPERTIES (
    "enable_unique_key_merge_on_write" = "true",
    "enable_storage_row_cache" = "true"
);
```

**设计说明**：
- `rid` 作为主键，保证幂等更新
- `enable_unique_key_merge_on_write` 写入时即完成版本合并，查询时无需多版本扫描
- `properties` 保留完整 JSON，便于灵活查询；若字段固定，可展开为独立列

### 9.6 与 Action 主流程协同

- **Action 返回**：提交后等待 PG 事务成功即返回（毫秒级），**不等待索引同步**
- **查询路径**：
  - **实时点查**（按主键）：直接读 PG `object_state` 表（强一致）
  - **分析查询/列表页**：读 Doris（最终一致，延迟 3-5 秒），业务可接受
- **顺序保证**：Kafka 分区键 = `rid`，同一对象变更有序，Doris 按主键覆盖后最终状态正确

### 9.7 与 Iceberg CDC 管道的关系

本管道（PG → Kafka → Doris）与阶段 7 CDC 管道（PG → Iceberg）是 **并行互补** 关系：

| 维度 | PG → Iceberg CDC（阶段 7） | PG → Kafka → Doris（阶段 8） |
|------|---------------------------|------------------------------|
| **目标** | 主数据持久化（数据湖） | 索引加速层实时更新 |
| **延迟** | 分钟级（微批合并） | 亚秒～3-5 秒 |
| **数据完整性** | 全量字段 + 快照，支持时间旅行 | 索引列 + 热点属性 |
| **新增类型** | 需更新 Iceberg schema | 自动适配（动态 Topic + 动态表名） |

建议通过 **单一 SeaTunnel PG-CDC Source** 驱动，分流写入 Iceberg 和 Kafka，降低 PG 复制槽压力：

```hocon
# 统一 PG CDC Source，双路 Sink
source {
  PostgreSQL-CDC {
    table-names = ["gaia.public.object_state"]
  }
}
# Sink 1: Iceberg（主数据）
sink { Iceberg { ... } }
# Sink 2: Kafka（索引同步）
sink { Kafka { topic = "${dynamic_topic}" ... } }
```

### 9.8 运维与扩展

**新增对象类型流程**（全程零配置）：
1. PG 中写入 `object_state` 表，`object_type_api_name` 设为新类型名
2. Kafka 自动创建新 Topic（`auto.create.topics.enable=true`）
3. Doris 中创建对应目标表（结构见 9.5）
4. 两个 SeaTunnel 任务均无需重启，自动适配

**监控与告警**：
- **Kafka**：`consumer_lag` 预警 > 10 秒
- **SeaTunnel**：Checkpoint 完成时间/失败次数、Source/Sink 吞吐量
- **Doris**：Stream Load 延迟/失败率、表数据版本数

**故障恢复**：
- SeaTunnel 任务失败 → 从上一个 Checkpoint 恢复，Kafka offset 自动重置
- Doris 未提交事务 → 2PC 自动回滚
- Doris 表结构变更 → 在线 `ALTER TABLE` 不影响任务

**部署要求**：

| 组件 | 版本 | 配置要点 |
|------|------|----------|
| PostgreSQL | 12+ | `wal_level = logical`，创建复制槽 |
| Kafka | 2.8+ | `auto.create.topics.enable=true` |
| SeaTunnel | 2.3.13+ | `connector-cdc-postgresql`、`connector-kafka`、`connector-doris` |
| Doris | 1.2+ | Unique Key Merge-on-Write |

```bash
cd $SEATUNNEL_HOME
./bin/install-plugin.sh --source pg --source kafka --sink kafka --sink doris --transform sql
```

### 9.9 方案核心优势

| 特性 | 实现方式 | 收益 |
|------|----------|------|
| **统一引擎** | 全程 SeaTunnel，无额外组件 | 运维复杂度降至最低 |
| **动态多表路由** | `${dynamic_topic}` + `${target_table}` | 新增类型零配置，管道零重启 |
| **物理隔离** | 每个对象类型独立 Topic + 独立 Doris 表 | 避免缓存污染和连接池膨胀 |
| **低延迟** | CDC 流式 + 批量攒批 | 端到端 3-5 秒 |
| **Exactly-Once** | Checkpoint + Doris 2PC | 故障恢复不丢不重 |
| **顺序保证** | Kafka 分区键 = rid | 同对象变更有序，最终状态正确 |

---

## 10. 阶段 9：Write-back + 反馈环路防御（P3）

### 9.1 Write-back 到 RDBMS

```python
class WriteBackManager:
    """Write-back changes to external source systems."""

    async def write_back_to_rdbms(
        self,
        table_name: str,
        primary_key: str,
        changes: dict[str, Any],
        sync_metadata: dict[str, str],
    ) -> None:
        """Write changes back to source RDBMS via SeaTunnel JDBC sink.

        Args:
            table_name: Target table in source system
            primary_key: Primary key column name
            changes: Column-value pairs to update
            sync_metadata: Contains gaia_sync_tx, gaia_sync_user for feedback loop prevention
        """
        # Inject sync metadata into the update
        augmented_changes = {**changes, **sync_metadata}
        sql = self._build_upsert(table_name, primary_key, augmented_changes)

        # Submit to SeaTunnel as a one-shot JDBC task
        ...

    @staticmethod
    def _build_upsert(
        table: str,
        pk: str,
        changes: dict[str, Any],
    ) -> str:
        """Build a MERGE/UPSERT statement with sync metadata injection."""
        set_clause = ", ".join(f"{k} = :{k}" for k in changes)
        return f"""
            INSERT INTO {table} ({', '.join(changes.keys())})
            VALUES ({', '.join(f':{k}' for k in changes)})
            ON CONFLICT ({pk}) DO UPDATE SET {set_clause}
        """
```

### 9.2 反馈环路防御（增量拉取过滤）

```python
class IngestionFilter:
    """Prevent feedback loops during incremental data ingestion.

    Rewrites ingestion queries to exclude rows written back by Gaia itself.
    Uses transaction tagging (gaia_sync_tx, gaia_sync_user) for filtering.
    """

    def rewrite_query(
        self,
        original_sql: str,
        table: str,
        watermark_column: str,
        last_sync_tx_id: str | None,
    ) -> str:
        """Rewrite ingestion SQL to filter out self-written rows.

        Original:
            SELECT * FROM source_table WHERE last_modified > :watermark

        Rewritten:
            SELECT * FROM source_table
            WHERE last_modified > :watermark
              AND (gaia_sync_tx IS NULL OR gaia_sync_tx != :last_sync_tx)
        """
        if last_sync_tx_id is None:
            return original_sql

        if "WHERE" in original_sql.upper():
            return f"{original_sql} AND (gaia_sync_tx IS NULL OR gaia_sync_tx != '{last_sync_tx_id}')"
        else:
            return f"{original_sql} WHERE (gaia_sync_tx IS NULL OR gaia_sync_tx != '{last_sync_tx_id}')"
```

## 10. 异常层级补充

文件：`src/ontology/core/exceptions.py`（追加）

```python
class ConflictError(OntologyError):
    """Data conflict (HTTP 409) — optimistic lock failure."""
    def __init__(self, message: str) -> None:
        super().__init__(f"Conflict: {message}")


class ValidationError(OntologyError):
    """Input validation failure (HTTP 422)."""
    ...

class OutboxError(OntologyError):
    """Outbox execution failed after all retries — moved to DLQ."""
    ...

class ActionAlreadyExecutedError(OntologyError):
    """Duplicate action execution (idempotency key collision, HTTP 200 with cached result)."""
    ...
```

## 11. 路由层更新

文件：`src/ontology/routes/action/__init__.py`（重写）

```python
"""Action routes — data write operations with full lifecycle."""

from fastapi import APIRouter, Depends, HTTPException

from ontology.config.container import container
from ontology.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from ontology.core.schemas.action import ActionExecutionRequest, ActionExecutionResult, ActionTypeCreate
from ontology.core.schemas.ontology import ActionType
from ontology.services.action_service import ActionService

router = APIRouter(prefix="/actions", tags=["actions"])


def get_action_service() -> ActionService:
    return container.action_service


@router.post("/{ontology}/{object_type}/{action}", status_code=200)
async def execute_action(
    ontology: str,
    object_type: str,
    action: str,
    request: ActionExecutionRequest,
    service: ActionService = Depends(get_action_service),
) -> ActionExecutionResult:
    """Execute an action against an object type in an ontology.

    Full lifecycle:
    1. Idempotency check → reject duplicates
    2. Parameter validation → reject invalid input
    3. Rule evaluation → compute derived values
    4. Mutation building → generate change intents
    5. Conflict detection → reject or auto-merge
    6. Atomic commit (execution log + outbox)
    7. Async CDC to Iceberg + index sync
    """
    try:
        result = await service.execute_action(
            object_type_api_name=object_type,
            action_api_name=action,
            request=request,
            ontology_api_name=ontology,
        )
        return result
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{ontology}/types/{action_type}", status_code=201)
async def define_action_type(
    ontology: str,
    action_type: str,
    definition: ActionTypeCreate,
    service: ActionService = Depends(get_action_service),
) -> ActionType:
    """Define a new ActionType with parameter/rules/effect specification."""
    try:
        # 路由中提取 action_type 作为 api_name
        definition.api_name = action_type
        result = await service.define_action_type(
            ontology_api_name=ontology,
            action_type_def=definition,
        )
        return result
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

---

# 第四部分：优先级路线图

## 1. 四象限评估

```
                    高业务价值
                        │
        P1-规则引擎     │  P0-ActionType定义补全
        P1-CDC管道      │  P0-参数校验引擎
        P1-OutboxExecutor│  P0-Outbox ORM+事务
                        │
   ─────────────────────┼────────────────────── 实现难度
                        │
        P3-Write-back   │  P2-冲突检测
        P3-反馈环路防御  │  P2-Webhook副作用
        P4-FoO沙箱      │
                        │
                    低业务价值
```

## 2. 分阶段执行计划

| 阶段 | 内容 | 预估工作量 | 前置依赖 |
|------|------|-----------|----------|
| **S1** | ActionType 定义补全（Schema + ORM + Route） | 2-3 天 | 无 |
| **S2** | 参数校验引擎 + ActionService 重构 | 3-4 天 | S1 |
| **S3** | Outbox ORM 模型 + 同库事务提交 | 3-5 天 | S2 |
| **S4** | 声明式规则引擎（simpleeval） | 5-7 天 | S2 |
| **S5** | Outbox Executor（轮询 + Webhook） | 3-4 天 | S3 |
| **S6** | SeaTunnel CDC 管道（PG → Iceberg） | 3-5 天 | S3 |
| **S7** | 冲突检测（基于 Iceberg snapshot） | 3-5 天 | S2 |
| **S8** | 实时索引同步（PG → Kafka → Doris） | 4-6 天 | S3 + S6 |
| **S9** | Write-back 机制 + 反馈环路防御 | 5-7 天 | S5 + S6 |

## 3. 里程碑

| 里程碑 | 阶段 | 可演示能力 |
|--------|------|-----------|
| **M1** — Action 定义可配 | S1 | 通过 API 创建/查询 ActionType（含参数定义、规则） |
| **M2** — Action 执行可用 | S2+S3 | 参数校验 + 幂等去重 + 执行日志 + Outbox 持久化 |
| **M3** — 规则引擎生效 | S4 | 派生值计算 + 约束验证，错误定位到具体规则 |
| **M4** — 异步闭环 | S5+S6+S8 | Outbox Webhook + CDC 主数据 + 实时索引同步 |
| **M5** — 企业级保障 | S7+S9 | 冲突检测 + 自动合并 + 外部系统回写 |

## 4. 测试策略

| 层级 | 内容 | 覆盖 |
|------|------|------|
| **单元测试** | ParameterValidator 各类型校验 | 全部 DataType + 边界值 |
| **单元测试** | ActionRuleEngine 表达式求值 | 派生/约束/安全沙箱 |
| **单元测试** | ConflictDetector snapshot 比对 | 无冲突/属性级冲突/行级冲突 |
| **集成测试** | ActionService.execute_action 全流程 | Mock 所有 Layer |
| **系统测试** | Outbox → Webhook E2E | docker-compose + webhook mock |
| **系统测试** | CDC PG → Iceberg → Doris 全链路 | 全栈 docker-compose |
| **故障注入** | Doris 不可用时降级 | Iceberg 直读 |
| **故障注入** | Webhook 503 重试到 DLQ | OutboxExecutor |

---

## 附录 A：Git 提交规范建议

```
feat(action): add ActionTypeCreate schema with parameter/rule definitions
feat(action): implement ParameterValidator with full type checking
feat(action): add OutboxModel for transactional side effects
feat(action): implement ActionRuleEngine with safe expression evaluation
feat(action): add OutboxExecutor with exponential backoff retry
feat(action): add ConflictDetector with property-level OCC
feat(action): add CDC pipeline template for PG-to-Iceberg streaming
feat(action): add real-time index sync pipeline (PG → Kafka → Doris)
feat(action): implement WriteBackManager with feedback loop prevention
test(action): add unit tests for ParameterValidator
test(action): add unit tests for ActionRuleEngine security sandbox
test(action): add E2E test for Outbox → Webhook → DLQ flow
```

---

## 附录 B：依赖项检查

| 依赖 | 用途 | 当前状态 |
|------|------|----------|
| `simpleeval` | 安全表达式求值 | ⬜ 需新增依赖 |
| `httpx` | OutboxExecutor HTTP 客户端 | ✅ 已有 |
| `pyiceberg` | Iceberg 快照管理 | ✅ 已有 |
| `sqlalchemy` | ORM + 事务 | ✅ 已有 |
| SeaTunnel PostgreSQL-CDC connector | CDC 管道 | ⬜ 需验证兼容性 |

---

*文档版本：v1.0 — 2026-06-12*
*对标参考：Palantir Foundry Action (OSv2) 架构设计*
