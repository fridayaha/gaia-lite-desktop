# Palantir 风格开源分层架构 — 架构设计与评审（v5 终稿）

> **核心目标**：对标 Palantir Foundry Ontology 的 5 层架构，通过目录隔离 + 明确的组件边界实现层间解耦。Doris 作为索引加速层，Trino 作为主要查询引擎，PostgreSQL 作为业务元数据持久层。
>
> **工程方法论**：以中国航天系统工程总体设计思想为指导，坚持"一次成功"可靠性理念，贯彻技术状态管理、综合集成验证、全生命周期运维演进。

***

## 〇、总体要求（系统工程顶层设计）

### 0.1 系统关键能力等级（验收基线）

| 能力维度         | 指标                    | 目标值          | 测量方法                                                     |
| ------------ | --------------------- | ------------ | -------------------------------------------------------- |
| **物理对象查询延迟** | P95 响应时间              | < 200ms      | Doris 索引过滤 + IcebergStore 原生点查，端到端计时（不含网络往返，单次请求内层间调用累计） |
| **虚拟表查询延迟**  | P95 响应时间              | < 500ms      | Trino 联邦查询 Virtual Table 指向的外部表，含联邦 JOIN（3 表以内）        |
| **索引同步延迟**   | Iceberg 写入 → Doris 可见 | 外部接入：backfill 触发后秒级；Action 写入：≤1s（outbox INDEX effect → OutboxExecutor） | 外部接入由 ObjectIndexFunnel 从 Iceberg scan_latest 读后 upsert Doris；Action 写入经 outbox INDEX effect→OutboxExecutor 1s 轮询→DorisIndexStore.upsert（均不走 SeaTunnel） |
| **虚拟表查询并发**  | 稳态 QPS                | > 50         | Trino 集群 3 节点，混合读写负载下持续 5 分钟采样                           |
| **物理对象查询并发** | 稳态 QPS                | > 200        | 含 Doris 索引过滤，混合读写负载下持续 5 分钟采样                            |
| **数据写入吞吐**   | 单 Pipeline 吞吐         | > 10K rows/s | SeaTunnel 主流水线，1KB/row 典型行大小，持续 10 分钟采样                  |
| **时间旅行查询延迟** | P95 响应时间              | < 1s         | Trino `FOR VERSION AS OF`，单表、100 万行以内快照                  |
| **系统可用性**    | 核心查询链路                | 99.9%        | 月度统计，含降级策略生效时段，不含计划维护窗口                                  |

> **测量条件说明**：以上指标基于开发/测试环境（单机 Docker Compose）作为功能验证基线。生产环境需在目标硬件规格下重新标定，不作为本阶段强制验收条件。

### 0.2 功能-性能-可靠性三维分解矩阵

| Palantir 能力 | 主要贡献组件                              | 依赖条件                         | 风险等级 | 降级策略                                           |
| ----------- | ----------------------------------- | ---------------------------- | ---- | ---------------------------------------------- |
| **本体定义**    | PostgreSQL (元数据) + Gravitino (物理注册) | PG 可用 + Gravitino 可用         | 低    | PG 离线时本体定义不可用，已注册物理表不受影响                       |
| **物理对象查询**  | Doris (索引) → IcebergStore (全量)      | Doris 可用 + Iceberg 可用        | 中    | Doris 不可用时降级为 Trino 直接扫描 Iceberg（带分区裁剪）        |
| **虚拟对象查询**  | Gravitino (Virtual Table 联邦元信息) + Trino (执行) | Gravitino 可用 + Trino 可用      | 中    | Gravitino 不可用时虚拟表查询直接失败（无降级路径）                 |
| **时间旅行**    | Trino + Iceberg 快照                  | Trino 支持 `FOR VERSION AS OF` | 高    | 若 Gravitino Connector 不透传，改用 `iceberg_catalog` |
| **数据写入**    | SeaTunnel → Iceberg                 | SeaTunnel 可用 + RustFS 可用     | 中    | 写入失败不丢数据（源端保留 CDC offset），恢复后追赶                |
| **索引同步**    | ObjectIndexFunnel (外部接入) / OutboxExecutor (Action 写入) → Doris | Doris 可用      | 低    | 同步延迟增大时告警，物理查询自动降级为 Trino 全表扫（不经 SeaTunnel） |

### 0.3 总体架构师职责

1. **接口基线管控**：所有 Layer 类的公开方法签名（含 pydantic 模型）冻结后，任何修改需通过接口评审
2. **跨层协调**：当一层实现变更影响其他层时（如 Iceberg 表结构变更影响 Doris 索引表），由总体架构师协调同步更新
3. **指标监控**：持续跟踪 0.1 节的关键能力等级，任何指标劣化需触发根因分析
4. **技术债务管理**：维护已知技术债务清单，设定偿还触发条件

***

## 一、架构分层总览（对标 Palantir）

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Routes（HTTP 薄层）                            │
│  object_types.py / load_objects.py / aggregate.py / execute_action.py│
├──────────────────────────────────────────────────────────────────────┤
│                     Services（业务编排层）                             │
│  OntologyService / ObjectQueryService /        │
│  TimeTravelService / ActionService                                   │
│         ↓ 直接依赖层实现 ↓                                            │
├──────────────────────────────────────────────────────────────────────┤
│                   Layer Implementations（层实现）                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Catalog  │ │ Metadata │ │ Dataset  │ │  Index   │ │ Pipeline │   │
│  │  Layer   │ │  Layer   │ │  Layer   │ │  Layer   │ │  Layer   │   │
│  │Gravitino │ │PostgreSQL│ │ Iceberg  │ │  Doris   │ │SeaTunnel │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│                                                       ┌──────────┐   │
│                                                       │  Engine  │   │
│                                                       │  Trino   │   │
│                                                       └──────────┘   │
├──────────────────────────────────────────────────────────────────────┤
│                   Core Models（领域模型，纯类型）                       │
│  Ontology / Dataset / Index / Query / Pipeline                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.1 分层隔离原则

| 原则                       | 说明                                                                                                         |
| ------------------------ | ---------------------------------------------------------------------------------------------------------- |
| **每层独立目录，职责单一**          | `layers/catalog/`、`layers/metadata/`、`layers/dataset/`、`layers/index/`、`layers/pipeline/`、`layers/engine/` |
| **层间通过明确的类方法调用**         | 不引入 interface 抽象，直接依赖具体类                                                                                   |
| **替换组件 = 替换整个 layer 目录** | 换目录 + 改 services 的 import 路径                                                                               |
| **每层只做一件事**              | Catalog 管物理资产，Metadata 管业务元数据，Dataset 管持久化，Index 管加速，Pipeline 管流转，Engine 管查询                               |

### 1.2 核心硬约束

| # | 约束                                | 实现机制                                             |
| - | --------------------------------- | ------------------------------------------------ |
| 1 | Gravitino 仅管理物理数据资产               | 注册 Iceberg 表、Doris 外表、View 定义；RBAC 和血缘           |
| 2 | PostgreSQL 存储业务本体元数据              | Ontology、ObjectType、PropertyDef、LinkType 等全部领域模型 |
| 3 | 主数据统一在 RustFS/S3 + Iceberg        | Iceberg REST Catalog 指向 S3；外部数据经 SeaTunnel 主流水线写入，Action 操作态经 outbox ARCHIVE effect 异步 MERGE  |
| 4 | Doris 严格作为索引加速层                   | 仅存主键 + 索引列 + 常用热点属性，不存全量明细                       |
| 5 | Trino 作为主要查询引擎                    | 通过 Gravitino Connector 联邦查询 Iceberg，承载全量数据读取     |
| 6 | SeaTunnel 承担 PipelineBuilder（外部源→Iceberg 搬运） | SeaTunnel 只负责把外部数据源搬进 Iceberg/TimescaleDB；Iceberg→Doris/Neo4j/PostGIS 的写入不走 SeaTunnel，由 ObjectIndexFunnel + OutboxExecutor 直连各引擎（2026-07 去 SeaTunnel 化） |
| 7 | 移除 Redis                          | 用 Doris 自带缓存 + Iceberg ACID + 分区策略替代             |

### 1.3 组件版本矩阵

| 组件                   | 版本                             | 镜像 / 下载                              | 说明                                            |
| -------------------- | ------------------------------ | ------------------------------------ | --------------------------------------------- |
| **Apache Gravitino** | `1.3.0` (stable)               | `apache/gravitino:1.3.0`             | 物理资产注册、View、RBAC、血缘、审计                        |
| **Apache SeaTunnel** | `2.3.13` (stable) + **dev 分支** | `apache/seatunnel:2.3.13`            | Gravitino 集成（schema\_url）在 dev 分支，预计 3.0.0 发布 |
| **Apache Iceberg**   | `1.11.0`                       | REST Catalog                         | 2026-05-19 发布，远程 Scan Planning、ETag、幂等 Key    |
| **Apache Doris**     | `4.0.6`                        | `apache/doris:4.0.6-fe` / `4.0.6-be` | 索引加速层，倒排索引 + 向量索引                             |
| **RustFS**           | `V1` (latest)                  | `rustfs/rustfs:latest`               | S3 兼容对象存储，Iceberg 存储底座                        |
| **Apache Trino**     | `478`                          | `trinodb/trino:478`                  | 主要查询引擎，通过 Gravitino Connector 联邦查询            |
| **PostgreSQL**       | `16`                           | `postgres:16`                        | 业务本体元数据持久层                                    |

> **注意**：MinIO 开源版已于 2025 年 12 月进入维护模式，替换为 RustFS V1。开发/测试环境直接使用，生产环境可切换云厂商 S3。

### 1.4 Gravitino ↔ SeaTunnel 集成说明

SeaTunnel 与 Gravitino 的深度集成能力（`schema_url` 自动拉取表结构）在 **dev 分支**开发中（PR #10402），预计 SeaTunnel 3.0.0 正式发布。

**当前集成方式**：

- SeaTunnel 稳定版 (2.3.13)：配置中显式声明 schema
- SeaTunnel dev 分支：支持 `schema_url` 指向 Gravitino REST API，自动拉取

**docker-compose 处理**：Gravitino 1.3.0 + SeaTunnel 2.3.13，手动配置 schema。待 3.0.0 发布后升级。

***

## 二、组件定位与对标

### 2.1 组件职责矩阵

| 组件                      | 职责                                                 | 不做的事           | Palantir 对标              |
| ----------------------- | -------------------------------------------------- | -------------- | ------------------------ |
| **PostgreSQL**          | 存储业务本体元数据（Ontology、ObjectType、PropertyDef 等全部领域模型） | 不存物理表元数据、不参与查询 | OMS 元数据库                 |
| **Apache Gravitino**    | 物理资产注册（Iceberg 表、Doris 外表、View）、RBAC、血缘、审计         | 不存业务本体元数据      | Catalog                  |
| **Apache SeaTunnel**    | 外部数据源采集/清洗/转换、写入 Iceberg/TimescaleDB（不参与 Iceberg→Doris 同步）                   | 不做元数据管理、不做查询路由、不做 Iceberg→Doris/Neo4j 同步 | PipelineBuilder（搬运层）          |
| **RustFS/S3 + Iceberg** | 存全量业务明细、版本快照、Schema 演进、ACID                        | 不做在线查询、不做检索加速  | 底层 Durable Storage       |
| **Apache Doris 4.x**    | 索引加速：主键 + 索引列 + 热点属性，全文/向量检索                       | 不存全量明细、不作为数据基准 | OSv2 Index Runtime（索引部分） |
| **Apache Trino**        | 主要查询引擎：联邦查询、全量数据加载、View 执行                         | 不做主数据存储        | Query Engine             |

### 2.2 Doris ↔ Iceberg ↔ Trino 职责边界

| 维度        | Doris           | Iceberg           | Trino       |
| --------- | --------------- | ----------------- | ----------- |
| **存储内容**  | 主键 + 索引列 + 热点属性 | 全量业务明细 + 历史快照     | 无存储，纯计算     |
| **读流量**   | 索引过滤，返回 ID 列表   | 按 ID 批量返回全量属性     | 统一查询入口，联邦路由 |
| **写流量**   | 仅接收 Iceberg 同步  | 唯一写入入口（外部源经 SeaTunnel 写入；Action 经 outbox ARCHIVE effect 异步 MERGE） | 无           |
| **数据一致性** | 最终一致（秒/分钟级）     | 强一致（ACID）         | 读取时态        |
| **索引**    | 主键 + 倒排 + 向量    | 无                 | 无           |
| **版本**    | 仅当前版本           | 全量历史快照            | 按需读取快照      |

### 2.3 两种对象类型及其查询路径

| 类型                    | 底层存储                          | 查询路径                               |
| --------------------- | ----------------------------- | ---------------------------------- |
| **托管对象类型 (MANAGED)** | 有 Iceberg 物理表                 | Doris 索引过滤 → IcebergStore 原生点查全量加载 |
| **虚拟对象类型 (VIRTUAL)**  | 无 Iceberg 表，Virtual Table 指向外部表 | 直接 Trino 联邦查询外部表（无 Doris 参与）    |

### 2.4 Doris 索引表结构示例

```sql
CREATE TABLE doris_orders_index (
    order_id    BIGINT,           -- 主键
    status      VARCHAR(50),      -- 索引列（倒排）
    region      VARCHAR(100),     -- 索引列（倒排）
    amount      DECIMAL(18,2),    -- 热点属性（常用过滤/排序）
    created_at  DATETIME,         -- 热点属性（时间范围过滤）
    embedding   ARRAY<FLOAT>,     -- 向量列
    INDEX idx_status (status) USING INVERTED,
    INDEX idx_region (region) USING INVERTED,
    INDEX idx_vector (embedding) USING VECTOR
)
DUPLICATE KEY (order_id)
PARTITION BY RANGE (created_at) ();
```

> **红线**：Doris 索引表不存储 `description`、`customer_id`、`product_id` 等全量明细字段。全量属性从 Iceberg 通过 IcebergStore 原生 API 获取。

***

## 三、领域模型设计（core/models/）

> 纯类型定义，零外部依赖。参考 Palantir OMS 13 张核心表结构，按需选取关键实体建模。以下伪代码使用 Python 风格（pydantic BaseModel + 类型注解）描述领域模型结构。

### 3.1 Ontology 模型

```python
# core/models/ontology.py

"""顶层本体容器（对标 Palantir Ontology + Space）"""
class Ontology(BaseModel):
    id: str                    # UUID v4
    api_name: str              # 全局唯一标识
    display_name: str
    description: str
    rid: str                   # 资源标识符
    created_at: datetime
    updated_at: datetime

"""对象类型定义（对标 Palantir ObjectType）"""
class ObjectType(BaseModel):
    id: str                    # UUID v4
    ontology_id: str           # 所属 Ontology
    api_name: str              # 在 Ontology 内唯一
    display_name: str
    description: str
    primary_key: str           # 主键属性名（非 FK，直接存属性 api_name）
    title_property: str        # 标题属性名（非 FK，直接存属性 api_name）
    storage_type: Literal["MANAGED", "VIRTUAL"]  # 托管对象 vs 虚拟对象
    visibility: Literal["NORMAL", "PROMINENT", "HIDDEN"]
    status: Literal["ACTIVE", "ENDORSED", "EXPERIMENTAL", "DEPRECATED"]
    properties: list[PropertyDef]
    links: list[LinkTypeDef]
    created_at: datetime
    updated_at: datetime

"""属性定义（对标 Palantir Property）"""
class PropertyDef(BaseModel):
    id: str                    # UUID v4
    object_type_id: str        # 所属 ObjectType
    api_name: str              # 在 ObjectType 内唯一
    display_name: str
    description: str
    data_type: DataType
    is_primary_key: bool
    is_title_property: bool
    nullable: bool
    indexed: bool              # 是否作为索引列同步到 Doris
    physical_mapping: PhysicalColumnRef | None  # 指向底层物理列的映射（MANAGED 类型必填）
    created_at: datetime
    updated_at: datetime

"""支持的属性数据类型（剪裁后，移除 MARKING/CIPHER/TIME_SERIES）"""
DataType = Literal[
    "STRING", "INTEGER", "SHORT", "LONG", "BOOLEAN", "BYTE",
    "FLOAT", "DOUBLE", "DECIMAL",
    "DATE", "TIMESTAMP",
    "ARRAY", "STRUCT", "VECTOR",
    "GEOPOINT", "GEOSHAPE",
    "MEDIA_REFERENCE", "ATTACHMENT",
]

"""物理列引用（桥接 Ontology → Dataset）"""
class PhysicalColumnRef(BaseModel):
    catalog_name: str
    schema_name: str
    table_name: str
    column_name: str

"""共享属性（全局可复用，对标 Palantir SharedProperty）"""
class SharedProperty(BaseModel):
    id: str                    # UUID v4
    api_name: str              # 全局唯一
    display_name: str
    description: str
    data_type: DataType
    created_at: datetime
    updated_at: datetime

"""关系类型定义（对标 Palantir LinkType）"""
class LinkTypeDef(BaseModel):
    id: str                    # UUID v4
    ontology_id: str
    api_name: str              # 在 Ontology 内唯一
    display_name: str
    description: str
    source_object_type_id: str
    target_object_type_id: str
    foreign_key_property_api_name: str | None
    cardinality: Literal["ONE", "MANY"]
    direction: Literal["OUTGOING", "INCOMING"]
    created_at: datetime
    updated_at: datetime

"""操作类型定义（对标 Palantir ActionType）"""
class ActionType(BaseModel):
    id: str                    # UUID v4
    ontology_id: str
    api_name: str              # 在 Ontology 内唯一
    display_name: str
    description: str
    affected_object_type_id: str | None  # ON DELETE SET NULL
    parameters: dict[str, Any]           # JSONB
    rules: dict[str, Any]                # JSONB
    submission_criteria: dict[str, Any]  # JSONB
    status: Literal["ACTIVE", "DEPRECATED"]
    created_at: datetime
    updated_at: datetime

"""接口类型（Preview，对标 Palantir InterfaceType）"""
class InterfaceType(BaseModel):
    id: str                    # UUID v4
    ontology_id: str
    api_name: str
    display_name: str
    description: str
    extends_interface_ids: list[str]  # JSONB，多重继承
    status: Literal["EXPERIMENTAL"]
    properties: list[InterfaceProperty]
    created_at: datetime
    updated_at: datetime

"""接口属性（对标 Palantir InterfaceProperty）"""
class InterfaceProperty(BaseModel):
    id: str                    # UUID v4
    interface_type_id: str
    api_name: str
    display_name: str
    description: str
    data_type: DataType
    is_shared: bool
    created_at: datetime
    updated_at: datetime

"""值类型（领域语义包装器，对标 Palantir ValueType）"""
class ValueType(BaseModel):
    id: str                    # UUID v4
    ontology_id: str
    api_name: str
    display_name: str
    description: str
    base_type: DataType
    constraints: dict[str, Any]  # JSONB
    created_at: datetime
    updated_at: datetime

"""结构化属性类型（全局，对标 Palantir Struct）"""
class Struct(BaseModel):
    id: str                    # UUID v4
    api_name: str              # 全局唯一
    display_name: str
    description: str
    fields: list[StructField]  # JSONB，深度为 1，不可嵌套
    created_at: datetime
    updated_at: datetime

class StructField(BaseModel):
    name: str
    data_type: DataType
    nullable: bool

"""对象类型分组（对标 Palantir ObjectTypeGroup）"""
class ObjectTypeGroup(BaseModel):
    id: str                    # UUID v4
    ontology_id: str
    api_name: str
    display_name: str
    description: str
    created_at: datetime
    updated_at: datetime

"""分支管理（对标 Palantir Branch）"""
class Branch(BaseModel):
    id: str                    # UUID v4
    ontology_id: str
    name: str                  # 在 Ontology 内唯一
    is_main: bool
    status: Literal["ACTIVE", "MERGED", "CLOSED"]
    created_at: datetime
    updated_at: datetime
```

### 3.1.1 关键设计模式（对齐 Palantir OMS）

| 模式                  | 说明                                                                                                    |
| ------------------- | ----------------------------------------------------------------------------------------------------- |
| **主键**              | 全部使用 UUID v4，不依赖数据库自增                                                                                 |
| **外键删除策略**          | 统一 `ON DELETE CASCADE`，唯一例外是 `ActionType.affected_object_type_id` 用 `SET NULL`                        |
| **唯一约束**            | `api_name` 在所属范围内唯一（Ontology 内 / 全局）                                                                  |
| **枚举存储**            | 全部存为 `VARCHAR`（非数据库 ENUM），pydantic `Literal` 类型做校验层                                                   |
| **JSONB 列**         | `parameters`、`rules`、`submission_criteria`、`constraints`、`fields`、`extends_interface_ids` 等灵活结构用 JSON |
| **纯关联表**            | `ObjectType ↔ SharedProperty` 多对多通过独立关联表，复合主键                                                         |
| **ID vs api\_name** | 内部关联用 UUID（FK），业务接口用 `api_name`（String 路径参数）                                                          |
| **storage\_type**   | MANAGED（有 Iceberg 表）vs VIRTUAL（Virtual Table 指向外部表），决定查询路径                                             |
| **indexed**         | 标记属性是否同步到 Doris 索引表                                                                                   |

### 3.1.2 ER 关系简图

```
ontologies (1) ──┬── (N) object_types ──┬── (N) properties
                 │                      ├── (N) object_type_shared_properties ── (N) shared_properties
                 │                      └── (N) link_types (source/target)
                 ├── (N) action_types
                 ├── (N) interface_types ── (N) interface_properties
                 ├── (N) value_types
                 ├── (N) object_type_groups
                 └── (N) branches

structs          (全局，无 FK 关联)
shared_properties (全局，无 FK 关联)
```

### 3.2 Dataset 模型

```python
# core/models/dataset.py

class Dataset(BaseModel):
    name: str
    schema: DatasetSchema
    storage_location: str
    partition_spec: list[PartitionField]

class DatasetSchema(BaseModel):
    columns: list[ColumnDef]

class ColumnDef(BaseModel):
    name: str
    type: str
    nullable: bool

class PartitionField(BaseModel):
    source_column: str
    transform: Literal["identity", "year", "month", "day", "hour", "bucket"]
    transform_param: int | None = None

class DatasetSnapshot(BaseModel):
    snapshot_id: int
    timestamp: int
    operation: Literal["append", "overwrite", "delete"]
    summary: dict[str, str]

class WriteResult(BaseModel):
    snapshot: DatasetSnapshot
    rows_written: int
```

### 3.3 Index 模型

```python
# core/models/index.py

"""索引字段定义"""
class IndexField(BaseModel):
    name: str
    index_type: Literal["PRIMARY_KEY", "INVERTED", "VECTOR", "RANGE"]
    vector_config: VectorConfig | None = None

class VectorConfig(BaseModel):
    dimension: int
    metric: Literal["L2", "COSINE", "IP"]

"""索引表定义"""
class IndexTable(BaseModel):
    object_type_api_name: str
    source_dataset: str
    fields: list[IndexField]
    partition_by: list[str]

"""索引查询请求
   vector_search 和 full_text_search 互斥，同时指定时优先使用向量检索
"""
class IndexQuery(BaseModel):
    object_type_api_name: str
    filters: list[IndexFilter]
    vector_search: VectorSearch | None = None
    full_text_search: FullTextSearch | None = None
    limit: int
    offset: int

class VectorSearch(BaseModel):
    field: str
    vector: list[float]
    top_k: int

class FullTextSearch(BaseModel):
    field: str
    query: str

IndexFilter = (
    FilterEq | FilterNeq | FilterIn | FilterRange | FilterContains
)

class FilterEq(BaseModel):
    field: str
    op: Literal["eq"]
    value: str | int | float

class FilterNeq(BaseModel):
    field: str
    op: Literal["neq"]
    value: str | int | float

class FilterIn(BaseModel):
    field: str
    op: Literal["in"]
    values: list[str | int | float]

class FilterRange(BaseModel):
    field: str
    op: Literal["range"]
    min: int | float | None = None
    max: int | float | None = None

class FilterContains(BaseModel):
    field: str
    op: Literal["contains"]
    value: str

"""索引查询结果（仅返回 rid 列表）"""
class IndexResult(BaseModel):
    rids: list[str]
    total: int
```

### 3.4 Query 模型

```python
# core/models/query.py

class ObjectSet(BaseModel):
    object_type_api_name: str
    rids: list[str] | None = None
    filter: QueryFilter | None = None

QueryFilter = (
    FilterEq | FilterAnd | FilterOr | FilterSearchAround
)

class FilterEq(BaseModel):
    type: Literal["eq"]
    field: str
    value: Any

class FilterAnd(BaseModel):
    type: Literal["and"]
    filters: list[QueryFilter]

class FilterOr(BaseModel):
    type: Literal["or"]
    filters: list[QueryFilter]

class FilterSearchAround(BaseModel):
    type: Literal["search_around"]
    link_type_api_name: str
    source_object_set: ObjectSet

class LoadObjectsRequest(BaseModel):
    object_set: ObjectSet
    properties: list[str]
    order_by: OrderBy | None = None
    limit: int
    offset: int
    as_of_snapshot_id: int | None = None  # 时间旅行：指定快照 ID

class OrderBy(BaseModel):
    field: str
    direction: Literal["ASC", "DESC"]

class AggregationRequest(BaseModel):
    object_set: ObjectSet
    metrics: list[AggregationMetric]
    group_by: list[str] | None = None

class AggregationMetric(BaseModel):
    field: str
    func: Literal["count", "sum", "min", "max", "avg", "cardinality"]
    alias: str | None = None
```

### 3.5 Pipeline 模型

```python
# core/models/pipeline.py

class PipelineDef(BaseModel):
    name: str
    type: Literal["MAIN", "FILE_SYNC", "KAFKA_INGESTION", "KAFKA_TIMESERIES", "EXTERNAL_CDC"]  # INDEX_SYNC 已废弃（去 SeaTunnel 化）
    source: PipelineSource
    transforms: list[PipelineTransform]
    sink: PipelineSink

class PipelineSource(BaseModel):
    type: Literal["mysql-cdc", "kafka", "postgres-cdc", "file", "iceberg-incremental"]
    config: dict[str, Any]

class PipelineTransform(BaseModel):
    type: Lil["field-mapping", "filter", "mask", "projection"]
    config: dict[str, Any]

class PipelineSink(BaseModel):
    type: Literal["iceberg", "doris"]
    config: dict[str, Any]

class PipelineStatus(BaseModel):
    name: str
    state: Literal["RUNNING", "STOPPED", "FAILED"]
    last_checkpoint: int | None = None
    records_processed: int
```

***

## 四、层实现（layers/）

> 每层是一个独立目录，直接封装对应开源组件的原生 API。以下伪代码使用 Python 风格（`async def` +型注解 + snake\_case 命名）描述各层公开方法签名。

### 4.1 Catalog 层 — GravitinoRegistry

```python
# layers/catalog/gravitino_registry.py

"""
物理资产注册中心
职责：Iceberg 表注册、Doris 外表注册、View 定义、RBAC、血缘
不存储业务本体元数据（本体元数据在 PostgresMetaStore）
"""
class GravitinoRegistry:
    def __init__(self, client: GravitinoClient) -> None: ...

    # ── Dataset 挂载 ──
    async def register_dataset(self, dataset:-> None: ...
    async def get_dataset(self, name: str) -> Dataset: ...

    # ── 虚拟表（Virtual Table，外部联邦代理） ──
    # create_view 已删除（Gravitino SQL View 线路废弃）
    async def is_view(self, name: str) -> bool: ...          # 运行时探测，保留
    async def get_table_columns(...) -> list[dict]: ...      # 联邦拉列，VIRTUAL schema 用

    # ── 权限校验（基于 Gravitino RBAC） ──
    # 当前阶段仅支持对象类型级权限（read/write），属性级权限和基于 visibility 的过滤留待后续迭代
    async def check_accesstype_api_name: str, operation: Literal["read", "write"]) -> bool: ...

    # ── 表路由解析 ──
    async def resolve_physical_table(self, object_type_api_name: str) -> dict[str, str]: ...
```

### 4.2 Metadata 层 — PostgresMetaStore

```python
# layers/metadata/postgres_meta_store.py

"""
业务本体元数据持久层
存储全部领域模型：Ontology、ObjectType、PropertyDef、LinkType 等

当前阶段：ObjectType.properties 和 links 以 JSONB 存储，保持灵活
后续可降级为es 表、links 表），通过 object_type_id FK 关联
以支持属性级粒度的查询、审计和版本控制
"""
class PostgresMetaStore:
    def __init__(self, db: AsyncConnectionPool) -> None: ...

    # ── Ontology 容器 ──
    async def create_ontology(self, ontology: Ontology) -> None: ...
    async def get_ontology(self, api_name: str) -> Ontology | None: ...
    async def list_ontologies(self) -> list[Ontology]: ...

    # ── ObjectType ──
    async def create_object_type(self,  ObjectType) -> None: ...
    async def get_object_type(self, ontology_api_name: str, api_name: str) -> ObjectType | None: ...
    async def list_object_types(self, ontology_api_name: str) -> list[ObjectType]: ...
    async def update_object_type(self, id: str, updates: dict[str, Any]) -> None: ...

    # ── Property ──
    async def add_property(self, object_type_id: str, prop: PropertyDef) -> None: ...
    async def get_properties(self, object_type_id: str) -> list[PropertyDef]: ...

    # ── ty ──
    async def create_shared_property(self, prop: SharedProperty) -> None: ...
    async def link_shared_property(self, object_type_id: str, shared_property_id: str) -> None: ...

    # ── LinkType ──
    async def create_link_type(self, link: LinkTypeDef) -> None: ...
    async def get_link_types(self, ontology_api_name: str) -> list[LinkTypeDef]: ...

    # ── ActionType ──
    async def create_action_type(self, action: ActionType) -> None: ...
    async def get_action_type(self, logy_api_name: str, api_name: str) -> ActionType | None: ...

    # ── InterfaceType ──
    async def create_interface_type(self, iface: InterfaceType) -> None: ...

    # ── ValueType ──
    async def create_value_type(self, vt: ValueType) -> None: ...

    # ── Struct ──
    async def create_struct(self, struct: Struct) -> None: ...

    # ── ObjectTypeGroup ──
    async def create_group(self, group: ObjectTypeGroup) -> None: ...

    # ── Branch ──
    async def crnch(self, branch: Branch) -> None: ...
```

### 4.3 Dataset 层 — IcebergStore

```python
# layers/dataset/iceberg_store.py

class IcebergStore:
    def __init__(self, catalog: IcebergRESTCatalog) -> None: ...

    async def append(self, dataset: str, rows: list[dict[str, Any]]) -> WriteResult: ...
    async def overwrite(self, dataset: str, rows: list[dict[str, Any]]) -> WriteResult: ...

    # 按主键批量加载全量属性（使用 Iceberg 原生 TableScan + Expression API，不走 Trino SQL）
    ad_by_ids(self, dataset: str, ids: list[str], columns: list[str]) -> list[dict[str, Any]]:
        # 使用 Iceberg RowFilter: id IN (...)，比 Trino SQL IN 子句更高效
        # 避免 ID 列表过大时 SQL 拼接膨胀
        ...

    async def load_by_ids_as_of(
        self, dataset: str, ids: list[str], columns: list[str], snapshot_id: int
    ) -> list[dict[str, Any]]: ...

    async def scan_as_of(
        self, dataset: str, columns: list[str], snapshot_id: int, limit: int
    ) -> list[dict[]]: ...

    async def get_snapshots(self, dataset: str) -> list[DatasetSnapshot]: ...
    async def get_latest_snapshot(self, dataset: str) -> DatasetSnapshot: ...

    async def get_schema(self, dataset: str) -> DatasetSchema: ...
    async def evolve_schema(self, dataset: str, additions: list[ColumnDef]) -> None: ...
```

### 4.4 Index 层 — DorisIndexStore

```python
# layers/index/doris_index_store.py

"""
索引加速层
仅存储主键 + 索引列 + 热点属性，不存全量明细
"""
class DorisxStore:
    def __init__(self, connection: DorisConnection) -> None: ...

    async def create_index_table(self, object_type: ObjectType) -> None:
        # 根据 ObjectType.properties 中 indexed=True 的字段创建索引表
        # 仅包含主键 + 索引列 + 热点属性
        ...

    async def drop_index_table(self, object_type_api_name: str) -> None: ...

    async def upsert(self, object_type_api_name: str, records: list[dict[str, Any]]) -> None:
        # INSERT ... ON DUPLICATE KEY UPDATEï     ...

    async def delete_by_ids(self, object_type_api_name: str, ids: list[str]) -> None: ...

    async def query(self, query: IndexQuery) -> IndexResult:
        # 根据 filter 构造 WHERE（利用倒排/向量索引）
        # 仅返回 rid 列表，不返回全量属性
        # vector_search 和 full_text_search 互斥，同时指定时优先向量检索
        ...
```

### 4.5 Pipeline 层 — SeaTunnelEngine

> **⚠️ 实现订正（2026-07 去 SeaTunnel 化）**：下方伪代码为设计阶段原画。实际实现中，`create_index_sync_pipeline` / `update_sync_pipeline`（Iceberg→Doris 索引同步）**从未落地为 SeaTunnel 方法**——该职责先由 `IndexSyncService.sync_now`（Trino 读 + Doris upsert）承担，后由 `ObjectIndexFunnel`（从 Iceberg scan_latest 读 → DorisIndexStore.upsert，统一 rid 分配 + 四引擎扇出）取代。SeaTunnel 现只保留 `create_sync_pipeline`（MAIN，外部源→Iceberg）+ `create_file_sync_pipeline` / `create_kafka_ingestion_pipeline` / `create_kafka_timeseries_pipeline` / `create_external_cdc_pipeline`。Doris/Neo4j/PostGIS 的写入均不走 SeaTunnel。详见 [architecture_overview.md](./architecture_overview.md) §5.6 + [ADR-008](./adr-008-iceberg-doris-sync-path.md) 修订记录。

```python
# layers/pipeline/sea_tunnel_engine.py

class SeaTunnelEngine:
__init__(self, zeta_client: SeaTunnelZetaClient) -> None: ...

    async def create_main_pipeline(
        self, source: PipelineSource, target_dataset: str, transforms: list[PipelineTransform] | None = None
    ) -> PipelineDef:
        # 生成 SeaTunnel conf: source → Iceberg sink
        # 部署到 Zeta namespace: seatunnel-ns-main
        ...

    async def create_index_sync_pipeline(
        self, source_dataset: str, target_object_type: str, index_fields: list[str]
    ) -> PipelineDef:
        #  conf: Iceberg source（增量） → Doris sink（仅索引列 + 热点列）
        # 部署到 Zeta namespace: seatunnel-ns-sync
        ...

    # 更新索引同步流水线（ObjectType 属性变更时调用）
    # PoC 阶段可手动触发，后续通过 OntologyService 自动调用
    async def update_sync_pipeline(self, object_type_api_name: str, index_fields: list[str]) -> None:
        # 重新生成 SeaTunnel conf 并重启对应任务
        ...

    async def start(self, name: str) -> Nonsync def stop(self, name: str) -> None: ...
    async def get_status(self, name: str) -> PipelineStatus: ...
```

### 4.6 Engine 层 — TrinoQueryEngine

```python
# layers/engine/trino_query_engine.py

"""
主要查询引擎
通过 Gravitino Connector 联邦查询所有注册的 Catalog
"""
class TrinoQueryEngine:
    def __init__(self, client: TrinoClient) -> None: ...

    # 统一 SQL 入口，底层通过 Gravitino Connector 自动路由
    async def query(self, sql: str) -> list[dict[str, Any]]: ...
```

***

## 五、业务编排层（services/）

> Services 直接依赖 layers/ 中的具体类，通过构造函数注入。以下伪代码使用 Python 风格。

### 5.1 ObjectQueryService — 核心查询（分场景路由）

```python
# services/object_query_service.py

"""
核心查询编排
根据 ObjectType.storage_type 分流：
  MANAGED → Doris 索引过滤 → IcebergStore 原生点查全量加载
  VIRTUAL → 直接 Trino 联邦查询 Virtual Table 指向的外部表

降级策略：
  - Doris 不可用时：èrino 扫描 Iceberg（带分区裁剪）
  - IcebergStore 不可用时：回退 Trino 按 ID 查询
"""
class ObjectQueryService:
    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry,
        index: DorisIndexStore,
        dataset: IcebergStore,
        engine: TrinoQueryEngine,
    ) -> None: ...

    async def load_objects(self, request: LoadObjectsRequest) -> list[dict[str, Any]]:
        # Step 1: 获取 ObjectType 元数据
        parts = request.object_set.object_type_api_name.split(".")
        object_type = await self.metadata.get_object_type(parts[0], parts[1])

        # Step 2: 权限校验
        allowed = await self.catalog.check_access(object_type.api_name, "read")
        if not allowed:
            raise ForbiddenError()

        # Step 3: 根据 storage_type 分流
        if object_type.storage_type == "VIRTUAL":
            return await self._load_virtual_objects(object_type, request)
        return await self._load_physical_objects(object_type, request)

    # 托管对象：Doris 索引 → Iceberg 原生点查全量加载
    async def _load_physical_objects(
        self, object_type: ObjectType, request: LoadObjectsRequest
    ) -> list[dict[str, Any]]:
        rids: list[str]

        if request.object_set.filter:
            try:
                # Doris 索引过滤，仅返回 ID 列表
                index_result = await self.index.query(IndexQuery(
                    object_type_api_name=object_type.api_name,
                filters=self._translate_filter(request.object_set.filter),
                    limit=request.limit,
                    offset=request.offset,
                ))
                rids = index_result.rids
            except Exception as e:
                # 降级：Doris 不可用时，直接 Trino 扫描 Iceberg（带分区裁剪）
                logger.warning("Doris index unavailable, falling back to Trino scan", extra={"error": str(e)})
                table_info = await self.catalog.r_physical_table(object_type.api_name)
                where_clause = self._translate_filter_to_sql(request.object_set.filter)
                sql = (
                    f"SELECT {', '.join(request.properties)} "
                    f"FROM iceberg_catalog.{table_info['table']} "
                    f"WHERE {where_clause} "
                    f"LIMIT {request.limit} OFFSET {request.offset}"
                )
                return await self.engine.query(sql)
        elif request.object_set.rids:
            rids = request.object_set.rids
        else:
            # 无过滤条件，直接 Trino 全表扫
            table_info = await self.catalog.resolve_physical_table(object_type.api_name)
            sql = (
                f"SELECT {', '.join(request.properties)} "
                f"FROM iceberg_catalog.{table_info['table']} "
                f"LIMIT {request.limit} OFFSET {request.offset}"
            )
            return await self.engine.query(sql)

        if not rids:
      return []

        # Iceberg 原生点查全量属性（使用 TableScan + Expression API，不走 Trino SQL）
        table_info = await self.catalog.resolve_physical_table(object_type.api_name)
        return await self.dataset.load_by_ids(table_info["table"], rids, request.properties)

    # 虚拟对象：直接 Trino 执行 View
    async def _load_virtual_objects(
        self, object_type: ObjectType, request: LoadObjectsRequest
    ) -> list[dict[str, Any]]:
        view_name = f"{object_type.api_name}_view"
        sql = f"SELECT {', '.join(request.properties)} FROM gravitino_catalog.ontology.{view_name}"

        if request.object_set.filter:
            where_clause = self._translate_filter_to_sql(request.object_set.filter)
            sql += f" WHERE {where_clause}"

        sql += f" LIMIT {request.limit} OFFSET {request.offset}"
        return await self.engine.query(sql)

    def _translate_filter(self, filter: QueryFilter) -> list[IndexFilter]: ...
    def _translate_filter_to_sql(self, filter: QueryFilter) -> str: ...
```

### 5.2 虚拟对象查询 — Virtual Table 联邦

> 原独立的 `VirtualTableService`（查 Gravitino SQL View）已删除，其语义与 Palantir Virtual Table 冲突。
> 虚拟对象查询现由 `ObjectQueryService` 按 `storage_type=VIRTUAL` 分流，走 Trino 联邦查询 Virtual Table 指向的外部表，全程不经过 Doris。
>
> Virtual Table 是外部数据源表的联邦代理指针（`DatasetGovernance.kind=VIRTUAL`），
> 由 `POST /datasources/{ds}/virtual-tables` 登记产生。详见 [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) §3.2/§3.4。

### 5.3 TimeTravelService — 时间旅行查询

```python
# services/time_travel_service.py

"""
场景：时间旅行 / 历史快照查询
走 Trino + Iceberg 快照

注意：FOR VERSION AS OF 是 Iceberg SQL 扩展，需在 PoC 阶段验证
Gravitino Connector 是否透传此语法。若不支持，需直接使用 iceberg_catalog。
"""
class TimeTra  def __init__(
        self,
        catalog: GravitinoRegistry,
        engine: TrinoQueryEngine,
    ) -> None: ...

    async def load_objects_as_of(
        self, object_type_api_name: str, ids: list[str], properties: list[str], snapshot_id: int
    ) -> list[dict[str, Any]]:
        allowed = await self.catalog.check_access(object_type_api_name, "read")
        if not allowed:
            raise ForbiddenError()
        table_info = await self.catalog.resolve_physical_table(object_type_api_name)
        id_list = ", ".join(f"'{id}'" for id in ids)
        sql = (
            f"SELECT {', '.join(properties)} "
            f"FROM iceberg_catalog.{table_info['table']} "
            f"FOR VERSION AS OF {snapshot_id} "
            f"WHERE id IN ({id_list})"
        )
        return await self.engine.query(sql)
```

### 5.4 ActionService — 写入编排

```python
# services/action_service.py

"""
场景：数据更新/Action
写入 Iceberg（唯一写入入口），Doris 索引同步由 ObjectIndexFunnel（外部接入）/ OutboxExecutor（Action 写入）处理，不经 SeaTunnel
"""
class ActionService:
    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry,
        dataset: IcebergStore,
    ) -> None: ...

    async def execute_action(
        self, object_type_api_name: str, action: str, payload: dict[str, Any]
    ) -> None:
        allowed = await self.catalog.check_access(object_type_api_name, "write")
        if not allowed:
            raise ForbiddenError()
        table_info = await self.catalog.resolve_physical_table(object_type_api_name)
        await self.dataset.append(table_info["table"], [payload])
```

### 5.5 OntologyService — 本体管理

```python
# services/ontology_service.py

"""
本体管理编排
先写 PG 元数据，再调用 Gravitino 注册物理资产
"""
class OntologyService:
    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry,
        index: DorisIndexStore,
    ) -> None: ...

    async def create_ontology(self, ontology: Ontology) -> None:
      ait self.metadata.create_ontology(ontology)

    async def define_object_type(self, object_type: ObjectType) -> None:
        # Step 1: PG 存储业务本体元数据
        await self.metadata.create_object_type(object_type)

        if object_type.storage_type == "MANAGED":
            # Step 2: Gravitino 注册物理表
            # (物理表由 Gaia 经 IcebergStore.create_managed_table 创建，SeaTunnel 只写数据)

            # Step 3: 创建 Doris 索引表（仅索引列 + 热点列）
            await self.idex_table(object_type)
        # VIRTUAL 类型：无需注册物理表和索引表

    async def add_shared_property(self, prop: SharedProperty) -> None:
        await self.metadata.create_shared_property(prop)

    async def link_shared_property(self, object_type_id: str, shared_property_id: str) -> None:
        await self.metadata.link_shared_property(object_type_id, shared_property_id)

    async def define_link_type(self, link: LinkTypeDef) -> None:
        await self.metadata.create_link_type(link)
ef define_action_type(self, action: ActionType) -> None:
        await self.metadata.create_action_type(action)

    async def define_value_type(self, vt: ValueType) -> None:
        await self.metadata.create_value_type(vt)

    async def create_struct(self, struct: Struct) -> None:
        await self.metadata.create_struct(struct)

    async def create_group(self, group: ObjectTypeGroup) -> None:
        await self.metadata.create_group(group)

    async def create_branch(self, branch: Branch) -> None:
        await self.metadata.create_branch(branch)
```

***

## 六、依赖注入容器（config/container.py）

```python
# config/container.py

from layers.catalog.gravitino_registry import GravitinoRegistry
from layers.metadata.postgres_meta_store import PostgresMetaStore
from layers.dataset.iceberg_store import IcebergStore
from layers.index.doris_index_store import DorisIndexStore
from layers.pipeline.sea_tunnel_engine import SeaTunnelEngine
from layers.engine.trino_query_engine import TrinoQueryEngine
from services.object_query_service import ObjectQueryService
from services.time_travel_service import TimeTravelService
from services.action_service import ActionService
from services.ontology_service import OntologyService

catalog = GravitinoRegistry(gravitino_client)
metadata = PostgresMetaStore(pg_pool)
dataset = IcebergStore(iceberg_catalog)
index = DorisIndexStore(doris_connection)
pipeline = SeaTunnelEngine(zeta_client)
engine = TrinoQueryEngine(trino_client)

object_query_service = ObjectQueryService(metadata, catalog, index, dataset, engine)
# virtual_table_service 已删除（VirtualTableService 线路废弃）
time_travel_service = TimeTravelService(catalog, engine)
action_service = ActionService(metadata, catalog, dataset)
ontology_service = OntologyService(metadata, catalog, index)
```

***

## 七、完整数据流（4 个场景，最终版）

### 场景 1：新数据接入

```
源端（MySQL/Kafka/IoT）
    ↓ CDC/全量
SeaTunnel 流水线1ï¢）
    ↓ 写入
Iceberg（RustFS，生成新快照，唯一写入入口）
    ↓ 元数据注册
Gravitino（注册物理表）
    ↓ 触发
SeaTunnel 流水线2（仅同步索引列 + 热点列）
    ↓ 写入索引列
Doris（更新索引表）
    ↓
PostgreSQL（本体元数据已在定义 ObjectType 时写入）
```

### 场景 2：物理对象查询（核心链路）

```
客户端
    ↓ 条件查询 + 属性列表
ObjectQueryService（识别 storage_type=MANAGED）
    ↓ 索引过æis（倒排/向量索引过滤 → 返回 rid 列表）
    │ 若 Doris 不可用 → 降级为 Trino 直接扫描 Iceberg（带分区裁剪）
    ↓ 主键列表
IcebergStore.load_by_ids()（原生 TableScan + Expression API 点查）
    ↓ 全量业务属性
客户端
```

### 场景 3：虚拟对象查询

```
客户端
    ↓ 条件查询
ObjectQueryService（识别 storage_type=VIRTUAL）
    ↓ 直接 Trino 联邦查询 Virtual Table 指向的外部表
Trino（通过 Gravitino Connector 执行 View）
    ↓ SELECT * FROM gvitino_catalog.ontology.view_name
客户端

无 Doris 参与
```

### 场景 4：时间旅行 / 历史查询

```
客户端
    ↓ 指定 as_of_snapshot_id
TimeTravelService
    ↓ 权限校验 + 表路由
Trino（Iceberg 快照查询）
    ↓ SELECT ... FOR VERSION AS OF {snapshot_id}
Iceberg（读取指定快照数据）
    ↓ 返回历史版本
客户端
```

***

## 八、故障模式与影响分析（FMEA）

> 系统工程核心实践：识别每个组件失效对最终用户的影响，定义é值。

### 8.1 场景 1：新数据接入

| 故障模式                     | 影响                 | 严重度 | 恢复程序                                                                                                                                                                                            | 告警规则                                                 |
| ------------------------ | ------------------ | --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| SeaTunnel 主流水线崩溃         | 新数据无法写入 Iceberg    | 高   | ① 确认 Zeta Master 存活 → `curl zeta-master:5801/health`；② 检查任务日志 → `seatunnel.sh -l`；③ 若 OOM 则调大 `taskmanager.memory.process.size`；④ 重启任务 → `seatunnel.sh -r {job_id}`；⑤ 源端 CDC offset 不提交，æ½赶 | `seatunnel_main_status != RUNNING` 持续 > 60s          |
| RustFS 不可用               | Iceberg 写入失败       | 高   | ① 检查 RustFS 进程 → `systemctl status rustfs`；② 检查磁盘空间 → `df -h`；③ 若磁盘满则清理过期快照 → `iceberg_expire_snapshots`；④ 重启 RustFS；⑤ SeaTunnel 自动重试写入（指数退避，最多 10 次）                                           | `rustfs_health != ok` 立即告警                           |
| Iceberg RESlog 不可用 | 表操作失败              | 高   | ① 检查 REST Catalog 进程；② 检查后端存储（RustFS）连通性；③ 重启 REST Catalog 服务；④ 写入端缓存待提交数据，恢复后批量提交                                                                                                              | `iceberg_rest_health != ok` 立即告警                     |
| SeaTunnel 索引同步延迟 > 60s   | Doris 索引滞后，查询可能漏数据 | 中   | ① 检查同步ä→ `seatunnel.sh -s {sync_job_id}`；② 若延迟持续增长则扩容同步任务并行度；③ 物理查询自动降级为 Trino 全表扫（由 ObjectQueryService 检测 Doris 异常时触发）                                                                     | `doris_sync_lag > 60s` 且 `trino_query_p95 > 3s` 触发预警 |
| Doris 索引同步崩溃             | 索引表停止更新            | 中   | ① 检查同步任务日志；② 重启同步任务；③ 物理查询自动降级为 Tri；④ 同步恢复后从上次 checkpoint 追赶                                                                                                                              | `seatunnel_sync_status != RUNNING` 持续 > 120s         |

### 8.2 场景 2：物理对象查询

| 故障模式             | 影响                  | 严重度 | 恢复程序                                                                                                                                                                    | 告警规则                             |
| ---------------- | ------------------- | --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| Doris 不可用        | 索引过滤失败              | 中   | ① 检查 FE Master → `curl doris-fe:8030/api/health`；② 检查 BE 存活 → `show backends`；③ 若 FE 单点故障则é；④ 若 BE 多节点宕机则扩容 BE；⑤ ObjectQueryService 自动降级为 Trino 直接扫描 Iceberg（带分区裁剪），延迟增加但可用 | `doris_fe_health != ok` 立即告警     |
| IcebergStore 不可用 | 全量属性加载失败            | 高   | ① 检查 REST Catalog 健康；② 检查 RustFS 连通性；③ 若 REST Catalog 不可用则回退 Trino 按 ID 查询（`SELECT ... WHERE id IN (...)`）；④ 若 RustFS 不可用则查询整体失败，需立即恢复存储                                | `iceberg_rest_health != ok` 立即告警 |
| PostgreSQL 不可用   | 无法获取 ObjectType 元数据 | 高   | ① 检查 PG 进程 → `pg_isready`；② 检查连接池 → `SELECT count(*) FROM pg_stat_activity`；③ 若连接池满则重启应用释放连接；④ 若 PG 宕机则触发主从切换（Patroni）；⑤ 查询直接失败（无降级路径），需立即恢复                                    | `pg_health != ok` 立即告警           |
| Trino 不可用   失效           | 高   | ① 检查 Coordinator → `curl trino:8080/v1/info`；② 检查 Worker 节点；③ 若 Coordinator 单点故障则重启；④ 若 Worker 不足则扩容；⑤ 查询失败，需立即恢复                                                               | `trino_health != ok` 立即告警        |
| Gravitino 不可用    | 权限校验失败，表路由失败        | 高   | ① 检查 Gravitino Server → `curl gravitino:8090/api/health`；② 检查 Gravitino 存储后端；绕过权限（使用本地缓存的表路由映射），但 RBAC 失效；④ 需立即恢复 Gravitino 服务                                          | `gravitino_health != ok` 立即告警    |

### 8.3 场景 3：虚拟对象查询

| 故障模式          | 影响          | 严重度 | 恢复程序                                                                | 告警规则                          |
| ------------- | ----------- | --- | ----------------------------------------------------------------------------------------- |
| Gravitino 不可用 | Virtual Table 联邦元信息不可获取 | 高   | ① 检查 Gravitino Server 健康；② 重启 Gravitino 服务；③ 虚拟表查询直接失败（无降级路径），需立即恢复 | `gravitino_health != ok` 立即告警 |
| Trino 不可用     | View 无法执行   | 高   | ① 检查 Trino Coordinator；② 重启 Trino 集群；③ 查询失败，需立即恢复                   | `trino_health != ok` 立即告警     |

### 8.4 场景 4：时间旅行查询模式                                        | 影响        | 严重度 | 恢复程序                                                                                                                                        | 告警规则                                 |
| ------------------------------------------- | --------- | --- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------itino Connector 不透传 `FOR VERSION AS OF` | 时间旅行语法不可用 | 高   | ① PoC 阶段验证语法支持；② 若不支持，TimeTravelService 配置开关切换为 `iceberg_catalog` 直连；③ 更新 Trino catalog 配置，添加 `iceberg.properties`                          | PoC 阶段验证，若不支持则默认使用 `iceberg_catalog` |
| Iceberg 快照已过期                               | 历史数据不可读   | 中   | ① 返回明确错误信息 `SnapshotExpiredError(snapsh{id}, expired_at={ts})`；② 提示用户可用的最近快照列表；③ 调整快照保留策略 `history.expire.max-snapshot-age-ms` 防止过早过期 | 非告警，业务异常（返回 410 Gone）                |

### 8.5 系统运行状态向量

| 指标                | 正常阈值         | 预警阈值           | 告警阈值    |
| ----------------- | ------------ | -------------- | ------- |
| Doris 索引同步延迟      | < 30s        | 30s \~ 60s     | > 60s   |
| Trino 查询 P95 延è 500ms      | 500ms \~ 1s    | > 1s    |
| 物理对象查询 P95 延迟     | < 200ms      | 200ms \~ 500ms | > 500ms |
| 查询成功率             | > 99.9%      | 99% \~ 99.9%   | < 99%   |
| PostgreSQL 连接池使用率 | < 50%        | 50% \~ 80%     | > 80%   |
| SeaTunnel 主流水线吞吐  | > 10K rows/s | 5K \~ 10K      | < 5K    |

***

## 九、部署与链路隔离规范

### 9.1 集群规划

| 组件                      | 角色                   | 节点数             | 核心配置                               |
| ----------------------- | -------------------- | --------------- | ---------------------------------------- |
| PostgreSQL 16           | 业务本体元数据              | 1（开发）/ 3（HA 生产） | 存储全部领域模型表                                |
| Apache Gravitino 1.3.0  | 物理资产注册/RBAC/血缘       | 3（HA）           | 对接 Iceberg REST Catalog；开启 View、权限、血缘、审计 |
| RustFS V1               | S3 兼容对象存储| 集群              | 承载全部 Iceberg 数据                          |
| Apache Iceberg 1.11.0   | 主 Dataset、版本管理       | 依托对象存储          | 开启快照、时间旅行、增量读取                           |
| Apache SeaTunnel 2.3.13 | PipelineBuilder 数据管道 | 4\~6（Zeta 集群）   | 两组任务组：组1 源→Iceberg、组2 Iceberg→Doris      |
| Apache Doris 4.0.6      | 索引加速层                | FE:3 + BE:6\~8  | 仅存索引列+热点列；开启倒æ           |
| Apache Trino 478        | 主要查询引擎               | 3\~6            | 对接 Gravitino Connector，联邦查询              |

### 9.2 链路隔离规范

1. **两条 SeaTunnel 流水线物理隔离**
   - 主写入流水线（数据源→Iceberg）：优先保障吞吐、实时性，分配更多资源
   - 索引同步流水线（Iceberg→Doris）：仅同步索引列+热点列，允许最终一致
   - 禁止用同一条任务混合"主数据写入"和"索引同步"
2. **Iceber作为唯一写入入口**
   - Doris 不承接写请求、不作为数据基准
   - 索引延迟可接受秒/分钟级最终一致
3. **Virtual Table 使用规范**
   - 托管对象(MANAGED)：直接映射单张 Iceberg 托管表
   - 虚拟对象(VIRTUAL)：映射 Virtual Table（外部表联邦代理），由 Trino 联邦查询
   - 虚拟表查询不走 Doris
   - 已废弃：Gravitino SQL View 线路（create_view/get_view 已删除）
4. **Doris 索引表设计红线**
   - 仅存储：主键 + 索引列（倒排/向量）+ 热点属性（常用过滤/排序）
   - 不存储：全量明细、大字段、二进制
   - 按业务维度分区，配合 Doris 缓存提升查询性能
5. **全局移除 Redis**
   - 鉴权 Token 缓存：用 Gravitino 自带 JWT 缓存
   - 热点数据缓存：用 Doris 自带 Page Cache + 分区剪裁

### 9.3 运维红线与自愈策略

| 组件           | 健康检查                      | 重启策略                      | 备份策略                 |
| ------------ | ------------------------- | ------------------------- | -------------------- |
| PostgreSQL   | `pg_isrea   | `restart: unless-stopped` | 每日 pg\_dump + WAL 归档 |
| Gravitino    | HTTP `/api/health`        | `restart: unless-stopped` | 定期备份 Gravitino 存储目录  |
| RustFS       | HTTP `/minio/health/live` | `restart: unless-stopped` | 依赖对象存储自身冗余           |
| Iceberg REST | HTTP `/v1/config`         | `restart: unless-stopped` | 元数据在 RustFS，无需额外备份   |
| SeaTunnel    | Zeta 集群自愈                 | `restart: unless-stopped` | 配置在 Git，状æ     |
| Doris FE/BE  | HTTP `/api/health`        | `restart: unless-stopped` | 定期备份 FE 元数据          |
| Trino        | HTTP `/v1/info`           | `restart: unless-stopped` | 无状态，无需备份             |

### 9.4 可观测性设计

| 层次               | 实现方式                                | 内容                                                        |
| ---------------- | ----------------------------------- | --------------------------------------------------------- |**        | Python `logging` + JSON 格式          | 每条日志含 `trace_id`、`span_id`、`layer`、`method`、`duration_ms` |
| **trace\_id 传递** | FastAPI 中间件生成，通过 `contextvars` 跨层传递 | 一次请求在所有 Layer 调用中共享同一 `trace_id`                          |
| **Metrics**      | Prometheus `prometheus_client`      | 每层对外调用（SQL、REST）的耗时直方图、状态码计数器、错误率                         |
| **Grafana 面板**   | 预置 daON                   | 各层健康度总览、查询延迟分布、同步延迟趋势、错误率热力图                              |
| **告警规则**         | Prometheus AlertManager             | 基于 8.5 节运行状态向量阈值，组合告警规则                                   |

***

## 十、各层红线

| 层                         | 允许                                    | 禁止              |
| ------------------------- | ------------------------------------- | -------------etadata (PostgreSQL)** | 存业务本体元数据（Ontology、ObjectType 等全部领域模型） | 存物理表元数据、参与查询    |
| **Catalog (Gravitino)**   | 注册物理资产、虚拟表、RBAC、血缘                    | 存业务本体元数据、参与数据计算 |
| **Dataset (Iceberg)**     | 存全量明细 + 历史快照、时间旅行                     | 做在线查询、做检索加速     |
| **Index (Doris)**         | 存主键 + 索引列 + 热点属性、全文/向量检       | 存全量明细、作为写入入口    |
| **Pipeline (SeaTunnel)**  | 数据采集/清洗/写入/索引同步                       | 做元数据管理、做查询路由    |
| **Engine (Trino)**        | 联邦查询、全量数据加载、View 执行                   | 做主数据存储          |

***

## 十一、项目文件结构

```
ontology/
├── docker-compose.yml
├── .env.example
├── config/                             # 各开源组件原生配置
│   ├── g│   └── gravitino.conf
│   ├── seatunnel/
│   │   ├── seatunnel.yaml
│   │   └── jobs/
│   │       ├── pipeline-main/          # 流水线1：源 → Iceberg
│   │       └── pipeline-sync/          # 流水线2：Iceberg → Doris（仅索引列）
│   ├── iceberg/
│   │   └── catalog.properties
│   ├── doris/
│   │   ├── fe.conf
│   │   └── be.conf
│   ├── rustfs/
│   └── trino/
│       â│           ├── gravitino.properties    # Gravitino Connector
│           └── iceberg.properties
├── infra/                              # 初始化脚本
│   ├── init-pg-schema.sql              # 只建 gravitino_store schema + pgcrypto（业务表由 Alembic 管）
│   └── gravitino-pg-schema.sql          # Gravitino entity store 表结构（1.2.0 base + 1.3.0 升级）
├── alembic/                            # Alembic 迁移（业务表 schema 单一真相源）
│   ├── env.py                          # 迁移环境（统一 Base.metadata，排除 gravitino_store/Iceberg 元数据表）
│   └── versions/                       # revision 链
├── alembic.ini                         # Alembic 配置
│   ├── init-rustfs.sh
│   ├── init-iceberg-tables.sql
│   ├── init-doris-index-tables.sql     # 仅索引列 + 热点列
│   └── register-to-gravitino.sh
├── src/
│   └── ontology/                       # Python 包
│       ├── core/
│       │   ├── models/                 # SQLAlchemy ORM 模型
│       │   └── schemas/                # pydantic Schema
│       ├── layers/
│       │   ├── catalog/
│       │   ├── metadata/
│       │   ├── dataset/
│       │   ├── index/
│       │   ├── pipeline/
│       │   └── engine/
│       ├── services/
│       ├── routes/
│       ├── config/
│       â─ middleware/
│       └── main.py
├── tests/
│   ├── unit/
│   ├── integration/                    # 接口集成测试
│   ├── e2e/
│   └── performance/                    # 性能测试
├── examples/
├── scripts/
│   ├── bootstrap-all.sh
│   └── seed-sample-data.py
├── pyproject.toml
└── Dockerfile
```

***

## 十二、实施步骤（5 阶段 + Sprint 0）

### Sprint 0：风险驱动的技术验证（先行）

> 不等整体框架完成，先排除 P0 风险。

1. **P0 验证：RustFS + Iceberg 读写兼容性**
   - 启动 RustFS + Iceberg REST Catalog
   - 建表、INSERT、SELECT、时间旅行、快照回滚
   - 验证 FileIO、atomic rename、multipart 上传
2. **P0 验证：Trino + Gravitino Connector 时间旅行**
   - 启动 Trino + Gravitino
   - 执行 `SELECT ... FOR VERSION AS OF {snapshot_id}`
   - 若不支持，TimeTravelService 直接使用 `iceberg_catalog`

### 阶段 1：核心模型定义（）

1. 创建项目骨架（pyproject.toml, src/ontology/）
2. 编写 SQLAlchemy ORM 模型 + pydantic Schema
3. 编写 PostgresMetaStore（SQLAlchemy 2.0 async）
4. **冻结所有 Layer 类的公开方法签名**（ICD 基线 v1.0）
5. 验证：`mypy` + `ruff` + 单元测试通过

### 阶段 2：层实现（可并行）

1. 编写 `layers/catalog/gravitino_registry.py`
2. 编写 `layers/dataset/iceberg_store.py`
3. 编写 `layers/index/doris_index_store.py`
4. 编写 `layers/pipeline/sea_tunnel_engine `layers/engine/trino_query_engine.py`
6. 编写各层单元测试（Mock 底层客户端）
7. **接口集成测试**：注入真实 Layer，Mock 底层（httpx.MockTransport / testcontainers）

### 阶段 3：业务编排 + 容器

1. 编写 `services/` 5 个 Service（含降级逻辑）
2. 编写 `config/container.py` 组装
3. 编写 Service 接口集成测试

### 阶段 4：HTTP 路由 + Docker Compose + 可观测性

1. 编写 `routes/` 薄层路由
2. 编写 `docker-compose.yml`（含 healthcheck +estart 策略）
3. 编写 `infra/` 初始化脚本
4. 编写 `config/` 各组件原生配置
5. 编写结构化日志中间件（trace\_id 传递）
6. 编写 Metrics 采集（Prometheus 格式）
7. 编写 Grafana 面板配置

### 阶段 5：端到端验证 + 故障注入

1. 启动 docker-compose，确认全部 healthy
2. 跑通 4 个场景的端到端测试
3. **故障注入测试**：kill 组件、注入网络延迟，验证降级策略
4. **全链路压测**：验证 0.1 节关键能力等级

***

#层测试策略（V 模型）

> 航天工程追求"一次成功"，背后是极其严苛的验证体系。

### 13.1 四层测试金字塔

| 测试层级       | 范围                                                               | 工具                      | 覆盖率目标                 | 执行频率        |
| ---------- | ---------------------------------------------------------------- | ----------------------- | --------------------- | ----------- |
| **单元测试**   | 每个 Layer 类的方ck 底层客户端                                         | pytest + pytest-asyncio | 行覆盖率 > 80%，异常路径 100%  | 每次提交        |
| **接口集成测试** | Service 调用真实 Layer，Mock 底层（httpx.MockTransport / testcontainers） | pytest + testcontainers | 每个 Service 方法至少 2 个场景 | 每次提交        |
| **系统测试**   | 全真实组件 docker-compose，4 个场景 E2E                                   | pytest + docker-compose | 4 个核心场景全覆ç      | 每次 PR / 每日  |
| **性能测试**   | 单独环境，用具体负载测试指标是否达标                                               | locust / k6             | 0.1 节全部指标             | 每次组件升级 / 每周 |

### 13.2 异常覆盖率要求

每个方法的异常路径必须有明确断言：

- 连接超时 → 返回明确错误码，不崩溃
- 权限拒绝 → 返回 403，不泄露内部信息
- 数据冲突 → 返回 409，附带冲突详情
- 依赖不可用 →级逻辑，记录告警日志

### 13.3 回归测试自动化

每一次组件升级（SeaTunnel 镜像更新、Doris 版本升级、Gravitino 版本升级），必须触发完整的接口集成测试 + 系统测试套件。

***

## 十四、实施风险与缓解

| 风险                                   | 等级 | 影响                 | 缓解措施                                                         |
| ------------------------------------ | -- | ------------------ | ---------------------------------------------------------- |
| **RustFS + Iceberg 兼容性**             | P0 | 存储底座不可用，阻塞全部数据流    | Sprint 0 优先验证                                                |
| **Trino + Gravitino Connector 时间旅行** | P0 | 历史快照查询不可用          | Sprint 0 优先验证；若不支持，直接使用 `iceberg_catalog`                    |
| **Trino + Iceberg 按 ID 查询延迟**        | P1 | 物理对象查询链路延迟         | IcebergStore.load\_by\_ids() 使用原生 TableScan + Expression API |
| **SeaTunnel 索引同步配置**                 | P1 | ObjectType 变更后同步失效 | 元数据驱动生成同步任务；预留 update\_sync\_pipeline() 扩展点                  |
| **Gravitino 联邦查询（Virtual Table）**          | P1 | 虚拟表查询不可用           | 已验证与 Trino 集成兼容；JDBC catalog 联邦成熟                                    |
| **Doris 索引列选择**                      | P2 | 索引命中率不足            | 由 `Proexed` 字段控制；可动态调整                           |
| **虚拟表过滤条件下推**                        | P2 | 复杂联邦查询性能差      | PoC 阶段增加多表 JOIN + 过滤条件场景测试                              |

### 14.1 PoC 验证优先级

| 优先级    | 验证项                                                  | 阻塞范围   | 所属阶段     |
| ------ | ---------------------------------------------------- | ------ | -------- |
| **P0** | RustFS + Iceber性（建表、追加、快照回滚）                   | 全部数据流  | Sprint 0 |
| **P0** | Trino + Gravitino Connector `FOR VERSION AS OF` 语法支持 | 时间旅行查询 | Sprint 0 |
| **P1** | IcebergStore.load\_by\_ids() 原生点查性能基准                | 物理对象查询 | 阶段 2     |
| **P1** | 向量/全文检索互斥逻辑                                          | 索引查询   | 阶段 2     |
| **P2** | 虚拟表多表 JOIN + 过滤条件下推                             | 阶段 5     |
| **P2** | SeaTunnel 同步配置动态更新                                   | 索引同步运维 | 阶段 5     |
| **P2** | 故障注入：Doris 不可用时降级 Trino 扫描                           | 系统弹性   | 阶段 5     |
| **P2** | 故障注入：Gravitino 不可用时物理表查询                             | 系统弹性   | 阶段 5     |

***

## 十五、架构决策记录（ADR）索引

> 每个关键技术选择均记录为 ADR，包含背景、决策、后æ¥期。

| ADR #   | 决策                                          | 背景                                        | 替代方案                                        |
| ------- | ------------------------------------------- | ----------------------------------------- | ------------------------------------------- |
| ADR-001 | 使用 Doris 作索引加速层，而非直接用 Trino 计算              | Trino 全表扫延迟不可控，Doris 倒排/向量索引可实现 < 50ms 过滤 | 直接用 Trino延迟高）；用 Elasticsearch（运维重）      |
| ADR-002 | 使用 SeaTunnel 而非 Flink 作 Pipeline            | SeaTunnel 配置驱动、与 Gravitino 深度集成、部署轻量      | Flink（功能更强但运维复杂）；Spark Streaming（批处理思维）     |
| ADR-003 | 使用 RustFS 而非 MinIO                          | MinIO 开源版 2025.12 进入维护模式                  | MinIO（已弃用）；Ceph（过重）；直接 S3（生产环境可切换）          |
| ADR-004 | ä 存储业务本体元数据                     | 需要事务性、强一致性、成熟生态                           | MySQL（Doris 已用 MySQL 协议，避免混淆）；etcd（不适合复杂查询） |
| ADR-005 | ObjectType.properties 当前用 JSONB，后续可降级为关系表   | 初期灵活迭代，后期按需拆分                             | 直接建关系表（初期开发效率低）；纯文档数据库（查询能力弱）               |
| ADR-006 | 使用 Python + FastAPI 而 Express | Python 在 Iceberg/Trino/S3 官方库支持更成熟        | TypeScript（前端同构优势）；Go（性能更好但生态不如 Python）     |

***

## 十六、接口控制文档（ICD）基线

> 每一层的公开方法签名作为接口基线，随代码版本化。任何修改需通过测试和评审。以下签名与第四节的层实现伪代码保持一致。

### ICD-01: PostgresMetaStore

| 方法                                                                   | 输入                | 输出                                              | 版本   |
| -------------------------------------------------------------------- | ------------------------------- | ----------------------------------------------- | ---- |
| `create_ontology(ontology: Ontology)`                                | Ontology 领域模型                   | `None`                                          | v1.0 |
| `get_ontology(api_name: str)`                                        | 全局唯一 api\_name          | `Ontology \| None`                              | v1.0 |
| `list_ontologies()`                                                  | 无                               | `list[Ontology]`                                | v1.0 |
| `create_object_type(object_type: ObjectType)`                        | ObjectType 领域模型（含嵌套 properties） | `None`                                          | v1.0 |
| `get_object_type(ontology_api_name: str, api_name: str)`             | 两级 api\_name               ObjectType \| None`（含 eager-loaded properties） | v1.0 |
| `list_object_types(ontology_api_name: str)`                          | Ontology api\_name              | `list[ObjectType]`                              | v1.0 |
| `update_object_type(id: str, updates: dict[str, Any])`               | 对象类型 ID + 部分更新字段                | `None`                                          | v1.0 |
| `add_property(object_type_id: str, prop: PropertyDef)`               | 对象类型 ID + 属性定ä        | `None`                                          | v1.0 |
| `get_properties(object_type_id: str)`                                | 对象类型 ID                         | `list[PropertyDef]`                             | v1.0 |
| `create_shared_property(prop: SharedProperty)`                       | SharedProperty 领域模型             | `None`                                          | v1.0 |
| `link_shared_property(object_type_id: str, shared_property_id: str)` | 对象类型 ID + 共享属     | `None`                                          | v1.0 |
| `create_link_type(link: LinkTypeDef)`                                | LinkTypeDef 领域模型                | `None`                                          | v1.0 |
| `get_link_types(ontology_api_name: str)`                             | Ontology api\_name              | `list[LinkTypeDef]`                             | v1.0 |
| `create_action_type(action: ActionType)`                             | ActionType 领域模型                 | `None`                                          | v1.0 |
| `get_action_type(ontology_api_name: str, api_name: str)`             | 两级 api\_name                    | `ActionType \| None`                            | v1.0 |
| `create_interface_type(iface: InterfaceType)`                        | InterfaceType 领域模型              | `None`                                          | v1.0 |
| `create_value_type(vt: ValueType)`                                   | ValueType 领域模型                 None`                                          | v1.0 |
| `create_struct(struct: Struct)`                                      | Struct 领域模型                     | `None`                                          | v1.0 |
| `create_group(group: ObjectTypeGroup)`                               | ObjectTypeGroup 领域模型            | `None`                                          | v1.0 |
| `create_branch(branch: Branch)`                                      | Branch 领域模型                    `                                          | v1.0 |

### ICD-02: GravitinoRegistry

| 方法                                                                             | 输入               | 输出                                         | 版本   |
| ------------------------------------------------------------------------------ | ---------------- | ------------------------------------------ | ---- |
| `register_dataset(dataset: Dataset)`                                           | Dataset 领域模型 one`                                     | v1.0 |
| `get_dataset(name: str)`                                                       | Dataset 名称       | `Dataset`                                  | v1.0 |
| `is_view(name: str)`                                                           | 资源名称             | `bool`                                     | v1.0 |
| `get_table_columns(catalog, schema, table)`                                    | Virtual Table 定位符        | `list[dict]`（列定义）                      | v1.0 |
> `create_view` / `get_view` 已删除（Gravitino SQL View 线路废弃，见 [dataset-ontology-binding.md](../design/dataset-ontology-binding.md) §3.4）
| `check_access(object_type_api_name: str, operation: Literal["read", "write"])` | 资源名 + 操作         | `bool`                                     | v1.0 |
| `resolve_physical_table(object_type_api_name: str)`                            | 对象类型 api\_name   | `dict[str, str]`（含 catalog, schema, table） | v1.0 |

### ICD-03: IcebergStore

| 方法                                                                                      | 输入                        | 输出                      | 版本   |
| --------------------------------------------------------------------------------------- | ------------------------- | ----------------------- | ---- |
| `load_by_ids(dataset: str, ids: list[str], columns: list[str])`                         | 表名 + ID 列表 + 列名列表         | `list[dict[str, Any]]`  | v1.0 |
| `load_by_ids_as_of(dataset: str, ids: list[str], columns: list[str], snapshot_id: int)` | 表名 + ID 列表 + 列名列表 + 快照 ID | `list[dict[str, Any]]`  | v1.0 |
| `append(dataset: str, rows: list[dict[str, Any]])`                                      | 表名 + 行数据                  | `WriteResult`           | v1.0 |
| `overwrite(dataset: str, rows: list[dict[str, Any]])`                                   | 表名 + 行数据                  | `WriteResult`           | v1.0 |
| `get_sndataset: str)`                                                           | 表名                        | `list[DatasetSnapshot]` | v1.0 |
| `get_latest_snapshot(dataset: str)`                                                     | 表名                        | `DatasetSnapshot`       | v1.0 |
| `get_schema(dataset: str)`                                                              | 表名                        | `DatasetSchema`         | v1.0 |
| `evolve_schema(dataset: str, additions: list[ColumnDef])                    | 表名 + 新增列定义                | `None`                  | v1.0 |

### ICD-04: DorisIndexStore

| 方法                                                                 | 输入                     | 输出                                  | 版本   |
| ------------------------------------------------------------------ | ---------------------- | ----------------------------------- | ---- |
| `query(query: IndexQuery)`                                         | IndexQuery 领å        | `IndexResult`（含 object\_ids, total） | v1.0 |
| `create_index_table(object_type: ObjectType)`                      | ObjectType 领域模型        | `None`                              | v1.0 |
| `drop_index_table(object_type_api_name: str)`                      | 对象类型 api\_name         | `None`                              | v1.0 |
| `upsert(object_type_api_name: str, records: list[dict[str, Any]])` | 对象类型 api\_name + 记录列表  | `None`                              |  `delete_by_ids(object_type_api_name: str, ids: list[str])`         | 对象类型 api\_name + ID 列表 | `None`                              | v1.0 |

### ICD-05: TrinoQueryEngine

| 方法                | 输入      | 输出                     | 版本   |
| ----------------- | ------- | ---------------------- | ---- |
| `query(sql: str)` | SQL 字符串 | `list[dict[str, Any]]` | v1.0 |

### ICD-06: SeaTunnelEngine

| 方法                                                                                                         | 输入                          | 输出               | 版本   |
| ---------------------------------------------------------------------------------------------------------------- | --------------------------- | ---------------- | ---- |
| `create_main_pipeline(source: PipelineSource, target_dataset: str, transforms: list[PipelineTransform] \| None)` | 数据源 + 目标 Dataset + 可选转换     | `PipelineDef`    | v1.0 |
| `create_index_sync_pipeline(source_dataset: str,_type: str, index_fields: list[str])`              | 源 Dataset + 目标对象类型 + 索引字段列表 | `PipelineDef`    | v1.0 |
| `update_sync_pipeline(object_type_api_name: str, index_fields: list[str])`                                       | 对象类型 api\_name + 索引字段列表     | `None`           | v1.0 |
| `start(name: str)`                                                                                               | Pipeline 名称                 | `None`           | v1.0 |
| `stopame: str)`                                                                                                | Pipeline 名称                 | `None`           | v1.0 |
| `get_status(name: str)`                                                                                          | Pipeline 名称                 | `PipelineStatus` | v1.0 |

***

## 十七、架构演进路线图

> 为每个预留扩展点定义明确的触发条件，避免过早优化，也防止演进无据可依。

| 扩展点                | 当前方案                             | 触发条件                                | 目标方案                                                    |
| -------------------------- | -------------------------------- | ----------------------------------- | ------------------------------------------------------- |
| **properties JSONB → 关系表** | ObjectType.properties 以 JSONB 存储 | 对象类型数量 > 100 且属性变更频繁                | 拆分为独立 properties 表，通过bject\_type\_id FK 关联            |
| **SeaTunnel → Flink 替换**   | SeaTunnel 2.3.13                 | SeaTunnel 无法满足复杂流处理需求（如多流 JOIN、CEP） | 替换 Pipeline 层为 Flink，保持接口不变                             |
| **Doris 索引自动更新**           | 手动触发 update\_sync\_pipeline()    | ObjectType 属性变更频率 > 每周 10 次         | OntologyService.define\_object\_type() 自动触发             |
| **Trino 同步 → 异步**          | on-client dbapi（同步）    | 虚拟表查询并发 > 50 QPS 且 P95 > 500ms      | 替换为 `trino.async_client`                                |
| **单机 → 集群**                | 开发环境单节点                          | 生产环境上线                              | PostgreSQL HA（Patroni）、Gravitino HA（3 节点）、Doris FE 3 节点 |
| **Gravitino 缓存**           | 无缓存，每次实时查询                       | Gravitino 查询延迟 > 100ms 且 QPS > 100  | 在 Ggistry 层加 TTL 缓存（TTL 30s）                  |

***

## 十八、系统工程原则与最佳实践速查

> 以下清单从中国航天系统工程总体设计思想提炼，覆盖本文档中所有工程原则，作为全生命周期的快速参考和评审检查表。

### 18.1 总体设计原则

| # | 原则             | 要点                         | 文档对应                |
| - | -------------- | -------------------------- | ------------------- |
| 1 | **一切从任务总目标出å¹"做什么"和"做到什么程度"，再决定"怎么做" | §0.1 关键能力等级作为验收基线   |
| 2 | **逐层分解，全局最优**  | 各分系统独立优化不能损害整体性能           | §0.2 三维分解矩阵         |
| 3 | **总体架构师统一协调**  | 跨层变更必须经过总体评审               | §0.3 总体架构师职责        |
| 4 | **硬约束不可妥协**    | 核心约束写入文档，任何违反需 ADR 记录      | §1.2 核心硬约束          |
| 5    | 每层只做一件事，不越界                | §2.1 组件职责矩阵、§十 各层红线 |

### 18.2 综合集成原则

| #  | 原则             | 要点                   | 文档对应                    |
| -- | -------------- | -------------------- | ----------------------- |
| 6  | **重视组网后的涌现行为** | 独立组件正常 ≠ 集成后正常       | §八 FMEA 级联影响分析          |
| 7  | **定义系统运行状态向量** | 关键指标有正常/预警/告警三ç | §8.5 运行状态向量             |
| 8  | **全链路故障注入验证**  | 不仅验证正常路径，还要验证异常路径    | §十二 阶段 5 故障注入测试         |
| 9  | **降级优于崩溃**     | 每个依赖不可用时有明确的降级路径     | §0.2 降级策略列、§八 FMEA 恢复程序 |
| 10 | **可观测性内建**     | 日志、Metrics、追踪从第一天就设计 | §9.4 可观测性设计             |

### 18.3 技术状态管理原则

| #  | 原则         | 要点                        | 文档对应                      |
| -- | ----------- | ------------------------- | ------------------------- |
| 11 | **架构决策可追溯** | 每个关键技术选择有 ADR 文档          | §十五 ADR 索引                |
| 12 | **接口基线版本化** | 层间接口随代码版本控制，修改需评审         | §十六 ICD 基线                |
| 13 | **配置基线化**   | 所有组件配置纳入版本控制，环境差异通过环境变量覆盖 |ig/ 目录）      |
| 14 | **严禁手动热改**  | 生产环境配置变更必须走 CI/CD 流水线     | §9.3 运维红线                 |
| 15 | **组件版本锁定**  | 所有开源组件版本明确声明，升级需回归测试      | §1.3 组件版本矩阵、§13.3 回归测试自动化 |

### 18.4 验证与确认原则（V\&V）

| #  | 原则            | 要点                       | 文档对应                         |
| -- | ------------- | ------------------------ | ----------------- |
| 16 | **分层测试，逐级验证** | 单元 → 接口集成 → 系统 → 性能，四层递进 | §十三 V 模型测试策略                 |
| 17 | **异常路径必须覆盖**  | 每个方法的异常路径有明确断言           | §13.2 异常覆盖率要求                |
| 18 | **回归测试自动化**   | 组件升级必须触发完整测试套件           | §13.3 回归测试自动化                |
| 19 | **风险驱动验证**    | 不等整体框架完成，先排除 P0 风       | §十二 Sprint 0、§14.1 PoC 验证优先级 |
| 20 | **性能指标可测量**   | 每个指标有明确的测量方法和条件          | §0.1 测量方法列                   |

### 18.5 演进与债务管理原则

| #  | 原则           | 要点                             | 文档对应              |
| -- | ------------ | ------------------------------ | ----------------- |
| 21 | **扩展点有触发条件** | 每个预留扩展点定义明确的触发条件               | §十七 æ演进路线图       |
| 22 | **不过早优化**    | 当前方案够用就不升级，等触发条件满足再演进          | §十七 触发条件列         |
| 23 | **技术债务显式管理** | 已知债务清单 + 偿还触发条件                | §0.3 总体架构师职责第 4 条 |
| 24 | **替换 = 换目录** | 组件替换只需替换整个 layer 目录 + 改 import | §1.1 分层隔离原则       |

### 18.6 数据架构原则

| #  | 原则            | 要点                       | 文档对应             |
| -- | ------------- | --------------------------------------- | ---------------- |
| 25 | **唯一写入入口**    | Iceberg 是唯一数据写入入口，Doris 不承接写请求          | §1.2 约束 #3、#4    |
| 26 | **索引 ≠ 数据基准** | Doris 仅存索引列+热点列，全量数据在 Iceberg           | §2.4 Doris 索引表红线 |
| 27 | **读写分离**      | 写入走 SeaTunnel → Iceberg，读取走 Doris/Trino | §七 数据流场景         |
| 28 数据与数据分离**  | PG 存业务元数据，Gravitino 存物理元数据，Iceberg 存数据  | §1.2 约束 #1、#2    |
| 29 | **最终一致可接受**   | 索引同步允许秒/分钟级延迟，数据基准强一致                   | §2.2 数据一致性行      |

### 18.7 评审检查表

> 每次架构评审、PR Review、组件升级时，逐项检查以下条目：

- [ ] 所有 Layer 公开方法签名是否与 ICD 基线一致？
- [ ] 新增依赖是否引入了跨层耦合？
- [ ] é？
- [ ] 新增代码的异常路径是否有测试覆盖？
- [ ] 0.1 节关键指标是否有劣化？
- [ ] 组件版本是否与 §1.3 版本矩阵一致？
- [ ] 配置变更是否已纳入版本控制？
- [ ] 是否有新的架构决策需要记录 ADR？
- [ ] Doris 索引表是否仅包含主键 + 索引列 + 热点属性？
- [ ] 数据写入是否仅通过 Iceberg（非 Doris）？

***

## 十九、交付物清单

1. `core/models/` — 领域模型（SQLAlchemy ORM + pydantic Schema）
2. `个层实现（含 PostgresMetaStore）
3. `services/` — 5 个业务编排服务（含降级逻辑）
4. `config/container.py` — 依赖注入容器
5. `routes/` — HTTP 路由（Palantir 形状 API）
6. `docker-compose.yml` — 一键启动分层栈（7 个组件，含 healthcheck + restart）
7. `infra/` — 初始化脚本（含 PG schema）
8. `middleware/` — 结构化日志 + trace\_id + Prometheus Metrics
9. 单元测试 + 接口集成测试 + E2E 测试 + 故障注入测试 + 性能测试
10. ADR 文档（6 个架构决策记录）
11. ICD 文档（6 个接口控制文档）
12. Grafana 面板配置（各层健康度 + 查询延迟 + 同步延迟）

***

## 附录：系统工程原则与最佳实践记录

> 以下原则从中国航天系统工程总体设计思想提炼，作为本项目全生命周期的工程指南。

### A.1 总体设计原则

| 原则             | 说明                         | 在本项目中的体现                       |
| -------------- | ------------------------ | ------------------------------ |
| **一切从任务总目标出发** | 先定义"做什么"和"做到什么程度"，再决定"怎么做" | 0.1 节关键能力等级作为验收基线，所有层实现服务于这些指标 |
| **逐层分解，全局最优**  | 各分系统独立优化不能损害整体性能           | 0.2 节三维分解矩阵确保每项能力有明确的责任组件和依赖条件 |
| **总体架构师统一协调**  | 跨层变更必须经过总体评审               | 0.3 节å¶构师职责，ICD 基线管控        |

### A.2 综合集成原则

| 原则             | 说明                | 在本项目中的体现                    |
| -------------- | ----------------- | --------------------------- |
| **重视组网后的涌现行为** | 独立组件正常 ≠ 集成后正常    | 第八节 FMEA 分析每个组件失效的级联影响      |
| **定义系统运行状态向量** | 关键指标有正常/预警/告警三级阈值 | 8.5 节运行状态向量 + 9.4 节可观æ全链路故障注入验证**  | 不仅验证正常路径，还要验证异常路径 | 阶段 5 故障注入测试（kill 组件、注入网络延迟） |

### A.3 技术状态管理原则

| 原则          | 说明                        | 在本项目中的体现                                                |
| ----------- | ------------------------- | ------------------------------------------------------- |
| **架构决策可追溯** | 每个关键技术选择有文档记录             | 第十五节 ADR，含背景、决策、后果、替代方案                                 |
| **接口基线版本化** | 层间接口随代码版本控制               | 第十六节 ICD，任何修改需通过测试和评审                                   |
| **配置基线化**   | 所有组件配置纳入版本控制，环境差异通过环境变量覆盖 | docker-compose.yml + .env.example + config/ 目录全部 Git 管理 |
| **严禁手动热改**  | 生产环境配置变更必须走 CI/CD 流水线  置变更通过 Git PR → CI 验证 → 部署                              |

### A.4 验证与确认原则（V\&V）

| 原则            | 说明                       | 在本项目中的体现                    |
| ------------- | ------------------------ | --------------------------- |
| **分层测试，逐级验证** | 单元 → 接口集成 → 系统 → 性能，四层递进 | 第十三节 V 模型测试策略               |
| **异常路径必须覆盖**  | 每个方法的异常路径有明ç­言           | 13.2 节异常覆盖率要求               |
| **回归测试自动化**   | 组件升级必须触发完整测试套件           | 13.3 节回归测试自动化               |
| **风险驱动验证**    | 不等整体框架完成，先排除 P0 风险       | Sprint 0 + 14.1 节 PoC 验证优先级 |

### A.5 演进与债务管理原则

| 原则           | 说明                    | 在本项目中的体现          |
| ------------ | --------------------- | ----------------- |
| *| 每个预留扩展点定义明确的触发条件      | 第十七节架构演进路线图       |
| **不过早优化**    | 当前方案够用就不升级，等触发条件满足再演进 | 第十七节触发条件列         |
| **技术债务显式管理** | 已知债务清单 + 偿还触发条件       | 0.3 节总体架构师职责第 4 条 |
