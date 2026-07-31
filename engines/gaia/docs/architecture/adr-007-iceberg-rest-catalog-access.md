# ADR-007：Iceberg REST Catalog 访问通道（pyiceberg 子类化 + Trino 双通道）

| 字段     | 内容 |
| -------- | ---- |
| **状态** | 已采纳 |
| **审批日期** | 2026-06-18 |
| **影响层** | `layers/dataset/IcebergStore` |
| **相关 ICD** | ICD-03 IcebergStore |

---

## 背景

`IcebergStore` 原本通过 pyiceberg 的 `load_catalog("rest", ...)` 访问 Gravitino 内嵌的
Iceberg REST Catalog（端口 9001），用于读写 `ontology.*` 命名空间下的所有 Iceberg 表。

在 Gravitino 1.2.0 的 **memory backend + `credential-providers=s3-token`** 配置下，
pyiceberg 的标准用法**三道关卡全部失败**，导致数据集详情页的「物理列定义」「Iceberg
快照历史」长期空白（实际数据已由 SeaTunnel 正确写入，Trino 也能查询）：

| 关卡 | pyiceberg 默认行为 | Gravitino 响应 | 根因 |
| ---- | ------------------ | -------------- | ---- |
| ① 初始化 | `RestCatalog.__init__` 强制 `GET /v1/config?warehouse=...` | **401** "The provided credentials did not support" | 带 `warehouse` 查询参数 + S3 静态凭证触发 s3-token 校验路径 |
| ② 请求头 | session 默认带 `X-Iceberg-Access-Delegation: vended-credentials` | **400** | Gravitino memory backend 不支持凭证代理 |
| ③ 鉴权 | `AuthManager` 默认尝试 OAuth2，注入 `Authorization: Bearer ...` | **401** | 任何 Bearer token 都被拒 |

这三关都无法通过 pyiceberg 的配置项关闭（`__init__` 硬编码调 `_fetch_config`，
access-delegation header 和 auth manager 是 `_create_session` 的默认行为）。

此外，**Iceberg REST 的数据扫描端点 `/v1/.../tables/{t}/scan` 在 memory backend 下同样
不可用**（HTTP 500 "No in-memory file found for location: s3://..."）——manifest/metadata
文件并未真正持久化到 S3，REST server 无法在 scan planning 阶段读取它们。pyiceberg 的
`Table.scan()` 走 FileIO 直读数据文件，也会撞到同一问题。

### 现状验证（2026-06-18）

| 通道 | 操作 | 结果 |
| ---- | ---- | ---- |
| 裸 curl `GET /v1/namespaces/ontology/tables/object_types_raw` | 元数据读取 | ✅ HTTP 200，返回完整 metadata JSON（schema + snapshots）|
| 裸 curl `POST /v1/.../tables/object_types_raw/scan` | 数据扫描 | ❌ HTTP 500 "No in-memory file found" |
| Trino `SELECT * FROM iceberg.ontology.object_types_raw` | 数据查询 | ✅ 23 行 |
| Trino `... FOR VERSION AS OF <snapshot_id>` | 时间旅行 | ✅ 23 行 |
| Trino `iceberg.information_schema.columns` | 列定义 | ✅ 12 列 |
| Trino `"table$snapshots"` 系统表 | 快照 | ❌ 同样读不到 metadata.json |

**关键结论**：Gravitino memory backend 下，**元数据 JSON 可经 REST 内联返回（不依赖
S3 文件读取），数据文件必须经 Trino 直读**。两条通道各自可靠，互补。

---

## 决策

`IcebergStore` 采用**双通道**，各走最可靠的路径：

### 1. 元数据通道：pyiceberg `RestCatalog` 子类化

不裸用 httpx（会重新发明轮子：重试、异常映射、类型化 schema/snapshot 模型、namespace/table
CRUD、commit 协议，pyiceberg 全已有）。改为**子类化 `RestCatalog` 重写两个钩子**，绕开
上述三关，其余 100% 复用库能力：

```python
class GravitinoRestCatalog(RestCatalog):
    def _fetch_config(self):           # 关卡①：跳过 GET /v1/config
        self._supported_endpoints = set(DEFAULT_ENDPOINTS)
        self._namespace_separator = DEFAULT_NAMESPACE_SEPARATOR
    def _create_session(self):         # 关卡②③：去掉 access-delegation header + auth
        session = super()._create_session()
        session.headers.pop("X-Iceberg-Access-Delegation", None)
        session.auth = None
        return session
```

覆盖方法：`get_schema` / `get_snapshots` / `get_latest_snapshot` / `ensure_namespace` /
`drop_table_if_exists` / `evolve_schema`。

### 2. 数据通道：Trino Iceberg Connector

数据读写通过 `TrinoQueryEngine` 执行 `iceberg.{ns}.{t}` 上的 SQL：

- 点查：`SELECT {cols} FROM iceberg.{ns}.{t} WHERE id IN (...)`
- 时间旅行：`... FOR VERSION AS OF <snapshot_id>`
- 写入：`INSERT INTO ... VALUES ...`；overwrite = `DELETE` + `INSERT`

覆盖方法：`load_by_ids` / `load_by_ids_as_of` / `scan_as_of` / `append` / `overwrite`。

### 3. 配套调整

- **namespace 补全**：dataset api_name 存储时不带 namespace（如 `object_types_raw`），
  新增 `settings.iceberg_namespace`（默认 `"ontology"`），`IcebergStore._qualified()` 在
  标识符不含 `.` 时自动补前缀。
- **异常语义修正**：`DataSourceService.get_dataset_schema`/`get_dataset_snapshots` 不再
  静默吞所有异常；仅对 `NotFoundError`（表真不存在）返回空，其它故障抛
  `IcebergUnavailableError`，让前端能区分「没数据」与「元数据服务故障」。
- **依赖瘦身**：移除 `pyarrow`、`aioboto3`（仅服务 pyiceberg 的数据 IO 路径，已不用）。
  保留 `pyiceberg`（用于 catalog/schema/snapshot）。

---

## 后果

### 正面

- **彻底移除 pyiceberg 的 401 死结**，数据集详情页物理列/快照恢复正常显示（端到端验证
  通过：`object_types_raw` 返回 12 列 + 1 个 append 快照）。
- **复用成熟库**：不维护手写 REST 客户端，schema/snapshot 类型映射、重试、异常处理由
  pyiceberg 负责。
- **数据通道走 Trino**：与架构原则「Trino 作为主要查询引擎」一致；生产查询路径本就有
  Trino fallback（`ObjectQueryService`），本决策等于把 fallback 扶正为正路，更诚实。
- **异常语义清晰**：前端不再被误导性的「暂无数据」迷惑。
- **依赖更少**：去掉 pyarrow/aioboto3 两个重量级依赖。

### 负面

- `IcebergStore` 对数据操作**依赖 `TrinoQueryEngine`**（Engine 层）。这在分层隔离上是
  Dataset 层向 Engine 层的反向依赖。缓解方式：通过构造注入（`IcebergStore(engine=...)`），
  由 `Container`/`Service` 组装，`IcebergStore` 不在内部 `import` TrinoQueryEngine（仅
  类型注解，`TYPE_CHECKING` 下），符合「跨层协调由 Service 层编排」的精神。
- `append`/`overwrite` 用 `INSERT VALUES` 拼接，**不适合大批量写入**（无生产调用方，
  当前仅测试覆盖；未来若需要批量写，应改用 Trino 的 `INSERT ... SELECT` 从 staging 表
  或 SeaTunnel 落数）。
- `evolve_schema` 对嵌套类型（`list<...>`/`map<...>`/`struct<...>`）回退到 `string`，
  嵌套类型演化不在当前范围。
- `GravitinoRestCatalog` 的两处重写是**与 pyiceberg 内部实现耦合**的适配代码，未来
  pyiceberg 升级若改动 `_fetch_config`/`_create_session` 的语义，需回归验证。

---

## 替代方案

| 方案 | 未选择的原因 |
| ---- | ------------ |
| **裸 httpx 直连 REST `/v1/...`** | 重新发明 `RestCatalog` 已有能力（重试、异常映射、类型化模型、commit 协议），维护成本高。仅作为 `GravitinoRestCatalog` 不可用时的应急手段。 |
| **Service 层降级编排**（IcebergStore 失败 → Service 调 Trino 兜底） | 治标不治本：元数据通道仍走不通的 pyiceberg，只是把失败兜底到 Service 层，增加编排复杂度；且数据集页空白问题的根因（pyiceberg 401）未消除。 |
| **全部走 Iceberg REST（含 scan/commit）** | REST `/scan` 在 memory backend 下 HTTP 500 不可用，数据读取线上会一直失败，仍需靠现有 Trino fallback 兜底——等于「代码统一了但实际不通」。 |
| **换掉 Gravitino memory backend**（改 jdbc/sql backend，metadata 持久化到 PG） | 根本解，但涉及部署变更、数据迁移、回归验证，工作量大，超出「修正 Iceberg 访问方式」范围。列为后续独立议题。 |
| **升级 Gravitino 到 1.3.0+** | 见 `docs/bugfix/gravitino-1.3.0-upgrade.md`：1.3.0 的价值在 `type-converter.custom.mapping`（解决 jsonb 查询），与 s3-token 凭证问题无直接关联；不能依赖升级解决本问题。 |

---

## 验证记录（2026-06-18）

| 验证项 | 结果 |
| ------ | ---- |
| `GravitinoRestCatalog.load_table` | ✅ 返回 12 列 schema + current snapshot |
| `RestCatalog.update_schema().add_column().commit()` | ✅ 列数 12→13→12（已回滚） |
| `IcebergStore.get_schema/get_snapshots/get_latest_snapshot`（端到端）| ✅ 返回正确 |
| `GET /api/datasets/object_types_raw/schema` | ✅ 返回 12 列物理列定义 |
| `GET /api/datasets/object_types_raw/snapshots` | ✅ 返回 1 个 append 快照（含 summary）|
| 单元测试 | ✅ 485 passed（含 21 个 IcebergStore 测试）|
| ruff / mypy | ✅ 全绿 |

---

## 回退条件

若未来出现以下任一情况，需重新评估本决策：

1. pyiceberg 升级后 `_fetch_config`/`_create_session` 的内部契约改变，`GravitinoRestCatalog`
   重写失效且无法平滑适配。
2. Gravitino 修复 memory backend 的 s3-token 凭证问题（`/v1/config` 不再 401），届时可
   回退为标准 `load_catalog("rest", ...)`，移除 `GravitinoRestCatalog` 子类。
3. Gravitino memory backend 被替换为持久化 backend，REST `/scan` 端点可用，届时可评估
   数据通道也回归 REST（移除对 Trino 的依赖）。
