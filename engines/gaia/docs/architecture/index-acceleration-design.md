# Doris 在线读主源设计 (Online Read Primary Source)

> **用途**：本体对象在线读主源层的完整设计参考，覆盖意图、架构、组件契约、数据流、失败语义、可观测性与测试标准。供后续设计与开发对齐。
>
> **历史**：2026-06-25 ADR-001 修订前，本文档描述「纯索引加速层」；修订后 Doris 升级为在线读主源（存全量结构化属性），本文档已同步更新。
>
> **关联文档**：[architecture_plan.md](./architecture_plan.md) · [action-architecture.md](./action-architecture.md) · [data-flow-diagrams.md](../design/data-flow-diagrams.md) · [implementation-status.md](./implementation-status.md) · [dataset-ontology-binding.md](../design/dataset-ontology-binding.md)
>
> **状态**：核心链路已接通（2026-06-18），实时同步待联调。详见文末"实现状态"。

---

## 一、设计意图

### 1.1 要解决的问题

本体对象查询的典型模式是"按属性过滤对象"（如"状态为 active 的订单"）。若直接在 Iceberg 全表扫描，延迟随数据量增长不可控，无法满足在线查询的 P95 < 200ms SLO。

### 1.2 解法：存储-索引分离

对标 Palantir Foundry OSv2 的 **Storage-Index Separation** 架构（见 `action-architecture.md` §Decoupled Search Index）：把"过滤"和"取全量"拆成两段，由不同存储引擎承担。

```
查询请求
  │
  ├─ 阶段 1: 索引过滤 (Doris)        ── 倒排/向量/范围索引，毫秒级
  │     输入: filter (status=active)
  │     输出: rid 列表
  │
  └─ 阶段 2: 全量加载 (Iceberg)      ── 列存点查，按 ID 批量取
        输入: rid 列表
        输出: 全量属性行
```

### 1.3 为什么是 Doris

| 候选 | 否决理由 |
|------|---------|
| Elasticsearch | 引入额外组件，与"Iceberg 唯一写入入口"原则冲突，需独立同步链路 |
| Trino 全表扫 | 延迟随数据量线性增长，P95 不可控 |
| Redis | 违反"无 Redis"原则（原则 7），缓存语义与索引语义混淆 |
| **Doris 4.x** ✅ | 自带倒排+向量+范围索引，MySQL 协议易接，可作为只读索引层不参与写入 |

---

## 二、架构定位

### 2.1 五层中的 Index 层

```
┌─────────────────────────────────────────────────────────┐
│  Service 编排层                                          │
│  OntologyService · ObjectQueryService · IndexSyncService│
├──────────────┬──────────────┬──────────────┬────────────┤
│  Catalog     │  Metadata    │  Dataset     │  Index     │
│  Gravitino   │  PostgreSQL  │  Iceberg     │  Doris     │
│  物理资产注册 │  业务元数据   │  全量明细     │  索引加速   │
├──────────────┴──────────────┴──────────────┴────────────┤
│  Pipeline (SeaTunnel) · Engine (Trino)                  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 职责边界

| 维度 | Doris (Index) | Iceberg (Dataset) | Trino (Engine) |
|------|--------------|-------------------|----------------|
| **存储内容** | 主键 + 索引列 + 热点属性 | 全量业务明细 + 历史快照 | 无存储，纯计算 |
| **读流量** | 索引过滤，返回 ID 列表 | 按 ID 批量返回全量属性 | 联邦查询 / 降级兜底 |
| **索引类型** | 主键 + 倒排(INVERTED) + 向量(VECTOR) + 范围(RANGE) | 无 | 无 |
| **写入入口** | ❌ 只读消费（由 SeaTunnel 同步） | ✅ 唯一写入入口 | ❌ |
| **参与对象** | 仅 MANAGED 类型 | MANAGED 类型 | MANAGED + VIRTUAL |

### 2.3 七条架构原则中的相关项

| 原则 | 对索引层的约束 |
|------|--------------|
| **4. Doris 作为在线读主源** | 存全量结构化属性（含索引列）；大字段/二进制类型以序列化引用形式存储。点查/过滤直出 Doris，Iceberg/Trino 退为历史快照/批量分析/容灾路径（ADR-001 修订） |
| **7. 移除 Redis** | 用 Doris 自带缓存 + Iceberg ACID + 分区策略替代 |
| **层间隔离** | Index 层不直接调 Dataset 层；跨层编排统一在 `IndexSyncService` |
| **Iceberg 唯一写入入口** | Doris 只读消费 SeaTunnel 同步的数据，不接受业务写入 |

---

## 三、组件契约

### 3.1 Schema 层 (`core/schemas/index.py`)

```python
class IndexField(BaseModel):
    name: str                                    # 物理列名（physical_mapping.column_name，非 api_name）
    index_type: Literal["PRIMARY_KEY", "INVERTED", "VECTOR", "RANGE"]
    vector_config: dict[str, Any] | None = None

class IndexTable(BaseModel):
    object_type_api_name: str
    source_dataset: str
    fields: list[IndexField]
    partition_by: list[str]

class IndexFilter(BaseModel):
    field: str
    op: Literal["eq", "neq", "in", "range", "contains"]
    value / values / min / max                   # 按 op 取用

class IndexQuery(BaseModel):
    object_type_api_name: str
    filters: list[IndexFilter]
    vector_search: dict | None                   # 与 full_text_search 互斥，vector 优先
    full_text_search: dict | None
    limit: int = 100
    offset: int = 0

class IndexResult(BaseModel):
    rids: list[str]                        # 仅返回 ID，全量属性由 Iceberg 提供
    total: int
```

### 3.2 Index 层 (`layers/index/doris_index_store.py`)

| 方法 | 职责 | 失败语义 |
|------|------|---------|
| `create_index_table(api_name, fields, partition_by)` | DDL：建 `idx_<api_name>` 表，按 field.index_type 建倒排/向量/范围索引 | `DorisUnavailableError` |
| `drop_index_table(api_name)` | DDL：删表（幂等） | `DorisUnavailableError` |
| `table_exists(api_name)` | 探测表是否存在（`information_schema.tables`） | 表不存在返回 `False`；Doris 不可达抛 `DorisUnavailableError` |
| `upsert(api_name, records)` | DML：`INSERT ... ON DUPLICATE KEY UPDATE` 批量写入 | `DorisUnavailableError` |
| `delete_by_ids(api_name, ids)` | DML：按主键删（参数化 `%s`） | `DorisUnavailableError` |
| `query(IndexQuery)` | 查询：按 filters 过滤，返回 `IndexResult{rids, total}` | `DorisUnavailableError` |

**安全**：标识符经 `_validate_identifier`（`[A-Za-z_][A-Za-z0-9_]*` 白名单）后插值；值经 `_escape_val` 转义（含反斜杠）；delete 走参数化。

**命名约定**：Doris 表名 `idx_<object_type_api_name>`；主键列名 `<object_type_api_name>_id`。

### 3.3 编排层 (`services/index_sync_service.py`)

> **⚠️ 实现订正（2026-07 T1.10）**：本表为设计阶段原画。实际实现中 `IndexSyncService` **只负责 Doris 索引表 DDL**（provision/rebuild/deprovision 建/删表），不再调用 `SeaTunnelEngine`，不再有 `create_index_sync_pipeline` / `update_sync_pipeline` / `backfill`。数据同步职责已剥离：外部接入数据由 `ObjectIndexFunnel`（从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`，统一 rid 分配/复用 + 四引擎扇出），Action 业务写入由 outbox INDEX effect → `OutboxExecutor`。详见 [graph-reasoning-design.md](./graph-reasoning-design.md) §6 + [handoff-rid-funnel-closure.md](./handoff-rid-funnel-closure.md)。

| 方法 | 触发时机 | 行为 | 失败语义 |
|------|---------|------|---------|
| `provision(api_name, properties)` | ObjectType **创建** | drop→create_index_table(真实字段)（仅 DDL，不启动任何 pipeline） | `IndexProvisionError`（非致命，caller catch） |
| `rebuild(api_name, properties)` | ObjectType **更新**（property 变更） | drop→create_index_table（仅 DDL；数据由 ObjectIndexFunnel/OutboxExecutor 重填） | `IndexProvisionError` |
| `deprovision(api_name)` | ObjectType **删除** | drop_index_table（best-effort，不阻塞删除） | 失败仅 log |
| `backfill(api_name, records)` | 一次性回填 | upsert 真实行（用于同步未追上时或冒烟测试） | `IndexProvisionError` |

**幂等性**：provision/rebuild 都先 drop 再 create，处理重试和残留表。

### 3.4 字段提取器 (`services/index_field_extractor.py`)

从 ObjectType properties 推导 `IndexField[]`，是"哪些列进 Doris、怎么索引"的唯一真相源。

**映射规则**：
| property 特征 | index_type |
|--------------|-----------|
| `is_primary_key=True` | `PRIMARY_KEY`（无需 indexed 标记） |
| `indexed=True` + STRING | `INVERTED`（倒排，eq/in/contains 高效） |
| `indexed=True` + 数值/时间 (INTEGER/DECIMAL/DATE/TIMESTAMP...) | `RANGE`（范围，>/</between 高效） |
| `indexed=True` + VECTOR/GEOPOINT | `VECTOR`（向量，ANN） |
| 无 `physical_mapping` | 跳过（逻辑属性无 Iceberg 列可镜像） |
| `indexed=True` + 红线类型 (STRUCT/ARRAY/ATTACHMENT/MEDIA_REFERENCE/GEOSHAPE) | **拒绝**（原则 4），记 skipped |

**列名取 `physical_mapping.column_name`**，非 property api_name——二者可能不同（属性映射到不同名源列时）。

**容错**：单个 property 配置异常不阻断整体，记入 `skipped` 返回。

### 3.5 查询路由 (`services/object_query_service.py` `_load_physical`)

```
有 filter 且无显式 IDs:
  1. table_exists?  否 → info "not_built" → Trino scan
  2. table_exists 抛 DorisUnavailableError → warning "doris_down" → Trino scan
  3. query(IndexQuery) → 命中 → Iceberg load_by_ids
                        → 抛 DorisUnavailableError → warning "doris_down" → Trino scan
有显式 IDs: 直接 Iceberg load_by_ids
无 filter 无 IDs: Trino scan (no_filter)
Iceberg 失败: Trino by-ID fallback
```

### 3.6 Pipeline 层 (`layers/pipeline/sea_tunnel_engine.py`)

> **⚠️ 实现订正（2026-07 T1.10）**：下表原画的方法 `create_index_pipeline` / `update_index_pipeline` / `stop_index_pipelines` **均已删除**（SeaTunnel 不再参与 Iceberg→Doris 同步）。Doris 写入统一收口到 `ObjectIndexFunnel`（外部接入路径，Python 侧 `DorisIndexStore.upsert`）。`SeaTunnelEngine` 现仅保留 `create_sync_pipeline`（MAIN，外部源→Iceberg）+ `create_file_sync_pipeline` / `create_kafka_ingestion_pipeline` / `create_kafka_timeseries_pipeline` / `create_external_cdc_pipeline`。

> **2026-07-08 去 SeaTunnel 化**：原 `create_pg_to_kafka_pipeline()` / `create_kafka_to_doris_pipeline()`（object_state→Kafka→Doris 实时索引，阶段 8）已删除，改 outbox INDEX effect → OutboxExecutor ≤1s → DorisIndexStore.upsert。object_state 的 Doris 同步不再走 SeaTunnel。详见 [action-sync-outbox-design.md](../design/action-sync-outbox-design.md)。

---

## 四、数据流

### 4.1 建模期：创建 ObjectType（触发索引表创建）

```
用户在前端勾选 property 的 searchable(=indexed)
  → POST /object-types/create  { properties: [{searchable: true, physical_mapping: {...}}] }
  → OntologyService.define_object_type_batch
      ├─ PG 事务：写 ObjectType + PropertyDef(indexed=prop.searchable)  ✅
      ├─ Gravitino 注册 Iceberg 表                                       ✅
      └─ IndexSyncService.provision(api_name, properties)               ✅
            ├─ IndexFieldExtractor.extract → IndexField[]（真实字段）
            └─ DorisIndexStore.drop + create_index_table(idx_<api>, fields)  # 仅 DDL，不启动 pipeline
```

> **数据同步订正**：建模期只建 Doris 表 DDL。数据写入由 `ObjectIndexFunnel`（外部接入路径，从 Iceberg `scan_latest` 读 → `DorisIndexStore.upsert`）或 `OutboxExecutor`（Action 写入路径，outbox INDEX effect）负责，均不走 SeaTunnel。

### 4.2 同步期：Iceberg → Doris（外部接入路径）

```
外部源数据经 SeaTunnel MAIN pipeline 写入 Iceberg
  → ObjectIndexFunnel 从 Iceberg scan_latest 读全量行
  → rid 分配/复用（按业务 PK 查 Doris idx 表）→ DorisIndexStore.upsert（仅索引列 + rid）
  → 可选：按 capabilities 门控扇出 Neo4j / PostGIS 投影
  → Doris 索引表可被 query 命中
```

> Action 业务写入路径独立：mutation → PG 事务(object_state + outbox[INDEX]) → OutboxExecutor 1s 轮询 → DorisIndexStore.upsert/delete_by_ids（≤1s 近实时），同样不经 SeaTunnel。

### 4.3 查询期：物理对象查询（两段式）

```
GET /query/objects  { filter: {status: active} }
  → ObjectQueryService._load_physical
      ├─ table_exists(api)?  是
      │   └─ DorisIndexStore.query(IndexQuery{filters:[status=active]})
      │       → IndexResult{rids: [O-001, O-002]}
      └─ IcebergStore.load_by_ids(table, [O-001,O-002], properties)
          → [{order_id:O-001, status:active, amount:120, ...}, ...]
```

### 4.4 降级流（Doris 不可用 / 索引未建）

```
ObjectQueryService
  ├─ table_exists = False      → info  "not_built"   → Trino scan (带分区裁剪)
  ├─ Doris 不可达              → warning "doris_down" → Trino scan
  └─ Trino scan: SELECT props FROM iceberg.table WHERE <filter翻译> LIMIT/OFFSET
```

VIRTUAL 类型全程无 Doris，直接 Trino 联邦查 Virtual Table。

---

## 五、失败语义与可观测性

### 5.1 失败处理原则

**索引 provisioning 永不阻断 ObjectType CRUD**：`OntologyService._provision_index` catch `IndexProvisionError`，仅 log warning，ObjectType 仍在 PG 创建成功。查询自动降级 Trino，直到重试成功。

### 5.2 异常层级 (`core/exceptions.py`)

| 异常 | 含义 | 触发降级 |
|------|------|---------|
| `DorisUnavailableError` | Doris 集群不可达 | warning + `doris_down` 指标 |
| `IndexNotBuiltError` | 索引表未建（语义保留，当前用 `table_exists` bool 判定） | info + `not_built` 指标 |
| `IndexProvisionError` | provisioning 失败（DDL/pipeline） | 不阻断 CRUD，log warning |

### 5.3 Prometheus 指标 (`observability/metrics.py`)

| 指标 | 标签 | 用途 |
|------|------|------|
| `ontology_object_query_index_hit_total` | `object_type` | 走 Doris 索引路径的查询数 |
| `ontology_object_query_fallback_total` | `object_type`, `reason` | 降级到 Trino 的查询数；reason ∈ `not_built`/`doris_down`/`no_filter`/`empty_result` |

**运维判读**：`not_built` 占比高 → ObjectType 未 provision 或 property 未标 indexed；`doris_down` 持续 > 0 → Doris 集群故障，P1 告警。

### 5.4 SLO (`engineering_principles_and_best_practices.md`)

| 指标 | 目标 | 告警阈值 |
|------|------|---------|
| 物理对象查询 P95 | < 200ms | — |
| 索引同步延迟 | Action 写入 ≤1s（outbox）；外部接入 backfill 触发后秒级 | Action 路径 >1s / 外部接入长时间未同步告警 |
| 物理对象查询 QPS | > 200 | — |
| Doris FE 健康 | ok | `doris_fe_health != ok` → P1 |

---

## 六、前端契约

前端**只涉及属性级 `indexed` 的修改与呈现**，不感知索引运行态（查询路径对用户透明，符合设计）。

### 6.1 字段命名链路

前端用 `searchable`，后端 ORM/返回态用 `indexed`，转换在 service 层：

```
前端复选框 p.searchable
  → CreateObjectWizard 提交 { searchable: bool }
  → 后端 PropertyInput.searchable
  → OntologyService: indexed = prop.searchable   ← 转换点
  → ORM PropertyDef.indexed
```

### 6.2 修改入口

- `CreateObjectWizard.tsx` Step 1 源列映射表的"可搜索"复选框（title="倒排索引核心开关"）
- `OntologyWorkspace.tsx` 创建/编辑提交时透传 `searchable`

### 6.3 呈现入口

- `ObjectDetailPanel.tsx`：`{p.indexed && <span title="已建索引">🔍</span>}`

### 6.4 前端不涉及

索引同步状态、查询路径、Doris 健康、降级原因——均无 UI（设计上对用户透明）。

---

## 七、测试标准

### 7.1 测试矩阵

| 层级 | 文件 | 覆盖 |
|------|------|------|
| 单测 | `tests/unit/layers/test_doris_index_store.py` | DDL/DML/query 各 op + 连接错误（mock aiomysql） |
| 单测 | `tests/unit/services/test_index_field_extractor.py` | datatype→index_type 映射、红线拒绝、无 physical_mapping 跳过、PK 总入索引 |
| 单测 | `tests/unit/services/test_index_sync_service.py` | provision/rebuild/deprovision/backfill 编排 + 失败包装 |
| 单测 | `tests/unit/services/test_object_query_index_path.py` | 降级区分（not_built/doris_down/no_filter）+ 指标计数 |
| 集成 | `tests/integration/test_index_acceleration.py` | **端到端"有数据"验证**：extract→provision→backfill→query 返回真实 ID |

### 7.2 集成验证要点（"确认有数据"）

`test_index_acceleration.py` 用 `FakeDorisConnection`（内存执行 Doris SQL 方言）验证：
1. provision 建表 SQL 含真实索引列（非 `fields=[]`）
2. backfill upsert 真实行，`ON DUPLICATE KEY UPDATE` 幂等
3. query 按 filter 过滤返回**真实 rids**（如 status=active 返回 O-001/O-002，排除 O-003）
4. table_exists 反映 provisioning 状态
5. deprovision 清理表 + 停 pipeline

### 7.3 待真实环境验证

- SeaTunnel `create_index_sync_pipeline` 的 HOCON 模板真实可用（当前用 stub）
- 端到端：define ObjectType → 写 Iceberg → 等 INDEX_SYNC → query 命中 Doris（需带 SeaTunnel+Doris 的环境）

---

## 八、实现状态（2026-06-18，含真实环境验证）

### 已通过真实 Doris 容器验证（`scripts/verify_index_live.py`）

9 项检查全过，证明索引加速层在**真实 Apache Doris 4.0.5** 上可用：
1. IndexFieldExtractor 推导真实字段（PK + INVERTED + RANGE）
2. table_exists 建表前返回 False
3. provision 真实 DDL 建表成功
4. table_exists 建表后返回 True
5. backfill upsert 3 行真实数据
6. query `status=active` 返回真实 `['O-001','O-002']`
7. query `region=APAC` 返回真实 `['O-001','O-003']`
8. upsert 幂等（UNIQUE KEY 模型自动 merge）
9. deprovision 真实删表

### 真实环境验证中发现的 bug（均已修复）

| Bug | 根因 | 修复 |
|-----|------|------|
| 建表报 `replication_num should be less than available backends` | Doris 默认 replication_num=3，单 BE 环境不满足 | `create_index_table` 加 `PROPERTIES("replication_num"=settings.doris_replication_num)`，默认 1，生产可配 |
| upsert 报 `mismatched input 'ON'` | Doris 不支持 MySQL 的 `ON DUPLICATE KEY UPDATE` 语法 | 改用 `UNIQUE KEY` 表模型，普通 `INSERT` 自动 upsert merge |
| 建表报 `should not contain random distribution desc` | Doris 4.x 要求 Unique/Duplicate 表显式分桶 | 加 `DISTRIBUTED BY HASH(pk) BUCKETS 1` |
| query 报 `Unknown column '<api>_id'` | `query()` 硬编码 `<api_name>_id` 作 PK 列名，但建表用 property 的 physical_column | `IndexQuery` 加 `pk_column` 字段，由 `ObjectQueryService` 从 `object_type.primary_key` 填入 |
| 建表报 `no viable alternative at input '(\n\n)'` | property 未标 is_primary_key 且无 physical_mapping 时 fields 为空，生成空表 SQL | `extract` 接受 `primary_key` 参数，匹配 api_name 的 property 视为 PK |

### API 端到端验证

通过重建的 `ontology-api` 容器真实调 `POST /object-types/create`：
- ✅ Doris 真实建表 `idx_ticket`（含 `ticket_id` PK 列）
- ✅ 表真实可写入可查询（直接连 Doris upsert + query 验证）
- 🟡 SeaTunnel INDEX_SYNC pipeline 提交返回 400（见下）

### 组件状态

| 组件 | 状态 | 说明 |
|------|------|------|
| `IndexField` schema | ✅ | 完整，新增 `pk_column` 字段 |
| `DorisIndexStore` | ✅ | create/drop/table_exists/upsert/delete/query，真实 Doris 验证通过 |
| `IndexFieldExtractor` | ✅ | 含红线校验 + primary_key 匹配 |
| `IndexSyncService` | ✅ | provision/rebuild/deprovision（仅 Doris 表 DDL；数据同步已剥离给 ObjectIndexFunnel/OutboxExecutor） |
| `OntologyService` 接线 | ✅ | define/update/delete 触发，真实 API 验证通过 |
| `ObjectQueryService` 降级区分 | ✅ | not_built/doris_down + 指标 |
| `container` 注入 | ✅ | `index_sync_service` property |
| 真实 Doris 集成验证 | ✅ | `scripts/verify_index_live.py` 9 项全过 |
| API 端到端 | ✅ | Doris 建表 + 写入 + 查询 |
| ~~SeaTunnel INDEX_SYNC 真实联调~~ | ⚫ 已废弃 | 2026-07 T1.10 删除 `create_index_pipeline`/backfill/stream 模板。Doris 写入统一收口到 `ObjectIndexFunnel`（Python 侧 `DorisIndexStore.upsert`），不再走 SeaTunnel |
| 实时同步（阶段 8） | ✅ | 2026-07-08 改 outbox INDEX effect → OutboxExecutor ≤1s → Doris upsert（去 SeaTunnel 化） |
| vector/full_text 查询 | 🔴 | schema 就绪，`query()` 实现待补（P2） |

### ~~SeaTunnel INDEX_SYNC 联调~~（已废弃，历史记录）

> **2026-07 T1.10 起 SeaTunnel 不再参与 Iceberg→Doris 同步**。以下为历史记录，保留以备溯源。当前 Doris 写入走 `ObjectIndexFunnel`（外部接入）+ `OutboxExecutor`（Action 写入），均不经 SeaTunnel。

历史问题：真实提交返回 400 Bad Request，定位为两个配置问题：
1. **Doris sink `fenodes` 端口错误**：模板用 `doris_port`（9030，MySQL 协议），但 SeaTunnel Doris sink 的 `fenodes` 需 FE **HTTP 端口 8030**
2. **HOCON 格式**：V1 端点需 `?format=hocon`

均已修复（fenodes 改 8030 + submit-job 加 `?format=hocon`），backfill(BATCH)/stream(STREAMING) 双模板曾 live 验证通过（2026-07-06）。后路径 A 重构去常驻 stream 只留 backfill，最终于 T1.10 整体删除（改 ObjectIndexFunnel 统一 rid 分配 + 四引擎扇出）。

---

## 九、演进路线

| 优先级 | 工作项 | 依赖 |
|--------|--------|------|
| ✅ | SeaTunnel INDEX_SYNC 真实联调 | fenodes 8030 + `?format=hocon` 修复 (2026-07-06) |
| ✅ | 实时索引同步（≤1s） | outbox INDEX effect → OutboxExecutor (2026-07-08 去 SeaTunnel 化) |
| P2 | `query()` 实现 vector_search / full_text_search 分支 | 独立 |
| P2 | `ObjectDetailPanel` 🔍 反映真实建表状态（后端返回 `index_built`） | 独立 |
| P2 | `OperationsDashboard` 索引同步延迟卡片 | 指标已就绪 |
| 后续 | IndexSyncScheduler lifespan 接线（路径 A 周期 backfill，外部接入数据一致性兑底） | 独立 |

---

## 十、关键决策记录

| 决策 | 理由 |
|------|------|
| 用 `table_exists` bool 而非 `IndexNotBuiltError` 异常判降级 | 减少 try/except 嵌套；异常保留给真正的故障（Doris 不可达） |
| provisioning 失败不回滚 PG | 索引是性能优化非正确性依赖；Doris 故障不应阻断建模 |
| 索引列名取 `physical_mapping.column_name` 而非 api_name | 属性可映射到不同名源列，Doris 需与 Iceberg 列名一致 |
| 红线类型强制拒绝入 Doris | 原则 4 不可妥协，即使用户误标 indexed |
| `searchable`(API) vs `indexed`(ORM) 命名分裂 | 历史遗留，service 层转换；前端契约用 `searchable` 不变 |
