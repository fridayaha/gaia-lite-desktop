# TextQL 完整设计文档（含参考材料与验证证据）

> 本文档为 [ADR-012](./adr-012-textql-ontology-driven-nl-query.md) 的配套资料库，固化：
> 1. 设计过程讨论中用户提供的全部参考材料原文（防丢失）
> 2. 三轮 SqlGlot 可行性验证的完整脚本与结果
> 3. SQL 类型学分析过程
>
> 设计定稿日期：2026-06-27

---

## 目录

- [一、设计讨论过程回顾](#一设计讨论过程回顾)
- [二、SQL 类型学分析](#二sql-类型学分析)
- [三、可行性验证证据](#三可行性验证证据)
- [三-bis、实现记录（Phase 1-2 落地）](#三-bis实现记录phase-1-2-落地2026-06-28)
- [四、附录：参考材料原文](#四附录参考材料原文)

---

## 一、设计讨论过程回顾

### 讨论脉络

1. **第一轮**：用户提供 TextQL 四步流水线 + Palantir 七层机制材料，要求讨论实现方案。AI 先读代码摸现状。
2. **第二轮**：AI 读代码后确认 Gaia 已具备 80% 基础设施，走的是 Palantir 范式 B（LLM Tool Use），但召回 + Schema 注入缺失导致 `text_to_sql 0/70`。提出五步流水线设计。
3. **第三轮**：用户反馈三点：
   - aliases 不单独建字段，复用 description 做语义检索
   - 向量模型用 `paraphrase-multilingual-MiniLM-L12-v2`，CPU 部署，Doris 原生支持
   - 19 个原子工具不够，需补 text2sql（LLM+Schema+SqlGlot），但与 ObjectSet 结合方式未想清楚
4. **第四轮**：AI 提出 text2sql 与 ObjectSet 结合的三种方案，推荐方案 2（ObjectSet 作逻辑视图）。用户要求先验证可行性。
5. **第五轮**：AI 跑 v1 验证（6/12），暴露列归属解析等难点。修 v2（14/15）。用户给车企全链路场景材料，要求补验证。
6. **第六轮**：AI 跑 v3（10/13），覆盖车企 5 表全链路 JOIN + 占比/同比/趋势等核心 BI 场景。明确编译器边界（只做 SELECT，UPDATE 走 Action）。
7. **第七轮**：用户确认四点（可行性/边界/Phase1范围/CI回归），要求输出完整方案文档，记录所有参考信息。→ 本文档产出。

### 用户确认的关键决策点

1. ✅ 架构上同意方案 2（ObjectSet 作逻辑视图，text2sql 编译到物理表）
2. ✅ 编译器边界：只做 SELECT，UPDATE/Action 走现有 Action 工具，What-if 走 Scenario，多步推理走工具链编排
3. ✅ Phase 1 范围：单表 + JOIN(≤5表) + 子查询 + 聚合 + 窗口 + 时间函数 + 占比计算；CTE/复杂自连接/同比环比/UNION 放 Phase 2
4. ✅ 三个验证脚本作为编译器 CI 回归基线

---

## 二、SQL 类型学分析

基于材料六（8大通用场景）+ 材料七（车企6大业务域）的所有自然语言问句，归纳为 15 种 SQL 能力类型：

| 类型 | SQL 特征 | 出现频次 | 材料典型问句 | 编译器覆盖 |
|------|---------|---------|------------|-----------|
| T1 单表过滤检索 | WHERE+排序+分页 | 极高 | "2025Q2下单的华东区企业客户" | ✅ v1 |
| T2 跨实体 JOIN | 多表 JOIN via LinkType | 极高 | "逾期订单对应的客户负责人" | ✅ v1 |
| T3 多层关联穿透 | 3+表链式 JOIN | 高 | "整车→总成→零件→供应商" | ✅ v3 |
| T4 多维聚合 | GROUP BY 多维+SUM/COUNT | 极高 | "每区域销售额分别多少" | ✅ v3 |
| T5 占比/比率计算 | 聚合相除 | 高 | "VIP客户占比""复购率" | ✅ v3 |
| T6 同比/环比对比 | SELF JOIN 跨期 | 高 | "同比去年变化多少" | 🟡 Phase2 |
| T7 TopN+占比 | ORDER BY+LIMIT+窗口占比 | 中 | "Top10回款客户及占比" | 🟡 Phase2 |
| T8 排名/分位 | ROW_NUMBER/RANK OVER | 中 | "各产线周转天数排序" | ✅ v2 |
| T9 时间序列趋势 | 按时间分组+排序 | 高 | "每月故障率变化趋势" | ✅ v3 |
| T10 异常根因多步推理 | 多次查询+LLM编排 | 中 | "延迟率上升原因" | ⚠️ 工具链编排 |
| T11 What-if 情景模拟 | 参数化重算 | 中 | "如果涨价10%利润降多少" | ⚠️ Scenario 引擎 |
| T12 Action 回写 | UPDATE/INSERT | 高 | "把订单交付日期延后" | ⚠️ Action 工具 |
| T13 审计溯源 | 查日志表 | 中 | "订单修改记录和操作人" | 🟡 需审计本体 |
| T14 多轮上下文追问 | 复用前序 ObjectSet | 高 | "其中VIP占比多少" | ⚠️ 对话状态管理 |
| T15 全链路端到端 | 6+表 JOIN 跨域 | 低但价值极高 | "下单→排产→...→售后" | ✅ v3(5表) |

### 关键洞察

- 查询类（T1-T9 + T15）是 text2sql 编译器的职责范围，覆盖材料绝大多数"查数/分析"问句
- T10/T11/T12/T14 不是编译器问题，分别属于工具编排/Scenario/Action/对话状态，需明确划界
- T5（占比）+ T6（同比环比）是企业 BI 最高频类型，必须在 Phase 1-2 覆盖
- T15（车企全链路）是 Gaia 在垂直行业的核心价值，5 表 JOIN 已验证通过

---

## 三、可行性验证证据

### 验证环境
- sqlglot 30.12.0
- Python 3.12
- 模拟本体 Schema（车企全链路 9 个 ObjectType + 完整 LinkType）

### 验证脚本

脚本保留于仓库，作为编译器技术预研原型与可行性证据（脚本内内联了独立的原型编译器实现，与生产实现 `src/ontology/services/textql/sql_compiler.py` 分离；不参与 CI 回归，生产回归由 `tests/unit/` 覆盖）：
- `scripts/verify_sqlglot_feasibility.py`（v1，基础 12 场景）
- `scripts/verify_sqlglot_feasibility_v2.py`（v2，修正列归属 15 场景）
- `scripts/verify_sqlglot_feasibility_v3.py`（v3，车企全链路 13 场景）

### v1 结果：6/12 通过

**通过**（6）：单表过滤排序分页、多表JOIN、子查询、自定义算式、非法表名拦截、非法列名拦截、非法JOIN拦截

**失败**（6）及原因：
- 多表列归属解析失败（`CANNOT_RESOLVE_COLUMN_OWNER`）：列前缀是 alias，遍历顺序导致 Table 已改写，alias→ObjectType 映射丢失
- 别名列校验误报（`INVALID_COLUMN: 'rn'/'total'`）：SELECT 的 `AS xxx` 是输出别名，不该校验
- SQL 注入误报：`DROP TABLE x` 触发表校验（实际是好事，多语句被拦）

**修复方向**：两遍遍历（pass1 收集映射，pass2 改写）+ 物理名反查 + 输出别名跳过

### v2 结果：14/15 通过

**修复**：两遍遍历 + `from_`/`with_` key（SqlGlot 30.x 坑）+ 物理名反查

**通过**（14）：含自连接、嵌套子查询+JOIN、CASE 表达式、SQL注入拦截

**失败**（1）：CTE+UNION（场景7）—— CTE 名 `vip`/`overdue` 不是 ObjectType，外层 `SELECT customerId FROM vip` 无法解析。根因：CTE 作为"虚拟表"其输出列需追溯到定义内部。**定性为工程量问题，Phase 2 解决**。

### v3 结果：10/13 通过

**覆盖真实业务 SQL 类型**：4-5表JOIN穿透、占比计算、同比环比、窗口函数占比、时间序列趋势、多维聚合

**通过**（10）含关键场景：
- T3a 4表JOIN穿透（索赔→车辆→零件→供应商）✅
- T15 5表全链路（订单→客户→车辆→排产→索赔）✅
- T5a VIP客户占比（聚合相除）✅
- T5b 复购率（子查询+CASE）✅
- T6a 同比销售额（SELF JOIN 跨期）✅
- T6b 环比（本月vs上月）✅
- T7 Top10客户及金额占比（窗口函数 SUM OVER）✅
- T9a/T9b 月度趋势（DATE_FORMAT 分组）✅
- T10 多步推理单步（边界场景，编译器只处理单步）✅

**失败**（3）及定性：
- T12a/T12b UPDATE 回写：**设计边界**，非缺陷。UPDATE 不进编译器，走 Action 工具
- T4 三维拆解多表同名列歧义：LLM 生成质量约束，prompt 引导 + 友好报错解决

### 5表全链路 JOIN 编译产物示例

```sql
-- 输入：逻辑 SQL（LLM 生成，api_name + ObjectType 名）
SELECT o.orderId, c.customerName, v.vin, pp.planDate, cl.faultCode
FROM Order o JOIN Customer c ON o.customerId = c.customerId
JOIN Vehicle v ON o.vehicleId = v.vehicleId
JOIN ProductionPlan pp ON v.vehicleId = pp.vehicleId
JOIN Claim cl ON v.vehicleId = cl.vehicleId
WHERE o.status = 'DELIVERED'

-- Doris 编译产物
SELECT o.order_id, c.customer_name, v.vin, pp.plan_date, cl.fault_code
FROM idx_auto__order AS o JOIN idx_auto__customer AS c ON o.customer_id = c.customer_id
JOIN idx_auto__vehicle AS v ON o.vehicle_id = v.vehicle_id
JOIN idx_auto__production_plan AS pp ON v.vehicle_id = pp.vehicle_id
JOIN idx_auto__claim AS cl ON v.vehicle_id = cl.vehicle_id
WHERE o.`status` = ?
-- params: ['DELIVERED']
```

### 关键技术要点（验证得出）

1. **两遍遍历必须**：Pass 1 收集 alias→ObjectType + CTE 定义 + 子查询输出列；Pass 2 改写。否则列归属解析失败。
2. **SqlGlot 30.x 的 args key 是 `from_`/`with_` 不是 `from`/`with`**（调试坑，花两轮才定位）。
3. **列归属解析三层 fallback**：alias 前缀 → alias_map；CTE/子查询别名 → 输出列集合（信任内层已校验）；无前缀 → 单表 fallback（多表歧义时报错）。
4. **递归改写时 Table.name 会变物理名**，列解析要兼容两种形态（物理名反查 `phys_to_ot`）。
5. **参数化绑定**：字面量抽到 `?` 占位 + params 列表，Doris/Trino 都支持。替代手写转义。
6. **UPDATE 节点结构**：`args["this"]` 是 Table，`args["expressions"]` 是 SET 子句（EQ 节点），WHERE 在 `args["where"]`。但 UPDATE 不进编译器。

### Doris 4.x VECTOR/ANN 关键语法（经文档核实）

```sql
-- 建表：向量存 ARRAY<FLOAT>，USING ANN 索引
CREATE TABLE idx_ontology_semantic (
  ontology_api_name VARCHAR,
  element_type VARCHAR,
  element_api_name VARCHAR,
  display_name VARCHAR,
  description VARCHAR,
  embedding ARRAY<FLOAT>,      -- 384 维
  INDEX idx_vec (embedding) USING ANN PROPERTIES(
    "index_type" = "hnsw",     -- hnsw | ivf | ivf_on_disk
    "metric_type" = "inner_product",  -- l2_distance | inner_product
    "dim" = "384",
    "quantizer" = "flat"       -- flat | sq8 | sq4 | pq
  )
) UNIQUE KEY(ontology_api_name, element_type, element_api_name)
  DISTRIBUTED BY HASH(ontology_api_name);

-- cosine 检索：向量 L2 归一化后用 inner_product（cosine 不直接支持）
SELECT element_api_name, display_name,
       inner_product_approximate(embedding, ?) AS similarity
FROM idx_ontology_semantic
WHERE ontology_api_name = ?
ORDER BY similarity DESC
LIMIT 10;
-- params: [<384维归一化查询向量>, <ontology>]

-- l2_distance 检索
SELECT id, l2_distance_approximate(embedding, ?) AS distance
FROM table ORDER BY distance ASC LIMIT 10;
```

**注意**：现有 `doris_index_store.py` L191 用的是旧 `USING VECTOR` 语法，Phase 2 需迁移到 `USING ANN`。


## 三-bis、实现记录（Phase 1-2 落地，2026-06-28）

> 可行性验证（§三）证明方案可行后，Phase 1-2 已完整实现。本节记录实现期的关键发现、与设计的偏差及原因，作为续开发/运维参考。完整组件状态见 [implementation-status.md](./implementation-status.md)。

### 1. 与设计期的关键偏差

#### 偏差一：ONNX CPU 推理替代 sentence-transformers + torch

**设计期**（§三 验证环境 + ADR §Step 2.3）：建议 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` + torch。

**实现期现实**：sentence-transformers 依赖 torch（~800MB），国内镜像下载超时；DeepSeek API 不提供 embedding 端点（`/embeddings` 返回 404，官方只有 chat 模型）。

**最终方案**：ONNX Runtime + tokenizers，用模型仓库已有的量化 ONNX 文件 `onnx/model_qint8_avx512.onnx`（113MB）。
- 依赖仅 `onnxruntime` ~50MB + `tokenizers` ~5MB，国内镜像秒装，无 torch
- 推理 ~15ms/句（比 torch 快），CPU 友好
- `OnnxEmbeddingProvider` 实现 `EmbeddingProvider` Protocol，可插拔
- 模型手工下载到 `models/`（已 gitignore），4.4GB 含 4 种权重格式，实际只用 ONNX 量化版

**教训**：设计期的"建议技术栈"在网络受限环境可能不可行，实现期要准备 Plan B。ONNX 是轻量推理的好选择，量化模型更适合 CPU。

#### 偏差二：Doris ANN 索引构建路径

**设计期**（§三 Doris 4.x VECTOR 语法）：CREATE TABLE 内联 `USING ANN PROPERTIES` 建索引。

**实现期现实**：inline ANN 索引触发 memtable load 预分配 ~2GB，超过 dev 容器 1GB → `MEM_LIMIT_EXCEEDED`。HNSW 和 IVF 都有此问题。

**最终方案**：两步走——
1. `create_semantic_table` 建表**不带 ANN 索引**（纯 ARRAY<FLOAT> 列）
2. 数据 upsert 后用 `ALTER TABLE ... ADD INDEX ... USING ANN` 建索引（走低内存路径）

索引类型从 HNSW 改为 **IVF**（`nlist=128`）——IVF 内存更友好，适合小规模本体元数据（几十到几百元素）。

**教训**：Doris 4.x ANN 的 inline 建索引有内存放大问题，生产环境也建议用 ALTER ADD INDEX 路径。

#### 偏差三：Doris BE 容器内存调优

**实现期新增**：为支持 ANN，`config/doris/be.conf` 加 `mem_limit=80%` + `load_mem_limit=80%`（按容器内存算），`docker-compose.yml` doris-be 内存 `1g→3g`。这是实现期才暴露的环境约束，设计期未预见。

#### 偏差四～六：2026-07 修订（设计决策 C + 方言感知联邦 JOIN + SELECT * 展开）

**修订背景**：Phase 1-2 落地后，多表 JOIN 场景暴露三个深层问题，2026-07 统一修订。

**偏差四：删除 `query_with_sql` 的 `object_type` 参数（设计决策 C）**

- **设计期**：`query_with_sql(ontology, object_type, sql)`，`object_type` 作“主 ObjectType，Schema 锡点 + 权限”。
- **问题**：多表 JOIN 时 `SELECT a.p1, b.p2 FROM A JOIN B` 没有唯一“主对象”，填谁都有权限/路由/列名回映的漏洞（只校验一个 OT 的读权限、只看一个 OT 的 storage_type、列名回映只用一个 OT 的映射表）。
- **修订**：删除参数，编译器新增 `involved_object_types(sql)` 从 SQL 推断所有引用 OT，对每一个统一做权限/路由/回映。原则“把复杂留给自己，把简单留给客户”——SQL 已写明所有表，不要求调用方重复提供。

**偏差五：方言感知物理名 + Trino 跨 catalog 联邦 JOIN（修订 MIXED_STORAGE_JOIN）**

- **设计期**：编译器对 Doris/Trino 两方言输出同一物理名（Doris `idx_<ont>__<type>`），路由判定看单个 OT 的 storage_type；MANAGED+VIRTUAL 混合报 `MIXED_STORAGE_JOIN` 拒绝。
- **问题**：① MANAGED 表降级 Trino 时用 Doris 名 `idx_*`，Trino 查不到（应是 `iceberg.ontology.<snake>`），降级路径必失败；② Trino 本就支持跨 catalog JOIN，拒绝混合存储是错误保守。
- **修订**：编译器方言感知——MANAGED 表 Doris 方言=`idx_<ont>__<type>`，Trino 方言=`iceberg.ontology.<snake_type>`；VIRTUAL 两方言均用外部三段式。路由：全 MANAGED→Doris主/Trino降级，含 VIRTUAL→Trino 跨 catalog 联邦 JOIN（不再报 MIXED_STORAGE_JOIN）。另：表改写为物理名后无 alias 的表自动设 `AS <逻辑 OT 名>`，使列前缀仍可解析。

**偏差六：`SELECT *` 编译期展开（同 apiName 冲突消歧）**

- **设计期**：`SELECT *` 透传给 Doris/Trino 裸展开。
- **问题**：多表 JOIN 时两 OT 同 apiName 属性（如都有 `id`/`status`）在 DB 层同名列冲突，dict 合并后后者覆盖前者，数据静默丢失。
- **修订**：编译期把顶层 `SELECT *`（parent 为 Select 的 Star）展开为显式列，同 apiName 冲突加 OT 前缀（`<OT>_<api>`，如 `ManualOutboundCall_id`），不冲突保持纯 apiName。`COUNT(*)` 内 Star 不展开；CTE/子查询 `*` 不展开。用户显式别名始终尊重。

**教训**：单表/点查场景的设计假设（单一主对象锡点、单一物理名、裸 `*`）在多表 JOIN 场景全部失效。多表 JOIN 是独立的语义层，需要推断式权限/路由/回映、方言感知物理名、星号展开消歧三套机制叠加才能正确。

### 2. 实现期发现的关键技术要点（设计期未记录）

#### Doris MySQL 协议不支持 ARRAY 参数化

`vector_search` 的查询 embedding 不能用 `%s` 占位符（Doris 当 VARCHAR，报 `inner_product_approximate(ARRAY<FLOAT>, VARCHAR)` 类型错）。embedding 内联为 `[1.0,2.0,...]` ARRAY 字面量——embedding 是模型 L2 归一化输出（非用户输入），内联安全。

**语法坑**：`[1.0,2.0]` 是 ARRAY 字面量，`ARRAY([1.0,2.0])` 会被解释成 `ARRAY<ARRAY<...>>` 嵌套类型。必须用 `[...]` 而非 `ARRAY([...])`。

#### Doris LIMIT 不接受参数化占位符

编译器对 `exp.Limit`/`exp.Offset` 的 Literal 内联（不参数化），否则 Doris 报 `mismatched input 'LIMIT'`。数字字面量内联安全（SQL 语法保证只能是数字）。

#### Doris ?→%s 占位符转换

Doris 用 MySQL 协议（aiomysql），占位符是 `%s` 不是 `?`。`DorisIndexStore.execute_sql` 把编译器产出的 `?` 转成 `%s`，让调用层保持 dialect 无关。Trino 原生支持 `?`，无需转换。

#### 语义表必须 DUPLICATE KEY

Doris ANN 索引只能用在 DUP_KEYS 表（`ANN index can only be used in DUP_KEYS table`）。语义表用 `DUPLICATE KEY`，upsert 语义靠应用层 delete-then-insert 实现。

#### CTE 输出列只收集 AS 别名

`_collect_output_cols` 只收集 `AS alias` 的输出列，不收集 bare column（如 `SELECT amount`）。否则 CTE 内 `SELECT bogus FROM Order` 的 `bogus` 会被误当 CTE 输出列而跳过白名单校验——护栏漏洞。bare column 引用走 ObjectType 白名单校验。

#### `_is_cte_output_col` 的跨 CTE 边界问题

`find_ancestor(exp.Select)` 会跨 CTE 边界向上找，可能把 CTE 内部列误判为外层 CTE 输出列。解决：`_collect_output_cols` 只收 AS 别名（bare column 不收），内层列仍走 ObjectType 校验。

### 3. 循环导入处理

`textql/orchestrator.py` type-annotates `Container`，直接导入会循环（`routes/ai` → `container` → `object_query_service` → `textql/__init__` → `orchestrator` → `container`）。解法：
- orchestrator 用 `TYPE_CHECKING` 导入 Container（运行时不导入）
- `textql/__init__.py` 不导出 orchestrator（routes/ai 懒导入）
- `object_query_service` 直接从 `textql.sql_compiler` 导入（不经 `__init__`）

### 4. 端到端验证的真实数据（Airline 本体）

#### 向量化规模
- 9 ObjectType + 61 Property + 8 LinkType = **78 元素**索引到 Doris 语义表
- 单次全本体向量化（embedding + upsert + 建索引）秒级完成

#### 引擎B 召回质量（口语化表达，引擎A 失效场景）

| 口语化查询 | 引擎B Top 召回 | 相似度 | 引擎A |
|-----------|---------------|--------|-------|
| "维修保养" | MaintenanceTask (OT) | 0.696 | 失效 |
| "机组人员" | Crew (OT) + 角色/部门 | 0.870 | 失效 |
| "旅客订座" | 座位号/票价/旅客编号 | 0.858 | 失效 |
| "大飞机" | 机龄/机型/制造商 | 0.846 | 失效 |

#### ONNX 推理性能
- 单句延迟：**15.1ms**（CPU，384 维）
- 5 句批量：84.5ms
- 语义质量：卡车↔Truck = 0.959，大车↔货运车辆 = 0.631，无关项 < 0.5

### 5. 测试基线

| 测试文件 | 用例数 | 覆盖 |
|---------|--------|------|
| test_textql_schemas.py | 24 | IR schema 表达力 + 序列化 + 校验 |
| test_sql_compiler.py | 32 | 编译器全特性 + 三大护栏 + CTE |
| test_semantic_recall.py | 9 | 引擎A 精确匹配 + 澄清 |
| test_vector_recall.py | 8 | 引擎B 融合 + VectorIndexer |
| test_embedding.py | 7 | ONNX 推理 + 语义相似度 |
| test_schema_injector.py | 7 | Schema 注入 + 护栏提示 |
| test_textql_orchestrator.py | 7 | Step 1-3 串联 + 失败回退 |
| test_object_query_whitelist.py | 21 | 白名单护栏（路标 #2） |
| test_textql_e2e.py | 5 | 端到端（GAIA_TEXTQL_E2E=1 触发） |
| **合计** | **115 + 5** | |

全量回归：908 测试 0 破坏。ruff + mypy --strict 全过。


---

## 四、附录：参考材料原文

### 材料一：TextQL 四步流水线

> 商业产品，不开源，仅取方法论。本方案不复刻 TextQL，落地其设计思想到 Gaia 自研代码。

**核心定位**：区别于直接靠大模型生成 SQL 的通用方案，TextQL 整套 Text2SQL 以企业专属业务本体为底层骨架，通过结构化、可管控的分步流程产出稳定、可解释的 SQL，规避 LLM 幻觉、不可控的缺陷。

**完整四步生成流程**（案例：上月销量前5的商品）：

1. **搭建企业专属业务本体**：梳理业务实体（客户、订单、商品等），定义实体关联关系，构建业务到数仓表结构映射图谱。类比：图书馆搭建杜威十进制分类体系。输出：标准化本体模型，记录表关联、业务对象定义。
2. **NLP 提取查询关键属性**：传统 NLP 解析用户自然问句，抽取查询核心要素（实体、指标、筛选条件）。类比：读者输入书名/主题，系统检索对应分类编码。输出：用户查询所需的属性关键词列表。
3. **属性与本体语义映射匹配**：通过向量嵌入做语义+词汇双层匹配，将业务名词映射到数据库真实字段；复用本体预定义的表关联路径自动补齐 JOIN 逻辑。类比：拿着分类编码，按图书馆分区找到对应藏书。输出：绑定数据库字段、关联关系的标准化本体属性。
4. **基于预定义规则编译生成 SQL**：使用领域专用语言 DSL，按本体预设的指标、维度、过滤、排序、分页规则，自动拼装完整可执行 SQL；所有业务指标计算公式、关联逻辑均提前固化在本体中。类比：告知图书馆员检索条件，馆员按固定规则整理出目标书单。输出：完整、可直接运行的标准 SQL 语句。

**行业痛点**：
- 传统 LLM 直出 SQL 幻觉严重、多表 JOIN 准确率低
- 语义层（LookML/dbt）只能覆盖固定指标，跨系统、多源数据无力
- 业务人员查数必须依赖分析师，周期数天
- 企业数仓分散无统一业务语义映射

**TextQL 内部技术栈**：
- 本体存储：图数据库 + 关系库存储实体关联图谱
- 语义匹配：向量嵌入做业务术语模糊检索
- SQL 编译：自研 DSL 编译器 + SQLGlot 做跨库 SQL 转换
- LLM 层：GPT-4/Gemini 仅用于意图抽取，SQL 主体不靠 LLM 生成

### 材料二：Palantir 七层机制

> 公开描述，取架构蓝本。核心思想：LLM 作为推理引擎和编排器，调用由本体驱动的确定性工具。

**核心基石：本体（Ontology）**——一个活着的业务"数字孪生"。不是静态数据字典，而是动态的、决策导向的语义层，将底层复杂数据表映射为业务人员熟悉的对象（Objects）、属性（Properties）和关系（LinkTypes）。

**七层深度剖析**：

1. **输入理解与意图解析**：意图识别（查询/分析/执行动作）+ 实体提取与消歧
2. **多级语义检索与匹配**：元数据匹配（displayName/description/别名）+ 向量语义检索（Embeddings）+ HyDE（假设性答案检索）
3. **本体模式注入与上下文构建**：注入候选 ObjectType 完整模式（属性/关联/数据类型/业务约束）+ 确定性检索上下文
4. **工具调用与可执行查询生成**：LLM 作为编排器，选择并调用预定义工具，生成 ObjectSet

**三大核心约束（护栏）**：
1. 实体约束：LLM 只能查询本体中已定义的 ObjectType
2. 字段约束：LLM 只能使用该 ObjectType 下已定义的 Property
3. 关系约束：跨对象查询只能通过本体中已定义的 LinkType 进行 JOIN

**中文场景特殊适配**：
- displayName 优先匹配中文
- apiName 必须规范设置英文标识（否则语义匹配成功但查询生成失败）

### 材料三：三元元数据体系协同机制

| 元数据 | 服务对象 | 核心作用 | 是否可人工编辑 |
|--------|---------|---------|--------------|
| displayName | 人（用户、业务人员） | 语义理解、结果展示 | ✅ 是 |
| apiName | 机器（API、代码） | 生成可执行查询 | ✅ 是（需遵循规范） |
| rid | 系统（平台底层） | 权限、路由、定位、缓存 | ❌ 否（自动生成） |

**三元素协同链路**：

| 阶段 | 使用的元数据 | 作用 |
|------|------------|------|
| 用户输入"货运车辆" | displayName | 语义匹配，锁定业务概念 |
| 系统确认匹配对象 | rid | 校验权限、定位数据源 |
| 生成可执行查询 | apiName | 构造 `Truck.produceYear = 2025` |
| 执行查询并返回 | displayName | 用"出厂年份"渲染结果标题 |
| 出错时定位问题 | rid | 错误信息中精确指向问题资源 |

**一句话**：displayName 让 AI"听懂"人话，apiName 让 AI"写出"机器码，rid 让系统"找准"数据和权限。

### 材料四：语义召回双引擎机制

| 引擎 | 核心逻辑 | 优势 | 劣势 |
|------|---------|------|------|
| 精确匹配引擎 | 基于 displayName/description/别名字面匹配 | 确定性高、零幻觉 | 无法处理同义词/口语化表达 |
| 向量检索引擎 | 基于 Embedding 语义相似度匹配 | 泛化能力强、容错性高 | 可能召回无关内容 |

**Palantir 支持的四种检索方法**：

| 方法 | 说明 |
|------|------|
| Keyword Search | 基于 BM25 算法的关键词频率检索 |
| Vector Cosine | 基于嵌入向量的余弦相似度检索 |
| Augmented Keyword Search | LLM 生成关键词、同义词、相关词进行增强检索 |
| HyDE | LLM 生成假设性答案后做向量检索 |

支持 **Rank Fusion**（排名融合）组合多种检索方法。

**HyDE 核心原理**：先让 LLM 根据用户问题生成"假设性答案片段"，然后对该假设性答案向量化再去匹配，弥合 Query 与 Document 的语义鸿沟。

**双引擎协同**：精确匹配优先（兜底确定性），向量检索兜底（覆盖泛化性），结果融合去重重排，本体约束确保安全性（召回结果只能来自本体已定义元素）。

### 材料五：Schema 注入机制

**六大类注入内容**：
1. ObjectType 基础信息（apiName/displayName/description/rid）
2. 所有 Property 完整定义（apiName/displayName/数据类型/description/约束）
3. 所有 LinkType 定义（关联对象/方向/名称/基数）
4. 数据源映射（Backing Datasources）
5. 权限元数据
6. 类型类与特殊能力（地理空间/向量搜索/时序）

**数据类型（Value Type）语义包装器**：String/Integer/Double/Boolean/Date/Timestamp + Geospatial/Vector/Markdown/Hyperlink + Enum

**两步走实现**：
- Object Type Search（对象类型搜索）：基于元数据粗筛候选 ObjectType
- Object Type Lookup（对象类型查找）：针对具体 ObjectType 检索完整元数据

**确定性检索上下文**：每次用户消息都确定性地从数据源获取信息传给 LLM。可预测/可审计/可配置。配置参数：对象集范围、最大对象数量（1-25，默认5）、内容属性（1-5）。

**三大护栏本质**：把 LLM 从"什么都能编的创作者"降级为"只能按图施工的工匠"——拥有理解自然语言和推理的灵活性，但所有操作被锁定在本体定义的确定性边界内。

### 材料六：Tool Use 机制

**核心设计**：Palantir 从不让 AI"回忆"数据，而是让 AI"去查"数据。LLM 被降级为推理引擎和编排器。

**三大类工具**：
1. **Data 工具**：Query Objects（生成 ObjectSet）/ Ontology Aggregation（聚合运算）/ Ontology SQL（对 ObjectSet 执行 SQL 查询）
2. **Logic 工具**：执行预测、优化等计算模型
3. **Action 工具**：创建或修改本体对象

**ObjectSet**：并非真实数据拷贝，而是一个指向数据逻辑的"视图"（View）。三大核心约束防止 LLM 越界。

**OAG（本体增强生成）**：RAG 的进阶版，将 LLM 锚定在由本体定义的数据、逻辑、行动三位一体的企业运营现实中。

### 材料七：落地场景全景（8 大通用场景）

1. **基础业务对象检索**（T1）：按条件精准定位业务实体。典型："2025年第二季度下单的华东区企业客户有哪些？"
2. **跨实体关联探查**（T2/T3）：沿业务关系顺藤摸瓜。典型："这批逾期订单对应的客户负责人和联系方式是什么？"
3. **多维聚合分析**（T4/T5）：指标统计、分组对比与趋势查看。典型："今年上半年每个区域的销售额分别是多少？"
4. **异常根因排查**（T10）：多步推理拆解复杂问题。典型："上周华东区配送延迟率突然上升的原因是什么？"
5. **情景模拟推演**（T11）：What-if 假设性分析。典型："如果原材料价格上涨10%，三季度利润会下降多少？"
6. **业务操作执行**（T12）：查询后直接完成行动闭环。典型："把订单#20250701的预计交付日期延后到7月15日"
7. **合规溯源审计**（T13）：验证数据来源与操作轨迹。典型："追溯订单#10235的所有修改记录和操作人"
8. **连续对话追问**（T14）：基于上下文递进式探索。典型："（接华东区客户列表后）那其中VIP客户占比多少？"

**核心特征**：用户全程使用业务语言，不需要提及任何表名、字段名、SQL 语法或系统名称。

### 材料八：车企制造领域场景（6 大业务域）

#### 一、研发工程域：设计、BOM 与试验数据贯通
- 场景1：多层级 BOM 与零部件谱系查询（"2025款纯电SUV的动力电池包BOM包含哪些一级总成，对应供应商分别是谁？"）
- 场景2：试验故障与设计变更关联分析（"本次耐久试验电机绝缘失效样件对应哪个设计版本，近期有无相关设计变更？"）
- 场景3：研发问题闭环与整改跟踪（"给负责电池包设计的团队下发整改任务，要求3天内提交失效分析报告"）

#### 二、生产制造域：工厂数字孪生下的可视与可控
- 场景1：在制品与生产进度跟踪（"VIN尾号1234的车辆现在在哪个工位，有没有异常停线？"）
- 场景2：制程质量根因排查（"昨天总装下线车辆中多少台存在螺栓扭矩超差，集中在哪几个工位？"）
- 场景3：产线设备运维与预测性维护（"焊装线3号机器人最近7天故障停机时长和故障类型分布"）
- 场景4：生产排程扰动模拟（"如果明天涂装车间缺2名操作工，当日产量下降多少，哪些订单延期？"）

#### 三、采购与供应链域：全链路韧性与风险管控
- 场景1：供应链风险穿透排查（"某地区封控会影响哪些供应商的哪些零部件供应？"）
- 场景2：缺料影响情景模拟（"如果某款车规芯片交付延后2周，导致哪些车型减产，影响多少台产量？"）
- 场景3：库存与物流全链路跟踪（"动力总成类零部件全厂库存有多少，分别在哪些仓库，可支撑多少天生产？"）

#### 四、销售与渠道域：订单、库存与经营健康度
- 场景1：订单全链路跟踪（"订单号A2025070123的车现在排产了吗，预计什么时候下线交付？"）
- 场景2：经销商库存健康度诊断（"全国经销商纯电车型库存总量是多少，库存周转天数平均多少天？"）
- 场景3：销量多维拆解与预测（"按省份、车型、价位三个维度拆解二季度销量构成"）

#### 五、营销与用户运营域：用户洞察与配置优化
- 场景1：用户偏好与车型配置分析（"2025款车型用户选装率最高的三个配置是什么？"）
- 场景2：线索转化与投放效果分析（"二季度各渠道线索量和最终转化率分别是多少，哪个渠道ROI最高？"）

#### 六、售后服务与质量域：故障、索赔与召回闭环
- 场景1：售后故障爆发性识别（"近一个月上报的动力系统故障主要集中在哪些车型和生产批次？"）
- 场景2：质量问题全链路追溯（"VIN码为LXXXXXXXX的车辆出现动力电池故障，追溯其全链路信息：生产工厂、电池包批次、电芯供应商、出厂质检记录"）
- 场景3：召回范围精准定位与成本测算（"因某零部件缺陷需要召回，涉及多少台在役车辆，分布在哪些区域？"）
- 场景4：备件库存与需求预测（"全国备件中心的常用易损件库存水平，哪些存在缺货风险？"）

#### 七、跨域全链路：端到端穿透式查询（车企专属核心价值）
- "从售后批量出现的刹车故障，一路追溯到对应的零部件供应商、原材料批次、生产工位、质检记录，找出根本原因"
- "全链路追踪一台车：从用户下单→排产→零部件齐套→生产下线→物流运输→经销商交付→售后维修的完整生命周期"

**车企价值定位**：本体驱动架构在车企最不可替代的价值——跨越研发、制造、供应、售后多个业务域的端到端穿透查询，传统 BI 需要跨多个系统人工拼接数据，本体中通过 LinkType 一次关联即可完成。

### 材料九：整体收束（方法论）

> 来自材料第二十轮收束

这套机制最值得研究的地方，从来不是"用了什么 AI 模型"，而是**如何用系统工程的思维，把一项不确定的技术，转化为一套可落地、可治理、可信任的企业级生产力**。它的精妙与局限都源于同一个内核——用确定性的工程体系，驾驭不确定性的模型能力。

Palantir 的自然语言能力之所以能在硬核企业场景真正落地，本质上不是因为模型更聪明，而是因为本体把整个业务世界"翻译"成了 AI 能理解、能操作、能负责的确定性体系，让 AI 的交互真正长在了业务现实之上，而非悬浮在数据库表之上。
