# Gaia 数据流全景图

> **版本**：v1.0 | **日期**：2026-06-17
> **基于**：architecture_overview.md, architecture_plan.md, action-architecture.md, data-layer-design.md

---

## 目录

1. [全局数据流概览](#1-全局数据流概览)
2. [流 A：数据源接入 → 本体对象](#2-流-a数据源接入--本体对象)
3. [流 B：本体对象查询（物理对象）](#3-流-b本体对象查询物理对象)
4. [流 C：虚拟对象查询](#4-流-c虚拟对象查询)
5. [流 D：时间旅行查询](#5-流-d时间旅行查询)
6. [流 E：Action 写入闭环](#6-流-eaction-写入闭环)
7. [流 F：AI 辅助建模](#7-流-fai-辅助建模)
8. [组件拓扑全景](#8-组件拓扑全景)

---

## 1. 全局数据流概览

Gaia 的数据流分为 **6 条主干流**，覆盖从外部数据接入到业务本体建模、查询、写入、AI 辅助的完整链路：

```
                              ┌──────────────────────────────┐
                              │       用户 (前端/API)         │
                              └──────┬──────────┬────────────┘
                                     │          │
                    ┌────────────────┤          ├────────────────┐
                    │                │          │                │
                    ▼                ▼          ▼                ▼
            ┌─────────────┐  ┌────────────┐  ┌───────────┐  ┌───────────┐
            │ 流 A: 接入  │  │流 B/C/D:查询│  │流 E: 写入 │  │流 F: AI   │
            │ 数据源→本体  │  │物理/虚拟/   │  │Action 闭环│  │辅助建模   │
            │            │  │时间旅行    │  │          │  │          │
            └──────┬──────┘  └─────┬──────┘  └─────┬─────┘  └─────┬─────┘
                   │               │               │               │
   ┌───────────────┼───────────────┼───────────────┼───────────────┼───────────┐
   │                               Gaia 后端 (FastAPI)                          │
   │                                                                           │
   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
   │  │DataSource│ │ObjectQuery│ │ TimeTrav │ │ Action   │ │Ontology  │        │
   │  │Service   │ │Service   │ │ Service  │ │ Service  │ │Service   │        │
   │  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘        │
   │       │            │            │            │            │               │
   │  ┌────┴────────────┴────────────┴────────────┴────────────┴────┐          │
   │  │                   6 个 Layer 实现                           │          │
   │  │  Catalog(Gravitino) │ Metadata(PG) │ Dataset(Iceberg)      │          │
   │  │  Index(Doris)       │ Pipeline(SeaTunnel) │ Engine(Trino)   │          │
   │  └────────────────────────────┬───────────────────────────────┘          │
   └───────────────────────────────┼──────────────────────────────────────────┘
                                   │
   ┌───────────────────────────────┼──────────────────────────────────────────┐
   │                        基础设施层 (9 个 Docker 服务)                       │
   │                                                                          │
   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐ │
   │  │PostgreSQL │  │Gravitino │  │ Iceberg  │  │  Doris   │  │   Trino   │ │
   │  │  :5432   │  │  :8090   │  │(RustFS)  │  │FE:9030   │  │  :8080    │ │
   │  │本体元数据  │  │物理资产注册│  │ 主数据存储 │  │ 索引加速  │  │ 联邦查询  │ │
   │  └──────────┘  └──────────┘  └──────────┘  └──────────┘  └───────────┘ │
   │                                                                          │
   │  ┌──────────┐  ┌──────────┐  ┌──────────┐                               │
   │  │SeaTunnel │  │  Kafka   │  │  RustFS  │                               │
   │  │  :5801   │  │  :9092   │  │  :9000   │                               │
   │  │数据流水线  │  │ 消息队列  │  │ S3 存储  │                               │
   │  └──────────┘  └──────────┘  └──────────┘                               │
   └──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 流 A：数据源接入 → 本体对象

**业务含义**：用户将外部数据库/文件系统中的原始数据，经过连接、探索、同步、映射四步，最终变成 Gaia 中可查询的业务对象。

```
步骤 1: 创建数据源 (DataSource)                   步骤 2: 探索 Schema
═══════════════════════════                   ═══════════════════
                                              用户选择数据源 → 选择数据库
用户填写连接信息                                     │
  {host, port, database, credential}                ▼
     │                                    DataSourceService.explore()
     ▼                                         │
DataSourceService.create_datasource()          └─ Trino:
     │                                           SHOW TABLES FROM
     ├─ PG: INSERT data_sources                      gravitino.{catalog}.{db}
     │                                           DESCRIBE {table}
     └─ Gravitino: register_jdbc_catalog()
        (POST /api/metalakes/ontology/catalogs)   → 返回表列表 + 列信息
           {                                            ┌──────────────────┐
             name: "erp_mysql",                         │ orders (120万行)  │
             type: "relational",                        │ customers (8万行) │
             provider: "jdbc-mysql",                    │ products (2000行) │
             properties: {                              └──────────────────┘
               jdbc-url, jdbc-user, ...
             }
           }
     │
     ▼
  DataSource.status = "CONNECTED"
  DataSource.gravitino_catalog_name = "erp_mysql"


步骤 3: 创建同步任务 (SyncTask)                   步骤 4: 启动同步
════════════════════════════                   ════════════════

用户选择表(如 orders) + 同步模式                       SeaTunnel 执行 MAIN Pipeline:
     │                                              ┌────────────────────────┐
     ▼                                              │ Source: MySQL JDBC     │
DataSourceService.create_sync_task()                │   ↓ CDC/binlog 读取    │
     │                                              │ Transform: (可选映射)   │
     ├─ AI 推断: 分析列结构 → 推荐配置               │   ↓                    │
     │   sync_mode: incremental                    │ Sink: Iceberg          │
     │   transaction_type: append                  │   写入 RustFS/S3       │
     │   incremental_column: updated_at            └────────────────────────┘
     │                                                        │
     ├─ Iceberg: 创建 orders_raw 表                          ▼
     │   (基于源表 Schema 推断)                      ┌──────────────────┐
     │                                              │ Iceberg orders_raw│
     ├─ SeaTunnel: 提交 job                         │  snapshot #1     │
     │   (JDBC Source → Iceberg Sink)               │  snapshot #2     │
     │                                              │  snapshot #3 ... │
     └─ PG: INSERT sync_tasks                      └────────┬─────────┘
            status = "DRAFT"                                │
                      │                                     │ 自动触发
                      ▼                                     ▼
            POST /sync-tasks/{name}/start          ┌──────────────────────┐
                                                   │ ObjectIndexFunnel    │
                                                   │ Iceberg scan→ Doris   │
                                                   └──────────┬───────────┘
                                                              │
                                                              ▼
                                                   ┌──────────────────┐
                                                   │ Doris 索引表      │
                                                   │ (主键 + 索引列    │
                                                   │  + 热点属性)     │
                                                   └──────────────────┘


步骤 5: 映射到本体对象 (ObjectType)
══════════════════════════════════

用户在 ObjectType 编辑中选择 Dataset: orders_raw
     │
     ▼
  Schema 列 ←→ 本体属性 映射:
     order_no    → 订单编号 (STRING)
     amount      → 金额 (DECIMAL)
     status      → 状态 (STRING)
     created_at  → 创建时间 (TIMESTAMP)
     │
     ▼
OntologyService.define_object_type()
     │
     ├─ PG: INSERT object_types + properties
     │      (含 physical_mapping → dataset_api_name + column_name)
     │
     ├─ Gravitino: 注册物理表路由
     │
     └─ Doris: CREATE INDEX TABLE
            (仅 indexed=true 的属性列)
```

### 数据源 → 本体的简化总览

```
外部 MySQL/PG/S3/Kafka
        │
        │ 1. 创建连接 (DataSource + Credential → PG + Gravitino JDBC Catalog)
        ▼
   Gravitino Catalog (JDBC/Fileset)
        │
        │ 2. 探索 (Trino SHOW TABLES / DESCRIBE)
        ▼
   选择表 + AI 推断同步配置
        │
        │ 3. 创建 SyncTask → SeaTunnel 执行
        ▼
   Iceberg (orders_raw)  ← 唯一写入入口
        │
        ├──→ Gravitino 注册物理表
        │
        └──→ ObjectIndexFunnel → Doris    (索引列)
                │
                │ 4. 映射属性
                ▼
   ObjectType (PG 元数据)
        │  physical_mapping.catalog_name → Iceberg catalog
        │  physical_mapping.table_name → orders_raw
        │  physical_mapping.column_name → order_no / amount / ...
        ▼
   用户可查询 "订单" 对象 (流 B)
```

---

## 3. 流 B：本体对象查询（托管对象）

**业务含义**：用户查询一个 MANAGED 类型的业务对象（如"状态为active的订单"），系统通过 Doris 索引快速过滤 → Iceberg 原生 API 获取全量属性。

```
客户端请求
  POST /objects/load
  {
    object_set: { object_type_api_name: "hr.employee", filter: {status: "active"} },
    properties: ["name", "department", "salary"],
    limit: 50
  }
        │
        ▼
ObjectQueryService.load_objects()
        │
        ├─ Step 1: 解析 object_type_api_name → ontology + type
        │         Metadata.get_object_type("hr", "employee")
        │         → storage_type = MANAGED
        │
        ├─ Step 2: 权限校验
        │         Catalog.check_access("hr.employee", "read")
        │         → allowed ✅ / 403 ForbiddenError ❌
        │
        ├─ Step 3: 根据 storage_type 路由
        │         MANAGED → _load_physical_objects()
        │
        ├─ Step 4: 索引过滤 (有 filter 时)
        │         ┌────────────────────────────────────┐
        │         │ DorisIndexStore.query()            │
        │         │   IndexQuery({                     │
        │         │     object_type_api_name: "employee"│
        │         │     filters: [{status: "active"}]  │
        │         │   })                                │
        │         │                                    │
        │         │ SELECT rid                   │
        │         │ FROM idx_hr_employee               │
        │         │ WHERE status = 'active'             │
        │         │   (利用倒排索引，毫秒级)              │
        │         │                                    │
        │         │ → IndexResult {                    │
        │         │     rids: [uuid1, uuid2, ...] │
        │         │     total: 230                     │
        │         │   }                                │
        │         └────────────────────────────────────┘
        │
        │         ⚠️ Doris 不可用 → 降级
        │         ┌────────────────────────────────────┐
        │         │ TrinoQueryEngine.query()           │
        │         │   SELECT name, department, salary  │
        │         │   FROM iceberg_catalog.hr.employee │
        │         │   WHERE status = 'active'          │
        │         │   LIMIT 50                         │
        │         │   (全表扫，带分区裁剪)               │
        │         └────────────────────────────────────┘
        │
        ├─ Step 5: 全量属性加载 (按 ID 列表)
        │         ┌────────────────────────────────────┐
        │         │ IcebergStore.load_by_ids()         │
        │         │   使用 Iceberg Expression API       │
        │         │   RowFilter: id IN (uuid1, uuid2..)│
        │         │   只读指定列: name, dept, salary    │
        │         │                                    │
        │         │ → [{name:"张三", department:"研发", │
        │         │     salary: 50000}, ...]            │
        │         └────────────────────────────────────┘
        │
        │         ⚠️ Iceberg 不可用 → 降级
        │         ┌────────────────────────────────────┐
        │         │ TrinoQueryEngine.query()           │
        │         │   SELECT name, department, salary  │
        │         │   FROM iceberg_catalog.hr.employee │
        │         │   WHERE id IN ('uuid1', 'uuid2')   │
        │         └────────────────────────────────────┘
        │
        ▼
  返回 [{name, department, salary}, ...]


物理查询的两阶段架构 (为什么快？)

  ┌──────────────────────┐          ┌──────────────────────────┐
  │ 阶段 1: 索引过滤      │          │ 阶段 2: 全量属性加载       │
  │ Doris (倒排/向量索引) │  ID列表  │ Iceberg (列存, 原生点查)   │
  │                      │ ──────→  │                          │
  │ 只存: 主键 + 索引列    │          │ 存: 全量列, 全量历史快照    │
  │ 行为: WHERE 过滤      │          │ 行为: load_by_ids(id列表) │
  │ 延迟: <5ms           │          │ 延迟: <50ms (1000行以内)  │
  └──────────────────────┘          └──────────────────────────┘

  为什么不用 Trino 一步完成？
  - Trino WHERE + SELECT 需要全表扫描（或依赖 Iceberg 元数据过滤）
  - Doris 倒排索引对少量过滤列（status, region, tag）比 Iceberg 分区裁剪更高效
  - Iceberg Expression API (RowFilter) 对 IN (id列表) 做了优化
```

### 无条件查询或无 ID 列表时

```
  filter == null && rids == null
       │
       ▼
  TrinoQueryEngine.query()
    SELECT name, dept, salary
    FROM iceberg_catalog.hr.employee
    LIMIT 50 OFFSET 0
```

---

## 4. 流 C：虚拟对象查询

**业务含义**：虚拟对象没有 Iceberg 托管表，其数据来自 Virtual Table——外部数据源表的联邦代理指针。查询时 Trino 联邦查询 Virtual Table 指向的外部表，实时拉取返回结果。全程无 Doris 参与。

> Virtual Table 由 `POST /datasources/{ds}/virtual-tables` 登记产生（`DatasetGovernance.kind=VIRTUAL`），
> `storage_location` 存三段式定位符 `catalog.schema.table`。已废弃 Gravitino SQL View 线路。
> 详见 [dataset-ontology-binding.md](./dataset-ontology-binding.md) §3.2/§3.4。

```
客户端请求
  POST /objects/load
  {
    object_set: { object_type_api_name: "hr.employee_virtual" },
    properties: ["name", "dept"],
    limit: 50
  }
        │
        ▼
ObjectQueryService.load_objects()
        │
        ├─ Step 1: Metadata.get_object_type()
        │         → storage_type = VIRTUAL
        │
        ├─ Step 2: Catalog.check_access()
        │         → allowed ✅
        │
        └─ Step 3: VIRTUAL 分流 → _load_virtual_objects()
                  │  解析 property.physical_mapping → Virtual Table 定位符
                  ▼
        ┌────────────────────────────────────────────┐
        │ TrinoQueryEngine.query()                   │
        │                                            │
        │ SELECT name, dept                          │
        │ FROM <catalog>.<schema>.<external_table>   │
        │ WHERE ...  (如有 filter)                    │
        │ LIMIT 50                                   │
        │                                            │
        │ ↑ Trino 联邦查询外部表（Virtual Table 指向）  │
        │   计算下推到外部引擎                        │
        └────────────────────────────────────────────┘
                  │
                  ▼
        返回 [{name, dept}, ...]


  虚拟对象 vs 托管对象

  ┌──────────────────┬─────────────────┬────────────────────┐
  │ 维度              │ MANAGED         │ VIRTUAL            │
  ├──────────────────┼─────────────────┼────────────────────┤
  │ 底层存储          │ Iceberg 托管表  │ Virtual Table(外部联邦代理) │
  │ 索引加速          │ Doris ✅        │ 无 ❌              │
  │ 数据来源          │ 同步到 Iceberg   │ 实时联邦查询外部表 │
  │ 适用场景          │ 高频查询,大表    │ 外部表直读,不落地  │
  │ 降级路径          │ Trino 全表扫    │ 无降级(直接失败)    │
  │ 时间旅行          │ ✅              │ 取决于 View 定义    │
  │ 写入(Action)      │ ✅              │ ❌ (只读)           │
  └──────────────────┴─────────────────┴────────────────────┘
```

---

## 5. 流 D：时间旅行查询

**业务含义**：查询某个历史时间点的对象状态，利用 Iceberg 快照机制实现"时光倒流"。

```
客户端请求
  POST /objects/load
  {
    object_set: {
      object_type_api_name: "hr.employee",
      rids: ["uuid1", "uuid2"]
    },
    properties: ["name", "salary"],
    as_of_snapshot_id: 1234567890
  }
        │
        ▼
TimeTravelService.load_objects_as_of()
        │
        ├─ Step 1: Catalog.check_access("hr.employee", "read")
        │         → allowed ✅
        │
        ├─ Step 2: Catalog.resolve_physical_table("hr.employee")
        │         → {catalog: "iceberg_catalog", schema: "hr", table: "employee"}
        │
        └─ Step 3: Trino 时间旅行查询
                  │
                  ▼
        ┌────────────────────────────────────────────┐
        │ TrinoQueryEngine.query()                   │
        │                                            │
        │ SELECT name, salary                        │
        │ FROM iceberg_catalog.hr.employee           │
        │ FOR VERSION AS OF 1234567890               │  ← Iceberg SQL 扩展
        │ WHERE id IN ('uuid1', 'uuid2')             │
        └────────────────────────────────────────────┘
                  │
                  ▼
        ┌────────────────────────────────────────────┐
        │ Iceberg 快照读取                            │
        │                                            │
        │  snapshot #1  → snapshot #2 → snapshot #3  │
        │  (t=0)          (t=1h)        (t=2h)       │
        │                              ↑             │
        │                     as_of_snapshot_id       │
        │                                            │
        │ 读取 snapshot #3 时刻的 manifest 文件         │
        │ → 定位该时刻的数据文件 (Parquet)              │
        │ → 返回历史版本数据                            │
        └────────────────────────────────────────────┘
                  │
                  ▼
        返回 [{name:"张三", salary:45000}, ...]
              (可能是旧工资，而非当前的50000)


依赖前提:
  ⚠️ 需要 Trino Gravitino Connector 透传 FOR VERSION AS OF 语法
  ⚠️ 若不支持 → 降级使用 iceberg_catalog (绕过 Gravitino)
```

---

## 6. 流 E：Action 写入闭环

**业务含义**：用户通过 Action 修改业务对象（如"发货订单"），系统在 PG 事务内原子提交变更 + 审计日志 + 副作用队列，随后异步同步到 Iceberg 和 Doris。

### 6.1 Action 同步执行路径（热路径，毫秒级）

```
客户端请求
  POST /actions/{ontology}/{object_type}/{action}
  {
    parameters: { order_id: "ord-001", new_status: "shipped" },
    idempotency_key: "req-abc-123"
  }
        │
        ▼
ActionService.execute_action()
        │
        ├─ Step 1: 解析 ActionType 定义
        │         Metadata.get_action_type(ontology, action)
        │         → parameters: [{api_name:"order_id", type:STRING, required:true}, ...]
        │         → rules: [
        │             {type:"constraint", target:"new_status",
        │              expression:"new_status in ('pending','shipped','cancelled')"},
        │             {type:"derivation", target:"updated_at",
        │              expression:"datetime.now(UTC)"}
        │           ]
        │
        ├─ Step 2: 幂等性检查 (idempotency_key)
        │         Metadata.get_execution_by_idempotency_key("req-abc-123")
        │         → null (首次执行) / → cached result (重复请求，直接返回)
        │
        ├─ Step 3: 参数校验
        │         ActionValidator.validate(param_defs, payload)
        │         → order_id 必填 ✅
        │         → new_status 类型 STRING ✅
        │         → 未知参数拒绝 ❌
        │
        ├─ Step 4: 规则引擎求值
        │         ActionRuleEngine.evaluate(rules, parameters)
        │         Phase 1 - derivation:
        │           updated_at = datetime.now(UTC) → "2026-06-17T10:30:00Z"
        │         Phase 2 - constraint:
        │           "shipped" in ('pending','shipped','cancelled') → True ✅
        │
        ├─ Step 5: 权限校验
        │         Catalog.check_access("order", "write") → allowed ✅
        │
        ├─ Step 6: 构建 mutations (含 expected_version)
        │         [
        │           {
        │             type: "UPDATE_PROPERTY",
        │             rid: "ord-001",
        │             expected_version: 5,   ← 客户端上次读取时的版本号
        │             properties: {
        │               status: "shipped",
        │               updated_at: "2026-06-17T10:30:00Z"
        │             }
        │           }
        │         ]
        │
        └─ Step 7: PostgreSQL 原子事务 (同一 session)
                  │
                  ▼
        ┌─────────────────────────────────────────────────────┐
        │          PostgreSQL Transaction                     │
        │                                                     │
        │  ┌──────────────────────────────────────────────┐  │
        │  │ object_state (行级 OCC)                       │  │
        │  │                                              │  │
        │  │ UPDATE object_state                          │  │
        │  │ SET properties = '{"status":"shipped",...}', │  │
        │  │     version = version + 1,                   │  │
        │  │     updated_at = NOW()                       │  │
        │  │ WHERE rid = 'ord-001'                  │  │
        │  │   AND version = 5  ← OCC 版本校验             │  │
        │  │ RETURNING version;                            │  │
        │  │                                              │  │
        │  │ → 成功: version = 6                          │  │
        │  │ → 冲突: affected_rows = 0 → ConflictError   │  │
        │  │         (他人已经修改了 ord-001)               │  │
        │  └──────────────────────────────────────────────┘  │
        │                                                     │
        │  ┌──────────────────────────────────────────────┐  │
        │  │ action_execution_logs (审计)                   │  │
        │  │                                              │  │
        │  │ INSERT INTO action_execution_logs            │  │
        │  │   (id, action_type_api_name, object_type..., │  │
        │  │    idempotency_key, parameters, mutations,    │  │
        │  │    status)                                    │  │
        │  │ VALUES (..., 'req-abc-123', ..., 'COMPLETED')│  │
        │  └──────────────────────────────────────────────┘  │
        │                                                     │
        │  ┌──────────────────────────────────────────────┐  │
        │  │ outbox (副作用队列)                            │  │
        │  │                                              │  │
        │  │ INSERT INTO outbox                           │  │
        │  │   (id, action_execution_id, effect_type,     │  │
        │  │    effect_config, status)                     │  │
        │  │ VALUES (..., 'WEBHOOK',                      │  │
        │  │   '{"url":"https://erp/api/notify",...}',    │  │
        │  │   'PENDING')                                  │  │
        │  └──────────────────────────────────────────────┘  │
        │                                                     │
        │  COMMIT;  ← 三者原子提交                             │
        └─────────────────────────────────────────────────────┘
                  │
                  ▼
        ActionExecutionResult {
          status: "applied",           ← 毫秒级返回，数据已生效
          action_id: "abc-def-123",
          affected_objects: {"ord-001": 6},
          mutations: [...]
        }
```

### 6.2 Action 异步流（冷路径，秒到分钟级）

> **⚠️ 架构演进（2026-07-08 去 SeaTunnel 化）**：本节原画描述的「SeaTunnel CDC（PG WAL 双路分流 → Iceberg / Kafka→Doris）」**已废弃删除**。当前真实架构为 outbox 驱动：
> - object_state → Doris（实时索引）：outbox `INDEX` effect → `OutboxExecutor` 1s 轮询 → `DorisIndexStore.upsert/delete_by_ids`（≤1s 近实时），**不经 SeaTunnel**
> - object_state → Iceberg（主数据）：outbox `ARCHIVE` effect → `SyncFlushScheduler` 5min 微批 → `IcebergStore.merge`（Trino MERGE INTO），**不经 SeaTunnel**
> - 图边投影：ActionService Step 11 RELATE→`project_link` / UNRELATE→`delete_link`（capabilities 门控）
>
> 下方原始架构图保留作为历史记录。当前架构以 [action-loop-design.md](../architecture/action-loop-design.md) §四.4 + [action-sync-outbox-design.md](./action-sync-outbox-design.md) 为准。

<details><summary>历史架构图（SeaTunnel CDC 双路分流，已被 outbox 驱动取代）</summary>

```
PG 事务 COMMIT 后，异步发生:

                    ┌──────────────────────────────────┐
                    │   PostgreSQL WAL (Write-Ahead Log)│
                    │   包含 object_state 变更记录       │
                    └──────────────┬───────────────────┘
                                   │
                    ┌──────────────┼──────────────────────────────┐
                    │    SeaTunnel CDC (统一 PG-CDC Source)        │
                    │                                              │
                    │  ┌────────────────┐  ┌────────────────────┐ │
                    │  │ PG → Iceberg   │  │ PG → Kafka         │ │
                    │  │ (主数据持久化)  │  │ (实时索引同步)       │ │
                    │  └───────┬────────┘  └─────────┬──────────┘ │
                    └──────────┼─────────────────────┼────────────┘
                               │                     │
                               ▼                     ▼
                    ┌──────────────────┐  ┌──────────────────────┐
                    │ Iceberg 新快照    │  │ Kafka                │
                    │ (分钟级微批合并)   │  │ Topic: action_order  │
                    │                  │  │    action_customer   │
                    │ 追加变更到        │  │    action_product    │
                    │ Iceberg 表        │  │ (按对象类型物理隔离)  │
                    └──────────────────┘  └──────────┬───────────┘
                                                     │
                                                     │ SeaTunnel 阶段二
                                                     │ Kafka → Doris
                                                     ▼
                                          ┌──────────────────────┐
                                          │ Doris 索引表更新       │
                                          │ (3-5 秒延迟)          │
                                          │ order 表:             │
                                          │   rid = ord-001 │
                                          │   version = 6         │
                                          │   properties = {...}  │
                                          └──────────────────────┘


                    ┌──────────────────────────────────────────┐
                    │  Outbox Executor (异步轮询 PG outbox 表)  │
                    │                                          │
                    │  1. SELECT * FROM outbox                 │
                    │     WHERE status = 'PENDING'              │
                    │     ORDER BY created_at                   │
                    │     LIMIT 100                             │
                    │                                          │
                    │  2. 执行副作用:                            │
                    │     WEBHOOK:                              │
                    │       POST https://erp/api/notify         │
                    │       Headers: X-Idempotency-Key: xxx     │
                    │       Body: {order_id, new_status}        │
                    │     → 200 OK → mark COMPLETED             │
                    │     → 500 Error → 指数退避重试             │
                    │        1st: 10s (±50% jitter)             │
                    │        2nd: 20s                           │
                    │        3rd: 40s → max_retries=3 → DLQ     │
                    │                                          │
                    │     WRITE_BACK:                            │
                    │       SeaTunnel JDBC Sink → 外部 RDBMS    │
                    │       注入 gaia_sync_tx 防反馈环路          │
                    │                                          │
                    │  3. 死信队列 (DLQ):                        │
                    │     status = 'DLQ' + 告警通知             │
                    └──────────────────────────────────────────┘
```

</details>

> **当前真实路径（outbox 驱动）**：Action 主事务原子提交 `object_state` + `execution_log` + `outbox[INDEX|ARCHIVE|WEBHOOK|WRITE_BACK|...]`。`OutboxExecutor`（1s 轮询，处理 INDEX→Doris 近实时 + WEBHOOK/WRITE_BACK）与 `SyncFlushScheduler`（5min 微批，处理 ARCHIVE→Iceberg MERGE）异步消费 outbox，不涉及 SeaTunnel。详见 [action-sync-outbox-design.md](./action-sync-outbox-design.md)。

### 6.3 Action 冲突处理

```
并发场景: 用户 A 和 B 同时修改同一订单

时间线:
  t=0     用户 A 查询订单 ord-001 → version=5, status="pending"
  t=1     用户 B 查询订单 ord-001 → version=5, status="pending"
  t=2     用户 A 提交: UPDATE WHERE version=5 → 成功, version=6
  t=3     用户 B 提交: UPDATE WHERE version=5 → affected_rows=0 → ConflictError

用户 B 收到:
  HTTP 409 Conflict
  {
    detail: "Object 'ord-001' modified by another action (expected version 5)",
    error_type: "ConflictError"
  }

前端处理:
  1. 显示 "数据已被他人修改，请刷新后重试"
  2. 自动重新加载 ord-001 (version=6, status="shipped")
  3. 用户 B 基于最新数据重新编辑 + 提交
```

### 6.4 Action 数据流简化全景

```
用户 POST /actions/.../execute
         │
         ▼
 ActionService ──── 参数校验 + 规则引擎 + 权限
         │
         ▼
   ┌─────────────────────────────────────┐
   │  PG 原子事务 (同步, 毫秒)            │
   │  ┌───────────┐ ┌──────┐ ┌───────┐  │
   │  │object_state│ │exec  │ │outbox │  │
   │  │ (OCC写入)  │ │_log  │ │(副作用)│  │
   │  └───────────┘ └──────┘ └───────┘  │
   └──────────┬──────────────────────────┘
              │ COMMIT → 返回 "applied"
              │
              ├─────────────────────────────────────┐
              │                                     │
    ┌─────────▼──────────┐              ┌───────────▼──────────┐
    │ OutboxExecutor      │              │ SyncFlushScheduler    │
    │ (1s 轮询)           │              │ (5min 微批)           │
    │  ├─ INDEX → Doris   │              │  └─ ARCHIVE → Iceberg │
    │  │   (索引, ≤1s)    │              │     (主数据 MERGE)    │
    │  ├─ Webhook → ERP   │              │                       │
    │  ├─ Write-back → DB │              │                       │
    │  └─ DLQ (失败兜底)  │              │                       │
    └────────────────────┘              └──────────────────────┘
```

> **注**：原画中此处的「SeaTunnel CDC（PG WAL → Iceberg + Kafka→Doris）」分支已于 2026-07-08 去 SeaTunnel 化删除，由 outbox INDEX/ARCHIVE effect 取代（上图 OutboxExecutor + SyncFlushScheduler）。SeaTunnel 不再出现在 Action 写入闭环中。

---

## 7. 流 F：AI 辅助建模

**业务含义**：用户用自然语言描述业务概念，AI 帮助生成 Ontology 定义草稿，用户确认后创建。

```
用户输入 "汽车制造领域，需要车型、工单、零部件三个对象"
        │
        ▼
前端 AiSuggestPanel
  ├─ 组装 system_prompt (ONTOLOGY_SUGGEST_PROMPT)
  │   规则: 输出格式 + 字段约束 + 主键/标题要求
  ├─ 组装 user_prompt (用户的自然语言描述)
  │
  └─ POST /ai/stream { system_prompt, user_prompt }
        │
        ▼
后端 (纯代理, 不感知业务)
  services/ai_assistant.py
  ├─ Agent(model, system_prompt)
  ├─ agent.run_stream(user_prompt)
  └─ SSE 流: stream_text(delta=True)
        │
        ▼
SSE 事件流 → 前端实时渲染
  data: {"type":"partial","text":"["}
  data: {"type":"partial","text":"{\"api_name\":\"vehicle\","}
  data: {"type":"partial","text":"\"display_name\":\"车型\","}
  data: {"type":"partial","text":"\"properties\":[{\"api_name\":\"model\","}
  ...逐 token 渲染...
  data: {"type":"result","text":"[{...完整JSON...}]"}
        │
        ▼
前端解析 JSON → 展示建议卡片
  车型 (vehicle)
    ├─ model (型号) STRING PK
    ├─ brand (品牌) STRING
    └─ price (价格) DECIMAL
  工单 (work_order)
    ├─ order_no (工单号) STRING PK
    ├─ status (状态) STRING
    └─ priority (优先级) STRING
  零部件 (part)
    ├─ part_no (零件号) STRING PK
    └─ name (名称) STRING
        │
        ▼
用户确认 → 批量创建
  ├─ 查重 (与已有 objectTypes 比对)
  ├─ 批量 POST /ontologies/{name}/object-types/create
  └─ 创建关系 LinkType
        │
        ▼
PG Ontology 元数据更新 → Canvas 图谱刷新


同样模式用于:
  - 属性建议 (PROPERTY_SUGGEST_PROMPT)
  - Schema 校验 (SCHEMA_VALIDATE_PROMPT)
  - 同步模式推断 (SYNC_MODE_INFERENCE_PROMPT)
  - 数据源映射 (DATASOURCE_MAPPING_PROMPT)

**关键设计原则**：
- 后端是纯代理，不知道任何业务概念
- 前端持有所有 prompt 模板
- 新增 AI 场景 = 前端加一个 prompt 模板，后端零改动

---

## 8. 组件拓扑全景

### 8.1 所有数据流在组件上的叠加

```
                         ┌─────────────────────────────────────────┐
                         │              前端 (React)               │
                         │  本体编辑 / 数据源管理 / Action 执行     │
                         │  AI 面板 / 图谱画布 / 查询界面           │
                         └────────┬──────────┬──────────┬──────────┘
                                  │          │          │
              ┌───────────────────┤          │          ├───────────────────┐
              │ 流F: POST /ai    │ 流A:     │ 流B/C/D: │ 流E: POST         │
              │     /stream     │ /data-   │ /objects │ /actions          │
              │                  │ sources  │ /load    │ /.../execute      │
              ▼                  ▼          ▼          ▼                   │
┌─────────────────────────────────────────────────────────────────────────┐
│                          FastAPI Routes                                  │
│                                                                         │
│  /ai/stream  /datasources  /objects/load  /objects/aggregate           │
│  /actions/{ontology}/{type}/{action}  /time-travel  /metrics  /health  │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        Services (业务编排层)                             │
│                                                                         │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌──────────────┐│
│  │OntologyService│ │ObjectQuery    │ │ActionService  │ │DataSource    ││
│  │               │ │Service        │ │               │ │Service       ││
│  │ 本体/属性/关系 │ │ 查询路由+降级  │ │ 写入+OCC+    │ │ 数据源+探索   ││
│  │ CRUD         │ │               │ │ Outbox       │ │ +同步编排    ││
│  └───────┬───────┘ └───────┬───────┘ └───────┬───────┘ └──────┬───────┘│
│          │                 │                 │                 │        │
│  ┌───────┴───────┐ ┌───────┴───────┐ ┌───────┴───────┐ ┌──────┴──────┐│
│  │VirtualTable   │ │TimeTravel    │ │ActionRule    │ │Outbox       ││
│  │Service        │ │Service       │ │Engine        │ │Executor     ││
│  └───────────────┘ └───────────────┘ └───────────────┘ └─────────────┘│
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ 依赖注入
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     Layer Implementations (6 层)                         │
│                                                                         │
│  Catalog(Gravitino)    Metadata(PostgreSQL)   Dataset(Iceberg)         │
│  ┌──────────────────┐ ┌───────────────────┐ ┌──────────────────────┐   │
│  │register_jdbc     │ │CRUD: Ontology,    │ │append / overwrite    │   │
│  │  _catalog        │ │ObjectType,        │ │load_by_ids          │   │
│  │register_dataset  │ │Property, Link,    │ │load_by_ids_as_of    │   │
│  │get_tbl_cols      │ │ActionType,        │ │get_snapshots        │   │
│  │check_access      │ │DataSource,        │ │evolve_schema        │   │
│  │resolve_table     │ │Credential,        │ │scan_as_of           │   │
│  │                  │ │SyncTask, Dataset, │ │                      │   │
│  │                  │ │Outbox, ExecLog,   │ │                      │   │
│  │                  │ │ObjectState        │ │                      │   │
│  └──────────────────┘ └───────────────────┘ └──────────────────────┘   │
│                                                                         │
│  Index(Doris)          Pipeline(SeaTunnel)      Engine(Trino)          │
│  ┌──────────────────┐ ┌───────────────────┐ ┌──────────────────────┐   │
│  │create_index_table│ │create_main_pipe   │ │query(sql)            │   │
│  │query (倒排/向量)  │ │create_index_sync  │ │list_tables           │   │
│  │upsert / delete   │ │create_cdc_pipe    │ │describe_table        │   │
│  │                  │ │create_kafka_doris │ │sample_data           │   │
│  │                  │ │start / stop /     │ │                      │   │
│  │                  │ │  get_status       │ │                      │   │
│  └──────────────────┘ └───────────────────┘ └──────────────────────┘   │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       9 个 Docker 服务 + 数据流                          │
│                                                                         │
│  ┌────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────┐ │
│  │ PostgreSQL │   │  Gravitino   │   │Iceberg/RustFS│   │   Doris   │ │
│  │   :5432    │   │:8090/:9001   │   │   :9000      │   │:9030/:9050│ │
│  │            │   │              │   │              │   │           │ │
│  │ 流E(写入)  │   │ 流A(注册)    │   │ 流A(同步)    │   │ 流B(索引) │ │
│  │ 流A/B/C/D  │   │ 流B/C/D(查询)│   │ 流B(点查)    │   │ 流E(索引  │ │
│  │ (元数据)   │   │ 流E(权限)    │   │ 流D(快照)    │   │  同步)    │ │
│  └──────┬─────┘   └──────┬───────┘   └──────┬───────┘   └─────┬─────┘ │
│         │                │                   │                  │       │
│  ┌──────┴─────┐   ┌──────┴───────┐   ┌──────┴───────┐          │       │
│  │ SeaTunnel  │   │    Trino     │   │    Kafka     │          │       │
│  │   :5801    │   │    :8080     │   │    :9092     │          │       │
│  │            │   │              │   │              │          │       │
│  │ 流A(主同步)│   │ 流B/C/D(查询)│   │ 外部源 CDC    │          │       │
│  │ (外部源→   │   │ 流A(探索)    │   │ (ADR-014)    │          │       │
│  │  Iceberg)  │   │              │   │              │          │       │
│  └────────────┘   └──────────────┘   └──────────────┘          │       │
│                                                                         │
│  API (FastAPI) :8000                                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 8.2 各组件在数据流中的角色汇总

| 组件 | 流A(接入) | 流B(物理查询) | 流C(虚拟查询) | 流D(时间旅行) | 流E(Action) | 流F(AI) |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| **PostgreSQL** | 存DataSource/Dataset/SyncTask元数据 | 存ObjectType定义 | 存ObjectType定义 | — | 存object_state + execution_log + outbox (原子事务) | — |
| **Gravitino** | 注册JDBC Catalog + Iceberg表 | 权限校验 + 表路由 | 存储View定义 | 表路由 | 权限校验(write) | — |
| **Iceberg (RustFS)** | 同步目标(唯一写入入口) | 全量属性点查 | — | 历史快照读取 | ARCHIVE outbox 微批 MERGE 入库(冷路径) | — |
| **Doris** | 索引同步目标 | 索引过滤(倒排/向量) | — | — | INDEX outbox 近实时 upsert(≤1s) | — |
| **Trino** | 探索查询(SHOW/DESCRIBE) | 降级查询 | View SQL执行 | 时间旅行SQL | IcebergStore.merge (MERGE INTO) + maintenance | — |
| **SeaTunnel** | MAIN pipeline (外部源→Iceberg 搬运) + FILE_SYNC/KAFKA_*/EXTERNAL_CDC | — | — | — | —（object_state 同步已去 SeaTunnel 化，改 outbox） | — |
| **Kafka** | 外部数据源 CDC/落地 (ADR-014) | — | — | — | —（object_state 同步不再走 Kafka） | — |
| **DeepSeek/LLM** | AI推断同步配置 | — | — | — | — | 本体建模建议 |

### 8.3 依赖方向（严格单向）

```
Frontend (React)
    │
    ▼
Routes (FastAPI) ──→ Services ──→ Layers ──→ Core Models
                          │
                          ├── OntologyService ──→ Metadata + Catalog + Index
                          ├── ObjectQueryService ──→ Metadata + Catalog + Index + Dataset + Engine
                          ├── ActionService ──→ Metadata + Catalog + Dataset
                          ├── DataSourceService ──→ Metadata + Catalog + Engine + Pipeline + Dataset
                          ├── TimeTravelService ──→ Catalog + Engine
                          ├── (VirtualTableService 已删除) ──→ —
                          ├── ActionRuleEngine ──→ (纯计算)
                          ├── ActionValidator ──→ (纯计算)
                          ├── OutboxExecutor ──→ Metadata
                          └── ConflictDetector ──→ Dataset
```

### 8.4 降级策略在各流中的触发

```
流 A (数据接入):
  Gravitino不可用 → 无法创建/探索DataSource，返回明确错误
  Trino不可用 → 探索不可用，同步任务不受影响(SeaTunnel直连)
  SeaTunnel不可用 → SyncTask status=FAILED，支持手动重试
  Iceberg不可用 → SeaTunnel自动重试(指数退避, 最多10次)
  RustFS不可用 → SeaTunnel自动重试(指数退避, 最多10次)
  外部数据源不可用 → SyncTask status=ERROR, 按周期自动重试

流 B (物理查询):
  Doris不可用 → Trino直接扫描Iceberg(带分区裁剪)
  Iceberg不可用 → Trino按ID查询
  Gravitino不可用(物理表) → 绕过权限(缓存表路由)

流 C (虚拟查询):
  Gravitino不可用 → 直接失败(无降级路径)
  Trino不可用 → 直接失败

流 D (时间旅行):
  Gravitino Connector不透传语法 → 改用iceberg_catalog直连

流 E (Action):
  PostgreSQL不可用 → 写入直接失败(无降级路径)
  Outbox Webhook失败 → 指数退避重试 → DLQ
  Write-back失败 → 指数退避重试 → DLQ
  CDC管道延迟 → Iceberg/Doris数据滞后(最终一致)
```

---

## 附录：关键文件索引

| 文件 | 涉及的数据流 |
|------|:---:|
| `src/ontology/services/datasource_service.py` | 流A |
| `src/ontology/services/ontology_service.py` | 流A (步骤5) |
| `src/ontology/services/object_query_service.py` | 流B, 流C |
| `src/ontology/services/time_travel_service.py` | 流D |
| ~~`src/ontology/services/virtual_table_service.py`~~ | ~~流C~~ 已删除（虚拟对象查询改由 ObjectQueryService 按 storage_type=VIRTUAL 走 Trino 联邦） |
| `src/ontology/services/action_service.py` | 流E |
| `src/ontology/services/action_rule_engine.py` | 流E |
| `src/ontology/services/action_validator.py` | 流E |
| `src/ontology/services/outbox_executor.py` | 流E (异步) |
| `src/ontology/services/conflict_detector.py` | 流E (审计) |
| `src/ontology/services/write_back_manager.py` | 流E (异步) |
| `src/ontology/services/ai_assistant.py` | 流F |
| `src/ontology/layers/catalog/gravitino_registry.py` | 流A/B/C/D/E |
| `src/ontology/layers/metadata/postgres_meta_store.py` | 流A/B/C/E |
| `src/ontology/layers/dataset/iceberg_store.py` | 流A/B/E |
| `src/ontology/layers/index/doris_index_store.py` | 流A/B/E |
| `src/ontology/layers/engine/trino_query_engine.py` | 流A/B/C/D |
| `src/ontology/layers/pipeline/sea_tunnel_engine.py` | 流A/E |
| `src/ontology/routes/ai.py` | 流F |
| `src/ontology/routes/action/__init__.py` | 流E |
| `docs/architecture_overview.md` | 全流 |
| `docs/architecture_plan.md` | 全流 |
| `docs/action-architecture.md` | 流E (详细) |
| `docs/data-layer-design.md` | 流A (详细) |
| `docs/ai-integration-guide.md` | 流F (详细) |

---

*文档版本：v1.0 | 生成日期：2026-06-17 | 基于 Gaia 项目代码分析 + docs/ 目录全部文档*