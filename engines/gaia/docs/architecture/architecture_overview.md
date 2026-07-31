# Gaia — Palantir 风格开源分层架构 · 全景概览

> **版本**: 0.1.0 | **代码**: ~27,756 行 Python (src) + ~34,711 行测试 (tests) | **组件**: 11 个 Docker 服务（含可选 Neo4j） | **测试**: 1268 用例（后端 130 文件 / 前端 22 文件 169 用例）
>
> **⚠️ 本文件为概览**，组件真实状态以 [`implementation-status.md`](./implementation-status.md) 为唯一真相源。本文统计已于 2026-07-06 校准。

---

## 目录

1. [架构总览 — 8 分层](#1-架构总览--8-分层)
2. [组件版本矩阵](#2-组件版本矩阵)
3. [架构红线](#3-架构红线)
4. [领域模型 ER 图](#4-领域模型-er-图)
5. [8 个 Layer 实现总览](#5-8-个-layer-实现总览)
6. [22 个 Service 编排总览](#6-22-个-service-编排总览)
7. [六种核心数据流](#7-六种核心数据流)
8. [Docker Compose 11 服务拓扑](#8-docker-compose-11-服务拓扑)
9. [降级策略矩阵](#9-降级策略矩阵)
10. [异常层级树](#10-异常层级树)
11. [测试全景图](#11-测试全景图)
12. [运维红线与告警](#12-运维红线与告警)
13. [工程附录](#13-工程附录)

---

## 1. 架构总览 — 8 分层

> 2026-07 扩展：原 6 Layer + 本体工具层，新增 **Graph Layer**（Neo4j，ADR-015）+ **GeoTime Layer**（PostGIS + TimescaleDB），用于图关联推理与时空多维分析。

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                          Routes（HTTP 薄层 — FastAPI）                            │
│  /ontologies  /objects  /actions  /api/datasources  /ai  /metrics                │
│  /objects/{ont}/*  (query-dataframe / object-set / traverse / exists-link /       │
│                    find-paths / spatial-filter / series-query / analysis)        │
│                    ← 图关联推理 (ADR-015)  ⚠️ query-nl/explore-plan 已删          │
├──────────────────────────────────────────────────────────────────────────────────┤
│                本体工具层 tools/（ADR-009, 22 工具 / 8 toolset 模块 + HITL 切面）      │
│       三入口：MCP / AG-UI / REST  ← 薄包装 Service + 审计                         │
├──────────────────────────────────────────────────────────────────────────────────┤
│                        Services（业务编排层, 22 个）                              │
│  ┌────────────┐ ┌──────────────┐ ┌────────────────┐ ┌────────────┐ ┌──────────┐  │
│  │ Ontology   │ │ ObjectQuery  │ │ TimeTravel     │ │ Action     │ │DataSrc   │  │
│  │ Service    │ │ Service      │ │ Service        │ │ Service    │ │Service   │  │
│  └────────────┘ └──────────────┘ └────────────────┘ └────────────┘ └──────────┘  │
│  ┌────────────┐ ┌──────────────┐ ┌────────────────┐ ┌──────────────────────┐    │
│  │ ActionRule │ │ActionValidator│ │ActionAuthorizer│ │ OutboxExecutor /     │    │
│  │ Engine     │ │              │ │ (三层权限)      │ │ WriteBackManager     │    │
│  └────────────┘ └──────────────┘ └────────────────┘ └──────────────────────┘    │
│  ┌────────────┐ ┌──────────────┐ ┌────────────────┐ ┌──────────────────────┐    │
│  │IndexSync   │ │IndexField    │ │ConflictDetect  │ │ IngestionFilter /    │    │
│  │Service     │ │Extractor     │ │or              │ │ ActionSyncService    │    │
│  └────────────┘ └──────────────┘ └────────────────┘ └──────────────────────┘    │
│  ┌────────────┐ ┌──────────────┐ ┌────────────────┐ ┌──────────────────────┐    │
│  │ AIAgent /  │ │ TextQL 子包  │ │ **DataFrame    │ │ **GraphProjector /   │    │
│  │ AIGenerate │ │ (ADR-012)    │ │  QueryService**│ │  GeoTimeProjector /  │    │
│  │            │ │              │ │  (ObjectSet IR)│ │  AnalysisRecordStore │    │
│  └────────────┘ └──────────────┘ └────────────────┘ └──────────────────────┘    │
│         ↓ 依赖注入（Container 延迟初始化） ↓                                       │
├──────────────────────────────────────────────────────────────────────────────────┤
│                     Layer Implementations（8 层实现）                             │
│                                                                                  │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐ ┌──────────────────┐  │
│  │   Catalog      │ │   Metadata     │ │   Dataset      │ │     Index        │  │
│  │   Layer        │ │   Layer        │ │   Layer        │ │     Layer        │  │
│  │ GravitinoReg.. │ │ PostgresMeta.. │ │  IcebergStore  │ │ DorisIndexStore  │  │
│  │  httpx → REST  │ │  SQLAlchemy    │ │  pyiceberg     │ │  aiomysql        │  │
│  │  Gravitino     │ │  → asyncpg     │ │  → REST Catalog│ │  → MySQL Protocol│  │
│  └────────────────┘ └────────────────┘ └────────────────┘ └──────────────────┘  │
│                                                                                  │
│  ┌────────────────┐ ┌────────────────┐                                            │
│  │   Pipeline     │ │    Engine      │                                            │
│  │   Layer        │ │    Layer       │                                            │
│  │ SeaTunnelEng.. │ │ TrinoQueryEng. │                                            │
│  │  Jinja2 + httpx│ │  trino-python  │                                            │
│  │  → Zeta REST   │ │  → JDBC/HTTP   │                                            │
│  └────────────────┘ └────────────────┘                                            │
│                                                                                  │
│  ┌────────────────┐ ┌────────────────┐         🆕 ADR-015 图关联推理             │
│  │   Graph        │ │   GeoTime      │         ObjectSet IR 编排多引擎联动        │
│  │   Layer        │ │   Layer        │         (PG + Neo4j + PostGIS + Timescale) │
│  │ Neo4jGraphStore│ │ GeoTimeStore   │                                            │
│  │  neo4j async   │ │ PostGIS +      │                                            │
│  │  → Cypher      │ │ TimescaleDB    │                                            │
│  └────────────────┘ └────────────────┘                                            │
├──────────────────────────────────────────────────────────────────────────────────┤
│                   Core Models（领域模型 — 纯类型定义）                              │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────────┐  │
│  │ models/     │ │ schemas/    │ │ schemas/    │ │ schemas/    │ │ schemas/   │  │
│  │ ontology.py │ │ dataset.py  │ │ index.py    │ │ object_set  │ │ textql/    │  │
│  │ (ORM)       │ │ (pydantic)  │ │ (pydantic)  │ │ graph.py    │ │ ai.py      │  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘ └────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 层间依赖方向（严格单向）

```
Routes (HTTP) ──→ 本体工具层 ──→ Services ──→ Layers ──→ Core Models (纯类型)
                                  ↑              ↑
                                  └── 构造函数注入 ┘
```

**核心原则**：
- Services 直接依赖 Layers 中的**具体类**，不引入 interface 抽象
- **层与层之间不直接互相调用**（如 Index 层不调 Dataset 层）
- 跨层协调由 Service 层编排
- 替换组件 = 替换整个 layer 目录 + 改 Services 的 import 路径

---

## 2. 组件版本矩阵

| 组件 | 版本 | 容器镜像 | Python 库 | 端口 | 内存限制 |
|------|------|---------|-----------|------|---------|
| **FastAPI** | 0.115+ | — | `fastapi` | 8000 | 512m |
| **PostgreSQL** | 16 + PostGIS 3.6.1 + TimescaleDB 2.24.0 | `ngosang/timescaledb-postgis:2.24.0-pg16-postgis3.6` | `asyncpg` + `sqlalchemy[asyncio]` | 5432 | 512m |
| **Apache Gravitino** | 1.3.0 | `apache/gravitino:1.3.0` | `httpx` | 8090 | 1g |
| **Apache Iceberg REST** | — (Gravitino 内置) | `apache/gravitino:1.3.0` (Iceberg REST Server) | `pyiceberg` | 9001 | (含于 Gravitino 1g) |
| **RustFS** | latest | `rustfs/rustfs:latest` | `aioboto3` | 9000/9002 | 1g |
| **Apache Doris** | 4.0.5 | `apache/doris:fe-4.0.5` / `be-4.0.5` | `aiomysql` | 9030/9050 | 1g |
| **Apache Trino** | 478 | `trinodb/trino:478` | `trino-python-client` | 8080 | 1g |
| **Apache SeaTunnel** | 2.3.13 | `apache/seatunnel:2.3.13` | `jinja2` + `httpx` | 5801 | 512m |
| **Apache Kafka** | 4.3.0 | `apache/kafka:4.3.0` | — | 9092 | — |
| **Neo4j** 🆕 | 5-community | `neo4j:5-community` (profile=graph) | `neo4j[async]` | 7687 | 512m |
| **Ibis** 🆕 | 10.8 | — | `ibis-framework[postgres]` | — | — |

### Doris ↔ Iceberg ↔ Trino 职责边界

| 维度 | Doris | Iceberg | Trino |
|------|-------|---------|-------|
| **存储内容** | 主键 + 索引列 + 热点属性 | 全量业务明细 + 历史快照 | 无存储，纯计算 |
| **读流量** | 索引过滤，返回 ID 列表 | 按 ID 批量返回全量属性 | 统一查询入口，联邦路由 |
| **写流量** | 仅接收 Iceberg 同步 | 唯一写入入口（外部源经 SeaTunnel 写入；Action 经 outbox ARCHIVE effect 异步 MERGE） | 无 |
| **数据一致性** | 最终一致（秒/分钟级） | 强一致（ACID） | 读取时态 |
| **索引** | 主键 + 倒排 + 向量 | 无 | 无 |
| **版本** | 仅当前版本 | 全量历史快照 | 按需读取快照 |

---

## 3. 架构红线（不可违反）

| # | 红线 | 说明 |
|---|------|------|
| 1 | **Gravitino 仅管理物理数据资产** | 注册 Iceberg 表、Doris 外表、View 定义、RBAC。不存业务本体元数据 |
| 2 | **PostgreSQL 仅存业务本体元数据** | Ontology、ObjectType、PropertyDef 等。不存物理表元数据、不参与查询 |
| 3 | **Iceberg 是主数据唯一写入入口** | 外部数据经 SeaTunnel 写入 Iceberg；Action 操作态写 PG `object_state`（read-your-writes），经 outbox ARCHIVE effect 异步同步 Iceberg（≤5min MERGE）。Doris/Trino 只读 |
| 4 | **Doris 作为在线读主源** | 存全量结构化属性 + 倒排/向量索引（ADR-001 修订）；Iceberg/Trino 退为历史快照/批量分析/容灾路径 |
| 5 | **Trino 是主要联邦查询引擎** | 通过 Gravitino Connector 联邦查询；Virtual Table 查询必须走 Trino，无 Doris 降级路径 |
| 6 | **SeaTunnel 承担 PipelineBuilder（外部源→Iceberg 搬运）** | SeaTunnel 只负责把外部数据源搬进 Iceberg/TimescaleDB。从 Iceberg 往 Doris/Neo4j/PostGIS 的写入不走 SeaTunnel，统一由 `ObjectIndexFunnel`（外部接入路径）+ `OutboxExecutor`（Action 写入路径）直连各引擎完成（2026-07 去 SeaTunnel 化） |
| 7 | **无 Redis** | 用 Doris 自带缓存 + Iceberg ACID + 分区策略替代 |
| 8 | **Doris 索引表名必须带本体前缀** | `idx_{ontology}__{type}`（snake_case），避免跨本体数据互盖/误删（已修复） |
| 9 | **VIRTUAL 目标禁止写入** | ActionService.execute_action 拒绝 VIRTUAL 目标，前端置灰禁用 |
| 10 | **物理资源命名走 snake_case 保词界** | Doris 表名/S3 key/Iceberg 表名/dataset api_name 用 `_to_snake`；业务 api_name 不得泄漏进物理命名 |
| 11 | **Ontology API 层不吃自然语言（两层正交）** | 对齐 Palantir Foundry 范式：层 1 `/objects/*` 只吃结构化 ObjectSet IR（对应 OSDK search），层 2 `/ai/*` 吃 NL（LLM tool calling 调层 1）。**禁止**在 `/objects/*` 加 NL 端点（如 query-nl）。NL 查询走 `/ai/agent` 或 MCP 工具。见 CLAUDE.md 红线 11 + ADR-015 D4 |

### 各层允许/禁止矩阵

| 层 | 允许 | 禁止 |
|----|------|------|
| **Metadata (PostgreSQL)** | 存业务本体元数据 + object_state + outbox + datasets 治理记录 + interface 关联表 | 存物理表元数据 |
| **Catalog (Gravitino)** | 注册物理资产、虚拟表、RBAC、血缘 | 存业务本体元数据、参与数据计算 |
| **Dataset (Iceberg)** | 存全量明细 + 历史快照、时间旅行 | 做在线查询、做检索加速 |
| **Index (Doris)** | 在线读主源：全量结构化属性 + 倒排/向量索引 + IVF ANN 语义表 | 作为写入入口（写入经 ObjectIndexFunnel / outbox INDEX effect，不经 SeaTunnel） |
| **Pipeline (SeaTunnel)** | 外部数据源采集/清洗/写入 Iceberg + CDC + 文件/Kafka/时序同步 | 做元数据管理、做查询路由、做 Iceberg→Doris 同步（已改 Python 直连） |
| **Engine (Trino)** | 联邦查询、全量数据加载、Virtual Table 执行 | 做主数据存储 |
| **Graph (Neo4j)** 🆕 | 图遍历（search_around / find_paths / exists_link）、indexed 属性投影 | 存业务本体元数据、做主数据存储 |
| **GeoTime (PostGIS+TimescaleDB)** 🆕 | 空间过滤（ST_DWithin/ST_Within）、时序查询（超表）、GEOPOINT/TIME_SERIES 投影 | 存业务本体元数据、做主数据存储 |

---

## 4. 领域模型 ER 图

### 4.1 核心实体关系

```
ontologies (1) ──┬── (N) object_types ──┬── (N) properties
                 │                      ├── (N) object_type_shared_properties ── (N) shared_properties
                 │                      └── (N) link_types (source/target)
                 ├── (N) action_types
                 ├── (N) interface_types ── (N) interface_properties
                 ├── (N) value_types
                 ├── (N) object_type_groups
                 └── (N) branches

structs              (全局，无 FK 关联)
shared_properties    (全局，无 FK 关联)
action_execution_logs ── (N) outbox
object_state         (操作态写入目标，独立于元数据)
```

### 4.2 表结构简图

```sql
-- ── ontologies ──
CREATE TABLE ontologies (
    id          VARCHAR(32) PRIMARY KEY,     -- UUID v4
    api_name    VARCHAR(255) UNIQUE NOT NULL, -- 全局唯一标识
    display_name VARCHAR(255) NOT NULL,
    description TEXT DEFAULT '',
    rid         VARCHAR(255) DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);

-- ── object_types ──
CREATE TABLE object_types (
    id            VARCHAR(32) PRIMARY KEY,
    ontology_id   VARCHAR(32) NOT NULL REFERENCES ontologies(id) ON DELETE CASCADE,
    api_name      VARCHAR(255) NOT NULL,     -- 在 Ontology 内唯一
    display_name  VARCHAR(255) NOT NULL,
    description   TEXT DEFAULT '',
    primary_key   VARCHAR(255) NOT NULL,     -- 指向属性 api_name
    title_property VARCHAR(255) NOT NULL,
    storage_type  VARCHAR(20) NOT NULL CHECK (storage_type IN ('MANAGED','VIRTUAL')),
    visibility    VARCHAR(20) DEFAULT 'NORMAL',
    status        VARCHAR(20) DEFAULT 'ACTIVE',
    created_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL
);

-- ── properties ──
CREATE TABLE properties (
    id               VARCHAR(32) PRIMARY KEY,
    object_type_id   VARCHAR(32) NOT NULL REFERENCES object_types(id) ON DELETE CASCADE,
    api_name         VARCHAR(255) NOT NULL,
    display_name     VARCHAR(255) NOT NULL,
    description      TEXT DEFAULT '',
    data_type        VARCHAR(50) NOT NULL,   -- STRING|INTEGER|BOOLEAN|TIMESTAMP|...
    is_primary_key   BOOLEAN DEFAULT FALSE,
    is_title_property BOOLEAN DEFAULT FALSE,
    nullable         BOOLEAN DEFAULT TRUE,
    indexed          BOOLEAN DEFAULT FALSE,   -- 是否同步到 Doris
    physical_catalog VARCHAR(255),           -- 物理列映射
    physical_schema  VARCHAR(255),
    physical_table   VARCHAR(255),
    physical_column  VARCHAR(255),
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL
);

-- ── link_types ──
CREATE TABLE link_types (
    id             VARCHAR(32) PRIMARY KEY,
    ontology_id    VARCHAR(32) NOT NULL REFERENCES ontologies(id) ON DELETE CASCADE,
    api_name       VARCHAR(255) NOT NULL,
    display_name   VARCHAR(255) NOT NULL,
    description    TEXT DEFAULT '',
    source_object_type_id VARCHAR(32) NOT NULL,
    target_object_type_id VARCHAR(32) NOT NULL,
    foreign_key_property_api_name VARCHAR(255),
    cardinality    VARCHAR(10) NOT NULL CHECK (cardinality IN ('ONE','MANY')),
    direction      VARCHAR(10) NOT NULL CHECK (direction IN ('OUTGOING','INCOMING')),
    created_at     TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL
);

-- ── action_types ──
CREATE TABLE action_types (
    id                     VARCHAR(32) PRIMARY KEY,
    ontology_id            VARCHAR(32) NOT NULL REFERENCES ontologies(id) ON DELETE CASCADE,
    api_name               VARCHAR(255) NOT NULL,
    display_name           VARCHAR(255) NOT NULL,
    description            TEXT DEFAULT '',
    affected_object_type_id VARCHAR(32),    -- SET NULL (例外)
    parameters             JSONB DEFAULT '{}',
    rules                  JSONB DEFAULT '{}',
    submission_criteria    JSONB DEFAULT '{}',
    status                 VARCHAR(20) DEFAULT 'ACTIVE',
    created_at             TIMESTAMPTZ NOT NULL,
    updated_at             TIMESTAMPTZ NOT NULL
);
```

### 4.3 操作状态表（Action 写目标）

```sql
-- ── object_state（操作态，OCC 乐观锁） ──
CREATE TABLE object_state (
    rid            VARCHAR(64) PRIMARY KEY,
    object_type_api_name VARCHAR(255) NOT NULL,
    version              INT DEFAULT 1,          -- OCC 版本号
    properties           JSONB DEFAULT '{}',
    ontology_id          VARCHAR(32) NOT NULL REFERENCES ontologies(id),
    created_at           TIMESTAMPTZ NOT NULL,
    updated_at           TIMESTAMPTZ NOT NULL
);

-- ── action_execution_logs（审计日志） ──
CREATE TABLE action_execution_logs (
    id                VARCHAR(32) PRIMARY KEY,
    action_id         VARCHAR(32) NOT NULL,
    action_type_api_name VARCHAR(255) NOT NULL,
    object_type_api_name VARCHAR(255) NOT NULL,
    ontology_id       VARCHAR(32) NOT NULL REFERENCES ontologies(id),
    idempotency_key   VARCHAR(64) UNIQUE NOT NULL,  -- 幂等性保障
    parameters        JSONB DEFAULT '{}',
    mutations         JSONB DEFAULT '[]',
    status            VARCHAR(20) DEFAULT 'PENDING',
    error             TEXT,
    performed_by      VARCHAR(255) DEFAULT 'system',
    read_snapshot_id  INT,
    created_at        TIMESTAMPTZ NOT NULL
);

-- ── outbox（副作用队列 — 幂等保障） ──
CREATE TABLE outbox (
    id                  VARCHAR(32) PRIMARY KEY,
    action_execution_id VARCHAR(32) NOT NULL REFERENCES action_execution_logs(id),
    effect_type         VARCHAR(50) NOT NULL,  -- WEBHOOK | WRITE_BACK
    effect_config       JSONB DEFAULT '{}',
    payload             JSONB DEFAULT '{}',
    status              VARCHAR(20) DEFAULT 'PENDING',  -- PENDING|COMPLETED|FAILED|DLQ
    retry_count         INT DEFAULT 0,
    max_retries         INT DEFAULT 3,
    last_error          TEXT,
    next_retry_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL
);
```

---

## 5. 8 个 Layer 实现总览

| Layer | 类名 | 文件 | 核心依赖 | 行数 (估) | 公开方法数 |
|-------|------|------|---------|-----------|-----------|
| Metadata | `PostgresMetaStore` | `layers/metadata/postgres_meta_store.py` | SQLAlchemy 2.0 async | ~600 | 30+ |
| Catalog | `GravitinoRegistry` | `layers/catalog/gravitino_registry.py` | httpx | ~400 | 10+ |
| Dataset | `IcebergStore` | `layers/dataset/iceberg_store.py` | pyiceberg | ~280 | 10 |
| Index | `DorisIndexStore` | `layers/index/doris_index_store.py` | aiomysql | ~450 | 12+ |
| Engine | `TrinoQueryEngine` | `layers/engine/trino_query_engine.py` | trino-python-client | ~100 | 5 |
| Pipeline | `SeaTunnelEngine` | `layers/pipeline/sea_tunnel_engine.py` | Jinja2 + httpx | ~700 | 12+ |
| **Graph** 🆕 | `Neo4jGraphStore` | `layers/graph/neo4j_graph_store.py` | neo4j[async] | ~445 | 10+ |
| **GeoTime** 🆕 | `GeoTimeStore` | `layers/geotime/geotime_store.py` | asyncpg (PostGIS+TimescaleDB) | ~333 | 8 |

### 5.1 PostgresMetaStore — 核心方法签章

```
create_ontology(ontology)           → Ontology
get_ontology(api_name)              → Ontology
list_ontologies()                   → list[Ontology]
update_ontology(api_name, ...)      → Ontology
delete_ontology(api_name)           → None
create_object_type(object_type)     → ObjectType
get_object_type(ontology, api_name) → ObjectType
list_object_types(ontology)         → list[ObjectType]
update_object_type(id, updates)     → ObjectType
add_property(object_type_id, prop)  → PropertyDef
get_properties(object_type_id)      → list[PropertyDef]
create_shared_property(prop)        → SharedProperty
link_shared_property(ot_id, sp_id)  → None
create_link_type(link)              → LinkTypeDef
get_link_types(ontology)            → list[LinkTypeDef]
create_action_type(action)          → ActionType
get_action_type(ontology, api_name) → ActionType
create_interface_type(iface)        → InterfaceType
create_value_type(vt)               → ValueType
create_struct(struct)               → Struct
create_group(group)                 → ObjectTypeGroup
create_branch(branch)               → Branch
[Action Execution]:
create_execution_log(...)           → ActionExecutionLogModel
get_execution_by_idempotency_key(k) → ActionExecutionLogModel | None
create_outbox_record(...)           → OutboxModel
fetch_pending_outbox(batch_size)    → list[dict]
mark_outbox_completed(id)           → None
retry_outbox(id, ...)               → None
move_outbox_to_dlq(id, error)       → None
upsert_object_state(...)            → int (new_version or 0 on conflict)
get_object_state(rid)         → dict | None
get_object_states_by_type(type, ...)→ list[dict]
delete_object_state(rid)      → None
commit_transaction()                → None
rollback_transaction()              → None
```

### 5.2 GravitinoRegistry — 核心方法

```
register_dataset(catalog, schema, name, location, columns)  → None
create_view 已删除（Gravitino SQL View 线路废弃）                      → None
get_table_columns(catalog, schema, table)                  → list[dict]  # 联邦拉列
is_view(catalog, schema, name)                                → bool
check_access(object_type_api_name, operation)                 → bool
resolve_physical_table(object_type_api_name)                  → dict{catalog,schema,table}
```

### 5.3 IcebergStore — 核心方法

```
append(dataset, rows)                    → WriteResult
overwrite(dataset, rows)                 → WriteResult
load_by_ids(dataset, ids, columns)       → list[dict]   ← 核心查询路径
load_by_ids_as_of(dataset, ids, cols, snapshot_id) → list[dict]
scan_as_of(dataset, columns, snapshot_id, limit)  → list[dict]
get_snapshots(dataset)                   → list[DatasetSnapshot]
get_latest_snapshot(dataset)             → DatasetSnapshot | None
get_schema(dataset)                      → DatasetSchema
evolve_schema(dataset, additions)        → None
```

### 5.4 DorisIndexStore — 核心方法

```
create_index_table(object_type_api_name, fields, partition_by)  → None
drop_index_table(object_type_api_name)                           → None
upsert(object_type_api_name, records)                            → None
delete_by_ids(object_type_api_name, ids)                         → None
load_by_ids(object_type_api_name, ids, columns)  → list[dict]  (点查)
execute_sql(ontology, object_type, sql, params)  → list[dict]  (参数化查询, TextQL 编译器路径)
```

### 5.5 TrinoQueryEngine — 核心方法

```
query(sql, params?) → list[dict[str, Any]]
```

### 5.6 SeaTunnelEngine — 流水线类型

> **职责边界（2026-07 去 SeaTunnel 化后）**：SeaTunnel 现只承担「外部源 → Iceberg/TimescaleDB」的搬运，不再参与 Iceberg→Doris 或任何→Neo4j/PostGIS 的写入。从 Iceberg 往 Doris/Neo4j/PostGIS 的写入统一由 `ObjectIndexFunnel`（Python 侧批量直连）或 `OutboxExecutor`（Action 写入 outbox 驱动）完成，不走 SeaTunnel。

| 流水线类型 | 方法 | 源 → 目标 | 说明 |
|-----------|------|----------|------|
| MAIN | `create_sync_pipeline()` | MySQL/PG/Kafka/... → Iceberg | 主数据写入（外部数据源接入，Catalog First：表由 Gaia 经 `IcebergStore.create_managed_table` 建好，SeaTunnel sink `schema_save_mode=IGNORE` 只写数据） |
| FILE_SYNC | `create_file_sync_pipeline()` | S3File → Iceberg | 文件同步 (ADR-014) |
| KAFKA_INGESTION | `create_kafka_ingestion_pipeline()` | Kafka → Iceberg | Kafka 落地 (ADR-014) |
| KAFKA_TIMESERIES | `create_kafka_timeseries_pipeline()` | Kafka → TimescaleDB | 时序同步 (ADR-015) |
| EXTERNAL_CDC | `create_external_cdc_pipeline()` | 外部 MySQL/PG/OpenGauss/TiDB → Iceberg | 外部 CDC (ADR-014) |

> **已删除的流水线类型（历史记录）**：
> - `INDEX_BACKFILL` / `create_index_pipeline()`（Iceberg→Doris）—— **2026-07 T1.10 删除**。Doris 写入统一收口到 `ObjectIndexFunnel`（从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`，负责 rid 分配/复用）。详见 [graph-reasoning-design.md](./graph-reasoning-design.md) §6 + [handoff-rid-funnel-closure.md](./handoff-rid-funnel-closure.md)。
> - `ACTION_CDC` / `create_action_cdc_pipeline()`（PG action_execution_logs → Iceberg 审计归档）—— **2026-07-10 删除**（无调用方，审计日志 PG append-only 已足够）。
> - `PG_TO_KAFKA` / `KAFKA_TO_DORIS` / `DUAL_SINK`（object_state 同步）—— **2026-07-08 去 SeaTunnel 化删除**，改 outbox 驱动（INDEX effect→Doris ≤1s / ARCHIVE effect→Iceberg ≤5min MERGE）。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

---

### 5.7 Neo4jGraphStore — 核心方法（🆕 ADR-015）

```
create_label(label) / create_constraint(label, prop) / create_indexed_index(label, prop)
upsert_node(label, rid, props) / upsert_edge(rel_type, src, dst, props)
delete_node(rid) / delete_edge(...)
search_around(src_vids, rel_types, direction, max_depth, limit) → GraphTraversalResult
exists_link(src_vids, rel_types, mode=ANY|SINGLE_TARGET) → bool
count_nodes(label, filter?) → int
find_paths(src_vid, dst_vid, rel_types?, max_depth, limit) → list[Path]  # allShortestPaths
```

### 5.8 GeoTimeStore — 核心方法（🆕）

```
create_geo_table(table, geo_column, extra_columns)  # GiST 索引
create_timeseries_hypertable(table, time_column, ...)  # TimescaleDB 超表
upsert_geo(table, rows) / append_series(table, rows)
spatial_filter(table, filter)  # withinDistance / withinPolygon / withinBoundingBox
series_query(table, filter)     # 时间窗 + 实体过滤
table_exists(table) / drop_table(table)
```

---

## 6. 22 个 Service 编排总览

> 2026-07 新增 4 个 Service（GraphProjector / GeoTimeProjector / DataFrameQueryService / AnalysisRecordStore），用于图关联推理（ADR-015）。详见 [implementation-status.md §二](./implementation-status.md) 与 [§十二](./implementation-status.md)。

| Service | 文件 | 依赖的 Layers | 职责 |
|---------|------|-------------|------|
| **OntologyService** | `services/ontology_service.py` | Metadata, Catalog, Index, Graph, GeoTime | 本体 CRUD + 物理/虚拟 ObjectType 全生命周期 + 图/时空 schema provision |
| **ObjectQueryService** | `services/object_query_service.py` | Metadata, Catalog, Index, Dataset, Engine | 核心查询路由：MANAGED→Doris+Iceberg, VIRTUAL→Trino联邦 + execute_compiled_sql（TextQL） |
| **TimeTravelService** | `services/time_travel_service.py` | Catalog, Engine | 历史快照查询（Trino FOR VERSION AS OF） |
| **ActionService** | `services/action_service.py` | Metadata, Catalog, Dataset, Graph, GeoTime | Action 定义+执行：幂等性/OCC/Atomictx/Outbox + Step 11 图边投影（capabilities 门控） + 主事务追加 INDEX/ARCHIVE outbox（去 SeaTunnel 化） |
| **ActionRuleEngine** | `services/action_rule_engine.py` | — | 声明式规则引擎（derivation/constraint/validation + 上下文注入） |
| **ActionValidator** | `services/action_validator.py` | — | 参数类型/必填/校验 + 动态默认值 |
| **ActionAuthorizer** 🆕 (ADR-011) | `services/action_auth.py` | Metadata | 三层权限（执行/行级/参数级） |
| ~~**ActionSyncService**~~ | ~~`services/action_sync_service.py`~~ | ~~Pipeline~~ | **已删除（2026-07-08 去 SeaTunnel 化）**。object_state 同步改 outbox 驱动（INDEX/ARCHIVE effect） |
| **DataSourceService** | `services/datasource_service.py` | Catalog, Pipeline | 数据源 CRUD + 探索 + 虚拟表登记 + 多源 connector 分流（ADR-014）+ CDC/timeseries 同步 |
| **IndexSyncService** | `services/index_sync_service.py` | Index | provision/rebuild/deprovision（仅 Doris 索引表 DDL）；数据同步已剥离给 ObjectIndexFunnel（2026-07 T1.10 删除 SeaTunnel backfill） |
| **IndexFieldExtractor** | `services/index_field_extractor.py` | — | indexed 字段推导 + 红线校验 |
| **ConflictDetector** | `services/conflict_detector.py` | Metadata, Dataset | OCC 冲突检测 + 后台审计 run_audit_loop |
| **OutboxExecutor** | `services/outbox_executor.py` | Metadata, Index, WriteBack | Outbox 消费：INDEX→Doris(≤1s)/webhook/writeback/sub_action/kafka_topic + 指数退避 + DLQ（排除 ARCHIVE） |
| **WriteBackManager** | `services/write_back_manager.py` | Dataset | Write-back: Outbox → Iceberg 异步写入 |
| **SyncFlushScheduler** 🆕 | `services/sync_flush_scheduler.py` | Metadata, Dataset | 消费 ARCHIVE outbox 微批→IcebergStore.merge + outbox 7 天清理 |
| **IcebergMaintenanceService** 🆕 | `services/iceberg_maintenance_service.py` | Engine | 路径 A 配套：Trino ALTER TABLE EXECUTE 小文件/snapshot 治理 |
| ~~**IndexSyncScheduler**~~ | ~~`services/index_sync_scheduler.py`~~ | ~~Pipeline~~ | **已删除（2026-07-10）**。外部接入数据改方案 A：provision/sync_now 事件驱动触发 Doris 同步 |
| **IngestionFilter** | `services/ingestion_filter.py` | — | 反馈环过滤（接入 DataSourceService 增量查询重写） |
| **AIAgent** | `services/ai_agent.py` | (route 挂载) | AG-UI pydantic-ai Agent（挂全部 8 toolset 模块 / 22 工具，ADR-009） |
| **AIGenerate** | `services/ai_generate.py` | (route import) | LLM 原语：generate_text / stream_text / stream_structured（scaffold） |
| **TextQL** 🆕 (ADR-012) | `services/textql/` 子包 | Metadata, Index, Engine | 五步流水线 + ObjectSetParser 推理线入口 + ExplorePlanParser |
| **GraphProjector** 🆕 (ADR-015) | `services/graph_projector.py` | Graph | object_state/links → Neo4j 投影（仅 indexed 属性）+ rebuild |
| **GeoTimeProjector** 🆕 | `services/geotime_projector.py` | GeoTime | object_state → PostGIS 投影（仅空间属性对象） |
| **DataFrameQueryService** 🆕 (ADR-015) | `services/object_set_executor.py` | Metadata, Graph, GeoTime | ObjectSet IR 编排中枢（递归求值 + 多引擎联动 + EvidenceChain） |
| **AnalysisRecordStore** 🆕 | `services/analysis_record_store.py` | Metadata | 证据链快照 save/get |

### ObjectQueryService 路由决策树

```
load_objects(request)
│
├─ Step 1: 解析 api_name → ontology_api_name + type_api_name
├─ Step 2: Metadata.get_object_type() → ObjectType
├─ Step 3: Catalog.check_access() → 403 if denied
│
├─ storage_type == "VIRTUAL"?
│   └──→ Trino View 查询（无 Doris）
│
└─ storage_type == "MANAGED"?
    │
    ├─ 有 filter 且无 rids?
    │   ├─ Doris 在线 → execute_sql 参数化查询 → rids
    │   └─ Doris 不可用 → 降级 Trino 全表扫（带分区裁剪）
    │
    ├─ 有 rids?
    │   └─ 直接使用
    │
    ├─ 无条件？→ Trino 全表扫
    │
    └─ IcebergStore.load_by_ids() → 全量属性（走 Expression API）
        └─ Iceberg 不可用 → 降级 Trino 按 ID 查询
```

### ActionService 执行生命周期

```
execute_action()
│
├─ Step 1: 解析 ActionType 定义
├─ Step 2: 幂等性检查（idempotency_key）
│   └─ 已存在 → 返回 cached result
├─ Step 3: 参数类型校验（ActionValidator）
├─ Step 4: 规则评估（ActionRuleEngine: derivation → constraint）
├─ Step 5: 权限校验（Catalog.check_access(write)）
├─ Step 6: 构建 mutations + OCC version
├─ Step 7: PG 原子写入（同一事务）：
│   ├─ upsert_object_state() — OCC WHERE version = :expected
│   │   └─ 影响行=0 → rollback + ConflictError
│   ├─ create_execution_log() — 审计追踪
│   ├─ create_outbox_record() — 副作用队列（webhook/writeback）
│   └─ commit_transaction()
└─ Step 8: 返回 result.status = "applied"
```

---

## 7. 六种核心数据流

### 场景 1：新数据接入

```
                    ┌─────────────────────┐
                    │   源端 (MySQL/Kafka) │
                    └─────────┬───────────┘
                              │ CDC/全量
                    ┌─────────▼───────────┐
                    │  SeaTunnel 流水线 1  │  MAIN pipeline
                    │  (主写入 Iceberg)    │
                    └─────────┬───────────┘
                              │ 写入
             ┌────────────────▼──────────────────┐
             │  Iceberg (RustFS/S3)               │
             │  • 全量明细 + 历史快照              │
             │  • ACID 事务性写入                  │
             │  • 唯一写入入口                     │
             └────────┬────────────────┘
                      │ 注册
             ┌────────▼────────┐
             │  Gravitino      │
             │  • 物理表注册    │
             │  • RBAC + 血缘   │
             └─────────────────┘
                      │ scan_latest
             ┌────────▼───────────────┐
             │  ObjectIndexFunnel      │  Python 侧索引编排
             │  • rid 分配/复用        │  (取代旧 SeaTunnel INDEX pipeline)
             │  • 四引擎扇出           │
             └──┬─────────────┬────────┘
                │ 写入索引列   │ 投影(capabilities 门控)
       ┌────────▼────────┐  ┌─▼──────────────┐
       │  Doris 索引表    │  │ Neo4j / PostGIS │
       │  • 主键+索引列   │  │ • 图/时空投影    │
       │  • 热点属性      │  └────────────────┘
       └─────────────────┘

     元数据已就绪：
        PostgreSQL ← OntologyService 在定义 ObjectType 时写入
```

> **注**：SeaTunnel 在本场景只承担「源端 → Iceberg」的搬运（流水线 1 MAIN）。从 Iceberg 往 Doris / Neo4j / PostGIS 的写入由 `ObjectIndexFunnel` 直连各引擎完成，不再经过 SeaTunnel（2026-07 去 SeaTunnel 化）。Action 业务写入路径走 outbox INDEX effect → OutboxExecutor → Doris（≤1s），同样不经 SeaTunnel。

### 场景 2：物理对象查询（核心链路）

```
┌──────────┐
│  客户端   │  GET /objects/load
└────┬─────┘
     │ object_type=hr.employee, filter={status:active}, properties=[name,dept,salary]
     ▼
┌──────────────────────────────────────────────────────────────────┐
│  ObjectQueryService                                              │
│  1. Metadata.get_object_type("hr", "employee")                   │
│     → storage_type=MANAGED                                      │
│  2. Catalog.check_access("hr.employee", "read")                  │
│     → allowed                                                    │
└────┬─────────────────────────────────────────────────────────────┘
     │
     ├── 有 filter → Doris 索引过滤
     │   ┌──────────────────┐
     │   │  DorisIndexStore │  execute_sql(sql=?, params=[...])
     │   │  SELECT id FROM  │
     │   │  idx_hr_employee │  → [uuid1, uuid2, ...]
     │   │  WHERE status=.. │
     │   └────────┬─────────┘
     │            │ rids
     │            ▼
     │   ┌──────────────────┐
     │   │  IcebergStore    │  load_by_ids("ontology.employee",
     │   │  (Expression API)│    ids=[...], columns=[name,dept,salary])
     │   └────────┬─────────┘
     │            │ [{name:..., dept:..., salary:...}, ...]
     │            ▼
     │   ┌──────────────────┐
     │   │    客户端 ← 结果  │
     │   └──────────────────┘
     │
     └── Doris 不可用时 → 降级
         ┌──────────────────┐
         │  TrinoQueryEngine│  SELECT name, dept, salary
         │  (Gravitino      │  FROM gravitino_catalog.iceberg...
         │   Connector)     │  WHERE status='active'
         └────────┬─────────┘
                  │
                  ▼
         ┌──────────────────┐
         │    客户端 ← 结果  │
         └──────────────────┘
```

### 场景 3：虚拟对象查询

```
┌──────────┐
│  客户端   │  GET /objects/load
└────┬─────┘
     │ object_type=hr.employee_virtual
     ▼
┌───────────────────────────────────────────────────────────────┐
│  ObjectQueryService                                           │
│  → storage_type=VIRTUAL                                       │
│  → 无 Doris 参与                                              │
└────┬──────────────────────────────────────────────────────────┘
     │
     ▼
┌───────────────────────────────────────────────────────────────┐
│  TrinoQueryEngine                                              │
│  SELECT name, dept                                            │
│  FROM gravitino_catalog.ontology.hr_employee_virtual_view     │
│  WHERE ... LIMIT ...                                          │
└────┬──────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────┐
│  客户端 ← 结果    │
└──────────────────┘
```

### 场景 4：时间旅行

```
┌──────────┐
│  客户端   │  GET /objects/load?as_of_snapshot_id=1234567890
└────┬─────┘
     ▼
┌──────────────────────────────────────────────────────────────┐
│  TimeTravelService                                           │
│  1. Catalog.check_access("read")                              │
│  2. Catalog.resolve_physical_table → {catalog,schema,table}   │
└────┬─────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────────────┐
│  TrinoQueryEngine                                             │
│  SELECT name, dept                                           │
│  FROM iceberg_catalog.ontology.employee                      │
│  FOR VERSION AS OF 1234567890                                │
│  WHERE id IN ('uuid1', 'uuid2')                              │
└────┬─────────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────┐
│  Iceberg 快照读取  │
└──────────────────┘
```

### 场景 5：自然语言查询（TextQL，ADR-012）

```
用户问 → intent_parser(LLM→QueryIR) → semantic_recall(精确匹配+向量召回兜底)
       → schema_injector(确定性注入) → query_with_sql(ontology, sql) 工具
       （SqlGlot 编译，推断所有 OT；全 MANAGED→Doris主/Trino降级；含 VIRTUAL→Trino 跨 catalog 联邦）
```

### 场景 6：图关联推理与时空多维分析（ADR-015，🆕）

```
用户问 (NL)
   ↓
ObjectSetParser (4 层保障) → ObjectSet IR (判别联合，对齐 Palantir 15 type 中的 13 type)
   ↓
DataFrameQueryService.execute(IR)
   ├── objectType/static/filter → PG object_state (属性过滤，Ibis 临时表下推)
   ├── searchAround → Neo4j GraphStore (Cypher 图遍历)
   ├── spatial filter → PostGIS (ST_DWithin / ST_Within)
   ├── timeRange → TimescaleDB (超表)
   ├── union/intersect/subtract/aggregate/select/order_by → Ibis 集合运算
   └── interfaceBase/interfaceLinkSearchAround → 跨类型查询 (Interface 关联表)
   ↓
水合 (object_state 批量取, 分批 5000) + EvidenceChain 证据累积
   ↓
返回结果 + evidence_id (可查证据链快照)
```

> 详见 [graph-reasoning-design.md](./graph-reasoning-design.md) 与 [implementation-status.md §十二](./implementation-status.md)。

---

## 8. Docker Compose 11 服务拓扑

```
┌─────────────────────────────────────────────────────────────────────┐
│                        docker-compose.yml                           │
│                                                                     │
│  ┌───────────┐    ┌───────────────────┐    ┌───────────┐  │
│  │  RustFS   │◄───│  Gravitino        │    │  Kafka    │  │
│  │  :9000    │    │  :8090 + :9001    │    │  :9092    │  │
│  │  S3 存储  │    │  元数据 + Iceberg  │    │  消息队列  │  │
│  └───────────┘    └───────────────────┘    └───────────┘  │
│       ▲                │                │                │          │
│       │                │                │           ┌────┘          │
│       │                ▼                ▼           ▼               │
│       │         ┌───────────────────────────────────────────┐       │
│       │         │              SeaTunnel                    │       │
│       └─────────┤  Zeta Cluster  :5801                      │       │
│                 │  MAIN/FILE_SYNC/KAFKA_*/EXTERNAL_CDC       │       │
│                 │  (仅外部源→Iceberg/TimescaleDB 搬运)        │       │
│                 └───────────────────────────────────────────┘       │
│                                              │                      │
│  ┌───────────┐    ┌───────────┐              │                      │
│  │ PostgreSQL│    │  Doris    │◄─────────────┘                      │
│  │  :5432    │    │  FE:9030  │                                     │
│  │  本体元数据 │    │  BE:9050  │ 索引层                              │
│  └───────────┘    └───────────┘                                     │
│       │                │                                             │
│       ▼                ▼                                             │
│  ┌─────────────────────────────────────┐                           │
│  │              Trino  :8080           │                           │
│  │  Gravitino Connector → Iceberg     │                            │
│  │  联邦查询引擎                        │                            │
│  └─────────────────────────────────────┘                           │
│       ▲                                                            │
│       │                                                            │
│  ┌────┴────┐                                                      │
│  │  API    │  FastAPI  :8000                                       │
│  │  Routes │  /health /metrics                                     │
│  └─────────┘                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 服务端口映射

| 服务 | 内部端口 | 对外端口 | 健康检查 |
|------|---------|---------|----------|
| RustFS | 9000(API)/9002(Console) | 9000/9002 | `curl :9000/health` |
| PostgreSQL (PostGIS+TimescaleDB) | 5432 | 5432 | `pg_isready -U ontology` |
| Gravitino Iceberg REST | 9001 | 9001 | `/iceberg/v1/config` → JSON |
| Gravitino | 8090 | 8090 | `/metrics` → 200 |
| Doris FE | 9030(MySQL) | 9030 | `SHOW FRONTENDS` |
| Doris BE | 9050 | 9050 | `SHOW BACKENDS` |
| Trino | 8080 | 8080 | `/v1/info` |
| SeaTunnel | 5801(REST) | 5801 | Zeta 集群自愈 |
| Kafka | 9092 | 9092 | — |
| Neo4j 🆕 (profile=graph) | 7687(Bolt)/7474(HTTP) | 7687/7474 | `/` → 200 |
| migrate 🆕 (init 容器) | — | — | `alembic upgrade head` 一次性 |
| API | 8000 | 8000 | `/health` → `{"status":"ok"}` |

---

## 9. 降级策略矩阵

| 故障 | 影响范围 | 降级行为 | 恢复 |
|------|---------|---------|------|
| **Doris 不可用** | 物理对象索引过滤 | Trino 直接扫描 Iceberg（带分区裁剪） | Doris 恢复后自动切回 |
| **IcebergStore 不可用** | 物理对象点查 | Trino 按 ID 查询 | 自动重试，指数退避 |
| **Gravitino 不可用（物理表）** | 物理表查询 | 绕过权限检查（缓存表路由） | Gravitino 恢复后恢复 |
| **Gravitino 不可用（虚拟表）** | 虚拟表查询 | 直接失败（无降级路径） | — |
| **索引同步延迟** | Doris 数据滞后 | 自动降级为 Trino 全表扫 | 延迟恢复后切回 |
| **SeaTunnel 崩溃** | 外部数据源接入写入 | 源端 CDC offset 不提交；Action 业务写入不受影响（走 outbox，不经 SeaTunnel） | 恢复后追赶上 |
| **RustFS 不可用** | Iceberg 写入失败 | SeaTunnel 自动重试（指数退避，最多 10 次） | 磁盘空间/进程恢复 |
| **PostgreSQL 不可用** | 本体定义 | 已注册物理表不受影响 | pg_isready 恢复 |

---

## 10. 异常层级树

```
OntologyError (base)
├── NotFoundError          → HTTP 404  — 资源不存在
├── ConflictError          → HTTP 409  — 唯一约束 / OCC 冲突
├── ValidationError        → HTTP 422  — 参数校验失败
├── ForbiddenError         → HTTP 403  — 权限拒绝
├── DorisUnavailableError  → 触发降级  — Doris 不可用
├── IcebergUnavailableError→ 触发降级  — Iceberg 不可用
├── GravitinoUnavailableError → 触发降级 — Gravitino 不可用
├── OutboxError            → DLQ       — Outbox 永久失败
└── ActionAlreadyExecutedError → 幂等性 — 重复提交
```

### 错误响应格式

```json
{
  "detail": "ObjectType in ontology hr not found: nonexistent",
  "error_type": "NotFoundError"
}
```

---

## 11. 测试全景图

**总计**: 130 个后端测试文件（~34,711 行）+ 22 个前端测试文件（169 用例），1268 个后端测试函数。详见 [implementation-status.md §十二.6](./implementation-status.md)。

### 单元测试 (49 个文件)

| 层级 | 文件 | 测试内容 |
|------|------|---------|
| **Core** | `test_ontology_models.py` | ORM 模型映射、默认值、关系 |
| **Core** | `test_action_schemas.py` | Action pydantic 校验 |
| **Layers** | `test_postgres_meta_store.py` | PostgresMetaStore CRUD + 异常 |
| **Layers** | `test_gravitino_registry.py` | GravitinoRegistry API + 降级 |
| **Layers** | `test_iceberg_store.py` | IcebergStore 读写 + 时间旅行 |
| **Layers** | `test_doris_index_store.py` | DorisIndexStore 查询 + DDL |
| **Layers** | `test_trino_query_engine.py` | TrinoQueryEngine SQL 执行 |
| **Layers** | `test_sea_tunnel_engine.py` | SeaTunnelEngine 流水线生命周期 |
| **Layers** | `test_sea_tunnel_cdc.py` | SeaTunnel CDC 流水线 |
| **Services** | `test_ontology_service.py` | OntologyService CRUD + MANAGED/VIRTUAL 分流 |
| **Services** | `test_object_query_service.py` | ObjectQueryService 路由 + 降级 |
| **Services** | `test_action_service.py` | ActionService 幂等性 + OCC + 原子事务 |
| **Services** | `test_action_rule_engine.py` | ActionRuleEngine derivation/constraint |
| **Services** | `test_action_validator.py` | ActionValidator 参数校验 |
| **Services** | `test_conflict_detector.py` | ConflictDetector OCC 冲突检测 |
| **Services** | `test_outbox_executor.py` | OutboxExecutor 消费 + 重试 + DLQ |
| **Services** | `test_write_back_manager.py` | WriteBackManager write-back 写入 |
| **Services** | `test_ingestion_filter.py` | 数据接入过滤逻辑 |
| **Services** | `test_time_travel_service.py` | TimeTravelService 历史查询 |
| **Services** | ~~`test_virtual_table_service.py`~~ | ~~VirtualTableService~~ 已删除 |
| **Middleware** | `test_error_handler.py` | 异常→HTTP 映射 |

### 集成测试 (1 个文件)

| 文件 | 测试内容 |
|------|---------|
| `integration/test_routes.py` | FastAPI 路由集成：Ontology CRUD + Action 执行 + 错误响应格式 |

### 系统测试 (1 个文件)

| 文件 | 场景 |
|------|------|
| `system/test_scenarios.py` | S1: Ontology 定义; S2: 物理对象查询; S3: 虚拟对象查询; S4: 时间旅行 |

> 系统测试默认跳过（`@pytest.mark.system`），需设置 `RUN_SYSTEM_TESTS=1`。

### 测试策略分层

```
        /\
       /  \  E2E 系统测试    ← 少量 (1 文件), 全栈 Docker, 默认跳过
      /    \  集成测试        ← 1 文件, FastAPI TestClient
     /______\____
    /  单元测试    ← ~95% (26 文件), Mock 所有外部依赖
   /______________\__
  / 静态分析(ruff+mypy) ← CI 第一关, 零容忍
 /____________________\
```

---

## 12. 运维红线与告警

### 系统运行状态向量

| 指标 | 正常阈值 | 预警阈值 | 告警阈值 |
|------|---------|---------|---------|
| Doris 索引同步延迟 | < 30s | 30s ~ 60s | > 60s |
| Trino 查询 P95 延迟 | < 500ms | 500ms ~ 1s | > 1s |
| 物理对象查询 P95 延迟 | < 200ms | 200ms ~ 500ms | > 500ms |
| 查询成功率 | > 99.9% | 99% ~ 99.9% | < 99% |
| PostgreSQL 连接池使用率 | < 50% | 50% ~ 80% | > 80% |
| SeaTunnel 主流水线吞吐 | > 10K rows/s | 5K ~ 10K | < 5K |

### 告警规则

| 告警项 | 触发条件 | 严重级别 |
|--------|---------|---------|
| SeaTunnel 主流水线崩溃 | `seatunnel_main_status != RUNNING` 持续 > 60s | P0 |
| RustFS 不可用 | `rustfs_health != ok` | P0 |
| PostgreSQL 不可用 | `pg_health != ok` | P0 |
| Trino 不可用 | `trino_health != ok` | P0 |
| Gravitino 不可用 | `gravitino_health != ok` | P0 |
| Doris 不可用 | `doris_fe_health != ok` | P1 |
| 索引同步延迟 > 60s | `doris_sync_lag > 60s` | P1 |
| 查询成功率 < 99% | `query_success_rate < 0.99` | P1 |
| PostgreSQL 连接池 > 80% | `pg_pool_usage > 0.8` | P2 |

### 健康检查配置

| 服务 | 命令 | 间隔 | 超时 | 重试 |
|------|------|------|------|------|
| RustFS | `curl -sf http://localhost:9000/health` | 15s | 5s | 5 |
| PostgreSQL | `pg_isready -U ontology` | 15s | 5s | 5 |
| Iceberg REST | — (默认) | — | — | — |
| Gravitino | — (默认) | — | — | — |
| Doris FE | — (默认) | — | — | — |
| Doris BE | — (默认) | — | — | — |
| Trino | — (默认) | — | — | — |
| SeaTunnel | — (Zeta 集群自愈) | — | — | — |

### Docker 内存限制

| 服务 | mem_limit | mem_reservation | cpus |
|------|-----------|----------------|------|
| PostgreSQL | 512m | 256m | 1.0 |
| Gravitino | 1g | 512m | 1.5 |
| RustFS | 1g | 512m | 1.5 |
| Iceberg REST | 1g | 512m | 1.5 |
| SeaTunnel | 512m | 256m | 1.0 |
| Doris FE/BE | 1g | 512m | 1.5 |
| Trino | 1g | 512m | 1.5 |
| API | 512m | 256m | 1.0 |

### 重启策略

所有服务统一配置 `restart: unless-stopped`。

### Prometheus Metrics 采集

```
# HTTP 请求
http_request_total{method, path, status}
http_request_duration_seconds{method, path, status}

# 层调用
layer_call_total{layer, method, status}
layer_call_duration_seconds{layer, method, status}

# 查询
query_total{query_type, storage_type, status}
query_duration_seconds{query_type, storage_type}
```

---

## 13. 工程附录

### 13.1 对比 Palantir Foundry

| Palantir Foundry 能力 | Gaia 实现 | 组件 |
|-----------------------|-----------|------|
| **Ontology Management** | Ontology / ObjectType / PropertyDef | PostgreSQL → PostgresMetaStore |
| **Object Storage (OSv2)** | Iceberg 全量明细 | pyiceberg → IcebergStore |
| **Index (OSv2 Index Runtime)** | Doris 索引加速（主键 + 倒排 + 向量） | aiomysql → DorisIndexStore |
| **Query Engine** | Trino 联邦查询 | trino-python-client → TrinoQueryEngine |
| **PipelineBuilder** | SeaTunnel 流水线 | Jinja2 + httpx → SeaTunnelEngine |
| **Catalog** | Gravitino 物理资产/View/RBAC | httpx → GravitinoRegistry |
| **Actions** | ActionService + ActionRuleEngine + Outbox | 全 PG 事务 + OCC + CDC |
| **Time Travel** | Iceberg FOR VERSION AS OF | Trino + Iceberg 快照 |
| **Virtual Tables** | Virtual Table(外部联邦代理) + Trino 联邦查询 | 外部表定位符 + 联邦查询 |

### 13.2 Python 编码规范（强制）

| # | 规范 | 说明 |
|---|------|------|
| 1 | **SQLAlchemy 2.0 async ORM** | `DeclarativeBase` + `select()` 风格，禁止裸 SQL |
| 2 | **pydantic v2 API 校验** | 请求/响应校验，与 ORM 模型分离 |
| 3 | **类型注解全覆盖** | `mypy --strict` 通过，禁止 `Any` 泛滥 |
| 4 | **`datetime.now(UTC)`** | Python 3.12+ 标准，禁止 `utcnow()` |
| 5 | **`uuid.uuid4().hex` 主键** | 32 字符，分布式友好，禁止自增 ID |
| 6 | **`async` 全链路** | 数据库、HTTP、文件 IO 全部异步 |
| 7 | **`ruff` 格式化 + lint** | 零容忍，一行 120 字符 |

### 13.3 关键技术模式

```python
# 主键 + 时间戳
def new_uuid() -> str:        return uuid.uuid4().hex
def utcnow() -> datetime:     return datetime.now(UTC)

# SQLAlchemy 查询（禁止裸 SQL）
stmt = select(OntologyModel).where(OntologyModel.api_name == api_name)
result = await session.execute(stmt)
return result.scalar_one_or_none()

# pydantic 与 ORM 分离
pydantic_obj = OntologyCreate.model_validate(orm_obj)    # ORM → pydantic
orm_obj = OntologyModel(**schema_obj.model_dump())       # pydantic → ORM

# 异常路径
class OntologyError(Exception): ...
class NotFoundError(OntologyError): ...   # 404
class ConflictError(OntologyError): ...   # 409
```

### 13.4 ADR 索引

| ADR # | 决策 | 替代方案 |
|-------|------|----------|
| ADR-001 | Doris 作在线读主源（存全量属性） | Trino 直接算 / Elasticsearch / 纯索引层 |
| ADR-002 | SeaTunnel 而非 Flink | Flink / Spark Streaming |
| ADR-003 | RustFS 而非 MinIO | MinIO（已弃用）/ Ceph / S3 |
| ADR-004 | PostgreSQL 存业务本体元数据 | MySQL / etcd |
| ADR-005 | ObjectType.properties 用 JSONB | 关系表 / 纯文档数据库 |
| ADR-006 | Python + FastAPI | TypeScript / Go |
| ADR-007 | Iceberg REST Catalog 访问通道 (pyiceberg 子类化 + Trino 双通道) | 直连 REST / Hive metastore |
| ADR-008 | Iceberg→Doris 索引同步路径 (BATCH 全量已通，STREAMING 增量 2026-07-06 实测可用，待拆分双模板) | 升级 SeaTunnel / Hadoop Catalog 强制 version-hint |
| ADR-009 | 本体工具层（22 工具 toolset + MCP/AG-UI/REST 三入口） | 手工封装工具 / MCP Resources |
| ADR-010 | 本体 HITL 审批机制 | pydantic-ai requires_approval / 自定义端点 |
| ADR-011 | Action P1（上下文注入/三层权限/CDL/Link mutation/版本管控/preview） | — |
| ADR-012 | 本体驱动自然语言查询 TextQL | 纯 LLM 直出 SQL / 自研 DSL 编译器 |
| ADR-013 | 前端 React Aria Components 作为 headless 行为层 | Ant Design/Mantine / Radix |
| ADR-014 | 多源异构数据融合连接器体系（25 种，6 大品类） | 自建 ConnectorRegistry/SPI |
| ADR-015 | AG-UI Agent 驱动图探索画布（图关联推理 + ObjectSet IR） | 保留 explore-plan 单步循环 / 画布工具全放后端 |
| adr-action-mutation | Action Mutation Mapping（声明式规则 + ValueSource + hydrate） | — |

### 13.5 代码统计

| 维度 | 文件数 | 行数 |
|------|--------|------|
| **后端源码 (src/ontology)** | 101 个 .py | ~27,756 |
| **后端测试 (tests)** | 130 个 .py | ~34,711 |
| **前端源码 (src/web-ui/src)** | 134 个 .ts/.tsx（含 79 个组件） | — |
| **前端测试** | 22 个 .test.ts/.tsx | 169 用例 |
| **后端测试函数** | — | 1268 |
| **ADR 实体文件** | 11 | — |

> 后端测试函数数 1268 为 `grep -rhE "^\s*(async )?def test_" tests/` 实测值（含参数化展开前）。

---

> 本文档于 2026-07-06 校准。组件真实状态以 [`implementation-status.md`](./implementation-status.md) 为唯一真相源。版本: 0.1.0
