# 本体工具层架构(本体能力向 Agent 暴露)

> **交接文档**。本文定义 Gaia 本体能力如何提供给 Agent 使用:暴露哪些能力、如何从本体元数据派生、如何分场景暴露与演进。供后续实现人员按图施工。
>
> **相关文档速查**:
> - 决策记录:[ADR-009](./adr-009-ontology-tool-layer.md)(Sprint 1 工具层基线,11 条决策)· [ADR-010](./adr-010-ontology-hitl.md)(Sprint 2 HITL 分级审批,6 条决策)
> - 实现状态 + 后续路标:[implementation-status.md](./implementation-status.md)(§三-bis 本体工具层,逐组件状态 + 8 项后续路标)
> - **Palantir 原始范式参照**:[reference.md](../reference.md) - 本文档的能力清单、工具族划分、下推/治理思路均派生自该文档对 Palantir Foundry 本体→AIP 工具体系的深度拆解(四大工具族 + 两治理 + 30 迭代 + 末尾工具族分层总览/选型速记)。reference.md 当前已落地的迭代与 Gaia 各阶段对应如下(早期规划的 10-19 专项检索、21-25 元数据辅助、51-75 写动作、76-90 场景等占位编号未在 reference.md 兑现,以实际章节为准):
>   - **MVP 14 只读工具** ← 第 1-9 迭代(Object Get / Filter / Exists / Bulk Get / 单跳·多跳·反向遍历 / Link Exists / Object Count;其中聚合对应第 9 迭代 Object Count 思路,Gaia 的 aggregate/topn 在 reference.md 无 1:1 迭代,属 Gaia 自主拆分;Link Exists 对应第 8 迭代)
>   - **Sprint 2 元数据写 + 动作族** ← 对应 reference.md 第三/四族的元数据派生与 Action 契约思路(reference.md 顶层§二、§三 拆解,非具体迭代编号)
>   - **远期实例数据写工具族** ← 第 28-30 迭代(Bulk Object Upsert / Bulk Link Upsert / Bulk Object Delete)
>   - **远期关系增强** ← 第 26 迭代(带关系属性过滤的关联遍历)
>   - **远期元数据拓扑** ← 第 27 迭代(本体全局路径探查)
>   - **远期时序/审计** ← 第 20 迭代(对象属性历史回溯)
>   - reference.md 末尾「全 30 迭代总目录 + 工具族分层架构总览 + 选型速记」是工具族分层(只读查询层 / 元数据审计辅助层 / 写操作事务层)的权威参照
> - v3.0 AG-UI 集成(本层改造其 `ai_agent.py`/`routes/ai.py`):[ai-integration-guide.md](../engineer/ai-integration-guide.md)

---

## 一、定位

在现有数据层 + 本体元数据层之上,补一层"本体能力如何提供给 Agent 使用"。

```
消费者
  · 外部 Agent (Cursor/Claude Desktop/自建)  ──MCP(stdio/HTTP)──┐
  · Gaia 内置 Web UI                         ──AG-UI(/ai/agent)──┤ pydantic-ai Agent
  · 脚本/后端                                ──REST──────────────┤ 直接挂 toolset
                                                              └──┐ 不经 MCP
                                                                 ▼
                                    同一组工具函数 (pydantic-ai FunctionToolset)
                                                                 │ 薄包装 + 审计切面(MVP 仅审计)
                                                                 ▼
                                    现有 Service 编排层 (ObjectQueryService 扩展 filter/exists/count/aggregate/topn)
                                                                 ▼
                                    现有执行引擎层 (Iceberg/Doris/Trino, 按 storage_type 分叉)
```

本文档聚焦**能力层**(暴露什么、怎么派生、怎么演进),技术实现细节(toolset 组装、FastMCP 桥接、container 解耦、Layer 扩方法)留实现期,本文不展开。

## 二、核心范式:建模即工具

工具不是手工封装,而是从本体元数据派生。对应关系:

| 本体元数据 | 派生的工具能力 |
|----------|--------------|
| `OntologyModel` | `list_ontologies`(列举可用本体) |
| `ObjectTypeModel` | 该类型的检索/聚合工具(通用式,`object_type` 作参数)+ schema 描述工具 |
| `LinkTypeModel` | 关系遍历工具 + schema 描述工具 |
| `ActionTypeModel`(Sprint 2) | 动作执行/预校验工具 |

工具定义用 pydantic-ai `FunctionToolset`,三入口共享同一组工具函数。元数据变更 → 重建 toolset(变更即同步)。

## 三、暴露方式(业务语义)

| 消费者 | 接入方式 | 是否经 MCP |
|--------|---------|-----------|
| 外部 Agent(Cursor/Claude Desktop/自建) | MCP(FastMCP 暴露,stdio + Streamable HTTP 双传输) | 是 |
| Gaia 内置 Web UI | AG-UI(pydantic-ai `Agent(toolsets=[...])`,进程内直接挂载) | **否** |
| 脚本/后端 | REST(现有路由) | 否 |

**关键**:AG-UI 入口在 FastAPI 进程内,pydantic-ai Agent 直接持有 FunctionToolset,工具调用是进程内函数调用,**不经 MCP**。MCP 仅是给外部 Agent 用的协议出口,不是内部总线。

## 四、下推与路由(业务语义,工具层不感知)

工具层不感知 `storage_type`,由 `ObjectQueryService` 按现有 `_load_physical`/`_load_virtual` 路由模式分叉:

| 对象 `storage_type` | 数据落点 | 过滤/聚合在哪算 |
|-------------------|---------|---------------|
| **MANAGED** | Iceberg(主数据)+ Doris(索引) | Doris 索引层做过滤/聚合(**本地计算**,OLAP 强项)→ Iceberg 取属性;Doris 不可用降级 Trino 扫 Iceberg |
| **VIRTUAL** | 外部数据源,不落地 Iceberg/Doris | Trino 联邦查询 Virtual Table,谓词/聚合**下推**到外部源 |

> **术语严格性**:"下推"一词**仅指 VIRTUAL 对象经 Trino 向外部源透传谓词/聚合**。MANAGED 路径的 Doris 过滤/聚合是本地计算,不叫"下推"。Doris 是 Gaia 自有索引层,数据在 Doris 里,不存在"下推到 Doris"的概念。

## 五、能力清单(MVP:元/点查/查询/关系/写/动作 共 16 个工具)

> 每个工具给出:意图 / 适用场景 / 参数语义 / 边界 / one-shot 示例 / 注意点。底层实现、REST API 不展开。
>
> **工具契约质量规范**:工具契约 = 函数签名 + 类型注解 + docstring,pydantic-ai/FastMCP 自动生成 LLM 可见 JSON Schema。三件套硬规范:
> - **函数签名**:参数名语义化、类型精确(不用 `Any`)、可选参数带默认值
> - **docstring**:意图 + 适用场景 + 参数语义 + 返回 + 边界(何时不用本工具)+ one-shot 示例
> - **返回类型注解**:结构化 dict/TypedDict,不用裸 str
>
> review 时和代码同级要求。错误统一返回结构化 `{"error": "CODE", "message": "..."}`,工具层不抛异常给 LLM。

### 5.1 元层(4 个)- Agent 认清场子

#### `list_ontologies`
- **意图**:列出 Gaia 中所有可用本体,是 Agent 任何操作的第一步
- **参数**:无
- **返回**:`[{api_name, display_name, description}]`
- **one-shot**:
  > 用户:"看看系统里有哪些业务领域"
  > → Agent 调 `list_ontologies`
  > → 返回 `[{api_name:"manufacturing",display_name:"制造业本体",description:"..."},{api_name:"supply_chain",...}]`
  > → Agent 据此选后续工具的 `ontology` 参数
- **注意点**:
  - **本工具是所有实例层工具的前置**--Agent 若直接调 `query_with_sql` 而不知 `ontology` 值,应先回头调本工具
  - 返回的 `api_name` 严格大小写敏感,后续工具的 `ontology` 参数必须原样使用
  - 本工具无副作用,可重复调用

#### `list_object_types(ontology)`
- **意图**:列出指定本体下的所有对象类型
- **参数**:`ontology`(本体 api_name,先调 `list_ontologies` 确认)
- **返回**:`[{api_name, display_name, description, storage_type}]`
- **one-shot**:
  > 调 `list_object_types("manufacturing")`
  > → `[{api_name:"Order",storage_type:"MANAGED"},{api_name:"Supplier",storage_type:"VIRTUAL"}]`
- **注意点**:
  - **`storage_type` 决定查询路径与能力边界**--VIRTUAL 对象实时性依赖外部源,无时间旅行;MANAGED 有时间旅行能力(Sprint 2+)
  - Agent 选定 `object_type` 后,调实例层工具前建议先 `describe_object_type` 确认属性名与主键
  - `object_type` 的 `api_name` 严格大小写敏感

#### `describe_object_type(ontology, object_type)`
- **意图**:获取对象类型的完整 schema(属性清单/类型/主键/可过滤可排序标记)
- **参数**:`ontology`, `object_type`
- **返回**:`{api_name, primary_key, storage_type, properties:[{api_name, type, is_primary_key, filterable?, sortable?}]}`
- **one-shot**:
  > 调 `describe_object_type("manufacturing","Order")`
  > → `{primary_key:"order_no", storage_type:"MANAGED", properties:[{api_name:"order_no",type:"STRING",is_primary_key:true},{api_name:"amount",type:"DECIMAL",filterable:true,sortable:true},{api_name:"status",type:"ENUM",filterable:true}]}`
- **注意点**:
  - **这是降低工具调用错误率的关键**--`query_with_sql` 的属性名、主键值类型,都要先据此确认
  - 属性 `api_name` 严格大小写敏感,filter/sort 里写错属性名会返回 `INVALID_FILTER`
  - **`type` 决定 filter 可用操作符**--STRING 只能 eq/neq/contains/in,DECIMAL/INT 才能 gt/gte/lt/lte,ENUM 只能 eq/in
  - 复合主键 MVP 暂不支持(`query_with_sql` 的 `WHERE <pk> = ?` 仅收单字段主键,Sprint 2 评估支持)

#### `describe_link_type(ontology, link_type)`
- **意图**:获取关系类型 schema(两端对象类型/基数/方向/关系自身属性)
- **参数**:`ontology`, `link_type`
- **返回**:`{api_name, source_object_type, target_object_type, cardinality, directional, has_properties}`
- **one-shot**:
  > 调 `describe_link_type("manufacturing","HAS_CUSTOMER")`
  > → `{source_object_type:"Order", target_object_type:"Customer", cardinality:"MANY_TO_ONE", directional:true, has_properties:false}`
- **注意点**:
  - **多跳遍历前必须先 describe 每一跳的 link_type**,确认方向与连通性
  - `cardinality` 决定遍历返回单对象(`MANY_TO_ONE`/`ONE_TO_ONE`)还是列表(`ONE_TO_MANY`/`MANY_TO_MANY`)--Agent 据此预判返回结构
  - `directional:false` 的关系双向可遍历,`direction` 参数无意义
  - `has_properties:true` 的关系有自身属性(如生效时间/角色),Sprint 2+ 提供关系属性查询工具

### 5.2 查询族(1 个)- 万能 SQL 入口

#### `query_with_sql(ontology, sql)`
- **意图**:用逻辑 SQL 查询本体对象,是过滤/计数/聚合/TopN/JOIN/窗口/算术的**唯一入口**(text2sql path B,ADR-012)
- **为什么是唯一入口**:原先的 `filter_object`/`count_object`/`aggregate_object`/`topn_object`/`exists_object` 五个原子工具已删除(2026-06),统一收敛到 `query_with_sql`。原因:① 五个工具直接用 api_name 拼物理 SQL,Trino 报 `COLUMN_NOT_FOUND`(camelCase api_name vs snake_case 物理列名);② 五个工具全走 Trino,绕过 Doris 在线读主源;③ `query_with_sql` 走编译器做 api_name→物理列名映射 + 参数化绑定 + Doris 主/Trino 降级,更安全且结果一致。
- **参数语义**:
  - **无 `object_type` 参数**(设计决策 C,2026-07):SQL 里已写明所有表,不再要求调用方重复、可能出错地提供 `object_type`。编译器通过 `involved_object_types(sql)` 从 SQL 推断所有引用的 ObjectType,对**每一个**参与 JOIN 的 OT 统一做:权限校验(`check_access`)+ 存储路由 + 列名回映。单表场景推断结果与显式传等价,多表 JOIN 场景消除了“主对象锡点”填谁都不对的歧义。
  - `sql`:逻辑 SQL,用 ObjectType api_name 当表名、property api_name 当列名。编译器自动:映射到物理列名、参数化字面量、校验表/列/JOIN 白名单
- **返回**:`{data:[...], row_count}`;guardrail 错误返回 `{error:{code:INVALID_TABLE|INVALID_COLUMN|INVALID_JOIN|...}}`。返回行的键名始终是属性 **api_name**(camelCase),不是底层 snake_case 物理列名
- **存储路由**(全 MANAGED→Doris主/Trino降级;含 VIRTUAL→Trino跨 catalog 联邦):
  - 全 MANAGED:Doris 主路径,失败降级 Trino(方言感知编译,MANAGED 表在 Trino 方言下编译为 `iceberg.ontology.<snake_type>`)
  - 含 VIRTUAL:走 Trino 跨 catalog 联邦 JOIN——Trino 原生支持跨 catalog JOIN,MANAGED 表可见为 `iceberg.ontology.<snake>`,VIRTUAL 表可见为外部 `<catalog>.<schema>.<table>`,一条 SQL 即可跨两者。不再报 `MIXED_STORAGE_JOIN`(2026-07 修订:Trino 本就支持联邦,旧实现错误拒绝)
- **`SELECT *` 展开**(2026-07):编译期自动把顶层 `SELECT *` 展开为显式列。多表 JOIN 时若两个 OT 有同 api_name 属性(如都有 `id`/`status`),冲突列加 OT 前缀消歧(`<OT>_<api>`,如 `ManualOutboundCall_id`/`Lead_id`),不冲突列保持纯 api_name。避免裸 `*` 在 DB 层同名列冲突导致数据静默丢失。`COUNT(*)` 内的 Star 不受影响。用户显式写的别名(`AS xxx`)始终被尊重
- **能力覆盖**(Phase 1):
  - 计数:`SELECT COUNT(*) FROM <OT> WHERE <filter>`
  - 过滤/列表:`SELECT <cols> FROM <OT> WHERE <filter> ORDER BY ... LIMIT n OFFSET m`
  - 聚合:`SELECT func(col) FROM <OT> WHERE <filter> GROUP BY <dims> HAVING ...`
  - TopN:`SELECT ... FROM <OT> WHERE <filter> ORDER BY <col> DESC LIMIT n`
  - 存在性:`SELECT 1 FROM <OT> WHERE <filter> LIMIT 1`
  - JOIN(须沿已定义 LinkType)、子查询、窗口函数(ROW_NUMBER OVER)、自定义算术(amount*0.8)、比率(SUM(a)/COUNT(b))、时间函数(DATE_FORMAT/YEAR/MONTH)
  - 跨 MANAGED+VIRTUAL JOIN(走 Trino 联邦)
- **不支持**:CTE(WITH)、UNION、UPDATE/INSERT(用 Action 工具)
- **one-shot**:
  > 用户:"统计今日呼出数"
  > → `query_with_sql("marketing","SELECT COUNT(*) AS cnt FROM ManualOutboundCall WHERE callTime >= '2026-06-30T00:00:00' AND callTime < '2026-07-01T00:00:00'")`
  > → `{data:[{cnt:3}], row_count:1}`
  >
  > 用户:"各区域订单总金额"
  > → `query_with_sql("manufacturing","SELECT region, SUM(amount) AS total FROM Order GROUP BY region")`
  > → `{data:[{region:"EAST",total:1280000},{region:"WEST",total:860000}], row_count:2}`
  >
  > 用户:"统计某销售当天对有效线索的人工外呼次数"
  > → `query_with_sql("marketing","SELECT COUNT(*) AS cnt FROM ManualOutboundCall JOIN Lead ON Lead.id = ManualOutboundCall.leadId JOIN LeadAllocateRecord ON LeadAllocateRecord.leadsId = Lead.id JOIN SalesConsultant ON SalesConsultant.userId = LeadAllocateRecord.salesConsultantId WHERE SalesConsultant.phone = '17838371975' AND ManualOutboundCall.callTime LIKE '2026-07-01%' AND Lead.leadsStatus = '100410'")`
  > → `{data:[{cnt:0}], row_count:1}`(4 表 JOIN,编译器推断全部 OT + 权限校验 + Doris主执行)
- **注意点**:
  - **属性名用 api_name(camelCase)**,编译器映射物理列名--Agent 不需要知道物理列名
  - **MANAGED 对象走 Doris(在线读主源),Doris 不可用或表未建时降级 Trino 扫 Iceberg;VIRTUAL 对象走 Trino 联邦**
  - 字面量参数化绑定,注入安全
  - JOIN 必须是本体已定义的 LinkType,否则 `INVALID_JOIN`
  - **占比**:带 `GROUP BY` 拿各组绝对值后,LLM 自行算 `每组/总和`(分子分母过滤与权限一致,安全)

### 5.4 关系族(3 个)

#### `list_link_types(ontology)`
- **意图**:列出指定本体的所有关系类型,遍历前确认可用关系
- **参数**:`ontology`
- **返回**:`[{api_name, source_object_type, target_object_type, cardinality}]`
- **one-shot**:
  > 调 `list_link_types("manufacturing")`
  > → `[{api_name:"HAS_CUSTOMER",source_object_type:"Order",target_object_type:"Customer"},{api_name:"HAS_ITEMS",source_object_type:"Order",target_object_type:"OrderItem"}]`
- **注意点**:
  - **遍历前必须先调本工具或 `describe_link_type` 确认关系存在与方向**
  - 与 `list_object_types` 配套使用--Agent 先看有哪些对象,再看对象间有哪些关系
  - 返回的 `api_name` 严格大小写敏感,后续 `traverse_link` 的 `link_type` 参数原样使用

#### `traverse_link(ontology, link_type, source_keys[], direction?, target_filter?, target_properties?, include_source_mapping?)`
- **意图**:沿指定关系遍历,返回关联目标对象;支持批量源一次遍历
- **适用场景**:已知一个或一批源对象主键,查其直接关联目标(正反向均覆盖)
- **参数语义**:
  - `link_type`:关系类型 api_name
  - `source_keys`:**源对象主键数组**,支持单个或批量源,自动去重;同一对象类型(源端由 `describe_link_type` 的 `source_object_type` 决定,反向时为目标端)
  - `direction`:可选 `forward`/`reverse`,默认 `forward`(正向 = 源→目标,反向 = 目标→源,复用同一 Link Type 的反向邻接索引,无需为反向单独建模)
  - `target_filter`:可选,对目标对象的过滤条件,语法同 `query_with_sql` 的 WHERE 谓词;过滤谓词下推到存储层与权限过滤合并执行,先过滤再读属性
  - `target_properties`:可选,目标对象返回属性投影,省 token;默认返回全部有权限属性
  - `include_source_mapping`:可选,默认 `false`;批量源时建议开启,返回 `source_to_target_map` 明确关联归属
- **返回**:
  - `target_objects`:去重后的目标对象列表,单条结构与 `query_with_sql` 返回一致
  - `source_to_target_map`(开启时):`{<source_key>: [<target_rid>...]}`
  - 返回结构同时受 `cardinality` 影响:`MANY_TO_ONE`/`ONE_TO_ONE` 单源时返回单对象或 `null`,多源或 `ONE_TO_MANY`/`MANY_TO_MANY` 返回列表
- **one-shot**:
  > 正向单源:"订单 PO-2024-056 的客户信息"
  > → `traverse_link("manufacturing","HAS_CUSTOMER",source_keys=["PO-2024-056"])`
  > → `{target_objects:[{customer_no:"C001",name:"...",...}], source_to_target_map:{"PO-2024-056":["C001"]}}`(MANY_TO_ONE)
  >
  > 反向单源:"哪些订单属于客户 C001"
  > → `traverse_link("manufacturing","HAS_CUSTOMER",source_keys=["C001"],direction="reverse")`
  > → `{target_objects:[{order_no:"PO-...",...},...]}`(反向 ONE_TO_MANY 返回列表)
  >
  > 批量源 + 目标过滤:"飞机 AC-001、AC-002 在线的发动机"
  > → `traverse_link("manufacturing","HAS_ENGINE",source_keys=["AC-001","AC-002"],target_filter={"property":"status","eq":"ONLINE"},include_source_mapping=true)`
  > → `{target_objects:[{engine_no:"E-...",status:"ONLINE",...},...], source_to_target_map:{"AC-001":["E-003"],"AC-002":["E-007"]}}`
- **边界**:仅单跳;多跳需 Agent 链式调用(每跳结果的 target 主键作下一跳 `source_keys`);仅返回直接关联,不递归;`target_filter` 仅能引用目标对象自身属性,不能引用关系属性(关系属性过滤留远期 `traverse_link_filtered`)
- **注意点**:
  - **多跳遍历必须 Agent 逐跳调用**,每跳前 `describe_link_type` 确认方向与连通性--Gaia MVP 不提供单次多跳工具(远期 `multi_hop_traverse`/`find_object_path`)
  - `direction` 默认 `forward`;**反向遍历前确认 link_type 支持反向**(`describe_link_type` 的 `directional` 字段,`directional:false` 的关系双向均可)
  - **批量源是核心能力**--一次传入多个源主键,存储层批量查邻接索引 + 合并去重目标 + 批量读属性,性能随源数量近线性扩展,远优于循环单源调用。单源时传一个元素的数组即可
  - 遍历结果自动应用权限过滤,无权限关联对象不出现,`source_to_target_map` 中也不留痕迹--Agent 无法通过返回数量差推断无权限关联
  - **返回结构由 `cardinality` 决定**--`MANY_TO_ONE`/`ONE_TO_ONE` 单源返回单对象或 `null`,`ONE_TO_MANY`/`MANY_TO_MANY` 返回列表--Agent 据 `describe_link_type` 预判,避免解构错误
  - `source_keys` 元素必须是源对象类型的主键值,不是 RID--先 `describe_link_type` 确认源对象类型,再 `describe_object_type` 确认主键字段

#### `exists_link(ontology, link_type, source_key, direction?, target_key?)`
- **意图**:仅判断关联关系是否存在,不拉关联对象属性,低开销;是存在性检查在关系维度的等价能力(单对象存在性用 `query_with_sql` 的 `SELECT 1 ... LIMIT 1`)
- **适用场景**:仅需二元判断「源与目标是否关联」「源是否有某类关联」时,优先于 `traverse_link`--写操作前校验归属、防重复绑定、推理分支判断
- **参数语义**:
  - `link_type`:关系类型 api_name
  - `source_key`:**单个**源对象主键(仅支持单源,不支持批量;批量校验需循环或用 `traverse_link`)
  - `direction`:可选 `forward`/`reverse`,默认 `forward`
  - `target_key`:可选,指定目标主键。**传入 = 指定目标校验模式**(判断源与该具体目标是否存在关联);**不传 = 任意目标校验模式**(判断源是否有至少一个该类型关联)
- **返回**:`{exists: bool, mode: "ANY_TARGET"|"SINGLE_TARGET"}`
- **one-shot**:
  > 任意目标模式:"工单 WO-001 有没有负责班组"
  > → `exists_link("manufacturing","ASSIGNED_TO",source_key="WO-001")` → `{"exists":true,"mode":"ANY_TARGET"}`
  >
  > 指定目标模式:"订单 PO-2024-056 是否属于客户 C001"(写操作前校验归属)
  > → `exists_link("manufacturing","HAS_CUSTOMER",source_key="PO-2024-056",target_key="C001")` → `{"exists":true,"mode":"SINGLE_TARGET"}`
- **边界**:仅单源;仅返回是/否,不返回关联数量/属性/RID;不能基于关系属性判断(关系属性查询留远期 `get_link_properties`)
- **注意点**:
  - **只回答是/否**,比 `traverse_link` 拉全量后判空高效--邻接索引早停,找到即返回
  - **无权限关联等价于不存在,返回 `false`**--不要用于探测权限
  - 指定目标模式下,`target_key` 的对象类型必须与关系对应端一致,否则 `INVALID_TARGET_OBJECT_TYPE`
  - `link_type` 不存在或不属于该源对象类型返回 `INVALID_LINK_TYPE`;方向不支持返回 `INVALID_LINK_DIRECTION`

## 六、MVP 范围与演进

| 阶段 | 能力 |
|------|------|
| **MVP(本设计)** | 14 只读工具(元层 4 + 检索 5 + 聚合 2 + 关系 3) |
| Sprint 2 | 动作族(`invoke_action`/`validate_action`)+ 写类(`define_object_type`/`add_property`/`define_link_type`/`link_dataset` 等)+ HITL 方案(MCP elicitation 兼容性评估,单独 ADR) |
| Sprint 3 | 治理(权限 Principal + 审计入库,单独 ADR)+ 语义检索(Doris 向量索引)+ REST 路由统一过审计切面 |
| 远期 | 函数族(Ontology Function 抽象)、场景族(CoW 沙箱)、多跳遍历工具、关系属性查询工具、时间旅行工具(`query_with_sql` 的 `VERSION AS OF` 语法)、**实例数据批量写工具族**(第 28-30 迭代)、**带关系属性过滤的遍历**(第 26 迭代)、**全局路径探查**(第 27 迭代) |

**MVP 期间 Gaia 内置 Web UI 对话降级为只读**(Agent 能查不能建),写类工具留 Sprint 2 随 HITL 一起恢复前端"建议→应用"流程。

### Sprint 2 已交付(ADR-010)

写/执行工具 6 个 + 分级 HITL 双协议闭环已落地。汇总:

| 工具 | 类型 | 风险 | 说明 |
|------|------|------|------|
| `define_object_type` | 写 | medium | 创建对象类型,薄包装 OntologyService |
| `add_property` | 写 | medium | 加属性,薄包装 OntologyService |
| `define_link_type` | 写 | medium | 创建关系,薄包装 OntologyService(api_name→id 解析) |
| `link_dataset` | 写 | medium | 绑定数据集列映射,薄包装 OntologyService |
| `invoke_action` | 执行 | 按 ActionType.risk_level | 执行动作,薄包装 ActionService(含幂等/OCC/Outbox) |
| `validate_action` | 执行 | 不审批(纯校验) | 预校验参数+规则,薄包装 ParameterValidator |

各工具完整契约(参数语义/返回/one-shot/边界/注意点,与代码 `_logic` 签名一致):

#### `define_object_type(ontology, display_name, primary_key?, title_property?, storage_type?, description?, properties?)`
- **意图**:在本体中创建一个新对象类型(本体建模变更,非数据实例写入)
- **适用场景**:Agent 发现用户描述的实体在本体中不存在,需要新增类型时(如"我们还有一类「设备点检记录」没建模」」)
- **参数语义**:
  - `ontology`:本体 api_name(先 `list_ontologies` 确认)
  - `display_name`:人类可读名称(如「订单明细」)。api_name 由后端从 display_name 推导(PascalCase),无需手填
  - `primary_key`:可选,主键属性的 api_name 或 display_name。省略时由后端从属性的 `is_primary_key=true` 标记反推(Q2)
  - `title_property`:可选,用作展示标题的属性 api_name/display_name。省略时由 `is_title_property=true` 反推
  - `storage_type`:可选,`MANAGED`(默认,落 Iceberg+Doris)/ `VIRTUAL`(外部源联邦),决定后续查询路径与能力边界
  - `description`:可选,语义描述,帮助 LLM 理解
  - `properties`:可选,初始属性列表 `[{display_name, data_type, is_primary_key?, is_title_property?, indexed?, nullable?, description?}]`,api_name 由后端从 display_name/backing_column 推导(camelCase);不传则仅创建主键属性、后续用 `add_property` 补
- **返回**:`{api_name, status:"created"}` 或 NEED_APPROVAL/DENIED marker
- **one-shot**:
  > `define_object_type("manufacturing", api_name="inspection_record", display_name="点检记录", primary_key="record_no", title_property="record_no", storage_type="MANAGED", description="设备日常点检记录", properties=[{api_name:"record_no",display_name:"记录编号",data_type:"STRING"},{api_name:"result",display_name:"点检结果",data_type:"ENUM"}])`
- **边界**:仅创建类型定义,不写入业务数据实例(实例写入留远期实例数据批量写工具族);`api_name` 冲突返回错误;触发 Doris 索引表 schema 演进
- **注意点**:medium 风险固定(建模变更都算中危),HITL 会列出对象形状确认后才创建;`storage_type` 一旦设定不可随意变更(影响数据落点)

#### `add_property(ontology, object_type, api_name, display_name, data_type, indexed?, nullable?, description?)`
- **意图**:为已存在的对象类型添加一个属性
- **适用场景**:对象类型已建但缺字段(如 Order 需加 `priority` 字段)
- **参数语义**:
  - `object_type`:目标对象类型 api_name
  - `api_name`/`display_name`/`description`:同上
  - `data_type`:属性数据类型(STRING/DECIMAL/INT/ENUM/DATE/BOOLEAN 等,决定 filter 可用操作符)
  - `indexed`:可选,默认 `false`;设 `true` 会触发 Doris 索引表 schema 演进,加速该字段过滤/排序
  - `nullable`:可选,默认 `true`
- **返回**:`{api_name, object_type, status:"added"}` 或 marker
- **one-shot**:
  > `add_property("manufacturing","Order",api_name="priority",display_name="优先级",data_type="ENUM",indexed=true,nullable=false,description="订单优先级 P1/P2/P3")`
- **边界**:仅加属性定义,不同步历史数据填充(历史行该字段为 null/默认);`indexed=true` 有 schema 演进开销
- **注意点**:medium 固定;`data_type` 一旦定下,filter 可用操作符随之固定(STRING 不能 gt);高频过滤字段建议 `indexed=true`

#### `define_link_type(ontology, api_name, display_name, source_object_type, target_object_type, cardinality?, direction?, foreign_key_property?, description?)`
- **意图**:定义两个对象类型之间的关系类型
- **适用场景**:需建立实体间关联(如 Order 与 Customer 的归属关系)
- **参数语义**:
  - `source_object_type`/`target_object_type`:两端对象类型 api_name(工具内部 api_name→id 解析)
  - `cardinality`:可选,默认 `MANY`(一对多);可选 `ONE`(一对一)/`MANY`(一对多)/`MANY_MANY`(多对多)--决定 `traverse_link` 返回单对象还是列表
  - `direction`:可选,默认 `OUTGOING`;可选 `OUTGOING`/`INCOMING`/`BOTH`
  - `foreign_key_property`:可选,外键属性 api_name(存储在源或目标端属性上,用于物化邻接索引)
- **返回**:`{api_name, status:"created"}` 或 marker
- **one-shot**:
  > `define_link_type("manufacturing", api_name="has_customer", display_name="归属客户", source_object_type="Order", target_object_type="Customer", cardinality="MANY", direction="OUTGOING", foreign_key_property="customer_no", description="订单归属于唯一客户")`
- **边界**:仅定义关系元数据,邻接索引需后续数据管道同步才可遍历;两端对象类型必须已存在
- **注意点**:medium 固定;`cardinality` 影响 `traverse_link` 返回结构,建模时就要想清楚;`direction=BOTH` 的关系双向可遍历

#### `link_dataset(ontology, object_type, dataset_api_name, column_mappings)`
- **意图**:将对象类型的属性绑定到底层物理数据集的列,完成数据物化
- **适用场景**:对象类型已定义、属性已加,但还没绑定数据源时(MANAGED 对象必须绑定才有数据可查)
- **参数语义**:
  - `dataset_api_name`:底层物理数据集 api_name(Iceberg 表或外部表)
  - `column_mappings`:属性→物理列映射数组 `[{<property_api_name>: <column_name>}, ...]`(或 `[{property, column}]`,以代码 _logic 为准)
- **返回**:`{object_type, dataset_api_name, mapped_properties:<int>, status:"linked"}` 或 marker
- **one-shot**:
  > `link_dataset("manufacturing","Order",dataset_api_name="iceberg_orders",column_mappings=[{"order_no":"order_id"},{"amount":"total_amount"},{"status":"order_status"}])`
- **边界**:仅 MANAGED 对象需要(VIRTUAL 对象查外部源不经 Iceberg/Doris);绑定后数据需管道同步才可见
- **注意点**:medium 固定;映射的属性必须已在对象类型上定义;主键属性必须映射

#### `invoke_action(ontology, object_type, action_type, parameters?, idempotency_key?)`
- **意图**:执行预定义动作(ActionType)。对应 reference.md §三动作族契约--「Agent 只能提案、规则决定执行」,走 ActionType 契约的五要素(参数 Schema / 提交校验 / 业务执行逻辑 / 多系统回写 / 审计)
- **适用场景**:执行有副作用的业务动作(如「批准订单」「指派工单」「关闭告警」),而非直接 CRUD 数据实例
- **参数语义**:
  - `object_type`:动作所作用的对象类型 api_name
  - `action_type`:ActionType api_name(建模时定义,含参数契约 + risk_level)
  - `parameters`:动作参数 dict,必须符合 ActionType 的参数 Schema(`validate_action` 可预校验)
  - `idempotency_key`:可选,幂等键,相同键重复提交不会产生重复变更(exactly-once 语义)
- **返回**:`{status, action_id, mutations:[...]}`,status ∈ `applied`/`accepted`/`conflict`/`validation_failed`;medium/high risk 返回 NEED_APPROVAL/DENIED marker 而非直接结果
- **one-shot**(low-risk,无审批):
  > `invoke_action("manufacturing","Order","update_note",parameters={"order_no":"PO-001","note":"已发货"})`
  > → `{status:"applied", action_id:"...", mutations:[...]}`
- **边界**:走 ActionType 契约,不是直接数据 CRUD--批量数据同步/导入/清理用远期实例数据批量写工具族;动作执行前强制三重校验(权限 / 参数合法性 / 业务规则),任一不过终止并返回明确原因;多系统回写失败自动回滚本体事务
- **注意点**:
  - **risk_level 来自 ActionType 元数据**(建模时标注,默认 low),运行时读取驱动 gating:low 直接执行;medium 列影响确认;high 强确认--标注生效无需改代码
  - **幂等性**:`idempotency_key` + OCC + Outbox 三重保障,Agent 可安全重试
  - 执行前建议先 `validate_action` 预校验参数,避免提交后才发现参数非法
  - high 风险动作建议人工复核 mutations 影响范围

#### `validate_action(ontology, object_type, action_type, parameters?)`
- **意图**:预校验动作参数 + 规则,**不执行**、不触发 HITL、无副作用--对应 reference.md §三动作族的「业务规则校验」独立前置节点
- **适用场景**:`invoke_action` 前确认参数合法、避免无效提交;或 Agent 需判断某参数组合是否可行再做决策
- **参数语义**:同 `invoke_action` 的 `ontology`/`object_type`/`action_type`/`parameters`(无 `idempotency_key`,因不执行)
- **返回**:`{valid: bool, errors:[...]}`--`valid:true` 表示参数通过校验;`valid:false` 时 `errors` 列出具体失败原因
- **one-shot**:
  > `validate_action("manufacturing","Order","update_note",parameters={"order_no":"PO-001","note":"已发货"})` → `{valid:true, errors:[]}`
  > 参数非法:`validate_action(...,parameters={"order_no":"PO-001"})` → `{valid:false, errors:["note 为必填"]}`
- **边界**:仅校验参数 + 规则,不检查权限、不执行回写、不产生 action_id;medium/high 动作的 `validate_action` 也不触发审批(纯校验)
- **注意点**:纯读语义,天然幂等;通过校验不代表 `invoke_action` 必然成功(执行时还有权限/并发冲突校验);建议作为 `invoke_action` 的标准前置步骤

HITL 分级(`ActionType.risk_level` 驱动,默认 low 跳过;medium 列影响确认;high AG-UI 输名称/MCP 是-否)。
双路径:AG-UI `AGUIApprovalHandler`(NEED_APPROVAL marker → `/ai/action/confirm` 恢复);MCP `MCPApprovalHandler`(`Context.elicit` 同步确认,无降级)。
共享逻辑 + 双套暴露(`<tool>_logic` 协议无关函数,AG-UI `@ts.tool` / MCP `@mcp.tool` 各包装)。详见 [ADR-010](./adr-010-ontology-hitl.md)。

## 七、实现指引(交接给实现人员)

### 7.1 目录结构

```
src/ontology/
├── tools/                          # 【新增】本体工具层
│   ├── __init__.py
│   ├── executor.py                 # 审计切面(MVP) → 调 Service;后续扩权限/HITL
│   ├── registry.py                 # 从本体元数据派生工具,动态注册到 toolset
│   └── toolsets/
│       ├── __init__.py
│       ├── metadata.py             # 元层 4 工具
│       ├── object_query.py         # 检索 5 + 聚合 2
│       └── link_traversal.py       # 关系 3(list_link_types / traverse_link / exists_link)
├── protocols/                      # 【新增】协议入口
│   ├── __init__.py
│   └── mcp_server.py               # FastMCP server,把 toolset 暴露为 MCP
├── services/
│   ├── object_query_service.py     # 【扩展】query 入口 execute_compiled_sql + aggregate_objects(REST 复用);filter/count/topn/exists 已删
│   └── ai_agent.py                 # 【改造】挂统一 toolset,删 suggest/apply/confirm/演示 AppState
├── config/
│   └── container.py                # 【核实/解耦】能脱离 FastAPI 独立构造 Service
└── ...(其余不变)
```

### 7.2 实现要点

1. **工具定义用 pydantic-ai `FunctionToolset`**。每个 toolset 是一个 `FunctionToolset[None]`,工具用 `@ts.tool` 装饰 async 函数。函数签名 + 类型注解 + docstring 即工具契约,严格按 §五 的规范写。

2. **`FunctionToolset` → `FastMCP` 桥接**(✅ 已验证 fastmcp 3.4.2):`FastMCP.add_tool(tool.function)` 自动用函数名作 MCP 工具名、从注解+docstring 生 schema,与 pydantic-ai 一致。14 工具全经 MCP 可见(12 可用 + traverse_link/exists_link 骨架返回 TOOL_NOT_IMPLEMENTED),7 端到端测试通过(`tests/unit/protocols/test_mcp_server.py`)。

3. **`ObjectQueryService` 扩展**:新增方法按现有 `_load_physical`/`_load_virtual` 路由模式分叉 storage_type,不另起 Service 文件。`filter` 表达式走 ObjectSet IR (`object_set_executor.py`) 或 TextQL 编译器 (`sql_compiler.py`),均已参数化 + 本体白名单校验,不新造谓词树。

> **🚫 反模式禁止(硬规范)**:不要在 Python 层手写 SQL 操作符翻译器(如对 `eq/neq/gt/...` 写 14 个 if-elif 分支、手写字面量转义、手写正则校验标识符)。Trino/Doris 都是标准 SQL,操作符即 SQL 关键字--用**操作符映射表**(`{"eq":"=","neq":"!=",...}`)查表拼接即可,值用**参数化查询**绑定(不要手写 `replace("'","''")`),属性名走**本体元数据白名单校验**(`ObjectType.properties[].api_name`,不存在的属性报 `INVALID_FILTER`,天然防注入,不需要正则)。第一版 `_filter_dict_to_sql`/`_sql_literal`/`_validate_identifier` 违反此规范,**已于 2026-07-13 全部解决**(见 §八 待重构项)：TextQL 编译器 + ObjectSet IR 两条生产路径均已参数化 + 白名单；遗留非参数化的 `DorisIndexStore.query`/`load_by_filter`/`aggregate` 无生产调用方,直接删除(见 ICD-04 v1.1)。

4. **`config/container.py` 解耦**:核实 container 是否强耦合 FastAPI `app` 生命周期。若耦合,借机解耦--DI 容器应协议无关(CLAUDE.md 分层强调独立于 Routes)。`ontology-mcp` 进程复用 container 工厂构造 Service,不起 HTTP。

5. **MCP 独立进程入口**:`pyproject.toml` 加 `[project.scripts] ontology-mcp = "ontology.protocols.mcp_server:main"`,支持 `--stdio`/`--http --port N` 双传输。

6. **`executor.py` MVP 仅审计**:记录调用方/工具名/入参/结果/耗时,调用方记 anonymous(治理 Principal 留 Sprint 3)。后续扩权限校验与 HITL 在此切面收口。

7. **删除现有演示工具**:`ai_agent.py` 的 `suggest_object_types`/`apply_suggestions`/`confirm_action`/`AppState` 演示字段全删。`routes/ai.py` 的 `/ai/action/confirm` 端点同步删。`AppState` 若 AG-UI 仍需共享状态,按实际需要重定义(不含演示字段)。

8. **AG-UI 入口改造**:`routes/ai.py` 的 `POST /ai/agent` 改为 `Agent(toolsets=[metadata_ts, object_query_ts, link_traversal_ts])` 挂载同一批 FunctionToolset,**进程内直接调用,不经 MCP**。保留 `AGUIAdapter.dispatch_request` + `manage_system_prompt='client'`。

9. **前端 `AiSuggestPanel` 配合改造**:MVP 降级为只读对话(Agent 能查不能建)。写类工具留 Sprint 2 恢复"建议→应用"流程。

> **✅ Sprint 2 已完成（commit 584af2c）**：写工具 + 动作工具经 MetadataApprovalToolset HITL 批量审批恢复，多轮对话式建模已可用（AG-UI Thread + message_history + Capability 方法论）。

### 7.3 测试要求(遵循 CLAUDE.md TDD)

- 单元测试覆盖每个工具:正常路径 + 异常路径(不存在/无权限/参数非法/超限)
- `ObjectQueryService` 新增方法:MANAGED/VIRTUAL 两条路径 + Doris 降级 Trino 路径
- MCP 端到端:用 `fastmcp.Client` 进程内连 `FastMCP` 实例,验证 14 个工具可 list/call
- 异常路径覆盖率 100%,行覆盖率 > 90%

## 八、后续工作(待完成项)

> 实现人员按阶段推进,每项完成后更新 [implementation-status.md](./implementation-status.md)。

### 待重构项（🚫 技术债）

| 项 | 问题 | 重构方向与现状 |
|----|------|----------|
| `ObjectQueryService._filter_dict_to_sql` 的 if-elif 操作符链 | 手写 14 操作符 if-elif 链，重复造轮子 | ✅ **已重构**：改用操作符映射表 `_OP_COMPARE`/`_OP_NULL` 查表拼接（见 §7.2 反模式禁止）。if-elif 链已消除 |
| `_sql_literal` 手写字面量转义 | `replace("'","''")` 转义不完备 | ✅ **已解决 (2026-07-13)**：TextQL 编译器路径（`sql_compiler.py`）已全面参数化——literal 提取为 `?` placeholder + params list，`_compile_and_run` 透传 params 到 `DorisIndexStore.execute_sql` / `TrinoQueryEngine.query`。ObjectSet IR 路径（`object_set_executor.py`）同样走 `:param` 占位符 + SQLAlchemy `text` 绑定。遗留的 `_sql_literal`/`_escape_val` 字面量转义仅保留在 `upsert` 拼 VALUES（Doris INSERT 不接受参数化 VALUES 子句）和 `table_exists` 的 `SHOW TABLES` 表名插值（表名走 `_validate_identifier` 白名单） |
| `_validate_identifier` 正则校验 | 设计要求属性名走本体元数据白名单校验 | ✅ **已解决 (2026-07-13)**：ObjectSet IR 路径已落地 `_load_allowed_fields` + `_validate_filter_fields`（P2，2026-07-06）——从本体 ObjectType.properties 加载 api_name 并集，filter/where 入口校验不在白名单则 raise `INVALID_FILTER`（列前 20 个可用属性助纠正）。TextQL 编译器路径走 SqlGlot AST 天然白名单（表/列/JOIN 均校验）。Doris 层 `_validate_identifier` 正则仅作列名兜底（identifier 不能参数化）；遗留 `query`/`load_by_filter`/`aggregate` 非参数化方法已删除（无生产调用方，见 ICD-04 v1.1） |

### 设计更新:关系族增强(签名已落地,执行待实现)

§五 5.4 关系族已从 2 工具扩展为 3 工具,与 reference.md 第 5/8 迭代对齐。**代码签名与 docstring 已落地**(MCP 可见、契约已生成),**执行逻辑待 LinkTraversalService**:

| 项 | 设计变更(本文档) | 当前实现状态 | 跟进动作 |
|----|------------------|------------|----------|
| `traverse_link` 批量源 | `source_key` → `source_keys[]`,新增 `target_filter`/`target_properties`/`include_source_mapping` | 🟡 签名 + docstring 已落地,返回 `TOOL_NOT_IMPLEMENTED` | Sprint 3+ 图数据库方案落地时实现执行逻辑(批量邻接索引查 + 目标过滤下推 + `source_to_target_map`) |
| `exists_link` 新增 | 关系存在性校验 | 🟡 签名 + docstring 已落地,返回 `TOOL_NOT_IMPLEMENTED` | Sprint 3+ 随 `traverse_link` 一起实现(同依赖邻接索引) |
| `count_object` 返回字段 | 设计为 `{count}`(Gaia 自主简化) | ⚰️ 已随 count_object 工具删除(2026-06,统一收敛到 query_with_sql) | - |

> MCP 注册工具数为 14(12 可用 + 2 骨架)。§7.3 测试要求「验证 14 个工具可 list/call」中的 call 仅针对 12 个可用工具;traverse_link/exists_link 的 call 测试断言 `TOOL_NOT_IMPLEMENTED`(见 `tests/unit/protocols/test_mcp_server.py::test_mcp_not_implemented_tool_returns_envelope`)。实现进度以 [implementation-status.md](./implementation-status.md) 为准。

### Sprint 1 实现期核实项

| 项 | 核实内容 | 风险 |
|----|---------|------|
| `FunctionToolset` → `FastMCP` 桥接 | ✅ 已验证(fastmcp 3.4.2,14 工具注册 / 12 可用 + 2 骨架,7 端到端测试) | - |
| `config/container.py` 解耦 | ✅ 已确认(container 不耦合 FastAPI app) | - |
| `DorisIndexStore` 聚合能力 | ✅ 已解决(2026-07-13):聚合统一走 `ObjectQueryService.aggregate_by_request` → TextQL 编译器 → `execute_sql` 参数化路径;遗留 `DorisIndexStore.aggregate` 非参数化方法已删(无生产调用方,见 ICD-04 v1.1) | - |
| VIRTUAL 对象 Trino 联邦查询 | 🟡 代码已实现待真实验证:query_with_sql 走 Doris 主/Trino 降级,但 Trino→外部源透传依赖外部源能力 | 中(依赖外部源) |
| filter 谓词结构 | ✅ 已解决(2026-07-13):遗留 `IndexFilter` schema 已删;filter 走 ObjectSet IR `Filter`/`WhereClause` (参数化 + `_validate_filter_fields` 白名单) 或 TextQL 编译器 (SqlGlot AST 白名单) | - |
| 批量/分页上限具体值 | ✅ 已定:query_with_sql 的 LIMIT 由 SQL 表达 | - |

### Sprint 2(动作族 + 写类 + HITL)- ✅ 已交付

见 §六「Sprint 2 已交付(ADR-010)」完整契约。6 工具(`define_object_type`/`add_property`/`define_link_type`/`link_dataset`/`invoke_action`/`validate_action`)+ 分级 HITL 双协议闭环已落地。要点:

- 动作族工具:`invoke_action(ontology, object_type, action_type, parameters?, idempotency_key?)` / `validate_action(ontology, object_type, action_type, parameters?)`--薄包装 ActionService / ParameterValidator
- 写类工具:`define_object_type`/`add_property`/`define_link_type`/`link_dataset`,薄包装 OntologyService 现有方法
- **HITL 方案**(ADR-010):
  - MCP 路径:`Context.elicit` 同步确认(Claude Desktop 支持;Cursor 早期不支持 elicitation,无降级,直接拒)
  - AG-UI 路径:`NEED_APPROVAL` marker → `/ai/action/confirm` 端点恢复
  - 分级确认:`ActionType.risk_level` 驱动(low 跳过 / medium 列影响确认 / high AG-UI 输名称·MCP 是-否);写类工具固定 medium
- 前端「建议→应用」流程随写类工具恢复

### Sprint 3(治理 + 语义检索)

- **治理 Principal + 权限 + 审计入库**(单独 ADR):
  - 统一 `Principal` 抽象,三入口(MCP auth / AG-UI 业务字段 / REST JWT)各自提取
  - 功能级权限:Gravitino RBAC(`check_access`)
  - 列级权限:ObjectType/PropertyDef metadata 标记 + 查询投影裁剪
  - 行级权限:待 Gravitino 受限视图能力成熟
  - 审计入专用库(PG 表或专用日志),与 `trace_id` 串联
- **语义检索**:`semantic_search_object`(reference.md §14),依赖 Doris 4.0.5 向量索引成熟度
- REST 路由统一过 `executor.py` 审计切面

### 远期

- **函数族**(reference.md 第二族):需先建 Ontology Function 抽象(TypeScript/Python 函数 + 版本管理 + 沙箱),工作量大
- **场景族**(reference.md 第四族):CoW 沙箱 + What-if + 影响路径 + 敏感性分析,工作量极大,按真实需求触发
- **多跳遍历工具**:`multi_hop_traverse(path[])`,单次多跳
- **全局路径探查工具**(reference.md 第 27 迭代):`find_object_path(ontology, source_object_type, target_object_type, max_hop)`--BFS 枚举起止对象类型间所有合法多跳关联路径,输出结构化路径模板。是 `multi_hop_traverse` 的**前置规划底座**:Agent 未知两端如何关联时先探查全部可行路径,再逐跳拉数据;多跳遍历报错时也可回探校验路径合法性。仅基于静态元数据推演,不触碰业务实例,max_hop 硬上限 5。需本体元数据拓扑探查独立权限
- **关系属性查询工具**:`get_link_properties`,查关系自身属性(生效时间/角色/权重)
- **带关系属性过滤的遍历**(reference.md 第 26 迭代):`traverse_link_filtered(ontology, link_type, source_keys[], link_filter, target_filter?)`--在遍历时按关系自身属性(角色/状态/权重/生效时间)筛选链路,引擎内前置裁剪,再读目标实体。是 `traverse_link` + `get_link_properties` 的**组合增强**:避免「拉全量关联 + 上层 LLM 过滤」的无效 IO 与 Token 浪费(reference.md 给了对比表,无效链路提前丢弃,IO/Token 大幅下降)。强依赖关系自定义属性(无自有属性的关系报 `LINK_NO_CUSTOM_PROPS`,降级为普通遍历);`link_filter` 仅能引用关系字段,不能引用源/目标实体字段
- **时间旅行**:`query_with_sql` 支持 `FOR VERSION AS OF {snapshot}` 语法 + `trace_property_history` 工具(reference.md §20/第 20 迭代)
- **实例数据批量写工具族**(reference.md 第 28-30 迭代):与已交付的「元数据写工具」(`define_object_type` 等改本体模型)是**不同层级**--本族改的是**业务数据实例**,需独立设计:
  - `bulk_upsert_object(ontology, object_type, records[], write_mode?, conflict_strategy?)`--同类型实体批量增改,主键自动区分 INSERT/UPDATE,支持 UPSERT/INSERT_ONLY/UPDATE_ONLY 三模式 + ROLLBACK_ALL/SKIP_INVALID 冲突策略(第 28 迭代)
  - `bulk_upsert_link(ontology, link_type, link_records[], default_operation?, conflict_strategy?)`--批量绑定/解绑关联、批量更新关系自有属性,以 `(source,target)` 三元组唯一标识,支持 CREATE_UPDATE/DELETE(第 29 迭代)
  - `bulk_delete_object(ontology, object_type, primary_keys[], delete_mode?, cascade_link_strategy?)`--批量逻辑/物理删除,关联处理三策略 BLOCK_IF_LINKED/CASCADE_DELETE/NONE(第 30 迭代)
  - **与 `invoke_action` 的边界**:`invoke_action` 走 ActionType 契约(提交参数 + 规则决定执行 + 多系统回写 + 审计),适合「业务动作」;本族是直接的数据实例 CRUD,适合「批量数据同步/导入/清理」场景。两者都需走 HITL 分级审批(物理删除、大批量覆盖属高危)
  - 依赖:底层存储批量事务 + 软删系统字段(isDeleted/deleteTime)+ 关联邻接索引批量扫描;分片事务、幂等(主键/三元组天然幂等)
- **会话持久化**:Redis 按 `thread_id` 存 `AppState` + 消息历史(ai-integration-guide §9.3)

## 九、参考

- [ADR-009](./adr-009-ontology-tool-layer.md) - Sprint 1 工具层基线决策(11 条)
- [ADR-010](./adr-010-ontology-hitl.md) - Sprint 2 HITL 分级审批决策(6 条)
- [reference.md](../reference.md) - Palantir 本体→Agent 工具体系深度拆解(四大工具族 + 两治理 + 30 迭代 + 末尾工具族分层总览/选型速记)
- [ai-integration-guide.md](../engineer/ai-integration-guide.md) - v3.0 AG-UI 集成(本层改造其 `ai_agent.py`/`routes/ai.py`)
- [CLAUDE.md](../../CLAUDE.md) - 分层红线(Doris 索引层 / Trino 联邦 / VIRTUAL 表 / storage_type 定义)+ 规范 8(联邦查询 SQL 不手写翻译器)+ 分级确认红线
- [implementation-status.md](./implementation-status.md) - 实现状态 + 后续路标(§三-bis 本体工具层)
