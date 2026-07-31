# ADR-020：本体全量元数据聚合端点（describe_ontology）

| 字段     | 内容 |
| -------- | ---- |
| **状态** | 已采纳 |
| **审批日期** | 2026-07-16 |
| **影响层** | `tools/toolsets/metadata.py`（新增 `describe_ontology` 逻辑 + 工具）、`protocols/mcp_server.py`（MCP 注册）、`services/ontology_service.py`（新增 `assemble_ontology_metadata` 聚合装配）、`services/textql/orchestrator.py`（`build_ontology_summary` 复用装配函数 + 补 Action 概要）、`core/schemas/ontology.py`（新增 `OntologyFullMetadata` 结构体） |
| **相关文档** | [ADR-009 本体工具层](./adr-009-ontology-tool-layer.md)（三入口 + 共享 `*_logic` 模式）、[ADR-012 TextQL](./adr-012-textql-ontology-driven-nl-query.md)（`build_ontology_summary` 文本注入，本 ADR 与其互补）、[ADR-019 三入口能力分层](./adr-019-three-entry-capability-layering.md)（`describe_ontology` 属操作面发现能力，MCP 必注册）、[reference.md](../reference.md)（Palantir `/fullMetadata` 范式源头） |
| **前置问题** | 外部 Agent（MCP）首次接入一个本体时，需串行调用 `list_ontologies → list_object_types → describe_object_type(×N) → describe_link_type(×M)` 才能拼出完整 schema，每次工具调用都是一轮 LLM 往返，发现阶段开销过大。 |

---

## 背景

### 痛点：发现阶段的 N 轮 round-trip

本体元数据对外暴露目前是细粒度工具集（`metadata.py` 4 个工具）。一个外部 Agent 要"搞清楚某本体里 Customer 与 Order 的关系"，最少需要：

```
list_ontologies → list_object_types → describe_object_type(Customer)
                                   → describe_object_type(Order)
                                   → describe_link_type(has_order)
```

5 次工具调用，每次都走 LLM tool-call 往返，prompt 上下文随轮次膨胀。对 MCP 外部 Agent（无"当前本体"概念、无系统上下文预注入）尤其昂贵。

### Palantir 的解法

Palantir Foundry 专为该痛点设计了聚合端点：

```
GET /v2/ontologies/{ontology}/fullMetadata
```

官方原话：

> Get the full Ontology metadata. This includes the objects, links, actions, queries, and interfaces. **This endpoint is designed to return as much metadata as possible in a single request to support OSDK workflows.** It may omit certain entities rather than fail the request.

关键设计：

1. **单次请求返回全部五类元数据**（objects / links / actions / queries / interfaces）
2. **返回 `map<ApiName, FullMetadata>`**，客户端按 api_name O(1) 索引
3. **"may omit rather than fail"**——best-effort 聚合，单个实体加载失败不阻塞整体
4. **`ObjectTypeFullMetadata` 是聚合体**：每个 OT 自带 `object_type` + `link_types` + `implements_interfaces`，自描述闭环

Palantir 同时保留细粒度端点（`list_object_types` / `get_object_type` / ...）服务增量探查与分页；聚合端点服务"首次接入"与"OSDK 代码生成"。

### Gaia 内部 Agent 已有等价机制（文本注入）

`/ai/agent` 路由在 Agent 启动前调用 `build_ontology_summary(container, ontology)`，**一次 DB 查询把 ObjectType + Property + LinkType 渲染成 markdown 塞进 `ctx.deps.injected_schema`**，内部 Agent 启动即知本体结构，发现阶段 round-trip 已压到 0。这正是 Palantir AIP 的 "Retrieval Context → Ontology context" 范式（每条消息前确定性注入）。

---

## 决策

### 1. 新增 `describe_ontology` 聚合工具/端点（对齐 Palantir fullMetadata）

新增一个工具 `describe_ontology(ontology) -> OntologyFullMetadata`，单次返回整个本体的结构化元数据：

```
OntologyFullMetadata {
  ontology: { api_name, display_name, description }
  object_types: map<api_name, ObjectTypeFullMetadata>
  link_types:  map<api_name, LinkTypeDef>
  action_types: map<api_name, ActionTypeSummary>   # 概要，不含完整 parameters
  interfaces:  list<InterfaceType>                  # 当前为 preview，列表即可
  partial: bool          # 是否有实体被省略
  omitted: list[str]     # 被省略的实体标识（best-effort 语义）
}

ObjectTypeFullMetadata {
  api_name, display_name, description, primary_key, title_property,
  storage_type, visibility, status,
  properties: list<PropertyDef>,
  inbound_links:  list<link_api_name>,   # 谁指向我
  outbound_links: list<link_api_name>,   # 我指向谁
  actions: list<action_api_name>,        # 作用于我的 Action
}
```

**设计要点**（逐条对标 Palantir）：

| 要点 | 实现 |
|------|------|
| 单次请求全量 | `assemble_ontology_metadata` 一次并发查 metadata 层（object_types + link_types + action_types + interfaces） |
| map 索引 | `object_types` / `link_types` / `action_types` 均为 `dict[api_name, ...]` |
| may omit rather than fail | 整个装配包在 try/except，单类查询失败记入 `omitted` 而非抛异常；返回 `partial=true` |
| OT 自带 inbound/outbound links | 装配时按 `source/target_object_type_id` 分组挂到每个 OT |
| OT 自带 actions | 装配时按 `affected_object_type_id` 分组挂到每个 OT |

### 2. 入口分层（遵循 ADR-019）

| 入口 | 是否注册 `describe_ontology` | 理由 |
|------|:---:|------|
| **MCP** | ✅ 注册 | 外部 Agent 无"当前本体"上下文，发现阶段必须自助；属操作面发现能力 |
| **AG-UI** | ❌ 不注册 | 内部 Agent 已通过 `build_ontology_summary` 文本注入获得结构，注册反而诱导 LLM 冗余调用（已有摘要还重复拉全量）。AG-UI 与 MCP 能力对等原则的**例外**：协议必然差异（AG-UI 有隐式 ontology 上下文，参见 ADR-019 §2 例外条款同 `list_ontologies`） |
| **REST** | ✅ 注册（`GET /ontologies/{ont}/fullMetadata`） | 管理面全功能，脚本/OSDK 代码生成器使用 |

> **AG-UI 不注册的判据**：这不是能力缺失，而是"同一信息已通过更高效的通道（系统上下文注入）提供"。AG-UI Agent 能看到的信息**不亚于** `describe_ontology` 返回的内容（见决策 4：`build_ontology_summary` 补 Action 后信息对齐）。

### 3. 抽 `assemble_ontology_metadata` 为单一真相源（DRY）

`describe_ontology` 与 `build_ontology_summary` 共用同一个数据装配函数，消除两套装配的漂移风险：

```python
# services/ontology_service.py
async def assemble_ontology_metadata(
    self, ontology_api_name: str
) -> OntologyFullMetadata:
    """单次装配本体全量元数据。describe_ontology 工具与 build_ontology_summary 共用。"""
```

- `describe_ontology` 工具 → 直接返回该结构体（JSON 序列化）
- `build_ontology_summary` → 拿该结构体做 markdown 渲染

### 4. `build_ontology_summary` 补 Action 概要（对齐两入口认知）

当前 `build_ontology_summary` 明确省略 Action（"write-side concerns，按需调工具"）。但 MCP 外部 Agent 通过 `describe_ontology` 能看到 Action 列表，内部 Agent 却看不到，**两入口对"本体能做什么动作"认知不一致**——违背 ADR-019「MCP 与 AG-UI 操作面能力集必须对等」的精神。

决策：`build_ontology_summary` 渲染时补上每个 OT 的可用 Action api_name + display_name + 一句话描述（**不含完整 parameters schema**，那个仍按需 `describe_action_type` / `validate_action`）。完整 parameters 走细粒度工具。

### 5. 保留细粒度工具

`describe_object_type` / `describe_link_type` / `list_object_types` / `list_link_types` / `list_ontologies` **全部保留**，服务：
- 增量深入探查（已知要看某一个 OT 的完整 filterable/sortable hints）
- 大本体分页场景（`describe_ontology` 是 best-effort 全量，超大本体可能 omit）
- `describe_ontology` 失败时的降级路径

---

## 反模式（明确禁止）

- ❌ 用 `describe_ontology` 替换内部 Agent 的 `build_ontology_summary` 文本注入——文本形态对 LLM 更友好，且已在每轮 context 里，强行换 JSON 反而要 LLM 重新解析
- ❌ 在 AG-UI 路径注册 `describe_ontology`——内部已有摘要，注册诱导冗余调用
- ❌ 让 `describe_ontology` 在某类元数据查询失败时整体报错——必须 best-effort（`partial` + `omitted`）
- ❌ 在 `describe_ontology` 里塞完整 Action parameters——体积过大，按需走细粒度工具
- ❌ `assemble_ontology_metadata` 与 `build_ontology_summary` 各自手搓装配——必须共用单一真相源

---

## 与红线的关系

- **红线 11（Ontology API 不吃 NL）**：不冲突。`describe_ontology` 是元数据发现，返回结构化 schema，无 LLM 参与
- **红线 12（三入口能力分层）**：`describe_ontology` 属操作面发现能力，MCP 必注册；AG-UI 因隐式上下文例外不注册（同 `list_ontologies` 先例）；REST 管理面注册
- **ADR-019 §2 例外条款**：AG-UI 隐式 ontology 上下文导致部分发现工具无需注册的先例已确立（`list_ontologies`），`describe_ontology` 沿用同一例外

---

## 实现清单

1. `core/schemas/ontology.py`：新增 `ObjectTypeFullMetadata` + `ActionTypeSummary` + `OntologyFullMetadata` pydantic 模型
2. `services/ontology_service.py`：新增 `assemble_ontology_metadata` 方法（并发查 4 类元数据 + 分组挂载 + best-effort）
3. `services/textql/orchestrator.py`：`build_ontology_summary` 改为消费 `assemble_ontology_metadata` 输出 + 补 Action 概要渲染
4. `tools/toolsets/metadata.py`：新增 `describe_ontology_logic`（薄包装 `assemble_ontology_metadata`）+ `_contracts.py` 描述
5. `tools/__init__.py`：导出 `describe_ontology_logic`
6. `protocols/mcp_server.py`：注册 `describe_ontology` MCP 工具
7. `routes/ontology/`：新增 `GET /ontologies/{ont}/fullMetadata` REST 端点
8. 测试：`tests/unit/tools/test_metadata_toolset.py` 补 `describe_ontology` 用例；`tests/unit/services/` 补 `assemble_ontology_metadata` 用例；`build_ontology_summary` Action 渲染用例
