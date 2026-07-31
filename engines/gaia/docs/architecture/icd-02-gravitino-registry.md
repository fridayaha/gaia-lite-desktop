# ICD-02: GravitinoRegistry — 物理资产注册中心接口契约

| 字段 | 内容 |
| ---- | ---- |
| **接口版本** | v1.0 |
| **实现类** | `ontology.layers.catalog.GravitinoRegistry` |
| **所属层** | Catalog Layer |
| **依赖组件** | Apache Gravitino 1.3.0（主服务 8090 + 内置 Iceberg REST Catalog 9001） |
| **关联 ADR** | —（Catalog 层定位见 architecture_plan.md §4.1） |
| **关联文档** | `dataset-ontology-binding.md`（Virtual Table 定义）、ADR-014（多源 catalog 注册） |

---

## 1. 职责边界

| 允许 | 禁止 |
| ---- | ---- |
| 注册物理资产（Iceberg 表、JDBC/Fileset/Lakehouse/Kafka catalog） | 存业务本体元数据（业务元数据在 PG） |
| Virtual Table 联邦代理登记与列定义拉取 | 参与数据计算 |
| RBAC 权限校验（`check_access`） | 做主数据存储 |
| 表路由解析（`resolve_backing_table`） | — |

> 红线 #1：Gravitino 仅管理物理数据资产，不存业务本体元数据。

> ⚠️ `create_view` / `get_view` 已删除（Gravitino SQL View 线路废弃，见 dataset-ontology-binding.md §3.4）。Virtual Table = 外部数据源表的联邦代理指针（不落地），由 `POST /datasources/{ds}/virtual-tables` 登记产生。

---

## 2. 构造

```python
class GravitinoRegistry:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None: ...
```

- 依赖注入：由 `config/container.py` 注入 `httpx.AsyncClient`
- 通过 httpx 调用 Gravitino REST API（8090）+ Iceberg REST API（9001）

---

## 3. 接口方法签名（v1.0 基线）

### 3.1 物理表注册

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `register_dataset` | `(schema: str, name: str, location: str, columns: list[dict[str, object]], catalog: str = "") -> None` | None | `GravitinoUnavailableError` |

- 通过 Iceberg REST API（9001）注册物理表，注册后立即可被 Trino 查询
- `catalog` 参数为 API 一致性保留，实际由 REST bridge 指向单一 Iceberg REST endpoint

### 3.2 Virtual Table / 联邦列定义

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `is_view` | `(catalog: str, schema: str, name: str) -> bool` | 是否为虚拟表 | `GravitinoUnavailableError` |
| `get_table_columns` | `(catalog: str, schema: str, table: str) -> list[dict]` | 列定义（联邦拉列，VIRTUAL schema 用） | `GravitinoUnavailableError` |

> `get_table_columns` 用于 Virtual Table 的列定义拉取（Trino 联邦查询前需知道列结构）。

### 3.3 权限校验

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `check_access` | `(object_type_api_name: str, operation: Literal["read", "write"]) -> bool` | 是否允许 | `GravitinoUnavailableError` |

- 基于 Gravitino RBAC
- 当前阶段仅支持对象类型级权限（read/write），属性级权限和 visibility 过滤留待后续迭代

### 3.4 表路由解析

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `resolve_backing_table` | `(object_type_api_name: str) -> dict[str, str]` | `{catalog, schema, table}` | `NotFoundError`、`GravitinoUnavailableError` |

- 解析 ObjectType → 物理表定位符（catalog.schema.table）
- MANAGED 类型：返回 Iceberg 物理表
- VIRTUAL 类型：返回 Virtual Table 指向的外部表

### 3.5 多源 Catalog 管理（ADR-014 扩展）

| 方法 | 签名 | 返回 | 说明 |
| ---- | ---- | ---- | ---- |
| `register_jdbc_catalog` | `(...) -> None` | None | 注册 JDBC catalog（MySQL/PG/openGauss/Kingbase/OceanBase/StarRocks...） |
| `register_lakehouse_catalog` | `(...) -> None` | None | 注册 Lakehouse catalog（Iceberg/Hudi） |
| `register_kafka_catalog` | `(...) -> None` | None | 注册 Kafka catalog |
| `register_fileset_catalog` | `(...) -> None` | None | 注册 Fileset catalog（S3/local） |
| `remove_catalog` | `(catalog_name: str, force: bool = True) -> None` | None | 移除 catalog |
| `list_catalogs` | `() -> list[dict[str, Any]]` | catalog 列表 | — |

> 多源 catalog 管理是 ADR-014（多源异构数据融合连接器体系）的扩展能力，连接器从 2 种扩展到 25 种。

---

## 4. 异常契约

| 异常 | HTTP 映射 | 触发场景 |
| ---- | --------- | -------- |
| `GravitinoUnavailableError(OntologyError)` | 触发降级 / 503 | Gravitino 不可用 |
| `NotFoundError(OntologyError)` | 404 | 物理表/catalog 不存在 |

### 降级策略

| 故障 | 降级行为 |
| ---- | -------- |
| Gravitino 不可用（物理表查询） | 绕过权限（缓存表路由），但 RBAC 失效 |
| Gravitino 不可用（Virtual Table 查询） | **直接失败（无降级路径）** |

---

## 5. 关键设计约束

1. **Gravitino 1.3.0 内置 Iceberg REST Catalog**（端口 9001，端点前缀 `/iceberg`），非独立 tabulario/iceberg-rest 服务，无 8181 端口
2. **REST Catalog 后端为 jdbc**（元数据持久化到 PG `iceberg_tables` 表），非 memory backend
3. **`?warehouse=` 查询参数会 404**（Gravitino 已知缺陷），验证时不带 warehouse 参数
4. **国产库 JDBC 驱动用独立类名**（`com.huawei.opengauss.jdbc.Driver` 等），避免与官方 `org.postgresql.Driver` 同名冲突（ADR-014 D4，错误模式 #10）
5. **Virtual Table 不落地**：仅联邦代理指针，查询走 Trino，不进 Doris

---

## 6. 变更管理

- 公开方法签名变更需 bump ICD 版本号并评审
- ADR-014 扩展的 `register_*_catalog` 方法为 v1.x 增量，不破坏 v1.0
- 实体文件：`docs/architecture/icd-02-gravitino-registry.md`（本文件）
- 索引表：`architecture_plan.md` §十六 ICD-02、`CLAUDE.md` ICD 基线索引
