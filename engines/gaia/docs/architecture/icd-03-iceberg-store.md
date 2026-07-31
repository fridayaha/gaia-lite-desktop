# ICD-03: IcebergStore — 全量明细持久化层接口契约

| 字段 | 内容 |
| ---- | ---- |
| **接口版本** | v1.0 |
| **实现类** | `ontology.layers.dataset.IcebergStore` |
| **所属层** | Dataset Layer |
| **依赖组件** | Apache Iceberg 1.11.0（REST Catalog，Gravitino 内置 9001）+ RustFS/S3 + Trino 478（数据通道） |
| **关联 ADR** | ADR-007（pyiceberg 子类化 + Trino 双通道）、ADR-008（Iceberg→Doris 同步路径） |
| **关联文档** | `dataset-ontology-binding.md`、`action-sync-outbox-design.md`（MERGE INTO upsert） |

---

## 1. 职责边界

| 允许 | 禁止 |
| ---- | ---- |
| 存全量业务明细 + 历史快照 | 做在线查询（在线读走 Doris，红线 #4） |
| 时间旅行（snapshot 读取） | 做检索加速（检索走 Doris） |
| Schema 演进（`evolve_schema`） | 作为索引层 |
| `scan_latest`（sync_now 读取路径，ADR-008） | — |
| `merge`（MERGE INTO upsert/delete，Action outbox ARCHIVE effect） | — |

> 红线 #3：Iceberg 是主数据唯一写入入口（Action 操作态例外：写 PG object_state，经 outbox ARCHIVE effect 异步 MERGE INTO Iceberg）。

---

## 2. 构造与双通道（ADR-007）

```python
class IcebergStore:
    def __init__(self, engine: TrinoQueryEngine, settings) -> None: ...
```

IcebergStore 采用**双通道**（ADR-007）：

| 通道 | 实现 | 用途 |
| ---- | ---- | ---- |
| 元数据通道 | pyiceberg `GravitinoRestCatalog` 子类化（重写 `_fetch_config` + `_create_session` 绕过 Gravitino memory backend 的 s3-token 401） | schema / snapshot / namespace / drop_table |
| 数据通道 | `TrinoQueryEngine`（`iceberg.{ns}.{t}` SQL） | load_by_ids / scan / append / merge / overwrite |

> 原因：Gravitino memory backend 下 REST `/scan` 端点 HTTP 500 不可用，数据必须经 Trino 直读。元数据 JSON 可经 REST 内联返回，数据文件必须经 Trino。

---

## 3. 接口方法签名（v1.0 基线）

### 3.1 元数据通道（pyiceberg 子类化）

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `get_schema` | `(dataset: str) -> DatasetSchema` | 物理列定义 | `NotFoundError`、`IcebergUnavailableError` |
| `get_snapshots` | `(dataset: str) -> list[DatasetSnapshot]` | 快照历史 | `IcebergUnavailableError` |
| `get_latest_snapshot` | `(dataset: str) -> DatasetSnapshot \| None` | 最新快照 | `IcebergUnavailableError` |
| `evolve_schema` | `(dataset: str, additions: list[ColumnDef]) -> None` | 加列（schema 演进） | `IcebergUnavailableError` |
| `create_managed_table` | `(dataset_api_name: str, schema: ManagedTableSchema, *, properties: dict[str,str] \| None = None) -> None` | 经 Gravitino/Iceberg REST 建托管表（带 PK identifier/列 doc/required/表 properties）；已存在则走 `ensure_schema` 演进（不删表不丢 snapshot）。Catalog First 建表入口 | `IcebergUnavailableError` |
| `ensure_schema` | `(dataset_api_name: str, schema: ManagedTableSchema) -> None` | 已存在的表加缺失列（带 doc/required），保 snapshot 历史 | `IcebergUnavailableError` |
| `ensure_namespace` | `(namespace: str) -> None` | 确保 namespace 存在 | — |
| `drop_table_if_exists` | `(namespace: str, table: str) -> bool` | 删表（存在则删，返回是否删除） | — |
| `ensure_warehouse_bucket` | `() -> None` | 确保 S3 warehouse bucket 存在 | — |

> 嵌套类型（`list<...>`/`map<...>`/`struct<...>`）的 `evolve_schema` 回退到 `string`。

### 3.2 数据通道（TrinoQueryEngine）

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `load_by_ids` | `(dataset: str, ids: list[str], columns: list[str]) -> list[dict[str, Any]]` | 全量属性（按 ID 点查） | `IcebergUnavailableError` |
| `load_by_ids_as_of` | `(dataset: str, ids: list[str], columns: list[str], snapshot_id: int) -> list[dict[str, Any]]` | 历史快照点查（时间旅行） | `IcebergUnavailableError` |
| `scan_latest` | `(dataset: str, columns: list[str], limit: int = 10000) -> list[dict[str, Any]]` | 最新快照扫描（sync_now 读取路径，ADR-008） | `IcebergUnavailableError` |
| `scan_as_of` | `(dataset: str, columns: list[str], snapshot_id: int, limit: int = 100) -> list[dict[str, Any]]` | 历史快照扫描 | `IcebergUnavailableError` |
| `append` | `(dataset: str, rows: list[dict[str, Any]]) -> WriteResult` | 追加写入 | `IcebergUnavailableError` |
| `overwrite` | `(dataset: str, rows: list[dict[str, Any]]) -> WriteResult` | 覆盖写入（DELETE + INSERT） | `IcebergUnavailableError` |
| `merge` | `(dataset: str, rows: list[dict[str, Any]], pk_columns: list[str], *, delete: bool = False) -> WriteResult` | MERGE INTO upsert/delete（Action outbox ARCHIVE effect，action-sync-outbox-design.md §3.3/§8.4） | `IcebergUnavailableError` |

> ⚠️ `merge` 的 PK 是**业务主键的 backing_column**（如 `flight_id`），不是 `rid`。Trino 的普通 INSERT 对 Iceberg v2 upsert 表不会自动去重，必须用 MERGE INTO 实现"按 PK 覆盖"。

> ⚠️ `append`/`overwrite` 用 `INSERT VALUES` 拼接，**不适合大批量写入**（无生产调用方，当前仅测试覆盖）。

### 3.3 命名规范

- `dataset` 参数：业务表名（如 `ontology.flight`），`_qualified()` 自动补 namespace 前缀（`settings.iceberg_namespace`，默认 `ontology`）
- 物理资源命名走 snake_case 保词界（红线 #10）：Iceberg 表名用 `core/naming.py` 的 `_to_snake`

---

## 4. 异常契约

| 异常 | HTTP 映射 | 触发场景 |
| ---- | --------- | -------- |
| `IcebergUnavailableError(OntologyError)` | 触发降级 | Iceberg REST Catalog / RustFS / Trino 不可用 |
| `NotFoundError(OntologyError)` | 404 | 表不存在 |

### 降级策略

| 故障 | 降级行为 |
| ---- | -------- |
| IcebergStore 不可用（点查） | Trino 按 ID 查询（`SELECT ... WHERE id IN (...)`） |
| RustFS 不可用 | SeaTunnel 自动重试（指数退避，最多 10 次） |

---

## 5. 关键设计约束

1. **唯一写入入口**：主数据写入只通过 Iceberg（SeaTunnel 流水线 / `append`/`overwrite`/`merge`），Doris/Trino 只读（红线 #3）
2. **Action 操作态例外**：写 PG `object_state`（read-your-writes），经 outbox ARCHIVE effect 异步 `merge` 到 Iceberg（SyncFlushScheduler 微批，≤5min）
3. **双通道解耦**：元数据走 pyiceberg（REST），数据走 Trino（SQL），两者各自可靠互补（ADR-007）
4. **`GravitinoRestCatalog` 重写与 pyiceberg 内部耦合**：pyiceberg 升级若改 `_fetch_config`/`_create_session` 语义需回归验证
5. **namespace 补全**：dataset api_name 存储时不带 namespace，`_qualified()` 自动补前缀

---

## 6. 变更管理

- 公开方法签名变更需 bump ICD 版本号并评审
- `scan_latest`（ADR-008）和 `merge`（action-sync-outbox）为 v1.x 增量，不破坏 v1.0
- 实体文件：`docs/architecture/icd-03-iceberg-store.md`（本文件）
- 索引表：`architecture_plan.md` §十六 ICD-03、`CLAUDE.md` ICD 基线索引
