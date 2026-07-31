# 待评估：Doris 存全量数据替代 Dataset(Iceberg/Trino) 查询

**记录时间**: 2026-06-25
**状态**: ✅ POC 完成 + 已全面实施
**记录时间**: 2026-06-25
**关联**: `docs/bugfix/db-connection-leak-and-point-lookup-perf.md`、benchmark read-path 性能、`docs/architecture/adr-001-doris-as-online-read-source.md`

---

## POC 结论（2026-06-25 实测）

用 benchmark golden flight 表（1万行）在 Doris 建 Unique Key 全量表，实测三种点查路径：

| 路径 | @10 p95 | @10 qps | 评价 |
|------|---------|---------|------|
| A. Trino 点查 Iceberg（现状） | ~1900ms | 1.8 | ❌ 慢两个数量级，Trino 单次点查 3-5s |
| B. Doris 全量 + 持久连接池点查 | **65ms** | **552** | ✅ 比 A 快 ~300 倍 |
| C. Doris 全量 + 每次新建连接点查 | 3064ms | 35 | 连接开销大，验证后端必须用持久连接池 |

过滤查询（status='Delayed' LIMIT 100，Doris 全量直出）：@10 p95=70ms、qps=436，同样远优于 Trino 路径。

**决策**：POC 数据充分支撑修订 ADR-001，Doris 升级为在线读主源（存全量属性），点查/过滤直出 Doris，Iceberg/Trino 退为历史快照/批量分析/容灾。后端 Doris 客户端**必须用持久连接池**（每次新建连接 qps 仅 35）。

---

## 背景

benchmark read-path 优化（跳过 PG read-your-writes）后，单次点查延迟从 131ms 降到 82ms，但并发下 p95 仍 ~1-2s、qps ~1.5。瓶颈转移到 **Trino 点查 Iceberg 慢**：

```
Trino 单次点查 iceberg.ontology.flight WHERE flight_id IN (1024): 3-5s/次
```

根因是 Iceberg 表无主键索引/分区，点查退化为全表扫描（booking 表 50 万行）。这是 Iceberg+Trino 点查的固有缺陷，靠分区只能缓解、无法根治。

由此引出一个架构层问题：**点查是高频热路径，是否应该让 Doris 直接存全量数据，点查完全不碰 Dataset(Iceberg/Trino)？**

---

## 当前架构（ADR-001 / 红线 #4）

Doris **严格作为索引加速层**，只存：主键 + 索引列 + 热点属性。查询流程：

```
点查(rids)  → 跳过 Doris → Trino 点查 Iceberg        ← 当前慢路径
过滤查询(filter)  → Doris 过滤出 IDs → Iceberg 取全量属性   ← Doris 只回 ID
```

`DorisIndexStore.query` 只 `SELECT pk_column`，全量属性必须回 Iceberg 取（`load_by_ids` → Trino）。所以即使 Doris 可用，点查和取属性都绕不开 Trino。

## 评估命题

让 Doris 存**全量属性**（不只是索引列），点查/取属性直接在 Doris 完成，Dataset(Iceberg/Trino) 退化为：
- 历史快照/时间旅行（Iceberg 不可替代的能力）
- 批量分析/全表扫描（Trino 的强项）
- 不再承担在线点查

预期收益：点查从「Trino 3-5s」降到「Doris ~10ms级」，并发 qps 数量级提升。

---

## 待评估事项

### 1. 架构红线影响
- [ ] 红线 #4「Doris 严格作为索引加速层，不存全量明细、大字段、二进制」——本方案直接违反，需评估是否修订 ADR-001
- [ ] 红线 #3「Iceberg 是主数据唯一写入入口」——若 Doris 存全量，Doris 数据来源仍是 Iceberg（经 IndexSync 同步），写入入口不变，但 Doris 成为在线读的主源
- [ ] 评估 Doris 与 Iceberg 的**一致性窗口**：Doris 全量数据由 IndexSync 同步，存在延迟，点查可能读到旧值（需结合 read-your-writes 补偿）

### 2. 存储成本与能力边界
- [ ] Doris 存全量后，单表行数/列数上限是否够用（booking 50 万行 × 全属性，Doris 4.0.5 容量评估）
- [ ] 大字段/二进制（如附件）是否仍排除在 Doris 外，只存结构化属性（建议保留 Iceberg 存大字段）
- [ ] Doris 的列存 + 倒排/向量索引对「按 ID 取全属性」是否真比 Trino+Iceberg 快（需 POC 实测）

### 3. 查询路径改造
- [ ] `DorisIndexStore` 新增 `load_by_ids`（返回全量属性，非仅 ID），或新增 `DorisObjectStore`
- [ ] `ObjectQueryService._load_physical` 点查路径改为：Doris `load_by_ids` 直出，失败才降级 Trino
- [ ] `IndexSyncService` 同步范围从「索引列」扩到「全量属性」，评估同步延迟与写入放大
- [ ] 降级策略更新：Doris 不可用 → Trino 点查 Iceberg（保留现有降级，作为容灾）

### 4. POC 验证（决策前置）
- [ ] 用 benchmark golden 数据集（flight 1万 / booking 50万）在 Doris 建全量表
- [ ] 实测对比三种点查路径的 p95/qps：
  - A. 现状：Trino 点查 Iceberg（已知 3-5s）
  - B. Doris 存全量 + Doris 点查
  - C. Doris 存全量 + Doris 点查 + Doris 不可用降级 Trino
- [ ] 实测过滤查询：Doris 全量直出 vs Doris 过滤 + Iceberg 回表
- [ ] 量化 IndexSync 全量同步的延迟和资源开销

### 5. 一致性与写入
- [ ] Action 写 PG object_state → outbox INDEX effect → Doris upsert（≤1s）+ outbox ARCHIVE effect → Iceberg MERGE（≤5min），延迟链路是否可接受（去 SeaTunnel 化后）
- [ ] Doris 全量数据的更新频率：外部接入走 ObjectIndexFunnel 全量 scan_latest；Action 写入走 outbox INDEX effect 近实时 upsert
- [ ] read-your-writes 在 Doris 主读路径下如何保证（PG object_state 仍前置，或 Doris 直读 + 版本号合并）

---

## 决策建议（待 POC 后定）

- 若 POC 证明 Doris 全量点查 p95 < 50ms 且同步延迟可接受 → 修订 ADR-001，Doris 升级为在线读主源，Iceberg/Trino 退为历史/分析/容灾
- 若 POC 显示 Doris 全量存储成本过高或同步延迟不可接受 → 维持现状，转而优化 Iceberg 分区策略 + Trino 物化视图缓解点查

无论哪种决策，**点查慢的问题必须解决**——它是 benchmark read-path 和未来生产读性能的核心瓶颈。

---

## 关联代码索引

| 位置 | 内容 |
|------|------|
| `src/ontology/layers/index/doris_index_store.py` | `query`（仅返回 ID）、`upsert`、`create_index_table` |
| `src/ontology/services/object_query_service.py` | `_load_physical`（点查/过滤路由）、`_fallback_to_trino_scan` |
| `src/ontology/services/index_sync_service.py` | Iceberg→Doris 同步编排 |
| `src/ontology/layers/dataset/iceberg_store.py` | `load_by_ids`（Trino 点查，当前慢路径） |
| `docs/architecture/adr-001-*.md` | Doris 作索引加速层（需评估修订） |

---

## 教训

1. **架构红线要定期用真实负载复盘**：ADR-001 定 Doris 为"纯索引层"时，可能没预料到 Trino 点查 Iceberg 会慢到 3-5s。红线基于的假设（Iceberg 点查够快）被实测证伪时，红线本身要进入评估，不能教条坚持。
2. **"索引层 vs 全量层"的边界由查询模式决定**：高频点查取全属性的场景，索引层回表全量层（Doris→Iceberg）的跨组件往返本身就是性能税。当点查占比高时，让加速层直接持有全量更合理。
3. **POC 驱动架构决策**：改 ADR-001 影响面大，必须用 benchmark 实测数据支撑，不能凭推理下结论。
