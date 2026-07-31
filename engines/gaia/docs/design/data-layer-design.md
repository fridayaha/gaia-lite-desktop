# Gaia 数据层设计 - DataSource / Dataset / ObjectType 完整方案

> **版本**:v2.0
> **对标系统**:Palantir Foundry Data Connection + Dataset + Ontology
> **核心目标**:在 Gaia 开源分层架构上,构建从"外部数据接入"到"本体对象映射"的完整数据链路。向上支撑 Ontology 语义层,向下对接 Gravitino/SeaTunnel/Trino/Iceberg/Doris 五大开源组件。
>
> **前置文档**:[架构设计评审 (v5)](./architecture_plan.md)
>
> **关联文档**:
> - [前端数据层设计](./frontend-data-layer-design.md) - 组件树 & 交互流
>
> **工程原则**:遵循 [CLAUDE.md](../CLAUDE.md) 四条核心设计哲学。

---

## 目录

- [一、架构定位与边界](#一架构定位与边界)
- [二、领域模型设计](#二领域模型设计)
- [三、分层职责更新](#三分层职责更新)
- [四、完整数据流](#四完整数据流)
- [五、DataSourceService 编排设计](#五datasourceservice-编排设计)
- [六、REST API 设计](#六rest-api-设计)
- [七、前端交互设计](#七前端交互设计)
- [八、PG Schema](#八pg-schema)
- [九、MVP 范围](#九mvp-范围)
- [十、遗留项](#十遗留项)

---

## 一、架构定位与边界

### 1.1 三个核心概念 + Capability 模型

Palantir 的设计哲学是「一个 Source 一次配置,多种能力复用」。我们对标此模型,在 Gaia 中实现:

```
DataSource (一次配置,多种能力)
├── 📋 探索 Schema       → Trino → Gravitino JDBC Catalog → 外部 DB
├── 📊 抽样预览数据       → Trino → Gravitino JDBC Catalog
├── 🔄 批量同步 (表)      → SeaTunnel → Iceberg (transaction_type: SNAPSHOT/APPEND)
├── 📁 文件同步           → SeaTunnel → Iceberg (S3/HDFS connector)
├── ⚡ CDC 增量同步       → SeaTunnel PostgreSQL-CDC → Iceberg
└── 👻 虚拟表(Virtual Table, P2) → 外部表联邦代理 → Trino 直读
```

**与 Palantir 的对齐**（术语基准见 [dataset-ontology-binding.md](./dataset-ontology-binding.md) §一）：
- Virtual Table = 外部数据源表（MySQL/PG/Snowflake/BigQuery...）的联邦代理指针，不落地，Trino 联邦查询。**MySQL/PG 同样支持**（通过 Gravitino JDBC catalog 登记为 Virtual Table），并非"必须先同步到 Iceberg"。
- 虚拟表在 P2 阶段通过 Gravitino JDBC catalog + Trino 联邦实现，**不走 Gravitino SQL View**（该线路已废弃，见下文）。
- 已废弃的错误定义："虚拟表 = Gravitino View"、"MySQL/PG 不支持虚拟表"。
- Palantir 的一个 Source 可复用多个 Capability 得益于其一体化架构。我们用 `capabilities` 派生字段(由 connector_type 在 API 序列化时自动推断)来模拟此概念。

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  DataSource  │────→│   Dataset    │────→│  ObjectType  │
│  (外部连接)   │sync │ (平台内载体) │map  │  (业务本体)   │
│  PG 存储     │     │ Iceberg 存储 │     │  PG 存储     │
│  Gravitino   │     │ Gravitino   │     │  Gravitino   │
│  注册为      │     │ 注册为       │     │ 注册为      │
│  JDBC Catalog│     │ Iceberg 表  │     │ View/Virtual │
└──────────────┘     └──────────────┘     └──────────────┘
                            │
                            │ transform
                            ▼
                     ┌──────────────┐
                     │   Dataset'   │  ← 可进一步加工,链路不限长
                     └──────────────┘
```

| 概念 | 用户语言 | 存储层 | 生命周期 |
|------|---------|--------|---------|
| **DataSource** | "我连接了 ERP 数据库" | PG(元数据) + Gravitino(JDBC Catalog) | 创建 → 连接测试 → 同步管理 → 废弃 |
| **Dataset** | "我的订单原始数据" | Iceberg(数据文件) + Gravitino(表注册) + PG(治理元数据) | 同步创建 → 持续更新 → 加工 → 留存清理 |
| **ObjectType** | "订单" 业务对象 | PG(本体元数据)+ Doris(索引)+ Iceberg(全量数据读路径) | 定义 → 映射 → 使用 → 废弃 |

### 1.2 关键隔离原则

| # | 原则 | 实现 |
|---|------|------|
| 1 | **ObjectType 不感知 DataSource** | `PhysicalColumnRef` 只引用 `dataset_api_name`,不引用 `data_source_api_name` |
| 2 | **DataSource → Dataset 不是 1:1** | 一个 DataSource 可同步生成多个 Dataset;一个 Dataset 可加工出另一个 Dataset |
| 3 | **Dataset 是加工链的节点** | `orders_raw` → `orders_clean` → `orders_agg`,每个都是独立 Dataset |
| 4 | **Gravitino 统一元数据（Catalog First）** | DataSource 注册为 JDBC Catalog,Dataset 注册为 Iceberg 表,Trino 通过 Gravitino Connector 一站式发现。**托管表由 Gaia 调用 Gravitino(Iceberg REST)建表**(带主键/注释/NULL/表属性),**SeaTunnel 只写数据不建表**(`schema_save_mode=IGNORE`);PG `datasets` 治理表不存物理 schema。详见 `CLAUDE.md` §Gravitino Catalog First 原则 |

### 1.3 添加 DataSourceService 后的分层总览

```
Routes(HTTP 薄层)
    ↓
Services(业务编排层)
├── OntologyService          ← 本体管理(已有)
├── ObjectQueryService       ← 核心查询(已有)
├── TimeTravelService        ← 时间旅行(已有)
├── ActionService            ← 写入编排(已有)
└── DataSourceService        ← 【新增】数据源管理 + 探索 + 同步编排
    ↓ 依赖各层

> 注：原 `VirtualTableService`（查 Gravitino SQL View）已删除，其语义与 Palantir Virtual Table 冲突。
> 虚拟表查询现由 ObjectQueryService 按 `storage_type=VIRTUAL` 走 Trino 联邦查询 Virtual Table 指向的外部表。
> 详见 [dataset-ontology-binding.md](./dataset-ontology-binding.md) §3.4。
Layer Implementations(层实现)
├── Catalog(Gravitino)      ← 新增:动态注册 JDBC Catalog
├── Metadata(PostgreSQL)    ← 新增:DataSource / Credential / SyncTask 表
├── Dataset(Iceberg)       ← 不变
├── Index(Doris)           ← 不变
├── Pipeline(SeaTunnel)    ← 不变:执行 SyncTask 对应的 MAIN 流水线
└── Engine(Trino)          ← 新增:探索查询(通过 Gravitino Connector)
Core Models(领域模型)
```

---

## 二、领域模型设计

> 以下使用 Python pydantic 风格描述领域模型,与 `core/schemas/` 目录一一对应。

### 2.1 DataSource(数据源)

```python
# core/schemas/datasource.py

class DataSource(BaseModel):
    """外部数据源实例 - 用户视角的"我连接了一个 MySQL/S3/Kafka"。

    对标 Palantir Source。
    存储层:PG Metadata。
    同时注册为 Gravitino 的 JDBC/Fileset Catalog(支持 Virtual Table 联邦场景)。

    示例:
      DataSource(
          api_name="erp_mysql_prod",
          connector_type="mysql",
          connector_config={"host": "10.0.1.5", "port": 3306, "database": "erp_prod"},
          credential_id="cred_001",
      )
    """
    id: str                          # UUID v4
    api_name: str                    # 全局唯一,如 "erp_mysql_prod"
    display_name: str                # "ERP 生产库"
    description: str = ""
    connector_type: str              # "mysql" | "postgresql" | "s3" | "kafka" | ...
    connector_config: dict[str, Any]  # JSONB - host/port/database/SSL 等非敏感连接参数
    credential_id: str | None = None # FK → Credential
    status: str = "DISCONNECTED"     # CONNECTED | DISCONNECTED | ERROR
    created_at: datetime
    updated_at: datetime

    # 以下字段由系统维护,用户不可见
    gravitino_catalog_name: str = ""  # Gravitino 中对应的 Catalog 名称(自动生成 = api_name)
    capabilities: list[str] = []       # 【派生字段】由 connector_type 推断,API 序列化时自动填充

# capability 映射表(由 connector_type 推断,不存 DB)
CAPABILITY_MAP = {
    "mysql":       ["explore", "batch_sync", "cdc"],
    "postgresql":  ["explore", "batch_sync", "cdc"],
    "s3":          ["explore", "file_sync"],
    "kafka":       ["explore", "streaming_sync"],
}
```

### 2.2 Credential(凭证)

```python
class Credential(BaseModel):
    """凭证 - 外部系统认证凭据。

    存储层:PG,secret_data 列明文 JSON。
    API 响应中永远不返回 secret_data 明文(脱敏为 "***")。

    TODO(SEC-001): AES-256-GCM encrypt secret_data at rest before production deployment.

    示例:
      Credential(
          api_name="erp_readonly",
          credential_type="username_password",
          secret_data={"username": "gaia_sync", "password": "xxx"},
      )
    """
    id: str
    api_name: str
    credential_type: str            # "username_password" | "access_key" | "token"
    secret_data: dict[str, Any]     # 明文 JSON(遗留),API 响应中脱敏
    created_at: datetime
    # 注意:无 updated_at - 凭证只允许替换(DELETE + CREATE),不允许修改
```

### 2.3 SyncTask(同步任务)

```python
class SyncTask(BaseModel):
    """数据同步任务 - DataSource → Dataset 的执行单元。

    一个 SyncTask 对应 SeaTunnel 的一个 MAIN Pipeline。
    DataSourceService 根据 SyncTask 配置自动生成 SeaTunnel HOCON 配置并部署。

    存储层:PG Metadata。

    示例(增量同步 MySQL orders 表):
      SyncTask(
          api_name="sync_erp_orders",
          data_source_id="ds_001",
          source_config={"table": "orders", "incremental_column": "updated_at", "incremental_start": "2025-01-01"},
          target_dataset_api_name="orders_raw",
          sync_mode="incremental",
          transaction_type="append",
      )
    """
    id: str
    api_name: str
    data_source_id: str                      # FK → DataSource
    sync_type: str = "table"                  # 【新增】"table" | "file"
    source_config: dict[str, Any]            # JSONB - {table, query, incremental_column, file_pattern, ...}
    target_dataset_api_name: str             # 目标 Dataset 名
    sync_mode: str = "full_snapshot"         # "full_snapshot" | "incremental"
    transaction_type: str = "snapshot"       # 映射到 Iceberg 事务类型:"snapshot" | "append" | "update"
    allow_schema_changes: bool = False       # 【新增】是否允许源端 Schema 变更后继续同步
    max_duration_minutes: int | None = None  # 【新增】超时取消
    file_filters: dict[str, Any] | None = None  # 【新增】文件同步专用过滤
    schedule: dict[str, Any] | None = None   # JSONB - {cron: "0 1 * * *", interval_minutes: 15}
    status: str = "DRAFT"                    # DRAFT | RUNNING | STOPPED | FAILED
    pipeline_name: str | None = None         # 对应的 SeaTunnel job name(系统自动生成)
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
```

### 2.4 Dataset(增强已有模型)

```python
# core/schemas/dataset.py - 需增强

class Dataset(BaseModel):
    """Dataset(Iceberg 表)- 平台内数据载体。

    对标 Palantir Dataset。
    存储层:Iceberg(数据文件)+ PG(治理元数据)+ Gravitino(物理表注册)。

    新增字段:
      - source_dataset_api_name: 加工来源(从哪个 Dataset 加工而来),null 表示来自外部数据源同步
      - kind: 资源类型 MANAGED(托管表,落地 Iceberg) | VIRTUAL(虚拟表,外部联邦代理,不落地)
      - is_view: 仅 MANAGED 的子类型标记(Foundry View,未实现,恒 false);语义已收窄,不再表示 Gravitino View
    """
    name: str                                # 如 "orders_raw"
    schema_: DatasetSchema = Field(alias="schema")
    storage_location: str                    # MANAGED: Iceberg 路径; VIRTUAL: "catalog.schema.table" 三段式定位符
    partition_spec: list[PartitionField] = Field(default_factory=list)
    # ── 新增字段 ──
    source_dataset_api_name: str | None = None  # 加工来源:null=外部同步, 非null=转换而来
    data_source_api_name: str | None = None     # 外部数据源(仅 source_dataset_api_name=null 时有值)
    kind: Literal["MANAGED", "VIRTUAL"] = "MANAGED"  # 资源类型
    is_view: bool = False                       # 仅 MANAGED 子类型标记(Foundry View,未实现)
    row_count_estimate: int | None = None       # 行数估算(周期性更新)
    created_at: datetime
    updated_at: datetime

# 以下已有模型不变
class DatasetSchema(BaseModel):
    columns: list[ColumnDef]

class ColumnDef(BaseModel):
    name: str
    type: str
    nullable: bool = True

class PartitionField(BaseModel):
    source_column: str
    transform: Literal["identity", "year", "month", "day", "hour", "bucket"]
    transform_param: int | None = None

class DatasetSnapshot(BaseModel):
    snapshot_id: int
    timestamp: int
    operation: Literal["append", "overwrite", "delete"] = "append"
    summary: dict[str, Any] = Field(default_factory=dict)

class WriteResult(BaseModel):
    snapshot: DatasetSnapshot
    rows_written: int
```

### 2.5 PhysicalColumnRef 增强

```python
# core/schemas/ontology.py - PhysicalColumnRef 需增强

class PhysicalColumnRef(BaseModel):
    """物理列引用 - 将 ObjectType 属性桥接到 Dataset 的物理列。

    不依赖 DataSource,只依赖 Dataset。
    """
    dataset_api_name: str    # 【新增】Dataset 的 api_name,替代原 data_source_api_name
    catalog_name: str        # Iceberg catalog 名
    schema_name: str         # Schema 名
    table_name: str          # 物理表名(与 dataset_api_name 对应)
    column_name: str         # 物理列名
```

### 2.6 Source Config 标准化

```python
# sync_type = "table" 时
source_config = {
    "table": "orders",               # 表名
    "query": None,                   # 或自定义 SQL(二选一)
    "incremental_column": "updated_at",
    "incremental_start": "2025-01-01",
}

# sync_type = "file" 时
source_config = {
    "subfolder": "/data/orders/",    # 文件路径
    "format": "parquet",             # 文件格式
}

file_filters = {
    "exclude_synced": True,          # 排除已同步文件
    "path_pattern": "*.parquet",     # 路径正则
    "modified_after": "2025-01-01",
    "max_files_per_sync": 1000,
}
```

### 2.7 探索结果模型(只读,不持久化)

```python
# core/schemas/datasource.py - 探索结果

class TableInfo(BaseModel):
    """数据源探索结果:表/集合/主题 信息"""
    name: str
    schema_: str = Field(default="", alias="schema")
    row_count_estimate: int | None = None
    columns: list["ColumnInfo"] = Field(default_factory=list)

class ColumnInfo(BaseModel):
    """数据源探索结果:列/字段 信息"""
    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    comment: str = ""
```

---

## 三、分层职责更新

### 3.1 GravitinoRegistry(Catalog 层)- 新增方法

```python
class GravitinoRegistry:
    # ══ 已有方法(不变)══
    async def register_dataset(...) -> None: ...
    async def is_view(...) -> bool: ...            # 运行时探测某表是否为 Gravitino view,保留
    async def check_access(...) -> bool: ...
    async def resolve_physical_table(...) -> dict[str, str]: ...
    async def get_table_columns(...) -> list[dict]: ...  # 联邦拉列,VIRTUAL 虚拟表 schema 用
    # create_view 已删除(Gravitino SQL View 线路废弃)

    # ══ 新增:动态 Catalog 管理 ══
    async def register_jdbc_catalog(
        self,
        catalog_name: str,
        provider: str,               # "jdbc-mysql" | "jdbc-postgresql" | ...
        properties: dict[str, str],  # jdbc-url, jdbc-user, jdbc-password, jdbc-driver
    ) -> None:
        """动态注册 JDBC Catalog 到 Gravitino。

        POST /api/metalakes/ontology/catalogs
        {
            "name": catalog_name,
            "type": "relational",
            "provider": provider,
            "comment": "...",
            "properties": {
                "jdbc-url": "jdbc:mysql://...",
                "jdbc-user": "...",
                "jdbc-password": "...",
                "jdbc-driver": "com.mysql.cj.jdbc.Driver"
            }
        }

        Gravitino 1.2.0 支持 REST API 动态创建,即时生效,无需重启。
        多 Catalog 并存:Gravitino 自身的元数据存储后端 (gravitino.conf 中的 backend=jdbc)
        与动态注册的外部数据源 Catalog 是独立的概念。

        前提:JDBC 驱动 jar 包需预先放入 Gravitino 的插件目录:
          ${GRAVITINO_HOME}/catalogs/jdbc-mysql/libs/mysql-connector-j-xxx.jar
          ${GRAVITINO_HOME}/catalogs/jdbc-postgresql/libs/postgresql-xxx.jar

        Trino Gravitino Connector 自动发现新 Catalog。
        """

    async def register_fileset_catalog(
        self,
        catalog_name: str,
        provider: str,               # "fileset-s3" | "fileset-hdfs"
        properties: dict[str, str],
    ) -> None:
        """动态注册 Fileset Catalog(S3/HDFS 等对象存储)。

        POST /api/metalakes/ontology/catalogs
        { name: catalog_name, type: "fileset", provider: provider, properties: {...} }
        """

    async def remove_catalog(self, catalog_name: str) -> None:
        """移除 Gravitino Catalog(DataSource 废弃时调用)。"""

    async def list_catalogs(self) -> list[dict[str, Any]]:
        """列出所有已注册的 Catalog(含 DataSource Catalog + 内置 Iceberg Catalog)。"""
```

### 3.2 PostgresMetaStore(Metadata 层)- 新增方法

```python
class PostgresMetaStore:
    # ══ 已有方法(不变)══
    async def create_ontology(...) -> None: ...
    async def get_object_type(...) -> ObjectType | None: ...
    ...

    # ══ 新增:DataSource / Credential / SyncTask CRUD ══
    async def create_datasource(self, ds: DataSource) -> DataSource: ...
    async def get_datasource(self, api_name: str) -> DataSource | None: ...
    async def list_datasources(self) -> list[DataSource]: ...
    async def update_datasource(self, id: str, updates: dict[str, Any]) -> None: ...
    async def delete_datasource(self, id: str) -> None: ...

    async def create_credential(self, cred: Credential) -> Credential: ...
    async def get_credential(self, api_name: str) -> Credential | None: ...
    async def delete_credential(self, id: str) -> None: ...

    async def create_sync_task(self, task: SyncTask) -> SyncTask: ...
    async def get_sync_task(self, api_name: str) -> SyncTask | None: ...
    async def list_sync_tasks_for_datasource(self, datasource_id: str) -> list[SyncTask]: ...
    async def update_sync_task(self, id: str, updates: dict[str, Any]) -> None: ...

    # ── Dataset 治理元数据(新增)──
    async def register_dataset(self, ds: Dataset) -> Dataset: ...
    async def get_dataset(self, api_name: str) -> Dataset | None: ...
    async def list_datasets(self) -> list[Dataset]: ...
    async def update_dataset_stats(self, api_name: str, row_count: int) -> None: ...

    # ── 对象类型物理映射查询(新增)──
    async def get_object_types_for_dataset(
        self, dataset_api_name: str
    ) -> list[ObjectType]:
        """查询哪些 ObjectType 映射到了指定 Dataset 的列。
        用于删除 Dataset 前的级联影响分析。
        """
```

### 3.3 TrinoQueryEngine(Engine 层)- 职责不变,新增 SQL 辅助

```python
class TrinoQueryEngine:
    # 已有方法不变
    async def query(self, sql: str) -> list[dict[str, Any]]: ...

    # ── 新增:探索查询辅助方法 ──
    async def list_tables(self, catalog: str, database: str) -> list[str]:
        """SHOW TABLES FROM {catalog}.{database}"""
        ...

    async def describe_table(
        self, catalog: str, database: str, table: str
    ) -> list[dict[str, Any]]:
        """DESCRIBE {catalog}.{database}.{table}"""
        ...

    async def sample_data(
        self, catalog: str, database: str, table: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """SELECT * FROM {catalog}.{database}.{table} LIMIT {limit}"""
        ...
```

### 3.4 SeaTunnelEngine(Pipeline 层)- 模板增强

当前 `create_main_pipeline()` 的 Source 模板为通用 JDBC 模板。需增加 S3/Kafka Source 模板变体:

```python
class SeaTunnelEngine:
    # 已有方法不变
    async def create_main_pipeline(...) -> PipelineDef: ...
    async def create_index_sync_pipeline(...) -> PipelineDef: ...
    ...

    # ── 增强:根据 connector_type 选择正确的 Source 模板 ──
    async def create_sync_pipeline(
        self,
        connector_type: str,
        source_config: dict[str, Any],
        target_dataset: str,
        transforms: list[dict[str, Any]] | None = None,
    ) -> PipelineDef:
        """根据 connector_type 自动选择 SeaTunnel Source 模板(JDBC/S3/Kafka/...)。

        内部路由:
          mysql|postgresql|oracle|sqlserver → JDBC Source 模板
          s3 → S3 File Source 模板
          kafka → Kafka Source 模板
        """
```

---

## 四、完整数据流

### 4.1 场景 A:MySQL → Dataset → ObjectType(完整链路)

```
用户                            Gaia 系统                           开源组件
────────                    ────────────                       ──────────

1 创建数据源
  填写连接信息
  测试连接 ────────────→ DataSourceService.create_datasource()
                              │
                              ├─ PG: INSERT data_sources
                              ├─ Gravitino: register_jdbc_catalog("erp_mysql", "jdbc-mysql", ...)
                              └─ Trino: 自动发现新 Catalog (Gravitino Connector 动态刷新)

2 探索数据源
  选择数据库 erp ──────→ DataSourceService.explore()
  看到表列表                │
  - orders (120万行)        └─ Trino: SHOW TABLES FROM gravitino.erp_mysql.erp
  - customers (8万行)           DESCRIBE gravitino.erp_mysql.erp.orders

3 创建同步任务
  选择 orders 表 ──────→ DataSourceService.create_sync_task()
  点击"智能推荐"              │
  AI 推断:                   ├─ AI: 调用 /ai/stream (SYNC_MODE_INFERENCE_PROMPT)
    增量 + update_time       │   分析 columns + row_count → 推荐 sync_mode/transaction_type
  用户确认 ──────────→       │
                              ├─ Iceberg: 创建 orders_raw 表(基于 MySQL schema 推断)
                              ├─ SeaTunnel: submit job (JDBC Source → Iceberg Sink)
                              └─ PG: INSERT sync_tasks

4 启动同步              → DataSourceService.start_sync()
                              └─ SeaTunnel: start job

5 同步执行
                              SeaTunnel 执行:
                                MySQL orders ──→ Iceberg orders_raw (snapshot #1)
                              Gravitino: Iceberg REST Catalog 自动注册

6 创建本体对象
  在 ObjectType 编辑中    → DataSourceService.list_datasets()
  选择 Dataset: orders_raw    返回 [{ name: "orders_raw", schema: {...} }]
  字段映射:
    order_no → 订单编号     → AI 辅助: DATASOURCE_MAPPING_PROMPT
    amount   → 金额
    status   → 状态
  保存 ────────────────→ OntologyService.define_object_type()
                              ├─ PG: INSERT object_types + properties (含 physical_mapping)
                              └─ Doris: CREATE INDEX TABLE idx_orders
```

### 4.2 场景 B:Dataset → Dataset 加工链路

```
orders_raw (Iceberg, APPEND, 来自 MySQL 同步)
    │
    │ SeaTunnel Transform Pipeline
    │ (SQL: 清洗 + 去重 + 标准化)
    ▼
orders_clean (Iceberg, SNAPSHOT daily)
    │
    │ SeaTunnel Transform Pipeline
    │ (SQL: 聚合统计)
    ▼
orders_daily_agg (Iceberg, SNAPSHOT daily)
    │
    │ Map to ObjectType
    ▼
ObjectType "订单日报" (VIRTUAL storage_type → Virtual Table over orders_daily_agg)
```

**实现要点**:
- `orders_clean.source_dataset_api_name = "orders_raw"`
- `orders_daily_agg.source_dataset_api_name = "orders_clean"`
- 加工链路自动记录血缘(预留 Gravitino 血缘字段)

### 4.3 场景 C:虚拟表直读(无数据搬迁)

```
用户创建 DataSource: Snowflake 数仓
    ↓
Gravitino 注册为 JDBC Catalog
    ↓
登记 Virtual Table: 指向 Snowflake 外部表（POST /datasources/{ds}/virtual-tables）
    ↓
ObjectType (storage_type=VIRTUAL) 映射到 Virtual Table
    ↓
用户查询 ObjectType → Trino 联邦查询外部表 → Snowflake 实时计算
```

> 注：原场景描述"Gravitino 创建 View"已废弃。Virtual Table 是外部表的联邦代理指针，
> 不创建任何 Gravitino SQL View。详见 [dataset-ontology-binding.md](./dataset-ontology-binding.md) §3.2。

### 4.4 降级场景

| 故障 | DataSource 链路影响 | 降级行为 |
|------|-------------------|---------|
| Gravitino 不可用 | 无法创建/探索 DataSource | 返回明确错误,提示稍后重试 |
| Trino 不可用 | 探索不可用,同步任务不受影响 | 探索 API 返回 503,同步任务正常运行(SeaTunnel 直连) |
| SeaTunnel 不可用 | 同步任务无法执行 | SyncTask status → FAILED,支持手动重试 |
| Iceberg 不可用 | 同步任务写入失败 | SeaTunnel 自动重试(指数退避,最多 10 次),源端 CDC offset 不提交 |
| 外部数据源不可用 | 同步任务暂停 | SyncTask status → ERROR, SeaTunnel 按调度周期自动重试 |

---

## 五、DataSourceService 编排设计

```python
# services/datasource_service.py

class DataSourceService:
    """数据源管理编排服务。

    协调 Metadata (PG) + Catalog (Gravitino) + Engine (Trino)
    + Pipeline (SeaTunnel) + Dataset (Iceberg) 五层完成数据接入全流程。
    """

    def __init__(
        self,
        metadata: PostgresMetaStore,
        catalog: GravitinoRegistry,
        engine: TrinoQueryEngine,
        pipeline: SeaTunnelEngine,
        dataset: IcebergStore,
    ) -> None: ...

    # ══════════════════════════════════════════════════════════
    # DataSource 生命周期
    # ══════════════════════════════════════════════════════════

    async def create_datasource(self, ds: DataSourceCreate) -> DataSource:
        """创建数据源。

        1. PG 存储 DataSource 记录
        2. Gravitino 注册 JDBC/Fileset Catalog
           - "mysql", "postgresql", "oracle", "sqlserver" → JDBC Catalog
           - "s3", "hdfs" → Fileset Catalog
           - "kafka" → 不注册 Catalog(消息无 schema 管理需求,P2 再评估)
        """
        ...

    async def test_connection(self, datasource_api_name: str) -> ConnectionTestResult:
        """测试数据源连接。

        通过 Trino 查询 Gravitino Catalog 验证连通性:
          SELECT 1 FROM gravitino.{catalog_name}.INFORMATION_SCHEMA.TABLES LIMIT 1
        """
        ...

    # ══════════════════════════════════════════════════════════
    # 数据探索
    # ══════════════════════════════════════════════════════════

    async def explore(
        self, datasource_api_name: str, database: str
    ) -> list[TableInfo]:
        """探索数据源的表结构和行数估算。

        通过 Trino 查询 Gravitino Catalog:
          1. SHOW TABLES FROM gravitino.{ds.api_name}.{database}
          2. 每张表: DESCRIBE + SELECT COUNT(*) (或系统表估算)
        """
        ...

    # ══════════════════════════════════════════════════════════
    # 同步任务生命周期
    # ══════════════════════════════════════════════════════════

    async def create_sync_task(self, task: SyncTaskCreate) -> SyncTask:
        """创建同步任务。

        1. 从 DataSource 获取连接信息 + 凭证
        2. 从探索结果获取源表 Schema
        3. Iceberg 建表(基于源 Schema 推断,字段类型映射)
        4. 生成 SeaTunnel HOCON 配置
        5. 部署到 SeaTunnel Zeta
        6. PG 存储 SyncTask 记录
        """
        ...

    async def start_sync(self, task_api_name: str) -> None:
        """启动同步任务。首次执行触发 Iceberg SNAPSHOT 事务。"""

    async def stop_sync(self, task_api_name: str) -> None:
        """停止同步任务。"""

    async def get_sync_status(self, task_api_name: str) -> SyncTaskStatus:
        """查询同步状态(从 PG + SeaTunnel 综合获取)。"""

    # ══════════════════════════════════════════════════════════
    # AI 辅助推断
    # ══════════════════════════════════════════════════════════

    async def infer_sync_config(
        self, datasource_api_name: str, table_name: str
    ) -> SyncConfigInference:
        """AI 推断最佳同步配置。

        传入表结构 + 行数估算 → AI 返回推荐的 sync_mode, transaction_type,
        incremental_column, partition_column。

        由前端调用 /ai/stream (SYNC_MODE_INFERENCE_PROMPT) 并传入
        table_info JSON,后端不持有 prompt 逻辑。
        """
        table_info = await self.explore(datasource_api_name, table_name)
        return SyncConfigInference(table_info=table_info)  # 返回给前端,前端调 AI

    # ══════════════════════════════════════════════════════════
    # Dataset 管理
    # ══════════════════════════════════════════════════════════

    async def list_datasets(self) -> list[Dataset]:
        """列出所有可用的 Dataset(供 ObjectType 映射时选择)。"""

    async def get_dataset_schema(self, dataset_api_name: str) -> DatasetSchema:
        """获取 Dataset 的 Schema(供前端 SchemaViewer 组件渲染字段列表)。"""

    # ══════════════════════════════════════════════════════════
    # 级联影响分析
    # ══════════════════════════════════════════════════════════

    async def preview_impact(
        self, target_api_name: str, target_type: str, action: str
    ) -> ImpactAnalysis:
        """分析操作的影响范围。

        target_type: "datasource" | "dataset" | "sync_task"
        action: "delete"

        返回:
          - severity: LOW | MEDIUM | HIGH
          - impacts: [{ type: "object_type"|"sync_task"|"link_type", api_name: ..., effect: "..." }]

        用于前端分级确认弹窗(低危→弹窗,中危→列出影响,高危→输入名称确认)。
        """
```

---

## 六、REST API 设计

### 6.1 路由结构

```
POST   /datasources                              # 创建数据源
GET    /datasources                              # 列出所有数据源
GET    /datasources/{api_name}                   # 获取数据源详情
PATCH  /datasources/{api_name}                   # 更新数据源
DELETE /datasources/{api_name}                   # 删除数据源(含影响分析)

POST   /datasources/{api_name}/test-connection   # 测试连接

POST   /datasources/{api_name}/explore           # 探索数据源
         { database: "erp_prod" }                      → [TableInfo, ...]
GET    /datasources/{api_name}/explore/{database}/{table}/sample
         ?limit=10                                      → { columns: [...], rows: [...] }

POST   /datasources/{api_name}/sync-tasks       # 创建同步任务
GET    /datasources/{api_name}/sync-tasks       # 列出数据源的同步任务
GET    /sync-tasks/{api_name}                   # 获取同步任务详情
POST   /sync-tasks/{api_name}/start             # 启动同步
POST   /sync-tasks/{api_name}/stop              # 停止同步
DELETE /sync-tasks/{api_name}                   # 删除同步任务

POST   /credentials                              # 创建凭证
GET    /credentials                              # 列出凭证(脱敏)
DELETE /credentials/{api_name}                   # 删除凭证

GET    /datasets                                 # 列出所有 Dataset
GET    /datasets/{api_name}                      # 获取 Dataset 详情
GET    /datasets/{api_name}/schema               # 获取 Dataset Schema
GET    /datasets/{api_name}/snapshots            # 获取快照历史

POST   /impact-analysis                          # 级联影响分析
         { target_api_name: "...", target_type: "dataset", action: "delete" }
         → ImpactAnalysis
```

### 6.2 请求/响应示例

```json
// POST /datasources
{
  "api_name": "erp_mysql_prod",
  "display_name": "ERP 生产库",
  "connector_type": "mysql",
  "connector_config": {
    "host": "10.0.1.5",
    "port": 3306,
    "database": "erp_prod",
    "extra_params": "useSSL=true&serverTimezone=Asia/Shanghai"
  },
  "credential_id": "cred_erp_readonly"
}

// 响应 201
{
  "id": "a1b2c3...",
  "api_name": "erp_mysql_prod",
  "display_name": "ERP 生产库",
  "connector_type": "mysql",
  "status": "CONNECTED",
  "gravitino_catalog_name": "erp_mysql_prod",
  "created_at": "2026-06-16T10:00:00Z"
}
```

```json
// POST /datasources/erp_mysql_prod/sync-tasks
{
  "api_name": "sync_erp_orders",
  "source_config": {
    "table": "orders",
    "incremental_column": "updated_at",
    "incremental_start": "2025-01-01"
  },
  "target_dataset_api_name": "orders_raw",
  "sync_mode": "incremental",
  "transaction_type": "append",
  "schedule": {
    "interval_minutes": 15
  }
}

// 响应 201
{
  "id": "d4e5f6...",
  "api_name": "sync_erp_orders",
  "data_source_id": "a1b2c3...",
  "target_dataset_api_name": "orders_raw",
  "sync_mode": "incremental",
  "transaction_type": "append",
  "status": "DRAFT",
  "pipeline_name": "main_orders_raw",
  "created_at": "2026-06-16T10:05:00Z"
}
```

---

## 七、前端交互设计

> **详细前端组件树、交互流、复用矩阵见**:[前端数据层设计](./frontend-data-layer-design.md)

### 核心设计要点

**页面路由**:
```
/data                        DataSourceListPage      数据源列表
/data/sources/:name          DataSourceDetailPage    数据源详情(探索/同步/状态)
/data/syncs/:name            SyncTaskDetailPage      同步任务详情
/data/datasets/:name         DatasetDetailPage       Dataset 详情
```

**关键复用组件(6 个新组件)**:

| 组件 | 复用次数 | 使用场景 |
|------|:---:|------|
| **SchemaTreeBrowser** | 4 | 探索面板、Dataset Schema、ObjectType 映射、SyncTask 预览 |
| **ColumnList** | 5 | 树内展开、表格模式(所有需要展示列定义的场景) |
| **CapabilityBar** | 3 | 根据 DataSource.capabilities 渲染操作按钮 |
| **PreviewTable** | 4 | 探索预览、SyncTask 预览、Dataset 预览 |
| **SyncModeSelector** | 3 | 探索已选表、SyncTask 创建/编辑 |
| **ConfirmDialog** | 5+ | 所有删除操作,统一分级确认 |

**探索面板交互(最大变化)**:
```
ExplorerView = SchemaTreeBrowser (左侧) + SyncConfigPanel (右侧)
               └── 勾选表 → 自动填入右侧已选列表
               └── 每张已选表可调整 SyncModeSelector
               └── [一键创建同步] → 批量创建所有已选表的 SyncTask
```

### 确认机制:分级确认

```
┌────────────────────────────────────────────────────────────┐
│  数据源管理                                                 │
│                                                            │
│  左侧:数据源列表                         右侧:详情面板    │
│  ┌─────────────────────┐          ┌─────────────────────┐ │
│  │ + 添加数据源          │          │ 数据源详情            │ │
│  │                      │          │                      │ │
│  │ ▸ ERP 生产库 (MySQL)  │          │ ○ 连接状态: ✓ 正常    │ │
│  │   订单原始数据 ✓      │          │    上次检查: 10:00    │ │
│  │   客户原始数据 ✓      │          │                      │ │
│  │                      │          │ 同步任务 (2)          │ │
│  │ ▸ 日志系统 (Kafka)    │          │ ▸ sync_erp_orders    │ │
│  │   点击流 ✓            │          │   增量 | 每15分钟     │ │
│  │                      │          │   上次: 14:30, 120万行 │ │
│  └─────────────────────┘          │                      │ │
│                                    │ ▸ sync_erp_customers  │ │
│                                    │   全量 | 每日 01:00    │ │
│                                    │   上次: 01:00, 8万行   │ │
│                                    └─────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

**渐进而非一次性全暴露**:
- 默认模式:输入 连接器类型 + 主机 + 端口 + 数据库 + 凭证 → 测试连接 → 保存
- 高级模式(折叠面板):JDBC 参数、网络模式、Agent 配置(P2)

### 7.2 数据探索交互

用户点击数据源 → 选择"探索"标签:

```
┌─────────────────────────────────────────────────────────────┐
│  探索: erp_mysql_prod :: erp_prod                            │
│                                                              │
│  搜索: [________]  🔍                                         │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 表名            │ 行数估算     │ 列数  │ 增量字段      │  │
│  ├───────────────────────────────────────────────────────┤  │
│  │ orders          │ ~120 万      │ 18   │ updated_at ✓  │  │
│  │ order_items     │ ~350 万      │ 12   │ updated_at ✓  │  │
│  │ customers       │ ~8 万        │ 15   │ updated_at ✓  │  │
│  │ products        │ ~2000        │ 10   │ -            │  │
│  │ inventory_log   │ ~5000 万     │ 8    │ created_at ✓  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ▸ 展开 orders: 字段列表                                     │
│    id (BIGINT, PK) | order_no (VARCHAR) | customer_id       │
│    (BIGINT) | amount (DECIMAL) | status (VARCHAR) |          │
│    created_at (TIMESTAMP) | updated_at (TIMESTAMP)           │
│                                                              │
│  选中: [✓ orders]  →  [一键同步]  [预览数据]                 │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 一键同步 + AI 推断

用户选择表后,点击"一键同步":

```
┌─────────────────────────────────────────────────────────────┐
│  创建同步任务: orders                                          │
│                                                              │
│  🤖 AI 推荐配置(可修改)                                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ 同步模式:      [增量同步 ▾]     理由: 存在 updated_at 列  │
│  │ 事务类型:      [APPEND ▾]      理由: 仅新增无修改         │
│  │ 增量字段:      [updated_at ▾]   理由: 单调递增时间戳      │
│  │ 目标 Dataset:  [orders_raw    ]                          │
│  │ 调度周期:      [每 15 分钟 ▾]                            │
│  └───────────────────────────────────────────────────────┘  │
│                                                              │
│  ▸ 高级设置(分区策略 / 字段映射 / 并行度)                    │
│                                                              │
│  [取消]  [创建并直接启动]                                     │
└─────────────────────────────────────────────────────────────┘
```

### 7.4 ObjectType 映射 Dataset 交互

在 ObjectType 编辑表单中:

```
┌─────────────────────────────────────────────────────────────┐
│  编辑对象: 订单 (Order)                                       │
│                                                              │
│  基本属性                          数据来源                  │
│  ┌─────────────────────┐    ┌─────────────────────────────┐│
│  │ api_name: order      │    │ 选择 Dataset:               ││
│  │ display_name: 订单    │    │ [orders_raw ▾]              ││
│  │ storage_type: MANAGED│    │                             ││
│  └─────────────────────┘    │ Schema 字段      本体属性    ││
│                              │ ─────────────────────────   ││
│  属性列表                     │ order_no  ──→  订单编号      ││
│  ┌─────────────────┐        │ amount    ──→  金额          ││
│  │ ✓ 订单编号 STRING│        │ status    ──→  状态          ││
│  │   金额   DECIMAL │        │ created_at ─→  创建时间      ││
│  │   状态   STRING  │        │                             ││
│  │   创建时间 TSTAMP │       │ [自动映射]  [手动映射]       ││
│  │ + 添加属性        │       │                             ││
│  └─────────────────┘        └─────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

**组件复用映射**:

| 前端组件 | 数据源上下文 | ObjectType 上下文 | 同步任务上下文 |
|---------|-----------|-----------------|-------------|
| `DataSourceSelector` | 数据源列表 | ObjectType 编辑(选 Dataset) | SyncTask 创建(选源表) |
| `SchemaViewer` | 探索面板(浏览表结构) | ObjectType 编辑(Schema 列 → 属性映射) | Dataset 详情(Schema 面板) |
| `SyncTaskCard` | 数据源详情(同步任务列表) | - | 管线监控面板 |
| `CredentialForm` | 数据源创建/编辑 | - | - |
| `ImpactAnalysisModal` | 删除 DataSource | 删除 ObjectType | 删除 Dataset |

### 7.5 确认机制:分级确认

| 操作 | 影响级别 | 确认方式 | 影响的资产(前端展示) |
|------|---------|---------|---------------------|
| 删除空 DataSource(无 SyncTask) | LOW | 弹窗确认 | 无 |
| 删除有 SyncTask 的 DataSource | MEDIUM | 列出影响的 SyncTask | 3 个 SyncTask,2 个 Dataset |
| 删除被 ObjectType 引用的 Dataset | HIGH | 输入 Dataset 名称确认 | 2 个 ObjectType,5 个 PropertyDef |
| 删除含已部署流水线的 SyncTask | LOW | 弹窗确认(自动 stop 流水线) | 1 个 SeaTunnel job |

### 确认机制:分级确认

**实现**:`POST /impact-analysis` 返回 `ImpactAnalysis { severity, impacts[] }`,前端根据 severity 选择确认模式。

---

## 八、PG Schema

### 8.1 新增 DDL

```sql
-- ── 数据源实例 ──
CREATE TABLE data_sources (
    id                      VARCHAR(32) PRIMARY KEY,
    api_name                VARCHAR(255) NOT NULL UNIQUE,
    display_name            VARCHAR(255) NOT NULL,
    description             TEXT DEFAULT '',
    connector_type          VARCHAR(50) NOT NULL,       -- mysql | postgresql | s3 | kafka | ...
    connector_config        JSONB NOT NULL,              -- {host, port, database, ssl_params, ...}
    credential_id           VARCHAR(32) REFERENCES credentials(id) ON DELETE SET NULL,
    status                  VARCHAR(20) DEFAULT 'DISCONNECTED',
    gravitino_catalog_name  VARCHAR(255) NOT NULL DEFAULT '',  -- Gravitino 中对应的 Catalog 名
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_data_sources_connector ON data_sources(connector_type);

-- ── 凭证 ──
CREATE TABLE credentials (
    id              VARCHAR(32) PRIMARY KEY,
    api_name        VARCHAR(255) NOT NULL UNIQUE,
    credential_type VARCHAR(50) NOT NULL,       -- username_password | access_key | token
    secret_data     JSONB NOT NULL,              -- 明文 JSON(TODO SEC-001: 加密)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    -- 注意:无 updated_at,凭证只允许替换不允许修改
);

-- ── 同步任务 ──
CREATE TABLE sync_tasks (
    id                        VARCHAR(32) PRIMARY KEY,
    api_name                  VARCHAR(255) NOT NULL UNIQUE,
    data_source_id            VARCHAR(32) NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
    source_config             JSONB NOT NULL,        -- {table, query, incremental_column, file_pattern, ...}
    target_dataset_api_name   VARCHAR(255) NOT NULL,
    sync_mode                 VARCHAR(20) NOT NULL DEFAULT 'full_snapshot',  -- full_snapshot | incremental
    transaction_type          VARCHAR(20) NOT NULL DEFAULT 'snapshot',       -- snapshot | append
    schedule                  JSONB,                 -- {cron: "...", interval_minutes: ...}
    status                    VARCHAR(20) NOT NULL DEFAULT 'DRAFT',          -- DRAFT | RUNNING | STOPPED | FAILED
    pipeline_name             VARCHAR(255),
    last_run_at               TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_sync_tasks_datasource ON sync_tasks(data_source_id);
CREATE INDEX idx_sync_tasks_status ON sync_tasks(status);

-- ── Dataset 治理元数据 ──
CREATE TABLE datasets (
    id                        VARCHAR(32) PRIMARY KEY,
    api_name                  VARCHAR(255) NOT NULL UNIQUE,
    display_name              VARCHAR(255) NOT NULL DEFAULT '',
    storage_location          VARCHAR(1024) NOT NULL DEFAULT '',  -- MANAGED: Iceberg 路径; VIRTUAL: catalog.schema.table
    partition_config          JSONB,
    source_dataset_api_name   VARCHAR(255),         -- 加工来源:null=外部同步
    data_source_api_name      VARCHAR(255),         -- 外部数据源(仅 source_dataset_api_name=null 时有值)
    kind                      VARCHAR(20) NOT NULL DEFAULT 'MANAGED',  -- MANAGED | VIRTUAL
    is_view                   BOOLEAN NOT NULL DEFAULT FALSE,         -- 仅 MANAGED 子类型标记(Foundry View,未实现)
    row_count_estimate        BIGINT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_datasets_source ON datasets(source_dataset_api_name);
```

### 8.2 已有表增强 ALTER

```sql
-- PropertyDefModel 已有 physical_catalog/physical_schema/physical_table/physical_column 四列
-- 新增 dataset 引用
ALTER TABLE properties ADD COLUMN physical_dataset_api_name VARCHAR(255);
```

### 8.3 ER 关系增量

```
data_sources (1) ── (N) sync_tasks
data_sources (1) ── (0|1) credentials
sync_tasks (N) ──► target_dataset_api_name → datasets(api_name)

datasets (N) ←── properties.physical_dataset_api_name
  │
  │ (加工链)
  └── datasets.source_dataset_api_name → datasets(api_name)
```

---

## 九、MVP 范围

### 9.1 MVP 包含(P0)

| 模块 | 内容 |
|------|------|
| **连接器类型** | MySQL, PostgreSQL, S3 (MinIO/RustFS) |
| **域模型** | DataSource(含 capabilities 派生字段), Credential, SyncTask(含 sync_type/allow_schema_changes), Dataset(增强) |
| **PG 表** | data_sources, credentials, sync_tasks, datasets |
| **Gravitino** | register/remove JDBC Catalog (mysql/postgresql),**DataSource 创建时同步注册**(不解耦) |
| **Trino 探索** | list_tables, describe_table, sample_data (通过 Gravitino Connector,默认 schema="public") |
| **SeaTunnel 同步** | create_sync_pipeline (connector_type 路由) |
| **AI 辅助** | SYNC_MODE_INFERENCE_PROMPT, DATASOURCE_MAPPING_PROMPT |
| **前端组件** | SchemaTreeBrowser, ColumnList, CapabilityBar, PreviewTable, SyncModeSelector, ConfirmDialog (6 个新组件) |
| **前端页面** | DataSourceListPage (重写), DataSourceDetailPage, SyncTaskDetailPage, DatasetDetailPage |
| **确认机制** | 分级确认(LOW/MEDIUM/HIGH),调用 /api/impact-analysis |

### 9.2 MVP 不包含(后续迭代)

| 模块 | 阶段 |
|------|------|
| Oracle, SQL Server, Kafka, MongoDB 连接器 | Sprint+1 (P1) |
| Dataset → Dataset 加工流水线 UI | Sprint+1 |
| 凭证加密(SEC-001) | Sprint+2 |
| Gravitino 血缘自动记录 | Sprint+2 |
| 国产信创数据库、Snowflake/BigQuery 虚拟表(Virtual Table 联邦代理) | Sprint+3 (P2) |
| Agent 代理网络模式 | P3 |
| SaaS 系统专用连接器 | P3 |
| 工业 IoT 专有协议 | P3 |

---

## 十、遗留项

| 编号 | 问题 | 触发条件 | 解决方案 |
|------|------|---------|---------|
| **SEC-001** | `credentials.secret_data` 明文存储 | 生产环境上线前 | AES-256-GCM 应用层加密,Key 从环境变量/KMS 获取 |
| **GRAV-001** | ~~Gravitino 1.2.0 是否支持 REST API 动态创建 Catalog?~~ | ✅ 已确认支持。**关键实现细节**:属性中必须包含 `jdbc-database`(Gravitino 1.2.0 强制要求),JDBC driver jar 需预置到 Gravitino 容器 `catalogs/{provider}/libs/`。 | - |
| **PERF-001** | `explore()` 对大表 COUNT(*) 可能导致 Trino 全表扫描 | 单表 > 1000 万行 | 用系统表估算(`information_schema.TABLES.TABLE_ROWS`)替代 COUNT(*) |
| **SYNC-001** | `SyncTask.schedule` 当前为 JSONB,未与 SeaTunnel 调度引擎集成 | 需要定时自动执行 | 评估 SeaTunnel 内置调度 vs 外部 cron + API 触发 |
| **TX-001** | DataSource → Gravitino Catalog → SeaTunnel Pipeline 创建为多步非原子操作 | 中间步骤失败导致不一致 | 先创建 PG 记录 + Gravitino Catalog(立即生效),再生成 SeaTunnel Pipeline。若 SeaTunnel 注册失败,标记 SyncTask status=FAILED,支持手动重试 |
| **DEPLOY-001** | Gravitino 容器需预置 JDBC 驱动 jar 包 | 启动前 | Dockerfile 中 COPY mysql-connector-j + postgresql driver 到对应 catalogs/ 子目录。MVP 只需 MySQL + PG 驱动 |
| **SCHEMA-001** | explore() 默认 schema 应为 "public"(不是 database 名) | 已修复 | Gravitino JDBC Catalog 已指向 database,Trino 层的 schema 是 "public",不是 database 名 |

---

## 附录：与架构文档的关系

| 架构文档章节 | 本文档对应 |
|-----------|---------|
| §1.2 核心硬约束 | 全部遵守，未突破任何红线 |
| §2.3 两种对象查询路径 | 新增 DataSource 层不影响已有查询路径 |
| §3.1 领域模型 | 新增 DataSource/Credential/SyncTask，增强 Dataset/PhysicalColumnRef |
| §4.1 Catalog 层 | GravitinoRegistry 新增 JDBC/Fileset Catalog 动态管理方法 |
| §4.2 Metadata 层 | PostgresMetaStore 新增 DataSource/Credential/SyncTask/Dataset CRUD |
| §4.5 Pipeline 层 | SeaTunnelEngine 新增 `create_sync_pipeline()` — 根据 connector_type 路由 Source 模板 |
| §4.6 Engine 层 | TrinoQueryEngine 新增探索辅助方法（list_tables, describe_table, sample_data） |
| §5 业务编排层 | 新增 DataSourceService（第 6 个 Service） |
| §6 DI 容器 | config/container.py 新增 datasource_service 组装 |
| §7 数据流场景 | 新增场景 A（完整链路）、场景 B（Dataset 加工）、场景 C（虚拟表直读，Virtual Table 联邦代理） |
| §15 ADR 索引 | 需新增 ADR-007: 数据源接入架构决策 |
| §16 ICD 基线 | 需新增 ICD-06（DataSourceService）的接口签名 |

---

## 设计决策日志（Design Decision Log）

> 记录设计评审中确认的关键决策，避免后续反复讨论。

### DDL-001: DataSource 创建时必须注册 Gravitino Catalog（不解耦）

**背景**：讨论过是否让 DataSource 创建和 Gravitino Catalog 注册解耦——允许用户先创建 DataSource 做探索，需要同步时再注册 Catalog。

**决策**：**保持耦合**。DataSource 创建时同步调用 `catalog.register_jdbc_catalog()`。

**理由**：
1. Gravitino JDBC Catalog 注册后，Trino 通过 Gravitino Connector **即时自动发现**，无需重启
2. 如果解耦，探索只能用 Python 直连或 Trino 配置 Catalog 文件（需重启），用户体验差
3. Palantir 也要求 Source 配置完成后才能使用任何 Capability

### DDL-002: Capability 作为派生字段，不存 DB

**背景**：对标 Palantir 的 Capability 模型，是否需要在 PG 中存储每个 DataSource 的能力列表。

**决策**：**派生字段**。`DataSource.capabilities` 由 `connector_type` 在 API 序列化时自动推断，不存 DB。

**理由**：
1. Capability 由 connector_type 唯一确定，不存在"同一个 MySQL 有的支持 CDC 有的不支持"的情况
2. 避免 PG 数据与逻辑不同步
3. 前端 CapabilityBar 组件根据此字段决定展示哪些操作按钮

**映射表**：
| connector_type | capabilities |
|---------------|-------------|
| mysql, postgresql | explore, batch_sync, cdc |
| s3 | explore, file_sync |
| kafka | explore, streaming_sync |

### DDL-003: 探索默认 Schema 为 "public"

**背景**：创建探索 API 时，前端传 `database: ""` → 后端 fallback 到 `connector_config.database`（如 "ontology"） → Trino 报 `Schema 'ontology' does not exist`。

**决策**：**默认 schema = "public"**。Gravitino JDBC Catalog 的 `jdbc-url` 已指向数据库，Trino 层的 schema 命名空间是 PG 的 schema 概念（默认为 "public"），不是 database 名。

**修复**：`datasource_service.py` 中 `schema = database if database else "public"`。

### DDL-004: Gravitino JDBC Catalog 必须包含 jdbc-database 属性

**背景**：Gravitino 1.2.0 在初始化 JDBC Catalog 时强制要求 `jdbc-database` 属性，即使 JDBC URL 中已包含数据库名。不提供则报 `null in jdbc-database is invalid`。

**决策**：`register_jdbc_catalog()` 方法签名新增 `jdbc_database` 参数，DataSourceService 从 `connector_config.database` 提取并传递。

### DDL-005: 前端组件最大化复用（6 个核心组件）

**背景**：当前 DataConnections 页面将表单、卡片、探索面板全部内联，不可复用。

**决策**：提取 6 个无依赖基础组件（StatusBadge, SearchBar, ConfirmDialog, CapabilityBar, ColumnList, PreviewTable），再用它们组装 5 个复合组件（SchemaTreeBrowser, SyncModeSelector, SyncConfigPanel, DataSourceCard, SyncTaskCard），最后由 4 个页面使用。

**详细设计**：见 [前端数据层设计](./frontend-data-layer-design.md)。
