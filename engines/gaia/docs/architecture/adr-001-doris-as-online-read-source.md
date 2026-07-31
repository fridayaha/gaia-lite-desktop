# ADR-001: Doris 作为在线读主源（存全量属性）

**状态**: 已采纳（2026-06-25 修订）
**决策者**: 架构组
**关联**: `docs/bugfix/eval-doris-full-data-replace-dataset-lookup.md`（POC 数据）、`docs/bugfix/db-connection-leak-and-point-lookup-perf.md`

---

## 背景

原 ADR-001（初始决策）将 Doris 定位为「纯索引加速层」：只存主键 + 索引列 + 热点属性，查询时 Doris 过滤出 ID → 回 Iceberg 取全量属性。该决策基于一个隐含假设：**Iceberg/Trino 点查足够快**。

2026-06-25 benchmark read-path 排查发现该假设不成立：

| 路径 | @10 p95 | @10 qps |
|------|---------|---------|
| Trino 点查 Iceberg（现状） | ~1900ms | 1.8 |
| Trino 单次点查 | 3-5s | — |

Iceberg 表无主键索引/分区，点查退化为全表扫描（booking 50 万行）。这是 Iceberg+Trino 点查的固有缺陷，靠分区只能缓解、无法根治。

## 决策

**Doris 升级为在线读主源**，存全量结构化属性（单表 `idx_{ont}__{type}` 扩列存全量）。点查/过滤查询直出 Doris，Iceberg/Trino 退为：
- 历史快照 / 时间旅行（Iceberg 不可替代的能力）
- 批量分析 / 全表扫描（Trino 的强项）
- 容灾降级路径（Doris 不可用时）

### 红线 #4 修订

| | 修订前 | 修订后 |
|---|---|---|
| 红线 #4 | Doris **严格**作为索引加速层，不存全量明细、大字段、二进制 | Doris 作为**在线读主源**，存全量结构化属性；大字段/二进制类型以序列化引用形式存储。Iceberg/Trino 退为历史/分析/容灾 |

红线 #3（Iceberg 是主数据唯一写入入口）不变——Doris 全量数据仍由 IndexSync 从 Iceberg 同步，不直写。

## POC 数据支撑（2026-06-25）

用 benchmark golden flight 表（1 万行）在 Doris 建 Unique Key 全量表实测：

| 路径 | @10 p95 | @10 qps | 评价 |
|------|---------|---------|------|
| A. Trino-Iceberg（现状） | ~1900ms | 1.8 | 慢两个数量级 |
| B. Doris 全量 + 持久连接池 | **65ms** | **552** | 比 A 快 ~300 倍 |
| C. Doris 全量 + 每次新建连接 | 3064ms | 35 | 连接开销大 |

过滤查询（Doris 全量直出）：@10 p95=70ms、qps=436。

**关键发现**：连接管理决定成败——持久连接池 qps=552，每次新建连接 qps 仅 35。后端 Doris 客户端必须用连接池。

## 实施

### 代码改动
- `DorisIndexStore`：模块级 `aiomysql.create_pool`（lazy-init，lifespan 关闭）；新增 `load_by_ids`/`load_by_filter`（全量直出）；`create_index_table` 支持 `STORED_ONLY` 列（类型映射 `_DORIS_TYPE_MAP`）
- `IndexFieldExtractor`：取消红线排除，所有非 PK 属性都进 Doris（indexed 建索引，非 indexed 标 `STORED_ONLY`）；`ExtractionResult` 增加 `stored_columns`
- `IndexSyncService.sync_now`：从 Iceberg 读全量列同步到 Doris
- `ObjectQueryService._load_physical`：Doris 全量直出为主路径，Trino-Iceberg 降级
- `main.py` lifespan：关闭 Doris 连接池

### 表模型
- 单表 `idx_{ont}__{type}`（不拆 index 表 + full 表），Doris Unique Key 模型，INSERT 幂等
- 索引列（PRIMARY_KEY/INVERTED/RANGE/VECTOR）保持 VARCHAR(255) + 对应索引
- STORED_ONLY 列按 `_DORIS_TYPE_MAP` 映射源数据类型

## 后果

### 正面
- 点查性能提升 ~300 倍（qps 1.8→552），并发能力大幅改善
- 过滤查询单次往返（Doris 过滤 + 取全量合并为一条 SQL）
- 降级路径保留，Doris 故障时 Trino 兜底

### 负面 / 需监控
- **存储成本**：Doris 存全量，需监控单表行数/列数上限（booking 50 万行 × 全属性）
- **一致性窗口**：Doris 全量由 IndexSync 同步（~30s lag），点查可能读旧值 → 由 read-your-writes（PG object_state）前置补偿（当前临时禁用，见待办2）
- **同步延迟**：IndexSync 全量同步的延迟和写入放大需监控

## 修订记录

- **2026-06-25 初始修订**：从「纯索引层」升级为「在线读主源」，POC 数据支撑。原 ADR-001 决策被证伪（Iceberg 点查不够快）。
