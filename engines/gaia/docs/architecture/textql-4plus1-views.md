# TextQL 4+1 视图设计（ADR-012 架构视图）

> 本文档用 Kruchten 4+1 视图模型描述 TextQL（本体驱动自然语言查询）系统的架构，作为 [ADR-012](./adr-012-textql-ontology-driven-nl-query.md) 的架构视图补充。5 个视图从不同干系人视角描述同一系统，相互印证。
>
> 视图状态：反映 Phase 1-2 已实现状态（2026-06-28）。

---

## 视图总览

| 视图 | 回答的问题 | 主要干系人 | TextQL 体现 |
|------|-----------|-----------|------------|
| **逻辑视图** | 系统提供什么功能？功能如何分解？ | 产品/业务/架构师 | 五步流水线 + IR 一等公民 + 双引擎召回 + 三大护栏 |
| **进程视图** | 运行时如何并发/通信/调度？ | 系统集成/运维 | AG-UI SSE 流 + ONNX 推理 + Doris ANN 查询 + 降级链路 |
| **开发视图** | 代码如何组织？模块依赖？ | 开发者 | textql 子包分层 + 8 模块 + 依赖方向 + 测试基线 |
| **物理视图** | 部署在哪些节点？如何连接？ | 运维/部署 | Docker Compose 9 服务 + Doris BE 内存配置 + 模型文件 |
| **场景视图（+1）** | 关键用例如何贯穿各视图？ | 所有人 | 4 个端到端场景（口语化查询/复杂SQL/护栏拦截/降级） |

---

## 一、逻辑视图（功能分解）

> 视角：系统提供的功能能力，与实现技术无关。

### 1.1 功能能力分解

```
TextQL 自然语言查询系统
│
├── 意图理解（Step 1）
│   └── NL → QueryIR：LLM 结构化输出，按本体概念分类抽取
│       ├── 对象识别（objects → ObjectType 线索）
│       ├── 属性识别（properties → Property/度量/派生指标 线索）
│       ├── 关系识别（links → LinkType 线索）
│       ├── 条件/分组/排序/窗口解析
│       └── 意图分类（query/aggregate/topn/multi_step/complex_sql）
│
├── 语义召回（Step 2）—— 双引擎互补
│   ├── 引擎A 精确匹配（确定性，零幻觉）
│   │   └── displayName + description 别名 → api_name
│   └── 引擎B 向量召回（兜底，覆盖口语化/同义词）
│       ├── ONNX CPU 推理（384维 L2归一化向量）
│       └── Doris ANN 检索（IVF + inner_product ≈ cosine）
│   └── 融合：引擎A 优先，低置信/未命中时引擎B 兜底，合并去重
│
├── Schema 注入（Step 3）—— 确定性护栏
│   └── 候选 ObjectType 完整 Schema → LLM 上下文
│       ├── 6 类信息（基础/属性/关系/类型约束/业务约束/数据源）
│       └── 三大护栏：实体/字段/关系约束（LLM 只能用已定义元素）
│
├── 查询编排（Step 4）—— LLM Tool Use 双路径
│   ├── 路径A 原子工具（19个）—— 单表/标准聚合/TopN
│   └── 路径B text2sql（第20工具）—— 复杂查询逃生通道
│       ├── SqlGlot 编译器：逻辑SQL → 物理方言SQL（Doris/Trino）
│       ├── 编译期三大护栏：表/列/JOIN 白名单
│       └── 参数化绑定：字面量 → ? 占位（防注入）
│   └── 多步查询：LLM 自管理状态，迭代调工具补充召回
│
└── 查询执行（Step 5）—— 确定性执行
    ├── Doris 主路径（MANAGED 对象，全量属性直出）
    ├── Trino 降级（Doris 宕机/表未建/VIRTUAL 联邦）
    └── 出口映射：物理列 → api_name → displayName
```

### 1.2 核心抽象：QueryIR 一等公民

QueryIR 是贯穿 Step 1-4 的结构化"查询意图图"，是系统的核心抽象：

```
QueryIR（一等公民）
├── 角色：Step1-3 产出的中间表示，Step4 双消费者共消费，持久化审计载体
├── 本体概念分类：objects/properties/links/filters/group_by/order_by/windows
├── 双消费者：
│   ├── text2sql 编译器：IR → 逻辑SQL → 物理SQL
│   └── 原子工具：IR → filter_object/aggregate_object 参数
└── 多步继承：每个 IR 实例可命名，多步查询串成链（决策二：LLM 自管理）
```

**三大架构决策**（详见 ADR-012 §核心架构决策）：
1. **IR 一等公民**：非扁平词袋，按本体角色分类，召回变"按图索骥"
2. **LLM 自管理多步**：不上 ObjectSet 具名引用，上层语义负担轻
3. **只做查询层**：UPDATE 走 Action，What-if 走 Scenario，多步推理走工具链

### 1.3 边界：TextQL 不做什么

| 场景 | 处理方 | 理由 |
|------|--------|------|
| UPDATE/INSERT | 现有 Action 工具 | 已有 ActionService + OCC + 审计，不该绕过 |
| What-if 情景模拟 | Scenario 引擎（未实现） | 参数化重算，非查询 |
| 多步根因推理 | LLM 工具链编排 | 多次查询+推理，非单 SQL |
| 多轮对话追问 | 对话状态管理（未实现） | 复用前序 ObjectSet |

---

## 二、进程视图（运行时并发与通信）

> 视角：运行时的进程/线程、并发、通信、调度、降级。

### 2.1 请求处理时序（单次自然语言查询）

```
用户浏览器
    │ POST /ai/agent (AG-UI RunAgentInput, SSE)
    ▼
FastAPI 进程（uvicorn，单进程多协程）
    │
    ├─[1] routes/ai.py: 提取 thread_id + ontology + 最后一条用户消息
    │
    ├─[2] orchestrator.build_injected_schema(container, ontology, msg)  [协程]
    │   │
    │   ├─[2a] intent_parser.parse_intent(msg)  [LLM 调用, ~1-2s]
    │   │       └─ DeepSeek API (pydantic-ai result_type=QueryIR)
    │   │          → QueryIR
    │   │
    │   ├─[2b] _maybe_vector_search(container, ontology)  [协程]
    │   │       ├─ DorisIndexStore.semantic_table_exists()  [Doris 查询]
    │   │       ├─ get_embedding_provider()  [懒加载 ONNX 会话, 首次~1s]
    │   │       └─ 返回 async vector_search 闭包（或 None 禁用引擎B）
    │   │
    │   ├─[2c] SemanticRecaller.recall(ir)  [async]
    │   │       ├─ 引擎A 同步精确匹配（内存）
    │   │       └─ 引擎B（低置信时）:
    │   │           ├─ OnnxEmbeddingProvider.embed([noun])  [CPU, ~15ms]
    │   │           └─ DorisIndexStore.vector_search(emb, ontology)  [Doris ANN]
    │   │          → RecallResult
    │   │
    │   └─[2d] SchemaInjector.build_context_block(ots, recall)
    │           → schema_block 字符串（塞入 AppState.injected_schema）
    │
    ├─[3] AGUIAdapter.dispatch_request(agent, deps)  [SSE 流式输出]
    │   │
    │   └─ pydantic-ai Agent 运行（LLM 多轮工具调用）
    │       ├─ system_prompt 动态注入 schema_block（@agent.system_prompt 装饰器）
    │       │
    │       ├─ LLM 决策调工具（DeepSeek API，每轮 ~1-2s）
    │       │   ├─ 路径A: filter_object/aggregate_object/...
    │       │   │   └─ ObjectQueryService → Doris/Trino（带白名单护栏）
    │       │   └─ 路径B: query_with_sql
    │       │       ├─ OntologySqlCompiler.compile(logical_sql, dialect)
    │       │       │   [纯 CPU, 两遍遍历, <10ms]
    │       │       └─ ObjectQueryService.execute_compiled_sql
    │       │           ├─ Doris 主路径（execute_sql, ?→%s）
    │       │           └─ Doris 失败 → Trino 降级（重新编译 Trino 方言）
    │       │
    │       └─ 每步工具结果回 LLM，LLM 决定继续调工具 or 返回最终答案
    │
    └─ SSE 流式返回 TEXT_MESSAGE / TOOL_CALL / RUN_FINISHED 事件
```

### 2.2 并发模型

| 组件 | 并发模型 | 阻塞点 | 说明 |
|------|---------|--------|------|
| FastAPI | 单进程多协程（asyncio） | LLM API、Doris/Trino IO | 全异步，不阻塞事件循环 |
| ONNX 推理 | 同步（CPU） | ~15ms/句 | 单次查询只 embed 1 次（用户问句），可接受；批量向量化是低频操作 |
| Doris 连接池 | aiomysql 连接池 | IO | 多协程共享池，await 不阻塞 |
| pydantic-ai Agent | 协程 | LLM API | 单次 Agent.run 内部多轮工具调用串行（LLM 决策→工具→LLM） |
| ONNX InferenceSession | 线程安全 | — | 单例会话，多协程共享（onnxruntime session inference 线程安全） |

### 2.3 降级链路（可靠性）

```
查询执行降级（Step 5）：
  MANAGED 对象 + Doris 可用 + 表已建 → Doris 直出（主路径）
       │ Doris 宕机
       ▼
       Trino 扫描 Iceberg（降级，带分区裁剪）
       │ Doris 表未建（not_built）
       ▼
       Trino 扫描 Iceberg（info 级，非故障）

引擎B 降级（Step 2）：
  语义表存在 + 模型已装 → 引擎B 向量召回
       │ 任一条件不满足 / 查询失败
       ▼
       纯引擎A 精确匹配（非致命，回退）

Step 1-3 降级：
  LLM 意图解析失败 → 注入全量 Schema（无召回收窄）
  召回/注入失败 → 空 schema_block（agent 仍可运行，LLM 自调 metadata 工具）
```

### 2.4 性能特征（实测，Airline 本体）

| 环节 | 延迟 | 说明 |
|------|------|------|
| Step 1 意图解析 | ~1-2s | DeepSeek API，单次 |
| Step 2 引擎A | <1ms | 内存精确匹配 |
| Step 2 引擎B | ~15ms + Doris ANN ~10ms | ONNX CPU + Doris IVF |
| Step 3 Schema 注入 | <1ms | 内存渲染 |
| Step 4 编译器 | <10ms | 纯 CPU，两遍遍历 |
| Step 5 Doris 执行 | ~10-50ms | 点查/过滤 |
| **端到端单轮** | **~2-3s** | 主要耗时在 LLM API |

---

## 三、开发视图（代码组织与依赖）

> 视角：代码模块划分、依赖方向、分层规则。

### 3.1 模块划分

```
src/ontology/
├── core/schemas/
│   └── textql.py                    # IR schema（一等公民）—— 纯 pydantic，无依赖
│
├── services/textql/                 # TextQL 子包（8 模块）
│   ├── __init__.py                  # 公共导出（不含 orchestrator，防循环）
│   ├── intent_parser.py             # Step 1: 依赖 ai_generate + QueryIR
│   ├── semantic_recall.py           # Step 2: 依赖 QueryIR + ObjectType（引擎B 注入）
│   ├── embedding.py                 # 引擎B 推理: 依赖 onnxruntime + tokenizers
│   ├── vector_indexer.py            # 向量化流水线: 依赖 DorisIndexStore + EmbeddingProvider
│   ├── schema_injector.py           # Step 3: 依赖 ObjectType + RecallResult
│   ├── schema_provider.py           # 编译器输入: 依赖 PostgresMetaStore
│   ├── sql_compiler.py              # Step 4 编译器: 依赖 sqlglot + OntologySchemaProvider Protocol
│   └── orchestrator.py              # Step 1-3 串联: 依赖上述（Container 用 TYPE_CHECKING）
│
├── services/object_query_service.py # ✏️ 扩展: execute_compiled_sql + 白名单护栏
├── layers/index/doris_index_store.py # ✏️ 扩展: 语义表 + ANN + execute_sql
├── tools/toolsets/object_query.py   # ✏️ 扩展: 第20工具 query_with_sql
├── tools/state.py                   # ✏️ 扩展: AppState.injected_schema
├── protocols/mcp_server.py          # ✏️ 扩展: MCP 注册 query_with_sql
├── services/ai_agent.py             # ✏️ 扩展: 动态 system_prompt 装饰器
└── routes/ai.py                     # ✏️ 扩展: /ai/agent 入口接 orchestrator
```

### 3.2 依赖方向（严格分层，禁止反向）

```
routes/ai.py
    ↓
orchestrator.py ──────────────┐
    ↓                          ↓
intent_parser   semantic_recall   schema_injector
    ↓              ↓                  ↓
ai_generate    embedding.py        ObjectType/RecallResult
    ↓              ↓
  LLM API     onnxruntime
                   ↓
            DorisIndexStore（vector_search）
                   ↓
                Doris

sql_compiler.py（独立，无 service 依赖）
    ↓
OntologySchemaProvider Protocol  ←── schema_provider.py（实现）
                                        ↓
                                  PostgresMetaStore

object_query_service.py
    ↓
sql_compiler（路径B）+ Doris/Trino（执行）+ 白名单护栏
```

**关键约束**：
- `sql_compiler.py` 通过 `OntologySchemaProvider` Protocol 解耦，不直接依赖 PostgresMetaStore（可单测）
- `orchestrator.py` 用 `TYPE_CHECKING` 导入 Container，避免循环（routes→container→object_query_service→textql/__init__→orchestrator→container）
- `textql/__init__.py` 不导出 orchestrator，routes/ai 懒导入

### 3.3 测试基线

| 测试文件 | 用例 | 类型 |
|---------|------|------|
| test_textql_schemas.py | 24 | 单元（IR 表达力） |
| test_sql_compiler.py | 32 | 单元（编译器全特性） |
| test_semantic_recall.py | 9 | 单元（引擎A） |
| test_vector_recall.py | 8 | 单元（引擎B 融合 + indexer） |
| test_embedding.py | 7 | 单元（ONNX 推理，需模型） |
| test_schema_injector.py | 7 | 单元（Schema 注入） |
| test_textql_orchestrator.py | 7 | 单元（Step 1-3 串联，mock） |
| test_object_query_whitelist.py | 21 | 单元（白名单护栏） |
| test_textql_e2e.py | 5 | 集成（GAIA_TEXTQL_E2E=1，真实 Doris） |
| **合计** | **115 + 5** | |

技术预研原型与可行性证据：4 个可行性验证脚本（`scripts/verify_sqlglot_feasibility*.py` + `verify_ir_feasibility.py`，内联独立原型编译器，与生产实现分离，不参与 CI 回归）。

---

## 四、物理视图（部署与节点）

> 视角：进程部署在哪些物理/虚拟节点，如何连接。

### 4.1 部署拓扑（Docker Compose，dev 环境）

```
┌─────────────────────────────────────────────────────────────┐
│  Docker Host（单机，dev）                                       │
│                                                               │
│  ┌──────────────┐    ┌──────────────────────────────────────┐│
│  │ FastAPI 进程  │    │ Doris FE (8090)                      ││
│  │ (uvicorn)    │    │   ↓ heartbeat                         ││
│  │  :8000       │    │ Doris BE (9050)  ← 内存 3g（TextQL 调整）││
│  │              │    │   - 对象数据表 idx_{ont}__{type}        ││
│  │  挂载:        │    │   - 语义表 idx_ontology_semantic       ││
│  │  - models/   │    │     (ARRAY<FLOAT> + IVF ANN 索引)      ││
│  │    MiniLM    │    └──────────────────────────────────────┘│
│  │    ONNX 模型 │                                              │
│  │              │    ┌──────────────┐  ┌──────────────────┐  │
│  │  依赖进程:    │    │ PostgreSQL   │  │ Trino (8080)     │  │
│  │  - PG        │←──→│ :5432        │  │  Doris 降级路径   │  │
│  │  - Doris     │    │ 本体元数据     │  │  VIRTUAL 联邦     │  │
│  │  - Trino     │    └──────────────┘  └──────────────────┘  │
│  │  - Gravitino │                                              │
│  └──────┬───────┘    ┌──────────────┐  ┌──────────────────┐  │
│         │            │ Gravitino       │  ┌──────────────────┐  │
│         │            │ :8090 主服务     │  │ Iceberg REST      │  │
│         │            │ :9001 Iceberg    │  │ RustFS/S3 后端     │  │
│         │            │   REST Catalog   │  │ :9000             │  │
│         │            │ 物理资产注册      │  └──────────────────┘  │
│         │            └─────────────────┘                          │
│         │                                                     │
│         │ DeepSeek API（外部，LLM 推理）                          │
│         └─────────────────────────────────────────────────→   │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 TextQL 相关的物理配置变更

| 配置项 | 变更 | 原因 |
|--------|------|------|
| `config/doris/be.conf` | 加 `mem_limit=80%` + `load_mem_limit=80%` | 按 Docker 容器内存算，而非宿主机（否则 Doris 看到宿主内存，load_mem_limit 估算过大） |
| `docker-compose.yml` doris-be | `mem_limit 1g→3g`, `mem_reservation 512m→1g` | ANN memtable load 预分配 ~2GB，1g 容器触发 MEM_LIMIT_EXCEEDED |
| `models/` 目录 | 手工下载 MiniLM ONNX 模型，gitignore | 4.4GB 二进制资产不入库；onnxruntime 只加载指定的量化 ONNX 文件 |
| `pyproject.toml` | 加 sqlglot + onnxruntime + tokenizers | 编译器 + ONNX CPU 推理（无 torch 依赖） |

### 4.3 外部依赖

| 依赖 | 用途 | Phase |
|------|------|-------|
| DeepSeek API | Step 1 意图解析 + Step 4 LLM 工具编排 | 必需（无本地 LLM） |
| Doris 4.0.5 | Step 5 执行 + Step 2 引擎B ANN 检索 | 必需（降级可走 Trino） |
| Trino 470 | Step 5 降级 + VIRTUAL 联邦 | 降级路径 |
| MiniLM ONNX 模型 | Step 2 引擎B embedding | 引擎B 必需（引擎A 不需要） |

### 4.4 生产部署考量

- **ONNX 模型**：生产可放对象存储或镜像内，多实例共享只读
- **Doris BE 内存**：生产按集群规模调 `mem_limit`，IVF 索引内存友好
- **LLM API**：生产考虑 LLM 网关（限流/缓存/降级）
- **语义表**：多本体共享一张表（`ontology_api_name` 列区分），生产可按本体分桶优化

---

## 五、场景视图（+1，关键用例贯穿）

> 视角：用关键用例验证各视图协作。4 个场景覆盖正常路径、复杂查询、护栏拦截、降级。

### 场景 1：口语化查询（验证引擎B 价值）

**用户问句**："查询维修保养相关的任务"

**贯穿各视图**：

| 步骤 | 逻辑视图能力 | 进程视图动作 | 开发视图模块 | 物理视图节点 |
|------|------------|------------|------------|------------|
| 1. 接收 | 意图理解 | routes/ai 提取消息 | routes/ai.py | FastAPI |
| 2. 解析 | NL→QueryIR | LLM 调用 ~1.5s | intent_parser | DeepSeek API |
| 3a. 引擎A | 精确匹配 | 内存匹配 | semantic_recall | FastAPI 内存 |
| 3b. 引擎A 失效 | "维修保养"≠任何 OT displayName | — | — | — |
| 3c. 引擎B | 向量召回 | ONNX embed + Doris ANN | embedding + doris_index_store | FastAPI CPU + Doris BE |
| 3d. 引擎B 命中 | MaintenanceTask (sim=0.696) | — | — | — |
| 4. 注入 | Schema 注入 | 渲染 MaintenanceTask 完整 Schema | schema_injector | FastAPI 内存 |
| 5. 编排 | LLM 调工具 | LLM 决策调 filter_object | ai_agent | DeepSeek API |
| 6. 执行 | Doris 直出 | 查询 idx_airline__maintenancetask | object_query_service | Doris BE |
| 7. 返回 | 出口映射 | 物理列→api_name | object_query_service | FastAPI |

**验证点**：引擎A 失效时引擎B 兜底，Schema 注入把 MaintenanceTask 排第一位。

### 场景 2：复杂 SQL（验证 text2sql 编译器）

**用户问句**："各机型的飞机数量，按数量降序"

**LLM 生成逻辑 SQL**（Step 4 路径B）：
```sql
SELECT model, COUNT(*) AS cnt FROM Aircraft GROUP BY model ORDER BY cnt DESC
```

**编译器处理**（Step 4）：
- Pass 1：收集 Aircraft 无 alias
- Pass 2：`Aircraft` → `idx_airline__aircraft`，`model` → `model`，`COUNT(*)` 保留，`ORDER BY cnt`（输出别名，跳过校验）
- 产出 Doris SQL：`SELECT model, COUNT(*) AS cnt FROM idx_airline__aircraft GROUP BY model ORDER BY cnt DESC`
- 参数：`[]`（无字面量）

**执行**：Doris 返回机型分组计数。

**验证点**：编译器正确处理聚合 + GROUP BY + ORDER BY 输出别名 + 物理名映射。

### 场景 3：护栏拦截（验证三大护栏）

**用户问句**："查询飞机的颜色"

**LLM 生成逻辑 SQL**：
```sql
SELECT aircraftId, color FROM Aircraft
```

**编译器处理**：
- `Aircraft` → 校验通过（已定义 ObjectType）
- `aircraftId` → 校验通过（Aircraft 已定义属性）
- `color` → **校验失败**：`color` 不在 Aircraft.properties 白名单 → `INVALID_COLUMN`

**返回**：`{"error": {"code": "INVALID_COLUMN", "message": "未知 Property 'color' 不属于 ObjectType Aircraft"}}`

**验证点**：编译期字段护栏拦截 LLM 瞎编字段，而非放行后 SQL 执行报错（可解释）。

### 场景 4：Doris 降级（验证可靠性）

**前提**：Doris BE 宕机。

**用户问句**："查询状态为 Operational 的飞机"

**执行链路**：
- `execute_compiled_sql` 编译 Doris SQL + Trino SQL（双方言预编译）
- `index.table_exists()` → DorisUnavailableError
- 降级：`engine.query(trino_sql, params)` —— Trino 扫描 Iceberg
- 出口映射：物理列 → api_name

**验证点**：Doris 失败时自动降级 Trino，查询仍可完成（延迟略增）。

---

## 六、视图一致性矩阵

验证 4 个视图相互印证，无矛盾：

| 设计要素 | 逻辑视图 | 进程视图 | 开发视图 | 物理视图 |
|---------|---------|---------|---------|---------|
| IR 一等公民 | Step1-3 产出，Step4 消费 | QueryIR 对象在协程间传递 | `core/schemas/textql.py` | 仅内存，无持久化节点 |
| 双引擎召回 | 引擎A+B 互补 | 引擎B 懒加载 ONNX + Doris ANN | semantic_recall + embedding + vector_indexer | ONNX 模型在 FastAPI 节点，ANN 数据在 Doris BE |
| 三大护栏 | 实体/字段/关系约束 | 编译期校验，失败不进执行 | sql_compiler + 白名单 | 纯 CPU，无外部节点 |
| LLM 编排 | Tool Use 双路径 | pydantic-ai Agent 多轮工具调用 | ai_agent + toolsets | LLM API 外部 |
| 降级链路 | Doris→Trino | 异常捕获 + 重编译 | object_query_service | Doris BE + Trino 双节点 |
| ONNX CPU 推理 | 引擎B embedding | 单例会话，~15ms/句 | embedding.py | 模型文件挂载 FastAPI 节点 |

**一致性结论**：4 视图描述同一系统，关键要素在各视图均有对应体现，无矛盾。

---

## 七、演进路标（Phase 3+ 对各视图的影响）

| Phase 3+ 项 | 逻辑视图影响 | 进程视图影响 | 开发视图影响 | 物理视图影响 |
|------------|------------|------------|------------|------------|
| HyDE + Rank Fusion | 引擎B 增强（LLM 生成假设答案再检索） | 多一次 LLM 调用 | semantic_recall 扩展 | 无 |
| traverse_link 跨对象 JOIN | 新增跨实体查询能力 | 新 LinkTraversalService 进程内调用 | 新 service + 工具实现 | 可能引入图数据库节点 |
| 多轮对话状态 | ObjectSet 上下文复用 | 对话状态在 thread 间持久化 | 新对话状态管理模块 | 可能引入 Redis（当前无） |
| 行级权限注入 | WHERE 自动补权限谓词 | 编译期注入权限 | sql_compiler + 白名单扩展 | 无 |
| 审计 ObjectType 建模 | T13 审计溯源查询 | 新审计表查询路径 | 新 ObjectType + 工具 | Doris 新表 |

每个 Phase 3+ 项的影响范围清晰，便于评估工作量与风险。
