# ICD-04: DorisIndexStore — 在线读主源接口契约

| 字段 | 内容 |
| ---- | ---- |
| **接口版本** | v1.0 |
| **实现类** | `ontology.layers.index.DorisIndexStore` |
| **所属层** | Index Layer |
| **依赖组件** | Apache Doris 4.0.5（FE 9030 MySQL 协议 / BE 9050），`aiomysql` 连接池 |
| **关联 ADR** | ADR-001（Doris 作为在线读主源，存全量属性）、ADR-008（Iceberg→Doris 同步路径）、ADR-012（IVF ANN 语义表） |
| **关联文档** | `index-acceleration-design.md`、`textql-design.md`（向量召回） |

---

## 1. 职责边界

| 允许 | 禁止 |
| ---- | ---- |
| 在线读主源：存全量结构化属性 + 倒排/向量索引 | 作为写入入口（写入仍经 Iceberg→IndexSync） |
| 点查/参数化查询直出（`load_by_ids`/`execute_sql`） | 存大字段/二进制（以序列化引用形式存储） |
| IVF ANN 语义检索（`vector_search`） | 做历史快照（仅当前版本） |
| 语义表管理（`create_semantic_table`/`build_semantic_index`/`upsert_semantic_rows`） | 做主数据存储（主数据在 Iceberg） |

> 红线 #4：Doris 作为在线读主源，存全量结构化属性（ADR-001 修订）；大字段/二进制以序列化引用形式存储。Iceberg/Trino 退为历史快照/批量分析/容灾路径。

> 红线 #8：Doris 索引表名必须带本体前缀 `idx_{ontology}__{type}`（snake_case），避免跨本体数据互盖/误删。

---

## 2. 构造与连接池

```python
class DorisIndexStore:
    def __init__(self, connection: Any | None = None) -> None: ...
```

- 模块级 `aiomysql.create_pool`（lazy-init，lifespan 关闭）
- **连接池是性能关键**：持久连接池 qps=552，每次新建连接 qps 仅 35（ADR-001 POC 数据）
- `close_pool()`：lifespan 关闭连接池

### 表名生成

```python
def _table_name(self, ontology_api_name: str, object_type_api_name: str) -> str:
    # → idx_{ontology}__{type}（snake_case，红线 #8）
```

由 `core/naming.doris_index_table` 生成，保词界。

---

## 3. 接口方法签名（v1.0 基线）

### 3.1 索引表生命周期

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `create_index_table` | `(ontology_api_name: str, object_type_api_name: str, fields: list[dict[str, Any]], partition_by: list[str] \| None = None) -> None` | None | `IndexProvisionError` |
| `drop_index_table` | `(ontology_api_name: str, object_type_api_name: str) -> None` | None | — |
| `table_exists` | `(ontology_api_name: str, object_type_api_name: str) -> bool` | 表是否存在 | — |

- `fields` 含索引列（PRIMARY_KEY/INVERTED/RANGE/VECTOR）+ STORED_ONLY 列（全量属性）
- 类型映射走 `_DORIS_TYPE_MAP`

### 3.2 数据写入（经 IndexSync，非直写）

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `upsert` | `(ontology_api_name: str, object_type_api_name: str, records: list[dict[str, Any]]) -> None` | None（INSERT 幂等，Unique Key 模型） | `DorisUnavailableError` |
| `delete_by_ids` | `(ontology_api_name: str, object_type_api_name: str, ids: list[str]) -> None` | None | `DorisUnavailableError` |

> 写入入口（两条，均不走 SeaTunnel）：① 外部数据接入路径——`ObjectIndexFunnel` 从 Iceberg `scan_latest` 读全量数据 → `DorisIndexStore.upsert`（统一负责 rid 分配/复用 + 四引擎扇出，已取代旧的 SeaTunnel INDEX backfill pipeline，见 ADR-008 修订记录 + graph-reasoning-design §6）；② Action 业务写入路径——outbox `INDEX` effect → `OutboxExecutor` 1s 轮询 → `DorisIndexStore.upsert/delete_by_ids`（近实时 ≤1s）。`IndexSyncService` 现仅负责 Doris 索引表 DDL（provision/rebuild/deprovision 建表删表），不再管数据同步。**Doris 不承接外部直写**。

### 3.3 在线查询（主路径）

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `load_by_ids` | `(ontology_api_name, object_type_api_name, ids, columns) -> list[dict[str, Any]]` | 全量属性直出（ADR-001 修订） | `DorisUnavailableError` |
| `execute_sql` | `(ontology_api_name, object_type_api_name, sql, params=None) -> list[dict[str, Any]]` | 参数化物理 SQL 执行（TextQL 编译器产物，`?` 占位符） | `DorisUnavailableError` |

> Doris 读路径仅此两条，均已参数化（`load_by_ids` 走 `cursor.execute(sql, ids)`，`execute_sql` 走 `cursor.execute(sql, params)`）。2026-07-13 删除了非参数化的遗留方法 `query`/`load_by_filter`/`aggregate`（及其 `IndexFilter`/`IndexQuery`/`IndexResult` schema + `_build_filter_clause` 字面量拼接）——它们无生产调用方，且违反「值用参数化查询绑定」红线。聚合查询统一走 `ObjectQueryService.aggregate_by_request` → TextQL 编译器 → `execute_sql`。

### 3.4 IVF ANN 语义表（ADR-012 扩展）

| 方法 | 签名 | 返回 | 说明 |
| ---- | ---- | ---- | ---- |
| `semantic_table_exists` | `() -> bool` | 语义表是否存在 | — |
| `create_semantic_table` | `(dim: int = 384) -> None` | 创建 IVF ANN 语义表 | — |
| `build_semantic_index` | `(dim: int = 384) -> None` | 构建向量索引 | — |
| `upsert_semantic_rows` | `(rows: list[dict[str, Any]]) -> None` | 写入语义向量行 | — |
| `vector_search` | `(...) -> list[dict]` | IVF ANN 向量检索（TextQL 召回兜底） | — |

---

## 4. 异常契约

| 异常 | HTTP 映射 | 触发场景 |
| ---- | --------- | -------- |
| `DorisUnavailableError(OntologyError)` | 触发降级 | Doris FE/BE 不可用 |
| `IndexNotBuiltError(OntologyError)` | 500 | 索引表未建（provision 未完成） |
| `IndexProvisionError(OntologyError)` | 500 | 索引建表失败 |

### 降级策略

| 故障 | 降级行为 |
| ---- | -------- |
| Doris 不可用 | Trino 直接扫描 Iceberg（点查 load_by_ids / 过滤 scan / 聚合，带分区裁剪；TextQL 编译器按目标重编译） |
| 索引同步延迟 | 告警，自动降级为 Trino 全表扫 |

> 降级是 ADR-001 的核心设计：Doris 故障时 Trino-Iceberg 兜底，保证可用性。

---

## 5. 关键设计约束

1. **表名带本体前缀**：`idx_{ontology}__{type}`（红线 #8），ObjectType api_name 仅在本体内唯一
2. **全量属性存储**：ADR-001 修订后，Doris 存全量结构化属性（STORED_ONLY 列），非仅索引列
3. **连接池必须持久化**：模块级 `aiomysql.create_pool`，禁止每次新建连接（性能差 15 倍）
4. **物理命名 snake_case**：表名走 `core/naming.doris_index_table`（红线 #10）
5. **写入不经 Doris 直写**：经 IndexSync（Iceberg→Doris）或 outbox INDEX effect，Doris 是读主源
6. **VECTOR 类型**：`ARRAY<FLOAT>`，IVF ANN 索引（Doris 4.x 向量索引）
7. **BE 内存**：1g→3g（ADR-012 ANN 调优）

---

## 6. 变更管理

- 公开方法签名变更需 bump ICD 版本号并评审
- ADR-001 修订（全量属性存储）和 ADR-012 扩展（语义表）为 v1.x 增量
- **v1.1（2026-07-13）**：删除遗留非参数化查询方法 `query`/`load_by_filter`/`aggregate` + `IndexFilter`/`IndexQuery`/`IndexResult` schema + `_build_filter_clause` 字面量拼接。原因：无生产调用方（生产读统一走 `execute_sql` 参数化路径），且违反「值用参数化查询绑定」红线。属收紧未用契约，无下游依赖，非破坏性
- 实体文件：`docs/architecture/icd-04-doris-index-store.md`（本文件）
- 索引表：`architecture_plan.md` §十六 ICD-04、`CLAUDE.md` ICD 基线索引
