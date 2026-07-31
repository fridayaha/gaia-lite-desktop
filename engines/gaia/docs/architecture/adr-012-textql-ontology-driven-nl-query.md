# ADR-012：本体驱动的自然语言查询（TextQL）—— LLM Tool Use + 确定性召回与 Schema 注入 + SqlGlot 编译器

| 字段 | 内容 |
| ---- | ---- |
| **状态** | 已采纳（Phase 1-2 已实现并端到端验证通过，2026-06-28） |
| **审批日期** | 2026-06-27（设计定稿）；2026-06-28（Phase 1-2 实现完成） |
| **影响层** | 新增 `core/schemas/textql.py`（QueryIR 一等公民）+ `services/textql/` 子包（intent_parser/semantic_recall/schema_injector/schema_provider/sql_compiler/orchestrator/embedding/vector_indexer）；扩展 `layers/index/doris_index_store.py`（语义表 + ANN 索引 + execute_sql）；扩展 `tools/toolsets/object_query.py` + `protocols/mcp_server.py`（第 20 工具 `query_with_sql`，工具总数 19→20）；改造 `routes/ai.py` + `services/ai_agent.py` + `tools/state.py`（确定性召回 + Schema 注入入口）；重构 `services/object_query_service.py`（白名单护栏 + `execute_compiled_sql`）；新增依赖 `sqlglot` + `onnxruntime` + `tokenizers`（ONNX CPU 推理，无 torch 依赖）；Doris BE 配置变更（`be.conf` mem_limit + docker-compose 内存 1g→3g） |
| **相关文档** | [textql-4plus1-views.md](./textql-4plus1-views.md)（4+1 架构视图）、[textql-design.md](./textql-design.md)（完整设计 + 可行性验证证据 + 参考材料）、[adr-009-ontology-tool-layer.md](./adr-009-ontology-tool-layer.md)、[adr-001-doris-as-online-read-source.md](./adr-001-doris-as-online-read-source.md)、[implementation-status.md](./implementation-status.md) |
| **后续 ADR** | Scenario 情景引擎（What-if）、对话状态管理（多轮追问）、治理 Principal + 行级权限（路标 #4）、traverse_link 跨对象 JOIN 将单独成文 |

---

## 背景

### 行业痛点与核心解法

行业普遍痛点（来自参考材料）：

1. 传统 LLM 直出 SQL 幻觉严重、多表 JOIN 准确率低
2. 语义层（LookML/dbt）只能覆盖固定指标，跨系统、多源数据无力
3. 业务人员查数必须依赖分析师，周期数天
4. 企业数仓分散（Snowflake/BigQuery/Redshift + BI + 文档）无统一业务语义映射

核心解法：**自建企业业务本体（Ontology），强约束 LLM 生成 SQL，实现高准确率、可解释、口径统一的自然语言查询。**

### Gaia 现状摸底（2026-06-27）

读完关键代码后确认：**Gaia 已具备 TextQL/Palantir 方案约 80% 的基础设施**，许多机制已落地：

| TextQL/Palantir 机制 | Gaia 现状 | 缺口 |
|------|------|------|
| ① 三元元数据（displayName/apiName/rid） | ✅ 完整（v6，implementation-status §十）。`id`=UUID hex=rid，`api_name`=apiName，`display_name`=displayName，`description`=Text | 无（description 复用作语义检索素材，含多语种别名/同义词，不单建 aliases 字段） |
| ② 本体 Schema（ObjectType/Property/LinkType） | ✅ 完整 ORM + schema + backing_mapping 语义/物理解耦 | — |
| ③ 查询执行（Doris 主 + Trino 降级） | ✅ `ObjectQueryService` 路由完整 | — |
| ④ LLM Tool Use 编排器 | ✅ `ai_agent.py` pydantic-ai Agent + 19 工具 toolset（AG-UI）+ MCP 暴露 | **这就是 Palantir 范式 B 的实现** |
| ⑤ filter/aggregate/topn/exists/count 工具 | ✅ 7 个 object_query 工具 + 标识符白名单校验 + 操作符映射表 | 跨表 JOIN（traverse_link 待实现）；复杂查询（窗口/CTE/算式）无逃生通道 |
| ⑥ Schema 注入 | 🟡 部分。metadata toolset 有 `describe_object_type`/`describe_link_type`，但 LLM 需主动调用 | 缺**确定性自动 Schema 注入**（每次必拉） |
| ⑦ 语义召回双引擎 | 🔴 缺。当前靠 LLM 自己调 `list_object_types` + displayName 精确匹配 | 缺向量检索 + HyDE |
| ⑧ 向量索引载体 | ✅ Doris 4.0.5 原生支持 `ARRAY<FLOAT>` + `USING ANN` 索引（Faiss HNSW/IVF） | 未实际用于语义召回 |
| ⑨ Text2SQL 准确率 | ❌ benchmark 显示 `text_to_sql 0/70`（implementation-status §十 遗留 #6） | **核心痛点** |

**最关键的发现**：Gaia 当前其实走的就是 **Palantir 范式 B（LLM Tool Use 编排）**——`ai_agent.py` 已把 LLM 做成"按钮操作员"，通过 `filter_object`/`aggregate_object` 等工具调用 `ObjectQueryService`，LLM 不直接写 SQL。但 benchmark 显示 `text_to_sql 0/70`，说明**范式对，但召回和 Schema 注入环节没做扎实**，导致 LLM 找不到正确的 ObjectType/Property，工具调用失败。

所以本方案的核心不是"从零搭 TextQL"，而是**补齐召回 + Schema 注入这两个缺失环节，并新增 text2sql 编译器作为复杂查询的逃生通道，让现有 Tool Use 体系真正可用**。

### 关于 TextQL 不开源

TextQL（商业产品）不开源，拿不到其 DSL 编译器、内部实现细节。本方案**不复刻 TextQL，而是把它的设计思想 + Palantir 的公开机制落地到 Gaia 自己的代码里**：DSL 编译器、语义召回引擎、Schema 注入、Tool 体系全部自研。这反而更纯粹，无黑盒依赖。

---

## 决策

### 核心范式：改良的 Palantir 范式 B（LLM Tool Use 编排）

与现有 `ai_agent.py` 一致，不推翻重来。LLM 作为"推理引擎 + 编排器"，通过调用确定性工具执行操作，不直接生成裸 SQL。

### 核心架构决策（三个根本拱择，2026-06-27 定稿）

#### 决策一：QueryIR 作为一等公民

Step 1-3 协同产出一份结构化的"查询意图图"（QueryIR），按本体概念分类（objects/properties/links/filters/group_by/order_by/windows），对标材料「自然语言要素 → SQL 元素 → 本体概念」三列表。IR 是 Step 2-4 多个消费者的共同输入，且持久化作为审计追溯载体。

**为什么不选隐式词袋**：隐式词袋丢失了"要素属于哪类本体概念"的语义，Step 2 召回还得猜每个词是对象还是属性。IR 按本体角色分类抽取，召回变成"按角色到对应本体元素里查"的精确操作，真正兑现材料"降维成在本体业务地图上查找对应对象和属性"的价值。

**可行性验证**（2026-06-27，`scripts/verify_ir_feasibility.py`）：
- IR 表达力：9/9 通过（T1-T9 全场景，含窗口/派生指标/多表/多步）
- LLM 产出稳定性：10/10 通过（真实 DeepSeek API，含 benchmark 真实用例）
- IR 字段设计可直接被 text2sql 编译器（objects/links/properties/filters→SQL）和原子工具（filter_object/aggregate_object 参数）双消费
- 额外收益：IR 持久化天然支持审计追溯、多步查询状态追溯

#### 决策二：多步查询状态由 LLM 自管理（不上 ObjectSet 具名引用）

真实复杂查询常需多步 SQL 协作完成（如"上季度销量前5的商品各自的供应商"=聚合TopN→查商品→查供应商）。多步之间的中间状态传递采用 **LLM 自管理**：LLM 在对话上下文里自己记着中间结果，后续步手填参数。**不引入 ObjectSet 具名引用机制**。

**为什么不选 ObjectSet 具名引用**：ObjectSet 是 Palantir 平台内部概念，提升到 LLM 编排层会让 LLM 理解"先产出 ObjectSet 变量、再引用变量名"这套编程式语义，语义负担太重，背离"自然语言驱动"初衷。等真实出现"中间结果太大 LLM 上下文装不下"的痛点再加，不做预期性抽象。

**多步可审计性不丢**：虽然不上 ObjectSet 引用，但**每步的 IR 持久化**（决策一的价值）。多步查询 = 一串 IR 实例 + 各自的工具调用记录，事后照样能追溯"这个查询分了几步、每步理解成什么、结果是什么"。审计走 IR 持久化，不走 ObjectSet 引用。

**工具层零改动**：现有 19 个工具 + text2sql 工具都收裸数据（primary_keys 列表、filter 字典），不需要引入 ObjectSet 引用类型，工具签名保持简单。

#### 决策三：本次只做查询工具层，Action/Function 不涉及

ADR-012 严格限定在**查询工具层**：Step 1-5（意图解析、召回、Schema 注入、text2sql + 原子工具、查询执行）。

**不涉及**：
- Action 层（已有 `ActionService` 体系，单独层次）
- Functions/Indicator 本体抽象（派生指标 Phase 1-3 先用 text2sql 算式表达，口径统一靠 Schema 注入里写清属性 description 含计算口径说明软约束；Function/Indicator 硬建模留待后续单独 ADR）
- What-if Scenario 引擎（T11，远期单独 ADR）

**边界呼应**：text2sql 编译器只做 SELECT，UPDATE 走现有 Action 工具（见「编译器边界划分」章节）。材料表最后一行的 Action Types/Functions 在本次范围外。

### 完整五步流水线

```
用户自然语言
    ↓
[Step 1] 意图解析（LLM，复用现有 ai_generate）
    ↓ QueryIR（一等公民，按本体概念分类的结构化查询意图图）
[Step 2] 语义召回双引擎（🆕 核心）
  ├─ 引擎A：元数据精确匹配（按 IR 角色在对应本体元素里查：object_refs→ObjectType、property_refs→Property、link_refs→LinkType）
  └─ 引擎B：向量语义检索（Doris ANN，兜底）+ HyDE
    ↓ RecallResult（候选 ObjectType + Property，仅来自本体已定义元素；回填 IR 的 api_name）
[Step 3] 确定性 Schema 注入（🆕 核心）
  自动拉取候选 ObjectType 完整 Schema，注入 LLM 上下文（不等 LLM 主动调 describe）
    ↓ 注入 Schema 块
[Step 4] LLM Tool Use 编排（双路径 + 多步可迭代）
  ├─ 路径A：原子工具（19个，复用现有）—— 单表/标准聚合/TopN
  └─ 路径B：text2sql（🆕 第20工具 query_with_sql）—— 复杂查询逃生通道
  LLM 生成逻辑 SQL（api_name + ObjectType 名）→ SqlGlot 编译器改写 + 三大护栏 → 物理方言 SQL
  多步查询由 LLM 自主调度（在上下文自管理中间状态）；信息不足时主动调 metadata 工具补充召回
    ↓
[Step 5] 确定性查询执行（✅ 复用现有 ObjectQueryService）
  Doris 主 + Trino 降级；出口统一 _map_backing_to_api + display_renderer → 返回 displayName
  每步 IR 持久化（审计追溯载体）
```

### Step 1：意图解析（复用现有，产出 QueryIR 一等公民）

**现状**：`ai_generate.py` 提供 `generateText`/`streamText` 原语。LLM 在 Agent 里已做意图识别。

**核心决策：QueryIR 作为一等公民**（详见下方「核心架构决策」章节）。Step 1 产出的不是扁平词袋，而是**按本体概念分类的结构化查询意图图**，对标材料「自然语言要素 → SQL 元素 → 本体概念」三列表。IR 是 Step 2-4 多个消费者的共同输入，且持久化作为审计追溯载体。

```python
# core/schemas/textql.py
class FilterSpec(BaseModel):
    subject: str          # 筛选主体业务名词「出厂年份」（待 Step2 映射 api_name）
    op: str               # eq/neq/gt/gte/lt/lte/in/notIn/contains/startsWith/between/isNull/isNotNull
    value: Any = None     # between 用 [min,max]；in 用 list；isNull 忽略

class OrderBySpec(BaseModel):
    subject: str; direction: str = "asc"

class PropertyRef(BaseModel):
    name: str             # 属性业务名词「销量」「总金额」
    role: str = "select"  # select|metric|group_key|derived
    expr: str | None = None  # 派生指标算式，仅 role=derived（如 "SUM(a)/COUNT(*)"）

class ObjectRef(BaseModel):
    name: str             # 对象类型业务名词「订单」「货运车辆」
    is_primary: bool = True  # 是否主对象（查询锚点）

class LinkRef(BaseModel):
    from_object: str; to_object: str; link_name: str | None = None

class WindowSpec(BaseModel):
    func: str; partition_by: list[str]; order_by: list[OrderBySpec]; alias: str

class QueryIR(BaseModel):
    """查询意图图（一等公民）：Step1-3 协同产出，Step4 双消费者共消费，持久化审计。"""
    raw_query: str
    intent_type: str      # query|aggregate|topn|count|complex_sql|multi_step
    # 对应 FROM/JOIN（材料表第2行）
    objects: list[ObjectRef]
    links: list[LinkRef]
    # 对应 SELECT（材料表第1行）
    properties: list[PropertyRef]
    # 对应 WHERE/HAVING（材料表第3行）
    filters: list[FilterSpec]
    # 对应 GROUP BY（材料表第4行）
    group_by: list[str]
    # 对应 ORDER BY / LIMIT（材料表第5行）
    order_by: list[OrderBySpec]
    limit: int | None = None; offset: int | None = None
    # 窗口函数（T7/T8）
    windows: list[WindowSpec] = []
    # 派生指标标记（T5，路由到 text2sql 路径）
    has_derived_metric: bool = False
    # 召回未决标记（Step2 召回不全时，留给 LLM 迭代补充）
    needs_recall_refinement: bool = False
```

**实现位置**：`services/textql/intent_parser.py`，调 `ai_generate.generate_text` + pydantic-ai `result_type=QueryIR` 结构化输出。

**关键约束**：
1. **按本体概念分类抽取**：每个要素标注本体角色（objects/properties/links/group_by），Step 2 召回变成「按角色到对应本体元素里查」，而非全文匹配。
2. **不做字段映射**：IR 里只填业务名词（中文），api_name 映射留给 Step 2，避免 LLM 在无本体上下文时瞎猜字段。
3. **派生指标用 expr 表达**：复购率/占比等派生指标用 `role=derived` + `expr` 算式，不新建 Function 本体抽象（边界决策，见「范围边界」）。

**可行性验证证据**（2026-06-27，`scripts/verify_ir_feasibility.py`）：
- IR 表达力：9/9 通过（T1-T9 全场景，含窗口/派生指标/多表/多步）
- LLM 产出稳定性：10/10 通过（真实 DeepSeek API，含 benchmark 真实用例）
- 验证脚本作为 IR schema 的 CI 回归基线（见「后续工作 #1」）

### Step 2：语义召回双引擎（🆕 核心，新建模块）

当前最大缺口，也是 `text_to_sql 0/70` 的根因。

#### 2.1 语义检索素材：复用 description 字段

不单建 aliases 字段。ObjectType/Property/LinkType 的 `description`（现有 ORM 已有 `Text` 字段）承载所有语义检索素材：业务说明 + 多语种别名 + 同义词。

示例：PropertyDef `turnover_rate` 的 `description = "离职率、Turnover Rate、Attrition Rate。衡量员工流失情况的核心HR指标"`。embedding 时整体向量化。

#### 2.2 引擎 A：元数据精确匹配（确定性，零幻觉）

```python
class SemanticRecaller:
    async def recall(self, ontology: str, ir: QueryIR) -> RecallResult:
        # 引擎A优先：按 IR 角色在对应本体元素里精确匹配
        candidates = await self._exact_match(ontology, ir)
        if candidates.confidence >= CONFIDENCE_THRESHOLD:
            return candidates
        vector_hits = await self._vector_search(ontology, ir)  # 引擎B兜底
        return self._merge_and_rerank(candidates, vector_hits)
```

精确匹配逻辑：按 IR 的本体角色分流召回——`objects`→ObjectType 的 displayName/description、`properties`→Property 的 displayName/description、`links`→LinkType 的 displayName/description。完全匹配=高置信度，包含匹配=中置信度。输出 `RecallResult`（含候选 ObjectType + Property + **回填到 IR 的 api_name**）。

#### 2.3 引擎 B：向量语义检索（Doris ANN，兜底）

**关键决策：向量索引放 Doris**（与 ADR-001 一致，Doris 是在线读主源；CLAUDE.md 无 Redis 红线不影响，Doris 自带 ANN 能力）。**不引入独立向量库。**

**模型选型**：`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- 384 维，多语种（覆盖中文 + 英文 apiName/description）
- CPU 友好，无 GPU 依赖，轻量部署
- Doris 4.x 原生支持该维度的 ANN 索引

**Doris 向量表设计**（语义召回专用，非对象数据表）：

```sql
CREATE TABLE idx_ontology_semantic (
  ontology_api_name VARCHAR,
  element_type VARCHAR,        -- OBJECT_TYPE | PROPERTY | LINK_TYPE
  element_api_name VARCHAR,
  display_name VARCHAR,
  description VARCHAR,
  embedding ARRAY<FLOAT>,      -- 384 维（MiniLM-L12-v2）
  INDEX idx_vec (embedding) USING ANN PROPERTIES(
    "index_type" = "hnsw",
    "metric_type" = "inner_product",  -- cosine 归一化后用 inner_product
    "dim" = "384",
    "quantizer" = "flat"
  )
) UNIQUE KEY(ontology_api_name, element_type, element_api_name)
  DISTRIBUTED BY HASH(ontology_api_name);
```

**关键 Doris 4.x VECTOR 语法要点**（经文档核实）：
- 向量存 `ARRAY<FLOAT>`，不引入新数据类型
- ANN 索引 `USING ANN PROPERTIES("index_type"="hnsw"|"ivf"|"ivf_on_disk", "metric_type"="l2_distance"|"inner_product", "dim"=N)`
- **cosine 不直接支持**：向量 L2 归一化后用 `inner_product`，查询用 `inner_product_approximate(col, [vec])` + `ORDER BY ... DESC`
- `l2_distance` 用 `l2_distance_approximate` + `ORDER BY ... ASC`
- 现有 `doris_index_store.py` L191 用的是旧 `USING VECTOR` 语法，需改为 Doris 4.x 的 `USING ANN` 语法

**查询示例**（cosine 语义召回）：
```sql
SELECT element_type, element_api_name, display_name,
       inner_product_approximate(embedding, ?) AS similarity
FROM idx_ontology_semantic
WHERE ontology_api_name = ?
ORDER BY similarity DESC
LIMIT 10;
-- params: [<384维查询向量>, <ontology_api_name>]
```

#### 2.4 向量化流水线

本体定义/更新时，把 displayName + description 拼接后调 embedding API（MiniLM-L12-v2），写入 `idx_ontology_semantic` 表。挂到 `OntologyService.define/update` 的现有 `IndexSyncService` 钩子上——**复用触发机制，不新引入 SeaTunnel pipeline**（元数据级，量小，直接 PG → embedding API → Doris）。

#### 2.5 HyDE 增强（Phase 2，可选）

```python
async def _hyde_search(self, intent: QueryIntent) -> list[VectorHit]:
    # 1. LLM 生成假设性答案片段（弥合 query/document 语义鸿沟）
    hypothetical = await ai_generate.generate_text(
        instructions="基于这个查询，生成一段假设性的本体描述",
        prompt=intent.raw_query)
    # 2. 对假设性答案做 embedding 检索
    return await self._vector_search_raw(hypothetical)
```

Palantir 支持四种检索方法：Keyword Search（BM25）、Vector Cosine、Augmented Keyword Search（LLM 生成同义词）、HyDE（LLM 生成假设性答案后向量检索）。Phase 1 实现引擎A（精确匹配≈Keyword）+ 引擎B（Vector Cosine），Phase 2 加 HyDE + Rank Fusion。

#### 2.6 召回护栏

召回结果**只能来自本体已定义元素**（向量检索范围限定在 `idx_ontology_semantic` 表，不查外部），杜绝幻觉。

### Step 3：确定性 Schema 注入（🆕 核心，新建模块）

材料反复强调的"用本体驯化 LLM"的关键。当前 `describe_object_type` 是 LLM **主动**调用，不可靠；改为**系统自动注入**。

#### 3.1 实现：动态 system prompt 构造器

不修改 `ai_agent.py` 的 Agent 结构，在 `/ai/agent` route 入口加一层动态 Schema 注入（pydantic-ai 支持动态 system prompt）。**每条用户消息前插入一次召回+注入**（对标材料"确定性检索上下文：每次用户消息都运行"）。

```python
class SchemaInjector:
    async def build_context_block(self, ontology: str, recall: RecallResult) -> str:
        blocks = []
        for cand in recall.object_types[:MAX_INJECT_OBJECTS]:  # 限制 1-25 个
            ot = await self._meta.get_object_type(ontology, cand.api_name)
            blocks.append(self._render_object_type_schema(ot))
        return "\n\n---\n\n".join(blocks)

    def _render_object_type_schema(self, ot: ObjectType) -> str:
        # 渲染六大类信息：基础信息 + Properties + LinkType + 数据类型约束 + 业务约束 + 关系
        lines = [
            f"## ObjectType: {ot.api_name} (displayName: {ot.display_name})",
            f"description: {ot.description}",
            f"primary_key: {ot.primary_key}  title_property: {ot.title_property}",
            "### Properties (只能用以下字段，禁止编造):",
        ]
        for p in ot.properties or []:
            lines.append(f"- {p.api_name} ({p.data_type}, displayName={p.display_name}"
                         f"{', required' if not p.nullable else ''}"
                         f"{', PK' if p.is_primary_key else ''})  # {p.description}")
        # LinkType（关系约束）
        for lt in await self._meta.list_link_types(ot.ontology_id):
            if lt.source_object_type_id == ot.id or lt.target_object_type_id == ot.id:
                lines.append(f"- Link: {lt.api_name} → {lt.display_name} ({lt.cardinality})")
        return "\n".join(lines)
```

#### 3.2 三大护栏的落地映射

| Palantir 护栏 | Gaia 实现位置 | 状态 |
|------|------|------|
| 实体约束（只能查已定义 ObjectType） | Schema 注入只给候选 OT + 工具签名要求 `object_type` 必填 + `_validate_identifier` | ✅ 已有校验 + 🆕 注入强化 |
| 字段约束（只能用已定义 Property） | `_filter_dict_to_sql` 的 `_validate_identifier`（regex）→ **改为 `ot.properties` 白名单**（implementation-status 路标 #2） | 🟡 待重构（本方案推动） |
| 关系约束（只能走已定义 LinkType） | `traverse_link` 工具（骨架已注册）+ Schema 注入列出 LinkType + **text2sql 编译器 JOIN 校验** | 🟡 待 LinkTraversalService + 编译器 |

**本方案直接推动路标 #2 完成**：把 `_validate_identifier` 从 regex 改为属性白名单。这恰好是材料"字段约束"护栏的实现，也是 `text_to_sql 0/70` 的另一个根因（LLM 瞎编字段名，regex 放行了，查询报错）。

### Step 4：LLM Tool Use 编排（双路径）

#### 路径 A：原子工具（19个，复用现有，零改动）

覆盖：`filter_object`/`aggregate_object`/`topn_object`/`count_object`/`exists_object`/`get_object`/`bulk_get_object`（均已删除，统一收敛到 `query_with_sql`）+ metadata 工具 + write/action 工具。

#### 路径 B：text2sql（🆕 第20工具 `query_with_sql`）

**为什么需要**：单纯 19 个原子工具不够。工具层是"预定义查询模板"，LLM 只能在模板参数里填值。真实业务查询有大量工具模板覆盖不到的复杂 SQL：

| 查询类型 | 现有工具能否覆盖 |
|---------|----------------|
| 单表过滤/聚合/TopN | ✅ |
| **多表 JOIN + 自定义投影** | ❌ traverse_link 只返回对象集 |
| **子查询/CTE/窗口函数** | ❌ 完全无法表达 |
| **跨 ObjectType 自定义算式** `amount * 0.8` | ❌ 工具只支持固定聚合 |
| **UNION/复杂 HAVING/占比计算** | ❌ |

这些是 Palantir 用 **Ontology SQL 工具**解决的（材料提到"AIP Logic 提供 Ontology SQL 工具，对 ObjectSet 执行 SQL 查询"）——即本方案要补的 text2sql 能力。

#### text2sql 与 ObjectSet 的结合方式（核心设计决策）

**采用方案 2：ObjectSet 作逻辑视图，text2sql 编译到物理表。**

ObjectSet 在 text2sql 路径里的角色 = "逻辑视图定义"，具体三处结合：

**(a) ObjectSet 提供"逻辑表名"**：text2sql 的 LLM 写 `FROM Order`，这个 `Order` 就是 ObjectSet 的 `object_type_api_name`。编译器靠它定位物理表 + 拉取 Schema 做护栏校验。没有 ObjectSet 的对象类型定义，text2sql 无从编译。

**(b) ObjectSet 的 filter 是 text2sql WHERE 子句的"权限 + 语义"基础**：复杂 SQL 执行时，编译器在 WHERE 里自动注入 ObjectSet 已有的权限谓词（行级权限，路标 #4）。例如 LLM 写 `SELECT ... FROM Order`，编译器补成 `SELECT ... FROM idx__order WHERE __perm_user_id = 'u1'`。text2sql 天然继承 ObjectSet 的安全边界，不会绕权限。

**(c) 两条路径共享同一个 ObjectSet 出口映射**：无论原子工具还是 text2sql，最终都过 `_map_backing_to_api`（物理列→api_name）+ `display_renderer`（api_name→displayName）。保证业务用户看到的人话一致。

```python
# tools/toolsets/object_query.py 新增第20工具
@ts.tool
async def query_with_sql(
    ctx: RunContext[AppState],
    ontology: str = "",        # 本体 api_name（AG-UI 可省略，默认 Web UI 当前本体）
    sql: str = "",            # 逻辑 SQL（api_name + ObjectType 名）
) -> dict[str, Any]:
    """对本体对象执行自定义 SQL 查询（复杂查询逃生通道）。

    用于复杂查询：多表 JOIN（需走
    已定义 LinkType）、子查询、窗口函数、自定义算式等。

    sql 使用 ObjectType api_name 当表名、Property api_name 当列名。例如：
    SELECT o.orderNo, c.name
    FROM Order o JOIN Customer c ON o.customerId = c.customerId
    WHERE c.region = 'EAST'
    JOIN 的表对必须在本体 LinkType 中已定义，否则报 INVALID_JOIN。

    所有表名/列名编译时校验为本体已定义元素；值参数化绑定防注入。
    """
```

> **设计决策 C（2026-07 修订）**：早期版本签名含 `object_type: str` 参数
> （“主 ObjectType，Schema 锚点 + 权限”）。该参数在多表 JOIN 场景下语义
> 不成立——`SELECT a.p1, b.p2 FROM A JOIN B` 没有唯一“主对象”，填谁都有
> 权限/路由/列名回映的漏洞（只校验一个 OT 的读权限、只看一个 OT 的
> storage_type 路由、列名回映只用一个 OT 的映射表）。修订后删除该参数，
> 编译器通过 `involved_object_types(sql)` 从 SQL 推断所有引用的 OT，对
> 每一个 OT 统一做权限校验 + 存储路由 + 列名回映。原则：“把复杂留给
> 自己，把简单留给客户”——SQL 里已写明所有表，不要求调用方重复、可能
> 出错地提供。

#### OntologySqlCompiler 编译器（核心新模块）

```python
# services/textql/sql_compiler.py
class OntologySqlCompiler:
    """把 LLM 生成的逻辑 SQL 编译成 Doris 或 Trino 物理方言 SQL。

    三大护栏在编译期强制：
    - 表名必须 = 本体已定义 ObjectType
    - 列名必须 = 该 ObjectType 已定义 Property
    - JOIN 的表对必须 = 本体已定义 LinkType
    """

    def compile(self, logical_sql: str,
                dialect: Literal["doris","trino"]) -> tuple[str, list[Any]]:
        self.params = []
        ast = sqlglot.parse_one(logical_sql, read="mysql")  # LLM 写类 MySQL 语法
        self._enforce_scope(ast)        # 拒绝 UPDATE/INSERT/UNION 等
        self._pass1_collect(ast)        # alias→ObjectType + CTE + 子查询输出列
        self._expand_stars(ast)         # SELECT * 展开为显式列（冲突加 OT 前缀）
        ast = self._rewrite(ast, dialect)  # 表/列/JOIN 白名单 + 方言感知物理名 + 字面量参数化
        return ast.sql(dialect=dialect), self.params

    def involved_object_types(self, logical_sql: str) -> list[str]:
        """从 SQL 推断所有引用的 ObjectType（去重）。设计决策 C 的核心：
        权限/路由/回映不再依赖调用方传入的单个 `object_type` 锡点。"""
```

**关键技术要点（三轮可行性验证得出）**：
- **两遍遍历**：Pass 1 收集 alias→ObjectType 映射 + CTE 定义 + 子查询输出列；Pass 2 改写。否则列归属解析失败。
- **SqlGlot 30.x 的 args key 是 `from_`/`with_` 不是 `from`/`with`**（调试坑）。
- **列归属解析**：alias 前缀 → alias_map；CTE/子查询别名 → 输出列集合（信任内层已校验）；无前缀 → 单表 fallback（多表歧义时报错让 LLM 加前缀）。
- **递归改写时 Table.name 会从 `Order` 变成 `idx_auto__order`**，列解析要兼容两种形态（物理名反查，含 Doris 名与 Trino `iceberg.ontology.*` 名两套）。
- **方言感知物理名（2026-07）**：MANAGED 表在 Doris 方言下编译为 `idx_<ont>__<type>`（Doris 索引表），在 Trino 方言下编译为 `iceberg.ontology.<snake_type>`（Iceberg 表，经 `iceberg` catalog 可见）；VIRTUAL 表两方言都用外部 `<catalog>.<schema>.<table>` 三段式。这是 Trino 跨 catalog 联邦 JOIN 的前提——MANAGED 与 VIRTUAL 可在一条 Trino SQL 内 JOIN。
- **表别名补偿（2026-07）**：表改写为物理名后，无显式 alias 的表会自动设 `AS <逻辑 OT 名>`，使 `Order.amount` 这类列前缀仍可解析（否则前缀 dangling 报 COLUMN_NOT_FOUND）。
- **`SELECT *` 展开（2026-07）**：编译期把顶层 `SELECT *` 展开为显式列。多表 JOIN 时同 api_name 属性冲突（如两表都有 `id`）加 OT 前缀消歧（`<OT>_<api>`），避免 DB 层同名列冲突静默丢数据。`COUNT(*)` 内的 Star 不展开；CTE/子查询上的 `*` 不展开（信任内层）。
- **参数化绑定**：字面量抽到 `?` 占位 + params 列表，Doris/Trino 都支持。替代现有 `_sql_literal` 手写转义。
- **UPDATE/INSERT 不处理**：走现有 Action 工具路径（见边界划分）。

### Step 5：查询执行（2026-07 修订：路由增强）

`ObjectQueryService.execute_compiled_sql` 路由（设计决策 C 后不再接收
`object_type` 参数，从 SQL 推断所有 OT）：
- **全 MANAGED** → Doris 主路径，失败降级 Trino（方言感知编译，Trino 方言下 MANAGED 表 = `iceberg.ontology.<snake>`）
- **含 VIRTUAL** → Trino 跨 catalog 联邦 JOIN（MANAGED 的 `iceberg.ontology.*` + VIRTUAL 的外部 `<catalog>.*` 在一条 Trino SQL 内 JOIN）。2026-07 修订前错误报 `MIXED_STORAGE_JOIN` 拒绝，现修正：Trino 本就支持跨 catalog 联邦。
- **权限校验**：对推断出的每个 OT 逐个 `check_access(read)`，任一无权限 → `ForbiddenError`（旧实现只校验单个“锡点” OT，JOIN 进来的表裸奔）
- **列名回映**：`_map_backing_to_api_multi` 合并所有参与 OT 的物理列→api_name 映射；冲突列名（同物理列映不同 api_name）保留物理名不误映
text2sql 编译产物走 `execute_compiled_sql`。Doris 失败时重新编译成 Trino 方言降级。

---

## 编译器边界划分（重要设计决策）

经三轮可行性验证，明确 text2sql 编译器**只做 SELECT**，以下场景不进编译器：

| 场景类型 | 处理方式 | 理由 |
|---------|---------|------|
| **查询类（T1-T9）** | text2sql 编译器 ✅ | 单 SQL 可表达 |
| 多步推理（T10） | 工具链编排（非编译器） | 需多次查询 + LLM 推理，不是单 SQL |
| What-if 情景模拟（T11） | Scenario 引擎（非编译器） | 参数化重算，非查询 |
| Action 回写（T12） | **现有 Action 工具**（非编译器） | 已有 ActionService + OCC + 审计，不该绕过。材料场景六明确"基于 Action 操作与回写能力" |
| 审计溯源（T13） | 查日志表（编译器 + 审计本体） | 属查询类，但需先建审计 ObjectType |
| 多轮追问（T14） | 对话状态管理（非编译器） | 复用前序 ObjectSet |

**UPDATE 不进编译器的依据**：材料场景六（业务操作执行）"把订单交付日期延后"在 Palantir 里是 Action 工具，不是 SQL。Gaia 已有完整 Action 体系（ActionService + ActionType + OCC + 审计 + Outbox），text2sql 若处理 UPDATE 会绕过这套治理，违背架构红线。

---

## 技术可行性验证证据（四轮，2026-06-27）

验证脚本保留于 `scripts/verify_sqlglot_feasibility_v{1,2,3}.py`，作为编译器技术预研原型与可行性证据（脚本内联独立原型编译器，与生产实现分离；不参与 CI 回归，生产回归由 `tests/unit/` 覆盖）。

### v4：IR 一等公民可行性（9+10 通过）

验证 IR 作为一等公民的两个前提：表达力 + LLM 产出稳定性。

**IR 表达力 9/9**：手工构造 T1-T9 全场景的 IR，验证字段齐全（objects/properties/links/filters/group_by/order_by/windows/derived），含窗口函数、派生指标、多表关联、多步查询。

**LLM 产出稳定性 10/10**：真实 DeepSeek API，10 个自然语言问句（含 benchmark 真实用例）全部稳定产出结构化 IR。LLM 正确分类意图（query/aggregate/topn/complex_sql/multi_step）、正确识别对象与派生指标、subject/name 填中文业务名词（非字段名）。

**结论**：IR 作为一等公民技术可行。字段设计可直接被 text2sql 编译器和原子工具双消费；持久化天然支持审计追溯。

### v1-v3：SqlGlot 编译器可行性

### 验证环境
- sqlglot 30.12.0
- 模拟本体 Schema（车企全链路：Order/Vehicle/Part/Supplier/Customer/Claim/Defect 等 9 个 ObjectType）
- Doris 4.x + Trino 双方言编译

### v1（基础，12 场景）：6/12 通过
覆盖：单表/JOIN/子查询/窗口/算式/聚合/护栏拦截。暴露难点：列归属解析、别名列校验、SQL 注入误报。

### v2（修正列归属，15 场景）：14/15 通过
修复：两遍遍历 + 物理名反查 + `from_`/`with_` key。覆盖：+ 自连接/嵌套子查询/CASE/SQL注入拦截。唯一未通过：CTE（工程量问题）。

### v3（车企全链路，13 场景）：10/13 通过
覆盖真实业务 SQL 类型：4-5表JOIN穿透、占比计算（聚合相除）、同比环比 SELF JOIN、窗口函数占比、时间序列趋势、多维聚合。

**通过的产出 SQL 示例**（5表全链路，车企核心场景）：
```sql
-- 逻辑 SQL（LLM 生成，api_name + ObjectType 名）
SELECT o.orderId, c.customerName, v.vin, pp.planDate, cl.faultCode
FROM Order o JOIN Customer c ON o.customerId = c.customerId
JOIN Vehicle v ON o.vehicleId = v.vehicleId
JOIN ProductionPlan pp ON v.vehicleId = pp.vehicleId
JOIN Claim cl ON v.vehicleId = cl.vehicleId
WHERE o.status = 'DELIVERED'

-- 编译产物（Doris 方言，物理表名 + 物理列名 + 参数化）
SELECT o.order_id, c.customer_name, v.vin, pp.plan_date, cl.fault_code
FROM idx_auto__order AS o JOIN idx_auto__customer AS c ON o.customer_id = c.customer_id
JOIN idx_auto__vehicle AS v ON o.vehicle_id = v.vehicle_id
JOIN idx_auto__production_plan AS pp ON v.vehicle_id = pp.vehicle_id
JOIN idx_auto__claim AS cl ON v.vehicle_id = cl.vehicle_id
WHERE o.`status` = ?
-- params: ['DELIVERED']
```

### 剩余 3 个失败的定性

| 场景 | 性质 | 处置 |
|------|------|------|
| T12a/T12b UPDATE 回写 | **设计边界**，非缺陷 | 不进编译器，走 Action 工具（见边界划分） |
| T4 三维拆解多表同名列歧义 | LLM 生成质量约束 | prompt 引导 + 友好报错（多表同名列提示加前缀） |

### SQL 类型覆盖矩阵（基于材料第二十/二十一轮归纳）

| 类型 | SQL 特征 | 材料典型问句 | 编译器覆盖 |
|------|---------|------------|-----------|
| T1 单表过滤检索 | WHERE+排序+分页 | "2025Q2下单的华东区企业客户" | ✅ v1 |
| T2 跨实体 JOIN | 多表 JOIN via LinkType | "逾期订单对应的客户负责人" | ✅ v1 |
| T3 多层关联穿透 | 3+表链式 JOIN | "整车→总成→零件→供应商" | ✅ v3（4-5表） |
| T4 多维聚合 | GROUP BY 多维+SUM/COUNT | "每区域销售额分别多少" | ✅ v3 |
| T5 占比/比率计算 | 聚合相除 SUM(a)/SUM(b) | "VIP客户占比""复购率" | ✅ v3 |
| T6 同比/环比对比 | SELF JOIN 跨期 | "同比去年变化多少" | ✅ v3 |
| T7 TopN+占比 | ORDER BY+LIMIT+窗口占比 | "Top10回款客户及占比" | ✅ v3 |
| T8 排名/分位 | ROW_NUMBER/RANK OVER | "各产线周转天数排序" | ✅ v2 |
| T9 时间序列趋势 | 按时间分组+排序 | "每月故障率变化趋势" | ✅ v3 |
| T10 异常根因多步推理 | 多次查询+LLM编排 | "延迟率上升原因" | ⚠️ 工具链编排（非编译器） |
| T11 What-if 情景模拟 | 参数化重算 | "如果涨价10%利润降多少" | ⚠️ Scenario 引擎（非编译器） |
| T12 Action 回写 | UPDATE/INSERT | "把订单交付日期延后" | ⚠️ Action 工具（非编译器） |
| T13 审计溯源 | 查日志表 | "订单修改记录和操作人" | 🟡 需审计本体建模 |
| T14 多轮上下文追问 | 复用前序 ObjectSet | "其中VIP占比多少" | ⚠️ 对话状态管理（非编译器） |
| T15 全链路端到端 | 6+表 JOIN 跨域 | "下单→排产→生产→物流→交付→售后" | ✅ v3（5表验证通过） |

---

## 模块划分与文件落点

```
src/ontology/
├── core/schemas/
│   └── textql.py                    # 🆕 QueryIntent / RecallResult / CandidateObjectType
├── services/textql/                 # 🆕 TextQL 子包
│   ├── __init__.py
│   ├── intent_parser.py             # Step 1
│   ├── semantic_recall.py           # Step 2 双引擎
│   ├── vector_indexer.py            # 🆕 向量化流水线（define/update 钩子）
│   ├── schema_injector.py           # Step 3
│   ├── sql_compiler.py              # 🆕 Step 4 路径B SqlGlot 编译器
│   └── display_renderer.py          # Step 4/5 displayName 渲染
├── layers/index/doris_index_store.py # ✏️ 新增 create_semantic_table / vector_search / execute_sql；USING VECTOR → USING ANN
├── services/ontology_service.py     # ✏️ define/update 钩子触发向量化
├── services/object_query_service.py # ✏️ _validate_identifier 改白名单（路标 #2）+ 新增 execute_compiled_sql
├── tools/toolsets/object_query.py   # ✏️ 新增第20工具 query_with_sql
├── tools/state.py                   # ✏️ AppState 加 injected_schema 字段
├── routes/ai.py                     # ✏️ agent 入口插入 Step 1-3
└── config/container.py              # ✏️ 注入 TextQL services
```

**新增依赖**：
- `sqlglot>=30.0`（SQL AST 解析 + 跨方言 transpile，已验证 30.12.0 可用）
- `sentence-transformers`（MiniLM-L12-v2 模型推理，CPU 部署）
- 模型权重 `paraphrase-multilingual-MiniLM-L12-v2`（384 维，多语种）

---

## 分阶段实施计划与范围边界（固化，防遗忘）

> ⚠️ 本节为设计定稿的范围承诺，各 Phase 不得超范围。超范围需重新评审。

### Phase 1（MVP，约 1 周）：精确匹配 + Schema 注入 + 白名单 + 半托管 text2sql

**目标**：把 `text_to_sql 0/70` 拉起来到 ≥30/70；单表 + 简单 JOIN 查询跑通。

**范围**：
- [ ] `description` 复用为语义检索素材（无表结构变更）
- [ ] Step 2 引擎 A：元数据精确匹配（displayName + description 字面匹配）
- [ ] Step 3 Schema 注入器：确定性自动拉取候选 OT 完整 Schema
- [ ] `_validate_identifier` 改 `ot.properties` 白名单（路标 #2，穿透 ObjectType）
- [ ] Step 4 路径 B text2sql 编译器，**支持范围**：
  - 单表过滤 + 排序 + 分页（T1）
  - 多表 JOIN ≤5 表，走 LinkType 校验（T2/T3/T15）
  - 子查询（T3 的非 CTE 形式）
  - 多维聚合 + GROUP BY + HAVING（T4）
  - 窗口函数 ROW_NUMBER/RANK OVER（T8）
  - 自定义算式 `amount * 0.8`（T5 简单形式）
  - 占比计算 聚合相除（T5）
  - 时间函数 DATE_FORMAT/YEAR/MONTH 分组（T9）
  - 三大护栏：非法表/列/JOIN 拦截
  - 参数化绑定（字面量 → `?` 占位）
- [ ] 第 20 工具 `query_with_sql` 接线
- [ ] agent 入口插入 Step 1-3

**text2sql 编译器 Phase 1 明确不支持**（识别到即拒绝，降级到原子工具链式调用）：
  - ~~CTE（WITH 子句）~~ ✅ Phase 2 已支持（2026-06-27）
  - 复杂自连接（同表多 alias 的深层嵌套）
  - 同比环比 SELF JOIN 跨期（T6）—— Phase 2（v3 子查询形式已验证）
  - 窗口函数占比组合（T7）—— Phase 2（v3 已验证）
  - UNION/INTERSECT/EXCEPT

**验收**：`text_to_sql` benchmark ≥30/70；v1+v2 验证脚本全过；新增 Phase 1 范围用例全过。

### Phase 2（约 1 周）：向量召回 + 编译器长尾补全

**目标**：`text_to_sql ≥50/70`；口语化表达（"大车"）能命中。

**范围**：
- [ ] Step 2 引擎 B：Doris ANN 向量检索（MiniLM-L12-v2 + 384 维 HNSW）
- [ ] 向量化流水线（define/update 钩子）
- [ ] HyDE + Rank Fusion
- [x] 编译器补全：CTE（✅ 2026-06-27，6 单测）；同比环比 SELF JOIN（T6，v3 已验证）；窗口函数占比（T7，v3 已验证）；UNION（仍 Phase 3+）
- [ ] displayName 渲染（display_renderer）
- [ ] Doris/Trino 方言函数兼容性测试矩阵

**验收**：`text_to_sql ≥50/70`；v3 验证脚本全过（含 CTE 场景7）。

### Phase 3（约 1 周）：跨对象 + 多轮 + 治理

**目标**：`text_to_sql ≥60/70`；跨对象查询跑通。

**范围**：
- [ ] `traverse_link` 工具实现（LinkTraversalService，跨对象 JOIN）
- [ ] 多轮对话状态管理（T14，复用前序 ObjectSet）
- [ ] 行级权限注入（路标 #4，text2sql WHERE 自动补权限谓词）
- [ ] 审计 ObjectType 建模（T13）

**验收**：`text_to_sql ≥60/70`；跨对象查询端到端跑通。

### Phase 4（持续）：性能优化 + 监控

**范围**：
- [ ] aggregate 走 Doris（当前降级 Trino）
- [ ] 评估集扩充 + 效果监控 dashboard
- [ ] P95 延迟达标

---

## 风险评估

| 风险 | 等级 | 缓解 |
|------|------|------|
| 列归属解析在极端场景误判 | 中 | 大量测试用例（v1/v2/v3 基线）+ 编译失败时降级到原子工具路径 |
| LLM 生成语法 SqlGlot 解析失败 | 中 | parse 失败直接返回错误让 LLM 重试；SQL 特性白名单兜底 |
| CTE/窗口函数等长尾覆盖不全 | 低 | Phase 1 不支持，复杂查询降级到原子工具链式调用 |
| Doris/Trino 方言差异导致运行时错误 | 中 | 编译产物先在目标库 EXPLAIN 验证再执行；方言函数测试矩阵 |
| MiniLM-L12-v2 中文召回质量 | 中 | Phase 2 评估，必要时换 bge-m3 等中文优化模型 |
| Doris ANN 索引调优（HNSW 参数） | 中 | 参考官方 practical-guide，按数据量调 max_degree/ef_construction |

---

## 后续工作（路标）

| # | 项 | 阶段 | 说明 |
|---|----|------|------|
| 1 | 编译器回归测试纳入 CI | Phase 1 | v1/v2/v3 验证脚本作为 CI 硬门槛，每个支持的 SQL 特性都有用例，防 SqlGlot 升级或本体 Schema 变化导致回归 |
| 2 | Doris 4.x ANN 语法迁移 | Phase 2 | `doris_index_store.py` L191 旧 `USING VECTOR` 语法 → `USING ANN PROPERTIES(...)`，对齐 Doris 4.x |
| 3 | SQL 特性白名单 | Phase 1 | 约束 LLM 只用支持的语法，超出白名单编译期拒绝（替代自研 DSL） |
| 4 | 方言函数兼容矩阵 | Phase 2 | DATE_FORMAT/YEAR/MONTH 等在 Doris vs Trino 的差异测试 |
| 5 | 行级权限注入 | Phase 3 | text2sql WHERE 自动补权限谓词（路标 #4 协同） |
| 6 | Scenario 情景引擎 | 远期 | T11 What-if，单独 ADR |
| 7 | 对话状态管理 | Phase 3 | T14 多轮追问，复用前序 ObjectSet |

---

## 参考材料索引

> 以下材料为本 ADR 设计依据，完整内容见 [textql-design.md](./textql-design.md) §附录。

### 材料一：TextQL 四步流水线（商业产品，不开源，仅取方法论）
- 核心定位：以业务本体为底层骨架，结构化可管控分步流程产出稳定可解释 SQL
- 四步：搭建本体 → NLP 提取关键属性 → 属性与本体语义映射 → DSL 编译生成 SQL
- 类比：图书馆杜威分类法
- 技术栈：图数据库+关系库、向量嵌入、自研 DSL 编译器 + SQLGlot、GPT-4/Gemini 仅做意图抽取

### 材料二：Palantir 七层机制（公开描述，取架构蓝本）
- 核心思想：LLM 作为推理引擎和编排器，调用由本体驱动的确定性工具
- 七层：输入理解意图解析 → 多级语义检索匹配 → Schema 注入上下文构建 → Tool Use 可执行查询生成
- 三大约束护栏：实体约束、字段约束、关系约束
- 三元元数据体系：displayName（人）/ apiName（机器）/ rid（系统）
- 双引擎召回：精确匹配 + 向量检索（Keyword/Vector/Augmented/HyDE + Rank Fusion）
- OAG（本体增强生成）：RAG 进阶版，LLM 锚定在数据/逻辑/行动三位一体的企业运营现实

### 材料三：三元元数据协同机制
- displayName：业务可读，无需唯一，支持多语言，语义匹配第一入口
- apiName：机器可执行，PascalCase/camelCase，必须唯一，生成可执行查询
- rid：系统自动生成，全局唯一，权限校验/数据路由/缓存定位/错误定位
- 中文场景适配：displayName 优先匹配中文，apiName 必须规范设置英文标识

### 材料四：Schema 注入机制
- 六大类注入内容：ObjectType 基础信息 + Properties + LinkType + 数据源映射 + 权限元数据 + 类型类
- 两步走：Object Type Search（粗筛）→ Object Type Lookup（精查）
- 确定性检索上下文：每次用户消息都运行，可预测/可审计/可配置

### 材料五：Tool Use 机制
- LLM 降级为按钮操作员，不写 SQL 不计算，选择并调用预定义工具
- 三类工具：Data（Query Objects/Ontology Aggregation/Ontology SQL）、Logic、Action
- ObjectSet：指向数据逻辑的视图，非真实数据拷贝

### 材料六：落地场景全景（8 大通用场景）
1. 基础业务对象检索（T1）
2. 跨实体关联探查（T2/T3）
3. 多维聚合分析（T4/T5）
4. 异常根因排查（T10，多步推理）
5. 情景模拟推演（T11，What-if）
6. 业务操作执行（T12，Action）
7. 合规溯源审计（T13）
8. 连续对话追问（T14，多轮）

### 材料七：车企制造领域场景（6 大业务域）
- 研发工程域（BOM/试验/变更）、生产制造域（数字孪生/质量根因/预测维护/排程模拟）、采购供应链域（风险穿透/缺料模拟/库存物流）、销售渠道域（订单全链路/库存健康/销量拆解）、营销用户运营域（偏好分析/线索转化）、售后质量域（故障识别/全链路追溯/召回/备件）
- 跨域全链路端到端穿透（T15，车企专属核心价值）

---

## 实现状态（Phase 1-2 已完成，2026-06-28）

> 本章节记录 Phase 1-2 真实落地的关键信息，作为运维/续开发的参考。架构视图见 [textql-4plus1-views.md](./textql-4plus1-views.md)（逻辑/进程/开发/物理/场景 4+1 视图），完整组件状态见 [implementation-status.md](./implementation-status.md) §「TextQL Phase 1-2 实现状态」。

### 已实现组件（8 模块 + 3 改造，115 单测 + 5 E2E）

| 组件 | 文件 | 测试 | 说明 |
|------|------|------|------|
| IR schema（一等公民） | `core/schemas/textql.py` | 24 | QueryIR + RecallResult + FilterSpec/PropertyRef/ObjectRef/LinkRef/WindowSpec |
| Step 1 意图解析 | `services/textql/intent_parser.py` | LLM 10/10 | pydantic-ai `result_type=QueryIR` 结构化输出 |
| Step 2 引擎A+B 召回 | `services/textql/semantic_recall.py` | 17 | 引擎A 精确匹配 + 引擎B 向量兜底（低置信触发，async recall） |
| Step 2 引擎B 推理 | `services/textql/embedding.py` | 7 | OnnxEmbeddingProvider（ONNX CPU，详见下文） |
| Step 2 向量化流水线 | `services/textql/vector_indexer.py` | 2 | define/update 钩子把本体元素 embedding 写入 Doris 语义表 |
| Step 3 Schema 注入 | `services/textql/schema_injector.py` | 7 | 确定性注入，6 类信息，MAX_INJECT=8 |
| Step 1-3 串联 | `services/textql/orchestrator.py` | 7 | agent 入口自动接线，引擎B 按需启用（非致命） |
| Schema Provider | `services/textql/schema_provider.py` | — | MetaStoreSchemaProvider 从 PG 加载本体给编译器 |
| Step 4 编译器 | `services/textql/sql_compiler.py` | 32 | SqlGlot + 三大护栏 + CTE 支持（Phase 2） |
| ObjectQueryService 扩展 | `services/object_query_service.py` | 21 | `execute_compiled_sql` + 白名单护栏（路标 #2） |
| DorisIndexStore 扩展 | `layers/index/doris_index_store.py` | — | 语义表 + ANN 索引 + execute_sql |
| 第20工具 query_with_sql | `tools/toolsets/object_query.py` + `protocols/mcp_server.py` | E2E | AG-UI + MCP 双协议，工具总数 19→20 |

### 关键技术决策（实现期确立，非设计期）

#### 1. ONNX CPU 推理替代 sentence-transformers + torch

设计期建议用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`，但 sentence-transformers 依赖 torch（~800MB，国内镜像下载超时）。实现期改用 **ONNX Runtime + tokenizers** 方案：

- 用模型仓库已有的量化 ONNX 文件 `onnx/model_qint8_avx512.onnx`（113MB，CPU 最优）
- `onnxruntime` ~50MB + `tokenizers` ~5MB，无 torch 依赖，国内镜像秒装
- 推理速度 ~15ms/句（比 torch 还快），CPU 友好
- `OnnxEmbeddingProvider` 实现 `EmbeddingProvider` Protocol（可插拔，未来换 bge-m3/API 只需实现接口）
- L2 归一化输出，Doris `inner_product` == cosine

**模型文件**：手工下载到 `models/paraphrase-multilingual-MiniLM-L12-v2/`（4.4GB，含 4 种权重格式，实际只用 ONNX 量化版 ~113MB + tokenizer ~30MB）。`models/` 已加入 `.gitignore`（二进制资产不入库）。冗余文件（pytorch_model.bin/tf_model.h5/openvino/）不清理——onnxruntime 只加载显式指定的那一个 .onnx 文件，不影响运行。

#### 2. Doris ANN 索引构建：ALTER ADD INDEX 替代 inline CREATE TABLE

Doris 4.x ANN 索引在 CREATE TABLE 内联声明时，memtable load 预分配 ~2GB 内存，超过 dev 容器默认 1GB → `MEM_LIMIT_EXCEEDED`。解法：

- `create_semantic_table` 建表时**不带 ANN 索引**（纯存 ARRAY<FLOAT>）
- 数据 upsert 后用 `build_semantic_index`（`ALTER TABLE ... ADD INDEX ... USING ANN`）建索引——走低内存路径
- `vector_indexer.index_ontology` 首次索引后自动调 `build_semantic_index`（幂等）
- 索引类型用 **IVF**（比 HNSW 内存友好，适合小规模本体元数据），`nlist=128`

#### 3. Doris BE 内存配置变更

为支持 ANN 索引构建，Doris BE 配置调整：

- `config/doris/be.conf` 加 `mem_limit = 80%` + `load_mem_limit = 80%`（按容器内存算，而非宿主机）
- `docker-compose.yml` doris-be 服务内存 `1g → 3g`（mem_limit），mem_reservation `512m → 1g`，覆盖 heavy-resources 锚点

#### 4. 编译器 LIMIT/OFFSET 不参数化

Doris 的 `LIMIT ?` 占位符不被接受（`mismatched input 'LIMIT'`）。编译器对 `exp.Limit`/`exp.Offset` 的 Literal 内联（数字字面量，SQL 语法保证安全，不参数化）。

#### 5. Doris MySQL 协议不支持 ARRAY 参数化

`vector_search` 的查询 embedding 不能用 `%s` 占位符传（Doris 会当 VARCHAR，报 `inner_product_approximate(ARRAY<FLOAT>, VARCHAR)` 类型错）。embedding 内联为 `[1.0,2.0,...]` ARRAY 字面量——embedding 是模型 L2 归一化输出（非用户输入），内联安全。注意语法是 `[1.0,2.0]` 不是 `ARRAY([...])`（后者产生 `ARRAY<ARRAY<...>>` 嵌套类型）。

#### 6. 语义表用 DUPLICATE KEY（非 UNIQUE KEY）

Doris ANN 索引只能用在 DUP_KEYS 表。语义表用 `DUPLICATE KEY(ontology_api_name, element_type, element_api_name)`，upsert 语义靠应用层 delete-then-insert 实现（`upsert_semantic_rows`）。

#### 7. CTE 输出列只收集 AS 别名

`_collect_output_cols` 只收集 `AS alias` 的输出列，不收集 bare column（如 `SELECT amount FROM Order`）。否则 CTE 内 `SELECT bogus FROM Order` 的 `bogus` 会被误当 CTE 输出列而跳过白名单校验（护栏漏洞）。bare column 引用走 ObjectType 白名单校验。

### 端到端验证结果（Airline 本体 + Doris 真实环境）

#### text2sql 编译器 + 执行链路
- 单表过滤 `SELECT aircraftId, model, status FROM Aircraft WHERE status='Operational'` → 5 行真实数据，api_name 正确映射
- 聚合 `GROUP BY status, COUNT(*)` → 2 行（Operational:450, Maintenance:50）
- 三大护栏：非法表/列/JOIN 拦截；UPDATE 拒绝（走 Action）
- CTE：`WITH vip AS (...) SELECT ... FROM vip` 编译通过

#### 五步流水线（agent 入口自动接线）
用户问"查询状态为Operational的飞机机型"：
1. Step 1 LLM 解析意图 → QueryIR
2. Step 2 精确匹配召回 Aircraft
3. Step 3 注入 Aircraft 完整 Schema（4306 字符，9 属性+约束+护栏）
4. Step 4 LLM 用注入 Schema 调 `query_with_sql`
5. Step 5 返回 5 种真实机型（A350-900/A320neo/B777-300ER/B787-9/B737-800）

#### 双引擎召回（引擎B 价值验证）
口语化表达引擎A 失效，引擎B 向量召回精准命中：

| 口语化查询 | 引擎B 召回 | 相似度 |
|-----------|-----------|--------|
| "维修保养" | MaintenanceTask（ObjectType） | 0.696 |
| "机组人员" | Crew（ObjectType）+ 角色/部门属性 | 0.870 |
| "旅客订座" | 座位号/票价/旅客编号属性 | 0.858 |
| "大飞机" | 机龄/机型/制造商属性 | 0.846 |

完整五步流水线验证：用户问"查询维修保养相关的任务" → 引擎A 失效 → 引擎B 召回 MaintenanceTask → Schema 注入把 MaintenanceTask 排第一位 → LLM 调对应工具。

### 编译器支持的 SQL 特性（Phase 1-2）

| 特性 | 状态 | 说明 |
|------|------|------|
| 单表过滤/排序/分页 | ✅ | LIMIT/OFFSET 内联 |
| 多表 JOIN（≤5表） | ✅ | 走 LinkType 校验 |
| 子查询 | ✅ | WHERE 子查询 + 子查询 JOIN |
| CTE（WITH） | ✅ Phase 2 | 多 CTE + CTE+JOIN + 输出别名 |
| 多维聚合 + GROUP BY + HAVING | ✅ | |
| 窗口函数 ROW_NUMBER/RANK OVER | ✅ | |
| 自定义算式 `amount * 0.8` | ✅ | |
| 占比计算（聚合相除） | ✅ | role=derived + expr |
| 时间函数 DATE_FORMAT/YEAR/MONTH | ✅ | |
| 三大护栏（表/列/JOIN 白名单） | ✅ | |
| 参数化绑定（字面量→?） | ✅ | Doris ?→%s 转换 |
| UNION/INTERSECT/EXCEPT | ❌ Phase 3+ | |
| UPDATE/INSERT/DELETE | ❌ 永不做 | 走 Action 工具 |

### 质量门

- **115 单元测试 + 5 E2E 测试**（GAIA_TEXTQL_E2E=1 触发），全量 908 测试 0 回归
- ruff check + ruff format + mypy --strict 全过（87 源文件）
- 4 个可行性验证脚本作为技术预研原型与可行性证据（`scripts/verify_sqlglot_feasibility*.py` + `verify_ir_feasibility.py`，内联独立原型编译器，不参与 CI 回归）
- 前端 tsc 0 非测试错误，默认 ToolCallPart 渲染器自动支持 query_with_sql（无需新增渲染器）

### Phase 3 待做项

- HyDE + Rank Fusion（引擎B 增强）
- traverse_link 跨对象 JOIN（LinkTraversalService，单独做）
- 多轮对话状态管理（T14）
- 行级权限注入（路标 #4 协同）
- 审计 ObjectType 建模（T13）
- Trino 降级路径表名重编译（聚合+Doris 失败场景的边缘 case）
- `_validate_identifier` 全链路参数化绑定（当前白名单+escape，参数化待穿透调用点）
