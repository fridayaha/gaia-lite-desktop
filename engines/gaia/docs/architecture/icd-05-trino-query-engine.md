# ICD-05: TrinoQueryEngine — 联邦查询引擎接口契约

| 字段 | 内容 |
| ---- | ---- |
| **接口版本** | v1.0 |
| **实现类** | `ontology.layers.engine.TrinoQueryEngine` |
| **所属层** | Engine Layer |
| **依赖组件** | Apache Trino 478（通过 Gravitino Connector 联邦查询），`trino-python-client` |
| **关联 ADR** | ADR-007（IcebergStore 数据通道经 Trino）、ADR-012（TextQL SqlGlot 编译器，方言感知） |
| **关联文档** | `architecture_plan.md` §4.6、`docs/engineer/verification-guide.md` |

---

## 1. 职责边界

| 允许 | 禁止 |
| ---- | ---- |
| 联邦查询（通过 Gravitino Connector 路由所有 catalog） | 做主数据存储 |
| 全量数据加载（Iceberg 扫描） | 做在线读主源（在线读走 Doris，红线 #4） |
| Virtual Table 执行（外部表联邦，无 Doris 降级） | 做索引加速 |
| 时间旅行（`FOR VERSION AS OF`） | 做元数据管理 |
| 探索辅助（`list_tables`/`describe_table`/`sample_data`） | 做查询路由（路由在 Service 层） |

> 红线 #5：Trino 是主要联邦查询引擎，通过 Gravitino Connector 联邦查询；Virtual Table 查询必须走 Trino，**无 Doris 降级路径**。

---

## 2. 构造

```python
class TrinoQueryEngine:
    def __init__(self, connection: Connection | None = None) -> None: ...
```

- 依赖注入：由 `config/container.py` 注入 `trino-python-client` 的 `Connection`
- 底层通过 Gravitino Connector 自动路由到所有注册的 catalog（Iceberg / JDBC / Kafka / ...）

---

## 3. 接口方法签名（v1.0 基线）

### 3.1 核心查询

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `query` | `(sql: str, params: list[Any] \| None = None) -> list[dict[str, Any]]` | 行列表（每行为 column→value 字典） | `TrinoUnavailableError` |

- 统一 SQL 入口，底层通过 Gravitino Connector 自动路由
- `params`：可选位置参数（参数化查询绑定）
- **方言感知**：TextQL 编译器（ADR-012）按目标重编译 SQL——全 MANAGED→Doris 主/Trino 降级；含 VIRTUAL→Trino 跨 catalog 联邦 JOIN（`iceberg.ontology.*` / `gravitino_jdbc.*`）

### 3.2 探索辅助（数据源接入用）

| 方法 | 签名 | 返回 | 异常 |
| ---- | ---- | ---- | ---- |
| `list_tables` | `(catalog: str, schema: str = "") -> list[str]` | 表名列表 | `TrinoUnavailableError` |
| `describe_table` | `(catalog: str, schema: str, table: str) -> list[dict[str, Any]]` | 列定义 | `TrinoUnavailableError` |
| `sample_data` | `(catalog: str, schema: str, table: str, limit: int = 10) -> list[dict[str, Any]]` | 样本数据 | `TrinoUnavailableError` |
| `test_connection` | `(catalog: str) -> bool` | 连接是否可用 | — |

### 3.3 Catalog 管理（Trino 侧）

| 方法 | 签名 | 返回 | 说明 |
| ---- | ---- | ---- | ---- |
| `register_catalog_in_trino` | `(...) -> None` | None | 在 Trino 动态注册 catalog（配合 Gravitino `register_*_catalog`） |
| `remove_catalog_in_trino` | `(catalog_name: str) -> None` | None | 移除 Trino catalog |

---

## 4. 异常契约

| 异常 | HTTP 映射 | 触发场景 |
| ---- | --------- | -------- |
| `TrinoUnavailableError(OntologyError)` | 503 | Trino Coordinator/Worker 不可用 |

### 降级策略

| 故障 | 降级行为 |
| ---- | -------- |
| Trino 不可用（MANAGED 查询） | Doris 全量直出（正常路径本就是 Doris 主，Trino 是降级路径） |
| Trino 不可用（VIRTUAL 查询） | **直接失败（无降级路径）** |
| Trino 不可用（时间旅行） | **直接失败**（时间旅行依赖 Iceberg 快照，必须经 Trino `FOR VERSION AS OF`） |

> 注意：Trino 在本架构中既是 Virtual Table 的**唯一执行路径**，也是 Doris 不可用时的**容灾降级路径**。前者无降级，后者是降级目标本身。

---

## 5. 关键设计约束

1. **Gravitino Connector 联邦**：Trino 通过 Gravitino Connector 自动路由所有 catalog，无需在 Trino 侧逐个配 catalog（动态注册由 `register_catalog_in_trino` 完成）
2. **FOR VERSION AS OF 透传**：时间旅行语法经 Gravitino Connector 透传到 Iceberg（Sprint 0 P0 验证通过）
3. **方言感知**：TextQL 编译器（ADR-012）按目标重编译 SQL，MANAGED 用 Doris 方言，VIRTUAL/降级用 Trino 方言
4. **跨 catalog 联邦 JOIN**：含 VIRTUAL 的查询走 Trino 跨 catalog JOIN（`iceberg.ontology.*` JOIN `gravitino_jdbc.*`），不再拒绝 MIXED_STORAGE（ADR-012 修订）
5. **同步客户端**：当前用 `trino-python-client` on-client dbapi（同步），通过 `asyncio.to_thread` 包装为 async；虚拟表查询并发 > 50 QPS 且 P95 > 500ms 时考虑换 `trino.async_client`（演进触发条件）
6. **Trino ↔ Gravitino Connector 插件版本绑定**：见 CLAUDE.md「组件升级指南」，强绑定 Trino 版本段 + Gravitino 版本

---

## 6. 变更管理

- 公开方法签名变更需 bump ICD 版本号并评审
- `register_catalog_in_trino`/`remove_catalog_in_trino`（多源融合）为 v1.x 增量
- 实体文件：`docs/architecture/icd-05-trino-query-engine.md`（本文件）
- 索引表：`architecture_plan.md` §十六 ICD-05、`CLAUDE.md` ICD 基线索引
