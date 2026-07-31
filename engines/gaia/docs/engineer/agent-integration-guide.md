# Gaia Agent 对接指南

> 面向 **外部 Agent 开发者** 的 MCP 接入文档。覆盖 MCP 工具调用契约、HITL 机制、分页与错误处理。
>
> 版本：v1.0 | 对应 ADR-019 | 日期：2026-07-15

---

## 一、Gaia 是什么

Gaia 是一个本体驱动的智能决策平台。你（Agent）通过 Gaia 暴露的能力，可以**查询企业本体里的业务对象**、**推理对象间的关系**、**执行已定义的业务动作**（以上均通过 MCP 操作面完成）。

你不接触底层存储（PostgreSQL / Iceberg / Doris / Neo4j / Trino），只通过本体语义层操作。所有能力都围绕三个核心概念：

| 概念 | 说明 | 例子 |
|------|------|------|
| **ObjectType** | 业务对象类型（类比数据库表的结构定义 + 业务语义） | `Customer`、`Order`、`Supplier` |
| **LinkType** | 对象间的关系类型 | `Customer` `places` `Order` |
| **ActionType** | 可执行的业务动作（带参数校验 + 权限 + 写入规则） | `cancel_order`、`approve_loan` |

---

## 二、MCP 对接（唯一入口）

MCP 是外部 Agent 的**唯一入口**。Gaia MCP server 暴露 13 个工具（查询/推理/动作），握手时返回 server instructions 说明能力边界。

### 2.1 启动 MCP server

Gaia MCP server 是独立进程，支持两种传输：

```bash
# stdio（本地 IDE / Claude Desktop 集成）
ontology-mcp --stdio

# Streamable HTTP（远程 Agent / 自建 Agent）
ontology-mcp --http --port 9000 --host 0.0.0.0
```

### 2.2 客户端配置示例

**Claude Desktop**（`claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "gaia-ontology": {
      "command": "ontology-mcp",
      "args": ["--stdio"]
    }
  }
}
```

**Python 客户端**（fastmcp）：

```python
from fastmcp import Client

# stdio
async with Client("gaia-ontology", transport="stdio") as client:
    tools = await client.list_tools()
    result = await client.call_tool("list_ontologies", {})

# HTTP
async with Client("http://localhost:9000/mcp") as client:
    result = await client.call_tool("query_with_sql", {
        "ontology": "SupplyChain",
        "sql": "SELECT name, riskLevel FROM Supplier WHERE riskLevel = 'high'"
    })
```

### 2.3 握手：先读 instructions

连接后**先调 `list_tools` 并读 server instructions**——它声明了能力边界和推荐调用顺序：

```
Recommended call order:
  1. list_ontologies — discover valid `ontology` values.
  2. describe_ontology — bootstrap a new ontology in ONE call (all object
     types + links + actions). Preferred over the
     list_object_types → describe_object_type → describe_link_type chain.
  3. list_object_types / list_link_types — enumerate when you only need
     names, or describe_object_type / describe_link_type for one entity's
     full schema (e.g. filterable/sortable hints, full action parameters).
  4. query_with_sql (attribute) or query_with_dataframe (relationship/spatial/temporal).
  5. traverse_link / exists_link / find_paths for relationships.
  6. Write/action tools require elicitation support for HITL.
```

### 2.4 `ontology` 参数

**每个 MCP 工具都要求 `ontology` 参数**（外部 Agent 没有隐式"当前本体"上下文）。值来自 `list_ontologies` 返回的 `api_name`。

---

## 三、核心能力详解

### 3.1 MCP 工具清单（13 个）

| 工具 | 类别 | 需 HITL |
|------|------|:------:|
| `list_ontologies` | 元数据 | 否 |
| `describe_ontology` | 元数据（bootstrap） | 否 |
| `list_object_types` / `describe_object_type` | 元数据 | 否 |
| `list_link_types` / `describe_link_type` | 元数据 | 否 |
| `query_with_sql` | 查询 | 否 |
| `query_with_dataframe` | 推理查询 | 否 |
| `traverse_link` | 关系推理 | 否 |
| `exists_link` | 关系推理 | 否 |
| `find_paths` | 关系推理 | 否 |
| `validate_action` | 动作预检 | 否 |
| `invoke_action` | 动作执行 | 是 |

> **`describe_ontology` 是首次接入的首选**——一次调用返回整个本体的 ObjectType（含 properties + inbound/outbound links + 可用 actions）+ LinkType + ActionType 概要 + Interface，避免 `list_object_types → describe_object_type(×N) → describe_link_type(×M)` 串行往返。对齐 Palantir `/fullMetadata`。best-effort：某类元数据加载失败时 `partial=true` + `omitted` 列出跳过的类别（其余仍返回），不要把 partial 当错误。完整 action 参数仍需 `validate_action` / `describe_object_type` 按需获取。

### 3.2 两条查询线：`query_with_sql` vs `query_with_dataframe`

这是最容易选错的决策点。Gaia 有两条独立的查询线：

| 维度 | `query_with_sql` | `query_with_dataframe` |
|------|------------------|------------------------|
| **查询引擎** | Doris（在线读主源） | Neo4j + PostGIS + TimescaleDB + PG |
| **擅长** | 属性过滤、聚合、JOIN、点查 | 关系遍历、空间过滤、时序、集合运算 |
| **输入** | 逻辑 SQL（ObjectType api_name 作表名） | ObjectSet IR（JSON，判别联合） |
| **分页** | SQL 自己写 LIMIT/OFFSET | `cursor` 参数 |
| **SQL 限制** | JOIN ≤5 表，不支持 CTE(WITH)/UNION/写操作 | — |

**选型口诀**：
- 问"有多少 / 总额 / 列出 region=EAST 的"→ `query_with_sql`
- 问"谁供应 S001 / 2 跳内连到哪些订单 / 5km 内的"→ `query_with_dataframe`

### 3.3 ObjectSet IR 结构（`query_with_dataframe` 输入）

ObjectSet IR 是 JSON，`type` 字段判别操作类型：

```jsonc
// 起始集：某类型的全部对象
{"type":"objectType","object_type":"Customer"}

// 起始集 + 过滤
{"type":"objectType","object_type":"Supplier",
 "filters":[{"field":"riskLevel","op":"exactMatch","value":"high"}]}

// 显式对象列表（业务主键；必须带 object_type 以便翻译层解析 rid）
{"type":"static","object_type":"Supplier","objects":["S001","S002"]}

// 图遍历（searchAround，≤3 跳，默认 direction="both", hops=(1,3)）
{"type":"searchAround","link":"supplies",
 "object_set":{"type":"objectType","object_type":"Order"}}
// 限制方向：加 "direction":"out"（out | in | both）
// 限制跳数：加 "hops":[2,3]（只取 2-3 跳）

// 嵌套过滤（flat AND 简写）
{"type":"filter",
 "filters":[{"field":"status","op":"exactMatch","value":"open"}],
 "object_set":{...任意 IR...}}

// 复杂逻辑用 where（and/or/not）替代 flat filters：
{"type":"filter",
 "where":{"type":"and","value":[
   {"field":"region","op":"exactMatch","value":"EAST"},
   {"type":"or","value":[
     {"field":"risk","op":"exactMatch","value":"high"},
     {"field":"priority","op":"exactMatch","value":"critical"}
   ]}
 ]},
 "object_set":{...}}

// 集合运算
{"type":"union","object_sets":[IR, IR, ...]}
// 也支持 intersect（交集）和 subtract（差集），结构一致
{"type":"intersect","object_sets":[IR, IR]}
{"type":"subtract","object_sets":[baseline_IR, remove_IR]}

// 聚合
{"type":"aggregate","object_set":IR,
 "group_by":["riskLevel"],
 "aggregations":[{"func":"count","field":"","alias":"cnt"}]}

// 投影
{"type":"select","object_set":IR,"select_fields":["name","city"]}
```

**过滤操作符（16 种）**：`exactMatch` / `notEqual` / `in` / `notIn` / `range` / `greaterThan` / `lessThan` / `contains` / `startsWith` / `endsWith` / `withinDistance` / `withinPolygon` / `withinBoundingBox` / `timeRange` / `isNull` / `isNotNull`。

**操作符可用性取决于属性 data_type**（先调 `describe_object_type` 确认，用错会返回 `INVALID_FILTER`）：

| data_type | 可用 filter op |
|-----------|---------------|
| STRING | exactMatch, notEqual, contains, in, notIn, startsWith, endsWith, isNull, isNotNull |
| INTEGER / DECIMAL | exactMatch, notEqual, in, notIn, range, greaterThan, lessThan, isNull, isNotNull |
| BOOLEAN | exactMatch, isNull, isNotNull |
| DATE / TIMESTAMP | exactMatch, notEqual, range, timeRange, greaterThan, lessThan, isNull, isNotNull |
| ENUM | exactMatch, in, isNull, isNotNull |

**操作符值约定**：

- `in`/`notIn`：`value=[v1,v2,...]`
- `withinDistance`：`center=[lon,lat]`（GeoJSON 顺序：经度在前），`max_distance=米`
- `withinPolygon`：`coords=[[lon,lat],...]`（GeoJSON 顺序，闭合多边形）
- `withinBoundingBox`：`coords=[[minLon,minLat],[maxLon,maxLat]]`
- `range`：`value={"min":...,"max":...}`（min/max **均 inclusive**）
- `timeRange`：`value={"start":...,"end":...}`（start/end **均 inclusive**，ISO 8601）
- 可选 `order_by`：`[{"field":"<f>","desc":false}]`（保证分页稳定）

### 3.4 返回结构

`query_with_dataframe` 返回：

```jsonc
{
  "objects": [{"rid":"O1","api_name":"Order","props":{...}}, ...],
  "edges": [{"source_vid":"S001","target_vid":"O1","link_type":"supplies","direction":"out"}, ...],
  "aggregates": [{"group":{"riskLevel":"high"},"aggregates":{"cnt":42}}],
  "truncated": true,
  "next_cursor": "rid-50",    // truncated=true 时才有，传入下次调用取下一页
  "stats": {"steps":3,"engines_used":["postgres","neo4j"]},
  "evidence_id": "ev-abc123"   // 证据链 ID，REST GET /objects/{ont}/analysis/{evidence_id}
}
```

### 3.5 分页（cursor 语义）

`query_with_dataframe` 支持 cursor 分页：

1. 首次调用不传 `cursor`，响应里 `truncated=true` 时带 `next_cursor`
2. 下次调用传 `cursor=<上次的 next_cursor>` 取下一页
3. **IR 必须跨分页调用完全一致**（cursor 依赖稳定的 rid 顺序，**连 filter 值改动都算新查询**，cursor 会失效）
4. cursor 绑定特定 IR + ontology，不能跨查询混用

### 3.6 关系推理三件套

| 工具 | 问的问题 | 返回 |
|------|---------|------|
| `traverse_link` | "S001 关联的目标对象是谁？" | `target_objects` 列表（恒为 list，无关系时 `[]`；不随 cardinality 折叠） |
| `exists_link` | "S001 和 C001 有关系吗？" | `{"exists":bool,"mode":"SINGLE_TARGET"\|"ANY_TARGET"}` |
| `find_paths` | "S001 到 C001 的最短路径是什么？" | `{"paths":[[rid,...]],"count":N}` |

- `traverse_link` 支持批量 source（传 `source_keys` 列表）。**入参用业务主键（primary_key），非 rid 非 RID**——系统内部经翻译层解析为 rid（查 object_state by primary_key）。rid 是底层存储主键（对齐 Palantir RID），只在返回值 `objects[].rid` 里出现，不作为入参要求
- `traverse_link` 的 `target_filter` 参数已声明但**当前版本未实现**（传入不影响结果）
- `traverse_link` 返回的 `source_to_target_map`：key 是 Agent 传入的业务主键（pk），value 是目标 rid 列表
- `exists_link` 只支持单个 source（批量用 traverse）
- `find_paths` 的 `source_key`/`target_key` 也是业务主键（内部解析为 rid）。传 `link_types` 能精确解析两端 ObjectType；不传则跨类型扫描
- `find_paths` 的 `max_depth` 谨慎调大——路径爆炸是指数级的

### 3.7 写操作返回格式

| 工具 | 成功返回 |
|------|---------|
| `invoke_action` | `{"status":"completed","action_id":"...","mutations":[...]}` |

> `invoke_action` 的 `idempotency_key` 建议用 UUID v4，服务端按 ActionType+key 去重保证 exactly-once。

---

## 四、HITL（人在回路确认）

`invoke_action` 需要人工确认。Gaia 用 **elicitation** 机制让 MCP 客户端弹原生确认框。

### 4.1 哪些工具需要 HITL

| 工具 | 需 HITL | 说明 |
|------|:------:|------|
| `validate_action` | 否 | 只读预检 |
| `invoke_action` | **是** | 执行有副作用的动作 |
| 所有查询/推理工具 | 否 | 只读 |

### 4.2 三种结果（客户端必须区分）

| 情况 | 触发 | Agent 收到 |
|------|------|-----------|
| 客户端不支持 elicitation | `elicit` 抛异常 | `{"error":{"code":"ELICITATION_UNSUPPORTED"}}` |
| 用户拒绝/取消 | 用户点"取消" | `{"status":"DENIED"}` |
| 用户确认 | 用户点"确认" | `{"status":"completed","action_id":"...","mutations":[...]}` |

**重要**：`ELICITATION_UNSUPPORTED` 表示**环境不支持**（换支持 elicitation 的客户端），`DENIED` 表示**用户拒绝**（不要重试，尊重用户决定）。两者不能混为一谈。

### 4.3 Claude Desktop / Cursor 配置

这些客户端原生支持 elicitation，无需额外配置。自建 MCP 客户端需实现 `elicitation` capability。

---

## 五、错误处理约定

| 场景 | 表现 | Agent 应对 |
|------|------|-----------|
| MCP 参数名拼错 | `TypeError` / missing arg | MCP 参数名以 `list_tools` 返回的 schema 为准：`query_with_sql` 用 `sql`（非 `logical_sql`） |
| 本体/对象类型不存在 | `{"error":{"code":"NOT_FOUND",...}}` | 先 `list_*` 确认名称 |
| 权限不足 | `{"error":{"code":"FORBIDDEN",...}}` | 不要重试，告知用户无权限 |
| Doris 不可用 | 自动降级 Trino，响应正常 | 无感（可能有延迟） |
| `exists_link` 对象不可见 | 返回 `exists:false` | **不要**用 exists_link 探测权限（不可见=不存在） |
| `find_paths` 无路径 | `{"paths":[],"count":0}` | 不是错误，是合法结果 |

**不要**解析错误消息文本——错误结构是 `{"error":{"code":"<SCREAMING_SNAKE>","message":"..."}}`，按 `code` 分支。

---

## 六、推荐工作流

### 6.1 首次接入一个本体

```
1. list_ontologies                    → 拿到 ontology api_name
2. describe_ontology(ont)             → 一次拿全 objects+links+actions（bootstrap）
3. query_with_sql / query_with_dataframe → 开始查询
```

> 第 2 步用 `describe_ontology` 一次拿全结构，**不要**再串行调
> `list_object_types → describe_object_type(×N) → describe_link_type(×M)`。
> 只在需要某单个类型的完整细节（filterable/sortable 提示、完整 action 参数）时
> 才用 `describe_object_type` / `describe_link_type` / `validate_action`。

### 6.2 探索关系网络

```
1. describe_ontology(ont)            → 已给出所有 link 的 source→target + 方向 + FK
2. traverse_link(ont, link, [src_key]) → 单跳展开
3. find_paths(ont, src, tgt)          → 找连通路径
```

> 关系的 source/target 类型 + 方向 + 基数已在 `describe_ontology` 返回的
> `link_types` 里给出，无需再调 `describe_link_type`。仅在需要某条边的
> 完整字段细节时才用 `describe_link_type`。

### 6.3 执行动作

```
1. validate_action(ont, ot, action, params) → 预检参数（无 HITL）
2. invoke_action(ont, ot, action, params)   → 执行（弹 HITL 确认）
```

### 6.4 脚本化场景（REST 备选）

需要脚本/后端集成的场景，Gaia 也提供 REST 端点，对应 MCP 工具的查询能力：

```bash
# 本体全量元数据（bootstrap，对齐 MCP describe_ontology）
curl http://localhost:8000/ontologies/SupplyChain/fullMetadata

# SQL 属性查询
curl -X POST http://localhost:8000/objects/textsql \
  -H "Content-Type: application/json" \
  -d '{"ontology_api_name":"SupplyChain","logical_sql":"SELECT name, riskLevel FROM Supplier WHERE riskLevel = high"}'

# 多跳路径推理
curl -X POST http://localhost:8000/objects/SupplyChain/find-paths \
  -H "Content-Type: application/json" \
  -d '{"source_key":"S001","target_key":"C001","max_depth":3}'
```

完整操作面 REST 端点清单见 OpenAPI 文档：`GET /openapi.json`。

---

## 七、限制与约束

| 约束 | 说明 |
|------|------|
| `searchAround` ≤ 3 跳 | Palantir 硬限制，防止图爆炸。默认 direction="both", hops=(1,3)。方向可指定 out/in/both |
| `find_paths.max_depth` 谨慎调大 | 路径数指数增长 |
| MCP 不暴露 ActionType 定义 | 管理面能力，走 REST |
| 批量 Action 不经 MCP | Agent 应单步决策单步执行 |
| VIRTUAL 对象只读 | 不能写（Trino 联邦查询，无落地）。`describe_object_type` 返回 `storage_type` 可判断 |
| `query_with_sql` 不支持 CTE/UNION | 仅支持单表、JOIN ≤5 表、子查询、聚合、窗口函数。需要 UNION 时拆成多次调用 |
| MCP 工具签名是已发布契约 | 新增工具兼容，移除/改签名是 breaking |

---

## 附录：参考文档

- [ADR-019 三入口能力分层](../architecture/adr-019-three-entry-capability-layering.md)——能力边界判据
- [ADR-020 本体全量元数据聚合端点](../architecture/adr-020-ontology-full-metadata-endpoint.md)——`describe_ontology` bootstrap 设计
- [ADR-009 本体工具层](../architecture/adr-009-ontology-tool-layer.md)——工具层架构
- [ADR-010 HITL 审批机制](../architecture/adr-010-ontology-hitl.md)——elicit 协议细节
- [ADR-015 AG-UI 图探索](../architecture/adr-015-agent-driven-graph-explore.md)——ObjectSet IR 设计
- [Palantir 范式参照](../reference.md)——Ontology API 吃结构化 IR 的设计源头
